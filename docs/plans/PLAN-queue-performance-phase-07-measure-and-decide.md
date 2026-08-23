# Phase 7 — re-measure the queue, and decide about fairness

Master plan: [PLAN-queue-performance.md](PLAN-queue-performance.md)

**Status: Complete.** This is step 7 of the master plan; the master
plan numbers its work in steps and this file is that step's detailed
plan, so "step 7" and "phase 7" are the same thing throughout. What
the measurement found is in "Findings" at the end of this document,
which corrects one thing this plan's own survey got wrong.

**Planning effort: high.** The mechanical part (extract a distribution
from a log stream) is easy. The judgement is in deciding what the
numbers mean: the pipeline has at least three distinct sources of
delay which all land in the same `wait_seconds` field, and calling any
of them "starvation" without separating them first is how this phase
gets the wrong answer.

## Why this phase exists

Steps 1-6 attacked a specific failure: `network_apply_update_dnsmasq`
ops waiting more than 60 seconds behind a serialised single-threaded
`sf-net` worker. They landed batched dequeue, worker-side and
enqueue-side coalescing, and a per-op latency event so the result could
be seen.

Step 7 is the measurement those changes were instrumented for, and the
decision that follows it: is the wait tail gone, or does the dispatcher
still need explicit fairness (bounded staleness, or reserved slots) so
that sustained higher-priority load cannot starve a lower-priority
queue?

## Scope

**In scope:**

* A committed, repeatable tool that turns a stream of Shaken Fist JSON
  log lines into a queue-wait distribution.
* A measured verdict from `sfcbr` over a window of at least 24 hours,
  and a second one from a cluster CI run.
* The fairness decision, written into the master plan with the numbers
  that justify it.
* Correcting the two documentation claims the survey found to be
  false (below), and closing out the master plan.
* Filing issues for latency sources this phase measures but does not
  fix.

**Out of scope:**

* Implementing fairness. If the verdict is "yes, we need it", that
  becomes step 8 with its own plan. A measurement phase that also
  changes the thing it measures cannot report a clean before and after.
* Fixing the two non-fairness latency sources the survey already
  found (fixed-delay dependency deferral; `background_high_io` waits
  behind the disk-busy gate). Both are real and both are someone
  else's plan — this phase files them.
* The two follow-ups the master plan already defers:
  `NodeNetOp.network_apply_create_hypervisor` coalescing, which needs
  multi-column target support in the find/claim primitives. Step 7's
  numbers inform whether it is worth doing; it is not done here.
* Changing event retention. The survey found the retention
  documentation is wrong; the *behaviour* may or may not be wrong, and
  deciding that is not this phase's job.

## What the survey found (2026-08-23)

### Steps 1-6 are genuinely on `develop`

The master plan's Status section said steps 1-6 were "implemented, on
the `network-facade` branch". They merged as PR #3194 on 2026-05-26 and
every artefact is present on `develop`:

| Step | Evidence |
|------|----------|
| 1. Visibility | `shakenfist/daemons/queues/workitem.py:159-171`, `shakenfist/daemons/network/workitem.py:384-401` |
| 2. Batched dequeue | `mariadb.dequeue_work_items()` (`shakenfist/mariadb.py:23290`); no singular `dequeue_work_item` remains |
| 3. Coalescible metadata | `BaseClusterOperation.coalescible_tasks` / `coalescible_target_column` (`shakenfist/operations/baseoperation.py:130`), `COALESCIBLE_TASKS` (`shakenfist/schema/operations/net_op.py:63`) |
| 4. Worker-side dedup | `BaseClusterOperation.execute` (`shakenfist/operations/baseoperation.py:294-380`) |
| 5. Enqueue-side dedup | `net_op.create_and_enqueue` (`shakenfist/schema/operations/net_op.py:105-153`) |
| 6. Caller-site audit | `reconciled_network_uuids` (`shakenfist/operations/node_inst_netdesc_op.py:315`) |

The master plan's Status wording has been corrected in this commit, and
step 1's description with it: the implementation did **not** emit a
separate `'started executing'` event at the pickup boundary. It folded
the wait fields into the existing end-of-op `'execution duration'`
event, on the grounds that a second event doubles the eventlog cost on
the dispatcher's critical path. `docs/operator_guide/networking/overview.md:507`
already documents the combined form correctly; only the master plan was
stale.

