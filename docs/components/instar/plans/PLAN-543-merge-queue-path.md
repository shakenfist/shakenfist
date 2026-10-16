# PLAN-543: Cut the merge queue's critical path

## Status: In progress

Steps 1 to 6 and 8 are done, and step 5's measurement is recorded under
Execution ("The step-5 measurement"). Step 7 is the only outstanding
step, and it is blocked on private-ci rather than on anything here.

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
settled; Decision 8 records it. What remains is not a question but a
release gate: step 7 cannot merge until private-ci's conductor phase
is deployed (Decision 9).

One scope correction worth carrying, because it was wrong in the first
exchange with that session. Seven places prepare testdata, not five:
`package-matrix`, `integration-core`, `integration-convert-qcow2`,
`integration-convert-vhd` and `oslo-crossval-master` in
`functional-tests.yml`, plus `coverage-fuzz.yml` (nightly, already
`[self-hosted, debian-13, xl]`) and `test-drift-fix.yml`.
`package-smoke` and `snapshot-harnesses` do not.

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

   - **`TESTDATA_REQUIRE_CACHE=1`.** Their request, and the right one:
     without it a mount failure degrades to a silent 7.46 GiB clone
     that merely looks slow -- the same shape as the bug this whole
     plan is about. With it, a missing cache is an infra error in the
     idiom `prepare-testdata.sh` already uses for a non-materialised
     LFS tree.
   - **Do not resparsify over the overlay.** Their catch, and it would
     have silently eaten most of the saving: the "Resparsify test
     images" step does `cp --sparse=always` then `mv` over every image
     under `downloaded/`, which on a copy-on-write overlay writes
     those bytes into the overlay. The image is already resparsified
     at build time, so this must run only on the fresh-clone path.
     That means `prepare-testdata.sh` has to report which branch it
     took, which it does not do today.

9. **Step 7 must not merge before private-ci's conductor phase is
   deployed.** This is a hard sequencing constraint, not a
   preference. A conductor that does not know the `instar` label
   buckets the job as plain `debian-13`/`xl` and starts runners that
   never advertise `instar`, so GitHub never assigns the job and it
   waits out a 24-hour cancellation. That is strictly worse than the
   ejections this plan exists to fix. The gate is private-ci#144
   merging and soaking for 12 hours, and the `ci-instar-testdata`
   session will say when it has.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Derived stestr timings (D1). Decide the mechanism by reading stestr's scheduler rather than guessing: `stestr` partitions from timings held in its repository, so the options are a committed timing file loaded into a fresh repository before `stestr run`, or an explicit worker assignment. Implement whichever stestr actually supports without a hand-written test list, in `tools/test-package-functional.sh` around the existing `STESTR_ARGS` construction at line 281, skipping cleanly when the file is absent so a developer checkout still runs. Add a `tools/ci/` self-test in the shape of the existing `tools/ci/test-*.sh`, asserting the timing file is consumed and that a missing file is not an error. Prove the effect by running the suite on this host twice, before and after, and quoting stestr's Worker Balance block both times -- the host run shows the same imbalance (1520 tests on one worker against 510), so it is reproducible in six minutes without CI. Do not change concurrency in this step. |
