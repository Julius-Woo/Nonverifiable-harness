"""Evolution contracts, meaningful failure paths, and a mock full iteration."""

import json
from pathlib import Path

import httpx
import pytest

from evolution.accounting import (
    AccountedBackend,
    BudgetHalt,
    PhaseGuard,
    cost_summary,
)
from evolution.candidates import (
    Manifest,
    copy_seed,
    freeze,
    scan_source,
    source_hash,
    working_copy,
)
from evolution.evaluation import aggregate, oracle_label
from evolution.loop import EvolutionLoop, acceptance_decision, select_control
from evolution.prompts import SOURCES, TEMPLATE, render
from evolution.state import State
from harness.ledger import CallTags

ROOT = Path(__file__).resolve().parents[1]


def candidate(tmp_path):
    work = copy_seed(ROOT / "harness", tmp_path / "work")
    manifest = Manifest(
        "c1", "seed", "A1", 1, "session1", "description", source_hash(work)
    )
    manifest.write(work)
    return work, manifest


def test_manifest_freeze_and_full_copy_rollback(tmp_path):
    work, manifest = candidate(tmp_path)
    frozen = tmp_path / "accepted"
    freeze(work, frozen, manifest)
    assert Manifest.read(frozen) == manifest
    copy = working_copy(frozen, tmp_path / "new")
    (copy / "harness/seed.py").write_text("# Changed working state\n")
    assert source_hash(frozen) == manifest.source_sha256
    assert not (frozen.stat().st_mode & 0o222)
    with pytest.raises(ValueError):
        Manifest("../escape", "seed", "A1", 1, "session", "", "0" * 64)
    with pytest.raises(ValueError):
        Manifest("c", "seed", "A1", -1, "session", "", "0" * 64)


@pytest.mark.parametrize(
    "leak",
    [
        'open("/tests/test_outputs.py")',
        'x = "bn-fit-modify"',
        'x = "L3Rlc3RzL3Rlc3Rfb3V0cHV0cy5weQ=="',
        'x = "\\x2ftests\\x2ftest_outputs.py"',
        'x = "reference_output"',
    ],
)
def test_forbidden_references(tmp_path, leak):
    work, _ = candidate(tmp_path)
    path = work / "harness/seed.py"
    assert scan_source(work, ROOT / "harness", ["bn-fit-modify"]) == []
    path.write_text(path.read_text() + "\n" + leak + "\n")
    assert scan_source(work, ROOT / "harness", ["bn-fit-modify"])


def test_candidate_symlinks_extra_files_and_plumbing_rejected(tmp_path):
    work, _ = candidate(tmp_path)
    (work / "secret").symlink_to(ROOT / ".env")
    (work / "harness/openai_api.py").write_text("pass\n")
    errors = scan_source(work, ROOT / "harness")
    assert "unsupported_file:secret" in errors
    assert "immutable_plumbing_changed:harness/openai_api.py" in errors


def test_prompt_only_score_source_varies():
    for arm, source in SOURCES.items():
        assert render(arm).replace(source, "{score_source}") == TEMPLATE
        assert not any(
            word in render(arm).lower()
            for word in ("oracle", "tests", "optimized")
        )


def test_acceptance_wiring_and_original_rule():
    assert acceptance_decision("A1", 0.3, 0.4, 0.5, 0.5, tau=0.02)
    assert not acceptance_decision("A1", 0.3, 0.31, 0.5, 1, tau=0.02)
    assert not acceptance_decision("A1", 0.3, 0.8, 0.5, 0.49, tau=0.02)
    assert not acceptance_decision("A1", 0.3, None, 0.5, 0.5)
    assert acceptance_decision("A1", 0.3, 0.301, rule="improve")
    assert not acceptance_decision("A1", 0.3, 0.3, rule="improve")


