# Terminal-Bench 2 task-model and protocol calibration

Date: 2026-09-10. P1.1/P1.2. Rewards below come from unmodified Harbor 0.22 Docker verifiers; model completion claims never establish correctness.

## Setup and reproducibility

Dataset `terminal-bench@2.0`, commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`. The cached `task.toml` field `metadata.difficulty` contains 4 easy, 55 medium, and 30 hard tasks (89 total). Fixed sampling seed **260910**; sorted task names, independent per-stratum shuffles from one seeded Python RNG, and Hamilton largest-remainder allocation with lexical tie-breaking. Search is allocated first, then anchor, then sealed. The six smoke tasks were eligible with no preference; only `cobol-modernization` was selected.

| Split | Easy | Medium | Hard | Total |
| --- | ---: | ---: | ---: | ---: |
| search | 1 | 11 | 6 | 18 |
| anchor | 0 | 4 | 2 | 6 |
| sealed | 0 | 4 | 2 | 6 |

The proportional 30-task allocation has only one easy task; it is impossible to put an easy task in every split while retaining that allocation. Metadata hashes are stored with each selected task. Reproduce with `uv run python -m scripts.make_tb2_split`.

Each original configuration uses all 30 tasks × 2 fresh attempts (avg@2), low reasoning, 4,096 max completion tokens, 24 model calls, 30 seconds per command, 180 seconds per API call, and unchanged task-defined Harbor build/agent/verifier timeouts. Temperature and generation seed are omitted (provider defaults); 260910 is the dataset-sampling and analysis seed. Zero API retries and zero Harbor retries. No planning, self-verification, model-specific text, or rescue logic was added. The API JSON prompt only removes the CLI-residue sentence. Native changes only protocol instructions and message transport: exactly terminal/read_file/write_file function tools, `parallel_tool_calls=false`, and a plain final message to finish. Native actions normalize into the same assistant/observation/finish JSONL records. Native here means Chat Completions function tools; Responses API was not measured.

Batches run sequentially on the same host. The initial mini/json batch used concurrency 8. The recovery and all remaining original batches use concurrency 4, as required by the resume instruction. These 30-second command limits are part of the unchanged seed and do not by themselves establish host overload. Image pulls and long verifiers affect batch wall time, so compare agent seconds separately. The first batch warms Docker images for later batches. Source hashes are archived in [the manifest](../logs/calibration_source_manifest.json).

Run a batch with `uv run python -m scripts.calibrate luna-native --concurrency 4`; substitute any configuration below. Each launch archives its full pinned Harbor config in `logs/calibration-<configuration>-260910/config.json`. Rebuild this report with `uv run python -m scripts.calibration_report`.

USD uses measured API token usage and `scripts/prices.json` standard API proxy rates, not verified Azure invoice charges. Terra is $2 input / $0.20 cached input / $2.50 cache write / $12 output per million tokens in the unchanged [recorded price table](../scripts/prices.json). Reasoning is included in output. Every logical API call is ledgered, with HTTP attempts and rate-limit headers nested under it. A process-safe shared reservation guard caps the original experiment at $40; every rollout has a $1 projected cap. Ambiguous request charges retain conservative reservations.

The two `-medium` follow-up rows use the identical 30-task split, seed, JSON prompt, 24-call cap, 4,096 completion tokens, command/API timeouts, and zero retries; only `reasoning_effort=medium` changes model behavior. Both follow-up batches run sequentially at `--n-concurrent 3` while W9 shares Docker, with at most six combined `alexgshaw` containers. They share a separate **$8** guard in `costs/calibration_medium_budget.json` and retain the **$1** per-rollout guard. Reproduce with `sg docker -c 'uv run python -m scripts.calibrate mini-json-medium --concurrency 3'` (substitute `luna-json-medium`); regenerate this extension with `uv run python -m scripts.calibration_medium_report`. The original six rows and their accounting below are retained.

## Metrics and results

Primary pass = verifier reward 1 with no trial exception. Solver failures/timeouts count as failures even if a raw reward is 1. All scheduled attempts remain in the fixed denominator (60 overall; 36/12/12 by split); build/grader failures are explicitly listed and counted as zero. Interrupted attempts are replaced by their recovery results; killed attempts are not counted as failures or extra rollouts. Any unfinished batch is provisional, not a completed estimate. No-action means a finish record with zero executed-tool observations; protocol-error feedback is not a tool observation. Steps count logical API calls, including failed calls and finish. Cost/steps means use result-bearing trials; agent time averages trials with recorded agent execution. Batch wall includes setup, verification and cleanup. Confidence intervals resample tasks (both attempts together), 10,000 bootstrap draws, seed 260910.
Rows rejected by the API have zero operational success, not a measured seed-accuracy estimate. Their agent seconds measure rejection overhead; their no-action gate is unassessed.

| Configuration | Results / verifier | Pass avg@2 | 95% task CI | Search | Anchor | Sealed | No-action |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mini-json | 60/60; 53 | 8.3% (5/60) | 1.7%–16.7% | 8.3% | 16.7% | 0.0% | 0/60 (0.0%) |
| mini-native | 60/60; 54 | 8.3% (5/60) | 1.7%–16.7% | 5.6% | 8.3% | 16.7% | 2/60 (3.3%) |
| luna-json | 60/60; 52 | 23.3% (14/60) | 11.7%–36.7% | 27.8% | 25.0% | 8.3% | 7/60 (11.7%) |
| luna-native | 60/60; 60 | 0.0% (0/60); API rejected | not applicable | 0.0% | 0.0% | 0.0% | 0 finishes; unassessed |
| terra-native | 60/60; 60 | 0.0% (0/60); API rejected | not applicable | 0.0% | 0.0% | 0.0% | 0 finishes; unassessed |
| terra-json | 60/60; 49 | 28.3% (17/60) | 13.3%–43.3% | 38.9% | 16.7% | 8.3% | 1/60 (1.7%) |
| mini-json-medium | 60/60; 55 | 13.3% (8/60) | 5.0%–23.3% | 16.7% | 8.3% | 8.3% | 0/60 (0.0%) |
| luna-json-medium | 60/60; 50 | 33.3% (20/60) | 18.3%–50.0% | 38.9% | 33.3% | 16.7% | 7/60 (11.7%) |

| Configuration | Mean steps | Mean known USD | Max known USD | Known total USD | Mean agent s | Batch wall s | Concurrency | 429 / 5xx | Harbor timeouts | Command timeouts | HTTP 400 | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mini-json | 4.47 | 0.005871 | 0.022926 | 0.352279 | 30.05 | 1323.48 | 8 | 0 / 0 | 0 | 7 | 0 | 0 |
| mini-native | 4.53 | 0.005833 | 0.031123 | 0.387583 | 27.12 | ≥1637.21 | 4 | 0 / 0 | 0 | 6 | 0 | 2 |
| luna-json | 6.63 | 0.010849 | 0.074944 | 0.650967 | 47.08 | 1635.12 | 4 | 0 / 0 | 0 | 8 | 0 | 0 |
| luna-native | 1.00 | 0.000000 | 0.000000 | 0.000000 | 0.44 | 972.23 | 4 | 0 / 0 | 0 | 0 | 60 | 60 |
| terra-native | 1.00 | 0.000000 | 0.000000 | 0.000000 | 0.40 | 981.57 | 4 | 0 / 0 | 0 | 0 | 60 | 60 |
| terra-json | 6.10 | 0.124265 | 0.855366 | 7.455917 | 68.23 | 1495.78 | 4 | 0 / 0 | 0 | 11 | 0 | 1 |
| mini-json-medium | 4.63 | 0.012434 | 0.042391 | 0.746036 | 54.38 | 2151.86 | 3 | 0 / 0 | 0 | 5 | 0 | 0 |
| luna-json-medium | 7.55 | 0.015929 | 0.133919 | 0.955728 | 77.41 | 2639.47 | 3 | 0 / 0 | 0 | 10 | 0 | 0 |

Paired task-bootstrap contrasts (first minus second; provisional if either batch is unfinished). API-rejected configurations are omitted from capability contrasts:

| Contrast | Difference (pp) | 95% CI (pp) |
| --- | ---: | ---: |
| mini-native − mini-json | 0.0 | -6.7 to 8.3 |
| luna-json − mini-json | 15.0 | 6.7 to 25.0 |
| terra-json − luna-json | 5.0 | -5.0 to 16.7 |

## Decision rule

Eligibility requires a completed 30-task avg@2 pass rate inside 15–45% and no-action termination below 5%, with the identical seed for every model.

| Configuration | Pass-rate gate | No-action gate | Common-prompt gate | Eligible |
| --- | --- | --- | --- | --- |
| mini-json | below 15% | pass | pass | no |
| mini-native | below 15% | pass | pass | no |
| luna-json | within range | fail | pass | no |
| luna-native | not measurable: API rejects configuration | not assessed: no model response | pass | no: unsupported as tested |
| terra-native | not measurable: API rejects configuration | not assessed: no model response | pass | no: unsupported as tested |
| terra-json | within range | pass | pass | yes |
| mini-json-medium | below 15% | pass | pass | no |
| luna-json-medium | within range | fail | pass | no |

<!-- RECOMMENDATION -->
**Eligibility re-applied after both medium-reasoning batches.** Every eligible configuration is listed below so the user can choose. Costs use measured known USD per completed rollout; the 2,800-rollout projection covers task-model API calls only and excludes judges, evolvers, and cross-judges.

| Eligible configuration | Reasoning | Pass avg@2 | No-action | USD / rollout | Projected USD / 2,800 rollouts |
| --- | --- | ---: | ---: | ---: | ---: |
| terra-json | low | 28.3% | 1.7% | 0.124265 | 347.94 |

The lowest-cost eligible option is **terra-json** at **$0.124265/rollout** (**$347.94** for 2,800). This is a cost-based recommendation within the predefined gates, not a claim of a statistically established capability advantage. No pilot configuration is changed by this report.

Even the lowest-cost eligible option's task-only projection exceeds the **$300 whole-pilot guard** in `docs/decisions-260910.md` Section 3, before other model roles. The current eligibility gates and 2,800-rollout pilot therefore have no measured eligible option that fits that guard. This report does not change the guard or authorize a pilot run.

### Medium versus low reasoning

| Configuration | Pass change (pp; paired 95% CI) | No-action, low → medium | USD / rollout, low → medium | Cost ratio | Agent s, low → medium | Agent-time ratio |
| --- | --- | --- | --- | ---: | --- | ---: |
| mini-json-medium | +5.0 (-1.7 to +11.7) | 0/60 → 0/60 | 0.005871 → 0.012434 | 2.12× | 30.05 → 54.38 | 1.81× |
| luna-json-medium | +10.0 (-1.7 to +21.7) | 7/60 → 7/60 | 0.010849 → 0.015929 | 1.47× | 47.08 → 77.41 | 1.64× |

Mini's escalation rule is now measured: medium yields 8/60 passes (13.3%) and 0/60 no-action finishes; it is ineligible.

Luna at medium records 7/60 (11.7%) no-action finishes versus 7/60 (11.7%) at low. The no-action gate still fails; medium reasoning does not resolve the seed concern on this sample.

No-action measures explicit finish records with zero executed tools. Empty API answers remain separate operational failures, even when no tool ran. Mini/medium recorded 18 empty-answer failures; Luna/medium recorded 4. A lower no-action finish rate therefore does not by itself establish that every form of failing before tool use was resolved.

Agent latency is reported separately from batch wall time. The medium jobs use concurrency 3 and share Docker with W9; the original Mini/JSON and Luna/JSON rows used 8 and 4. Batch-time and latency differences therefore include host effects. All solver failures remain in the fixed denominator; the measured 30-task confidence intervals remain wide.

### Follow-up accounting and verification

Both jobs completed **120 attempt slots** with **105 verifier rewards**. All **731 request artifacts** reconcile to the ledger. Known follow-up cost is **$1.701763**; guard used/reserved is **$1.701763 / $8**, including **$0.000000** retained reservations and 0 unknown-cost calls. All rollout guards stayed at or below $1. [Follow-up audit](../logs/calibration-medium-followup-260910/audit.json).

| Job | Min MemAvailable GiB | Samples below 6 GiB | Max alexgshaw at admission | Max periodic task containers | Docker admission pauses |
| --- | ---: | ---: | ---: | ---: | ---: |
| [calibration-mini-json-medium-260910](../logs/calibration-mini-json-medium-260910/memory.jsonl) | 22.88 | 0 | 4 | 5 | 0 |
| [calibration-luna-json-medium-260910](../logs/calibration-luna-json-medium-260910/memory.jsonl) | 21.04 | 0 | 4 | 5 | 0 |

MemAvailable admission starts at 10 GiB, pauses new trials below 6 GiB, and resumes at 10 GiB; running trials finish. Memory and Docker statistics are sampled every two minutes, with admission checks between trials. Source hashes verify unchanged harness, prompts, split, and prices. The original report and recommendation are archived [here](../logs/calibration-medium-followup-260910/calibration-before.md). Focused test and lint results are recorded in [verification](../logs/calibration-medium-followup-260910/verification.txt). No commit, push, or model CLI call was made.

Admission counts come from `docker ps` image names; periodic counts cover task service names ending in `__env-main-1` in Docker stats. Mini admission events are in its memory log; Luna admission events are in [docker_admission.jsonl](../logs/calibration-luna-json-medium-260910/docker_admission.jsonl). The completed follow-up left no containers of its own running.
<!-- END RECOMMENDATION -->

## Interrupted runs, memory guard, and accounting

Two earlier drivers were killed by a low-memory watchdog, not a kernel OOM. The second interruption occurred with MemAvailable above 20 GB (operator report); low MemFree reflected page cache. Orphan containers had already been removed before this recovery. Disk reconciliation found 51 finalized mini/native trial results in the original job, plus two verifier-bearing results in `calibration-mini-native-260910-resume`: `adaptive-rejection-sampler` and `pypi-server`. The job-level result.json is never counted as a trial.

Exactly seven remaining slots were launched once in `calibration-mini-native-260910-recovery2`: `query-optimize`, `filter-js-from-html`, `gcode-to-text`, `headless-terminal`, `protein-assembly`, `raman-fitting`, and `torch-pipeline-parallelism`. Per-task links mark all nine replacement results as resumed. Killed attempts and unfinished verifier stdout never supply a reward. Existing finalized solver errors were retained as operational failures, not retried. Only a finalized trial with a verifier reward contributes to the verifier count; command failures without a verifier remain explicit failures in the fixed avg@2 denominator. See [attempt reconciliation](../logs/calibration_attempt_reconciliation.json).

The resumed batches ran in order: luna/native, luna/json, terra/native, then budget-permitting terra/json, one job at a time. Each invocation explicitly uses `--n-concurrent 4`. Before Harbor starts, `free -g` is logged and MemAvailable from `/proc/meminfo` must be at least 10 GiB. During each job, `free -g` and `docker stats --no-stream` are sampled every two minutes, with additional admission checks between trials. Below 6 GiB, new trials pause while running trials finish; admission resumes at 10 GiB. Memory evidence is linked below.

| Job segment | Final results / verifier rewards | Wall s | Min sampled available GiB | Samples below 6 GiB | Memory log |
| --- | ---: | ---: | ---: | ---: | --- |
| calibration-mini-json-260910 | 60 / 53 | 1323.48 | unknown | unknown | not recorded |
| calibration-mini-native-260910 | 51 / 45 | ≥970.52 | unknown | unknown | not recorded |
| calibration-mini-native-260910-recovery2 | 7 / 7 | 605.41 | 23.91 | 0 | [samples](../logs/calibration-mini-native-260910-recovery2/memory.jsonl) |
| calibration-mini-native-260910-resume | 2 / 2 | ≥61.28 | 23.00 | 0 | [samples](../logs/calibration-mini-native-260910-resume/memory.jsonl) |
| calibration-luna-json-260910 | 60 / 52 | 1635.12 | 23.44 | 0 | [samples](../logs/calibration-luna-json-260910/memory.jsonl) |
| calibration-luna-native-260910 | 60 / 60 | 972.23 | 23.93 | 0 | [samples](../logs/calibration-luna-native-260910/memory.jsonl) |
| calibration-terra-native-260910 | 60 / 60 | 981.57 | 24.70 | 0 | [samples](../logs/calibration-terra-native-260910/memory.jsonl) |
| calibration-terra-json-260910 | 60 / 49 | 1495.78 | 23.68 | 0 | [samples](../logs/calibration-terra-json-260910/memory.jsonl) |

Interrupted segment wall times are lower bounds from the last persisted Harbor update; exact watchdog kill timestamps were not retained. Mini/native batch wall is the sum of those lower bounds and the measured final recovery wall; driver downtime is excluded. All other completed batch walls are measured by the launcher. Historical mini/json used concurrency 8; this limits causal latency comparisons with the later batches.

Experiment known API cost, including interrupted work: **$8.846745**. Shared guard used/reserved: **$12.509495 / $40**. Interrupted mini/native work accounts for $0.037620 of known cost and is included in configuration totals, but excluded from completed-rollout means. `costs/summary.md` covers the entire project ledger; these historical totals cover the original six configurations and diagnostic; the medium follow-up is accounted separately above.

Two lost ledger writes were reconstructed from orphan request artifacts, with unknown dispatch/status, response, usage, cost, and latency explicitly preserved. Their existing conservative reservations total **$0.023641**; they were not released or charged twice. API error calls also retain reservations when usage is unavailable. [Recovery audit](../logs/calibration_ledger_recovery.jsonl). Every saved calibration request now has a ledger record. Reported HTTP status counts exclude these unknown statuses.

Luna/native returned HTTP 400 before model generation. One separately ledgered diagnostic call captured the endpoint error: function tools with reasoning_effort are unsupported for gpt56luna on Chat Completions; the service suggests Responses or reasoning `none`. The calibration retains its common Chat Completions transport and low reasoning. Thus this row measures an unsupported API configuration, not Luna's task-solving ability. The diagnostic is excluded from benchmark denominators and included in the $40 guard. [Captured rejection](../logs/calibration-native-diagnostic/error-response.txt). HTTP error calls provide no token usage or invoice evidence, so their costs remain unknown; zero known USD must not be read as zero billed USD.

The optional Terra/json budget check repriced Luna/json's measured tokens at Terra rates, then added a 50% margin: $9.76, against $35.34 remaining at the check. This is a projection, not an assumption of identical token use; the shared $40 guard remains binding. [Projection audit](../logs/calibration_terra_json_projection.json).

## Per-task evidence and operational failures

### mini-json

Served models: gpt-5-mini-2025-08-07. Calls: 268; unknown-cost calls: 0. Agent started: 60/60. No-action among started agents: 0.0%. Exceptions: `{"NonZeroAgentExitCodeError": 2, "RuntimeError": 7}`.
Calls with a served-model response: 268; HTTP 400 rejections: 0.

Failure messages and counts: `{"Backend failed: API returned no answer": 1, "Command timed out after 30 seconds": 7, "Seed exhausted its 24-call limit": 1}`.

No-action traces: none.

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [0.0](../logs/harbor/calibration-mini-json-260910/bn-fit-modify__iSb5UF9/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/bn-fit-modify__izMshru/result.json) | 0% | 4.5 | 0.009540 | 38.1 | none |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-mini-json-260910/break-filter-js-from-html__bLbbJTC/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/break-filter-js-from-html__debSxwY/result.json) | 0% | 4.5 | 0.002464 | 16.0 | none |
| build-pmars | search | [None](../logs/harbor/calibration-mini-json-260910/build-pmars__Bbk5GaZ/result.json), [None](../logs/harbor/calibration-mini-json-260910/build-pmars__UQbwUjv/result.json) | 0% | 1.5 | 0.001146 | 36.4 | RuntimeError, RuntimeError |
| caffe-cifar-10 | search | [0.0](../logs/harbor/calibration-mini-json-260910/caffe-cifar-10__78qRPPw/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/caffe-cifar-10__ZrisqY3/result.json) | 0% | 3.0 | 0.003930 | 21.4 | none |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-mini-json-260910/cancel-async-tasks__ycqC5B9/result.json), [1.0](../logs/harbor/calibration-mini-json-260910/cancel-async-tasks__zZgMrdJ/result.json) | 50% | 2.0 | 0.002638 | 13.2 | none |
| cobol-modernization | search | [1.0](../logs/harbor/calibration-mini-json-260910/cobol-modernization__Sqsyo2M/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/cobol-modernization__Vt9Tibv/result.json) | 50% | 3.0 | 0.005431 | 23.0 | none |
| count-dataset-tokens | search | [None](../logs/harbor/calibration-mini-json-260910/count-dataset-tokens__JtbfaBn/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/count-dataset-tokens__v5CScJT/result.json) | 0% | 3.0 | 0.002107 | 27.3 | RuntimeError |
| dna-assembly | search | [0.0](../logs/harbor/calibration-mini-json-260910/dna-assembly__DACiMpc/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/dna-assembly__VUwYBtK/result.json) | 0% | 7.5 | 0.006343 | 30.1 | none |
| hf-model-inference | search | [None](../logs/harbor/calibration-mini-json-260910/hf-model-inference__EcepAxZ/result.json), [1.0](../logs/harbor/calibration-mini-json-260910/hf-model-inference__XnMDMzv/result.json) | 50% | 5.0 | 0.009007 | 57.7 | RuntimeError |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-mini-json-260910/make-doom-for-mips__Dr9t23R/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/make-doom-for-mips__im2xYmR/result.json) | 0% | 14.0 | 0.019862 | 66.3 | none |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-mini-json-260910/mteb-leaderboard__TY5nhzj/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/mteb-leaderboard__djzLfUF/result.json) | 0% | 13.5 | 0.005199 | 44.3 | NonZeroAgentExitCodeError |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-mini-json-260910/mteb-retrieve__V3RPYir/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/mteb-retrieve__j3Uxv78/result.json) | 0% | 2.5 | 0.002644 | 27.1 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-mini-json-260910/path-tracing__uim6p6d/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/path-tracing__ymFtUUv/result.json) | 0% | 3.5 | 0.004659 | 16.3 | none |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-mini-json-260910/pytorch-model-cli__PeTwxyC/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/pytorch-model-cli__aCF3Wro/result.json) | 0% | 3.0 | 0.003651 | 18.5 | none |
| qemu-alpine-ssh | search | [None](../logs/harbor/calibration-mini-json-260910/qemu-alpine-ssh__D2JEHHz/result.json), [None](../logs/harbor/calibration-mini-json-260910/qemu-alpine-ssh__rwany7S/result.json) | 0% | 2.0 | 0.003093 | 49.5 | RuntimeError, RuntimeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-mini-json-260910/query-optimize__cwrcbfX/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/query-optimize__uZTgr7N/result.json) | 0% | 4.0 | 0.003591 | 18.3 | none |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-mini-json-260910/sqlite-db-truncate__bx7E5Af/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/sqlite-db-truncate__fW3YABJ/result.json) | 0% | 6.0 | 0.006611 | 23.8 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-mini-json-260910/write-compressor__NU55kpX/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/write-compressor__mtbgqHQ/result.json) | 0% | 3.5 | 0.003825 | 19.2 | none |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-mini-json-260910/chess-best-move__2ew3eeX/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/chess-best-move__eb7ZRBY/result.json) | 0% | 5.0 | 0.004580 | 15.3 | none |
| compile-compcert | anchor | [None](../logs/harbor/calibration-mini-json-260910/compile-compcert__DaFf7uo/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/compile-compcert__WH6rjCo/result.json) | 0% | 3.0 | 0.003710 | 31.4 | RuntimeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-mini-json-260910/configure-git-webserver__5HXLtDF/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/configure-git-webserver__wvGJUTo/result.json) | 0% | 4.0 | 0.006605 | 49.5 | none |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-mini-json-260910/filter-js-from-html__Gkhr3jL/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/filter-js-from-html__UWqveyt/result.json) | 0% | 2.0 | 0.004942 | 19.2 | none |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-mini-json-260910/polyglot-rust-c__Ji3NfHC/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/polyglot-rust-c__m7QX8Hy/result.json) | 0% | 5.5 | 0.015324 | 54.0 | none |
| pypi-server | anchor | [1.0](../logs/harbor/calibration-mini-json-260910/pypi-server__JMojMXd/result.json), [1.0](../logs/harbor/calibration-mini-json-260910/pypi-server__owAJXXu/result.json) | 100% | 2.0 | 0.003827 | 28.4 | none |
| adaptive-rejection-sampler | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/adaptive-rejection-sampler__kxMwwc2/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/adaptive-rejection-sampler__nbbQpqr/result.json) | 0% | 2.0 | 0.009355 | 33.3 | NonZeroAgentExitCodeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/gcode-to-text__BWxhwW5/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/gcode-to-text__CKj4Yiq/result.json) | 0% | 5.0 | 0.004661 | 16.2 | none |
| headless-terminal | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/headless-terminal__Z7ZVidL/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/headless-terminal__apeYPqa/result.json) | 0% | 6.5 | 0.008555 | 35.1 | none |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/protein-assembly__YDmiWBq/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/protein-assembly__dNH5w4Y/result.json) | 0% | 4.0 | 0.002145 | 11.0 | none |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/raman-fitting__S8obqN4/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/raman-fitting__XLZ3ZrY/result.json) | 0% | 6.5 | 0.008719 | 34.4 | none |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-mini-json-260910/torch-pipeline-parallelism__BSznJtT/result.json), [0.0](../logs/harbor/calibration-mini-json-260910/torch-pipeline-parallelism__eNqCaCa/result.json) | 0% | 2.5 | 0.007977 | 27.4 | none |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5-mini"
  ],
  "x-ratelimit-limit-requests": {
    "min": 1000.0,
    "max": 1000.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 1000000.0,
    "max": 1000000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 998.0,
    "max": 999.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 966036.0,
    "max": 999811.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 2.0
  }
}
```

