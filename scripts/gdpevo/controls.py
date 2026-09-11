"""Execute all reference controls and the two review R1 exploit controls."""

import argparse
import json
import time
from pathlib import Path

from gdpevo import GROUPS, SOURCE, task_path
from gdpevo.oracle import GRADER, run_grader, verify_frozen


def wrong_control(group: int) -> bytes:
    task = task_path(SOURCE / "data/task_groups", group, "train", "001")
    answer = json.loads((task / "output/answer.json").read_text())
    if group == 15:
        contact = answer["packet_contact"]["specialist_provider"]
        contact["phone"], contact["fax"] = contact["fax"], contact["phone"]
    elif group == 18:
        row = next(r for r in answer["fee_reconciliation"] if r["fee_items"])
        row["fee_items"].append(dict(row["fee_items"][0]))
    else:
        raise ValueError("No control for this group")
    return json.dumps(answer).encode()


def controls(output: Path) -> dict:
    manifest = verify_frozen()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    records = []
    for group in GROUPS:
        for split in ("train", "test"):
            for number in range(1, 6):
                task_id = f"{number:03}"
                task = task_path(
                    SOURCE / "data/task_groups", group, split, task_id
                )
                raw = (task / "output/answer.json").read_bytes()
                rec = {"task": f"{group:03}/{split}/{task_id}"}
                for name, root in (
                    ("hardened", GRADER),
                    ("native", SOURCE / "data/task_groups"),
                ):
                    rec[name] = run_grader(
                        task_path(root, group, split, task_id)
                        / "eval/eval.sh",
                        raw,
                    )
                records.append(rec)
    wrong = []
    for group in (15, 18):
        raw = wrong_control(group)
        rec = {"task": f"{group:03}/train/001"}
        for name, root in (
            ("hardened", GRADER),
            ("native", SOURCE / "data/task_groups"),
        ):
            rec[name] = run_grader(
                task_path(root, group, "train", "001") / "eval/eval.sh",
                raw,
            )
        wrong.append(rec)
    passed = all(
        r[name].get("score") == 1 and not r[name].get("error")
        for r in records
        for name in ("hardened", "native")
    ) and all(
        0 <= r["hardened"].get("score", 1) < 1
        and r["native"].get("score") == 1
        and not r["hardened"].get("error")
        and not r["native"].get("error")
        and r["native"].get("returncode") == 0
        for r in wrong
    )
    summary = {
        "passed": passed,
        "manifest": manifest,
        "references": len(records),
        "reference_passes": sum(
            r["hardened"].get("score") == 1 for r in records
        ),
        "wrong_controls": wrong,
        "wall_s": time.monotonic() - started,
        "records": records,
    }
    (output / "controls.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = controls(parser.parse_args().output)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("records", "wrong_controls")
            }
        )
    )
    print(
        json.dumps(
            [
                {
                    "task": r["task"],
                    "hardened": r["hardened"].get("score"),
                    "native": r["native"].get("score"),
                }
                for r in result["wrong_controls"]
            ]
        )
    )
    raise SystemExit(not result["passed"])
