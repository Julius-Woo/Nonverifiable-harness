"""Process-shared Harbor concurrency and memory admissions across all arms."""

import asyncio
import fcntl
import time
from pathlib import Path

from evolution.evaluation import available_gib
from evolution.workspace import docker
from harness.ledger import append_jsonl, utc_now


class HarborAdmission:
    def __init__(self, root, trial_id, limit=4, scope=None):
        self.root, self.trial_id = Path(root), trial_id
        self.limit = min(4, max(1, limit))
        self.scope = scope
        self.shared_directory = self.root / "logs/evolution-admission"
        self.shared_directory.mkdir(parents=True, exist_ok=True)
        if scope and Path(scope).name != scope:
            raise ValueError("Admission scope must be an experiment name")
        self.directory = (
            self.shared_directory / scope if scope else self.shared_directory
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handles = [
            (self.directory / f"slot-{i}.lock").open("a+") for i in range(4)
        ]
        self.owned = None
        self.gate = (self.shared_directory / "admission.lock").open("a+")

    def memory_ready(self, memory):
        """Shared hysteresis: pause below 6 GiB; resume strictly above 8."""
        marker = self.shared_directory / "memory-paused"
        if memory < 6:
            marker.touch(exist_ok=True)
        elif memory > 8:
            marker.unlink(missing_ok=True)
        return not marker.exists()

    async def __aenter__(self):
        started = time.monotonic()
        # Give existing waiters one polling interval before a new arrival.
        # Otherwise a fast replacement process can repeatedly jump the queue.
        await asyncio.sleep(2.1)
        while True:
            fcntl.flock(self.gate, fcntl.LOCK_EX)
            try:
                memory = available_gib()
                active = docker(
                    "ps", "--format", "{{.Names}} {{.Image}}"
                ).stdout.splitlines()
                foreign = any(
                    "alexgshaw/" in row and not row.startswith("nvhe-")
                    for row in active
                )
                own_containers = {
                    row.split("__env", 1)[0]
                    for row in active
                    if row.startswith("nvhe-") and "__env" in row
                }
                claimed = set()
                for handle in self.handles:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        handle.seek(0)
                        claimed.add(handle.read().strip())
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                total_containers = len(own_containers)
                if self.scope:
                    # Other experiments have independent limits. Keep the
                    # shared memory guard and a seven-task host ceiling for
                    # qualification (three) plus calibration (four).
                    configs = self.root / "logs/evolution" / self.scope
                    own_containers = {
                        identity
                        for identity in own_containers
                        if any(configs.glob(f"*/configs/{identity}.json"))
                    }
                    foreign = False
                # Count earlier workers and orphaned task containers.
                untracked = own_containers - claimed
                limit = max(
                    0,
                    (min(2, self.limit) if foreign else self.limit)
                    - len(untracked),
                )
                host_ready = not self.scope or total_containers < 7
                if (
                    host_ready
                    and self.memory_ready(memory)
                    and len(claimed | untracked)
                    < (min(2, self.limit) if foreign else self.limit)
                ):
                    for handle in self.handles[:limit]:
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        handle.seek(0)
                        handle.truncate()
                        handle.write(self.trial_id)
                        handle.flush()
                        self.owned = handle
                        append_jsonl(
                            self.directory / "admissions.jsonl",
                            {
                                "ts": utc_now(),
                                "trial_id": self.trial_id,
                                "scope": self.scope,
                                "available_gib": memory,
                                "foreign_tb2": foreign,
                                "global_limit": min(2, self.limit)
                                if foreign
                                else self.limit,
                                "untracked_own_containers": len(untracked),
                                "wait_s": time.monotonic() - started,
                            },
                        )
                        return self
            finally:
                fcntl.flock(self.gate, fcntl.LOCK_UN)
            await asyncio.sleep(2)

    async def __aexit__(self, *_):
        if self.owned:
            self.owned.seek(0)
            self.owned.truncate()
            self.owned.flush()
            fcntl.flock(self.owned, fcntl.LOCK_UN)
        for handle in self.handles:
            handle.close()
        self.gate.close()
