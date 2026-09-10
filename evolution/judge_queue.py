"""Durable asynchronous judging; only terminal flags may leave trusted results.

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
import uuid
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
    prompt_hash,
    prompt_messages,
)
from evolution.sanitize import (
    DEFAULT_OBSERVATION_CHARS,
    DEFAULT_TRAJECTORY_CHARS,
    VERSION,
    canonical,
    digest,
    sanitize,
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
            append_jsonl(
                self.path,
                {
                    "ts": utc_now(),
                    "status_code": response.status_code,
                    "body": body.replace(self.key, "[REDACTED]"),
                },
            )
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
        self.rpm = float(
            config.get(
                f"{prefix}_RPM",
                100 if prefix == "XJUDGE" else 250,
            )
        )
        self.tpm = float(
            config.get(
                f"{prefix}_TPM",
                100000 if prefix == "XJUDGE" else 250000,
            )
        )
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
            extra_params={
                **self.params,
                "user": digest(
                    f"{self.identity}:{item_id}:{attempt}:{uuid.uuid4().hex}"
                ),
            },
            max_retries=0,
            budget_usd=self.budget_usd,
            prices_path=Path(self.prices_path) if self.prices_path else None,
            shared_budget_path=self.budget_path,
            shared_budget_usd=self.budget_usd,
            transport=ErrorArchiveTransport(
                self.output
                / "calls"
                / item_id
                / str(attempt)
                / "errors.jsonl",
                self.key,
            ),
        )


def preflight_prompt(prompt, *, max_tokens=2048, tpm=250_000):
    """Measure exact wire messages and the unchanged backend's admission bound.

    The backend limits content UTF-8 bytes + 256 to 200,000. Serialized wire
    bytes additionally include JSON quoting and are reported separately.
    One minute of TPM includes the maximum completion reservation.
    """
    content_bytes = len(prompt.encode("utf-8"))
    input_reservation = content_bytes + 256
    reservation = input_reservation + max_tokens
    report = {
        "wire_prompt_bytes": len(canonical(prompt_messages(prompt)).encode()),
        "prompt_content_bytes": content_bytes,
        "backend_input_reservation": input_reservation,
        "backend_limit": 200_000,
        "reserved_tokens": reservation,
        "endpoint_tpm": tpm,
    }
    if input_reservation > 200_000:
        raise ValueError(
            f"Evidence v3 wire prompt still exceeds backend limit after caps: "
            f"{input_reservation} input reservation > 200000 "
            f"({report['wire_prompt_bytes']} serialized wire bytes)"
        )
    if reservation > tpm:
        raise ValueError(
            f"Evidence v3 prompt plus completion reservation {reservation} "
            f"exceeds one minute's endpoint TPM {tpm}; "
            "choose explicit evidence caps that fit this endpoint"
        )
    return report


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
        limiter_owner="queue",
    ):
        if limiter_owner not in {"queue", "transport"}:
            raise ValueError("Unknown limiter owner")
        self.limiter_owner = limiter_owner
        self.evidence_version = VERSION
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, rollout TEXT, judge TEXT, repeat INTEGER,
            evidence TEXT, prompt_hash TEXT, status TEXT, attempts INTEGER,
            result TEXT, error TEXT, updated TEXT, evidence_version TEXT,
            UNIQUE(rollout, judge, repeat, evidence_version)
        )""")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        if "evidence_version" not in columns:
            self.db.close()
            raise ValueError("Legacy evidence version: use a new queue")
        versions = {
            r[0]
            for r in self.db.execute(
                "SELECT DISTINCT evidence_version FROM items"
            )
        }
        if versions - {VERSION}:
            self.db.close()
            raise ValueError("Mixed or unsupported evidence versions")
        self.db.commit()
        self.backend_factory, self.limiter = backend_factory, limiter
        self.signature = endpoint_signature or {}
        self.max_tokens = max_tokens
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS configuration "
            "(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT)"
        )
        configuration = canonical(
            {
                "endpoint": self.signature,
                "max_tokens": self.max_tokens,
                "evidence_version": VERSION,
                "limiter_owner": limiter_owner,
            }
        )
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
        if evidence.trajectory.version != self.evidence_version:
            raise ValueError("Mixed evidence versions are forbidden")
        prompt = build_prompt(judge, evidence)
        preflight_prompt(
            prompt, max_tokens=self.max_tokens, tpm=self.limiter.tpm
        )
        frozen_hash = prompt_hash(prompt)
        serialized = canonical(evidence.to_dict())
        item_id = digest(
            canonical(
                {
                    "rollout": rollout,
                    "judge": judge,
                    "repeat": repeat,
                    "evidence_version": self.evidence_version,
                }
            )
        )
        self.db.execute("BEGIN IMMEDIATE")
        first = self.db.execute(
            "SELECT evidence FROM items LIMIT 1"
        ).fetchone()
        if (
            first
            and json.loads(first[0])["trajectory"]["caps"]
            != (evidence.trajectory.to_dict()["caps"])
        ):
            self.db.rollback()
            raise ValueError("Mixed evidence cap parameters are forbidden")
        previous = self.db.execute(
            "SELECT * FROM items WHERE rollout=? AND judge=? AND repeat=? "
            "AND evidence_version=?",
            (rollout, judge, repeat, self.evidence_version),
        ).fetchone()
        if previous and (
            previous["evidence"] != serialized
            or previous["prompt_hash"] != frozen_hash
        ):
            self.db.rollback()
            self._event(
                previous,
                "conflict",
                reason="frozen_input_changed",
                proposed_prompt_hash=frozen_hash,
                proposed_evidence_hash=digest(serialized),
            )
            raise ValueError(
                "Frozen measurement conflict: prompt or evidence changed"
            )
        self.db.execute(
            "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                rollout,
                judge,
                repeat,
                serialized,
                frozen_hash,
                "pending",
                0,
                None,
                None,
                utc_now(),
                self.evidence_version,
            ),
        )
        self.db.commit()
        return previous["id"] if previous else item_id

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
                        digest(canonical(payload["messages"]))
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
                        "evidence_version": item["evidence_version"],
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
        for path in sorted(
            (self.archive_root / item["id"]).glob("*/*/response.json")
        ):
            if path.parent.name in known:
                continue
            try:
                request_path = path.parent / "request.json"
                request = json.loads(request_path.read_text())
                if (
                    digest(canonical(request["messages"]))
                    != (item["prompt_hash"])
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
                "phase": "P1.4",
                "run_id": self.run_id,
                "task": item["id"],
                "role": "judge",
                "arm": item["judge"].upper(),
                "iteration": item["repeat"],
                "call_id": path.parent.name,
                "backend": "openai_api",
                "ts": datetime.fromtimestamp(
                    request_path.stat().st_mtime,
                    timezone.utc,
                ).isoformat(),
                "requested_model": request["model"],
                "model": response.get("model") or request["model"],
                "raw_dir": str(path.parent),
                "ok": bool(content),
                "finish_reason": choice.get("finish_reason"),
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cached_input_tokens": prompt_usage.get("cached_tokens"),
                "cache_write_tokens": prompt_usage.get("cache_write_tokens"),
                "reasoning_tokens": output_usage.get("reasoning_tokens"),
                "cost_usd": None,
                "cost_source": "unknown",
                "wall_s": None,
                "api_ms": None,
                "http_429s": 0,
                "attempts": [],
                "note": "Recovered response after interruption before ledger",
                "request_params": {
                    k: v
                    for k, v in request.items()
                    if k not in {"messages", "model"}
                },
                "recovered_from_archive": True,
            }
            append_jsonl(self.ledger, record)
            records.setdefault(item["id"], []).append(record)

    async def _one(self, item):
        evidence = JudgeInput.from_dict(json.loads(item["evidence"]))
        prompt = build_prompt(item["judge"], evidence)
        if prompt_hash(prompt) != item["prompt_hash"]:
            raise ValueError("Queued evidence or prompt changed after enqueue")
        reservation = len(prompt.encode()) + 256 + self.max_tokens
        for attempt in range(item["attempts"] + 1, 3):
            backend = self.backend_factory(item["id"], attempt)
            transport_owned = getattr(backend, "owns_endpoint_quota", False)
            if transport_owned != (self.limiter_owner == "transport"):
                raise ValueError(
                    "Queue and backend disagree on limiter ownership"
                )
            try:
                if hasattr(backend, "projected_cost"):
                    backend.projected_cost(prompt)
                if reservation > self.limiter.tpm:
                    raise ValueError("Endpoint capacity exceeded")
                waited = (
                    await self.limiter.acquire(reservation)
                    if self.limiter_owner == "queue"
                    else 0
                )
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
                reserved_tokens=(
                    reservation if self.limiter_owner == "queue" else 0
                ),
                limiter_owner=self.limiter_owner,
                limiter_wait_s=waited,
            )
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
                owner = backend.limiter if transport_owned else self.limiter
                owner.defer(delay)
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

    async def run(
        self, concurrency=4, *, limit=None, source=None, on_result=None
    ):
        """Drain durable work and an optional async stream with backpressure.

        Source yields (rollout, judge, JudgeInput, repeat). Committed results
        reach on_result immediately, including already settled keys.
        """
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
            pending = asyncio.Queue(maxsize=2 * concurrency)
            scheduled = set()

            async def emit(item):
                if on_result is not None:
                    await on_result(
                        dict(
                            self.db.execute(
                                "SELECT * FROM items WHERE id=?", (item["id"],)
                            ).fetchone()
                        )
                    )

            async def producer():
                count = 0
                for row in self.rows():
                    if row["status"] == "pending" and (
                        limit is None or count < limit
                    ):
                        scheduled.add(row["id"])
                        await pending.put(row)
                        count += 1
                if source is not None:
                    async for rollout, judge, evidence, repeat in source:
                        identity = self.enqueue(
                            rollout, judge, evidence, repeat
                        )
                        row = dict(
                            self.db.execute(
                                "SELECT * FROM items WHERE id=?", (identity,)
                            ).fetchone()
                        )
                        if row["status"] in {"done", "failed"}:
                            await emit(row)
                        elif identity not in scheduled:
                            scheduled.add(identity)
                            await pending.put(row)
                for _ in range(concurrency):
                    await pending.put(None)

            async def worker():
                while True:
                    item = await pending.get()
                    try:
                        if item is None:
                            return
                        await self._one(item)
                        await emit(item)
                    finally:
                        pending.task_done()

            async with asyncio.TaskGroup() as group:
                group.create_task(producer())
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


