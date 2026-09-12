# Context (handoff record)

Updated: 2026-09-09, end of Phase 0 (background and resource preparation). Plan: `docs/PLAN.md` (Sections 0-8 research plan; Section 9 execution plan, 9.7 proposed amendments awaiting approval). Environment: `docs/resources.md`. Reviews: `docs/reviews.md`.

## Phase 0 interim results

| Step | Result | Artifact |
| --- | --- | --- |
| P0.1 environment survey | No provider keys initially; three subscription CLIs work as backends; Docker via `sg docker`; Harbor 0.22 local Docker. User then supplied Azure subscriptions: 7 of 9 endpoints live (GPT-5 line through 5.6 luna/sol/terra, DeepSeek V3.2/V4 Flash/Pro, Kimi K2.5/K2.6), Bearer auth on `<base>/openai/v1`, per-deployment limits recorded. `.env` written (mode 600), key file deleted. | `docs/resources.md`, `scripts/azure_probe.py`, `.env` (ignored) |
| P0.2 literature digest | Ten papers digested with section references; 21 plan deltas; 12 PREREG parameter blocks. Independent review R2: substantially source-faithful, 10 major + 7 minor corrections; all 17 applied by correction pass W1b (correction log appended to the digest). Most consequential: A3 as written is not RHO's recipe (coreset 10, 3 re-solves, 3 proposals, fixed first-rollout baseline, one round); SIGIL 5.2/5.7 do not exist (v2); A2 measures procedural compliance, a different construct; a compute-matched control is missing. | `docs/background.md`, `docs/reviews.md` R2 |
| P0.3 infrastructure | Seed ReAct Harbor agent (shell + file tools, step cap, no retries/planning), CLI backends (Claude/Codex/Copilot) and `openai_api` backend, process-safe cost ledger with priced records, cost report, smoke launcher; 51 provider-free tests pass. Smoke on local Docker: 19 trials, all with verifier rewards reconciled to ledger records. Independent review R3: smoke valid and seed minimal; its one blocking claim (redacted auth header) was a reviewer artifact; 9 valid findings on isolation enforcement, accounting robustness, failure semantics, and resumability are Phase 1 tasks P1.6, P1.9, P1.11. | `harness/`, `scripts/`, `tests/`, `docs/infrastructure.md`, `docs/smoke_run.md`, `costs/summary.md` |
| P0.4 GDPevo | 12 groups (finance 008-011, healthcare 013-016, legal 017-020) = 120 tasks; all 120 reference answers score 1.0 offline with standalone Python graders, deterministic on repeat; 12 business services start locally without Docker; train-only `POST /api/judge` route is an oracle leak that must be disabled outside the agent. Independent review R1: conditional GO confirmed as permission to build the adapter, with 5 findings (1 fixed, 4 to Phase 1). | `docs/gdpevo_assessment.md`, `scripts/gdpevo/`, `docs/reviews.md` R1 |

Smoke-run numbers (six easy TB2 tasks, concurrency 4, single attempt):

| Task model | Pass | Mean USD / rollout | Mean agent s / rollout | Batch wall s |
| --- | ---: | ---: | ---: | ---: |
| gpt-5-mini (low) | 3/6 | 0.012 | 47 | 171 |
| gpt-5.1 | 4/6 | 0.072 | 37 | 147 |
| gpt-5.6-luna | 4/6 | 0.004 | 13 | 64 |

No 429s or Harbor timeouts at concurrency 4. Luna finished two tasks without any container action (claimed it had no tools), so its score is not trusted yet. Recommendation carried into Phase 1: gpt-5-mini low reasoning as the baseline, with a stratified 30-task pilot before freezing the task model (target seed pass rate 20-40%).

Phase 0 exit criteria: (a) TB2 task end-to-end with oracle result and ledger entry: met. (b) GDPevo grader runs locally on healthcare and legal groups: met. (c) background digest reviewed with contradictions resolved: met (R2 findings applied by W1b).

## Costs, Phase 0

