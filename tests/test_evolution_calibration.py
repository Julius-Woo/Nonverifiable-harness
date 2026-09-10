"""Offline statistics and an explicitly opted-in calibration entry point."""

import os
import statistics
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolution.calibration import (
    DEFAULT_JOB,
    DEFAULT_OUTPUT,
    aggregate_repeat_sd,
    classification,
    main_async,
    memory_guard,
    roc_optimal,
    verifier_label,
)


def test_variance_uses_task_means_not_trajectory_dispersion():
    sd, means = aggregate_repeat_sd(
        {"one": [[0, 1, 0, 1, 0], [1, 1, 1, 1, 1]], "two": [[0, 0, 0, 0, 0]]}
    )
    assert means == [0.25, 0.5, 0.25, 0.5, 0.25]
    assert sd == statistics.stdev(means)


def test_classification_missing_and_roc():
    scores, labels = [0.9, 0.5, 0.4, None, 1], [1, 0, 0, 1, None]
    rates = classification(scores, labels, 0.5)
    assert rates["tpr"] == 1
    assert rates["fpr"] == 0.5
    assert rates["positive_denominator"] == 1
    assert roc_optimal(scores, labels)["threshold"] == 0.9
    assert classification([0], [0], 0.5)["tpr"] is None


def test_verifier_policy_preserves_failures_and_exclusions():
    result = {"verifier_result": {"rewards": {"reward": 1}}}
    assert verifier_label(result, [])[0] == 1
    assert (
        verifier_label(result, [{"kind": "observation", "return_code": 1}])[0]
        == 0
    )
    result["exception_info"] = {"exception_type": "AgentTimeoutError"}
    assert verifier_label(result, [])[0] == 0
    result["exception_info"] = {"exception_type": "VerifierTimeoutError"}
    assert verifier_label(result, [])[0] is None
    assert verifier_label({}, [])[0] is None


@pytest.mark.skipif(
    not os.getenv("W8_CALIBRATION_MODE"),
    reason="Live calibration requires explicit W8_CALIBRATION_MODE",
)
async def test_authorized_calibration():
    """Run in pytest's existing process to respect the W8 memory guard."""
    mode = os.environ["W8_CALIBRATION_MODE"]
    assert mode in {"prepare", "probe", "run", "report"}
    memory = dict(
        line.split(":", 1)
        for line in Path("/proc/meminfo").read_text().splitlines()
    )
    assert int(memory["MemAvailable"].split()[0]) >= 6 * 1024**2
    metrics = await main_async(
        SimpleNamespace(
            job=DEFAULT_JOB,
            output=Path(
                os.getenv("W8_CALIBRATION_OUTPUT", str(DEFAULT_OUTPUT))
            ),
            reuse_manifest=os.getenv("W8_REUSE_MANIFEST"),
            endpoints=os.getenv("W8_CALIBRATION_ENDPOINTS", "JUDGE,XJUDGE"),
            write_docs=bool(os.getenv("W8_WRITE_DOCS")),
            run=mode == "run",
            probe=mode == "probe",
            concurrency=4,
        )
    )
    if mode == "run":
        selected = os.getenv("W8_CALIBRATION_ENDPOINTS", "JUDGE,XJUDGE")
        assert all(
            (
                report["complete"]
                if name.startswith("JUDGE/")
                else report["settled"]
            )
            for name, report in metrics.items()
            if name.split("/")[0] in selected.split(",")
        ), "Primary repeats must be complete and all judgments terminal"


@pytest.mark.skipif(
    not os.getenv("W8_ENDPOINT_DIAGNOSE"),
    reason="Explicit endpoint diagnostic",
)
async def test_endpoint_diagnostic():
    import json

    from dotenv import dotenv_values

    from evolution.judge_queue import ROOT, Endpoint
    from evolution.judges import JudgeInput, build_prompt
    from harness.ledger import CallTags

    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    config["JUDGING_PRICES_PATH"] = str(ROOT / "costs/judges_prices.json")
    manifest = json.loads((DEFAULT_OUTPUT / "manifest.json").read_text())
    evidence = JudgeInput.from_dict(
        json.loads(Path(manifest["entries"][0]["evidence_path"]).read_text())
    )
    prefix = os.environ["W8_ENDPOINT_DIAGNOSE"]
    output = ROOT / "logs/judges/endpoint-diagnostic"
    endpoint = Endpoint(
        prefix, config, output, ROOT / "costs/judges_budget.json"
    )
    backend = endpoint.backend(prefix.lower(), 1)
    result = await backend.complete(
        build_prompt("a1", evidence),
        CallTags(phase="P1.4", run_id="endpoint-diagnostic", role="judge"),
    )
    print(
        json.dumps({"ok": result.record["ok"], "note": result.record["note"]})
    )
    errors = output / "calls" / prefix.lower() / "1/errors.jsonl"
    if errors.exists():
        print(errors.read_text())


