# PREREG / PLAN reconciliation and freeze log

Date: **2026-09-12 UTC**. Status: **PREREG version 1.0 frozen pending R10**; freeze-commit ID deliberately left for the committing operator. This task made no commit, push, model call or pilot launch. It writes only `PREREG.md`, PLAN Sections 1–8/9.1 and this log. The task's explicit three-file boundary overrides the general context.md update convention; this file records the handoff context.

Binding order: `decisions-260912.md` > the superseded portions of `decisions-260910.md` and the proposal addendum. The 260912 filename is authoritative despite its internal 2026-09-11 title. Decisions A1–A10 remain binding except the explicitly superseded task-model/budget provisions. Historical review/implementation documents describe prior conditions; they do not override ratification.

The specification is reconciled in the allowed files. **R4 #3's affordability requirement cannot be closed:** the fixed complete schedule exceeds the ratified USD 450 estimate and USD 650 guard. Values are preserved, arithmetic is explicit, and the conflict is logged as REC-01 rather than silently reducing the design or authorizing more spending. R10 and final qualification remain execution work, not completed review evidence.

## Decision mapping

Locations use the document section numbers and named paragraph/table so they remain usable after line shifts. “Retained” means the already-correct W4 text was checked and preserved; “applied” includes removal of provisional authority/drafting labels.

