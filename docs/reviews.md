# Review log

Independent reviews (Copilot CLI, `gpt-6-astra`, `--no-custom-instructions`) of interim artifacts, with the leader's disposition of each finding. Raw review text is under `logs/review/` (git-ignored); this file is the durable record.

## R1 - docs/gdpevo_assessment.md (2026-09-09)

Reviewer verdict: T2 may proceed to Phase 1 integration work; the conditional GO is permission to build the adapter, not approval for judge-driven T2 runs. Evidence for task counts, read-only HTTP environments, local deterministic rule grading, and the recorded controls was confirmed against the checkout.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking (for formal runs) | Proposed isolation layout does not close every PLAN Section 5 path: uniform evolver-prompt check, per-arm workspaces, and cache isolation unspecified; upstream env images `COPY . /app` so oracle material is inside the solver image; PLAN's `test_*.py` trace filter misses GDPevo's `eval.py`, `evaluator.py`, reference JSON; shared business data means "sealed" test records are visible in the environment. | Phase 1 task T2-ISO: build our own solver and service images from filtered sources; seven-row Section 5 acceptance matrix exercised at the tool boundary; GDPevo-specific trace filter patterns; decide and document whether held-out business records are intentionally shared. PLAN amendment A5 proposed (Section 5 filter list). |
| 2 | major | Graders have full-credit blind spots: string-containment contact check (TG015) and set-deduplicated fee rows with fixed expected total (TG018). | Phase 1 task T2-ORACLE: add valid-but-wrong controls to the oracle characterization; version a hardened grader separately and freeze before runs; always report native score alongside. |
| 3 | major | "Oracle pass rate" needs a frozen binary definition (normalized_score if present else score; valid submission and valid grading; abs(s-1) <= tolerance; fixed denominator); T2 is artifact/rule acceptance, not process correctness as PLAN Section 3 states. | PREREG item; PLAN amendment A3 proposed (Section 3 wording for T2 and binary/partial-credit reporting). |
| 4 | major | Solver interface underspecified: authenticated SQL (`X-Task-Token`, JSON `query`), `X-API-Key`, and the upstream `environment_access.md` descriptor are missing from staged prompts; helper probes only unauthenticated GETs from the host. | Phase 1 task T2-ADAPTER: pinned solver image, separate service containers, read-only input mount, filtered API descriptor with credentials and request shapes, end-to-end attempt in one healthcare and one legal group through the real boundary. |
| 5 | minor | `audit_evaluators.py` evaluated the `score` default eagerly, rejecting normalized-only results. | Fixed by the leader on 2026-09-09 (explicit conditional, optional raw score). Regression test for normalized-only results added to Phase 1 task list. |

## R2 - docs/background.md (2026-09-09)

Reviewer verdict: the digest is substantially source-faithful; no blocking source-access problem; several factual attributions and protocol omissions need correction before PREREG relies on it. Ten major and seven minor findings, plus a table of confirmed source claims and a row-by-row disposition of the digest's delta table.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | major | RHO's baseline-selection rule is specified (Algorithm 1 step 5: first original-harness group rollout, fixed across candidates); digest called it unspecified. | Correction pass W1b; amendment A1 updated to name the rule. |
| 2 | major | Harness Updating's Haiku gains attached to the wrong benchmarks. | W1b corrects. |
| 3 | major | HASE frozen-policy ablation attributed to the wrong task. | W1b corrects. |
| 4 | major | Meta-Harness did not discover the completion checklist as described. | W1b corrects. |
| 5 | major | Phantom Guardrails' fabrication recast as exploitation; Equation 3 measures unsupported guard inclusion. | W1b corrects; Section 6.2 sublabels will use Phantom's own definition. |
| 6 | major | The "missing SEAL Section 7" dependency is not established; the plan's wording most likely refers to its own Section 7. SIGIL 5.2/5.7 gap is real. | Amendment A2 rewritten; W1b withdraws the SEAL claim. |
| 7 | major | A2 (process judge) measures procedural compliance, a different construct, not just a different scale; affects interpretation of A2's gap and FPR. | Amendment A4 extended; PREREG item. |
| 8 | major | Rethinking's operational equal-budget protocol incompletely recorded (repaired TB2.1, 89 tasks, m = 1, K = 5, 45/10/34 split, infrastructure exceptions scored 0). | W1b records; amendment A6 cites the specifics. |
| 9 | major | GDPevo oracle and isolation need more than the weighted-score summary. | Covered by R1 dispositions and amendments A3/A5. |
| 10 | major | HarnessX metric (pass@2) and stopping rule overgeneralized. | W1b qualifies; PREREG defines avg@2 explicitly. |
| 11-17 | minor | MCP edit scope omits skills; adherence-rubric accounting machinery; SEAL comparison controls; Meta-Harness archive retention and final evaluation; small RHO/GDPevo details; Part B mixes discrepancy types; Harness Updating repository URL not in text. | W1b applies; Part B rows relabeled as discrepancy / extension / unverifiable. |

