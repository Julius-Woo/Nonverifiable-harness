"""Positive-list task staging and mechanical API descriptors."""

import ast
import json
import re
import shlex
import shutil
from pathlib import Path

from gdpevo import SOURCE, task_path

# These are benchmark service credentials, never TASK model credentials.
# Runtime constants are read from the pinned app, not from .env.
QUERY_CONTRACTS = {
    13: ("/query", "sql", None, None, True),
    14: ("/sql/query", "sql", "Authorization", "TOKEN", True),
    16: ("/api/query", "sql", "X-Clinic-Token", "READONLY_TOKEN", True),
    17: ("/api/query", "sql", "X-API-Key", "API_KEY", True),
    19: ("/api/sql", "query", "X-Task-Token", "SQL_TOKEN", True),
    20: ("/api/query", "sql", None, "QUERY_TOKEN", False),
}
FORBIDDEN = {
    "eval",
    "output",
    "notes",
    "judge_train_eval",
    "judge_evaluators",
    "judge_api.py",
    "task_group.yaml",
    "__pycache__",
    ".env",
}


def business_routes(group_dir: Path) -> list[dict]:
    routes = []
    group = int(group_dir.name.rsplit("_", 1)[1])
    query = QUERY_CONTRACTS.get(group, (None,))[0]
    for line in (group_dir / "env/endpoints.txt").read_text().splitlines():
        method, path = line.split()
        if re.search(r"judge|admin|operator|reset|reseed|health", path):
            continue
        if method == "GET" or (method == "POST" and path == query):
            routes.append({"method": method, "path": path})
    return routes


def descriptor(group_dir: Path, base_url: str) -> dict:
    group = int(group_dir.name.rsplit("_", 1)[1])
    result = {"base_url": base_url, "routes": business_routes(group_dir)}
    if group not in QUERY_CONTRACTS:
        return result
    path, field, header, constant, params = QUERY_CONTRACTS[group]
    source = ast.parse((group_dir / "env/app.py").read_text())
    constants = {
        n.targets[0].id: n.value.value
        for n in source.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    headers = {"Content-Type": "application/json"}
    body = {field: "SELECT 1"}
    syntax = {
        13: "SELECT or read-only PRAGMA",
        14: "SELECT, WITH, or PRAGMA table_info(table_name)",
        20: "SELECT or WITH",
    }.get(group, "SELECT")
    required = {field: f"string: one read-only {syntax} query"}
    optional = {"params": "array (default [])"} if params else {}
    if group in (14, 17):
        optional["params"] = "array or object (default [])"
    if header:
        value = constants[constant]
        headers[header] = f"Bearer {value}" if group == 14 else value
    elif constant:
        body["token"] = constants[constant]
        required["token"] = constants[constant]
    if group == 19:
        optional["limit"] = "integer (default 200; maximum 1000)"
    command = ["curl", "-sS", "-X", "POST", base_url + path]
    for name, value in headers.items():
        command.extend(["-H", f"{name}: {value}"])
    command.extend(["--data", json.dumps(body)])
    result["query"] = {
        "method": "POST",
        "path": path,
        "headers": headers,
        "required_json_fields": required,
        "optional_json_fields": optional,
        "example_body": body,
        "example": shlex.join(command),
    }
    return result


def stage_task(
    group: int,
    split: str,
    task_id: str,
    destination: Path,
    base_url: str = "http://gateway:8080",
    source: Path = SOURCE,
) -> dict:
    """Create a fresh workspace; reject symlinks and unexpected input files."""
    task = task_path(source / "data/task_groups", group, split, task_id)
    if (task / "input").is_symlink() or not task.resolve().is_relative_to(
        source.resolve()
    ):
        raise ValueError("Unsafe task input root")
    entries = list((task / "input").rglob("*"))
    for entry in entries:
        rel = entry.relative_to(task / "input")
        if entry.is_symlink() or any(p in FORBIDDEN for p in rel.parts):
            raise ValueError(f"Unsafe task input: {rel}")
        if entry.is_file() and not (
            rel.as_posix() == "prompt.txt" or rel.parts[0] == "payloads"
        ):
            raise ValueError(f"Unexpected task input: {rel}")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(task / "input", destination / "input")
    prompt = destination / "input/prompt.txt"
    prompt.write_text(
        prompt.read_text().replace("<TASK_ENV_BASE_URL>", base_url)
    )
    access = descriptor(task.parents[1], base_url)
    lines = ["# Environment access", "", f"Base URL: {base_url}", ""]
    lines += [f"{r['method']} {r['path']}" for r in access["routes"]]
    if "query" in access:
        lines += [
            "",
            "Query request contract:",
            json.dumps(access["query"], indent=2),
        ]
    (destination / "environment_access.md").write_text("\n".join(lines) + "\n")
    filtered = {
        "group": group,
        "split": split,
        "task_id": task_id,
        "input": "input/",
        "answer": "/work/answer.json",
        "state_mode": "read_only",
        "api": access,
    }
    (destination / "task.json").write_text(
        json.dumps(filtered, indent=2) + "\n"
    )
    return filtered
