# Keep the deleted-object sweep ahead of its arrivals

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`AGENTS.md` for project conventions and the index into
`docs/`, including build commands and database access
patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

Every object that reaches a final state (`deleted`, `complete`,
`abort` or `expired`, `shakenfist/constants.py:204-209`) is meant to be
hard deleted once it has sat there for `CLEANER_DELAY` (an hour by
default; 30 seconds for `*_op` types, `_deleted_object_delay()` in
`shakenfist/daemons/cluster/scheduled_tasks.py`). The elected cluster
maintainer does this in `per_deleted_object_checks()`. On sfcbr it
has not kept up for months, and the gap is widening.
[#4473](https://github.com/shakenfist/shakenfist/issues/4473) reported
the backlog at 1.32M objects on 2026-10-06.

### What the sweep does today

All references are to `develop` at 46142d9d66.

- **One FIFO for every type.** `DELETED_OBJECTS_QUEUE` is a single
  `queue.Queue()` (`scheduled_tasks.py:42`) holding `(object_type,
  uuid)` tuples in fill order.
- **The queue is refilled only when it is empty.**
  `per_deleted_object_checks()` (`scheduled_tasks.py:805-819`) calls
  `_fill_per_deleted_object_queue()` only on
  `DELETED_OBJECTS_QUEUE.empty()`. With a backlog it is never empty,
  so in practice it refills only when sf-cluster restarts, which on
  sfcbr means a deploy.
- **The fill is unbounded.** `_fill_per_deleted_object_queue()`
  (`:822-878`) asks `get_objects_by_state()` for *every* final-state
  uuid of each of the 29 types older than the delay, and the reply
  size has no bound but gRPC's message size limit. That is the defect
  class [Bound gRPC reply sizes](PLAN-grpc-bounded-replies.md) exists
  for, and its phase 2 (bounded pages for sweep callers) touches the
  same call.
- **There is 40 seconds of work an hour.** The job is scheduled every
  15 minutes (`shakenfist/daemons/cluster/main.py:1067-1068`) with a
  10 second processing budget (`:817-818`), less whatever the fill
  took that pass.
- **Every object is processed one at a time.**
  `_process_per_deleted_object_queue()` (`:881-920`) hydrates the
  object with `from_db()`, re-reads its state, and calls the object's
  own `hard_delete()`. The base `DatabaseBackedObject.hard_delete()`
  (`shakenfist/baseobject.py:759-763`) makes three delete calls and
  writes an event, and every subclass adds its own deletes in front:
  two for a network or an interface, four for a blob, three for an
  operation. `Instance.hard_delete()` (`shakenfist/instance.py:1740-1757`)
  first runs `_release_placement()`, a placement transaction.
  `Namespace.hard_delete()` (`shakenfist/namespace.py:344-385`)
  cascades to every key, mapping rule, inbound trust and capacity
  claim the namespace still has. So one object costs somewhere around
  six to fifteen database round trips, more for a namespace.

The re-ordering helpers `_sweep_object_types()` and
`_SWEEP_RESUME_AFTER` (`:66-105`) only re-order the *fill* after a
tier-wide failure. They do nothing for fairness during the drain,
which is why whole types starve. #4473's table shows instance,
namespace, ipam and dhcp hard deletes all at zero for a day while
`artifact_fetch_op` got 2,124.

### What sfcbr shows now

Queue size at the last refill of each day, from sf-cluster's own log.
To reproduce:

```
loki-query '{job="shakenfist"} |= "Refreshed per-deleted-object queue"' \
    --tenant sfcbr --since 13d --limit 200
```

| Day (last refill, UTC) | Items |
|---|---|
| 2026-10-05T20:09 | 1,308,660 |
| 2026-10-07T20:07 | 1,356,655 |
| 2026-10-09T23:51 | 1,400,584 |
| 2026-10-11T19:52 | 1,446,851 |
| 2026-10-13T20:06 | 1,495,575 |
| 2026-10-15T21:57 | 1,540,445 |
| 2026-10-16T21:53 | 1,588,687 |
| 2026-10-17T23:03 | 1,626,578 |

That is about 24k a day through 2026-10-15 and about 43k a day for
the last two days. #4473 measured the drain at 29 to 116 objects an
hour, which is 0.3 to 1.4 seconds per object. At that cost no budget
the 15 minute job could afford would drain the queue. Even 15 minutes
of work every 15 minutes, the whole job, would manage about 60k a day.

### What phase 1 measured

Phase 1's metrics went live on sfcbr at 2026-10-19T20:15Z, when sf-5
refilled the queue. sf-4 took over at 2026-10-20T19:40Z and refilled
it again. The numbers below are 24 hour windows ending
2026-10-20T21:23Z, queried from the 33fl Prometheus with phase 1's
brief 1d. `rate()` and `increase()` handle the change of leader per
series.

**Throughput.** The sweep hard deleted 2,048 objects in 24 hours and
spent 974 seconds doing it. That is the whole budget: 10 seconds every
15 minutes is 960 seconds a day. Every entry it reached was
`hard_deleted`. No entry was `gone`, `not_final`, `too_young` or
`failed`, so none of the budget was spent on entries that were no
longer actionable.

**Starvation.** All 2,048 came from the four types at the head of the
fill order:

| Type | Hard deleted | Mean seconds | In timed RPCs | Outside them |
|---|---|---|---|---|
| `artifact_fetch_op` | 1,351 | 0.458 | 0.343 | 0.115 |
| `dhcp` | 516 | 0.147 | 0.095 | 0.052 |
| `instance` | 130 | 0.558 | 0.305 | 0.253 |
| `blob` | 51 | 4.056 | 0.698 | 3.358 |

The other 16 types with a backlog, 1.72M objects between them, got
nothing.

**Where the time goes.** Reads are cheap and the deletes that remove a
row are not. Mean wall time per RPC, as the client sees it:

| RPC | Mean ms | Kind |
|---|---|---|
| `GetObjectState`, `GetClusterOperation`, `GetInstance`, `GetBlob`, `GetDnsMasq` | 2.0-2.4 | read |
| `ReleaseInstancePlacement` | 1.9 | placement already gone |
| `DeleteClusterOperationError`, `DeleteObjectMetadata` | 3.4-5.1 | delete, usually of nothing |
| `DeleteBlobTransfersForBlob` | 12 | delete |
| `DeleteDnsMasq` | 49 | delete of a row |
| `DeleteInstanceAttributes`, `DeleteInstance` | 63-70 | delete of a row |
| `DeleteClusterOperationTargetsForObject`, `DeleteObjectState` | 84-88 | delete of a row |
| `DeleteClusterOperationTarget`, `DeleteClusterOperation` | 105-116 | delete of a row |
| `DeleteBlob`, `DeleteBlobAttributes` | 138-139 | delete of a row |
| `DeleteBlobHashes` | 250 | delete of several rows |

A delete that removes a row costs 25 to 125 times a read. The "usually
of nothing" label is inferred: most objects have no metadata and no
operation errors, and those two RPCs cost what a read costs. MariaDB on
labqnap runs with `innodb_flush_log_at_trx_commit=1` and no binary log.
It reported no row lock waits over the window. That pattern fits a
per-commit log flush of something like 50 to 100 ms on labqnap's
storage. It is the most likely explanation, but phase 1 cannot prove
it.

**The time outside the timed RPCs is mostly `DeleteObjectEvents`.**
`_grpc_delete_object_events()` (`shakenfist/mariadb.py:6508`) calls
its stub directly with its own 30 second timeout, not through
`_grpc_call()`, so phase 1's scope never saw it. It is the base
`hard_delete()`'s event-reference delete. The objects with the most
events cost the most outside: 3.4 seconds for a blob, 0.25 for an
instance, 0.12 for an operation.

**Arrivals.** Between the fills at 2026-10-19T20:15Z (1,688,146) and
2026-10-20T19:55Z (1,726,236), the backlog grew by 38,090 while the
sweep removed about 2,000. That is about 40k arrivals a day, which
matches the Loki series above. By type, over those 23.7 hours:
`namespace` 8,819, `node_inst_op` 7,750, `net_op` 5,612,
`namespace_key` 3,943, `node_net_op` 3,809, `node_inst_netdesc_op`
1,603, `interface` 1,143, and under 1,000 each for the rest.

**Backlog by type** at 2026-10-20T21:23Z, 1,725,810 in all:
`node_inst_op` 639,004, `net_op` 367,052, `node_net_op` 199,403,
`node_inst_netdesc_op` 109,500, `namespace_key` 103,252, `interface`
81,284, `net_macaddr_ip_op` 48,251, `namespace` 45,755,
`node_blob_op` 39,108, `network` 32,712, `net_iface_op` 27,775,
`net_iface_ip_op` 18,657, `node_inst_net_iface_op` 4,726, `ipam`
3,114, `instance` 2,622, `artifact_fetch_op` 1,186,
`node_inst_snap_op` 1,016, `net_ip_op` 732, `dhcp` 630, `blob` 34.

**What it means.** At about 0.4 seconds an object, keeping up with
40k arrivals a day takes about four and a half hours of serial work
a day. The sweep gets 16 minutes. Fairness alone (phase 2) cannot close
that gap within today's budget, and a larger budget alone would spend
it on commits. The cost per object has to fall (phase 3).

**#4399 is not the sweep.** sf-cleaner's `GetObjectState` rate is
2.86/s, against 2.90/s three days earlier. Every `GetObjectState` the
sweep makes comes from `caller_daemon="cluster"`, at about 0.05/s.
Phase 1 scopes only sf-cluster's sweep, so it cannot attribute
sf-cleaner's reads, and #4399 stays open. Phase 4's drain is a natural
experiment: if sf-cleaner's rate falls as the backlog drains, the
backlog was the cause.

### What phase 2 measured

Phase 2 (merged in dd5723c3db) went live on sfcbr on 2026-10-22, a few
minutes before 09:10Z. The numbers below are the 24 hour window ending
2026-10-23T09:10Z, from brief 2g's queries against the 33fl Prometheus.
They were recorded while phase 3 was being planned.

**Throughput.** The sweep hard deleted 76,840 objects, against phase
1's 2,048. A further 241 entries were `gone`. None were `not_final`,
`too_young` or `failed`. The backlog stood at 1,744,910 and fell by
about 39k a day. Arrivals were therefore about 38k a day, which agrees
with phase 1's 40k.

**Fairness.** Every type with a backlog was served. Each type still
holding one got the same share, about 5,100 objects a day. Seven types
drained to nothing or nearly nothing: `node_inst_net_iface_op` (4,678
to 0), `ipam` (3,780 to 15), `instance` (2,045 to 23), `node_inst_snap_op`,
`artifact_fetch_op`, `net_ip_op` and `dhcp`. An equal share is not
enough for the two busiest types. Over the window:

| Type | Arrivals | Hard deleted | Backlog change |
|---|---|---|---|
| `namespace` | 8,705 | 5,111 | +3,594 |
| `node_inst_op` | 7,441 | 5,085 | +2,356 |
| `net_op` | 5,134 | 5,085 | +49 |
| `namespace_key` | 4,804 | 5,111 | -307 |
| `node_net_op` | 3,106 | 5,085 | -1,979 |

The other seven large types each fell by 3,600 to 4,700. "Arrivals"
here is the backlog change plus the hard deletes.

**Cadence.** The sweep spent 13,585 seconds in 24 hours. That is 20
seconds every 128 seconds, not every 60. The maintenance loop runs due
jobs once a minute, and `schedule` sets a job's next run from the
moment it finishes, so a 20 second job registered every minute misses
every other pass. Phase 3's D19 fixes this.

**Where the time goes.** Phase 1's per-commit reading holds, and the
floor is lower on a quieter tier. The mean milliseconds per RPC, as the
sweep sees them:

| RPC | Mean ms |
|---|---|
| reads (`Get*`), `ReleaseInstancePlacement` | 1.6-2.1 |
| `DeleteClusterOperationError`, `DeleteObjectMetadata` (usually delete nothing) | 2.0-3.2 |
| `DeleteClusterOperationTarget` | 7.2 |
| `DeleteClusterOperationTargetsForObject` | 21.5 |
| `DeleteObjectState`, `DeleteClusterOperation`, `DeleteNetwork`, `DeleteInstance`, `DeleteNamespace` and the other single-row deletes | 38-49 |
| `DeleteObjectEvents` | 57.8 |
| `DeleteAgentOperation` | 75.7 |

An operation's hard delete costs 0.133 to 0.146 seconds. That is three
writing commits: the state, the events and the operation row.
`namespace` costs 0.25 seconds, `instance` 0.44 and `blob` 3.6. The
fill's paged reads cost 2 to 11 ms a page for every type. The hourly
backlog count costs about 7.7 seconds in all, 3.1 of them for
`node_inst_op`.

**Database load.** sf-database served 195.7 requests a second, up
from 182.9 the day before, and committed 21.9 a second, up from 17.9.
sf-cluster's requests went from 21.4 a second to 29.6, so it accounts
for 8.2 of the 12.8 a second rise. sf-cluster's `GetObjectState` went
from 2.54 a second to 4.01, against a budget of 1.986 plus 0.016 per
instance with a tolerance of twice that. The sweep's per-object delete
RPCs now run at 0.06 to 0.9 a second each, which is above the
unbudgeted ceiling.

**#4399.** sf-cleaner's `GetObjectState` rate was 2.88 a second,
against 3.16 the day before and 2.69 three days before. The small
types that drained have not moved it. The large backlogs that remain
are operations, so phase 4's drain is still the experiment that can
answer #4399.

**What it means.** Fairness worked, and the cost per object did not
change. At three commits an operation, keeping every type ahead of its
arrivals means more commits a day, not fewer. Phase 3 changes the cost:
one commit for a batch rather than three for each object. It also fixes
the cadence.

### Why it matters

- **Database load.** Anything that lists including deleted objects
  pays for the whole backlog. #4473 ties the API's per-instance read
  pairs growing 2.7x to 6x at a flat request rate, and
  `GetInstance::cleaner` going from 0.9 to 7.5 qps, to that. It also
  ties sf-database at 322/s against a 176/s baseline, which matters
  to the [database load reduction](PLAN-database-load-reduction.md)
  model. The sfcbr CI conductor's `get_instances(all=True)` per
  namespace teardown was sized on the assumption of one hour of
  retention.
- **Things that wait for a hard delete wait indefinitely.** The REST
  namespace delete (`shakenfist/external_api/auth.py:380-421`) only
  soft deletes. A namespace's name cannot be reused until the static
  row goes (the comment at `:409-411`), and its capacity claims are
  returned only by `Namespace.hard_delete()` (`namespace.py:374-381`).
  The CI conductor deletes its claims explicitly at teardown
  (scheduler-reservations phase 4c, step 3), so CI is protected, but
  any other caller that deletes a namespace without deleting its
  claims first holds that capacity until the sweep reaches the
  namespace.
- **It is quiet.** Nothing errors and nothing alerts. The only
  signal is the queue size in an INFO line logged on restart.

### Related work

- [#3533](https://github.com/shakenfist/shakenfist/issues/3533)
  (closed): moved hydration out of the fill and pushed the age filter
  into SQL, which made the fill survivable but not fast.
- [#3638](https://github.com/shakenfist/shakenfist/issues/3638)
  (closed) and [Bound gRPC reply sizes](PLAN-grpc-bounded-replies.md):
  the unbounded fill reply.
- [#3922](https://github.com/shakenfist/shakenfist/issues/3922):
  errored objects are never hard deleted. Fixing it adds to the
  arrival rate, so it must not land before this plan's throughput
  phase does.
- [#4428](https://github.com/shakenfist/shakenfist/issues/4428): the
  events prune is also slow, and both sweeps share the events tables.
- [#4399](https://github.com/shakenfist/shakenfist/issues/4399):
  sf-cleaner's `GetObjectState` rate has doubled since July. Phase 1
  checked whether that is the sweep. It is not, and phase 1 could not
  tell whether it is the backlog (see "What phase 1 measured").

## Mission and problem statement

Make the hard-delete sweep keep up, keep it visibly keeping up, and
drain the backlog sfcbr already has. When this plan is done:

1. An object in a final state is hard deleted within a bounded time
   after its delay has passed, for every object type, on a cluster
   with sfcbr's churn. The bound is **24 hours**, set from phase 1's
   numbers while planning phase 2. At about 40k arrivals a day, any
   tighter bound needs the same continuous throughput as 24 hours
   does. A day is also what a daily report can check.
2. No object type can starve another. A large backlog in one type
   slows that type, not the rest.
3. The sweep's reads are bounded by construction, not by gRPC's
   message size limit.
4. The backlog is a published metric, per object type, so a sweep
   that falls behind is visible within a day rather than at a million
   rows.
5. sfcbr's existing backlog has been drained at a rate the database
   tier could absorb, without a hand-run SQL delete.

This plan does not change *what* gets hard deleted (which states,
which delays), with one exception: if phase 1 shows that the
retention policy itself is part of the problem, that is raised as a
decision, not quietly changed. Errored objects (#3922) and releasing
namespace claims at soft delete are left as future work, for the
reasons given there.

## Open questions

These are the questions the phase plans have to answer. Each says
which phase owns it and, where there is one, what the planner
recommends today.

1. **What does one hard delete cost, and where does the time go?**
   (Phase 1.) #4473 infers 0.3 to 1.4 seconds per object. That is far
   more than six to fifteen point operations should cost. Either one
   step is slow (the instance placement release, the namespace
   cascade, the event write), or every gRPC round trip from the
   maintainer is slow. We should not design the throughput fix until
   this is measured per object type and per step. **Answered by phase
   1** (see "What phase 1 measured"): no single step dominates. The
   cost is the deletes that actually remove a row, each 50 to 250 ms,
   plus the event-reference delete that phase 1's scope could not see.
   Reads cost about 2 ms. The instance placement release, a suspect
   when this was written, costs 2 ms. The namespace cascade was never
   reached, because namespaces starved, so it is still unmeasured.

2. **Which order should the drain use within a type?** (Phase 2.)
   **Answered while planning phase 2: primary key order, round robin
   across types.** This replaces the earlier recommendation of oldest
   first. `object_states` has no index that can return a type's
   final-state rows in `update_time` order. Oldest first would need a
   new index on the cluster's largest table, or a sort of up to 639k
   rows for every page. Walking the existing primary key `(object_type,
   object_uuid)` with a keyset cursor is bounded and index-served. It
   is also the shape [Bound gRPC reply sizes](PLAN-grpc-bounded-replies.md)
   already specifies for this RPC. The order within a type does not
   change what the mission needs: every object is reached within one
   pass of its type's cursor. See phase 2's D8.

3. **Where should the sweep run?** (Phase 2.) Today it shares the
   elected maintainer's `schedule.run_pending()` loop, and the
   watchdog is only petted between jobs (WatchdogSec=300s). A larger
   budget inside that loop competes with every other scheduled job and
   with the watchdog. The options are a bigger budget, a higher
   cadence, or a dedicated thread with its own pet. **Answered while
   planning phase 2: stay in the maintenance loop, run every minute,
   and make the budget a configuration option.** Phase 1 shows that
   the time goes on database commits, so a thread would only add
   concurrency against the tier that is the bottleneck, and it would
   bring a second sweeper after a lost election. See phase 2's D10.

4. **Batch the deletes, or keep per-object `hard_delete()`?**
   (Phase 3.) Several types' `hard_delete()` is nothing but table
   deletes keyed on the uuid (operations, namespace keys, IPAMs,
   artifacts, interfaces). Those could be one bulk `DELETE ... WHERE
   uuid IN (...)` per table, behind new `DatabaseService` RPCs (see
   the `add-grpc-service` skill). Instances, namespaces and blobs have
   cascades with side effects (placement release, claim release,
   blob transfers) that must stay per object. **Recommendation:**
   batch where the hard delete is pure deletes, keep the rest per
   object. Phase 1's numbers strengthen the case. If the cost is a
   per-commit flush, which is the likeliest reading, then deleting a
   batch in one transaction pays that cost once per batch, not three or
   four times per object. **Answered while planning phase 3: batch
   the pure-delete types, which turn out to be 25 of the 29.** Blobs
   are among them: their transfer rows are a delete keyed on the blob,
   not a cascade. Instances, namespaces, namespace claims and nodes stay
   per object. See phase 3's D14-D16.

5. **How should sfcbr's backlog be drained?** (Phase 4.) At the
   target throughput, 1.6M objects is still days of work, and a drain
   that runs flat out becomes the database load it was meant to
   remove. sf-database's ceilings are already known to be tight: the
   MariaDB connection ceiling and the labqnap buffer pool behind the
   watchdog SIGABRTs. **Recommendation:** drain with the production
   sweep, throttled, and watch database load while it runs. Do not
   use a one-off SQL delete, which would skip the cascades in question
   4.

6. **Does the hard-delete audit event outlive the object?**
   (Phase 1.) `DatabaseBackedObject.hard_delete()` deletes the
   object's `event_objects` rows and then calls `add_event(...,
   'hard deleted object')`, which writes a fresh `event_objects` row
   for the object it has just deleted. **Answered while planning
   phase 1: harmless.** Stage A of the daily events prune deletes
   `event_objects` rows by the age of their event, so that row expires
   with ordinary retention. It is bounded, not a leak.

## Execution

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

!!! note "In this project"

    The same term is written twice: once in the phase table
    below, and once in the row this plan carries in
    `docs/plans/index.md`. Keep them in step -- the index row is
    the whole-plan status, so it only reaches `Complete` once
    every phase has been completed, abandoned or superseded.

Phase plans are written one at a time with the `next-phase` skill,
each when the phase before it has merged. Phase 1's numbers decide the
shape of phases 2 and 3, so only their scope is fixed here.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Measure the sweep | [PLAN-deleted-object-sweep-phase-01-measure.md](PLAN-deleted-object-sweep-phase-01-measure.md) | Complete | 6537966c00 |
| 2. A fair, bounded drain | [PLAN-deleted-object-sweep-phase-02-fair-drain.md](PLAN-deleted-object-sweep-phase-02-fair-drain.md) | Complete | dd5723c3db |
| 3. Cheaper hard deletes | [PLAN-deleted-object-sweep-phase-03-batched-deletes.md](PLAN-deleted-object-sweep-phase-03-batched-deletes.md) | In progress | — |
| 4. Drain sfcbr and keep it drained | PLAN-deleted-object-sweep-phase-04-drain.md | Not started | — |
| 5. Push audit | - | Not started | — |

### Phase 1: measure the sweep

Planning effort: medium. What gets measured is decided here, and the
instrumentation follows patterns that already exist (Prometheus
gauges in `scheduled_tasks.py`, such as `SWEEP_WORK_LIST_FAILURE_STREAK`).

Ship durable instrumentation, not a one-off script, because phase 4
needs the same signals to prove the drain:

- A gauge of queue depth per object type, set at fill and updated as
  the drain proceeds.
- A histogram of hard-delete wall time per object type, plus enough
  per-step timing (hydrate, state re-read, each subclass delete, the
  base deletes, the event) to answer open question 1. A DEBUG-level
  structured log line per object, sampled, is acceptable for the
  per-step part if a histogram per step is too many series.
- The per-type backlog count #4473 could not get without a direct
  database read. The fill already reads one list per type, so the
  depth gauge at the first pass after a fill *is* that count, and no
  one-off SQL query is needed (corrected while planning phase 1).

Then deploy to sfcbr, collect at least 24 hours, and write the numbers
into this plan's Situation section. Answer open question 1 and check
#4399 against the result. Open question 6 was answered while planning
phase 1.

Definition of done: the instrumentation has merged and is publishing
from sfcbr's leader. The measurement can only start after the phase
lands, so the numbers, and the revision of phase 2's and phase 3's
scope to match them, are recorded by phase 2's planning commit.

### Phase 2: a fair, bounded drain

Planning effort: high, because it touches the elected maintainer's
watchdog and job-sharing behaviour.

- Replace the single FIFO with per-type work, drained round robin, so
  one type's backlog cannot starve another (mission 2).
- Refill a type when its own work runs out, not only when everything
  is empty, so a restart is no longer what keeps the sweep alive.
- Bound each fill. Ask for a page per type in primary key order
  (open question 2), rather than every uuid. [Bound gRPC reply
  sizes](PLAN-grpc-bounded-replies.md) has not started, so this phase
  adds the paged `GetObjectsByState` in the shape that plan already
  specifies, and that plan's phase 2 reuses it.
- Answer open question 3 and set the budget and cadence to match.
- Keep the backlog visible. Paging means the sweep no longer holds
  the whole backlog in memory, so phase 1's depth gauge stops being
  the backlog. Replace it with a per-type count from the database
  (added while planning phase 2).
- Make `DeleteObjectEvents` visible to phase 1's timing scope, so
  that phase 3 is planned against complete numbers, including the
  namespace cascade that phase 2 reaches for the first time (added
  while planning phase 2).

Definition of done: a unit test showing that a type with a huge
backlog does not stop another type's objects from being hard deleted
in the same pass, and evidence that the same test fails against the
current single FIFO, so that it is known to test what it claims to.
After deploy, sfcbr's 24 hour numbers show hard deletes in every type
with a backlog. They are recorded by phase 3's planning commit.

### Phase 3: cheaper hard deletes

Planning effort: high. It may add `DatabaseService` RPCs, and it
changes the cascade code for objects that hold capacity.

Scope follows from phase 1, and from phase 2's numbers for the types
phase 1 never reached. Phase 1 found that the cost is in the deletes
that remove rows, at 50 to 250 ms each, and in `DeleteObjectEvents`.
So the candidates are now:

- Delete a batch of objects of one type in one database transaction,
  paying the per-commit cost once per batch (open question 4). The
  pure-delete types come first, as before.
- Include the event-reference delete in that batch.
- Confirm or refute the per-commit flush reading. The batch's own
  latency against its size answers it.

Dropping the `from_db()` hydration is **no longer a candidate**. Reads
cost about 2 ms, so two of them per object are under 1% of an
operation's cost. The cascades that release capacity (instance
placement, namespace claims) keep their per-object semantics, and
their existing unit tests must still pass unchanged.

*Revised while planning phase 3* (see "What phase 2 measured"):

- Phase 2's numbers confirm the per-commit reading as far as numbers
  short of a batch can. Every delete that removes a row costs 40 to
  50 ms, whatever it removes, and a delete of nothing costs what a
  read does.
- **Hydration is dropped after all, for the batched types,** for a
  reason the paragraph above did not weigh: each read is a request
  against the load budget, and sf-cluster's `GetObjectState` is
  already near its tolerance. The batch moves the state and age
  re-check into its own transaction instead (phase 3's D14).
- Phase 2's sweep runs every other maintenance pass, not every
  minute. Phase 3 fixes that (D19).
- Phase 3 adds a cap on objects per pass and defaults it near today's
  rate, so that this phase changes what a hard delete costs and phase
  4 changes how fast the backlog drains (D18).
- The batchable classes' own `hard_delete()` moves onto the batch as
  well, so each type's table list exists once and every caller's hard
  delete of those types is one commit (phase 3's D15, decided at its
  back brief). The capacity cascades keep their code. Where a
  namespace cascade test asserted a batchable child's individual
  deletes, it now asserts the child's batch call, so "their existing
  unit tests must still pass unchanged" holds for the cascades' own
  behaviour and not for those assertions.

### Phase 4: drain sfcbr and keep it drained

Planning effort: medium.

- Drain the existing backlog with the production sweep, throttled
  (open question 5), watching sf-database load with the `sf-db-load`
  skill while it runs. The throttle is phase 3's
  `DELETED_OBJECT_SWEEP_MAX_OBJECTS`, which phase 3 defaults to about
  phase 2's rate. Raising it is this phase's job.
- Add load-budget entries for the sweep's pairs once the drain rate
  is settled (phase 3's D23).
- Add a regression guard so that, once drained, a sweep falling
  behind is reported rather than discovered: an entry in the 33fl
  infra report reading phase 2's backlog gauge, or an alert,
  whichever matches how comparable signals are already reported.
- Document the sweep's tuning, and the regression guard, in
  `docs/operator_guide/database.md`, beside the metrics phase 1
  documents in "Watching for sweeps that silently do nothing".

Definition of done: the per-type gauge on sfcbr has stayed near the
arrival rate times the delay for a week, and #4473 is closed.

### Phase 5: push audit

Run `PUSH-AUDIT.md` over the merged ranges recorded in the table
above.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

!!! note "In this project"

    The runbook is `PUSH-AUDIT.md` at the repository root, and
    it reads its `$RANGE` from the `Merged` column, so a plan
    without one cannot be audited once its phases have merged.

    The column goes in the master plan's Execution table, last:

        | Phase | Plan | Status | Merged |

    `tools/check-plan-status.py` reads the `Status` column by
    name rather than by position, so a `Merged` column added
    after it does not disturb the index arithmetic. A phase
    which has not landed reads `—` -- in a table cell or a
    `**Merged**:` line alike -- rather than an empty cell (as
    the worked example in the `plan-file-conventions` block
    above leaves it), `TBD` or `n/a`.

    A few of this repository's older roadmaps write their
    phases as prose sections rather than as a table:
    `blob-storage-roadmap.md`, `api-query-batching-roadmap.md`
    and `PLAN-attribute-field-masks.md`, which are the three
    named in `check-plan-status.py`'s `HAND_COUNTED`. The
    first two carry a `**Merged**:` line in the phase's own
    section -- beside `**Status**` where the phase has one,
    otherwise directly under the heading. The third carries no
    such line, which is a decision rather than an oversight:
    it is `Complete` and has no push-audit phase, so the
    shared block's do-not-reopen rule leaves it alone. Nothing
    checks either form, so a wrong SHA is caught only in
    review.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    This repository does not track human review, so it has no
    `REVIEWS.md` and no `prune-reviews` workflow; the third rule
    has nothing to act on here. The first two apply as written.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving schema design, cross-daemon coordination,
    protocol changes, or subtle correctness questions (locking,
    consistency, migration safety) should be planned at high
    effort. Phases that mirror an already-established pattern --
    adding a new MariaDB accessor alongside an existing one, for
    example -- can be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    High effort typically means a new object type lifecycle, a
    cross-daemon protocol change, or migration logic; medium
    effort typically means a new gRPC endpoint parallel to an
    existing one, or a new MariaDB accessor; low effort typically
    means a rename, a log line, or regenerating proto stubs.

    A worked brief for this codebase: instead of "add MariaDB
    functions for the new object", write "add direct, gRPC, and
    public wrappers for `get_widget` in `shakenfist/mariadb.py`,
    mirroring the `get_artifact` trio at lines
    11129/11797/11811. The direct path uses
    `_get_widgets_table()`; the gRPC wrapper goes through
    `GetWidget` on the database daemon; register the counter in
    the Monitor operations list in
    `shakenfist/daemons/database/main.py`."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy).
    - [ ] If proto files changed, stubs were regenerated with
          `tox -e genprotos` and committed.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* Object or attribute filtering is pushed down to the
  MariaDB SQL layer where indexes can make it faster, rather
  than materialising everything and filtering in Python.
* There are unit tests for core logic and preferably
  functional test coverage as well (see
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests`).
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* gRPC proto changes (if any) have been regenerated with
  `tox -e genprotos`.
* Documentation in `docs/` has been updated to describe any
  new features, commands, or database changes. In particular
  `docs/operator_guide/database.md` has been updated for
  schema changes.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules, daemons,
  or object types.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans*
  table: the date the plan was written, a link to it, a
  one-line intent, its status from the vocabulary above,
  and its phase arithmetic (`0 of 7`, or `—` for a plan
  with no phases). One row per master plan, never one per
  phase — the phases are tracked in this plan's own
  Execution table, and duplicating them in the index is
  how the two drift apart. Rows run oldest first.
* **`order.yml`** — add an entry for the new master plan,
  in the intended reading order. This registers the plan
  and is what the plan-status check reads; it does **not**
  by itself put the plan in the published site navigation,
  which `mkdocs.yml.tmpl` currently lists by hand. The file
  is not ours: its format and its meaning as a per-directory
  navigation allowlist come from `tools/sync_component_docs.py`
  in shakenfist/actions, which is how a repository's docs are
  synced into a documentation site. Keeping it complete is
  what would make generating the nav from it a mechanical
  change later. Phase files should *not* be added to
  `order.yml`; they are linked from the master plan's
  Execution table only, which makes that table the only path
  to them.

The index row carries the whole-plan status, so it only
reaches `Complete` once every phase has been completed,
abandoned or superseded. Update it as the arithmetic
changes, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- **Errored objects are never hard deleted
  ([#3922](https://github.com/shakenfist/shakenfist/issues/3922)).**
  The comment above `FINAL_OBJECT_STATES` already calls the current
  rule backwards. It is left out because it adds to the arrival rate,
  and fixing it before phase 3 would make the backlog worse. Pick it
  up after phase 4.
- **Release namespace claims at soft delete.** The REST namespace
  delete revokes inbound trusts straight away because waiting for the
  hard delete would leave them standing (`auth.py:409-417`). Capacity
  claims carry the same argument and are not released until
  `Namespace.hard_delete()`. A faster sweep shortens that window but
  does not remove it. File it as its own issue.
- **Drop `hard_delete()`'s per-call fallback.** Phase 3 moves the
  batchable classes' `hard_delete()` onto the batch RPC and keeps a
  fallback that calls the old per-table functions when sf-database
  does not serve it (phase 3's D15). Once every supported
  sf-database serves the RPC, the fallback and the per-table public
  functions it alone calls can go.
- **Retention policy.** If phase 1 shows the 30 second `*_op` delay
  or the hour for everything else is itself a cost, record it here
  rather than changing it in this plan.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

None yet. This plan closes
[#4473](https://github.com/shakenfist/shakenfist/issues/4473). Related
issues to watch while working on it are listed under Situation.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
