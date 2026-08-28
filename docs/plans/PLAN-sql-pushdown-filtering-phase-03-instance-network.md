# Phase 3 — Instance and Network pushdown

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 2: [PLAN-sql-pushdown-filtering-phase-02-artifact.md](PLAN-sql-pushdown-filtering-phase-02-artifact.md).

Planning effort: **medium** (sonnet). The phase-2 override and
tests are the template; this phase re-applies the pattern to
two more object types with adjustments for their quirks.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly with
particular attention to `shakenfist/instance.py` (the
Instance class, its constructor which takes a dict via
`_static_values_to_dict`, `ACTIVE_STATES`), `shakenfist/network/network.py`
(the Network class, similar constructor shape, the
`FLOATING_NETWORK_UUID` singleton), `shakenfist/baseobject.py`
(the generic `from_db_by_ref` and `namespace_filter`
semantics), and `shakenfist/mariadb.py` (the phase-1
primitives `find_instances` and `find_networks`). Ground any
claim in what the code does today. Flag uncertainty
explicitly.

## Goal

Replace the Python-side scan in `Instance.from_db_by_ref`
and `Network.from_db_by_ref` with a single call to
`mariadb.find_instances(criteria)` / `mariadb.find_networks(criteria)`
so that every name-based REST lookup
(`shakenfist/external_api/base.py:211` for instances,
`shakenfist/external_api/base.py:319` for networks,
`shakenfist/external_api/instance.py:304` for network
lookups during instance creation) executes exactly one
indexed SQL query that filters on state, namespace, and
name simultaneously.

Non-goals for this phase:

* Changing the `Instances` or `Networks` iterator classes —
  that is phase 4.
* Changing `Instance.filter()` or `Network.filter()`.
  Master plan keeps the predicate API as a documented
  fallback.
* Ad-hoc bulk scans elsewhere in these files — phase 5.
* Any Artifact changes.

## Design

