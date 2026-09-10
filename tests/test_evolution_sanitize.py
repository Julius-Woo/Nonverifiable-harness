"""Hidden-content filters preserve public evidence and input bytes."""

import base64
import json

import pytest

from evolution.judge_queue import export_trace
from evolution.sanitize import SanitizedTrajectory, sanitize, sanitize_file


@pytest.mark.parametrize("path", [
    "/tests/check.py", "/app/test_secret.py", "/app/eval.py",
    "/app/evaluator.py", "/app/eval/config", "/app/output/answer",
    "/app/notes/guide", "/app/judge_api.py", "/app/judge_train_eval/x",
    "/oracle/data", "/verifier/reward.txt", "reference_output: CANARY",
    "rubric: CANARY", "%2Ftests%2Fsecret", r"\u002ftests/secret",
    r"C:\tests\secret.py",
])
def test_hidden_access_removes_paired_results(path):
    records = [
        {"kind": "assistant", "step": 1,
         "text": json.dumps({"action": "terminal", "command": "cat " + path})},
        {"kind": "observation", "step": 1, "command": "cat " + path,
         "stdout": "CANARY"},
        {"kind": "finish", "answer": "Unable to finish"},
    ]
    before = json.dumps(records)
    result = sanitize(records)
    assert "CANARY" not in result.trajectory.events_json
    assert "CANARY" not in result.redactions_json
    assert len(result.trajectory.events) == 3
    assert json.dumps(records) == before


def test_encoded_fields_and_public_checks():
    hidden = base64.b64encode(b"rubric: SECRET-CANARY").decode()
    result = sanitize([
        {"kind": "artifact", "path": "/app/public.txt", "content": hidden},
        {"kind": "state", "content": "public", "oracle_result": 1},
        {"kind": "observation", "command": "python /app/check.py",
         "stdout": "public check succeeded"},
    ])
    assert hidden not in result.trajectory.events_json
    assert "oracle_result" not in result.trajectory.events_json
    assert "public check succeeded" in result.trajectory.events_json
    with pytest.raises(ValueError):
        SanitizedTrajectory('[{"kind":"state","content":"rubric: secret"}]')


def test_file_read_only_and_complete_export(tmp_path):
    source = tmp_path / "trace.jsonl"
    records = [
        {"kind": "instruction", "text": "Task"},
        {"kind": "observation", "command": "read public",
         "stdout": "z" * 13000},
    ]
    raw = "\n".join(json.dumps(r) for r in records)
    source.write_text(raw)
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    with pytest.raises(ValueError):
        sanitize_file(source, alias)
    hardlink = tmp_path / "hardlink"
    hardlink.hardlink_to(source)
    with pytest.raises(ValueError):
        sanitize_file(source, hardlink)
    evidence = export_trace(source, tmp_path / "export")
    assert len(evidence.trajectory.events[1]["stdout"]) == 13000
    assert source.read_text() == raw