| ID | Ratified content / disposition | PREREG location | PLAN location |
| --- | --- | --- | --- |
| A1 | A3-native one recipe round; A3-loop separate iterative extension, 10/3/3 and fixed first-rollout baseline. AD14 supersedes native-as-entry-gate timing. | [§4](../PREREG.md#4-a3-native-a3-loop-and-commensurability), §1 entry, §9 A3 choices | [§4](PLAN.md#4-feedback-arms-the-evolver-sees-only-its-arms-signal), §8 |
| A2 | SEAL points to this plan's §7; SIGIL cites 2.1–2.2 and 4.1. Already-correct references retained. | §5 rubric and §3 claim metric | §4.1/§6.2; existing References retained unchanged |
| A3 | T2 healthcare 013–016/legal 017–020, 40/40; finance 008–011 anchors; hardened grader-v1 hash; TG015/TG018 retained; native mean secondary; valid-wrong controls; range before tolerance under AD9. | [§3](../PREREG.md#3-measurement-definitions), T2 predicate/hash | §3 T2 table, §6.1, §8 |
| A4 | Raw common-rollout gap, no centering; secondary change/regret; A2 construct interpretation; no A3 gap/H1/H3. | §§2–4; missing-score cohort explicit in §3 | §1 H1/H5, §§6.1/6.3 |
| A5 | Filtered images/canary checks, seven-row log, shared business data; cache row exception explicitly ratified. | §§1/3/5, DEV-03 | [§5](PLAN.md#5-information-isolation-protocol) |
| A6 | C-TTS fixed seed, comparator-matched logical budgets and selection signal; A0 selector exception; full cost table. | §§1/5/7.1 | §§2/5/6.5/8 |
| A7 | H1 positive OLS slope, paired 95% CI; separate 5 pp non-inferiority. AD6 makes pilot sufficiency exploratory. | §§2/8 | §§1/6.5 |
| A8 | Fixed task/evolver/judge mapping; AD1 supersedes mini/candidate baseline. | §§5/6/9 task/scorer rows | §2 table, [§9.1](PLAN.md#91-resource-mapping-ratified-2026-09-12) |
| A9 | Uniform durable retry/denominator policy, as modified by L1'/AD13: recovered events ordinary, terminal failures fail, content-policy failure never retried/excluded. | [§6](../PREREG.md#6-failure-and-timeout-policies), §3 labels | §§6.1/8 |
| A10 | TB2 exact commit, metadata-stratified committed split/seed/hash, smoke eligibility. Retained. | §1 data/split | §3 T1 row |
| AD1 | terra-low-8k, low/JSON/8192/24/30 s; nearest 30% band centre, cost secondary; five standing behaviour metrics; historical versus retained denominators explicit. | §§3/6/9, selection evidence and calibration update | §2 task row, §6.2, §9.1 |
| AD2 | Item 5 reverse-keyed, 1 = no unsupported claim; equal five-item denominator. Applied/retained. | §5 rubric, §9 fixed choices | §4.1 |
| AD3 | Multi-label classes, dominant-class rule and co-occurrence table; H2 diff denominator once. | §3 edit taxonomy, §2 H2, §9 | §6.2 |
| AD4 | H5 compares FPR drift to judge-repeat **SD in matching score units**, separate from continuous-score tau. | §§2/5/8, numeric FPR control/sensitivity | §6.3 |
| AD5 | epsilon = 0; one-task allowance Phase 3 sensitivity only. | §§5/9 | §7 |
| AD6 | Pilot H2/non-inferiority exploratory; confirmatory Phase 3 on 40 sealed tasks; H1/H3/H5 pilot tests with explicit sensitivity. Six-test pilot Holm family fixed. | §§2/8/9 | §§1/6.3/6.5 |
| AD7 | A0/A1/A2/C-TTS two candidates; A3-loop three proposals; comparator-specific C-TTS budgets. | §§1/4/7.1 | §§2/4/6.5/8 |
| AD8 | Every post-start change timestamped/logged, reason and observed-results disclosure; preserve frozen originals. | Frozen state change control, §§6/9.1 | §1 freeze reference, §8 |
| AD9 | Enforce finite 0 <= s <= 1 before abs(s−1) <= 1e-6; no invalid-normalized fallback or clamping. | §3 T2 predicate | §6.1 |
| AD10 | Exact ratified L1' wording; recovered command timeout/parse error/nonzero exit never flips verifier pass; L2 rejected. | §3 T1, §6 failure table, §5 offline label reconciliation | §§6.1/8 |
| AD11 | JSON-in-text seed for all task models/arms; native Responses adapter unfunded Phase 3; protocol-error standing rate. | §§3/6/9 | §§2/6.2/8/9.1 |
| AD12 | Shared task-model reasoning+output allowance 8,192; exhaustion is task failure. Historical judge allowances explicitly identified to preserve the measured tau condition. | §§6/9 scorer settings | §§2/6.1/8/9.1 |
| AD13 | Sole-call API timeout/no response: retry once, then exclude/count. Content-policy rejection: separate task-failure category in denominator, never retry/exclude. Historical missing retries disclosed. | §§3/5/6, DEV-04 | §§6.1/6.2/8 |
| AD14 | Pre-pilot cap 120, native 40–60 concurrent/non-gate, qualification 30/gate; pilot estimate 450, guard 650/5 days, rollout 1/session 5, first-five re-estimate, concurrency 4/memory guard. **Affordability portion unresolved (REC-01).** | [§7/7.1](../PREREG.md#7-budgets-and-guards), §§1/9.1 | §§4/8/9.1 |
| AD15 | v3 first/last 4,000, 200,000 envelope, explicit elisions/longest-middle-first; **evolver same export**, claim detector **full sanitized trace**, N=5; terra v3 tau values and archived v1 provenance. | §§3/5/8/9 | §§5/6.2/6.3/7 |

## Other binding provisions without original decision IDs

The keys below are log references, not newly invented source decision IDs.

| Source provision / log key | Application and locations |
| --- | --- |
| 260910 §2 / OLD-MODEL | Superseded by AD1/AD10–AD13. Removed mini-first/escalation/fallback/lowest-cost selection from PREREG §9 and PLAN §§2/9.1. |
| 260910 §3 / OLD-BUDGET | Superseded by AD14. Removed USD 200/300, four-day and concurrency-eight conditions from the allowed specification. Retained API-only Kimi pilot family; narrowed to A1 per task/R4 #11. Phase 3 Gemini/Claude ≤50-trajectory sensitivity remains outside pilot. |
| 260910 §4 / PILOT | T1 A0/A1/A2/A3-loop plus C-TTS, two seeds/T=6, A4 deferred, P1.6/P1.8/P1.9/P1.11/Section 5 entry evidence: PREREG §§1/7; PLAN §§2/4/8. |
| 260912 §4 / HK-REPLACEMENTS | Two operator replacement lineages logged individually with exact trial IDs as PREREG DEV-01/02; no new retry authorization or further action. PLAN §8 points to these. |
| 260912 §4 / HK-CACHE | PREREG §§1/5/9.1 and PLAN §§5/9.1: 6/7 live; provider cache unobservable, ratified nonblocking limitation. |
| 260912 §4 / HK-R10 | Frozen state and PREREG §§1/9.1, PLAN §8: one final R10 of manifest/PREREG/Section 5 log; after R10 only information isolation or pass-label findings block. Other findings logged or Phase 3; hard guards remain binding. |
| 260912 §4 / HK-SEQUENCE | Reconciliation/freeze -> final controller qualification gate -> pilot; clean native calibration concurrent under its own cap, never a pilot input/gate. PREREG §§1/4/7; PLAN §§4/8. No launch performed. |
| Task / FREEZE-N | Fixed **N=5 steps [D]**, PREREG §§3/9 and PLAN §6.2; no outcome-selected window. Frozen version/date/future commit field/pending-R10 flag added. |

## R4 findings 1–12

| Finding | Disposition | PREREG / PLAN locations and evidence |
| --- | --- | --- |
| R4-1 | Resolved by binding AD1; no unaudited chat override remains. | PREREG authority/§9; PLAN §§2/9.1; nearest-centre selection and full settings. |
| R4-2 | Resolved as an exploratory pilot claim, not asserted powered non-inferiority. | PREREG §§2/8; PLAN §§1/6.5. Five-pp margin retained, confirmatory Phase 3 on 40 sealed tasks. |
| R4-3 | **Partially resolved; affordability unapplied, REC-01.** | PREREG §7.1 and PLAN §8 fix the full per-stage schedule, distinguish logical/physical rollouts and separate native costs. Complete design needs 12,648–13,224 nominal logical slots at ~USD 1,421–1,486 solver cost; USD 450/650 cannot fund it. |
| R4-4 | Resolved; all four extra decisions ratified. | AD2/3/4/5: PREREG §§3/5/8; PLAN §§4.1/6.2/6.3/7. No provisional D tags remain. |
| R4-5 | Resolved with fixed schedule and missing-score estimand. | PREREG §§1/3/6/7.1/8; PLAN §6.1. J over valid scores with counts; paired common-cohort gap; full oracle denominator separate; sealed once per iteration plus t=0; final retained harness. |
| R4-6 | Resolved at specification level. | PREREG §§2/8 control-based H1/H3/H5 sensitivity table and H2 two-seed limit; PLAN §§1/6.3. A3 noise statistic still unmeasured, transparently non-estimable until available. |
| R4-7 | Resolved for task/protocol, A1/A2 tau and N. | PREREG §§5/9 use supplied calibration, exact artifact hashes, v3 tau 0.006022079/0.017391640, N=5. No mechanical selection formula awaiting unknown input; A3 measurement availability separately disclosed. |
| R4-8 | Resolved by enumerated frozen operational choices. | PREREG §9 covers randomness, immutable condition, schedule, missingness, ties/pools, rubric applicability, dominant class, A3 floor/embeddings/digests/references, thresholds, bootstrap estimability and test family. Phase 3-only choices explicitly deferred. |
| R4-9 | Resolved; no redesign-through-amendment loophole. | Frozen state change control; PREREG §§6/9.1, PLAN §8: timestamp/reason/observed-results disclosure, preserve original, post-start changes logged deviations. |
| R4-10 | Resolved. | PREREG §3 / PLAN §6.1 explicitly enforce range first, then 1e-6 tolerance; retain valid submission/grader and fixed denominator. |
| R4-11 | Resolved. | PREREG §§5/7.1/7 and PLAN §§6.4/9.1: Kimi A1 only, A2 unusable, observed-pair counts/bounds, descriptive transfer; neither score drop nor persistence proves deception. |
| R4-12 | Resolved with narrow oracle exceptions. | PREREG §§1/5 and PLAN §§4/5/8: A0 binary search feedback and trusted C-TTS(A0) per-task oracle selector allowed; no raw diagnostics/tests/references or sealed-driven evolution selection. |

## Measured facts and unapplied items

1. **REC-01 — funding conflict remains unapplied.** AD14's component arithmetic is approximately USD 315 solver + 30 judges + 100 evolver + 5 cross-judge = **450**, assuming roughly **2,800 rollouts**. PREREG §7.1 fixes an explicit complete schedule at **12,648 + 12A** slots, A=0–48 acceptances, before retries. At the measured **USD 0.112336/rollout**, solvers cost **USD 1,420.83–1,485.53**. The documented older implementation estimates **13,872** slots (~USD 1,554 at its rounded 0.112 rate). Neither fits the ratified guard. The task permits logging an unapplied decision, but does not authorize changing T, seeds, task counts, candidates, matched controls or budget. The funding/schedule clarification was raised while independent edits continued. No funding increase or scope reduction is inferred from silence.
2. **A3 noise evidence remains an execution dependency, not an open parameter.** The supplied R9/A3 record contains no matching five-repeat v3/Azure fixed-reference H5 SD. The rule and non-estimability handling are frozen; no numeric value was invented, borrowed from A1/A2 or obtained through new calls. The clean native run is explicitly not a pilot gate. Its separate 260912 running calibration uses an independently documented 900-second inspection timeout/avg@2 search extension; that native-only condition does not silently change the frozen pilot's 300-second inspection/one-attempt search schedule.
3. **Historical facts cannot be rewritten as completed policies.** The new offline calibration report excludes four sole-call API timeouts without archived retries and logs the missing-retry deviation. PREREG DEV-04 preserves it; the prospective retry-once rule remains. Terra's original 18/60 differs from retained 18/56 and available-task mean 34.5%; no-action numerator is zero, retained denominator 56. These measured corrections do not alter the ratified model. Mini's four distinct passing search tasks refine the decision's approximate three-task rationale.
4. **Review/commit/qualification remain outside this edit task.** Frozen commit field is deliberately administrative and unfilled because commits are forbidden. R10 is pending. No manifest/launcher/source changes, qualification run or external publication is asserted. General context.md and Copilot-review conventions do not authorize edits outside the explicit three-file boundary or model calls prohibited by this task.

All other ratified decisions are applied or retained consistently in the permitted specification. Cache observability is a ratified limitation, not an unapplied decision. Phase 3-only choices remain explicitly deferred by design.

## Verification record

Documentation-only verification uses exact protected-section comparisons, whitespace/diff checks, decision/R4 coverage, local link/anchor checks, and independent arithmetic from archived JSON. No pytest suite or model-based review is claimed; no code is changed by this task and no commit is being made. Concurrent workers are modifying other files in the shared workspace; those changes are outside this reconciliation's authorship.

Verified successfully:

- `git diff --check -- PREREG.md docs/PLAN.md docs/prereg_reconciliation.md`.
- Exact comparison against initial HEAD: PLAN pre-Section-1 text, References/Section-9 introduction and all Sections **9.2–9.7 unchanged**.
- Every **A1–A10**, **AD1–AD15** and **R4-1–R4-12** has a mapping row; no provisional D tags or calibration-open-items section remain.
- Exact ratified L1' predicate/recovered-event text matches PREREG and PLAN; shared tau, budget, memory-guard and review constants checked.
- Split, terra v3 manifest and metrics SHA-256 values match the archived files; local Markdown file links and section anchors resolve.
- Independent arithmetic confirms ordinary **858 + 6a**, A3-loop **588 + 6a** slots per seed and total **12,648 + 12A** including matched controls. Nominal counts assume valid proposals/eligible promotion; failures may leave unused slots and reduce realized cost, but are not an affordability assumption.
- Archived-score re-expression verifies valid-score TPR/FPR counts and five-repeat sigma_FPR after the four AD13 exclusions; original JSON is unchanged. H1/H3/H5 sensitivity values are explicitly judge-noise-only planning calculations.

Coverage summary: **25 decision IDs mapped (AD14 affordability partial), 12 R4 findings mapped (11 resolved, R4-3 partial), 4 housekeeping provisions applied**. Unapplied feasibility is REC-01; missing A3 H5 calibration evidence is disclosed separately with a frozen rule. No code tests/model-based review were run or claimed.
