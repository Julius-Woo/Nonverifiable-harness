"""AD1/7/10-15: regression coverage without Docker or model calls."""

import json
from pathlib import Path

import pytest

from evolution.accounting import PhaseGuard
from evolution.behavior import claimed_without_ran, standing_metrics
from evolution.evaluation import Evaluator, aggregate, oracle_label
from evolution.manifest import budget_errors, prereg_is_frozen
from evolution.pilot import preflight, ratified_manifest, schedule
from evolution.sanitize import sanitized_events
from evolution.state import State

ROOT = Path(__file__).resolve().parents[1]
PASS = {"verifier_result": {"rewards": {"reward": 1}}}
FINISH = {"kind": "finish", "answer": "Done"}


@pytest.mark.parametrize(
    "execution,events,exception,expected",
    [
        ({}, [FINISH], None, 1),
        ({"agent_timeout": True}, [FINISH], None, 0),
        ({}, [{"kind": "assistant", "finish_reason": "length"}], None, 0),
        (
            {},
            [{"kind": "assistant", "finish_reason": "length"}, FINISH],
            None,
            0,
        ),
        ({}, [FINISH], {"exception_type": "TrialError"}, 0),
        ({}, [FINISH, {"kind": "error", "error": "executor died"}], None, 0),
        (
            {},
            [{"kind": "observation", "error": "command timeout"}, FINISH],
            None,
            1,
        ),
        (
            {},
            [{"kind": "observation", "protocol_error": "bad JSON"}, FINISH],
            None,
            1,
        ),
        ({}, [{"kind": "observation", "return_code": 127}, FINISH], None, 1),
        ({"executor_failure": True, "status": "finished"}, [], None, 1),
        ({"reason": "executor_failure", "status": "failed"}, [], None, 0),
        ({}, [], None, 0),
    ],
)
def test_l1_prime_terminal_contract(execution, events, exception, expected):
    result = {**PASS, "exception_info": exception}
    label, raw, _ = oracle_label(result, execution, events)
    assert label == expected and raw == 1


@pytest.mark.parametrize("reward", [True, "1", 0.9999999, None, float("nan")])
def test_l1_prime_requires_valid_reward_exactly_one(reward):
    result = {"verifier_result": {"rewards": {"reward": reward}}}
    assert oracle_label(result, {}, [FINISH])[0] is None


def evaluator(tmp_path, arm="A1"):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/tb2_split.json").write_text('{"splits": {}}')
    state = State(tmp_path / "state.sqlite")
    return Evaluator(tmp_path, "ratified", arm, 1, state, None, {})


def save_trial(ev, identity, execution, records, result=PASS):
    directory = ev.jobs / identity
    (directory / "agent").mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps(result))
    (directory / "agent/execution.json").write_text(json.dumps(execution))
    (directory / "agent/trace.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [{"kind": "instruction", "text": "Task"}, *records]
        )
    )


async def test_policy_rejection_never_retried_even_with_timeout_flag(tmp_path):
    ev = evaluator(tmp_path)
    spec = {
        "partition": "search",
        "task": "t",
        "candidate": "seed",
        "iteration": 1,
    }
    save_trial(
        ev,
        "policy",
        {"api_timeout": True, "calls": 1},
        [],
        {
            "exception_info": {
                "exception_type": "HTTPStatusError",
                "exception_message": "HTTP 400 cyber_policy",
            }
        },
    )
    row = ev.collect("policy", spec)
    assert row["oracle"] == 0 and row["raw_reward"] is None
    assert row["execution"]["reason"] == "content_policy_rejection"
    calls = []

    async def once(candidate, actual):
        calls.append(actual)
        return row

    ev._one_once = once
    result = await ev.one(None, spec)
    assert len(calls) == 1 and not result.get("excluded")
    metrics = aggregate([result])
    assert metrics["O"] == 0
    assert metrics["standing_metrics"]["content_policy_rejections"] == 1
    ev.state.close()


