"""Frozen host oracle, immutable submission snapshots, fixed denominator."""

import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gdpevo import ROOT, SOURCE, task_path
from scripts.gdpevo.freeze_grader import tree_hash

GRADER = ROOT / "gdpevo/grader_v1"


def strict_json(raw: bytes | str):
    def constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result

    return json.loads(
        raw,
        parse_constant=constant,
        parse_float=number,
        object_pairs_hook=pairs,
    )


def binary_rule(submission, grader: dict, status: str = "finished") -> int:
    """A3: every attempted task contributes exactly one denominator unit."""
    if not isinstance(submission, dict) or status != "finished":
        return 0
    if grader.get("error") or grader.get("returncode") != 0:
        return 0
    result = grader.get("result")
    if not isinstance(result, dict) or result.get("error"):
        return 0
    score = result.get("normalized_score", result.get("score"))
    if isinstance(score, bool) or not isinstance(score, (float, int)):
        return 0
    return int(math.isfinite(score) and abs(score - 1) <= 1e-6)


def verify_frozen() -> dict:
    manifest = json.loads(
        (ROOT / "gdpevo/grader_v1_manifest.json").read_text()
    )
    if tree_hash(GRADER) != manifest["tree_sha256"]:
        raise RuntimeError("Frozen grader hash mismatch")
    return manifest


def run_grader(script: Path, raw: bytes, timeout: float = 15) -> dict:
    """Grade an isolated read-only snapshot, never a mutable solver path."""
    started = time.monotonic()
    snapshots = ROOT / "oracle/snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=snapshots) as work:
        snapshot = Path(work) / "answer.json"
        snapshot.write_bytes(raw)
        snapshot.chmod(0o444)
        env = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "gdpevo/grader_guard"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        process = subprocess.Popen(
            ["bash", str(script.resolve()), str(snapshot)],
            cwd=script.parent,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        error = None
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            error = "timeout"
        record = {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "wall_s": time.monotonic() - started,
        }
        try:
            result = strict_json(stdout)
            score = result.get("normalized_score", result.get("score"))
            if (
                isinstance(score, bool)
                or not isinstance(score, (float, int))
                or not math.isfinite(score)
                or not 0 <= score <= 1 + 1e-6
            ):
                raise ValueError("Invalid normalized score")
            record.update(result=result, score=score)
            if result.get("error"):
                error = error or "grader_reported_error"
        except (ValueError, TypeError, AttributeError):
            error = error or "invalid_grader_output"
        if process.returncode:
            error = error or "grader_exit"
        if snapshot.read_bytes() != raw:
            error = "snapshot_modified"
        if error:
            record["error"] = error
        return record


def grade_attempt(
    group: int,
    split: str,
    task_id: str,
    raw: bytes,
    output: Path,
    status: str = "finished",
    timeout: float = 15,
) -> dict:
    manifest = verify_frozen()
    output.mkdir(parents=True, exist_ok=False)
    answer = output / "answer.json"
    answer.write_bytes(raw)
    answer.chmod(0o444)
    try:
        submission = strict_json(raw)
        valid = isinstance(submission, dict)
    except (ValueError, UnicodeDecodeError):
        submission, valid = None, False
    results = {}
    for name, root in (
        ("grader_v1", GRADER),
        ("upstream", SOURCE / "data/task_groups"),
    ):
        script = task_path(root, group, split, task_id) / "eval/eval.sh"
        results[name] = run_grader(script, raw, timeout)
    result = {
        "group": group,
        "split": split,
        "task_id": task_id,
        "status": status,
        "valid_json_object": valid,
        "denominator": 1,
        "binary": binary_rule(submission, results["grader_v1"], status),
        "grader_v1_score": results["grader_v1"].get("score"),
        "upstream_native_score": results["upstream"].get("score"),
        "grader_tree_sha256": manifest["tree_sha256"],
        "answer_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot": "private host copy, mode 0444, verified after grading",
        **results,
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
