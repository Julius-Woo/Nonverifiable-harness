"""Provider-free protocol, budget, and stratification checks."""

import json
from pathlib import Path

import httpx
import pytest

from harness.budget import adjust_budget
from harness.ledger import CallTags
from harness.openai_api import OpenAIAPIBackend
from harness.seed import API_SYSTEM, NATIVE_TOOLS, run_seed
from scripts.cost_report import read_ledger
from scripts.make_tb2_split import make_split
from tests.test_seed import FakeEnvironment


@pytest.mark.parametrize("protocol", ["json", "native"])
async def test_real_backend_protocol_roundtrip(tmp_path, protocol):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if protocol == "native":
            assert body["tools"] == NATIVE_TOOLS
            assert body["parallel_tool_calls"] is False
            if len(requests) == 1:
                message = {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_one",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": '{"command":"pwd"}',
                            },
                        }
                    ],
                }
            else:
                assert body["messages"][-1]["role"] == "tool"
                assert body["messages"][-1]["tool_call_id"] == "call_one"
                message = {"content": "done"}
        else:
            assert "tools" not in body
            assert "model CLI" not in body["messages"][0]["content"]
            message = {
                "content": (
                    '{"action":"terminal","command":"pwd"}'
                    if len(requests) == 1
                    else '{"action":"finish","answer":"done"}'
                )
            }
        return httpx.Response(
            200,
            json={
                "model": "gpt-5-mini",
                "choices": [{"message": message}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    backend = OpenAIAPIBackend(
        "https://example.test",
        "test-key",
        "gpt-5-mini",
        tmp_path / "ledger",
        tmp_path / "calls",
        transport=httpx.MockTransport(handler),
        max_retries=0,
        shared_budget_path=tmp_path / "budget",
    )
    trace = tmp_path / "trace"
    result = await run_seed(
        "task",
        FakeEnvironment(),
        backend,
        CallTags(),
        trace,
        tool_protocol=protocol,
    )
    assert result == "done"
    rows = read_ledger(trace)
    assert [r["kind"] for r in rows] == [
        "instruction",
        "assistant",
        "observation",
        "assistant",
        "finish",
    ]
    assert json.loads(rows[1]["text"]) == {
        "action": "terminal",
        "command": "pwd",
    }
    assert len(read_ledger(tmp_path / "ledger")) == 2
    assert "model CLI" not in API_SYSTEM
    assert json.loads((tmp_path / "budget").read_text())["used_usd"] > 0


def test_shared_budget_rejects_before_mutation(tmp_path):
    path = tmp_path / "budget"
    adjust_budget(path, 0.8, 1)
    with pytest.raises(ValueError, match="experiment budget"):
        adjust_budget(path, 0.3, 1)
    assert json.loads(path.read_text())["used_usd"] == 0.8
    assert adjust_budget(path, -0.5, 1) == pytest.approx(0.3)


def test_split_deterministic_disjoint_and_proportional():
    tasks = [
        {"name": f"{difficulty}-{i}", "difficulty": difficulty}
        for difficulty, count in [("easy", 4), ("medium", 55), ("hard", 30)]
        for i in range(count)
    ]
    split = make_split(tasks)
    assert split == make_split(list(reversed(tasks)))
    assert split["sample_counts"] == {"easy": 1, "medium": 19, "hard": 10}
    assert [len(v) for v in split["splits"].values()] == [18, 6, 6]
    assert len({t["name"] for v in split["splits"].values() for t in v}) == 30
    stored = json.loads(Path("data/tb2_split.json").read_text())
    assert stored["sample_counts"] == split["sample_counts"]


@pytest.mark.parametrize("action", ["read_file", "write_file"])
async def test_native_file_actions_and_observation(tmp_path, action):
    from harness.backends import Completion

    class Backend:
        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, tags, **kwargs):
            self.calls += 1
            if self.calls == 1:
                args = {"path": "/tmp/a"}
                if action == "write_file":
                    args["content"] = "hello"
                message = {
                    "tool_calls": [
                        {
                            "id": "one",
                            "type": "function",
                            "function": {
                                "name": action,
                                "arguments": json.dumps(args),
                            },
                        }
                    ]
                }
                return Completion("", {"call_id": "1", "ok": True}, message)
            return Completion("done", {"call_id": "2", "ok": True}, {})

    trace = tmp_path / "trace"
    await run_seed(
        "task",
        FakeEnvironment(),
        Backend(),
        CallTags(),
        trace,
        tool_protocol="native",
    )
    rows = read_ledger(trace)
    assert rows[2]["kind"] == "observation"
    assert "command" in rows[2]
    assert json.loads(rows[1]["text"])["action"] == action


async def test_native_plain_finish_has_no_observation(tmp_path):
    from harness.backends import Completion

    class Backend:
        async def complete(self, *args, **kwargs):
            return Completion("done", {"call_id": "1", "ok": True}, {})

    trace = tmp_path / "trace"
    await run_seed(
        "task",
        FakeEnvironment(),
        Backend(),
        CallTags(),
        trace,
        tool_protocol="native",
    )
    assert [r["kind"] for r in read_ledger(trace)] == [
        "instruction",
        "assistant",
        "finish",
    ]


async def test_native_malformed_call_is_feedback_without_execution(tmp_path):
    from harness.backends import Completion
    from harness.seed import SeedError

    class Backend:
        async def complete(self, *args, **kwargs):
            return Completion(
                "",
                {"call_id": "1", "ok": True},
                {
                    "tool_calls": [
                        {
                            "id": "one",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": "invalid json",
                            },
                        }
                    ],
                },
            )

    trace = tmp_path / "trace"
    env = FakeEnvironment()
    with pytest.raises(SeedError, match="1-call limit"):
        await run_seed(
            "task",
            env,
            Backend(),
            CallTags(),
            trace,
            tool_protocol="native",
            max_steps=1,
        )
    assert "protocol_error" in read_ledger(trace)[-1]
    assert "command" not in read_ledger(trace)[-1]


async def test_native_preserves_text_accompanying_a_tool_call(tmp_path):
    from harness.backends import Completion
    from harness.seed import SeedError

    class Backend:
        async def complete(self, *args, **kwargs):
            return Completion(
                "I claim this is correct.",
                {"call_id": "1", "ok": True},
                {
                    "tool_calls": [
                        {
                            "id": "one",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": '{"command":"pwd"}',
                            },
                        }
                    ],
                },
            )

    trace = tmp_path / "trace"
    with pytest.raises(SeedError, match="1-call limit"):
        await run_seed(
            "task",
            FakeEnvironment(),
            Backend(),
            CallTags(),
            trace,
            tool_protocol="native",
            max_steps=1,
        )
    action = json.loads(read_ledger(trace)[1]["text"])
    assert action["assistant_text"] == "I claim this is correct."
    assert action["command"] == "pwd"
