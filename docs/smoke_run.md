# Terminal-Bench 2 API smoke run

Date: 2026-09-09. All reported rewards come from Harbor 0.22's
unmodified TB2 verifiers on local Docker. No reward was inferred from
an agent's final answer. `scripts/smoke_report.py` reconciles every
trajectory call ID, served model, token count and USD total with the
ledger and Harbor result, and checks each verifier reward file.
The audited Luna cost adjustment below is checked separately.

## Fixed setup and task selection

Dataset: `terminal-bench@2.0`, upstream revision
`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. One attempt per task; zero
Harbor retries. Four metadata-easy tasks: `fix-git`, `overfull-hbox`,
`prove-plus-comm`, `cobol-modernization`. Two smaller medium tasks:
`openssl-selfsigned-cert` and `nginx-request-logging`, each with a
20-minute expert estimate in dataset metadata. These six were selected
before B and held fixed across all three models; this is an easier
diagnostic sample, not a random estimate of TB2 accuracy.

The seed uses low reasoning, 4,096 max completion tokens, 24 calls,
180 seconds per API call including retry backoff, and 30 seconds per
container command. Harbor retains task-defined build/verifier/agent
timeouts. Each rollout has a $1 projected budget cap; 19 planned
attempts fit under $25. Prices are dated estimates for Azure billing
from `scripts/prices.json`, not an Azure invoice. Public OpenAI rates
were checked on the official model pages:
[GPT-5 Mini](https://developers.openai.com/api/docs/models/gpt-5-mini),
[GPT-5.1](https://developers.openai.com/api/docs/models/gpt-5.1),
[Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Run totals

| Run | Deployment | Passes | Verifier results | Calls | USD | Job wall s | -n |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| smoke-extract-elf-20260909T173634776990Z | gpt-5-mini | 0/1 | 1 | 4 | 0.008233 | 70.3 | 1 |
| smoke-b-mini-20260909T173835202568Z | gpt-5-mini | 3/6 | 6 | 46 | 0.073761 | 171.1 | 4 |
| smoke-c-gpt51-20260909T174139182884Z | gpt-5.1 | 4/6 | 6 | 47 | 0.429726 | 147.3 | 4 |
| smoke-c-luna-20260909T174406584238Z | gpt56luna | 4/6 | 6 | 22 | 0.023800 | 64.2 | 4 |

## smoke-extract-elf-20260909T173634776990Z

Served model: `gpt-5-mini-2025-08-07`

```bash
uv run harbor run --dataset terminal-bench@2.0 --include-task-name extract-elf --agent harness.harbor_agent:SeedAgent --model gpt-5-mini --env docker --n-concurrent 1 --n-attempts 1 --max-retries 0 --job-name smoke-extract-elf-20260909T173634776990Z --jobs-dir /home/argustest/Nonverifiable-harness/logs/harbor --agent-kwarg backend=openai_api --agent-kwarg run_id=smoke-extract-elf-20260909T173634776990Z --agent-kwarg task_name=extract-elf --agent-kwarg ledger_path=/home/argustest/Nonverifiable-harness/costs/ledger.jsonl --agent-kwarg max_steps=24 --agent-kwarg timing_path=/home/argustest/Nonverifiable-harness/logs/smoke-extract-elf-20260909T173634776990Z/timing.jsonl
```

| Task | Reward | Steps | Input | Cached | Writes | Output | Reasoning | USD | Trial wall s | Agent s | 429 | Exception |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [extract-elf](../logs/harbor/smoke-extract-elf-20260909T173634776990Z/extract-elf__vseQPKM/result.json) | 0.0 | 4 | 12107 | 0 | 0 | 2603 | 1088 | 0.008233 | 64.3 | 27.1 | 0 | none |

API latency: median 3.09s, p95 18.57s, max 18.57s. Peak overlapping agents: 1; peak HTTP requests: 1. HTTP 429: 0; HTTP 5xx: 0.

Peak rolling 60-second load: 4 calls, 14,710 input+output tokens; 28,491 input+completion-cap tokens. These client-side measurements do not reproduce Azure's quota estimator.

Observed rate-limit header values (ranges for varying counters):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5-mini"
  ],
  "x-ratelimit-limit-requests": [
    "1000"
  ],
  "x-ratelimit-limit-tokens": [
    "1000000"
  ],
  "x-ratelimit-remaining-requests": [
    "999"
  ],
  "x-ratelimit-remaining-tokens": {
    "min": 991877.0,
    "max": 999691.0
  },
  "x-ratelimit-renewalperiod-requests": [
    "60"
  ],
  "x-ratelimit-renewalperiod-tokens": [
    "60"
  ],
  "x-ratelimit-reset-requests": [
    "0"
  ],
  "x-ratelimit-reset-tokens": [
    "0"
  ]
}
```

