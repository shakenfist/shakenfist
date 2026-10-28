# Phase 1: measure the sweep

Parent plan:
[PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md).

**Planning effort:** medium, as the master plan specifies. The
instrumentation follows patterns that already exist in
`shakenfist/daemons/cluster/scheduled_tasks.py`. What needs care is
measuring the right thing. If a hard delete's cost is attributed to
the wrong step, phase 3 fixes the wrong step, and since the sweep is
silent by nature, nobody would notice.

This phase starts the plan's decision sequence at **D1**. There is one
namespace across the whole plan.

## Scope

In:

- Per-object timing of the deleted-object sweep, by object type and
  outcome.
- Per-RPC timing of the database calls made *inside* the sweep, by
  object type and RPC name, with no edits to any `hard_delete()`
  override.
- A per-type queue depth gauge, which also gives the per-type backlog
  count the master plan wanted from a one-off SQL query.
- Operator documentation and release notes for the new metrics.
- After merge and deploy, 24 hours of sfcbr data, written into the
  master plan by phase 2's planning commit.

Out:

- **Any change to what the sweep does**: budget, cadence, ordering,
  refill rule. Measuring a system while changing it measures neither.
  That is phase 2.
- Timing for the other sweeps (`per_blob`, `per_instance`,
  `reconcile_orphans`). The scope mechanism in D2 makes adding them
  trivial later, but nothing in this plan needs them.
- Server-side latency metrics on sf-database. See D1.

## What the survey found

All references are to `develop` at 46142d9d66 plus the master plan
commit.

1. **sf-database has no latency metric, and its caller attribution
   stops at the daemon.** `database_requests_total{operation,
   caller_daemon}` (`shakenfist/daemons/database/main.py:6338-6342`)
   counts requests and nothing else. `caller_daemon` is a process-wide
   value set once at startup (`shakenfist/util/caller_identity.py:45-55`),
   so sf-cluster's sweep cannot be told apart from its other scheduled
   jobs on the server side.
2. **Every sweep RPC goes through one function.** sf-cluster is not in
   `DIRECT_MARIADB_CALLERS` (`shakenfist/mariadb.py:571`), so every
   database call it makes goes through `_grpc_call()` (`mariadb.py:709`,
   235 call sites), which already computes `method_name` from the stub
   (`:808-811`). Timing there covers all 18 `hard_delete()` overrides,
   `from_db()` and the state re-read, without touching any of them.
3. **The sweep runs on sf-cluster's MainThread.** The scheduled jobs
   run inside `schedule.run_pending()` on the main loop. A
   `contextvars.ContextVar` set by the sweep is therefore visible to
   every `_grpc_call()` the sweep makes, and invisible to the eventlog
   and logship drainer threads, which is what we want.
4. **The leader-only gauge pattern already exists, and the queue is
   not part of it.** `clear_sweep_failure_metrics()`
   (`scheduled_tasks.py:221-238`) drops leader-only label sets on
   demotion, and is called from `daemons/cluster/main.py:1201`.
   `DELETED_OBJECTS_QUEUE` itself is *not* cleared on demotion. A node
   that loses the lock keeps its queue and carries on from it if it is
   re-elected. Any gauge must therefore be recomputed from the queue
   each pass, not maintained incrementally from a zero that demotion
   reset.
5. **The processing loop has five outcomes, and only one of them is
   visible.** `_process_per_deleted_object_queue()` (`:881-920`)
   either hard deletes, finds the object gone (`from_db()` returned
   None), finds it no longer final, finds it too young, or swallows an
   exception. Only the count of all five is logged (`Processed N
   items`). sfcbr's queue was filled at the last restart and can be
   days old, so how much of the 10 second budget goes on entries that
   are no longer actionable is a real question.
6. **Prometheus already scrapes sf-cluster on sfcbr.** The 33fl
   Prometheus (`maui.home.stillhq.com:9099`) scrapes
   `CLUSTER_METRICS_PORT` (13007) on every node
   (`33fl/templates/prometheus.yml.j2:341-354`). A query for
   `scheduler_capacity_cluster_total` on 2026-10-18 answered from
   `sf-6`, the current leader. New sf-cluster metrics are visible on
   the next deploy, with no scrape changes.
