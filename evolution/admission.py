"""Process-shared Harbor concurrency and memory admissions across all arms."""

import asyncio
import fcntl
import time
from pathlib import Path

from evolution.evaluation import available_gib
from evolution.workspace import docker
from harness.ledger import append_jsonl, utc_now


class HarborAdmission:
    def __init__(self, root, trial_id):
        self.root, self.trial_id = Path(root), trial_id
        self.directory = self.root / "logs/evolution-admission"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handles = [
            (self.directory / f"slot-{i}.lock").open("a+") for i in range(4)
        ]
        self.owned = None
        self.gate = (self.directory / "admission.lock").open("a+")

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
                # Count earlier workers and orphaned task containers.
                untracked = own_containers - claimed
                limit = max(0, (2 if foreign else 4) - len(untracked))
                if memory >= 6 and len(claimed | untracked) < (
                    2 if foreign else 4
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
                                "available_gib": memory,
                                "foreign_tb2": foreign,
                                "global_limit": 2 if foreign else 4,
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
