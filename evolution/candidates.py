"""Content-addressed harness snapshots; never import candidate code on host."""

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from evolution.sanitize import _views

# Meta-harness experimental/controller.py UNIVERSAL_FORBIDDEN, plus PLAN §5.
UNIVERSAL_FORBIDDEN = (
    "/tests",
    "test_outputs",
    "verifier",
    "/solution",
    "task.toml",
    "oracle/",
    "logs/harbor",
    "reward.txt",
    "test-stdout",
    "test-stderr",
    "eval.py",
    "evaluator.py",
    "eval/",
    "output/",
    "notes/",
    "judge_api.py",
    "judge_train_eval/",
    "ground_truth",
    "reference_output",
    "reference_solution",
    "grading_criteria",
    "evaluation_criteria",
)
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


def safe_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value) or ".." in value:
        raise ValueError("Invalid experiment/candidate identifier")
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temp.open("w") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def source_hash(path):
    h = hashlib.sha256()
    for file in sorted(Path(path).rglob("*")):
        if file.is_symlink():
            raise ValueError("Candidate symlinks are forbidden")
        if file.is_file() and file.name != "manifest.json":
            h.update(str(file.relative_to(path)).encode() + b"\0")
            h.update(file.read_bytes())
    return h.hexdigest()


@dataclass(frozen=True)
class Manifest:
    candidate_id: str
    parent_id: str | None
    arm: str
    iteration: int
    evolver_session_id: str
    diff_summary: str
    source_sha256: str
    entrypoint: str = "harness.seed:run_seed"
    version: int = 1

    def __post_init__(self):
        for item in (self.candidate_id, self.arm, self.evolver_session_id):
            safe_id(item)
        if self.parent_id is not None:
            safe_id(self.parent_id)
        if type(self.iteration) is not int or self.iteration < 0:
            raise ValueError("Invalid iteration")
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_sha256):
            raise ValueError("Invalid source digest")
        if self.entrypoint != "harness.seed:run_seed" or self.version != 1:
            raise ValueError("Unsupported candidate contract")
        if not isinstance(self.diff_summary, str):
            raise TypeError("Diff summary must be text")

    def write(self, path):
        atomic_json(Path(path) / "manifest.json", asdict(self))

    @classmethod
    def read(cls, path):
        return cls(**json.loads((Path(path) / "manifest.json").read_text()))


def copy_seed(seed, destination):
    destination = Path(destination)
    destination.mkdir(parents=True)
    shutil.copytree(
        seed,
        destination / "harness",
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
        ),
    )
    return destination


def working_copy(parent, destination):
    shutil.copytree(parent, destination)
    for path in [Path(destination), *Path(destination).rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return Path(destination)


def scan_source(candidate, seed, task_names=(), extra=()):
    """Scan every source; grandfather only exact immutable seed plumbing.

    The frozen seed has controller paths in adapter/backend files.
    Those files are copied for provenance but cannot change or run on the host.
    The editable seed loop has one pre-existing docstring mentioning verifier;
    only that exact original AST docstring is exempted, never new occurrences.
    """
    candidate, seed = Path(candidate), Path(seed)
    failures = []
    expected = {"manifest.json"} | {
        str(p.relative_to(seed.parent)) for p in seed.rglob("*.py")
    }
    actual = set()
    for path in candidate.rglob("*"):
        rel = str(path.relative_to(candidate))
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            failures.append(f"unsupported_file:{rel}")
            continue
        if path.is_dir():
            if rel != "harness":
                failures.append(f"extra_directory:{rel}")
            continue
        actual.add(rel)
        if path.stat().st_size > 250_000:
            failures.append(f"oversized_source:{rel}")
            continue
        if rel == "manifest.json":
            continue
        if rel not in expected:
            failures.append(f"extra_file:{rel}")
            continue
        source = path.read_text()
        original = (seed / path.name).read_text()
        if path.name != "seed.py":
            if source != original:
                failures.append(f"immutable_plumbing_changed:{rel}")
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            failures.append(f"syntax_error:{rel}")
            continue
        # Retain the original seed byte-for-byte; exemption is a single known
        # inert docstring, not an allowlist for executable verifier references.
        old = (
            '"""A bounded seed failure; Harbor should still run '
            'the verifier."""'
        )
        source = source.replace(old, "", 1)
        views = "\n".join(_views(source)).casefold()
        for term in (*UNIVERSAL_FORBIDDEN, *task_names, *extra):
            if term.casefold() in views:
                failures.append(f"forbidden_reference:{rel}:{term}")
        if re.search(r"test_[\w.-]+\.py|rubric\w*\s*[:=]", views):
            failures.append(f"forbidden_pattern:{rel}")
        if not any(
            isinstance(n, ast.AsyncFunctionDef) and n.name == "run_seed"
            for n in tree.body
        ):
            failures.append("missing_async_run_seed")
    for missing in expected - actual:
        failures.append(f"missing_file:{missing}")
    return sorted(set(failures))


def freeze(working, destination, manifest):
    if source_hash(working) != manifest.source_sha256:
        raise ValueError("Candidate changed before freeze")
    shutil.copytree(working, destination)
    manifest.write(destination)
    for path in [Path(destination), *Path(destination).rglob("*")]:
        path.chmod(0o555 if path.is_dir() else 0o444)


def diff_source(parent, candidate):
    return "".join(
        difflib.unified_diff(
            (Path(parent) / "harness/seed.py").read_text().splitlines(True),
            (Path(candidate) / "harness/seed.py").read_text().splitlines(True),
            fromfile="parent/harness/seed.py",
            tofile="candidate/harness/seed.py",
        )
    )
