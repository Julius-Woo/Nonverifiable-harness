"""Audit calibration trials and render measured tables without hiding "
"failures."""

import hashlib
import json
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.calibrate import ALL_CONFIGS, ALLOWANCE_CONFIGS, ROOT
from scripts.calibration_cohorts import (
    account,
    load_manifest,
    render_costs,
    scoped_calls,
)
from scripts.calibration_contracts import TERMINATIONS, classify_attempt
from scripts.cost_report import read_ledger
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


def collect(label, ledger, split, manifest=None):
    manifest = manifest or load_manifest()
    run_id = f"calibration-{label}-260910"
    job = ROOT / "logs/harbor" / run_id
    if not job.exists():
        return None
    by_task = {t["name"]: s for s, ts in split["splits"].items() for t in ts}
    jobs = [
        job.parent / r["job_name"]
        for r in manifest["runs"]
        if r["configuration"] == label
        and r["kind"] == "benchmark"
        and (job.parent / r["job_name"]).exists()
    ]
    run_ids = {p.name for p in jobs}
    calls = [
        r for r in scoped_calls(ledger, manifest) if r["run_id"] in run_ids
    ]
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
        exception_path = path.parent / "exception.txt"
        api_error_paths = sorted(
            path.parent.glob("agent/calls/*/http_error_*.json")
        )
        outcome = classify_attempt(
            data,
            trace,
            rows,
            exception_path.read_text() if exception_path.exists() else "",
            [json.loads(p.read_text()) for p in api_error_paths],
        )
        trial = {
            "task": name,
            "split": by_task[name],
            "trial": path.parent.name,
            "reward": reward,
            "pass": outcome["pass_l1"],
            "published_pass": int(reward == 1 and not error),
            "exception_type": error,
            "exception_message": exception.get("exception_message"),
            "environment_failed": bool(error and not timing),
            "harbor_timeout": "Timeout" in (error or "")
            and "Command" not in (error or ""),
            "resumed": path.parent.parent.name != run_id,
            "no_action": outcome["no_action"],
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
            "api_error_paths": [
                str(p.relative_to(ROOT)) for p in api_error_paths
            ],
        }
        trial.update(outcome)
        trial["job_name"] = path.parent.parent.name
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
    labels = {
        key: label_metrics(trials, split, key)
        for key in ("pass_l1", "pass_l1_prime", "pass_l2")
    }
    from scripts.calibration_ratified import standing_metrics

    return {
        "configuration": label,
        "cohort": next(
            r["cohort"]
            for r in manifest["runs"]
            if r["configuration"] == label
        ),
        "max_completion_tokens": config.get("agents", [{}])[0]
        .get("kwargs", {})
        .get("max_completion_tokens", 4096),
        "labels": labels,
        "standing_metrics": standing_metrics(trials),
        "raw_reward_ones": sum(t["reward"] == 1 for t in trials),
        "termination_counts": dict(Counter(t["termination"] for t in trials)),
        "no_action_reasons": dict(
            Counter(t["termination"] for t in trials if t["no_action"])
        ),
        "no_action_details": dict(
            Counter(t["termination_detail"] for t in trials if t["no_action"])
        ),
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


def label_metrics(trials, split, label):
    if label == "pass_l1_prime":
        from scripts.calibration_ratified import retained_label_metrics

        return retained_label_metrics(trials, split, label)
    names = sorted(t["name"] for ts in split["splits"].values() for t in ts)
    rates = {
        name: sum(t[label] for t in trials if t["task"] == name) / 2
        for name in names
    }
    rng = random.Random(260910)
    values = list(rates.values())
    draws = sorted(
        mean(rng.choices(values, k=len(values))) for _ in range(10000)
    )
    return {
        "successes": sum(t[label] for t in trials),
        "pass_rate": mean(values),
        "pass_ci95": [draws[250], draws[9750]],
        "task_rates": rates,
        "split_rates": {
            s: sum(t[label] for t in trials if t["split"] == s) / (2 * len(ts))
            for s, ts in split["splits"].items()
        },
    }


def eligible(row, label):
    metrics = row["labels"][label]
    if label == "pass_l1_prime":
        return (
            row["n_results"] == 60
            and row["response_calls"] > 0
            and row["max_completion_tokens"] == 8192
            and "-json" in row["configuration"]
            and metrics["pass_rate"] is not None
            and 0.15 <= metrics["pass_rate"] <= 0.45
            and row["standing_metrics"]["no_action"]["rate"] < 0.05
        )
    return (
        row["n_results"] == 60
        and row["response_calls"] > 0
        and 0.15 <= metrics["pass_rate"] <= 0.45
        and row["no_action_rate"] < 0.05
    )


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(map(str, row)) + " |" for row in rows],
        "",
    ]


