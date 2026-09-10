"""R6 label correction from archived scores. No endpoint or queue is opened.

Run: uv run python -m evolution.reanalyze_judges [--write-docs]
Original scores, prompts, manifests, evidence and costs remain immutable.
"""

import argparse
import copy
import hashlib
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

from evolution.calibration import (
    DEFAULT_OUTPUT,
    aggregate_repeat_sd,
    bootstrap_rates,
    classification,
    roc_optimal,
    verifier_label,
)
from evolution.outcomes import termination
from evolution.sanitize import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "logs/judges/r6-label-correction-v1"


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def summarize(entries, vectors, judge, *, draws=10000):
    flat = [s for v in vectors for s in v]
    labels = [
        e["oracle_label"]
        for e, v in zip(entries, vectors, strict=True)
        for _ in v
    ]
    result = {
        "classification_0.5": classification(flat, labels, 0.5),
        "classification_prereg": classification(
            flat,
            labels,
            0.8 if judge == "a2" else 0.5,
        ),
        "roc_optimal": roc_optimal(flat, labels),
        "bootstrap_ci95": bootstrap_rates(entries, vectors, draws=draws),
        "bootstrap_prereg_ci95": bootstrap_rates(
            entries,
            vectors,
            0.8 if judge == "a2" else 0.5,
            draws=draws,
        ),
        "missing_judge_by_oracle_label": {
            str(label): sum(
                s is None and y == label
                for s, y in zip(flat, labels, strict=True)
            )
            for label in (0, 1, None)
        },
    }
    # Worst/best missing-score assignments over the fixed labelled cohort.
    bounds = {}
    for threshold in (0.5, 0.8):
        limits = [
            classification(
                [fill if s is None else s for s in flat],
                labels,
                threshold,
            )
            for fill in (0, 1)
        ]
        bounds[str(threshold)] = {
            k: [p[k] for p in limits] for k in ("tpr", "fpr")
        }
    result["missing_score_bounds"] = bounds
    if all(s is not None for s in flat) and len(vectors[0]) >= 2:
        search = defaultdict(list)
        for e, v in zip(entries, vectors, strict=True):
            if e["partition"] == "search":
                search[e["task"]].append(v)
        result["tau"], result["search_repeat_means"] = aggregate_repeat_sd(
            search,
        )
        for threshold in (0.5, 0.8):
            fprs = [
                classification(
                    [
                        v[r]
                        for e, v in zip(entries, vectors, strict=True)
                        if e["partition"] == "search"
                    ],
                    [
                        e["oracle_label"]
                        for e in entries
                        if e["partition"] == "search"
                    ],
                    threshold,
                )["fpr"]
                for r in range(len(vectors[0]))
            ]
            result[f"search_sigma_fpr_{threshold}"] = (
                statistics.stdev(fprs) if None not in fprs else None
            )
    return result


def verify_scores(source, metrics):
    """Independently check archived vectors with read-only SQLite."""
    queues = {"JUDGE": "judge.sqlite", "XJUDGE": "xjudge-cap8192.sqlite"}
    verified = 0
    for prefix, name in queues.items():
        path = source / name
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            for rollout, judge, repeat, result in db.execute(
                "SELECT rollout,judge,repeat,result FROM items"
            ):
                score = json.loads(result)["score"] if result else None
                if (
                    score
                    != metrics[f"{prefix}/{judge}"]["per_trajectory"][rollout][
                        repeat
                    ]
                ):
                    raise ValueError("Archived queue/score mismatch")
                verified += 1
    return verified


