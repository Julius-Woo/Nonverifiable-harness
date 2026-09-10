"""Append medium follow-up evidence using the original calibration reducer."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from harbor.models.job.config import JobConfig

from harness.ledger import price_usage
from harness.seed import API_SYSTEM
from scripts.calibrate import CONFIGS, MEDIUM_CONFIGS, ROOT, job_config
from scripts.calibration_report import collect, paired_interval, render
from scripts.cost_report import read_ledger, render_report

ARCHIVE = ROOT / "logs/calibration-medium-followup-260910"


def eligible(row):
    return (
        row["n_results"] == row["n_agent_started"] == 60
        and 0.15 <= row["pass_rate"] <= 0.45
        and row["no_action_rate"] < 0.05
        and row["response_calls"] > 0
    )


def audit(results, ledger):
    manifest = json.loads(
        (ROOT / "logs/calibration_source_manifest.json").read_text()
    )
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    run_ids = {row["run_id"] for row in results}
    calls = [row for row in ledger if row["run_id"] in run_ids]
    ids = {row["call_id"] for row in calls}
    assert len(ids) == len(calls)
    prices = json.loads((ROOT / "scripts/prices.json").read_text())
    request_ids = set()
    memory_stats = []
    for row in results:
        label = row["configuration"]
        assert row["n_results"] == 60
        assert set(Counter(t["task"] for t in row["trials"]).values()) == {2}
        assert row["summary"]["return_code"] == 0
        expected = job_config(label, 3)
        job = ROOT / "logs/harbor" / row["run_id"]
        config = JobConfig.model_validate_json(
            (job / "config.json").read_text()
        )
        assert config.n_concurrent_trials == 3
        assert config.n_attempts == 2
        assert config.retry.max_retries == 0
        assert config.tasks == expected.tasks
        assert config.agents == expected.agents
        for request in job.glob("*/agent/calls/*/request.json"):
            request_ids.add(request.parent.name)
            payload = json.loads(request.read_text())
            assert payload["model"] == MEDIUM_CONFIGS[label][0]
            assert payload["reasoning_effort"] == "medium"
            assert payload["max_completion_tokens"] == 4096
            assert payload["messages"][0]["content"].startswith(
                API_SYSTEM + "\nConversation:\n"
            )
            assert not {"tools", "temperature", "seed"} & payload.keys()
        run_dir = ROOT / "logs" / row["run_id"]
        memory = read_ledger(run_dir / "memory.jsonl")
        samples = [r for r in memory if "available_gb" in r]
        docker = [r for r in memory if r.get("kind") == "docker_admission"]
        docker += read_ledger(run_dir / "docker_admission.jsonl")
        assert samples[0]["available_gb"] >= 10
        assert docker and max(r["alexgshaw_containers"] for r in docker) <= 6
        assert all(
            r["foreign_containers"] + r["reserved_trials"] + 1 <= 6
            for r in docker
            if r["admitted"]
        )
        periodic_containers = max(
            sum(
                "__env-main-1" in line
                for line in sample.get("docker_stats", "").splitlines()
            )
            for sample in samples
        )
        assert periodic_containers <= 6
        memory_stats.append(
            {
                "run_id": row["run_id"],
                "min_available_gib": min(r["available_gb"] for r in samples),
                "samples_below_6_gib": sum(
                    r["available_gb"] < 6 for r in samples
                ),
                "max_sampled_alexgshaw": max(
                    r["alexgshaw_containers"] for r in docker
                ),
                "max_periodic_task_containers": periodic_containers,
                "docker_admission_pauses": sum(
                    not r["admitted"] for r in docker
                ),
            }
        )
    assert request_ids == ids
    for row in calls:
        response = Path(row["raw_dir"]) / "response.json"
        if response.exists():
            usage = json.loads(response.read_text())["usage"]
            assert usage["prompt_tokens"] == row["input_tokens"]
            assert usage["completion_tokens"] == row["output_tokens"]
            if row["cost_usd"] is not None:
                assert (
                    abs(
                        price_usage(row["pricing_model"], row, prices)
                        - row["cost_usd"]
                    )
                    < 1e-10
                )
    budget = json.loads(
        (ROOT / "costs/calibration_medium_budget.json").read_text()
    )
    used = sum(t["budget_used_usd"] for r in results for t in r["trials"])
    assert all(
        t["budget_used_usd"] <= 1 + 1e-8 for r in results for t in r["trials"]
    )
    assert abs(used - budget["used_usd"]) < 1e-8
    assert used <= 8
    known = sum(r.get("cost_usd") or 0 for r in calls)
    report = {
        "finalized_attempts": sum(r["n_results"] for r in results),
        "verifier_rewards": sum(r["n_verifier"] for r in results),
        "ledger_calls": len(calls),
        "audited_requests": len(request_ids),
        "known_usd": known,
        "guard_used_usd": used,
        "retained_reservations_usd": max(0.0, used - known),
        "unknown_cost_calls": sum(r["cost_usd"] is None for r in calls),
        "source_hashes_match": True,
        "same_prompt_and_split": True,
        "two_finalized_attempts_per_task": True,
        "memory": memory_stats,
    }
    (ARCHIVE / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def extend(original, results, split, evidence):
    new = [r for r in results if r["configuration"] in MEDIUM_CONFIGS]
    generated = render(new, split)
    doc = original
    # Insert exact rows produced by the established report renderer.
    for header in (
        "| Configuration | Results / verifier |",
        "| Configuration | Mean steps |",
        "| Configuration | Pass-rate gate |",
    ):
        table = generated[generated.index(header) :].split("\n\n", 1)[0]
        rows = "\n".join(table.splitlines()[2:])
        end = doc.index("\n\n", doc.index(header))
        doc = doc[:end] + "\n" + rows + doc[end:]
    doc = doc.replace(
        "Each configuration uses all 30 tasks",
        "Each original configuration uses all 30 tasks",
        1,
    )
    doc = doc.replace(
        "The recovery and all remaining batches use concurrency 4",
        "The recovery and all remaining original batches use concurrency 4",
        1,
    ).replace(
        "caps this experiment at $40",
        "caps the original experiment at $40",
        1,
    )
    note = (
        "The two `-medium` follow-up rows use the identical 30-task split, "
        "seed, JSON prompt, 24-call cap, 4,096 completion tokens, command/API "
        "timeouts, and zero retries; only `reasoning_effort=medium` changes "
        "model behavior. Both follow-up batches run sequentially at "
        "`--n-concurrent 3` while W9 shares Docker, with at most six combined "
        "`alexgshaw` containers. They share a separate **$8** guard in "
        "`costs/calibration_medium_budget.json` and retain the **$1** "
        "per-rollout guard. Reproduce with `sg docker -c 'uv run python -m "
        "scripts.calibrate mini-json-medium --concurrency 3'` (substitute "
        "`luna-json-medium`); regenerate this extension with `uv run python "
        "-m scripts.calibration_medium_report`. The original six rows and "
        "their accounting below are retained.\n\n"
    )
    doc = doc.replace(
        "## Metrics and results\n", note + "## Metrics and results\n", 1
    )
    choices = [r for r in results if eligible(r)]
    recommendation = [
        "",
        "**Eligibility re-applied after both medium-reasoning batches.** "
        "Every eligible configuration is listed below so the user can "
        "choose. Costs use measured known USD per completed rollout; "
        "the 2,800-rollout projection covers task-model API calls only "
        "and excludes judges, evolvers, and cross-judges.",
        "",
        "| Eligible configuration | Reasoning | Pass avg@2 | No-action | "
        "USD / rollout | Projected USD / 2,800 rollouts |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in choices:
        reasoning = (
            "medium" if row["configuration"] in MEDIUM_CONFIGS else "low"
        )
        recommendation.append(
            f"| {row['configuration']} | {reasoning} | "
            f"{row['pass_rate']:.1%} | {row['no_action_rate']:.1%} | "
            f"{row['mean_usd']:.6f} | {row['mean_usd'] * 2800:.2f} |"
        )
    if choices:
        cheapest = min(choices, key=lambda r: r["mean_usd"])
        recommendation += [
            "",
            "The lowest-cost eligible option is "
            f"**{cheapest['configuration']}** "
            f"at **${cheapest['mean_usd']:.6f}/rollout** "
            f"(**${cheapest['mean_usd'] * 2800:.2f}** for 2,800). "
            "This is a cost-based recommendation within the predefined "
            "gates, not a claim of a statistically established capability "
            "advantage. No pilot configuration is changed by this report.",
        ]
        if cheapest["mean_usd"] * 2800 > 300:
            recommendation += [
                "",
                "Even the lowest-cost eligible option's task-only "
                "projection exceeds the **$300 whole-pilot guard** in "
                "`docs/decisions-260910.md` Section 3, before other model "
                "roles. The current eligibility gates and 2,800-rollout "
                "pilot therefore have no measured eligible option that "
                "fits that guard. This report does not change the guard "
                "or authorize a pilot run.",
            ]
    recommendation += [
        "",
        "### Medium versus low reasoning",
        "",
        "| Configuration | Pass change (pp; paired 95% CI) | "
        "No-action, low → medium | USD / rollout, low → medium | "
        "Cost ratio | Agent s, low → medium | Agent-time ratio |",
        "| --- | --- | --- | --- | ---: | --- | ---: |",
    ]
    by_label = {r["configuration"]: r for r in results}
    for row in new:
        low = by_label[row["configuration"].removesuffix("-medium")]
        delta, lower, upper = paired_interval(row, low)
        recommendation.append(
            f"| {row['configuration']} | {delta * 100:+.1f} "
            f"({lower * 100:+.1f} to {upper * 100:+.1f}) | "
            f"{low['no_action_count']}/60 → {row['no_action_count']}/60 | "
            f"{low['mean_usd']:.6f} → {row['mean_usd']:.6f} | "
            f"{row['mean_usd'] / low['mean_usd']:.2f}× | "
            f"{low['mean_agent_s']:.2f} → {row['mean_agent_s']:.2f} | "
            f"{row['mean_agent_s'] / low['mean_agent_s']:.2f}× |"
        )
    mini, luna = (by_label[label] for label in MEDIUM_CONFIGS)
    empty_answer = "Backend failed: API returned no answer"
    recommendation += [
        "",
        f"Mini's escalation rule is now measured: medium yields "
        f"{mini['successes']}/60 passes ({mini['pass_rate']:.1%}) and "
        f"{mini['no_action_count']}/60 no-action finishes; "
        f"it is {'eligible' if eligible(mini) else 'ineligible'}.",
        "",
        f"Luna at medium records {luna['no_action_count']}/60 "
        f"({luna['no_action_rate']:.1%}) no-action finishes versus "
        "7/60 (11.7%) at low. "
        + (
            "The no-action gate now passes; this is consistent with a "
            "reasoning-effort effect on this sample, without establishing "
            "that low reasoning is the sole cause."
            if luna["no_action_rate"] < 0.05
            else "The no-action gate still fails; medium reasoning does not "
            "resolve the seed concern on this sample."
        ),
        "",
        "No-action measures explicit finish records with zero executed "
        "tools. Empty API answers remain separate operational failures, "
        "even when no tool ran. Mini/medium recorded "
        f"{mini['failure_reasons'].get(empty_answer, 0)} "
        "empty-answer failures; Luna/medium recorded "
        f"{luna['failure_reasons'].get(empty_answer, 0)}. "
        "A lower no-action finish rate therefore does not by itself "
        "establish that every form of failing before tool use was resolved.",
        "",
        "Agent latency is reported separately from batch wall time. "
        "The medium jobs use concurrency 3 and share Docker with W9; "
        "the original Mini/JSON and Luna/JSON rows used 8 and 4. "
        "Batch-time and latency differences therefore include host effects. "
        "All solver failures remain in the fixed denominator; the measured "
        "30-task confidence intervals remain wide.",
        "",
        "### Follow-up accounting and verification",
        "",
        f"Both jobs completed **{evidence['finalized_attempts']} attempt "
        f"slots** with **{evidence['verifier_rewards']} verifier rewards**. "
        f"All **{evidence['audited_requests']} request artifacts** reconcile "
        f"to the ledger. Known follow-up cost is "
        f"**${evidence['known_usd']:.6f}**; guard used/reserved is "
        f"**${evidence['guard_used_usd']:.6f} / $8**, including "
        f"**${evidence['retained_reservations_usd']:.6f}** retained "
        f"reservations and {evidence['unknown_cost_calls']} unknown-cost "
        "calls. All rollout guards stayed at or below $1. "
        "[Follow-up audit](../logs/calibration-medium-followup-260910/"
        "audit.json).",
        "",
        "| Job | Min MemAvailable GiB | Samples below 6 GiB | "
        "Max alexgshaw at admission | Max periodic task containers | "
        "Docker admission pauses |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in evidence["memory"]:
        recommendation.append(
            f"| [{row['run_id']}](../logs/{row['run_id']}/memory.jsonl) | "
            f"{row['min_available_gib']:.2f} | "
            f"{row['samples_below_6_gib']} | "
            f"{row['max_sampled_alexgshaw']} | "
            f"{row['max_periodic_task_containers']} | "
            f"{row['docker_admission_pauses']} |"
        )
    recommendation += [
        "",
        "MemAvailable admission starts at 10 GiB, pauses new trials below "
        "6 GiB, and resumes at 10 GiB; running trials finish. Memory and "
        "Docker statistics are sampled every two minutes, with admission "
        "checks between trials. Source hashes verify unchanged harness, "
        "prompts, split, and prices. The original report and recommendation "
        "are archived [here](../logs/calibration-medium-followup-260910/"
        "calibration-before.md). Focused test and lint results are recorded "
        "in [verification](../logs/calibration-medium-followup-260910/"
        "verification.txt). No commit, push, or model CLI call was made.",
        "",
        "Admission counts come from `docker ps` image names; periodic "
        "counts cover task service names ending in `__env-main-1` in "
        "Docker stats. Mini admission events are in its memory log; "
        "Luna admission events are in [docker_admission.jsonl]"
        "(../logs/calibration-luna-json-medium-260910/"
        "docker_admission.jsonl). The completed follow-up left no "
        "containers of its own running.",
        "",
    ]
    start = doc.index("<!-- RECOMMENDATION -->") + len(
        "<!-- RECOMMENDATION -->"
    )
    end = doc.index("<!-- END RECOMMENDATION -->")
    doc = doc[:start] + "\n".join(recommendation) + doc[end:]
    doc = doc.replace(
        "experiment totals here filter phase P1.2.",
        "these historical totals cover the original six configurations "
        "and diagnostic; the medium follow-up is accounted separately above.",
    )
    doc += (
        "\n"
        + generated.split(
            "## Per-task evidence and operational failures\n\n", 1
        )[1]
    )
    # Historical table rows and evidence must remain byte-for-byte present.
    for line in original.splitlines():
        if line.startswith("|"):
            assert line in doc, line
    return doc


def main():
    ledger = read_ledger(ROOT / "costs/ledger.jsonl")
    split = json.loads((ROOT / "data/tb2_split.json").read_text())
    results = [
        collect(label, ledger, split) for label in CONFIGS | MEDIUM_CONFIGS
    ]
    assert all(results), "Both medium jobs must exist"
    new = [r for r in results if r["configuration"] in MEDIUM_CONFIGS]
    evidence = audit(new, ledger)
    original = (ARCHIVE / "calibration-before.md").read_text()
    doc = extend(original, results, split, evidence)
    (ROOT / "docs/calibration.md").write_text(doc)
    (ROOT / "costs/calibration_results.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    (ROOT / "costs/summary.md").write_text(render_report(ledger))
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