7. **The sweep is invisible in today's request counts.** Ranking
   `database_requests_total{caller_daemon="cluster"}` over 6 hours on
   2026-10-18 put no `Delete*` RPC in the top 30. The thirtieth was at
   0.046/s. That fits the 30-120 hard deletes an hour #4473 measured,
   and it means request rates cannot tell us the sweep's cost. Timing
   is the only way.
8. **Open question 6 can be answered from the code alone.** Stage A
   of the events prune (`mariadb.py`, the stage that deletes
   `event_objects` by age) removes rows where `events.timestamp < now
   - max_age`. So the `event_objects` row that
   `DatabaseBackedObject.hard_delete()` writes after deleting the
   object's other rows expires with ordinary event retention. It is
   bounded, not a leak, and is not worth fixing.

**Corrections made at source** in this planning commit, so that later
steps do not redo them:

- In the master plan's phase 1 section, the one-off `GROUP BY` count
  from `object_states` is replaced by the depth gauge (D4). The fill
  already reads a per-type list, so the gauge at fill time *is* the
  per-type backlog, and it needs no direct database access.
- The master plan's phase 1 definition of done now says the numbers
  are recorded by phase 2's planning commit. The measurement can only
  start after this phase merges and deploys, and a phase cannot record
  what happens after it lands.
