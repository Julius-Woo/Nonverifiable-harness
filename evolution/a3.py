"""One RHO Algorithm 1 round, reused by native calibration and A3-loop."""

import asyncio
import json
import time
from pathlib import Path

from evolution.a3_coreset import embed_fingerprints, select_coreset
from evolution.a3_inspection import difficulty_digest
from evolution.a3_metrics import (
    annotate_measurement,
    completion,
    measurement_report,
    metrics,
    pair_record,
    phase_censored,
    preference_report,
)
from evolution.a3_operators import (
    Operators,
    evidence,
    first_baselines,
    mean_preference,
    preference_total,
    source_evidence,
)
from evolution.accounting import BudgetHalt, cost_summary
from evolution.candidates import atomic_json, source_hash
from evolution.evaluation import Evaluator
from evolution.judge_queue import export_trace
from evolution.sanitize import digest

ARMS = {"A3-native", "A3-loop"}


class A3Evaluator(Evaluator):
    """RHO scoring is paired and orchestrated after solves; never call A1."""

    trace_exporter = staticmethod(export_trace)

    def export_feedback(self, rows):
        for row in rows:
            if row.get("evidence"):
                evidence(row)
        return super().export_feedback(rows)

    def __init__(
        self, root, experiment, arm, iteration, state, guard, config, **kwargs
    ):
        from evolution.a3_manifest import require_v3

        require_v3(config)
        super().__init__(
            root, experiment, arm, iteration, state, guard, config, **kwargs
        )

    async def _one_once(self, candidate, spec):
        row = annotate_measurement(await super()._one_once(candidate, spec))
        self.state.finish(row["id"], row)
        return row

    async def one(self, candidate, spec):
        row = annotate_measurement(await super().one(candidate, spec))
        self.state.finish(row["id"], row)
        return row

    async def batch(
        self, candidate, partition, stage, attempts=1, tasks=None, **kwargs
    ):
        kwargs["judge"] = False
        tasks = tasks or [t["name"] for t in self.split["splits"][partition]]
        try:
            rows = await super().batch(
                candidate, partition, stage, attempts, tasks=tasks, **kwargs
            )
        except BudgetHalt:
            # The shared queue may stop before scheduling every logical slot.
            # Account for all slots without dispatching or granting a retry.
            rows = []
            start = kwargs.get("replicate_start", 0)
            for task in tasks:
                for repeat in range(start, start + attempts):
                    spec = self.spec(candidate, partition, stage, task, repeat)
                    identity = self.state.schedule(spec)
                    stored = self.state.row(identity)
                    if stored["result"]:
                        row = json.loads(stored["result"])
                    else:
                        row = {
                            "id": identity,
                            **spec,
                            "oracle": None,
                            "score": None,
                            "status": "budget_halted",
                            "budget_halt": True,
                            "censored": stored["status"] == "running",
                        }
                    row = annotate_measurement(row)
                    self.state.finish(identity, row)
                    rows.append(row)
        rows = [annotate_measurement(row) for row in rows]
        atomic_json(
            (self.logs if partition == "search" else self.private)
            / "batches"
            / f"{stage}-{candidate.name}-{partition}.json",
            {"metrics": metrics(rows), "rows": rows},
        )
        return rows

    async def score(self, rows):
        raise ValueError("A3 requires explicit paired self-preference inputs")