def contrasts(results):
    by_name = {r["configuration"]: r for r in results}
    pairs = [
        ("luna-json", "terra-json"),
        ("terra-json", "mini-json"),
        ("luna-json-medium", "terra-json"),
        ("terra-json", "mini-json-medium"),
        ("luna-json-medium", "mini-json-medium"),
        ("mini-json-medium", "mini-json"),
        ("luna-json-medium", "luna-json"),
        ("mini-native", "mini-json"),
        ("luna-json", "mini-json"),
    ]
    pairs += [
        (label, label.removesuffix("-8k")) for label in ALLOWANCE_CONFIGS
    ]
    pairs += [
        ("luna-json-medium-8k", "terra-json-8k"),
        ("terra-json-8k", "mini-json-medium-8k"),
        ("luna-json-medium-8k", "mini-json-medium-8k"),
    ]
    output = []
    for first, second in pairs:
        if first not in by_name or second not in by_name:
            continue
        for label in ("pass_l1", "pass_l2"):
            for scope in ("overall", "search", "anchor", "sealed"):
                a, b = (
                    by_name[first]["labels"][label],
                    by_name[second]["labels"][label],
                )
                if scope != "overall":
                    names = {
                        t["task"]
                        for t in by_name[first]["trials"]
                        if t["split"] == scope
                    }
                    a, b = (
                        {"task_rates": {n: m["task_rates"][n] for n in names}}
                        for m in (a, b)
                    )
                delta, low, high = paired_interval(a, b)
                output.append(
                    {
                        "first": first,
                        "second": second,
                        "label": label,
                        "scope": scope,
                        "difference": delta,
                        "ci95": [low, high],
                    }
                )
    return output


