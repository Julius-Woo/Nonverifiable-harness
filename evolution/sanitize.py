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

VERSION = "sanitized-trajectory-v1"
REDACTED = "[REDACTED]"
PATH_PATTERN = re.compile(
    r"(?i)(?:^|[/\\\s\"'=:;(`])(?:"
    r"tests?[/\\]|test_[\w.-]+\.py\b|"
    r"eval(?:[/\\]|\.py\b)|evaluator(?:\.py)?\b|"
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
    "assistant": {"text", "step", "ok"},
    "observation": {
        "step",
        "command",
        "stdout",
        "stderr",
        "return_code",
        "error",
        "protocol_error",
        "wall_s",
        "visible_result",
    },
    "finish": {"answer"},
    "error": {"text", "step", "error"},
    "termination": {"status"},
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
                if isinstance(value, str) and forbidden(value):
                    raise ValueError("Evidence must be sanitized")
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


def sanitize(records, *, hidden_values=()):
    """Strip forbidden events/fields and linked results, preserving order.

    A disallowed command removes the entire associated result, including
    content with no path marker. Known canary/secret values can be supplied by
    the trusted boundary; matching fields are removed wholesale.
    """
    records = list(records)
    source = canonical(records)
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
            elif unsafe(value):
                clean[key] = REDACTED
                log(i, key, value, "hidden_content")
            else:
                clean[key] = value
        output.append(clean)
    return SanitizationResult(
        SanitizedTrajectory(canonical(output)), canonical(logs), digest(source)
    )


def solver_visible(records, observation_chars=12000):
    """Reproduce run_seed history visibility, retaining every decision/event.

    Call only for traces generated with this known seed visibility limit.
    Full raw/sanitized observation archives remain separate.
    """
    if observation_chars <= 0:
        raise ValueError("observation_chars must be positive")
    result, projection = [], []
    for i, event in enumerate(records):
        event = dict(event)
        if event.get("kind") == "observation":
            observation = {
                k: v
                for k, v in event.items()
                if k not in {"kind", "step", "wall_s"}
            }
            visible = json.dumps(observation)
            if len(visible) > observation_chars:
                projection.append(
                    {
                        "event": i,
                        "source_chars": len(visible),
                        "visible_chars": observation_chars,
                        "sha256": digest(visible),
                        "reason": "seed_observation_visibility",
                    }
                )
                # Retain command and execution status for A2; never fabricate
                # missing output past the boundary visible to the solver.
                event = {
                    k: v
                    for k, v in event.items()
                    if k not in {"stdout", "stderr"}
                }
                event["visible_result"] = (
                    visible[:observation_chars] + "\n[observation truncated]"
                )
        result.append(event)
    return result, projection


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
