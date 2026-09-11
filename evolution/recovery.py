"""Explicit, audited recovery of evolver transport failures without resampling.

Completed actions are replayed from the log without executing them or calling
an API. Ambiguous transport failures can be continued explicitly within the
original call and money caps; every failed attempt remains accounted.
Already-written source can be validated without another model call after
a prompt ceiling.
"""

import json
import shutil
from dataclasses import asdict

from evolution.accounting import AccountedBackend
from evolution.candidates import (
    Manifest,
    atomic_json,
    diff_source,
    freeze,
    scan_source,
    source_hash,
)
from evolution.prompts import render
from evolution.sanitize import digest
from evolution.workspace import ExecResult, Workspace
from harness.backends import Completion
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.seed import run_seed


def compact_context(prompt, limit=140000):
    """Shorten old observations; preserve the template and replies."""
    if len(json.dumps(prompt).encode()) <= limit:
        return prompt, []
    prefix, serialized = prompt.split("Conversation:\n", 1)
    history = json.loads(serialized)
    changed = []
    for cap in (3000, 1000, 300):
        for index, message in enumerate(history[1:], 1):
            content = message.get("content")
            if message["role"] != "user" or not isinstance(content, str):
                continue
            if len(content) <= cap:
                continue
            message["content"] = (
                content[:cap] + "\n[earlier observation shortened]"
            )
            changed.append({"message": index, "retained_characters": cap})
            reduced = prefix + "Conversation:\n" + json.dumps(history)
            if len(json.dumps(reduced).encode()) <= limit:
                return reduced, changed
    raise ValueError(
        "Evolver context exceeds limit after observation projection"
    )


class EvolverBackend(AccountedBackend):
    async def complete(self, prompt, tags, **kwargs):
        projected, changes = compact_context(prompt)
        if changes:
            append_jsonl(
                self.audit_path.parent / "context_projections.jsonl",
                {
                    "ts": utc_now(),
                    "arm": tags.arm,
                    "task": tags.task,
                    "original_sha256": digest(prompt),
                    "sent_sha256": digest(projected),
                    "changes": changes,
                },
            )
        return await super().complete(projected, tags, **kwargs)


class ReplayBackend:
    is_api = True

    def __init__(self, completed, live):
        self.completed, self.live, self.index = completed, live, 0

    async def complete(self, prompt, tags):
        if self.index < len(self.completed):
            record = self.completed[self.index]
            self.index += 1
            if record.get("wire_prompt") is not None:
                projected, _ = compact_context(prompt)
                if projected != record["wire_prompt"]:
                    raise ValueError(
                        "Recovered response prompt differs from replay"
                    )
            return Completion(
                record["text"],
                {
                    "call_id": record["call_id"],
                    "ok": True,
                    "note": "replayed",
                },
            )
        return await self.live.complete(prompt, tags)


class ReplayEnvironment:
    def __init__(self, observations, live):
        self.observations, self.live, self.index = observations, live, 0

    async def exec(self, command, timeout_sec=30):
        if self.index < len(self.observations):
            row = self.observations[self.index]
            self.index += 1
            if row["command"] != command:
                raise ValueError(
                    "Replay command does not match archived action"
                )
            if row.get("error"):
                raise TimeoutError(row["error"])
            return ExecResult(
                row.get("stdout", ""),
                row.get("stderr", ""),
                row.get("return_code", 0),
            )
        return await self.live.exec(command, timeout_sec=timeout_sec)