def test_state_resume_never_repeats_completed_or_interrupted(tmp_path):
    path = tmp_path / "state.sqlite"
    state = State(path)
    one = state.schedule({"task": "one"})
    two = state.schedule({"task": "two"})
    three = state.schedule({"task": "three"})
    state.start(one)
    state.finish(one, {"id": one, "score": 0.8})
    state.start(two)
    state.start(three)
    state.close()
    state = State(path)
    assert state.schedule({"task": "one"}) == one
    assert state.recover(one) == "done"
    assert state.recover(two) == "done"
    assert json.loads(state.row(two)["result"])["status"] == "interrupted"
    assert state.recover(three, {"id": three, "score": 0.2}) == "done"
    assert json.loads(state.row(three)["result"])["score"] == 0.2
    with pytest.raises(ValueError):
        state.start(one)
    state.close()


def test_phase_and_session_guards_survive_restart(tmp_path):
    now = [100]
    path = tmp_path / "budget.sqlite"
    guard = PhaseGuard(
        path, estimate=2, ceiling=10, hours=1, clock=lambda: now[0]
    )
    guard.reserve("r1", "session", 1, 1.5)
    with pytest.raises(BudgetHalt):
        guard.reserve("r2", "session", 0.6, 1.5)
    guard.settle("r1", 0.4, "response")
    guard.reserve("r2", "session", 1, 1.5)
    guard.close()
    guard = PhaseGuard(path, clock=lambda: now[0])
    assert guard.limit == 3
    assert guard.used() == pytest.approx(1.4)
    with pytest.raises(BudgetHalt):
        guard.reserve("r3", "other", 2, 5)
    now[0] += 3601
    with pytest.raises(BudgetHalt):
        guard.check()
    guard.close()


async def test_intent_precedes_dispatch_and_missing_cache_is_explicit(
    tmp_path,
):
    audit = tmp_path / "requests.jsonl"
    payloads = []

    async def dispatch(request):
        events = [json.loads(s) for s in audit.read_text().splitlines()]
        assert events[-1]["event"] == "request_intent"
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "gpt56luna",
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"finish","answer":"done"}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    backend = AccountedBackend(
        base_url="https://example.test/v1",
        api_key="secret-key",
        model="gpt56luna",
        ledger=tmp_path / "ledger.jsonl",
        logs_dir=tmp_path / "calls",
        max_retries=0,
        budget_usd=1,
        prices_path=ROOT / "costs/judges_prices.json",
        guard_path=tmp_path / "budget.sqlite",
        limiter_path=tmp_path / "limiter.sqlite",
        audit_path=audit,
        tags=CallTags(run_id="exp", arm="A1"),
        scope="rollout",
        transport=httpx.MockTransport(dispatch),
        max_calls=1,
    )
    result = await backend.complete("Hello", CallTags())
    assert result.record["ok"]
    with pytest.raises(BudgetHalt):
        await backend.complete("Again", CallTags())
    summary = cost_summary(tmp_path / "ledger.jsonl", audit)
    assert summary["unknown_ledger_calls"] == 1
    assert summary["uncached_upper_usd"] > 0
    assert summary["unresolved_requests"] == 0
    assert "secret-key" not in audit.read_text()
    backend.close()


def test_oracle_pass_uses_explicit_reward_and_timeout_rule():
    result = {"verifier_result": {"rewards": {"reward": 1}}}
    assert oracle_label(result, {"status": "finished"}, [])[0] == 1
    assert oracle_label(result, {"status": "timeout_or_cancelled"}, [])[0] == 0
    assert oracle_label(
        result, {"tool_failed": True, "status": "finished"}, []
    )[0] == 1
    assert oracle_label({}, {}, [])[0] is None


def test_control_uses_own_signal():
    rows = [
        {"id": "a", "task": "one", "score": 0.8, "oracle": 0},
        {"id": "b", "task": "one", "score": 0.4, "oracle": 1},
    ]
    assert select_control(rows, "A1")[0]["id"] == "a"
    assert aggregate(rows)["O"] == 0.5


