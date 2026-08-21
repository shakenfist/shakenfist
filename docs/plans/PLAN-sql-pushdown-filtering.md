# SQL-pushdown for object filtering

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
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Object lookup and iteration currently pulls full tables from
MariaDB and filters in Python. The pattern appears in three
shapes:

1. **`DatabaseBackedObject.filter(filters)` overrides** —
   `Instance.filter`, `Network.filter`, and the recently-added
   `Artifact.filter` each call `mariadb.get_all_*()` and then
   apply a list of Python predicates. Only caller is
   `DatabaseBackedObject.from_db_by_ref` at
   `shakenfist/baseobject.py:387`, which in turn is invoked
   from REST handlers for every name-based lookup.

2. **`DatabaseBackedObjectIterator` default** —
   `shakenfist/baseobject.py:754` resolves a prefilter name
   (`active`/`deleted`/`healthy`/`inactive`) to a list of
   states, calls `mariadb.get_objects_by_state()` to pull
   UUIDs matching those states, then hydrates each UUID via
   `base_object._db_get(uuid)`. N+1 queries: one for the
   state list, one per UUID.

3. **`Instances` and `Networks` iterator overrides** —
   `shakenfist/instance.py:2037` and
   `shakenfist/network/network.py:905` do a hybrid: call
   `get_objects_by_state()` for the UUID set, then scan
   `get_all_instances()` / `get_all_networks()` and keep rows
   whose UUID is in the set. Fewer round-trips than the base
   class, but the full table is still pulled over the wire.

4. **`Artifacts` iterator** —
   `shakenfist/artifact.py:513` skips the state prefilter
   entirely and scans `get_all_artifacts()` unconditionally.

5. **Ad-hoc bulk scans** — `shakenfist/instance.py:2057+`,
   `shakenfist/network/network.py:943+`,
   `shakenfist/network/interface.py:332+` scan the full
   table when a secondary index would do.

All of these bypass the indexes that already exist in MariaDB
(notably `idx_object_states_type_state` on `object_states`,
and the primary keys on `artifacts`, `instances`, `networks`).
`CLAUDE.md` is explicit that filtering should be pushed down
where possible, and an automated reviewer flagged the
Artifact case specifically on PR 3160.

## Mission and problem statement

Replace Python-side filtering with parameterised SQL that
joins the per-type table with `object_states` (and with
`*_attributes` where needed) so the database does the work.
This applies to:

* the `from_db_by_ref` hot path for Artifact, Instance and
  Network
* the `Artifacts`, `Instances` and `Networks` iterator
  prefilter paths
* the ad-hoc bulk scans in `instance.py`, `network/network.py`
  and `network/interface.py`

Scope boundaries:

* **In scope:** state, namespace and name pushdown — these
  are the filters the existing predicates express (see
  `state_filter`, `namespace_filter`, `url_filter`,
  `type_filter`, `namespace_exact_filter`, and the name
  comparison in `from_db_by_ref`).
* **Out of scope:** pushdown for predicates that read
  lazily-loaded `*_attributes` fields (e.g. Artifact
  `shared`/`max_versions`, Instance `power_state`). Those
  require a second JOIN and warrant a separate design pass.
* **Out of scope:** operation-queue pushdown — work queue
  already uses indexed SQL.

## Open questions

1. **Filter-predicate translation.** Should we detect known
   `partial(state_filter, ...)` / `partial(namespace_filter,
   ...)` callables at the `filter()` entry point and rewrite
   them to SQL, keeping the existing predicate API for
   callers? Or add a new typed criteria API
   (`filter_by(states=..., namespace=..., name=...)`) and
   migrate callers? The typed API is cleaner but touches every
   caller; the predicate-rewrite keeps signatures stable but
   is fragile (relies on `partial.func` / `partial.args`).
   **Current leaning:** typed API. The predicate API remains
   as a fallback for genuinely custom callables; all
   predefined filters expose a typed equivalent.

2. **gRPC path for new accessors.** Each new SQL-pushdown
   function needs the usual three-layer treatment — direct,
   gRPC handler, public wrapper. How many new proto messages
   is that in total, and is there a shared shape (states,
   namespace, name) we can reuse across object types? **Current
   leaning:** one generic `ObjectFilterCriteria` message plus
   per-type request/response wrappers; counter-register per
   type.

