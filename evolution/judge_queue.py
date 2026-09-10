"""Durable asynchronous judging; CLI never reads a verifier result.

SQLite serializes endpoint token reservations across processes. One runner
owns each queue. HTTP retries are disabled in the backend; the queue dispatches
at most two attempts, each with its own limiter reservation and ledger row.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

from evolution.judges import (
    PROMPT_VERSION,
    JudgeFailure,
    JudgeInput,
    build_prompt,
    judge_once,
    parse_response,
)
from evolution.sanitize import (
    canonical,
    digest,
    sanitize,
    solver_visible,
)
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.openai_api import OpenAIAPIBackend, retry_delay

ROOT = Path(__file__).resolve().parents[1]


class ErrorArchiveTransport(httpx.AsyncHTTPTransport):
    """Retain credential-redacted provider errors without changing backend."""

    def __init__(self, path, key):
        super().__init__()
        self.path, self.key = Path(path), key

    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            append_jsonl(self.path, {
                "ts": utc_now(), "status_code": response.status_code,
                "body": body.replace(self.key, "[REDACTED]"),
            })
        return response


class TokenBucketLimiter:
    """A shared dual token bucket with conservative UTF-8 token reservations.

    Requests larger than one minute's token capacity reject immediately.
    Reservations are not refunded: prompt plus maximum output tokens bound
    consumption, including transport errors. A shared SQLite path makes limits
    survive restarts and coordinate different queues on the same endpoint.
    """

    def __init__(
        self,
        rpm=250,
        tpm=250_000,
        *,
        path=None,
        endpoint="default",
        clock=time.time,
        sleep=asyncio.sleep,
    ):
        if any(not math.isfinite(v) or v <= 0 for v in (rpm, tpm)):
            raise ValueError("RPM and TPM must be finite and positive")
        if rpm < 1:
            raise ValueError("RPM must permit at least one request")
        self.rpm, self.tpm = float(rpm), float(tpm)
        self.clock, self.sleep, self.endpoint = clock, sleep, endpoint
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS buckets (
            endpoint TEXT PRIMARY KEY, requests REAL, tokens REAL,
            updated REAL, rpm REAL, tpm REAL, blocked_until REAL DEFAULT 0
        )""")
        self.db.commit()

    async def acquire(self, tokens):
        if type(tokens) is not int or not 0 < tokens <= self.tpm:
            raise ValueError("Token reservation must fit endpoint capacity")
        started = self.clock()
        while True:
            now = self.clock()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute(
                    "SELECT requests,tokens,updated,rpm,tpm,blocked_until "
                    "FROM buckets WHERE endpoint=?",
                    (self.endpoint,),
                ).fetchone()
                if row:
                    requests, available, updated, rpm, tpm, blocked = row
                    if (rpm, tpm) != (self.rpm, self.tpm):
                        raise ValueError("Conflicting limits for one endpoint")
                    elapsed = max(0, now - updated)
                    requests = min(self.rpm, requests + elapsed * rpm / 60)
                    available = min(self.tpm, available + elapsed * tpm / 60)
                else:
                    requests, available = self.rpm, self.tpm
                    blocked = 0
                delay = max(
                    0,
                    (1 - requests) * 60 / self.rpm,
                    (tokens - available) * 60 / self.tpm,
                    blocked - now,
                )
                if delay <= 1e-8:
                    requests -= 1
                    available -= tokens
                self.db.execute(
                    "INSERT OR REPLACE INTO buckets VALUES (?,?,?,?,?,?,?)",
                    (
                        self.endpoint,
                        requests,
                        available,
                        now,
                        self.rpm,
                        self.tpm,
                        blocked,
                    ),
                )
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            if delay <= 1e-8:
                return max(0, self.clock() - started)
            await self.sleep(delay)

    def defer(self, seconds):
        """Honor Retry-After for every worker sharing this endpoint."""
        self.db.execute(
            "UPDATE buckets SET blocked_until=max(blocked_until, ?) "
            "WHERE endpoint=?",
            (self.clock() + seconds, self.endpoint),
        )
        self.db.commit()

    def close(self):
        self.db.close()


