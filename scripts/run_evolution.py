"""Run authorized infrastructure validation from an immutable manifest."""

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import dotenv_values

from evolution.accounting import BudgetHalt
from evolution.manifest import freeze_manifest, resolve
from evolution.reconcile import reconcile
from evolution.workspace import ensure_image
from scripts.run_pilot import execute


async def main_async(args):
    root = Path(__file__).resolve().parents[1]
    value = json.loads(args.manifest.read_text())
    if value["purpose"] != "infrastructure":
        raise ValueError(
            "Pilot manifests must use the gated run_pilot launcher"
        )
    ensure_image(root)
    manifest = resolve(root, value, dotenv_values(root / ".env"))
    freeze_manifest(root, manifest)
    try:
        await execute(
            root,
            manifest,
            resume=args.resume,
            recover_sessions=args.recover_sessions,
        )
    except BudgetHalt as exc:
        print(f"HALTED: {exc}", flush=True)
        return 2
    finally:
        reconcile(
            root
            / "costs"
            / manifest.get("budget_experiment", manifest["experiment"])
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--recover-sessions", action="store_true")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
