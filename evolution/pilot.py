"""Portable, content-addressed pilot inputs and read-only launch preflight."""

import json
import math
import re
import statistics
from pathlib import Path

from evolution.a3_manifest import validate
from evolution.isolation import verify_matrix
from evolution.judges import PROMPTS
from evolution.manifest import budget_errors, file_hash, prereg_is_frozen
from evolution.sanitize import canonical, digest

TAU = {"A1": 0.006022079, "A2": 0.017391640}


def reference(root, path):
    return {"path": path, "sha256": file_hash(Path(root) / path)}


def seal_inputs(root, value):
    """Freeze local inputs without Docker, credentials, or paid backends."""
    root = Path(root)
    value = json.loads(json.dumps(value))
    paths = [
        "PREREG.md",
        "data/tb2_split.json",
        "harness/seed.py",
        "uv.lock",
        "costs/judges_prices.json",
        "docs/decisions-260912.md",
        "docs/decisions-260910.md",
        "docs/decisions-260910-addendum.md",
        "scripts/run_pilot.py",
        "scripts/run_evolution.py",
    ]
    paths += [
        str(p.relative_to(root))
        for directory in ("evolution", "harness")
        for p in sorted((root / directory).rglob("*.py"))
    ]
    value["input_hashes"] = {
        p: file_hash(root / p) for p in sorted(set(paths))
    }
    value["split"] = reference(root, "data/tb2_split.json")
    value["split_sha256"] = value["split"]["sha256"]
    value.pop("manifest_sha256", None)
    value["manifest_sha256"] = digest(canonical(value))
    return value


def schedule(root, value):
    split = json.loads(
        (
            Path(root)
            / value.get("split", {}).get("path", "data/tb2_split.json")
        ).read_text()
    )
    sizes = {
        k: len(v[: value.get("partition_limits", {}).get(k)])
        for k, v in split.get("splits", {}).items()
    }
    search, sealed, anchor = (
        sizes.get(k, 0) for k in ("search", "sealed", "anchor")
    )
    arms = []
    for arm in value["arms"]:
        if arm.startswith("C-TTS-"):
            comparator = next(r for r in arms if r["arm"] == arm[6:])
            arms.append({**comparator, "arm": arm, "comparator": arm[6:]})
            continue
        n = value["candidates_per_arm"].get(arm, 2)
        initial = 2 * sealed
        per_iteration = search * (6 + n) + n + 2 * sealed
        if arm == "A3-loop":
            initial += 2 * search
            per_iteration = value["a3"]["k"] * (n + 3) + search + 2 * sealed
        if value["acceptance"] == "anchor":
            per_iteration += 2 * anchor
        arms.append(
            {
                "arm": arm,
                "t0_rollouts": initial,
                "rollouts_per_iteration": per_iteration,
                "rollouts_per_seed": initial + value["T"] * per_iteration,
            }
        )
    total = sum(a["rollouts_per_seed"] for a in arms) * len(value["seeds"])
    budget = value["budget"]
    return {
        "T": value["T"],
        "seeds": value["seeds"],
        "tasks": sizes,
        "arms": arms,
        "nominal_rollouts": total,
        "solver_projection_usd": round(
            total * budget["unit_rollout_estimate_usd"], 6
        ),
        "estimate_usd": budget["estimate_usd"],
        "guard_usd": budget["guard_usd"],
        "wall_clock_hours": budget["wall_clock_hours"],
        "projection_basis": "All proposals valid, fresh paired confirmation; "
        "excludes retries, judges, evolvers, cross-judge and A3 operators",
    }


def check_reference(root, item):
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        return False
    path = Path(root) / item["path"]
    return path.is_file() and file_hash(path) == item.get("sha256")


def tau_errors(root, value):
    errors = []
    for arm, tau in TAU.items():
        if arm not in value["arms"]:
            continue
        item = value.get("tau_evidence", {}).get(arm)
        try:
            if not check_reference(root, item):
                raise ValueError("missing or changed artifact")
            measured = json.loads((Path(root) / item["path"]).read_text())
            data = measured[f"JUDGE/{arm.lower()}"]
            means = data["search_repeat_means"]
            if not (
                len(means) == 5
                and all(
                    type(x) in (int, float) and math.isfinite(x) for x in means
                )
                and math.isclose(statistics.stdev(means), tau, abs_tol=5e-10)
                and value["tau"][arm] == tau
            ):
                raise ValueError("tau differs from ratified five-repeat SD")
            calibration = value["judge_calibration"]
            if not check_reference(root, calibration):
                raise ValueError("calibration manifest missing or changed")
            condition = json.loads(
                (Path(root) / calibration["path"]).read_text()
            )
            if (
                condition["evidence_version"] != value["evidence_version"]
                or condition["prompt_version"] != value["prompt_version"]
                or condition["split_sha256"] != value["split_sha256"]
                or condition["prompt_hashes"]
                != {k: digest(v) for k, v in PROMPTS.items()}
                or condition["caps"] != value.get("evidence_caps")
            ):
                raise ValueError("calibration condition differs")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            errors.append(f"{arm} tau evidence invalid: {exc}")
    return errors


