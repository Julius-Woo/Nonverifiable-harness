"""Receipt-first reconciliation, including killed backend appends."""

import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from evolution.candidates import atomic_json
from harness.ledger import append_jsonl, price_usage
from harness.openai_api import pricing_model


def read_rows(path):
    path = Path(path)
    return (
        [json.loads(s) for s in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def reconcile(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "reconcile.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        unresolved = []

        def recover_rows(path):
            rows = []
            lines = path.read_text().splitlines() if path.exists() else []
            for index, line in enumerate(lines, 1):
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    unresolved.append(
                        {
                            "reason": "damaged_jsonl_row",
                            "path": str(path),
                            "line": index,
                            "sha256": hashlib.sha256(
                                line.encode()
                            ).hexdigest(),
                        }
                    )
            return rows

        ledger = directory / "ledger.jsonl"

        def append_recovery(record):
            if ledger.exists() and ledger.stat().st_size:
                with ledger.open("rb+") as handle:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        # Preserve the damaged fragment as its own line;
                        # never join a recovered receipt onto that fragment.
                        handle.seek(0, os.SEEK_END)
                        handle.write(b"\n")
                        handle.flush()
                        os.fsync(handle.fileno())
            append_jsonl(ledger, record)

        records = recover_rows(ledger)
        by_raw = {r.get("raw_dir"): r for r in records}
        events = recover_rows(directory / "requests.jsonl")
        intents = {
            e["id"]: e for e in events if e["event"] == "request_intent"
        }
        receipts = {}
        repaired = []
        for path in sorted((directory / "requests").glob("*/receipt.json")):
            receipt = json.loads(path.read_text())
            receipts[receipt["id"]] = receipt
        db_path = directory / "budget.sqlite"
        reservations = {}
        if db_path.exists():
            with sqlite3.connect(db_path) as db:
                reservations = {
                    r[0]: r for r in db.execute("SELECT * FROM requests")
                }
        for identity in sorted(
            set(intents) | set(receipts) | set(reservations)
        ):
            intent = intents.get(identity)
            receipt = receipts.get(identity)
            if not intent:
                unresolved.append(
                    {
                        "id": identity,
                        "reason": "reservation_or_receipt_without_intent",
                    }
                )
                continue
            raw = intent["backend_raw_dir"]
            record = by_raw.get(raw)
            response_path = Path(intent["archive"]) / "response.json"
            if (
                receipt is None
                and response_path.exists()
                and intent.get("prices")
            ):
                data = json.loads(response_path.read_text())
                usage = data.get("usage") or {}
                embedding = intent.get("operation") == "embeddings"
                if embedding:
                    usage = {**usage, "completion_tokens": 0}
                successful = (
                    bool(data.get("data")) and "error" not in data
                    if embedding
                    else bool(data.get("choices"))
                )
                details = usage.get("prompt_tokens_details") or {}
                recovered = {
                    **{
                        k: intent.get(k)
                        for k in (
                            "ts",
                            "run_id",
                            "arm",
                            "iteration",
                            "task",
                            "role",
                        )
                    },
                    "call_id": Path(raw).name,
                    "raw_dir": raw,
                    "request_id": identity,
                    "model": data.get("model") or intent["requested_model"],
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                    "cached_input_tokens": details.get(
                        "cached_tokens", usage.get("prompt_cache_hit_tokens")
                    ),
                    "cache_write_tokens": details.get(
                        "cache_write_tokens",
                        details.get("cache_creation_tokens", 0),
                    ),
                    "cost_usd": None,
                    "ok": successful,
                    "cost_source": "durable_response_recovery",
                }
                prices = intent["prices"]
                model = pricing_model(recovered["model"], prices)
                recovered["known_response_cost_usd"] = price_usage(
                    model, recovered, prices
                )
                rates = prices["models"].get(model)
                upper = None
                if rates and all(
                    type(usage.get(k)) is int
                    for k in ("prompt_tokens", "completion_tokens")
                ):
                    upper = (
                        usage["prompt_tokens"]
                        * max(rates["input"], rates["cache_write"])
                        + usage["completion_tokens"] * rates["output"]
                    ) / 1e6
                if not successful:
                    upper = None
                if embedding:
                    recovered["known_response_cost_usd"] = upper
                    recovered["cost_usd"] = upper
                receipt = {
                    **intent,
                    "usage": usage,
                    "uncached_upper_usd": upper,
                    "ledger_record": recovered,
                    "event": "receipt",
                    "recovered_from_response": True,
                }
                atomic_json(Path(intent["archive"]) / "receipt.json", receipt)
                receipts[identity] = receipt
            if receipt and (
                record is None
                or record.get("cost_source") == "unresolved_intent"
            ):
                record = receipt["ledger_record"]
                append_recovery(record)
                by_raw[raw] = record
                repaired.append(identity)
                if identity in reservations:
                    with sqlite3.connect(db_path) as db:
                        db.execute(
                            "UPDATE requests SET charged=?,status='response' "
                            "WHERE id=?",
                            (receipt.get("uncached_upper_usd"), identity),
                        )
            if record is None:
                # A durable unresolved ledger row represents
                # a dispatched intent,
                # never an invented zero charge. A later receipt supersedes it
                # in the reconciliation report, without deleting history.
                record = {
                    **{
                        k: intent.get(k)
                        for k in (
                            "ts",
                            "run_id",
                            "arm",
                            "iteration",
                            "task",
                            "role",
                        )
                    },
                    "call_id": Path(raw).name,
                    "raw_dir": raw,
                    "request_id": identity,
                    "cost_usd": None,
                    "known_response_cost_usd": None,
                    "ok": False,
                    "cost_source": "unresolved_intent",
                    "note": "No durable receipt; reservation retained",
                }
                append_recovery(record)
                by_raw[raw] = record
                repaired.append(identity)
            if receipt and identity in reservations:
                known = receipt["ledger_record"].get("known_response_cost_usd")
                fully_priced = known is not None and receipt[
                    "ledger_record"
                ].get("ok")
                charge = (
                    known
                    if fully_priced
                    else receipt.get("uncached_upper_usd")
                )
                if charge is not None:
                    with sqlite3.connect(db_path) as db:
                        previous = db.execute(
                            "SELECT charged FROM requests WHERE id=?",
                            (identity,),
                        ).fetchone()[0]
                        if previous is None or charge < previous - 1e-12:
                            append_jsonl(
                                directory / "settlement_reconciliation.jsonl",
                                {
                                    "request_id": identity,
                                    "event": "completed_receipt_settlement",
                                    "previous_conservative_charge": previous,
                                    "fully_priced_charge": charge,
                                    "budget_and_deadline_unchanged": True,
                                },
                            )
                            db.execute(
                                "UPDATE requests SET charged=? WHERE id=?",
                                (charge, identity),
                            )
            if not receipt or receipt.get("uncached_upper_usd") is None:
                unresolved.append(
                    {
                        "id": identity,
                        "reason": "unknown_charge",
                        "reserved_usd": intent["reserved_usd"],
                    }
                )
            if identity not in reservations:
                unresolved.append(
                    {"id": identity, "reason": "intent_without_reservation"}
                )
        for request_path in (directory / "requests").glob("*/request.json"):
            if request_path.parent.name not in intents:
                unresolved.append(
                    {
                        "id": request_path.parent.name,
                        "reason": "request_artifact_without_intent",
                    }
                )
        known_raw = {e["backend_raw_dir"] for e in intents.values()}
        for raw, record in by_raw.items():
            if raw not in known_raw and record.get("attempts"):
                unresolved.append(
                    {
                        "call_id": record.get("call_id"),
                        "reason": "ledger_dispatch_without_intent",
                    }
                )
        report = {
            "reservations": len(reservations),
            "intents": len(intents),
            "receipts": len(receipts),
            "matched_ledger_rows": sum(
                e["backend_raw_dir"] in by_raw for e in intents.values()
            ),
            "repaired": repaired,
            "unresolved": unresolved,
            "complete": not unresolved,
        }
        effective = []
        for identity in sorted(
            set(intents) | set(receipts) | set(reservations)
        ):
            intent = intents.get(identity) or receipts.get(identity, {})
            raw = by_raw.get(intent.get("backend_raw_dir"), {})
            receipt = receipts.get(identity, {})
            reservation = reservations.get(identity)
            known = receipt.get("ledger_record", {}).get(
                "known_response_cost_usd"
            )
            effective.append(
                {
                    "request_id": identity,
                    "ledger_call_id": raw.get("call_id"),
                    "backend_raw_dir": intent.get("backend_raw_dir"),
                    "known_receipt_cost_usd": known,
                    "receipt_upper_usd": receipt.get("uncached_upper_usd"),
                    "reserved_usd": intent.get(
                        "reserved_usd", reservation[2] if reservation else None
                    ),
                    "has_intent": identity in intents,
                    "has_receipt": identity in receipts,
                    "has_reservation": identity in reservations,
                }
            )
        atomic_json(directory / "reconciled_ledger.json", effective)
        atomic_json(directory / "reconciliation.json", report)
        return report
