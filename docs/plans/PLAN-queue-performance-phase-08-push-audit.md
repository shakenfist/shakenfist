# PLAN: Queue performance phase 8 -- push audit

Planning effort: medium. Review effort: medium.

## Why this phase exists

Every step of [PLAN-queue-performance.md](PLAN-queue-performance.md)
has merged, and step 7 closed the question the plan was written to
answer. What has not happened is a single review of the plan's work
*as a whole*. Steps 1-6 were reviewed as part of PR #3194, a 105 file
`network-facade` refactor in which the queue changes were a minority
of the diff; step 7 was reviewed on its own. Nobody has looked at the
queue-performance changes as one body of work.

`PUSH-AUDIT.md` is the repository's audit template. It is normally a
pre-push gate, run against `develop...HEAD`. Here it runs
retrospectively, which changes the baseline but not the questions.

## Scope

**In scope.** The code this plan added or changed, across both
merges, audited under the `PUSH-AUDIT.md` headings: wave 1
mechanical checks, and wave 2's code quality, test coverage,
documentation and security reviews.

**Out of scope.** The rest of PR #3194. The `network-facade` refactor
is far larger than this plan and auditing it here would be auditing
somebody else's change under this plan's name. Where a queue-performance
change depends on a network-facade one, the dependency is noted rather
than followed.

**Out of scope.** Fixing anything the audit finds, unless it is
trivial or blocking. The plan's own convention -- established by
step 7 -- is that a review phase records and files, rather than
expanding into the work it discovers. Blocking findings are fixed
here because a blocking finding is by definition not something to
leave on `develop`.

## Decisions

1. **The audit baseline is the plan's commit range, not
   `develop...HEAD`.** `PUSH-AUDIT.md` assumes unmerged work. This
   work is merged, so `git diff develop...HEAD` is empty and every
   command in the template would report success against nothing. The
   baseline is instead the union of the two merges which carried this
   plan:

   | Steps | Merge | Range |
   |-------|-------|-------|
   | 1-6 | PR #3194 `57867532c` | `57867532c^1..57867532c` |
   | 7 | PR #3865 `2daebabc1` | `2daebabc1^1..2daebabc1` |

2. **Within PR #3194, the audit is restricted to the files this plan
   changed.** Auditing all 105 files would not be this plan's audit.
   The queue-performance footprint in that merge is:

   * `shakenfist/daemons/daemon.py` (batched dequeue, disk-busy gate)
   * `shakenfist/daemons/network/workitem.py` (dispatcher, defer backoff)
   * `shakenfist/daemons/queues/workitem.py` (dispatcher, wait event)
   * `shakenfist/daemons/queues/startup_tasks.py`
   * `shakenfist/operations/baseoperation.py` (queue lists, coalescing)
   * `shakenfist/schema/operations/net_op.py`, `node_net_op.py`, `util.py`
   * `shakenfist/mariadb.py` and `protos/database.proto`, restricted to
     `dequeue_work_items`, `find_existing_coalescible_op` and
     `claim_coalescible_siblings`

   `mariadb.py` changed by 1,057 insertions in that merge, the large
   majority of which is network-facade work. Only the three functions
   above belong to this plan.

3. **The audit is run inline, not by sub-agents.** `PUSH-AUDIT.md`
   describes four judgment agents. The findings are what the phase is
   for; the mechanism is not load-bearing, and the operator has asked
   in this session that sub-agents not be spawned. Each of the four
   briefs is worked through in turn against the same diff, and the
   findings are reported under the same four headings so the output
   is comparable to an agent-run audit.

4. **Wave 1's exit condition is relaxed in one specific way.**
   The template says to stop if `pre-commit` or `tox` fails. Those
   run against the working tree, which is `develop` plus this phase's
   documentation -- so a failure would be a pre-existing failure on
   `develop`, not something this plan introduced. If wave 1 fails,
   record it, check whether the plan's own diff is implicated, and
   continue to wave 2 rather than stopping. Stopping would only be
   correct if this branch were about to be pushed as code.

