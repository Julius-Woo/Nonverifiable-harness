"""Trusted GDPevo controller; never installed in solver containers."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "external/GDPevo"
GROUPS = (8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20)


def task_path(root: Path, group: int, split: str, task_id: str) -> Path:
    """Resolve only the frozen benchmark's bounded task identifiers."""
    if group not in GROUPS or split not in ("train", "test"):
        raise ValueError("Unsupported group or split")
    if task_id not in ("001", "002", "003", "004", "005"):
        raise ValueError("Expected a three-digit task ID from 001 to 005")
    return root / f"task_group_{group:03}" / f"{split}_tasks" / task_id
