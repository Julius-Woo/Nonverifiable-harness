"""Run remaining configurations sequentially with four guarded trial slots."""

import argparse
import json
import subprocess
import time

from scripts.calibrate import ROOT
from scripts.calibration_report import collect
from scripts.cost_report import read_ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after-job", required=True)
    args = parser.parse_args()
    previous = ROOT / "logs" / args.after_job / "summary.json"
    while not previous.exists():
        time.sleep(5)
    if json.loads(previous.read_text())["return_code"] != 0:
        raise RuntimeError(
            "Recovery did not complete; reconcile before proceeding"
        )
    state_path = ROOT / "logs/calibration_queue_resumed.json"
    state = {"concurrency": 4, "batches": []}
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    for label in ("luna-native", "luna-json", "terra-native", "terra-json"):
        budget = json.loads(
            (ROOT / "costs/calibration_budget.json").read_text()
        )
        entry = {"configuration": label, "started_unix": time.time()}
        state["batches"].append(entry)
        if label == "terra-json":
            native = collect(
                "terra-native", read_ledger(ROOT / "costs/ledger.jsonl"), split
            )
            projected = max(1, native["known_usd"] * 1.5) if native else 40
            entry["projected_usd"] = projected
            if 40 - budget["used_usd"] < projected:
                entry["status"] = "skipped_optional_budget"
                state_path.write_text(json.dumps(state, indent=2) + "\n")
                continue
        entry["status"] = "running"
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        completed = subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "scripts.calibrate",
                label,
                "--concurrency",
                "4",
                "--timeout",
                "7200",
            ],
            cwd=ROOT,
        )
        entry.update(
            status="returned",
            return_code=completed.returncode,
            finished_unix=time.time(),
        )
        state_path.write_text(json.dumps(state, indent=2) + "\n")
        if completed.returncode:
            raise RuntimeError(
                f"{label} launcher failed; inspect before proceeding"
            )
        subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "scripts.calibration_report",
            ],
            cwd=ROOT,
            check=True,
        )
    state["status"] = "finished"
    state_path.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
