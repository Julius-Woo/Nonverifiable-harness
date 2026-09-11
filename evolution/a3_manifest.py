"""A3 v3 manifest fields over the unchanged common manifest/entry gates.

The common module still freezes A3's historical v2 recipe. Project only those
legacy fields for its structural validation, then restore and hash the actual
v3 condition before freezing or dispatch. Never relabel calibration evidence.
"""

import copy
import json
import math
import statistics
from pathlib import Path

from evolution import manifest as common
from evolution.sanitize import VERSION, canonical, digest


def has_a3(value):
    return any(
        a.removeprefix("C-TTS-").startswith("A3-")
        for a in value.get("arms", [])
    )


def defaults(experiment, *, validation=False):
    value = common.defaults(experiment, validation=validation)
    value["a3"]["evidence_version"] = VERSION
    value["a3_native_budget"] = {
        "estimate_usd": 50,
        "guard_usd": 60,
        "wall_clock_hours": 4,
        "status": "pending_authorization",
    }
    return value


def require_v3(value):
    if (
        value.get("evidence_version") != VERSION
        or value.get("a3", {}).get("evidence_version") != VERSION
    ):
        raise ValueError(
            "Prospective A3 requires evidence v3; v2 is replay-only"
        )


def legacy_projection(value):
    result = copy.deepcopy(value)
    if has_a3(value):
        require_v3(value)
        result["a3"]["evidence_version"] = "sanitized-trajectory-v2"
        if result["arms"] == ["A3-native"]:
            result["evidence_version"] = "sanitized-trajectory-v2"
    return result


def validate(value):
    common.validate(legacy_projection(value))
    budget = value.get("a3_native_budget")
    if budget is not None:
        for key in ("estimate_usd", "guard_usd", "wall_clock_hours"):
            amount = budget.get(key)
            if (
                type(amount) not in (int, float)
                or not math.isfinite(amount)
                or amount <= 0
            ):
                raise ValueError("Invalid A3-native budget")
        if budget["estimate_usd"] > budget["guard_usd"]:
            raise ValueError("A3-native estimate exceeds its guard")


def resolve(root, value, config):
    validate(value)
    resolved = common.resolve(root, legacy_projection(value), config)
    if has_a3(value):
        resolved["a3"] = copy.deepcopy(value["a3"])
        resolved["evidence_version"] = VERSION
        contract = common.file_hash(Path(root) / "evolution/sanitize.py")
        resolved["hashes"]["a3_evidence_contract"] = contract
        resolved["hashes"]["evidence_contract"] = contract
        resolved.pop("resolved_sha256", None)
        resolved["resolved_sha256"] = digest(canonical(resolved))
    return resolved


def valid_a3_tau(root, value):
    """Match the common calibration conditions using the actual v3 version."""
    try:
        item = value["tau_evidence"]["A3-loop"]
        path = Path(root) / item["path"]
        if common.file_hash(path) != item["sha256"]:
            return False
        data = json.loads(path.read_text())
        scores = data["aggregate_scores"]
        return (
            data["evidence_version"] == VERSION
            and data["judge"] == "A3-loop"
            and len(scores) == 5
            and all(
                type(x) in (int, float) and math.isfinite(x) for x in scores
            )
            and math.isclose(
                statistics.stdev(scores),
                value["tau"]["A3-loop"],
                abs_tol=1e-12,
            )
            and data["prompt_sha256"] == value["hashes"]["a3_prompts"]
            and data["provider"] == value["providers"]["task"]
            and data["task_provider"] == value["providers"]["task"]
            and data["seed_sha256"] == value["hashes"]["seed"]
            and data["split_sha256"] == value["split_sha256"]
            and data["a3_recipe"] == value["a3"]
            and data["embedding_provider"]
            == value["providers"].get("embedding")
        )
    except (OSError, KeyError, TypeError, ValueError):
        return False


def entry_errors(root, value):
    errors = common.entry_errors(root, value)
    if has_a3(value):
        try:
            require_v3(value)
        except ValueError as exc:
            errors.append(str(exc))
    if "A3-loop" in value["arms"]:
        legacy_error = "A3-loop lacks valid frozen v2 tau evidence"
        errors = [error for error in errors if error != legacy_error]
        if not valid_a3_tau(root, value):
            errors.append("A3-loop lacks valid frozen v3 tau evidence")
    return errors
