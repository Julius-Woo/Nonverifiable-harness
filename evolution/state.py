"""Durable trial and stage identities with explicit crash recovery."""

import json
import sqlite3
from pathlib import Path

from evolution.sanitize import canonical, digest
from harness.ledger import append_jsonl, utc_now


class State:
    def __init__(self, path, private=None):
        self.private = Path(private) if private else None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS trials "
            "(id TEXT PRIMARY KEY, spec TEXT, status TEXT, "
            "attempt INTEGER, result TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS stages "
            "(id TEXT PRIMARY KEY, value TEXT)"
        )
        self.db.commit()

    def encode(self, identity, value):
        def contains_private(item):
            if isinstance(item, dict):
                return item.get("partition") in {"anchor", "sealed"} or any(
                    contains_private(v) for v in item.values()
                )
            return isinstance(item, list) and any(
                contains_private(v) for v in item
            )

        if self.private and contains_private(value):
            from evolution.candidates import atomic_json

            path = self.private / (digest(identity) + ".json")
            atomic_json(path, value)
            return canonical({"private_ref": str(path)})
        return canonical(value)

    @staticmethod
    def decode(value):
        value = json.loads(value)
        if isinstance(value, dict) and set(value) == {"private_ref"}:
            return json.loads(Path(value["private_ref"]).read_text())
        return value

    def stage(self, key, value=None):
        if value is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO stages VALUES(?,?)",
                (key, self.encode(key, value)),
            )
            self.db.commit()
        row = self.db.execute(
            "SELECT value FROM stages WHERE id=?", (key,)
        ).fetchone()
        return self.decode(row[0]) if row else None

    def schedule(self, spec):
        identity = "nvhe-" + digest(canonical(spec))[:24]
        self.db.execute(
            "INSERT OR IGNORE INTO trials VALUES(?,?,?,0,NULL)",
            (identity, canonical(spec), "pending"),
        )
        self.db.commit()
        return identity

    def row(self, identity):
        row = self.db.execute(
            "SELECT * FROM trials WHERE id=?", (identity,)
        ).fetchone()
        result = dict(row)
        if result.get("result"):
            result["result"] = canonical(self.decode(result["result"]))
        return result

    def start(self, identity):
        row = self.row(identity)
        if row["status"] != "pending":
            raise ValueError("Only pending trials may dispatch")
        self.db.execute(
            "UPDATE trials SET status='running' WHERE id=?", (identity,)
        )
        self.db.commit()

    def finish(self, identity, result):
        self.db.execute(
            "UPDATE trials SET status='done',result=? WHERE id=?",
            (self.encode(identity, result), identity),
        )
        self.db.commit()

    def recover(self, identity, result=None):
        row = self.row(identity)
        if row["status"] == "done":
            return "done"
        if result is not None:
            self.finish(identity, result)
            return "done"
        if row["status"] == "running":
            # Never silently grant a new solver rollout after interruption.
            self.finish(
                identity,
                {
                    "id": identity,
                    **json.loads(row["spec"]),
                    "status": "interrupted",
                    "oracle": None,
                    "raw_reward": None,
                    "score": None,
                    "reason": "interrupted_trial_no_automatic_retry",
                },
            )
            append_jsonl(
                self.path.with_suffix(".events.jsonl"),
                {
                    "ts": utc_now(),
                    "trial_id": identity,
                    "event": "interrupted_without_response",
                    "retry": False,
                },
            )
            return "done"
        return "pending"

    def close(self):
        self.db.close()
