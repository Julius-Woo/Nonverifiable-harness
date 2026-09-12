"""Prepare/check a frozen pilot manifest; dispatch requires all entry gates."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from evolution.a3_manifest import defaults, resolve
from evolution.accounting import PhaseGuard
from evolution.candidates import atomic_json
from evolution.loop import EvolutionLoop
from evolution.manifest import (
    budget_errors,
    file_hash,
    freeze_manifest,
)
from evolution.pilot import preflight, qualification_reports, seal_inputs
from evolution.reconcile import reconcile
from evolution.workspace import ensure_image
from harness.ledger import utc_now


async def execute(
    root,
    manifest,
    *,
    resume=False,
    recover_sessions=False,
    allow_pending_review=False,
):
    """Infrastructure authorization is only exposed by run_evolution."""
    if manifest.get("purpose") == "pilot":
        portable = {
            k: v
            for k, v in manifest.items()
            if k
            not in {
                "resolved_sha256",
                "hashes",
                "providers",
                "tasks",
                "runtime_images",
                "task_images",
                "sampling_seed_support",
                "host_runtime",
            }
        }
        report = preflight(
            root, portable, allow_pending_review=allow_pending_review
        )
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
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
                    else:
                        if (
                            recover_sessions
                            and arm != "A3-loop"
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
                                "standing_metrics": result.get(
                                    "standing_metrics"
                                ),
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
        paths.extend(oracle_root.glob("*/behavior-i*.json"))
        paths.extend(oracle_root.glob("*/behavior/*.json"))
        for path in paths:
            checkpoints[str(path.relative_to(root))] = json.loads(
                path.read_text()
            )
    atomic_json(private / "final_report.json", checkpoints)
    if manifest.get("run_kind") == "qualification":
        qualification_reports(root, manifest, results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prepare", metavar="EXPERIMENT")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-pending-review", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.prepare:
        atomic_json(args.manifest, defaults(args.prepare))
        print("Draft manifest written; no pilot calls made.")
        return 0
    value = json.loads(args.manifest.read_text())
    if value.get("controller_root"):
        controller = (root / value["controller_root"]).resolve()
        if controller != root.resolve():
            script = controller / "scripts/run_pilot.py"
            if not controller.is_dir() or file_hash(script) != value.get(
                "input_hashes", {}
            ).get("scripts/run_pilot.py"):
                raise ValueError(
                    "Frozen controller source is missing or changed"
                )
            arguments = list(sys.argv[1:])
            index = arguments.index("--manifest") + 1
            arguments[index] = str(args.manifest.resolve())
            os.chdir(controller)
            os.execv(
                sys.executable,
                [sys.executable, "-m", "scripts.run_pilot", *arguments],
            )
    if value["purpose"] != "pilot":
        raise ValueError("run_pilot accepts only pilot manifests")
    report = preflight(root, value)
    if args.allow_pending_review and not args.check:
        if value.get("run_kind") != "qualification":
            raise ValueError("Pending-review exception is qualification-only")
        other_errors = [
            error
            for error in report["errors"]
            if error != "R10 re-check pending"
        ]
        if not other_errors:
            if not value.get("review_deviation"):
                timestamp = utc_now()
                record = (
                    f"\n\n**{timestamp} / QUAL-R10:** Leader-authorized "
                    "qualification launch while R10 is pending, using "
                    "`--allow-pending-review`; original ordering required "
                    "completed review. Schedule pressure; affects all eight "
                    "qualification arms only. Discard these results if R10 "
                    "reports an isolation- or label-blocking finding. No "
                    "qualification results observed at authorization.\n"
                )
                with (root / "PREREG.md").open("a") as handle:
                    handle.write(record)
                value["review_deviation"] = {
                    "timestamp": timestamp,
                    "flag": "--allow-pending-review",
                    "discard_on_blocking_finding": True,
                    "prereg_record": "QUAL-R10",
                }
                value = seal_inputs(root, value)
                atomic_json(args.manifest, value)
            report = preflight(root, value, allow_pending_review=True)
    atomic_json(
        root / "logs" / "evolution" / value["experiment"] / "entry_gates.json",
        report,
    )
    print(json.dumps(report, indent=2))
    if report["errors"]:
        print("BLOCKED: " + "; ".join(report["errors"]))
        return 2
    if args.check:
        print("All entry gates passed; no paid calls or Docker calls made.")
        return 0
    # Docker and endpoint resolution are launch-time operations, after gates.
    ensure_image(root)
    resolved = resolve(root, value, dotenv_values(root / ".env"))
    freeze_manifest(root, resolved)
    results = asyncio.run(
        execute(
            root,
            resolved,
            resume=args.resume,
            allow_pending_review=args.allow_pending_review,
        )
    )
    if value.get("run_kind") == "qualification":
        atomic_json(
            root
            / "logs"
            / "evolution"
            / value["experiment"]
            / "qualification.json",
            {
                "passed": len(results) == len(value["arms"])
                and all(r.get("status") == "complete" for r in results),
                "manifest_sha256": value["manifest_sha256"],
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
