"""Trusted Harbor adapter: candidate Python executes in a networkless peer."""

import asyncio
import json
import time
from pathlib import Path

from dotenv import dotenv_values
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError

from evolution.accounting import AccountedBackend
from evolution.candidates import Manifest, atomic_json, source_hash
from evolution.workspace import Workspace
from harness.ledger import CallTags, append_jsonl, utc_now
from harness.seed import action_command

ROOT = Path(__file__).resolve().parents[1]


class CandidateAgent(BaseAgent):
    def __init__(
        self,
        logs_dir,
        model_name=None,
        *,
        candidate_path,
        experiment,
        arm,
        iteration,
        trial_id,
        accounting_dir,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.candidate = Path(candidate_path)
        manifest = Manifest.read(self.candidate)
        if source_hash(self.candidate) != manifest.source_sha256:
            raise ValueError("Immutable candidate digest mismatch")
        self.identity = trial_id
        self.logs_dir = Path(logs_dir)
        self.evidence_dir = (
            ROOT
            / "logs/evolution"
            / experiment
            / arm
            / "task-evidence"
            / trial_id
        )
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.accounting = Path(accounting_dir)
        config = dotenv_values(ROOT / ".env")
        tags = CallTags(
            "P1.3", experiment, arm, int(iteration), trial_id, "task"
        )
        self.backend = AccountedBackend(
            base_url=config["TASK_ALT2_API_BASE"],
            api_key=config["TASK_ALT2_API_KEY"],
            model=model_name or "gpt56luna",
            ledger=self.accounting / "ledger.jsonl",
            logs_dir=self.evidence_dir / "calls",
            effort="low",
            max_completion_tokens=4096,
            max_retries=0,
            budget_usd=1,
            prices_path=ROOT / "costs/judges_prices.json",
            guard_path=self.accounting / "budget.sqlite",
            limiter_path=ROOT / "logs/evolution-endpoints.sqlite",
            audit_path=self.accounting / "requests.jsonl",
            tags=tags,
            scope=trial_id,
            rpm=float(config.get("TASK_ALT2_RPM", 250)),
            tpm=float(config.get("TASK_ALT2_TPM", 250000)),
        )
        self.tags = tags
        self.step = -1
        self.tool_failed = False
        self.tool_calls = 0

    @staticmethod
    def name():
        return "isolated-candidate"

    def version(self):
        return "1.0.0"

    async def setup(self, environment):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        # Hidden scripts are uploaded only after the candidate process stops.
        probe = await environment.exec(
            "for p in /tests/test.sh /tests/test_outputs.py "
            "/solution/solve.sh "
            "/oracle /app/.env /var/run/docker.sock; do "
            'if [ -e "$p" ]; then printf \'unexpected:%s\\n\' "$p"; fi; done',
            timeout_sec=30,
        )
        atomic_json(
            self.logs_dir / "task_boundary.json",
            {
                "hidden_paths_absent": not bool((probe.stdout or "").strip()),
                "probe_stdout": probe.stdout,
            },
        )
        if (probe.stdout or "").strip():
            raise RuntimeError("Hidden artifact exists before task execution")

    def event(self, row):
        append_jsonl(self.evidence_dir / "trace.jsonl", row)

    async def handle(self, message, environment, context):
        kind = message.get("kind")
        if kind == "complete":
            prompt = message["prompt"]
            if not isinstance(prompt, str) or len(prompt.encode()) > 180000:
                raise ValueError("Invalid/oversized candidate API prompt")
            self.step += 1
            reply = await self.backend.complete(prompt, self.tags)
            self.event(
                {
                    "kind": "assistant",
                    "step": self.step,
                    "text": reply.text,
                    "ok": reply.record["ok"],
                    "call_id": reply.record["call_id"],
                }
            )
            for attr, key in (
                ("n_input_tokens", "input_tokens"),
                ("n_output_tokens", "output_tokens"),
            ):
                setattr(
                    context,
                    attr,
                    (getattr(context, attr, 0) or 0)
                    + (reply.record.get(key) or 0),
                )
            try:
                action = json.loads(reply.text)
                if not isinstance(action, dict):
                    raise ValueError("Expected a JSON object")
                if action.get("action") == "finish":
                    if not isinstance(action.get("answer"), str):
                        raise ValueError("answer must be a string")
                else:
                    action_command(action)
            except (ValueError, KeyError, TypeError) as exc:
                self.tool_failed = True
                self.event(
                    {
                        "kind": "observation",
                        "step": self.step,
                        "protocol_error": str(exc),
                    }
                )
            # Candidate never receives host paths, accounting, model settings,
            # trial identity, or API credentials from a Completion record.
            return {
                "text": reply.text,
                "message": reply.message,
                "record": {
                    "call_id": str(self.step),
                    "ok": reply.record["ok"],
                    "note": reply.record.get("note", ""),
                },
            }
        if kind == "exec":
            self.tool_calls += 1
            if self.tool_calls > 96:
                raise ValueError("Host-enforced tool-call cap reached")
            command = message["command"]
            if not isinstance(command, str) or len(command.encode()) > 100000:
                raise ValueError("Invalid candidate shell command")
            started = time.monotonic()
            try:
                result = await environment.exec(command, timeout_sec=30)
                row = {
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                    "return_code": result.return_code,
                }
                if result.return_code:
                    self.tool_failed = True
            except Exception as exc:
                self.tool_failed = True
                row = {"error": str(exc)}
            self.event(
                {
                    "kind": "observation",
                    "step": self.step,
                    "command": command,
                    **row,
                    "wall_s": time.monotonic() - started,
                }
            )
            if "error" in row:
                return {"error": row["error"]}
            return {
                k: v[:12000] if isinstance(v, str) else v
                for k, v in row.items()
            }
        if kind == "finish":
            answer = message.get("answer")
            if not isinstance(answer, str) or len(answer) > 200000:
                raise ValueError("Invalid final answer")
            self.event({"kind": "finish", "answer": answer})
            (self.evidence_dir / "final.txt").write_text(answer)
            return {}
        if kind == "failure":
            raise NonZeroAgentExitCodeError(str(message.get("error"))[:1000])
        raise ValueError("Unknown candidate RPC operation")

    async def run(self, instruction, environment, context):
        started, status = time.monotonic(), "failed"
        self.event({"kind": "instruction", "text": instruction})
        name = "nvhe-task-" + self.identity[-48:]
        proc = None
        try:
            with Workspace(
                self.candidate,
                name,
                audit=self.evidence_dir / "runtime_boundary.json",
            ):
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "python3",
                    "-u",
                    "/opt/worker.py",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=256000,
                )
                proc.stdin.write(
                    (json.dumps({"instruction": instruction}) + "\n").encode()
                )
                await proc.stdin.drain()
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        raise NonZeroAgentExitCodeError(
                            "Candidate runtime exited"
                        )
                    message = json.loads(line)
                    reply = await self.handle(message, environment, context)
                    proc.stdin.write((json.dumps(reply) + "\n").encode())
                    await proc.stdin.drain()
                    if message["kind"] == "finish":
                        status = "finished"
                        break
        except asyncio.CancelledError:
            status = "timeout_or_cancelled"
            raise
        except Exception as exc:
            raise NonZeroAgentExitCodeError(str(exc)) from exc
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            self.event({"kind": "termination", "status": status})
            context.metadata = {
                "status": status,
                "calls": self.backend.calls,
                "tool_failed": self.tool_failed,
                "tool_calls": self.tool_calls,
            }
            atomic_json(
                self.evidence_dir / "execution.json",
                {
                    "status": status,
                    "calls": self.backend.calls,
                    "tool_failed": self.tool_failed,
                    "tool_calls": self.tool_calls,
                },
            )
            append_jsonl(
                self.accounting / "timings.jsonl",
                {
                    "ts": utc_now(),
                    "kind": "task",
                    "trial_id": self.identity,
                    "arm": self.tags.arm,
                    "iteration": self.tags.iteration,
                    "status": status,
                    "wall_s": time.monotonic() - started,
                },
            )
            self.backend.close()
