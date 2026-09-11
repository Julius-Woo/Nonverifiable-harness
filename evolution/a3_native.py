"""Run the authorized P1.5 A3-native infrastructure calibration."""

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values

from evolution.candidates import atomic_json, safe_id
from evolution.loop import EvolutionLoop
from evolution.manifest import defaults, freeze_manifest, resolve
from evolution.reconcile import reconcile


def native_manifest(experiment, *, embedding="azure"):
    value = defaults(experiment, validation=True)
    value.update(
        arms=["A3-native"],
        T=1,
        partition_limits={},
        candidates_per_arm={"A3-native": 3},
        acceptance="improve",
        seed=1,
        evidence_version="sanitized-trajectory-v2",
    )
    value["budget"].update(
        estimate_usd=20,
        guard_usd=20,
        wall_clock_hours=4,
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
    return value


async def execute(root, manifest, *, resume=False):
    loop = EvolutionLoop(
        root,
        manifest["experiment"],
        "A3-native",
        manifest=manifest,
        resume=resume,
        rule="improve",
        concurrency=4,
        estimate=20,
        ceiling=20,
        hours=4,
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
    parser.add_argument(
        "--embedding", choices=("azure", "bge"), default="azure"
    )
    args = parser.parse_args()
    safe_id(args.experiment)
    root = Path(__file__).resolve().parents[1]
    if args.resume:
        from evolution.state import State

        state_path = root / "runs" / args.experiment / "A3-native/state.sqlite"
        if state_path.exists():
            state = State(state_path)
            try:
                finished = state.stage("finished-1")
            finally:
                state.close()
            if finished is not None:
                print(
                    json.dumps(
                        {
                            k: finished[k]
                            for k in (
                                "arm",
                                "decision",
                                "coreset_preference",
                                "J_t",
                                "O_t_search",
                                "cost_upper_usd",
                            )
                        }
                    )
                )
                return
    value = native_manifest(args.experiment, embedding=args.embedding)
    manifest = resolve(root, value, dotenv_values(root / ".env"))
    freeze_manifest(root, manifest)
    atomic_json(
        root / "runs" / args.experiment / "authorization.json",
        {
            "arm": "A3-native",
            "purpose": "infrastructure",
            "authorization": "User P1.5 task; single native calibration",
            "pilot_gates_bypassed": False,
            "pilot_entry": "not applicable to authorized infrastructure",
            "max_usd": 20,
        },
    )
    result = asyncio.run(execute(root, manifest, resume=args.resume))
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "arm",
                    "decision",
                    "coreset_preference",
                    "J_t",
                    "O_t_search",
                    "cost_upper_usd",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
