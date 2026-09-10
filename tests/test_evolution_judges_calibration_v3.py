"""Frozen source selection and pre-enqueue v3 capacity checks."""

import hashlib
import json
from pathlib import Path

import pytest

from evolution.calibration import enqueue_manifest, prepare
from evolution.judge_queue import ROOT
from evolution.judges import PROMPTS, JudgeInput
from evolution.sanitize import canonical, digest, sanitize


def source_manifest(tmp_path):
    job = tmp_path / "job"
    trial = job / "selected"
    (trial / "agent").mkdir(parents=True)
    trace = trial / "agent/trace.jsonl"
    trace.write_text(json.dumps({"kind": "instruction", "text": "Task"}))
    result = trial / "result.json"
    result.write_text(
        json.dumps({"verifier_result": {"rewards": {"reward": 1}}})
    )
    entry = {
        "task": "task",
        "trial": "selected",
        "attempt": 2,
        "partition": "search",
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
    }
    manifest = tmp_path / "old-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "job": str(job.resolve()),
                "entries": [entry],
                "selection": "frozen selection",
                "split_sha256": "frozen split",
                "prompt_hashes": {j: digest(p) for j, p in PROMPTS.items()},
            }
        )
    )
    return job, manifest


def test_v3_reexpresses_fixed_selection_and_freezes_caps(tmp_path):
    job, source = source_manifest(tmp_path)
    output = tmp_path / "v3"
    manifest = prepare(job, output, {}, source)
    assert manifest["evidence_version"] == "v3"
    assert manifest["entries"][0]["attempt"] == 2
    assert manifest["entries"][0]["label_readings"]["executor"]["label"] == 1
    assert (
        manifest["reused_manifest_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert prepare(job, output, {}, source) == manifest
    with pytest.raises(ValueError, match="Frozen evidence/settings"):
        prepare(job, output, {"JUDGING_OBSERVATION_CHARS": 20}, source)
    evidence = Path(manifest["entries"][0]["evidence_path"])
    evidence.write_text(evidence.read_text() + " ")
    with pytest.raises(ValueError, match="Frozen evidence file"):
        prepare(job, output, {}, source)


def test_v3_reexpression_rejects_changed_raw_source(tmp_path):
    job, source = source_manifest(tmp_path)
    (job / "selected/agent/trace.jsonl").write_text("{}")
    with pytest.raises(ValueError, match="raw trace/result hash"):
        prepare(job, tmp_path / "v3", {}, source)


def test_v3_calibration_preflights_whole_set_before_any_enqueue(tmp_path):
    output = tmp_path / "v3"
    output.mkdir()
    paths = []
    for index, task in enumerate(("Task", "x" * 200000)):
        path = tmp_path / f"evidence-{index}.json"
        path.write_text(
            canonical(JudgeInput(task, sanitize([]).trajectory).to_dict())
        )
        paths.append(path)
    manifest = {
        "repeats": {"JUDGE": 5},
        "entries": [
            {"trial": str(i), "evidence_path": str(path)}
            for i, path in enumerate(paths)
        ],
    }
    config = {
        "JUDGE_MODEL": "DeepSeek-V4-Flash",
        "JUDGE_API_BASE": "https://example.test",
        "JUDGE_API_KEY": "test-only",
        "JUDGING_PRICES_PATH": str(ROOT / "costs/judges_prices.json"),
        "JUDGING_BUDGET_PATH": str(tmp_path / "budget.json"),
    }
    # Real queue creation, local configuration only. No backend dispatch.
    with pytest.raises(ValueError, match="backend limit after caps"):
        enqueue_manifest(manifest, output, config)
    import sqlite3

    with sqlite3.connect(output / "judge.sqlite") as db:
        assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    assert not (tmp_path / "budget.json").exists()


def test_v3_analysis_retains_missing_usage_reservation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from evolution import calibration

    archive = tmp_path / "unknown-call"
    archive.mkdir()
    (archive / "request.json").write_text(
        json.dumps(
            {
                "messages": [{"role": "user", "content": "x" * 10000}],
                "max_completion_tokens": 2048,
            }
        )
    )
    ledger = tmp_path / "ledger.jsonl"
    records = [
        {
            "run_id": "v3",
            "arm": arm,
            "model": "DeepSeek-V4-Flash",
            "requested_model": "DeepSeek-V4-Flash",
            "raw_dir": str(archive),
            "input_tokens": None if arm == "A1" else 1000,
            "output_tokens": None if arm == "A1" else 200,
        }
        for arm in ("A1", "A2")
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in records))
    queue = SimpleNamespace(
        ledger=ledger,
        run_id="v3",
        rows=lambda: [
            {
                "rollout": "trace",
                "judge": judge,
                "repeat": 0,
                "status": "failed",
                "result": None,
            }
            for judge in ("a1", "a2")
        ],
    )
    manifest = {
        "repeats": {"JUDGE": 1},
        "entries": [
            {
                "trial": "trace",
                "task": "task",
                "partition": "search",
                "oracle_label": 0,
                "raw_reward": 0,
            }
        ],
    }
    monkeypatch.setattr(calibration, "bootstrap_rates", lambda *args: {})
    metrics = calibration.analyze(manifest, [queue], tmp_path)
    unknown = metrics["JUDGE/a1"]
    assert unknown["attempt_uncached_planning_total_usd"] is None
    assert unknown["attempts_without_usage"] == 1
    assert unknown["attempt_usage_estimate_usd"] == 0
    assert unknown["attempt_unresolved_reservation_usd"] == pytest.approx(
        0.0075856
    )
    known = metrics["JUDGE/a2"]
    assert known["attempt_usage_estimate_usd"] == pytest.approx(0.00074)
    assert known["attempts_without_usage"] == 0
    assert known["attempt_unresolved_reservation_usd"] == 0