def export_trace(
    trace_path,
    output,
    *,
    observation_chars=DEFAULT_OBSERVATION_CHARS,
    trajectory_chars=DEFAULT_TRAJECTORY_CHARS,
    result=None,
    execution=None,
):
    """Export v3 capped sanitized observations and trusted terminal fields.

    Result/execution records are controller-only inputs: only the termination
    allowlist is exported. No verifier diagnostics, rewards, or paths enter it.
    """
    raw = Path(trace_path).read_bytes()
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    full = sanitize(
        records,
        result=result,
        execution=execution,
        observation_chars=observation_chars,
        trajectory_chars=trajectory_chars,
    )
    instructions = [
        e["text"] for e in full.trajectory.events if e["kind"] == "instruction"
    ]
    if len(instructions) != 1:
        raise ValueError("Exactly one task instruction is required")
    evidence = JudgeInput(instructions[0], full.trajectory)
    documents = {
        name: canonical(full.trajectory.to_dict())
        for name in ("sanitized-full.json", "sanitized.json")
    }
    documents["redactions.json"] = canonical(
        {
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "redactions": full.redactions,
            "evidence_version": VERSION,
            "observation_policy": "v3_head_tail_then_longest_middle",
            "caps": full.trajectory.to_dict()["caps"],
            "truncations": full.trajectory.truncations,
            "sanitized_sha256": digest(full.trajectory.events_json),
        }
    )
    output = Path(output)
    # Check all files before writing any: a rejected frozen-input change must
    # not replace the original evidence or redaction provenance on disk.
    for name, content in documents.items():
        path = output / name
        if path.exists() and path.read_text() != content:
            raise ValueError("Frozen trace export conflict; use a new version")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in documents.items():
        path = output / name
        if not path.exists():
            path.write_text(content)
    return evidence


