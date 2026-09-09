# Cost summary

API-equivalent USD estimates are separate from subscription charges.
Known USD is a subtotal; unknown costs are not treated as free.
Wall time sums model calls and is not elapsed rollout time.
Previously unpriced calls resolved with the same-date price table: 1. Original ledger records are retained.

| Phase | Arm | Role | Model | Calls | Failed | Input | Cached | Output | Reasoning | Known USD | Unknown USD calls | Premium | Wall s |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| P0.3 | A0 | other | claude-haiku-4-5-20251001 | 1 | 0 | 3438 | 0 | 41 | 0 (+1 unknown) | 0.003643 | 0 | 0 | 2.784 |
| P0.3 | A0 | other | gpt-5.6-luna | 1 | 1 | 0 (+1 unknown) | 0 (+1 unknown) | 0 (+1 unknown) | 0 (+1 unknown) | 0.000000 | 1 | 0 | 0.195 |
| P0.3 | A0 | other | gpt-5.6-luna-2026-07-09 | 1 | 0 | 1822 | 0 | 9 | 0 | 0.000466 | 0 | 0 | 1.647 |
| P0.3 | A0 | review | gpt-5.6-sol | 1 | 1 | 26969 | 0 | 9816 | 9322 | 0.331162 | 0 | 1 | 145.165 |
| P0.3 | A0 | task | gpt-5-mini-2025-08-07 | 50 | 0 | 200914 | 88064 | 25790 | 9792 | 0.081994 | 0 | 0 | 298.365 |
| P0.3 | A0 | task | gpt-5.1-2025-11-13 | 47 | 0 | 356424 | 212608 | 22338 | 11141 | 0.429726 | 0 | 0 | 214.087 |
| P0.3 | A0 | task | gpt-5.6-luna-2026-07-09 | 22 | 0 | 64846 | 0 | 6464 | 1951 | 0.023800 | 0 | 0 | 72.996 |
| P0.4 | assessment | review | gpt-6-astra | 1 | 1 | 24554 | 0 | 2103 | 1808 | 0.412067 | 0 | 1 | 60.301 |

Recorded calls: 124. Known USD subtotal: $1.282859. Unknown USD calls: 1.

Source rates and verification date: `scripts/prices.json`.
Raw evidence: each ledger record's `raw_dir`. Rollout timings: `logs/<run_id>/timing.jsonl`.
