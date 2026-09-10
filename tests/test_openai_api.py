import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from harness.ledger import CallTags, price_usage
from harness.openai_api import OpenAIAPIBackend, pricing_model, retry_delay
from scripts.cost_report import read_ledger


def response_data(model="gpt-5-mini-2025-08-07"):
    return {
        "model": model,
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 400},
            "completion_tokens": 200,
            "completion_tokens_details": {"reasoning_tokens": 150},
        },
    }


def backend(tmp_path, handler, **kwargs):
    return OpenAIAPIBackend(
        "https://example.test",
        "secret-test-key",
        "gpt-5-mini",
        tmp_path / "ledger",
        tmp_path / "calls",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_request_served_model_usage_and_secret_hygiene(tmp_path):
    def handler(request):
        assert (
            str(request.url)
            == "https://example.test/openai/v1/chat/completions"
        )
        assert request.headers["authorization"] == "Bearer secret-test-key"
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 4096
        assert body["reasoning_effort"] == "low"
        assert "max_tokens" not in body
        return httpx.Response(
            200,
            json=response_data(),
            headers={
                "x-ratelimit-limit-tokens": "1000000",
                "authorization": "secret-test-key",
            },
        )

    result = await backend(tmp_path, handler).complete("test", CallTags())
    row = result.record
    assert row["ok"] and result.text == "OK"
    assert row["model"] == "gpt-5-mini-2025-08-07"
    assert row["requested_model"] == "gpt-5-mini"
    assert row["reasoning_tokens"] == 150
    assert row["cost_usd"] == pytest.approx(0.00056)
    assert read_ledger(tmp_path / "ledger") == [row]
    assert "secret-test-key" not in "".join(
        p.read_text() for p in tmp_path.rglob("*") if p.is_file()
    )


async def test_429_and_503_retry_after_recorded(tmp_path, monkeypatch):
    statuses = iter([429, 503, 200])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("harness.openai_api.asyncio.sleep", sleep)

    def handler(request):
        return httpx.Response(
            next(statuses),
            json=response_data(),
            headers={
                "retry-after": "2",
                "x-ratelimit-remaining-requests": "0",
            },
        )

    result = await backend(tmp_path, handler).complete("test", CallTags())
    assert result.record["ok"]
    assert result.record["http_429s"] == 1
    assert sleeps == [2, 2]
    assert result.record["unpriced_attempts"] == 1
    assert result.record["cost_usd"] is None
    assert result.record["known_response_cost_usd"] > 0
    assert len(result.record["attempts"]) == 3
    assert len(read_ledger(tmp_path / "ledger")) == 1


@pytest.mark.parametrize("status", [400, 401, 429, 503])
async def test_http_failure_is_bounded_and_accounted(tmp_path, status):
    result = await backend(
        tmp_path,
        lambda request: httpx.Response(status, text="secret-test-key"),
        max_retries=0,
    ).complete("test", CallTags())
    assert not result.record["ok"]
    assert result.record["cost_usd"] is None
    assert result.record["note"] == f"HTTP {status}"
    assert len(read_ledger(tmp_path / "ledger")) == 1


async def test_timeout_and_cancellation_accounting(tmp_path):
    async def handler(request):
        await asyncio.sleep(60)

    api = backend(tmp_path, handler, timeout_s=0.02)
    reply = await api.complete("test", CallTags())
    assert reply.record["note"] == "TimeoutError"
    task = asyncio.create_task(api.complete("test", CallTags()))
    await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(read_ledger(tmp_path / "ledger")) == 2
    assert api.budget_used > 0


async def test_unknown_usage_is_not_free_and_budget_prevents_request(tmp_path):
    def handler(request):
        data = response_data()
        del data["usage"]["prompt_tokens_details"]
        return httpx.Response(200, json=data)

    api = backend(tmp_path, handler)
    reply = await api.complete("test", CallTags())
    assert reply.record["cached_input_tokens"] is None
    assert reply.record["cost_usd"] is None
    assert api.budget_used > 0
    api.budget_usd = api.budget_used
    result = await api.complete("test", CallTags())
    assert "budget exceeded" in result.record["note"]
    assert result.record["attempts"] == []


def test_retry_after_date_milliseconds_and_invalid():
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    delay = retry_delay({"retry-after": format_datetime(future)}, 0)
    assert 8 <= delay <= 10
    assert retry_delay({"retry-after-ms": "2500"}, 0) == 2.5
    assert 4 <= retry_delay({"retry-after": "invalid"}, 2) <= 5


def test_required_prices_aliases_and_reasoning_not_double_billed():
    prices = json.loads(Path("scripts/prices.json").read_text())
    for model in (
        "gpt-5-mini",
        "gpt-5.1",
        "gpt-5.5",
        "gpt-5.6-luna",
        "DeepSeek-V4-Flash",
        "DeepSeek-V4-Pro",
        "Kimi-K2.6",
    ):
        rates = prices["models"][model]
        assert rates["checked_at"] == "2026-09-09"
        assert rates["assumption"] is True
        usage = dict(
            input_tokens=100,
            cached_input_tokens=20,
            cache_write_tokens=0,
            output_tokens=50,
            reasoning_tokens=40,
        )
        assert price_usage(model, usage, prices) == pytest.approx(
            (
                80 * rates["input"]
                + 20 * rates["cached_input"]
                + 50 * rates["output"]
            )
            / 1e6
        )
    assert pricing_model("gpt56luna", prices) == "gpt-5.6-luna"
    assert pricing_model("gpt-5.1-2025-11-13", prices) == "gpt-5.1"
    assert pricing_model("gpt-5.1-unknown", prices) == "gpt-5.1-unknown"


async def test_cache_write_pricing_matches_luna_response(tmp_path):
    def handler(request):
        data = response_data("gpt-5.6-luna-2026-07-09")
        data["usage"]["prompt_tokens_details"]["cache_write_tokens"] = 500
        return httpx.Response(200, json=data)

    result = await backend(tmp_path, handler).complete("test", CallTags())
    assert result.record["cache_write_tokens"] == 500
    assert result.record["cost_usd"] == pytest.approx(
        (100 * 0.2 + 400 * 0.02 + 500 * 0.25 + 200 * 1.2) / 1e6
    )


async def test_audited_repricing_is_idempotent(tmp_path):
    from scripts.reprice_api import repair

    def handler(request):
        data = response_data("gpt-5.6-luna-2026-07-09")
        data["usage"]["prompt_tokens_details"]["cache_write_tokens"] = 500
        return httpx.Response(200, json=data)

    api = backend(tmp_path, handler)
    result = await api.complete("test", CallTags())
    original = dict(result.record)
    original["cache_write_tokens"] = 0
    original["cost_usd"] = price_usage("gpt-5.6-luna", original, api.prices)
    api.ledger.write_text(json.dumps(original) + "\n")
    audit = tmp_path / "audit.jsonl"
    count, delta = repair(api.ledger, api.prices, audit)
    assert count == 1 and delta == pytest.approx(0.000025)
    assert read_ledger(audit)[0]["original"] == original
    assert read_ledger(api.ledger)[0]["cost_usd"] == result.record["cost_usd"]
    assert repair(api.ledger, api.prices, audit) == (0, 0)


@pytest.mark.parametrize("as_json", [True, False])
async def test_http_error_body_is_sanitized_and_behavior_unchanged(
    tmp_path, as_json
):
    requests = []

    def handler(request):
        requests.append(request)
        if as_json:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": (
                            "Function tools unsupported: secret-test-key"
                        ),
                        "param": "reasoning_effort",
                    },
                    "api_key": "different-credential",
                    "nested": [{"authorization": "Bearer another-credential"}],
                },
            )
        return httpx.Response(
            400, text="unsupported secret-test-key Bearer other-key"
        )

    api = backend(tmp_path, handler, max_retries=0)
    result = await api.complete("test", CallTags())
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {
        "model": "gpt-5-mini",
        "messages": [{"role": "user", "content": "test"}],
        "max_completion_tokens": 4096,
        "reasoning_effort": "low",
    }
    assert result.record["note"] == "HTTP 400"
    assert not result.record["ok"] and result.record["cost_usd"] is None
    assert api.budget_used == api.projected_cost("test")
    raw = Path(result.record["raw_dir"])
    saved = json.loads((raw / "http_error_1.json").read_text())
    assert saved["status_code"] == 400
    assert saved["method"] == "POST"
    assert (
        saved["endpoint"] == "https://example.test/openai/v1/chat/completions"
    )
    assert not (raw / "response.json").exists()
    archived = "".join(
        p.read_text() for p in tmp_path.rglob("*") if p.is_file()
    )
    for secret in (
        "secret-test-key",
        "different-credential",
        "another-credential",
        "other-key",
    ):
        assert secret not in archived
    assert "unsupported" in archived


