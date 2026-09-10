"""Mount-allowlisted, networkless containers for edits and candidate code."""

import asyncio
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evolution.candidates import atomic_json
from harness.ledger import append_jsonl, utc_now

IMAGE = "nvh-evolution-runtime:v1"


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *map(str, args)],
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
        env={**os.environ, "DOCKER_BUILDKIT": "0"},
    )


def ensure_image(root):
    docker("build", "-t", IMAGE, Path(root) / "evolution/runtime")
    return docker(
        "image", "inspect", IMAGE, "--format", "{{.Id}}"
    ).stdout.strip()


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    return_code: int


class Workspace:
    def __init__(
        self, candidate, name, *, feedback=None, writable=False, audit=None
    ):
        self.candidate, self.name = Path(candidate).resolve(), name
        self.feedback = Path(feedback).resolve() if feedback else None
        self.writable, self.audit = writable, Path(audit) if audit else None

    def __enter__(self):
        command = [
            "run",
            "-d",
            "--name",
            self.name,
            "--network",
            "none",
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
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--mount",
            f"type=bind,src={self.candidate},dst=/candidate"
            + ("" if self.writable else ",readonly"),
        ]
        if self.feedback:
            command += [
                "--mount",
                f"type=bind,src={self.feedback},dst=/feedback,readonly",
            ]
        docker(*command, IMAGE)
        info = json.loads(docker("inspect", self.name).stdout)[0]
        mounts = info["Mounts"]
        expected = {str(self.candidate): self.writable}
        if self.feedback:
            expected[str(self.feedback)] = False
        actual = {m["Source"]: m["RW"] for m in mounts if m["Type"] == "bind"}
        if actual != expected or info["HostConfig"]["NetworkMode"] != "none":
            self.close()
            raise RuntimeError("Container mount/network boundary mismatch")
        if self.audit:
            atomic_json(
                self.audit,
                {
                    "name": self.name,
                    "image": info["Image"],
                    "mounts": mounts,
                    "network": info["HostConfig"]["NetworkMode"],
                    "user": info["Config"]["User"],
                    "environment": info["Config"]["Env"],
                    "cap_drop": info["HostConfig"]["CapDrop"],
                    "read_only_root": info["HostConfig"]["ReadonlyRootfs"],
                },
            )
        return self

    async def exec(self, command, timeout_sec=30):
        timeout_sec = min(30, max(1, float(timeout_sec)))
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "--workdir",
            "/candidate",
            self.name,
            "timeout",
            "--kill-after=1",
            str(timeout_sec),
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def read(stream):
            data = bytearray()
            while chunk := await stream.read(65536):
                if len(data) < 200000:
                    data.extend(chunk[: 200000 - len(data)])
            return data.decode(errors="replace")

        out, err = await asyncio.wait_for(
            asyncio.gather(
                read(proc.stdout),
                read(proc.stderr),
            ),
            timeout_sec + 10,
        )
        await proc.wait()
        return ExecResult(out, err, proc.returncode)

    async def canary_check(self, paths, hidden_values, log):
        rows = []
        for path in paths:
            # Actual file/shell tool commands, known paths and aliases.
            for command in (
                f"cat -- {shlex.quote(str(path))}",
                f"cat -- /proc/1/root{shlex.quote(str(path))}",
            ):
                result = await self.exec(command)
                row = {
                    "ts": utc_now(),
                    "kind": "boundary_probe",
                    "container": self.name,
                    "command": command,
                    "denied": result.return_code != 0,
                    "content_absent": all(
                        v not in result.stdout + result.stderr
                        for v in hidden_values
                    ),
                }
                append_jsonl(Path(log), row)
                rows.append(row)
        # A write must also fail, not just an absent-path read.
        for path in paths[-1:]:
            result = await self.exec(
                f"printf intrusion > {shlex.quote(str(path))}"
            )
            row = {
                "ts": utc_now(),
                "kind": "cross_arm_write",
                "container": self.name,
                "denied": result.return_code != 0,
                "content_absent": True,
            }
            append_jsonl(Path(log), row)
            rows.append(row)
        return all(r["denied"] and r["content_absent"] for r in rows)

    async def import_check(self):
        result = await self.exec(
            "PYTHONDONTWRITEBYTECODE=1 python3 -c "
            "'import inspect; from harness.seed import run_seed; "
            "assert inspect.iscoroutinefunction(run_seed)'"
        )
        return {
            "ok": result.return_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def close(self):
        docker("rm", "-f", self.name, check=False)

    def __exit__(self, *_):
        self.close()
