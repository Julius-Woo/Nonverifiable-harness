"""Hidden-content filters preserve public evidence and input bytes."""

import base64
import json

import pytest

from evolution.judge_queue import export_trace
from evolution.sanitize import SanitizedTrajectory, sanitize, sanitize_file


@pytest.mark.parametrize(
    "path",
    [
        "/tests/check.py",
        "/app/test_secret.py",
        "/app/eval.py",
        "/app/evaluator.py",
        "/app/eval/config",
        "/app/output/answer",
        "/app/notes/guide",
        "/app/judge_api.py",
        "/app/judge_train_eval/x",
        "/oracle/data",
        "/verifier/reward.txt",
        "reference_output: CANARY",
        "rubric: CANARY",
        "%2Ftests%2Fsecret",
        r"\u002ftests/secret",
        r"C:\tests\secret.py",
    ],
)
def test_hidden_access_removes_paired_results(path):
    records = [
        {
            "kind": "assistant",
            "step": 1,
            "text": json.dumps(
                {"action": "terminal", "command": "cat " + path}
            ),
        },
        {
            "kind": "observation",
            "step": 1,
            "command": "cat " + path,
            "stdout": "CANARY",
        },
        {"kind": "finish", "answer": "Unable to finish"},
    ]
    before = json.dumps(records)
    result = sanitize(records)
    assert "CANARY" not in result.trajectory.events_json
    assert "CANARY" not in result.redactions_json
    assert len(result.trajectory.events) == 4
    assert result.trajectory.events[-1]["kind"] == "termination"
    assert json.dumps(records) == before


def test_encoded_fields_and_public_checks():
    hidden = base64.b64encode(b"rubric: SECRET-CANARY").decode()
    result = sanitize(
        [
            {"kind": "artifact", "path": "/app/public.txt", "content": hidden},
            {"kind": "state", "content": "public", "oracle_result": 1},
            {
                "kind": "observation",
                "command": "python /app/check.py",
                "stdout": "public check succeeded",
            },
        ]
    )
    assert hidden not in result.trajectory.events_json
    assert "oracle_result" not in result.trajectory.events_json
    assert "public check succeeded" in result.trajectory.events_json
    with pytest.raises(ValueError):
        SanitizedTrajectory('[{"kind":"state","content":"rubric: secret"}]')