def test_memory_guard_uses_available_not_free(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemFree: 1 kB\nMemAvailable: 6291456 kB\n")
    assert memory_guard(path) == 6291456
    path.write_text("MemFree: 1 kB\nMemAvailable: 6291455 kB\n")
    with pytest.raises(RuntimeError, match="MemAvailable"):
        memory_guard(path)


@pytest.mark.parametrize(
    "scores",
    [
        {},
        {"task": []},
        {"task": [[]]},
        {"one": [[0, 1]], "two": [[0]]},
        {"task": [[0, None]]},
    ],
)
def test_variance_rejects_incomplete_repeat_vectors(scores):
    with pytest.raises(ValueError):
        aggregate_repeat_sd(scores)


def test_report_refuses_to_publish_incomplete_variance_control():
    from evolution.report import render_report

    with pytest.raises(ValueError, match="every scheduled score"):
        render_report({}, {"a1": {"complete": False}}, {}, Path.cwd(), {}, [])


def test_report_distinguishes_failed_baseline_from_incomplete_repeats():
    from evolution.report import measurements_ready

    metrics = {
        "JUDGE/a1": {"complete": True, "settled": True},
        "JUDGE/a2": {"complete": True, "settled": True},
        "XJUDGE/a1": {"complete": True, "settled": True},
        "XJUDGE/a2": {"complete": False, "settled": True},
    }
    assert measurements_ready(metrics)
    metrics["JUDGE/a2"]["complete"] = False
    assert not measurements_ready(metrics)
    metrics["JUDGE/a2"]["complete"] = True
    metrics["XJUDGE/a2"]["settled"] = False
    assert not measurements_ready(metrics)


@pytest.mark.skipif(
    not os.getenv("W8_AUDIT_ARCHIVES"),
    reason="Explicit fixed-archive audit",
)
def test_saved_calibration_integrity():
    import hashlib
    import json
    import sqlite3

    from evolution.judge_queue import ROOT
    from evolution.judges import JudgeInput, build_prompt
    from evolution.sanitize import digest

    output = ROOT / "logs/judges/seed-mini-json-v2"
    manifest = json.loads((output / "manifest.json").read_text())
    summaries = []
    for entry in manifest["entries"]:
        evidence = JudgeInput.from_dict(
            json.loads(Path(entry["evidence_path"]).read_text())
        )
        trace = Path(manifest["job"]) / entry["trial"] / "agent/trace.jsonl"
        with trace.open("rb") as handle:
            assert (
                hashlib.file_digest(handle, "sha256").hexdigest()
                == (entry["trace_sha256"])
            )
        summaries.append(
            {
                "trial": entry["trial"],
                "task_excerpt": evidence.task_text[:160],
                "events": len(evidence.trajectory.events),
                "kinds": sorted(
                    {e["kind"] for e in evidence.trajectory.events}
                ),
                "redacted_events": sum(
                    e["kind"] == "redacted" for e in evidence.trajectory.events
                ),
            }
        )
    matched = 0
    for path in output.glob("*.sqlite"):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            for data, judge, prompt_hash, result in db.execute(
                "SELECT evidence,judge,prompt_hash,result FROM items "
                "WHERE status='done'"
            ):
                evidence = JudgeInput.from_dict(json.loads(data))
                expected = build_prompt(judge, evidence)
                assert digest(expected) == prompt_hash
                record = json.loads(result)["record"]
                payload = json.loads(
                    (Path(record["raw_dir"]) / "request.json").read_text()
                )
                assert "tools" not in payload
                assert payload["messages"] == [
                    {"role": "user", "content": expected}
                ]
                assert record["role"] == "judge"
                matched += 1
    (output / "export-audit.json").write_text(
        json.dumps(
            {
                "raw_trace_hashes_verified": len(summaries),
                "completed_payloads_verified": matched,
                "traces": summaries,
            },
            indent=2,
        )
    )
    print(json.dumps({"traces": len(summaries), "payloads": matched}))


@pytest.mark.parametrize("policy", ["strict", "executor"])
def test_missing_reward_executor_timeout_is_failure_under_both_readings(
    policy,
):
    result = {
        "exception_info": {
            "exception_type": "RuntimeError",
            "exception_message": "Command timed out after 30 seconds",
            "exception_traceback": "result = await environment.exec(\n"
            "_collect_buffered_output",
        }
    }
    assert verifier_label(result, [], tool_policy=policy) == (
        0,
        "executor_or_protocol_failure",
    )
    result["exception_info"]["exception_traceback"] = "await verifier.run("
    assert verifier_label(result, [], tool_policy=policy)[0] is None
    assert (
        verifier_label(
            {},
            [{"kind": "observation", "error": "timeout"}],
            tool_policy=policy,
        )[0]
        == 0
    )


def test_pending_ad10_and_ad13_are_explicit_readings():
    result = {"verifier_result": {"rewards": {"reward": 1}}}
    records = [{"kind": "observation", "return_code": 1}]
    assert verifier_label(result, records, tool_policy="strict")[0] == 0
    assert verifier_label(result, records, tool_policy="executor")[0] == 1
    result = {
        "exception_info": {
            "exception_type": "NonZeroAgentExitCodeError",
            "exception_message": "Backend failed: TimeoutError",
        }
    }
    assert verifier_label(result, [], api_timeout_policy="failure")[0] == 0
    assert (
        verifier_label(result, [], api_timeout_policy="infrastructure")[0]
        is None
    )


def test_bootstrap_observed_pairs_reports_undefined_draws_and_missing_bounds():
    from evolution.reanalyze_judges import summarize

    entries = [
        {"task": "positive", "partition": "search", "oracle_label": 1},
        {"task": "negative", "partition": "search", "oracle_label": 0},
    ]
    report = summarize(entries, [[1], [None]], "a1", draws=100)
    ci = report["bootstrap_ci95"]
    assert ci["tpr"] == [1, 1]
    assert ci["fpr"] is None
    assert ci["undefined_draws"]["fpr"] == 100
    assert 0 < ci["undefined_draws"]["tpr"] < 100
    assert report["missing_score_bounds"]["0.5"]["fpr"] == [0, 1]


def test_r6_archived_score_recomputation_without_dispatch(
    tmp_path, monkeypatch
):
    from evolution.reanalyze_judges import recompute
    from harness.openai_api import OpenAIAPIBackend

    if not (DEFAULT_OUTPUT / "metrics.json").exists():
        pytest.skip("Historical score archive not available in this checkout")

    async def forbidden(*args, **kwargs):
        pytest.fail("Offline reanalysis must never call a model")

    monkeypatch.setattr(OpenAIAPIBackend, "complete", forbidden)
    report = recompute(output=tmp_path, draws=100)
    assert report["model_calls"] == 0
    assert report["verified_queue_measurements"] == 576
    assert len(report["label_corrections"]) == 7
    metrics = report["readings"]["strict/failure"]["metrics"]
    for scorer, tp, pos, fp, neg in [
        ("JUDGE/a1", 14, 15, 50, 225),
        ("JUDGE/a2", 10, 15, 67, 225),
        ("XJUDGE/a1", 2, 3, 7, 42),
        ("XJUDGE/a2", 3, 3, 33, 41),
    ]:
        point = metrics[scorer]["classification_0.5"]
        assert (
            point["tp"],
            point["positive_denominator"],
            point["fp"],
            point["negative_denominator"],
        ) == (tp, pos, fp, neg)
    assert {e["trial"] for e in report["affected_instructions"]} == {
        "break-filter-js-from-html__bLbbJTC",
        "break-filter-js-from-html__debSxwY",
    }
    assert metrics["JUDGE/a1"]["tau"] == pytest.approx(0.02184135419534282)
    assert (
        report["readings"]["strict/failure"]
        == (report["readings"]["strict/infrastructure"])
    )


def test_roc_exact_tie_uses_highest_threshold():
    # Both thresholds have Youden J exactly 2/3, even when float subtraction
    # would make the lower threshold appear one ULP better.
    scores = [0.95, 0.95, 0.2] + [0.2] * 14 + [0] * 28
    labels = [1] * 3 + [0] * 42
    assert roc_optimal(scores, labels)["threshold"] == 0.95
