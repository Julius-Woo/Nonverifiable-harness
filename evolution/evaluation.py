"""Harbor scheduling, private oracle recording, and queued search judging."""

import asyncio
import fcntl
import json
import signal
import time
from pathlib import Path

from evolution.accounting import AccountedBackend, BudgetHalt
from evolution.behavior import (
    behavior_flags,
    claimed_without_ran,
    standing_metrics,
)
from evolution.candidates import atomic_json
from evolution.judge_queue import (
    Endpoint,
    JudgeQueue,
    TokenBucketLimiter,
    export_trace,
)
from evolution.judges import mixture
from evolution.outcomes import api_timeout_only, grader_failure, termination
from evolution.sanitize import sanitized_events
from evolution.workspace import docker
from harness.ledger import CallTags, append_jsonl, utc_now


def available_gib():
    lines = Path("/proc/meminfo").read_text().splitlines()
    return (
        int(next(s for s in lines if s.startswith("MemAvailable:")).split()[1])
        / 1024**2
    )


def oracle_label(result, execution, records):
    """AD10 L1': outcome label, independent of recovered process events."""
    from evolution.outcomes import termination

    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
        "reward"
    )
    outcome = termination(records, result=result, execution=execution)
    if outcome["agent_timeout"]:
        return 0, reward, "agent_timeout"
    if outcome["reason"] == "unknown" and reward is None:
        return None, reward, "missing_or_invalid_verifier"
    if outcome["reason"] not in {"normal_finish", "no_tools_or_inability"}:
        return 0, reward, outcome["reason"]
    if grader_failure(result, records, execution):
        return None, reward, "grader_failure"
    if type(reward) not in (int, float) or reward not in (0, 1):
        return None, reward, "missing_or_invalid_verifier"
    return int(reward == 1), reward, "valid_verifier"


def aggregate(rows, expected=None):
    """Equal task/seed blocks; missing judge scores remain missing.

    O uses the fixed allocation. Registered A9 exclusion and observed-only
    quantities are separate diagnostics; incomplete blocks cannot be paired.
    Expected slots let controls retain missing selections in the denominator.
    """
    rows = list(rows)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate rollout identity in aggregation")
    if expected is not None:
        by_slot = {
            (r.get("seed", 1), r["task"], r.get("replicate", 0)): r
            for r in rows
        }
        if len(by_slot) != len(rows):
            raise ValueError("Duplicate fixed-allocation slot")
        if len({tuple(slot) for slot in expected}) != len(expected):
            raise ValueError("Duplicate expected allocation slot")
        rows = [
            by_slot.get(
                tuple(slot),
                {
                    "id": f"missing-{slot}",
                    "seed": slot[0],
                    "task": slot[1],
                    "replicate": slot[2],
                    "score": None,
                    "oracle": None,
                },
            )
            for slot in expected
        ]
    blocks = {}
    for row in rows:
        blocks.setdefault(
            (row.get("seed", 1), row.get("task", "task")), []
        ).append(row)

    def mean(values):
        return sum(values) / len(values) if values else None

    def equal_seeds(values):
        seeds = {}
        for (seed, task), value in values.items():
            if value is not None:
                seeds.setdefault(seed, []).append(value)
        return mean([mean(v) for v in seeds.values()])

    scores = [r["score"] for r in rows if r.get("score") is not None]
    labels = [r["oracle"] for r in rows if r.get("oracle") is not None]
    block_j = {
        k: mean([r["score"] for r in v if r.get("score") is not None])
        for k, v in blocks.items()
    }
    block_o = {
        k: sum(r.get("oracle") or 0 for r in v) / len(v)
        for k, v in blocks.items()
    }
    complete = {
        k: v
        for k, v in blocks.items()
        if all(
            r.get("oracle") is not None and r.get("score") is not None
            for r in v
        )
    }
    common_j = equal_seeds(
        {k: mean([r["score"] for r in v]) for k, v in complete.items()}
    )
    common_o = equal_seeds(
        {k: mean([r["oracle"] for r in v]) for k, v in complete.items()}
    )
    return {
        "standing_metrics": standing_metrics(rows),
        "J": equal_seeds(block_j),
        "O": equal_seeds(
            {
                k: mean(
                    [r.get("oracle") or 0 for r in v if not r.get("excluded")]
                )
                for k, v in blocks.items()
            }
        ),
        "O_fixed_denominator": equal_seeds(block_o),
        "J_observed_attempts": mean(scores),
        "O_observed_attempts": mean(labels),
        "J_fixed_denominator_sensitivity": equal_seeds(
            {
                k: sum(r.get("score") or 0 for r in v) / len(v)
                for k, v in blocks.items()
            }
        ),
        "common_complete_task_blocks": len(complete),
        "common_J": common_j,
        "common_O": common_o,
        "common_gap": (
            common_j - common_o
            if common_j is not None
            and not any(r.get("scale") == "signed_preference" for r in rows)
            else None
        ),
        "task_blocks": len(blocks),
        "scheduled": len(rows),
        "judge_missing": len(rows) - len(scores),
        "oracle_excluded": len(rows) - len(labels),
        "incomplete_task_blocks": len(blocks) - len(complete),
        "trials": [r["id"] for r in rows],
    }