### mini-native

Served models: gpt-5-mini-2025-08-07. Calls: 301; unknown-cost calls: 2. Agent started: 60/60. No-action among started agents: 3.3%. Exceptions: `{"RuntimeError": 6, "NonZeroAgentExitCodeError": 6}`.
Calls with a served-model response: 299; HTTP 400 rejections: 0.

Failure messages and counts: `{"Command timed out after 30 seconds": 6, "Backend failed: API returned no answer": 6}`.

No-action traces: [polyglot-rust-c__ZxSUiDw](../logs/harbor/calibration-mini-native-260910/polyglot-rust-c__ZxSUiDw/agent/trace.jsonl), [polyglot-rust-c__q6uA3Rt](../logs/harbor/calibration-mini-native-260910/polyglot-rust-c__q6uA3Rt/agent/trace.jsonl).

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [0.0](../logs/harbor/calibration-mini-native-260910/bn-fit-modify__QsAPtdz/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/bn-fit-modify__ey8unsa/result.json) | 0% | 5.0 | 0.011441 | 35.4 | none |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-mini-native-260910/break-filter-js-from-html__FGebZQm/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/break-filter-js-from-html__fb2doKj/result.json) | 0% | 5.0 | 0.002614 | 15.5 | none |
| build-pmars | search | [None](../logs/harbor/calibration-mini-native-260910/build-pmars__hPrzabG/result.json), [None](../logs/harbor/calibration-mini-native-260910/build-pmars__rgdnpQu/result.json) | 0% | 1.0 | 0.000433 | 33.2 | RuntimeError, RuntimeError |
| caffe-cifar-10 | search | [0.0](../logs/harbor/calibration-mini-native-260910/caffe-cifar-10__2Z2GuNp/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/caffe-cifar-10__RT6QhPK/result.json) | 0% | 5.0 | 0.005716 | 26.6 | none |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-mini-native-260910/cancel-async-tasks__WjKzhHP/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/cancel-async-tasks__aFVaFcp/result.json) | 0% | 2.0 | 0.003016 | 12.3 | none |
| cobol-modernization | search | [0.0](../logs/harbor/calibration-mini-native-260910/cobol-modernization__UmjCwZz/result.json), [1.0](../logs/harbor/calibration-mini-native-260910/cobol-modernization__usKLSb4/result.json) | 50% | 5.5 | 0.007732 | 30.3 | none |
| count-dataset-tokens | search | [0.0](../logs/harbor/calibration-mini-native-260910/count-dataset-tokens__aqC7BML/result.json), [None](../logs/harbor/calibration-mini-native-260910/count-dataset-tokens__iZFKznE/result.json) | 0% | 7.0 | 0.006788 | 67.9 | RuntimeError |
| dna-assembly | search | [0.0](../logs/harbor/calibration-mini-native-260910/dna-assembly__HBax7kd/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/dna-assembly__zRjwsn8/result.json) | 0% | 6.0 | 0.004009 | 24.2 | none |
| hf-model-inference | search | [0.0](../logs/harbor/calibration-mini-native-260910/hf-model-inference__VRkTvdn/result.json), [1.0](../logs/harbor/calibration-mini-native-260910/hf-model-inference__cXngunf/result.json) | 50% | 3.0 | 0.003879 | 23.8 | none |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-mini-native-260910/make-doom-for-mips__RRMuEJS/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/make-doom-for-mips__RULdoGs/result.json) | 0% | 14.5 | 0.024886 | 42.4 | none |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-mini-native-260910/mteb-leaderboard__d299zYM/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/mteb-leaderboard__siWLqoq/result.json) | 0% | 3.5 | 0.001267 | 8.5 | NonZeroAgentExitCodeError |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-mini-native-260910/mteb-retrieve__BTSuhNe/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/mteb-retrieve__K9vGfhx/result.json) | 0% | 3.5 | 0.003550 | 25.6 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-mini-native-260910/path-tracing__FRaUHSz/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/path-tracing__wcErUr5/result.json) | 0% | 5.0 | 0.008253 | 22.0 | none |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-mini-native-260910/pytorch-model-cli__MGik37H/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/pytorch-model-cli__t5Es7d5/result.json) | 0% | 3.0 | 0.007117 | 30.6 | NonZeroAgentExitCodeError |
| qemu-alpine-ssh | search | [0.0](../logs/harbor/calibration-mini-native-260910/qemu-alpine-ssh__D8hwrBN/result.json), [None](../logs/harbor/calibration-mini-native-260910/qemu-alpine-ssh__NRDi7Ad/result.json) | 0% | 6.0 | 0.006562 | 61.0 | RuntimeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-mini-native-260910/query-optimize__ZCMGT6r/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/query-optimize__ud8uHb6/result.json) (resumed) | 0% | 5.5 | 0.005203 | 20.3 | NonZeroAgentExitCodeError |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-mini-native-260910/sqlite-db-truncate__WW4P9t6/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/sqlite-db-truncate__nif9S74/result.json) | 0% | 6.5 | 0.004379 | 22.9 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-mini-native-260910/write-compressor__TVDzdhw/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/write-compressor__jkreVsX/result.json) | 0% | 2.5 | 0.004247 | 18.3 | none |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-mini-native-260910/chess-best-move__ARWd8k6/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/chess-best-move__Ph9jRgP/result.json) | 0% | 3.0 | 0.002148 | 7.3 | NonZeroAgentExitCodeError |
| compile-compcert | anchor | [0.0](../logs/harbor/calibration-mini-native-260910/compile-compcert__gXqFiW6/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/compile-compcert__i8yaFuF/result.json) | 0% | 5.0 | 0.003793 | 21.6 | none |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-mini-native-260910/configure-git-webserver__VMcxQ7x/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/configure-git-webserver__mhLpXvp/result.json) | 0% | 5.0 | 0.006796 | 34.9 | none |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-mini-native-260910/filter-js-from-html__5dYXT4J/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/filter-js-from-html__47DkVix/result.json) (resumed) | 0% | 2.0 | 0.004580 | 17.8 | none |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-mini-native-260910/polyglot-rust-c__ZxSUiDw/result.json), [0.0](../logs/harbor/calibration-mini-native-260910/polyglot-rust-c__q6uA3Rt/result.json) | 0% | 1.0 | 0.003105 | 14.9 | none |
| pypi-server | anchor | [None](../logs/harbor/calibration-mini-native-260910/pypi-server__ZVwV4iR/result.json), [1.0](../logs/harbor/calibration-mini-native-260910-resume/pypi-server__2Srv6SW/result.json) (resumed) | 50% | 1.5 | 0.002745 | 30.4 | RuntimeError |
| adaptive-rejection-sampler | sealed | [None](../logs/harbor/calibration-mini-native-260910/adaptive-rejection-sampler__HcWiaky/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-resume/adaptive-rejection-sampler__zdRK9jH/result.json) (resumed) | 0% | 3.0 | 0.010554 | 48.9 | RuntimeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-mini-native-260910/gcode-to-text__73Rsr3E/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/gcode-to-text__HCBf2Ye/result.json) (resumed) | 0% | 6.5 | 0.003590 | 15.1 | NonZeroAgentExitCodeError |
| headless-terminal | sealed | [1.0](../logs/harbor/calibration-mini-native-260910/headless-terminal__PNEQSmi/result.json), [1.0](../logs/harbor/calibration-mini-native-260910-recovery2/headless-terminal__5BTv3FM/result.json) (resumed) | 100% | 7.5 | 0.005709 | 27.5 | none |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-mini-native-260910/protein-assembly__xDXyuTL/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/protein-assembly__Z7DuH5a/result.json) (resumed) | 0% | 3.5 | 0.003512 | 14.0 | none |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-mini-native-260910/raman-fitting__D3zMTqq/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/raman-fitting__U8xK4cQ/result.json) (resumed) | 0% | 7.0 | 0.008965 | 27.6 | none |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-mini-native-260910/torch-pipeline-parallelism__uMdWZ64/result.json), [0.0](../logs/harbor/calibration-mini-native-260910-recovery2/torch-pipeline-parallelism__J6rL6KP/result.json) (resumed) | 0% | 1.5 | 0.008394 | 32.7 | NonZeroAgentExitCodeError |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5-mini"
  ],
  "x-ratelimit-limit-requests": {
    "min": 1000.0,
    "max": 1000.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 1000000.0,
    "max": 1000000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 997.0,
    "max": 999.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 962479.0,
    "max": 999705.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 2.0
  }
}
```

### luna-json

Served models: gpt-5.6-luna-2026-07-09. Calls: 398; unknown-cost calls: 0. Agent started: 60/60. No-action among started agents: 11.7%. Exceptions: `{"RuntimeError": 8, "NonZeroAgentExitCodeError": 2}`.
Calls with a served-model response: 398; HTTP 400 rejections: 0.

Failure messages and counts: `{"Command timed out after 30 seconds": 8, "Seed exhausted its 24-call limit": 2}`.

No-action traces: [chess-best-move__22sDkeg](../logs/harbor/calibration-luna-json-260910/chess-best-move__22sDkeg/agent/trace.jsonl), [configure-git-webserver__W6jVdTb](../logs/harbor/calibration-luna-json-260910/configure-git-webserver__W6jVdTb/agent/trace.jsonl), [count-dataset-tokens__8eWUAAw](../logs/harbor/calibration-luna-json-260910/count-dataset-tokens__8eWUAAw/agent/trace.jsonl), [headless-terminal__YupsCyg](../logs/harbor/calibration-luna-json-260910/headless-terminal__YupsCyg/agent/trace.jsonl), [mteb-leaderboard__ugH9FvM](../logs/harbor/calibration-luna-json-260910/mteb-leaderboard__ugH9FvM/agent/trace.jsonl), [query-optimize__75radBF](../logs/harbor/calibration-luna-json-260910/query-optimize__75radBF/agent/trace.jsonl), [sqlite-db-truncate__SAvNX4q](../logs/harbor/calibration-luna-json-260910/sqlite-db-truncate__SAvNX4q/agent/trace.jsonl).

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [1.0](../logs/harbor/calibration-luna-json-260910/bn-fit-modify__QKEhoG5/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/bn-fit-modify__fCqtt4S/result.json) | 50% | 10.5 | 0.009624 | 55.2 | none |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-luna-json-260910/break-filter-js-from-html__545wyXU/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/break-filter-js-from-html__RKg5pYa/result.json) | 0% | 5.5 | 0.003853 | 25.7 | none |
| build-pmars | search | [1.0](../logs/harbor/calibration-luna-json-260910/build-pmars__Q2PMeuj/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/build-pmars__daFULrS/result.json) | 100% | 10.0 | 0.013679 | 40.6 | none |
| caffe-cifar-10 | search | [None](../logs/harbor/calibration-luna-json-260910/caffe-cifar-10__Lm5XiaD/result.json), [None](../logs/harbor/calibration-luna-json-260910/caffe-cifar-10__TAcRKVn/result.json) | 0% | 2.5 | 0.000415 | 34.9 | RuntimeError, RuntimeError |
| cancel-async-tasks | search | [1.0](../logs/harbor/calibration-luna-json-260910/cancel-async-tasks__Cb2TfEY/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/cancel-async-tasks__nHPuYCU/result.json) | 50% | 2.0 | 0.001788 | 13.7 | none |
| cobol-modernization | search | [1.0](../logs/harbor/calibration-luna-json-260910/cobol-modernization__54miKSF/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/cobol-modernization__RF4rFdU/result.json) | 100% | 5.5 | 0.008090 | 29.9 | none |
| count-dataset-tokens | search | [0.0](../logs/harbor/calibration-luna-json-260910/count-dataset-tokens__8eWUAAw/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/count-dataset-tokens__jXsfpHp/result.json) | 50% | 5.0 | 0.006123 | 26.5 | none |
| dna-assembly | search | [0.0](../logs/harbor/calibration-luna-json-260910/dna-assembly__2EYcCrq/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/dna-assembly__9g2oVNK/result.json) | 0% | 6.5 | 0.010566 | 32.6 | none |
| hf-model-inference | search | [None](../logs/harbor/calibration-luna-json-260910/hf-model-inference__DehunbH/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/hf-model-inference__zUw5RKp/result.json) | 50% | 3.5 | 0.001979 | 33.1 | RuntimeError |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-luna-json-260910/make-doom-for-mips__Qa9xLKC/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/make-doom-for-mips__hMZyEZq/result.json) | 0% | 11.0 | 0.037500 | 41.6 | none |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-luna-json-260910/mteb-leaderboard__K4LJvPc/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/mteb-leaderboard__ugH9FvM/result.json) | 0% | 4.0 | 0.006631 | 14.8 | none |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-luna-json-260910/mteb-retrieve__vHAtTyV/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/mteb-retrieve__wQezZCG/result.json) | 0% | 4.5 | 0.002036 | 33.9 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-luna-json-260910/path-tracing__5eg4azt/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/path-tracing__Q6GzLZ7/result.json) | 0% | 9.5 | 0.012059 | 47.9 | none |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-luna-json-260910/pytorch-model-cli__bJjKKTW/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/pytorch-model-cli__zfAi2ez/result.json) | 50% | 7.5 | 0.007246 | 23.8 | none |
| qemu-alpine-ssh | search | [None](../logs/harbor/calibration-luna-json-260910/qemu-alpine-ssh__7DBzqhp/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/qemu-alpine-ssh__8JjCE8K/result.json) | 0% | 6.5 | 0.004270 | 79.5 | RuntimeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-luna-json-260910/query-optimize__75radBF/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/query-optimize__8Q5bQsj/result.json) | 50% | 2.5 | 0.002452 | 12.0 | none |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-luna-json-260910/sqlite-db-truncate__SAvNX4q/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/sqlite-db-truncate__scBHnrG/result.json) | 0% | 4.0 | 0.005178 | 19.2 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-luna-json-260910/write-compressor__NQdp3uY/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/write-compressor__ikW6NSi/result.json) | 0% | 17.0 | 0.038600 | 117.7 | NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-luna-json-260910/chess-best-move__22sDkeg/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/chess-best-move__jibPeCS/result.json) | 0% | 12.5 | 0.034391 | 174.7 | NonZeroAgentExitCodeError |
| compile-compcert | anchor | [None](../logs/harbor/calibration-luna-json-260910/compile-compcert__7CupkdT/result.json), [None](../logs/harbor/calibration-luna-json-260910/compile-compcert__BnatUXc/result.json) | 0% | 4.0 | 0.000717 | 36.6 | RuntimeError, RuntimeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-luna-json-260910/configure-git-webserver__W6jVdTb/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/configure-git-webserver__WsejXkG/result.json) | 50% | 5.0 | 0.003562 | 26.0 | none |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-luna-json-260910/filter-js-from-html__YFoc9Nq/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/filter-js-from-html__vK9Px8C/result.json) | 0% | 2.0 | 0.003142 | 19.7 | none |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-luna-json-260910/polyglot-rust-c__5WBu9Ao/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/polyglot-rust-c__Sq5Qpjf/result.json) | 0% | 12.5 | 0.041839 | 173.1 | none |
| pypi-server | anchor | [1.0](../logs/harbor/calibration-luna-json-260910/pypi-server__B4CAffB/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/pypi-server__FseY7zA/result.json) | 100% | 3.5 | 0.001906 | 14.1 | none |
| adaptive-rejection-sampler | sealed | [None](../logs/harbor/calibration-luna-json-260910/adaptive-rejection-sampler__LSHmUJb/result.json), [None](../logs/harbor/calibration-luna-json-260910/adaptive-rejection-sampler__RDRTnQo/result.json) | 0% | 3.5 | 0.006544 | 56.0 | RuntimeError, RuntimeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-luna-json-260910/gcode-to-text__AFoXH4i/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/gcode-to-text__dJVz7aF/result.json) | 0% | 7.0 | 0.010341 | 18.6 | none |
| headless-terminal | sealed | [0.0](../logs/harbor/calibration-luna-json-260910/headless-terminal__YupsCyg/result.json), [1.0](../logs/harbor/calibration-luna-json-260910/headless-terminal__uhihxwd/result.json) | 50% | 4.5 | 0.005801 | 19.1 | none |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-luna-json-260910/protein-assembly__eHuX9sv/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/protein-assembly__jS6tXB3/result.json) | 0% | 13.0 | 0.028140 | 118.9 | none |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-luna-json-260910/raman-fitting__RrcXUu4/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/raman-fitting__ZjtKdTr/result.json) | 0% | 10.5 | 0.009444 | 44.0 | none |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-luna-json-260910/torch-pipeline-parallelism__9rgxADd/result.json), [0.0](../logs/harbor/calibration-luna-json-260910/torch-pipeline-parallelism__TcYDkKC/result.json) | 0% | 3.5 | 0.007566 | 28.9 | none |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt56luna"
  ],
  "x-ratelimit-limit-requests": {
    "min": 500.0,
    "max": 500.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 500000.0,
    "max": 500000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 497.0,
    "max": 499.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 455709.0,
    "max": 499811.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 5.0
  }
}
```

