# Cluster op visibility phase 2: observational flag, schema and query layer

This is a detailed phase plan under
[PLAN-cluster-op-visibility.md](PLAN-cluster-op-visibility.md).
Read the master plan's Situation section first, but do not trust
its line numbers: the survey below found almost all of them
stale and gives current ones.

**Planning effort:** high (per the master plan). **Review
effort:** high. This phase changes a schema, a gRPC message
that every enqueue crosses, and the two queries that
`Network.is_okay()`, the network maintainer and six
`runs_after` chains depend on.

## Scope

In scope:

* `cluster_operation_targets` v3: a new `observational` column,
  and the `idx_cot_target` index widened to cover it.
* A way for an operation model to declare which of its *tasks*
  are observational, and an enqueue path that turns that into
  the stored flag, written in the same transaction as the op.
* `get_latest_cluster_operation_target()` and
  `has_pending_cluster_operation_target()` exclude observational
  rows, in SQL.
* The read paths that hand target rows back (the target model,
  its proto, the by-target op listing) carry the flag.
* Deleting the dead `_set_last_cluster_operation()`, the
  redundant post-hoc target write in `node_blob_op`, and with
  them the whole `create_cluster_operation_target` path.
* The schema and upgrade documentation for v3.

Out of scope, deliberately:

* Marking anything observational. Every model ships with an
  empty observational task set, so this phase changes no
  behaviour. A test pins that, and phase 3 changes the test on
  purpose.
* The developer-guide account of mutating vs observational ops.
  It is written in phase 3, when the distinction first means
  something. A page explaining a mechanism nothing uses would be
  wrong the day phase 3 lands.
* `has_pending_operations` in `external_view()`, and rewriting
  `get_cluster_operations()` and the instance-delete
  cancellation walk over the targets table (phase 4).
* Changing the phase 1 CI await helpers (phase 5).

## What the survey found

The master plan was written on 2026-07-19, and three months of
other work has landed since. Its *premises* still hold: the
pointer race is real, the six `runs_after` sites still exist,
and the flag still belongs in SQL. Several of its *facts* do
not. The corrections below have also been made at their source,
in the master plan's Situation, Open questions and phase 3
sections, as part of this planning commit, so later phases do
not need to redo them.

1. **Every line reference has moved.** `mariadb.py` has roughly
   doubled. Current locations:
   * Table definition: `_get_cluster_operation_targets_table()`
     at `shakenfist/mariadb.py:1703`. Migration:
     `_ensure_cluster_operation_targets_schema()` at
     `mariadb.py:1754`. `CLUSTER_OPERATION_TARGETS_VERSION = 2`
     at `mariadb.py:440`, registered in `EXPECTED_SCHEMA_VERSIONS`
     at `mariadb.py:534`. Startup enforcement:
     `verify_schema_versions()` at `mariadb.py:1275`.
   * Direct accessors: `mariadb.py:4897-5250`. gRPC client
     wrappers: `mariadb.py:5254-5470`, with `_target_from_proto()`
     at `mariadb.py:5282`. Public dispatchers:
     `mariadb.py:5475-5646`.
   * The atomic enqueue: `_direct_create_and_enqueue_cluster_operation`
     at `mariadb.py:21466` (target inserts at about line 21590),
     `_grpc_create_and_enqueue_cluster_operation` at
     `mariadb.py:21941`, public `create_and_enqueue_cluster_operation`
     at `mariadb.py:22539`.
   * By-target listing: `_direct_list_cluster_operations_for_target`
     at `mariadb.py:21396`.
   * The proto is `protos/database.proto`, not
     `shakenfist/protos/database.proto`. RPCs at lines 334-342 and
     366; `ClusterOperationTargetProto` at 2449;
     `CreateAndEnqueueClusterOperationTarget` and
     `CreateAndEnqueueClusterOperationRequest` at 2612-2627;
     `ClusterOperationData` at 2567;
     `ListClusterOperationsForTargetReply` at 2600.
   * Database daemon handlers: `shakenfist/daemons/database/main.py:5457-5840`,
     `ListClusterOperationsForTarget` at 6018,
     `CreateAndEnqueueClusterOperation` at 6068, counter
     registration around 6566.
   * The CI helpers moved with the package: they are in
     `shakenfist/deploy/shakenfist_ci/base.py`, not
     `deploy/shakenfist_ci/base.py`.