5. **Findings are graded blocking or advisory, and blocking findings
   are fixed in this phase.** Advisory findings are filed as issues
   and listed here. A finding which is real but out of this plan's
   scope -- network-facade code reached by a queue-performance change
   -- is filed and named as such.

6. **A clean audit is a result.** If a heading finds nothing, it says
   so in one sentence. An audit which reports nothing under every
   heading is recorded as such rather than padded.

## Step plan

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 8a | low | opus | none | Add phase 8 to the master plan, write this phase plan, register it in `docs/plans/index.md`, and set the master plan's status back to In progress. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit. |
| 8b | low | opus | none | Wave 1. Run `pre-commit run --all-files` and `tox`. Run the template's style greps against the two ranges in decision 1 rather than `develop...HEAD`. Confirm whether proto stubs are fresh, given `protos/database.proto` is in scope. Record results. |
| 8c | medium | opus | none | Wave 2 mechanical sweep plus the 2a code-quality brief, over the decision-2 file list. The SQL-pushdown and cached-FK-list rules are blocking; check `dequeue_work_items`, `find_existing_coalescible_op` and `claim_coalescible_siblings` for the three-layer direct/gRPC/public pattern and registered Monitor counters. |
| 8d | medium | opus | none | The 2b test-coverage brief. `find_existing_coalescible_op` and `claim_coalescible_siblings` are concurrency primitives; check for adversarial coverage (the enqueue race the plan acknowledges in step 5, terminal-state siblings, an empty task list) and for functional coverage under `shakenfist/deploy/shakenfist_ci`. |
| 8e | medium | opus | none | The 2c documentation brief. The plan changed operator-visible queue behaviour and event payloads; check `docs/operator_guide/networking/overview.md`, `docs/developer_guide/`, `ARCHITECTURE.md` and `AGENTS.md` against the code, and apply the README, LLM-doc and plan-phase-reference shared blocks. |
| 8f | high | opus | none | The 2d security brief. Concurrency is the live area: the coalescing fold marks sibling operations complete from one worker while another may hold them, and the plan documents a routing invariant in `network/workitem.py` which the fold depends on. Check that invariant holds, check for SQL built by interpolation in the three new functions, and check whether the wait event leaks anything into the broadly-readable event log. |
| 8g | medium | opus | none | Grade every finding, fix the blocking ones, file the advisory ones, and write the results into this plan and the master plan. Set both statuses. |

## Risks and mitigations

* **The audit rubber-stamps merged code.** Reviewing something that
  already shipped invites confirming it. Mitigation: each heading must
  name what it actually examined -- a function, a file, a test -- and
  "nothing found" is only acceptable alongside that list. Step 8g
  checks this before writing the results.
* **Scope creep into network-facade.** The queue changes sit inside a
  much larger refactor and the boundary is a judgement call.
  Mitigation: decision 2 pins the file list; anything outside it is
  filed rather than fixed, and named as out of scope.
* **Blocking findings on merged code have no cheap fix.** A blocking
  finding here means something is wrong on `develop` right now.
  Mitigation: that is the point of running the audit; the fix lands in
  this phase's PR, and if it is too large for that, the phase says so
  and files it at high priority rather than silently downgrading it to
  advisory.

## Definition of done

* Every one of the four wave 2 headings has a written result naming
  what was examined.
* Wave 1's four commands and four style greps have been run against
  the decision-1 ranges, with output recorded -- not asserted.
* `git diff 57867532c^1 57867532c -- <decision 2 file list>` and
  `git diff 2daebabc1^1 2daebabc1` have both actually been read, not
  just summarised from the plan.
