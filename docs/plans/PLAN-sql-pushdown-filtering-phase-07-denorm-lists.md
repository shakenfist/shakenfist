# Phase 7 — Denormalised child-UUID list removal

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).
Phase 6: [PLAN-sql-pushdown-filtering-phase-06-tests-docs.md](PLAN-sql-pushdown-filtering-phase-06-tests-docs.md).

Planning effort: **medium** (sonnet). Mechanical refactor
with two flavours of surgery: one data-shape change
(property return type flips from list-of-UUID-strings to
list-of-NetworkInterface-objects) and one schema migration
(drop now-unused columns).

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly with
particular attention to `shakenfist/network/network.py`
(`Network.networkinterfaces` property + `add_networkinterface`
/ `remove_networkinterface` mutators + the
`networkinterfaces_initialized` flag),
`shakenfist/instance.py` (`Instance.interfaces` property,
setter, and `interfaces_append` helper),
`shakenfist/schema/network_attributes.py` and
`shakenfist/schema/instance_attributes.py` (the stored
column shapes), and the ~14 production call sites that read
or write these attributes. Ground any claim in what the
code does today. Flag uncertainty explicitly.

## Goal

Replace two hand-maintained lists of child-object UUIDs
with query-backed properties that read the live
`network_interfaces` table:

