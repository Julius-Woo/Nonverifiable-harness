"""Audit T2 graders; keep raw oracle output outside agent workspaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

GROUPS = (8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20)
ROOT = Path(__file__).resolve().parents[2]


def run_grader(script: Path, answer: Path, env: dict, timeout: float) -> dict:
    """Run the shell entry point, preserving errors and score semantics."""
    started = time.perf_counter()
    process = subprocess.Popen(
        ["bash", str(script), str(answer)],
        cwd=script.parent,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        return {
            "error": "timeout",
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "wall_s": time.perf_counter() - started,
        }
    record = {
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "wall_s": time.perf_counter() - started,
    }
    try:
        result = json.loads(stdout)
        if "normalized_score" in result:
            score = result["normalized_score"]
        else:
            score = result["score"]
        record["raw_score"] = result.get("score")
        record["max_score"] = result.get("max_score")
        if isinstance(score, bool) or not isinstance(score, (float, int)):
            raise TypeError("score must be numeric")
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("score outside [0, 1]")
        record.update(score=score, correct=result.get("correct"))
        points = result.get("points", [])
        record["all_points_passed"] = (
            all(
                point.get("passed", point.get("matched")) is True
                for point in points
            )
            if points
            else None
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        record["error"] = f"invalid grader result: {exc}"
    if process.returncode:
        record["error"] = f"grader exit {process.returncode}"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "external/GDPevo"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, nargs="+", default=GROUPS)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    source = args.source.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    records = []
    inventory = []
    with tempfile.TemporaryDirectory(prefix="gdpevo_audit_") as temp:
        temp = Path(temp)
        # This is a Python network guard for inspected graders, not an OS jail.
        (temp / "sitecustomize.py").write_text(
            "import sys\n"
            "def guard(event, args):\n"
            "    if event.startswith('socket.'):\n"
            "        raise RuntimeError('GDPevo audit: network disabled')\n"
            "sys.addaudithook(guard)\n",
            encoding="utf-8",
        )
        env = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "PYTHONPATH": str(temp),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        guard_check = subprocess.run(
            [sys.executable, "-c", "import socket; socket.socket()"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if "GDPevo audit: network disabled" not in guard_check.stderr:
            raise RuntimeError("Python network guard failed its self-check")
        controls = {}
        for name, content in {
            "empty": "{}",
            "malformed": "{",
            "null": "null",
        }.items():
            path = temp / f"{name}.json"
            path.write_text(content, encoding="utf-8")
            controls[name] = path
        controls["missing"] = temp / "missing.json"
        for group in args.groups:
            group_dir = source / "data/task_groups" / f"task_group_{group:03}"
            for split in ("train", "test"):
                tasks = sorted((group_dir / f"{split}_tasks").glob("[0-9]*"))
                if len(tasks) != 5:
                    raise ValueError(
                        f"Expected 5 {split} tasks in {group_dir}"
                    )
                for task in tasks:
                    key = f"{group:03}/{split}/{task.name}"
                    reference = task / "output/answer.json"
                    script = task / "eval/eval.sh"
                    for required in (
                        reference,
                        script,
                        task / "input/prompt.txt",
                    ):
                        if not required.is_file():
                            raise FileNotFoundError(required)
                    inputs = [script, reference, *task.glob("eval/*")]
                    shared = group_dir / "eval_common.py"
                    if shared.exists():
                        inputs.append(shared)
                    inventory.append(
                        {
                            "task": key,
                            "title": (task / "input/prompt.txt")
                            .read_text()
                            .splitlines()[0],
                            "sha256": {
                                str(p.relative_to(source)): hashlib.sha256(
                                    p.read_bytes()
                                ).hexdigest()
                                for p in sorted(set(inputs))
                                if p.is_file()
                            },
                        }
                    )
                    cases = {
                        "reference": reference,
                        "repeat": reference,
                        **controls,
                        "template": task
                        / "input/payloads/answer_template.json",
                    }
                    for case, answer in cases.items():
                        if case == "template" and not answer.exists():
                            continue
                        result = run_grader(script, answer, env, args.timeout)
                        raw = args.output / key / f"{case}.json"
                        raw.parent.mkdir(parents=True, exist_ok=True)
                        raw.write_text(json.dumps(result, indent=2) + "\n")
                        records.append(
                            {
                                "task": key,
                                "case": case,
                                **{
                                    k: v
                                    for k, v in result.items()
                                    if k not in ("stdout", "stderr")
                                },
                            }
                        )
    references = [r for r in records if r["case"] == "reference"]
    repeats = [r for r in records if r["case"] == "repeat"]
    summary = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain"],
                text=True,
            ).strip()
        ),
        "python": sys.version,
        "network_guard": (
            "Python socket audit events denied; self-check passed"
        ),
        "task_count": len(references),
        "invocations": len(records),
        "reference_full_score": sum(r.get("score") == 1 for r in references),
        "reference_correct_true": sum(
            r.get("correct") is True for r in references
        ),
        "reference_errors": sum("error" in r for r in references),
        "repeat_score_matches": sum(
            (a.get("score"), a.get("correct"), a.get("error"))
            == (b.get("score"), b.get("correct"), b.get("error"))
            for a, b in zip(references, repeats, strict=True)
        ),
        "median_grader_wall_s": statistics.median(
            r["wall_s"] for r in records
        ),
        "total_wall_s": time.perf_counter() - started,
        "model_calls": 0,
        "model_cost_usd": 0,
        "inventory": inventory,
        "results": records,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in ("inventory", "results")
            },
            indent=2,
        )
    )
    return int(any("error" in r or r.get("score") != 1 for r in references))


if __name__ == "__main__":
    raise SystemExit(main())
