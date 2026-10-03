# Phase 2 -- Autostart and instance restore

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
Phase 1b landed as [#4395](https://github.com/shakenfist/shakenfist/pull/4395)
(`447ed75ae`). The cleaner now sees inactive domains, records a guest
which powered itself off as `off` with libvirt's shutoff reason, and
takes the instance's node lock, with a two second bound, around every
write.

**Planning effort:** high, as the master plan asks. This phase changes
what every hypervisor does at boot, and what sf-queues does at start.
Getting autostart wrong in one direction leaves a user's powered off
instance running after a reboot. Getting it wrong in the other leaves
every running instance on a rebooted hypervisor powered off. **Review
effort:** high for step 2, which changes what the cleaner writes to
libvirt.

## Scope

**In scope:**

* F10. A domain's libvirt autostart flag says whether it should be
  running. Power on sets it, as now. Power off clears it, and so does
  the cleaner when it records an inactive domain as `off`. The cleaner
  rule also fixes the domains already powered off before this phase.
* Ordering every Shaken Fist daemon after `libvirt-guests.service`, so
  that at host shutdown the cleaner has stopped before libvirt-guests
  shuts guests down (D3).
* F2. Deleting the instance half of sf-queues' startup restore. The
  network restore and the placement reconciliation stay.
* F3. Deleting `_health_check_kvm_process()`.
* Unit tests for each change, each shown to fail without it, and a
  functional test that reads the autostart flag on the hypervisor.
* Correcting the master plan's phase 2 section where the survey found
  it wrong, and answering open question 1. Both are done in this
  phase's planning commit, so later steps should not redo them.

**Out of scope:**

* A functional test which reboots a hypervisor (D7).
* What the power operations return (F4 to F8, F12), which is phase 3.
  In particular `power_off()` still records `off` when `destroy()`
  failed (F5). This phase clears autostart where `off` is recorded, so
  phase 3 moves both together.
* Changing the distribution's `libvirt-guests` configuration (D4).
* Domains with a managed save image, which only exist if an operator
  configured `ON_SHUTDOWN=suspend` (Future work).
* Autostart on inactive domains of instances in an error state, which
  the cleaner returns early for (S9).

## What the survey found

The master plan's phase 2 section has the right three changes. One of
them, `setAutostart(0)` in `power_off()`, is not enough on its own: it
neither fixes the domains which are already powered off, nor covers a
guest which powered itself off. The master plan's section is corrected
in this phase's planning commit.

### S1 -- autostart is set on every power on and never cleared

`_power_on_inner()` calls `domain.setAutostart(1)` after
`domain.create()` succeeds (`instance.py:2388-2394`), and raises if that
fails. Nothing in the tree calls `setAutostart(0)`; `grep -rn
setAutostart shakenfist --include=*.py` finds that call and one test
fake (`test_instance.py:819`). `power_off()` (`instance.py:2411-2441`)
only calls `destroy()`. The flag lives with the persistent domain, which
an instance keeps from its first power on until it is deleted.

### S2 -- open question 1 is answered from the packages

Read from the packages themselves (`apt-get download`, then
`dpkg-deb -x`) in `debian:trixie` (libvirt 11.3.0-3+deb13u3) and
`ubuntu:24.04` (libvirt 10.0.0-2ubuntu8.19), on 2026-10-02:

| | Debian 13 | Ubuntu 24.04 |
|---|---|---|
| Package shipping `libvirt-guests.service` | `libvirt-daemon-common` | `libvirt-daemon-system` |
| Enabled by the postinst | yes | yes |
| `ON_BOOT` (from `libvirt-guests.sh`) | `ignore` | `ignore` |
| `ON_SHUTDOWN` | `shutdown` | `shutdown` |
| Uncommented `/etc/default/libvirt-guests` lines | none | `PARALLEL_SHUTDOWN=10`, `SHUTDOWN_TIMEOUT=120` |
| `WantedBy` | `multi-user.target` | `multi-user.target` |

The deployer configures none of it: `grep -rn libvirt-guests
shakenfist/deploy` finds nothing. libvirt 11's own `auto_shutdown_*`
settings in `qemu.conf` default to `none` for the system daemon, and
its comments say they must stay `none` while libvirt-guests is enabled.

So at host shutdown libvirt-guests asks each running guest to shut
down, and at boot it starts nothing itself. What starts a guest after a
reboot is the autostart flag alone. A user's powered off instance has
that flag set (S1), so it comes back. That is F10.

### S3 -- the cleaner could see host shutdown, and nothing orders it

If the cleaner writes autostart (D2), it must not be running while
libvirt-guests shuts guests down at host shutdown. If it were, it would
record each guest as `off` and clear its autostart, and the reboot would
leave every instance on the hypervisor powered off.

systemd stops units in the reverse of their start order. libvirt-guests
is `WantedBy=multi-user.target`, and a target is implicitly ordered
after the units it wants. The node role's `sf.service` template
(`deploy/collection/roles/node/templates/sf.service:23-41`) orders
`sf-database` after `multi-user.target`, and the other daemons after
`sf-database`. A hypervisor which runs no `sf-database` breaks that
chain, because ordering against a unit which is not loaded does
nothing. `sf.target` is ordered after `multi-user.target`, but that
orders the target, not the daemons it wants. Nothing guarantees the
order the cleaner rule needs, so this phase adds it (D3).

### S4 -- instance restore is a no-op, and deleting it is safe

`restore_instances()` (`daemons/queues/startup_tasks.py:99`) runs on a
background thread at sf-queues start (`:284-288`, `:377`). It:

1. collects this node's healthy instances, and their networks, at
   `:103-129`;
2. restores each network at `:140-150`;
3. "restores" each instance at `:152-166`, which is F2. `inst.power_state` is a
   dict, so `not in started` is always true and every instance is
   skipped, and `Instance.create_on_hypervisor()` does not exist;
4. reconciles placement references against the instance list from step
   1, at `:168` onwards.

Only step 3 goes. Steps 1, 2 and 4 keep working on the same instance
list. Nothing else names `create_on_hypervisor` on an instance. The
network method of that name, its `BridgedVXLanNetwork` worker, and
their tests are unrelated and stay.

The instance loop has no job anyway. A restart of sf-queues or
libvirtd leaves running domains running. After a hypervisor reboot,
autostart starts the domains that should be running (S2).

### S5 -- the restore test exercises the dead loop

`test_queues_startup_restore.py`'s `FakeInstance` (`:24-61`) stores
`power_state` as the string `'on'` and defines `create_on_hypervisor()`.
Those are the two details which hide F2. Its only test,
`RestoreInstancesErrorPathTestCase.test_failed_restore_enqueues_delete_and_continues`
(`:64-125`), is about the instance loop. The placement reconciliation
assertions at its end (`:120-125`) are the only cover the step 4
reconciliation has in that file, so they are kept in a rewritten test
rather than deleted with it. `StartupRestoreThreadTestCase` (`:128`
onwards) is about threading and is unaffected.

### S6 -- `_health_check_kvm_process()` is dead, and owns an import

`operations/node_inst_op.py:176-184` has no caller (`grep -rn
_health_check_kvm_process shakenfist` finds only the definition), and
it compares the `power_state` dict to `'on'`. It is the only user of
`psutil` in that module (`:4`, `:180`, `:181`), so the import goes too,
or flake8 fails.

### S7 -- the cleaner's two `off` branches are where autostart is cleared

In `_update_inactive_domain()` (`daemons/cleaner/scheduled_tasks.py:334`):

* `:424-427`: when the database already says `off`, the function
  returns without a lock (phase 1b's S14). This is where the domains
  already powered off before this phase are found. Every user power
  off before now left autostart set.
* `:429-456`: the locked branch which records a detected power off.

The plan first said the caller (`:558-568`) catches `libvirtError` per
domain. It does not: the `try` wraps the whole loop, so one failed
`setAutostart()` would have ended the pass for every domain listed after
it. Step 2 found this and gave the call to `_update_inactive_domain()`
its own `try`, which logs the error and moves to the next domain; the
next pass retries.

`domain.autostart()` and `setAutostart()` are local libvirt calls, so
the already-off branch costs no database load. It takes the node lock
once for each domain that still has the flag set.

### S8 -- the power endpoints already hold the lock the cleaner takes

`InstancePowerOffEndpoint` and `InstancePowerOnEndpoint` call
`power_off()` and `power_on()` inside `get_lock(..., global_scope=False)`
(`external_api/instance.py:1597`, `:1622`). The queued delete calls
`power_off()` inside the same lock (`node_inst_op.py:188`, `:219`).
Taking that lock and looking the domain up again inside it (phase 1b's
`_instance_lock()` and `_inactive_domain_inside_lock()`) therefore
cannot clear the flag on a domain which a concurrent power on has just
started.

### S9 -- inactive domains of errored instances keep autostart

`_update_inactive_domain()` returns early for building states, deleted
and delete-wait instances, and the files-missing branch, before it
reaches either `off` branch. An errored instance whose files are gone
keeps its autostart flag. libvirt cannot start it after a reboot either,
because its disks are missing, so this is recorded rather than fixed.

## Decisions

### D1 -- autostart means "should be running"

Power on sets the flag, as it does now. Every path which records `off`
for an inactive domain clears it: `power_off()` and the cleaner. After
a hypervisor reboot, exactly the instances the database last recorded
as running come back.

In `power_off()` the clear goes next to `update_power_state('off')`, not
before `destroy()`. Phase 3 (F5) will stop `power_off()` recording `off`
when `destroy()` failed, and it should then move both together.

### D2 -- the cleaner clears autostart too

This is the decision most likely to be argued with. The alternative is
to clear the flag only on a user's power off, and let a guest which
powered itself off come back after a hypervisor reboot.

The cleaner clears it, for three reasons:

* `power_state` is what users read. Phase 1b made it say `off` for a
  guest which powered itself off. If that guest then started after a
  reboot, `off` would only have been true until the next reboot.
* The cleaner is the only place which can fix the domains that were
  powered off before this phase. Before 1b, `off` in the database could
  only have come from `power_off()`, so every such domain has autostart
  set.
* It makes `power_off()`'s clear self-healing. If `setAutostart(0)`
  fails there, the next cleaner pass clears it (D5).

The cost is that the cleaner must never run while libvirt-guests shuts
guests down at host shutdown, or a reboot powers everything off. D3
makes that an explicit systemd ordering rather than an accident. The
functional test checks the ordering on a real node.

### D3 -- every Shaken Fist daemon starts after libvirt-guests

Add `After=libvirt-guests.service` to the node role's `sf.service`
template for every daemon, so that systemd stops them all before
libvirt-guests at shutdown. Ordering against a unit which is not loaded
does nothing, so this is harmless on a node without libvirt.

It also means the daemons start after libvirt-guests at boot. With
`ON_BOOT=ignore` that unit does nothing at start, so this delays
nothing.

### D4 -- the distribution's libvirt-guests configuration stays

Nothing in D1 depends on `ON_BOOT` or `ON_SHUTDOWN`. With the defaults,
guests are shut down cleanly and autostart decides which come back. If
an operator sets `ON_BOOT=start`, libvirt-guests restarts the guests
which were running, which autostart would have done anyway. If an
operator sets `ON_SHUTDOWN=suspend`, guests are saved and restored, and
that only creates managed save images (Future work).

Changing these settings would be an upgrade-time behaviour change for
existing operators, with nothing to gain. The operator guide says what
the defaults do.

### D5 -- a failure to clear autostart is recorded, not raised

If `setAutostart(0)` raises in `power_off()`, add an audit event,
`instance autostart configuration error`, with the error in `extra`.
This is the message power on already uses. Then carry on and return as
now.

The domain is off. Raising would turn a successful power off into a
500. The next cleaner pass finds the domain off with autostart still
set, and clears it (D2).

Power on keeps raising when `setAutostart(1)` fails. That is phase 3's
business if it is anyone's.

### D6 -- the cleaner clears the flag under the lock, and only if `off` still holds

* **Detected power off branch.** Once it has written `off`, and while
  it still holds the lock, the branch calls `setAutostart(0)` on the
  domain it looked up again inside the lock.
* **Already off branch.** With no lock, it calls `domain.autostart()`.
  If that returns false, it returns, so the steady state takes no lock
  and makes no write. Otherwise it takes `_instance_lock()`, looks the
  domain up again with `_inactive_domain_inside_lock()`, checks that the
  power state still reads `off`, and only then calls `setAutostart(0)`.
  It logs at info level, and adds an audit event, `autostart cleared`,
  with `extra={'reason': 'instance is powered off'}`. The event is once
  per domain, so the cost is bounded.

Both re-checks are what make D2 safe against a power on which is
concurrent with the cleaner (S8).

### D7 -- the flag is tested, not a hypervisor reboot

A functional test which reboots a hypervisor would need the node
lifecycle job's machinery, and that job is the least stable in CI.
What this phase changes is the flag. libvirt's autostart semantics, and
the package defaults in S2, are what turn the flag into behaviour at
boot. So the functional test reads the flag with `virsh dominfo` on the
instance's hypervisor:

* set after create;
* cleared after an API power off;
* set again after power on;
* cleared after the cleaner detects a killed qemu.

It also reads D3's ordering from systemd on that node. A reboot test
goes in Future work.

### D8 -- `restore_instances()` keeps its name

After this phase it restores networks and reconciles placement, so the
name is still accurate. It is referenced by the background thread and
by `StartupRestoreThreadTestCase`, and renaming it is churn. Its first
comment is updated to say that instances are not restored, and why.

## Step plan

Steps run one at a time in the phase worktree. Sub-agents must not run
in parallel in one worktree, because pre-commit's stash is repo-wide.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | `power_off()` clears autostart. See brief 1. Commit: "Clear autostart when powering an instance off." |
| 2 | high | opus | none | The cleaner clears autostart on `off` inactive domains. See brief 2. Commit: "cleaner: clear autostart on powered off domains." |
| 3 | low | sonnet | none | Order SF daemons after libvirt-guests. See brief 3. Commit: "Stop SF daemons before libvirt-guests." |
| 4 | medium | sonnet | none | Delete the instance restore loop and `_health_check_kvm_process()`. See brief 4. Commit: "Remove the instance restore that never ran." |
| 5 | medium | sonnet | none | Functional test of the autostart flag. See brief 5. Commit: "Test the autostart flag on the hypervisor." |
| 6 | medium | sonnet | none | Documentation. See brief 6. Commit: "Document power state across a reboot." |
| 7 | -- | management | -- | Dispatch the functional tests on the branch and read the result. See brief 7. |

Every step ends with `pre-commit run --all-files` passing. The
management session reads each diff before committing it.

### Brief 1 -- `power_off()` clears autostart

In `shakenfist/instance.py`, `power_off()` (lines 2411-2441):

* Immediately before `self.agent_state = constants.AGENT_INSTANCE_OFF`,
  call `inst.setAutostart(0)` on the domain. Note that `inst` is the
  libvirt domain in this method.
* Wrap the call in `try`/`except lc.libvirt.libvirtError as e`. In the
  except branch, call
  `self.add_event(EVENT_TYPE_AUDIT, 'instance autostart configuration error', extra={'message': str(e)})`
  and carry on: the power state and the poweroff event are still
  written, and nothing is raised (D5).
* Add a comment saying why: the flag is what restarts a domain after a
  hypervisor reboot, and the cleaner retries a failed clear.

Unit tests go in `shakenfist/tests/test_instance.py`, in a new
`InstancePowerOffAutostartTestCase(InstanceLibvirtTestCase)` placed
after `InstancePowerEventTimestampsTestCase`. Use a small domain fake
derived from `FakeDomain`, as `ClockedDomain` is (line 802), which
records each `setAutostart` flag and can raise on it. Patch `add_event`
as `InstancePowerEventTimestampsTestCase` does. Test:

* a power off calls `setAutostart(0)` exactly once;
* the clear also happens when `destroy()` raises "domain is not
  running";
* when `setAutostart` raises `FakeLibvirtError`, power off still returns
  normally, the power state is `off`, and the events include both
  `instance autostart configuration error` and `poweroff`;
* with no domain (`_mock_libvirt(None)`), nothing is called.

`ClockedDomain.setAutostart` currently does nothing (line 819). Leave
it, since those tests do not care. Check that each new test fails when
the `setAutostart(0)` line is removed, and say so in your report.

### Brief 2 -- the cleaner clears autostart on `off` inactive domains

This changes what the cleaner writes to libvirt, so read
`docs/plans/PLAN-power-state-correctness-phase-02-autostart-restore.md`,
sections S3, S7, S8, D2 and D6, before starting. Also read
`_update_inactive_domain()` in
`shakenfist/daemons/cleaner/scheduled_tasks.py` (line 334 onwards), and
its helpers `_instance_lock()` and `_inactive_domain_inside_lock()`.

In `_update_inactive_domain()`:

* **Detected power off branch** (lines 429-456). After the `detected
  poweroff` event is added, still inside the `with _instance_lock(...)`
  block, call `domain.setAutostart(0)` on the domain returned by
  `_inactive_domain_inside_lock()`. Keep the existing order of the
  power state, agent state and event: the functional suite waits for the
  event and then reads the state once. The autostart clear goes after
  them because nothing waits on it.
* **Already off branch** (lines 424-427), replacing the bare `return`:
  * if `not domain.autostart()`, return, taking no lock;
  * otherwise `with _instance_lock(inst, pet) as locked:`. Return if
    not locked. Then call `_inactive_domain_inside_lock(lc,
    instance_uuid, log_ctx)` and return if it gives None. Return if
    `inst.power_state.get('power_state') != 'off'`.
  * Then call `setAutostart(0)` on the fresh domain. Log at info
    level, "Cleared autostart on powered off instance". Add
    `inst.add_event(EVENT_TYPE_AUDIT, 'autostart cleared', extra={'reason': 'instance is powered off'})`.
  * Put that in a small helper if it reads better, but keep the
    unlocked `autostart()` check outside the lock.
* Do not catch `libvirtError` here. The caller (around line 566)
  logs it and moves to the next domain, and the next pass retries.
  (As planned, this said the caller already did that. It did not, and
  step 2 added the per-domain catch; see S7.)

In `shakenfist/tests/test_daemon_cleaner.py`:

* Give `FakeLibvirtDomain` (line 160) an `autostart` constructor
  argument, defaulting to True since every SF domain in production has
  it set. Add an `autostart()` method returning it, and a
  `setAutostart(flag)` which records the call in a module list. Follow
  the pattern of `_test_undefined_domains`, and reset it the same way.
* Add tests to `CleanerInactiveDomainTestCase`, using its
  `_inactive_instance()` helper:
  * detected power off clears autostart, after writing `off`;
  * already `off` with autostart set takes the lock and clears it, once;
  * already `off` with autostart clear takes no lock and makes no call;
  * already `off`, lock busy (`FakeInstanceLocks` timeout): no clear;
  * already `off`, domain active when looked up again inside the lock:
    no clear;
  * already `off`, but the power state reads `on` once the lock is held:
    no clear. A concurrent power on finished first.
  * a building, deleted or delete-wait instance, and the files-missing
    branch: no autostart call at all.
  * `setAutostart` raising `FakeLibvirtError` does not stop the next
    domain being processed.

Mutation-check each guard: remove it, run the tests, and confirm a
named test fails. List each mutation and the test that caught it in
your report. The management session re-runs at least two of them.

### Brief 3 -- order SF daemons after libvirt-guests

In `shakenfist/deploy/collection/roles/node/templates/sf.service`, add
`After=libvirt-guests.service` to every daemon's `[Unit]` section,
including `database`, `sentinel-first`, `privexec`, `nodelock` and
`sentinel-last`. The simplest way is one line outside the
`{% if %}` chain, just before `PartOf=sf.target`.

Add a comment in the style of the long comment at the top of the file
saying three things:

* systemd stops units in reverse start order, so this stops every SF
  daemon before libvirt-guests shuts guests down at host shutdown;
* the cleaner must not see that shutdown, or it would record every guest
  `off` and clear its autostart;
* the existing chain through `sf-database` does not guarantee it on a
  node without `sf-database`.

Point at
`docs/plans/PLAN-power-state-correctness-phase-02-autostart-restore.md`
S3 and D3.

Check that the rendered unit is still valid. Render the template for
two items (`cleaner` and `database`) with a short jinja2 snippet, and
paste the `[Unit]` sections in your report. Do not touch any other
file.

### Brief 4 -- remove the instance restore that never ran

In `shakenfist/daemons/queues/startup_tasks.py`:

* Delete the `for inst in instances:` loop at lines 152-166.
* Update the comment at the top of `restore_instances()` (line 100) to
  say three things:
  * instances are deliberately not restored here;
  * libvirt keeps running domains running across a restart of sf-queues
    or libvirtd;
  * after a hypervisor reboot, a domain's autostart flag decides
    whether it starts.

  Point at `docs/plans/PLAN-power-state-correctness.md` F2 and F10.
* Fix the comment at lines 168-171, which says "the restore work above
  can take many minutes". It is now the network restore.
* Keep everything else: the instance collection, the network restore
  and the placement reconciliation.

In `shakenfist/operations/node_inst_op.py`, delete
`_health_check_kvm_process()` (lines 176-184) and the `import psutil`
at line 4. Run `grep -n psutil` on the file afterwards to confirm
nothing else used it.

In `shakenfist/tests/test_queues_startup_restore.py`:

* Remove `power_state`, `fail_restore`, `restored` and
  `create_on_hypervisor()` from `FakeInstance`.
* Replace `RestoreInstancesErrorPathTestCase` with a test case for the
  restore as it now is. With two instances, assert three things:
  * no instance has `enqueue_delete_due_error` called;
  * both instances' placements are reconciled through
    `admit_instance_placement` with `enforce=False`, as the old test's
    last assertions did;
  * nothing calls a `create_on_hypervisor` on an instance. A plain
    class without the method, as `FakeInstance`'s docstring explains,
    makes any such call raise.
* Keep the module docstring's account of the threading regression,
  which `StartupRestoreThreadTestCase` covers.

Confirm the new test fails if the deleted loop is restored. Restoring
it raises `AttributeError` on the fake, which `ignore_exception` hides,
and then the delete is enqueued. Say what you checked.

### Brief 5 -- test the autostart flag on the hypervisor

In `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_state_changes.py`:

* Add a helper to the test class, or to `base.py` if another test file
  could use it. It is
  `_domain_autostart(node, instance_uuid)`. It runs
  `self._node_exec(node, ['virsh', 'dominfo', 'sf:%s' % instance_uuid], sudo=True)`,
  finds the `Autostart:` line, and returns True for `enable` and False
  for `disable`. It fails the test, with the output, if the line is
  missing.
* Add `test_lifecycle_power_off_clears_autostart`, following
  `test_lifecycle_qemu_killed` (line 292) for:
  * `_start_target()`;
  * `_node_by_uuid(inst['node'])`;
  * `_require_node_exec(node)`, which must be called before any node
    command so that the test skips visibly.

  Then assert, in order:
  * autostart is enabled after create;
  * after `self.test_client.power_off_instance(inst['uuid'])` it is
    disabled;
  * after `power_on_instance` and the existing wait for the agent, used
    by `test_lifecycle_power_cycle` (line 157), it is enabled again.

  Each assertion message names the step.
* In `test_lifecycle_qemu_killed`, after the existing assertions, assert
  that autostart is disabled once the cleaner has recorded the power
  off. That is the cleaner's path (D2). The detected poweroff event
  comes before the clear, so poll for up to 120 seconds, every 5
  seconds, before failing. The cleaner writes the event and then clears
  the flag in the same locked block, so a single pass should be enough.
* In the new test, also check D3's ordering on the node:
  `self._node_exec(node, ['systemctl', 'show', '-p', 'After', 'sf-cleaner.service'])`.
  Assert that `libvirt-guests.service` is in the output, with a message
  pointing at the phase plan's D3.

Add a unit test to `shakenfist/tests/test_ci_power_state.py`, the same
way phase 1b tested `_detected_poweroff_reason`. It covers
`_domain_autostart` parsing `enable`, `disable`, and output with no
`Autostart:` line.

### Brief 6 -- document power state across a reboot

In `docs/operator_guide/power_states.md`, add a section on what happens
to instances when a hypervisor reboots:

* Shaken Fist sets libvirt's autostart flag when it powers an instance
  on, and clears it when the instance is powered off, either by the API
  or because the cleaner found the guest off.
* After a reboot, exactly the instances which were running come back.
* The distribution's `libvirt-guests` defaults on Debian 13 and Ubuntu
  24.04 shut guests down cleanly at host shutdown and start nothing at
  boot. Shaken Fist does not change them. Give the S2 table's values.
* What `ON_BOOT=start` and `ON_SHUTDOWN=suspend` would change, per D4.
* SF daemons are ordered after `libvirt-guests.service` so that the
  cleaner is stopped before guests are shut down. Say why.
* The `autostart cleared` event, and `instance autostart configuration
  error` on power off.

Also say that sf-queues does not restart instances when it starts.
Check `docs/operator_guide/upgrades.md` around line 67, which mentions a
hypervisor reboot, and correct it if it says anything this phase makes
false. Do not touch `ARCHITECTURE.md`, `AGENTS.md` or `README.md`: no
component or convention changes.

### Brief 7 -- functional run

The management session dispatches the functional tests workflow on the
branch and reads the Guests job. It confirms that
`test_lifecycle_power_off_clears_autostart` and
`test_lifecycle_qemu_killed` ran rather than skipped, and that both
passed. If the ordering assertion fails, D3's template change did not
reach the node, and that is investigated, not weakened. The run URL
goes in the pull request description.

## Risks and mitigations

* **A reboot powers off every instance on a hypervisor.** This happens
  if the cleaner records guests `off` while libvirt-guests shuts them
  down. Mitigation: D3's explicit ordering, asserted on a real node by
  brief 5. D6's re-check inside the lock does not help here, because the
  guests really are off. The ordering is the guard, which is why it is
  asserted rather than assumed.
* **The first deploy clears autostart on every powered off domain at
  once.** One lock and one event per domain, once. That is bounded by
  the number of powered off instances on a node, and all of them are
  meant to stay off. sfcbr's count is unknown, because phase 1b's
  inventory was not taken (Future work).
* **A cleared flag on an instance which should be running.** This can
  only happen through a race with power on. D6 re-checks under the
  power endpoints' own lock, and brief 2 tests both re-checks with
  mutation checks.
* **Deleting restore hides a case it handled.** S4 shows it handled
  none, and brief 4's test shows no instance is touched.
* **The unit fakes lie again.** Brief 2's fake defaults to autostart
  set, as production is, and brief 4's `FakeInstance` loses the two
  details which hid F2.

## Definition of done

* [ ] `grep -rn 'setAutostart(0)' shakenfist --include=*.py | grep -v tests`
      prints exactly the `power_off()` call and the cleaner's calls.
* [ ] `grep -rn "create_on_hypervisor" shakenfist/daemons/queues/startup_tasks.py`
      prints only the network call.
* [ ] `grep -rn "_health_check_kvm_process\|import psutil" shakenfist/operations/node_inst_op.py`
      prints nothing.
* [ ] `grep -n "libvirt-guests.service" shakenfist/deploy/collection/roles/node/templates/sf.service`
      prints the `After=` line, and it is outside the `{% if %}` chain.
* [ ] `grep -n "power_state = 'on'\|def create_on_hypervisor" shakenfist/tests/test_queues_startup_restore.py`
      prints nothing.
* [ ] Every guard in brief 2 has a named test which fails without it.
      The management session has re-run at least two of the sub-agent's
      mutations.
* [ ] The functional run shows `test_lifecycle_power_off_clears_autostart`
      and `test_lifecycle_qemu_killed` passed, not skipped.
* [ ] `docs/operator_guide/power_states.md` names both new events and
      the `libvirt-guests` defaults, and no page in `docs/` other than
      `docs/plans/` says sf-queues restarts instances.
* [ ] `pre-commit run --all-files` passes.

## Back brief

Before starting step 1, back brief Mikal on D2 and D3 together. The
cleaner clearing autostart on a guest which powered itself off is a
behaviour choice. It is safe only because of D3's systemd ordering,
which this phase adds rather than finds. If Mikal prefers a
user-only clear, steps 2 and 3 shrink to the already-off branch's
migration. That would also have to consult events, because a domain
which is `off` in the database no longer says who powered it off.
Either way, steps 1 and 4 are unaffected.

## Future work

* A node lifecycle test which reboots a hypervisor with one running and
  one powered off instance, and checks which comes back (D7).
* Domains with a managed save image. `ON_SHUTDOWN=suspend` creates
  them, and `domain.undefine()` refuses such a domain without
  `VIR_DOMAIN_UNDEFINE_MANAGED_SAVE`, including in the cleaner's
  `_undefine_domain()`. Only reachable if an operator changes the
  libvirt-guests default (D4).
* Autostart on inactive domains of errored instances (S9).
* Phase 1b's sfcbr inactive-domain inventory was never taken, so how
  many domains its first deploy acted on, and this phase's will, is
  unknown. Reading the cleaner's `detected poweroff`, `autostart
  cleared` and unknown-domain log lines on sfcbr after deploy would
  answer it.
