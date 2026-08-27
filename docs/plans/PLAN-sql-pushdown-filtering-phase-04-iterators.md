# Phase 4 — Iterator rework

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 3: [PLAN-sql-pushdown-filtering-phase-03-instance-network.md](PLAN-sql-pushdown-filtering-phase-03-instance-network.md).

Planning effort: **high** (opus). The iterator base class is
broadly used (23+ call sites across daemons, REST handlers,
operations, and other object modules) and a signature change
ripples widely. Getting the design wrong here is expensive to
back out.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly with
particular attention to `shakenfist/baseobject.py`
(`DatabaseBackedObjectIterator` base class with its
`filters`/`prefilter`/`get_iterator` shape),
`shakenfist/artifact.py` (the `Artifacts` iterator with its
own `__iter__` and `get_iterator` overrides),
`shakenfist/instance.py` (the `Instances` iterator with a
hybrid `get_all_instances` + UUID-set filter),
`shakenfist/network/network.py` (the `Networks` iterator,
similar hybrid), and the 23+ in-tree call sites of these
three iterators. Ground any claim in what the code does today.
Flag uncertainty explicitly.

## Goal

Replace the N+1 (base class) and full-table-scan (concrete
overrides) iterator paths with a single `mariadb.find_*(criteria)`
call per iteration, pushing both state and namespace to SQL.
Preserve the predicate-filter API for custom callables
(e.g. `namespace_or_shared_filter`, `url_filter`,
`type_filter`) that have no simple SQL equivalent.

Non-goals for this phase:

* Ad-hoc bulk scans outside the iterator classes
  (`instance.py:2057+` and similar) — that is phase 5.
* Attribute-column pushdown (Artifact `shared`, Instance
  `power_state` et al.) — out of scope per the master plan.
* Adding `LIMIT`/pagination — API Query Batching roadmap.
* Deleting `Artifact.filter() / Instance.filter() / Network.filter()`
  — those remain as predicate-API fallbacks.

## Design

### Typed criteria on the iterator

Extend `DatabaseBackedObjectIterator.__init__`:

```python
def __init__(self, filters=None, prefilter=None, namespace=None,
             suppress_failure_audit=False):
    self.filters = filters or []
    self.prefilter = prefilter
    self.namespace = namespace
    self.suppress_failure_audit = suppress_failure_audit
```

`namespace=None` preserves current behaviour
(no namespace filter applied). Callers that want namespace
pushdown pass `namespace='tenant-a'`. The 'system' meaning
matches `baseobject.namespace_filter`: when a caller passes
`namespace='system'`, the iterator collapses that to
`None` internally (i.e. no namespace WHERE clause), same
rule we established in phases 2 and 3.

`filters` defaults to `None` (treated as `[]`) so
no-filter calls simplify to `Artifacts(prefilter='active')`
and `Networks(namespace=ns)`. Existing positional-list
callers continue to work.

### Base class get_iterator rewrite

Replace the current two-step resolve-states + per-UUID
`_db_get` loop with a single `find_*` call:

```python
def get_iterator(self):
    target_states = self._resolve_prefilter_to_states()
    criteria_namespace = (
        self.namespace
        if self.namespace and self.namespace != 'system'
        else None)
    criteria = ObjectFilterCriteria(
        states=list(target_states),
        namespace=criteria_namespace,
    )
    for data in self._find(criteria):
        yield str(data.uuid), self._to_static_values(data)
```

Two new abstract-ish methods for concrete iterators to fill
in:

* `_find(criteria)` — returns an iterable of Pydantic
  model instances. Each concrete iterator implements it by
  calling the matching `mariadb.find_*`. If none of the
  three matches, the default raises `NotImplementedError`
  with a clear message (mirroring the phase 1 `_db_get`
  pattern).
* `_to_static_values(data)` — converts the Pydantic to
  whatever shape the object's constructor expects.
  * Artifacts: pass through (constructor accepts Pydantic
    directly).
  * Instances / Networks: `cls.base_object._static_values_to_dict(data)`.

`_resolve_prefilter_to_states` is the existing switch block
extracted into a helper for reuse.