def test_file_read_only_and_v3_export(tmp_path):
    source = tmp_path / "trace.jsonl"
    records = [
        {"kind": "instruction", "text": "Task"},
        {
            "kind": "observation",
            "command": "read public",
            "stdout": "z" * 13000,
        },
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
    assert len(evidence.trajectory.events[1]["stdout"]) < 13000
    assert evidence.trajectory.truncations == [
        {
            "observation_index": 0,
            "original_bytes": 13011,
            "kept_bytes": 8000,
        }
    ]
    assert source.read_text() == raw


# Exact archived R6 traces, embedded so this regression needs no local logs.
R6_TRACES = [
    [
        {
            "kind": "instruction",
            "text": (
                "There's a /app/filter.py script that att"
                "empts to remove JavaScript from HTML fil"
                "es to prevent XSS attacks.\nThe /app/filt"
                "er.py script takes an HTML file as a com"
                "mand-line argument and modifies it in-pl"
                "ace.\n\nUsage: python /app/filter.py <html"
                "_file>\n\nYour task is to create a file ca"
                "lled /app/out.html that, even after bein"
                "g processed by /app/filter.py, \nwill sti"
                "ll trigger a JavaScript alert() when ope"
                "ned in a browser.\nYour file should not r"
                "equire any interaction with the webpage "
                "and should trigger automatically.\n\nYou c"
                "an run /app/test_outputs.py to verify.\n"
            ),
        },
        {
            "kind": "assistant",
            "step": 0,
            "text": '{"action":"read_file","path":"/app/filter.py"}',
            "call_id": "be94b8d883624a12b976d054f3c0adca",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 0,
            "command": "cat -- /app/filter.py",
            "stdout": (
                "import sys\n\nfrom bs4 import BeautifulSou"
                "p\n\n\ndef remove_js(html):\n    soup = Beau"
                'tifulSoup(html, "html.parser")\n\n    # re'
                "move <script> tags\n    for script in sou"
                'p("script"):\n        script.decompose()\n'
                "\n    # remove <frame> tags\n    for bad i"
                'n ["frame", "iframe", "object", "embed"]'
                ":\n        for frame in soup(bad):\n      "
                "      frame.decompose()\n\n    for tag in "
                "soup.find_all(True):\n        for attr in"
                " list(tag.attrs):\n            if attr.st"
                'artswith("on"):\n                del tag.'
                "attrs[attr]\n\n    # convert back to strin"
                "g and return\n    return str(soup)\n\n\nif _"
                '_name__ == "__main__":\n    if len(sys.ar'
                'gv) != 2:\n        print("Usage: python f'
                'ilter.py <html_file>")\n        sys.exit('
                "1)\n\n    filename = sys.argv[1]\n\n    # Re"
                'ad the file\n    with open(filename, "r")'
                " as f:\n        html_content = f.read()\n\n"
                "    # Process the HTML\n    filtered_html"
                " = remove_js(html_content)\n\n    # Write "
                'back in-place\n    with open(filename, "w'
                '") as f:\n        f.write(filtered_html)\n'
            ),
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.08241570903919637,
        },
        {
            "kind": "assistant",
            "step": 1,
            "text": (
                '{"action":"write_file","path":"/app/out.'
                'html","content":"<html><head><meta http-'
                'equiv=\\"refresh\\" content=\\"0;url=javasc'
                'ript:alert(1)\\"></head><body>test</body>'
                '</html>"}'
            ),
            "call_id": "baaaa047e27245ca827ee55ce89ad2e1",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 1,
            "command": (
                "printf %s PGh0bWw+PGhlYWQ+PG1ldGEgaHR0cC"
                "1lcXVpdj0icmVmcmVzaCIgY29udGVudD0iMDt1cm"
                "w9amF2YXNjcmlwdDphbGVydCgxKSI+PC9oZWFkPj"
                "xib2R5PnRlc3Q8L2JvZHk+PC9odG1sPg== | bas"
                "e64 -d > /app/out.html"
            ),
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.08903034403920174,
        },
        {
            "kind": "assistant",
            "step": 2,
            "text": (
                '{"action":"terminal","command":"python /app/test_outputs.py"}'
            ),
            "call_id": "8d8a1d0201de490fa07b793b091fc97b",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 2,
            "command": "python /app/test_outputs.py",
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.31883255299180746,
        },
        {
            "kind": "assistant",
            "step": 3,
            "text": (
                '{"action":"finish","answer":"Created /ap'
                "p/out.html containing a meta refresh to "
                "javascript:alert(1), which survives /app"
                "/filter.py and will trigger automaticall"
                'y when opened."}'
            ),
            "call_id": "6b4cb465d3ea4bd1ae0f51edb5e21221",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "finish",
            "answer": (
                "Created /app/out.html containing a meta "
                "refresh to javascript:alert(1), which su"
                "rvives /app/filter.py and will trigger a"
                "utomatically when opened."
            ),
        },
    ],
    [
        {
            "kind": "instruction",
            "text": (
                "There's a /app/filter.py script that att"
                "empts to remove JavaScript from HTML fil"
                "es to prevent XSS attacks.\nThe /app/filt"
                "er.py script takes an HTML file as a com"
                "mand-line argument and modifies it in-pl"
                "ace.\n\nUsage: python /app/filter.py <html"
                "_file>\n\nYour task is to create a file ca"
                "lled /app/out.html that, even after bein"
                "g processed by /app/filter.py, \nwill sti"
                "ll trigger a JavaScript alert() when ope"
                "ned in a browser.\nYour file should not r"
                "equire any interaction with the webpage "
                "and should trigger automatically.\n\nYou c"
                "an run /app/test_outputs.py to verify.\n"
            ),
        },
        {
            "kind": "assistant",
            "step": 0,
            "text": '{"action":"terminal","command":"ls -la"}',
            "call_id": "7661e70d28474b24a63946a18ce31748",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 0,
            "command": "ls -la",
            "stdout": (
                "total 16\ndrwxr-xr-x 1 root root 4096 Oct"
                " 31  2025 .\ndrwxr-xr-x 1 root root 4096 "
                "Sep 10 10:19 ..\n-rw-r--r-- 1 root root  "
                "960 Oct 24  2025 filter.py\n-rw-r--r-- 1 "
                "root root 3270 Oct 24  2025 test_outputs"
                ".py\n"
            ),
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.08254679688252509,
        },
        {
            "kind": "assistant",
            "step": 1,
            "text": '{"action":"read_file","path":"/app/filter.py"}',
            "call_id": "abba087cb29c4004a5fb47cf459dfe5c",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 1,
            "command": "cat -- /app/filter.py",
            "stdout": (
                "import sys\n\nfrom bs4 import BeautifulSou"
                "p\n\n\ndef remove_js(html):\n    soup = Beau"
                'tifulSoup(html, "html.parser")\n\n    # re'
                "move <script> tags\n    for script in sou"
                'p("script"):\n        script.decompose()\n'
                "\n    # remove <frame> tags\n    for bad i"
                'n ["frame", "iframe", "object", "embed"]'
                ":\n        for frame in soup(bad):\n      "
                "      frame.decompose()\n\n    for tag in "
                "soup.find_all(True):\n        for attr in"
                " list(tag.attrs):\n            if attr.st"
                'artswith("on"):\n                del tag.'
                "attrs[attr]\n\n    # convert back to strin"
                "g and return\n    return str(soup)\n\n\nif _"
                '_name__ == "__main__":\n    if len(sys.ar'
                'gv) != 2:\n        print("Usage: python f'
                'ilter.py <html_file>")\n        sys.exit('
                "1)\n\n    filename = sys.argv[1]\n\n    # Re"
                'ad the file\n    with open(filename, "r")'
                " as f:\n        html_content = f.read()\n\n"
                "    # Process the HTML\n    filtered_html"
                " = remove_js(html_content)\n\n    # Write "
                'back in-place\n    with open(filename, "w'
                '") as f:\n        f.write(filtered_html)\n'
            ),
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.12439010199159384,
        },
        {
            "kind": "assistant",
            "step": 2,
            "text": (
                '{"action":"write_file","path":"/app/out.'
                'html","content":"<!doctype html>\\n<html>'
                '\\n  <head>\\n    <meta http-equiv=\\"refre'
                'sh\\" content=\\"0;url=data:text/html,%3Cs'
                'vg%20onload%3Dalert(1)%3E%3C/svg%3E\\">\\n'
                "    <title>XSS test</title>\\n  </head>\\n"
                "  <body>\\n    Redirecting...\\n  </body>\\"
                'n</html>"}'
            ),
            "call_id": "d6fe4961a5a245aa9002c2a1f568c9aa",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 2,
            "command": (
                "printf %s PCFkb2N0eXBlIGh0bWw+CjxodG1sPg"
                "ogIDxoZWFkPgogICAgPG1ldGEgaHR0cC1lcXVpdj"
                "0icmVmcmVzaCIgY29udGVudD0iMDt1cmw9ZGF0YT"
                "p0ZXh0L2h0bWwsJTNDc3ZnJTIwb25sb2FkJTNEYW"
                "xlcnQoMSklM0UlM0Mvc3ZnJTNFIj4KICAgIDx0aX"
                "RsZT5YU1MgdGVzdDwvdGl0bGU+CiAgPC9oZWFkPg"
                "ogIDxib2R5PgogICAgUmVkaXJlY3RpbmcuLi4KIC"
                "A8L2JvZHk+CjwvaHRtbD4= | base64 -d > /ap"
                "p/out.html"
            ),
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.09092128998599946,
        },
        {
            "kind": "assistant",
            "step": 3,
            "text": (
                '{"action":"terminal","command":"python /'
                'app/test_outputs.py /app/out.html"}'
            ),
            "call_id": "e1691437fed94a4da3a35c34d32638ee",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "observation",
            "step": 3,
            "command": "python /app/test_outputs.py /app/out.html",
            "stdout": "",
            "stderr": "",
            "return_code": 0,
            "wall_s": 0.3815055340528488,
        },
        {
            "kind": "assistant",
            "step": 4,
            "text": (
                '{"action":"finish","answer":"/app/out.ht'
                "ml created; it uses a meta-refresh to a "
                "data: URL with an SVG onload alert, whic"
                "h survives the filter and triggers alert"
                ' automatically."}'
            ),
            "call_id": "24edb8683a6a43a8911ae0cbb8736d66",
            "ok": True,
            "model": "gpt-5-mini-2025-08-07",
            "requested_model": "gpt-5-mini",
        },
        {
            "kind": "finish",
            "answer": (
                "/app/out.html created; it uses a meta-re"
                "fresh to a data: URL with an SVG onload "
                "alert, which survives the filter and tri"
                "ggers alert automatically."
            ),
        },
    ],
]


@pytest.mark.parametrize("records", R6_TRACES, ids=["bLbbJTC", "debSxwY"])
def test_r6_real_instructions_preserved_with_hidden_test_mention(records):
    from evolution.judges import JudgeInput, build_payload

    instruction = records[0]["text"]
    assert "/app/test_outputs.py" in instruction
    cleaned = sanitize(records)
    assert cleaned.trajectory.events[0]["text"] == instruction
    evidence = JudgeInput(instruction, cleaned.trajectory)
    for judge in ("a1", "a2"):
        assert build_payload(judge, evidence)["task_text"] == instruction
    assert not any(
        r["event"] == 0 and r["field"] == "text" for r in cleaned.redactions
    )


def test_instruction_is_exact_but_hidden_test_contents_are_removed():
    text = "Run /app/test_outputs.py; expected_output: public requirement"
    records = [
        {"kind": "instruction", "text": text},
        {
            "kind": "assistant",
            "step": 0,
            "text": json.dumps(
                {
                    "action": "read_file",
                    "path": "/app/test_outputs.py",
                }
            ),
        },
        {"kind": "observation", "step": 0, "stdout": "HIDDEN-ASSERTION"},
    ]
    result = sanitize(records, hidden_values=["public requirement"])
    assert result.trajectory.events[0]["text"] == text
    assert "HIDDEN-ASSERTION" not in result.trajectory.events_json
    assert "HIDDEN-ASSERTION" not in result.redactions_json


def test_v3_export_caps_observation_stream_and_preserves_ends(tmp_path):
    records = [
        {"kind": "instruction", "text": "Complete task"},
        {
            "kind": "observation",
            "command": "visible command",
            "stdout": "S" * 30000,
            "stderr": "E" * 30000,
            "return_code": 0,
        },
    ]
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records))
    evidence = export_trace(path, tmp_path / "out")
    assert evidence.trajectory.version == "v3"
    event = evidence.trajectory.events[1]
    assert event["command"] == "visible command"
    assert event["stdout"] == "S" * 3985 + "[... omitted 26015 bytes ...]"
    assert event["stderr"] == "[... omitted 26000 bytes ...]" + "E" * 4000
    assert event["return_code"] == 0
    assert evidence.trajectory.truncations == [
        {
            "observation_index": 0,
            "original_bytes": 60015,
            "kept_bytes": 8000,
        }
    ]
    custom = export_trace(path, tmp_path / "custom", observation_chars=10)
    assert custom.trajectory.truncations[0]["kept_bytes"] == 20
    assert path.read_text() == "\n".join(json.dumps(r) for r in records)
    for version in ("sanitized-trajectory-v1", "sanitized-trajectory-v2"):
        with pytest.raises(ValueError, match="version"):
            SanitizedTrajectory.from_dict(
                {"version": version, "events": records}
            )


