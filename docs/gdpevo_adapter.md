# GDPevo adapter — T2 experiment preparation

The 2026-09-10 P1.10 groundwork is recorded below for provenance. See the
T2 preparation section for the current runner and integrated acceptance.
The unchanged harness entry point was exercised through
real Docker boundaries on **013/train/001 (healthcare)** and
**017/train/001 (legal)**, using the TASK endpoint and `gpt-5-mini`, low
reasoning, 4,096 completion tokens, and a 24-call cap. Both attempts wrote valid
JSON objects; both received oracle **0**, with native normalized score **0.0**.
These are recorded seed failures, not missing runs. No paid retries or
selection of better outcomes were performed.

The original adapter smoke suite reported seven helper checks and 174
repeated service checks across 12 filtered images. R5 established that these
were **not** a seven-row integrated Section 5 pass. All 120 frozen reference controls
scored 1.0. The two known full-credit blind spots are rejected by grader-v1.
The complete project test suite, including the opt-in Docker test, passed
250 tests in the final verification.

## Architecture and ownership

`gdpevo/` is a trusted host-controller package. It imports `harness.seed.run_seed`
and `harness.openai_api.OpenAIAPIBackend`; no harness files were edited for
P1.10. `Attempt.exec()` implements the environment interface used by the seed's
terminal, read-file, and write-file actions. The seed's JSON action protocol,
history, observation truncation, finish handling, and step cap are reused.
The adapter supplies the task prompt and mechanical workspace instructions.
There is no model-specific solver prompting or answer repair.

```text
Trusted host controller
  TASK API calls + per-call cost ledger
  unchanged run_seed -> docker exec --user 1000:1000 -> solver
  frozen grader + native grader -> oracle/<run_id>/result.json
  separate arm_feedback() -> restricted arm-facing JSON

        internal front network          internal back network
  solver ---------------- gateway ---------------- business service
           TCP 8080 only                   TCP 8080
```

Each attempt gets three uniquely named containers and two unique internal
Docker networks. No ports are published. The service has only the back
network; the solver has only the front network; the gateway connects both.
The solver's firewall permits outgoing IPv4 TCP only to that attempt's gateway
on port 8080 and incoming replies only from that gateway. IPv6 is dropped.
External DNS, Internet access, the bridge host, and direct service access were
tested from inside the solver and refused. `/etc/hosts` resolves `gateway`, so
the solver does not need Docker's embedded DNS.

All attempt containers have read-only root filesystems, no-new-privileges, a 512 MiB
memory limit, one CPU, and a 128-process limit. Service and gateway run as UID
1000 with all capabilities dropped. The solver initializer briefly has only
NET_ADMIN/SETUID/SETGID/SETPCAP to install its firewall, then drops privileges.
Every solver action explicitly executes as UID 1000; effective and permitted
capability sets were verified as zero. Docker's default seccomp profile stays
enabled. No Docker socket, host checkout, model credentials, or `.env` is
mounted or included in an image.

### Image contents and pinning

All created image tags start with `nvh-gdpevo-`. The runtime uses immutable image
IDs from [`images.lock.json`](../docker/gdpevo/images.lock.json), rather than
resolving mutable tags. The base is Linux amd64 Python 3.12.12 slim Bookworm,
pinned to upstream manifest digest
`sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301`.

| Image | Additional contents |
| --- | --- |
| base | bash, coreutils, curl, jq, Python, iptables, UID 1000 |
| solver | firewall initializer, privilege-drop helper, empty `/work` mount points |
| gateway | standard-library HTTP proxy; per-attempt route config mounted read-only |
| service-008…020 | filtered business application and data; pinned Flask 3.1.2/Werkzeug 3.1.3 and pinned transitive Python packages |

APT packages are resolved during build; the resulting runtime image IDs are
frozen. A clean rebuild may produce different IDs and requires acceptance to
be rerun. Build logs and complete image metadata are under
`logs/gdpevo/p110-images-final/`. The solver's installed Python includes pip,
but it has no package-install network access and its root filesystem is
read-only. This is a container boundary, not a VM or a defense against a
host administrator or kernel compromise.

### Mounts and staging

| Solver path | Access | Source |
| --- | --- | --- |
| `/work/input` | read-only | current task's prompt and payloads, preserving paths |
| `/work/environment_access.md` | read-only | generated mechanical API descriptor |
| `/work/task.json` | read-only | filtered group/split/task/input/answer/API descriptor |
| `/work/scratch` | writable | fresh attempt-specific directory; also `TMPDIR` |
| `/work/answer.json` | writable file | fresh empty submission file, mounted separately |

