# Evolution loop and fail-closed pilot entry

This document distinguishes infrastructure validation from pilot data. The
Phase 2 pilot has **not** been run. Pending addendum decisions are manifest
parameters, not ratified study facts. The pilot launcher refuses paid dispatch
while preregistration, calibration, isolation, or budget entry gates are missing.

## R8b follow-up

| Finding | Change and regression coverage | Status |
| --- | --- | --- |
| 1 — Section 5 | Actual evolver container environment and mounts are archived; peer probes target the other real arm root. Solver processes are paused before filesystem snapshotting; a fresh grader container receives tests and a grader-only canary. The machine-readable matrix audits actual requests, exports, acceptance-cycle feedback, and these live boundaries. The concrete Harbor factory has a regression test. | 6/7 rows passed on the completed real run. Row 7 is blocked: provider cache partitioning is unverified. Finding 1 is not closed. |
| 2 — pass labels / A9 | `oracle_label` applies reward 1, no executor/protocol failure, and no agent timeout. `executor` and `strict` readings are explicit. Raw reward is separate. Post-hoc relabelling raises and cannot update feedback. One linked clean-state infrastructure replacement and one identical-snapshot grader retry are durable. | Implemented; executor/strict, timeout, missing-label, and immutable-feedback regressions pass. |
| 3 — pilot launcher | `scripts/run_pilot.py` resolves/freezes a manifest, checks entry gates before evaluation, and schedules independent seeds and iterations with a shared phase guard. Seed sealed t=0 precedes proposals. Controls match their own comparator. A3-loop is a reserved hook that refuses dispatch. | Implemented and deliberately blocked. No pilot calls. |
| 4 — C-TTS v2 | Held-out control scoring uses full v2 exports and trusted termination metadata. The obsolete 12,000-character export argument is gone. | Full mocked A1/A2 control paths preserve observations exceeding 16,000 characters. No paid control run was requested. |
| 5 — wire prompt | Exact wire messages are hashed; transport adds no system message. Cache identifiers are API metadata. The real wire audit compares paid judge payloads with queued v2 evidence and evolver instructions with the common template. | All 809 real wire requests have exact message hashes and no injected system message; 42 actual A1 judge exports also pass oracle-injection comparison. |
| 6 — control resume | Explicit replicate indices and durable expected index lists replace completed-row counts. Resume fills missing identities; selected pools report K and missing slots with fixed denominators. Comparator/selection-signal mismatches and duplicate identities fail. | A1/A2 out-of-order resume regressions pass, including replicate 1 completing before 0. |
| 7 — iteration recovery | Recovery and cleanup inspect only the active iteration. The parent is its checkpoint, including after a previous acceptance and sealed evaluation. | Regression recovers a second-iteration source write with the accepted first-iteration parent. |
| 8 — aggregation | Oracle rates average fixed-allocation task blocks and seeds equally. Missing counts, observed-attempt diagnostics, complete common-cohort gaps, and incomplete blocks are separate. J retains missing judge scores as missing, as PREREG requires; a fixed-allocation zero-score sensitivity is separate. | Unequal task sizes, missing observations, duplicate identities, and missing control slots covered. |
| 9 — condition freeze | Resolved endpoints, deployments, effective sampling/allowances, tau, prompts, evidence implementation, seed, split, prices, controller source, cached task descriptors, actual image IDs, launchers, harness runtime, and dependency lock are hashed. Resume re-resolves and rejects changes. Served-version drift persists a phase halt. | Implemented; v1 tau is never silently substituted for v2. |
| 10 — accounting | Reservation and intent precede dispatch. Transport writes a durable response and accounting receipt before settlement or backend ledger append. Reconciliation repairs lost ledger rows from receipts, or receipts from archived responses, and explicitly lists unmatched/unknown items. | Crash-window, damaged-ledger, partial-503, exact-wire, and orphan-reservation regressions pass. All 855 reservations/intents map to ledger entries; 18 charges remain explicitly unresolved. |
| 11 — investigator blinding | Sealed/anchor Harbor results, state values, batches, and checkpoint metrics live under `oracle/`. Public state contains private references. Public summaries/stdout expose search J/O and decisions only. A separate oracle report is produced at run end. | Private-state and public-summary regressions pass. |
| 12 — durable evolver cap | A SQLite reservation counts each dispatched request against its session before dispatch. Restart checks durable counts, not just assistant traces; recovery can replay a response archived before its trace append. | Receipt-before-trace restart regression passes; the original 24-call cap remains binding. |

