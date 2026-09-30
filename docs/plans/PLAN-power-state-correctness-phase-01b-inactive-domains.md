# Phase 1b -- The cleaner sees powered off domains

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
Phase 1a landed as [#4372](https://github.com/shakenfist/shakenfist/pull/4372)
(`8aa69c5e4`). It gave the cleaner `get_inactive_sf_domains()`, made the
cleaner test's libvirt fake list domains the way libvirt does, and left
`test_update_power_states_detects_shutoff` as an expected failure for
this phase to turn on.

**Planning effort:** high, as the master plan asks. This phase makes
live the cleaner code which deletes disks and undefines domains, and
that code has not run against a powered off domain since 2022. A wrong
answer here is data loss, not a wrong status field. **Review effort:**
high for steps 4 and 5, which change what the cleaner deletes.

## Scope

**In scope:**

* Pointing the cleaner's second loop at `get_inactive_sf_domains()`, so
  a guest which powers itself off is recorded as `off`, with its
  `agent_state` and libvirt's shutoff reason.
* The guards that the second loop's deleting branches never needed
  while they were dead: an instance lock with a bounded wait, a re-read
  of the domain inside it, a validated uuid, a confirmed absence before
  deleting an unknown domain, a re-entry guard on "instance files
  missing", and a delete path which does not leak.
* Applying the same lock, re-read, uuid validation and confirmed
  absence to the first loop. The lock and re-read fix F13.
* Giving the apparmor sweep its own listing of every defined domain.
* Unit tests for every guard, each shown to fail when its guard is
  removed, and a functional test of a guest powering itself off.
* Correcting the master plan's phase 1b section where the survey found
  it wrong. That is done in this phase's planning commit, so later steps
  should not redo it.

**Out of scope:**

* The rest of [#3373](https://github.com/shakenfist/shakenfist/issues/3373):
  the gRPC wrappers in `mariadb.py` still report a non-retryable
  `RpcError` as "not found". This phase adds a strict lookup for the
  cleaner alone (D3), shaped so #3373 can later make it the default.
* Autostart, instance restore and F3's `_health_check_kvm_process`,
  which are phase 2.
* What power operations return (F4 to F8, F12), which is phase 3.
* Mapping libvirt's rarer states (F9), and the first loop's unreachable
  `crashed` branch beyond documenting it (D7).
* Stray domains belonging to instances whose delete ended in state
  `error` (D9).
* Changing `instances_total` in the resources daemon.

## What the survey found

The master plan's phase 1b section has the right list of hazards, but
three of its prescriptions would not work as written, and the survey
found one hazard it does not list. The master plan's phase 1b section
is corrected in this phase's planning commit.

### S1 -- a node lock cannot time out

`NodeLock.__enter__` (`util/concurrency.py:386-404`) retries every 0.2
seconds until it gets the lock, forever. `get_lock()` drops its
`timeout` argument for node scope (`baseobject.py:537-538`), so sf-queues
restore's `get_lock(timeout=120, ..., global_scope=False)`
(`daemons/queues/startup_tasks.py:154`) has never timed out. The master
plan's "instance lock with a short timeout, skip if busy" therefore
needs a timeout added first. That is cheap: a single lock request is
already non-blocking, because `_node_lock_request()` returns whether the
lock was granted (`util/concurrency.py:326-364`).

### S2 -- the instance lock is a complete guard

Every call that defines, starts, destroys or undefines one of our domains
is in `instance.py`:
* `_power_on_inner()`
* `_power_on_retry_prep()`
* `power_off()`
* `_delete_on_hypervisor()`

Every caller of those holds the instance's node lock:
* the power endpoints (`external_api/instance.py:1529-1660`);
* instance create and start (`operations/node_inst_netdesc_op.py:469`
  and `:368`);
* the queued delete (`operations/node_inst_op.py:188`).

The cleaner is the only thing that changes an instance's power state
without the lock. A cleaner which takes the lock and re-reads the
domain inside it cannot observe a domain mid-way through a power on,
power off or delete. The master plan's skip of `initial`, `preflight`
and `creating` becomes belt and braces, and costs nothing, so it stays.

### S3 -- `inst.delete()` is not the delete path

For the `delete-wait` branch the master plan proposes `inst.delete()`,
"as the first loop does" (F11). `delete()` runs
`_delete_on_hypervisor()` and `_delete_globally()`
(`instance.py:1616-1627`). The queued delete also does two things
`delete()` does not (`operations/node_inst_op.py:201-240`):
* It deletes the instance's network interfaces.
* It cleans up networks no longer used on the node.

`_delete_globally()` sets the state to `deleted`. Any queued delete
behind it then returns early (`node_inst_op.py:193`), so the interfaces
would never be deleted. The delete path is `enqueue_delete()`
(`instance.py:2515`) (D5).

The first loop's `inst.delete()` does not have this problem. It only
runs for instances already in `deleted`, whose global teardown has run.

### S4 -- for `deleted`, the by-hand teardown is correct

When the instance is already `deleted`, the second loop's local rmtree
and undefine are the right action: the global half ran, and a queued
delete would return early. Only its `state = deleted` write is redundant,
and it is a no-op anyway (`baseobject.py:586-587`).

### S5 -- a database error can still read as "unknown domain, delete it"

Both of the cleaner's `not inst` branches treat `Instance.from_db()`
returning `None` as "a Shaken Fist domain the database has never heard
of", and delete it:
* **The first loop, live today.** It runs `virsh destroy`, `virsh
  undefine` and rmtree on the instance's directory
  (`scheduled_tasks.py:115-119`, `:38-58`). That is a running instance
  and its disks.
* **The second loop, once this phase makes it live.** It would do the
  same to a powered off instance.

**What #3373's first half already covers.** It landed as `19f2d5354`,
although the issue is still open. An outage which exhausts
`_grpc_call()`'s retries on `UNAVAILABLE`, `DEADLINE_EXCEEDED` or
`CANCELLED` now raises `DatabaseUnavailable` (`mariadb.py:741`, `:900`).
That exception is not a `grpc.RpcError`, so it gets past
`_grpc_get_instance()`'s handler. It is not a `libvirtError` either, so
it aborts the cleaner's pass rather than deleting anything, and
`_resilient_job` retries the pass a minute later. The broad outage
case is safe today.

**What is still open.** Any *non-retryable* `RpcError` is raised on
the first attempt. `_grpc_get_instance()` still maps it to `None`
(`mariadb.py:20371-20375`). The likely source is `UNKNOWN` or
`INTERNAL`, which is what sf-database answers when its handler raises,
for example on the InnoDB trouble recorded against sf-database. #3373
lists that as its follow-on work. `_direct_get_instance()` does the same
with `OperationalError`, but only sf-database and sf-ctl use the direct
path (`DIRECT_MARIADB_CALLERS`, `mariadb.py:566`), and the cleaner never
does.

So a narrower hazard remains live in the first loop and would become
live in the second: one server-side error on one lookup deletes one
instance's disks.

### S6 -- the domain name is trusted

The instance uuid is `domain.name().split(':')[1]`, unvalidated.
* **A domain named `sf:`.** `from_db('')` returns `None` early
  (`baseobject.py:373-374`), so `_delete_instance_files('')` removes
  `STORAGE_PATH/instances/` itself.
* **A domain named `sf:garbage`.** It raises `ValueError` from `UUID()`
  in `Instance._db_get()` (`instance.py:487`). That is not a
  `libvirtError`, so it escapes the loop's handler and aborts the rest
  of the pass, apparmor sweep included, every minute.

The first loop also interpolates the suffix into a shell command
(`scheduled_tasks.py:43-45`).

`libvirt.tmpl` sets the domain's `<uuid>` to the instance uuid, so
`domain.UUIDString()` is an independent check.

### S7 -- the apparmor sweep reads the second loop's list

`all_libvirt_uuids` is filled by the second loop (`scheduled_tasks.py:223-227`).

**If that loop reads inactive domains.** The sweep would believe no
running domain exists. Running Shaken Fist instances would survive only
because their instance directory also exists.

**What the list has held since before 1a.** Only `sf:` domains (F1).
So the profiles of a foreign VM on the hypervisor are already eligible
for deletion once they are old enough.

**If listing fails.** The list stays empty, and every old profile
without an instance directory becomes eligible.

### S8 -- error states re-enter

The "instance files missing" branch writes `<state>-error`:
* From an error state, that is invalid, and raises.
* From `error`, it writes `error-error`, which is equally invalid.

`error` matters because `_delete_globally()` leaves an errored
instance's delete in state `error`, not `deleted` (`instance.py:1611-1614`).
None of the branches' skip lists include `error`.

### S9 -- the shutoff reason is available and unused

`extract_power_state()` discards the reason `domain.state()` returns
(`util/libvirt.py:72-86`). The pause reason already has a
display-string table and a raw-enum predicate (`util/libvirt.py:12-31`,
`:88-123`), which is the precedent here.

libvirt's shutoff reasons are `UNKNOWN`, `SHUTDOWN`, `DESTROYED`,
`CRASHED`, `MIGRATED`, `SAVED`, `FAILED`, `FROM_SNAPSHOT` and `DAEMON`.
The expected mappings are:
* a guest `poweroff` gives `SHUTDOWN`;
* our `power_off()` (`destroy()`) gives `DESTROYED`;
* a qemu killed by a signal gives `CRASHED`, according to libvirt's qemu
  driver.

The last is unverified here, and step 6 checks it where the suite can
run commands on the hypervisor.

### S10 -- `crashed` is documented as terminal and is never produced

`docs/operator_guide/power_states.md:11` says a `crashed` instance is
also in state `error`. Only the first loop writes `crashed`, for an
*active* crashed domain. Phase 1a found that branch unreachable, because
`on_crash` is `restart` in `libvirt.tmpl` and there is no panic device.

### S11 -- nothing writes `agent_state` when a guest powers itself off

The sidechannel drops its monitor within a second of the domain going
inactive (`daemons/sidechannel/main.py:2033-2053`). It writes nothing on
the way out, so `agent_state` keeps its last value, usually ready. Only
`power_off()` writes `AGENT_INSTANCE_OFF` (`instance.py:2409`).

### S12 -- the unit test fakes need three honest details

In `test_daemon_cleaner.py`:
* `FakeLibvirtDomain.UUIDString()` returns `'fake_uuid'`.
* It has no `isActive()`.
* The connection's `lookupByName()` returns a *running* domain whatever
  it is asked for.

Nothing fakes the node lock, which has no socket in unit tests. The
cleaner does not take one today.

### S13 -- the functional helpers fit, and one message goes stale

`_await_power_off()` (`deploy/shakenfist_ci/base.py:787`) waits for the
"detected poweroff" event, and nothing calls it yet.
`_await_instance_event()` allows five minutes after create, which covers
one or two cleaner passes (open question 5).

`_assert_power_state()` reads once, by phase 0's D1. It appends an F13
hint to its failure message (`base.py:815-817`), which this phase makes
stale.

### S14 -- database load

**Steady state.** A powered off instance whose database row already says
`off` costs the cleaner, per pass:
* its static values (cached for 300 seconds);
* its state;
* `place_instance()`, which takes and releases the placement attribute
  lock, reads the placement and returns early;
* its power state.

That is four or five requests a minute, 0.07 to 0.08 requests a second.
It is the same as the cleaner already spends on each running instance,
and the node lock (D1) is a local socket that costs the database
nothing.

**The budget.** `shakenfist/data/database_load_budget.yaml` charges the
cleaner per standing instance, meaning per instance in state `created`.
Powered off instances are already counted in that. So the model's form
does not change. Its fitted slope was measured on clusters where few
instances were off, so it rises by at most the fraction of instances
powered off, and on CI that is close to zero.

**Where the new cost goes.** A lock and a re-read are taken only when
the cleaner is about to write, which is once per power change, not once
per pass.

No budget change is proposed.

## Decisions

### D1 -- node locks get an optional timeout

`NodeLock(name, timeout=None)`. `None` keeps today's wait-forever
behaviour. A number raises a new `exceptions.NodeLockTimeout` (a
`LockException`) once that many seconds have passed without the lock.
`get_lock()` gains a keyword `node_timeout=None`, passed only to
`NodeLock`.

`get_lock()`'s existing `timeout` is not reused for node scope:
* sf-queues restore passes `timeout=120` with `global_scope=False`, and
  would start timing out;
* every other node-scope caller gets the default of 60 without asking
  for it.

Neither change belongs in this phase.

### D2 -- every cleaner write happens under the lock, after a re-read

Before the cleaner writes an instance's `power_state`, `agent_state` or
`state`, or enqueues or performs a delete, it:
1. takes `inst.get_lock(op=..., global_scope=False, node_timeout=2)`;
2. inside the lock, looks the domain up again with
   `lc.get_domain_from_sf_uuid()` and re-derives what it was about to
   write from that fresh domain and a fresh read of the instance;
3. writes only if the fresh reading still calls for it.

**When the lock is unavailable.** On `NodeLockTimeout` or
`MissingNodeLockSocket` the domain is skipped for this pass at debug
level. The next pass retries.

**The steady state takes no lock.** When the database already agrees
with libvirt there is nothing to write. This applies to both loops, and
in the first loop it is the fix for F13.

**Why two seconds.** Holders of the instance lock are power operations,
creates and deletes, which take seconds to minutes. Waiting on them
would hold up the whole pass, and a minute later the cleaner gets
another chance.

### D3 -- confirm absence before deleting an unknown domain

`exceptions.DatabaseUnavailable` already exists (S5). Add a keyword-only
`strict=False` to `mariadb.get_instance()`. When it is true, the
wrapper re-raises *any* `grpc.RpcError` it would have mapped to `None`
as `DatabaseUnavailable`, chained from the original error, and the
direct path does the same for `OperationalError`. A genuine miss still
returns `None`.

In both loops, when `Instance.from_db()` returns `None`, the cleaner
calls `mariadb.get_instance(uuid, strict=True)`:
* **It raises.** The domain is skipped for this pass, and the cleaner
  logs a warning naming #3373.
* **It returns a row.** The first lookup was the error. The domain is
  skipped for this pass, and the next pass handles it normally.
* **It returns `None` without raising.** The domain really is unknown,
  and the existing deletion proceeds.

`DatabaseUnavailable` raised by `from_db()` itself keeps aborting the
pass, as it does today. The whole database is unreachable, so nothing
else in the pass would succeed either.

**Why not finish #3373.** Mapping non-retryable errors to an exception
in all of the wrappers changes every caller behind them, which is a
change of its own.

**Why in this phase.** This phase turns the second loop's deleting
branch on, and the first loop's branch is the same code shape, so it
uses the same helper.

The strict path is written so #3373 can make it the default and delete
the keyword.

### D4 -- a domain's name must agree with its uuid

One helper, `_sf_instance_uuid(domain) -> str | None`, returns the name's
suffix only when it equals `domain.UUIDString()`. Otherwise it logs a
warning naming both, and the cleaner skips that domain entirely: no
lookup, no delete, and no `virsh` command. Both loops call it before
anything else.

This is stronger than `util_general.valid_uuid4()`, which accepts any
32 hex digits because `uuid.UUID(..., version=4)` overwrites the version
bits rather than checking them. It also fails safe. A legacy domain
whose uuid does not match is left alone rather than deleted.

### D5 -- a stale `delete-wait` instance is re-enqueued, not deleted in place

This deliberately differs from the master plan's F11 prescription, for
the reason in S3.

**For `delete-wait`.** After the existing five minute grace, and under
D2's lock, the cleaner calls `inst.enforced_deletes_increment()`. It then
acts on the count:
* On counts 1, 6 and 11, roughly five minutes apart, it calls
  `inst.enqueue_delete()` and adds the audit event "stray powered off
  instance delete enqueued" with `{'attempt': n}`.
* On count 16 it adds "stray powered off instance delete abandoned" at
  error level.
* On other counts it does nothing.

The counter is the one the first loop's escalation uses. An instance
cannot be in both loops' deleting branches at once.

**For `deleted`.** The by-hand local teardown stays (S4). It moves
inside the lock, and loses its redundant state write.

### D6 -- files missing is recorded once

The "instance files missing" branch changes state only when the instance
is not in `Instance.ERROR_STATES`, a set which includes `error`. When it
does, it moves to `<state>-error` and then sets
`inst.error = 'instance files missing'`, in the same order as the I/O
error branch, because the error setter rejects a message on a
non-error state. From an error state it does nothing, logging at debug
level.

### D7 -- a powered off domain is `off`; the reason is recorded, never branched on

The cleaner writes `power_state = 'off'` for every inactive domain,
whatever the shutoff reason. The reason is display only, like the pause
reason:
* `SHUTOFF_REASON_STRINGS` and `extract_shutoff_reason()` are added to
  `util/libvirt.py`, next to their pause equivalents.
* The reason goes in the "detected poweroff" event's `extra`, as
  `{'reason': ..., 'previous_power_state': ...}`.

An inactive domain never produces `crashed`, and the instance's state
does not change.

**The write order is load-bearing:** `power_state`, then
`agent_state = AGENT_INSTANCE_OFF`, then the event. The functional test
waits for the event and then reads `power_state` once (phase 0's D1).

**The first loop's active `crashed` branch is left as it is.** It is
unreachable with `on_crash=restart`, and `power_states.md` will say so.

This is the decision most likely to be argued with. The master plan
framed a crash as the case F3 was trying to catch, and mapping
`CRASHED` to `crashed` would match the documented meaning. It is not
done here, for three reasons:
* **It is recoverable.** A qemu killed by the OOM killer comes back with
  a power on. Documented `crashed` means state `error`, which is
  terminal and allows only deletion.
* **The reason may not persist.** libvirt may not keep an inactive
  domain's shutoff reason across a libvirtd restart. This is
  unverified, and step 6 records what it sees. Branching on a value
  that may read `unknown` after a restart would make the same guest
  `off` or `crashed` depending on when libvirtd last restarted.
* **Operators still see the difference.** The event records the reason,
  which is what the mission asks for: distinguishing an OOM kill from a
  guest `poweroff`.

### D8 -- the apparmor sweep lists every defined domain itself

`LibvirtConnection.get_all_domain_uuids()` returns
`{d.UUIDString() for d in self.conn.listAllDomains(0)}`. Flags `0` means
every defined domain, active or inactive, including foreign ones. The
sweep calls it directly rather than reading a list built by the second
loop. If it raises `libvirtError`, the sweep is skipped for this pass.
The comment's claim about foreign domains then becomes true (F1).

### D9 -- stray domains of instances in state `error` are left alone

When an errored instance is deleted, it ends in state `error` (S8). If
its undefine failed, its domain lingers. This phase records the domain
as `off` and does not delete it. `error` is not a deleted state:
state_targets allows deleting it again. Deciding when such a domain is
a stray needs the state machine work this plan declared out of scope.
It is recorded as future work.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | See brief 1. Commit: "Give node locks an optional timeout." |
| 2 | medium | sonnet | none | See brief 2. Commit: "libvirt: shutoff reasons and all domain uuids." |
| 3 | medium | sonnet | none | See brief 3. Commit: "Add a strict instance lookup for deletions." |
| 4 | high | opus | none | See brief 4. Commit: "cleaner: guard deletes and power state writes." |
| 5 | high | opus | none | See brief 5. Commit: "cleaner: detect powered off instances." |
| 6 | medium | sonnet | none | See brief 6. Commit: "Test a guest powering itself off." |
| 7 | low | sonnet | none | See brief 7. Commit: "Document detected power off." |
| 8 | medium | management session | none | Run `tox -epy3`, `tox -emypy` and `pre-commit run --all-files`. Dispatch `functional-tests.yml` on the branch (`gh workflow run functional-tests.yml --ref power-state-correctness-phase-01b-inactive-domains`). Confirm that the Guests job passes, that `test_lifecycle_guest_poweroff` passed rather than skipped, and what `test_lifecycle_qemu_killed` did. Then run the inventory in brief 8. Record the run URLs and the inventory counts in the pull request description. |

The steps run in order, in one worktree, never in parallel, because
pre-commit's stash is repository-wide. Steps 1 to 3 are additive and
pass on their own. Step 4 changes no behaviour a user can see, except
for skipping unsafe deletes. Step 5 is the switch. Everything it relies
on has landed and been tested by then, so a reviewer can read step 5 as
the behaviour change alone.

Every sub-agent report must list each guard it added, and for each one
the test that failed when that guard was removed. That is the check the
management session reviews: the plan-checks memory records that an
unmutated test is not evidence.

### Brief 1 -- a bounded node lock

In `shakenfist/util/concurrency.py`, give `NodeLock.__init__` a
`timeout: float | None = None`. In `__enter__`, raise
`exceptions.NodeLockTimeout` once `time.time() - start_time` exceeds a
non-`None` timeout, after a failed request. Keep the existing slow-wait
log.

Add `NodeLockTimeout(LockException)` to `shakenfist/exceptions.py`, with
a docstring saying it is raised only when a caller asked for a bounded
wait.

In `shakenfist/baseobject.py`, give `get_lock()` a keyword
`node_timeout: float | None = None`, passed to `NodeLock` and ignored
for cluster scope. Add a comment that `timeout` is for cluster locks and
is not applied to node locks, and why: restore passes 120 and would
start timing out. Do not change `get_lock_attr()`.

Unit tests in a new `shakenfist/tests/test_util_concurrency_nodelock.py`,
patching `_node_lock_request`:
* No timeout keeps retrying. Grant on the fifth request.
* A timeout raises `NodeLockTimeout` when never granted. Patch
  `time.time` and `time.sleep` rather than sleeping for real.
* A lock granted on the first request with a timeout does not raise.
* `get_lock(global_scope=False, node_timeout=...)` passes the timeout
  through, and `get_lock(global_scope=False, timeout=120)` does not.

Mutation check: removing the raise must fail the second test.

### Brief 2 -- shutoff reasons and every domain's uuid

In `shakenfist/util/libvirt.py`:
* Add `SHUTOFF_REASON_STRINGS` next to `PAUSED_REASON_STRINGS`, in the
  same `getattr` style. The keys and values are:
  * `VIR_DOMAIN_SHUTOFF_UNKNOWN` 'unknown'
  * `_SHUTDOWN` 'shutdown'
  * `_DESTROYED` 'destroyed'
  * `_CRASHED` 'crashed'
  * `_MIGRATED` 'migrated'
  * `_SAVED` 'saved'
  * `_FAILED` 'failed'
  * `_FROM_SNAPSHOT` 'from snapshot'
  * `_DAEMON` 'daemon'
* Add `extract_shutoff_reason(domain) -> str | None`, mirroring
  `extract_pause_reason()`: `None` unless the state is `SHUTOFF`, and
  `'unrecognised reason N'` for an unknown code. Its docstring says it
  is display only, and that phase 1b's D7 is why nothing branches on
  it.
* Add `get_all_domain_uuids() -> set[str]` next to the listing helpers:
  one `self.conn.listAllDomains(0)` call, with no name filter. Its
  docstring says it includes foreign domains on purpose, for the
  apparmor sweep, and contrasts it with the `sf` helpers.

In `shakenfist/tests/test_util_libvirt.py`, extend the existing fake
module with the shutoff reason constants, and test:
* each mapping;
* a non-shutoff domain giving `None`;
* an unknown code;
* `get_all_domain_uuids()` making exactly one call with flags `0` and
  keeping a non-`sf:` domain.

`tox -emypy` is strict on this file (phase 1a S8).

### Brief 3 -- a strict instance lookup

`exceptions.DatabaseUnavailable` already exists (`exceptions.py:198`),
and `_grpc_call()` already raises it for an exhausted outage. Do not
add another exception, and do not change `_grpc_call()`.

In `shakenfist/mariadb.py`:
* `get_instance(inst_uuid, *, strict=False)`. When `strict` is true,
  skip the cache read, since the cache only ever holds hits. Pass
  `strict` to `_grpc_get_instance()` and `_direct_get_instance()`.
* In each of those, when `strict` is true, re-raise the caught
  `grpc.RpcError` or `OperationalError` as
  `exceptions.DatabaseUnavailable(...) from e`.
* A genuine miss (`not reply.found`, or no row) still returns `None`.
* Leave the error log in place.
* Extend `get_instance()`'s docstring with what `strict` is for, and a
  pointer to #3373.

Unit tests go in `shakenfist/tests/test_database_unavailable.py`, which
already fakes `grpc.RpcError` for #3373. For the gRPC path:
* strict and a non-retryable error such as `StatusCode.UNKNOWN` raises
  `DatabaseUnavailable`;
* strict and a miss returns `None`;
* non-strict and the same error still returns `None`, which is today's
  behaviour and is kept for every other caller;
* an exhausted retryable error raises `DatabaseUnavailable` either way,
  as it does today.

Add the matching strict and non-strict pair for the direct path with
`OperationalError`.

Mutation check: dropping the strict re-raise must fail a test.

### Brief 4 -- the guards, before anything is switched on

All in `shakenfist/daemons/cleaner/scheduled_tasks.py`. The second loop
still reads active domains after this step, so the only behaviour change
is deletes and writes that no longer happen.

1. **`_sf_instance_uuid(domain)`, D4.** Use it at the top of both loops
   in place of `split(':')[1]`, skipping the domain on `None`.
2. **`_instance_confirmed_absent(instance_uuid)`, D3.** It returns True
   only when `mariadb.get_instance(UUID(instance_uuid), strict=True)`
   returns `None`. On `DatabaseUnavailable` it logs a warning citing
   #3373 and returns False. Do not catch `DatabaseUnavailable` from
   `Instance.from_db()` itself: that should keep aborting the pass. If it returns a row, it returns False. Both
   loops' `not inst` branches call it, and skip the domain unless it
   returns True.
3. **`_locked(inst)`, D2.** A small context manager, or an inline
   try/except, wrapping `inst.get_lock(op='Cleaner power state
   update', global_scope=False, node_timeout=2)`. It turns
   `NodeLockTimeout` and `MissingNodeLockSocket` into a skip.

   In the first loop, the reading goes before `update_power_state()`:
   * Compare `lc.extract_power_state(domain)` with
     `inst.power_state.get('power_state')`.
   * **They agree:** take no lock, and fall through to the `crashed` and
     `paused` branches as today. Those write only on a change of state,
     and have their own guards.
   * **They differ:** take the lock. Inside it, re-look-up the domain
     with `lc.get_domain_from_sf_uuid()`. If it is gone or inactive,
     skip, because the second loop's next pass owns it. Otherwise call
     `update_power_state()` with the re-extracted state.

   Also run the `crashed` and I/O error branches' state writes under the
   lock, after the re-look-up. The `deleted` escalation is left alone.
   It acts on instances whose global delete already ran, and the
   `virsh`/SIGKILL path is its own lock-free last resort.
4. **The apparmor sweep, D8.** Build `all_libvirt_uuids` from
   `lc.get_all_domain_uuids()`, called just before the sweep. On
   `libvirtError`, log and skip the sweep. Delete the second loop's
   `all_libvirt_uuids` accumulation.

Unit tests in `shakenfist/tests/test_daemon_cleaner.py`.

First make the fakes honest:
* `FakeLibvirtDomain.UUIDString()` returns the name's suffix for `sf:`
  domains, and a fixed foreign uuid for `apache2`.
* Add `isActive()`.
* Make `lookupByName()` return the domain `listAllDomains()` would,
  rather than a running one.
* Add a `get_lock` fake which records calls and can be told to raise
  `NodeLockTimeout`, patched at `shakenfist.instance.Instance.get_lock`
  or at `util_concurrency.NodeLock`, whichever the existing test
  setup makes simpler.

Then add tests:
* A mismatched uuid domain causes no delete and no lookup.
* A domain named `sf:` causes no rmtree.
* An unknown active domain with `DatabaseUnavailable` on the strict
  lookup is not destroyed. Make the strict lookup raise for this, so
  that `from_db()` returns `None` the way a non-retryable error makes
  it.
* An unknown active domain with a strict miss is destroyed, which is
  today's behaviour.
* F13: a lock timeout leaves `power_state` unwritten.
* F13: a domain which reads `paused` in the pass, but `off` inside the
  lock, is not written as `paused`.
* The sweep keeps a foreign domain's profile.
* The sweep deletes nothing when listing raises.
* Agreeing states take no lock, since that is the database load claim
  in S14.

Mutate each guard in turn and report which test failed.

### Brief 5 -- the switch

In the second loop of `update_power_states()`:
* Iterate `lc.get_inactive_sf_domains()`, and delete the "deliberately
  still iterates active domains" comment.
* Keep the `seen` check. It covers a domain that went inactive between
  the loops.

For each domain, after brief 4's uuid and absence handling:

1. **Unknown domain** (confirmed absent). This is today's rmtree and
   undefine, unchanged.
2. **`initial`, `preflight` or `creating`.** Skip, logging at debug
   level (S2).
3. **`deleted`, older than five minutes.** Under the lock, and after
   re-reading the state, do the local teardown, with no state write
   (S4, D5).
4. **`delete-wait`, older than five minutes.** Under the lock, after
   re-reading the state, apply D5's enqueue schedule. Do not call
   `inst.delete()`.
5. **Everything else.** `place_instance(config.NODE_UUID,
   enforce=False)` as today, then:
   * **Files missing:** apply D6.
   * **Otherwise, if `power_state` is not `off`:** take the lock and
     re-look-up the domain. If it is now active or gone, skip. Then, in
     this order:
     1. `update_power_state('off')`;
     2. `inst.agent_state = constants.AGENT_INSTANCE_OFF`;
     3. `inst.add_event(EVENT_TYPE_AUDIT, 'detected poweroff',
        extra={'reason': lc.extract_shutoff_reason(domain),
        'previous_power_state': ...})`.

     The order is load-bearing (D7).

Remove `@unittest.expectedFailure` from
`test_update_power_states_detects_shutoff`, and its docstring's "phase
1b" text. It must now pass in all three test classes that run it (phase
1a S6).

New tests, each with a mutation check:
* The shutoff instance gets `agent_state` `AGENT_INSTANCE_OFF`.
* The event carries the reason.
* The writes happen in order. Record the calls on a mock.
* An instance already `off` gets no lock, no write and no event.
* `creating` is skipped.
* A lock timeout is skipped.
* A domain active again inside the lock is skipped.
* `delete-wait` calls `enqueue_delete()` on counts 1, 6 and 11 only,
  adds the abandon event at 16, and never calls `delete()`.
* `deleted` does the teardown without a state write.
* Files missing from `created` goes to `created-error` with the error
  message.
* Files missing from `error` and from `created-error` does nothing and
  raises nothing.
* An unknown inactive domain behind `DatabaseUnavailable` is not
  removed.

Run `stestr run shakenfist.tests.test_daemon_cleaner` from `.tox/py3`
and report that no expected failures remain.

### Brief 6 -- functional tests

In `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_state_changes.py`,
add `test_lifecycle_guest_poweroff` to `TestStateChanges`, modelled on
`test_lifecycle_power_cycle`:
1. `inst = self._start_target('guestpoweroff')`, then record `after =
   time.time()`.
2. `self._await_command(inst['uuid'], 'systemd-run --on-active=3
   systemctl poweroff')`. This returns at once, because `systemd-run`
   only schedules the power off. A bare `poweroff` would never reply.
3. `self._await_power_off(inst['uuid'], after=after)`, then
   `self._assert_power_state(inst['uuid'], 'off', 'after guest power
   off')`. A single read is valid because the cleaner writes the state
   before the event.
4. Assert that `agent_state` is `'not ready (instance powered off)'`, and
   that the event's `extra['reason']` is `'shutdown'`.
5. Power on through the API, `_await_instance_ready()`, and assert
   `on`. Then assert that `agent_system_boot_time` changed, as the power
   cycle test does.

Add `test_lifecycle_qemu_killed`:
1. Resolve the instance's node to the node dict `_node_exec()` wants.
   The only existing user is `cluster_ci_tests/test_stray_vxlan.py`,
   which calls `self._network_node()`. Find or write the equivalent for
   an instance's node. Node exec is unproven in the Guests suite, which
   is why the skip below matters.
2. Call `_require_node_exec(node)`.
3. `pkill -9 -f 'guest=sf:<uuid>'` with sudo.
4. Await "detected poweroff", then assert `off`, the instance state still
   `created`, and the reason. Assert `'crashed'`. If it reads otherwise,
   do not weaken the assertion. Stop and report what libvirt said,
   because D7 and power_states.md depend on it.

In `base.py`, delete the F13 hint from `_assert_power_state()`
(`base.py:815-817`), since step 4 fixed F13.

### Brief 7 -- documentation

`docs/operator_guide/power_states.md`:
* Say that the cleaner detects a guest which powers itself off, or
  whose qemu dies, within one or two passes of about a minute each.
  Say that it records `off`, sets the agent state, and adds a "detected
  poweroff" event whose `reason` is libvirt's.
* List the reason strings and what each means.
* Rewrite the `crashed` bullet: it is written only for a domain libvirt
  reports as crashed while still active, which Shaken Fist's domain
  configuration prevents (`on_crash` is `restart`). A qemu which dies
  is `off` with reason `crashed`, and a power on recovers it.

`docs/developer_guide/subsystem_internals.md` (around line 425): one
sentence that `update_power_states` takes the instance's node lock with
a two second bound before writing, and skips the instance if busy.

No AGENTS.md or ARCHITECTURE.md change: no convention or component
changes.

### Brief 8 -- inventory before merge

The switch makes live the deletion branches that have been dead since
2022, on every hypervisor at once. Before merging, the management
session asks Mikal to run, on each sfcbr hypervisor, the only
long-lived cluster:

```
virsh list --inactive --name | grep '^sf:' | while read d; do
  u=${d#sf:}; printf '%s %s\n' "$u" \
    "$(sf-client --json instance show "$u" 2>/dev/null |
       jq -r '"\(.state) \(.power_state)"' || echo MISSING)"
done
```

The session then classifies each line into brief 5's branches, and
counts each. Any line in the unknown branch (the lookup found no
instance) is a gate: every such instance is looked at before merge. So
is any line in `delete-wait`, `deleted`, or files missing. The counts go
in the pull request description.

## Risks and mitigations

* **The first deploy acts on every inactive domain at once.** Four
  years of powered off guests, strays and half-deleted instances become
  visible together. Mitigation: brief 8's inventory before merge. D3's
  confirmation and D4's validation make the deleting branches fail
  safe. D5 enqueues deletes through the normal path at most three times.
* **A database error deletes disks (S5).** An exhausted outage is
  already fail-safe, because it aborts the pass. A non-retryable error
  is not, and D3 closes that for the cleaner, tested with the strict
  lookup raising. The general fix stays with #3373, which gets a
  comment linking this phase.
* **The cleaner holds an instance lock when systemd's watchdog kills
  it.** A node lock held by a dead process stays held until sf-nodelock
  restarts. Any holder has this problem. Mitigation: the cleaner holds
  the lock only for a re-look-up and at most four writes, it pets the
  watchdog before taking it, and `place_instance()` stays outside it.
* **The unit fakes lie again.** Phase 1a's lesson. Mitigation: brief 4
  makes the fakes' uuid, `isActive()` and lookup honest, and every guard
  carries a mutation check the management session reads.
* **The `CRASHED` claim is wrong.** Mitigation: the reason is display
  only (D7), and `test_lifecycle_qemu_killed` checks it on a real
  hypervisor. A different answer changes one doc line and one
  assertion, and nothing the cleaner does.
* **#3373 lands first and changes `get_instance()`.** Mitigation: resolve
  the conflict by keeping whichever raises. D3's keyword exists to be
  deleted by #3373.
* **Database load.** S14 estimates none that the budget does not already
  charge. Mitigation: brief 4's no-lock-in-steady-state test, and the
  load budget check in CI.
* **Worktree pre-commit stash races.** Steps run one after another and
  never in parallel.

## Definition of done

* [ ] `grep -n expectedFailure shakenfist/tests/test_daemon_cleaner.py`
      prints nothing, and `stestr run shakenfist.tests.test_daemon_cleaner`
      reports no failures and no expected failures.
* [ ] In `shakenfist/daemons/cleaner/scheduled_tasks.py`,
      `grep -n "get_active_sf_domains\|get_inactive_sf_domains\|get_all_domain_uuids"`
      prints exactly three calls, one of each. The first loop calls the
      active helper, the second loop the inactive one, and the sweep the
      all-uuids one.
* [ ] `grep -n "inst.delete()" shakenfist/daemons/cleaner/scheduled_tasks.py`
      prints only the first loop's `deleted` escalation.
* [ ] `grep -n "name().split(':')\|domain_name.split(':')" shakenfist/daemons/cleaner/scheduled_tasks.py`
      prints nothing: every uuid in `update_power_states()` comes from
      `_sf_instance_uuid()`. It prints two lines on `develop` today.
      `clear_old_libvirt_logs()` parses log file names, not domains, and
      is not matched.
* [ ] The step 4 and step 5 reports name, for each guard, the test that
      failed when the guard was removed. The guards are:
      * uuid mismatch;
      * the empty name;
      * the strict lookup;
      * the lock timeout;
      * the re-look-up inside the lock;
      * the error-state re-entry guard;
      * the sweep skipping on a listing failure;
      * the sweep keeping foreign domains;
      * the write order;
      * the `delete-wait` enqueue schedule.
* [ ] `tox -epy3`, `tox -emypy` and `pre-commit run --all-files` pass.
* [ ] A `workflow_dispatch` run of `functional-tests.yml` on the branch
      passes its Guests job, in which `test_lifecycle_guest_poweroff`
      *passed*. The run's output says whether `test_lifecycle_qemu_killed`
      passed or skipped. If it failed on the reason, D7 and
      power_states.md are revisited before merge.
* [ ] `grep -n "F13" shakenfist/deploy/shakenfist_ci/base.py` prints
      nothing.
* [ ] `docs/operator_guide/power_states.md` no longer says a `crashed`
      instance is always in state `error` without saying when `crashed`
      occurs. It names the "detected poweroff" event and its `reason`.
* [ ] Brief 8's inventory counts are in the pull request description,
      and Mikal has looked at every line in a deleting branch.

## Back brief

Before starting step 1, back brief Mikal on the three places this plan
departs from, or adds to, the master plan's phase 1b section. Each is
cheap to change now, and expensive after steps 4 and 5 have built tests
around it:
* **D3.** A strict lookup before any delete of an unknown domain.
  #3373's first half made a full outage abort the pass, but a single
  non-retryable error still reads as "not found", and the first loop
  destroys a running instance on that today.
* **D5.** Re-enqueueing `delete-wait` deletes rather than calling
  `inst.delete()`, which would leak interfaces.
* **D7.** `off` plus a recorded reason, rather than `crashed`.

Before merging, the brief 8 inventory is the second gate.

## Future work

* [#3373](https://github.com/shakenfist/shakenfist/issues/3373): make
  non-retryable errors raise for every lookup, then delete D3's
  `strict` keyword.
* Stray domains of instances whose delete ended in state `error` (D9).
* Whether libvirt keeps an inactive domain's shutoff reason across a
  libvirtd restart. Record it if step 6 or later testing shows either
  way.
* `NodeLock` has no recovery when its holder dies. That is not specific
  to this phase.
