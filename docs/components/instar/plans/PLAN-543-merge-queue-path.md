# PLAN-543: Cut the merge queue's critical path

## Status: Complete

Every step in the Execution table is done. Steps 1 to 6 and 8 landed
in #671 (merged as `eea59b99`), with step 5's measurement recorded
under Execution ("The step-5 measurement"). Step 7's two halves
landed in #673 (merged as `673233f3`), the second once the conductor
gate cleared; the step 7 row records both and the evidence for the
gate. The post-merge work is recorded under Execution ("The step-7
measurement" and "The second push audit").

**A red nightly that was not the disk.** On 18 Oct the six VHD tests
this plan moved off the merge queue failed on all seven distros
(#780 to #786), each in a fraction of a second with `instar info`
returning `Invalid argument (os error 22)`. That was the first
nightly to read the testdata disk, and the merge queue cannot see
those six tests, so the disk was the obvious suspect -- and wrong.
The cause was #778: Debian's 6.12.113 kernel, which reached the CI
runner images in the same window, rejects `KVM_SET_SREGS` when the
VMM sets no guest CPUID. The evidence is a pair of nightlies that
both read the disk: run 39080337878 on `81423f22` failed every leg,
and run 39178562519 on `b0e1f249` passed every leg (six tests in
662 s on Debian 13), with only #779 and #788 -- the #778 fix --
between them. It is recorded here because the coincidence will
recur: a nightly-only test set means a regression that lands
alongside an infrastructure change is first seen together with it.

#543 itself is not closed by this plan: the capacity direction is its
other half.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the workflow's gating
model, the Makefile `test-container-*` selectors, the test-partition
guard, `tools/ci/` script and self-test conventions), and ground your
answers in what the code actually does today. Do not speculate about
the codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

Consult `AGENTS.md` for build commands and conventions,
`docs/testing.md` for the distro matrix's documented behaviour, and
`.github/workflows/functional-tests.yml:26-53` for the recorded
argument about what runs where. That comment block is a decision,
not a description; this plan changes one of its premises and must say
so there rather than quietly working around it.

The request was to triage #652's merge-queue timeout, which became
the measurement issue #543 had been waiting for. The first draft of
this plan proposed packing the seven matrix legs into three jobs.
Michael then asked whether any jobs should move to a scheduled
nightly instead, and whether a big data copy at the start of each leg
could be cached. Measuring both questions moved the plan off packing
and onto the suite; see Situation.

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

This is a single-change plan in the shape of
`PLAN-611-backing-path-oracle.md`: one pull request, no phase files.
Its steps are the Execution table below, and the push audit is its
last step.

## Situation

Issue #543 records a run of `checks_timed_out` ejections of green
pull requests from instar's merge queue -- five of them tabulated in
its 2026-10-10 update. The cause was found that day and fixed in
shakenfist/private-ci#66: the provisioner walked `CI_IMAGES` then
`CI_SIZES` in declaration order, so any queued `xs`/`s`/`m`/`l` job
took the fleet's free slot ahead of every `xl` job, however long the
`xl` job had waited. Oldest-queued-first replaced that.

PR #652's ejection on 2026-10-12 was the first after the fix, and so
the measurement #543 said instar's CI changes were on hold for. The
fix did what it was designed to do, and it is not enough. Queue wait
per `xl` matrix job, before (15 merge groups, 09-30 to 10-09) and
after (6 merge groups, 10-10 to 10-12):

| | Before | After |
|---|---|---|
| Jobs measured | 105 | 40 |
| Median wait | 90 min | 121 min |
| p90 wait | 352 min | 188 min |
| Max wait | 524 min | 236 min |
| Never scheduled | 0 | 2 |

Fair ordering removed the starvation -- p90 nearly halved, the worst
case more than halved -- but added no capacity, so the distribution
converged on the mean rather than shifting down, which is why the
median rose. Throughput is unchanged. The same convergence now shows
on the front of the critical path, where the two `debian-13, s` jobs
used to clear in about three minutes:

| `s` job wait | Before | After |
|---|---|---|
| Median | 3 min | 11 min |
| p90 | 10 min | 54 min |
| Max | 49 min | 99 min |

#652's run holds both of the worst post-fix observations:
`build-and-test` waited 99 minutes and `package-build` 54, so 153 of
its 362 minutes were spent before the matrix was even queued. The run
itself finished `success` 58 minutes after the ejection, 420 minutes
after its merge group was created.

### What the survey found: there is no data copy to cache

The first thing to check was whether each leg pays a large fixed cost
before testing -- a testdata clone, an image pull, a package install
-- that a cache could remove. It does not. Step times from the
Debian 12 leg of run 38206699440:

| Step | Time |
|---|---|
| Set up job, checkout | 7 s |
| `tools/ci/prepare-testdata.sh` | 88 s |
| Resparsify test images | 6 s |
| `tools/ci/install-docker.sh` | 21 s |
| Download the package artifact | 5 s |
| **Run the suite on debian:12** | **2539 s** |

Inside that last step, pulling `debian:12` and installing the package
plus its test prerequisites -- 418 packages, because `qemu-utils`
drags in most of a desktop -- accounts for about three minutes. The
suite itself reports `Ran: 3588 tests in 2349.5103 sec`.

**So about three minutes of a forty-two minute leg is setup.** A
perfect cache of every byte fetched before the suite starts would buy
two minutes. This is not where the time is.

The testdata step deserves its own note, because the obvious fix for
it is already written and dead. `tools/ci/prepare-testdata.sh` has two
branches: update a cache at `$TESTDATA_CACHE`
(`/srv/ci/cached/instar-testdata`) in place, or clone fresh into the
workspace. The job log takes the second every time --

```
No cache at /srv/ci/cached/instar-testdata, cloning fresh...
Cloning into '/srv/ci/_work/instar/instar/instar-testdata'...
Updating files: 100% (769672/769672)
Filtering content: 100% (308/308), 7.46 GiB | 133.88 MiB/s, done.
```

-- because the sfcbr runners are fresh VMs, so nothing persists at that
path and the update-in-place branch never fires. Each leg checks out
769,672 files and pulls **7.46 GiB** of git-LFS content, taking a
median of 82 seconds across 28 legs (72 s to 129 s).

Seven legs per merge group is about **52 GiB of LFS transfer**, and the
five pull-request jobs that also prepare testdata add roughly 37 GiB
per pull request. That is a real cost and worth removing, but it is not
this plan's cost: the legs run in parallel, so it is about 80 seconds
of the critical path. Recorded under Deferred work, where it belongs --
it is a runner-image change, not an instar change.

### Where the time is: one worker, and six tests

The suite runs at `--concurrency 4`. Per-worker figures from the same
leg, with the execute totals derived from the per-test lines and
agreeing with stestr's own Worker Balance block to the second:

| Worker | Execute | Wall |
|---|---|---|
| 0 | 2197 s | 39:09 |
| 3 | 1490 s | 24:53 |
| 2 | 1367 s | 22:49 |
| 1 | 913 s | 15:14 |

**The leg's wall time is worker 0's wall time**, and workers 1 to 3
finish between 14 and 24 minutes earlier and then idle. Even
distribution of the 5967 seconds of execute time would be 1492
seconds per worker, so roughly twelve minutes of every leg is
scheduling imbalance rather than work.

The imbalance has a single cause. Worker 0 drew all three VirtualPC
tests:

| Test | Time | Worker |
|---|---|---|
| `TestConvertVhdToQcow2Roundtrip.test_virtualpc_vhd_qcow2_roundtrip` | 609 s | 0 |
| `TestConvertVhdToRaw.test_convert_virtualpc_vhd` | 505 s | 0 |
| `TestConvertVhdCompare.test_compare_virtualpc_vhd_vs_raw` | 310 s | 0 |

That is 1424 of worker 0's 2197 seconds. `tests/.stestr.conf` sets
only `test_path` and `top_dir`, and the stestr repository is created
fresh under `/tmp` in each container, so stestr has no prior timings
to bin-pack from and these land wherever the split puts them. The
same imbalance appears in a local run of the suite on this host (1520
tests on one worker against 510 on another), so it is systematic, not
a property of CI. It has simply never mattered, because the suite
takes six minutes locally.

Those three tests have three Hyper-V siblings, and the six together
are half the suite:

| Class | Tests | Execute |
|---|---|---|
| `TestConvertVhdToQcow2Roundtrip` | 2 | 1233 s |
| `TestConvertVhdToRaw` (hyperv, virtualpc) | 2 | 1064 s |
| `TestConvertVhdCompare` (hyperv, virtualpc) | 2 | 648 s |
| **Subtotal** | **6** | **2945 s of 5967** |

By module, `test_convert` is 5045 of the 5967 seconds in 248 of 1684
timed tests; everything else in the suite is under a thousand
seconds. The remaining 30 tests of the `convert-vhd` partition are 45
seconds in total.

### The two fixes are the same fix, twice

Removing those six tests changes the per-worker picture to 866 / 808 /
773 / 577 seconds against an even split of 756 -- a worst worker 15%
over even, instead of 47% over. **The imbalance is caused by the six
tests, so the two findings above are one finding.** In sequence:

| | Worst worker | Leg |
|---|---|---|
| Today | 2197 s | 42 min |
| Timing data only | ~1492 s | ~30 min |
| Six tests nightly only | 866 s | ~16 min |
| Both | ~756 s | ~15 min |

Balancing first does deliver about twelve minutes, because the six
tests are still there to be balanced. But once they move, balancing
is worth about another ninety seconds. **Michael chose the
balance-then-nightly order knowing this**; the plan keeps it, and
scopes step 1 accordingly -- as the guard that stops a future heavy
test silently recreating this, not as the performance fix.

### Why packing is no longer first

The first draft of this plan packed the seven legs into three jobs to
cut `xl` acquisitions from seven to three. That is still a real
lever -- with ordering fair, acquisition count is what the critical
path is made of -- but it is now second order. At a 42-minute leg,
seven legs through instar's effective share of about three slots is
three waves, and the matrix stage of run 38206699440 took 235
minutes. At an 18-minute leg it is about 54 minutes for seven legs in
three waves against about 48 for three packed groups in one. The gap
mostly closes on its own, so packing is a decision to take **after**
the measurement in step 5, not before it. The packing analysis is
preserved under Deferred work so it does not have to be redone.

## Mission and problem statement

Get the merge group's critical path far enough under the 360-minute
`check_response_timeout_minutes` that a busy fleet does not eject
green pull requests, by removing work from the gate rather than
rearranging it. Change which tests gate a merge; change nothing about
what is tested, only when.

Target, with every term measured in step 5 rather than assumed:
`build-and-test` 21 min, `package-build` 7 min, a matrix leg about 18
min, and seven legs through three slots about 54 min, for a critical
path near 85 minutes of work against the 420 observed. The queue wait
is not in instar's gift and the capacity direction in #543 remains
its complement.

## Open questions

None blocking. The judgement a reviewer should attack is Decision 3:
six tests that are arguably the matrix's whole purpose stop gating
merges.

The disk interface was open when this plan was drafted and is now
settled; Decision 8 records it. The release gate that followed it --
step 7 could not merge until private-ci's conductor phase was deployed
(Decision 9) -- has cleared, and Decision 9 records the three pieces of
evidence.

One scope correction worth carrying, because it was wrong in the first
exchange with that session, and a second correction on top of it.
Seven places prepared testdata when that exchange happened, not five:
`package-matrix`, `integration-core`, `integration-convert-qcow2`,
`integration-convert-vhd` and `oslo-crossval-master` in
`functional-tests.yml`, plus `coverage-fuzz.yml` and
`test-drift-fix.yml`. `package-smoke` and `snapshot-harnesses` do not.
Step 3 then added two more, because the nightly has two shapes of leg:
`nightly-legs` and `measure` in `nightly-matrix.yml`. **Nine jobs
prepare testdata, and eight of them carry the label** -- the exception
is `test-drift-fix.yml`, immediately below. The set is asserted in both
directions rather than trusted: a consumer without the label pays for a
7.46 GiB clone it did not need, and a labelled job that is not a
consumer takes a scarce disk-bearing runner for nothing.

`test-drift-fix.yml` is the exception and must stay one.
`[self-hosted, claude-code]` is the *static* fleet, managed by 33fl's
`static_runner.yml`; the conductor only watches it and never attaches
anything to those VMs, so no static runner advertises `instar`, and a
job carrying the label would queue until GitHub cancels it 24 hours
later. It keeps cloning fresh. Because that is an inconsistency a
later reader will want to tidy away, step 7 records the reason in the
workflow itself and not only here. Seeding
`/srv/ci/cached/instar-testdata` once per static VM would let the
existing update-in-place branch work there too, but it is only safe
while each VM runs one job at a time, and it is 33fl's change --
tracked in private-ci's plan, not here.

## Decisions

1. **Seed stestr with timing data, as a guard rather than an
   optimisation.** Without it the scheduler cannot bin-pack, so any
   future test over a few minutes recreates the single-slow-worker
   failure mode, invisibly -- the suite just gets slower. Worth about
   ninety seconds once Decision 3 lands, and worth having anyway for
   that reason.

   It must be *derived*, not hand-maintained. This repository has
   already been bitten by hand-maintained figures drifting (the
   mutation harness's case counts drifted three review rounds
   running, and are now computed). A committed timing file refreshed
   by the nightly job in Decision 2 satisfies that: the nightly runs
   the suite anyway, so it can publish the timings it measured.

2. **A nightly cross-distro job**, in the shape of
   `differential-fuzz.yml`: `schedule:` plus `workflow_dispatch`,
   `issues: write`, and a failure that files or updates an issue. A
   nightly whose red is only visible in the Actions tab is not a
   safety net, so the issue-filing step is part of the job and not a
   follow-up.

   Settled while building it, and recorded here because the back brief
   asks for the workflow-file question in writing:

   - **Its own file**, `.github/workflows/nightly-matrix.yml`.
     `needs:` cannot cross workflow files, and `package-build` is
     gated to `merge_group || workflow_dispatch`, so a scheduled job
     could not consume its artifact from either side of that boundary.
     The nightly builds its own packages, as `differential-fuzz.yml`
     builds its own binaries. Adding `schedule:` to
     `functional-tests.yml` would also fire every job there whose
     condition begins `always()`, starting with `can_enqueue`.
   - **Two shapes of leg, because D1 and D3 pull against each other.**
     D1 assumes the nightly runs the whole suite and can therefore
     publish the timings it measured; D3 makes it run six tests. Both
     hold: seven distro legs run the six moved tests, which is the
     coverage, and one more leg runs the whole suite on Debian 13,
     which is the measurement. That leg is redundant as coverage and
     is not there for coverage -- it is the only job left in CI that
     executes the whole suite in a single stestr repository, so it is
     the only place the timing file can be derived from. The workflow
     says so, because a reader will otherwise delete it.
   - **The measurement leg does not seed the timings it measures.**
     stestr keeps one duration per test id and only the tests that
     execute update theirs, so a seeded repository hands back the
     seeded figure for everything that did not run: a deleted test's
     duration would live on forever, and a test that silently stopped
     running would never lose its old one. Setting `STESTR_TIMING_OUT`
     therefore turns seeding off for that run, which costs it an
     unbalanced partition and nothing else.
   - **The refresh is a pull request, never a push to `develop`,** and
     only when the measurement moved materially. "Materially" is
     defined on each test's share of the suite's weight rather than on
     seconds, because stestr's bin packing is scale-free: a uniformly
     slower runner changes every number in the file and not one
     scheduling decision. One test moving two points of the suite's
     weight (about 100 seconds, against a worker's quarter share of 25
     points), or a tenth of the weight relocating between tests, is
     material. Everything else is noise, and the file is only a
     scheduling hint -- a stale one degrades packing gradually and can
     never make a run wrong -- so the default is silence.

3. **The six heavy VHD conversion tests move off the merge gate** and
   run nightly on all seven distros. They are the Hyper-V and
   VirtualPC roundtrip, to-raw and compare cases.

   This is the decision that costs something, and the cost is real:
   those conversions are where a distro's qemu-img is most likely to
   differ from instar, which is close to the matrix's reason for
   existing. Three things make it the right trade anyway. They
   already run on every pull request through
   `integration-convert-vhd` on debian-13, so an instar-side
   regression is caught at PR time regardless of this change. The
   distro axis moves when a distro ships a new qemu, not when instar
   changes, so a per-merge cadence is far finer than the signal. And
   a nightly surfaces a distro divergence within 24 hours, against a
   status quo where the whole queue intermittently ejects green work
   and nothing lands at all.

   Only the six move, not the `convert-vhd` partition: its other 30
   tests are 45 seconds, and moving them would buy nothing while
   making the merge-queue selector harder to read.

4. **The merge-queue matrix gets an explicit selector**, and the
   nightly gets its complement, both expressed as Makefile
   `test-container-*` targets like the existing three so that
   `tools/ci/check-test-partition.py` reads them from the one place
   they are defined.

5. **Extend the partition guard to the merge queue and the
   nightly.** It discovers pull-request jobs today, so the six tests
   staying claimed by `integration-convert-vhd` keeps it green and it
   would not notice the matrix selector dropping something no other
   queue job covers. The silent loss of a whole module from the gate
   is this plan's worst failure mode and has no symptom -- CI goes
   green faster. The guard must assert that the merge-queue selector
   and the nightly selector together cover what the matrix covers
   today.

6. **The superset argument is amended, not deleted.**
   `functional-tests.yml:33-43` records that the queue's coverage is a
   strict superset of the pull-request integration jobs, with one
   documented exception for the libvhdi oracle. After this change
   there is a second exception, and it is bigger. Say exactly what it
   is, where the moved tests run instead, and at what cadence.

7. **Packing is deferred to a decision after step 5**, not planned
   here. See Deferred work.

8. **Consume the testdata dependencies disk** (step 7). Each job that
   prepares testdata clones 7.46 GiB of git-LFS content and checks out
   769,672 files, a median of 82 seconds; that is about 52 GiB per
   merge group. private-ci is adding a dependencies disk, and its
   contract is settled -- see `docs/plans/PLAN-instar-testdata-disk.md`
   in private-ci, section "Contract with instar", which cites the
   measurements above. As agreed with that work:

   - **Requesting it:** add `instar` to `runs-on`, e.g. `[self-hosted,
     debian-13, xl, instar]`. The conductor matches labels as a set,
     so position does not matter. Michael chose a label over a size
     token (`xl-instar`) knowing a plain `[debian-13, xl]` job can
     then take an instar runner; the conductor counts those.
   - **Finding it:** nothing to do. Userdata mounts it before the
     runner registers.
   - **Path:** `/srv/ci/cached/instar-testdata`, writable -- a
     per-runner copy-on-write overlay of a label image, discarded with
     the runner. `prepare-testdata.sh`'s update-in-place branch needs
     no change.
   - **Contents:** a depth-1 clone of main with LFS materialised in
     both the working tree and `.git/lfs/objects`, which is what keeps
     `lfs pull` incremental. Rebuilt nightly, so at most about a day
     stale. Owned by the runner's `debian` user, which is the user the
     jobs run as, so git's `safe.directory` check is satisfied.
     Carries no credential: the build strips the GitLab token from
     `.git/config`, including the `[lfs "https://oauth2:...@..."]`
     section git-lfs writes, so `prepare-testdata.sh`'s existing
     `git remote set-url` with the job's own token is what
     authenticates. The existing fetch, reset and `lfs pull` stay
     exactly as they are.
   - **Already resparsified**, but only `downloaded/` and only
     `*.qcow2`, `*.vmdk`, `*.vhd`, `*.vhdx` and `*.img` -- the image
     build mirrors instar's own step at
     `functional-tests.yml:702-715`, deliberately leaving `custom/`
     fixtures with the allocation their tests expect.

     That mirroring was a silent drift hazard -- an extension added to
     instar's list would not reach the image build, and would show up
     only as slower jobs -- so it is being removed rather than
     documented. Once `tools/ci/resparsify-testdata.sh` is on the
     default branch, private-ci's image build will fetch and run it
     instead of carrying a copy; the build machine has no instar
     checkout, but instar is public, so it fetches over HTTPS and runs
     the script as `debian` against the clone. A fetch failure fails
     the build, which turns a rename or removal into a failed nightly
     with an issue rather than into slower jobs.

     **This makes the script a cross-repository interface, and step 7
     owns keeping it stable:** one positional argument (the tree
     path), no reliance on `GITHUB_*` variables, and a non-zero exit
     on failure. instar's own cache-path needs (resparsify only the
     changed files) must therefore be expressed as *additional*
     optional arguments or a flag, leaving the one-argument call
     meaning "the whole tree" for all time. Until it lands, private-ci
     ships the copied loop.

   Two things instar owes in return, and both are the interesting part
   of step 7 rather than the label:

   - **`TESTDATA_REQUIRE_CACHE=1`.** Their request, and the right one.
     What it catches is a **path mismatch**: the disk mounted somewhere
     other than where we look, or a runner that advertised the label
     without the disk. Without the flag either of those degrades to a
     silent 7.46 GiB clone that merely looks slow -- the same shape as
     the bug this whole plan is about. With it, that is an infra error
     in the idiom `prepare-testdata.sh` already uses for a
     non-materialised LFS tree.

     It does **not** catch a failed mount, and the message must not
     say it does. That session confirmed the sequencing: a mount
     failure makes their userdata print
     `CONDUCTOR-CACHE-DISK-FAILED` and stop *before the runner
     registers*, so the job stays queued and never reaches our script
     at all. A message blaming a mount would send the next person to
     read userdata logs for an event that cannot have happened. The
     assertion therefore names `/srv/ci/cached/instar-testdata`
     literally rather than inheriting the default from
     `TESTDATA_CACHE`, so that a change on either side of the contract
     fails the assertion first.
   - **Do not resparsify the whole tree over the overlay.** Their
     catch, and it would have silently eaten most of the saving: the
     "Resparsify test images" step does `cp --sparse=always` then `mv`
     over every image under `downloaded/`, which on a copy-on-write
     overlay writes those bytes into the overlay. Skipping it on the
     cache path outright is wrong in the other direction, though --
     the image is resparsified at build time, but the objects the
     job's own `lfs pull` fetches are not, so a day of drift would
     accumulate fully allocated. What the cache path needs is the
     changed files and nothing else, which means
     `prepare-testdata.sh` has to report both which branch it took and
     what the update changed. Neither of which it did.

