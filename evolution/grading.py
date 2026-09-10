"""Grade a frozen filesystem in a fresh PID/network namespace, retry once.

The solver container stays paused until Harbor teardown. No verifier upload
can be observed by surviving solver processes. This infrastructure condition
requires qualification:
process-dependent tasks require separate compatibility qualification for pilot.
"""

import asyncio
import json
import os
import shlex
import shutil
import time
from pathlib import Path

from harbor.trial.single_step import SingleStepTrial
from harbor.verifier.verifier import Verifier

from evolution.candidates import atomic_json
from evolution.workspace import ExecResult, docker
from harness.ledger import append_jsonl, utc_now


class FrozenEnvironment:
    """Minimal verifier-only environment; never exposed to candidate RPC."""

    def __init__(self, original, name):
        self.os = original.os
        self.capabilities = original.capabilities.model_copy(
            update={"mounted": True}
        )
        self.name = name
        self.default_user = "root"

    async def exec(
        self, command, cwd=None, env=None, timeout_sec=None, user=None
    ):
        args = ["docker", "exec", "-u", str(user or self.default_user)]
        if cwd:
            args += ["-w", cwd]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [self.name, "bash", "-c", command]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout_sec)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        return ExecResult(
            out.decode(errors="replace"),
            err.decode(errors="replace"),
            proc.returncode,
        )

    async def upload_dir(self, source_dir, target_dir):
        docker("exec", self.name, "mkdir", "-p", str(target_dir))
        await asyncio.to_thread(
            docker, "cp", str(source_dir) + "/.", f"{self.name}:{target_dir}"
        )