2. **The flag cannot be per operation model.** The master plan
   preferred a per-model marker and asked phase 2 to confirm.
   Both models that carry the known observational work also
   carry mutating work:
   * `node_inst_op` (`schema/operations/node_inst_op.py:29`) has
     `collect_billing_statistics` and `health_check_kvm_process`
     (the sweep at `daemons/cluster/scheduled_tasks.py:480`) and
     `instance_delete` (enqueued at `instance.py:2798`).
   * `node_blob_op` (`schema/operations/node_blob_op.py:31`) has
     `verify_size_and_checksum` (the sweep at
     `scheduled_tasks.py:413`) and `ensure_local`.

   The master plan's fallback was a flag passed by every caller.
   Decision 1 takes a third route.

3. **Terminal operations do not stay in the table.** Since #3864
   an operation is hard-deleted about thirty seconds after it
   goes terminal. `BaseClusterOperation.hard_delete()`
   (`operations/baseoperation.py:267`) deletes its
   `cluster_operation_targets` rows first. The 7-day
   `CLUSTER_OPERATION_TARGET_RETENTION` prune
   (`daemons/cluster/main.py:176`) is now only a backstop. So
   this table holds in-flight ops plus the last half-minute of
   terminal ones, not a "full append-only history". Three
   consequences:
   * The master plan's reason for still writing observational
     rows ("audit history") is weak. The real reason is
     load-bearing: the per-blob sweep (`scheduled_tasks.py:388`)
     reads blob target rows through
     `get_cluster_operation_targets_for_object()` so that it does
     not enqueue a duplicate checksum op for a node that already
     has one pending. Those are exactly the ops phase 3 will mark
     observational. That reader must keep seeing them.
   * Per-object row counts are small, which makes the index
     change cheap (decision 4).
   * Two of the master plan's open questions are settled.
     `/operations?all=true` cannot grow without bound, because
     history is capped at about thirty seconds of terminal ops.
     And yes, op hard-delete prunes target rows. Both are now
     recorded as resolved in the master plan.

4. **There are two more target-row writers than the master plan
   says.** It names the auto-write loop in
   `enqueue_cluster_operation()` as the only writer. Since phase
   1b, the writer is the inline insert inside the atomic
   enqueue transaction. There are two more:
   * `node_blob_op.create_and_enqueue()`
     (`schema/operations/node_blob_op.py:96`) calls
     `mariadb.create_cluster_operation_target()` *after*
     `enqueue_cluster_operation()` has already written the same
     row in-transaction (`blob_uuid` is in its `target_fields`).
     The second write always hits `uq_cot_op_target`, which the
     direct writer treats as success. It is a redundant
     non-atomic write left over from before phase 1b.
   * `baseobject._set_last_cluster_operation()`
     (`baseobject.py:793`) has no production callers, as the
     master plan said. Two tests still exercise it
     (`tests/test_cluster_operation_targets.py:54-80`), plus a
     stub in `tests/test_external_api.py:83`.

   Once both go, nothing in the tree calls
   `create_cluster_operation_target`.

5. **A seventh consumer of the pointer was missed.** Besides the
   six `runs_after` sites, the external views and
   `get_cluster_operations()`, the instance delete path reads
   `self.last_cluster_operation` (`instance.py:2773`) and walks
   that op's dependency tree to decide which outstanding ops to
   cancel. A background op that shadows the pointer makes delete
   cancel nothing. This phase's filter fixes that case as a side
   effect once phase 3 marks the sweeps. The general fix, walking
   every pending target row the way phase 4 will for
   `get_cluster_operations()`, has been added to phase 4's scope
   in the master plan.

