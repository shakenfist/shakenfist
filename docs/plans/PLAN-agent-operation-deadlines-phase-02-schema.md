# Agent operation deadlines phase 2: schema for deadlines and progress

## Prompt

This phase gives an `AgentOperation` somewhere to record the caller's
timing intent and the executor's progress, and changes no behaviour at
all: nothing in this phase reads the values it stores. Phase 3 sets
them from the REST API, phase 4 enforces them, phase 5 retries against
them.

Ground every change in the tree rather than in this document's
description of it: `shakenfist/schema/agentoperation_data.py` and
`shakenfist/schema/agentoperation_attributes.py` (the pydantic models
that *are* the table definitions), `shakenfist/schema/sqlalchemy.py`
(`pydantic_to_sqlalchemy_table`, and how `Optional[X]` becomes a
nullable column), `shakenfist/mariadb.py` (the three-layer
direct/gRPC/public pattern, the table version constants, and the
`_ensure_*_schema` migration functions), `protos/database.proto` and
`shakenfist/daemons/database/main.py` (the gRPC servicer),
`shakenfist/tests/mock_mariadb.py`, and
`shakenfist/operations/agentoperation.py` (the object).

Consult `CLAUDE.md` for the attribute field-mask rule, the two uuid
formats in MariaDB, and the `tox -e genprotos` requirement.

**Planning effort:** high. The mechanical half of this phase -- adding
two nullable columns to each of two tables -- follows patterns already
in the tree. The half that is not mechanical is what NULL is allowed
to mean in each column, and whether the object version bumps, and both
of those turned out to be wrong in the master plan. See decisions 1
and 2.

## Scope

In:

- Two new static values on `AgentOperationData`, `deadline` and
  `progress_timeout`, and two new mutable attributes on
  `AgentOperationAttributesData`, `last_progress` and `attempts`.
- The `agent_operations` and `agent_operation_attributes` table
  version bumps and the additive migrations that go with them, run by
  `sf-ctl ensure-mariadb-schema`.
- The new fields through all three MariaDB layers, the proto, the gRPC
  servicer, the test mock and the field-mask helper.
- `AgentOperation.new()` accepting the two static values, defaulted so
  the three existing API call sites are unchanged.
- `external_view()` returning all four values.
- A live-MariaDB test that the migration actually migrates.
- The `docs/operator_guide/database.md` schema tables, and the agent
  operation API reference's example response.

Out:

- Every reader of the new values. Nothing in this phase enforces a
  deadline, observes progress, counts an attempt, or removes
  `AGENT_OPERATION_EXECUTION_TIMEOUT`
  (`daemons/sidechannel/main.py:56`). Phase 2 is revertible on its
  own precisely because nothing depends on it yet.
- The REST parameters `deadline_seconds` and
  `progress_timeout_seconds`, their declarations, their
  `STRUCTURED_PARAMETERS` entries, and the two config defaults. All
  phase 3.
- The `expired` state and its `state_targets` / `FINAL_OBJECT_STATES`
  obligations. Phase 4.
- The `last_progress` write throttle. It is a property of the writer,
  and phase 4 owns the writer.
- Fixing `baseobject.upgrade()`'s inability to read an object written
  by a newer node -- see decision 2 and *Future work*.

## What the survey found

Every factual claim in the master plan's phase 2 scope was checked
against the tree. The mechanical claims hold. Two of the design claims
do not, and both are corrected at source in the master plan by the
same commit that adds this file.

1. **The two pydantic models are the table definitions, and
   `Optional[X]` is all that nullability requires.**
   `pydantic_to_sqlalchemy_table` (`schema/sqlalchemy.py:392`) sets
   `nullable=is_optional and not is_pk` at line 446, and the type map
   at lines 272-273 gives `int -> sa.BigInteger()` and `float ->
   sa.Double()`. So `Optional[float] = None` is exactly a nullable
   `DOUBLE`. There is **no** server-default support in the generator,
   which decision 3 has to account for.
