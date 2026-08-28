# Phase 5 — Ad-hoc bulk scan cleanup

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 4: [PLAN-sql-pushdown-filtering-phase-04-iterators.md](PLAN-sql-pushdown-filtering-phase-04-iterators.md).

Planning effort: **medium** (sonnet). The patterns are all
in phase 1 and phase 4; this phase extends them to three
iterators that were explicitly out of scope earlier.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly with
particular attention to `shakenfist/network/interface.py`
(the `NetworkInterfaces` iterator with its hybrid
`get_objects_by_state` + `get_all_network_interfaces` +
UUID-set filter), `shakenfist/node.py` (the `Nodes`
iterator that scans UUIDs via `get_all_node_uuids` and does
per-UUID `from_db`), `shakenfist/namespace.py` (the
`Namespaces` iterator that scans names via
`get_all_namespace_names` and does per-name `from_db`),
and any other `mariadb.get_all_*(` call site outside
`.filter()` classmethods or genuinely bulk admin tooling.
Ground any claim in what the code does today. Flag
uncertainty explicitly.

## Goal

Finish the iterator-pushdown story by porting the three
remaining hybrid iterators (`NetworkInterfaces`, `Nodes`,
`Namespaces`) to the same pattern Artifacts / Instances /
Networks got in phase 4. Audit the rest of the tree for
any bulk-scan + Python-filter pattern we missed and fix or
document each.

Non-goals for this phase:

* `Artifact.filter()`, `Instance.filter()`, `Network.filter()`
  classmethods — kept as the documented predicate fallback
  per the master plan.
