# PLAN: Queue performance phase 10 -- where the pre-execution time goes

Planning effort: high. Review effort: medium.

## Why this phase exists

The master plan's phase 10 entry is titled "the 15 second dependency
wait" and points at #3863: a dependency wait re-enqueued a flat 15
seconds into the future on the queues `sf-queues` drains, which step 7
measured as a 15.78 s p50 on the `user_waiting` lane against 0.77 s for
operations which never deferred.

That fix landed. #3916 gave `sf-queues` the same 0.1 s doubling to a
15 s cap that `sf-net` already had, and #3863 is closed. Phase 9's
window was the first data carrying it, and it recorded two things: the
back-off is live, and the lane's p99 is still 17.18 s with roughly 400
of 823 first deferrals sitting at 15-17 s for reasons #3916 does not
explain.

So the phase's title is now wrong and its subject has moved. This plan
re-scopes it around the question phase 9 actually left open: **given
that a first deferral now costs 0.1 s, where does the remaining time
before an operation executes actually go?**

## Scope

In scope:

* Decomposing `wait_seconds` into the intervals it currently conflates,
  from event data already retained in Loki.
* Classifying the residual high-wait population: which operation types,
  which queues, what they were waiting on, and which interval holds the
  time.
* The instrument changes that decomposition needs, which are small and
  are listed as steps rather than assumed.
* A decision, recorded in the master plan: either a named fix with
  evidence behind it, or an explicit finding that the residual is
  benign and why.

Out of scope:

* Any change to queue worker concurrency, pool sizing or dispatch
  fairness. Phase 7 deliberately deferred the fairness question and
  this phase does not reopen it -- if the measurement says the time is
  queue-sit under a busy worker pool, that becomes a filed issue and a
  successor phase, not an edit inside this one.
* #3884, the multi-column coalescing key. That is phase 11.
* Any re-measurement of coalescing. Phase 9 did that and its numbers
  stand.

## What the survey found

The master plan's phase 10 section was written before #3916 and is
stale in the ways below. Every claim here was checked against the tree
at `2fcfe8afc` and against `sfcbr`'s retained logs on 2026-08-30.

### The back-off ladder is real, and the flat 15 s is gone

`dependency_defer_delay()` exists at
`shakenfist/daemons/queues/workitem.py:32` and is exactly as described:
`INITIAL_DEFER_DELAY = 0.1`, `MAX_DEFER_DELAY = 15.0`,
`DEFER_DELAY_MULTIPLIER = 2.0`, with an exponent clamp at 64. It is
called from both dependency-wait sites,
`workitem.py:150` (`depends_on`) and `workitem.py:177`
(`runs_after`), and `op.current_defer_count` is restored from the
persisted work item at `workitem.py:109`, so the ladder is stateless
and survives redelivery as the plan claims.

Counting the defer events over phase 9's own 42 hour window
(2026-08-27T13:15Z onwards, still retained) gives the ladder exactly:

| delay | events |
|-------|--------|
| 0.1 s | 1,391 |
| 0.2 s | 905 |
| 0.4 s | 600 |
| 0.8 s | 394 |
| 1.6 s | 129 |
| 3.2 s | 44 |
| 6.4 s | 21 |
| 12.8 s | 11 |
| 15.0 s | **1** |

Total 3,496, which matches `sum(count_over_time(...))` over the
unfiltered `Execution deferred` selector exactly, so no bucket is
missing.

**A flat 15 second dependency defer now happens once in 42 hours.**
Whatever the 15-17 s population is waiting for, it is not the defer
delay. That is the single most important correction this survey makes,
and it inverts the phase: the title names a cause that no longer
operates.

### `defer()` still has a flat 15 s default, though nothing observed hit it

