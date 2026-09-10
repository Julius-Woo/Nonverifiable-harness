"""Ledger interrupted requests without inventing usage or responses."""

import json
from datetime import datetime, timezone

from harness.backends import TOKEN_FIELDS
from harness.ledger import append_jsonl
from harness.openai_api import OpenAIAPIBackend
from scripts.calibrate import ROOT
from scripts.calibration_cohorts import load_manifest, scoped_calls
from scripts.cost_report import read_ledger


def main():
    ledger = ROOT / "costs/ledger.jsonl"
    manifest = load_manifest()
    scoped = scoped_calls(read_ledger(ledger), manifest, "original-six")
    known = {row.get("call_id") for row in scoped}
    audit = []
    # Only the two stopped jobs: never reconcile requests from a live job.
    for run_id in (
        "calibration-mini-native-260910",
        "calibration-mini-native-260910-resume",
    ):
        run = next(r for r in manifest["runs"] if r["job_name"] == run_id)
        assert run["cohort"] == "original-six"
        assert run["budget_guard"] == "costs/calibration_budget.json"
        job = ROOT / "logs/harbor" / run_id
        for request in sorted(job.glob("*/agent/calls/*/request.json")):
            if request.parent.name in known:
                continue
            assert not (request.parent / "response.json").exists()
            payload = json.loads(request.read_text())
            backend = OpenAIAPIBackend(
                base_url="https://unused.invalid",
                api_key="unused",
                model=payload["model"],
                ledger=ledger,
                logs_dir=request.parent,
            )
            reservation = backend.projected_cost(json.dumps(payload))
            row = {
                "phase": run["phase"],
                "run_id": run_id,
                "arm": run["configuration"],
                "iteration": 0,
                "task": request.parents[3].name.rsplit("__", 1)[0],
                "role": "task",
                "backend": "openai_api",
                "call_id": request.parent.name,
                "ts": datetime.fromtimestamp(
                    request.stat().st_mtime, timezone.utc
                ).isoformat(),
                "timestamp_source": "request artifact mtime",
                "requested_model": payload["model"],
                "model": payload["model"],
                "served_model": None,
                **dict.fromkeys(TOKEN_FIELDS),
                "cost_usd": None,
                "cost_source": "unknown",
                "premium_requests": 0,
                "ok": False,
                "note": "Watchdog interruption: request artifact exists; "
                "dispatch, HTTP status, response, usage and elapsed time "
                "unknown. Existing shared budget reservation retained.",
                "raw_dir": str(request.parent.resolve()),
                "price_table_date": backend.prices["checked_at"],
                "attempts": [],
                "http_429s": 0,
                "request_params": {
                    key: payload[key]
                    for key in ("reasoning_effort", "max_completion_tokens")
                },
                "reconstructed_after_interruption": True,
                "conservative_reservation_usd": reservation,
            }
            append_jsonl(ledger, row)
            audit.append(row)
    audit_path = ROOT / "logs/calibration_ledger_recovery.jsonl"
    for row in audit:
        append_jsonl(audit_path, row)
    print(
        json.dumps(
            {
                "recovered_records": len(audit),
                "retained_reservations_usd": sum(
                    row["conservative_reservation_usd"] for row in audit
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
