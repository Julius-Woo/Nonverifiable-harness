"""OpenAI-compatible chat completions with bounded retries and accounting."""

import asyncio
import json
import math
import random
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from harness.backends import TOKEN_FIELDS, Completion
from harness.budget import adjust_budget
from harness.ledger import CallTags, append_jsonl, price_usage, utc_now


def pricing_model(model, prices):
    """Resolve only exact names, explicit aliases, and dated snapshots."""
    model = prices.get("aliases", {}).get(model, model)
    if model in prices["models"]:
        return model
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    return base if base in prices["models"] else model


def retry_delay(headers, attempt):
    """Honor seconds, HTTP dates, and Azure's millisecond retry header."""
    delays = []
    for key, scale in (("retry-after", 1), ("retry-after-ms", 0.001)):
        value = headers.get(key)
        if value is None:
            continue
        try:
            delay = float(value) * scale
        except ValueError:
            try:
                delay = (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay):
            delays.append(max(0, delay))
    return max(delays) if delays else min(30, 2**attempt) + random.random()


class OpenAIAPIBackend:
    """Same complete(prompt, tags) interface as CLIBackend.

    One ledger row per logical call; HTTP attempts and response headers are
    nested in that row. No credentials or request headers are persisted.
    Instances are owned by one rollout and used sequentially.
    """

    is_api = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        ledger: Path,
        logs_dir: Path,
        timeout_s: float = 180,
        effort: str | None = "low",
        max_completion_tokens: int = 4096,
        extra_params: dict | None = None,
        prices_path: Path | None = None,
        max_retries: int = 3,
        budget_usd: float = 1.0,
        transport=None,
        shared_budget_path=None,
        shared_budget_usd=40.0,
    ):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        if max_retries < 0 or not math.isfinite(budget_usd) or budget_usd <= 0:
            raise ValueError("Invalid retry or budget limit")
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/openai/v1"
        self.api_key, self.model = api_key, model
        self.ledger, self.logs_dir = Path(ledger), Path(logs_dir)
        self.timeout_s, self.max_retries = timeout_s, max_retries
        self.budget_usd, self.budget_used = budget_usd, 0.0
        self.transport = transport
        self.shared_budget_path = shared_budget_path
        self.shared_budget_usd = float(shared_budget_usd)
        prices_path = prices_path or (
            Path(__file__).resolve().parents[1] / "scripts/prices.json"
        )
        self.prices = json.loads(prices_path.read_text())
        self.params = dict(extra_params or {})
        if {
            "model",
            "messages",
            "stream",
            "max_tokens",
            "n",
        } & self.params.keys():
            raise ValueError("extra_params cannot override request structure")
        self.params.setdefault("max_completion_tokens", max_completion_tokens)
        if effort is not None:
            self.params.setdefault("reasoning_effort", effort)
        maximum = self.params["max_completion_tokens"]
        if not isinstance(maximum, int) or maximum <= 0:
            raise ValueError(
                "max_completion_tokens must be a positive integer"
            )

    def projected_cost(self, prompt):
        # UTF-8 bytes conservatively bound text tokens, plus message overhead.
        tokens = len(prompt.encode()) + 256
        if tokens > 200_000:
            raise ValueError("Prompt exceeds conservative short-context limit")
        model = pricing_model(self.model, self.prices)
        rates = self.prices["models"].get(model)
        if rates is None:
            raise ValueError("Budget guard requires a known deployment price")
        return (
            tokens * max(rates["input"], rates["cache_write"])
            + self.params["max_completion_tokens"] * rates["output"]
        ) / 1_000_000

    async def complete(
        self, prompt: str, tags: CallTags, *, messages=None, tools=None
    ) -> Completion:
        call_id = uuid.uuid4().hex
        raw_dir = self.logs_dir.resolve() / call_id
        raw_dir.mkdir(parents=True)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.touch(exist_ok=True)
        record = {
            **asdict(tags),
            "ts": utc_now(),
            "call_id": call_id,
            "backend": "openai_api",
            "requested_model": self.model,
            "model": self.model,
            **dict.fromkeys(TOKEN_FIELDS),
            "cost_usd": None,
            "cost_source": "unknown",
            "premium_requests": 0,
            "api_ms": 0.0,
            "ok": False,
            "note": "",
            "raw_dir": str(raw_dir),
            "price_table_date": self.prices["checked_at"],
            "attempts": [],
            "request_params": self.params,
        }
        started, answer, message = time.monotonic(), "", None
        payload = {
            "model": self.model,
            "messages": messages or [{"role": "user", "content": prompt}],
            **self.params,
        }
        if tools is not None:
            payload.update(tools=tools, parallel_tool_calls=False)
        try:
            if not self.api_key or not self.base_url.startswith("http"):
                raise ValueError("API credentials and base URL are required")

            # Defense in depth if a remote response ever echoes a credential.
            def save(name, data):
                encoded = json.dumps(data, indent=2)
                (raw_dir / name).write_text(
                    encoded.replace(self.api_key, "[REDACTED]")
                )

            save("request.json", payload)
            reserve = self.projected_cost(
                json.dumps(payload) if tools is not None else prompt
            )
            async with asyncio.timeout(self.timeout_s):
                async with httpx.AsyncClient(
                    timeout=self.timeout_s, transport=self.transport
                ) as client:
                    for attempt in range(self.max_retries + 1):
                        if self.budget_used + reserve > self.budget_usd:
                            raise ValueError(
                                "Projected rollout budget exceeded"
                            )
                        # Retain reserve on ambiguous HTTP/transport errors.
                        if self.shared_budget_path:
                            adjust_budget(
                                self.shared_budget_path,
                                reserve,
                                self.shared_budget_usd,
                            )
                        self.budget_used += reserve
                        detail = {"attempt": attempt + 1, "ts": utc_now()}
                        record["attempts"].append(detail)
                        api_started = time.monotonic()
                        try:
                            response = await client.post(
                                self.base_url + "/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {self.api_key}"
                                },
                                json=payload,
                            )
                        finally:
                            detail["wall_s"] = time.monotonic() - api_started
                            record["api_ms"] += detail["wall_s"] * 1000
                        detail["status_code"] = response.status_code
                        detail["headers"] = {
                            k: v.replace(self.api_key, "[REDACTED]")
                            for k, v in response.headers.items()
                            if k.startswith("x-ratelimit-")
                            or k
                            in (
                                "retry-after",
                                "retry-after-ms",
                                "x-request-id",
                                "apim-request-id",
                                "x-ms-region",
                            )
                        }
                        if response.status_code == 429:
                            self.budget_used -= reserve
                            if self.shared_budget_path:
                                adjust_budget(
                                    self.shared_budget_path,
                                    -reserve,
                                    self.shared_budget_usd,
                                )
                        if (
                            response.status_code == 429
                            or 500 <= response.status_code < 600
                        ):
                            if attempt < self.max_retries:
                                delay = retry_delay(response.headers, attempt)
                                detail["retry_delay_s"] = delay
                                await asyncio.sleep(delay)
                                continue
                        if response.status_code >= 400:
                            raise RuntimeError(f"HTTP {response.status_code}")
                        data = response.json()
                        save("response.json", data)
                        usage = data.get("usage") or {}
                        prompt_details = (
                            usage.get("prompt_tokens_details") or {}
                        )
                        output_details = (
                            usage.get("completion_tokens_details") or {}
                        )
                        served = data.get("model") or self.model
                        record.update(
                            model=served,
                            served_model=data.get("model"),
                            input_tokens=usage.get("prompt_tokens"),
                            cached_input_tokens=prompt_details.get(
                                "cached_tokens"
                            ),
                            cache_write_tokens=prompt_details.get(
                                "cache_write_tokens",
                                prompt_details.get("cache_creation_tokens", 0),
                            ),
                            output_tokens=usage.get("completion_tokens"),
                            reasoning_tokens=output_details.get(
                                "reasoning_tokens"
                            ),
                        )
                        # Foundry also exposes cache hits at the top level.
                        if record["cached_input_tokens"] is None:
                            record["cached_input_tokens"] = usage.get(
                                "prompt_cache_hit_tokens"
                            )
                        record["pricing_model"] = pricing_model(
                            served, self.prices
                        )
                        record["cost_usd"] = price_usage(
                            record["pricing_model"], record, self.prices
                        )
                        if record["cost_usd"] is not None:
                            record["cost_source"] = "priced"
                            self.budget_used += record["cost_usd"] - reserve
                            if self.shared_budget_path:
                                adjust_budget(
                                    self.shared_budget_path,
                                    record["cost_usd"] - reserve,
                                    self.shared_budget_usd,
                                )
                        choice = data["choices"][0]
                        message = json.loads(
                            json.dumps(choice["message"]).replace(
                                self.api_key, "[REDACTED]"
                            )
                        )
                        answer = message.get("content") or ""
                        answer = answer.replace(self.api_key, "[REDACTED]")
                        record["finish_reason"] = choice.get("finish_reason")
                        record["ok"] = bool(
                            answer or (tools and message.get("tool_calls"))
                        )
                        if not record["ok"]:
                            record["note"] = "API returned no answer"
                        break
        except asyncio.CancelledError:
            record["note"] = "Cancelled during API call or retry backoff"
            raise
        except Exception as exc:
            # Exception text can contain headers/URLs; store only safe classes.
            record["note"] = (
                (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else type(exc).__name__
                ).replace(self.api_key, "[REDACTED]")
                if self.api_key
                else type(exc).__name__
            )
        finally:
            record["wall_s"] = time.monotonic() - started
            record["budget_used_usd"] = self.budget_used
            record["http_429s"] = sum(
                a.get("status_code") == 429 for a in record["attempts"]
            )
            record["unpriced_attempts"] = sum(
                a.get("status_code") is None
                or 500 <= a.get("status_code", 0) < 600
                for a in record["attempts"]
            )
            if record["unpriced_attempts"] and record["cost_usd"] is not None:
                record["known_response_cost_usd"] = record["cost_usd"]
                record["cost_usd"] = None
                record["cost_source"] = "unknown"
            append_jsonl(self.ledger, record)
        return Completion(answer, record, message)
