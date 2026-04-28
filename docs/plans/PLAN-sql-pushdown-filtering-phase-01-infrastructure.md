# Phase 1 — Query infrastructure

Master plan: [PLAN-sql-pushdown-filtering.md](PLAN-sql-pushdown-filtering.md).

Planning effort: **high** (opus). This phase establishes the
shared primitive used by every later phase; the API shape
and index decisions here lock in what phases 2–5 build.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly, with
particular attention to the MariaDB three-layer pattern
(`shakenfist/mariadb.py`), the Pydantic schemas in
`shakenfist/schema/`, the existing gRPC proto definitions in
`protos/database.proto`, and the database daemon monitor at
`shakenfist/daemons/database/main.py`. Ground any claim in
what the code does today rather than speculating. Flag
uncertainty explicitly.

## Goal

Land a single, well-tested primitive that every later phase
uses to run "find objects of type T whose state is in S, in
namespace N, with name M" as one parameterised SQL query —
with the same three-layer direct/gRPC/public routing as
every other MariaDB accessor.

Non-goals for this phase:

* Changing any caller. `Artifact.filter()`,
  `Instance.filter()`, `Network.filter()`, the iterators, and
  the ad-hoc bulk scans all remain untouched in this phase.
  Wiring them up is phase 2 onwards. Phase 1 is only about
  building the primitive and its tests.
* Attribute-column pushdown (lazy-loaded `*_attributes` join).
  Deferred per master plan.

## Design

### Criteria representation

Add a new Pydantic model
`shakenfist/schema/object_filter.py:ObjectFilterCriteria`:

```python
class ObjectFilterCriteria(BaseModel):
    states: Optional[list[str]] = None   # None = no state filter
    namespace: Optional[str] = None      # None = no namespace filter
    name: Optional[str] = None           # None = no name filter
```

Rationale:

* `states=None` vs `states=[]`: we use `None` for "skip this
  filter" so that `[]` (no matches) can be expressed
  distinctly if a caller ever needs it. The MariaDB helper
  treats an empty list the same as `None` but we keep the
  semantic split at the API.
* `namespace=None` vs `namespace=''`: `None` = skip, empty
  string = exact match on empty (never legal, but the query
  will return zero rows — as expected).
* `name=None` vs `name=''`: same reasoning.

No other fields in this phase. Adding `url`, `type`, or
`name_like` is a phase-6 decision once we know which phase-2+
call sites want them.

### Proto surface

Add to `protos/database.proto`:

```
message ObjectFilterCriteria {
  repeated string states = 1;       // empty = don't filter
  optional string namespace = 2;    // absent = don't filter
  optional string name = 3;         // absent = don't filter
}

message FindArtifactsRequest  { ObjectFilterCriteria criteria = 1; }
message FindArtifactsReply    { repeated ArtifactStaticData artifacts = 1; }

message FindInstancesRequest  { ObjectFilterCriteria criteria = 1; }
message FindInstancesReply    { repeated InstanceStaticData instances = 1; }

message FindNetworksRequest   { ObjectFilterCriteria criteria = 1; }
message FindNetworksReply     { repeated NetworkStaticData networks = 1; }
```

And three RPCs on the `DatabaseService` service:

```
rpc FindArtifacts (FindArtifactsRequest) returns (FindArtifactsReply) {}
rpc FindInstances (FindInstancesRequest) returns (FindInstancesReply) {}
rpc FindNetworks  (FindNetworksRequest)  returns (FindNetworksReply)  {}
```

Rationale:

* The Pydantic model uses `states` (plural), the proto uses
  `states` (plural, repeated). Direct mapping.
* `repeated string states = 1;` with length zero means "don't
  filter on state". This matches how
  `GetObjectsByStateRequest.state_values` works today.
* Three RPCs instead of one generic `FindObjects` because the
  reply shape is per-type (`ArtifactStaticData` vs
  `InstanceStaticData` etc.). Consistent with the existing
  `GetArtifact`/`GetInstance`/`GetNetwork` split.

Regenerate stubs with `tox -e genprotos`; commit regenerated
files alongside the `.proto` edit.

### Direct MariaDB helper

In `shakenfist/mariadb.py`, add one private helper and three
thin per-type wrappers.

Shared builder:

```python
def _build_object_filter_query(
        table: sa.Table,
        object_type: ObjectType,
        criteria: ObjectFilterCriteria) -> sa.Select:
    """SELECT <table>.* FROM <table>
       JOIN object_states s
         ON s.object_uuid = <table>.uuid
        AND s.object_type = <object_type>
       WHERE <criteria applied>."""
    states = _get_object_states_table()
    stmt = sa.select(table).join(
        states,
        sa.and_(
            states.c.object_uuid == table.c.uuid,
            states.c.object_type == object_type))
    if criteria.states:
        stmt = stmt.where(states.c.state_value.in_(criteria.states))
    if criteria.namespace is not None:
        stmt = stmt.where(table.c.namespace == criteria.namespace)
    if criteria.name is not None:
        stmt = stmt.where(table.c.name == criteria.name)
    return stmt
```

