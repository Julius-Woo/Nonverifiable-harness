"""The T2 runner must use the durable T1 transport without hidden retries."""

import json

import httpx
import pytest

from evolution.accounting import PhaseGuard, cost_summary
from evolution.reconcile import reconcile
from gdpevo import runner as module
from gdpevo.runner import Runner, defaults
from harness.ledger import CallTags


async def test_t2_transport_receipt_recovers_lost_ledger(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    value = defaults("receipt-test")
    value["arms"] = ["A0"]
    value["tasks"] = {"search": ["013/train/001"], "anchor": [], "sealed": []}
    value["schedule"] = [
        {
            "arm": "A0",
            "iteration": 0,
            "seed": 1,
            "task": "013/train/001",
            "replicate": 0,
        }
    ]
    value["providers"] = {
        "task": {
            "base_url": "https://example.invalid/v1",
            "deployment": "gpt56terra",
            "rpm": 10000,
            "tpm": 1000000,
        }
    }
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/prices.json").write_text(
        json.dumps(
            {
                "checked_at": "test",
                "models": {
                    "gpt-5.6-terra": {
                        "input": 2,
                        "cached_input": 0.2,
                        "cache_write": 2.5,
                        "output": 12,
                    }
                },
                "aliases": {"gpt56terra": "gpt-5.6-terra"},
            }
        )
    )
    instance = Runner(value, {"TASK_ALT2_API_KEY": "test-key"})
    PhaseGuard(
        instance.accounting / "budget.sqlite", estimate=1, ceiling=1, hours=1
    ).close()
    spec = instance.specs[0]
    backend = instance.backend(spec, "task", tmp_path / "calls")
    captured = []

    def response(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "gpt56terra",
                "choices": [
                    {"message": {"content": "{}"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    backend.external_transport = httpx.MockTransport(response)
    try:
        reply = await backend.complete("test prompt", CallTags())
        assert reply.record["ok"]
        assert backend.max_retries == 0
        assert captured[0]["max_completion_tokens"] == 8192
        assert captured[0]["reasoning_effort"] == "low"
        assert captured[0]["messages"] == [
            {"role": "user", "content": "test prompt"}
        ]
    finally:
        backend.close()
        instance.lock.close()
    # Simulate death after the receipt but before the backend ledger append.
    (instance.accounting / "ledger.jsonl").unlink()
    result = reconcile(instance.accounting)
    assert result["complete"]
    assert (
        result["intents"]
        == result["receipts"]
        == result["matched_ledger_rows"]
        == 1
    )
    costs = cost_summary(
        instance.accounting / "ledger.jsonl",
        instance.accounting / "requests.jsonl",
    )
    assert costs["calls"] == 1
    assert costs["known_usd"] == pytest.approx(0.00032)
    assert costs["unresolved_requests"] == 0


async def test_transport_503_never_retries_and_retains_unknown_charge(
    tmp_path, monkeypatch
):
    from evolution.accounting import AccountedBackend

    guard_path = tmp_path / "budget.sqlite"
    PhaseGuard(guard_path, estimate=1, ceiling=1, hours=1).close()
    called = []

    def unavailable(request):
        called.append(request)
        return httpx.Response(503, json={"error": "unavailable"})

    backend = AccountedBackend(
        base_url="https://example.invalid/v1",
        api_key="key",
        model="gpt56terra",
        ledger=tmp_path / "ledger.jsonl",
        logs_dir=tmp_path / "calls",
        max_retries=0,
        budget_usd=1,
        guard_path=guard_path,
        limiter_path=tmp_path / "limit.sqlite",
        audit_path=tmp_path / "requests.jsonl",
        tags=CallTags(),
        scope="only-one",
        transport=httpx.MockTransport(unavailable),
    )
    try:
        reply = await backend.complete("test", CallTags())
        assert not reply.record["ok"]
        assert len(called) == 1
    finally:
        backend.close()
    result = reconcile(tmp_path)
    assert result["intents"] == 1
    assert any(r["reason"] == "unknown_charge" for r in result["unresolved"])
    costs = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    assert (
        costs["unresolved_requests"] == 1 and costs["budget_accounted_usd"] > 0
    )
