"""Seed-only matched-rollout C-TTS(A3) selection with isolated pair inputs."""

from evolution.a3 import bounded_map
from evolution.a3_operators import Operators
from evolution.sanitize import digest


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
        key = (
            row["task"],
            row["replicate"] % 2 if partition == "sealed" else 0,
        )
        reference = references[key]
        if row["id"] == reference["id"]:
            value = 0.0 if row.get("evidence") else None
        else:
            value = await ops.rank(
                digest(f"control:{row['id']}:{reference['id']}")[:24],
                row,
                reference,
                seed,
                seed,
            )
        return {
            **row,
            "score": value,
            "scale": "signed_preference",
            "reference_id": reference["id"],
        }

    return await bounded_map(score, rows)
