# Decisions addendum 2026-09-10 (DRAFT for user ratification)

Drafted by the leader session after reviews R4 and R5 (`docs/reviews.md`). Items here either record a decision the user gave in chat that is not yet in `docs/decisions-260910.md`, or ask for ratification of design choices that went beyond amendments A1-A10. Nothing here is in force until the user marks it approved (edit the Status column, or reply in chat and the leader will record it).

| ID | Item | Proposed decision | Status |
| --- | --- | --- | --- |
| AD1 | Task-model baseline (overrides decisions file Section 2) | Not frozen to gpt-5-mini. P1.2 calibration compares gpt-5-mini, gpt-5.6-luna, and gpt-5.6-terra (terra added by the leader as a same-generation fallback) under the same seed; the user prefers luna. Selection rule: among models meeting seed pass rate 15-45% at avg@2 on the stratified 30 and no-action rate below 5% with no model-specific prompt, choose luna if eligible, otherwise the cheapest eligible; if none is eligible, apply the decisions file's escalation rule to the user's preferred model. The tool protocol (JSON-in-text or native function calling) is chosen once for all models and arms from the same experiment. | pending |
| AD2 | Section 4.1 rubric item 5 reverse-keyed ("unsupported claims of completion" scored so that 1 = no unsupported claim) | Approve; required for a consistent 0-1 average. | pending |
| AD3 | Section 6.2 edit classification multi-label instead of mutually exclusive classes (recommended by review R2 from HarnessX/Phantom evidence) | Approve; primary reporting uses the dominant class plus a co-occurrence table. | pending |
| AD4 | Section 6.3 H5 comparator: judge-repeat standard deviation in score units instead of "variance" | Approve. | pending |
| AD5 | Section 7 epsilon fixed at 0 (no one-task anchor regression allowance) | Approve for the pilot; a one-task allowance may be tested as a sensitivity row in Phase 3. | pending |
| AD6 | Pilot inference status | The Phase 2 pilot is exploratory for the non-inferiority ("judge sufficient") claim and for H2; confirmatory tests of those move to Phase 3 with 40 sealed tasks. H1 slope test, H3 ordering, and H5 drift remain as preregistered pilot tests with their sensitivity stated from the variance control. | pending |
| AD7 | Candidates per iteration | A0, A1, A2, C-TTS: 2 candidates per iteration; A3-loop: 3 proposals per iteration (RHO native); budgets reported per arm; C-TTS matched to each compared arm's rollout budget. | pending |
| AD8 | PREREG amendment clause | After the first pilot iteration starts, any change is a logged deviation with timestamp and reason, never a silent edit. | pending |
| AD9 | T2 binary oracle range | 0 <= s <= 1 enforced before the abs(s-1) <= 1e-6 tolerance. | pending |
