"""Resolved experimental conditions and fail-closed, unpaid entry checks."""

import hashlib
import json
import math
import platform
import sqlite3
import statistics
import tomllib
from importlib.metadata import version
from pathlib import Path

from evolution.candidates import atomic_json, safe_id
from evolution.judges import PROMPT_VERSION, PROMPTS
from evolution.prompts import render
from evolution.sanitize import VERSION as EVIDENCE_VERSION
from evolution.sanitize import canonical, digest
from evolution.workspace import IMAGE, docker

PENDING = ["AD1", "AD7", "AD10", "AD11", "AD12", "AD13", "AD14"]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def defaults(experiment, *, validation=False):
    """Proposals are defaults, never assertions of ratification."""
    return {
        "schema_version": 1,
        "experiment": safe_id(experiment),
        "purpose": "infrastructure" if validation else "pilot",
        "prereg_frozen": False,
        "ratifications": {k: False for k in PENDING},
        "task_model": {
            "name": "gpt-5.6-terra",
            "deployment": "gpt56terra",
            "endpoint_prefix": "TASK_ALT2",
            "reasoning_effort": "low",
            "completion_allowance": 8192,
            "tool_protocol": "json",
            "max_calls": 24,
            "api_timeout_s": 180,
        },
        "tool_failure_reading": "executor",
        "api_timeout_policy": "infrastructure",
        "tau": {
            "A0": 0.0,
            "A1": 0.0 if validation else None,
            "A2": None,
            "A3-loop": None,
        },
        "tau_evidence": {},
        "epsilon": 0.0,
        "acceptance": "anchor" if validation else "improve",
        "arms": ["A0", "A1"]
        if validation
        else [
            "A0",
            "A1",
            "A2",
            "A3-loop",
            "C-TTS-A0",
            "C-TTS-A1",
            "C-TTS-A2",
            "C-TTS-A3-loop",
        ],
        "a3_loop_hook": None,
        "seeds": [1] if validation else [1, 2],
        "T": 1 if validation else 6,
        "candidates_per_arm": {"A0": 2, "A1": 2, "A2": 2, "A3-loop": 3},
        "partition_limits": {"search": 6, "anchor": 3, "sealed": 3}
        if validation
        else {},
        "evidence_version": EVIDENCE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "budget": {
            "estimate_usd": 15 if validation else 400,
            "guard_usd": 15 if validation else 600,
            "wall_clock_hours": 3.5 if validation else 120,
            "rollout_usd": 1,
            "evolver_session_usd": 5,
            "unit_rollout_estimate_usd": 0.112,
        },
        "concurrency": 4,
        "cache_policy": "unique_user_metadata",
        "cache_partition_attestation": None,
        "entry_evidence": {},
        "section5_artifact": None,
    }


def validate(value):
    safe_id(value["experiment"])
    if not value["arms"] or not value["seeds"]:
        raise ValueError("At least one arm and seed are required")
    if value["acceptance"] not in {"anchor", "improve"}:
        raise ValueError("Unknown acceptance rule")
    if (
        type(value["epsilon"]) not in (int, float)
        or not math.isfinite(value["epsilon"])
        or value["epsilon"] < 0
    ):
        raise ValueError("Invalid epsilon")
    if value["schema_version"] != 1 or value["purpose"] not in {
        "pilot",
        "infrastructure",
    }:
        raise ValueError("Unsupported manifest")
    task = value["task_model"]
    if task["tool_protocol"] != "json":
        raise ValueError("Only the implemented JSON protocol is admitted")
    if value["tool_failure_reading"] not in {"executor", "strict"}:
        raise ValueError("Unknown tool failure reading")
    if value["api_timeout_policy"] not in {"infrastructure", "failure"}:
        raise ValueError("Unknown API timeout policy")
    for n in [
        value["T"],
        value["concurrency"],
        task["completion_allowance"],
        task["max_calls"],
        *value["seeds"],
        *value["candidates_per_arm"].values(),
    ]:
        if type(n) is not int or n <= 0:
            raise ValueError("Counts must be positive integers")
    if value["concurrency"] != 4 or task["max_calls"] != 24:
        raise ValueError("Harbor concurrency must be 4 and model-call cap 24")
    if len(set(value["seeds"])) != len(value["seeds"]) or len(
        set(value["arms"])
    ) != len(value["arms"]):
        raise ValueError("Duplicate arm or seed")
    allowed = {"A0", "A1", "A2", "A4", "A3-loop"}
    for arm in value["arms"]:
        if arm.removeprefix("C-TTS-") not in allowed:
            raise ValueError("Unknown arm")
        if arm.startswith("C-TTS-") and arm[6:] not in value["arms"]:
            raise ValueError("Control requires its own signal comparator")
    for n in value["budget"].values():
        if type(n) not in (int, float) or not math.isfinite(n) or n <= 0:
            raise ValueError("Invalid budget")
    if value["evidence_version"] != EVIDENCE_VERSION:
        raise ValueError("Evidence condition changed")
    for tau in value["tau"].values():
        if tau is not None and (not math.isfinite(tau) or tau < 0):
            raise ValueError("Invalid tau")


