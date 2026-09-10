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

VERSION = "v3"
DEFAULT_OBSERVATION_CHARS = 4_000
DEFAULT_TRAJECTORY_CHARS = 200_000
OBSERVATION_FIELDS = ("command", "stdout", "stderr", "error", "protocol_error")


def cap_parameters(observation_chars, trajectory_chars):
    if type(observation_chars) is not int or observation_chars < 0:
        raise ValueError("observation_chars must be a nonnegative integer")
    if type(trajectory_chars) is not int or trajectory_chars <= 0:
        raise ValueError("trajectory_chars must be a positive integer")
    return {
        "observation_head_chars": observation_chars,
        "observation_tail_chars": observation_chars,
        "trajectory_chars": trajectory_chars,
    }


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
    truncations_json: str = "[]"
    observation_chars: int = DEFAULT_OBSERVATION_CHARS
    trajectory_chars: int = DEFAULT_TRAJECTORY_CHARS

    def __post_init__(self):
        if self.version != VERSION:
            raise ValueError("Unsupported sanitizer version")
        cap_parameters(self.observation_chars, self.trajectory_chars)
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
        truncations = self.truncations
        if not isinstance(truncations, list):
            raise ValueError("truncations must be a list")
        observations = [e for e in events if e["kind"] == "observation"]
        seen = set()
        for row in truncations:
            if not isinstance(row, dict) or set(row) != {
                "observation_index",
                "original_bytes",
                "kept_bytes",
            }:
                raise ValueError("Invalid truncation record")
            index = row["observation_index"]
            if (
                any(type(v) is not int for v in row.values())
                or not (
                    0 <= index < len(observations)
                    and 0 <= row["kept_bytes"] < row["original_bytes"]
                )
                or index in seen
            ):
                raise ValueError("Invalid truncation counts or index")
            seen.add(index)
            texts = [
                observations[index].get(k, "") for k in OBSERVATION_FIELDS
            ]
            texts = [text for text in texts if isinstance(text, str)]
            marker = re.compile(r"\[\.\.\. omitted [1-9][0-9]* bytes \.\.\.\]")
            if not any(marker.search(text) for text in texts):
                raise ValueError(
                    "Truncation requires a visible elision marker"
                )
            # A caller cannot evade the head/tail cap by attaching a record
            # to a large untrimmed observation. Generated markers add at most
            # one marker per text field; source text may also contain markers.
            marker_bound = len(texts) * len(
                f"[... omitted {row['original_bytes']} bytes ...]"
            )
            if (
                sum(len(text) for text in texts)
                > 2 * self.observation_chars + marker_bound
                or row["kept_bytes"] > 8 * self.observation_chars
                or row["kept_bytes"]
                > sum(len(text.encode()) for text in texts)
            ):
                raise ValueError("Truncated observation exceeds v3 cap")
        for index, event in enumerate(observations):
            size = sum(
                len(event.get(k, ""))
                for k in OBSERVATION_FIELDS
                if isinstance(event.get(k, ""), str)
            )
            if size > 2 * self.observation_chars and index not in seen:
                raise ValueError(
                    "Observation exceeds v3 cap without provenance"
                )
        if len(canonical(self.to_dict())) > self.trajectory_chars:
            raise ValueError("Sanitized evidence exceeds v3 trajectory cap")

    @property
    def events(self):
        return json.loads(self.events_json)

    @property
    def truncations(self):
        return json.loads(self.truncations_json)

    def to_dict(self):
        return {
            "version": self.version,
            "events": self.events,
            "truncations": self.truncations,
            "caps": cap_parameters(
                self.observation_chars, self.trajectory_chars
            ),
        }

    @classmethod
    def from_dict(cls, value):
        if value.get("version") != VERSION:
            raise ValueError("Unsupported sanitizer version")
        if set(value) != {"version", "events", "truncations", "caps"}:
            raise ValueError("Disallowed trajectory envelope fields")
        caps = value["caps"]
        if (
            set(caps)
            != {
                "observation_head_chars",
                "observation_tail_chars",
                "trajectory_chars",
            }
            or caps["observation_head_chars"] != caps["observation_tail_chars"]
        ):
            raise ValueError("Invalid v3 cap parameters")
        return cls(
            canonical(value["events"]),
            value["version"],
            canonical(value["truncations"]),
            caps["observation_head_chars"],
            caps["trajectory_chars"],
        )


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    trajectory: SanitizedTrajectory
    redactions_json: str
    source_sha256: str

    @property
    def redactions(self):
        return json.loads(self.redactions_json)


