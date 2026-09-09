"""One paid, Docker-independent call using the production adapter."""

import argparse
import asyncio
from pathlib import Path

from harness.backends import CLIBackend
from harness.ledger import CallTags


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("claude", "codex", "copilot"))
    parser.add_argument("--model")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--role", choices=("other", "review"), default="other")
    parser.add_argument("--run-id", default="p03-backend-probe")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    model = args.model
    if model is None and args.role != "review":
        model = "haiku" if args.backend == "claude" else "gpt-5.6-luna"
    backend = CLIBackend(
        args.backend,
        model,
        Path("costs/ledger.jsonl"),
        Path("logs") / args.run_id / "calls",
        timeout_s=args.timeout,
        effort="high" if args.role == "review" else "low",
    )
    prompt = (
        args.prompt_file.read_text()
        if args.prompt_file
        else "Reply with exactly OK. Do not use tools."
    )
    reply = await backend.complete(
        prompt, CallTags(run_id=args.run_id, role=args.role)
    )
    print(reply.text or reply.record["note"])
    print(f"ok={reply.record['ok']} raw_dir={reply.record['raw_dir']}")
    return 0 if reply.record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
