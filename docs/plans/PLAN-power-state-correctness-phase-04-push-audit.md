# Phase 4 -- Push audit

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
Phase 3 landed as [#4468](https://github.com/shakenfist/shakenfist/pull/4468)
(`d3d5f15ca`), which completes the plan's behaviour changes. Every power
operation now writes `power_state` from the domain's observed state, the
cleaner sees powered off domains, autostart means "should be running",
and the functional suite asserts `power_state` throughout.

**Planning effort:** medium. The audit follows an established pattern
(`PLAN-ci-cloud-sizing-phase-07-push-audit.md` and its precedents).
**Review effort:** high for steps 4c, 4f and 4g. The plan changed
code that decides whether to delete a domain or an instance, and that
code is already deployed.

## Why this phase exists

The plan ran for two weeks across five merged pull requests. Each was
reviewed on its own and each went green, but nobody has read the result
as one body of work. This plan needs that reading for a reason specific
to what it changed.

`power_state` and `agent_state` are cached values with several writers:

* the six power operations;
* the cleaner's two loops, active and inactive;
* `create()` and `_instance_delete()`;
* the sidechannel monitor;
* the kvm health check.

Each phase made one or two writers truthful in isolation. Phase 3's D9
showed what isolation misses. `unpause()` was correct, the sidechannel
monitor was correct, and together they stranded `agent_state` at "no
contact" for a short pause. Nothing in a single phase's diff showed the
defect; only a functional run did. The phases also each argued from a
mapping (`extract_power_state()`, F9) that the master plan deliberately
left alone and asked this phase to revisit.

## Scope

**In scope.**

* The code, tests and documentation in the five phase ranges in D1,
  audited under `PUSH-AUDIT.md`'s wave 1 and the four wave 2 lenses.
* The two plan-document merges, under the documentation lens only (D2).
* A plan-specific lens on the writers of `power_state` and
  `agent_state`, which carries the F9 re-read (D3).
* A read-only look at what the deployed cleaner has done on sfcbr (D4).
* Consolidating the phases' deferred work into the master plan's Future
  work (D5).

**Out of scope.**

* Re-litigating each pull request's review. Each phase plan records what
  its review found and how it was disposed of. Read those first.
* #4309, the SPICE packaging and `is_powered_on()` fix that prompted the
  plan. It merged before phase 0 and is not one of the plan's ranges.
* Fixing advisory findings. Like the precedents, this phase fixes what
  is blocking or trivial and files the rest (D6).
* The items phase plans already deferred in writing, unless the audit
  finds one is worse than its deferral says.

## What the survey found

The master plan's phase 4 section is two sentences: run `PUSH-AUDIT.md`
over the `Merged` column's ranges, and re-read F9. The `Merged` column
is complete and every cell names a merge commit, so the plan is
auditable as it stands. Five things the section does not say change how
the audit should run, and the first two are corrected at source in this
phase's planning commit.

### S1 -- two plan-document merges are not in the `Merged` column

`6c5807018` ([#4310](https://github.com/shakenfist/shakenfist/pull/4310),
the master plan, 783 insertions) and `304c6ca51`
([#4405](https://github.com/shakenfist/shakenfist/pull/4405), the phase
2 plan and phase 1b's close-out, 683 insertions) landed only plan
documents. They are not phases, so they stay out of the Execution table,
but a plan document is where a false claim enters the tree. This plan's
own record shows that happening: phases 1b, 2 and 3 each corrected
several master plan claims at source. D2 routes both merges to the
documentation lens.

### S2 -- phase 3 made F9's mapping load bearing

F9 was recorded as low risk because PM suspend is disabled in
`libvirt.tmpl` (lines 41-43, confirmed) and `SHUTDOWN` is transient. When
F9 was written, `extract_power_state()` only fed the stored value. After
phase 3 it is also the success oracle for pause and unpause:

* `instance.py:2591` and `:2643` are the "already in the target state"
  checks;
* `:2611` and `:2664` are the retry loops;
* `:2449` and `:2534` are the power on and failed power off recording.

The cleaner's `_active_domain_needs_write()` and
`_update_active_domain()` (`daemons/cleaner/scheduled_tasks.py:189` and
`:206`) and the sidechannel (`daemons/sidechannel/main.py:2096`) read it
too.

One consequence is concrete. A domain in libvirt's `SHUTDOWN` state, a
guest partway through shutting itself down, maps to `on`. An unpause
issued then reads it as already running, writes `on`, and answers 200
without calling libvirt. The question for the audit is no longer whether
the mapping is accurate. It is whether any decision built on the mapping
since phase 1b does the wrong thing for one of the states it folds
together. The master plan's phase 4 section is updated to say so.

`extract_power_state_pretty()` still raises `KeyError` on an
unrecognised enum. Its only caller is the instance statistics gathering
in `operations/node_inst_op.py:128-129`, so an unmapped state would fail
that operation and nothing else. All eight current `virDomainState`
values are mapped.

### S3 -- the deployed effect of the cleaner change was never measured

Phase 1b gave the cleaner branches which act on inactive domains:

* detecting a powered off guest;
* deleting the domain of an instance the database no longer knows;
* finishing a `delete-wait`.

Phase 2 added clearing autostart. Phase 1b merged before its sfcbr
inventory of inactive domains was taken (phase 1b plan, `:868`). Phase 2's
Future work asked for the cleaner's log lines on sfcbr to be read after
deploy, and nobody has done that. Whether the first deploy deleted
anything, and what, is unknown. This is the plan's equivalent of
`PLAN-queue-performance-phase-08-push-audit.md`'s lesson: ask what the
corrected code then *did*, not only whether it is correct. D4 asks it.

### S4 -- the phases' deferred work is not in the master plan

The master plan's Future work lists four items, all from the original
audit. The phase plans defer at least twelve more. Phase 1b's Future work
has six:

* #3373's remainder;
* stray domains of errored deletes;
* shutoff reason persistence;
* the unguarded crashed branch;
* `_await_image_event()`'s dead field;
* `NodeLock` holder death.

Phase 2 has four, phase 3 has four, and some are duplicated between
phases. Phase 3's "delete undefines a domain whose `destroy()` failed,
turning a running persistent domain transient and leaving it running" is
the most consequential, and appears only in that phase plan. D5 has the
documentation lens consolidate them. This is not corrected in the
planning commit, because judging which are still live and which deserve
an issue is audit work.

### S5 -- two conditional wave 1 checks do not apply, by evidence

* No range touches a proto file:
  `git diff --name-only <m>^1..<m> | grep proto` is empty for all five.
* No range adds a mermaid diagram: `git diff <m>^1..<m> | grep -c
  '^+```mermaid'` is 0 for all seven.

Step 4b re-runs both checks rather than trusting this paragraph, and
records them as not applicable.

### Claims that held

The `Merged` column names five merge commits, one per phase, and each is
on `develop` with the expected first parent. No phase landed in another
repository, and no phase's cell needs reconstruction.

## Decisions

### D1 -- the baseline is five merge ranges

`git diff develop...HEAD` on this branch is this plan document. Every
command in `PUSH-AUDIT.md` would pass against it.

| Phase | PR | Merge | Range | Size |
|-------|----|-------|-------|------|
| 0 | #4327 | `250a40871` | `250a40871^1..250a40871` | 7 files, +628/-20 |
| 1a | #4372 | `8aa69c5e4` | `8aa69c5e4^1..8aa69c5e4` | 11 files, +650/-85 |
| 1b | #4395 | `447ed75ae` | `447ed75ae^1..447ed75ae` | 20 files, +2950/-187 |
| 2 | #4437 | `776dc9e70` | `776dc9e70^1..776dc9e70` | 16 files, +899/-83 |
| 3 | #4468 | `d3d5f15ca` | `d3d5f15ca^1..d3d5f15ca` | 18 files, +2360/-67 |

That is 7,487 insertions, about half of them plan documents and unit
tests.

As in the precedents, `PUSH-AUDIT.md`'s "once per range" applies to the
mechanical greps, which run per range so a hit names the change that
introduced it. Each judgment agent reads all five ranges. The findings
worth having are about how the ranges interact.

**Judge correctness on the net state, not on a range.** Later phases
rewrote earlier ones:

* phase 3 removed phase 2's `stopped` flag;
* phase 2 removed the kvm health check's delete branch;
* phase 1b rewrote most of what phase 1a left in the cleaner.

A defect in an early range that a later range fixed is not a finding.
An agent that reports one has to check `develop` first.

### D2 -- the two plan-document merges get the documentation lens only

`6c5807018` and `304c6ca51` (S1) have no code, so there is nothing for
the code, test or security lenses to ask of them. Step 4e reads them for
claims that are still stated as current but are no longer true.

### D3 -- a fifth lens: who writes `power_state` and `agent_state`, and from what

`PUSH-AUDIT.md`'s headings ask whether each change is correct. This
plan's characteristic failure is different: two correct writers which
disagree. D9 in phase 3 is the known instance, and F13 (the cleaner
writing a stale reading over a power operation's value) is another,
fixed in phase 1b. Two members of a class found by accident are not
evidence that the class is now empty.

The lens builds a table of every writer of either field on `develop`.
For each writer it records:

* the triggering event;
* the observation the value is derived from;
* the lock held, if any;
* whether the write is conditional on a change.

Then it walks the interleavings that matter. Each operation below runs
against a cleaner pass, the sidechannel monitor, and each other:

* power on, power off, pause and unpause;
* create and delete;
* a guest poweroff.

For each pair the question is what the stored value is afterwards and
whether anything corrects it.

The same lens re-reads F9 (S2), because the mapping is the observation
most of those writers derive from. For each `virDomainState` value it
records what `extract_power_state()` returns and what each decision
built on it then does. It grades the result:

* deliberately left alone, with the reason;
* a finding;
* "unreachable", naming the configuration that makes it so.

This brief gets opus at high effort. It has to hold the instance
methods, the cleaner, the sidechannel daemon and the operation
dispatcher in mind at once, and what it is looking for is an
interleaving rather than a line.

### D4 -- a sixth lens: what the deployed cleaner did, read from sfcbr

S3's question has an answer in sfcbr's logs, and nothing else can give
it. Step 4h is read only:

* `loki-query` for the cleaner's log lines since the first deploy that
  carried `447ed75ae`, namely:
  * detected poweroff;
  * autostart cleared;
  * unknown inactive domain deletion;
  * delete-wait completion;
  * the strict lookup refusing to act;
* `sf-client` reads of the instances those lines name.

It establishes first which `develop` commit each sfcbr hypervisor runs.
A lens reading logs from before a deploy reports nothing and calls it
clean.

The judgement is whether every deletion was of something that should
have been deleted. A deletion that was not is blocking, whatever its
count. A lens with no log lines to read says so and why: not deployed,
retention expired, or the query matched nothing. It does not report a
clean result.

### D5 -- the documentation lens consolidates the deferred work

Step 4e collects every Future work item from the five phase plans and
the master plan. It drops duplicates, checks each against `develop` to
see whether a later phase resolved it, and proposes the master plan's
consolidated Future work list. Each item it judges a live defect, rather
than an extension, gets an issue. Phase 3's "delete undefines a running
domain" is the first candidate. Step 4i writes the list into the master
plan.

### D6 -- findings are graded and disposed of in writing

* Blocking findings are fixed in this phase.
* Advisory findings are filed as issues and listed in the Findings
  section.
* A finding outside this plan's scope is filed and named as such, rather
  than downgraded.

A clean lens is a result only alongside the list of what it examined.

### D7 -- wave 1 reports failures rather than stopping on them

Wave 1 runs against `develop` plus this plan, so a failure is a
pre-existing failure, not something this phase introduced. Record it,
check whether a plan range is implicated, and continue to wave 2.

One flake is known:
`shakenfist.tests.external_api.test_nested_sweep.NestedSweepOffTestCase.test_the_sweep`
fails under parallel stestr on refusal-reason ordering and passes in
isolation. Re-run it alone before grading it.

## Step plan

Every sub-agent except 4b and 4i is read only, and must not run
`pre-commit` or `tox`. Pre-commit's stash is repository wide, and 4b runs
it in this worktree.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | opus | none | *(Management session, this document.)* Close out phase 3 and register phase 4, correcting the master plan's phase 4 section per S1 and S2. Two commits: the close-out, then this plan. |
| 4b | medium | sonnet | none | Wave 1. See brief 4b. |
| 4c | high | opus | none | Code quality (2a). See brief 4c. |
| 4d | medium | sonnet | none | Test coverage (2b). See brief 4d. |
| 4e | medium | sonnet | none | Documentation (2c), plan-document merges (D2) and Future work (D5). See brief 4e. |
| 4f | high | opus | none | Security (2d). See brief 4f. |
| 4g | high | opus | none | Writers and F9 (D3). See brief 4g. |
| 4h | medium | sonnet | none | sfcbr's cleaner (D4). See brief 4h. |
| 4i | high | opus | none | *(Management session.)* Grade, fix, file, record. See brief 4i. |

4b runs first. 4c to 4h are independent and run in parallel once 4b
reports, taking its output as input.

### Brief 4b -- wave 1

* Run `pre-commit run --all-files` and `tox` in the worktree, and record
  the real output.
* Apply D7.
* Run `PUSH-AUDIT.md`'s four style greps against each of D1's five
  ranges separately, and report the output of each, not a summary:
  * lines over 120 characters;
  * stray `print(`;
  * new `etcd` references;
  * `mariadb.get_all_*(` without a `# nopushdown:` tag.
* Re-run S5's two checks and record proto freshness and mermaid
  rendering as not applicable, with that evidence.
* Run the style conformance brief from `PUSH-AUDIT.md`.

Phase 1b added `mariadb.py` lookup code (the strict lookup). Check it
follows the three-layer pattern: `_direct_*`, `_grpc_*`, and a public
wrapper.

### Brief 4c -- code quality

Take 4b's mechanical output as input. Read the five ranges and judge the
net state on `develop` (D1).

**The blocking rules.**

* SQL pushdown.
* Cached FK lists.
* The three-layer pattern, for the `mariadb.py` change in `447ed75ae`.

**Duplicated logic.** The obvious candidates:

* the four copies of suspend or resume, catch "domain is not running",
  then retry, in `pause()` and `unpause()` (`instance.py:2595-2680`);
* the two `domain is not running` string matches;
* any helper in `shakenfist_ci/base.py` that phases 0 to 3 each grew a
  variant of.

**The plan-references-in-code shared block.** This plan is prone to
breaking it: its phases named decisions, and its sub-agents wrote
comments while reading them. One instance, given as a worked example
and not as the answer: `_active_domain_needs_write()`'s docstring
(`daemons/cleaner/scheduled_tasks.py:181-187`) cites "S14 in
docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md"
in place of the reason. Grep the ranges' added lines in code, comments,
docstrings and test names for:

* `\bS[0-9]+\b`, `\bD[0-9]+\b` and `\bF[0-9]+\b`;
* `phase [0-9]`;
* `PLAN-`.

Report each hit as acceptable, because it points at unbuilt work, or as
a finding.

**The comment proportion shared block**, over the cleaner and
`instance.py`'s power methods. Both carry long explanatory comments,
some load bearing.

**Source file size.** `instance.py` and
`daemons/cleaner/scheduled_tasks.py` are both large and both grew.
Report their length and whether a seam exists. This is advisory only.

**Mechanical triage.** Triage every TODO, `# noqa` and `# type: ignore`
the sweep found.

### Brief 4d -- test coverage

The plan's mission promised the following. For each one, name the
functional test that exercises it, the unit tests, and whether the
functional test would have failed before the change:

* a guest which powers itself off is reported `off`;
* a powered off instance stays off across a hypervisor reboot;
* the power operations answer truthfully;
* sf-queues restore can no longer mass delete;
* there is a failed power on test;
* the fakes behave like libvirt.

The hypervisor reboot one has no functional test and is deferred in
phase 2's Future work. Say whether the unit tests close enough of that
gap.

**The fakes.** The plan exists partly because unit-test fakes modelled
impossible libvirt behaviour. Read every libvirt fake in the ranges'
tests, in `test_daemon_cleaner.py`, `test_instance.py`,
`test_util_libvirt.py`, `test_daemon_sidechannel_monitor.py` and
`test_ci_power_state.py`. Check each against the facts the phases
verified in libvirt's source:

* `listAllDomains()` with the active and inactive flags partitions the
  domains;
* `suspend()` of a paused domain succeeds;
* `resume()` of a running domain raises "domain is already running";
* both raise "domain is not running" when the domain is inactive;
* `power_state` is a dict.

Report any fake that would let a reintroduced defect pass. The master
plan's Future work asks whether a CI check for this is warranted, so
answer that.

**Adversarial cases.** Check coverage of:

* a domain that vanishes between lookup and action;
* a libvirt error partway through a cleaner pass;
* concurrent delete of an instance the cleaner is acting on.

Flag assertions that test implementation details, for example call
counts on mocks where behaviour could be asserted instead.

### Brief 4e -- documentation, plan documents and Future work

**The four documentation shared blocks**, over the five ranges:

* README;
* LLM doc;
* diagram;
* plan-phase references.

The canonical pages are `docs/operator_guide/power_states.md`,
`docs/developer_guide/api_reference/instances.md` and
`docs/developer_guide/subsystem_internals.md`. For each statement those
pages make about behaviour, check it against `develop`. Three phases
edited `power_states.md` and none re-read the whole page. Look
especially for a statement that phase 1b or 2 made true and phase 3
made false.

**The two plan-document merges (D2).** Read `6c5807018` and
`304c6ca51`, and any claim in the master plan still stated as current.
`8aa69c5e4` also edited `PLAN-transient-capacity-refusals.md`. Say what
that edit claims, and whether it is still true.

**Consolidate Future work (D5).** List every Future work item in
`PLAN-power-state-correctness*.md`. For each item, give:

* its source;
* whether `develop` still has the gap;
* a proposed grade: extension, known limitation, or live defect.

Propose an issue title and body for each live defect, but do not file
it; 4i files. Phase 3's delete-undefines item is the first candidate.

**Plan statuses.** Confirm they agree with `docs/plans/index.md`, and
run `tools/check-plan-status.py`.

### Brief 4f -- security

In order of likely value:

1. **What a failed power operation discloses.**
   `external_api/instance.py:1647-1652` and `:1607-1612` return
   `f'instance failed to power on: {e}'` with the 500, and write the same
   text to an audit event. `e` carries libvirt's or qemu's last error
   text, which can name host paths:
   * the disk image path, from the very failure that
     `test_lifecycle_power_on_failure` provokes by moving the root disk
     aside;
   * the qemu binary;
   * nvram paths.

   Who can read that response, and who can read the event? Is any of it
   a host detail a namespace user should not see? Compare the error
   bodies other instance endpoints return for libvirt failures.
2. **The cleaner's deletions.** The cleaner now deletes domains it
   cannot match to a live instance. Check that:
   * the filter to `sf:` names is applied before any action, so no
     foreign domain can be undefined;
   * the strict lookup cannot read "database unavailable" as "not
     found" on any path that deletes;
   * a crafted or racing instance state cannot make the cleaner undefine
     a running instance's domain.
3. **The abort file.** `daemons/daemon.py`'s `sidechannel_abort_path()`
   builds a path under `/run/sf` from a name. Confirm every caller
   passes a process-chosen UUID, per the path-traversal shared block.
   Check that a missing or unwritable `/run/sf` cannot raise into
   `unpause()`.
4. **Concurrency.** Phase 1b added `NodeLock` timeouts and a lock in the
   cleaner. Check for a lock ordering against the power endpoints'
   instance lock that could deadlock. 4g covers the value races; this
   covers liveness.
5. **Subprocess.** Check the `virsh undefine --nvram "sf:{instance_uuid}"`
   call in the cleaner, and any other shell call in the ranges.

Report each finding with a severity. Critical and high findings must be
fixed before this phase closes.

### Brief 4g -- writers of `power_state` and `agent_state`, and F9

This is the plan-specific lens (D3). Read `develop`, not the ranges.

1. **The writers table.** Grep `update_power_state(`, `power_state =`,
   `agent_state =` and `.agent_state` assignments across `shakenfist/`.
   This plan also found that handlers are dispatched by name, so grep
   finding no caller is not proof of dead code: check
   `operations/*.py` dispatch by task name. For each writer, record:
   * the trigger;
   * the observation it derives the value from;
   * the lock it holds;
   * whether it writes only on change.
2. **The interleavings.** For each pair of writers that can run
   concurrently, say what the stored value is after both, and what
   corrects it if it is wrong. At minimum, cover:
   * the cleaner's active loop against each power operation, since F13
     was fixed for one direction (confirm which);
   * the cleaner's inactive loop against power on, since a domain
     starting during the pass is the case;
   * the sidechannel monitor against pause, unpause and power off, since
     D9 was the pause case (confirm the others);
   * create's power on against delete;
   * the kvm health check's stale `kvm_pid` clearing against a power on.

   Grade each interleaving:
   * sound, naming the mechanism;
   * self-correcting within one cleaner pass, which is acceptable if
     documented;
   * a defect.
3. **F9.** For each of `NOSTATE`, `RUNNING`, `BLOCKED`, `PAUSED`,
   `SHUTDOWN`, `SHUTOFF`, `CRASHED` and `PMSUSPENDED`:
   * what `extract_power_state()` returns;
   * whether our domain configuration can reach that state, citing
     `libvirt.tmpl` (`on_crash`, `<pm>`);
   * what each decision site in S2 then does.

   S2 gives one concrete case: unpause during `SHUTDOWN` answers 200
   without calling libvirt. Grade it, and find any others. Finish with
   one sentence for the master plan: F9 deliberately left alone, and
   why; or F9's specific remaining defects, filed.

### Brief 4h -- what sfcbr's cleaner did

Read only. Use `~/bin/loki-query` (see its skill) and `sf-client` with
the operator's sfcbr credentials; change nothing.

1. **Establish what sfcbr runs.** For each hypervisor, find the deployed
   version, and the date the first build containing `447ed75ae` (phase
   1b) and `776dc9e70` (phase 2) was deployed. If neither has been
   deployed, stop and report that. It is a result, and S3 stays open.
2. **Query the cleaner's log lines** since that date. Take the exact
   message strings from `daemons/cleaner/scheduled_tasks.py` on
   `develop`; do not guess them. Exclude Loki's own query log (see the
   `loki-query` skill). Count, by hypervisor and day:
   * detected poweroff;
   * autostart cleared;
   * the deletion of an inactive domain with no live instance;
   * delete-wait completion;
   * any refusal or skip the strict lookup logs.
3. **Check every deletion.** For each instance UUID a deletion line
   names, read the instance with `sf-client`, or its events if it is
   gone. Decide whether it should have been deleted. Report any that
   should not have been, with the evidence.
4. **Check the detected poweroffs.** Spot check three: does the instance
   now read `off`, and did its owner power it off, or did the guest shut
   down? A poweroff detected for an instance whose domain was running is
   blocking.

Report counts, not log dumps. Give a verdict on S3.

### Brief 4i -- grade, fix, file, record

*(Management session.)*

* Spot check two findings per agent against `develop` before accepting
  each report, and record the spot check.
* Grade every finding blocking or advisory, and bring the blocking list
  back before fixing anything (see Back brief).
* Fix the blocking findings in this branch, each with a test shown to
  fail without the fix.
* File advisory findings and D5's live defects as issues.
* Write the Findings section of this plan, the master plan's
  consolidated Future work and its *Bugs fixed during this work*, and
  F9's one sentence in the master plan's findings table.
* Set the statuses. The master plan reaches Complete only if no blocking
  finding remains open.
* Run `python3 tools/check-plan-status.py` and `pre-commit run
  --all-files`.

## Risks and mitigations

* **The audit rubber-stamps green code.** Five ranges that all passed
  review and CI invite confirmation. *Mitigation:* D6, so every clean
  lens lists what it examined, and 4i spot checks two findings per
  agent.
* **An agent reports a defect a later phase fixed.** *Mitigation:* D1's
  net-state rule, restated in 4c's brief. 4i checks each finding against
  `develop` before grading it.
* **4h finds the cleaner deleted something it should not have.** That is
  a production data loss finding, not an audit footnote. *Mitigation:*
  it is blocking by D4. 4i reports it to the operator immediately,
  before finishing the rest of the phase, and the fix may need its own
  pull request ahead of this one.
* **4h has nothing to read.** sfcbr may not have deployed the phases, or
  Loki's retention may have expired. *Mitigation:* the brief stops and
  reports that as a result. S3 then stays open in the master plan's
  Future work, rather than closing as clean.
* **4g finds a race with no cheap fix.** The writers are spread across
  three daemons. *Mitigation:* a race that self-corrects within a
  cleaner pass is graded as documented-acceptable, not blocking. Only a
  race that leaves a wrong value with nothing to correct it blocks.

## Definition of done

Run these; do not read them.

1. Phase 3's `Merged` cell reads `d3d5f15ca`:
   `grep -c '| 3\. .*| Complete | .d3d5f15ca. |' docs/plans/PLAN-power-state-correctness.md`
   prints 1.
2. `python3 tools/check-plan-status.py` passes.
3. `git diff --exit-code origin/develop -- docs/plans/order.yml`.
4. Every lens has a written result naming what it examined: 4b to 4h,
   seven in all.
5. 4b's four style greps have output recorded per range, and proto
   freshness and mermaid rendering are recorded as not applicable with
   S5's evidence.
6. 4g's writers table exists in the Findings section, with each
   interleaving graded. F9's row in the master plan's findings table
   carries the re-read's outcome.
7. 4h's verdict on S3 is recorded. It either names the deployed version
   and the deletion counts, or explains why there was nothing to read.
8. The master plan's Future work holds every live item from the five
   phase plans, and each live defect has an issue number.
9. Every finding has a grade and a disposition: fixed here, filed as
   #NNNN, or declined with a reason.
10. No blocking finding is open. The master plan and `docs/plans/index.md`
    read Complete, 6 of 6, only if that holds.
11. If the audit finds nothing, that is recorded in one sentence.

## Back brief

Before executing any step of this plan, please back brief the operator
as to your understanding of the plan and how the work you intend to do
aligns with that plan.

Two gates:

* **Before 4h runs, confirm D4.** It reads production logs and instance
  records on sfcbr. Both are read only, but the operator should know it
  is happening, and may prefer to run the queries personally.
* **Before 4i fixes anything, bring the blocking list back.** Grading a
  finding against code that has shipped and is green is a judgement
  about risk, not a rule.

## Findings

The audit found one blocking defect and six findings that were fixed
here, and it filed three issues for the rest. The blocking defect was in
phase 1b's own guard: the strict instance lookup did not work over
gRPC, which is the only path the cleaner uses. So a MariaDB error still
read as "this instance does not exist" just before the cleaner deleted a
domain and its disks.

Every lens's result is below, with what it examined. The management
session checked two findings per agent against `develop` before grading
them; the checks are recorded under each lens.

### Wave 1 (4b) -- passed

* **pre-commit and tox.** `pre-commit run --all-files` passed all
  fifteen hooks. tox passed `py3`, `flake8` and `cover`, with 0
  failures. `test_nested_sweep` passed, so it did not need re-running on
  its own.
* **Style greps.** All four style greps were empty for each of D1's five
  ranges: lines over 120 characters, `print(`, `etcd`, and
  `get_all_*` without `# nopushdown:`.
* **Proto freshness and mermaid rendering.** Neither applies. No range
  touches a proto file, and no range, including the two plan-only
  merges, adds a mermaid block.
* **The strict lookup in `mariadb.py`** (`447ed75ae`). It has the
  three-layer shape, but 4c showed that shape is not compliant in
  substance (B1).
* **Style conformance.** It passed. The plan citations it noticed went
  to 4c.

### Fixed in this phase

| # | Finding | Lens | Commit | Test that fails without the fix |
|---|---|---|---|---|
| B1 | **Blocking.** The `GetInstance` servicer called `_direct_get_instance()` without `strict`. A MariaDB `OperationalError` therefore came back as `found=False` with OK status. The cleaner is never a direct MariaDB caller, so its strict lookup returned `None` on a database error. `_instance_confirmed_absent()` then let it destroy and undefine the domain and remove its directory. The servicer now reads strictly, and a failed read answers INTERNAL. | 4c | `1e4368ed4` | `GetInstanceServicerTestCase.test_operational_error_is_internal_not_a_miss` |
| B2 | `_power_on_inner()` wrote `agent_state` "no contact" after the domain started, but a monitor which had already handshaken kept its cached "ready". That stranded the agent on a running guest, the same class as phase 3's D9. Power on now restarts the monitor, but only when it started the domain. | 4g (G1) | `dfa7846d9` | `InstancePowerOnTestCase.test_starting_the_domain_restarts_the_sidechannel_monitor`, `test_already_running_leaves_the_sidechannel_monitor_alone` |
| B3 | A domain which stopped between the cleaner's `isActive()` and its state read was written `off` by the active loop. That skipped the detected power off's `agent_state`, event and autostart clear. The active loop now leaves such a domain to the inactive loop. | 4g (G2) | `1a2e99374` | `test_domain_stopping_inside_the_active_loop_is_left_to_the_inactive_loop` |
| B4 | `unpause()` answered 500 when a `resume()` found the domain already running. It now treats that as success. | 4d | `33cb95f99` | `test_unpause_retry_finding_the_domain_running_succeeds`, `test_unpause_first_resume_finding_the_domain_running_succeeds` |
| B5 | The cleaner's per-domain guard caught only `libvirtError`, although its comment promised to skip only the failing domain. A failed `virsh` fallback, a database outage or a node lock transport error ended the whole pass. Both loops now skip only the failing domain, for a named set of errors. | 4c (B), 4f (F2) | `5b2b95abd` | `test_virsh_failure_skips_only_that_domain`, `test_active_domain_failure_skips_only_that_domain` |
| B6 | The plan left about 146 citations (S14, D7, F13, "phase 1b", plan paths, commit hashes) in code, docstrings, test names and assertion messages, against the plan-references-in-code rule. They were replaced by the reasons they stood for. `tools/check-plan-phase-references.py` checks only `docs/`, which is why nothing caught them. | 4c (G) | `6dae09023` | n/a |
| B7 | Failed power on and power off answered 500 with libvirt's raw error text, which names host disk, nvram and qemu paths. That broke the convention that every other instance endpoint's libvirt failure answers an opaque `server error`. The bodies are now generic, and the detail stays in the owner-readable audit event. Graded low (4f F1); fixed because it was cheap, at the cost of reversing phase 3 D1's "with the last error". | 4f (F1) | `924184994` | `test_failed_power_on_answers_500_and_records_the_error`, `test_failed_power_off_answers_500_and_records_the_error` |

The management session re-ran the fix commits' mutation script, and it
killed all nine mutations.

### Filed

* [#4485](https://github.com/shakenfist/shakenfist/issues/4485).
  `agent_state` can read ready while an instance is paused or off. This
  covers 4g's G3 (an in-flight monitor write landing after a pause or
  power off), G4 (the monitor constructor overwriting the paused and off
  values) and G5 (pauses and resumes outside Shaken Fist).
* [#4486](https://github.com/shakenfist/shakenfist/issues/4486). Delete
  removes an instance's disks while its domain is still running, after
  `destroy()` failed twice. This corrects phase 3's Future work entry:
  that entry says delete undefines the running domain, but `undefine()`
  is in fact skipped. The issue also carries 4d's finding that the
  cleaner's "Deleting stray instance" branch, which is what recovers
  from this, has no tests.
  * 4e graded this blocking. The management session graded it advisory:
    the disks belong to an instance the user asked to delete, and the
    cleaner kills the stray domain within minutes.
* [#4487](https://github.com/shakenfist/shakenfist/issues/4487).
  Clean-ups from 4c:
  * one matcher for "domain is not running";
  * the copies of pause and unpause;
  * the two CI node lookups;
  * `_await_instance_event()`'s dead `message` parameter;
  * the sidechannel cache explanation, written out three times.

### Recorded, not filed

* **File size.** `instance.py` is 3,312 lines, 226 more than before the
  plan. Its power operations, about 440 lines, are a seam for a later
  split. `daemons/sidechannel/main.py` is 2,151 lines. Both are advisory.
* **Type hints.** The new cleaner and instance helpers have none, and
  none of these files is in the mypy rollout.
* **The TODO in `_undefine_domain()`.** It has an answer:
  `domain.undefineFlags(VIR_DOMAIN_UNDEFINE_NVRAM)` would also remove the
  virsh fallback.
* **The cleaner's virsh helpers interpolate `instance_uuid` into a shell
  command.** That is safe because `_sf_instance_uuid()` accepts only a
  name whose suffix equals libvirt's own UUID. The helpers themselves
  accept any string, so validating it inside them would be defence in
  depth (4f F3).
* **`sidechannel_abort_path()`.** Every caller passes a process-chosen
  UUID (4f F4).
* **Foreign domains.** A domain someone defines by hand, named
  `sf:<its own uuid>` and with no instance, is treated as ours and
  deleted (4f F6).
* **`power_off()` with no domain** writes nothing and answers 200. This
  predates the plan (4c D).
* **`subsystem_internals.md`** says no lock is taken when libvirt and
  the database agree. Crashed and I/O-error-paused domains are
  exceptions (4e). Advisory.

### Who writes `power_state` and `agent_state` (4g, D3)

The full writers tables and the twelve interleavings are in 4g's report.
Its conclusions:

* **Sound:**
  * I1, the cleaner's active loop against each power operation. The
    F13 fix covers one direction, and the other was never open.
  * I3, the inactive loop against power on.
  * I5, the D9 fix.
  * I8 and I9, create against delete and against the cleaner.
  * I12, the power operations against each other.
* **Self-correcting:**
  * I10, the kvm health check clearing a fresh `kvm_pid`, within one
    resources daemon pass.
  * I11, a guest powering off during a power operation, within one
    cleaner pass.
* **Defects:**
  * I2 (B3) and I7 (B2) were fixed here.
  * I4 and I6 are filed as #4485.

### F9 re-read (4g, D3)

**F9 is deliberately left alone.** Under `libvirt.tmpl`, the only
reachable state which `extract_power_state()` folds into another is
`SHUTDOWN`. It is transient: with `on_poweroff=destroy`, libvirt kills
qemu as soon as it enters it. Every decision built on it either matches
what libvirt itself answers, or leaves a value which the cleaner's next
pass corrects. For example, `resume()` of a `SHUTDOWN` domain returns
success without acting, so `unpause()`'s shortcut answers what libvirt
would. The other folded states are unreachable:

* the qemu driver never sets `NOSTATE` or `BLOCKED`;
* there is no panic device, so `CRASHED` cannot happen, and
  `on_crash=restart` would make it transient anyway;
* `<pm>` disables S3 and S4, so `PMSUSPENDED` cannot happen.

### What sfcbr's cleaner did (4h, D4) -- nothing yet, so S3 stays open

All six sfcbr hypervisors run `ec82dca6d`, which carries phases 1b and
2 but not 3. Builds with phase 1b were first deployed between 19:00 and
22:00 UTC on 2026-10-02, and builds with phase 2 between 2026-10-04
21:00 and 2026-10-05 09:00 UTC. From 2026-10-03 to 2026-10-07 the
cleaner logged none of the following, on any host:

* a detected power off;
* clearing autostart;
* removing an unknown inactive domain;
* deleting, enqueueing or abandoning a stray powered off instance;
* a strict lookup refusal;
* an active loop destroy.

The logging was visible: a control search for another of the cleaner's
warnings ("Blob missing from node") found 707 lines, and INFO and
WARNING both reach Loki. No sfcbr instance is currently powered off and
undeleted. **The branches have never run there, which is not the same as
running clean**, so S3 moves to Future work. The same queries need
re-running after a week or two of the current build. Two details for
whoever runs them: `program` is not a Loki stream label, so filter with
`|= "sf-cleaner"`, and exclude `host="maui"`. The management session
reproduced the zero count and the control.

### Test coverage (4d)

Every promise in the mission has a unit test. Every promise except two
also has a functional test which would have failed before the change:

* a hypervisor reboot, deferred in phase 2 and proxied by the autostart
  tests;
* sf-queues restore, which cannot be exercised safely in a functional
  run.

Four of the five libvirt facts the phases verified are faithfully
modelled by the fakes. The fifth, `resume()` of a running domain, was
untested for unpause, and B4 now tests it. A CI check for fakes is not
warranted, which answers the master plan's Future work question.

### Documentation (4e)

* **Docs against code.** Apart from the advisory sentence recorded
  above, every behavioural statement in `power_states.md`, the power
  section of `instances.md`, and the parts of `subsystem_internals.md`
  these ranges touched matches `develop`.
* **Plan-only merges (D2).** Neither `6c5807018` nor `304c6ca51` leaves
  a stale claim stated as current. `8aa69c5e4`'s edit to
  `PLAN-transient-capacity-refusals.md` is still true.
* **Plan statuses.** `tools/check-plan-status.py` passes.
* **Future work (D5).** The consolidated list is in the master plan.

### Spot checks

| Lens | Checked |
|---|---|
| 4b | `test_nested_sweep` passed in the tox log; the strict lookup's three layers |
| 4c | B1, by reading the servicer and `_direct_get_instance()`; the cleaner's single active loop catch |
| 4d | The active loop's catch at debug level; no test references "Deleting stray instance" |
| 4e | `_delete_on_hypervisor()`'s unconditional `rmtree`. Confirmed, but graded advisory, not blocking. |
| 4f | `suppress_exceptions_to_client` answers `server error`; `_domain_root_disk_path`'s "the API does not expose disk paths". 4f's "cleaner deletions sound" missed B1, and 4c's reading was taken. |
| 4g | G1's write order in `_power_on_inner()`; G2's unguarded `off` in `_update_active_domain()` |
| 4h | The searched string is the cleaner's own; a zero count and the control reproduced |
