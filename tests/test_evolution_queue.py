"""Offline queue durability, retry accounting, and endpoint admission tests."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from evolution.judge_queue import JudgeQueue, TokenBucketLimiter
from evolution.judges import JudgeInput
from evolution.sanitize import sanitize
from harness.openai_api import OpenAIAPIBackend


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def evidence():
    return JudgeInput("Write an answer", sanitize([
        {"kind": "instruction", "text": "Write an answer"},
        {"kind": "finish", "answer": "Could not finish"},
    ]).trajectory)


def response(text='{"score":0.6,"rationale":"Visible evidence"}'):
    return {
        "model": "gpt-5-mini",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens": 30,
        },
    }


def queue_at(path, handler, clock=None, **kwargs):
    clock = clock or Clock()
    limiter = TokenBucketLimiter(
        tpm=1_000_000, clock=clock, sleep=clock.sleep,
    )

    def factory(item_id, attempt):
        return OpenAIAPIBackend(
            "https://example.test", "mock-private-key", "gpt-5-mini",
            ledger=path.parent / "ledger.jsonl",
            logs_dir=path.parent / "calls" / item_id / str(attempt),
            max_retries=0,
            transport=httpx.MockTransport(handler),
            shared_budget_path=path.parent / "budget.json",
            shared_budget_usd=15,
        )

    return JudgeQueue(
        path, factory, limiter, ledger=path.parent / "ledger.jsonl",
        archive_root=path.parent / "calls", **kwargs,
    )


def close(queue):
    queue.close()
    queue.limiter.close()


async def test_dual_bucket_refill_and_persistent_endpoint_state(tmp_path):
    clock = Clock()
    options = dict(path=tmp_path / "limits.sqlite", clock=clock,
                   sleep=clock.sleep)
    first = TokenBucketLimiter(2, 100, endpoint="shared", **options)
    second = TokenBucketLimiter(2, 100, endpoint="shared", **options)
    separate = TokenBucketLimiter(2, 100, endpoint="other", **options)
    assert await first.acquire(80) == 0
    assert await second.acquire(50) == pytest.approx(18)
    assert await first.acquire(1) == pytest.approx(12)
    assert await separate.acquire(100) == 0
    first.defer(70)
    assert await second.acquire(1) == pytest.approx(70)
    with pytest.raises(ValueError):
        await first.acquire(101)
    for limiter in (first, second, separate):
        limiter.close()


async def test_limiter_configuration_conflict_and_invalid_limits(tmp_path):
    for rpm, tpm in ((0, 1), (0.5, 100), (2, float("nan"))):
        with pytest.raises(ValueError):
            TokenBucketLimiter(rpm, tpm)
    first = TokenBucketLimiter(path=tmp_path / "limits")
    second = TokenBucketLimiter(rpm=100, path=tmp_path / "limits")
    await first.acquire(10)
    with pytest.raises(ValueError, match="Conflicting"):
        await second.acquire(10)
    first.close()
    second.close()


async def test_resumption_does_not_rejudge_completed_items(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        assert "tools" not in calls[-1]
        assert set(calls[-1]["messages"][0]) == {"role", "content"}
        return httpx.Response(200, json=response())

    path = tmp_path / "queue.sqlite"
    queue = queue_at(path, handler)
    item = queue.enqueue("rollout", "a1", evidence())
    assert queue.enqueue("rollout", "a1", evidence()) == item
    assert await queue.run() == {"done": 1}
    close(queue)
    queue = queue_at(path, handler)
    assert queue.enqueue("rollout", "a1", evidence()) == item
    assert await queue.run() == {"done": 1}
    assert len(calls) == 1
    records = [json.loads(s) for s in queue.ledger.read_text().splitlines()]
    assert len(records) == 1 and records[0]["role"] == "judge"
    assert "mock-private-key" not in queue.ledger.read_text()
    assert queue.rows()[0]["attempts"] == 1
    close(queue)


async def test_crash_after_ledger_before_queue_commit_recovers(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())

    path = tmp_path / "queue.sqlite"
    queue = queue_at(path, handler)
    queue.enqueue("rollout", "a1", evidence())
    await queue.run()
    original = json.loads(queue.rows()[0]["result"])
    queue.db.execute("UPDATE items SET status='running',result=NULL")
    queue.db.commit()
    close(queue)
    queue = queue_at(path, handler)
    assert await queue.run() == {"done": 1}
    recovered = json.loads(queue.rows()[0]["result"])
    assert recovered["call_id"] == original["call_id"]
    assert recovered["raw_rationale"] == original["raw_rationale"]
    assert len(calls) == 1
    close(queue)


async def test_crash_before_ledger_recovers_saved_response(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())

    queue = queue_at(tmp_path / "queue.sqlite", handler)
    queue.enqueue("rollout", "a1", evidence())
    await queue.run()
    original = json.loads(queue.rows()[0]["result"])
    queue.db.execute("UPDATE items SET status='running',result=NULL")
    queue.db.commit()
    queue.ledger.write_text("")
    assert await queue.run() == {"done": 1}
    recovered = json.loads(queue.rows()[0]["result"])
    assert recovered["call_id"] == original["call_id"]
    assert recovered["record"]["recovered_from_archive"]
    assert recovered["record"]["cost_usd"] is None
    assert recovered["record"]["wall_s"] is None
    assert len(calls) == 1
    assert len(queue.ledger.read_text().splitlines()) == 1
    close(queue)


@pytest.mark.parametrize("failure", ["schema", "http"])
async def test_retry_once_then_failed_is_durable(tmp_path, failure):
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"retry-after": "3"})
            if failure == "http"
            else httpx.Response(200, json=response('{"score":true}'))
        )

    clock = Clock()
    path = tmp_path / "queue.sqlite"
    queue = queue_at(path, handler, clock)
    queue.enqueue("rollout", "a1", evidence())
    assert await queue.run() == {"failed": 1}
    assert queue.rows()[0]["attempts"] == 2
    assert queue.rows()[0]["result"] is None
    if failure == "http":
        assert clock.waits == [3]
    assert len(queue.ledger.read_text().splitlines()) == 2
    close(queue)
    queue = queue_at(path, handler)
    assert await queue.run() == {"failed": 1}
    assert len(calls) == 2
    close(queue)


async def test_retry_success_and_interrupted_attempt_limit(tmp_path):
    statuses = iter([503, 200])

    def handler(request):
        return httpx.Response(next(statuses), json=response())

    queue = queue_at(tmp_path / "queue.sqlite", handler)
    queue.enqueue("normal", "a1", evidence())
    assert await queue.run() == {"done": 1}
    assert queue.rows()[0]["attempts"] == 2
    queue.enqueue("interrupted", "a1", evidence())
    queue.db.execute(
        "UPDATE items SET status='running',attempts=2 "
        "WHERE rollout='interrupted'"
    )
    queue.db.commit()
    assert await queue.run() == {"done": 1, "failed": 1}
    close(queue)


async def test_worker_limit_and_exclusive_ownership(tmp_path):
    active = maximum = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 4:
            started.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json=response())

    path = tmp_path / "queue.sqlite"
    queue = queue_at(path, handler)
    for i in range(7):
        queue.enqueue(str(i), "a1", evidence())
    runner = asyncio.create_task(queue.run())
    await asyncio.wait_for(started.wait(), 3)
    other = queue_at(path, handler)
    with pytest.raises(RuntimeError, match="active runner"):
        await other.run()
    close(other)
    release.set()
    assert await runner == {"done": 7}
    assert maximum == 4
    for count in (0, 5, True):
        with pytest.raises(ValueError):
            await queue.run(count)
    close(queue)


def test_endpoint_settings_cannot_change_on_resume(tmp_path):
    path = tmp_path / "queue.sqlite"
    handler = lambda request: httpx.Response(200, json=response())
    queue = queue_at(path, handler, endpoint_signature={"model": "one"})
    close(queue)
    with pytest.raises(ValueError, match="cannot change endpoint"):
        queue_at(path, handler, endpoint_signature={"model": "two"})


async def test_oversized_reservation_fails_without_api_call(tmp_path):
    def handler(request):
        pytest.fail("Oversized requests must never reach the API")

    queue = queue_at(tmp_path / "queue.sqlite", handler)
    queue.limiter.close()
    queue.limiter = TokenBucketLimiter(tpm=100)
    queue.enqueue("rollout", "a1", evidence())
    assert await queue.run() == {"failed": 1}
    assert queue.rows()[0]["attempts"] == 0
    assert not Path(queue.ledger).exists()
    close(queue)