class Endpoint:
    """Trusted endpoint configuration, never included in judge evidence."""

    def __init__(self, prefix, config, output, budget_path, budget_usd=15):
        self.prefix = prefix
        self.model = config.get(f"{prefix}_MODEL", "")
        self.base = config.get(f"{prefix}_API_BASE", "")
        self.key = config.get(f"{prefix}_API_KEY", "")
        if not self.model or not self.base or not self.key:
            raise ValueError(f"Missing {prefix} endpoint configuration")
        # Foundry quotas are per deployment, including models on one host.
        self.identity = digest(self.base.rstrip("/") + "/" + self.model)
        self.output, self.budget_path = Path(output), Path(budget_path)
        self.budget_usd = budget_usd
        self.rpm = float(config.get(
            f"{prefix}_RPM", 100 if prefix == "XJUDGE" else 250,
        ))
        self.tpm = float(config.get(
            f"{prefix}_TPM", 100000 if prefix == "XJUDGE" else 250000,
        ))
        self.max_tokens = int(
            config.get(
                f"{prefix}_MAX_COMPLETION_TOKENS",
                8192 if prefix == "XJUDGE" else 2048,
            )
        )
        self.timeout = float(config.get(f"{prefix}_TIMEOUT_S", 180))
        self.prices_path = config.get(
            "JUDGING_PRICES_PATH", str(ROOT / "costs/judges_prices.json")
        )
        # Foundry rejects the thinking extension. Leave reasoning at provider
        # default; freeze accepted sampling and archive actual response usage.
        self.params = {
            "temperature": 0.6,
            "top_p": 0.95,
            "response_format": {"type": "json_object"},
        }

    @property
    def signature(self):
        return {
            "endpoint": self.identity,
            "model": self.model,
            "params": self.params,
            "max_completion_tokens": self.max_tokens,
        }

    def backend(self, item_id, attempt):
        return OpenAIAPIBackend(
            self.base,
            self.key,
            self.model,
            ledger=ROOT / "costs/judges_ledger.jsonl",
            logs_dir=self.output / "calls" / item_id / str(attempt),
            timeout_s=self.timeout,
            effort=None,
            max_completion_tokens=self.max_tokens,
            extra_params=self.params,
            max_retries=0,
            budget_usd=self.budget_usd,
            prices_path=Path(self.prices_path) if self.prices_path else None,
            shared_budget_path=self.budget_path,
            shared_budget_usd=self.budget_usd,
            transport=ErrorArchiveTransport(
                self.output / "calls" / item_id / str(attempt)
                / "errors.jsonl",
                self.key,
            ),
        )


