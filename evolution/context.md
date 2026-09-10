# Evolution infrastructure handoff

The requested P1.3/P1.6 evolution loop and C-TTS implementation are complete.
Both required real infrastructure iterations finished in experiment
`p13-p16-260910`: A0 and A1, two API-generated candidates each, 18 search
tasks per candidate, fresh paired avg@2 confirmation, paired anchors, incumbent
update/rollback, and one sealed avg@2 allocation per iteration. These are
infrastructure validation results, not clean confirmatory pilot evidence.

## Results and artifacts

| Arm | Decision / incumbent | Final search J | Final search O | Sealed O | Audited cost upper including reserves |
| --- | --- | ---: | ---: | ---: | ---: |
| A0 | Accepted i01-c2 | 0.166667 | 3/18 | 1/11 valid; 1/12 fixed denominator | $2.402539 |
| A1 | Rejected i01-c2; retained seed | 0.269444 | 4/18 | 0/12 | $4.282774 |

A0 confirmation was 8/36 versus 10/36; anchors 1/6 versus 2/6.
A1 confirmation J was 0.330556 versus 0.247222, below the required gain;
confirmation O was 10/36 versus 11/36. A1 seed anchor was 1/6 and candidate
anchor was unavailable after one verifier timeout, so rejection is conservative.

There are 170 A0 and 171 A1 scheduled trial records, all terminal. Total
conservative planning cost is $6.685313, including $0.473873 reserved for four
uncertain charges. Known partial cost is $3.952585; invoices are not verified.
The report reprices 13 early A0 receipts conservatively, adding $0.00109515
relative to the original immutable iteration summary. Original receipts and
summaries remain unchanged; stage/arm totals reconcile in the final report.
The evaluation phase ran 5.29 hours, ending at 17:43:15 UTC on 2026-09-10.

- `docs/evolution.md`: architecture, boundaries, verbatim prompts, acceptance
  matrix, complete results, cost/timing tables, verification, and open issues.
- `logs/evolution/p13-p16-260910/report.json`: canonical reconciled results,
  per-stage and per-session counts, costs, timing, manifests, and decisions.
- `logs/evolution/p13-p16-260910/audit.json`: 2,545 requests, 329 task-start
  traces (325 private, four legacy), 144 exact judge payloads, 277 feedback
  files, and 20 source/export trace hashes per arm; no audit errors.
- `logs/evolution/p13-p16-260910/final_verification.json`: real completed-run
  resume produced zero new requests and zero duplicate summaries; candidate
  hashes/read-only modes, raw reward records, and budget caps verified.
- `runs/p13-p16-260910/{A0,A1}/`: immutable candidates, working copies, stable
  SQLite state, incumbent checkpoints, and append-only evolution summaries.
- `costs/p13-p16-260910/`: ledger, intent/response/error lifecycle records,
  payload archives, timings, shared phase guard, and cost reconciliation.
- `oracle/p13-p16-260910/`: private raw rewards, labels, acceptance inputs.
- `feedback/{A0,A1}/p13-p16-260910/`: sanitized arm-specific search feedback.
- `logs/harbor/p13-p16-260910/`: all Harbor trial artifacts.
- `logs/evolution/p13-p16-260910/context-history.md`: prior operational notes.

## Implementation and frozen choices

Launch with `uv run python -m scripts.run_evolution`. The implementation uses
networkless evolver and candidate-runtime containers; candidate Python is never
imported into the credential-bearing host. Only candidate working source is
writable to the evolver, and its own sanitized feedback is read-only.
`harness/seed.py` is the editable surface; copied plumbing remains unchanged.
The host enforces API/tool caps and records authoritative traces outside the
Harbor agent-log mount, exporting them after teardown.

The explicit task fallback was frozen because calibration had no unambiguous
recommendation at start: TASK_ALT2 deployment gpt56luna, JSON protocol, low
reasoning, 4,096 output tokens, 24 model calls, 30-second commands. Evolver is
EVOLVER/DeepSeek-V4-Pro; judges use the existing JUDGE/DeepSeek-V4-Flash layer.
AD1 model ratification and formal PREREG gates remain pending.

Existing sanitize/judges/judge_queue/acceptance public interfaces are unchanged.
Judge calibration remains in `logs/judges/seed-mini-json-v2`; exact tau is
A1 0.02184135419534282 and A2 0.022566773346210982. Epsilon is zero. A4 uses
the fixed equal mixture and requires an explicit calibrated tau for anchors.
`--acceptance improve` exposes the original strict-improvement, no-anchor rule.
C-TTS A0/A1/A2/A4 arm types match comparator rollout allocations and select by
their own signals. C-TTS was checked with mocked end-to-end/incremental tests;
no real C-TTS run was part of these two live iterations.

Stable IDs and trial leases prevent duplicate dispatch. Resume waits for a
surviving worker's evidence export before recovering its result; an interrupted
trial without a result is not silently rerun. Controller ownership is checked
before and after admission waiting. Phase halts persist across roles even if
a rejected reservation rolls back. Per-session and per-rollout guards remain
$5 and $1. A shared endpoint limiter spans roles; Docker admission allows at
most four own Harbor trials, or two with foreign alexgshaw containers, and
pauses below 6 GiB MemAvailable. Credentials stay in the host .env and are never
mounted into candidate workspaces or printed.

The initial four-hour maximum was amended before expiry to six hours, interpreting
the user's approximate time estimate and prioritizing the required end state.
This was a logged controller interpretation, not a user approval. The $30 hard
ceiling remained fixed; `phase_amendments.jsonl` records the change. Both live
iterations completed before the amended 18:25:43 UTC deadline.

## Verification and disclosed limitations

Latest project-wide check: 377 passed, 5 skipped in 15.37 seconds
(`logs/evolution-pytest-full.txt`). Ruff passes the new loop/report modules and
tests. Actual Docker boundary checks passed 3/3
(`logs/evolution-pytest-docker.txt`). Final report cost assertions cover all
2,545 real requests and confirm phase/scope caps after repricing.

Initial A0 baseline had 12 pre-API stdout=None setup failures and four early
traces in the legacy Harbor-writable log location. Later private trace isolation
covers all candidate screens, confirmation, anchors, and final measurements.
The initial tool-failure veto was corrected to reward=1/no-agent-timeout before
promotion, without solver reruns; A0 proposals saw the earlier partial feedback.
All original rows, feedback, and corrections are preserved.

A1 c1 recovered a completed source write after a prompt ceiling, with no new
model call. A1 c2 had two explicit transport continuations, replaying successful
actions without re-execution; all failed calls count toward its original caps.
Two task HTTP errors (500 and 400) had no usage telemetry and were not retried.
One early A1 seed measurement was reused and one extra replicate remains outside
the final metric, yielding 171 scheduled trials. No hidden whole-rollout retry
or additional sealed allocation occurred.

Two verifier timeouts remain unlabelled: A0 sealed torch-pipeline-parallelism,
and A1 candidate-anchor filter-js-from-html, both at 900 seconds. Raw reward is
null, with fixed-denominator sensitivity available. A dedicated frozen-artifact
regrade path remains open before formal pilot use. Full T1 image/network/grader
isolation provenance and provider-internal cache partition verification also
remain open. The wheel currently omits evolution, so run from the checkout.
No harness/gdpevo edits, commits, pushes, or assistant CLIs were performed by
this worker. Do not touch concurrent calibration-* jobs or their directories.
