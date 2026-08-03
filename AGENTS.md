# AGENTS.md - AI Agent Instructions for Shaken Fist

This file provides instructions for AI agents (Claude Code, GitHub Copilot, etc.)
working on the Shaken Fist codebase.

## Project Context

Shaken Fist is a minimal cloud orchestration platform for VM and network
management. See [CLAUDE.md](CLAUDE.md) for detailed development guidance.

## CI/CD Architecture

### GitHub Actions Workflows

The repository uses several GitHub Actions workflows:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `functional-tests.yml` | Main CI: lint, unit tests, functional tests | PR, merge_group |
| `documentation-tests.yml` | Build and test documentation | PR |
| `pin-indirect-dependencies.yml` | Reconcile pinned indirect dependencies, adding new ones and removing obsolete ones (runs `tools/pin-indirect-dependencies.sh`) | Daily schedule, PR self-test |
| `export-repo-config.yml` | Export GitHub repo settings to version control | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by pr-fix-tests) | workflow_call, workflow_dispatch |
| `issue-fix.yml` | Triage open issues, propose a fix as a draft PR | workflow_dispatch |

### Merge Queue Pattern

The CI uses a two-stage merge queue pattern (see [this blog post](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/)):

1. **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
2. **`Can merge`** - Runs on `merge_group` events, gates the actual merge

**Important**: Only `Can see status` and `Can enqueue` are required status checks
in branch protection. `Can merge` is evaluated by the merge queue itself, not as
a required check.

### Exported Repository Configuration

Repository settings (rulesets, branch protection, merge queue config) are
exported to `.github/exported-config/` for version control and audit purposes:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If the `export-repo-config` workflow creates a PR, it means GitHub UI settings
have changed and should be reviewed.

## Automated CI Jobs

### Automated Delinter

When flake8 fails, the `automated_delinter` job runs Claude Code to fix lint
errors automatically. It skips if the last commit was from the bot to prevent
loops.

### Automated Exception Fixer

When functional tests detect exceptions in logs, the `automated_exception_fixer`
job downloads the test bundles and runs Claude Code to analyze and fix the
issues.

### Automated Reviewer

After successful tests, the `automated_reviewer` job calls the shared
`shakenfist/actions/.github/workflows/pr-auto-review.yml@main` reusable
workflow, which reviews the PR with the `review-pr-with-claude` action.
All the gating other than "CI passed" lives in that shared workflow: the
runner, the 60 minute timeout, the pull-request-event and
same-repository restrictions, its own concurrency group, and the
bot-commit check which keeps a bot push from triggering a review which
triggers another bot push. What this repository supplies is the `needs:`
list naming the test jobs and the token `permissions`, which a
cross-repository reusable workflow cannot grant itself.

The `@shakenfist-bot please re-review` command in `pr-re-review.yml`
still uses the `shakenfist/actions/review-pr-with-claude@main` action
directly, because it deliberately passes `force` to review a PR the bot
has already reviewed.

The reviewer produces structured JSON reviews, creates GitHub issues for
actionable items, and embeds the JSON in the PR comment for automation.

### Developer Automation (Bot Commands)

Authorized users can trigger automation by commenting on PRs:

- **`@shakenfist-bot please re-review`** - Triggers a fresh automated
  review of the PR using the shared review action.
- **`@shakenfist-bot please address comments`** - Runs Claude Code to
  address actionable items from the automated review. Uses
  `tools/address-comments-with-claude.sh` with dual-checkout security
  (trusted tools from base branch, PR code separately).
- **`@shakenfist-bot please attempt to fix`** - Runs Claude Code to fix
  unit test failures (`tox -ecover`). Uses `test-drift-fix.yml` with
  structured commit summaries.

## Working with This Codebase

### Code Style

- Single quotes for strings, double quotes for docstrings
- 120 character line wrap
- Trim trailing whitespace
- See [CLAUDE.md](CLAUDE.md) for detailed style guide

### Attribute updates use field masks

The `update_*_attributes` functions in `shakenfist/mariadb.py` require a
`fields` argument naming exactly the model fields the caller changed;
only those columns are written to MariaDB. `fields=None` (write every
column) is reserved for row creation and pydantic-upgrade persistence.
An unmasked update is a cross-attribute lost update waiting to happen:
it pushes a stale snapshot of the other columns over concurrent
writers' committed changes. Relational data (like instance placement)
belongs in a table with per-row inserts and deletes — see the
`instance_location` rows in `object_references` — never in a JSON list
on an attributes row.

### Native ENUM columns and Python enums

