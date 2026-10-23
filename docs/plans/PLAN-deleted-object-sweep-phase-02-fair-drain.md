# Phase 2: a fair, bounded drain

Parent plan:
[PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md).

**Planning effort:** high, as the master plan specifies. This phase
changes how the elected maintainer spends its loop, adds a field to a
`DatabaseService` RPC that a complement-set caller depends on, and
adds a second RPC. Each of those is cheap to get subtly wrong.

Decisions continue the plan's single sequence: phase 1 used D1-D6, so
this phase starts at **D7**.

## Scope

In:

- Per-type work, drained round robin across types, so a large backlog
  in one type cannot starve the others (mission 2).
- Bounded fills: one page per type, read in primary key order with a
  keyset cursor, through new optional fields on `GetObjectsByState`.
- A type is refilled when its own work runs out, so the sweep no
  longer depends on a restart to see new arrivals.
- The sweep runs every minute, with its budget set by a new
  configuration option (open question 3).
- A per-type backlog gauge read from the database, replacing phase 1's
  queue depth gauge, which stops meaning "backlog" once the sweep
  pages.
- `DeleteObjectEvents` observed by phase 1's timing scope.
- Operator documentation and release notes.
- After merge and deploy, 24 hours of sfcbr data, recorded by phase
  3's planning commit.

Out:

- **Any change to what one hard delete does.** The per-entry body
  (hydrate, re-check, `hard_delete()`) moves into a helper unchanged.
  Making it cheaper is phase 3, and changing it here would muddy the
  measurement phase 3 is planned from.
- Batch delete RPCs. Phase 3.
- Throttling or tuning the drain of sfcbr's backlog. Phase 4. This
  phase sets a default budget and makes it configurable. It does not
  try to drain at a planned rate.
- Paging any other `GetObjectsByState` caller. [Bound gRPC reply
  sizes](PLAN-grpc-bounded-replies.md) owns the rest.
- #4399. Phase 1 showed it is not the sweep (see the master plan's
  "What phase 1 measured").

## What the survey found

References are to `develop` at ce8d0641b7.

