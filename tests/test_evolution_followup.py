"""R8b regression contracts and fail-closed entry gates."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evolution.accounting import (
    AccountedBackend,
    BudgetHalt,
    PhaseGuard,
    cost_summary,
)
from evolution.evaluation import aggregate, oracle_label
from evolution.isolation import verify_matrix
from evolution.manifest import defaults, entry_errors, freeze_manifest
from evolution.reconcile import reconcile
from evolution.state import State
from harness.ledger import CallTags

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "observation",
    [
        {"kind": "observation", "error": "command timeout"},
        {"kind": "observation", "protocol_error": "bad JSON"},
        {"kind": "observation", "return_code": 2},
    ],
)
def test_ad10_recovered_observations_do_not_veto_pass(observation):
    label, raw, _ = oracle_label(
        {"verifier_result": {"rewards": {"reward": 1}}},
        {},
        [observation, {"kind": "finish", "answer": "Done"}],
    )
    assert label == raw == 1


def test_r8b2_timeout_veto_even_when_verifier_missing():
    assert oracle_label({}, {"agent_timeout": True}, [])[0] == 0
    assert oracle_label({}, {"executor_failure": True}, [])[0] == 0


def test_r8b8_equal_tasks_fixed_allocation_and_missing_judges():
    rows = [
        {"id": "a", "task": "one", "score": 1, "oracle": 1},
        {"id": "b", "task": "one", "score": None, "oracle": None},
        {"id": "c", "task": "two", "score": 0, "oracle": 0},
    ]
    result = aggregate(rows)
    assert result["O"] == 0.25
    assert result["O_observed_attempts"] == 0.5
    assert result["J"] == 0.5
    assert result["judge_missing"] == 1
    assert result["common_complete_task_blocks"] == 1
    assert result["common_gap"] == 0
    assert result["incomplete_task_blocks"] == 1
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate(rows + rows)


def test_r8b8_missing_control_slots_keep_denominator():
    row = {"id": "a", "task": "one", "replicate": 0, "score": 1, "oracle": 1}
    result = aggregate(
        [row],
        expected=[(1, "one", 0), (1, "one", 1), (1, "two", 0), (1, "two", 1)],
    )
    assert result["scheduled"] == 4
    assert result["O"] == 0.25
    assert result["oracle_excluded"] == 3


def test_r8b11_private_state_never_contains_sealed_values(tmp_path):
    state = State(tmp_path / "public.sqlite", private=tmp_path / "oracle")
    row = {
        "id": "sealed",
        "partition": "sealed",
        "raw_reward": 0.812391,
        "oracle": 1,
    }
    identity = state.schedule({"partition": "sealed", "task": "one"})
    state.finish(identity, row)
    state.stage("sealed-stage", [row])
    raw = state.db.execute("SELECT result FROM trials").fetchone()[0]
    assert "raw_reward" not in raw and ".812391" not in raw
    assert state.stage("sealed-stage") == [row]
    assert json.loads(state.row(identity)["result"]) == row
    state.close()


def test_r8b1_fixture_matrix_and_missing_evidence_fail_closed(tmp_path):
    matrix = {
        "kind": "fixture",
        "rows": [
            {"row": i, "status": "passed", "evidence": []} for i in range(1, 8)
        ],
    }
    errors = verify_matrix(tmp_path, matrix)
    assert len(errors) >= 8
    matrix["kind"] = "real_infrastructure_validation"
    matrix["rows"][0]["evidence"] = [{"path": "missing", "sha256": "0" * 64}]
    assert any("missing/changed" in e for e in verify_matrix(tmp_path, matrix))


def test_r8b3_pending_manifest_cannot_dispatch(tmp_path):
    (tmp_path / "PREREG.md").write_text("must not be treated as frozen")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/tb2_split.json").write_text("{}")
    errors = entry_errors(tmp_path, defaults("pilot"))
    assert any("PREREG" in e for e in errors)
    assert any("AD10" in e for e in errors)
    assert any("Section 5" in e for e in errors)
    assert any("Budget guard" in e for e in errors)
    assert any("A3-loop" in e for e in errors)
    assert not (tmp_path / "costs").exists()


def test_r8b9_manifest_hash_freezes_resolved_condition(tmp_path):
    value = {"experiment": "x", "provider": "one", "tau": 0.03}
    freeze_manifest(tmp_path, value)
    freeze_manifest(tmp_path, value)
    with pytest.raises(ValueError, match="condition mismatch"):
        freeze_manifest(tmp_path, {**value, "tau": 0.04})


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("tool_failure_reading", "strict"),
        ("api_timeout_policy", "failure"),
    ],
)
def test_ad10_ad13_reject_superseded_manifest_policies(field, new_value):
    m = defaults("x", validation=True)
    assert m["task_model"]["deployment"] == "gpt56terra"
    assert m["task_model"]["completion_allowance"] == 8192
    assert not m["ratifications"]["AD1"]
    m[field] = new_value
    from evolution.manifest import validate

    with pytest.raises(ValueError):
        validate(m)


def backend(tmp_path, transport, max_calls=24):
    return AccountedBackend(
        base_url="https://example.test/v1",
        api_key="SECRET",
        model="gpt56terra",
        ledger=tmp_path / "ledger.jsonl",
        logs_dir=tmp_path / "calls",
        guard_path=tmp_path / "budget.sqlite",
        limiter_path=tmp_path / "limiter.sqlite",
        audit_path=tmp_path / "requests.jsonl",
        scope="session",
        tags=CallTags(run_id="x", role="evolver"),
        max_calls=max_calls,
        prices_path=ROOT / "costs/judges_prices.json",
        max_retries=0,
        transport=transport,
        budget_usd=5,
    )


def response():
    return httpx.Response(
        200,
        json={
            "model": "gpt56terra",
            "choices": [
                {
                    "message": {
                        "content": '{"action":"finish","answer":"ok"}'
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


async def test_r8b12_call_cap_survives_receipt_before_trace_kill(tmp_path):
    calls = []

    async def send(request):
        calls.append(request)
        return response()

    first = backend(tmp_path, httpx.MockTransport(send), max_calls=1)
    await first.complete("Hello", CallTags())
    first.close()
    # No assistant trace is written. A new backend must still see the dispatch.
    resumed = backend(tmp_path, httpx.MockTransport(send), max_calls=1)
    with pytest.raises(BudgetHalt, match="cap"):
        await resumed.complete("Hello again", CallTags())
    assert len(calls) == 1
    resumed.close()


async def test_r8b10_receipt_rebuilds_lost_ledger_idempotently(tmp_path):
    b = backend(tmp_path, httpx.MockTransport(lambda request: response()))
    await b.complete("Hello", CallTags())
    b.close()
    expected = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    (tmp_path / "ledger.jsonl").unlink()
    report = reconcile(tmp_path)
    assert report["complete"] and len(report["repaired"]) == 1
    assert reconcile(tmp_path)["repaired"] == []
    actual = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    assert actual["known_usd"] == pytest.approx(expected["known_usd"])
    assert actual["tokens"]["input_tokens"] == 100


async def test_r8b10_response_before_receipt_window_recovers_cost(tmp_path):
    b = backend(tmp_path, httpx.MockTransport(lambda request: response()))
    await b.complete("Hello", CallTags())
    b.close()
    next((tmp_path / "requests").glob("*/receipt.json")).unlink()
    (tmp_path / "ledger.jsonl").unlink()
    report = reconcile(tmp_path)
    assert report["complete"] and report["receipts"] == 1
    assert len(report["repaired"]) == 1


def test_r8b10_unmatched_reservation_is_explicit(tmp_path):
    g = PhaseGuard(tmp_path / "budget.sqlite")
    g.reserve("orphan", "session", 0.1, 1)
    g.close()
    report = reconcile(tmp_path)
    assert not report["complete"]
    assert (
        report["unresolved"][0]["reason"]
        == "reservation_or_receipt_without_intent"
    )


async def test_r8b5_wire_messages_unchanged_and_exact_hash(tmp_path):
    from evolution.sanitize import canonical, digest

    seen = []

    def send(request):
        seen.append(json.loads(request.content))
        return response()

    b = backend(tmp_path, httpx.MockTransport(send))
    await b.complete("Frozen instruction", CallTags())
    b.close()
    intent = json.loads(
        (tmp_path / "requests.jsonl").read_text().splitlines()[0]
    )
    assert seen[0]["messages"] == [
        {"role": "user", "content": "Frozen instruction"}
    ]
    assert intent["prompt_sha256"] == digest(canonical(seen[0]["messages"]))


async def test_r8b1_factory_preserves_isolated_concrete_class(monkeypatch):
    from evolution.grading import IsolatedTrial

    monkeypatch.setattr(
        IsolatedTrial, "_resolve_agent_skills", lambda config: None
    )

    async def load(config):
        return SimpleNamespace(has_steps=False), "download"

    monkeypatch.setattr(IsolatedTrial, "_load_task", load)
    monkeypatch.setattr(IsolatedTrial, "__init__", lambda self, *a, **kw: None)

    async def bridge(self):
        pass

    monkeypatch.setattr(IsolatedTrial, "_create_bridge", bridge)
    result = await IsolatedTrial.create(SimpleNamespace(source_trial=None))
    assert type(result) is IsolatedTrial


@pytest.mark.parametrize("arm", ["A1", "A2"])
async def test_r8b4_r8b6_control_current_contract_out_of_order_resume(
    tmp_path, arm
):
    import shutil

    from test_evolution_loop import FakeEvaluator

    from evolution.candidates import Manifest, copy_seed, source_hash
    from evolution.judges import JudgeInput
    from evolution.loop import EvolutionLoop

    class ControlEvaluator(FakeEvaluator):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.jobs = tmp_path / "jobs"

        async def batch(self, *args, **kwargs):
            rows = await super().batch(*args, **kwargs)
            for row in rows:
                trace = self.jobs / row["id"] / "agent/trace.jsonl"
                trace.parent.mkdir(parents=True, exist_ok=True)
                events = [
                    {"kind": "instruction", "text": "A legitimate task"},
                    {
                        "kind": "observation",
                        "command": "cat /app/file",
                        "stdout": "z" * 16000,
                        "return_code": 0,
                    },
                    {"kind": "finish", "answer": "finished"},
                ]
                trace.write_text("".join(json.dumps(r) + "\n" for r in events))
            return rows

        async def score(self, rows):
            for row in rows:
                value = json.loads(Path(row["evidence"]).read_text())
                evidence = JudgeInput.from_dict(value)
                from evolution.sanitize import VERSION

                assert evidence.trajectory.version == VERSION
                if VERSION == "sanitized-trajectory-v2":
                    assert len(json.dumps(evidence.to_dict())) > 16000
                else:
                    assert evidence.trajectory.truncations
                    assert (
                        evidence.trajectory.truncations[0]["original_bytes"]
                        >= 16000
                    )
                row["score"] = 0.8
                self.state.finish(row["id"], row)
            return rows

    shutil.copytree(
        ROOT / "harness",
        tmp_path / "harness",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / ".env").write_text("")
    comparator = tmp_path / arm
    seed = copy_seed(tmp_path / "harness", comparator / "candidates/seed")
    Manifest("seed", None, arm, 0, "seed", "seed", source_hash(seed)).write(
        seed
    )
    db = State(comparator / "state.sqlite")
    db.stage("finished-1", {"status": "complete"})
    for partition in ("search", "anchor", "sealed"):
        for replicate in (0, 1):
            db.schedule(
                {
                    "task": "task",
                    "partition": partition,
                    "iteration": 1,
                    "replicate": replicate,
                }
            )
    db.close()
    loop = EvolutionLoop(
        tmp_path, "mock", f"C-TTS-{arm}", evaluator_class=ControlEvaluator
    )
    frozen = loop.seed(seed / "harness")
    # Replicate 1 finished before 0 when the earlier controller died.
    await loop.evaluator.batch(
        frozen,
        "sealed",
        "control-task-1",
        1,
        tasks=["task"],
        replicate_start=1,
    )
    loop.evaluator.calls.clear()
    result = await loop.control(comparator)
    assert result["rollouts"] == 6
    sealed_calls = [r for r in loop.evaluator.calls if r[1] == "sealed"]
    assert sealed_calls == [("seed", "sealed", "control-task-0", 1)]
    private = json.loads(
        (loop.evaluator.private / "control-t1.json").read_text()
    )
    assert private["sealed"]["pool_sizes"] == {"task": [1, 1]}
    assert private["sealed"]["missing_selection_slots"] == 0
    assert len(set(private["sealed"]["selected"])) == 2
    loop.close()


async def test_r8b7_recovery_after_previous_sealed_uses_iteration_parent(
    tmp_path, monkeypatch
):
    from evolution import recovery
    from evolution.candidates import (
        Manifest,
        copy_seed,
        source_hash,
        working_copy,
    )

    directory = tmp_path / "runs/x/A1"
    directory.mkdir(parents=True)
    seed = copy_seed(ROOT / "harness", directory / "candidates/seed")
    Manifest("seed", None, "A1", 0, "seed", "", source_hash(seed)).write(seed)
    parent = working_copy(seed, directory / "candidates/accepted-first")
    path = parent / "harness/seed.py"
    path.write_text(path.read_text() + "\n# Prior accepted change\n")
    Manifest(
        parent.name, "seed", "A1", 1, "prior", "", source_hash(parent)
    ).write(parent)
    work = working_copy(parent, directory / "working/i02-c1")
    source = work / "harness/seed.py"
    source.write_text(source.read_text() + "\n# Second iteration change\n")
    state = State(directory / "state.sqlite")
    state.stage("checkpoint-2", {"candidate": str(parent)})
    old = state.schedule(
        {"iteration": 1, "partition": "sealed", "stage": "measurement"}
    )
    state.stage(
        "proposal-i02-c1",
        {
            "status": "invalid",
            "session_id": "x-A1-i02-c1",
            "reason": (
                "Backend failed: Prompt exceeds "
                "conservative short-context limit"
            ),
        },
    )
    logs = tmp_path / "logs"
    session = logs / "sessions/i02-c1"
    session.mkdir(parents=True)
    (session / "trace.jsonl").write_text(
        json.dumps(
            {
                "kind": "assistant",
                "ok": True,
                "call_id": "old",
                "text": json.dumps(
                    {
                        "action": "write_file",
                        "path": "/candidate/harness/seed.py",
                        "content": source.read_text(),
                    }
                ),
            }
        )
        + "\n"
    )
    accounting = tmp_path / "costs"
    accounting.mkdir()
    (accounting / "requests.jsonl").write_text(
        json.dumps({"role": "task", "task": old}) + "\n"
    )

    class Workspace:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        async def canary_check(self, *args):
            return True

        async def import_check(self):
            return {"ok": True}

    monkeypatch.setattr(recovery, "Workspace", Workspace)
    guard = PhaseGuard(accounting / "budget.sqlite")
    loop = SimpleNamespace(
        root=tmp_path,
        directory=directory,
        state=state,
        logs=logs,
        accounting=accounting,
        iteration=2,
        arm="A1",
        experiment="x",
        guard=guard,
        canaries=lambda: ([], []),
        evaluator=SimpleNamespace(
            jobs=tmp_path / "jobs",
            feedback=tmp_path / "feedback",
            split={"splits": {"search": [{"name": "some-task-name"}]}},
        ),
    )
    await recovery.recover_sessions(loop)
    recovered = state.stage("proposal-i02-c1")
    assert recovered["status"] == "valid"
    assert recovered["manifest"]["parent_id"] == parent.name
    assert state.row(old) is not None
    guard.close()
    state.close()


async def test_r8b12_twenty_four_durable_dispatches_are_a_hard_cap(tmp_path):
    calls = []

    def send(request):
        calls.append(request)
        return response()

    for _ in range(24):
        b = backend(tmp_path, httpx.MockTransport(send))
        await b.complete("Same session across restarts", CallTags())
        b.close()
    b = backend(tmp_path, httpx.MockTransport(send))
    with pytest.raises(BudgetHalt, match="cap"):
        await b.complete("A forbidden 25th dispatch", CallTags())
    b.close()
    assert len(calls) == 24


async def test_r8b9_served_model_drift_persists_phase_halt(tmp_path):
    replies = [
        response(),
        httpx.Response(
            200,
            json={
                "model": "a-different-served-version",
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
        ),
    ]
    calls = []

    def send(request):
        calls.append(request)
        return replies.pop(0)

    b = backend(tmp_path, httpx.MockTransport(send))
    await b.complete("First", CallTags())
    with pytest.raises(BudgetHalt, match="version drift"):
        await b.complete("Second", CallTags())
    b.close()
    resumed = backend(tmp_path, httpx.MockTransport(send))
    with pytest.raises(BudgetHalt, match="version drift"):
        await resumed.complete("Must never dispatch", CallTags())
    resumed.close()
    assert len(calls) == 2


def test_r8b9_resolved_provider_settings_change_the_frozen_hash(monkeypatch):
    from evolution import manifest

    monkeypatch.setattr(
        manifest,
        "docker",
        lambda *a, **kw: SimpleNamespace(stdout="sha256:" + "a" * 64),
    )
    config = {
        "TASK_ALT2_API_BASE": "https://task.test",
        "TASK_ALT2_API_KEY": "key",
        "EVOLVER_API_BASE": "https://evolver.test",
        "EVOLVER_API_KEY": "key",
        "EVOLVER_MODEL": "evolver",
        "JUDGE_API_BASE": "https://judge.test",
        "JUDGE_API_KEY": "key",
        "JUDGE_MODEL": "judge",
    }
    first = manifest.resolve(ROOT, defaults("x", validation=True), config)
    second = manifest.resolve(
        ROOT,
        defaults("x", validation=True),
        {**config, "JUDGE_MAX_COMPLETION_TOKENS": "4096"},
    )
    assert first["resolved_sha256"] != second["resolved_sha256"]
    assert (
        first["providers"]["judge"]["params"]["max_completion_tokens"] == 2048
    )
    assert (
        second["providers"]["judge"]["params"]["max_completion_tokens"] == 4096
    )
    assert "API_KEY" not in json.dumps(first)
