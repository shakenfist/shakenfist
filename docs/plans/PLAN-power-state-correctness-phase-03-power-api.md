# Phase 3 -- Power operations answer truthfully

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
Phase 2 landed as [#4437](https://github.com/shakenfist/shakenfist/pull/4437)
(`776dc9e70`). Autostart now means "should be running": power on sets
it, and power off and the cleaner clear it. `power_off()` already
leaves the flag set when `destroy()` fails for any reason other than
"not running", which was review feedback on #4437.

**Planning effort:** high, as the master plan asks. Every change here
alters what a public endpoint answers. **Review effort:** high for steps
1 and 2. Those change `power_on()` and `power_off()`, which instance
create and delete also call, not only the API.

## Scope

**In scope:**

* F4. A power on which failed answers an error, not 200 with a `null`
  body.
* F5. A power off whose `destroy()` failed does not record `off`, and
  answers an error.
* F6 and F7. Pause and unpause judge success by the domain's state.
  They answer 409 for a powered off instance, as `reboot()` has since
  #3630.
* F8:
  * powering on a running instance records its real power state and
    sets autostart;
  * powering on a paused one is refused;
  * the generic start-error retry regenerates the domain XML;
  * `agent_state` is reset after every successful power on.
* F12. All six power endpoints declare the 406 they can answer, and each
  declares the other codes this phase makes it answer.
* Open question 3, answered (D8).
* [#2241](https://github.com/shakenfist/shakenfist/issues/2241),
  explicitly excluded (D7).
* Unit tests for each change, each shown to fail without it, and the
  functional tests the master plan lists.
* Correcting the master plan where the survey found it wrong. This is
  done in this phase's planning commit, so later steps should not redo
  it.

**Out of scope:**

* Recovering a crashed guest on unpause (#2241, D7).
* The three non-power endpoints which also use `requires_instance_active`
  but do not declare 406 (S7), filed as #4449.
* Changing what a successful power operation returns. It stays 200 with
  a `null` body (D6).
* Undefining a running domain on delete when `destroy()` failed. That
  is pre-existing, and recorded as Future work.
* F9, which phase 4 re-reads.

## What the survey found

The master plan's phase 3 section has the right scope, but five of its
claims are wrong or stale. Each is corrected at its source in this
phase's planning commit.

### S1 -- unpause twice is a 500, not a 409

F6 says libvirt's qemu driver "returns success for suspending a paused
domain or resuming a running one". The first half is true:
`qemuDomainSuspend()` stops the CPUs only when the domain is not already
paused, and then returns 0. That holds in libvirt 11.3.0 (Debian 13)
and 10.0.0 (Ubuntu 24.04).

The second half is false. `qemuDomainResume()` raises
`VIR_ERR_OPERATION_INVALID`, "domain is already running", for a running
domain. So today:

* pausing twice loops three times on an unchanged `power_state`, then
  answers 409 "pause failed after 3 attempts", which is F6 as written;
* unpausing twice leaks a raw libvirtError, so it answers 500.

Both suspend and resume raise "domain is not running" for an inactive
domain (`virDomainObjCheckActive()`), which is F7.

### S2 -- there are six power endpoints, not four

F12 and the phase section say four. `requires_instance_active` guards
six power endpoints in `external_api/instance.py`:

* `InstanceRebootSoftEndpoint` (`:1530`)
* `InstanceRebootHardEndpoint` (`:1555`)
* `InstancePowerOffEndpoint` (`:1580`)
* `InstancePowerOnEndpoint` (`:1605`)
* `InstancePauseEndpoint` (`:1636`)
* `InstanceUnpauseEndpoint` (`:1661`)

None of them declares 406.

### S3 -- nothing can tell a failed power on was out of capacity

Open question 2 answered 507 "for port or memory exhaustion". Neither
can be detected:

* **Ports.** `_allocate_console_port()` (`instance.py:1744`) loops with
  `while True` until it binds a free port, so port allocation never
  fails. The two "port" retry branches in `_power_on_inner()` handle a
  *collision* with a port libvirt or another process holds. They
  reallocate and retry, and five attempts that all collide are not
  exhaustion.
* **Memory.** There is no memory branch. A qemu which cannot allocate
  its RAM fails `domain.create()` with text in qemu's own stderr, which
  falls through to the "process exited while connecting to monitor"
  branch or the generic one. Placement has already reserved the
  instance's memory in the ledger.

The answer is changed to 500 for every failed power on (D1).

### S4 -- `power_on()` and `power_off()` are not only API calls

`create()` calls `power_on()` (`instance.py:1542`) and then judges
success by `is_powered_on()`, not by the return value. Delete calls
`power_off()` twice:

* `NodeInstOp._instance_delete()` (`operations/node_inst_op.py:230`)
  calls it with no `try`;
* `_delete_on_hypervisor()` (`instance.py:1567`) calls it inside a
  `try` which ignores every exception.

So making either method raise changes create and delete, not only the
endpoints. D2 and D3 say how each caller is kept as it is.

### S5 -- power on of a paused instance answers success

`domain.create()` on a paused domain raises "domain is already running",
because a paused domain is active. `_power_on_inner()` then returns
`True` (`:2366`) without:

* reading the power state;
* setting autostart;
* recording an event.

The instance stays paused, and the API answers 200. The same path leaves
a running instance whose autostart flag was lost without it being set
again, which #4437's review raised.

### S6 -- the generic start-error retry reuses the old domain

The final `else` of `_power_on_inner()`'s start errors (`:2384`) calls
`_power_on_retry_prep(None, ..., needs_port_reallocation=True)`.
Because `domain` is `None` there, the domain is not undefined. The
retry finds the old domain, whose XML still names the old ports, and
starts it again. So the reallocated ports are never used.

F8 is accurate on `agent_state`. It is set to `AGENT_NEVER_TALKED` only
when the *first* attempt failed. That happens even if every later
attempt failed too, and never after a power on that worked first time.
So after a power off, a successful power on leaves `agent_state`
reading "instance powered off" until sidechannel next connects.

### S7 -- three non-power endpoints share F12's gap

`InstanceAgentGetEndpoint`, `InstanceAgentExecuteEndpoint` and
`InstanceScreenshotEndpoint` also use `requires_instance_active` without
declaring 406. They are not power operations, so they are out of scope.
They are filed as
[#4449](https://github.com/shakenfist/shakenfist/issues/4449).

### S8 -- guest CI tests already use node exec

The phase section says no guest CI test uses `_node_exec` yet. Phase 2
added two that do:

* `_domain_autostart()` in `base.py:833`;
* the autostart poll in `test_lifecycle_qemu_killed`.

Both reach it through `_require_node_exec()`. The failed power on test
has a pattern to follow.

### Claims that held

These held:

* F4, F5 and F7 as written.
* F8's "already running" and retry claims.
* `client-python` maps 406, 409, 500 and 507 to distinct exceptions
  (`apiclient.py:168-178`).
* The client retries a 507 only when the caller opted in and the body is
  marked transient, so a 500 is never retried.
* The client's power methods return `r.json()` (`apiclient.py:892-918`),
  so a `null` success body stays `None`.
* F13 was fixed by phase 1b, so the power endpoints' lock is the one the
  cleaner takes.

## Decisions

### D1 -- a failed power on answers 500, with the last error

`power_on()` raises a new `exceptions.InstancePowerOnFailed`, a subclass
of `InstanceException`, when every attempt failed. Its message is the
last attempt's error, which `_power_on_retry_prep()` already has.

The endpoint maps it to `sf_api.error(500, 'instance failed to power
on: <message>', suppress_traceback=True)`. `create()` catches it and
falls through to its existing `is_powered_on()` check. So instance
create behaves exactly as now: the error delete, and its event.

Before raising, `power_on()` records the domain's real power state: the
`extract_power_state()` of the domain if one exists, otherwise `off`. A
failed power on must not leave an earlier `on` behind.

**This revisits open question 2, and is the decision a reviewer is most
likely to argue with.** The answer was 507 for exhaustion and 500
otherwise. S3 shows there is no exhaustion signal to branch on:

* a 507 on "five port collisions" would tell a client the cluster is
  out of capacity when it is not;
* a 507 on unparsed qemu text would sometimes be right, by accident.

`client-python` maps 507 to `InsufficientResourcesException`, and
callers who opted in retry it when marked transient. A false 507 is
worse than an honest 500. If a memory signal is ever wanted, it belongs
with the placement ledger, not with this text parsing (Future work).

### D2 -- a failed power off answers 500 and does not record `off`

When `destroy()` raises anything other than "domain is not running",
`power_off()` reads the domain again:

* **The domain is inactive.** The power off took effect despite the
  error. Clear autostart, record `off` and return as now.
* **The domain is still active.** Record its real power state, set no
  `off`, leave autostart alone (as #4437 already does), add an audit
  event `poweroff failed` carrying the error, and raise a new
  `exceptions.InstancePowerOffFailed`. The endpoint maps it to 500.

If the second read raises, the outcome is unknowable. Raise without
writing a power state, so that the cleaner settles it.

Delete must not change behaviour. `_instance_delete()` wraps its call in
`try/except exceptions.InstancePowerOffFailed`, logs a warning, and
carries on. `_delete_on_hypervisor()` already ignores every exception.

### D3 -- power on of a running instance succeeds, and of a paused one is refused

On "domain is already running", `_power_on_inner()` reads the domain's
state:

* **`paused`:** raise `InvalidLifecycleState('you cannot power on a
  paused instance; unpause it instead')`. The endpoint already maps
  that to 409.

  *Resume instead* would make power on mean two things. A refusal
  keeps power on idempotent only for the state it names.
* **`on`:**
  * set autostart, as a fresh power on does;
  * record `on`;
  * add the `poweron` event without the libvirt timing extras, since
    this call did not start the domain;
  * return success.

  This is also the repair path for a running instance whose flag was
  lost.

`create()` never meets a paused domain, because the domain was just
defined.

### D4 -- the retry regenerates the domain, and `agent_state` follows the outcome

* The generic start-error branch passes `domain` to
  `_power_on_retry_prep()`, so the retry defines fresh XML with the
  reallocated ports. That is what the "process exited" branch already
  does.
* `agent_state = AGENT_NEVER_TALKED` moves to after any successful
  attempt.
* A power on whose every attempt failed leaves `agent_state` as it was.

### D5 -- pause and unpause judge by the domain, and are idempotent

Both follow `reboot()`'s pattern (#3630):

* **Inactive domain.** If there is no domain, or `domain.isActive()` is
  false, raise `InvalidLifecycleState` (409). A libvirtError containing
  "domain is not running" from `suspend()` or `resume()` raises the
  same, which covers the race after the check.
* **Already in the target state.** Read the domain's state first. If it
  is already `paused` for pause, or `on` for unpause, record it and
  succeed without calling libvirt. Pausing twice and unpausing twice
  both answer 200.

  The master plan's functional test list names "pausing twice" without
  an expected answer. 200 matches power on (D3) and libvirt's own
  suspend.
* **Success.** It is "the domain's state after the call is the target
  state", not "`update_power_state()` changed the stored value". The
  stored state is written either way.
* **Retry.** Keep the three-attempt, one-second retry, now judged by the
  domain's state. It is what #2241's rescope credits with covering the
  transient case. After three attempts, `pause failed` and `unpause
  failed` stay 409, as now.

### D6 -- success bodies and the declared codes

* **Success.** A successful power operation still answers 200 with a
  `null` body. Returning the instance would be a contract change with
  no defect behind it.
* **406.** Every one of the six endpoints declares
  `(406, 'Instance is not ready.', None)`, the wording the VDI console
  endpoints use.
* **500.** Power on and power off add `(500, '<operation> failed on the
  hypervisor.', None)`.
* **409.** The power on 409 description widens to cover the paused case
  as well as UEFI.
* **Pause and unpause.** These declare no 500. They can still answer
  one for an unexpected libvirtError, as every endpoint can, but this
  phase adds no deliberate 500 there.

### D7 -- #2241 is excluded, with a comment

After this phase, unpausing a guest whose qemu died answers 409 "you
cannot unpause a powered off instance", because the domain is inactive.
The user's recovery is a power on, which works. #2241 asks for
automatic recovery: recreating the apparmor profile and restarting.
That is a feature with its own failure modes, not a truthfulness fix.

Step 6 comments on #2241 with this and leaves it open.

### D8 -- open question 3: `power_state` stays the asserted value

After this phase every power operation writes `power_state` from the
domain's observed state, on success and failure alike. The cleaner
corrects drift within a pass or two. So the cached value is what the
operations themselves read back, and it is what users see. Asserting it
remains right, and the functional tests here assert it after every call
that this phase changes.

### D9 -- unpause restarts the agent monitor (found in implementation)

The first functional run,
[37285857342](https://github.com/shakenfist/shakenfist/actions/runs/37285857342),
found a defect older than this phase. It stranded
`test_lifecycle_power_on_paused` at agent state "not ready (no contact)"
for 30 minutes after a pause of a few seconds, although the domain was
running.

* **The cause.** `unpause()` writes "no contact" and relies on the
  sidechannel monitor to write the agent's real state. The monitor
  caches readiness and writes `agent_state` only when that cache
  changes, and once it reads ready it sends plain pings whose replies
  change nothing.
* **Why the existing test missed it.** The sidechannel daemon tears down
  a paused domain's monitor, so a long pause gets a fresh monitor.
  `test_lifecycle_pause_cycle` waits for not-ready before unpausing,
  which is why it never saw this. A short pause leaves the old monitor's
  cache at ready, and nothing corrects the database.

Mikal chose to fix this in sidechannel rather than in the test. A
reply-gap check alone could not see a pause shorter than a healthy
agent's two to three second reply interval, so the fix
(`df54e28f4`) has two parts:

* `unpause()` sets the instance's monitor abort file after a resume that
  ran, so the daemon replaces the monitor and the new one re-handshakes,
  whatever the length of the pause. Agent operation executors have
  their own abort file and are not affected.
* The monitor resets its cached readiness after six seconds with no data
  from the agent. This is a backstop for freezes outside pause and
  unpause.

The same run was refused capacity in the Guests job for
`test_lifecycle_power_on_failure`, because each new test brought its own
instances. Brief 5's first three tests were folded into one,
`test_lifecycle_pause_semantics` (`ec22aa708`), which ends by awaiting
the agent after a short pause, as a regression check for D9.

## Step plan

Steps run one at a time in the phase worktree. Steps 1 to 4 all edit
`instance.py` and `external_api/instance.py`, and sub-agents must not
share a worktree concurrently, because pre-commit's stash is repo-wide.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | none | Power on answers truthfully (F4, F8; D1, D3, D4). See brief 1. Commit: "Make a failed power on an error." |
| 2 | high | opus | none | Power off answers truthfully (F5; D2). See brief 2. Commit: "Do not record off when destroy() fails." |
| 3 | medium | sonnet | none | Pause and unpause judge by the domain (F6, F7; D5). See brief 3. Commit: "Judge pause and unpause by domain state." |
| 4 | low | sonnet | none | Declare the codes on all six endpoints (F12; D6). See brief 4. Commit: "Declare 406 on the power endpoints." |
| 5 | medium | opus | none | Functional tests. See brief 5. Commit: "Test what the power endpoints answer." |
| 6 | low | sonnet | none | Documentation and the #2241 comment. See brief 6. Commit: "Document what the power endpoints answer." |
| 7 | -- | management | -- | Dispatch the functional tests on the branch and read the result. See brief 7. The first run found D9; its fix and the test consolidation were added as two further commits. |

Every step ends with `pre-commit run --all-files` passing. The
management session reads each diff before committing it, and re-runs
at least two of each step's mutations.

### Brief 1 -- power on answers truthfully

Files: `shakenfist/exceptions.py`, `shakenfist/instance.py`
(`create()` `:1532`, `power_on()` `:2279`, `_power_on_retry_prep()`,
`_power_on_inner()` `:2327`), `shakenfist/external_api/instance.py`
(`InstancePowerOnEndpoint` `:1605`), `shakenfist/tests/test_instance.py`.

1. Add `InstancePowerOnFailed(InstanceException)` to `exceptions.py`.
2. Keep the last attempt's message. `_power_on_retry_prep()` already
   builds it, so store it on the instance object, not in the database.
   Raise `InstancePowerOnFailed(message)` from `power_on()` when every
   attempt failed.
3. Before raising, record the real power state: extract it from the
   domain if one exists, otherwise `off`.
4. `create()` catches `InstancePowerOnFailed` around `self.power_on()`
   and falls through to its `is_powered_on()` check unchanged. Write a
   test that a create whose power on fails still error-deletes with
   "instance failed to power on".
5. On "domain is already running", read the state (D3):
   * `paused`: raise `InvalidLifecycleState`.
   * otherwise: `setAutostart(1)`, record the state, add the `poweron`
     event without timing extras, and return `True`.
6. In the final `else` of the start errors, pass `domain` rather than
   `None` to `_power_on_retry_prep()` (D4).
7. Move `self.agent_state = constants.AGENT_NEVER_TALKED` to after a
   successful attempt (D4).
8. In the endpoint, map `InstancePowerOnFailed` to
   `sf_api.error(500, f'instance failed to power on: {e}',
   suppress_traceback=True)`, and add an audit event `power on failed`.

Unit tests go in `test_instance.py` next to `InstancePowerStateTestCase`
(`:751`), using its fakes. Cover:

* every attempt failing raises, and records `off`;
* a later attempt succeeding returns, and sets `agent_state`;
* a first attempt succeeding sets `agent_state` (the F8 regression);
* already running records `on` and sets autostart;
* already paused raises `InvalidLifecycleState` and calls neither;
* the generic start error undefines the domain before the retry.

For each, remove the guard and show a named test fails; list the
mutations in the commit message.

**Watch for:** `InstancePowerEventTimestampsTestCase` (`:823`) asserts
the timing extras. The already-running event must not carry them, and
an existing test may need its expectation stated rather than changed.
Check `shakenfist/tests/external_api/` for a test of the power on
endpoint. There probably is none, so add one for the 500 mapping.

### Brief 2 -- power off answers truthfully

Files: `shakenfist/exceptions.py`, `shakenfist/instance.py`
(`power_off()` `:2411`), `shakenfist/operations/node_inst_op.py`
(`_instance_delete()`, the call at `:230`),
`shakenfist/external_api/instance.py` (`InstancePowerOffEndpoint`
`:1580`), `shakenfist/tests/test_instance.py`.

Implement D2:

1. Add `InstancePowerOffFailed(InstanceException)`.
2. In `power_off()`, on a `destroy()` error other than "not running",
   call `inst.isActive()`:
   * **false:** treat it as stopped. This means autostart is cleared,
     `off` is recorded and the `poweroff` event is written without
     timing extras.
   * **true:** record `extract_power_state(inst)`, add `poweroff
     failed` with `extra={'message': str(e)}`, leave autostart alone,
     and raise `InstancePowerOffFailed(str(e))`. Do not set
     `agent_state`.
   * **the read raises a libvirtError:** raise `InstancePowerOffFailed`
     without writing a power state.
3. Keep #4437's `stopped` flag semantics and its comment. Update the
   comment to say F5 is now fixed rather than pending.
4. `_instance_delete()` catches `InstancePowerOffFailed` around
   `inst.power_off()`, logs a warning with the instance, and continues.
   Write a test that a delete whose power off fails still reaches the
   rest of the delete.
5. The endpoint maps `InstancePowerOffFailed` to 500, as brief 1 does
   for power on.

Tests extend `InstancePowerOffAutostartTestCase` (`:910`). Its
`AutostartDomain` fake already raises on `destroy()`. Give it an
`isActive()` result. Cover all three branches, each with a mutation.
`test_power_off_leaves_autostart_when_destroy_failed` must still pass.

### Brief 3 -- pause and unpause judge by the domain

Files: `shakenfist/instance.py` (`pause()` `:2493`, `unpause()`
`:2520`), `shakenfist/tests/test_instance.py`.

Implement D5, following `reboot()` (`:2466`) and
`InstanceRebootTestCase` (`:619`) for the inactive-domain pattern and
its tests.

1. **Inactive domain.** No domain, or `not domain.isActive()`, raises
   `InvalidLifecycleState('you cannot pause a powered off instance')`,
   and the same for unpause. A libvirtError containing "domain is not
   running" from `suspend()` or `resume()` raises the same, `from e`.
2. **Already in the target state.** Before calling libvirt, read
   `lc.extract_power_state(domain)`. If it is already the target
   (`paused`, or `on`), write it with `update_power_state()`, set
   `agent_state` as the method does now, and return.
3. **Success.** The loop's condition becomes "the extracted state is
   not the target", not "`update_power_state()` returned False". Write
   the state with `update_power_state()` once it matches. The three
   attempt bound, the events and the 409 on exhaustion stay as they
   are.

Tests:

* pause of an inactive domain gives 409, and so does unpause;
* the "not running" race gives 409;
* pause twice succeeds without a second `suspend()`;
* unpause of a running domain succeeds without calling `resume()`;
* a stored value already `paused`, as the cleaner may have written,
  still succeeds when the domain is paused;
* a domain that never reaches the target gives 409 after three
  attempts.

Mutation-check each.

### Brief 4 -- declare the codes on all six endpoints

File: `shakenfist/external_api/instance.py`, `:1530-1685`.

Make these `swag_from` response changes (D6):

* All six endpoints add `(406, 'Instance is not ready.', None)`.
* Power on adds `(500, 'Power on failed on the hypervisor.', None)`.
* Power off adds `(500, 'Power off failed on the hypervisor.', None)`.
* Power on's 409 becomes `'The instance cannot be powered on: it is
  paused, or UEFI boot is unavailable.'`.

Then run the spec tests in `shakenfist/tests/external_api/`
(`test_openapi_spec.py`, `test_api_reference_specs.py`,
`test_parameter_declarations.py`) and regenerate any checked-in spec
they compare against. Do not touch the three non-power endpoints from
S7.

### Brief 5 -- functional tests

File: `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_state_changes.py`.
Helpers live in `shakenfist/deploy/shakenfist_ci/base.py`:
`_assert_power_state`, `_node_exec`, `_require_node_exec` and
`_domain_autostart`. Existing tests show how to start a target
(`_start_target`) and await power changes.

Add tests. (Tests 1 to 3 were later folded into one,
`test_lifecycle_pause_semantics`, on one instance; see D9.)

1. **`test_lifecycle_pause_powered_off`.** Power off, then pause gives
   `ResourceStateConflictException` (409), not `InternalServerError`.
   Unpause gives the same. The power state is still `off`.
2. **`test_lifecycle_pause_twice`.** Pause twice and unpause twice:
   every call succeeds, with the power state asserted after each.
3. **`test_lifecycle_power_on_paused`.** Pause, then power on gives
   409, and the power state is still `paused`. Unpause before cleanup.
4. **`test_lifecycle_power_on_failure`.**
   * Call `_require_node_exec()` first, so the test skips visibly where
     node exec is unavailable.
   * Power off.
   * Find the root disk path from the instance's block devices on its
     node, and `mv` it aside with `_node_exec(..., sudo=True)`.
   * Power on gives `InternalServerError` (500), and the power state is
     not `on`.
   * Move the disk back in a `finally`, power on, and assert `on`.

   Confirm which libvirt error a missing disk produces. It should fall
   into the generic branch, which after brief 1 undefines and
   regenerates the domain; check that the regenerated XML still points
   at the restored path.

Unit-test any new `base.py` helper in `shakenfist/tests/test_ci_power_state.py`,
as phase 2 did for `_domain_autostart`.

### Brief 6 -- documentation and #2241

* **`docs/developer_guide/api_reference/instances.md`**, after the
  power management list (`:271-297`). Add what each operation answers:
  * 406 when the instance is not `created`;
  * 409 for an operation the instance's power state does not allow;
  * 500 when the hypervisor refused a power on or power off;
  * pause and unpause are idempotent;
  * power on of a running instance succeeds;
  * power on of a paused one is refused.
* **`docs/operator_guide/power_states.md`.** Say that a failed power on
  or power off records the domain's real state, and name the new
  `power on failed` and `poweroff failed` events.
* **#2241.** Once step 3 is committed, post the D7 comment and leave
  the issue open.

### Brief 7 -- functional run

Dispatch the functional workflow on the branch, as phases 1b and 2 did.
Read the result. Confirm the new tests ran rather than skipped,
and that `test_lifecycle_pause_cycle`, `test_lifecycle_power_cycle` and
the reboot tests still pass.

## Risks and mitigations

* **Instance create or delete changes behaviour.** S4: both call the
  power methods.
  * **Mitigation:** briefs 1 and 2 each add a test that create and
    delete behave as before when power on or power off fails. Review
    checks there is no other caller with
    `grep -rn "\.power_on()\|\.power_off()" shakenfist --include=*.py`.
* **A client treats the new 500 as retryable, or the new 409 as
  something else.**
  * **Mitigation:** the survey checked that `client-python` maps each
    to its own exception and retries neither. The ansible collection
    has no power operations.
* **Regenerating the domain on the generic retry loses something the
  old domain had.**
  * **Mitigation:** the "process exited" branch already undefines and
    regenerates, so this is a well-trodden path. Brief 5's failed power
    on test powers on again after the retry, on a real hypervisor.
* **Idempotent unpause hides a real failure.**
  * **Mitigation:** it short-circuits only when libvirt reports the
    domain running, which is exactly what a successful unpause leaves
    behind.
* **The failed power on test leaves a broken instance in CI.**
  * **Mitigation:** the disk is restored in a `finally`, and the test
    asserts a working power on afterwards.

## Definition of done

* [ ] `grep -n "def power_on" -A30 shakenfist/instance.py | grep -c "raise exceptions.InstancePowerOnFailed"`
      is at least 1, and `grep -n "InstancePowerOnFailed\|InstancePowerOffFailed" shakenfist/external_api/instance.py`
      prints both endpoint mappings.
* [ ] `grep -n "unhandled instance start error" -B2 -A2 shakenfist/instance.py`
      shows `domain`, not `None`, passed to `_power_on_retry_prep()`.
* [ ] `grep -n "while not self.update_power_state" shakenfist/instance.py`
      prints nothing.
* [ ] `python3 -c "import re; s=open('shakenfist/external_api/instance.py').read(); [print(n, '(406' in b) for n, b in re.findall(r'class (Instance(?:Reboot(?:Soft|Hard)|Power(?:On|Off)|Pause|Unpause)Endpoint)\(.*?\n(.*?)def post', s, re.S)]"`
      prints six lines, all `True`.
* [ ] `NodeInstOp._instance_delete()` catches `InstancePowerOffFailed`,
      and a unit test shows a delete continues past a failed power off.
* [ ] Every guard in briefs 1 to 3 has a named test that fails without
      it. The management session has re-run at least two mutations per
      step.
* [ ] The functional run shows `test_lifecycle_pause_semantics` and
      `test_lifecycle_power_on_failure` passed, not skipped,
      and the existing lifecycle tests still pass.
* [ ] #2241 has the D7 comment.
* [ ] A short pause and unpause leaves the agent ready:
      `test_lifecycle_pause_semantics` passes in the functional run (D9).
* [ ] `pre-commit run --all-files` passes.

## Back brief

Before starting step 1, back brief Mikal on D1 and D5 together:

* **D1** changes open question 2's recorded answer from "507 or 500"
  to "500". The S3 evidence is that nothing can tell exhaustion apart.
* **D5** makes pausing and unpausing twice succeed rather than fail.

Both are API contract choices which are cheap to flip now and awkward
after clients have seen them. Steps 2 and 4 are unaffected by either.

## Future work

* Declaring 406 on the three non-power endpoints of S7,
  [#4449](https://github.com/shakenfist/shakenfist/issues/4449).
* A memory exhaustion signal for power on, if one is wanted. It belongs
  with the placement ledger, not in qemu's error text (D1).
* Delete undefines a domain whose `destroy()` failed, which turns a
  running persistent domain transient and leaves it running. This is
  pre-existing, found by this survey (S4), and unchanged here.
  Phase 4 corrected this: the undefine is skipped, because
  `power_off()` now raises, but the disks are still removed. Tracked as
  [#4486](https://github.com/shakenfist/shakenfist/issues/4486).
* Automatic recovery of a crashed guest on unpause (#2241, D7).
