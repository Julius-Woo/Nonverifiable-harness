"""Trusted acceptance bit and an explicitly search-only evolver interface.

Python types are an API boundary, not an operating-system security boundary.
The controller owns CandidateEvaluation; it never serializes it to an evolver.
"""

import math
from dataclasses import dataclass
from typing import Literal


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and numeric")


@dataclass(frozen=True, slots=True)
class SearchEvaluation:
    baseline: float
    candidate: float | None
    family: str = "deepseek"

    def __post_init__(self):
        _finite(self.baseline, "baseline judge score")
        for score in (self.baseline, self.candidate):
            if score is not None:
                _finite(score, "judge score")
                if not 0 <= score <= 1:
                    raise ValueError("Judge scores must be in [0, 1]")
        if not isinstance(self.family, str) or not self.family.strip():
            raise ValueError("Judge family is required")

    @property
    def gain(self):
        return (
            None if self.candidate is None else self.candidate - self.baseline
        )


@dataclass(frozen=True, slots=True, repr=False)
class AnchorEvaluation:
    baseline_oracle: float
    candidate_oracle: float

    def __post_init__(self):
        for score in (self.baseline_oracle, self.candidate_oracle):
            _finite(score, "anchor rate")
            if not 0 <= score <= 1:
                raise ValueError("Anchor rates must be in [0, 1]")

    def __repr__(self):
        return "AnchorEvaluation(<sealed>)"


@dataclass(frozen=True, slots=True, repr=False)
class CandidateEvaluation:
    search: SearchEvaluation
    anchor: AnchorEvaluation | None = None
    cross_judge: SearchEvaluation | None = None

    def __post_init__(self):
        if type(self.search) is not SearchEvaluation:
            raise TypeError("search must be SearchEvaluation")
        if (
            self.anchor is not None
            and type(self.anchor) is not AnchorEvaluation
        ):
            raise TypeError("anchor must be AnchorEvaluation")
        if self.cross_judge is not None and (
            type(self.cross_judge) is not SearchEvaluation
        ):
            raise TypeError("cross_judge must be SearchEvaluation")

    def __repr__(self):
        return "CandidateEvaluation(<trusted controller only>)"


@dataclass(frozen=True, slots=True)
class EvolverContext:
    """No arbitrary dictionary or anchor-bearing object can be attached."""

    search: SearchEvaluation

    def __post_init__(self):
        if type(self.search) is not SearchEvaluation:
            raise TypeError("Evolver context accepts only SearchEvaluation")

    def to_dict(self):
        return {
            "search": {
                "baseline": self.search.baseline,
                "candidate": self.search.candidate,
                "family": self.search.family,
            }
        }


def accept(
    candidate_eval: CandidateEvaluation,
    tau: float,
    epsilon: float = 0,
    *,
    variant: Literal["F-in", "F-cross", "F-agree"] = "F-in",
    cross_tau: float | None = None,
) -> bool:
    """Export a bit only; failures reject, equality at either boundary passes.

    F-agree is an optional prospective implementation: both different-family
    judges' search gains must reach their respective thresholds; no anchor.
    """
    if type(candidate_eval) is not CandidateEvaluation:
        raise TypeError("CandidateEvaluation is required")
    for name, value in (("tau", tau), ("epsilon", epsilon)):
        _finite(value, name)
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")
    if variant not in {"F-in", "F-cross", "F-agree"}:
        raise ValueError("Unknown acceptance variant")
    if variant == "F-agree":
        cross = candidate_eval.cross_judge
        if candidate_eval.anchor is not None:
            raise ValueError("F-agree does not use an anchor")
        if cross is None:
            raise ValueError("F-agree requires a second judge")
        if cross.family.casefold().strip() == (
            candidate_eval.search.family.casefold().strip()
        ):
            raise ValueError("F-agree requires different model families")
        threshold = tau if cross_tau is None else cross_tau
        _finite(threshold, "cross_tau")
        if threshold < 0:
            raise ValueError("cross_tau must be nonnegative")
        return bool(
            candidate_eval.search.gain is not None
            and candidate_eval.search.gain >= tau
            and cross.gain is not None
            and cross.gain >= threshold
        )
    anchor = candidate_eval.anchor
    if anchor is None:
        raise ValueError("Anchor is required for F-in/F-cross")
    return bool(
        candidate_eval.search.gain is not None
        and candidate_eval.search.gain >= tau
        and anchor.baseline_oracle - anchor.candidate_oracle <= epsilon
    )
