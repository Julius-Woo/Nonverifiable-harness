# Evolution loop and ratified pilot entry

The Phase 2 pilot has not been launched. The binding contract is
[decisions-260912.md](decisions-260912.md); the older validation results below
are historical infrastructure evidence. The two reviewable input manifests
are [pilot-t1-260912.json](../runs/manifests/pilot-t1-260912.json) and
[qualification-t1-260912b.json](../runs/manifests/qualification-t1-260912b.json).
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
an executed action. Public summaries and stdout aggregate **search only**. All-partition and
anchor/sealed standing metrics remain in oracle-side
`behavior-i<iteration>.json`. A3 sealed measurement counts and private audit
counts also remain oracle-side; public state stores only private references. Ordinary seed
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

## Qualification variant t1-260912b (2026-09-12)

The leader authorized all eight arms, T=1, seed=1, on the first six search,
three anchor, and three sealed tasks in split file order. The variant manifest
is `runs/manifests/qualification-t1-260912b.json`, with the parent manifest's
file hash recorded. Qualification's ceiling is USD 60 inside the unchanged
pre-pilot USD 120 allocation. The original USD 30 estimate is retained, so the
existing 1.5-times-estimate guard is conservatively binding at USD 45.
Harbor concurrency is three and admission pauses below 6 GiB MemAvailable.

Entry bundles under `logs/evolution/qualification-t1-260912b/entry/` retain
artifact hashes for P1.8, P1.9, and P1.11. The exact frozen PREREG was extracted
from commit `b59a1e8`. R8b did not have a `final_verification.json` artifact;
the bundle includes a new offline inventory derived from its existing final
validation report and completed-run resume verification. Historical unknown
charges and the provider-cache limitation remain disclosed.

The new launcher option `--allow-pending-review` applies only to qualification,
records the deviation in the launch manifest and PREREG deviation log, and
leaves strict `--check` reporting pending R10. It bypasses no other gate.
Qualification results must be discarded for an isolation- or label-blocking
R10 finding. The normal launcher emits public and oracle-side Markdown reports
and checks private trial references and evolver boundary records.

The resumed leader decision authorizes qualification-only k=min(10, search
tasks)=6, with G=3 and N=3 unchanged. `QualificationA3Round` in
`evolution/pilot.py` retains the existing difficulty, embedding, DPP and ranking
procedure, using six-task preference denominators. Pilot A3 remains k=10 on
18 search tasks. The deviation is recorded as QUAL-CORESET before launch.


Resumed preparation verification: 63 focused tests passed, including a mocked
six-task A3 round with positive-gain acceptance and completed replay without
new trials; full suite 691 passed and six skipped. Strict variant preflight
passes every gate except R10. Launch uses the logged QUAL-R10 exception and
runs under the existing systemd unit; progress is archived under
`logs/evolution/qualification-t1-260912b/`. No CLI model calls were used.


## R10 corrections and qualification revision (2026-09-12)

R10-03/R10-11 replace filesystem snapshot grading with verification in the
original live task container. There is no Docker pause/commit, replacement
PID/network namespace, or grader-initiated killing of the live service.
A controller-only helper starts from the immutable original task image and
uses detached bind-mount handles to temporarily cover `/bin`, `/usr`, `/lib*`,
`/sbin`, `/etc/ld.so*`, and original-image declared interpreter directories
read-only. Original runtime aliases are checked before mounting. A planted
`/etc/ld.so.preload` is covered with an empty read-only file. The verifier
runs through `env -i` and `bash --noprofile --norc`; startup/environment hooks
and solver PATH overrides are excluded. HOME and verifier Python package
installation directories are freshly allocated under the hidden tmpfs.

Before hidden tests are uploaded, the helper enumerates solver processes.
Processes owning listening TCP/UDP/Unix sockets, or matching an original-image
`nvh.verifier.service_pattern` label, remain running and are logged as
`kept_services`. Others receive SIGSTOP. Only then are hidden tests streamed
into a new root-owned mode-0700 tmpfs. Receipts use a separate new tmpfs.
Verifier descendants are killed before teardown; hidden mounts are removed
before stopped solver processes resume. Each verifier exception permits one
retry in that same live container, without a solver re-run; receipts are
cleared between attempts. Lost live state cannot be reconstructed from an
image and is excluded instead of rerunning the solver.

The supplied verifier bootstrap requires packages absent from some original
images (qemu needs curl and sshpass). These declared packages are downloaded
and extracted using the original image's package manager into a separate
controller-owned bundle, with package-content hashes in
`oracle/verifier-dependencies/*/receipt.json`. The bundle is mounted read-only
at `/tests/.deps`; PATH contains this pristine dependency bundle and pristine
image directories only. The test's apt bootstrap is satisfied by these staged
packages; its assertions and reward script remain unchanged. Python/uv
bootstrap uses fresh verifier-only directories. The Debian mirror exposed an
expired security index and a missing binary; staging records the signed base
release package versions and disables the index expiry check. This bootstrap
accommodation is recorded explicitly in the PREREG deviation log.

