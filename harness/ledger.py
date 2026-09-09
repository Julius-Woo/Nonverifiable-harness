"""Process-safe accounting; unavailable usage is null, never invented zero."""

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(record, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class CallTags:
    phase: str = "P0.3"
    run_id: str = "backend-probe"
    arm: str = "A0"
    iteration: int = 0
    task: str = "probe"
    role: str = "task"


def price_usage(model: str, usage: dict, prices: dict) -> float | None:
    """Price short-context standard usage, with output including reasoning.

    CLI totals cannot resolve long-context pricing per provider request. Leave
    aggregates above the threshold unknown rather than apply a wrong tier.
    """
    rates = prices["models"].get(model)
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
    )
    if rates is None or any(usage.get(key) is None for key in fields):
        return None
    total, cached, written, output = (usage[key] for key in fields)
    if min(total, cached, written, output) < 0 or cached + written > total:
        raise ValueError("Invalid normalized token counts")
    if total > 272_000:
        return None
    return (
        (total - cached - written) * rates["input"]
        + cached * rates["cached_input"]
        + written * rates["cache_write"]
        + output * rates["output"]
    ) / 1_000_000