6. **`get_recent_terminal_op_states_for_target()` is a new
   by-target reader** (`mariadb.py:5099`), used by the network
   maintainer's cooldown and circuit breaker
   (`daemons/network/maintain.py:583,600`). Both calls pass
   `target_object_type=NETWORK` and `op_type='net_op'`. No
   network op will be observational, so this phase leaves it
   unfiltered (decision 5).

7. **Coalescing touches none of this.** Only `net_op` declares
   `coalescible_key_columns` (`operations/net_op.py:67`), and its
   coalescing queries join `cluster_operations` and
   `object_states`, not the targets table.

8. **A live-MariaDB test pattern exists, and CI already runs
   it.** `shakenfist/tests/test_mariadb_enum_columns_live.py`
   and five siblings are gated on `SF_MARIADB_TEST_DSN`.
   `tools/ci-enum-widening-test.sh` picks up any
   `test_mariadb_*_live.py` module, so a new one is run in CI
   without workflow edits.
   `PLAN-agent-operation-deadlines-phase-02-schema.md` step 2d is
   a worked example of the brief to follow.

Nothing in the survey contradicts the master plan's design
decisions, which are mutating vs observational, a column
denormalised onto the targets table, and the filter in SQL.

## Decisions

1. **Observational is declared per task and derived per
   operation.** Each schema model in
   `shakenfist/schema/operations/*_op.py` may declare
   `observational_tasks: ClassVar[frozenset[model_tasks]]`,
   alongside `target_fields`. A model that does not declare it
   has none. `enqueue_cluster_operation()` sets the stored flag
   to true when the operation has at least one task *and every
   task* is in that set. One mutating task makes the whole op
   mutating.

   This is the decision a reviewer is most likely to push back
   on, because the master plan offered two other options. A
   per-model marker cannot represent `node_inst_op` or
   `node_blob_op` (survey item 2). A per-call flag would work,
   but every caller would then have to remember to pass it. A
   caller that adds `instance_delete` to a sweep's task list
   would silently enqueue a mutating op flagged observational,
   and that op would be invisible to `has_pending`. Deriving the
   flag from the tasks means the flag cannot disagree with the
   work the op does. It also keeps the classification in the
   schema file next to the task enum, where phase 3's audit
   comments will live. The "every task" rule keeps the mistake
   conservative: a mixed op counts as mutating, so the worst
   case is a pointer that still gets shadowed. Without that rule,
   a mixed op flagged observational would bypass gating.

   The derivation is one helper in
   `shakenfist/schema/operations/util.py`,
   `is_observational(model_class, tasks) -> bool`. It accepts
   the serialised task names that `metadata['tasks']` holds after
   `model_dump(mode='json')`.

2. **One flag per operation on the wire, one per row in
   storage.** `CreateAndEnqueueClusterOperationRequest` gains
   `bool observational = 8`. It is not added to each
   `CreateAndEnqueueClusterOperationTarget`, because an operation
   has one classification. The direct writer copies it onto every
   target row it inserts. Storage is per row because the filters
   run against the targets table, and a JOIN to read the
   classification back from `cluster_operations.metadata_json`
   would cost what the column avoids.

3. **Column: `observational BOOLEAN NOT NULL DEFAULT 0`**, added
   with `ADD COLUMN IF NOT EXISTS`. The server default is the
   conservative value, so a row an older daemon writes during a
   rolling upgrade is mutating. That is the same reasoning the
   master plan gave, and it is now explicit in the migration
   comment.