1. **Phase 1 is complete and its numbers are in the master plan.**
   Merge 6537966c00 (#4572, which carried #4573) is on `develop`, the
   phase 1 worktree and branches are gone, and the metrics answer from
   sfcbr's leader. Brief 1d's queries were run over 24 hours ending
   2026-10-20T21:23Z and recorded under "What phase 1 measured".
   Phase 1's definition of done items 1-6 were checked when it
   landed, and item 7 is met by this planning commit.
2. **The measured cost changes what phase 2 can deliver.** The sweep
   gets 960 seconds a day and spent 974. At about 0.4 seconds an
   object, keeping up with 40k arrivals a day would take about four
   and a half hours. Fairness spreads the budget across types, but it
   cannot make the sweep keep up. Phase 2 should make the sweep fair,
   bounded and correctly sized. Phase 3 has to make it cheap.
3. **Nothing pages `GetObjectsByState` yet.** [Bound gRPC reply
   sizes](PLAN-grpc-bounded-replies.md) is `Not started` (0 of 7). Its
   worked brief already names the shape: `page_size` and `after_uuid`
   on the request, `more_available` on the reply, keyset on
   `object_uuid` (`PLAN-grpc-bounded-replies.md:441-452`). The request
   today has `object_type`, `state_values` and `updated_before`
   (`protos/database.proto:796-805`).
4. **One caller of the unpaged read is a complement set.** The
   servicer's docstring (`shakenfist/daemons/database/main.py:734-760`)
   explains that `get_active_blob_uuids()` (`shakenfist/mariadb.py:7693`)
   treats every blob *not* in the reply as deletable. A paged or
   truncated reply reaching that caller would delete blobs. The
   unpaged path must keep sending `page_size` 0, and must never see a
   page.
5. **Oldest first is not index-served.** `object_states` has a primary
   key on `(object_type, object_uuid)`, plus `idx_object_states_type_state`
   on `(object_type, state_value)` and `idx_object_states_uuid`
   (`shakenfist/mariadb.py:1407-1447`). No index includes
   `update_time`, so `ORDER BY update_time LIMIT n` would sort a type's
   whole final-state range (639k rows for `node_inst_op`) for every
   page. A keyset on `object_uuid` within one `object_type` walks the
   primary key.
6. **There is a pattern for adding an index, but this phase does not
   need it.** `object_states` is versioned, and v3 added an index
   online with `CREATE INDEX IF NOT EXISTS` (`mariadb.py:1671-1689`).
   It is recorded here in case D8 is overturned.
7. **The maintenance loop can afford a larger budget.**
   `_run_due_scheduled_jobs()` pets the watchdog before every job
   (`shakenfist/daemons/cluster/main.py:990-1019`). sf-cluster runs
   with `WatchdogSec=300s` (`deploy/collection/roles/node/templates/sf.service:73`).
   The loop runs a maintenance pass when 60 seconds have passed since
   the last one (`main.py:1138`). A 20 second job every minute
   is well inside the watchdog window. The one cost is that the other
   one-minute jobs (`log_cluster_queue_lengths`,
   `reap_stuck_cluster_operation_jobs`) run up to 20 seconds later.
8. **`DeleteObjectEvents` bypasses `_grpc_call()`.**
   `_grpc_delete_object_events()` (`mariadb.py:6508-6533`) calls
   `stub.DeleteObjectEvents(request, timeout=30.0)` directly and only
   logs a failure. That is deliberate: a failed event cleanup must not
   fail a hard delete, and orphans are pruned daily. It is also why
   phase 1's scope cannot see the largest cost outside the timed RPCs.
9. **There is no count RPC, and the pattern for one is known.**
   Nothing in `mariadb.py` counts `object_states` rows. The
   `add-grpc-service` skill (`.claude/skills/add-grpc-service/`)
   covers the proto, stubs, servicer, Monitor counter and client
   layers. Monitor counters are registered in the list at
   `daemons/database/main.py:6416`.
10. **The metrics phase 1 added are unreleased.** No release tag
    contains 6537966c00 (`v0.8.0-rc4` is from 2024), and
    `docs/release_notes/v07-v08.md` is the in-progress notes. So
    replacing `cluster_deleted_object_queue_depth` breaks nobody.
11. **About 75 references in the tests use the FIFO.**
    `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py` uses
    `DELETED_OBJECTS_QUEUE`, `_fill_per_deleted_object_queue` and
    `_process_per_deleted_object_queue` in `PerDeletedObjectQueueTestCase`
    (`:778`) and `DeletedObjectSweepMixedQueueTestCase` (`:1092`), and
    `test_namespace_key_object.py` uses one more. Step 2e rewrites
    them.

**Corrections made at source** in this planning commit:

- The master plan's Situation gains "What phase 1 measured", and open
  questions 1, 2 and 3 are answered there. Open question 2's answer
  replaces its earlier recommendation (oldest first) with primary key
  order. See D8.
- The master plan's mission item 1 now states the bound: 24 hours.
- The master plan's phase 2 section drops "refill when its last fill
  is older than a bound" and adds the backlog gauge and the
  `DeleteObjectEvents` timing. Phase 3's candidates drop the
  `from_db()` hydration, which costs about 1% of an operation's time.
  Phase 4 now reads phase 2's backlog gauge, not phase 1's.
- The master plan's #4399 bullet records what phase 1 found.
- `PLAN-grpc-bounded-replies.md`'s dependencies note that this phase
  pages `GetObjectsByState` first.
- The `docs/plans/index.md` row describes what phase 1 measured.

## Decisions

### D7 -- Per-type work, round robin, with the rotation kept across passes

Replace `DELETED_OBJECTS_QUEUE` with a `dict[str, collections.deque[str]]`
of uuids per type. The drain takes **one** entry from each type that
has work, in the canonical order of `OBJECT_NAMES_TO_CLASSES`, then
starts again. The position in that rotation is module state, so a
pass that runs out of budget part way through the types starts the
next pass at the type after the last one served. Without that, the
types at the head of the order would be favoured whenever the budget
cuts a round short, which is phase 1's starvation in a milder form.

Round robin gives a type with 30 objects the same share as a type with
639k. That is what mission 2 asks for. A slow type, such as blobs at 4
seconds each, takes more of the budget per turn than a fast one, but
it cannot stop the others getting a turn.

### D8 -- Pages in primary key order, through new fields on the same RPC

Add to `GetObjectsByStateRequest` `uint32 page_size = 4` (0 means
unbounded, today's behaviour) and `string after_uuid = 5`. Add `bool
more_available = 2` to `GetObjectsByStateReply`. A paged request
returns at most `page_size` uuids with `object_uuid > after_uuid`, in
ascending `object_uuid` order, and sets `more_available` when there
were more. The SQL asks for `page_size + 1` rows to find that out
without counting.

This is the decision a reviewer is most likely to question, because
the master plan recommended oldest first. Finding 5 is why it changed.
Oldest first needs either a new index on the cluster's largest table,
which is a migration for a sort order the mission does not need, or a
sort of a whole type's range for every page, which is the unbounded
read this phase removes. Primary key order is index-served, matches
the shape the bounded-replies plan already specifies, and still meets
mission 1: every object in a type is reached within one walk of that
type's cursor. An object that arrives behind the cursor waits for the
walk to wrap. During the drain, oldest first would also make it wait,
behind the same backlog.

Wire compatibility, both directions:

- A new client sends `page_size` 0 from the unpaged wrapper, so an old
  or new server answers exactly as today. `get_active_blob_uuids()`
  and every other unpaged caller are untouched (finding 4).
- A new client paging against an **old** sf-database gets the whole
  unordered list, because the old server ignores fields it does not
  know. The paged client wrapper detects that (more uuids than
  `page_size`) and returns the whole list with `more_available` false.
  The sweep then behaves like today's fill until sf-database is
  upgraded, and its cursor resets, so no keyset is built on an
  unordered list.
- An old client never sends the new fields.

### D9 -- Refill a type when its own work runs out

At the start of each pass, for each type in the rotation whose deque
is empty, read one page from that type's cursor, with the existing age
filter (`updated_before`). A page with `more_available` moves the
cursor to its last uuid. A page without it resets the cursor to `''`,
so the next walk picks up anything that arrived behind it. A type
whose page came back empty is not read again for
`DELETED_OBJECT_EMPTY_RECHECK` (300 seconds), so an idle cluster
costs at most 29 small reads every five minutes. A restart is no
longer what refills the sweep.

*Revised in review:* any completed walk backs off, not only an empty
page, so an object whose hard delete always fails is retried every
five minutes rather than every pass. The names became
`DELETED_OBJECT_WALK_RECHECK` and `_DELETED_OBJECT_RECHECK_AT`. The
fill also stops starting reads at the pass's deadline. See the
execution notes.

The page size is `DELETED_OBJECT_PAGE_SIZE` = 500. At phase 1's costs
that is several minutes of one type's share of the budget, so a type
with a backlog is read about once an hour. Each reply is at most 500
uuids, about 20 KB.

The tier-wide failure handling stays as it is. A `DatabaseUnavailable`
during the fill stops the fill and records where to resume
(`_record_sweep_stopped`). A per-reply `None` skips that type and
moves on. Both are accounted through `_record_sweep_read()`, so the
failure streak gauge keeps working. One thing is new: **a pass whose
fill hit `DatabaseUnavailable` does not drain**. Each entry's first
read would spend a whole `_grpc_call` retry budget, up to about 93
seconds in the slow shape, and fail anyway.

Each page read runs inside `rpc_timing_scope('per_deleted_object_fill',
objtype)`, so the cost of the keyset query is visible per type (see
the risk about the query plan).

### D10 -- Stay in the maintenance loop: every minute, configurable budget

This answers open question 3. Register `per_deleted_object_checks`
every minute instead of every 15. Its budget per pass, fill included,
is a new option, `DELETED_OBJECT_SWEEP_SECONDS`, with a default of
20 and `ge=1`. That raises the sweep's daily time from 960 seconds to
28,800. At phase 1's costs that is roughly 70k objects a day: enough
to keep up with sfcbr's 40k and start eating the backlog, but not to
drain it in reasonable time. That is phase 3's job.

The budget is a cap, not a spend. A cluster with nothing to delete
finishes the job in milliseconds.

A dedicated thread was rejected. Phase 1 shows the time goes on
database commits. A thread would add concurrency against the tier
that is the bottleneck, and it would need its own answer to the
question this loop already answers, which is what stops a node that
has just lost the election from sweeping alongside the new leader. The
watchdog concern that prompted the thread option is met by the pet
between jobs (finding 7).

The configuration option exists now because phase 4 needs a throttle,
and a cluster smaller than sfcbr might want less than 20 seconds a
minute.

### D11 -- A per-type backlog gauge from a count, every hour

With paging, the sweep holds at most a page per type in memory, so
phase 1's `cluster_deleted_object_queue_depth` no longer measures the
backlog. Replace it with

    cluster_deleted_object_backlog{object_type}

set by a new scheduled job, `publish_deleted_object_backlog`, every 60
minutes. For each type it calls a new `CountObjectsByState` RPC (`SELECT
COUNT(*) ... WHERE object_type = :t AND state_value IN (...)`). Every
type is published, zeros included, so an alert or report can rely on
the series existing. Like the other leader-only metrics it is cleared
on demotion. A newly elected node publishes within an hour.

The count has **no age filter**. `update_time` is not in
`idx_object_states_type_state`, so an age filter would turn an
index-only range count into a row lookup per match. The count
therefore includes objects still inside their grace period, which is
at most an hour's arrivals for non-operation types and 30 seconds' for
operations. On sfcbr that is about 2,000 against 1.7M. The docs say
so.

Phase 1's gauge is removed, along with the counter that fed it. It is
unreleased (finding 10). The per-pass log line keeps a `remaining`
field, which now counts the entries held in memory.

### D12 -- Time `DeleteObjectEvents` without changing how it is called

Factor the observation in `_grpc_call()`'s `finally` into a small
helper, and call that helper from `_grpc_delete_object_events()`
around its direct stub call, labelled `DeleteObjectEvents`. The direct
call, its 30 second timeout and its log-and-continue failure handling
all stay as they are (finding 8). Without this, phase 3 would be
planned against numbers missing the largest cost for blobs and a
quarter of the cost for operations.

### D13 -- The per-entry body is moved, not changed

The body of today's processing loop moves into
`_hard_delete_deleted_object(objtype, obj_uuid, outcomes)`: the scope,
`from_db()`, the state and age checks, `hard_delete()`, the five
outcomes, the histogram observation and the exception handling. Phase
1's `test_call_order_is_unchanged` keeps asserting the same calls per
entry. What changes is which entry comes next, not what happens to it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | opus | none | Page `GetObjectsByState` by primary key (D8). See brief 2a. |
| 2b | medium | sonnet | none | Add `CountObjectsByState` (D11's RPC). See brief 2b. |
| 2c | low | sonnet | none | Time `DeleteObjectEvents` inside a scope (D12). See brief 2c. |
| 2d | medium | sonnet | none | Publish the backlog gauge (D11). See brief 2d. |
| 2e | high | opus | none | Drain fairly, a page at a time (D7, D9, D10, D13). See brief 2e. |
| 2f | low | sonnet | none | Document the changes. See brief 2f. |
| 2g | — | management session | — | After merge and deploy, measure and record. See brief 2g. |

Run the steps in order, one at a time, in this worktree. 2d and 2e
both edit `scheduled_tasks.py` and its test module. Two agents in one
tree can also undo each other through pre-commit's stash.

### Brief 2a: page `GetObjectsByState` by primary key

In `protos/database.proto`, add to `GetObjectsByStateRequest`:

    uint32 page_size = 4;   // 0 means unbounded (today's behaviour)
    string after_uuid = 5;  // keyset cursor; '' means from the start

and to `GetObjectsByStateReply`:

    bool more_available = 2;  // set only on a paged reply that stopped early

Regenerate with `tox -e genprotos` and commit the generated files.

In `shakenfist/mariadb.py`, beside `get_objects_by_state()` (`:4725`):

- Add `class ObjectsByStatePage(NamedTuple)` with `uuids: list[str]`
  and `more_available: bool`.
- Add `_direct_get_objects_by_state_page(object_type, state_values,
  page_size, after_uuid='', updated_before=None) ->
  Optional[ObjectsByStatePage]`. Use the same `WHERE` as
  `_direct_get_objects_by_state()` (`:4325`), plus `object_uuid >
  :after_uuid` when `after_uuid` is set. Add `ORDER BY object_uuid` and
  `LIMIT page_size + 1`. `more_available` is whether the extra row
  came back, and the extra row is dropped. Return `None` on
  `OperationalError`, as the unpaged function does. You may share the
  where-building with the unpaged function through a small helper, but
  its results must not change.
- Add `_grpc_get_objects_by_state_page(...)`. It builds the request
  with the two new fields and calls `_grpc_call()`. **If the reply has
  more than `page_size` uuids, the server is an older sf-database that
  ignored the fields.** Return all of them with `more_available=False`,
  and say why in the docstring (D8). On `grpc.RpcError`, call
  `_log_failed_read()` and return `None`, mirroring
  `_grpc_get_objects_by_state()`.
- Add the public `get_objects_by_state_page(object_type, state_values,
  page_size, after_uuid='', updated_before=None)`, routed by
  `_use_database_service()`. Raise `ValueError` for `page_size < 1`,
  because 0 is the wire value for "unbounded" and belongs to the
  unpaged call. Its docstring must say that a page is never a complete
  answer, and that a complement-set caller must use
  `get_objects_by_state()`.
- Do **not** change `get_objects_by_state()`,
  `_grpc_get_objects_by_state()` or `_direct_get_objects_by_state()`.
  They must keep sending and serving `page_size` 0 (survey finding 4).

In `DatabaseServicer.GetObjectsByState` (`daemons/database/main.py:734`),
if `request.page_size > 0`, call `mariadb.get_objects_by_state_page()`
and return `object_uuids` plus `more_available`. Otherwise take
today's path, unchanged. Use the same error handling on both branches:
`INVALID_ARGUMENT` for an unknown type, `UNAVAILABLE` for a `None`
result, `INTERNAL` for an exception. Count both branches with the
existing `get_objects_by_state` Monitor counter.

Tests:

- Servicer: the paged branch in the style of
  `shakenfist/tests/test_database_servicer_failed_reads.py`. A `None`
  result sets `UNAVAILABLE`. A page passes `more_available` through.
  `page_size` 0 takes the old path.
- Client: `_grpc_get_objects_by_state()` sends `page_size` 0. The paged
  wrapper passes the fields through. A reply longer than `page_size`
  comes back whole with `more_available=False`. An `RpcError` gives
  `None`.
- Live: a new `shakenfist/tests/test_mariadb_objects_by_state_page_live.py`,
  gated on `SF_MARIADB_TEST_DSN` exactly like
  `test_mariadb_capacity_reconcile_live.py`. Insert `object_states` rows
  for two types in mixed states and ages. Walk one type with page
  sizes 1, 2 and the row count. Assert that the union of the pages
  equals the unpaged result, that each page is in ascending uuid
  order, that only the last page has `more_available` false, that the
  state and age filters hold, and that the other type never appears.
- `public get_objects_by_state_page(page_size=0)` raises `ValueError`.

Commit subject: `Page GetObjectsByState by primary key.`

### Brief 2b: add `CountObjectsByState`

Follow the `add-grpc-service` skill (`.claude/skills/add-grpc-service/`).

- Proto: `rpc CountObjectsByState (CountObjectsByStateRequest) returns
  (CountObjectsByStateReply) {}`. The request has `ObjectType
  object_type = 1` and `repeated string state_values = 2`, and the reply
  has `uint64 count = 1`. Regenerate with `tox -e genprotos`.
- `_direct_count_objects_by_state(object_type, state_values) ->
  Optional[int]`: `SELECT COUNT(*) FROM object_states WHERE object_type
  = :t AND state_value IN (...)`, with `None` on `OperationalError`.
  There is deliberately no age filter. The docstring must explain why
  (D11): `update_time` is not in `idx_object_states_type_state`, so an
  age filter turns an index-only count into a row lookup per match.
  Raise `ValueError` for an empty `state_values`. An unfiltered count
  of a whole type is not what this is for.
- Servicer `CountObjectsByState`. Mirror `GetObjectsByState`'s status
  handling: `INVALID_ARGUMENT` for an unknown type or empty states,
  `UNAVAILABLE` for `None`, `INTERNAL` for an exception. Add a
  `count_objects_by_state` Monitor counter to the operations list at
  `daemons/database/main.py:6416`.
- `_grpc_count_objects_by_state()` through `_grpc_call()`, returning
  `None` via `_log_failed_read()` on `RpcError`. An older sf-database
  answers `UNIMPLEMENTED`, which lands here, and the caller treats it
  as a failed read. Add the public `count_objects_by_state()` routed
  by `_use_database_service()`.
- Tests: servicer branches, the client wrapper, and the live module
  from 2a gains a count test, asserting the count equals the length of
  the unpaged read with no age filter.

Commit subject: `Count objects by state in sf-database.`

### Brief 2c: time `DeleteObjectEvents` inside a scope

In `shakenfist/mariadb.py`:

- Factor the scoped observation in `_grpc_call()`'s `finally` into
  `_observe_scoped_rpc(timing_scope, operation, started)`. Behaviour
  must not change, and the existing tests in
  `shakenfist/tests/test_mariadb_rpc_timing.py` must pass unchanged.
- In `_grpc_delete_object_events()` (`:6508`), read the scope, take
  `time.monotonic()` before the stub call, and observe in a `finally`
  with operation `'DeleteObjectEvents'`. Keep the direct stub call,
  its `timeout=30.0` and its warning-only failure handling. Do not
  route it through `_grpc_call()`. The docstring says why it is direct.
- Add tests to `test_mariadb_rpc_timing.py`. In a scope, a successful
  call and an `RpcError` both observe one sample. Outside a scope,
  nothing is observed.

Commit subject: `Time DeleteObjectEvents inside a timing scope.`

### Brief 2d: publish the backlog gauge

In `shakenfist/daemons/cluster/scheduled_tasks.py`:

- Add `CLUSTER_DELETED_OBJECT_BACKLOG = Gauge('cluster_deleted_object_backlog',
  ..., ['object_type'])` beside the phase 1 metrics. Its help text says
  it is a count of objects in a final state, including those still
  inside their grace period.
- Add `publish_deleted_object_backlog()`, decorated with
  `@util_general.recorded_method` like the other jobs. For each type
  in `_sweep_object_types('deleted_object_backlog',
  list(OBJECT_NAMES_TO_CLASSES))`, inside
  `mariadb.rpc_timing_scope('deleted_object_backlog', objtype)`, call
  `mariadb.count_objects_by_state(ObjectType(objtype),
  FINAL_OBJECT_STATES)`.
  - A number sets the gauge, zero included.
  - `None` logs a warning and removes that type's label set, so a
    stale value cannot outlive the read that failed.
  - `DatabaseUnavailable` calls `_record_sweep_stopped(...)` and
    breaks. Finishing every type calls `_record_sweep_completed(...)`.
    This is the same tier-wide rule as the fill and the same reason
    (the long comment in `_fill_per_deleted_object_queue()`).
- `clear_deleted_object_metrics()` also clears the backlog gauge.
- In `shakenfist/daemons/cluster/main.py`, register
  `schedule.every(60).minutes.do(scheduled_tasks.publish_deleted_object_backlog)`
  beside the other jobs. Use a plain registration, not
  `_register_anchored_job`. Losing up to an hour of a gauge after a
  restart costs nothing, and anchoring is for jobs whose period
  outlives the daemon.
- Leave `cluster_deleted_object_queue_depth` alone in this step. 2e
  removes it.
- Tests, in `test_daemon_cluster_scheduled_tasks.py`: every type is
  published, zero included; `None` removes the label set; a
  `DatabaseUnavailable` stops the loop and records the resume point;
  demotion clears the gauge.

Commit subject: `Publish the deleted-object backlog per type.`

### Brief 2e: drain fairly, a page at a time

This step replaces the FIFO. Read D7, D9, D10 and D13 first, and
phase 1's brief 1b, which instrumented the code you are changing.

**First, before editing anything**, write the fairness test and run it
against the unmodified `scheduled_tasks.py`. It must fail there.
Quote the failure in the commit message. This is the master plan's
phase 2 definition of done.

- Add a new `DeletedObjectSweepFairnessTestCase` in
  `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py`. Drive
  `per_deleted_object_checks()`, the scheduled entry point, so the
  same test runs against both implementations.
- Back it with one fake store of final-state uuids. Use the first type
  in `OBJECT_NAMES_TO_CLASSES` with 5,000 uuids and the last type with
  3. Patch both `mariadb.get_objects_by_state` and
  `mariadb.get_objects_by_state_page` from it. Use `create=True` on
  the second, because it does not exist on the old code.
- Patch `get_object_class` in the module to return a fake class.
  Its `from_db()` returns an object in a final state with an old
  `update_time`, and its `hard_delete()` records `(type, uuid)` and
  advances a fake clock by 0.1 seconds.
- Patch `time.time` and `time.monotonic` in the module to that clock,
  and set the budget so that one pass handles about 50 entries. On the
  old code that is the hard-coded 10 seconds. On the new code, patch
  `config.DELETED_OBJECT_SWEEP_SECONDS`.
- Assert that all three of the last type's objects were hard deleted
  in the first pass. On the old code the FIFO serves the first type
  only, so the test fails.

Then implement, in `shakenfist/daemons/cluster/scheduled_tasks.py`:

- Module state:
  - `_DELETED_OBJECT_WORK: dict[str, collections.deque[str]]`;
  - `_DELETED_OBJECT_CURSOR: dict[str, str]`;
  - `_DELETED_OBJECT_EMPTY_UNTIL: dict[str, float]`;
  - `_DELETED_OBJECT_NEXT_TYPE: int`, the rotation position, an index
    into `list(OBJECT_NAMES_TO_CLASSES)`.

  Constants: `DELETED_OBJECT_PAGE_SIZE = 500` and
  `DELETED_OBJECT_EMPTY_RECHECK = 300`. As with the old queue, none of
  this is cleared on demotion. Say so in a comment, as the old code
  does for `_SWEEP_RESUME_AFTER`.
- Remove `DELETED_OBJECTS_QUEUE`, `_DELETED_OBJECTS_DEPTH`,
  `CLUSTER_DELETED_OBJECT_QUEUE_DEPTH`, `_publish_deleted_object_depth()`,
  `_fill_per_deleted_object_queue()` and
  `_process_per_deleted_object_queue()`. `clear_deleted_object_metrics()`
  now clears only the backlog gauge from 2d.
- Add `_sweep_work_page(sweep, object_type, state_values, page_size,
  after_uuid, updated_before) -> WorkPage`, a sibling of
  `_sweep_work_list()` (`:189`) with the same `DatabaseUnavailable`
  handling and the same `_record_sweep_read()` accounting. `WorkPage`
  carries `uuids` (None for a failed read), `more_available` and
  `tier_unavailable`.
- Add `_fill_deleted_object_work(now) -> bool`, which returns False
  when the tier is unavailable. Iterate
  `_sweep_object_types('per_deleted_object', types)`. Skip a type
  whose deque is non-empty, or whose `_DELETED_OBJECT_EMPTY_UNTIL` is
  in the future. Otherwise read one page inside
  `mariadb.rpc_timing_scope('per_deleted_object_fill', objtype)`,
  using `updated_before=now - _deleted_object_delay(objtype)`.
  - Tier unavailable: `_record_sweep_stopped(...)`, return False.
  - Per-reply `None`: continue.
  - Otherwise extend the deque. Set the cursor to the last uuid if
    `more_available`, else to `''`. If the page was empty and not
    `more_available`, set empty-until to `now +
    DELETED_OBJECT_EMPTY_RECHECK`.
  - After the loop, `_record_sweep_completed(...)` and return True.
  - Keep the long comment about tier-wide failures from the old fill.
    It still applies.
- Move the per-entry body of the old loop, unchanged, into
  `_hard_delete_deleted_object(objtype, obj_uuid, outcomes)` (D13).
- Add `_drain_deleted_object_work(execution_limit, outcomes) -> int`.
  Until the budget is spent or no type has work, choose the first
  type at or after `_DELETED_OBJECT_NEXT_TYPE` (wrapping) whose deque
  is non-empty, pop one uuid from its left, set
  `_DELETED_OBJECT_NEXT_TYPE` to the index after that type, and call
  `_hard_delete_deleted_object()`. Check the budget before each entry,
  as the old loop did.
- Rewrite `per_deleted_object_checks()`. Take the budget from
  `config.DELETED_OBJECT_SWEEP_SECONDS`, fill, and skip the drain if
  the fill returned False. Otherwise drain with whatever budget the
  fill left, and log one line per pass. Keep the message `Processed N
  items from per-deleted-object queue` so that Loki searches keep
  working. Its fields are `total`, one count per outcome, `remaining`
  (the sum of the deque lengths), `filled` (the uuids read this pass)
  and `elapsed_seconds`. Remove the "Refreshed per-deleted-object
  queue" line, which no longer describes anything.
- In `shakenfist/config.py`, beside `CLEANER_DELAY`, add
  `DELETED_OBJECT_SWEEP_SECONDS: int = Field(20, ge=1, description=...)`.
  The description says it is the per-minute time budget for the
  deleted-object sweep, fill included, and that it caps work rather
  than spending it.
- In `shakenfist/daemons/cluster/main.py:1067`, register the sweep
  `every(1).minutes`, and add a comment pointing at D10's reasoning:
  the cost is in commits, and the watchdog is petted between jobs.

Update the existing tests that use the FIFO (survey finding 11) to
the new structures, keeping what each asserted. Keep
`test_call_order_is_unchanged` asserting the same per-entry calls. Add
tests for:

- the rotation surviving a budget cut part way through the types;
- one type refilling while others still have work;
- the cursor advancing on `more_available` and resetting without it;
- an empty page backing that type off for
  `DELETED_OBJECT_EMPTY_RECHECK`;
- a fill that hits `DatabaseUnavailable` not draining, and recording
  the resume point;
- a per-reply `None` not stopping the other types' fills;
- page reads observing `database_client_rpc_seconds` with `sweep`
  `per_deleted_object_fill`.

Commit subject: `Drain deleted objects fairly, a page at a time.`

### Brief 2f: document the changes

- `docs/operator_guide/database.md`:
  - Rewrite the sweep paragraph that begins "The cluster daemon also
    runs a deleted-object sweep (every 15 minutes)". It now runs every
    minute, within `DELETED_OBJECT_SWEEP_SECONDS`. It reads one page
    of up to 500 uuids per type in primary key order, refilling a type
    when its own work runs out. It takes one object from each type in
    turn, so a large backlog in one type cannot stop the others.
  - Add a bullet for `DELETED_OBJECT_SWEEP_SECONDS` to the
    configuration list just above that paragraph.
  - In "Watching for sweeps that silently do nothing", replace the
    `cluster_deleted_object_queue_depth` bullet with
    `cluster_deleted_object_backlog`. Say that it is hourly, includes
    objects still inside their grace period, is published for every
    type including zero, is dropped on demotion, and appears within an
    hour of an election.
  - Add `per_deleted_object_fill` and `deleted_object_backlog` to the
    `sweep` values of `database_client_rpc_seconds`, and say that
    `DeleteObjectEvents` is now included.
  - Update the `remaining` field and add `filled`.
  - Add a PromQL example for the backlog trend:
    `deriv(sum(cluster_deleted_object_backlog)[1d:1h])`.
- `docs/release_notes/v07-v08.md`:
  - Replace the queue depth bullet with the backlog gauge.
  - Add the new configuration option.
  - Add a line under the existing behaviour changes saying that the
    sweep now runs every minute and is fair across types.
- Do not cite this plan or its phases from outside `docs/plans/`.

Commit subject: `Document the paged deleted-object sweep.`

### Brief 2g: measure (management session, after deploy)

Run this once sfcbr runs a build containing this phase, which
`count(cluster_deleted_object_backlog)` confirms by answering 29.
Wait 24 hours. Then query `http://maui.home.stillhq.com:9099/api/v1/query`:

```
# Every type with a backlog is being served
sum by (object_type) (increase(cluster_deleted_object_seconds_count{outcome="hard_deleted"}[24h]))
cluster_deleted_object_backlog

# Throughput and backlog trend
sum(increase(cluster_deleted_object_seconds_count{outcome="hard_deleted"}[24h]))
deriv(sum(cluster_deleted_object_backlog)[24h:1h]) * 86400

# Per-object cost by type, now including namespaces and DeleteObjectEvents
sum by (object_type) (rate(cluster_deleted_object_seconds_sum{outcome="hard_deleted"}[24h]))
  / sum by (object_type) (rate(cluster_deleted_object_seconds_count{outcome="hard_deleted"}[24h]))
topk(30, sum by (object_type, operation) (increase(database_client_rpc_seconds_sum{sweep="per_deleted_object"}[24h])))

# The keyset page and the count, per type
sum by (object_type) (rate(database_client_rpc_seconds_sum{sweep="per_deleted_object_fill"}[24h]))
  / sum by (object_type) (rate(database_client_rpc_seconds_count{sweep="per_deleted_object_fill"}[24h]))
max by (object_type) (rate(database_client_rpc_seconds_sum{sweep="deleted_object_backlog"}[24h])
  / rate(database_client_rpc_seconds_count{sweep="deleted_object_backlog"}[24h]))

# What it costs the database tier
sum(rate(database_requests_total[24h]))
rate(mysql_global_status_commands_total{command="commit"}[24h])

# The sweep's pairs against the shipped load budget (see Execution notes)
sort_desc(sum by (operation) (rate(database_requests_total{caller_daemon="cluster"}[24h])))
```

Compare the last two with the 24 hours before the deploy (`offset
1d`). Phase 3's planning commit records the results in the master
plan, under "What phase 2 measured".

## Risks and mitigations

- **The keyset query picks the wrong index.** MariaDB might range-scan
  `idx_object_states_type_state` and sort by uuid, instead of walking
  the primary key. That would cost a sort of the type's final-state
  range per page, which is the same shape as oldest first. Mitigation:
  D9's fill scope publishes the page read's latency per type, and
  brief 2g checks it. If `node_inst_op`'s page averages over 100 ms,
  add `FORCE INDEX (PRIMARY)` in a follow-up commit on this branch or
  in phase 3. The live test cannot catch this, because a test table is
  too small for the optimiser to choose the way it would on sfcbr.
- **More sweep work is more database load.** The sweep goes from 16
  minutes of work a day to up to eight hours. At phase 1's mix that is
  about three row-deleting commits per object, so about 2.5 more
  commits a second, against labqnap's 17.7 today: about 14% more.
  Mitigation: `DELETED_OBJECT_SWEEP_SECONDS` can be lowered without a
  release. Brief 2g compares commit and request rates before and
  after. The memory note on labqnap's buffer pool (sf-database
  watchdog stalls) is the failure mode to watch for. If it reappears,
  lower the budget first and investigate second.
- **The backlog count is a new hourly scan.** 29 index-only range
  counts covering about 1.7M index entries. Mitigation: the
  `deleted_object_backlog` scope publishes each count's latency, and
  brief 2g reports the worst type. If one count exceeds a few seconds,
  stagger the types across passes.
- **Namespaces are reached for the first time.** There are 45,755 of
  them, and `Namespace.hard_delete()` cascades to keys, mapping rules,
  inbound trusts and capacity claims (`shakenfist/namespace.py:344-385`).
  Its cost is unmeasured. Releasing claims of long-deleted namespaces
  is correct, and is part of what this plan exists to do, but it will
  change `scheduler_capacity_*` gauges on sfcbr as it happens.
  Mitigation: a single entry can overrun the budget only by its own
  cost, and the watchdog window is 300 seconds. Brief 2g reports the
  namespace cost.
- **A mixed-version window during the deploy.** Mitigation: D8's
  compatibility rules and 2a's test of the long-reply fallback. An old
  sf-database answers `CountObjectsByState` with `UNIMPLEMENTED`, which
  is logged as a failed read and leaves the gauge unset until the
  upgrade completes.
- **The Loki history in the master plan stops.** "Refreshed
  per-deleted-object queue" goes away, and with it the series the
  Situation table was built from. Mitigation: the backlog gauge
  replaces it with a better series. The master plan keeps the old
  table as history.

## Definition of done

1. `pre-commit run --all-files` passes in the worktree.
2. The fairness test fails against `develop`'s `scheduled_tasks.py`,
   checked before step 2e's edits, with the failure quoted in 2e's
   commit message. It passes against the final tree.
3. `grep -rn "DELETED_OBJECTS_QUEUE\|cluster_deleted_object_queue_depth\|_DELETED_OBJECTS_DEPTH"
   shakenfist docs --include=*.py --include=*.md | grep -v docs/plans`
   finds nothing.
4. A unit test asserts that `_grpc_get_objects_by_state()` sends
   `page_size` 0, and `git diff develop -- shakenfist/mariadb.py` shows
   no change inside `get_objects_by_state()`,
   `_grpc_get_objects_by_state()` or `_direct_get_objects_by_state()`.
5. `tox -e genprotos` leaves no diff.
6. `grep -n "per_deleted_object_checks\|publish_deleted_object_backlog"
   shakenfist/daemons/cluster/main.py` shows the sweep on
   `every(1).minutes` and the backlog on `every(60).minutes`.
7. No `hard_delete()` body changed: the same check as phase 1's
   definition of done item 3.
8. The live test module passes against a disposable MariaDB, either
   locally with `SF_MARIADB_TEST_DSN` set or in CI's live lane. Say
   which in the pull request.
9. `docs/operator_guide/database.md` and
   `docs/release_notes/v07-v08.md` name `cluster_deleted_object_backlog`
   and `DELETED_OBJECT_SWEEP_SECONDS`, and
   `grep -rn "phase 2\|phase-02" docs --include=*.md | grep -v
   docs/plans` finds nothing new.
10. After deploy: brief 2g's queries have been run. Every type whose
    backlog was non-zero at the start of the window shows hard deletes
    in it. The results are recorded by phase 3's planning commit.

## Execution notes

- **2026-10-21 (UTC), back brief.** Michael agreed with D8 and D10 as
  written, which ungates step 2e. He asked for the live MariaDB tests
  to be run locally, and allowed this session to push and open the
  pull request once the definition of done passes.
- **Live tests run locally against a throwaway `mariadb:11.8`
  container**, with a scratch virtualenv using PyMySQL
  (`mariadb+pymysql://`), because MySQLdb is not installed on the
  development host. CI uses the mysqldb dialect through
  `tools/ci-enum-widening-test.sh`. The baseline before this phase was
  140 live tests. After steps 2a and 2b it is 149, all passing.
- **A risk the plan missed: the database load budget.**
  `shakenfist/data/database_load_budget.yaml` budgets sf-cluster's
  `GetObjectState` (1.986/s base) and `GetObjectsByState` (0.222/s
  base), but none of the sweep's `Delete*` RPCs. Those are below the
  unbudgeted ceiling today because the sweep runs 16 minutes a day. At
  D10's budget on sfcbr, each will run at roughly 0.8/s, above the
  ceiling of `max(0.25, 0.05 × nodes)`, and `GetObjectState` will
  approach its tolerance.
  - `ShakenFistUnbudgetedDatabasePolling` is not deployed on the 33fl
    Prometheus (no such rule on 2026-10-21), so nothing pages. The 33fl
    nightly ratchet will report the new pairs.
  - In CI, `test_no_unbudgeted_fixed_rate_database_polling` flags a
    pair whose rate is constant across four one-minute windows and
    independent of suite activity. A sweep that stays budget-bound for
    four minutes would look like that. CI's local MariaDB should make
    deletes cheap enough that the sweep drains and goes idle, so its
    rate follows arrivals, but the pull request's CI is the real test.
  - Not fixed here, because a budget entry should be derived from
    measured rates, not guessed. Brief 2g adds a check of the sweep's
    pairs against the budget. Phase 3's planning commit decides between
    budget entries for the sweep and treating its rate as activity
    coupled.
- **2026-10-22 (UTC), first automated review** (1 document, 6
  consider, 1 none). Taken:
  - The fill now stops starting reads at the pass's deadline, and the
    next pass resumes with the types it did not reach. D10 said reading
    counts against the budget, but before this change the fill only
    reduced the drain's share; nothing stopped it.
  - D9's backoff now applies to every completed walk. With only empty
    pages backed off, an object whose hard delete always fails was
    re-read and logged at ERROR every minute. Before this phase that
    happened every 15 minutes, when the old queue emptied.
  - Upgrade order (`sf-database` first) is documented, and so is an
    `absent()` alert on the backlog gauge in place of failure streak
    coverage for the hourly count.
  - The proto comments are leading comments, so the generated stubs
    describe the right fields.
  - Tests cover an old server's long reply going through the fill.
  - Declined: feeding the backlog count into the failure streak gauge.
    That gauge's contract is a work-list read whose failure stops a
    sweep collecting, and the count collects nothing.
  - The PR's smoke run included
    `test_no_unbudgeted_fixed_rate_database_polling`, which *skipped*:
    the tier's rate was too level (activity spread 1.30 against 1.80
    required) to tell polling from activity. Its summary listed no
    unbudgeted or over-budget pairs. The merge queue's runs repeat it.

## Back brief

Before executing any step, back brief Michael on how the work maps to
this plan. Gate step 2e in particular on agreement with D8 (primary
key order rather than the master plan's earlier oldest first) and D10
(every minute, 20 seconds a minute by default, and no thread). Both
are cheap to change now, and 2e's rewrite of about 75 test references
is tedious to redo.
