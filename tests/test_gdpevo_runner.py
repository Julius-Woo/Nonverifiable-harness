"""A9 denominators, durable retries, role routing and real v3 interface."""

import json

import pytest

from evolution.judge_queue import ingest_manifest
from evolution.sanitize import VERSION
from gdpevo import runner as module
from gdpevo.oracle import grade_attempt
from gdpevo.runner import (
    Runner,
    Slots,
    classify,
    defaults,
    schedule,
    task_role,
    validate,
)
from harness.seed import SeedError


def manifest():
    value = defaults("test-t2")
    value["tasks"] = {
        "search": ["013/train/001"],
        "anchor": ["008/train/001"],
        "sealed": ["017/test/001"],
    }
    value["schedule"] = [
        {"arm": arm, "seed": 1, "iteration": 0, "task": task, "replicate": 0}
        for arm in value["arms"]
        for tasks in value["tasks"].values()
        for task in tasks
    ]
    return value


def test_t1_schema_and_full_pools():
    value = defaults("full")
    validate(value)
    assert {k: len(v) for k, v in value["tasks"].items()} == {
        "search": 40,
        "sealed": 40,
        "anchor": 40,
    }
    assert task_role("008/test/003") == "anchor"
    for arm in ["A0", "A1", "A2", "A4", "A3-loop", "C-TTS-A0"]:
        value["arms"] = ["A0", arm] if arm != "A0" else ["A0"]
        validate(value)
        assert {s["arm"] for s in schedule(value)} == set(value["arms"])
    value["tasks"]["search"].append("013/test/001")
    with pytest.raises(ValueError, match="role mismatch"):
        validate(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("transport_max_retries", 1),
        ("oracle_range_policy", "legacy"),
        ("api_timeout_policy", "ignore"),
        ("docker_concurrency", 0),
    ],
)
def test_invalid_policy_rejected(field, value):
    spec = manifest()
    spec[field] = value
    with pytest.raises(ValueError):
        validate(spec)


def test_slots_write_ahead_and_restart(tmp_path):
    specs = schedule(manifest())
    slots = Slots(tmp_path, specs)
    assert sum(r["denominator"] for r in module.read_rows(slots.path)) == 6
    slots.update(
        specs[0]["rollout_id"], status="running", infrastructure_attempts=1
    )
    resumed = Slots(tmp_path, specs)
    assert resumed.rows[specs[0]["rollout_id"]]["infrastructure_attempts"] == 1
    assert len(resumed.rows) == 6
    assert len(module.read_rows(tmp_path / "rollout_events.jsonl")) == 1


@pytest.mark.parametrize(
    "reading,expected", [("executor", "finished"), ("strict", "tool_failure")]
)
def test_tool_failure_readings(reading, expected):
    value = manifest()
    value["tool_failure_reading"] = reading
    records = [
        {"kind": "observation", "command": "false", "return_code": 1},
        {"kind": "finish", "answer": "done"},
    ]
    assert classify(records, None, value)[0] == expected
    records[0]["protocol_error"] = "invalid action"
    assert classify(records, None, value)[0] == "executor_failure"
    records[0].pop("protocol_error")
    records[0]["error"] = "command timeout"
    assert classify(records, None, value)[0] == "executor_failure"


def test_timeout_policy_is_parameterized_and_limited_to_first_call():
    value = manifest()
    records = [{"kind": "assistant", "ok": False}]
    error = SeedError("Backend failed: TimeoutError")
    assert classify(records, error, value)[0] == "infrastructure_failure"
    value["api_timeout_policy"] = "failure"
    assert classify(records, error, value)[0] == "api_timeout_failure"
    value["api_timeout_policy"] = "infrastructure"
    assert classify(records * 2, error, value)[0] == "api_timeout_failure"
    assert classify([], TimeoutError(), value)[0] == "solver_timeout"


