"""Build contexts containing business data and service code only."""

import ast
import json
import re
import shutil
from pathlib import Path

from gdpevo import GROUPS, SOURCE
from gdpevo.staging import business_routes

ORACLE_NAME = re.compile(r"judge|eval|admin|operator_reset", re.I)
PRIVATE_ROUTE = re.compile(r"/api/judge|/(?:admin|operator)/|reset|reseed")


class BusinessOnly(ast.NodeTransformer):
    """Remove judge imports, registrations, handlers and admin routes."""

    def __init__(self, group: int):
        self.group = group

    def visit_ImportFrom(self, node):
        if ORACLE_NAME.search(node.module or ""):
            return None
        node.names = [n for n in node.names if not ORACLE_NAME.search(n.name)]
        return node if node.names else None

    def visit_Import(self, node):
        node.names = [n for n in node.names if not ORACLE_NAME.search(n.name)]
        return node if node.names else None

    def visit_FunctionDef(self, node):
        if ORACLE_NAME.search(node.name):
            return None
        if any(
            PRIVATE_ROUTE.search(ast.unparse(d)) for d in node.decorator_list
        ):
            return None
        if node.name == "do_POST" and self.group in (8, 9, 11, 15):
            node.body = ast.parse('self.send_error(404, "Not found")').body
            return node
        return self.generic_visit(node)

    def visit_If(self, node):
        if re.search(
            r"judge|TASK_ENV_ENABLE_JUDGE", ast.unparse(node.test), re.I
        ):
            return None
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if any(ORACLE_NAME.search(ast.unparse(t)) for t in node.targets):
            return None
        return self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if ORACLE_NAME.search(ast.unparse(node.target)):
            return None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if re.search(r"register_judge|judge_enabled", ast.unparse(node), re.I):
            return None
        return self.generic_visit(node)

    def visit_Dict(self, node):
        pairs = [
            (k, v)
            for k, v in zip(node.keys, node.values)
            if not (
                isinstance(k, ast.Constant)
                and ORACLE_NAME.search(str(k.value))
            )
        ]
        node.keys = [k for k, _ in pairs]
        node.values = [v for _, v in pairs]
        return self.generic_visit(node)

    def visit_Set(self, node):
        node.elts = [
            e
            for e in node.elts
            if not (
                isinstance(e, ast.Constant)
                and PRIVATE_ROUTE.search(str(e.value))
            )
        ]
        return self.generic_visit(node)


def clean_metadata(value):
    """Remove orchestration metadata; preserve shared business records."""
    if isinstance(value, dict):
        return {
            k: clean_metadata(v)
            for k, v in value.items()
            if not re.search(
                r"judge|eval|construction|train|test|target", k, re.I
            )
        }
    if isinstance(value, list):
        return [
            clean_metadata(v)
            for v in value
            if not (
                isinstance(v, str)
                and re.search(r"judge|eval|construction|reset|reseed", v, re.I)
            )
        ]
    return value


def stage_service(
    group: int, destination: Path, source: Path = SOURCE
) -> dict:
    if group not in GROUPS:
        raise ValueError("Unsupported group")
    origin = source / "data/task_groups" / f"task_group_{group:03}"
    env = origin / "env"
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / "env"
    target.mkdir()
    entry = "app.py" if (env / "app.py").exists() else "server.py"
    tree = BusinessOnly(group).visit(ast.parse((env / entry).read_text()))
    ast.fix_missing_locations(tree)
    filtered = ast.unparse(tree) + "\n"
    if re.search(r"judge_api|/api/judge|TASK_ENV_ENABLE_JUDGE", filtered):
        raise ValueError(f"Unremoved oracle code in group {group}")
    compile(filtered, "app.py", "exec")
    (target / "app.py").write_text(filtered)

    # Data generators can embed gold answers. Keep only mechanical constants
    # needed by imports; never copy generator bodies or judge data.
    generator = env / "generate_data.py"
    if generator.exists():
        names = {
            "SEED",
            "SERVICE_NAME",
            "STATE_MODE",
            "BASE_DIR",
            "ROOT",
            "DATA_DIR",
            "DB_PATH",
            "MANIFEST_PATH",
            "SCHEMA_VERSION",
            "DEFAULT_DB_PATH",
            "CONTAINER_DB_PATH",
        }
        constants = [
            n
            for n in ast.parse(generator.read_text()).body
            if isinstance(n, ast.Assign)
            and all(
                isinstance(t, ast.Name) and t.id in names for t in n.targets
            )
        ]
        mechanical = "from pathlib import Path\nimport os\n"
        mechanical += "\n".join(ast.unparse(n) for n in constants)
        mechanical += (
            "\nENDPOINTS = "
            + repr(
                [f"{r['method']} {r['path']}" for r in business_routes(origin)]
            )
            + "\n"
        )
        mechanical += (
            "def unavailable(*args, **kwargs):\n"
            "    raise RuntimeError('Business data must be prebuilt')\n"
            "generate = generate_all = "
            "generate_database = main = unavailable\n"
        )
        (target / "generate_data.py").write_text(mechanical)
    if group == 8:
        shutil.copyfile(
            env / "advisory_rules.py", target / "advisory_rules.py"
        )
    copied = []
    for folder in ("data", "generated"):
        if not (env / folder).exists():
            continue
        for path in sorted((env / folder).iterdir()):
            if path.is_symlink():
                raise ValueError("Symlink in service data")
            if not path.is_file() or re.search(
                r"judge|eval|construction|seed_manifest", path.name, re.I
            ):
                continue
            if path.suffix not in (
                ".json",
                ".db",
                ".sqlite",
                ".sqlite3",
                ".csv",
            ):
                raise ValueError(f"Unexpected data file: {path.name}")
            dst = target / folder / path.name
            dst.parent.mkdir(exist_ok=True)
            if "manifest" in path.name:
                dst.write_text(
                    json.dumps(
                        clean_metadata(json.loads(path.read_text())), indent=2
                    )
                )
            else:
                shutil.copyfile(path, dst)
            copied.append(str(dst.relative_to(target)))
    if (env / "manifest.json").exists():
        (target / "manifest.json").write_text(
            json.dumps(
                clean_metadata(json.loads((env / "manifest.json").read_text()))
            )
        )
    (target / "endpoints.txt").write_text(
        "\n".join(
            f"{r['method']} {r['path']}" for r in business_routes(origin)
        )
        + "\n"
    )
    return {"group": group, "data_files": copied, "entrypoint": entry}