`stage_task(group, split, task_id, destination)` creates a fresh directory and
rejects reuse, invalid IDs, unexpected input files, and symlinks. It replaces
`<TASK_ENV_BASE_URL>` in the prompt with `http://gateway:8080`. Payload bytes and
relative paths are retained. It never copies full task manifests, evaluators,
references, notes, judge modules, construction workspaces, or experiment
reports. In particular, an upstream URL placeholder inside a payload remains
unchanged; the resolved prompt and access descriptor are authoritative.

The checkout has no pre-generated `environment_access.md`. The descriptor
follows the mechanical contract described in the upstream evaluation workflow,
using `env/endpoints.txt` and inspected application request handling. It lists
business GET routes and documented query POSTs only, omitting health and
orchestration routes. It preserves benchmark authentication and JSON shapes:

| Group | Query route | Authentication | Required query field |
| --- | --- | --- | --- |
| 013 | `/query` | none | `sql` |
| 014 | `/sql/query` | Authorization Bearer benchmark token | `sql` |
| 016 | `/api/query` | X-Clinic-Token | `sql` |
| 017 | `/api/query` | X-API-Key | `sql` |
| 019 | `/api/sql` | X-Task-Token | `query` |
| 020 | `/api/query` | benchmark `token` in JSON body | `sql` |

Required/optional JSON types, supported SQL statement forms, content type,
runtime benchmark credentials, and runnable `SELECT 1` examples are staged.
All six query examples were executed successfully from solver containers.
These benchmark credentials are separate from the host-only TASK API key.

`stage_service()` creates a new, filtered build context, never a Docker build
from the upstream group root. AST filtering physically removes judge imports,
handlers, registrations, and administrative reset/reseed routes. Judge data,
judge evaluator directories, environment evaluator helpers, and construction
manifests are absent. Data-generator bodies can contain gold answers, so only
the mechanical constants needed by the application are retained; generation
functions are replaced with fail-closed stubs. Prebuilt business records are
copied, and orchestration metadata is filtered. The upstream tree is unchanged.

The gateway forwards to one fixed upstream address, preserves only necessary
request headers, and checks both method and path against its allowlist. It
rejects encoded or ambiguous paths, absolute proxy URLs, administrative routes,
unsupported methods, oversized bodies, and ambiguous request framing. Business
SQL restrictions remain enforced by the upstream application. Even if a judge
flag were set, there is no judge module or route in the built service.

**Held-out business records are intentionally shared.** A task group's service
contains records for both train and test tasks. The sealed split is sealed by
task text, payloads, references, and grader, not by business-record membership.
This follows decision A5 and is a benchmark property.

## Frozen oracle and controls

Source: GDPevo commit `0999c0d1e6f59b1cb8464cac4e5ed856b5dcb9e4`.
The 12 selected groups are 008–011 and 013–020: 120 tasks total.
`gdpevo/grader_v1/` contains each task's evaluator code and reference assets,
plus shared `eval_common.py` where required. The upstream Apache license is
retained in `gdpevo/LICENSE.upstream`.

**grader-v1 SHA-256:**
`12997be4c3781c5f3366d9d102329edac3771077a8e52b1329cda1ba0e763f6e`

Hash definition: sorted relative POSIX path, NUL, file bytes, NUL, concatenated
and SHA-256 hashed. Python bytecode/cache files are excluded. The manifest is
outside the hashed tree. The controller verifies the hash before controls and
grading. A test compares every frozen file against upstream and permits changes
only in these two files:

- `task_group_015/train_tasks/001/eval/eval.py`: compare every expected contact
  value with its corresponding field, replacing containment in serialized JSON.
- `task_group_018/train_tasks/001/eval/eval.py`: reject duplicate or malformed
  fee items before set comparison can hide them; check that the submitted case
  total agrees with both the expected total and the sum of submitted fee rows.

| Control | grader-v1 | Upstream native |
| --- | ---: | ---: |
| All 120 reference answers | 120/120 at 1.0 | 120/120 at 1.0 |
| TG015 train/001: swap phone and fax | 0.95 | 1.0 |
| TG018 train/001: duplicate fee item, leave total unchanged | 0.722222 | 1.0 |

Controls completed in 11.49 seconds, with no model usage. Full subprocess
results are in `logs/gdpevo/p110-controls-final/controls.json`. Initial control
runs exposed missing runtime reference dependencies and one grader's path
resolution incompatibility with memfd paths; both were resolved before freezing
the final hash and running either model attempt. Initial logs are retained.
The host grader interpreter was Python 3.14.3 in the project-root uv environment.

