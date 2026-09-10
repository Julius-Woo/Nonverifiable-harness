"""Crash-durable admission, money reservations, and request lifecycle audit."""

import asyncio
import hashlib
import json
import math
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

import httpx

from evolution.candidates import atomic_json
from evolution.judge_queue import TokenBucketLimiter
from evolution.sanitize import canonical, digest
from harness.ledger import append_jsonl, utc_now
from harness.openai_api import OpenAIAPIBackend, pricing_model


class BudgetHalt(RuntimeError):
    """A binding experiment/session guard; never an experimental retry."""

    def __init__(self, message, *, phase=False):
        super().__init__(message)
        self.phase = phase


class PhaseGuard:
    def __init__(
        self, path, *, estimate=20, ceiling=30, hours=4, clock=time.time
    ):
        if any(
            not math.isfinite(x) or x <= 0 for x in (estimate, ceiling, hours)
        ):
            raise ValueError("Budget and duration must be positive")
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS phase "
            "(id INTEGER PRIMARY KEY, started REAL, deadline REAL, "
            "estimate REAL, ceiling REAL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS requests "
            "(id TEXT PRIMARY KEY, scope TEXT, reserved REAL, "
            "charged REAL, status TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS phase_halt "
            "(id INTEGER PRIMARY KEY, reason TEXT, occurred REAL)"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO phase VALUES(1,?,?,?,?)",
            (clock(), clock() + hours * 3600, estimate, ceiling),
        )
        self.db.commit()
        row = self.db.execute("SELECT * FROM phase").fetchone()
        self.deadline, self.limit = row[2], min(1.5 * row[3], row[4])

    def used(self):
        return self.db.execute(
            "SELECT coalesce(sum(coalesce(charged,reserved)),0) FROM requests"
        ).fetchone()[0]

    def check(self, projected=0):
        # Explicit controller amendments are persisted; constructor flags
        # never reset them when a worker or arm resumes.
        self.deadline = self.db.execute(
            "SELECT deadline FROM phase WHERE id=1"
        ).fetchone()[0]
        halted = self.db.execute(
            "SELECT reason FROM phase_halt WHERE id=1"
        ).fetchone()
        if halted:
            raise BudgetHalt(halted[0], phase=True)
        reason = None
        if self.clock() >= self.deadline:
            reason = "Phase wall-clock limit reached"
        elif self.used() + projected > self.limit + 1e-10:
            reason = "Projected phase API budget exceeded"
        if reason:
            self.persist_halt(reason)
            raise BudgetHalt(reason, phase=True)

    def persist_halt(self, reason):
        """Stop later roles even when a rejected reservation spent nothing."""
        transaction = self.db.in_transaction
        self.db.execute(
            "INSERT OR IGNORE INTO phase_halt VALUES(1,?,?)",
            (reason, self.clock()),
        )
        if not transaction:
            self.db.commit()

    def reserve(self, request_id, scope, amount, scope_limit):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.check(amount)
            used = self.db.execute(
                "SELECT coalesce(sum(coalesce(charged,reserved)),0) "
                "FROM requests WHERE scope=?",
                (scope,),
            ).fetchone()[0]
            if used + amount > scope_limit + 1e-10:
                raise BudgetHalt("Projected session/rollout budget exceeded")
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,NULL,?)",
                (request_id, scope, amount, "intent"),
            )
            self.db.commit()
        except BaseException as exc:
            self.db.rollback()
            if isinstance(exc, BudgetHalt) and exc.phase:
                # The reservation rollback must not also erase a phase halt.
                self.persist_halt(str(exc))
            raise

    def settle(self, request_id, amount, status):
        # Unknown charges retain the full conservative reservation.
        self.db.execute(
            "UPDATE requests SET charged=?,status=? WHERE id=?",
            (amount, status, request_id),
        )
        self.db.commit()

    def close(self):
        self.db.close()


class AuditTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        inner,
        backend,
        tags,
        guard,
        limiter,
        audit,
        scope,
        scope_limit,
    ):
        self.inner, self.backend, self.tags = inner, backend, tags
        self.guard, self.limiter, self.audit = guard, limiter, Path(audit)
        self.scope, self.scope_limit = scope, scope_limit

    async def handle_async_request(self, request):
        payload = json.loads(request.content)
        raw_dirs = [
            p.parent for p in self.backend.logs_dir.glob("*/request.json")
        ]
        backend_raw = max(raw_dirs, key=lambda p: p.stat().st_mtime_ns)
        # The backend archives this exact payload before transport execution.
        # Cache partitioning belongs in metadata, never in prompt messages.
        body = request.content.decode()
        reservation = self.backend.projected_cost(body)
        tokens = (
            len(body.encode())
            + 256
            + self.backend.params["max_completion_tokens"]
        )
        self.guard.check(reservation)
        waited = await self.limiter.acquire(tokens)
        request_id = uuid.uuid4().hex
        self.guard.reserve(
            request_id, self.scope, reservation, self.scope_limit
        )
        archive = self.audit.parent / "requests" / request_id
        archive.mkdir(parents=True)
        atomic_json(archive / "request.json", payload)
        row = {
            "ts": utc_now(),
            "event": "request_intent",
            "id": request_id,
            "run_id": self.tags.run_id,
            "arm": self.tags.arm,
            "iteration": self.tags.iteration,
            "task": self.tags.task,
            "role": self.tags.role,
            "scope": self.scope,
            "reserved_usd": reservation,
            "reserved_tokens": tokens,
            "limiter_wait_s": waited,
            "archive": str(archive),
            "backend_raw_dir": str(backend_raw),
            "payload_sha256": hashlib.sha256(request.content).hexdigest(),
            "prompt_sha256": digest(canonical(payload["messages"])),
            "cache_user": payload["user"],
        }
        append_jsonl(self.audit, row)
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
            raw = await response.aread()
            safe = raw.decode(errors="replace").replace(
                self.backend.api_key, "[REDACTED]"
            )
            try:
                data = json.loads(safe)
            except ValueError:
                data = {"unparsed": safe}
            atomic_json(archive / "response.json", data)
            # Keep JudgeQueue's existing response-archive recovery usable if
            # killed before the backend ledger append. The canonical request
            # and audit archives both contain the exact outbound payload.
            atomic_json(backend_raw / "response.json", data)
            usage = data.get("usage") or {}
            rates = self.backend.prices["models"].get(
                pricing_model(
                    data.get("model") or self.backend.model,
                    self.backend.prices,
                )
            )
            upper = None
            if rates and all(
                type(usage.get(k)) is int
                for k in (
                    "prompt_tokens",
                    "completion_tokens",
                )
            ):
                upper = (
                    usage["prompt_tokens"]
                    * max(rates["input"], rates["cache_write"])
                    + usage["completion_tokens"] * rates["output"]
                ) / 1e6
            if response.status_code == 429:
                upper = 0.0
                from harness.openai_api import retry_delay

                self.limiter.defer(retry_delay(response.headers, 0))
            self.guard.settle(request_id, upper, "response")
            append_jsonl(
                self.audit,
                {
                    **row,
                    "ts": utc_now(),
                    "event": "request_response",
                    "status_code": response.status_code,
                    "wall_s": time.monotonic() - started,
                    "usage": usage,
                    "uncached_upper_usd": upper,
                },
            )
            return response
        except BaseException as exc:
            append_jsonl(
                self.audit,
                {
                    **row,
                    "ts": utc_now(),
                    "event": "request_unresolved",
                    "error": type(exc).__name__,
                    "wall_s": time.monotonic() - started,
                },
            )
            raise

    async def aclose(self):
        await self.inner.aclose()