class IsolatedTrial(SingleStepTrial):
    @classmethod
    async def create(cls, config):
        if config.source_trial is not None:
            raise ValueError("Use the durable frozen-artifact grader retry")
        cls._resolve_agent_skills(config)
        task, download = await cls._load_task(config)
        if task.has_steps:
            raise ValueError("Multistep tasks are not qualified")
        trial = cls(config, _task=task, _task_download_result=download)
        await trial._create_bridge()
        return trial

    def grading_directory(self):
        kwargs = self.config.agent.kwargs
        return (
            Path(__file__).resolve().parents[1]
            / "oracle"
            / kwargs["experiment"]
            / kwargs["arm"]
            / "grading"
            / self.config.trial_name
        )

    async def _frozen_snapshot(self, private, user, env):
        path = private / "boundary.json"
        if path.exists():
            record = json.loads(path.read_text())
            if not record.get("snapshot_ready"):
                raise RuntimeError(
                    "Interrupted snapshot needs manual recovery"
                )
            return record
        original = self.agent_environment
        found = await original._run_docker_compose_command(
            ["ps", "-q", "main"]
        )
        container = found.stdout.strip()
        if not container:
            raise RuntimeError("Cannot identify solver container")
        info = json.loads(docker("inspect", container).stdout)[0]
        private.mkdir(parents=True, exist_ok=True)
        docker("pause", container)
        snapshot = (
            (
                await asyncio.to_thread(
                    docker, "commit", "--no-pause", container
                )
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        record = {
            "schema_version": 2,
            "solver_container": container,
            "solver_image": info["Image"],
            "snapshot_image": snapshot,
            "solver_paused": True,
            "grader_fresh_pid_namespace": True,
            "task": self.config.task.path.as_posix(),
            "attempts": [],
            "mounts": [],
            "snapshot_ready": False,
            "verifier_user": user,
            "verifier_env": env,
        }
        atomic_json(path, record)
        if self._result is not None:
            atomic_json(
                private / "pregrade-result.json",
                self._result.model_dump(mode="json"),
            )
        for index, mount in enumerate(info["Mounts"]):
            target = mount["Destination"]
            if target == "/logs/verifier":
                continue
            destination = private / "mounts" / str(index)
            destination.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                docker, "cp", f"{container}:{target}/.", destination
            )
            record["mounts"].append([target, str(destination)])
        record["snapshot_ready"] = True
        atomic_json(path, record)
        return record

    async def _run_shared_verifier(
        self, *, timeout_sec, user, env=None, step_name=None, step_cfg=None
    ):
        if step_name or step_cfg:
            raise RuntimeError("Multistep grading has not been qualified")
        private = self.grading_directory()
        record = await self._frozen_snapshot(private, user, env)
        snapshot, container = (
            record["snapshot_image"],
            record["solver_container"],
        )
        if record.get("complete"):
            from harbor.models.verifier.result import VerifierResult

            return VerifierResult.model_validate(record["verifier_result"])
        if len(record["attempts"]) >= 2:
            raise RuntimeError("Frozen grader retry exhausted")
        terminal = False
        try:
            for attempt in range(len(record["attempts"]), 2):
                name = f"{self.config.trial_name}-grade-{attempt}"
                out = private / f"attempt-{attempt}"
                out.mkdir(exist_ok=True)
                # A killed attempt consumes its slot before any execution.
                record["attempts"].append(
                    {
                        "index": attempt,
                        "status": "dispatched",
                        "snapshot_image": snapshot,
                    }
                )
                atomic_json(private / "boundary.json", record)
                # Preserve partial output from the previous attempt; never
                # accept its reward as a receipt for this fresh attempt.
                for path in self.paths.verifier_dir.iterdir():
                    if path.is_file():
                        shutil.copy2(path, out / ("previous-" + path.name))
                        path.unlink()
                args = [
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    "bridge",
                    "--entrypoint",
                    "sleep",
                    "--mount",
                    f"type=bind,src={self.paths.verifier_dir.resolve()},dst=/logs/verifier",
                    snapshot,
                    "infinity",
                ]
                docker(*args)
                try:
                    for target, source in record["mounts"]:
                        docker("exec", name, "mkdir", "-p", target)
                        await asyncio.to_thread(
                            docker, "cp", source + "/.", f"{name}:{target}"
                        )
                    marker = "NVH_GRADER_ONLY_" + self.config.trial_name
                    docker("exec", name, "mkdir", "-p", "/tests")
                    docker(
                        "exec",
                        name,
                        "sh",
                        "-c",
                        "printf %s "
                        + shlex.quote(marker)
                        + " > /tests/nvh-isolation-canary.txt",
                    )
                    original = docker("inspect", container, check=False)
                    if original.returncode == 0:
                        state = json.loads(original.stdout)[0]["State"]
                        absent = docker(
                            "cp",
                            f"{container}:/tests/nvh-isolation-canary.txt",
                            private / "unexpected-canary",
                            check=False,
                        )
                        safe = state.get("Paused") or not state.get("Running")
                        if absent.returncode == 0 or not safe:
                            raise RuntimeError(
                                "Solver/grader isolation failed"
                            )
                    record["canary"] = {
                        "value": marker,
                        "absent_in_solver": True,
                        "solver_paused_during_upload": True,
                    }
                    atomic_json(private / "boundary.json", record)
                    target_env = FrozenEnvironment(
                        self.agent_environment, name
                    )
                    target_env.default_user = record["verifier_user"] or "root"
                    verifier = Verifier(
                        task=self.task,
                        trial_paths=self.paths,
                        environment=target_env,
                        logger=self.logger,
                        override_env=self.config.verifier.env or None,
                        verifier_env=record["verifier_env"],
                    )
                    result = await asyncio.wait_for(
                        verifier.verify(), timeout_sec
                    )
                    record["attempts"][-1]["status"] = "complete"
                    record["complete"] = True
                    record["verifier_result"] = result.model_dump(mode="json")
                    atomic_json(private / "boundary.json", record)
                    terminal = True
                    return result
                except Exception as exc:
                    record["attempts"][-1].update(
                        status="failed", error=type(exc).__name__
                    )
                    atomic_json(private / "boundary.json", record)
                    if attempt == 1:
                        terminal = True
                        raise
                finally:
                    docker("rm", "-f", name, check=False)
        finally:
            docker("kill", container, check=False)
            if terminal or len(record["attempts"]) >= 2:
                docker("image", "rm", snapshot, check=False)
                shutil.rmtree(private / "mounts", ignore_errors=True)
            append_jsonl(
                private / "events.jsonl",
                {
                    "ts": utc_now(),
                    "event": "frozen_grading_end",
                    "solver_never_resumed": True,
                    "snapshot_retained_for_recovery": not terminal
                    and len(record["attempts"]) < 2,
                },
            )


async def resume_frozen_grading(root, config_path):
    """Complete a killed grader using its snapshot; never call agent.run."""
    from harbor.models.trial.config import TrialConfig

    from evolution.accounting import BudgetHalt, PhaseGuard
    from evolution.admission import HarborAdmission

    config = TrialConfig.model_validate_json(Path(config_path).read_text())
    trial = await IsolatedTrial.create(config)
    private = trial.grading_directory()
    try:
        record = json.loads((private / "boundary.json").read_text())
        saved = json.loads((private / "pregrade-result.json").read_text())
        guard = PhaseGuard(
            Path(config.agent.kwargs["accounting_dir"]) / "budget.sqlite"
        )
        try:
            guard.check()
            async with (
                asyncio.timeout(max(0, guard.deadline - time.time())),
                HarborAdmission(Path(root), config.trial_name),
            ):
                try:
                    result = await trial._run_shared_verifier(
                        timeout_sec=min(
                            trial._verifier_timeout_sec,
                            max(1, guard.deadline - time.time()),
                        ),
                        user=record["verifier_user"],
                        env=record["verifier_env"],
                    )
                    saved["verifier_result"] = result.model_dump(mode="json")
                except Exception as exc:
                    saved["verifier_result"] = None
                    saved["exception_info"] = {
                        "exception_type": "FrozenGraderRetryExhausted",
                        "exception_message": type(exc).__name__,
                    }
                destination = config.trials_dir / config.trial_name / "agent"
                destination.mkdir(parents=True, exist_ok=True)
                for name in (
                    "trace.jsonl",
                    "execution.json",
                    "final.txt",
                    "runtime_boundary.json",
                ):
                    source = trial.agent.evidence_dir / name
                    if source.exists():
                        temporary = (
                            destination
                            / f".trusted-recovery-{os.getpid()}-{name}"
                        )
                        temporary.unlink(missing_ok=True)
                        temporary.write_bytes(source.read_bytes())
                        os.replace(temporary, destination / name)
                atomic_json(
                    config.trials_dir / config.trial_name / "result.json",
                    saved,
                )
                append_jsonl(
                    private / "events.jsonl",
                    {
                        "ts": utc_now(),
                        "event": "frozen_grader_recovered",
                        "solver_calls": 0,
                    },
                )
        except TimeoutError as exc:
            guard.persist_halt("Phase wall-clock limit reached")
            raise BudgetHalt(
                "Phase wall-clock limit reached", phase=True
            ) from exc
        finally:
            guard.close()
    finally:
        trial.agent.backend.close()
