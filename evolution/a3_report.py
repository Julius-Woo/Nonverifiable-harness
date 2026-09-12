"""Offline A3-native audit; sealed outcomes stay private."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from evolution.a3_metrics import (
    completion,
    metrics,
    phase_censored,
    preference_report,
)
from evolution.a3_operators import mean_preference
from evolution.a3_v2 import JudgeInput as HistoricalJudgeInput
from evolution.accounting import cost_summary
from evolution.candidates import atomic_json
from evolution.judges import JudgeInput
from evolution.manifest import file_hash
from evolution.sanitize import canonical, digest
from evolution.state import State


def rows(path):
    return (
        [json.loads(s) for s in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def completion_report(summary, selection, search, sealed, tasks, references):
    """Reconstruct missingness without modifying any frozen run artifacts."""
    proposals = {}
    for proposal in summary["candidates"]:
        scored = selection.get(proposal["id"])
        if scored is None:
            scored = [
                {
                    "id": f"{proposal['id']}:{task}",
                    "task": task,
                    "reference_id": references[task],
                    "score": None,
                    "pair_status": "unscored-failure",
                }
                for task in tasks
            ]
        preferences = preference_report(scored)
        proposals[proposal["id"]] = {
            "source_status": proposal["status"],
            "proposal_failure_reason": proposal.get("reason"),
            "eligible": proposal.get("preference") is not None,
            "selection_preference": proposal.get("preference"),
            **preferences,
        }
    fields = completion(search, sealed)
    return {
        "schema_version": "a3-completion-v3",
        "experiment": summary["experiment"],
        "arm": summary["arm"],
        "evidence_version": summary.get(
            "evidence_version", "sanitized-trajectory-v2"
        ),
        "regeneration": (
            "offline; frozen evidence, decisions and labels unchanged"
        ),
        "status": "complete"
        if fields["measurement_complete"]
        else "measurement-incomplete",
        "decision": summary["decision"],
        "decision_basis": "eligibility-based rejection"
        if not any(p["eligible"] for p in proposals.values())
        else "signed preference with incumbent-retaining zero tie",
        "incumbent": summary["incumbent"],
        "selection": proposals,
        **fields,
        "J_t": metrics(search)["J"],
        "O_t_search": metrics(search)["O"],
        "search_estimates": metrics(search),
        "J_t_scored_pairs": fields["search_preferences"]["scored"],
        "O_t_sealed": None,
        "sealed_outcomes": "private; consult measurement_complete before use",
    }


def pair_table(preferences):
    lines = [
        "| Task | Replicate | Raw A→B | Signed preference | Status | W/T/L |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for pair in preferences["pairs"]:
        rating = pair["raw_rating"]
        score = pair["signed_preference"]
        lines.append(
            f"| {pair['task']} | {pair['replicate']} | "
            f"{rating if rating is not None else '—'} | "
            f"{score if score is not None else '—'} | {pair['status']} | "
            f"{pair['outcome'] or '—'} |"
        )
    return "\n".join(lines)


def completion_markdown(value):
    lines = [
        "# A3-native offline completion report",
        "",
        f"Decision: {value['decision']}; {value['decision_basis']}.",
        "",
        f"Optimization complete: {value['optimization_complete']}. "
        f"Measurement complete: {value['measurement_complete']}. "
        f"Endpoint eligible: {value['endpoint_eligible']}.",
        "",
        f"Evidence version: {value['evidence_version']}; "
        "offline report corrections.",
        "",
    ]
    for name, preferences in [
        *value["selection"].items(),
        ("Fixed seed reference measurement", value["search_preferences"]),
    ]:
        lines.extend(
            [
                f"## {name}",
                "",
                f"Wins/ties/losses: {preferences['wins']}/"
                f"{preferences['ties']}/{preferences['losses']}; "
                f"scored {preferences['scored']}/{preferences['planned']}. "
                f"Win rate: {preferences['win_rate']} (wins / scored pairs).",
                "",
                pair_table(preferences),
                "",
            ]
        )
    for name in ("search_measurements", "sealed_measurements"):
        lines.extend(
            [
                f"## {name}",
                "",
                "| Task | Replicate | Status |",
                "| --- | ---: | --- |",
            ]
        )
        lines.extend(
            f"| {row['task']} | {row['replicate']} | {row['status']} |"
            for row in value[name]["rows"]
        )
        lines.extend(
            ["", f"Status counts: {value[name]['status_counts']}", ""]
        )
    if "costs_by_stage" in value:
        lines.extend(
            [
                "## Cost and completion",
                "",
                f"Censoring: **{value['censoring']}**. "
                f"Pass label: {value['pass_label']}.",
                "",
                "| Stage | HTTP requests | Conservative USD |",
                "| --- | ---: | ---: |",
            ]
        )
        lines.extend(
            f"| {stage} | {cost['requests']} | {cost['usd']:.8f} |"
            for stage, cost in value["costs_by_stage"].items()
        )
        total_requests = sum(
            c["requests"] for c in value["costs_by_stage"].values()
        )
        lines.extend(
            [
                f"| Total | {total_requests} | {value['total_usd']:.8f} |",
                "",
                "Uncached API-equivalent receipts plus unresolved "
                "reservations; "
                "not an Azure invoice.",
                "",
                "Standing metrics: `"
                + json.dumps(value["standing_metrics"])
                + "`",
                "",
            ]
        )
    return "\n".join(lines)


def report(root, experiment):
    root = Path(root).resolve()
    arm = "A3-native"
    run = root / "runs" / experiment / arm
    manifest = json.loads((run.parent / "manifest.json").read_text())
    clean = manifest.get("clean_calibration", {})
    search_attempts = clean.get("search_measurement_attempts", 1)
    private = root / "oracle" / experiment / arm
    state = sqlite3.connect(
        f"{(run / 'state.sqlite').as_uri()}?mode=ro", uri=True
    )
    state.row_factory = sqlite3.Row

    def stage(key):
        row = state.execute(
            "SELECT value FROM stages WHERE id=?", (key,)
        ).fetchone()
        return State.decode(row[0]) if row else None

    try:
        summary = stage("finished-1")
        if not summary or summary["status"] not in {
            "complete",
            "measurement-incomplete",
        }:
            raise ValueError("Native round is not complete")
        trials = [
            {
                **State.decode(row["result"]),
                "_physical_infrastructure_attempt": bool(
                    json.loads(row["spec"]).get("infrastructure_attempt")
                ),
            }
            for row in state.execute(
                "SELECT spec,result FROM trials WHERE result IS NOT NULL"
            )
        ]
        refs = stage("a3-references-1")
        core = stage("a3-coreset")
    finally:
        state.close()
    expected_groups = {
        (task, replicate) for task in core["tasks"] for replicate in range(3)
    }
    groups = [
        r
        for r in trials
        if r["stage"] == "a3-group"
        and not r.get("_physical_infrastructure_attempt")
    ]
    assert {(r["task"], r["replicate"]) for r in groups} == expected_groups
    assert len(groups) == 30
    assert refs == {r["task"]: r["id"] for r in groups if r["replicate"] == 0}
    assert len(summary["candidates"]) == 3
    for proposal in summary["candidates"]:
        if "pair_scores" in proposal:
            assert len(proposal["pair_scores"]) == 10
            assert proposal["preference"] == mean_preference(
                proposal["pair_scores"]
            )
            assert proposal["reference_ids"] == refs
    assert not any(r["partition"] == "anchor" for r in trials)
    assert all(
        r["stage"] == "measurement"
        for r in trials
        if r["partition"] == "sealed"
    )
    logical = [
        r for r in trials if not r.get("_physical_infrastructure_attempt")
    ]
    for stage, partition, expected in (
        ("a3-prior", "search", 18),
        ("measurement", "search", 18 * search_attempts),
        ("measurement", "sealed", 12),
    ):
        assert (
            sum(
                r["stage"] == stage and r["partition"] == partition
                for r in logical
            )
            == expected
        )
    logs = root / "logs/evolution" / experiment / arm
    pair_logs = private if clean else logs
    search = json.loads((pair_logs / "a3/i01/measurement.json").read_text())[
        "rows"
    ]
    selection = {
        proposal["id"]: json.loads(
            (
                pair_logs / "a3/i01" / f"selection-{proposal['id']}.json"
            ).read_text()
        )["rows"]
        for proposal in summary["candidates"]
        if "pair_scores" in proposal
    }
    completed = completion_report(
        summary,
        selection,
        search,
        [r for r in logical if r["partition"] == "sealed"],
        core["tasks"],
        refs,
    )
    export_logs = private / "evaluation" if clean else logs
    exports = list((export_logs / "exports").glob("*/evidence.json"))
    export_versions = Counter()
    for path in exports:
        value = json.loads(path.read_text())
        export_versions[value["trajectory"]["version"]] += 1
        contract = (
            HistoricalJudgeInput
            if value["trajectory"]["version"] == ("sanitized-trajectory-v2")
            else JudgeInput
        )
        contract.from_dict(value)
    boundaries = list((logs / "a3").glob("**/boundary-*.json"))
    proposal_boundaries = list((logs / "sessions").glob("*/boundary.json"))
    for path in boundaries + proposal_boundaries:
        boundary = json.loads(path.read_text())
        assert boundary["network"] == "none" and boundary["read_only_root"]
        assert "ALL" in boundary["cap_drop"]
        binds = [m for m in boundary["mounts"] if m["Type"] == "bind"]
        if path in boundaries:
            assert len(binds) == 1
            assert binds[0]["Destination"] == "/candidate"
            assert not binds[0]["RW"]
            assert Path(binds[0]["Source"]).name == "inputs"
        else:
            assert {m["Destination"]: m["RW"] for m in binds} == {
                "/candidate": True,
                "/feedback": False,
            }
    assert len(proposal_boundaries) == 3
    accounting = root / "costs" / experiment
    events = rows(accounting / "requests.jsonl")
    intents = [r for r in events if r["event"] == "request_intent"]
    responses = {
        r["id"]: r for r in events if r["event"] == "request_response"
    }
    allowed_roles = {"a3-resolve", "a3-diagnose", "a3-propose", "a3-rank"}
    for intent in intents:
        assert intent["arm"] == arm and intent["role"] in allowed_roles
        payload_path = Path(intent["archive"]) / "request.json"
        payload = json.loads(payload_path.read_text())
        if intent.get("operation") == "embeddings":
            assert payload["model"] == "text-embedding-3-large"
            assert payload["dimensions"] == 1024
            assert intent["role"] == "a3-diagnose"
            assert intent["prompt_sha256"] == digest(
                canonical(payload["input"])
            )
            assert payload["user"] == intent["cache_user"]
            continue
        assert payload["model"] == "gpt56terra"
        assert intent["prompt_sha256"] == digest(
            canonical(payload["messages"])
        )
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert payload["user"] == intent["cache_user"]
    role_costs = {}
    ledger = rows(accounting / "ledger.jsonl")
    for role in sorted(allowed_roles):
        calls = [r for r in intents if r["role"] == role]
        records = [r for r in ledger if r["role"] == role]
        calls_by_scope = Counter(r["scope"] for r in calls)
        assert max(calls_by_scope.values(), default=0) <= 24
        role_costs[role] = {
            "calls": len(calls),
            "maximum_scope_calls": max(calls_by_scope.values(), default=0),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in records),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in records),
            "api_wall_s": sum(
                responses.get(r["id"], {}).get("wall_s", 0) for r in calls
            ),
            "uncached_upper_usd": sum(
                responses.get(r["id"], {}).get("uncached_upper_usd") or 0
                for r in calls
            ),
            "unresolved_reservations_usd": sum(
                r["reserved_usd"]
                for r in calls
                if responses.get(r["id"], {}).get("uncached_upper_usd") is None
            ),
        }
    stages = defaultdict(list)
    for trial in trials:
        stages[f"{trial['stage']}:{trial['partition']}"].append(trial)
    allocation = {}
    for stage, items in stages.items():
        logical = [
            r for r in items if not r.get("_physical_infrastructure_attempt")
        ]
        allocation[stage] = {
            "physical_trials": len(items),
            "logical_rollouts": len(logical),
            "infrastructure_replacements": sum(
                bool(r.get("_physical_infrastructure_attempt")) for r in items
            ),
            "excluded": sum(
                bool(r.get("excluded")) or r.get("oracle") is None
                for r in logical
            ),
            "oracle_labels_missing": sum(
                r.get("oracle") is None for r in logical
            ),
            "phase_budget_censored": sum(phase_censored(r) for r in logical),
        }
        if items[0]["partition"] == "search":
            allocation[stage]["search_passes"] = sum(
                r.get("oracle") == 1 for r in logical
            )
    operators = [
        json.loads(p.read_text())
        for p in (logs / "a3/i01").glob("**/operators/*.json")
    ]
    operator_versions = {}
    for version in sorted({r["version"] for r in operators}):
        records = [r for r in operators if r["version"] == version]
        operator_versions[version] = {
            "operators": len(records),
            "failures": sum(r.get("result") is None for r in records),
            "retries": sum(max(0, len(r["attempts"]) - 1) for r in records),
        }
    with sqlite3.connect(
        f"{(accounting / 'budget.sqlite').as_uri()}?mode=ro", uri=True
    ) as budget:
        scope_costs = dict(
            budget.execute(
                "SELECT scope, sum(coalesce(charged, reserved)) "
                "FROM requests GROUP BY scope"
            )
        )
        phase = budget.execute(
            "SELECT started, deadline, ceiling FROM phase WHERE id=1"
        ).fetchone()
        halts = budget.execute(
            "SELECT reason,occurred FROM phase_halt"
        ).fetchall()
    assert sum(scope_costs.values()) <= phase[2] + 1e-8
    for role in allowed_roles:
        scopes = {r["scope"] for r in intents if r["role"] == role}
        maximum = max((scope_costs[s] for s in scopes), default=0)
        assert maximum <= (1 if role == "a3-resolve" else 5) + 1e-8
        role_costs[role]["maximum_scope_usd"] = maximum
    audit = {
        "arm": arm,
        "purpose": "infrastructure",
        "experiment": experiment,
        "recipe_verified": {
            "k": 10,
            "G": 3,
            "N": 3,
            "rounds": 1,
            "fixed_first_group_references": True,
            "candidate_A_baseline_B": True,
            "full_v2_exports": export_versions["sanitized-trajectory-v2"],
            "capped_v3_exports": export_versions["v3"],
            "read_only_inspection_boundaries": len(boundaries),
            "isolated_proposal_boundaries": len(proposal_boundaries),
        },
        "decision": summary["decision"],
        "incumbent": summary["incumbent"],
        "completion": completed,
        "optimization_complete": completed["optimization_complete"],
        "measurement_complete": completed["measurement_complete"],
        "endpoint_eligible": completed["endpoint_eligible"],
        "decision_basis": completed["decision_basis"],
        "candidate_preferences": {
            p["id"]: p.get("preference") for p in summary["candidates"]
        },
        "candidate_statuses": {
            p["id"]: p["status"] for p in summary["candidates"]
        },
        "selection_pairs": sum(
            len(p.get("pair_scores", [])) for p in summary["candidates"]
        ),
        "selection_pairs_missing": sum(
            score is None
            for p in summary["candidates"]
            for score in p.get("pair_scores", [])
        ),
        "J_t": summary["J_t"],
        "O_t_search": summary["O_t_search"],
        "allocations": allocation,
        "costs_by_role": role_costs,
        "costs": cost_summary(
            accounting / "ledger.jsonl", accounting / "requests.jsonl"
        ),
        "wall_s": summary["wall_s"],
        "operator_failures": sum(not r.get("result") for r in operators),
        "operator_retries": sum(
            max(0, len(r["attempts"]) - 1) for r in operators
        ),
        "operators_by_version": operator_versions,
        "undispatched_admission_repairs": sum(
            bool(r.get("admission_recovery")) for r in operators
        ),
        "budget_guard": {
            "started_epoch": phase[0],
            "deadline_epoch": phase[1],
            "ceiling_usd": phase[2],
            "charged_plus_reserved_usd": sum(scope_costs.values()),
            "maximum_scope_usd": max(scope_costs.values(), default=0),
            "halts": [
                {"reason": reason, "occurred_epoch": occurred}
                for reason, occurred in halts
            ],
        },
        "served_models": dict(
            Counter(r["model"] for r in ledger if r.get("ok"))
        ),
        "embedding_http_calls": sum(
            r.get("operation") == "embeddings" for r in intents
        ),
        "manifest_sha256": file_hash(
            root / "runs" / experiment / "manifest.json"
        ),
        "report_controller_sha256": digest(
            canonical(
                {
                    str(p.relative_to(root)): file_hash(p)
                    for p in sorted((root / "evolution").rglob("*.py"))
                }
            )
        ),
        "coreset_sha256": file_hash(run / "a3-coreset.json"),
        "ledger_sha256": hashlib.sha256(
            (accounting / "ledger.jsonl").read_bytes()
        ).hexdigest(),
    }
    if clean:
        from evolution.a3_calibration import l1_prime, stage_costs
        from evolution.a3_coreset import select_coreset
        from evolution.behavior import (
            claim_report,
            claimed_without_ran,
            standing_metrics,
        )
        from evolution.sanitize import sanitized_events

        assert not halts
        admissions = rows(run / "stage-admissions.jsonl")
        assert admissions and all(r["admitted"] for r in admissions)
        assert all(
            r["used_usd"] + r["projected_stage_usd"] <= r["cap_usd"] == 60
            for r in admissions
        )
        projection = clean["projection"]
        assert projection["admitted"] and projection["projected_usd"] <= 55
        assert projection["created_at"] < min(r["ts"] for r in intents)
        assert not any(phase_censored(r) for r in trials)
        trial_ids = {r["id"] for r in trials}
        host_admissions = [
            r
            for path in (
                root / "logs/evolution-admission/admissions.jsonl",
                root / "logs/evolution-admission" / experiment
                / "admissions.jsonl",
            )
            for r in rows(path)
            if r["trial_id"] in trial_ids
        ]
        assert {r["trial_id"] for r in host_admissions} == trial_ids
        assert all(
            r["available_gib"] >= 6 and r["global_limit"] <= 4
            for r in host_admissions
        )
        atomic_json(run / "host-admissions.json", host_admissions)
        assert set(export_versions) == {"v3"}
        assert core["embedding"]["model"] == "text-embedding-3-large"
        assert core["seed"] == manifest["seed"] == 1
        assert core["tasks"] == select_coreset(
            core["descriptions"], core["vectors"], seed=core["seed"]
        )
        assert all(
            p["eligible"] == p["complete"]
            for p in completed["selection"].values()
        )
        assert all(
            p["status_counts"]["censored"] == 0
            and p["status_counts"]["unscored-budget"] == 0
            for p in completed["selection"].values()
        )
        assert completed["search_preferences"]["complete"]
        assert completed["endpoint_eligible"]
        for row in trials:
            source = Path(row.get("source", ""))
            if not source.is_file():
                continue
            result = json.loads(source.read_text())
            trace = source.parent / "agent/trace.jsonl"
            records = rows(trace)
            expected = l1_prime(result, row.get("execution", {}), records)[0]
            if (
                not row.get("excluded")
                and row.get("reason") != "api_timeout_infrastructure"
            ):
                assert row["oracle"] == expected
        cost_stages = stage_costs(root, experiment)
        total = sum(s["usd"] for s in cost_stages.values())
        assert total <= 60
        costs_total = (
            audit["costs"]["uncached_upper_usd"]
            + audit["costs"]["reserved_unresolved_usd"]
        )
        assert abs(total - costs_total) < 1e-8
        # Shared runtime revisions sometimes classify an empty filter-metadata
        # key as a refusal. Use the actual final response for the diagnostic
        # category; raw artifacts and L1' failure labels remain unchanged.
        latest_calls = {}
        for call in sorted(ledger, key=lambda r: r.get("ts", "")):
            if call["role"] == "a3-resolve":
                latest_calls[call["task"]] = call
        behavior_corrections = []
        for trial in trials:
            call = latest_calls.get(trial.get("replacement_id", trial["id"]))
            if (
                not call
                or trial.get("execution", {}).get("reason") == "normal_finish"
            ):
                continue
            response_path = Path(call["raw_dir"]) / "response.json"
            if not response_path.exists():
                continue
            response = json.loads(response_path.read_text())
            choices = response.get("choices", [])
            if (
                choices
                and not response.get("error")
                and all(
                    c.get("finish_reason") == "length"
                    and not c.get("message", {}).get("refusal")
                    for c in choices
                )
            ):
                original = trial.get("execution", {}).get("reason")
                if original != "token_step_budget_exhaustion":
                    behavior_corrections.append(
                        {
                            "id": trial["id"],
                            "original": original,
                            "corrected": "token_step_budget_exhaustion",
                            "response_sha256": file_hash(response_path),
                            "oracle_label_changed": False,
                        }
                    )
                assert trial.get("oracle") in (0, None)
                trial["execution"] = {
                    **trial.get("execution", {}),
                    "reason": "token_step_budget_exhaustion",
                }
                trial["behavior"] = {
                    **trial.get("behavior", {}),
                    "exhaustion": True,
                }
        atomic_json(
            private / "standing-metric-corrections.json", behavior_corrections
        )
        logical_trials = [
            r for r in trials if not r.get("_physical_infrastructure_attempt")
        ]
        # Launch and later worker versions wrote different detector schemas.
        # Recompute only this offline diagnostic from full sanitized traces;
        # never substitute the capped ranking export or alter frozen labels.
        detectors = []
        for trial in logical_trials:
            if trial.get("excluded"):
                continue
            source = Path(trial["source"])
            trace = source.parent / "agent/trace.jsonl"
            events, _, _ = sanitized_events(
                rows(trace),
                result=json.loads(source.read_text()),
                execution=trial.get("execution", {}),
            )
            detector = claimed_without_ran(events, window=5)
            detectors.append(detector)
            atomic_json(
                private / "behavior-recomputed" / f"{trial['id']}.json",
                {
                    "id": trial["id"],
                    "source_sha256": file_hash(trace),
                    "claimed_without_ran": detector,
                    "oracle_label_changed": False,
                },
            )
        claims = claim_report(detectors)
        atomic_json(private / "claimed-without-ran-clean.json", claims)
        completed.update(
            censoring="none",
            pass_label="L1-prime",
            search_measurement_attempts=search_attempts,
            costs_by_stage=cost_stages,
            total_usd=total,
            standing_metrics=standing_metrics(logical_trials),
            claimed_without_ran=claims,
            embedding=core["embedding"],
            seed=core["seed"],
            diagnoses_completed=sum(
                p.name.startswith("diagnose-")
                and json.loads(p.read_text()).get("result") is not None
                for p in (logs / "a3/i01").glob("**/operators/*.json")
            ),
            valid_proposals=sum(
                p["status"] == "valid" for p in summary["candidates"]
            ),
            runtime_amendments=[
                str(p.relative_to(root))
                for p in sorted(run.parent.glob("runtime-amendment-*.json"))
            ],
            preprocessing_retry_deviation=json.loads(
                (run.parent / "preprocessing-retry-deviation.json").read_text()
            )
            if (run.parent / "preprocessing-retry-deviation.json").exists()
            else None,
        )
        assert completed["diagnoses_completed"] == 10
        audit.update(
            costs_by_stage=cost_stages,
            total_usd=total,
            censoring="none",
            pass_label="L1-prime",
            standing_metrics=completed["standing_metrics"],
            standing_metric_category_corrections=len(behavior_corrections),
            stage_admissions_verified=len(admissions),
            predispatch_projection_usd=projection["projected_usd"],
            host_admissions_verified=len(host_admissions),
            minimum_admitted_memory_gib=min(
                r["available_gib"] for r in host_admissions
            ),
        )
        atomic_json(
            private / "search-avg2.json",
            {
                "incumbent": summary["incumbent"],
                "attempts_per_task": search_attempts,
                "pass_label": "L1-prime",
                "metrics": metrics(search),
                "rows": search,
            },
        )
    atomic_json(run / "infrastructure-audit.json", audit)
    atomic_json(run / "completion-report.json", completed)
    (run / "completion-report.md").write_text(completion_markdown(completed))
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default="a3-native-260910")
    args = parser.parse_args()
    result = report(Path(__file__).resolve().parents[1], args.experiment)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "arm",
                    "decision",
                    "candidate_preferences",
                    "optimization_complete",
                    "measurement_complete",
                    "endpoint_eligible",
                    "decision_basis",
                    "J_t",
                    "O_t_search",
                    "costs",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
