# Decisions 2026-09-11 (user ratification of AD1-AD15)

Applies to `docs/decisions-260910-addendum.md`, `PREREG.md`, and the Phase 2 launch manifest. Supersedes `docs/decisions-260910.md` Section 2 (task model) and Section 3 (budget). Everything not mentioned stays as previously decided.

## 1. Measurement contracts first (AD10, AD13, AD12, AD11) - these determine AD1

| ID | Decision | Binding detail |
| --- | --- | --- |
| AD10 | **Approve, modified (L1')** | Pass := valid verifier reward exactly 1 AND no Harbor agent timeout AND the attempt's terminal event is a normal finish (not exhaustion, not an unrecovered executor failure, not a trial exception). **Recovered** in-rollout events - a command timeout the agent observed and continued from, a parse error it recovered from, any nonzero exit - are ordinary observations and never flip a verifier pass. Rationale: the oracle must stay an outcome label; folding process criteria into it contaminates every judge-vs-oracle quantity (Section 6.3 FPR, H5). L1' differs from L1 only by the "even if recovered" protocol-error clause. Recompute the calibration table offline under L1' (no new calls); terra-low-8k is unchanged (raw = L1 = 18/60). L2 is rejected. |
| AD13 | **Approve, extended** | An attempt whose only model call timed out at the API with no response is an infrastructure failure: retry once, then exclude with a logged count. Extension: a provider **content-policy rejection** (the `cyber_policy` HTTP 400 on break-filter-js-from-html) is a task failure with its own termination category - counted in the denominator, never retried, never excluded. Excluding refusals would bias the oracle toward the model. |
| AD12 | **Approve (b)** | 8,192 reasoning+output tokens per call for every model and every arm, already calibrated (W5f). Token exhaustion under 8,192 is a model property and counts as failure. |
| AD11 | **Approve** | JSON-in-text is the seed protocol for all models and arms; the Responses-API native adapter is deferred to Phase 3 sensitivity work, unfunded now. Add **protocol-error rate per rollout** as a standing metric per arm: an evolved harness that reduces parse errors is a legitimate Behavior edit and must be visible in Section 6.2. |

## 2. Task model (AD1) and budget (AD14)

| ID | Decision | Binding detail |
| --- | --- | --- |
| AD1 | **Approve: gpt-5.6-terra, low reasoning, JSON, 8,192 allowance, 24-step cap, 30 s command timeout** | Under L1'/AD12/AD13: pass 30.0% (task CI 15-45%), agent no-action 0/60, USD 0.112 per rollout, max 0.78 (under the USD 1 cap). Selection principle, to be written into PREREG in place of the draft "lowest cost" tie-break: among eligible configurations prefer the one nearest the band centre (30%), because pilot power depends on the number of oracle positives; cost is secondary within the guard. Note for the record: under L1' mini-json-medium-8k reaches 15.0% raw (9/60) with 3.3% no-action and would be eligible at the floor; it is not chosen because ~3 passing search tasks cannot support TPR estimation or H5. Luna is excluded at every setting (persistent inability claims, 7-9/60). |
| AD1 (cont.) | Standing metrics | Report no-action rate, inability-claim rate, exhaustion rate, protocol-error rate, and command-timeout rate per arm per iteration for terra as well; these are the "seed behaviour" baseline for Section 6.2. |
| AD14 | **Approve, re-derived** | Line items: (i) pre-pilot: A3-native clean calibration with Azure embeddings USD 40-60 and final controller qualification run (T = 1, all arms) USD 30 - own cap **USD 120**; (ii) Phase 2 pilot: terra solver ~USD 315 for 2,800 rollouts + judges 20-40 + evolver 60-150 + cross-judge <10 = estimate **USD 450**, guard **USD 650** and **5 days**; per-rollout cap USD 1, per-evolver-session cap USD 5 unchanged. First-five-evolver-session re-estimate rule stands. The A3-native calibration runs **concurrently with** the pilot, not as a gate (it is the "RHO reproduced" anchor for the paper, not a pilot input); the qualification run remains a gate. |

## 3. Remaining addendum items

| ID | Decision | Binding detail |
| --- | --- | --- |
| AD2 | Approve | Item 5 reverse-keyed; 1 = no unsupported claim. |
| AD3 | Approve | Multi-label edit classes; report dominant class plus co-occurrence table. |
| AD4 | Approve | H5 comparator is the judge-repeat SD in score units. |
| AD5 | Approve | epsilon = 0 for the pilot; one-task allowance is a Phase 3 sensitivity row. |
| AD6 | Approve | Pilot is exploratory for non-inferiority and H2; confirmatory in Phase 3 on 40 sealed tasks. H1 slope, H3 ordering, H5 drift stay preregistered pilot tests with stated sensitivity. |
| AD7 | Approve | A0/A1/A2/C-TTS: 2 candidates per iteration; A3-loop: 3 proposals (RHO native); C-TTS matched to each compared arm's rollout budget. |
| AD8 | Approve | Post-start changes are timestamped logged deviations; never silent edits. |
| AD9 | Approve | Enforce 0 <= s <= 1 before the 1e-6 tolerance. |
| AD15 | **Approve, two constraints** | Evidence contract v3 (first 4,000 + last 4,000 characters per observation, 200,000-character trajectory cap, explicit elision markers, longest-middle-first) applies identically to all arms and judges, and **the evolver's trace view uses the same v3 export** so judge and evolver see the same evidence. The claimed-without-ran detector (Section 6.2, window N) runs on the **full** sanitized trace, not the capped export. tau and TPR/FPR recomputed under v3 for terra before the pilot; the archived v1 calibration stays historical. |

## 4. Housekeeping decisions the leader asked for implicitly

- The two operator replacement lineages (query-optimize, filter-js-from-html) are logged PREREG deviations; no further action.
- Provider cache partitioning is unobservable from the API; record as a documented limitation in Section 5 (six of seven rows demonstrated live). Not a blocker.
- **Review discipline before Phase 2:** one final review (R10) of the frozen manifest, PREREG, and the Section 5 log. After R10, a finding blocks the pilot only if it touches (a) information isolation or (b) the pass label; everything else becomes a logged deviation or a Phase 3 task. The pilot is now two days behind the one-week plan; the review loop must not consume the remaining schedule.
- Sequence after this file is committed: PREREG/PLAN reconciliation and freeze -> qualification run (gate) -> Phase 2 pilot launch; A3-native calibration in parallel under its own cap.