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
