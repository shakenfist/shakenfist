# Phase 3: cheaper hard deletes

Parent plan:
[PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md).

**Planning effort:** high, as the master plan specifies. This phase adds
a `DatabaseService` RPC that deletes rows from up to a dozen tables in
one transaction, decides in SQL which objects are still eligible, and
changes what the elected maintainer does with most of its sweep. A
wrong table list deletes too little and leaks rows; a wrong guard
deletes objects that are still in use.

Decisions continue the plan's single sequence: phase 2 used D7-D13, so
this phase starts at **D14**.

## Scope

In:

- A batch hard delete for the object types whose `hard_delete()` is
  nothing but row deletes keyed on the object: one RPC, one
  transaction per batch, with the state and age re-check moved into
  the transaction (D14).
- A test that pins the batch's table list to each of those classes'
  own `hard_delete()`, so the two cannot drift (D15).
- The sweep using the batch for those types, and the existing
  per-object path for the rest (D16, D17).
- A cap on objects hard deleted per pass, so this phase changes the
  cost of the sweep and not its rate (D18).
- The sweep running on every maintenance pass, which phase 2 meant it
  to and which it does not (D19).
- Metrics for the batch, documentation, release notes.
- After merge and deploy, 24 hours of sfcbr data, recorded by phase
  4's planning commit.

Out:

- **Any change to a class's own `hard_delete()`.** Every other caller
  (namespace cascades, superseded issuers and mapping rules, upload
  cleanup, CI) keeps exactly the code it has today. D15 explains why
  the batch does not replace it.
- **Instance, namespace and namespace claim cascades.** They keep
  their per-object semantics, and their unit tests must pass
  unchanged (the master plan's phase 3 constraint).
- **Draining sfcbr's backlog at a planned rate, and the regression
  guard.** Phase 4. This phase ships the knob (D18) with a default
  close to today's rate.
- **Load-budget entries** for the sweep's new RPC pair (D23).
- #3922 (errored objects are never hard deleted). It adds arrivals, so
  it still waits for phase 4.

## What the survey found

The survey read the master plan's phase 3 section against `develop` at
1b55ef2dae and against sfcbr's 24 hours on phase 2, ending
2026-10-23T09:10Z. The full numbers are in the master plan under "What
phase 2 measured". The findings below are the ones this phase is
planned from. False claims were corrected at their
source in this planning commit, so a later step need not redo it.

1. **The per-commit reading is all but confirmed.** Every delete that
   removes a row now costs 40 to 50 ms, whatever it removes:
   `DeleteObjectState` 40 ms, `DeleteClusterOperation` 41 ms,
   `DeleteBlobHashes` (several rows) 43 ms, `DeleteNamespace` 44 ms.
   Deletes that usually remove nothing cost what a read costs:
   `DeleteObjectMetadata` 3.2 ms, `DeleteClusterOperationError` 2.0 ms.
   Cost tracks "this statement wrote", not rows written. That is the
   shape of a redo log flush per commit under
   `innodb_flush_log_at_trx_commit=1`. Phase 1's 50 to 250 ms spread
   came from a different mix and a busier tier; the floor is the same.
   The batch's own latency against its size (3f) is the last word.

2. **An operation's hard delete is three such commits, about 135 ms.**
   `node_inst_op` costs 0.138 s, `net_op` 0.139 s, and the other
   operation types 0.13 to 0.15 s. They are 14 of the 29 types and
   about 70% of the backlog.

3. **Most types' hard deletes are nothing but row deletes keyed on the
   object.** The inventory, with the functions each calls before the
   base class's three (`delete_state`, `delete_object_metadata`,
   `delete_object_events`, `shakenfist/baseobject.py:759-763`):

   | Types | Class | Own deletes |
   |---|---|---|
   | the 14 `*_op` types | `BaseOperation.hard_delete` (`operations/baseoperation.py:268`) | `delete_cluster_operation_target`, `delete_cluster_operation_error`, `delete_cluster_operation` |
   | `agentoperation` | `agentoperation.py:553` | `delete_agent_operation_attributes`, `delete_agent_operation`, `remove_all_references_from`, then `BaseOperation`'s three |
   | `artifact` | `artifact.py:747` | `delete_all_artifact_indexes`, `delete_artifact_attributes`, `delete_artifact`, then `delete_cluster_operation_targets_for_object` (`baseobject.py:835`) |
   | `network` | `network/network.py:813` | `delete_network_attributes`, `delete_network`, then `delete_cluster_operation_targets_for_object` |
   | `blob` | `blob.py:1029` | `delete_blob_hashes`, `delete_blob_transfers_for_blob`, `delete_blob_attributes`, `delete_blob` |
   | `dhcp` | `managed_executables/dnsmasq.py:125` | `delete_dnsmasq` |
   | `interface` | `network/interface.py:302` | `delete_network_interface_attributes`, `delete_network_interface` |
   | `ipam` | `ipam.py:428` | `delete_reservations_for_ipam`, `delete_ipam` (an in-memory IPAM returns early; one read from the database never is) |
   | `namespace_key`, `trusted_issuer`, `mapping_rule` | `namespace_key.py:424`, `trusted_issuer.py:292`, `mapping_rule.py:605` | their attributes delete, then their static delete |
   | `upload` | `upload.py:149` | `delete_upload` |

   None of them reads anything from the hydrated object but its uuid
   and type. None takes a lock or touches a file. The table is the
   management session's reading of a sub-agent inventory, spot-checked
   at the line numbers given; brief 3c's parity test is what makes it
   authoritative.