async def test_infrastructure_retry_exclusion_and_crash_resume(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    spec = manifest()
    instance = Runner(spec, {})
    calls = []

    async def failing(entry, number):
        calls.append((entry["rollout_id"], number))
        raise OSError("docker unavailable")

    instance.attempt = failing
    first, second = instance.specs[:2]
    instance.slots.update(
        second["rollout_id"], status="running", infrastructure_attempts=1
    )
    await instance.trial(first)
    await instance.trial(second)
    assert [n for i, n in calls if i == first["rollout_id"]] == [0, 1]
    assert [n for i, n in calls if i == second["rollout_id"]] == [1]
    for entry in (first, second):
        row = instance.slots.rows[entry["rollout_id"]]
        assert row["denominator"] == 1 and row["eligible_denominator"] == 0
        assert row["excluded"] and row["infrastructure_exclusions"] == 1
    await instance.trial(first)
    assert len(calls) == 3
    assert len(module.read_rows(instance.slots.path)) == 6
    instance.lock.close()


async def test_real_trajectory_layout_ingestible_v3_and_blinded_exports(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    instance = Runner(manifest(), {})

    async def attempted(spec, number):
        logs = (
            tmp_path
            / "logs/gdpevo/test-t2"
            / spec["rollout_id"]
            / f"attempt-{number}"
        )
        logs.mkdir(parents=True)
        trace = [
            {
                "kind": "instruction",
                "text": "Read the business record and write answer.json.",
            },
            {
                "kind": "assistant",
                "step": 0,
                "text": (
                    '{"action":"terminal",'
                    '"command":"cat /work/input/payloads/request.json"}'
                ),
            },
            {
                "kind": "observation",
                "step": 0,
                "command": "cat /work/input/payloads/request.json",
                "stdout": '{"request":1}',
                "return_code": 0,
            },
            {"kind": "finish", "answer": "done"},
        ]
        (logs / "trajectory.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in trace)
        )
        return {
            "status": "finished",
            "excluded": False,
            "binary": 1,
            "grader_retries": {"grader_v1": 0, "upstream": 0},
            "execution": {"status": "finished"},
        }, logs

    async def signal(*args):
        return 0.6

    instance.attempt, instance.signal = attempted, signal
    for spec in instance.specs:
        await instance.trial(spec)
    instance.exports()
    captured = []

    class Queue:
        def enqueue(self, identity, judge, evidence, repeat):
            captured.append(evidence.to_dict())
            assert evidence.trajectory.version == VERSION == "v3"
            assert evidence.trajectory.events[-1]["kind"] == "termination"
            return identity + judge

    identities = ingest_manifest(
        Queue(), instance.directory / "trajectories.json", tmp_path / "judge"
    )
    assert len(identities) == 12
    assert not any("grader_v1_score" in json.dumps(e) for e in captured)
    feedback = list((tmp_path / "feedback").rglob("*.json"))
    assert len(feedback) == 2
    public_rows = module.read_rows(instance.slots.path)
    assert all(
        "binary" not in r for r in public_rows if r["task_role"] != "search"
    )
    assert sum(r["denominator"] for r in public_rows) == 6
    instance.lock.close()


def test_grader_a9_retry_and_native_failure_cannot_erase_success(
    tmp_path, monkeypatch
):
    from gdpevo import oracle

    monkeypatch.setattr(oracle, "verify_frozen", lambda: {"tree_sha256": "x"})
    calls = []

    def grade(script, raw, timeout):
        calls.append(str(script))
        if "grader_v1" in str(script):
            return {"returncode": 0, "result": {"score": 1}, "score": 1}
        raise OSError("native grader cannot launch")

    monkeypatch.setattr(oracle, "run_grader", grade)
    result = grade_attempt(13, "train", "001", b"{}", tmp_path / "grade")
    assert result["binary"] == 1 and not result["excluded"]
    assert result["native_grader_failed"]
    assert result["grader_retries"] == {"grader_v1": 0, "upstream": 1}
    assert len(calls) == 3
    assert (
        grade_attempt(13, "train", "001", b"{}", tmp_path / "grade") == result
    )
    assert len(calls) == 3


def test_grader_double_failure_excluded_but_solver_failure_is_not(
    tmp_path, monkeypatch
):
    from gdpevo import oracle

    monkeypatch.setattr(oracle, "verify_frozen", lambda: {"tree_sha256": "x"})
    monkeypatch.setattr(
        oracle,
        "run_grader",
        lambda *args: {"returncode": 1, "error": "grader_exit"},
    )
    result = grade_attempt(13, "train", "001", b"{}", tmp_path / "grade")
    assert result["binary"] is None and result["excluded"]
    assert result["grader_retries"]["grader_v1"] == 1
    failed = grade_attempt(
        13, "train", "001", b"[]", tmp_path / "invalid", "non_object_answer"
    )
    assert failed["binary"] == 0 and not failed["excluded"]


def test_snapshot_confirms_stop_and_limits_size(tmp_path, monkeypatch):
    from gdpevo import boundary

    instance = boundary.Attempt(13, "train", "001", tmp_path)
    instance.answer_path = tmp_path / "answer.json"
    instance.answer_path.write_bytes(b" " * 4_000_001)
    running = True

    class Result:
        stdout = ""

    def docker(*args, **kwargs):
        result = Result()
        if args[0] == "inspect":
            result.stdout = json.dumps([{"State": {"Running": running}}])
        return result

    monkeypatch.setattr(boundary, "docker", docker)
    with pytest.raises(RuntimeError, match="still running"):
        instance.snapshot()
    running = False
    with pytest.raises(ValueError, match="4 MB"):
        instance.snapshot()


def test_grader_resumes_readonly_snapshot_after_interrupted_dispatch(
    tmp_path, monkeypatch
):
    from gdpevo import oracle

    monkeypatch.setattr(oracle, "verify_frozen", lambda: {"tree_sha256": "x"})
    output = tmp_path / "interrupted-grade"
    output.mkdir()
    (output / "answer.json").write_bytes(b"{}")
    (output / "answer.json").chmod(0o444)
    (output / "grader_v1-0-intent.json").write_text('{"status":"started"}')
    calls = []

    def success(*args):
        calls.append(args)
        return {"returncode": 0, "result": {"score": 1}, "score": 1}

    monkeypatch.setattr(oracle, "run_grader", success)
    result = grade_attempt(13, "train", "001", b"{}", output)
    assert result["binary"] == 1
    assert result["grader_retries"]["grader_v1"] == 1
    assert len(calls) == 2


def test_resource_journal_precedes_launch_and_refuses_foreign_cleanup(
    tmp_path, monkeypatch
):
    from gdpevo import boundary

    instance = boundary.Attempt(13, "train", "001", tmp_path)
    seen = []

    def failed_docker(*args, **kwargs):
        saved = json.loads((tmp_path / "resources.json").read_text())
        assert args[-1] in saved["networks"]
        seen.append(args)
        raise OSError("daemon lost after request")

    monkeypatch.setattr(boundary, "docker", failed_docker)
    with pytest.raises(OSError):
        instance.network("-back")
    assert seen
    (tmp_path / "resources.json").write_text(
        json.dumps({"containers": ["foreign-worker"], "networks": []})
    )
    with pytest.raises(ValueError, match="foreign"):
        instance.recover(tmp_path)


def test_changed_schedule_does_not_mutate_existing_denominators(tmp_path):
    specs = schedule(manifest())
    slots = Slots(tmp_path, specs)
    before = slots.path.read_bytes()
    with pytest.raises(ValueError, match="schedule changed"):
        Slots(tmp_path, specs[:-1])
    assert slots.path.read_bytes() == before


async def test_runner_recovers_grader_checkpoint_without_solver(
    tmp_path, monkeypatch
):
    import hashlib

    monkeypatch.setattr(module, "ROOT", tmp_path)
    instance = Runner(manifest(), {})
    spec = instance.specs[0]
    identity = spec["rollout_id"]
    logs = tmp_path / "logs/gdpevo/test-t2" / identity / "attempt-0"
    logs.mkdir(parents=True)
    execution = {"status": "finished"}
    (logs / "submission.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "execution": execution,
                "answer_sha256": hashlib.sha256(b"{}").hexdigest(),
            }
        )
    )
    (logs / "trajectory.jsonl").write_text(
        '{"kind":"instruction","text":"business task"}\n'
        '{"kind":"finish","answer":"done"}\n'
    )
    private = instance.private / identity / "attempt-0"
    private.mkdir(parents=True)
    (private / "answer.json").write_bytes(b"{}")
    (private / "answer.json").chmod(0o444)
    instance.slots.update(
        identity, status="running", infrastructure_attempts=1
    )
    called = []

    def grade(*args):
        called.append(args)
        return {
            "status": "finished",
            "binary": 1,
            "excluded": False,
            "grader_retries": {"grader_v1": 1, "upstream": 0},
        }

    async def no_solver(*args):
        pytest.fail("Grader recovery must not rerun solver")

    monkeypatch.setattr(module, "grade_attempt", grade)
    instance.attempt = no_solver
    await instance.trial(spec)
    assert len(called) == 1
    assert instance.slots.rows[identity]["status"] == "complete"
    assert instance.slots.rows[identity]["binary"] == 1
    instance.lock.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"[]", "non_object_answer"),
        (b"broken", "invalid_json_answer"),
        (ValueError("Submission exceeds 4 MB"), "oversized_answer"),
        (PermissionError("not readable"), "unreadable_answer"),
    ],
)
async def test_submission_failures_finalize_actual_runner_slot(
    tmp_path, monkeypatch, raw, expected
):
    from types import SimpleNamespace

    monkeypatch.setattr(module, "ROOT", tmp_path)

    class Boundary:
        def __init__(self, group, split, task, directory):
            self.directory = directory

        def __enter__(self):
            folder = self.directory / "staged/input"
            folder.mkdir(parents=True)
            (folder / "prompt.txt").write_text(
                "Produce a business JSON answer."
            )
            return self

        def snapshot(self):
            if isinstance(raw, Exception):
                raise raw
            return raw

        def __exit__(self, *_):
            pass

    class Backend:
        is_api = True
        tags = module.CallTags()

        async def complete(self, *args):
            return SimpleNamespace(
                text='{"action":"finish","answer":"done"}',
                record={"call_id": "unpaid", "ok": True},
                message=None,
            )

        def close(self):
            pass

    instance = Runner(manifest(), {}, boundary_factory=Boundary)
    instance.backend = lambda *args, **kwargs: Backend()
    called = []

    def grade(group, split, task, answer, output, status):
        called.append(status)
        return {
            "status": status,
            "binary": 0,
            "excluded": False,
            "grader_retries": {"grader_v1": 0, "upstream": 0},
        }

    monkeypatch.setattr(module, "grade_attempt", grade)
    spec = instance.specs[0]
    await instance.trial(spec)
    row = instance.slots.rows[spec["rollout_id"]]
    assert called == [expected]
    assert row["disposition"] == expected
    assert row["denominator"] == row["eligible_denominator"] == 1
    assert row["binary"] == 0 and not row["excluded"]
    instance.lock.close()


