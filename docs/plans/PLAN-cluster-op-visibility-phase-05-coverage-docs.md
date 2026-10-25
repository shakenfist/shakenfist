# Cluster op visibility phase 5: CI helpers, coverage and documentation

This is a detailed phase plan under
[PLAN-cluster-op-visibility.md](PLAN-cluster-op-visibility.md).
Phase 4 put `has_pending_operations` on the instance, network and
artifact views behind the `object-pending-operations` capability, and
client-python#436 added `await_no_pending_operations()` and the
per-object listing accessors. This phase moves the functional suite's
await helpers onto that field, adds the one end-to-end check the earlier
phases could not write as unit tests, and puts right what the
documentation says about how long a finished operation is visible.

**Planning effort:** medium (per the master plan). **Review effort:**
medium. The code is test-only. The documentation step touches about
twenty sites, and the risk there is saying one fact two ways, not
saying it wrongly once.

## Scope

In scope:

* `_await_object_operations_complete()` in
  `shakenfist/deploy/shakenfist_ci/base.py`, rebuilt on the client's
  `await_no_pending_operations()`, with unit tests.
* `TestOutstandingOperationsBodyAll` moved onto the client's new
  wrappers for the body-path reads.
* One new merge-queue functional test, proving on a real cluster that
  the per-instance sweep's operations are stored as observational and
  never become `last_cluster_operation`.
* Every statement of how long a finished cluster operation stays
  visible, in `docs/`, swagger and code comments, made consistent with
  the one-minute deleted-object sweep. This absorbs #4603.
* A `docs/developer_guide/ci.md` section on waiting for an object's
  operations in the functional suite.
* A walk of the master plan's success criteria, recorded in this file,
  so the push audit (phase 6) starts from a known position.

Out of scope, deliberately:

* A fallback for servers that do not advertise
  `object-pending-operations` (decision 3).
* Anything that changes server behaviour. If a step finds a server bug,
  it files an issue and says so; it does not fix it here.
* The deleted-object sweep's cadence or backlog. That is
  [PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md)'s work.
  This phase only describes it correctly.
* The master plan's other Future work items (interfaces carrying a
  pointer, the four remaining `runs_after` sites, a fail-closed delete
  walk, the agent preflight not targeting its instance).

## What the survey found

The survey ran on 2026-10-25 against `develop` at `9efbff1481`. Phase 4
is complete. #4606 and its follow-up #4617 are on `develop`, and
client-python#436 merged on 2026-10-23. Most of the master plan's phase
5 premises hold. Two findings change the phase: the deleted-object sweep
now runs every minute, so phase 4's lifetime numbers were already
stale when they merged, and the boolean field cannot carry the error
check the helpers rely on today.

1. **The client side is ready.** client-python `develop` (`fdcfe6e`) has
   `get_instance_clusteroperations()`, `get_network_clusteroperations()`
   and `get_artifact_clusteroperations()` (`shakenfist_client/apiclient.py:887-945`)
   and `await_no_pending_operations(object_type, object_ref,
   namespace=None, timeout=None, interval=1)` (`apiclient.py:1897`). The
   helper raises `IncapableException` when the capability is missing or
   a view lacks the field, `TimeoutException` on timeout, and
   `ResourceNotFoundException` if the object disappears while it waits.
   CI installs the client from a checkout of client-python at the
   matching revision, not from PyPI (shakenfist/actions
   `.github/workflows/smoke-cluster.yml:7-32` and `:414-430`), so the
   suite can call these methods now.
