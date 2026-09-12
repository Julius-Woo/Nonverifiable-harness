"""Fail-closed recovery and provenance regressions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolution.candidates import atomic_json
from evolution.evaluation import Evaluator
from evolution.grading import IsolatedTrial
from evolution.manifest import defaults, entry_errors, file_hash
from evolution.state import State


@pytest.mark.parametrize("calls,expected_retries", [(1, 1), (2, 0)])
async def test_api_infrastructure_rule_requires_only_one_call(
    tmp_path, calls, expected_retries
):
    evaluator = object.__new__(Evaluator)
    evaluator.private = tmp_path / "oracle"
    evaluator.config = {"api_timeout_policy": "infrastructure"}
    evaluator.state = State(tmp_path / "state.sqlite")
    spec = {"task": "task", "partition": "search"}
    identity = evaluator.state.schedule(spec)
    attempts = []

    async def once(candidate, current):
        attempts.append(current)
        return {
            "id": identity,
            **current,
            "status": "complete",
            "execution": {
                "api_timeout": True,
                "calls": calls,
                "action_executed": False,
            },
            "oracle": None,
            "score": None,
        }

    evaluator._one_once = once
    await evaluator.one(Path("candidate"), spec)
    assert len(attempts) == 1 + expected_retries
    evaluator.state.close()


async def test_interrupted_grading_never_repeats_completed_solver(
    tmp_path, monkeypatch
):
    import evolution.grading as grading

    evaluator = object.__new__(Evaluator)
    evaluator.root = tmp_path
    evaluator.private = tmp_path / "oracle"
    evaluator.logs = tmp_path / "logs"
    evaluator.config = {}
    evaluator.state = State(tmp_path / "state.sqlite")
    spec = {"task": "task", "partition": "search"}
    identity = evaluator.state.schedule(spec)
    atomic_json(
        evaluator.private / "grading" / identity / "boundary.json",
        {"snapshot_ready": True},
    )
    calls = []

    async def once(candidate, spec):
        calls.append("solver-state-read")
        return {
            "id": identity,
            **spec,
            "status": "interrupted",
            "oracle": None,
        }

    async def resume(root, config):
        calls.append("frozen-grader-only")

    evaluator._one_once = once
    evaluator.collect = lambda identity, spec: {
        "id": identity,
        **spec,
        "oracle": 1,
        "status": "complete",
    }
    monkeypatch.setattr(grading, "resume_frozen_grading", resume)
    row = await evaluator.one(Path("candidate"), spec)
    assert calls == ["solver-state-read", "frozen-grader-only"]
    assert row["oracle"] == 1 and row["solver_retried"] is False
    evaluator.state.close()


async def test_grader_crash_retries_same_live_artifact(tmp_path, monkeypatch):
    from harbor.environments.capabilities import EnvironmentCapabilities
    from harbor.models.task.config import TaskOS

    import evolution.grading as grading

    calls = []

    def docker(*args, **kwargs):
        assert args[0] not in {"pause", "commit", "kill"}
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(
                stdout=json.dumps([{"Config": {"Labels": {}}}])
            )
        return SimpleNamespace(stdout=json.dumps([{"Image": "original"}]))

    class Runtime:
        def __init__(self, container, image, *args, **kwargs):
            calls.append((container, image))

        async def start(self):
            return {"live_state": True}

        async def quiesce(self):
            calls.append("drained")

        async def close(self):
            calls.append("released")

    class Verifier:
        count = 0

        def __init__(self, **kwargs):
            calls.append(kwargs["environment"].name)

        async def verify(self):
            Verifier.count += 1
            if Verifier.count == 1:
                raise RuntimeError("simulated verifier crash")
            return SimpleNamespace(
                model_dump=lambda **kw: {"rewards": {"reward": 0}}
            )

    async def execute(*args, **kwargs):
        return SimpleNamespace(return_code=0)

    monkeypatch.setattr(grading, "docker", docker)
    monkeypatch.setattr(grading, "LiveRuntime", Runtime)
    monkeypatch.setattr(grading, "Verifier", Verifier)
    monkeypatch.setattr(grading.FrozenEnvironment, "exec", execute)

    async def compose(args):
        return SimpleNamespace(stdout="live-solver")

    trial = object.__new__(IsolatedTrial)
    trial._result = None
    trial.agent_environment = SimpleNamespace(
        _run_docker_compose_command=compose,
        os=TaskOS.LINUX,
        capabilities=EnvironmentCapabilities(mounted=True),
    )
    trial.config = SimpleNamespace(
        task=SimpleNamespace(path=Path("task")),
        trial_name="one",
        verifier=SimpleNamespace(env={}),
    )
    trial.task = SimpleNamespace(
        paths=SimpleNamespace(tests_dir=tmp_path / "tests")
    )
    trial.logger = None
    trial.paths = SimpleNamespace(verifier_dir=tmp_path / "verifier")
    trial.paths.verifier_dir.mkdir()
    trial.grading_directory = lambda: tmp_path / "private"
    await trial._run_shared_verifier(timeout_sec=1, user="root")
    assert calls == [
        ("live-solver", "original"),
        "live-solver",
        "drained",
        "live-solver",
        "released",
    ]
    record = json.loads((tmp_path / "private/boundary.json").read_text())
    assert [r["status"] for r in record["attempts"]] == ["failed", "complete"]
    assert record["complete"]


def test_truthy_tau_metadata_is_not_calibration_evidence(tmp_path):
    (tmp_path / "PREREG.md").write_text("Frozen")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/tb2_split.json").write_text("{}")
    value = defaults("pilot")
    value["tau"]["A1"] = 0.03
    value["tau_evidence"]["A1"] = "claimed calibrated"
    errors = entry_errors(tmp_path, value)
    assert any("A1 lacks valid frozen v2 tau" in e for e in errors)


def test_tau_evidence_requires_five_actual_aggregate_scores(tmp_path):
    from evolution.sanitize import VERSION

    (tmp_path / "PREREG.md").write_text("Frozen")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/tb2_split.json").write_text("{}")
    value = defaults("pilot")
    value["hashes"] = {"judge_prompts": "prompt", "seed": "seed"}
    value["providers"] = {
        "judge": {"model": "judge"},
        "task": {"model": "task"},
    }
    value["split_sha256"] = file_hash(tmp_path / "data/tb2_split.json")
    value["tau"]["A1"] = 0.0
    artifact = tmp_path / "tau.json"
    atomic_json(
        artifact,
        {
            "evidence_version": VERSION,
            "judge": "A1",
            "aggregate_scores": [0.5] * 4,
            "prompt_sha256": "prompt",
            "seed_sha256": "seed",
            "provider": value["providers"]["judge"],
            "task_provider": value["providers"]["task"],
            "split_sha256": value["split_sha256"],
        },
    )
    value["tau_evidence"]["A1"] = {
        "path": "tau.json",
        "sha256": file_hash(artifact),
    }
    assert any(
        "A1 lacks valid frozen v2 tau" in e
        for e in entry_errors(tmp_path, value)
    )
    data = json.loads(artifact.read_text())
    data["aggregate_scores"].append(0.5)
    atomic_json(artifact, data)
    value["tau_evidence"]["A1"]["sha256"] = file_hash(artifact)
    assert not any(
        "A1 lacks valid frozen v2 tau" in e
        for e in entry_errors(tmp_path, value)
    )


async def test_known_partial_503_cost_survives_reporting_and_repair(tmp_path):
    import httpx
    from test_evolution_followup import backend, response

    from evolution.accounting import cost_summary
    from evolution.reconcile import reconcile
    from harness.ledger import CallTags

    replies = [
        httpx.Response(
            503,
            json={
                "model": "gpt56terra",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
                "error": {"message": "temporary service error"},
            },
        ),
        response(),
    ]
    b = backend(tmp_path, httpx.MockTransport(lambda request: replies.pop(0)))
    failed = await b.complete("First", CallTags())
    success = await b.complete("Retry", CallTags())
    assert not failed.record["ok"] and success.record["ok"]
    b.close()
    summary = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    assert summary["known_usd"] == pytest.approx(
        2 * success.record["cost_usd"]
    )
    assert summary["unresolved_requests"] == 1
    assert summary["reserved_unresolved_usd"] > 0
    assert summary["tokens"]["input_tokens"] == 200
    report = reconcile(tmp_path)
    assert not report["complete"]
    assert (
        len(json.loads((tmp_path / "reconciled_ledger.json").read_text())) == 2
    )


async def test_shared_budget_reports_independent_seed_costs(tmp_path):
    import httpx
    from test_evolution_followup import backend, response

    from evolution.accounting import cost_summary
    from harness.ledger import CallTags

    for seed in ("seed1", "seed2"):
        b = backend(tmp_path, httpx.MockTransport(lambda request: response()))
        b.tags = CallTags(run_id=seed, arm="A0")
        await b.complete("One seed", CallTags())
        b.close()
    first = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl", run_id="seed1"
    )
    total = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    assert first["calls"] == 1 and total["calls"] == 2
    assert first["known_usd"] * 2 == pytest.approx(total["known_usd"])


def test_budget_database_must_match_frozen_condition(tmp_path):
    from evolution.accounting import PhaseGuard
    from evolution.manifest import budget_errors, defaults

    manifest = defaults("budget", validation=True)
    path = tmp_path / "costs/budget/budget.sqlite"
    assert budget_errors(tmp_path, manifest) == ["Budget guard is not armed"]
    guard = PhaseGuard(path, estimate=15, ceiling=15, hours=3.5)
    assert budget_errors(tmp_path, manifest) == []
    guard.db.execute("UPDATE phase SET ceiling=600")
    guard.db.commit()
    assert budget_errors(tmp_path, manifest)
    guard.close()


@pytest.mark.parametrize("item", [True, "approved", {"path": "missing"}])
def test_cache_gate_rejects_truthy_unverified_attestation(tmp_path, item):
    from evolution.manifest import defaults, valid_cache_attestation

    manifest = defaults("cache", validation=True)
    manifest["cache_partition_attestation"] = item
    assert not valid_cache_attestation(tmp_path, manifest)


def test_reconcile_retains_orphan_and_damaged_row(tmp_path):
    from evolution.accounting import PhaseGuard
    from evolution.reconcile import reconcile

    guard = PhaseGuard(tmp_path / "budget.sqlite")
    guard.reserve("orphan", "session", 0.12, 1)
    guard.close()
    audit = tmp_path / "requests.jsonl"
    audit.write_text('{"event":"request_intent","id":')
    report = reconcile(tmp_path)
    assert not report["complete"]
    assert {r["reason"] for r in report["unresolved"]} == {
        "damaged_jsonl_row",
        "reservation_or_receipt_without_intent",
    }
    effective = json.loads((tmp_path / "reconciled_ledger.json").read_text())
    assert len(effective) == 1
    assert effective[0]["request_id"] == "orphan"
    assert effective[0]["reserved_usd"] == 0.12
    assert not effective[0]["has_intent"]
    assert audit.read_text() == '{"event":"request_intent","id":'


def test_distinct_ids_cannot_overwrite_one_allocation_slot():
    from evolution.evaluation import aggregate

    rows = [
        {"id": name, "task": "one", "replicate": 0, "oracle": 1}
        for name in ("a", "b")
    ]
    with pytest.raises(ValueError, match="Duplicate fixed-allocation"):
        aggregate(rows, expected=[(1, "one", 0)])


def test_pilot_main_blocks_before_loop_or_paid_backend(tmp_path, monkeypatch):
    import sys

    from evolution.manifest import defaults
    from scripts import run_pilot

    manifest = defaults("blocked")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(manifest))
    (tmp_path / "PREREG.md").write_text("must not be treated as frozen")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/tb2_split.json").write_text("{}")
    monkeypatch.setattr(run_pilot, "__file__", str(tmp_path / "scripts/x.py"))
    monkeypatch.setattr(run_pilot, "ensure_image", lambda root: None)
    monkeypatch.setattr(run_pilot, "resolve", lambda *args: manifest)

    def forbidden(*args, **kwargs):
        raise AssertionError("No paid loop may be constructed before gates")

    monkeypatch.setattr(run_pilot, "EvolutionLoop", forbidden)
    monkeypatch.setattr(sys, "argv", ["run_pilot", "--manifest", str(path)])
    assert run_pilot.main() == 2
    report = json.loads(
        (tmp_path / "logs/evolution/blocked/entry_gates.json").read_text()
    )
    assert not report["passed"] and report["paid_calls"] == 0


def test_tau_evidence_is_bound_to_provider_and_condition(tmp_path):
    import statistics

    from evolution.manifest import defaults, entry_errors, file_hash

    (tmp_path / "PREREG.md").write_text("Draft")
    (tmp_path / "data").mkdir()
    split = tmp_path / "data/tb2_split.json"
    split.write_text("{}")
    manifest = defaults("tau", validation=True)
    manifest["providers"] = {
        "judge": {"deployment": "judge-one"},
        "task": {"deployment": "task-one"},
    }
    manifest["hashes"] = {"judge_prompts": "prompt", "seed": "seed"}
    manifest["split_sha256"] = file_hash(split)
    scores = [0.2, 0.3, 0.4, 0.3, 0.2]
    manifest["tau"]["A1"] = statistics.stdev(scores)
    path = tmp_path / "tau.json"
    path.write_text(
        json.dumps(
            {
                "evidence_version": manifest["evidence_version"],
                "judge": "A1",
                "aggregate_scores": scores,
                "prompt_sha256": "prompt",
                "seed_sha256": "seed",
                "split_sha256": manifest["split_sha256"],
                "provider": manifest["providers"]["judge"],
                "task_provider": manifest["providers"]["task"],
            }
        )
    )
    manifest["tau_evidence"]["A1"] = {
        "path": "tau.json",
        "sha256": file_hash(path),
    }
    assert not any(
        "tau evidence" in e for e in entry_errors(tmp_path, manifest)
    )
    manifest["providers"]["judge"]["deployment"] = "judge-two"
    assert any("tau evidence" in e for e in entry_errors(tmp_path, manifest))


async def test_receipt_cost_survives_damaged_ledger_without_repair(tmp_path):
    import httpx
    from test_evolution_followup import backend, response

    from evolution.accounting import cost_summary
    from harness.ledger import CallTags

    b = backend(tmp_path, httpx.MockTransport(lambda request: response()))
    reply = await b.complete("Durable cost", CallTags())
    b.close()
    (tmp_path / "ledger.jsonl").write_text('{"partial":')
    result = cost_summary(
        tmp_path / "ledger.jsonl", tmp_path / "requests.jsonl"
    )
    assert result["damaged_ledger_rows"] == 1
    assert result["known_usd"] == pytest.approx(reply.record["cost_usd"])