def render(results, split, manifest=None, accounting=None, comparisons=None):
    manifest = manifest or load_manifest()
    accounting = accounting or {}
    comparisons = (
        comparisons if comparisons is not None else contrasts(results)
    )
    text = [
        "# Terminal-Bench 2 task-model and protocol calibration",
        "",
        "Date: 2026-09-10. **P1.2 allowance experiment (W5f), reported "
        "under the W5e/R7 corrected contracts.** Three new 8,192-token "
        "jobs extend the eight historical 4,096-token configurations. "
        "All rewards use saved, unmodified Harbor 0.22 verifier evidence. "
        "This is AD12 evidence; AD10–AD12 remain pending decisions.",
        "",
        "## Setup and reproducibility",
        "",
        f"Dataset `terminal-bench@2.0`, commit `{split['git_commit']}`; "
        "89 tasks: 4 easy, 55 medium, 30 hard. The archived "
        "[population metadata](../data/tb2_population.json) includes task "
        "IDs, difficulties, "
        "and task.toml SHA-256 hashes. Cache paths were checked against "
        "the pinned GitTaskId. "
        "The offline check reproduces the entire committed split, "
        "including selected hashes: "
        "`uv run python -m scripts.calibration_split_check`. It reads "
        "local manifests only; "
        "it does not resolve a registry, download tasks, or rewrite the "
        "split.",
        "",
        "Sampling seed 260910; sorted tasks, one Python RNG with "
        "per-stratum shuffles, "
        "Hamilton largest remainders and lexical tie-breaking. Search is "
        "allocated first, "
        "then anchor, then sealed. Smoke tasks were eligible without "
        "preference; only "
        "`cobol-modernization` overlaps. One easy task cannot populate all "
        "three splits.",
        "",
    ]
    text += table(
        ["Split", "Easy", "Medium", "Hard", "Total"],
        [
            [
                s,
                *[
                    Counter(t["difficulty"] for t in ts)[d]
                    for d in ("easy", "medium", "hard")
                ],
                len(ts),
            ]
            for s, ts in split["splits"].items()
        ],
    )
    text += [
        "Every configuration has 30 tasks × 2 finalized attempts. Shared "
        "settings: "
        "4,096 completion tokens historically and 8,192 for rows ending "
        "`-8k` (reasoning plus output), 24 model calls, "
        "30 seconds per "
        "command, 180 seconds per API call, $1 per-rollout guard, and "
        "task-defined Harbor "
        "timeouts. Temperature and generation seed were omitted. The "
        "original six rows "
        "use low reasoning; W5d adds Mini/JSON and Luna/JSON at medium. No "
        "Terra/medium "
        "row exists. W5f repeats Terra/low, Mini/medium, and Luna/medium "
        "with the shared 8,192-token allowance, concurrency 3, a separate "
        "$15 cohort guard, and the unchanged $1 rollout guard. Native "
        "means Chat Completions function tools, with "
        "`parallel_tool_calls=false`, terminal/read_file/write_file, and a "
        "plain final answer. "
        "JSON uses the same common serialized history and prompt across "
        "deployments. "
        "There is no model-specific prompt, planning, self-verification, "
        "or rescue.",
        "",
        "Regenerate all corrected results, all cohorts, and costs with "
        "`uv run python -m scripts.calibration_report`; validate with "
        "`uv run python -m scripts.audit_calibration`. These are offline "
        "reducers. "
        "The earlier medium-only extension script is superseded; use this "
        "unified entry point.",
        "",
        "## Corrected measurement contracts",
        "",
        "**No action:** every finalized completed or failed solver attempt "
        "without an "
        "executed container/file action, divided by 60 (36/12/12 by "
        "split). No finalized "
        "grader/infrastructure exclusions were recorded. Interrupted work "
        "belongs in costs "
        "and replacement lineage, not as extra scored attempts. An emitted "
        "call or suggested "
        "shell command is not execution. A command observation proves "
        "execution regardless "
        "of its exit code. Historical timeout tracebacks reaching "
        "subprocess output collection "
        "also prove execution; their missing observation is not counted as "
        "no action.",
        "",
        "**L1 (proposed AD10):** valid verifier reward exactly 1, no agent "
        "timeout and no "
        "executor-level tool failure. Executor-level failures include "
        "command timeout, "
        "execution exception, and **any action protocol/parse error, even "
        "if recovered**. "
        "**L2 (strict A9):** L1 plus no nonzero exit from any agent-issued "
        "command, including "
        "legitimate false predicates. Exhausted solvers fail under PREREG "
        "Section 6. All remaining trial-exception rows here have raw "
        "reward 0 or missing, and therefore fail both labels. A missing "
        "finish record or no action alone does not override valid "
        "verifier credit. Raw verifier reward is preserved; neither "
        "completion claims "
        "nor recovery claims replace it. AD10 remains pending, so neither "
        "reading is silently ratified.",
        "",
        "**Termination taxonomy:** one terminal category per attempt, plus "
        "independent "
        "failure flags. A later normal finish stays a normal terminal "
        "event even if an earlier "
        "parse error disqualifies its pass. Inability/no-tools claims "
        "require no action and "
        "an explicit inability statement in the final answer. Exhaustion "
        "uses the final "
        "failed response `finish_reason: length`, the 24-call-limit "
        "exception, or the "
        "rollout-budget stop (separately counted below). Execution "
        "failures use traces and "
        "exception stacks. Remaining unhandled failures, including HTTP "
        "rejection, are trial "
        "exceptions. Protocol-only terminal failures have their own "
        "category; recovered errors "
        "are reported independently. Precedence is final finish, "
        "exhaustion, executor failure, "
        "unrecovered parse failure, remaining trial exception. Agent "
        "timeout is retained as a flag.",
        "",
        "Derived per-attempt labels, terminal causes, action evidence with "
        "source lines, "
        "nonzero-command exits, call UUIDs, raw rewards, and result hashes "
        "are in "
        "[calibration_results.json](../costs/calibration_results.json). "
        "The derived "
        "[attempt "
        "events](../logs/calibration-8k-followup-260910/attempts.jsonl) "
        "preserve execution "
        "evidence without inventing timestamps or changing historical "
        "traces. Adding execution "
        "start/outcome events to the live seed is outside this task’s "
        "allowed harness changes; "
        "future executor exceptions before output collection can therefore "
        "remain ambiguous.",
        "",
        "## Corrected results",
        "",
        "avg@2 averages the two binary labels per task, then tasks "
        "equally. All rows contain "
        "60 finalized attempts. Raw reward 1 is shown against all 60 "
        "slots; missing rewards "
        "are listed separately and never imputed as successes. Native "
        "rejection rows have "
        "zero operational pass; their capability and behavioral no-action "
        "gate are unmeasured.",
        "",
    ]
    text += table(
        [
            "Configuration",
            "Verifier / missing",
            "Raw reward 1",
            "L1 passes / avg@2",
            "L2 passes / avg@2",
            "No action",
        ],
        [
            [
                r["configuration"],
                f"{r['n_verifier']} / {60 - r['n_verifier']}",
                f"{r['raw_reward_ones']}/60 ({r['raw_reward_ones'] / 60:.1%})",
                *[
                    f"{r['labels'][k]['successes']}/60 "
                    f"({r['labels'][k]['pass_rate']:.1%})"
                    for k in ("pass_l1", "pass_l2")
                ],
                f"{r['no_action_count']}/60 ({r['no_action_rate']:.1%})",
            ]
            for r in results
        ],
    )
    text += [
        "Task-bootstrap percentile intervals use 10,000 draws, analysis "
        "seed 260910 "
        "(the original calibration convention). Each draw carries both "
        "attempts "
        "together. These describe task sampling, not independent rerun "
        "variability.",
        "",
    ]
    for key, name in (("pass_l1", "L1"), ("pass_l2", "L2")):
        text += [f"### {name}: overall and split rates", ""]
        text += table(
            [
                "Configuration",
                "Overall",
                "95% task CI",
                "Search (36)",
                "Anchor (12)",
                "Sealed (12)",
            ],
            [
                [
                    r["configuration"],
                    f"{r['labels'][key]['pass_rate']:.1%}",
                    (
                        "not measurable"
                        if not r["response_calls"]
                        else "–".join(
                            f"{v:.1%}" for v in r["labels"][key]["pass_ci95"]
                        )
                    ),
                    *[
                        f"{r['labels'][key]['split_rates'][s]:.1%}"
                        for s in ("search", "anchor", "sealed")
                    ],
                ]
                for r in results
            ],
        )
    text += [
        "### Termination reason breakdown",
        "",
        "Each cell is **all finalized attempts (no-action subset)**. Rows "
        "sum to 60; "
        "the parenthesized counts sum to that row’s no-action numerator.",
        "",
    ]
    text += table(
        [
            "Configuration",
            "Normal finish",
            "No-tools / inability",
            "Token / step / budget exhaustion",
            "Protocol / parse terminal",
            "Executor failure",
            "Trial exception",
        ],
        [
            [
                r["configuration"],
                *[
                    f"{r['termination_counts'].get(k, 0)} "
                    f"({r['no_action_reasons'].get(k, 0)})"
                    for k in TERMINATIONS[:6]
                ],
            ]
            for r in results
        ],
    )
    text += table(
        [
            "Configuration",
            "Token limit (no action)",
            "24-call cap",
            "USD cap",
            "Any protocol error attempts",
            "Any nonzero command attempts",
            "Command timeouts",
            "Agent timeouts",
        ],
        [
            [
                r["configuration"],
                "{} ({})".format(
                    sum(t["token_exhaustion"] for t in r["trials"]),
                    sum(
                        t["no_action"]
                        for t in r["trials"]
                        if t["token_exhaustion"]
                    ),
                ),
                sum(t["step_exhaustion"] for t in r["trials"]),
                sum(t["budget_exhaustion"] for t in r["trials"]),
                sum(bool(t["protocol_errors"]) for t in r["trials"]),
                sum(bool(t["nonzero_commands"]) for t in r["trials"]),
                r["command_timeouts"],
                sum(t["agent_timeout"] for t in r["trials"]),
            ]
            for r in results
        ],
    )
    text += [
        "At 4,096 tokens, Luna/low’s seven no-action attempts comprise "
        "six inability claims "
        "and one "
        "instruction-only final answer (`configure-git-webserver`); all "
        "seven contain "
        "protocol errors. Terra/low’s nine comprise eight first-response "
        "token exhaustions "
        "and one unsupported ordinary completion claim "
        "(`filter-js-from-html`). "
        "Mini/native has two inability finals and one first-response token "
        "exhaustion: "
        "**3/60 = exactly 5%, which fails the strictly-below-5% gate**. "
        "HTTP-rejected Luna/native and Terra/native are each 60/60 "
        "operational no action, "
        "without measuring generated model behavior. "
        "Mini/native’s four remaining trial exceptions are empty API "
        "answers with finish_reason stop, after earlier actions. They "
        "are not token exhaustion, inability claims, or proven outages.",
        "",
        "### Paired contrasts under both labels",
        "",
        "Differences are first minus second, in percentage points, using "
        "paired task "
        "bootstrap with both attempts preserved. Low-to-medium comparisons "
        "and contrasts "
        "against Terra/low span separately timed cohorts and host loads; "
        "they are descriptive. "
        "Terra/medium comparisons are unavailable because it was not run. "
        "Overall contrasts "
        "appear below; corresponding search/anchor/sealed contrasts and "
        "CIs are archived in "
        "[contrasts.json](../logs/calibration-8k-followup-260910/contrasts.json).",
        "",
    ]
    text += table(
        ["Contrast", "Label", "Difference (pp)", "95% CI (pp)"],
        [
            [
                r["first"] + " − " + r["second"],
                r["label"].replace("pass_", "").upper(),
                f"{100 * r['difference']:+.1f}",
                " to ".join(f"{100 * v:+.1f}" for v in r["ci95"]),
            ]
            for r in comparisons
            if r["scope"] == "overall"
        ],
    )
    from scripts.calibration_allowance_report import render_allowance

    text += render_allowance(results)
    text += [
        "## Eligibility and pending decisions",
        "",
        "The screen requires complete avg@2 in **15–45% inclusive**, no "
        "action "
        "**strictly below 5%**, a common prompt and a usable tested API "
        "configuration. "
        "Prompt parity holds for every row; the two native rejection rows "
        "fail API "
        "usability. Neither table is a model freeze or approval of pending "
        "AD1/AD10–AD12.",
        "",
    ]
    for key, name in (("pass_l1", "L1"), ("pass_l2", "L2")):
        text += [f"### Eligibility under {name}", ""]
        text += table(
            [
                "Configuration",
                "Pass avg@2 / band gate",
                "No action / gate",
                "Common prompt",
                "Eligible",
            ],
            [
                [
                    r["configuration"],
                    (
                        f"{r['labels'][key]['pass_rate']:.1%} / "
                        + (
                            "pass"
                            if 0.15 <= r["labels"][key]["pass_rate"] <= 0.45
                            else "fail"
                        )
                    )
                    if r["response_calls"]
                    else "API rejected; unmeasured",
                    f"{r['no_action_count']}/60 / "
                    + (
                        ("pass" if r["no_action_rate"] < 0.05 else "fail")
                        if r["response_calls"]
                        else "behavior unassessed"
                    ),
                    "pass",
                    "yes" if eligible(r, key) else "no",
                ]
                for r in results
            ],
        )
    eligibility = {
        name: [r["configuration"] for r in results if eligible(r, key)]
        for key, name in (("pass_l1", "L1"), ("pass_l2", "L2"))
    }
    text += [
        "**Eligible under L1: "
        + (", ".join(eligibility["L1"]) or "none")
        + "; under L2: "
        + (", ".join(eligibility["L2"]) or "none")
        + ".** "
        "The historical Terra recommendation remains withdrawn. "
        "Eligibility is an empirical screen, not a model freeze or "
        "ratification of AD10–AD12. Selection also depends on the pending "
        "AD1 versus PREREG selection/fallback rule and full pilot guards.",
        "",
        "### Pilot cost projection and operating limits",
        "",
        "The following descriptive projections cover every configuration "
        "and multiply mean known cost over all 60 finalized attempts by "
        "2,800 solver rollouts. They exclude judges, evolution, "
        "cross-judges, interruptions, and unresolved billing. They are "
        "not projections conditional on success.",
        "",
    ]
    text += table(
        [
            "Configuration",
            "Mean calls",
            "Known USD / finalized rollout",
            "Known USD / 2,800",
            "Max known USD / rollout",
            "All-work known USD",
            "Agent s",
            "Batch wall s",
            "Nominal concurrency",
        ],
        [
            [
                r["configuration"],
                f"{r['mean_steps']:.2f}",
                f"{r['mean_usd']:.6f}"
                if r["response_calls"]
                else "unknown billing",
                f"{2800 * r['mean_usd']:.2f}"
                if r["response_calls"]
                else "not estimable",
                f"{r['max_usd']:.6f}"
                if r["response_calls"]
                else "unknown billing",
                f"{r['known_usd']:.8f}",
                f"{r['mean_agent_s']:.2f}",
                ("≥" if r["summary"].get("wall_is_lower_bound") else "")
                + f"{r['summary'].get('wall_s', 0):.2f}",
                r["concurrency"],
            ]
            for r in results
        ],
    )
    text += [
        "Prices are standard API proxies from "
        "[scripts/prices.json](../scripts/prices.json), "
        "not verified Azure invoices. Historical Terra/4k’s $347.94 "
        "solver-only "
        "projection already exceeds "
        "the $300 whole-pilot guard. Low-cost early failures do not "
        "establish useful capacity. "
        "The USD 300/four-day pilot guards remain unchanged.",
        "",
        "Mini/JSON used nominal concurrency 8; other original jobs used 4; "
        "medium jobs used "
        "3 while W9 shared Docker with a six-container combined admission "
        "limit. The new 8k jobs use concurrency 3 and a combined "
        "seven-container admission limit, with MemAvailable admission "
        "paused below 6 GiB and resumed at 10 GiB. The "
        "Mini/native resume log also shows four old containers alongside "
        "four new containers. "
        "Nominal launcher concurrency therefore does not fully describe "
        "host load. Batches "
        "were sequential, cache conditions differed, and agent/window "
        "times are descriptive. "
        "Only Mini supplies concurrency-eight evidence. The eight historical "
        "configurations record "
        "zero HTTP 429s, zero 5xx responses, and zero Harbor agent "
        "timeouts; command timeouts "
        "remain failures and do not by themselves prove a host outage. The "
        "selected "
        "configuration’s capacity gate is unresolved.",
        "",
        "Mini/native’s wall lower bound is 970.519741 + 61.280396 + "
        "605.406588 "
        "= 1,637.206725 seconds, excluding downtime and unknown tails. "
        "Original memory "
        "samples and W5d admission evidence remain under the respective "
        "run directories; "
        "W5d’s historical "
        "[audit](../logs/calibration-medium-followup-260910/audit.json) "
        "records the shared-host observations.",
        "",
        "## Cohort-scoped accounting manifest",
        "",
        "The explicit [run "
        "manifest](../data/calibration_run_manifest.json) fixes job names, "
        "phase tags, cohorts, budget guards, nominal concurrency, and "
        "saved config hashes. "
        "Reducers reject unmanifested P1.2 calls or ownership mismatches "
        "instead of silently "
        "absorbing later experiments. Diagnostic spending belongs to the "
        "original $40 guard "
        "but never to benchmark denominators.",
        "",
    ]
    text += table(
        ["Job name", "Phase", "Cohort", "Budget guard / USD"],
        [
            [
                r["job_name"],
                r["phase"],
                r["cohort"],
                f"{r['budget_guard']} / {r['budget_usd']}",
            ]
            for r in manifest["runs"]
        ],
    )
    text += table(
        [
            "Cohort",
            "Finalized / verifier rewards",
            "Calls (final / interrupted / diagnostic)",
            "Finalized known USD",
            "Interrupted known USD",
            "Total known USD",
            "Retained reserve USD",
            "Used/reserved / guard USD",
            "Null-cost records",
        ],
        [
            [
                name,
                f"{r['finalized_attempts']} / {r['verifier_rewards']}",
                f"{r['ledger_calls']} ({r['finalized_calls']} / "
                f"{r['interrupted_calls']} / {r['diagnostic_calls']})",
                f"{r['finalized_known_usd']:.8f}",
                f"{r['interrupted_known_usd']:.8f}",
                f"{r['known_usd']:.8f}",
                f"{r['retained_reservations_usd']:.8f}",
                f"{r['guard_used_usd']:.8f} / {r['budget_usd']}",
                r["null_cost_calls"],
            ]
            for name, r in accounting.items()
        ],
    )
    if "w5f-8k" in accounting:
        new = accounting["w5f-8k"]
        states = new["cost_states"]
        text += [
            "The 8k cohort has "
            f"{states.get('priced', 0)} priced call records, "
            f"{states.get('other_unknown', 0)} API-timeout records with "
            "unknown charges, "
            f"{states.get('http_error_charge_unknown', 0)} HTTP rejection "
            "with unknown charges, and "
            f"{states.get('local_budget_stop_no_dispatch', 0)} local "
            "rollout-budget stop with no API dispatch or additional API "
            "usage. Thus the eight null-cost records do not mean eight "
            "unresolved dispatched charges. All seven unresolved "
            "dispatched requests belong to Terra. The $1 rollout cap was "
            "retained for every attempt. The dedicated "
            "[8k manifest](../data/calibration_8k_run_manifest.json) "
            "records the allowance, guards, baseline configurations, and "
            "final job hashes. No 8k attempts were interrupted or replaced.",
            "",
        ]
    text += [
        "Original Mini/native resolves as **51 original + 2 resume + 7 "
        "recovery2 = 60** "
        "finalized slots. Eight interrupted directories contribute 29 "
        "requests, not extra "
        "failures. Nine replacement results fill missing slots (some "
        "original slots had "
        "not started). No finalized failures were retried. The 124 "
        "original null-cost records "
        "comprise 120 benchmark HTTP rejections, one diagnostic rejection, "
        "two reconstructed "
        "requests with unknown dispatch, and one local budget stop with "
        "`attempts: []` "
        "and zero API time. That local stop adds zero API usage and is not "
        "an unresolved "
        "dispatched charge. Its $0.828084 budget-used value is cumulative "
        "previously incurred "
        "cost. The two reconstructed reservations total $0.023641 and "
        "remain retained.",
        "",
        "**Logged Phase-1 single-retry exception:** `query-optimize` and "
        "`filter-js-from-html` each had an interrupted original attempt, "
        "an interrupted "
        "operator replacement, and a finalized second operator "
        "replacement. The manifest "
        "records their exact three-attempt lineage and the historical "
        "recovery authorization "
        "reported in R7. This is explicitly a deviation from PREREG "
        "Section 6’s single "
        "infrastructure retry, despite zero configured API/Harbor retries. "
        "It is logged here "
        "and in the manifest under this task’s authorization; the "
        "prohibited PREREG file "
        "was not edited, and formal policy reconciliation remains pending.",
        "",
        "The [cohort audit]"
        "(../logs/calibration-8k-followup-260910/audit.json) "
        "checks UUID "
        "bijection, finalized slots, payload parity, response token usage, "
        "prices, and all "
        "budget guards. [costs/summary.md](../costs/summary.md) presents "
        "the three cohorts "
        "separately, with other project calls outside those guards.",
        "",
        "## Native rejection evidence",
        "",
        "The saved [Luna diagnostic]"
        "(../logs/calibration-native-diagnostic/error-response.txt) "
        "says exactly:",
        "",
        "> Function tools with reasoning_effort are not supported for "
        "gpt56luna in "
        "/v1/chat/completions. To use function tools, use /v1/responses or "
        "set "
        "reasoning_effort to 'none'.",
        "",
        "It is an `invalid_request_error` on `reasoning_effort`, with null "
        "error code, "
        "from the low-effort Chat Completions request. **Terra’s specific "
        "cause is inferred**, "
        "because no Terra HTTP error body was saved; its 60 HTTP 400s "
        "establish rejection "
        "of the tested configuration only. Neither row measures general "
        "native-tool "
        "capability. Responses is a suggested alternative for Luna, with "
        "no adapter or "
        "calibration evidence here. Changing only Luna to reasoning none "
        "would create "
        "another model-specific experimental difference.",
        "",
        "The W5e backend change archives sanitized HTTP "
        "error bodies and "
        "endpoint metadata per HTTP attempt, including retried errors, "
        "without changing "
        "requests, retry decisions, or reservations. The original backend "
        "bytes remain "
        "in [the source "
        "archive](../logs/calibration-r7-reanalysis/sources/openai_api.py); "
        "the historical source manifest is preserved. AD11’s JSON-for-all "
        "choice and "
        "AD12’s completion-allowance choice remain pending. Compatibility "
        "preflight is "
        "still needed before any future benchmark dispatch.",
        "",
        "## What remains unknown",
        "",
        "- Which A9 reading is ratified (AD10), which selection/fallback "
        "rule governs "
        "(AD1 versus PREREG), and the formal reconciliation of the two "
        "operator retry exceptions.",
        "- Whether to ratify common JSON (AD11), or implement and "
        "calibrate a common valid "
        "native transport. Terra’s exact rejection body is unavailable.",
        "- Whether to ratify the 8,192-token allowance (AD12). The three "
        "new jobs supply evidence; Terra/medium remains unmeasured.",
        "- Repeat-run variability, effects of provider sampling defaults, "
        "and a generation "
        "seed. Task-bootstrap CIs do not estimate these sources of "
        "variability.",
        "- Selected-model concurrency-eight behavior, realized host-load "
        "effects, and "
        "whether a lower concurrency will be ratified.",
        "- Azure invoice charges for rejected/interrupted requests and "
        "full pilot costs "
        "after model selection, including judge/evolver costs and runtime.",
        "- Historical execution-start timestamps were not recorded; "
        "subprocess traceback "
        "evidence recovers action presence in these timeout cases, not "
        "exact timing. "
        "Live execution-start logging remains a follow-up outside this "
        "change.",
        "",
        "## Appendix: superseded published tables",
        "",
        "**Superseded — retained verbatim for provenance, not current "
        "results or decisions.** "
        "These tables used finish-only no action and "
        "reward-1-without-trial-exception pass. "
        "They include the original six rows and W5d medium additions. "
        "Their Terra eligibility "
        "and associated recommendation are withdrawn. The complete "
        "previous document is "
        "[archived](../logs/calibration-r7-reanalysis/calibration-before.md).",
        "",
    ]
    original = (
        ROOT / "logs/calibration-r7-reanalysis/calibration-before.md"
    ).read_text()
    heading = ""
    lines = original.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading = line.lstrip("# ")
        if line.startswith("|"):
            if i == 0 or not lines[i - 1].startswith("|"):
                text += [f"### Superseded: {heading}", ""]
            text.append(line)
            if i + 1 == len(lines) or not lines[i + 1].startswith("|"):
                text.append("")
    return "\n".join(text)


