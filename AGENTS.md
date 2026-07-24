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
| `pin-indirect-dependencies.yml` | Keep indirect dependencies pinned | Daily schedule |
| `export-repo-config.yml` | Export GitHub repo settings to version control | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by pr-fix-tests) | workflow_call, workflow_dispatch |

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

After successful tests, the `automated_reviewer` job uses the shared
`shakenfist/actions/review-pr-with-claude@main` action to review the PR.
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
`cpu_schedulable`, `cpu_cores_schedulable`, `memory_reserved_mb`
(and `cpu_cores_performance` / `cpu_cores_efficiency` on hybrid
CPUs) into `node_metrics`. On the consuming side,
`Scheduler._schedulable_threads()` and
`Scheduler._memory_reserved_mb()` in `shakenfist/scheduler.py`
apply per-node fallbacks for metrics rows written by older
resources daemons (the CPU fallback synthesises a role-aware
reservation so un-upgraded nodes don't look artificially large) —
admission, ordering and `summarize_resources()` all go through
these helpers, so keep them in sync if you touch capacity
arithmetic. Operator-facing documentation is
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

### Key Directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