The public interfaces of `evolution.judges`, `judge_queue`, `sanitize`, and
`acceptance` are retained. Candidate source still runs in a networkless peer;
only its own working harness is writable and its own feedback is mounted read
only. Credentials remain on the host. Candidate code is never imported into the
credential-bearing controller. English source/comments and the original JSON
action protocol are retained.

## Manifest schema

`evolution.manifest.defaults()` produces a **draft**, not approval. The resolved
manifest is written to `runs/<experiment>/manifest.json` and compared byte for
byte on resume. Input examples are retained under `runs/`.

| Fields | Meaning / proposed default |
| --- | --- |
| `schema_version`, `experiment`, `purpose` | Version 1; stable ID; `pilot` or separately authorized `infrastructure`. |
| `budget_experiment` | Optional shared cost-directory identity; validation diagnostics and the final run share the same guard. No budget or deadline reset on resume. |
| `prereg_frozen`, `ratifications` | Explicit false defaults. AD1/AD7/AD10–AD14 remain pending. The launcher also rejects the current PREREG draft text. |
| `task_model.name`, `.deployment`, `.endpoint_prefix` | Proposed `gpt-5.6-terra`, `gpt56terra`, `TASK_ALT2`. Endpoint URLs are resolved; API keys are never serialized. |
| `.reasoning_effort`, `.completion_allowance`, `.tool_protocol` | Proposed `low`, `8192`, `json`, per pending AD1/AD11/AD12. |
| `.max_calls`, `.api_timeout_s` | 24 model calls and 180 seconds per request. Tool commands remain bounded at 30 seconds. |
| `tool_failure_reading` | `executor` default, or `strict`; the latter also vetoes chosen commands' nonzero exit codes. Executor exceptions, timeouts and action-protocol failures veto both. |
| `api_timeout_policy` | Proposed `infrastructure`, or `failure`, per pending AD13. Only an attempt whose sole API call timed out without an executed action qualifies for the infrastructure replacement. |
| `tau`, `tau_evidence`, `epsilon`, `acceptance` | Tau per judge and its evidence (`path`/`sha256`, five finite aggregate scores under v2 and the frozen prompt, provider, task-provider, seed, and split hashes/settings); A0 is 0, uncalibrated judge defaults are null. Epsilon defaults to 0. Pilot acceptance is ordinary strict improvement; infrastructure can exercise the private anchor gate. |
| `arms`, `a3_loop_hook` | A0/A1/A2, reserved A3-loop, and matched C-TTS variants. The unimplemented A3 hook is an explicit pre-dispatch blocker. |
| `seeds`, `T`, `candidates_per_arm` | Proposed seeds 1/2, T=6, ordinary arms 2 proposals, A3-loop 3 per pending AD7. Distinct seed workspaces share one phase budget. |
| `partition_limits`, resolved `tasks` | Empty for pilot; validation takes the first 6 search, 3 anchor, and 3 sealed task IDs. Full split hash remains frozen. |
| `evidence_version`, `prompt_version` | `sanitized-trajectory-v2`, `judge-v2`; historical v1 tau does not calibrate this condition. |
| `budget` | Proposed estimate $400, hard guard $600, 120 hours per pending AD14; $1 per solver rollout and $5 per evolver session. Validation uses $15 total. |
| `concurrency` | Exactly 4; a shared process admission guard also requires at least 6 GiB **MemAvailable**. |
| `cache_policy`, `cache_partition_attestation` | Unique metadata identifiers; provider enforcement is an explicit unresolved gate, not an inferred guarantee. |
| `entry_evidence`, `section5_artifact` | Paths and SHA-256 values for required entry evidence and the real seven-row matrix. Missing, changed, fixture, or blocked evidence refuses entry. |
| resolved `providers`, `hashes`, `host_runtime`, `runtime_images`, `task_images`, `split_sha256`, `resolved_sha256` | Actual settings and content/image identities, rechecked before dispatch. Local sampling identities do not imply provider determinism; unsupported seed controls are disclosed. |