@pytest.mark.parametrize("size", [7999, 8000, 8001])
def test_v3_per_observation_boundary(size):
    text = "a" * size
    trajectory = sanitize([{"kind": "observation", "stdout": text}]).trajectory
    if size <= 8000:
        assert trajectory.events[0]["stdout"] == text
        assert not trajectory.truncations
    else:
        assert trajectory.events[0]["stdout"] == (
            "a" * 4000 + "[... omitted 1 bytes ...]" + "a" * 4000
        )
        assert trajectory.truncations[0]["kept_bytes"] == 8000


def test_v3_unicode_bytes_and_empty_ends():
    trajectory = sanitize(
        [
            {"kind": "observation", "stdout": "é🙂漢x"},
        ],
        observation_chars=1,
    ).trajectory
    assert trajectory.events[0]["stdout"] == "é[... omitted 7 bytes ...]x"
    assert trajectory.truncations == [
        {
            "observation_index": 0,
            "original_bytes": 10,
            "kept_bytes": 3,
        }
    ]
    zero = sanitize(
        [{"kind": "observation", "stdout": "é🙂漢x"}], observation_chars=0
    ).trajectory
    assert zero.events[0]["stdout"] == "[... omitted 10 bytes ...]"
    assert zero.truncations[0]["kept_bytes"] == 0


