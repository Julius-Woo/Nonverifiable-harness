"""Resumable trusted evolution controller and matched seed-only C-TTS arm."""

import asyncio
import fcntl
import json
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import dotenv_values

from evolution.acceptance import (
    AnchorEvaluation,
    CandidateEvaluation,
    SearchEvaluation,
    accept,
)
from evolution.accounting import (
    BudgetHalt,
    PhaseGuard,
    cost_summary,
)
from evolution.candidates import (
    Manifest,
    atomic_json,
    copy_seed,
    diff_source,
    freeze,
    safe_id,
    scan_source,
    source_hash,
    working_copy,
)
from evolution.evaluation import Evaluator, aggregate
from evolution.prompts import render
from evolution.recovery import EvolverBackend
from evolution.state import State
from evolution.workspace import Workspace
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.seed import SeedError, run_seed

TAU = {"A0": 0.0, "A1": 0.02184135419534282, "A2": 0.022566773346210982}


def acceptance_decision(
    arm,
    baseline,
    proposed,
    baseline_anchor=None,
    proposed_anchor=None,
    *,
    rule="anchor",
    tau=None,
):
    if baseline is None or proposed is None:
        return False
    if rule == "improve":
        return proposed > baseline
    if baseline_anchor is None or proposed_anchor is None:
        return False
    threshold = TAU.get(arm) if tau is None else tau
    if threshold is None:
        raise ValueError(
            "A4 anchor acceptance requires a calibrated mixture tau"
        )
    return accept(
        CandidateEvaluation(
            SearchEvaluation(baseline, proposed),
            AnchorEvaluation(baseline_anchor, proposed_anchor),
        ),
        tau=threshold,
        epsilon=0,
    )


def select_control(rows, signal):
    if signal not in ("A0", "A1", "A2", "A4"):
        raise ValueError("Unsupported C-TTS signal")
    selected = []
    for task in sorted({r["task"] for r in rows}):
        available = [
            r for r in rows if r["task"] == task and r.get("score") is not None
        ]
        if not available:
            continue
        selected.append(
            sorted(available, key=lambda r: (-r["score"], r["id"]))[0]
        )
    return selected


