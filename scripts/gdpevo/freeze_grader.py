"""Reproduce grader-v1 without editing the pinned upstream checkout."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from gdpevo import GROUPS, ROOT, SOURCE


def tree_hash(root: Path) -> str:
    """Hash sorted relative POSIX paths, NUL, file bytes, NUL."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def freeze(destination: Path, source: Path = SOURCE) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    upstream = source / "data/task_groups"
    for group in GROUPS:
        src = upstream / f"task_group_{group:03}"
        dst = destination / src.name
        for split in ("train", "test"):
            for task in sorted((src / f"{split}_tasks").glob("[0-9]*")):
                target = dst / task.relative_to(src)
                shutil.copytree(
                    task / "eval",
                    target / "eval",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                # Several graders load reference assets at runtime.
                shutil.copytree(task / "output", target / "output")
        if (src / "eval_common.py").exists():
            shutil.copyfile(src / "eval_common.py", dst / "eval_common.py")

    contact = destination / "task_group_015/train_tasks/001/eval/eval.py"
    before = contact.read_text()
    old = (
        "    blob = json.dumps(contact if contact else answer, "
        "sort_keys=True)\n"
        "    return all(expected in blob for expected in "
        'EXPECTED["specialist_provider"].values())'
    )
    new = (
        "    return all(\n"
        "        contact.get(field) == expected\n"
        "        for field, expected in "
        'EXPECTED["specialist_provider"].items()\n'
        "    )"
    )
    assert before.count(old) == 1, "Upstream contact check changed"
    contact.write_text(before.replace(old, new))

    fees = destination / "task_group_018/train_tasks/001/eval/eval.py"
    before = fees.read_text()
    old = "        if actual != expected_norm or not status_ok:"
    new = (
        "        if (\n"
        "            actual != expected_norm or not status_ok\n"
        "            or not isinstance(items, list)\n"
        "            or len(items) != len(actual)\n"
        "        ):"
    )
    assert before.count(old) == 1, "Upstream fee check changed"
    after = before.replace(old, new)
    old = (
        '        if not is_money(recs.get(case, {}).get("case_total"), '
        "expected):"
    )
    new = (
        "        rec = recs.get(case, {})\n"
        '        items = rec.get("fee_items", [])\n'
        "        amounts = [\n"
        '            money(item.get("amount")) '
        "if isinstance(item, dict) else None\n"
        "            for item in items\n"
        "        ] if isinstance(items, list) else [None]\n"
        "        if (\n"
        '            not is_money(rec.get("case_total"), expected)\n'
        "            or None in amounts\n"
        '            or not is_money(rec.get("case_total"), sum(amounts))\n'
        "        ):"
    )
    assert after.count(old) == 1, "Upstream total check changed"
    fees.write_text(after.replace(old, new))
    return {
        "version": "grader-v1",
        "tree_sha256": tree_hash(destination),
        "hash_contract": "sorted relative POSIX path + NUL + bytes + NUL",
        "source_commit": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "patched_files": [
            str(p.relative_to(destination)) for p in (contact, fees)
        ],
        "groups": list(GROUPS),
        "tasks": 120,
    }


if __name__ == "__main__":
    manifest = freeze(ROOT / "gdpevo/grader_v1")
    (ROOT / "gdpevo/grader_v1_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(json.dumps(manifest, indent=2))