def recompute(source=DEFAULT_OUTPUT, output=OUTPUT, *, draws=10000):
    source, output = Path(source), Path(output)
    if output.resolve() == source.resolve():
        raise ValueError("Correction must use a separate archive directory")
    manifest = json.loads((source / "manifest.json").read_text())
    archived = json.loads((source / "metrics.json").read_text())
    verified = verify_scores(source, archived)
    entries = manifest["entries"]
    corrections, affected, outcomes = [], [], {}
    readings = {
        f"{tool}/{api}": copy.deepcopy(entries)
        for tool in ("strict", "executor")
        for api in ("failure", "infrastructure")
    }
    for index, entry in enumerate(entries):
        trial = Path(manifest["job"]) / entry["trial"]
        trace, result_path = trial / "agent/trace.jsonl", trial / "result.json"
        if sha256(trace) != entry["trace_sha256"] or (
            sha256(result_path) != entry["result_sha256"]
        ):
            raise ValueError(f"Archived source changed: {entry['trial']}")
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        result = json.loads(result_path.read_text())
        outcomes[entry["trial"]] = termination(records, result=result)
        for reading, cohort in readings.items():
            tool, api = reading.split("/")
            label, reason = verifier_label(
                result,
                records,
                tool_policy=tool,
                api_timeout_policy=api,
            )
            cohort[index].update(oracle_label=label, label_reason=reason)
        corrected = readings["strict/failure"][index]
        if corrected["oracle_label"] != entry["oracle_label"]:
            corrections.append(
                {
                    "trial": entry["trial"],
                    "before": entry["oracle_label"],
                    "after": corrected["oracle_label"],
                    "reason": corrected["label_reason"],
                    "result_sha256": entry["result_sha256"],
                    "trace_sha256": entry["trace_sha256"],
                }
            )
        evidence_path = Path(entry["evidence_path"])
        evidence = json.loads(evidence_path.read_text())
        instruction = next(
            e["text"] for e in records if e["kind"] == "instruction"
        )
        if evidence["task_text"] != instruction:
            affected.append(
                {
                    "trial": entry["trial"],
                    "task": entry["task"],
                    "evidence_path": str(evidence_path),
                    "evidence_sha256": sha256(evidence_path),
                    "instruction_sha256": digest(instruction),
                    "archived_task_text": evidence["task_text"],
                    "scores_unchanged": True,
                    "effect_of_restored_instruction": (
                        "unknown_without_rejudging"
                    ),
                    "scores": {
                        k: v["per_trajectory"][entry["trial"]]
                        for k, v in archived.items()
                    },
                }
            )
    reports, cache = {}, {}
    for reading, cohort in readings.items():
        key = tuple(e["oracle_label"] for e in cohort)
        if key not in cache:
            cache[key] = {
                scorer: summarize(
                    cohort,
                    [v["per_trajectory"][e["trial"]] for e in entries],
                    scorer.split("/")[1],
                    draws=draws,
                )
                for scorer, v in archived.items()
            }
        reports[reading] = {
            "label_counts": {
                str(label): key.count(label) for label in (0, 1, None)
            },
            "labels": {e["trial"]: e["oracle_label"] for e in cohort},
            "metrics": cache[key],
        }
    report = {
        "version": "r6-label-correction-v1",
        "source": str(source),
        "source_hashes": {
            name: sha256(source / name)
            for name in ("manifest.json", "metrics.json")
        },
        "score_vectors_sha256": digest(
            canonical({k: v["per_trajectory"] for k, v in archived.items()})
        ),
        "verified_queue_measurements": verified,
        "model_calls": 0,
        "label_corrections": corrections,
        "affected_instructions": affected,
        "termination": outcomes,
        "before": {k: v["classification_0.5"] for k, v in archived.items()},
        "readings": reports,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                **manifest,
                "analysis_version": report["version"],
                "source_manifest_sha256": report["source_hashes"][
                    "manifest.json"
                ],
                "entries": readings["strict/failure"],
            },
            indent=2,
        )
        + "\n"
    )
    (output / "report.md").write_text(render(report))
    return report


def rate(point, name):
    numerator = point["tp" if name == "tpr" else "fp"]
    denominator = point[
        "positive_denominator" if name == "tpr" else "negative_denominator"
    ]
    return (
        f"{100 * numerator / denominator:.2f}% ({numerator}/{denominator})"
        if denominator
        else "undefined"
    )


def interval(value):
    return (
        "–".join(f"{v:.2%}" for v in value)
        if value is not None
        else "undefined"
    )