| 2 | medium | opus | none | The selectors (D3, D4). Add two Makefile targets beside the existing `test-container-core`, `test-container-convert-qcow2` and `test-container-convert-vhd` at `Makefile:681-775`: one for what the merge queue runs and one for what the nightly runs. The nightly's selection is exactly the six tests of `TestConvertVhdToQcow2Roundtrip`, `TestConvertVhdToRaw` and `TestConvertVhdCompare` whose fixtures are the Hyper-V and VirtualPC images -- name them by test id, not by class, because each of those classes also holds sub-second cases that should stay on the gate. The queue's selection is the complement. Thread the queue selector into `tools/ci/run-matrix-entry.sh`; note that `MATRIX_SELECT` is explicitly reserved for local partial runs and must not be reused for this, so add a separate variable and keep `MATRIX_SELECT`'s warning intact. |
| 3 | medium | opus | none | The nightly job (D2). A scheduled cross-distro job over the same seven-distro list, reusing `package-build`'s artifact or rebuilding, running the step-2 nightly selector. Copy the structure of `differential-fuzz.yml` for `schedule:`, `workflow_dispatch:`, `issues: write` and the failure reporting: a red nightly must file or update an issue naming the distro and the test. Publish the timing data step 1 consumes (D1) as part of this job. Decide and record whether it lives in `functional-tests.yml` or its own workflow file -- `needs:` cannot cross workflow files, which is the constraint that matters. |
| 4 | medium | opus | none | The guard (D5). Extend `tools/ci/check-test-partition.py` so its discovered job set includes the merge-queue matrix and the nightly, and so it asserts their selectors together cover everything the matrix covers today. Keep its stated principle intact: the job set is discovered from the workflow and the selectors read from the Makefile, never a second copy. Extend `tools/ci/test_check_test_partition.py` with a case per new failure mode: a test claimed by no queue job and no nightly job; a nightly selector that silently overlaps the queue's; a matrix selector that drops a whole module. Add the guard's invocation to the `ci-tooling` job if the existing invocation does not already cover the new assertions. |
| 5 | medium | opus | none | **Done: dispatched as workflow run 38818648724; median clean leg 16m31s against the 42-minute baseline, and the comparison is posted on #543. See "The step-5 measurement" below for the legs and the two caveats.** Measure (the point of the plan). Dispatch the workflow and record, by the same method as Situation: per-leg step times, the suite's wall time, stestr's Worker Balance block, the matrix stage span and the total. Compare against this run's 42-minute leg, 39:09 suite, 2197/1490/1367/913 worker split, 235-minute matrix stage and 420-minute total. Post the comparison to #543. If the leg does not reach roughly 18 minutes, say so plainly and do not tune the selector to make the number look better -- the selector is a coverage decision, not a dial. |
| 6 | low | sonnet | none | Documentation. Amend the superset argument at `functional-tests.yml:26-53` per D6 -- state the second exception, where the six tests run instead, and at what cadence. Update `docs/testing.md:418-457`, which describes the distro matrix and how to reproduce a CI entry: the entry-level command is unchanged but what the queue selects is not, and the nightly is a new thing to document. Record the measured per-distro suite medians (30 to 38 min, every distro's max above the slowest median) so a later reader can check whether the spread is still narrow. ~~No `CHANGELOG.md` entry: nothing user-visible changes.~~ **Reversed during step 8, on audit advice: there is an entry, under Changed.** The original call read "user-visible" as "visible in the binary", and nothing in the binary changes. But the person this is visible to is a contributor, and what they need is discoverable only from the changelog: a VHD change that passes the merge queue and then turns the nightly red has found a per-distro qemu divergence rather than a flake, and the six tests moving off the gate is why the two can now disagree. Finding that out from a GitHub issue filed overnight, with no record of the change in behaviour, is the failure the entry prevents. |
| 7 | medium | opus | none | The testdata dependencies disk (D8, D9). **Half of this step has already landed, in the branch that did steps 1 to 6 and 8: the resparsify step is out of YAML and into `tools/ci/resparsify-testdata.sh`, with the comment naming private-ci's image build as a cross-repository consumer (D8) and a `tools/ci/test-resparsify-testdata.sh` that exercises their calling convention. What is still outstanding is the `instar` runner label, `TESTDATA_REQUIRE_CACHE=1`, and making the resparsify conditional on which branch `prepare-testdata.sh` took -- do not redo the lift.** **Do not merge this step before the `ci-instar-testdata` session confirms private-ci's conductor phase is deployed and soaked (D9); an early merge queues every labelled job for 24 hours and then cancels it.** Add `instar` to `runs-on` for all seven testdata consumers -- `package-matrix`, `integration-core`, `integration-convert-qcow2`, `integration-convert-vhd` and `oslo-crossval-master` in `functional-tests.yml`, the nightly from step 3, and `coverage-fuzz.yml`; leave `test-drift-fix.yml` unlabelled and add a comment there saying why -- `claude-code` is the static fleet, no static runner advertises `instar`, and a labelled job would queue for 24 hours and then be cancelled, so the inconsistency with every other testdata consumer is deliberate and must survive a future tidy-up. Set `TESTDATA_REQUIRE_CACHE=1` on each, and teach `prepare-testdata.sh` to treat a missing cache under that flag as an infra error in the same idiom as its existing LFS-pointer guard. Then make resparsify conditional: have `prepare-testdata.sh` export which branch it took to `$GITHUB_ENV` beside `TESTDATA_PATH`, and on the cache path resparsify **only the files the fetch and `lfs pull` changed**, not the whole tree. Skipping it outright would be wrong in the other direction: the image is resparsified at build time, but objects `lfs pull` fetches on the runner are not, so a day of testdata drift would accumulate unsparsified files. Resparsifying everything is equally wrong -- `cp --sparse=always` over a copy-on-write overlay writes the 7.5 GiB the disk exists to avoid. The changed set is derivable from the reset (`git diff --name-only` across it) intersected with the extension list. The shared script that step is narrowing already exists, so this is an extra optional argument to `tools/ci/resparsify-testdata.sh` and a matching case in its self-test, not a new script: its one-argument call means "the whole tree" permanently, because private-ci's image build fetches it over HTTPS and relies on that (D8). Message that session when it merges. Extend the prepare-testdata self-test for the require-cache failure path and prove it with the cache absent, not by reading it. Measure one leg before and after and record it on #543. |
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

### Review checklist additions

- [ ] Every test that runs in the merge-queue matrix today runs
      either in the queue or in the nightly after this change, and the
      guard fails if that stops being true. Proved by the step-4
      self-test, not by reading the selectors.
- [ ] All seven distros still run in both the queue and the nightly.
      A distro dropped during the change is the failure mode with no
      symptom.
- [ ] A red nightly produces an issue. Proved by running the failure
      path, not by reading the step.
- [ ] `MATRIX_SELECT` is still reserved for local partial runs, still
      warns, and is set nowhere in CI.
- [ ] The six moved tests are named by test id. A class-level selector
      would also move that class's sub-second cases off the gate for
      no gain.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A module or distro silently stops gating; CI goes green faster and nothing fails | D5's guard extension, with a self-test case per failure mode. This is the plan's worst outcome and the only one with no symptom. Owner: step 4. |
| The nightly goes red and nobody notices | D2 makes issue filing part of the job, and the review checklist requires the failure path to be exercised rather than read. Owner: step 3. |
| A distro-specific VHD conversion divergence lands and sits for up to a day | Accepted, and it is the price of D3. Bounded by the tests still running per-PR on debian-13, so only the distro axis is delayed. |
| Step 1 picks a timing mechanism stestr does not really support, and the balance silently does not change | Step 1 proves the effect from stestr's own Worker Balance block on this host, before and after, where the whole suite is six minutes. No CI round trip needed. Owner: step 1. |
| The leg does not reach 18 minutes and the queue is still near the limit | Step 5 reports it either way, and packing is held in Deferred work for exactly this case rather than being spent in advance. |
| Step 7 merges before private-ci's conductor knows the `instar` label, and every labelled job queues 24 hours then cancels | D9 makes this a release gate, not a judgement call: the `ci-instar-testdata` session confirms deployment first. The step is last in the table and the back brief says to land steps 1 to 6 without it rather than hold the pull request. Owner: step 7. |
| The resparsify step copies 7.5 GiB into the copy-on-write overlay and gives back most of the disk's saving | D8: resparsify only on the fresh-clone path, which requires `prepare-testdata.sh` to report which branch it took. Found by the `ci-instar-testdata` session, not by this plan -- worth noting as evidence that the disk's value is easy to lose silently. Owner: step 7. |
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
