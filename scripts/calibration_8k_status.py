"""Read live allowance progress without touching Harbor evidence."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.calibrate import ALLOWANCE_CONFIGS, ROOT
from scripts.calibration_contracts import classify_attempt
from scripts.cost_report import read_ledger


def status():
    by_trial = defaultdict(list)
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    for row in ledger:
        if row["run_id"].endswith("-8k-260910"):
            by_trial[Path(row["raw_dir"]).parents[2].name].append(row)
    output = []
    for label in ALLOWANCE_CONFIGS:
        job = ROOT / "logs/harbor" / f"calibration-{label}-260910"
        rows = []
        for path in job.glob("*/result.json"):
            exception = path.parent / "exception.txt"
            rows.append(
                classify_attempt(
                    json.loads(path.read_text()),
                    read_ledger(path.parent / "agent/trace.jsonl"),
                    by_trial[path.parent.name],
                    exception.read_text() if exception.exists() else "",
                )
            )
        output.append(
            {
                "configuration": label,
                "finalized": len(rows),
                "L1": sum(r["pass_l1"] for r in rows),
                "L2": sum(r["pass_l2"] for r in rows),
                "no_action": sum(r["no_action"] for r in rows),
                "token_exhaustion": sum(r["token_exhaustion"] for r in rows),
                "step_exhaustion": sum(r["step_exhaustion"] for r in rows),
                "budget_exhaustion": sum(r["budget_exhaustion"] for r in rows),
                "terminations": dict(Counter(r["termination"] for r in rows)),
            }
        )
    return {
        "jobs": output,
        "budget": json.loads(
            (ROOT / "costs/calibration_8k_budget.json").read_text()
        ),
    }


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