The current binary oracle uses `normalized_score` when present, otherwise
`score`, and requires a finite numeric value in **[0,1]** before applying
`abs(s - 1) <= 1e-6`, a valid JSON object and a valid grader execution. Duplicate
keys and non-finite JSON numbers are rejected. The T2 runner's A9 lifecycle and
its scheduled/eligible denominator fields are described below. The original
P1.10 runner did not yet implement grader retry/exclusion or reject scores
slightly above one; its historical measurements remain unchanged.

### Oracle-side execution

The controller stops the solver before reading the bound answer file, refuses
symlinks, and limits answers to 4 MB. It copies the bytes into a private host
snapshot, makes it mode 0444, and verifies unchanged bytes after each grader.
The solver and gateway have no mount or route to these copies. The archived
`oracle/<run_id>/answer.json` is also mode 0444 and has a recorded SHA-256.
Immutability is enforced against the solver; the trusted host owner can still
change file permissions. No host-admin-resistant immutability is claimed.

Both graders execute on the host with sanitized environments, no inherited
model credentials, a Python socket audit guard, and a 15-second timeout that
kills the grader process group. The grader code is trusted, inspected code;
the Python network guard is not an OS sandbox for arbitrary graders. Raw output
stays under git-ignored `oracle/`. `arm_feedback()` produces a separate view
containing only the authorized score and filtered solver trace; raw rubric
details and expected values are never passed to the arm view.

## Historical Section 5 smoke evidence — not an entry gate

Primary log: `logs/gdpevo/p110-acceptance-final/acceptance.json`.
It records container and network inspections, actual command responses,
per-arm mounted views, and **174/174** repeated service checks: 14 checks per
group across 12 groups plus six query examples, not 174 distinct routes.

| Section 5 row | Historical check | Evidence limit |
| --- | --- | --- |
| 1: oracle files | Random host oracle canary unreadable through solver and arm-container tools; separate canary check in each paid rollout | Canary smoke passed |
| 2: trace contamination | 20 adversarial file-read traces across GDPevo/TB2 oracle paths under `/work` and `/app`; reads refused and observations filtered | 20 probes; not task rollouts |
| 3: judge inputs | Closed keyword-only task/trace/answer schema rejects an `oracle` argument; no grader filesystem input | Helper check only |
| 4: uniform evolver prompt | A0–A4 prompt files read inside their containers; templates identical after replacing score-source text | Templates; not dispatched prompts |
| 5: anchors | Anchor and sealed feedback rejected; no anchor mount in arm containers | Helper check only |
| 6: cross-arm isolation | Five distinct containers and directories; each tool view contains only its own arm marker and feedback | Fixture views; no cross-arm write probe |
| 7: caches | Distinct `user` and `prompt_cache_key` per run/arm/role; config inspected in each arm container; both real API runs accepted scoped parameters | Provider enforcement unverified |

The 20 traces in row 2 are deliberate tool-boundary probes, not 20 paid task
rollouts. Both actual seed trajectories were also inspected. The future
evolution/judge controller must use these interfaces and repeat the matrix on
its real integrated roles; these fixture containers do not constitute a full
evolution experiment. Provider-internal cache partitioning cannot be inspected;
the evidence establishes distinct request keys and accepted API parameters.

Additional checks include 403 responses for judge/reset/reseed/operator routes
and encoded judge paths, read-only input enforcement, missing host `.env` and
Docker socket, zero effective/permitted capabilities, direct-service denial,
Internet/bridge denial, and external DNS denial. All 12 filtered applications
started successfully. Every started container and network was cleaned up;
no unrelated Docker resources were pruned.

## Real rollout results and accounting

Both requests used deployment `gpt-5-mini`; all 16 API responses identified
`gpt-5-mini-2025-08-07` and returned HTTP 200. There were no unpriced attempts.
Pricing uses the project's recorded price table, not a claim about a final
provider invoice. Per-call rows include token counts, cache usage, model,
request parameters, request IDs, latency, and priced USD.

| Metric | Healthcare 013/train/001 | Legal 017/train/001 |
| --- | ---: | ---: |
| Run ID | `p110-healthcare-013-train001` | `p110-legal-017-train001` |
| Calls / cap | 9 / 24 | 7 / 24 |
| Input tokens | 22,739 | 55,871 |
| Cached input tokens | 16,256 | 38,528 |
| Output tokens | 1,584 | 3,013 |
| API cost, USD | 0.00519515 | 0.01132495 |
| API wall time, seconds | 27.321 | 41.165 |
| Rollout wall time, including boundary lifecycle | 30.176 | 43.995 |
| Total wall time, through oracle | 30.259 | 44.078 |
| Valid JSON object / grader execution | yes / yes | yes / yes |
| grader-v1 / native normalized score | 0.0 / 0.0 | 0.0 / 0.0 |
| Binary / denominator | 0 / 1 | 0 / 1 |
| Host canary inaccessible | yes | yes |
| Solver status | tool_failure | finished |

