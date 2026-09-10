# Calibration handoff — complete

P1.1/P1.2 is complete. The unchanged `tb2_split.json` contains 30 tasks from
terminal-bench@2.0 at commit 69671fbaac6d67a7ef0dfec016cc38a64ef7a77c, sampled
with seed 260910 from `task.toml:metadata.difficulty`. Population: 4 easy,
55 medium, 30 hard. Sample: 1 easy, 19 medium, 10 hard; search/anchor/sealed
sizes 18/6/6. In-memory reproduction from all 89 cached metadata files matched
exactly. `scripts/make_tb2_split.py` remains the reproduction script.

Recommend **GPT-5.6 Terra + JSON**, deployment `gpt56terra`, endpoint prefix
`TASK_ALT2`, low reasoning, 4096 completion tokens, 24 calls, 30-second command
limit, concurrency 4. It is the sole configuration meeting all calibration
gates: 17/60 passes (28.3%) and 1/60 no-action finishes (1.7%), with the common
JSON prompt. Mini/json and mini/native both pass 5/60 (8.3%). Luna/json passes
14/60 (23.3%) but has 7/60 no-action finishes (11.7%). Luna/native and
Terra/native each received 60 HTTP 400 rejections without model generation
under Chat Completions with low reasoning; they are unsupported as tested.
The Responses API was not tested. `.env` and global model defaults were not
changed; future callers should explicitly select the recommended model and
protocol. See `docs/calibration.md` for all split/task metrics and caveats.

All six configurations have two finalized slots for each of the 30 tasks:
360 finalized slots, 328 verifier rewards, and 32 separately listed command
failures. Mini/native was reconciled as 51 original results + 2 first-recovery
results + exactly 7 final replacements in
`calibration-mini-native-260910-recovery2`. Killed attempts were never scored
as failures or extra rollouts. Nine replacement results are marked in the doc.
The resumed jobs ran sequentially in the required order, always with four
concurrent trials. MemAvailable admission was 10 GiB, pause threshold 6 GiB;
no admission pause occurred in the recorded resumed-job samples.

The experiment ledger has 1454 logical calls/request artifacts. Known API
cost is $8.84674543; retained reservations are $3.66274995; the shared guard
ends at $12.50949538 / $40. Every rollout respected its $1 projected cap.
Two interrupted requests were recovered into the ledger with unknown charges.
One diagnostic captured the Luna/native API rejection. Terra/json had one
local budget stop before HTTP dispatch, which incurred zero incremental
API usage; the null ledger cost is preserved and explained in both reports.
Terra/json mean rollout cost is $0.12426528, max $0.855366, mean agent time
68.23 seconds. It costs 11.45x Luna/json's mean, so eligibility carries a
substantial cost premium.

Final verification: 67 focused harness/calibration tests passed; scoped Ruff
passed. `logs/calibration_final_audit.json` verifies request/ledger coverage,
fixed prompts/settings, pinned task revisions, unchanged seed/API/price/split
hashes, two finalized slots per task/configuration, and budget reconciliation.
`scripts/calibration_report.py` regenerates `docs/calibration.md`, preserves
its recommendation block, and updates `costs/calibration_results.json` and
`costs/summary.md`. The latter covers all project phases, not only calibration.

No W5 work remains. No commits, pushes, or model CLI calls were made. Root
context.md and protected planning/decision files remain owned by other workers.
