# Environment and Resource Survey

Date: 2026-09-09. Produced by the leader session (Phase 0, P0.1). Workers should treat this as ground truth about the machine and update it when something changes.

## Host

- Linux 6.17 (Azure VM), 8 vCPU, 31 GB RAM, 1.9 TB free disk, no GPU.
- Python 3.12.3, uv 0.11.2, Node 22, Docker 29.7.2, git 2.43, gh 2.92, pdftotext (poppler) present.
- Docker: socket is `root:docker`. The user was added to the `docker` group on 2026-09-09 (passwordless sudo). Until a new login session, run Docker commands as `sg docker -c "<cmd>"`. Verified: `sg docker -c "docker ps"` works.
- Git remote: `https://github.com/Julius-Woo/Nonverifiable-harness.git`, branch `main`.

## Quotas (user statement, 2026-09-09)

- Claude subscription: Max 5x; half is reserved for the leader (Fable 5.1) session. Experiments may use Claude Code only for low-volume roles.
- Codex: top tier. Copilot CLI: no cap.
- No Anthropic API key. Azure OpenAI / Foundry subscriptions were supplied on 2026-09-09 and are recorded in `.env` (git-ignored); see the Azure section below.

## Azure endpoints (probed 2026-09-09 with `scripts/azure_probe.py`; keys live only in `.env`)

All working endpoints accept `Authorization: Bearer <key>` or `api-key: <key>` on `<base>/openai/v1/{chat/completions,responses,models}`. Deployment names can differ from the served model (for example deployment `gpt-4o` on ai4mtest1 serves gpt-5.1), so always read the served model from the response. Per-deployment limits come from `x-ratelimit-limit-*` headers.

| `.env` index | Host | Working deployments (deployment -> served model) | Notes |
| --- | --- | --- | --- |
| EP0 | southcentralus.api.cognitive.microsoft.com | gpt55 -> gpt-5.5; gpt56luna -> gpt-5.6-luna; gpt56sol -> gpt-5.6-sol; gpt56terra -> gpt-5.6-terra | newest OpenAI line |
| EP1 | ai4mtest20.openai.azure.com | gpt-5.5; gpt-4o; gpt-4o-mini | |
| EP2 | ai4mtest3.openai.azure.com | gpt-5, gpt-5.1, gpt-5.2, gpt-54 (-> gpt-5.4), gpt-5-pro, gpt-54-pro, gpt-5.2-codex, o1, o3, o3-mini, o4-mini, gpt-4.1-mini aliases | |
| EP4 | sc-vs-mcbi748j-southcentralus.cognitiveservices.azure.com | DeepSeek-V3.2-Speciale, Kimi-K2.5, Kimi-K2.6, gpt-5, gpt-5.4, gpt-5.3-codex, gpt-5.1-codex-mini, gpt-5.4-pro-2, gpt-4o(-mini) | |
| EP6 | ai4mtest1.openai.azure.com | gpt-5-mini (1000 RPM / 1M TPM), gpt-5.1 (10000 RPM / 1M TPM), gpt-5, gpt-5.2, gpt-5.5, gpt-5.1-codex(-max), gpt-5.2-codex, o1, o3, o3-mini, o4-mini, gpt-4o(-mini) | task-model endpoint |
| EP7 | zhxia-mifqfhub-centralus.openai.azure.com | gpt-5-chat, gpt-5.1-chat (-> gpt-chat-latest), gpt-5-pro | |
| EP8 | ai-scxz9974854ai035672784725.openai.azure.com | DeepSeek-V4-Flash (250 RPM / 250k TPM), DeepSeek-V4-Pro, DeepSeek-V3.2(-Speciale), Kimi-K2.6 (100 RPM / 100k TPM), gpt-5, gpt-4o, gpt-4 (-> gpt-4.1) | evolver / judge / cross-judge endpoint |
| dead | ai4m-shared1.services.ai.azure.com; japaneast.api.cognitive.microsoft.com | 401 invalid key | |

Measured per-deployment limits (requests per minute / tokens per minute): gpt-5-mini 1000 / 1M; gpt-5.1 10000 / 1M; gpt-5.5 (EP6) 500 / 500k; gpt56luna 500 / 500k; gpt56terra 1000 / 1M; gpt-54 (EP2) 800 / 80k; DeepSeek-V4-Flash 250 / 250k; DeepSeek-V4-Pro 1000 / 1M; Kimi-K2.6 100 / 100k.

Role assignment is in `docs/PLAN.md` 9.1 and mirrored by the `TASK_*`, `EVOLVER_*`, `JUDGE_*`, `XJUDGE*_*` variables in `.env`. Raw probe output (no keys): `logs/azure_probe.json`.

## CLI model access (probed before the Azure keys arrived)

All model calls go through subscription CLIs. Each was probed on 2026-09-09 with a one-word prompt.