Per-type direct helpers hydrate rows into the right Pydantic
model:

```python
def _direct_find_artifacts(
        criteria: ObjectFilterCriteria) -> list[ArtifactData]:
    engine = _get_engine()
    table = _get_artifacts_table()
    stmt = _build_object_filter_query(table, ObjectType.ARTIFACT, criteria)
    try:
        with engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()
            return [ArtifactData(**row._mapping) for row in rows]
    except OperationalError as e:
        LOG.warning(f'MariaDB find failed for artifacts: {e}')
        return []

# Analogous _direct_find_instances, _direct_find_networks.
```

Instance hydration uses the existing `_static_values_to_dict`
shape (dict, not Pydantic) because Instance's constructor
takes a dict today. Phase 3 may revisit. For phase 1 the
direct helper returns `list[dict[str, Any]]` for instance and
network, matching their existing `_direct_get_all_*`
contracts.

### gRPC handlers

In `shakenfist/daemons/database/main.py`, add three handler
methods on `DatabaseServicer` mirroring the existing
`GetAllArtifacts` / `GetAllInstances` / `GetAllNetworks`
handlers. Each increments its counter, builds an
`ObjectFilterCriteria` from the proto request, calls the
matching `_direct_find_*` helper, and serialises the reply.
Counters to register in `Monitor.__init__`:

```
'find_artifacts', 'find_instances', 'find_networks',
```

### Public wrappers

In `shakenfist/mariadb.py`:

```python
def find_artifacts(
        criteria: ObjectFilterCriteria) -> list[ArtifactData]:
    if _use_database_service():
        return _grpc_find_artifacts(criteria)
    return _direct_find_artifacts(criteria)

# Analogous find_instances, find_networks.
```

### Indexes

The `object_states` table already has
`idx_object_states_type_state` on
`(object_type, state_value)`, which is what the state
filter wants. The per-type tables already have a namespace
index (artifacts, instances) and Network has one too.

We **add** a name index on each of the three per-type
tables:

```python
# In schema/artifact_data.py, schema/instance_data.py,
# schema/network_data.py — annotate `name`:
name: Annotated[str, SQLIndex()]
```

Rationale: the name-lookup path from `from_db_by_ref` joins
on object_states (small set after state filter) and then
equals-compares on name. With state filter alone, a name
match on a non-indexed column still scans up to ~hundreds of
rows per namespace. Adding the index makes name lookups
truly O(1) and costs almost nothing at write time for these
tables.

`pydantic_to_sqlalchemy_table` consumes `SQLIndex`
annotations when a table is created fresh, but
`MetaData.create_all(checkfirst=True)` does **not** add a
new index to a pre-existing table. Existing production
deployments therefore need an explicit idempotent
`CREATE INDEX IF NOT EXISTS idx_<tbl>_name ON <tbl>(name)`
statement in the corresponding `_ensure_*_schema` function
(for artifacts, instances, networks). Phase 1 adds these
three statements. They are guarded by
`CREATE INDEX IF NOT EXISTS` on MariaDB 10.5+; on older
engines, wrap in a lookup against
`information_schema.statistics`. The ensure functions
already run on every daemon startup, so no separate
migration step is needed — existing clusters pick up the
index the next time the database daemon restarts.

### Error handling

`OperationalError` / `IntegrityError` returns `[]` with a
`LOG.warning`. Unlike the existing `_direct_get_all_*`
helpers, the warning line **always includes the full
criteria** (`states`, `namespace`, `name`) so a failing
query can be reproduced from the log without replay. None
of those fields are PII for this codebase — namespaces and
names are admin-visible identifiers, not tenant data. The
gRPC handler wraps unexpected exceptions in
`util_exceptions.ignore_exception` and returns an empty
reply, again matching existing pattern. `ignore_exception`
should also receive the criteria in its context dict for
the same reason.

No new counter for errors — the Counter only tracks call
count, consistent with the rest of the daemon.

