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

"The caller writes every column anyway" is not a reason to pass `None`.
`TrustedIssuer.update` and `MappingRule.update` both replace their whole
attribute set, because an issuer's URL and key source are one
configuration and a rule's policy is one unit — and both still name
every field. Naming them keeps `None` meaning only "creation or
upgrade", so a reader can tell the two cases apart, and it means the
day somebody adds a single-field writer they inherit a masked function
rather than having to retrofit one. The mask travels over gRPC as
`repeated string fields` on the request message, and the mock in
`shakenfist/tests/mock_mariadb.py` honours it too — a mock that
replaced the whole row would let a caller name the wrong fields and
still see the write it expected.

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
mutates host network state for a network which exists (the one exception,
reaping devices belonging to networks which no longer exist, is described
under "maintain is discovery-only" below). Its constructor is called
exclusively from the
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

**The one exception: reaping stray vxlans.** Maintain deletes orphaned
vxlan devices (`_handle_stray_vxlans()`) directly, on the maintain thread,
rather than through the queue. This is deliberate and is the only
host-mutating code outside the net-worker. The exception is kept exactly
as wide as the argument for it: it covers *only* devices whose network
object no longer exists, because for those the queue path is unavailable
by construction — an operation has to target an object, and there is no
object left to target. The neighbouring case, where the network still
exists but no instance on this node uses it, *is* enqueued: it becomes a
`node_net_op` `network_destroy` targeting (this node, that network), so
it stays inside the dispatcher and serialises against any concurrent
create for the same network.

Three properties make the direct case safe. First, the networks row is
written before any device is created, so a device whose vxid has no row
can never be a network under construction — it can only be residue.
Second, neither mutating branch commits until the host agrees: if a
device Shaken Fist did not create is still enslaved to `br-vxlan-<vxid>`
then a domain is attached to that bridge right now, whatever the
database records say, and the stray is protected instead. A bridge
which does not exist answers that question with "nothing" rather than
failing to answer it — teardown deletes the bridge before the vxlan
interface and rediscovery keys on the interface, so a vxlan device with
no bridge is the commonest stray shape and must stay reapable. Third,
deletion is idempotent and guarded by `check_for_interface()`, so
racing the net-worker's own `network_destroy` teardown of the same device
is harmless; each device is deleted inside its own `try`/`except` which
logs and re-arms the grace period rather than killing the maintain
thread. Devices are only touched after they have been stray for
`MAINTAIN_STRAY_VXLAN_GRACE_SECONDS` (default 300). A stray which is
*not* actionable is warned about once per episode rather than on every
pass — see issue #3597 for the log storm that motivated this.

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
`shared`. `requires_artifact_access` calls it.

`requires_artifact_ownership` is the deliberately stricter mutation guard and
tests `request_namespace() not in [obj.namespace, 'system']` — the same test
`requires_instance_ownership` and `requires_network_ownership` use. It
consults neither the `shared` flag nor the trust list: sharing publishes an
object for reading, and a trust is a visibility grant, so neither one hands
out a delete button. Creating an object *in* a namespace that trusts you is a
separate question and is still allowed.

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
  names narrowly. The ownership guard already refuses the write whichever way
  the object was named, so this is defence in depth rather than the only
  gate — but a name must never resolve into someone else's namespace on a
  route that then deletes what it found. New route, ownership guard, narrow
  ref decorator — the pairing goes together.

The same split applies to *url* resolution, and this is where it was missed.
`Artifact.from_url` filters by `namespace_or_shared_filter`, so it can return
an artifact belonging to whoever shares with or trusts the caller. That is the
right answer for a caller which will read the result and the wrong one for a
caller which will write to it: the upload and cache routes used it to pick a
write target, so a trusted namespace could name the owner's `source_url` and
have its own blob added as the newest version — and because `add_index` ends in
`delete_old_versions`, the owner's older versions went with it. Write paths use
`Artifact.owned_from_url()`, which resolves by ownership and does not create.

