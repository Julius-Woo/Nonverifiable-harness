"""Embedding wire accounting, conservative failures, and response ordering."""

import json

import httpx
import pytest

from evolution.a3_embeddings import MODEL, AccountedEmbeddingBackend
from evolution.accounting import BudgetHalt
from evolution.candidates import atomic_json
from evolution.sanitize import canonical, digest
from harness.ledger import CallTags


def backend(tmp_path, transport):
    prices = tmp_path / "prices.json"
    atomic_json(
        prices,
        {
            "models": {
                MODEL: {
                    "input": 0.13,
                    "cached_input": 0.13,
                    "cache_write": 0.13,
                    "output": 0,
                }
            }
        },
    )
    return AccountedEmbeddingBackend(
        base_url="https://fixture.test/openai/v1",
        api_key="fixture-secret",
        model=MODEL,
        effort=None,
        max_completion_tokens=1,
        ledger=tmp_path / "ledger.jsonl",
        logs_dir=tmp_path / "calls",
        prices_path=prices,
        guard_path=tmp_path / "budget.sqlite",
        limiter_path=tmp_path / "limiter.sqlite",
        audit_path=tmp_path / "requests.jsonl",
        tags=CallTags(
            "P1.5", "fixture", "A3-native", 1, "embedding", "a3-diagnose"
        ),
        scope="embedding",
        budget_usd=1,
        max_calls=1,
        transport=transport,
    )


async def test_embedding_wire_receipt_order_and_durable_call_cap(tmp_path):
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        assert request.url.path == "/openai/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "model": MODEL,
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
                "data": [
                    {"index": 1, "embedding": [0, 2]},
                    {"index": 0, "embedding": [3, 0]},
                ],
            },
        )

    b = backend(tmp_path, httpx.MockTransport(respond))
    try:
        vectors, meta = await b.embed(["first", "second"], dimensions=2)
        assert vectors == [[1, 0], [0, 1]]
        assert meta["served_model"] == MODEL
        assert b.guard.used() == pytest.approx(10 * 0.13 / 1e6)
        with pytest.raises(BudgetHalt, match="call cap"):
            await b.embed(["third"], dimensions=2)
    finally:
        b.close()
    assert len(seen) == 1 and "messages" not in seen[0]
    events = [
        json.loads(s)
        for s in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    intent = next(e for e in events if e["event"] == "request_intent")
    assert intent["operation"] == "embeddings"
    assert intent["prompt_sha256"] == digest(canonical(["first", "second"]))
    ledger = json.loads((tmp_path / "ledger.jsonl").read_text())
    assert ledger["output_tokens"] == 0 and ledger["input_tokens"] == 10
    assert "fixture-secret" not in (tmp_path / "requests.jsonl").read_text()


async def test_embedding_http_failure_retains_conservative_reservation(
    tmp_path,
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            500, json={"error": {"message": "service unavailable"}}
        )
    )
    b = backend(tmp_path, transport)
    try:
        with pytest.raises(ValueError, match="HTTP status 500"):
            await b.embed(["nonempty"], dimensions=2)
        assert b.guard.used() > 0
        row = b.guard.db.execute(
            "select charged,status from requests"
        ).fetchone()
        assert row == (None, "response")
    finally:
        b.close()


async def test_embedding_response_recovers_without_receipt_or_ledger(tmp_path):
    from evolution.reconcile import reconcile

    b = backend(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "model": MODEL,
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                    "data": [{"index": 0, "embedding": [1, 0]}],
                },
            )
        ),
    )
    try:
        await b.embed(["first"], dimensions=2)
    finally:
        b.close()
    (tmp_path / "ledger.jsonl").unlink()
    next((tmp_path / "requests").glob("*/receipt.json")).unlink()
    reconcile(tmp_path)
    receipt = json.loads(
        next((tmp_path / "requests").glob("*/receipt.json")).read_text()
    )
    assert receipt["usage"]["completion_tokens"] == 0
    assert receipt["ledger_record"]["ok"] is True
    assert receipt["uncached_upper_usd"] == pytest.approx(10 * 0.13 / 1e6)
    assert json.loads((tmp_path / "ledger.jsonl").read_text())[
        "cost_usd"
    ] == pytest.approx(10 * 0.13 / 1e6)