class JudgeQueue:
    def __init__(
        self,
        path,
        backend_factory,
        limiter,
        *,
        endpoint_signature=None,
        max_tokens=2048,
        event_log=None,
        ledger=None,
        archive_root=None,
        run_id="judge-queue",
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, rollout TEXT, judge TEXT, repeat INTEGER,
            evidence TEXT, prompt_hash TEXT, status TEXT, attempts INTEGER,
            result TEXT, error TEXT, updated TEXT
        )""")
        self.db.commit()
        self.backend_factory, self.limiter = backend_factory, limiter
        self.signature = endpoint_signature or {}
        self.max_tokens = max_tokens
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS configuration "
            "(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)"
        )
        configuration = canonical({
            "endpoint": self.signature, "max_tokens": self.max_tokens,
        })
        previous = self.db.execute(
            "SELECT value FROM configuration WHERE id=1"
        ).fetchone()
        if previous and previous[0] != configuration:
            self.db.close()
            raise ValueError("A durable queue cannot change endpoint settings")
        self.db.execute(
            "INSERT OR IGNORE INTO configuration VALUES (1, ?)",
            (configuration,),
        )
        self.db.commit()
        self.event_log = Path(
            event_log or self.path.with_suffix(".events.jsonl")
        )
        self.ledger = Path(ledger) if ledger else None
        self.archive_root = Path(archive_root) if archive_root else None
        self.run_id = run_id

    def enqueue(self, rollout, judge, evidence, repeat=0):
        if type(repeat) is not int or repeat < 0:
            raise ValueError("repeat must be a nonnegative integer")
        prompt_hash = digest(build_prompt(judge, evidence))
        item_id = digest(
            canonical(
                {
                    "rollout": rollout,
                    "judge": judge,
                    "repeat": repeat,
                    "prompt_hash": prompt_hash,
                    "endpoint": self.signature,
                    "max_tokens": self.max_tokens,
                }
            )
        )
        self.db.execute(
            "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                rollout,
                judge,
                repeat,
                canonical(evidence.to_dict()),
                prompt_hash,
                "pending",
                0,
                None,
                None,
                utc_now(),
            ),
        )
        self.db.commit()
        return item_id

    def rows(self):
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM items ORDER BY rowid")
        ]

    def _event(self, item, status, **extra):
        append_jsonl(
            self.event_log,
            {
                "ts": utc_now(),
                "item_id": item["id"],
                "status": status,
                **extra,
            },
        )

    def _recover(self):
        """Recover durable ledger responses after a crash before DB commit."""
        records = {}
        if self.ledger and self.ledger.exists():
            for line in self.ledger.read_text().splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("run_id") == self.run_id:
                    records.setdefault(record.get("task"), []).append(record)
        for item in self.rows():
            if item["status"] != "running":
                continue
            self._recover_response_archive(item, records)
            recovered = None
            for record in records.get(item["id"], []):
                if not record.get("ok"):
                    continue
                raw = Path(record["raw_dir"])
                try:
                    payload = json.loads((raw / "request.json").read_text())
                    if (
                        digest(payload["messages"][0]["content"])
                        != item["prompt_hash"]
                    ):
                        continue
                    response = json.loads((raw / "response.json").read_text())
                    text = response["choices"][0]["message"]["content"]
                    score, rationale = parse_response(item["judge"], text)
                    recovered = {
                        "judge": item["judge"],
                        "score": score,
                        "rationale": rationale,
                        "raw_rationale": text,
                        "record": record,
                        "call_id": record["call_id"],
                        "model": record.get("model"),
                        "prompt_sha256": item["prompt_hash"],
                        "prompt_version": PROMPT_VERSION,
                    }
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            status = (
                "done"
                if recovered
                else ("failed" if item["attempts"] >= 2 else "pending")
            )
            self.db.execute(
                "UPDATE items SET status=?,result=?,error=?,updated=? "
                "WHERE id=?",
                (
                    status,
                    canonical(recovered) if recovered else None,
                    None if recovered else "interrupted_attempt",
                    utc_now(),
                    item["id"],
                ),
            )
            self.db.commit()
            self._event(item, status, recovery=True)

    def _recover_response_archive(self, item, records):
        """A saved HTTP response must not be billed again after a hard kill.

        If the process died before the backend's ledger append, reconstruct
        only observable fields. Keep billing and latency unknown and retain
        the original shared-budget reservation. Do not fabricate telemetry.
        """
        if self.archive_root is None or self.ledger is None:
            return
        known = {r["call_id"] for r in records.get(item["id"], [])}
        for path in sorted((self.archive_root / item["id"]).glob(
            "*/*/response.json"
        )):
            if path.parent.name in known:
                continue
            try:
                request_path = path.parent / "request.json"
                request = json.loads(request_path.read_text())
                if digest(request["messages"][0]["content"]) != (
                    item["prompt_hash"]
                ):
                    continue
                response = json.loads(path.read_text())
                choice = response["choices"][0]
                content = choice["message"].get("content") or ""
                usage = response.get("usage") or {}
                prompt_usage = usage.get("prompt_tokens_details") or {}
                output_usage = usage.get("completion_tokens_details") or {}
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                continue
            record = {
                "phase": "P1.4", "run_id": self.run_id,
                "task": item["id"], "role": "judge",
                "arm": item["judge"].upper(), "iteration": item["repeat"],
                "call_id": path.parent.name, "backend": "openai_api",
                "ts": datetime.fromtimestamp(
                    request_path.stat().st_mtime, timezone.utc,
                ).isoformat(),
                "requested_model": request["model"],
                "model": response.get("model") or request["model"],
                "raw_dir": str(path.parent), "ok": bool(content),
                "finish_reason": choice.get("finish_reason"),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cached_input_tokens": prompt_usage.get("cached_tokens"),
                "cache_write_tokens": prompt_usage.get("cache_write_tokens"),
                "reasoning_tokens": output_usage.get("reasoning_tokens"),
                "cost_usd": None, "cost_source": "unknown", "wall_s": None,
                "api_ms": None, "http_429s": 0, "attempts": [],
                "note": "Recovered response after interruption before ledger",
                "request_params": {
                    k: v for k, v in request.items()
                    if k not in {"messages", "model"}
                },
                "recovered_from_archive": True,
            }
            append_jsonl(self.ledger, record)
            records.setdefault(item["id"], []).append(record)

    async def _one(self, item):
        evidence = JudgeInput.from_dict(json.loads(item["evidence"]))
        prompt = build_prompt(item["judge"], evidence)
        if digest(prompt) != item["prompt_hash"]:
            raise ValueError("Queued evidence or prompt changed after enqueue")
        reservation = len(prompt.encode()) + 256 + self.max_tokens
        for attempt in range(item["attempts"] + 1, 3):
            try:
                waited = await self.limiter.acquire(reservation)
            except ValueError:
                self.db.execute(
                    "UPDATE items SET status='failed',error=?,updated=? "
                    "WHERE id=?",
                    ("endpoint_capacity_exceeded", utc_now(), item["id"]),
                )
                self.db.commit()
                self._event(
                    item, "failed", reason="endpoint_capacity_exceeded"
                )
                return
            self.db.execute(
                "UPDATE items SET status='running',attempts=?,updated=? "
                "WHERE id=?",
                (attempt, utc_now(), item["id"]),
            )
            self.db.commit()
            self._event(
                item,
                "dispatch",
                attempt=attempt,
                reserved_tokens=reservation,
                limiter_wait_s=waited,
            )
            backend = self.backend_factory(item["id"], attempt)
            tags = CallTags(
                "P1.4",
                self.run_id,
                item["judge"].upper(),
                item["repeat"],
                item["id"],
                "judge",
            )
            try:
                result = await judge_once(
                    backend, item["judge"], evidence, tags
                )
            except JudgeFailure as exc:
                record = exc.record
                details = record.get("attempts") or []
                delay = (
                    retry_delay(details[-1].get("headers", {}), 0)
                    if details
                    else 1
                )
                self.limiter.defer(delay)
                status = "failed" if attempt == 2 else "pending"
                self.db.execute(
                    "UPDATE items SET status=?,error=?,updated=? WHERE id=?",
                    (status, str(exc), utc_now(), item["id"]),
                )
                self.db.commit()
                self._event(
                    item,
                    status,
                    reason=str(exc),
                    attempt=attempt,
                    call_id=record.get("call_id"),
                )
            else:
                self.db.execute(
                    "UPDATE items SET status='done',result=?,error=NULL,"
                    "updated=? WHERE id=?",
                    (canonical(result), utc_now(), item["id"]),
                )
                self.db.commit()
                self._event(
                    item, "done", attempt=attempt, call_id=result["call_id"]
                )
                return

    async def run(self, concurrency=4, *, limit=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 4:
            raise ValueError("concurrency must be an integer from 1 to 4")
        # A restart may reclaim running items only after exclusive ownership.
        with self.path.with_suffix(".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "This queue already has an active runner"
                ) from exc
            self._recover()
            pending = asyncio.Queue()
            for row in self.rows():
                if row["status"] == "pending" and (
                    limit is None or pending.qsize() < limit
                ):
                    pending.put_nowait(row)

            async def worker():
                while not pending.empty():
                    try:
                        item = pending.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        await self._one(item)
                    finally:
                        pending.task_done()

            async with asyncio.TaskGroup() as group:
                for _ in range(concurrency):
                    group.create_task(worker())
        return self.counts()

    def counts(self):
        return dict(
            self.db.execute(
                "SELECT status,count(*) FROM items GROUP BY status"
            ).fetchall()
        )

    def close(self):
        self.db.close()


def export_trace(trace_path, output, *, observation_chars=None):
    """Read only the trace; sanitize before any visibility projection."""
    raw = Path(trace_path).read_bytes()
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    full = sanitize(records)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    raw_hash = hashlib.sha256(raw).hexdigest()
    (output / "sanitized-full.json").write_text(
        canonical(full.trajectory.to_dict())
    )
    # Apply projection to already-sanitized fields, so hidden content beyond
    # the visibility cutoff can never cause a partially exposed tainted field.
    visible, projection = full.trajectory.events, []
    if observation_chars is not None:
        visible, projection = solver_visible(visible, observation_chars)
    judged = sanitize(visible)
    (output / "sanitized.json").write_text(
        canonical(judged.trajectory.to_dict())
    )
    (output / "redactions.json").write_text(
        canonical(
            {
                "source_sha256": raw_hash,
                "redactions": full.redactions,
                "visibility_projection": projection,
                "projection_redactions": judged.redactions,
                "sanitized_sha256": digest(judged.trajectory.events_json),
            }
        )
    )
    instructions = [
        e["text"]
        for e in judged.trajectory.events
        if e["kind"] == "instruction"
    ]
    if len(instructions) != 1:
        raise ValueError("Exactly one task instruction is required")
    return JudgeInput(instructions[0], judged.trajectory)


def configured_queue(prefix, output, config, budget_usd=15):
    endpoint = Endpoint(
        prefix, config, output, ROOT / "costs/judges_budget.json", budget_usd
    )
    limiter = TokenBucketLimiter(
        endpoint.rpm,
        endpoint.tpm,
        path=ROOT / "logs/judges/limiters.sqlite",
        endpoint=endpoint.identity,
    )
    suffix = config.get(f"{prefix}_QUEUE_SUFFIX", "")
    if suffix and (not suffix.isascii() or not suffix.isalnum()):
        raise ValueError("Queue suffix must contain ASCII letters/digits")
    queue_name = prefix.lower() + (f"-{suffix}" if suffix else "")
    queue = JudgeQueue(
        Path(output) / f"{queue_name}.sqlite",
        endpoint.backend,
        limiter,
        endpoint_signature=endpoint.signature,
        max_tokens=endpoint.max_tokens,
        ledger=ROOT / "costs/judges_ledger.jsonl",
        archive_root=Path(output) / "calls",
        run_id=f"{Path(output).name}-{queue_name}",
    )
    return queue, endpoint


async def main_async(args):
    job = Path(args.job).resolve()
    judges = args.judges.lower().split(",")
    if not set(judges) <= {"a1", "a2"} or args.repeats < 1:
        raise ValueError("judges must be a1,a2 and repeats must be positive")
    output = ROOT / "logs/judges" / (job.name + "-" + args.endpoint.lower())
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    queue, _ = configured_queue(args.endpoint, output, config)
    try:
        for trace in sorted(job.glob("*/agent/trace.jsonl")):
            trial = trace.parents[1]
            # No result contents enter this ingestion path. Presence alone is
            # the Harbor completion marker; incomplete trials are left alone.
            if not (trial / "result.json").is_file():
                continue
            evidence = export_trace(trace, output / "traces" / trial.name)
            for repeat in range(args.repeats):
                for judge in judges:
                    queue.enqueue(str(trial), judge, evidence, repeat)
        print(canonical(await queue.run(args.concurrency)), flush=True)
    finally:
        queue.close()
        queue.limiter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--judges", default="a1,a2")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--endpoint", choices=["JUDGE", "XJUDGE"], default="JUDGE"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