The infrastructure manifest uses tau(A1)=0 **only to exercise the acceptance
plumbing**, not as a calibrated threshold. Pilot manifests require v2 variance
evidence. Resolving a manifest requires cached task descriptors matching the
split and locally available task image digests; it does not make model calls.

## Launching, gates, and blinding

Prepare and inspect a pilot without running it:

```bash
uv run python -m scripts.run_pilot \
  --manifest runs/example/input_manifest.json --prepare example
uv run python -m scripts.run_pilot \
  --manifest runs/example/input_manifest.json --check
```

The second command checks the frozen preregistration flag and text, pending
ratification flags, manifest and split hashes, v2 tau evidence, the Section 5
artifact and its evidence hashes, P1.8/P1.9/P1.11/P1.5 artifacts, the armed phase
guard with matching estimate/ceiling/duration, supported arms, hashed provider
cache attestation, and nominal schedule cost. An existing
condition mismatch fails before paid dispatch. A3-loop cannot fall through to
an ordinary arm. `run_evolution` rejects pilot manifests; its manifest path is
for explicitly authorized infrastructure validation. The legacy ungated
command-line path is removed; `--manifest` is required. `--recover-sessions`
explicitly resumes eligible unfinished sessions within their durable limits.

The final unpaid preflight is archived in
`runs/r8b-pilot-proposal/entry_gates.json` and
`logs/r8b-pilot-proposal.stdout`; its input manifest references the completed
real Section 5 artifact. It is **blocked**, exited 2, and made zero model calls.
The earlier preflight remains under `runs/r8b-pilot-preflight/`.
The default ordinary/control schedule already allocates **11,520 rollouts**
across three ordinary arms, matched controls, and two seeds, including t=0.
At the proposed $0.112 unit rate that is **$1,290.24 for solvers alone**,
before A3, judges, evolvers, retries, or cross-judging. The pending $400/$600
proposal cannot fund this implemented schedule. Ratification must resolve this
mismatch rather than allowing the guard to stop a purported complete pilot.

The ordinary loop freezes the seed, completes a sealed avg@2 checkpoint at
`t=0`, then obtains search feedback and proposals. Candidate screening uses one
attempt per search task; confirmation uses fresh paired avg@2 attempts; final
search measurement uses one attempt, and sealed measurement uses avg@2. An
incomplete arm never substitutes its latest checkpoint for T. Public reports
contain J, search O, acceptance decisions and operational counts. The separate
`oracle/<experiment>/final_report.json` contains sealed checkpoint metrics.
Private fields are not returned by the public loop summary.

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

## Verification and remaining entry requirements

`uv run pytest -q`: **451 passed, 5 skipped** in 20.11 seconds, archived in
`logs/evolution-followup-pytest-final.txt`. Focused R8b tests include the complete
mock A1/A2 control branches, later-iteration recovery, exact request hashing,
receipt/ledger crash windows, fixed task blocks, private state, and fail-closed
manifest/matrix checks. Ruff passes evolution modules, both launchers, and the
changed evolution tests; `git diff --check` passes.
The actual unpaid Docker grading check passed before corrected paid validation.

Open pilot dependencies are ratification of the pending decisions, frozen
PREREG, v2 tau calibration, native A3 calibration/implementation, compatible
P1.8/P1.9/P1.11 entry evidence, a realistic complete-schedule budget, provider
cache enforcement, and qualification of the final controller hash.
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