Total recorded model cost: **USD 0.01652010**, below the USD 10 task ceiling.
Each backend had a USD 1 rollout limit; the available backend shared-budget
hook enforced USD 10 through `logs/gdpevo/p110-budget.json`. Both attempts
performed container actions, so no-action termination was 0/2. The fixed
denominator result is 0/2; neither failure was excluded.

Healthcare read a guessed, nonexistent payload filename, then recovered the
actual roster file. Its SQL query used a nonexistent table and received a
business API error; it nevertheless wrote an incorrect answer. The nonzero
file-read command also triggers the adapter's tool-failure classification.
Legal retrieved several real business endpoints and read saved records, then
wrote an answer that failed the grader's required artifact checks. No grader
feedback was exposed to either solver, and neither answer was manually edited.

For each run, `logs/gdpevo/<run_id>/` holds `trajectory.jsonl`, `ledger.jsonl`,
`calls/`, `canary.json`, `harness_hashes.json`, `summary.json`, separate
`arm_feedback.json`, and the attempt's mount/network/service logs.
`oracle/<run_id>/` holds the immutable-to-solver answer and both complete grader
results. `logs/gdpevo/rollouts.jsonl` contains one summary row per attempt.
The harness hashes record the shared workspace's imported implementation;
concurrent work elsewhere in the repository was left untouched.

Final verification: `GDPEVO_DOCKER_TESTS=1 uv run pytest -q` passed **250 tests
in 15.10 seconds** (`logs/gdpevo/p110-pytest-final.log`). The required plain
`uv run pytest -q` also passed: **249 passed, 1 optional Docker test skipped**
in 12.20 seconds (`logs/gdpevo/p110-pytest-required.log`). Ruff passed for the
new adapter, scripts, Docker Python helpers, and tests; unchanged vendored
evaluators are excluded from formatting. `p110-final-audit.json` records an
independently regenerated, matching grader manifest and reconciled per-rollout
ledger/oracle results. The upstream checkout remains clean; `oracle/` and
`logs/` remain git-ignored. Final Docker checks found no remaining
`nvh-gdpevo-` containers or networks.

## Historical P1.10 reproduction

Run from the repository root with the existing uv environment and Docker group
membership. No Claude, Codex, or Copilot CLI is used. The runner uses the TASK
endpoint from `.env`; it never prints or stages model credential values.

```bash
uv run python -m scripts.gdpevo.build_images --output logs/gdpevo/NEW-images
uv run python -m scripts.gdpevo.controls --output logs/gdpevo/NEW-controls
uv run python -m scripts.gdpevo.acceptance --output logs/gdpevo/NEW-acceptance
GDPEVO_DOCKER_TESTS=1 uv run pytest -q
uv run python -m scripts.gdpevo.run_seed --group 13 --run-id NEW-healthcare \
  --controls logs/gdpevo/NEW-controls/controls.json \
  --acceptance logs/gdpevo/NEW-acceptance/acceptance.json
uv run python -m scripts.gdpevo.run_seed --group 17 --run-id NEW-legal \
  --controls logs/gdpevo/NEW-controls/controls.json \
  --acceptance logs/gdpevo/NEW-acceptance/acceptance.json
```

The seed runner defaults to the successful P1.10 control and acceptance logs at
their recorded `p110-*-final` paths. Overrides are validated against the frozen
grader manifest, selected group, and locked image IDs; rebuilding images
invalidates old acceptance evidence. Fresh run IDs are mandatory and existing
run directories are never overwritten. To regenerate the frozen
grader into a new directory, call `scripts.gdpevo.freeze_grader.freeze()` and
compare its returned hash; do not overwrite the frozen version in place.

## T2 preparation: R5 findings 1–8 (2026-09-11)

The supported experiment entry point is now `python -m gdpevo.runner
--manifest <path>`. It imports the unchanged `harness.seed.run_seed`, uses the
actual solver/gateway/service Docker boundary, and shares T1's manifest
validation, evidence-v3 exporter, judge contracts, prompt renderer, endpoint
limiter, and durable request/receipt accounting. The old `scripts.gdpevo.run_seed`
command is retained as historical P1.10 tooling, not the Phase 3 launch path.
No files under `harness/`, `evolution/`, or `external/GDPevo/` were changed.