def preflight(root, value):
    """Read-only checks without environment resolution, Docker, or network."""
    root, errors = Path(root), []
    try:
        validate(value)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"Manifest schema invalid: {exc}")
    frozen = prereg_is_frozen(root / "PREREG.md")
    if not frozen:
        errors.append("PREREG not frozen")
    errors.extend(ratified_settings_errors(value))
    prereg_text = (
        (root / "PREREG.md").read_text()
        if (root / "PREREG.md").exists()
        else ""
    )
    review_pending = bool(
        re.search(
            r"\|\s*Final review\s*\|[^\n]*pending R10", prereg_text, re.I
        )
    )
    if review_pending:
        errors.append("R10 final review pending")
    unsigned = {k: v for k, v in value.items() if k != "manifest_sha256"}
    if digest(canonical(unsigned)) != value.get("manifest_sha256"):
        errors.append("Manifest hash mismatch")
    inputs = value.get("input_hashes", {})
    required = {
        "PREREG.md",
        "data/tb2_split.json",
        "harness/seed.py",
        "uv.lock",
        "docs/decisions-260912.md",
        "scripts/run_pilot.py",
        "scripts/run_evolution.py",
    }
    required.update(
        str(p.relative_to(root)) for p in (root / "evolution").rglob("*.py")
    )
    if not required <= inputs.keys():
        errors.append("Manifest input hashes incomplete")
    for path, sha in inputs.items():
        if not check_reference(root, {"path": path, "sha256": sha}):
            errors.append(f"Frozen input missing or changed: {path}")
    if not check_reference(root, value.get("split")):
        errors.append("Split hash mismatch")
    for decision in (
        "AD1",
        "AD7",
        "AD10",
        "AD11",
        "AD12",
        "AD13",
        "AD14",
        "AD15",
    ):
        if value.get("ratifications", {}).get(decision) is not True:
            errors.append(f"{decision} remains unratified")
    if value.get("blinding") is not True:
        errors.append("Investigator blinding must be enabled")
    matrix = value.get("section5_evidence")
    if not check_reference(root, matrix):
        errors.append("Section 5 artifact missing or changed")
    else:
        try:
            data = json.loads((root / matrix["path"]).read_text())
            matrix_errors = verify_matrix(root, data)
            # Ratification explicitly accepts this one documented limitation.
            if value.get("cache_partition_limitation"):
                matrix_errors = [
                    e
                    for e in matrix_errors
                    if e != "Section 5 row 7 is not passed"
                ]
            errors.extend(matrix_errors)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"Section 5 artifact invalid: {exc}")
    for gate in ("P1.8", "P1.9", "P1.11"):
        if not check_reference(
            root, value.get("entry_evidence", {}).get(gate)
        ):
            errors.append(f"{gate} evidence missing or changed")
    errors.extend(tau_errors(root, value))
    errors.extend(budget_errors(root, value))
    if value.get("run_kind") != "qualification":
        item = value.get("qualification") or {}
        qualification_path = root / item.get(
            "manifest_path", "missing-qualification.json"
        )
        try:
            qualification = json.loads(qualification_path.read_text())
            unsigned_qualification = {
                k: v
                for k, v in qualification.items()
                if k != "manifest_sha256"
            }
            if (
                digest(canonical(unsigned_qualification))
                != item.get("manifest_sha256")
                or qualification.get("manifest_sha256")
                != item.get("manifest_sha256")
                or qualification.get("run_kind") != "qualification"
                or any(
                    qualification.get(k) != value.get(k)
                    for k in (
                        "input_hashes",
                        "task_model",
                        "arms",
                        "evidence_caps",
                        "split_sha256",
                        "pass_label",
                        "candidates_per_arm",
                    )
                )
            ):
                raise ValueError("qualification condition differs")
        except (OSError, ValueError, TypeError):
            errors.append("Qualification manifest missing or changed")
        path = root / item.get(
            "result_path", "logs/missing-qualification.json"
        )
        if not path.is_file():
            errors.append("Qualification has not passed")
        else:
            try:
                result = json.loads(path.read_text())
                if result.get("passed") is not True or result.get(
                    "manifest_sha256"
                ) != item.get("manifest_sha256"):
                    errors.append(
                        "Qualification result does not match frozen manifest"
                    )
            except (ValueError, TypeError):
                errors.append("Qualification result invalid")
    try:
        projected = schedule(root, value)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"Schedule unavailable: {exc}")
        projected = {}
    if (
        projected.get("solver_projection_usd", 0)
        > value["budget"]["guard_usd"]
    ):
        errors.append(
            "Nominal solver schedule exceeds budget guard before "
            "judges/evolvers; schedule reconciliation required"
        )
    return {
        "passed": not errors,
        "errors": errors,
        "paid_calls": 0,
        "docker_calls": 0,
        "prereg_frozen": frozen,
        "review_status": "pending R10"
        if review_pending
        else "not pending in PREREG",
        "schedule": projected,
        "limitations": [value["cache_partition_limitation"]]
        if value.get("cache_partition_limitation")
        else [],
    }