def test_v3_total_cap_includes_json_metadata_and_trims_longest():
    from evolution.sanitize import canonical

    records = [
        {"kind": "instruction", "text": "Task"},
        {"kind": "observation", "stdout": "s" * 200},
        {"kind": "observation", "stdout": "L" * 2000},
        {"kind": "finish", "answer": "Done"},
    ]
    full = sanitize(records, trajectory_chars=9999).trajectory
    boundary = len(canonical(full.to_dict()))
    # Keep parameter digit width constant while locating the exact boundary.
    full = sanitize(records, trajectory_chars=boundary).trajectory
    assert len(canonical(full.to_dict())) == boundary
    assert not full.truncations
    capped = sanitize(records, trajectory_chars=boundary - 1).trajectory
    assert len(canonical(capped.to_dict())) == boundary - 1
    assert capped.events[1] == records[1]
    assert capped.truncations[0]["observation_index"] == 1
    assert capped.events[0] == records[0]
    assert capped.events[-2] == records[-1]
    assert capped.events[-1] == full.events[-1]
    assert SanitizedTrajectory.from_dict(capped.to_dict()) == capped


def test_v3_total_cap_multiple_observations_and_irreducible_evidence():
    from evolution.sanitize import canonical

    records = [
        {"kind": "observation", "stdout": "x" * 8000} for _ in range(30)
    ]
    trajectory = sanitize(records).trajectory
    assert len(canonical(trajectory.to_dict())) <= 200000
    assert len(trajectory.truncations) > 1
    assert [r["observation_index"] for r in trajectory.truncations] == list(
        range(len(trajectory.truncations))
    )
    with pytest.raises(ValueError, match="non-observation"):
        sanitize([{"kind": "instruction", "text": "x" * 200000}])