2. **Neither `_ensure_agent_operations_schema` (`mariadb.py:18454`)
   nor `_ensure_agent_operation_attributes_schema` (`mariadb.py:18491`)
   has ever had a migration step.** Both consist of a create-if-absent
   block and nothing else; their comments say the historical v1->v2
   step was the etcd import marker. This phase therefore writes the
   first real migration for both tables, rather than adding a step to
   an established ladder. The shape to copy is
   `_ensure_instance_attributes_schema`'s v1->v2 block
   (`mariadb.py:19161-19176`), which is an `ADD COLUMN IF NOT EXISTS`
   with a comment explaining why re-running is safe.
3. **The field mask phase 1 added is exactly what phase 2 needs, and
   is currently a one-element dict.**
   `_agent_operation_attributes_column_values` (`mariadb.py:18688`)
   builds `{'results': ...}` and raises `ValueError` on an unknown
   field name; `add_result()`
   (`operations/agentoperation.py:183`) passes `fields=['results']`.
   Adding `last_progress` and `attempts` to that dict is the whole
   change, and from that moment `add_result()`'s mask is load-bearing
   rather than decorative.
4. **The static-value read and write paths enumerate columns by hand
   and will not pick the new ones up for free.**
   `_direct_create_agent_operation` (`mariadb.py:18533`) names five
   columns in its `insert().values()`, `_direct_get_agent_operation`
   (`mariadb.py:18566`) names five in its `AgentOperationData(...)`
   construction, and the gRPC pair
   (`mariadb.py:18763`/`18785`) plus the servicer's
   `_agentop_from_proto`/`_agentop_to_proto`
   (`daemons/database/main.py:4291`/`4306`) do the same. The
   attributes pair (`mariadb.py:18633`/`18656`, servicer `4419`/`4431`)
   likewise. Six places per field, and the compiler catches none of
   them, which is why the step plan names all of them.
5. **The test mock needs no per-field work.**
   `_mariadb_update_agent_operation_attributes`
   (`tests/mock_mariadb.py:2555`) applies the mask with
   `setattr(stored, field, getattr(data, field))`, and the create/get
   mocks store the pydantic object whole, so new model fields flow
   through unchanged. Only a mock that reconstructs the model by hand
   would have needed editing, and this one does not.
6. **proto3 field presence is available and already used for exactly
   this problem.** `NamespaceKeyAttributesProto.expiry`
   (`protos/database.proto:1836`) is `optional double expiry = 4;`
   with the comment "absent = never expires", read back with
   `d.HasField('expiry')` at `mariadb.py:13160`. Without `optional`, a
   proto3 `double` cannot distinguish "unset" from `0.0`, which is
   precisely the distinction decision 1 depends on -- and the gRPC
   path is the one every daemon except `sf-database` itself actually
   uses.
7. **The master plan's "NULL means no wall-clock deadline" is wrong,
   and inconsistent with its own progress timeout.** The design sketch
   says NULL `deadline` means the client asked for no deadline, while
   NULL `progress_timeout` means "use the server default". The same
   absence would mean opposite things in two adjacent columns, and the
   deadline reading is the unsafe one: every row written before this
   release, and every row written by a not-yet-upgraded API node
   during the rollout, is NULL, so at the moment phase 4 deletes the
   900-second constant those operations would become unbounded rather
   than defaulted. Decision 1 resolves it.
8. **The master plan's "version bump 3 -> 4" would break agent
   operations during a rolling upgrade.** `AgentOperation.current_version`
   is 3 (`operations/agentoperation.py:23`) as claimed, but
   `baseobject.upgrade()` (`baseobject.py:198`) cannot read a row
   whose version is *higher* than the reader's: it loops while
   `version != current_version`, so for a v4 row on a v3 node it looks
   up `_upgrade_step_4_to_5` via `getattr(self, step)` with no default
   and raises `AttributeError`. The `if not step_func: raise
   UpgradeException` immediately below (lines 208-211) is dead code
   for that reason. Transcribing the loop and running it with
   `{'version': 4}` against a class whose `current_version` is 3
   reproduces it. Agent operations are created on an API node and read
   on the hypervisor's `sf-sidechannel`, so a bump would break agent
   operations on every not-yet-rolled hypervisor for the length of the
   rollout. Decision 2 declines the bump; the underlying
   `baseobject` defect is recorded in *Future work* as out of scope.
