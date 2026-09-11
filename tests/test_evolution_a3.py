"""RHO arithmetic, evidence boundaries, replay, and real-loop integration."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolution.a3 import A3Round
from evolution.a3_control import score_control_pool
from evolution.a3_coreset import select_coreset
from evolution.a3_native import native_manifest
from evolution.a3_operators import (
    Operators,
    first_baselines,
    mean_preference,
    pair_evidence,
    signed_preference,
)
from evolution.a3_v2 import JudgeInput, sanitize
from evolution.candidates import atomic_json, source_hash, working_copy
from evolution.evaluation import aggregate
from evolution.loop import EvolutionLoop, acceptance_decision, select_control
from evolution.manifest import defaults, validate
from evolution.sanitize import canonical

ROOT = Path(__file__).resolve().parents[1]


def descriptions(n=18):
    return [
        {
            "task": f"task-{i}",
            "difficulty": 5,
            "abstract_fingerprint": "Structural invariants.",
        }
        for i in range(n)
    ]


def vectors(n=18):
    return [[float(i == j) for j in range(n)] for i in range(n)]


def test_dpp_deterministic_permutation_and_seeded_ties():
    items, embeddings = descriptions(), vectors()
    result = select_coreset(items, embeddings, seed=19)
    assert len(result) == len(set(result)) == 10
    assert result == select_coreset(items, embeddings, seed=19)
    assert result == select_coreset(items[::-1], embeddings[::-1], seed=19)
    assert result != select_coreset(items, embeddings, seed=20)
    items[0]["difficulty"] = 10
    assert select_coreset(items, embeddings)[0] == "task-0"


def test_dpp_diversity_floor_and_rank_deficiency():
    items = descriptions(3)
    items[0]["difficulty"], items[1]["difficulty"] = 10, 9
    items[2]["difficulty"] = 0
    assert select_coreset(items, [[1, 0], [1, 0], [0, 1]], k=2) == [
        "task-0",
        "task-2",
    ]
    assert len(select_coreset(items, [[1, 0]] * 3, k=3)) == 3
    with pytest.raises(ValueError):
        select_coreset(items, [[0, 0]] * 3, k=2)
    with pytest.raises(ValueError):
        select_coreset([{**x, "oracle": 1} for x in items], vectors(3), k=2)


def test_first_group_rollout_is_reference_despite_later_better_outcome():
    rows = [
        {
            "id": str(i),
            "task": "x",
            "candidate": "parent",
            "replicate": i,
            "score": i,
            "oracle": int(i > 0),
        }
        for i in (2, 0, 1)
    ]
    assert first_baselines(rows, ["x"], candidate="parent")["x"]["id"] == "0"
    with pytest.raises(ValueError):
        first_baselines(rows + [rows[1]], ["x"], candidate="parent")
    with pytest.raises(ValueError):
        first_baselines(rows, ["x"], candidate="other")


@pytest.mark.parametrize(
    "value,expected", [(-10, 1), (10, -1), (0, 0), (-3, 0.3)]
)
def test_rank_sign_and_normalization(value, expected):
    assert (
        signed_preference({"value": value, "rationale": "Evidence."})
        == expected
    )


@pytest.mark.parametrize("value", [True, 1.5, 11, float("nan"), "2"])
def test_invalid_rank_rejected(value):
    with pytest.raises(ValueError):
        signed_preference({"value": value, "rationale": "Evidence."})


def test_fixed_denominator_and_signed_acceptance():
    assert mean_preference([1, -1] + [0.5] * 8) == 0.4
    assert mean_preference([1] * 9 + [None]) is None
    assert mean_preference([1] * 9) is None
    assert acceptance_decision("A3-loop", -0.4, -0.2, 0.5, 0.5, tau=0.1)
    assert not acceptance_decision("A3-loop", -0.4, -0.2, 0.5, 0.4, tau=0.1)
    assert not acceptance_decision("A3-loop", 0, -0.1, 0.5, 0.5, tau=0)
    assert not acceptance_decision("A3-native", 0, 0, rule="improve")
    assert (
        aggregate(
            [
                {
                    "id": "x",
                    "score": 0.5,
                    "oracle": 0,
                    "scale": "signed_preference",
                }
            ]
        )["common_gap"]
        is None
    )


def write_evidence(path, *, task="Task.", answer="done", secret=False):
    records = [
        {"kind": "instruction", "text": task},
        {"kind": "observation", "step": 0, "stdout": "x" * 17000},
    ]
    if secret:
        records += [
            {
                "kind": "observation",
                "step": 1,
                "command": "cat /tests/test_outputs.py",
                "stdout": "SECRET_EXPECTATION",
            }
        ]
    records += [{"kind": "finish", "answer": answer}]
    value = JudgeInput(task, sanitize(records).trajectory).to_dict()
    atomic_json(path, value)
    return str(path)


def test_pair_contract_preserves_full_v2_and_scrubs_sources(tmp_path):
    candidate = tmp_path / "candidate"
    (candidate / "harness").mkdir(parents=True)
    (candidate / "harness/seed.py").write_text(
        "x = 1\n# oracle_result: SECRET\ny = 2\n"
    )
    row = {
        "evidence": write_evidence(tmp_path / "trace.json", secret=True),
        "oracle": 1,
        "anchor": "ANCHOR_SECRET",
        "result": "SECRET_RESULT",
    }
    payload = pair_evidence(row, row, candidate, candidate)
    wire = canonical(payload)
    assert "x" * 17000 in wire
    assert all(
        secret not in wire
        for secret in (
            "SECRET_EXPECTATION",
            "ANCHOR_SECRET",
            "SECRET_RESULT",
            "oracle_result",
        )
    )
    assert "x = 1" in wire and "y = 2" in wire


class MockEvaluator:
    def __init__(
        self, root, experiment, arm, iteration, state, guard, config, **kwargs
    ):
        self.root, self.arm, self.iteration, self.state = (
            root,
            arm,
            iteration,
            state,
        )
        self.logs = root / "logs" / experiment / arm
        self.private = root / "oracle" / experiment / arm
        self.feedback = root / "feedback" / arm / experiment
        self.jobs = self.private / "harbor"
        for path in (self.logs, self.private, self.feedback, self.jobs):
            path.mkdir(parents=True, exist_ok=True)
        self.split = {
            "splits": {
                "search": [{"name": f"task-{i}"} for i in range(18)],
                "anchor": [{"name": "anchor-secret"}],
                "sealed": [{"name": "sealed-secret"}],
            }
        }
        self.calls = []

    async def batch(
        self, candidate, partition, stage, attempts=1, tasks=None, **kwargs
    ):
        tasks = tasks or [t["name"] for t in self.split["splits"][partition]]
        rows = []
        self.calls.append(
            (candidate.name, partition, stage, attempts, list(tasks))
        )
        for task in tasks:
            for i in range(attempts):
                spec = {
                    "iteration": self.iteration,
                    "candidate": candidate.name,
                    "partition": partition,
                    "stage": stage,
                    "task": task,
                    "replicate": i,
                }
                identity = self.state.schedule(spec)
                row = {
                    "id": identity,
                    **spec,
                    "oracle": 1,
                    "score": None,
                    "status": "complete",
                    "execution": {"status": "finished"},
                    "evidence": write_evidence(
                        self.logs / f"{identity}.json", task=task
                    ),
                }
                self.state.finish(identity, row)
                rows.append(row)
        return rows

    def export_feedback(self, rows):
        pass


class MockOperators:
    def __init__(self):
        self.calls, self.pairs = [], []

    async def call(self, kind, identity, payload):
        assert "oracle" not in payload and "anchor" not in canonical(payload)
        assert "sealed" not in canonical(payload)
        self.calls.append((kind, identity))
        if kind == "difficulty":
            return {
                "difficulty": 5,
                "abstract_fingerprint": "Structural invariants.",
            }
        return {
            "severity": 0.5,
            "inconsistency_analysis": "Different plans.",
            "harness_improvement_direction": "Validate assumptions.",
        }

    async def rank(self, identity, row, reference, candidate, baseline):
        assert reference["replicate"] == 0
        self.pairs.append((row, reference, candidate.name, baseline.name))
        return 0.4 if candidate.name.endswith("c1") else -0.2


async def mock_propose(self, parent, slot):
    name = f"i{self.iteration:02}-c{slot}"
    path = self.directory / "candidates" / name
    if not path.exists():
        working_copy(parent, path)
        source = path / "harness/seed.py"
        source.write_text(source.read_text() + f"\n# Improvement {name}\n")
    return {"id": name, "candidate": str(path), "status": "valid"}


def make_loop(tmp_path, arm, *, iteration=1, resume=False):
    (tmp_path / "harness").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "harness/seed.py", tmp_path / "harness/seed.py")
    manifest = native_manifest("mock-a3")
    manifest["arms"] = [arm]
    manifest["candidates_per_arm"] = {arm: 3}
    return EvolutionLoop(
        tmp_path,
        "mock-a3",
        arm,
        manifest=manifest,
        rule="anchor" if arm == "A3-loop" else "improve",
        tau=0.1,
        evaluator_class=MockEvaluator,
        iteration=iteration,
        resume=resume,
    )


@pytest.mark.parametrize("arm", ["A3-native", "A3-loop"])
async def test_full_round_real_loop_mocked_models_and_harbor(
    tmp_path, monkeypatch, arm
):
    monkeypatch.setattr(EvolutionLoop, "propose", mock_propose)
    loop = make_loop(tmp_path, arm)
    ops = MockOperators()
    runner = A3Round(
        loop,
        operators=ops,
        embedder=lambda texts: (vectors(len(texts)), {"model": "fixture"}),
    )
    try:
        result = await runner.run()
        assert result["accepted"] and result["incumbent"] == "i01-c1"
        assert len(result["candidates"]) == 3
        assert result["coreset_preference"] == pytest.approx(0.4)
        assert result["scale"] == "signed_preference"
        assert "common_gap" not in result["search_measurement"]
        assert [kind for kind, _ in ops.calls].count("difficulty") == 18
        assert [kind for kind, _ in ops.calls].count("diagnose") == 10
        group = [c for c in loop.evaluator.calls if c[2] == "a3-group"]
        assert len(group) == 1 and group[0][3] == 3 and len(group[0][4]) == 10
        pairs = [p for p in ops.pairs if p[0]["stage"] == "a3-after"]
        assert len(pairs) == 30
        assert all(p[1]["stage"] == "a3-group" for p in pairs)
        assert len({p[1]["id"] for p in pairs}) == 10
        sealed = [c for c in loop.evaluator.calls if c[1] == "sealed"]
        assert len(sealed) == (1 if arm == "A3-native" else 2)
        if arm == "A3-native":
            assert not any(c[1] == "anchor" for c in loop.evaluator.calls)
        count = len(ops.calls) + len(ops.pairs)
        assert await runner.run() == result
        assert len(ops.calls) + len(ops.pairs) == count
    finally:
        loop.close()
    if arm == "A3-loop":
        loop = make_loop(tmp_path, arm, iteration=2, resume=True)
        ops = MockOperators()
        runner = A3Round(
            loop,
            operators=ops,
            embedder=lambda _: pytest.fail("Coreset must stay fixed"),
        )
        try:
            result = await runner.run()
            assert result["parent"] == "i01-c1"
            assert not any(kind == "difficulty" for kind, _ in ops.calls)
            assert all(
                p[3] == "i01-c1"
                for p in ops.pairs
                if p[0]["stage"] == "a3-after"
            )
            assert all(
                p[3] == "seed"
                for p in ops.pairs
                if p[0]["stage"] == "measurement"
            )
        finally:
            loop.close()


async def test_control_uses_self_preference_and_disjoint_sealed_pools(
    tmp_path,
):
    rows = [
        {
            "id": f"r{i}",
            "partition": "sealed",
            "task": "x",
            "replicate": i,
            "oracle": int(i == 2),
            "evidence": "fixture",
        }
        for i in range(6)
    ]
    seen = []

    async def rank(identity, row, reference, candidate, baseline):
        seen.append((row["replicate"], reference["replicate"]))
        return {2: -0.4, 3: 0.2, 4: 0.6, 5: -0.1}[row["replicate"]]

    ops = SimpleNamespace(rank=rank)
    loop = SimpleNamespace(
        iteration=1, evaluator=SimpleNamespace(private=tmp_path)
    )
    scored = await score_control_pool(loop, rows, tmp_path, operators=ops)
    chosen = [
        select_control(
            [r for r in scored if r["replicate"] % 2 == parity], "A3-loop"
        )[0]["id"]
        for parity in (0, 1)
    ]
    assert chosen == ["r4", "r3"]
    assert seen == [(2, 0), (3, 1), (4, 0), (5, 1)]
    assert ops.directory.is_relative_to(tmp_path)


def test_native_and_loop_manifest_constraints():
    validate(native_manifest("native"))
    value = defaults("pilot")
    validate(value)
    assert value["a3_loop_hook"] == "evolution.a3:A3Round"
    value["candidates_per_arm"]["A3-loop"] = 2
    with pytest.raises(ValueError, match="three"):
        validate(value)
    value = native_manifest("native")
    value["T"] = 2
    with pytest.raises(ValueError, match="one standalone"):
        validate(value)


async def test_operator_retry_is_durable_and_input_changes_fail(
    tmp_path, monkeypatch
):
    loop = SimpleNamespace(logs=tmp_path, iteration=1, arm="A3-native")
    ops = Operators(loop)
    calls = []

    class Backend:
        tags = None
        logs_dir = tmp_path / "calls"

        async def complete(self, prompt, tags):
            calls.append(prompt)
            return SimpleNamespace(record={"ok": True}, text='{"value": 99}')

        def close(self):
            pass

    monkeypatch.setattr(ops, "backend", lambda *a, **k: Backend())
    assert await ops.call("rank", "one", {"allowed": "fixture"}) is None
    assert await ops.call("rank", "one", {"allowed": "fixture"}) is None
    assert len(calls) == 2
    with pytest.raises(ValueError, match="changed"):
        await ops.call("rank", "one", {"allowed": "changed"})


async def test_control_full_loop_matches_physical_allocations(
    tmp_path, monkeypatch
):
    from evolution import a3_control
    from evolution.candidates import Manifest, copy_seed
    from evolution.state import State

    comparator = tmp_path / "A3-loop"
    seed = copy_seed(ROOT / "harness", comparator / "candidates/seed")
    Manifest(
        "seed", None, "A3-loop", 0, "seed", "seed", source_hash(seed)
    ).write(seed)
    state = State(comparator / "state.sqlite")
    state.stage("finished-1", {"status": "complete"})
    for partition in ("search", "anchor", "sealed"):
        for replicate in range(4):
            state.schedule(
                {
                    "iteration": 1,
                    "task": "task-0",
                    "partition": partition,
                    "replicate": replicate,
                }
            )
    state.close()

    class ControlEvaluator(MockEvaluator):
        async def batch(
            self,
            candidate,
            partition,
            stage,
            attempts=1,
            tasks=None,
            replicate_start=0,
            **kwargs,
        ):
            rows = []
            for task in tasks:
                spec = {
                    "iteration": self.iteration,
                    "candidate": candidate.name,
                    "task": task,
                    "partition": partition,
                    "stage": stage,
                    "replicate": replicate_start,
                }
                identity = self.state.schedule(spec)
                trace = self.jobs / identity / "agent/trace.jsonl"
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text(
                    json.dumps({"kind": "instruction", "text": "Task."}) + "\n"
                )
                row = {
                    "id": identity,
                    **spec,
                    "score": None,
                    "oracle": 1,
                    "evidence": write_evidence(self.logs / f"{identity}.json"),
                }
                self.state.finish(identity, row)
                rows.append(row)
            return rows

        async def score(self, rows):
            pytest.fail("A3 control must never invoke independent A1 scorer")

    class ControlOperators:
        def __init__(self, loop):
            self.directory = loop.logs

        async def rank(self, identity, row, reference, candidate, baseline):
            payload = pair_evidence(row, reference, candidate, baseline)
            assert set(payload) == {
                "task_text",
                "trajectory_A",
                "trajectory_B",
                "harness_A",
                "harness_B",
                "harness_diff_B_to_A",
            }
            return 0.5

    monkeypatch.setattr(a3_control, "Operators", ControlOperators)
    shutil.copytree(ROOT / "harness", tmp_path / "harness")
    loop = EvolutionLoop(
        tmp_path, "control", "C-TTS-A3-loop", evaluator_class=ControlEvaluator
    )
    try:
        result = await loop.control(comparator)
        assert result["rollouts"] == 12 and result["edited"] is False
        assert result["scale"] == "signed_preference"
        assert result["J_t"] == 0.5
        assert result["allocations"]["search"]["metrics"]["common_gap"] is None
        assert (
            loop.state.db.execute("SELECT count(*) FROM trials").fetchone()[0]
            == 12
        )
        assert await loop.control(comparator) == result
        assert (
            loop.state.db.execute("SELECT count(*) FROM trials").fetchone()[0]
            == 12
        )
    finally:
        loop.close()


async def test_loop_dispatches_reserved_a3_hook(tmp_path, monkeypatch):
    from evolution import a3

    loop = make_loop(tmp_path, "A3-loop")
    called = []

    class Hook:
        def __init__(self, actual):
            assert actual is loop

        async def run(self):
            called.append(True)
            return {"hook": "A3-loop"}

    monkeypatch.setattr(a3, "A3Round", Hook)
    try:
        assert await loop.run() == {"hook": "A3-loop"}
        assert called == [True]
    finally:
        loop.close()


def test_a3_exporter_stays_full_v2_during_generic_contract_migration(tmp_path):
    from evolution.a3 import A3Evaluator

    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {
                    "kind": "instruction",
                    "text": "Read the complete observation.",
                },
                {"kind": "observation", "stdout": "q" * 22000},
                {"kind": "finish", "answer": "Done."},
            ]
        )
    )
    exported = A3Evaluator.trace_exporter(trace, tmp_path / "export")
    assert exported.trajectory.version == "sanitized-trajectory-v2"
    assert "q" * 22000 in canonical(exported.to_dict())
    with pytest.raises(ValueError, match="Unsupported"):
        JudgeInput.from_dict(
            {
                "task_text": "Task.",
                "trajectory": {
                    **exported.trajectory.to_dict(),
                    "version": "v3",
                },
            }
        )


async def test_failed_best_rank_does_not_gain_acceptance(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(EvolutionLoop, "propose", mock_propose)
    loop = make_loop(tmp_path, "A3-native")

    class FailedOperators(MockOperators):
        async def rank(self, identity, row, reference, candidate, baseline):
            if candidate.name.endswith("c1"):
                return None if row["task"] == "task-0" else 1.0
            return -0.2

    # Include task-0 in the mocked coreset without changing production DPP.
    async def coreset(seed):
        tasks = [f"task-{i}" for i in range(18)]
        prior = await loop.batch(seed, "search", "a3-prior", tasks=tasks)
        return {"tasks": tasks[:10], "search_tasks": tasks, "prior": prior}

    runner = A3Round(loop, operators=FailedOperators())
    monkeypatch.setattr(runner, "coreset", coreset)
    try:
        result = await runner.run()
        assert result["candidates"][0]["preference"] is None
        assert result["accepted"] is False
        assert result["incumbent"] == "seed"
    finally:
        loop.close()


def test_operator_inherits_solver_endpoint_quota(tmp_path, monkeypatch):
    from evolution import a3_operators

    captured = []

    def backend(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(a3_operators, "AccountedBackend", backend)
    manifest = native_manifest("quota")
    loop = SimpleNamespace(
        root=tmp_path,
        logs=tmp_path,
        accounting=tmp_path,
        experiment="quota",
        arm="A3-native",
        iteration=1,
        manifest=manifest,
        config={
            **manifest,
            "providers": {"task": {"rpm": 250, "tpm": 250000}},
            "TASK_ALT2_API_BASE": "https://task.test",
            "TASK_ALT2_API_KEY": "fixture",
        },
    )
    ops = Operators(loop)
    for role in ("a3-diagnose", "a3-propose", "a3-rank"):
        ops.backend(role, role, session=role == "a3-propose")
    assert all(row["rpm"] == 250 and row["tpm"] == 250000 for row in captured)
    assert all(
        row["base_url"] == "https://task.test/openai/v1" for row in captured
    )
    assert all(row["model"] == "gpt56terra" for row in captured)


def test_rho_difficulty_digest_keeps_full_source_and_head_tail(tmp_path):
    from evolution.a3_inspection import difficulty_digest

    path = Path(write_evidence(tmp_path / "large.json", answer="FINAL_MARKER"))
    value = json.loads(path.read_text())
    before = path.read_bytes()
    result = difficulty_digest(value, budget=100)
    assert result["task_text"] == value["task_text"]
    assert "prior-trajectory middle omitted" in result["trajectory_digest"]
    assert result["digest_policy"]["original_bpe_tokens"] > 100
    assert result["digest_policy"]["bpe_budget"] == 100
    assert path.read_bytes() == before


async def test_oversized_rank_uses_full_read_only_inspection(
    tmp_path, monkeypatch
):
    from evolution import a3_inspection

    loop = SimpleNamespace(logs=tmp_path, iteration=1, arm="A3-native")
    ops = Operators(loop)
    sessions, observed = [], []

    def backend(*args, session=False):
        sessions.append(session)
        return SimpleNamespace(close=lambda: None)

    async def inspect(operators, kind, identity, payload, backend, attempt):
        observed.append(payload)
        return '{"value": -4, "rationale": "Observed improvement."}'

    monkeypatch.setattr(ops, "backend", backend)
    monkeypatch.setattr(a3_inspection, "inspect", inspect)
    payload = {"trajectory": "full" * 50000}
    result = await ops.call("rank", "large", payload)
    assert signed_preference(result) == 0.4
    assert observed == [payload] and sessions == [True]
    record = json.loads(
        next((ops.directory / "operators").glob("*.json")).read_text()
    )
    assert record["mode"] == "workspace"


@pytest.mark.parametrize("score", [2, -2, True, float("nan")])
def test_signed_improve_path_enforces_scale(score):
    with pytest.raises(ValueError):
        acceptance_decision("A3-loop", 0, score, rule="improve")


async def test_a3_recovery_cannot_dispatch_the_independent_evolver():
    from evolution.recovery import recover_sessions

    with pytest.raises(ValueError, match="A3 owns"):
        await recover_sessions(SimpleNamespace(arm="A3-loop"))


async def test_undispatched_admission_repair_preserves_evidence(
    tmp_path, monkeypatch
):
    from evolution import a3_inspection

    loop = SimpleNamespace(
        logs=tmp_path, accounting=tmp_path, iteration=1, arm="A3-native"
    )
    (tmp_path / "requests.jsonl").write_text("")
    ops = Operators(loop)
    calls, inspections = [], []

    async def complete(prompt, tags):
        calls.append(prompt)
        return SimpleNamespace(record={"ok": False}, text="")

    async def inspect(operators, kind, identity, payload, backend, attempt):
        inspections.append(payload)
        return '{"value": 0, "rationale": "Comparable performance."}'

    monkeypatch.setattr(
        ops,
        "backend",
        lambda *a, **kw: SimpleNamespace(
            complete=complete, tags=None, close=lambda: None
        ),
    )
    monkeypatch.setattr(a3_inspection, "INLINE_BYTES", 140000)
    payload = {"trajectory": "x" * 100000}
    assert await ops.call("rank", "admission", payload) is None
    assert len(calls) == 2
    ops.recover_undispatched("rank-admission")
    monkeypatch.setattr(a3_inspection, "INLINE_BYTES", 60000)
    monkeypatch.setattr(a3_inspection, "inspect", inspect)
    assert (await ops.call("rank", "admission", payload))["value"] == 0
    assert inspections == [payload]
    original = json.loads(
        (ops.directory / "admission/rank-admission.json").read_text()
    )
    assert len(original["attempts"]) == 2
    with pytest.raises(ValueError, match="unrepaired"):
        ops.recover_undispatched("rank-admission")
    # Subsequent routing changes cannot invalidate or repeat a completed call.
    monkeypatch.setattr(a3_inspection, "INLINE_BYTES", 200000)
    assert (await ops.call("rank", "admission", payload))["value"] == 0
    assert len(inspections) == 1


def test_admission_repair_refuses_even_unresolved_http_intents(tmp_path):
    loop = SimpleNamespace(
        logs=tmp_path, accounting=tmp_path, iteration=1, arm="A3-native"
    )
    ops = Operators(loop)
    atomic_json(
        ops.directory / "operators/rank-dispatched.json",
        {"complete": True, "attempts": [{"status": "failed"}]},
    )
    (tmp_path / "requests.jsonl").write_text(
        json.dumps(
            {
                "event": "request_intent",
                "arm": "A3-native",
                "iteration": 1,
                "task": "rank-dispatched",
            }
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="HTTP dispatch"):
        ops.recover_undispatched("rank-dispatched")


async def test_default_coreset_uses_azure_embedding_backend(
    tmp_path, monkeypatch
):
    from evolution import a3_embeddings

    loop = make_loop(tmp_path, "A3-native")
    loop.config.update(
        AZURE_EP8_BASE="https://fixture.test", AZURE_EP8_KEY="fixture"
    )
    observed = []

    async def embed(actual, texts):
        assert actual is loop
        observed.extend(texts)
        return vectors(len(texts)), {"model": "text-embedding-3-large"}

    monkeypatch.setattr(a3_embeddings, "embed_azure", embed)
    runner = A3Round(loop, operators=MockOperators())
    try:
        result = await runner.coreset(loop.seed())
        assert len(observed) == 18 and len(result["tasks"]) == 10
        assert result["embedding"]["model"] == "text-embedding-3-large"
        assert await runner.coreset(loop.seed()) == result
        assert len(observed) == 18
    finally:
        loop.close()


def test_native_embedding_modes_keep_the_legacy_condition_explicit():
    azure = native_manifest("azure-default")
    bge = native_manifest("paper-bge", embedding="bge")
    validate(azure)
    validate(bge)
    assert azure["a3"]["embedding"] == "text-embedding-3-large"
    assert bge["a3"]["embedding"] == "BAAI/bge-large-en-v1.5"
    with pytest.raises(ValueError, match="Unknown embedding"):
        native_manifest("unknown", embedding="unknown")