class AccountedBackend(OpenAIAPIBackend):
    """Use existing backend accounting plus pre-dispatch transport intents."""

    owns_endpoint_quota = True

    def __init__(
        self,
        *,
        guard_path,
        limiter_path,
        audit_path,
        tags,
        scope,
        rpm=250,
        tpm=250000,
        max_calls=24,
        **kwargs,
    ):
        self.audit_path = Path(audit_path)
        self.guard = PhaseGuard(guard_path)
        self.tags, self.scope, self.max_calls = tags, scope, max_calls
        self.calls = 0
        self.external_transport = kwargs.pop("transport", None)
        super().__init__(**kwargs)
        endpoint = hashlib.sha256(
            (kwargs["base_url"].rstrip("/") + "/" + self.model).encode()
        ).hexdigest()
        self.limiter = TokenBucketLimiter(
            rpm,
            tpm,
            path=limiter_path,
            endpoint=endpoint,
        )

    async def complete(self, prompt, tags, **kwargs):
        if kwargs:
            raise ValueError(
                "Evolution only admits the calibrated JSON protocol"
            )
        self.guard.check()
        if self.calls >= self.max_calls:
            raise BudgetHalt("Host-enforced model-call cap reached")
        self.calls += 1
        # Non-prompt cache isolation. Providers must honor this field for a
        # cache-separation claim; local tests verify the wire mechanism only.
        self.params = {
            **self.params,
            "user": digest(
                f"{self.tags.run_id}:{self.tags.arm}:{self.tags.role}:"
                f"{self.tags.task}:{uuid.uuid4().hex}"
            ),
        }
        self.transport = AuditTransport(
            inner=self.external_transport or httpx.AsyncHTTPTransport(),
            backend=self,
            tags=self.tags,
            guard=self.guard,
            limiter=self.limiter,
            audit=self.audit_path,
            scope=self.scope,
            scope_limit=self.budget_usd,
        )
        try:
            async with asyncio.timeout(
                max(0, self.guard.deadline - time.time())
            ):
                result = await super().complete(prompt, replace(self.tags))
        except TimeoutError as exc:
            self.guard.persist_halt("Phase wall-clock limit reached")
            raise BudgetHalt(
                "Phase wall-clock limit reached", phase=True
            ) from exc
        if "budget exceeded" in result.record.get("note", "").lower():
            raise BudgetHalt(result.record["note"])
        return result

    def close(self):
        self.guard.close()
        self.limiter.close()


def cost_summary(ledger, audit, *, arm=None, iteration=None):
    records = []
    if Path(ledger).exists():
        records = [
            json.loads(s) for s in Path(ledger).read_text().splitlines()
        ]
    events = []
    if Path(audit).exists():
        events = [json.loads(s) for s in Path(audit).read_text().splitlines()]

    def included(row):
        return (arm is None or row.get("arm") == arm) and (
            iteration is None or row.get("iteration") == iteration
        )

    records = [r for r in records if included(r)]
    events = [r for r in events if included(r)]
    intents = {e["id"]: e for e in events if e["event"] == "request_intent"}
    responses = {
        e["id"]: e for e in events if e["event"] == "request_response"
    }
    return {
        "calls": len(intents),
        "known_usd": sum(
            r.get("cost_usd") or r.get("known_response_cost_usd") or 0
            for r in records
        ),
        "unknown_ledger_calls": sum(
            r.get("cost_usd") is None for r in records
        ),
        "uncached_upper_usd": sum(
            e.get("uncached_upper_usd") or 0 for e in responses.values()
        ),
        "unresolved_requests": sum(
            i not in responses
            or responses[i].get("uncached_upper_usd") is None
            for i in intents
        ),
        "reserved_unresolved_usd": sum(
            e["reserved_usd"]
            for i, e in intents.items()
            if i not in responses
            or responses[i].get("uncached_upper_usd") is None
        ),
        "api_wall_s": sum(
            e.get("wall_s", 0)
            for e in events
            if e["event"] in {"request_response", "request_unresolved"}
        ),
        "tokens": {
            k: sum(r.get(k) or 0 for r in records)
            for k in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            )
        },
        "roles": {
            role: {
                "calls": sum(e["role"] == role for e in intents.values()),
                "uncached_upper_usd": sum(
                    e.get("uncached_upper_usd") or 0
                    for e in responses.values()
                    if e["role"] == role
                ),
            }
            for role in ("task", "evolver", "judge")
        },
    }