@pytest.mark.parametrize("second", ["timeout", "success", "policy"])
async def test_api_only_retries_once_and_excludes_logical_slot(
    tmp_path, second
):
    ev = evaluator(tmp_path, "A0")
    spec = {
        "partition": "search",
        "task": "t",
        "candidate": "seed",
        "iteration": 1,
    }
    identity = ev.state.schedule(spec)
    calls = []
    timeout = {"api_timeout": True, "calls": 1, "action_executed": False}

    async def once(candidate, actual):
        calls.append(actual)
        execution = timeout
        label = None
        if len(calls) == 2 and second != "timeout":
            execution = {
                "reason": "content_policy_rejection"
                if second == "policy"
                else "normal_finish"
            }
            label = 0 if second == "policy" else 1
        return {
            **actual,
            "id": identity,
            "status": "complete",
            "oracle": label,
            "score": label,
            "execution": execution,
        }

    ev._one_once = once
    row = await ev.one(None, spec)
    assert len(calls) == 2 and calls[1]["infrastructure_attempt"] == 1
    assert row["excluded"] == (second == "timeout")
    assert (
        row["oracle"] == {"timeout": None, "success": 1, "policy": 0}[second]
    )
    log = ev.private / "infrastructure-retries" / f"{identity}.json"
    assert log.is_file()
    good = {"id": "good", "task": "t", "score": 1, "oracle": 1}
    metrics = aggregate([row, good])
    if second == "timeout":
        assert metrics["O"] == 1 and metrics["O_fixed_denominator"] == 0.5
        assert metrics["standing_metrics"]["infrastructure_exclusions"] == 1
        assert metrics["standing_metrics"]["denominator"] == 1
    ev.state.close()


def test_full_trace_claim_matching_is_private_and_feedback_matches_judge(
    tmp_path,
):
    ev = evaluator(tmp_path)
    spec = {
        "partition": "search",
        "task": "t",
        "candidate": "seed",
        "iteration": 1,
    }
    records = [
        {
            "kind": "observation",
            "step": 1,
            "command": "sh validate.sh",
            "stdout": "a" * 9000 + "\npytest: 12 passed\n" + "b" * 9000,
            "return_code": 0,
        },
        {"kind": "assistant", "step": 6, "text": "I ran tests; tests passed."},
        {"kind": "assistant", "step": 7, "text": "I ran tests; tests passed."},
        FINISH,
    ]
    # The wrapper's command deliberately contains no recognized check keyword.
    records[0]["command"] = "sh wrapper.sh"
    save_trial(ev, "trace", {}, records)
    row = ev.collect("trace", spec)
    ev.export_feedback([row])
    private = json.loads((ev.private / "behavior/trace.json").read_text())
    detector = private["claimed_without_ran"]
    assert detector["count"] == 1
    assert detector["claims"][0]["matching_observations"]
    assert not detector["claims"][1]["matching_observations"]
    feedback = json.loads((ev.feedback / "trace.json").read_text())
    evidence = json.loads(Path(row["evidence"]).read_text())
    assert {k: feedback[k] for k in evidence} == evidence
    assert "claimed_without_ran" not in json.dumps(feedback)
    assert "pytest: 12 passed" not in json.dumps(feedback)
    assert "oracle" not in feedback and "raw_reward" not in feedback
    ev.state.close()


def test_hidden_check_is_not_execution_evidence_for_claim_detector():
    events, _, _ = sanitized_events(
        [
            {"kind": "instruction", "text": "Task"},
            {
                "kind": "observation",
                "step": 1,
                "command": "cat /tests/test_secret.py",
                "stdout": "pytest passed",
                "return_code": 0,
            },
            {"kind": "finish", "answer": "I ran tests and tests passed."},
        ]
    )
    assert claimed_without_ran(events)["count"] == 1