4. **Widen `idx_cot_target` to `(target_object_type,
   target_uuid, observational)`.** Both filtered queries have the
   shape `WHERE type = ? AND uuid = ? AND observational = 0`, and
   `get_latest` adds `ORDER BY sequence_number DESC LIMIT 1`.
   InnoDB secondary indexes carry the primary key, so the widened
   index serves the filter and that ordering without a sort,
   reading only the row it returns.
   The two-column prefix still serves every unfiltered reader. The
   index is replaced rather than joined by a second one: the
   prefix makes a second index redundant, and every extra index
   costs a write on the busiest insert in the cluster. The
   migration is a single `ALTER TABLE` with `ADD COLUMN IF NOT
   EXISTS`, `DROP INDEX IF EXISTS idx_cot_target` and an
   *unconditional* `ADD INDEX idx_cot_target (...)`, so a crash
   before `_set_table_version()` can be re-run safely: the re-run
   just rebuilds the index. *(Corrected during step 2b.)* This
   plan originally specified `ADD INDEX IF NOT EXISTS`. MariaDB
   evaluates `IF NOT EXISTS` against the table as it stood before
   the `ALTER`, so that form drops the old index and then skips
   the add, which leaves no index at all. The step 2b live test
   reproduced this on 10.11 and 11.8, and now guards against it. The table holds in-flight ops
   plus about thirty seconds of terminal ones (survey item 3),
   so the rebuild is small. The live test checks with `EXPLAIN`
   that both filtered queries use the index.

5. **Only the two "current state" queries filter.**
   `get_latest_cluster_operation_target()` and
   `has_pending_cluster_operation_target()` add
   `observational IS false`, in the direct SQL (the gRPC path
   reaches the same direct function in the database daemon, so
   there is one filter, not two). These readers deliberately stay
   unfiltered, each with a one-line comment saying why:
   * `get_cluster_operation_targets_for_object()`: the per-blob
     sweep's duplicate guard needs observational rows (survey
     item 3).
   * `list_cluster_operations_for_target()`: this is the "what
     touched this object" view, so it reports everything and
     carries the flag.
   * `get_recent_terminal_op_states_for_target()`: its only
     callers ask about `net_op` against networks, which will
     never be observational. If that changes, the circuit breaker
     wants every failure anyway.
   * The delete and prune paths.

   `has_pending` keeps failing closed on `OperationalError`.

6. **The flag is readable wherever a target row is.**
   `ClusterOperationTargetData` gains `observational: bool =
   False`, and its `external_view()` includes it.
   `ClusterOperationTargetProto` gains `bool observational = 7`.
   `_target_from_proto()` and the daemon's target-to-proto
   conversions carry it. The by-target listing returns
   `cluster_operations` headers rather than target rows, so its
   step-one query also selects `observational`, and each returned
   dict gets an `observational` key. That reaches the gRPC path as
   `optional bool observational` on `ClusterOperationData`, set
   only by `ListClusterOperationsForTarget` (unset everywhere
   else, rather than a misleading `false`), and the REST
   `/clusteroperations?target_*` response. This reports the
   stored value, not a fresh computation from the tasks. The
   stored value is what governs `has_pending`, and someone asking
   "why is or isn't this pending" needs that value.

7. **Delete the whole `create_cluster_operation_target` path.**
   That covers the `node_blob_op` call, `_set_last_cluster_operation()`
   and its tests and stub, the public, gRPC and direct trio, the
   `CreateClusterOperationTarget` RPC, its handler and counter, the
   `CreateClusterOperationTargetRequest` message, and the
   `mock_mariadb.py` mock. This is safe in a mixed-version roll.
   An old daemon still running the redundant `node_blob_op` write
   gets `UNIMPLEMENTED` from an upgraded gateway, its gRPC wrapper
   returns `False` and logs a warning (`mariadb.py:5254-5279`), and
   the caller ignores the return value. The row it was trying to
   write already exists, because the old daemon's own atomic
   enqueue wrote it. Deleting the path also means decisions 2 and
   6 have one fewer writer to thread the flag through.

8. **Inertness is pinned by a test, not promised.** A unit test
   iterates over every schema model in
   `shakenfist/schema/operations/` and asserts that none declares
   a non-empty `observational_tasks`. Its docstring says phase 3
   replaces it with the classification table. A second unit test
   uses a test-local model that marks one task observational, and
   proves the filter end to end through `enqueue_cluster_operation()`
   and the sqlite fixture. An all-observational op does not change
   `get_latest` and does not make `has_pending` true, while a
   mixed op does both. This is the master plan's phase 3 unit test
   brought forward, since the mechanism exists now.

