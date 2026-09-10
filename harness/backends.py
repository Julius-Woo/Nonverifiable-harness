"""One-shot CLI adapters with raw evidence and accounting on every attempt."""

import asyncio
import json
import math
import os
import signal
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.ledger import CallTags, append_jsonl, price_usage, utc_now

TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)


@dataclass
class Completion:
    text: str
    record: dict
    message: dict | None = None


def parse_claude(stdout: str, model: str) -> tuple[str, dict]:
    data = json.loads(stdout)
    raw = data.get("usage", {})
    cached = raw.get("cache_read_input_tokens", 0)
    written = raw.get("cache_creation_input_tokens", 0)
    total = raw.get("input_tokens")
    models = list(data.get("modelUsage", {}))
    usage = {
        "model": "mixed:" + ",".join(sorted(models))
        if len(models) > 1
        else next(iter(models), model),
        "mixed_models": len(models) > 1,
        "input_tokens": total + cached + written
        if total is not None
        else None,
        "cached_input_tokens": cached,
        "cache_write_tokens": written,
        "output_tokens": raw.get("output_tokens"),
        "reasoning_tokens": None,
        "cost_usd": data.get("total_cost_usd"),
        "api_ms": data.get("duration_api_ms"),
        "premium_requests": 0,
        "provider_ok": not data.get("is_error", False),
    }
    return data.get("result", ""), usage


def parse_codex(stdout: str, model: str) -> tuple[str, dict]:
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    turns = [e["usage"] for e in events if e.get("type") == "turn.completed"]
    messages = [
        e["item"]["text"]
        for e in events
        if e.get("type") == "item.completed"
        and e.get("item", {}).get("type") == "agent_message"
    ]
    usage = {
        key: (
            sum(t[key] for t in turns)
            if turns and all(key in t for t in turns)
            else None
        )
        for key in TOKEN_FIELDS
    }
    # Codex does not expose cache creation separately.
    usage["cache_write_tokens"] = 0 if turns else None
    usage.update(
        model=model,
        premium_requests=0,
        provider_ok=bool(turns)
        and not any(e.get("type") in ("error", "turn.failed") for e in events),
    )
    return messages[-1] if messages else "", usage


def parse_copilot(stdout: str, path: Path, model: str) -> tuple[str, dict]:
    data = json.loads(path.read_text())
    details = data.get("tokenDetails", {})

    def count(key):
        return details.get(key, {}).get("tokenCount")

    uncached, cached, written = (
        count(k) for k in ("input", "cache_read", "cache_write")
    )
    counts = (uncached, cached, written)
    metrics = data.get("modelMetrics", {})
    actual = (
        next(iter(metrics))
        if len(metrics) == 1
        else data.get("currentModel", model)
    )
    usage = {
        "model": actual,
        "input_tokens": sum(counts) if None not in counts else None,
        "cached_input_tokens": cached,
        "cache_write_tokens": written,
        "output_tokens": count("output"),
        "reasoning_tokens": metrics.get(actual, {})
        .get("usage", {})
        .get("reasoningTokens"),
        "premium_requests": data.get("totalPremiumRequestCost"),
        "api_ms": data.get("totalApiDurationMs"),
        "provider_ok": True,
        "mixed_models": len(metrics) > 1,
    }
    return stdout.strip(), usage