9. **A live-MariaDB migration test costs no CI wiring.**
   `tools/ci-enum-widening-test.sh` runs
   `stestr run --serial 'shakenfist\.tests\.test_mariadb_.*_live\.'`,
   deliberately regex-matched so that "a live test module added later
   is picked up without touching this script", and the workflow job
   that calls it (`.github/workflows/functional-tests.yml:700-728`,
   "Schema ENUM widening") runs on `merge_group`. A new
   `test_mariadb_agent_operations_live.py` therefore runs in the merge
   queue against a real MariaDB with no workflow edit. The script also
   fails the job if zero live tests ran, so the module cannot silently
   skip.
10. **The documentation that enumerates these columns is two table
    rows.** `docs/operator_guide/database.md:912` and `:935` list the
    columns of `agent_operations` and `agent_operation_attributes`
    respectively. `docs/developer_guide/api_reference/agentoperations.md`
    carries an example response body that will gain fields.

## Decisions

1. **NULL means "no client intent recorded, use the server default",
   in both new static columns; an explicit `0.0` means "none".** So
   `deadline` NULL -> phase 4 applies `AGENT_OPERATION_DEFAULT_DEADLINE`,
   `deadline = 0.0` -> no wall-clock deadline at all; `progress_timeout`
   NULL -> the server default for progress-capable commands,
   `progress_timeout = 0.0` -> progress timeout disabled. This
   contradicts the master plan (survey finding 7) and is corrected
   there. Three reasons: absence should not mean opposite things in
   adjacent columns; the unsafe reading is the one being replaced,
   because NULL is what every legacy row and every row written by a
   not-yet-upgraded API node contains, and phase 4 deletes the 900s
   backstop those rows currently rely on; and `0.0` is an unambiguous
   sentinel for `deadline` because a real deadline is an absolute unix
   timestamp of order 1.7e9 and can never legitimately be zero. The
   REST sentinel the master plan already specified -- the client
   passing `deadline_seconds=0` to mean "no deadline" -- maps onto the
   stored `0.0` directly, so phase 3's API contract is unchanged.
   Phase 4 inherits one obligation from this: a NULL deadline needs an
   anchor for the default, and the operation has no stored creation
   timestamp, so the fallback anchor is dispatch time. That is
   strictly a fallback for rows no deadline-aware API server wrote,
   and it is written into the master plan's enforcement section by
   this commit.
2. **`AgentOperation.current_version` stays at 3. There is no object
   version bump and no `_upgrade_step_3_to_4`.** This contradicts the
   master plan (survey finding 8) and is corrected there. A bump would
   make every not-yet-rolled node raise `AttributeError` on any agent
   operation created by an already-rolled API node, for the length of
   the rollout, and this is the one object type whose write and read
   nodes are reliably different processes on different machines. It
   would also buy nothing: there is no data to migrate, because NULL
   is a meaningful value under decision 1 rather than a gap to fill,
   so the step would be an empty no-op like the existing
   `_upgrade_step_2_to_3`. The fact a deployment actually needs to
   know -- "does this database have the columns?" -- is carried by the
   *table* schema version, which `sf-database` refuses to start
   against if it is behind. This is the decision most likely to be
   argued with, since the project's habit is to bump on any change to
   an object's static values; the counter-argument is that the habit
   is harmless only because most bumps have not previously landed on
   an object read by a different daemon than the one that writes it.
3. **`attempts` is a non-nullable integer defaulting to 0;
   `last_progress` is a nullable float.** An attempt count has no
   "unknown" state worth representing, and a non-nullable column means
   phase 5 never has to write `attempts or 0`. Because the table
   generator has no server-default support (survey finding 1), the
   migration adds it as `BIGINT NOT NULL DEFAULT 0` while a fresh
   `create_all` produces `BIGINT NOT NULL` with no default. That
   divergence is deliberate and harmless -- every insert supplies the
   value -- and the live test asserts the behaviour that matters
   (existing rows read back 0) rather than the DDL text.