A handful of columns are native MariaDB `ENUM` types built with
`sa.Enum(SomePythonEnum)` (e.g. `object_states.object_type`). MariaDB
freezes an `ENUM`'s value list at `CREATE TABLE` time, so adding a
member to the Python enum works on fresh installs but breaks existing
databases ("Data truncated for column", error 1265) — greenfield CI
will not catch this. You do NOT need to write a migration when adding
an enum member: `ensure_schema()` ends with a reconciliation pass
(`_ensure_native_enum_columns()` in `shakenfist/mariadb.py`) that
discovers every `sa.Enum` column from the SQLAlchemy metadata and
widens stale columns automatically. Unit coverage lives in
`shakenfist/tests/test_mariadb_enum_columns.py`; the live upgrade path
is exercised against a real MariaDB by the "Schema ENUM widening" CI
job in `functional-tests.yml` (`tools/ci-enum-widening-test.sh`).

### Documentation

- When a change adds, renames, or removes a user-visible concept
  (an object type, state, term, or similar), update
  [`docs/glossary.md`](docs/glossary.md) in the same change so the
  glossary never drifts from the code.

### In-memory only objects never touch the database

Objects constructed with `in_memory_only=True` (the IPAM built when
hydrating a deleted network, blob-reference image artifacts) keep their
state, attributes and events in process memory. Any new persistence
path must be guarded on `self.in_memory_only`: a database row written
for an in-memory object is orphaned forever, because `hard_delete()`
early-returns for in-memory objects and state-driven iterators skip
objects whose static row is missing (issue 3532). Related uuid format
gotcha: `object_states.object_uuid` stores dashed uuids while `sa.Uuid`
static-table columns store undashed CHAR(32) — SQL joining the two must
transform one side (see the orphan reconciliation queries in
`mariadb.py`).

### Events vs logs

Shaken Fist has two structured-record streams; choose the right
one when emitting a message:

- **If the message relates to one or more Shaken Fist objects**
  (instance, network, blob, artifact, …), emit an **event** via
  `eventlog.add_event()` / `add_event_multi()`. Events are the
  authoritative per-object record (stored in MariaDB, read back
  through the REST API) and also emit an `Added event` log line,
  so they appear in the log stream too.
- **If the message has no directly-associated object** (daemon
  lifecycle, scheduler decisions, node-level conditions), emit a
  **log** line via the module `LOG`.

Events stay authoritative in MariaDB — they are never moved to
Loki; logs ship to Loki (or the local journal). The `Added event`
echo into the log stream is controlled by `LOG_EVENTS_TO_LOKI`
(default on). See
[`docs/operator_guide/logging.md`](docs/operator_guide/logging.md)
and [`docs/operator_guide/events.md`](docs/operator_guide/events.md).

### Testing

```bash
tox                              # Run all tests
tox -eflake8                     # Lint check
tox -emypy                       # Type checking
tox -ecover                      # Coverage report
stestr run {test_name}           # Run specific test
```

### Pre-commit Hooks

The repository uses pre-commit hooks to validate code before commits:

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

Current hooks:
- `actionlint` - Validates GitHub Actions workflow files
- `ansible-lint` - Validates the `shakenfist.shakenfist` Ansible collection
  (`shakenfist/deploy/collection/`)
- `mypy` - Type checking via tox (incremental rollout)

### sf-net daemon topology

`sf-net` runs a `net-worker` job on **every** cluster node (not only the
elected network node). Each node's worker drains its own per-node
`{node_uuid}-network-*` queues for hypervisor-local operations
(`create_on_hypervisor`, `ensure_mesh`). Additionally, the elected network
node's worker also drains the cluster-wide `networknode-clusteroperation-*`
queues for network-node-only operations (`create_on_network_node`,
`add_floating_ip`, etc.). This two-family design means per-hypervisor network
mutations are parallelised across nodes while network-node-singleton operations
remain serialised.

### Network facade architecture

**Worker-only mutation surface.** `BridgedVXLanNetwork`
(`shakenfist/network/bridged_vxlan_network.py`) is the only place that
mutates host network state. Its constructor is called exclusively from the
single-threaded net-worker dispatcher
(`shakenfist/daemons/network/workitem.py`) — making re-entrancy through
the queue structurally impossible. External callers always hold `Network`;
the dispatcher constructs `BridgedVXLanNetwork` and calls `_apply_*`
methods on it. The single-worker-per-queue invariant (see the comment
block at `self._defer_delays` in workitem.py) is a load-bearing property:
it is why the dispatcher's in-memory exponential back-off map is correct,
and why cross-daemon serialisation can be queue-based rather than
lock-based. All `NodeLock(global_scope=False)` wrappers that formerly
existed inside `_apply_*` methods have been removed — only `sf-net`
dequeues and executes network work, so concurrent invocation across
daemons cannot happen by construction. The cancellation check on dequeue
runs before the `_apply_*` call; if the op is already cancelled, the
worker skips execution and transitions the op to `STATE_ABORT`.

