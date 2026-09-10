"""Resume a failed evolver call without repeating completed work."""

import json

from evolution.recovery import (
    ReplayBackend,
    ReplayEnvironment,
    compact_context,
)
from harness.backends import Completion
from harness.ledger import CallTags
from harness.seed import run_seed


def test_context_projection_preserves_instruction_and_assistant_edits():
    instruction = "The same frozen instruction."
    edit = json.dumps(
        {
            "action": "write_file",
            "path": "/candidate/harness/seed.py",
            "content": "# Recorded source edit\n",
        }
    )
    history = [{"role": "user", "content": instruction}]
    for _ in range(12):
        history += [
            {"role": "assistant", "content": edit},
            {"role": "user", "content": "Visible observation " * 1000},
        ]
    prompt = "Tools\nConversation:\n" + json.dumps(history)
    projected, changes = compact_context(prompt, limit=20000)
    actual = json.loads(projected.split("Conversation:\n", 1)[1])
    assert actual[0]["content"] == instruction
    assert [m["content"] for m in actual if m["role"] == "assistant"] == [
        edit
    ] * 12
    assert changes
    assert len(json.dumps(projected).encode()) <= 20000


async def test_replay_does_not_repeat_api_calls_or_terminal_side_effects(
    tmp_path,
):
    class LiveBackend:
        calls = 0
        is_api = True

        async def complete(self, prompt, tags):
            self.calls += 1
            assert "already executed" in prompt
            return Completion(
                '{"action":"finish","answer":"done"}',
                {"ok": True, "call_id": "new", "note": ""},
            )

    class LiveEnvironment:
        calls = 0

        async def exec(self, command, timeout_sec):
            self.calls += 1
            raise AssertionError("Completed command must not execute twice")

    live, environment = LiveBackend(), LiveEnvironment()
    old = [
        {
            "text": '{"action":"terminal","command":"echo recorded"}',
            "call_id": "old",
            "ok": True,
        }
    ]
    observations = [
        {
            "command": "echo recorded",
            "stdout": "already executed",
            "stderr": "",
            "return_code": 0,
        }
    ]
    answer = await run_seed(
        "instruction",
        ReplayEnvironment(observations, environment),
        ReplayBackend(old, live),
        CallTags(),
        tmp_path / "trace.jsonl",
        max_steps=3,
    )
    assert answer == "done"
    assert live.calls == 1
    assert environment.calls == 0


def test_boundary_resume_requeues_only_undispatched_trials(tmp_path):
    from types import SimpleNamespace

    from evolution.candidates import atomic_json
    from evolution.recovery import reconcile_paused
    from evolution.state import State

    state = State(tmp_path / "state.sqlite")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    accounting = tmp_path / "costs"
    accounting.mkdir()
    spec = {"stage": "promotion", "task": "one"}
    pending = state.schedule(spec)
    completed = state.schedule({**spec, "task": "two"})
    dispatched = state.schedule({**spec, "task": "three"})
    artifact = state.schedule({**spec, "task": "four"})
    state.finish(completed, {"id": completed, "score": 0.75})
    (jobs / completed).mkdir()
    (jobs / artifact).mkdir()
    (accounting / "requests.jsonl").write_text(
        json.dumps({"role": "task", "task": dispatched}) + "\n"
    )
    atomic_json(tmp_path / "pause-next.json", {"stages": ["promotion"]})
    state.stage("batch-promotion", [{"id": pending}, {"id": completed}])
    logs = tmp_path / "logs"
    (logs / "configs").mkdir(parents=True)
    (logs / "configs" / f"{pending}.json").write_text("{}")
    (logs / "configs" / f"{pending}.stderr").write_text(
        "paused before dispatch"
    )
    loop = SimpleNamespace(
        directory=tmp_path,
        logs=logs,
        accounting=accounting,
        state=state,
        evaluator=SimpleNamespace(jobs=jobs),
    )
    reconcile_paused(loop)
    ids = {r[0] for r in state.db.execute("SELECT id FROM trials")}
    assert pending not in ids
    assert {completed, dispatched, artifact} <= ids
    assert json.loads(state.row(completed)["result"])["score"] == 0.75
    assert state.stage("batch-promotion") is None
    assert state.schedule(spec) == pending
    assert not (tmp_path / "pause-next.json").exists()
    preserved = list((logs / "paused-before-dispatch").rglob("*.stderr"))
    assert preserved[0].read_text() == "paused before dispatch"
    state.close()