* `all_instances()` at
  [shakenfist/instance.py:2113](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/instance.py#L2113)
  — uses `get_all_instance_uuids()` + per-UUID `from_db()`.
  N+1 but bounded (instance counts are moderate in practice
  and it's not on any REST hot path). Leave it; revisit in
  future work if measured.
* Admin-only `get_all_*` accessors:
  `mariadb.get_all_cluster_locks` (debug CLI),
  `mariadb.get_all_node_metrics` (resources daemon bulk
  export), `mariadb.get_all_artifact_indexes(self.uuid)`
  (already scoped to a single artifact). Legitimate use
  cases.
* Attribute-column pushdown (Artifact `shared` etc.) — out
  of master-plan scope.
* Renaming `mock_etcd.py` — tracked under master plan
  Future work.

## Design

### NetworkInterfaces

`NetworkInterfaces` at
[shakenfist/network/interface.py:300-349](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/network/interface.py#L300-L349)
is the exact hybrid pattern Artifacts / Instances /
Networks had before phase 4: it resolves prefilter to
target_states, calls `get_objects_by_state` for UUIDs,
then scans `get_all_network_interfaces()` filtering by
UUID-set membership. Phase 4 left it alone because the
phase-1 primitive was only built for the three scoped
object types; NetworkInterfaces needs its own
`find_network_interfaces`.

Plan:

1. Add `find_network_interfaces` to the phase-1
   infrastructure (proto message, direct helper, gRPC
   wrapper, public router, counter registration,
   mock_etcd mock). Mirror the exact shape of
   `find_artifacts` / `find_instances` / `find_networks`.
   `NetworkInterface` has no `namespace` column on its
   static-data pydantic, so the criteria's `namespace`
   filter is a no-op (it's preserved in the criteria
   message for schema consistency but never applied to
   the SQL WHERE clause for this type). Step 5a's back
   brief confirms this by reading `NetworkInterfaceData`.
2. Port `NetworkInterfaces` to the phase-4 iterator
   pattern: `_find(criteria)` → `mariadb.find_network_interfaces(criteria)`,
   `_to_static_values(data)` → `NetworkInterface._static_values_to_dict(data)`.
   Preserve the existing `__iter__` shape. Override
   `_resolve_prefilter_to_states` the same way the
   phase-4 iterators did (return empty set on
   `prefilter=None` so the current "return everything" is
   preserved — confirm by grepping for call sites of
   `NetworkInterfaces(` and their prefilter choices).

### Nodes and Namespaces (no new primitives)

`Nodes` and `Namespaces` both use their own UUID / name
enumeration helpers (`get_all_node_uuids`,
`get_all_namespace_names`) followed by per-key `from_db`.
They don't filter by namespace (Nodes has no namespace;
Namespaces *is* the namespace). State filtering is via
`get_objects_by_state`, same as the base class default.

Plan:

1. Simplify each: remove the `__iter__` override and let
   the base class's `get_iterator` run via the default
   `_find` (which already does `get_objects_by_state` +
   `_db_get`, matching the current per-key `from_db` path
   byte-for-byte). No new `mariadb.find_*` primitive
   needed.
2. For each: override `_resolve_prefilter_to_states` to
   return empty set on `prefilter=None` *only if* the
   current iterator returned all rows when no prefilter
   was set (check in the back brief). If the current
   iterator was already returning ACTIVE_STATES on
   prefilter=None, no override needed — inherit the base
   default.

The audit in step 5a will confirm whether Nodes /
Namespaces currently have the "prefilter=None returns
everything" semantic that phase 4 preserved for the big
three.

### Rest of the audit

`grep -rn "mariadb\.get_all_" shakenfist/ --include="*.py"`
turns up a handful of call sites outside the iterator and
`.filter()` paths. Classify each in step 5a's back brief:

| Caller | Action |
|--------|--------|
| `shakenfist/locks.py:118` `get_all_cluster_locks` | Admin-only; skip. |
| `shakenfist/namespace.py:264` `get_all_namespace_names` | Covered by Namespaces port. |
| `shakenfist/baseobject.py:77` `get_all_node_uuids` | Admin skip — inside `_maintain_version_cache()`, a bounded metrics-cache refresh, not the Nodes iterator. Separate code path from `Nodes.__iter__`. |
| `shakenfist/daemons/resources/main.py:408` `get_all_node_metrics` | Admin-only bulk metrics export; skip. |
| `shakenfist/network/interface.py:332,340,347` `get_all_network_interfaces` | Covered by NetworkInterfaces port. |
| `shakenfist/artifact.py:338,414` `get_all_artifact_indexes(self.uuid)` | Already scoped to one artifact; skip. |
| `shakenfist/instance.py:2114` `get_all_instance_uuids` | `all_instances()` N+1; out of scope. |
| `shakenfist/node.py:627` `get_all_node_uuids` | Covered by Nodes port. |

If the audit discovers anything new, add a step.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 5a   | medium | sonnet | none      | Audit and back brief. Run the grep above; for each result outside `.filter()` and the phase-4 iterators, classify (covered by 5b-5d below, admin skip, or new). Confirm `NetworkInterfaceData` has no namespace column. Confirm current `Nodes.__iter__` and `Namespaces.__iter__` return everything when `prefilter=None` or return ACTIVE_STATES. Report findings. No edits. |
| 5b   | medium | sonnet | none      | Add `find_network_interfaces` to the phase-1 infrastructure in `shakenfist/mariadb.py`: `_build_object_filter_query` already works for any table, so the direct helper is one thin wrapper. Add the gRPC proto message (`FindNetworkInterfacesRequest` / `Reply`), regenerate stubs, add the gRPC handler in `shakenfist/daemons/database/main.py`, register the counter, add `_grpc_find_network_interfaces` and the public `find_network_interfaces` router, and add the `_mariadb_find_network_interfaces` mock in `shakenfist/tests/mock_etcd.py` (honouring `criteria.states` via `mariadb_states`, same as the phase-4 fixup). Model everything on `find_networks`. Do not touch any caller. One commit. |
| 5c   | medium | sonnet | none      | Port `NetworkInterfaces` iterator in `shakenfist/network/interface.py` to the phase-4 pattern: `_find`, `_to_static_values`, maybe `_resolve_prefilter_to_states` per the 5a finding. Drop the hybrid `get_iterator` body. One commit. |
| 5d   | medium | sonnet | none      | Port `Nodes` iterator in `shakenfist/node.py` and `Namespaces` iterator in `shakenfist/namespace.py`. Remove `__iter__` overrides; override `_resolve_prefilter_to_states` per the 5a finding if needed. These use the base-class default `_find` (the N+1 path via `get_objects_by_state` + `_db_get`) — no new primitive. One commit per object type (two commits). |
| 5e   | low    | haiku  | none      | Run `pre-commit run --all-files`. Fix anything flagged (likely unused imports from predicate removal). Commit if changes. |

## Back brief

Before executing any step, the sub-agent must back brief
the operator with:

* Files it intends to change.
* For step 5a: the classification table with one line per
  finding, and the confirmed prefilter=None semantics for
  Nodes / Namespaces.
* For step 5c/5d: the grep result for
  `NetworkInterfaces\(\|Nodes\(\|Namespaces\(` call sites
  in production code, with the prefilter each passes.
* Any design decision not explicit in this plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief. No unrelated edits.
- [ ] `pre-commit run --all-files` passes (flake8, stestr,
      mypy).
- [ ] Proto stubs regenerated with `tox -e genprotos` in
      the same commit as the `.proto` change (step 5b).
- [ ] New counter key appears in the Monitor operations
      list.
- [ ] Commit message references the phase 5 plan with the
      Co-Authored-By line including model / context /
      effort.

## Success criteria for phase 5

* `NetworkInterfaces`, `Nodes`, `Namespaces` iterators
  issue exactly one indexed SQL query per iteration
  (single `find_network_interfaces` for the first;
  `get_objects_by_state` + `_db_get` for the other two,
  which matches the base-class default).
* The hybrid `get_objects_by_state` + `get_all_*` +
  UUID-set filter pattern no longer appears in production
  code (verified by a grep in the success check).
* All `get_all_*(` production call sites are either in
  the documented predicate `.filter()` fallbacks, admin
  tooling, or scoped-to-a-single-parent helpers.
* No caller behaviour changes. Unit tests and the full
  `tox` pass.

## Open questions for this phase

1. **NetworkInterface namespace semantics.** Resolved:
   `find_network_interfaces` accepts `namespace` in the
   criteria but silently ignores it (the direct helper
   does not add a WHERE clause on namespace because
   `NetworkInterfaceData` has no namespace column).
   Consistent proto shape across types, and no production
   caller sets namespace on this type today. When /
   if a caller needs namespace-scoped interface queries,
   the master plan's Future work entry lays out two
   landing options (JOIN through the owning Network, or
   add a column with data migration); either can drop
   into the no-op criteria field as a one-line enable.

2. **`_resolve_prefilter_to_states` override on
   NetworkInterfaces.** Phase 4 overrode this in
   Artifacts / Instances / Networks to preserve the
   pre-phase-4 "no state filter when prefilter=None"
   semantic. The current `NetworkInterfaces.__iter__` has
   an `if self.prefilter:` guard: with `prefilter=None`
   it drops straight into `get_all_network_interfaces()`
   (returns everything, no state filter). So same
   treatment. Step 5c applies it.

3. **Nodes/Namespaces behaviour when prefilter=None.**
   Looking at the current `Nodes.__iter__` and
   `Namespaces.__iter__`: both start with `all_uuids =
   get_all_node_uuids()` (or names) unconditionally and
   apply state filter **only when `self.prefilter`** is
   truthy. So `prefilter=None` returns everything.
   Matches the phase-4 treatment. Step 5d applies the
   same `_resolve_prefilter_to_states` override.
