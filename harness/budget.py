"""Process-safe experiment reservations, retained on ambiguous requests."""

import fcntl
import json
import os
from pathlib import Path


def adjust_budget(path, delta, limit):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        state = json.loads(handle.read() or '{"used_usd": 0}')
        used = state["used_usd"] + delta
        if used > limit + 1e-10:
            raise ValueError("Projected experiment budget exceeded")
        state.update(used_usd=used, limit_usd=limit)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(state))
        handle.flush()
        os.fsync(handle.fileno())
        return used