2. **The helper today.** `_await_object_operations_complete()`
   (`base.py:1382-1425`) polls `list_cluster_operations_for_target()`
   every 10 seconds for an instance and every 5 for a network, for up to
   5 minutes. It fails the test on an op in `error` only if it saw that
   op outstanding earlier in the same wait. It reads neither op's
   `observational` flag, so a billing sweep that errors (#4585's
   family; #4608 stopped it erroring the *instance*, not the op) fails
   whatever test happened to be waiting. It has eight callers in five
   files: `instance_hotplug.py:72`, `:109` and `:218` (a mixin the
   smoke suites run on every PR), `smoke_ci_tests/test_networking.py:491`,
   `cluster_ci_tests/test_coalescing.py:561`,
   `cluster_ci_tests/test_api.py:37`, and the two thin wrappers in
   `base.py`. Its comment cites the master plan file, which the
   plan-history-in-source audit (#4630) discourages.
3. **The field cannot carry the error check.** `has_pending_operations`
   is a boolean, and an op in `error` is not pending. A helper that only
   polled the field would return happily after the hot-plug it waited
   for had failed, and the test would fail later, on an assertion that
   says nothing about the op. The error check has to come from a
   listing (decision 2).
4. **Nothing runs this suite against an older server.** The smoke
   workflow checks out shakenfist and client-python and deploys both
   (finding 1). `functional-tests.yml` and `scheduled-tests.yml` run the
   PR head or `develop`, and no job runs the suite against a released
   server. The master plan's "keep the by-target fallback for older
   servers" therefore describes a branch no CI job would ever take
   (decision 3).
5. **The deleted-object sweep runs every minute now.** #4595
   (deleted-object-sweep phase 2, merged 2026-10-22) moved
   `per_deleted_object_checks()` from every fifteen minutes to every
   minute (`shakenfist/daemons/cluster/main.py:1080`), within a
   `DELETED_OBJECT_SWEEP_SECONDS` budget (`shakenfist/config.py:777`,
   default 20). Operations become eligible thirty seconds after a final
   state (`daemons/cluster/scheduled_tasks.py:913-916`). Errored ops are
   still never hard deleted (#3922), so their target rows wait for the
   seven-day `CLUSTER_OPERATION_TARGET_RETENTION` prune. Three
   consequences:
   * Phase 4 merged a day after #4595 and said "about fifteen minutes"
     in five places, all wrong on arrival:
     `docs/developer_guide/api_reference/clusteroperations.md:41`, the
     `all` parameter's swagger text at `external_api/instance.py:2546`,
     `external_api/network.py:1073` and `external_api/artifact.py:1065`,
     and `docs/release_notes/v07-v08.md:1454`. The master plan repeated
     it three times.
   * The sixteen "thirty seconds" lines that #4603 and this survey found
     (listed in step 5d) are now close on timing, but most say an op
     *is removed* thirty seconds after it finishes, when thirty seconds
     is when it becomes eligible.
     #4603's own proposed wording ("up to fifteen minutes") is now wrong
     as well.
   * Running every minute is not the same as reaching an op within a
     minute. Once a pass has walked a type's final-state objects to the
     end, the sweep does not walk that type again for
     `DELETED_OBJECT_WALK_RECHECK`, five minutes
     (`scheduled_tasks.py:76` and `:1081-1084`). So even on an idle
     cluster a finished op is removed between thirty seconds and about
     six minutes after it finishes. *(Corrected during step 5c review:
     this bullet first said "within a minute or two".)* On a cluster
     whose sweep has a backlog, sfcbr today
     ([PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md)
     phase 4), removal waits longer, until the sweep reaches the op.
6. **The sweep coincidence cannot be provoked.** The master plan asks
   for a test that hot-plugs while a billing sweep fires.
   `per_instance_checks_and_usage()` runs every five minutes
   (`main.py:1068`) and enqueues one `node_inst_op` per `created`
   instance, with `collect_billing_statistics` and
   `health_check_kvm_process` at `user_facing` priority
   (`scheduled_tasks.py:552-602`). No API enqueues those tasks, so a test
   cannot make one land inside a given await. The only on-demand
   observational enqueue is the blob checksum GET
   (`external_api/blob.py:345`), whose op targets a blob, and a blob
   carries neither the pointer nor the field.
7. **`TestOutstandingOperationsBodyAll`**
   (`cluster_ci_tests/test_api.py:17-70`) makes three raw `_request_url`
   calls: two with `all` in the body, one with `?all=true`. The client's
   `get_network_clusteroperations()` sends `all` in a JSON body
   (`apiclient.py:920-925`), which is exactly the path #3629 broke. So
   the wrapper *is* the body path, and the call that has to stay raw is
   the query-string one, because the client never sends that. The master
   plan's "it must keep one raw call, because the raw body path is what
   it tests" had that backwards, and is corrected at source. With the
   one-minute sweep, the completed ops the test looks for are visible
   for between thirty seconds and about six minutes (finding 5), not up
   to fifteen minutes. The
   test reads straight after `_await_network_operations_complete()`, so
   that is enough, and no open issue reports it flaking.
8. **Cluster CI tests only run in the merge queue**
   (`docs/developer_guide/coding_rules.md`, "Cluster CI tests only run
   in the merge queue"). `test_api.py` and any new test under
   `cluster_ci_tests/` get no CI signal on the PR. The rule says to run
   them anyway, or at least import them in a scratch venv. The helper
   itself is exercised on the PR, through the smoke suites' hot-plug
   tests.
9. **The CI helpers have unit tests.** `shakenfist/tests/test_ci_*.py`
   load `base.py` by path, with stubs for `shakenfist_client` and
   `prettytable`, which are not test dependencies of this repository.
   `test_ci_capacity_wait.py` holds the pattern: `_client_stubs()`
   (line 66), `_load_ci_base()` (line 108), and `_WrapperHarness`
   (line 616), a `BaseTestCase` subclass that skips `setUp`.
10. **The top-level docs say nothing affected.** `ARCHITECTURE.md`,
    `AGENTS.md` and `README.md` never mention `cluster_operation_targets`,
    `last_cluster_operation` or `has_pending_operations`. They point at
    `docs/developer_guide/database_internals.md` (`ARCHITECTURE.md:57-63`,
    `AGENTS.md:21`), which phases 2 to 4 kept current. This repository
    has no `CLAUDE.md`. `docs/developer_guide/ci.md` documents the
    functional suite's other conventions ("Creating instances in the
    functional suite", line 134) but not how to wait for an object's
    operations. Another worktree (`shakenfist-wt-4407`, uncommitted)
    is adding a `ci.md` section directly after that one, and edits
    unrelated parts of `base.py`.
11. **Issues.** #4603 is open and unlabelled, with no branch. #3278 (the
    CI coverage tracking issue) is open. #4630, the plan-history audit,
    is being fixed in another worktree and does not name `base.py`.

Corrections at source, made in this planning commit: the master plan's
Situation bullet on terminal-op lifetime, its `all=true` open question,
its phase 4 summary, its phase 5 section (findings 3, 4, 6 and 7), and
its Future work entries for #4603 and #3278. Phase 4's plan is left as
written, because it records what was true when it was planned.

## Decisions

1. **The helpers wait through the client's
   `await_no_pending_operations()`.** They do not poll the field by
   hand. The point of the phase is that the functional suite should
   depend on the same contract a user would, and the client helper is
   that contract. Every hot-plug test on every PR then exercises the
   capability, the field, the view and the client helper end to end.
   The helper is passed `timeout=300`, keeping today's five minutes, and
   `interval=poll_interval`, keeping today's 10 and 5 seconds. A client
   `TimeoutException` becomes today's `self.fail()` message. I have not
   claimed the switch is cheaper. A full view GET and a by-target listing
   that hydrates a handful of ops cost about the same, and cost is not
   the reason for it.

2. **The error check moves to two listings around the wait.** Before
   waiting, the helper reads `list_cluster_operations_for_target()`
   once and records the UUIDs of ops already in `error`. After the wait
   it reads the listing again and fails the test, with today's message,
   on any op that is in `error`, is not `observational`, and was not in
   the first set. Observational ops are ignored, which is the master
   plan's phase 3 addition: a billing sweep that errors is recorded on
   the op and is not the test's business. This is stricter than today
   in one case and the same in the others. Today's check misses an op
   that goes from `queued` to `error` between two polls, because it
   never saw the op outstanding. The bracketing check catches it. An op
   that errors before the first listing is ignored, as it is today,
   because a helper that starts after the mutation returned cannot tell
   that op from history.

3. **No fallback for older servers.** This is the decision a reviewer is
   most likely to dispute, because the master plan asks for the
   fallback. Finding 4 is the reason: no CI job runs this suite against
   a server older than itself, so a fallback branch would never run, and
   an untested wait loop is the kind of code this plan exists to remove.
   If the capability is missing, the helper fails the test, saying the
   suite expects a server at its own revision that advertises
   `object-pending-operations`. A suite run against the wrong server
   then fails at once and says why. A fallback would quietly measure
   something else instead. If an upgrade job that runs this suite
   against a released server is ever added, it can add the fallback,
   with a job to test it.

4. **The end-to-end test checks the classification, not the
   coincidence.** The master plan's test needs a sweep to land inside
   one particular await, which a test cannot arrange (finding 6), so as
   written it would pass whether or not the fix worked. What the
   functional suite can prove, and the unit tests cannot, is that a real
   sweep op on a real cluster reaches the targets table as
   observational and is never named by `last_cluster_operation`. The
   new test creates an instance and waits for the sweep to reach it,
   bounded at eight minutes: the five-minute cadence, one fill and one
   processing pass, and margin. It then asserts that the sweep op is
   listed with `observational: true`, that the pointer never names it,
   and that `has_pending_operations` is false once nothing mutating is
   outstanding. The race itself is covered by the unit tests of phases
   2 to 4, and by the hot-plug tests, which now wait on the field
   (decision 1). The test goes under `cluster_ci_tests/`, so the PR does
   not pay for the wait and the merge queue does, in parallel with the
   rest of that suite. A blob checksum variant was considered and
   declined: a blob carries neither pointer nor field, so it could only
   check the stored flag, and phase 3's classification test already
   pins that.

5. **`TestOutstandingOperationsBodyAll` reads the body path through
   the client.** Its two body-path reads become
   `get_network_clusteroperations(uuid, all=True)` and
   `get_network_clusteroperations(uuid)`. The query-string read stays a
   raw `_request_url`, because the client never sends a query string
   (finding 7). The docstring says why. The test also asserts that
   every listed op carries an `observational` key, which phase 4 made
   part of the response and which nothing in the functional suite
   checks yet.

6. **#4603 is absorbed and re-scoped for the one-minute sweep.** The
   fact is stated in full in one place. `docs/operator_guide/database.md`
   already describes the deleted-object sweep (around line 968, inside
   "High-Churn Dedicated Tables"). That paragraph gets its own heading
   and becomes the canonical statement. A finished operation becomes
   eligible for hard delete thirty seconds after it reaches a final
   state, and the deleted-object sweep, which runs every minute, removes
   it and its target rows. Once the sweep has walked operations to the
   end it waits five minutes before walking them again, so removal
   normally comes within about six minutes of finishing, and later
   while the sweep is working through a backlog. Errored
   operations are never hard deleted, so their target rows stay until
   the `CLUSTER_OPERATION_TARGET_RETENTION` prune, seven days by
   default. Every other page gives the short form, "eligible thirty
   seconds after finishing and removed by the deleted-object sweep",
   and links to the canonical paragraph. Code comments and swagger text
   state eligibility only, and never the cadence, because the cadence
   belongs to another subsystem and has changed once already. Code
   comments are not allowed to drift with it. The PR says
   `Fixes #4603`.

7. **The functional-suite convention goes in `ci.md`; the top-level docs
   do not change.** A new section in `docs/developer_guide/ci.md`, "Waiting
   for an object's cluster operations", says that tests wait with
   `_await_instance_operations_complete()` or
   `_await_network_operations_complete()`, never by polling
   `last_cluster_operation`. It says what the helpers wait for, that
   observational ops never hold or fail a wait, and what fails a test.
   It sits before "Creating instances in the functional suite", not
   after it, to stay clear of the section the 4407 worktree is adding
   there (finding 10). `ARCHITECTURE.md`, `AGENTS.md` and `README.md` are
   left alone, because none of them describes the affected storage or
   API (finding 10), so the master plan's success criterion about them
   is met without an edit. No convention for agents changes. No comment
   goes on #3278: the `ci.md` section is where a test author will look,
   and the tracking issue would only restate it.

8. **Phase 5 closes the plan's implementation.** It records a walk of
   every master plan success criterion in this file's Execution notes,
   with the command or link that shows each is met. Phase 6, the push
   audit, then starts from evidence rather than from the table.

## Step plan

Run the steps in order, one at a time, in this worktree
(`shakenfist-wt-cov-p05`). Pre-commit's stash is repo-wide, so
concurrent agents in one tree can revert each other. Each step is one
commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | **Rebuild the await helper on the pending field.** Read decisions 1 to 3 and survey findings 1 to 4 and 9 of `docs/plans/PLAN-cluster-op-visibility-phase-05-coverage-docs.md` first. Rewrite `_await_object_operations_complete(self, object_type, object_uuid, poll_interval, label)` in `shakenfist/deploy/shakenfist_ci/base.py` (line 1382), keeping its signature and both wrappers below it unchanged. (1) If `self.system_client.check_capability('object-pending-operations')` is false, `self.fail()` with a message that the suite expects a server at its own revision that advertises `object-pending-operations`. (2) Read `self.system_client.list_cluster_operations_for_target(object_type, object_uuid)` and keep the set of `uuid`s whose `state` is `error`. (3) Call `self.system_client.await_no_pending_operations(object_type, object_uuid, timeout=5 * 60, interval=poll_interval)`. Catch the client's timeout exception and `self.fail()` with today's message (`'{Label} operations did not complete within 5 minutes for {uuid}'`). `base.py` defines its own `TimeoutException` at line 158, so name the client's explicitly (`apiclient.TimeoutException`). Confirm the class name in client-python with `git -C /srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python show origin/develop:shakenfist_client/apiclient.py`, because the local `develop` checkout there is stale. (4) Read the listing again, and `self.fail()` with today's error message for the first op whose `state` is `error`, whose `op.get('observational', False)` is false, and whose `uuid` is not in the first set. Delete `terminal_states` and `observed_outstanding`. Replace the leading comment with one that says why: the pointer can name a finished background op while an earlier one is still queued; `has_pending_operations` excludes observational work; and the field is a boolean, so errors come from the two listings. Do not cite plan files, phases or test names from history in the comment (the plan-history-in-source audit, #4630). Check that none of the eight callers listed in finding 2 waits on an object it has just deleted, because the client helper raises `ResourceNotFoundException` for that, where today's loop returned. Report what you find. Unit tests in a new `shakenfist/tests/test_ci_await_operations.py`, loading `base.py` the way `shakenfist/tests/test_ci_capacity_wait.py` does (`_client_stubs()` line 66, `_load_ci_base()` line 108, `_WrapperHarness` line 616). Reuse those helpers if they can be imported without copying, otherwise copy the minimum. The stub `apiclient` must gain whichever exception classes the helper catches. Use a fake client whose `check_capability`, `list_cluster_operations_for_target` and `await_no_pending_operations` are scripted. Cases: (a) the wait returns and the second listing has no new error, so the helper returns; (b) a mutating op absent from the first listing is in `error` in the second, so it fails naming that op; (c) an op already in `error` in the first listing is ignored; (d) an observational op in `error` is ignored; (e) a client timeout becomes the five-minute failure; (f) a missing capability fails before any listing or wait; (g) the wait is called with the object type, uuid, `timeout=300` and the caller's interval. Mutation-test with the `mutation-check` skill and report the results: drop the observational check, and (d) must fail; drop the first-listing exclusion, and (c) must fail; drop the second listing, and (b) must fail. Run `tox -e py3 -- shakenfist.tests.test_ci_await_operations`, then `pre-commit run --all-files`. Commit subject: "Wait on has_pending_operations in CI helpers." |
| 5b | low | sonnet | none | **Read the body path through the client.** Read decision 5 and survey findings 7 and 8 first. In `TestOutstandingOperationsBodyAll.test_network_outstanding_operations_body_all` (`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api.py:33`), replace the `_request_url('GET', url, data={'all': True})` read with `self.test_client.get_network_clusteroperations(net['uuid'], all=True)` and the plain `_request_url('GET', url)` read with `self.test_client.get_network_clusteroperations(net['uuid'])`. Keep the `?all=true` read as a raw `_request_url`. Rewrite the class docstring: the client's wrapper sends `all` in a JSON body, which is the path issue 3629 broke, so going through the wrapper is the test, and the query-string form stays raw because the client never sends it. Add an assertion that every op in the `all=True` listing has an `observational` key. Keep the existing comments' reasoning about background sweeps. This test runs only in the merge queue (finding 8), so verify it the way `docs/developer_guide/coding_rules.md` ("Cluster CI tests only run in the merge queue") asks: make a scratch venv in the session scratchpad, `pip install -e` the client-python checkout at `origin/develop` (use a `git worktree add --detach` of `origin/develop` in the scratchpad, never move the checkout's own branch) plus `testtools prettytable oslo.concurrency`, and confirm `python -c 'import shakenfist_ci.cluster_ci_tests.test_api'` succeeds with `shakenfist/deploy` on `PYTHONPATH`, and that `get_network_clusteroperations` exists on `apiclient.Client`. Remove the scratch worktree afterwards. `pre-commit run --all-files`. Commit subject: "Read outstanding ops through the client." |
| 5c | medium | sonnet | none | **Prove sweep ops are observational on a real cluster.** Read decision 4 and survey findings 6 and 8 first. Add `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_observational_sweeps.py`, with one `base.BaseNamespacedTestCase` subclass (namespace prefix `obssweep`), following a small neighbour such as `cluster_ci_tests/test_events.py` for setup, instance creation (always `self.create_instance()`, see `docs/developer_guide/ci.md` "Creating instances in the functional suite"), image choice and cleanup. The test: create one instance with no extra networks, `_await_instance_ready()`, then `_await_instance_operations_complete()`. Then poll `self.system_client.list_cluster_operations_for_target('instance', uuid)` every 10 seconds, for at most a module constant `SWEEP_WAIT = 8 * 60`, whose comment explains the bound (`per_instance_checks_and_usage` runs every five minutes, plus a fill and a processing pass, plus margin; cite `shakenfist/daemons/cluster/main.py`, not a line number). Stop at the first op whose `tasks` include both `collect_billing_statistics` and `health_check_kvm_process`. Ops are hard deleted between thirty seconds and about six minutes after they finish, so a 10-second poll will see it. On each poll, also GET the instance and assert that `last_cluster_operation`, when it is not null, does not name any op the listing marks `observational`. If at some poll every non-terminal op in the listing is observational, assert that `has_pending_operations` is false at that poll. Once the sweep op is found: assert `observational` is true on it; poll until it is terminal or gone (bounded at 60 seconds); then assert the pointer does not name it, and that `has_pending_operations` is false. If no sweep op appears within `SWEEP_WAIT`, fail, saying the per-instance sweep never reached the instance, which is itself worth knowing. Record the seconds waited with `self.addDetail()` so the merge-queue log shows the cost. Comments say why, not which plan asked. This test runs only in the merge queue, so verify it the same way as step 5b (scratch venv, import check). The management session will run it on a cluster before merge. `pre-commit run --all-files`. Commit subject: "Check sweep ops are observational end to end." |
| 5d | medium | sonnet | none | **Say how long a finished op is visible, once.** Read decision 6 and survey finding 5 first. Do not put plan phase numbers in anything under `docs/` outside `docs/plans/` (the `check-plan-phase-references` hook enforces it). (1) In `docs/operator_guide/database.md`, give the deleted-object sweep paragraph (around line 968, "The cluster daemon also runs a deleted-object sweep, every minute ...") its own heading at the right level, and make it the canonical statement: eligible thirty seconds after a final state (`_deleted_object_delay()`, `shakenfist/daemons/cluster/scheduled_tasks.py`); removed with its `cluster_operation_targets` rows by the sweep, which runs every minute but walks a type again only five minutes after its last walk reached the end (`DELETED_OBJECT_WALK_RECHECK`), so normally within about six minutes, longer while the sweep has a backlog (`publish_deleted_object_backlog` makes the backlog visible; say how an operator can see it if the page already does); errored operations never hard deleted (#3922), so their target rows wait for the `CLUSTER_OPERATION_TARGET_RETENTION` prune, seven days by default. Run `python3 tools/check-doc-anchors.py` after adding the heading. (2) Rewrite every other site to the short form and link to that heading. Docs: `docs/operator_guide/database.md:702` (table row: "the last ~30 seconds of terminal ones"), `:1145`, `:1194`; `docs/operator_guide/upgrades.md:348`; `docs/operator_guide/networking/overview.md:815`; `docs/developer_guide/database_internals.md:385`; `docs/developer_guide/api_reference/clusteroperations.md:41` ("within about fifteen minutes"); `docs/release_notes/v07-v08.md:1403` and `:1454` (release notes describe behaviour, so correct them in place). Swagger, which states eligibility only and no cadence: the `all` parameter text at `shakenfist/external_api/instance.py:2546`, `network.py:1073` and `artifact.py:1065`. Code comments, eligibility only, no cadence, and keep each comment's own point intact: `shakenfist/operations/baseoperation.py:211`, `:757`; `shakenfist/operations/net_op.py:104`; `shakenfist/schema/cluster_operation_target.py:10`; `shakenfist/daemons/cluster/main.py:177`; `shakenfist/tests/operations/test_baseoperation.py:624`; `shakenfist/tests/schema/operations/test_upgrade_step_sweep.py:13`; `shakenfist/tests/test_mariadb_cluster_operation_targets_live.py:268`; `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_coalescing.py:17`. Line numbers are from `develop` at `9efbff1481`; find each by its text if it has moved. Then run both greps from this plan's Definition of done and resolve anything else they find. Do not touch `docs/plans/`. `tox -e docs` if it builds locally, then `pre-commit run --all-files`. Commit subject: "Describe finished-op lifetime consistently." with `Fixes #4603` in the body. |
| 5e | medium | sonnet | none | **Document the functional-suite convention.** Read decisions 2, 3 and 7 and survey finding 10 first, and read step 5a's committed helper. Add a section "Waiting for an object's cluster operations" to `docs/developer_guide/ci.md`, placed immediately before "## Creating instances in the functional suite" (line 134), in the file's style (short, reasons first, no plan phase numbers). Say: a test that mutates an object and then asserts on the result waits with `_await_instance_operations_complete()` or `_await_network_operations_complete()`; never poll `last_cluster_operation`, which names only the newest mutating op, so an older one can still be queued behind it; the helpers wait on `has_pending_operations` through the client's `await_no_pending_operations()`; observational work such as the billing and health sweep neither holds a wait open nor fails it; a mutating op that ends in `error` during the wait fails the test, and one already in error beforehand does not; and the suite requires a server that advertises `object-pending-operations`, because it always runs against its own revision. Link to the API reference's "Waiting for an object to settle" section in `docs/developer_guide/api_reference/clusteroperations.md` for what the field means. Run `python3 tools/check-doc-anchors.py`, then `pre-commit run --all-files`. Commit subject: "Document waiting for operations in CI." |
| 5f | n/a | management | n/a | After each step: read the changed files rather than the summary, re-run the step's mutations rather than trusting the report, run `pre-commit run --all-files`, check the commit against the master plan's review checklist, and commit. Before pushing, run step 5c's test, and `test_api.py`'s `TestOutstandingOperationsBodyAll`, against a real cluster: a `workflow_dispatch` of `Functional tests` on the branch runs the `(collection)` matrix (`gh workflow run functional-tests.yml --ref cluster-op-visibility-phase-05-coverage-docs`, then `ci-status`). Record the sweep test's measured wait. Then walk the master plan's success criteria (decision 8) and record each, with its evidence, under Execution notes below. Push, and watch the PR's smoke suites, which run the rebuilt helper through the hot-plug tests (`ci-status shakenfist/shakenfist runs --branch cluster-op-visibility-phase-05-coverage-docs`). Offer the PR; do not open it unasked. Its body says `Fixes #4603`. |

## Risks and mitigations

* **The rebuilt helper returns too early.** If `has_pending_operations`
  were wrong, every hot-plug test would race again. *Mitigation:* phase
  4's sqlite tests pin the field against the listing, and the smoke
  suites' three hot-plug tests (`instance_hotplug.py`) fail loudly if
  the attach has not landed when the helper returns. That is the exact
  failure this plan started from. The management session reads their
  results on the PR, not only the job's colour.
* **The helper now fails where it used to pass.** Decision 2 catches an
  op that errors between polls, which today's loop missed, and decision
  3 fails without the capability. A test that has been passing while one
  of its ops errored will start failing. *Mitigation:* that failure is
  real and is the point, but the management session checks any new
  smoke failure on the PR against the op it names before calling it a
  regression of this phase.
* **The sweep test is slow, or does not see a sweep.** It holds one
  merge-queue worker for up to eight minutes. A loaded cluster whose
  per-instance pass runs out of its ten-second budget before reaching
  the instance would fail it. *Mitigation:* the wait is bounded and
  recorded with `addDetail`. Step 5f measures it on a cluster before
  merge. If it routinely runs past six minutes, or fails for lack of a
  sweep, the bound or the test's place in the suite is revisited before
  merge, not after.
* **`TestOutstandingOperationsBodyAll`'s window is now short.** Completed
  ops stay visible for as little as thirty seconds, rather than for up
  to fifteen minutes (finding 7). *Mitigation:* the test reads straight after the
  await, and step 5f runs it on a cluster. If it flakes, the fix is to
  create a fresh op immediately before the `all=True` read, not to widen
  the window.
* **A lifetime statement is missed or contradicts the canonical one.**
  *Mitigation:* the Definition of done greps for both wordings, and the
  management session reads every site step 5d touched against the
  canonical paragraph.
* **`ci.md` and `base.py` conflict with the 4407 worktree.**
  *Mitigation:* the new `ci.md` section sits before "Creating instances
  in the functional suite", and the 4407 change adds its section after
  it. In `base.py` the two changes touch different methods. Whichever
  merges second rebases, which should be mechanical.
* **The client helper's semantics change under the suite.** CI installs
  client-python at the matching revision (finding 1), so a client change
  to `await_no_pending_operations()` reaches this helper with no review
  here. *Mitigation:* step 5a's unit tests pin the calls the helper
  makes, and the `ci.md` section names the client helper, so a client
  reviewer can see who depends on it.

## Definition of done

Each item is checkable from the tree or from a command. Every grep was
run at planning time, and gave the "now" result noted.

- [ ] `grep -n "await_no_pending_operations" shakenfist/deploy/shakenfist_ci/base.py`
      prints one line (now nothing), and
      `grep -c "observed_outstanding\|terminal_states" shakenfist/deploy/shakenfist_ci/base.py`
      prints `0` (now `5`).
- [ ] `grep -c "self.test_client._request_url(" shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api.py`
      prints `1` (now `3`), and `grep -c "get_network_clusteroperations" shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api.py`
      prints `2` (now `0`).
- [ ] `grep -l "collect_billing_statistics" shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_observational_sweeps.py`
      prints the file (now it does not exist).
- [ ] Phase 4's lifetime number is gone. This prints `0` (now `5`):

      ```
      grep -rnE "removed within about fifteen|last fifteen minutes|within about fifteen" \
          docs shakenfist --include=*.md --include=*.py --exclude-dir=plans | wc -l
      ```
- [ ] No "thirty seconds" lifetime claim survives without saying
      eligibility. This prints `0` (now `16`), and the management session
      reads each site from step 5d's list, since a rewrapped sentence can
      slip past a line grep:

      ```
      grep -rniE "(about |~)?(thirty|30) seconds (after|of terminal)|hard[- ]deleted,? (about )?(thirty|30) seconds|deleted (about )?thirty seconds" \
          docs shakenfist --include=*.md --include=*.py --exclude-dir=plans | grep -v eligible | wc -l
      ```
- [ ] No page promises removal within a minute or two. This prints `0`
      (now `0`, and it must stay so after step 5d):

      ```
      grep -rniE "minute or two|thirty to ninety|within a minute of (finishing|going terminal)" \
          docs shakenfist --include=*.md --include=*.py --exclude-dir=plans | wc -l
      ```
- [ ] Code comments and swagger state no cadence for the deleted-object
      sweep. `grep -rnE "every (minute|fifteen minutes)" shakenfist --include=*.py | grep -i "sweep\|hard.delet"`
      prints only the scheduler comment in `daemons/cluster/main.py`
      and `config.py`'s `DELETED_OBJECT_SWEEP_SECONDS` description,
      which own the cadence.
- [ ] `grep -n "^## Waiting for an object's cluster operations" docs/developer_guide/ci.md`
      prints one line, before the line `grep -n "^## Creating instances in the functional suite" docs/developer_guide/ci.md`
      prints.
- [ ] `git diff --stat develop... -- ARCHITECTURE.md AGENTS.md README.md`
      prints nothing. (Three dots: against the merge base, because
      `develop` moves while the branch is open.)
- [ ] `tox -e py3 -- shakenfist.tests.test_ci_await_operations shakenfist.tests.test_ci_capacity_wait`
      passes.
- [ ] Step 5a's three mutations each fail the test they name. This was
      demonstrated in 5f, not assumed.
- [ ] `python3 tools/check-doc-anchors.py` and `pre-commit run --all-files`
      pass.
- [ ] A `Functional tests` run that includes the `(collection)` matrix
      passed `test_observational_sweeps` and
      `TestOutstandingOperationsBodyAll` on this branch, and the sweep
      test's recorded wait is in the Execution notes.
- [ ] The smoke suites are green on the PR, including the three
      `instance_hotplug.py` tests.
- [ ] The Execution notes list every success criterion in the master
      plan with the evidence that it is met, or say why it is not.
- [ ] The PR body says `Fixes #4603`, and #4603 closes when it merges.

## Back brief

Before step 5a, back brief the operator on how you understand this
phase and the order of the steps, as the master plan requires. One gate
follows:

* **Before step 5a, confirm decisions 3 and 4.** Each departs from the
  master plan's outline. Decision 3 drops a fallback the outline asked
  for, and decision 4 replaces the outline's functional test with a
  different one. Either costs nothing to change now and a re-plan of
  steps 5a and 5c later.

Step 5d is not gated. It is wide but mechanical once decision 6's
wording is agreed, and that wording is in this plan for review.

## Execution notes

* **2026-10-25 (AEDT), back brief.** Mikal confirmed decision 3 (no
  fallback for servers without `object-pending-operations`) and decision
  4 (the classification test, bounded at eight minutes), and gave this
  session permission to push the branch, dispatch `Functional tests` on
  it, and open the PR.
* **Step 5a.** As planned. In review, the stub client's
  `TimeoutException` moved into `test_ci_capacity_wait.py`'s shared
  `_client_stubs()`, as a plain `Exception` subclass like the real one,
  instead of being monkeypatched onto that module when the new test
  imports it. The three mutations were re-run on a copy of the tree and
  each was killed by the test it names (`mutation-check`, 3 of 3). None
  of the eight callers waits on an object it has just deleted, so the
  client's `ResourceNotFoundException` does not arise.
* **Step 5b.** As planned. The wrapper's default call sends
  `{'all': false}` in the body rather than a bare GET, which asks the
  same question. The management session rewrapped the docstring.
* **Step 5c.** As planned, with three choices the brief left open: the
  sweep op is matched by its task set, not by `operation_type`; an op
  that ends in `error` does not fail the test; and a sweep op still not
  finished after sixty seconds does. The management session rewrote the
  `SWEEP_WAIT` comment, because one run of the per-instance sweep both
  refills and processes its queue.
* **Correction found while reviewing 5c, before 5d's brief.** The
  deleted-object sweep runs every minute, but it walks a type again
  only `DELETED_OBJECT_WALK_RECHECK` (five minutes) after its last walk
  of that type reached the end. Survey finding 5, finding 7, decision 6,
  the 5c and 5d briefs and the risk on
  `TestOutstandingOperationsBodyAll` said a finished op goes within "a
  minute or two" or "thirty to ninety seconds". They now say between
  thirty seconds and about six minutes, as do the master plan's
  Situation bullet and `all=true` open question. A Definition of done
  item guards the wrong wording. This makes `TestOutstandingOperationsBodyAll`
  less likely to flake, not more.
* **Step 5d.** As planned. The canonical statement is the new heading
  "The deleted-object sweep" in `docs/operator_guide/database.md`
  (`#the-deleted-object-sweep`), which names the
  `cluster_deleted_object_backlog` metric. Three comments relied on
  the old timing ("within a minute", "almost immediately", "about
  thirty seconds of terminal ones") and now say "soon" or "recently
  finished"; each comment's own argument still holds. The management
  session rewrapped several edited paragraphs and comments. Rewrapping
  `net_op.py` once split "eligible" from "thirty seconds" and tripped
  the line grep, which shows the grep catches false positives as well
  as misses.
* **Step 5e.** As planned. Every claim in the new `ci.md` section was
  checked against the committed helper.
* **Rebase.** Before pushing, the branch was rebased onto
  `origin/develop`, 18 commits ahead, with no conflicts. Every
  Definition of done grep, the two CI-helper test modules and
  `pre-commit run --all-files` were re-run afterwards and pass. The
  ARCHITECTURE/AGENTS/README check now diffs against the merge base,
  because a two-dot diff against a moved `develop` showed that
  branch's own `ARCHITECTURE.md` change.

### Master plan success criteria

Walked on 2026-10-25 against the rebased branch.

| Criterion | Status | Evidence |
|-----------|--------|----------|
| `pre-commit run --all-files` passes | Met | Run after the rebase, exit 0. |
| New code follows existing patterns | Met | Storage went through the three-layer pattern in phases 1 to 4. This phase adds test helpers and docs only. |
| Observational filter is in SQL, with index support | Met | `table.c.observational == sa.false()` in `_direct_get_latest_cluster_operation_target()` and `_direct_has_pending_cluster_operation_target()` (`mariadb.py`); `idx_cot_target` covers `(target_object_type, target_uuid, observational)`. |
| Observational op changes neither pointer nor pending, proven by a unit test | Met | `test_observational_current_state.py`: `test_observational_op_does_not_shadow_or_count_as_pending`, `test_sweep_alone_leaves_no_pointer_and_nothing_pending`. On a real cluster: `test_observational_sweeps` (step 5c), result below. |
| `/instances/<ref>/clusteroperations` returns a queued op behind a later terminal one, proven by a test | Met | `test_object_cluster_operations.py::test_success_criterion`. |
| `test_interface_plug_and_exec_reboot` no longer races the hot-plug | Pending CI | The helper now waits on `has_pending_operations` (step 5a). Evidence will be the PR's smoke runs of the three `instance_hotplug.py` tests. |
| Protos regenerated with `tox -e genprotos` | Met | Re-running `tox -e genprotos` on the branch leaves the tree clean. |
| `database.md` documents the v3 schema; developer guide documents mutating/observational | Met | `docs/operator_guide/database.md` ("The table is at schema v3", the `observational` column); `docs/developer_guide/database_internals.md` (the `observational` flag and `is_observational()`). |
| Top-level docs updated where they describe the affected storage or API | Met, no edit needed | None of `ARCHITECTURE.md`, `AGENTS.md` or `README.md` describes it (finding 10), and this repository has no `CLAUDE.md`. |
| `index.md` and `order.yml` reference the plan with current status | Met | `order.yml:42`; the `index.md` row reads `In progress`, `5 of 7`. |
