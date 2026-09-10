"""Harbor scheduling, private oracle recording, and queued search judging."""

import asyncio
import fcntl
import json
import signal
import time
from pathlib import Path

from evolution.accounting import AccountedBackend, BudgetHalt
from evolution.candidates import atomic_json
from evolution.judge_queue import (
    Endpoint,
    JudgeQueue,
    TokenBucketLimiter,
    export_trace,
)
from evolution.judges import mixture
from evolution.workspace import docker
from harness.ledger import CallTags, append_jsonl, utc_now


def available_gib():
    lines = Path("/proc/meminfo").read_text().splitlines()
    return (
        int(next(s for s in lines if s.startswith("MemAvailable:")).split()[1])
        / 1024**2
    )


def oracle_label(result, execution, records):
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
        "reward"
    )
    exception = (result.get("exception_info") or {}).get("exception_type", "")
    failed = execution.get("tool_failed", False) or any(
        e.get("kind") == "observation"
        and (
            e.get("error")
            or e.get("protocol_error")
            or e.get("return_code") not in (None, 0)
        )
        for e in records
    )
    if (
        "AgentTimeout" in exception
        or execution.get("status") == "timeout_or_cancelled"
    ):
        return 0, reward, "agent_timeout"
    if type(reward) not in (int, float) or reward not in (0, 1):
        return None, reward, "missing_or_invalid_verifier"
    # The task's explicit rule supersedes PREREG's stricter tool-failure rule.
    # Tool/protocol failures remain recorded diagnostics, not extra vetoes.
    reason = "valid_verifier"
    if failed or exception == "NonZeroAgentExitCodeError":
        reason = "valid_verifier_with_solver_or_tool_failure"
    return int(reward == 1), reward, reason


def aggregate(rows):
    scores = [r["score"] for r in rows if r.get("score") is not None]
    labels = [r["oracle"] for r in rows if r.get("oracle") is not None]
    # All tasks have the same replicate count at a complete checkpoint.
    return {
        "J": sum(scores) / len(scores) if scores else None,
        "O": sum(labels) / len(labels) if labels else None,
        "O_fixed_denominator": sum(labels) / len(rows) if rows else None,
        "scheduled": len(rows),
        "judge_missing": len(rows) - len(scores),
        "oracle_excluded": len(rows) - len(labels),
        "trials": [r["id"] for r in rows],
    }


class Evaluator:
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
        self.jobs = self.root / "logs/harbor" / experiment / arm
        self.private = self.root / "oracle" / experiment / arm
        self.feedback = self.root / "feedback" / arm / experiment
        for path in (self.logs, self.jobs, self.private, self.feedback):
            path.mkdir(parents=True, exist_ok=True)
        self.split = json.loads(
            (self.root / "data/tb2_split.json").read_text()
        )

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
        label, raw, reason = oracle_label(result, execution, records)
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
            "exception_info": result.get("exception_info"),
        }
        atomic_json(self.private / f"{identity}.json", row)
        if trace.exists() and spec["partition"] == "search":
            evidence = export_trace(
                trace,
                self.logs / "exports" / identity,
                observation_chars=12000,
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
                "model_name": "gpt56luna",
                "kwargs": {
                    "candidate_path": str(candidate),
                    "experiment": self.experiment,
                    "arm": self.arm,
                    "iteration": self.iteration,
                    "trial_id": identity,
                    "accounting_dir": str(self.accounting),
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

        async def worker():
            nonlocal completed
            while not pending.empty():
                index, spec = pending.get_nowait()
                self.guard.check()
                while available_gib() < 6:
                    self.guard.check()
                    await asyncio.sleep(30)
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
                completed += 1
                print(
                    f"{self.arm} {stage} {partition}: "
                    f"{completed}/{len(specs)}",
                    flush=True,
                )
                pending.task_done()

        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(min(self.concurrency, len(specs))):
                    group.create_task(worker())
        except* BudgetHalt as failures:
            raise BudgetHalt(
                "Phase guard halted the Harbor queue"
            ) from failures
        rows = results
        if (
            judge
            and partition == "search"
            and self.arm not in ("A0", "C-TTS-A0")
        ):
            rows = await self.score(rows)
        if partition == "search" and stage != "smoke":
            self.export_feedback(rows)
        metrics = aggregate(rows)
        metrics["wall_s"] = time.monotonic() - started
        atomic_json(
            self.logs
            / "batches"
            / f"{stage}-{candidate.name}-{partition}.json",
            {"metrics": metrics, "rows": rows},
        )
        return rows

    async def score(self, rows):
        async with self.score_lock:
            return await self._score(rows)

    async def _score(self, rows):
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
                scope=f"judge-{item_id}",
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
        )
        ids = {}
        try:
            for row in rows:
                if row.get("evidence"):
                    evidence = JudgeInput.from_dict(
                        json.loads(Path(row["evidence"]).read_text())
                    )
                    ids[row["id"]] = [
                        queue.enqueue(row["id"], k, evidence) for k in kinds
                    ]
            await queue.run(concurrency=4)
            completed = {
                r["id"]: json.loads(r["result"]) if r["result"] else None
                for r in queue.rows()
            }
            for row in rows:
                results = [completed[i] for i in ids.get(row["id"], [])]
                if results and all(r is not None for r in results):
                    row["score"] = (
                        mixture(*(r["score"] for r in results))
                        if len(results) == 2
                        else results[0]["score"]
                    )
                    row["judge_ids"] = ids[row["id"]]
                self.state.finish(row["id"], row)
        finally:
            queue.close()
            limiter.close()
            for backend in backends:
                backend.close()
        return rows

    def export_feedback(self, rows):
        for row in rows:
            if not row.get("evidence"):
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
