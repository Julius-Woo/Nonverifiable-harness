"""Aggregate the ledger without hiding unknown costs or failed calls."""

import argparse
import fcntl
import json
from collections import defaultdict
from pathlib import Path

from harness.ledger import price_usage


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open() as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("expected an object")
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            records.append(record)
    return records


def render_report(records: list[dict]) -> str:
    prices = json.loads((Path(__file__).with_name("prices.json")).read_text())
    records = [dict(r) for r in records]
    repriced = 0
    for record in records:
        if (
            record.get("cost_usd") is None
            and record.get("backend") in ("codex", "copilot")
            and record.get("price_table_date") == prices["checked_at"]
            and not record.get("mixed_models")
        ):
            cost = price_usage(record["model"], record, prices)
            if cost is not None:
                record["cost_usd"] = cost
                repriced += 1
    groups = defaultdict(list)
    for record in records:
        key = tuple(
            str(record.get(k, "unknown"))
            for k in ("phase", "arm", "role", "model")
        )
        groups[key].append(record)
    lines = [
        "# Cost summary",
        "",
        "API-equivalent USD estimates are separate from subscription charges.",
        "Known USD is a subtotal; unknown costs are not treated as free.",
        "Wall time sums model calls and is not elapsed rollout time.",
        f"Previously unpriced calls resolved with the same-date price table: "
        f"{repriced}. Original ledger records are retained.",
        "",
        "| Phase | Arm | Role | Model | Calls | Failed | Input | Cached | "
        "Output | Reasoning | Known USD | Unknown USD calls | Premium | "
        "Wall s |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: |",
    ]

    def count(rows, key):
        values = [r.get(key) for r in rows]
        total = sum(v for v in values if v is not None)
        missing = sum(v is None for v in values)
        return str(total) + (f" (+{missing} unknown)" if missing else "")

    for key, rows in sorted(groups.items()):
        usd = sum(r.get("cost_usd") or 0 for r in rows)
        unknown = sum(r.get("cost_usd") is None for r in rows)
        cells = [*key, str(len(rows)), str(sum(not r["ok"] for r in rows))]
        cells += [
            count(rows, k)
            for k in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
            )
        ]
        cells += [
            f"{usd:.6f}",
            str(unknown),
            count(rows, "premium_requests"),
            f"{sum(r.get('wall_s', 0) for r in rows):.3f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if not records:
        lines += ["", "No model calls recorded in this ledger."]
    lines += [
        "",
        f"Recorded calls: {len(records)}. Known USD subtotal: "
        f"${sum(r.get('cost_usd') or 0 for r in records):.6f}. "
        f"Unknown USD calls: "
        f"{sum(r.get('cost_usd') is None for r in records)}.",
        "",
        "Source rates and verification date: `scripts/prices.json`.",
        "Raw evidence: each ledger record's `raw_dir`. "
        "Rollout timings: `logs/<run_id>/timing.jsonl`.",
        "",
    ]
    local_stops = sum(
        r.get("backend") == "openai_api"
        and r.get("cost_usd") is None
        and r.get("attempts") == []
        and r.get("note") in {
            "Projected rollout budget exceeded",
            "Projected experiment budget exceeded",
        }
        for r in records
    )
    if local_stops:
        lines += [
            f"The unknown-USD record count includes {local_stops} local "
            "budget rejection(s) with no HTTP attempt. These incurred "
            "zero incremental API usage; original null-cost ledger "
            "records are retained.",
            "",
        ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger", type=Path, default=Path("costs/ledger.jsonl")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("costs/summary.md")
    )
    args = parser.parse_args()
    report = render_report(read_ledger(args.ledger))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report)


if __name__ == "__main__":
    main()
