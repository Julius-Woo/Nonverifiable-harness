"""Fixed seed calibration and offline verifier comparison.

This trusted analysis module reads labels only after constructing the isolated
judge evidence. Its manifest is never part of a judge/evolver request.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import statistics
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from dotenv import dotenv_values

from evolution.judge_queue import ROOT, configured_queue, export_trace
from evolution.judges import PROMPT_VERSION, PROMPTS, JudgeInput, build_prompt
from evolution.outcomes import termination
from evolution.sanitize import VERSION, canonical, digest

DEFAULT_JOB = ROOT / "logs/harbor/calibration-mini-json-260910"
DEFAULT_OUTPUT = ROOT / "logs/judges/seed-mini-json-v2"
SAMPLING = (
    "temperature 0.6, top_p 0.95, JSON object response, "
    "reasoning at provider default; no provider seed"
)


def select_trials(job, split):
    result = json.loads((job / "result.json").read_text())
    if not result.get("finished_at"):
        raise ValueError("Calibration requires a completed Harbor job")
    groups = defaultdict(list)
    for path in job.glob("*/result.json"):
        data = json.loads(path.read_text())
        groups[data["task_name"]].append(
            (data["started_at"], path.parent.name, path)
        )
    selected = []
    for partition, tasks in split["splits"].items():
        for task in tasks:
            attempts = sorted(groups[task["name"]])
            if not attempts:
                raise ValueError(f"No attempt for {task['name']}")
            for index, (_, _, path) in enumerate(
                attempts[: 2 if partition == "search" else 1], 1
            ):
                if not (path.parent / "agent/trace.jsonl").is_file():
                    raise ValueError(
                        f"Missing trajectory for {path.parent.name}"
                    )
                selected.append((task["name"], partition, index, path))
    return selected


def verifier_label(
    result, records, *, tool_policy="strict", api_timeout_policy="failure"
):
    """A9 failures precede verifier availability; pending AD10/13 are explicit.

    Strict counts nonzero exits as failures; executor (AD10) treats them as
    ordinary observations. A missing grader result is never a favourable zero.
    """
    if tool_policy not in {"strict", "executor"}:
        raise ValueError("Unknown tool failure policy")
    if api_timeout_policy not in {"failure", "infrastructure"}:
        raise ValueError("Unknown API timeout policy")
    outcome = termination(records, result=result)
    exception = result.get("exception_info") or {}
    error = exception.get("exception_type", "")
    if outcome["agent_timeout"]:
        return 0, "solver_timeout"
    if outcome["executor_failure"] or outcome["protocol_failure"]:
        return 0, "executor_or_protocol_failure"
    if tool_policy == "strict" and outcome["nonzero_exit"]:
        return 0, "nonzero_command_failure"
    if outcome["reason"] == "token_step_budget_exhaustion":
        return 0, "solver_budget_exhaustion"
    if outcome["api_timeout"]:
        return (
            (None, "api_timeout_infrastructure")
            if (api_timeout_policy == "infrastructure")
            else (0, "api_timeout_failure")
        )
    if error == "NonZeroAgentExitCodeError":
        return 0, "solver_failure"
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
        "reward"
    )
    if type(reward) not in (int, float) or reward not in (0, 1):
        return None, "missing_or_invalid_verifier"
    if error:
        return None, "verifier_or_infrastructure_exception"
    return int(reward == 1), "valid_verifier"


def prepare(job, output, config, source_manifest=None):
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if source_manifest and not manifest_path.exists():
        source = Path(source_manifest).resolve()
        previous = json.loads(source.read_text())
        if previous["job"] != str(job.resolve()):
            raise ValueError("A fixed calibration cannot change jobs")
        if previous["prompt_hashes"] != {
            j: digest(p) for j, p in PROMPTS.items()
        }:
            raise ValueError("Cannot reuse evidence with changed prompts")
        previous.update(
            sampling=SAMPLING,
            reused_manifest=str(source),
            reused_manifest_sha256=hashlib.sha256(
                source.read_bytes()
            ).hexdigest(),
        )
        manifest_path.write_text(json.dumps(previous, indent=2))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["job"] != str(job.resolve()):
            raise ValueError("A fixed calibration cannot change jobs")
        for judge, prompt in PROMPTS.items():
            if manifest["prompt_hashes"][judge] != digest(prompt):
                raise ValueError(
                    "Frozen prompts changed; use a new output directory"
                )
        if manifest["sampling"] != SAMPLING:
            raise ValueError("Sampling changed; use a new output directory")
        for entry in manifest["entries"]:
            JudgeInput.from_dict(
                json.loads(Path(entry["evidence_path"]).read_text())
            )
        return manifest
    split_path = ROOT / "data/tb2_split.json"
    split = json.loads(split_path.read_text())
    entries = []
    for name, partition, attempt, result_path in select_trials(job, split):
        trace = result_path.parent / "agent/trace.jsonl"
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        result = json.loads(result_path.read_text())
        evidence = export_trace(
            trace,
            output / "traces" / result_path.parent.name,
            result=result,
        )
        label, reason = verifier_label(result, records)
        evidence_path = (
            output / "traces" / result_path.parent.name / "evidence.json"
        )
        evidence_path.write_text(canonical(evidence.to_dict()))
        entries.append(
            {
                "task": name,
                "partition": partition,
                "attempt": attempt,
                "trial": result_path.parent.name,
                "evidence_path": str(evidence_path),
                "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                "result_sha256": hashlib.sha256(
                    result_path.read_bytes()
                ).hexdigest(),
                "oracle_label": label,
                "label_reason": reason,
                "raw_reward": (
                    (result.get("verifier_result") or {}).get("rewards") or {}
                ).get("reward"),
            }
        )
    manifest = {
        "version": "seed-calibration-v2",
        "evidence_version": VERSION,
        "job": str(job.resolve()),
        "selection": (
            "started_at ascending, trial_name tie-break; "
            "first 30 plus second 18 search"
        ),
        "split_sha256": hashlib.sha256(split_path.read_bytes()).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "prompt_hashes": {j: digest(p) for j, p in PROMPTS.items()},
        "repeats": {"JUDGE": 5, "XJUDGE": 1},
        "tau_rule": "sample SD of five equally task-weighted search means",
        "primary_threshold": 0.5,
        "a2_prereg_threshold": 0.8,
        "observation_chars": None,
        "entries": entries,
        "sampling": SAMPLING,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def enqueue_manifest(manifest, output, config):
    queues, projection = [], {}
    for prefix, repeats in manifest["repeats"].items():
        queue, endpoint = configured_queue(prefix, output, config)
        total, remaining = 0, 0
        existing = {r["id"]: r for r in queue.rows()}
        fixed_items = {
            (r["rollout"], r["judge"], r["repeat"]): r
            for r in existing.values()
        }
        for repeat in range(repeats):
            for entry in manifest["entries"]:
                evidence = JudgeInput.from_dict(
                    json.loads(Path(entry["evidence_path"]).read_text())
                )
                for judge in ("a1", "a2"):
                    previous = fixed_items.get((entry["trial"], judge, repeat))
                    if previous and previous["evidence"] != canonical(
                        evidence.to_dict()
                    ):
                        raise ValueError("Frozen trajectory evidence changed")
                    item_id = queue.enqueue(
                        entry["trial"], judge, evidence, repeat
                    )
                    backend = endpoint.backend(item_id, 1)
                    cost = backend.projected_cost(
                        build_prompt(judge, evidence)
                    )
                    total += 2 * cost
                    row = existing.get(item_id, {})
                    if row.get("status") not in {"done", "failed"}:
                        remaining += (2 - row.get("attempts", 0)) * cost
        projection[prefix] = {
            "all_items_two_attempt_bound_usd": total,
            "remaining_attempt_bound_usd": remaining,
        }
        queues.append(queue)
    remaining_total = sum(
        p["remaining_attempt_bound_usd"] for p in projection.values()
    )
    budget_path = ROOT / "costs/judges_budget.json"
    used = (
        json.loads(budget_path.read_text())["used_usd"]
        if (budget_path.exists())
        else 0
    )
    projection["used_or_reserved_usd"] = used
    projection["remaining_two_attempt_bound_usd"] = remaining_total
    projection["projected_total_usd"] = used + remaining_total
    projection["budget_limit_usd"] = 15
    (output / "projection.json").write_text(json.dumps(projection, indent=2))
    if projection["projected_total_usd"] > 15:
        for queue in queues:
            queue.close()
            queue.limiter.close()
        raise ValueError(
            "Projected calibration exceeds USD 15; no calls dispatched"
        )
    return queues, projection


def classification(scores, labels, threshold):
    pairs = [
        (s, y)
        for s, y in zip(scores, labels, strict=True)
        if y is not None and s is not None
    ]
    tp = sum(s >= threshold and y == 1 for s, y in pairs)
    fp = sum(s >= threshold and y == 0 for s, y in pairs)
    positives = sum(y == 1 for _, y in pairs)
    negatives = sum(y == 0 for _, y in pairs)
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "positive_denominator": positives,
        "negative_denominator": negatives,
        "tpr": tp / positives if positives else None,
        "fpr": fp / negatives if negatives else None,
    }


def roc_optimal(scores, labels):
    """Youden J, highest threshold breaks ties; descriptive, never adopted."""
    thresholds = sorted(
        {0.0, math.nextafter(1.0, 2.0), *(s for s in scores if s is not None)}
    )
    points = [
        classification(scores, labels, threshold) for threshold in thresholds
    ]
    valid = [
        p for p in points if p["tpr"] is not None and p["fpr"] is not None
    ]
    return (
        max(
            valid,
            key=lambda p: (
                Fraction(p["tp"], p["positive_denominator"])
                - Fraction(p["fp"], p["negative_denominator"]),
                p["threshold"],
            ),
        )
        if valid
        else None
    )


def aggregate_repeat_sd(task_scores):
    """Equal task weights, then sample SD in aggregate score units."""
    if not task_scores:
        raise ValueError("No search scores")
    first = next(iter(task_scores.values()))
    if not first:
        raise ValueError("Every task needs at least one trajectory")
    repeats = len(first[0])
    if not repeats or any(
        not attempts or any(len(a) != repeats for a in attempts)
        for attempts in task_scores.values()
    ):
        raise ValueError("Every task needs complete aligned repeat vectors")
    if any(
        type(s) not in (int, float) or not math.isfinite(s)
        for attempts in task_scores.values()
        for attempt in attempts
        for s in attempt
    ):
        raise ValueError("Repeat scores must be finite numbers")
    if repeats < 2:
        return None, []
    means = []
    for repeat in range(repeats):
        per_task = [
            statistics.mean(attempt[repeat] for attempt in attempts)
            for attempts in task_scores.values()
        ]
        means.append(statistics.mean(per_task))
    return statistics.stdev(means), means


def bootstrap_rates(entries, repeat_scores, threshold=0.5, draws=10000):
    """Task-cluster bootstrap; retain all attempts/repeats within each task."""
    tasks = defaultdict(list)
    for index, entry in enumerate(entries):
        tasks[entry["task"]].append(index)
    names, rng = sorted(tasks), random.Random(260911)
    rates = {"tpr": [], "fpr": []}
    for _ in range(draws):
        indexes = [
            i for task in rng.choices(names, k=len(names)) for i in tasks[task]
        ]
        scores = [s for i in indexes for s in repeat_scores[i]]
        labels = [
            entries[i]["oracle_label"]
            for i in indexes
            for _ in repeat_scores[i]
        ]
        result = classification(scores, labels, threshold)
        for key in rates:
            if result[key] is not None:
                rates[key].append(result[key])
    intervals = {
        key: (
            [
                sorted(values)[int(0.025 * len(values))],
                sorted(values)[min(len(values) - 1, int(0.975 * len(values)))],
            ]
            if values
            else None
        )
        for key, values in rates.items()
    }
    return {
        **intervals,
        "draws": draws,
        "seed": 260911,
        "undefined_draws": {k: draws - len(v) for k, v in rates.items()},
    }


def analyze(manifest, queues, output):
    entries = manifest["entries"]
    results = {}
    prices = json.loads((ROOT / "costs/judges_prices.json").read_text())
    for queue, prefix in zip(queues, manifest["repeats"], strict=True):
        rows = queue.rows()
        attempted = []
        if queue.ledger and queue.ledger.exists():
            with queue.ledger.open() as handle:
                attempted = [
                    r
                    for line in handle
                    if (r := json.loads(line)).get("run_id") == queue.run_id
                ]
        repeats = manifest["repeats"][prefix]
        by_key = {(r["rollout"], r["judge"], r["repeat"]): r for r in rows}
        for judge in ("a1", "a2"):
            vectors, calls, complete = [], [], True
            for entry in entries:
                vector = []
                for repeat in range(repeats):
                    row = by_key[(entry["trial"], judge, repeat)]
                    result = (
                        json.loads(row["result"]) if row["result"] else None
                    )
                    vector.append(result["score"] if result else None)
                    if result:
                        calls.append(result["record"])
                    else:
                        complete = False
                vectors.append(vector)
            flat = [s for vector in vectors for s in vector]
            labels = [
                e["oracle_label"] for e in entries for _ in range(repeats)
            ]
            report = {
                "complete": complete,
                "settled": all(
                    r["status"] in {"done", "failed"}
                    for r in rows
                    if r["judge"] == judge
                ),
                "failed": sum(
                    r["status"] == "failed"
                    for r in rows
                    if r["judge"] == judge
                ),
                "scored": len(calls),
                "scheduled": len(flat),
                "missing_judge_by_oracle_label": {
                    str(label): sum(
                        s is None and y == label
                        for s, y in zip(flat, labels, strict=True)
                    )
                    for label in (0, 1, None)
                },
                "classification_0.5": classification(flat, labels, 0.5),
                "classification_prereg": classification(
                    flat, labels, 0.8 if judge == "a2" else 0.5
                ),
                "classification_raw_reward_0.5": classification(
                    flat,
                    [
                        e["raw_reward"] if e["raw_reward"] in (0, 1) else None
                        for e in entries
                        for _ in range(repeats)
                    ],
                    0.5,
                ),
                "roc_optimal": roc_optimal(flat, labels),
                "per_trajectory": {
                    e["trial"]: v
                    for e, v in zip(entries, vectors, strict=True)
                },
                "served_models": sorted({r["model"] for r in calls}),
                "mean_latency_s": statistics.mean(
                    r["wall_s"] for r in calls if r["wall_s"] is not None
                )
                if any(r["wall_s"] is not None for r in calls)
                else None,
                "known_cost_usd": sum(
                    r["cost_usd"] for r in calls if r["cost_usd"] is not None
                ),
                "unknown_cost_calls": sum(
                    r["cost_usd"] is None for r in calls
                ),
            }
            # Missing cache telemetry is not zero usage. Report a separate
            # all-input-uncached planning upper estimate without editing the
            # original ledger or releasing its conservative budget reserves.
            estimates = []
            for record in calls:
                rates = prices["models"].get(record["model"])
                if rates and all(
                    record.get(k) is not None
                    for k in (
                        "input_tokens",
                        "output_tokens",
                    )
                ):
                    estimates.append(
                        (
                            record["input_tokens"]
                            * max(rates["input"], rates["cache_write"])
                            + record["output_tokens"] * rates["output"]
                        )
                        / 1_000_000
                    )
            report["uncached_planning_total_usd"] = (
                sum(estimates) if len(estimates) == len(calls) else None
            )
            report["uncached_planning_mean_usd"] = (
                statistics.mean(estimates)
                if estimates and len(estimates) == len(calls)
                else None
            )
            attempts = [r for r in attempted if r["arm"] == judge.upper()]
            attempt_estimates = []
            for record in attempts:
                rates = prices["models"].get(record["model"])
                if rates and all(
                    record.get(k) is not None
                    for k in (
                        "input_tokens",
                        "output_tokens",
                    )
                ):
                    attempt_estimates.append(
                        (
                            record["input_tokens"]
                            * max(rates["input"], rates["cache_write"])
                            + record["output_tokens"] * rates["output"]
                        )
                        / 1_000_000
                    )
            report["attempts"] = len(attempts)
            report["attempt_uncached_planning_mean_usd"] = (
                statistics.mean(attempt_estimates)
                if attempt_estimates
                and len(attempt_estimates) == len(attempts)
                else None
            )
            report["attempt_uncached_planning_total_usd"] = (
                sum(attempt_estimates)
                if len(attempt_estimates) == len(attempts)
                else None
            )
            if calls and not report["unknown_cost_calls"]:
                report["mean_call_cost_usd"] = report["known_cost_usd"] / len(
                    calls
                )
            else:
                report["mean_call_cost_usd"] = None
            report["bootstrap_ci95"] = bootstrap_rates(entries, vectors)
            if complete and repeats >= 2:
                search = defaultdict(list)
                for entry, vector in zip(entries, vectors, strict=True):
                    if entry["partition"] == "search":
                        search[entry["task"]].append(vector)
                tau, means = aggregate_repeat_sd(search)
                sds = [statistics.stdev(v) for v in vectors]
                report.update(
                    tau=tau,
                    search_repeat_means=means,
                    pooled_within_trajectory_sd=math.sqrt(
                        statistics.mean(s * s for s in sds)
                    ),
                    mean_within_trajectory_sd=statistics.mean(sds),
                    all_trajectory_mean_sd=statistics.stdev(
                        statistics.mean(v[r] for v in vectors)
                        for r in range(repeats)
                    ),
                    per_trajectory_sd={
                        e["trial"]: sd
                        for e, sd in zip(entries, sds, strict=True)
                    },
                )
                for threshold in (0.5, 0.8):
                    fprs = []
                    for repeat in range(repeats):
                        selected = [
                            (e, v[repeat])
                            for e, v in zip(entries, vectors, strict=True)
                            if e["partition"] == "search"
                        ]
                        rates = classification(
                            [v for _, v in selected],
                            [e["oracle_label"] for e, _ in selected],
                            threshold,
                        )
                        fprs.append(rates["fpr"])
                    report[f"search_sigma_fpr_{threshold}"] = (
                        statistics.stdev(fprs) if None not in fprs else None
                    )
            results[f"{prefix}/{judge}"] = report
    (output / "metrics.json").write_text(
        json.dumps(results, indent=2, allow_nan=False)
    )
    return results


def observed_operations(queues):
    """Use durable dispatch/completion timestamps; include restart pauses."""
    report = {}
    for queue in queues:
        records = []
        if queue.ledger and queue.ledger.exists():
            with queue.ledger.open() as handle:
                for line in handle:
                    record = json.loads(line)
                    if record.get("run_id") == queue.run_id:
                        records.append(record)
        events = []
        if queue.event_log.exists():
            events = [
                json.loads(s) for s in queue.event_log.read_text().splitlines()
            ]
        dispatches = [e for e in events if e["status"] == "dispatch"]
        if not records:
            report[queue.run_id] = {"calls": 0}
            continue
        starts = [datetime.fromisoformat(r["ts"]).timestamp() for r in records]
        elapsed = max(
            s + (r["wall_s"] or 0)
            for s, r in zip(
                starts,
                records,
                strict=True,
            )
        ) - min(starts)
        tokens = sum(
            (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
            for r in records
        )
        report[queue.run_id] = {
            "calls": len(records),
            "queue_counts": queue.counts(),
            "elapsed_including_pauses_s": elapsed,
            "calls_per_minute": 60 * len(records) / elapsed,
            "observed_tokens": tokens,
            "observed_tokens_per_minute": 60 * tokens / elapsed,
            "mean_latency_s": statistics.mean(
                r["wall_s"] for r in records if r["wall_s"] is not None
            )
            if any(r["wall_s"] is not None for r in records)
            else None,
            "unknown_latency_calls": sum(r["wall_s"] is None for r in records),
            "http_429s": sum(r["http_429s"] for r in records),
            "unknown_cost_calls": sum(r["cost_usd"] is None for r in records),
            "limiter_wait_s_sum": sum(e["limiter_wait_s"] for e in dispatches),
            "reserved_tokens": sum(e["reserved_tokens"] for e in dispatches),
            "rpm": queue.limiter.rpm,
            "tpm": queue.limiter.tpm,
            "max_reservation": max(e["reserved_tokens"] for e in dispatches),
            "endpoint_signature": queue.signature,
        }
    return report


def memory_guard(path=Path("/proc/meminfo")):
    memory = dict(line.split(":", 1) for line in path.read_text().splitlines())
    available_kib = int(memory["MemAvailable"].split()[0])
    if available_kib < 6 * 1024**2:
        raise RuntimeError("Variance control requires 6 GiB MemAvailable")
    return available_kib


async def main_async(args):
    if args.run or args.probe:
        available = memory_guard()
        print(canonical({"mem_available_kib": available}), flush=True)
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    config["JUDGING_PRICES_PATH"] = str(ROOT / "costs/judges_prices.json")
    config.setdefault("XJUDGE_QUEUE_SUFFIX", "cap8192")
    job, output = Path(args.job).resolve(), Path(args.output).resolve()
    manifest = prepare(
        job, output, config, getattr(args, "reuse_manifest", None)
    )
    queues, projection = enqueue_manifest(manifest, output, config)
    print(
        canonical(
            {
                "trajectories": len(manifest["entries"]),
                "projection": projection,
            }
        ),
        flush=True,
    )
    try:
        if args.run or args.probe:
            # Endpoints run sequentially so the global ceiling is four,
            # including retries. No additional process is needed.
            selected = getattr(args, "endpoints", "JUDGE,XJUDGE").split(",")
            for queue, prefix in zip(queues, manifest["repeats"], strict=True):
                if prefix not in selected:
                    continue
                print(f"Running {queue.run_id}", flush=True)
                await queue.run(
                    args.concurrency, limit=2 if args.probe else None
                )
        metrics = analyze(manifest, queues, output)
        operations = observed_operations(queues)
        (output / "operations.json").write_text(
            json.dumps(operations, indent=2)
        )
        if getattr(args, "write_docs", False):
            from evolution.report import render_report

            budget = json.loads(
                (ROOT / "costs/judges_budget.json").read_text()
            )
            with (ROOT / "costs/judges_ledger.jsonl").open() as handle:
                ledger = [json.loads(line) for line in handle]
            (ROOT / "docs/judges.md").write_text(
                render_report(
                    manifest,
                    metrics,
                    operations,
                    output,
                    budget,
                    ledger,
                )
            )
        print(
            canonical(
                {
                    k: {
                        x: v[x]
                        for x in (
                            "scored",
                            "scheduled",
                            "failed",
                            "complete",
                            "settled",
                        )
                    }
                    for k, v in metrics.items()
                }
            ),
            flush=True,
        )
        return metrics
    finally:
        for queue in queues:
            queue.close()
            queue.limiter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default=str(DEFAULT_JOB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--reuse-manifest", type=Path)
    parser.add_argument("--endpoints", default="JUDGE,XJUDGE")
    parser.add_argument("--write-docs", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
