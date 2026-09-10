"""Integrated seven-row Section 5 evidence, built only from a real run."""

import json
from pathlib import Path

from evolution.candidates import atomic_json
from evolution.judge_queue import export_trace
from evolution.judges import JudgeInput, build_prompt
from evolution.manifest import file_hash, valid_cache_attestation
from evolution.prompts import render
from evolution.reconcile import read_rows
from evolution.sanitize import canonical, digest

NAMES = [
    "Evolver reads grader results",
    "Trace contains hidden artifacts",
    "Judge indirectly sees oracle",
    "Evolver prompt hints at oracle",
    "Anchor/sealed discovery",
    "Cross-arm contamination",
    "Shared judge/evolver cache",
]


def verify_matrix(root, matrix):
    errors = []
    if matrix.get("kind") != "real_infrastructure_validation":
        errors.append("Section 5 requires real-run evidence")
    if matrix.get("validation_complete") is not True:
        errors.append("Section 5 validation run is incomplete")
    rows = matrix.get("rows", [])
    if [r.get("row") for r in rows] != list(range(1, 8)):
        errors.append("Section 5 requires exactly seven ordered rows")
    for row in rows:
        if row.get("status") != "passed" or not row.get("evidence"):
            errors.append(f"Section 5 row {row.get('row')} is not passed")
        for item in row.get("evidence", []):
            path = Path(root) / item["path"]
            if not path.is_file() or file_hash(path) != item["sha256"]:
                errors.append(
                    f"Section 5 evidence missing/changed: {item['path']}"
                )
    return errors


