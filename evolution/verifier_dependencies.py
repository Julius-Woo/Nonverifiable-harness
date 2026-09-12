"""Stage verifier-declared packages without writing pristine system mounts."""

import fcntl
import hashlib
import json
import os
import re
import shlex
from pathlib import Path

from evolution.candidates import atomic_json
from evolution.workspace import docker


def declared_packages(tests):
    script = Path(tests) / "test.sh"
    text = script.read_text() if script.exists() else ""
    packages = set()
    for command in re.findall(r"^\s*apt-get install[^\n]*", text, re.M):
        packages.update(
            p for p in shlex.split(command)[2:] if not p.startswith("-")
        )
    if not all(
        re.fullmatch(r"[a-z0-9][a-z0-9+.:-]*(?:=[\w.+:~\-]+)?", p)
        for p in packages
    ):
        raise ValueError("Unsupported verifier package declaration")
    return sorted(packages)


def prepare(image, tests, root, *, packages=None):
    packages = declared_packages(tests) if packages is None else packages
    if not packages:
        return None
    identity = hashlib.sha256(
        json.dumps([image, packages]).encode()
    ).hexdigest()
    directory = Path(root) / "oracle/verifier-dependencies" / identity
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "prepare.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = directory / "receipt.json"
        if receipt.exists():
            return directory / "tree"
        tree = directory / "tree"
        tree.mkdir(exist_ok=True)
        command = (
            "set -eu; mkdir -p /nvh-bundle/debs/partial; "
            "apt-get -o Acquire::Check-Valid-Until=false update; "
            ". /etc/os-release; "
            'apt-get -t "$VERSION_CODENAME" '
            "-o Dir::Cache::archives=/nvh-bundle/debs "
            "--download-only --reinstall install -y "
            + shlex.join(packages)
            + "; for p in /nvh-bundle/debs/*.deb; do "
            'dpkg-deb -x "$p" /nvh-bundle/tree; done; '
            f"chown -R {os.getuid()}:{os.getgid()} /nvh-bundle"
        )
        name = "nvh-r10-deps-" + identity[:20]
        try:
            docker(
                "run",
                "-d",
                "--name",
                name,
                "--entrypoint",
                "/bin/sh",
                "--mount",
                f"type=bind,src={directory.resolve()},dst=/nvh-bundle",
                image,
                "-c",
                command,
            )
            result = docker("wait", name)
            log = docker("logs", name)
            (directory / "prepare.log").write_text(log.stdout + log.stderr)
            if result.stdout.strip() != "0":
                raise RuntimeError(
                    "Verifier package staging failed: " + str(directory)
                )
        finally:
            docker("rm", "-f", name, check=False)
        binary = tree / "bin"
        binary.mkdir(exist_ok=True)
        wrapper = binary / "apt-get"
        wrapper.write_text(
            "#!/bin/sh\n# Original-image packages are staged.\nexit 0\n"
        )
        wrapper.chmod(0o755)
        hashes = {
            str(p.relative_to(tree)): hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
            for p in tree.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        atomic_json(
            receipt,
            {"original_image": image, "packages": packages, "files": hashes},
        )
        return tree