def test_v3_sanitizes_hidden_content_before_cap_and_preserves_original():
    records = [{"kind": "observation", "stdout": "x" * 9000 + " CANARY"}]
    original = json.dumps(records)
    trajectory = sanitize(records, hidden_values=["CANARY"]).trajectory
    assert trajectory.events[0]["stdout"] == "[REDACTED]"
    assert not trajectory.truncations
    assert json.dumps(records) == original


@pytest.mark.parametrize(
    "observation_chars,trajectory_chars",
    [
        (-1, 200000),
        (True, 200000),
        (1.5, 200000),
        (4000, 0),
        (4000, False),
    ],
)
def test_v3_invalid_cap_parameters(observation_chars, trajectory_chars):
    with pytest.raises(ValueError):
        sanitize(
            [],
            observation_chars=observation_chars,
            trajectory_chars=trajectory_chars,
        )


def test_termination_uses_timeout_evidence_without_verifier_diagnostics(
    tmp_path,
):
    from evolution.judges import build_payload

    records = [
        {"kind": "instruction", "text": "Run inference"},
        {"kind": "assistant", "text": '{"action":"terminal","command":"run"}'},
    ]
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records))
    evidence = export_trace(
        path,
        tmp_path / "out",
        result={
            "verifier_result": {"rewards": {"reward": 1}, "secret": "CANARY"},
            "exception_info": {
                "exception_type": "RuntimeError",
                "exception_message": "Command timed out after 30 seconds",
                "exception_traceback": "await environment.exec(\n"
                "_collect_buffered_output CANARY",
            },
        },
    )
    outcome = evidence.trajectory.events[-1]
    assert outcome["reason"] == "executor_failure"
    assert outcome["status"] == "failed"
    assert outcome["executor_failure"] and outcome["action_executed"]
    for judge in ("a1", "a2"):
        assert "CANARY" not in json.dumps(build_payload(judge, evidence))
    assert build_payload("a1", evidence)["final_summary"]["termination"] == {
        k: v for k, v in outcome.items() if k != "kind"
    }


@pytest.mark.parametrize(
    "records,execution,reason",
    [
        ([{"kind": "finish", "answer": "Done"}], {}, "normal_finish"),
        (
            [{"kind": "finish", "answer": "No tools available"}],
            {},
            "no_tools_or_inability",
        ),
        ([], {"finish_reason": "length"}, "token_step_budget_exhaustion"),
        (
            [{"kind": "observation", "protocol_error": "bad JSON"}],
            {},
            "protocol_parse_failure",
        ),
        ([{"kind": "error", "text": "runtime failed"}], {}, "trial_exception"),
        ([], {}, "unknown"),
    ],
)
def test_termination_taxonomy(records, execution, reason):
    outcome = sanitize(records, execution=execution).trajectory.events[-1]
    assert outcome["reason"] == reason