4. **Both proto messages use proto3 explicit presence.** `optional
   double deadline`, `optional double progress_timeout` on
   `AgentOperationStaticData`; `optional double last_progress` on
   `AgentOperationAttributesProto`, and a plain `int64 attempts` since
   it is never NULL. Copy `NamespaceKeyAttributesProto.expiry`
   (`protos/database.proto:1836`) and its `HasField` read at
   `mariadb.py:13160`. Without presence the wire cannot distinguish
   "unset" from `0.0`, and decision 1 dies at the gRPC boundary, which
   is the boundary every daemon except `sf-database` crosses.
5. **`AgentOperation.new()` gains `deadline=None, progress_timeout=None`
   keyword arguments now**, defaulted so the three call sites in
   `external_api/instance.py` (lines 1701, 1745, 1789) are untouched
   by this phase. Phase 3 then only has to declare the REST parameters
   and pass them. Adding the parameters with the columns keeps the
   object layer's story in one commit.
6. **`external_view()` returns all four values and costs no extra
   database round trip.** The master plan asks for the two static
   values; `attempts` and `last_progress` come along because they are
   what makes a wedged operation diagnosable from `sf-client` while
   phases 4 and 5 are being brought up, and because they are free: the
   view already reads the attributes row once via `self.results`
   (`operations/agentoperation.py:129`, `156-164`). Restructure it to
   read the attributes row once into a local and take all three values
   from it, rather than adding two more properties that would each
   repeat the read.
7. **The two table versions both go 2 -> 3, and the migrations are
   additive `ADD COLUMN IF NOT EXISTS`.** No backfill, no index, no
   constraint. `ADD COLUMN` for trailing nullable columns is an
   instant operation on InnoDB, and `agent_operations` is a small
   table, so no online-DDL care is required beyond what
   `ensure-mariadb-schema` already does.
8. **The live migration test is a required part of this phase, not a
   nice-to-have.** The unit suite mocks MariaDB completely, so nothing
   else in the repository can tell the difference between a migration
   that works and one that raises inside its `try`. Survey finding 9
   means the test runs in the merge queue for free.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Add the four fields to the pydantic models, bump both table versions, and write both migrations. In `shakenfist/schema/agentoperation_data.py`, add `deadline: Optional[float] = None` and `progress_timeout: Optional[float] = None` to `AgentOperationData` after `commands` and before `version`, each with a docstring-level comment stating the decision 1 semantics verbatim: NULL means no client intent was recorded and the server default applies, `0.0` means the client explicitly asked for none, and `deadline` is an absolute unix timestamp. Extend the class docstring's Attributes list the same way. In `shakenfist/schema/agentoperation_attributes.py`, add `last_progress: Optional[float] = None` (unix timestamp of the most recent observed progress; NULL means none observed yet) and `attempts: int = 0` (dispatch counter for phase 5's retry bound), extending its docstring too. Note `AgentOperationData` is `frozen=True` and `AgentOperationAttributesData` is not; do not change either. In `shakenfist/mariadb.py`: bump `AGENT_OPERATIONS_VERSION` (line 351) and `AGENT_OPERATION_ATTRIBUTES_VERSION` (line 352) from 2 to 3 -- `EXPECTED_SCHEMA_VERSIONS` at lines 449-450 is derived from the constants so needs no edit, and no new table means `EXPECTED_TABLE_NAMES` in `tests/test_mariadb_schema_concurrency.py:154` needs none either. Add a `if current_ver < AGENT_OPERATIONS_VERSION:` block to `_ensure_agent_operations_schema` (line 18454) after the create block and before the return, issuing `ALTER TABLE agent_operations ADD COLUMN IF NOT EXISTS deadline DOUBLE NULL` and the same for `progress_timeout`, then `_set_table_version`; model it on `_ensure_instance_attributes_schema`'s v1->v2 block at lines 19161-19176 including its comment about why re-running is safe, and wrap the executes in the `try/except (IntegrityError, OperationalError)` with a `LOG.debug` the way the v2->v3 block at 19177-19196 does. Do the same in `_ensure_agent_operation_attributes_schema` (line 18491) for `last_progress DOUBLE NULL` and `attempts BIGINT NOT NULL DEFAULT 0`; the `DEFAULT 0` is required so existing rows are valid and is a deliberate divergence from what `create_all` produces on a fresh database (see decision 3), so say so in the comment. Then extend the direct layer: `_direct_create_agent_operation` (line 18533) adds `deadline=data.deadline, progress_timeout=data.progress_timeout` to its `insert().values()`; `_direct_get_agent_operation` (line 18566) passes `deadline=result.deadline, progress_timeout=result.progress_timeout` into the `AgentOperationData(...)` it builds; `_direct_create_agent_operation_attributes` (line 18633) and `_direct_get_agent_operation_attributes` (line 18656) do the equivalent for `last_progress` and `attempts`; and `_agent_operation_attributes_column_values` (line 18688) gains `'last_progress': data.last_progress` and `'attempts': data.attempts` in its `all_values` dict -- that dict is the mask's vocabulary, so a field missing from it cannot be written. Do not touch the gRPC layer, the proto, the servicer or the object; those are 2b and 2c. |
