# Terminal-Bench 2 task-model and protocol calibration

Date: 2026-09-10. **P1.2 allowance experiment (W5f), reported under the W5e/R7 corrected contracts.** Three new 8,192-token jobs extend the eight historical 4,096-token configurations. All rewards use saved, unmodified Harbor 0.22 verifier evidence. This is AD12 evidence; AD10–AD12 remain pending decisions.

## Setup and reproducibility

Dataset `terminal-bench@2.0`, commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`; 89 tasks: 4 easy, 55 medium, 30 hard. The archived [population metadata](../data/tb2_population.json) includes task IDs, difficulties, and task.toml SHA-256 hashes. Cache paths were checked against the pinned GitTaskId. The offline check reproduces the entire committed split, including selected hashes: `uv run python -m scripts.calibration_split_check`. It reads local manifests only; it does not resolve a registry, download tasks, or rewrite the split.

Sampling seed 260910; sorted tasks, one Python RNG with per-stratum shuffles, Hamilton largest remainders and lexical tie-breaking. Search is allocated first, then anchor, then sealed. Smoke tasks were eligible without preference; only `cobol-modernization` overlaps. One easy task cannot populate all three splits.

| Split | Easy | Medium | Hard | Total |
| --- | --- | --- | --- | --- |
| search | 1 | 11 | 6 | 18 |
| anchor | 0 | 4 | 2 | 6 |
| sealed | 0 | 4 | 2 | 6 |

Every configuration has 30 tasks × 2 finalized attempts. Shared settings: 4,096 completion tokens historically and 8,192 for rows ending `-8k` (reasoning plus output), 24 model calls, 30 seconds per command, 180 seconds per API call, $1 per-rollout guard, and task-defined Harbor timeouts. Temperature and generation seed were omitted. The original six rows use low reasoning; W5d adds Mini/JSON and Luna/JSON at medium. No Terra/medium row exists. W5f repeats Terra/low, Mini/medium, and Luna/medium with the shared 8,192-token allowance, concurrency 3, a separate $15 cohort guard, and the unchanged $1 rollout guard. Native means Chat Completions function tools, with `parallel_tool_calls=false`, terminal/read_file/write_file, and a plain final answer. JSON uses the same common serialized history and prompt across deployments. There is no model-specific prompt, planning, self-verification, or rescue.

Regenerate all corrected results, all cohorts, and costs with `uv run python -m scripts.calibration_report`; validate with `uv run python -m scripts.audit_calibration`. These are offline reducers. The earlier medium-only extension script is superseded; use this unified entry point.

## Corrected measurement contracts

**No action:** every finalized completed or failed solver attempt without an executed container/file action, divided by 60 (36/12/12 by split). No finalized grader/infrastructure exclusions were recorded. Interrupted work belongs in costs and replacement lineage, not as extra scored attempts. An emitted call or suggested shell command is not execution. A command observation proves execution regardless of its exit code. Historical timeout tracebacks reaching subprocess output collection also prove execution; their missing observation is not counted as no action.

**L1 (proposed AD10):** valid verifier reward exactly 1, no agent timeout and no executor-level tool failure. Executor-level failures include command timeout, execution exception, and **any action protocol/parse error, even if recovered**. **L2 (strict A9):** L1 plus no nonzero exit from any agent-issued command, including legitimate false predicates. Exhausted solvers fail under PREREG Section 6. All remaining trial-exception rows here have raw reward 0 or missing, and therefore fail both labels. A missing finish record or no action alone does not override valid verifier credit. Raw verifier reward is preserved; neither completion claims nor recovery claims replace it. AD10 remains pending, so neither reading is silently ratified.

**Termination taxonomy:** one terminal category per attempt, plus independent failure flags. A later normal finish stays a normal terminal event even if an earlier parse error disqualifies its pass. Inability/no-tools claims require no action and an explicit inability statement in the final answer. Exhaustion uses the final failed response `finish_reason: length`, the 24-call-limit exception, or the rollout-budget stop (separately counted below). Execution failures use traces and exception stacks. Remaining unhandled failures, including HTTP rejection, are trial exceptions. Protocol-only terminal failures have their own category; recovered errors are reported independently. Precedence is final finish, exhaustion, executor failure, unrecovered parse failure, remaining trial exception. Agent timeout is retained as a flag.

Derived per-attempt labels, terminal causes, action evidence with source lines, nonzero-command exits, call UUIDs, raw rewards, and result hashes are in [calibration_results.json](../costs/calibration_results.json). The derived [attempt events](../logs/calibration-8k-followup-260910/attempts.jsonl) preserve execution evidence without inventing timestamps or changing historical traces. Adding execution start/outcome events to the live seed is outside this task’s allowed harness changes; future executor exceptions before output collection can therefore remain ambiguous.

## Corrected results

avg@2 averages the two binary labels per task, then tasks equally. All rows contain 60 finalized attempts. Raw reward 1 is shown against all 60 slots; missing rewards are listed separately and never imputed as successes. Native rejection rows have zero operational pass; their capability and behavioral no-action gate are unmeasured.

| Configuration | Verifier / missing | Raw reward 1 | L1 passes / avg@2 | L2 passes / avg@2 | No action |
| --- | --- | --- | --- | --- | --- |
| mini-json | 53 / 7 | 5/60 (8.3%) | 5/60 (8.3%) | 3/60 (5.0%) | 1/60 (1.7%) |
| mini-native | 54 / 6 | 5/60 (8.3%) | 5/60 (8.3%) | 4/60 (6.7%) | 3/60 (5.0%) |
| luna-json | 52 / 8 | 14/60 (23.3%) | 9/60 (15.0%) | 4/60 (6.7%) | 7/60 (11.7%) |
| luna-native | 60 / 0 | 0/60 (0.0%) | 0/60 (0.0%) | 0/60 (0.0%) | 60/60 (100.0%) |
| terra-native | 60 / 0 | 0/60 (0.0%) | 0/60 (0.0%) | 0/60 (0.0%) | 60/60 (100.0%) |
| terra-json | 49 / 11 | 17/60 (28.3%) | 17/60 (28.3%) | 7/60 (11.7%) | 9/60 (15.0%) |
| mini-json-medium | 55 / 5 | 8/60 (13.3%) | 8/60 (13.3%) | 6/60 (10.0%) | 5/60 (8.3%) |
| luna-json-medium | 50 / 10 | 20/60 (33.3%) | 9/60 (15.0%) | 4/60 (6.7%) | 7/60 (11.7%) |
| terra-json-8k | 46 / 14 | 18/60 (30.0%) | 18/60 (30.0%) | 8/60 (13.3%) | 4/60 (6.7%) |
| mini-json-medium-8k | 53 / 7 | 9/60 (15.0%) | 7/60 (11.7%) | 5/60 (8.3%) | 2/60 (3.3%) |
| luna-json-medium-8k | 51 / 9 | 15/60 (25.0%) | 5/60 (8.3%) | 3/60 (5.0%) | 9/60 (15.0%) |

Task-bootstrap percentile intervals use 10,000 draws, analysis seed 260910 (the original calibration convention). Each draw carries both attempts together. These describe task sampling, not independent rerun variability.

### L1: overall and split rates

| Configuration | Overall | 95% task CI | Search (36) | Anchor (12) | Sealed (12) |
| --- | --- | --- | --- | --- | --- |
| mini-json | 8.3% | 1.7%–16.7% | 8.3% | 16.7% | 0.0% |
| mini-native | 8.3% | 1.7%–16.7% | 5.6% | 8.3% | 16.7% |
| luna-json | 15.0% | 6.7%–25.0% | 16.7% | 25.0% | 0.0% |
| luna-native | 0.0% | not measurable | 0.0% | 0.0% | 0.0% |
| terra-native | 0.0% | not measurable | 0.0% | 0.0% | 0.0% |
| terra-json | 28.3% | 13.3%–43.3% | 38.9% | 16.7% | 8.3% |
| mini-json-medium | 13.3% | 5.0%–23.3% | 16.7% | 8.3% | 8.3% |
| luna-json-medium | 15.0% | 5.0%–26.7% | 13.9% | 25.0% | 8.3% |
| terra-json-8k | 30.0% | 15.0%–45.0% | 33.3% | 33.3% | 16.7% |
| mini-json-medium-8k | 11.7% | 1.7%–23.3% | 13.9% | 0.0% | 16.7% |
| luna-json-medium-8k | 8.3% | 1.7%–16.7% | 8.3% | 0.0% | 16.7% |

### L2: overall and split rates

| Configuration | Overall | 95% task CI | Search (36) | Anchor (12) | Sealed (12) |
| --- | --- | --- | --- | --- | --- |
| mini-json | 5.0% | 0.0%–10.0% | 5.6% | 8.3% | 0.0% |
| mini-native | 6.7% | 1.7%–13.3% | 5.6% | 8.3% | 8.3% |
| luna-json | 6.7% | 1.7%–13.3% | 8.3% | 8.3% | 0.0% |
| luna-native | 0.0% | not measurable | 0.0% | 0.0% | 0.0% |
| terra-native | 0.0% | not measurable | 0.0% | 0.0% | 0.0% |
| terra-json | 11.7% | 1.7%–23.3% | 11.1% | 16.7% | 8.3% |
| mini-json-medium | 10.0% | 3.3%–20.0% | 13.9% | 8.3% | 0.0% |
| luna-json-medium | 6.7% | 1.7%–13.3% | 5.6% | 8.3% | 8.3% |
| terra-json-8k | 13.3% | 3.3%–26.7% | 11.1% | 16.7% | 16.7% |
| mini-json-medium-8k | 8.3% | 0.0%–18.3% | 11.1% | 0.0% | 8.3% |
| luna-json-medium-8k | 5.0% | 0.0%–13.3% | 2.8% | 0.0% | 16.7% |

### Termination reason breakdown

Each cell is **all finalized attempts (no-action subset)**. Rows sum to 60; the parenthesized counts sum to that row’s no-action numerator.

| Configuration | Normal finish | No-tools / inability | Token / step / budget exhaustion | Protocol / parse terminal | Executor failure | Trial exception |
| --- | --- | --- | --- | --- | --- | --- |
| mini-json | 51 (0) | 0 (0) | 2 (1) | 0 (0) | 7 (0) | 0 (0) |
| mini-native | 46 (0) | 2 (2) | 2 (1) | 0 (0) | 6 (0) | 4 (0) |
| luna-json | 44 (1) | 6 (6) | 2 (0) | 0 (0) | 8 (0) | 0 (0) |
| luna-native | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 60 (60) |
| terra-native | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 0 (0) | 60 (60) |
| terra-json | 35 (1) | 0 (0) | 14 (8) | 0 (0) | 11 (0) | 0 (0) |
| mini-json-medium | 37 (0) | 0 (0) | 18 (5) | 0 (0) | 5 (0) | 0 (0) |
| luna-json-medium | 37 (0) | 7 (7) | 6 (0) | 0 (0) | 10 (0) | 0 (0) |
| terra-json-8k | 37 (0) | 0 (0) | 2 (0) | 0 (0) | 14 (0) | 7 (4) |
| mini-json-medium-8k | 47 (0) | 0 (0) | 6 (2) | 0 (0) | 7 (0) | 0 (0) |
| luna-json-medium-8k | 40 (2) | 7 (7) | 4 (0) | 0 (0) | 9 (0) | 0 (0) |

| Configuration | Token limit (no action) | 24-call cap | USD cap | Any protocol error attempts | Any nonzero command attempts | Command timeouts | Agent timeouts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mini-json | 1 (1) | 1 | 0 | 7 | 26 | 7 | 0 |
| mini-native | 2 (1) | 0 | 0 | 1 | 25 | 6 | 0 |
| luna-json | 0 (0) | 2 | 0 | 16 | 31 | 8 | 0 |
| luna-native | 0 (0) | 0 | 0 | 0 | 0 | 0 | 0 |
| terra-native | 0 (0) | 0 | 0 | 0 | 0 | 0 | 0 |
| terra-json | 12 (8) | 1 | 1 | 1 | 29 | 11 | 0 |
| mini-json-medium | 18 (5) | 0 | 0 | 4 | 27 | 5 | 0 |
| luna-json-medium | 4 (0) | 2 | 0 | 27 | 33 | 10 | 0 |
| terra-json-8k | 1 (0) | 0 | 1 | 0 | 30 | 14 | 0 |
| mini-json-medium-8k | 5 (2) | 1 | 0 | 9 | 33 | 7 | 0 |
| luna-json-medium-8k | 0 (0) | 4 | 0 | 28 | 31 | 9 | 0 |

At 4,096 tokens, Luna/low’s seven no-action attempts comprise six inability claims and one instruction-only final answer (`configure-git-webserver`); all seven contain protocol errors. Terra/low’s nine comprise eight first-response token exhaustions and one unsupported ordinary completion claim (`filter-js-from-html`). Mini/native has two inability finals and one first-response token exhaustion: **3/60 = exactly 5%, which fails the strictly-below-5% gate**. HTTP-rejected Luna/native and Terra/native are each 60/60 operational no action, without measuring generated model behavior. Mini/native’s four remaining trial exceptions are empty API answers with finish_reason stop, after earlier actions. They are not token exhaustion, inability claims, or proven outages.

### Paired contrasts under both labels

Differences are first minus second, in percentage points, using paired task bootstrap with both attempts preserved. Low-to-medium comparisons and contrasts against Terra/low span separately timed cohorts and host loads; they are descriptive. Terra/medium comparisons are unavailable because it was not run. Overall contrasts appear below; corresponding search/anchor/sealed contrasts and CIs are archived in [contrasts.json](../logs/calibration-8k-followup-260910/contrasts.json).

| Contrast | Label | Difference (pp) | 95% CI (pp) |
| --- | --- | --- | --- |
| luna-json − terra-json | L1 | -13.3 | -25.0 to -1.7 |
| luna-json − terra-json | L2 | -5.0 | -11.7 to +1.7 |
| terra-json − mini-json | L1 | +20.0 | +8.3 to +33.3 |
| terra-json − mini-json | L2 | +6.7 | +1.7 to +13.3 |
| luna-json-medium − terra-json | L1 | -13.3 | -30.0 to +3.3 |
| luna-json-medium − terra-json | L2 | -5.0 | -16.7 to +5.0 |
| terra-json − mini-json-medium | L1 | +15.0 | +3.3 to +26.7 |
| terra-json − mini-json-medium | L2 | +1.7 | -5.0 to +8.3 |
| luna-json-medium − mini-json-medium | L1 | +1.7 | -6.7 to +11.7 |
| luna-json-medium − mini-json-medium | L2 | -3.3 | -11.7 to +5.0 |
| mini-json-medium − mini-json | L1 | +5.0 | -1.7 to +11.7 |
| mini-json-medium − mini-json | L2 | +5.0 | +0.0 to +11.7 |
| luna-json-medium − luna-json | L1 | +0.0 | -11.7 to +11.7 |
| luna-json-medium − luna-json | L2 | +0.0 | -6.7 to +6.7 |
| mini-native − mini-json | L1 | +0.0 | -6.7 to +8.3 |
| mini-native − mini-json | L2 | +1.7 | -3.3 to +8.3 |
| luna-json − mini-json | L1 | +6.7 | +0.0 to +15.0 |
| luna-json − mini-json | L2 | +1.7 | +0.0 to +5.0 |
| terra-json-8k − terra-json | L1 | +1.7 | -5.0 to +8.3 |
| terra-json-8k − terra-json | L2 | +1.7 | +0.0 to +5.0 |
| mini-json-medium-8k − mini-json-medium | L1 | -1.7 | -8.3 to +5.0 |
| mini-json-medium-8k − mini-json-medium | L2 | -1.7 | -8.3 to +5.0 |
| luna-json-medium-8k − luna-json-medium | L1 | -6.7 | -18.3 to +3.3 |
| luna-json-medium-8k − luna-json-medium | L2 | -1.7 | -8.3 to +5.0 |
| luna-json-medium-8k − terra-json-8k | L1 | -21.7 | -35.0 to -8.3 |
| luna-json-medium-8k − terra-json-8k | L2 | -8.3 | -20.0 to +1.7 |
| terra-json-8k − mini-json-medium-8k | L1 | +18.3 | +6.7 to +31.7 |
| terra-json-8k − mini-json-medium-8k | L2 | +5.0 | +0.0 to +13.3 |
| luna-json-medium-8k − mini-json-medium-8k | L1 | -3.3 | -15.0 to +6.7 |
| luna-json-medium-8k − mini-json-medium-8k | L2 | -3.3 | -15.0 to +5.0 |

## Completion allowance experiment: AD12 evidence

Each comparison below is 4,096 → 8,192 reasoning-plus-output tokens per call, with the same task split, common JSON prompt, reasoning effort, 24-call limit, and $1 rollout guard. Each side contains 60 finalized attempts. Sampling seed 260910 selects the split and bootstrap; API generation seed and temperature remain omitted. The jobs ran sequentially on a shared host; Terra also changes nominal concurrency from 4 to 3, as required for coexistence with W9. These are descriptive allowance comparisons with provider-sampling and host-load variation, not an AD12 decision.

| Configuration (4k → 8k) | L1 passes | L2 passes | No action | Token exhaustion (no action) | 24-call cap | USD cap | All exhaustion |
| --- | --- | --- | --- | --- | --- | --- | --- |
| terra-json-8k | 17 → 18 | 7 → 8 | 9 → 4 | 12 (8) → 1 (0) | 1 → 0 | 1 → 1 | 14 → 2 |
| mini-json-medium-8k | 8 → 7 | 6 → 5 | 5 → 2 | 18 (5) → 5 (2) | 0 → 1 | 0 → 0 | 18 → 6 |
| luna-json-medium-8k | 9 → 5 | 4 → 3 | 7 → 9 | 4 (0) → 0 (0) | 2 → 4 | 0 → 0 | 6 → 4 |

| Configuration (4k → 8k) | Mean known USD / rollout | Max known USD / rollout | USD / 2,800 | Mean calls | Mean agent s | Batch wall s |
| --- | --- | --- | --- | --- | --- | --- |
| terra-json-8k | 0.124265 → 0.112336 | 0.855366 → 0.783566 | 347.94 → 314.54 | 6.10 → 6.23 | 68.23 → 76.07 | 1495.78 → 2245.95 |
| mini-json-medium-8k | 0.012434 → 0.022485 | 0.042391 → 0.097377 | 34.81 → 62.96 | 4.63 → 6.67 | 54.38 → 94.90 | 2151.86 → 3066.81 |
| luna-json-medium-8k | 0.015929 → 0.017009 | 0.133919 → 0.094151 | 44.60 → 47.62 | 7.55 → 7.75 | 77.41 → 73.68 | 2639.47 → 2639.81 |

All projected costs use known response charges. Unknown-cost requests remain separately visible, with conservative reservations retained in cohort accounting; their actual charges are unresolved. API timeouts are trial exceptions under W5e, distinct from token exhaustion and Harbor timeouts.

| 8k configuration | Known USD / 2,800 | Retained reserves in 60 USD | USD / 2,800 with reservations |
| --- | --- | --- | --- |
| terra-json-8k | 314.54 | 0.756453 | 349.84 |
| mini-json-medium-8k | 62.96 | 0.000000 | 62.96 |
| luna-json-medium-8k | 47.62 | 0.000000 | 47.62 |

The reservation-inclusive figure scales finalized rollout guard usage by 2,800/60. It is a conservative planning proxy under the same price assumptions, not an invoice or a claim that retained reservations were billed. Both projections exclude other pilot components.

**terra-json-8k:** token exhaustion changed from 12 to 1; no action changed from 9/60 to 4/60 (8k reasons: Backend failed: TimeoutError: 4). L1 changed from 28.3% to 30.0%; L2 from 11.7% to 13.3%. Attempts with protocol errors changed from 1 to 0; 24-call stops changed from 1 to 0. Mean known cost changed by -9.6%; the 8k solver-only pilot projection is $314.54.

**mini-json-medium-8k:** token exhaustion changed from 18 to 5; no action changed from 5/60 to 2/60 (8k reasons: completion token limit: 2). L1 changed from 13.3% to 11.7%; L2 from 10.0% to 8.3%. Attempts with protocol errors changed from 4 to 9; 24-call stops changed from 0 to 1. Mean known cost changed by +80.8%; the 8k solver-only pilot projection is $62.96.

**luna-json-medium-8k:** token exhaustion changed from 4 to 0; no action changed from 7/60 to 9/60 (8k reasons: final answer: 2, inability claim without execution: 7). L1 changed from 15.0% to 8.3%; L2 from 6.7% to 5.0%. Attempts with protocol errors changed from 27 to 28; 24-call stops changed from 2 to 4. Mean known cost changed by +6.8%; the 8k solver-only pilot projection is $47.62.

### Operational checks by configuration

| Configuration | HTTP 400 | HTTP 429 | HTTP 5xx | Harbor timeouts | Command timeouts | API timeout trials | Null-cost records | Environment failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mini-json | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 |
| mini-native | 0 | 0 | 0 | 0 | 6 | 0 | 2 | 0 |
| luna-json | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 |
| luna-native | 60 | 0 | 0 | 0 | 0 | 0 | 60 | 0 |
| terra-native | 60 | 0 | 0 | 0 | 0 | 0 | 60 | 0 |
| terra-json | 0 | 0 | 0 | 0 | 11 | 0 | 1 | 0 |
| mini-json-medium | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 |
| luna-json-medium | 0 | 0 | 0 | 0 | 10 | 0 | 0 | 0 |
| terra-json-8k | 1 | 0 | 0 | 0 | 14 | 6 | 8 | 0 |
| mini-json-medium-8k | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 |
| luna-json-medium-8k | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 |

Saved 8k HTTP rejections are reported separately from historical native-protocol rejection evidence. The common prompts were retained and no rejected calls were retried.

| Configuration | Task | HTTP status | Saved error code | Action executed in attempt | Evidence |
| --- | --- | --- | --- | --- | --- |
| terra-json-8k | break-filter-js-from-html | 400 | cyber_policy | yes | [saved error](../logs/harbor/calibration-terra-json-8k-260910/break-filter-js-from-html__iRo55Ld/agent/calls/55c3db38978e452aad0b3e2425671d89/http_error_1.json) |

The [8k operational audit](../logs/calibration-8k-followup-260910/operational_audit.json) records the following admission samples. Container counts include W9; pending local trial startups are reserved before admission. Periodic samples complement per-trial admission checks and do not constitute continuous host monitoring.

| Job | Min MemAvailable GiB | Memory pause samples | Max containers in admission samples | Max admitted total slots |
| --- | --- | --- | --- | --- |
| calibration-terra-json-8k-260910 | 20.60 | 0 | 5 | 7 |
| calibration-mini-json-medium-8k-260910 | 19.18 | 0 | 5 | 7 |
| calibration-luna-json-medium-8k-260910 | 22.64 | 0 | 5 | 7 |

8k environment-start failures: none. No finalized attempts were excluded.

## Eligibility and pending decisions

The screen requires complete avg@2 in **15–45% inclusive**, no action **strictly below 5%**, a common prompt and a usable tested API configuration. Prompt parity holds for every row; the two native rejection rows fail API usability. Neither table is a model freeze or approval of pending AD1/AD10–AD12.

### Eligibility under L1

| Configuration | Pass avg@2 / band gate | No action / gate | Common prompt | Eligible |
| --- | --- | --- | --- | --- |
| mini-json | 8.3% / fail | 1/60 / pass | pass | no |
| mini-native | 8.3% / fail | 3/60 / fail | pass | no |
| luna-json | 15.0% / pass | 7/60 / fail | pass | no |
| luna-native | API rejected; unmeasured | 60/60 / behavior unassessed | pass | no |
| terra-native | API rejected; unmeasured | 60/60 / behavior unassessed | pass | no |
| terra-json | 28.3% / pass | 9/60 / fail | pass | no |
| mini-json-medium | 13.3% / fail | 5/60 / fail | pass | no |
| luna-json-medium | 15.0% / pass | 7/60 / fail | pass | no |
| terra-json-8k | 30.0% / pass | 4/60 / fail | pass | no |
| mini-json-medium-8k | 11.7% / fail | 2/60 / pass | pass | no |
| luna-json-medium-8k | 8.3% / fail | 9/60 / fail | pass | no |

### Eligibility under L2

| Configuration | Pass avg@2 / band gate | No action / gate | Common prompt | Eligible |
| --- | --- | --- | --- | --- |
| mini-json | 5.0% / fail | 1/60 / pass | pass | no |
| mini-native | 6.7% / fail | 3/60 / fail | pass | no |
| luna-json | 6.7% / fail | 7/60 / fail | pass | no |
| luna-native | API rejected; unmeasured | 60/60 / behavior unassessed | pass | no |
| terra-native | API rejected; unmeasured | 60/60 / behavior unassessed | pass | no |
| terra-json | 11.7% / fail | 9/60 / fail | pass | no |
| mini-json-medium | 10.0% / fail | 5/60 / fail | pass | no |
| luna-json-medium | 6.7% / fail | 7/60 / fail | pass | no |
| terra-json-8k | 13.3% / fail | 4/60 / fail | pass | no |
| mini-json-medium-8k | 8.3% / fail | 2/60 / pass | pass | no |
| luna-json-medium-8k | 5.0% / fail | 9/60 / fail | pass | no |

**Eligible under L1: none; under L2: none.** The historical Terra recommendation remains withdrawn. Eligibility is an empirical screen, not a model freeze or ratification of AD10–AD12. Selection also depends on the pending AD1 versus PREREG selection/fallback rule and full pilot guards.

### Pilot cost projection and operating limits

The following descriptive projections cover every configuration and multiply mean known cost over all 60 finalized attempts by 2,800 solver rollouts. They exclude judges, evolution, cross-judges, interruptions, and unresolved billing. They are not projections conditional on success.

| Configuration | Mean calls | Known USD / finalized rollout | Known USD / 2,800 | Max known USD / rollout | All-work known USD | Agent s | Batch wall s | Nominal concurrency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mini-json | 4.47 | 0.005871 | 16.44 | 0.022926 | 0.35227860 | 30.05 | 1323.48 | 8 |
| mini-native | 4.53 | 0.005833 | 16.33 | 0.031123 | 0.38758290 | 27.12 | ≥1637.21 | 4 |
| luna-json | 6.63 | 0.010849 | 30.38 | 0.074944 | 0.65096713 | 47.08 | 1635.12 | 4 |
| luna-native | 1.00 | unknown billing | not estimable | unknown billing | 0.00000000 | 0.44 | 972.23 | 4 |
| terra-native | 1.00 | unknown billing | not estimable | unknown billing | 0.00000000 | 0.40 | 981.57 | 4 |
| terra-json | 6.10 | 0.124265 | 347.94 | 0.855366 | 7.45591680 | 68.23 | 1495.78 | 4 |
| mini-json-medium | 4.63 | 0.012434 | 34.81 | 0.042391 | 0.74603555 | 54.38 | 2151.86 | 3 |
| luna-json-medium | 7.55 | 0.015929 | 44.60 | 0.133919 | 0.95572755 | 77.41 | 2639.47 | 3 |
| terra-json-8k | 6.23 | 0.112336 | 314.54 | 0.783566 | 6.74014850 | 76.07 | 2245.95 | 3 |
| mini-json-medium-8k | 6.67 | 0.022485 | 62.96 | 0.097377 | 1.34911310 | 94.90 | 3066.81 | 3 |
| luna-json-medium-8k | 7.75 | 0.017009 | 47.62 | 0.094151 | 1.02053355 | 73.68 | 2639.81 | 3 |

Prices are standard API proxies from [scripts/prices.json](../scripts/prices.json), not verified Azure invoices. Historical Terra/4k’s $347.94 solver-only projection already exceeds the $300 whole-pilot guard. Low-cost early failures do not establish useful capacity. The USD 300/four-day pilot guards remain unchanged.

Mini/JSON used nominal concurrency 8; other original jobs used 4; medium jobs used 3 while W9 shared Docker with a six-container combined admission limit. The new 8k jobs use concurrency 3 and a combined seven-container admission limit, with MemAvailable admission paused below 6 GiB and resumed at 10 GiB. The Mini/native resume log also shows four old containers alongside four new containers. Nominal launcher concurrency therefore does not fully describe host load. Batches were sequential, cache conditions differed, and agent/window times are descriptive. Only Mini supplies concurrency-eight evidence. The eight historical configurations record zero HTTP 429s, zero 5xx responses, and zero Harbor agent timeouts; command timeouts remain failures and do not by themselves prove a host outage. The selected configuration’s capacity gate is unresolved.

Mini/native’s wall lower bound is 970.519741 + 61.280396 + 605.406588 = 1,637.206725 seconds, excluding downtime and unknown tails. Original memory samples and W5d admission evidence remain under the respective run directories; W5d’s historical [audit](../logs/calibration-medium-followup-260910/audit.json) records the shared-host observations.

## Cohort-scoped accounting manifest

The explicit [run manifest](../data/calibration_run_manifest.json) fixes job names, phase tags, cohorts, budget guards, nominal concurrency, and saved config hashes. Reducers reject unmanifested P1.2 calls or ownership mismatches instead of silently absorbing later experiments. Diagnostic spending belongs to the original $40 guard but never to benchmark denominators.

| Job name | Phase | Cohort | Budget guard / USD |
| --- | --- | --- | --- |
| calibration-luna-json-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-luna-json-medium-260910 | P1.2 | w5d-medium | costs/calibration_medium_budget.json / 8 |
| calibration-luna-native-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-mini-json-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-mini-json-medium-260910 | P1.2 | w5d-medium | costs/calibration_medium_budget.json / 8 |
| calibration-mini-native-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-mini-native-260910-recovery2 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-mini-native-260910-resume | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-terra-json-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-terra-native-260910 | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-native-diagnostic | P1.2 | original-six | costs/calibration_budget.json / 40 |
| calibration-terra-json-8k-260910 | P1.2 | w5f-8k | costs/calibration_8k_budget.json / 15 |
| calibration-mini-json-medium-8k-260910 | P1.2 | w5f-8k | costs/calibration_8k_budget.json / 15 |
| calibration-luna-json-medium-8k-260910 | P1.2 | w5f-8k | costs/calibration_8k_budget.json / 15 |

| Cohort | Finalized / verifier rewards | Calls (final / interrupted / diagnostic) | Finalized known USD | Interrupted known USD | Total known USD | Retained reserve USD | Used/reserved / guard USD | Null-cost records |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original-six | 360 / 328 | 1454 (1424 / 29 / 1) | 8.80912498 | 0.03762045 | 8.84674543 | 3.66274995 | 12.50949538 / 40 | 124 |
| w5d-medium | 120 / 105 | 731 (731 / 0 / 0) | 1.70176310 | 0.00000000 | 1.70176310 | 0.00000000 | 1.70176310 / 8 | 0 |
| w5f-8k | 180 / 150 | 1239 (1239 / 0 / 0) | 9.10979515 | 0.00000000 | 9.10979515 | 0.75645300 | 9.86624815 / 15 | 8 |

The 8k cohort has 1231 priced call records, 6 API-timeout records with unknown charges, 1 HTTP rejection with unknown charges, and 1 local rollout-budget stop with no API dispatch or additional API usage. Thus the eight null-cost records do not mean eight unresolved dispatched charges. All seven unresolved dispatched requests belong to Terra. The $1 rollout cap was retained for every attempt. The dedicated [8k manifest](../data/calibration_8k_run_manifest.json) records the allowance, guards, baseline configurations, and final job hashes. No 8k attempts were interrupted or replaced.

Original Mini/native resolves as **51 original + 2 resume + 7 recovery2 = 60** finalized slots. Eight interrupted directories contribute 29 requests, not extra failures. Nine replacement results fill missing slots (some original slots had not started). No finalized failures were retried. The 124 original null-cost records comprise 120 benchmark HTTP rejections, one diagnostic rejection, two reconstructed requests with unknown dispatch, and one local budget stop with `attempts: []` and zero API time. That local stop adds zero API usage and is not an unresolved dispatched charge. Its $0.828084 budget-used value is cumulative previously incurred cost. The two reconstructed reservations total $0.023641 and remain retained.

**Logged Phase-1 single-retry exception:** `query-optimize` and `filter-js-from-html` each had an interrupted original attempt, an interrupted operator replacement, and a finalized second operator replacement. The manifest records their exact three-attempt lineage and the historical recovery authorization reported in R7. This is explicitly a deviation from PREREG Section 6’s single infrastructure retry, despite zero configured API/Harbor retries. It is logged here and in the manifest under this task’s authorization; the prohibited PREREG file was not edited, and formal policy reconciliation remains pending.

The [cohort audit](../logs/calibration-8k-followup-260910/audit.json) checks UUID bijection, finalized slots, payload parity, response token usage, prices, and all budget guards. [costs/summary.md](../costs/summary.md) presents the three cohorts separately, with other project calls outside those guards.

## Native rejection evidence

The saved [Luna diagnostic](../logs/calibration-native-diagnostic/error-response.txt) says exactly:

> Function tools with reasoning_effort are not supported for gpt56luna in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.

It is an `invalid_request_error` on `reasoning_effort`, with null error code, from the low-effort Chat Completions request. **Terra’s specific cause is inferred**, because no Terra HTTP error body was saved; its 60 HTTP 400s establish rejection of the tested configuration only. Neither row measures general native-tool capability. Responses is a suggested alternative for Luna, with no adapter or calibration evidence here. Changing only Luna to reasoning none would create another model-specific experimental difference.

The W5e backend change archives sanitized HTTP error bodies and endpoint metadata per HTTP attempt, including retried errors, without changing requests, retry decisions, or reservations. The original backend bytes remain in [the source archive](../logs/calibration-r7-reanalysis/sources/openai_api.py); the historical source manifest is preserved. AD11’s JSON-for-all choice and AD12’s completion-allowance choice remain pending. Compatibility preflight is still needed before any future benchmark dispatch.

## What remains unknown

- Which A9 reading is ratified (AD10), which selection/fallback rule governs (AD1 versus PREREG), and the formal reconciliation of the two operator retry exceptions.
- Whether to ratify common JSON (AD11), or implement and calibrate a common valid native transport. Terra’s exact rejection body is unavailable.
- Whether to ratify the 8,192-token allowance (AD12). The three new jobs supply evidence; Terra/medium remains unmeasured.
- Repeat-run variability, effects of provider sampling defaults, and a generation seed. Task-bootstrap CIs do not estimate these sources of variability.
- Selected-model concurrency-eight behavior, realized host-load effects, and whether a lower concurrency will be ratified.
- Azure invoice charges for rejected/interrupted requests and full pilot costs after model selection, including judge/evolver costs and runtime.
- Historical execution-start timestamps were not recorded; subprocess traceback evidence recovers action presence in these timeout cases, not exact timing. Live execution-start logging remains a follow-up outside this change.

## Appendix: superseded published tables

**Superseded — retained verbatim for provenance, not current results or decisions.** These tables used finish-only no action and reward-1-without-trial-exception pass. They include the original six rows and W5d medium additions. Their Terra eligibility and associated recommendation are withdrawn. The complete previous document is [archived](../logs/calibration-r7-reanalysis/calibration-before.md).

### Superseded: Setup and reproducibility

| Split | Easy | Medium | Hard | Total |
| --- | ---: | ---: | ---: | ---: |
| search | 1 | 11 | 6 | 18 |
| anchor | 0 | 4 | 2 | 6 |
| sealed | 0 | 4 | 2 | 6 |

### Superseded: Metrics and results

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

### Superseded: Metrics and results

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

### Superseded: Metrics and results

| Contrast | Difference (pp) | 95% CI (pp) |
| --- | ---: | ---: |
| mini-native − mini-json | 0.0 | -6.7 to 8.3 |
| luna-json − mini-json | 15.0 | 6.7 to 25.0 |
| terra-json − luna-json | 5.0 | -5.0 to 16.7 |

### Superseded: Decision rule

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

### Superseded: Decision rule

| Eligible configuration | Reasoning | Pass avg@2 | No-action | USD / rollout | Projected USD / 2,800 rollouts |
| --- | --- | ---: | ---: | ---: | ---: |
| terra-json | low | 28.3% | 1.7% | 0.124265 | 347.94 |

### Superseded: Medium versus low reasoning

| Configuration | Pass change (pp; paired 95% CI) | No-action, low → medium | USD / rollout, low → medium | Cost ratio | Agent s, low → medium | Agent-time ratio |
| --- | --- | --- | --- | ---: | --- | ---: |
| mini-json-medium | +5.0 (-1.7 to +11.7) | 0/60 → 0/60 | 0.005871 → 0.012434 | 2.12× | 30.05 → 54.38 | 1.81× |
| luna-json-medium | +10.0 (-1.7 to +21.7) | 7/60 → 7/60 | 0.010849 → 0.015929 | 1.47× | 47.08 → 77.41 | 1.64× |

### Superseded: Follow-up accounting and verification

| Job | Min MemAvailable GiB | Samples below 6 GiB | Max alexgshaw at admission | Max periodic task containers | Docker admission pauses |
| --- | ---: | ---: | ---: | ---: | ---: |
| [calibration-mini-json-medium-260910](../logs/calibration-mini-json-medium-260910/memory.jsonl) | 22.88 | 0 | 4 | 5 | 0 |
| [calibration-luna-json-medium-260910](../logs/calibration-luna-json-medium-260910/memory.jsonl) | 21.04 | 0 | 4 | 5 | 0 |

### Superseded: Interrupted runs, memory guard, and accounting

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

### Superseded: mini-json

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

### Superseded: mini-native

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

### Superseded: luna-json

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

### Superseded: luna-native

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

### Superseded: terra-native

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

### Superseded: terra-json

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

### Superseded: mini-json-medium

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

### Superseded: luna-json-medium

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