### Concrete iterator overrides

Each concrete iterator becomes a ~10-line subclass:

```python
class Artifacts(dbo_iter):
    base_object = Artifact

    def _find(self, criteria):
        return mariadb.find_artifacts(criteria)

    def _to_static_values(self, data):
        return data  # Artifact accepts ArtifactData directly

    def __iter__(self):
        for _, static_values in self.get_iterator():
            obj = Artifact(static_values)
            filtered = self.apply_filters(obj)
            if filtered:
                yield filtered
```

`Instances` and `Networks` follow the same shape, with
`_to_static_values` calling `_static_values_to_dict` and
the constructor taking a dict.

Notably:

* The `__iter__` method on `Artifacts` stops scanning
  `get_all_artifacts` — now it calls `get_iterator`, which
  calls `find_artifacts`, which JOINs on `object_states`
  and filters in SQL.
* `Instances.get_iterator` stops doing the two-phase
  `get_objects_by_state` + `get_all_instances` + UUID-set
  filter; it's one SQL call.
* `Networks.get_iterator` same.
* The `FLOATING_NETWORK_UUID` skip in the existing
  `Networks.__iter__` stays (it's still a legitimate
  bookkeeping sentinel to hide from tenant iteration);
  this is a Python-side check, not a SQL filter.

### Caller migration

23 call sites currently construct an iterator. The phase
splits them into three buckets:

**Migrate to namespace kwarg** (predicates that are exactly
`partial(baseobject.namespace_filter, ns)`):