def resolve(root, value, config):
    """No credentials enter this manifest."""
    root = Path(root)
    value = json.loads(json.dumps(value))
    for field in (
        "resolved_sha256",
        "hashes",
        "providers",
        "tasks",
        "split_sha256",
        "runtime_images",
        "task_images",
        "sampling_seed_support",
        "host_runtime",
    ):
        value.pop(field, None)
    validate(value)
    split_path = root / "data/tb2_split.json"
    split = json.loads(split_path.read_text())
    value["split_sha256"] = file_hash(split_path)
    value["tasks"] = {
        k: [t["name"] for t in tasks][: value["partition_limits"].get(k)]
        for k, tasks in split["splits"].items()
    }
    task = value["task_model"]
    providers = {}
    for role, prefix in [
        ("task", task["endpoint_prefix"]),
        ("evolver", "EVOLVER"),
        ("judge", "JUDGE"),
    ]:
        base = config[f"{prefix}_API_BASE"].rstrip("/")
        if not base.endswith("/v1"):
            base += "/openai/v1"
        providers[role] = {
            "base_url": base,
            "deployment": task["deployment"]
            if role == "task"
            else config[f"{prefix}_MODEL"],
            "rpm": float(config.get(f"{prefix}_RPM", 250)),
            "tpm": float(config.get(f"{prefix}_TPM", 250000)),
        }
    providers["task"].update(
        params={
            "reasoning_effort": task["reasoning_effort"],
            "max_completion_tokens": task["completion_allowance"],
        },
        timeout_s=task["api_timeout_s"],
    )
    providers["evolver"].update(
        params={
            "temperature": 0.6,
            "top_p": 0.95,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 4096,
        },
        timeout_s=180,
    )
    providers["judge"].update(
        params={
            "temperature": 0.6,
            "top_p": 0.95,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": int(
                config.get("JUDGE_MAX_COMPLETION_TOKENS", 2048)
            ),
        },
        timeout_s=float(config.get("JUDGE_TIMEOUT_S", 180)),
    )
    value["providers"] = providers
    value["hashes"] = {
        "prereg": file_hash(root / "PREREG.md"),
        "seed": file_hash(root / "harness/seed.py"),
        "prices": file_hash(root / "costs/judges_prices.json"),
        "dependencies": file_hash(root / "uv.lock"),
        "harness_runtime": digest(
            canonical(
                {
                    str(p.relative_to(root)): file_hash(p)
                    for p in sorted((root / "harness").rglob("*.py"))
                }
            )
        ),
        "judge_prompts": digest(canonical(PROMPTS)),
        "evolver_prompts": digest(
            canonical(
                {
                    a: render(a, task["completion_allowance"])
                    for a in ("A0", "A1", "A2", "A4")
                }
            )
        ),
        "evidence_contract": file_hash(root / "evolution/sanitize.py"),
        "controller": digest(
            canonical(
                {
                    str(p.relative_to(root)): file_hash(p)
                    for p in sorted((root / "evolution").rglob("*.py"))
                }
            )
        ),
        "launchers": digest(
            canonical(
                {
                    name: file_hash(root / "scripts" / name)
                    for name in ("run_evolution.py", "run_pilot.py")
                }
            )
        ),
    }
    value["runtime_images"] = {
        IMAGE: docker(
            "image", "inspect", IMAGE, "--format", "{{.Id}}"
        ).stdout.strip(),
    }
    value["host_runtime"] = {
        "python": platform.python_version(),
        "harbor": version("harbor"),
        "httpx": version("httpx"),
    }
    # Pin cached dataset task descriptors and the actual local image IDs.
    value["task_images"] = {}
    cache = Path.home() / ".cache/harbor/tasks"
    for task_name in sorted(
        {t for group in value["tasks"].values() for t in group}
    ):
        descriptors = sorted(cache.glob(f"*/{task_name}/task.toml"))
        descriptors = [
            p
            for p in descriptors
            if file_hash(p)
            == next(
                t["task_toml_sha256"]
                for group in split["splits"].values()
                for t in group
                if t["name"] == task_name
            )
        ]
        if not descriptors:
            raise ValueError(
                f"Pinned task descriptor is not cached: {task_name}"
            )
        descriptor = descriptors[0]
        env = tomllib.loads(descriptor.read_text())["environment"]
        image = env.get("docker_image")
        if not image:
            raise ValueError(
                f"Task image must be pinned before dispatch: {task_name}"
            )
        image_id = docker(
            "image", "inspect", image, "--format", "{{.Id}}"
        ).stdout.strip()
        value["task_images"][task_name] = {
            "image": image,
            "digest": image_id,
            "task_toml_sha256": file_hash(descriptor),
        }
    value["sampling_seed_support"] = (
        "unsupported; local deterministic identities only"
    )
    value["resolved_sha256"] = digest(canonical(value))
    return value


