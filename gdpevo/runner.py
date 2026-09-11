"""Manifest-driven T2 seed rollouts with durable A9 slots and v3 exports.

The experiment owns task selection. This controller schedules measurements;
proposal/selection algorithms may call it with explicit schedule entries.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import dotenv_values

from evolution.accounting import (
    AccountedBackend,
    BudgetHalt,
    PhaseGuard,
    cost_summary,
)
from evolution.candidates import atomic_json, safe_id
from evolution.judge_queue import export_trace
from evolution.judges import JudgeFailure, build_payload, judge_once
from evolution.manifest import defaults as t1_defaults
from evolution.manifest import validate as t1_validate
from evolution.outcomes import termination
from evolution.reconcile import reconcile
from evolution.sanitize import VERSION, canonical, digest
from gdpevo import GROUPS, ROOT, SOURCE, task_path
from gdpevo.boundary import Attempt
from gdpevo.feedback import api_scope
from gdpevo.oracle import grade_attempt, strict_json, verify_frozen
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.seed import SeedError, run_seed


class InfrastructureFailure(RuntimeError):
    """Host/service/provider failure; one whole-attempt replacement allowed."""


def read_rows(path):
    return (
        [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
        if path.exists()
        else []
    )


def task_role(task):
    group, split, task_id = task.split("/")
    group = int(group)
    task_path(SOURCE / "data/task_groups", group, split, task_id)
    if group in (8, 9, 10, 11):
        return "anchor"
    return "search" if split == "train" else "sealed"


def defaults(experiment):
    value = t1_defaults(experiment, validation=True)
    value.update(
        testbed="T2",
        concurrency=4,
        arms=["A0", "A1"],
        oracle_range_policy="unit_interval",
        transport_max_retries=0,
        rollout_timeout_s=1200,
        command_timeout_s=30,
        memory_min_available_gib=6,
        docker_concurrency=2,
        tasks={"search": [], "anchor": [], "sealed": []},
    )
    for group in GROUPS:
        for split in ("train", "test"):
            for number in range(1, 6):
                task = f"{group:03}/{split}/{number:03}"
                value["tasks"][task_role(task)].append(task)
    value["budget"].update(estimate_usd=10, guard_usd=10, wall_clock_hours=3)
    return value


def validate(value):
    t1_validate(value)
    if value.get("testbed") != "T2":
        raise ValueError("Expected T2 manifest")
    if value.get("oracle_range_policy") != "unit_interval":
        raise ValueError(
            "Only the requested AD9 unit_interval rule is implemented"
        )
    if value.get("transport_max_retries") != 0:
        raise ValueError(
            "A9 runner owns retries; transport_max_retries must be zero"
        )
    if set(value["tasks"]) != {"search", "anchor", "sealed"}:
        raise ValueError(
            "Explicit search, anchor and sealed task pools required"
        )
    seen = set()
    for role, tasks in value["tasks"].items():
        for task in tasks:
            if task_role(task) != role or task in seen:
                raise ValueError("Task role mismatch or duplicate task")
            seen.add(task)
    for name in (
        "rollout_timeout_s",
        "command_timeout_s",
        "memory_min_available_gib",
        "docker_concurrency",
    ):
        if type(value[name]) not in (int, float) or not 0 < value[
            name
        ] < float("inf"):
            raise ValueError("Invalid runtime limit")
    if (
        type(value["docker_concurrency"]) is not int
        or value["docker_concurrency"] > 4
    ):
        raise ValueError("Docker concurrency must be an integer up to four")
    for spec in schedule(value):
        if spec["task"] not in seen or spec["arm"] not in value["arms"]:
            raise ValueError("Schedule entry outside manifest pools")
        if spec["seed"] not in value["seeds"]:
            raise ValueError("Unknown schedule seed")
        if (
            type(spec["iteration"]) is not int
            or not 0 <= spec["iteration"] <= value["T"]
        ):
            raise ValueError("Invalid schedule iteration")
        if type(spec["replicate"]) is not int or spec["replicate"] < 0:
            raise ValueError("Invalid replicate")


def schedule(value):
    if "schedule" in value:
        entries = value["schedule"]
    else:
        entries = [
            {
                "arm": arm,
                "seed": seed,
                "iteration": iteration,
                "task": task,
                "replicate": replicate,
            }
            for arm in value["arms"]
            for seed in value["seeds"]
            for iteration in range(value["T"] + 1)
            for tasks in value["tasks"].values()
            for task in tasks
            for replicate in range(value.get("replicates", 1))
        ]
    result, seen = [], set()
    for entry in entries:
        if set(entry) != {"arm", "seed", "iteration", "task", "replicate"}:
            raise ValueError(
                "Schedule requires arm, seed, iteration, task and replicate"
            )
        identity = digest(canonical(entry))[:24]
        if identity in seen:
            raise ValueError("Duplicate scheduled rollout")
        seen.add(identity)
        result.append(
            {
                **entry,
                "rollout_id": identity,
                "task_role": task_role(entry["task"]),
            }
        )
    if not result:
        raise ValueError("Empty rollout schedule")
    return result


def condition_hashes():
    paths = [
        p
        for folder in (
            "gdpevo",
            "docker/gdpevo",
            "scripts/gdpevo",
            "harness",
            "evolution",
        )
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and (
            p.suffix == ".py"
            or p.name.endswith("Dockerfile")
            or p.suffix == ".sh"
        )
    ]
    paths += [
        ROOT / "data/gdpevo_hidden_columns.json",
        ROOT / "docker/gdpevo/images.lock.json",
        ROOT / "scripts/prices.json",
    ]
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def resolve(value, config):
    validate(value)
    result = json.loads(json.dumps(value))
    result.pop("resolved_sha256", None)
    result["hashes"] = condition_hashes()
    result["grader"] = verify_frozen()
    result["evidence_version"] = VERSION
    result["providers"] = {}
    for role, prefix in (
        ("task", value["task_model"]["endpoint_prefix"]),
        ("judge", "JUDGE"),
        ("evolver", "EVOLVER"),
    ):
        base = config.get(f"{prefix}_API_BASE", "").rstrip("/")
        if base and not base.endswith("/v1"):
            base += "/openai/v1"
        result["providers"][role] = {
            "base_url": base,
            "deployment": value["task_model"]["deployment"]
            if role == "task"
            else config.get(f"{prefix}_MODEL"),
            "rpm": float(config.get(f"{prefix}_RPM", 250)),
            "tpm": float(config.get(f"{prefix}_TPM", 250000)),
        }
    result["resolved_sha256"] = digest(canonical(result))
    return result


class Slots:
    """One atomic materialized row per scheduled slot plus fsynced history.

    rows.jsonl is a fixed-denominator view, not an append stream to sum twice.
    All slots are present before the first container or paid request starts.
    """

    def __init__(self, directory, specs):
        self.directory = Path(directory)
        self.path = self.directory / "rollouts.jsonl"
        self.rows = {r["rollout_id"]: r for r in read_rows(self.path)}
        if self.rows and set(self.rows) != {s["rollout_id"] for s in specs}:
            raise ValueError("Frozen rollout schedule changed")
        for spec in specs:
            if spec["rollout_id"] not in self.rows:
                self.rows[spec["rollout_id"]] = {
                    **spec,
                    "status": "scheduled",
                    "denominator": 1,
                    "eligible_denominator": None,
                    "excluded": False,
                    "infrastructure_attempts": 0,
                }
        self.flush()

    def flush(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w") as handle:
            for row in self.rows.values():
                handle.write(canonical(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        fd = os.open(self.directory, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def update(self, identity, **values):
        row = {**self.rows[identity], **values, "updated_at": utc_now()}
        # Persist the materialized identity before attempting any work.
        self.rows[identity] = row
        self.flush()
        append_jsonl(self.directory / "rollout_events.jsonl", row)
        return row


def classify(records, error, manifest):
    outcome = termination(records)
    if isinstance(error, TimeoutError):
        return "solver_timeout", {**outcome, "agent_timeout": True}
    assistants = [r for r in records if r.get("kind") == "assistant"]
    if isinstance(error, SeedError) and str(error).startswith(
        "Backend failed:"
    ):
        timeout = "Timeout" in str(error)
        first_timeout = (
            timeout and len(assistants) == 1 and not outcome["action_executed"]
        )
        if (
            first_timeout
            and manifest["api_timeout_policy"] == "infrastructure"
        ):
            return "infrastructure_failure", {**outcome, "api_timeout": True}
        if timeout:
            return "api_timeout_failure", {**outcome, "api_timeout": True}
        return "infrastructure_failure", outcome
    if outcome["executor_failure"] or outcome["protocol_failure"]:
        return "executor_failure", outcome
    if (
        manifest["tool_failure_reading"] == "strict"
        and outcome["nonzero_exit"]
    ):
        return "tool_failure", outcome
    if error:
        return "seed_failure", outcome
    return "finished", outcome


class Runner:
    def __init__(self, manifest, config=None, *, boundary_factory=Attempt):
        self.config = (
            config if config is not None else dotenv_values(ROOT / ".env")
        )
        self.manifest = manifest
        validate(manifest)
        self.experiment = safe_id(manifest["experiment"])
        self.directory = ROOT / "runs" / self.experiment
        self.private = ROOT / "oracle" / self.experiment
        self.accounting = ROOT / "costs" / self.experiment
        self.boundary_factory = boundary_factory
        self.specs = schedule(manifest)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory / "controller.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.slots = Slots(self.directory, self.specs)
        self.semaphore = asyncio.Semaphore(manifest["docker_concurrency"])

    def freeze(self):
        resolved = resolve(self.manifest, self.config)
        path = self.directory / "manifest.json"
        if path.exists() and json.loads(path.read_text()) != resolved:
            raise ValueError(
                "Frozen experiment condition changed; refusing resume"
            )
        atomic_json(path, resolved)
        self.manifest = resolved
        budget = resolved["budget"]
        guard = PhaseGuard(
            self.accounting / "budget.sqlite",
            estimate=budget["estimate_usd"],
            ceiling=budget["guard_usd"],
            hours=budget["wall_clock_hours"],
        )
        guard.close()
        if resolved["purpose"] != "infrastructure":
            # Formal Phase 3 awaits ratification and qualification.
            raise ValueError(
                "Formal Phase 3 requires ratified entry gates; "
                "this runner currently admits infrastructure experiments"
            )
        controls = ROOT / resolved.get(
            "controls_path", "logs/gdpevo/p110-controls-final/controls.json"
        )
        data = json.loads(controls.read_text())
        if not data["passed"] or data["manifest"] != resolved["grader"]:
            raise ValueError("Frozen grader controls missing or changed")
        atomic_json(
            self.directory / "controls-provenance.json",
            {
                "path": str(controls.relative_to(ROOT)),
                "sha256": hashlib.sha256(controls.read_bytes()).hexdigest(),
            },
        )

    def backend(self, spec, role, logs, *, scope_suffix="", max_calls=None):
        model = self.manifest["task_model"]
        prefix = model["endpoint_prefix"] if role == "task" else role.upper()
        provider = self.manifest["providers"][role]
        tags = CallTags(
            "T2-preparation",
            self.experiment,
            spec["arm"],
            spec["iteration"],
            spec["task"],
            role,
        )
        scope = (
            f"{self.experiment}/{spec['seed']}/{spec['arm']}/"
            f"{spec['rollout_id']}/{role}{scope_suffix}"
        )
        params = api_scope(
            f"{self.experiment}/{spec['seed']}", spec["arm"], role
        )
        if role != "task":
            # This OpenAI-compatible endpoint accepts user, but rejects
            # the OpenAI-specific prompt_cache_key parameter.
            params.pop("prompt_cache_key")
            params.update(
                temperature=0.6,
                top_p=0.95,
                response_format={"type": "json_object"},
            )
        return AccountedBackend(
            base_url=provider["base_url"],
            api_key=self.config.get(f"{prefix}_API_KEY", ""),
            model=provider["deployment"],
            ledger=self.accounting / "ledger.jsonl",
            logs_dir=logs / "calls",
            timeout_s=model["api_timeout_s"],
            effort=model["reasoning_effort"] if role == "task" else None,
            max_completion_tokens=model["completion_allowance"]
            if role == "task"
            else 2048,
            extra_params=params,
            max_retries=0,
            budget_usd=self.manifest["budget"]["rollout_usd"]
            if role == "task"
            else self.manifest["budget"]["evolver_session_usd"],
            prices_path=ROOT / "scripts/prices.json",
            guard_path=self.accounting / "budget.sqlite",
            limiter_path=ROOT / "logs/gdpevo/endpoints.sqlite",
            audit_path=self.accounting / "requests.jsonl",
            tags=tags,
            scope=scope,
            rpm=provider["rpm"],
            tpm=provider["tpm"],
            max_calls=max_calls or model["max_calls"],
        )

    async def admission(self, spec):
        deadline = time.monotonic() + 120
        while True:
            available = next(
                int(line.split()[1]) / 1024**2
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
            if available >= self.manifest["memory_min_available_gib"]:
                append_jsonl(
                    self.directory / "admissions.jsonl",
                    {
                        "rollout_id": spec["rollout_id"],
                        "MemAvailable_gib": available,
                        "ts": utc_now(),
                    },
                )
                return
            if time.monotonic() > deadline:
                raise InfrastructureFailure("MemAvailable admission timed out")
            await asyncio.sleep(2)

    async def attempt(self, spec, number):
        identity = spec["rollout_id"]
        logs = (
            ROOT
            / "logs/gdpevo"
            / self.experiment
            / identity
            / f"attempt-{number}"
        )
        logs.mkdir(parents=True, exist_ok=True)
        result_path = (
            self.private / identity / f"attempt-{number}" / "result.json"
        )
        if result_path.exists():
            return json.loads(result_path.read_text()), logs
        group, split, task_id = spec["task"].split("/")
        await self.admission(spec)
        backend = self.backend(spec, "task", logs, scope_suffix=f"/{number}")
        status, raw, outcome = "infrastructure_failure", b"", {}
        error = None
        try:
            with self.boundary_factory(
                int(group), split, task_id, logs / "environment"
            ) as boundary:
                if self.manifest.get("acceptance_probes"):
                    from gdpevo.acceptance import solver_probes

                    await solver_probes(boundary, logs, self.private)
                    boundary.tool_failed = boundary.executor_failed = False
                instruction = (
                    (logs / "environment/staged/input/prompt.txt").read_text()
                    + "\n\nWork in /work. Read /work/environment_access.md "
                    "for API access and read all files under "
                    "/work/input/payloads. Write the final JSON object to "
                    "/work/answer.json. Use /work/scratch for temporary files."
                )
                try:
                    async with asyncio.timeout(
                        self.manifest["rollout_timeout_s"]
                    ):
                        answer = await run_seed(
                            instruction,
                            boundary,
                            backend,
                            backend.tags,
                            logs / "trajectory.jsonl",
                            max_steps=self.manifest["task_model"]["max_calls"],
                            command_timeout_s=self.manifest[
                                "command_timeout_s"
                            ],
                            tool_protocol="json",
                        )
                    (logs / "final.txt").write_text(answer)
                except (SeedError, TimeoutError, BudgetHalt) as exc:
                    error = exc
                except Exception as exc:
                    error = SeedError("Executor exception")
                    append_jsonl(
                        logs / "trajectory.jsonl",
                        {"kind": "observation", "error": type(exc).__name__},
                    )
                status, outcome = classify(
                    read_rows(logs / "trajectory.jsonl"), error, self.manifest
                )
                if isinstance(error, BudgetHalt):
                    status = "budget_halt"
                try:
                    raw = boundary.snapshot()
                except (ValueError, OSError) as exc:
                    status = (
                        "oversized_answer"
                        if isinstance(exc, ValueError)
                        else "unreadable_answer"
                    )
                if status == "finished":
                    try:
                        submission = strict_json(raw)
                        if not isinstance(submission, dict):
                            status = "non_object_answer"
                    except (ValueError, UnicodeDecodeError):
                        status = "invalid_json_answer"
        finally:
            backend.close()
        execution = {
            **outcome,
            "status": "finished" if status == "finished" else "failed",
        }
        atomic_json(logs / "execution.json", execution)
        if (logs / "trajectory.jsonl").exists():
            append_jsonl(
                logs / "trajectory.jsonl",
                {
                    "kind": "termination",
                    **{k: v for k, v in execution.items() if k != "kind"},
                },
            )
        if status == "infrastructure_failure":
            raise InfrastructureFailure(
                "Provider failed before eligible completion"
            )
        atomic_json(
            logs / "submission.json",
            {
                "status": status,
                "execution": execution,
                "answer_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        result = grade_attempt(
            int(group), split, task_id, raw, result_path.parent, status
        )
        result.update(status=status, execution=execution)
        atomic_json(result_path, result)
        if isinstance(error, BudgetHalt) and error.phase:
            raise error
        return result, logs

    async def signal(self, spec, evidence, logs, binary):
        arm = spec["arm"].removeprefix("C-TTS-")
        if arm == "A0":
            return binary
        if arm in ("A3-loop", "A3-native"):
            # A3 requires a paired baseline and ranker; never substitute O.
            return None
        judges = {"A1": ("a1",), "A2": ("a2",), "A4": ("a1", "a2")}[arm]
        scores = []
        for judge in judges:
            result_file = logs / f"{judge}-result.json"
            if result_file.exists():
                scores.append(json.loads(result_file.read_text())["score"])
                continue
            atomic_json(
                logs / f"{judge}-payload.json", build_payload(judge, evidence)
            )
            score = None
            for attempt in range(2):
                event = logs / f"{judge}-{attempt}-intent.json"
                if event.exists():
                    continue
                atomic_json(event, {"status": "started"})
                backend = self.backend(
                    spec,
                    "judge",
                    logs / f"{judge}-{attempt}",
                    scope_suffix=f"/{judge}/{attempt}",
                    max_calls=1,
                )
                try:
                    reply = await judge_once(
                        backend, judge, evidence, backend.tags
                    )
                    score = reply["score"]
                    atomic_json(
                        result_file,
                        {"score": score, "call_id": reply["call_id"]},
                    )
                    break
                except JudgeFailure:
                    pass
                finally:
                    backend.close()
            scores.append(score)
        return sum(scores) / len(scores) if None not in scores else None

    async def trial(self, spec):
        identity = spec["rollout_id"]
        if self.slots.rows[identity]["status"] == "complete":
            return
        async with self.semaphore:
            row = self.slots.rows[identity]
            result, logs = None, None
            for number in range(2):
                attempt_dir = self.private / identity / f"attempt-{number}"
                logs = (
                    ROOT
                    / "logs/gdpevo"
                    / self.experiment
                    / identity
                    / f"attempt-{number}"
                )
                if (attempt_dir / "result.json").exists():
                    result = json.loads(
                        (attempt_dir / "result.json").read_text()
                    )
                    if "execution" not in result:
                        result["execution"] = json.loads(
                            (logs / "execution.json").read_text()
                        )
                        atomic_json(attempt_dir / "result.json", result)
                    break
                if (attempt_dir / "answer.json").exists() and (
                    logs / "submission.json"
                ).exists():
                    # Grader recovery never spends a replacement solver call.
                    saved = json.loads((logs / "submission.json").read_text())
                    raw = (attempt_dir / "answer.json").read_bytes()
                    if (
                        hashlib.sha256(raw).hexdigest()
                        != saved["answer_sha256"]
                    ):
                        raise ValueError("Frozen answer checkpoint changed")
                    group, split, task_id = spec["task"].split("/")
                    result = grade_attempt(
                        int(group),
                        split,
                        task_id,
                        raw,
                        attempt_dir,
                        saved["status"],
                    )
                    result.update(execution=saved["execution"])
                    atomic_json(attempt_dir / "result.json", result)
                    Attempt.recover(logs / "environment")
                    break
                if number < row["infrastructure_attempts"]:
                    # A crash consumes the in-flight attempt, never repeats it.
                    environment = (
                        ROOT
                        / "logs/gdpevo"
                        / self.experiment
                        / identity
                        / f"attempt-{number}"
                        / "environment"
                    )
                    Attempt.recover(environment)
                    continue
                if (self.accounting / "budget.sqlite").exists():
                    guard = PhaseGuard(self.accounting / "budget.sqlite")
                    try:
                        guard.check()
                    finally:
                        guard.close()
                self.slots.update(
                    identity,
                    status="running",
                    infrastructure_attempts=number + 1,
                )
                try:
                    result, logs = await self.attempt(spec, number)
                    break
                except BudgetHalt:
                    raise
                except Exception as exc:
                    atomic_json(
                        attempt_dir / "infrastructure.json",
                        {
                            "type": type(exc).__name__,
                            "attempt": number,
                            "status": "infrastructure_failure",
                        },
                    )
            if result is None:
                self.slots.update(
                    identity,
                    status="complete",
                    disposition="infrastructure_excluded",
                    excluded=True,
                    eligible_denominator=0,
                    infrastructure_exclusions=1,
                )
                return
            self.slots.update(
                identity,
                status="exporting",
                disposition=result["status"],
                excluded=result["excluded"],
                eligible_denominator=0 if result["excluded"] else 1,
                grader_retries=result["grader_retries"],
                grader_exclusions=int(result["excluded"]),
                **(
                    {"binary": result["binary"]}
                    if spec["task_role"] == "search"
                    else {}
                ),
            )
            evidence = export_trace(
                logs / "trajectory.jsonl",
                logs / "evidence",
                execution=result["execution"],
            )
            score = None
            if spec["task_role"] == "search" and not result["excluded"]:
                score = await self.signal(
                    spec, evidence, logs, result["binary"]
                )
                if score is not None:
                    path = (
                        ROOT
                        / "feedback"
                        / self.experiment
                        / f"seed-{spec['seed']}"
                        / spec["arm"]
                        / f"{identity}.json"
                    )
                    atomic_json(
                        path,
                        {
                            "task": spec["task"],
                            "score": score,
                            "trajectory": evidence.trajectory.to_dict(),
                        },
                    )
            self.slots.update(
                identity,
                status="complete",
                trajectory=os.path.relpath(
                    logs / "trajectory.jsonl", self.directory
                ),
                execution=result["execution"],
                feedback_exported=score is not None,
                judge_failed=spec["task_role"] == "search"
                and score is None
                and not result["excluded"]
                and spec["arm"].removeprefix("C-TTS-")
                not in {"A3-loop", "A3-native"},
                feedback_status=(
                    "exported"
                    if score is not None
                    else "pairwise_signal_required"
                    if spec["arm"].removeprefix("C-TTS-")
                    in {"A3-loop", "A3-native"}
                    else "unavailable"
                ),
            )
            print(
                json.dumps(
                    {
                        "rollout_id": identity,
                        "arm": spec["arm"],
                        "role": spec["task_role"],
                        "status": "complete",
                    }
                ),
                flush=True,
            )

    async def run(self):
        try:
            self.freeze()
            reconcile(self.accounting)
            results = await asyncio.gather(
                *(self.trial(spec) for spec in self.specs),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            self.exports()
            self.lock.close()

    def exports(self):
        entries = [
            {
                "rollout_id": row["rollout_id"],
                "path": row["trajectory"],
                "metadata": {
                    k: row[k]
                    for k in ("arm", "seed", "iteration", "task", "task_role")
                },
                "execution": row["execution"],
            }
            for row in self.slots.rows.values()
            if row.get("trajectory")
        ]
        atomic_json(
            self.directory / "trajectories.json",
            {
                "trajectories": entries,
                "expected_count": len(entries),
                "scheduled_count": len(self.specs),
            },
        )
        reconcile(self.accounting)
        summary = cost_summary(
            self.accounting / "ledger.jsonl",
            self.accounting / "requests.jsonl",
        )
        summary.update(
            scheduled=len(self.specs),
            complete=sum(
                r["status"] == "complete" for r in self.slots.rows.values()
            ),
            exclusions_by_arm={
                arm: sum(
                    r["excluded"]
                    for r in self.slots.rows.values()
                    if r["arm"] == arm
                )
                for arm in self.manifest["arms"]
            },
        )
        atomic_json(self.directory / "summary.json", summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(Runner(json.loads(args.manifest.read_text())).run())


if __name__ == "__main__":
    main()