def render(report):
    metrics = report["readings"]["strict/failure"]["metrics"]
    lines = [
        "## R6 correction from archived scores",
        "",
        "The seven missing-reward trajectories are proven in-rollout executor "
        "failures (30-second command timeouts), retained as failures "
        "under A9. "
        "The corrected strict cohort has **3 positives, 45 negatives, and "
        "0 unlabelled trajectories**. No model calls or solver reruns "
        "were made; "
        "all archived scores and evidence are unchanged.",
        "",
        "| Scorer | Before TPR at 0.5 | After TPR at 0.5 | Before FPR at 0.5 "
        "| After FPR at 0.5 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for scorer, before in report["before"].items():
        after = metrics[scorer]["classification_0.5"]
        lines.append(
            f"| {scorer} | {rate(before, 'tpr')} | "
            f"{rate(after, 'tpr')} | {rate(before, 'fpr')} | "
            f"{rate(after, 'fpr')} |"
        )
    lines += [
        "",
        "A2's preregistered positive threshold remains **0.8**. "
        "The requested 0.5 table is descriptive, not a threshold amendment.",
        "",
        "| Scorer | TPR at 0.8 | FPR at 0.8 | Search sigma_FPR at 0.8 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for scorer in ("JUDGE/a2", "XJUDGE/a2"):
        point = metrics[scorer]["classification_prereg"]
        sigma = metrics[scorer].get("search_sigma_fpr_0.8")
        lines.append(
            f"| {scorer} | {rate(point, 'tpr')} | {rate(point, 'fpr')} "
            f"| {sigma if sigma is not None else 'undefined'} |"
        )
    lines += [
        "",
        "| Scorer | Tau (unchanged) | Corrected search sigma_FPR at 0.5 "
        "| Descriptive ROC threshold | ROC TPR | ROC FPR |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scorer, m in metrics.items():
        roc = m["roc_optimal"]
        lines.append(
            f"| {scorer} | {m.get('tau', 'undefined')} | "
            f"{m.get('search_sigma_fpr_0.5', 'undefined')} | "
            f"{roc['threshold']:.6f} | {rate(roc, 'tpr')} | "
            f"{rate(roc, 'fpr')} |"
        )
    lines += [
        "",
        "Tau remains the sample SD of five equally task-weighted "
        "search-repeat means: label changes cannot change score variance. "
        "These tau values describe the archived v1 evidence condition only. "
        "Kimi has one repeat, so its tau and sigma_FPR remain undefined. "
        "ROC maxima use Youden J with the highest threshold breaking ties; "
        "they are not adopted.",
        "",
        "Observed-pair task-cluster bootstrap, 10,000 draws, seed "
        "260911, carrying all attempts/repeats per sampled task. Undefined "
        "conditional draws are counted rather than imputed. Missing-score "
        "bounds assign every missing score negative or positive over all "
        "45 oracle negatives; observed-pair intervals do not remove "
        "availability bias.",
        "",
        "| Scorer | TPR 95% CI at 0.5 | FPR 95% CI at 0.5 | Undefined "
        "TPR/FPR draws | Missing scores (+ / −) | FPR missing-score bounds |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scorer, m in metrics.items():
        ci, missing = m["bootstrap_ci95"], m["missing_judge_by_oracle_label"]
        undefined = ci["undefined_draws"]
        lines.append(
            f"| {scorer} | {interval(ci['tpr'])} | "
            f"{interval(ci['fpr'])} | {undefined['tpr']}/"
            f"{undefined['fpr']} | {missing['1']} / {missing['0']} | "
            f"{interval(m['missing_score_bounds']['0.5']['fpr'])} |"
        )
    lines += [
        "",
        "**Pending AD10:** strict A9 counts nonzero command exits as "
        "failures; the executor-only reading treats them as ordinary "
        "observations but still fails command timeouts, execution exceptions, "
        "and action protocol errors. The seven corrected cases fail under "
        "both readings. Neither pending decision is treated as ratified.",
        "",
        "| Scorer | AD10 executor-only TPR at 0.5 | FPR at 0.5 | "
        "TPR at preregistered threshold | FPR at preregistered threshold |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    alternative = report["readings"]["executor/failure"]
    for scorer, m in alternative["metrics"].items():
        p, q = m["classification_0.5"], m["classification_prereg"]
        lines.append(
            f"| {scorer} | {rate(p, 'tpr')} | {rate(p, 'fpr')} | "
            f"{rate(q, 'tpr')} | {rate(q, 'fpr')} |"
        )
    api_timeouts = sum(
        v["api_timeout"] for v in report["termination"].values()
    )
    lines += [
        "",
        f"**Pending AD13:** {api_timeouts} selected attempts have an "
        "identified API timeout. Both readings (API timeout as failure versus "
        "infrastructure exclusion) are recomputed in the analysis artifact; "
        "they yield identical rates in this cohort. These seven timeouts "
        "occurred inside command execution, not at the model API. Unknown "
        "grader/infrastructure failures remain unlabelled, with exclusions "
        "requiring the separate A9 retry record.",
        "",
        "Corrected trajectory labels:",
        "",
    ]
    lines += [
        f"- `{e['trial']}`: missing → failure; {e['reason']}."
        for e in report["label_corrections"]
    ]
    lines += [
        "",
        "**Old-sanitizer score flags:** both instructions below became "
        "`[REDACTED]` in the paid calibration. All associated scores are "
        "flagged as affected by missing task context, retained numerically "
        "unchanged; the effect of restored instructions on the scores is "
        "unknown without rejudging. The regression fixtures preserve both "
        "original traces, including the `/app/test_outputs.py` mention.",
        "",
        "| Trajectory | DeepSeek A1 repeats | DeepSeek A2 repeats | "
        "Kimi A1 | Kimi A2 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in report["affected_instructions"]:
        values = [str(entry["scores"][k]) for k in metrics]
        lines.append(f"| `{entry['trial']}` | " + " | ".join(values) + " |")
    lines += [
        "",
        "The [versioned correction](../logs/judges/"
        "r6-label-correction-v1/analysis.json) includes source hashes, "
        "all policy readings, uncertainty, missing-score bounds, instruction "
        "flags, and checks against all 576 durable queue measurements. "
        "The [corrected manifest](../logs/judges/r6-label-correction-v1/"
        "manifest.json) references the original evidence. The original "
        "[manifest](../logs/judges/seed-mini-json-v2/manifest.json) and "
        "[score vectors](../logs/judges/seed-mini-json-v2/metrics.json) "
        "remain "
        "historical records; their old label-dependent statistics "
        "are superseded.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-docs", action="store_true")
    args = parser.parse_args()
    report = recompute()
    if args.write_docs:
        path = ROOT / "docs/judges.md"
        start, end = "<!-- R6_ANALYSIS_BEGIN -->", "<!-- R6_ANALYSIS_END -->"
        text = path.read_text()
        prefix, rest = text.split(start, 1)
        _, suffix = rest.split(end, 1)
        path.write_text(prefix + start + "\n" + render(report) + end + suffix)
    print(render(report).split("A2's preregistered", 1)[0])


if __name__ == "__main__":
    main()
