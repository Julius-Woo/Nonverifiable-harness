"""Reconcile Harbor rewards with trajectories and the call ledger."""

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from harness.ledger import price_usage
from scripts.cost_report import read_ledger

ROOT = Path(__file__).resolve().parents[1]
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def elapsed(timing):
    return (
        timestamp(timing["finished_at"]) - timestamp(timing["started_at"])
    ).total_seconds()


def peak_overlap(intervals):
    events = [(start, 1) for start, end in intervals]
    events += [(end, -1) for start, end in intervals]
    current = peak = 0
    for _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    return peak


def percentile(values, fraction):
    values = sorted(values)
    return (
        values[max(0, math.ceil(len(values) * fraction) - 1)]
        if values
        else None
    )


def collect(root, run_id, ledger):
    run_dir = root / "logs" / run_id
    summary = json.loads((run_dir / "summary.json").read_text())
    command = json.loads((run_dir / "command.json").read_text())
    calls = [r for r in ledger if r["run_id"] == run_id]
    prices = json.loads((root / "scripts/prices.json").read_text())
    for row in calls:
        source = Path(row["raw_dir"]) / "response.json"
        raw = json.loads(source.read_text())
        assert row["model"] == raw["model"]
        usage = raw["usage"]
        details = usage.get("prompt_tokens_details") or {}
        assert row["input_tokens"] == usage["prompt_tokens"]
        assert row["output_tokens"] == usage["completion_tokens"]
        assert row["cached_input_tokens"] == details.get("cached_tokens")
        assert row["cache_write_tokens"] == details.get(
            "cache_write_tokens", details.get("cache_creation_tokens", 0)
        )
        assert row["reasoning_tokens"] == usage.get(
            "completion_tokens_details", {}
        ).get("reasoning_tokens")
        assert math.isclose(
            row["cost_usd"],
            price_usage(row["pricing_model"], row, prices),
            abs_tol=1e-10,
        )
    by_id = {r["call_id"]: r for r in calls}
    assert len(by_id) == len(calls), "Duplicate ledger call IDs"
    job_dir = root / "logs/harbor" / run_id
    trials, seen = [], set()
    intervals = []
    for result_path in sorted(job_dir.glob("*/result.json")):
        data = json.loads(result_path.read_text())
        trial = result_path.parent
        trace = read_ledger(trial / "agent/trace.jsonl")
        assistant = [r for r in trace if r["kind"] == "assistant"]
        ids = [r["call_id"] for r in assistant]
        rows = [by_id[call_id] for call_id in ids]
        assert len(set(ids)) == len(ids), "Duplicate trajectory calls"
        assert all(r["task"] == data["task_name"] for r in rows)
        assert all(
            r.get("model") == by_id[r["call_id"]]["model"] for r in assistant
        )
        seen.update(ids)
        totals = {
            k: sum(r[k] for r in rows)
            if all(r.get(k) is not None for r in rows)
            else None
            for k in TOKEN_KEYS
        }
        costs = [r.get("cost_usd") for r in rows]
        cost = sum(costs) if None not in costs else None
        agent = data.get("agent_result") or {}
        for field, key in (
            ("n_input_tokens", "input_tokens"),
            ("n_cache_tokens", "cached_input_tokens"),
            ("n_output_tokens", "output_tokens"),
        ):
            assert agent.get(field) == totals[key], f"Harbor {field} mismatch"
        original_cost = sum(
            r.get("accounting_correction", {}).get(
                "original_cost_usd", r["cost_usd"]
            )
            for r in rows
        )
        assert math.isclose(agent["cost_usd"], original_cost, abs_tol=1e-10)
        rewards = (data.get("verifier_result") or {}).get("rewards")
        reward_path = trial / "verifier/reward.txt"
        reward = rewards.get("reward") if rewards else None
        assert reward_path.exists(), "Verifier reward file missing"
        assert float(reward_path.read_text()) == reward
        timing = data.get("agent_execution")
        if timing and timing.get("finished_at"):
            intervals.append(
                (
                    timestamp(timing["started_at"]),
                    timestamp(timing["finished_at"]),
                )
            )
        trials.append(
            {
                "task": data["task_name"],
                "trial": data["trial_name"],
                "reward": reward,
                "steps": len(rows),
                **totals,
                "cost_usd": cost,
                "original_harbor_cost_usd": original_cost,
                "accounting_adjustment_usd": cost - original_cost,
                "wall_s": elapsed(data),
                "agent_s": elapsed(timing) if timing else None,
                "api_s": sum(r["api_ms"] for r in rows) / 1000,
                "http_429s": sum(r["http_429s"] for r in rows),
                "exception": data.get("exception_info"),
                "call_ids": ids,
                "result_path": str(result_path.relative_to(root)),
                "result_sha256": hashlib.sha256(
                    result_path.read_bytes()
                ).hexdigest(),
                "reward_path": str(reward_path.relative_to(root)),
                "task_revision": data["task_id"],
                "protocol_errors": sum("protocol_error" in r for r in trace),
                "command_timeouts": sum(
                    r.get("error") == "command timeout" for r in trace
                ),
            }
        )
    assert seen == set(by_id), "Ledger calls missing from trajectories"
    assert len(trials) == len(summary.get("tasks", ["extract-elf"]))
    assert math.isclose(
        sum(t["original_harbor_cost_usd"] for t in trials),
        summary["known_cost_usd"],
        abs_tol=1e-10,
    )
    summary["original_known_cost_usd"] = summary["known_cost_usd"]
    summary["known_cost_usd"] = sum(t["cost_usd"] for t in trials)
    http_intervals = []
    headers = {}
    for row in calls:
        for attempt in row["attempts"]:
            start = timestamp(attempt["ts"])
            http_intervals.append(
                (start, start + timedelta(seconds=attempt["wall_s"]))
            )
            for key, value in attempt.get("headers", {}).items():
                if key.startswith("x-ratelimit-"):
                    headers.setdefault(key, set()).add(value)
    windows = [
        [
            r
            for r in calls
            if 0
            <= (timestamp(r["ts"]) - timestamp(start["ts"])).total_seconds()
            < 60
        ]
        for start in calls
    ]
    latencies = [r["wall_s"] for r in calls]
    return {
        "run_id": run_id,
        "summary": summary,
        "command": command,
        "trials": trials,
        "served_models": sorted({r["model"] for r in calls}),
        "passes": sum(t["reward"] == 1 for t in trials),
        "verifier_results": sum(t["reward"] is not None for t in trials),
        "peak_agents": peak_overlap(intervals),
        "peak_http_requests": peak_overlap(http_intervals),
        "call_latency_median_s": statistics.median(latencies),
        "call_latency_p95_s": percentile(latencies, 0.95),
        "call_latency_max_s": max(latencies),
        "http_429s": sum(r["http_429s"] for r in calls),
        "http_5xxs": sum(
            500 <= a.get("status_code", 0) < 600
            for r in calls
            for a in r["attempts"]
        ),
        "headers": {k: sorted(v) for k, v in headers.items()},
        "max_prompt_tokens": max(r["input_tokens"] for r in calls),
        "max_output_tokens": max(r["output_tokens"] for r in calls),
        "max_calls_60s": max(len(w) for w in windows),
        "max_input_output_tokens_60s": max(
            sum(r["input_tokens"] + r["output_tokens"] for r in w)
            for w in windows
        ),
        "max_input_cap_tokens_60s": max(
            sum(
                r["input_tokens"]
                + r["request_params"]["max_completion_tokens"]
                for r in w
            )
            for w in windows
        ),
        "ledger_matched": True,
    }