def ingest_manifest(
    queue, manifest, output, *, judges=("a1", "a2"), repeats=1
):
    """Layout-independent trusted trajectory manifest; metadata stays local.

    Envelope: {"trajectories": [{"rollout_id": str, "path": str,
    "metadata": object, "execution": object (optional)}]}. Paths are relative
    to the manifest file; no implicit result discovery or oracle export.
    """
    manifest = Path(manifest)
    data = json.loads(manifest.read_text())
    entries = data["trajectories"]
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be positive")
    identities = []
    for entry in entries:
        rollout = entry["rollout_id"]
        if not isinstance(rollout, str) or not rollout:
            raise ValueError("rollout_id must be nonempty")
        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        destination = Path(output) / "traces" / digest(rollout)
        evidence = export_trace(
            manifest.parent / entry["path"],
            destination,
            execution=entry.get("execution"),
        )
        for repeat in range(repeats):
            for judge in judges:
                identities.append(
                    queue.enqueue(rollout, judge, evidence, repeat)
                )
        (destination / "metadata.json").write_text(canonical(metadata))
    return identities


def configured_queue(prefix, output, config, budget_usd=15):
    endpoint = Endpoint(
        prefix,
        config,
        output,
        config.get("JUDGING_BUDGET_PATH", ROOT / "costs/judges_budget.json"),
        budget_usd,
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
    source = args.manifest or args.job
    job = Path(source).resolve()
    judges = args.judges.lower().split(",")
    if not set(judges) <= {"a1", "a2"} or args.repeats < 1:
        raise ValueError("judges must be a1,a2 and repeats must be positive")
    output = ROOT / "logs/judges" / (job.name + "-" + args.endpoint.lower())
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    queue, _ = configured_queue(args.endpoint, output, config)
    try:
        if args.manifest:
            ingest_manifest(
                queue,
                args.manifest,
                output,
                judges=judges,
                repeats=args.repeats,
            )
        for trace in (
            [] if args.manifest else sorted(job.glob("*/agent/trace.jsonl"))
        ):
            trial = trace.parents[1]
            # A trusted result marks completion. Only allowlisted execution
            # outcomes enter the evidence; verification contents stay local.
            if not (trial / "result.json").is_file():
                continue
            result = json.loads((trial / "result.json").read_text())
            evidence = export_trace(
                trace, output / "traces" / trial.name, result=result
            )
            for repeat in range(args.repeats):
                for judge in judges:
                    queue.enqueue(str(trial), judge, evidence, repeat)
        print(canonical(await queue.run(args.concurrency)), flush=True)
    finally:
        queue.close()
        queue.limiter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job")
    source.add_argument("--manifest", type=Path)
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