### luna-native

Served models: none recorded. Calls: 60; unknown-cost calls: 60. Agent started: 60/60. No-action among started agents: 0.0%. Exceptions: `{"NonZeroAgentExitCodeError": 60}`.
Calls with a served-model response: 0; HTTP 400 rejections: 60.

Failure messages and counts: `{"Backend failed: HTTP 400": 60}`.

No-action traces: none.

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [0.0](../logs/harbor/calibration-luna-native-260910/bn-fit-modify__BaQpAhg/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/bn-fit-modify__R2ZXbc4/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-luna-native-260910/break-filter-js-from-html__5fR52Z6/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/break-filter-js-from-html__9uEyvBo/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| build-pmars | search | [0.0](../logs/harbor/calibration-luna-native-260910/build-pmars__7crqaz9/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/build-pmars__eUFkKMb/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| caffe-cifar-10 | search | [0.0](../logs/harbor/calibration-luna-native-260910/caffe-cifar-10__NYvpf9Z/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/caffe-cifar-10__VrGqXde/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-luna-native-260910/cancel-async-tasks__focgFpA/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/cancel-async-tasks__sf666yV/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| cobol-modernization | search | [0.0](../logs/harbor/calibration-luna-native-260910/cobol-modernization__TgYGRuy/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/cobol-modernization__yrBYhoV/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| count-dataset-tokens | search | [0.0](../logs/harbor/calibration-luna-native-260910/count-dataset-tokens__B75SP6k/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/count-dataset-tokens__gFoJndz/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| dna-assembly | search | [0.0](../logs/harbor/calibration-luna-native-260910/dna-assembly__AFWmdee/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/dna-assembly__zcCeVC4/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| hf-model-inference | search | [0.0](../logs/harbor/calibration-luna-native-260910/hf-model-inference__DPskGy9/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/hf-model-inference__Kxf6ZUB/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-luna-native-260910/make-doom-for-mips__3QnSRt2/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/make-doom-for-mips__uWnDDQu/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-luna-native-260910/mteb-leaderboard__FQc58QR/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/mteb-leaderboard__S5bRMV6/result.json) | 0% | 1.0 | 0.000000 | 1.0 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-luna-native-260910/mteb-retrieve__QL9SwzC/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/mteb-retrieve__occxywJ/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| path-tracing | search | [0.0](../logs/harbor/calibration-luna-native-260910/path-tracing__Agw8TEQ/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/path-tracing__PykfxRE/result.json) | 0% | 1.0 | 0.000000 | 0.7 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-luna-native-260910/pytorch-model-cli__ixZfEDB/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/pytorch-model-cli__qj4dRTN/result.json) | 0% | 1.0 | 0.000000 | 1.9 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| qemu-alpine-ssh | search | [0.0](../logs/harbor/calibration-luna-native-260910/qemu-alpine-ssh__BNwgPbn/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/qemu-alpine-ssh__fH2hGo3/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-luna-native-260910/query-optimize__NRE3m2A/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/query-optimize__iHKv4aZ/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-luna-native-260910/sqlite-db-truncate__G6PtRjm/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/sqlite-db-truncate__vYbuZEG/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| write-compressor | search | [0.0](../logs/harbor/calibration-luna-native-260910/write-compressor__3dMZmFG/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/write-compressor__DgAiuze/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/chess-best-move__5YY5g4K/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/chess-best-move__oyGwcAE/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| compile-compcert | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/compile-compcert__AMTBqRW/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/compile-compcert__qX9Hbq8/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/configure-git-webserver__3UGgUpk/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/configure-git-webserver__okjbnzu/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/filter-js-from-html__Dg7VwWc/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/filter-js-from-html__E5FbgDb/result.json) | 0% | 1.0 | 0.000000 | 0.6 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/polyglot-rust-c__2vhyhfA/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/polyglot-rust-c__nkAao4h/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pypi-server | anchor | [0.0](../logs/harbor/calibration-luna-native-260910/pypi-server__Ynxs7Xc/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/pypi-server__j9SKq88/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| adaptive-rejection-sampler | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/adaptive-rejection-sampler__7kb29DN/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/adaptive-rejection-sampler__AK7XSsZ/result.json) | 0% | 1.0 | 0.000000 | 0.5 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/gcode-to-text__Pg2EkiP/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/gcode-to-text__h74NiaY/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| headless-terminal | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/headless-terminal__FwnS2xB/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/headless-terminal__Vimddj9/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/protein-assembly__DRFMQFP/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/protein-assembly__SZGUGCv/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/raman-fitting__jcxkf2A/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/raman-fitting__kWKiDTu/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-luna-native-260910/torch-pipeline-parallelism__bsHdq8o/result.json), [0.0](../logs/harbor/calibration-luna-native-260910/torch-pipeline-parallelism__wckzGfr/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{}
```

### terra-native

Served models: none recorded. Calls: 60; unknown-cost calls: 60. Agent started: 60/60. No-action among started agents: 0.0%. Exceptions: `{"NonZeroAgentExitCodeError": 60}`.
Calls with a served-model response: 0; HTTP 400 rejections: 60.

Failure messages and counts: `{"Backend failed: HTTP 400": 60}`.

No-action traces: none.

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [0.0](../logs/harbor/calibration-terra-native-260910/bn-fit-modify__ENVDFUm/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/bn-fit-modify__socKgMc/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-terra-native-260910/break-filter-js-from-html__RBThaPa/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/break-filter-js-from-html__oN84oSz/result.json) | 0% | 1.0 | 0.000000 | 2.0 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| build-pmars | search | [0.0](../logs/harbor/calibration-terra-native-260910/build-pmars__ZrRcDME/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/build-pmars__xpurnRy/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| caffe-cifar-10 | search | [0.0](../logs/harbor/calibration-terra-native-260910/caffe-cifar-10__4UBGH6B/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/caffe-cifar-10__UUuW8Df/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-terra-native-260910/cancel-async-tasks__LN6pttK/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/cancel-async-tasks__xjGUq6N/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| cobol-modernization | search | [0.0](../logs/harbor/calibration-terra-native-260910/cobol-modernization__pr2MNpZ/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/cobol-modernization__xL4jwGG/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| count-dataset-tokens | search | [0.0](../logs/harbor/calibration-terra-native-260910/count-dataset-tokens__pCWDMei/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/count-dataset-tokens__v8yAHjw/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| dna-assembly | search | [0.0](../logs/harbor/calibration-terra-native-260910/dna-assembly__CwgckiE/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/dna-assembly__DxmaLyW/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| hf-model-inference | search | [0.0](../logs/harbor/calibration-terra-native-260910/hf-model-inference__oLNuZet/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/hf-model-inference__uJQqGZD/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-terra-native-260910/make-doom-for-mips__ESn5sWc/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/make-doom-for-mips__jSUR3T8/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-terra-native-260910/mteb-leaderboard__ZmReN97/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/mteb-leaderboard__jp5KRrG/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-terra-native-260910/mteb-retrieve__U2WNSg7/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/mteb-retrieve__aTMZ4xs/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| path-tracing | search | [0.0](../logs/harbor/calibration-terra-native-260910/path-tracing__Cp7Bh74/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/path-tracing__LMLQeEP/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-terra-native-260910/pytorch-model-cli__bQt5mKY/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/pytorch-model-cli__zXHkHqA/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| qemu-alpine-ssh | search | [0.0](../logs/harbor/calibration-terra-native-260910/qemu-alpine-ssh__8FK57um/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/qemu-alpine-ssh__c88zHJs/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-terra-native-260910/query-optimize__5T2ih6M/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/query-optimize__ELFgexD/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-terra-native-260910/sqlite-db-truncate__6xxMb7v/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/sqlite-db-truncate__fqBsbTf/result.json) | 0% | 1.0 | 0.000000 | 0.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| write-compressor | search | [0.0](../logs/harbor/calibration-terra-native-260910/write-compressor__7J9HDaP/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/write-compressor__NmSoEiN/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/chess-best-move__a9pknVw/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/chess-best-move__zdHa6my/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| compile-compcert | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/compile-compcert__6J8Eha7/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/compile-compcert__U3Hb8FN/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/configure-git-webserver__CKN5CuX/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/configure-git-webserver__bczf7j2/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/filter-js-from-html__oaoMVea/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/filter-js-from-html__ocWHKne/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/polyglot-rust-c__bqEhswH/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/polyglot-rust-c__y3keWih/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pypi-server | anchor | [0.0](../logs/harbor/calibration-terra-native-260910/pypi-server__uKX2eMU/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/pypi-server__x4oZ23u/result.json) | 0% | 1.0 | 0.000000 | 0.5 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| adaptive-rejection-sampler | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/adaptive-rejection-sampler__B6No6dX/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/adaptive-rejection-sampler__Cubbf6L/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/gcode-to-text__FJ6QGYW/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/gcode-to-text__JDefFQA/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| headless-terminal | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/headless-terminal__mL9BSJz/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/headless-terminal__vKei8Fc/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/protein-assembly__KwsQQME/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/protein-assembly__QTYE6pP/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/raman-fitting__DjVc2mD/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/raman-fitting__aZtqxdv/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-terra-native-260910/torch-pipeline-parallelism__3kncQvY/result.json), [0.0](../logs/harbor/calibration-terra-native-260910/torch-pipeline-parallelism__mEtBV8L/result.json) | 0% | 1.0 | 0.000000 | 0.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{}
```

### terra-json

Served models: gpt-5.6-terra-2026-07-09. Calls: 366; unknown-cost calls: 1. Agent started: 60/60. No-action among started agents: 1.7%. Exceptions: `{"RuntimeError": 11, "NonZeroAgentExitCodeError": 14}`.
Calls with a served-model response: 365; HTTP 400 rejections: 0.

Failure messages and counts: `{"Command timed out after 30 seconds": 11, "Backend failed: API returned no answer": 12, "Backend failed: Projected rollout budget exceeded": 1, "Seed exhausted its 24-call limit": 1}`.

No-action traces: [filter-js-from-html__jtTxh4k](../logs/harbor/calibration-terra-json-260910/filter-js-from-html__jtTxh4k/agent/trace.jsonl).

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [1.0](../logs/harbor/calibration-terra-json-260910/bn-fit-modify__aMQaopu/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/bn-fit-modify__gC2D2PZ/result.json) | 100% | 6.0 | 0.054668 | 28.3 | none |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-terra-json-260910/break-filter-js-from-html__53cyeKw/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/break-filter-js-from-html__VGHP4cL/result.json) | 0% | 4.0 | 0.015829 | 12.4 | none |
| build-pmars | search | [1.0](../logs/harbor/calibration-terra-json-260910/build-pmars__TzbuFR4/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/build-pmars__mXEqrHn/result.json) | 50% | 6.0 | 0.124980 | 80.8 | NonZeroAgentExitCodeError |
| caffe-cifar-10 | search | [None](../logs/harbor/calibration-terra-json-260910/caffe-cifar-10__CogmU2U/result.json), [None](../logs/harbor/calibration-terra-json-260910/caffe-cifar-10__vMNUP6X/result.json) | 0% | 3.0 | 0.009125 | 39.2 | RuntimeError, RuntimeError |
| cancel-async-tasks | search | [1.0](../logs/harbor/calibration-terra-json-260910/cancel-async-tasks__KX4EBUV/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/cancel-async-tasks__RrodRYV/result.json) | 100% | 3.5 | 0.028953 | 15.9 | none |
| cobol-modernization | search | [1.0](../logs/harbor/calibration-terra-json-260910/cobol-modernization__Q9wHa4A/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/cobol-modernization__XQjMJog/result.json) | 100% | 3.0 | 0.040828 | 22.9 | none |
| count-dataset-tokens | search | [1.0](../logs/harbor/calibration-terra-json-260910/count-dataset-tokens__DzUsjLf/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/count-dataset-tokens__z4joFbb/result.json) | 100% | 11.5 | 0.130710 | 71.3 | none |
| dna-assembly | search | [0.0](../logs/harbor/calibration-terra-json-260910/dna-assembly__RJnJTx9/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/dna-assembly__qXbw2z6/result.json) | 0% | 1.0 | 0.050328 | 106.1 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| hf-model-inference | search | [1.0](../logs/harbor/calibration-terra-json-260910/hf-model-inference__5oNmpGH/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/hf-model-inference__GAkNP8U/result.json) | 100% | 4.0 | 0.029198 | 31.1 | none |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-terra-json-260910/make-doom-for-mips__JU6pWub/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/make-doom-for-mips__LTdCkqd/result.json) | 0% | 8.5 | 0.399316 | 94.4 | NonZeroAgentExitCodeError |
| mteb-leaderboard | search | [None](../logs/harbor/calibration-terra-json-260910/mteb-leaderboard__fPjuWrZ/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/mteb-leaderboard__sWV6enr/result.json) | 0% | 14.5 | 0.270737 | 69.4 | RuntimeError |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-terra-json-260910/mteb-retrieve__BqQbAbC/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/mteb-retrieve__TScGcDo/result.json) | 0% | 4.0 | 0.015187 | 28.1 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-terra-json-260910/path-tracing__2dA6mzm/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/path-tracing__m4dmTcR/result.json) | 0% | 9.5 | 0.267775 | 58.2 | none |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-terra-json-260910/pytorch-model-cli__cZ8a2n5/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/pytorch-model-cli__j5KTm2u/result.json) | 50% | 5.0 | 0.075519 | 69.6 | NonZeroAgentExitCodeError |
| qemu-alpine-ssh | search | [None](../logs/harbor/calibration-terra-json-260910/qemu-alpine-ssh__8i7Vi8o/result.json), [None](../logs/harbor/calibration-terra-json-260910/qemu-alpine-ssh__s5fgjbL/result.json) | 0% | 9.0 | 0.063877 | 118.1 | RuntimeError, RuntimeError |
| query-optimize | search | [None](../logs/harbor/calibration-terra-json-260910/query-optimize__avHwTzr/result.json), [None](../logs/harbor/calibration-terra-json-260910/query-optimize__uVrRopR/result.json) | 0% | 5.0 | 0.028962 | 50.6 | RuntimeError, RuntimeError |
| sqlite-db-truncate | search | [1.0](../logs/harbor/calibration-terra-json-260910/sqlite-db-truncate__K5SV8Um/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/sqlite-db-truncate__KDBvVyR/result.json) | 100% | 5.5 | 0.036064 | 19.6 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-terra-json-260910/write-compressor__WE4xeML/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/write-compressor__yeTAMzB/result.json) | 0% | 16.0 | 0.537061 | 270.1 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-terra-json-260910/chess-best-move__2t4jAB4/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/chess-best-move__EZJ9ehB/result.json) | 0% | 3.0 | 0.057645 | 95.5 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| compile-compcert | anchor | [None](../logs/harbor/calibration-terra-json-260910/compile-compcert__roty7WY/result.json), [None](../logs/harbor/calibration-terra-json-260910/compile-compcert__xpp2kk3/result.json) | 0% | 4.5 | 0.013103 | 44.8 | RuntimeError, RuntimeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-terra-json-260910/configure-git-webserver__LJUHybP/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/configure-git-webserver__WefhbE2/result.json) | 0% | 2.0 | 0.033815 | 56.7 | NonZeroAgentExitCodeError |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-terra-json-260910/filter-js-from-html__ZubWGtn/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/filter-js-from-html__jtTxh4k/result.json) | 0% | 1.0 | 0.043020 | 55.7 | NonZeroAgentExitCodeError |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-terra-json-260910/polyglot-rust-c__LLQoZCK/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/polyglot-rust-c__cW3HnkZ/result.json) | 0% | 6.5 | 0.180417 | 107.5 | none |
| pypi-server | anchor | [1.0](../logs/harbor/calibration-terra-json-260910/pypi-server__YLmL7eq/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/pypi-server__hrnWmVr/result.json) | 100% | 3.5 | 0.022858 | 14.7 | none |
| adaptive-rejection-sampler | sealed | [None](../logs/harbor/calibration-terra-json-260910/adaptive-rejection-sampler__LKb2SyD/result.json), [None](../logs/harbor/calibration-terra-json-260910/adaptive-rejection-sampler__VgeXScC/result.json) | 0% | 1.5 | 0.015591 | 43.2 | RuntimeError, RuntimeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-terra-json-260910/gcode-to-text__e636jJW/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/gcode-to-text__sxUgLXF/result.json) | 0% | 16.5 | 0.630510 | 132.5 | NonZeroAgentExitCodeError |
| headless-terminal | sealed | [0.0](../logs/harbor/calibration-terra-json-260910/headless-terminal__9ETCA8k/result.json), [1.0](../logs/harbor/calibration-terra-json-260910/headless-terminal__FTkXbSL/result.json) | 50% | 3.0 | 0.050276 | 59.0 | NonZeroAgentExitCodeError |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-terra-json-260910/protein-assembly__GEMm82m/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/protein-assembly__ejKtsCu/result.json) | 0% | 11.0 | 0.315747 | 142.1 | NonZeroAgentExitCodeError |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-terra-json-260910/raman-fitting__98PzuSL/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/raman-fitting__c24M3FK/result.json) | 0% | 7.5 | 0.094752 | 71.0 | none |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-terra-json-260910/torch-pipeline-parallelism__EeTaxPG/result.json), [0.0](../logs/harbor/calibration-terra-json-260910/torch-pipeline-parallelism__m3XnbPe/result.json) | 0% | 4.0 | 0.091107 | 37.9 | none |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt56terra"
  ],
  "x-ratelimit-limit-requests": {
    "min": 1000.0,
    "max": 1000.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 1000000.0,
    "max": 1000000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 997.0,
    "max": 999.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 966022.0,
    "max": 999811.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 2.0
  }
}
```


### mini-json-medium

Served models: gpt-5-mini-2025-08-07. Calls: 278; unknown-cost calls: 0. Agent started: 60/60. No-action among started agents: 0.0%. Exceptions: `{"NonZeroAgentExitCodeError": 18, "RuntimeError": 5}`.
Calls with a served-model response: 278; HTTP 400 rejections: 0.

Failure messages and counts: `{"Backend failed: API returned no answer": 18, "Command timed out after 30 seconds": 5}`.

No-action traces: none.

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/bn-fit-modify__AxveZUP/result.json), [1.0](../logs/harbor/calibration-mini-json-medium-260910/bn-fit-modify__wKbzax7/result.json) | 50% | 4.0 | 0.015859 | 66.4 | NonZeroAgentExitCodeError |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/break-filter-js-from-html__4TXSBqd/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/break-filter-js-from-html__a6PPeM7/result.json) | 0% | 5.0 | 0.007137 | 35.2 | none |
| build-pmars | search | [None](../logs/harbor/calibration-mini-json-medium-260910/build-pmars__ES2Cgmf/result.json), [None](../logs/harbor/calibration-mini-json-medium-260910/build-pmars__RTWEJ6o/result.json) | 0% | 1.0 | 0.000728 | 35.1 | RuntimeError, RuntimeError |
| caffe-cifar-10 | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/caffe-cifar-10__2QpN4Ri/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/caffe-cifar-10__aSQvRG2/result.json) | 0% | 7.0 | 0.024279 | 98.7 | NonZeroAgentExitCodeError |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/cancel-async-tasks__WgnAbdK/result.json), [1.0](../logs/harbor/calibration-mini-json-medium-260910/cancel-async-tasks__Yh7gNCX/result.json) | 50% | 2.5 | 0.006329 | 28.2 | none |
| cobol-modernization | search | [1.0](../logs/harbor/calibration-mini-json-medium-260910/cobol-modernization__e8A429z/result.json), [1.0](../logs/harbor/calibration-mini-json-medium-260910/cobol-modernization__xKydLXc/result.json) | 100% | 8.0 | 0.013546 | 54.8 | none |
| count-dataset-tokens | search | [None](../logs/harbor/calibration-mini-json-medium-260910/count-dataset-tokens__ZCGCrNv/result.json), [None](../logs/harbor/calibration-mini-json-medium-260910/count-dataset-tokens__uT66cDz/result.json) | 0% | 4.0 | 0.009084 | 68.0 | RuntimeError, RuntimeError |
| dna-assembly | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/dna-assembly__Cg3uQRM/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/dna-assembly__nFKkmab/result.json) | 0% | 4.5 | 0.008894 | 39.0 | NonZeroAgentExitCodeError |
| hf-model-inference | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/hf-model-inference__Latia9o/result.json), [1.0](../logs/harbor/calibration-mini-json-medium-260910/hf-model-inference__d4AfvmD/result.json) | 50% | 3.5 | 0.011238 | 63.1 | none |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/make-doom-for-mips__j3kKRqr/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/make-doom-for-mips__s8Eups6/result.json) | 0% | 11.0 | 0.026319 | 76.4 | NonZeroAgentExitCodeError |
| mteb-leaderboard | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/mteb-leaderboard__9SgTbJh/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/mteb-leaderboard__pFbNvQM/result.json) | 0% | 4.0 | 0.003575 | 21.1 | none |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/mteb-retrieve__9wsvxGe/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/mteb-retrieve__zT4bjqh/result.json) | 0% | 3.0 | 0.005413 | 34.4 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/path-tracing__uU9AfQi/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/path-tracing__z8L89gJ/result.json) | 0% | 4.0 | 0.009222 | 34.0 | none |
| pytorch-model-cli | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/pytorch-model-cli__osUEYyV/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/pytorch-model-cli__pWNMgxu/result.json) | 0% | 4.0 | 0.010493 | 46.5 | none |
| qemu-alpine-ssh | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/qemu-alpine-ssh__H5CUM4q/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/qemu-alpine-ssh__bSMWmLk/result.json) | 0% | 5.0 | 0.019065 | 78.4 | NonZeroAgentExitCodeError |
| query-optimize | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/query-optimize__DyZyTZb/result.json), [1.0](../logs/harbor/calibration-mini-json-medium-260910/query-optimize__QyQq9dL/result.json) | 50% | 4.5 | 0.006423 | 30.1 | none |
| sqlite-db-truncate | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/sqlite-db-truncate__XjeEPWr/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/sqlite-db-truncate__eor8JqY/result.json) | 0% | 6.0 | 0.016117 | 60.0 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-mini-json-medium-260910/write-compressor__rSkEfja/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/write-compressor__yvFWhpa/result.json) | 0% | 4.0 | 0.009828 | 44.3 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-mini-json-medium-260910/chess-best-move__hfsyAPe/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/chess-best-move__zuvTCoy/result.json) | 0% | 11.5 | 0.034123 | 117.2 | none |
| compile-compcert | anchor | [None](../logs/harbor/calibration-mini-json-medium-260910/compile-compcert__HjuRkgX/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/compile-compcert__KtLWNqa/result.json) | 0% | 5.5 | 0.014087 | 76.1 | RuntimeError |
| configure-git-webserver | anchor | [0.0](../logs/harbor/calibration-mini-json-medium-260910/configure-git-webserver__3bp88Bh/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/configure-git-webserver__CH4obta/result.json) | 0% | 4.5 | 0.019755 | 96.9 | NonZeroAgentExitCodeError |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-mini-json-medium-260910/filter-js-from-html__PobKuZs/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/filter-js-from-html__ySYgTuX/result.json) | 0% | 2.0 | 0.011856 | 54.6 | NonZeroAgentExitCodeError |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-mini-json-medium-260910/polyglot-rust-c__8BC3rWq/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/polyglot-rust-c__rEtBBX9/result.json) | 0% | 1.0 | 0.008262 | 34.0 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pypi-server | anchor | [1.0](../logs/harbor/calibration-mini-json-medium-260910/pypi-server__dgAyJoa/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/pypi-server__jfjSDQa/result.json) | 50% | 3.0 | 0.014369 | 72.5 | NonZeroAgentExitCodeError |
| adaptive-rejection-sampler | sealed | [0.0](../logs/harbor/calibration-mini-json-medium-260910/adaptive-rejection-sampler__MUvrpB4/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/adaptive-rejection-sampler__dKr6RrR/result.json) | 0% | 1.0 | 0.008372 | 33.9 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-mini-json-medium-260910/gcode-to-text__BGcSn8j/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/gcode-to-text__Qq7iFJ6/result.json) | 0% | 7.0 | 0.013448 | 37.9 | none |
| headless-terminal | sealed | [1.0](../logs/harbor/calibration-mini-json-medium-260910/headless-terminal__XuayyfU/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/headless-terminal__qyxJBtZ/result.json) | 50% | 4.5 | 0.008658 | 38.5 | none |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-mini-json-medium-260910/protein-assembly__Z63VSrA/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/protein-assembly__nuvdhXa/result.json) | 0% | 4.5 | 0.012074 | 55.4 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-mini-json-medium-260910/raman-fitting__BQo7riC/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/raman-fitting__HJiCRdE/result.json) | 0% | 7.5 | 0.014972 | 58.0 | none |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-mini-json-medium-260910/torch-pipeline-parallelism__2ooaefd/result.json), [0.0](../logs/harbor/calibration-mini-json-medium-260910/torch-pipeline-parallelism__yUhynwm/result.json) | 0% | 2.0 | 0.009494 | 42.8 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt-5-mini"
  ],
  "x-ratelimit-limit-requests": {
    "min": 1000.0,
    "max": 1000.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 1000000.0,
    "max": 1000000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 999.0,
    "max": 999.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 954309.0,
    "max": 999811.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 2.0
  }
}
```