3. **Iterator signature.** The `DatabaseBackedObjectIterator`
   base takes `filters` and `prefilter`. Should we keep both
   or collapse into a single typed criteria? **Current
   leaning:** keep both; `prefilter` is a convenient shorthand
   (`'active'`) that resolves to state values, and the typed
   criteria adds namespace/name/url/type on top.

4. **Result cardinality caps.** Should the SQL queries
   enforce a `LIMIT` on unbounded iterators (paging) to
   protect against runaway callers? **Current leaning:** not
   in this plan — separate work, track under the API Query
   Batching roadmap.

5. **Migration / compatibility.** Do we need to keep the
   Python-side `get_all_*` accessors? They're still used by
   sync loops and admin tooling. **Current leaning:** keep
   them, just stop using them from filter paths.

Please confirm the leanings above before phase 1 planning
begins.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Query infrastructure | PLAN-sql-pushdown-filtering-phase-01-infrastructure.md | Complete |
| 2. Artifact pushdown | PLAN-sql-pushdown-filtering-phase-02-artifact.md | Complete |
| 3. Instance and Network pushdown | PLAN-sql-pushdown-filtering-phase-03-instance-network.md | Complete |
| 4. Iterator rework | PLAN-sql-pushdown-filtering-phase-04-iterators.md | Complete |
| 5. Ad-hoc bulk scan cleanup | PLAN-sql-pushdown-filtering-phase-05-adhoc.md | Complete |
| 6. Tests and documentation | PLAN-sql-pushdown-filtering-phase-06-tests-docs.md | Complete |
| 7. Denormalised child-UUID list removal | PLAN-sql-pushdown-filtering-phase-07-denorm-lists.md | Complete |

Phase outlines (detailed plans to be written when each phase
starts):

**Phase 1 — Query infrastructure.** Design and add a
typed filter-criteria representation (Pydantic) and one
generic `ObjectFilterCriteria` proto message. Add the
three-layer trio (direct, gRPC handler, public wrapper) for a
shared `find_objects` helper that joins the per-type table
with `object_states`. Register counters. This is the largest
phase because it introduces the common primitive the rest of
the work builds on.

**Phase 2 — Artifact pushdown.** Override
`Artifact.from_db_by_ref` to use the phase-1 primitive with
`states=ACTIVE_STATES`, `namespace=...`, `name=...` pushed to
SQL. Remove the Python-side predicate loop for this path.
Leave `Artifact.filter()` for the generic case. Add unit
tests for the SQL path.

**Phase 3 — Instance and Network pushdown.** Mirror phase 2
for Instance and Network `from_db_by_ref` overrides.
Coordinate with any instance-specific lookups (e.g.
`from_db_by_uuid_and_namespace`) that already exist.

**Phase 4 — Iterator rework.** Port `Artifacts`, `Instances`
and `Networks` iterators to use phase-1 primitive so prefilter
states *and* namespace are pushed to SQL. Remove the
`get_all_*()` + set-membership filter pattern. Verify
behaviour parity via existing functional tests.

**Phase 5 — Ad-hoc bulk scan cleanup.** Audit remaining
`get_all_*()` callers outside filter/iterator paths
(`instance.py:2057+`, `network/network.py:943+`,
`network/interface.py:332+` and any new ones discovered) and
rewrite to use the phase-1 primitive or an appropriate
indexed query. Each call site gets its own commit.

**Phase 6 — Tests, documentation and guardrails.** Fill in
any unit-test gaps identified in earlier phases, add
`cluster_ci` coverage for the pushdown paths where behaviour
could regress (name collision within namespace, state
transitions during lookup, cross-namespace lookups under
`system`), and update `docs/operator_guide/database.md` and
`ARCHITECTURE.md` to describe the new pattern.

Also add a guardrail in `PUSH-AUDIT.md` to keep this
regression from sneaking back in: a wave-1 mechanical grep
that flags new additions of `mariadb.get_all_*(` in the diff
(excluding `shakenfist/mariadb.py`, admin tooling under
`shakenfist/client/`, and explicit sync-loop call sites
listed in an allowlist), and a matching bullet in the
wave-2a code-quality brief that names SQL-pushdown
discipline as blocking rather than advisory. Any legitimate
new bulk-scan call site must either be on the allowlist or
accompanied by a comment explaining why pushdown is not
possible.