**Residual limitation:** a root solver shares the container with verification,
and kept services remain untrusted live code. They can attempt to observe or
modify verifier activity, retain open file descriptors, or evolve task state
between retries. This is not a complete isolation guarantee against root.
The helper disables dumpability; runtime audits record kept/stopped PIDs,
read-only mounts, sampled service descriptors into tests/results, a hidden
canary, and a bounded canary-copy scan of regular files under `/app`, `/tmp`,
and `/root` (5 MiB/file, 128 MiB total). Audit truncation is explicit. A detected
copy, sensitive descriptor, missing teardown, or cleanup error fails grading
and qualification. Transient accesses between samples or unscanned files
remain possible; absence of a canary hit is not proof that no access occurred.

R10-02 separates grader status from trusted solver termination. After a normal
finish, verifier failure after its retry produces `oracle=null`,
`excluded=true`, and `reason=exclusion_reason="grader_failure"`. Standing
metrics count grader exclusions separately. Genuine solver failures retain
L1' failure labels. Grader exception text never becomes judge/evolver
termination evidence; excluded attempts never reach their scoring/feedback
queues. R10-04 publishes search-only standing metrics and keeps sealed/A3
measurement counts and all-partition audit counts private.

R10-05 makes excluded A3 samples and fixed references ineligible for ranking
and diagnosis. An excluded replicate-zero reference is retained as an
ineligible identity; no later sample silently replaces it. R10-07 resolves
C-TTS selector ties by replicate ID before stable rollout ID. R10-08 adds
shared memory hysteresis: pause below 6 GiB, resume strictly above 8 GiB,
with qualification Harbor concurrency capped at three. The 8 GiB threshold
is the user-authorized departure from the frozen body's 10 GiB.

R10-06 is recorded as an exact schedule deviation, preserving the implemented
fresh baseline and smoke batches, conditional confirmation, absent anchor
monitoring under `improve`, and absent separate control t=0 checkpoint.
Qualification remains all eight arms, T=1, one seed, 6/3/3, A3 k=6 and a $60
hard guard. The discarded pre-R10 artifacts are retained under
`oracle/qualification-t1-260912b/discarded-before-r10`; their original charges
remain in the guard. The first corrected attempt was also drained and archived after concurrent
W13 reporting-source drift and a missing helper-interpreter setup error.
Revision `r10-live-v3-isolated` prevents rollout identity reuse. Its immutable
source tree is `oracle/qualification-t1-260912b/controller-r10-live-v5`; the
launcher verifies and re-execs that source before checking or dispatching.
The intermediate v2 source copy placed workspace links in the public run tree;
it was drained and archived privately. The final public-tree audit rejects
links into oracle/ or .env. The final collector also preserves grader exclusions
when they occur on an API-infrastructure replacement attempt.
The copied code writes through workspace output links to the standard paths.
All twelve subset images passed unpaid runtime setup checks after adding a
separately staged helper interpreter for images without Python. C-TTS runs
independent preassigned replicates concurrently within the same Harbor limit,
with ordered collection and unchanged allocation/selection.
R10-09's A3 H5 noise estimate remains unavailable; no numeric claim is made.

Adversarial regression results are in `logs/r10-controls/tests.txt` and the
three control JSON files. Both deliberately failing small Docker tasks return
reward 0: planted `/etc/profile.d`, modified bash and PATH-shadow Python do
not execute; the polling background reader obtains no hidden canary content.
The full qemu-alpine-ssh supplied verifier returns reward 1 for the reference
solution's running QEMU guest, with QEMU recorded as a kept listening service.
All eleven R10 tests, including these three Docker controls and a freshly
constructed reference QEMU guest, passed together in 78.03 seconds
(`logs/r10-controls/tests-v2.txt`). Project-wide `uv run pytest -q` passes:
707 passed, 9 skipped in 66.84 seconds (`logs/r10-pytest-v5-final.txt`). The real
Docker tests are explicit opt-in checks and are among the default skips.

The qualification planning estimate is $40 so that the existing 150% guard
rule enforces the explicitly authorized $60 ceiling (the former $30 estimate
would stop at $45). Start time, deadline, ceiling and all prior charges remain
unchanged; this alignment is logged in PREREG and
`logs/r10-guard-alignment.json`. Qualification execution and final gate evidence
will be recorded below after completion.

