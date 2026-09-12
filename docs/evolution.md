# Evolution loop and ratified pilot entry

The Phase 2 pilot has not been launched. The binding contract is
[decisions-260912.md](decisions-260912.md); the older validation results below
are historical infrastructure evidence. The two reviewable input manifests
are [pilot-t1-260912.json](../runs/manifests/pilot-t1-260912.json) and
[qualification-t1-260912.json](../runs/manifests/qualification-t1-260912.json).
Checks make no model calls, invoke no Docker commands, and never create or
reset a budget guard. A failed check exits 2.

## Changes by decision

| Decision | Implemented contract |
| --- | --- |
| AD1 | Terra `gpt56terra` on `TASK_ALT2`, low reasoning, JSON, 8,192 task completion allowance, 24 calls, 30-second commands; standing behavior metrics for every arm. |
| AD5 / AD7 | Epsilon 0; two proposals for A0/A1/A2, three for A3-loop. Each C-TTS variant matches its own comparator's logical allocation, with physical infrastructure replacements reported separately. |
| AD9 | Score range validation precedes tolerance/acceptance; the ordinary strict-improvement path now validates unit-range scores too. |
| AD10 | L1′ is the only oracle label. Recovered observations do not veto a verifier pass. Raw reward stays separate. |
| AD11 / AD12 | JSON protocol remains fixed. Task solvers and the ordinary evolver use 8,192 completion tokens. Protocol errors are rollout-level standing metrics, including recovered errors. Judge sampling/allowances retain the measured v3 condition: Flash 2,048 and Kimi 8,192. |
| AD13 | A sole API call timing out without response is infrastructure: one durable clean-state replacement, then logged exclusion. HTTP content-policy rejections are task failures with a distinct category, never retried/excluded. Failed empty API responses do not create spurious parse errors. |
| AD14 | Pilot estimate $450, hard guard $650, 120 hours; qualification $30. $1 per rollout and $5 per evolver session. The first five distinct completed evolver sessions produce a durable re-estimate; guards/deadline do not reset. A3-native calibration is concurrent and is not a pilot input/gate. |
| AD15 | Judge and evolver receive the identical v3 capped evidence. Claim matching uses the full sanitized event stream, N=5, and writes only oracle-side diagnostics. Tau A1=0.006022079, A2=0.017391640. |
| AD8 / review rule | Content hashes reject condition drift; `--check` never silently re-freezes changed inputs. Changes after start require timestamped deviations and a new explicitly reviewed condition. R10 reviews the frozen manifest, PREREG and Section 5 log. Provider cache enforcement is the ratified six-of-seven-row limitation. |

AD2's reverse-keyed item 5 remains in the judge contract. AD3's multi-label
edit analysis, AD4's score-unit judge-repeat comparator, and AD6's exploratory
pilot interpretation are specified in PREREG; these changes do not introduce
new analysis results. A4 remains excluded: PREREG does not state the requested
`tau_A4 = 0.5 * (tau_A1 + tau_A2)` derivation. The manifest records its potential
0.5/0.5 component weights without adding it to the arm schedule.

## L1′ and infrastructure accounting

Pass is **a valid numeric verifier reward exactly 1, no Harbor agent timeout,
and a normal terminal finish**. Terminal token/step/budget exhaustion,
unrecovered executor failure, trial exception, or provider policy rejection
fails even if the verifier produced 1. A normal final answer expressing
inability remains an outcome decision: the answer alone cannot veto or confer
credit. Missing/invalid verifier rewards remain unlabelled, with their
missingness reported; raw reward is never rewritten.

An observed command timeout followed by continued execution and normal finish,
a recovered parse error, and any nonzero command exit are ordinary observations.
They affect the operational metrics, never L1′. An unrecovered failure and a
recovered event are distinguished by trusted terminal evidence. The old
`executor`/`strict` choices are rejected. Common defaults retain a compatibility
field fixed to `L1'` for the separate T2 caller; pilot files use `pass_label`
only. Historical labels/feedback are not silently relabelled.