**Phase 7 — Denormalised child-UUID list removal.** Several
attributes-table fields are hand-maintained lists of child
object UUIDs that became pure denormalisation once MariaDB
gained indexed FK-like columns. Targets:
`network_attributes.networkinterfaces` (replaceable by
`WHERE network_uuid = ?`),
`instance_attributes.interfaces` (replaceable by
`WHERE instance_uuid = ?`), and the adjacent dead
`networkinterfaces_initialized` flag. Each field becomes a
query-backed property that returns live data from the
`network_interfaces` table; the `add_*` / `remove_*`
mutator methods go away. Drop the columns via an
`_ensure_*_schema` migration after callers migrate. Sweep
the ~10 call sites. Not included:
`node_attributes.instances` (would need placement-JSON
pushdown — defer to the attribute-column work),
`node_attributes.daemons` (not object UUIDs),
`namespace_attributes.trust` (graph, not parent-child).

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

Phase 1 and phase 4 touch broadly-used primitives; spawn
those sub-agents with `isolation: "worktree"`. Phases 2, 3,
5, 6, 7 are contained enough to work in the main tree once
the phase plan is tight.

### Planning effort

Each phase plan should be created at the effort level noted
below. Phase 1 is high because the shared primitive has to
work for three object types and one iterator base class;
getting the API wrong here ripples through every later
phase. Phase 4 is high because we're changing iterator
semantics across the codebase.

| Phase | Planning effort | Model |
|-------|-----------------|-------|
| 1 | high | opus |
| 2 | medium | sonnet |
| 3 | medium | sonnet |
| 4 | high | opus |
| 5 | medium | sonnet |
| 6 | medium | sonnet |
| 7 | medium | sonnet |

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants
  (state machine transitions, lock ordering, cross-daemon
  protocol), or researching external references.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a few
  files but the approach is well-defined (e.g. adding a new
  gRPC endpoint mirroring an existing one).
- **low** — Purely mechanical changes (rename, reformat,
  regenerate proto stubs, add a log line).

**Model choice:**
- **opus** — Deep reasoning, cross-daemon architectural
  understanding, subtle correctness judgment (locking,
  state machines, migration logic), or intricate
  implementation where getting it wrong would be costly to
  debug.
- **sonnet** — Good default for well-briefed implementation
  work. Works when the plan front-loads research and the
  brief is detailed.
- **haiku** — Purely mechanical tasks with near-complete
  instructions.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include: what to change,
which files to touch, what patterns to follow (with file +
line references), and any non-obvious constraints. Front-load
research.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `pre-commit run --all-files` passes (flake8, stestr
      unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `Artifact.from_db_by_ref`, `Instance.from_db_by_ref` and
  `Network.from_db_by_ref` resolve a name lookup with exactly
  one indexed SQL query (verified via mypy / test assertion
  on the number of `mariadb.get_all_*` calls).
* `Artifacts`, `Instances` and `Networks` iterators with a
  state or namespace filter execute a single SQL query
  scoped by those filters, rather than scanning the full
  table.
* No production call path calls `mariadb.get_all_*()` as a
  precursor to filtering in Python. The accessors remain for
  sync loops and admin tooling but are not on the filter hot
  path.
* The existing predicate-based `filter()` API still works
  for genuinely custom callables and is documented as a
  fallback.
* Code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, mypy).
* Proto edits are regenerated with `tox -e genprotos` and
  committed in the same commit as the `.proto` change.
