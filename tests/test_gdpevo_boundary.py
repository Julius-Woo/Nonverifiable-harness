import importlib.util
import json
import os

import httpx
import pytest

from gdpevo import ROOT
from gdpevo.boundary import Attempt
from gdpevo.feedback import (
    SOURCES,
    api_scope,
    arm_feedback,
    evolver_prompt,
    filtered_trace,
    judge_input,
)
from harness.ledger import CallTags
from harness.openai_api import OpenAIAPIBackend

spec = importlib.util.spec_from_file_location(
    "gateway", ROOT / "docker/gdpevo/gateway.py"
)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/judge"),
        ("GET", "/api/judge"),
        ("POST", "/admin/reset"),
        ("POST", "/admin/reseed"),
        ("PUT", "/patients/P001"),
        ("GET", "/patients/../api/judge"),
        ("GET", "/patients/%2e%2e"),
        ("GET", "http://evil/patients/P001"),
        ("GET", "//evil/patients/P001"),
        ("CONNECT", "/patients/P001"),
    ],
)
def test_gateway_denies_undocumented_routes(method, path):
    routes = [
        {"method": "GET", "path": "/patients/{patient_id}"},
        {"method": "POST", "path": "/query"},
    ]
    assert not gateway.allowed(method, path, routes)
    assert gateway.allowed("GET", "/patients/P001?x=1", routes)
    assert gateway.allowed("POST", "/query", routes)


def test_closed_feedback_schemas_and_uniform_prompts():
    with pytest.raises(TypeError):
        judge_input(task_description="t", trace=[], answer={}, oracle={})
    for role in ("anchor", "sealed"):
        with pytest.raises(ValueError):
            arm_feedback("A0", task_role=role, task="t", trace=[], score=1)
    templates = [
        evolver_prompt(a).replace(s, "SOURCE") for a, s in SOURCES.items()
    ]
    assert len(set(templates)) == 1
    scopes = [
        json.dumps(api_scope("run", a, r))
        for a in SOURCES
        for r in ("judge", "evolver", "task")
    ]
    assert len(scopes) == len(set(scopes))
    records = [
        {
            "kind": "observation",
            "command": f"cat /work/{path}",
            "stdout": "private canary",
        }
        for path in (
            "eval/evaluator.py",
            "output/answer.json",
            "notes/notes.md",
            "judge_api.py",
            "task_group.yaml",
            "tests/test_x.py",
        )
    ]
    assert "private canary" not in json.dumps(filtered_trace(records))


async def test_cache_scope_reaches_api_request(tmp_path):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "gpt-5-mini",
                "choices": [
                    {"message": {"content": "answer"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    for arm, role in (("A1", "judge"), ("A1", "evolver"), ("A2", "judge")):
        scope = api_scope("test", arm, role)
        backend = OpenAIAPIBackend(
            base_url="https://example.invalid/v1",
            api_key="test-key",
            model="gpt-5-mini",
            ledger=tmp_path / "ledger.jsonl",
            logs_dir=tmp_path / "calls",
            extra_params=scope,
            transport=httpx.MockTransport(respond),
        )
        result = await backend.complete("prompt", CallTags(role=role, arm=arm))
        assert result.record["ok"]
        assert all(captured[-1][k] == v for k, v in scope.items())
    assert len({r["prompt_cache_key"] for r in captured}) == 3
    assert len({r["user"] for r in captured}) == 3


@pytest.mark.skipif(
    os.getenv("GDPEVO_DOCKER_TESTS") != "1",
    reason="Opt-in Docker boundary integration check",
)
async def test_blocked_route_from_inside_solver(tmp_path):
    with Attempt(13, "train", "001", tmp_path / "attempt") as boundary:
        result = await boundary.exec(
            "curl -sS -o /work/scratch/refused.json -w '%{http_code}' "
            "-X POST http://gateway:8080/api/judge"
        )
        assert result.return_code == 0
        assert result.stdout == "403"