### The measurement is possible, but not the way the plan assumed

The plan says the `'started executing'` event distribution tells us
what we need. Reading those events back out of the database is not
possible after the fact:

* Cluster operations are hard-deleted **30 seconds** after reaching a
  final state (`_deleted_object_delay()` returns 30 for any object type
  ending in `_op`, `shakenfist/daemons/cluster/scheduled_tasks.py:735`;
  `FINAL_OBJECT_STATES` includes `complete`,
  `shakenfist/constants.py:191`).
* `hard_delete()` calls `delete_object_events()`
  (`shakenfist/baseobject.py:689`), which drops the `event_objects`
  rows for that op (`shakenfist/mariadb.py:5835`). The `events` row
  survives, but orphaned — nothing joins it to an op, a queue, or a
  target — until the daily orphan sweep deletes it
  (`_direct_prune_orphan_events`, `shakenfist/mariadb.py:5639`;
  scheduled at `shakenfist/daemons/cluster/main.py:619`).
* There is no REST endpoint for cluster operation events, and
  `get_object_events()` is per-object only.

So `docs/operator_guide/networking/overview.md:563` — "The `execution
duration` event is retained for `MAX_USAGE_EVENT_AGE` (default 30
days)" — is false twice over: the event is unreachable from its object
after ~30 seconds, and its row is gone within a day. Step 7d corrects
the documentation. Whether a 30 second window is the *right* retention
for an operation's audit trail is a separate question and is filed, not
answered here.

What does work is the log stream. `eventlog.add_event_multi` echoes
every event as an `Added event` log line whenever `LOG_EVENTS_TO_LOKI`
is set, which it is by default (`shakenfist/eventlog.py:96`,
`shakenfist/config.py:702`). The echo carries the full `extra` dict.
A live sample from `sfcbr` confirms it:

```json
{"logger_name": "shakenfist.eventlog", "message": "execution duration",
 "event_type": "usage",
 "extra": {"seconds": 0.1026, "wait_seconds": 2.0639, "defer_count": 0,
           "queue_name": "7ce66641-...-clusteroperation-user_facing"},
 "node_inst_op": "1ed3668d-351c-4f86-b1aa-f86547ce1926",
 "program": "sf-queues"}