Admission continuation: v3 was paused because its global limit counted W13 workers against the qualification limit of three, starving qualification despite available memory. The v4 source changes only admission and worker scoping: three qualification workers, shared 6/8 GiB memory hysteresis, and a seven-task host ceiling for qualification plus up to four calibration workers. The three queued workers had never created Harbor jobs; they return to pending with unchanged identities. Completed initial measurements are retained without inspecting outcome values. The old source, manifests and state/configuration hashes are archived privately in `oracle/qualification-t1-260912b/admission-continuation-v3`; the migration and previous input hashes are recorded in PREREG. A regression holds four calibration slots while admitting exactly three qualification workers and checks shared memory hysteresis.

The first ordinary evolver proposal hit an infrastructure scope collision: twenty discarded-run calls and four current calls shared a 24-call counter. V5 namespaces ordinary evolver scopes by qualification revision. Only the four current request scopes are migrated, with the prior journal and guard archived in `oracle/qualification-t1-260912b/scope-continuation-v4`; every phase charge is preserved. The interrupted session continues through audited response/observation replay within its original 24 calls and $5 limit. Existing recovery uses a 300-second API timeout; that exact difference is in the appended deviation log. No completed action is rerun and no hidden outcome was consulted. The regression confirms independent revision call caps while retaining historical spending.

The provider-capacity interruption was reconciled on continuation at 07:27 UTC.
Both active manifests' self-hashes and every pinned input match the immutable
v5 source (`logs/qualification-t1-260912b-frozen-resume-check.json`). The A0
runner and interrupted worker leases were free. Only the two containers for
`nvhe-0b9ef8757d758322be218a43`, identified by their qualification artifact
mounts and exact trial identity, were removed; W13 containers were untouched.
All 53 completed rollout records were retained. The launcher `--resume`
path recorded the incomplete solver and its single policy-authorized clean
infrastructure replacement, `nvhe-e1e24093154660108de2665f`; no completed
rollout was dispatched again. Reconciliation and the preserved identities are
in `logs/qualification-t1-260912b-resume-reconciliation.json`, and the recovery
link is oracle-side under A0's `infrastructure-retries/` directory.
The unchanged guard accounted for USD 8.5147532 before continuation and retains
the USD 60 ceiling and original deadline. The resume log is
`logs/qualification-t1-260912b-run-v5-resume.txt`; periodic memory, spending,
and completed-arm observations are in the adjacent `resume-monitor.jsonl`.
Project-wide tests were rerun successfully: 707 passed, 9 skipped in 64.82
seconds (`logs/r10-pytest-resume-final.txt`).

### Qualification execution and invalidation (2026-09-12)

The resumed launcher reached the end of all eight arms and wrote the public
report at `runs/qualification-t1-260912b/report.md` and the private report at
`oracle/qualification-t1-260912b/report.md`. **The qualification did not pass and is invalidated for R10-04.**
The final expanded audit found public sealed preference counters and
all-partition rollout totals that the initial audit missed. The logged
pending-review exception requires discarding this run. Original artifacts
are archived privately under `oracle/qualification-t1-260912b/discarded-r10-v5`;
public summaries and finished SQLite checkpoints have been redacted.
Seven arms are complete; C-TTS-A3-loop is `measurement-incomplete` and
`endpoint_eligible=false`. All solver rollouts finished, but the unchanged
USD 60 guard blocked seven search preference scores for cobol-modernization.
An additional search rollout was excluded after its one infrastructure
replacement also failed; its missing preference separately prevents the
strict A3 completeness predicate from passing. No extra solver retry was
granted and no missing score was supplied or treated as zero. The final
`qualification.json` correctly records `passed=false` for the frozen manifest.

The table contains historical search results from the invalidated v5 run;
none is a valid qualification endpoint.

| Arm | Public J | Search O | Disposition |
| --- | ---: | ---: | --- |
| A0 | 0.333333 | 0.333333 | complete; rejected |
| A1 | 0.666667 | 0.333333 | complete; rejected |
| A2 | 0.666667 | 0.333333 | complete; rejected |
| A3-loop | -0.150000 | 0.333333 | complete; rejected |
| C-TTS-A0 | 0.333333 | 0.333333 | complete; control |
| C-TTS-A1 | 0.666667 | 0.333333 | complete; control |
| C-TTS-A2 | 0.866667 | 0.333333 | complete; control |
| C-TTS-A3-loop | 0.300000* | 0.333333* | incomplete; endpoint ineligible |

The starred values describe selection from the currently scored subset, so
they are provisional and must not be interpreted as a completed C-TTS
endpoint. Standing metrics in the public report cover search only. All
held-out measurements and all-partition totals are now confined to
oracle-side artifacts after redaction. No sealed outcome was consulted to change the execution.

