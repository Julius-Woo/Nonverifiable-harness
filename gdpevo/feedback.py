"""Explicit arm-facing views; raw oracle results never enter judge inputs."""

import hashlib
import json
import re

PRIVATE = re.compile(
    r"(?:^|[/\s])(?:oracle|eval|output|notes|tests|judge_train_eval|"
    r"judge_evaluators|construction|experiments)(?:[/\s]|$)|"
    r"judge_api\.py|(?:eval|evaluator|test_[\w-]+)\.py|"
    r"task_group\.yaml|normalized_score|earned_score|expected_values",
    re.I,
)
SOURCES = {
    "A0": "a binary outcome score",
    "A1": "an outcome assessment score",
    "A2": "a procedural compliance score",
    "A3": "a self-preference score",
    "A4": "a combined outcome and procedural compliance score",
}


def filtered_trace(records: list[dict]) -> list[dict]:
    """Defense in depth; isolation prevents oracle reads in the first place."""
    result = []
    allowed = {
        "kind",
        "step",
        "text",
        "command",
        "stdout",
        "stderr",
        "return_code",
        "error",
        "protocol_error",
        "answer",
        "wall_s",
    }
    for record in records:
        row = {k: v for k, v in record.items() if k in allowed}
        if PRIVATE.search(json.dumps(row)):
            row = {"kind": record.get("kind"), "redacted": True}
        result.append(row)
    return result


def judge_input(
    *, task_description: str, trace: list[dict], answer: dict
) -> dict:
    """Closed schema without oracle, grader, or filesystem inputs."""
    if not isinstance(answer, dict):
        answer = {}
    return {
        "task_description": task_description,
        "trace": filtered_trace(trace),
        "answer": answer,
    }


def arm_feedback(
    arm: str, *, task_role: str, task: str, trace: list[dict], score: float
) -> dict:
    if arm not in SOURCES or task_role != "search":
        raise ValueError("Only search feedback belongs in an arm workspace")
    return {"task": task, "score": score, "trace": filtered_trace(trace)}


def evolver_prompt(arm: str) -> str:
    return (
        "Improve the feedback score by editing the supplied harness.\n"
        f"The feedback score is {SOURCES[arm]}.\n"
        "Use only the supplied search feedback and workspace tools.\n"
    )


def api_scope(run_id: str, arm: str, role: str) -> dict:
    scope = hashlib.sha256(f"{run_id}/{arm}/{role}".encode()).hexdigest()
    return {"user": f"gdpevo-{scope}", "prompt_cache_key": f"gdpevo-{scope}"}
