# Decisions 2026-09-10 (user, after Phase 0 handoff)

Scope: PLAN 9.7 amendments A1-A10, task-model baseline, pilot budget. Apply to `docs/PLAN.md` Sections 1-8 and `PREREG.md`. Anything not listed here stays as written.

## 1. Amendments A1-A10

| ID | Decision | Binding detail |
| --- | --- | --- |
| A1 | **Approve** | A3 has two forms. (a) `A3-native`: one RHO round exactly as Algorithm 1 (coreset 10, 3 re-solves, 3 proposals, fixed first original-harness rollout as pairwise baseline). Run once in P1.5 as the calibration anchor; it is the number we cite when we say "RHO reproduced". (b) `A3-loop`: the same recipe applied once per iteration for T iterations, used in the pilot arms. Label them separately in every figure; never present A3-loop as RHO. |
| A2 | **Approve** | Editorial. Fix both citations; SIGIL references become 2.1-2.2 and 4.1. |
| A3 | **Approve, modified** | T2 = healthcare 013-016 + legal 017-020, 40 search / 40 sealed; finance 008-011 anchor pool. Binary oracle: `normalized_score` (else `score`) with `abs(s - 1) <= 1e-6`, valid JSON submission, valid grader run, fixed denominator; timeouts count in the denominator. Oracle = the **hardened grader** (blind spots TG015/TG018 patched, frozen as `grader-v1` with a hash in PREREG); the upstream native mean score is reported alongside as a secondary column. Do not drop TG015/TG018 from the task set. The valid-but-wrong controls (R1 #2) must pass before any judge-driven T2 run. |
| A4 | **Approve, simplified** | Primary $\Delta_t = J_t - O_t^{search}$ with **no seed-centering** (raw calibration). Secondary: change-from-seed, and oracle regret $O_t^{sealed}(A0) - O_t^{sealed}(A_i)$. For A3 the pairwise win-rate is not commensurable with a pass rate, so A3 is tested on oracle regret and on H2/H5 only; PREREG states this explicitly. A2 is labelled "procedural compliance" in all figure legends and its gap is interpreted as construct disagreement plus error. |
| A5 | **Approve** | Blocking for any T2 run. Build filtered solver and service images; canary-file checks replace name-based checks; the seven-row Section 5 matrix is executed at the tool boundary and its log is a Phase 2 entry criterion. Held-out business records: **shared** (document it as a property of the benchmark, not a leak — the sealed set is sealed by task text and grader, not by data). |
| A6 | **Approve** | Add control `C-TTS`: seed harness, no edits, equal rollout budget, per-task selection by the **same signal as the arm being compared** (A1's judge for A1, A2's rubric for A2, self-preference for A3, oracle for A0). Report full call/token/USD/wall per arm in one table. Rethinking's m=1, K=5 is the reference; our K is set by the matched budget. |
| A7 | **Approve** | H1 operational test: OLS slope of $\Delta_t$ on $t$, task-level paired bootstrap, 95% CI excludes 0; single-step reversals tolerated. "Judge sufficient" is a **non-inferiority** claim: $O_T^{sealed}(A_i) \ge O_T^{sealed}(A0) - 5\,\text{pp}$ with the CI lower bound above the margin. 5 pp is preregistered. |
| A8 | **Approve** | Task model per Section 2 below; the rest as in 9.1. |
| A9 | **Approve** | Fixed policies, identical across arms, in PREREG: solver timeout = fail; in-rollout tool failure = fail (agent behaviour); judge/ranker failure = retry once, then the candidate is **not accepted** and the event is logged; grader failure = retry once, then the trial is excluded with a logged count; infrastructure failure = retry once, then excluded with a logged count. Exclusions are reported per arm in the budget table. |
| A10 | **Approve** | Pin `terminal-bench@2.0` at commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c` (the smoke commit). Stratify by dataset difficulty metadata, seed fixed and committed in `data/tb2_split.json`. The six smoke tasks are eligible; do not exclude them and do not favour them. |

## 2. Task-model baseline

**`gpt-5-mini`, low reasoning, 4,096 max completion tokens, 24-step cap** — frozen for the pilot unless P1.2 shows a seed pass rate outside 15-45% on the stratified 30 (avg@2).

Why not the alternatives:
- `gpt-5.6-luna`: two of six smoke tasks ended with no container action and a claim of having no tools. That is exactly the "claim without action" behaviour the judge arms are supposed to detect in *evolved* harnesses; a task model that does it at the seed contaminates H5. Excluded from the pilot. It may return in Phase 3 as a second task-model tier only if its no-action rate on the 30-task set is below 5% with no model-specific prompt.
- `gpt-5.1`: 6x the per-rollout cost of mini with no evidence of a clearer signal, and closer to the ceiling where harness benefit shrinks (Lin et al.). Held as an optional Phase 3 sensitivity row.

Escalation rule (fixes model choice without touching strata, per A10): if mini's seed pass rate on the stratified 30 is below 15%, move to medium reasoning, same model; if above 45%, keep the seed and the split as they are and record the number — do not weaken the seed further or re-stratify. Report no-action termination rate as a standing metric for every model.

## 3. Budget and guards

Recomputed from smoke-run unit costs (mini easy-task mean USD 0.012/rollout; assume 0.03 on the stratified mix), pilot design A0-A3, T=6, 2 seeds, 2 candidates, 18 search / 6 sealed avg@2:

| Component | Volume | Estimate |
| --- | --- | --- |
| Task rollouts (search + sealed + A3 re-solves + C-TTS) | ~2,800 | USD 60-90 |
| Judge calls (DeepSeek-V4-Flash, A1/A2/A4 on every search rollout, variance control) | ~3,000 | USD 20-40 |
| Evolver sessions (DeepSeek-V4-Pro, full search traces in context) | ~100 | USD 60-150 (least certain; measure the first 5 sessions and re-estimate) |
| Cross-judge (Kimi-K2.6, final-harness trajectories only) | ~300 | < USD 10 |
| **Phase 2 total** | | **USD 150-300** |

Decisions:
- Phase 2 estimate is set to **USD 200**; the 150% guard therefore halts at **USD 300** and at **4 days** wall-clock. Per-rollout cap stays USD 1. Add a per-evolver-session cap of USD 5 (halt and report, do not retry).
- Concurrency: run P1.2 at 8; if clean (no 429s, no Harbor timeouts), pilot at 8. Judge throughput is the binding limit (V4-Flash 250k TPM with 30k-token process-judge inputs ≈ 8 calls/min): run judging asynchronously, never inline with rollouts.
- J-cross: **Kimi-K2.6 via API is the only cross-judge in the pilot.** Gemini via Copilot (14 premium requests per call) and Claude Haiku are reserved for a final ≤50-trajectory sample in Phase 3.
- Phase 3 (T2 40/40, T=10, 3 seeds, A0-A4 + F-in/F-cross/F-agree + C-TTS) is provisionally USD 600-900; no guard set until the T2 adapter's per-rollout cost is measured in P1.10.

## 4. Pilot arms and seeds (confirming Phase 2 as written in context.md)

A0, A1, A2, A3-loop, plus C-TTS (A6), two seeds, T=6. A4 stays in Phase 3. Phase 2 does not start until P1.6, P1.8, P1.9, P1.11 pass and the Section 5 acceptance log is committed.