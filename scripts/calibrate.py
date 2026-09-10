"""Run bounded, pinned avg@2 calibration batches; never retry a trial."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from harbor.models.job.config import JobConfig

from scripts.cost_report import read_ledger, render_report
from scripts.smoke import run_harbor

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "mini-json": ("gpt-5-mini", "TASK", "json"),
    "mini-native": ("gpt-5-mini", "TASK", "native"),
    "luna-json": ("gpt56luna", "TASK_ALT2", "json"),
    "luna-native": ("gpt56luna", "TASK_ALT2", "native"),
    "terra-native": ("gpt56terra", "TASK_ALT2", "native"),
    "terra-json": ("gpt56terra", "TASK_ALT2", "json"),
}
MEDIUM_CONFIGS = {
    "mini-json-medium": CONFIGS["mini-json"],
    "luna-json-medium": CONFIGS["luna-json"],
}

ALLOWANCE_CONFIGS = {
    "terra-json-8k": CONFIGS["terra-json"],
    "mini-json-medium-8k": CONFIGS["mini-json"],
    "luna-json-medium-8k": CONFIGS["luna-json"],
}
ALL_CONFIGS = CONFIGS | MEDIUM_CONFIGS | ALLOWANCE_CONFIGS


def job_config(label, concurrency):
    allowance = label in ALLOWANCE_CONFIGS
    medium = label.removesuffix("-8k") in MEDIUM_CONFIGS
    if allowance and concurrency != 3:
        raise ValueError("Allowance follow-up requires concurrency 3")
    if medium and concurrency != 3:
        raise ValueError("Medium follow-up requires concurrency 3")
    if not medium and not allowance and concurrency != 4:
        raise ValueError("Resumed calibration requires concurrency 4")
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    model, endpoint, protocol = ALL_CONFIGS[label]
    run_id = f"calibration-{label}-260910"
    return JobConfig.model_validate(
        {
            "job_name": run_id,
            "jobs_dir": str(ROOT / "logs/harbor"),
            "n_attempts": 2,
            "n_concurrent_trials": concurrency,
            "retry": {"max_retries": 0},
            "environment": {"type": "docker"},
            "agents": [
                {
                    "name": "harness.harbor_agent:SeedAgent",
                    "model_name": model,
                    "kwargs": {
                        "backend": "openai_api",
                        "tool_protocol": protocol,
                        "run_id": run_id,
                        "phase": "P1.2",
                        "arm": label,
                        "endpoint_prefix": endpoint,
                        "max_steps": 24,
                        "reasoning_effort": "medium" if medium else "low",
                        "max_completion_tokens": 8192 if allowance else 4096,
                        "api_max_retries": 0,
                        "rollout_budget_usd": 1.0,
                        "ledger_path": str(ROOT / "costs/ledger.jsonl"),
                        "timing_path": str(
                            ROOT / "logs" / run_id / "timing.jsonl"
                        ),
                        "shared_budget_path": str(
                            ROOT
                            / "costs"
                            / (
                                "calibration_8k_budget.json"
                                if allowance
                                else "calibration_medium_budget.json"
                                if medium
                                else "calibration_budget.json"
                            )
                        ),
                        "shared_budget_usd": (
                            15.0 if allowance else 8.0 if medium else 40.0
                        ),
                    },
                }
            ],
            "tasks": [
                {
                    "path": t["name"],
                    "git_url": split["git_url"],
                    "git_commit_id": split["git_commit"],
                    "source": "terminal-bench",
                }
                for rows in split["splits"].values()
                for t in rows
            ],
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", choices=ALL_CONFIGS)
    parser.add_argument("--concurrency", type=int, choices=[3, 4], default=4)
    parser.add_argument("--recover-missing", action="store_true")
    parser.add_argument("--timeout", type=int, default=4200)
    args = parser.parse_args()
    config = job_config(args.configuration, args.concurrency)
    if args.recover_missing:
        from collections import Counter

        original_job = ROOT / "logs/harbor" / config.job_name
        jobs = [original_job] + sorted(
            original_job.parent.glob(config.job_name + "-*")
        )
        counts = Counter(
            json.loads(p.read_text())["task_name"]
            for job in jobs
            for p in job.glob("*/result.json")
        )
        missing = [t for t in config.tasks if counts[t.path.name] < 2]
        assert missing and all(counts[t.path.name] == 1 for t in missing)
        config.tasks = missing
        config.n_attempts = 1
        suffix = "-resume"
        index = 2
        while (ROOT / "logs" / (config.job_name + suffix)).exists():
            suffix = f"-recovery{index}"
            index += 1
        config.job_name += suffix
        config.agents[0].kwargs["run_id"] = config.job_name
        config.agents[0].kwargs["timing_path"] = str(
            ROOT / "logs" / config.job_name / "timing.jsonl"
        )
    run_dir = ROOT / "logs" / config.job_name
    run_dir.mkdir(parents=True, exist_ok=False)
    config_path = run_dir / "config.json"
    config_path.write_text(config.model_dump_json(indent=2))
    command = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "scripts.harbor_memory_guard",
        "run",
        "--config",
        str(config_path),
        "--n-concurrent",
        str(args.concurrency),
    ]
    (run_dir / "command.json").write_text(json.dumps(command, indent=2))
    os.environ["CALIBRATION_MEMORY_LOG"] = str(run_dir / "memory.jsonl")
    if args.configuration in ALLOWANCE_CONFIGS:
        os.environ["CALIBRATION_DOCKER_LIMIT"] = "7"
    elif args.configuration in MEDIUM_CONFIGS:
        os.environ["CALIBRATION_DOCKER_LIMIT"] = "6"
    subprocess.run(
        ["docker", "ps"], check=True, capture_output=True, timeout=20
    )
    started = time.monotonic()
    status = "completed"
    with (
        (run_dir / "stdout.txt").open("w") as out,
        (run_dir / "stderr.txt").open("w") as err,
    ):
        try:
            code = run_harbor(command, ROOT, out, err, args.timeout)
        except subprocess.TimeoutExpired:
            code, status = 124, "batch_timeout"
        finally:
            ledger = read_ledger(ROOT / "costs/ledger.jsonl")
            (ROOT / "costs/summary.md").write_text(render_report(ledger))
    calls = [r for r in ledger if r["run_id"] == config.job_name]
    summary = {
        "run_id": config.job_name,
        "configuration": args.configuration,
        "concurrency": args.concurrency,
        "status": status,
        "return_code": code,
        "wall_s": time.monotonic() - started,
        "calls": len(calls),
        "known_cost_usd": sum(r.get("cost_usd") or 0 for r in calls),
        "unknown_cost_calls": sum(r.get("cost_usd") is None for r in calls),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
