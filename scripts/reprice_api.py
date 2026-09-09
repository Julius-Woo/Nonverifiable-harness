"""Audit and repair cache-write accounting from preserved API responses.

Run only after jobs finish. Original records are preserved in the audit log;
Harbor verifier files and results are never modified. Repeated runs are no-ops.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

from harness.ledger import append_jsonl, price_usage, utc_now
from harness.openai_api import pricing_model


def repair(ledger, prices, audit):
    count, delta = 0, 0.0
    with ledger.open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        rows = [json.loads(line) for line in handle if line.strip()]
        for row in rows:
            if row.get("backend") != "openai_api":
                continue
            source = Path(row["raw_dir"]) / "response.json"
            if not source.exists():
                continue
            raw = source.read_bytes()
            data = json.loads(raw)
            details = (data.get("usage") or {}).get(
                "prompt_tokens_details"
            ) or {}
            written = details.get("cache_write_tokens")
            if written is None or written == row.get("cache_write_tokens"):
                continue
            original = dict(row)
            row["cache_write_tokens"] = written
            row["cost_usd"] = price_usage(
                pricing_model(row["model"], prices), row, prices
            )
            row["accounting_correction"] = {
                "ts": utc_now(),
                "reason": (
                    "Normalize response "
                    "prompt_tokens_details.cache_write_tokens"
                ),
                "original_cache_write_tokens": original["cache_write_tokens"],
                "original_cost_usd": original["cost_usd"],
                "response_sha256": hashlib.sha256(raw).hexdigest(),
            }
            append_jsonl(audit, {"original": original, "corrected": row})
            count += 1
            delta += (row["cost_usd"] or 0) - (original["cost_usd"] or 0)
        if count:
            handle.seek(0)
            handle.write(
                "".join(json.dumps(r, allow_nan=False) + "\n" for r in rows)
            )
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    return count, delta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger", type=Path, default=Path("costs/ledger.jsonl")
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("logs/cache-write-corrections.jsonl"),
    )
    args = parser.parse_args()
    prices = json.loads(Path("scripts/prices.json").read_text())
    count, delta = repair(args.ledger, prices, args.audit)
    print(f"Audited {count} corrected calls; cost adjustment ${delta:.8f}")


if __name__ == "__main__":
    main()