Confirmed and retained: HarnessX Section 4.2 pathology examples (including output-rewriting processors) are real; GDPevo group IDs (healthcare 13-16, legal 17-20, finance 8-11) confirmed against Table 2; Rethinking's no-verifier averages match Table 1; Meta-Harness TB search and final evaluation use the same 89 tasks.

## R3 - Phase 0 infrastructure (harness/, scripts/, tests/, docs/smoke_run.md) (2026-09-09)

Reviewer verdict: the smoke run is a valid Phase 0 result and the default API seed satisfies the intended minimality (file/terminal actions, bounded protocol-error feedback, no planning or self-verification prompts), but the checkout is not yet a stable foundation for the evolution loop until accounting and failure semantics are reliable end-to-end and isolation is enforced rather than merely recorded.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking (claimed) | API backend sends a literal `******` Authorization header and the test enshrines it. | **Not confirmed.** `harness/openai_api.py:185` builds `Bearer {api_key}` and `tests/test_openai_api.py:46` asserts `Bearer secret-test-key`; the reviewer's Copilot session displayed its own secret redaction of the file. No change. |
| 2 | major | Section 5 isolation has bookkeeping hooks (arm, run_id, trial-local logs) but no enforceable boundary: no per-arm candidate roots, mount allowlists, forbidden-reference gate, sanitized judge export, or cache partitioning. | Phase 1 tasks P1.3 and P1.6 (already planned); explicitly listed as prerequisites for any judge-driven search. |
| 3 | major | `scripts/azure_probe.py` made billable calls outside the ledger, so `costs/summary.md` is not exhaustive. | Phase 1 task P1.11a: backfill probe usage from `logs/azure_probe.json` with provenance; route any future probes through the ledger. Noted in context.md costs. |
| 4 | major | Cancellation and SIGKILL can lose ledger records; CLI timeouts skip usage parsing. | Phase 1 task P1.11b: request-intent record before dispatch, completion/failure events keyed by call id, restart reconciliation. |
| 5 | major | Reports sum only `cost_usd`, dropping known partial costs after ambiguous 5xx; reprice script discards ambiguity. | Phase 1 task P1.11c: shared accounting reducer with known/unresolved components; 503-then-200 fixture through report and repair. |
| 6 | major | Failure reporting is happy-path-only; pass counted as `reward == 1` even after an agent timeout, contrary to Section 8. | PREREG item and Phase 1 task P1.11d: pass = reward 1 and no agent timeout; raw verifier reward kept separately; fixtures for timeout, missing response artifact, null price. |
| 7 | major | Launcher not resumable or globally budgeted; no shared RPM/TPM admission control; 150% stop rule not implemented. | Phase 1 task P1.9 (expanded): stable trial ids, durable queue, resume at trial boundary, shared endpoint limits, phase-wide budget reservations. |
| 8 | major | Codex CLI backend is not tool-free and inherits the host environment after `.env` is loaded. | Policy recorded in PLAN 9.1 and docs/infrastructure.md: experimental roles use the API path only; CLI backends are for probes and the optional Copilot/Claude cross-judges with a minimal environment. |
| 9 | minor | Test coverage weak at pilot contracts; DeepSeek/Kimi usage objects lack cache/reasoning fields and will stay partly unknown. | Phase 1 task P1.11e: provider fixtures replayed through accounting and reporting; keep unknown counters explicit. |
| 10 | minor | gpt-5-mini recommendation is a provisional weak baseline, not a calibrated selection; luna's no-action terminations are real model behaviour, not Docker failures. | Agreed; P1.2 stratified calibration before freezing the task model; track no-action termination as a metric; no model-specific rescue prompts. |

## R4 - PLAN Sections 1-8 amendments and PREREG.md draft (2026-09-10)