class CLIBackend:
    def __init__(
        self,
        backend: str,
        model: str | None,
        ledger: Path,
        logs_dir: Path,
        timeout_s: float = 120,
        effort: str = "low",
        prices_path: Path | None = None,
    ):
        if backend not in ("claude", "codex", "copilot"):
            raise ValueError(f"Unknown backend: {backend}")
        if model is None and backend != "copilot":
            raise ValueError("Only Copilot review supports the CLI default")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        self.backend, self.model = backend, model
        self.ledger, self.logs_dir = Path(ledger), Path(logs_dir)
        self.timeout_s, self.effort = timeout_s, effort
        prices_path = prices_path or (
            Path(__file__).resolve().parents[1] / "scripts/prices.json"
        )
        self.prices = json.loads(prices_path.read_text())

    def command(self, prompt: str, usage_file: Path) -> tuple[list[str], str]:
        if self.backend == "claude":
            return [
                "claude",
                "-p",
                "--model",
                self.model,
                "--output-format",
                "json",
                "--tools",
                "",
                "--safe-mode",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--no-session-persistence",
                "--disable-slash-commands",
            ], prompt
        if self.backend == "codex":
            return [
                "codex",
                "exec",
                "-m",
                self.model,
                "--json",
                "--ephemeral",
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-c",
                'approval_policy="never"',
                "-c",
                f'model_reasoning_effort="{self.effort}"',
                "-c",
                "project_doc_max_bytes=0",
                "-",
            ], prompt
        command = [
            "copilot",
            "--no-custom-instructions",
            "--no-auto-update",
            "--disable-builtin-mcps",
            "--available-tools=",
            "--no-ask-user",
            "--no-remote",
            "--no-remote-export",
            "-s",
            "--effort",
            self.effort,
            "--usage-output-file",
            str(usage_file),
            "--log-dir",
            str(usage_file.parent / "cli-logs"),
            "-p",
            prompt,
        ]
        if self.model:
            command.extend(["--model", self.model])
        return command, ""

    async def complete(self, prompt: str, tags: CallTags) -> Completion:
        if tags.role != "review" and self.model is None:
            raise ValueError("Experiments require an explicit model")
        if tags.role != "review" and any(
            s in (self.model or "").lower() for s in ("fable", "opus")
        ):
            raise ValueError("Use the designated inexpensive experiment model")
        call_id = uuid.uuid4().hex
        raw_dir = self.logs_dir.resolve() / call_id
        raw_dir.mkdir(parents=True)
        usage_file = raw_dir / "usage.json"
        (raw_dir / "prompt.txt").write_text(prompt)
        command, stdin = self.command(prompt, usage_file)
        # Prove the ledger writable before incurring model usage.
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.touch(exist_ok=True)
        record = {
            **asdict(tags),
            "ts": utc_now(),
            "call_id": call_id,
            "backend": self.backend,
            "model": self.model or "cli-default",
            **dict.fromkeys(TOKEN_FIELDS),
            "cost_usd": None,
            "cost_source": "unknown",
            "premium_requests": None,
            "api_ms": None,
            "ok": False,
            "note": "",
            "raw_dir": str(raw_dir),
            "price_table_date": self.prices["checked_at"],
        }
        started = time.monotonic()
        proc = None
        text = ""
        try:
            # Fresh workspaces avoid benchmark files and prior CLI sessions.
            # This is not the Phase 1 filesystem/network isolation boundary.
            with tempfile.TemporaryDirectory(prefix="harness-cli-") as work:
                with (
                    (raw_dir / "stdout.txt").open("wb") as out,
                    (raw_dir / "stderr.txt").open("wb") as err,
                ):
                    proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=work,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=out,
                        stderr=err,
                        start_new_session=True,
                    )
                    try:
                        await asyncio.wait_for(
                            proc.communicate(stdin.encode()), self.timeout_s
                        )
                    except (TimeoutError, asyncio.CancelledError):
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        await proc.wait()
                        raise
            stdout = (raw_dir / "stdout.txt").read_text(errors="replace")
            if self.backend == "claude":
                text, usage = parse_claude(stdout, record["model"])
            elif self.backend == "codex":
                text, usage = parse_codex(stdout, record["model"])
            else:
                text, usage = parse_copilot(
                    stdout, usage_file, record["model"]
                )
            record.update(usage)
            if record["cost_usd"] is not None:
                record["cost_source"] = "reported"
            elif not record.get("mixed_models"):
                record["cost_usd"] = price_usage(
                    record["model"], record, self.prices
                )
                if record["cost_usd"] is not None:
                    record["cost_source"] = "priced"
            record["ok"] = (
                proc.returncode == 0 and usage["provider_ok"] and bool(text)
            )
            if not record["ok"]:
                record["note"] = "CLI failed or returned no successful answer"
        except asyncio.CancelledError:
            record["note"] = "Cancelled; subprocess group killed"
            raise
        except Exception as exc:
            record["note"] = f"{type(exc).__name__}: {exc}"
        finally:
            record["wall_s"] = time.monotonic() - started
            record["return_code"] = proc.returncode if proc else None
            append_jsonl(self.ledger, record)
        return Completion(text, record)
