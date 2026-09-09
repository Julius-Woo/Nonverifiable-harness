"""Smoke-test T2 business APIs on disposable copies without Docker."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from audit_evaluators import GROUPS, ROOT

# One real data endpoint per environment, in addition to its health endpoint.
DATA_ENDPOINTS = {
    8: "/api/clients",
    9: "/api/finance/branches",
    10: "/api/portfolios",
    11: "/api/branches",
    13: "/patients",
    14: "/api/cases",
    15: "/api/patients",
    16: "/api/protocols",
    17: "/api/matters",
    18: "/api/cases",
    19: "/api/policies",
    20: "/api/deals",
}


def request(base: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=2) as response:
            return {
                "status": response.status,
                "body": response.read().decode("utf-8"),
            }
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8")}


def smoke(group: int, source: Path, output: Path, judge: bool) -> dict:
    started = time.perf_counter()
    group_dir = source / "data/task_groups" / f"task_group_{group:03}"
    record = {"group": group, "judge_enabled": judge}
    with tempfile.TemporaryDirectory(prefix=f"gdpevo_{group:03}_") as temp:
        env_dir = Path(temp) / "env"
        shutil.copytree(group_dir / "env", env_dir)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "TASK_ENV_BIND": "127.0.0.1",
            "TASK_ENV_PORT": str(port),
            "TASK_ENV_ENABLE_JUDGE": str(int(judge)),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
        if judge and group in (18, 20):
            # These facades locate train graders outside env/.
            shutil.copytree(
                group_dir / "train_tasks", Path(temp) / "train_tasks"
            )
            env["TASK_GROUP_ROOT"] = temp
            record["staged_train_graders"] = True
        # 016/017 setup.sh assumes /app; app.py has portable path defaults
        # and initializes missing data. All other scripts run in the copy.
        command = (
            [sys.executable, "app.py"]
            if group in (16, 17)
            else ["bash", "setup.sh"]
        )
        record["command"] = command
        log_path = output / f"{group:03}-judge-{int(judge)}.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                command,
                cwd=env_dir,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                health_path = (
                    "/api/health" if group in (8, 10, 11) else "/health"
                )
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"service exited {process.returncode}"
                        )
                    try:
                        health = request(base, health_path)
                        if health["status"] == 200:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("service health timeout")
                record["startup_s"] = time.perf_counter() - started
                record["health"] = health
                record["data_path"] = DATA_ENDPOINTS[group]
                record["data"] = request(base, DATA_ENDPOINTS[group])
                reference = json.loads(
                    (
                        group_dir / "train_tasks/001/output/answer.json"
                    ).read_text()
                )
                record["train_judge"] = request(
                    base,
                    "/api/judge",
                    {
                        "task_id": "train_001",
                        "answer": reference,
                    },
                )
                record["test_judge"] = request(
                    base,
                    "/api/judge",
                    {
                        "task_id": "test_001",
                        "answer": {},
                    },
                )
                train_status = record["train_judge"]["status"]
                test_status = record["test_judge"]["status"]
                record["ok"] = (
                    record["data"]["status"] == 200
                    and train_status == (200 if judge else 404)
                    and test_status in ((400, 403) if judge else (404,))
                    and (
                        not judge
                        or json.loads(record["train_judge"]["body"]).get(
                            "score"
                        )
                        == 1.0
                    )
                )
            except (OSError, ValueError, RuntimeError) as exc:
                record.update(ok=False, error=str(exc))
            finally:
                if process.poll() is None:
                    # Terminate only this helper's dedicated process group.
                    import signal

                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
    record["wall_s"] = time.perf_counter() - started
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "external/GDPevo"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, nargs="+", default=GROUPS)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    records = []
    for group in args.groups:
        for judge in (False, True):
            result = smoke(group, args.source.resolve(), args.output, judge)
            records.append(result)
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k
                        not in ("data", "health", "train_judge", "test_judge")
                    }
                ),
                flush=True,
            )
    report = {
        "python": sys.version,
        "flask": importlib.metadata.version("flask"),
        "total_wall_s": time.perf_counter() - started,
        "records": records,
    }
    (args.output / "summary.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return int(any(not r["ok"] for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