## smoke-b-mini-20260909T173835202568Z

Served model: `gpt-5-mini-2025-08-07`

```bash
uv run harbor run --dataset terminal-bench@2.0 --agent harness.harbor_agent:SeedAgent --model gpt-5-mini --env docker --n-concurrent 4 --n-attempts 1 --max-retries 0 --job-name smoke-b-mini-20260909T173835202568Z --jobs-dir /home/argustest/Nonverifiable-harness/logs/harbor --agent-kwarg backend=openai_api --agent-kwarg run_id=smoke-b-mini-20260909T173835202568Z --agent-kwarg endpoint_prefix=TASK --agent-kwarg ledger_path=/home/argustest/Nonverifiable-harness/costs/ledger.jsonl --agent-kwarg max_steps=24 --agent-kwarg timing_path=/home/argustest/Nonverifiable-harness/logs/smoke-b-mini-20260909T173835202568Z/timing.jsonl --include-task-name fix-git --include-task-name overfull-hbox --include-task-name prove-plus-comm --include-task-name cobol-modernization --include-task-name openssl-selfsigned-cert --include-task-name nginx-request-logging
```

| Task | Reward | Steps | Input | Cached | Writes | Output | Reasoning | USD | Trial wall s | Agent s | 429 | Exception |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [cobol-modernization](../logs/harbor/smoke-b-mini-20260909T173835202568Z/cobol-modernization__ocva6vb/result.json) | 1.0 | 3 | 10729 | 2048 | 0 | 2251 | 576 | 0.006723 | 59.1 | 23.9 | 0 | none |
| [fix-git](../logs/harbor/smoke-b-mini-20260909T173835202568Z/fix-git__G978qME/result.json) | 1.0 | 6 | 11417 | 2304 | 0 | 1185 | 640 | 0.004706 | 58.9 | 23.0 | 0 | none |
| [nginx-request-logging](../logs/harbor/smoke-b-mini-20260909T173835202568Z/nginx-request-logging__YPacujP/result.json) | 0.0 | 7 | 32540 | 12544 | 0 | 5176 | 1536 | 0.015665 | 80.4 | 58.1 | 0 | none |
| [openssl-selfsigned-cert](../logs/harbor/smoke-b-mini-20260909T173835202568Z/openssl-selfsigned-cert__pTzRvQx/result.json) | 0.0 | 6 | 35952 | 16000 | 0 | 5257 | 1024 | 0.015902 | 74.3 | 53.3 | 0 | none |
| [overfull-hbox](../logs/harbor/smoke-b-mini-20260909T173835202568Z/overfull-hbox__x2NoPEg/result.json) | 0.0 | 15 | 86797 | 49536 | 0 | 8099 | 4160 | 0.026752 | 166.8 | 96.0 | 0 | none |
| [prove-plus-comm](../logs/harbor/smoke-b-mini-20260909T173835202568Z/prove-plus-comm__CD6wEC9/result.json) | 1.0 | 9 | 11372 | 5632 | 0 | 1219 | 768 | 0.004014 | 70.6 | 27.7 | 0 | none |

API latency: median 3.28s, p95 18.40s, max 20.51s. Peak overlapping agents: 4; peak HTTP requests: 4. HTTP 429: 0; HTTP 5xx: 0.

Peak rolling 60-second load: 34 calls, 160,214 input+output tokens; 217,855 input+completion-cap tokens. These client-side measurements do not reproduce Azure's quota estimator.