- The master plan's open question 6 is marked answered (finding 8).
- The master plan's phase 4 now names
  `docs/operator_guide/database.md` ("Watching for sweeps that
  silently do nothing") as the operator page, rather than "find the
  page".

## Decisions

### D1 -- Measure in the client, not in sf-database

A server-side histogram per RPC would cover every daemon, but it
would time only the server's share. Our question is what the *sweep*
pays per object, including the network, the client's retries, and the
Python between calls. It also could not attribute the cost to the
sweep (finding 1). Client-side timing answers the actual question.
The cost is that it only exists where we put a scope.

### D2 -- Attribute RPC time through a scope in `_grpc_call()`

Add a context-variable scope to `shakenfist/mariadb.py`: a context
manager `rpc_timing_scope(sweep, object_type)`, plus a histogram

    database_client_rpc_seconds{sweep, object_type, operation}

that `_grpc_call()` observes when, and only when, a scope is active.
The observation covers the whole call including retries, because that
is what the caller waits for. Outside a scope the only cost is one
`ContextVar.get()` per call.

This is the decision a reviewer is most likely to question, because
it puts sweep-specific accounting into the busiest function in the
codebase. The alternative is to wrap each step in the sweep: hydrate,
re-read, `hard_delete()`. But `hard_delete()` is 18 overrides with
cascades. Timing it as one step tells us *that* an instance is slow,
not *which* of its six-plus calls is slow, and open question 1 needs
the second answer. A scope set in one place, recorded in one place,
and invisible everywhere else is the smallest change that gives it.
The mechanism is general on purpose (a `sweep` label, not a hard-coded
name), so phase 2 or later work can time other sweeps without new
plumbing.

### D3 -- Per-object timing by type and outcome

    cluster_deleted_object_seconds{object_type, outcome}

is a histogram observed once per dequeued entry. `outcome` is one of
`hard_deleted`, `gone`, `not_final`, `too_young` and `failed`,
matching the five branches in finding 5. Subtracting the RPC
histogram's `_sum` from this one's gives the time spent outside the
database. If that is large, the cost is in our own code, and the
phase 3 fix changes.

Buckets: `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10`. The
expected range is 0.3 to 1.4 seconds per object. These buckets
resolve it, and resolve a 10ms RPC from a 100ms one, while keeping
the series count down (see Risks).

### D4 -- Queue depth by type, recomputed every pass

Keep a `collections.Counter` of queued entries per type beside
`DELETED_OBJECTS_QUEUE`. Increment it on put and decrement it on get,
both in `scheduled_tasks.py`. At the end of every
`per_deleted_object_checks()` pass, set

    cluster_deleted_object_queue_depth{object_type}

from the counter, for every type in it. Because it is set from the
counter each pass, not adjusted from the gauge, a re-elected node
publishes the right depth on its first pass (finding 4). On demotion
the gauge is cleared with the other leader-only metrics, but the
counter is not, because the queue is not.

The gauge at the first pass after a fill *is* the per-type backlog
the master plan asked for. It is newer than the fill by one pass's
drain, which at today's rate is noise.

### D5 -- One structured line per pass

Replace `Processed {processed} items from per-deleted-object queue`
with the same message plus structured fields: the total, a count per
outcome, the remaining depth, and the pass's elapsed processing time.
Keep the message text so that existing Loki searches for it keep
working. This makes the pass visible in Loki, where #4473's evidence
came from, without a Prometheus query.

### D6 -- No behaviour change

Nothing in this phase changes which objects are processed, in what
order, or for how long. A unit test asserts that the processing loop
still makes the same calls in the same order for a mixed queue, so a
reviewer can see that the instrumentation is all this phase changed.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add the RPC timing scope to `shakenfist/mariadb.py` (D2). See brief 1a. |
| 1b | medium | sonnet | none | Instrument the deleted-object sweep (D3-D6) in `shakenfist/daemons/cluster/scheduled_tasks.py`. See brief 1b. |
| 1c | low | sonnet | none | Document the metrics. See brief 1c. |
| 1d | — | management session | — | After merge and deploy, collect and record the measurements. See brief 1d. |

### Brief 1a: the RPC timing scope

In `shakenfist/mariadb.py`:

- Add a module-level `contextvars.ContextVar` named
  `_RPC_TIMING_SCOPE`, defaulting to `None`. It holds a `(sweep,
  object_type)` tuple.
- Add a `prometheus_client.Histogram`, `DATABASE_CLIENT_RPC_SECONDS =
  Histogram('database_client_rpc_seconds', ..., ['sweep',
  'object_type', 'operation'], buckets=(...D3's buckets...))`.
  `shakenfist/logship_drainer.py:90` is an existing `Histogram` to
  mirror for style. The help text must say the histogram is only
  observed inside an `rpc_timing_scope()`, so most daemons export no
  samples for it.
- Add `@contextlib.contextmanager def rpc_timing_scope(sweep: str,
  object_type: str)`. It sets the variable, yields, and resets it with
  the token in a `finally`. Give it a docstring explaining D2: why the
  scope exists, that it is per thread/context, and that nesting
  replaces the outer scope for the duration.
- In `_grpc_call()` (`:709`), read the scope once at entry. If it is
  set, take `time.monotonic()` before the retry loop and observe the
  elapsed time in a `finally` around the whole loop, labelled with
  `method_name or 'unknown'`. Observe on success *and* on failure. A
  call that exhausts its retries is exactly the expensive case.
  Outside a scope, do nothing beyond the one `get()`.
- Do not change any `_grpc_call()` behaviour, signature or return
  value.

Tests go in a new `shakenfist/tests/test_mariadb_rpc_timing.py`,
following the mocking style of the existing direct `_grpc_call()`
tests in `shakenfist/tests/test_database_unavailable.py`:

- In a scope, a successful call observes one sample with the right
  labels.
- Outside a scope, nothing is observed.
- A call that raises still observes.
- The scope is reset after the `with` block, including when the body
  raises.

Read sample counts from `prometheus_client.REGISTRY.get_sample_value(
'database_client_rpc_seconds_count', {...})` before and after, as a
delta, because the registry is process-global across tests.

Commit subject: `Time database RPCs inside a named scope.`

### Brief 1b: instrument the deleted-object sweep

In `shakenfist/daemons/cluster/scheduled_tasks.py`:

- Beside `DELETED_OBJECTS_QUEUE` (`:42`), add `_DELETED_OBJECTS_DEPTH:
  collections.Counter[str]`. In `_fill_per_deleted_object_queue()`
  (`:822-878`), increment it next to the `put()`. In
  `_process_per_deleted_object_queue()` (`:881-920`), decrement it
  right after a successful `get()`, before any `continue`. Add a
  comment saying it must move in step with the queue, and that it is
  deliberately not cleared on demotion because the queue is not
  (survey finding 4).
- Add the histogram `CLUSTER_DELETED_OBJECT_SECONDS` (D3, labels
  `object_type` and `outcome`) and the gauge
  `CLUSTER_DELETED_OBJECT_QUEUE_DEPTH` (D4, label `object_type`) next
  to the existing metric definitions (`:44-64`).
- In the processing loop, wrap each entry's work in
  `mariadb.rpc_timing_scope('per_deleted_object', objtype)` and time
  it with `time.monotonic()`. Set an `outcome` variable on each
  existing branch (`gone` where `from_db()` returned None, `not_final`,
  `too_young` where the age check fails, `hard_deleted`, and `failed`
  in the `except`), and observe once in a `finally`. Do not reorder or
  change the branches. The age check is currently an `if` with no
  `else`, so `too_young` is its implicit else.
- Give `_process_per_deleted_object_queue()` an optional
  `outcomes: Optional[collections.Counter] = None` parameter that it
  fills with the per-outcome counts, and keep returning the total as
  an `int`. Several tests in
  `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py` (around
  `:1022-1064`) assert on that integer, and changing the return type
  would churn them for no gain.
- In `per_deleted_object_checks()` (`:805-819`), log D5's structured
  line, keeping the existing message text, and then set the depth
  gauge for every key in `_DELETED_OBJECTS_DEPTH`. Remove a type's
  label set when its count reaches zero, and drop it from the counter.
  Note that the existing early `return` when the fill took over 10
  seconds skips processing. The gauge must still be set on that path,
  because a pass that only filled is exactly the one that reports the
  backlog.
- Add a `clear_deleted_object_metrics()` that clears the gauge (not
  the counter) and call it next to `clear_sweep_failure_metrics()` at
  `daemons/cluster/main.py:1201`. Mirror that function's docstring
  reasoning.

Tests, in `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py`
next to the existing `per_deleted_object` tests:

- A mixed queue with one entry for each outcome produces one histogram
  sample per outcome, and per-outcome counts that match.
- The depth gauge equals the per-type counts after a fill, and falls
  as entries are processed.
- The gauge is set when the fill exceeded its budget and processing
  was skipped.
- After `clear_deleted_object_metrics()`, the gauge has no samples but
  the counter still matches the queue, and the next pass republishes
  it.
- D6: with `from_db` and `hard_delete` mocked, the calls made for the
  mixed queue are the same, in the same order, as before this change.
  Write this test first and run it against the untouched code, so
  that it is known to pass there.

Commit subject: `Publish the deleted-object sweep's cost and backlog.`

### Brief 1c: document the metrics

- In `docs/operator_guide/database.md`, in the "Watching for sweeps
  that silently do nothing" section (`:970-1010`), add the three new
  metrics after the failure-streak gauge, in the same style. Say what
  each one measures, that the depth gauge and the per-object histogram
  are leader-only like the streak gauge, and that
  `database_client_rpc_seconds` is observed only inside a timing scope,
  which today is only this sweep. Give two example queries: mean
  seconds per hard delete by type, and the top RPCs by total time
  (the queries are in brief 1d). State the arrival-rate reasoning a
  reader needs: a depth that grows pass over pass means the sweep is
  behind.
- In `docs/release_notes/v07-v08.md`, under "New Prometheus metrics"
  (`:1065`), add one bullet per metric, mirroring the
  `cluster_sweep_work_list_failure_streak` bullet at `:1084`.
- Check `pre-commit run --all-files`. It includes the documentation
  link checker and a checker that rejects plan phase numbers in
  documentation, so do not cite this plan's phases from `docs/`
  outside `docs/plans/`.

Commit subject: `Document the deleted-object sweep metrics.`

### Brief 1d: measure (management session, after deploy)

Run this after the phase's pull request has merged and sfcbr is
running a build that contains it. Confirm that with
`count by (instance) (cluster_deleted_object_queue_depth)`, which
answers only once the leader is publishing. Then wait at least 24
hours. Every query goes to `http://maui.home.stillhq.com:9099/api/v1/query`.

```
# Backlog per type, now and its trend
cluster_deleted_object_queue_depth
deriv(sum(cluster_deleted_object_queue_depth)[24h:15m])

# Mean seconds per entry, by type and outcome
sum by (object_type, outcome) (rate(cluster_deleted_object_seconds_sum[24h]))
  / sum by (object_type, outcome) (rate(cluster_deleted_object_seconds_count[24h]))

# Share of the budget spent on entries that were not hard deleted
sum by (outcome) (rate(cluster_deleted_object_seconds_sum[24h]))

# Where the database time goes, by type and RPC
topk(20, sum by (object_type, operation) (rate(database_client_rpc_seconds_sum[24h])))

# Mean latency per RPC
sum by (operation) (rate(database_client_rpc_seconds_sum[24h]))
  / sum by (operation) (rate(database_client_rpc_seconds_count[24h]))

# Time outside the database, by type
sum by (object_type) (rate(cluster_deleted_object_seconds_sum[24h]))
  - sum by (object_type) (rate(database_client_rpc_seconds_sum{sweep="per_deleted_object"}[24h]))
```

Write the results into the master plan's Situation section, under a
heading "What phase 1 measured", answer open question 1 there, and
check #4399 against the RPC breakdown: is sf-cleaner's
`GetObjectState` growth the backlog? Do this in phase 2's planning
commit, as the `next-phase` skill's survey step, and revise phase 2
and 3's scope to match.

## Risks and mitigations

- **Series count.** `database_client_rpc_seconds` has up to 29 types
  times the RPCs each type's hard delete makes. A realistic estimate is
  under 200 label sets, times 12 series each (ten buckets, `_sum`,
  `_count`), so about 2,400 series, all on one node. That is
  acceptable for the 33fl Prometheus. Mitigation: after deploy, the
  management session checks
  `count({__name__=~"database_client_rpc_seconds.*"})` in brief 1d
  before doing anything else. Above 5,000, drop `object_type` from the
  RPC histogram and rely on the per-object histogram for the type
  split.
- **The scope leaks into unrelated calls.** If a nested call opens a
  thread or reuses a context, its RPCs would be attributed to the
  sweep. Mitigation: brief 1a's reset test, and finding 3 (the sweep is
  MainThread-only). The reviewer checks that no thread is started
  inside the scoped block.