def ratified_manifest(root, experiment, *, qualification=False):
    """Build ratified inputs; PREREG freeze remains an external gate."""
    from evolution.a3_manifest import defaults

    value = defaults(experiment)
    value.pop("tool_failure_reading", None)
    value.pop("a3_native_budget", None)
    value.update(
        run_kind="qualification" if qualification else "pilot",
        prereg_frozen=prereg_is_frozen(Path(root) / "PREREG.md"),
        ratifications={f"AD{i}": True for i in range(1, 16)},
        blinding=True,
        memory_min_available_gib=6,
        claimed_without_ran_window_steps=5,
        evidence_caps={
            "observation_head_chars": 4000,
            "observation_tail_chars": 4000,
            "trajectory_chars": 200000,
        },
        content_policy_policy="task_failure_no_retry_no_exclusion",
        reestimate_rule={
            "after_sessions": 5,
            "action": "archive projection; halt if over guard",
            "retain_guard_and_deadline": True,
        },
        evolver_model={
            "name": "DeepSeek-V4-Pro",
            "endpoint_prefix": "EVOLVER",
            "completion_allowance": 8192,
        },
        judge_model={
            "name": "DeepSeek-V4-Flash",
            "endpoint_prefix": "JUDGE",
            "completion_allowance": 2048,
            "sampling": "temperature=0.6, top_p=0.95, JSON object",
        },
        cross_judge={
            "name": "Kimi-K2.6",
            "endpoint_prefix": "XJUDGE",
            "scorers": ["A1"],
            "completion_allowance": 8192,
            "purpose": "oracle-side diagnostic; never selection",
        },
        a4={
            "included": False,
            "weights": {"A1": 0.5, "A2": 0.5},
            "reason": "PREREG does not ratify tau_A4 = 0.5*(tau_A1 + tau_A2)",
        },
        cache_partition_limitation=(
            "Provider cache partition enforcement is unobservable through "
            "the API. Section 5 demonstrates six of seven rows live; "
            "ratification accepts row 7 as a documented limitation."
        ),
    )
    value["tau"].update(TAU)
    metrics = reference(root, "logs/judges/terra-8k-v3/metrics.json")
    value["tau_evidence"] = {a: metrics for a in TAU}
    value["judge_calibration"] = reference(
        root, "logs/judges/terra-8k-v3/manifest.json"
    )
    value["section5_artifact"] = (
        "runs/r8b-validation-260910-final/section5_matrix.json"
    )
    value["section5_evidence"] = reference(root, value["section5_artifact"])
    if qualification:
        value.update(T=1, seeds=[1])
        value["budget"].update(estimate_usd=30, guard_usd=30)
    return seal_inputs(root, value)


def ratified_settings_errors(value):
    """Reject metadata inconsistent with the implemented settings."""
    errors = []
    expected_task = {
        "name": "gpt-5.6-terra",
        "deployment": "gpt56terra",
        "endpoint_prefix": "TASK_ALT2",
        "reasoning_effort": "low",
        "completion_allowance": 8192,
        "tool_protocol": "json",
        "max_calls": 24,
        "command_timeout_s": 30,
    }
    for key, expected in expected_task.items():
        if value.get("task_model", {}).get(key) != expected:
            errors.append(f"Ratified task_model.{key} must be {expected}")
    for field, name, allowance in (
        ("evolver_model", "DeepSeek-V4-Pro", 8192),
        ("judge_model", "DeepSeek-V4-Flash", 2048),
        ("cross_judge", "Kimi-K2.6", 8192),
    ):
        model = value.get(field, {})
        if (
            model.get("name") != name
            or model.get("completion_allowance") != allowance
        ):
            errors.append(f"Ratified {field} settings differ")
    if value.get("cross_judge", {}).get("scorers") != ["A1"]:
        errors.append("Cross-judge must use A1 only")
    for key, expected in {
        "epsilon": 0,
        "claimed_without_ran_window_steps": 5,
        "memory_min_available_gib": 6,
        "content_policy_policy": "task_failure_no_retry_no_exclusion",
    }.items():
        if value.get(key) != expected:
            errors.append(f"Ratified {key} must be {expected}")
    if any(a.removeprefix("C-TTS-") == "A4" for a in value.get("arms", [])):
        errors.append("A4 is excluded by the frozen PREREG")
    for arm in ("A0", "A1", "A2"):
        if value.get("candidates_per_arm", {}).get(arm) != 2:
            errors.append(f"AD7 requires two {arm} candidates")
    if value.get("reestimate_rule", {}).get("after_sessions") != 5:
        errors.append("AD14 requires the first-five-session re-estimate")
    return errors