class EvolutionLoop:
    def __init__(
        self,
        root,
        experiment,
        arm,
        *,
        resume=False,
        rule="anchor",
        concurrency=4,
        estimate=20,
        ceiling=30,
        hours=4,
        iteration=1,
        tau=None,
        evaluator_class=Evaluator,
    ):
        self.root, self.experiment, self.arm = (
            Path(root).resolve(),
            safe_id(experiment),
            safe_id(arm),
        )
        self.iteration, self.rule, self.tau = iteration, rule, tau
        self.directory = self.root / "runs" / experiment / arm
        if self.directory.exists() and not resume:
            raise ValueError("Arm already exists; pass --resume")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state = State(self.directory / "state.sqlite")
        self.accounting = self.root / "costs" / experiment
        self.guard = PhaseGuard(
            self.accounting / "budget.sqlite",
            estimate=estimate,
            ceiling=ceiling,
            hours=hours,
        )
        self.config = dotenv_values(self.root / ".env")
        self.evaluator = evaluator_class(
            self.root,
            experiment,
            arm,
            iteration,
            self.state,
            self.guard,
            self.config,
            concurrency=concurrency,
        )
        self.logs = self.root / "logs/evolution" / experiment / arm
        self.logs.mkdir(parents=True, exist_ok=True)
        configuration = {
            "experiment": experiment,
            "arm": arm,
            "rule": rule,
            "model": "gpt56luna",
            "protocol": "json",
            "tau": tau,
            "iteration": iteration,
            "candidate_count": 2,
        }
        key = f"configuration-{iteration}"
        previous = self.state.stage(key)
        if previous and previous != configuration:
            raise ValueError(
                "Cannot change a resumed experiment configuration"
            )
        self.state.stage(key, configuration)
        measurement_key = f"measurement-policy-{iteration}"
        policy = {"search": 1, "sealed": 2, "promotion": 2}
        previous_policy = self.state.stage(measurement_key)
        if previous_policy is not None and previous_policy != policy:
            raise ValueError("Cannot change a resumed measurement allocation")
        self.state.stage(measurement_key, policy)

    def seed(self, source=None):
        accepted = self.directory / "candidates"
        accepted.mkdir(exist_ok=True)
        seed = accepted / "seed"
        if not seed.exists():
            work = copy_seed(
                source or self.root / "harness",
                self.directory / "seed-staging",
            )
            manifest = Manifest(
                "seed",
                None,
                self.arm,
                0,
                "seed",
                "Unchanged seed package",
                source_hash(work),
            )
            manifest.write(work)
            freeze(work, seed, manifest)
            shutil.rmtree(work)
        return seed

    def canaries(self):
        base = self.root / "oracle" / self.experiment / "canaries"
        base.mkdir(parents=True, exist_ok=True)
        paths = [base / "oracle.txt", base / "anchor.txt", base / "sealed.txt"]
        other = (
            self.root
            / "runs"
            / self.experiment
            / "isolation-peer"
            / "canary.txt"
        )
        other.parent.mkdir(parents=True, exist_ok=True)
        paths.append(other)
        for path in paths:
            if not path.exists():
                path.write_text("NVH_SECRET_" + uuid.uuid4().hex)
        return paths, [p.read_text() for p in paths]

    async def propose(self, parent, slot):
        identity = f"i{self.iteration:02}-c{slot}"
        key = f"proposal-{identity}"
        previous = self.state.stage(key)
        if previous:
            if previous["status"] == "running":
                previous.update(
                    status="invalid", reason="interrupted_evolver_no_retry"
                )
                self.state.stage(key, previous)
            return previous
        session_id = f"{self.experiment}-{self.arm}-{identity}"
        work = working_copy(parent, self.directory / "working" / identity)
        session = self.logs / "sessions" / identity
        session.mkdir(parents=True)
        prompt = render(self.arm)
        (session / "prompt.txt").write_text(prompt)
        tags = CallTags(
            "P1.3",
            self.experiment,
            self.arm,
            self.iteration,
            identity,
            "evolver",
        )
        backend = EvolverBackend(
            base_url=self.config["EVOLVER_API_BASE"],
            api_key=self.config["EVOLVER_API_KEY"],
            model=self.config["EVOLVER_MODEL"],
            ledger=self.accounting / "ledger.jsonl",
            logs_dir=session / "calls",
            effort=None,
            max_completion_tokens=4096,
            extra_params={
                "temperature": 0.6,
                "top_p": 0.95,
                "response_format": {"type": "json_object"},
            },
            max_retries=0,
            budget_usd=5,
            timeout_s=180,
            prices_path=self.root / "costs/judges_prices.json",
            guard_path=self.accounting / "budget.sqlite",
            limiter_path=self.root / "logs/evolution-endpoints.sqlite",
            audit_path=self.accounting / "requests.jsonl",
            tags=tags,
            scope=session_id,
            rpm=float(self.config.get("EVOLVER_RPM", 250)),
            tpm=float(self.config.get("EVOLVER_TPM", 250000)),
        )
        result = {
            "id": identity,
            "parent": parent.name,
            "status": "running",
            "session_id": session_id,
            "started_at": utc_now(),
        }
        self.state.stage(key, result)
        started = time.monotonic()
        paths, values = self.canaries()
        try:
            with Workspace(
                work,
                f"nvhe-evo-{self.arm.lower()}-{identity}",
                feedback=self.evaluator.feedback,
                writable=True,
                audit=session / "boundary.json",
            ) as workspace:
                canary_ok = await workspace.canary_check(
                    paths, values, session / "canaries.jsonl"
                )
                if not canary_ok:
                    raise RuntimeError("Evolver isolation check failed")
                answer = await run_seed(
                    prompt,
                    workspace,
                    backend,
                    tags,
                    session / "trace.jsonl",
                    max_steps=24,
                    tool_protocol="json",
                )
                result["answer"] = answer
                result["canary_ok"] = await workspace.canary_check(
                    paths, values, session / "canaries-after.jsonl"
                )
                result["import"] = await workspace.import_check()
            task_names = [
                t["name"]
                for tasks in self.evaluator.split["splits"].values()
                for t in tasks
            ]
            problems = scan_source(
                work, self.directory / "candidates/seed/harness", task_names
            )
            diff = diff_source(parent, work)
            (session / "diff.patch").write_text(diff)
            result["scan_failures"] = problems
            if not diff:
                problems.append("unchanged_candidate")
            if not result["canary_ok"] or not result["import"]["ok"]:
                problems.append("import_or_boundary_failed")
            if problems:
                result.update(status="invalid", reason=",".join(problems))
            else:
                manifest = Manifest(
                    identity,
                    parent.name,
                    self.arm,
                    self.iteration,
                    session_id,
                    answer[:2000],
                    source_hash(work),
                )
                manifest.write(work)
                destination = self.directory / "candidates" / identity
                freeze(work, destination, manifest)
                result.update(
                    status="valid",
                    candidate=str(destination),
                    manifest=asdict(manifest),
                )
        except BudgetHalt:
            result.update(
                status="halted", reason="evolver_or_phase_budget_guard"
            )
            self.state.stage(key, result)
            raise
        except (SeedError, RuntimeError, ValueError) as exc:
            result.update(status="invalid", reason=str(exc))
        finally:
            backend.close()
            result["wall_s"] = time.monotonic() - started
            self.state.stage(key, result)
            atomic_json(session / "validation.json", result)
            append_jsonl(
                self.accounting / "timings.jsonl",
                {
                    "ts": utc_now(),
                    "kind": "evolver",
                    "arm": self.arm,
                    "iteration": self.iteration,
                    **result,
                },
            )
        return result

    async def batch(self, candidate, partition, stage, attempts=1, **kwargs):
        key = f"batch-{self.iteration}-{candidate.name}-{partition}-{stage}"
        previous = self.state.stage(key)
        if previous is not None:
            return previous
        rows = await self.evaluator.batch(
            candidate, partition, stage, attempts, **kwargs
        )
        self.state.stage(key, rows)
        return rows

    async def run(self):
        finished_key = f"finished-{self.iteration}"
        previous = self.state.stage(finished_key)
        if previous:
            self.write_summary(previous)
            return previous
        started = self.state.stage(f"started-{self.iteration}")
        if started is None:
            started = {
                "time": time.time(),
                "costs": cost_summary(
                    self.accounting / "ledger.jsonl",
                    self.accounting / "requests.jsonl",
                ),
            }
            self.state.stage(f"started-{self.iteration}", started)
        seed = self.seed()
        incumbent_state = self.state.stage("incumbent") or {
            "candidate": str(seed)
        }
        parent = Path(incumbent_state["candidate"])
        # Checkpoint the full incumbent; proposals never mutate it.
        checkpoint = self.state.stage(f"checkpoint-{self.iteration}")
        if checkpoint is None:
            checkpoint = incumbent_state
            self.state.stage(f"checkpoint-{self.iteration}", checkpoint)
        parent = Path(checkpoint["candidate"])
        baseline = await self.batch(parent, "search", "baseline")
        proposals = [await self.propose(parent, i) for i in (1, 2)]
        candidates = []

        async def evaluate_proposal(proposal):
            if proposal["status"] != "valid":
                return
            candidate = Path(proposal["candidate"])
            smoke = await self.batch(
                candidate,
                "search",
                "smoke",
                tasks=["cancel-async-tasks"],
                judge=False,
            )
            # Smoke validates the runtime contract, never task success.
            smoke_ok = all(
                r.get("execution", {}).get("calls", 0) > 0
                and r.get("execution", {}).get("status") == "finished"
                for r in smoke
            )
            proposal["smoke_ok"] = smoke_ok
            if not smoke_ok:
                proposal.update(
                    status="invalid", reason="one_task_smoke_failed"
                )
                return
            rows = await self.batch(candidate, "search", "screen")
            metrics = aggregate(rows)
            proposal["search"] = metrics
            if metrics["J"] is not None and not metrics["judge_missing"]:
                candidates.append((metrics["J"], candidate.name, candidate))

        await asyncio.gather(*(evaluate_proposal(p) for p in proposals))
        winner = (
            sorted(candidates, key=lambda item: (-item[0], item[1]))[0][2]
            if candidates
            else None
        )
        accepted = False
        promotion = {}
        if winner:
            # Fresh paired avg@2 confirmation for both, as PREREG specifies.
            incumbent_confirm, candidate_confirm = await asyncio.gather(
                self.batch(parent, "search", "promotion-incumbent", 2),
                self.batch(winner, "search", "promotion-candidate", 2),
            )
            left, right = (
                aggregate(incumbent_confirm),
                aggregate(candidate_confirm),
            )
            promotion = {"incumbent": left, "candidate": right}
            left_anchor = right_anchor = None
            if self.rule == "anchor":
                left_rows, right_rows = await asyncio.gather(
                    self.batch(parent, "anchor", "acceptance", 1),
                    self.batch(winner, "anchor", "acceptance", 1),
                )
                left_anchor_result = aggregate(left_rows)
                right_anchor_result = aggregate(right_rows)
                left_anchor = (
                    None
                    if left_anchor_result["oracle_excluded"]
                    else left_anchor_result["O"]
                )
                right_anchor = (
                    None
                    if right_anchor_result["oracle_excluded"]
                    else right_anchor_result["O"]
                )
            if not left["judge_missing"] and not right["judge_missing"]:
                accepted = acceptance_decision(
                    self.arm,
                    left["J"],
                    right["J"],
                    left_anchor,
                    right_anchor,
                    rule=self.rule,
                    tau=self.tau,
                )
            atomic_json(
                self.evaluator.private / f"acceptance-i{self.iteration}.json",
                {
                    "baseline_anchor": left_anchor,
                    "candidate_anchor": right_anchor,
                    "accepted": accepted,
                    "rule": self.rule,
                },
            )
        incumbent = winner if accepted else parent
        update = (
            {
                "candidate": str(incumbent),
                "source_sha256": source_hash(incumbent),
            }
            if accepted
            else checkpoint
        )
        self.state.stage("incumbent", update)
        atomic_json(self.directory / "incumbent.json", update)
        # Fresh incumbent search curve and exactly one avg@2 sealed checkpoint.
        measured, sealed = await asyncio.gather(
            self.batch(incumbent, "search", "measurement", 1),
            self.batch(incumbent, "sealed", "measurement", 2),
        )
        metrics, sealed_metrics = aggregate(measured), aggregate(sealed)
        costs = cost_summary(
            self.accounting / "ledger.jsonl",
            self.accounting / "requests.jsonl",
            arm=self.arm,
            iteration=self.iteration,
        )
        summary = {
            "experiment": self.experiment,
            "arm": self.arm,
            "iteration": self.iteration,
            "status": "complete",
            "parent": parent.name,
            "incumbent": incumbent.name,
            "winner": winner.name if winner else None,
            "accepted": accepted,
            "decision": "accepted" if accepted else "rejected",
            "rule": self.rule,
            "tau": TAU.get(self.arm) if self.tau is None else self.tau,
            "epsilon": 0,
            "baseline": aggregate(baseline),
            "candidates": proposals,
            "promotion": promotion,
            "J_t": metrics["J"],
            "O_t_search": metrics["O"],
            "O_t_sealed": sealed_metrics["O"],
            "search_measurement": metrics,
            "sealed_measurement": sealed_metrics,
            "wall_s": time.time() - started["time"],
            "costs_iteration": costs,
            "costs_cumulative": cost_summary(
                self.accounting / "ledger.jsonl",
                self.accounting / "requests.jsonl",
                arm=self.arm,
            ),
            "cost_upper_usd": costs["uncached_upper_usd"]
            + costs["reserved_unresolved_usd"],
            "search_measurement_attempts": 1,
            "diff_class": None,
        }
        self.state.stage(finished_key, summary)
        # A unique durable stage prevents duplicates; resume repairs this
        # append if interrupted between the DB checkpoint and append.
        self.write_summary(summary)
        return summary

    def write_summary(self, summary):
        path = self.directory / "evolution_summary.jsonl"
        existing = (
            [json.loads(s) for s in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        if not any(r["iteration"] == self.iteration for r in existing):
            append_jsonl(path, summary)
        atomic_json(self.logs / f"iteration-{self.iteration}.json", summary)

    async def control(self, comparator):
        """Match comparator trial allocations by partition and per-task ID.

        Sealed selection uses two disjoint replicate pools. The seed receives
        no pool scores; C-TTS signal selection happens only after all solves.
        """
        comparator = Path(comparator).resolve()
        comparator_key = f"control-comparator-{self.iteration}"
        fixed = self.state.stage(comparator_key)
        if fixed is not None and fixed != str(comparator):
            raise ValueError("A resumed control cannot change its comparator")
        self.state.stage(comparator_key, str(comparator))
        finished_key = f"finished-{self.iteration}"
        previous = self.state.stage(finished_key)
        if previous is not None:
            self.write_summary(previous)
            return previous
        started_key = f"started-{self.iteration}"
        started = self.state.stage(started_key)
        if started is None:
            started = {"time": time.time()}
            self.state.stage(started_key, started)
        signal = self.arm.removeprefix("C-TTS-")
        comparator_seed = comparator / "candidates/seed"
        seed = self.seed(comparator_seed / "harness")
        if source_hash(seed) != Manifest.read(comparator_seed).source_sha256:
            raise ValueError("C-TTS must use its comparator's frozen seed")
        comparator_state = Path(comparator) / "state.sqlite"
        if not comparator_state.exists():
            raise ValueError("Comparator state does not exist")
        db = State(comparator_state)
        if db.stage(f"finished-{self.iteration}") is None:
            db.close()
            raise ValueError("Comparator iteration must finish before C-TTS")
        allocations = {}
        for row in db.db.execute("SELECT spec FROM trials"):
            spec = json.loads(row[0])
            if spec["iteration"] <= self.iteration:
                key = (spec["partition"], spec["task"])
                allocations[key] = allocations.get(key, 0) + 1
        db.close()
        rows = []
        for (partition, task), count in sorted(allocations.items()):
            existing = []
            for stored in self.state.db.execute(
                "SELECT spec,result FROM trials"
            ):
                spec = json.loads(stored["spec"])
                if (spec["partition"], spec["task"]) == (partition, task):
                    if stored["result"]:
                        existing.append(json.loads(stored["result"]))
            if len(existing) > count:
                raise ValueError("Control has exceeded comparator allocation")
            produced = list(existing)
            if len(existing) < count:
                produced += await self.batch(
                    seed,
                    partition,
                    f"control-{task}",
                    count - len(existing),
                    tasks=[task],
                    judge=partition == "search",
                    replicate_start=len(existing),
                )
            if partition != "search" and signal != "A0":
                # Build scorer evidence privately; it never reaches feedback.
                from evolution.judge_queue import export_trace

                for row in produced:
                    trace = (
                        self.evaluator.jobs / row["id"] / "agent/trace.jsonl"
                    )
                    if trace.exists():
                        evidence = export_trace(
                            trace,
                            self.logs / "exports" / row["id"],
                            observation_chars=12000,
                        )
                        path = (
                            self.logs / "exports" / row["id"] / "evidence.json"
                        )
                        atomic_json(path, evidence.to_dict())
                        row["evidence"] = str(path)
                produced = await self.evaluator.score(produced)
            rows.extend(produced)
        selected = {}
        for partition in ("search", "anchor", "sealed"):
            pool = [r for r in rows if r["partition"] == partition]
            if partition == "sealed":
                chosen = [
                    r
                    for parity in (0, 1)
                    for r in select_control(
                        [r for r in pool if r["replicate"] % 2 == parity],
                        signal,
                    )
                ]
            else:
                chosen = select_control(pool, signal)
            selected[partition] = {
                "metrics": aggregate(chosen),
                "selected": [r["id"] for r in chosen],
                "allocated": len(pool),
                "missing_selection_slots": (
                    len({r["task"] for r in pool})
                    * (2 if partition == "sealed" else 1)
                    - len(chosen)
                ),
            }
        costs = cost_summary(
            self.accounting / "ledger.jsonl",
            self.accounting / "requests.jsonl",
            arm=self.arm,
            iteration=self.iteration,
        )
        summary = {
            "experiment": self.experiment,
            "arm": self.arm,
            "iteration": self.iteration,
            "status": "complete",
            "decision": "control",
            "accepted": None,
            "J_t": selected["search"]["metrics"]["J"],
            "O_t_search": selected["search"]["metrics"]["O"],
            "O_t_sealed": selected["sealed"]["metrics"]["O"],
            "wall_s": time.time() - started["time"],
            "costs_iteration": costs,
            "cost_upper_usd": costs["uncached_upper_usd"]
            + costs["reserved_unresolved_usd"],
            "comparator": str(comparator),
            "allocations": selected,
            "candidate": "seed",
            "edited": False,
            "rollouts": len(rows),
        }
        self.state.stage(finished_key, summary)
        self.write_summary(summary)
        return summary

    def close(self):
        self.state.close()
        self.guard.close()
        self.lock.close()