| Resource | Usage |
| --- | --- |
| Azure OpenAI API (smoke runs) | USD 0.54 (19 rollouts + parser validation), priced from `scripts/prices.json` (list prices, unverified against invoice) |
| Claude Code (Haiku probes) | USD 0.017 API-equivalent |
| Copilot CLI | premium requests: probes 3 + gemini 14 + reviews 2 (R1, R2) + R3 pending + W2's own review calls 2 |
| Codex CLI (gpt-6-astra) | W2 2.68M in (2.56M cached) / 30k out; W3 4.41M in (4.27M cached) / 25k out; W2b 4.57M in (4.43M cached) / 38k out; W1/W1b via plugin (about 25 + 20 min), token usage not captured by the plugin log |
| Wall-clock | Phase 0 leader session about 2.5 h; workers ran concurrently (W1 ~25 min, W2 ~45 min, W3 ~20 min, W2b ~35 min) |

Ledger: `costs/ledger.jsonl` (ignored) rolled up in `costs/summary.md`. Known gap (R3 finding 3): about 100 tiny Azure inventory probe calls from `scripts/azure_probe.py` are outside the ledger; usage is in `logs/azure_probe.json` and will be backfilled in P1.11a.

## Research questions of this phase and answers

1. Does any paper contradict Sections 1-7? Yes on definitions, not on the core design: A3 recipe, oracle scale mixing, missing SIGIL sections, A2 construct, absent compute control, monotonicity undefined. Proposed amendments A1-A10 in `docs/PLAN.md` 9.7.
2. Per-rollout cost, latency, safe concurrency: about 1-7 cents and 40-90 s per rollout depending on model; concurrency 4 clean; higher concurrency untested.
3. Is GDPevo usable offline with a deterministic oracle? Yes for grading; formal T2 runs need our own filtered images, a route-allowlisted service gateway, an authenticated API descriptor, and a frozen binary-score contract.

## Decisions needed from the user

(2026-09-10 update: items 1-3 below were answered in `docs/decisions-260910.md`; the new open items are AD1-AD9 in `docs/decisions-260910-addendum.md`, drafted after reviews R4/R5.)

1. Approve, modify, or reject amendments A1-A10 (`docs/PLAN.md` 9.7).
2. Confirm the task-model baseline (gpt-5-mini low) or ask for luna/gpt-5.1 in the stratified pilot.
3. Confirm the pilot budget guard (150% of estimate: Phase 2 pilot estimated at about USD 60-150 API and 3-5 days wall-clock at concurrency 4-8).

## Phase 1 plan (pilot infrastructure)

Owner: Codex workers unless noted; leader checks alignment; Copilot reviews each artifact.

| ID | Task | Acceptance |
| --- | --- | --- |
| P1.1 | Stratified TB2 split: 30 tasks by dataset difficulty metadata, fixed seed, 18 search / 6 anchor / 6 sealed; pinned dataset commit. | `data/tb2_split.json` committed with seed and strata counts. |
| P1.2 | Seed-harness pilot on the 30 tasks with gpt-5-mini (and luna, gpt-5.1 as candidates), avg@2, concurrency 4 and 8. | Seed pass rate per model; choice recorded; concurrency limit measured. |
| P1.3 | Evolver loop: API-driven ReAct evolver (DeepSeek-V4-Pro) editing the harness in a per-arm container workspace; one prompt template with only the score-source paragraph varying; candidate validity checks (import, smoke task, forbidden-reference scan). | Two candidates per iteration produced and evaluated end-to-end on the search set for A0. |
| P1.4 | Judges: A1 outcome judge and A2 process judge (Section 4.1 rubric) with DeepSeek-V4-Flash; hard-coded input schema; rationale archived; A4 mixture; judge-variance control (5 repeats on fixed seed trajectories) to set tau. | Judge scores for seed trajectories with variance estimate. |
| P1.5 | A3 self-preference per RHO's native recipe (A1 amendment), using the task model. | One RHO round reproduced on the search set. |
| P1.6 | Isolation protocol: oracle/ never mounted; canary files; trace filters (TB2 and GDPevo patterns); per-arm workspaces; cache isolation via per-arm user ids; Section 5 acceptance matrix executed and logged. | All acceptance checks pass and are recorded. |
| P1.7 | Sealed-anchor acceptance (Section 7) implementation with tau and epsilon parameters. | Unit-tested; anchor results never appear in evolver context. |
| P1.8 | `PREREG.md`: H1-H5 with operational thresholds, oracle binary definition, failure policies, budgets, analysis plan; committed before any pilot iteration. | Reviewed by Copilot; committed. |
| P1.9 | Orchestration and monitoring: stable experiment/trial ids, durable queue with resume at trial boundary (no undisclosed retries of experimental rollouts), shared per-endpoint RPM/TPM admission control, per-iteration timing/cost/pass-rate log, budget guard halting at 150% of the phase estimate. | Kill-and-resume test passes; dashboard file updated by the loop. |
| P1.11 | Accounting and failure-semantics hardening (from R3): (a) backfill probe usage; (b) request-intent records and cancellation/SIGKILL-safe ledger with restart reconciliation; (c) shared accounting reducer with known/unresolved components; (d) pass = reward 1 and no agent timeout, raw reward kept separately, failure fixtures; (e) provider usage fixtures (DeepSeek, Kimi) through the full pipeline. | Tests for each; `costs/summary.md` reports known and unresolved separately. |
| P1.10 | GDPevo adapter groundwork (from R1): filtered solver and service images, route allowlist, authenticated API descriptor, binary-score contract, valid-but-wrong grader controls, end-to-end seed rollout in one healthcare and one legal group. | One rollout per domain with oracle result and ledger entry. |