4. **Four types are not pure deletes.**
   - `instance` (`instance.py:1740`) first runs `_release_placement()`,
     a capacity transaction that reads the hydrated object's claim.
   - `namespace` (`namespace.py:344`) cascades to its keys, mapping
     rules and claims, and rewrites other namespaces' trust attributes.
   - `namespace_claim` (`namespace_claim.py:381`) returns capacity in a
     guarded transaction and raises `WriteException` on failure.
   - `node` (`node.py:881`) deliberately does nothing.

5. **Each delete is its own commit today.** Every `_direct_delete_*`
   opens `engine.connect()`, runs one statement and commits
   (`mariadb.py:4311`, `5501`, `21788`). Multi-statement transactions
   use `with engine.begin()`, wrapped in `_retry_transaction()`
   (`mariadb.py:26619`); `_direct_delete_orphaned_object_states`
   (`mariadb.py:25081`) is the closest precedent, a bulk delete on
   `object_states` in one transaction. No existing RPC deletes rows
   from several tables for several objects at once.

6. **`DELETE ... RETURNING` is available.** Shaken Fist requires
   MariaDB 10.11 or later and refuses MySQL
   (`docs/operator_guide/database.md`, "MariaDB compatibility
   requirements"). SQLAlchemy 2.1.5's MariaDB dialect reports
   `delete_returning` and compiles `DELETE FROM object_states WHERE
   ... RETURNING object_states.object_uuid`. MariaDB has no `UPDATE
   ... RETURNING` (the comment at `mariadb.py:27669`), which does not
   matter here.

7. **Every table the batch deletes from is keyed for it.**
   `event_objects`' primary key is `(object_type, object_uuid,
   event_uuid)` (`mariadb.py:2841`), so an `object_uuid IN (...)`
   delete for one type is an index range per uuid. The other tables
   are keyed on the uuid column the `_direct_*` function already uses.

8. **The sweep runs every other maintenance pass, not every minute.**
   sf-cluster logs a pass every 2 minutes 8 seconds on sfcbr, each
   using its full 20 seconds. The maintenance loop runs scheduled jobs
   when `now - last_loop_run >= 60` (`daemons/cluster/main.py`, in
   `_run_inner`). `schedule` sets a job's next run to its interval
   after the job *finishes*. A 20 second sweep started at T is next due
   at T+80, misses the pass at T+60, and runs at T+120. Phase 2's D10
   meant every minute; the sweep has half the duty cycle it was given.
   Short jobs registered `every(1).minutes` are unaffected, because
   they finish within the loop's poll interval.

9. **Reads are cheap in latency but not in load.** Phase 1 dropped
   "skip the hydration" as a candidate because reads cost 2 ms. They
   do, but each is a database request, and sf-cluster's
   `GetObjectState` is at 4.01/s, up from 2.54/s, against a budget of 1.986/s plus
   0.016/s per instance, with a tolerance of twice that. Most of the
   excess is the sweep's per-object re-checks. A faster per-object
   sweep would push that pair, and the per-object `Delete*` pairs,
   further over. A batch that does the re-check in SQL makes none of
   those requests.

10. **The keyset walk is healthy.** Phase 2's top risk, the paged read
    choosing a sort over the primary key, did not happen: a page costs
    2 to 12 ms for every type. The hourly count is the slow read, at
    3 s for `node_inst_op`, which is acceptable once an hour.

11. **There are 29 object types, not 28.** `OBJECT_NAMES_TO_CLASSES`
    has had 29 entries since before this plan began; the master plan,
    phase 1's and phase 2's plans, two comments in
    `scheduled_tasks.py` and a test docstring say 28. The plans are
    corrected in this planning commit; the code comments are corrected
    by step 3d, which edits that file anyway.

12. **An equal share leaves the two busiest types growing.** Every
    type with a backlog got about 5,100 objects a day. `namespace`
    took 8,705 arrivals and `node_inst_op` 7,441, so their backlogs
    grew by 3,594 and 2,356. `net_op` held level. Fixing the cadence
    alone would double the share. D18's default sets the floor.

## Decisions

### D14 -- Batch the pure-delete types in one transaction, re-checked in SQL

Add one RPC, `HardDeleteObjects(object_type, object_uuids,
state_values, updated_before)`, returning the uuids it deleted. In one
transaction it:

1. deletes the `object_states` rows for those uuids of that type whose
   state is one of `state_values` and whose `update_time` is older than
   `updated_before`, `RETURNING object_uuid`;
2. deletes, for exactly the returned uuids, every other row that
   type's `hard_delete()` removes (D15's table list), including the
   `event_objects` rows;
3. commits once.

The first statement is the eligibility check that the per-object path
does with `from_db()` and a state read, made atomic with the delete:
an object which left its final state, or was touched again, after the
sweep listed it is simply not returned, and nothing else of it is
deleted. It is also the guarded DML statement a transaction here must
open with (see `_direct_release_instance_placement`'s comment), so no
plain `SELECT` runs first.

This reverses the master plan's "dropping the hydration is no longer
a candidate" for these types, for survey finding 9's reason: the reads
are cheap per call and expensive in aggregate. On the batch path the
sweep makes one request per batch instead of six to ten per object.

The server rejects more than 1,000 uuids in one call, so a caller
mistake cannot build an unbounded transaction.

### D15 -- The table list lives once, beside the SQL, and a test pins it to the classes

The batch needs, for each batchable type, the statements its
`hard_delete()` runs. Two ways to keep one copy:

- **Rewrite every `hard_delete()` onto the batch delete**, so each
  class's own deletes become one call with a list of one. One copy,
  and every caller's hard delete becomes one commit. But it changes
  every caller at once, including the namespace cascade and its tests,
  which the master plan says must pass unchanged, and it makes this
  phase's measurement a measurement of everything.
- **Keep `hard_delete()` as it is and declare the batch's list in
  `mariadb.py`, beside the SQL**, with a unit test that runs each
  batchable class's real `hard_delete()` against recording mocks and
  asserts that the `mariadb` delete functions it calls are exactly the
  ones the list says the batch replaces. Two copies, but a drift in
  either fails the build.

**The second.** The list maps each type to the names of the public
`mariadb` delete functions it replaces, and each name to one SQL
statement that mirrors that function's `_direct_` implementation. A
live test then checks each statement deletes what its function
deletes. Moving `hard_delete()` itself onto the batch is recorded as
future work in the master plan, to be taken once the batch has run in
production.

### D16 -- Instance, namespace, namespace claim and node stay per object

Survey finding 4. Each has a side effect the batch cannot make
without reimplementing it, and the master plan keeps those cascades'
semantics. `node` is a deliberate no-op and stays one. Together they
are three of sfcbr's 29 backlogs (`namespace` 72,768, `instance` and
`namespace_claim` near zero), and `namespace` at 0.25 s an object fits
in the time the batch frees.

### D17 -- A turn takes up to one batch of one type

The drain keeps phase 2's round robin and rotation. A turn now takes
up to `DELETED_OBJECT_BATCH_SIZE` entries from the next type with
work: a batchable type deletes them in one call, a per-object type
deletes them one at a time with the time budget checked between
objects. Fairness stays by object count, so a per-object type is not
starved by types whose turn is a single cheap call. The batch size
defaults to 25, small enough that an operation batch is a few hundred
rows and a turn of namespaces fits the time budget.

### D18 -- Throttle by objects per pass; this phase changes cost, not rate

A batch of 25 operations is one commit instead of 75. Inside phase 2's
20 second budget the sweep could hard delete thousands of objects a
minute, and would, on the first pass after deploy: sfcbr's 1.75M
backlog in hours, as a write load on labqnap nobody has measured.
That is phase 4's drain, which the master plan wants throttled and
watched with `sf-db-load`.

So a new option, `DELETED_OBJECT_SWEEP_MAX_OBJECTS`, caps the entries
a pass takes, with the time budget kept as a ceiling. It defaults to
**100**, about what phase 2 would manage at its intended cadence
(survey finding 8: about 3,200 an hour at every other pass, so 6,000
an hour, 100 a minute, at every pass). That is nearly four times
sfcbr's arrival rate of about 26 a minute, so the backlog still falls.
It also gives each of the 12 types still holding a backlog about
12,000 a day, above the busiest type's arrivals (survey finding 12:
`namespace` takes 8,705 a day). And phase 3's 24 hours then measure
the same rate at a lower cost, which is the comparison worth having.
Phase 4 raises it.

This is the decision most likely to be argued with: it ships a batch
path and then holds it to roughly the old throughput. The alternative,
letting the time budget govern, is fine for a cluster with no backlog
and is exactly the unmeasured flat-out drain the master plan warned
against for one with sfcbr's.

### D19 -- The sweep runs on every maintenance pass

Register the sweep with a 30 second interval, so that whatever its
duration it is due again by the next 60 second pass, and say why in
the registration's comment. The loop's own 60 second gate still sets
the cadence. Other jobs keep their intervals: they are short enough
not to slip.

### D20 -- The audit event stays, one per object

Each object the batch deletes gets the same `EVENT_TYPE_AUDIT` 'hard
deleted object' event the base `hard_delete()` writes, through
`eventlog.add_event()` with the type and uuid. That call only
enqueues to the local spool (`eventlog.py:19`); the spool's drainer
ships events in batches through `RecordEventBatch`, so it adds no
synchronous request.

### D21 -- Metrics: keep the per-entry series, add a per-batch one

`cluster_deleted_object_seconds` keeps counting entries by type and
outcome, so phase 2's throughput queries and dashboards still work: a
batch observes each of its entries with an equal share of the batch's
wall time. A new outcome, `skipped`, counts batch entries the guard
did not return (gone, no longer final, or too young; the batch cannot
tell which). A new histogram, `cluster_deleted_object_batch_seconds`
by `object_type`, observes each batch call, which with the entry
counts gives batch latency against batch size (3f).

### D22 -- An older sf-database falls back to the per-object path

A new sf-cluster calling an sf-database without the RPC gets
`UNIMPLEMENTED`. The client wrapper raises a distinct exception for it,
as the coalescing wrappers do with `CoalescingUnavailable`, and the
sweep hard deletes that turn's entries one at a time as phase 2 does,
logging once per pass. The operator guide already says to upgrade
`sf-database` first.

### D23 -- No load-budget entries in this phase

The batch removes most of the sweep's per-object pairs, the ones
phase 2's execution notes found above the unbudgeted ceiling, and
adds one pair, `HardDeleteObjects` from sf-cluster, at most a few
calls a minute at D18's default. A budget entry should be derived
from a measured rate, and the rate is set by D18's cap, which phase 4
moves. Brief 3f checks every sf-cluster pair against the budget after
deploy, and phase 4 adds the entries once it has settled the rate.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | low | sonnet | none | Run the sweep on every maintenance pass (D19). See brief 3a. |
| 3b | high | opus | none | Add `HardDeleteObjects` (D14, D15's list). See brief 3b. |
| 3c | high | opus | none | Pin the list to the classes (D15). See brief 3c. |
| 3d | high | opus | none | Batch the sweep, with its throttle (D16-D18, D20-D22). See brief 3d. |
| 3e | low | sonnet | none | Document the changes. See brief 3e. |
| 3f | — | management session | — | After merge and deploy, measure and record. See brief 3f. |

Run the steps in order, one at a time, in this worktree. 3a and 3d
both touch sf-cluster's scheduling and tests, 3b and 3c both edit
`mariadb.py`'s neighbourhood, and two agents in one tree can undo each
other through pre-commit's stash.

### Brief 3a: run the sweep on every maintenance pass

In `shakenfist/daemons/cluster/main.py`, `_run_inner()` registers
`scheduled_tasks.per_deleted_object_checks` with
`schedule.every(1).minutes`. Change it to `schedule.every(30).seconds`
and rewrite the comment above it to say why: the maintenance loop runs
due jobs once `now - last_loop_run >= 60`, and `schedule` sets a
job's next run to its interval after the job *finishes*, so a job
which runs for its whole `DELETED_OBJECT_SWEEP_SECONDS` budget and is
registered every minute is not yet due at the next pass and runs only
every other one. On sfcbr that halved the sweep's duty cycle. Keep the
existing reasons in the comment (no thread; the watchdog is petted
between jobs).

Add a unit test that drives the real `schedule` library through two
consecutive passes 60 seconds apart with a sweep that takes 20
seconds, using a patched clock (`schedule` reads
`datetime.datetime.now`; check the installed version's source for
exactly what to patch), and asserts the sweep ran in both. Prefer
registering the job the way `_run_inner()` does by factoring the
registration of the deleted-object jobs into a small function the
test can call, rather than copying the interval into the test. Show
the test fails with `every(1).minutes` (mutation) and passes with the
change.

Commit subject: "Run the deleted-object sweep every pass."

### Brief 3b: add `HardDeleteObjects`

Use the `add-grpc-service` skill's pattern for a new
`DatabaseService` RPC, and read `CountObjectsByState` (added by
phase 2) as the nearest worked example in each file.

`protos/database.proto`: `rpc HardDeleteObjects
(HardDeleteObjectsRequest) returns (HardDeleteObjectsReply)`, with
`HardDeleteObjectsRequest{ObjectType object_type = 1; repeated string
object_uuids = 2; repeated string state_values = 3; double
updated_before = 4;}` and `HardDeleteObjectsReply{repeated string
deleted_uuids = 1;}`. Use leading comments, one per field, so the
generated `.pyi` attaches them to the right fields. Regenerate with
`tox -e genprotos`.

`shakenfist/mariadb.py`, the three-layer trio
`_direct_hard_delete_objects`, `_grpc_hard_delete_objects` and
public `hard_delete_objects(object_type, object_uuids, state_values,
updated_before) -> Optional[list[str]]`:

- The public function raises `ValueError` for an empty
  `state_values`, an `updated_before` of 0 or None, more than
  `HARD_DELETE_OBJECTS_MAX` (1,000) uuids, or a type with no entry in
  the list below. An empty uuid list returns `[]` without a call.
- The list: a module-level mapping from `ObjectType` to the ordered
  names of the public `mariadb` delete functions that type's
  `hard_delete()` calls, *excluding* the base three, and a second
  mapping from each function name to a builder of one SQLAlchemy
  `DELETE` statement for a list of uuids. Each builder mirrors the
  WHERE clause of that function's `_direct_` implementation exactly,
  with `IN` in place of `=`, and cites it. Survey finding 3's table is
  the starting point; read every `_direct_` function you map rather
  than trusting the table. Types: the 14 `*_op` types,
  `agentoperation`, `artifact`, `network`, `blob`, `dhcp`,
  `interface`, `ipam`, `namespace_key`, `trusted_issuer`,
  `mapping_rule`, `upload`. Not `instance`, `namespace`,
  `namespace_claim` or `node`.
- The direct implementation, inside `_retry_transaction()` and one
  `with engine.begin() as conn`: first `DELETE FROM object_states
  WHERE object_type = :t AND object_uuid IN :uuids AND state_value IN
  :states AND update_time < :before RETURNING object_uuid`; if nothing
  came back, return `[]`; otherwise run every mapped statement for the
  returned uuids, then the `object_metadata` and `event_objects`
  deletes (mirroring `_direct_delete_object_metadata` and
  `_direct_delete_object_events`), and return the returned uuids. No
  plain `SELECT` before the first statement (see the comment at
  `_direct_release_instance_placement`). An `OperationalError` that
  `_retry_transaction()` does not retry is logged and returns None.
- The gRPC wrapper goes through `_grpc_call()`, so it is timed and
  retried like any other call; it returns None on `grpc.RpcError`,
  except that `UNIMPLEMENTED` raises a new
  `exceptions.HardDeleteBatchUnavailable`, so the caller can fall back
  (D22). Follow `exceptions.CoalescingUnavailable` and the comment
  above the coalescing wrappers (`mariadb.py:22684`), which is the
  precedent for an RPC-specific exception on `UNIMPLEMENTED`.
- Servicer in `shakenfist/daemons/database/main.py`, with its Monitor
  counter, validating the request the way the public function does.

Tests: unit tests for validation, routing and the gRPC wrapper's
error mapping, mirroring phase 2's `test_mariadb_objects_by_state_page.py`.
Live tests (`SF_MARIADB_TEST_DSN`, mirroring
`test_mariadb_objects_by_state_page_live.py`), which phase 2 ran
locally against a throwaway `mariadb:11.8` container with PyMySQL (see
phase 2's execution notes for the setup):

- for each batchable type, rows created in every mapped table for
  three uuids, of which one is eligible, one is in a non-final state,
  and one is final but too young; only the eligible one's rows go,
  across every table, and the reply names only it;
- a uuid shared with another object type is not touched in that
  type's tables;
- a statement that fails part way (inject one by patching a builder)
  leaves every table as it was: the transaction rolled back;
- 1,001 uuids is refused.

Commit subject: "Add a batch hard delete RPC."

### Brief 3c: pin the list to the classes

A unit test module, `shakenfist/tests/test_hard_delete_batch_parity.py`.
For each type in 3b's mapping, build an instance of that type's class
without the database (look at how existing unit tests construct these
objects; most have a helper or accept a static-values dict), patch
every public `mariadb.delete_*`, `mariadb.remove_all_references_from`
and `eventlog`/`add_event` path the classes reach with recording
mocks, call the real `hard_delete()`, and assert:

- the ordered list of delete functions called, minus the base three,
  equals the mapping's list for that type;
- the base three were called;
- nothing outside the allowed set was called (a new side effect in a
  class's `hard_delete()` must fail this test, not pass silently).

Also assert that every type in `OBJECT_NAMES_TO_CLASSES` is either in
the mapping or in an explicit per-object set (`instance`, `namespace`,
`namespace_claim`, `node`) in the same test module, so a new object
type cannot be silently left out of both.

Mutation-test it with the `mutation-check` skill: drop one function
from one type's mapping; add one delete call to one class's
`hard_delete()`; move a type from the mapping to nowhere. Each must
fail the right test. Report the script's path.

Commit subject: "Pin the batch hard delete to each class."

### Brief 3d: batch the sweep

In `shakenfist/daemons/cluster/scheduled_tasks.py`:

- `_drain_deleted_object_work()` (line 1139) takes turns as D17 says.
  A batchable type (one in 3b's mapping) pops up to
  `config.DELETED_OBJECT_BATCH_SIZE` uuids and calls
  `mariadb.hard_delete_objects()` with `FINAL_OBJECT_STATES` and
  `time.time() - _deleted_object_delay(objtype)`, inside
  `mariadb.rpc_timing_scope('per_deleted_object', objtype)`. For each
  returned uuid, `eventlog.add_event(EVENT_TYPE_AUDIT, objtype, uuid,
  'hard deleted object')` (D20). A per-object type pops up to the same
  number and runs the existing `_hard_delete_deleted_object()` on each,
  checking the deadline between them.
- The pass stops at whichever comes first: the time budget, or
  `config.DELETED_OBJECT_SWEEP_MAX_OBJECTS` entries taken (D18). A turn
  never takes more than the cap has left.
- Outcomes and metrics as D21: each batch entry is observed in
  `CLUSTER_DELETED_OBJECT_SECONDS` with outcome `hard_deleted` or the
  new `skipped`, each with an equal share of the batch's wall time;
  `CLUSTER_DELETED_OBJECT_BATCH_SECONDS` (new, by `object_type`)
  observes the call. Add `skipped` to `DELETED_OBJECT_OUTCOMES`, so the
  pass log line gains the field.
- Failure handling. A None reply (a failed call) counts the whole
  batch `failed` and drops it: the next walk of that type, after its
  recheck, finds them again. `DatabaseUnavailable` puts the batch back
  at the front of its type's work and ends the pass, as the fill does.
  `HardDeleteBatchUnavailable` (D22) runs that turn per object and logs a
  warning once per pass.
- Correct the two "28 object types" comments (lines 1019 and 1052) to
  29, and the test docstring that repeats it.

In `shakenfist/config.py`, beside `DELETED_OBJECT_SWEEP_SECONDS`:
`DELETED_OBJECT_BATCH_SIZE: int = Field(25, ge=1, le=1000, ...)` and
`DELETED_OBJECT_SWEEP_MAX_OBJECTS: int = Field(100, ge=1, ...)`, with
descriptions an operator can act on.

Tests in `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py`,
alongside phase 2's `DeletedObjectSweepPagedWorkTestCase`:

- a batchable type's turn makes one `hard_delete_objects` call with up
  to the batch size, no `from_db()`, and one audit event per returned
  uuid;
- a per-object type's turn still goes through `from_db()` and
  `hard_delete()`, one at a time;
- `skipped` and `hard_deleted` are counted from the reply;
- the per-pass cap stops the pass, and a turn takes only what the cap
  has left;
- the rotation still serves a per-object type between batch turns (a
  fairness test in the spirit of phase 2's
  `DeletedObjectSweepFairnessTestCase`);
- None, `DatabaseUnavailable` and `HardDeleteBatchUnavailable` each behave
  as above.

Mutation-test with the `mutation-check` skill (ignore the cap; batch a
per-object type; skip the audit event; drop the requeue on
`DatabaseUnavailable`) and report the script's path.

Commit subject: "Hard delete most types in batches."

### Brief 3e: document the changes

- `docs/operator_guide/database.md`, the deleted-object sweep section:
  bullets for `DELETED_OBJECT_BATCH_SIZE` and
  `DELETED_OBJECT_SWEEP_MAX_OBJECTS` beside
  `DELETED_OBJECT_SWEEP_SECONDS`, saying the cap is the drain-rate
  knob and the time budget a ceiling; which types are deleted in
  batches and which one at a time, and why in one sentence; the
  `skipped` outcome and `cluster_deleted_object_batch_seconds` in the
  metrics list; and that a pass now runs every minute.
- `docs/release_notes/v07-v08.md`: extend the deleted-object sweep
  entry.
- `docs/developer_guide/database_internals.md`: one paragraph on the
  batch RPC, the mapping in `mariadb.py`, and the parity test that
  must be updated with any change to a batchable class's
  `hard_delete()`.

No plan, phase or decision citations outside `docs/plans`.

Commit subject: "Document batched hard deletes."

### Brief 3f: measure (management session, after deploy)

Once sfcbr runs a build containing this phase
(`cluster_deleted_object_batch_seconds_count` exists), wait 24 hours
and query the 33fl Prometheus as phase 2's brief 2g did (the script
is kept in this phase's execution notes), adding:

```
# Batch latency and size, per type
sum by (object_type) (rate(cluster_deleted_object_batch_seconds_sum[24h]))
  / sum by (object_type) (rate(cluster_deleted_object_batch_seconds_count[24h]))
