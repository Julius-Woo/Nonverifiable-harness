"""Audit calibration trials and render measured tables without hiding "
"failures."""

import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from harness.ledger import price_usage
from scripts.calibrate import CONFIGS, ROOT
from scripts.cost_report import read_ledger, render_report
from scripts.smoke_report import elapsed


def mean(xs):
    return statistics.mean(xs) if xs else 0.0


def job_elapsed(raw):
    """Harbor job timestamps mix naive UTC and explicit UTC offsets."""

    def utc(value):
        parsed = datetime.fromisoformat(value)
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed
        )

    return (
        utc(raw.get("finished_at") or raw["updated_at"])
        - utc(raw["started_at"])
    ).total_seconds()


def paired_interval(first, second):
    differences = [
        first["task_rates"][name] - second["task_rates"][name]
        for name in sorted(first["task_rates"])
    ]
    rng = random.Random(260910)
    draws = sorted(
        mean(rng.choices(differences, k=len(differences)))
        for _ in range(10000)
    )
    return mean(differences), draws[250], draws[9750]


def operational_notes(results):
    """Document interruptions, memory, and unknown billing evidence."""
    text = [
        "## Interrupted runs, memory guard, and accounting",
        "",
        "Two earlier drivers were killed by a low-memory watchdog, not a "
        "kernel OOM. The second interruption occurred with MemAvailable "
        "above 20 GB (operator report); low MemFree reflected page cache. "
        "Orphan containers had already been removed before this recovery. "
        "Disk reconciliation found 51 finalized mini/native trial results "
        "in the original job, plus two verifier-bearing results in "
        "`calibration-mini-native-260910-resume`: "
        "`adaptive-rejection-sampler` and `pypi-server`. "
        "The job-level result.json is never counted as a trial.",
        "",
        "Exactly seven remaining slots were launched once in "
        "`calibration-mini-native-260910-recovery2`: `query-optimize`, "
        "`filter-js-from-html`, `gcode-to-text`, `headless-terminal`, "
        "`protein-assembly`, `raman-fitting`, and "
        "`torch-pipeline-parallelism`. Per-task links mark all nine "
        "replacement results as resumed. Killed attempts and unfinished "
        "verifier stdout never supply a reward. Existing finalized solver "
        "errors were retained as operational failures, not retried. "
        "Only a finalized trial with a verifier reward contributes to "
        "the verifier count; command failures without a verifier remain "
        "explicit failures in the fixed avg@2 denominator. See "
        "[attempt reconciliation]"
        "(../logs/calibration_attempt_reconciliation.json).",
        "",
        "The resumed batches ran in order: luna/native, luna/json, "
        "terra/native, then budget-permitting terra/json, one job at a "
        "time. Each invocation explicitly uses `--n-concurrent 4`. "
        "Before Harbor starts, `free -g` is logged and MemAvailable from "
        "`/proc/meminfo` must be at least 10 GiB. During each job, "
        "`free -g` and `docker stats --no-stream` are sampled every "
        "two minutes, with additional admission checks between trials. "
        "Below 6 GiB, new trials pause while running trials finish; "
        "admission resumes at 10 GiB. Memory evidence is linked below.",
        "",
        "| Job segment | Final results / verifier rewards | Wall s | "
        "Min sampled available GiB | Samples below 6 GiB | Memory log |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        base = result["run_id"]
        for job in sorted((ROOT / "logs/harbor").glob(base + "*")):
            paths = list(job.glob("*/result.json"))
            trials = [json.loads(p.read_text()) for p in paths]
            verified = sum(
                ((t.get("verifier_result") or {}).get("rewards") or {}).get(
                    "reward"
                )
                is not None
                for t in trials
            )
            run_dir = ROOT / "logs" / job.name
            memory = [
                row for row in read_ledger(run_dir / "memory.jsonl")
                if "available_gb" in row
            ]
            low = min((m["available_gb"] for m in memory), default=None)
            pauses = sum(m["available_gb"] < 6 for m in memory)
            summary_path = run_dir / "summary.json"
            if summary_path.exists():
                wall = f"{json.loads(summary_path.read_text())['wall_s']:.2f}"
            else:
                raw = json.loads((job / "result.json").read_text())
                prefix = "" if raw.get("finished_at") else "≥"
                wall = f"{prefix}{job_elapsed(raw):.2f}"
            link = (
                f"[samples](../logs/{job.name}/memory.jsonl)"
                if memory
                else "not recorded"
            )
            minimum = f"{low:.2f}" if low is not None else "unknown"
            text.append(
                f"| {job.name} | {len(trials)} / {verified} | {wall} | "
                f"{minimum} | {pauses if memory else 'unknown'} | {link} |"
            )
    text += [
        "",
        "Interrupted segment wall times are lower bounds from the last "
        "persisted Harbor update; exact watchdog kill timestamps were "
        "not retained. Mini/native batch wall is the sum of those lower "
        "bounds and the measured final recovery wall; driver downtime "
        "is excluded. All other completed batch walls are measured by "
        "the launcher. Historical mini/json used concurrency 8; this "
        "limits causal latency comparisons with the later batches.",
        "",
    ]
    budget_path = ROOT / "costs/calibration_budget.json"
    if budget_path.exists():
        used = json.loads(budget_path.read_text())["used_usd"]
        all_calls = [
            row
            for row in read_ledger(ROOT / "costs/ledger.jsonl")
            if row.get("phase") == "P1.2"
        ]
        known = sum(row.get("cost_usd") or 0 for row in all_calls)
        interrupted = sum(r["interrupted_known_usd"] for r in results)
        text += [
            f"Experiment known API cost, including interrupted work: "
            f"**${known:.6f}**. Shared guard used/reserved: "
            f"**${used:.6f} / $40**. Interrupted mini/native work "
            f"accounts for ${interrupted:.6f} "
            "of known cost and is included in configuration totals, "
            "but excluded from completed-rollout means. "
            "`costs/summary.md` covers the entire project ledger; "
            "experiment totals here filter phase P1.2.",
            "",
            "Two lost ledger writes were reconstructed from orphan "
            "request artifacts, with unknown dispatch/status, response, "
            "usage, cost, and latency explicitly preserved. Their "
            "existing conservative reservations total **$0.023641**; "
            "they were not released or charged twice. API error calls "
            "also retain reservations when usage is unavailable. "
            "[Recovery audit](../logs/calibration_ledger_recovery.jsonl). "
            "Every saved calibration request now has a ledger record. "
            "Reported HTTP status counts exclude these unknown statuses.",
            "",
        ]
    diagnostic = ROOT / "logs/calibration-native-diagnostic/error-response.txt"
    if diagnostic.exists():
        text += [
            "Luna/native returned HTTP 400 before model generation. One "
            "separately ledgered diagnostic call captured the endpoint "
            "error: function tools with reasoning_effort are unsupported "
            "for gpt56luna on Chat Completions; the service suggests "
            "Responses or reasoning `none`. The calibration retains "
            "its common Chat Completions transport and low reasoning. "
            "Thus this row measures an unsupported API configuration, "
            "not Luna's task-solving ability. The diagnostic is excluded "
            "from benchmark denominators and included in the $40 guard. "
            "[Captured rejection](../logs/calibration-native-diagnostic/"
            "error-response.txt). HTTP error calls provide no token "
            "usage or invoice evidence, so their costs remain unknown; "
            "zero known USD must not be read as zero billed USD.",
            "",
        ]
    projection = ROOT / "logs/calibration_terra_json_projection.json"
    if projection.exists():
        estimate = json.loads(projection.read_text())
        text += [
            "The optional Terra/json budget check repriced Luna/json's "
            "measured tokens at Terra rates, then added a 50% margin: "
            f"${estimate['projected_usd']:.2f}, against "
            f"${estimate['remaining_usd_at_check']:.2f} remaining at "
            "the check. This is a projection, not an assumption of "
            "identical token use; the shared $40 guard remains binding. "
            "[Projection audit]"
            "(../logs/calibration_terra_json_projection.json).",
            "",
        ]
    return text


def collect(label, ledger, split):
    run_id = f"calibration-{label}-260910"
    job = ROOT / "logs/harbor" / run_id
    if not job.exists():
        return None
    by_task = {t["name"]: s for s, ts in split["splits"].items() for t in ts}
    jobs = [job] + sorted(job.parent.glob(run_id + "-*"))
    run_ids = {p.name for p in jobs}
    calls = [r for r in ledger if r["run_id"] in run_ids]
    by_trial = defaultdict(list)
    for row in calls:
        by_trial[Path(row["raw_dir"]).parents[2].name].append(row)
    summary_path = ROOT / "logs" / run_id / "summary.json"
    summary = (
        json.loads(summary_path.read_text()) if summary_path.exists() else {}
    )
    if len(jobs) > 1:
        segments = []
        for segment in jobs:
            saved = ROOT / "logs" / segment.name / "summary.json"
            if saved.exists():
                segments.append(json.loads(saved.read_text()))
            else:
                raw = json.loads((segment / "result.json").read_text())
                segments.append(
                    {
                        "run_id": segment.name,
                        "wall_s": job_elapsed(raw),
                        "wall_is_lower_bound": not raw.get("finished_at"),
                    }
                )
        summary = {
            "segments": segments,
            "wall_s": sum(s.get("wall_s", 0) for s in segments),
            "wall_is_lower_bound": any(
                s.get("wall_is_lower_bound") for s in segments
            ),
        }
    config = json.loads((job / "config.json").read_text())
    trials = []
    for path in sorted(p for j in jobs for p in j.glob("*/result.json")):
        data = json.loads(path.read_text())
        assert data["task_id"]["git_commit_id"] == split["git_commit"]
        name = data["task_name"]
        assert name in by_task
        trace = read_ledger(path.parent / "agent/trace.jsonl")
        rows = by_trial[path.parent.name]
        ids = {r["call_id"] for r in rows}
        trace_ids = {r["call_id"] for r in trace if r["kind"] == "assistant"}
        assert trace_ids <= ids
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
        reward = rewards.get("reward")
        reward_path = path.parent / "verifier/reward.txt"
        if reward is not None and reward_path.exists():
            assert float(reward_path.read_text()) == reward
        exception = data.get("exception_info") or {}
        error = exception.get("exception_type")
        timing = data.get("agent_execution")
        no_action = any(r["kind"] == "finish" for r in trace) and not any(
            r["kind"] == "observation" and "command" in r for r in trace
        )
        trial = {
            "task": name,
            "split": by_task[name],
            "trial": path.parent.name,
            "reward": reward,
            "pass": int(reward == 1 and not error),
            "exception_type": error,
            "exception_message": exception.get("exception_message"),
            "environment_failed": bool(error and not timing),
            "harbor_timeout": "Timeout" in (error or "")
            and "Command" not in (error or ""),
            "resumed": path.parent.parent.name != run_id,
            "no_action": no_action,
            "finish_answer": next(
                (r["answer"] for r in trace if r["kind"] == "finish"), None
            ),
            "steps": len(rows),
            "observations": sum(
                r["kind"] == "observation" and "command" in r for r in trace
            ),
            "protocol_errors": sum("protocol_error" in r for r in trace),
            "command_timeouts": sum(
                r.get("error") == "command timeout" for r in trace
            )
            or int(
                "Command timed out" in exception.get("exception_message", "")
            ),
            "known_cost_usd": sum(
                r.get("cost_usd") or r.get("known_response_cost_usd") or 0
                for r in rows
            ),
            "unknown_cost_calls": sum(r.get("cost_usd") is None for r in rows),
            "budget_used_usd": max(
                (r["budget_used_usd"] for r in rows), default=0
            ),
            "agent_s": elapsed(timing)
            if timing and timing.get("finished_at")
            else None,
            "result_path": str(path.relative_to(ROOT)),
            "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "call_ids": sorted(ids),
            "ledger_without_trace": sorted(ids - trace_ids),
        }
        assert trial["budget_used_usd"] <= 1 + 1e-8
        trials.append(trial)
    counts = Counter(t["task"] for t in trials)
    assert all(n <= 2 for n in counts.values())
    if summary and summary.get("return_code") == 0:
        raw_dirs = {
            str(p.parent.resolve())
            for p in job.glob("*/agent/calls/*/request.json")
        }
        assert raw_dirs <= {r["raw_dir"] for r in calls}, (
            "Request artifacts missing from ledger"
        )
        assert {call for t in trials for call in t["call_ids"]} == {
            r["call_id"] for r in calls
        }, "Ledger calls missing from result-bearing trials"
    headers = defaultdict(set)
    for row in calls:
        for attempt in row["attempts"]:
            for key, value in attempt.get("headers", {}).items():
                if key.startswith("x-ratelimit-") or key.startswith(
                    "retry-after"
                ):
                    headers[key].add(value)
    compact = {}
    for key, values in sorted(headers.items()):
        try:
            numbers = [float(v) for v in values]
            compact[key] = {"min": min(numbers), "max": max(numbers)}
        except ValueError:
            compact[key] = sorted(values)
    successes = sum(t["pass"] for t in trials)
    no_action = sum(t["no_action"] for t in trials)
    task_rates = {
        name: sum(t["pass"] for t in trials if t["task"] == name) / 2
        for name in sorted(by_task)
    }
    rng = random.Random(260910)
    values = list(task_rates.values())
    boots = sorted(mean(rng.choices(values, k=30)) for _ in range(10000))
    return {
        "configuration": label,
        "run_id": run_id,
        "summary": summary,
        "concurrency": config.get("n_concurrent_trials", 4),
        "n_results": len(trials),
        "n_agent_started": sum(t["steps"] > 0 for t in trials),
        "n_verifier": sum(t["reward"] is not None for t in trials),
        "successes": successes,
        "pass_rate": successes / 60,
        "pass_ci95": [boots[250], boots[9750]],
        "task_rates": task_rates,
        "split_rates": {
            s: sum(t["pass"] for t in trials if t["split"] == s)
            / (2 * len(ts))
            for s, ts in split["splits"].items()
        },
        "no_action_count": no_action,
        "no_action_rate": no_action / 60,
        "no_action_started_rate": no_action
        / max(1, sum(t["steps"] > 0 for t in trials)),
        "mean_steps": mean([t["steps"] for t in trials]),
        "mean_agent_s": mean(
            [t["agent_s"] for t in trials if t["agent_s"] is not None]
        ),
        "mean_usd": mean([t["known_cost_usd"] for t in trials]),
        "max_usd": max((t["known_cost_usd"] for t in trials), default=0),
        "known_usd": sum(
            r.get("cost_usd") or r.get("known_response_cost_usd") or 0
            for r in calls
        ),
        "unknown_cost_calls": sum(r.get("cost_usd") is None for r in calls),
        "response_calls": sum(bool(r.get("served_model")) for r in calls),
        "http_400s": sum(
            a.get("status_code") == 400 for r in calls for a in r["attempts"]
        ),
        "http_429s": sum(r["http_429s"] for r in calls),
        "http_5xx": sum(
            500 <= a.get("status_code", 0) < 600
            for r in calls
            for a in r["attempts"]
        ),
        "harbor_timeouts": sum(t["harbor_timeout"] for t in trials),
        "command_timeouts": sum(t["command_timeouts"] for t in trials),
        "interrupted_known_usd": sum(
            r.get("cost_usd") or 0
            for r in calls
            if r["call_id"] not in {i for t in trials for i in t["call_ids"]}
        ),
        "exceptions": dict(
            Counter(t["exception_type"] for t in trials if t["exception_type"])
        ),
        "failure_reasons": dict(
            Counter(
                (t["exception_message"] or "")[:160]
                for t in trials
                if t["exception_type"]
            )
        ),
        "headers": compact,
        "trials": trials,
        "served_models": sorted(
            {r["served_model"] for r in calls if r.get("served_model")}
        ),
        "calls": len(calls),
    }


def render(results, split):
    text = [
        "# Terminal-Bench 2 task-model and protocol calibration",
        "",
        "Date: 2026-09-10. P1.1/P1.2. Rewards below come from "
        "unmodified Harbor 0.22 Docker verifiers; model completion "
        "claims never establish correctness.",
        "",
        "## Setup and reproducibility",
        "",
        f"Dataset `terminal-bench@2.0`, commit `{split['git_commit']}`. "
        f"The cached `task.toml` field `metadata.difficulty` contains 4 "
        f"easy, 55 medium, and 30 hard tasks (89 total). Fixed sampling "
        f"seed **260910**; sorted task names, independent per-stratum "
        f"shuffles from one seeded Python RNG, and Hamilton "
        f"largest-remainder allocation with lexical tie-breaking. "
        f"Search is allocated first, then anchor, then sealed. The six "
        f"smoke tasks were eligible with no preference; only "
        f"`cobol-modernization` was selected.",
        "",
        "| Split | Easy | Medium | Hard | Total |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for s, ts in split["splits"].items():
        c = Counter(t["difficulty"] for t in ts)
        text.append(
            f"| {s} | {c['easy']} | {c['medium']} | {c['hard']} | {len(ts)} |"
        )
    text += [
        "",
        "The proportional 30-task allocation has only one easy task; "
        "it is impossible to put an easy task in every split while "
        "retaining that allocation. Metadata hashes are stored with "
        "each selected task. Reproduce with `uv run python -m "
        "scripts.make_tb2_split`.",
        "",
        "Each configuration uses all 30 tasks × 2 fresh attempts "
        "(avg@2), low reasoning, 4,096 max completion tokens, 24 model "
        "calls, 30 seconds per command, 180 seconds per API call, and "
        "unchanged task-defined Harbor build/agent/verifier timeouts. "
        "Temperature and generation seed are omitted (provider defaults); "
        "260910 is the dataset-sampling and analysis seed. "
        "Zero API retries and zero Harbor retries. No planning, "
        "self-verification, model-specific text, or rescue logic was "
        "added. The API JSON prompt only removes the CLI-residue "
        "sentence. Native changes only protocol instructions and "
        "message transport: exactly terminal/read_file/write_file "
        "function tools, `parallel_tool_calls=false`, and a plain "
        "final message to finish. Native actions normalize into the "
        "same assistant/observation/finish JSONL records. Native here "
        "means Chat Completions function tools; Responses API was "
        "not measured.",
        "",
        "Batches run sequentially on the same host. The initial mini/json "
        "batch used concurrency 8. The recovery and all remaining batches "
        "use concurrency 4, as required by the resume instruction. "
        "These 30-second command "
        "limits are part of the unchanged seed and do not by themselves "
        "establish host overload. Image pulls and long verifiers affect "
        "batch wall time, so compare agent seconds separately. The first "
        "batch warms Docker images for later batches. Source hashes are "
        "archived in [the manifest]"
        "(../logs/calibration_source_manifest.json).",
        "",
        "Run a batch with `uv run python -m scripts.calibrate "
        "luna-native --concurrency 4`; substitute any configuration "
        "below. Each launch archives its full pinned Harbor config in "
        "`logs/calibration-<configuration>-260910/config.json`. "
        "Rebuild this report with `uv run python -m "
        "scripts.calibration_report`.",
        "",
        "USD uses measured API token usage and `scripts/prices.json` "
        "standard API proxy rates, not verified Azure invoice charges. "
        "Terra is $2 input / $0.20 cached input / $2.50 cache write / "
        "$12 output per million tokens in the unchanged "
        "[recorded price table](../scripts/prices.json). "
        "Reasoning is included in output. Every logical API call is "
        "ledgered, with HTTP attempts and rate-limit headers nested "
        "under it. A process-safe shared reservation guard caps this "
        "experiment at $40; every rollout has a $1 projected cap. "
        "Ambiguous request charges retain conservative reservations.",
        "",
        "## Metrics and results",
        "",
        "Primary pass = verifier reward 1 with no trial exception. "
        "Solver failures/timeouts count as failures even if a raw "
        "reward is 1. All scheduled attempts remain in the fixed "
        "denominator (60 overall; 36/12/12 by split); "
        "build/grader failures are explicitly listed and counted as zero. "
        "Interrupted attempts are replaced by their recovery results; "
        "killed attempts are not counted as failures or extra rollouts. "
        "Any unfinished batch is provisional, not a completed estimate. "
        "No-action means a finish record with zero executed-tool "
        "observations; protocol-error feedback is not a tool "
        "observation. Steps count logical API calls, including failed "
        "calls and finish. "
        "Cost/steps means use result-bearing trials; agent time "
        "averages trials with recorded agent execution. Batch wall "
        "includes setup, verification and cleanup. Confidence "
        "intervals resample tasks (both attempts together), 10,000 "
        "bootstrap draws, seed 260910.",
        "Rows rejected by the API have zero operational success, "
        "not a measured seed-accuracy estimate. Their agent seconds "
        "measure rejection overhead; their no-action gate is unassessed.",
        "",
        "| Configuration | Results / verifier | Pass avg@2 | 95% task "
        "CI | Search | Anchor | Sealed | No-action |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        lo, hi = r["pass_ci95"]
        s = r["split_rates"]
        rejected = bool(r["http_400s"] and not r["response_calls"])
        interval = "not applicable" if rejected else f"{lo:.1%}–{hi:.1%}"
        no_action = (
            "0 finishes; unassessed"
            if rejected
            else f"{r['no_action_count']}/60 ({r['no_action_rate']:.1%})"
        )
        text.append(
            f"| {r['configuration']} | {r['n_results']}/60; "
            f"{r['n_verifier']} | {r['pass_rate']:.1%} "
            f"({r['successes']}/60)"
            f"{'; API rejected' if rejected else ''} | {interval} | "
            f"{s['search']:.1%} | {s['anchor']:.1%} | {s['sealed']:.1%} "
            f"| {no_action} |"
        )
    text += [
        "",
        "| Configuration | Mean steps | Mean known USD | Max known USD | "
        "Known total USD "
        "| Mean agent s | Batch wall s | Concurrency | 429 / 5xx | "
        "Harbor timeouts | Command timeouts | HTTP 400 | Unknown-cost calls |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: "
        "| ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        wall = r["summary"].get("wall_s")
        text.append(
            f"| {r['configuration']} | {r['mean_steps']:.2f} | "
            f"{r['mean_usd']:.6f} | {r['max_usd']:.6f} | "
            f"{r['known_usd']:.6f} | {r['mean_agent_s']:.2f} | "
            f"{('≥' if r['summary'].get('wall_is_lower_bound') else '')}"
            f"{f'{wall:.2f}' if wall is not None else 'running'} | "
            f"{r['concurrency']} | {r['http_429s']} / {r['http_5xx']} | "
            f"{r['harbor_timeouts']} | {r['command_timeouts']} | "
            f"{r['http_400s']} | {r['unknown_cost_calls']} |"
        )
    text += [
        "",
        "Paired task-bootstrap contrasts (first minus second; "
        "provisional if either batch is unfinished). API-rejected "
        "configurations are omitted from capability contrasts:",
        "",
        "| Contrast | Difference (pp) | 95% CI (pp) |",
        "| --- | ---: | ---: |",
    ]
    by_label = {r["configuration"]: r for r in results}
    for first, second in (
        ("mini-native", "mini-json"),
        ("luna-native", "luna-json"),
        ("luna-native", "mini-native"),
        ("terra-native", "luna-native"),
        ("terra-native", "terra-json"),
        ("luna-json", "mini-json"),
        ("terra-json", "luna-json"),
    ):
        if (
            first in by_label
            and second in by_label
            and by_label[first]["response_calls"]
            and by_label[second]["response_calls"]
        ):
            delta, low, high = paired_interval(
                by_label[first], by_label[second]
            )
            text.append(
                f"| {first} − {second} | {delta * 100:.1f} | "
                f"{low * 100:.1f} to {high * 100:.1f} |"
            )
    text += [
        "",
        "## Decision rule",
        "",
        "Eligibility requires a completed 30-task avg@2 pass rate "
        "inside 15–45% and no-action termination below 5%, with the "
        "identical seed for every model.",
        "",
        "| Configuration | Pass-rate gate | No-action gate | "
        "Common-prompt gate | Eligible |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        p = 0.15 <= r["pass_rate"] <= 0.45
        n = r["no_action_rate"] < 0.05
        complete = r["n_results"] == 60 and r["n_agent_started"] == 60
        gate = (
            "within range"
            if p
            else "below 15%"
            if r["pass_rate"] < 0.15
            else "above 45%"
        )
        eligible = (
            "yes"
            if p and n and complete
            else "no"
            if complete
            else "incomplete/environment-limited"
        )
        no_action_gate = "pass" if n else "fail"
        if r["calls"] and not r["response_calls"] and r["http_400s"]:
            gate = "not measurable: API rejects configuration"
            eligible = "no: unsupported as tested"
            no_action_gate = "not assessed: no model response"
        text.append(
            f"| {r['configuration']} | "
            f"{gate} "
            f"| {no_action_gate} | "
            "pass | "
            f"{eligible} "
            f"|"
        )
    text += [
        "",
        "<!-- RECOMMENDATION -->",
        "Recommendation pending completion and evidence review.",
        "<!-- END RECOMMENDATION -->",
        "",
        *operational_notes(results),
        "## Per-task evidence and operational failures",
        "",
    ]
    for r in results:
        served = ", ".join(r["served_models"]) or "none recorded"
        no_action_links = (
            ", ".join(
                f"[{t['trial']}](../"
                f"{Path(t['result_path']).parent}/agent/trace.jsonl)"
                for t in r["trials"]
                if t["no_action"]
            )
            or "none"
        )
        text += [
            f"### {r['configuration']}",
            "",
            f"Served models: {served}. "
            "Calls: "
            f"{r['calls']}; unknown-cost calls: "
            f"{r['unknown_cost_calls']}. Agent started: "
            f"{r['n_agent_started']}/60. No-action among started "
            f"agents: {r['no_action_started_rate']:.1%}. Exceptions: "
            f"`{json.dumps(r['exceptions'])}`.",
            f"Calls with a served-model response: {r['response_calls']}; "
            f"HTTP 400 rejections: {r['http_400s']}.",
            "",
            "Failure messages and counts: "
            f"`{json.dumps(r['failure_reasons'])}`.",
            "",
            f"No-action traces: {no_action_links}.",
            "",
            "| Task | Split | Attempt raw reward (result links) | "
            "avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | "
            "Exceptions |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for s, ts in split["splits"].items():
            for t in ts:
                rows = [v for v in r["trials"] if v["task"] == t["name"]]
                links = (
                    ", ".join(
                        f"[{v['reward']}](../{v['result_path']})"
                        + (" (resumed)" if v["resumed"] else "")
                        for v in rows
                    )
                    or "pending"
                )
                errors = (
                    ", ".join(
                        v["exception_type"]
                        for v in rows
                        if v["exception_type"]
                    )
                    or "none"
                )
                agent_times = [
                    v["agent_s"] for v in rows if v["agent_s"] is not None
                ]
                text.append(
                    f"| {t['name']} | {s} | {links} | "
                    f"{sum(v['pass'] for v in rows) / 2:.0%} | "
                    f"{mean([v['steps'] for v in rows]):.1f} | "
                    f"{mean([v['known_cost_usd'] for v in rows]):.6f} | "
                    f"{mean(agent_times):.1f} "
                    f"| {errors} |"
                )
        failures = [t for t in r["trials"] if t["environment_failed"]]
        text += ["", "Environment build/setup failures:", ""]
        if not failures:
            text.append("None recorded.")
        for trial in failures:
            detail = (trial["exception_message"] or "").replace("\n", " ")
            text.append(
                f"- [{trial['trial']}](../{trial['result_path']}): "
                f"{trial['exception_type']}: {detail[:500]}"
            )
        text += [
            "",
            "Observed rate-limit headers (all distinct strings or "
            "numeric min/max; request IDs remain in the ledger):",
            "",
            "```json",
            json.dumps(r["headers"], indent=2),
            "```",
            "",
        ]
    return "\n".join(text) + "\n"


def main():
    # Preserve the completed follow-up when rebuilding through the old entry.
    from scripts.calibrate import MEDIUM_CONFIGS

    if all(
        (ROOT / "logs" / f"calibration-{label}-260910" / "summary.json")
        .exists()
        for label in MEDIUM_CONFIGS
    ):
        from scripts.calibration_medium_report import main as medium_main

        return medium_main()
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    prices = json.loads((ROOT / "scripts/prices.json").read_text())
    calibration_calls = [r for r in ledger if r.get("phase") == "P1.2"]
    assert len({r["call_id"] for r in calibration_calls}) == len(
        calibration_calls
    )
    for row in calibration_calls:
        response = Path(row["raw_dir"]) / "response.json"
        if response.exists():
            data = json.loads(response.read_text())
            assert data["usage"]["prompt_tokens"] == row["input_tokens"]
            assert data["usage"]["completion_tokens"] == row["output_tokens"]
            if row.get("cost_usd") is not None:
                assert (
                    abs(
                        price_usage(row["pricing_model"], row, prices)
                        - row["cost_usd"]
                    )
                    < 1e-10
                )
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    results = [r for label in CONFIGS if (r := collect(label, ledger, split))]
    (ROOT / "costs/calibration_results.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    doc = ROOT / "docs/calibration.md"
    rendered = render(results, split)
    if doc.exists() and "<!-- RECOMMENDATION -->" in doc.read_text():
        old = (
            doc.read_text()
            .split("<!-- RECOMMENDATION -->")[1]
            .split("<!-- END RECOMMENDATION -->")[0]
        )
        rendered = rendered.replace(
            "\nRecommendation pending completion and evidence review.\n", old
        )
    doc.write_text(rendered)
    (ROOT / "costs/summary.md").write_text(render_report(ledger))
    print(
        json.dumps(
            [
                {
                    k: r[k]
                    for k in (
                        "configuration",
                        "n_results",
                        "successes",
                        "no_action_count",
                        "known_usd",
                        "http_429s",
                        "harbor_timeouts",
                        "exceptions",
                    )
                }
                for r in results
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
