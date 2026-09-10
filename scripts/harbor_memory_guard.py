"""Invoke Harbor with memory admission control, without changing trials."""

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from harbor.cli.main import app
from harbor.trial.queue import TrialQueue


def snapshot(include_docker=True):
    free = subprocess.check_output(["free", "-g"], text=True)
    available = (
        int(
            next(
                line
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            ).split()[1]
        )
        / 1024**2
    )
    stats = ""
    if include_docker:
        try:
            stats = subprocess.check_output(
                ["docker", "stats", "--no-stream"], text=True, timeout=30
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            # Preserve admission monitoring even if the stats command fails.
            stats = f"docker stats unavailable: {type(exc).__name__}"
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "available_gb": available,
        "admission": "pause"
        if available < 6
        else "ready"
        if available >= 10
        else "hold",
        "free": free,
        "docker_stats": stats,
    }
    with Path(os.environ["CALIBRATION_MEMORY_LOG"]).open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    return available


class AdmissionGuard:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.monitor_task = None

    def observe(self, available):
        if available < 6:
            self.ready.clear()
        elif available >= 10:
            self.ready.set()

    async def monitor(self):
        while True:
            available = await asyncio.to_thread(snapshot)
            self.observe(available)
            await asyncio.sleep(120)

    async def wait(self):
        if self.monitor_task is None:
            self.monitor_task = asyncio.create_task(self.monitor())
        # Also check admission between periodic samples to catch fast drops.
        available = await asyncio.to_thread(snapshot, False)
        self.observe(available)
        await self.ready.wait()


def main():
    while snapshot() < 10:
        import time

        time.sleep(30)
    guard = AdmissionGuard()
    original = TrialQueue._execute_trial_with_retries

    async def guarded(queue, config):
        await guard.wait()
        return await original(queue, config)

    TrialQueue._execute_trial_with_retries = guarded
    app()


if __name__ == "__main__":
    main()