Phase 2 (pilot on T1) starts only after P1.8 is committed and P1.6, P1.9, and P1.11 pass. Experimental roles use the API backends only (R3 finding 8).

## Phase 1 progress log (2026-09-10)

- User decisions in `docs/decisions-260910.md`; task-model choice overridden in chat in favour of gpt-5.6-luna pending the P1.2 experiment (no gpt-5.5-mini deployment exists on Azure).
- W4 done: amendments A1-A10 applied to PLAN Sections 1-8; `PREREG.md` drafted (D-draft marks on statistical details). Open conflict for the user: A3-loop uses 3 proposals per iteration (RHO native) while Section 2 fixes 2 candidates for other arms; default is to keep both and report budgets per arm.
- W7 done (P1.10): GDPevo adapter with filtered staging, judge-free service images behind a route-allowlist gateway, isolated solver container, grader-v1 (TG015/TG018 patched, tree hash), binary oracle rule; 7/7 acceptance checks, 174/174 boundary probes; one healthcare and one legal seed rollout end-to-end (both scored 0); USD 0.017; 250 tests. Review R5 pending.
- W5c done (P1.1 + P1.2): `data/tb2_split.json` (18/6/6, seed recorded, dataset pinned); `docs/calibration.md`: mini/json 8.3% pass (0% no-action, USD 0.006/rollout); mini/native 8.3% (3.3%); luna/json 23.3% (11.7% no-action, USD 0.011); terra/json 28.3% (1.7% no-action, USD 0.124, 68 agent-s); luna/native and terra/native rejected with HTTP 400 on all attempts. Only terra/json passes every gate; terra's 5 pp edge over luna has a paired-bootstrap 95% interval of -5.0 to +16.7 pp. Follow-up W5d: mini/json and luna/json at medium reasoning. Review R7 then showed that under PREREG's definitions terra's no-action rate is 15% (mostly first-response token exhaustion) and that the pass label needs the A9 tool-failure meaning ratified (AD10); no low-effort configuration is eligible under the strict reading. Native rejection cause: function tools with reasoning_effort unsupported on chat/completions (Responses API would work) - AD11. W5d done: mini/json medium 13.3% (0% no-action, USD 0.012); luna/json medium 33.3% (11.7% no-action, USD 0.016) - luna's no-action behaviour persists at medium reasoning. W5e done: under corrected contracts no configuration qualifies (L1: terra 28.3% pass but 15% no-action, mostly token exhaustion; luna 15.0% with persistent inability claims; mini-medium 13.3% with 18 exhaustions). W5f done (8,192 allowance): terra-low 30.0% L1 pass, 4/60 no-action all API timeouts, USD 0.112/rollout; mini-medium 11.7%, 2/60; luna-medium 8.3%, 9/60 inability claims. Only terra-low-8k is eligible once AD10-AD13 are ratified; AD1 revised to propose it, AD14 proposes the pilot budget USD 400 (guard USD 600 / 5 days). Decision AD1 awaits W5d + W5e; pilot budget re-derived afterwards.
- W8c done (P1.4 + P1.7): judges, sanitizer, async queue, acceptance rule; `docs/judges.md`: tau = 0.02184 (A1) / 0.02257 (A2) from 5 repeats on the fixed seed set; seed TPR/FPR at 0.5: A1 93.3%/26.3%, A2 66.7%/32.1%; Kimi A1 66.7%/20.0%, A2 100%/80.0% with 7 failures; DeepSeek about USD 0.0006 (A1) / 0.0032 (A2) per call; 309 tests. Open: two task prompts over-redacted by the sanitizer, 7 missing verifier labels, observation truncation. Review R6 launched.
- Tool protocol: native function calling is rejected (HTTP 400) by the gpt-5.6 luna/terra deployments; JSON-in-text protocol fixed for all models and arms.
- W9 done (P1.3, P1.6, C-TTS): `docs/evolution.md`; per-arm immutable candidate roots, containerized evolver workspace (candidate rw, feedback ro, no oracle/.env/host), one prompt template, forbidden-reference and canary validation, Harbor evaluation, async judging, sealed-anchor acceptance, durable resume, budget guards. Two real iterations: A0 accepted (+0.056 confirmed, anchors 1/6 to 2/6), A1 rejected (judge gain -0.083). USD 6.69. Open: ratification-dependent settings, isolation/cache/regrade items listed in the doc. Review R8 launched; W8d launched for R6 fixes.
- Incident: the Claude session's low-memory watchdog killed all background tasks twice (MemFree 2-3 GB from page cache while MemAvailable stayed above 20 GB; no kernel OOM). Orphaned Harbor containers were removed. Long workers now run detached as systemd user units (`nvh-*`) with Harbor at concurrency 4 and a MemAvailable-based admission guard; see memory note.