def freeze_manifest(root, value):
    path = Path(root) / "runs" / value["experiment"] / "manifest.json"
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(
                "Frozen experiment condition mismatch; refusing resume"
            )
    else:
        atomic_json(path, value)
    return path


def budget_errors(root, manifest):
    """An existing database cannot silently substitute a looser budget."""
    experiment = manifest.get("budget_experiment", manifest["experiment"])
    path = Path(root) / "costs" / experiment / "budget.sqlite"
    if not path.is_file():
        return ["Budget guard is not armed"]
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT started,deadline,estimate,ceiling "
                "FROM phase WHERE id=1"
            ).fetchone()
        budget = manifest["budget"]
        matches = row and all(
            math.isclose(actual, expected, abs_tol=0.01)
            for actual, expected in (
                (row[2], budget["estimate_usd"]),
                (row[3], budget["guard_usd"]),
                (row[1] - row[0], budget["wall_clock_hours"] * 3600),
            )
        )
    except sqlite3.Error:
        matches = False
    return [] if matches else ["Budget guard differs from frozen manifest"]


def valid_cache_attestation(root, manifest):
    """Require content-addressed provider evidence, never a truthy flag."""
    item = manifest.get("cache_partition_attestation")
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        return False
    path = Path(root) / item["path"]
    if not path.is_file() or file_hash(path) != item.get("sha256"):
        return False
    try:
        evidence = json.loads(path.read_text())
        return (
            evidence["kind"] == "provider_enforced_cache_partition"
            and evidence["enforced"] is True
            and evidence["providers"] == manifest["providers"]
            and evidence["policy"] == manifest["cache_policy"]
            and set(evidence["isolated_dimensions"])
            >= {"experiment", "seed", "arm", "role"}
            and bool(evidence["provider_evidence"])
            and all(
                (Path(root) / proof["path"]).is_file()
                and file_hash(Path(root) / proof["path"]) == proof["sha256"]
                for proof in evidence["provider_evidence"]
            )
        )
    except (KeyError, TypeError, ValueError):
        return False