| 2b | medium | sonnet | none | Carry the four new fields across the gRPC boundary with proto3 explicit presence. In `protos/database.proto`, add to `AgentOperationStaticData` (line 2108) `optional double deadline = 6;` and `optional double progress_timeout = 7;`, and to `AgentOperationAttributesProto` (line 2137) `optional double last_progress = 3;` and `int64 attempts = 4;`. Comment each in the style of `NamespaceKeyAttributesProto.expiry` at line 1836 -- absent means SQL NULL, which for these two means "no client intent recorded, server default applies", while an explicit 0 means "none"; `attempts` is not optional because it is never NULL. Regenerate with `tox -e genprotos` -- **never** run `grpc_tools.protoc` directly -- and commit the regenerated `shakenfist/protos/` files in the same commit. In `shakenfist/mariadb.py`, set the new fields on the request in `_grpc_create_agent_operation` (line 18763) and `_grpc_create_agent_operation_attributes` (line 18826), and read them back with `HasField` in `_grpc_get_agent_operation` (line 18785) and `_grpc_get_agent_operation_attributes` (line 18844), copying the `expiry=d.expiry if d.HasField('expiry') else None` idiom at line 13160. `_grpc_update_agent_operation_attributes` (line 18868) already forwards `fields`; it must now also populate `last_progress` and `attempts` on the proto it sends, because the mask names columns and the servicer reads their values off the message. Note that a proto3 `optional` field cannot be set to None in Python -- build the message and then assign only when the value is not None, or use a kwargs dict, whichever reads better against the surrounding code. In `shakenfist/daemons/database/main.py`, extend `_agentop_from_proto` (line 4291), `_agentop_to_proto` (line 4306), `_agentop_attrs_from_proto` (line 4419) and `_agentop_attrs_to_proto` (line 4431) with the same presence handling. Register no new metrics counters -- these are existing RPCs. `shakenfist/tests/mock_mariadb.py` needs no edit: its agent operation mocks store and mask the pydantic objects whole (see `_mariadb_update_agent_operation_attributes` at line 2555), so new model fields flow through unchanged; confirm this by reading it rather than by assuming. |
| 2c | medium | sonnet | none | Expose the new values on the object. In `shakenfist/operations/agentoperation.py`: give `new()` (line 109) `deadline=None, progress_timeout=None` keyword arguments after `commands` and thread them into the `_db_create` metadata dict; do **not** touch the three call sites in `shakenfist/external_api/instance.py` (lines 1701, 1745, 1789), which is phase 3's work. In `_db_create` (line 57), pass `deadline=metadata.get('deadline')` and `progress_timeout=metadata.get('progress_timeout')` into the `AgentOperationData(...)` construction; leave the initial `AgentOperationAttributesData(uuid=..., results={})` alone, since `last_progress` and `attempts` take their model defaults. In `_db_get` (line 85), add `'deadline': data.deadline` and `'progress_timeout': data.progress_timeout` to the result dict. In `__init__` (line 41), store them as private attributes alongside `__commands` and add `deadline` and `progress_timeout` read-only properties in the "Static values" block after `commands` (line 151). **Do not change `current_version` and do not add an upgrade step** -- see decision 2 in the plan; a reviewer expecting a bump should be pointed at that decision. Restructure `external_view()` (line 121) so it reads the attributes row once into a local -- reusing the get-or-create dance the `results` property performs at lines 156-164, which is what it is implicitly calling today via `self.results` -- and adds `results`, `attempts` and `last_progress` from that one read plus `deadline` and `progress_timeout` from the static values; the point is that the view gains three fields without gaining a database round trip. Add unit tests to `shakenfist/tests/test_instance.py` alongside `AgentOperationQueueTestCase` (line 732), or a new module if that reads better: that `AgentOperation.new()` with no deadline arguments stores None for both and that an operation created with `deadline=1.5, progress_timeout=2.5` reads them back, that `attempts` defaults to 0 and `last_progress` to None, and that `external_view()` contains all five of `deadline`, `progress_timeout`, `attempts`, `last_progress` and `results`. |
| 2d | high | opus | none | Write `shakenfist/tests/test_mariadb_agent_operations_live.py`, a live-MariaDB test that the phase 2 migration migrates. Model it closely on `shakenfist/tests/test_mariadb_enum_columns_live.py`: the same `@unittest.skipUnless(os.environ.get('SF_MARIADB_TEST_DSN'), ...)` gate, the same DESTRUCTIVE docstring warning, the same `sa.create_engine(os.environ[DSN_ENV])` setUp with an `addCleanup` that drops the tables it touched (`agent_operations`, `agent_operation_attributes`, `schema_versions`). The test builds the current schema via the public `ensure_schema()` path, then *rewinds* the database to what a pre-phase-2 deployment looks like -- `ALTER TABLE ... DROP COLUMN` for all four new columns and `_set_table_version(engine, table, 2)` for both tables -- inserts a row into each table using only the pre-phase-2 columns, runs `ensure_schema()` again, and asserts: both tables report version 3; all four columns exist (query `information_schema.columns`); the pre-existing `agent_operations` row reads back NULL for `deadline` and `progress_timeout`; the pre-existing `agent_operation_attributes` row reads back `attempts = 0` and NULL `last_progress` (this is the assertion that catches an `ADD COLUMN ... NOT NULL` without a `DEFAULT`, which would fail outright on a non-empty table); and a third `ensure_schema()` call is a clean no-op that leaves the versions at 3. **Mutation-test every assertion before you are done**: temporarily break the migration (drop the `DEFAULT 0`, then drop one `ADD COLUMN`, then leave the version un-bumped) and confirm the test fails each time for the right reason, then restore. A live test that passes against a broken migration is worse than no test. Run the module the way CI does -- `SF_MARIADB_TEST_DSN=... stestr run --serial 'shakenfist\.tests\.test_mariadb_agent_operations_live\.'` -- against a disposable MariaDB; `tools/ci-install-mariadb.sh` with `tools/bootstrap-mariadb.sql` stands one up, or use a local instance. Do not edit `tools/ci-enum-widening-test.sh` or `.github/workflows/functional-tests.yml`: the script's regex already matches any `test_mariadb_*_live.py` module by design, and confirming that by reading it is part of this step. |
| 2e | low | sonnet | none | Documentation for the schema change. In `docs/operator_guide/database.md`, extend the `agent_operations` row (line 912) to list `deadline`, `progress_timeout` and the `agent_operation_attributes` row (line 935) to list `last_progress`, `attempts`, matching the terse column-list style of the surrounding rows. Then add a short paragraph -- in the section those tables live in, wherever that section's prose already sits -- stating the decision 1 semantics once, in the words the pydantic docstrings use: for `deadline` and `progress_timeout`, NULL means no client intent was recorded and the server default applies, an explicit `0` means the client asked for none, and `deadline` is an absolute unix timestamp rather than a duration. This is the fact most likely to be restated wrongly elsewhere later, so it wants exactly one home. In `docs/developer_guide/api_reference/agentoperations.md`, add the new keys to the example response body so it matches what `external_view()` now returns. Do not document the REST parameters `deadline_seconds` / `progress_timeout_seconds` or the config options -- they do not exist until phase 3, and documenting them now would be documenting a lie. |