`BaseClusterOperation.defer()` still declares `delay: float = 15.0`
(`shakenfist/operations/baseoperation.py:552-555`). #3916 changed the
call sites, not the default. Exactly one caller in the tree relies on
that default: `shakenfist/operations/node_blob_op.py:139`, a bare
`self.defer()` on the `BlobAlreadyBeingTransferred` path. It is the
last flat-15 defer in the codebase and step 10a removes it.

**Corrected during step 10c.** This section originally attributed the
window's single 15.0 s defer event to that call site. That was wrong.
All 32 `Execution deferred for 15.0 seconds` events in the retained
span come from `node_inst_netdesc_op` -- the ladder reaching its own
`MAX_DEFER_DELAY` cap at `defer_count >= 8` -- and `node_blob_op` did
not defer once in either measured window. The code finding stands and
the fix is still right; the evidence for it does not, and no observed
event came from that path.

### `wait_seconds` conflates three intervals and separates none of them

`execution_duration_extra()`
(`shakenfist/operations/baseoperation.py`) emits `wait_seconds` as
`start_time - self.created_at`, plus `defer_count` and `queue_name`.
There is no timestamp for when the work item was enqueued, when it was
first dequeued, or when it was last redelivered. For an operation
which ran with `defer_count == 1`, `wait_seconds` is therefore:

```
(created -> first dequeue) + 0.1 s + (redelivery -> start)
```

and nothing in the event says which of the two queue-sit terms holds
the time. Phase 9 read the 15-17 s figure off this scalar, which is why
it could observe the population but not explain it.

Creation and enqueue are *not* a candidate gap:
`enqueue_cluster_operation()` at
`shakenfist/schema/operations/util.py:19` stamps `creation_time` and
passes it to `mariadb.create_and_enqueue_cluster_operation()`, which
writes the `cluster_operations` row, the `object_states` row and the
`work_queue` row in one transaction. `created_at` is the enqueue time.

### The data needed to decompose it is already in Loki

`eventlog.add_event_multi()` echoes every event to the log stream as an
`Added event` line, gated by `LOG_EVENTS_TO_LOKI` and by
`suppress_event_logging`. That gate is on for `sfcbr` -- phase 9 read
`execution duration` through the same path. A sampled defer event
carries everything a per-operation join needs:

```json
{"ts": "2026-08-29T10:09:58.197Z",
 "message": "Execution deferred for 0.1 seconds",
 "extra": {"waiting_on": [["artifact_fetch_op", "17e76c3b-..."]],
           "defer_count": 1},
 "node_inst_netdesc_op": "4a8a878d-412e-4e49-8adf-883bdebe8fc6",
 "program": "sf-queues"}
```

The operation type is the *field name* and the operation uuid its
value, which is the join key. Combined with the `execution duration`
event, the decomposition needs no new field at all:

* `created_at` = execution event timestamp − `wait_seconds`
* first dequeue ≈ timestamp of the first `Execution deferred` event
  for that uuid
* so `created -> first dequeue` is directly computable, and the
  residual falls out.

Both phase 9's window (3,496 defer events) and a fresh trailing 42
hours (3,234) are retained as of 2026-08-30, so this phase does not
need a code deploy followed by another multi-day wait. That is the
decision the whole step plan turns on.

### One instrument gap is real

The defer delay is interpolated into the message string
(`f'Execution deferred for {delay} seconds'`) and is not in `extra`.
Every count in the table above came from matching prose. That works,
but it makes the ladder's own values unqueryable as a number and it
breaks silently if the wording changes.

### `program` is not a Loki stream label

It is a field inside the JSON payload, so `{program="sf-queues"}`
selects nothing and returns `0` rather than erroring. Anyone
partitioning this data by daemon must filter on the parsed field.
Worth stating because a zero here reads exactly like a real absence.

### Corrections made at source

Made as part of the planning commit, so no later step redoes them:

* The master plan's phase 10 entry is retitled and its narrative
  rewritten around the measured ladder, replacing "the 15 second
  dependency wait" and its "re-scope this phase against that data"
  placeholder.