### Manifest and scheduling

`gdpevo.runner.defaults(experiment)` extends `evolution.manifest.defaults()`
and `validate()` calls the same T1 schema validator. The defaults are proposed
experimental settings, not ratification of AD1/AD9/AD10/AD13. The default pool
contains all **40 search, 40 sealed, and 40 finance-anchor** tasks: healthcare
013–016 and legal 017–020 use train for search and test for sealed; finance
008–011 uses both splits as the anchor pool. Select anchor IDs explicitly in
the manifest; the acceptance diagnostic uses only 008/train/001.

| Fields | T2 contract |
| --- | --- |
| `schema_version`, `experiment`, `purpose`, `arms`, `seeds`, `T` | T1 schema v1; unique experiment identity; infrastructure runs are authorized here. Formal pilot/Phase 3 launches remain gated. |
| `tasks.search`, `.anchor`, `.sealed` | Explicit lists of `GGG/train-or-test/TTT` IDs; role and pool membership checked before dispatch. |
| `schedule` | Optional explicit array of `{arm, seed, iteration, task, replicate}`. IDs are deterministic hashes of the entire tuple. Duplicate slots are rejected. |
| omitted `schedule` | Cartesian product of arms × seeds × iterations 0…T × selected tasks × `replicates` (default 1). This is measurement scheduling, not an implementation of the full proposal/selection loop. |
| `task_model` | `name=gpt-5.6-terra`, `deployment=gpt56terra`, `endpoint_prefix=TASK_ALT2`, low reasoning, JSON, completion allowance 8192, max calls 24, API timeout 180 s. Deployment is read from the manifest, overriding `.env`'s model default. |
| `tool_failure_reading` | `executor` (AD10 proposal) or `strict`. Executor/protocol failures veto success in both; strict also vetoes ordinary nonzero command exits. |
| `api_timeout_policy` | `infrastructure` (AD13 proposal) or `failure`. Only a sole first API call timing out before an action qualifies for the timeout replacement under the proposed rule. |
| `oracle_range_policy` | Explicit `unit_interval`: enforce 0 ≤ s ≤ 1 before tolerance. Other policies are refused; AD9 is not silently ratified. |
| `transport_max_retries` | Frozen to zero; A9 controller logic owns replacements and grader/judge retries. |
| `budget` | T1 phase estimate, guard, wall-clock hours, per-attempt rollout USD and evolver-session USD. Conservative pre-dispatch reservations include uncertain charges. |
| `docker_concurrency`, `memory_min_available_gib` | Two concurrent attempts for this run; up to four supported. Admission requires 6 GiB **MemAvailable**. All Docker resources have the `nvh-gdpevo-` prefix. |
| `rollout_timeout_s`, `command_timeout_s` | 1200 seconds per seed attempt and 30 seconds per command. |
| `controls_path`, `acceptance_probes` | Matching frozen-grader controls are required; optional real solver probes execute before seed actions and do not count as solver failures. |
| resolved `hashes`, `grader`, `providers`, `resolved_sha256` | Controller, boundary, exporter, seed, prompt, price, image-lock and hidden-column audit identities; exact comparison on resume. No credentials are persisted. |

Any supported arm, including A4, A3-native/A3-loop and matched C-TTS arms, can
schedule a measurement of any selected group/split/task. Search A0 exports
only its binary signal; A1/A2 use the corresponding T1 judge, and A4 averages
its two judge calls. A3 measurements produce v3 trajectories with
`feedback_status=pairwise_signal_required`; the outer A3 paired-baseline
ranker must supply the signal. They never substitute the oracle for preference.
This runner does not invent proposals, acceptance cycles or A3 baselines.
Seed IDs partition local identities and workspaces; the provider is not
claimed to support deterministic sampling seeds.

### A9 accounting and recovery

`runs/<experiment>/rollouts.jsonl` is an atomically replaced materialized
view with **one row per scheduled tuple**, all written before any attempt.
`rollout_events.jsonl` is the fsynced history; do not sum its repeated versions.
Every row has `denominator=1`. `eligible_denominator=0` and a typed logged
exclusion retain excluded slots visibly; scheduled/interrupted slots have no
invented label. Analysis must report scheduled, completed, eligible, excluded
and missing counts rather than silently averaging only surviving traces.