def test_costs_do_not_include_other_concurrent_arms_or_iterations(tmp_path):
    from evolution.accounting import cost_summary

    events = []
    for arm, iteration, charge in [("A0", 1, 1), ("A1", 1, 2), ("A0", 2, 4)]:
        base = {
            "id": f"{arm}-{iteration}",
            "arm": arm,
            "iteration": iteration,
            "role": "task",
        }
        events += [
            {**base, "event": "request_intent", "reserved_usd": 5},
            {
                **base,
                "event": "request_response",
                "uncached_upper_usd": charge,
                "wall_s": 10,
            },
        ]
    events += [
        {
            "id": "timeout",
            "arm": "A0",
            "iteration": 1,
            "role": "evolver",
            "event": "request_intent",
            "reserved_usd": 0.5,
        },
        {
            "id": "timeout",
            "arm": "A0",
            "iteration": 1,
            "role": "evolver",
            "event": "request_unresolved",
            "wall_s": 180,
        },
    ]
    path = tmp_path / "audit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in events))
    result = cost_summary(
        tmp_path / "ledger.jsonl", path, arm="A0", iteration=1
    )
    assert result["calls"] == 2
    assert result["uncached_upper_usd"] == 1
    assert result["reserved_unresolved_usd"] == 0.5
    assert result["api_wall_s"] == 190


def test_label_correction_preserves_originals_and_never_reruns(tmp_path):
    from types import SimpleNamespace

    from evolution.candidates import atomic_json
    from evolution.recovery import reconcile_labels
    from evolution.state import State

    state = State(tmp_path / "state.sqlite")
    spec = {"partition": "search", "stage": "screen", "task": "one"}
    identity = state.schedule(spec)
    old = {
        "id": identity,
        **spec,
        "oracle": 0,
        "raw_reward": 1,
        "score": 0,
        "execution": {"tool_failed": True},
    }
    state.finish(identity, old)
    state.stage("batch-one", [old])
    jobs, logs, private, feedback = [
        tmp_path / n for n in ("jobs", "logs", "oracle", "feedback")
    ]
    atomic_json(
        jobs / identity / "result.json",
        {"verifier_result": {"rewards": {"reward": 1}}},
    )
    atomic_json(feedback / f"{identity}.json", {"score": 0})
    atomic_json(logs / "batches/screen.json", {"rows": [old], "metrics": {}})
    exported = []
    loop = SimpleNamespace(
        state=state,
        iteration=1,
        arm="A0",
        logs=logs,
        evaluator=SimpleNamespace(
            jobs=jobs,
            private=private,
            feedback=feedback,
            export_feedback=exported.extend,
        ),
    )
    reconcile_labels(loop)
    assert json.loads(state.row(identity)["result"])["oracle"] == 1
    assert state.stage("batch-one")[0]["score"] == 1
    assert exported[0]["score"] == 1
    correction = json.loads((logs / "label_correction.jsonl").read_text())
    assert correction["original"]["oracle"] == 0
    assert correction["solver_rerun"] is False
    reconcile_labels(loop)
    assert len(exported) == 1
    state.db.execute("DELETE FROM stages WHERE id LIKE 'label-rule-%'")
    state.db.commit()
    state.stage("finished-1", {"status": "complete"})
    reconcile_labels(loop)
    assert len(exported) == 1
    state.close()


def test_pause_signals_once_and_never_signals_an_orphan(tmp_path, monkeypatch):
    from evolution import trial_worker

    pause = tmp_path / "pause.json"
    pause.write_text("{}")
    signals = []
    monkeypatch.setattr(trial_worker.os, "getppid", lambda: 12345)
    monkeypatch.setattr(
        trial_worker.os, "kill", lambda *args: signals.append(args)
    )
    assert trial_worker.signal_controller_once(tmp_path, pause, 12345, "one")
    assert not trial_worker.signal_controller_once(
        tmp_path, pause, 12345, "two"
    )
    assert len(signals) == 1
    monkeypatch.setattr(trial_worker.os, "getppid", lambda: 1)
    assert not trial_worker.signal_controller_once(
        tmp_path, pause, 12345, "three"
    )
    assert not trial_worker.signal_controller_once(tmp_path, pause, 1, "four")
    assert len(signals) == 1


async def test_stable_trial_lease_waits_for_surviving_worker(tmp_path):
    import asyncio

    from evolution.trial_worker import trial_lease

    entered = asyncio.Event()
    path = tmp_path / "one-trial.lock"

    async def replacement():
        async with trial_lease(path):
            entered.set()

    async with trial_lease(path):
        task = asyncio.create_task(replacement())
        await asyncio.sleep(0.01)
        assert not entered.is_set()
    await asyncio.wait_for(task, 1)
    assert entered.is_set()


