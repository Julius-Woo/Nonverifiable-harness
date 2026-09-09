import fcntl
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from harness.ledger import append_jsonl
from scripts.cost_report import read_ledger, render_report


def writer(path, worker):
    for index in range(20):
        append_jsonl(
            path, {"worker": worker, "index": index, "data": "x" * 5000}
        )


def test_concurrent_process_appends(tmp_path):
    path = tmp_path / "ledger.jsonl"
    processes = [
        multiprocessing.Process(target=writer, args=(path, i))
        for i in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    records = read_ledger(path)
    assert len(records) == 80
    assert len({(r["worker"], r["index"]) for r in records}) == 80


def test_report_does_not_hide_missing_cost_or_failed_call():
    report = render_report(
        [
            {"ok": True, "cost_usd": 0.5, "wall_s": 2},
            {"ok": False, "cost_usd": None, "wall_s": 1},
        ]
    )
    assert "Recorded calls: 2" in report
    assert "$0.500000" in report
    assert "Unknown USD calls: 1" in report
    assert "(+2 unknown)" in report


def test_corrupt_ledger_fails_with_line_number(tmp_path):
    path = tmp_path / "ledger"
    path.write_text('{}\n{"partial":')
    with pytest.raises(ValueError, match=":2:"):
        read_ledger(path)


def test_report_waits_for_writer_to_finish_record(tmp_path):
    path = tmp_path / "ledger"
    partial_written = threading.Event()
    finish_write = threading.Event()

    def partial_writer():
        with path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write('{"ok":')
            handle.flush()
            partial_written.set()
            assert finish_write.wait(2)
            handle.write("true}\n")
            handle.flush()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_future = pool.submit(partial_writer)
        assert partial_written.wait(2)
        reader_future = pool.submit(read_ledger, path)
        try:
            time.sleep(0.05)
            assert not reader_future.done()
        finally:
            finish_write.set()
        writer_future.result()
        assert reader_future.result() == [{"ok": True}]


def test_report_resolves_same_date_unknown_price_without_mutating_ledger():
    row = {
        "model": "gpt-5.6-sol",
        "backend": "copilot",
        "price_table_date": "2026-09-09",
        "ok": False,
        "input_tokens": 26969,
        "cached_input_tokens": 0,
        "cache_write_tokens": 26966,
        "output_tokens": 9816,
        "reasoning_tokens": 9322,
        "cost_usd": None,
    }
    report = render_report([row])
    assert "$0.331162" in report
    assert "Unknown USD calls: 0" in report
    assert row["cost_usd"] is None