Two rules fell out of fixing it, and both generalise past artifacts:

- **A predicate is part of a function's contract, not an implementation
  detail.** If one lookup serves both intents, say which it is in the name and
  make the other one a separate function. A default that silently suits readers
  is how a write path inherits a read's authorisation.
- **Creating and modifying are different grants.** A trust may let a namespace
  *give* you an object it did not have — additive, and the operator guide
  promises it. It must not let that namespace replace what an object you
  already own resolves to. When a route can do either, authorise the two cases
  separately rather than once at the top, and put the audit event *after* the
  check so a refused caller cannot write to the event log of a namespace it is
  about to be told does not exist.

Three call sites that end in `add_index` still resolve with `from_url`, and the
sweep was not exhaustive. They are listed here so the next reader does not
assume otherwise:

- `external_api/instance.py` (instance create) resolves a caller-supplied
  `disk.base` with `create_if_new=True`. Namespace B naming A's `source_url`
  lands on A's artifact, but the fetch pulls from the owner's own URL rather
  than from bytes B supplied, so an unchanged URL adds no version. Lower
  severity than the upload hole, not zero.
- `external_api/label.py` (`LabelEndpoint.post`) builds
  `sf://label/<namespace>/<name>` from the request, so the URL is
  namespace-scoped and a caller cannot steer it into somebody else's namespace.
  Note that the `requires_admin=True` in its `swag_from` is documentation and
  enforces nothing — see the swagger note elsewhere in this file.
- `operations/artifact_fetch_op.py` runs behind the instance path above and
  inherits its namespace.

Issue #3640 tracks narrowing them. Until then, treat "write paths use
`owned_from_url`" as the rule being converged on rather than one the tree
already satisfies, and do not add a fourth exception.

### Credential-carrying routes are not logged, not redacted

Two independent loggers see a request body: `app.py`'s `before_request` audit
event, and `log_request` in `base.py`, which merges the parsed body into the
decorated method's kwargs and logs those. Both ask
`api_base.handles_credentials()` — one predicate, in one place — and drop the
whole body when it answers yes.

Redacting by field name was tried and is wrong. `key` means a metadata key
name on most endpoints and a secret on a few, so a name-based rule has to know
which route it is on anyway, and it silently starts leaking the day somebody
adds a route it has not heard of. That is exactly what happened: the federated
exchange's credential field is `token`, which was on neither redaction list,
so identity tokens were logged verbatim at INFO and shipped to the log
aggregator.

Every route which takes a credential lives under `/auth/`. Keep it that way,
or extend the predicate — never the redaction lists.

### A check that runs after the parse is not a check

The endpoint-method decorators are not the outermost thing in a request.
`log_request` calls `get_json(force=True)` before any method body runs, so a
size or shape check written inside a `post()` cannot prevent work that has
already happened. Anything protecting an *unauthenticated* endpoint from
attacker-controlled input has to be an `@app.before_request` hook registered
ahead of `log_request_info` — see `limit_federated_body_size`.

While you are there: `flask.request.content_length` is `None` for chunked
transfer encoding. Treating unknown as small enough lets any caller opt out of
a size limit by choosing a header, so refuse with 411 rather than measuring.

### Two records must not claim one lookup key

`federation.issuer_for_token` resolves a trusted issuer by scanning for a
matching `issuer_url`. Uniqueness on that column is therefore a correctness
property, not tidiness: two live records claiming one URL make which
provider's signing keys are trusted depend on listing order, so an
administrator repointing an issuer would believe they had while some requests
kept verifying against the old JWKS. The create and update endpoints refuse a
duplicate by calling `federation.issuer_claiming_url`, the same function the
resolution path uses.

A check-then-write is not an invariant on its own. `issuer_url` lives in the
attributes row and has no unique index -- and cannot easily get one, because a
soft-deleted issuer keeps its row and its URL is deliberately reusable -- so
both endpoints hold `_issuer_url_lock()` across the check *and* the write it
guards. Without that, two administrators configuring one provider at the same
moment both read "free" and both write. This is the `vsock_cids` pattern from
`instance.py`: where a unique index cannot be the arbiter, a cluster lock
around the check-then-act has to be.