* There are unit tests for every new MariaDB primitive and
  at least one `cluster_ci` functional test that would have
  failed without the pushdown (e.g. asserting a name lookup
  doesn't return stale rows from a different namespace).
* Lines wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* `docs/operator_guide/database.md` describes the new
  primitive and the SQL pushdown rule.
* `ARCHITECTURE.md` mentions the three-layer pattern's
  filter-pushdown discipline.
* `PUSH-AUDIT.md` has a wave-1 mechanical grep that
  flags new `mariadb.get_all_*(` additions outside the
  allowlist, and the wave-2a brief marks SQL-pushdown
  discipline as blocking.

### Future work

Items explicitly deferred out of this plan to avoid scope
creep:

* **Attribute-column pushdown.** Filters that read
  lazily-loaded `*_attributes` fields (Artifact `shared` /
  `max_versions`, Instance `power_state` / `ports`) will
  still evaluate in Python. A follow-up plan should add the
  second JOIN and confirm the attributes tables have the
  right indexes.
* **`LIMIT` / pagination.** Unbounded iterators stay
  unbounded here. Track under the API Query Batching
  roadmap.
* **Event-table pushdown.** Event listing and search is
  similar shape (scan + Python filter) but has its own
  indexes and callers; out of scope for this plan.
* **Admin/sync loops.** `get_all_*` accessors used by
  periodic cluster maintenance remain. Whether they also
  benefit from pushdown is a separate question.
* **Rename / rethink `shakenfist/tests/mock_etcd.py`.**
  Stale name from the pre-MariaDB era. It no longer mocks
  etcd — it now provides an in-memory fake of the
  `shakenfist.mariadb` public API. Minimum: rename the file
  and class (mechanical, ~40+ import sites). Bigger
  rethink: decide whether a stateful hand-rolled fake is
  still the right shape now that phase 1 gave us real
  SQLAlchemy primitives — a sqlite-backed test DB using
  the actual engine would exercise the real SQL
  (including the pushed-down state/namespace JOINs added
  in phases 1-4) instead of a second implementation that
  can drift. Track separately; do not bundle with any
  SQL-pushdown phase.
* **NetworkInterface namespace column.** `NetworkInterfaceData`
  has no namespace column today; interface-namespace ownership
  is derived from the owning `Network` (or `Instance`). Phase 5
  investigation found zero current production call sites that
  filter interfaces by namespace — the dnsmasq DHCP path uses
  the per-Network `networkinterfaces` attribute rather than a
  namespace-scoped query. Defer until there is a concrete
  caller. When that caller appears, decide between: (a)
  JOIN-based pushdown through `networks.namespace` or
  `instances.namespace` (no schema change, no denormalisation
  risk), or (b) adding the column with a data-migration v-bump
  (consistent shape with Artifact/Instance/Network but risks
  drift if a Network is ever reassigned). `find_network_interfaces`
  in phase 5 accepts a namespace criteria field that is
  currently a no-op so either path can land as a one-line
  enable.

#### Items raised by the phase 7 pre-push audit

The `PUSH-AUDIT.md` audit on the post-phase-7 branch
flagged a handful of items that are real but not phase-7
specific. Capturing them here so the next contributor in
this area has a punch list rather than re-discovering
them:

* **Refactor the duplicated `from_db_by_ref` overrides.**
  The pattern (UUID short-circuit → namespace='system'/None
  collapsed to `criteria_namespace=None` → build
  `ObjectFilterCriteria` → call the type's `find_*` →
  raise `MultipleObjects` on duplicates) is now ~25 lines
  copy-pasted in `shakenfist/artifact.py`,
  `shakenfist/instance.py`, and `shakenfist/network/network.py`.
  A shared base-class helper that takes a criteria-builder
  callback would eliminate the drift risk before a fourth
  type lands.
* **`FindArtifacts` gRPC handler error reporting.** The
  except branch in `shakenfist/daemons/database/main.py`
  for `FindArtifacts` omits the `context.set_code(grpc.StatusCode.INTERNAL)`
  and `context.set_details(str(e))` calls that the other
  three new `Find*` handlers use. Inconsistency, not a
  regression — gRPC callers get a less descriptive error
  on database failure.
* **`Namespaces(prefilter=None)` silently yields nothing.**
  `_resolve_prefilter_to_states` returns an empty set when
  `prefilter is None`, which the base `_find` translates to
  a `state_value IN ()` clause that matches zero rows. The
  docstring still promises "every namespace". Currently
  dormant because the only production caller passes
  `prefilter='active'`. Either fix the resolver to return
  no state filter for the `None` case, or update the
  docstring; do not leave the contract mismatched.
* **`interfaces_for_instance` is now weaker than its
  caller.** `shakenfist/network/interface.py:interfaces_for_instance`
  iterates the full active-NI set and filters in Python.
  `Instance.interfaces` (phase 7d) does the same job via a
  `WHERE instance_uuid = ?` index lookup. Two remaining
  callers in `shakenfist/daemons/queues/startup_tasks.py`
  and `shakenfist/operations/node_inst_op.py` should
  migrate to `inst.interfaces` (or call
  `find_network_interfaces` with `instance_uuid` set
  directly) to benefit from the index.
* **Compile-time guard against unsupported criteria
  fields.** `_build_object_filter_query` silently ignores
  fields that don't map to a column on the target table —
  it relies on each `_direct_find_*` helper remembering to
  strip them. Adding an explicit per-type whitelist (or
  raising at construction time) would prevent a future
  helper from forgetting and tripping a runtime
  `AttributeError`.
* **Trailing test gaps from phase 7.** Phase 7d's
  ``Instance.interfaces`` shape change is exercised
  through cluster_ci and indirectly via
  ``test_make_config_drive``, but lacks a focused unit
  test (analogous to phase 7's
  ``NetworkInterfacesPropertyTestCase`` covering
  ``Network.networkinterfaces``). Likewise
  ``DnsMasq._enumerate_leases`` with an attached NI, and a
  combined ``network_uuid``+``instance_uuid`` filter case
  in ``DirectFindNetworkInterfacesTestCase``, are missing.
  Low-risk: fill in opportunistically when next touching
  these files.
* **`Artifact.hard_delete` does not clean MariaDB rows.**
  `NetworkInterface.hard_delete` correctly clears both
  `network_interface_attributes` and `network_interfaces`;
  the equivalent override on `Artifact` is missing, so
  hard-deleting an artifact leaves orphan
  `artifacts` / `artifact_attributes` rows. Pre-existing
  hygiene gap, not introduced by SQL pushdown work.
* **Database gRPC service is unauthenticated
  (pre-existing).** `shakenfist/daemons/database/main.py`
  binds via `add_insecure_port` with no interceptor. Phase
  1-7's new `Find*` RPCs inherit the gap. Anyone with TCP
  reach to `DATABASE_API_PORT` can enumerate any tenant's
  objects via the criteria fields. Not regressed by this
  plan but worth tracking — fix is service-mesh ACL,
  shared-cluster JWT interceptor, or mTLS on the channel.
* **`NetworkInterface.order` race on concurrent hot-plug
  (pre-existing).** Two concurrent
  `InstanceInterfacesEndpoint.post` requests on the same
  instance can both compute the same `max(order)+1` and
  create two NICs with the same `order`. There is no
  UNIQUE constraint on `(instance_uuid, order)`. Phase 7d
  did remove the `interfaces` lock from
  `interfaces_append`, but that lock only protected the
  cached UUID list, not the order computation — so this
  is *not* a regression. Fix is either a lock around the
  read+create or a UNIQUE constraint with retry-on-collide
  (mirroring the MAC-collision handling already in
  `network/interface.py:NetworkInterface.new`).

### Bugs fixed during this work

Real bugs the rollout caught, outside the plan's design
decisions:

* **Stability-branch merge-CI regressions.** The work
  started from three independent bugs on the stability
  branch that were making Debian 12 merge-CI time out:
  `NotImplementedError: Artifact must override filter()`
  (fixed in commit 915de7e7 by overriding the classmethod
  — phase 2 later pushed this further to SQL),
  `RuntimeError: MARIADB_HOST not configured` from
  `get_active_blob_uuids()` calling `_direct_*` on
  non-primary nodes (5d620d90), and `KeyError` on three
  unregistered cluster-operation counters in the database
  daemon (a812a44c).
* **mock_etcd state-filter fixup (phase 4).** The existing
  `_mariadb_find_*` mocks ignored `criteria.states`. They
  passed while the iterators still routed through the old
  `get_all_* + cls.filter` path (which had its own state
  check). With the phase-4 port the iterators hit
  `find_*` directly, so the mocks were exposed as a
  silent-match hazard. Now they cross-reference
  `self.mariadb_states` the same way the real SQL JOIN on
  `object_states` does.
* **Phase-4 `_resolve_prefilter_to_states` regression.**
  The unified base-class default coerced `prefilter=None`
  to `ACTIVE_STATES`, but the pre-phase-4
  Artifacts/Instances/Networks overrides had returned
  every row when no prefilter was set. Two
  `InstancesTestCase` tests caught this. Fix: per-type
  override returning `set()` on `prefilter=None`; base
  class default unchanged so IPAMs/AgentOperations keep
  working.
* **Namespaces alphabetical-ordering regression
  (phase 5).** The pre-phase-5 `Namespaces.__iter__`
  relied on `mariadb.get_all_namespace_names()` returning
  names in alphabetical order. The new `_find` path goes
  through `get_objects_by_state` which returns
  insertion-order UUIDs. `test_auth.test_get_namespaces`
  caught this. Fix: sort by name inside
  `Namespaces.__iter__` before yielding.

### Documentation index maintenance

When this master plan was created:

* A row was added to the *Master plans* table in
  `docs/plans/index.md`.
* An entry was added to `docs/plans/order.yml`.

Phase plan files are linked from the Execution table above
and are not individually listed in `order.yml`.

When all phases of a plan are complete, update the status
column in `docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
