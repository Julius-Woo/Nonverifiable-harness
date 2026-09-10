"""Audit calibration evidence, prompts, accounting, and boundaries."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from harbor.models.job.config import JobConfig

from harness.seed import API_SYSTEM, NATIVE_SYSTEM, NATIVE_TOOLS
from scripts.calibrate import CONFIGS, ROOT
from scripts.calibration_report import collect
from scripts.cost_report import read_ledger


def main():
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    calls = [row for row in ledger if row.get("phase") == "P1.2"]
    ids = {row["call_id"] for row in calls}
    assert len(ids) == len(calls), "Duplicate calibration ledger records"
    for row in calls:
        request = Path(row["raw_dir"]) / "request.json"
        assert request.exists(), f"Ledger call has no request: {request}"
        assert request.parent.name == row["call_id"]
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    manifest = json.loads(
        (ROOT / "logs/calibration_source_manifest.json").read_text()
    )
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    results = []
    requests = Counter()
    for label, (model, endpoint, protocol) in CONFIGS.items():
        result = collect(label, ledger, split)
        if result is None:
            continue
        assert result["n_results"] == 60, f"Incomplete {label}"
        assert set(Counter(t["task"] for t in result["trials"]).values()) == {
            2
        }
        for job in (ROOT / "logs/harbor").glob(result["run_id"] + "*"):
            config = JobConfig.model_validate_json(
                (job / "config.json").read_text()
            ).model_dump(mode="json")
            assert config["n_concurrent_trials"] == (
                8 if label == "mini-json" else 4
            )
            # Harbor omits default-valued fields in its saved config.
            assert JobConfig.model_validate(config).retry.max_retries == 0
            assert config["agents"][0]["model_name"] == model
            kwargs = config["agents"][0]["kwargs"]
            for key, expected in {
                "backend": "openai_api",
                "tool_protocol": protocol,
                "endpoint_prefix": endpoint,
                "max_steps": 24,
                "reasoning_effort": "low",
                "max_completion_tokens": 4096,
                "api_max_retries": 0,
                "rollout_budget_usd": 1,
                "shared_budget_usd": 40,
            }.items():
                assert kwargs[key] == expected, (job.name, key)
            for request in job.glob("*/agent/calls/*/request.json"):
                assert request.parent.name in ids, str(request)
                payload = json.loads(request.read_text())
                assert payload["model"] == model
                assert payload["reasoning_effort"] == "low"
                assert payload["max_completion_tokens"] == 4096
                if protocol == "native":
                    assert payload["messages"][0] == {
                        "role": "system",
                        "content": NATIVE_SYSTEM,
                    }
                    assert payload["tools"] == NATIVE_TOOLS
                    assert payload["parallel_tool_calls"] is False
                else:
                    assert payload["messages"][0]["content"].startswith(
                        API_SYSTEM + "\nConversation:\n"
                    )
                    assert "tools" not in payload
                requests[label] += 1
        results.append(result)
    known = sum(row.get("cost_usd") or 0 for row in calls)
    orphan_reserves = sum(
        row.get("conservative_reservation_usd", 0) for row in calls
    )
    rollout_budgets = defaultdict(float)
    interrupted_reserves = defaultdict(float)
    for row in calls:
        owner = str(Path(row["raw_dir"]).parent)
        rollout_budgets[owner] = max(
            rollout_budgets[owner], row.get("budget_used_usd", 0)
        )
        interrupted_reserves[owner] += row.get(
            "conservative_reservation_usd", 0
        )
    assert all(
        used + interrupted_reserves[owner] <= 1 + 1e-8
        for owner, used in rollout_budgets.items()
    ), "Per-rollout guard exceeded"
    accounted = sum(rollout_budgets.values()) + orphan_reserves
    guard = json.loads((ROOT / "costs/calibration_budget.json").read_text())
    assert guard["used_usd"] <= 40
    assert abs(accounted - guard["used_usd"]) < 1e-8
    report = {
        "completed_configurations": [r["configuration"] for r in results],
        "finalized_attempts": sum(r["n_results"] for r in results),
        "verifier_rewards": sum(r["n_verifier"] for r in results),
        "audited_requests": dict(requests),
        "ledger_calls": len(calls),
        "known_usd": known,
        "retained_reservations_usd": accounted - known,
        "orphan_reservations_usd": orphan_reserves,
        "guard_used_usd": guard["used_usd"],
        "unknown_cost_calls": sum(r.get("cost_usd") is None for r in calls),
        "source_hashes_match": True,
        "model_specific_prompt": False,
        "two_finalized_attempts_per_task": True,
    }
    (ROOT / "logs/calibration_final_audit.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
