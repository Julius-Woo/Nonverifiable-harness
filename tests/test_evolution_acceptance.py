"""Sealed acceptance thresholds and evolver serialization boundary."""

import json
from dataclasses import FrozenInstanceError

import pytest

from evolution.acceptance import (
    AnchorEvaluation,
    CandidateEvaluation,
    EvolverContext,
    SearchEvaluation,
    accept,
)


@pytest.mark.parametrize(
    "candidate,anchor,tau,expected",
    [
        (0.75, 0.5, 0.25, True),
        (0.74, 0.5, 0.25, False),
        (0.9, 0.49, 0.1, False),
        (None, 0.8, 0, False),
    ],
)
def test_gate(candidate, anchor, tau, expected):
    evaluation = CandidateEvaluation(
        SearchEvaluation(0.5, candidate), AnchorEvaluation(0.5, anchor)
    )
    assert accept(evaluation, tau, 0) is expected
    assert accept(evaluation, tau, 0, variant="F-cross") is expected


def test_anchor_isolation():
    search = SearchEvaluation(0.5, 0.75)
    anchor = AnchorEvaluation(0.1234567, 0.2345678)
    trusted = CandidateEvaluation(search, anchor)
    context = EvolverContext(search)
    assert "anchor" not in json.dumps(context.to_dict())
    assert "0.1234567" not in repr(trusted)
    with pytest.raises(TypeError):
        EvolverContext(trusted)
    with pytest.raises((FrozenInstanceError, TypeError)):
        context.anchor = anchor
    with pytest.raises(TypeError):
        EvolverContext(search, anchor=anchor)
    with pytest.raises(ValueError):
        SearchEvaluation(None, 0.5)


def test_agreement_requires_distinct_families_and_both_gains():
    primary = SearchEvaluation(0.5, 0.75, "DeepSeek")
    cross = SearchEvaluation(0.5, 0.625, "Moonshot")
    evaluation = CandidateEvaluation(primary, cross_judge=cross)
    assert accept(evaluation, 0.25, variant="F-agree", cross_tau=0.125)
    with pytest.raises(ValueError, match="explicit calibrated cross_tau"):
        accept(evaluation, 0.25, variant="F-agree")
    with pytest.raises(ValueError):
        accept(
            CandidateEvaluation(primary, cross_judge=primary),
            0.1,
            variant="F-agree",
        )
    with pytest.raises(ValueError):
        accept(evaluation, float("nan"), variant="F-agree")


def test_signed_a3_gate_units_are_explicit_and_separate_from_pass_rates():
    search = SearchEvaluation(-0.5, -0.2, scale="signed_preference")
    assert search.gain == pytest.approx(0.3)
    candidate = CandidateEvaluation(search, AnchorEvaluation(0.5, 0.5))
    assert accept(candidate, 0.25)
    assert EvolverContext(search).to_dict()["search"]["scale"] == (
        "signed_preference"
    )
    for value in (-1, 1):
        SearchEvaluation(value, value, scale="signed_preference")
    for value in (-1.01, 1.01, True, float("nan")):
        with pytest.raises(ValueError):
            SearchEvaluation(value, 0, scale="signed_preference")
    with pytest.raises(ValueError):
        SearchEvaluation(-0.5, 0.1)
    with pytest.raises(ValueError):
        AnchorEvaluation(-0.1, 0.5)


def test_agreement_does_not_borrow_smaller_primary_tau():
    evaluation = CandidateEvaluation(
        SearchEvaluation(0, 1, "DeepSeek"),
        cross_judge=SearchEvaluation(0, 1, "Moonshot"),
    )
    with pytest.raises(ValueError, match="cross_tau"):
        accept(evaluation, 0, variant="F-agree")
    assert accept(evaluation, 0, variant="F-agree", cross_tau=1)
    assert not accept(evaluation, 0, variant="F-agree", cross_tau=1.1)
