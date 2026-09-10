"""Memory admission uses available memory and preserves pause hysteresis."""

import json
from pathlib import Path

from scripts import harbor_memory_guard as guard


def test_snapshot_uses_memavailable_despite_low_memfree(tmp_path, monkeypatch):
    log = tmp_path / "memory.jsonl"
    monkeypatch.setenv("CALIBRATION_MEMORY_LOG", str(log))
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if str(path) == "/proc/meminfo":
            return "MemFree: 1048576 kB\nMemAvailable: 22020096 kB\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        return "Mem: 31 5 1 0 25 21\n" if command[0] == "free" else "stats"

    monkeypatch.setattr(guard.subprocess, "check_output", output)
    assert guard.snapshot() == 21
    assert calls == [["free", "-g"], ["docker", "stats", "--no-stream"]]
    assert json.loads(log.read_text())["admission"] == "ready"


def test_pause_persists_until_ten_gib():
    admission = guard.AdmissionGuard()
    admission.observe(6)
    assert admission.ready.is_set()
    admission.observe(5.9)
    assert not admission.ready.is_set()
    admission.observe(9.9)
    assert not admission.ready.is_set()
    admission.observe(10)
    assert admission.ready.is_set()
