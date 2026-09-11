"""Azure embeddings through the shared accounted transport and guard."""

import asyncio
import json
import math
import time
import uuid

import httpx

from evolution.accounting import AccountedBackend, AuditTransport, BudgetHalt
from evolution.candidates import atomic_json
from evolution.sanitize import canonical, digest
from harness.ledger import CallTags, append_jsonl, utc_now

MODEL = "text-embedding-3-large"
INPUT_RATE = 0.13


class AccountedEmbeddingBackend(AccountedBackend):
    is_embedding = True

    async def embed(self, texts, dimensions=1024):
        if not texts or any(not isinstance(s, str) or not s for s in texts):
            raise ValueError("Embedding inputs must be nonempty strings")
        self.guard.check()
        raw = self.logs_dir.resolve() / uuid.uuid4().hex
        raw.mkdir(parents=True)
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": dimensions,
            "encoding_format": "float",
            "user": digest(self.scope + raw.name),
        }
        atomic_json(raw / "request.json", payload)
        transport = AuditTransport(
            inner=self.external_transport or httpx.AsyncHTTPTransport(),
            backend=self,
            tags=self.tags,
            guard=self.guard,
            limiter=self.limiter,
            audit=self.audit_path,
            scope=self.scope,
            scope_limit=self.budget_usd,
        )
        try:
            async with asyncio.timeout(
                max(0, self.guard.deadline - time.time())
            ):
                async with httpx.AsyncClient(
                    transport=transport, timeout=self.timeout_s
                ) as client:
                    response = await client.post(
                        self.base_url + "/embeddings",
                        headers={"Authorization": "Bearer " + self.api_key},
                        json=payload,
                    )
            if not response.is_success:
                raise ValueError(
                    f"Embedding HTTP status {response.status_code}"
                )
            data = response.json()
            self._check_served_model(data["model"])
            return normalize_vectors(data, len(texts), dimensions), {
                "model": self.model,
                "served_model": data["model"],
                "dimensions": dimensions,
                "normalization": "L2",
                "raw_dir": str(raw),
                "input_rate_usd_per_million": INPUT_RATE,
            }
        except TimeoutError as exc:
            self.guard.persist_halt("Phase wall-clock limit reached")
            raise BudgetHalt(
                "Phase wall-clock limit reached", phase=True
            ) from exc
        finally:
            # Reuse the transport's durable receipt, including unknown charges.
            receipt = getattr(transport, "receipt_path", None)
            if receipt is not None and receipt.exists():
                record = json.loads(receipt.read_text())["ledger_record"]
                record["cost_usd"] = record.get("known_response_cost_usd")
                record["served_model"] = record["model"]
                append_jsonl(self.ledger, record)


def normalize_vectors(data, count, dimensions):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("Embedding response requires a data list")
    if any(
        not isinstance(row, dict) or type(row.get("index")) is not int
        for row in data["data"]
    ):
        raise ValueError("Embedding indices must be integers")
    rows = sorted(data["data"], key=lambda row: row["index"])
    if [r["index"] for r in rows] != list(range(count)):
        raise ValueError("Embedding result indices differ from input order")
    result = []
    for row in rows:
        vector = row["embedding"]
        if len(vector) != dimensions or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in vector
        ):
            raise ValueError("Invalid embedding dimensions or values")
        norm = math.sqrt(sum(v * v for v in vector))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("Embedding must have a finite nonzero norm")
        result.append([v / norm for v in vector])
    return result


def azure_provider(loop):
    provider = loop.config.get("providers", {}).get("embedding", {})
    base = provider.get("base_url") or loop.config.get("AZURE_EP8_BASE")
    key = loop.config.get("AZURE_EP8_KEY")
    if not base or not key:
        raise ValueError("A3 Azure embeddings require AZURE_EP8_BASE/KEY")
    return base, key, provider


async def embed_azure(loop, texts, *, identity="coreset-embeddings"):
    base, key, provider = azure_provider(loop)
    directory = loop.logs / "a3" / identity
    path = directory / "result.json"
    condition = {
        "input_sha256": digest(canonical(texts)),
        "model": MODEL,
        "dimensions": 1024,
        "base_url": base,
    }
    record = (
        json.loads(path.read_text())
        if path.exists()
        else {
            **condition,
            "arm": loop.arm,
            "attempts": [],
        }
    )
    if any(record.get(k) != v for k, v in condition.items()):
        raise ValueError("Frozen embedding condition changed")
    if "vectors" in record:
        return record["vectors"], record["metadata"]
    prices = directory / "prices.json"
    atomic_json(
        prices,
        {
            "date": "2026-09-11",
            "source": "https://developers.openai.com/api/docs/models/text-embedding-3-large",
            "models": {
                MODEL: {
                    "input": INPUT_RATE,
                    "output": 0,
                    "cached_input": INPUT_RATE,
                    "cache_write": INPUT_RATE,
                }
            },
        },
    )
    backend = AccountedEmbeddingBackend(
        base_url=base,
        api_key=key,
        model=MODEL,
        effort=None,
        max_completion_tokens=1,
        ledger=loop.accounting / "ledger.jsonl",
        logs_dir=directory / "calls",
        prices_path=prices,
        guard_path=loop.accounting / "budget.sqlite",
        limiter_path=loop.root / "logs/evolution-endpoints.sqlite",
        audit_path=loop.accounting / "requests.jsonl",
        tags=CallTags(
            "P1.5",
            loop.experiment,
            loop.arm,
            loop.iteration,
            identity,
            "a3-diagnose",
        ),
        scope=f"{loop.experiment}-{loop.arm}-i{loop.iteration}-{identity}",
        budget_usd=1,
        max_calls=2,
        max_retries=0,
        rpm=provider.get("rpm", 250),
        tpm=provider.get("tpm", 250000),
    )
    try:
        # Recover a response already received before a controller interruption.
        for response in sorted(backend.logs_dir.glob("*/response.json")):
            request = json.loads(
                (response.parent / "request.json").read_text()
            )
            if (
                request["input"] != texts
                or request["model"] != MODEL
                or request["dimensions"] != 1024
            ):
                raise ValueError("Archived embedding input changed")
            data = json.loads(response.read_text())
            if "data" in data:
                try:
                    vectors = normalize_vectors(data, len(texts), 1024)
                except (ValueError, TypeError, KeyError):
                    continue
                backend._check_served_model(data["model"])
                metadata = {
                    "model": MODEL,
                    "served_model": data["model"],
                    "dimensions": 1024,
                    "normalization": "L2",
                    "raw_dir": str(response.parent),
                    "input_rate_usd_per_million": INPUT_RATE,
                    "recovered": True,
                }
                break
        else:
            while len(record["attempts"]) < 2:
                record["attempts"].append(
                    {"ts": utc_now(), "status": "running"}
                )
                atomic_json(path, record)
                try:
                    vectors, metadata = await backend.embed(texts)
                    record["attempts"][-1]["status"] = "complete"
                    break
                except (
                    ValueError, TypeError, KeyError, httpx.HTTPError
                ) as exc:
                    record["attempts"][-1].update(
                        status="failed", error_type=type(exc).__name__
                    )
                    atomic_json(path, record)
            else:
                raise ValueError("Embedding batch failed after A9 retry")
        record.update(
            vectors=vectors,
            metadata={
                **metadata,
                "endpoint": "Azure EP8",
                "fingerprints_sha256": condition["input_sha256"],
            },
        )
        atomic_json(path, record)
        return vectors, record["metadata"]
    finally:
        backend.close()
