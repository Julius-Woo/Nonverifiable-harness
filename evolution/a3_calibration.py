"""Clean native calibration: prospective cost gates and the AD10 label."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from evolution.a3 import A3Evaluator
from evolution.accounting import BudgetHalt
from evolution.candidates import atomic_json
from evolution.loop import EvolutionLoop
from evolution.outcomes import termination
from evolution.sanitize import canonical, digest
from evolution.state import State
from harness.ledger import append_jsonl, utc_now


def stage_costs(root, experiment):
    """Attribute uncached receipts and unresolved reservations to stages."""
    root = Path(root)
    database = root / "runs" / experiment / "A3-native/state.sqlite"
    specs = {}
    if database.exists():
        with sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            specs = {
                i: json.loads(s)
                for i, s in db.execute("SELECT id,spec FROM trials")
            }
    accounting = root / "costs" / experiment
    audit = accounting / "requests.jsonl"
    events = (
        [json.loads(s) for s in audit.read_text().splitlines()]
        if audit.exists()
        else []
    )
    intents = {e["id"]: e for e in events if e["event"] == "request_intent"}
    responses = {
        e["id"]: e for e in events if e["event"] == "request_response"
    }
    for path in (accounting / "requests").glob("*/receipt.json"):
        row = json.loads(path.read_text())
        responses[row["id"]] = row
    totals = defaultdict(lambda: {"usd": 0.0, "requests": 0, "unresolved": 0})
    for identity, row in intents.items():
        spec = specs.get(row["task"], {})
        if spec:
            stage = spec["stage"]
            if stage == "measurement":
                stage = "measurement-" + spec["partition"]
        elif row.get("operation") == "embeddings":
            stage = "embeddings"
        elif row["role"] == "a3-diagnose":
            stage = (
                "difficulty"
                if row["task"].startswith("difficulty-")
                else "diagnosis"
            )
        else:
            stage = row["role"]
        cost = responses.get(identity, {}).get("uncached_upper_usd")
        totals[stage]["unresolved"] += cost is None
        totals[stage]["usd"] += row["reserved_usd"] if cost is None else cost
        totals[stage]["requests"] += 1
    return dict(totals)


def project_round(root, previous="a3-native-260910"):
    """Scale the historical stages to all 3 proposals and search avg@2."""
    costs = stage_costs(root, previous)
    allocation = {
        "a3-prior": (18, 18),
        "difficulty": (18, 18),
        "embeddings": (1, 1),
        "a3-group": (30, 30),
        "diagnosis": (10, 10),
        "a3-propose": (3, 3),
        "a3-after": (10, 30),
        "a3-rank": (28, 66),
        "measurement-search": (18, 36),
        # Four historical sealed slots were censored: scale from 8 to 12.
        "measurement-sealed": (8, 12),
    }
    stages = {}
    for stage, (old, new) in allocation.items():
        historical = costs[stage]["usd"]
        stages[stage] = {
            "previous_usd": historical,
            "previous_units": old,
            "planned_units": new,
            "projected_usd": historical * new / old * 1.5,
            "unit_usd": historical / old * 1.5,
        }
    total = sum(s["projected_usd"] for s in stages.values())
    return {
        "created_at": utc_now(),
        "previous_experiment": previous,
        "method": (
            "Uncached receipts + unresolved reservations; "
            "allocation scaling; 50% contingency"
        ),
        "stages": stages,
        "projected_usd": total,
        "dispatch_threshold_usd": 55,
        "cap_usd": 60,
        "admitted": total <= 55,
    }


def l1_prime(result, execution, records):
    """Recovered errors are observations; the terminal event decides AD10."""
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get(
        "reward"
    )
    flags = termination(records, result=result, execution=execution)
    exception = result.get("exception_info") or {}
    if "cyber_policy" in canonical(exception) or "content_policy" in canonical(
        exception
    ):
        return 0, reward, "provider_content_policy_rejection"
    if flags["agent_timeout"]:
        return 0, reward, "agent_timeout"
    if flags["reason"] != "normal_finish" or exception:
        return (
            0,
            reward,
            flags["reason"] if not exception else "trial_exception",
        )
    if type(reward) not in (int, float) or reward not in (0, 1):
        return None, reward, "missing_or_invalid_verifier"
    return int(reward == 1), reward, "valid_verifier_L1_prime"


class PrivateState(State):
    """Native state stores references; all underlying records stay private."""

    def encode(self, identity, value):
        path = self.private / (digest(identity) + ".json")
        atomic_json(path, value)
        return canonical({"private_ref": str(path)})


class CleanEvaluator(A3Evaluator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logs = self.private / "evaluation"
        self.logs.mkdir(parents=True, exist_ok=True)

    def collect(self, identity, spec):
        row = super().collect(identity, spec)
        if row is None:
            return None
        directory = self.jobs / identity
        result = json.loads((directory / "result.json").read_text())
        trace = directory / "agent/trace.jsonl"
        records = (
            [json.loads(s) for s in trace.read_text().splitlines()]
            if trace.exists()
            else []
        )
        label, raw, reason = l1_prime(result, row["execution"], records)
        from evolution.behavior import behavior_flags

        row["behavior"] = behavior_flags(
            records,
            termination(records, result=result, execution=row["execution"]),
        )
        if row["reason"] == "api_timeout_infrastructure":
            label, reason = None, row["reason"]
        row.update(
            oracle=label, raw_reward=raw, reason=reason, pass_label="L1-prime"
        )
        row.pop("measurement_status", None)
        atomic_json(self.private / f"{identity}.json", row)
        return row


class CleanLoop(EvolutionLoop):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, evaluator_class=CleanEvaluator, **kwargs)
        path, private = self.state.path, self.state.private
        self.state.close()
        self.state = PrivateState(path, private=private)
        self.evaluator.state = self.state

    def stage_boundary(self, stage, units):
        plan = self.manifest["clean_calibration"]["projection"]
        projected = plan["stages"][stage]["unit_usd"] * units
        used = self.guard.used()
        row = {
            "ts": utc_now(),
            "stage": stage,
            "units": units,
            "used_usd": used,
            "projected_stage_usd": projected,
            "cap_usd": self.guard.limit,
            "admitted": used + projected <= self.guard.limit,
        }
        append_jsonl(self.directory / "stage-admissions.jsonl", row)
        if not row["admitted"]:
            atomic_json(
                self.directory / "completion-report.json",
                {
                    "status": "incomplete",
                    "reason": "stage_projection_exceeds_cap",
                    "stopped_before": stage,
                    "censoring": "none",
                    "optimization_complete": False,
                    "measurement_complete": False,
                    "endpoint_eligible": False,
                },
            )
            raise BudgetHalt(
                "Clean native stage boundary budget stop", phase=True
            )
        self.guard.check()

    async def batch(self, candidate, partition, stage, attempts=1, **kwargs):
        key = f"batch-{self.iteration}-{candidate.name}-{partition}-{stage}"
        if self.state.stage(key) is None:
            tasks = (
                kwargs.get("tasks")
                or self.evaluator.split["splits"][partition]
            )
            cost_stage = (
                f"measurement-{partition}" if stage == "measurement" else stage
            )
            self.stage_boundary(cost_stage, len(tasks) * attempts)
        return await super().batch(
            candidate, partition, stage, attempts, **kwargs
        )

    async def propose(self, parent, slot):
        if self.state.stage(f"proposal-i{self.iteration:02}-c{slot}") is None:
            self.stage_boundary("a3-propose", 1)
        return await super().propose(parent, slot)