9. **Schema docs ship with the schema.** The master plan put all
   documentation in phase 5. The operator-facing v3 schema and
   upgrade notes cannot wait that long, because an operator
   deploying phase 2 runs the migration. They go in this phase.
   The developer-guide explanation goes in phase 3 (see Scope).

## Step plan

Run the steps in order, in this worktree, one at a time.
Pre-commit's stash is repo-wide, so concurrent agents in one tree
can revert each other. Each step is one commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | **Delete the dead target-write path.** Read decision 7 and survey item 4 of `docs/plans/PLAN-cluster-op-visibility-phase-02-observational-schema.md` first. Remove: the post-enqueue `mariadb.create_cluster_operation_target(...)` call in `shakenfist/schema/operations/node_blob_op.py:96` (and the now-unused `time` import if nothing else needs it) -- `enqueue_cluster_operation()` already writes that row in-transaction because `blob_uuid` is in `target_fields`; `DatabaseBackedObjectWithOperations._set_last_cluster_operation()` (`shakenfist/baseobject.py:793`), its two tests in `shakenfist/tests/test_cluster_operation_targets.py` (around lines 54-80) and the stub in `shakenfist/tests/test_external_api.py:83` (check whether anything in that file calls the stub before deleting); the public `create_cluster_operation_target`, `_grpc_create_cluster_operation_target` and `_direct_create_cluster_operation_target` in `shakenfist/mariadb.py` (around 5475, 5254 and 4897); the `CreateClusterOperationTarget` RPC (`protos/database.proto:334`) and `CreateClusterOperationTargetRequest` message (around line 2458); the handler `CreateClusterOperationTarget` in `shakenfist/daemons/database/main.py:5457` and its entry in the counter list around line 6566; and the mock in `shakenfist/tests/mock_mariadb.py` (around line 977, plus its `_mariadb_create_cluster_operation_target` side-effect method). Tests in `shakenfist/tests/schema/operations/test_node_blob_op.py` that assert the redundant write happened should be changed to assert the target is passed to `create_and_enqueue_cluster_operation` instead. Regenerate stubs with `tox -e genprotos` (never run protoc directly). Finish with `grep -rn "create_cluster_operation_target\b\|CreateClusterOperationTarget\b\|_set_last_cluster_operation" shakenfist protos --include=*.py --include=*.proto \| grep -v _pb2` returning nothing, then `pre-commit run --all-files`. Commit subject: `Remove the redundant cluster op target writer.` |
| 2b | high | opus | none | **Schema v3 and its live migration test.** Read decisions 3 and 4 first. In `shakenfist/mariadb.py`: add `sa.Column('observational', sa.Boolean(), nullable=False, server_default=sa.false())` to `_get_cluster_operation_targets_table()` (line 1703) and change `idx_cot_target` there to `('target_object_type', 'target_uuid', 'observational')`; bump `CLUSTER_OPERATION_TARGETS_VERSION` to 3 (line 440); add a `current_ver < 3` block to `_ensure_cluster_operation_targets_schema()` (line 1754), modelled on the v2 block's style and comment density, issuing one `ALTER TABLE cluster_operation_targets ADD COLUMN IF NOT EXISTS observational BOOLEAN NOT NULL DEFAULT 0, DROP INDEX IF EXISTS idx_cot_target, ADD INDEX idx_cot_target (target_object_type, target_uuid, observational)` (no `IF NOT EXISTS` on the add; see decision 4) and a comment explaining the conservative default for rows written by older daemons mid-roll and why the index is replaced not added. The fresh-install path creates the v1 table from the *current* definition then runs every upgrade block, so check the v2 and v3 blocks are both no-ops against a table created from the new definition (that is what the `IF [NOT] EXISTS` clauses are for -- confirm the v2 block's `ADD CONSTRAINT` tolerance still holds). Update every sqlite fixture that hand-builds this table in `shakenfist/tests/` (`grep -rn "cluster_operation_targets" shakenfist/tests`) to include the column with the same type -- a fixture whose column types differ from production masked a real JOIN bug in phase 1b. Then write `shakenfist/tests/test_mariadb_cluster_operation_targets_live.py`, following `shakenfist/tests/test_mariadb_enum_columns_live.py` and the brief for step 2d of `docs/plans/PLAN-agent-operation-deadlines-phase-02-schema.md` (same `SF_MARIADB_TEST_DSN` gate, DESTRUCTIVE docstring, cleanup). It must: build the schema via `ensure_schema()`, rewind to v2 (drop the column, restore the two-column index, `_set_table_version(..., 2)`), insert a pre-v3 row, re-run `ensure_schema()` and assert version 3, the column exists, the old row reads `observational = 0`, and `idx_cot_target` covers three columns (`information_schema.statistics`); assert a further `ensure_schema()` is a no-op; and assert `EXPLAIN` of the exact SQL that the direct `get_latest` and `has_pending` functions emit **after step 2d** shows `key = idx_cot_target` (write the queries in the test to match decision 5's shape -- step 2d will make the production functions emit it). Mutation-test the migration assertions: break the `DEFAULT 0`, drop the `ADD INDEX`, leave the version un-bumped -- each must fail the test -- then restore. Run it against a disposable MariaDB: `docker run -d --rm --name sf-cot-live -e MARIADB_ROOT_PASSWORD=x -e MARIADB_DATABASE=sf -p 33306:3306 mariadb:10.11`, `SF_MARIADB_TEST_DSN='mysql+pymysql://root:x@127.0.0.1:33306/sf' stestr run --serial 'shakenfist\.tests\.test_mariadb_cluster_operation_targets_live\.'`, then repeat with `mariadb:11.8` (CI runs 10.11; sfcbr runs 11.x), and stop the containers. Report both runs' output. Commit subject: `Add an observational column to op targets.` |
| 2c | high | opus | none | **Write the flag.** Read decisions 1, 2 and 8 first. Add `is_observational(model_class, tasks) -> bool` to `shakenfist/schema/operations/util.py`: false if `model_class` is None, if `tasks` is empty, or if the model has no `observational_tasks`; otherwise true iff every entry of `tasks` (serialised task *names*, the form `metadata['tasks']` holds after `model_dump(mode='json')`) names a member of `model_class.observational_tasks`. Document the ClassVar convention (`observational_tasks: ClassVar[frozenset[model_tasks]]`, declared beside `target_fields`, absent means empty) in that function's docstring and in a comment beside `target_fields` in `node_inst_op.py` only -- do not add the ClassVar to any model. In `enqueue_cluster_operation()` compute it and pass `observational=` to `mariadb.create_and_enqueue_cluster_operation`. Thread an `observational: bool = False` keyword through the public (`mariadb.py:22539`), gRPC (`21941`) and direct (`21466`) `create_and_enqueue_cluster_operation`; the direct one sets it on every target insert. Add `bool observational = 8;` to `CreateAndEnqueueClusterOperationRequest` in `protos/database.proto` with a comment (one flag per op, false = mutating = what an older client sends), and read it in the `CreateAndEnqueueClusterOperation` handler (`shakenfist/daemons/database/main.py:6068`). `tox -e genprotos`. Unit tests: `is_observational` truth table (no model, no tasks, none declared, all observational, mixed); the direct writer stamps every target row; the gRPC wrapper sends the field; `enqueue_cluster_operation` passes what the helper returns; and the decision 8 inertness test -- import every module in `shakenfist/schema/operations/` that defines `model` and assert none has a non-empty `observational_tasks`, with a docstring saying phase 3 replaces it. Existing tests: `shakenfist/tests/test_enqueue_cluster_operation.py`, `test_mariadb_cluster_op_enqueue.py`. Single quotes, 120-column lines. `pre-commit run --all-files`. Commit subject: `Derive and store whether an op is observational.` |
| 2d | high | opus | none | **Read the flag, and filter on it.** Read decisions 5 and 6 first, and the decision 8 second test. In `shakenfist/mariadb.py`: add `table.c.observational == sa.false()` to the WHERE of `_direct_get_latest_cluster_operation_target` (around 5015) and `_direct_has_pending_cluster_operation_target` (around 5057), each with a short comment pointing at `docs/plans/PLAN-cluster-op-visibility.md`; keep `has_pending`'s fail-closed handler. Add a one-line comment to each deliberately unfiltered reader listed in decision 5 saying why. Add `observational: bool = Field(default=False)` to `ClusterOperationTargetData` (`shakenfist/schema/cluster_operation_target.py`) and to its `external_view()`; set it from the row in every direct reader that builds one; add `bool observational = 7;` to `ClusterOperationTargetProto` and carry it in `_target_from_proto()` (`mariadb.py:5282`) and in the daemon handlers that build that proto (`shakenfist/daemons/database/main.py` around 5600-5720). For the by-target listing (`_direct_list_cluster_operations_for_target`, `mariadb.py:21396`): select `observational` alongside `operation_uuid` in step one, and add an `observational` key to each returned dict; add `optional bool observational = N;` (next free tag) to `ClusterOperationData` (`protos/database.proto:2567`), set it in `ListClusterOperationsForTarget` (main.py:6018) only, and read it back in the gRPC client listing only when `HasField` says it was set; check the REST handler in `shakenfist/external_api/clusteroperation.py` passes the key through, and add a unit test that drives the endpoint through the **JSON body** path (not only the query string -- see the phase 1 lesson in the master plan's Bugs section). `tox -e genprotos`. The decision 8 behaviour test: with a test-local schema model declaring one observational task, enqueue a mutating op then an all-observational op against the same instance through `enqueue_cluster_operation()` on the sqlite fixture and assert `get_latest` still names the mutating op and `has_pending` reflects only the mutating op's state; then a mixed op does move `get_latest`. Mutation-test it: delete the new predicate from each of the two functions in turn and confirm the test fails, then restore. Re-run step 2b's live test, which now exercises the production queries. `pre-commit run --all-files`. Commit subject: `Hide observational ops from current op state.` |
| 2e | medium | sonnet | none | **Document v3.** Read decisions 3-7. Update `docs/operator_guide/database.md` (the table summary near line 695 and the `cluster_operation_targets` section near line 1066: the new column, its default, the widened index, and that rows live only until the op is hard-deleted about thirty seconds after going terminal, with the 7-day prune as a backstop -- correct anything there that still says "append-only history"); add a `#### cluster_operation_targets v2 to v3` entry to `docs/operator_guide/upgrades.md` beside the v1-to-v2 one (line 263) covering: run `sf-ctl ensure-mariadb-schema` before rolling daemons (gateways refuse to start below v3 via `verify_schema_versions`), the online cost (instant column add, small index rebuild), that older daemons mid-roll write mutating rows, and that the `CreateClusterOperationTarget` RPC is gone and an old node daemon logging its failure during the roll is expected and harmless. In `docs/developer_guide/database_internals.md` "Cluster Operation Tracking" (line 271) add two sentences: target rows carry an `observational` flag derived from the op's tasks, and `get_latest`/`has_pending` ignore observational rows -- and link the master plan for the rationale (the full developer guidance lands in phase 3). Do not edit `AGENTS.md`, `ARCHITECTURE.md` or `README.md`. Check that no fact about row lifetime or the column default is stated differently on two pages. `pre-commit run --all-files`. Commit subject: `Document cluster_operation_targets v3.` |
| 2f | n/a | management | n/a | After each step: review the diff against the master plan's checklist, read the changed files rather than the summary, run `pre-commit run --all-files`, and commit. After 2e, push, and watch the PR's CI, including the live-MariaDB job and the smoke suites (`ci-status shakenfist/shakenfist runs --branch cluster-op-visibility-phase-02-observational-schema`). Offer the PR; do not open it unasked. |

