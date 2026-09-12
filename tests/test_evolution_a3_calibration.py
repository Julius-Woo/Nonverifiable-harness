"""Clean native cost admission, L1' labels, and private state."""

import json
from types import SimpleNamespace

import pytest

from evolution.a3_calibration import CleanLoop, PrivateState, l1_prime
from evolution.accounting import BudgetHalt


def test_l1_prime_recovers_observations_but_requires_normal_finish():
    result = {"verifier_result": {"rewards": {"reward": 1}}}
    recovered = [
        {"kind": "observation", "protocol_error": True},
        {
            "kind": "observation",
            "command": "x",
            "error": "timeout",
            "return_code": 1,
        },
        {"kind": "finish", "answer": "Completed."},
    ]
    assert l1_prime(result, {}, recovered)[0] == 1
    assert (
        l1_prime(result, {"status": "timeout_or_cancelled"}, recovered)[0] == 0
    )
    assert (
        l1_prime(result, {"reason": "token_step_budget_exhaustion"}, [])[0]
        == 0
    )
    assert (
        l1_prime(
            {**result, "exception_info": {"exception_type": "TrialError"}},
            {},
            recovered,
        )[0]
        == 0
    )
    assert (
        l1_prime(
            {"verifier_result": {"rewards": {"reward": True}}}, {}, recovered
        )[0]
        is None
    )
    refusal = {
        "exception_info": {"exception_message": "HTTP 400 cyber_policy"}
    }
    assert l1_prime(refusal, {}, [])[::2] == (
        0,
        "provider_content_policy_rejection",
    )


def test_stage_gate_stops_before_dispatch_without_censoring(tmp_path):
    loop = object.__new__(CleanLoop)
    loop.directory = tmp_path
    loop.manifest = {
        "clean_calibration": {
            "projection": {"stages": {"a3-rank": {"unit_usd": 2}}}
        }
    }
    checked = []
    loop.guard = SimpleNamespace(
        used=lambda: 59, limit=60, check=lambda: checked.append(True)
    )
    with pytest.raises(BudgetHalt, match="stage boundary"):
        loop.stage_boundary("a3-rank", 1)
    assert checked == []
    report = json.loads((tmp_path / "completion-report.json").read_text())
    assert report["status"] == "incomplete" and report["censoring"] == "none"
    loop.guard.used = lambda: 10
    loop.stage_boundary("a3-rank", 1)
    assert checked == [True]


def test_search_state_keeps_raw_oracle_in_private_archive(tmp_path):
    state = PrivateState(
        tmp_path / "state.sqlite", private=tmp_path / "oracle"
    )
    try:
        value = {"partition": "search", "oracle": 1, "raw_reward": 1}
        state.stage("search", value)
        stored = json.loads(
            state.db.execute("SELECT value FROM stages").fetchone()[0]
        )
        assert set(stored) == {"private_ref"}
        assert state.stage("search") == value
    finally:
        state.close()


async def test_clean_round_measures_full_search_avg2(tmp_path, monkeypatch):
    from test_evolution_a3 import (
        MockOperators,
        make_loop,
        mock_propose,
        vectors,
    )

    from evolution.a3 import A3Round
    from evolution.loop import EvolutionLoop

    monkeypatch.setattr(EvolutionLoop, "propose", mock_propose)
    loop = make_loop(tmp_path, "A3-native")
    loop.manifest["clean_calibration"] = {"search_measurement_attempts": 2}
    operators = MockOperators()
    runner = A3Round(
        loop,
        operators=operators,
        embedder=lambda texts: (vectors(len(texts)), {"model": "fixture"}),
    )
    try:
        result = await runner.run()
        assert result["search_measurement_attempts"] == 2
        assert result["search_measurements"]["planned"] == 36
        assert result["search_preferences"]["scored"] == 36
        assert result["endpoint_eligible"]
        assert len(operators.pairs) == 66
        core = json.loads((loop.directory / "a3-coreset.json").read_text())
        assert "prior" not in core and "prior_private_ref" in core
        assert not (loop.logs / "a3/i01/measurement.json").exists()
        assert (loop.evaluator.private / "a3/i01/measurement.json").exists()
    finally:
        loop.close()
