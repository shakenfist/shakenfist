# Replace `last_cluster_operation` with a `cluster_operation_targets`-driven check

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named
`PLAN-replace-last-cluster-operation-phase-NN-descriptive.md`,
and tracked in the Execution table below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The `last_cluster_operation` field on a database-backed
object historically pointed at the most recent cluster
operation targeting that object. It was an etcd-era design:
a single JSON pointer in `object_metadata` updated by every
caller that enqueued an op against the object. The network
maintainer's `Network.is_okay()` check (in
`shakenfist/network/network.py`) reads this pointer to decide
whether to defer its own recreate path while an op is in
flight.

The `cluster_operation_targets` MariaDB table has since been
introduced (see `CLAUDE.md` *Cluster Operation Targets* and
`shakenfist/schema/cluster_operation_target.py`). It records
**every** cluster operation against **every** target object,
with `AUTO_INCREMENT` sequence numbering for ordering.
`baseobject.DatabaseBackedObjectWithOperations.last_cluster_operation`
is now a property that calls
`mariadb.get_latest_cluster_operation_target(...)` and
synthesises the old shape; `set_last_cluster_operation` is a
thin write to that table. So the storage migration is
already done — the API surface and the gating logic still
behave as if it were a single pointer.

The single-pointer behaviour now produces two distinct bugs:

1. **Latest-only race**: `is_okay()` reads only the latest
   target row. If a later op against the same object has
   already reached a terminal state while an earlier op is
   still queued/executing, the maintainer sees "latest is
   terminal", concludes nothing is in flight, and runs its
   own recreate path. The earlier op is the one actually
   modifying the network on this hypervisor.
2. **Forgotten-call race**: every caller that enqueues an
   op against an object must remember to call
   `set_last_cluster_operation` on that object. We just
   shipped a CI failure caused by exactly this in the hot-plug
   interface flow (commit `8923391c`), and an audit found six
   more sites at risk: `shakenfist/network/network.py:731,
   782, 798, 816, 839, 863`,
   `shakenfist/external_api/instance.py:1554`, and
   `shakenfist/daemons/cluster/scheduled_tasks.py:210`. Three
   are race-prone (`network.py` 782/798/816 in particular)
   because they're reachable from API paths that the
   maintainer can race.

The CI workflow has been intermittently broken in subtly
related ways for ~20 months. We want one clean landing of the
right design rather than another iteration of "patch the
caller you noticed".

## Mission and problem statement

Replace the single-pointer `last_cluster_operation` semantics
with a `cluster_operation_targets`-driven check that:

* Returns "in flight" if **any** non-terminal cluster
  operation targets the object, regardless of whether a
  later op against the same object has since completed.
* Removes the per-caller obligation to remember
  `set_last_cluster_operation` — targeting is recorded
  automatically inside the `*_create_and_enqueue` helpers
  for every target object referenced by the op's schema.
* Preserves the `last_cluster_operation` field in
  `external_view()` output as a synthetic projection of the
  same query, so external API consumers (CLI, tests, anyone
  scraping the JSON) keep working unchanged.

The current `last_cluster_operation` *property* on the base
object already does the synthesis. The work is in switching
gating callers to a new "any-in-flight" query and automating
the writes.

## Decisions

These open questions were resolved by the operator before
phase planning began. Each is recorded here so phase plans
can reference the chosen path without re-litigating.

1. **Synthetic `external_view` semantics — keep latest of
   any state.** `last_cluster_operation` in `external_view()`
   continues to return the latest `cluster_operation_targets`
   row regardless of state. The new gating query is a
   separate, internal-only API. Consumers like
   `runs_after=[instance_from_db.last_cluster_operation]`
   (`external_api/instance.py:1027`) keep their existing
   meaning.

2. **Auto-targeting via explicit declaration.** Each
   operation schema model declares its targets via a
   `target_object_types: ClassVar` (or equivalent). Convention
   is brittle: `node_uuid` is execution context (Node does
   not inherit `DatabaseBackedObjectWithOperations`, see
   decision 5), and `interface_uuid` is sometimes context.
   Explicit declaration is unambiguous.

3. **`set_last_cluster_operation` becomes private.** Once
   auto-targeting lands, the method is renamed to
   `_set_last_cluster_operation` so callers cannot use it
   wrong. The `daemons/network/maintain.py:113` caller
   (missed in the original audit) is also rewired to
   auto-targeting in phase 3.

4. **Drop the per-target attr lock.**
   `_direct_create_cluster_operation_target`
   (`mariadb.py:5294`) is a pure INSERT with no
   read-modify-write; `sequence_number` is `AUTO_INCREMENT`,
   `operation_uuid` is `UNIQUE`. Concurrent writers do not
   conflict, so `obj.get_lock_attr('last_cluster_operation',
   ...)` is removed alongside the explicit
   `set_last_cluster_operation` calls in phase 3.