def entry_errors(root, manifest):
    """Checks run before constructing a paid backend or evaluating t=0."""
    root = Path(root)
    errors = []
    if (
        not manifest.get("prereg_frozen")
        or "must not be treated as frozen" in (root / "PREREG.md").read_text()
    ):
        errors.append("PREREG is not frozen")
    for key in PENDING:
        if manifest.get("ratifications", {}).get(key) is not True:
            errors.append(f"{key} remains unratified")
    unsigned = {k: v for k, v in manifest.items() if k != "resolved_sha256"}
    if digest(canonical(unsigned)) != manifest.get("resolved_sha256"):
        errors.append("Manifest hash mismatch")
    if manifest.get("split_sha256") != file_hash(root / "data/tb2_split.json"):
        errors.append("Split hash mismatch")
    for signal in sorted({a.removeprefix("C-TTS-") for a in manifest["arms"]}):
        if signal != "A0":
            item = manifest["tau_evidence"].get(signal)
            valid_tau = False
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                path = root / item["path"]
                if path.is_file() and file_hash(path) == item.get("sha256"):
                    try:
                        data = json.loads(path.read_text())
                        scores = data["aggregate_scores"]
                        valid_tau = (
                            data["evidence_version"] == EVIDENCE_VERSION
                            and data["judge"] == signal
                            and len(scores) == 5
                            and all(
                                type(x) in (int, float) and math.isfinite(x)
                                for x in scores
                            )
                            and manifest["tau"].get(signal) is not None
                            and math.isclose(
                                statistics.stdev(scores),
                                manifest["tau"][signal],
                                abs_tol=1e-12,
                            )
                            and data["prompt_sha256"]
                            == manifest["hashes"]["judge_prompts"]
                            and data["provider"]
                            == manifest["providers"]["judge"]
                            and data["task_provider"]
                            == manifest["providers"]["task"]
                            and data["seed_sha256"]
                            == manifest["hashes"]["seed"]
                            and data["split_sha256"]
                            == manifest["split_sha256"]
                        )
                    except (KeyError, TypeError, ValueError):
                        valid_tau = False
            if not valid_tau:
                errors.append(f"{signal} lacks valid frozen v2 tau evidence")
        if signal == "A3-loop":
            errors.append("Reserved A3-loop hook is not implemented")
    matrix = manifest.get("section5_artifact")
    if not matrix or not (root / matrix).is_file():
        errors.append("Section 5 artifact missing")
    else:
        from evolution.isolation import verify_matrix

        entry_matrix = json.loads((root / matrix).read_text())
        errors.extend(verify_matrix(root, entry_matrix))
        prior_manifest = (
            root
            / "runs"
            / entry_matrix.get("experiment", "missing")
            / "manifest.json"
        )
        if not prior_manifest.is_file() or file_hash(
            prior_manifest
        ) != entry_matrix.get("manifest_sha256"):
            errors.append("Section 5 validation manifest missing or changed")
        else:
            prior = json.loads(prior_manifest.read_text())
            keys = (
                "seed",
                "judge_prompts",
                "evolver_prompts",
                "evidence_contract",
                "controller",
                "launchers",
                "dependencies",
                "harness_runtime",
            )
            if any(
                prior.get("hashes", {}).get(k) != manifest["hashes"].get(k)
                for k in keys
            ):
                errors.append(
                    "Section 5 evidence is for a different "
                    "code/prompt condition"
                )
    for gate in ["P1.8", "P1.9", "P1.11", "P1.5"]:
        item = manifest.get("entry_evidence", {}).get(gate)
        if (
            not item
            or not (root / item["path"]).is_file()
            or file_hash(root / item["path"]) != item["sha256"]
        ):
            errors.append(f"{gate} evidence missing or changed")
    errors.extend(budget_errors(root, manifest))
    ordinary = [
        a
        for a in manifest["arms"]
        if not a.startswith("C-TTS-") and a != "A3-loop"
    ]
    search = len(manifest.get("tasks", {}).get("search", []))
    sealed = len(manifest.get("tasks", {}).get("sealed", []))
    anchor = len(manifest.get("tasks", {}).get("anchor", []))
    nominal = 0
    for arm in ordinary:
        candidates = manifest["candidates_per_arm"].get(arm, 2)
        per_iteration = search * (6 + candidates) + candidates + 2 * sealed
        if manifest["acceptance"] == "anchor":
            per_iteration += 2 * anchor
        count = 2 * sealed + manifest["T"] * per_iteration
        nominal += count * (2 if f"C-TTS-{arm}" in manifest["arms"] else 1)
    nominal *= len(manifest["seeds"])
    projected_solver_cost = (
        nominal * manifest["budget"]["unit_rollout_estimate_usd"]
    )
    if projected_solver_cost > manifest["budget"]["estimate_usd"]:
        errors.append(
            f"Nominal solver schedule ({nominal} rollouts) exceeds "
            "budget estimate before judges/evolvers"
        )
    if not valid_cache_attestation(root, manifest):
        errors.append("Provider cache partition is unverified")
    return errors