**Network methods enqueue; maintain is discovery-only.** All 15
host-mutating `Network` methods enqueue a cluster operation and return an
op handle rather than mutating state directly. `shakenfist/daemons/network/maintain.py`
is discovery-only: it never blocks on `raise_for_error()`. Each maintain
pass applies a five-guard pipeline before enqueueing any reconciliation op
at `PRIORITY.background` — (1) queue-depth safety, (2) per-network gating
via `has_pending_cluster_operation`, (3) cooldown on recent errors,
(4) circuit-breaker on repeated errors, (5) enqueue. Three config knobs
control the guards: `MAINTAIN_QUEUE_DEPTH_THRESHOLD` (default 50),
`MAINTAIN_RECONCILE_COOLDOWN_SECONDS` (default 60),
`MAINTAIN_RECONCILE_CIRCUIT_K` (default 5).

**REST API surface.** The two network delete endpoints
(`DELETE /networks/<uuid>` and `DELETE /networks`) return HTTP 202
(Accepted) with an op-handle body; callers poll
`GET /clusteroperations/<op_uuid>` for completion. Two discovery endpoints
are available: `GET /clusteroperations/<op_uuid>/chain` (transitive
`depends_on` ancestor closure, namespace-scoped) and
`GET /clusteroperations?target_object_type=<type>&target_uuid=<uuid>`
(ops targeting an object, SQL-layer namespace filtering). The only
surviving `@redirect_to_network_node` is on `NetworkPingEndpoint.get`
because the ping handler runs `ip netns exec` directly on the network
node; migrating it to queue-based requires op-output infrastructure not
yet built (deferred future work).

**Error handling.** `ErrorReport` (`shakenfist/operations/error_report.py`)
is the on-the-wire shape for failed cluster operations: fields `code`,
`message`, `details`, `origin_class`, `traceback`. Errors are data, never
rehydrated Python exception types. The `_EXCEPTION_CODE_REGISTRY` dict
maps typed exceptions to stable string codes (e.g.
`'network.ensure_mesh.failed'`). The op carries `error_report` in its
`external_view`; `op.raise_for_error(timeout=None)` polls until terminal
and raises `NetworkOperationFailed` if the op ended in `STATE_ERROR`,
letting callers that want exception-flow control use a familiar `try/raise`
pattern without the error type being load-bearing across process
boundaries.

### Scheduler and node capacity metrics

