"""Inventory pinned business databases and generator annotation evidence."""

import hashlib
import json
import re
import sqlite3

from gdpevo import GROUPS, ROOT, SOURCE

HIDDEN = {
    19: {
        "contractor_applications": ["target_group"],
        "liquor_applications": ["target_group"],
        "alcohol_licensees": ["target_group"],
    }
}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def audit():
    groups = []
    for group in GROUPS:
        env = SOURCE / f"data/task_groups/task_group_{group:03}/env"
        generator = env / "generate_data.py"
        text = generator.read_text()
        databases = []
        for path in sorted(env.rglob("*")):
            if path.suffix not in {".db", ".sqlite", ".sqlite3"}:
                continue
            tables = []
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                for (table,) in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "ORDER BY name"
                ):
                    columns = [
                        r[1]
                        for r in db.execute(
                            f"PRAGMA table_info({quote(table)})"
                        )
                    ]
                    hidden = HIDDEN.get(group, {}).get(table, [])
                    tables.append(
                        {
                            "table": table,
                            "columns": columns,
                            "public_columns": [
                                c for c in columns if c not in hidden
                            ],
                            "hidden_columns": hidden,
                            "row_count": db.execute(
                                f"SELECT count(*) FROM {quote(table)}"
                            ).fetchone()[0],
                            "hidden_nonnull_counts": {
                                c: db.execute(
                                    f"SELECT count({quote(c)}) "
                                    f"FROM {quote(table)}"
                                ).fetchone()[0]
                                for c in hidden
                            },
                        }
                    )
            databases.append(
                {
                    "path": str(path.relative_to(env)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "tables": tables,
                }
            )
        groups.append(
            {
                "group": group,
                "generator_sha256": hashlib.sha256(
                    generator.read_bytes()
                ).hexdigest(),
                "generator_annotation_lines": [
                    {"line": i, "text": line.strip()}
                    for i, line in enumerate(text.splitlines(), 1)
                    if re.search(
                        r'target_group|["\']task_(?:id|use)["\']|'
                        r'["\'](?:train|test)_\d{3}["\']',
                        line,
                    )
                ],
                "databases": databases,
                "decision": (
                    "Remove construction annotations; "
                    "retain shared business records."
                ),
            }
        )
    report = {
        "schema_version": 1,
        "groups": groups,
        "summary": {
            "groups": len(groups),
            "databases": sum(len(g["databases"]) for g in groups),
            "tables": sum(
                len(d["tables"]) for g in groups for d in g["databases"]
            ),
            "columns": sum(
                len(t["columns"])
                for g in groups
                for d in g["databases"]
                for t in d["tables"]
            ),
            "hidden_column_instances": 3,
            "hidden_column_names": ["target_group"],
            "affected_groups": [19],
        },
        "json_audit": {
            "removed_manifest_fields": [
                "task_id",
                "task_group_id",
                "task_group",
                "primary_matters",
                "task_relevant_seed_objects",
                "target_groups",
                "target_identifiers",
                "target_case_ids",
                "target_patient_ids",
            ],
            "excluded_files": [
                "judge_data.json",
                "train_judge_data.json",
                "construction_manifest.json",
            ],
            "retained_business_fields": {
                "008": [
                    "conversion_bracket_targets",
                    "expected_return",
                    "expected_growth_rate",
                ],
                "010": ["target_hy_reduction_pct", "constraints"],
                "013": ["target_condition"],
                "014": ["case_criteria", "supports_criteria"],
                "015": ["preferred_target_patient_id", "expected_terms"],
                "016": ["program_hint", "risk_score"],
                "017": [
                    "remediation_actions",
                    "responsiveness",
                    "privilege_status",
                ],
                "018": ["judge", "approved_monthly"],
                "020": ["target_name", "preferred_position"],
            },
            "basis": (
                "Inspected schemas of all shipped databases, all 12 "
                "generators, and keys of JSON data/generated files. Listed "
                "retained fields are domain facts, operational decisions or "
                "policy guidance exposed by business endpoints; they do not "
                "label train/test construction membership. Full generator "
                "bodies and task_group.yaml are never staged."
            ),
        },
    }
    path = ROOT / "data/gdpevo_hidden_columns.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    audit()