## State at 2026-09-11 (end of autonomous Phase 1 work)

All Phase 1 engineering is implemented and independently reviewed (R1-R9; dispositions in `docs/reviews.md`): seed harness and backends, cost ledger, stratified split and calibration (docs/calibration.md), judge layer with evidence contract v3 and terra tau (docs/judges.md), isolated evolution loop with fail-closed pilot launcher (docs/evolution.md), A3-native/A3-loop/C-TTS(A3) (docs/a3.md), GDPevo adapter with T2 acceptance matrix (docs/gdpevo_adapter.md). Tests: 581 passing.

Ratified 2026-09-12 (`docs/decisions-260912.md`). PREREG frozen pending R10 (W12); calibration recomputed under L1' (W5g). Loop updated for the ratified contracts (W9c); manifests written; R10 launched. Open budget question REC-01: the pilot schedule is 13,872 rollouts (about USD 1,554 solver) versus the USD 650 guard; the qualification manifest as written (all arms, full split) is USD 145 versus its USD 30 cap and will be trimmed to the 6/3/3 subset as a logged deviation. After ratification, in order: (1) PREREG/PLAN reconciliation and freeze (Codex, R4 items 3-12); (2) clean A3-native calibration with Azure embeddings (USD 40-60); (3) final controller qualification run with the frozen manifest (T = 1, all arms, about USD 30); (4) Phase 2 pilot (estimate USD 400 if terra, guard USD 600 / 5 days per AD14).

Costs to date (API, conservative): smoke 0.54; calibration 8.85 + 1.70 + 9.87; judges 3.24 (+ earlier mini calibration about 4); evolution runs 6.69 + 14.49; A3 19.74; GDPevo 0.02 + 9.80; total about USD 90. Codex usage: about 200M input tokens (almost all cached) and 1.4M output tokens across W1-W11; Copilot: 9 reviews plus probes.


## A3-native clean calibration worker (2026-09-12)

Active task and handoff: `runs/a3-native-260912/context.md`. Own $60 cap; projected $48.95493134 before dispatch. Only A3 module/test code is edited. Historical run stays immutable. Raw oracle data stays under oracle/.
