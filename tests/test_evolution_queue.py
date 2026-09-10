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
    return JudgeInput(
        "Write an answer",
        sanitize(
            [
                {"kind": "instruction", "text": "Write an answer"},
                {"kind": "finish", "answer": "Could not finish"},
            ]
        ).trajectory,
    )


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
        tpm=1_000_000,
        clock=clock,
        sleep=clock.sleep,
    )

    def factory(item_id, attempt):
        return OpenAIAPIBackend(
            "https://example.test",
            "mock-private-key",
            "gpt-5-mini",
            ledger=path.parent / "ledger.jsonl",
            logs_dir=path.parent / "calls" / item_id / str(attempt),
            max_retries=0,
            transport=httpx.MockTransport(handler),
            shared_budget_path=path.parent / "budget.json",
            shared_budget_usd=15,
        )

    return JudgeQueue(
        path,
        factory,
        limiter,
        ledger=path.parent / "ledger.jsonl",
        archive_root=path.parent / "calls",
        **kwargs,
    )


def close(queue):
    queue.close()
    queue.limiter.close()


async def test_dual_bucket_refill_and_persistent_endpoint_state(tmp_path):
    clock = Clock()
    options = dict(
        path=tmp_path / "limits.sqlite", clock=clock, sleep=clock.sleep
    )
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

    def handler(request):
        return httpx.Response(200, json=response())

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
    with pytest.raises(ValueError, match="one minute"):
        queue.enqueue("rollout", "a1", evidence())
    assert await queue.run() == {}
    assert not queue.rows()
    assert not Path(queue.ledger).exists()
    close(queue)


async def test_frozen_identity_conflicts_never_grant_new_attempts(
    tmp_path,
    monkeypatch,
):
    from evolution import judges

    queue = queue_at(
        tmp_path / "queue.sqlite",
        lambda r: httpx.Response(200, json=response()),
    )
    original = evidence()
    identity = queue.enqueue("rollout", "a1", original)
    await queue.run()
    changed = JudgeInput("Changed instruction", original.trajectory)
    with pytest.raises(ValueError, match="conflict"):
        queue.enqueue("rollout", "a1", changed)
    # Even A1 evidence absent from its summary must be frozen too.
    events = original.trajectory.events
    events.insert(1, {"kind": "assistant", "text": "Different reasoning"})
    with pytest.raises(ValueError, match="conflict"):
        queue.enqueue(
            "rollout",
            "a1",
            JudgeInput(
                original.task_text,
                sanitize(events).trajectory,
            ),
        )
    monkeypatch.setitem(judges.PROMPTS, "a1", judges.PROMPTS["a1"] + "changed")
    with pytest.raises(ValueError, match="conflict"):
        queue.enqueue("rollout", "a1", original)
    assert len(queue.rows()) == 1
    assert queue.rows()[0]["id"] == identity
    assert queue.rows()[0]["attempts"] == 1
    assert queue.rows()[0]["status"] == "done"
    assert (
        sum(
            json.loads(s)["status"] == "conflict"
            for s in queue.event_log.read_text().splitlines()
        )
        == 3
    )
    close(queue)