API-timeout-only requires one call, no response (an empty failed assistant
record is not a response), and no executed action. Retry identity is persisted
before dispatch. A second infrastructure failure excludes that logical slot;
its original and replacement evidence/costs remain archived. The replacement
cannot inherit a physical-attempt index into the logical record or gain another
retry after restart. A response-bearing attempt, or timeout after multiple
calls/actions, is not this exception. Provider `cyber_policy`/content-policy
errors propagate a fixed termination category from the private HTTP response
archive, keeping provider text out of feedback. Policy rejections count as 0
in the denominator even when the verifier reward is absent.

`aggregate().O` excludes explicitly logged infrastructure slots, averaging
remaining attempts within tasks and then tasks/seeds equally.
`O_fixed_denominator` retains the exclusion-as-failure sensitivity. Missing
paired blocks remain incomplete; no replacement task ID is invented.

## Standing metrics and evidence isolation

Public per-arm/per-iteration summaries and the private oracle report contain
`standing_metrics`: no-action, inability-claim, exhaustion, protocol-error and
command-timeout rates, their numerators/denominator, missing diagnostic counts,
retry counts, infrastructure-exclusion counts/reasons, and policy-rejection
counts. Each rate counts affected logical rollouts, not individual events;
recovered errors count. Inability means a no-action terminal answer claiming
inability/no tools. A command merely emitted by the model does not establish
an executed action. Public summaries aggregate all partitions without task
identities, verifier values, or sealed scores. The oracle-side
`behavior-i<iteration>.json` additionally breaks down partitions. Ordinary seed
batches already carry these same metrics in their private/public batch artifacts;
no extra solver batch is introduced for diagnostics.

`feedback/<arm>/<experiment>/<rollout>.json` embeds the exact `JudgeInput`
export used by its scorer. V3 keeps the first/last 4,000 observation characters
and caps the canonical trajectory at 200,000 characters, using explicit
elision markers and longest-middle-first reduction. The historical filename
`sanitized-full.json` is a compatibility alias for that **capped** export;
it is never the claim detector's input.

The detector consumes `sanitized_events()` before any cap. It links claims
within five solver steps to observed checks, preserves negated/quoted claims
and ambiguous objects for review, and distinguishes successful, failed, and
unobserved checks. A failed executed check is not "unrun." Matching is a
mechanical diagnostic, not an assertion of deception. Outputs are under
`oracle/<experiment>/<arm>/behavior/`; per-iteration oracle reports include
trace-level and conditional claim-level prevalence. Detector outputs, full
uncapped traces and verifier diagnostics never enter feedback or public reports.

## Manifest and launch gates

The two files contain the same condition except experiment identity, `T`,
seeds, and phase budget. Qualification uses T=1, seed 1, estimate/guard $30;
pilot uses T=6, seeds 1/2, estimate $450 and guard $650. Both retain the complete
18 search / 6 anchor / 6 sealed split and 120-hour maximum. The ratified
five-day limit is not reset when checking or resuming.

| Manifest fields | Frozen value / meaning |
| --- | --- |
| `task_model` | `gpt-5.6-terra`, `gpt56terra`, `TASK_ALT2`, low, JSON, 8192, 24 calls, 30 s commands, 180 s API timeout. |
| `evolver_model`, `judge_model`, `cross_judge` | DeepSeek-V4-Pro; DeepSeek-V4-Flash; Kimi-K2.6 with A1 only, oracle-side diagnostics, never selection. |
| `arms` | A0, A1, A2, A3-loop, C-TTS-A0, C-TTS-A1, C-TTS-A2, C-TTS-A3-loop. |
| `candidates_per_arm`, `a3` | Ordinary 2; A3 3 and its existing v3 native recipe. No A3 module is edited by this task. |
| `pass_label`, `api_timeout_policy` | `L1'`; `infrastructure`, with one replacement and explicit exclusion. |
| `tau`, `tau_evidence`, `judge_calibration` | Ratified rounded A1/A2 SDs and hashed actual five-repeat v3 artifacts. The SD comparison permits only rounding at the ninth decimal. A3 noise readiness is separate from the non-gating native calibration. |
| `split`, `split_sha256` | `data/tb2_split.json`; `a3796dd7ed0a7f384e5474d684f38762306eb795e7d52a5abce79e3080b340c2`. |
| `evidence_version`, `evidence_caps`, `prompt_version` | `v3`, 4000/4000/200000, `judge-v3`. |
| `budget`, `reestimate_rule` | Ratified estimates/guards; $1 solver, $5 session, re-estimate after 5 completed sessions. Forecasts exclude unpriced extra roles explicitly. |
| `concurrency`, `memory_min_available_gib` | 4, with the existing 6 GiB MemAvailable guard; foreign TB2 containers reduce own concurrency to 2. |
| `blinding` | True; separate public summaries and oracle report. |
| `input_hashes`, `manifest_sha256` | SHA-256 over PREREG, decisions, split, seed/harness, prices, lock, controller sources and launchers, plus a canonical whole-manifest digest. No API credentials. |
| `section5_evidence`, `entry_evidence` | Hashed real T1 Section 5 matrix and P1.8/P1.9/P1.11 evidence; missing or changed evidence blocks. Row 7's documented provider limitation is accepted; missing evidence for the other rows is not. |
| `qualification` | Pilot binds the qualification manifest digest and expected completion-report path. Qualification cannot certify itself before running. |