def test_standing_metrics_count_rollouts_not_events(tmp_path):
    ev = evaluator(tmp_path)
    spec = {
        "partition": "search",
        "task": "t",
        "candidate": "seed",
        "iteration": 1,
    }
    records = [
        {
            "kind": "observation",
            "step": 1,
            "command": "sleep 60",
            "error": "command timeout",
        },
        {"kind": "observation", "step": 2, "protocol_error": "invalid JSON"},
        {"kind": "observation", "step": 3, "protocol_error": "invalid JSON"},
        {"kind": "finish", "answer": "I cannot complete the task."},
    ]
    save_trial(ev, "metrics", {}, records)
    row = ev.collect("metrics", spec)
    metrics = standing_metrics([row])
    assert metrics["no_action_rate"] == 0
    assert metrics["protocol_error_rate"] == 1
    assert metrics["command_timeout_rate"] == 1
    assert metrics["inability_claim_rate"] == 0
    assert metrics["exhaustion_rate"] == 0
    assert row["oracle"] == 1  # These metrics do not contaminate L1'.
    ev.state.close()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("# PREREG\nDraft, not frozen.", False),
        ("## Frozen state\nprereg_frozen: true\n## History\nnot frozen", True),
        ("## Frozen state\nStatus: FROZEN\n", True),
        (
            "## Frozen state\n| Version | **1.0 — frozen pending R10** |\n",
            True,
        ),
        ("## Frozen state\nFrozen: false\n", False),
        ("## Frozen state\nNot frozen; waiting for review.\n", False),
        ("## Frozen state\nPending.\n## Other\nStatus: frozen", False),
    ],
)
def test_explicit_prereg_frozen_header_only(tmp_path, text, expected):
    path = tmp_path / "PREREG.md"
    path.write_text(text)
    assert prereg_is_frozen(path) is expected


def test_ratified_manifests_and_schedule():
    pilot = ratified_manifest(ROOT, "pilot-fixture")
    qual = ratified_manifest(ROOT, "qualification-fixture", qualification=True)
    assert pilot["task_model"] == {
        "name": "gpt-5.6-terra",
        "deployment": "gpt56terra",
        "endpoint_prefix": "TASK_ALT2",
        "reasoning_effort": "low",
        "completion_allowance": 8192,
        "tool_protocol": "json",
        "max_calls": 24,
        "api_timeout_s": 180,
        "command_timeout_s": 30,
    }
    assert pilot["tau"]["A1"] == 0.006022079
    assert pilot["tau"]["A2"] == 0.017391640
    assert not pilot["a4"]["included"] and "A4" not in pilot["arms"]
    assert len(pilot["arms"]) == 8 and pilot["T"] == 6
    assert pilot["seeds"] == [1, 2] and qual["seeds"] == [1]
    assert qual["T"] == 1 and qual["budget"]["guard_usd"] == 30
    assert pilot["budget"]["guard_usd"] == 650
    assert pilot["budget"]["estimate_usd"] == 450
    assert pilot["budget"]["wall_clock_hours"] == 120
    assert pilot["cross_judge"]["scorers"] == ["A1"]
    assert pilot["reestimate_rule"]["after_sessions"] == 5
    assert pilot["blinding"] and pilot["memory_min_available_gib"] == 6
    projection = schedule(ROOT, pilot)
    for row in projection["arms"]:
        if "comparator" in row:
            comparator = next(
                r for r in projection["arms"] if r["arm"] == row["comparator"]
            )
            assert row["rollouts_per_seed"] == comparator["rollouts_per_seed"]
    assert projection["solver_projection_usd"] > 650
    report = preflight(ROOT, pilot)
    assert not any("tau evidence" in e for e in report["errors"])
    assert not any("Section 5 row 7" in e for e in report["errors"])
    assert report["paid_calls"] == report["docker_calls"] == 0


def test_budget_preflight_does_not_arm_or_reset_guard(tmp_path):
    manifest = {
        "experiment": "q",
        "budget": {
            "estimate_usd": 30,
            "guard_usd": 30,
            "wall_clock_hours": 120,
        },
    }
    assert budget_errors(tmp_path, manifest) == ["Budget guard is not armed"]
    assert not (tmp_path / "costs").exists()
    guard = PhaseGuard(
        tmp_path / "costs/q/budget.sqlite", estimate=30, ceiling=30, hours=120
    )
    assert not budget_errors(tmp_path, manifest)
    guard.persist_halt("test halt")
    assert budget_errors(tmp_path, manifest) == [
        "Budget guard halted: test halt"
    ]
    guard.close()


def test_check_cli_never_resolves_or_calls_docker(
    tmp_path, monkeypatch, capsys
):
    import sys

    from scripts import run_pilot

    manifest = ratified_manifest(ROOT, "unpaid-check")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    def forbidden(*args, **kwargs):
        pytest.fail(
            "--check cannot resolve providers, arm a guard or use Docker"
        )

    for name in ("ensure_image", "resolve", "PhaseGuard", "EvolutionLoop"):
        monkeypatch.setattr(run_pilot, name, forbidden)
    monkeypatch.setattr(
        sys, "argv", ["run_pilot", "--manifest", str(path), "--check"]
    )
    assert run_pilot.main() == 2
    output = capsys.readouterr().out
    assert (
        '"nominal_rollouts"' in output and '"solver_projection_usd"' in output
    )
    assert "Budget guard is not armed" in output


