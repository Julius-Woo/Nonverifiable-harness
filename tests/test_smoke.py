import json
import subprocess
import sys

import pytest

from harness.ledger import append_jsonl
from scripts import smoke


@pytest.mark.parametrize("missing", [False, True])
def test_docker_denial_stops_before_harbor(tmp_path, monkeypatch, missing):
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["smoke"])
    calls = []
    error = (
        "permission denied while trying to connect to the docker API "
        "at unix:///var/run/docker.sock\n"
    )

    def denied(command, **kwargs):
        calls.append(command)
        if missing:
            raise FileNotFoundError("docker is missing")
        return subprocess.CompletedProcess(command, 1, "", error)

    monkeypatch.setattr(smoke.subprocess, "run", denied)
    assert smoke.main() == 1
    assert calls == [["docker", "ps"]]
    assert ("docker is missing" if missing else error) in (
        tmp_path / "docs/smoke_run.md"
    ).read_text()
    assert "No model calls" in (tmp_path / "costs/summary.md").read_text()


def test_dry_run_does_not_invoke_any_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["smoke", "--dry-run"])

    def unexpected(*args, **kwargs):
        pytest.fail("dry-run must not access Docker or models")

    monkeypatch.setattr(smoke.subprocess, "run", unexpected)
    assert smoke.main() == 0
    assert list(tmp_path.iterdir()) == []


def test_collects_zero_reward_as_valid_oracle_result(tmp_path):
    trial = tmp_path / "extract-elf__abc"
    trial.mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "verifier_result": {"rewards": {"reward": 0}},
                "exception_info": None,
            }
        )
    )
    results = smoke.oracle_results(tmp_path)
    assert results[0]["rewards"] == {"reward": 0}


def test_smoke_command_uses_local_docker_and_bounded_trials(tmp_path):
    command = smoke.harbor_command(tmp_path, "run")
    assert command[command.index("--env") + 1] == "docker"
    assert command[command.index("--n-concurrent") + 1] == "1"
    assert command[command.index("--max-retries") + 1] == "0"
    assert command[command.index("--model") + 1] == "gpt-5-mini"


@pytest.mark.parametrize("harbor_fails", [False, True])
def test_full_launcher_with_simulated_harbor(
    tmp_path,
    monkeypatch,
    harbor_fails,
):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["smoke"])

    def run(command, *args):
        if harbor_fails:
            return 2
        run_id = command[command.index("--job-name") + 1]
        trial = tmp_path / "logs" / "harbor" / run_id / "extract-elf__abc"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "verifier_result": {"rewards": {"reward": 0}},
                    "exception_info": None,
                }
            )
        )
        append_jsonl(
            tmp_path / "costs/ledger.jsonl",
            {
                "run_id": run_id,
                "ok": True,
                "cost_usd": 0.01,
            },
        )
        return 0

    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(smoke, "run_harbor", run)
    assert smoke.main() == (1 if harbor_fails else 0)
    summary_path = next((tmp_path / "logs").glob("*/summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["status"] == (
        "incomplete" if harbor_fails else "oracle_recorded"
    )


def test_harbor_timeout_reaps_process(tmp_path):
    import os

    pid_file = tmp_path / "pid"
    code = (
        f"import os, time; open({str(pid_file)!r}, 'w')"
        ".write(str(os.getpid())); time.sleep(60)"
    )
    with (tmp_path / "out").open("w") as out:
        with pytest.raises(subprocess.TimeoutExpired):
            smoke.run_harbor(
                [sys.executable, "-c", code], tmp_path, out, out, 0.2
            )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
