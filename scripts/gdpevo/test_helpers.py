"""Regression checks for heterogeneous GDPevo grader contracts."""

import json
import os
import shlex
import time
from pathlib import Path

import pytest
from audit_evaluators import run_grader


@pytest.mark.parametrize(
    ("payload", "exit_code", "score", "has_error"),
    [
        ({"score": 12, "max_score": 12, "normalized_score": 1.0}, 0, 1, False),
        ({"score": 0}, 0, 0, False),
        ({"score": 1}, 1, 1, True),
        ({"score": True}, 0, None, True),
        ({"score": float("nan")}, 0, None, True),
        ([], 0, None, True),
    ],
)
def test_grader_contract(tmp_path, payload, exit_code, score, has_error):
    """Exit success is not task success; point totals need normalization."""
    script = tmp_path / "eval.sh"
    script.write_text(
        "cat <<'GDPEVO_RESULT'\n"
        + json.dumps(payload)
        + f"\nGDPEVO_RESULT\nexit {exit_code}\n"
    )
    result = run_grader(script, Path("unused.json"), dict(os.environ), 2)
    assert result.get("score") == score
    assert ("error" in result) == has_error


def test_timeout_is_not_a_zero_score(tmp_path):
    script = tmp_path / "eval.sh"
    script.write_text("exec sleep 2\n")
    result = run_grader(script, Path("unused.json"), dict(os.environ), 0.01)
    assert result["error"] == "timeout"
    assert "score" not in result


def test_timeout_stops_background_children(tmp_path):
    marker = tmp_path / "surviving-child"
    script = tmp_path / "eval.sh"
    script.write_text(
        f"(sleep 0.15; touch {shlex.quote(str(marker))}) & wait\n"
    )
    result = run_grader(script, Path("unused.json"), dict(os.environ), 0.03)
    assert result["error"] == "timeout"
    time.sleep(0.25)
    assert not marker.exists()
