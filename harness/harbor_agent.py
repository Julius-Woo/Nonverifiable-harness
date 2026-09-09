"""Harbor 0.22 adapter. Task commands execute only via its environment."""

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError

from harness.backends import CLIBackend
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.openai_api import OpenAIAPIBackend
from harness.seed import SeedError, run_seed


class SeedAgent(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        backend: str | None = None,
        ledger_path: str = "costs/ledger.jsonl",
        run_id: str = "smoke-extract-elf",
        phase: str = "P0.3",
        arm: str = "A0",
        iteration: int = 0,
        task_name: str | None = None,
        max_steps: int = 24,
        call_timeout_s: float = 180,
        command_timeout_s: int = 30,
        timing_path: str | None = None,
        endpoint_prefix: str = "TASK",
        reasoning_effort: str = "low",
        max_completion_tokens: int = 4096,
        rollout_budget_usd: float = 1.0,
        **kwargs,
    ):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        backend = backend or os.getenv("HARNESS_BACKEND", "openai_api")
        model_name = (
            model_name
            or os.getenv("HARNESS_MODEL")
            or (
                os.getenv(f"{endpoint_prefix}_MODEL", "gpt-5-mini")
                if backend == "openai_api"
                else "haiku"
                if backend == "claude"
                else "gpt-5.6-luna"
            )
        )
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        common = dict(
            model=model_name,
            ledger=Path(ledger_path).resolve(),
            logs_dir=Path(logs_dir) / "calls",
            timeout_s=float(call_timeout_s),
            effort=reasoning_effort,
        )
        if backend == "openai_api":
            self.backend = OpenAIAPIBackend(
                base_url=os.getenv(f"{endpoint_prefix}_API_BASE", ""),
                api_key=os.getenv(f"{endpoint_prefix}_API_KEY", ""),
                max_completion_tokens=int(max_completion_tokens),
                budget_usd=float(rollout_budget_usd),
                **common,
            )
        else:
            self.backend = CLIBackend(backend=backend, **common)
        task_name = task_name or Path(logs_dir).parent.name.rsplit("__", 1)[0]
        self.tags = CallTags(phase, run_id, arm, iteration, task_name)
        self.max_steps = int(max_steps)
        self.command_timeout_s = int(command_timeout_s)
        self.timing_path = (
            Path(timing_path)
            if timing_path
            else (Path("logs") / run_id / "timing.jsonl")
        )

    @staticmethod
    def name() -> str:
        return "react-seed"

    def version(self) -> str:
        return "0.2.0"

    async def setup(self, environment) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, instruction, environment, context) -> None:
        records = []

        def update(record):
            records.append(record)
            for attr, key in (
                ("n_input_tokens", "input_tokens"),
                ("n_cache_tokens", "cached_input_tokens"),
                ("n_output_tokens", "output_tokens"),
                ("cost_usd", "cost_usd"),
            ):
                values = [r.get(key) for r in records]
                setattr(
                    context, attr, sum(values) if None not in values else None
                )
            context.metadata = {"calls": len(records), "status": "running"}

        started = time.monotonic()
        status = "failed"
        try:
            answer = await run_seed(
                instruction,
                environment,
                self.backend,
                self.tags,
                self.logs_dir / "trace.jsonl",
                max_steps=self.max_steps,
                command_timeout_s=self.command_timeout_s,
                on_completion=update,
            )
            (self.logs_dir / "final.txt").write_text(answer)
            status = "finished"
        except SeedError as exc:
            raise NonZeroAgentExitCodeError(str(exc)) from exc
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            context.metadata = {"calls": len(records), "status": status}
            append_jsonl(
                self.timing_path,
                {
                    "ts": utc_now(),
                    "kind": "agent_rollout",
                    "run_id": self.tags.run_id,
                    "task": self.tags.task,
                    "trial": str(self.logs_dir),
                    "status": status,
                    "wall_s": time.monotonic() - started,
                },
            )
