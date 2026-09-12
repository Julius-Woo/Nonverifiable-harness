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

TAU = {"A0": 0.0}
# v1 calibration is historical; v2 thresholds must be supplied explicitly.


def acceptance_decision(
    arm,
    baseline,
    proposed,
    baseline_anchor=None,
    proposed_anchor=None,
    *,
    rule="anchor",
    tau=None,
    epsilon=0,
):
    if baseline is None or proposed is None:
        return False
    signed = (
        SearchEvaluation(
            baseline, proposed, family="task-self", scale="signed_preference"
        )
        if arm in {"A3-native", "A3-loop"}
        else None
    )
    if rule == "improve":
        checked = signed or SearchEvaluation(baseline, proposed)
        return checked.gain > 0
    if baseline_anchor is None or proposed_anchor is None:
        return False
    threshold = TAU.get(arm) if tau is None else tau
    if threshold is None:
        raise ValueError(
            "Anchor acceptance requires an explicit calibrated v2 tau"
        )
    return accept(
        CandidateEvaluation(
            signed
            if signed is not None
            else SearchEvaluation(baseline, proposed),
            AnchorEvaluation(baseline_anchor, proposed_anchor),
        ),
        tau=threshold,
        epsilon=epsilon,
    )


def select_control(rows, signal):
    if signal not in ("A0", "A1", "A2", "A4", "A3-loop", "A3-native"):
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
        manifest=None,
    ):
        self.root, self.experiment, self.arm = (
            Path(root).resolve(),
            safe_id(experiment),
            safe_id(arm),
        )
        self.manifest = manifest or {}
        self.completion_allowance = self.manifest.get("task_model", {}).get(
            "completion_allowance", 8192
        )
        self.candidate_count = self.manifest.get("candidates_per_arm", {}).get(
            arm, 3 if arm.startswith("A3") else 2
        )
        self.epsilon = self.manifest.get("epsilon", 0)
        self.iteration, self.rule, self.tau = iteration, rule, tau
        self.directory = self.root / "runs" / experiment / arm
        if self.directory.exists() and not resume:
            raise ValueError("Arm already exists; pass --resume")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.state = State(
            self.directory / "state.sqlite",
            private=self.root / "oracle" / experiment / arm / "state",
        )
        self.accounting = (
            self.root
            / "costs"
            / self.manifest.get("budget_experiment", experiment)
        )
        self.guard = PhaseGuard(
            self.accounting / "budget.sqlite",
            estimate=estimate,
            ceiling=ceiling,
            hours=hours,
        )
        self.config = {**dotenv_values(self.root / ".env"), **self.manifest}
        if arm in {"A3-native", "A3-loop"}:
            from evolution.a3_control import retry_policy

            policy = retry_policy(self.config)
            saved_policy = self.state.stage("a3-retry-policy")
            if saved_policy is not None and saved_policy != policy:
                raise ValueError("A3 infrastructure-retry policy changed")
            self.state.stage("a3-retry-policy", policy)
        if (
            arm in {"A3-native", "A3-loop", "C-TTS-A3-loop"}
            and evaluator_class is Evaluator
        ):
            from evolution.a3 import A3Evaluator

            evaluator_class = A3Evaluator
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
        self.evaluator.accounting = self.accounting
        self.logs = self.root / "logs/evolution" / experiment / arm
        self.logs.mkdir(parents=True, exist_ok=True)
        configuration = {
            "experiment": experiment,
            "arm": arm,
            "rule": rule,
            "model": self.config.get("task_model", {}).get(
                "deployment", "gpt56terra"
            ),
            "manifest_sha256": self.manifest.get("resolved_sha256"),
            "protocol": "json",
            "tau": tau,
            "iteration": iteration,
            "candidate_count": self.candidate_count,
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
            / ("A1" if self.arm == "A0" else "A0")
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
        if hasattr(self, "a3"):
            from evolution.a3_operators import PROPOSE

            prompt = PROPOSE.replace("8192", str(self.completion_allowance))
        else:
            prompt = render(self.arm, self.completion_allowance)
        (session / "prompt.txt").write_text(prompt)
        tags = CallTags(
            "P1.3",
            self.experiment,
            self.arm,
            self.iteration,
            identity,
            "evolver",
        )
        backend = (
            self.a3.ops.backend("a3-propose", identity, session=True)
            if hasattr(self, "a3")
            else EvolverBackend(
                base_url=self.config["EVOLVER_API_BASE"],
                api_key=self.config["EVOLVER_API_KEY"],
                model=self.config["EVOLVER_MODEL"],
                ledger=self.accounting / "ledger.jsonl",
                logs_dir=session / "calls",
                effort=None,
                max_completion_tokens=8192,
                extra_params={
                    "temperature": 0.6,
                    "top_p": 0.95,
                    "response_format": {"type": "json_object"},
                },
                max_retries=0,
                budget_usd=self.manifest.get("budget", {}).get(
                    "evolver_session_usd", 5
                ),
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
        )
        if hasattr(self, "a3"):
            tags = backend.tags
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
                feedback=self.a3.feedback
                if hasattr(self, "a3")
                else self.evaluator.feedback,
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
                    "experiment": self.experiment,
                    "arm": self.arm,
                    "iteration": self.iteration,
                    **result,
                },
            )
            self.reestimate()
        return result

    def reestimate(self):
        from evolution.accounting import reestimate_after_sessions

        estimate = reestimate_after_sessions(
            self.root, self.manifest, self.accounting
        )
        if estimate and (
            estimate["status"] != "estimated"
            or estimate["projected_solver_plus_evolver_usd"]
            > self.manifest["budget"]["guard_usd"]
        ):
            reason = "First-five-session re-estimate requires budget review"
            self.guard.persist_halt(reason)
            raise BudgetHalt(reason, phase=True)

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
        self.reestimate()
        if self.arm in {"A3-native", "A3-loop"}:
            from evolution.a3 import A3Round

            return await A3Round(self).run()
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
        if self.state.stage("seed-sealed-checkpoint") is None:
            sealed_seed = await self.batch(
                seed, "sealed", "seed-checkpoint", 2
            )
            if any(
                r.get("status") in {"interrupted", "infrastructure_failed"}
                for r in sealed_seed
            ):
                raise RuntimeError("Incomplete t=0 seed checkpoint")
            atomic_json(
                self.evaluator.private / "checkpoint-t0.json",
                {
                    "candidate": str(seed),
                    "source_sha256": source_hash(seed),
                    "metrics": aggregate(sealed_seed),
                    "rows": sealed_seed,
                },
            )
            self.state.stage("seed-sealed-checkpoint", {"complete": True})
        baseline = await self.batch(parent, "search", "baseline")
        proposals = [
            await self.propose(parent, i)
            for i in range(1, self.candidate_count + 1)
        ]
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
                    epsilon=self.epsilon,
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
            run_id=self.experiment,
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
            "epsilon": self.epsilon,
            "baseline": aggregate(baseline),
            "candidates": proposals,
            "promotion": promotion,
            "J_t": metrics["J"],
            "O_t_search": metrics["O"],
            "search_measurement": metrics,
            "wall_s": time.time() - started["time"],
            "costs_iteration": costs,
            "costs_cumulative": cost_summary(
                self.accounting / "ledger.jsonl",
                self.accounting / "requests.jsonl",
                arm=self.arm,
                run_id=self.experiment,
            ),
            "cost_upper_usd": costs["uncached_upper_usd"]
            + costs["reserved_unresolved_usd"],
            "search_measurement_attempts": 1,
            "diff_class": None,
        }
        atomic_json(
            self.evaluator.private / f"checkpoint-t{self.iteration}.json",
            {
                "iteration": self.iteration,
                "incumbent": incumbent.name,
                "O_t_sealed": sealed_metrics["O"],
                "sealed_measurement": sealed_metrics,
            },
        )
        self.state.stage(finished_key, summary)
        # A unique durable stage prevents duplicates; resume repairs this
        # append if interrupted between the DB checkpoint and append.
        self.write_summary(summary)
        return summary

    def write_summary(self, summary):
        from evolution.behavior import standing_metrics

        rows = [
            self.state.decode(r["result"])
            for r in self.state.db.execute(
                "SELECT spec,result FROM trials WHERE result IS NOT NULL"
            )
            if json.loads(r["spec"])["iteration"] == self.iteration
        ]
        summary["standing_metrics"] = standing_metrics(rows)
        self.state.stage(f"finished-{self.iteration}", summary)
        from evolution.behavior import claim_report

        claims = []
        for row in rows:
            if row.get("excluded") or row.get("infrastructure_attempt"):
                continue
            identity = row.get("replacement_id", row["id"])
            path = self.evaluator.private / "behavior" / f"{identity}.json"
            if path.is_file():
                claims.append(
                    json.loads(path.read_text())["claimed_without_ran"]
                )
        atomic_json(
            self.evaluator.private / f"behavior-i{self.iteration}.json",
            {
                "arm": self.arm,
                "iteration": self.iteration,
                "standing_metrics": summary["standing_metrics"],
                "claimed_without_ran": claim_report(claims),
                "partitions": {
                    part: standing_metrics(
                        [r for r in rows if r.get("partition") == part]
                    )
                    for part in ("search", "anchor", "sealed")
                },
            },
        )
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
        if comparator.name != signal:
            raise ValueError(
                "C-TTS comparator must match its selection signal"
            )
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
        if signal == "A3-loop":
            from evolution.a3_control import check_retry_policy

            try:
                check_retry_policy(self, db)
            except ValueError:
                db.close()
                raise
        for row in db.db.execute("SELECT spec FROM trials"):
            spec = json.loads(row[0])
            if spec.get("infrastructure_attempt"):
                continue
            if spec["iteration"] <= self.iteration:
                key = (spec["partition"], spec["task"])
                allocations[key] = allocations.get(key, 0) + 1
        db.close()
        rows = []
        for (partition, task), count in sorted(allocations.items()):
            existing = {}
            for stored in self.state.db.execute(
                "SELECT spec,result FROM trials"
            ):
                spec = json.loads(stored["spec"])
                if (spec["partition"], spec["task"]) == (partition, task):
                    if spec.get("infrastructure_attempt"):
                        continue
                    index = spec["replicate"]
                    if index in existing:
                        raise ValueError("Duplicate control replicate index")
                    if stored["result"]:
                        existing[index] = self.state.decode(stored["result"])
            if any(index >= count for index in existing):
                raise ValueError("Control has exceeded comparator allocation")
            plan_key = f"control-plan-{partition}-{task}-{self.iteration}"
            expected = list(range(count))
            fixed_plan = self.state.stage(plan_key)
            if fixed_plan is not None and fixed_plan != expected:
                raise ValueError("Control allocation changed on resume")
            self.state.stage(plan_key, expected)
            produced = []
            for index in expected:
                if index not in existing:
                    scheduled_specs = [
                        item
                        for stored in self.state.db.execute(
                            "SELECT spec FROM trials"
                        )
                        if (item := json.loads(stored["spec"]))
                        and (item["partition"], item["task"])
                        == (partition, task)
                    ]
                    physical = sum(
                        not item.get("infrastructure_attempt")
                        for item in scheduled_specs
                    )
                    already_scheduled = any(
                        item["replicate"] == index
                        and not item.get("infrastructure_attempt")
                        for item in scheduled_specs
                    )
                    if physical >= count and not already_scheduled:
                        raise ValueError(
                            "C-TTS allocation exhausted by "
                            "infrastructure retries"
                        )
                    new = await self.evaluator.batch(
                        seed,
                        partition,
                        f"control-{task}-{index}",
                        1,
                        tasks=[task],
                        judge=partition == "search",
                        replicate_start=index,
                    )
                    if len(new) != 1 or new[0]["replicate"] != index:
                        raise ValueError("Control rollout identity mismatch")
                    existing[index] = new[0]
                produced.append(existing[index])
            physical = sum(
                (spec["partition"], spec["task"]) == (partition, task)
                and (not spec.get("infrastructure_attempt"))
                for stored in self.state.db.execute("SELECT spec FROM trials")
                if (spec := json.loads(stored["spec"]))
            )
            if physical != count:
                atomic_json(
                    self.evaluator.private / "control-allocation-failure.json",
                    {
                        "partition": partition,
                        "task": task,
                        "expected": count,
                        "actual": physical,
                        "reason": (
                            "Infrastructure replacement changed "
                            "matched allocation"
                        ),
                    },
                )
                raise ValueError(
                    "C-TTS physical allocation mismatch; "
                    "no matched result emitted"
                )
            if partition != "search" and signal != "A0":
                # Build scorer evidence privately; it never reaches feedback.
                from evolution.judge_queue import export_trace

                for row in produced:
                    trace = (
                        self.evaluator.jobs
                        / (row.get("replacement_id", row["id"]))
                        / "agent/trace.jsonl"
                    )
                    if trace.exists():
                        evidence = export_trace(
                            trace,
                            self.evaluator.private / "exports" / row["id"],
                            execution=row.get("execution", {}),
                            result={
                                "exception_info": row.get("exception_info")
                            },
                        )
                        path = (
                            self.evaluator.private
                            / "exports"
                            / row["id"]
                            / "evidence.json"
                        )
                        atomic_json(path, evidence.to_dict())
                        row["evidence"] = str(path)
                if signal != "A3-loop":
                    produced = await self.evaluator.score(produced)
            if signal == "A3-loop":
                from evolution.a3_control import score_control_pool

                produced = await score_control_pool(self, produced, seed)
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
            expected_tasks = sorted(
                task for (part, task) in allocations if part == partition
            )
            slots = [
                (1, task, index)
                for task in expected_tasks
                for index in range(2 if partition == "sealed" else 1)
            ]
            selected_rows = [
                {
                    **r,
                    "seed": 1,
                    "replicate": r["replicate"] % 2
                    if partition == "sealed"
                    else 0,
                }
                for r in chosen
            ]
            selected[partition] = {
                "metrics": aggregate(selected_rows, expected=slots),
                "pool_sizes": {
                    task: [
                        sum(
                            r["task"] == task
                            and (
                                partition != "sealed"
                                or r["replicate"] % 2 == parity
                            )
                            for r in pool
                        )
                        for parity in range(2 if partition == "sealed" else 1)
                    ]
                    for task in expected_tasks
                },
                "selected": [r["id"] for r in chosen],
                "allocated": len(pool),
                "missing_selection_slots": (
                    len({r["task"] for r in pool})
                    * (2 if partition == "sealed" else 1)
                    - len(chosen)
                ),
            }
            if signal == "A3-loop":
                from evolution.a3_metrics import metrics, preference_report

                selected[partition]["metrics"] = metrics(
                    selected_rows, expected=slots
                )
                selected[partition]["preferences"] = preference_report(pool)
                selected[partition]["allocation_unit"] = "logical_rollouts"
                selected[partition]["physical_trials"] = sum(
                    json.loads(r[0])["partition"] == partition
                    for r in self.state.db.execute("SELECT spec FROM trials")
                )
        costs = cost_summary(
            self.accounting / "ledger.jsonl",
            self.accounting / "requests.jsonl",
            arm=self.arm,
            iteration=self.iteration,
            run_id=self.experiment,
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
            "wall_s": time.time() - started["time"],
            "costs_iteration": costs,
            "cost_upper_usd": costs["uncached_upper_usd"]
            + costs["reserved_unresolved_usd"],
            "comparator": str(comparator),
            "allocations": {"search": selected["search"]},
            "candidate": "seed",
            "edited": False,
            **({"scale": "signed_preference"} if signal == "A3-loop" else {}),
            "rollouts": len(rows),
        }
        if signal == "A3-loop":
            from evolution.a3_metrics import completion, preference_report

            sealed_pool = [r for r in rows if r["partition"] == "sealed"]
            completed = completion(
                [r for r in rows if r["partition"] == "search"],
                sealed_pool,
            )
            sealed_preferences = preference_report(sealed_pool)
            if not sealed_preferences["complete"]:
                completed.update(
                    measurement_complete=False, endpoint_eligible=False
                )
            completed["sealed_preference_status_counts"] = sealed_preferences[
                "status_counts"
            ]
            summary.update(
                **completed,
                status="complete"
                if completed["measurement_complete"]
                else "measurement-incomplete",
                allocation_unit="logical_rollouts",
                retry_policy=self.state.stage("a3-control-allocation-policy"),
                physical_trials=self.state.db.execute(
                    "SELECT count(*) FROM trials"
                ).fetchone()[0],
            )
        atomic_json(
            self.evaluator.private / f"control-t{self.iteration}.json",
            selected,
        )
        self.state.stage(finished_key, summary)
        self.write_summary(summary)
        return summary

    def close(self):
        self.state.close()
        self.guard.close()
        self.lock.close()