def main():
    from scripts.calibration_ratified import (
        provenance_manifest,
        render_ratified,
        update_document,
    )

    manifest = load_manifest()
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    results = [
        collect(label, ledger, split, manifest) for label in ALL_CONFIGS
    ]
    assert all(r and r["n_results"] == 60 for r in results)
    assert all(
        set(Counter(t["task"] for t in r["trials"]).values()) == {2}
        for r in results
    )
    accounting = account(ledger, manifest, results, ROOT)
    path = ROOT / "docs/calibration.md"
    document = update_document(path.read_text(), render_ratified(results))
    provenance = provenance_manifest(results, ROOT)
    # Prepare all outputs before mutating the permitted derived artifacts.
    costs = render_costs(ledger, manifest, accounting)
    path.write_text(document)
    (ROOT / "data/calibration_ratified_manifest.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    (ROOT / "costs/summary.md").write_text(costs)
    print(
        json.dumps(
            [
                {
                    "configuration": r["configuration"],
                    "L1": r["labels"]["pass_l1"]["successes"],
                    "L1_prime": r["labels"]["pass_l1_prime"],
                    "L2": r["labels"]["pass_l2"]["successes"],
                    "standing_metrics": r["standing_metrics"],
                    "no_action": r["no_action_count"],
                    "reasons": r["termination_counts"],
                    "no_action_reasons": r["no_action_reasons"],
                }
                for r in results
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
