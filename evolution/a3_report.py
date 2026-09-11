"""Offline A3-native audit; sealed outcomes stay private."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from evolution.a3_operators import mean_preference
from evolution.a3_v2 import JudgeInput
from evolution.accounting import cost_summary
from evolution.candidates import atomic_json
from evolution.manifest import file_hash
from evolution.sanitize import canonical, digest
from evolution.state import State


def rows(path):
    return (
        [json.loads(s) for s in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def report(root, experiment):
    root = Path(root)
    arm = "A3-native"
    run = root / "runs" / experiment / arm
    state = State(
        run / "state.sqlite",
        private=root / "oracle" / experiment / arm / "state",
    )
    try:
        summary = state.stage("finished-1")
        if not summary or summary["status"] != "complete":
            raise ValueError("Native round is not complete")
        trials = [
            {
                **State.decode(row["result"]),
                "_physical_infrastructure_attempt": bool(
                    json.loads(row["spec"]).get("infrastructure_attempt")
                ),
            }
            for row in state.db.execute(
                "SELECT spec,result FROM trials WHERE result IS NOT NULL"
            )
        ]
        refs = state.stage("a3-references-1")
        core = state.stage("a3-coreset")
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
        ("measurement", "search", 18),
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
    exports = list((logs / "exports").glob("*/evidence.json"))
    for path in exports:
        JudgeInput.from_dict(json.loads(path.read_text()))
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
            "excluded": sum(r.get("oracle") is None for r in logical),
            "phase_budget_censored": sum(
                "Projected phase API budget exceeded" in canonical(r)
                for r in logical
            ),
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
    with sqlite3.connect(accounting / "budget.sqlite") as budget:
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
    assert sum(scope_costs.values()) <= 20 + 1e-8
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
            "full_v2_exports": len(exports),
            "read_only_inspection_boundaries": len(boundaries),
            "isolated_proposal_boundaries": len(proposal_boundaries),
        },
        "decision": summary["decision"],
        "incumbent": summary["incumbent"],
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
        "final_controller_sha256": digest(
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
    atomic_json(run / "infrastructure-audit.json", audit)
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
                    "J_t",
                    "O_t_search",
                    "costs",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