5. **Node objects are not auto-targets.** Confirmed:
   `Node` (`shakenfist/node.py:32`) inherits
   `DatabaseBackedObject`, not
   `DatabaseBackedObjectWithOperations`. All `node_*_op`
   schemas target other objects (instance, network, blob,
   artifact); `node_uuid` on those schemas identifies the
   execution location and is not registered as a target.

6. **Drop the `object_metadata.last_cluster_operation_json`
   column now.** This branch has not been deployed, so
   there is no rollback path that needs the dead column.
   Phase 4 fully severs the read/write paths in
   `mariadb.py` and `daemons/database/main.py`, drops the
   column from the SQLAlchemy schema, and removes the
   field from the gRPC `ObjectMetadataReply` message.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Add `has_pending_cluster_operation` query and tests | [PLAN-replace-last-cluster-operation-phase-01-query.md](PLAN-replace-last-cluster-operation-phase-01-query.md) | Complete |
| 2. Switch `Network.is_okay()` and other gating callers | [PLAN-replace-last-cluster-operation-phase-02-gating.md](PLAN-replace-last-cluster-operation-phase-02-gating.md) | Complete |
| 3. Auto-target tracking, remove explicit callers, privatise setter | [PLAN-replace-last-cluster-operation-phase-03-auto-target.md](PLAN-replace-last-cluster-operation-phase-03-auto-target.md) | Complete |
| 4. Drop `object_metadata.last_cluster_operation_json` column | [PLAN-replace-last-cluster-operation-phase-04-drop-column.md](PLAN-replace-last-cluster-operation-phase-04-drop-column.md) | Complete |
| 5. Documentation and final audit | [PLAN-replace-last-cluster-operation-phase-05-docs.md](PLAN-replace-last-cluster-operation-phase-05-docs.md) | Complete |

### Phase outlines

**Phase 1.** Add a new `has_pending_cluster_operation()`
method on `DatabaseBackedObjectWithOperations` that returns
True if any row in `cluster_operation_targets` for this
`(object_type, uuid)` references an operation whose
`object_states.state_value` is in the active set
(`queued`, `preflight`, `executing`). The terminal set is
defined by exclusion, matching
`_direct_delete_stale_cluster_operation_targets`
(`mariadb.py:5508`). Implement as `_direct_*` / `_grpc_*` /
public trio in `mariadb.py`, mirroring the existing
`get_latest_cluster_operation_target` shape, including a new
proto RPC `HasPendingClusterOperationTarget`. Add unit tests
covering: no targets, single in-flight target, single
terminal target, multiple targets with mixed states, and
multiple terminal targets followed by an in-flight one (the
latest-only race we are fixing). Plan effort: high; phase
plan effort: medium.

**Phase 2.** Switch `Network.is_okay()`, the network
maintainer in `shakenfist/daemons/network/maintain.py`, and
any sibling gating logic to use
`has_pending_cluster_operation()`. The current `is_okay()`
opens with a `last_cluster_operation` lookup and only falls
through to bridge / dnsmasq checks when the latest op is
terminal — that whole prelude is replaced with the new
query. Re-run the audit (phase plan should include rerunning
the Explore agent on `is_okay`-equivalents across object
types) so we don't miss e.g. the cleaner daemon. Plan
effort: medium; phase plan effort: medium.

**Phase 3.** Move target-tracking into the
`*_create_and_enqueue` helpers in
`shakenfist/schema/operations/*.py`. Each schema's `model`
class declares its target object types via a class variable
(see decision 2), and `enqueue_cluster_operation` (or the
helper directly) writes a `cluster_operation_targets` row
per target before returning. In the same phase, sweep the
audit list (`shakenfist/network/network.py:731, 782, 798,
816, 839, 863`, `shakenfist/external_api/instance.py:1554`,
`shakenfist/daemons/cluster/scheduled_tasks.py:210`,
`shakenfist/daemons/network/maintain.py:113`, plus the
operation-execution sites listed in the explore findings
attached to this plan), removing the explicit
`set_last_cluster_operation` call and the surrounding
`obj.get_lock_attr('last_cluster_operation', ...)` wrapper
at each site. Finally rename `set_last_cluster_operation` to
`_set_last_cluster_operation` so the only remaining callers
are the enqueue helpers and tests. Plan effort: high; phase
plan effort: high.

**Phase 4.** Drop the dead
`object_metadata.last_cluster_operation_json` column.
Remove the SQLAlchemy column definition (`mariadb.py:566`),
the read paths (`mariadb.py:5071-5072` direct,
`mariadb.py:5168-5169` gRPC), the NULL write
(`mariadb.py:5105`), the `last_cluster_operation` field from
the `ObjectMetadata` Pydantic model and the proto
`ObjectMetadataReply`, and the corresponding
`daemons/database/main.py` plumbing. Add a schema migration
that drops the column. Regenerate protos with
`tox -e genprotos`. Plan effort: medium; phase plan effort:
medium.