Each step is its own commit. 2a and 2b both edit `shakenfist/mariadb.py`
and should be done in that order; 2c depends on both; 2d depends on 2a
only; 2e last, so it describes what actually landed.

## Risks and mitigations

- **Decisions 1 and 2 both contradict the master plan, and a reviewer
  reading the master plan alone would find this phase wrong.**
  Mitigation: the planning commit corrects the master plan's design
  sketch and phase table at source, and both decisions here carry the
  reproduction or the file reference that motivated them. The
  `baseobject.upgrade()` behaviour in particular is reproducible in
  four lines and the plan says so.
- **Decision 1 is a phase 2 decision with a phase 4 obligation.** If
  phase 4 enforces "NULL means no deadline" anyway, the columns are
  fine and the behaviour is the unsafe one this phase was trying to
  avoid, and nothing in phase 2 can fail to notice. Mitigations: the
  semantics are written into the pydantic field comments and the
  proto comments, which phase 4 must read to use the fields at all;
  they are stated once in `docs/operator_guide/database.md` by step
  2e; and the master plan's enforcement section is edited by the
  planning commit to name the dispatch-time fallback anchor.
- **Six hand-enumerated column lists per field, none of them checked
  by the compiler** (survey finding 4). A missed one is a silently
  dropped value on one of the two paths, and the direct path is only
  exercised on database-tier nodes. Mitigations: the 2a and 2b briefs
  name every site with a line number; the live test in 2d exercises
  the direct path against a real server; and the definition-of-done
  script greps each layer for both field names.