class Evaluator:
    trace_exporter = staticmethod(export_trace)

    def __init__(
        self,
        root,
        experiment,
        arm,
        iteration,
        state,
        guard,
        config,
        concurrency=4,
    ):
        self.root, self.experiment, self.arm = Path(root), experiment, arm
        self.iteration, self.state, self.guard = iteration, state, guard
        self.config, self.concurrency = config, min(4, concurrency)
        self.score_lock = asyncio.Lock()
        self.accounting = self.root / "costs" / experiment
        self.logs = self.root / "logs" / "evolution" / experiment / arm
        self.jobs = self.root / "oracle" / experiment / arm / "harbor"
        self.private = self.root / "oracle" / experiment / arm
        self.feedback = self.root / "feedback" / arm / experiment
        for path in (self.logs, self.jobs, self.private, self.feedback):
            path.mkdir(parents=True, exist_ok=True)
        self.split = json.loads(
            (self.root / "data/tb2_split.json").read_text()
        )
        if config.get("tasks"):
            self.split["splits"] = {
                k: [t for t in v if t["name"] in config["tasks"][k]]
                for k, v in self.split["splits"].items()
            }

    def spec(self, candidate, partition, stage, task, replicate):
        return {
            "experiment": self.experiment,
            "arm": self.arm,
            "iteration": self.iteration,
            "candidate": candidate.name,
            "partition": partition,
            "stage": stage,
            "task": task,
            "replicate": replicate,
            **(
                {
                    "qualification_revision": self.config[
                        "qualification_revision"
                    ]
                }
                if self.config.get("qualification_revision")
                else {}
            ),
        }

    def collect(self, identity, spec):
        directory = self.jobs / identity
        result_path = directory / "result.json"
        if not result_path.exists():
            return None
        result = json.loads(result_path.read_text())
        trace = directory / "agent/trace.jsonl"
        records = (
            [json.loads(s) for s in trace.read_text().splitlines()]
            if trace.exists()
            else []
        )
        execution_path = directory / "agent/execution.json"
        execution = (
            json.loads(execution_path.read_text())
            if execution_path.exists()
            else {}
        )
        outcome = termination(records, result=result, execution=execution)
        execution = {
            **execution,
            **{k: v for k, v in outcome.items() if k != "kind"},
        }
        execution["api_timeout_only"] = api_timeout_only(execution, records)
        label, raw, reason = oracle_label(result, execution, records)
        if execution["api_timeout_only"]:
            label, reason = None, "api_timeout_infrastructure"
        full_events, _, _ = sanitized_events(
            records, result=result, execution=execution
        )
        detector = claimed_without_ran(full_events, window=5)
        atomic_json(
            self.private / "behavior" / f"{identity}.json",
            {
                "id": identity,
                **spec,
                "claimed_without_ran": detector,
            },
        )
        row = {
            "id": identity,
            **spec,
            "oracle": label,
            "raw_reward": raw,
            "reason": reason,
            "status": "complete",
            "score": None,
            "source": str(result_path),
            "execution": execution,
            "behavior": behavior_flags(records, outcome, result=result),
            "exception_info": result.get("exception_info"),
            "excluded": reason == "grader_failure",
            "exclusion_reason": "grader_failure"
            if reason == "grader_failure"
            else None,
        }
        atomic_json(self.private / f"{identity}.json", row)
        if trace.exists() and spec["partition"] == "search":
            evidence = self.trace_exporter(
                trace,
                self.logs / "exports" / identity,
                result=result,
                execution=execution,
            )
            atomic_json(
                self.logs / "exports" / identity / "evidence.json",
                evidence.to_dict(),
            )
            row["evidence"] = str(
                self.logs / "exports" / identity / "evidence.json"
            )
        if self.arm == "A0" or self.arm == "C-TTS-A0":
            row["score"] = label
        return row

    async def one(self, candidate, spec):
        row = await self._one_once(candidate, spec)
        if row.get("exclusion_reason") == "grader_failure":
            return row
        if row.get("replacement_id") or spec.get("infrastructure_attempt", 0):
            return row
        boundary = self.private / "grading" / row["id"] / "boundary.json"
        if boundary.exists() and (
            row.get("status") in {"infrastructure_failed", "interrupted"}
            or row.get("reason") == "missing_or_invalid_verifier"
        ):
            record = json.loads(boundary.read_text())
            if record.get("live_ready") or record.get("snapshot_ready"):
                from evolution.grading import resume_frozen_grading

                await resume_frozen_grading(
                    self.root, self.logs / "configs" / f"{row['id']}.json"
                )
                row = self.collect(row["id"], spec) or row
                row.update(grader_recovered=True, solver_retried=False)
                self.state.finish(row["id"], row)
            else:
                row.update(
                    excluded=True,
                    exclusion_reason="grader_failure",
                    reason="grader_failure",
                    oracle=None,
                    score=None,
                    solver_retried=False,
                )
                self.state.finish(row["id"], row)
            return row
        execution = row.get("execution", {})
        if execution.get("reason") == "content_policy_rejection":
            return row
        api_only = execution.get(
            "api_timeout_only", api_timeout_only(execution)
        )
        infra = row.get("status") in {"infrastructure_failed", "interrupted"}
        infra = infra or api_only
        if not infra:
            return row
        # The new attempt is linked before dispatch and is never a new task.
        replacement_spec = {
            **spec,
            "infrastructure_attempt": 1,
            "replaces": row["id"],
        }
        replacement_id = self.state.schedule(replacement_spec)
        atomic_json(
            self.private / "infrastructure-retries" / f"{row['id']}.json",
            {
                "original": row,
                "replacement_id": replacement_id,
                "policy": "one_clean_state_replacement",
                "attempt": 1,
            },
        )
        replacement = await self._one_once(candidate, replacement_spec)
        replacement_execution = replacement.get("execution", {})
        excluded = replacement_execution.get(
            "reason"
        ) != "content_policy_rejection" and (
            replacement.get("status")
            in {
                "infrastructure_failed",
                "interrupted",
            }
            or replacement_execution.get(
                "api_timeout_only", api_timeout_only(replacement_execution)
            )
        )
        excluded = excluded or replacement.get("excluded", False)
        result = {
            **replacement,
            "id": row["id"],
            **spec,
            "replacement_id": replacement_id,
            "retried": True,
            "infrastructure_attempt": spec.get("infrastructure_attempt", 0),
            "excluded": excluded,
            "exclusion_reason": (
                replacement.get("exclusion_reason")
                or "infrastructure_retry_exhausted"
            )
            if excluded
            else None,
        }
        if excluded:
            result.update(oracle=None, score=None)
        self.state.finish(row["id"], result)
        atomic_json(self.private / f"{row['id']}.json", result)
        return result

    async def _one_once(self, candidate, spec):
        identity = self.state.schedule(spec)
        row = self.state.row(identity)
        if row["status"] == "done":
            return json.loads(row["result"])
        if row["status"] == "running":
            lease = (
                self.root / "logs/evolution-trial-leases" / f"{identity}.lock"
            )
            if lease.exists():
                # A surviving worker owns its result and trusted evidence
                # until export finishes. Resume waits instead of dispatching
                # again or collecting a result before that export is ready.
                with lease.open("a+") as handle:
                    while True:
                        self.guard.check()
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            await asyncio.sleep(0.2)
                        else:
                            fcntl.flock(handle, fcntl.LOCK_UN)
                            break
        existing = self.collect(identity, spec)
        if self.state.recover(identity, existing) == "done":
            return json.loads(self.state.row(identity)["result"])
        self.guard.check()
        self.state.start(identity)
        configuration = {
            "trial_name": identity,
            "trials_dir": str(self.jobs),
            "task": {
                "path": spec["task"],
                "git_url": self.split["git_url"],
                "git_commit_id": self.split["git_commit"],
                "source": "terminal-bench",
            },
            "environment": {"type": "docker"},
            "agent": {
                "import_path": "evolution.harbor_agent:CandidateAgent",
                "model_name": self.config.get("task_model", {}).get(
                    "deployment", "gpt56terra"
                ),
                "kwargs": {
                    "candidate_path": str(candidate),
                    "harbor_concurrency": self.concurrency,
                    "experiment": self.experiment,
                    "arm": self.arm,
                    "iteration": self.iteration,
                    "trial_id": identity,
                    "accounting_dir": str(self.accounting),
                    "task_settings": self.config.get("task_model", {}),
                    "resolved_provider": self.config.get("providers", {}).get(
                        "task"
                    ),
                    "rollout_budget": self.config.get("budget", {}).get(
                        "rollout_usd", 1
                    ),
                },
            },
        }
        config_path = self.logs / "configs" / f"{identity}.json"
        atomic_json(config_path, configuration)
        started = time.monotonic()
        with (
            config_path.with_suffix(".stdout").open("w") as out,
            config_path.with_suffix(".stderr").open("w") as err,
        ):
            proc = await asyncio.create_subprocess_exec(
                str(self.root / ".venv/bin/python"),
                "-m",
                "evolution.trial_worker",
                str(config_path),
                cwd=self.root,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            try:
                await asyncio.wait_for(
                    proc.wait(), max(1, self.guard.deadline - time.time())
                )
            except (TimeoutError, asyncio.CancelledError):
                proc.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(proc.wait(), 30)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                raise BudgetHalt("Phase wall-clock limit/interruption")
        result = self.collect(identity, spec) or {
            "id": identity,
            **spec,
            "status": "infrastructure_failed",
            "oracle": None,
            "raw_reward": None,
            "score": None,
            "reason": "harbor_no_result",
            "return_code": proc.returncode,
        }
        self.state.finish(identity, result)
        append_jsonl(
            self.accounting / "timings.jsonl",
            {
                "ts": utc_now(),
                "kind": "harbor_trial",
                **spec,
                "id": identity,
                "wall_s": time.monotonic() - started,
                "status": result["status"],
            },
        )
        return result

    async def batch(
        self,
        candidate,
        partition,
        stage,
        attempts=1,
        tasks=None,
        judge=True,
        replicate_start=0,
    ):
        tasks = tasks or [t["name"] for t in self.split["splits"][partition]]
        specs = [
            self.spec(candidate, partition, stage, task, repeat)
            for task in tasks
            for repeat in range(replicate_start, replicate_start + attempts)
        ]
        rows, started = [], time.monotonic()
        results = [None] * len(specs)
        pending = asyncio.Queue()
        for index, spec in enumerate(specs):
            pending.put_nowait((index, spec))
        completed = 0
        judging = (
            judge
            and partition == "search"
            and self.arm not in ("A0", "C-TTS-A0")
        )
        ready = asyncio.Queue(maxsize=2 * self.concurrency)

        async def stream():
            while True:
                row = await ready.get()
                if row is None:
                    return
                yield row

        async def rollouts():
            async with asyncio.TaskGroup() as group:
                for _ in range(min(self.concurrency, len(specs))):
                    group.create_task(worker())
            if judging:
                await ready.put(None)

        async def worker():
            nonlocal completed
            while not pending.empty():
                index, spec = pending.get_nowait()
                self.guard.check()
                # Process-shared admission owns memory hysteresis.
                active = docker(
                    "ps", "--format", "{{.Names}} {{.Image}}"
                ).stdout
                foreign = [
                    s
                    for s in active.splitlines()
                    if "alexgshaw/" in s and not s.startswith("nvhe-")
                ]
                append_jsonl(
                    self.logs / "admissions.jsonl",
                    {
                        "ts": utc_now(),
                        "stage": stage,
                        "partition": partition,
                        "available_gib": available_gib(),
                        "foreign_tb2": len(foreign),
                        "concurrency": min(
                            self.concurrency, 2 if foreign else 4
                        ),
                        "global_worker_guard": True,
                        "paused": False,
                    },
                )
                results[index] = await self.one(candidate, spec)
                if judging:
                    await ready.put(results[index])
                completed += 1
                if partition == "search":
                    print(
                        f"{self.arm} {stage} search: {completed}/{len(specs)}",
                        flush=True,
                    )
                pending.task_done()

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(rollouts())
                if judging:
                    group.create_task(self.score_stream(stream()))
        except* BudgetHalt as failures:
            raise BudgetHalt(
                "Phase guard halted the Harbor queue"
            ) from failures
        rows = results
        if partition == "search" and stage != "smoke":
            self.export_feedback(rows)
        metrics = aggregate(rows)
        metrics["wall_s"] = time.monotonic() - started
        atomic_json(
            (self.logs if partition == "search" else self.private)
            / "batches"
            / f"{stage}-{candidate.name}-{partition}.json",
            {"metrics": metrics, "rows": rows},
        )
        return rows

    async def score(self, rows):
        async def stream():
            for row in rows:
                yield row

        await self.score_stream(stream())
        return rows

    async def score_stream(self, rows):
        async with self.score_lock:
            return await self._score_stream(rows)

    async def _score_stream(self, rows):
        from evolution.judges import JudgeInput

        output = self.logs / "judges"
        endpoint = Endpoint(
            "JUDGE", self.config, output, self.accounting / "unused.json", 30
        )
        kinds = (
            ["a1", "a2"]
            if self.arm.endswith("A4")
            else ["a2" if self.arm.endswith("A2") else "a1"]
        )
        limiter = TokenBucketLimiter(
            endpoint.rpm,
            endpoint.tpm,
            path=self.root / "logs/evolution-endpoints.sqlite",
            endpoint=endpoint.identity,
        )
        backends = []

        def factory(item_id, attempt):
            backend = AccountedBackend(
                base_url=endpoint.base,
                api_key=endpoint.key,
                model=endpoint.model,
                ledger=self.accounting / "ledger.jsonl",
                logs_dir=output / "calls" / item_id / str(attempt),
                timeout_s=endpoint.timeout,
                effort=None,
                max_completion_tokens=endpoint.max_tokens,
                extra_params=endpoint.params,
                max_retries=0,
                budget_usd=1,
                prices_path=Path(endpoint.prices_path),
                guard_path=self.accounting / "budget.sqlite",
                limiter_path=self.root / "logs/evolution-endpoints.sqlite",
                audit_path=self.accounting / "requests.jsonl",
                tags=CallTags(
                    "P1.3",
                    self.experiment,
                    self.arm,
                    self.iteration,
                    item_id,
                    "judge",
                ),
                scope=f"judge-{item_id}-{attempt}",
                rpm=endpoint.rpm,
                tpm=endpoint.tpm,
                max_calls=1,
            )
            backends.append(backend)
            return backend

        # Queue owns retries and recovery; never call judge_once here.
        queue = JudgeQueue(
            output / "queue.sqlite",
            factory,
            limiter,
            endpoint_signature={**endpoint.signature, "arm": self.arm},
            max_tokens=endpoint.max_tokens,
            ledger=self.accounting / "ledger.jsonl",
            archive_root=output / "calls",
            run_id=self.experiment,
            limiter_owner="transport",
        )
        ids, by_rollout, settled = {}, {}, {}

        async def source():
            async for row in rows:
                by_rollout[row["id"]] = row
                if row.get("evidence") and not row.get("excluded"):
                    evidence = JudgeInput.from_dict(
                        json.loads(Path(row["evidence"]).read_text())
                    )
                    ids[row["id"]] = [
                        queue.enqueue(row["id"], k, evidence) for k in kinds
                    ]
                    for kind in kinds:
                        yield row["id"], kind, evidence, 0
                else:
                    self.state.finish(row["id"], row)

        async def ingest(item):
            settled[item["id"]] = (
                json.loads(item["result"]) if item["result"] else None
            )
            row = by_rollout.get(item["rollout"])
            if row is None or not all(i in settled for i in ids[row["id"]]):
                return
            results = [settled[i] for i in ids[row["id"]]]
            row["score"] = None
            if all(r is not None for r in results):
                row["score"] = (
                    mixture(*(r["score"] for r in results))
                    if len(results) == 2
                    else results[0]["score"]
                )
            row["judge_ids"] = ids[row["id"]]
            self.state.finish(row["id"], row)

        try:
            await queue.run(concurrency=4, source=source(), on_result=ingest)
        finally:
            queue.close()
            limiter.close()
            for backend in backends:
                backend.close()
        return list(by_rollout.values())

    def export_feedback(self, rows):
        for row in rows:
            if not row.get("evidence") or row.get("excluded"):
                continue
            evidence = json.loads(Path(row["evidence"]).read_text())
            # Hard allowlist: no oracle, result path, partition, or rationale.
            atomic_json(
                self.feedback / f"{row['id']}.json",
                {
                    "rollout_id": row["id"],
                    "candidate_id": row["candidate"],
                    "score": row.get("score"),
                    **evidence,
                },
            )
