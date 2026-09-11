import json

import pytest

from gdpevo import GROUPS, SOURCE, task_path
from gdpevo.oracle import (
    GRADER,
    binary_rule,
    run_grader,
    strict_json,
    verify_frozen,
)
from scripts.gdpevo.controls import wrong_control


@pytest.mark.parametrize(
    "score,expected",
    [
        ({"score": 1}, 1),
        ({"normalized_score": 1}, 1),
        ({"normalized_score": 1, "score": 17}, 1),
        ({"normalized_score": 0.2, "score": 1}, 0),
        ({"score": 0.9999995}, 1),
        ({"score": 0.999998}, 0),
        ({"score": True}, 0),
        ({"score": "1"}, 0),
        ({"score": float("nan")}, 0),
        ({"score": float("inf")}, 0),
        ({}, 0),
    ],
)
def test_binary_score_contract(score, expected):
    assert binary_rule({}, {"returncode": 0, "result": score}) == expected


@pytest.mark.parametrize("status", ["timeout", "seed_failure", "tool_failure"])
def test_timeout_and_tool_failure_are_failures(status):
    assert (
        binary_rule({}, {"returncode": 0, "result": {"score": 1}}, status) == 0
    )


def test_invalid_submission_and_grader():
    valid = {"returncode": 0, "result": {"score": 1}}
    for submission in (None, [], 1, "{}"):
        assert binary_rule(submission, valid) == 0
    assert binary_rule({}, {**valid, "returncode": 1}) == 0
    assert binary_rule({}, {**valid, "error": "timeout"}) == 0
    assert (
        binary_rule(
            {},
            {
                **valid,
                "result": {"score": 1, "error": "bad"},
            },
        )
        == 0
    )
    for raw in ('{"a":NaN}', '{"a":1e999}', '{"a":1,"a":2}', "{"):
        with pytest.raises(ValueError):
            strict_json(raw)


def test_frozen_tree_and_exact_patch_scope():
    manifest = verify_frozen()
    changed = []
    for path in GRADER.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(GRADER)
        if (
            path.read_bytes()
            != (SOURCE / "data/task_groups" / rel).read_bytes()
        ):
            changed.append(rel.as_posix())
    assert sorted(changed) == sorted(manifest["patched_files"])


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("split", ["train", "test"])
@pytest.mark.parametrize("task_id", [f"{n:03}" for n in range(1, 6)])
def test_all_frozen_reference_controls(group, split, task_id):
    task = task_path(GRADER, group, split, task_id)
    raw = (task / "output/answer.json").read_bytes()
    result = run_grader(task / "eval/eval.sh", raw)
    assert "error" not in result, result
    assert result["score"] == 1
    assert binary_rule(json.loads(raw), result) == 1


@pytest.mark.parametrize("group", [15, 18])
def test_valid_but_wrong_controls(group):
    raw = wrong_control(group)
    for root, hardened in (
        (GRADER, True),
        (SOURCE / "data/task_groups", False),
    ):
        task = task_path(root, group, "train", "001")
        result = run_grader(task / "eval/eval.sh", raw)
        assert "error" not in result
        assert (result["score"] < 1) if hardened else (result["score"] == 1)


def test_grader_timeout(tmp_path):
    script = tmp_path / "eval.sh"
    script.write_text("sleep 5\n")
    result = run_grader(script, b"{}", timeout=0.05)
    assert result["error"] == "timeout"
    assert binary_rule({}, result) == 0


@pytest.mark.parametrize(
    "score,expected",
    [
        (1.00000000001, 0),
        (1.0000005, 0),
        (-0.0000001, 0),
        (0, 0),
        (1, 1),
        (0.9999995, 1),
    ],
)
def test_range_before_tolerance(score, expected, tmp_path):
    grader = {"returncode": 0, "result": {"score": score}}
    assert binary_rule({}, grader) == expected
    script = tmp_path / "eval.sh"
    script.write_text(
        "printf '%s\\n' '" + json.dumps({"score": score}) + "'\n"
    )
    result = run_grader(script, b"{}")
    assert bool(result.get("error")) == (not 0 <= score <= 1)


def test_wrong_control_rejects_native_score_one_with_invalid_exit(
    tmp_path, monkeypatch
):
    from scripts.gdpevo import controls as module

    calls = []

    def fake_grader(*args):
        calls.append(args)
        if len(calls) <= 240:
            return {"score": 1, "returncode": 0}
        if len(calls) % 2:
            return {"score": 0.9, "returncode": 0}
        return {"score": 1, "returncode": 1, "error": "grader_exit"}

    monkeypatch.setattr(module, "run_grader", fake_grader)
    result = module.controls(tmp_path / "controls")
    assert not result["passed"]
    assert result["reference_passes"] == 120