```

The op's uuid appears as a key named for its object type, which is how
the analysis attributes a wait to an operation class without a join.

### The original tail is gone

A 9h35m sample from `sfcbr` (2026-08-23 08:14Z to 17:49Z, the most
recent 5,000 `execution duration` events, so the window is complete for
those events), grouped by operation type:

| Op type | n | p50 | p90 | p99 | max |
|---------|---|-----|-----|-----|-----|
| `net_op` | 1612 | 0.79 | 1.83 | 11.30 | 23.75 |
| `node_inst_op` | 1574 | 0.78 | 1.75 | 2.10 | 3.76 |
| `node_net_op` | 657 | 0.99 | 1.85 | 2.21 | 4.02 |
| `node_inst_netdesc_op` | 288 | **15.74** | 16.93 | 18.18 | **215.69** |
| `artifact_fetch_op` | 271 | 0.96 | 1.88 | 2.07 | 2.11 |
| `net_macaddr_ip_op` | 186 | 0.92 | 2.05 | 6.74 | 6.82 |
| `node_blob_op` | 139 | 2.00 | **568.27** | 583.48 | **583.57** |
| `net_iface_op` | 131 | 0.96 | 1.90 | 2.35 | 2.39 |
| `net_iface_ip_op` | 131 | 0.93 | 1.97 | 5.74 | 6.98 |

`net_op` — the family that contains `network_apply_update_dnsmasq`, and
the whole reason this plan exists — now runs at a p90 of 1.83 seconds
against a >60 second starting point. That is the headline result and
the phase should say so plainly.

### Three floors and two tails, which must not be confused

This is the judgement the phase turns on. `wait_seconds` is
`start_time - op.created_at`: insert to execution. Four different things
live inside it.

1. **The idle poll floor (~0.2-2.0 s).** The dispatcher polls with
   adaptive backoff capped at `IDLE_POLL_MAX_SECONDS = 2.0`
   (`shakenfist/daemons/daemon.py:78-80`, issue #3499). Almost every
   queue in the sample sits at p50 ~0.8 s and p90 ~1.8 s, which *is*
   that cap. It is not queueing and it is not unfairness; a verdict
   that treats a p90 of 1.8 s as a latency problem has measured the
   poll interval.
2. **Dependency deferral (15 s per defer, flat).** `defer()` takes
   `delay=15` by default (`shakenfist/operations/baseoperation.py:393`)
   and the dependency-wait path in both dispatchers calls it with no
   argument. `node_inst_netdesc_op` runs on the `user_waiting` queue
   with a median `defer_count` of 1 and a median wait of 15.74 s
   against a median execution time of 1.71 s — one dependency wait is a
   flat 15 second tax on the most latency-sensitive queue there is. Its
   215.69 s maximum is 14 defers, exactly. This is the largest
   user-visible latency in the sample and it has nothing to do with
   fairness.
3. **Designed backpressure.** `Daemon.dequeue_job` omits background
   queues entirely unless there are two free worker slots past the
   user-facing reservation, and omits `*_high_io` background queues
   whenever `DISK_BUSY_PER_SECOND_METRIC` exceeds 800
   (`shakenfist/daemons/daemon.py:665-690`). `node_blob_op` on
   `background_high_io` shows a p90 wait of 568 s with a **zero** defer
   count and a p50 execution time of 6.1 s — consistent with a
   deliberately gated queue on a busy disk, not with `FIELD()`
   starvation. Distinguishing these two requires correlating the wait
   against the node's disk-busy metric over the same window, which is
   step 7b's real work.
4. **Actual queue wait**, which is what the fairness question is about,
   and which is whatever is left after the first three are accounted
   for.

The `FIELD()` starvation risk is real and documented at
`shakenfist/mariadb.py:22050-22055`. The sample's only candidate for it
is `networknode-background` (p50 4.29 s, p90 21.21 s against
`networknode-user_facing`'s 0.72/1.92), on 39 samples — too few to
decide on, which is why this phase wants 24 hours rather than an
afternoon.

### Nothing else in the master plan's step 7 was wrong

The audit findings table and the follow-ups section both still describe
the tree accurately.

## Decisions

1. **Measure from the log stream, not the events table.** The events
   are unreachable in the database within 30 seconds of an op
   completing (above), and the log echo carries every field the
   analysis needs. This also makes CI measurable, since CI already
   bundles the daemon logs.

2. **The tool reads newline-delimited JSON on stdin.** Not a Loki
   client. Shaken Fist's log lines reach three different places
   depending on where you are standing — Loki on `sfcbr` (via
   `loki-query`, an operator's personal helper that is not in this
   repository), the CI artifact bundle, and `journalctl -o cat` on a
   node — and all three yield the same JSON objects. A tool that reads
   stdin works in all three; a tool with a Loki client built in works
   in one and hardcodes home-lab specifics into the repository. It
   lives at `tools/queue-wait-report.py`.

3. **The report separates deferred from non-deferred waits.** Every
   percentile is reported twice: over all samples, and over
   `defer_count == 0` only. Without that split, `node_inst_netdesc_op`'s
   15 second dependency tax reads as a queueing problem, and the
   fairness question gets answered wrongly. The report also groups by
   queue *class* (`networknode`, per-node, per-network) and priority
   lane, not by raw queue name, since raw names embed uuids and never
   aggregate.

4. **A p90 at or below `IDLE_POLL_MAX_SECONDS` is "no wait".** The
   verdict must state the floor it is measuring against, and the tool
   must print it, so nobody later reads 1.8 s as a regression.

5. **A `background_high_io` wait is not starvation until the disk-busy
   gate is excluded.** Step 7b correlates the tail against
   `DISK_BUSY_PER_SECOND_METRIC` for the same node and window before
   attributing anything to queue ordering.

6. **This phase decides, it does not implement.** If the numbers say
   fairness is needed, step 7 records the decision, the shape it should
   take, and the evidence, and step 8 gets its own plan. This is the
   decision most likely to be argued with: it would be cheaper to add
   bounded-staleness ordering while the context is hot. It is refused
   because a measurement phase that changes the system it measures
   cannot produce a clean before-and-after, and because the survey
   already suggests fairness is *not* where the remaining latency is —
   spending step 8 on `FIELD()` ordering when the real cost is a flat
   15 second defer would be optimising the wrong thing.

7. **`sfcbr` is the primary measurement; CI is secondary.** CI is where
   the problem was first seen, but a CI cluster lives for under an hour
   and its load is a burst, so its tail is dominated by cold-start
   effects. `sfcbr` runs the same code under a real steady state and
   over 24 hours it can answer the starvation question. CI is measured
   too, because the original >60 s observation came from there and the
   phase should show the same measurement at the same site.

8. **Correct the retention documentation; file the behaviour.** The
   30-day retention claim is wrong and is fixed in step 7d. Whether an
   operation's events *should* vanish 30 seconds after it completes is
   a design question with an obvious cost either way, and it gets an
   issue rather than a decision here.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Write `tools/queue-wait-report.py`: reads newline-delimited JSON on stdin, ignores non-JSON lines, and keeps objects whose `message` is `execution duration` and whose `extra` contains `wait_seconds`. For each, derive: `wait_seconds`, `extra.seconds`, `extra.defer_count`, `extra.queue_name`, `program`, and the operation type, which is the object key ending in `_op` (e.g. `node_inst_op`) — that key's value is the op uuid. Classify `queue_name` by splitting on `-clusteroperation-` (and on `-network-`, which the per-network queues use): a target of literally `networknode` is class `networknode`, a 36-character uuid target is class `per-node` or `per-network` depending on the separator, and the suffix is the priority lane. Print three tables — by (queue class, lane), by operation type, and by lane alone — each with n, p50, p90, p99, max for `wait_seconds`, the same percentiles restricted to `defer_count == 0`, the median and p90 of `extra.seconds`, and the count with `defer_count > 0`. Print the sample window (min and max `ts`) and print the line `Idle poll floor: p90 at or below 2.0s is the dispatcher poll cap (IDLE_POLL_MAX_SECONDS), not queue wait.` Follow the repo style: copyright header, single quotes, 120 columns, `argparse`, no third-party dependencies beyond the standard library. Add `shakenfist/tests/test_queue_wait_report.py` with a handful of synthetic lines covering: a `networknode` queue, a per-node queue, a per-network queue, a deferred op, a malformed line, and a line that is not an `execution duration` event. |
| 7b | high | opus | none | Capture at least 24 hours of `execution duration` events from `sfcbr` and produce the verdict. The operator's `loki-query` helper reaches them: `loki-query '{job="shakenfist"} \|= "execution duration"' --tenant sfcbr --since 24h --limit 20000`. Note that the limit truncates from the *oldest* end, so check the printed window actually spans 24 hours and re-run with a larger limit if it does not. Feed the capture to `tools/queue-wait-report.py`. Then answer, in writing, with numbers: (a) is the `net_op` tail gone; (b) is any lower-priority lane starved — for each candidate, exclude the two non-fairness explanations first, using `defer_count == 0` percentiles for deferral and, for any `*_high_io` background tail, the node's `DISK_BUSY_PER_SECOND_METRIC` over the same window (available from the Grafana `shakenfist` dashboard on maui, or from `mariadb.get_node_metrics`); (c) what the largest remaining user-visible latency actually is. The expected shape of the answer from the planning survey is "the tail is gone, fairness is not needed, the remaining cost is a flat 15 s dependency defer" — treat that as a hypothesis to falsify, not a conclusion to confirm, and say so if the 24 hour window disagrees with the 9 hour one. |
| 7c | medium | sonnet | none | Run the same report against a cluster CI run and compare. Take the most recent successful `functional-tests.yml` cluster-CI run (see the `ci-status` helper), download its `bundle.zip` artifact, and find the Shaken Fist daemon logs inside it — note that the Loki dumps in these bundles have repeatedly been empty, so fall back to the per-node journals, which carry the same JSON lines. Feed them to `tools/queue-wait-report.py` and report the same three tables. State the sample size honestly: a CI cluster runs for well under an hour and the numbers are a burst, not a steady state. The comparison that matters is `net_op` at the site where the >60 s waits were originally observed. |
| 7d | medium | sonnet | none | Write the outcome up. In `docs/plans/PLAN-queue-performance.md`: replace step 7 with what was measured and decided, including the tables from 7b and 7c, set the Status section to Complete, and set the step 7 row of the Execution table. In `docs/plans/index.md`: set the plan's status and phase arithmetic. In `docs/operator_guide/networking/overview.md`, fix the retention paragraph at line 563 — the `execution duration` event is unreachable from its operation roughly 30 seconds after the operation completes, because cluster operations are hard-deleted then and `hard_delete()` takes their `event_objects` rows with them; the orphaned `events` row is removed by the daily prune. Say where an operator should look instead (the log stream), and mention `tools/queue-wait-report.py`. Do not restate the numbers in more than one place. |
| 7e | medium | sonnet | none | File the issues this phase deliberately does not fix, one each, each with the measured numbers from 7b: (1) a dependency defer costs a flat 15 seconds on the `user_waiting` queue, which dominates instance-start latency — `defer()`'s `delay=15` default at `shakenfist/operations/baseoperation.py:393` is a fixed poll where an event or a shorter first delay would do; (2) `execution duration` and every other operation event become unreachable 30 seconds after the operation completes, so an operator cannot review a completed operation's own audit trail; (3) only if 7b confirms it is not the disk-busy gate, the `background_high_io` wait tail. Search for existing issues before filing each — #3516 and #3773 touch agent-operation deferral and may already cover part of (1). |
| 7f | high | opus | none | Code review of everything this phase produced, per `CLAUDE.md`'s "perform a code review at the end of a plan". Read `tools/queue-wait-report.py` against its tests: the percentile function's behaviour on small samples, the queue-name classifier against a real capture rather than only the synthetic fixtures, and whether the `defer_count == 0` split is applied consistently in all three tables. Check that no number appears in two documents with two values. Raise concerns rather than fixing silently. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The 24 hour window is quiet and contains no contention, so the starvation question goes unanswered | 7b states the sample's contention explicitly (worker occupancy, queue depths). If the window is idle, the honest verdict is "no evidence of starvation under this load", and the phase says which load it did not test rather than claiming a clean bill of health. Reviewer checks the verdict does not overclaim. |
| A tail gets attributed to unfairness when it is the disk-busy gate or a dependency defer | Decisions 3 and 5 make the exclusions mandatory, 7a's report makes them mechanical (the `defer_count == 0` split), and 7f re-checks that they were applied. |
| `loki-query` retention is shorter than 24 hours for the `sfcbr` tenant | 7b checks the printed window before analysing. If retention is short, capture forward instead: run the query on a schedule for a day, or capture from the nodes' journals directly. |
| The CI bundle turns out to contain no usable log lines | Known and expected for the Loki dumps in the bundle (this has bitten before); 7c goes to the journals. If neither has them, 7c reports that fact — it is itself a CI observability defect worth filing — and the verdict rests on `sfcbr` alone, which decision 7 already treats as primary. |
| The report tool becomes a one-off that rots | It ships with unit tests and is run by two separate steps in this phase against two different inputs. |

## Definition of done

* `tools/queue-wait-report.py` exists, and
  `python3 tools/queue-wait-report.py < /dev/null` exits zero with a
  "no samples" message rather than a traceback.
* `shakenfist/tests/test_queue_wait_report.py` passes under `tox`, and
  covers a malformed line, a non-matching event, and a deferred op.
* A capture of at least 24 hours from `sfcbr` has been run through the
  tool, and the printed window in the report spans at least 24 hours.
* The master plan's step 7 states, with numbers, whether the `net_op`
  wait tail is gone and whether fairness is needed, and if fairness is
  needed it names the mechanism and the evidence.
* Every latency source this phase identified but did not fix has either
  an issue number or a sentence saying why it needs none.
* `docs/operator_guide/networking/overview.md` no longer claims the
  `execution duration` event is retained for 30 days, and no page
  states a retention for it that another page contradicts.
* `docs/plans/index.md`'s row for this plan and the master plan's
  Execution table carry the same status, and
  `python3 tools/check-plan-status.py` passes.
* `pre-commit run --all-files` passes.

## Findings

Executed 2026-08-23. All six steps done.

### The verdict

The 26 hour `sfcbr` window and the 33 minute CI window agree with each
other and with the planning survey: **the queue-wait tail this plan set
out to remove is gone, and explicit fairness is not needed.** The
numbers, and the three exclusions the verdict rests on, are written up
in the master plan under "What step 7 measured" and are not repeated
here.

The hypothesis this plan told step 7b to falsify rather than confirm
survived falsification. Two of the three tails were tested directly
against the alternative explanation rather than argued away:

* `node_inst_netdesc_op`'s 15.78 s median falls to 0.77 s once
  deferred operations are excluded, on 1,013 samples. That is the
  `defer_count == 0` split from decision 3 doing exactly the job it
  was added for.
* `node_blob_op`'s 403 s p90 was tested by measuring how much work its
  own queue completed during each of the eight worst waits: between
  1,281 and 1,334 seconds, which exceeds the wait itself because
  several workers run concurrently. A starved queue does no work
  during its wait; this one was saturated throughout. That test turned
  out to be stronger than the disk-busy correlation decision 5 called
  for, and was used instead of it.
* `networknode`/`background`'s 4.45 s median resolved the same way:
  during its largest burst the background lane ran 34.5 s of its own
  work in 29 s while the user-facing lane on the same queue ran 2.9 s
  in total.

### What the survey got wrong

One thing. This plan's survey listed three sources of delay inside
`wait_seconds` plus "actual queue wait". It missed a fourth queue
*class*: `artifact_fetch_op` enqueues against a target of literally
`any` when no node is specified
(`shakenfist/schema/operations/artifact_fetch_op.py:64`), which is
neither `networknode` nor a uuid. The first version of
`tools/queue-wait-report.py` classified those 27 CI samples as
`unknown`. Fixed in the tool as an `any-node` class, with a test. No
`any` queue appeared in the `sfcbr` window, so only the CI numbers
were ever affected, and only by being filed under the wrong row.

Everything else the survey asserted held, including the parts it was
least sure of: the events really are unreachable 30 seconds after an
operation completes, and the `~1.8 s` p90 floor really is the idle
poll cap and appears on every quiet queue in both windows.

### Deviations from the plan

* **Decision 5's disk-busy correlation was not used.** The
  same-queue-occupancy test above answers the same question from the
  log stream alone, without needing the node metrics, and is a
  stronger negative: it shows the queue was busy, not merely that the
  gate was open. Decision 5's requirement -- that a
  `background_high_io` tail is not starvation until the alternative is
  excluded -- was met, by a different exclusion.
* **The Loki capture had to be paged.** A single `query_range` request
  is capped at 5,000 entries, which is about nine hours of this
  cluster's traffic, and `--since` alone always returns the newest
  entries in its window, so it cannot walk backwards. The 26 hour
  capture was assembled from thirteen two-hour windows. Anyone
  repeating this should expect to page rather than raise `--limit`.
* **Only two of the three candidate issues were filed.** #3863 (the
  flat 15 second dependency defer) and #3864 (operation events
  unreachable 30 seconds after completion). The third,
  `background_high_io`, was conditional on 7b confirming it was not
  self-inflicted, and 7b found that it was, so there is nothing to
  file: a queue saturated with its own work is the system working.

### Follow-ups still not landed

The master plan's two deferred follow-ups are unchanged by this
measurement, and neither is now urgent.
`NodeNetOp.network_apply_create_hypervisor` coalescing would reduce
enqueue volume at node startup, but `node_net_op` measures a p90 wait
of 1.86 s -- the poll floor -- so there is no latency to win, only
work. Explicit fairness is answered above.

## Back brief

Before executing any step of this plan, back brief the operator on your
understanding of it and how the work you intend to do aligns with it.

One gate: **after step 7b, stop and report the verdict before writing
anything into the master plan.** The fairness decision is the whole
point of the phase, and it is much cheaper to argue about it from the
numbers than from a finished write-up.
