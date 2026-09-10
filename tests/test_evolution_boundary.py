"""Real container boundary acceptance, explicitly enabled without API calls."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from evolution.candidates import Manifest, copy_seed, source_hash
from evolution.harbor_agent import CandidateAgent
from evolution.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


async def test_harbor_empty_output_boundary_probe(tmp_path):
    class EmptyEnvironment:
        async def exec(self, command, timeout_sec):
            return SimpleNamespace(stdout=None, stderr=None, return_code=0)

    agent = object.__new__(CandidateAgent)
    agent.logs_dir = tmp_path
    await agent.setup(EmptyEnvironment())
    assert json.loads((tmp_path / "task_boundary.json").read_text())[
        "hidden_paths_absent"
    ]


@pytest.mark.skipif(
    os.getenv("EVOLUTION_DOCKER_TEST") != "1",
    reason="Explicit real Docker boundary check",
)
async def test_actual_file_shell_cross_arm_and_read_only_feedback(tmp_path):
    work = copy_seed(ROOT / "harness", tmp_path / "candidate")
    Manifest("seed", None, "A0", 0, "seed", "seed", source_hash(work)).write(
        work
    )
    feedback = tmp_path / "feedback"
    feedback.mkdir()
    (feedback / "authorized.txt").write_text("Authorized score: 0.5")
    paths = []
    for name in ("oracle", "anchor", "sealed", "other_arm"):
        path = tmp_path / name / "secret.txt"
        path.parent.mkdir()
        path.write_text(f"SECRET_VALUE_{name}_17f8b221")
        paths.append(path)
    values = [p.read_text() for p in paths]
    with Workspace(
        work,
        "nvhe-boundary-test",
        feedback=feedback,
        writable=True,
        audit=tmp_path / "boundary.json",
    ) as box:
        assert await box.canary_check(
            paths, values, tmp_path / "canaries.jsonl"
        )
        assert (await box.import_check())["ok"]
        assert (await box.exec("cat /feedback/authorized.txt")).stdout
        assert (
            await box.exec("echo overwrite > /feedback/authorized.txt")
        ).return_code != 0
        assert (await box.exec("cat /var/run/docker.sock")).return_code != 0
        assert (await box.exec("cat /candidate/.env")).return_code != 0
        assert (await box.exec("ls /sys/class/net")).stdout.strip() == "lo"
    boundary = json.loads((tmp_path / "boundary.json").read_text())
    assert boundary["network"] == "none"
    assert boundary["cap_drop"] == ["ALL"]
    assert boundary["read_only_root"]
    assert [p.read_text() for p in paths] == values


async def test_shared_admission_caps_foreign_calibration_at_two(
    tmp_path, monkeypatch
):
    import asyncio

    from evolution import admission

    monkeypatch.setattr(admission, "available_gib", lambda: 12)
    monkeypatch.setattr(
        admission,
        "docker",
        lambda *args: SimpleNamespace(stdout="foreign alexgshaw/example\n"),
    )
    first = admission.HarborAdmission(tmp_path, "nvhe-first")
    second = admission.HarborAdmission(tmp_path, "nvhe-second")
    third = admission.HarborAdmission(tmp_path, "nvhe-third")
    await first.__aenter__()
    await second.__aenter__()
    pending = asyncio.create_task(third.__aenter__())
    await asyncio.sleep(0.05)
    assert not pending.done()
    await first.__aexit__()
    await asyncio.wait_for(pending, 3)
    records = [
        json.loads(s)
        for s in (tmp_path / "logs/evolution-admission/admissions.jsonl")
        .read_text()
        .splitlines()
    ]
    assert all(r["global_limit"] == 2 for r in records)
    await second.__aexit__()
    await third.__aexit__()