sum by (object_type) (increase(cluster_deleted_object_seconds_count{outcome=~"hard_deleted|skipped"}[24h]))
  / sum by (object_type) (increase(cluster_deleted_object_batch_seconds_count[24h]))

# What the tier paid, against the 24 hours before the deploy
sum(rate(database_requests_total[24h]))
sum(rate(mysql_global_status_commands_total{command="commit"}[24h]))
sort_desc(sum by (operation) (rate(database_requests_total{caller_daemon="cluster"}[24h])))
```

The per-commit reading holds if a batch of n costs about one commit
plus a small per-row term, rather than n times an operation's 135 ms.
Phase 4's planning commit records the results under "What phase 3
measured", with the per-object cost per type against phases 1 and 2,
and sets phase 4's drain rate from them.

## Risks and mitigations

- **The table list drifts from a class's `hard_delete()`.** A batch
  that misses a table leaks rows forever; one with an extra table
  deletes rows it should not. Mitigation: 3c's parity test, which
  fails on either, and its check that every type is classified.
- **The guard is wrong and deletes a live object.** Mitigation: the
  guard is the first statement, and 3b's live test puts a non-final
  and a too-young object in the same batch as an eligible one and
  checks all of their tables.
- **A batch's transaction is large or contends.** `event_objects` is
  the widest table; a blob had 3.4 s of event-reference deletes in
  phase 1. Mitigation: the default batch is 25, the server refuses
  more than 1,000, `_retry_transaction()` retries a deadlock, and 3f
  reads batch latency per type. Blobs number in the tens.
- **CI runs MariaDB 10.11 and sfcbr 11.8.** `DELETE ... RETURNING`
  exists in both, but the live tests run locally on 11.8 and in CI on
  10.11. Mitigation: 3b's live tests run in both places before merge;
  the PR records the CI run.
- **Snapshot isolation.** MariaDB 11.6.2 and later can raise
  `ER_CHECKREAD` for a transaction that reads before it writes.
  Mitigation: the transaction opens with the guarded `DELETE`, as the
  placement transactions do.
- **D18's default is too low or too high for some cluster.** 100 a
  minute is 144k a day, against sfcbr's 40k arrivals. Mitigation: it
  is an option, documented as the knob, and phase 4 sets sfcbr's from
  measurement.
- **The batch path's events are lost.** The audit events go through
  the same spool as every other event, with its existing drop counter.
  Losing one loses an audit line, not data.
- **Mixed versions during the deploy.** Mitigation: D22's fallback,
  tested in 3d.

## Definition of done

1. `pre-commit run --all-files` passes.
2. 3a's cadence test fails with `every(1).minutes` and passes with the
   change.
3. 3b's live tests pass against MariaDB 11.8 locally and 10.11 in CI,
   including the rollback test.
4. 3c's parity test fails for each of its three mutations, and every
   type in `OBJECT_NAMES_TO_CLASSES` is classified.
5. No class's `hard_delete()` changed, and their tests are unchanged:
   `git diff origin/develop --stat -- shakenfist/instance.py
   shakenfist/namespace.py shakenfist/namespace_claim.py
   shakenfist/node.py shakenfist/baseobject.py shakenfist/operations/
   shakenfist/blob.py shakenfist/artifact.py shakenfist/network/
   shakenfist/ipam.py shakenfist/namespace_key.py
   shakenfist/trusted_issuer.py shakenfist/mapping_rule.py
   shakenfist/upload.py shakenfist/agentoperation.py
   shakenfist/managed_executables/` is empty.
6. 3d's tests fail for each of its four mutations.
7. `tox -e genprotos` leaves no diff.
8. The docs name both new options, the `skipped` outcome and the batch
   histogram, and `grep -rn "phase [0-9]\|D1[4-9]\|D2[0-3]" docs
   --include=*.md | grep -v docs/plans` finds nothing this phase added.
9. After deploy, 3f's numbers are recorded by phase 4's planning
   commit.

## Execution notes

## Back brief

Before starting, confirm with Michael:

- **D18's default of 100 objects a pass.** It holds the drain near
  today's rate on purpose. If he would rather phase 3 start the drain,
  the number to agree is the default, and 3f then measures load at
  that rate instead.
- **D15's choice to leave `hard_delete()` alone**, and so to carry two
  copies of each batchable type's table list with a test between them.
- Whether this session may push and open the pull request once the
  definition of done passes.
- The live tests run locally against a throwaway MariaDB container, as
  in phase 2.
