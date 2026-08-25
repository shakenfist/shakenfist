# PLAN: Queue performance phase 9 -- prove coalescing works

Planning effort: high. Review effort: medium.

## Why this phase exists

[PLAN-queue-performance.md](PLAN-queue-performance.md) reached
Complete at 8 of 8, and its own status section names two things it
left unproven:

* Step 7's measurement characterises a cluster in which coalescing
  was inert, because the fold and the enqueue-side dedup never
  matched a row until #3878 was fixed in phase 8.
* The fold's cost was never measured at all, and it has since grown
  `FOR UPDATE` locks and an UPDATE against a hot table.

Phase 8 found the defect by reading SQL, three months after it
shipped. What did not exist then, and still does not, is anything
which would have failed: #3879 records that
`grep -rln coalesc shakenfist/deploy/shakenfist_ci/` returns
nothing. This phase closes both gaps together, because they turn
out to need the same thing -- evidence that coalescing matched a
row in a running cluster, durable enough to assert on and to count.

## Scope

**In scope.**

* Making the worker-side fold's evidence durable, so it can be
  observed after the operation carrying it is gone.
* Functional CI coverage in `shakenfist/deploy/shakenfist_ci`
  asserting that coalescing matched a row on a real cluster.
* Instrumenting the fold's cost so it appears in the same event
  stream `tools/queue-wait-report.py` already reads, and teaching
  that tool to report it.
* Re-running step 7's measurement on `sfcbr` with coalescing live,
  and writing the result into the master plan next to the numbers
  it qualifies.

**Out of scope.**

* #3884, the multi-column coalescing key. That is phase 11.
* #3863, the flat 15 second dependency wait. That is phase 10.
* Fixing #3864 generally. A completed operation's events being
  unreachable 30 seconds later is a real defect with a much wider
  blast radius than coalescing; this phase works around it for one
  event rather than solving it. See decision 2.
* Adding a real MariaDB to the unit test suite. See decision 5.

## What the survey found

Nine findings. Three of them change what this phase should build,
and two of them are corrections to the master plan, made at source
in this same commit (see "Corrections made at source" below), so
nothing later in this phase needs to redo them.

1. **#3879's text predates the fix it was filed alongside, and the
   gap it describes is no longer the gap.** The issue says the only
   coverage is mocked, citing `test_baseoperation.py` (mocks the
   primitive) and `test_mariadb_work_queue.py` (mocks
   `_get_engine`). Phase 8 then added
   `shakenfist/tests/test_mariadb_coalescing.py`, which executes the
   real statements against a database and would have caught #3878.
   The remaining gap is narrower and differently shaped than the
   issue describes; this plan works to the survey, not to the issue
   text, and the issue is updated when the phase closes.

2. **The existing "real database" is sqlite, and the tests say so
   themselves.** `shakenfist/tests/dbfixture.py:14-15` builds an
   in-memory sqlite engine from `mariadb.py`'s own `sa.Table`
   definitions. That is enough to catch a join which can never match
   -- which is exactly #3878 -- and it is explicitly not enough for
   the locking half:
   `shakenfist/tests/test_mariadb_coalescing.py:25-32` records that
   SQLAlchemy's sqlite dialect emits nothing for `FOR UPDATE`, so
   every one of those tests runs uncontended.

3. **The fold's event cannot be observed by a functional test as
   things stand.** `BaseClusterOperation.execute` emits
   `coalesced sibling ops` through `self.add_event`
   (`shakenfist/operations/baseoperation.py:370`), and
   `DatabaseBackedObject.add_event`
   (`shakenfist/baseobject.py:349-356`) writes it against the
   operation's own uuid and nothing else. A cluster operation is
   hard deleted 30 seconds after reaching a final state
   (`_deleted_object_delay` returns 30 for any type ending `_op`,
   `shakenfist/daemons/cluster/scheduled_tasks.py:735-738`), and
   `hard_delete()` takes its `event_objects` rows with it. So the
   only durable record of a fold is a log line. This is #3864 seen
   from a second angle.