The structure mirrors phase 2 with one key adjustment: both
`Instance` and `Network` take a **dict** in their
constructors (not a Pydantic model), so the override must
convert the `InstanceData` / `NetworkData` returned by
`find_*` via `_static_values_to_dict` before calling `cls(...)`.
This is visible at
[shakenfist/instance.py:344](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/instance.py#L344):

```python
obj = cls(cls._static_values_to_dict(data))
```

and at
[shakenfist/network/network.py:182](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/network/network.py#L182).
Artifact's constructor accepted the Pydantic directly, so
its phase-2 override used `cls(matches[0])` — that does not
port.

### Instance override

```python
@classmethod
def from_db_by_ref(
        cls, object_ref, namespace=None):
    """Look up an instance by UUID or by name within a namespace.

    UUID lookups short-circuit to from_db. Name lookups push
    state + namespace + name down to a single indexed SQL
    query via mariadb.find_instances.
    """
    if object_ref and util_general.valid_uuid4(object_ref):
        return cls.from_db(object_ref)

    criteria_namespace = (
        namespace if namespace and namespace != 'system' else None)

    criteria = ObjectFilterCriteria(
        states=list(cls.ACTIVE_STATES),
        namespace=criteria_namespace,
        name=object_ref,
    )
    matches = mariadb.find_instances(criteria)

    if not matches:
        return None
    if len(matches) > 1:
        raise exceptions.MultipleObjects(
            f'multiple instances have the name "{object_ref}"'
            f' in namespace "{namespace}"')
    return cls(cls._static_values_to_dict(matches[0]))
```

Key points:

* `Instance.ACTIVE_STATES` is a larger set than Artifact's
  (nine state strings including error variants, see
  [shakenfist/instance.py:177-183](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/instance.py#L177-L183)).
  `list(...)` flattens it to the `list[str]` shape
  `ObjectFilterCriteria.states` expects; MariaDB builds an
  `IN (...)` clause.
* Hydration goes through `_static_values_to_dict` since
  Instance's constructor is dict-based.
* Namespace and UUID handling are identical to phase 2.

### Network override

```python
@classmethod
def from_db_by_ref(
        cls, object_ref, namespace=None):
    """Look up a network by UUID or by name within a namespace."""
    if object_ref and util_general.valid_uuid4(object_ref):
        return cls.from_db(object_ref)

    criteria_namespace = (
        namespace if namespace and namespace != 'system' else None)

    criteria = ObjectFilterCriteria(
        states=list(cls.ACTIVE_STATES),
        namespace=criteria_namespace,
        name=object_ref,
    )
    matches = mariadb.find_networks(criteria)

    if not matches:
        return None
    if len(matches) > 1:
        raise exceptions.MultipleObjects(
            f'multiple networks have the name "{object_ref}"'
            f' in namespace "{namespace}"')
    return cls(cls._static_values_to_dict(matches[0]))
```

Considerations specific to Network:

* **`FLOATING_NETWORK_UUID` singleton.** The `Networks`
  iterator skips this UUID
  ([shakenfist/network/network.py:964](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/network/network.py#L964))
  because it is a bookkeeping sentinel, not a real tenant
  network. The `from_db_by_ref` path does not need the same
  skip: the floating network has `namespace=None` (or some
  non-tenant sentinel), so a tenant-scoped query
  (`WHERE namespace = :ns`) won't match it, and a
  system-scoped query that does match it would be an
  administrative lookup where returning the sentinel is
  acceptable. Confirm by checking the floating network's
  stored namespace value in step 3a before writing the
  override; if it is `None` then MariaDB's `namespace = :ns`
  naturally excludes it, and we are done. If it is
  `'system'` or something else, note that in the override.
* **Nullable namespace.** `NetworkData.namespace` is
  `Optional[str]`, unlike Instance/Artifact which require
  it. The SQL equality `WHERE namespace = :ns` correctly
  excludes NULL rows — SQL NULL semantics give us the right
  answer for free.
* **Network state set.** Network does not declare an
  explicit `ACTIVE_STATES` override; it inherits the base
  class default (`{STATE_INITIAL, STATE_CREATING,
  STATE_CREATED, STATE_ERROR, STATE_DELETE_WAIT}`). Confirm
  in step 3a; if it does inherit, `list(cls.ACTIVE_STATES)`
  still works as expected.

### Leave filter() intact

Neither `Instance.filter()` nor `Network.filter()` changes.
They remain the predicate-based fallback. The only prior
caller of these was `DatabaseBackedObject.from_db_by_ref`
via `cls.filter(filters)`; with the override in place that
path no longer runs for name lookups.

### No schema changes

Phase 1 already added `idx_instances_name` and
`idx_networks_name`. No migrations needed here.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 3a   | medium | sonnet | none      | Back brief, then add the `from_db_by_ref` override to the `Instance` class in `shakenfist/instance.py`, positioned immediately after the existing `from_db` method. Add imports for `ObjectFilterCriteria` (from `shakenfist.schema.object_filter`) and `util_general` (from `shakenfist.util`) if not already present. Also confirm in the back brief that `Instance.ACTIVE_STATES` is the set at lines 177-183 and that no other code in-tree references `Instance.filter(` directly. Do NOT remove `Instance.filter()`. |
| 3b   | medium | sonnet | none      | Mirror 3a for the `Network` class in `shakenfist/network/network.py`. Before writing the override, explicitly check and report in the back brief: (1) the stored value of `namespace` for the floating network (grep for `FLOATING_NETWORK_UUID` creation / insertion; likely `None`), and (2) whether `Network` declares its own `ACTIVE_STATES` or inherits the base-class default. If the floating network's namespace is non-None and is a value a user could plausibly pass, surface the concern before making the change. Do NOT remove `Network.filter()`. |
| 3c   | medium | sonnet | none      | Unit tests for the Instance override in `shakenfist/tests/test_instance_from_db_by_ref.py`. Mirror the 7-test structure used in `test_artifact_from_db_by_ref.py`: UUID short-circuit, name+namespace, name+system, name+None, zero match, one match, multi-match raises MultipleObjects. Mock `mariadb.find_instances`. Build mock `InstanceData` instances with the minimum required fields (read `shakenfist/schema/instance_data.py` for the field list); use `Instance.current_version` to match the constructor. Verify the constructor receives a dict produced from `_static_values_to_dict`, not the raw Pydantic. |
| 3d   | medium | sonnet | none      | Unit tests for the Network override in `shakenfist/tests/test_network_from_db_by_ref.py`. Mirror 3c but for Network and mock `mariadb.find_networks`. Build mock `NetworkData` instances; check `shakenfist/schema/network_data.py` for required fields (namespace is Optional). The Network constructor creates or loads an IPAM per network; mock `ipam.IPAM.from_db` to avoid the database dependency — read how `test_network*.py` files already handle this, if any. If the Network constructor's IPAM side-effects make a unit test too heavy, fall back to asserting the call-shape on `mariadb.find_networks` and return-None / MultipleObjects behaviour only, and document the constructor-side-effect gap in the test file docstring. |
| 3e   | medium | sonnet | none      | Functional test for Instance same-name cross-namespace in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_instances.py` (or the most appropriate file — confirm by inspection). Pattern: create instances named 'shared-name' in two namespaces, confirm each namespace's client resolves its own. Keep wall time under 60 seconds (tiny VMs, minimal boot). If the `TestArtifactLookupByName` pattern from phase 2 maps cleanly, reuse it. If the Instance REST path rejects same-name duplicates the way Artifact does, document and skip. |
| 3f   | medium | sonnet | none      | Functional test for Network same-name cross-namespace in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_networks.py` (or equivalent). Same pattern as 3e. |
| 3g   | low    | haiku  | none      | Run `pre-commit run --all-files`. If anything fails, fix and rerun. Commit each logical chunk as the monitoring session directs. |

Commit grouping (to sidestep pre-commit's stash-and-restore
surprise observed in phase 1):

* Instance override + its unit tests (3a + 3c) in one commit.
* Network override + its unit tests (3b + 3d) in one commit.
* Functional tests (3e + 3f) in a third commit.
* Any pre-commit fixes in a fourth commit if needed.

## Back brief

Before executing any step, the sub-agent must back brief
the operator with:

* Files it intends to change and the specific methods.
* Results of the grep/check it was asked to do (e.g. 3b's
  floating-network namespace inspection).
* Any design decision not explicit in this plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief. No unrelated edits.
- [ ] Each override lives immediately after its `from_db`.
- [ ] Each override hydrates via `_static_values_to_dict`
      (not `cls(data)` directly).
- [ ] Existing `filter()` methods unchanged.
- [ ] `pre-commit run --all-files` passes.
- [ ] Unit tests mock `mariadb.find_instances` /
      `mariadb.find_networks`, not the engine.
- [ ] Commit message references the phase 3 plan and the
      Co-Authored-By line includes model / context / effort.

## Success criteria for phase 3

* `Instance.from_db_by_ref` overridden in
  `shakenfist/instance.py`.
* `Network.from_db_by_ref` overridden in
  `shakenfist/network/network.py`.
* Name lookups for either type execute exactly one
  indexed SQL call (verified in the unit tests via
  `mock.assert_called_once`).
* UUID lookups continue to short-circuit through `from_db`.
* `namespace='system'` and `namespace=None` behave
  identically for name lookups.
* `MultipleObjects` is raised when the name is ambiguous
  within the scope.
* Functional tests exist for same-name cross-namespace
  resolution for both Instance and Network.
* `pre-commit run --all-files` passes.
* `Instance.filter()` and `Network.filter()` are unchanged.

## Open questions for this phase

1. **Floating network namespace.** Step 3b's back brief
   will record the stored value. Hypothesis: `None` (the
   floating network is a cross-tenant sentinel). If that
   holds, `WHERE namespace = :ns` naturally excludes it
   and no special handling is required. Confirm before
   writing the override.

2. **Network constructor side-effects in unit tests.** The
   Network constructor calls `ipam.IPAM.from_db` and may
   create an IPAM record if missing. This complicates pure
   unit testing of the override. Options: (a) mock
   `ipam.IPAM.from_db` and `ipam.IPAM.new`; (b) avoid
   exercising the constructor in most tests (assert the
   call-shape on `find_networks` and pre-`cls(...)` branch
   behaviour only). Step 3d's brief asks the sub-agent to
   pick whichever matches the existing test style and
   document any gap.

3. **Instance functional test wall-time.** Instances
   imply a VM boot. If the existing `test_instances.py`
   already creates minimal VMs fast (small memory, no
   cloud-init), we can match that. If it boots a full
   Debian image, we may want to short-circuit the name
   resolution at the REST layer (e.g. create but never
   start, or delete immediately after creation) to keep
   the test fast.
