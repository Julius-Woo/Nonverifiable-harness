# GDPevo adapter groundwork — P1.10

Completed 2026-09-10. The unchanged harness entry point was exercised through
real Docker boundaries on **013/train/001 (healthcare)** and
**017/train/001 (legal)**, using the TASK endpoint and `gpt-5-mini`, low
reasoning, 4,096 completion tokens, and a 24-call cap. Both attempts wrote valid
JSON objects; both received oracle **0**, with native normalized score **0.0**.
These are recorded seed failures, not missing runs. No paid retries or
selection of better outcomes were performed.

The adapter's seven isolation checks passed; all 12 filtered service images
started and passed 174 boundary checks. All 120 frozen reference controls
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

The binary oracle implements A3: use `normalized_score` when present, otherwise
`score`; require a finite numeric value with `abs(s - 1) <= 1e-6`, a valid JSON
object, and a valid grader execution. Duplicate JSON keys, malformed JSON,
non-object submissions, and non-finite numbers are invalid. The denominator
is one for every attempt. Timeouts, seed exhaustion/failure, and tool failures
receive binary zero; grader errors also receive zero and an explicit error
record. Native normalized score and raw native grader JSON remain secondary
outputs, independent of the binary result. This groundwork uses the requested
fixed-denominator A3 rule; future orchestration must reconcile the broader A9
retry/exclusion policy without silently dropping failures.

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

## Section 5 acceptance evidence

Primary log: `logs/gdpevo/p110-acceptance-final/acceptance.json`.
It records container and network inspections, actual command responses,
per-arm mounted views, and all **174/174** successful service-boundary checks.

| Section 5 row | Executed check | Result |
| --- | --- | --- |
| 1: oracle files | Random host oracle canary unreadable through solver and arm-container tools; separate canary check in each paid rollout | Pass |
| 2: trace contamination | 20 adversarial file-read traces across GDPevo/TB2 oracle paths under `/work` and `/app`; reads refused and observations filtered | 20/20 pass |
| 3: judge inputs | Closed keyword-only task/trace/answer schema rejects an `oracle` argument; no grader filesystem input | Pass |
| 4: uniform evolver prompt | A0–A4 prompt files read inside their containers; templates identical after replacing score-source text | Pass |
| 5: anchors | Anchor and sealed feedback rejected; no anchor mount in arm containers | Pass |
| 6: cross-arm isolation | Five distinct containers and directories; each tool view contains only its own arm marker and feedback | Pass |
| 7: caches | Distinct `user` and `prompt_cache_key` per run/arm/role; config inspected in each arm container; both real API runs accepted scoped parameters | Pass |

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

## Reproduction and remaining Phase 3 work

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

Before full Phase 3 T2 runs:

1. Integrate these boundaries and closed feedback views into the real
   evolver/judge/anchor controller, rerun all seven checks there, and manually
   inspect a larger sample of actual solver trajectories.
2. Freeze the 40-search/40-sealed healthcare/legal manifest, finance anchors,
   grader hash in PREREG, and the full Phase 3 budget. These two seed failures
   do not estimate the benchmark's aggregate difficulty or justify tuning it.
3. Connect durable trial admission/resume, endpoint rate limits, cancellation
   accounting, cleanup recovery after host SIGKILL, and the agreed A9 policy.
4. Validate all 80 main tasks through the boundary and calibrate realistic
   token/context and latency distributions. Business-route coverage here is
   smoke coverage plus two real attempts, not exhaustive semantic equivalence.
5. Keep native scores beside grader-v1 results. Any further grader changes
   require a new version and controls; do not revise grader-v1 after outcomes.
6. Include the adapter in packaging if wheel-based deployment is needed.
   Current entry points run directly from the repo root.

No commit or push was made, and no changes to the protected harness, upstream,
data, decision, plan, or root context files were made by this task.