4. **The enqueue-side dedup has already solved that problem, for
   itself.** `net_op.create_and_enqueue`
   (`shakenfist/schema/operations/net_op.py:190-199`) emits
   `enqueue-side dedup: reused pending op` through
   `eventlog.add_event_multi` against *both* the operation and the
   network, with a comment saying it does so deliberately, "mirroring
   the 'coalesced sibling ops' event the worker-side fold emits on
   its survivor". The mirror is not actually symmetric: the dedup
   event lands on an object that outlives the operation, and the
   fold event does not.

5. **The fold is not deterministically reachable from a functional
   test, and that is by design.** Two guards stand in front of it
   (`shakenfist/operations/baseoperation.py:325-357`): it is skipped
   when `dispatcher_batch_size == 1`, and skipped unless the queue
   name starts with `networknode-`. More fundamentally, the
   enqueue-side dedup is the common path -- it returns the existing
   op's uuid rather than inserting a second row -- so a sibling only
   exists when two callers raced the dedup lookup. The fold is the
   safety net for that race, and a test which demands it fire is a
   test which demands a race happen on schedule.

6. **`get_network_events` is available to functional tests and is
   already used.** `shakenfist/deploy/shakenfist_ci/base.py:719` and
   `cluster_ci_tests/test_events.py:38`. That file also establishes
   the polling idiom this phase needs: events are eventually
   consistent because emitting daemons spool and drain in ~100 ms
   batches, so `test_network_events` polls for up to 30 seconds
   rather than asserting once.

7. **Both coalescing primitives are already counted, and neither is
   timed.** `claim_coalescible_siblings` and
   `find_existing_coalescible_op` are registered in the Monitor
   operations list (`shakenfist/daemons/database/main.py:6217-6218`)
   and incremented in the servicer (lines 259 and 286), so call
   *rates* are queryable from Prometheus today. They are `Counter`
   objects; sf-database defines no latency histogram for any RPC, so
   the ~200 ms figure recorded in `baseoperation.py:327` cannot be
   confirmed or refuted from metrics.

8. **`tools/queue-wait-report.py` reports nothing about
   coalescing.** It matches `execution duration` events and reports
   wait, execution, defer count and queue. Its own docstring
   explains why the log stream is the data source: operation events
   cannot be read back from the database after the fact, for the
   reason in finding 3.

9. **The `execution duration` event is emitted by the dispatcher,
   not by `execute()`.** Both dispatchers build the `extra` dict
   after `op.execute()` returns
   (`shakenfist/daemons/network/workitem.py:397-414`,
   `shakenfist/daemons/queues/workitem.py:160-171`) and read
   per-operation values off the operation object
   (`op.current_defer_count`). Anything measured inside `execute()`
   reaches that event the same way: as an attribute the dispatcher
   reads.

### Corrections made at source

Two claims in the master plan are wrong and are corrected in this
commit, so no later step has to work around them:

* It says phase 8 added "eight tests which execute the real
  statements against a real database". There are thirteen in
  `test_mariadb_coalescing.py` (plus six in
  `schema/test_net_op_coalescing.py`, which are not that kind of
  test).
* "a real database" reads as MariaDB and is sqlite. The distinction
  is load-bearing for this phase -- it is the whole reason finding 2
  leaves a gap -- so the master plan now says which database, and
  what that does and does not prove.

## Decisions

1. **Assert that coalescing matched a row, not that a particular
   mechanism fired.** The functional test's assertion is that at
   least one of the two coalescing events appears on the network:
   `enqueue-side dedup: reused pending op` or `coalesced sibling
   ops`. Both mean a coalescing query matched a row, and #3878
   broke both at once, so either one failing to appear across a
   burst is the signal. Demanding a specific one makes the test a
   race detector (finding 5). The test records which it saw in its
   test details, so a shift from one to the other is visible to a
   human reading a passing run without failing the run.

