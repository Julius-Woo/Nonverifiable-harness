"""R9: v3, exact preferences, censoring, budgets and offline audit."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evolution_a3 import (
    MockOperators,
    make_loop,
    mock_propose,
)

from evolution.a3 import A3Evaluator, A3Round
from evolution.a3_control import check_retry_policy, retry_policy
from evolution.a3_manifest import require_v3, valid_a3_tau
from evolution.a3_metrics import (
    annotate_measurement,
    completion,
    measurement_report,
    metrics,
    phase_censored,
    preference_report,
)
from evolution.a3_native import native_manifest
from evolution.a3_operators import Operators, evidence, mean_preference
from evolution.a3_report import completion_report, pair_table
from evolution.accounting import BudgetHalt
from evolution.candidates import atomic_json
from evolution.evaluation import Evaluator
from evolution.judge_queue import JudgeQueue
from evolution.manifest import file_hash
from evolution.state import State


def row(identity="one", *, score=0.0, oracle=0, **kwargs):
    return {
        "id": identity,
        "task": identity,
        "replicate": 0,
        "score": score,
        "oracle": oracle,
        **kwargs,
    }


def test_exact_cancelling_ratings_and_equal_positive_totals():
    assert mean_preference([-0.3, 0.1, 0.2] + [0.0] * 7) == 0.0
    assert mean_preference([-0.3, 0.1, 0.3] + [0.0] * 7) == 0.01
    assert mean_preference([0.1] + [0.0] * 9) == 0.01
    with pytest.raises(ValueError, match="integer rating"):
        mean_preference([0.11] * 10)


@pytest.mark.parametrize("arm", ["A3-native", "A3-loop"])
@pytest.mark.parametrize("positive_tie", [False, True])
async def test_round_retains_incumbent_on_zero_and_uses_id_on_positive_tie(
    tmp_path, monkeypatch, arm, positive_tie
):
    from evolution.loop import EvolutionLoop

    monkeypatch.setattr(EvolutionLoop, "propose", mock_propose)
    loop = make_loop(tmp_path, arm)
    loop.rule = "improve"

    class TiedOperators(MockOperators):
        async def rank(self, identity, trial, reference, candidate, baseline):
            index = int(trial["task"].split("-")[-1])
            if candidate.name.endswith("c1"):
                values = [-0.3, 0.1, 0.3 if positive_tie else 0.2] + [0.0] * 15
                return values[index]
            if candidate.name.endswith("c2") and positive_tie:
                return 0.1 if index == 0 else 0.0
            return -0.2

    runner = A3Round(loop, operators=TiedOperators())

    async def core(seed):
        tasks = [f"task-{i}" for i in range(18)]
        prior = await loop.batch(seed, "search", "a3-prior", tasks=tasks)
        return {"tasks": tasks[:10], "search_tasks": tasks, "prior": prior}

    monkeypatch.setattr(runner, "coreset", core)
    try:
        result = await runner.run()
        assert result["accepted"] is positive_tie
        assert result["incumbent"] == ("i01-c1" if positive_tie else "seed")
        assert result["candidates"][0]["signed_total"] == int(positive_tie)
        assert result["candidates"][0]["raw_ratings"][:3] == [
            3,
            -1,
            -3 if positive_tie else -2,
        ]
    finally:
        loop.close()


def test_censoring_excludes_only_phase_halts_and_preserves_raw_labels():
    rows = [
        row("pass", score=0.5, oracle=1),
        row(
            "local-cap",
            score=-0.2,
            exception_info={
                "exception_message": "Projected rollout budget exceeded",
                "exception_traceback": "evolution.accounting.BudgetHalt: cap",
            },
        ),
        row(
            "phase",
            score=1.0,
            oracle=1,
            exception_info={
                "exception_message": "Projected phase API budget exceeded",
            },
        ),
    ]
    before = copy.deepcopy(rows)
    assert not phase_censored(rows[1])
    marked = annotate_measurement(rows[2])
    assert marked["oracle"] == 1 and marked["measurement_status"] == "censored"
    assert marked["execution"]["phase_halt"]
    result = metrics(rows)
    assert result["J"] is None and result["O"] is None
    assert result["censored"] == result["excluded_censored"] == 1
    assert result["uncensored_diagnostic"]["rollouts"] == 2
    assert result["uncensored_diagnostic"]["O"] == 0.5
    assert result["preferences"]["wins"] == 1
    assert result["preferences"]["losses"] == 1
    assert rows == before


def test_all_pair_statuses_and_win_rate_denominator():
    rows = [
        row("win", score=0.3),
        row("tie"),
        row("loss", score=-0.4),
        row("failed", score=None),
        row("budget", score=None, rank_budget_halt=True),
        row("censored", score=1.0, censored=True),
    ]
    report = preference_report(rows)
    assert report["status_counts"] == {
        "scored": 3,
        "unscored-budget": 1,
        "unscored-failure": 1,
        "censored": 1,
    }
    assert (report["wins"], report["ties"], report["losses"]) == (1, 1, 1)
    assert report["win_rate"] == 1 / 3
    assert report["win_rate_denominator"] == 3
    assert report["pairs"][-1]["signed_preference"] is None
    assert report["observed_signed_mean"] == -1 / 30
    assert all(r["task"] in pair_table(report) for r in rows)
    assert "| tie | 0 | 0 | 0.0 | scored | tie |" in pair_table(report)


async def test_phase_halt_fills_all_measurement_slots_without_dispatch(
    tmp_path, monkeypatch
):
    evaluator = object.__new__(A3Evaluator)
    evaluator.state = State(tmp_path / "state.sqlite")
    evaluator.logs, evaluator.private = tmp_path / "logs", tmp_path / "private"
    evaluator.split = {"splits": {"sealed": [{"name": "x"}]}}
    evaluator.spec = lambda candidate, part, stage, task, repeat: {
        "candidate": candidate.name,
        "partition": part,
        "stage": stage,
        "task": task,
        "replicate": repeat,
    }

    async def halted(self, candidate, partition, stage, attempts, **kwargs):
        spec = self.spec(candidate, partition, stage, "x", 0)
        self.state.start(self.state.schedule(spec))
        raise BudgetHalt("Projected phase API budget exceeded", phase=True)

    monkeypatch.setattr(Evaluator, "batch", halted)
    try:
        rows = await evaluator.batch(Path("seed"), "sealed", "measurement", 2)
        assert [r["measurement_status"] for r in rows] == [
            "censored",
            "unscored-budget",
        ]
        report = json.loads(
            (
                evaluator.private / "batches/measurement-seed-sealed.json"
            ).read_text()
        )
        assert report["metrics"]["O"] is None
        assert report["metrics"]["scheduled"] == 2
        assert (
            evaluator.state.db.execute(
                "SELECT count(*) FROM trials"
            ).fetchone()[0]
            == 2
        )
        assert not completion([row("search")], rows)["endpoint_eligible"]
    finally:
        evaluator.state.close()


async def test_operator_budget_halt_remains_explicit_on_resume(
    tmp_path, monkeypatch
):
    ops = Operators(
        SimpleNamespace(logs=tmp_path, iteration=1, arm="A3-native")
    )
    calls = []

    async def halt(*args):
        calls.append(True)
        raise BudgetHalt("Projected phase API budget exceeded", phase=True)

    monkeypatch.setattr(
        ops,
        "backend",
        lambda *a, **k: SimpleNamespace(
            complete=halt, tags=None, close=lambda: None
        ),
    )
    for _ in range(2):
        with pytest.raises(BudgetHalt):
            await ops.call("rank", "budget", {"task_text": "Task."})
    assert calls == [True]
    record = json.loads(
        next((ops.directory / "operators").glob("*.json")).read_text()
    )
    assert record["status"] == "unscored-budget"
    assert record["attempts"][0]["status"] == "unscored-budget"


async def test_ranked_measurement_marks_budget_and_censored_references(
    tmp_path,
):
    loop = make_loop(tmp_path, "A3-native")
    seed = loop.seed()

    async def halt(*args):
        raise BudgetHalt("Phase wall-clock limit reached", phase=True)

    runner = A3Round(loop, operators=SimpleNamespace(rank=halt))
    rows = [row("x", score=None), row("y", score=None)]
    refs = {"x": row("ref-x"), "y": row("ref-y", censored=True)}
    try:
        scored = await runner.rank_rows("measurement", rows, refs, seed, seed)
        assert [r["pair_status"] for r in scored] == [
            "unscored-budget",
            "censored",
        ]
        assert not completion(scored, [row("sealed")])["measurement_complete"]
    finally:
        loop.close()


async def test_a3_operator_and_generic_queue_refuse_historical_v2(
    tmp_path, monkeypatch
):
    from evolution.a3_v2 import JudgeInput, sanitize

    old = JudgeInput(
        "Task.",
        sanitize([{"kind": "instruction", "text": "Task."}]).trajectory,
    )
    path = tmp_path / "old.json"
    atomic_json(path, old.to_dict())
    with pytest.raises(ValueError, match="Unsupported"):
        evidence({"evidence": str(path)})
    ops = Operators(
        SimpleNamespace(logs=tmp_path, iteration=1, arm="A3-native")
    )
    monkeypatch.setattr(
        ops, "backend", lambda *a, **k: pytest.fail("No dispatch")
    )
    with pytest.raises(ValueError, match="Unsupported"):
        await ops.call(
            "diagnose", "v2", {"trajectories": [old.trajectory.to_dict()]}
        )
    queue = JudgeQueue(tmp_path / "queue.sqlite", None, None)
    try:
        with pytest.raises(ValueError, match="Mixed evidence"):
            queue.enqueue("old", "a1", old)
        queue.db.execute(
            "INSERT INTO items(id,evidence_version) VALUES('old',?)",
            (old.trajectory.version,),
        )
        queue.db.commit()
    finally:
        queue.db.close()
    with pytest.raises(ValueError, match="unsupported evidence"):
        JudgeQueue(tmp_path / "queue.sqlite", None, None)


def test_native_budget_separate_from_loop_and_azure_model_recorded():
    from scripts.run_pilot import defaults

    manifest = defaults("pilot")
    assert manifest["a3"]["embedding"] == "text-embedding-3-large"
    assert manifest["a3"]["evidence_version"] == "v3"
    loop_budget = copy.deepcopy(manifest["budget"])
    manifest["a3_native_budget"]["guard_usd"] = 55
    assert manifest["budget"] == loop_budget
    native = native_manifest("native", budget_usd=55, estimate_usd=45, hours=5)
    assert native["budget"]["guard_usd"] == 55
    assert native["budget"]["estimate_usd"] == 45
    assert native["budget"]["wall_clock_hours"] == 5
    require_v3(native)
    native["a3"]["evidence_version"] = "sanitized-trajectory-v2"
    with pytest.raises(ValueError, match="v3"):
        require_v3(native)
    with pytest.raises(ValueError, match="budget"):
        native_manifest("negative", budget_usd=-1)


async def test_native_execute_uses_manifest_budget(tmp_path, monkeypatch):
    from evolution import a3_native

    seen = []

    class Loop:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs)

        async def run(self):
            return {"mock": True}

        def close(self):
            pass

    monkeypatch.setattr(a3_native, "EvolutionLoop", Loop)
    monkeypatch.setattr(a3_native, "reconcile", lambda *a: None)
    manifest = native_manifest(
        "budget", budget_usd=55, estimate_usd=45, hours=5
    )
    await a3_native.execute(tmp_path, manifest)
    assert (seen[0]["estimate"], seen[0]["ceiling"], seen[0]["hours"]) == (
        45,
        55,
        5,
    )


def test_control_refuses_retry_policy_drift(tmp_path):
    comparator, control = (
        State(tmp_path / "left.sqlite"),
        State(tmp_path / "right.sqlite"),
    )
    comparator.stage(
        "a3-retry-policy", retry_policy({"api_timeout_policy": "failure"})
    )
    loop = SimpleNamespace(
        state=control, config={"api_timeout_policy": "infrastructure"}
    )
    try:
        with pytest.raises(
            ValueError, match="comparator infrastructure-retry"
        ):
            check_retry_policy(loop, comparator)
        assert control.stage("a3-control-allocation-policy") is None
    finally:
        comparator.close()
        control.close()


def test_v3_tau_artifact_must_match_actual_condition(tmp_path):
    value = native_manifest("tau")
    value.update(
        hashes={"a3_prompts": "prompt", "seed": "seed"},
        providers={
            "task": {"model": "task"},
            "embedding": {"deployment": "text-embedding-3-large"},
        },
        split_sha256="split",
        tau={"A3-loop": 0.0},
    )
    data = {
        "aggregate_scores": [0.2] * 5,
        "judge": "A3-loop",
        "evidence_version": "v3",
        "provider": value["providers"]["task"],
        "task_provider": value["providers"]["task"],
        "seed_sha256": "seed",
        "prompt_sha256": "prompt",
        "split_sha256": "split",
        "a3_recipe": value["a3"],
        "embedding_provider": value["providers"]["embedding"],
    }
    path = tmp_path / "tau.json"
    for version, expected in [
        ("v3", True),
        ("sanitized-trajectory-v2", False),
    ]:
        atomic_json(path, {**data, "evidence_version": version})
        value["tau_evidence"] = {
            "A3-loop": {"path": "tau.json", "sha256": file_hash(path)}
        }
        assert valid_a3_tau(tmp_path, value) is expected


def test_offline_report_preserves_eligibility_and_censoring():
    summary = {
        "experiment": "historical",
        "arm": "A3-native",
        "decision": "rejected",
        "incumbent": "seed",
        "candidates": [
            {"id": "c1", "status": "valid", "preference": None},
            {"id": "c2", "status": "invalid"},
            {"id": "c3", "status": "invalid"},
        ],
    }
    tasks = [f"t{i}" for i in range(10)]
    scores = [None, 0.0, -0.9, -0.3, 0.9, 0.7, 0.0, 0.8, -0.2, None]
    selection = {
        "c1": [row(task, score=score) for task, score in zip(tasks, scores)]
    }
    search = [
        row(f"search{i}", score=None if i < 2 else 0.0) for i in range(18)
    ]
    sealed = [row(f"sealed{i}", censored=i >= 8) for i in range(12)]
    result = completion_report(
        summary,
        selection,
        search,
        sealed,
        tasks,
        {task: "ref-" + task for task in tasks},
    )
    assert result["decision_basis"] == "eligibility-based rejection"
    assert result["optimization_complete"]
    assert (
        not result["measurement_complete"] and not result["endpoint_eligible"]
    )
    assert result["sealed_measurements"]["status_counts"]["censored"] == 4
    assert (
        result["search_preferences"]["status_counts"]["unscored-failure"] == 2
    )
    assert result["selection"]["c1"]["observed_signed_mean"] == 0.125
    assert result["selection"]["c1"]["selection_preference"] is None
    assert (
        result["selection"]["c1"]["wins"],
        result["selection"]["c1"]["ties"],
        result["selection"]["c1"]["losses"],
    ) == (3, 2, 3)
    assert result["selection"]["c2"]["unscored"] == 10
    assert all(
        "raw_oracle" not in r for r in result["sealed_measurements"]["rows"]
    )
    assert (
        measurement_report(sealed, private=True)["rows"][-1]["raw_oracle"] == 0
    )


def test_resolved_a3_manifest_hashes_actual_v3_contract(tmp_path, monkeypatch):
    from evolution import a3_manifest
    from evolution.sanitize import canonical, digest

    source = tmp_path / "evolution/sanitize.py"
    source.parent.mkdir()
    source.write_text("# Frozen v3 fixture\n")

    def resolve(root, value, config):
        assert value["a3"]["evidence_version"] == "sanitized-trajectory-v2"
        return {
            **value,
            "hashes": {"a3_evidence_contract": "old-v2"},
            "resolved_sha256": "old-hash",
        }

    monkeypatch.setattr(a3_manifest.common, "resolve", resolve)
    value = native_manifest("v3")
    result = a3_manifest.resolve(tmp_path, value, {})
    assert value["a3"]["evidence_version"] == "v3"
    assert (
        result["a3"]["evidence_version"] == result["evidence_version"] == "v3"
    )
    assert result["hashes"]["a3_evidence_contract"] == file_hash(source)
    assert result["hashes"]["evidence_contract"] == file_hash(source)
    assert result["resolved_sha256"] == digest(
        canonical({k: v for k, v in result.items() if k != "resolved_sha256"})
    )


def test_v3_entry_check_preserves_other_pilot_blocks(tmp_path, monkeypatch):
    from evolution import a3_manifest

    value = a3_manifest.defaults("blocked")
    monkeypatch.setattr(
        a3_manifest.common,
        "entry_errors",
        lambda *a: [
            "PREREG is not frozen",
            "Budget guard differs from frozen manifest",
            "A3-loop lacks valid frozen v2 tau evidence",
        ],
    )
    errors = a3_manifest.entry_errors(tmp_path, value)
    assert errors == [
        "PREREG is not frozen",
        "Budget guard differs from frozen manifest",
        "A3-loop lacks valid frozen v3 tau evidence",
    ]
    value["a3"]["evidence_version"] = "sanitized-trajectory-v2"
    assert any(
        "requires evidence v3" in e
        for e in a3_manifest.entry_errors(tmp_path, value)
    )


def test_completed_native_replay_uses_corrected_report_without_writes(
    tmp_path,
):
    from evolution.a3_native import finished_result

    directory = tmp_path / "runs/archived/A3-native"
    state = State(directory / "state.sqlite")
    state.stage("finished-1", {"decision": "rejected", "J_t": 0.1})
    state.close()
    before = (directory / "state.sqlite").read_bytes()
    report = {
        "experiment": "archived",
        "decision": "rejected",
        "J_t": 0.1,
        "optimization_complete": True,
        "measurement_complete": False,
        "endpoint_eligible": False,
        "status": "measurement-incomplete",
        "sealed_measurements": {"status_counts": {"scored": 8, "censored": 4}},
    }
    atomic_json(directory / "completion-report.json", report)
    result = finished_result(tmp_path, "archived")
    assert (
        not result["measurement_complete"] and not result["endpoint_eligible"]
    )
    assert result["sealed_measurements"]["status_counts"]["censored"] == 4
    assert (directory / "state.sqlite").read_bytes() == before


async def test_round_checkpoint_cannot_hide_censored_measurement(
    tmp_path, monkeypatch
):
    from test_evolution_a3 import vectors

    from evolution.loop import EvolutionLoop

    monkeypatch.setattr(EvolutionLoop, "propose", mock_propose)
    loop = make_loop(tmp_path, "A3-native")
    original = loop.evaluator.batch

    async def batch(candidate, partition, stage, *args, **kwargs):
        rows = await original(candidate, partition, stage, *args, **kwargs)
        if partition == "sealed":
            rows[-1]["exception_info"] = {
                "exception_message": "Projected phase API budget exceeded"
            }
        return rows

    monkeypatch.setattr(loop.evaluator, "batch", batch)
    runner = A3Round(
        loop,
        operators=MockOperators(),
        embedder=lambda texts: (vectors(len(texts)), {"model": "fixture"}),
    )
    try:
        result = await runner.run()
        assert result["optimization_complete"] and result["accepted"]
        assert result["status"] == "measurement-incomplete"
        assert not result["endpoint_eligible"]
        checkpoint = json.loads(
            (loop.evaluator.private / "checkpoint-t1.json").read_text()
        )
        assert checkpoint["O_t_sealed"] is None
        assert checkpoint["sealed_measurement"]["excluded_censored"] == 1
        assert checkpoint["measurements"]["rows"][-1]["raw_oracle"] == 1
        count = len(loop.evaluator.calls)
        assert await runner.run() == result
        assert len(loop.evaluator.calls) == count
    finally:
        loop.close()