- **`optional` on a proto3 scalar cannot be assigned None in Python.**
  A naive `AgentOperationStaticData(deadline=data.deadline)` raises
  when the value is None, and the failure would be at runtime on the
  gRPC path only. Mitigation: called out in the 2b brief; the unit
  suite's gRPC-routing tests and `pre-commit` will not catch it, so
  the management session reads those four converters specifically.
- **An old `sf-database` during a rolling upgrade silently drops the
  new fields**, because proto3 ignores unknown fields, so an operation
  created through it has NULL deadline. Under decision 1 that means
  "apply the server default", which is the safe direction and needs no
  mitigation -- it is recorded here so nobody later reads it as a bug.
- **The migration runs against a table nobody has migrated before.**
  Mitigation: both migrations are additive `ADD COLUMN IF NOT EXISTS`
  with no backfill and no constraint, they are idempotent by
  construction, and 2d proves the whole path on a real MariaDB with
  its assertions mutation-tested.

## Definition of done

The first six are a script, run from the repository root:

```bash
# 1. The four fields exist on the two pydantic models.
grep -q 'deadline: Optional\[float\]' shakenfist/schema/agentoperation_data.py
grep -q 'progress_timeout: Optional\[float\]' shakenfist/schema/agentoperation_data.py
grep -q 'last_progress: Optional\[float\]' shakenfist/schema/agentoperation_attributes.py
grep -q 'attempts: int' shakenfist/schema/agentoperation_attributes.py

# 2. Both table versions moved, and the migrations are additive and
#    idempotent. Two ADD COLUMN IF NOT EXISTS per table.
grep -q '^AGENT_OPERATIONS_VERSION = 3' shakenfist/mariadb.py
grep -q '^AGENT_OPERATION_ATTRIBUTES_VERSION = 3' shakenfist/mariadb.py
test 4 -eq "$(grep -c 'ALTER TABLE agent_operation.* ADD COLUMN IF NOT EXISTS' shakenfist/mariadb.py)"

# 3. The mask's vocabulary grew, so the new attributes are writable.
#    Without this the columns exist and can never be set.
sed -n '/^def _agent_operation_attributes_column_values/,/^def /p' shakenfist/mariadb.py \
    | grep -q "'last_progress'"
sed -n '/^def _agent_operation_attributes_column_values/,/^def /p' shakenfist/mariadb.py \
    | grep -q "'attempts'"

# 4. Both MariaDB layers carry both static values. The direct layer is
#    only exercised on database-tier nodes and the gRPC layer
#    everywhere else, so a value dropped on one path is invisible to
#    the other's tests.
for f in _direct_create_agent_operation _direct_get_agent_operation \
         _grpc_create_agent_operation _grpc_get_agent_operation; do
    sed -n "/^def ${f}(/,/^def [a-z_]*(/p" shakenfist/mariadb.py | grep -q deadline
    sed -n "/^def ${f}(/,/^def [a-z_]*(/p" shakenfist/mariadb.py | grep -q progress_timeout
done

# 5. The proto uses explicit presence for the three nullable fields,
#    and the stubs were regenerated in the same commit.
grep -q 'optional double deadline' protos/database.proto
grep -q 'optional double progress_timeout' protos/database.proto
grep -q 'optional double last_progress' protos/database.proto
git diff --name-only HEAD~1 | grep -q 'shakenfist/protos/database_pb2'

# 6. The object version did NOT move, and no upgrade step appeared.
#    This is decision 2, and it is the one a well-meaning reviewer or
#    a later agent is most likely to "fix".
grep -q '    current_version = 3' shakenfist/operations/agentoperation.py
test 0 -eq "$(grep -c '_upgrade_step_3_to_4' shakenfist/operations/agentoperation.py)"
```