async def test_phase_deadline_cancels_inflight_call_and_retains_intent(
    tmp_path,
):
    import asyncio
    import time
    from pathlib import Path

    import httpx
    import pytest

    from evolution.accounting import AccountedBackend, BudgetHalt, cost_summary

    async def delayed(request):
        await asyncio.sleep(30)
        raise AssertionError("The phase deadline must cancel this request")

    root = Path(__file__).resolve().parents[1]
    backend = AccountedBackend(
        base_url="https://example.test/v1",
        api_key="test-secret",
        model="gpt56luna",
        ledger=tmp_path / "ledger.jsonl",
        logs_dir=tmp_path / "calls",
        max_retries=0,
        budget_usd=1,
        prices_path=root / "costs/judges_prices.json",
        guard_path=tmp_path / "budget.sqlite",
        limiter_path=tmp_path / "limiter.sqlite",
        audit_path=tmp_path / "requests.jsonl",
        tags=CallTags(run_id="deadline", arm="A1", role="task"),
        scope="one",
        transport=httpx.MockTransport(delayed),
    )
    backend.guard.db.execute(
        "UPDATE phase SET deadline=?", (time.time() + 0.5,)
    )
    backend.guard.db.commit()
    try:
        with pytest.raises(BudgetHalt, match="wall-clock"):
            await backend.complete("A short prompt", CallTags())
        summary = cost_summary(
            tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
        )
        assert summary["calls"] == 1
        assert summary["unresolved_requests"] == 1
        assert summary["reserved_unresolved_usd"] > 0
    finally:
        backend.close()


def test_phase_budget_halt_survives_reservation_rollback_and_reaches_peers(
    tmp_path,
):
    import pytest

    from evolution.accounting import BudgetHalt, PhaseGuard

    for dispatch in (False, True):
        path = tmp_path / f"budget-{dispatch}.sqlite"
        worker = PhaseGuard(path, estimate=1, ceiling=2)
        controller = PhaseGuard(path)
        try:
            worker.reserve("paid", "first", 0.5, 1)
            # A scope cap stops only that rollout; other work may continue.
            with pytest.raises(BudgetHalt, match="session/rollout"):
                worker.reserve("scope-rejected", "first", 0.6, 1)
            controller.check()
            with pytest.raises(BudgetHalt, match="phase API"):
                if dispatch:
                    worker.reserve("phase-rejected", "second", 1.1, 5)
                else:
                    worker.check(1.1)
            assert worker.used() == 0.5
            with pytest.raises(BudgetHalt, match="phase API"):
                controller.check()
            assert (
                controller.db.execute(
                    "SELECT count(*) FROM requests"
                ).fetchone()[0]
                == 1
            )
        finally:
            worker.close()
            controller.close()


async def test_controller_death_during_admission_prevents_harbor_dispatch(
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    import pytest

    from evolution import trial_worker

    owner = [42]
    created = []

    class Admission:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            owner[0] = 1

        async def __aexit__(self, *args):
            pass

    async def create(config):
        created.append(config)
        raise AssertionError("An orphan must not create a Harbor trial")

    config = SimpleNamespace(
        agent=SimpleNamespace(kwargs={"experiment": "unit", "arm": "A1"}),
        trial_name="stable-trial",
        trials_dir=tmp_path / "jobs",
    )
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setattr(
        trial_worker, "__file__", str(tmp_path / "evolution/trial_worker.py")
    )
    monkeypatch.setattr(trial_worker.sys, "argv", ["worker", str(path)])
    monkeypatch.setattr(trial_worker.os, "getppid", lambda: owner[0])
    monkeypatch.setattr(trial_worker, "HarborAdmission", Admission)
    monkeypatch.setattr(trial_worker.Trial, "create", create)
    monkeypatch.setattr(
        trial_worker.TrialConfig, "model_validate_json", lambda _: config
    )
    with pytest.raises(RuntimeError, match="Orphaned"):
        await trial_worker.main()
    assert created == []


async def test_resume_waits_for_surviving_worker_evidence_without_dispatch(
    tmp_path,
    monkeypatch,
):
    import asyncio
    import fcntl
    from types import SimpleNamespace

    from evolution.evaluation import Evaluator
    from evolution.state import State

    state = State(tmp_path / "state.sqlite")
    spec = {"arm": "A0", "task": "task", "stage": "screen"}
    identity = state.schedule(spec)
    state.start(identity)
    lease = tmp_path / "logs/evolution-trial-leases" / f"{identity}.lock"
    lease.parent.mkdir(parents=True)
    ready = []
    collected = []
    expected = {"id": identity, **spec, "evidence_exported": True}

    def collect(*args):
        collected.append(True)
        return expected if ready else None

    async def forbidden_dispatch(*args, **kwargs):
        raise AssertionError("Resume must wait, without another dispatch")

    evaluator = Evaluator.__new__(Evaluator)
    evaluator.root = tmp_path
    evaluator.state = state
    evaluator.guard = SimpleNamespace(check=lambda: None)
    evaluator.collect = collect
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_dispatch)
    try:
        with lease.open("a+") as worker:
            fcntl.flock(worker, fcntl.LOCK_EX)
            resumed = asyncio.create_task(
                evaluator.one(tmp_path / "candidate", spec)
            )
            await asyncio.sleep(0.01)
            assert not resumed.done()
            assert collected == []
            ready.append(True)
            fcntl.flock(worker, fcntl.LOCK_UN)
        assert await asyncio.wait_for(resumed, 1) == expected
        assert state.row(identity)["status"] == "done"
    finally:
        state.close()
