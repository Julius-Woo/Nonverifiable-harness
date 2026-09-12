"""Seed-only matched-rollout C-TTS(A3) selection with isolated pair inputs."""

from evolution.a3 import bounded_map
from evolution.a3_metrics import (
    annotate_measurement,
    pair_record,
    phase_censored,
)
from evolution.a3_operators import Operators, evidence
from evolution.accounting import BudgetHalt
from evolution.sanitize import digest


def retry_policy(config):
    return {
        "infrastructure_replacements": 1,
        "replacement_state": "clean",
        "api_timeout_policy": config.get(
            "api_timeout_policy", "infrastructure"
        ),
        "frozen_grading_recovery": True,
    }


def check_retry_policy(loop, comparator_state):
    expected = comparator_state.stage("a3-retry-policy")
    if expected != retry_policy(loop.config):
        raise ValueError(
            "A3 control must use the comparator infrastructure-retry policy"
        )
    fixed = loop.state.stage("a3-control-allocation-policy")
    policy = {"unit": "logical_rollouts", "retry_policy": expected}
    if fixed is not None and fixed != policy:
        raise ValueError("A3 control allocation policy changed on resume")
    loop.state.stage("a3-control-allocation-policy", policy)


async def score_control_pool(loop, rows, seed, *, operators=None):
    """Compare each sample to its pool's fixed first seed sample.

    Sealed parity pools are disjoint. Their scorer archives remain private and
    nothing is exported to an optimizer. control() owns allocation and resume.
    """
    if not rows:
        return []
    partition = rows[0]["partition"]
    if any(row["partition"] != partition for row in rows):
        raise ValueError("Mixed control partition")
    ops = operators or Operators(loop)
    if partition != "search":
        ops.directory = (
            loop.evaluator.private / "a3-control" / f"i{loop.iteration:02}"
        )
        ops.directory.mkdir(parents=True, exist_ok=True)
    references = {}
    for row in rows:
        key = (
            row["task"],
            row["replicate"] % 2 if partition == "sealed" else 0,
        )
        if (
            key not in references
            or row["replicate"] < references[key]["replicate"]
        ):
            references[key] = row

    async def score(row):
        row = annotate_measurement(row)
        key = (
            row["task"],
            row["replicate"] % 2 if partition == "sealed" else 0,
        )
        reference = references[key]
        value = None
        if reference.get("excluded"):
            row["reference_excluded"] = True
        if phase_censored(reference):
            row["reference_censored"] = True
        if row["measurement_status"] == "unscored-budget":
            row["rank_budget_halt"] = True
        elif (
            not row.get("excluded")
            and not reference.get("excluded")
            and not phase_censored(row)
            and not row.get("reference_censored")
        ):
            if row["id"] == reference["id"]:
                try:
                    evidence(row)  # Even the zero-reference must satisfy v3.
                    value = 0.0
                except (ValueError, OSError):
                    pass
            else:
                try:
                    value = await ops.rank(
                        digest(f"control:{row['id']}:{reference['id']}")[:24],
                        row,
                        reference,
                        seed,
                        seed,
                    )
                except BudgetHalt:
                    row["rank_budget_halt"] = True
        result = {
            **row,
            "score": value,
            "scale": "signed_preference",
            "reference_id": reference["id"],
        }
        pair = pair_record(result)
        return {
            **result,
            "pair_status": pair["status"],
            "raw_rating": pair["raw_rating"],
        }

    return await bounded_map(score, rows)