2. **Make the fold emit on the network as well as the operation.**
   Replace the `self.add_event` at `baseoperation.py:371` with an
   `eventlog.add_event_multi` against the operation and its
   coalescing target, exactly as the enqueue-side dedup already
   does four lines of code away. This is a code change inside a
   coverage phase, which is the decision a reviewer is most likely
   to argue with, so the reasoning in full:

   * Without it, decision 1's assertion has only one of its two
     halves, and the half it keeps is the one that does not prove
     the fold works.
   * The alternative -- fixing #3864 so operation events outlive
     their operation -- is a retention change affecting every
     operation type and every event, weighed against a per-operation
     cost the whole plan was written to reduce. That is a plan of
     its own, not a step here.
   * The alternative of asserting against the log stream from a
     functional test means teaching the CI suite to read journals
     from five nodes. `tools/queue-wait-report.py` already does that
     job, out of band, and that is where finding 8's work goes.
   * The precedent is already set and already commented, by the
     enqueue side, in the same direction.

   The event's `extra` payload does not change; only the objects it
   is attached to. The target object is derived from
   `coalescible_target_column`, so it stays correct for any future
   operation type that declares coalescing rather than being
   hard-coded to networks.

3. **Measure the fold's cost on the existing event, not a new
   one.** `execute()` records the wall-clock time spent inside
   `claim_coalescible_siblings` on the operation, and both
   dispatchers copy it into the `execution duration` event's `extra`
   as `coalesce_seconds` alongside `wait_seconds`, following finding
   9's pattern. Step 1 of the master plan established that a second
   event on the dispatcher's critical path is the thing not to do,
   and that reasoning has not changed. `tools/queue-wait-report.py`
   then reports it from data it is already reading.

4. **Report the skip reason too.** The fold has two guards and a
   third implicit one (no coalescible tasks in the job). A
   measurement that cannot distinguish "the fold ran and found
   nothing" from "the fold never ran" repeats the shape of #3878, in
   which zero was indistinguishable from disabled. `execute()` records
   which of those happened and the report counts them, so "coalescing
   is doing nothing" is always answerable.

5. **Real-MariaDB concurrency coverage for the `FOR UPDATE` half is
   filed, not built.** Finding 2 names it as the gap sqlite cannot
   close. Closing it properly means a MariaDB service in the unit
   test job and a test that runs two connections against it, which is
   test-infrastructure work benefitting far more than this plan --
   and it interacts with the snapshot-isolation constraint recorded
   in `docs/developer_guide/coding_rules.md`, where CI's MariaDB
   10.11 is blind to behaviour that 11.6.2 enforces. This phase
   files it as its own issue and says so in the master plan. The
   functional test does exercise the locking path on real MariaDB
   with real concurrency, non-deterministically; that is worth
   having and is not a substitute.

6. **The measurement is a `sfcbr` window, matched to step 7's
   method.** Same tool, same shape of window (roughly 24 hours of
   production steady state), so the numbers are comparable to the
   ones they qualify. The CI window step 7 also captured is not
   repeated: its value was showing the >60 s tail gone, that question
   is closed, and a 33 minute window says nothing useful about a fold
   which fires on races.

7. **The master plan's step 7 numbers are annotated, not
   replaced.** They are a correct measurement of a real system, and
   the phase 8 caveat above them is what makes them readable. The
   new numbers are added beside them under their own heading, saying
   what changed and what did not.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | low | opus | none | Add phases 9, 10 and 11 to the master plan's Execution table, write this phase plan, set the master plan's status back to `In progress`, and update its `docs/plans/index.md` row to `In progress` / `8 of 11` with an Intent reflecting the survey. Apply the two corrections under "Corrections made at source". Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit. |
