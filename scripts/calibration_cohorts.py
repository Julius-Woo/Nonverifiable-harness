"""Explicit calibration run ownership and accounting, independent of phase."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.cost_report import render_report

MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "data/calibration_run_manifest.json"
)


def load_manifest(path=MANIFEST_PATH):
    manifest = json.loads(path.read_text())
    names = [run["job_name"] for run in manifest["runs"]]
    if len(names) != len(set(names)):
        raise ValueError("Run belongs to multiple cohorts")
    for run in manifest["runs"]:
        cohort = manifest["cohorts"][run["cohort"]]
        for key in ("phase", "budget_guard", "budget_usd"):
            if run[key] != cohort[key]:
                raise ValueError(f"Inconsistent {key}: {run['job_name']}")
    return manifest


def scoped_calls(ledger, manifest, cohort=None):
    runs = {r["job_name"]: r for r in manifest["runs"]}
    selected = []
    for row in ledger:
        run = runs.get(row.get("run_id"))
        if run is None:
            if row.get("phase") == "P1.2":
                raise ValueError(f"Unmanifested P1.2 run: {row.get('run_id')}")
            continue
        if (
            row.get("phase") != run["phase"]
            or row.get("arm") != run["configuration"]
        ):
            raise ValueError(f"Ledger ownership mismatch: {row['call_id']}")
        if cohort is None or run["cohort"] == cohort:
            selected.append(row)
    ids = [r["call_id"] for r in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate calibration ledger UUID")
    return selected


def known_cost(row):
    return row.get("cost_usd") or row.get("known_response_cost_usd") or 0.0


def cost_state(row):
    if row.get("cost_usd") is not None:
        return "priced"
    if row.get("reconstructed_after_interruption"):
        return "interrupted_dispatch_unknown"
    if row.get("attempts") == [] and row.get("note") in {
        "Projected rollout budget exceeded",
        "Projected experiment budget exceeded",
    }:
        return "local_budget_stop_no_dispatch"
    if any(a.get("status_code", 0) >= 400 for a in row.get("attempts", [])):
        return "http_error_charge_unknown"
    return "other_unknown"


def account(ledger, manifest, results, root):
    output = {}
    for cohort, settings in manifest["cohorts"].items():
        calls = scoped_calls(ledger, manifest, cohort)
        runs = {
            r["job_name"]: r for r in manifest["runs"] if r["cohort"] == cohort
        }
        rows = [r for r in results if r["cohort"] == cohort]
        finalized = {
            i for r in rows for t in r["trials"] for i in t["call_ids"]
        }
        budgets = defaultdict(float)
        orphan_reserves = 0.0
        for call in calls:
            owner = str(Path(call["raw_dir"]).parent)
            budgets[owner] = max(
                budgets[owner], call.get("budget_used_usd", 0)
            )
            orphan_reserves += call.get("conservative_reservation_usd", 0)
        guard = json.loads((root / settings["budget_guard"]).read_text())
        known = sum(known_cost(r) for r in calls)
        accounted = sum(budgets.values()) + orphan_reserves
        if abs(accounted - guard["used_usd"]) > 1e-8:
            raise ValueError(f"Guard reconciliation mismatch: {cohort}")
        if guard["used_usd"] > settings["budget_usd"]:
            raise ValueError(f"Guard exceeded: {cohort}")
        interrupted = [
            r
            for r in calls
            if r["call_id"] not in finalized
            and runs[r["run_id"]]["kind"] == "benchmark"
        ]
        diagnostic = [
            r for r in calls if runs[r["run_id"]]["kind"] == "diagnostic"
        ]
        output[cohort] = {
            **settings,
            "job_names": list(runs),
            "finalized_attempts": sum(r["n_results"] for r in rows),
            "verifier_rewards": sum(r["n_verifier"] for r in rows),
            "ledger_calls": len(calls),
            "finalized_calls": len(finalized),
            "interrupted_calls": len(interrupted),
            "diagnostic_calls": len(diagnostic),
            "known_usd": known,
            "finalized_known_usd": sum(
                known_cost(r) for r in calls if r["call_id"] in finalized
            ),
            "interrupted_known_usd": sum(map(known_cost, interrupted)),
            "diagnostic_known_usd": sum(map(known_cost, diagnostic)),
            "cost_states": dict(Counter(map(cost_state, calls))),
            "null_cost_calls": sum(r.get("cost_usd") is None for r in calls),
            "guard_used_usd": guard["used_usd"],
            "retained_reservations_usd": max(0.0, accounted - known),
            "orphan_reservations_usd": orphan_reserves,
        }
    return output


def render_costs(ledger, manifest, accounting):
    text = [
        "# Cost summary with calibration cohort separation",
        "",
        "Regenerate offline: `uv run python -m scripts.calibration_report`. "
        "P1.2 phase tags alone do not identify a budget cohort.",
        "",
    ]
    ids = set()
    for cohort, info in accounting.items():
        rows = scoped_calls(ledger, manifest, cohort)
        ids.update(r["call_id"] for r in rows)
        text += [
            f"## {cohort}",
            "",
            f"Guard: {info['budget_guard']}; used/reserved "
            f"${info['guard_used_usd']:.8f} / ${info['budget_usd']}. "
            "Retained reservations: "
            f"${info['retained_reservations_usd']:.8f}.",
            "",
            render_report(rows).replace("# Cost summary\n", "", 1),
        ]
    other = [r for r in ledger if r.get("call_id") not in ids]
    text += [
        "## Other project cohorts (outside calibration guards)",
        "",
        render_report(other).replace("# Cost summary\n", "", 1),
    ]
    return "\n".join(text)