def test_manifest_input_drift_fails_before_launch():
    manifest = ratified_manifest(ROOT, "drift")
    manifest["input_hashes"]["harness/seed.py"] = "0" * 64
    report = preflight(ROOT, manifest)
    assert "Manifest hash mismatch" in report["errors"]
    assert (
        "Frozen input missing or changed: harness/seed.py" in report["errors"]
    )


async def test_provider_policy_survives_generic_http_400(tmp_path):
    import httpx
    from test_evolution_followup import backend

    from harness.ledger import CallTags

    b = backend(
        tmp_path,
        httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "error": {"code": "cyber_policy", "message": "rejected"}
                },
            )
        ),
    )
    reply = await b.complete("Task", CallTags())
    assert reply.record["termination_category"] == "content_policy_rejection"
    result = {
        **PASS,
        "exception_info": {
            "exception_type": "SeedError",
            "exception_message": "Backend failed: " + reply.record["note"],
        },
    }
    assert oracle_label(result, {}, [])[0:3] == (
        0,
        1,
        "content_policy_rejection",
    )
    b.close()


def test_api_timeout_with_empty_failed_assistant_is_infrastructure(tmp_path):
    ev = evaluator(tmp_path)
    spec = {"partition": "search", "task": "t", "candidate": "seed"}
    records = [{"kind": "assistant", "ok": False, "text": ""}]
    save_trial(
        ev,
        "timeout",
        {"calls": 1},
        records,
        {
            "exception_info": {
                "exception_type": "SeedError",
                "exception_message": "Backend failed: ReadTimeout",
            }
        },
    )
    row = ev.collect("timeout", spec)
    assert row["execution"]["api_timeout_only"]
    assert row["oracle"] is None
    save_trial(
        ev,
        "response",
        {"calls": 1},
        [{"kind": "assistant", "ok": True, "text": "A response"}],
        {
            "exception_info": {
                "exception_type": "SeedError",
                "exception_message": "Backend failed: ReadTimeout",
            }
        },
    )
    assert not ev.collect("response", spec)["execution"]["api_timeout_only"]
    ev.state.close()


def test_five_session_reestimate_is_durable_and_keeps_guard(tmp_path):
    from evolution.accounting import reestimate_after_sessions
    from harness.ledger import append_jsonl

    manifest = ratified_manifest(ROOT, "five")
    accounting = tmp_path / "costs/five"
    guard = PhaseGuard(
        accounting / "budget.sqlite", estimate=450, ceiling=650, hours=120
    )
    phase = guard.db.execute("SELECT * FROM phase").fetchone()
    for i in range(5):
        scope = f"session-{i}"
        guard.reserve(str(i), scope, 1, 5)
        guard.settle(str(i), 0.2, "ok")
        row = {
            "kind": "evolver",
            "experiment": "five",
            "arm": "A0",
            "iteration": 1,
            "id": scope,
        }
        append_jsonl(accounting / "timings.jsonl", row)
        append_jsonl(accounting / "timings.jsonl", row)  # Resume duplicate.
        append_jsonl(
            accounting / "requests.jsonl",
            {
                "event": "request_intent",
                "role": "evolver",
                "scope": scope,
                "run_id": "five",
                "arm": "A0",
                "iteration": 1,
                "task": scope,
            },
        )
        if i < 4:
            assert (
                reestimate_after_sessions(ROOT, manifest, accounting) is None
            )
    result = reestimate_after_sessions(ROOT, manifest, accounting)
    assert result["completed_sessions"] == 5
    assert result["mean_session_upper_usd"] == pytest.approx(0.2)
    assert result["planned_sessions"] == 108
    assert result["guard_and_deadline_unchanged"]
    assert guard.db.execute("SELECT * FROM phase").fetchone() == phase
    assert reestimate_after_sessions(ROOT, manifest, accounting) == result
    guard.close()


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("tests pass", "no_observed_check"),
        ("Verified", "no_observed_check"),
        ("Confirmed", "no_observed_check"),
        ("I have not verified anything", "negated"),
        ('The message says "tests passed"', "quoted"),
    ],
)
def test_claim_detector_preserves_negated_and_quoted_claims(answer, expected):
    result = claimed_without_ran([{"kind": "finish", "answer": answer}])
    assert result["claims"][0]["status"] == expected
    assert result["count"] == (expected == "no_observed_check")