| CLI | Auth | Models available | Probe result | Usage reporting |
| --- | --- | --- | --- | --- |
| Claude Code 2.1.263 (`claude -p`) | subscription | `haiku` (= `claude-haiku-4-5-20251001`), `sonnet`, `opus` aliases; Fable 5.1 must not be used for experiments | Haiku: 1.5 s wall, US$0.0128 for a trivial call (6.3k cached system tokens) | `--output-format json` returns `usage`, `modelUsage`, `total_cost_usd`, `duration_ms` |
| Codex CLI 0.153.4 (`codex exec`) | ChatGPT login | `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`, `gpt-5.3-codex`, `gpt-5.3-codex-spark` (272k ctx except spark 128k) | `gpt-5.6-luna` low effort: 3.2 s wall | `--json` emits `turn.completed` with input / cached / output / reasoning tokens; `-o file` saves last message |
| Copilot CLI 1.0.83 (`copilot -p`) | GitHub login (Julius-Woo) | `gpt-6-astra`, `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol` confirmed; `gemini-3.8-flash` confirmed (user-supplied name; `gemini-3.8-pro`, `gemini-3.8-flash-lite`, `gemini-3.5-pro`, `gemini-3-pro`, `gemini-2.5-pro`, `gemini-3-flash` rejected); Claude models not available | 6 s wall; every call costs 1 premium request | `--usage-output-file` writes tokens, premium-request cost, per-model metrics |

Recommended invocation patterns:

```bash
# Task model as a text-only backend (prompt via stdin; tools disabled)
echo "$PROMPT" | claude -p --model haiku --output-format json --tools ""

# Evolver / judge (non-interactive, JSON event stream, usage in turn.completed)
codex exec -m gpt-5.6-luna -c model_reasoning_effort=low --json --skip-git-repo-check -C "$WORKDIR" "$PROMPT"
#   sandbox flags: -s read-only | -s workspace-write (add -c sandbox_workspace_write.network_access=true for network)
#   workers that need Docker: sg docker -c "codex exec -s workspace-write -c sandbox_workspace_write.network_access=true --add-dir ~/.cache --add-dir /tmp ..."

# Review / J-cross
copilot --no-custom-instructions --model gpt-6-astra -s --usage-output-file usage.json -p "$PROMPT"
```

Caveats:

- Claude Code `-p` with `--disallowedTools` is variadic; put the prompt on stdin or the flag swallows it.
- Do not pass `--help` to the Codex companion `task` helper; it launches a task.
- Copilot premium-request quota is finite; do not use Copilot for per-rollout judging. Measured premium cost per call: `gpt-5.6-luna` and `gpt-5.6-terra` = 1, `gemini-3.8-flash` = 14. J-cross re-scoring must be batched (several trajectories per call) or limited to final-harness trajectories.
- Docker inside a Codex worker works when the worker is launched under `sg docker -c` with the workspace-write sandbox and network enabled (verified 2026-09-09: `docker ps` and `docker info` succeed). The Claude Code auto-mode classifier rejects the Codex full-bypass sandbox mode, so use workspace-write.

## Meta-Harness

- Local copy: `/home/argustest/Meta-harness260728` (a research-math fork of `stanford-iris-lab/meta-harness`, upstream HEAD 2026-07-11). Relevant parts:
  - `reference_examples/terminal_bench_2/`: `meta_harness.py` (evolution loop: propose via Claude Code, `harbor run`, frontier update), `claude_wrapper.py` (proposer wrapper with logging), `agents/baseline_kira.py` (Terminus2 subclass using litellm; 1185 lines, too strong as a seed), `scripts/run_eval.sh`. Defaults assume Runloop/Modal and Opus 4.6; pinned to Harbor 0.18.
  - `experimental/harbor_meta_harness/`: controller that scores a harness on a task suite, forbidden-reference leakage checks per task, evaluation in a short-lived subprocess, `suites/tb2-easy.toml`. Directly relevant to Section 5 isolation.
  - `copilot_wrapper.py`, `sandbox.py`, `proposer_view.py` at the fork root: pattern for a non-Claude proposer with tool allowlists and gold-hiding views.
- No Harbor is installed in any local venv. `uvx --from harbor harbor` resolves Harbor 0.22.0 (89 packages). Default environment is `docker`; `-d terminal-bench@2.0` is the dataset id used by the paper code.

## Benchmarks

| Testbed | Source | Status |
| --- | --- | --- |
| T1 Terminal-Bench 2 | `laude-institute/terminal-bench-2` (89 tasks), via Harbor dataset `terminal-bench@2.0` | reachable; not yet downloaded |
| T2 GDPevo | `Prism-Shadow/GDPevo` (240 tasks, 24 groups, 5 train + 5 test each, rule evaluators) | reachable; healthcare groups 013-016, legal 017-020, finance 008-011 (anchor). Shallow clone target: `external/GDPevo` |
| T3 tau2-bench | `sierra-research/tau2-bench` | reachable; optional |

## Papers

All ten papers cited in `docs/PLAN.md` were downloaded to `docs/papers/` (PDF plus `pdftotext -layout` text; directory is git-ignored):
SEAL 2607.24300, RHO 2606.05922, Phantom Guardrails 2607.13083, HASE 2607.03935, HarnessX 2606.14249, Rethinking Harness Evolution Eval 2607.12227, SIGIL 2607.27309, GDPevo 2608.03764, Harness Updating Is Not Harness Benefit 2605.30621, Meta-Harness 2603.28052.

## Other local projects that may be reusable

- `/home/argustest/Auto-Meta260807/star-evolve`: earlier evolution experiment (not inspected in detail).
- `/home/argustest/Guarded-search`: containerized MCP broker with network-deny acceptance tests; useful pattern for the evolver container isolation checks.
- `/home/argustest/copilot_runner.py`: batch runner for Copilot CLI with concurrency, launch delay, and auth-failure abort.
