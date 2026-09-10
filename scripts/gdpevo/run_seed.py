"""One real GDPevo seed attempt through the isolated Docker boundary."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import time

from dotenv import dotenv_values

from gdpevo import ROOT
from gdpevo.boundary import Attempt, image_ref
from gdpevo.feedback import api_scope, arm_feedback
from gdpevo.oracle import grade_attempt, verify_frozen
from harness.ledger import CallTags, append_jsonl
from harness.openai_api import OpenAIAPIBackend
from harness.seed import SeedError, run_seed


def validate_evidence(group, controls_path, acceptance_path):
    manifest = verify_frozen()
    controls = json.loads(controls_path.read_text())
    if not controls["passed"] or controls["manifest"] != manifest:
        raise RuntimeError("Frozen grader controls must pass before rollouts")
    acceptance = json.loads(acceptance_path.read_text())
    covered = {s["group"] for s in acceptance["services"] if s["passed"]}
    if not acceptance["passed"] or group not in covered:
        raise RuntimeError("Boundary acceptance must cover the selected group")
    boundary = json.loads(
        (acceptance_path.parent / f"g{group:03}/boundary.json").read_text()
    )
    for container in boundary["containers"]:
        role = container["Name"].rsplit("-", 1)[1]
        tag = f"nvh-gdpevo-{role}"
        if role == "service":
            tag += f"-{group:03}"
        if container["Image"] != image_ref(tag + ":v1"):
            raise RuntimeError("Images changed since boundary acceptance")


async def rollout(
    group,
    task_id,
    run_id,
    model="gpt-5-mini",
    controls_path=None,
    acceptance_path=None,
):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", run_id):
        raise ValueError("Invalid run ID")
    controls_path = controls_path or (
        ROOT / "logs/gdpevo/p110-controls-final/controls.json"
    )
    acceptance_path = acceptance_path or (
        ROOT / "logs/gdpevo/p110-acceptance-final/acceptance.json"
    )
    validate_evidence(group, controls_path, acceptance_path)
    logs = ROOT / "logs/gdpevo" / run_id
    logs.mkdir(parents=True, exist_ok=False)
    oracle_dir = ROOT / "oracle" / run_id
    if oracle_dir.exists():
        raise FileExistsError(oracle_dir)
    # Only the host controller reads model credentials. Do not persist values.
    config = dotenv_values(ROOT / ".env")
    ledger = logs / "ledger.jsonl"
    backend = OpenAIAPIBackend(
        base_url=config.get("TASK_API_BASE", ""),
        api_key=config.get("TASK_API_KEY", ""),
        model=model,
        ledger=ledger,
        logs_dir=logs / "calls",
        effort="low",
        max_completion_tokens=4096,
        budget_usd=1.0,
        max_retries=1,
        extra_params=api_scope(run_id, "A0", "task"),
        shared_budget_path=ROOT / "logs/gdpevo/p110-budget.json",
        shared_budget_usd=10.0,
    )
    provenance = {}
    for name in ("seed.py", "openai_api.py", "ledger.py", "backends.py"):
        content = (ROOT / "harness" / name).read_bytes()
        provenance[name] = hashlib.sha256(content).hexdigest()
    (logs / "harness_hashes.json").write_text(json.dumps(provenance, indent=2))
    task = f"{group:03}/train/{task_id}"
    tags = CallTags("P1.10", run_id, "A0", 0, task, "task")
    started = time.monotonic()
    status, error, raw = "failed", None, b""
    canary = ROOT / "oracle" / f"{run_id}-canary.txt"
    canary.parent.mkdir(exist_ok=True)
    canary.write_text(os.urandom(32).hex())
    canary.chmod(0o600)
    with Attempt(group, "train", task_id, logs / "attempt") as boundary:
        probe = await boundary.exec(
            f"cat {shlex.quote(str(canary))} /oracle/{canary.name}",
            timeout_sec=5,
        )
        canary_ok = (
            probe.return_code != 0 and canary.read_text() not in probe.stdout
        )
        (logs / "canary.json").write_text(
            json.dumps(
                {
                    "passed": canary_ok,
                    "return_code": probe.return_code,
                    "stdout": probe.stdout,
                    "stderr": probe.stderr,
                },
                indent=2,
            )
        )
        if not canary_ok:
            raise RuntimeError("Oracle canary readable from solver")
        boundary.tool_failed = (
            False  # Acceptance probe is not a solver action.
        )
        instruction = (
            (logs / "attempt/staged/input/prompt.txt").read_text()
            + "\n\nWork in /work. Read /work/environment_access.md for API "
            "access and read all files under /work/input/payloads. "
            "Write the final JSON object to /work/answer.json. "
            "Use /work/scratch for temporary files."
        )
        try:
            async with asyncio.timeout(1200):
                answer = await run_seed(
                    instruction,
                    boundary,
                    backend,
                    tags,
                    logs / "trajectory.jsonl",
                    max_steps=24,
                    command_timeout_s=30,
                )
            (logs / "final.txt").write_text(answer)
            status = "tool_failure" if boundary.tool_failed else "finished"
        except TimeoutError:
            status, error = "timeout", "rollout timeout"
        except SeedError as exc:
            status, error = "seed_failure", str(exc)
        finally:
            raw = boundary.snapshot()
    rollout_wall = time.monotonic() - started
    result = grade_attempt(group, "train", task_id, raw, oracle_dir, status)
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    costs = [r["cost_usd"] for r in records]
    summary = {
        "run_id": run_id,
        "task": task,
        "model": model,
        "reasoning_effort": "low",
        "max_steps": 24,
        "status": status,
        "error": error,
        "calls": len(records),
        "cost_usd": sum(costs) if None not in costs else None,
        "budget_reserved_usd": backend.budget_used,
        "input_tokens": sum(r.get("input_tokens") or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in records),
        "api_wall_s": sum(r["wall_s"] for r in records),
        "rollout_wall_s": rollout_wall,
        "total_wall_s": time.monotonic() - started,
        "binary": result["binary"],
        "denominator": 1,
        "grader_v1_score": result["grader_v1_score"],
        "upstream_native_score": result["upstream_native_score"],
        "canary_passed": canary_ok,
    }
    (logs / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    append_jsonl(ROOT / "logs/gdpevo/rollouts.jsonl", summary)
    trace = [
        json.loads(line)
        for line in (logs / "trajectory.jsonl").read_text().splitlines()
    ]
    (logs / "arm_feedback.json").write_text(
        json.dumps(
            arm_feedback(
                "A0",
                task_role="search",
                task=task,
                trace=trace,
                score=result["binary"],
            ),
            indent=2,
        )
    )
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", type=int, required=True)
    parser.add_argument("--task-id", default="001")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--controls", type=type(ROOT))
    parser.add_argument("--acceptance", type=type(ROOT))
    args = parser.parse_args()
    asyncio.run(
        rollout(
            args.group,
            args.task_id,
            args.run_id,
            args.model,
            args.controls,
            args.acceptance,
        )
    )
