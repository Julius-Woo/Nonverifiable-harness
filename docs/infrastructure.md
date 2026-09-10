# Harness infrastructure

The seed uses one JSON action per model call: terminal, read_file, write_file,
or finish. Harbor's Docker environment executes all task commands. The host
never executes model-generated commands. A finish action is a completion claim;
only the unmodified task verifier supplies correctness.

The current backend is `openai_api` (`harness/openai_api.py`), implementing the
same async `complete(prompt, CallTags) -> Completion` interface as the existing
CLI adapters. It uses HTTPX and Bearer authentication against
`<base>/openai/v1/chat/completions`; a base already ending in `/v1` is accepted.
The original CLI adapters remain available explicitly. No model CLI was used
for the API smoke experiment.

## Setup and configuration

```bash
uv sync --locked
uv run pytest -q
uv run ruff check harness scripts/backend_probe.py scripts/cost_report.py \
  scripts/smoke.py scripts/smoke_report.py scripts/reprice_api.py tests
bash scripts/run_smoke.sh --dry-run
```

The project-root `.venv/` and `uv.lock` pin Harbor 0.22.0. `python-dotenv` loads
project-root `.env` without overriding exported variables. Credentials stay on
the host; they are not passed as Harbor command arguments or into task Docker
containers. Never put keys in agent kwargs, traces, or commands.

| Variable | Meaning |
| --- | --- |
| `HARNESS_BACKEND` | `openai_api` by default; explicit `claude`, `codex`, or `copilot` selects the existing adapter |
| `HARNESS_MODEL` | Default deployment when Harbor's `--model` is absent; otherwise `TASK_MODEL` from `.env` |
| `TASK_API_BASE`, `TASK_API_KEY`, `TASK_MODEL` | Primary Azure endpoint, Bearer credential, default `gpt-5-mini` deployment |
| `TASK_MODEL_ALT` | `gpt-5.1` deployment at the primary endpoint; select with `--model gpt-5.1` |
| `TASK_ALT2_API_BASE`, `TASK_ALT2_API_KEY`, `TASK_ALT2_MODEL` | Alternate Azure endpoint; select `--endpoint-prefix TASK_ALT2 --model gpt56luna` |
| `JUDGE_*`, `EVOLVER_*`, `XJUDGE_*` | Future role endpoints; the API backend accepts explicit base/key/model for each role |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | Generic compatibility variables for other clients; the seed uses the selected role prefix |

Explicit Harbor model/agent kwargs take precedence over environment defaults.
`endpoint_prefix` chooses a complete base/key/model group; a primary credential
is never silently combined with the alternate endpoint.

The API backend accepts `extra_params` (for example `reasoning_effort` and
`max_completion_tokens`). Streaming, multiple choices, `max_tokens`, and
replacement messages/model are rejected. The seed forwards `reasoning_effort`
and `max_completion_tokens` agent kwargs. Defaults used in smoke: low reasoning,
4,096 completion tokens including reasoning, 24 calls, 180 seconds per logical
API call including retries, and 30 seconds per command. Harbor additionally
uses the dataset's agent, build, and verifier timeouts.

## Running and inspecting Docker trials

```bash
bash scripts/run_smoke.sh --timeout 1800
bash scripts/run_smoke.sh --label b-mini --model gpt-5-mini -n 4 \
  --tasks fix-git overfull-hbox prove-plus-comm cobol-modernization \
  openssl-selfsigned-cert nginx-request-logging --timeout 2400
bash scripts/run_smoke.sh --label c-luna --model gpt56luna \
  --endpoint-prefix TASK_ALT2 -n 4 \
  --tasks fix-git overfull-hbox prove-plus-comm cobol-modernization \
  openssl-selfsigned-cert nginx-request-logging --timeout 2400
uv run python -m scripts.cost_report
```

The launcher checks `docker ps` before downloading tasks or making model calls.
Harbor runs `terminal-bench@2.0` with `harness.harbor_agent:SeedAgent`, one attempt
per task and zero Harbor retries. Dataset and image contents are unchanged.
Harbor uses its default container cleanup; no image/container pruning is used.
The launcher bounds job wall time, sends SIGINT on expiry, allows ten seconds
for cleanup, then kills remaining members of its own process group. A hard kill
can leave containers requiring inspection.

Artifacts:

- `logs/harbor/<run_id>/<trial>/result.json`: real verifier rewards, exceptions,
  timing, agent token/cost totals, and task revision.
- `logs/harbor/<run_id>/<trial>/verifier/`: verifier stdout and reward file.
- `logs/harbor/<run_id>/<trial>/agent/trace.jsonl`: instruction, actions,
  observations, served model and requested deployment, and ledger call IDs.
- `logs/harbor/<run_id>/<trial>/agent/calls/<call_id>/`: request JSON without
  authentication and raw response JSON. These are local research artifacts.
- `logs/<run_id>/command.json`, `summary.json`, `timing.jsonl`, and Harbor stdout
  and stderr: launch configuration, costs, and agent/job wall times.
