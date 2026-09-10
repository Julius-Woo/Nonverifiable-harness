"""Disposable Docker environment implementing harness.seed's exec interface."""

import asyncio
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from gdpevo import ROOT
from gdpevo.staging import stage_task

PREFIX = "nvh-gdpevo-"


def image_ref(tag):
    lock = json.loads((ROOT / "docker/gdpevo/images.lock.json").read_text())
    return lock["images"][tag]


def docker(*args, check=True, timeout=120):
    return subprocess.run(
        ["docker", *map(str, args)],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    return_code: int


class Attempt:
    def __init__(self, group, split, task_id, directory: Path):
        self.group, self.split, self.task_id = group, split, task_id
        self.directory = directory.resolve()
        self.name = PREFIX + uuid.uuid4().hex[:12]
        self.containers, self.networks = [], []
        self.tool_failed = False
        self.solver = self.name + "-solver"

    def network(self, suffix):
        name = self.name + suffix
        docker("network", "create", "--internal", name)
        self.networks.append(name)
        return name

    def launch(self, name, network, image, *extra, privileged_init=False):
        command = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--log-opt",
            "max-size=10m",
        ]
        if privileged_init:
            for cap in ("NET_ADMIN", "SETUID", "SETGID", "SETPCAP"):
                command += ["--cap-add", cap]
        # Register before launch so partial startup is still cleaned up.
        self.containers.append(name)
        docker(*command, *extra, image_ref(image))

    @staticmethod
    def ip(name, network):
        info = json.loads(docker("inspect", name).stdout)[0]
        return info["NetworkSettings"]["Networks"][network]["IPAddress"]

    def __enter__(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            self.task = stage_task(
                self.group, self.split, self.task_id, self.directory / "staged"
            )
            scratch = self.directory / "scratch"
            scratch.mkdir()
            self.answer_path = self.directory / "answer.json"
            self.answer_path.touch()
            routes = self.directory / "routes.json"
            routes.write_text(json.dumps(self.task["api"]["routes"]))
            back = self.network("-back")
            front = self.network("-front")
            service = self.name + "-service"
            gateway = self.name + "-gateway"
            self.launch(
                service, back, f"nvh-gdpevo-service-{self.group:03}:v1"
            )
            self.service_ip = self.ip(service, back)
            self.launch(
                gateway,
                back,
                "nvh-gdpevo-gateway:v1",
                "-e",
                f"UPSTREAM_IP={self.service_ip}",
                "--mount",
                f"type=bind,src={routes},dst=/config/routes.json,readonly",
            )
            docker("network", "connect", front, gateway)
            gateway_ip = self.ip(gateway, front)
            staged = self.directory / "staged"
            mounts = []
            for name in ("input", "environment_access.md", "task.json"):
                mounts += [
                    "--mount",
                    f"type=bind,src={staged / name},dst=/work/{name},readonly",
                ]
            mounts += [
                "--mount",
                f"type=bind,src={scratch},dst=/work/scratch",
                "--mount",
                f"type=bind,src={self.answer_path},dst=/work/answer.json",
            ]
            self.launch(
                self.solver,
                front,
                "nvh-gdpevo-solver:v1",
                *mounts,
                "--add-host",
                f"gateway:{gateway_ip}",
                "-e",
                f"GATEWAY_IP={gateway_ip}",
                "-e",
                "TMPDIR=/work/scratch",
                privileged_init=True,
            )
            route = next(
                r["path"]
                for r in self.task["api"]["routes"]
                if r["method"] == "GET"
                and "{" not in r["path"]
                and "<" not in r["path"]
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                probe = docker(
                    "exec",
                    "--user",
                    "1000:1000",
                    self.solver,
                    "curl",
                    "-fsS",
                    "--max-time",
                    "2",
                    "-o",
                    "/dev/null",
                    f"http://gateway:8080{route}",
                    check=False,
                )
                if probe.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Service boundary did not become ready")
            metadata = {
                "containers": [
                    json.loads(docker("inspect", c).stdout)[0]
                    for c in self.containers
                ],
                "networks": [
                    json.loads(docker("network", "inspect", n).stdout)[0]
                    for n in self.networks
                ],
            }
            (self.directory / "boundary.json").write_text(
                json.dumps(metadata, indent=2)
            )
            return self
        except BaseException:
            self.close()
            raise

    async def exec(self, command, timeout_sec=30):
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "--user",
            "1000:1000",
            "--workdir",
            "/work",
            self.solver,
            "timeout",
            "--signal=TERM",
            "--kill-after=1s",
            f"{timeout_sec}s",
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def bounded_read(stream):
            saved = bytearray()
            while chunk := await stream.read(65536):
                if len(saved) < 200_000:
                    saved.extend(chunk[: 200_000 - len(saved)])
            return saved.decode(errors="replace")

        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(
                    bounded_read(process.stdout),
                    bounded_read(process.stderr),
                ),
                timeout_sec + 10,
            )
            await process.wait()
        except BaseException:
            self.tool_failed = True
            process.kill()
            await process.wait()
            # Killing docker exec alone leaves its container child alive.
            docker("kill", self.solver, check=False)
            raise
        if process.returncode:
            self.tool_failed = True
        if process.returncode in (124, 137):
            raise TimeoutError("Container command timed out")
        return ExecResult(stdout, stderr, process.returncode)

    def snapshot(self):
        docker("kill", self.solver, check=False)
        fd = os.open(self.answer_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(4_000_001)
        except BaseException:
            raise
        if len(raw) > 4_000_000:
            raise ValueError("Submission exceeds 4 MB")
        return raw

    def close(self):
        for name in reversed(self.containers):
            log = docker("logs", name, check=False)
            if self.directory.exists():
                (self.directory / f"{name}.log").write_text(
                    log.stdout + log.stderr
                )
            docker("rm", "-f", name, check=False)
        for name in reversed(self.networks):
            docker("network", "rm", name, check=False)
        self.containers.clear()
        self.networks.clear()

    def __exit__(self, *_):
        self.close()
