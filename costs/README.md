# Cost Ledger

Every model call made by any component (task model, evolver, judge, review) appends one JSON line to `costs/ledger.jsonl` (git-ignored; summaries are committed). `scripts/cost_report.py` rolls the ledger into `costs/summary.md` grouped by phase, arm, role, and model.

Record schema (one line per call):

```json
{"ts": "2026-09-09T17:00:00Z", "phase": "P0.3", "run_id": "smoke-extract-elf", "arm": "A0", "iteration": 0,
 "task": "extract-elf", "role": "task|evolver|judge|review|other", "backend": "claude|codex|copilot",
 "model": "claude-haiku-4-5-20251001", "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
 "reasoning_tokens": 0, "cost_usd": 0.0, "cost_source": "reported|priced", "premium_requests": 0,
 "wall_s": 0.0, "api_ms": 0, "ok": true, "note": ""}
```

Rules:

- `cost_usd` is API-equivalent USD. Claude Code reports it directly (`cost_source: reported`). OpenAI-family calls are priced from token counts using the price table in `scripts/prices.json` (`cost_source: priced`).
- Wall-clock for whole rollouts and iterations is recorded separately by the loop in `logs/<run_id>/timing.jsonl`.
- The ledger must exist and be exercised by the smoke test before any formal run (CLAUDE.md criterion 2).

Implementation: `harness/ledger.py`, `harness/backends.py`, and
`scripts/cost_report.py`. Run `uv run python -m scripts.cost_report`.

The implementation extends the schema with `call_id`, `raw_dir`,
`cache_write_tokens`, `return_code`, and `price_table_date`. `input_tokens`
includes cache reads and writes. Missing usage and costs are `null`, with
`cost_source: unknown`; they are never silently recorded as zero. Reasoning
tokens are a subset of output where available. One row is one CLI invocation;
provider-request counts may be greater than one. See
`docs/infrastructure.md` for pricing assumptions and incomplete usage handling.

The `openai_api` backend records one row per logical completion, with HTTP
attempts in `attempts`, `requested_model`, the served `model`, `pricing_model`,
`http_429s`, and request parameters. Retry headers and attempt latency are
retained without credentials. API responses and credential-free request bodies
are in `raw_dir`. The dated prices are estimates for Azure billing, as disclosed
in each new price row. Unknown or ambiguous usage is not silently priced at zero.

An initial Luna cache-write omission was repaired with
`scripts/reprice_api.py`. Original and corrected rows are retained in
`logs/cache-write-corrections.jsonl`; the corrected ledger rows include source
response hashes and original USD. Raw Harbor rewards/results were not changed.
See `docs/smoke_run.md` for the exact adjustment and reconciliation command.
