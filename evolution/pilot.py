"""Portable, content-addressed pilot inputs and read-only launch preflight."""

import asyncio
import json
import math
import re
import statistics
from pathlib import Path

from evolution.a3 import A3Round
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


def preflight(root, value, *, allow_pending_review=False):
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
    if (
        "A3-loop" in value.get("arms", [])
        and value.get("partition_limits", {}).get("search", 10) < 10
        and not value.get("qualification_coreset")
    ):
        errors.append(
            "A3 search subset requires an authorized coreset adapter"
        )
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
    review_exception = (
        allow_pending_review
        and value.get("run_kind") == "qualification"
        and value.get("review_deviation", {}).get(
            "discard_on_blocking_finding"
        )
        is True
    )
    if review_pending and not review_exception:
        errors.append("R10 re-check pending")
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
        item = value.get("entry_evidence", {}).get(gate)
        valid = check_reference(root, item)
        if valid:
            try:
                bundle = json.loads((root / item["path"]).read_text())
                if bundle.get("kind") == "qualification_entry_evidence":
                    valid = bool(bundle.get("artifacts")) and all(
                        check_reference(root, artifact)
                        for artifact in bundle["artifacts"]
                    )
            except (ValueError, TypeError, AttributeError):
                valid = False
        if not valid:
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
        "review_status": "R10 re-check pending"
        if review_pending
        else "not pending in PREREG",
        "pending_review_allowed": bool(review_pending and review_exception),
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


def public_summary_errors(value):
    """Check serialized summaries, including non-standing sealed counters."""
    errors = []
    forbidden = {
        "O_t_sealed", "sealed_measurement", "sealed_measurements",
        "sealed_preference_status_counts", "baseline_anchor",
        "candidate_anchor", "oracle_raw",
    }

    def inspect(item):
        if isinstance(item, dict):
            for key in forbidden.intersection(item):
                errors.append(f"Private value in public report: {key}")
            if "arm" in item or "standing_metrics_partition" in item:
                for key in {"rollouts", "physical_trials"}.intersection(item):
                    errors.append(
                        f"All-partition count in public report: {key}"
                    )
            for child in item.values():
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)

    inspect(value)
    return errors


def public_artifact_errors(root, directory):
    """Reject private links and leaked fields in materialized reports."""
    import sqlite3

    root, directory = Path(root), Path(directory)
    private = (root / "oracle").resolve()
    secret = (root / ".env").resolve()
    errors = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            target = path.resolve()
            if (
                target == secret
                or target == private
                or private in target.parents
            ):
                errors.append(
                    "Public artifact links into private workspace: "
                    + str(path.relative_to(directory))
                )
        elif path.name in {
            "evolution_summary.jsonl", "public_report.json",
            "qualification_audit.json",
        }:
            values = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.suffix == ".jsonl"
                else [json.loads(path.read_text())]
            )
            for value in values:
                errors.extend(public_summary_errors(value))
        elif path.name == "state.sqlite":
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                if not db.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='stages'"
                ).fetchone():
                    continue
                for (value,) in db.execute(
                    "SELECT value FROM stages WHERE id LIKE 'finished-%'"
                ):
                    errors.extend(public_summary_errors(json.loads(value)))
    return errors