class FakeEvaluator:
    def __init__(self, root, experiment, arm, iteration, *args, **kwargs):
        self.feedback = root / "feedback" / arm
        self.private = root / "oracle" / arm
        self.private.mkdir(parents=True, exist_ok=True)
        self.feedback.mkdir(parents=True, exist_ok=True)
        self.split = {"splits": {"search": [{"name": "task"}]}}
        self.calls = []
        self.state = args[0]
        self.iteration = iteration

    async def batch(self, candidate, partition, stage, attempts=1, **kwargs):
        self.calls.append((candidate.name, partition, stage, attempts))
        score = 0.2 if candidate.name == "seed" else 0.8
        rows = []
        start = kwargs.get("replicate_start", 0)
        for i in range(start, start + attempts):
            spec = {
                "candidate": candidate.name,
                "partition": partition,
                "stage": stage,
                "task": kwargs.get("tasks", ["task"])[0],
                "replicate": i,
                "iteration": self.iteration,
            }
            identity = self.state.schedule(spec)
            row = {
                "id": identity,
                **spec,
                "score": score,
                "oracle": 1,
                "execution": {"calls": 2, "status": "finished"},
            }
            self.state.finish(identity, row)
            rows.append(row)
        return rows


async def test_mock_end_to_end_iteration_and_resume(tmp_path, monkeypatch):
    import shutil

    shutil.copytree(
        ROOT / "harness",
        tmp_path / "harness",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / ".env").write_text("")
    loop = EvolutionLoop(
        tmp_path, "mock", "A1", evaluator_class=FakeEvaluator, tau=0.02
    )

    async def propose(parent, slot):
        work = working_copy(parent, loop.directory / f"mock-c{slot}")
        path = work / "harness/seed.py"
        path.write_text(path.read_text() + "\n# Generic revision\n")
        return {"id": work.name, "status": "valid", "candidate": str(work)}

    monkeypatch.setattr(loop, "propose", propose)
    result = await loop.run()
    assert result["accepted"]
    assert len(result["candidates"]) == 2
    assert result["J_t"] == 0.8
    assert "O_t_sealed" not in result
    private = json.loads(
        (loop.evaluator.private / "checkpoint-t1.json").read_text()
    )
    assert private["O_t_sealed"] == 1
    assert result["search_measurement_attempts"] == 1
    assert any(
        part == "search" and stage == "measurement" and attempts == 1
        for _, part, stage, attempts in loop.evaluator.calls
    )
    assert sum(part == "sealed" for _, part, _, _ in loop.evaluator.calls) == 2
    before = len(loop.evaluator.calls)
    assert await loop.run() == result
    assert len(loop.evaluator.calls) == before
    assert (
        len(
            (loop.directory / "evolution_summary.jsonl")
            .read_text()
            .splitlines()
        )
        == 1
    )
    loop.close()


async def test_control_matches_incremental_partition_budget(tmp_path):
    import shutil

    shutil.copytree(
        ROOT / "harness",
        tmp_path / "harness",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / ".env").write_text("")
    comparator = tmp_path / "A0"
    frozen_seed = copy_seed(
        tmp_path / "harness", comparator / "candidates/seed"
    )
    Manifest(
        "seed", None, "A0", 0, "seed", "seed", source_hash(frozen_seed)
    ).write(frozen_seed)
    state = State(comparator / "state.sqlite")
    state.stage("finished-1", {"status": "complete"})
    for partition, count in (("search", 3), ("anchor", 2), ("sealed", 4)):
        for i in range(count):
            state.schedule(
                {
                    "partition": partition,
                    "task": "task",
                    "iteration": 1,
                    "replicate": i,
                }
            )
    loop = EvolutionLoop(
        tmp_path, "mock", "C-TTS-A0", evaluator_class=FakeEvaluator
    )
    first = await loop.control(comparator)
    assert "rollouts" not in first
    assert json.loads(
        (loop.evaluator.private / "summary-i1.json").read_text()
    )["rollouts"] == 9
    assert first["cost_upper_usd"] == 0
    assert first["wall_s"] >= 0
    assert first["J_t"] == first["allocations"]["search"]["metrics"]["J"]
    assert loop.state.stage("finished-1") == first
    private = json.loads(
        (loop.evaluator.private / "control-t1.json").read_text()
    )
    assert private["sealed"]["metrics"]["scheduled"] == 2
    calls = len(loop.evaluator.calls)
    again = await loop.control(comparator)
    assert "rollouts" not in again
    assert len(loop.evaluator.calls) == calls
    loop.close()
    for i in range(2):
        state.schedule(
            {
                "partition": "sealed",
                "task": "task",
                "iteration": 2,
                "replicate": i,
            }
        )
    state.stage("finished-2", {"status": "complete"})
    state.close()
    # Reuse prior control records in a new iteration, dispatch only the delta.
    loop = EvolutionLoop(
        tmp_path,
        "mock",
        "C-TTS-A0",
        resume=True,
        iteration=2,
        evaluator_class=FakeEvaluator,
    )
    second = await loop.control(comparator)
    assert "rollouts" not in second
    assert json.loads(
        (loop.evaluator.private / "summary-i2.json").read_text()
    )["rollouts"] == 11
    assert loop.evaluator.calls == [
        ("seed", "sealed", "control-task-4", 1),
        ("seed", "sealed", "control-task-5", 1),
    ]
    loop.close()