**Phase 5.** Update `docs/operator_guide/database.md` to
describe the new gating model and the removal of the
single-pointer field. Update `CLAUDE.md` *Cluster Operation
Targets* entry to reflect that there is no longer a
companion JSON column on `object_metadata`. Update
`ARCHITECTURE.md` if the locking section references
`last_cluster_operation` semantics. Run
`pre-commit run --all-files` and the merge-queue functional
CI suite to confirm no regression. Plan effort: low; phase
plan effort: low.

## Agent guidance

(See PLAN-TEMPLATE.md *Agent guidance* section for the full
execution-model boilerplate. This plan inherits that
guidance verbatim — sub-agents implement, the management
session reviews, opus is the default for cross-daemon
correctness work, sonnet for well-briefed mechanical
sweeps.)

The phase that most warrants opus is phase 3 (auto-target
tracking, audit sweep, and privatisation). It touches every
operation schema, the queue helpers, and changes a contract
that ~20 callers rely on. Phase 1 and 2 are well-scoped and
could be sonnet with a detailed brief. Phase 4 is a
schema-and-proto change — sonnet is fine, with the
management session verifying the migration runs on a fresh
DB and that `tox -e genprotos` was rerun. Phase 5 is
mechanical (haiku acceptable).

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* `Network.is_okay()` (and any sibling gating method)
  returns False only when no non-terminal cluster operation
  targets the network — fully history-aware.
* No call site in `shakenfist/` (outside the
  `*_create_and_enqueue` helpers and tests) calls
  `set_last_cluster_operation` directly. Every cluster-op
  target write is automated by the enqueue helpers.
* `external_view()` for every object type that previously
  exposed `last_cluster_operation` continues to expose the
  same shape, sourced from
  `mariadb.get_latest_cluster_operation_target`.
* The CI failure mode "recreating not okay network on
  hypervisor" no longer fires for first-time creations
  during op-pickup races.
* `pre-commit run --all-files` passes (flake8, stestr unit
  tests, mypy).
* Docs in `docs/` are updated to describe the new gating
  model and deprecation of the single-pointer.
* The full functional CI suite goes green for at least one
  merge-queue run.

### Future work

* **Rewire list-style consumers of `last_cluster_operation`
  to use a pending-operations list query.** Phase 2's
  audit identified two consumers that share the same
  latest-only race as `Network.is_okay()` but cannot use
  the boolean `has_pending_cluster_operation`:
  `shakenfist/baseobject.py:722` `get_cluster_operations()`
  (consumed by three external API endpoints —
  `external_api/network.py:764`,
  `external_api/artifact.py:856`,
  `external_api/instance.py:1725`) and
  `shakenfist/instance.py:1772-1777`
  `Instance.enqueue_delete()` aborting outstanding ops
  before delete. The right fix is a separate
  `get_pending_cluster_operation_targets()` query plus
  rewiring those two consumers — deferred because it
  needs a list-based query rather than a boolean and the
  failure mode (an unaborted op during instance delete)
  is different enough from the network maintainer race
  that bundling them would obscure both fixes.
* **Consider exposing a full history endpoint** — once the
  data is reliably populated, an external API to list all
  cluster ops against a given object would aid debugging.
  Out of scope here.
* **Pushdown `has_pending_cluster_operation` filtering** —
  iterators that walk objects and call `is_okay` would
  benefit from a SQL-side join filter. Defer to the
  pushdown roadmap.

### Bugs fixed during this work

- **Latest-only race in `Network.is_okay()`** — the legacy
  single-pointer read concluded "no op in flight" whenever a
  later terminal op had been written, even if an earlier op was
  still queued or executing. The network maintainer raced the
  queue worker and produced the recurring `recreating not okay
  network on hypervisor` audit event for first-time creations.
  Fixed in phase 2 by switching to `has_pending_cluster_operation()`,
  which joins against `object_states` and checks all target rows
  rather than only the latest one.

- **Forgotten-call race in the hot-plug interface flow
  (commit `8923391c`)** — the API path
  `external_api/instance.py:1028-1029` called
  `set_last_cluster_operation` twice, once on the instance and
  once on the network, but the execution-path call in
  `operations/node_inst_net_iface_op.py:168` was also missing.
  Fixed in phase 3 by moving target writes inside
  `enqueue_cluster_operation` so callers cannot forget.

- **Additional forgotten `set_last_cluster_operation` calls found
  during phase 3 sweep** — the original audit identified six at-risk
  sites; two more were found during implementation:
  `network/network.py:763` in `remove_dhcp_lease` and
  `operations/node_inst_net_iface_op.py:168` in the hot-plug
  execution path. All sites removed in phase 3 when target writes
  were centralised inside `enqueue_cluster_operation`.

### Documentation index maintenance

When this master plan lands, update:

* **`docs/plans/index.md`** — add a row to the *Plan
  Status* table.
* **`docs/plans/order.yml`** — add an entry for this master
  plan (phase files are not added to `order.yml`).

When all phases of the plan are complete, update the status
column in `docs/plans/index.md` to `Complete`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
