"""Exercise the Section 5 adapter interfaces at real Docker tool boundaries."""

import argparse
import asyncio
import json
import shlex
import uuid
from dataclasses import asdict
from pathlib import Path

from gdpevo import GROUPS, ROOT
from gdpevo.boundary import Attempt, docker, image_ref
from gdpevo.feedback import (
    SOURCES,
    api_scope,
    arm_feedback,
    evolver_prompt,
    filtered_trace,
    judge_input,
)


async def acceptance(output: Path, groups):
    output.mkdir(parents=True, exist_ok=False)
    canary = ROOT / "oracle" / f"canary-{uuid.uuid4().hex}.txt"
    canary.parent.mkdir(exist_ok=True)
    canary.write_text(uuid.uuid4().hex)
    service_checks = []
    trace_checks = []
    for group in groups:
        checks = []
        try:
            with Attempt(group, "train", "001", output / f"g{group:03}") as b:

                async def check(name, command, predicate):
                    result = await b.exec(command, timeout_sec=8)
                    record = {
                        "name": name,
                        "passed": bool(predicate(result)),
                        **asdict(result),
                    }
                    checks.append(record)

                await check(
                    "oracle_canary",
                    f"cat {shlex.quote(str(canary))}",
                    lambda r: (
                        r.return_code != 0
                        and canary.read_text() not in r.stdout
                    ),
                )
                await check(
                    "input_read_only",
                    "echo changed >> /work/input/prompt.txt",
                    lambda r: r.return_code != 0,
                )
                await check(
                    "host_checkout_absent",
                    f"cat {ROOT}/.env",
                    lambda r: r.return_code != 0 and not r.stdout,
                )
                await check(
                    "no_docker_socket",
                    "test ! -e /var/run/docker.sock",
                    lambda r: r.return_code == 0,
                )
                await check(
                    "zero_effective_capabilities",
                    "awk '/CapEff|CapPrm|NoNewPrivs/ {print}' "
                    "/proc/self/status",
                    lambda r: (
                        "CapEff:\t0000000000000000" in r.stdout
                        and "CapPrm:\t0000000000000000" in r.stdout
                        and "NoNewPrivs:\t1" in r.stdout
                    ),
                )
                for path in (
                    "/api/judge",
                    "/admin/reset",
                    "/admin/reseed",
                    "/operator/reset",
                    "/api/%6audge",
                ):
                    await check(
                        "blocked_" + path,
                        "curl -sS -o /dev/null -w '%{http_code}' -X POST "
                        + shlex.quote("http://gateway:8080" + path),
                        lambda r: r.return_code == 0 and r.stdout == "403",
                    )
                for name, url in (
                    ("service_direct", f"http://{b.service_ip}:8080/health"),
                    ("internet", "http://1.1.1.1"),
                    ("host_bridge", "http://172.17.0.1:80"),
                ):
                    await check(
                        name,
                        f"curl -sS --max-time 1 {shlex.quote(url)}",
                        lambda r: r.return_code != 0,
                    )
                await check(
                    "external_dns",
                    "python3 -c 'import socket; "
                    "s=socket.socket(socket.AF_INET, "
                    "socket.SOCK_DGRAM); s.settimeout(1); "
                    's.sendto(bytes.fromhex("000001000001000000000000'
                    '076578616d706c6503636f6d0000010001"), '
                    '("127.0.0.11",53)); s.recv(1024)\'',
                    lambda r: r.return_code != 0,
                )
                if "query" in b.task["api"]:
                    query = b.task["api"]["query"]
                    await check(
                        "authenticated_query",
                        query["example"],
                        lambda r: (
                            r.return_code == 0
                            and "error" not in json.loads(r.stdout)
                        ),
                    )
                if group == 13:
                    paths = [
                        "eval/eval.py",
                        "eval/evaluator.py",
                        "output/answer.json",
                        "notes/notes.md",
                        "judge_api.py",
                        "task_group.yaml",
                        "judge_train_eval/train_001_evaluator.py",
                        "judge_evaluators/train_001_eval.py",
                        "tests/test_x.py",
                        "construction/answers.json",
                    ]
                    for base in ("/work", "/app"):
                        for path in paths:
                            command = "cat " + shlex.quote(f"{base}/{path}")
                            result = await b.exec(command)
                            trace = [
                                {
                                    "kind": "observation",
                                    "command": command,
                                    "stdout": result.stdout,
                                    "stderr": result.stderr,
                                }
                            ]
                            trace_checks.append(
                                {
                                    "path": f"{base}/{path}",
                                    "refused": result.return_code != 0,
                                    "filtered": filtered_trace(trace),
                                }
                            )
        except Exception as exc:
            checks.append(
                {
                    "name": "startup_or_probe",
                    "passed": False,
                    "error": str(exc),
                }
            )
        service_checks.append(
            {
                "group": group,
                "checks": checks,
                "passed": all(c["passed"] for c in checks),
            }
        )
        print(f"Group {group:03}: {service_checks[-1]['passed']}", flush=True)

    # Tool-visible arm workspaces use the same minimal image, network=none,
    # and exactly one per-arm mount. They have no oracle or anchor mount.
    arm_results, containers = [], []
    try:
        for arm in SOURCES:
            work = output / "arms" / arm
            work.mkdir(parents=True)
            (work / "feedback.json").write_text(
                json.dumps(
                    arm_feedback(
                        arm,
                        task_role="search",
                        task="search-001",
                        trace=[],
                        score=0.5,
                    )
                )
            )
            (work / "prompt.txt").write_text(evolver_prompt(arm))
            (work / "api_scope.json").write_text(
                json.dumps(
                    {
                        role: api_scope("acceptance", arm, role)
                        for role in ("judge", "evolver")
                    }
                )
            )
            (work / f"{arm}-marker.txt").write_text(arm)
            name = "nvh-gdpevo-arm-" + uuid.uuid4().hex[:12]
            containers.append(name)
            docker(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--user",
                "1000:1000",
                "--security-opt",
                "no-new-privileges=true",
                "--entrypoint",
                "sleep",
                "--mount",
                f"type=bind,src={work},dst=/work/feedback,readonly",
                image_ref("nvh-gdpevo-solver:v1"),
                "infinity",
            )
            command = (
                f"test ! -r {shlex.quote(str(canary))} && "
                "test ! -e /work/anchor && "
                "cat /work/feedback/prompt.txt "
                "/work/feedback/api_scope.json && "
                "find /work/feedback -type f"
            )
            probe = docker("exec", name, "sh", "-c", command)
            cross = all(
                f"{other}-marker.txt" not in probe.stdout
                for other in SOURCES
                if other != arm
            )
            arm_results.append(
                {
                    "arm": arm,
                    "passed": probe.returncode == 0 and cross,
                    "tool_output": probe.stdout,
                    "inspect": json.loads(docker("inspect", name).stdout)[0],
                }
            )
    finally:
        for name in containers:
            docker("rm", "-f", name, check=False)
    closed_schema = False
    try:
        judge_input(task_description="task", trace=[], answer={}, oracle={})
    except TypeError:
        closed_schema = True
    anchor_hidden = True
    for task_role in ("anchor", "sealed"):
        try:
            arm_feedback(
                "A0", task_role=task_role, task="hidden", trace=[], score=1
            )
            anchor_hidden = False
        except ValueError:
            pass
    templates = {
        evolver_prompt(a).replace(s, "SOURCE") for a, s in SOURCES.items()
    }
    scopes = [
        api_scope("acceptance", a, r)["prompt_cache_key"]
        for a in SOURCES
        for r in ("judge", "evolver")
    ]
    rows = [
        {
            "row": 1,
            "check": "oracle canary absent from solver and arm tools",
            "passed": all(x["passed"] for x in arm_results)
            and all(
                c["passed"]
                for x in service_checks
                for c in x["checks"]
                if c["name"] == "oracle_canary"
            ),
        },
        {
            "row": 2,
            "check": "20 tool traces: oracle paths absent and filtered",
            "passed": len(trace_checks) == 20
            and all(
                t["refused"] and t["filtered"][0].get("redacted")
                for t in trace_checks
            ),
        },
        {
            "row": 3,
            "check": "closed judge input schema rejects oracle field",
            "passed": closed_schema,
        },
        {
            "row": 4,
            "check": "five evolver templates differ only in score source",
            "passed": len(templates) == 1,
        },
        {
            "row": 5,
            "check": "anchor/sealed feedback rejected; no anchor mount",
            "passed": anchor_hidden,
        },
        {
            "row": 6,
            "check": "distinct arm workspaces; cross-arm markers absent",
            "passed": len(arm_results) == 5
            and all(x["passed"] for x in arm_results),
        },
        {
            "row": 7,
            "check": "arm/role user and prompt_cache_key visible in config",
            "passed": len(set(scopes)) == 10,
        },
    ]
    result = {
        "passed": all(r["passed"] for r in rows)
        and all(s["passed"] for s in service_checks),
        "matrix": rows,
        "services": service_checks,
        "trace_checks": trace_checks,
        "arms": arm_results,
        "canary_path": str(canary),
    }
    (output / "acceptance.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps({"passed": result["passed"], "matrix": rows}, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", type=int, nargs="+", default=GROUPS)
    args = parser.parse_args()
    result = asyncio.run(acceptance(args.output.resolve(), args.groups))
    raise SystemExit(not result["passed"])
