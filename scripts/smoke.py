"""Run one extract-elf trial, or print its command without touching Docker."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from harness.ledger import append_jsonl, utc_now
from scripts.cost_report import read_ledger, render_report

ROOT = Path(__file__).resolve().parents[1]


def run_harbor(command, cwd, stdout, stderr, timeout_s):
    """Bound Harbor wall time and allow its asyncio cancellation cleanup."""
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True
    )
    try:
        return proc.wait(timeout=timeout_s)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # Kill any remaining members, even if the group leader has exited.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise


def harbor_command(
    root: Path,
    run_id: str,
    tasks=None,
    model=None,
    concurrency=1,
    endpoint_prefix="TASK",
) -> list[str]:
    tasks = tasks or ["extract-elf"]
    command = [
        str(root / ".venv/bin/harbor"),
        "run",
        "--dataset",
        "terminal-bench@2.0",
        "--agent",
        "harness.harbor_agent:SeedAgent",
        "--model",
        model
        or os.getenv(
            "HARNESS_MODEL",
            os.getenv(f"{endpoint_prefix}_MODEL", "gpt-5-mini"),
        ),
        "--env",
        "docker",
        "--n-concurrent",
        str(concurrency),
        "--n-attempts",
        "1",
        "--max-retries",
        "0",
        "--job-name",
        run_id,
        "--jobs-dir",
        str(root / "logs" / "harbor"),
        "--agent-kwarg",
        f"backend={os.getenv('HARNESS_BACKEND', 'openai_api')}",
        "--agent-kwarg",
        f"run_id={run_id}",
        "--agent-kwarg",
        f"endpoint_prefix={endpoint_prefix}",
        "--agent-kwarg",
        f"ledger_path={root / 'costs/ledger.jsonl'}",
        "--agent-kwarg",
        "max_steps=24",
        "--agent-kwarg",
        f"timing_path={root / 'logs' / run_id / 'timing.jsonl'}",
    ]

    for task in tasks:
        command.extend(["--include-task-name", task])
    return command


def oracle_results(job: Path) -> list[dict]:
    results = []
    for path in sorted(job.glob("*/result.json")):
        data = json.loads(path.read_text())
        verifier = data.get("verifier_result") or {}
        results.append(
            {
                "trial": path.parent.name,
                "rewards": verifier.get("rewards"),
                "exception_info": data.get("exception_info"),
                "source": str(path),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Overall Harbor wall limit in seconds",
    )
    parser.add_argument("--tasks", nargs="+", default=["extract-elf"])
    parser.add_argument("--model")
    parser.add_argument("-n", "--concurrency", type=int, default=1)
    parser.add_argument("--endpoint-prefix", default="TASK")
    parser.add_argument("--label", default="extract-elf")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.concurrency <= 0:
        parser.error("--concurrency must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    run_id = (
        "smoke-"
        + args.label
        + "-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    command = harbor_command(
        ROOT,
        run_id,
        args.tasks,
        args.model,
        args.concurrency,
        args.endpoint_prefix,
    )
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    run_dir = ROOT / "logs" / run_id
    run_dir.mkdir(parents=True)
    ledger = ROOT / "costs/ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.touch(exist_ok=True)
    started = time.monotonic()
    status, exit_code = "failed", 1
    try:
        try:
            check = subprocess.run(
                ["docker", "ps"], capture_output=True, text=True, timeout=20
            )
            (run_dir / "docker.stdout.txt").write_text(check.stdout)
            (run_dir / "docker.stderr.txt").write_text(check.stderr)
            error = check.stderr if check.returncode else ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error or check.returncode:
            status = "docker_preflight_failed"
            with (ROOT / "docs/smoke_run.md").open("a") as handle:
                handle.write(
                    f"\n## Attempt {run_id}\n\nDocker preflight failed; "
                    f"no rollout started.\n\n```text\n{error}\n```\n"
                )
            print(error, flush=True)
            return 1
        (run_dir / "command.json").write_text(json.dumps(command, indent=2))
        job_dir = ROOT / "logs" / "harbor" / run_id
        with (
            (run_dir / "harbor.stdout.txt").open("w") as out,
            (run_dir / "harbor.stderr.txt").open("w") as err,
        ):
            try:
                return_code = run_harbor(command, ROOT, out, err, args.timeout)
            except subprocess.TimeoutExpired:
                return_code = 124
                status = "timeout"
        results = oracle_results(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "smoke_oracle.json").write_text(
            json.dumps(results, indent=2)
        )
        calls = [r for r in read_ledger(ledger) if r["run_id"] == run_id]
        complete = (
            return_code == 0
            and len(results) == len(args.tasks)
            and all(r["rewards"] for r in results)
            and all(r["exception_info"] is None for r in results)
            and bool(calls)
        )
        if status != "timeout":
            status = "oracle_recorded" if complete else "incomplete"
        exit_code = 0 if complete else 1
        summary = {
            "run_id": run_id,
            "status": status,
            "harbor_return_code": return_code,
            "model_calls": len(calls),
            "known_cost_usd": sum(r.get("cost_usd") or 0 for r in calls),
            "unknown_cost_calls": sum(
                r.get("cost_usd") is None for r in calls
            ),
            "wall_s": time.monotonic() - started,
            "concurrency": args.concurrency,
            "tasks": args.tasks,
            "safe_concurrency": "not established",
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    except KeyboardInterrupt:
        status = "cancelled"
        raise
    finally:
        append_jsonl(
            run_dir / "timing.jsonl",
            {
                "ts": utc_now(),
                "kind": "smoke_total",
                "run_id": run_id,
                "status": status,
                "wall_s": time.monotonic() - started,
            },
        )
        (ROOT / "costs/summary.md").write_text(
            render_report(read_ledger(ledger))
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