And, by inspection:

- `shakenfist/tests/test_mariadb_agent_operations_live.py` exists,
  every assertion in it has been mutation-tested against a
  deliberately broken migration, and the module passes against a real
  MariaDB with `SF_MARIADB_TEST_DSN` set. A live test nobody has seen
  fail proves nothing.
- `external_view()` returns `deadline`, `progress_timeout`, `attempts`
  and `last_progress` in addition to what it returns today, and reads
  the attributes row exactly once -- asserted by a test, since the
  round-trip count is the reason the fields were restructured rather
  than added as properties.
- No fact about what NULL means is stated differently in
  `shakenfist/schema/agentoperation_data.py`, `protos/database.proto`,
  `docs/operator_guide/database.md`, and the master plan's design
  sketch. All four say: NULL means no client intent was recorded and
  the server default applies; `0` means none.
- Nothing outside this phase's files reads the new values. `grep -rn
  'deadline\|progress_timeout\|last_progress' shakenfist/daemons/`
  finds no new consumer.
- `pre-commit run --all-files` passes (flake8, stestr, mypy).
- Cluster CI's `test_agentops` passes on the branch, proving the
  schema change did not disturb the existing agent operation path.

## Future work

- **`baseobject.upgrade()` cannot read an object written by a newer
  node.** Survey finding 8: for a row whose version exceeds the
  reader's `current_version`, the loop looks up the next
  `_upgrade_step_N_to_M` with `getattr(self, step)` and no default,
  so it raises `AttributeError` rather than the `UpgradeException`
  the author clearly intended — the `if not step_func: raise
  exceptions.UpgradeException(...)` at `baseobject.py:208-211` is
  unreachable. This affects every object type, not agent operations
  in particular, and `docs/operator_guide/upgrades.md` currently
  promises rolling upgrades are safe without qualifying it. Out of
  scope here for two reasons: the fix is a `baseobject` behaviour
  change touching every object type and deserves its own review, and
  decision 2 means this phase does not depend on it. It wants an
  issue of its own, and the fix is a judgement call between raising a
  clean `UpgradeException`, and treating a newer row as readable
  when the reader can still make sense of its fields.
- **A "created at" timestamp on `AgentOperation`.** Decision 1's
  fallback anchor for a NULL deadline is dispatch time only because
  the operation has no stored creation timestamp; `object_states`
  carries one for the `initial` transition but reading it is an extra
  query on a hot path. If a creation timestamp is ever wanted for
  other reasons, the fallback anchor should move to it.

## Back brief

Before executing any step, back brief the operator on the plan as
understood. In particular, confirm agreement on decisions 1 and 2
before step 2a begins -- both contradict the master plan, both are
cheap to change now and expensive to change once phases 3 to 5 are
written against them, and decision 2 in particular is a deliberate
departure from the project's habit of bumping an object version
whenever its static values change.

Additionally, gate step 2d: bring the mutation-test results to the
management session (which assertion failed for which break) before
the step is considered complete, since a live test is only worth its
CI minutes if it has been seen to fail.
