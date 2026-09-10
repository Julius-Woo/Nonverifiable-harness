"""Judge evidence boundary and exact response contracts."""

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from evolution.judges import (
    PROMPTS, JudgeFailure, JudgeInput, build_payload, judge_once,
    mixture, parse_response,
)
from evolution.sanitize import sanitize
from harness.ledger import CallTags


@pytest.fixture
def evidence():
    return JudgeInput("Create /app/answer.txt", sanitize([
        {"kind": "instruction", "text": "Create /app/answer.txt"},
        {"kind": "assistant", "step": 0, "text": json.dumps({
            "action": "write_file", "path": "/app/answer.txt",
            "content": "visible artifact",
        })},
        {"kind": "observation", "step": 0, "command": "write",
         "return_code": 0, "stdout": "written"},
        {"kind": "finish", "answer": "Done"},
    ]).trajectory)


def a2_response():
    return {"items": [
        {"item": i, "score": int(i != 5), "rationale": "Evidence checked",
         "applicable": True, "activated": True, "omission": False,
         "ambiguous": False} for i in range(1, 6)
    ], "early_termination": False}


def test_payload_contract_and_summary(evidence):
    a1 = build_payload("a1", evidence)
    assert set(a1) == {"task_text", "final_summary"}
    assert "command" not in json.dumps(a1)
    artifact = a1["final_summary"]["recorded_artifacts"][0]
    assert artifact["content"] == "visible artifact"
    assert artifact["provenance"] == "solver_authored_write_attempt"
    assert build_payload("a2", evidence)["sanitized_trajectory"] == (
        evidence.trajectory.to_dict()
    )
    with pytest.raises(ValueError):
        JudgeInput.from_dict({**evidence.to_dict(), "oracle": 1})
    with pytest.raises(TypeError):
        JudgeInput("Task", {"events": []})
    with pytest.raises((FrozenInstanceError, TypeError)):
        evidence.oracle = 1
    for prompt in PROMPTS.values():
        assert not any(word in prompt.lower() for word in (
            "ground truth", "test cases", "optimized",
        ))


@pytest.mark.parametrize("score", [
    True, -1, 1.1, float("nan"), "1", 10**1000,
])
def test_invalid_a1_scores(score):
    with pytest.raises(ValueError):
        parse_response("a1", json.dumps({"score": score, "rationale": "x"}))


def test_a2_exact_schema_and_mixture():
    data = a2_response()
    assert parse_response("a2", json.dumps(data))[0] == 0.8
    assert mixture(0.4, 0.8) == pytest.approx(0.6)
    with pytest.raises(JudgeFailure):
        mixture(None, 1)
    data["items"][0]["score"] = True
    with pytest.raises(ValueError):
        parse_response("a2", json.dumps(data))
    data = a2_response()
    data["items"][4]["applicable"] = False
    with pytest.raises(ValueError):
        parse_response("a2", json.dumps(data))


async def test_call_role_and_original_rationale(evidence):
    class Backend:
        async def complete(self, prompt, tags):
            assert tags.role == "judge"
            assert tags.arm == "A1"
            assert "visible artifact" in prompt
            return SimpleNamespace(
                text='{"score":0.7,"rationale":"Visible write"}',
                record={"ok": True, "call_id": "mock", "model": "mock"},
            )
    result = await judge_once(Backend(), "a1", evidence, CallTags())
    assert result["score"] == 0.7
    assert json.loads(result["raw_rationale"])["rationale"] == "Visible write"
