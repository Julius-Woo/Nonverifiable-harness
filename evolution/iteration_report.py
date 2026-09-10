"""Reconcile finished iteration results, stages, sessions, and API costs."""

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from evolution.audit import audit_experiment, response_upper_usd
from evolution.candidates import atomic_json
from evolution.state import State


def read_rows(path):
    return (
        [json.loads(s) for s in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def report_experiment(root, experiment):
    root = Path(root)
    audit = audit_experiment(root, experiment)
    accounting = root / "costs" / experiment
    events = read_rows(accounting / "requests.jsonl")
    timings = read_rows(accounting / "timings.jsonl")
    terminal = {
        r["id"]: r
        for r in events
        if r["event"] in {"request_response", "request_unresolved"}
    }
    price_bytes = (root / "costs/judges_prices.json").read_bytes()
    prices = json.loads(price_bytes)
    upper_by_id, adjustments = {}, []
    scope_costs = defaultdict(float)
    scope_limits = {}
    for intent in events:
        if intent["event"] != "request_intent":
            continue
        response = terminal.get(intent["id"], {})
        payload = json.loads(
            (Path(intent["archive"]) / "request.json").read_text()
        )
        upper = response_upper_usd(payload, response, prices)
        upper_by_id[intent["id"]] = upper
        scope_costs[intent["scope"]] += (
            intent["reserved_usd"] if upper is None else upper
        )
        scope_limits[intent["scope"]] = 5 if intent["role"] == "evolver" else 1
        recorded = response.get("uncached_upper_usd")
        if upper is not None and recorded is not None:
            if abs(upper - recorded) > 1e-12:
                adjustments.append(
                    {
                        "request": intent["id"],
                        "arm": intent["arm"],
                        "recorded_upper_usd": recorded,
                        "audited_upper_usd": upper,
                        "delta_usd": upper - recorded,
                    }
                )
    for scope, cost in scope_costs.items():
        if cost > scope_limits[scope] + 1e-9:
            raise ValueError("Repriced scope cost exceeds its cap")
    with sqlite3.connect(accounting / "budget.sqlite") as budget:
        estimate, ceiling = budget.execute(
            "SELECT estimate,ceiling FROM phase WHERE id=1"
        ).fetchone()
    if sum(scope_costs.values()) > min(estimate * 1.5, ceiling) + 1e-9:
        raise ValueError("Repriced phase cost exceeds its cap")
    arms = {}
    for arm in ("A0", "A1"):
        directory = root / "runs" / experiment / arm
        state = State(directory / "state.sqlite")
        summary = state.stage("finished-1")
        if not summary:
            state.close()
            raise ValueError(
                f"{arm} is not complete; no final report produced"
            )
        rows = [
            json.loads(r[0])
            for r in state.db.execute(
                "SELECT result FROM trials WHERE result IS NOT NULL"
            )
        ]
        state.close()
        stages, owners = {}, {}
        for row in rows:
            stage = f"{row['stage']}:{row['partition']}"
            data = stages.setdefault(
                stage,
                {
                    "scheduled": 0,
                    "completed_harbor_results": 0,
                    "oracle_pass": 0,
                    "oracle_fail": 0,
                    "oracle_excluded": 0,
                    "missing_judge": 0,
                    "tool_failure_diagnostics": 0,
                    "whole_rollout_retries": 0,
                    "api_calls": 0,
                    "api_wall_s": 0,
                    "conservative_token_upper_usd": 0,
                    "unresolved_reservations_usd": 0,
                    "harbor_service_s": 0,
                },
            )
            data["scheduled"] += 1
            data["completed_harbor_results"] += bool(row.get("source"))
            label = row.get("oracle")
            data["oracle_pass"] += label == 1
            data["oracle_fail"] += label == 0
            data["oracle_excluded"] += label is None
            data["tool_failure_diagnostics"] += bool(
                row.get("execution", {}).get("tool_failed")
            )
            if row["partition"] == "search" and row["stage"] != "smoke":
                data["missing_judge"] += row.get("score") is None
            owners[row["id"]] = stage
            for identity in row.get("judge_ids", []):
                owners[identity] = stage
        queue_path = (
            root / "logs/evolution" / experiment / arm / "judges/queue.sqlite"
        )
        if queue_path.exists():
            with sqlite3.connect(queue_path) as queue:
                for identity, rollout in queue.execute(
                    "SELECT id,rollout FROM items"
                ):
                    if rollout in owners:
                        owners[identity] = owners[rollout]
        proposals = {
            "api_calls": 0,
            "api_wall_s": 0,
            "conservative_token_upper_usd": 0,
            "unresolved_reservations_usd": 0,
        }
        for event in events:
            if event["event"] != "request_intent" or event["arm"] != arm:
                continue
            stage = owners.get(event["task"])
            data = (
                proposals if event["role"] == "evolver" else stages.get(stage)
            )
            if data is None:
                raise ValueError(f"Unattributed API call {event['id']}")
            data["api_calls"] += 1
            response = terminal.get(event["id"], {})
            data["api_wall_s"] += response.get("wall_s", 0)
            upper = upper_by_id[event["id"]]
            if upper is None:
                data["unresolved_reservations_usd"] += event["reserved_usd"]
            else:
                data["conservative_token_upper_usd"] += upper
        for timing in timings:
            if timing.get("arm") != arm or timing["kind"] != "harbor_trial":
                continue
            stage = owners.get(timing["id"])
            if stage:
                stages[stage]["harbor_service_s"] += timing["wall_s"]
        sessions = []
        logs = root / "logs/evolution" / experiment / arm
        recoveries = read_rows(logs / "session_recovery.jsonl")
        for proposal in summary["candidates"]:
            calls = [
                r
                for r in events
                if r["event"] == "request_intent"
                and r["arm"] == arm
                and r["role"] == "evolver"
                and r["task"] == proposal["id"]
            ]
            costs = [terminal.get(r["id"], {}) for r in calls]
            session_costs = {
                "actual_api_calls": len(calls),
                "api_wall_s": sum(r.get("wall_s", 0) for r in costs),
                "conservative_token_upper_usd": sum(
                    upper_by_id[r["id"]] or 0 for r in calls
                ),
                "unresolved_reservations_usd": sum(
                    call["reserved_usd"]
                    for call in calls
                    if upper_by_id[call["id"]] is None
                ),
            }
            endings = [
                r["ts"]
                for r in recoveries
                if r.get("candidate") == proposal["id"]
                and r["event"] == "explicit_recovery_result"
            ]
            elapsed = proposal["wall_s"]
            if endings:
                elapsed = (
                    datetime.fromisoformat(endings[-1])
                    - datetime.fromisoformat(proposal["started_at"])
                ).total_seconds()
            sessions.append(
                {
                    "candidate": proposal["id"],
                    "session_elapsed_including_pauses_s": elapsed,
                    "status": proposal["status"],
                    "accounting": session_costs,
                    "initial_session_wall_s": proposal["wall_s"],
                    "recovery_events": [
                        r
                        for r in recoveries
                        if r.get("candidate") == proposal["id"]
                    ],
                    "smoke_ok": proposal.get("smoke_ok"),
                    "search": proposal.get("search"),
                    "manifest": proposal.get("manifest"),
                }
            )
        if (
            sum(s["api_calls"] for s in stages.values())
            + proposals["api_calls"]
            != audit["costs_by_arm"][arm]["calls"]
        ):
            raise ValueError(
                "Stage API totals do not reconcile with the audit"
            )
        for field in (
            "conservative_token_upper_usd",
            "unresolved_reservations_usd",
        ):
            total = sum(s[field] for s in stages.values()) + proposals[field]
            if not math.isclose(
                total, audit["costs_by_arm"][arm][field], abs_tol=1e-9
            ):
                raise ValueError("Stage costs do not reconcile with audit")
        acceptance = root / "oracle" / experiment / arm / "acceptance-i1.json"
        arms[arm] = {
            "summary": summary,
            "acceptance": json.loads(acceptance.read_text())
            if acceptance.exists()
            else None,
            "stages": stages,
            "proposal_accounting": proposals,
            "sessions": sessions,
            "costs": audit["costs_by_arm"][arm],
        }
    report = {
        "experiment": experiment,
        "arms": arms,
        "audit_errors": audit["errors"],
        "cost_reconciliation": {
            "prices_sha256": hashlib.sha256(price_bytes).hexdigest(),
            "legacy_receipt_adjustments": adjustments,
            "total_adjustment_usd": sum(r["delta_usd"] for r in adjustments),
            "audited_upper_with_reserves_usd": sum(scope_costs.values()),
            "phase_and_scope_caps": "passed",
            "note": "Original append-only summaries and receipts retained; "
            "final stage and arm costs use one conservative pricing rule.",
        },
        "notes": [
            "Harbor service seconds include admission wait; overlapping "
            "stages do not sum to elapsed iteration time.",
            "Tool failures are diagnostics; pass follows reward=1 "
            "with no agent timeout.",
            "Original source/feedback and infrastructure incidents remain "
            "archived; these are infrastructure iterations, not "
            "confirmatory pilot evidence.",
        ],
    }
    atomic_json(root / "logs/evolution" / experiment / "report.json", report)
    atomic_json(
        accounting / "reconciliation.json", report["cost_reconciliation"]
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    report_experiment(Path(__file__).resolve().parents[1], args.experiment)


if __name__ == "__main__":
    main()
