"""Live verification with temporary original-image runtime mounts."""

import asyncio
import io
import json
import os
import shlex
import tarfile
import time
from pathlib import Path

from harbor.trial.single_step import SingleStepTrial
from harbor.verifier.verifier import Verifier

from evolution.candidates import atomic_json
from evolution.workspace import ExecResult, docker
from harness.ledger import append_jsonl, utc_now


class FrozenGraderRetryExhausted(RuntimeError):
    """The two verifier attempts failed; solver termination is unaffected."""


class LiveRuntime:
    """Install a trusted runtime without replacing the live task container."""

    def __init__(
        self,
        container,
        image,
        tests,
        private,
        *,
        interpreters=(),
        service_pattern="",
    ):
        self.container, self.image = container, image
        self.tests, self.private = Path(tests), Path(private)
        self.interpreters, self.service_pattern = interpreters, service_pattern
        self.name = "nvhe-grade-" + container[:24]

    async def start(self):
        self.private.mkdir(parents=True, exist_ok=True)
        for name in ("release", "quiesce", "quiesced.json"):
            (self.private / name).unlink(missing_ok=True)
        (self.private / "runtime.json").unlink(missing_ok=True)
        helper = Path(__file__).with_name("trusted_runtime.py").resolve()
        from evolution.verifier_dependencies import prepare

        dependencies = await asyncio.to_thread(
            prepare,
            self.image,
            self.tests,
            Path(__file__).resolve().parents[1],
        )
        extra_mounts = []
        if dependencies:
            extra_mounts = [
                "--mount",
                "type=bind,src="
                + str(dependencies.resolve())
                + ",dst=/nvh-deps,readonly",
            ]
        probe = await asyncio.to_thread(
            docker,
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            self.image,
            "-c",
            "command -v python3",
            check=False,
        )
        python = probe.stdout.strip()
        if not python.startswith("/"):
            helper_runtime = await asyncio.to_thread(
                prepare,
                self.image,
                self.tests,
                Path(__file__).resolve().parents[1],
                packages=["python3"],
            )
            extra_mounts += [
                "--mount",
                "type=bind,src="
                + str(helper_runtime.resolve())
                + ",dst=/nvh-helper,readonly",
                "-e",
                "LD_LIBRARY_PATH=/nvh-helper/usr/lib/x86_64-linux-gnu",
            ]
            python = "/nvh-helper/usr/bin/python3"
        await asyncio.to_thread(
            docker,
            "run",
            "-d",
            "--name",
            self.name,
            "--privileged",
            "--pid",
            "container:" + self.container,
            "--network",
            "container:" + self.container,
            "--mount",
            f"type=bind,src={helper},dst=/nvh-runtime.py,readonly",
            "--mount",
            f"type=bind,src={self.private.resolve()},dst=/nvh-control",
            "-e",
            "NVH_INTERPRETERS=" + json.dumps(self.interpreters),
            "-e",
            "NVH_SERVICE_PATTERN=" + self.service_pattern,
            *extra_mounts,
            "--entrypoint",
            python,
            self.image,
            "-I",
            "/nvh-runtime.py",
        )
        for _ in range(600):
            path = self.private / "runtime.json"
            try:
                audit = json.loads(path.read_text())
            except (FileNotFoundError, ValueError):
                audit = {}
            if audit.get("error"):
                raise RuntimeError(audit["error"])
            if audit.get("released"):
                raise RuntimeError("Trusted runtime released before upload")
            if audit.get("ready"):
                # Upload only after the process sweep and trusted mounts exist.
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode="w") as archive:
                    for child in self.tests.iterdir():
                        archive.add(child, arcname=child.name)
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "exec",
                    "-i",
                    "-u",
                    "root",
                    self.container,
                    "/usr/bin/env",
                    "-i",
                    "PATH=/usr/bin:/bin",
                    "/bin/tar",
                    "--no-same-owner",
                    "-xf",
                    "-",
                    "-C",
                    "/tests",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, error = await process.communicate(data.getvalue())
                if process.returncode:
                    raise RuntimeError(
                        "Hidden test upload failed: " + error.decode()
                    )
                return audit
            info = json.loads(docker("inspect", self.name).stdout)[0]
            if not info["State"]["Running"]:
                raise RuntimeError(
                    "Trusted runtime helper failed: "
                    + docker("logs", self.name).stderr[-2000:]
                )
            await asyncio.sleep(0.1)
        raise RuntimeError("Trusted runtime setup timed out")

    async def quiesce(self):
        request = str(time.monotonic_ns())
        (self.private / "quiesce").write_text(request)
        for _ in range(100):
            try:
                receipt = json.loads(
                    (self.private / "quiesced.json").read_text()
                )
                if receipt.get("request") == request:
                    return
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)
        raise FrozenGraderRetryExhausted(
            "Verifier descendants could not be drained"
        )

    async def close(self):
        (self.private / "release").touch()
        # Wait for unmount-before-resume cleanup, never kill the live service.
        await asyncio.to_thread(docker, "wait", self.name, check=False)
        docker("rm", "-f", self.name, check=False)
        path = self.private / "runtime.json"
        if path.exists():
            audit = json.loads(path.read_text())
            if (
                audit.get("canary_leaks")
                or audit.get("sensitive_service_fds")
                or audit.get("cleanup_errors")
                or (audit.get("ready") and not audit.get("released"))
            ):
                raise FrozenGraderRetryExhausted(
                    "Live runtime isolation audit failed"
                )