The guard first rejected an additional reservation while accounting for
USD 59.956408. Final receipt settlement reduced the accounted total to
**USD 59.64372272**, comprising settled guard charges of USD 50.19892152 and
retained unresolved reservations of USD 9.44480120. All discarded-run and
interruption charges remain included. The cap, estimate, original deadline,
and persisted halt are unchanged. Known receipt-reported usage is
USD 49.55259942; settled guard charges also retain conservative pricing
adjustments. The source report's raw known-cost field was inflated to
USD 97.09171040 by aliased `raw_dir` path spellings. The final reports use
unique durable request-ID receipts for known costs and token counts;
`costs/qualification-t1-260912b/report_receipt_reconciliation.json` records
this correction to the historical reports. V6 also fixes the source
aggregation: receipt identity deduplicates aliased or archived raw paths,
and known durable receipt charges take precedence over raw ledger guesses.
Regression tests cover both path forms without changing guard accounting. Original launcher reports are preserved under
`oracle/qualification-t1-260912b/launcher-reports-before-final-annotation/`.
No frozen execution source, trial state, selection, or guard was changed to
make this reporting correction.

The expanded final audit detected an **R10-04 isolation/blinding anomaly**.
The initial scanner omitted `sealed_preference_status_counts` and the
all-partition `rollouts` and `physical_trials` summary fields. V6 removes
these before public persistence and export, archives full summaries privately,
and scans public JSON/JSONL plus finished SQLite stages. Search-only counts
are named explicitly. Regression tests assert all three fields stay out of
public summaries and checkpoints and remain available privately. The
post-remediation scan reports no remaining forbidden fields. No pass-label
anomaly or runtime canary/service-FD/cleanup anomaly was detected. The
same-container root/kept-service limitation and bounded audit remain as
described above; these checks do not prove complete isolation. The preserved-rollout audit found zero new solver request intents
for the 53 pre-resume completed identities. The continuation took about six
hours, exceeding the approximate four-hour estimate; the frozen per-task
control schedule and long-running trials were retained. Sampled MemAvailable
stayed at or above 25.10 GiB, with the three-worker admission limit unchanged.
Resource observations continue in
`logs/qualification-t1-260912b-continuation-monitor.jsonl` after the initial
four-hour monitor window.

Final verification: `uv run pytest -q` passed with **712 passed, 9 skipped**
in 62.25 seconds (`logs/r10-pytest-v6-final.txt`). The prior real-Docker
adversarial controls passed all 11 tests (`logs/r10-controls/tests-v2.txt`):
startup/profile/binary/PATH attacks produced no canary and reward 0; the
background reader obtained no hidden content and reward 0; the known-good
QEMU/SSH service remained live and received reward 1. Those runtime files
are unchanged in v6. The simulated verifier-crash and retry-exhaustion
regressions retain `oracle=null`, `excluded=true`, reason `grader_failure`
after a normal solver finish, with separate counts and no solver termination
or grader-status exposure to judges/evolvers.

Both manifests have been re-frozen against immutable controller v6:
`oracle/qualification-t1-260912b/controller-r10-live-v6`. Qualification
revision `r10-live-v4-export-counters` is **unexecuted**. Its embedded hash is
`65548d26e600d115be36f511210aaeea1234babd77bade73fcca9beb0c39d164`;
the pilot embedded hash is
`c4f2e76b207cc262debc17f3d8341427f05aabcc039e5aa577b643f0bfa2569c`.
Every pinned input and both manifest self-hashes verify. The prior file,
manifest and changed-input hashes are appended to PREREG's deviation log;
complete old manifests are in `runs/manifests/r10-v5/`. V6 changes only
`accounting.py`, `loop.py`, `pilot.py` and the appended deviation log relative
to v5. All A3 source is copied byte-for-byte from v5, preserving the concurrent
W13 work in the main workspace. Freeze evidence is `logs/r10-v6-freeze.json`.

Strict qualification `--check` reports `R10 re-check pending` and the
persisted budget halt (`logs/qualification-t1-260912b-check-final.txt`).
Pilot `--check` reports R10, the missing successful qualification for v6,
and REC-01's unfunded schedule (`logs/pilot-t1-260912-check-final.txt`).
There are no input-hash failures. No v6 qualification or pilot was launched;
no commit, push, or CLI model call was made.

R10-02/03/11 are implemented and regression-verified. R10-04's remaining
export defect is corrected and regression-verified in v6, but has not passed
a fresh live qualification. R10-05/07/08 are implemented; R10-06 is the exact
schedule deviation already logged; R10-10 manifests are regenerated with
previous hashes retained. R10-09 remains non-estimable Phase 3 work. R10-01's
funding constraint is an observed qualification blocker. A fresh valid
qualification requires a funding/recovery decision: the current budget
halt is retained, the v5 run is discarded, and the frozen completeness
criterion is unchanged. Finishing the eight arm controller returns does
not make this a passing gate.
