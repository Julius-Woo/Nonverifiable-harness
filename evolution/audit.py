"""Read-only post-run evidence audit and accounting reconciliation."""

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from evolution.candidates import Manifest, atomic_json, source_hash
from evolution.judges import JudgeInput, build_prompt
from evolution.prompts import render
from harness.ledger import append_jsonl, utc_now


def response_upper_usd(payload, response, prices):
    """Reprice receipts consistently across the audit and stage reports."""
    if response and response.get("status_code") == 429:
        return 0.0
    usage = response.get("usage", {}) if response else {}
    if not all(
        type(usage.get(key)) is int
        for key in ("prompt_tokens", "completion_tokens")
    ):
        return None
    model = prices.get("aliases", {}).get(payload["model"], payload["model"])
    rates = prices["models"][model]
    return (
        usage["prompt_tokens"] * max(rates["input"], rates["cache_write"])
        + usage["completion_tokens"] * rates["output"]
    ) / 1e6


def audit_experiment(root, experiment):
    root = Path(root)
    accounting = root / "costs" / experiment
    events = [
        json.loads(s)
        for s in (accounting / "requests.jsonl").read_text().splitlines()
    ]
    ledger = [
        json.loads(s)
        for s in (accounting / "ledger.jsonl").read_text().splitlines()
    ]
    intents = {r["id"]: r for r in events if r["event"] == "request_intent"}
    responses = {
        r["id"]: r for r in events if r["event"] == "request_response"
    }
    unresolved_events = {
        r["id"]: r for r in events if r["event"] == "request_unresolved"
    }
    prices = json.loads((root / "costs/judges_prices.json").read_text())
    canaries = [
        p.read_text()
        for p in (root / "oracle" / experiment / "canaries").glob("*.txt")
    ]
    peer = root / "runs" / experiment / "isolation-peer/canary.txt"
    if peer.exists():
        canaries.append(peer.read_text())
    errors, cache_users, by_arm = [], [], {}
    judge_prompts = {}
    for queue_path in (root / "logs/evolution" / experiment).glob(
        "*/judges/queue.sqlite"
    ):
        with sqlite3.connect(queue_path) as queue:
            for identity, judge, evidence in queue.execute(
                "SELECT id,judge,evidence FROM items"
            ):
                judge_prompts[identity] = build_prompt(
                    judge, JudgeInput.from_dict(json.loads(evidence))
                )
    judged_payloads = 0

    for request_id, intent in intents.items():
        payload = json.loads(
            (Path(intent["archive"]) / "request.json").read_text()
        )
        encoded = json.dumps(payload)
        if any(value in encoded for value in canaries):
            errors.append(
                {"request": request_id, "error": "canary_in_payload"}
            )
        messages = payload["messages"]
        namespace = payload.get("user", "")
        cache_users.append(namespace)
        if not namespace:
            errors.append(
                {"request": request_id, "error": "missing_cache_user"}
            )
        if intent["role"] == "evolver":
            text = messages[0]["content"]
            conversation = json.loads(text.split("Conversation:\n", 1)[1])
            if conversation[0]["content"] != render(intent["arm"]):
                errors.append(
                    {"request": request_id, "error": "evolver_prompt_changed"}
                )
        if intent["role"] == "judge":
            expected = judge_prompts.get(intent["task"])
            if (
                expected is None
                or messages != [{"role": "user", "content": expected}]
                or "tools" in payload
            ):
                errors.append(
                    {
                        "request": request_id,
                        "error": "judge_payload_differs_from_queue",
                    }
                )
            else:
                judged_payloads += 1
        row = by_arm.setdefault(
            intent["arm"],
            {
                "calls": 0,
                "known_usd": 0,
                "conservative_token_upper_usd": 0,
                "unresolved_reservations_usd": 0,
                "unresolved_requests": 0,
                "api_wall_s": 0,
                "by_role": {},
            },
        )
        row["calls"] += 1
        response = responses.get(request_id)
        role = row["by_role"].setdefault(
            intent["role"],
            {
                "calls": 0,
                "conservative_token_upper_usd": 0,
                "api_wall_s": 0,
            },
        )
        role["calls"] += 1
        upper = response_upper_usd(payload, response, prices)
        if upper is not None:
            row["conservative_token_upper_usd"] += upper
            role["conservative_token_upper_usd"] += upper
        else:
            row["unresolved_reservations_usd"] += intent["reserved_usd"]
            row["unresolved_requests"] += 1
        terminal = response or unresolved_events.get(request_id, {})
        elapsed = terminal.get("wall_s", 0)
        row["api_wall_s"] += elapsed
        role["api_wall_s"] += elapsed
    if len(cache_users) != len(set(cache_users)):
        errors.append({"error": "duplicate_cache_user"})
    task_calls = Counter(
        (r["arm"], r["task"]) for r in intents.values() if r["role"] == "task"
    )
    for (arm, identity), count in task_calls.items():
        if count > 24:
            errors.append(
                {
                    "arm": arm,
                    "trial": identity,
                    "error": "task_call_cap_exceeded",
                    "calls": count,
                }
            )
    checked_task_traces = 0
    private_task_traces = 0
    legacy_task_traces = []
    trace_keys = set(task_calls)
    trace_keys.update(
        (path.parents[2].name, path.parent.name)
        for path in (root / "logs/evolution" / experiment).glob(
            "*/task-evidence/*/trace.jsonl"
        )
    )
    for arm, identity in sorted(trace_keys):
        trace = (
            root
            / "logs/evolution"
            / experiment
            / arm
            / "task-evidence"
            / identity
            / "trace.jsonl"
        )
        if trace.exists():
            private_task_traces += 1
        else:
            trace = (
                root
                / "logs/harbor"
                / experiment
                / arm
                / identity
                / "agent/trace.jsonl"
            )
            if not trace.exists():
                errors.append(
                    {
                        "arm": arm,
                        "trial": identity,
                        "error": "missing_task_trace",
                    }
                )
                continue
            legacy_task_traces.append(
                {
                    "arm": arm,
                    "trial": identity,
                    "path": str(trace),
                    "provenance": "Legacy Harbor-writable agent log; not "
                    "a private authoritative trace",
                }
            )
        instructions = sum(
            json.loads(line).get("kind") == "instruction"
            for line in trace.read_text().splitlines()
        )
        if instructions != 1:
            errors.append(
                {
                    "trace": str(trace),
                    "error": "duplicate_or_missing_task_start",
                }
            )
        checked_task_traces += 1
    for arm, row in by_arm.items():
        calls = [r for r in ledger if r["arm"] == arm]
        row["known_usd"] = sum(
            r.get("cost_usd") or r.get("known_response_cost_usd") or 0
            for r in calls
        )
        row["unknown_ledger_costs"] = sum(
            r.get("cost_usd") is None for r in calls
        )
        row["tokens"] = {
            k: sum(r.get(k) or 0 for r in calls)
            for k in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            )
        }
    feedback_count = 0
    for arm in by_arm:
        for path in (root / "feedback" / arm / experiment).glob("*.json"):
            text = path.read_text()
            data = json.loads(text)
            if set(data) != {
                "rollout_id",
                "candidate_id",
                "score",
                "task_text",
                "trajectory",
            }:
                errors.append(
                    {
                        "feedback": str(path),
                        "error": "unexpected_feedback_fields",
                    }
                )
            if any(value in text for value in canaries):
                errors.append(
                    {"feedback": str(path), "error": "canary_in_feedback"}
                )
            feedback_count += 1
    trace_exports = []
    for arm in by_arm:
        exports = root / "logs/evolution" / experiment / arm / "exports"
        for path in sorted(exports.glob("*/evidence.json"))[:20]:
            evidence = JudgeInput.from_dict(json.loads(path.read_text()))
            trace = (
                root
                / "logs/harbor"
                / experiment
                / arm
                / path.parent.name
                / "agent/trace.jsonl"
            )
            redactions = json.loads(
                (path.parent / "redactions.json").read_text()
            )
            with trace.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            ok = actual_hash == redactions["source_sha256"]
            trace_exports.append(
                {
                    "arm": arm,
                    "trial": path.parent.name,
                    "source_hash_matches": ok,
                    "events": len(evidence.trajectory.events),
                }
            )
            if not ok:
                errors.append(
                    {"trace": str(trace), "error": "source_hash_changed"}
                )
    snapshots = []
    for path in (root / "runs" / experiment).glob("*/candidates/*"):
        manifest = Manifest.read(path)
        ok = source_hash(path) == manifest.source_sha256
        snapshots.append({"candidate": str(path), "hash_matches": ok})
        if not ok:
            errors.append(
                {"candidate": str(path), "error": "immutable_snapshot_changed"}
            )
    boundaries = []
    for path in (root / "logs/evolution" / experiment).rglob("*boundary.json"):
        data = json.loads(path.read_text())
        if "mounts" not in data:
            continue
        allowed = {"/candidate", "/feedback", "/tmp"}
        ok = data["network"] == "none" and all(
            m["Destination"] in allowed for m in data["mounts"]
        )
        boundaries.append({"path": str(path), "ok": ok})
        if not ok:
            errors.append(
                {"boundary": str(path), "error": "unexpected_mount_or_network"}
            )
    result = {
        "ts": utc_now(),
        "experiment": experiment,
        "errors": errors,
        "actual_requests_audited": len(intents),
        "task_start_traces_audited": checked_task_traces,
        "private_task_traces_audited": private_task_traces,
        "legacy_task_traces": legacy_task_traces,
        "unique_cache_cache_users": len(set(cache_users)),
        "feedback_files_audited": feedback_count,
        "judge_payloads_match_queue": judged_payloads,
        "trace_exports_audited": trace_exports,
        "snapshots": snapshots,
        "boundaries": boundaries,
        "costs_by_arm": by_arm,
        "role_calls": dict(Counter(r["role"] for r in intents.values())),
        "cache_gate": (
            "Unique outbound cache_users enforced; provider internal cache "
            "partition is not independently observable"
        ),
        "cost_note": (
            "Token upper estimates use max(input, cache-write) rates; null "
            "cache telemetry remains null in the original ledger. Per-arm "
            "totals use actual arm tags, independent of concurrent "
            "wall windows."
        ),
    }
    atomic_json(root / "logs/evolution" / experiment / "audit.json", result)
    atomic_json(accounting / "summary.json", by_arm)
    append_jsonl(accounting / "audit_history.jsonl", result)
    return result