`--check` verifies the portable input manifest. It reads only the explicit
**Frozen state** section of PREREG; absence/unfrozen state reports exactly
`PREREG not frozen`. W12's version-table declaration "frozen pending R10" is
recognized as a frozen specification; its pending R10 row is a separate
entry error, not an R10 certification. Administrative
freeze-commit updates still change the PREREG hash and require an explicit
pre-start reseal. Hash changes never take effect merely by running `--check`.

The budget check opens an existing guard read-only and verifies amount,
duration, halt state, expenditure and deadline. It does not arm one implicitly.
Arm a matching `PhaseGuard` only when launch is authorized and ready, using
the manifest's estimate/guard/hours at `costs/<experiment>/budget.sqlite`.
Launch-time resolution then freezes actual endpoints, served-condition
settings, local image IDs and host runtime in `runs/<experiment>/manifest.json`.
Resume re-resolves and refuses drift. `run_evolution` remains infrastructure-only.

```bash
uv run python scripts/run_pilot.py --manifest runs/manifests/qualification-t1-260912.json --check
uv run python scripts/run_pilot.py --manifest runs/manifests/pilot-t1-260912.json --check

# Only after the gates and R10 are satisfied and launch is authorized:
uv run python scripts/run_pilot.py --manifest runs/manifests/qualification-t1-260912.json
uv run python scripts/run_pilot.py --manifest runs/manifests/pilot-t1-260912.json
# Add --resume to continue the same frozen run.
```

The launcher prints the entire per-arm schedule and solver cost projection
before refusing. The current implementation includes ordinary baseline and
smoke allocations and does not yet implement W12's reconciled reuse/monitoring
schedule. Its nominal **13,872** pilot rollouts cost **$1,553.664** at $0.112
before judges, evolvers, retries or cross-judging. Qualification uses **1,296**
slots, projecting **$145.152**. Neither fits its ratified guard. PREREG's
reconciled 12,648–13,224-slot schedule also exceeds the pilot guard. These are
explicit feasibility blockers, not permission to reduce tasks/arms or increase
spending. No complete paid pilot is claimed.

`runs/` and `logs/` are ignored by the repository. The manifest files are ready
for review/staging with `git add -f runs/manifests/*-t1-260912.json`; this task
does not stage, commit or push. The check artifacts are archived under
`logs/evolution/<experiment>/entry_gates.json`, with stdout in
`logs/{qualification,pilot}-t1-260912-check.txt`. Regression coverage is in
`tests/test_evolution_ratified.py` and the updated evolution suites.

## Real validation (infrastructure evidence only)

The corrected validation is `r8b-validation-260910-final`. Its exact manifest is
`runs/r8b-validation-260910-final/manifest.json`: A0/A1, one seed, one iteration,
two proposal slots per arm, six search tasks, three anchors and three sealed
tasks, terra-low, JSON, 8,192 tokens, 24 calls, four concurrent Harbor admissions.
The search task IDs are `bn-fit-modify`, `break-filter-js-from-html`,
`build-pmars`, `caffe-cifar-10`, `cancel-async-tasks`, `cobol-modernization`.