| Condition | Disposition |
| --- | --- |
| Solver wall timeout, exhausted seed, executor/protocol failure | Failure, binary 0, denominator retained. |
| Oversized (>4,000,000 bytes), unreadable, invalid JSON or non-object answer | Typed solver failure with binary 0; the snapshot cannot abort slot accounting. |
| Primary grader launch/timeout/parse/exit failure | Retry once against the same frozen snapshot; then exclude with a logged count. |
| Secondary native grader failure | Retry once and log separately; it cannot erase a valid primary result. |
| Infrastructure failure | At most one new solver attempt under the same logical slot, then exclusion. Interrupted in-flight attempts consume their retry identity. |
| Judge failure | Retry once, then no feedback score and a logged failure; acceptance cannot consume a missing score. |
| Budget/deadline guard | Stop dispatch. Reserved/scheduled identities persist for reconciliation; budget stops are not a source of free retries. |

Existing solver failures remain failures; a secondary grader error cannot
convert an invalid/non-object submission into an exclusion.

The solver is confirmed stopped before reading its answer. Grader dispatch
intents and result records are durable, and a grader-only interruption resumes
from the read-only answer without another solver call. Docker resource names
are journaled before creation so interrupted attempts can clean their own
resources before replacement. Resume rejects changed slot sets and resolved
conditions. A completed resume dispatches no new requests.

`costs/<experiment>/` uses T1 `AccountedBackend`, `PhaseGuard` and `reconcile`.
Each physical HTTP attempt has a reservation and request intent before network
dispatch, then a durable response/receipt before the backend ledger append.
Receipts repair lost ledger rows. Reports separate physical requests, known
prices, retained unresolved reservations, missing cache-price details and
per-arm exclusions. Both solver and judge transports explicitly use zero
HTTP retries. Whole-rollout infrastructure replacements have separate bounded
attempt scopes inside the same phase guard.

Raw answers, hardened/native results, and anchor acceptance inputs stay in
`oracle/<experiment>/`. Sealed and anchor oracle labels are absent from public
rollout summaries and stdout. Only search feedback is written under
`feedback/<experiment>/seed-<seed>/<arm>/`; the trusted anchor gate exports
exactly `{"accepted": <bool>}`. The evolver mounts only its own directory.

### Hidden-column audit and staging

[`data/gdpevo_hidden_columns.json`](../data/gdpevo_hidden_columns.json) records
all 12 generators, eight shipped SQLite databases, **104 tables and 936 column
instances**, source hashes, row counts, public-column allowlists and generator
annotation evidence. Four groups use JSON business records rather than SQLite;
the audit also inspected their field names and all data/generated JSON keys.

Three hidden column instances were confirmed, all named `target_group` in
TG019. They label construction membership rather than business facts:

| Table | Rows | Non-null hidden annotations removed |
| --- | ---: | ---: |
| `contractor_applications` | 111 | 58 |
| `liquor_applications` | 33 | 14 |
| `alcohol_licensees` | 91 | 44 |

Staging checks the pinned source database hash, removes annotation indexes and
columns, checks every resulting public-column list and vacuums the database.
The column and its contents are physically absent, so aliasing, positional
projection or compound SELECT cannot recover it. All public business rows and
remaining values are preserved. No other SQLite construction columns were
identified. Domain fields such as financial expected returns, clinical program
hints, legal review classifications and policy guidance remain available;
the audit explains these decisions rather than treating every `target` or
`expected` name as hidden.

JSON manifests additionally lose `primary_matters`, nested `task_id` and other
`task_*` construction mappings. Existing judge/construction JSON exclusions
remain. Task staging still never copies `task_group.yaml`, including rubric
fields, and generator bodies are never included in service images. Shared
train/test business records remain an intentional A5 benchmark property.

All 12 service images were rebuilt. Six live TG019 gateway probes cover the
old alias/UNION exploit and the legitimate public-column variant for all three
tables: old widths are refused; public widths succeed. Evidence:
`logs/gdpevo/t2-hidden-service-260911/checks.json`.

### Integrated acceptance run

The clean qualification run is `t2-accept-260911-final`. It schedules 20 search
rollouts (five replicates of each of 013/train/001 and 017/train/001 under each
of A0/A1), two finance-anchor measurements and two sealed measurements. Every
solver run uses the actual isolated solver container and business gateway;
there are no fixture trajectories in the final matrix. The seed solver code,
model condition, images and trusted controller/exporter hashes are pinned in
`runs/t2-accept-260911-final/manifest.json`.