Observed rate-limit header values (ranges for varying counters):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5-mini"
  ],
  "x-ratelimit-limit-requests": [
    "1000"
  ],
  "x-ratelimit-limit-tokens": [
    "1000000"
  ],
  "x-ratelimit-remaining-requests": [
    "998",
    "999"
  ],
  "x-ratelimit-remaining-tokens": {
    "min": 977431.0,
    "max": 999821.0
  },
  "x-ratelimit-renewalperiod-requests": [
    "60"
  ],
  "x-ratelimit-renewalperiod-tokens": [
    "60"
  ],
  "x-ratelimit-reset-requests": [
    "0"
  ],
  "x-ratelimit-reset-tokens": [
    "0",
    "1"
  ]
}
```

## smoke-c-gpt51-20260909T174139182884Z

Served model: `gpt-5.1-2025-11-13`

```bash
uv run harbor run --dataset terminal-bench@2.0 --agent harness.harbor_agent:SeedAgent --model gpt-5.1 --env docker --n-concurrent 4 --n-attempts 1 --max-retries 0 --job-name smoke-c-gpt51-20260909T174139182884Z --jobs-dir /home/argustest/Nonverifiable-harness/logs/harbor --agent-kwarg backend=openai_api --agent-kwarg run_id=smoke-c-gpt51-20260909T174139182884Z --agent-kwarg endpoint_prefix=TASK --agent-kwarg ledger_path=/home/argustest/Nonverifiable-harness/costs/ledger.jsonl --agent-kwarg max_steps=24 --agent-kwarg timing_path=/home/argustest/Nonverifiable-harness/logs/smoke-c-gpt51-20260909T174139182884Z/timing.jsonl --include-task-name fix-git --include-task-name overfull-hbox --include-task-name prove-plus-comm --include-task-name cobol-modernization --include-task-name openssl-selfsigned-cert --include-task-name nginx-request-logging
```

| Task | Reward | Steps | Input | Cached | Writes | Output | Reasoning | USD | Trial wall s | Agent s | 429 | Exception |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [cobol-modernization](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/cobol-modernization__Uv8YFua/result.json) | 1.0 | 6 | 23601 | 7296 | 0 | 4190 | 1617 | 0.063193 | 50.8 | 32.2 | 0 | none |
| [fix-git](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/fix-git__dDqLqv8/result.json) | 1.0 | 9 | 15513 | 4352 | 0 | 951 | 442 | 0.024005 | 39.2 | 21.8 | 0 | none |
| [nginx-request-logging](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/nginx-request-logging__Fv32vzd/result.json) | 1.0 | 5 | 25891 | 17408 | 0 | 4293 | 2677 | 0.055710 | 60.2 | 42.3 | 0 | none |
| [openssl-selfsigned-cert](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/openssl-selfsigned-cert__aNXT9Np/result.json) | 0.0 | 2 | 2051 | 0 | 0 | 716 | 22 | 0.009724 | 25.3 | 7.0 | 0 | none |
| [overfull-hbox](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/overfull-hbox__EkgtKKX/result.json) | 0.0 | 16 | 277268 | 178816 | 0 | 9934 | 4814 | 0.244757 | 143.3 | 90.8 | 0 | none |
| [prove-plus-comm](../logs/harbor/smoke-c-gpt51-20260909T174139182884Z/prove-plus-comm__LFTQ9UM/result.json) | 1.0 | 9 | 12100 | 4736 | 0 | 2254 | 1569 | 0.032337 | 54.8 | 30.7 | 0 | none |

API latency: median 3.08s, p95 13.69s, max 18.20s. Peak overlapping agents: 4; peak HTTP requests: 4. HTTP 429: 0; HTTP 5xx: 0.

Peak rolling 60-second load: 40 calls, 305,821 input+output tokens; 370,676 input+completion-cap tokens. These client-side measurements do not reproduce Azure's quota estimator.

Observed rate-limit header values (ranges for varying counters):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5.1"
  ],
  "x-ratelimit-limit-requests": [
    "10000"
  ],
  "x-ratelimit-limit-tokens": [
    "1000000"
  ],
  "x-ratelimit-remaining-requests": [
    "9999"
  ],
  "x-ratelimit-remaining-tokens": {
    "min": 960207.0,
    "max": 999778.0
  },
  "x-ratelimit-renewalperiod-requests": [
    "60"
  ],
  "x-ratelimit-renewalperiod-tokens": [
    "60"
  ],
  "x-ratelimit-reset-requests": [
    "0"
  ],
  "x-ratelimit-reset-tokens": [
    "0",
    "1",
    "2"
  ]
}
```