- **Instrumentation changes behaviour.** Mitigation: D6's call-order
  test, written first and run against the untouched code.
- **The measurement reflects a stale queue.** sfcbr's queue was
  filled at the last deploy, and this phase's deploy refills it. The
  first 24 hours therefore measure a fresh fill, which is
  representative of what phase 2's refill-on-age will see.

## Definition of done

1. `pre-commit run --all-files` passes in the worktree.
2. `grep -n "rpc_timing_scope" shakenfist/mariadb.py
   shakenfist/daemons/cluster/scheduled_tasks.py` shows the definition
   and exactly one use.
3. `grep -c "def hard_delete" -r shakenfist --include=*.py` is
   unchanged (18 outside tests), and no `hard_delete()` body was
   edited: `git diff develop -- $(grep -rl "def hard_delete"
   shakenfist --include=*.py | grep -v tests)` touches no
   `hard_delete` hunk.
4. D6's call-order test passes against `develop`'s
   `scheduled_tasks.py`, which is checked by running it before step
   1b's edits, and against the final tree.
5. Removing the `finally` observation in `_grpc_call()` makes brief
   1a's "a call that raises still observes" test fail (mutation
   check, run once and reverted).
6. `docs/operator_guide/database.md` and
   `docs/release_notes/v07-v08.md` name all three metrics, and
   `grep -rn "phase 1\|phase-01" docs --include=*.md | grep -v
   docs/plans` finds nothing new.
7. After deploy: `cluster_deleted_object_queue_depth` answers from the
   leader on sfcbr. Brief 1d's queries have been run, and the results
   are in the master plan (recorded by phase 2's planning commit).

## Back brief

Before executing any step, back brief Michael on how the work maps
to this plan, and in particular on D2. It is the one change outside
the sweep, and it is cheap to agree before step 1a and tedious to
unwind after 1b has built on it.