Reviewer verdict: A1, A2, A4-A7, A9, A10 are represented correctly (A3-native/A3-loop distinction, raw same-search gap, oracle regret, failure policies, isolation, guards); Section 9 untouched apart from the phase log. PREREG cannot be frozen merely by filling the three open rows: decision authority for the task-model override, the non-inferiority procedure, and the rollout budget must be reconciled first. Commit it as an exploratory pilot preregistration with H4 deferred.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking | A8 as written (select among mini/luna/terra with fallback rules) does not match the binding decisions file (mini frozen); the chat override is not an auditable authorization, and terra plus the cost-selection policy were never approved. | `docs/decisions-260910-addendum.md` drafted for the user to ratify; PREREG task-model row marked "pending ratification". |
| 2 | blocking | The 5 pp non-inferiority test on 6 sealed tasks x avg@2 x 2 seeds can manufacture certainty. | Addendum item: pilot is exploratory for the sufficiency claim; confirmatory non-inferiority moves to Phase 3 (40 sealed tasks). |
| 3 | blocking | Evaluation schedule (C-TTS, A3-native, promotion confirmation, variance control) not reconciled with the USD 200 estimate. | PREREG Section 7 to carry an explicit rollout-schedule table; estimate re-derived from measured P1.2/P1.4 unit costs and the first two W9 iterations. |
| 4 | major | Four edits beyond A1-A10: rubric item 5 reverse-keyed; multi-label edit classes; H5 comparator as SD; epsilon fixed at 0 (one-task allowance removed). | Addendum items for ratification; PREREG marks them [D-pending]. |
| 5 | major | Measurement schedule and missing-score estimand not fixed. | PREREG fix: J_t over rollouts with valid judge scores, missing count reported; sealed evaluation of the incumbent after each iteration. |
| 6 | major | H1 sensitivity unspecified; H2 cannot be confirmatory with two seeds. | PREREG: H2 exploratory in the pilot; minimum detectable slope stated from the variance control. |
| 7 | major | Open-item formulas mechanical only once inputs are fixed. | Accepted; inputs come from docs/calibration.md and docs/judges.md. |
| 8 | major | Remaining post-hoc choices. | Enumerate and fix in the reconciliation pass before the pilot. |
| 9 | major | Amendment clause permits redesign after observing the primary endpoint. | PREREG: amendments after pilot start are logged deviations, never silent changes. |
| 10 | major | A3's T2 pass predicate stricter than the approved tolerance. | Align PLAN and PREREG to abs(s-1) <= 1e-6 with 0 <= s <= 1. |
| 11 | major | Cross-judge interpretation conflicts between PLAN and PREREG. | Align in the reconciliation pass. |
| 12 | minor | Oracle-access wording needs narrow exceptions (A0 evidence; C-TTS(A0) selector). | Fix wording in the reconciliation pass. |

## R5 - GDPevo adapter (P1.10) (2026-09-10)

Reviewer verdict: credible adapter groundwork; file and container isolation substantially implemented; grader hardening and the two priced rollouts supported; the "7/7" headline overstates integrated Section 5 coverage; formal T2 runs remain blocked by experiment wiring, acceptance evidence, failure/denominator handling, and accounting lifecycle.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking (for T2 runs) | Several acceptance rows use fixtures or helper assertions instead of the integrated boundary (synthetic traces, argument-level judge isolation, template-only prompt uniformity, flag-level anchor isolation, no cross-arm write attempts). | Phase 3 prep task T2-ACCEPT: integrated checks through real rollouts, serialized judge payloads, dispatched prompts, and real workspaces; the P1.6 matrix built by W9 for T1 is reused. |
| 2 | blocking (for T2 runs) | Runner hard-codes train/A0/iteration 0 and a USD 10 budget; judge queue discovers only Harbor's layout, not GDPevo's trajectory.jsonl. | Phase 3 prep task T2-WIRE: experiment-driven runner; judge queue accepts a manifest of trajectories independent of layout. |
| 3 | major | Fixed-denominator accounting lost on failure paths (oversized answers, grader launch failures); A9 policies unimplemented in the runner. | T2-WIRE; shared failure-policy module with P1.11d. |
| 4 | major | Accounting not crash-complete. | P1.11b (already scheduled). |
| 5 | major | TG019 exposes hidden annotation columns (train/test identifiers) through SQL. | T2-ISO: column filtering at staging for TG019 and an audit of every group's database for hidden annotation columns; required before any T2 run. |
| 6 | minor | Transport retries (max_retries = 1) not a zero-retry guarantee. | Document: transport retries follow A9; the seed loop itself has none. |
| 7 | minor | Scores slightly above 1 pass. | Enforce 0 <= s <= 1 before the tolerance; boundary tests. |
| 8 | minor | Wrong-control gate omits native grader-error validation. | Require a valid native run plus score 1. |

## R6 - Judge layer (P1.4 / P1.7) (2026-09-10)

