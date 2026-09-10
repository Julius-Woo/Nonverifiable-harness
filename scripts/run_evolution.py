"""Run isolated, API-driven evolution or a matched seed-only C-TTS arm."""

import argparse
import asyncio
import json
from pathlib import Path

from evolution.accounting import BudgetHalt
from evolution.loop import EvolutionLoop
from evolution.workspace import ensure_image


async def main_async(args):
    root = Path(__file__).resolve().parents[1]
    ensure_image(root)
    for arm in args.arms:
        loop = EvolutionLoop(
            root,
            args.experiment,
            arm,
            resume=args.resume,
            rule=args.acceptance,
            concurrency=args.n_concurrent,
            estimate=args.estimate_usd,
            ceiling=args.budget_usd,
            hours=args.hours,
            iteration=args.iteration,
            tau=args.tau,
        )
        try:
            if args.resume and not args.recover_sessions:
                from evolution.recovery import (
                    reconcile_labels,
                    reconcile_paused,
                )

                reconcile_paused(loop)
                reconcile_labels(loop)
            if args.recover_sessions:
                from evolution.recovery import recover_sessions

                await recover_sessions(loop)
            if arm.startswith("C-TTS-"):
                if not args.compare:
                    raise ValueError(
                        "C-TTS requires --compare <arm workspace>"
                    )
                result = await loop.control(args.compare)
            else:
                result = await loop.run()
            print(json.dumps(result, indent=2), flush=True)
        except BudgetHalt as exc:
            print(f"HALTED: {exc}", flush=True)
            return 2
        finally:
            loop.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument(
        "--arms",
        nargs="+",
        default=["A0", "A1"],
        choices=[
            "A0",
            "A1",
            "A2",
            "A4",
            "C-TTS-A0",
            "C-TTS-A1",
            "C-TTS-A2",
            "C-TTS-A4",
        ],
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--recover-sessions", action="store_true")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument(
        "--acceptance", choices=["anchor", "improve"], default="anchor"
    )
    parser.add_argument(
        "--n-concurrent", type=int, choices=range(1, 5), default=4
    )
    parser.add_argument("--estimate-usd", type=float, default=20)
    parser.add_argument("--budget-usd", type=float, default=30)
    parser.add_argument("--hours", type=float, default=4)
    parser.add_argument("--tau", type=float)
    parser.add_argument("--compare", type=Path)
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
