"""One stable Harbor 0.22 Trial, without Harbor's whole-trial retry layer."""

import asyncio
import fcntl
import json
import os
import signal
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from harbor.models.trial.config import TrialConfig

from evolution.admission import HarborAdmission
from evolution.grading import IsolatedTrial as Trial


@asynccontextmanager
async def trial_lease(path):
    """Serialize a stable trial ID across surviving and resumed workers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def signal_controller_once(directory, pause, controller_pid, identity):
    """Elect one signaling child and never signal an orphan's PID 1 parent."""
    if controller_pid <= 1 or os.getppid() != controller_pid:
        return False
    claim = directory / (
        f"pause-signal-{controller_pid}-{pause.stat().st_mtime_ns}.json"
    )
    try:
        with claim.open("x") as handle:
            json.dump(
                {"controller_pid": controller_pid, "trial_id": identity},
                handle,
            )
    except FileExistsError:
        return False
    try:
        os.kill(controller_pid, signal.SIGINT)
    except ProcessLookupError:
        return False
    return True


def require_controller(controller_pid):
    if controller_pid <= 1 or os.getppid() != controller_pid:
        raise RuntimeError("Orphaned trial worker cannot dispatch")


async def main():
    controller_pid = os.getppid()
    require_controller(controller_pid)
    config = TrialConfig.model_validate_json(Path(sys.argv[1]).read_text())
    root = Path(__file__).resolve().parents[1]
    kwargs = config.agent.kwargs
    pause = root / "runs" / kwargs["experiment"] / kwargs["arm"] / "pause.json"
    next_pause = pause.with_name("pause-next.json")
    if next_pause.exists():
        from evolution.state import State

        state = State(pause.parent / "state.sqlite")
        spec = json.loads(state.row(config.trial_name)["spec"])
        state.close()
        policy = json.loads(next_pause.read_text())
        if spec["stage"] in policy.get("stages", []):
            pause = next_pause
    if pause.exists():
        # Only our owning controller receives the signal, from its own child
        # process namespace, before any Harbor environment or API dispatch.
        from evolution.state import State
        from harness.ledger import append_jsonl, utc_now

        state = State(pause.parent / "state.sqlite")
        state.db.execute(
            "UPDATE trials SET status='pending' WHERE id=?",
            (config.trial_name,),
        )
        state.db.commit()
        state.close()
        append_jsonl(
            pause.parent / "pause.events.jsonl",
            {
                "ts": utc_now(),
                "trial_id": config.trial_name,
                "event": "paused_before_harbor_dispatch",
            },
        )
        if json.loads(pause.read_text()).get("drain", False):
            # New admissions wait until all earlier trials in this arm have
            # finished. Then the parent can pause without killing a rollout.
            state = State(pause.parent / "state.sqlite")
            quiet = 0
            judge_path = (
                root
                / "logs/evolution"
                / kwargs["experiment"]
                / kwargs["arm"]
                / "judges/queue.sqlite"
            )
            try:
                while quiet < 3:
                    active = state.db.execute(
                        "SELECT count(*) FROM trials WHERE status='running'"
                    ).fetchone()[0]
                    if judge_path.exists():
                        with sqlite3.connect(judge_path) as judges:
                            active += judges.execute(
                                "SELECT count(*) FROM items "
                                "WHERE status IN ('pending','running')"
                            ).fetchone()[0]
                    quiet = 0 if active else quiet + 1
                    await asyncio.sleep(1)
            finally:
                state.close()
        signal_controller_once(
            pause.parent, pause, controller_pid, config.trial_name
        )
        return
    lease = root / "logs/evolution-trial-leases" / f"{config.trial_name}.lock"
    async with trial_lease(lease):
        if (config.trials_dir / config.trial_name / "result.json").exists():
            return
        async with HarborAdmission(
            Path(__file__).resolve().parents[1], config.trial_name
        ):
            # A parent can disappear during a long admission wait.
            require_controller(controller_pid)
            trial = await Trial.create(config)
            await trial.run()
        # Harbor mounts agent logs into the task container. Keep trusted
        # evidence outside that mount until the task container is gone.
        # Atomic replacement also prevents a solver-created symlink redirect.
        source = getattr(trial.agent, "evidence_dir", None)
        if source:
            destination = config.trials_dir / config.trial_name / "agent"
            destination.mkdir(parents=True, exist_ok=True)
            for name in (
                "trace.jsonl",
                "execution.json",
                "final.txt",
                "runtime_boundary.json",
            ):
                path = source / name
                if path.exists():
                    temporary = destination / f".trusted-{os.getpid()}-{name}"
                    # Teardown precedes export, preventing writer races.
                    temporary.unlink(missing_ok=True)
                    temporary.write_bytes(path.read_bytes())
                    os.replace(temporary, destination / name)


if __name__ == "__main__":
    asyncio.run(main())
