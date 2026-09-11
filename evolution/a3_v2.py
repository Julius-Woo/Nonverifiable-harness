# Frozen v2 compatibility implementation for the explicitly v2 A3 arm.
# Kept independent of the concurrent generic evidence-v3 migration.
# Origin: committed evolution/sanitize.py, judges.JudgeInput,
# and judge_queue.export_trace; no truncation or schema migration.
"""Versioned, non-mutating TB2/GDPevo evidence exports.

This is defense in depth, not a replacement for filesystem/service isolation.
Redaction provenance contains hashes and locations, never removed content.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from evolution.outcomes import FLAGS, REASONS, termination

VERSION = "sanitized-trajectory-v2"
REDACTED = "[REDACTED]"
PATH_PATTERN = re.compile(
    r"(?i)(?:^|[/\\\s\"'=:;(`])(?:"
    r"tests[/\\]|test_[\w.-]+\.py\b|"
    r"eval(?:[/\\]|\.py\b)|evaluator\.py\b|"
    r"output[/\\]|notes[/\\]|judge_api(?:\.py)?\b|"
    r"judge_train_eval(?:[/\\]|\b)|oracle(?:[/\\]|\b)|"
    r"verifier(?:[/\\]|\b)|reference[_ -]?(?:outputs?|solutions?)\b|"
    r"reward\.txt\b|test-stdout\.txt\b|test-stderr\.txt\b)"
)
FIELD_PATTERN = re.compile(
    r"(?i)(?:rubric|grading_criteria|evaluation_criteria|expected_output|"
    r"reference_output|reference_solution|oracle_result|verifier_result|"
    r"judge_score|judge_rationale|ground_truth|test_output|test_results)"
)
TEXT_FIELD_PATTERN = re.compile(
    r"(?im)[\"']?(?:rubric\w*|grading_criteria|evaluation_criteria|"
    r"expected_output|reference_output|reference_solution|oracle_result|"
    r"verifier_result|judge_score|judge_rationale|ground_truth|"
    r"test_output|test_results)[\"']?\s*[:=]"
)
ALLOWED = {
    "instruction": {"text"},
    "assistant": {"text", "step", "ok", "finish_reason"},
    "observation": {
        "step",
        "command",
        "stdout",
        "stderr",
        "return_code",
        "error",
        "protocol_error",
        "wall_s",
    },
    "finish": {"answer"},
    "error": {"text", "step", "error"},
    "termination": {"status", "reason", *FLAGS},
    "artifact": {"path", "content", "step"},
    "state": {"content", "step"},
    "redacted": {"step"},
}


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False
    )


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _views(text):
    """Inspect common encodings, without executing anything from a trace."""
    yield text
    normalized = text.replace("\\\\", "/")
    for _ in range(3):
        decoded = unquote(normalized)
        decoded = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda m: chr(int(m[1], 16)),
            decoded,
        )
        decoded = re.sub(
            r"\\x([0-9a-fA-F]{2})",
            lambda m: chr(int(m[1], 16)),
            decoded,
        )
        if decoded == normalized:
            break
        normalized = decoded
        yield normalized
    # Covers seed write_file's base64 shell wrapper and encoded references.
    for match in re.finditer(r"(?<![\w])[A-Za-z0-9+/]{16,}={0,2}", text):
        token = match[0]
        if len(token) > 200_000:
            continue
        try:
            yield base64.b64decode(token, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass


def forbidden(text):
    return any(
        PATH_PATTERN.search(v) or TEXT_FIELD_PATTERN.search(v)
        for v in _views(text)
    )


@dataclass(frozen=True, slots=True)
class SanitizedTrajectory:
    """Immutable evidence; trusted provenance is deliberately separate."""

    events_json: str
    version: str = VERSION

    def __post_init__(self):
        if self.version != VERSION:
            raise ValueError("Unsupported sanitizer version")
        events = json.loads(self.events_json)
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            kind = event.get("kind")
            if kind not in ALLOWED or set(event) - ALLOWED[kind] - {"kind"}:
                raise ValueError("Disallowed trajectory fields")
            for value in event.values():
                if not isinstance(value, (str, int, float, bool, type(None))):
                    raise ValueError("Event values must be scalar")
                if (
                    kind != "instruction"
                    and isinstance(value, str)
                    and forbidden(value)
                ):
                    raise ValueError("Evidence must be sanitized")
            if kind == "termination":
                if event.get("reason") not in REASONS:
                    raise ValueError("Invalid termination reason")
                if event.get("status") not in {
                    "finished",
                    "failed",
                    "unknown",
                }:
                    raise ValueError("Invalid termination status")
                if any(type(event.get(k)) is not bool for k in FLAGS):
                    raise ValueError(
                        "Termination flags must be explicit booleans"
                    )
        if not events or events[-1].get("kind") != "termination":
            raise ValueError("Evidence requires an explicit terminal outcome")
        canonical(events)  # Reject NaN/Infinity even in caller-created values.

    @property
    def events(self):
        return json.loads(self.events_json)

    def to_dict(self):
        return {"version": self.version, "events": self.events}

    @classmethod
    def from_dict(cls, value):
        if set(value) != {"version", "events"}:
            raise ValueError("Disallowed trajectory envelope fields")
        return cls(canonical(value["events"]), value["version"])


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    trajectory: SanitizedTrajectory
    redactions_json: str
    source_sha256: str

    @property
    def redactions(self):
        return json.loads(self.redactions_json)


def sanitize(records, *, hidden_values=(), result=None, execution=None):
    """Strip forbidden events/fields and linked results, preserving order.

    A disallowed command removes the entire associated result, including
    content with no path marker. Known canary/secret values can be supplied by
    the trusted boundary; matching fields are removed wholesale.
    """
    records = list(records)
    if any(not isinstance(e, dict) for e in records):
        raise ValueError("Trace events must be objects")
    if any(
        e.get("kind") == "observation" and "visible_result" in e
        for e in records
    ):
        raise ValueError(
            "Projected observations require a separate evidence version"
        )
    source = canonical(records)
    outcome = termination(records, result=result, execution=execution)
    logs, output, tainted_steps = [], [], set()
    hidden = tuple(x for x in hidden_values if x)

    def log(index, field, value, reason):
        logs.append(
            {
                "event": index,
                "field": field,
                "reason": reason,
                "sha256": digest(canonical(value)),
            }
        )

    def unsafe(value):
        return isinstance(value, str) and (
            forbidden(value) or any(x in value for x in hidden)
        )

    for i, event in enumerate(records):
        if not isinstance(event, dict):
            raise ValueError("Trace events must be objects")
        if event.get("kind") == "assistant" and unsafe(event.get("text")):
            if "step" in event:
                tainted_steps.add(event["step"])
        if event.get("kind") == "observation" and unsafe(event.get("command")):
            if "step" in event:
                tainted_steps.add(event["step"])
    for i, event in enumerate(records):
        kind = event.get("kind")
        if kind == "termination":
            log(i, "*", event, "termination_normalized")
            continue
        if kind not in ALLOWED or (
            kind == "artifact" and unsafe(event.get("path"))
        ):
            log(i, "*", event, "disallowed_event")
            output.append({"kind": "redacted"})
            continue
        if kind in {"assistant", "observation"} and (
            event.get("step") in tainted_steps
            or unsafe(event.get("command"))
            or (kind == "assistant" and unsafe(event.get("text")))
        ):
            log(i, "*", event, "hidden_access_and_paired_result")
            output.append(
                {
                    "kind": "redacted",
                    **{k: event[k] for k in ("step",) if k in event},
                }
            )
            continue
        clean = {"kind": kind}
        for key, value in event.items():
            if key == "kind":
                continue
            if key not in ALLOWED[kind] or FIELD_PATTERN.search(key):
                log(i, key, value, "disallowed_field")
            elif kind != "instruction" and unsafe(value):
                clean[key] = REDACTED
                log(i, key, value, "hidden_content")
            else:
                clean[key] = value
        output.append(clean)
    output.append(outcome)
    return SanitizationResult(
        SanitizedTrajectory(canonical(output)), canonical(logs), digest(source)
    )


def sanitize_file(source: Path, destination: Path, *, hidden_values=()):
    """Write a new export; refuse to overwrite the input, including aliases."""
    if source.resolve() == destination.resolve() or (
        destination.exists() and source.samefile(destination)
    ):
        raise ValueError("Raw trace is read-only")
    raw = source.read_bytes()
    result = sanitize(
        [json.loads(line) for line in raw.splitlines() if line.strip()],
        hidden_values=hidden_values,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        canonical(
            {
                "trajectory": result.trajectory.to_dict(),
                "redactions": result.redactions,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "sanitizer_version": VERSION,
            }
        )
    )
    return result


@dataclass(frozen=True, slots=True)
class JudgeInput:
    task_text: str
    trajectory: SanitizedTrajectory

    def __post_init__(self):
        if type(self.task_text) is not str or not self.task_text.strip():
            raise ValueError("Task text must be a nonempty string")
        if type(self.trajectory) is not SanitizedTrajectory:
            raise TypeError("Only SanitizedTrajectory is accepted")

    @classmethod
    def from_dict(cls, data):
        if set(data) != {"task_text", "trajectory"}:
            raise ValueError("Only task_text and trajectory are permitted")
        return cls(
            data["task_text"],
            SanitizedTrajectory.from_dict(data["trajectory"]),
        )

    def to_dict(self):
        return {
            "task_text": self.task_text,
            "trajectory": self.trajectory.to_dict(),
        }


def export_trace(
    trace_path, output, *, observation_chars=None, result=None, execution=None
):
    """Export complete sanitized observations and trusted terminal fields.

    Result/execution records are controller-only inputs: only the termination
    allowlist is exported. No verifier diagnostics, rewards, or paths enter it.
    """
    if observation_chars is not None:
        raise ValueError("Evidence v2 requires complete observations; no cap")
    raw = Path(trace_path).read_bytes()
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    full = sanitize(records, result=result, execution=execution)
    instructions = [
        e["text"] for e in full.trajectory.events if e["kind"] == "instruction"
    ]
    if len(instructions) != 1:
        raise ValueError("Exactly one task instruction is required")
    evidence = JudgeInput(instructions[0], full.trajectory)
    documents = {
        name: canonical(full.trajectory.to_dict())
        for name in ("sanitized-full.json", "sanitized.json")
    }
    documents["redactions.json"] = canonical(
        {
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "redactions": full.redactions,
            "evidence_version": VERSION,
            "observation_policy": "complete_sanitized_observations",
            "sanitized_sha256": digest(full.trajectory.events_json),
        }
    )
    output = Path(output)
    # Check all files before writing any: a rejected frozen-input change must
    # not replace the original evidence or redaction provenance on disk.
    for name, content in documents.items():
        path = output / name
        if path.exists() and path.read_text() != content:
            raise ValueError("Frozen trace export conflict; use a new version")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in documents.items():
        path = output / name
        if not path.exists():
            path.write_text(content)
    return evidence
