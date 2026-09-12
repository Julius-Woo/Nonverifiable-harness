"""R10 adversarial regressions for the live verifier and blinded reports."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.models.task.config import TaskOS

from evolution.admission import HarborAdmission
from evolution.behavior import standing_metrics
from evolution.evaluation import Evaluator, oracle_label
from evolution.grading import FrozenEnvironment, LiveRuntime
from evolution.loop import EvolutionLoop, select_control
from evolution.outcomes import termination
from evolution.state import State
from evolution.workspace import docker


def test_discarded_evolver_scope_cannot_consume_new_revision_call_cap(
    tmp_path,
):
    from evolution.accounting import BudgetHalt, PhaseGuard

    loop = object.__new__(EvolutionLoop)
    loop.experiment, loop.arm, loop.manifest = "qualification", "A0", {}
    old = loop.evolver_session_id("i01-c1")
    loop.manifest = {"qualification_revision": "corrected"}
    current = loop.evolver_session_id("i01-c1")
    guard = PhaseGuard(tmp_path / "budget.sqlite", estimate=40, ceiling=60)
    try:
        for i in range(24):
            guard.reserve(f"old-{i}", old, 0.01, 5, max_calls=24)
        for i in range(24):
            guard.reserve(f"current-{i}", current, 0.01, 5, max_calls=24)
        with pytest.raises(BudgetHalt, match="call cap"):
            guard.reserve("excess", current, 0.01, 5, max_calls=24)
        total = guard.db.execute(
            "SELECT sum(reserved) FROM requests"
        ).fetchone()[0]
        assert total == pytest.approx(0.48)
    finally:
        guard.close()


@pytest.mark.parametrize(
    "error", ["RuntimeError", "FrozenGraderRetryExhausted"]
)
def test_verifier_crash_is_excluded_without_changing_solver_termination(error):
    records = [
        {"kind": "observation", "command": "true", "return_code": 0},
        {"kind": "finish", "answer": "Done."},
    ]
    execution = {"status": "finished", "reason": "normal_finish"}
    result = {
        "verifier": {"started_at": "2026-09-12T00:00:00Z"},
        "exception_info": {
            "exception_type": error,
            "exception_message": "PRIVATE GRADER CRASH cyber_policy timeout",
        },
    }
    assert oracle_label(result, execution, records) == (
        None,
        None,
        "grader_failure",
    )
    projected = termination(records, result=result, execution=execution)
    assert projected["reason"] == "normal_finish"
    assert "GRADER" not in json.dumps(projected)


async def test_qualification_admission_does_not_count_calibration_slots(
    tmp_path, monkeypatch
):
    import asyncio
    import fcntl
    from types import SimpleNamespace

    from evolution import admission

    monkeypatch.setattr(admission, "available_gib", lambda: 12)
    monkeypatch.setattr(
        admission,
        "docker",
        lambda *args: SimpleNamespace(
            stdout="".join(
                f"nvhe-w13-{i}__env-main-1 image\n" for i in range(4)
            )
        ),
    )
    calibration = HarborAdmission(tmp_path, "calibration")
    for handle in calibration.handles:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write("calibration")
        handle.flush()
    workers = [
        HarborAdmission(
            tmp_path, f"qualification-{i}", limit=3, scope="qualification"
        )
        for i in range(4)
    ]
    try:
        for worker in workers[:3]:
            await asyncio.wait_for(worker.__aenter__(), 3)
        pending = asyncio.create_task(workers[3].__aenter__())
        await asyncio.sleep(2.2)
        assert not pending.done()
        assert not workers[0].memory_ready(5.9)
        assert not calibration.memory_ready(7.9)
        assert calibration.memory_ready(8.1)
        await workers[0].__aexit__()
        await asyncio.wait_for(pending, 3)
    finally:
        for worker in workers[1:]:
            await worker.__aexit__()
        await calibration.__aexit__()


def test_grader_exclusion_is_persisted_and_counted(tmp_path):
    evaluator = object.__new__(Evaluator)
    evaluator.jobs = tmp_path / "jobs"
    evaluator.private = tmp_path / "oracle"
    evaluator.arm = "A0"
    evaluator.logs = tmp_path / "logs"
    directory = evaluator.jobs / "trial"
    (directory / "agent").mkdir(parents=True)
    (directory / "agent/execution.json").write_text(
        json.dumps({"status": "finished"})
    )
    (directory / "agent/trace.jsonl").write_text(
        json.dumps({"kind": "finish", "answer": "Done"}) + "\n"
    )
    (directory / "result.json").write_text(
        json.dumps(
            {
                "exception_info": {
                    "exception_type": "FrozenGraderRetryExhausted"
                }
            }
        )
    )
    row = evaluator.collect("trial", {"partition": "sealed"})
    assert row["oracle"] is None and row["excluded"] is True
    assert row["reason"] == row["exclusion_reason"] == "grader_failure"
    assert row["execution"]["reason"] == "normal_finish"
    metrics = standing_metrics([row])
    assert metrics["grader_failures"] == 1
    assert metrics["denominator"] == 0


def test_public_summary_has_search_metrics_only(tmp_path):
    loop = object.__new__(EvolutionLoop)
    loop.iteration, loop.arm = 1, "A0"
    loop.directory, loop.logs = tmp_path / "runs", tmp_path / "logs"
    loop.evaluator = SimpleNamespace(private=tmp_path / "oracle")
    loop.state = State(
        loop.directory / "state.sqlite", private=tmp_path / "oracle/state"
    )
    for part, count in [("search", 2), ("sealed", 7), ("anchor", 3)]:
        for replicate in range(count):
            spec = {"partition": part, "iteration": 1, "replicate": replicate}
            identity = loop.state.schedule(spec)
            loop.state.finish(
                identity,
                {
                    **spec,
                    "id": identity,
                    "behavior": {"exhaustion": part != "search"},
                },
            )
    summary = {
        "arm": "C-TTS-A3-loop",
        "iteration": 1,
        "sealed_measurements": {"planned": 7},
        "sealed_preference_status_counts": {"scored": 7},
        "rollouts": 12,
        "physical_trials": 12,
    }
    loop.write_summary(summary)
    assert summary["standing_metrics"]["scheduled"] == 2
    assert summary["standing_metrics"]["counts"]["exhaustion"] == 0
    forbidden = {
        "sealed_measurements", "sealed_preference_status_counts",
        "rollouts", "physical_trials",
    }
    assert not forbidden.intersection(summary)
    assert summary["search_rollouts"] == 2
    assert summary["search_physical_trials"] == 2
    assert not forbidden.intersection(loop.state.stage("finished-1"))
    for path in [
        loop.directory / "evolution_summary.jsonl",
        loop.logs / "iteration-1.json",
    ]:
        public = json.loads(path.read_text())
        assert public["standing_metrics_partition"] == "search"
        assert public["standing_metrics"]["denominator"] == 2
        assert not forbidden.intersection(public)
    private_summary = json.loads(
        (tmp_path / "oracle/summary-i1.json").read_text()
    )
    assert private_summary["sealed_preference_status_counts"] == {"scored": 7}
    assert private_summary["rollouts"] == 12
    private = json.loads((tmp_path / "oracle/behavior-i1.json").read_text())
    assert private["standing_metrics"]["scheduled"] == 12
    loop.state.close()


@pytest.mark.parametrize("field", [
    "sealed_preference_status_counts", "rollouts", "physical_trials",
])
def test_public_artifact_audit_detects_nonstanding_private_counts(
    tmp_path, field,
):
    from evolution.pilot import public_artifact_errors, public_summary_errors

    public = tmp_path / "runs"
    public.mkdir()
    summary = {"arm": "C-TTS-A3-loop", "iteration": 1, field: 7}
    assert public_summary_errors([summary])
    (public / "evolution_summary.jsonl").write_text(
        json.dumps(summary) + "\n"
    )
    assert public_artifact_errors(tmp_path, public)
    state = State(public / "state.sqlite")
    state.stage("finished-1", summary)
    summary.pop(field)
    summary["allocations"] = {"search": {"physical_trials": 7}}
    (public / "evolution_summary.jsonl").write_text(
        json.dumps(summary) + "\n"
    )
    assert public_artifact_errors(tmp_path, public)
    state.stage("finished-1", summary)
    state.close()
    assert public_artifact_errors(tmp_path, public) == []


@pytest.mark.parametrize("archived", [False, True])
def test_receipt_costs_deduplicate_controller_path_aliases(tmp_path, archived):
    from evolution.accounting import cost_summary

    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    accounting = tmp_path / "costs"
    receipts = accounting / "requests" / "request-1"
    receipts.mkdir(parents=True)
    raw = actual / "call-1"
    record = {
        "raw_dir": str(raw), "cost_usd": 99,
        "input_tokens": 10, "output_tokens": 1,
    }
    if archived:
        record["call_id"] = "stable-backend-call"
    other = tmp_path / "archived" if archived else alias
    (accounting / "ledger.jsonl").write_text(
        json.dumps(record) + "\n"
        + json.dumps({**record, "raw_dir": str(other / "call-1")}) + "\n"
    )
    (accounting / "requests.jsonl").write_text("")
    (receipts / "receipt.json").write_text(json.dumps({
        "id": "request-1", "backend_raw_dir": str(raw),
        "uncached_upper_usd": 0.25, "role": "task",
        "ledger_record": {
            **record, "cost_usd": None,
            "known_response_cost_usd": 0.2,
        },
    }))
    value = cost_summary(
        accounting / "ledger.jsonl", accounting / "requests.jsonl"
    )
    assert value["known_usd"] == pytest.approx(0.2)
    assert value["tokens"]["input_tokens"] == 10
    assert value["tokens"]["output_tokens"] == 1


def test_control_ties_use_replicate_before_hash():
    rows = [
        {"task": "one", "replicate": 9, "id": "aaa", "score": 1},
        {"task": "one", "replicate": 0, "id": "zzz", "score": 1},
    ]
    assert select_control(rows, "A1")[0]["id"] == "zzz"


def test_memory_hysteresis_is_shared_and_strict(tmp_path):
    first, second = (
        HarborAdmission(tmp_path, "first"),
        HarborAdmission(tmp_path, "second"),
    )
    try:
        assert first.memory_ready(6.5)
        assert not first.memory_ready(5.9)
        assert not second.memory_ready(7.9)
        assert not second.memory_ready(8)
        assert second.memory_ready(8.01)
        assert first.memory_ready(6.1)
    finally:
        for admission in (first, second):
            for handle in admission.handles:
                handle.close()
            admission.gate.close()


@pytest.mark.skipif(
    os.environ.get("NVH_R10_DOCKER") != "1",
    reason="Explicit real-Docker adversarial controls",
)
@pytest.mark.parametrize(
    "attack", ["startup-and-binaries", "background-reader"]
)
async def test_real_docker_hidden_tests_and_reward_boundary(tmp_path, attack):
    image = "nvh-evolution-runtime:v1"
    name = "nvh-r10-" + attack
    tests = tmp_path / "tests"
    tests.mkdir()
    marker = "R10_HIDDEN_TEST_CONTENT_8d19b2"
    (tests / "secret.txt").write_text(marker)
    (tests / "test.sh").write_text(
        '#!/bin/bash\npython3 -c "assert 1 == 2"\n'
        "if [ $? -eq 0 ]; then echo 1; else echo 0; fi "
        "> /logs/verifier/reward.txt\n"
    )
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--entrypoint",
        "/bin/sleep",
        image,
        "infinity",
    )
    runtime = LiveRuntime(name, image, tests, tmp_path / "runtime")
    original = SimpleNamespace(
        os=TaskOS.LINUX, capabilities=EnvironmentCapabilities(mounted=True)
    )
    verifier = FrozenEnvironment(original, name)
    try:
        if attack == "startup-and-binaries":
            docker(
                "exec",
                name,
                "/bin/sh",
                "-c",
                "mkdir -p /etc/profile.d /app/shadow; "
                'echo "touch /tmp/r10-executed" > /etc/profile.d/r10.sh; '
                'printf "#!/bin/sh\\ntouch /tmp/r10-executed\\nexit 0\\n" '
                "> /app/shadow/python3; "
                "chmod +x /app/shadow/python3; "
                "cp /bin/bash /bin/bash.original; "
                'printf "#!/bin/sh\\ntouch /tmp/r10-executed\\nexit 0\\n" '
                "> /bin/bash",
            )
        else:
            docker(
                "exec",
                "-d",
                name,
                "/usr/local/bin/python3",
                "-c",
                "import pathlib,time\n"
                "while True:\n"
                " try:\n"
                '  data=pathlib.Path("/tests/secret.txt").read_text()\n'
                '  with open("/tmp/reader.log","a") as f: f.write(data)\n'
                " except OSError: pass\n"
                " time.sleep(.001)\n",
            )
        audit = await runtime.start()
        result = await verifier.exec(
            "/bin/bash --noprofile --norc /tests/test.sh",
            env={
                "PATH": "/app/shadow:/bin",
                "BASH_ENV": "/etc/profile.d/r10.sh",
            },
        )
        assert result.return_code == 0, result.stderr
        await asyncio.sleep(0.2)
        receipt = await verifier.exec("cat /logs/verifier/reward.txt")
        assert receipt.stdout.strip() == "0"
        assert (
            await verifier.exec("test ! -e /tmp/r10-executed")
        ).return_code == 0
        assert (
            marker not in (await verifier.exec("cat /tmp/reader.log")).stdout
        )
        assert (
            await verifier.exec('stat -c "%a:%u:%g" /tests')
        ).stdout.strip() == "700:0:0"
        assert (await verifier.exec("touch /usr/r10-write")).return_code != 0
        if attack == "background-reader":
            assert any(
                "reader.log" in r["command"]
                for r in audit["stopped_processes"]
            )
        docker(
            "exec",
            "-d",
            name,
            "/bin/sh",
            "-c",
            "sleep 300 & echo $! > /tmp/verifier-child.pid; wait",
        )
        await asyncio.sleep(0.1)
        await runtime.quiesce()
        drained = await verifier.exec(
            "p=$(cat /tmp/verifier-child.pid); "
            "[ ! -e /proc/$p/stat ] || "
            'test "$(cut -d " " -f 3 /proc/$p/stat)" = Z'
        )
        assert drained.return_code == 0
        destination = Path("logs/r10-controls") / (attack + ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                {
                    "passed": True,
                    "reward": 0,
                    "canary_executed": False,
                    "hidden_content_leaked": False,
                    "runtime": audit,
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()
        docker("rm", "-f", name, check=False)


async def test_a3_excluded_reference_never_gets_replaced_or_ranked(tmp_path):
    from evolution.a3_control import score_control_pool
    from evolution.a3_metrics import metrics

    class Operators:
        async def rank(self, *args):
            raise AssertionError("Excluded comparison reached the ranker")

    loop = SimpleNamespace(
        iteration=1, evaluator=SimpleNamespace(private=tmp_path)
    )
    rows = [
        {
            "id": "first",
            "task": "t",
            "partition": "search",
            "replicate": 0,
            "excluded": True,
            "oracle": None,
            "score": None,
        },
        {
            "id": "second",
            "task": "t",
            "partition": "search",
            "replicate": 1,
            "oracle": 1,
            "score": None,
        },
    ]
    scored = await score_control_pool(
        loop, rows, Path("seed"), operators=Operators()
    )
    assert [r["reference_id"] for r in scored] == ["first", "first"]
    assert all(r["score"] is None for r in scored)
    assert metrics(scored)["J"] is None


@pytest.mark.skipif(
    os.environ.get("NVH_R10_QEMU") != "1",
    reason="Explicit live QEMU compatibility control",
)
async def test_qemu_live_service_keeps_reward_one(tmp_path):
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier

    task_path = next(
        Path("/home/argustest/.cache/harbor/tasks").glob(
            "*/qemu-alpine-ssh/task.toml"
        )
    ).parent
    task = Task(task_path)
    name = os.environ.get("NVH_R10_QEMU_CONTAINER", "nvh-r10-qemu-regression")
    owns = "NVH_R10_QEMU_CONTAINER" not in os.environ
    image = "alexgshaw/qemu-alpine-ssh:20251031"
    if owns:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--memory",
            "2g",
            "--cpus",
            "1",
            "--entrypoint",
            "/bin/sleep",
            image,
            "infinity",
        )
        docker("cp", task_path / "solution/solve.sh", name + ":/app/solve.sh")
        await asyncio.to_thread(
            docker, "exec", name, "/bin/bash", "/app/solve.sh"
        )
        # The reference expect script can return before guest setup finishes.
        for _ in range(90):
            probe = await asyncio.to_thread(
                docker,
                "exec",
                name,
                "/usr/bin/python3",
                "-c",
                "import socket; "
                's=socket.create_connection(("127.0.0.1",2222),2); '
                's.settimeout(2); assert s.recv(100).startswith(b"SSH-")',
                check=False,
            )
            if probe.returncode == 0:
                break
            await asyncio.sleep(2)
        else:
            docker("rm", "-f", name, check=False)
            raise AssertionError("Reference guest SSH did not start")
    runtime = LiveRuntime(
        name, image, task.paths.tests_dir, tmp_path / "runtime"
    )
    original = SimpleNamespace(
        os=TaskOS.LINUX, capabilities=EnvironmentCapabilities(mounted=True)
    )
    env = FrozenEnvironment(original, name)
    paths = TrialPaths(tmp_path / "trial")
    paths.verifier_dir.mkdir(parents=True)
    try:
        audit = await runtime.start()
        await env.exec("mkdir -p /tests/.home")
        result = await asyncio.wait_for(
            Verifier(task=task, trial_paths=paths, environment=env).verify(),
            300,
        )
        assert result.rewards["reward"] == 1
        assert any(
            "qemu-system" in r["command"] and r["listening_sockets"]
            for r in audit["kept_services"]
        )
        destination = Path("logs/r10-controls/qemu-regression.json")
        destination.write_text(
            json.dumps(
                {"passed": True, "reward": 1, "runtime": audit}, indent=2
            )
        )
    finally:
        await runtime.close()
        if owns:
            docker("rm", "-f", name, check=False)


def test_launcher_checks_exact_frozen_source_before_redirect(
    tmp_path, monkeypatch
):
    from evolution.manifest import file_hash
    from scripts import run_pilot

    controller = tmp_path / "frozen"
    script = controller / "scripts/run_pilot.py"
    script.parent.mkdir(parents=True)
    script.write_text('print("frozen source")\n')
    manifest = tmp_path / "manifest.json"
    value = {
        "controller_root": "frozen",
        "input_hashes": {"scripts/run_pilot.py": file_hash(script)},
    }
    manifest.write_text(json.dumps(value))
    monkeypatch.setattr(
        run_pilot, "__file__", str(tmp_path / "scripts/run_pilot.py")
    )
    monkeypatch.setattr(
        run_pilot.sys,
        "argv",
        ["run_pilot", "--manifest", str(manifest), "--check"],
    )
    calls = []
    monkeypatch.setattr(run_pilot.os, "chdir", lambda path: calls.append(path))

    def execute(binary, args):
        calls.append(args)
        raise RuntimeError("exec captured")

    monkeypatch.setattr(run_pilot.os, "execv", execute)
    with pytest.raises(RuntimeError, match="exec captured"):
        run_pilot.main()
    assert calls[0] == controller
    assert calls[1][1:3] == ["-m", "scripts.run_pilot"]
    assert calls[1][-1] == "--check"
    script.write_text('print("concurrent mutation")\n')
    with pytest.raises(ValueError, match="missing or changed"):
        run_pilot.main()


async def test_replacement_keeps_its_grader_failure_exclusion(tmp_path):
    evaluator = object.__new__(Evaluator)
    evaluator.private = tmp_path / "oracle"
    evaluator.state = State(tmp_path / "state.sqlite")
    evaluator.config = {}
    spec = {"task": "task", "partition": "search"}
    original_id = evaluator.state.schedule(spec)
    calls = []

    async def once(candidate, current):
        calls.append(current)
        if not current.get("infrastructure_attempt"):
            return {
                "id": original_id,
                **current,
                "status": "complete",
                "oracle": None,
                "score": None,
                "execution": {"api_timeout_only": True},
            }
        return {
            "id": evaluator.state.schedule(current),
            **current,
            "status": "complete",
            "oracle": None,
            "score": None,
            "excluded": True,
            "reason": "grader_failure",
            "exclusion_reason": "grader_failure",
            "execution": {"status": "finished", "reason": "normal_finish"},
        }

    evaluator._one_once = once
    try:
        row = await evaluator.one(Path("candidate"), spec)
        assert len(calls) == 2
        assert row["id"] == original_id and row["retried"]
        assert row["excluded"] and row["oracle"] is None
        assert row["exclusion_reason"] == "grader_failure"
        assert row["execution"]["reason"] == "normal_finish"
        assert standing_metrics([row])["grader_failures"] == 1
    finally:
        evaluator.state.close()


def test_public_tree_cannot_link_private_workspace(tmp_path):
    from evolution.pilot import public_artifact_errors

    public = tmp_path / "runs/qualification"
    public.mkdir(parents=True)
    private = tmp_path / "oracle"
    private.mkdir()
    (tmp_path / ".env").write_text("not-a-real-secret")
    (public / "oracle-link").symlink_to(private, target_is_directory=True)
    (public / ".env").symlink_to(tmp_path / ".env")
    assert len(public_artifact_errors(tmp_path, public)) == 2
    (public / "oracle-link").unlink()
    (public / ".env").unlink()
    assert public_artifact_errors(tmp_path, public) == []
