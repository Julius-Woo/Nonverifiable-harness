"""Render the measured judge report without any network or solver access."""

import json
from collections import Counter
from pathlib import Path

from evolution.judges import PROMPT_VERSION, PROMPTS
from evolution.sanitize import ALLOWED, VERSION


def number(value, places=6):
    return "undefined" if value is None else f"{value:.{places}f}"


def percent(value):
    return "undefined" if value is None else f"{100 * value:.2f}%"


def rate_cells(rates):
    return (
        f"{percent(rates['tpr'])} ({rates['tp']}/"
        f"{rates['positive_denominator']}) | "
        f"{percent(rates['fpr'])} ({rates['fp']}/"
        f"{rates['negative_denominator']})"
    )


def measurements_ready(metrics):
    """Require full primary repeats and bounded, terminal cross judgments."""
    return (
        {"JUDGE/a1", "JUDGE/a2", "XJUDGE/a1", "XJUDGE/a2"} <= metrics.keys()
        and all(m.get("settled", False) for m in metrics.values())
        and all(
            m["complete"]
            for name, m in metrics.items()
            if name.startswith("JUDGE/")
        )
    )


def render_report(manifest, metrics, operations, output, budget, ledger):
    """All table entries come from archived responses, never placeholders."""
    if not measurements_ready(metrics):
        raise ValueError(
            "The report requires every scheduled score to finish under A9 "
            "and complete primary variance repeats"
        )
    if manifest.get("evidence_version") != VERSION:
        raise ValueError(
            "Historical v1 reports use evolution.reanalyze_judges; "
            "do not regenerate them with the current evidence contract"
        )
    entries = manifest["entries"]
    counts = Counter(e["partition"] for e in entries)
    labels = Counter(e["oracle_label"] for e in entries)
    root = Path(__file__).resolve().parents[1]
    artifact_dir = str(Path(output).resolve().relative_to(root))
    lines = [
        "# Judges and sealed-anchor acceptance (P1.4 / P1.7)",
        "",
        "Measured on 2026-09-10 using the fixed, completed mini/JSON seed "
        "job. A1 measures outcome plausibility; A2 measures **procedural "
        "compliance**. Its disagreement with the outcome verifier includes "
        "construct disagreement, not just scoring error.",
        "",
        "## Fixed evidence and estimands",
        "",
        f"The [manifest](../{artifact_dir}/manifest.json) fixes "
        "48 trajectories: "
        "the first attempt of each of 30 tasks and the second attempt of each "
        "of 18 search tasks. Attempts are ordered by `started_at`, with trial "
        "name as tie-breaker. The Harbor job contains 61 result records; no "
        "solver, verifier, container, or Harbor job was rerun by W8.",
        "",
        "| Partition | Tasks | Selected trajectories |",
        "| --- | ---: | ---: |",
    ]
    for partition, count in counts.items():
        tasks = len(
            {e["task"] for e in entries if e["partition"] == partition}
        )
        lines.append(f"| {partition} | {tasks} | {count} |")
    lines += [
        "",
        f"There are {labels[1]} valid positives, {labels[0]} valid negatives, "
        f"and {labels[None]} unlabelled trajectories. Primary labels "
        "implement "
        "PREREG: reward 1 requires a valid verifier and no solver timeout or "
        "in-rollout tool failure. Raw rewards remain in the manifest and "
        "receive a separate sensitivity table below. Proven solver and "
        "executor failures count before verifier availability is checked; "
        "unresolved grader/infrastructure outcomes remain unlabelled.",
        "",
        "Evidence v2 retains complete sanitized observations and explicit "
        "termination fields. No observation cap is permitted; oversized "
        "requests fail capacity checks before model dispatch. "
        "Task instruction "
        "text is preserved exactly, including hidden-file mentions. Hidden "
        "contents and paired test outputs are removed with hash-only "
        "provenance. Historical v1 scores retain their old evidence contract "
        "and are reported separately by evolution.reanalyze_judges.",
        "",
        "## Measured variance and frozen tau",
        "",
        "DeepSeek scores all 48 trajectories five times with A1 and A2 "
        "(480 successful scores). For each repeat, average the two attempts "
        "within each search task, then average the 18 tasks equally. **Tau "
        "is the sample SD (`ddof=1`) of those five aggregate search scores**, "
        "in [0,1] score units, following PREREG Section 5. It is not the "
        "dispersion between different tasks. The pooled within-trajectory "
        "SD is `sqrt(mean(sample_variance(five scores)))` over all 48 traces. "
        "Kimi attempts the same set once per judge (96 scheduled scores, "
        "with the single A9 retry for failures), "
        "so Kimi repeat SD and tau are not estimable. Epsilon remains **0**.",
        "",
        "| Model / judge | Scores / scheduled (failed) | "
        "Pooled within-trajectory SD | "
        "Search aggregate SD = tau | Search FPR SD at 0.5 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, report in metrics.items():
        lines.append(
            f"| {name} | {report['scored']}/{report['scheduled']} "
            f"({report['failed']}) | "
            f"{number(report.get('pooled_within_trajectory_sd'))} | "
            f"{number(report.get('tau'))} | "
            f"{number(report.get('search_sigma_fpr_0.5'))} |"
        )
    lines += [
        "",
        "Search aggregate scores in repeat order (repeat IDs 0–4):",
        "",
    ]
    for name, report in metrics.items():
        if "search_repeat_means" in report:
            means = ", ".join(number(v) for v in report["search_repeat_means"])
            lines.append(f"- **{name}:** {means}.")
    lines += [
        "",
        f"Exact values, all 48 per-trajectory repeat vectors, SDs, and "
        f"task-cluster bootstrap intervals are in "
        f"[metrics.json](../{artifact_dir}/metrics.json). Repeats measure "
        "judge noise with a fixed solver; they do not estimate solver "
        "variability. Repeat labels are local IDs, not provider RNG seeds. "
        "Five replicates give a noisy SD estimate.",
        "",
        "## Seed classification",
        "",
        "The requested threshold **score >= 0.5** is primary below. "
        "Pool the five independently judged scores for each labelled "
        "DeepSeek trajectory; the denominator therefore repeats each "
        "trajectory five times. These are not independent task observations. "
        "The deterministic 10,000-draw bootstrap resamples whole task blocks "
        "with all their attempts and repeats (local seed 260911). "
        "Kimi contributes at most one observed score per labelled trajectory.",
        "",
        "Kimi failures after the one allowed retry remain missing, never "
        "zero or favourable scores. Classification uses paired observed "
        "scores and labels, with denominators shown. These missing "
        "judgments can bias the descriptive cross-judge comparison; the "
        "full oracle cohort is retained separately in the manifest.",
        "",
        "| Model / judge | TPR at 0.5 | FPR at 0.5 | "
        "Descriptive ROC-optimal threshold | ROC TPR | ROC FPR |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, report in metrics.items():
        roc = report["roc_optimal"]
        threshold = (
            "above 1 (all negative)"
            if roc and roc["threshold"] > 1
            else number(roc["threshold"] if roc else None)
        )
        lines.append(
            f"| {name} | {rate_cells(report['classification_0.5'])} | "
            f"{threshold} | {percent(roc['tpr'] if roc else None)} | "
            f"{percent(roc['fpr'] if roc else None)} |"
        )
    lines += [
        "",
        "Missing judge scores by oracle label:",
        "",
        "| Model / judge | Oracle positive | Oracle negative | Unlabelled |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, report in metrics.items():
        missing = report["missing_judge_by_oracle_label"]
        lines.append(
            f"| {name} | {missing['1']} | {missing['0']} | {missing['None']} |"
        )
    lines += [
        "",
        "ROC-optimal means maximum Youden J (TPR − FPR), with the highest "
        "threshold breaking ties. It is an in-sample diagnostic and **is not "
        "adopted**. A2's preregistered pilot positive label remains "
        "**score >= 0.8**; the task-requested 0.5 table does not amend it.",
        "",
        "| A2 model | TPR at preregistered 0.8 | FPR at 0.8 | Search FPR SD |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, report in metrics.items():
        if name.endswith("/a2"):
            lines.append(
                f"| {name} | {rate_cells(report['classification_prereg'])} | "
                f"{number(report.get('search_sigma_fpr_0.8'))} |"
            )
    lines += [
        "",
        "Sensitivity: direct comparison to available raw reward "
        "(before the timeout/tool-failure override), at threshold 0.5:",
        "",
        "| Model / judge | Raw-reward TPR | Raw-reward FPR |",
        "| --- | ---: | ---: |",
    ]
    for name, report in metrics.items():
        rates = report["classification_raw_reward_0.5"]
        lines.append(f"| {name} | {rate_cells(rates)} |")
    lines += [
        "",
        "Unlabelled fixed trajectories (still judged in all repeats):",
        "",
    ]
    for entry in entries:
        if entry["oracle_label"] is None:
            lines.append(f"- `{entry['trial']}`: `{entry['label_reason']}`.")
    lines += [
        "",
        "## Cost, latency, and observed throughput",
        "",
        "Every attempted request uses the unchanged `OpenAIAPIBackend` "
        "and [judge ledger](../costs/judges_ledger.jsonl), role `judge`. "
        "Some responses omit cached-input counts. The ledger therefore "
        "correctly retains unknown USD costs for those calls. The "
        "table's dollar amounts are **planning upper estimates** from "
        "measured input/output tokens, charging every input token as "
        "uncached at [the planning rates](../costs/judges_prices.json); "
        "they are not invoice-verified billing. DeepSeek rates are $0.50 "
        "input / $1.20 output per million tokens; Kimi rates are $1 / $4. "
        "Reasoning, where present, is included in output usage and is not "
        "charged a second time.",
        "",
        "| Model / judge | API attempts | Mean estimated USD / attempt | "
        "Estimated total USD (all attempts) | "
        "Mean successful-call latency (s) | Unknown-cost successful calls |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, report in metrics.items():
        lines.append(
            f"| {name} | {report['attempts']} | "
            f"{number(report['attempt_uncached_planning_mean_usd'])} | "
            f"{number(report['attempt_uncached_planning_total_usd'])} | "
            f"{number(report['mean_latency_s'], 2)} | "
            f"{report['unknown_cost_calls']} |"
        )
    lines += [
        "",
        "| Queue | Attempts | Elapsed minutes | Calls/min | "
        "Observed tokens/min | Reserved RPM / TPM | 429s |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for name, report in operations.items():
        lines.append(
            f"| {name} | {report['calls']} | "
            f"{number(report['elapsed_including_pauses_s'] / 60, 2)} | "
            f"{number(report['calls_per_minute'], 2)} | "
            f"{number(report['observed_tokens_per_minute'], 0)} | "
            f"{report['rpm']:.0f} / {report['tpm']:.0f} | "
            f"{report['http_429s']} |"
        )
    run_ids = set(operations)
    setup = [r for r in ledger if r.get("run_id") not in run_ids]
    lines += [
        "",
        "Throughput spans the first dispatch to last completion, including "
        "probe/resume pauses. It is observed throughput, not a claimed "
        "saturation benchmark. The dual token bucket reserves UTF-8 prompt "
        "bytes + 256 overhead + maximum completion tokens per attempt, "
        "without token refunds. This intentionally overestimates usage. "
        "At most four judge API requests were in flight globally; endpoints "
        "were run sequentially. MemAvailable was checked against 6 GiB "
        "before live runs.",
        "",
        f"The shared budget currently retains **${budget['used_usd']:.6f}** "
        "of the **$15** cap, including unpriced-call reservations. "
        f"There are {len(setup)} archived setup/compatibility attempts "
        "outside the final measurement queues. Rejected `thinking` "
        "parameters and Kimi's exhausted 2,048-token responses remain "
        "archived; no completed scores were regenerated. Larger output "
        "allowances use an explicitly distinct queue configuration, never "
        "a reset retry counter. The projection includes remaining permitted "
        "attempts plus all previously consumed or reserved budget.",
        "",
        "## Prompts and schemas",
        "",
        f"Prompt version: `{PROMPT_VERSION}`. Sanitizer version: `{VERSION}`. "
        "The following prompts are verbatim; each is followed by "
        "`\\n\\nEvidence JSON:\\n` and canonical JSON evidence. "
        "Both model families use the same prompts and response parsers.",
        "",
    ]
    lines += [
        "Sampling and endpoint settings actually used by the final queues:",
        "",
        "```json",
        json.dumps(
            {
                name: report["endpoint_signature"]
                for name, report in operations.items()
            },
            indent=2,
        ),
        "```",
        "",
    ]
    for name, prompt in PROMPTS.items():
        lines += [
            f"### {name.upper()} prompt",
            "",
            "```text",
            prompt,
            "```",
            "",
        ]
    lines += [
        "### Evidence contracts",
        "",
        "`JudgeInput` accepts exactly `{task_text: str, trajectory: "
        "SanitizedTrajectory}`. The immutable trajectory envelope is "
        "`{version: str, events: list[allowlisted scalar event objects]}`. "
        "Additional envelope/event fields are rejected. Allowed event "
        "fields (in addition to `kind`) are:",
        "",
        "```json",
        json.dumps({k: sorted(v) for k, v in ALLOWED.items()}, indent=2),
        "```",
        "",
        "A1 sends exactly `{task_text, final_summary}`. The deterministic "
        "summary has `final_solver_message`, `recorded_artifacts`, "
        "`last_state_observation`, `termination`, and "
        "`sanitized_source_sha256`. Latest explicit write attempts are "
        "identified as solver-authored attempts, not verified file contents. "
        "No filesystem read or second model supplies artifact contents. "
        "A2 sends exactly `{task_text, sanitized_trajectory}`. Neither "
        "request includes tools, result files, redaction logs, hidden data, "
        "oracle labels, partition identities, or other candidates' scores.",
        "",
        "A1 responses require finite numeric score in [0,1] and a nonempty "
        "rationale. A2 requires all five ordered binary items and their "
        "rationales/applicability flags, then averages their scores with "
        "denominator five. Booleans are not accepted as numeric scores. "
        "A4 is exactly `0.5 * a1 + 0.5 * a2` from the archived component "
        "scores, with zero additional model calls; a missing component "
        "fails the mixture. Rationale text and raw responses remain archived.",
        "",
        "### Sanitization and trust boundaries",
        "",
        "The sanitizer removes hidden `test_*.py`, `tests/`, `eval.py`, "
        "`evaluator.py`, `eval/`, reference `output/`, `notes/`, "
        "`judge_api.py`, `judge_train_eval/`, rubric fields, oracle/verifier "
        "fields, and reference outputs. A forbidden access also removes "
        "its paired observation by step, even if the observation contains "
        "no identifying path. Common URL, backslash, Unicode/hex, and "
        "base64 encodings are inspected. Redaction logs contain locations, "
        "reasons, and hashes, never removed content. Raw files are never "
        "modified. Legitimate public execution evidence is retained.",
        "",
        "This is defense in depth: arbitrary aliases, copied hidden content "
        "without markers, and adversarial encodings still require the "
        "separate filesystem/service isolation gate. Python frozen types "
        "are an API boundary, not a security sandbox. The controller owns "
        "`CandidateEvaluation(SearchEvaluation, AnchorEvaluation)`; "
        "`EvolverContext` accepts only a `SearchEvaluation`, rejects extra "
        "anchor fields, and serializes search scores only. `accept` returns "
        "one bit for search gain >= tau and anchor regression <= epsilon. "
        "Failed judge scores reject. F-in/F-cross require the anchor; "
        "F-agree accepts only when two different families meet their own "
        "gain thresholds, and does not use an anchor. These are prospective "
        "repair rules, not permission to use anchors for pilot selection.",
        "",
        "## Operation and verification",
        "",
        "Generic asynchronous queue:",
        "",
        "```bash",
        "uv run python -m evolution.judge_queue --job <completed-harbor-job> "
        "--judges a1,a2 --repeats 5",
        "```",
        "",
        "Reproduce this report from the durable queues without API calls:",
        "",
        "```bash",
        "W8_CALIBRATION_MODE=report W8_WRITE_DOCS=1 \\",
        f"W8_CALIBRATION_OUTPUT={artifact_dir} \\",
        "XJUDGE_MAX_COMPLETION_TOKENS=8192 XJUDGE_QUEUE_SUFFIX=cap8192 \\",
        "uv run pytest -q -s "
        "tests/test_evolution_calibration.py::test_authorized_calibration",
        "```",
        "",
        "Configure `JUDGE_API_BASE`, `JUDGE_API_KEY`, `JUDGE_MODEL` and "
        "the corresponding `XJUDGE_*` variables. Optional `{PREFIX}_RPM`, "
        "`{PREFIX}_TPM`, `{PREFIX}_MAX_COMPLETION_TOKENS`, "
        "`{PREFIX}_TIMEOUT_S`, and `{PREFIX}_QUEUE_SUFFIX` override defaults. "
        "Primary limits default to 250 RPM / 250k TPM; Kimi defaults to "
        "100 RPM / 100k TPM. `JUDGING_PRICES_PATH` selects the planning "
        "price table. Default completion allowances are 2,048 tokens for "
        "DeepSeek and 8,192 for Kimi. Queue settings cannot change on resume. "
        "An explicit "
        "suffix archives a separate compatibility configuration. Exact "
        "model settings are retained in every request and ledger record.",
        "",
        "SQLite WAL and exclusive runner locking preserve queue state. "
        "Each attempt is durably counted before dispatch. Backend retries "
        "are disabled; the queue retries once, then durably fails the "
        "measurement. Resume recovers completed responses from ledger/raw "
        "archives before dispatching pending items. A crash with an "
        "in-flight request and no response is ambiguous: its reservation "
        "and attempt stay consumed. The independent shared endpoint bucket "
        "persists across restarts and honors Retry-After.",
        "",
        "Run offline verification with `uv run pytest -q`. Live calibration "
        "is explicitly opt-in through `W8_CALIBRATION_MODE` in the "
        "calibration test; normal test runs make no judge API calls. "
        "Regression tests cover filters, captured payloads, strict schemas, "
        "mixtures, retry failures, both crash windows, resumption, "
        "concurrency, admission control, statistics, and anchor separation.",
        "",
        "## Remaining limitations",
        "",
        "- Invoice prices, cache counts, and dated served model snapshots "
        "are unavailable; requests and exact returned model names are "
        "archived. No claim of deterministic or cache-isolated sampling "
        "is made. The separate P1.6 cache/isolation gate remains necessary.",
        "- Full observations can exceed API capacity. Such measurements "
        "fail explicitly; v1 variance does not calibrate the v2 condition.",
        "- Missing verifier results and the small number of valid positives "
        "limit classification precision. ROC thresholds are descriptive only.",
        "- Kimi has one repeat; its variance and tau are undefined. This "
        "seed cross-judge baseline was explicitly requested for W8, whereas "
        "the pilot preregistration reserves cross-judging for final traces. "
        "Some Kimi responses exhaust the 8,192-token allowance without a "
        "final answer; their A9 failures and missing-score denominators "
        "are reported above. No extra retries or post-hoc score imputation "
        "were used to complete that baseline.",
        "- The root wheel configuration packages `harness` and `scripts` "
        "only. `evolution` works from the project checkout; adding it to "
        "wheel packaging requires a future authorized pyproject change.",
        "",
    ]
    verification = Path(output) / "pytest.txt"
    if verification.exists():
        summary = verification.read_text().strip().splitlines()[-1]
        lines += [
            "## Recorded verification",
            "",
            f"Latest project-wide offline test result: **{summary}** "
            f"([raw output](../{artifact_dir}/pytest.txt)).",
            "",
        ]
    audit = Path(output) / "export-audit.json"
    if audit.exists():
        data = json.loads(audit.read_text())
        lines += [
            f"The [export audit](../{artifact_dir}/export-audit.json) "
            f"verified {data['raw_trace_hashes_verified']} unchanged raw "
            f"trace hashes and {data['completed_payloads_verified']} "
            "completed API payloads against the exact queued evidence; "
            "no tools or oracle fields were added to the request schema.",
            "",
        ]
    return "\n".join(lines)
