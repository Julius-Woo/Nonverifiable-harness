"""Trusted termination projection, without verifier diagnostics or labels."""

import re

REASONS = {
    "normal_finish",
    "no_tools_or_inability",
    "token_step_budget_exhaustion",
    "executor_failure",
    "protocol_parse_failure",
    "trial_exception",
    "unknown",
}
FLAGS = {
    "executor_failure",
    "protocol_failure",
    "nonzero_exit",
    "agent_timeout",
    "api_timeout",
    "action_executed",
}


def termination(records, *, result=None, execution=None):
    """Use recorded outcomes; an issued command alone is not execution proof.

    Match docs/calibration.md's terminal precedence, keeping recovered failure
    flags independent. Exception text is used locally and never exported.
    Unknown is explicit when an incomplete trace has no trusted outcome.
    """
    result, execution = result or {}, execution or {}
    exception = result.get("exception_info") or {}
    error_type = exception.get("exception_type") or ""
    message = exception.get("exception_message") or ""
    stack = exception.get("exception_traceback") or ""
    observations = [e for e in records if e.get("kind") == "observation"]
    terminal = [e for e in records if e.get("kind") == "termination"]
    previous = terminal[-1] if terminal else {}
    finishes = [e for e in records if e.get("kind") == "finish"]
    errors = [e for e in records if e.get("kind") == "error"]
    # Only execution-stack evidence identifies a result-level command timeout;
    # grader/build timeouts must not be recast as solver behaviour.
    in_exec = "await environment.exec(" in stack
    executor = in_exec or any(e.get("error") for e in observations)
    protocol = any(e.get("protocol_error") for e in observations)
    nonzero = any(e.get("return_code") not in (None, 0) for e in observations)
    agent_timeout = (
        "AgentTimeout" in error_type
        or execution.get("status") == "timeout_or_cancelled"
        or previous.get("status") == ("timeout_or_cancelled")
    )
    api_timeout = "Backend failed: TimeoutError" in message
    executed = any("command" in e for e in observations) or (
        in_exec
        and (
            "_collect_buffered_output" in stack
            or "process.communicate(" in stack
        )
    )
    flags = {
        "executor_failure": bool(executor),
        "protocol_failure": bool(protocol),
        "nonzero_exit": bool(nonzero),
        "agent_timeout": bool(agent_timeout),
        "api_timeout": bool(api_timeout),
        "action_executed": bool(executed),
    }
    for key in FLAGS:
        flags[key] = (
            flags[key]
            or previous.get(key) is True
            or execution.get(key) is True
        )
    assistants = [e for e in records if e.get("kind") == "assistant"]
    last_finish = assistants[-1].get("finish_reason") if assistants else None
    exhausted = (
        execution.get("finish_reason") == "length" or last_finish == "length"
    ) or bool(
        re.search(
            r"exhausted its \d+-call limit|budget exceeded|token limit|"
            r"model-call cap reached",
            message,
            re.I,
        )
    )
    if finishes:
        answer = finishes[-1].get("answer", "").replace("’", "'")
        inability = not flags["action_executed"] and re.search(
            r"\b(unable|cannot|can't|couldn't|could not|no tools|"
            r"tools? (?:access )?(?:is |are )?unavailable)\b",
            answer,
            re.I,
        )
        reason = "no_tools_or_inability" if inability else "normal_finish"
    elif previous.get("reason") in REASONS - {"unknown"}:
        reason = previous["reason"]
    elif execution.get("reason") in REASONS - {"unknown"}:
        reason = execution["reason"]
    elif (
        previous.get("status") == "finished"
        or execution.get("status") == "finished"
    ):
        reason = "normal_finish"
    elif exhausted:
        reason = "token_step_budget_exhaustion"
    elif flags["executor_failure"]:
        reason = "executor_failure"
    elif flags["protocol_failure"] and not (
        flags["agent_timeout"] or flags["api_timeout"]
    ):
        reason = "protocol_parse_failure"
    elif (
        error_type
        or errors
        or previous.get("status")
        in {
            "failed",
            "timeout_or_cancelled",
        }
        or execution.get("status") in {"failed", "timeout_or_cancelled"}
    ):
        reason = "trial_exception"
    else:
        reason = "unknown"
    return {
        "kind": "termination",
        "status": "finished"
        if reason in {"normal_finish", "no_tools_or_inability"}
        else ("unknown" if reason == "unknown" else "failed"),
        "reason": reason,
        **flags,
    }