The scheduler ranks hypervisors by load per schedulable thread and
admits against reservation-adjusted capacity. The reservation
arithmetic lives in `shakenfist/daemons/resources/main.py`
(`_compute_reservations()`, `_get_hybrid_core_counts()`), which
publishes `cpu_cores`, `cpu_threads`, `cpu_cores_reserved`,
`cpu_schedulable`, `cpu_cores_schedulable`, `memory_reserved_mb`,
`disk_reservation_gb` (and `cpu_cores_performance` /
`cpu_cores_efficiency` on hybrid CPUs) into `node_metrics`. On the
consuming side, `Scheduler._schedulable_threads()` and
`Scheduler._memory_reserved_mb()` in `shakenfist/scheduler.py`
apply per-node fallbacks for metrics rows written by older
resources daemons (the CPU fallback subtracts this node's own
`NODE_CPU_RESERVATION_THREADS`, with no infra-role bump, so
un-upgraded nodes don't look artificially large) — admission,
ordering and `summarize_resources()` all go through these helpers,
so keep them in sync if you touch capacity arithmetic.
Operator-facing documentation is
[`docs/operator_guide/scheduler.md`](docs/operator_guide/scheduler.md).

### Node resource health

Node storage health drives `node.state`, on a different axis from the
daemon-liveness watchdog below. `shakenfist/resource_health.py` is the
reusable, timeout-guarded path-check primitive (a hung `hard`-NFS mount
blocks rather than erroring, so the deadline is the unhealthy signal).
`shakenfist/node_health.py` maps a node's role to the object types it
hosts, runs each type's declared `health_dependencies` paths, and marks
the node `STATE_ERROR` via a `health` event (`EVENT_TYPE_HEALTH`, a
channel separate from the audit log) carrying the affected object types. `sf-resources` runs this on its own thread (not the metrics
loop); `sf-cluster` reads the affected types back
(`node_health.errored_node_affected_types`) and cascades — erroring
instances and re-replicating blobs — mirroring the deleted-node path but
erroring rather than deleting. Node error is operator-cleared only
(`sf-ctl clear-node-error`). Operator docs:
[`docs/operator_guide/node_health.md`](docs/operator_guide/node_health.md).

### Daemon liveness (systemd watchdog)

`Daemon.pet_watchdog()` in `shakenfist/daemons/daemon.py` is the liveness
seam for every non-trivial daemon. `Daemon.idle()` calls it automatically,
so any daemon whose main loop reaches `idle()` at the end of each pass
already pets the watchdog.

Any daemon loop that performs a **long pass without going through `idle()`**
must call `pet_watchdog()` explicitly — otherwise systemd will kill the
process once `WatchdogSec` (60s) elapses, even though the daemon is working
normally. The existing explicit callers are the `sf-cluster` elected loop
and the `_cluster_wide_cleanup`, `_maintain_blobs`, and
`_find_missing_blobs` passes in `sf-cluster`/`sf-cleaner`. If you add a
new long-running maintenance pass to any of the eight armed daemons
(database, net, cleaner, cluster, queues, resources, transfers,
sidechannel), add `self.pet_watchdog()` calls inside its inner loop.

### sf-api health probing

`shakenfist/external_api/health.py` is the per-worker readiness module. Each
gunicorn worker process runs a background checker thread (started in the
`post_fork` hook in `gunicorn_config.py`) that polls sf-database's
`grpc.health.v1.Health/Check` every 5 seconds and caches the result. The
`/readyz` and `/healthz` endpoints call `health.is_ready()` to answer in
microseconds without an RPC on the request path.

The `post_worker_init` hook in `gunicorn_config.py` installs a SIGTERM
handler that calls `health.begin_drain()` (flipping `/readyz` to 503) and
then waits `API_DRAIN_GRACE` seconds before the normal worker shutdown.

### VDI console token mint path

Shaken Fist mints short lived Ed25519 JWTs for the Kerbside VDI console
proxy. `shakenfist/external_api/instance.py`
(`InstanceVDIProxyConsoleHelperEndpoint`, `GET
/instances/<ref>/vdiconsoleproxy`) mints a token and returns a proxy URL;
`shakenfist/util/vdi_tokens.py` owns all key handling (mint, ensure, rotate,
public view); `shakenfist/external_api/admin.py`
(`AdminVDITokenPublicKeyEndpoint`, `GET /admin/vditokenpubkey`) publishes the
public verification keys. The signing key lives in a single `cluster_config`
row, `KERBSIDE_JWT_SIGNING_KEY` (two-key rotation window). The `sf-ctl`
`ensure-kerbside-signing-key` / `rotate-kerbside-signing-key` subcommands
bootstrap and rotate it. Operator runbook:
`docs/operator_guide/vdi_console_tokens.md`.

### Never restate a visibility predicate

A listing endpoint filters; a single-object endpoint cannot, because it has
already resolved the object by the time it knows who is asking. The two must
still agree, so the single-object guard calls *the same function* the listing
filters with rather than open-coding the equivalent test.

For artifacts that function is `namespace_or_shared_filter(namespace, obj)`:
own namespace, a namespace whose trust list names the caller, `system`, or
`shared`. `requires_artifact_access` calls it. `requires_artifact_ownership`
is the deliberately stricter mutation guard and tests `namespace_is_trusted`
alone — sharing publishes an artifact for reading, so the write paths must
not consult the `shared` flag.

This is not a style preference. `requires_artifact_access` used to restate the
rule as `if a.shared and requestor not in [a.namespace, 'system']`, which is
inverted in both directions, and because `arg_is_artifact_ref` resolves a UUID
straight to `Artifact.from_db` with no namespace filter, that decorator was the
only guard on the path. Any caller who knew a UUID could read any namespace's
unshared artifacts. Refuse with `404` rather than `403` so the refusal does not
confirm the object exists, and pair every new refusal test with a control that
shows the same request succeeding when the one thing under test changes.

The same predicate governs how a *name* resolves on the read routes, not just
whether a resolved object is allowed through. `arg_is_visible_artifact_ref`
(paired with `requires_artifact_access`) resolves through
`Artifact.from_db_by_ref_visible_to`, which searches the caller's own
namespace first and only then widens to what `namespace_or_shared_filter`
admits. Two rules to preserve if you touch this:

- The caller's own namespace must win, or sharing an object silently
  retargets every tenant who already used that name.
- Routes which change an object use plain `arg_is_artifact_ref` and resolve
  names narrowly. Trust still permits the write by UUID; what must not happen
  is a name resolving into someone else's namespace on a route that then
  deletes what it found. New route, ownership guard, narrow ref decorator —
  the pairing goes together.

### Key Directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