A preceding diagnostic, `t2-accept-260911`, was halted when an added crash-window
regression showed that interrupted grader recovery tried to rewrite a read-only
snapshot. Its stop was unrelated to scores; its original labels and 24 scheduled
slots remain archived. It is not included in the final acceptance sample.
Diagnostic accounted usage is **$0.9967905**, including **$0.206813** in unresolved
reservations. The clean run has a separate **$9** guard, so the combined
conservative ceiling remains below $10. The recovery bug was fixed and its
regression passed before the clean run began. An outer-JSON scanner false
positive for business fields called `notes` was also corrected before that run;
checks inspect actual scalar contents using the evidence-v3 pattern contract.

The shared workspace received concurrent changes to eight `evolution/a3*.py`
files and `evolution/loop.py`. This task did not modify them. The run-specific
`runs/t2-accept-260911-final/complete_acceptance.py` requires exactly A0/A1,
checks that all nine modules are unloaded before and after qualification, and
records their exact names and hashes in `acceptance-controller.json`.

The first integrated acceptance attempt then exposed two adapter defects:
DeepSeek rejects the OpenAI-specific `prompt_cache_key` parameter, and the base
Python image exits immediately when used as a T1 candidate workspace. The runner
now sends only the supported `user` namespace for judge/evolver requests; task
requests retain their original parameters. A dedicated candidate Dockerfile
keeps the workspace running, and a positive liveness check precedes isolation
probes. Both fixes are explicitly hashed in `acceptance-amendment.json`.
The pre-fix sources and failed acceptance evidence are preserved under
`acceptance-before-fixes/`. All task rollouts, labels and retry counts remain
unchanged. The standard runner still rejects full-tree hash drift on resume;
this qualification used an explicit post-rollout amendment, not a rewritten
solver manifest.

The pre-fix A1 search judges exhausted their two controller attempts on the
unsupported parameter (18 rejected requests for nine eligible search slots).
Their `judge_failed` flags and missing feedback are retained. They were not
re-judged beyond A9. The corrected transport was subsequently verified by a
successful real judge dispatch of injected-and-sanitized evidence, eight real
cache-canary requests, and two real evolver dispatch sessions. These are
separately scoped acceptance diagnostics, not replacement search measurements.
Thus this run establishes the listed boundary properties, not a successful A1
evolution iteration. A fresh measurement run with the final controller remains
necessary before interpreting A1 search feedback.

#### Measured result (2026-09-11)

**Section 5: 6/7 pass; overall gate remains false.** The seventh row is
`unverified_provider_enforcement`. Machine-readable evidence is
[`section5_matrix_t2.json`](../runs/t2-accept-260911-final/section5_matrix_t2.json).

| Section 5 row | Result | Real integrated evidence |
| --- | --- | --- |
| 1. Oracle-file isolation | Pass | Healthcare/legal solver canaries and live candidate workspaces; own-workspace positive control succeeds. |
| 2. Complete traces and tool boundary | Pass | 20 terminal search traces scanned automatically; live gateway, direct-service, firewall and public-GDPevo GitHub/raw egress probes. |
| 3. Serialized judge isolation | Pass | All 24 actual trajectories ingested as 48 A1/A2 payloads; private injected strings absent; one corrected real judge response succeeds. |
| 4. Dispatched evolver prompt uniformity | Pass | Two actual wire requests match the T1 renderer and differ only in score-source text. |
| 5. Anchor/sealed isolation | Pass | Two real finance anchors and two real sealed measurements; only search feedback and a closed-schema acceptance bit in arm-facing directories. |
| 6. Cross-arm reads and writes | Pass | Real networkless candidate workspaces refuse exact-path and proc-root reads and cross-arm writes; own candidate writes succeed; feedback is read-only. |
| 7. Provider cache partition | Unverified | Eight successful prime/probe calls disclose no other session's canary; this does not prove provider-side enforcement. |

There are **24 scheduled and terminal logical slots**, with a fixed scheduled
denominator of 24 and eligible denominator of 23. The 20 search traces comprise
19 eligible rollouts and one terminal infrastructure exclusion after both
permitted first-call attempts timed out. Its real terminal trace is included
in the judge manifest with `excluded=true`, `eligible_denominator=0`, and its
original disposition; no third solver attempt was made. The qualification
controller supplements this excluded trace export from the original attempt
logs, without altering labels. The four private measurements remain outside
arm-facing feedback. Dispositions across the 24 slots are 17 finished,
five executor failures, one in-rollout API-timeout failure, and one infrastructure
exclusion. A terminal failure trace is complete evidence of that failure, not
a successful solver answer.

