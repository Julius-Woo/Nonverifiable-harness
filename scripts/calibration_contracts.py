"""Offline R7 outcome contracts; raw evidence is never rewritten."""

import re

TERMINATIONS = (
    "normal_finish",
    "no_tools_or_inability",
    "token_step_budget_exhaustion",
    "protocol_parse_failure",
    "executor_failure",
    "trial_exception",
)


def classify_attempt(data, trace, calls, exception_text=""):
    """Separate terminal cause, action evidence, and disqualifying events.

    A recovered parse error disqualifies both labels, but does not replace
    a later normal finish as the terminal cause. An emitted tool call alone
    never proves execution. Historical subprocess collection tracebacks do.
    """
    exception = data.get("exception_info") or {}
    message = exception.get("exception_message") or ""
    error_type = exception.get("exception_type") or ""
    observations = [
        (i, row)
        for i, row in enumerate(trace, 1)
        if row.get("kind") == "observation" and "command" in row
    ]
    evidence = [
        {
            "source": "agent/trace.jsonl",
            "line": i,
            "event": "execution_outcome",
            "step": row.get("step"),
            "return_code": row.get("return_code"),
            "error": row.get("error"),
        }
        for i, row in observations
    ]
    # Historical Harbor RuntimeError bypassed the seed's TimeoutError catch.
    in_exec = "await environment.exec(" in exception_text
    collected = in_exec and (
        "_collect_buffered_output" in exception_text
        or "process.communicate(" in exception_text
    )
    if collected:
        evidence.append(
            {
                "source": "exception.txt",
                "event": "execution_started",
                "basis": (
                    "environment.exec reached subprocess output collection"
                ),
                "outcome": message,
            }
        )
    protocol_errors = sum("protocol_error" in row for row in trace)
    command_timeouts = sum(
        row.get("error") == "command timeout" for row in trace
    ) + int("Command timed out" in message)
    exec_errors = any(row.get("error") for _, row in observations)
    executor_failure = bool(command_timeouts or in_exec or exec_errors)
    nonzero = [
        {"line": i, "step": row.get("step"), "return_code": row["return_code"]}
        for i, row in observations
        if row.get("return_code") is not None and row["return_code"] != 0
    ]
    finishes = [row for row in trace if row.get("kind") == "finish"]
    answer = finishes[-1].get("answer", "") if finishes else None
    no_action = not evidence
    inability = bool(
        no_action
        and answer
        and re.search(
            r"\b(unable|cannot|can't|couldn't|could not|no tools|"
            r"tools? (?:access )?(?:is |are )?unavailable)\b",
            answer.replace("’", "'"),
            re.I,
        )
    )
    # Use trace order, never UUID order, to identify the final API response.
    by_id = {row["call_id"]: row for row in calls}
    assistants = [row for row in trace if row.get("kind") == "assistant"]
    last = by_id.get(assistants[-1].get("call_id"), {}) if assistants else {}
    token_exhaustion = bool(
        not finishes and last.get("finish_reason") == "length"
    )
    step_exhaustion = bool(re.search(r"exhausted its \d+-call limit", message))
    budget_exhaustion = "Projected rollout budget exceeded" in message
    api_rejected = any(
        a.get("status_code", 0) >= 400
        for row in calls
        for a in row.get("attempts", [])
    ) and not any(row.get("served_model") for row in calls)
    agent_timeout = "Timeout" in error_type and "Agent" in error_type
    if finishes and not error_type:
        termination = "no_tools_or_inability" if inability else "normal_finish"
        detail = (
            "inability claim without execution"
            if inability
            else "final answer"
        )
    elif token_exhaustion or step_exhaustion or budget_exhaustion:
        termination = "token_step_budget_exhaustion"
        detail = (
            "completion_token_limit"
            if token_exhaustion
            else "24_call_limit"
            if step_exhaustion
            else "rollout_budget_limit"
        )
    elif executor_failure:
        termination = "executor_failure"
        detail = (
            "command_timeout" if command_timeouts else "execution_exception"
        )
    elif protocol_errors and not api_rejected and not agent_timeout:
        termination = "protocol_parse_failure"
        detail = "unrecovered_action_parse_error"
    else:
        termination = "trial_exception"
        detail = (
            "api_rejection"
            if api_rejected
            else "agent_timeout"
            if agent_timeout
            else message or error_type or "missing_finish"
        )
    reward = ((data.get("verifier_result") or {}).get("rewards") or {}).get(
        "reward"
    )
    # AD10 explicitly includes action protocol/parse errors, even recovered.
    tool_failure_l1 = bool(executor_failure or protocol_errors)
    # PREREG separately rejects exhausted solvers. Neither a missing finish
    # record nor no action alone overrides a valid verifier reward.
    l1 = int(
        reward == 1
        and not (
            agent_timeout
            or tool_failure_l1
            or token_exhaustion
            or step_exhaustion
            or budget_exhaustion
        )
    )
    return {
        "termination": termination,
        "termination_detail": detail,
        "no_action": no_action,
        "action_evidence": evidence,
        "action_evidence_uncertain": bool(in_exec and not evidence),
        "no_tools_or_inability_claim": inability,
        "finish_answer": answer,
        "agent_timeout": agent_timeout,
        "executor_failure": executor_failure,
        "tool_failure_l1": tool_failure_l1,
        "protocol_errors": protocol_errors,
        "command_timeouts": command_timeouts,
        "nonzero_commands": nonzero,
        "token_exhaustion": token_exhaustion,
        "step_exhaustion": step_exhaustion,
        "budget_exhaustion": budget_exhaustion,
        "api_rejected": api_rejected,
        "pass_l1": l1,
        "pass_l2": int(l1 and not nonzero),
    }
