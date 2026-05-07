# Phase 4: Drop the dead `object_metadata.last_cluster_operation_json` column

This is phase 4 of `PLAN-replace-last-cluster-operation.md`.
Phases 1–3 already landed: the new history-aware query
exists, `Network.is_okay()` uses it, target rows are
written automatically by `enqueue_cluster_operation`, every
explicit `set_last_cluster_operation` caller has been
swept, and the setter has been renamed
`_set_last_cluster_operation`.

## Goal

Fully sever the dead `last_cluster_operation_json` column
on `object_metadata`. Master plan decision 6 already
ruled "drop the column now, this branch has not been
deployed and there is no rollback path that needs it".

After phase 4 lands:
- The SQLAlchemy column definition is gone.
- The proto reply field is reserved (so the field number
  cannot be reused by accident).
- All `_direct_*` / `_grpc_*` / handler code paths that
  read or write the column are gone.
- The `last_cluster_operation` field on the
  `ObjectMetadataData` Pydantic model is gone.
- A schema migration drops the column from any database
  that already has it (the `_ensure_object_metadata_schema`
  function gains a v2→v3 step).
- Doc comments that describe object_metadata as "stores
  metadata and last_cluster_operation" are updated.

The `DatabaseBackedObjectWithOperations.last_cluster_operation`
property continues to exist — it reads from
`cluster_operation_targets` via
`get_latest_cluster_operation_target` and is consumed by
`runs_after=[...]` chains and `external_view()` projections
(master plan decision 1). Phase 4 does not touch the
property; it only removes the dead JSON column path.

## Audit findings

The following references were enumerated. Group A is
required work; Group B is doc-comment cleanup; Group C is
deliberately left alone.

**Group A — code paths to remove:**
- `shakenfist/mariadb.py:566` — column definition in
  `_get_object_metadata_table`.
- `shakenfist/mariadb.py:5070-5078` — direct read in
  `_direct_get_object_metadata`.
- `shakenfist/mariadb.py:5101-5106` — direct upsert
  passes `last_cluster_operation_json=None`.
- `shakenfist/mariadb.py:5167-5176` — gRPC read in
  `_grpc_get_object_metadata`.
- `shakenfist/daemons/database/main.py:4055-4058` —
  daemon handler populates the reply field.
- `shakenfist/schema/object_metadata.py:63` —
  `last_cluster_operation` field on
  `ObjectMetadataData` Pydantic model.
- `protos/database.proto:1683` — the
  `last_cluster_operation_json` field on
  `GetObjectMetadataReply`.
- `shakenfist/tests/mock_etcd.py:2237-2239` —
  constructs `ObjectMetadataData` with
  `last_cluster_operation=data.get('last_cluster_operation')`.
  Once the field is removed from the Pydantic model
  Pydantic will reject the kwarg, so this must be
  removed in the same change.

**Group B — doc comments to update:**
- `shakenfist/mariadb.py:553` — table docstring "stores
  metadata and last_cluster_operation".
- `shakenfist/mariadb.py:5047` — section header comment.
- `shakenfist/mariadb.py:5237, 5275` — function
  docstrings.
- `shakenfist/schema/object_metadata.py:5, 15, 18, 45` —
  module docstring and field docstring.
- `shakenfist/schema/instance_attributes.py:29` — note
  comment.
- `protos/database.proto:254` — section header comment.

**Group C — deliberately untouched:**
- `shakenfist/baseobject.py:673,688,698,723` —
  `last_cluster_operation` property and friends.
- `shakenfist/instance.py:425, 519, 1772, 1799, 1895` —
  property reads (external_view, enqueue_delete tree
  walk, runs_after).
- `shakenfist/artifact.py:378`,
  `shakenfist/network/network.py:313, 331`,
  `shakenfist/external_api/*` — same flavour, all
  property reads.
- `shakenfist/tests/test_external_api.py:73, 79-80` —
  test fixture mock object with `last_cluster_operation`
  attribute and a `_set_last_cluster_operation` shim.
  These mock the *property contract*, not the JSON
  column. Leave them alone.
