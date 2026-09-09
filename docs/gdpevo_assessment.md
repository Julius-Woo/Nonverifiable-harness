# GDPevo usability assessment for testbed T2

Assessment date: 2026-09-09. Scope: Phase 0, P0.4 of [PLAN.md](PLAN.md).

**Recommendation: conditional GO for T2 integration.** All 120 reference answers
in the requested healthcare, legal, and finance groups receive normalized score
1.0 from their local rule graders. All 12 business services run without Docker.
The Phase 0 GDPevo availability gate is satisfied. **Formal judge-driven evolution
is NO-GO until the controller enforces oracle isolation and fixes its scoring
contract.** These are integration requirements, not missing benchmark resources.

GDPevo supports studying judge/oracle divergence on synthetic, rule-bound business
work in medical and legal domains. Its oracle grades final artifacts against
encoded rules; it does not verify the agent's actual process, clinical outcomes,
or the quality of unconstrained professional advice. T2 findings must retain
that distinction.

## Evidence and provenance

The inspected checkout is [Prism-Shadow/GDPevo at
0999c0d1e6f59b1cb8464cac4e5ed856b5dcb9e4](https://github.com/Prism-Shadow/GDPevo/tree/0999c0d1e6f59b1cb8464cac4e5ed856b5dcb9e4).
The checkout was clean during the final evaluator audit; no upstream files were
modified. Its root `LICENSE` identifies Apache License 2.0. This assessment
concerns this snapshot, not an unpinned future release.

Evidence comes primarily from executing the checkout and reading its
[data board](../external/GDPevo/data/DATA_BOARD.md), task manifests, evaluator
sources, environment sources, and evaluation guides. The locally downloaded
[paper](papers/GDPevo_2608.03764.txt), Sections 3.1–3.3 and 4, supplies construction
and protocol context. The paper describes agent-generated environments and
rule hybridization: train tasks expose subsets of enterprise rules, and test
tasks recombine them within the same business environment. That is a construction
claim; this assessment did not independently certify every rule/task mapping.

A compact, answer-free record is retained in
[assessment_results.json](../scripts/gdpevo/assessment_results.json), including
per-group controls, runtime versions, service statuses, and the inventory digest.
Detailed task inventories, evaluator/config/reference SHA-256 hashes, subprocess
stdout/stderr, scores, and timings are in:

- `logs/gdpevo/assessment-20260909/evaluators-reviewed/summary.json`
- `logs/gdpevo/assessment-20260909/evaluators-reviewed/<group>/<split>/<task>/<case>.json`
- `logs/gdpevo/assessment-20260909/services-final/summary.json` and service logs.

The earlier `evaluators/` and `services/` directories retain discovery runs. An
initial adapter incorrectly treated group 010's raw point total as a normalized
score; the final run uses its explicit `normalized_score`. Initial service probes
also assumed uniform HTTP error codes/root routes and lacked the train-grader
path required by 018/020. Final helpers incorporate the observed contracts.
These initial failures are not benchmark reference-answer failures.

Raw logs and the external checkout are git-ignored, so another checkout must
regenerate them. Ignore rules are not access controls: all these artifacts,
including this assessment and its compact evidence, belong to the trusted
assessment/controller side and must stay out of solver/evolver/judge views.

## Inventory and proposed split

The full release has 24 groups / 240 tasks. The requested subset is 12 groups /
120 tasks, each with exactly 5 train and 5 test tasks, input prompts, JSON answer
templates, reference answers, and `eval/eval.sh`. All 12 manifests declare
`state_mode: read_only`.

| Group | Domain and work | Train / test | Reference scores | Service runtime |
| --- | --- | ---: | ---: | --- |
| 008 | Finance: wealth, tax, trust, estate planning | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 009 | Finance: close reporting, compensation, payroll | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 010 | Finance: investment strategy and portfolio risk | 5 / 5 | 10/10 at 1.0 | Flask |
| 011 | Finance: branch credit risk and lending review | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 013 | Healthcare: intake, coverage, referral, transfer | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 014 | Healthcare: payer authorization, appeals, claims | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 015 | Healthcare: EHR quality and record governance | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 016 | Healthcare: protocol-bound decision support | 5 / 5 | 10/10 at 1.0 | Python standard library |
| 017 | Legal: investigation and production review | 5 / 5 | 10/10 at 1.0 | Flask |
| 018 | Legal: court dispositions, orders, financial entries | 5 / 5 | 10/10 at 1.0 | Flask |
| 019 | Legal: licensing eligibility and compliance | 5 / 5 | 10/10 at 1.0 | Flask |
| 020 | Legal: M&A contracts and negotiation | 5 / 5 | 10/10 at 1.0 | Flask |

Healthcare supplies 20 search + 20 sealed tasks; legal supplies another 20 + 20.
Thus the planned main T2 domain has **40 search and 40 sealed tasks**. Finance
provides a separate pool of **40 possible anchor tasks**, not 40 automatically
selected anchors. Freeze a small, group-stratified anchor manifest before
experiments; never select anchors according to candidate performance or expose
anchor rollouts to the evolver. Retain unused finance tasks for an independent
transfer check if the budget permits. Anchor size remains a preregistration
choice; this assessment does not alter the plan's thresholds.

For concrete train/test pairs, 013/001 changes a June intake roster to a July
community roster; 016/001 changes the respiratory case; 017/001 changes the
investigation matter; 020/001 changes the acquisition project. These share a
business environment and decision structure. The held-out split is therefore
within-environment transfer, not unseen institutions or unseen professions.
Preserve native splits and report results by group and domain. With eight main
groups, task dependence matters: supplement the plan's paired task bootstrap
with a group-aware sensitivity analysis rather than treating 80 tasks as 80
independent environments.

## Offline grader results and scoring contract

The final audit made **840 subprocess invocations**: for each of 120 tasks,
reference, reference repeat, empty object, malformed JSON, JSON `null`, missing
file, and the released unfilled template. It used the public `eval.sh` entry point,
a 15-second timeout, and explicit absolute answer paths.

| Check | Observed result |
| --- | --- |
| Reference answers | 120/120 normalized scores exactly 1.0; zero execution errors |
| Repeat reference scores | 120/120 match, including optional `correct` field |
| Empty objects | 119 zero scores; 013/train/002 scores 0.1666666667 |
| Unfilled templates | Same pattern as empty objects |
| Malformed JSON | 108 clean zero-score exits; 12 nonzero exits |
| JSON `null` | 95 clean zero-score exits; 25 nonzero exits |
| Missing answer file | 108 clean zero-score exits; 12 nonzero exits |
| Timing | 24.17 seconds total; median invocation 27.9 ms |
| Model usage for evaluator audit | Zero calls, zero tokens, $0 |

All inspected grader entry points and their local helpers use Python standard
library modules plus shell utilities; Flask is unnecessary for grading. No
business service, API credential, GPU, or container is needed. The audit removes
inherited credentials/proxy settings from grader subprocess environments and
installs a Python audit hook denying `socket.*` events. A socket-creation
self-check confirms the hook is active. This demonstrates operation without
Python network access for these inspected graders; it is not an OS-level network
sandbox or protection against an adversarial shell script.

The schema needs deliberate handling:

1. **Use `normalized_score` when present, otherwise `score`.** Group 010 reports
   raw `score`, `max_score`, and `normalized_score`. Other selected groups report
   a 0–1 `score`. Preserve raw JSON; validate finite numeric scores in [0, 1].
   Do not clamp raw point totals into [0, 1]. The helper now handles both shapes.
2. **Do not use exit code as correctness.** A valid wrong answer generally exits
   zero. Conversely, invalid-input behavior differs. Malformed/missing inputs
   exit nonzero in all 10 group-010 tasks and two group-015 tasks; `null` adds
   failures in groups 011, 018, and 020. There were 49 such exits, all on controls.
3. **`correct` is optional.** Only 15/120 reference responses contain
   `correct: true`; the other 105 omit it. No false `correct` flags were observed
   on references in this snapshot. A few sources use exact floating equality,
   so retain the original field but derive a consistent adapter-level full-pass
   flag from the validated normalized score with a frozen rounding tolerance.
4. **Group 009 has a shared dependency.** Its `eval.sh` reads group-root
   `eval_common.py`, per-task `eval/config.json`, and the reference answer. Copying
   only each `eval/` directory is insufficient. Preserve these on the oracle side.
5. **Rubric details contain answers.** Evaluators expose fields such as expected
   values, mismatches, and per-point goals. Never pipe this JSON into an outcome
   or process judge. Even A0 should receive only the plan-authorized pass/fail
   signal plus the solver trace, not an accidental gold-answer dump.

The 013/train/002 empty answer receives 1/6 because SP006's expected
ready-to-schedule set is empty and a missing candidate field normalizes to the
same empty set. This is a measured partial-credit weakness, not a full-pass
exploit. Do not silently change upstream grading after observing outcomes.
Report both normalized rubric score and full-pass rate. For compatibility with
PLAN Sections 4 and 6, keep the preregistered pass/fail oracle as the primary
A0/fitness metric unless a protocol amendment explicitly adopts partial credit.

Before production, the controller should validate that an answer file contains
a JSON object. Missing/malformed/non-object submissions should receive the same
predeclared task-failure treatment in every arm, with a distinct status recorded.
Unexpected grader crashes, missing grader assets, invalid grader output, and
infrastructure timeouts need explicit error statuses; never infer success from
an empty stdout or silently drop these tasks from the denominator. The audit
helper deliberately preserves raw failures rather than acting as that full
production adapter.

## Local environment and API usability

Actual verification used the existing repository-root uv environment:
**Python 3.14.3**, Flask **3.1.2**, and Werkzeug **3.1.8**. The host resource survey
lists Python 3.12.3, but that is not the interpreter used by these runs. Upstream
Dockerfiles vary (including Python 3.11/3.12); this assessment does not claim a
cross-version compatibility matrix. The grader path requires no added Python
dependency. Optional service dependencies are recorded in
[requirements.txt](../scripts/gdpevo/requirements.txt).

The service helper copied each `env/` into a fresh temporary directory, bound to
loopback on an ephemeral port, checked health and one real business-data endpoint,
and terminated its own process group. It tested both explicit
`TASK_ENV_ENABLE_JUDGE=0` and `=1`: **24/24 final smoke cases pass**. Startup ranged
from 0.104 to 0.914 seconds; the full smoke run took 6.12 seconds. This measures
startup and a few requests, not solver latency, throughput, or safe experiment
concurrency.

| Groups | Local launch / adjustment |
| --- | --- |
| 008–011, 013–015, 018–020 | Run `bash setup.sh` with the copied `env/` as cwd |
| 016, 017 | Run the selected Python interpreter on `app.py` from copied `env/`; their `setup.sh` files hard-code `cd /app` |
| 018, 020, only when testing the optional train judge | Copy `train_tasks/` beside `env/` and set `TASK_GROUP_ROOT` to that temporary parent; an env-only copy returned HTTP 503 |

Health paths are `/api/health` for 008, 010, and 011, and `/health` for the others.
Data paths vary: `/patients` for 013, `/api/protocols` for 016,
`/api/matters` for 017, and `/api/deals` for 020 are examples. Read each
`env/endpoints.txt`; neither `/` nor a guessed uniform API prefix is reliable.
Several services offer read-only SQL queries over HTTP; a browser is optional,
not required for the exercised API interface.

All 12 services returned HTTP 404 for `/api/judge` when disabled. With it enabled,
all returned score 1.0 for `train_001`'s reference answer. All rejected `test_001`
with HTTP 400 or 403, depending on the group. These checks cover the specified
requests, not every route/encoding or a security audit. The helper enables the
judge solely to characterize it; production T2 solver services should keep it
disabled in all arms and let the trusted controller grade offline.

Read-only task metadata does not make the application an isolation boundary.
Administrative reset/reseed endpoints exist, and environment source/data may
contain answer-derived material or embedded train graders. Some setup scripts
regenerate data on every launch. Disposable copies protect the source snapshot
and prevent one run's state from affecting another; a real experiment must also
restrict which routes and files the agent can access.

## Agent interface required for T2

The reusable upstream evaluation workspace is an agent-driven workflow, not a
ready-made Harbor task adapter. Its base/self/fewshot/reflect-3 conditions are not
the same as this project's A0–A4 feedback arms. Reuse data and grader mechanics;
do not import fewshot gold demonstrations or reflect-3 oracle access into
judge-only evolution.

A minimal file-and-terminal ReAct solver can use this contract:

1. The trusted controller selects `(group, split, task_id)`, creates a clean
   attempt workspace, and copies only that task's `input/` contents. It substitutes
   `<TASK_ENV_BASE_URL>` in `input/prompt.txt` with the restricted business-service
   URL. Preserve payload filenames and relative paths; prompts refer to them.
2. Start an independent business-service copy outside the agent's filesystem,
   with judge access disabled. Allow the task's documented business GET/query
   operations through a route allowlist. Do not give the solver the environment
   source, raw database files, generators, or admin routes.
3. Give the solver its prompt, input payloads, permitted tools, and output path.
   It retrieves records/policies, performs calculations/reconciliation, and
   writes one JSON object to `answer.json` matching the task template. Structured
   decisions, code sets, aggregates, and sometimes numerical tolerances matter;
   prose alone does not satisfy the contract.
4. Record the unedited tool/assistant trace, final answer, backend/model version,
   attempt ID, token usage, tool/turn counts, latency, and timeout/parse status.
   A2 needs the actual trace. Do not invent a process trace from the final answer.
5. After the attempt, the controller runs the offline grader against an immutable
   copy of the answer. Store full results under an oracle-only path. Derive arm
   feedback separately: A0 pass/fail, A1/A2 judge signals, A3 self-preference,
   A4 the planned mixture. Anchor feedback goes only to acceptance.
6. Start every rollout with clean solver context and filesystem; retain only the
   permitted evolved harness/skill between tasks. Fix split manifests, limits,
   normalization, and failure policy before comparing arms.

No real clinical/legal systems or live domain data are required. The task model,
evolver, and LLM judges still require their configured model backends; this
assessment did not run a model-generated T2 solution, estimate its accuracy, or
measure its rollout cost. The currently available resources are sufficient for
building that adapter without buying an external benchmark service.

## Isolation gates and limits of the recommendation

The most direct leakage path is `POST /api/judge`: although it is train-only,
train is T2's search set, so enabling it exposes exactly the oracle feedback
A1–A4 are supposed to lack. Disabling the route must be enforced outside the
agent, together with preventing filesystem access to `judge_train_eval/`,
`judge_api.py`, task `eval/`, reference `output/`, solution `notes/`, construction
workspaces, and released experiment reports. A full `task_group.yaml` also
contains rubric goals and paths; create a filtered task descriptor instead.

Before formal T2 runs, require these reviewable acceptance checks:

- From the actual solver/evolver sandbox, oracle-file canaries cannot be read,
  test-task prompts/references cannot be enumerated during search, and outbound
  access cannot fetch the public repository or experiment answers. A writable
  scratch directory alone does not restrict reading the host checkout.
- Business-route allowlists block judge and administrative endpoints even if
  called directly; the agent cannot edit/restart the trusted service with the
  judge enabled. Test this from the actual tool boundary, not just a prompt rule.
- The judge receives only its fixed task/artifact/trace schema. Inspect the
  plan-required sample of traces for rubric/reference leakage; protect anchor
  trajectories and oracle results as well as filenames.
- Run a real seed-harness task end-to-end in at least one healthcare and one legal
  group with usage accounting, clean contexts, and sealed oracle logging. Freeze
  binary/partial-credit reporting and invalid-submission treatment first.

These are future integration gates; this assessment has not implemented or
certified the production sandbox. It has completed the requested local usability
assessment. Reference-perfect grading proves internal consistency, not that
agent-generated gold labels are clinically/legally valid or that the grader is
immune to gaming. Synthetic enterprise rules, public released answers, a small
number of groups, and schema/partial-credit effects limit external validity.
A2 versus oracle disagreement can reflect process quality, presentation, or
formatting as well as incorrect task decisions; inspect disagreement cases
before calling an effect “judge deception.”

## Reproduction and verification

Run from the repository root. Use a fresh output directory for each invocation;
the helpers refuse to overwrite an existing run.

```bash
# On a fresh checkout only: external/ is not tracked by this project.
git clone https://github.com/Prism-Shadow/GDPevo.git external/GDPevo
git -C external/GDPevo checkout --detach \
  0999c0d1e6f59b1cb8464cac4e5ed856b5dcb9e4

# Use the existing project-root .venv; on a fresh checkout, create it via uv sync.
uv sync --group dev
uv pip install --python .venv/bin/python -r scripts/gdpevo/requirements.txt

uv run --no-sync python scripts/gdpevo/audit_evaluators.py \
  --output logs/gdpevo/reproduction/evaluators
uv run --no-sync python scripts/gdpevo/smoke_services.py \
  --output logs/gdpevo/reproduction/services

uv run --no-sync pytest -q scripts/gdpevo/test_helpers.py
uv run --no-sync ruff check scripts/gdpevo
```

Both helpers accept `--source` and `--groups` (for example `--groups 13 17`).
The evaluator helper exits nonzero on a reference failure, while retaining all
negative-control observations. The service helper exits nonzero on a failed
health/data/judge contract. Both are assessment tools running trusted upstream
code, not sandbox launchers for untrusted evolved harnesses.

Verification completed: 840 final evaluator invocations, 24 final service cases,
and eight focused pytest cases covering raw/normalized scores, zero-score
success exits, nonzero exits with a score, invalid result shapes/types, NaN,
timeout reporting, and cleanup of background grader children. Ruff lint and
formatting checks pass. After the concurrent
infrastructure work created the project test suite,
`uv run --no-sync pytest -q tests scripts/gdpevo/test_helpers.py` passed
**35 tests**. The earlier unrestricted `uv run --no-sync pytest -q` run
reported 6 failures, 10 passes, and 9 passing subtests: because the configured
`tests/` directory was absent, pytest recursed into the external checkout and
ran unrelated group-022 environment tests. Those failures concern SQL/auth/reset
HTTP responses in a group outside this assessment; their root cause was not
diagnosed or repaired. The targeted and project suites now pass; the out-of-scope
external suite remains unresolved. No commit or publication was made.


Independent review used Copilot CLI 1.0.83 with `gpt-6-astra`, high effort.
The installed CLI's model picker listed GPT-6 Astra and returned usage identifying
`gpt-6-astra`; no model fallback was necessary. It identified three issues:
background-child cleanup on grader timeout, omitted fresh-checkout instructions,
and stale test-status prose. All were resolved. After the subprocess fix, all
840 grader cases were rerun; scores, correctness fields, and errors match the
previous run, and the background-child regression passes. The reviewer did not
perform a second review of the fixes.

The review returned complete findings and usage, but exited 1 because it could
not persist session events to the read-only `~/.copilot/session-state/` directory.
This was a local CLI storage error, not an approval rejection. Its exact stdout,
stderr, usage, and timing are saved under
`logs/gdpevo/assessment-20260909/review/`. Usage was 24,554 input tokens (including
24,551 cache-write tokens), 2,103 output tokens (including 1,808 reasoning tokens),
1 premium request, and 60.30 seconds wall time. The cost ledger records
$0.412068 API-equivalent using the repository's [rate table](../scripts/prices.json),
separately from subscription billing, and preserves the unsuccessful CLI status.
Across discovery and final verification there were 2,520 grader invocations
(73.21 seconds total), all without model calls; model review usage is additional.
The main assessment-agent session's token usage is not available to these helpers.