* `Network.networkinterfaces` (currently
  `network_attributes.networkinterfaces: list[str]`) →
  replaced by `WHERE network_uuid = self.uuid` on the
  `network_interfaces` table (the `network_uuid` column is
  already `SQLIndex()`'d).
* `Instance.interfaces` (currently
  `instance_attributes.interfaces: list[str]`) →
  replaced by `WHERE instance_uuid = self.uuid` on the same
  table (same index story).

Drop the matching `add_*` / setter / `*_append` mutators,
remove the dead `networkinterfaces_initialized` flag, and
drop the stored columns via an `_ensure_*_schema` migration.

**Behaviour change**: both properties today return
`list[str]` of UUIDs. Post-phase they return
`list[NetworkInterface]` — hydrated objects. Callers that
do `for uuid in x: ni = NetworkInterface.from_db(uuid)`
shorten to `for ni in x`. One SQL query replaces N+1.

Non-goals for this phase:

* `node_attributes.instances` — would need attribute-column
  pushdown (placement is a JSON column). Tracked under
  Future work, not this phase.
* `node_attributes.daemons` — not object UUIDs.
* `namespace_attributes.trust` — a graph, not a
  parent-child list.
* Adding a `namespace` column to `NetworkInterface` — still
  deferred per its own Future-work entry. Phase 7 does not
  change `NetworkInterfaceData`'s shape.

## Design

### Primitive: extend `find_network_interfaces` with FK filters

The phase-1 `ObjectFilterCriteria` has `states`,
`namespace`, `name`. To support "interfaces where
`network_uuid = X`" and "interfaces where
`instance_uuid = X`" we extend the criteria with two
optional fields:

```python
class ObjectFilterCriteria(BaseModel):
    states: Optional[list[str]] = None
    namespace: Optional[str] = None
    name: Optional[str] = None
    # New in phase 7 — currently honoured only by
    # _direct_find_network_interfaces since they refer to
    # columns on the network_interfaces table. Other types
    # silently ignore (same rule already applied to
    # namespace/name on NetworkInterface).
    network_uuid: Optional[str] = None
    instance_uuid: Optional[str] = None
```

Corresponding proto fields on the `ObjectFilterCriteria`
message (optional), regenerated stubs.

`_build_object_filter_query` stays generic — callers pass
fields that are known-valid for the target table.
`_direct_find_network_interfaces` is the only direct
helper that threads these through. Other direct helpers
(artifacts/instances/networks) already strip unrelated
criteria; no change needed there.

Add two mock-friendly filters to
`_mariadb_find_network_interfaces` in `mock_etcd.py`.

Unit tests for the new filter path go into
`test_mariadb_find.py` — two new cases on
`DirectFindNetworkInterfacesTestCase`:
`test_network_uuid_filter` and `test_instance_uuid_filter`.

### Network.networkinterfaces becomes query-backed

Before:
```python
@property
def networkinterfaces(self):
    attrs = self._ensure_attributes()
    return attrs.networkinterfaces  # list[str]
```

After:
```python
@property
def networkinterfaces(self):
    """Currently-attached NetworkInterface objects.

    Previously cached as a list of UUID strings on
    network_attributes; now queried live from the
    network_interfaces table which has network_uuid
    indexed.
    """
    # Late import avoids a circular dependency
    # network.network -> network.interface -> network.network.
    from shakenfist.network import interface
    criteria = ObjectFilterCriteria(
        states=list(interface.NetworkInterface.ACTIVE_STATES),
        network_uuid=str(self.uuid),
    )
    return [
        interface.NetworkInterface(
            interface.NetworkInterface._static_values_to_dict(d))
        for d in mariadb.find_network_interfaces(criteria)
    ]
```

Drop `add_networkinterface`, `remove_networkinterface`,
and the `networkinterfaces_initialized` flag.

### Instance.interfaces becomes query-backed

Identical shape, with `instance_uuid` instead of
`network_uuid`. Drop the setter and `interfaces_append`;
the interface creation / deletion paths that used to write
`inst.interfaces = ...` no longer need to — they already
write the NetworkInterface row itself, which is now the
single source of truth.

Call sites that read `.interfaces` iterate objects instead
of UUIDs. Call sites that *wrote* the list (setter calls)
are removed entirely.

### Caller migration

Two distinct patterns to update (~14 call sites total,
audited in step 7a):

**Readers** — change `for uuid in x: ni =
NetworkInterface.from_db(uuid)` to `for ni in x`. For
boolean checks (`if not x.networkinterfaces`), the new
list-of-objects is truthy / falsy the same way a
list-of-strings is, so no change. For calls that pass the
list to a helper (e.g.
`wait_interfaces=network_from_db.networkinterfaces`),
adapt the helper signature — step 7c's back brief lists
each helper.

**Writers** — delete. The setter
`inst.interfaces = iface_uuids`, the mutator calls
`n.add_networkinterface(ni)` and `n.remove_networkinterface(self)`,
and `instance.interfaces_append(...)` all become no-ops
and are removed. The NetworkInterface row itself is what
makes the association; writing the attribute was pure
bookkeeping.

### Schema migration

Drop the columns from the pydantic schemas and add an
idempotent v-bump migration to each of
`_ensure_network_attributes_schema` and
`_ensure_instance_attributes_schema`:

```sql
ALTER TABLE network_attributes DROP COLUMN networkinterfaces;
ALTER TABLE network_attributes DROP COLUMN networkinterfaces_initialized;
ALTER TABLE instance_attributes DROP COLUMN interfaces;
```

Wrap in `try/except (IntegrityError, OperationalError)` the
way the phase-1 `idx_<table>_name` migration did — the
drop is idempotent so a re-run is a no-op.

Column drop is safe because the stored list is pure
cache: every UUID in the list is derivable from the
`network_interfaces` table via an indexed query.

### PUSH-AUDIT.md guardrail

The phase-6 guardrail catches new `mariadb.get_all_*(`
call sites; phase 7's work exposes a second pattern worth
guarding against — new `list[str]` / `list[UUID]` fields
on `*_attributes.py` schemas that are really cached FK
lists of child-object UUIDs.

A mechanical grep would false-positive on legitimate
non-FK list fields (e.g. `namespace_attributes.trust`,
`node_attributes.daemons`), so this guardrail lives in the
wave-2a code-quality judgment brief rather than wave-1. A
new bullet directs the reviewing sub-agent to flag any new
`list[str]` / `list[UUID4]` field on an `*_attributes.py`
schema as a review point: "is this a cached list of child
object UUIDs that a `WHERE <fk> = ?` query could provide
live? If so, the property should be query-backed and the
column should not exist."

Mutator-pair additions (`add_*` / `remove_*` methods that
append/remove from such a list) get the same flag.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 7a   | medium | sonnet | none      | Audit and back brief. Grep every production caller of `network.networkinterfaces`, `instance.interfaces`, `add_networkinterface`, `remove_networkinterface`, `interfaces_append`, `networkinterfaces_initialized`. Classify each as reader, writer, or mutator-helper. Confirm `networkinterfaces_initialized` has zero read sites. Check whether `_delete_network(n, n.networkinterfaces)` and `wait_interfaces=n.networkinterfaces` helpers take list-of-strings or can be adapted. Report findings. No edits. |
| 7b   | medium | sonnet | none      | Extend `ObjectFilterCriteria` (pydantic + proto + regenerated stubs) with `network_uuid: Optional[str]` and `instance_uuid: Optional[str]` optional fields. Update `_direct_find_network_interfaces` to honour them. Update `_mariadb_find_network_interfaces` in `mock_etcd.py` similarly. Extend `DirectFindNetworkInterfacesTestCase` with `test_network_uuid_filter` and `test_instance_uuid_filter`. Run `tox -e py3 -- shakenfist.tests.test_mariadb_find` to confirm. One commit. |
| 7c   | medium | sonnet | none      | Network side. Rewrite `Network.networkinterfaces` as a query-backed property returning `list[NetworkInterface]`. Remove `add_networkinterface`, `remove_networkinterface`, references to `networkinterfaces_initialized` from the getter/setter paths. Migrate all Network-side callers identified in 7a. This includes `network/interface.py:195` and `:299` (which called `add/remove_networkinterface`), `network/network.py:861` (delete loop), and the external_api / daemons paths. Adapt helpers that previously took a list of UUIDs. Be vigilant about the circular-import network<->interface (use a late import inside the property; document). One commit. |
| 7d   | medium | sonnet | none      | Instance side. Rewrite `Instance.interfaces` as a query-backed property returning `list[NetworkInterface]`. Remove the setter, `interfaces_append`, and all writer call sites. Readers migrate from UUIDs to objects. One commit. |
| 7e   | medium | sonnet | none      | Schema cleanup. Drop `networkinterfaces` and `networkinterfaces_initialized` fields from `schema/network_attributes.py:NetworkAttributesData` and `interfaces` from `schema/instance_attributes.py:InstanceAttributesData`. Add idempotent v-bump migrations to `_ensure_network_attributes_schema` and `_ensure_instance_attributes_schema` in `mariadb.py` that drop the columns (CREATE-INDEX-IF-NOT-EXISTS-style pattern with try/except). Bump the VERSION constants. One commit. |
| 7f   | low    | haiku  | none      | Add a wave-2a code-quality bullet to `PUSH-AUDIT.md` directing the reviewer to flag new `list[str]` / `list[UUID4]` fields on `shakenfist/schema/*_attributes.py` as a potential cached FK list — prompt the reviewer to ask "is this a list of child-object UUIDs that a `WHERE <fk> = ?` query could provide live?" — and to apply the same flag to any new `add_*` / `remove_*` mutator pair on an attributes object. Place the bullet adjacent to the phase-6 "SQL pushdown (blocking)" bullet. Wording should match the tone and structure of the adjacent bullets. One commit. |
| 7g   | low    | haiku  | none      | Run `pre-commit run --all-files`. Fix anything flagged (most likely a stale import after removing add/remove mutators). Mark phase 7 complete in `docs/plans/index.md`. Commit as needed. |

## Back brief

Before executing any step, the sub-agent must back brief
with:

* Files it intends to change.
* For step 7a: the full caller inventory table (reader /
  writer / mutator-helper; file:line; pattern used; does
  the migration break or just simplify).
* For step 7c: confirmation that the circular-import risk
  between `network/network.py` and `network/interface.py`
  can be handled with a late import inside the property
  body (the two modules already import each other at
  module scope — verify and document).
* For step 7e: confirmation that the existing
  `_ensure_network_attributes_schema` / `_ensure_instance_attributes_schema`
  functions have a recognisable version-bump pattern (they
  should — the phase-1 `idx_<table>_name` migration used
  the same shape).
* Any design decision not explicit in this plan.

## Management session review checklist

After each step:

- [ ] Files changed match the brief. No unrelated edits.
- [ ] `pre-commit run --all-files` passes (flake8, stestr,
      mypy).
- [ ] For 7b: proto stubs regenerated with `tox -e genprotos`.
- [ ] For 7c, 7d: grep confirms no production caller still
      uses the string-UUID semantic or the removed mutators
      (legacy tests excluded).
- [ ] Commit message references this phase plan with the
      Co-Authored-By line including model / context /
      effort.

## Success criteria for phase 7

* `Network.networkinterfaces` and `Instance.interfaces` are
  query-backed properties returning hydrated
  `NetworkInterface` objects.
* `add_networkinterface`, `remove_networkinterface`, the
  `interfaces` setter, `interfaces_append`, and the
  `networkinterfaces_initialized` flag are removed from
  production code.
* The `networkinterfaces`, `networkinterfaces_initialized`
  columns on `network_attributes` and the `interfaces`
  column on `instance_attributes` are dropped from the
  pydantic schemas, and an idempotent
  ALTER-TABLE-DROP-COLUMN migration runs on daemon start.
* `ObjectFilterCriteria` has two new optional fields
  (`network_uuid`, `instance_uuid`) with matching proto
  entries.
* All ~14 production call sites migrated; reader loops
  iterate objects, writer call sites deleted.
* `pre-commit run --all-files` passes; `test_mariadb_find`
  gains at least two new tests for the FK filters.
* `PUSH-AUDIT.md` wave-2a brief has a new bullet
  directing the reviewer to flag new attribute-list-of-FK
  patterns, so a future contributor does not re-introduce
  a denormalised cache that phase 7 just removed.

## Open questions for this phase

1. **Circular import risk between `network.py` and
   `interface.py`.** Both currently import each other at
   module scope. Adding a call from `Network.networkinterfaces`
   property body to `NetworkInterface` is fine (the
   instance-level call is evaluated lazily) but any new
   module-level import from `network.py` into
   `interface.py` (or vice-versa) would deadlock at
   startup. Late-import-inside-the-property is the safe
   move. Step 7c's back brief confirms the cycle is
   manageable.

2. **Empty list performance on hot paths.** `n.networkinterfaces`
   is called in tight loops (dnsmasq lease enumeration,
   network teardown). Today it's O(1) memory read; post-
   phase it's O(k) SQL query where k is the interface
   count for that network. With `network_uuid` indexed the
   query is index-only, but still a round-trip. For
   deployments with many small networks this is probably
   a wash; for deployments with few large networks it's
   an improvement (no N+1 hydration later). No action
   required in this phase — if a hot path shows up in
   profiling, local memoisation inside the Network
   instance is cheap to add. Flag this in the
   Future-work log if the plan discovers a caller that
   repeatedly reads the property inside a loop.

3. **Backwards compat for on-disk data.** The columns are
   cache — dropping them loses nothing. No data-migration
   safety net needed. Confirmed in Design.