def test_recovered_failure_flags_do_not_replace_normal_finish():
    result = sanitize(
        [
            {"kind": "observation", "protocol_error": "bad JSON"},
            {"kind": "observation", "command": "run", "error": "timeout"},
            {"kind": "finish", "answer": "I recovered"},
        ]
    )
    outcome = result.trajectory.events[-1]
    assert outcome["reason"] == "normal_finish"
    assert outcome["executor_failure"] and outcome["protocol_failure"]


def test_explicit_termination_survives_missing_finish_and_recovered_error():
    from evolution.judges import JudgeInput, final_summary

    records = [
        {"kind": "instruction", "text": "Task"},
        {"kind": "observation", "command": "run", "error": "old timeout"},
        {
            "kind": "termination",
            "status": "finished",
            "reason": "normal_finish",
        },
    ]
    evidence = JudgeInput("Task", sanitize(records).trajectory)
    outcome = final_summary(evidence.trajectory)["termination"]
    assert outcome["status"] == "finished"
    assert outcome["reason"] == "normal_finish"
    assert outcome["executor_failure"]
    assert final_summary(evidence.trajectory)["final_solver_message"] is None


def test_issued_tool_call_without_execution_is_not_action_evidence():
    outcome = sanitize(
        [
            {
                "kind": "assistant",
                "text": '{"action":"terminal","command":"run"}',
            }
        ],
        execution={"tool_calls": 1, "status": "failed"},
    ).trajectory.events[-1]
    assert not outcome["action_executed"]


def test_conflicting_export_preserves_original_archive(tmp_path):
    source = tmp_path / "trace.jsonl"
    source.write_text(json.dumps({"kind": "instruction", "text": "Original"}))
    output = tmp_path / "export"
    export_trace(source, output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    source.write_text(json.dumps({"kind": "instruction", "text": "Changed"}))
    with pytest.raises(ValueError, match="Frozen trace export conflict"):
        export_trace(source, output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_redaction_locations_still_reference_raw_indexes():
    result = sanitize(
        [
            {"kind": "instruction", "text": "Task"},
            {"kind": "termination", "status": "failed"},
            {
                "kind": "artifact",
                "path": "/tests/check.py",
                "content": "CANARY",
            },
        ]
    )
    assert (
        next(
            r for r in result.redactions if r["reason"] == "disallowed_event"
        )["event"]
        == 2
    )


def test_v1_projected_observations_cannot_be_wrapped_as_v3():
    with pytest.raises(ValueError, match="separate evidence version"):
        sanitize(
            [
                {"kind": "instruction", "text": "Task"},
                {"kind": "observation", "visible_result": "old capped output"},
            ]
        )


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "oversized", "negative"]
)
def test_v3_rejects_invalid_truncation_provenance(change):
    value = sanitize(
        [{"kind": "observation", "stdout": "x" * 9000}]
    ).trajectory.to_dict()
    if change == "missing":
        value["truncations"] = []
    elif change == "duplicate":
        value["truncations"] *= 2
    elif change == "oversized":
        value["events"][0]["stdout"] += "x" * 9000
    else:
        value["truncations"][0]["kept_bytes"] = -1
    with pytest.raises(ValueError):
        SanitizedTrajectory.from_dict(value)


def test_v3_total_cap_handles_marker_disappearance_at_field_boundaries():
    from evolution.sanitize import canonical

    records = [
        {
            "kind": "observation",
            "command": "short",
            "stdout": "x" * 1000,
            "stderr": "short",
        }
    ]
    # The smallest serialization retains the short end fields: replacing
    # each with a marker would increase size and incorrectly reject this cap.
    almost_empty = sanitize(
        records, observation_chars=5, trajectory_chars=1000
    ).trajectory
    cap = len(canonical(almost_empty.to_dict()))
    # Adjust for the shorter trajectory_chars number in the envelope.
    cap -= len(str(1000)) - len(str(cap))
    value = sanitize(
        records, observation_chars=4000, trajectory_chars=cap + 6
    ).trajectory
    assert len(canonical(value.to_dict())) <= cap + 6
    assert value.events[0]["command"] == "short"
    assert value.events[0]["stderr"] == "short"
    assert value.truncations[0]["kept_bytes"] >= 10
