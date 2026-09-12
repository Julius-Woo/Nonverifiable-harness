"""Offline R7 audit of both calibration cohorts and immutable raw evidence."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from harbor.models.job.config import JobConfig

from harness.ledger import price_usage
from harness.seed import API_SYSTEM, NATIVE_SYSTEM, NATIVE_TOOLS
from scripts.calibrate import ALL_CONFIGS, ROOT
from scripts.calibration_cohorts import account, load_manifest, scoped_calls
from scripts.calibration_report import collect
from scripts.calibration_split_check import compare
from scripts.cost_report import read_ledger


def audit(root=ROOT):
    manifest = load_manifest(root / "data/calibration_run_manifest.json")
    ledger = read_ledger(root / "costs/ledger.jsonl")
    calls = scoped_calls(ledger, manifest)
    ids = {r["call_id"] for r in calls}
    source_manifest = json.loads(
        (root / "logs/calibration_source_manifest.json").read_text()
    )
    source_checks = {}
    for name, digest in source_manifest["files"].items():
        source = root / name
        if name == "harness/openai_api.py":
            source = (
                root / "logs/calibration-r7-reanalysis/sources/openai_api.py"
            )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
        source_checks[name] = {
            "historical_sha256": digest,
            "verified_source": str(source.relative_to(root)),
            "current_sha256": hashlib.sha256(
                (root / name).read_bytes()
            ).hexdigest(),
        }
    allowance_sources = json.loads(
        (
            root / "logs/calibration-8k-followup-260910/source_manifest.json"
        ).read_text()
    )
    for name, digest in allowance_sources["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    split = json.loads((root / "data/tb2_split.json").read_text())
    results = [
        collect(label, ledger, split, manifest) for label in ALL_CONFIGS
    ]
    assert all(r and r["n_results"] == 60 for r in results)
    request_ids = set()
    requests_by_run = {}
    interrupted = []
    config_map = ALL_CONFIGS
    for run in manifest["runs"]:
        run_id = run["job_name"]
        if run["kind"] == "diagnostic":
            paths = sorted(
                (root / "logs" / run_id).glob("calls/*/request.json")
            )
        else:
            job = root / "logs/harbor" / run_id
            config_path = job / "config.json"
            assert (
                hashlib.sha256(config_path.read_bytes()).hexdigest()
                == run["job_config_sha256"]
            )
            config = JobConfig.model_validate_json(config_path.read_text())
            model, endpoint, protocol = config_map[run["configuration"]]
            assert config.n_concurrent_trials == run["nominal_concurrency"]
            assert config.retry.max_retries == 0
            assert config.agents[0].model_name == model
            kwargs = config.agents[0].kwargs
            for key, expected in {
                "backend": "openai_api",
                "tool_protocol": protocol,
                "endpoint_prefix": endpoint,
                "max_steps": 24,
                "reasoning_effort": run["reasoning_effort"],
                "max_completion_tokens": run.get(
                    "max_completion_tokens", 4096
                ),
                "api_max_retries": 0,
                "rollout_budget_usd": 1,
                "shared_budget_usd": run["budget_usd"],
                "phase": run["phase"],
                "run_id": run_id,
            }.items():
                assert kwargs[key] == expected, (run_id, key)
            assert (
                Path(kwargs["shared_budget_path"]).name
                == Path(run["budget_guard"]).name
            )
            paths = sorted(job.glob("*/agent/calls/*/request.json"))
            for trial in sorted(job.iterdir()):
                if (
                    trial.is_dir()
                    and "__" in trial.name
                    and not (trial / "result.json").exists()
                ):
                    interrupted.append(
                        {
                            "job_name": run_id,
                            "trial": trial.name,
                            "request_count": len(
                                list(trial.glob("agent/calls/*/request.json"))
                            ),
                        }
                    )
        requests_by_run[run_id] = len(paths)
        for path in paths:
            call_id = path.parent.name
            assert call_id not in request_ids, (
                f"Duplicate request artifact UUID: {call_id}"
            )
            request_ids.add(call_id)
            payload = json.loads(path.read_text())
            assert payload["reasoning_effort"] == run["reasoning_effort"]
            assert payload["max_completion_tokens"] == run.get(
                "max_completion_tokens", 4096
            )
            assert not {"temperature", "seed"} & payload.keys()
            if run["kind"] == "benchmark":
                assert payload["model"] == model
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
    assert request_ids == ids, {
        "missing_ledger": sorted(request_ids - ids),
        "missing_request": sorted(ids - request_ids),
    }
    prices = json.loads((root / "scripts/prices.json").read_text())
    budgets = defaultdict(float)
    orphan_reserves = defaultdict(float)
    for row in calls:
        raw = Path(row["raw_dir"])
        assert raw.name == row["call_id"] and (raw / "request.json").exists()
        owner = str(raw.parent)
        budgets[owner] = max(budgets[owner], row.get("budget_used_usd", 0))
        orphan_reserves[owner] += row.get("conservative_reservation_usd", 0)
        response = raw / "response.json"
        if response.exists():
            data = json.loads(response.read_text())
            usage = data["usage"]
            assert usage["prompt_tokens"] == row["input_tokens"]
            assert usage["completion_tokens"] == row["output_tokens"]
            assert data["choices"][0].get("finish_reason") == row.get(
                "finish_reason"
            )
            if row.get("cost_usd") is not None:
                assert (
                    abs(
                        price_usage(row["pricing_model"], row, prices)
                        - row["cost_usd"]
                    )
                    < 1e-10
                )
    assert all(
        value + orphan_reserves[owner] <= 1 + 1e-8
        for owner, value in budgets.items()
    )
    for result in results:
        assert set(Counter(t["task"] for t in result["trials"]).values()) == {
            2
        }
        assert sum(result["termination_counts"].values()) == 60
        assert (
            sum(result["no_action_reasons"].values())
            == result["no_action_count"]
        )
        assert not any(
            t["action_evidence_uncertain"] for t in result["trials"]
        )
        for trial in result["trials"]:
            assert (
                trial["pass_l2"]
                <= trial["pass_l1"]
                <= int(trial["reward"] == 1)
            )
            assert trial["no_action"] == (not trial["action_evidence"])
            # Validate the stated contract without a trial-exception shortcut.
            assert trial["pass_l1"] == int(
                trial["reward"] == 1
                and not trial["agent_timeout"]
                and not trial["tool_failure_l1"]
                and not trial["token_exhaustion"]
                and not trial["step_exhaustion"]
                and not trial["budget_exhaustion"]
            )
            assert trial["pass_l2"] == int(
                trial["pass_l1"] and not trial["nonzero_commands"]
            )
            assert trial["pass_l1_prime"] == int(
                type(trial["reward"]) in (int, float)
                and trial["reward"] == 1
                and not trial["agent_timeout"]
                and trial["termination"] == "normal_finish"
                and not trial["token_exhaustion"]
                and not trial["step_exhaustion"]
                and not trial["budget_exhaustion"]
            )
            assert trial["infrastructure_excluded"] == (
                trial["termination"] == "api_timeout_infrastructure"
            )
            if trial["termination"] == "content_policy_rejection":
                assert not trial["infrastructure_excluded"]
                assert trial["pass_l1_prime"] == 0
        metrics = result["labels"]["pass_l1_prime"]
        assert metrics["denominator"] + metrics["excluded"] == 60
        assert all(
            m["denominator"] == metrics["denominator"]
            for m in result["standing_metrics"].values()
        )
    accounting = account(ledger, manifest, results, root)
    for cohort, totals in accounting.items():
        assert (
            totals["finalized_attempts"]
            == manifest["cohorts"][cohort]["expected_finalized_attempts"]
        )
    for exception in manifest["operator_replacement_exceptions"]:
        assert exception["operator_replacements"] == 2
        assert [r["status"] for r in exception["lineage"]] == [
            "interrupted",
            "interrupted",
            "finalized",
        ]
        for entry in exception["lineage"]:
            path = root / entry["evidence"]
            assert path.exists()
            assert (path / "result.json").exists() == (
                entry["status"] == "finalized"
            )
    report = {
        "cohorts": accounting,
        "request_uuid_bijection": True,
        "requests_by_run": requests_by_run,
        "total_request_artifacts": len(request_ids),
        "interrupted_attempts": interrupted,
        "two_finalized_attempts_per_task": True,
        "ratified_labels_and_denominators": True,
        "infrastructure_exclusions": {
            r["configuration"]: r["labels"]["pass_l1_prime"]["excluded"]
            for r in results
        },
        "missing_infrastructure_retry_deviations": [
            t["result_path"]
            for r in results
            for t in r["trials"]
            if t["infrastructure_excluded"] and not t["api_retries_observed"]
        ],
        "payload_prompt_and_budget_checks": True,
        "response_usage_and_cost_checks": True,
        "action_presence_uncertain_attempts": 0,
        "source_checks": source_checks,
        "allowance_source_checks": allowance_sources,
        "backend_change": (
            "HTTP error-body archiving only; "
            "historical bytes verified from archive"
        ),
        "split_check": compare(
            root / "data/tb2_population.json", root / "data/tb2_split.json"
        ),
    }
    return report


def main():
    report = audit()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
