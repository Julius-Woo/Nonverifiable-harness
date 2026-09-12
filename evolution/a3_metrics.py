"""A3 completion, censoring and explicitly denominated preference reports."""

from collections import Counter
from fractions import Fraction

from evolution.a3_operators import raw_rating
from evolution.evaluation import aggregate

STATUSES = ("scored", "unscored-budget", "unscored-failure", "censored")


def phase_censored(row):
    """Read trusted execution metadata, never search solver evidence text."""
    execution = row.get("execution") or {}
    exception = row.get("exception_info") or {}
    return bool(
        row.get("censored")
        or execution.get("phase_halt")
        or "Projected phase API budget exceeded"
        in exception.get("exception_message", "")
        or "Phase wall-clock limit reached"
        in exception.get("exception_message", "")
    )


def annotate_measurement(row):
    """Preserve raw oracle labels even when a measurement is censored."""
    row = dict(row)
    censored = phase_censored(row)
    status = row.get("measurement_status")
    if censored:
        status = "censored"
        row["execution"] = {**row.get("execution", {}), "phase_halt": True}
    elif status not in STATUSES:
        status = (
            "scored"
            if row.get("oracle") is not None
            else (
                "unscored-budget"
                if row.get("budget_halt")
                else "unscored-failure"
            )
        )
    return {**row, "censored": censored, "measurement_status": status}


def pair_record(row):
    """Public pair table: no oracle labels or trusted execution internals."""
    status = row.get("pair_status")
    if row.get("excluded") or row.get("reference_excluded"):
        status = "unscored-failure"
    elif phase_censored(row) or row.get("reference_censored"):
        status = "censored"
    elif status not in STATUSES:
        status = (
            "scored"
            if row.get("score") is not None
            else (
                "unscored-budget"
                if row.get("rank_budget_halt")
                else "unscored-failure"
            )
        )
    score = row.get("score") if status == "scored" else None
    rating = raw_rating(score) if score is not None else None
    if (
        score is not None
        and row.get("raw_rating") is not None
        and row["raw_rating"] != rating
    ):
        raise ValueError("Raw A3 rating disagrees with signed preference")
    return {
        "id": row["id"],
        "task": row.get("task"),
        "replicate": row.get("replicate", 0),
        "reference_id": row.get("reference_id"),
        "status": status,
        "raw_rating": rating,
        "signed_preference": score,
        "outcome": (
            None
            if rating is None
            else "win"
            if rating < 0
            else "loss"
            if rating > 0
            else "tie"
        ),
    }


def preference_report(rows):
    pairs = [pair_record(row) for row in rows]
    statuses = Counter(p["status"] for p in pairs)
    outcomes = Counter(p["outcome"] for p in pairs)
    n = statuses["scored"]
    total = -sum(p["raw_rating"] for p in pairs if p["status"] == "scored")
    return {
        "pairs": pairs,
        "status_counts": {s: statuses[s] for s in STATUSES},
        "planned": len(pairs),
        "scored": n,
        "unscored": len(pairs) - n,
        "wins": outcomes["win"],
        "ties": outcomes["tie"],
        "losses": outcomes["loss"],
        "win_rate": outcomes["win"] / n if n else None,
        "win_rate_denominator": n,
        "win_rate_definition": "wins / scored pairs; ties are not wins",
        "observed_signed_mean": total / (10 * n) if n else None,
        "signed_total": total,
        "complete": n == len(pairs) and bool(pairs),
    }


def measurement_report(rows, *, private=False):
    rows = [annotate_measurement(row) for row in rows]
    counts = Counter(row["measurement_status"] for row in rows)
    result = {
        "rows": [
            {
                **{k: row.get(k) for k in ("id", "task", "replicate")},
                "status": row["measurement_status"],
                **({"raw_oracle": row.get("oracle")} if private else {}),
            }
            for row in rows
        ],
        "status_counts": {s: counts[s] for s in STATUSES},
        "planned": len(rows),
        "complete": counts["scored"] == len(rows) and bool(rows),
    }
    return result


def metrics(rows, expected=None):
    """Keep ordinary estimates undefined on censoring; label diagnostics.

    Missing rank failures retain the existing observed J convention with an
    explicit scored denominator. Censored solves never become oracle failures
    in an ordinary endpoint. Raw labels remain on the trusted rows.
    """
    rows = [annotate_measurement(row) for row in rows]
    pairs = preference_report(rows)
    censored = sum(phase_censored(row) for row in rows)
    budget_missing = sum(
        row["measurement_status"] == "unscored-budget" for row in rows
    )
    budget_incomplete = censored + budget_missing
    usable = [
        {**row, "score": pair["signed_preference"]}
        for row, pair in zip(rows, pairs["pairs"])
        if row["measurement_status"] not in {"censored", "unscored-budget"}
    ]
    result = aggregate(
        usable, expected=expected if not budget_incomplete else None
    )
    result.pop("common_gap", None)
    # Aggregate exact raw ratings within task/seed blocks, then equal seeds.
    blocks = {}
    for row in usable:
        if row.get("score") is not None:
            blocks.setdefault(
                (row.get("seed", 1), row.get("task", "task")), []
            ).append(Fraction(-raw_rating(row["score"]), 10))
    seeds = {}
    for (seed, _), scores in blocks.items():
        seeds.setdefault(seed, []).append(sum(scores) / len(scores))
    exact_j = (
        float(sum(sum(v) / len(v) for v in seeds.values()) / len(seeds))
        if seeds
        else None
    )
    result["J"] = exact_j
    result["J_observed_attempts"] = pairs["observed_signed_mean"]
    result.update(
        scale="signed_preference",
        scheduled=len(expected) if expected is not None else len(rows),
        censored=censored,
        excluded_censored=censored,
        excluded_unscored_budget=budget_missing,
        uncensored_rollouts=len(usable),
        measurement_status_counts=measurement_report(rows)["status_counts"],
        preferences=pairs,
        measurement_complete=measurement_report(rows)["complete"]
        and (expected is None or len(rows) == len(expected)),
        estimate_status="censored"
        if budget_incomplete
        else ("complete" if not result["oracle_excluded"] else "incomplete"),
    )
    if budget_incomplete:
        result["uncensored_diagnostic"] = {
            "J": exact_j,
            "O": result["O"],
            "rollouts": len(usable),
            "excluded_censored": censored,
            "excluded_unscored_budget": budget_missing,
            "task_blocks": result["task_blocks"],
        }
        for key in (
            "J",
            "O",
            "O_fixed_denominator",
            "O_observed_attempts",
            "J_observed_attempts",
            "J_fixed_denominator_sensitivity",
            "common_J",
            "common_O",
        ):
            result[key] = None
    return result


def completion(search, sealed, *, optimization_complete=True):
    search_report = measurement_report(search)
    sealed_report = measurement_report(sealed)
    pairs = preference_report(search)
    complete = (
        search_report["complete"]
        and sealed_report["complete"]
        and pairs["complete"]
    )
    return {
        "optimization_complete": optimization_complete,
        "measurement_complete": complete,
        "endpoint_eligible": optimization_complete and complete,
        "search_measurements": search_report,
        "search_preferences": pairs,
        "sealed_measurements": sealed_report,
    }