def build_matrix(root, experiment):
    root = Path(root).resolve()
    manifest_path = root / "runs" / experiment / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    accounting = root / "costs" / manifest.get("budget_experiment", experiment)
    logs = root / "logs/evolution" / experiment
    private = root / "oracle" / experiment
    output = root / "runs" / experiment / "section5_matrix.json"
    intents = [
        r
        for r in read_rows(accounting / "requests.jsonl")
        if r["event"] == "request_intent" and r["run_id"] == experiment
    ]
    if not intents or not {"A0", "A1"} <= {r["arm"] for r in intents}:
        raise ValueError("A real A0/A1 run is required")
    rows = []

    def evidence(paths):
        return [
            {
                "path": str(p.resolve().relative_to(root)),
                "sha256": file_hash(p),
            }
            for p in paths
            if p.is_file()
        ]

    def add(number, passed, paths, detail):
        rows.append(
            {
                "row": number,
                "name": NAMES[number - 1],
                "status": "passed" if passed else "blocked",
                "evidence": evidence(paths),
                "detail": detail,
            }
        )

    sessions = sorted(logs.glob("*/sessions/*/boundary.json"))
    probes = sorted(logs.glob("*/sessions/*/canaries*.jsonl"))
    probe_rows = [r for p in probes for r in read_rows(p)]
    boundaries = [json.loads(p.read_text()) for p in sessions]
    session_arms = {p.relative_to(logs).parts[0] for p in sessions}
    both_evolvers = {"A0", "A1"} <= session_arms
    canary_values = [
        p.read_text() for p in (private / "canaries").glob("*.txt")
    ]
    peer_paths = [
        root / "runs" / experiment / a / "canary.txt" for a in ("A0", "A1")
    ]
    canary_values += [p.read_text() for p in peer_paths if p.exists()]
    add(
        1,
        both_evolvers
        and bool(sessions and probe_rows)
        and all(r["denied"] and r["content_absent"] for r in probe_rows)
        and all(
            b["network"] == "none" and "environment" in b for b in boundaries
        ),
        sessions + probes,
        "Live evolver mounts, actual container environment, known-"
        "path and /proc alias probes before/after sessions.",
    )

    grading_paths = sorted(private.glob("*/grading/*/boundary.json"))
    grading = [json.loads(p.read_text()) for p in grading_paths]
    canary_values += [g.get("canary", {}).get("value", "") for g in grading]
    canary_values = [v for v in canary_values if v]
    traces = sorted(logs.glob("*/task-evidence/*/trace.jsonl"))
    inspection = []
    for path in traces:
        events = read_rows(path)
        inspection.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": file_hash(path),
                "events": len(events),
                "instruction_count": sum(
                    e.get("kind") == "instruction" for e in events
                ),
                "canary_absent": all(
                    v not in path.read_text() for v in canary_values
                ),
            }
        )
    inspection_path = logs / "trace_inspection.json"
    manual_path = logs / "manual-sanitized-review.json"
    manual = (
        json.loads(manual_path.read_text()) if manual_path.is_file() else {}
    )
    manual_rows = manual.get("traces", [])
    manual_ok = (
        manual.get("kind") == "manual_review_of_real_sanitized_traces"
        and manual.get("experiment") == experiment
        and len({r["path"] for r in manual_rows}) >= 20
        and all(
            r.get("reviewed") is True
            and (root / r["path"]).is_file()
            and file_hash(root / r["path"]) == r["sha256"]
            for r in manual_rows
        )
    )
    atomic_json(
        inspection_path,
        {"method": "complete authoritative trace scan", "traces": inspection},
    )
    add(
        2,
        bool(grading)
        and manual_ok
        and all(
            g.get("solver_paused")
            and g.get("grader_fresh_pid_namespace")
            and g.get("canary", {}).get("absent_in_solver")
            and g.get("canary", {}).get("solver_paused_during_upload")
            for g in grading
        )
        and all(t["canary_absent"] for t in inspection),
        grading_paths + [inspection_path, manual_path],
        "Actual solver containers paused before snapshot; grader "
        "canary exists only in fresh grader namespace. Complete "
        "trace scan archived. Process-dependent task compatibility"
        " remains a pilot qualification gate.",
    )

    wire = []
    injected = []
    for intent in intents:
        request_path = Path(intent["archive"]) / "request.json"
        payload = json.loads(request_path.read_text())
        messages = payload["messages"]
        item = {
            "id": intent["id"],
            "arm": intent["arm"],
            "role": intent["role"],
            "request": str(request_path.relative_to(root)),
            "no_injected_system": len(messages) == 1
            and messages[0]["role"] == "user",
            "exact_prompt_hash": digest(canonical(messages))
            == intent["prompt_sha256"],
            "cache_user": payload.get("user"),
            "canary_absent": all(
                v not in canonical(payload) for v in canary_values
            ),
        }
        if intent["role"] == "evolver":
            conversation = json.loads(
                messages[0]["content"].split("Conversation:\n", 1)[1]
            )
            item["uniform_instruction"] = conversation[0]["content"] == render(
                intent["arm"], manifest["task_model"]["completion_allowance"]
            )
        if intent["role"] == "judge":
            import sqlite3

            queue_path = logs / intent["arm"] / "judges/queue.sqlite"
            with sqlite3.connect(queue_path) as db:
                row = db.execute(
                    "SELECT rollout,judge,evidence FROM items WHERE id=?",
                    (intent["task"],),
                ).fetchone()
            if row:
                rollout, judge, saved = row
                actual = JudgeInput.from_dict(json.loads(saved))
                item["judge_exact_evidence"] = messages == [
                    {"role": "user", "content": build_prompt(judge, actual)}
                ]
                result_record = json.loads(
                    (private / intent["arm"] / f"{rollout}.json").read_text()
                )
                physical_id = Path(result_record["source"]).parent.name
                trace = (
                    logs
                    / intent["arm"]
                    / "task-evidence"
                    / physical_id
                    / "trace.jsonl"
                )
                execution = json.loads(
                    trace.with_name("execution.json").read_text()
                )
                # Inject into the real private controller result, then compare
                # its export to the evidence actually dispatched on this run.
                result_path = private / intent["arm"] / f"{rollout}.json"
                result_record = json.loads(result_path.read_text())
                raw = json.loads(Path(result_record["source"]).read_text())
                marker = "NVH_INJECTED_ORACLE_" + rollout
                raw["oracle_canary"] = marker
                raw.setdefault("verifier_result", {})
                if raw["verifier_result"] is None:
                    raw["verifier_result"] = {}
                raw["verifier_result"]["hidden_reference"] = marker
                injection_path = private / "injection" / f"{rollout}.json"
                atomic_json(injection_path, raw)
                projected = export_trace(
                    trace,
                    private / "injection" / rollout,
                    result=raw,
                    execution=execution,
                )
                injected.append(
                    {
                        "rollout": rollout,
                        "private_input": str(injection_path.relative_to(root)),
                        "private_input_sha256": file_hash(injection_path),
                        "same_as_dispatched": projected.to_dict()
                        == actual.to_dict(),
                        "canary_absent": marker
                        not in build_prompt(judge, projected),
                    }
                )
        response_path = Path(intent["archive"]) / "response.json"
        if response_path.exists():
            usage = json.loads(response_path.read_text()).get("usage") or {}
            item["reported_cache_hit_tokens"] = usage.get(
                "prompt_cache_hit_tokens",
                (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                ),
            )
        else:
            item["reported_cache_hit_tokens"] = None
        wire.append(item)
    wire_path = logs / "wire_audit.json"
    injection_path = logs / "oracle_injection_audit.json"
    atomic_json(
        wire_path,
        {
            "requests": wire,
            "all_exact": all(
                w["no_injected_system"] and w["exact_prompt_hash"]
                for w in wire
            ),
        },
    )
    atomic_json(injection_path, injected)
    add(
        3,
        bool(injected)
        and all(
            i["same_as_dispatched"] and i["canary_absent"] for i in injected
        )
        and all(
            w.get("judge_exact_evidence", True) and w["canary_absent"]
            for w in wire
        ),
        [wire_path, injection_path],
        "Real raw verifier results with injected hidden content "
        "re-export identically to actual paid A1 wire evidence; no"
        " synthetic rollout substitutes.",
    )
    add(
        4,
        both_evolvers
        and all(
            w["no_injected_system"]
            and w["exact_prompt_hash"]
            and w.get("uniform_instruction", True)
            for w in wire
        ),
        [wire_path] + sorted(logs.glob("*/sessions/*/prompt.txt")),
        "Exact wire messages hashed; only the score-source "
        "paragraph changes across dispatched evolver instructions.",
    )

    feedback = sorted((root / "feedback").glob(f"*/{experiment}/*.json"))
    scan = []
    for path in feedback:
        value = json.loads(path.read_text())
        scan.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": file_hash(path),
                "no_private_fields": not (
                    {
                        "oracle",
                        "raw_reward",
                        "baseline_anchor",
                        "candidate_anchor",
                        "O_t_sealed",
                    }
                    & value.keys()
                ),
                "canary_absent": all(
                    v not in path.read_text() for v in canary_values
                ),
            }
        )
    scan_path = logs / "feedback_isolation.json"
    atomic_json(scan_path, scan)
    acceptance = sorted(private.glob("*/acceptance-i*.json"))
    acceptance_audit = []
    from evolution.loop import acceptance_decision

    for path in acceptance:
        saved = json.loads(path.read_text())
        arm = path.parent.name
        iteration = int(path.stem.split("-i")[1])
        summary_path = logs / arm / f"iteration-{iteration}.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text())
        injected_acceptance = {
            **saved,
            "private_task_canary": "NVH_PRIVATE_ACCEPTANCE_" + arm,
        }
        injected_path = private / "injection" / f"acceptance-{arm}.json"
        atomic_json(injected_path, injected_acceptance)
        bit = acceptance_decision(
            arm,
            summary["promotion"]["incumbent"]["J"],
            summary["promotion"]["candidate"]["J"],
            injected_acceptance["baseline_anchor"],
            injected_acceptance["candidate_anchor"],
            rule=saved["rule"],
            tau=summary["tau"],
            epsilon=summary["epsilon"],
        )
        acceptance_audit.append(
            {
                "input": str(path.relative_to(root)),
                "input_sha256": file_hash(path),
                "injected_input": str(injected_path.relative_to(root)),
                "injected_sha256": file_hash(injected_path),
                "only_bit": type(bit) is bool,
                "matches_recorded_decision": bit
                == saved["accepted"]
                == summary["accepted"],
                "canary_absent_from_exports": all(
                    injected_acceptance["private_task_canary"]
                    not in p.read_text()
                    for p in feedback + [summary_path]
                ),
            }
        )
    acceptance_audit_path = logs / "acceptance_export_audit.json"
    atomic_json(acceptance_audit_path, acceptance_audit)
    add(
        5,
        bool(scan and acceptance)
        and len(acceptance_audit) == len(acceptance)
        and all(
            a["only_bit"]
            and a["matches_recorded_decision"]
            and a["canary_absent_from_exports"]
            for a in acceptance_audit
        )
        and all(s["no_private_fields"] and s["canary_absent"] for s in scan),
        [scan_path, acceptance_audit_path] + acceptance + probes,
        "Actual acceptance-cycle inputs remain private; all "
        "exported feedback is scanned, and live held-out path "
        "probes are denied.",
    )
    real_peer = all(
        (
            root / "runs" / experiment / a / "candidates/seed/manifest.json"
        ).is_file()
        for a in ("A0", "A1")
    )
    add(
        6,
        real_peer
        and both_evolvers
        and bool(probe_rows)
        and all(r["denied"] for r in probe_rows)
        and any(r["kind"] == "cross_arm_write" for r in probe_rows),
        sessions + probes + [p for p in peer_paths if p.exists()],
        "Both actual arm roots exist; real peer canary reads and "
        "writes fail in each live evolver container.",
    )
    users = [w["cache_user"] for w in wire]
    metadata_ok = all(users) and len(users) == len(set(users))
    cache_rows = [w for w in wire if w["role"] in {"judge", "evolver"}]
    cache_path = logs / "cache_observations.json"
    atomic_json(
        cache_path,
        {
            "responses": len(cache_rows),
            "positive_cache_hits": sum(
                type(w["reported_cache_hit_tokens"]) is int
                and w["reported_cache_hit_tokens"] > 0
                for w in cache_rows
            ),
            "unknown_cache_counts": sum(
                w["reported_cache_hit_tokens"] is None for w in cache_rows
            ),
            "unique_user_metadata": metadata_ok,
            "partition_enforcement_established": False,
        },
    )
    attestation = valid_cache_attestation(root, manifest)
    add(
        7,
        bool(metadata_ok and attestation),
        [wire_path, cache_path],
        "Unique run/arm/role/task API user metadata demonstrated "
        "on every real request. Provider enforcement is not "
        "observable from this API; no attestation is available, so"
        " this row and pilot entry remain blocked.",
    )
    matrix = {
        "schema_version": 1,
        "kind": "real_infrastructure_validation",
        "experiment": experiment,
        "manifest_sha256": file_hash(manifest_path),
        "validation_complete": all(
            (logs / arm / "iteration-1.json").is_file()
            and json.loads((logs / arm / "iteration-1.json").read_text()).get(
                "status"
            )
            == "complete"
            for arm in ("A0", "A1")
        ),
        "rows": rows,
        "passed": all(r["status"] == "passed" for r in rows),
    }
    matrix["passed"] = matrix["passed"] and matrix["validation_complete"]
    atomic_json(output, matrix)
    return matrix