async def recover_sessions(loop):
    """Explicit continuation after a boundary pause, with archived failures."""
    if "A3" in loop.arm:
        raise ValueError(
            "A3 owns its operator retries; interrupted proposals are dropped"
        )
    checkpoint = loop.state.stage(f"checkpoint-{loop.iteration}")
    if checkpoint is None:
        raise ValueError("Recovery requires the active iteration checkpoint")
    from pathlib import Path

    parent = Path(checkpoint["candidate"])
    events = loop.logs / "session_recovery.jsonl"
    # Discard only plans proven never to have reached Harbor or the API.
    audit = loop.accounting / "requests.jsonl"
    calls = (
        [json.loads(s) for s in audit.read_text().splitlines()]
        if audit.exists()
        else []
    )
    dispatched = {
        r["task"] for r in calls if r["role"] in {"task", "a3-resolve"}
    }
    if loop.state.stage(f"finished-{loop.iteration}") is not None:
        raise ValueError("Cannot reopen a completed iteration")
    for stored in loop.state.db.execute("SELECT id,spec FROM trials"):
        spec = json.loads(stored["spec"])
        if (
            spec["iteration"] == loop.iteration
            and spec["partition"] == "sealed"
            and spec["stage"] != "seed-checkpoint"
            and stored["id"] in dispatched
        ):
            raise ValueError("Cannot reopen selection after sealed dispatch")
    for row in loop.state.db.execute("SELECT id,spec FROM trials").fetchall():
        spec = json.loads(row["spec"])
        if (
            spec["iteration"] == loop.iteration
            and spec["stage"] == "measurement"
            and row["id"] not in dispatched
            and not (loop.evaluator.jobs / row["id"]).exists()
        ):
            append_jsonl(
                events,
                {
                    "ts": utc_now(),
                    "event": "unstarted_plan_discarded",
                    "trial_id": row["id"],
                    "spec": spec,
                },
            )
            loop.state.db.execute(
                "DELETE FROM trials WHERE id=?", (row["id"],)
            )
    loop.state.db.commit()
    for slot in (1, 2):
        identity = f"i{loop.iteration:02}-c{slot}"
        key = f"proposal-{identity}"
        result = loop.state.stage(key)
        if not result or result["status"] == "valid":
            continue
        initial = dict(result)
        reason = result.get("reason", "")
        if reason not in {
            "interrupted_evolver_no_retry",
            "Backend failed: Prompt exceeds conservative short-context limit",
            "Backend failed: TimeoutError",
        }:
            raise ValueError(
                "Source-invalid and budget-capped proposals cannot retry"
            )
        work = loop.directory / "working" / identity
        session = loop.logs / "sessions" / identity
        prior_trace = session / "trace.jsonl"
        prior_failures = 0
        recovery_number = 1
        while True:
            name = (
                "recovery"
                if recovery_number == 1
                else f"recovery-{recovery_number}"
            )
            recovery = session / name
            if not recovery.exists():
                break
            previous = recovery / "trace.jsonl"
            if not previous.exists():
                raise ValueError("Previous recovery has no completed trace")
            prior_rows = [
                json.loads(s) for s in prior_trace.read_text().splitlines()
            ]
            prior_failures += sum(
                r["kind"] == "assistant" and not r["ok"] for r in prior_rows
            )
            prior_trace = previous
            recovery_number += 1
        if recovery_number > 2:
            raise ValueError(
                "At most two explicitly requested transport continuations"
            )
        recovery.mkdir(exist_ok=False)
        atomic_json(recovery / "initial-validation.json", initial)
        append_jsonl(
            events,
            {
                "ts": utc_now(),
                "event": "explicit_recovery_start",
                "candidate": identity,
                "initial_reason": reason,
                "recovery_number": recovery_number,
            },
        )
        result["recovery_attempted"] = True
        loop.state.stage(key, result)
        rows = [json.loads(s) for s in prior_trace.read_text().splitlines()]
        completed = [r for r in rows if r["kind"] == "assistant" and r["ok"]]
        traced = {r.get("call_id") for r in rows if r["kind"] == "assistant"}
        from pathlib import Path

        for call in calls:
            if (
                call.get("event") != "request_intent"
                or call.get("scope") != result["session_id"]
            ):
                continue
            response_path = Path(call["archive"]) / "response.json"
            if (
                Path(call["backend_raw_dir"]).name in traced
                or not response_path.exists()
            ):
                continue
            response = json.loads(response_path.read_text())
            choices = response.get("choices") or []
            if choices and choices[0].get("message", {}).get("content"):
                request = json.loads(
                    (Path(call["archive"]) / "request.json").read_text()
                )
                completed.append(
                    {
                        "kind": "assistant",
                        "ok": True,
                        "call_id": Path(call["backend_raw_dir"]).name,
                        "text": choices[0]["message"]["content"],
                        "wire_prompt": request["messages"][0]["content"],
                    }
                )
        observations = [
            r for r in rows if r["kind"] == "observation" and "command" in r
        ]
        if (
            "TimeoutError" in reason
            or reason == "interrupted_evolver_no_retry"
        ):
            observed_steps = {r.get("step") for r in observations}
            for reply in completed:
                # A response missing from the assistant trace preceded any
                # action dispatch and can safely be replayed once.
                if reply.get("wire_prompt") is not None:
                    continue
                try:
                    action = json.loads(reply["text"])
                except (ValueError, TypeError):
                    continue
                if (
                    isinstance(action, dict)
                    and action.get("action")
                    in {"terminal", "read_file", "write_file"}
                    and reply.get("step") not in observed_steps
                ):
                    raise ValueError(
                        "Uncertain action completion; "
                        "refusing side-effect replay"
                    )
        attempted = prior_failures + sum(
            r["kind"] == "assistant" for r in rows
        )
        durable_attempts = loop.guard.db.execute(
            "SELECT count(*) FROM requests WHERE scope=?",
            (result["session_id"],),
        ).fetchone()[0]
        attempted = max(attempted, durable_attempts)
        if attempted >= 24:
            raise ValueError("Original evolver call cap exhausted")
        paths, values = loop.canaries()
        backend = None
        with Workspace(
            work,
            f"nvhe-recover-{loop.arm.lower()}-{identity}",
            feedback=loop.evaluator.feedback,
            writable=True,
            audit=recovery / "boundary.json",
        ) as workspace:
            if not await workspace.canary_check(
                paths, values, recovery / "canaries.jsonl"
            ):
                raise RuntimeError("Recovery workspace isolation failed")
            if (
                "TimeoutError" in reason
                or reason == "interrupted_evolver_no_retry"
            ):
                tags = CallTags(
                    "P1.3",
                    loop.experiment,
                    loop.arm,
                    loop.iteration,
                    identity,
                    "evolver",
                )
                backend = EvolverBackend(
                    base_url=loop.config["EVOLVER_API_BASE"],
                    api_key=loop.config["EVOLVER_API_KEY"],
                    model=loop.config["EVOLVER_MODEL"],
                    ledger=loop.accounting / "ledger.jsonl",
                    logs_dir=recovery / "calls",
                    effort=None,
                    max_completion_tokens=4096,
                    max_retries=0,
                    extra_params={
                        "temperature": 0.6,
                        "top_p": 0.95,
                        "response_format": {"type": "json_object"},
                    },
                    budget_usd=5,
                    timeout_s=300,
                    prices_path=loop.root / "costs/judges_prices.json",
                    guard_path=loop.accounting / "budget.sqlite",
                    limiter_path=loop.root / "logs/evolution-endpoints.sqlite",
                    audit_path=audit,
                    tags=tags,
                    scope=result["session_id"],
                    rpm=float(loop.config.get("EVOLVER_RPM", 250)),
                    tpm=float(loop.config.get("EVOLVER_TPM", 250000)),
                    max_calls=24,
                )
                try:
                    answer = await run_seed(
                        render(loop.arm, loop.completion_allowance),
                        ReplayEnvironment(observations, workspace),
                        ReplayBackend(completed, backend),
                        tags,
                        recovery / "trace.jsonl",
                        max_steps=24 - (attempted - len(completed)),
                        tool_protocol="json",
                    )
                finally:
                    backend.close()
            else:
                # A completed write is an artifact even if the next API call
                # cannot fit. Require byte identity with the archived action.
                writes = []
                for row in completed:
                    action = json.loads(row["text"])
                    if (
                        action.get("action") == "write_file"
                        and action.get("path") == "/candidate/harness/seed.py"
                    ):
                        writes.append(action["content"])
                if (
                    not writes
                    or writes[-1] != (work / "harness/seed.py").read_text()
                ):
                    raise ValueError(
                        "No complete recorded source write to recover"
                    )
                answer = (
                    "Updated harness/seed.py; complete recorded source write "
                    "recovered without another model call after the "
                    "prompt ceiling."
                )
            result["import"] = await workspace.import_check()
            result["canary_ok"] = await workspace.canary_check(
                paths, values, recovery / "canaries-after.jsonl"
            )
        problems = scan_source(
            work,
            loop.directory / "candidates/seed/harness",
            [
                t["name"]
                for tasks in loop.evaluator.split["splits"].values()
                for t in tasks
            ],
        )
        diff = diff_source(parent, work)
        (recovery / "diff.patch").write_text(diff)
        if (
            not diff
            or not result["import"]["ok"]
            or not result["canary_ok"]
            or problems
        ):
            result.update(
                status="invalid",
                reason="recovered_source_validation_failed",
                scan_failures=problems,
            )
        else:
            manifest = Manifest(
                identity,
                parent.name,
                loop.arm,
                loop.iteration,
                result["session_id"],
                answer[:2000],
                source_hash(work),
            )
            manifest.write(work)
            destination = loop.directory / "candidates" / identity
            freeze(work, destination, manifest)
            result.update(
                status="valid",
                candidate=str(destination),
                manifest=asdict(manifest),
                initial_session_reason=reason,
                reason=None,
                recovered_without_api=backend is None,
            )
        loop.state.stage(key, result)
        atomic_json(recovery / "validation.json", result)
        append_jsonl(
            events,
            {
                "ts": utc_now(),
                "event": "explicit_recovery_result",
                "candidate": identity,
                "status": result["status"],
                "source_sha256": source_hash(work),
            },
        )
    (loop.directory / "pause.json").unlink(missing_ok=True)