An initial diagnostic, `r8b-validation-260910`, stopped after six seed sealed
attempts and **before baseline feedback or proposals**. Harbor's concrete
factory bypassed the first subclass override. The corrected factory was then
checked through an unpaid actual Docker grading execution. The six diagnostic
attempts are excluded from the corrected validation and from the Section 5
matrix; their original artifacts and immutable labels are preserved. Their
costs remain inside the **same $15 shared guard**, under
`costs/r8b-validation-260910/`. No budget reset or post-hoc relabelling occurred.
See `logs/r8b-validation-diagnostic.json` and
`logs/r8b-grading-unpaid-check.stdout`.

The completed run used **129 logical rollout slots / 141 physical attempts**
(A0 68/76; A1 61/65), with 12 linked infrastructure replacements. Each arm had
exactly two proposal slots. A1's second proposal was unchanged and invalid;
no extra proposal was granted. Each arm completed its seed sealed checkpoint
before the first proposal, six paired anchor attempts over the three anchor
tasks, and its final search/sealed measurements. This is infrastructure
validation, not pilot data or an estimate of evolution effectiveness.

| Arm | Valid proposals / slots | Final J | Final search O | Decision | Model requests | Accounted cost, final run only |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| A0 | 2 / 2 | 0.50 | 3/6 | Reject; seed retained | 392 | $7.404558 |
| A1 | 1 / 2 | 0.40 | 3/6 | Reject; seed retained | 417 | $5.817014 |

Final search has **zero missing judge scores and zero missing oracle labels**
in both arms. Sealed outcomes are written only to
`oracle/r8b-validation-260910-final/final_report.json` and its four referenced
t=0/t=1 checkpoint files. Public tables deliberately contain search metrics.
The full allocation, missingness, condition, cost, and admission inventory is
`logs/evolution/r8b-validation-260910-final/validation_report.json`.

There were **809 requests** in the corrected run: **717 task, 50 evolver,
42 judge**. The four evolver sessions used **12, 15, 11, 12 calls**, respectively,
within their durable 24-call caps. All 141 Harbor admissions used limit **4**;
minimum **MemAvailable was 25.167 GiB**, with no foreign worker detected.
The completed-run resume added **zero requests and zero duplicate summaries**:
`logs/evolution/r8b-validation-260910-final/completed_resume.json`.

The machine-readable entry artifact is
**`runs/r8b-validation-260910-final/section5_matrix.json`**. It marks the
validation complete and **6/7 rows passed**, with hashed paths to actual
container probes, grading boundaries, all 141 trace scans, manual inspection
of 20 sanitized exports, exact paid wire requests, 42 real injected-oracle
exports, and both real acceptance cycles. No fixture rollout substitutes for
this evidence. All **809 wire message hashes match**, with no injected system
message. The grader-only canary was absent from the paused solver namespace.

**Row 7 remains blocked.** All request metadata identifiers are distinct, but
among 92 judge/evolver request archives, **38 responses report cache hits** and
54 omit the cache count. This does not identify cross-arm sharing, and cannot
establish an enforced partition. No provider-enforced cache namespace, disable
control, or qualifying attestation was available. Unique API user metadata is
not accepted as proof. The launcher therefore refuses pilot entry.

| Accounting scope | Requests | Known token-priced USD | Unresolved reservation USD | Guard-accounted USD |
| --- | ---: | ---: | ---: | ---: |
| Initial diagnostic | 46 | 1.161592 | 0.102947 | 1.264538 |
| Corrected validation | 809 | 10.953451 | 1.790118 | 13.221572 |
| Shared total | 855 | **12.115042** | **1.893065** | **14.486110** |