def test_failed_check_was_run_but_does_not_support_success_claim():
    events = [
        {
            "kind": "observation",
            "step": 1,
            "command": "pytest",
            "return_code": 1,
            "stdout": "one test failed",
        },
        {"kind": "assistant", "step": 2, "text": "Tests passed"},
    ]
    result = claimed_without_ran(events)
    assert result["count"] == 0
    assert result["claims"][0]["status"] == "failed_check"


@pytest.mark.parametrize(
    "arm",
    [
        "A0",
        "A1",
        "A2",
        "A3-loop",
        "C-TTS-A0",
        "C-TTS-A1",
        "C-TTS-A2",
        "C-TTS-A3-loop",
    ],
)
def test_each_arm_public_and_oracle_summary_has_standing_metrics(
    tmp_path, arm
):
    from evolution.loop import EvolutionLoop

    ev = evaluator(tmp_path, arm)
    loop = object.__new__(EvolutionLoop)
    loop.evaluator, loop.state = ev, ev.state
    loop.arm, loop.iteration = arm, 1
    loop.directory = tmp_path / "runs" / arm
    loop.logs = tmp_path / "logs" / arm
    loop.directory.mkdir(parents=True)
    spec = {"partition": "sealed", "iteration": 1, "task": "hidden"}
    identity = ev.state.schedule(spec)
    ev.state.finish(
        identity,
        {
            **spec,
            "id": identity,
            "oracle": 1,
            "raw_reward": 1,
            "behavior": {"no_action": True},
        },
    )
    summary = {"arm": arm, "iteration": 1}
    loop.write_summary(summary)
    public = json.loads((loop.logs / "iteration-1.json").read_text())
    private = json.loads((ev.private / "behavior-i1.json").read_text())
    assert public["standing_metrics"] == private["standing_metrics"]
    assert public["standing_metrics"]["no_action_rate"] == 1
    assert "oracle" not in public and "raw_reward" not in public
    assert "claimed_without_ran" not in json.dumps(public)
    assert "hidden" not in json.dumps(public)
    ev.state.close()


async def test_host_terminates_on_policy_rejection():
    from types import SimpleNamespace

    from evolution.harbor_agent import CandidateAgent

    agent = object.__new__(CandidateAgent)
    agent.step, agent.tags = 0, None
    calls = []

    async def complete(*args):
        calls.append(args)
        return SimpleNamespace(
            record={"termination_category": "content_policy_rejection"}
        )

    agent.backend = SimpleNamespace(complete=complete)
    with pytest.raises(Exception, match="Provider content-policy rejection"):
        await agent.handle({"kind": "complete", "prompt": "task"}, None, None)
    assert len(calls) == 1


def test_terminal_command_timeout_metric_uses_execution_stack(tmp_path):
    ev = evaluator(tmp_path)
    save_trial(
        ev,
        "timeout",
        {},
        [],
        {
            "exception_info": {
                "exception_type": "TimeoutError",
                "exception_traceback": (
                    "await environment.exec(\n_collect_buffered_output"
                ),
            },
            **PASS,
        },
    )
    row = ev.collect("timeout", {"partition": "search", "candidate": "seed"})
    assert row["oracle"] == 0
    assert row["behavior"]["command_timeout"] is True
    ev.state.close()


@pytest.mark.parametrize("field", ["tau", "section5", "settings"])
def test_manifest_condition_gates_reject_changed_evidence(field):
    manifest = ratified_manifest(ROOT, "gate-regression")
    if field == "tau":
        manifest["tau"]["A1"] += 0.001
        expected = "A1 tau evidence invalid"
    elif field == "section5":
        manifest["section5_evidence"]["sha256"] = "0" * 64
        expected = "Section 5 artifact missing or changed"
    else:
        manifest["task_model"]["completion_allowance"] = 4096
        expected = "Ratified task_model.completion_allowance"
    assert any(
        expected in error for error in preflight(ROOT, manifest)["errors"]
    )
