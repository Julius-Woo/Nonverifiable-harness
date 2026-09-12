"""Run the authorized P1.5 A3-native infrastructure calibration."""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from dotenv import dotenv_values

from evolution.a3_manifest import defaults, resolve, validate
from evolution.candidates import atomic_json, safe_id
from evolution.loop import EvolutionLoop
from evolution.manifest import freeze_manifest
from evolution.reconcile import reconcile


def finished_result(root, experiment):
    """Replay reads frozen state and its offline completion report."""
    directory = Path(root) / "runs" / experiment / "A3-native"
    path = directory / "state.sqlite"
    if not path.exists():
        return None
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT value FROM stages WHERE id='finished-1'"
        ).fetchone()
    if row is None:
        return None
    from evolution.state import State

    result = State.decode(row[0])
    if "optimization_complete" not in result:
        report_path = directory / "completion-report.json"
        if not report_path.exists():
            raise ValueError(
                "Regenerate the offline A3 completion report before replay"
            )
        report = json.loads(report_path.read_text())
        if (
            report["experiment"] != experiment
            or report["decision"] != result["decision"]
        ):
            raise ValueError("Offline completion report identity mismatch")
        result.update(report)
    return result


def print_result(result):
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in (
                    "arm",
                    "decision",
                    "status",
                    "coreset_preference",
                    "J_t",
                    "O_t_search",
                    "cost_upper_usd",
                    "optimization_complete",
                    "measurement_complete",
                    "endpoint_eligible",
                    "search_preferences",
                    "search_measurements",
                    "sealed_measurements",
                )
            }
        )
    )


def native_manifest(
    experiment, *, embedding="azure", budget_usd=60, estimate_usd=50, hours=4
):
    value = defaults(experiment, validation=True)
    value.update(
        arms=["A3-native"],
        T=1,
        partition_limits={},
        candidates_per_arm={"A3-native": 3},
        acceptance="improve",
        seed=1,
    )
    value["budget"].update(
        estimate_usd=estimate_usd,
        guard_usd=budget_usd,
        wall_clock_hours=hours,
        rollout_usd=1,
        evolver_session_usd=5,
    )
    if embedding == "bge":
        value["a3"].update(
            embedding="BAAI/bge-large-en-v1.5",
            embedding_revision="d4aa6901d3a41ba39fb536a557fa166f842b0e09",
        )
    elif embedding != "azure":
        raise ValueError("Unknown embedding implementation")
    value["a3_native_budget"].update(
        estimate_usd=estimate_usd, guard_usd=budget_usd, wall_clock_hours=hours
    )
    validate(value)
    return value


async def execute(root, manifest, *, resume=False):
    validate(manifest)
    budget = manifest["budget"]
    loop_class = EvolutionLoop
    if manifest.get("clean_calibration"):
        from evolution.a3_calibration import CleanLoop

        loop_class = CleanLoop
    loop = loop_class(
        root,
        manifest["experiment"],
        "A3-native",
        manifest=manifest,
        resume=resume,
        rule="improve",
        concurrency=4,
        estimate=budget["estimate_usd"],
        ceiling=budget["guard_usd"],
        hours=budget["wall_clock_hours"],
    )
    try:
        return await loop.run()
    finally:
        loop.close()
        reconcile(root / "costs" / manifest["experiment"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", default="a3-native-260910")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean-calibration", action="store_true")
    parser.add_argument(
        "--embedding", choices=("azure", "bge"), default="azure"
    )
    parser.add_argument("--native-budget-usd", type=float)
    parser.add_argument("--native-estimate-usd", type=float, default=50)
    parser.add_argument("--native-hours", type=float, default=4)
    args = parser.parse_args()
    safe_id(args.experiment)
    root = Path(__file__).resolve().parents[1]
    if args.resume:
        finished = finished_result(root, args.experiment)
        if finished is not None:
            print_result(finished)
            return
    if args.resume:
        value = json.loads(
            (root / "runs" / args.experiment / "manifest.json").read_text()
        )
        validate(value)  # Historical v2 is only admitted by completed replay.
        if (
            args.native_budget_usd is not None
            and args.native_budget_usd != value["budget"]["guard_usd"]
        ):
            raise ValueError("A resumed native run cannot change its budget")
    else:
        if args.native_budget_usd is None:
            parser.error("New calibration requires --native-budget-usd")
        value = native_manifest(
            args.experiment,
            embedding=args.embedding,
            budget_usd=args.native_budget_usd,
            estimate_usd=args.native_estimate_usd,
            hours=args.native_hours,
        )
        if args.clean_calibration:
            from evolution.a3_calibration import project_round

            projection = project_round(root)
            atomic_json(
                root / "runs" / args.experiment / "cost-projection.json",
                projection,
            )
            if not projection["admitted"]:
                raise ValueError(
                    "Full-round projection exceeds USD 55; refusing dispatch"
                )
            if args.embedding != "azure" or args.native_budget_usd != 60:
                raise ValueError(
                    "Clean calibration requires Azure and USD 60 cap"
                )
            value["clean_calibration"] = {
                "pass_label": "L1-prime",
                "search_measurement_attempts": 2,
                "operator_attempt_timeout_s": 900,
                "projection": projection,
                "ratification": "docs/decisions-260912.md AD1/AD10/AD14/AD15",
            }
            value["a3_native_budget"]["status"] = "authorized_AD14"
    if args.resume:
        from evolution.sanitize import canonical, digest

        unsigned = {k: v for k, v in value.items() if k != "resolved_sha256"}
        if digest(canonical(unsigned)) != value.get("resolved_sha256"):
            raise ValueError("Frozen native manifest hash mismatch")
        manifest = value
    else:
        manifest = resolve(root, value, dotenv_values(root / ".env"))
        freeze_manifest(root, manifest)
    atomic_json(
        root / "runs" / args.experiment / "authorization.json",
        {
            "arm": "A3-native",
            "purpose": "infrastructure",
            "authorization": "Explicit native launcher budget parameter",
            "pilot_gates_bypassed": False,
            "pilot_entry": "not applicable to authorized infrastructure",
            "max_usd": manifest["budget"]["guard_usd"],
        },
    )
    result = asyncio.run(execute(root, manifest, resume=args.resume))
    print_result(result)


if __name__ == "__main__":
    main()