All 24 exported traces pass the scalar-content pattern scan, including all
20 healthcare/legal search traces. The manual inspection listing is
[`manual_trace_listing.md`](../runs/t2-accept-260911-final/manual_trace_listing.md);
it lists actual sanitized files, and does not claim a separate human review.
Serialized payloads are in `judge_payloads/`; the 48 payload captures are not
48 paid judge calls. Evolver qualification sessions each stop after three paid
calls (durably counted), following real workspace actions. This is a dispatch
and isolation probe, not a completed proposal; the task-model cap remains 24.
All acceptance evidence is linked by the matrix, with SHA-256 inventory in
`evidence_sha256.json`.

The full project command **`uv run pytest -q` passes: 581 passed, 6 skipped**.
The opt-in Docker boundary suite also passes (13 tests). Logs are
`logs/gdpevo/t2-project-tests-final.log` and
`logs/gdpevo/t2-docker-tests-final.log`. Regression coverage includes exact
archived-manifest ingestion under evidence v3, endpoint-compatible request
parameters, crash recovery, immutable snapshots, A9 exclusions, above-one score
boundaries, invalid native wrong-control results, and preservation of every
public database row after filtering. All 12 live services pass their smoke
checks in `logs/gdpevo/t2-service-smoke-final/checks.json`.

#### Costs and R5 dispositions

Across both task-run experiments and every acceptance attempt: **317 physical
API requests; $8.20933144 known cost; $9.79713214 conservatively accounted**,
including 43 unresolved requests with $1.57962750 retained reservations. Other
partially priced responses are conservatively charged, so known cost plus
unresolved reservations alone is not the complete total. The clean run accounts
for $8.80034164 and the halted diagnostic for $0.99679050. Rejected requests and
timeouts remain in the ledger; no assumed refunds were used. No further paid
calls were made. Details:
[`preparation-summary.json`](../costs/t2-accept-260911-final/preparation-summary.json).
This is below the $10 cap; it is not a provider invoice.

| R5 finding | Disposition |
| --- | --- |
| 1: Integrated acceptance | Six rows demonstrated through real rollouts; provider cache enforcement remains unverified. |
| 2: Experiment runner / judge ingestion | Manifest-driven T2 measurement runner and real v3 manifest ingestion implemented and tested; full outer evolution still pending. |
| 3: Denominators / A9 | Write-ahead fixed slots, failure policies, bounded retries and logged exclusions implemented and exercised. |
| 4: Crash-complete costs | T1 durable transport receipts, reservations, reconciliation and phase/session guards integrated and tested. |
| 5: Hidden column | Full 12-group audit; three TG019 column instances removed before service staging; six live alias/UNION checks pass. |
| 6: Transport retries | `max_retries=0` explicit in both entry points; controller retries documented and tested. |
| 7: Score range | Finite `[0,1]` enforced before tolerance; boundary regressions pass. |
| 8: Wrong-control gate | Native grader must exit successfully, have no error, and score exactly one; regression passes. |

### Reproduction and remaining Phase 3 work

```bash
uv run python -m scripts.gdpevo.audit_hidden_columns
uv run python -m scripts.gdpevo.build_images --output logs/gdpevo/NEW-images
uv run python -m scripts.gdpevo.controls --output logs/gdpevo/NEW-controls
uv run python -m gdpevo.runner --manifest runs/NEW/input_manifest.json
uv run python -m gdpevo.acceptance --manifest runs/NEW/input_manifest.json
uv run pytest -q
```

Use a fresh experiment ID for changed conditions. Grader-v1 remains frozen at
`12997be4c3781c5f3366d9d102329edac3771077a8e52b1329cda1ba0e763f6e`;
120/120 reference controls pass, and the TG015/TG018 wrong controls still score
0.95/0.722222 under v1 versus 1.0 in valid native runs. Above-one scores now fail
before tolerance, including 1.00000000001 and 1.0000005. No graders were tuned
to the acceptance outcomes.

The full Phase 3 remains **40 search / 40 sealed, T=10, three seeds**, with
prospectively selected finance anchors, all-arm evolution and matched C-TTS
budgets. Remaining work includes ratification and PREREG freeze; a funded full
schedule; calibrated v3 judge thresholds for terra; connecting the outer
proposal/selection and A3 paired-baseline machinery to these T2 measurements;
all-80-task semantic coverage; a fresh A1 measurement with the corrected
transport; and an enforceable provider cache boundary.
These repeated seed rollouts qualify infrastructure, not benchmark difficulty,
evolution benefit, or the registered Phase 3 estimands. Wheel packaging remains
outside this task's permitted paths; entry points run from the repository root.