Reviewer verdict: judge schemas, rubric arithmetic, aggregate-score tau, and the A1/A2 sealed-anchor predicate are sound foundations; not ready for pilot sign-off until label handling, sanitization, and the evidence/dispatch/recovery contracts are reconciled. Preserve the calibration as historical evidence; correct labels without re-judging.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking | Seven "missing verifier labels" are in-rollout tool failures (30 s command timeouts) and must stay in the denominator as failures per A9. | W8d: relabel without re-judging; recompute TPR/FPR; docs/judges.md updated. |
| 2 | blocking | Whole-field sanitization erases legitimate task instructions when they mention a filtered filename (for example "You can run /app/test_outputs.py to verify"). | W8d: redact only file contents and test outputs, never the instruction text; keep a redaction log; regression test on the affected traces. |
| 3 | major | Exported evidence truncates observations to 12,000 characters, contrary to PREREG's complete-trace contract; termination status incomplete. | W8d: versioned evidence contract with complete sanitized observations (or a preregistered, documented cap) and explicit termination fields; PREREG aligned. |
| 4 | major | The P1.3 composition reserves endpoint quota twice (queue and transport) and does not stream judgments. | W9 follow-up: single limiter ownership; streaming ingestion. |
| 5 | major | P1.3's transport prepends a random "Context identifier" system message, changing the frozen judge prompt after its hash and archive. | W9 follow-up: cache-isolation identifier moves to the API `user` field or a hashed, archived field; prompt hash covers the wire prompt. |
| 6 | major | Crash accounting: backend ledger row written after dispatch; a hard kill can lose an attempted request. | P1.11b (request-intent record before dispatch). |
| 7 | major | Restart idempotency keyed on prompt hash, not frozen rollout identity. | W8d: primary key on rollout id + judge + repeat + evidence version; conflicts reported, not re-enqueued. |
| 8 | minor | Observed-pair cross-judge rates are not unbiased (availability differs). | Report with counts and a sensitivity bound. |
| 9 | minor | F-agree substitutes primary tau when cross tau is missing; SearchEvaluation restricts scores to [0,1] while A3 uses signed preference. | W8d: require explicit cross tau; widen the score type per A4's commensurability rule. |

## R7 - Task-model calibration (P1.1 / P1.2) (2026-09-10)

Reviewer verdict: the split and the common weak seed are credible; slot and cost accounting reconcile; but two measurement-contract errors invalidate terra's eligibility (no-action 15% under PREREG's definition; definite tool failures reduce every configuration below 15% under a strict reading of A9), so the task model cannot be frozen on this evidence. Native rejections establish API incompatibility of one configuration, not native tools in general.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | blocking | No-action metric counted only `finish` records; PREREG counts completed or failed attempts without an executed action. Corrected: mini/json 1/60, mini/native 3/60 (5.0%, fails "strictly below 5%"), luna/json 7/60 (11.7%), terra/json 9/60 (15.0%); eight terra cases are first-response token exhaustion (finish_reason length at the 4,096 reasoning+output allowance), not "no tools" claims. | W5e re-analysis: count all finalized terminations and separate reasons (normal finish, no-tools claim, token/step exhaustion, protocol failure); record execution-start events. Addendum AD12 asks whether the shared completion allowance is a seed parameter to revisit. |
| 2 | blocking | Pass label used "reward 1 and no trial exception"; A9 says in-rollout tool failures are failures. Strict reading (any nonzero command exit) gives terra 11.7%, luna 10.0%, mini 5-6.7%. | The intended meaning of "tool failure" must be ratified (AD10): executor-level failures (timeout, exception, protocol error) versus any nonzero exit of an agent-issued command. W5e reports both labels; raw rewards preserved. |
| 3 | major | Native rejection cause is established for luna only ("function tools with reasoning_effort unsupported in /v1/chat/completions; use /v1/responses or effort none"); terra's cause inferred because error bodies were discarded; JSON-for-all is a design choice, not a necessity. | Addendum AD11: ratify JSON as the deliberate model-agnostic protocol for this study, or fund a Responses-API native adapter and re-calibrate. Backend to archive sanitized error bodies (W5e). |
| 4 | major | AD1 and PREREG specify different selection rules; AD1 pending. | Reconciled in the PREREG pass after ratification. |
| 5 | major | Accounting reconciles (360 slots, 328 rewards, USD 8.85 known); two operator replacements need an explicit exception to the single-retry rule; medium-reasoning runs share phase tag P1.2 and would mix cohorts in regenerated aggregates. | W5e: explicit run/experiment/budget manifest; cohort-scoped aggregates; replacement lineage recorded and the exception logged in PREREG deviations. |
| 6 | major | Cost comparisons descriptive only; concurrency-8 evidence exists for mini only; terra at 2,800 rollouts is about USD 348 for solver calls alone. | Budget re-derivation after the model decision; capacity test for the selected model at the pilot concurrency. |
| 7 | minor | Split sound (seed 260910, Hamilton allocation 1/10/19, partitions 18/6/6); exact regeneration not independently certified. | W5e: archive the population metadata manifest and an offline comparison script. |
| 8 | minor | Seed integrity holds; open: repeat-run variability, sampling defaults, reasoning-effort sensitivity, token-allowance exhaustion, Responses-native performance, selected-model concurrency. | Bounded or resolved in the freeze record. |