### Put the meter above the expensive thing, not below it

`/auth/federated` is unauthenticated, so every step above the rate limit is a
step an anonymous caller gets for free, as often as they can send. Ordering
there is a security property, and the question to ask of each step is not "is
this cheap?" but "does this touch the database or the network?". Issuer
resolution reads once per configured issuer, so it belongs *below* the meter
even though there are only ever a handful of issuers; only the argument
checks, which touch nothing but the request, belong above it. The original
ordering had this backwards on its own stated logic -- it placed the meter
below the lookup to avoid writing a counter row, when the lookup it was
skipping cost more than the row did. If you add a step to that endpoint, place
it relative to `enforce_rate_limit` on that basis, and say why in a comment.

### A guard has to sit where the exception is raised

`CorruptMappingRule` comes from decoding `bound_claims` or `scopes`, which
only happens on the *attributes* read. `MappingRule.from_db_by_name` reads the
static row and the object state, so wrapping the lookup in a `try` looks like
protection and is none. Before writing a guard, find the raise site; before
believing a regression test, check that the thing it patches is on the path
that can actually fail. Use `MappingRule.policy()` to read the whole policy in
one go when you need more than one field -- it is also the single place to
catch this.

The same guard has to answer differently in different places, and that is not
inconsistency. The exchange endpoint *refuses* a damaged rule, because bound
claims it cannot read are bound claims it cannot check and minting anyway would
be authorising on a guess. `MappingRule.external_view()` *describes* one, with
an explicit `unusable` marker, because the CRUD routes exist to tell an owner
which rule is broken -- raising there turned one bad row into a 500 that hid
every healthy rule in the namespace, and made a successful delete report
failure on the one call that would have cleaned it up. Ask what the caller will
do with the answer before choosing.

### Fail closed on a field, not on a formatting accident

A reply that says "this went wrong" must say so in a field set only on the
success path. `CountFederatedAttemptReply` used to signal failure by carrying a
non-empty `error`, and the client read anything else as an answer -- so an
exception raised with no args, whose `str()` is empty, arrived as
`attempts=0, error=''` and was read as "nobody has tried this minute, allow".
That is the one direction a rate limiter must never fail, on the one endpoint
anybody may call.

Both federation replies now carry `bool ok`, set only where the work actually
succeeded, and the client tests that. `error` is diagnostics. When you add an
RPC whose reply has a permissive-looking default -- a zero count, an empty
list, a `False` that means "go ahead" -- carry an explicit success field rather
than inferring one, and write the test that returns the empty-error reply and
asserts the refusal. The invariant is not that today's code produces a message;
it is that no reply can be mistaken for a permissive one.

### Cluster CI tests only run in the merge queue

The `(collection)` matrix in `Functional tests` -- everything under
`deploy/shakenfist_ci/cluster_ci_tests/` -- is skipped on `pull_request` and
runs on `merge_group`. A green PR therefore says nothing about whether those
tests pass, or whether they can even reach their first assertion.

`cluster_ci_tests/test_federation.py` sat through four commits registering a
trusted issuer with an `http://` `jwks_uri` while the API had refused
non-HTTPS `jwks_uri` since the object was added. Every test in the class died
in `setUp` on a 400, and nothing said so until the branch entered the queue.

Two habits follow. When you add or change a cluster CI test, run it, or at
minimum drive the validator it depends on directly -- these tests import
cleanly given `pip install shakenfist-client testtools oslo.concurrency
prettytable` in a scratch venv, so "the client is not installed" is not a
reason to skip verification. And when a test needs input that a validator
rejects, the validator is usually right: change the test, never carve a
loopback or test-only exemption into a security check. If that makes the test
unrunnable, make it skip loudly and file the issue -- see #3639 for the JWKS
certificate case.

### Key Directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
