"""Calibration denominators, timeout failures, and pinned launch settings."""

import json
from pathlib import Path

import pytest

from scripts import calibration_report
from scripts.calibrate import CONFIGS, MEDIUM_CONFIGS, job_config
from scripts.make_tb2_split import COMMIT


def test_all_jobs_are_pinned_and_have_no_retries():
    for label in CONFIGS:
        config = job_config(label, 4)
        assert config.n_concurrent_trials == 4
        assert config.n_attempts == 2
        assert len(config.tasks) == 30
        assert {t.git_commit_id for t in config.tasks} == {COMMIT}
        assert config.retry.max_retries == 0
        kwargs = config.agents[0].kwargs
        assert kwargs["api_max_retries"] == 0
        assert kwargs["max_steps"] == 24
        assert kwargs["rollout_budget_usd"] == 1
        assert kwargs["shared_budget_usd"] == 40


def test_concurrency_above_four_is_rejected():
    with pytest.raises(ValueError, match="concurrency 4"):
        job_config("luna-native", 8)


def test_medium_jobs_change_only_reasoning_and_operational_settings():
    for label in MEDIUM_CONFIGS:
        baseline = job_config(label.removesuffix("-medium"), 4)
        medium = job_config(label, 3)
        assert medium.tasks == baseline.tasks
        assert medium.n_attempts == baseline.n_attempts == 2
        assert medium.retry == baseline.retry
        assert medium.environment == baseline.environment
        old = baseline.agents[0].kwargs.copy()
        new = medium.agents[0].kwargs.copy()
        assert new.pop("reasoning_effort") == "medium"
        assert old.pop("reasoning_effort") == "low"
        assert new.pop("shared_budget_usd") == 8
        assert old.pop("shared_budget_usd") == 40
        assert new["shared_budget_path"].endswith(
            "calibration_medium_budget.json"
        )
        for key in ("shared_budget_path", "run_id", "arm", "timing_path"):
            old.pop(key)
            new.pop(key)
        assert old == new
        with pytest.raises(ValueError, match="concurrency 3"):
            job_config(label, 4)


def test_job_elapsed_accepts_mixed_utc_formats():
    assert (
        calibration_report.job_elapsed(
            {
                "started_at": "2026-09-10T10:00:00",
                "updated_at": "2026-09-10T10:01:30Z",
                "finished_at": None,
            }
        )
        == 90
    )


def test_timeout_reward_one_is_failure_and_unstarted_stays_in_denominator(
    tmp_path, monkeypatch
):
    split = json.loads(Path("data/tb2_split.json").read_text())
    name = split["splits"]["search"][0]["name"]
    job = tmp_path / "logs/harbor/calibration-mini-json-260910"
    trial = job / f"{name}__example"
    trial.mkdir(parents=True)
    (job / "config.json").write_text('{"n_concurrent_trials": 8}')
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_id": {"git_commit_id": COMMIT},
                "task_name": name,
                "verifier_result": {"rewards": {"reward": 1}},
                "exception_info": {
                    "exception_type": "AgentTimeoutError",
                    "exception_message": "Agent timed out",
                },
            }
        )
    )
    monkeypatch.setattr(calibration_report, "ROOT", tmp_path)
    result = calibration_report.collect("mini-json", [], split)
    assert result["pass_rate"] == 0
    assert result["n_results"] == 1
    assert result["harbor_timeouts"] == 1
    assert result["no_action_count"] == 1
    assert len(result["task_rates"]) == 30
    assert result["trials"][0]["reward"] == 1


def test_recovery_merges_results_and_ignores_unfinalized_verifier(
    tmp_path, monkeypatch
):
    split = json.loads(Path("data/tb2_split.json").read_text())
    name = split["splits"]["search"][0]["name"]
    run_id = "calibration-mini-native-260910"
    jobs = tmp_path / "logs/harbor"
    for suffix, trial_id, reward in (
        ("", "original", 0),
        ("-recovery2", "new", 1),
    ):
        job = jobs / (run_id + suffix)
        trial = job / f"{name}__{trial_id}"
        trial.mkdir(parents=True)
        (job / "config.json").write_text('{"n_concurrent_trials": 4}')
        (job / "result.json").write_text(
            json.dumps(
                {
                    "started_at": "2026-09-10T10:00:00",
                    "updated_at": "2026-09-10T10:01:30Z",
                    "finished_at": None,
                }
            )
        )
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_id": {"git_commit_id": COMMIT},
                    "task_name": name,
                    "verifier_result": {"rewards": {"reward": reward}},
                    "exception_info": None,
                }
            )
        )
    killed = jobs / run_id / f"{name}__killed" / "verifier"
    killed.mkdir(parents=True)
    (killed / "reward.txt").write_text("1")
    monkeypatch.setattr(calibration_report, "ROOT", tmp_path)
    result = calibration_report.collect("mini-native", [], split)
    assert result["n_results"] == result["n_verifier"] == 2
    assert result["task_rates"][name] == 0.5
    assert result["successes"] == 1
    assert sum(t["resumed"] for t in result["trials"]) == 1
    assert result["summary"]["wall_s"] == 180
    assert result["summary"]["wall_is_lower_bound"]
