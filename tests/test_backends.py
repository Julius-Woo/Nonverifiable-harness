import asyncio
import json
import os
import sys

import pytest

from harness.backends import (
    CLIBackend,
    parse_claude,
    parse_codex,
    parse_copilot,
)
from harness.ledger import CallTags, price_usage
from scripts.cost_report import read_ledger


def test_claude_cache_normalization():
    text, usage = parse_claude(
        json.dumps(
            {
                "result": "OK",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                    "output_tokens": 7,
                },
                "total_cost_usd": 0.03,
                "modelUsage": {"claude-haiku-4-5-20251001": {}},
            }
        ),
        "haiku",
    )
    assert text == "OK"
    assert usage["input_tokens"] == 110
    assert usage["model"] == "claude-haiku-4-5-20251001"
    assert usage["cost_usd"] == 0.03


def test_claude_mixed_models_are_not_attributed_to_first_model():
    _, usage = parse_claude(
        json.dumps(
            {
                "result": "OK",
                "modelUsage": {"b": {}, "a": {}},
                "total_cost_usd": 0.1,
            }
        ),
        "a",
    )
    assert usage["model"] == "mixed:a,b"
    assert usage["mixed_models"] is True
    assert usage["cost_usd"] == 0.1


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 0])
def test_nonfinite_timeouts_rejected(tmp_path, timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        CLIBackend(
            "claude", "haiku", tmp_path / "ledger", tmp_path, timeout_s=timeout
        )


def test_codex_events_ignore_reasoning_text_and_sum_turn_usage():
    events = [
        {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "private reasoning"},
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "OK"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 50,
                "output_tokens": 20,
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 200,
                "cached_input_tokens": 80,
                "output_tokens": 10,
            },
        },
    ]
    text, usage = parse_codex("\n".join(map(json.dumps, events)), "model")
    assert text == "OK"
    assert usage["input_tokens"] == 300
    assert usage["cached_input_tokens"] == 130
    assert usage["output_tokens"] == 30
    assert usage["reasoning_tokens"] is None
    assert (
        parse_codex('{"type":"turn.failed"}', "model")[1]["provider_ok"]
        is False
    )


def test_copilot_real_usage_schema_and_cache_pricing(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(
        json.dumps(
            {
                "totalPremiumRequestCost": 1,
                "totalApiDurationMs": 2697,
                "currentModel": "gpt-5.6-terra",
                "tokenDetails": {
                    k: {"tokenCount": v}
                    for k, v in {
                        "input": 3,
                        "cache_read": 0,
                        "cache_write": 17200,
                        "output": 5,
                    }.items()
                },
                "modelMetrics": {
                    "gpt-5.6-terra": {"usage": {"reasoningTokens": 0}}
                },
            }
        )
    )
    _, usage = parse_copilot("OK", path, "cli-default")
    assert usage["input_tokens"] == 17203
    prices = {
        "models": {
            "gpt-5.6-terra": {
                "input": 2,
                "cached_input": 0.2,
                "cache_write": 2.5,
                "output": 12,
            }
        }
    }
    assert price_usage(usage["model"], usage, prices) == pytest.approx(
        0.043066
    )
    assert price_usage("unknown-model", usage, prices) is None
    usage["input_tokens"] = 300000
    assert price_usage("gpt-5.6-terra", usage, prices) is None


def backend(tmp_path, monkeypatch, code, timeout=2):
    adapter = CLIBackend(
        "claude",
        "haiku",
        tmp_path / "ledger.jsonl",
        tmp_path / "calls",
        timeout_s=timeout,
    )
    monkeypatch.setattr(
        adapter,
        "command",
        lambda *_: ([sys.executable, "-c", code], "test prompt"),
    )
    return adapter


@pytest.mark.parametrize(
    "code,ok",
    [
        (
            'import json; print(json.dumps({"result":"OK", "usage":'
            '{"input_tokens":1,"output_tokens":2}, "total_cost_usd":0.01}))',
            True,
        ),
        ('print("not json")', False),
        ('import sys; print("denied", file=sys.stderr); sys.exit(2)', False),
        (
            "import json; print(json.dumps("
            '{"result":"error", "is_error":True}))',
            False,
        ),
    ],
)
async def test_subprocess_failures_are_accounted(
    tmp_path, monkeypatch, code, ok
):
    adapter = backend(tmp_path, monkeypatch, code)
    result = await adapter.complete("hello", CallTags())
    rows = read_ledger(adapter.ledger)
    assert len(rows) == 1
    assert result.record == rows[0]
    assert rows[0]["ok"] is ok
    assert rows[0]["wall_s"] > 0
    if not ok:
        assert rows[0]["cost_usd"] is None


async def test_missing_executable_is_accounted(tmp_path, monkeypatch):
    adapter = backend(tmp_path, monkeypatch, "")
    monkeypatch.setattr(
        adapter,
        "command",
        lambda *_: ([str(tmp_path / "missing-executable")], ""),
    )
    result = await adapter.complete("hello", CallTags())
    assert result.record["ok"] is False
    assert "FileNotFoundError" in result.record["note"]
    assert len(read_ledger(adapter.ledger)) == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_kill_process_group(
    tmp_path,
    monkeypatch,
    cancel,
):
    pid_file = tmp_path / "pid"
    code = (
        f"import os, pathlib, time; pathlib.Path({str(pid_file)!r})"
        ".write_text(str(os.getpid())); time.sleep(60)"
    )
    adapter = backend(
        tmp_path, monkeypatch, code, timeout=0.2 if not cancel else 5
    )
    task = asyncio.create_task(adapter.complete("hello", CallTags()))
    if cancel:
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).record["ok"] is False
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    rows = read_ledger(adapter.ledger)
    assert len(rows) == 1 and not rows[0]["ok"]
    assert rows[0]["cost_usd"] is None


def test_cli_commands_keep_prompts_out_of_shell(tmp_path):
    for name, model in (
        ("claude", "haiku"),
        ("codex", "gpt-5.6-luna"),
        ("copilot", "gpt-5.6-luna"),
    ):
        adapter = CLIBackend(name, model, tmp_path / "ledger", tmp_path)
        command, stdin = adapter.command(
            "$(touch /should-not-exist)", tmp_path
        )
        assert command[0] == name
        assert not any(
            "bypass" in item or item == "--yolo" for item in command
        )
        if name != "copilot":
            assert stdin == "$(touch /should-not-exist)"