9. **Step 7 must not merge before private-ci's conductor phase is
   deployed.** This is a hard sequencing constraint, not a
   preference. A conductor that does not know the `instar` label
   buckets the job as plain `debian-13`/`xl` and starts runners that
   never advertise `instar`, so GitHub never assigns the job and it
   waits out a 24-hour cancellation. That is strictly worse than the
   ejections this plan exists to fix. The gate is private-ci#144
   merging and soaking for 12 hours, and the `ci-instar-testdata`
   session will say when it has.

   **The gate cleared on 2026-10-16**, and all three of the conditions
   it names were met rather than two of three:

   | Condition | Evidence |
   |---|---|
   | Deployed | private-ci #179 live on `maui`, 2026-10-16 17:22 AEDT |
   | Probed | a job on `[self-hosted, debian-13, s, instar]` assigned and run, 17:26 AEDT |
   | Soaked | 12 hours clear, to 2026-10-16 18:22 UTC |

   Their build side is done too, which matters because the held half
   assumes it: shakenfist/actions #166 and #167 are merged, so the
   nightly disk build now fetches instar's `resparsify-testdata.sh`
   from `develop` and runs it against its own clone, after scrubbing
   the GitLab token out of that clone. The cross-repository interface
   Decision 8 fixed therefore has a live consumer, and the copied loop
   that stood in for it is gone.

   Because it is a sequencing constraint and not a property of the
   code, it gates only the part of step 7 that requests the label.
   Everything else in that step is independent of whether the disk
   exists, so it lands first; the step 7 row carries the split.

   Two amendments that session supplied to the contract in Decision 8,
   recorded here because they change what the held half has to do:

   - **Contract item 1 ("add `instar` to `runs-on`") is verified for
     `debian-13` only.** Any job requesting the label must request
     `debian-13` alongside it. Every testdata consumer here already
     does, so this costs nothing today -- but it means the label is
     not a free addition to a leg on some other image.
   - **`instar` has to be in `.github/actionlint.yaml` before any
     `runs-on` names it.** actionlint rejects an unknown self-hosted
     runner label, and it runs in pre-commit, so without the entry the
     labelled change cannot be committed locally at all. The entry is
     therefore in the landed half: permitting a label changes no job's
     behaviour.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Derived stestr timings (D1). Decide the mechanism by reading stestr's scheduler rather than guessing: `stestr` partitions from timings held in its repository, so the options are a committed timing file loaded into a fresh repository before `stestr run`, or an explicit worker assignment. Implement whichever stestr actually supports without a hand-written test list, in `tools/test-package-functional.sh` around the existing `STESTR_ARGS` construction at line 281, skipping cleanly when the file is absent so a developer checkout still runs. Add a `tools/ci/` self-test in the shape of the existing `tools/ci/test-*.sh`, asserting the timing file is consumed and that a missing file is not an error. Prove the effect by running the suite on this host twice, before and after, and quoting stestr's Worker Balance block both times -- the host run shows the same imbalance (1520 tests on one worker against 510), so it is reproducible in six minutes without CI. Do not change concurrency in this step. |