* [shakenfist/artifact.py:609](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/artifact.py#L609)
  (`artifacts_in_namespace`)
* [shakenfist/network/network.py:1016](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/network/network.py#L1016)
  (equivalent network helper)
* [shakenfist/instance.py:2137](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/instance.py#L2137)
  (`instances_in_namespace`)
* [shakenfist/external_api/network.py:292](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/external_api/network.py#L292)
* [shakenfist/external_api/instance.py:893](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/external_api/instance.py#L893)

**Migrate to namespace kwarg with a tweak**
(predicates that are `partial(namespace_exact_filter, ns)` —
exact match, no 'system' special case):

* [shakenfist/external_api/artifact.py:387](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/external_api/artifact.py#L387)

For this one the caller knows namespace is never 'system'
(it's an admin path or a pre-checked tenant lookup), so
using the `namespace=ns` kwarg is behaviourally identical.
Flag this in the step 4e brief.

**Leave as Python predicate** (custom logic):

* `partial(namespace_or_shared_filter, ns)` — reads
  `shared` attribute (separate table).
* `url_filter`, `type_filter`, `instance_snapshot_filter`,
  `this_node_filter` — predicate-only.
* `[instance.this_node_filter]` etc. — same.

These keep their current predicate form; the SQL pushdown
still happens for the state prefilter, the namespace kwarg
is just `None`.

### Behaviour preservation

* `prefilter=None` continues to mean `ACTIVE_STATES`
  (the base class already coerces).
* Empty `filters=[]` continues to match everything.
* The `FLOATING_NETWORK_UUID` skip in `Networks.__iter__`
  is preserved.
* `suppress_failure_audit` is preserved on the base class.
* The `Networks.__iter__` has an extra early-yield check
  for deleted networks that we must trace through to be
  sure we don't regress — step 4d confirms.

### Risk: state-set differences

Each object type has its own `ACTIVE_STATES`. The phase
already relies on `self.base_object.ACTIVE_STATES`, so the
correct set is picked up automatically. Confirmed.

### Risk: namespace on Network

`NetworkData.namespace` is `Optional[str]` (nullable for
the floating network). A caller passing `namespace=None`
as the kwarg still means "no filter". A caller passing
`namespace='tenant-a'` means `WHERE namespace = 'tenant-a'`,
which excludes NULL rows by SQL semantics — correct.

### Base-class default: preserve N+1 path (revised)

Initial plan was to raise `NotImplementedError` from the
default `_find`. Step 4a's back brief found five other
subclasses of `DatabaseBackedObjectIterator` beyond the
three in scope: `NetworkInterfaces` (has its own
`get_iterator` override — safe), `IPAMs` and
`AgentOperations` (use the default `get_iterator` —
would break if we raise), and `Namespaces` / `Nodes`
(override `__iter__` end-to-end and never call
`get_iterator` — safe but latent footgun).

Decision: **preserve the existing N+1 path as the default
`_find`.** `get_objects_by_state` + per-UUID `_db_get`
continues to work for subclasses that haven't opted in.
Subclasses that want the fast single-SQL path override
`_find` (Artifacts, Instances, Networks do this in
4b/4c/4d). No breakage for IPAMs or AgentOperations.

This is strictly weaker than "raise" for catching new-
iterator-author mistakes, but it's the correct call given
existing callers depend on the default. Future work: audit
the remaining subclasses in a separate plan and migrate
them to explicit `_find` overrides so the default can
eventually become strict.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 4a   | high   | opus   | worktree  | Rewrite `DatabaseBackedObjectIterator` in `shakenfist/baseobject.py`: add `namespace` kwarg to `__init__`, split the prefilter-state resolver into `_resolve_prefilter_to_states`, rewrite `get_iterator` to call a new `_find(criteria)` hook with `ObjectFilterCriteria`, keep `apply_filters` as-is. Default `_find` raises `NotImplementedError` (operator-confirmed). Before raising, grep the tree for any live caller that instantiates `DatabaseBackedObjectIterator` directly or a subclass that does not override `_find` — if any, surface in the back brief before landing. Import `ObjectFilterCriteria`. Do not change any concrete iterator yet. Isolate in a worktree because this touches baseobject.py — broadly imported. |
| 4b   | medium | sonnet | none      | Port the `Artifacts` iterator in `shakenfist/artifact.py`: implement `_find` and `_to_static_values`, remove the old `__iter__` scan-all and the old `get_iterator` override. Update any caller in `artifact.py` that passes `partial(baseobject.namespace_filter, ns)` to use the new kwarg. |
| 4c   | medium | sonnet | none      | Port the `Instances` iterator in `shakenfist/instance.py`. Remove the hybrid `get_objects_by_state` + `get_all_instances` path. Update internal callers in `instance.py` to use `namespace=` kwarg where applicable. |
| 4d   | medium | sonnet | none      | Port the `Networks` iterator in `shakenfist/network/network.py`. Same shape as 4c. Preserve the `FLOATING_NETWORK_UUID` skip in `__iter__`. Confirm no behaviour regression on the nullable-namespace path. |
| 4e   | medium | sonnet | none      | Migrate REST handler / daemon call sites that pass `partial(baseobject.namespace_filter, ns)` (or `partial(namespace_exact_filter, ns)` where safe) to the new `namespace=` kwarg. Files: external_api/artifact.py:387, external_api/network.py:292, external_api/instance.py:893, plus any internal helpers in artifact.py, instance.py, network/network.py that still use the predicate form. Leave `namespace_or_shared_filter` and custom predicates alone. Expect 6-8 call sites. Each migration is a one-line change. |
| 4f   | medium | sonnet | none      | Unit tests. (1) Extend `shakenfist/tests/test_mariadb_find.py` or add a new test file covering the iterator's typed kwarg path: `Artifacts(namespace='ns', prefilter='active')` issues exactly one `mariadb.find_artifacts` call with the right criteria. (2) Confirm predicate callers that stayed with `filters=` still work (mock `mariadb.find_artifacts` returning a list, then verify `apply_filters` rejects items that don't match the predicate). Mirror for Instances and Networks. |
| 4g   | medium | sonnet | none      | Extend `shakenfist/tests/mock_etcd.py` mocks for `find_*` to also honour the state prefilter (they currently ignore `criteria.states` because pre-phase-4 callers always went through `get_all_*` and then the existing object-state machinery did the state check in Python). Phase 4 eliminates that second path, so the mock must apply state filtering now — or the iterator-driven tests in the existing test suite will fail. Fold into step 4a's commit if easy; otherwise separate commit. |
| 4h   | low    | haiku  | none      | Run `pre-commit run --all-files`. Fix anything flagged. |

Commit grouping (respecting the phase-1 stash-and-restore
lesson):

* 4a + 4g (base class + mock update) in one commit — both
  are prerequisites for any concrete iterator test.
* 4b (Artifacts port) + tests in one commit.
* 4c (Instances port) + tests in one commit.
* 4d (Networks port) + tests in one commit.
* 4e (caller migrations) in one commit.

This ordering also lets us run unit tests and the existing
REST suite after each concrete port to catch regressions
object-type by object-type.

## Back brief

Before executing any step, the sub-agent must back brief
the operator with:

* Files it intends to change and the specific methods.
* For step 4a: whether the base-class default `_find`
  raises or keeps N+1 behaviour, with a reason.
* For step 4g: confirmation that the state-mapping logic
  matches what the real SQL `JOIN object_states` does
  (particularly around the `None`/`[]` states distinction).
* Any design decision not explicit in this plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief. No unrelated edits.
- [ ] `pre-commit run --all-files` passes (flake8, stestr,
      mypy).
- [ ] For iterator ports: a grep for
      `mariadb\.get_all_(artifacts|instances|networks)` in
      the ported file no longer matches (or only matches
      in intentionally-preserved admin paths that phase 5
      will clean up).
- [ ] Callers migrated to `namespace=` still pass their
      existing functional tests under tox (we don't run
      cluster_ci here; trust the unit tests and the
      behaviour-preserving review).
- [ ] Commit messages reference the phase 4 plan and
      include the Co-Authored-By line with model / context
      / effort.

## Success criteria for phase 4

* `Artifacts`, `Instances`, `Networks` iterators each issue
  exactly one SQL query per iteration (verified via
  mocked assertion in the unit tests).
* No production iterator path calls `mariadb.get_all_*` +
  Python filter.
* `DatabaseBackedObjectIterator.__init__` accepts a
  `namespace` kwarg and the 6-8 call sites that
  previously used `partial(baseobject.namespace_filter, ns)`
  now use the kwarg.
* Custom predicate filters still work (
  `namespace_or_shared_filter`, `url_filter`,
  `type_filter`, `instance_snapshot_filter`,
  `this_node_filter`).
* `FLOATING_NETWORK_UUID` still hidden from tenant
  network iteration.
* `mock_etcd.py` mocks apply state + namespace + name
  filters consistently (needed because phase 4 removes the
  `get_all_*` fallback that was previously quietly doing
  state filtering in the base class iterator).
* `pre-commit run --all-files` passes.

## Open questions for this phase

1. **Base-class default `_find` behaviour.** Revised
   during step 4a's back brief: **preserve the N+1 path**
   as the default (get_objects_by_state + per-UUID
   _db_get). Raising turned out to break IPAMs and
   AgentOperations, which use the default `get_iterator`
   today. Future work (separate plan) should audit and
   migrate the remaining subclasses so the default can
   eventually raise; for now the slow path continues to
   function for opted-out types while Artifacts, Instances,
   and Networks get the fast path via explicit overrides.

2. **State-filter semantics on `mock_etcd`.** The existing
   mocks were implemented trusting that state filtering
   happened elsewhere. Step 4g updates them to apply the
   state filter. The question is: what state values does
   the mock track per object? Today the mock stores
   Pydantic `*Data` objects which do **not** include state;
   state is stored in a separate `object_states` machinery.
   Either: (a) the mock adds minimal state tracking
   tied to the object's creation path (defaulting to
   `'created'`), or (b) the mock always yields all objects
   regardless of `criteria.states`, matching the current
   "state filter ignored by mock" behaviour. **Current
   leaning:** (b) with an explanatory comment — REST unit
   tests don't need state-discrimination coverage; phase 4
   only needs the mocks to not regress. If any test turns
   out to depend on state discrimination, promote to (a)
   and track as followup.

3. **Scope of the iterator-driven test coverage.** The
   existing cluster_ci tests exercise every iterator via
   the REST API. Do we need new functional coverage? 
   **Current leaning:** no — the phase-1/phase-2/phase-3
   coverage is sufficient and the existing cluster_ci
   suite already hits all three iterator paths. New
   functional tests would be speculative and slow down
   the push cycle.