async def bounded_map(function, values, concurrency=4):
    semaphore = asyncio.Semaphore(concurrency)

    async def one(value):
        async with semaphore:
            return await function(value)

    results = await asyncio.gather(
        *(one(value) for value in values), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


class A3Round:
    def __init__(self, loop, *, operators=None, embedder=None):
        self.loop = loop
        self.ops = operators or Operators(loop)
        self.embedder = embedder
        self.feedback = (
            loop.root
            / "feedback"
            / loop.arm
            / loop.experiment
            / f"a3-i{loop.iteration:02}"
        )
        self.feedback.mkdir(parents=True, exist_ok=True)
        self.loop.a3 = self

    def boundary(self, stage, units):
        if hasattr(self.loop, "stage_boundary"):
            self.loop.stage_boundary(stage, units)

    async def coreset(self, seed):
        loop = self.loop
        fixed = loop.state.stage("a3-coreset")
        tasks = sorted(
            t["name"] for t in loop.evaluator.split["splits"]["search"]
        )
        if len(tasks) < 10:
            raise ValueError("A3 needs at least ten search tasks")
        if fixed is not None:
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
            descriptions, vectors, seed=loop.manifest.get("seed", 1)
        )
        fixed = {
            "arm": loop.arm,
            "evidence_version": "v3",
            "search_tasks": tasks,
            "tasks": selected,
            "k": 10,
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

    async def diagnoses(self, parent, tasks, groups):
        self.boundary("diagnosis", len(tasks))

        async def diagnose(task):
            rows = sorted(
                [r for r in groups if r["task"] == task],
                key=lambda r: r["replicate"],
            )
            if len(rows) != 3 or [r["replicate"] for r in rows] != [0, 1, 2]:
                raise ValueError(
                    "RHO diagnosis requires G=3 distinct re-solves"
                )
            if any(row.get("excluded") for row in rows):
                return None
            traces = [evidence(row) for row in rows]
            if len({t["task_text"] for t in traces}) != 1:
                raise ValueError("Group task text mismatch")
            result = await self.ops.call(
                "diagnose",
                task,
                {
                    "task_text": traces[0]["task_text"],
                    "harness": source_evidence(parent),
                    "trajectories": [item["trajectory"] for item in traces],
                },
            )
            if result is None:
                raise ValueError("Diagnosis failed after A9 retry")
            for index, trace in enumerate(traces):
                atomic_json(
                    self.feedback / "traces" / task / f"{index}.json", trace
                )
            return {"task_text": traces[0]["task_text"], "diagnosis": result}

        diagnosed = await bounded_map(diagnose, tasks)
        diagnosed = [item for item in diagnosed if item is not None]
        diagnosed.sort(key=lambda item: -item["diagnosis"]["severity"])
        atomic_json(self.feedback / "diagnoses.json", diagnosed)
        return diagnosed

    async def rank_rows(self, stage, rows, references, candidate, baseline):
        self.boundary("a3-rank", len(rows))

        async def rank(row):
            reference = references.get(row["task"])
            row = annotate_measurement(row)
            score = None
            if reference is not None and reference.get("excluded"):
                row["reference_excluded"] = True
            if reference is not None and phase_censored(reference):
                row["reference_censored"] = True
            if row["measurement_status"] == "unscored-budget":
                row["rank_budget_halt"] = True
            elif (
                not row.get("excluded")
                and not (reference or {}).get("excluded")
                and not phase_censored(row)
                and not row.get("reference_censored")
                and reference is not None
            ):
                try:
                    score = await self.ops.rank(
                        digest(f"{stage}:{row['id']}:{reference['id']}")[:24],
                        row,
                        reference,
                        candidate,
                        baseline,
                    )
                except BudgetHalt:
                    row["rank_budget_halt"] = True
            result = {
                **row,
                "score": score,
                "scale": "signed_preference",
                "reference_id": reference["id"] if reference else None,
                "pair_order": "candidate_A_reference_B",
            }
            pair = pair_record(result)
            return {
                **result,
                "pair_status": pair["status"],
                "raw_rating": pair["raw_rating"],
            }

        scored = await bounded_map(rank, rows)
        path = (
            (
                self.loop.evaluator.private
                if self.loop.manifest.get("clean_calibration")
                else self.loop.logs
            )
            / "a3"
            / f"i{self.loop.iteration:02}"
            / f"{stage}.json"
        )
        atomic_json(
            path,
            {"arm": self.loop.arm, "rows": scored, "metrics": metrics(scored)},
        )
        self.loop.evaluator.export_feedback(scored)
        return scored

    async def run(self):
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
                [r["score"] for r in scored]
            )
            proposal["pair_scores"] = [r["score"] for r in scored]
            proposal["raw_ratings"] = [r["raw_rating"] for r in scored]
            proposal["signed_total"] = preference_total(
                proposal["raw_ratings"]
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