The **$15 guard was not exceeded**. Fully priced receipts settle at their known
price; responses missing cache details retain conservative response bounds;
unknown charges retain their full reservations. The shared total additionally
includes **$0.478004** of conservative bounds for responses without complete
pricing metadata. Prices are the frozen catalog's planning assumptions, not a
verified provider invoice. The all-uncached sensitivity is $15.129591 including
unknown reservations; it is distinct from receipt-backed guarded accounting.
All settlement reductions are journaled in
`costs/r8b-validation-260910/settlement_reconciliation.jsonl`.

Reconciliation records **855 reservations, 855 intents, 839 receipts, and 855
mapped ledger entries**. All items are represented in
`costs/r8b-validation-260910/reconciled_ledger.json`; **18 requests remain
unresolved**, including two with response receipts whose total charge is
uncertain. There are no unassigned budget requests or damaged ledger/audit rows.
See `costs/r8b-validation-260910/reconciliation.json`. Unknown usage is retained,
not assigned a zero cost.

The exact source used by both arms is archived under
`logs/r8b-validation-260910-final-source/`; its controller hash matches the
validation manifest. After the real run and successful completed-run resume,
further recovery, accounting, control-allocation, and entry-gate hardening was
applied. The final code's pass-rule regression matches **all 129 logical rows**
without modifying any original artifact or feedback:
`logs/evolution/r8b-validation-260910-final/postrun_label_regression.json`.
The final grader also passed an unpaid actual Docker check with model dispatch
forbidden (`logs/r8b-final-grading-check.json`).

The final controller has a different condition hash. Its attempt to resume the
old validation was refused before any paid request:
`logs/evolution/r8b-validation-260910-final/changed_condition_resume.json`.
**Entry qualification for the final controller remains outstanding**; neither
the tests nor read-only replay are represented as a second paid validation or
as permission to reuse the earlier condition's matrix.

## Ratified-contract verification (2026-09-12)

`uv run pytest -q` passes: **685 passed, 6 skipped in 52.33 seconds**;
see `logs/ratified-full-final.txt`. Ruff passes the changed Python files and
`git diff --check` passes. No paid calls or Docker commands were executed.

Both final `--check` runs exit **2** and report `prereg_frozen: true`,
`paid_calls: 0`, and `docker_calls: 0`. Manifest/source/split/tau and Section 5
checks have no errors. Both report pending R10, missing P1.8/P1.9/P1.11 evidence,
an unarmed guard, and the over-budget nominal schedule. Pilot additionally
reports `Qualification has not passed`. The full stdout and JSON paths above
preserve the per-arm schedules and exact errors.

## Historical R8b verification and entry requirements

`uv run pytest -q`: **451 passed, 5 skipped** in 20.11 seconds, archived in
`logs/evolution-followup-pytest-final.txt`. Focused R8b tests include the complete
mock A1/A2 control branches, later-iteration recovery, exact request hashing,
receipt/ledger crash windows, fixed task blocks, private state, and fail-closed
manifest/matrix checks. Ruff passes evolution modules, both launchers, and the
changed evolution tests; `git diff --check` passes.
The actual unpaid Docker grading check passed before corrected paid validation.

The R8b-era pending-decision, v2-calibration, native-A3 and provider-cache
blockers are superseded by the ratification and the current gates above.
Qualification must still demonstrate the final controller/evidence/pass label
condition; the historical matrix alone does not certify these code changes.
Fresh-process grading also requires qualification
for tasks whose intended result depends on surviving solver-created services;
filesystem isolation alone does not establish unchanged benchmark semantics.
The manual trace review records that `break-filter-js-from-html` explicitly
ships and advertises `/app/test_outputs.py`; its contents and paired outputs
are removed from v2 judge evidence, while the instruction is preserved. This
public task fixture is not evidence that arbitrary hidden tests are safe to
ship in solver images.
No pilot, CLI model, commit, push, or external publication was performed.

## Historical evidence

The earlier `p13-p16-260910` run and its $6.685313 conservative planning estimate
remain historical infrastructure evidence. Its 2,545 wire requests contain the
old injected system message and its original feedback-policy deviation remains
disclosed. It does not demonstrate the corrected wire contract or a passed
seven-row gate. The prior full document is preserved at
`logs/evolution-pre-r8b-followup.md`; original requests, scores and artifacts were
not relabelled or overwritten by this follow-up.