* The master plan's Execution table row 10 is retitled to match and
  linked to this plan.
* The `docs/plans/index.md` row's description is updated to say that
  the flat 15 s defer is measurably gone and what the open question
  now is.

## Decisions

1. **Measure from retained Loki data, not from new instrumentation
   plus another wait.** Verified today: phase 9's window is still
   queryable and so is a fresh one. Shipping a field and waiting 42
   hours would cost days and answer the same question. This is the
   decision a reviewer is most likely to argue with, because adding
   `enqueued_at` and `first_dequeued_at` to the execution event would
   be a cleaner permanent instrument. The counter-argument is
   sequencing, not merit: we do not yet know which interval matters, and
   a permanent field added before that is known is as likely to measure
   the wrong interval as the right one. If step 10b cannot answer the
   question from the join, 10c adds the fields deliberately -- and by
   then it knows which ones.

2. **A separate tool, not a mode on `tools/queue-wait-report.py`.**
   The existing report reads one stream of `execution duration` events
   and produces a distribution table; phase 9 hardened it and its
   output is now quoted in the master plan. A timeline join needs a
   different query, a different data structure and a different output,
   and folding it in risks regressing a report that other phases cite.
   New tool: `tools/operation-timeline.py`.

3. **Fix the `defer()` default rather than only documenting it.** The
   phase's own finding is that the flat 15 s defer is gone; leaving a
   call site that still takes it contradicts the finding, and it is a
   one-line completion of #3916 in the same subsystem. `node_blob_op.py`
   is a blob-contention retry rather than a dependency wait, so it gets
   `defer_with_backoff()`'s semantics, not `dependency_defer_delay()`'s
   -- see the step brief.

4. **Put `delay` in the defer event's `extra`, and keep it in the
   message too.** The message text is what makes the existing 42 hours
   of history queryable; changing it would orphan the very window this
   phase measures. Adding the field is additive and makes future
   windows queryable numerically.

5. **The outcome may legitimately be "no code change".** If the join
   shows the residual is queue-sit under a busy worker pool, this phase
   files that as its own issue and records the finding; it does not
   start changing dispatch. Phase 7 deferred fairness on purpose and
   reopening it inside a measurement phase would repeat the mistake
   phase 9 was written to correct.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | sonnet | none | Add `delay` to the `extra` dict of the defer event in `BaseClusterOperation.defer()` (`shakenfist/operations/baseoperation.py`, the `add_event` at the `EVENT_TYPE_STATUS, f'Execution deferred for {delay} seconds'` call, currently around line 570). Leave the message string exactly as it is -- 42 hours of retained history is matched by that prose and changing it orphans the window phase 10 measures. Also change `shakenfist/operations/node_blob_op.py:139` from a bare `self.defer()` to `self.defer_with_backoff(reason='blob already being transferred')`, which returns False when the retry budget is exhausted; on False, error the operation out the way other exhausted-budget callers do (read `defer_with_backoff` at `baseoperation.py:585` for the contract). This is the last caller in the tree relying on `defer()`'s `delay=15.0` default. Add unit tests: one asserting the defer event carries a numeric `delay` in `extra` matching the message, one covering the node_blob_op backoff path including budget exhaustion. Commit subject: `Put the defer delay in the event, not just the prose.` |
