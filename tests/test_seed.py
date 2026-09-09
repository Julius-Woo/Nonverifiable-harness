import asyncio
from types import SimpleNamespace

import pytest
from harbor.agents.factory import AgentFactory
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from harness.backends import Completion
from harness.harbor_agent import SeedAgent
from harness.ledger import CallTags
from harness.seed import action_command, run_seed
from scripts.cost_report import read_ledger


class FakeBackend:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []

    async def complete(self, prompt, tags):
        self.prompts.append(prompt)
        return Completion(
            next(self.replies),
            {
                "ok": True,
                "call_id": str(len(self.prompts)),
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "output_tokens": 3,
                "cost_usd": 0.001,
            },
        )


class FakeEnvironment:
    def __init__(self):
        self.commands = []

    async def exec(self, command, timeout_sec):
        self.commands.append((command, timeout_sec))
        return SimpleNamespace(
            stdout="visible evidence", stderr="", return_code=0
        )


async def test_seed_recovers_from_bad_json_and_uses_environment(tmp_path):
    backend = FakeBackend(
        [
            "bad json",
            '{"action":"terminal","command":"pwd"}',
            '{"action":"finish","answer":"done"}',
        ]
    )
    env = FakeEnvironment()
    trace = tmp_path / "trace.jsonl"
    answer = await run_seed("task", env, backend, CallTags(), trace)
    assert answer == "done"
    assert env.commands == [("pwd", 30)]
    assert "protocol_error" in backend.prompts[1]
    assert "visible evidence" in backend.prompts[2]
    assert read_ledger(trace)[-1] == {"kind": "finish", "answer": "done"}


async def test_call_limit_stops_loop(tmp_path):
    backend = FakeBackend(['{"action":"terminal","command":"false"}'])
    with pytest.raises(RuntimeError, match="1-call limit"):
        await run_seed(
            "task",
            FakeEnvironment(),
            backend,
            CallTags(),
            tmp_path / "trace",
            max_steps=1,
        )
    assert len(backend.prompts) == 1


async def test_harbor_agent_contract_and_cost_context(tmp_path):
    agent = SeedAgent(
        logs_dir=tmp_path / "agent",
        ledger_path=str(tmp_path / "ledger"),
        timing_path=str(tmp_path / "timing.jsonl"),
    )
    agent.backend = FakeBackend(
        [
            '{"action":"read_file","path":"/tmp/a"}',
            '{"action":"finish","answer":"finished"}',
        ]
    )
    env, context = FakeEnvironment(), AgentContext()
    await agent.setup(env)
    await agent.run("task", env, context)
    assert context.n_input_tokens == 20
    assert context.n_cache_tokens == 4
    assert context.cost_usd == 0.002
    assert context.metadata["status"] == "finished"
    assert (tmp_path / "agent/final.txt").read_text() == "finished"
    assert read_ledger(tmp_path / "timing.jsonl")[0]["status"] == "finished"


async def test_harbor_partial_context_survives_exhaustion(tmp_path):
    agent = SeedAgent(
        logs_dir=tmp_path, max_steps=1, timing_path=str(tmp_path / "timing")
    )
    agent.backend = FakeBackend(['{"action":"terminal","command":"pwd"}'])
    context = AgentContext()
    with pytest.raises(RuntimeError, match="limit"):
        await agent.run("task", FakeEnvironment(), context)
    assert context.cost_usd == 0.001
    assert context.metadata["status"] == "failed"


def test_installed_harbor_factory_accepts_unified_agent_import_path(tmp_path):
    agent = AgentFactory.create_agent_from_config(
        AgentConfig(name="harness.harbor_agent:SeedAgent", model_name="haiku"),
        logs_dir=tmp_path,
    )
    assert isinstance(agent, SeedAgent)


async def test_harbor_cancellation_status(tmp_path):
    class CancelledBackend:
        async def complete(self, *args):
            raise asyncio.CancelledError

    agent = SeedAgent(logs_dir=tmp_path, timing_path=str(tmp_path / "timing"))
    agent.backend = CancelledBackend()
    context = AgentContext()
    with pytest.raises(asyncio.CancelledError):
        await agent.run("task", FakeEnvironment(), context)
    assert context.metadata["status"] == "cancelled"
    assert read_ledger(tmp_path / "timing")[0]["status"] == "cancelled"


def test_file_content_is_encoded_and_paths_are_shell_quoted():
    command = action_command(
        {
            "action": "write_file",
            "path": "/tmp/a'; touch /bad; '",
            "content": "$(touch /bad) `env`\n'quoted'",
        }
    )
    assert "$(touch" not in command and "`env`" not in command
    assert "base64 -d" in command
    with pytest.raises(ValueError, match="absolute"):
        action_command({"action": "read_file", "path": "relative"})


async def test_observation_truncation_keeps_raw_trace(tmp_path):
    backend = FakeBackend(
        [
            '{"action":"terminal","command":"pwd"}',
            '{"action":"finish","answer":"done"}',
        ]
    )
    await run_seed(
        "task",
        FakeEnvironment(),
        backend,
        CallTags(),
        tmp_path / "trace",
        observation_chars=10,
    )
    assert "[observation truncated]" in backend.prompts[1]
    assert "visible evidence" in (tmp_path / "trace").read_text()


def test_api_environment_selection_and_explicit_overrides(
    tmp_path, monkeypatch
):
    from harness.openai_api import OpenAIAPIBackend

    monkeypatch.setattr("harness.harbor_agent.load_dotenv", lambda *a: None)
    monkeypatch.setenv("HARNESS_BACKEND", "openai_api")
    monkeypatch.delenv("HARNESS_MODEL", raising=False)
    monkeypatch.setenv("TASK_MODEL", "gpt-5-mini")
    agent = SeedAgent(logs_dir=tmp_path / "fix-git__abc/agent")
    assert isinstance(agent.backend, OpenAIAPIBackend)
    assert agent.backend.model == "gpt-5-mini"
    assert agent.tags.task == "fix-git"
    monkeypatch.setenv("HARNESS_MODEL", "gpt-5.1")
    assert SeedAgent(logs_dir=tmp_path).model_name == "gpt-5.1"
    monkeypatch.setenv("TASK_ALT2_API_BASE", "https://alternate.test")
    monkeypatch.setenv("TASK_ALT2_API_KEY", "test-alternate")
    agent = SeedAgent(
        logs_dir=tmp_path, endpoint_prefix="TASK_ALT2", model_name="gpt56luna"
    )
    assert agent.backend.model == "gpt56luna"
    assert agent.backend.base_url == "https://alternate.test/openai/v1"
    assert agent.backend.api_key == "test-alternate"


async def test_seed_failure_uses_harbor_verifiable_error(tmp_path):
    from harbor.agents.installed.base import NonZeroAgentExitCodeError

    agent = SeedAgent(
        logs_dir=tmp_path, max_steps=1, timing_path=str(tmp_path / "timing")
    )
    agent.backend = FakeBackend(['{"action":"terminal","command":"pwd"}'])
    with pytest.raises(NonZeroAgentExitCodeError, match="limit"):
        await agent.run("task", FakeEnvironment(), AgentContext())