async def test_rollouts_and_judgments_overlap_with_immediate_state_ingestion(
    tmp_path,
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace

    from evolution import evaluation
    from evolution.judge_queue import export_trace
    from evolution.state import State

    ready = asyncio.Event()
    state = State(tmp_path / "state.sqlite")
    config = {
        "JUDGE_MODEL": "gpt-5-mini",
        "JUDGE_API_KEY": "mock-key",
        "JUDGE_API_BASE": "https://example.test/v1",
        "JUDGING_PRICES_PATH": str(ROOT / "costs/judges_prices.json"),
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data/tb2_split.json").write_text(
        json.dumps(
            {
                "splits": {"search": [{"name": "fast"}, {"name": "slow"}]},
            }
        )
    )
    guard = PhaseGuard(tmp_path / "costs/stream/budget.sqlite")
    evaluator = evaluation.Evaluator(
        tmp_path,
        "stream",
        "A1",
        1,
        state,
        guard,
        config,
        concurrency=2,
    )
    original_finish = state.finish

    def finish(identity, row):
        original_finish(identity, row)
        if row["task"] == "fast" and row.get("score") == 0.75:
            ready.set()

    state.finish = finish

    async def one(candidate, spec):
        if spec["task"] == "slow":
            # This rollout cannot complete until the fast judge is durably
            # ingested. Batch-then-judge would deadlock here.
            await asyncio.wait_for(ready.wait(), 3)
        identity = state.schedule(spec)
        trace = tmp_path / f"{spec['task']}.jsonl"
        trace.write_text(json.dumps({"kind": "instruction", "text": "Task"}))
        exported = tmp_path / "exports" / identity
        evidence = export_trace(trace, exported)
        evidence_path = exported / "evidence.json"
        evidence_path.write_text(json.dumps(evidence.to_dict()))
        row = {
            "id": identity,
            **spec,
            "evidence": str(evidence_path),
            "score": None,
            "oracle": 0,
        }
        state.finish(identity, row)
        return row

    async def dispatch(request):
        return httpx.Response(
            200,
            json={
                "model": "gpt-5-mini",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"score":0.75,'
                                '"rationale":"Recorded evidence"}'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    original_backend = evaluation.AccountedBackend

    def backend(**kwargs):
        return original_backend(
            **kwargs, transport=httpx.MockTransport(dispatch)
        )

    evaluator.one = one
    monkeypatch.setattr(evaluation, "AccountedBackend", backend)
    monkeypatch.setattr(evaluation, "available_gib", lambda: 10)
    monkeypatch.setattr(
        evaluation, "docker", lambda *args: SimpleNamespace(stdout="")
    )
    try:
        rows = await asyncio.wait_for(
            evaluator.batch(
                tmp_path / "candidate",
                "search",
                "screen",
            ),
            5,
        )
        assert [r["task"] for r in rows] == ["fast", "slow"]
        assert all(r["score"] == 0.75 for r in rows)
        assert ready.is_set()
        for row in rows:
            assert json.loads(state.row(row["id"])["result"])["score"] == 0.75
    finally:
        state.close()
        guard.close()