* Every finding carries a grade (blocking or advisory) and a
  disposition (fixed here, filed as #NNNN, or declined with a reason).
* No blocking finding is left unresolved. A finding is resolved when
  the defect it names is fixed; a *related* gap which the fix reveals
  but does not cause may be filed and tracked, provided the
  disposition table says so explicitly and grades the filed remainder
  advisory in its own right.
* The master plan's Execution table and `docs/plans/index.md` agree
  with each other and with `tools/check-plan-status.py`.
* The master plan's status is Complete only if no blocking finding
  remains open, under the definition above.

## Findings

The audit found one blocking defect: **cluster operation coalescing
has never worked**. Steps 4 and 5 of this plan -- the coalescing half
of a plan called "Queue performance and coalescing" -- have been inert
since PR #3194 merged on 2026-05-26. Filed as #3878, with #3879 for
the coverage gap that hid it.

### Wave 1

| Check | Result |
|-------|--------|
| `pre-commit run --all-files` | Pass |
| `tox` | Pass (py3, flake8, cover; 177s) |
| `tox -e genprotos` then `git diff --exit-code shakenfist/protos` | Pass, stubs fresh |
| Style greps, range A (steps 1-6) | Clean on all four |
| Style greps, range B (step 7) | Clean; see below |

Range B's raw grep output is not clean, and both hits are false
positives worth recording so the next run does not re-investigate
them. The fifteen "over 120 characters" hits are all markdown table
rows in plan files; restricted to `*.py` the count is zero. The
twenty-three `print(` hits are all in `tools/queue-wait-report.py`,
which is a report generator whose output is stdout. The grep exists
to catch debug prints left in daemon code and there are none.

Wave 1 was run against the working tree, which is `develop` plus this
phase's documentation, so per decision 4 a failure would have been a
pre-existing `develop` failure. There were none.

### Wave 2 mechanical sweep

Zero `TODO`/`FIXME`/`HACK`/`XXX` in either range. One `# noqa: E402`
in range A, on a deferred `eventlog_drainer` import, which is the
documented circular-import exemption. Zero `subprocess`/`os.system`/
`shell=True` in either range. Range B adds 22 test functions; range A
adds none *within the decision-2 file list*, which is an artefact of
that list excluding `shakenfist/tests/` -- PR #3194 in fact added
8,141 lines of tests. The file list should have named the test modules
explicitly; corrected in the reading, not in decision 2, so the
scoping error is visible.

### 2a. Code quality

Examined: `_direct_work_queue_dequeue_batch`,
`_direct_find_existing_coalescible_op`,
`_direct_claim_coalescible_siblings`, their gRPC and public wrappers,
`Daemon.dequeue_job`, and the queue-name helpers in
`shakenfist/operations/baseoperation.py`.

* **Three-layer pattern: satisfied.** `dequeue_work_items` at first
  looks like a public wrapper with no `_direct_`/`_grpc_` pair, but
  the pair is named for the `work_queue` family
  (`_direct_work_queue_dequeue_batch`), consistent with its siblings
  `resolve_work_item` and `restart_work_queue`. Both coalescing
  functions have the full trio and both gRPC handlers are registered
  in `shakenfist/daemons/database/main.py`.
* **SQL pushdown: no violations.** Zero new `mariadb.get_all_*(` call
  sites in either range. The dequeue does its ordering and filtering
  in SQL, which is the rule working as intended.
* **Cached FK list: no violations.** No new `list[str]` /
  `list[UUID4]` field on any `schema/*_attributes.py` model.
* **Advisory:** `_direct_claim_coalescible_siblings`'s docstring says
  `target_column` "can be interpolated into the ORDER BY safely".
  There is no `ORDER BY` in that function, and the column is not
  interpolated -- it is a `getattr(table.c, ...)` lookup used in a
  `WHERE`. The whitelist is real and correct; the sentence describing
  it points at the wrong mechanism, which is the kind of comment that
  misleads a reader about where the injection risk is. Not filed;
  small enough to fix alongside #3878, which touches the same
  function.

### 2b. Test review

Examined: `shakenfist/tests/operations/test_baseoperation.py`,
`shakenfist/tests/test_mariadb_work_queue.py`,
`shakenfist/tests/test_daemon_dequeue_job.py`,
`shakenfist/tests/test_daemon_worker_pool_high_io.py`, and
`shakenfist/deploy/shakenfist_ci/`.

* **Blocking, and the cause of #3878 going unnoticed for three
  months: coalescing has no test that executes its SQL.** The ten
  coalescing tests in `test_baseoperation.py` mock
  `mariadb.claim_coalescible_siblings`, so they assert the dispatcher
  decides to call the primitive. `ClaimCoalescibleSiblingsTestCase`
  mocks `_get_engine` and feeds canned rows to `fetchall`; its own
  docstring concedes it covers "the SQL-shape assertions", meaning it
  asserts the statement's shape and never that it matches a row. No
  functional coverage exists: `grep -rln coalesc
  shakenfist/deploy/shakenfist_ci/` returns nothing. Filed as #3879.
* The unit coverage that does exist is otherwise good, and notably
  covers the adversarial cases: empty task names, an invalid
  `target_column`, a malformed uuid, a dispatcher batch of one, an
  unset queue name, and dedup skipped when `depends_on` is present.
  Every one of those returns before the query runs, which is why they
  pass while the query itself is broken.
* Step 7's own tests are DB-free by construction and were reviewed
  under PR #3865; 22 tests, mutation-tested there.

### 2c. Documentation review

Examined: `docs/operator_guide/networking/overview.md`,
`docs/developer_guide/network_dispatcher.md`,
`docs/operator_guide/database.md`, `ARCHITECTURE.md`, `AGENTS.md`,
and the plan files.

* **No README, AGENTS.md or ARCHITECTURE.md growth** in either range
  that belongs in `docs/`. The shared-block disciplines are met.
* **No plan-phase references** leaked into `docs/` outside
  `docs/plans/`.
* **Worth recording as a positive:** the operator guide already names
  the exact diagnostic for #3878 -- "A *complete absence* of these
  during a CI run that's known to be enqueueing duplicate work would
  point at a bug in either the enqueue-side dedup ... or the
  worker-side fold". The documentation was right and predictive; what
  was missing was anything watching it. That is the argument for
  #3879 in one sentence.
* The documentation describes coalescing as working. It is accurate
  about intent and wrong about effect. Deliberately not corrected
  here: the fix for #3878 makes it true again, and editing the docs to
  say "this does not work" would be the wrong repair.

### 2d. Security review

Examined the same three SQL functions, the dispatcher event payloads,
and the routing invariant.

* **SQL injection: none.** Every filter value is bound through
  SQLAlchemy. The one dynamic identifier, `target_column`, is checked
  against the literal set `{network_uuid, instance_uuid, node_uuid}`
  before a `getattr(table.c, ...)` lookup which itself raises on an
  unknown column, and there is a test asserting a
  `malicious_column` argument returns before any query runs. The
  variable-length `FIELD(queue_name, ...)` ordering -- the obvious
  place to interpolate -- uses `sa.func.field(col, *queue_names)`,
  which parameterises.
* **Resource exhaustion: guarded.** `MAX_DEQUEUE_BATCH = 256` clamps
  `limit`, with a comment naming the gRPC handler as the trust
  boundary and noting production callers never reach it. That is the
  right reasoning for the right reason.
* **Credential handling: clean.** The wait event's `extra` carries
  only `wait_seconds`, `defer_count`, `queue_name` and `seconds`. No
  secret, namespace key or user-controlled string reaches the
  broadly-readable event log.
* **Concurrency: dormant rather than safe.** The fold transitions
  *other* operations to `complete` from one worker, and its safety
  rests on the routing invariant documented at
  `shakenfist/daemons/network/workitem.py:60-77` -- operations sharing
  a target always land on the same worker -- plus a `FOR UPDATE` and a
  `state_value = 'queued'` guard. The guards are present and correctly
  reasoned. They have also never run, because of #3878. This is
  recorded as informational rather than a finding: there is no live
  risk today, and the risk arrives the moment #3878 is fixed. It is
  the reason #3878 asks for functional coverage alongside the
  two-line repair rather than after it.

### Disposition

| # | Finding | Grade | Disposition |
|---|---------|-------|-------------|
| 1 | Coalescing join never matches, on two independent axes; steps 4 and 5 inert | Blocking | Fixed here, #3878 |
| 2 | No test executes the coalescing SQL | Blocking | Fixed here (`test_mariadb_coalescing.py`) |
| 2b | No functional coverage of coalescing in a live cluster | Advisory | Filed as #3879 |
| 3 | `claim_coalescible_siblings` docstring describes a mechanism it does not use | Advisory | Fixed here |
| 4 | Decision 2's file list omitted `shakenfist/tests/` | Advisory | Recorded above; no action |
| 5 | The fix adds two `# type: ignore[call-arg]` | Advisory | Accepted; see below |
| 6 | `network_ensure_mesh` is coalescible but does node-local work, so the fix would have activated a cross-node fold | Blocking | Fixed in review; see below |
| 7 | The fold's SQL cannot filter on queue, and nothing enforced the convention that made that safe | Blocking | Fixed in review; enqueue-time guard |
| 8 | Step 7's numbers were measured with coalescing inert, and the plan did not say so | Advisory | Fixed here |
| 9 | The enqueue-side dedup path emitted no event | Advisory | Fixed in review |
| 10 | The `ObjectType` and target-column skips were silent | Advisory | Fixed in review; both now log |
| 11 | The join and its preflight were duplicated verbatim in two functions | Advisory | Fixed in review; extracted |

Findings 1 and 2 were split apart on review. They had been graded as
one blocking item with a half-done disposition, which is not a state
the definition of done admits. The unit-level coverage is what
discharges the blocker -- it is the coverage whose absence let the
defect ship. Functional coverage is a real gap and is tracked as
#3879, but it is a standing gap in the suite rather than an unfixed
part of this defect, so it is graded advisory in its own right rather
than leaving a blocking row permanently open.

Findings 6 through 11 came from the automated reviewer on PR #3880 and
are recorded here because a push audit that does not record what the
next reviewer found would be claiming a completeness it did not have.
Finding 6 is the significant one, and it is a finding *about the
audit*: the audit verified that the coalescing join was broken and
that fixing it was correct, but did not ask what would happen once the
folding actually ran. See below.

Finding 5 is the audit's own mechanical sweep applied to the audit's
own change. `ObjectType` defines `__new__(cls, string, proto_id)`, so
mypy reads the by-value lookup `ObjectType('net_op')` as a constructor
call missing an argument, even though Enum resolves it by value at
runtime. The repository already carries exactly this suppression at
`shakenfist/node_health.py:204` and three places in `mariadb.py`, so
these match an established convention rather than introducing one.
Advisory, not blocking: the alternative is a helper on `ObjectType`,
which is a change to a widely used schema type and does not belong in
this phase.

### What the fix turned out to be

The audit reported finding 1 as a one-axis defect and estimated the
repair at two lines. Writing a test that executes the query against a
real database showed it was two independent mismatches in the same
`ON` clause, either of which alone is fatal:

1. **Uuid form.** `cluster_operations.uuid` is `sa.Uuid`, undashed
   CHAR(32); `object_states.object_uuid` is the dashed 36 character
   form. Fixed by routing through `_dashed_uuid_expr`, the helper the
   repository already had for this join.
2. **Enum form.** `object_states.object_type` is `sa.Enum(ObjectType)`
   and stores the enum *name*, `'NET_OP'`;
   `cluster_operations.operation_type` is a plain string holding the
   enum *value*, `'net_op'`. The join compared those two columns
   directly. A *bound* value would have been coerced by SQLAlchemy's
   Enum bind processor, which is why every other `object_states` query
   in `mariadb.py` gets away with passing a string -- but a
   column-to-column comparison has no processor to do the coercing.
   Fixed by binding `ObjectType(operation_type)`.

The estimate being wrong is the finding worth keeping: the original
audit reasoned from reading the code and got the count of defects
wrong, and only executing the query corrected it. That is the same
argument as finding 2, arriving from the other direction.

One thing the audit got wrong in the other direction, caught by
mutation-testing the new tests rather than by review: an intermediate
version of this fix also changed the `UPDATE` in
`claim_coalescible_siblings`, on the theory that it had the same enum
defect and would report siblings folded without actually folding them.
Reverting that change did not fail any test, which prompted a check of
the bind processor -- and the `UPDATE` binds a value, so it was always
correct. The change is kept for consistency with the `SELECT` and the
comment now says so rather than claiming a defect that never existed.

### On decision 5

Decision 5 says blocking findings are fixed in this phase, and the
audit initially declined to fix finding 1, on the grounds that
activating dormant coalescing is a behavioural change to a live
cluster. That reasoning still holds, and it is why the fix ships with
`shakenfist/tests/test_mariadb_coalescing.py`: eight tests which
execute the real statements against a real database, each one
mutation-tested against the defect it exists to catch. Two of them
fail if either half of the join regresses.

What that still does not give is functional coverage -- an assertion
that `coalesced sibling ops` fires in a running cluster. #3879 tracks
it, and until it exists the first production exercise of the fold will
be the first time the routing invariant at
`shakenfist/daemons/network/workitem.py:60-77` is tested with a fold
that actually fires. That is stated plainly rather than buried: this
change turns on a code path which has never run.

### What review found, and what the audit should have asked

The sentence above -- "this change turns on a code path which has
never run" -- was written as a caveat. It should have been read as a
question, and the automated reviewer on PR #3880 read it that way:
*given that it now runs, what does it do?*

The answer was a second defect (finding 6). `network_ensure_mesh` was
in `COALESCIBLE_TASKS`, and it is the one NetOp task that does
node-local work: `_apply_ensure_mesh` diffs *this* host's FDB. The
coalescing key is `COALESCIBLE_TARGET_COLUMN`, the network alone,
because `cluster_operations` has no queue column and NetOp's model has
no `node_uuid` -- the queue `target` is routing, and is never
persisted on the op row. So the fold could not tell hypervisor A's
mesh op from hypervisor B's.

Concretely: `network/network.py:316` and `external_api/instance.py:1091`
enqueue `[network_apply_create_network_node, network_ensure_mesh]` to
the cluster-wide `networknode` queue, while `network/network.py:980`
and `daemons/network/maintain.py:673,728` fan single-task
`network_ensure_mesh` ops out per hypervisor. Same op type, same
network, single task, state `queued` -- so the network node's survivor
matched them and marked them `complete`. Each of those nodes would
then drop its work item at the terminal-state branch and never update
its FDB.

This is the failure the comment at `baseoperation.py:335-353` says
broke `test_single_virtual_networks_work` on the network-facade
branch. The guard added at the time inspects only the *survivor's*
queue name, so it did not close it, and the invariant comment at
`daemons/network/workitem.py:63-66` claimed a soundness it did not
have: same-target routing is per-process, and two ops on two queues
are drained by two daemons.

The audit missed this because it asked whether the join was wrong --
which it was, and the fix for which is correct -- and did not ask what
the corrected join would then match. A dead code path has no
behaviour to audit; the moment you revive one, its behaviour is new
work, and reviewing the repair is not the same as reviewing what the
repair switches on.

Fixed in review, in three parts:

* `network_ensure_mesh` is no longer coalescible, with the reasoning
  recorded at the declaration.
* An enqueue-time guard raises `InvalidCoalescibleEnqueue` when any
  coalescible task is enqueued to a non-`networknode` target. The
  fold's SQL is structurally unable to filter on queue, so the
  convention that made it safe now has something enforcing it rather
  than three comments describing it.
* `shakenfist/tests/schema/test_net_op_coalescing.py` checks the guard
  at runtime and walks every `net_create_and_enqueue` call site
  statically, so a future per-node enqueue of a coalescible task fails
  the suite whether or not a test executes that line.

#3884 tracks the multi-column key -- and the `node_uuid` on the op row
it needs -- that would let per-node tasks coalesce within a node
properly, which is the optimisation this gives up.

**The plan is complete.** Steps 4 and 5 now do what the plan said they
did, the invariant they rely on is enforced rather than assumed, and
the coverage gap that hid the original defect is filed.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

Gate: after step 8b, report whether wave 1 passed before spending on
wave 2. If wave 1 fails for a reason unrelated to this plan --
decision 4 -- say so explicitly rather than presenting it as an audit
finding.