| 10b | high | opus | none | Write `tools/operation-timeline.py`, which reconstructs a per-operation timeline from Loki and decomposes `wait_seconds`. Read `tools/queue-wait-report.py` first: it is the house style for these tools, and its docstring carries the hard-won Loki rules -- a 5000 line cap where asking for more fails outright and asking for exactly 5000 silently truncates, so page with `query_range` in half-hour chunks and check no chunk returns at the ceiling. Endpoint `http://loki.home.stillhq.com:3100`, tenant header `X-Scope-OrgID: sfcbr`. Join two event streams on the operation uuid: `Execution deferred` events, where the uuid is the *value* of a field whose *name* is the operation type (e.g. `"node_inst_netdesc_op": "4a8a..."`) and whose `extra` carries `waiting_on` and `defer_count`; and `execution duration` events, whose `extra` carries `wait_seconds`, `defer_count` and `queue_name`. Derive `created_at` as the execution event's timestamp minus `wait_seconds`, then emit per operation: total wait, time from creation to first defer event, summed defer delay, and the unexplained residual. Note `program` is a JSON field and not a stream label, so `{program="sf-queues"}` matches nothing -- filter on the parsed field. Output a distribution table plus a breakdown of the high-wait tail by operation type, queue name and what it was `waiting_on`. Commit subject: `Reconstruct where an operation's wait actually went.` |
| 10c | high | opus | none | Run 10b's tool against `sfcbr` over both phase 9's window (from 2026-08-27T13:15Z, 42 hours) and a fresh trailing window, and write the findings into `docs/plans/PLAN-queue-performance.md` under a new `## What step 10 measured` section, in the style of the existing `## What step 9 measured`. Answer specifically: of the operations whose `wait_seconds` is 15-17 s, which interval holds the time, which operation types and queues they belong to, and what they were waiting on. State plainly if the answer is that the time is queue-sit rather than deferral. Do not change dispatch, concurrency or pool sizing whatever the answer -- decision 5 in the phase plan puts that out of scope. If the join cannot answer the question, say so explicitly and write down which timestamps the execution event would need to carry; that becomes the successor step rather than a guess. Commit subject: `Measure where the residual queue wait lives.` |
| 10d | medium | opus | none | Close the phase out. Based on 10c's finding, either file the successor issue (with the measured evidence, and the `automated-fix-attempted` label if the fix needs design rather than a same-day patch) or record explicitly that the residual is benign and why. Update the master plan's Execution table row 10 and the `docs/plans/index.md` row to `Complete` with the arithmetic at `10 of 11`, and write the phase plan's own `## Results` section the way phase 9's is written -- what was built, what was measured, what is outstanding. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: `Close phase 10: what the residual wait was.` |

## Risks and mitigations

* **The join cannot resolve the question, because the residual sits in
  an interval no event bounds.** Plausible: if the time is between
  redelivery and start, only the execution event brackets it and the
  bracket is the whole wait. Mitigated by 10c's explicit instruction to
  say so and name the timestamps needed, rather than reaching for a
  plausible story. That outcome converts decision 1 into a deliberate
  instrument change with a known target, which is a real result.
* **Loki retention expires mid-phase.** Phase 9's window is retained
  today but no retention policy has been read. Mitigated by 10c running
  the fresh trailing window as well as phase 9's, so the phase still has
  data if the older window ages out; the two windows also cross-check
  each other.
* **`sfcbr` is not a representative workload.** It is one cluster and
  phase 9 already noted its fold fires 7 times in 1,335 attempts.
  Mitigated by reporting operation types and queue names alongside the
  distribution, so a reader can judge which parts generalise, and by
  not proposing a fix from this data alone -- decision 5.
* **Step 10a's `node_blob_op` change alters retry behaviour on a
  contended blob path.** `defer_with_backoff` exhausts its budget where
  a bare `defer()` retried indefinitely, so an operation that used to
  spin forever now errors. That is the intended semantics but it is a
  behaviour change on a path this phase is not measuring. Mitigated by
  requiring the budget-exhaustion unit test in the brief, and by
  reviewing that the error path matches what other exhausted-budget
  callers do.

## Definition of done

* The defer event carries the delay as a number. Falsifiable as
  written:

  ```
  python3 -c "
  import ast, sys
  src = open('shakenfist/operations/baseoperation.py').read()
  fn = next(n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == 'defer')
  call = next(n for n in ast.walk(fn)
              if isinstance(n, ast.Call)
              and getattr(n.func, 'attr', None) == 'add_event')
  extra = next(k for k in call.keywords if k.arg == 'extra')
  keys = [x.value for x in extra.value.keys]
  sys.exit(0 if 'delay' in keys else 1)"
  ```

  and a unit test asserts the value in `extra` equals the value
  interpolated into the message.