def test_queue_refuses_legacy_and_mixed_evidence_versions(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE items (id TEXT)")
    with pytest.raises(ValueError, match="Legacy evidence version"):
        queue_at(path, lambda r: None)
    path = tmp_path / "mixed.sqlite"
    queue = queue_at(path, lambda r: None)
    queue.enqueue("one", "a1", evidence())
    queue.enqueue("two", "a1", evidence())
    queue.db.execute(
        "UPDATE items SET evidence_version='v1' WHERE rollout='one'"
    )
    queue.db.commit()
    close(queue)
    with pytest.raises(ValueError, match="Mixed"):
        queue_at(path, lambda r: None)


async def test_stream_ingests_results_before_source_finishes(tmp_path):
    fast_done, release_slow = asyncio.Event(), asyncio.Event()
    completions = []

    async def handler(request):
        if (
            "Slow task"
            in json.loads(request.content)["messages"][0]["content"]
        ):
            await release_slow.wait()
        return httpx.Response(200, json=response())

    async def source():
        yield "slow", "a1", JudgeInput("Slow task", evidence().trajectory), 0
        yield "fast", "a1", evidence(), 0
        await asyncio.wait_for(fast_done.wait(), 3)
        assert completions == ["fast"]
        yield "later", "a1", evidence(), 0
        release_slow.set()

    async def ingest(row):
        assert row["status"] == "done"
        completions.append(row["rollout"])
        if row["rollout"] == "fast":
            fast_done.set()

    queue = queue_at(tmp_path / "queue.sqlite", handler)
    assert await queue.run(source=source(), on_result=ingest) == {"done": 3}
    assert completions[0] == "fast"
    assert set(completions) == {"fast", "slow", "later"}
    close(queue)


async def test_manifest_accepts_harbor_and_gdpevo_layouts(tmp_path):
    from evolution.judge_queue import ingest_manifest

    entries = []
    for identity, layout in [
        ("harbor", "harbor/agent/trace.jsonl"),
        ("gdpevo", "gdpevo/trajectory.jsonl"),
    ]:
        path = tmp_path / layout
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"kind": "instruction", "text": identity}))
        entries.append(
            {
                "rollout_id": identity,
                "path": layout,
                "metadata": {
                    "oracle_label": 1,
                    "task": "PRIVATE_METADATA_CANARY",
                },
                "execution": {"status": "failed"},
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"trajectories": entries}))
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    queue = queue_at(tmp_path / "queue.sqlite", handler)
    ids = ingest_manifest(queue, manifest, tmp_path / "out", judges=["a1"])
    assert len(ids) == 2
    await queue.run()
    assert {r["rollout"] for r in queue.rows()} == {"harbor", "gdpevo"}
    assert "PRIVATE_METADATA_CANARY" not in json.dumps(payloads)
    assert "oracle_label" not in json.dumps(payloads)
    assert (
        ingest_manifest(queue, manifest, tmp_path / "out", judges=["a1"])
        == ids
    )
    await queue.run()
    assert len(payloads) == 2
    close(queue)


