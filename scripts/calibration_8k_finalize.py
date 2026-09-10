"""Seal new job hashes and audit the authorized allowance cohort offline."""

import hashlib
import json
from collections import Counter

from scripts.calibrate import ALLOWANCE_CONFIGS, ROOT


def finalize_manifests():
    rows = []
    for label in ALLOWANCE_CONFIGS:
        run_id = f"calibration-{label}-260910"
        job = ROOT / "logs/harbor" / run_id
        counts = Counter(
            json.loads(p.read_text())["task_name"]
            for p in job.glob("*/result.json")
        )
        assert len(counts) == 30 and set(counts.values()) == {2}
        log = ROOT / "logs" / run_id
        summary = json.loads((log / "summary.json").read_text())
        assert summary["return_code"] == 0
        memory = [
            json.loads(line)
            for line in (log / "memory.jsonl").read_text().splitlines()
        ]
        admission = [
            json.loads(line)
            for line in (log / "docker_admission.jsonl")
            .read_text()
            .splitlines()
        ]
        maximum = max(r["alexgshaw_containers"] for r in admission)
        reserved_maximum = max(
            r["foreign_containers"] + r["reserved_trials"] + 1
            for r in admission
            if r["admitted"]
        )
        assert maximum <= 7 and reserved_maximum <= 7
        rows.append(
            {
                "job_name": run_id,
                "finalized_attempts": sum(counts.values()),
                "minimum_memavailable_gib": min(
                    r["available_gb"] for r in memory
                ),
                "memory_pause_samples": sum(
                    r["admission"] == "pause" for r in memory
                ),
                "maximum_observed_alexgshaw_containers": maximum,
                "maximum_admitted_foreign_plus_reserved": reserved_maximum,
                "container_wait_samples": sum(
                    not r["admitted"] for r in admission
                ),
                "summary": summary,
            }
        )
    for name in (
        "calibration_run_manifest.json",
        "calibration_8k_run_manifest.json",
    ):
        path = ROOT / "data" / name
        manifest = json.loads(path.read_text())
        for run in manifest["runs"]:
            if run["cohort"] != "w5f-8k":
                continue
            config = ROOT / "logs/harbor" / run["job_name"] / "config.json"
            run["job_config_sha256"] = hashlib.sha256(
                config.read_bytes()
            ).hexdigest()
            run["baseline_configuration"] = run["configuration"].removesuffix(
                "-8k"
            )
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    archive = ROOT / "logs/calibration-8k-followup-260910"
    (archive / "operational_audit.json").write_text(
        json.dumps(rows, indent=2) + "\n"
    )
    return rows


if __name__ == "__main__":
    print(json.dumps(finalize_manifests(), indent=2))