def qualification_reports(root, value, results):
    """Publish public aggregates and keep checkpoint contents oracle-side."""
    import sqlite3

    from evolution.accounting import cost_summary
    from evolution.candidates import atomic_json
    from harness.ledger import utc_now

    root = Path(root)
    experiment = value["experiment"]
    directory = root / "runs" / experiment
    accounting = root / "costs" / value.get("budget_experiment", experiment)
    costs = cost_summary(
        accounting / "ledger.jsonl", accounting / "requests.jsonl"
    )
    anomalies = public_artifact_errors(root, directory)
    for path in (root / "logs/evolution" / experiment).glob(
        "*/iteration-*.json"
    ):
        anomalies.extend(public_summary_errors(json.loads(path.read_text())))
    private_rows = 0
    for arm in value["arms"]:
        database = directory / arm / "state.sqlite"
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            for spec, result in db.execute(
                "SELECT spec,result FROM trials WHERE result IS NOT NULL"
            ):
                if json.loads(spec)["partition"] in {"anchor", "sealed"}:
                    private_rows += 1
                    if set(json.loads(result)) != {"private_ref"}:
                        anomalies.append(
                            f"{arm}: private trial stored publicly"
                        )
    sessions = list(
        (root / "logs/evolution" / experiment).glob(
            "*/sessions/*/validation.json"
        )
    )
    for path in sessions:
        session = json.loads(path.read_text())
        if session.get("canary_ok") is False:
            anomalies.append(
                f"Boundary canary failure: {path.relative_to(root)}"
            )
    anomalies.extend(public_summary_errors(results))
    from evolution.state import State

    grader_exclusions = 0
    for arm in value["arms"]:
        state = State(directory / arm / "state.sqlite")
        try:
            for stored in state.db.execute(
                "SELECT result FROM trials WHERE result IS NOT NULL"
            ):
                row = state.decode(stored[0])
                if row.get("exclusion_reason") == "grader_failure":
                    grader_exclusions += 1
                    if row.get("oracle") is not None or not row.get(
                        "excluded"
                    ):
                        anomalies.append(
                            f"{arm}: grader failure label anomaly"
                        )
                if row.get("execution", {}).get("reason") == "grader_failure":
                    anomalies.append(
                        f"{arm}: grader status entered solver termination"
                    )
        finally:
            state.close()
        for boundary in (root / "oracle" / experiment / arm / "grading").glob(
            "*/boundary.json"
        ):
            record = json.loads(boundary.read_text())
            if "snapshot_image" in record:
                anomalies.append(f"{arm}: filesystem snapshot grader used")
            runtime_path = boundary.parent / "runtime/runtime.json"
            if runtime_path.exists():
                runtime = json.loads(runtime_path.read_text())
                if (
                    runtime.get("canary_leaks")
                    or runtime.get("sensitive_service_fds")
                    or runtime.get("cleanup_errors")
                    or not runtime.get("released")
                ):
                    anomalies.append(f"{arm}: live runtime audit anomaly")
    audit = {
        "created_at": utc_now(),
        "blinding": value["blinding"],
        "pass_label": value["pass_label"],
        "private_trial_references_checked": private_rows,
        "all_partition_grader_exclusions": grader_exclusions,
        "evolver_sessions_checked": len(sessions),
        "isolation_or_pass_label_anomalies": sorted(set(anomalies)),
        "section5_evidence": value["section5_evidence"],
        "provider_cache_limitation": value.get("cache_partition_limitation"),
        "review_status": "pending R10; discard on isolation/label finding",
        "costs": costs,
        "manifest_sha256": value["manifest_sha256"],
    }
    atomic_json(
        root / "oracle" / experiment / "qualification_audit.json", audit
    )
    public_audit = {
        k: v
        for k, v in audit.items()
        if k
        not in {
            "private_trial_references_checked",
            "all_partition_grader_exclusions",
        }
    }
    atomic_json(directory / "qualification_audit.json", public_audit)
    if anomalies:
        raise ValueError("Qualification isolation audit failed; see audit")

    def number(item):
        return "undefined" if item is None else f"{item:.6f}"

    lines = [
        "# Qualification t1-260912b",
        "",
        "All eight arms; T=1; seed=1; first 6 search / 3 anchor / 3 sealed "
        "tasks in split file order. Blinding enabled; sealed values are "
        "available only in the oracle-side report.",
        "",
        "Results remain provisional pending R10 and must be discarded for "
        "an isolation- or label-blocking finding.",
        "",
        "| Arm | J | Search O | Decision | Guarded USD | Wall seconds |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for result in results:
        arm_costs = cost_summary(
            accounting / "ledger.jsonl",
            accounting / "requests.jsonl",
            arm=result["arm"],
            run_id=experiment,
        )
        lines.append(
            f"| {result['arm']} | {number(result['J_t'])} | "
            f"{number(result['O_t_search'])} | {result['decision']} | "
            f"{number(arm_costs['budget_accounted_usd'])} | "
            f"{number(result.get('wall_s'))} |"
        )
    lines += [
        "",
        "Standing metrics cover search-partition logical rollouts only.",
        "",
        "| Arm | N | No action | Inability | Exhaustion | Protocol error "
        "| Command timeout | Infrastructure retries / exclusions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        metric = result.get("standing_metrics") or {}
        counts = metric.get("counts", {})
        values = [
            str(counts.get(k, "undefined"))
            for k in (
                "no_action",
                "inability_claim",
                "exhaustion",
                "protocol_error",
                "command_timeout",
            )
        ]
        lines.append(
            f"| {result['arm']} | {metric.get('denominator', 'undefined')} | "
            + " | ".join(values)
            + f" | {metric.get('infrastructure_retries', 'undefined')} / "
            f"{metric.get('infrastructure_exclusions', 'undefined')} |"
        )
    lines += [
        "",
        f"Budget-accounted cost: USD {costs['budget_accounted_usd']:.6f}; "
        f"known cost USD {costs['known_usd']:.6f}; unresolved reservations "
        f"USD {costs['reserved_unresolved_usd']:.6f} across "
        f"{costs['unresolved_requests']} requests. Guard USD "
        f"{value['budget']['guard_usd']}; concurrency {value['concurrency']}; "
        "MemAvailable threshold 6 GiB.",
        "",
        "Isolation checks: private trial references and "
        f"{len(sessions)} evolver session records checked; public result "
        "objects scanned for private value fields. Isolation/pass-label "
        "anomalies detected by these checks: none. "
        "R10 re-check remains pending.",
        "",
        value.get("cache_partition_limitation", ""),
        "",
        "Full costs and audit: `qualification_audit.json`. Private checkpoint "
        f"report: `oracle/{experiment}/final_report.json`.",
        "",
    ]
    (directory / "report.md").write_text("\n".join(lines))
    private = root / "oracle" / experiment
    checkpoints = json.loads((private / "final_report.json").read_text())
    (private / "report.md").write_text(
        "# Oracle-side qualification report\n\nPrivate sealed checkpoints; "
        "never use for evolution or investigator tuning.\n\n```json\n"
        + json.dumps(checkpoints, indent=2)
        + "\n```\n"
    )
    return audit


class QualificationA3Round(A3Round):
    """Qualification-only allocation; preserve the frozen pilot A3 recipe."""

    async def coreset(self, seed):
        from evolution.a3 import bounded_map
        from evolution.a3_coreset import embed_fingerprints, select_coreset
        from evolution.a3_inspection import difficulty_digest
        from evolution.a3_operators import evidence, first_baselines
        from evolution.candidates import atomic_json, source_hash

        loop = self.loop
        validate(loop.manifest)
        k = loop.manifest["qualification_coreset"]["k"]
        fixed = loop.state.stage("a3-coreset")
        tasks = sorted(
            t["name"] for t in loop.evaluator.split["splits"]["search"]
        )
        if k != min(10, len(tasks)):
            raise ValueError("Qualification coreset differs from search size")
        if fixed is not None:
            if fixed.get("k") != k:
                raise ValueError("Frozen qualification coreset size changed")
            if fixed.get("evidence_version") != "v3":
                raise ValueError("Frozen A3 coreset must use v3 evidence")
            if fixed["search_tasks"] != tasks or fixed[
                "seed_sha256"
            ] != source_hash(seed):
                raise ValueError("Frozen coreset input changed")
            expected = loop.manifest.get("a3", {}).get("embedding")
            if (
                self.embedder is None
                and expected
                and (fixed["embedding"]["model"] != expected)
            ):
                raise ValueError("Frozen coreset embedding model changed")
            return fixed
        azure = loop.manifest.get("a3", {}).get("embedding") == (
            "text-embedding-3-large"
        )
        import tiktoken

        tiktoken.get_encoding("cl100k_base")  # Preflight before paid priors.
        if azure and self.embedder is None:
            from evolution.a3_embeddings import azure_provider

            azure_provider(loop)  # Fail before paid priors if keys are absent.
        prior = await loop.batch(seed, "search", "a3-prior", tasks=tasks)
        prior_by_task = first_baselines(prior, tasks, candidate=seed.name)

        async def describe(task):
            result = await self.ops.call(
                "difficulty",
                task,
                await asyncio.to_thread(
                    difficulty_digest, evidence(prior_by_task[task])
                ),
            )
            if result is None:
                raise ValueError("Coreset difficulty failed after A9 retry")
            return {"task": task, **result}

        self.boundary("difficulty", len(tasks))
        descriptions = await bounded_map(describe, tasks)
        fingerprints = [item["abstract_fingerprint"] for item in descriptions]
        if azure and self.embedder is None:
            from evolution.a3_embeddings import embed_azure

            self.boundary("embeddings", 1)
            vectors, embedding = await embed_azure(loop, fingerprints)
        else:
            vectors, embedding = await asyncio.to_thread(
                self.embedder or embed_fingerprints, fingerprints
            )
        selected = select_coreset(
            descriptions, vectors, k=k, seed=loop.manifest.get("seed", 1)
        )
        fixed = {
            "arm": loop.arm,
            "evidence_version": "v3",
            "search_tasks": tasks,
            "tasks": selected,
            "k": k,
            "theta": 0.7,
            "floor": 0.1,
            "floor_semantics": "raw_score_before_max_normalization",
            "seed": loop.manifest.get("seed", 1),
            "seed_sha256": source_hash(seed),
            "embedding": embedding,
            "descriptions": descriptions,
            "vectors": vectors,
            "prior": prior,
        }
        loop.state.stage("a3-coreset", fixed)
        public_core = fixed
        if loop.manifest.get("clean_calibration"):
            private_core = loop.evaluator.private / "a3-coreset.json"
            atomic_json(private_core, fixed)
            public_core = {k: v for k, v in fixed.items() if k != "prior"}
            public_core["prior_private_ref"] = str(private_core)
        atomic_json(loop.directory / "a3-coreset.json", public_core)
        return fixed

    async def run(self):
        import time

        from evolution.a3 import ARMS
        from evolution.a3_metrics import (
            completion,
            measurement_report,
            metrics,
            preference_report,
        )
        from evolution.a3_operators import (
            first_baselines,
            mean_preference,
            preference_total,
        )
        from evolution.accounting import cost_summary
        from evolution.candidates import atomic_json, source_hash
        from evolution.loop import acceptance_decision

        loop = self.loop
        native = loop.arm == "A3-native"
        if loop.arm not in ARMS or (native and loop.iteration != 1):
            raise ValueError("A3-native has exactly one round")
        if loop.candidate_count != 3:
            raise ValueError("RHO requires exactly N=3 proposals")
        key = f"finished-{loop.iteration}"
        previous = loop.state.stage(key)
        if previous is not None:
            loop.write_summary(previous)
            return previous
        from evolution.a3_manifest import require_v3

        require_v3(loop.manifest)
        start_key = f"started-{loop.iteration}"
        started = loop.state.stage(start_key) or {"time": time.time()}
        loop.state.stage(start_key, started)
        seed = loop.seed()
        checkpoint_key = f"checkpoint-{loop.iteration}"
        checkpoint = (
            loop.state.stage(checkpoint_key)
            or loop.state.stage("incumbent")
            or {
                "candidate": str(seed),
                "source_sha256": source_hash(seed),
            }
        )
        loop.state.stage(checkpoint_key, checkpoint)
        parent = Path(checkpoint["candidate"])
        core = await self.coreset(seed)
        tasks = core["tasks"]
        seed_references = first_baselines(
            core["prior"], core["search_tasks"], candidate="seed"
        )
        if not native and loop.state.stage("seed-sealed-checkpoint") is None:
            sealed = await loop.batch(seed, "sealed", "seed-checkpoint", 2)
            atomic_json(
                loop.evaluator.private / "checkpoint-t0.json",
                {
                    "arm": loop.arm,
                    "candidate": str(seed),
                    "rows": sealed,
                    "metrics": metrics(sealed),
                    "measurements": measurement_report(sealed, private=True),
                    "measurement_complete": metrics(sealed)[
                        "measurement_complete"
                    ],
                },
            )
            seed_rows = await loop.batch(seed, "search", "a3-seed-measurement")
            seed_measured = await self.rank_rows(
                "seed-measurement", seed_rows, seed_references, seed, seed
            )
            loop.state.stage(
                "seed-sealed-checkpoint", completion(seed_measured, sealed)
            )
        group = await loop.batch(parent, "search", "a3-group", 3, tasks=tasks)
        references = first_baselines(group, tasks, candidate=parent.name)
        # Freeze reference identities before diagnosis/proposals/ranking.
        reference_key = f"a3-references-{loop.iteration}"
        identities = {task: row["id"] for task, row in references.items()}
        fixed = loop.state.stage(reference_key)
        if fixed is not None and fixed != identities:
            raise ValueError("First group rollout reference changed")
        loop.state.stage(reference_key, identities)
        await self.diagnoses(parent, tasks, group)
        proposals = [await loop.propose(parent, slot) for slot in range(1, 4)]
        for proposal in proposals:
            if proposal["status"] != "valid":
                proposal["preferences"] = preference_report(
                    [
                        {
                            "id": f"{proposal['id']}:{task}",
                            "task": task,
                            "reference_id": identities[task],
                            "score": None,
                            "pair_status": "unscored-failure",
                        }
                        for task in tasks
                    ]
                )
                proposal["ineligible_reason"] = "source_checks_failed"
                continue
            candidate = Path(proposal["candidate"])
            rows = await loop.batch(
                candidate, "search", "a3-after", tasks=tasks
            )
            scored = await self.rank_rows(
                f"selection-{candidate.name}",
                rows,
                references,
                candidate,
                parent,
            )
            proposal["search"] = metrics(scored)
            proposal["preference"] = mean_preference(
                [r["score"] for r in scored], expected=len(tasks)
            )
            proposal["pair_scores"] = [r["score"] for r in scored]
            proposal["raw_ratings"] = [r["raw_rating"] for r in scored]
            proposal["signed_total"] = preference_total(
                proposal["raw_ratings"], expected=len(tasks)
            )
            proposal["preferences"] = preference_report(scored)
            proposal["reference_ids"] = identities
        eligible = [p for p in proposals if p.get("preference") is not None]
        best = (
            min(eligible, key=lambda p: (-p["signed_total"], p["id"]))
            if eligible
            else None
        )
        winner = Path(best["candidate"]) if best else None
        preference = best["preference"] if best else None
        accepted = False
        anchors = {"baseline_anchor": None, "candidate_anchor": None}
        if winner is not None and preference > 0:
            if not native and loop.rule == "anchor":
                left, right = await asyncio.gather(
                    loop.batch(parent, "anchor", "acceptance"),
                    loop.batch(winner, "anchor", "acceptance"),
                )
                anchors = {
                    "baseline_anchor": None
                    if metrics(left)["oracle_excluded"]
                    else metrics(left)["O"],
                    "candidate_anchor": None
                    if metrics(right)["oracle_excluded"]
                    else metrics(right)["O"],
                }
            accepted = acceptance_decision(
                loop.arm,
                0,
                preference,
                anchors["baseline_anchor"],
                anchors["candidate_anchor"],
                rule="improve" if native else loop.rule,
                tau=loop.tau,
                epsilon=loop.epsilon,
            )
        atomic_json(
            loop.evaluator.private / f"acceptance-i{loop.iteration}.json",
            {
                "arm": loop.arm,
                **anchors,
                "accepted": accepted,
                "rule": "rho-positive" if native else loop.rule,
            },
        )
        incumbent = winner if accepted else parent
        update = {
            "candidate": str(incumbent),
            "source_sha256": source_hash(incumbent),
        }
        loop.state.stage("incumbent", update)
        atomic_json(loop.directory / "incumbent.json", update)
        measurement_attempts = loop.manifest.get("clean_calibration", {}).get(
            "search_measurement_attempts", 1
        )
        measured = await loop.batch(
            incumbent, "search", "measurement", measurement_attempts
        )
        measured = await self.rank_rows(
            "measurement", measured, seed_references, incumbent, seed
        )
        # No sealed evidence enters any RHO operator. Native has only this
        # standard incumbent avg@2 checkpoint, no sealed t=0 or anchor calls.
        sealed = await loop.batch(incumbent, "sealed", "measurement", 2)
        measured_metrics = metrics(measured)
        completion_fields = completion(measured, sealed)
        costs = cost_summary(
            loop.accounting / "ledger.jsonl",
            loop.accounting / "requests.jsonl",
            arm=loop.arm,
            iteration=loop.iteration,
            run_id=loop.experiment,
        )
        summary = {
            "experiment": loop.experiment,
            "arm": loop.arm,
            "purpose": loop.manifest.get("purpose"),
            "evidence_version": "v3",
            "iteration": loop.iteration,
            "status": "complete"
            if completion_fields["measurement_complete"]
            else "measurement-incomplete",
            **completion_fields,
            "parent": parent.name,
            "incumbent": incumbent.name,
            "winner": winner.name if winner else None,
            "accepted": accepted,
            "decision": "accepted" if accepted else "rejected",
            "rule": "rho-positive" if native else loop.rule,
            "tau": loop.tau,
            "epsilon": loop.epsilon,
            "coreset": tasks,
            "coreset_preference": preference,
            "baseline": {"J": 0, "scale": "signed_preference"},
            "candidates": proposals,
            "J_t": measured_metrics["J"],
            "O_t_search": measured_metrics["O"],
            "search_measurement": measured_metrics,
            "scale": "signed_preference",
            "authorized_hypotheses": ["H2", "H5"],
            "wall_s": time.time() - started["time"],
            "costs_iteration": costs,
            "costs_cumulative": cost_summary(
                loop.accounting / "ledger.jsonl",
                loop.accounting / "requests.jsonl",
                arm=loop.arm,
                run_id=loop.experiment,
            ),
            "cost_upper_usd": costs["uncached_upper_usd"]
            + costs["reserved_unresolved_usd"],
            "search_measurement_attempts": measurement_attempts,
            "diff_class": None,
        }
        atomic_json(
            loop.evaluator.private / f"checkpoint-t{loop.iteration}.json",
            {
                "arm": loop.arm,
                "iteration": loop.iteration,
                "incumbent": incumbent.name,
                "O_t_sealed": metrics(sealed)["O"],
                "sealed_measurement": metrics(sealed),
                "measurements": measurement_report(sealed, private=True),
                "measurement_complete": completion_fields[
                    "sealed_measurements"
                ]["complete"],
                "endpoint_eligible": completion_fields["endpoint_eligible"],
            },
        )
        loop.state.stage(key, summary)
        loop.write_summary(summary)
        return summary