- `costs/ledger.jsonl`: process-safe call accounting (normally append-only).

Task tags are inferred from Harbor's trial directory unless explicitly supplied.
The adapter translates bounded seed failures into Harbor's
`NonZeroAgentExitCodeError` so the verifier still runs and the failure remains
visible in `exception_info`. Cancellation propagates. Rewards of zero are valid
verifier results, not task passes. See `docs/smoke_run.md` for measured results.

## Retries, budget, and accounting

Each logical `complete` invocation appends one ledger row, even on HTTP errors,
malformed responses, timeouts, budget stops, or cancellation. Nested `attempts`
record each HTTP request's UTC start, elapsed time, status, request ID, and
allowlisted rate-limit headers. At most three retries follow 429 or 5xx replies.
`Retry-After` seconds/HTTP dates and Azure `retry-after-ms` are honored; otherwise
exponential backoff with jitter applies. A delay exceeding the remaining call
deadline ends the call without retrying early. Transport errors are retained
and not retried automatically because billing/completion may be ambiguous.
A successful response following an ambiguous 5xx retains its known response
cost separately; total call USD is null while earlier attempt billing is unknown.

Input includes cached input. Cached and reasoning counts retain null when
unavailable; reasoning is a subset of output and is never charged twice.
Both `cache_write_tokens` and `cache_creation_tokens` are recognized inside
`prompt_tokens_details`. Missing cache-write counters are assumed zero for
these Azure chat responses.
The served response model is used for pricing, with exact aliases and dated
snapshot suffixes resolved against `scripts/prices.json`. Unknown prices/counts
remain null. Existing CLI semantics and reported Claude costs are preserved.
The summary includes failed calls and explicitly counts unknown costs.

Rates are USD per million tokens. Public OpenAI standard rates are proxies for
these Azure deployments; Azure invoice rates were not verified. New rows carry
dated assumptions, including unverified DeepSeek/Kimi planning prices. Cache
writes, where reported separately, use the table's rate.

Each API backend instance has a $1 projected rollout cap by default. Before each
HTTP request, the guard reserves a conservative UTF-8-byte input estimate plus
256 framing tokens and the maximum completion, priced without cache discounts.
It rejects prompts above 200,000 estimated tokens. Successful known usage
replaces the reservation; 429 reservations are released. Reservations survive
ambiguous failures and unknown usage. These estimates are budget guards, not
invented ledger costs. One backend instance belongs to one sequential rollout.
The 19 planned attempts therefore reserve at most $19 under the price table;
this is not a global budget service for arbitrary future jobs.

The API backend does not establish Phase 1's sealed verifier filesystem and
network isolation protocol. Raw verifier logs must remain out of judge/evolver
inputs. Smoke results measure this seed on this host and task sample; they do
not establish full-dataset accuracy or a provider-wide concurrency guarantee.

## Smoke accounting correction

`scripts/reprice_api.py` repairs the initial Luna cache-write omission from
preserved raw API responses under a ledger lock. It preserves each original
and corrected record in `logs/cache-write-corrections.jsonl`, with a source
response hash and original USD on the corrected row. Run it only with jobs
stopped; repeated runs are no-ops. Raw Harbor results are never rewritten.
`scripts/smoke_report.py` reconciles original Harbor USD against the audit and
corrected ledger USD against raw usage. See `docs/smoke_run.md` for the exact
$0.00307390 adjustment and the live validation of the fixed parser.

## Tool protocol option (P1.2)

`HARNESS_TOOL_PROTOCOL=json|native` selects the seed protocol; the explicit
`tool_protocol` agent kwarg takes precedence. The default remains `json`.
Native is available with the API backend and exposes exactly `terminal`,
`read_file`, and `write_file`, finishing with a plain final message. It sends
Chat Completions function tools with parallel calls disabled and returns
observations using the corresponding tool-call IDs. All models receive the
same protocol prompt. Actions normalize into the existing trajectory JSONL
schema; original native messages remain in the raw API response files.
The API JSON prompt removes the sentence about avoiding model CLI tools.

Calibration uses `api_max_retries=0`, `rollout_budget_usd=1`, and a shared
`shared_budget_path` with `shared_budget_usd=40`. These are experiment controls;
the normal API retry default stays unchanged. Run the pinned 30-task avg@2
batches using `scripts/calibrate.py`; measured results and protocol/model
recommendation are in [calibration.md](calibration.md).

The completed P1.2 calibration recommends `gpt56terra` on `TASK_ALT2` with
`tool_protocol=json`, low reasoning, and concurrency 4: 28.3% avg@2 pass rate
and 1.7% no-action finishes on the fixed 30-task sample. The current native
Chat Completions configuration with low reasoning received HTTP 400 for both
`gpt56luna` and `gpt56terra`; native tools through Responses were not measured.
See [calibration.md](calibration.md) for the decision gates, costs, failures,
and the interrupted-run reconciliation.