- `shakenfist/tests/test_net.py:215, 222` — race-fix
  regression test that mocks
  `last_cluster_operation` to None to verify
  history-aware gating. Leave alone.
- `shakenfist/tests/mock_etcd.py:2987` — reads
  `network.last_cluster_operation` (the property). Leave
  alone.
- `shakenfist/deploy/shakenfist_ci/base.py:399` — reads
  the field from an `external_view()` dict. The property
  remains projected into external_view per master plan
  decision 1.
- `shakenfist/mariadb.py:3995-4078`
  (`_migrate_etcd_object_metadata`) — the etcd→MariaDB
  data migration. It already does NOT migrate
  `last_cluster_operation` into the JSON column (lines
  4047-4054 explicitly say "last_cluster_operation is
  now in cluster_operation_targets"). It only deletes
  the legacy etcd key. The function as a whole stays;
  internal references to `last_cluster_operation` are
  about the etcd key being cleaned up and remain
  accurate.
- `shakenfist/schema/cluster_operation_target.py:8` —
  comment that says "replaces the single-pointer
  last_cluster_operation column in object_metadata"
  describes history. Leave alone.

## Schema migration mechanism

The repo's table-versioning pattern lives in
`_ensure_*_schema` functions — `_get_table_version` /
`_set_table_version` track the version per table.
`OBJECT_METADATA_VERSION` is currently 2
(`mariadb.py:165`), but the existing
`_ensure_object_metadata_schema` (line 573) only handles
the create-at-v1 path; the v2 number is a target the
data migration (`_migrate_etcd_object_metadata`)
implicitly meets without a DDL change.

Phase 4 bumps `OBJECT_METADATA_VERSION` to 3 and adds a
v2→v3 step that drops the column. Use raw SQL via
`engine.connect()` — SQLAlchemy's `Table.drop_column` is
not stable across versions, and a one-line `ALTER TABLE`
is clearer than introspection-based DDL. Make it
idempotent so re-runs are safe:

```python
def _ensure_object_metadata_schema(engine: sa.Engine) -> dict[str, Any]:
    """Ensure the object_metadata table schema is up to date."""
    table_name = 'object_metadata'
    current_ver = _get_table_version(engine, table_name)
    start_ver = current_ver
    table = _get_object_metadata_table()

    if current_ver <= 0:
        LOG.info(f'Creating {table_name} table (version 1)')
        table.metadata.create_all(engine, tables=[table], checkfirst=True)
        current_ver = 1
        _set_table_version(engine, table_name, current_ver)

    if current_ver < 3:
        LOG.info(
            f'Upgrading {table_name} from v{current_ver} to v3: '
            'dropping dead last_cluster_operation_json column.')
        with engine.connect() as conn:
            conn.execute(sa.text(
                'ALTER TABLE object_metadata '
                'DROP COLUMN IF EXISTS last_cluster_operation_json'
            ))
            conn.commit()
        current_ver = 3
        _set_table_version(engine, table_name, current_ver)

    return {
        'table': table_name,
        'start_version': start_ver,
        'end_version': current_ver,
        'target_version': OBJECT_METADATA_VERSION,
        'migrated': start_ver != current_ver
    }
```

`DROP COLUMN IF EXISTS` is supported on MariaDB 10.0+ and
makes the migration safe to re-run, including on a fresh
database where the column never existed (the SQLAlchemy
table definition no longer includes it, so
`create_all` wouldn't have created it either — but the
explicit `IF EXISTS` makes the intent clear).

The version skip from 2→3 (no v2 step ever existed) is
deliberate. v2 is the target reached implicitly by the
etcd data migration; v3 is the first version that
requires DDL. Skipping a number is cheaper than
backfilling a fictional v2 step.

## Detailed work

### 1. Update the SQLAlchemy table definition

In `shakenfist/mariadb.py:550-570`, remove the
`last_cluster_operation_json` column from the
`_get_object_metadata_table()` definition. Update the
docstring on line 553 to no longer claim the table stores
`last_cluster_operation`.

### 2. Bump version and add the v2→v3 migration

In `shakenfist/mariadb.py`:
- Bump `OBJECT_METADATA_VERSION` from 2 to 3 (line 165).
- Add the v2→v3 step in
  `_ensure_object_metadata_schema()` per the snippet
  above.

### 3. Remove the read/write code paths

In `shakenfist/mariadb.py`:
- `_direct_get_object_metadata` (line 5050): drop the
  `lco = ...` lines and the
  `last_cluster_operation=lco` kwarg on the
  `ObjectMetadataData` constructor.
- `_direct_set_metadata` (line 5085): remove
  `last_cluster_operation_json=None` from the insert
  values dict (it is now a column that doesn't exist).
- `_grpc_get_object_metadata` (line 5151): drop the
  `lco = ...` lines and the constructor kwarg.

### 4. Update the proto

In `protos/database.proto`:
- Replace the line `string last_cluster_operation_json = 3;`
  with `reserved 3;` (and on the next line,
  `reserved "last_cluster_operation_json";`). This
  prevents the field number from being reused.
- Update the section comment at line 254 to drop the
  "and last_cluster_operation" wording.
- Run `tox -e genprotos`. Confirm regenerated
  `database_pb2.py`, `database_pb2.pyi`, and the gRPC
  stubs are committed.

### 5. Update the daemon handler

In `shakenfist/daemons/database/main.py:4030-4066`
(`GetObjectMetadata`), remove the
`last_cluster_operation_json=...` kwarg from the
`GetObjectMetadataReply` constructor.

### 6. Update the Pydantic schema

In `shakenfist/schema/object_metadata.py`:
- Remove the `last_cluster_operation` field (line 63).
- Update the module docstring (lines 5, 15, 18) and the
  attribute docstring (lines 41-47) to no longer claim
  the model carries `last_cluster_operation`.

### 7. Update the test mock

In `shakenfist/tests/mock_etcd.py:2237-2239`, drop the
`last_cluster_operation=data.get('last_cluster_operation')`
kwarg from the `ObjectMetadataData` constructor.

### 8. Update doc comments (Group B)

Update each file in Group B to remove the
"…and last_cluster_operation" wording. These are pure
text edits with no behaviour impact.

### 9. Tests

- Confirm `shakenfist/tests/test_object_metadata.py`
  still passes with the new `ObjectMetadataData` shape.
  The test file does not appear to assert anything about
  `last_cluster_operation` directly (an audit grep
  showed only `metadata`-related assertions), but verify
  by re-reading.
- `shakenfist/tests/test_database.py` — re-read for any
  assertion on the `last_cluster_operation_json` proto
  field. If any test directly inspects the reply for
  this field, update.
- Add a small test that exercises the v2→v3 migration
  path: spin up an in-memory engine, create an
  `object_metadata` table with the v2 column shape,
  bump the version row to 2, call
  `_ensure_object_metadata_schema`, assert the column
  is gone and the version row is now 3. Idempotency:
  call again and assert no error. (Skip this test if
  the existing migration tests don't use real engines —
  follow the existing testing style rather than
  introducing a new pattern.)

### 10. Lint and full test run

```bash
tox -e genprotos      # because the proto changed
pre-commit run --all-files
tox
```

Confirm `pre-commit run --all-files` passes (flake8,
unit tests, mypy) and `tox` passes the full unit suite.

## Files expected to change

- `protos/database.proto` — `reserved 3` plus comment
  cleanup.
- `shakenfist/protos/database_pb2.py` — regenerated.
- `shakenfist/protos/database_pb2.pyi` — regenerated.
- `shakenfist/protos/database_pb2_grpc.py` — regenerated.
- `shakenfist/protos/database_pb2_grpc.pyi` — regenerated.
- `shakenfist/mariadb.py` — column removal, version bump,
  v2→v3 migration, two read paths cleaned up, one write
  path cleaned up, doc comment updates.
- `shakenfist/daemons/database/main.py` — handler reply
  field removed.
- `shakenfist/schema/object_metadata.py` — Pydantic
  field and docstrings updated.
- `shakenfist/schema/instance_attributes.py` — note
  comment updated.
- `shakenfist/tests/mock_etcd.py` — constructor kwarg
  removed.
- Possibly `shakenfist/tests/test_object_metadata.py` —
  if an assertion needs updating.
- Possibly `shakenfist/tests/test_database.py` — if a
  proto-shape test needs updating.

No other files should change. In particular,
`baseobject.py`, `instance.py`, `artifact.py`,
`network/network.py`, the external API modules, and the
`external_view()` outputs are all untouched —
`last_cluster_operation` continues to be a property
backed by `cluster_operation_targets`.

## Commit shape

One commit, message along the lines of:

```
Drop dead last_cluster_operation_json column.

Phase 4 of the LCO replacement plan completes the
storage transition: the column is removed from the
SQLAlchemy schema, the proto reply field is reserved,
the read/write code paths in mariadb.py and the
database daemon handler are deleted, the
last_cluster_operation field is removed from
ObjectMetadataData, and an idempotent v2->v3 migration
drops the column from any database that already had it.

The DatabaseBackedObjectWithOperations.last_cluster_operation
property is unchanged -- it has read from
cluster_operation_targets since phase 1 -- and continues
to be consumed by external_view() projections and
runs_after=[...] chains per master plan decision 1.
```

Plus standard `Prompt:`, `Signed-off-by`, and
`Co-Authored-By` lines.

## Acceptance criteria

- `tox -e genprotos` produces no diff when re-run after
  the commit.
- `pre-commit run --all-files` passes.
- `tox` passes.
- The grep
  `grep -rn 'last_cluster_operation_json' shakenfist/ protos/ --include='*.py' --include='*.proto'`
  matches only:
  - the `reserved` line in `protos/database.proto`;
  - the v2→v3 migration text in
    `_ensure_object_metadata_schema`.
  - any binary descriptor strings inside
    `shakenfist/protos/database_pb2.py` (these are part
    of the `reserved` declaration's serialised form and
    are expected).
- Re-applying the migration is a no-op (`DROP COLUMN IF
  EXISTS` returns success on a column that no longer
  exists).
- `external_view()` outputs for Instance, Artifact, and
  Network still include `last_cluster_operation` as
  before — verified by a unit-test-level run, not just
  by inspection.

## Out of scope

- Bucket D follow-up (list-based pending-ops query for
  `Instance.enqueue_delete` and
  `baseobject.get_cluster_operations`) — tracked in
  master plan *Future work*.
- Documentation updates (phase 5).
- Any change to the
  `DatabaseBackedObjectWithOperations.last_cluster_operation`
  property or its consumers.
- Removing `OBJECT_METADATA_VERSION` plumbing or the
  per-table versioning pattern itself — phase 4 just
  uses the existing mechanism.

## Agent guidance

Phase 4 is a sonnet-friendly mechanical change with a
proto regeneration and one schema migration. The brief
should include:
- The exact list of `mariadb.py` line ranges to delete
  or change.
- The proto `reserved` syntax (the agent must use
  `reserved 3; reserved "last_cluster_operation_json";`
  rather than just deleting the line, to prevent field
  number reuse).
- A reminder to run `tox -e genprotos` and commit the
  regenerated stubs.

The management session reviews:
- The migration is idempotent (re-runs are no-ops).
- No file outside the *Files expected to change* list
  was modified.
- Regenerated proto stubs only show the
  `reserved 3`-related diff, not unrelated proto changes.
- `external_view()` output for Instance/Artifact/Network
  still includes `last_cluster_operation` — verifiable
  by reading the relevant `external_view` methods and by
  the existing unit tests passing unchanged.
