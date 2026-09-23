# Truthful instance power state

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
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

Issue [#4280](https://github.com/shakenfist/shakenfist/issues/4280)
reported that instances on a Debian 13 hypervisor never reached agent
ready. The trigger was packaging: trixie ships qemu's SPICE support in
`qemu-system-modules-spice`, which we only installed on Ubuntu 24.04, so
libvirt refused every domain definition. That took about 30 minutes to
surface in CI rather than seconds, and the reason was in the power state
code: `Instance.is_powered_on()` returned the truthy string `'off'` when
libvirt had no domain, so `create()` marked instances which had never
started as `created`, and the functional suite waited out the agent
timeout. Both are fixed on the `issue-4280-trixie-spice` branch
(`a100fcd26`), which had not merged when this plan was written and is a
prerequisite for it.

Mikal's reaction was that the power state detection code as a whole is
suspect, and that the functional tests covering it should be audited at
the same time. A read-only audit of the code and tests, and then a
second high-effort review of this plan's first draft (both 2026-09-23),
support that suspicion. Every finding below was checked against
`develop` at `2ef548d6a`:

| # | Where | Defect | Consequence |
|---|---|---|---|
| F1 | `util/libvirt.py` `get_all_domains()`; `daemons/cleaner/scheduled_tasks.py` second loop (~line 218) | `get_all_domains()` iterates `listDomainsID()`, which lists only *active* domains. It used `listDefinedDomains()` until `3b013bd3e` ("Correct counting of running instances", July 2022), and that commit also deleted the cleaner test's `listDefinedDomains` fake and its expectations that inactive domains become `off` -- the regression was visible in its diff. The cleaner's second loop acts only on domains absent from `seen`, which the first loop fills from the same active-only iterator. | The loop's power off and files-missing branches are dead: a guest which powers itself off (`on_poweroff` is `destroy`) is reported `on` forever. The `not inst` branch still runs for an unknown active SF domain whose destroy in the first loop failed, and a libvirtError partway through the first loop leaves `seen` partial, so the second loop can today mark *running* instances `off`. `get_all_domains()` also drops non-`sf:` domains, so the apparmor sweep's `all_libvirt_uuids` never holds foreign domains despite the comment claiming it does. |
| F2 | `daemons/queues/startup_tasks.py` ~line 156 | `inst.power_state` is a dict (`{'power_state': ...}`) but is tested with `not in [...]`, which is always true, so every instance is skipped. The call it would then make, `inst.create_on_hypervisor()`, no longer exists on `Instance` (only `Network` has it). | Instance restore on sf-queues start is a silent no-op. The two defects cancel: fixing only the comparison raises `AttributeError` for every instance and error-deletes it. Of the four states it matches, `initial` is real (written at `instance.py:654`), but `transition-to-on` and `unknown` are never written. |
| F3 | `operations/node_inst_op.py` `_health_check_kvm_process` | Compares the `power_state` dict to `'on'`; never called. | Dead. Wired up correctly it would error-delete every guest which powered itself off, because `kvm_pid` goes stale, so delete it rather than fix it. |
| F4 | `instance.py` `power_on()`; `external_api/instance.py` `InstancePowerOnEndpoint` | `power_on()` ignores the result of its last `_power_on_inner()` and returns `None`, which the endpoint returns. | An API power on which failed five times answers 200 with a `null` body. |
| F5 | `instance.py` `power_off()` | Logs any `destroy()` error other than "not running", then writes `power_state='off'` and returns. | The API reports a still-running instance as off. |
| F6 | `instance.py` `pause()`/`unpause()` | Treat "`update_power_state()` did not change the stored value" as "the operation failed". libvirt's qemu driver returns success for suspending a paused domain or resuming a running one. | Pausing twice, or losing a race with the cleaner writing `paused` first, raises a 409 for an operation which succeeded. |
| F7 | `instance.py` `pause()`/`unpause()` | Guard on "no domain" only; our domains are persistent, so a powered off instance has an *inactive* domain and `suspend()` raises a raw libvirtError. | 500 instead of the documented 409 -- the defect class #3630 fixed for `reboot()` alone. |
| F8 | `instance.py` `_power_on_inner()` | "domain is already running" returns `True` without updating `power_state`; the generic start-error path reallocates ports without undefining the domain, so the retry reuses stale XML; `agent_state = AGENT_NEVER_TALKED` is set only when the *first* attempt failed. | Powering on a *paused* instance answers success and leaves it paused. The retry loop's bookkeeping cannot be trusted. |
| F9 | `util/libvirt.py` `extract_power_state()` | `SHUTDOWN` and `NOSTATE` map to `on`, `PMSUSPENDED` to `paused`; `extract_power_state_pretty()` raises `KeyError` on an unknown enum. | Low: PM suspend is disabled in `libvirt.tmpl`, `SHUTDOWN` is transient. Recorded so the audit phase can confirm it was considered. |
| F10 | `instance.py` `_power_on_inner()`, `power_off()` | `setAutostart(1)` is called on every power on and nothing ever clears it; `power_off()` only calls `destroy()`. | After a hypervisor reboot libvirtd starts every instance ever powered on, including those the user powered off, and the database says `off` until the cleaner rewrites it. It also means sf-queues restore (F2) has no job: a restart of sf-queues or libvirtd leaves domains running, and autostart covers a reboot. Nothing in the deployer configures `libvirt-guests`, so the distribution default applies. |
| F11 | `daemons/cleaner/scheduled_tasks.py` ~lines 246-266 | The second loop's `delete-wait` branch rmtree's files, undefines the domain and sets `state = deleted` by hand, bypassing `_delete_globally()`. `_instance_delete` then returns early because the instance is already deleted (`node_inst_op.py:193`). | Dead today (F1). Once F1 is fixed, a powered off instance left in `delete-wait` for five minutes by a queue backlog leaks its ports, placement, references and agent operations. |
| F12 | the power endpoints | `requires_instance_active` answers 406 for any state other than `created`, but none of the four power endpoints declares 406 in its `swag_from`. | The published API is incomplete. |

The test suites did not catch any of this, for specific reasons:

- No functional test reads the `power_state` field. The lifecycle tests
  in `guest_ci_tests/test_state_changes.py` assert agent readiness,
  `agent_system_boot_time` and ping, which are good evidence that a guest
  is running but say nothing about what the API reports.
- Instance creation waits for `created`, and the suite treats that as
  meaning the domain started (`test_nodes.py:274-290` says so, and adds
  only that metrics lag behind it). Before the #4280 fix that was false,
  which is why #4280 was a timeout rather than a failure.
- `base.py:747` defines `_await_power_off()`, which waits for the
  "detected poweroff" event from dead loop F1. Nothing calls it: a guest
  shutdown test was evidently intended and never written, and would have
  found F1.
- Unit-test fakes model impossible production behaviour: the cleaner
  test's fake `listDomainsID()` returns `SHUTOFF` and `CRASHED` domains,
  and `test_queues_startup_restore.py`'s `FakeInstance` stores
  `power_state` as a string and defines `create_on_hypervisor()`. Each
  fake hides the bug in the code it tests.

## Mission and problem statement

Make an instance's reported `power_state` true, and make every power
operation's API answer true, then prove both in the functional suite so
that a regression fails a named test instead of timing one out.

Concretely, when this plan is complete:

- A guest which powers itself off is reported `off`, with its
  `agent_state` and the reason libvirt gives for the shutoff, within one
  or two cleaner passes, and a functional test does exactly that.
- An instance the user powered off stays off across a hypervisor reboot.
- Power on, power off, pause and unpause either do what was asked and
  report the resulting state, or return an error. None answers success
  for an operation that failed, and none returns a 500 for a request
  that is merely invalid in the current state.
- sf-queues no longer carries an instance restore which does nothing and
  which a partial fix would turn into a mass deletion (F2).
- Every functional lifecycle test asserts `power_state` as well as the
  guest-side evidence it already checks, and there is a functional test
  of a power on which fails.
- The unit-test fakes for libvirt behave like libvirt: `listDomainsID()`
  returns only active domains, and `power_state` is a dict.

Out of scope: the SPICE packaging and `is_powered_on()` fixes (on the
unmerged `issue-4280-trixie-spice` branch, a prerequisite), changes to
the instance state machine itself, and the agent side of agent
readiness.

## Open questions

The first draft asked five questions. The plan review answered four of
them from the code; the answers are recorded here so that the phases
can rely on them.

1. **Restore or delete (F2)?** Answered, subject to one check: delete
   the instance half of restore and keep the network half, which works.
   Autostart (F10) already restarts domains on a hypervisor reboot, and
   a restart of sf-queues or libvirtd leaves running domains running, so
   restore has had no job since `Instance` lost `create_on_hypervisor()`.
   Still to establish in phase 2, on a real hypervisor: the
   `libvirt-guests` default on Debian 13 and Ubuntu 24.04, since that
   decides whether guests are shut down or suspended across a reboot.
2. **What does a failed power on return?** Answered: 507 for port or
   memory exhaustion, 500 for anything else. client-python maps 409, 500
   and 507 to distinct exceptions and does not retry in `_request_url`,
   its CLI ignores the response body, and the ansible collection's
   `sf_instance` module has no power operations, so no client breaks.
3. **Is `power_state` the right thing to assert?** It is a cached value
   written by several actors (the power methods and the cleaner), and
   asserting it tests the cache, which is what users read. This plan
   assumes that is the right target. Open: phase 3 should say so
   explicitly if it finds otherwise.
4. **Rename `get_all_domains()`?** Answered: yes. Replace it with
   `listAllDomains()` using `VIR_CONNECT_LIST_DOMAINS_ACTIVE` and
   `_INACTIVE`, which also removes the N+1 lookups and the list-then-look-up
   race. The sidechannel (`sidechannel/main.py:2011`) wants active
   domains and is safe under the rename. The resources daemon
   (`resources/main.py:497-511`) counts only active domains, so its
   `instances_total` equals `instances_active` today, and
   `database_tier.py:602` and `ctl.py:607` depend on the active count.
   Keep those semantics and make any redefinition of `instances_total` an
   explicit decision rather than a side effect.
5. **Cleaner interval.** Answered: `schedule.every(1).minutes` is pumped
   once per main-loop pass, and each pass ends in `idle(60)` after blob
   maintenance (`cleaner/main.py:228-283`). Detection therefore takes
   roughly 60-120 seconds plus the pass duration, and is deferred
   entirely while `cluster_stable()` is false. `_await_instance_event`
   allows five minutes (`base.py:1125`), which fits.

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

No phase starts until `issue-4280-trixie-spice` has merged: phases 0, 1b
and 3 all rely on `create()` failing when power on fails.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 0. Assert power state in the existing lifecycle tests | PLAN-power-state-correctness-phase-00-assertions.md | Not started | — |
| 1a. Honest libvirt domain listing | PLAN-power-state-correctness-phase-01a-listing.md | Not started | — |
| 1b. The cleaner sees powered off domains | PLAN-power-state-correctness-phase-01b-inactive-domains.md | Not started | — |
| 2. Autostart and instance restore | PLAN-power-state-correctness-phase-02-autostart-restore.md | Not started | — |
| 3. Power operations answer truthfully | PLAN-power-state-correctness-phase-03-power-api.md | Not started | — |
| 4. Push audit | PLAN-power-state-correctness-phase-04-push-audit.md | Not started | — |

Each phase that changes behaviour lands its functional tests with it,
rather than leaving them to a trailing test phase.

### Phase 0: assert power state in the existing lifecycle tests

Add `power_state` assertions to every lifecycle test in
`guest_ci_tests/test_state_changes.py` and to the power cycle in
`test_interface_plug_and_exec_reboot`: `on` after create, `off` after
power off, `paused` then `on` around pause, `on` after each reboot. These
pass on `develop` today, because the power methods write those values.
The point is to have the net in place before phase 1b adds a second
writer that can race them. Plan at medium effort; the judgement is in
choosing waits which do not flake.

### Phase 1a: honest libvirt domain listing

No behaviour change. Replace `get_all_domains()` and `get_sf_domains()`
with helpers whose names say whether they return active or inactive
domains, built on `listAllDomains()` (open question 4), and move every
caller across while keeping its current active-only semantics. Rewrite
`test_daemon_cleaner.py`'s fake so `listDomainsID()`, or its
replacement, returns only active domains; the existing tests which
expect inactive domains to be seen will then fail, which is correct, and
are marked as expected failures for phase 1b to turn on. Plan at medium
effort.

### Phase 1b: the cleaner sees powered off domains

Point the cleaner's second loop and its apparmor sweep at the inactive
listing. This makes live code that deletes things, so this phase is
mostly the guards that the dead code never needed:

- **Race with power on.** An instance in `creating`, or mid power on,
  has a defined inactive domain between `define_xml()` and
  `domain.create()`, and `_power_on_inner()` undefines and redefines on
  each of up to five retries. Without a guard the cleaner can write
  `off` over a fresh `on`, which phase 0's assertions would catch as a
  flake. Skip instances in `initial`, `preflight` or `creating`; take
  the instance lock (`get_lock(global_scope=False)`, which the power
  endpoints hold) with a short timeout and skip if busy; and re-check
  `domain.isActive()` immediately before writing.
- **Delete through the delete path (F11).** Replace the `delete-wait`
  branch's by-hand rmtree, undefine and `state = deleted` with
  `inst.delete()`, as the first loop does.
- **Re-entry guard on "instance files missing".** `<state>-error` to
  `<state>-error-error` is invalid, and the exception it raises is not a
  libvirtError, so it would escape the `try`, abort the rest of the pass
  including the apparmor sweep, and recur every minute under
  `_resilient_job` (`cleaner/main.py:25-41`). Guard it as the I/O-error
  branch already is.
- **Validate the uuid before deleting.** The `not inst` branch rmtree's
  `STORAGE_PATH/instances/<name suffix>`; a domain named `sf:` would
  make that `instances/`. Cheap to prevent.
- **Record what happened.** Set `agent_state` to `AGENT_INSTANCE_OFF` on
  detected power off (today only `power_off()` does), and put libvirt's
  shutoff reason in the event's `extra`, so that a qemu killed by the
  OOM killer is distinguishable from a guest `poweroff`. Decide whether
  a `CRASHED` shutoff reason maps to `crashed` -- the case F3 was trying
  to catch.

Note the new database load: every pass now runs `place_instance()` for
every powered off instance, and the phase plan should estimate it
against the load model in `PLAN-database-load-reduction.md`. The
functional test is the one `_await_power_off()` was written for: a
guest powers itself off, and the suite asserts `power_state == 'off'`,
the agent state, and the event; then powers it on and asserts `on` and
agent ready. `await_agent_command(uuid, 'poweroff')` would hang because
the guest dies before replying, so issue a detached delayed power off
(`systemd-run --on-active=3 systemctl poweroff`). Plan at high effort:
the cleaner deletes things, and a wrong inactive-domain list is a
deletion bug.

### Phase 2: autostart and instance restore

Fix F10 by calling `setAutostart(0)` in `power_off()`, after first
establishing the `libvirt-guests` default on Debian 13 and Ubuntu 24.04
(open question 1). Delete the instance half of sf-queues restore (F2) in
the same change, and with it
`test_failed_restore_enqueues_delete_and_continues` and `FakeInstance`'s
string `power_state`. Delete F3's `_health_check_kvm_process`. Plan at
high effort: this is behaviour on every node at startup and reboot.

### Phase 3: power operations answer truthfully

F4 to F8 and F12, with their functional tests. `power_on()` reports
failure and the endpoint answers 507 or 500 (open question 2);
`power_off()` does not record `off` when `destroy()` failed and stays
consistent with phase 2's autostart change; pause and unpause judge
success by the domain's state rather than by whether the stored value
changed, and raise `InvalidLifecycleState` for an inactive domain the
way `reboot()` does after #3630; `_power_on_inner()` updates
`power_state` on "already running", resumes or refuses a paused domain
rather than answering success, and undefines stale XML on the generic
retry path. Declare 406 on the four power endpoints. Resolve or
explicitly exclude [#2241](https://github.com/shakenfist/shakenfist/issues/2241),
which is about unpause recovering from a crashed guest and overlaps F6
and F7.

Unit tests go alongside `InstanceRebootTestCase` and
`InstancePowerStateTestCase` in `test_instance.py`, each checked to fail
against the code it fixes. Functional tests: pausing a powered off
instance (409, not 500), pausing twice, and a failed power on. The last
uses `_node_exec` from `base.py`, which needs `sudo=True`, a node with an
`ip`, and SSH access, and which no guest CI test uses yet: move a
powered off instance's root disk aside, power on, assert the error and
`power_state != 'on'`, and restore the disk before cleanup. It must skip
visibly through `_require_node_exec` where node exec is unavailable.
Any status code change is an API contract change, so update the
`swag_from` declarations and `docs/`. Plan at high effort.

### Phase 4: push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 0 to 3, as
recorded in the `Merged` column. Re-read F9 and record whether it was
deliberately left alone.

<!-- shared-block: plan-push-audit-phase v3 -->
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
  untriggered for as long as it did.
<!-- shared-block-end -->

!!! note "In this project"

    The runbook is `PUSH-AUDIT.md` at the repository root, and
    it reads its `$RANGE` from the `Merged` column, so a plan
    without one cannot be audited once its phases have merged.

    The column goes in the master plan's Execution table, last:

        | Phase | Plan | Status | Merged |

    The worked example in the `plan-file-conventions` block
    above still shows the three-column form, because it
    predates this column and its canonical copy lives in
    shakenfist/development; add `Merged` after `Status` rather
    than copying that example as-is.

    `tools/check-plan-status.py` reads the `Status` column by
    name rather than by position, so a `Merged` column added
    after it does not disturb the index arithmetic. A phase
    which has not landed reads `—` -- in a table cell or a
    `**Merged**:` line alike -- rather than an empty cell,
    `TBD` or `n/a`.

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

- F9, the mapping of libvirt's rarer states, unless phase 4 decides
  otherwise.
- A CI check that no unit-test fake returns inactive domains from an
  active-domain listing, if phase 1a's rewrite shows the mistake is easy
  to reintroduce.
- An API-only way to make instance creation fail on demand, so the
  failed-create path can be tested functionally without node exec. A
  garbage UEFI `nvram_template` might make qemu refuse the domain; that
  is unverified and needs a spike.
- Whether `instances_total` in the resources daemon should count
  defined-but-inactive domains (open question 4). This plan keeps
  today's semantics.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- [#4280](https://github.com/shakenfist/shakenfist/issues/4280): the
  SPICE packaging defect and `is_powered_on()` returning a truthy
  string, fixed on the `issue-4280-trixie-spice` branch before this plan
  was written. It is what prompted the plan.

Related issues:

- [#4307](https://github.com/shakenfist/shakenfist/issues/4307) tracks
  this plan and findings F1 to F12.
- [#2241](https://github.com/shakenfist/shakenfist/issues/2241) (unpause
  should recover from a crashed guest or a qemu monitor EOF, not just
  retry) overlaps F6 and F7; phase 3 resolves or excludes it.
- [#3630](https://github.com/shakenfist/shakenfist/issues/3630) fixed
  F7's defect class for `reboot()` only, and is the pattern phase 3
  follows.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