## Risks and mitigations

* **The migration takes a lock on a hot table during a deploy.**
  Every enqueue writes this table. *Mitigation:* the column add
  is instant on MariaDB 10.3 and later, and the index rebuild is
  in place over a table bounded by in-flight work (survey item
  3). The step 2b live test times the migration on a seeded
  table and reports the figure. The reviewer checks that figure
  before the PR is offered.
* **New gateway code queries a column the database does not
  have yet.** *Mitigation:* `verify_schema_versions()` refuses
  to start a gateway below the expected version, and the
  upgrade note in step 2e says to migrate first. The reviewer
  confirms that the v3 bump is in `EXPECTED_SCHEMA_VERSIONS`.
* **The filter goes live early.** If any model declared an
  observational task in this phase, `last_cluster_operation`,
  `is_okay()` gating and the delete-cancellation walk would
  change behaviour before phase 3's audit. *Mitigation:* the
  decision 8 inertness test fails the build if that happens.
* **Removing an RPC breaks a mixed-version roll.** *Mitigation:*
  decision 7 traces the only old caller and shows that its
  failure is ignored and its row already exists. Step 2e
  documents the expected warning, so operators do not chase it.
* **A sqlite fixture hides a type mismatch again.**
  *Mitigation:* step 2b updates the fixtures to the production
  column types. The live test runs the production queries
  against real MariaDB 10.11 and 11.x.