## smoke-c-luna-20260909T174406584238Z

Served model: `gpt-5.6-luna-2026-07-09`

```bash
uv run harbor run --dataset terminal-bench@2.0 --agent harness.harbor_agent:SeedAgent --model gpt56luna --env docker --n-concurrent 4 --n-attempts 1 --max-retries 0 --job-name smoke-c-luna-20260909T174406584238Z --jobs-dir /home/argustest/Nonverifiable-harness/logs/harbor --agent-kwarg backend=openai_api --agent-kwarg run_id=smoke-c-luna-20260909T174406584238Z --agent-kwarg endpoint_prefix=TASK_ALT2 --agent-kwarg ledger_path=/home/argustest/Nonverifiable-harness/costs/ledger.jsonl --agent-kwarg max_steps=24 --agent-kwarg timing_path=/home/argustest/Nonverifiable-harness/logs/smoke-c-luna-20260909T174406584238Z/timing.jsonl --include-task-name fix-git --include-task-name overfull-hbox --include-task-name prove-plus-comm --include-task-name cobol-modernization --include-task-name openssl-selfsigned-cert --include-task-name nginx-request-logging
```

| Task | Reward | Steps | Input | Cached | Writes | Output | Reasoning | USD | Trial wall s | Agent s | 429 | Exception |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [cobol-modernization](../logs/harbor/smoke-c-luna-20260909T174406584238Z/cobol-modernization__Mzr6ciJ/result.json) | 1.0 | 8 | 38741 | 0 | 37659 | 3801 | 1257 | 0.014192 | 54.9 | 36.5 | 0 | none |
| [fix-git](../logs/harbor/smoke-c-luna-20260909T174406584238Z/fix-git__KgTHzGb/result.json) | 1.0 | 7 | 14971 | 0 | 14260 | 708 | 203 | 0.004557 | 32.9 | 15.7 | 0 | none |
| [nginx-request-logging](../logs/harbor/smoke-c-luna-20260909T174406584238Z/nginx-request-logging__w5AtxTR/result.json) | 1.0 | 3 | 8101 | 0 | 7506 | 893 | 176 | 0.003067 | 33.2 | 15.3 | 0 | none |
| [openssl-selfsigned-cert](../logs/harbor/smoke-c-luna-20260909T174406584238Z/openssl-selfsigned-cert__jVPoKVf/result.json) | 1.0 | 2 | 2532 | 0 | 2053 | 767 | 130 | 0.001529 | 26.7 | 8.5 | 0 | none |
| [overfull-hbox](../logs/harbor/smoke-c-luna-20260909T174406584238Z/overfull-hbox__poRbHtY/result.json) | 0.0 | 1 | 222 | 0 | 0 | 223 | 152 | 0.000312 | 59.5 | 3.0 | 0 | none |
| [prove-plus-comm](../logs/harbor/smoke-c-luna-20260909T174406584238Z/prove-plus-comm__QWAFjaV/result.json) | 0.0 | 1 | 279 | 0 | 0 | 72 | 33 | 0.000142 | 26.9 | 1.6 | 0 | none |

API latency: median 2.47s, p95 6.84s, max 6.93s. Peak overlapping agents: 4; peak HTTP requests: 4. HTTP 429: 0; HTTP 5xx: 0.

Peak rolling 60-second load: 22 calls, 71,310 input+output tokens; 154,958 input+completion-cap tokens. These client-side measurements do not reproduce Azure's quota estimator.