def cap_observations(
    events,
    *,
    observation_chars=DEFAULT_OBSERVATION_CHARS,
    trajectory_chars=DEFAULT_TRAJECTORY_CHARS,
):
    """Cap sanitized text, then the complete canonical trajectory envelope.

    Text fields form one stream in OBSERVATION_FIELDS order.
    Retained slices stay in their fields; each omitted segment gets a UTF-8
    byte marker. Provenance counts source text bytes, excluding markers/JSON.
    Indexes are zero-based among sanitized observations.
    Other events stay whole.
    """
    caps = cap_parameters(observation_chars, trajectory_chars)
    output = [dict(e) for e in events]
    observations = [e for e in output if e["kind"] == "observation"]
    originals = [dict(e) for e in observations]
    lengths = [
        sum(
            len(e.get(k, ""))
            for k in OBSERVATION_FIELDS
            if isinstance(e.get(k, ""), str)
        )
        for e in originals
    ]
    kept = [min(n, 2 * observation_chars) for n in lengths]
    records = {}

    def render(index, count):
        source, event = originals[index], observations[index]
        size = lengths[index]
        head, tail = (count + 1) // 2, count // 2
        offset, original_bytes, kept_bytes = 0, 0, 0
        for key in OBSERVATION_FIELDS:
            text = source.get(key)
            if not isinstance(text, str):
                continue
            original_bytes += len(text.encode())
            left = min(len(text), max(0, head - offset))
            right = min(
                len(text) - left, max(0, offset + len(text) - size + tail)
            )
            if count >= size:
                left, right = len(text), 0
            first, last = (
                text[:left],
                text[len(text) - right :] if right else "",
            )
            retained_bytes = len(first.encode()) + len(last.encode())
            omitted = len(text.encode()) - retained_bytes
            event[key] = (
                first + f"[... omitted {omitted} bytes ...]" + last
                if omitted
                else text
            )
            kept_bytes += retained_bytes
            offset += len(text)
        if count < size:
            records[index] = {
                "observation_index": index,
                "original_bytes": original_bytes,
                "kept_bytes": kept_bytes,
            }
        else:
            records.pop(index, None)
        kept[index] = count

    def envelope():
        return {
            "version": VERSION,
            "events": output,
            "caps": caps,
            "truncations": [records[i] for i in sorted(records)],
        }

    for index, count in enumerate(kept):
        render(index, count)
    # Removing a marker when an entire short field becomes retained can make
    # serialized size decrease. Search each monotone interval separately;
    # assuming one global monotone size would miss feasible cap boundaries.
    exhausted = set()
    while len(canonical(envelope())) > trajectory_chars:
        candidates = [
            i for i, count in enumerate(kept) if count and i not in exhausted
        ]
        if not candidates:
            raise ValueError(
                "V3 trajectory cap cannot fit non-observation "
                "evidence and elision metadata; increase "
                "trajectory_chars explicitly"
            )
        index = max(candidates, key=lambda i: (kept[i], -i))
        upper, size, offset = kept[index], lengths[index], 0
        boundaries = {0, upper + 1}
        for key in OBSERVATION_FIELDS:
            text = originals[index].get(key)
            if not isinstance(text, str):
                continue
            # ceil(count / 2) reaches the field end, or floor(count / 2)
            # reaches its start from the tail. A whole-observation boundary
            # also removes its cumulative truncation record.
            for point in (
                2 * (offset + len(text)) - 1,
                2 * (size - offset),
                size,
            ):
                if 0 <= point <= upper:
                    boundaries.add(point)
            offset += len(text)
        points = sorted(boundaries)
        best, smallest = None, None
        for left, stop in zip(points, points[1:]):
            render(index, left)
            length = len(canonical(envelope()))
            if smallest is None or (length, -left) < smallest[:2]:
                smallest = (length, -left, left)
            if length > trajectory_chars:
                continue
            low, high = left, stop - 1
            while low < high:
                middle = (low + high + 1) // 2
                render(index, middle)
                if len(canonical(envelope())) <= trajectory_chars:
                    low = middle
                else:
                    high = middle - 1
            best = low if best is None else max(best, low)
        render(index, best if best is not None else smallest[2])
        if best is None:
            exhausted.add(index)
    return SanitizedTrajectory.from_dict(envelope())


def sanitize(
    records,
    *,
    hidden_values=(),
    result=None,
    execution=None,
    observation_chars=DEFAULT_OBSERVATION_CHARS,
    trajectory_chars=DEFAULT_TRAJECTORY_CHARS,
):
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
        cap_observations(
            output,
            observation_chars=observation_chars,
            trajectory_chars=trajectory_chars,
        ),
        canonical(logs),
        digest(source),
    )


def sanitize_file(
    source: Path,
    destination: Path,
    *,
    hidden_values=(),
    observation_chars=DEFAULT_OBSERVATION_CHARS,
    trajectory_chars=DEFAULT_TRAJECTORY_CHARS,
):
    """Write a new export; refuse to overwrite the input, including aliases."""
    if source.resolve() == destination.resolve() or (
        destination.exists() and source.samefile(destination)
    ):
        raise ValueError("Raw trace is read-only")
    raw = source.read_bytes()
    result = sanitize(
        [json.loads(line) for line in raw.splitlines() if line.strip()],
        hidden_values=hidden_values,
        observation_chars=observation_chars,
        trajectory_chars=trajectory_chars,
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