## Steps

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | low    | haiku  | none      | Add `ObjectFilterCriteria` Pydantic model at `shakenfist/schema/object_filter.py` per design above. Export from the package if the existing schemas do so. Add a short docstring. No other files. |
| 1b   | medium | sonnet | none      | Annotate the `name` field on `shakenfist/schema/artifact_data.py`, `shakenfist/schema/instance_data.py` and `shakenfist/schema/network_data.py` with `SQLIndex()`. Then add an idempotent `CREATE INDEX IF NOT EXISTS idx_<table>_name ON <table>(name)` statement to each of `_ensure_artifacts_schema`, `_ensure_instances_schema`, `_ensure_networks_schema` in `shakenfist/mariadb.py` so pre-existing production tables pick up the index on daemon restart. On MariaDB versions where `IF NOT EXISTS` is not supported on `CREATE INDEX`, guard with a `information_schema.statistics` lookup. Verify idempotence with a unit test that runs the ensure function twice. |
| 1c   | medium | sonnet | none      | Add the proto messages and three RPCs to `protos/database.proto` per design above. Run `tox -e genprotos` and commit regenerated stubs (`shakenfist/protos/database_pb2*.py`). Do not touch any Python consumer yet. |
| 1d   | high   | opus   | worktree  | Add `_build_object_filter_query`, `_direct_find_artifacts`, `_direct_find_instances`, `_direct_find_networks` to `shakenfist/mariadb.py`. Model on the existing `_direct_get_all_*` trio at lines 11156, 11515, 11811 (see master plan for exact locations — re-read before implementing). Keep error handling consistent with the existing direct helpers. Isolate in a worktree because this touches `mariadb.py` broadly. |
| 1e   | medium | sonnet | none      | Add `FindArtifacts`, `FindInstances`, `FindNetworks` handlers in `shakenfist/daemons/database/main.py`. Mirror the existing `GetAllArtifacts` / `GetAllInstances` / `GetAllNetworks` handler shape. Register the three new counter keys in `Monitor.__init__`. Confirm the gRPC channel `max_receive_message_length` / `max_send_message_length` values on both client and server (search for `grpc.max_` options near the existing `DatabaseStub` setup and in `main.py` where the server is started) are large enough for a find-all response; the payload shape is identical to `GetAllArtifacts` etc., so existing limits should suffice, but raise them here if not. Note whatever you found in the commit message so we have a record. |
| 1f   | medium | sonnet | none      | Add `_grpc_find_artifacts`, `_grpc_find_instances`, `_grpc_find_networks` gRPC wrappers plus the three public `find_*` routers in `shakenfist/mariadb.py`. Model on the existing `_grpc_get_all_*` wrappers. |
| 1g   | medium | sonnet | none      | Unit tests in `shakenfist/tests/test_mariadb_find.py` covering: all-filters-present, each filter alone, no filters (should match all active), no match, mismatched namespace, `states=[]` vs `states=None`, empty-table, `OperationalError` returning `[]`. Mock `_get_engine` and the table; do not hit a real DB. Use existing mariadb tests as a reference for fixture style. |
| 1h   | low    | haiku  | none      | Run `pre-commit run --all-files` and `tox` at the root of the worktree. Fix anything they report. |

Each step should land as its own commit. Step 1d may land as
a separate commit from the wrappers (1f) even though both
touch `mariadb.py` — keeps the diff small and reviewable.
Steps 1b and 1c can land together with a note that the name
index only starts being queried when phase 2 arrives.

## Open questions for this phase

1. **Message-size tuning.** The payload shape is identical
   to `GetAllArtifacts` / `GetAllInstances` /
   `GetAllNetworks`, which already works in production, so
   the existing `max_receive_message_length` /
   `max_send_message_length` values should suffice. Step 1e
   confirms this by reading the gRPC channel setup (client
   side near `DatabaseStub`, server side in
   `database/main.py` where the server is started) and
   raising the limits if the current value is less than
   what a find-all response could be. Whatever is found is
   recorded in the commit message.

Resolved during planning:

* **Name indexes on existing tables.** Step 1b adds an
  idempotent `CREATE INDEX IF NOT EXISTS` (falling back to
  `information_schema.statistics` on older MariaDB) inside
  each `_ensure_*_schema`, so existing clusters pick up the
  index on daemon restart.
* **Error logging.** `_direct_find_*` always logs the full
  criteria (`states`, `namespace`, `name`) on failure —
  namespaces and names are admin-visible identifiers in
  this codebase, not tenant PII, and having them in the
  log makes a failed query reproducible without replay.

The phase 1 sub-agents should surface any additional
questions in the back brief before beginning implementation.

## Back brief

Before executing any step, the sub-agent briefed on that
step must back brief the operator with:

* The specific files and lines it intends to change.
* Any design decision it has made that is not explicit in
  this phase plan.
* Confirmation that it has re-read the referenced patterns
  in `mariadb.py` / `database/main.py` / the existing
  schemas, and that the changes it plans are consistent
  with them.

## Management session review checklist

After each step, the management session verifies:

- [ ] Files that were supposed to change actually changed.
- [ ] No unrelated files modified.
- [ ] `pre-commit run --all-files` passes.
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed in the same commit.
- [ ] New counter keys appear in the Monitor registration
      list.
- [ ] Unit tests in `test_mariadb_find.py` cover at least
      the eight scenarios listed for step 1g.
- [ ] Commit message follows project conventions including
      the Co-Authored-By line with model / context window /
      effort level.

## Success criteria for phase 1

* `ObjectFilterCriteria` lands with tests.
* `find_artifacts`, `find_instances`, `find_networks` public
  wrappers exist and route direct vs gRPC correctly.
* Counter registration list in `database/main.py` includes
  the three new keys.
* Proto stubs regenerated.
* Name indexes present on `artifacts`, `instances`,
  `networks` tables (verified either via `create_all` or
  via an idempotent `ALTER TABLE` in ensure-schema).
* No caller changed in this phase. `git grep find_artifacts
  find_instances find_networks` shows the new symbols only
  in `mariadb.py`, `daemons/database/main.py`, `protos/`,
  `tests/`, and this plan.