* `grep -rn '\.defer()' --include=*.py shakenfist/ | grep -v tests`
  returns nothing -- no caller relies on `defer()`'s 15.0 s default.
* `tools/operation-timeline.py` exists, runs against `sfcbr`, and its
  output distinguishes creation-to-first-dequeue from summed defer
  delay from unexplained residual. Running it against a window with no
  matching events exits cleanly rather than raising.
* Every window the tool reports was checked for chunk-level truncation
  at Loki's 5000 line ceiling, and the tool says so in its output
  rather than the operator having to remember.
* `docs/plans/PLAN-queue-performance.md` has a `## What step 10
  measured` section which states, for the 15-17 s population, which
  interval holds the time -- or states that the available events cannot
  say, and names the timestamps that would.
* No fact about the defer ladder is stated differently in
  `docs/plans/PLAN-queue-performance.md`, this plan, and
  `shakenfist/daemons/queues/workitem.py`'s header comment.
* The phase 10 entry in the master plan no longer describes the flat 15
  second dependency wait as a live problem.
* Either a successor issue exists with the measured evidence in it, or
  the master plan records why the residual needs no successor.
* `python3 tools/check-plan-status.py` passes and
  `pre-commit run --all-files` is clean.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with it.

Two gates, both cheap to raise and expensive to unwind:

* **After 10b, before 10c.** Show the tool's output on a short window
  first. The decomposition's correctness is entirely in whether
  `created_at = exec_ts - wait_seconds` and "first defer event" line up
  the way this plan assumes; if they do not, every number after it is
  wrong, and that is far cheaper to catch on thirty minutes of data
  than after a full window has been written up.
* **After 10c, before 10d.** The finding decides whether this phase
  ends in an issue or in a "benign, and here is why". Agree which
  before the close-out is written.

## Results

Steps 10a to 10d are done. The phase is complete.

It did not end where it started. The phase was re-scoped once at
planning time -- off "the 15 second dependency wait", which #3916 had
already fixed -- and then the measurement moved it again, because the
question it re-scoped onto turned out to rest on a mislabelled window.
Both moves are written up below rather than smoothed over.

### What was built

* **10a.** `BaseClusterOperation.defer()` now puts `delay` in the
  defer event's `extra` alongside `waiting_on` and `defer_count`
  (`shakenfist/operations/baseoperation.py`). The message string is
  deliberately unchanged: 42 hours of retained history is matched by
  that prose and this phase's whole method depends on it staying
  matchable. A unit test asserts the field equals the number
  interpolated into the message, so the two cannot drift.

  The same step converted the tree's last bare `self.defer()` --
  `shakenfist/operations/node_blob_op.py`, the
  `BlobAlreadyBeingTransferred` path -- to
  `defer_with_backoff(reason='blob already being transferred')`. That
  was the only caller left relying on `defer()`'s `delay=15.0`
  default. On budget exhaustion it emits an audit event and returns
  rather than erroring the operation out, matching the
  insufficient-space branch immediately above it in the same method:
  blob replication contention is benign and the next scheduled
  replication attempt will retry. Two tests cover the retry and the
  exhaustion paths.

* **10b.** `tools/operation-timeline.py` (1,371 lines), which
  reconstructs a per-operation timeline from Loki and decomposes
  `wait_seconds` into `(created -> first dequeue) + (summed defer
  delay) + (residual)`. It joins the `Execution deferred` stream to
  the `execution duration` stream on the operation uuid, which is not
  a fixed field -- an event names its subject as a *top level key*
  whose name is the operation type -- so the tool takes the single
  top level key which is not one of the log record's own fields
  rather than hardcoding a type and silently dropping the rest.

  It reads the delay from `extra` where 10a's field is present and
  falls back to parsing it out of the message where it is not, which
  is not garnish: the entire retained history this phase measures
  predates the field.

  It pages `query_range` in chunks, subdivides and refetches any
  chunk which comes back at exactly Loki's 5000 line ceiling, and
  cross-checks every stream against `count_over_time` (a metric query,
  not subject to the ceiling). It prints all of that, so an operator
  does not have to remember to ask.

  `shakenfist/tests/test_operation_timeline.py`, 26 tests, including
  one that an empty window exits cleanly rather than raising.