class FrozenEnvironment:
    """Verifier-only live adapter; environment is rebuilt from an allowlist."""

    def __init__(self, original, name, image_path=None):
        self.os = original.os
        self.capabilities = original.capabilities.model_copy(
            update={"mounted": False}
        )
        self.name = name
        self.default_user = "root"
        self.image_path = image_path

    async def exec(
        self, command, cwd=None, env=None, timeout_sec=None, user=None
    ):
        clean = {
            "PATH": (
                "/tests/.deps/bin:/tests/.deps/usr/bin:"
                "/tests/.python/bin:/usr/local/sbin:/usr/local/bin:"
                "/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "LD_LIBRARY_PATH": "/tests/.deps/usr/lib/x86_64-linux-gnu",
            "PIP_TARGET": "/tests/.python",
            "PYTHONPATH": "/tests/.python",
            "HOME": "/tests/.home",
            "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        }
        if self.image_path:
            clean["PATH"] += ":" + self.image_path
        forbidden = {
            "BASH_ENV",
            "ENV",
            "PATH",
            "HOME",
            "PYTHONPATH",
            "PYTHONHOME",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "SHELLOPTS",
            "BASHOPTS",
            "GLOBIGNORE",
            "CDPATH",
        }
        clean.update(
            {
                k: v
                for k, v in (env or {}).items()
                if k not in forbidden and not k.startswith("BASH_FUNC_")
            }
        )
        args = [
            "docker",
            "exec",
            "-u",
            "root",
            "-e",
            "LD_PRELOAD=",
            "-e",
            "LD_LIBRARY_PATH=",
        ]
        if cwd:
            args += ["-w", cwd]
        args += [self.name, "/usr/bin/env", "-i"]
        args += [f"{k}={v}" for k, v in clean.items()]
        args += ["/bin/bash", "--noprofile", "--norc", "-c", command]
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
        # The helper copied exactly this tree before announcing readiness.
        if str(target_dir) != "/tests":
            raise ValueError("Only the private tests mount is uploadable")

    async def download_dir(self, source_dir, target_dir):
        if str(source_dir) != "/logs/verifier":
            raise ValueError("Only verifier receipts may leave this adapter")
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "reward.txt",
            "reward.json",
            "test-stdout.txt",
            "ctrf.json",
        ):
            result = await self.exec(
                "cat -- " + shlex.quote(source_dir + "/" + name)
            )
            if result.return_code == 0:
                (target_dir / name).write_text(result.stdout)


class IsolatedTrial(SingleStepTrial):
    @classmethod
    async def create(cls, config):
        if config.source_trial is not None:
            raise ValueError("Use the durable identical-artifact grader retry")
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

    async def _run_shared_verifier(
        self, *, timeout_sec, user, env=None, step_name=None, step_cfg=None
    ):
        if step_name or step_cfg:
            raise RuntimeError("Multistep grading has not been qualified")
        private = self.grading_directory()
        private.mkdir(parents=True, exist_ok=True)
        path = private / "boundary.json"
        if path.exists():
            record = json.loads(path.read_text())
        else:
            found = await self.agent_environment._run_docker_compose_command(
                ["ps", "-q", "main"]
            )
            container = found.stdout.strip()
            info = json.loads(docker("inspect", container).stdout)[0]
            image_info = json.loads(
                docker("image", "inspect", info["Image"]).stdout
            )[0]
            labels = image_info["Config"].get("Labels") or {}
            image_env = dict(
                item.split("=", 1)
                for item in image_info["Config"].get("Env", [])
                if "=" in item
            )
            image_path = image_env.get("PATH", "")
            interpreters = json.loads(
                labels.get("nvh.verifier.interpreters", "[]")
            )
            interpreters += [
                p for p in image_path.split(":") if p.startswith("/")
            ]
            interpreters += [
                image_env[k]
                for k in ("VIRTUAL_ENV", "PYTHONHOME")
                if image_env.get(k, "").startswith("/")
            ]
            record = {
                "schema_version": 3,
                "solver_container": container,
                "solver_image": info["Image"],
                "live_ready": True,
                "attempts": [],
                "verifier_user": user,
                "verifier_env": env,
                "interpreter_paths": interpreters,
                "image_path": image_path,
                "service_pattern": labels.get(
                    "nvh.verifier.service_pattern", ""
                ),
            }
            atomic_json(path, record)
            if self._result is not None:
                atomic_json(
                    private / "pregrade-result.json",
                    self._result.model_dump(mode="json"),
                )
        if record.get("complete"):
            from harbor.models.verifier.result import VerifierResult

            return VerifierResult.model_validate(record["verifier_result"])
        if not record.get("live_ready") or len(record["attempts"]) >= 2:
            raise FrozenGraderRetryExhausted("Live grader retry unavailable")
        runtime = LiveRuntime(
            record["solver_container"],
            record["solver_image"],
            self.task.paths.tests_dir,
            private / "runtime",
            interpreters=record["interpreter_paths"],
            service_pattern=record["service_pattern"],
        )
        try:
            record["runtime"] = await runtime.start()
            atomic_json(path, record)
            for attempt in range(len(record["attempts"]), 2):
                record["attempts"].append(
                    {
                        "index": attempt,
                        "status": "dispatched",
                        "solver_container": record["solver_container"],
                    }
                )
                atomic_json(path, record)
                target = FrozenEnvironment(
                    self.agent_environment,
                    record["solver_container"],
                    image_path=record.get("image_path"),
                )
                # Retry fresh receipts against the same stopped solver.
                await target.exec(
                    "mkdir -p /tests/.home; rm -f /logs/verifier/*"
                )
                for receipt in self.paths.verifier_dir.iterdir():
                    if receipt.is_file():
                        receipt.unlink()
                try:
                    verifier = Verifier(
                        task=self.task,
                        trial_paths=self.paths,
                        environment=target,
                        logger=self.logger,
                        override_env=self.config.verifier.env or None,
                        verifier_env=record["verifier_env"],
                    )
                    result = await asyncio.wait_for(
                        verifier.verify(), timeout_sec
                    )
                    record["attempts"][-1]["status"] = "complete"
                    record.update(
                        complete=True,
                        verifier_result=result.model_dump(mode="json"),
                    )
                    atomic_json(path, record)
                    return result
                except Exception as exc:
                    record["attempts"][-1].update(
                        status="failed", error=type(exc).__name__
                    )
                    atomic_json(path, record)
                    await runtime.quiesce()
            raise FrozenGraderRetryExhausted(
                "Identical-artifact verifier retry exhausted"
            )
        except Exception as exc:
            record["grader_failure"] = type(exc).__name__
            atomic_json(path, record)
            raise FrozenGraderRetryExhausted(type(exc).__name__) from exc
        finally:
            try:
                await runtime.close()
            except FrozenGraderRetryExhausted:
                record.update(complete=False, grader_failure="isolation_audit")
                atomic_json(path, record)
                raise
            append_jsonl(
                private / "events.jsonl",
                {
                    "ts": utc_now(),
                    "event": "live_grading_end",
                    "solver_snapshot": False,
                    "solver_killed": False,
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
