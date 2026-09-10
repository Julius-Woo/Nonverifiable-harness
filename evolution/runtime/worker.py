"""Networkless candidate process. All external effects use bounded host RPC."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/candidate")


def rpc(kind, **values):
    sys.__stdout__.write(json.dumps({"kind": kind, **values}) + "\n")
    sys.__stdout__.flush()
    data = json.loads(sys.__stdin__.readline())
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


class Backend:
    is_api = True

    async def complete(self, prompt, tags, **kwargs):
        data = rpc("complete", prompt=prompt)
        return SimpleNamespace(**data)


class Environment:
    async def exec(self, command, timeout_sec=30):
        return SimpleNamespace(
            **rpc("exec", command=command, timeout_sec=timeout_sec)
        )


async def main():
    config = json.loads(sys.__stdin__.readline())
    from harness.ledger import CallTags
    from harness.seed import run_seed

    try:
        answer = await run_seed(
            config["instruction"],
            Environment(),
            Backend(),
            CallTags(task="task"),
            Path("/tmp/trace.jsonl"),
            max_steps=24,
            command_timeout_s=30,
            tool_protocol="json",
        )
        rpc("finish", answer=answer)
    except Exception as exc:
        rpc("failure", error=f"{type(exc).__name__}: {exc}")


asyncio.run(main())