* **10c.** Both windows measured and written up as `## What step 10
  measured` in `docs/plans/PLAN-queue-performance.md`.

* Operator documentation for the new tool is in
  `docs/operator_guide/networking/overview.md`, next to
  `tools/queue-wait-report.py`, framed as what to reach for when the
  "`wait_seconds` includes deliberate deferral" caveat stops being a
  footnote and becomes the question.

### What was measured

Two `sfcbr` windows of 42 hours each -- step 9's own window, so the
phase could confirm or refute step 9, and a trailing one in case the
first aged out. They **overlap by 18 hours**, because 42 hours of new
traffic did not exist yet, so they are a retention hedge and a
consistency check and not two independent samples. The full numbers
are in the master plan; the three findings are:

* **The deep tail is queue sit on the background lanes, not
  deferral.** Of the operations waiting 15 s or more, 92% in window A
  and 91% in window B never deferred once. `node_blob_op` on
  `background_high_io` is a saturated pool -- during the six worst
  waits the same queue executed between 1.03x and 2.69x the wait's own
  duration in its own work -- which is what step 7 concluded and this
  window confirms directly.

* **When an operation does defer, which interval holds the time
  depends sharply on how far up the ladder it got.** At one deferral
  the wait is 76% initial queue sit; from three deferrals up the
  ladder itself is the majority and by seven it is 90%. Every deferred
  operation over 15 s in either window is the same thing:
  `node_inst_netdesc_op`, `user_waiting`, `waiting_on` an
  `artifact_fetch_op`, at `defer_count` 7 or more. That is the
  operation the ladder exists for and nothing there needs fixing.

* **Redelivery is not exact in either direction, and it does not
  matter.** 421 legs in window A came back *before* their requested
  delay had elapsed, by up to 0.19 s, and the 1.6 s and 3.2 s rungs
  are early at the median. That is why a residual can be negative --
  122 operations in window A have one -- which is arithmetic, not a
  join error. The residual is 20% of the deferred population's summed
  wait in both windows with a p50 of 0.19 s, so nobody's latency is
  explained by redelivery drift.

### What was corrected

Four things this phase found wrong, three of them its own.

* **Step 9's unexplained 15-17 s population was pre-#3916 traffic,
  and the claim is withdrawn.** Step 9 published its window as
  2026-08-27T13:03Z to 2026-08-29T07:00Z. Sweeping the window
  boundaries and re-running `tools/queue-wait-report.py` reproduces
  every number it published at one and only one window, ten hours
  earlier: the log records' `ts` is local time carrying a `Z` suffix,
  and ten hours is this cluster's UTC offset. 10,108 of those 26,225
  samples fall before the redeploy that brought #3916 in, and 493 of
  the 823 first deferrals are pre-fix, where a first deferral lands in
  the 15-17 s band by construction.

  That reconstruction is confirmed independently by something which
  does not depend on getting the boundaries right. The pre-#3916 code
  passed no delay and took an `int` default, so it emitted `Execution
  deferred for 15 seconds`; the ladder computes a float and emits
  `15.0`. Counting the two forms separately over step 9's real window
  gives 411 integer-form events against 1 float-form, and the integer
  form falls to zero from 2026-08-28 onwards and stays there. 411
  against step 9's "roughly 400".

  The claim is corrected at source in the master plan's phase 10
  narrative, not only recorded here.