def test_archived_integrated_t2_manifest_is_ingestible_v3(tmp_path):
    """Exercise the exact real-run manifest, including its A9 exclusion."""
    path = module.ROOT / "runs/t2-accept-260911-final/trajectories.json"
    if not path.exists():
        pytest.skip("Local integrated acceptance archive is not present")
    captured = []

    class Queue:
        def enqueue(self, identity, judge, evidence, repeat):
            assert evidence.trajectory.version == VERSION == "v3"
            assert evidence.trajectory.events[-1]["kind"] == "termination"
            payload = evidence.to_dict()
            assert "target_group" not in json.dumps(payload)
            assert "grader_v1_score" not in json.dumps(payload)
            captured.append((identity, judge))
            return identity + judge

    manifest = json.loads(path.read_text())
    assert manifest["expected_count"] == manifest["scheduled_count"] == 24
    assert (
        sum(t["metadata"]["excluded"] for t in manifest["trajectories"]) == 1
    )
    entries = ingest_manifest(Queue(), path, tmp_path / "ingested")
    assert len(entries) == len(captured) == 48
    assert len({identity for identity, _ in captured}) == 24


@pytest.mark.parametrize("role", ["task", "judge", "evolver"])
def test_runner_uses_endpoint_compatible_cache_parameters(
    tmp_path, monkeypatch, role
):
    """DeepSeek rejects the OpenAI-only field before any judge can run."""
    monkeypatch.setattr(module, "ROOT", tmp_path)
    value = manifest()
    value["providers"] = {
        role: {
            "base_url": "https://example.invalid",
            "deployment": "test",
            "rpm": 100,
            "tpm": 10000,
        }
    }
    instance = Runner(value, {})
    captured = {}

    def backend(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "AccountedBackend", backend)
    try:
        instance.backend(instance.specs[0], role, tmp_path / "calls")
        params = captured["extra_params"]
        assert params["user"].startswith("gdpevo-")
        assert ("prompt_cache_key" in params) == (role == "task")
        assert captured["max_retries"] == 0
        if role != "task":
            assert params["response_format"] == {"type": "json_object"}
    finally:
        instance.lock.close()