| 9b | medium | sonnet | none | Make the fold's event durable. In `shakenfist/operations/baseoperation.py`, replace the `self.add_event(EVENT_TYPE_STATUS, 'coalesced sibling ops', ...)` call at line 370 with `eventlog.add_event_multi` against `[(self.object_type, str(self.uuid)), (<target object type>, str(target_uuid_attr))]`, keeping the `extra` payload byte-identical. Mirror the call shape in `shakenfist/schema/operations/net_op.py:190-199`. Derive the target's object type from the operation's `target_fields` mapping (`net_op.py:95-97` maps `network_uuid` to `ObjectType.NETWORK`) keyed by `coalescible_target_column` -- do not hard-code `'network'`, because phase 11 adds a second target column. Add a unit test in `shakenfist/tests/operations/test_baseoperation.py` asserting both object references appear; the existing tests there mock `mariadb.claim_coalescible_siblings`, so follow that pattern. Commit subject: "Emit the coalescing fold event on its target too." |
| 9c | medium | opus | none | Instrument the fold's cost and its skip reasons. In `BaseClusterOperation.execute` (`baseoperation.py:325-380`), time the `claim_coalescible_siblings` call and record it on the operation as an attribute; also record which of the guards fired (`batch_size_one`, `not_cluster_wide`, `no_coalescible_tasks`, or `ran`). Copy both into the `execution duration` event's `extra` in *both* dispatchers -- `shakenfist/daemons/network/workitem.py:409-414` and `shakenfist/daemons/queues/workitem.py:166-171` -- as `coalesce_seconds` and `coalesce_outcome`, following how `defer_count` is read off the op there. Both dispatchers must agree on field names; the report tool parses one stream from both. Do not add a second event. Unit tests for the new fields in the existing dispatcher tests. Commit subject: "Measure what the coalescing fold costs." |
| 9d | medium | sonnet | none | Teach `tools/queue-wait-report.py` to report coalescing. Add a section giving the `coalesce_seconds` distribution (same percentiles as the existing tables) and a count of each `coalesce_outcome`, both broken down by operation family the way the existing report is. Events without the fields are older-build traffic and must be skipped silently, not counted as zero -- the tool already ignores lines it does not recognise and the docstring explains why. Extend the module docstring to describe the new fields and where they come from. Commit subject: "Report coalescing cost in the queue wait report." |
| 9e | high | opus | none | The functional test. Add a new `TestCoalescing` class in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/` (a new `test_coalescing.py`; `test_events.py` is about the event system, not this). Allocate one network, then create several instances on it in a burst without awaiting each in turn, so several `network_apply_update_dnsmasq` enqueues overlap. Assert that at least one of `enqueue-side dedup: reused pending op` or `coalesced sibling ops` appears in `get_network_events` for that network, polling to a deadline the way `cluster_ci_tests/test_events.py:33-42` does and for the reason given there. Record which event(s) were seen, and their counts, via `self.addDetail` so a passing run still shows the mechanism. Read `shakenfist/deploy/shakenfist_ci/base.py` for the instance-creation and cleanup idioms before writing; follow the namespace-prefix pattern the other cluster tests use. The burst size is a judgement call: large enough that overlap is near-certain on a loaded CI cluster, small enough not to lengthen the suite materially -- justify the number chosen in a comment. Commit subject: "Add functional coverage for operation coalescing." |
| 9f | medium | opus | none | The measurement and closeout. Run `tools/queue-wait-report.py` over a `sfcbr` window of roughly 24 hours once 9b-9e are deployed there, per decision 6, using the `loki-query` invocation in the tool's docstring. Write the numbers into the master plan under a new heading beside "What step 7 measured", per decision 7: the `coalesce_seconds` distribution, the outcome breakdown, and whether the ~200 ms figure in `baseoperation.py:327` survives -- correct that comment if it does not. Also report the coalescing counter rates from Prometheus (`database_claim_coalescible_siblings_total`, `database_find_existing_coalescible_op_total`) as a cross-check that the two data sources agree. File the real-MariaDB concurrency issue from decision 5, update #3879 with what was actually built and close it, and set the phase status in both places. Commit subject: "Measure coalescing on a cluster where it works." |

## Risks and mitigations

* **9e is a flaky test waiting to happen.** It depends on
  overlapping enqueues on a shared CI cluster. Mitigation:
  decision 1 widens the assertion to either mechanism, which turns
  a race requirement into a burst requirement; the poll deadline
  follows the established idiom rather than a fresh guess; and the
  burst size is justified in a comment so a later flake has
  something to argue with. If it flakes anyway, the correct
  response is to widen the window or the burst, not to delete the
  assertion -- the phase exists because there was no assertion.
  The reviewer of 9e checks that the test fails when coalescing is
  disabled: temporarily emptying `COALESCIBLE_TASKS` must make it
  fail, and that mutation is run, not asserted.
* **9b changes an event's object references, which something may
  depend on.** Mitigation: the `extra` payload and message are
  unchanged, and the operation reference is kept, so every existing
  reader still sees what it saw. The change is purely additive in
  what it attaches to. The reviewer greps for the event message
  across the repository before approving.
* **9c adds work to the dispatcher's critical path, in a plan about
  reducing it.** Mitigation: the cost is one `time.time()` pair and
  two dict assignments on an event already being emitted; decision 3
  refuses the second event that would actually cost something. 9f
  reports the measured `seconds` distribution alongside, so a
  regression in dispatcher overhead is visible in the same output.
* **9f cannot run until 9b-9e are deployed to `sfcbr`.** The step is
  gated on a deploy this plan does not control. Mitigation: 9f is
  last and separable; if the deploy lags, 9a-9e land and 9f follows,
  with the phase staying `In progress` until it does. The phase is
  not complete on the strength of instrumentation nobody has read.
* **The phase proves the dedup and calls it proof of the fold.**
  Decision 1 accepts either event, so a run in which the fold never
  fires still passes. Mitigation: 9c's `coalesce_outcome` counting
  makes fold activity separately visible in 9f's report, on
  production data over a day rather than in one CI run. If that
  report shows the fold never running on `sfcbr` either, that is a
  finding for the master plan, not a silent pass.

## Definition of done

* `grep -rln coalesc shakenfist/deploy/shakenfist_ci/` returns at
  least one file -- the check #3879 opens with.
* The functional test has been observed to fail with
  `COALESCIBLE_TASKS` emptied, and that mutation run is described in
  the phase plan's results, not asserted.
* `coalesced sibling ops` appears in `get_network_events` output for
  a network whose operations have been hard deleted -- that is, the
  event outlives its operation.
* `tools/queue-wait-report.py` run against a stream containing the
  new fields prints a `coalesce_seconds` distribution and a non-empty
  outcome breakdown; run against a stream without them, it prints the
  same output it printed before this phase.
* The master plan states measured numbers for the fold's cost, from
  a cluster on which coalescing matches rows, and either confirms
  the ~200 ms figure in `baseoperation.py:327` or corrects it.
* Every claim in "Corrections made at source" is fixed in the master
  plan, and no fact about what the phase 8 tests prove is stated
  differently in the master plan, this plan, and
  `shakenfist/tests/test_mariadb_coalescing.py`.
* #3879 is closed with a note saying what was built, and the
  real-MariaDB concurrency gap from decision 5 is open as its own
  issue, linked from the master plan.
* `python3 tools/check-plan-status.py` passes and
  `pre-commit run --all-files` is clean.

## Back brief

Confirm before starting, and stop at the gate:

1. Restate decision 2 -- emitting the fold's event on its target --
   and whether the alternative of fixing #3864 instead is preferred.
   This is the one code change in a coverage phase and it is cheap
   to redo now and expensive after 9e is written against it.
2. Restate what 9e asserts, and confirm that "either coalescing
   event" is the intended assertion rather than a weakened one.
3. **Gate before 9e.** 9b and 9c change what a fold looks like from
   the outside. Confirm those two are reviewed and their shape
   agreed before the functional test is written against them.

## Results

To be completed as the phase executes.
