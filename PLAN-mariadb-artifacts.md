# Plan: Migrate Artifact Objects from etcd to MariaDB

**Status: COMPLETE**

## Overview

Migrate the Artifact object's static values and mutable attributes from etcd
to MariaDB, following the established patterns from the Blob migration. This
includes the artifact's version indexes, which are currently stored as
individual etcd attributes (`index_000000000001`, etc.) and should be migrated
to a dedicated `artifact_indexes` table for efficient querying.

## Current State

Artifact data is currently split across etcd and MariaDB:
- **MariaDB** (already migrated): object state (`object_states` table),
  object references (`object_references` table)
- **etcd** (to be migrated): static values (`/sf/artifact/{uuid}`),
  all attributes (`/sf/attribute/artifact/{uuid}/*`)

### Static values (immutable, set at creation)
- `uuid` - artifact identifier
- `artifact_type` - one of: snapshot, label, image, other
- `source_url` - origin URL (sf://blob/, sf://snapshot/, sf://instance/, etc.)
- `name` - human-readable name (derived from source_url if not set)
- `namespace` - owning namespace (required)
- `version` - schema version (currently 8)

### Attributes (mutable)
- `max_versions` - max version count to retain (dict: `{'max_versions': N}`)
- `shared` - whether artifact is shared cross-namespace (dict: `{'shared': bool}`)
- `highest_index` - current highest version index (dict: `{'index': N}`)
- `index_NNNNNNNNNNNN` - per-version entries (dict: `{'index': N, 'blob_uuid': UUID}`)
- `last_cluster_operation` - operation tracking (inherited from dbowo)
- `metadata` - user-defined key-value pairs (inherited from baseobject)

### In-memory-only artifacts
Artifacts of type IMAGE with source_url starting with `sf://blob/` are not
persisted at all -- they are ephemeral in-memory convenience objects. These
must be skipped during migration and continue to work unchanged.

## Design Decisions

### Artifact indexes get their own table

The `index_NNNNNNNNNNNN` attributes are fundamentally different from scalar
attributes like `max_versions` and `shared`. They are a one-to-many
relationship (one artifact has many version indexes), and they are queried by
iteration (`get_all_indexes()`), by specific index number (`del_index(N)`),
and by finding the highest index (`most_recent_index`).

A dedicated `artifact_indexes` table with a composite primary key
`(artifact_uuid, index_number)` allows:
- Efficient range queries and ordering via SQL
- Direct lookup by `(artifact_uuid, index_number)` without prefix scanning
- Atomic insert/delete of individual index entries
- Database-level enforcement of uniqueness per (artifact, index) pair

### Scalar attributes go in an attributes table

`max_versions`, `shared`, and `highest_index` are simple scalar values that
change during the artifact's lifetime. These go in an `artifact_attributes`
table following the blob_attributes pattern.

### `last_cluster_operation` and `metadata` stay in etcd for now

These attributes are inherited from `DatabaseBackedObjectWithOperations` and
`DatabaseBackedObject` respectively. They use the generic `_db_get_attribute`/
`_db_set_attribute` pattern shared by all object types. Migrating them would
require changes to the base class infrastructure that affect all object types.
They should be migrated as part of a broader effort later.

### Namespace and source_url need indexes

`source_url` is used by `Artifact.from_url()` which currently iterates all
artifacts to find a match. An index on `source_url` enables pushing this
filter down to MariaDB. Similarly, `namespace` is used for filtering in
`artifacts_in_namespace()` and `namespace_or_shared_filter()`.

### The Artifacts iterator needs updating

The `Artifacts` iterator currently uses `etcd.get_all('artifact', None)` via
`DatabaseBackedObjectIterator.get_iterator()`. After migration, it should
query the `artifacts` table in MariaDB. This means adding a `get_all_artifacts`
function to mariadb.py and overriding `get_iterator()` in the Artifacts class.

## Implementation Steps

### Step 1: Create Pydantic schemas

Create three schema files:

**`shakenfist/schema/artifact_data.py`** (frozen=True):
- `uuid: Annotated[UUID4, SQLNativeUUID()]` (primary key)
- `artifact_type: Annotated[str, SQLIndex()]`
- `source_url: Annotated[str, SQLIndex()]`
- `name: str`
- `namespace: Annotated[str, SQLIndex()]`
- `version: int`

**`shakenfist/schema/artifact_attributes.py`** (frozen=False):
- `uuid: Annotated[UUID4, SQLNativeUUID()]` (primary key)
- `max_versions: int = 0`
- `shared: Annotated[bool, SQLIndex()] = False`
- `highest_index: int = 0`

**`shakenfist/schema/artifact_index.py`** (frozen=True):
- `artifact_uuid: Annotated[UUID4, SQLNativeUUID()]`
- `index_number: int`
- `blob_uuid: Annotated[UUID4, SQLNativeUUID()]`
- Composite primary key: `(artifact_uuid, index_number)`
- Note: the Pydantic model defines the fields; the composite primary key is
  defined in the SQLAlchemy table definition in mariadb.py since Pydantic
  doesn't have a concept of composite keys.

### Step 2: Define gRPC protocol messages and RPCs

Add to `protos/database.proto`:

**Messages:**
- `ArtifactStaticData` - uuid, artifact_type, source_url, name, namespace,
  version
- `CreateArtifactRequest` / `GetArtifactRequest` / `GetArtifactReply`
- `GetAllArtifactsRequest` / `GetAllArtifactsReply`
- `DeleteArtifactRequest`
- `ArtifactAttributesData` - uuid, max_versions, shared, highest_index
- `CreateArtifactAttributesRequest` / `GetArtifactAttributesRequest` /
  `GetArtifactAttributesReply`
- `UpdateArtifactAttributesRequest`
- `DeleteArtifactAttributesRequest`
- `ArtifactIndexData` - artifact_uuid, index_number, blob_uuid
- `CreateArtifactIndexRequest` / `GetArtifactIndexRequest` /
  `GetArtifactIndexReply`
- `GetAllArtifactIndexesRequest` / `GetAllArtifactIndexesReply`
- `DeleteArtifactIndexRequest`

**RPCs:**
- `CreateArtifact`, `GetArtifact`, `GetAllArtifacts`, `DeleteArtifact`
- `CreateArtifactAttributes`, `GetArtifactAttributes`,
  `UpdateArtifactAttributes`, `DeleteArtifactAttributes`
- `CreateArtifactIndex`, `GetArtifactIndex`, `GetAllArtifactIndexes`,
  `DeleteArtifactIndex`

Run `tox -e genprotos` to regenerate stubs.

### Step 3: Implement MariaDB functions

Add to `shakenfist/mariadb.py`:

**Version constants:**
- `ARTIFACTS_VERSION = 1`
- `ARTIFACT_ATTRIBUTES_VERSION = 1`
- `ARTIFACT_INDEXES_VERSION = 1`

**Table definitions:**
- `_get_artifacts_table()` - columns: uuid (PK), artifact_type, source_url,
  name, namespace, version. Indexes on artifact_type, source_url, namespace.
- `_get_artifact_attributes_table()` - columns: uuid (PK), max_versions,
  shared, highest_index. Index on shared (for namespace_or_shared_filter).
- `_get_artifact_indexes_table()` - columns: artifact_uuid, index_number,
  blob_uuid. Composite primary key (artifact_uuid, index_number). Index on
  blob_uuid (for reverse lookups).

**Schema ensure functions:**
- `_ensure_artifacts_schema(engine)`
- `_ensure_artifact_attributes_schema(engine)`
- `_ensure_artifact_indexes_schema(engine)`
- Wire into `ensure_schema()`

**Direct access functions (called by database daemon):**
- `_direct_create_artifact(uuid, artifact_type, source_url, name, namespace, version)`
- `_direct_get_artifact(uuid)` -> dict or None
- `_direct_get_all_artifacts()` -> list of dicts
- `_direct_delete_artifact(uuid)`
- `_direct_create_artifact_attributes(uuid, max_versions, shared, highest_index)`
- `_direct_get_artifact_attributes(uuid)` -> dict or None
- `_direct_update_artifact_attributes(uuid, **kwargs)` - update specific fields
- `_direct_delete_artifact_attributes(uuid)`
- `_direct_create_artifact_index(artifact_uuid, index_number, blob_uuid)`
- `_direct_get_artifact_index(artifact_uuid, index_number)` -> dict or None
- `_direct_get_all_artifact_indexes(artifact_uuid)` -> list of dicts, ordered
  by index_number
- `_direct_delete_artifact_index(artifact_uuid, index_number)`

**gRPC client functions (called by other daemons):**
- Mirror of direct functions, routing through the database gRPC service

**Public API functions:**
- Routing layer that calls direct or gRPC based on
  `_use_database_service()`, following the established pattern

**Data migration functions (v2):**
- `_migrate_etcd_artifacts(engine)` - reads all artifacts from
  `etcd.get_all('artifact', None)`, writes to `artifacts` table, deletes
  from etcd on success
- `_migrate_etcd_artifact_attributes(engine)` - for each artifact UUID in the
  artifacts table, reads `max_versions`, `shared`, `highest_index` from etcd
  attributes, writes to `artifact_attributes` table, deletes from etcd
- `_migrate_etcd_artifact_indexes(engine)` - for each artifact UUID, reads all
  `index_*` attributes from etcd, writes to `artifact_indexes` table, deletes
  from etcd
- Register all three in `DATA_MIGRATIONS` dict

### Step 4: Implement gRPC handlers

Add to `shakenfist/daemons/database/main.py`:

- `CreateArtifact()`, `GetArtifact()`, `GetAllArtifacts()`,
  `DeleteArtifact()` handlers
- `CreateArtifactAttributes()`, `GetArtifactAttributes()`,
  `UpdateArtifactAttributes()`, `DeleteArtifactAttributes()` handlers
- `CreateArtifactIndex()`, `GetArtifactIndex()`, `GetAllArtifactIndexes()`,
  `DeleteArtifactIndex()` handlers
- Add Prometheus counters for all new operations

### Step 5: Update the Artifact class

Modify `shakenfist/artifact.py`:

**Bump version:**
- `current_version = 9`
- Add `_upgrade_step_8_to_9()` as a no-op (migration handled by sf-database
  startup)

**Override `_db_create()`:**
- Call `mariadb.create_artifact()` instead of `etcd.create()`
- Emit audit event

**Override `_db_get()`:**
- Call `mariadb.get_artifact()` instead of `etcd.get()`
- Return dict compatible with current `__init__()` expectations
- Handle version mismatch

**Override `from_db()`:**
- Similar to Blob.from_db(), handle the Pydantic model vs dict difference
  if we return a Pydantic model from `_db_get()`
- OR: have `_db_get()` return a dict for now to minimize changes to
  `__init__()` (simpler approach, consistent with how `__init__` currently
  works with `static_values` dicts)

**Replace attribute access for `max_versions`, `shared`, `highest_index`:**
- Add `__attributes` and `__attributes_loaded` instance variables
- Add `_load_attributes()` and `_ensure_attributes()` helper methods
- `max_versions` getter: load from `_ensure_attributes().max_versions`
- `max_versions` setter: call `mariadb.update_artifact_attributes(uuid, max_versions=value)`
- `shared` getter/setter: same pattern
- `highest_index`: no longer a separate attribute, computed from the indexes
  table or cached in attributes

**Replace index operations:**
- `most_recent_index` property: call `mariadb.get_all_artifact_indexes(uuid)`,
  return the last one (they come back ordered by index_number)
- `get_all_indexes()`: call `mariadb.get_all_artifact_indexes(uuid)`, yield
  each as a dict with `{'index': N, 'blob_uuid': str(uuid)}`
- `add_index(blob_uuid)`: use `_ensure_attributes()` to get/update
  `highest_index`, call `mariadb.create_artifact_index()`, call
  `mariadb.update_artifact_attributes()` to bump highest_index
- `del_index(index)`: call `mariadb.get_artifact_index()` then
  `mariadb.delete_artifact_index()`

**Update `hard_delete()`:**
- Delete all artifact indexes: iterate and delete, or add a
  `delete_all_artifact_indexes(artifact_uuid)` function
- Delete artifact attributes
- Delete artifact static values
- Call `super().hard_delete()` (handles state deletion and audit event)

**Update `Artifacts` iterator:**
- Override `get_iterator()` to use `mariadb.get_all_artifacts()` instead of
  `etcd.get_all()`. When prefilter is used, join with state data. When no
  prefilter, return all artifacts from MariaDB.

### Step 6: Add migration command to sf-ctl

Add to `shakenfist/client/ctl.py`:

- `migrate-artifacts-to-mariadb` command with `--dry-run` flag
- Migrates static values, attributes, and indexes in order
- Progress reporting every 100 artifacts
- Error handling and summary

Note: this may not be needed if the automatic migration in step 3 handles
everything on database daemon startup. Check how existing migrations
(blobs, nodes, namespaces) handle this -- if they all run automatically via
`DATA_MIGRATIONS`, then a manual command is redundant. Add it only if the
existing pattern includes manual commands.

### Step 7: Update test mocks

Update `shakenfist/tests/mock_etcd.py`:
- Add mock implementations for all new mariadb artifact functions
- Use in-memory dicts to simulate the three tables

Add new test file `shakenfist/tests/test_mariadb_artifacts.py`:
- Test schema creation and migration
- Test CRUD operations for static values, attributes, and indexes
- Test the Artifact class with MariaDB backend
- Test in-memory-only artifacts still work

### Step 8: Update documentation

**`docs/operator_guide/database.md`:**
- Add artifacts, artifact_attributes, artifact_indexes to the per-type tables
  section
- Add migration command documentation if applicable
- Update the migration phases table

**`ARCHITECTURE.md`:**
- Update to reflect artifact migration status

**`GOALS.md`:**
- Update the etcd-to-mariadb item to reflect artifacts as completed

**`README.md`:**
- Update if needed

## Verification Checklist

- [ ] `tox` - all existing tests pass
- [ ] `tox -e genprotos` - proto generation succeeds
- [ ] `tox -eflake8 -- -HEAD` - no style issues
- [ ] `pre-commit run --all-files` - all hooks pass
- [ ] New unit tests pass for artifact CRUD via MariaDB
- [ ] In-memory-only artifacts (TYPE_IMAGE + BLOB_URL) still work correctly
- [ ] `Artifact.from_url()` still finds artifacts correctly
- [ ] `artifacts_in_namespace()` still works
- [ ] `namespace_or_shared_filter` still works
- [ ] Index add/delete/iterate operations work
- [ ] `hard_delete()` cleans up all three tables
- [ ] Code review for any concerns

## Risks and Mitigations

**Risk:** The `Artifacts` iterator is used extensively for filtering. Changing
its data source from etcd to MariaDB could introduce subtle behavioral
differences.
**Mitigation:** The iterator already uses MariaDB for state-based prefiltering.
The change is to also source static values from MariaDB instead of etcd. The
filter functions operate on Artifact objects (not raw data), so they should
work unchanged.

**Risk:** The `index_NNNNNNNNNNNN` naming convention encodes the index number
in the attribute key. Moving to a proper table changes the access pattern.
**Mitigation:** The artifact_indexes table uses `index_number` as an integer
column, which is cleaner. All access to indexes goes through the Artifact
class methods (`add_index`, `del_index`, `get_all_indexes`, `most_recent_index`)
so the internal representation change is encapsulated.

**Risk:** Concurrent index operations. `add_index()` currently uses
`get_lock_attr('index', ...)` which is an etcd-based lock. This lock must
continue to work during and after migration.
**Mitigation:** The lock is on the attribute system, not on the indexes
specifically. It will continue to function via etcd. Consider whether a
MariaDB transaction could replace it in the future, but for now the etcd lock
is sufficient.

## Estimated Scope

- **Schema files**: 3 new files, ~50 lines each
- **database.proto**: ~80 lines of new messages and RPCs
- **mariadb.py**: ~400-500 lines of new functions
- **database/main.py**: ~150 lines of new gRPC handlers
- **artifact.py**: ~100 lines modified/added
- **mock_etcd.py**: ~50 lines of mock updates
- **test_mariadb_artifacts.py**: ~200 lines new test file
- **Documentation**: ~50 lines across 3 files

## Deferred Work

The following items were identified during implementation but are out of
scope for this migration:

- **`last_cluster_operation` and `metadata` attributes remain in etcd.**
  These are inherited from the base class and shared across all object
  types. Migrating them requires changes to the base class infrastructure.
- **Push namespace/source_url filtering down to MariaDB.** The
  `Artifacts` iterator now reads from MariaDB but still applies filters
  in Python. A future optimization could use SQL WHERE clauses for
  `namespace` and `source_url` filters via `from_url()` and
  `artifacts_in_namespace()`.
- **Replace etcd lock in `add_index()` with MariaDB transaction.**
  The `get_lock_attr('index', ...)` call still uses etcd for locking.
  Once all locking is migrated to MariaDB, this could use a database
  transaction instead.
- **TODO(andy) in `external_view()`**: Artifacts should not reference
  non-existent blobs. This pre-dates the migration and is unchanged.
- **Consolidate `from_db` and `_db_get` into base class.** The
  `from_db()` and `_db_get()` overrides are now near-identical across
  Artifact, Blob, Node, Namespace, Upload, and DnsMasq. The base class
  `from_db()` could be updated to handle Pydantic models directly,
  eliminating the override in every subclass.
- **Extract lazy attribute loading pattern.** The `_load_attributes`,
  `_ensure_attributes`, `_update_attributes` pattern is duplicated
  between Artifact and Blob. A base class mixin or generic method
  parameterized by the attributes type and mariadb functions would
  reduce duplication.
- **Reduce mock registration boilerplate in mock_etcd.py.** The
  3-line mock.patch/start/addCleanup pattern is repeated ~40 times.
  A helper method would eliminate ~120 lines of mechanical code.
