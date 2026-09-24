# Phase 0 -- Assert power state in the existing lifecycle tests

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
This is the plan's first phase. Its prerequisite, the #4280 fix
([#4309](https://github.com/shakenfist/shakenfist/pull/4309)), merged on
2026-09-23 as `de5e3833c` and is on `develop`.

**Planning effort:** medium, as the master plan asks. The code is small
and follows existing harness patterns. The one judgement call is whether
the assertion waits for a value or reads it once (D1), and that call is
made below.

## Scope

**In scope:**

* One harness helper, `_assert_power_state()`, in
  `shakenfist/deploy/shakenfist_ci/base.py`, and a unit test for it.
* `power_state` assertions in all six lifecycle tests in
  `guest_ci_tests/test_state_changes.py`: five in `TestStateChanges` and
  one in `TestDetectReboot`.
* The same assertions in both copies of
  `test_interface_plug_and_exec_reboot`, in `smoke_ci_tests` and in
  `guest_ci_tests` (D2).
* Correcting the master plan claims the survey found stale. This is
  done in the planning commit, so later steps should not redo it.

**Out of scope:**

* Any change to production code. Every assertion added here passes
  on `develop` today, apart from the rare race recorded as F13.
* Asserting `agent_state` or events on power off. Phase 1b owns the
  agent state the cleaner writes on a detected power off, and asserting
  it here would pin today's behaviour just before that phase changes it.
* Deduplicating the two copies of `test_interface_plug_and_exec_reboot`.
  This is filed as [#4318](https://github.com/shakenfist/shakenfist/issues/4318).
* Adding `power_state` checks to the other `_await_instance_ready()`
  calls in `test_agentops.py`. They create instances and never change
  power state, and `_start_target()`'s `on` assertion already covers
  create in every lifecycle test.
* Fixing F13. See D3.

## What the survey found

The master plan's phase 0 section was right in substance. The survey
found three omissions and one new defect, and has corrected all of them
at their source in the master plan and `docs/plans/index.md`.

### S1 -- `test_interface_plug_and_exec_reboot` exists twice

The master plan names one test, but there are two copies:
`smoke_ci_tests/test_agentops.py:344` and
`guest_ci_tests/test_agentops.py:343`. `smoke-ci.conf` runs the first on
every pull request, through the `smoke_collection` job in
`.github/workflows/functional-tests.yml:364`. `guest-ci.conf` runs the
second in the merge queue, through the `Guests` entry of
`functional_matrix_merge_collection` (`:461-469`).

The copies have drifted. The smoke copy has `net.ifnames` checks, a
per-method `hotplug_mac`, and a check that the interface name survives
the power cycle; the guest copy has none of these. That drift is
[#4318](https://github.com/shakenfist/shakenfist/issues/4318).

Both copies power off at the same point (smoke `:443-446`, guest
`:398-401`): `power_off_instance`, `_await_instance_not_ready`,
`power_on_instance`, `_await_instance_ready`.

### S2 -- `test_state_changes.py` has six lifecycle tests, not four

The master plan lists create, power off, pause and reboot.
`TestStateChanges` also has `test_lifecycle_reboot_powered_off` (`:127`,
the #3630 regression test), and `TestDetectReboot.test_agent_detects_reboot`
(`:218`) does a hard reboot. Both are in scope.

### S3 -- every asserted value is written before the call returns

All the power paths are synchronous. The REST endpoints
(`external_api/instance.py:1522-1647`) call the `Instance` methods
directly, holding the instance lock, and those methods write
`power_state` before returning:

| Transition | Writer | Value |
|---|---|---|
| create, power on | `_power_on_inner()` `instance.py:2326`, after `domain.create()` | `extract_power_state()`, `on` |
| power off | `power_off()` `instance.py:2351` | `off` |
| pause | `pause()` `instance.py:2395`, loops until the write lands | `paused` |
| unpause | `unpause()` `instance.py:2422` | `on` |
| soft or hard reboot | nothing | stays `on`: `on_reboot` is `restart` (`libvirt.tmpl:39`), so the domain stays active and `SHUTDOWN`/`RUNNING` both map to `on` |

`external_view()` (`instance.py:693-715`) publishes the dict's
`power_state` key as a top-level string, so `get_instance()['power_state']`
is the value to compare.

### S4 -- F13: the cleaner can overwrite a fresh value

`update_power_states()` reads the domain state at
`daemons/cleaner/scheduled_tasks.py:153` and writes it at `:154`. It
does not hold the instance lock that the power endpoints hold. If a
power off or pause completes between the read and the write, the stale
value wins:

- A paused instance then reads `on` until the next cleaner pass writes
  `paused`, which takes 60-120 seconds.
- A powered off instance reads `on` indefinitely, because the cleaner
  never sees inactive domains (F1).

The window is the time `update_power_state()` takes to acquire its
attribute lock and read the row, which is milliseconds. The cleaner
runs once a minute per node. A test would only see this if a power off
or pause completed inside that window, which is rare but possible.

This finding is new. It has been added to the master plan as F13, and
the fix is folded into phase 1b's lock-and-re-read guard.

### S5 -- the guest suite only runs in the merge queue

It also runs on `workflow_dispatch`
(`functional-tests.yml:432-434`), so the branch can run it before it is
enqueued. The smoke copy runs on the pull request anyway.

### S6 -- there is precedent for unit-testing harness helpers

`shakenfist/tests/test_ci_capacity_wait.py` loads `base.py` by path,
with stubs for `shakenfist_client` and `prettytable`, because those are
not test dependencies of this repository. The new helper's unit test
follows that pattern.

## Decisions

**D1 -- Read the value once, with no wait.** `_assert_power_state()`
reads `power_state` once and fails if it is wrong. It does not poll.

This is the decision most likely to be argued with, because the master
plan framed this phase as "choosing waits which do not flake". A wait
here would add no protection and would hide real faults:

- S3 shows that every value is written before the API call returns, or
  before the agent reports ready. A correct system therefore has the
  right value on the first read, so a wait adds nothing when things
  work.
- The one known way to fail, F13, is only helped by a wait long enough
  to cover a whole cleaner pass (about 150 seconds). That only rescues
  the pause case. The power-off case never heals, so it would fail
  after 150 seconds anyway.
- Once phase 1b lands, the cleaner will write `off` for inactive
  domains within one or two passes. A 150-second wait would then pass a
  regression in which `power_off()` stops writing `off`, as long as the
  cleaner fixed it afterwards. The assertion would no longer be testing
  what the API reports when the call returns.

The cost of this choice is that F13 can fail one of these tests. Every
such failure is a real, user-visible wrong answer, and the helper's
failure message names F13 as a suspect when the value it read is the
state before the operation. Phase 1b removes the cause. If F13 shows up
in the merge queue before 1b lands, the answer is to bring 1b's F13 fix
forward, not to add a wait.

**D2 -- Change both copies of `test_interface_plug_and_exec_reboot`.**
The smoke copy is the only power-state coverage that runs on every
pull request, so leaving it out would limit the assertions to the merge
queue. Deduplicating the copies is #4318's job. Doing it here would mix
a restructure into a phase whose diff should read as "assertions only".

**D3 -- Record F13; do not fix it here.** A fix means taking the
instance lock in the cleaner. Phase 1b already plans that lock for the
second loop, along with its timeout-and-skip behaviour and its database
load estimate, and the same change fixes the first loop. Doing it in
phase 0 would mean designing that lock twice.

**D4 -- The helper uses `system_client` and returns nothing.** It
matches `_await_agent_state()` (`base.py:1033`), which reads through
`system_client`, so that namespace permissions do not affect it. On
failure it calls `_log_instance_events()` before `self.fail()`, as
`_await_instance_event()` does (`base.py:1142-1143`), so the failure
shows the event history in the log. The message includes the expected
value, the value read, and the caller's description of what was just
done (for example "after power off").

**D5 -- Assert after the existing waits, not in their place.** Each new
assertion follows the test's existing guest-side check
(`_await_instance_ready()`, `_await_instance_not_ready()`, or the API
call's return). Those checks are evidence that the guest itself is
running, and the new assertions are about what the API reports. The
tests should check both.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | See brief 1. Commit: "Assert instance power state in the CI harness." |
| 2 | medium | sonnet | none | See brief 2. Commit: "Assert power state in the lifecycle tests." |
| 3 | low | management session | none | Dispatch `functional-tests.yml` on the branch (`gh workflow run functional-tests.yml --ref power-state-correctness-phase-00-assertions`) so the guest suite runs before the pull request is enqueued. Read the `Guests (collection)` job and the pull request's smoke job, and confirm every new assertion ran and passed. Any failure goes to D1's F13 analysis before anything else. |

### Brief 1 -- the helper and its unit test

Add this method to `BaseTestCase` in
`shakenfist/deploy/shakenfist_ci/base.py`, directly after
`_await_power_off()` (`:753`):

```python
def _assert_power_state(self, instance_uuid, expected, after):
```

* Read `self.system_client.get_instance(instance_uuid)['power_state']`
  once. Do not poll and do not sleep (D1 in the phase plan explains why;
  read it before changing this).
* If it equals `expected`, emit a tracing event
  (`self._emit_tracing_event({'msg': 'Power state asserted', ...})`,
  including the uuid and the value) and return.
* Otherwise call `self._log_instance_events(instance_uuid)` and then
  `self.fail(...)`. The message is
  `f'Instance {instance_uuid} reports power_state {observed!r} {after}, expected {expected!r}.'`
  When `observed == 'on'` and `expected` is `'off'` or `'paused'`, append
  a sentence saying this matches the cleaner stale-write race recorded
  as F13 in `docs/plans/PLAN-power-state-correctness.md`, so that
  whoever triages the failure knows where to look.
* Give the method a docstring of two or three sentences: power state is
  written synchronously by the call under test, so it is read once;
  point to the phase plan for why.

Unit test: new file `shakenfist/tests/test_ci_power_state.py`. Mirror
how `shakenfist/tests/test_ci_capacity_wait.py` loads `base.py` by path
with stubs for `shakenfist_client` and `prettytable`, and reuse its
helpers if they can be imported, rather than copying them. Construct a
`BaseTestCase` subclass instance without running its `setUp` (check how
`test_ci_capacity_wait.py` does this), give it a `mock.MagicMock()`
`system_client`, and patch `_emit_tracing_event` and
`_log_instance_events`. Tests:

1. Matching value: returns, reads `get_instance` exactly once, and does
   not call `_log_instance_events`.
2. Wrong value: raises `AssertionError`, the message contains the
   expected value, the observed value and the `after` text, and
   `_log_instance_events` was called once.
3. `on` read when `off` was expected: the message mentions F13.
4. `off` read when `on` was expected: the message does not mention F13.
5. The helper never sleeps. Patch `time.sleep` in the loaded module
   with a mock, run the wrong-value case, and assert the mock was not
   called.

Mutation check (report the result, do not commit it): change the
comparison to `!=` and confirm tests 1 and 2 fail; then make the helper
read `.get('power_state', expected)` from a response that has no
`power_state` key, and confirm a test fails. If none fails, add one
where `get_instance` returns a dict with no `power_state` key, and
assert that it fails the check. Run the new file with
`stestr run shakenfist.tests.test_ci_power_state` from a `tox -e py3`
environment, then run `pre-commit run --all-files`.

### Brief 2 -- the assertions

The helper from step 1 is `self._assert_power_state(uuid, expected, after)`.
The `after` argument is a short phrase such as `'after power off'`.
Add these calls and nothing else. Do not reformat, rename or reorder
anything, and do not remove existing checks.

In `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_state_changes.py`:

* `_start_target()`: after `_await_instance_ready()` (`:79`), assert
  `'on'` with `'after create'`. This covers create for all five
  `TestStateChanges` tests.
* `test_lifecycle_soft_reboot`: after the second
  `_await_instance_ready()` (`:101`), assert `'on'` with
  `'after soft reboot'`.
* `test_lifecycle_hard_reboot`: likewise after `:119`, with
  `'after hard reboot'`.
* `test_lifecycle_reboot_powered_off`: after `power_off_instance` (`:133`),
  assert `'off'` with `'after power off'`. After the two `assertRaises`
  calls, assert `'off'` again with `'after rejected reboots'`. A
  rejected reboot must not change state.
* `test_lifecycle_power_cycle`: directly after `power_off_instance`
  (`:151`), before the `time.sleep(5)`, assert `'off'` with
  `'after power off'`. After the second `_await_instance_ready()`
  (`:166`), assert `'on'` with `'after power on'`.
* `test_lifecycle_pause_cycle`: after `_await_instance_not_ready()`
  (`:186`), assert `'paused'` with `'after pause'`. After the
  `_await_instance_ready()` that follows unpause (`:199`), assert
  `'on'` with `'after unpause'`.
* `TestDetectReboot.test_agent_detects_reboot`: after the first
  `_await_instance_ready()` (`:238`), assert `'on'` with
  `'after create'`. After the one following the hard reboot (`:248`),
  assert `'on'` with `'after hard reboot'`.

In both `smoke_ci_tests/test_agentops.py` and
`guest_ci_tests/test_agentops.py`, in `test_interface_plug_and_exec_reboot`
only (smoke `:344`, guest `:343`; the copies have drifted, so find the
lines in each):

* After the first `_await_instance_ready()`, assert `'on'` with
  `'after create'`.
* After `_await_instance_not_ready()` following `power_off_instance`,
  assert `'off'` with `'after power off'`.
* After the `_await_instance_ready()` following `power_on_instance`,
  assert `'on'` with `'after power on'`.

These tests cannot be run locally, because they need a cluster.
Instead, run `python3 -m py_compile` on each edited file, then
`pre-commit run --all-files`. Afterwards, grep for the new calls and
confirm the count: 11 in `test_state_changes.py` and 3 in each
`test_agentops.py`, 17 in total.

## Risks and mitigations

* **F13 fails a test in the merge queue.** It is rare (S4). When it
  happens, the failure message names it. Mitigation: the management
  session triages any power-state failure from step 3 or the merge
  queue against F13 first. If F13 recurs, it brings phase 1b's F13 fix
  forward as its own pull request (D1). It does not add a wait.
* **F6 fails `test_lifecycle_pause_cycle` with a 409.** If the cleaner
  writes `paused` before `pause()` does, `pause()` raises. This fault
  exists today and this phase does not change it. The new assertion
  comes after the pause call, so it cannot make this worse. Mitigation:
  when triaging a pause-cycle failure, check for the `pause failed`
  event before blaming the assertion.
* **The helper passes vacuously.** For example, it could read a key
  that is not there and compare `None` with `None`. Mitigation: the
  expected values are literal strings that are never `None`, and brief
  1's mutation check covers a missing key. Step 3 confirms that the
  `Power state asserted` tracing events carry real values.
* **The two agentops copies are edited inconsistently.** Mitigation:
  the count check in brief 2 (three calls in each copy), and review of
  both diffs side by side.

## Definition of done

* [ ] `grep -c '_assert_power_state(' shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_state_changes.py`
      prints 11.
* [ ] The same grep prints 3 for each of
      `smoke_ci_tests/test_agentops.py` and
      `guest_ci_tests/test_agentops.py`.
* [ ] `grep -n 'time.sleep\|while ' ` over the body of
      `_assert_power_state` in `base.py` finds nothing, because D1 has
      no wait.
* [ ] `stestr run shakenfist.tests.test_ci_power_state` passes. The
      mutation check in brief 1 failed at least one test for each
      mutation, and the step 1 report says so.
* [ ] `pre-commit run --all-files` passes.
* [ ] A `workflow_dispatch` run of `functional-tests.yml` on the branch
      has a passing `Guests (collection)` job, and the pull request's
      smoke job passes. Both run URLs are recorded in the pull request
      description.
* [ ] No production file (anything outside `shakenfist/deploy/shakenfist_ci/`,
      `shakenfist/tests/` and `docs/`) is changed:
      `git diff --stat develop... -- shakenfist ':!shakenfist/deploy/shakenfist_ci' ':!shakenfist/tests'`
      prints nothing.

## Back brief

Before starting step 1, back brief Mikal on what will change:

* One harness helper, with its unit test.
* 17 assertion calls, across three test files and eight tests.
* No production code.

Also state D1 plainly: the assertions do not wait, so F13 can fail one
of them. If he would rather accept a wait until phase 1b lands, that
changes brief 1, and it must be settled before the helper is written.