async def test_accounted_queue_reserves_once_and_hashes_exact_wire_prompt(
    tmp_path,
):
    from evolution.accounting import AccountedBackend
    from evolution.sanitize import canonical, digest
    from harness.ledger import CallTags

    payloads, backends, reservations = [], [], []
    root = Path(__file__).resolve().parents[1]

    async def dispatch(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        intents = [
            json.loads(s)
            for s in (tmp_path / "audit.jsonl").read_text().splitlines()
        ]
        intent = intents[-1]
        wire_hash = digest(canonical(payload["messages"]))
        assert wire_hash == intent["prompt_sha256"]
        assert wire_hash == queue.rows()[0]["prompt_hash"]
        for directory in (intent["archive"], intent["backend_raw_dir"]):
            assert (
                json.loads((Path(directory) / "request.json").read_text())
                == payload
            )
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert "Context identifier" not in json.dumps(payload)
        assert len(payload["user"]) == 64
        return httpx.Response(
            200,
            json=response(
                '{"invalid":true}'
                if len(payloads) == 1
                else '{"score":0.6,"rationale":"Visible evidence"}',
            ),
        )

    def factory(identity, attempt):
        backend = AccountedBackend(
            base_url="https://example.test/v1",
            api_key="mock-key",
            model="gpt-5-mini",
            ledger=tmp_path / "ledger.jsonl",
            logs_dir=tmp_path / "calls" / identity / str(attempt),
            max_retries=0,
            budget_usd=1,
            prices_path=root / "costs/judges_prices.json",
            guard_path=tmp_path / "budget.sqlite",
            limiter_path=tmp_path / "limiter.sqlite",
            audit_path=tmp_path / "audit.jsonl",
            tags=CallTags(run_id="test", task=identity, role="judge"),
            scope=identity,
            transport=httpx.MockTransport(dispatch),
        )

        async def acquire(tokens):
            reservations.append(tokens)
            return 0

        backend.limiter.acquire = acquire
        backends.append(backend)
        return backend

    queue = JudgeQueue(
        tmp_path / "queue.sqlite",
        factory,
        TokenBucketLimiter(),
        limiter_owner="transport",
    )

    async def forbidden_reservation(tokens):
        pytest.fail("The queue must not reserve transport-owned quota")

    queue.limiter.acquire = forbidden_reservation
    queue.enqueue("rollout", "a1", evidence())
    try:
        assert await queue.run() == {"done": 1}
        assert len(payloads) == len(reservations) == 2
        assert payloads[0]["messages"] == payloads[1]["messages"]
        assert payloads[0]["user"] != payloads[1]["user"]
        result = json.loads(queue.rows()[0]["result"])
        assert result["prompt_sha256"] == queue.rows()[0]["prompt_hash"]
    finally:
        close(queue)
        for backend in backends:
            backend.close()


def test_plain_endpoint_cache_metadata_is_outside_prompt(tmp_path):
    from evolution.judge_queue import Endpoint

    endpoint = Endpoint(
        "JUDGE",
        {
            "JUDGE_API_BASE": "https://example.test/v1",
            "JUDGE_API_KEY": "mock-key",
            "JUDGE_MODEL": "gpt-5-mini",
        },
        tmp_path,
        tmp_path / "budget.json",
    )
    one, two = endpoint.backend("rollout", 1), endpoint.backend("rollout", 2)
    assert one.params["user"] != two.params["user"]
    assert len(one.params["user"]) == 64
    assert "user" not in endpoint.signature["params"]


async def test_v3_preflight_backend_and_full_minute_boundaries():
    from evolution.judge_queue import preflight_prompt

    prompt = "x" * (200000 - 256)
    report = preflight_prompt(prompt, max_tokens=2048, tpm=202048)
    assert report["backend_input_reservation"] == 200000
    assert report["reserved_tokens"] == 202048
    clock = Clock()
    limiter = TokenBucketLimiter(tpm=202048, clock=clock, sleep=clock.sleep)
    assert await limiter.acquire(report["reserved_tokens"]) == 0
    assert await limiter.acquire(report["reserved_tokens"]) == pytest.approx(
        60
    )
    limiter.close()
    with pytest.raises(ValueError, match="backend limit after caps"):
        preflight_prompt(prompt + "x", tpm=1000000)
    with pytest.raises(ValueError, match="one minute"):
        preflight_prompt(prompt, max_tokens=2048, tpm=202047)


def test_v3_queue_rejects_backend_overflow_before_insertion(tmp_path):
    queue = queue_at(
        tmp_path / "queue.sqlite", lambda r: pytest.fail("No HTTP")
    )
    large = JudgeInput("x" * 200000, sanitize([]).trajectory)
    with pytest.raises(ValueError, match="backend limit after caps"):
        queue.enqueue("too-large", "a1", large)
    assert queue.rows() == []
    close(queue)


def test_v3_queue_rejects_mixed_version_on_enqueue(tmp_path):
    from types import SimpleNamespace

    queue = queue_at(
        tmp_path / "queue.sqlite", lambda r: pytest.fail("No HTTP")
    )
    old = SimpleNamespace(
        trajectory=SimpleNamespace(version="sanitized-trajectory-v2")
    )
    with pytest.raises(ValueError, match="Mixed evidence versions"):
        queue.enqueue("legacy", "a1", old)
    assert queue.rows() == []
    close(queue)


def test_v3_queue_freezes_cap_parameters_across_rollouts_and_resume(tmp_path):
    path = tmp_path / "queue.sqlite"

    def handler(request):
        pytest.fail("No HTTP")

    queue = queue_at(path, handler)
    queue.enqueue("one", "a1", evidence())
    close(queue)
    queue = queue_at(path, handler)
    changed = JudgeInput(
        "Task", sanitize([], observation_chars=2000).trajectory
    )
    with pytest.raises(ValueError, match="Mixed evidence cap parameters"):
        queue.enqueue("two", "a1", changed)
    assert len(queue.rows()) == 1
    close(queue)


def test_v3_envelope_at_total_cap_still_gets_wire_preflight(tmp_path):
    from evolution.judge_queue import preflight_prompt
    from evolution.judges import build_prompt

    events = [{"kind": "instruction", "text": "Task"}] + [
        {"kind": "observation", "stdout": "x" * 8000} for _ in range(30)
    ]
    capped = JudgeInput("Task", sanitize(events).trajectory)
    # The trajectory cap includes its envelope, but the judge prompt adds
    # wording and task context. Reject only after measuring that actual input.
    with pytest.raises(ValueError, match="backend limit after caps"):
        preflight_prompt(build_prompt("a2", capped))
    smaller = JudgeInput(
        "Task", sanitize(events, trajectory_chars=190000).trajectory
    )
    assert (
        preflight_prompt(build_prompt("a2", smaller))["reserved_tokens"]
        < 250000
    )
