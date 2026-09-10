"""Prepare/check a frozen pilot manifest; dispatch requires all entry gates."""

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values

from evolution.accounting import PhaseGuard
from evolution.candidates import atomic_json
from evolution.loop import EvolutionLoop
from evolution.manifest import (
    budget_errors,
    defaults,
    entry_errors,
    freeze_manifest,
    resolve,
)
from evolution.reconcile import reconcile
from evolution.workspace import ensure_image


async def execute(root, manifest, *, resume=False, recover_sessions=False):
    """Infrastructure authorization is only exposed by run_evolution."""
    budget = manifest["budget"]
    guard = PhaseGuard(
        root
        / "costs"
        / manifest.get("budget_experiment", manifest["experiment"])
        / "budget.sqlite",
        estimate=budget["estimate_usd"],
        ceiling=budget["guard_usd"],
        hours=budget["wall_clock_hours"],
    )
    try:
        errors = budget_errors(root, manifest)
        if errors:
            raise ValueError("; ".join(errors))
        guard.check()
    finally:
        guard.close()
    results = []
    for seed in manifest["seeds"]:
        experiment = (
            manifest["experiment"]
            if len(manifest["seeds"]) == 1
            else f"{manifest['experiment']}-s{seed}"
        )
        condition = {
            **manifest,
            "budget_experiment": manifest.get(
                "budget_experiment", manifest["experiment"]
            ),
            "seed": seed,
        }
        # Real peer workspaces exist before either arm's first evolver session.
        for arm in manifest["arms"]:
            loop = EvolutionLoop(
                root,
                experiment,
                arm,
                resume=resume,
                manifest=condition,
                rule=manifest["acceptance"],
                estimate=budget["estimate_usd"],
                ceiling=budget["guard_usd"],
                hours=budget["wall_clock_hours"],
                tau=manifest["tau"].get(arm),
            )
            loop.seed()
            loop.close()
        for iteration in range(1, manifest["T"] + 1):
            for arm in manifest["arms"]:
                # Every iteration re-resolves the same frozen
                # condition before dispatch.
                fresh = resolve(
                    root,
                    {
                        k: v
                        for k, v in manifest.items()
                        if k
                        not in {
                            "resolved_sha256",
                            "hashes",
                            "providers",
                            "tasks",
                            "split_sha256",
                            "runtime_images",
                        }
                    },
                    dotenv_values(root / ".env"),
                )
                if fresh != manifest:
                    raise ValueError(
                        "Experimental condition drift before dispatch"
                    )
                loop = EvolutionLoop(
                    root,
                    experiment,
                    arm,
                    resume=True,
                    manifest=condition,
                    rule=manifest["acceptance"],
                    iteration=iteration,
                    tau=manifest["tau"].get(arm),
                    concurrency=manifest["concurrency"],
                    estimate=budget["estimate_usd"],
                    ceiling=budget["guard_usd"],
                    hours=budget["wall_clock_hours"],
                )
                try:
                    if arm.startswith("C-TTS-"):
                        result = await loop.control(
                            root / "runs" / experiment / arm[6:]
                        )
                    elif arm == "A3-loop":
                        raise ValueError(
                            "Reserved A3-loop hook has no implementation"
                        )
                    else:
                        if (
                            recover_sessions
                            and loop.state.stage(f"finished-{iteration}")
                            is None
                            and loop.state.stage(f"checkpoint-{iteration}")
                        ):
                            from evolution.recovery import (
                                recover_sessions as recover,
                            )

                            await recover(loop)
                        result = await loop.run()
                    results.append(result)
                    print(
                        json.dumps(
                            {
                                "arm": arm,
                                "seed": seed,
                                "iteration": iteration,
                                "J_t": result["J_t"],
                                "O_t_search": result["O_t_search"],
                                "decision": result["decision"],
                            }
                        ),
                        flush=True,
                    )
                finally:
                    loop.close()
    reconcile(
        root
        / "costs"
        / manifest.get("budget_experiment", manifest["experiment"])
    )
    atomic_json(
        root / "runs" / manifest["experiment"] / "public_report.json", results
    )
    # Separate oracle-side report; never print or merge into public results.
    private = root / "oracle" / manifest["experiment"]
    checkpoints = {}
    for seed in manifest["seeds"]:
        experiment = (
            manifest["experiment"]
            if len(manifest["seeds"]) == 1
            else f"{manifest['experiment']}-s{seed}"
        )
        oracle_root = root / "oracle" / experiment
        paths = list(oracle_root.glob("*/checkpoint-t*.json"))
        paths.extend(oracle_root.glob("*/control-t*.json"))
        for path in paths:
            checkpoints[str(path.relative_to(root))] = json.loads(
                path.read_text()
            )
    atomic_json(private / "final_report.json", checkpoints)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepare", metavar="EXPERIMENT")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.prepare:
        atomic_json(args.manifest, defaults(args.prepare))
        print("Draft manifest written; no pilot calls made.")
        return 0
    value = json.loads(args.manifest.read_text())
    if value["purpose"] != "pilot":
        raise ValueError("run_pilot accepts only pilot manifests")
    ensure_image(root)
    resolved = resolve(root, value, dotenv_values(root / ".env"))
    freeze_manifest(root, resolved)
    budget = resolved["budget"]
    guard = PhaseGuard(
        root / "costs" / resolved["experiment"] / "budget.sqlite",
        estimate=budget["estimate_usd"],
        ceiling=budget["guard_usd"],
        hours=budget["wall_clock_hours"],
    )
    try:
        guard.check()
        errors = entry_errors(root, resolved)
        atomic_json(
            root / "runs" / resolved["experiment"] / "entry_gates.json",
            {"passed": not errors, "errors": errors, "paid_calls": 0},
        )
        if errors:
            print("BLOCKED: " + "; ".join(errors))
            return 2
        if args.check:
            print("All entry gates passed; no pilot calls made.")
            return 0
        return asyncio.run(execute(root, resolved, resume=args.resume)) and 0
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
