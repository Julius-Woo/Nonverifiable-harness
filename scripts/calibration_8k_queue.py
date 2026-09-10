"""Run exactly the three authorized allowance jobs, sequentially."""

import json
import subprocess
from collections import Counter

from scripts.calibrate import ALLOWANCE_CONFIGS, ROOT


def main():
    for label in ALLOWANCE_CONFIGS:
        subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "scripts.calibrate",
                label,
                "--concurrency",
                "3",
                "--timeout",
                "6000",
            ],
            cwd=ROOT,
            check=True,
        )
        job = ROOT / "logs/harbor" / f"calibration-{label}-260910"
        counts = Counter(
            json.loads(p.read_text())["task_name"]
            for p in job.glob("*/result.json")
        )
        assert len(counts) == 30 and set(counts.values()) == {2}


if __name__ == "__main__":
    main()