def header_range(values):
    if len(values) <= 3:
        return values
    try:
        numeric = [float(v) for v in values]
        return {"min": min(numeric), "max": max(numeric)}
    except ValueError:
        return values


def render(runs):
    lines = [
        "# Terminal-Bench 2 API smoke run",
        "",
        "Date: 2026-09-09. All reported rewards come from Harbor 0.22's",
        "unmodified TB2 verifiers on local Docker. No reward was "
        "inferred from",
        "an agent's final answer. `scripts/smoke_report.py` reconciles every",
        "trajectory call ID, served model, token count and USD total with the",
        "ledger and Harbor result, and checks each verifier reward file.",
        "The audited Luna cost adjustment below is checked separately.",
        "",
        "## Fixed setup and task selection",
        "",
        "Dataset: `terminal-bench@2.0`, upstream revision",
        "`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. One attempt "
        "per task; zero",
        "Harbor retries. Four metadata-easy tasks: `fix-git`, "
        "`overfull-hbox`,",
        "`prove-plus-comm`, `cobol-modernization`. Two smaller medium tasks:",
        "`openssl-selfsigned-cert` and `nginx-request-logging`, each with a",
        "20-minute expert estimate in dataset metadata. These six "
        "were selected",
        "before B and held fixed across all three models; this is an easier",
        "diagnostic sample, not a random estimate of TB2 accuracy.",
        "",
        "The seed uses low reasoning, 4,096 max completion tokens, 24 calls,",
        "180 seconds per API call including retry backoff, and 30 seconds per",
        "container command. Harbor retains task-defined build/verifier/agent",
        "timeouts. Each rollout has a $1 projected budget cap; 19 planned",
        "attempts fit under $25. Prices are dated estimates for Azure billing",
        "from `scripts/prices.json`, not an Azure invoice. Public "
        "OpenAI rates",
        "were checked on the official model pages:",
        "[GPT-5 "
        "Mini](https://developers.openai.com/api/docs/models/gpt-5-mini),",
        "[GPT-5.1](https://developers.openai.com/api/docs/models/gpt-5.1),",
        "[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).",
        "",
        "## Run totals",
        "",
        "| Run | Deployment | Passes | Verifier results | Calls | "
        "USD | Job wall s | -n |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in runs:
        s = run["summary"]
        model = run["command"][run["command"].index("--model") + 1]
        lines.append(
            f"| {run['run_id']} | {model} | "
            f"{run['passes']}/{len(run['trials'])} | "
            f"{run['verifier_results']} | {s['model_calls']} | "
            f"{s['known_cost_usd']:.6f} | {s['wall_s']:.1f} | "
            f"{s['concurrency']} |"
        )
    for run in runs:
        lines += [
            "",
            f"## {run['run_id']}",
            "",
            "Served model: "
            + ", ".join(f"`{m}`" for m in run["served_models"]),
            "",
            "```bash",
            "uv run harbor " + " ".join(run["command"][1:]),
            "```",
            "",
            "| Task | Reward | Steps | Input | Cached | Writes | Output | "
            "Reasoning | USD | Trial wall s | Agent s | 429 | "
            "Exception |",
            "| " + " | ".join(["---"] + ["---:"] * 11 + ["---"]) + " |",
        ]
        for t in run["trials"]:
            exception = (t["exception"] or {}).get("exception_type", "none")
            cells = [
                f"[{t['task']}](../{t['result_path']})",
                str(t["reward"]),
                str(t["steps"]),
            ]
            cells += [str(t[k]) for k in TOKEN_KEYS]
            cells += [
                f"{t['cost_usd']:.6f}"
                if t["cost_usd"] is not None
                else "unknown",
                f"{t['wall_s']:.1f}",
                f"{t['agent_s']:.1f}",
                str(t["http_429s"]),
                exception,
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines += [
            "",
            f"API latency: median {run['call_latency_median_s']:.2f}s, "
            f"p95 {run['call_latency_p95_s']:.2f}s, "
            f"max {run['call_latency_max_s']:.2f}s. "
            f"Peak overlapping agents: {run['peak_agents']}; "
            f"peak HTTP requests: {run['peak_http_requests']}. "
            f"HTTP 429: {run['http_429s']}; "
            f"HTTP 5xx: {run['http_5xxs']}.",
            "",
            f"Peak rolling 60-second load: {run['max_calls_60s']} calls, "
            f"{run['max_input_output_tokens_60s']:,} input+output tokens; "
            f"{run['max_input_cap_tokens_60s']:,} input+completion-cap "
            "tokens. These client-side measurements do not reproduce "
            "Azure's quota estimator.",
            "",
            "Observed rate-limit header values (ranges for varying counters):",
            "",
            "```json",
            json.dumps(
                {k: header_range(v) for k, v in run["headers"].items()},
                indent=2,
            ),
            "```",
        ]
    total = sum(r["summary"]["known_cost_usd"] for r in runs)
    lines += [
        "",
        "## Evidence and accounting",
        "",
        f"Smoke-only usage: **${total:.6f}**, "
        f"{sum(r['summary']['model_calls'] for r in runs)} calls. "
        "Ledger reconciliation passed for all listed trials. "
        "The full cost summary also includes pre-existing calls; "
        "they are excluded from this experiment's subtotal.",
        "",
        "`costs/smoke_results.json` retains per-trial call IDs, "
        "result SHA-256 hashes,",
        "task revisions, raw result/reward paths, latency, "
        "protocol errors, and command",
        "timeouts. `costs/ledger.jsonl` retains per-call API "
        "attempt headers and raw",
        "request/response paths. `logs/<run_id>/timing.jsonl` "
        "separates agent and job",
        "elapsed time. Trial wall includes environment setup, "
        "verification and cleanup;",
        "parallel trial times and summed API latency are not job wall time.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    runs = [collect(ROOT, run_id, ledger) for run_id in args.run_ids]
    (ROOT / "costs/smoke_results.json").write_text(
        json.dumps(runs, indent=2) + "\n"
    )
    report = render(runs)
    if args.notes:
        report += args.notes.read_text()
    (ROOT / "docs/smoke_run.md").write_text(report)
    print(
        f"Reconciled {sum(len(r['trials']) for r in runs)} "
        "real verifier trials"
    )


if __name__ == "__main__":
    main()
