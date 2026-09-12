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


async def test_inspection_replays_prefix_without_paid_call_or_command(
    tmp_path,
):
    from evolution.a3_inspection import ReplayBackend, ReplayWorkspace
    from harness.backends import Completion
    from harness.ledger import CallTags
    from harness.seed import API_SYSTEM, run_seed

    instruction = "Read evidence, then finish."
    prompt = (
        API_SYSTEM
        + "\nConversation:\n"
        + json.dumps([{"role": "user", "content": instruction}])
    )
    call = tmp_path / "paid-call"
    call.mkdir()
    (call / "request.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": prompt}]})
    )
    prefix = [
        {
            "kind": "assistant",
            "step": 0,
            "call_id": "paid-call",
            "ok": True,
            "text": json.dumps(
                {"action": "terminal", "command": "cat evidence"}
            ),
        },
        {
            "kind": "observation",
            "step": 0,
            "command": "cat evidence",
            "stdout": "recorded evidence",
            "stderr": "",
            "return_code": 0,
        },
    ]
    calls = []

    async def complete(prompt, tags):
        calls.append(prompt)
        assert "recorded evidence" in prompt
        return Completion(
            '{"action":"finish","answer":"done"}',
            {
                "call_id": "new-call",
                "ok": True,
            },
        )

    backend = ReplayBackend(
        SimpleNamespace(
            logs_dir=tmp_path,
            is_api=True,
            complete=complete,
        ),
        prefix,
    )
    workspace = object.__new__(ReplayWorkspace)
    workspace.prefix, workspace.index = [prefix[1]], 0
    result = await run_seed(
        instruction,
        workspace,
        backend,
        CallTags("test", "test", "A3-native", 1, "task", "a3-diagnose"),
        tmp_path / "continuation.jsonl",
        max_steps=2,
    )
    assert result == "done" and len(calls) == 1
    assert backend.index == workspace.index == 1
    changed = ReplayBackend(backend.backend, prefix)
    with pytest.raises(ValueError, match="changed a paid prompt"):
        await changed.complete("changed", None)
    assert len(calls) == 1


async def test_operator_batch_settles_peers_before_raising():
    import asyncio

    from evolution.a3 import bounded_map

    finished = []

    async def operator(value):
        if value == 0:
            raise ValueError("local admission failed")
        await asyncio.sleep(0.01)
        finished.append(value)
        return value

    with pytest.raises(ValueError, match="local admission failed"):
        await bounded_map(operator, [0, 1, 2])
    assert sorted(finished) == [1, 2]


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_operator_distinguishes_token_exhaustion_from_refusal(
    tmp_path, monkeypatch, finish
):
    from evolution.a3_operators import A3Backend
    from evolution.accounting import AccountedBackend
    from harness.backends import Completion

    (tmp_path / "response.json").write_text(
        json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": "", "refusal": None},
                        "content_filter_results": {},
                    }
                ],
            }
        )
    )
    record = {
        "ok": False,
        "raw_dir": str(tmp_path),
        "termination_category": "content_policy_rejection",
        "note": "Provider content-policy rejection",
    }

    async def complete(*args):
        return Completion("", record)

    monkeypatch.setattr(AccountedBackend, "complete", complete)
    backend = object.__new__(A3Backend)
    result = await backend.complete("prompt", None)
    assert not result.record["ok"]
    if finish == "length":
        assert "termination_category" not in result.record
        assert "token exhaustion" in result.record["note"]
    else:
        assert (
            result.record["termination_category"] == "content_policy_rejection"
        )