### luna-json-medium

Served models: gpt-5.6-luna-2026-07-09. Calls: 453; unknown-cost calls: 0. Agent started: 60/60. No-action among started agents: 11.7%. Exceptions: `{"RuntimeError": 10, "NonZeroAgentExitCodeError": 6}`.
Calls with a served-model response: 453; HTTP 400 rejections: 0.

Failure messages and counts: `{"Command timed out after 30 seconds": 10, "Backend failed: API returned no answer": 4, "Seed exhausted its 24-call limit": 2}`.

No-action traces: [chess-best-move__qCP95LZ](../logs/harbor/calibration-luna-json-medium-260910/chess-best-move__qCP95LZ/agent/trace.jsonl), [cobol-modernization__VYo8FF2](../logs/harbor/calibration-luna-json-medium-260910/cobol-modernization__VYo8FF2/agent/trace.jsonl), [count-dataset-tokens__pQ4iQpo](../logs/harbor/calibration-luna-json-medium-260910/count-dataset-tokens__pQ4iQpo/agent/trace.jsonl), [dna-assembly__LZNL8iV](../logs/harbor/calibration-luna-json-medium-260910/dna-assembly__LZNL8iV/agent/trace.jsonl), [make-doom-for-mips__RytdnR7](../logs/harbor/calibration-luna-json-medium-260910/make-doom-for-mips__RytdnR7/agent/trace.jsonl), [query-optimize__WzVfU6Y](../logs/harbor/calibration-luna-json-medium-260910/query-optimize__WzVfU6Y/agent/trace.jsonl), [write-compressor__UgXaWcP](../logs/harbor/calibration-luna-json-medium-260910/write-compressor__UgXaWcP/agent/trace.jsonl).