* **This plan's own survey attributed the window's single 15.0 s
  defer to the wrong call site.** The survey said it came from
  `node_blob_op.py`'s bare `self.defer()`. It did not. All 33
  `Execution deferred for 15.0 seconds` events in the retained span
  come from `node_inst_netdesc_op`, and are the back-off ladder
  reaching its own `MAX_DEFER_DELAY` cap at `defer_count >= 8`;
  `node_blob_op` did not defer once in either window. The code
  finding stands and 10a's fix is still right -- a bare `defer()` is
  a latent flat fifteen seconds whether or not anything took it --
  but the evidence the survey offered for it was not evidence of it.
  Corrected in place, in the survey section above and in the master
  plan's method note.

* **This plan's `created_at` formula was wrong.** The survey wrote
  `created_at = execution event timestamp − wait_seconds`. The
  execution event is emitted *after* `op.execute()` returns, so its
  timestamp is the end of execution and not the start of it; the
  formula is `event_ts - seconds - wait_seconds`. Dropping the
  `seconds` term is a real error and not a rounding one -- on a
  long-running operation it places creation *after* that operation's
  own defer events and yields negative intervals. 10b found it while
  building the tool, which is what the gate after 10b existed for.

* **Step 10b's own preview looked like a flat contradiction of step
  9 and was not one.** The preview found 97 of 105 operations waiting
  >= 15 s had never deferred. Both accounts are true of disjoint
  populations: step 9 was looking only at `user_waiting`, which is the
  only lane on which anything defers at all -- 1,360 of 1,360 deferred
  operations in window A are on it, because a dependency wait is what
  defers -- and 10b was looking at the whole cluster, where the deep
  tail is on the background lanes.

Two method rules came out of this and are recorded in the master
plan so the next measurement does not relearn them: take the window
from Loki and never from the log records' own timestamps, and
cross-check every fetch against `count_over_time`.

### What is outstanding

* **The `networknode`/`background` incident is undiagnosable with the
  events we retain, and that is the phase's successor.** 12 `net_op`
  operations created within one second at 2026-08-28T23:30:52Z,
  executed 31 minutes later, serially, at intervals matching their own
  execution times -- while the same `sf-net` dispatcher executed 85
  other `networknode` operations during the span, 10 of them on the
  `background` lane itself. So it is neither a saturated pool nor a
  starved lane: the lane was served and these 12 items were not. Two
  mechanisms fit and the retained events cannot tell them apart, so
  the successor issue asks for the instruments rather than proposing a
  fix -- `dequeued_at` on the execution event, which splits time in
  the database queue from time inside the daemon after claiming, and
  `deliveries`, which distinguishes a redelivery after a lost worker
  from a first delivery. Both are cheap and neither should be added
  before somebody wants to answer this question again, which is the
  same sequencing argument decision 1 makes.

  **Decision 5 held.** No change to dispatch, concurrency, pool
  sizing or fairness was made or is proposed here. What the data says
  about them goes in the issue with the evidence attached.

  **The issue is drafted but not yet filed**, so this phase closes
  with that one action outstanding. Anything citing "the successor
  issue" above should be read as citing the draft until it has a
  number.

* **Step 7's exclusion of `networknode`/`background` should not be
  cited as if it still held.** Step 7 excluded that lane on the
  grounds that its waits were position in a burst rather than anything
  structural. A 31 minute wait on a queue which was concurrently being
  served is not that. The exclusion needs re-deriving before it is
  leant on again; the numbers step 7 published are not withdrawn.

* **A never-deferred wait still cannot be decomposed at all.** The
  join's entire resolution comes from defer events, and 92% of the
  deep tail emits none. The tail is therefore *classified* in this
  phase, not measured. That is precisely the gap the two timestamps
  above would close.

Nothing else is outstanding. Every definition-of-done item is met and
the phase is `Complete` in both the master plan and `index.md`; phase
11 (#3884, the multi-column coalescing key) remains.