Observed rate-limit header values (ranges for varying counters):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt56luna"
  ],
  "x-ratelimit-limit-requests": [
    "500"
  ],
  "x-ratelimit-limit-tokens": [
    "500000"
  ],
  "x-ratelimit-remaining-requests": [
    "497",
    "498",
    "499"
  ],
  "x-ratelimit-remaining-tokens": {
    "min": 489093.0,
    "max": 499721.0
  },
  "x-ratelimit-renewalperiod-requests": [
    "60"
  ],
  "x-ratelimit-renewalperiod-tokens": [
    "60"
  ],
  "x-ratelimit-reset-requests": [
    "0"
  ],
  "x-ratelimit-reset-tokens": [
    "0",
    "1"
  ]
}
```

## Evidence and accounting

Smoke-only usage: **$0.535520**, 119 calls. Ledger reconciliation passed for all listed trials. The full cost summary also includes pre-existing calls; they are excluded from this experiment's subtotal.

`costs/smoke_results.json` retains per-trial call IDs, result SHA-256 hashes,
task revisions, raw result/reward paths, latency, protocol errors, and command
timeouts. `costs/ledger.jsonl` retains per-call API attempt headers and raw
request/response paths. `logs/<run_id>/timing.jsonl` separates agent and job
elapsed time. Trial wall includes environment setup, verification and cleanup;
parallel trial times and summed API latency are not job wall time.

## Interpretation and Phase 1 recommendations

| Six-task comparison | Mini | GPT-5.1 | Luna |
| --- | ---: | ---: | ---: |
| Pass rate | 3/6 (50.0%) | 4/6 (66.7%) | 4/6 (66.7%) |
| Mean USD/rollout | $0.012294 | $0.071621 | $0.003967 |
| Mean agent seconds/rollout | 47.01 | 37.46 | 13.44 |
| Mean full trial seconds/rollout | 85.03 | 62.26 | 39.01 |
| Maximum observed USD/rollout | $0.026752 | $0.244757 | $0.014192 |
| Batch wall seconds | 171.12 | 147.32 | 64.20 |

Keep **GPT-5 Mini, low reasoning**, as the initial Phase 1 task-model baseline.
It follows the current resource plan, provides room for improvement, and costs
about one sixth as much as GPT-5.1 on this set. This recommendation is about a
useful evolution baseline; Luna is the measured cost/latency leader and deserves
a follow-up candidate pilot. Luna cost 3.10x less than Mini and its batch finished
2.67x faster, but part of that saving came from immediately finishing
`overfull-hbox` and `prove-plus-comm` without executing any action, claiming it
could not access tools. These were model failures, not Docker access failures.
Do not promote it on this six-task result alone.

None of the six-task pass rates establishes the plan's desired 20–40% seed
accuracy across TB2. Including A gives Mini 3/7, but this remains a deliberately
selected, tiny sample. Run a stratified pilot before fixing the task model for
formal evolution. The single-attempt results cannot distinguish sampling noise
from stable model differences. GPT-5.1's one additional pass over Mini does not
justify its 5.83x sample cost for the initial baseline.

Use **`-n 4` as the measured initial setting on this 8-vCPU/31-GB host** for
similarly sized tasks. Each comparison batch reached four overlapping agent
intervals and four concurrent HTTP requests. There were no 429s, 5xx responses,
HTTP retries, API timeouts, Harbor timeouts, or trial exceptions. This establishes
a working smoke setting, not a sustained-load guarantee or safety above four.
Task metadata allocates up to 4 GiB for overfull-hbox and 2 GiB for the other
selected tasks; larger Phase 1 tasks may require lower concurrency.

Observed advertised limits were Mini **1,000 RPM / 1,000,000 TPM**, GPT-5.1
**10,000 RPM / 1,000,000 TPM**, and Luna **500 RPM / 500,000 TPM**. Peak rolling
60-second client loads were respectively **34 / 40 / 22 calls**, and
**217,855 / 370,676 / 154,958 tokens** when each call's actual input is combined
with its 4,096-token completion cap. These are below the advertised limits;
they are not Azure's internal quota calculation. Remaining-request headers
stayed at limit-minus-one even during overlap, so they are not evidence of a
reliably observed aggregate quota bucket. No `Retry-After` header occurred in
live runs; seconds, HTTP-date and millisecond handling were tested with mocked
HTTP.

Retain **180 seconds per API call including backoff, 30 seconds per command,
24 calls, and the task-defined Harbor agent timeouts** for the next comparable
pilot. The slowest observed API call is in the run tables; agent wall time was
at most 96 seconds. These short successes do not justify tightening timeouts
for the full dataset. Use a 2,400-second job deadline for this six-task batch;
longer batches need a deadline based on the number of waves and task timeouts.
Keep 4,096 completion tokens for a fixed baseline, while tracking truncated
responses and budget exhaustion during the broader pilot.

Set a **$0.25 projected cap per Mini pilot rollout** using
`--agent-kwarg rollout_budget_usd=0.25` in direct Harbor launches. The observed
Mini maximum was $0.026752. The smoke default remains $1; the backend stops
before a next request would exceed its conservative token-based reservation.
Keep a separate job/experiment budget when scaling beyond these 19 trials.

## Failures, repairs, and accounting audit

- A (`extract-elf`) returned reward 0: the script existed, but the verifier
  found 0% of the required reference values. This was a correctness failure.
- Mini failed nginx's verifier, both Mini and GPT-5.1 failed the certificate
  task's Python verification-script check, and all models failed overfull-hbox.
  Detailed verifier stdout is next to each linked trial result.
- Mini recovered from two JSON protocol errors, and Luna recovered from one;
  all are recorded as observations. No container command timed out.
- Docker access, dataset download, image pulls, and verifier execution worked.
  No tmux installation, dataset patch, or verifier patch was necessary. The
  existing protocol-error feedback loop handled invalid JSON without retries
  of whole tasks. Harbor cleaned up all created containers; final `docker ps`
  was empty. No images or unrelated containers were pruned.
- The adapter now translates bounded seed errors into Harbor's
  `NonZeroAgentExitCodeError`, allowing verification after an exhausted/failed
  seed. This change was made after B started and before C; none of the 19
  trials exercised that error path, so the comparison's actions were unaffected.

Luna exposed `prompt_tokens_details.cache_write_tokens`, which the initial
parser missed. After C finished, `scripts/reprice_api.py` normalized **61,478
cache-write tokens across 14 calls**, adding **$0.00307390** at the table's
$0.25/M write rate instead of $0.20/M ordinary input. The original Luna job and
Harbor agent USD total is **$0.02072600**; corrected ledger/report USD is
**$0.02379990**. Original Harbor result files, reward files, model responses,
and trajectories were preserved unchanged. Every original ledger row and its
corrected version are retained in
[the correction audit](../logs/cache-write-corrections.jsonl); corrected rows
also record the source-response SHA-256 and original cost. The report checks
original Harbor USD against that audit and corrected USD against raw usage and
the current price table. Token counters shared with Harbor still match exactly.
This was an explicit audited repair, not an unrecorded rewrite of experiment
results. The repair script is idempotent.

A subsequent **live validation call** through the fixed Luna backend recorded
1,822 input tokens, zero cache reads, 1,819 cache writes, and 9 output tokens:
**$0.00046615, 1.647 seconds**. Its ledger call ID is
`8757831e247d4f81bf690dfeaec5dc17`, run ID
`api-cache-write-validation-20260909`; raw evidence is under
`logs/api-cache-write-validation/`. This is an accounting probe, excluded from
benchmark pass rates. **All API usage for this task totals $0.53598615** including
this validation, far below $25. The four Harbor jobs used **452.92 seconds
(7.55 minutes)** of summed sequential job wall time; A+B used 241.40 seconds,
so C was not skipped. This timing excludes implementation and between-run work.

## Verification and reproduction

`uv run pytest -q`: **51 passed**. The suite includes mocked HTTP retries,
Retry-After formats, errors, cancellation, unknown usage, budget rejection,
served snapshots, cache-write pricing, correction idempotency, backend selection,
and Harbor's verifiable failure type. A full source lint attempt found 46
pre-existing violations confined to protected `scripts/azure_probe.py`; it was
left unchanged. The following scoped lint and shell checks passed:

```bash
uv run ruff check harness scripts/backend_probe.py scripts/cost_report.py \
  scripts/smoke.py scripts/smoke_report.py scripts/reprice_api.py tests
bash -n scripts/run_smoke.sh
bash scripts/run_smoke.sh --dry-run
```

All 19 trial rewards, saved API usage, served names, unique trajectory call IDs,
original Harbor totals and corrected ledger totals were reconciled by:

```bash
uv run python -m scripts.smoke_report \
  smoke-extract-elf-20260909T173634776990Z \
  smoke-b-mini-20260909T173835202568Z \
  smoke-c-gpt51-20260909T174139182884Z \
  smoke-c-luna-20260909T174406584238Z \
  --notes logs/smoke-analysis.md
uv run python -m scripts.cost_report
```

No commit, push, model CLI call, or external publication was performed. `.env`
remains mode 600. The remaining Phase 1 issues are broader model calibration,
sustained-load concurrency, Azure invoice-rate verification, and the planned
verifier isolation boundary; they do not block this completed smoke run.