* **A filter test passes against a broken filter.**
  *Mitigation:* step 2d mutation-tests the predicate in both
  functions, and step 2b mutation-tests the migration
  assertions. The reviewer asks to see the failing runs.

## Definition of done

Each item is checkable from the tree or from a command:

- [ ] `grep -n "CLUSTER_OPERATION_TARGETS_VERSION = 3" shakenfist/mariadb.py`
      matches, and `EXPECTED_SCHEMA_VERSIONS` uses the constant.
- [ ] `grep -rn "create_cluster_operation_target\b\|CreateClusterOperationTarget\b\|_set_last_cluster_operation" shakenfist protos --include=*.py --include=*.proto | grep -v _pb2`
      prints nothing.
- [ ] `grep -rn "observational_tasks\s*[:=]" shakenfist/schema/operations/*_op.py`
      prints nothing, so no model is classified yet, and the
      inertness test that enforces this exists and passes.
- [ ] Deleting `observational == sa.false()` from either
      `_direct_get_latest_cluster_operation_target` or
      `_direct_has_pending_cluster_operation_target` makes the
      decision 8 behaviour test fail. This was demonstrated in
      step 2d, not assumed.
- [ ] `SF_MARIADB_TEST_DSN=... stestr run --serial 'shakenfist\.tests\.test_mariadb_cluster_operation_targets_live\.'`
      passes against `mariadb:10.11` and `mariadb:11.8`, and
      its `EXPLAIN` assertions name `idx_cot_target` for both
      filtered queries.
- [ ] The by-target endpoint returns an `observational` key per
      op, and a unit test drives it through the JSON body path.
- [ ] `pre-commit run --all-files` passes. `tox -e genprotos`
      leaves no diff.
- [ ] The descriptions of target-row lifetime and the column
      default in `docs/operator_guide/database.md`,
      `docs/operator_guide/upgrades.md` and
      `docs/developer_guide/database_internals.md` agree, and
      none of them still calls the table an append-only history.
- [ ] The CI live-MariaDB job and the smoke suites are green on
      the PR.
- [ ] The master plan's Execution table reads `In progress` for
      phase 2 with this file linked. That is done in this
      planning commit. The phase 3 planning run closes it out.

## Back brief

Before step 2a, back brief the operator on how you understand
this phase and the order of the steps, as the master plan
requires.

Before starting step 2c, stop and confirm decision 1 with the
operator: tasks declare observational, and an op is
observational only if every task is. Proposing it costs nothing.
Undoing it after phase 3 has classified the tasks would be
expensive, because the stored flags, the audit comments and the
tests are all built on it.