def reconcile_paused(loop):
    """Requeue undispatched plans while preserving completed trials."""
    files = [
        loop.directory / name for name in ("pause.json", "pause-next.json")
    ]
    if not any(p.exists() for p in files):
        return
    events = loop.directory / "pause.events.jsonl"
    paused = (
        {json.loads(s)["trial_id"] for s in events.read_text().splitlines()}
        if events.exists()
        else set()
    )
    stages = set()
    for path in files:
        if path.exists():
            stages.update(json.loads(path.read_text()).get("stages", []))
    audit = loop.accounting / "requests.jsonl"
    dispatched = (
        {
            r["task"]
            for s in audit.read_text().splitlines()
            if (r := json.loads(s))["role"] == "task"
        }
        if audit.exists()
        else set()
    )
    removed = set()
    for row in loop.state.db.execute("SELECT id,spec FROM trials").fetchall():
        spec = json.loads(row["spec"])
        if (
            spec["iteration"] == loop.iteration
            and (row["id"] in paused or spec["stage"] in stages)
            and row["id"] not in dispatched
            and not (loop.evaluator.jobs / row["id"]).exists()
        ):
            append_jsonl(
                loop.directory / "pause_recovery.jsonl",
                {
                    "ts": utc_now(),
                    "event": "undispatched_plan_requeued",
                    "trial_id": row["id"],
                    "spec": spec,
                },
            )
            config = loop.logs / "configs" / f"{row['id']}.json"
            if config.exists():
                archive = (
                    loop.logs
                    / "paused-before-dispatch"
                    / row["id"]
                    / str(config.stat().st_mtime_ns)
                )
                archive.mkdir(parents=True, exist_ok=True)
                for suffix in (".json", ".stdout", ".stderr"):
                    source = config.with_suffix(suffix)
                    if source.exists():
                        shutil.copy2(source, archive / source.name)
            loop.state.db.execute(
                "DELETE FROM trials WHERE id=?", (row["id"],)
            )
            removed.add(row["id"])
    for row in loop.state.db.execute("SELECT id,value FROM stages").fetchall():
        value = json.loads(row["value"])
        if (
            row["id"].startswith("batch-")
            and isinstance(value, list)
            and any(item.get("id") in removed for item in value)
        ):
            loop.state.db.execute(
                "DELETE FROM stages WHERE id=?", (row["id"],)
            )
    loop.state.db.commit()
    for path in files:
        path.unlink(missing_ok=True)


def reconcile_labels(loop):
    """Historical labels cannot alter evolver feedback."""
    raise ValueError(
        "Post-hoc relabelling is disabled; use a separate historical analysis"
    )