async def test_each_retry_error_body_archived_without_affecting_retry(
    tmp_path, monkeypatch
):
    statuses = iter([429, 503, 200])
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("harness.openai_api.asyncio.sleep", sleep)

    def handler(request):
        return httpx.Response(
            next(statuses), json=response_data(), headers={"retry-after": "2"}
        )

    result = await backend(tmp_path, handler).complete("test", CallTags())
    raw = Path(result.record["raw_dir"])
    assert result.record["ok"] and sleeps == [2, 2]
    assert (
        json.loads((raw / "http_error_1.json").read_text())["status_code"]
        == 429
    )
    assert (
        json.loads((raw / "http_error_2.json").read_text())["status_code"]
        == 503
    )
    assert not (raw / "http_error_3.json").exists()
    assert (raw / "response.json").exists()


async def test_error_archive_failure_does_not_change_http_outcome(
    tmp_path, monkeypatch
):
    original = Path.write_text

    def write(path, *args, **kwargs):
        if path.name.startswith("http_error_"):
            raise OSError("disk error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    result = await backend(
        tmp_path,
        lambda request: httpx.Response(400, text="bad"),
        max_retries=0,
    ).complete("test", CallTags())
    assert result.record["note"] == "HTTP 400"
    assert result.record["attempts"][0]["error_body_archive_failed"]
    assert len(result.record["attempts"]) == 1