| Task | Split | Attempt raw reward (result links) | avg@2 | Steps (mean) | USD (mean) | Agent s (mean) | Exceptions |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| bn-fit-modify | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/bn-fit-modify__APqheqc/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/bn-fit-modify__dL6HEJp/result.json) | 100% | 9.0 | 0.011651 | 58.5 | none |
| break-filter-js-from-html | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/break-filter-js-from-html__HbHbs8T/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/break-filter-js-from-html__YqAdjVr/result.json) | 0% | 4.0 | 0.003367 | 31.7 | none |
| build-pmars | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/build-pmars__UJsbhZS/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/build-pmars__tsaES6f/result.json) | 100% | 14.5 | 0.041307 | 77.8 | none |
| caffe-cifar-10 | search | [None](../logs/harbor/calibration-luna-json-medium-260910/caffe-cifar-10__VfbuHSD/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/caffe-cifar-10__f7yXsBV/result.json) | 0% | 2.0 | 0.000340 | 35.4 | RuntimeError, RuntimeError |
| cancel-async-tasks | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/cancel-async-tasks__5Haye9g/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/cancel-async-tasks__kGyyi2B/result.json) | 0% | 2.5 | 0.002811 | 29.8 | none |
| cobol-modernization | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/cobol-modernization__7iu9TBx/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/cobol-modernization__VYo8FF2/result.json) | 50% | 3.0 | 0.003562 | 23.2 | none |
| count-dataset-tokens | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/count-dataset-tokens__AFEn7n6/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/count-dataset-tokens__pQ4iQpo/result.json) | 50% | 7.0 | 0.010794 | 44.7 | none |
| dna-assembly | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/dna-assembly__LZNL8iV/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/dna-assembly__owKKtkh/result.json) | 0% | 7.0 | 0.016395 | 71.3 | NonZeroAgentExitCodeError |
| hf-model-inference | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/hf-model-inference__WmoGdaY/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/hf-model-inference__XVtcMUe/result.json) | 100% | 7.0 | 0.006349 | 42.4 | none |
| make-doom-for-mips | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/make-doom-for-mips__RytdnR7/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/make-doom-for-mips__WceWNKL/result.json) | 0% | 4.5 | 0.008391 | 30.3 | RuntimeError |
| mteb-leaderboard | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/mteb-leaderboard__ENtPpnN/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/mteb-leaderboard__qrcny77/result.json) | 100% | 12.5 | 0.024435 | 57.7 | none |
| mteb-retrieve | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/mteb-retrieve__WxA7dLC/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/mteb-retrieve__tqeKQUA/result.json) | 0% | 5.5 | 0.002351 | 32.8 | none |
| path-tracing | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/path-tracing__3KnATyD/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/path-tracing__ugpauz7/result.json) | 0% | 9.5 | 0.014301 | 65.8 | none |
| pytorch-model-cli | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/pytorch-model-cli__2uPeH4A/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/pytorch-model-cli__nTpFczk/result.json) | 50% | 9.5 | 0.013678 | 77.0 | none |
| qemu-alpine-ssh | search | [None](../logs/harbor/calibration-luna-json-medium-260910/qemu-alpine-ssh__LydQSYZ/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/qemu-alpine-ssh__ZaURSBo/result.json) | 0% | 22.0 | 0.033927 | 294.2 | RuntimeError, RuntimeError |
| query-optimize | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/query-optimize__K43fGwD/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/query-optimize__WzVfU6Y/result.json) | 50% | 2.5 | 0.002379 | 14.4 | none |
| sqlite-db-truncate | search | [1.0](../logs/harbor/calibration-luna-json-medium-260910/sqlite-db-truncate__hRA9SHT/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/sqlite-db-truncate__xg9rUtk/result.json) | 100% | 8.5 | 0.016368 | 66.5 | none |
| write-compressor | search | [0.0](../logs/harbor/calibration-luna-json-medium-260910/write-compressor__UMDoizD/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/write-compressor__UgXaWcP/result.json) | 0% | 12.5 | 0.041645 | 190.6 | NonZeroAgentExitCodeError |
| chess-best-move | anchor | [0.0](../logs/harbor/calibration-luna-json-medium-260910/chess-best-move__inNHNUK/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/chess-best-move__qCP95LZ/result.json) | 0% | 2.0 | 0.002737 | 34.2 | NonZeroAgentExitCodeError |
| compile-compcert | anchor | [None](../logs/harbor/calibration-luna-json-medium-260910/compile-compcert__5N6CVAK/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/compile-compcert__tJgoydj/result.json) | 0% | 4.0 | 0.001174 | 42.4 | RuntimeError, RuntimeError |
| configure-git-webserver | anchor | [1.0](../logs/harbor/calibration-luna-json-medium-260910/configure-git-webserver__63ZmFbi/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/configure-git-webserver__7d55HoZ/result.json) | 100% | 6.5 | 0.005554 | 46.3 | none |
| filter-js-from-html | anchor | [0.0](../logs/harbor/calibration-luna-json-medium-260910/filter-js-from-html__szmf6vA/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/filter-js-from-html__vHZ8HaK/result.json) | 0% | 2.5 | 0.006603 | 44.4 | none |
| polyglot-rust-c | anchor | [0.0](../logs/harbor/calibration-luna-json-medium-260910/polyglot-rust-c__APr4cm5/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/polyglot-rust-c__f3vwWxw/result.json) | 0% | 9.5 | 0.038747 | 304.5 | NonZeroAgentExitCodeError, NonZeroAgentExitCodeError |
| pypi-server | anchor | [1.0](../logs/harbor/calibration-luna-json-medium-260910/pypi-server__CcpnBBQ/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/pypi-server__WdCt6SE/result.json) | 100% | 4.5 | 0.003020 | 25.8 | none |
| adaptive-rejection-sampler | sealed | [None](../logs/harbor/calibration-luna-json-medium-260910/adaptive-rejection-sampler__Lhc38iz/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/adaptive-rejection-sampler__gdMcSX9/result.json) | 0% | 3.5 | 0.005958 | 60.8 | RuntimeError, RuntimeError |
| gcode-to-text | sealed | [0.0](../logs/harbor/calibration-luna-json-medium-260910/gcode-to-text__7N696Vc/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/gcode-to-text__gcooiMd/result.json) | 0% | 12.5 | 0.041453 | 87.9 | none |
| headless-terminal | sealed | [1.0](../logs/harbor/calibration-luna-json-medium-260910/headless-terminal__LgFWgXf/result.json), [1.0](../logs/harbor/calibration-luna-json-medium-260910/headless-terminal__MCnmdFv/result.json) | 100% | 6.5 | 0.012706 | 60.9 | none |
| protein-assembly | sealed | [0.0](../logs/harbor/calibration-luna-json-medium-260910/protein-assembly__8wXcGcE/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/protein-assembly__b8hFCv5/result.json) | 0% | 14.5 | 0.068585 | 184.7 | NonZeroAgentExitCodeError |
| raman-fitting | sealed | [0.0](../logs/harbor/calibration-luna-json-medium-260910/raman-fitting__jReoUok/result.json), [None](../logs/harbor/calibration-luna-json-medium-260910/raman-fitting__mcgTpin/result.json) | 0% | 13.5 | 0.025804 | 130.4 | RuntimeError |
| torch-pipeline-parallelism | sealed | [0.0](../logs/harbor/calibration-luna-json-medium-260910/torch-pipeline-parallelism__KuRdabt/result.json), [0.0](../logs/harbor/calibration-luna-json-medium-260910/torch-pipeline-parallelism__i8Z7DwT/result.json) | 0% | 4.0 | 0.011473 | 55.7 | none |

Environment build/setup failures:

None recorded.

Observed rate-limit headers (all distinct strings or numeric min/max; request IDs remain in the ledger):

```json
{
  "x-ratelimit-abusepenalty-active": [
    "False"
  ],
  "x-ratelimit-key": [
    "gpt56luna"
  ],
  "x-ratelimit-limit-requests": {
    "min": 500.0,
    "max": 500.0
  },
  "x-ratelimit-limit-tokens": {
    "min": 500000.0,
    "max": 500000.0
  },
  "x-ratelimit-remaining-requests": {
    "min": 497.0,
    "max": 499.0
  },
  "x-ratelimit-remaining-tokens": {
    "min": 461927.0,
    "max": 499811.0
  },
  "x-ratelimit-renewalperiod-requests": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-renewalperiod-tokens": {
    "min": 60.0,
    "max": 60.0
  },
  "x-ratelimit-reset-requests": {
    "min": 0.0,
    "max": 0.0
  },
  "x-ratelimit-reset-tokens": {
    "min": 0.0,
    "max": 4.0
  }
}
```