| 2 | medium | opus | none | The selectors (D3, D4). Add two Makefile targets beside the existing `test-container-core`, `test-container-convert-qcow2` and `test-container-convert-vhd` at `Makefile:681-775`: one for what the merge queue runs and one for what the nightly runs. The nightly's selection is exactly the six tests of `TestConvertVhdToQcow2Roundtrip`, `TestConvertVhdToRaw` and `TestConvertVhdCompare` whose fixtures are the Hyper-V and VirtualPC images -- name them by test id, not by class, because each of those classes also holds sub-second cases that should stay on the gate. The queue's selection is the complement. Thread the queue selector into `tools/ci/run-matrix-entry.sh`; note that `MATRIX_SELECT` is explicitly reserved for local partial runs and must not be reused for this, so add a separate variable and keep `MATRIX_SELECT`'s warning intact. |
| 3 | medium | opus | none | The nightly job (D2). A scheduled cross-distro job over the same seven-distro list, reusing `package-build`'s artifact or rebuilding, running the step-2 nightly selector. Copy the structure of `differential-fuzz.yml` for `schedule:`, `workflow_dispatch:`, `issues: write` and the failure reporting: a red nightly must file or update an issue naming the distro and the test. Publish the timing data step 1 consumes (D1) as part of this job. Decide and record whether it lives in `functional-tests.yml` or its own workflow file -- `needs:` cannot cross workflow files, which is the constraint that matters. |
| 4 | medium | opus | none | The guard (D5). Extend `tools/ci/check-test-partition.py` so its discovered job set includes the merge-queue matrix and the nightly, and so it asserts their selectors together cover everything the matrix covers today. Keep its stated principle intact: the job set is discovered from the workflow and the selectors read from the Makefile, never a second copy. Extend `tools/ci/test_check_test_partition.py` with a case per new failure mode: a test claimed by no queue job and no nightly job; a nightly selector that silently overlaps the queue's; a matrix selector that drops a whole module. Add the guard's invocation to the `ci-tooling` job if the existing invocation does not already cover the new assertions. |
| 5 | medium | opus | none | **Done: dispatched as workflow run 38818648724; median clean leg 16m31s against the 42-minute baseline, and the comparison is posted on #543. See "The step-5 measurement" below for the legs and the two caveats.** Measure (the point of the plan). Dispatch the workflow and record, by the same method as Situation: per-leg step times, the suite's wall time, stestr's Worker Balance block, the matrix stage span and the total. Compare against this run's 42-minute leg, 39:09 suite, 2197/1490/1367/913 worker split, 235-minute matrix stage and 420-minute total. Post the comparison to #543. If the leg does not reach roughly 18 minutes, say so plainly and do not tune the selector to make the number look better -- the selector is a coverage decision, not a dial. |
| 6 | low | sonnet | none | Documentation. Amend the superset argument at `functional-tests.yml:26-53` per D6 -- state the second exception, where the six tests run instead, and at what cadence. Update `docs/testing.md:418-457`, which describes the distro matrix and how to reproduce a CI entry: the entry-level command is unchanged but what the queue selects is not, and the nightly is a new thing to document. Record the measured per-distro suite medians (30 to 38 min, every distro's max above the slowest median) so a later reader can check whether the spread is still narrow. ~~No `CHANGELOG.md` entry: nothing user-visible changes.~~ **Reversed during step 8, on audit advice: there is an entry, under Changed.** The original call read "user-visible" as "visible in the binary", and nothing in the binary changes. But the person this is visible to is a contributor, and what they need is discoverable only from the changelog: a VHD change that passes the merge queue and then turns the nightly red has found a per-distro qemu divergence rather than a flake, and the six tests moving off the gate is why the two can now disagree. Finding that out from a GitHub issue filed overnight, with no record of the change in behaviour, is the failure the entry prevents. |
| 7 | medium | opus | none | **Done, in two commits: the half that does not request the runner label, then the half that does once the gate cleared.** The testdata dependencies disk (D8, D9). *First commit:* the resparsify step out of YAML and into `tools/ci/resparsify-testdata.sh`, with the comment naming private-ci's image build as a cross-repository consumer (D8) and a `tools/ci/test-resparsify-testdata.sh` exercising their calling convention (done in the branch that did steps 1 to 6 and 8 -- do not redo the lift); `prepare-testdata.sh` reporting which branch it took as `TESTDATA_SOURCE`, and on the cache branch the paths the update changed as `TESTDATA_CHANGED_FILES`, because the reset that changed them is the only moment that set can be derived; the narrowing itself as *further optional* arguments to `resparsify-testdata.sh` (`--files-from <list>`, trailing named paths, `--list-extensions`), leaving the one-argument whole-tree call byte-identical for private-ci (D8); `TESTDATA_REQUIRE_CACHE` implemented -- asserting `/srv/ci/cached/instar-testdata` literally, and diagnosing a path mismatch rather than a failed mount, which cannot reach it -- and **set in no workflow**; `tools/ci/test-prepare-testdata.sh`, which stubs git and proves the require-cache refusal with the cache absent, wired into `ci-tooling`; `instar` in `.github/actionlint.yaml`, which has to precede any `runs-on` that names it (D9) and changes no job's behaviour; and the reason `test-drift-fix.yml` stays unlabelled, recorded in that workflow rather than only here, because the reader about to tidy the inconsistency away is looking at the workflow. *Second commit, once the Decision 9 gate cleared:* `instar` on the `runs-on` of the eight testdata consumers that can ever be assigned a disk-bearing runner -- `package-matrix`, `integration-core`, `integration-convert-qcow2`, `integration-convert-vhd` and `oslo-crossval-master` in `functional-tests.yml`, `nightly-legs` and `measure` in `nightly-matrix.yml`, and `coverage-fuzz` in `coverage-fuzz.yml`, all of them already `[self-hosted, debian-13, xl]`, which is what the label is verified against; `TESTDATA_REQUIRE_CACHE: '1'` on each of those eight, in the prepare step's `env` beside the token it already passes; and the narrowing wired up through a new `tools/ci/resparsify-prepared-testdata.sh`, which reads `TESTDATA_SOURCE`/`TESTDATA_CHANGED_FILES` and calls the resparsifier whole-tree for a clone and `--files-from` for a cache. A script rather than a conditional in each step for three reasons: the resparsifier cannot branch on it itself, because it reads no environment variable by cross-repository contract; eight copies of the decision in YAML is eight places to drift with no symptom, which is the same hazard that made the resparsify loop a shared script; and a workflow step cannot be tested, whereas `tools/ci/test-resparsify-prepared-testdata.sh` measures allocated blocks on the files a run was *not* supposed to touch as well as the ones it was, and is wired into `ci-tooling`. All eight resparsify call sites moved to it at first, `test-drift-fix.yml`'s included; review then moved that one back to the one-argument form (`e5066c21`), because that workflow runs from the default branch against a pull request's own scripts, and a branch older than the wrapper would have no wrapper to call. Seven call sites use it, and the reason for the eighth is recorded in that workflow. The contract items this satisfies are the request (item 1), the path (item 3), the require-cache ask (item 5) and the scope (item 6: the pull-request jobs are labelled too, since every job carrying the label gets the disk). *After it merged:* the before/after measurement, taken over all 21 legs of the three merge groups that followed rather than one leg, is under "The step-7 measurement" below and on #543; the `ci-instar-testdata` session has been told the labelled half has landed. *Not labelled, on purpose:* `test-and-fix` in `test-drift-fix.yml`, which runs on the static fleet the conductor only watches, so no runner there will ever advertise `instar` and a labelled job would wait out a 24-hour cancellation (recorded in that workflow, not only here); and `ci-tooling`, which greps like a consumer because it runs `tools/ci/test-prepare-testdata.sh`, but that self-test stubs git and touches no testdata at all. |
| 8 | high | opus | none | Push audit: run `PUSH-AUDIT.md` over `git diff origin/develop...HEAD`. Findings are fixed in this branch before the PR, or declined in writing here. |

Commits: the plan; then one per step. Every commit passes
`pre-commit run --all-files`.

### The step-5 measurement

Dispatched as workflow run 38818648724. The comparison is posted on
#543; this is the record of it.

| Leg | Wall |
|-----|------|
| fastest | 14m07s |
| | 15m03s |
| **median** | **16m31s** |
| | 18m55s |
| slowest | 21m38s |

Five legs ran clean, median 16m31s against the 42-minute baseline in
Situation. That meets the success criterion of "about 18 minutes,
measured", and it is the number the plan was written to produce.

Two things about the run are not the change's doing, and both have to
be said rather than averaged away.

* **Two legs failed, on the load-flakiness this repository has already
  documented**, in `test_convert` re-encode cases. Attributed by
  measurement, not by assumption: the same tests passed in 84 to 138
  seconds on a green leg of the same run and took 484 to 610 seconds on
  the red ones, which is the signature of host contention rather than a
  behavioural difference.
* **The stage span and the total are not comparable to the 235 and 420
  minute baselines.** The first leg waited 1h52m for a runner, so what
  the run measures end to end is the fleet's queueing, not the suite.
  The per-leg wall times above are the figures this step set out to
  move, and they are measured on the legs themselves.

### The step-7 measurement

Taken after #673 merged, from the three merge groups that followed it
(runs 39020749854, 39092387730 and 39105118492): all 21 matrix legs
ran on a disk-bearing runner and reported `testdata source: cache`.
The baseline is the 14 cloning legs of runs 38818648724 (the step-5
dispatch) and 38862171039 (the first merge group after #671). The
comparison is posted on #543.

| `Prepare instar-testdata` | Legs | Median | Range |
|---|---|---|---|
| Fresh clone (before) | 14 | 1m30s | 1m14s - 3m18s |
| Disk current with testdata `main` (18 Oct) | 14 | 1m09s | 0m57s - 1m41s |
| Disk a day behind `main` (17 Oct, 643 paths changed) | 7 | 3m07s | 2m51s - 6m40s |

* **The transfer saving is real; the critical-path saving is about
  21 seconds, not the 80 Decision 8 expected.** A current disk moves
  no LFS content at all. What remains is git walking a 770,000-file
  tree with nothing to do: in one leg, about 19 s of `diff-files` and
  fetch, 1 s of reset, and 37 s of `git lfs ls-files` and an empty
  `git lfs pull`. Leg wall clocks do not show the saving, because the
  suite's spread under host contention (12 to 27 minutes on clean
  legs) is far wider.
* **A day-stale disk is slower than a clone.** On 17 Oct the fetch
  took 20 to 80 s and `git reset --hard` 2 to 4 minutes for 643
  changed paths. Recorded under Deferred work with the hypothesis,
  which is not measured.
* **The index survived the handoff.** The stat-dirty warning fired
  only on 17 Oct, at 59 tracked paths of which 59 were in-scope
  images -- the build's own resparsify, not a lost index, which
  would read near 770,000. The 18 Oct legs report none, which matches
  the `git update-index --refresh` that shakenfist/actions'
  `ansible/ci-instar-testdata.yml` now runs after resparsifying, so
  that ask no longer needs passing on. No leg reported LFS pointers.

### The second push audit

Step 8's audit ran at `2f8d7aaa`; four review-round commits followed
it into #673 unaudited (`7d67c57d`, `3b8989d8`, `17833e2c`,
`e5066c21`). `PUSH-AUDIT.md` was re-run over `2f8d7aaa..e5066c21`
after the merge. It found no code defect -- shellcheck, actionlint and
the four testdata self-tests pass, and the `git lfs ls-files` parse
was checked against a real git-lfs 3.6.1 -- and five documentation
errors, all fixed in the close-out commit:

* Three places still counted eight resparsify call sites or eight
  failing jobs after `e5066c21` moved `test-and-fix` back to the
  one-argument form and left seven (`docs/testing.md`,
  `test-drift-fix.yml`, `resparsify-prepared-testdata.sh`'s header).
* `resparsify-testdata.sh` still said a symlink is judged only by its
  target. Since `17833e2c`'s string filter a relative path must also
  pass on its own name, so a link like `downloaded/a.raw` ->
  `a.qcow2` is dropped from a list and refused when named; the header,
  two inline comments and `docs/testing.md` now say so, and the
  `classify` comment is back above `classify`.
* `test-testdata-changed-files.sh` described two sources for the
  changed-file list after `3b8989d8` added a third, and the step-7 row
  above did not record `e5066c21`'s reversal.

### Review checklist additions

- [x] Every test that runs in the merge-queue matrix today runs
      either in the queue or in the nightly after this change, and the
      guard fails if that stops being true. Proved by the step-4
      self-test, not by reading the selectors. **Checked at close-out:**
      `tools/ci/test_check_test_partition.py` passes its 40 cases, and
      deleting one of the six ids from `test-container-matrix-nightly`'s
      regex makes `tools/ci/check-test-partition.sh` exit 1 naming
      exactly that test; restored, it passes.
- [x] All seven distros still run in both the queue and the nightly.
      A distro dropped during the change is the failure mode with no
      symptom. **Checked:** the guard reports both distro lists agree
      on seven, and nightly run 39080337878 ran a leg for each.
- [x] A red nightly produces an issue. Proved by running the failure
      path, not by reading the step. **Proved by the real thing:**
      the #778 kernel regression described in Status above filed #780
      to #786, one per distro, each naming the distro and its six
      failing tests.
- [x] `MATRIX_SELECT` is still reserved for local partial runs, still
      warns, and is set nowhere in CI. **Checked:** no occurrence under
      `.github/`, and `tools/ci/run-matrix-entry.sh` still prints its
      partial-run note when it is set.
- [x] The six moved tests are named by test id. A class-level selector
      would also move that class's sub-second cases off the gate for
      no gain. **Checked:** `test-container-matrix-nightly`'s regex is
      six literal `test_convert.<Class>.<test>` ids.
- [x] The set of jobs carrying the `instar` label is exactly the set
      of jobs that run `prepare-testdata.sh`, minus the static-fleet
      one that can never be assigned such a runner. Checked in both
      directions by parsing the finished YAML, not by reading the
      diff: a consumer without the label pays for a clone it did not
      need, and a labelled non-consumer holds a scarce disk-bearing
      runner for nothing. `ci-tooling` is the trap -- it greps like a
      consumer and is not one. **Done**, by loading every workflow
      with a YAML parser and comparing the two sets: eight labelled
      jobs, all nine consumers accounted for, both exceptions
      (`ci-tooling`, which tests the script, and `test-and-fix`, which
      cannot be given the disk) matching their documented reason, and
      every labelled job also carrying `debian-13` and
      `TESTDATA_REQUIRE_CACHE: '1'`. This is a one-off check and not a
      standing guard, which review was right to press on; #674 tracks
      making it one, and `docs/testing.md` now says plainly that
      nothing enforces it.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A module or distro silently stops gating; CI goes green faster and nothing fails | D5's guard extension, with a self-test case per failure mode. This is the plan's worst outcome and the only one with no symptom. Owner: step 4. |
| The nightly goes red and nobody notices | D2 makes issue filing part of the job, and the review checklist requires the failure path to be exercised rather than read. Owner: step 3. |
| A distro-specific VHD conversion divergence lands and sits for up to a day | Accepted, and it is the price of D3. Bounded by the tests still running per-PR on debian-13, so only the distro axis is delayed. |
| Step 1 picks a timing mechanism stestr does not really support, and the balance silently does not change | Step 1 proves the effect from stestr's own Worker Balance block on this host, before and after, where the whole suite is six minutes. No CI round trip needed. Owner: step 1. |
| The leg does not reach 18 minutes and the queue is still near the limit | Step 5 reports it either way, and packing is held in Deferred work for exactly this case rather than being spent in advance. |
| Step 7 merges before private-ci's conductor knows the `instar` label, and every labelled job queues 24 hours then cancels | D9 makes this a release gate, not a judgement call: the `ci-instar-testdata` session confirms deployment first. The step is last in the table and the back brief says to land steps 1 to 6 without it rather than hold the pull request. Owner: step 7. **Discharged:** deployed, probed on a live `instar` job and soaked 12 hours before the labelled half was written -- see the table in D9. |
| The resparsify step copies 7.5 GiB into the copy-on-write overlay and gives back most of the disk's saving | D8: on the cache path resparsify only the files the update changed -- not the whole tree, and not nothing either, since the objects the job's own `lfs pull` fetches land fully allocated. That requires `prepare-testdata.sh` to report both which branch it took and what changed, and `resparsify-testdata.sh` to take a narrower request without disturbing its one-argument form. Found by the `ci-instar-testdata` session, not by this plan -- worth noting as evidence that the disk's value is easy to lose silently. Owner: step 7, first commit; wired up in the second. |
| `tools/ci/resparsify-testdata.sh`'s interface changes and breaks private-ci's nightly image build | D8 makes it a named interface with a self-test in their calling convention, and the script says who consumes it. Their side fails loudly rather than degrading, by design. Owner: step 7. |
| Someone later gives `test-drift-fix.yml` the `instar` label for consistency, and it queues 24 hours then cancels | The reason goes in that workflow next to the job, not only in this plan: a reader about to make the change is looking at the workflow. Owner: step 7. |
| Someone later "fixes" the gating by putting the six tests back | D6 records the reason in the workflow comment that already warns readers off re-running everything everywhere, which is where someone about to do this will be looking. |

## Administration and logistics

### Success criteria

* A matrix leg is about 18 minutes, measured, down from 42.
* Every test that gates a merge today either still gates or runs
  nightly on all seven distros, and a guard fails if that stops being
  true.
* A red nightly files an issue naming the distro and the test.
* stestr's Worker Balance on the slowest worker is within about 20%
  of the even split, measured.
* The superset argument in the workflow states its second exception
  accurately.
* `pre-commit run --all-files` passes and `ci-tooling` runs the new
  self-tests.
* The step-5 measurement is on #543, whichever way it came out. #543
  is not closed by this change: the capacity direction is the other
  half.

### Deferred work

* **Pack the matrix legs into three jobs** (D7). Analysis, kept so it
  need not be redone: group by package kind, 2 / 2 / 3 -- `deb-a`
  (Debian 12, Ubuntu 22.04), `deb-b` (Debian 13, Ubuntu 24.04), `rpm`
  (Fedora latest, Rocky 9, Rocky 10) -- which keeps each group
  homogeneous and preserves `run-matrix-entry.sh`'s per-kind log
  naming. Balance by count, not duration: the per-distro medians are
  30 to 38 minutes and every distro's max exceeds the slowest median,
  so a duration fit would be fitting noise. Keep the job key
  `package-matrix`, since `can_merge` at `functional-tests.yml:1335`
  depends on it and the comment there records that this name, not the
  per-entry names, is what branch protection requires. Two
  constraints a packed matrix must meet: every distro in a group runs
  whatever the earlier ones did, or a seven-distro signal collapses
  to "first failure wins"; and the per-distro `allow_failure`
  quarantine must move out of job-level `continue-on-error` into the
  group runner, because a job-level flag would disable a whole
  group's gate -- which is the sharp edge the comment at
  `functional-tests.yml:632-641` already warns about.
* **A standing guard on the `instar` label set** (#674). The
  correspondence between labelled jobs and testdata consumers is
  checked by hand; both directions of drift are silent, and the worst
  case -- the label on the static-fleet job -- queues for 24 hours
  before GitHub cancels it. Deferred rather than written here because
  the right shape is one PyYAML reader serving this and
  `check-test-partition.py`'s existing hand-rolled scan, which means
  adding PyYAML to that script's venv; a third hand-rolled scan would
  be the wrong direction. The checks themselves are written out in the
  issue.
* **Refresh the index on the runner rather than rewriting** (#675).
  `prepare-testdata.sh` unions the stat-dirty set into the
  changed-file list so the narrow resparsify shrinks what the reset
  re-expanded, which is correct but pays twice: the reset writes each
  file into the overlay and the resparsify writes it again. A
  `git update-index -q --refresh` before the fetch would re-hash those
  entries instead, so the reset leaves them alone. Deferred because
  the trade is unmeasured -- re-hashing goes through the LFS clean
  filter, so the worst case is hashing 7.46 GiB rather than writing
  it, and which is cheaper depends on the runner. The union stays
  either way as the backstop.
  The step-7 measurement makes this rarer than it looked: the disk
  build now refreshes its own index, and every leg on a disk built
  that way reported no stat-dirty paths. The trade only comes back if
  that build step regresses.
* **Make a stale disk no slower than a clone** (#790).
  * *What was measured.* On a disk a day behind testdata `main`,
    `git reset --hard` takes 2 to 4 minutes for 643 changed paths,
    and the prepare step 3m07s against a clone's 1m30s.
  * *Why the disk is stale.* Testdata `main` moves twice a day, both
    times from `coverage-fuzz.yml`: a one-file "Coverage fuzz run
    started" commit at the start of the run, which is its write
    check, and the corpus push at the end, which is the change that
    costs. The run's 04:00 UTC cron fires late (created at 08:11 to
    11:35 UTC over 11 to 18 Oct) and runs about eight hours, so the
    corpus push lands at about 16:25 to 17:10 UTC. The disk is
    rebuilt last in private-ci's nightly image cycle, which starts at
    13:11 UTC and reaches it at about 15:05 UTC (per the
    `ci-instar-testdata` session, from `conductor/imagebuilder.py`),
    so every corpus push misses it: a day's corpus is missing from
    the disk for about 22 hours. The 643 paths of 17 Oct were most
    likely the previous day's 16:25 UTC push of 583 corpus entries
    plus the OVA corpus.
  * *The window: private-ci's to close, agreed and not yet built.*
    As of 18 Oct (UTC) the chosen lever, relayed by the `ci-instar-testdata`
    session, is private-ci's `PLAN-instar-testdata-disk.md` phase 5,
    which is to rebuild the testdata disk soon after testdata `main`
    gains a commit rather than only nightly. That phase was not
    started then, so until its Execution row reads Complete, expect
    the window above. Moving coverage-fuzz's schedule is not a fix
    and is not to be done: the disk is behind from whenever the
    commit lands until the next build, so an earlier commit only
    moves the window.
  * *On this side, tracked in #790.* First, the skip-smudge reset.
    The hypothesis, not measured: the reset smudges each new LFS
    object individually where `git lfs pull` fetches a batch, so
    `GIT_LFS_SKIP_SMUDGE=1` on the reset and the existing pull after
    it would remove most of the cost. The pointer capture before the
    pull already lists what that would leave as pointers, so the
    narrow resparsify would still cover them;
    `tools/ci/test-testdata-changed-files.sh` is where to prove it.
    It is still worth having once private-ci rebuilds on change,
    because a disk is still briefly behind between a commit and its
    rebuild. Second, asked for by that session: make the start-of-run
    write check a non-committing one (a `git push --dry-run`, or a
    push to a scratch ref), so testdata `main` changes once a day
    rather than twice and private-ci is not rebuilding the disk for
    one timestamp file.
* **Raise `--concurrency` above 4.** Untested and plausibly the
  cheapest remaining win once the six tests are gone, but the suite
  boots a KVM guest per test and `docs/testing.md:434,459` warns that
  co-tenant matrix containers already contend for one KVM host.
  Needs measuring on an `xl` runner, not reasoning about.
* **Narrow the rest of `test_convert`.** It is 5045 of 5967 seconds,
  and a second tranche of twelve manifest and re-encode tests is
  about 1300 of them. The same argument as D3 applies with less
  force; revisit only if step 5 says the path is still too long.
* **Co-scheduling, which is probably better than capacity.** The
  other direction recorded in #543 is more `xl` capacity. A cheaper
  shape emerged while this plan was being written: private-ci is
  adding the testdata dependencies disk above, requested by a label on
  the job, which incidentally gives the conductor a way to *recognise*
  instar's merge-queue jobs.

  That matters because the post-#66 problem is not unfairness, it is
  that a merge group's seven `xl` jobs share one 360-minute deadline
  the fleet has no notion of. They are served fairly and spread over
  hours: a 121-minute median wait, 188 at p90, and two legs in #652's
  run never got a runner inside the window. Seven simultaneous slots
  is an implausible reservation; "these N jobs belong to one deadline,
  serve them together" is not. If this plan's steps land first, the
  ask shrinks to three jobs of about 18 minutes, which is a much
  easier thing to co-schedule than seven of 42.

  Ownership has moved: the `ci-instar-testdata` session has put this in
  private-ci's Future work as admission work for its claim-coverage
  plan, rather than in the disk plan. It is tracked there, not here.

  One hazard to carry into that conversation: private-ci#66 records
  the *label* dimension as the original starvation axis, which stopped
  mattering only when retiring the Debian 12 images made `debian-13`
  first in `CI_IMAGES`; the size half was what the oldest-first fix
  addressed. A fourth label on the matrix jobs is a new dimension for
  the provisioner to walk, so the fix needs to order across labels and
  not only sizes. A label pool with no runners also queues jobs
  silently rather than failing, which on a merge gate looks exactly
  like this issue.
* **Run the docs-only path filter in the merge queue too.** The
  `check_paths` job runs `dorny/paths-filter` only
  `if: github.event_name == 'pull_request'`
  (`.github/workflows/functional-tests.yml:74,78`), and everything else
  falls back to `code_changed=true`. The comment at `:61-65` records
  that as deliberate -- "the safe direction, and cheap to revisit if it
  proves annoying" -- so this is taking up the invitation, not fixing an
  oversight.

  It is annoying now, and measurably. #659 was documentation only: the
  `pull_request` run skipped `build-and-test` and the whole matrix, and
  the merge group then ran all of it, spending the full fan-out
  verifying code the change did not touch. That is not merely wasted
  `xl` time, it is the 360-minute deadline being spent on a change that
  cannot fail the suite -- the exact exposure this plan exists to
  shorten, arriving from a direction none of the five steps covers.

  The shape: give the filter step a `base` of
  `github.event.merge_group.base_sha` on a `merge_group` event so it
  diffs the group against what the group is being merged into. That is
  the right granularity rather than a convenient one -- a merge group
  can hold several pull requests, and `base_sha..head_sha` computes
  "docs only" over the whole group, which is what the gate needs to be
  true of. Verify the action's input names before writing it; this is
  from its documentation, not from a run.

  Two things to check rather than assume. The aggregate must still
  report success with the matrix skipped -- the `pull_request` path
  already proves it does for a docs-only change, and `can_merge` at
  `functional-tests.yml:1335` depends on the `package-matrix` job key
  rather than the per-entry names, so the skip shape is the one already
  in use. And confirm a label pool with no runners is not how a
  mis-specified filter fails, because that queues silently rather than
  erroring, which on a merge gate is indistinguishable from this issue.

  Independent of steps 1 to 5 and of private-ci#144: it changes no leg
  durations and needs no fleet behaviour, so it can land whenever. It is
  worth noting that it does nothing for a code change, which is every
  merge group this plan's measurements came from -- it removes a class
  of run rather than shortening the path.
* **Cancel an ejected merge group's run once its PR is re-queued.**
  Run 38206699440 held `xl` runners for 58 minutes past the ejection
  for a result nothing could use. Belongs to whatever watches the
  queue; recorded on #543.
* **The front of the path.** The two `s` jobs went from a 3-minute
  median wait to 11, and `build-and-test` waited 99 minutes in #652's
  run. `package-build` needs `build-and-test` only for its
  devcontainer image; whether that dependency is real is worth
  checking separately.
* **The libvhdi oracle is not required on the matrix path, and this
  plan does not change that.** `INSTAR_REQUIRE_LIBVHDI: '1'` is set
  only for `integration-core` (`functional-tests.yml:865`), which runs
  `make test-container-core` on a pull request. The matrix reaches the
  suite through `tools/ci/run-matrix-entry.sh` into
  `tools/test-package-functional.sh`, and neither sets or forwards the
  variable, so every libvhdi cross-check on a merge-queue leg skips
  silently and the leg still reports zero failures. Found while
  checking what the step 2 selectors keep on the gate; left alone
  deliberately, because requiring the oracle changes *what* is tested
  and this plan's mission is to change only *when*. It is the same
  shape as the defect this plan exists to fix, so it is worth its own
  issue rather than a footnote.
* **The length floor is a lower bound, not a count.** Step 2's fix
  derives it from `stestr list`, which enumerates only statically
  discoverable ids: measured on this tree, a listing of the suite
  minus `test_info_malicious` gives 2378 against a run's 3675, because
  roughly 1300 cases are built at load time. The floor therefore
  catches gross truncation and not a partial loss. The nightly from
  step 3 is the one job that executes the whole set, so it can publish
  the true executed count alongside the timing data and give the floor
  a tight bound; that is the right home for it and it is not done
  here.
**Declined from the push audit, in writing.** Two findings were real
and not taken. `tools/ci/check-test-partition.py` has grown from 301 to
over 700 lines and has a natural seam -- the Makefile and workflow
readers could be a shared library under two check entry points -- but
that is a refactor rather than a defect, and this branch's hardening is
already larger than the change it hardens. The two stestr self-tests
duplicate a venv build and a duration helper verbatim; the duplication
is in test scaffolding, costs a reader nothing and risks no behaviour.
Both are better done by whoever next needs to touch those files.

**Declined from the automated review rounds, in writing.** Two further
findings were real and not taken.

* **The publishing job runs on a self-hosted runner with persisted
  credentials.** Accepted rather than fixed. It is the only checkout in
  the workflow that keeps its job token, and that is the point of
  splitting it off the measuring leg: the job runs no tests, no
  containers and no testdata, it downloads one derived text file and
  pushes a branch, and these runners are provisioned per job. The
  exposure the split was built to remove -- a write token sitting in a
  tree while instar and `qemu-img` chew through 7.5 GiB of malicious
  images for two hours -- is gone either way.
* **The partition guard's YAML parsing is a hand-rolled text scan.**
  True, and it has already bitten once: it discovered a prose comment
  as a CI job, recorded under "Bugs fixed during this work" above.
  Replacing the scan with a real parser is the right follow-up, and it
  is not a late change to a branch whose hardening already exceeds the
  change it hardens -- the same call as the refactor declined above,
  and for the same reason.

### Bugs fixed during this work

* **A failed resparsify replaced a good image with a truncated one,
  and exited zero.** Found by comparing the eight inline copies of the
  step before replacing them with a shared script. Six of the eight ran
  `cp --sparse=always` and `mv` as two unconditional statements, so a
  copy that failed part way -- ENOSPC being the realistic case on a
  7.5 GiB tree -- was followed by an `mv` that moved the partial file
  over the original. The step then exited zero, because `mv` succeeded.
  The two newest copies had `cp && mv` and were correct. No copy
  propagated failure at all: `find -exec` does not fail on a non-zero
  child, and a `while` loop reports only its last command. The shared
  script takes the `&&`, adds the failure propagation neither form had,
  and its self-test asserts that an image survives a failed copy.
* **The partition guard was discovering a prose comment as a CI job**,
  and the comment was one this branch added two commits earlier. The
  timing-seed explanation in `functional-tests.yml` contains the words
  `"stestr run"`, which the guard's job discovery matched as an inline
  invocation; it then read the following prose as that job's selectors,
  giving it the bare words `so`, `already`, `recorded` and `in`. Those
  match a large slice of the suite, so the guard would have reported a
  genuinely orphaned test as claimed. Discovery now ignores matches
  inside YAML comments, with a regression test whose fixture carries
  the same shape of prose. Worth recording for how it was found: not by
  reading the guard, but by extending it and watching what it claimed
  to see.
* **The narrow resparsify skipped images the update's reset had just
  re-expanded.** Found by review of the pull request, and nearly
  dismissed: the first measurement said `git reset --hard` leaves a
  stat-dirty entry alone, and it was wrong because it used the inode
  as the signal. Git overwrites an existing path in place, so the
  inode survives a rewrite and reports every file as untouched. With
  mtime as the signal and an unchanged, clean-stat control beside it,
  the opposite shows: the reset *does* rewrite an entry whose content
  is identical but whose index stat data is stale, and the disk build
  leaves exactly that state behind because resparsifying is a `cp` and
  a `mv`. Under LFS those files return fully allocated, and no diff of
  the two trees names them, so the narrow list missed them and the
  disk quietly gave back part of its saving. `prepare-testdata.sh` now
  unions the stat-dirty set into the list -- which costs nothing, the
  reset having already written them into the overlay -- and warns,
  because the real fix is a `git update-index --refresh` on the side
  that builds the disk. `tools/ci/test-testdata-changed-files.sh`
  pins all of it against a real git, including that the tree diff
  alone would have missed it.
* **A new test passed on this machine and had never once passed in
  CI.** `tools/ci/test-testdata-changed-files.sh` builds local
  repositories with `git init`, and this developer has
  `init.defaultBranch=main` set where the runners leave it unset. On a
  runner the bare repository's HEAD therefore pointed at an unborn
  `master` while the push created `main`, and `git clone` reports that
  as "you appear to have cloned an empty repository" -- a warning, not
  an error -- so the script carried on and failed three assertions
  later with a confusing `find: no such directory`. Three rounds of
  review were reported with "gates green" on the strength of a local
  run; `ci-tooling` had been red since the commit that added the file,
  and the steps after it never ran at all. The test now neutralises
  `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM`, pins every branch name
  explicitly, and checks the clone produced a tree at the point it
  clones rather than letting the failure surface downstream. The
  general lesson is narrower than "run CI": a test whose subject is a
  tool's behaviour must not inherit that tool's ambient
  configuration, because the developer's configuration is part of
  what it is measuring.
* Otherwise none directly. #543 is not closed by this change.

### Back brief

Two gates, both cheap to agree and expensive to redo:

1. Before step 2 writes a selector, state the exact test ids moving
   to the nightly and wait for agreement. Everything downstream --
   the nightly job, the guard, the documented exception -- is built on
   that list.
2. Before step 3 commits to a workflow file, state whether the
   nightly lives in `functional-tests.yml` or its own file, and why.

Step 7 carries a third gate of a different kind: it is blocked on
another team's interface, not on agreement here. If the Open questions
are still unanswered when steps 1 to 6 are done, land those and take
step 7 as a follow-up rather than holding the pull request -- the
suite-side work is what closes the ejections, and the disk is worth 80
seconds of it.
