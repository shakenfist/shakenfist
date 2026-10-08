# Plan: the image supply chain, from end-of-life migration to production that reports its own failures

## Prompt

Written 2026-09-13, consolidating work that had spread across three
concurrent sessions and nine open issues in four repositories. Two
of those sessions have been closed; this plan is the single
statement of what is outstanding and in what order.

Two threads run through every issue here and they are not the same
problem. The first is a migration: Debian 12 reached end of
standard support on 2026-06-10 and the fleet still names it in 80
places. That is finite work with an end. The second is that the
machinery producing our base images failed five separate times in
one week without telling anyone, and was found only because
somebody went looking for something else. That has no end unless
the machinery changes.

Read `docs/audits/eol-distro.md` for what the criterion measures
and, importantly, what it cannot see.

## Situation

### The open work, as filed

| Issue | Repository | What |
|-------|------------|------|
| [#123](https://github.com/shakenfist/development/issues/123) | development | Debian 12 runner labels and container bases, fleet wide |
| [#38](https://github.com/shakenfist/private-ci/issues/38) | private-ci | Collated inventory of obsolete base image usage |
| [#39](https://github.com/shakenfist/private-ci/issues/39) | private-ci | Dependencies cache disk still built from `debian:11` |
| [#40](https://github.com/shakenfist/private-ci/issues/40) | private-ci | Retire the unused `debian-11` runner image and label |
| [#44](https://github.com/shakenfist/private-ci/issues/44) | private-ci | A conductor restart silently skips that day's nightly rebuild |
| [#45](https://github.com/shakenfist/private-ci/issues/45) | private-ci | No `debian-gnome-13`; the last bookworm label with no successor |
| [#826](https://github.com/Mach33Labs/33fl/issues/826) | 33fl | Two GitLab static runners stay on bookworm until deleted by hand |
| Phases 1-6 | images | `PLAN-image-build-modernisation.md`; see its own Execution table for what has landed |

Already landed and not repeated below: development#119 (the
`eol-distro` criterion), actions#66 (`debian-13-docker` published a
daemon with no client), 33fl#820 (static runners now *built* on
Debian 13), images#2 (the build fixes) and images#3 (that
repository's own plan).

### Five silent failures in one week

Each of these was found by hand, and none of them raised an alert:

1. **shakenfist/images published nothing for sixteen days.**
   `build.sh` is `#!/bin/bash -e` run from cron; one failing image
   aborted the run and cron mailed root, which nobody reads.
2. **`debian-docker:12`, `debian-gnome:12` and `debian-xfce:12`
   were Debian 11 for two years and two months.** They passed
   `DIB_RELEASE=bullseye` while naming `debian-12-extras`, from
   2024-07-06 until 2026-09-12. Nothing checked that the image
   matched its own name.
3. **`ci-images/debian-13-docker` published a working daemon with
   no `docker` binary.** Debian 13 split `docker.io`, and the CI
   images are built with recommends disabled. Fixed in actions#66
   by making the build run `docker version` and fail.
4. **private-ci's nightly rebuild did not fire on 2026-09-12** and
   nothing said so. A day on which the loop neither builds nor
   errors is indistinguishable from a day with nothing to do.
5. **`debian-11` reports `False` in every nightly cycle result**
   and has done since it stopped being buildable. The cycle
   summary carries a permanent failure that nobody reads.

The shape is identical every time: something that produces images
stopped producing correct images, and no signal existed. Four of
the five were found in the same week only because one investigation
led to another.

There is a measured cost to the staleness, beyond the missing
images. While `debian:13` was frozen for those sixteen days, every
CI image built from it apt-upgraded a fortnight of packages during
the build and carried the superseded versions into the published
blob. Measured on the plain `debian-13` label, where nothing
changed but the freshness of the base:

| Version | Size |
|---------|------|
| v61 (stale base) | 1320.4 MB |
| v62 (refreshed base) | 1146.3 MB |

174.1 MB, 13.2%. The same comparison on `debian-13-docker` is
quoted as 15% on #44, but that pair conflates the base refresh
with actions#66's own fix; the plain label above is the clean
measurement.

### The structural finding

The `eol-distro` criterion greps workflows for runner labels and
container images. That makes it a check on **consumers**. Every
place the fleet actually *produces* an end-of-life image is
invisible to it:

* **shakenfist/images** decides which releases get built at all.
  It sits on the audit's excluded list, whose stated reasons are
  internal tooling, historical archives and non-projects; none of
  the three fits a repository built from nightly that the fleet's
  CI depends on.
* **private-ci** decides which images become runner labels, in
  `IMAGE_BUILDS` and `CI_IMAGES`. It is in scope for four plan
  criteria and `sfui-vendor`, and nothing else.
* **33fl's static runners** advertise only `self-hosted` and
  `static`, so no workflow anywhere names an operating system. A
  grep of every consuming repository finds nothing while every one
  of those jobs runs on a retired release.

So the three definitions that create the exposure sit outside the
criterion that bans it, and the 80 findings in #123 are the
downstream shadow of decisions the audit cannot read. Fixing the
80 without fixing the three means the count returns at the next
end-of-life date.

## Mission and problem statement

Close the nine open issues in a sequence that does not break CI on
the way through, and change the image pipeline so that the next
failure announces itself instead of waiting to be noticed.

Not in scope: what goes *inside* the images, the DIB patches
carried in shakenfist/images, and whether the fleet should consume
these images at all. 33fl is a different organisation with its own
conventions, so this plan tracks its one issue and does not
prescribe how it is fixed.

## Open questions

### Q1. One criterion that reads producers, or a second criterion?

Phase 6 has to measure the producer definitions that `eol-distro`
cannot see. Either `eol-distro` grows the ability to read
`IMAGE_BUILDS`, `CI_IMAGES` and a build list, or a second
criterion does it.

**Default if nobody answers: a separate criterion.** The two
report different defects -- one says "this repository names a
banned label", the other says "this repository *offers* one" --
and they are fixed by different people. More decisively, an issue
title is the fleet-wide idempotency key for filing and closing,
and `scripts/tests/test_metadata.py` freezes those titles
precisely because changing one orphans every issue already open
under the old title. Widening `eol-distro`'s meaning changes what
its existing title claims; a new criterion carries a new title and
orphans nothing.

## Decisions

### D1. Signal before surgery

Every later phase changes something that produces images, and the
whole reason this plan exists is that we cannot currently tell when
image production breaks. Making those changes first and the
detection last would be running the same experiment that produced
the sixteen-day outage.

So detection comes first, even though it closes no migration issue
and resolves nothing on #123. Phases 1 and 2 are also the only
phases that address "so they don't occur again"; the rest is
cleanup that a future end-of-life date will otherwise recreate.

### D2. Verify the artifact, not the name

Three of the five silent failures were a published artifact that
did not match its own label: bullseye as `debian:12`, a docker
image with no docker, a runner label whose base no longer builds.
A freshness check catches none of these -- all three were current,
and two were being rebuilt nightly.

So the plan carries a separate phase that asserts what an image
*is* rather than when it was made. actions#66 already established
the pattern by ending the build with `docker version`; this
generalises it.

**Operative reading, added 2026-09-14.** Implementing phase 2
showed that "verify the artifact, not the name" taken literally is
the wrong way round: comparing the artifact against the build's own
inputs is exactly what would have passed every night for two years.
The reading that works is **verify the artifact against the name it
will be published under**, and the name is held by whatever
publishes rather than whatever builds. The correction in phase 2
sets out why. Anything else applying D2 -- the private-ci bullet in
that phase, and any future producer -- takes this reading.

### D3. `debian-gnome-13` before the consumer sweep

private-ci#45 is the only issue on the critical path of #123.
`debian-gnome-12` is the last bookworm label with no successor, so
any repository whose workflows name it cannot be migrated until the
successor label exists. Everything else in #123 is a swap between
labels that both already work.

### D4. Retire producers last, and only after their consumers

private-ci#40's follow-up retires `debian-12` and
`debian-12-docker` from `IMAGE_BUILDS` and `CI_IMAGES`. Doing that
before #123 completes takes the runners out from under jobs that
still request them. The `debian-11` half of #40 has no such
constraint -- nothing requests it -- so it can go early.

### D5. This plan does not re-plan shakenfist/images

That repository has its own plan, merged in images#3, with seven
phases of its own. Its phase 1 (per-image failure isolation) and
phase 4 (the freshness watchdog) are load-bearing here, so they are
named in the phase table below, but the detail stays there rather
than being copied.

### D6. 33fl gets no detection here

The Mission says 33fl is a different organisation with its own
conventions, and that applies to detection as much as to fixes.
Its static runners are named as the third producer in the
structural finding because the exposure is real and worth
recording, but phase 1 builds two detections, not three. 33fl#826
tracks its own rollover, and the note in
`group_vars/all/static_runners.yml` is where the exposure is
recorded for the next reader.

What belongs here instead is the general lesson: a static runner
fleet advertises no operating system, so it is structurally
invisible to a label-based audit. Any future fleet of that shape
needs the same treatment, and phase 6 says so.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Alarm on absence | Complete | images acccd2b (#5), images 47ed141 (#6), private-ci ae7b1f8 (#49), private-ci e1f8fb1 (#54), 33fl 6de1764 (#827) |
| 2. Verify the artifact, not the name | Complete | images 6028647 (#7), images 4800c72 (#8), images b872641 (#9), actions 2ac4a94 (#74), 33fl bbbd842 (#836) |
| 3. Unblock the migration | Complete | private-ci 9eace9d (#60), private-ci 2e18c13 (#61), private-ci dbb78ca (#63), actions 8684eec (#78), actions 781d267 (#80), kerbside 79c2506 (#435), kerbside cfef26a (#450) |
| 4. The consumer sweep | Complete | Label half: actions 5a677a9 (#90), agent-python 8303e99 (#140), client-python 93a0999 (#405), client-python-k3s 1576e72 (#67), clingwrap d5eb4ea (#136), divergulent 53136f2 (#117), library-utilities 6f63b95 (#60), ryll 060e649 (#397), sfui 30f5501 (#35), instar a1c09aa (#589), occystrap 4b9d5ff (#143), shakenfist 54b18a0 (#4306), with visual-digest-rust #23 closed as superseded. Guest-image half: actions 8c02ab0 (#97, 4f), shakenfist 2a94e58 (#4379, 4g), shakenfist 984fdd1 (#4385, 4h part 1), actions 227593c (#124, 4h part 2). 4i and 4j carry no commit. |
| 5. Retire the end-of-life producers | Complete | private-ci e77fca7 (#100, 5a), actions 2351ece (#136, 5b), 33fl e92a1b4 (#925, 5c), development b464fcc (#218, 5d), and the `ubuntu:18.04` removal the phase grew: actions 587683a (#140) with development 13fec36 (#221) carrying its plan bullet and its 5f check. 5e and 5f carry no commit. |
| 6. Close the audit's blind spot | In progress | development 358bef0 (#222, 6a), development 6163de8 (#226, 6b), images 707768f (#14, 6c). 6d filed images#13 and carries no commit. 6e ran 2026-10-07, all eleven checks agreed, and the commit that ticks the done list is this row's own -- so its merge is not knowable here and phase 7's first commit sets this `Status` and completes this cell, per the phase landing shared block. |
| 7. Push audit | Not started | |

### 1. Alarm on absence

Closes: part of private-ci#44. Depends on: nothing.

**Status: complete, 2026-09-14.** Both detections are built,
merged and deployed. The shakenfist/images watchdog is images#5;
the conductor's persisted nightly and its staleness gauge are
private-ci#49; the stale-label issue is private-ci#54; the Grafana
rule watching the gauge is 33fl#827. images#6 landed the per-image
failure isolation this phase carries as prevention.

**Correction, 2026-09-17.** This paragraph used to say images#6 "is
audited against the images repository's default branch as part of
that pull request, per phase 7". It is not. That is what the push
audit shared block *requires*, not a record of something that
happened: `shakenfist/images` has no `PUSH-AUDIT.md`, and phase 6 of
its own `PLAN-image-build-modernisation` -- the phase that would run
the audit -- is `Not started`. Restating a policy in the past tense
is how a plan comes to believe it has evidence it never collected.
What is true is recorded under phase 2 below, for every landing in
this plan so far.

The conductor deployed at 20:37 on 2026-09-13, carrying private-ci
`e1f8fb15`. It logged the priming path on startup -- it claimed that
day's slot rather than starting a rebuild in the evening -- and
Prometheus is scraping the gauge, which reads the primed slot rather
than zero. So the fallback that seeds from the claimed slot is
doing its job: without it a fresh deployment would read zero and
alert immediately despite having missed nothing.

**How the invariant at the head of this phase is satisfied.** The
gauge is exported by the conductor process itself, on maui, and
scraped by Prometheus on the same host. Taken alone that would
re-create failure 4: a conductor that is not running exports
nothing, and a rule that only compares a timestamp sees no data and
stays quiet. Two things prevent it, and both are load-bearing
rather than incidental.

`up{job="conductor"}` is synthesised by Prometheus when a scrape
succeeds or fails, not exported by the conductor, so it reports 0
for a conductor that is not running. The pre-existing `Host down`
rule in 33fl watches it. That is the detection which does not
depend on the thing it watches, and it was already in place before
this plan.

33fl#827 also sets `noDataState: Alerting`, so the absence of the
gauge alerts in its own right rather than reading as health.

Stating the division precisely, because the first draft of this
note got it wrong: private-ci#54's stale-label issues cover a
conductor that is running and reports a label going stale. A
conductor that is absent entirely is covered by `Host down` and by
the no-data state of 33fl#827, not by anything conductor-side. The
images producer is covered independently of all of it by images#5,
which reads the published site from a hosted runner.

**Do not use the 26 hour threshold this plan originally specified.**
It was taken from the `Nightly report not dispatched` rule, where
the thing being timed is a workflow dispatch and is instantaneous.
A full image rebuild is eleven images built serially and takes about
an hour and a half -- six runs between 2026-09-07 and 2026-09-13
took between 1h24m and 1h37m -- and the gauge advances on
completion, not on the slot. So the first completion after priming
lands 25.6 hours after the primed slot, which leaves 24 minutes of
margin against a 13 minute observed spread in build duration. The
rule would have had a real chance of firing spuriously on its first
night, which is the worst possible introduction for an alert.

Use 30 hours. A genuinely skipped night reaches 48, so anything
between roughly 28 and 44 separates "skipped" from "slow", and 30
still alerts within about four hours of when completion was due.
33fl#827 landed at 30 hours with that reasoning recorded beside it.

The general point, for any future rule of this shape: a threshold
over a completion timestamp has to cover the slot interval plus how
long the work takes, not just the slot interval.

**What follows is the original specification**, kept verbatim
because the phase 2 correction below shows what is lost by quietly
editing a plan to match what was built. It was departed from in one
place. The private-ci bullet specifies "a scheduled job elsewhere
that reads the published dashboard or the conductor's API"; what
landed reads a Prometheus gauge through a Grafana rule instead,
because that estate already existed, already scraped the conductor,
and already carried two rules of exactly this shape to copy. The
requirement the bullet was protecting -- that the detection not
depend on the thing it watches -- is met as set out above. The
scheduler-persistence item in the last paragraph is done, in
private-ci#49.

Two detections, for the two producers this organisation
controls. Neither may depend on the thing it watches -- a
component that has stopped running also stops reporting that it
has stopped running, which is how failure 4 stayed invisible for a
day. 33fl's static runners get no detection here, per D6.

* **shakenfist/images**: phase 4 of that repository's plan. A
  scheduled `HEAD` against
  `images.shakenfist.com/<image>/latest.qcow2`, comparing
  `Last-Modified` against a 72-hour threshold, from a runner with
  no connection to the build host. This measures what a consumer
  receives, so it also catches a broken publish step or stale
  nginx.
* **private-ci**: the conductor already tracks blob age per label
  in `_update_status(image_ages=...)`, so the data exists. It must
  not be the conductor that reports on it, though: the conductor is
  the component whose restart silently skipped a nightly rebuild,
  and a conductor that is not running cannot tell anyone it is not
  running. The detection is therefore a scheduled job elsewhere
  that reads the published dashboard or the conductor's API and
  files an issue when any label's age exceeds N days, or when the
  cycle summary is absent or stale.

Phase 1 also carries one piece of prevention, because it is what
makes the detection actionable: phase 1 of the shakenfist/images
plan, so one failing image stops one image rather than the whole
run. An alarm that fires for the entire list every time teaches
people to ignore it.

Also fix the scheduler bug #44 documents: persist the last
completed nightly rather than recomputing `nightly_due` into a
local at every loop start. Note that #44 is honest that this
mechanism does **not** explain the 2026-09-12 miss, so the fourth
checkbox there -- working out what actually happened -- stays open
after this phase and may be a separate defect.

### 2. Verify the artifact, not the name

Closes: nothing on its own. Depends on: nothing.

The phase that would have caught three of the five failures, and
the one most likely to be dropped for being nobody's issue.

**Status: complete, 2026-09-16.** `verify-release` is in the
element list of all fourteen images published on the night of
2026-09-16, the build host is on images `b872641` with a
root-owned checkout, and the desktop and dependency assertions are
on `actions` `main`. private-ci#45's first checkbox is ticked with
the artifact read directly -- Debian 13.7, trixie, GNOME 48 -- so
phase 3's stated dependency is recorded rather than remembered.

**None of this plan's out-of-repository landings has been push
audited, and phase 7 should not expect to cite one.** Checked
2026-09-17 across all ten merged pull requests recorded in phases 1
and 2 -- images#5, #6, #7, #8, #9; private-ci#49, #54; actions#74;
33fl#827, #836 -- plus private-ci#60 from phase 3. Not one carries
an audit in its body or its comments, and none of
`shakenfist/images`, `shakenfist/actions`,
`Mach33Labs/33fl` or `shakenfist/private-ci` has a `PUSH-AUDIT.md`
at all -- so no such audit could have been run. The shared block's
"audited as part of the pull request that lands it" has been the
plan's assumption rather than its practice.

**The shas themselves are verified.** All eleven merge commits
recorded in the Execution table were read on 2026-09-18 with `git
cat-file -p <sha>` in each of the four repositories: every one has
two parents and a message naming the matching pull request. No
repository here squash-merges, so each recorded sha's diff against
its first parent is the whole of what landed, which is what the
push audit shared block requires of the record rather than merely
of the number.

What that costs phase 7 is specific rather than general. It has
three options and should say which it took: run the accumulated
audit itself against each repository's default branch, which is the
work the shared block says it may cite instead of doing; accept the
three `shakenfist/images` landings as covered by that repository's
own phase 6 when it runs, which leaves actions#74 and 33fl#836
uncovered by anything; or record the gap and decline it in writing.
The one landing where this is not bookkeeping is images#8, which
broke the nightly build on its first night: an audit of the
accumulated diff is precisely the instrument that would have looked
at a self-update under errexit, and it did not run.

**What this phase cost, which belongs in phase 7's audit.** The
self-update it shipped (images#8) broke the nightly build on its
first night. `build.sh` runs under errexit, and
`before=$(git rev-parse HEAD)` on a line of its own is a simple
command, so when git refused the checkout on ownership the run
ended at 05:00:01 having published nothing. The host has no MTA,
so cron discarded the one line that explained it. It was found by
the completion check at the head of phase 3's planning, not by any
alarm: `tools/check-image-freshness.sh` uses a 72 hour threshold
and would not have reported it until 2026-09-17.

Two fixes, one per cause: images#9 puts every git command inside
the `if` condition so any git failure warns and builds anyway, with
two regression tests that exit 128 against the previous script;
33fl#836 owns the checkout as the user cron runs as, and asks
cron's question by stripping `SUDO_UID` rather than sudo's. The
general lesson for the rest of this plan: a check run under `sudo`
is not the check cron runs, and git is one of several tools that
behaves differently between them.

* **shakenfist/images**: after building an image, assert that it
  is what it claims. Landed 2026-09-13 as the `verify-release`
  element (images#7). This was listed under Future work in that
  repository's plan; this plan promoted it, because it is the only
  control that addresses D2.

  **Correction, 2026-09-13.** This bullet used to say that reading
  `/etc/os-release` and comparing it against "the release the build
  asked for" was enough to have caught the two-year bullseye defect
  on its first night. That is wrong, and the way it is wrong is the
  most useful thing in this phase.

  Nothing in those builds disagreed with itself. `build.sh` passed
  `DIB_RELEASE=bullseye`, diskimage-builder built bullseye, and the
  image honestly reported bullseye. Every comparison between the
  image and the build's own inputs would have passed, every night,
  for two years and two months. What was wrong was the name the
  artifact was published under.

  So the invariant worth asserting is not "the image matches what
  was requested" but "the image matches what it is about to be
  called" -- and the name is held by the thing doing the publishing,
  which is usually not the thing doing the building. The element
  therefore compares against the publish label, which `build.sh`
  passes in, and keeps the `DIB_RELEASE` comparison only as a
  secondary check.

  D2 says "verify the artifact, not the name". Read literally that
  is the wrong way round: verifying the artifact against the build
  is what would have failed here. Read as "verify the artifact
  against the name it will be published under", which is what it
  was reaching for, it is exactly right. Anything else applying D2
  -- the private-ci bullet below, and any future producer -- should
  take the second reading.
* **private-ci**: confirm `debian-gnome:13` is genuinely trixie
  before wiring it up, per #45's own caveat, and adopt the
  actions#66 pattern -- end each image build by exercising the
  thing that image exists to provide.

### 3. Unblock the migration

Closes: private-ci#45 (partly -- see decision 3.5), private-ci#39.
Depends on: phase 2, satisfied. Planning effort: high, because the
sequencing spans two repositories and the gnome snapshot machinery
is not where the original sketch said it was.

Two labels are stuck. `debian-gnome-12` has no trixie successor, so
the `eol-distro` criterion bans a label the fleet still needs. And
the dependencies cache disk -- which every runner and inner CI
primary mounts at `/srv/ci`, and whose absence blocks *all* CI
provisioning -- is built from `debian:11`, a base that can no longer
be built at all: `bullseye-security`'s `Release` expired
2026-09-08.

**Status: complete, and verified in production.** Both stuck labels
are unstuck. `ci-images/debian-gnome-13` first built on 2026-09-17
at 06:53 (529s, blob `b9b624c8-713e-440b-ad0f-82b0e5f5ef60`), and
the dependencies disk now builds on Debian 13 -- verified in
production rather than at merge, with the conductor deployed at
private-ci `ad9863eb` and the nightly creating its builder as
`disks=['100@debian:13', '50']` on 2026-09-18, against
`['100@debian:11', '50']` the night before. private-ci#39 is closed
with that evidence. private-ci#45 keeps its fourth checkbox for
phase 5. kerbside#450 merged on 2026-09-19, correcting the defect
kerbside#435 introduced, which was the last outstanding piece.

**Three things this phase got wrong, recorded for phase 7.**

*The survey scoped the gnome move at seven places across two
repositories. It was about thirteen across four files, in four
repositories.* `GNOME_LABEL`'s comment, two docstrings, three tests,
and the marker paragraphs in private-ci's own `AGENTS.md` and
`ARCHITECTURE.md` all named the release and would have gone
factually wrong. More importantly, `shakenfist/kerbside` reads the
cached snapshot off the disk by hardcoded path and was never looked
at, because the survey grepped only the repositories it expected to
touch. A consumer you do not grep for is a consumer you do not have.

*A merge is not a deploy, again.* private-ci#61 merged on 2026-09-17
at 09:52 and that evening's nightly still built on Debian 11,
because the conductor had not been redeployed -- it had even
restarted in between, which is not the same thing. This is the same
distinction that cost phase 2 a night of images, in a different
component, and neither phase had a check that would have caught it.

*Two ordering gates the plan set were both crossed.* actions#78
merged ahead of its "must not merge until 3c has built" gate, and
private-ci#63 merged ahead of actions#80 despite the playbook-first
rule. Neither caused damage -- the first by luck, since ansible's
`auto` interpreter discovery handles bullseye unaided, and the
second because the conductor was not deployed in the window -- but a
gate stated only in a plan file and a pull request body is not a
gate. Phase 7 should ask what would actually have enforced them.

**The gnome rename was reconsidered mid-phase, and the second answer
was better.** The plan called for renaming the cached snapshot from
`debian-12-gnome-agents` to `debian-13-gnome-agents` and sequencing
three repositories around it. Review on actions#80 pointed out that
the disk is reformatted from scratch on every build, so a rename has
no transition window at all. The published name is now
`debian-gnome-agents`, carrying no release, with a transitional
hardlink at the old name; `gnome_release` governs only the label
lookup and the scratch filename. That removes the release from a
cross-repository interface entirely, so the next desktop bump is not
a fleet change. kerbside#435 was written against the abandoned
design and merged anyway, reading a path that is never published and
silently taking the legacy hardlink on every run; kerbside#450
corrected it to the published name, keeping the legacy fallback for
clusters whose disk predates the stable name.

#### What the survey found

Checked 2026-09-16 against `private-ci` `84f39cc` and `actions`
`edc4b73` -- that repository's `main` that day, pinned to a sha
rather than written as `origin/main`, because a moving basis is
what makes a line number unreconcilable a fortnight later. The
original sketch for this phase was written before phase 2 executed.
Most of it survived; one claim was materially incomplete and one
was a near miss.

**Two bases, stated once so that every number below can be
placed.** Every line number in this survey is as it stands on those
two commits. Every line number in the *step briefs* is as it stands
**after step 3a**, that is on `private-ci` `392700d`, where 3a's
seven-line `IMAGE_BUILDS` entry shifted everything below
`conductor/imagebuilder.py:123` down by seven. So the survey's
`:143` and step 3b's `:149` are the same entry read on different
bases *and* by different anchors: `:143` is its `base_image` line
pre-3a, `:149` its `name` line post-3a (`:142` and `:150` being the
other of each pair). `conductor/imagebuilder.py` is byte-identical
between `84f39cc` and 3a's first parent, so the seven-line shift is
the only difference between the two bases. Nothing in this plan
pins `actions` after the survey, so re-derive anything quoted from
`actions/` against its `main` before editing rather than trusting
the number here.

**Confirmed as written.**

* `debian-gnome-13` really is absent from `IMAGE_BUILDS`.
  In `private-ci`, `debian-gnome-12` sits at
  `conductor/imagebuilder.py:120` with `playbook:
  ansible/ci-image-desktop.yml`, exactly as private-ci#45 quotes
  it.
* The dependencies entry really is `base_image: 'debian:11'`, at
  `conductor/imagebuilder.py:143` in `private-ci`.
* The two conditions to reword really are in `actions`, at
  `ansible/ci-dependencies.yml:50` and `:61`, still at those exact
  line numbers on `edc4b73` after phase 2 edited that file.
* `debian:13` and `rocky:10` really are absent from the cached image
  list in `actions` (the `Cache all minimal images we currently
  build to reduce network traffic` task,
  `ansible/ci-dependencies.yml:124-177`).

**Measured, because the sketch did not ask: the two new images
fit.** Step 3d grows the cached set on a fixed disk and decision 3.4
leaves three frozen entries in place, so the growth is worth a
number rather than an assumption. The cache disk is the builder
instance's second disk, declared as `- "50"` at
`ansible/ci-dependencies.yml:23-26` in `actions`: 50GB. The eleven
upstream images cached today total 8.9GiB by `Content-Length` on
`images.shakenfist.com` (measured 2026-09-17); the two additions
are `debian:13` at 0.41GiB and `rocky:10` at 0.96GiB, so 1.4GiB of
growth.

Comfortable -- but that bounds the growth, not the headroom. The
disk also carries the github-actions-runner tarball, the gnome
snapshot, and a depth-1 clone of `imago-testdata` which is
resparsified in place and whose size is not knowable from here. So
step 3c gathers a `df` reading from a live disk and step 3d stops
rather than adding the two images if the headroom is thin. This
paragraph is not the answer, only the part of it that could be
measured without a cluster.

**And the playbook does not check what it caches.** Worth stating
because it is tempting to assume it does: `actions`'
`ansible/ci-dependencies.yml` has exactly one cache-contents task,
`List contents of /srv/ci/cached` -- a bare `ls -lrth` at
`:285-286` -- which prints and asserts nothing. `get_url` fails the
play on a 404 or a full disk, so a *missing* entry does break the
build, but nothing inspects the set afterwards, and no task
confirms each entry has content. So "the build passed" is weaker
evidence than a definition of done should
lean on, and this phase's says what is actually read instead.

**Materially incomplete: the gnome snapshot is not one line.** The
sketch treats "should the cache disk snapshot `debian-gnome-13`
instead" as a decision. It is a decision, but acting on it touches
two repositories: one module constant, plus **seven further sites
that spell the label out literally and that no constant reaches**.
The master plan did not say so. Counted as sites rather than as
lines, because several sites span more than one line and the count
is meant to be checkable:

* In `private-ci`, `conductor/imagebuilder.py:225` -- `GNOME_LABEL
  = 'debian-gnome-12'`, the constant. It is read on five lines
  (`:514`, `:519`, `:538`, `:871`, `:1213`; six occurrences in the
  file counting the definition), which are the gnome-less marker
  (`:507-538`), the nightly scheduling (`:871`) and the operator log
  line (`:1213`). Those readers need no edit -- they follow the
  constant.
* Four literal sites in that same file that the constant does *not*
  reach, so editing `GNOME_LABEL` leaves them describing the old
  behaviour: the `IMAGE_BUILDS` comment block (`:132`, `:134`,
  `:138`), the gnome-less marker's own explanatory note (`:220`),
  and two docstrings (`:533` and `:569`). Step 3f rewrites all four;
  the definition of done greps for them, so reading them and leaving
  them will not pass.
* Three literal sites in `actions`, in `ansible/ci-dependencies.yml`:
  the jq selector at `:220`
  (`select(.source_url == "sf://label/ci-images/debian-gnome-12")`),
  the skip message at `:230`, and the download path
  `/tmp/debian-12-gnome-agents` at `:252-253`.

`GNOME_LABEL` and the playbook have to move together. The marker
exists so that a cluster which built `dependencies` before the gnome
label existed rebuilds it once the label appears; if the constant
watches `debian-gnome-12` while the playbook snapshots
`debian-gnome-13`, that rebuild is triggered by the wrong label's
arrival.

**A near miss worth recording so nobody else chases it.**
In `actions`, `ansible/ci-image-desktop.yml:151` hardcodes
`label: "ci-images/debian-gnome-12"`, which looks like it would send
a `debian-gnome-13` build to the 12 label. It does not: the
conductor passes `label` in `extra_vars` (`private-ci`
`conductor/imagebuilder.py:791`, `'label': 'ci-images/%s' %
image['label']`), which overrides the play's default. The same is
true of the `base_image: "debian:11"` at `actions`'
`ansible/ci-dependencies.yml:8`. Both are stale defaults that only
bite somebody running the playbook by hand, and both should be
corrected while in the file rather than left as traps.

**Two findings out of scope, recorded here rather than fixed.**

* The cached image list carries `ubuntu:20.04`, `debian:11` and
  `fedora:40`. `shakenfist/images` builds none of those any more --
  its list is `ubuntu:22.04 ubuntu:24.04 centos:9-stream debian:12
  debian-docker:12 debian-gnome:12 debian-xfce:12 debian:13
  debian-docker:13 debian-gnome:13 debian-xfce:13 rocky:8 rocky:9
  rocky:10`. All three URLs still return 200, so CI is quietly
  caching three frozen artifacts, two of them end of life. That is
  inventory work for phase 4 and retirement for phase 5, and it
  belongs to private-ci#38's collated inventory. File it there
  rather than widening this phase.
* `build.sh`'s reconciliation summary -- `Built:` / `Failed:` /
  `Not attempted:` at `build.sh:736-747` in `images` -- is printed
  to stdout only. Per-image logs ship to Loki; the summary does
  not. Under cron on a host with no MTA it is discarded, so the one
  output that distinguishes "never attempted" from "built fine"
  reaches nobody.
  images#6 built that reconciliation precisely to make that state
  visible. File against `shakenfist/images`; it is a phase 1
  detection gap rather than a phase 3 migration step.

#### Decisions

Numbered `3.N` rather than `1`-`5`, because this plan already has a
top-level Decisions section numbered D1 to D6 and the step briefs are
read one row at a time. "Decision 3.5" and "D5" are different
decisions and now look it.

3.1. **Order is: label, then base image, then snapshot.** Add
   `debian-gnome-13` first (step 3a), because step 3f cannot point
   the snapshot at a label that does not exist. Move the
   dependencies base image second (step 3b). Switch the gnome
   snapshot last (step 3f). Each code step that depends on something
   having been built is immediately preceded by its own observation
   step: 3c gates 3d, and 3e gates 3f.

3.2. **The cross-repository ordering constraint is real and is
   stated.** Deleting the `debian:11` interpreter branch from
   `ci-dependencies.yml` must land *after* the `IMAGE_BUILDS` base
   image move has built successfully, not before. While
   `dependencies` still builds on `debian:11`, removing that branch
   sends bullseye down the auto-detect path -- which is the quirk
   the branch exists for. Two pull requests in two repositories,
   with a build in between, not one flag day.

3.3. **Delete the `debian:11` branch rather than reword it.** The
   master plan says to reword the two `when:` conditions to name the
   bullseye interpreter quirk. Once the dependencies entry is on
   `debian:13`, nothing invokes `ci-dependencies.yml` with
   `base_image: debian:11` at all -- it is the only entry that uses
   that playbook -- so the condition is not obscure, it is dead. Two
   `add_host` tasks collapse to one with no `when:`. This is a
   deliberate departure from the master plan's wording, on the
   grounds that a clearly-named condition for a case that cannot
   occur is still a thing the next reader has to rule out.

3.4. **Yes, switch the snapshot to `debian-gnome-13`.** private-ci#45
   leaves it open. The cache disk exists so CI does not pull from
   the network, and a cache of the EOL desktop is a cache of the
   thing phase 5 is about to delete. Switching now means one nightly
   cycle in which the disk still carries the 12 snapshot, which is
   harmless.

3.5. **private-ci#45 is not closed by this phase.** Its fourth
   checkbox -- retire `debian-gnome-12` once nothing consumes it --
   is phase 5's work, and this phase deliberately leaves both
   `debian-gnome-12` and `debian-11` building so nothing breaks
   mid-plan. The issue keeps three of four boxes ticked and closes
   in phase 5. **This is the decision most likely to be argued
   with:** it leaves two end-of-life labels building for two more
   phases, and `eol-distro` will keep reporting them the whole time.
   The alternative -- retire as we go -- couples this phase to
   finding every consumer, which is exactly what phase 4 is for.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Landed 2026-09-17 as private-ci#60 (`392700d`).** In `shakenfist/private-ci`, add a `debian-gnome-13` entry to `IMAGE_BUILDS` in `conductor/imagebuilder.py`, immediately after the `debian-gnome-12` entry. Copy its shape exactly: `name` and `label` both `debian-gnome-13`, `base_image` `debian-gnome:13`, `base_image_user` `debian`, `playbook` `ansible/ci-image-desktop.yml`. Do not touch `GNOME_LABEL` in this step. Update `conductor/tests/test_imagebuilder.py` -- the build-order and missing-label assertions both enumerate labels. Run `tox` (or the repo's test command) and confirm green. Commit subject: "Add a debian-gnome-13 CI image." |
| 3b | high | opus | worktree | In `shakenfist/private-ci`, change the `dependencies` entry in `conductor/imagebuilder.py` (`'name': 'dependencies'` at `:149` once 3a has landed) from `base_image: 'debian:11'` to `'debian:13'`. `base_image_user` stays `debian`. Read the comment block immediately above it (`:133-148`) before editing -- it explains the gnome-less marker and the first/last build ordering, and it names `debian-gnome-12`; leave that naming alone, step 3f moves it. Check whether any test in `conductor/tests/test_imagebuilder.py` asserts the dependencies base image, and **run the suite and confirm green either way** -- the check is not the verification. Two `debian-11` things are out of scope and are not each other: the `IMAGE_BUILDS` entry at `:58-63` is the image this repository *builds* and decision 3.5 keeps it until phase 5; the `CI_IMAGES` entry at `conductor/provisioner.py:46-51` is the label runners *boot from*. Neither is the cache disk. High effort because the dependencies label gates all CI provisioning: if this build fails, nothing provisions. Commit subject: "Build the dependencies disk on Debian 13." |
| 3c | low | haiku | none | **The gate for 3d**, and the mitigation the risks section names. Observation step, no code change. After 3b merges, confirm the conductor has rebuilt the `dependencies` label (`DEPENDENCIES_LABEL`, `conductor/imagebuilder.py:233`) on `debian:13` and that the build succeeded: `sf-client --json artifact list`, or the conductor's own log. Report the label's blob uuid **and its creation time**, and confirm that time is after 3b merged -- yesterday's blob still answering the lookup is exactly what this gate exists to catch. While there, take two more readings, both for the risks section rather than for 3d: run `df -h /srv/ci/cached` on a runner with the disk mounted and report free space, which 3d's item (1) checks against the 1.4GiB the two new images need; and report how long the nightly cycle now takes with twelve images, from the conductor's log, so that phase 1's 30-hour staleness threshold can be re-read against a measurement instead of against this plan's arithmetic. No commit. |
| 3d | medium | sonnet | none | In `shakenfist/actions`, edit `ansible/ci-dependencies.yml`. (1) Add `debian:13` and `rocky:10` to the cached image list in the `Cache all minimal images we currently build to reduce network traffic` task (`:124-177`), following the existing `- { url: ..., name: ... }` shape exactly -- but first check 3c's reported free space against the 1.4GiB the two images need (survey), and stop and say so if the headroom is under 5GiB rather than adding them anyway. (2) Delete the `Add to ansible (force python3)` task at `:40-51` and remove the `when: base_image != "debian:11"` from the task at `:52-62`, so one unconditional `add_host` remains -- see decision 3.3. (3) Change the stale default at `:8` from `base_image: "debian:11"` to `"debian:13"`. (4) Change `mkfs.ext4 /dev/vdc` at `:109` to `mkfs.ext4 -O ^orphan_file /dev/vdc`, with a comment above it saying *only* that this one feature is disabled, why, and what the floor is: the disk is mounted read-write by every runner, the oldest of which boots `ubuntu2004-ci-template.qcow2` on kernel 5.4, and `orphan_file` needs 5.15. Do not write that the feature list is pinned -- it is not, this disables one bit, and a comment claiming more than the command does is the next reader's trap. See the back brief. **This must not merge until step 3c has reported a successful dependencies build on `debian:13`** -- see decision 3.2. Verify with `tools/ansible-syntax-check.sh`, which phase 2 added. **Two commits, not one.** Items (1) to (3) are the migration and go under "Cache Debian 13 and Rocky 10, drop bullseye."; item (4) is its own commit, subject "Build the cache disk to the kernel 5.4 feature floor.", with the back brief's compat-versus-ro_compat measurement in the body. It has its own reasoning, its own definition-of-done bullet and a different blast radius -- it changes what every runner mounts, against a stated kernel floor -- and the risk story in this phase leans on a revert being one commit. One pull request is fine; one commit is not. |
| 3e | low | haiku | none | **The gate for 3f.** Observation step, no code change. Confirm `ci-images/debian-gnome-13` exists *and has a blob*: `sf-client --json artifact list` filtered on `sf://label/ci-images/debian-gnome-13`, or the conductor's own log. A label that exists with no blob is the failure mode -- 3f points the playbook's jq selector at this label, and a selector that matches nothing skips the snapshot silently. Report the blob uuid. No commit. |
| 3f | high | opus | worktree | Move the dependencies disk's gnome snapshot from `debian-gnome-12` to `debian-gnome-13`, across two repositories, as two pull requests. **Merge the `shakenfist/actions` one first, then the `shakenfist/private-ci` one**: the playbook snapshots whatever label it is told to look up and already handles the lookup failing (the snapshot is skipped with a warning), so a skipped snapshot for one night is recoverable, whereas a `GNOME_LABEL` watching a label the playbook never snapshots is a marker that never clears. In `shakenfist/actions/ansible/ci-dependencies.yml`: the jq selector at `:220`, the skip message at `:230-232`, the comment at `:212-216`, and the `/tmp/debian-12-gnome-agents` scratch path at `:252-254` and `:269`. **The destination at `:270` is different**: `/srv/ci/cached/debian-12-gnome-agents` is the name the snapshot has *on the cache disk*, so anything outside this repository that reads the disk reads that name. **That grep set was too narrow and its result was wrong.** A grep of `shakenfist/shakenfist`, `shakenfist/private-ci` and `shakenfist/actions` on 2026-09-17 found only the audit documentation, but two consumers sit outside those three. `shakenfist/kerbside` copies the disk by name in `.github/workflows/functional-tests.yml:583-589` and boots it as a SPICE test target, which is a real functional dependency and not documentation. And the documentation hit is in **this** repository, at `docs/audits/eol-distro.md:80` and `:128` -- `shakenfist/docs/components/development/audits/eol-distro.md:128` is the *published mirror* of it, so editing the path the earlier draft cited fixes nothing. Of the two lines here, `:128` calls the disk a real dependency on an old release and becomes wrong on rename; `:80` uses the name as an example of a string the audit's token matcher must *not* read as a runner reference, and an example naming a disk that no longer exists is a weaker example rather than a broken one. So grep `shakenfist/images`, `shakenfist/kerbside`, `Mach33Labs/33fl` and this repository as well before renaming, and if the name stays, say so in the commit message rather than leaving it looking forgotten. Also fix the stale play default at `ansible/ci-image-desktop.yml:151`. Run `tools/ansible-syntax-check.sh`. In `shakenfist/private-ci` (line numbers post-3a, on `392700d`, per the survey's note on bases): `GNOME_LABEL` at `conductor/imagebuilder.py:232`, its five uses (`:521`, `:526`, `:545`, `:878`, `:1220`), and **rewrite** the four places that carry the literal without going through the constant -- the `IMAGE_BUILDS` comment block at `:133-147`, the gnome-less marker note at `:226-230`, and the two docstrings at `:540` and `:576` -- so that all of them describe the behaviour in terms of `debian-gnome-13`. The definition of done greps for `debian-gnome-12`, so re-reading these and leaving them alone will not make it pass. Grep the tests for `GNOME_LABEL` and for the literal `debian-gnome-12` (`conductor/tests/test_imagebuilder.py:58`, `:93`) and run the suite green. High effort because the gnome-less marker decides when a cluster rebuilds its cache disk, and a constant that watches one label while the playbook snapshots another produces a rebuild that never fires. Commit subjects: "Snapshot the Debian 13 desktop image." in each repository. |
| 3g | low | haiku | none | Housekeeping. Tick checkboxes two and three on private-ci#45 and leave it open per decision 3.5, saying in a comment which phase closes it. Close private-ci#39 with the merge commits. Record the two out-of-scope findings from the survey: comment the frozen cache entries onto private-ci#38, which already exists and is not closed here, and file a *new* issue for the discarded reconciliation summary against `shakenfist/images`. No commit in this repository. |
| 3h | low | haiku | none | **Confirms step 3f actually worked**, which nothing else does. Observation step, no code change. Last in the table rather than adjacent to 3f because it has to wait for a nightly `dependencies` rebuild to complete after 3f merged. The survey establishes two facts that combine badly: the playbook's jq selector skips the snapshot with a warning when the lookup matches nothing, and the playbook asserts nothing about what it cached. So a grep for the absence of the old label passes whether or not the snapshot ever succeeded. On a `dependencies` disk built after 3f, confirm `/srv/ci/cached/` carries the gnome snapshot at non-zero size, that it is the Debian 13 artifact and not a survivor of the old name, and that the playbook run did *not* log the `label does not exist yet` skip message. Report the blob uuid it was downloaded from so it can be matched against the one step 3e reported. If the snapshot was skipped, say so rather than filing it: the marker is designed to trigger one rebuild, so the next nightly may fix it, and knowing which happened is the point of this step. **Two definition-of-done bullets have no other step that collects their evidence, so collect both here**, on the same disk: run `dumpe2fs -h` on it and report whether `orphan_file` appears in its feature list, and report the `/srv/ci/cached` entries for `debian:13` and `rocky:10` with their sizes, read from the `List contents of /srv/ci/cached` output at `ci-dependencies.yml:285-286` in that run. This step is the only one that looks at a disk built after 3d: 3c reads one built before it, and 3e reads a blob rather than a disk. No commit. |

#### Risks and mitigations

* **The dependencies build fails on trixie and CI stops
  provisioning.** This is the real risk in the phase: a missing
  `dependencies` label blocks everything. Mitigated by step 3b being
  its own pull request with nothing else in it, so a revert is one
  commit; and by `_build_order()` already building `dependencies`
  first when its label is missing, so recovery is the next scan
  rather than a manual intervention. Checked by step 3c, which is
  where somebody watches the conductor build the label.

  **The one-commit revert holds only until 3d item (4) lands.**
  After that, reverting 3b on its own puts `base_image` back to
  `debian:11` while `mkfs.ext4 -O ^orphan_file` stays in the
  playbook -- and the measurement recorded below is that bullseye's
  `mke2fs` 1.46.2 exits 1 on that flag. The revert would fail the
  play and leave no `dependencies` label at all, which is precisely
  the outcome the mitigation exists to prevent. So the rollback is
  one commit while 3b is the newest thing landed, and two
  afterwards: revert the `shakenfist/actions` commit that added the
  flag **first**, then 3b. Stated here because a mitigation that
  fails closed is worse than none, and the moment somebody needs
  this is the worst moment to work it out.
* **3d merges before 3b builds.** Then bullseye takes the
  auto-detect interpreter path and the dependencies build breaks for
  the reason the deleted branch existed. Mitigated by decision 3.2
  being stated in 3d's own brief rather than only here, and by step
  3c being a gate in the table between the two. The earlier draft of
  this plan claimed 3b -- the label observation, now 3e -- as that
  gate; it was not one, it sat before the wrong step, and the
  phase's one CI-stopping risk was resting on a sentence.
* **The gnome snapshot switch half-lands.** `GNOME_LABEL` in one
  repository and the playbook in another cannot merge atomically.
  Mitigated by ordering: merge the playbook first (it snapshots
  whatever label it is told to look up, and the lookup failing is
  already handled -- the snapshot is skipped with a warning), then
  the constant. A skipped snapshot for one night is recoverable; a
  marker watching a label nobody builds is not self-correcting. That
  ordering is stated in 3f's own brief, not only here, for the same
  reason decision 3.2's is: 3f is the step most likely to be executed
  by a sub-agent reading one row.
* **Step 3a lengthens the nightly cycle that phase 1's staleness
  threshold was sized against.** Phase 1 chose the conductor's
  30-hour threshold from measured duration -- eleven images built
  serially, 1h24m to 1h37m across six runs, with the gauge advancing
  on completion. 3a makes it twelve, and a desktop image is not one
  of the cheap ones. The arithmetic still holds comfortably:
  completion lands about 25.6 hours after the primed slot, so 30
  hours leaves roughly 4.4 hours of headroom and one image cannot
  plausibly consume it. Recorded because the plan should say the
  interaction was looked at rather than leave it to be rediscovered,
  and because phase 5 later removes images while this phase adds
  one. Step 3c's brief asks for the new cycle duration alongside
  its `df` reading, so the arithmetic above gets checked against a
  measurement; the request lives in the table row because a
  sub-agent executing one row does not read this section.
* **Every dependencies disk built between 3b and 3d carries
  `orphan_file`.** 3b moves the builder to trixie; 3d item (4) is
  what turns the feature back off. Decision 3.2 forces 3d to wait for
  3c, so the window is real: disks built in it are snapshotted and
  mounted read-write by runners on kernels 5.4, 5.10 and 5.14, all
  below the 5.15 floor the feature needs. It spans as many nightly
  cycles as pass between the two merges, which is at least one and
  should be one.

  **Item (4) cannot be moved before 3b to close the window, and the
  attempt would be worse than the window.** Measured 2026-09-18 in
  `debian:11` and `debian:13` containers: bullseye's `mke2fs`
  1.46.2 rejects the flag outright -- `mkfs.ext4 -O ^orphan_file`
  exits 1 with `Invalid filesystem option set: ^orphan_file` -- and
  plain `mkfs.ext4` there sets no `orphan_file` anyway, because the
  feature did not exist. Trixie's 1.47.2 sets it by default and
  accepts `^orphan_file` to remove it. The task runs `mkfs.ext4`
  through `shell:`, so a non-zero exit fails the play and the
  `dependencies` label -- which gates all CI provisioning -- does
  not get built. **So item (4) must land in the same pull request as
  the trixie move or after it, never before.** That is a stronger
  constraint than decision 3.2 and is the reason the window cannot be
  engineered away, rather than an oversight.

  What makes the window acceptable is the back brief's
  compat-versus-ro_compat measurement: `orphan_file` sets a compat
  bit, so an older kernel mounts such a filesystem and ignores the
  feature. The narrow failure mode is a snapshot taken after an
  unclean unmount, where the orphan inode list is in a file the old
  kernel will not read. One nightly cycle of that exposure, on a
  disk that is rebuilt nightly anyway, is cheaper than a day with no
  `dependencies` label.
* **`debian-gnome:13` turns out not to boot a desktop under CI even
  though the guest image is correct.** Phase 2 confirmed the
  artifact is trixie with GNOME 48 and that it reaches the gdm3
  greeter. `ci-image-desktop.yml` now proves this at build time
  (actions#74), so this fails the build loudly rather than producing
  a label that looks fine.

#### Definition of done

* `IMAGE_BUILDS` contains a `debian-gnome-13` entry and
  `ci-images/debian-gnome-13` exists as a label with a blob.
* `grep -rn 'debian:11' conductor/imagebuilder.py` returns only the
  `IMAGE_BUILDS` entry that *builds* the `debian-11` image (`:58-63`
  before this phase edits the file), which decision 3.5 keeps until
  phase 5 -- and no dependencies entry. The `debian-11` entry in
  `conductor/provisioner.py`'s `CI_IMAGES`, which is the label
  runners boot from, is a different thing in a different file and
  this phase does not touch it.
* In `shakenfist/actions`, `grep -n 'debian:11'
  ansible/ci-dependencies.yml` returns **only** the two lines of the
  cached image list's `debian:11` entry (its `url` and its `name`),
  which the survey's out-of-scope finding deliberately leaves for
  private-ci#38 to collate. In particular it returns no `when:`
  clause and no `base_image:` play default: the latter is step 3d
  item (3), the smallest of its four changes and the one that would
  otherwise have no gate at all. Note that this is a grep whose
  passing output is non-empty, which is deliberate -- a bullet
  asking for nothing would be unsatisfiable while those three frozen
  entries stay, for the same reason the old `debian-gnome-12` bullet
  was. `ansible-playbook --syntax-check` passes via
  `tools/ansible-syntax-check.sh`.
* `dumpe2fs -h` on a dependencies disk built after this phase does
  not list `orphan_file` among its features. Collected by step 3h,
  which is the only step that reads a disk built after 3d.
* The cached image list contains `debian:13` and `rocky:10`, and a
  `dependencies` disk built after this phase has `/srv/ci/cached`
  entries for both with non-zero size. The playbook does not assert
  this -- see the survey -- so the evidence is the `List contents of
  /srv/ci/cached` output at `ci-dependencies.yml:285-286` in a
  successful run, read rather than assumed, together with the `df`
  step 3c reported. Step 3h collects it; 3c cannot, because it runs
  before 3d adds the two images.
* The old label survives only in the entry phase 5 retires. This
  is two greps, one per repository, not one across both:
  `shakenfist/actions` has no `conductor/` directory, so a single
  `grep -rn 'debian-gnome-12' conductor/ ansible/` errors and exits
  non-zero there.
  * In `shakenfist/private-ci`, `grep -rn 'debian-gnome-12'
    conductor/imagebuilder.py` returns only the *lines of* the
    `IMAGE_BUILDS` entry that phase 5 retires -- two of them, its
    `name` and its `label`, not one hit. Its `base_image` is
    `'debian-gnome:12'` with a colon and so does not match this
    pattern at all. No constant, no comment block, no marker note
    and neither docstring.
  * `conductor/tests/` is deliberately outside that grep. Decision
    3.5 keeps the entry building until phase 5, so the assertions
    naming `debian-gnome-12` there must *stay*; a gate that demanded
    they go would be satisfied most cheaply by deleting legitimate
    test coverage. Step 3f reads them and runs the suite green instead.
  * In `shakenfist/actions`, `grep -rn 'debian-gnome-12' ansible/`
    returns nothing at all: no jq selector, no skip message, no
    comment, no play default.
* `grep -rn 'debian-12-gnome' ansible/` in `shakenfist/actions` is a
  separate check, because the `/tmp` and cache-disk paths spell the
  label the other way round and can never appear in the grep above
  however it is scoped. It returns nothing if step 3f renamed the
  cache-disk destination, or only that destination if 3f decided to
  leave the name alone -- in which case 3f's commit message says so,
  per its brief, and this bullet is satisfied by that sentence
  rather than by an empty result.
* Step 3h has reported, from a `dependencies` disk built after 3f,
  that the gnome snapshot exists on the cache disk at non-zero size
  and is the Debian 13 artifact, and that the playbook's run did not
  log the skip message. The greps above pass whether or not the
  snapshot ever ran, so this is the bullet that distinguishes a
  switched label from a silently skipped one.
* private-ci#39 is closed; private-ci#45 has boxes one, two and
  three ticked, box four open, and a comment naming phase 5 as its
  closer.
* The frozen-entry finding is recorded on private-ci#38, which
  already existed before this phase and stays open, and a new issue
  exists against `shakenfist/images` for the discarded
  reconciliation summary. Two findings, one new issue.

#### Back brief

Confirm before step 3b is written: that building the dependencies
cache disk on `debian:13` is acceptable given the disk is snapshotted
and mounted by every runner, and that nothing consuming `/srv/ci`
depends on the disk's own filesystem being bullseye-era. The step is
cheap to propose and expensive to get wrong -- a broken dependencies
label stops all CI provisioning -- and the answer lives in what
mounts the disk rather than in what builds it.

**Answered 2026-09-16: yes, with one addition that step 3d must
carry.**

The builder's operating system is not consumed by anything. The
`dependencies` label is a snapshot of the *second* disk alone:
`ci-dependencies.yml` snapshots with `all: true`, records
`cisnapshot['meta']['vdc']['blob_uuid']`, and then explicitly
deletes the `vda` snapshot. So `base_image` picks the throwaway
builder, not the artifact, and moving it to `debian:13` changes
nothing a runner sees.

What it does change is the filesystem, and that is the coupling the
sketch missed. The disk is made by a bare `mkfs.ext4 /dev/vdc`
(`ci-dependencies.yml:108-109`), so it inherits whatever the
builder's distribution defaults to. Measured on trixie, e2fsprogs
1.47.2:

```
Filesystem features: has_journal ext_attr resize_inode dir_index
orphan_file filetype extent 64bit flex_bg metadata_csum_seed ...
```

`orphan_file` is new. `man 5 ext4`: "supported by Linux kernels
starting version 5.15, and by e2fsprogs starting with version
1.47.0." Bullseye's e2fsprogs did not set it; trixie's does, by
default, silently.

The consumers are older than that. Every runner attaches this disk
as its second disk (`provisioner.py:1512`), and the topology
playbooks mount `/dev/vdc` read-write with no options
(`ci-topology-slim-primary.yml:308-313` and five siblings). One of
those still boots `ubuntu2004-ci-template.qcow2` -- kernel 5.4. The
`debian-11` boot label is 5.10, and `rocky-9` is 5.14. All three
are below the floor.

**Measured 2026-09-17, so the risk is bounded rather than
unknown.** An earlier draft of this answer left open whether the
bit is compatible or incompatible. It is settleable, and was
settled by building both filesystems on trixie (e2fsprogs 1.47.2)
and reading the superblock feature words directly:

```
mkfs.ext4 -O  orphan_file   compat=0x0000103c  ro_compat=0x0000046b
mkfs.ext4 -O ^orphan_file   compat=0x0000003c  ro_compat=0x0000046b
```

The only difference is bit `0x1000` of `s_feature_compat`, which is
`EXT4_FEATURE_COMPAT_ORPHAN_FILE`. It is a **compat** feature: a
kernel that does not know it mounts the filesystem normally,
read-write, and ignores it. `mke2fs` also rejects `-O
orphan_present` as an invalid option, which confirms the paired
`orphan_present` bit is set by the kernel at runtime rather than at
format time; that one is `ro_compat`, and an unknown `ro_compat`
bit forces a read-only mount.

So the failure mode is narrow and specific: a pre-5.15 kernel is
refused a read-write mount only if the disk was snapshotted while
the orphan file still held entries. The playbook unmounts the disk
cleanly (`ci-dependencies.yml:288-291`) before snapshotting, and
every runner mounts a fresh copy of that snapshot, so in the normal
path `orphan_present` is clear and nothing breaks. The exposure is
a snapshot taken after an unclean unmount -- which is a real state
to be in, and one nobody would connect to a read-only `/srv/ci` on
a 20.04 runner six months later.

That bounds the risk; it does not remove it, and it does not change
the action. A shared cache disk should be built to a stated
compatibility floor rather than to whatever the builder's
distribution defaults to, because otherwise every future bump of
the builder OS re-rolls this dice in silence.

So step 3d also changes `mkfs.ext4 /dev/vdc` to `mkfs.ext4 -O
^orphan_file /dev/vdc` and says in a comment what the floor is and
who sets it. Note what that comment must *not* say: one flag is not
a pinned feature set, and the next e2fsprogs default that turns on
a new bit rolls the same dice again. The comment records that this
one feature is disabled for the 5.4 floor, which is true; pinning
the whole set with an explicit `-O` list would be the stronger
change, and is deliberately not taken here because a list written
today goes stale silently in the other direction, dropping features
the disk would benefit from. If a second feature ever has to be
disabled, that is the point to reconsider.

### 4. The consumer sweep

Closes: development#123. Contributes to private-ci#38, which closes
at the end of phase 5. Depends on: phase 3, satisfied. Planning
effort: high, because the inventory the master plan carried is a
fortnight stale in both directions, and because the criterion that
produced it cannot see the half of the migration that actually
gates phase 5.

**Status: complete, 2026-10-02.** 4j ran on 2026-10-02 and all six of
its checks pass; what they found is recorded under *What 4j confirmed*
at the end of this section. One step
landed out of band, one marker the phase was going to add turned out
to be already there and broken, and a bug in another repository
blocked the under-cloud half for three days. All of that is recorded
at the end of the survey -- the first two and the block under *Amended
2026-09-21*, the block's removal under *Amended 2026-09-24* -- and the
affected steps carry the correction inline. All thirteen label pull
requests have resolved -- ten in the first sweep, occystrap#143 and
shakenfist#4306 on 2026-09-24, and visual-digest-rust#23 closed as
superseded by a change that moved the label anyway -- and 4f merged as
actions#97 the same day. 4g merged as shakenfist#4379 on 2026-09-29
(22:03 UTC). Running 4g found a defect in 4h's own safety gate: the
gate grepped the artifact's URL spelling, and seven live consumers
name it by its bare short name, so it would have returned clean while
the step broke them. 4h then moved those consumers in shakenfist#4385
before removing the upload in actions#124, and its brief gives both
gates as commands, amended 2026-09-30.
**4f's post-merge verification has now passed on both runs**, which
discharges 4g's hold: see the 2026-09-29 amendment below. The
2026-09-26 amendment recording it as failing is left as the snapshot
it was. shakenfist#4309 unblocked the under-cloud half on 2026-09-23
-- the under-cloud rather than the guest-image half, which is a
distinction this phase keeps deliberately and which the risks section
spells out. Amendment dates in this phase are local time, AEST
(UTC+10); timestamps quoted from issues, merges and CI runs are UTC
and say so, which is why an amendment can be dated a day after the UTC
day its commit landed on.

private-ci#38 is the collated inventory of where the fleet uses
obsolete base images. It is a reference rather than a task, and it
closes when the thing it inventories is gone: this phase clears the
consumer half and phase 5 clears the producer half, so #38 closes at
the end of phase 5 rather than when its own checklist is ticked.

Two things make this less mechanical than a reference count
suggests:

* **Each runner-label move is two files.** A label change also needs
  the replacement declared in that repository's
  `.github/actionlint.yaml` under `self-hosted-runner: labels:`, in
  the same commit. actionlint fails a workflow naming an undeclared
  label, so missing this turns a one-line fix into a failing lint.
* **The runner labels are the visible half.** The guest images the
  CI clusters boot, and the artifact name a smoke cluster uploads
  into itself, are also Debian 12, and the `eol-distro` criterion
  reads neither. That half is the half phase 5 trips over.

#### What the survey found

Checked 2026-09-19 against each repository's `main` as fetched that
day, by shallow-cloning all twenty-nine non-archived repositories
in the `shakenfist` organisation and running this plan's own
criterion over every one of them, including the eight the daily
audit does not cover. The scan is reproducible from a checkout of this
repository:

```python
import sys
sys.path.insert(0, 'scripts')
from audit.checks import distros
from audit.repo import Repo
found = distros.scan(Repo(clone_path, name, 'shakenfist'))
```

It agrees line for line with `docs/audits/compliance.md` as
regenerated at 2026-09-19T10:32, which is the artifact to re-read
rather than any number written here.

**The count has almost halved, and not because of this plan.**
development#123 measured 80 references in 15 repositories on
2026-09-12. Today it is 45 in 13. Thirty-eight references were
cleared by ordinary repository work answering the daily audit's own
issues:
development's four cleared when #119 merged, exactly as #123
predicted; `instar` moved its whole pool on 2026-09-16 (`dbc1cc0`,
"Move CI onto the Debian 13 runner pool"), taking 20 of its 21; and
`kerbside` moved on 2026-09-17 (`45af922`, "Move CI off Debian 12,
which is end of life"), taking all 14. The count moved the other way
too -- `occystrap` went from 3 to 5 and `shakenfist` from 4 to 5 --
because new workflows land naming the label the fleet still
advertises. So the inventory is the compliance page, re-read at the
start of each step, and never a number in this plan.

**Every remaining label reference is a literal `runs-on:` line.**
All 44 of them; there is no matrix indirection, no
`workflow_dispatch` input default and no reusable-workflow input to
chase, which is what makes the sweep a token edit per line rather
than a reading exercise. The forty-fifth finding is an image, not a
label, and is decision 4.3.

| Repository | `debian-12` | `debian-12-docker` | The whole `actionlint.yaml` edit |
|------------|-------------|--------------------|----------------------------------|
| ryll | 5 | 12 | add `debian-13-docker`, delete both retired |
| occystrap | 4 | 1 | add `debian-13-docker`, delete both retired |
| shakenfist | 4 | 1 | add both, delete both retired |
| client-python-k3s | 4 | - | delete `debian-12` |
| actions | 3 | - | delete `debian-12` **and a stray `debian-12-docker`** |
| agent-python | 2 | - | add `debian-13`, delete `debian-12` |
| clingwrap | 2 | - | add `debian-13`, delete `debian-12` |
| sfui | 2 | - | delete `debian-12` |
| client-python | 1 | - | delete `debian-12` |
| divergulent | 1 | - | delete `debian-12` |
| library-utilities | 1 | - | delete `debian-12` |
| visual-digest-rust | 1 | - | add `debian-13`, delete `debian-12` |
| instar | - | - | none, and that is checked rather than assumed |

The fourth column is the entire `self-hosted-runner: labels:` edit for
that repository, read on 2026-09-19: what the workflows will need
declared once they have moved, and what stays declared with nothing
left to use it. `actions` is the asymmetric one -- it declares
`debian-12-docker` and no workflow in it names that label, so the
declaration already outlives its last user and a step that deletes
only `debian-12` leaves it behind. `instar` declares `debian-13`,
`debian-13-docker` and neither retired label, which is why step 4d
edits one line; step 4j greps all thirteen rather than trusting this
row, because a declaration leaves no finding and so nothing else in
the phase would notice.

**Both replacements are verified in production rather than
declared.** Last night's nightly published `ci-images/debian-13`
(blob `b70b037f-05e2-47d4-8512-7c661adfc917`) and
`ci-images/debian-13-docker` (`7b708330-4489-4181-b703-c93bb2fd6ff8`),
and runners of both labels served real jobs on 2026-09-19 -- a
`debian-13-docker` worker came online and busy at 19:08 for a
Mermaid lint, and `debian-13` jobs at `xl` and `s` were scheduled
alongside it. `/srv/ci/debian:13` is on the dependencies cache disk,
which phase 3 put there (`actions`
`ansible/ci-dependencies.yml:194`). Eleven of the twelve
`IMAGE_BUILDS` entries published a label last night; the one that did
not is `debian-11`, whose base can no longer be built, which is phase
5's retirement and phase 1's permanent `False` seen from the other
side.

**The half the criterion cannot see, and it is the half that gates
phase 5.** The `eol-distro` specification says so itself -- guest
images and cached disks are "the pre-push reviewer's to raise" --
but nobody has raised them, and phase 5 removes `debian-12` from
`IMAGE_BUILDS` and `CI_IMAGES`. Every site below then names a label
the conductor no longer builds:

* **The guest image label**, `sf://label/ci-images/debian-12`, at
  eight live sites: `actions`
  `build-smoke-cluster/action.yml:29` (the composite action's
  default) and `.github/workflows/smoke-cluster.yml:56` (the
  reusable workflow's own input default, which feeds it), and
  `shakenfist` `.github/workflows/functional-tests.yml:443`, `:463`,
  `:477`, `:515` and `.github/workflows/scheduled-tests.yml:42`,
  `:52`. Two callers pass no `base_image` at all and so take the
  default today: `shakenfist`
  `.github/workflows/functional-tests.yml:558` and `kerbside`
  `.github/workflows/sf-e2e-functional.yml:104`.
* **The cached image and the artifact name it is uploaded under**, at
  `actions` `build-smoke-cluster/action.yml:249` and `shakenfist`
  `.github/workflows/functional-tests.yml:583`. Both sites have the
  same shape and neither is greppable by its verb: `sf-client artifact
  upload` is assembled into a `${setup}` shell variable a few lines
  above, and the line that carries the artifact name and the source
  path reads `"${setup} debian-12 /srv/ci/debian:12 --shared
  --no-checksum"`. Grep `/srv/ci/debian:12` to find them; a grep for
  `artifact upload debian-12` finds neither.
* **That artifact name, read back by the test suite**, as
  `sf://upload/system/debian-12`: 43 references across 12 files in
  `shakenfist`, in `deploy/shakenfist_ci/` and `tests/` and
  `deploy/nodelifecycletests.sh:132`. There is a constant for it --
  `CLUSTER_CI_IMAGE` at `deploy/shakenfist_ci/base.py:37` -- and it
  reaches one of the 43. This is `GNOME_LABEL` again: a constant
  that names the thing, and a couple of dozen literals the constant
  does not reach. (Noted 2026-09-26: these paths omit the
  top-level `shakenfist/` package directory the source tree nests
  `deploy/` and `tests/` under, so they never resolved as written;
  4g's item (3) carries the resolving form. The line numbers here
  stay as the dated snapshot they were.)
* **The job names, read by a tool.** `shakenfist`'s matrix calls its
  lanes "Debian 12 cluster" and "Debian 12 tier"
  (`functional-tests.yml:441`, `:475`), and
  `tools/ci_headroom_harvest.py:134` and `:141` key
  `BUNDLE_TOPOLOGIES` off those exact strings, with the file's own
  comment warning that the derivation "would break silently if that
  changed". Renaming the lane without the tool is a silent stop, not
  a failure.

`private-ci`'s own unit tests match `ci-images/debian-12` eight
times in `conductor/tests/test_imagebuilder.py` -- seven naming the
label and one the `-docker` variant, which the substring every grep
in this phase uses also returns; those follow the
`IMAGE_BUILDS` entry in phase 5 rather than moving here.

**One claim in the master plan has already been overtaken.**
#123 recorded two findings that are not label swaps. The first still
holds: `instar` boots `debian:12` in a functional-test matrix that
deliberately covers several distributions. The second does not --
`kerbside`'s two bookworm-tagged rust images are already on trixie
(`rust/kerbside-proxy/Dockerfile:17` is `rust:slim-trixie` and
`loadtests/latency/Dockerfile:8` is `rust:1.97-trixie`), and that
repository has been compliant since 2026-09-17.

**Eight active repositories are outside the audit's matrix**, and
one of them is `shakenfist/images`, which builds the guest images
this whole plan is about. The others are `client-python-ova`,
`divergulent-reviews`, `homebrew-tap`, `performance`,
`reproducables`, `sonobouy` and `uefi-latency-guest`. All eight were
scanned by hand for this survey and none has a finding today, so
nothing is being missed right now -- but nothing is watching them
either, and "we grepped it once" is the state phase 3 said was not
good enough. Widening the matrix is phase 6's work, and decision 4.6
says why it is not done here.

**Amended 2026-09-21, after this plan merged, and anchored
2026-09-22.** Three things moved in the day after #148 landed. The
survey above is left exactly as it was read on 2026-09-19, because it
is the record of what the phase was planned against; the corrections
are here, and the steps they affect carry them inline as well. Every
date in this block and in the steps it amends is the day the thing
was read, which is why some of them are later than this heading: the
2026-09-21 pass found the three corrections below, and a second pass
on 2026-09-22 re-read the counts, the line numbers and the greps
before any of it was relied on.

**`shakenfist/actions` migrated itself, twenty-five minutes after
this plan merged.** `e2a56bd` ("Move off Debian 12 runners and guest
images.", 2026-09-20 02:08) answers actions#69 -- the daily audit's
own issue, not this phase. It moved all three `runs-on:` lines to
`debian-13` and deleted both retired declarations from
`.github/actionlint.yaml`, including the stray `debian-12-docker` the
survey flagged: step 4b's third repository, done. The fleet is now 42
references in 12 repositories, and `actions` is off the compliance
page. Step 4b covers two repositories now. This is the fourth time
this plan has watched the daily audit clear work it had scheduled --
after development's own four via #119, `instar` and `kerbside` --
which is the argument for 4j grepping the fleet rather than reading
the table above.

**`instar` already carries its marker, and it has never worked.** The
survey recorded instar's `image: 'debian:12'` as needing decision
4.3's exception, and step 4d as written says to add one. One is
already there, with a better reason than 4d proposed, at
`.github/workflows/functional-tests.yml:588` -- nine lines above the
finding at `:597`, written against the whole matrix entry.
`is_excepted()` reads the finding's own line and exactly one line
above it (`scripts/audit/checks/distros.py:275-279`), so it has never
applied, which is why instar is still on the compliance page having
moved its runner pool on 2026-09-16. That file last changed
2026-09-18, so this was true when the survey ran: the survey read the
scan's output, which reports the finding, and did not read the lines
around it. A scan that filters exceptions silently cannot tell a
missing marker from a misplaced one, and neither could the survey.
Step 4d moves the marker rather than adding a second.

**A trixie under-cloud breaks every instance's agent, which blocks
half this phase.** (Superseded 2026-09-24 -- the root cause was qemu
SPICE packaging, not the agent, and shakenfist#4309 closed it; the
canary evidence below still stands, the diagnosis in this heading does
not, and the 1820-second figures were inflated by the
`is_powered_on()` bug the amendment describes.) shakenfist#4280, filed
2026-09-20 03:53 out of the canary `actions` ran for its own
migration. Instances booted on a Debian 13 hypervisor never reach
agent ready -- `agent_state: "not ready (no contact)"`,
`agent_start_time: null` -- and the three `test_agentop_deadlines`
tests went from 137, 192 and 207 seconds to roughly 1820 each,
consuming the step's whole 45-minute budget so that the rest of the
suite never ran. Two canary runs six hours apart on the same topology,
`35471083618` green and `35483225701` red, with the under-cloud image
the only difference between them. `actions` reverted its own default
in `2e0d32a` ("Keep the under-cloud on bookworm.") and wrote the
evidence into the input's comment at
`.github/workflows/smoke-cluster.yml:55-65`. This plan does not fix
it: D5's reasoning applies, and a plan that closes a migration is not
the place to debug a hypervisor's side channel.

**The blocker splits the guest-image half in two, and only one half
is stuck.** (Superseded 2026-09-24 -- shakenfist#4309 closed the
block; the under-cloud/guest separation below survives and is
load-bearing, the present-tense claims about being stuck do not. See
the amendment under *Amended 2026-09-24*.) The survey treated "the
guest images the CI clusters boot" as one thing. #4280's evidence
separates them, and that separation is the useful part of it:

* **The under-cloud image** -- `base_image`, what the hypervisor VMs
  themselves boot -- is blocked. That is 4f's two defaults
  (`build-smoke-cluster/action.yml:32` and
  `.github/workflows/smoke-cluster.yml:67`, both renumbered since the
  survey) and 4g's item (1), the six `base_image` sites in
  `shakenfist`. None of them moves until #4280 closes.
* **The uploaded guest artifact** -- `sf://upload/system/debian-12`,
  what instances inside the nested cluster boot -- is not known to
  be blocked. #4280 says so explicitly, and the shape of its evidence
  is what makes the separation usable: both canary runs uploaded the
  same bookworm artifact, so the guest was the controlled variable
  rather than the suspect. Read what that does and does not settle.
  It rules the guest out as the cause of #4280; it says nothing about
  whether a *trixie* guest works, because no canary ran one. So what
  is unblocked is the rename -- 4f's release-neutral upload, 4g's
  items (2) to (4), and 4h -- and not, on this evidence, the content
  change 4f currently carries with it by sourcing the new name from
  `/srv/ci/debian:13`. Whether those two should land together is back
  brief question 4, which is where it is decided rather than here.

Decision 4.5 is what makes that separation survivable.
`sf://upload/system/debian` asserts no release, so the references
can be renamed while #4280 is open and the artifact's contents can
follow later without a second fleet-wide edit. The decision was
argued for the next desktop bump; it earns its keep sooner than that.

**Amended 2026-09-24: shakenfist#4280 is fixed, and the deferral
above is withdrawn.** shakenfist#4309 ("Start instances on Debian 13
hypervisors.") merged 2026-09-23 19:28 UTC and closed it. Read the
root cause before re-reading the deferral, because it is not what
the 09-21 amendment assumed and that decides how much of the
amendment survives:

* **The bug was in provisioning, not in the agent.** Debian 13, like
  Ubuntu 24.04, ships qemu's SPICE support in a separate
  `qemu-system-modules-spice` package. `roles/node/tasks/bootstrap.yml`
  installed it only on Ubuntu 24.04, so on a trixie hypervisor libvirt
  refused every domain definition -- `spice graphics are not supported
  with this QEMU`, 72 times in the libvirtd journal of run
  `35483225701`, which is the same red canary the amendment above
  cites. No instance ever started, so no agent was ever in a position
  to make contact. The fix installs the package wherever apt has it
  rather than enumerating releases, which covers the next one too.
* **A truthiness bug is what hid it.** `Instance.is_powered_on()`
  returned the string `'off'` when libvirt had no domain, and a
  non-empty string is true, so `create()` marked instances whose every
  power-on attempt had failed as `created`. That is why the suite
  waited out the full agent timeout instead of erroring, and it is
  the 1820-second `test_agentop_deadlines` figure the amendment
  recorded as evidence about the agent. #4309 returns `False` for a
  missing domain and adds five tests, two of which fail when the old
  return is restored. The further power-state defects that audit
  found are shakenfist#4307 and are not this plan's problem.

**What this restores.** The under-cloud half moves in this phase as
originally planned: 4f's two defaults and 4g's item (1). The eight
sites are listed here, re-read on 2026-09-24 against each
repository's default branch, and this list rather than the
definition of done is the record of them --
`actions` `build-smoke-cluster/action.yml:32` and
`.github/workflows/smoke-cluster.yml:67`, and `shakenfist`
`functional-tests.yml:443`, `:463`, `:479`, `:517` and
`scheduled-tests.yml:42`, `:52`. None of them acquired the comment
the deferral would have required, because no step ran; what exists
is the two comments `2e0d32a` wrote in `actions`, which 4f now
deletes with the default they explain rather than levelling up.

**Where the phase had got to when this landed.** 4a to 4e opened
thirteen pull requests across thirteen repositories. Ten had
merged by 2026-09-24 01:00: agent-python#140, clingwrap#136,
library-utilities#60, client-python#405, divergulent#117, sfui#35,
client-python-k3s#67, ryll#397, instar#589 and
kerbside-patches#1734. Two were still in flight with no failing
check: occystrap#143 and shakenfist#4306. The thirteenth,
visual-digest-rust#23, did not merge and will not: that
repository's CI had never run since its default branch was
renamed, because `ci.yml` still triggered on `main`
(visual-digest-rust#24), and its own #25 then fixed the trigger
and moved the runner label in one change, so #23 was closed as
superseded. Read that as the label half being done there rather
than as a step being skipped -- the label moved, by a different
pull request than this plan named, which is exactly the kind of
claim 4j's declaration grep exists to check rather than take on
trust. 4f does not wait for the two in flight: it is in `actions`
and neither of them touches that repository. 4g does wait for
shakenfist#4306, because both edit `functional-tests.yml`; the
wait is for review cleanliness rather than for correctness, since
4e changes runner-label tokens in place and renumbers nothing, so
item (1)'s six line numbers survive it either way.

**Amended 2026-09-26.** All three of those have since landed:
occystrap#143 at 2026-09-24 19:36 UTC, shakenfist#4306 at 2026-09-24
23:23 UTC, and 4f itself as actions#97 at 2026-09-24 19:35 UTC (merge
`8c02ab0e7`). The label half of this phase is therefore complete in
every repository and 4g's wait for shakenfist#4306 is discharged --
the only wait that sentence means; the next paragraph closes a
different one. The paragraph above is left as the snapshot it was,
because the survey's dated claims are read as evidence of when a thing
was true rather than as current state.

**4f merged and its verification has not passed. 4g does not start
until it does.** 4f's brief makes two post-merge runs its own
responsibility and says a failure in either is a revert of the
default-move commit. One of the two has run and failed:
`kerbside`'s nightly `sf-e2e-functional` on `develop`, run
36110697157 on 2026-09-25 08:01 UTC, the first scheduled run after
4f merged. The two before it, on 09-23 and 09-24, both passed. The
other run, `shakenfist`'s node-lifecycle job on `develop`, has not
been triggered at all, so it is unknown rather than green.

What the run log establishes, read rather than inferred: the
cluster built and deployed normally, and it took 4f's new default
-- the `build-smoke-cluster` step shows `base_image:
sf://label/ci-images/debian-13` and both uploads, the old
`debian-12` name and the new `debian` one. The failure is later,
in `import-instance.sh`: a guest booted inside the nested cluster
reached `state=error` with `power_state: off`, `error_message:
null` and `video: {model: cirrus, vdi: spice}`.

What it does not establish is the cause, and the distinction
matters because this looks like shakenfist#4280 and is not it.
#4309's fix is in the deployed collection -- version
`0.8.0-rc5.dev1385+g4404669e6`, and `de5e3833c` is an ancestor of
it -- and the task it added ran and did its work: *Install SPICE
modules where they are packaged separately* reports `changed:
[primary]`. So the missing package is not the explanation this
time. The other half of #4309 is a plausible reason this is
visible now: before it, `is_powered_on()` returned the truthy
string `'off'` for a missing domain, so an instance that failed
this way was marked `created` and the caller waited out a timeout
instead of erroring. A failure that used to present as a hang now
presents as `state=error`, which is an improvement in reporting
and not evidence of a new fault.

**The run's own artifacts cannot take this further, which is its
own finding.** `kerbside`'s `tools/sf-e2e/gather-artifacts.sh`
collects `sf-api` and `sf-console` journals only. The domain
definition happens in the node daemon and in libvirtd, and neither
journal is collected, so the error #4280's evidence turned on --
libvirt refusing a domain -- cannot be read out of a failed run at
all. `sf-api.journal` carries no `ERROR`-level line for the
instance; what it shows is a scheduler admitting it only after
waiving the demand guard, which is capacity pressure and is
kerbside#284's subject rather than this phase's.

So the state of 4f is: landed, fleet-wide for every consumer
pinning `@main`, with one of its two verification runs failing for
a reason not yet attributed and the other not yet run. That is a
decision for Michael -- revert 4f's default-move commit, or
diagnose first -- and not one an implementer picks up from this
table. kerbside#482 was auto-filed for the failing nightly on
2026-09-25 and is where the diagnosis belongs.

**What it does not restore.** The 09-21 amendment separated the
under-cloud image from the uploaded guest artifact, and that
separation stays: they are two images with two consumers, and 4f is
still additive for the artifact because a rename and a content
change are still different things. What expires is only the claim
that the under-cloud cannot move. Back brief questions 4 and 5 were
both premised on #4280 being open and are answered there.

**Four numbers drifted while the phase waited, all in
`shakenfist`.** Three are in `functional-tests.yml` and all moved by
the same two lines: the matrix lane in 4g's item (2) was `:475` and is
`:477`; the node-lifecycle caller in 4f's brief and in the definition
of done was `:558` and is `:560`; the upload in 4g's item (4) was
`:583` and is `:585`. The fourth is `deploy/nodelifecycletests.sh`,
which moved further and twice: `7e9c1cd45` took the upload reference
from `:132` to `:187` on 2026-09-22, and `f0bf8c67a` took it from
`:187` to `:217` on 2026-09-24 at 20:21 UTC. Every content at those
sites is unchanged. The first version of this paragraph named only the
lane while the same commit silently moved the upload, and left the
node-lifecycle number stale while claiming it had been re-read --
which is the defect this paragraph exists to document, committed
inside the document that documents it. The `:558` error is worth its
own sentence because of how it happened: the job's step name is at
`:558` today and its `uses:` line at `:560`, so a re-read that stops
at the first plausible line confirms the old number instead of
checking it. The `nodelifecycletests.sh` number is worth its own
sentence for the opposite reason: `:187` was correct when this
branch's second commit wrote it at 19:42 UTC on 2026-09-24, and
`f0bf8c67a` invalidated it thirty-nine minutes later. A re-read is not
a fix when the file it reads is under active development on a
timescale shorter than a review round -- which is why item (3) now
carries the grep that finds the line rather than the line, and why no
step brief in this plan carries a line number for that file -- the
survey's dated snapshot still records `:132`, as a snapshot rather
than as an address. This is the fourth to seventh time a line number
in this phase has moved under ordinary work in that repository. The
survey's own numbers are left as it recorded them on 2026-09-19 --
`:477`, `:515`, `:558`, `:583` and `nodelifecycletests.sh:132` --
because that section is a dated snapshot and renumbering half of it
would make it disagree with itself.

**Amended 2026-09-29: 4f's verification has passed on both runs, and
4g's hold is discharged.** Neither run needed a revert, and the
failure the 09-26 amendment could not attribute has not recurred.

`kerbside`'s nightly `sf-e2e-functional` on `develop` has passed three
consecutive times since the failure that amendment records -- runs
36228146379 (2026-09-26 07:53 UTC), 36306108473 (09-27 08:24) and
36399738723 (09-28 08:50), against the failure at 36110697157 (09-25
08:01). Three passes over a trixie under-cloud with no change to the
code between them is what makes the 09-25 run transient rather than a
regression from 4f's default move. It is not a diagnosis, and
kerbside#482 stays open for one; what it settles is the decision the
09-26 amendment parked with Michael, because there is now nothing for
a revert to fix.

`shakenfist`'s node-lifecycle job on `develop` -- the run that
amendment records as never triggered -- has since run twice and
passed twice: merge_group runs 36501556771 (2026-09-29 00:27 UTC) and
36513297089 (02:41), each a full six-host cluster build of about
fifty-eight minutes. It did not need the manual dispatch that
amendment anticipated, because ordinary merge traffic supplied it.

**That it ran green at all is the second half of this amendment, and
it took a fix.** Between 4f merging and those runs, every
`merge_group` run of that job failed, and the plan did not know
because of where the job is gated: `node_lifecycle_collection` runs on
`merge_group` and `workflow_dispatch` only, and skips on
`code_changed != 'false'`, so pull request CI structurally cannot
reach it and a docs-only merge skips it. Green pull requests and green
docs merges concealed a failure on every code merge for four days.

The cause was in `actions`, not in this repository, and it was a
design mismatch rather than a regression. The mesh-interface task in
the three multi-node topology playbooks inferred a netplan renderer
from `ansible_distribution_version | int > 11`, but the Debian 13 CI
image is deliberately `systemd-networkd`: trixie dropped `ifupdown`
from the default install, cloud-init detects that and writes to
`/etc/systemd/network/`, and `debian-13-extras` installs no netplan
for the playbook to call. Two factors had to coincide to break a job,
which is why exactly one broke -- taking 4f's unpinned default *and*
configuring a mesh. The other two unpinned callers, `kerbside`'s
`sf-e2e-functional` and this repository's canary, build `localhost`
topologies with no mesh; the four pinned matrix lanes configure a mesh
but boot bookworm or Ubuntu.

actions#112 replaced the inference with detection -- probe
`/usr/sbin/netplan`, set a `mesh_style` fact, and carry an `ifupdown`,
a `netplan` and a `networkd` arm -- and added an ungated assertion
that the mesh address is actually up before the play proceeds, which
is the post-condition whose absence let a missing mesh address present
as a healthy play and a cluster unable to reach its database. It
merged at 2026-09-29 00:03 UTC as `6b57319f1`. Run 36513297089's log
is the evidence that it works rather than merely passes: the detection
returns `ok` on all six hosts, the `ifupdown` and `netplan` arms skip
on all six, the `systemd-networkd` arm reports `changed` on all six,
and the assertion returns `ok` on its first attempt.

**The blind spot this exposed outlives the bug** and is phase 6's
business rather than this phase's, so it is recorded and not fixed
here: no workflow in `shakenfist/actions` builds a `slim-primary` or
`slim-tier` cluster, so that repository's own CI cannot exercise a
mesh at all, while every consumer pins `@main` and takes each merge
live fleet-wide immediately. A green pull request there is not
evidence about the multi-node path, and this is the second time in
this phase that the verification a change needed lived in a different
repository from the change.

#### Decisions

Numbered `4.N` for the same reason phase 3's are numbered `3.N`:
this plan has a top-level `D1` to `D6`, and "decision 4.2" and "D2"
should not be confusable.

4.1. **One pull request per repository, grouped into steps by
   size.** Each carries its own `actionlint.yaml` edit and is
   reviewed against the workflows it touches, which is what the
   master plan asked for. The steps group repositories only so that
   one sub-agent can carry several trivial ones; the pull requests
   stay separate, because the consistency issue they close is
   per repository and so is the CI that proves the move worked.

4.2. **The retired label leaves `actionlint.yaml` in the same
   commit as the last workflow line that names it.** That list is
   the set of labels a workflow *may* name, so leaving `debian-12`
   declared after the last user is gone lets the next workflow name
   a retired label and pass lint -- which is exactly how
   `occystrap` and `shakenfist` grew new findings this fortnight.
   This repository already took that decision for itself and wrote
   the reasoning into `.github/actionlint.yaml:15-20`: "The bookworm
   labels are deliberately absent ... Leaving them undeclared is what
   gives actionlint something to say about it". So this is the fleet
   applying a convention it has already adopted at the source of the
   templates, rather than a judgment call being made fresh.
   The cost is that a revert needs the declaration back, and a
   reviewer may reasonably prefer to keep the declarations until
   phase 5 retires the labels themselves. Taken anyway: the lint is
   the only thing standing between a fleet-wide convention and the
   next copy-pasted workflow, and a revert that needs two lines is
   not a hard revert.

4.3. **`instar` is marked, not migrated.** Its one remaining
   finding is `image: 'debian:12'` at
   `.github/workflows/functional-tests.yml:597`, test input in a
   matrix that deliberately covers several releases. The criterion
   describes this case and provides the marker for it; use it, with
   the reason on the line.

4.4. **The guest-image half is in scope for this phase.** It is not
   what the master plan's section described, and it roughly doubles
   the phase. It is in anyway, because D4 retires producers only
   after their consumers, and phase 5 removes `debian-12` from
   `IMAGE_BUILDS` and `CI_IMAGES`: every site listed in the survey
   above would then name a label the conductor does not build. The
   alternative -- a phase 4a for the invisible half -- was rejected
   because it separates two halves of one repository's migration
   into two plans, and `shakenfist` has both.

4.5. **The uploaded artifact gets a release-neutral name.** The
   smoke cluster uploads the cached image into itself as
   `debian-12` and 43 test references read it back by that name. The
   obvious move is `debian-13`, and the obvious move buys another
   43-reference edit at the next release. Phase 3 reached the same
   fork with the gnome snapshot and took the neutral name
   (`debian-gnome-agents`), which is why the next desktop bump is
   not a fleet change; take it again here. The artifact becomes
   `sf://upload/system/debian`, the release survives only in the
   path the action copies from (`/srv/ci/debian:13`), and the tests
   stop naming a release they do not care about. This is the
   decision a reviewer is most likely to argue with, because a test
   that says `debian` no longer says which Debian it exercised --
   the answer is that it never did: the name said 12 while the
   bytes were whatever the cache disk last cached, which for
   `debian-gnome:12` was Debian 11 for two years (private-ci#38).

4.6. **Repositories outside the audit matrix are surveyed here and
   watched in phase 6.** The survey is above; widening the matrix
   changes a workflow every repository's compliance depends on, and
   phase 6 is the phase that owns the audit's blind spots.

4.7. **The frozen cached-image list is not touched.** `ubuntu:20.04`,
   `debian:11` and `fedora:40` stay in `ci-dependencies.yml`'s cache
   list, and `debian:12` stays there and becomes frozen alongside
   them rather than being removed: the cache is what lets a test boot
   an old guest deliberately. No step edits that file.
   Retirement is phase 5's and private-ci#38's.

#### Step plan

Every step that edits a repository other than this one opens a pull
request there and waits for that repository's own CI. Steps 4a to
4e are independent of each other and of 4f to 4h; within 4f to 4h
the order is a real constraint and is stated in each brief. No step
prunes or regenerates `REVIEWS.md` (see the phase landing shared
block in `PLAN-TEMPLATE.md`).

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | low | sonnet | worktree | Six repositories, one pull request each, all the same shape. `shakenfist/agent-python` (`.github/workflows/functional-tests.yml:25`, `release.yml:71`), `client-python` (`release.yml:73`), `clingwrap` (`functional-tests.yml:22`, `release.yml:73`), `divergulent` (`release.yml:71`), `library-utilities` (`release.yml:81`), `visual-digest-rust` (`ci.yml:16`). Every one is a literal `runs-on: [self-hosted, ..., debian-12, ...]`; change that token to `debian-13` and leave the size token and everything else alone. Then `.github/actionlint.yaml`: add `debian-13` to `self-hosted-runner: labels:` in agent-python, clingwrap and visual-digest-rust (the other three already declare it), and delete `debian-12` from all six per decision 4.2. Re-read the repository's consistency issue first for the current line numbers -- the audit refiles daily and the numbers here are from 2026-09-19. Commit subject in each: "Move CI onto the Debian 13 runner pool." **actionlint passing is not the verification**: wait for the repository's own CI to run a job on the new label and pass, because the label provisions a different image and this is the step that finds out whether anything in it was load-bearing. Do not touch `REVIEWS.md`. |
| 4b | low | sonnet | worktree | Two repositories, same shape as 4a, separated only because each has more than two lines. `shakenfist/sfui` (`functional-tests.yml:18`, `:51`) and `client-python-k3s` (`functional-tests.yml:103`, `:225`, `release.yml:73`, `supply-chain.yml:67`). Both already declare `debian-13`; delete `debian-12` from each `actionlint.yaml`. Same commit subject and same verification as 4a. **`shakenfist/actions` was the third repository here and is already done** -- `e2a56bd` moved its three `runs-on:` lines and deleted both retired declarations, the stray `debian-12-docker` included, on 2026-09-20 in answer to actions#69. Confirm that rather than assume it, because this step's original brief is the record of what was needed. The check is `grep -rn 'debian-12' .github/` in a fresh clone, and the one hit it should return is the under-cloud default at `.github/workflows/smoke-cluster.yml:67`, which is a guest image rather than a runner label and was blocked by shakenfist#4280 at the time -- do not "finish" the migration by moving it. **Amended 2026-09-24: shakenfist#4309 closed that bug and 4f moves that default.** 4b has merged and this brief is the record of what it needed; read the instruction not to touch the hit as scoped to 4b, not as advice to 4f. That was still one hit on 2026-09-21: `2e0d32a` wrote an eleven-line reason above that input at `:55-65`, and it quotes `ci-images/debian-13`, the move it is refusing, rather than the `-12` default below it -- the shape 4g's item (1) was going to copy to the other six sites while the deferral stood, and no longer does. Read the hit rather than the count, though: a hit in a `runs-on:` line, or a `debian-12` list item in `.github/actionlint.yaml`, is what a partial label migration looks like and is what this step completes. A hit inside a comment is a reason, not a finding. Do not re-edit what is already correct. |
| 4c | medium | sonnet | worktree | The two `*-docker` repositories, one pull request each. `shakenfist/ryll`: five `debian-12` (`ci.yml:209`, `:290`, `:351`, `:385`, `supply-chain.yml:48`) and twelve `debian-12-docker` (`ci.yml:83`, `:115`, `:138`, `:235`, `fuzz.yml:60`, `manual-build.yml:99`, `mermaid-lint.yml:77`, `release.yml:76`, `:272`, `:319`, `:392`, `supply-chain.yml:66`). `shakenfist/occystrap`: four `debian-12` (`functional-tests.yml:50`, `python-unit-tests.yml:47`, `release.yml:71`, `supply-chain.yml:76`) and one `debian-12-docker` (`mermaid-lint.yml:77`). Both declare `debian-13` but neither declares `debian-13-docker`; add it, and delete both Debian 12 declarations. Medium rather than low because these are the repositories whose jobs actually use the docker daemon: the `debian-13-docker` image ships `docker.io` *and* `docker-cli` only since shakenfist/actions#66, and the build runs `docker version` so a broken image fails rather than publishing -- so if a docker job misbehaves on the new label, report it rather than working around it, because it means that fix regressed. Commit subject: "Move CI onto the Debian 13 runner pool." |
| 4d | low | sonnet | worktree | **The repositories no other step edits**, as two pull requests. First, one line in `shakenfist/instar`. `.github/workflows/functional-tests.yml:597` is `image: 'debian:12'`, deliberate test input in a matrix that covers several releases (decision 4.3). **A marker already exists and does not work; move it, do not add a second.** `:588-595` is a comment beginning `# audit-ok: eol-distro -- Debian 12 is a SUPPORTED TARGET here,` which explains the matrix entry better than any wording this plan would have proposed. A range rather than a length, because the range is self-checking against the `:596`/`:597` anchors below it: re-read on 2026-09-21 at instar `e3239f5`, it is eight lines, and an earlier draft of this brief said eleven, which is `actions`' block at `smoke-cluster.yml:55-65` and would have swallowed both anchors. It has never taken effect, because `is_excepted()` reads the finding's own line and exactly one line above it (`scripts/audit/checks/distros.py:275-279`) and this marker sits nine lines up. Keep the prose where it is -- a reader needs it at the top of the entry -- and move a marker **carrying its own short reason** onto `:596`, the `- name: 'Debian 12'` line, or directly above `:597`, indented to match: `# audit-ok: eol-distro -- supported target, see the note above` or similar. Not the bare token. `docs/audits/eol-distro.md:169-176` specifies the shape as "Mark the line, or the line above it, with the reason", and its worked example carries one; a dangling `-- ` satisfies `EXCEPTION_RE` and would leave the fleet's canonical instance of this marker as a token with nothing after it. Check the result with the criterion rather than by eye: re-run this plan's scan snippet against the edited clone and confirm instar returns zero findings. Change nothing else: instar moved its runner labels on 2026-09-16 and this is its only remaining finding. Its `.github/actionlint.yaml` declared `debian-13`, `debian-13-docker` and neither retired label on 2026-09-19, so there is nothing to delete -- but read it rather than trusting that sentence, and if a retired label is declared, delete it in this commit and say so, since no other step in the phase touches this repository. Commit subject: "Put the eol-distro marker where the audit reads it." -- the image has been marked deliberate since before this phase was planned, and what this commit changes is where the mark sits, which is also the general lesson and the second time the fleet has hit it. Second, `shakenfist/kerbside-patches`: it has fourteen workflows, every one of them already on `debian-13`, and its `.github/actionlint.yaml` still declares `debian-12`. It is not one of the repositories the criterion lists, because a declaration produces no finding -- it is decision 4.2's failure state in the repository that most recently migrated, and nothing else in this phase looks at it. Delete the declaration; there is no workflow line to change. Commit subject: "Stop declaring a retired runner label." `private-ci` also declares it and is deliberately left alone: it has no workflows at all, so the declaration governs nothing, and the repository is excluded from this criterion. Say that in the pull request rather than leaving it looking unnoticed. |
| 4e | medium | sonnet | worktree | `shakenfist/shakenfist`, runner labels only. Five lines: `.github/workflows/functional-tests.yml:536`, `:726`, `mermaid-lint.yml:94` (`debian-12-docker`), `pin-indirect-dependencies.yml:55`, `release.yml:105`. `.github/actionlint.yaml` declares neither replacement: add `debian-13` and `debian-13-docker`, delete `debian-12` and `debian-12-docker`. **Runner labels only.** The same workflow file also names the guest image `sf://label/ci-images/debian-12` at `:443`, `:463`, `:477`, `:515`, and those are step 4g -- moving them here would put a guest-image change into a pull request reviewed as a runner move. Medium because this repository's functional tests are the heaviest in the fleet and a provisioning failure here is expensive to diagnose from a red matrix. Commit subject: "Move CI onto the Debian 13 runner pool." |
| 4f | high | opus | worktree | **4f has merged as actions#97 and this brief is the record of what it needed; its post-merge verification has now passed on both runs, and the 2026-09-29 amendment in the survey is the record of that -- the 2026-09-26 amendment above it records the failing state it passed through, and is a snapshot rather than current. Additive for the artifact, not for the default, and it must merge before 4g. Restored 2026-09-24: shakenfist#4309 closed shakenfist#4280, so the two guest-image defaults this step was always going to move are back in it, alongside the release-neutral upload.** In `shakenfist/actions`, `build-smoke-cluster/action.yml`. The action uploads the cached image into the cluster it just built as artifact `debian-12`, and every `sf://upload/system/debian-12` reference in `shakenfist` reads it back -- 4g's item (3) gives the grep that enumerates them and says why the count is not to be trusted. Decision 4.5 moves that name to `debian`, with no release in it. **The upload line does not read the way a grep for the command would expect.** `build-smoke-cluster/action.yml` assembles `sf-client artifact upload` into a `${setup}` shell variable at `:260` and invokes it at `:263`, which literally reads `"${setup} debian-12 /srv/ci/debian:12 --shared --no-checksum"`. The artifact name and the source path are on `:263`; the verb is not. Those numbers are from 2026-09-21 and moved by fourteen lines when `e2a56bd` rewrote the comment above them, which is the reminder that they are a grep target rather than an address. Do it in two landings so neither repository is ever reading a name the other does not write: this step adds a *second* upload under the name `debian`, sourced from `/srv/ci/debian:13`, which phase 3 put on the cache disk (`ansible/ci-dependencies.yml:194`); 4h removes the old one after 4g has landed. **`/srv/ci/debian:13` is the right path and `/srv/ci/cached/debian:13` is not**: the builder mounts the dependencies disk at `/srv/ci/cached` and writes `{{item.name}}` into it, while the cluster nodes that run this upload mount the same disk at `/srv/ci` (the `Mount /srv/ci` tasks in the `ci-topology-*.yml` playbooks), so the file the builder wrote as `/srv/ci/cached/debian:13` is `/srv/ci/debian:13` on the node doing the uploading. Do not "correct" the path to the builder's spelling. **Leave the existing `debian-12` upload exactly as it is, source path included.** Additive has to mean the content too: repointing `:263` at `/srv/ci/debian:13` would leave an artifact called `debian-12` containing trixie, and every reference in `shakenfist` would start exercising Debian 13 one merge before the repository whose tests would explain a failure. Decision 4.7 keeps `/srv/ci/debian:12` cached for exactly this. **The two guest-image defaults move in this step.** Restored 2026-09-24. `build-smoke-cluster/action.yml:32` and `.github/workflows/smoke-cluster.yml:67` become `sf://label/ci-images/debian-13`. Unlike the upload, this is not additive and no transitional window covers it: every consumer pins `@main`, so the new default is live fleet-wide the moment it merges. **Delete the two comments `2e0d32a` wrote to explain the old default rather than editing them.** They are not the same comment -- the full evidence sits above the workflow input at `.github/workflows/smoke-cluster.yml:55-65` and names `shakenfist/shakenfist#4280`, while a three-line pointer above the action input at `build-smoke-cluster/action.yml:28-30` sends the reader to the workflow and does not name the issue -- but both explain a refusal this step withdraws, and a comment saying the under-cloud is deliberately bookworm sitting above a line that says trixie is worse than no comment at all. An earlier version of this brief had 4f *adding* the issue reference to that pointer, because the definition of done then required each deferred site to carry one; that bullet is gone with the deferral. **`docs/actions.md:202-216` is a third site and no grep for the image string finds it.** It is a prose paragraph, "Two things here are still Debian 12 on purpose", explaining both the default this step moves and the artifact name 4f to 4h rename, and it links #4280. Both of its halves stop being true in this phase, so rewrite it here rather than leaving a document that contradicts the file it documents -- and say in it that the artifact rename is in flight rather than done, because 4g and 4h have not landed when this does. Confirm the sweep with the **bare** `grep -rn --exclude-dir=.git 4280 .` over a fresh `actions` clone, prose included, which must return no reference to the issue -- read each hit, because a coincidental four-digit match (a byte count, a port, an abbreviated sha) is not one. It is the `shakenfist#` anchor that is being dropped here, not the `.git` scoping that every other gate in this plan carries: a clone's history still holds `2e0d32a`'s message and the deleted comments, and `grep -rn` over a pack file reports a binary match rather than nothing. Bare rather than anchored here, for the reason the deleted text gave and this amendment nearly lost: the epoch-timestamp collision that makes anchoring necessary is in `shakenfist`, not in this repository, so a bare grep over one clone is the wider net and costs nothing. It matters most at `docs/actions.md`, where the reference is a markdown link carrying both the `shakenfist/shakenfist#4280` text and the issue URL -- an anchored pattern happens to match the text form today, and would stop matching if the link text were ever shortened to the URL alone. The anchored form stays where the plan uses it as a fleet-wide set check. The release-neutral upload above is the guest artifact, which #4280's evidence held constant and still does. Every consumer of this repository pins `@main`, so what does land here lands for the whole fleet the moment it merges; say so in the pull request. **After this merges, trigger `kerbside`'s `sf-e2e-functional` and `shakenfist`'s node-lifecycle job on their default branches and read both runs to completion.** They are the two consumers that take the guest-image default without passing one, and neither repository's own diff shows the move: no step in this phase touches `kerbside` at all, and 4g's edit list does not reach the node-lifecycle job. A failure in either is a revert of this commit. Both runs are a definition-of-done bullet and this sentence is what causes them; the paragraph below explains why there are two rather than naming them a second time. Each carries the default move as well as the upload, so a failure has two suspects rather than one -- the guest the instances boot is held constant, so the run isolates the guest but not the run: read which image the under-cloud booted out of the run log before concluding anything about the upload. The deferral would have needed one run now and one after the block cleared; this needs two now. **The default move is not additive and two consumers take it, which is what the post-merge runs are for.** `shakenfist` `functional-tests.yml:560`, the node-lifecycle job, passes only `topology` to `build-smoke-cluster@main` and so takes the default; it is not in 4g's edit list and no other step in this phase reaches it. `kerbside` `sf-e2e-functional.yml:104` does the same, in a repository no step here touches. That is what the bolded instruction above asks for, and it asks for a deliberate trigger because waiting for whenever a pull request happens to run them is not the same thing. Both line numbers were re-read on 2026-09-24 and again on 2026-09-26. The gate before editing is a published blob: read the conductor's `sf-client label update "ci-images/debian-13"` line rather than the `IMAGE_BUILDS` entry, because an entry is not a blob and phase 3 lost a night to exactly that distinction. Keep the upload a single commit so its revert is one commit. The transitional double upload this step creates is bounded by 4h, and 4j check (5) is what proves 4h happened. It is not free while it lasts: every smoke cluster build uploads the cached image twice instead of once, against the same primary, so the window costs one extra image copy per cluster rather than one extra name. That is an argument for 4g and 4h following 4f promptly, not for skipping the transition -- the alternative is a flag day across two repositories that pin `@main`. The upload and `${setup}` numbers in this brief were re-read on 2026-09-21 and the two consumer numbers on 2026-09-24 and 2026-09-26; there is no single date for the brief. No consistency issue lists these sites -- they are the half the criterion cannot see -- so locate them with `grep -rn 'ci-images/debian-12' .` and `grep -rn '/srv/ci/debian:12' .`, and treat the one upload and the two defaults as the expected result of those greps rather than as the instruction. Grep the source path rather than the command: the literal string `artifact upload debian-12` appears nowhere in the fleet, for the `${setup}` reason above, so a grep for it returns nothing and would license the conclusion that this step has nothing to do. Two commits in one pull request: "Upload the cluster base image under a release-neutral name." for the upload, and "Boot smoke clusters on Debian 13." for the two defaults, the two comments they carried and the `docs/actions.md` paragraph. **The paragraph documents both halves while sitting entirely in the second commit, so reverting that commit does not restore a consistent document**: it brings back prose calling the artifact name deliberately Debian 12 while the neutral `debian` upload from the first commit is still in place, which is the contradiction the rewrite exists to prevent, reached by following this brief. A revert of the default-move commit must therefore re-correct the artifact half of the paragraph by hand. Splitting the paragraph edit across the two commits would be tidier and was not done, because the two halves are two sentences of one argument and separating them reads worse than the note does. Keep them apart because their reverts are different sizes and different risks -- the upload is additive and reverts to a no-op, while the default move is live for every consumer pinning `@main` and is the one a failing `kerbside` run sends you back to. |
| 4g | high | opus | worktree | **The hold is discharged: 4f's verification passed on both runs and 4g may start.** 4f merged as actions#97 on 2026-09-24; the 2026-09-26 amendment in the survey records its verification as failing and the 2026-09-29 amendment records it passing, which is the one that is current. The default move is not being reverted, so this brief is followed as written and item (1) needs no re-planning. kerbside#482 stays open for a diagnosis of the single transient failure, and nothing in 4g waits on it. One thing did change under 4g while it was held: the mesh-interface task in `actions` now detects the network renderer rather than inferring it from the release, which is what makes a trixie multi-node cluster work at all -- so the post-merge run this brief calls the real evidence is now testing 4g's edit rather than that bug. **Item (1) was blocked by shakenfist#4280 and was restored 2026-09-24 when shakenfist#4309 closed it.** `shakenfist/shakenfist`, the guest-image half, in one pull request but not one commit. (1) **Restored 2026-09-24.** The four `base_image: 'sf://label/ci-images/debian-12'` in `.github/workflows/functional-tests.yml` (`:443`, `:463`, `:479`, `:517`) and the two in `.github/workflows/scheduled-tests.yml` (`:42`, `:52`) are the under-cloud the hypervisor VMs boot. They become `sf://label/ci-images/debian-13`, `base_image_user` staying `debian` at each site. All six line numbers were re-read on 2026-09-24 and again on 2026-09-26, and are current as of the later date. shakenfist#4280 blocked this for three days and shakenfist#4309 fixed it by installing `qemu-system-modules-spice` on trixie, so what had failed was provisioning rather than the agent and nothing about these six lines was ever wrong. **Write no comment at any of these sites.** The deferral required one at each naming the issue; that requirement went with the deferral, and a comment explaining a bookworm under-cloud above a line that says trixie is worse than none. **Do not write an `audit-ok` token here either.** That was true while the deferral stood and is true now for a reason that outlives it: the criterion's exception is the literal token `audit-ok: eol-distro` (`EXCEPTION_RE`, `scripts/audit/checks/distros.py:197`), `is_excepted()` reads only the finding's own line and the one above it, and `docs/audits/eol-distro.md`'s "What this does not cover" puts guest images outside the criterion deliberately -- so there is no finding here to except and nothing a marker would suppress. If phase 6 widens the criterion, the marker shape for a guest-image site is that phase's decision. **This is the first time the suite runs a trixie under-cloud since the canary that failed.** #4309 is what makes it expected to work, and it landed with unit tests rather than with a green functional run, so the first merge run after this is the real evidence -- a failure there is a finding about #4309 and not about this edit, and the log to read is the libvirtd journal for the domain-definition error #4309 names. (2) The matrix lane names at `functional-tests.yml:441` and `:477` are "Debian 12 cluster" and "Debian 12 tier", and `tools/ci_headroom_harvest.py:134` and `:141` key `BUNDLE_TOPOLOGIES` off those strings *and* off the derived GitHub job names in the same entries; that file's own comment says the derivation would break silently if the names changed. Rename lanes and tool in the same commit, and update `shakenfist/tests/test_ci_headroom_harvest.py:61-62`. (3) Replace every `sf://upload/system/debian-12` reference with `sf://upload/system/debian` -- `git grep -n 'sf://upload/system/debian-12' -- shakenfist/` is what enumerates them, 41 lines in 13 files on 2026-09-26; most are under `shakenfist/deploy/shakenfist_ci/` and `shakenfist/tests/`, and one is `shakenfist/deploy/nodelifecycletests.sh` -- and route them through `CLUSTER_CI_IMAGE` (`shakenfist/deploy/shakenfist_ci/base.py:37`) wherever the file already imports from `base`, so the next release is one line. **Amended 2026-09-25 and again 2026-09-26: four path corrections, one line number withdrawn, and a count correction that was itself wrong and is superseded below, all verified against `origin/develop`.** The source tree is nested -- `deploy/` and `tests/` live under a top-level `shakenfist/` package directory, while `tools/` is at the repository root -- so the paths given without the prefix did not resolve. That is four of them in this item, plus `shakenfist/tests/test_ci_headroom_harvest.py:61-62` in item (2), which took the prefix in the same commit and is confirmed present at that path: the package-level `tests/` is the right one even though the script it covers, `tools/ci_headroom_harvest.py`, is at the root. The count is a count of *lines*, and it already includes `nodelifecycletests.sh` rather than standing beside it, so the old "12 files plus one more" reading double-counted it -- and the directories are now given as the grep that enumerates the references rather than as their scope, because `nodelifecycletests.sh` is a sibling of `shakenfist_ci/` and not a member, so an implementer deriving the grep from the two directory names misses it. The line number for that file is withdrawn rather than corrected: it was `:132` in the survey, `:187` when this branch's second commit corrected it, and `:217` thirty-nine minutes later. The survey's drift paragraph carries the detail. A literal that the constant does not reach is the phase 3 failure this step is repeating on purpose; the definition of done greps for the old name, so leaving any is not passing. (4) `functional-tests.yml:585` uploads the image itself for the node-lifecycle job, the same command as the action's upload line (`:263` on 2026-09-21, and a grep target rather than an address -- `e2a56bd` moved it by fourteen lines): move it to `debian` and `/srv/ci/debian:13` too. Commit subjects, one per numbered item and all four given verbatim rather than inferred, which is this phase's convention everywhere else: (1) "Boot the cluster lanes on Debian 13.", (2) "Rename the Debian 12 matrix lanes.", (3) "Read the cluster image by its neutral name.", (4) "Upload the node lifecycle image as debian.". **Every line number in this brief was read on 2026-09-19 in `shakenfist` and re-read against `origin/develop` on 2026-09-26, except the one `actions` number in item (4), which was re-read on 2026-09-21. No commit in this phase renumbers them -- but ordinary work in that repository does: `functional-tests.yml:477` and `:515` had become `:479` and `:517` by 2026-09-22, which is why item (1) lists them at the later numbers. They are a grep target rather than an address, and that matters most in item (1), which edits six specific lines and has no grep of its own beyond `ci-images/debian-12`. The one `actions` number, in item (4), was re-read on 2026-09-21 after `e2a56bd` moved it. None of these sites appears in any consistency issue, because they are the half the criterion cannot see.** 4g runs after 4e has edited two of the same files and after however much ordinary work has landed in between, so locate the work with `grep -rn 'ci-images/debian-12' .github/workflows/`, `grep -rn 'sf://upload/system/debian-12' .` and `grep -rn 'Debian 12 cluster\|Debian 12 tier' .`, and read the six `base_image` sites and the two lane names as the expected result of those greps: those two counts are stable, nothing in this repository's ordinary work has moved either since the survey, and either of them moving at all means something else edited these sites and is the finding worth halting for. **The `sf://upload/system/debian-12` count is not in that category and is not a gate.** Run `git grep -l 'sf://upload/system/debian-12' -- shakenfist/` and edit what it lists; it was 13 files and 41 lines on 2026-09-26 and both figures move under ordinary refactoring, so report what you find and carry on rather than halting on a disagreement. What proves the item finished is the definition of done's grep for the old name returning nothing, which is a question about the end state rather than about a count agreeing with this document. **Amended 2026-09-26, and this supersedes a wrong correction made on 2026-09-25.** The 09-25 amendment said the "48 in 13 files by 2026-09-22" figure "was never true". It was true. It was measured repo-wide and the correction re-measured it under `-- shakenfist/`, which is a different question, and then reported the disagreement as an error in the original rather than as a difference of scope. Measured properly, with `git grep -h 'sf://upload/system/debian-12' <sha> | wc -l` for lines and `git grep -l ... | wc -l` for files, at the tip of `origin/develop`: 2026-09-19, 43 lines in 12 files repo-wide and the same under `shakenfist/`; 2026-09-22, 48 in 13 repo-wide and 43 in 12 scoped; 2026-09-25, 50 in 13 and 43 in 12; 2026-09-26, 48 in 14 and **41 in 13** scoped. The repo-wide figures run ahead because this plan is itself synced into that repository at `docs/components/development/plans/PLAN-image-supply-chain.md`, so every paragraph written here about the string adds to a count measured there. **Do not use the count as a tripwire.** It has now moved under ordinary work too: `5202774ed` (2026-09-25 19:18 UTC) shared the interface hot plug tests via a mixin, which took `guest_ci_tests/test_agentops.py` and `smoke_ci_tests/test_agentops.py` from five matches each to three and added `shakenfist_ci/instance_hotplug.py` with two. A number that a routine refactor moves twice in a week cannot detect a third party editing these sites, which is the only job the halting rule gave it. What can do that job is the file set and the finishing grep, so both are stated below and the number is not an expectation. High effort because item (3) is forty-odd sites in a test suite whose failures are slow to read, and because item (2) fails silently rather than loudly. Item (1) adds to that rather than replacing it: items (2) to (4) rename what the tests boot and what the tool keys off, item (1) changes what the hypervisors themselves run, and a failing run after this lands has more than one suspect -- which is the argument for reading the run log rather than re-reading the diff. |
| 4h | medium | sonnet | worktree | **After 4g has merged, and gated on a grep rather than on this sentence.** **Two pull requests in two repositories, and the order between them is the point -- amended 2026-09-30.** When this step was written it was one `actions` commit; running 4g found that it also has consumers to move first, and 4g had already merged as shakenfist#4379 (2026-09-29 22:03 UTC) without them, so they are this step's. **(1) `shakenfist/shakenfist`, one pull request, merged before (2) is opened.** Both repositories are consumed at their default branch -- `build-smoke-cluster@main` is live fleet-wide the moment it merges -- so removing the upload first breaks every consumer still reading the old name, which is the flag day 4f split its own work to avoid. The consumers are references to the artifact by its bare short name rather than its URL: the URL form `sf://upload/system/debian-12` is the spelling of the *upload*, and these are invisible to a grep for it and were invisible to all three of 4g's definition-of-done greps. On 2026-09-30 gate B below found seven, all on `origin/develop`: `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_boot.py` carries four -- two `testscenarios` entries, each a scenario name `'debian-12'` and a `'base': 'debian-12'` that is the actual read -- and `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_imagefetch.py` three, each `sf-client artifact download debian-12 ...`. That is what the grep found on that date, not an expectation: edit what gate B lists when you run it, and report a disagreement rather than halting on it, as 4g's brief says of its own count. Move each read to `debian`, and rename the two scenario names with them so a test id does not name an image it no longer boots. The `/var/www/html/debian-12-...` paths in `test_imagefetch.py` are filenames the test writes, not reads of the artifact, and gate B deliberately does not report them. They pass today only because `build-smoke-cluster` still uploads both names, so this pull request's own CI proves the new name is read and cannot prove the old one is unread -- gate B is what proves that. Commit subject: "Read the cluster image artifact as debian, not debian-12." **(2) `shakenfist/actions`, after (1) has merged.** Remove the pre-existing `debian-12` upload that 4f deliberately left in place in `build-smoke-cluster/action.yml`, leaving only the `debian` one that 4f added. 4f adds a name and removes none; this step removes the old name. If the diff you are about to write deletes the line that says `debian`, you have the wrong one. Rewrite the comment block above the upload in the same edit: it explains the two-name window and quotes the old URL, and it is gate A's one expected hit. The rewritten comment names neither the old URL nor the two-name window, so gate A returns nothing after this edit -- the definition of done asks for exactly that, and a comment saying "formerly `sf://upload/system/debian-12`" fails it at 4j. **Before editing (2), run both gates over fresh clones of every non-archived repository in the organisation** -- not just `shakenfist`, which is where 4g worked -- **and stop and report instead of editing if either shows a read of the artifact beyond its expected residue.** Run them from a directory that contains only the clones, and pass `*` rather than `.`: the filters below anchor on `repo/path`, and whether `grep -r .` prefixes its output with `./` depends on the grep version. **Gate A, the URL:** `grep -rnI --exclude='*.md' --exclude-dir=.git 'sf://upload/system/debian-12' *`. Expected residue on 2026-09-30: one line, the comment in `actions/build-smoke-cluster/action.yml` (`:241` on that date) that this edit rewrites. **Gate B, the short name:** `grep -rnIE --exclude='*.md' --exclude-dir=.git '(^|[^[:alnum:]_/.-])debian-12([^[:alnum:]_.-]|$)' * | grep -vE -e '^(development/scripts|private-ci/conductor)/' -e '^[^:]*/actionlint\.yaml:' -e '^[^:]+:[0-9]+:[[:space:]]*(-[[:space:]]*)?runs-on:' -e '^[^:]+:[0-9]+:[[:space:]]*#'`. Expected residue on 2026-09-30 once (1) has merged: two lines, the upload itself at `actions/build-smoke-cluster/action.yml:267`, which this edit deletes, and a docstring in `shakenfist/tests/test_mariadb_capacity_admission.py` (`:1210`) that names the runner. Before (1) merges the seven above are in it too. **Read gate B's output by hand rather than comparing it to zero** -- a boolean is what hid the seven -- and treat anything that reads the artifact as a halt. What each filter subtracts, so a disagreement can be diagnosed rather than filtered further: the token pattern admits only a whole `debian-12`, not preceded by `/` (so neither `ci-images/debian-12`, the under-cloud label, nor the URL, which is gate A's) and not followed by `-` or `.` (so neither `debian-12-docker`, `debian-12-sfagent` and the other image and bundle names, nor a `.qcow2` filename). The upload is named exactly `debian-12`, so a longer token is a different label, image or file, not this artifact. `/srv/ci/debian:12` does not contain the token at all and is 4j check (5)'s. The two directory excludes are the audit's runner-label checks and fixtures in `development` and `private-ci`'s runner image builder, whose `IMAGE_BUILDS` name is a runner label -- 83 lines between them on 2026-09-30, every one a runner label and phase 5's. They are the one place this filter could hide a consumer, which is why there are exactly two and why they are directories that build runners rather than smoke clusters; do not add a third to make the output shorter. `actionlint.yaml` and a `runs-on:` key are runner labels and 4j check (4)'s; the `runs-on` filter is anchored as a YAML key, optionally a list item, so a code line that merely mentions `runs-on` is still reported (anchored and unanchored returned the same lines on 2026-09-30). A line whose first non-blank character is `#` is a comment, out of scope for the same reason prose is; a trailing comment on a code line is still reported. Checked 2026-09-30 by eleven mutations over the clone set: a `.j2` under `actions/ansible/`, an extensionless script, a `Makefile`, a workflow list item, a double-quoted string, a code line with a trailing comment and a file in `private-ci` outside `conductor/` are each reported; a `-docker` variant, the label URL, a comment line and a markdown file are not. Both gates take `-I`, so they differ only in their pattern: the artifact is read by source, and a binary fixture that happened to contain the string would print a `Binary file ... matches` line that is not a read. **Gate B sees only a literal `debian-12`.** A name assembled at runtime -- `f'debian-{release}'`, `debian-${VERSION}`, `'debian-' + release` -- is invisible to both gates, so also run `grep -rnIE --exclude='*.md' --exclude-dir=.git "debian-[\${'\"]" *` and read it by hand the same way; the bracket expression is deliberate, because inside double quotes a `\$` in an alternation reaches grep as a bare `$`, an end-of-line anchor, and silently stops matching `debian-${VERSION}`. Checked by mutation: `f'debian-{release}'`, `'debian-' + rel`, `"debian-" + x` and `debian-${VERSION}` are reported, `debian-12` and `debian-gnome` are not. On 2026-09-30 it returned three lines, none a read of this artifact: the Jinja-templated GNOME download name at `actions/ansible/ci-dependencies.yml:308`, and two `'debian-'` prefix tuples in `divergulent`. A name built further from its parts than that is beyond any grep; the backstop is the CI of the consumers (1) moved. That gap widens over time rather than closing: 4g's advice to route reads through `CLUSTER_CI_IMAGE` is advice to stop spelling the name literally. Both gates take `--exclude='*.md' --exclude-dir=.git`: this plan is in one of those clones and names the string a dozen times, and a gate that halts on its own plan file is a gate an agent learns to override. Prose is out of scope here for the same reason `eol-distro` exempts it -- a document describing a migration is not a dependency on it. **Exclude prose rather than allow-listing extensions.** An allow-list of `.py`, `.yml`, `.yaml` and `.sh` reads well and silently drops `.j2` -- of which `actions/ansible/` is full -- along with extensionless scripts and `Makefile`. This gate's failure is destructive by omission: it deletes the upload while a consumer it could not see still reads the name. Phase 3's retrospective is that a gate stated in a plan file is not a gate; this one is a command whose output decides the step. **Drop the "in flight" qualifier from `docs/actions.md` in the same commit.** Find it by content rather than by its opening words: grep `docs/actions.md` for `in flight` on `actions`' default branch, which is the paragraph 4f wrote about the guest-image upload. (It begins "The guest image the action uploads into the nested cluster" as 4f actually wrote it in actions#97, read 2026-09-26 -- but 4f's brief never dictated that wording, so the quote is a convenience and the grep is the address.) It says the rename was in flight, which was true when 4f landed and stops being true here; after this step there is one upload, under the name `debian`, and no transitional window left to describe. Leaving it is the same defect 4f rewrote the paragraph to avoid -- a document contradicting the file it documents -- and no other step in this phase reaches that file: 4g is in a different repository and 4j's greps exclude `*.md` by design. Commit subject: "Drop the transitional cluster image name." |
| 4i | low | haiku | none | Housekeeping, no commit in this repository. Close development#123 with the merge commits, noting that the fleet cleared 38 of its 80 references through ordinary repository work answering the daily audit before this phase began, and grew new ones in the same fortnight. That is a statement about the period before the phase, which no later merge can falsify; do not turn it into a claim about the final split, because the compliance page the next sentence sends you to shows the current count and not who cleared what, and by the time 4i runs this phase will have cleared most of the remainder itself. **Read the closing number off `docs/audits/compliance.md` when this step runs rather than from this plan**, per the survey's own rule that the inventory is the compliance page and never a number in this document: the survey counted 45 references in 13 repositories on 2026-09-19, the 2026-09-21 amendment made that 42 in 12 when `actions` cleared itself, and 4i runs after every other step has merged. Amended 2026-09-24: that was "the eight remaining steps -- sixteen pull requests, by those steps' own briefs" when this was written; 4a to 4e opened thirteen pull requests, eleven of which had resolved by 2026-09-24 -- ten merged and visual-digest-rust#23 closed as superseded -- so what 4i waited for was occystrap#143, shakenfist#4306, and 4f, 4g and 4h at one pull request each. Amended 2026-09-26: occystrap#143 and shakenfist#4306 merged on 2026-09-24 and 4f merged as actions#97 the same day, so 4i now waits for 4g and 4h alone, and the merge commits it closes development#123 with include `8c02ab0e7` for 4f. Amended 2026-09-30: 4g merged as shakenfist#4379, and 4h is now two pull requests, `shakenfist` then `actions`; both merge commits belong in the closing comment, because the `shakenfist` one is the consumer move 4g's discovery added and citing only the `actions` merge loses it. The net is the less interesting half: a fleet that grows references while clearing them is why decision 4.2 deletes the declaration with the last user. Comment on private-ci#38 with the guest-image inventory this survey found, since #38 is the collated reference and did not have it: the eight `sf://label/ci-images/debian-12` sites, the upload name, the seven reads of that upload by its bare short name `debian-12` found live in `shakenfist` on 2026-09-30 after every URL grep had returned clean, and the eight references in `private-ci`'s own `conductor/tests/test_imagebuilder.py` (seven naming the label, one the `-docker` variant) that follow `IMAGE_BUILDS` in phase 5. Leave #38 open; it closes at the end of phase 5. Do not close the per-repository consistency issues by hand -- the audit closes them itself when the repository goes compliant, and closing one by hand hides a repository that did not. `actions`' own issue will already have been closed that way, on 2026-09-20, before this step runs. |
| 4j | medium | sonnet | none | **Confirms the phase, which nothing else does.** Medium and sonnet rather than the mechanical pair its first draft had: six checks, three of which read a run log or a live grep across twenty-nine clones and decide whether the answer agrees with a diff. Observation step, no commit, run after every pull request above has merged and at least one morning's audit has run. (1) Read `docs/audits/compliance.md` on this repository's `main` and confirm the `eol-distro` table has no `non-compliant` row, and that its generation timestamp is after the last merge -- a stale page looks healthy, which the page's own header warns about. (2) Confirm the renamed artifact is both written and read, which takes two different runs because 4f and 4g land in different repositories. **Writing:** in a smoke cluster build after 4f, an upload line names the artifact `debian` with nothing after it. **Booting:** in a completed `shakenfist` functional-tests run after 4g, an instance boots `sf://upload/system/debian` with nothing after `debian`. **Anchor both ends, because the old names contain the new ones**: `debian-12` contains `debian` and `sf://upload/system/debian-12` contains `sf://upload/system/debian`, so an unanchored read passes on the pre-rename line. Between 4f and 4h the expected state of the build log is *two* upload lines, the old name and the new one, which is worth counting: finding one is itself a finding, and which one it is says whether 4f has not landed or 4h has landed early. Read both out of run logs rather than out of the workflow files, which only prove what was asked for. Keep the halves apart: 4f adds an upload in `actions` and renames nothing that `shakenfist` reads, so a run between 4f and 4g shows the write and cannot show the boot, and reporting the upload line as if it were the boot is a pass on the wrong assertion. The definition of done states the booting half only, and after 4g. **Read the under-cloud image too, and expect `debian-13`.** This check was written to do that; the 09-21 amendment inverted it while shakenfist#4280 was open, and 2026-09-24 restored it. After 4f and 4g both logs show the under-cloud booting `ci-images/debian-13`, and one still reading `-12` means a default did not move -- check which commit the run is of before reporting it, because a re-run of a pre-4g commit reads the old value legitimately. (3) Confirm `tools/ci_headroom_harvest.py` still matches its bundles after the lane rename, by running it against a merge run that completed after 4g. (4) `grep -E "^[[:space:]]*-[[:space:]]*['\"]?debian-12" <clone>/.github/actionlint.yaml`, the quote optional because `- "debian-12"` and `- 'debian-12'` are both valid YAML and these very files already quote their shellcheck entries that way, across fresh clones of **every** non-archived repository in the organisation, not the ones the criterion lists -- a stale declaration produces no finding in any criterion, so this is the only check in the phase that would catch one, and the repositories most likely to carry one are the ones that already migrated and so are not listed at all. The list-item anchor is load-bearing: this repository and `hunkydory` both name `debian-12` in a comment explaining why it is *not* declared, and a plain substring grep reports both. `private-ci` is the one expected hit and is step 4d's stated exception, for having no workflows at all. (5) `grep -rn --exclude='*.md' --exclude-dir=.git '/srv/ci/debian:12' .` over the same clones must return nothing, and `build-smoke-cluster/action.yml` must carry exactly one line naming `/srv/ci/debian:13`. The `--exclude` scoping is 4h's, for 4h's reason and in 4h's shape -- exclude prose, do not allow-list extensions: this plan file is in one of those clones and names the path several times, and a gate that halts on its own plan file is a gate an agent learns to override. It does not collide with decision 4.7, which was checked rather than assumed: `ci-dependencies.yml` spells its cache list as `name: "debian:12"` against an `images.shakenfist.com` URL, and checked against `actions` at the same commit as the rest of this survey, `build-smoke-cluster/action.yml:249` as the survey read it -- `:263` since `e2a56bd`, and found by grep rather than by line -- was the only line in that repository naming a `/srv/ci/debian` path at all, so the bookworm cache entry 4.7 keeps is not a hit. Grep the source path, not the command: the upload is assembled from a `${setup}` variable, so the literal string `artifact upload debian-12` appears nowhere in the fleet and a check looking for it passes whether or not 4h ran -- which is the shape of silent skip this step exists to catch, found by running the grep rather than reading it -- only this grep and gate B below can tell whether 4h ran, because the definition of done's label and URL greps match the reader spelling in `shakenfist` rather than the writer line in `actions`, while gate B reports the `debian-12` upload line itself until 4h deletes it; this grep is the mechanical half of the pair, compared to zero rather than read by hand, and a skipped 4h leaves every smoke cluster in the fleet uploading a bookworm image forever with no finding anywhere. The `exactly one` half catches the inverse mistake 4h's brief warns about. Then run 4h's gate B, the short-name read, over the same clones -- the command is in 4h's brief and the definition of done -- and read its output by hand rather than comparing it to zero: after 4h it should show only the runner docstring the definition of done names, and a line that reads the artifact by the name `debian-12` fails this check even though every URL grep above passes, which is the blind spot 4g found. (6) Read `docs/actions.md` on `actions`' default branch after 4h and confirm it describes the finished state: the under-cloud paragraph says trixie, and the guest-image paragraph describes a single upload under the name `debian` with no transitional window and no "in flight" qualifier. This is the one definition-of-done bullet whose subject no other check reaches, because every grep in this step excludes `*.md` by design, so without it the claim is a gate stated in a plan file rather than a gate. Report all six; if (2) to (6) disagrees with the diff, say so rather than filing it, because the phase is not over until they agree. |

#### Risks and mitigations

**A label that provisions is not an image that works.** The
`debian-13` image is a different rootfs: a job that relied on a
package bookworm shipped and trixie does not will fail at the step
that uses it, not at provisioning. Mitigated per repository rather
than centrally -- every step above waits for that repository's own
CI on the new label and treats a green actionlint as no evidence at
all. This is cheap here because the fleet has already done it
fourteen times: `instar` and `kerbside` moved 34 references between
them in two days without a follow-up fix.

**The guest-image change lands for the whole fleet at once.**
`build-smoke-cluster@main` is what every consumer pins, so 4f is live
everywhere the moment it merges, including for `kerbside`, which takes
the default. Amended 2026-09-24: the sharp half of this risk is back,
because shakenfist#4309 closed the bug that deferred it. 4f now lands
two things of different shapes. The upload is additive -- the old name
and its bookworm source stay untouched until 4h -- so nothing changes
underneath a consumer, and its residual risk is the cost of the
transitional window. The default is not additive: it moves every
consumer's under-cloud to trixie at the moment it merges, which is the
sharp half and is what this heading is about. Mitigated by 4h closing
the transitional window, by 4j check (5) proving it closed, and by 4f
triggering `kerbside`'s `sf-e2e-functional` and `shakenfist`'s
node-lifecycle job on their default branches after merge -- the two
consumers that take the default, one of them in the only repository no
step here edits. Those two runs hold the guest constant, because
nothing reads the new artifact name until 4g, so they are a cleaner
experiment than the "both changes at once" an earlier draft of this
paragraph claimed -- for the guest. They do not isolate the run: each
run carries two changes, the default move and the extra upload, while
what the instances boot is unchanged, which is the sense in which 4f's
brief says a failure has two suspects.

Amended 2026-09-26: this mitigation has fired, and half of it is
red. `kerbside`'s nightly `sf-e2e-functional` after 4f merged, run
36110697157, failed for a reason not yet attributed; the
node-lifecycle run has not been triggered. Whether 4f's default
move is reverted is open. The survey's 2026-09-26 amendment is the
record and kerbside#482 is where the diagnosis lives. Amended
2026-09-29: both halves are now green and no revert is happening --
`kerbside` passed on 09-26, 09-27 and 09-28, and node-lifecycle
passed twice on 09-29 once actions#112 landed. **The mitigation
worked, and it worked late.** What it caught, it caught through
`kerbside`'s nightly and not through this fleet's own CI, because
the job that was actually broken runs on `merge_group` and
`workflow_dispatch` only: four days of green pull requests hid it.
A mitigation that depends on a job the default verification path
cannot reach is worth less than this paragraph assumed.

**Nothing has ever booted a trixie guest artifact.** The 09-21
amendment established this and shakenfist#4309 does not change it:
both canary runs uploaded the same bookworm guest, so the guest
was the controlled variable rather than a tested one, and #4309
fixed hypervisor provisioning. The first run that exercises a
trixie guest is the `shakenfist` functional-tests run after 4g,
and for the four matrix lanes -- which pass `base_image`
explicitly, so their under-cloud moves in 4g rather than in 4f --
that run moves under-cloud and guest together. Mitigated only
partly, and the partial is the honest word: the node-lifecycle run
after 4f, once someone triggers it -- as of 2026-09-26 it has not
run -- is a data point about the same under-cloud image in the
same repository, which narrows a 4g failure towards the guest
without isolating it, and 4g's brief says to read the libvirtd
journal for the domain-definition error #4309 names before
concluding anything. If that is not enough when 4g runs, the
fallback is back brief question 4's proposal: source the neutral
name from `/srv/ci/debian:12`, land 4g, and move the content to
`:13` afterwards. Recorded here so it is a decision available at
that point rather than a rediscovery.

**The blocker closed, and nothing in this plan noticed.**
shakenfist#4280 gated 4f's defaults, 4g's item (1) and phase 5's
Debian 12 half, and it was an open bug in another repository with
no owner named here. The failure mode this section named was not
that it stayed open -- it was that it would close and nobody would
notice, leaving eight sites on a retired image with a comment
explaining a reason that had expired. It closed on 2026-09-23 and
was withdrawn from this plan the next morning, before any step had
written one of those comments. Read that as a near miss rather
than as a mitigation that worked: what caught it was someone
asking whether the bug had merged, not a check. The two
mitigations this section claimed would not have. Requiring a
comment at each of the eight makes the sites greppable and says
nothing about the issue's state, and it had not run yet anyway;
phase 5's first step reading the issue would have happened weeks
later, after 4f and 4g had landed comments that were already
wrong. The analysis to inherit is unchanged and still unfunded:
`plan-status-vocabulary` defines `Blocked` as "cannot proceed
until something outside the plan changes" and
`scripts/audit/checks/plans.py` accepts it, but a half-blocked
phase is still `In progress`, the vocabulary's rule is that the
term is the whole cell, and so there is nowhere to record *what*
the block is. Phase 7 should ask for a blocked-on column, and this
episode is the argument for it: a cell naming shakenfist#4280 is
something a daily audit could have watched close, which is exactly
what no human remembered to do.

**A silent stop, not a failure.** `ci_headroom_harvest.py` keys off
job names, and 4g renames them. Nothing fails if the tool is missed:
it matches nothing and reports empty. Mitigated by putting the
rename and the tool in one commit, and by 4j re-running the tool
against a real merge run rather than reading the diff.

**The inventory moves while the phase runs.** Two repositories grew
new findings during the fortnight this plan sat still. Mitigated by
every step re-reading the repository's consistency issue for current
line numbers before editing, and by 4j asserting against the
regenerated compliance page rather than against this plan's table.

**Phase 5 starts before this finishes.** D4 orders producers after
consumers, and the guest-image half is the part that makes that
ordering real. Mitigated by 4j being the gate: phase 5's first step
should refuse to start until 4j has reported every check it runs
agreeing. Amended 2026-09-24: 4j agreeing is sufficient again. The
eight under-cloud sites are back in this phase, so when 4j reports
every check agreeing there is no `ci-images/debian-12` left anywhere
for phase 5 to trip over, and phase 5's Debian 12 follow-up gates on
4j and nothing else. The `debian-11` bullet remains unaffected and
startable now, as it has been since phase 1 completed.

#### Definition of done
All twelve bullets hold as of 2026-10-02. Eight are covered by 4j,
whose output is under *What 4j confirmed* below: bullet 1 is 4j (1),
bullet 2 is 4j (4), bullets 4, 5 and 6 are 4j (5), bullet 8 is 4j (6),
bullet 10 is 4j (2) and bullet 11 is 4j (3). **Bullets 3, 7, 9 and 12
are not among 4j's checks**, so each carries its own evidence inline.
A checklist and an observation step's brief written weeks apart
overlap only in part, which is worth knowing before the next phase
writes both.

- [x] `docs/audits/compliance.md`'s `eol-distro` table has no
      `non-compliant` row, on a page generated after the last merge.
- [x] `grep -E "^[[:space:]]*-[[:space:]]*['\"]?debian-12"` over the
      `.github/actionlint.yaml` of every non-archived repository in
      the organisation returns only `private-ci`, which has no
      workflows for a declaration to govern. The anchor matters:
      this repository and `hunkydory` name the label in a comment
      saying why it is absent, and a substring grep reports both.
      The scope is the whole organisation rather than the
      repositories the criterion lists, because a repository that
      has already migrated is where a declaration outlives its
      last user -- `kerbside-patches` was exactly that when this
      phase was planned.
- [x] `grep -rn --exclude='*.md' --exclude-dir=.git
      "ci-images/debian-12"` over fresh clones of every non-archived
      repository returns nothing outside
      `private-ci/conductor/tests/test_imagebuilder.py`, whose eight
      references -- seven naming the label and one the `-docker`
      variant -- phase 5 moves with the `IMAGE_BUILDS` entries, with
      nothing else exempted. Amended 2026-09-24: this bullet listed
      eight under-cloud sites as expected hits while shakenfist#4280
      was open, each required to carry a comment naming the issue.
      shakenfist#4309 closed that bug, 4f and 4g move all eight, and
      both the exemption and the comment requirement are withdrawn --
      a hit anywhere outside that one `private-ci` test file now fails
      this bullet, which is what it asserted before the bug was found.
      The substring rather than the `sf://label/` form, so that both
      spellings are caught; the `private-ci` test file happens to use
      the prefixed one. The `--exclude` is what keeps the grep off
      prose -- this plan names the string a dozen times, and
      `development` is in the clone set -- and it is the same
      exemption `eol-distro` makes for a document describing a
      migration. It excludes prose rather than allow-listing
      extensions, so a reference in a `.j2` template or an
      extensionless script is still read. `private-ci` is in the clone
      set deliberately: it is excluded from the criterion, so it is
      exactly the repository a criterion-shaped check would miss.
      **Run 2026-10-02 over fresh `--depth 1` clones of all 29
      non-archived repositories, with no clone failures:** eight hits,
      all in `private-ci/conductor/tests/test_imagebuilder.py` --
      `:121`, `:156`, `:167`, `:180`, `:187`, `:197`, `:216` naming the
      label and `:874` the `-docker` variant, which is the seven and
      one this bullet predicted. Nothing anywhere else. This is the
      bullet no step owned: it is not one of 4j's six checks, and until
      this was run the phase was being declared complete on a
      criterion with no result.
- [x] 4h's gate A, the same grep for `sf://upload/system/debian-12`
      with `-I` added, returns nothing at all.
- [x] 4h's gate B, the short-name read, run over the same clones from
      a directory holding only them, shows nothing that reads the
      artifact:

      ```
      grep -rnIE --exclude='*.md' --exclude-dir=.git \
          '(^|[^[:alnum:]_/.-])debian-12([^[:alnum:]_.-]|$)' * \
        | grep -vE -e '^(development/scripts|private-ci/conductor)/' \
            -e '^[^:]*/actionlint\.yaml:' \
            -e '^[^:]+:[0-9]+:[[:space:]]*(-[[:space:]]*)?runs-on:' \
            -e '^[^:]+:[0-9]+:[[:space:]]*#'
      ```

      Its output is read by hand rather than compared to zero. The
      expected residue after 4h, as of 2026-09-30, is one docstring in
      `shakenfist/tests/test_mariadb_capacity_admission.py` naming the
      runner, which is not a read. The bullet above cannot stand in for
      this one: consumers name the artifact by its short name as well
      as its URL, and on 2026-09-30 seven such reads were live in
      `shakenfist` after all three of 4g's greps had returned clean.
      What each filter subtracts, and why, is in 4h's brief.
- [x] The same grep for `/srv/ci/debian:12` returns nothing, and
      `actions/build-smoke-cluster/action.yml` carries exactly one
      line naming `/srv/ci/debian:13`. This is one of two checks
      that can tell whether 4h ran: the label and URL greps above
      match the reader spelling in `shakenfist`, not the writer line
      in `actions`, while gate B above reports the `debian-12` upload
      line itself until 4h deletes it. This bullet is the mechanical
      half, compared to zero rather than read by hand, and its
      `exactly one` half also catches the inverse mistake of
      deleting the `debian` line instead. It greps the source path
      rather than `artifact upload debian-12`, because the command
      is assembled from a `${setup}` variable and that literal
      exists nowhere -- a bullet asking for it would pass vacuously. Decision 4.7's
      bookworm cache entry is not a hit: `ci-dependencies.yml`
      spells its cache list as `name: "debian:12"` against an
      `images.shakenfist.com` URL, not as a `/srv/ci/` path.
- [x] `kerbside`'s `sf-e2e-functional` workflow and `shakenfist`'s
      node-lifecycle job have each completed successfully on their
      default branches after 4f merged, triggered by 4f, which is the
      step that owns this bullet. They are the two consumers that take
      `build-smoke-cluster`'s default without passing a `base_image`
      -- `kerbside` `.github/workflows/sf-e2e-functional.yml:104` and
      `shakenfist` `.github/workflows/functional-tests.yml:560`, both
      re-read 2026-09-24 -- so 4f moves their under-cloud without
      either repository's diff showing it, and 4g's edit list reaches
      neither. `kerbside` is additionally the one repository no step
      in this phase edits at all. Amended 2026-09-24: while
      shakenfist#4280 was open this bullet covered `kerbside` alone
      and proved only that the upload had not broken it, because the
      default was not moving. It covers both and proves both again.
      Verified in the 2026-09-29 amendment in the survey above, which
      is where the run IDs are: `kerbside`'s nightly
      `sf-e2e-functional` on `develop` passed three consecutive times
      (36228146379, 36306108473, 36399738723) after the 09-25 failure,
      and `shakenfist`'s node-lifecycle job passed twice on `develop`
      (merge_group runs 36501556771 and 36513297089, each a full
      six-host build). The amendment also records why the second half
      took a fix in `actions` first.
- [x] `actions` `docs/actions.md` describes the finished state:
      the under-cloud paragraph says trixie, and the guest-image
      paragraph describes a single upload under the name `debian`
      with no transitional window. 4f writes the first and leaves
      the second saying the rename is in flight, which is true
      between 4f and 4h and false after; 4h owns removing it.
      Nothing else in the phase reads that file -- 4j's greps
      exclude `*.md` -- so without this bullet the phase closes
      with the documentation describing a completed rename as
      still under way.
- [x] `instar`'s `functional-tests.yml` carries the `audit-ok:
      eol-distro` marker **within one line of the finding, and with
      a reason after it**, and instar#564 is closed by the audit
      rather than by hand. The two requirements are independent and
      this phase only had to add the first: a marker with a reason
      has been in that file since before this phase was planned,
      nine lines away, so proximity is what was missing and the
      reason is what `docs/audits/eol-distro.md:169-176` has always
      asked for. Dropping either leaves the fleet's canonical
      instance of this marker wrong in one of the two ways.
      Verified 2026-10-02 on `instar` at `develop`: the marker is
      `.github/workflows/functional-tests.yml:651` and the finding it
      covers is `image: 'debian:12'` at `:652`, one line apart, with
      the reason `-- supported target, see the note above` on the
      marker line. instar#564 is closed, `COMPLETED`, at
      2026-09-24T11:29:44Z -- by the audit, which is the half of this
      bullet the phase could not do itself.
- [x] A completed `shakenfist` functional-tests run after 4g shows
      instances booting `sf://upload/system/debian`, read from the
      run's log, and the under-cloud in that same log reading
      `ci-images/debian-13`. Amended 2026-09-24: shakenfist#4280
      inverted the second half of this for three days and
      shakenfist#4309 restored it. It is the first green trixie
      under-cloud this suite will have produced, so read it rather
      than assuming it.
- [x] `tools/ci_headroom_harvest.py` matches its bundles on a merge
      run completed after 4g.
- [x] development#123 is closed; private-ci#38 is still open and
      carries the guest-image inventory. Verified 2026-10-02: #123
      closed by 4i, and #38's inventory is the comment of 2026-10-01
      08:06 UTC, which lists the label and artifact references the
      survey found and which the issue did not previously have. #38
      stays open until the end of phase 5 by design, which is why it
      is a bullet about two different states rather than two closures.

#### What 4j confirmed

4j ran on 2026-10-02 (AEST), after the 2026-10-01 12:44 UTC audit run
regenerated the compliance page -- 22:44 the previous evening in
Canberra, which is still on AEST until daylight saving starts on
2026-10-04. Bare
dates in this subsection are AEST and timestamps are UTC, which is the
convention the status paragraph states; the two differ by a calendar
day for anything before 10:00 local. All six checks pass. Recorded
here because the phase is not confirmed by its merges and nothing
else in this document says so.

(1) The `eol-distro` table on `main` lists no `non-compliant` row -- 19
compliant, `cloudgood` and `private-ci` N/A -- and the page was
generated at 2026-10-01 12:44:43 UTC, after the phase's last merge at
08:04:28 UTC. Both halves, because a stale page looks healthy: the
check was genuinely blocked on 2026-10-01, when the then-current page
predated the last two merges, and it was not run until that cleared.

(2) The renamed artifact is both written and read, read out of run logs
rather than workflow files, with both ends anchored. In the three
merge_group runs completed after 4g (36651832849, 36661716274,
36669390897, each confirmed a descendant of `2a94e582f`) the write side
shows the expected *two* upload lines for that date, the read side
shows `base=sf://upload/system/debian` unsuffixed, and the under-cloud
shows `sf://label/ci-images/debian-13`. The post-4h state -- *one*
upload line -- was then read out of run 36938755972, whose build log
postdates 4h part 2: exactly one upload, `debian /srv/ci/debian:13`,
and no occurrence of `debian-12` or `debian:12` anywhere in its 9,090
lines. The brief asks for the source check and the log check as a pair;
both agree.

(3) `tools/ci_headroom_harvest.py` matches all five `BUNDLE_TOPOLOGIES`
entries against a merge run completed after 4g, with no
`UnknownBundleError`. This is item (2) of 4g's own work, whose failure
mode was silent.

(4) The anchored `actionlint.yaml` list-item grep across fresh clones of
all 29 non-archived repositories returns one line, `private-ci`, which
is step 4d's stated exception for having no workflows. The anchor is
load-bearing and was checked: an unanchored grep also reports
`development` and `hunkydory`, both comments explaining why the label is
*not* declared.

(5) `/srv/ci/debian:12` returns nothing across those clones,
`build-smoke-cluster/action.yml` carries exactly one `/srv/ci/debian:13`,
gate A returns nothing, and gate B returns one line: a docstring in
`shakenfist/tests/test_mariadb_capacity_admission.py` naming a *runner*,
not the artifact. The runtime-assembly grep returns the same three known
benign lines it did on 2026-09-30.

(6) `docs/actions.md` describes the finished state: the under-cloud
paragraph says trixie, the guest-image paragraph describes a single
upload named `debian`, and `in flight` and `transition` no longer appear.

**One failure in that post-4h run is not this phase's.** The Debian 13
tier lane failed on
`test_no_unbudgeted_fixed_rate_database_polling`, which found four
undeclared fixed-rate polls above its 0.25/s ceiling --
`GetBlobAttributes/queues`, `UpdateBlobTransfer/transfers`,
`UpdateBlobLastUsed/queues` and `GetObjectsByState/queues`. The same
lane failed the same way in run 36806873191, *before* 4h merged, so it
predates this phase and is recurring rather than a flake. It is blob and
transfer traffic wanting a `database_load_budget.yaml` entry, and it
belongs to the database-load work. It has an owner there:
shakenfist#4401, filed automatically from run 36938755972 at 2026-10-02
01:06 UTC, names the same four pairs and the same Debian 13 slim-tier
lane. So this paragraph is a pointer rather than the only
record of a recurring failure on another repository's default branch.

**A note for later steps that quote a commit subject.** 4h part 1's
brief prescribes the subject `Read the cluster image artifact as debian,
not debian-12.`, which is 57 characters; the commit convention wants 50
or fewer. The step followed the brief, because a plan file carries
Michael's authority and substituting a different subject quietly is
worse than landing a long one. A brief that dictates a subject should
count it first.

#### Back brief

Five things to agree before the remaining steps run, because each
is cheap to propose and expensive to redo. The first three were
written when the phase was planned. The last two came from the
2026-09-21 amendment and decided what the phase was while
shakenfist#4280 was open; both are answered as of 2026-09-24, and
both answers are recorded in place rather than deleted, because
the reasoning is what a later reader needs when the next blocker
lands:

1. **Decision 4.5, the release-neutral artifact name.** It is a
   43-site edit in a test suite, and doing it as `debian-13`
   instead is the same edit for a worse result. If the neutral name
   is wrong, say so before 4f, not after 4g.
2. **Decision 4.2, deleting the retired declaration.** The
   alternative -- leave `debian-12` declared until phase 5 -- is
   defensible and makes every revert one line. This plan takes the
   stricter reading because two repositories grew new findings
   while it waited, and because this repository already made the
   same call for itself and wrote it into
   `.github/actionlint.yaml:15-20`. Agreeing here makes that a
   fleet convention rather than one repository's habit.
3. **Decision 4.4, the scope.** This phase is now two phases' worth
   of work in one section. Splitting the guest-image half into its
   own phase is reasonable; what is not reasonable is running phase
   5 without it.
4. **Whether 4f should rename the artifact and change its contents
   in one landing.** As written it adds the `debian` upload sourced
   from `/srv/ci/debian:13`, so the rename carries a bookworm to
   trixie guest change with it. That was fine when the under-cloud
   was moving in the same phase and a failure had one obvious
   suspect. With shakenfist#4280 open it conflates two variables in
   a suite whose failures take an hour to read, and #4280 was found
   precisely because someone held the guest constant and moved one
   thing. Both source paths are on the dependencies cache disk
   (`actions` `ansible/ci-dependencies.yml:186-195`), so sourcing
   the neutral name from `/srv/ci/debian:12` and moving it to
   `:13` in a separate later landing costs one commit and buys a
   controlled experiment. **The plan as amended still says
   `/srv/ci/debian:13`**; this is the change I would make and did
   not, because it revises a decision rather than correcting a fact,
   and 4h and 4j's greps are written against the `:13` spelling.
   Priced, so the answer is a decision rather than a flag: 4h's
   gate, 4j check (5)'s "exactly one line naming
   `/srv/ci/debian:13`" and the definition-of-done bullet that
   pairs with it are all written against `:13` and move with this
   choice, decision 4.7's bookworm cache entry stops being a
   retired path and becomes the one this phase ships, and a later
   landing is needed to move the artifact's contents from `:12` to
   `:13` once #4280 has been excluded as a suspect. Three cells,
   one decision, and one extra commit in a phase that does not
   exist yet. **Answered 2026-09-24: no change, the plan keeps
   `/srv/ci/debian:13` -- but not for the reason first given.** The
   first version of this answer said the controlled experiment was
   "not available at any price", because 4f moves the under-cloud
   default in the same landing. That conflates the two variables
   the 09-21 amendment was careful to separate. shakenfist#4309
   fixed hypervisor provisioning; it says nothing about whether a
   trixie *guest* works, and the 09-21 analysis above -- that no
   canary ever ran one -- is untouched by it. The guest content is
   still a variable that could be held constant, and this
   question's proposal is still purchasable at its stated price.

   It is not bought because the step ordering already buys most of
   it, one step earlier than the question looked. 4f uploads the
   trixie artifact under a name nothing reads yet: the
   references still say `debian-12`, which 4f leaves sourced from
   `/srv/ci/debian:12`. So the two runs after 4f move the
   under-cloud with the guest held constant -- the run also does
   the extra upload, so it isolates the guest and not the run --
   which is the definition of done's `kerbside` and node-lifecycle
   bullet, and
   4g then adds the guest content for the jobs that read the
   renamed artifact.

   What that does not cover is recorded in the risks section rather
   than argued away here: the four matrix lanes pass `base_image`
   explicitly, so their under-cloud moves in 4g, and for those
   lanes 4g changes under-cloud and guest together. The
   node-lifecycle run is a data point about the same image in the
   same repository, not a substitute for isolating it. 4h, 4j check
   (5) and the definition-of-done bullet stay written against
   `:13`, and the fallback if 4g's run is unreadable is this
   question's own proposal, which the risks section now states.
5. **Where the eight deferred under-cloud sites go.** They were
   not in this phase any more and not in phase 5, which is
   producers. Three options were open: reopen this phase when
   #4280 closed, add a phase 4b, or fold them into phase 5's first
   step as its entry gate. The third was tempting and wrong -- it
   makes a producer phase start with a consumer edit, which is the
   exact blurring D4 exists to prevent. **Answered 2026-09-24: the
   first, reopen this phase.** shakenfist#4309 closed the bug
   before any step had acted on the deferral, so there was nothing
   to migrate between phases and no landed comment to unwrite; the
   eight sites go back into 4f and 4g where they were planned, and
   phases 5 and 6 are unchanged by any of it. That this was
   answerable so cheaply is an accident of timing rather than a
   vindication of deferring it -- see the blocker paragraph in the
   risks section.

### 5. Retire the end-of-life producers

Closes: private-ci#38, private-ci#40, private-ci#45, 33fl#826.
Depends on: phase 4, complete 2026-10-02. Planning effort: high,
because the three bullets this section carried named two of the four
`IMAGE_BUILDS` entries that have to go, missed the one phase 3
assigned here by name, and described 33fl's half as hand work that a
tool in that repository has been doing weekly since July.

**Status: complete, 2026-10-05.** All six steps ran and all six
pull requests merged -- the four planned and the two the
`ubuntu:18.04` removal added; 5f's twelve checks all agreed and closed
private-ci#38. **One of the twelve could not be run as written.**
Check (6) specifies `ansible static_runners -m setup -a
'filter=ansible_distribution_release'`, which this environment's
command classifier refuses against live remote hosts, so the step
established the same fact a different way: for each of the eight
hosts, today's `manage.yml` deploy logged an `apt_repository`
invocation with `codename=None` resolving to `trixie`, which means
the codename came from each host's own `ansible_distribution_release`
fact rather than from a variable in `33fl` -- the distinction that
makes the log equivalent to the query rather than merely consistent
with it. That provenance is recorded in private-ci#38's thread, and
phase 6 should not write another check that names a command the
fleet cannot run. **An anchor proves a grep still fires; nothing in
this phase's design catches a check whose command is unavailable**,
which is the one gap in the vacuous-pass armour the phase was built
around.

The survey below was run on
2026-10-02, the day phase 4 closed, and it moved the phase in both
directions: the retirement is larger than the section said in
`private-ci` and in `actions`, and smaller in `33fl`, where the
blocker the issue describes appears to have been automated away two
months ago. It also found that the test suite is not the gate a
sweep of this shape wants it to be -- one test asserts a successful
build of a "known image" with the boundary mocked, so it will keep
passing once the image is not known any more.

One thing this phase cannot do is prove itself with the audit.
`eol-distro` does not read either list it empties: `private-ci` is
scoped by `only_checks` to four plan criteria and `sfui-vendor`
(`scripts/audit/repo.py:88-93`), and `33fl` is in another
organisation. So `compliance.md` reads exactly the same before and
after, and a done criterion asking for a green audit would pass
vacuously. Phase 6 is the phase that makes the producers
measurable; until it runs, the evidence here is greps, a test count
and a nightly cycle summary.

#### What this section asked for, 2026-09-13

Left as written, because the survey below contradicts parts of it
and the contradiction is the useful record:

* **private-ci#40, `debian-11`**: remove from `IMAGE_BUILDS` and
  `CI_IMAGES`. No workflow requests it, so this can go as soon as
  phase 1 lands -- it also removes the permanent `False` in every
  nightly cycle summary.
* **private-ci#40 follow-up, `debian-12` and `debian-12-docker`**:
  only after phase 4, per D4. Amended 2026-09-21 and again
  2026-09-24: for three days this also waited on shakenfist#4280,
  because phase 4 was going to close with eight under-cloud sites
  still naming `ci-images/debian-12` and removing the producer
  while they did is the breakage D4 exists to prevent.
  shakenfist#4309 closed that bug, phase 4 moves all eight in 4f
  and 4g, and the gate is "phase 4 complete" again -- specifically,
  4j reporting every check agreeing. The `debian-11` bullet above
  is unaffected and has been unblocked since phase 1 completed.
* **33fl#826**: delete the two GitLab static runner instances so
  `static_runner.yml` rebuilds them on `debian:13`, and confirm
  the six GitHub runners have rolled over. Consider a retire tool
  so this is not manual next time.

#### What the survey found

Checked 2026-10-02 against `shakenfist/private-ci` at `master`
(`8816709`), `shakenfist/actions` at `main` (`d72644f`),
`shakenfist/shakenfist` at `develop`, `shakenfist/development` at
`main` (`72d863e`) and the `33fl` working copy at `a7d7803c`.

1. **Four `IMAGE_BUILDS` entries have to go, not two.**
   `conductor/imagebuilder.py` still carries `debian-11`,
   `debian-12`, `debian-12-docker` **and `debian-gnome-12`**. The
   fourth is not an oversight in the tree, it is an omission in
   this section: decision 3.5 assigned it here by name -- "its
   fourth checkbox -- retire `debian-gnome-12` once nothing
   consumes it -- is phase 5's work" -- and private-ci#45 is open
   with three of its four boxes ticked for exactly that reason.
   private-ci#40 does not cover it either; its fourth checkbox
   names only `debian-12`. So without this bullet the phase closes
   with an end-of-life desktop image building nightly and an issue
   that cannot be closed.

2. **`CI_IMAGES` holds three of the four, and the two lists are
   not the same set.** `conductor/provisioner.py:45-100` lists
   `debian-11`, `debian-12` and `debian-12-docker`; it has no
   `debian-gnome-12`, because the desktop images are built for
   nested CI clusters to consume rather than for runners to boot
   (`imagebuilder.py:55-57`). A unit test asserts every `CI_IMAGES`
   label has an `IMAGE_BUILDS` entry and not the converse
   (`private-ci`'s own `AGENTS.md:259` at `8816709`, and the test is
   `MatrixTestCase.test_every_runner_label_has_a_build` in
   `conductor/tests/test_imagebuilder.py` -- named rather than cited by
   line, because the line moves and the name does not), so the
   asymmetry is intended and the edit is
   four entries in one file and three in the other, not seven in
   both.

3. **Removing all seven entries fails 22 tests, and every one of
   them is fixture-shaped.** Measured rather than predicted, on a
   clone of `master` with the entries deleted and nothing else
   changed: `pytest conductor/tests` reports **1267 passed, 2
   skipped** before and **22 failed, 1245 passed, 2 skipped**
   after. The failures are confined to three files --
   `test_provisioner_claims.py` (10),
   `test_imagebuilder.py` (9) and
   `test_provisioner_create_workers.py` (3) -- and they fail the
   same way: `create_workers()` walks `CI_IMAGES`, so a queue
   asking for a label that no longer exists provisions nothing and
   the assertion reads `2 != 0` or `no claim requested for size
   'xs'`. None of them is a logic failure and none of them wants a
   code change.

   Two constraints on the sweep, both from reading the tests rather
   than the failures. `test_provisioner_create_workers.py:256-263`
   needs **two different surviving labels** and depends on their
   order in `CI_IMAGES` -- its comment says "two runners are two
   different labels; CI_IMAGES is walked in order, and debian-11
   comes before debian-12" -- so renaming both fixtures to one
   label silently removes what the test covers. And the cheap fix
   of inserting a synthetic `debian-12` entry into the lists in
   test setup would make all 22 pass while re-creating the coupling
   this phase exists to remove.

4. **The test run is not the gate, and one test proves it.**
   `test_web.py:176-182` posts `{'name': 'debian-12'}` to
   `/api/build-image` with `imagebuilder.request_build` mocked to
   return `True`, and asserts a 200 and `{'requested':
   'debian-12'}`. The real `request_build` returns `False` for a
   name not in `IMAGE_BUILDS` (`imagebuilder.py:637`), so after
   this phase the test called `test_build_known_image` asserts a
   successful build of an image that is not known, and it does so
   without failing. Four more files name a retiree and do not fail
   -- `test_db.py` (5 lines), `test_db_costs.py` (2),
   `test_provisioner_costs.py` (7) and `test_staticrunners.py` (2)
   -- but those are opaque strings in a `runner_os` column or an
   instance `metadata` dict, where any label would do. `test_web.py`
   is the one where the fixture is load-bearing and the mock hides
   it. A sweep driven by the failing list misses it.

5. **`ansible/ci-image.yml` in `actions` carries the same dead
   branch phase 3 deleted from `ci-dependencies.yml`.** Four
   `when:` lines -- `:49`, `:60`, `:455` and `:466` -- gate two
   pairs of `add_host` tasks on `base_image == "debian:11"` versus
   `!= "debian:11"`, one pair for the rebuild host and one for the
   test host, differing only in whether
   `ansible_python_interpreter` is forced to `/usr/bin/python3`.
   `IMAGE_BUILDS` is the only caller of that playbook -- no
   workflow in `actions` invokes it and neither does 33fl -- so
   once the `debian-11` entry is gone all four conditions are dead
   and each pair collapses to one task with no `when:`. That is
   decision 3.3's reasoning applied to the file phase 3 did not
   reach, and decision 3.2's ordering applies too, in the same
   direction: while `debian-11` is still building, deleting the
   branch sends bullseye down the auto-detect path, which is the
   quirk the branch exists for.

6. **The frozen cache entries are not serving anybody, which is a
   defect rather than an argument for deleting them.** Decision 4.7
   left `ubuntu:20.04`, `debian:11`, `debian:12` and `fedora:40` in
   `ci-dependencies.yml`'s cached image list and said "retirement
   is phase 5's and private-ci#38's", on the grounds that the cache
   is what lets a test boot an old guest deliberately. A test does:
   `shakenfist/deploy/ansible_module_ci/004.yml:120` and `005.yml`
   create instances from `10@debian:11`, and 005's idempotency
   assertion is the regression coverage for shakenfist#3669. But it
   does not boot it from the cache. `build-smoke-cluster` uploads
   exactly one cached image into the nested cluster
   (`action.yml:257`, the `debian` artifact 4h left behind), no
   topology playbook configures an image mirror, and 004.yml's own
   comment says the create "pays a cold ~407 MiB internet fetch
   inside its await budget" which "has been observed to take 578
   seconds (issue 4000)" against a 600 second timeout. So the bytes
   are on the attached `/srv/ci` disk and the cluster fetches them
   over the internet anyway. That is a performance defect in how
   the cache is used, not a retirement, and decision 5.4 keeps the
   entries and files it.

7. **33fl's half may already be done, and three statements say it
   cannot be.** 33fl#826 says the two GitLab runners "have no
   retire tool, so nothing will ever recreate them", and the
   comment above `static_runner_debian_release` in
   `group_vars/all/static_runners.yml:52-53` says the same, and
   this section repeats it as "consider a retire tool so this is
   not manual next time". All three are stale.
   `tools/retire-gitlab-runners.py` has been in that repository
   since `743a0200` on 2026-07-30 -- before the issue was filed and
   six weeks before this plan was written -- it pauses the
   server-side runner record, drains, deletes the backing Shaken
   Fist instance and resumes, and `rundaily.sh:262` runs it inside
   the `--weekly` block beside `retire-github-runners.py`.
   `static_runner_debian_release` became 13 in `fa0e79d8` on
   2026-09-12. So if a weekly cycle has run since, both GitLab
   runners and all six GitHub runners have been rebuilt on trixie
   already and 33fl#826's work is observation and three text
   corrections. Not asserted here: `--weekly` is operator-invoked
   and the survey found no log of it reaching Loki
   (`{job="ansible-deploy"}` carries the deploy phases, not
   `rundaily`'s retire lines), so step 5c reads the fleet first and
   deletes only if the read says bookworm.

8. **Two more stale sentences, one of them in this repository.**
   `docs/audits/eol-distro.md:57-63` says `debian-gnome-12` "is
   listed although the CI conductor advertises no
   `debian-gnome-13` label yet" and that "what is missing is an
   entry in private-ci's `IMAGE_BUILDS` table, not an image". Phase
   3 added that entry, so the paragraph now tells a reader to file
   a request for something that exists. And
   `conductor/imagebuilder.py:195-198` explains
   `STALE_LABEL_SECONDS` by naming "the debian-11 case in #40",
   which stops being an example the moment this phase lands.

9. **Nothing else in the fleet names the labels.** Corroborating
   4j rather than repeating it: `/srv/ci/debian:12` appears nowhere
   in `actions` except the single `/srv/ci/debian:13` line 4h left,
   and the only `debian-12` left in `private-ci` outside
   `conductor/` is the declaration in `.github/actionlint.yaml`,
   which phase 4's done criterion deliberately exempted as the one
   repository with no workflows for a declaration to govern. It
   goes with the entries, per decision 4.2's reasoning.

#### Scope

In: `private-ci`'s two lists, their test fixtures and that
repository's `actionlint.yaml`; the dead `debian:11` branches in
`actions/ansible/ci-image.yml`; 33fl's static runner fleet state and
the three stale statements about it; the stale paragraph in this
repository's `docs/audits/eol-distro.md`; and closing private-ci#38,
#40, #45 and 33fl#826.

Out, and why:

* **`shakenfist/images` continuing to publish `debian:11`,
  `debian:12` and `debian-gnome:12`.** D5 -- this plan does not
  re-plan that repository, and private-ci#40 says the published
  images stay because they are useful for testing older guests.
  Retiring a runner label is not retiring an image.
* **Teaching the audit to read `IMAGE_BUILDS` and `CI_IMAGES`.**
  Phase 6, and Q1 is the open question about which instrument does
  it.
* **The cache-fetch defect in finding 6.** Filed in step 5e, not
  fixed: it is a change to how a nested cluster resolves guest
  images, it touches the action every consumer pins at `@main`, and
  it has nothing to do with retiring a producer.
* **`rocky-9`.** Not on the end-of-life table this plan works from.
* **Pruning or regenerating `REVIEWS.md`** in any repository, per
  the phase landing shared block in `PLAN-TEMPLATE.md`. Editing
  `docs/audits/eol-distro.md` in step 5d will invalidate that
  file's review mark; CI prunes it on the default branch and no
  step prunes it by hand.

#### Decisions

5.1. **`debian-gnome-12` is retired here, with the Debian 12 runner
   labels.** Decision 3.5 said so and this section did not, which
   is the omission finding 1 records. The consequence is that
   private-ci#45 closes in this phase, as 3.5 promised, and that
   the fixture sweep in finding 3 covers three labels rather than
   two.

5.2. **One pull request per repository, and two commits inside
   `private-ci`'s.** The repository rule is inherited from decision
   4.1 and for the same reason. The split inside it is new: the
   first commit removes `debian-11` and `debian-gnome-12`, which
   nothing in the fleet has ever requested, and the second removes
   `debian-12` and `debian-12-docker`, which phase 4 spent itself
   clearing. If the Debian 12 half has to come back -- a consumer
   the audit cannot see, a static runner, a repository outside the
   matrix -- the revert is one commit and does not take the
   uncontroversial half with it. The fixture sweep splits along the
   same line, which is why this is two commits rather than two
   pull requests: the two halves share
   `test_provisioner_create_workers.py` and reviewing them apart
   would mean reviewing that file twice.

5.3. **The 22 failures are fixed by renaming fixtures to surviving
   labels, and `test_web.py` is renamed although it does not
   fail.** Two surviving labels, not one, because of the ordering
   dependency in finding 3. No synthetic entries in test setup. And
   the done criterion is the test *count*, not a green run: **no
   fewer passed and the same skipped**, so a sweep that deletes
   coverage to make the suite pass fails the criterion. No fewer
   rather than exactly equal, because there is one test worth adding
   -- that `request_build` returns `False` for a retired name, which
   is the hole finding 4 found -- and a criterion demanding equality
   would argue against writing it. Any added test is named in the
   commit message so the rise is accounted for. The
   reference point is **the parent of 5a's merge commit, measured at
   the time**, not a number written down here. `master` moves while
   a phase runs -- it was `8816709` when the survey ran and
   `bc00be0` hours later -- so an absolute target would fail the
   criterion for an unrelated commit that added a test, or hide
   coverage this phase deleted behind one that did. 1267 passed and
   2 skipped is what `8816709` gave on 2026-10-02, recorded so the
   expected magnitude is known. This is the decision that matters
   most to get right, because the cheap alternatives all leave a
   green suite.

5.4. **The four frozen cache entries stay, and the reason is
   written down rather than inferred.** Decision 4.7 deferred them
   here. Keeping them costs one download per nightly dependencies
   build; removing them would foreclose the fix finding 6 points
   at, because an image that is not cached cannot later be served
   from the cache. So the entries stay and each gains a comment
   saying it is deliberately frozen and what would justify removing
   it. **This is the decision most likely to be argued with**: an
   unreferenced end-of-life image in a cache looks exactly like
   what this plan is about. The answer is that this plan is about
   producers of *runners* -- an image nothing boots cannot run a
   job on an unpatched kernel -- and that `debian:11` is not
   unreferenced anyway: shakenfist#3669's regression coverage boots
   it, over the internet, which is the defect rather than the
   justification.

   **Amended 2026-10-05.** This decision enumerated four entries and
   the cached list held five end-of-life ones. `ubuntu:18.04` was
   older than any it names, got no freeze comment because this
   enumeration did not reach it, and has been removed outright rather
   than frozen -- see bullet 15 and 5f (12). Four is the right number
   now because one was deleted, not because four was right when this
   was written.

5.5. **33fl is read before it is edited, and the read may close the
   issue without a deletion.** Finding 7 is three stale statements
   deep, so step 5c's first act is to ask the fleet what release it
   is on rather than to delete two instances on the strength of an
   issue written in September. If they are already on trixie, the
   step corrects the three statements and reports the evidence; if
   they are not, it deletes them, waits for the reconcile, and then
   does the same. Either way 5c does not close #826 -- 5e does, with
   5c's evidence, because closures are gathered in one step.

5.6. **private-ci#38 closes after the phase is confirmed, not after
   the last pull request merges.** It is the collated inventory and
   it closes when the thing it inventories is gone, which phase 4's
   status section said when it deferred it here. "Gone" is what 5f
   establishes, so #38 closes as 5f's final action rather than in
   5e, and the "after" half of its comment quotes 5f's output. It was
   5e when the phase was first written, which would have closed the
   inventory before anything confirmed the phase -- the defect 4i
   committed on development#123, reporting a compliance page as
   current status
   when it predated the merges it was describing.

#### Step plan

Every step that edits a repository other than this one opens a pull
request there and waits for that repository's own CI. Among the four
editing steps, 5a gates 5b and nothing else: 5c and 5d are independent
of both and of each other. The two observation steps are not free of
order either -- 5e follows all four, because it closes issues with
their merge commits, and 5f follows 5e and at least one nightly cycle
after 5a. No step prunes or regenerates `REVIEWS.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | worktree | **The phase's substance, in `shakenfist/private-ci`, one pull request and two commits** (decision 5.2). Commit one removes the `debian-11` and `debian-gnome-12` entries from `IMAGE_BUILDS` in `conductor/imagebuilder.py` and the `debian-11` entry from `CI_IMAGES` in `conductor/provisioner.py` (`:45-100`; there is no gnome entry there, see finding 2), and rewords the `STALE_LABEL_SECONDS` comment at `:195-198` which explains itself by naming "the debian-11 case in #40". **The reworded comment must not put any retired label in single quotes** -- name them bare or not at all. 5b's ordering gate and 5f check (1) both grep this file for `'debian-11'` and friends, so a comment keeping `'debian-11'` as a historical example makes both report a hit forever after. A false halt rather than a missed regression, but one that would be diagnosed at 5b rather than here. Commit two removes `debian-12` and `debian-12-docker` from both lists and deletes `debian-12` from `.github/actionlint.yaml` (finding 9; decision 4.2's reasoning). **The test sweep is the work, not the deletion.** Baseline first, on your own branch point, and record the numbers: `pytest conductor/tests` gave 1267 passed, 2 skipped on `8816709` on 2026-10-02, and if your branch point gives something else then that is your target rather than this -- then expect 22 failures in `test_provisioner_claims.py`, `test_imagebuilder.py` and `test_provisioner_create_workers.py`, every one of them a fixture naming a label that no longer provisions. Rename fixtures to surviving labels; do **not** add synthetic entries to the lists in test setup, and do not delete a test to make the count go green. `test_provisioner_create_workers.py:256-263` needs two *different* surviving labels and depends on their order in `CI_IMAGES`, so read its comment before choosing: `debian-13` and `debian-13-docker` satisfy it, one label used twice does not. Then sweep the files that do **not** fail: `test_web.py:176-182` asserts a 200 for a "known image" with `request_build` mocked, so it keeps passing while asserting something false (finding 4) -- rename it; `test_db.py`, `test_db_costs.py`, `test_provisioner_costs.py` and `test_staticrunners.py` use the label as an opaque `runner_os` or metadata string, so rename them for honesty and say in the commit message that nothing there was load-bearing. Finish with no fewer passed than your baseline and the same skipped; if you add a test -- `request_build` returning `False` for a retired name is the one worth adding -- name it in the commit message so the count rising is accounted for. Verify with `pre-commit run --all-files` and `tox -e flake8`. Commit subjects: `Retire the debian-11 and gnome-12 images.` and `Retire the Debian 12 runner images.` Do not touch `ansible/` in any repository -- that is 5b. |
| 5b | medium | sonnet | worktree | **After 5a has merged *and* one nightly cycle has built with the shortened list**, and gated on a grep rather than on this sentence: `git grep -n "'debian-11'" conductor/imagebuilder.py` in a fresh `private-ci` clone must return nothing, and the conductor's cycle summary for the night after must show no `debian-11` build. **That grep is known to fire rather than assumed to**: on `8816709`, the survey commit, it returns two lines -- the entry's `name` at `:59` and its `label` at `:62`, the `base_image` spelling `debian:11` deliberately not matching -- so zero means 5a landed rather than meaning the pattern was wrong. The quoted-label form is chosen over one naming the `'name':` key for the same reason: a pattern tied to the dict layout returns nothing if the layout is ever reshaped, which is a gate that cannot tell the two worlds apart. Then, in `shakenfist/actions`, delete the four dead `debian:11` conditions in `ansible/ci-image.yml` (`:49`, `:60`, `:455`, `:466`) and collapse each pair of `add_host` tasks into one. Two pairs: the rebuild host at `:39-60` and the test host at `:445-466`. They differ only in `ansible_python_interpreter: /usr/bin/python3`, which the surviving task must **not** carry -- the detect-system-python arm is the one that stays, which is what the `!= "debian:11"` condition meant. `IMAGE_BUILDS` is the only caller of this playbook, so after 5a nothing invokes it with `base_image: debian:11`; this is decision 3.3's reasoning applied to the file phase 3 did not reach, and decision 3.2's ordering is why it cannot land first. Afterwards `grep -n 'debian.11' ansible/ci-image.yml` returns nothing and `grep -c 'name: Add to ansible' ansible/ci-image.yml` returns **2**, which is the check that catches deleting the wrong arm of a pair. **That target is anchored rather than asserted**: the same grep returns **4** on `d72644f`, at `:39`, `:51`, `:445` and `:457`, so two afterwards is one surviving task per pair. Run `tools/ansible-syntax-check.sh` and `pre-commit run --all-files`. **The conductor reads this repository at `main` (`imagebuilder.py:41`, `ACTIONS_BRANCH = 'main'`), so the change is live on merge with no pinning to protect you** -- the same flag-day property 4h had. **Second commit in the same pull request:** in `ansible/ci-dependencies.yml`, add a comment to each of the four frozen cached image entries -- `debian:11`, `debian:12`, `ubuntu:20.04` and `fedora:40` -- saying it is deliberately frozen per decision 5.4 and naming what would justify removing it (nothing boots it any more, or the cache starts serving reads so that keeping it stops being free). Remove no entry, and do not touch `debian:13`, `rocky:10`, `ubuntu:22.04`, `ubuntu:24.04` or `centos:9-stream`. This is the only step that writes those comments, which 5f check (4) and the sixth done bullet both require; it is here rather than in 5a because 5a opens no pull request against `actions`. Nothing about the comments depends on 5a, but they wait for it anyway because they share this step's pull request and decision 5.2 keeps one pull request per repository. That costs nothing: 5f needs 5a and a nightly cycle regardless. An earlier draft called this half "gate-free", which was wrong -- the whole of 5b is gated. Commit subjects: `Drop the dead bullseye python branches.` and `Say which cached images are frozen.` |
| 5c | medium | sonnet | worktree | **`33fl`, and read before you edit** (decision 5.5). The premise of 33fl#826 is that the two GitLab static runners have no automated path off bookworm. `tools/retire-gitlab-runners.py` is that path, it has existed since 2026-07-30, and `rundaily.sh:262` runs it in the `--weekly` block; `static_runner_debian_release` became 13 on 2026-09-12 (`fa0e79d8`). So first establish what the eight runners in `static_runner_fleet` are actually running -- the `static-runners` namespace on sfcbr, via `sf-client instance list` and the instance creation times, or `ansible_distribution_release` from a one-off fact gather -- and report it before changing anything. If all eight are on trixie: delete nothing, and fix the three stale statements -- the comment at `group_vars/all/static_runners.yml:52-53` ("The gitlab runners have no retire tool") must name the tool and say the roll happens on the weekly cycle. If the two GitLab instances are still bookworm, delete them so the next reconcile rebuilds them, wait for it, then make the same correction. Either way leave `static_runner_debian_release: 13` alone and leave the paragraph about the audit's blindness to this fleet alone -- it is still true and phase 6 is where it goes. Do not close #826; 5e does that. Commit subject: `Say the gitlab runners do roll over.` |
| 5d | low | sonnet | worktree | One paragraph in this repository. `docs/audits/eol-distro.md:57-63` says `debian-gnome-12` "is listed although the CI conductor advertises no `debian-gnome-13` label yet" and that "what is missing is an entry in private-ci's `IMAGE_BUILDS` table, not an image". Phase 3 added that entry, so rewrite the paragraph to say the successor label exists and that a finding naming `debian-gnome-12` is now a request to retire the old entry rather than to add a new one. **Leave the end-of-life table at `:31-32` exactly as it is** -- it is the registry of banned labels and it must keep listing all five of `debian-11`, `debian-11-docker`, `debian-12`, `debian-12-docker` and `debian-gnome-12` after this phase, because its job is to name what a workflow may not ask for. Five banned labels against four retirements is not an inconsistency to resolve: `debian-11-docker` was never produced, and `debian-gnome-12` is a desktop image rather than a runner boot image. Do not touch `FROZEN_METADATA` or `FROZEN_ISSUE_TITLES`; the criterion's id, spec path and issue title do not change. Do not prune `REVIEWS.md`. Verify with `pre-commit run --all-files`. Commit subject: `Say debian-gnome-13 exists.` |
| 5e | medium | sonnet | none | Housekeeping, no commit, but **everything it writes is outward-facing and hard to retract**, which is why it is not the mechanical pair its first draft had: 4i ran low/haiku and produced two defects in one closing comment -- it double-counted a set of pull requests, and reported a compliance page as current status when that page predated the merges it was describing. File one issue in `shakenfist/shakenfist`: the nested cluster fetches `debian:11` from `images.shakenfist.com` although the bytes are on the attached `/srv/ci` cache disk, costing a cold ~407 MiB download inside `ansible_module_ci/004.yml`'s await budget, observed at 578 seconds against a 600 second timeout (shakenfist#4000). Give it the evidence from finding 6 -- `build-smoke-cluster/action.yml:257` uploads one cached image and no topology playbook sets a mirror -- and say explicitly that decision 5.4 keeps the cache entries and that this is about how they are served, not whether they exist. Then close private-ci#40, private-ci#45 and 33fl#826 with their merge commits. **Leave private-ci#38 open**: it closes in 5f, after the phase is confirmed, because its closing comment asserts an "after" state and nothing has checked that yet (decision 5.6). **Do not claim the audit confirms any of this** -- `eol-distro` does not read either list, and saying otherwise is the defect 4i produced in development#123's closing comment. **Never write a bot trigger phrase in any comment.** |
| 5f | medium | sonnet | none | **Confirms the phase, which nothing else does**, and cannot lean on the audit (see the status paragraph). Run after every pull request above has merged and at least one nightly cycle has completed. **Every check below, not a counted subset** -- this brief said "six" while listing ten for one round of review, which is enough for a step to stop at (6) and skip the only check the plan says has no automatic backstop: (1) in a fresh `private-ci` clone, `grep -n "'debian-11'\|'debian-12'\|'debian-12-docker'\|'debian-gnome-12'" conductor/imagebuilder.py conductor/provisioner.py` returns nothing, and `grep -rn 'debian-12' .github/` returns nothing; (2) `pytest conductor/tests` on 5a's merge commit gives no fewer passed and the same skipped as on its parent -- run it on both, because `master` moves and an absolute figure drifts; fewer passed means coverage was deleted, and more is fine if 5a's commit message names what it added. For scale, `8816709` gave 1267 passed and 2 skipped; (3) in `actions`, `grep -n 'debian.11' ansible/ci-image.yml` returns nothing, `grep -c 'name: Add to ansible' ansible/ci-image.yml` returns 2 against 4 on `d72644f`, and `tools/ansible-syntax-check.sh` passes; (4) the cached image list in `ansible/ci-dependencies.yml` still contains `debian:11`, `debian:12`, `ubuntu:20.04` and `fedora:40`, each carrying the freeze comment -- a check whose passing output is non-empty, deliberately (decision 5.4); (5) the conductor's nightly cycle summary for a night after 5a shows builds for the surviving labels only, with no `debian-11`, `debian-12`, `debian-12-docker` or `debian-gnome-12` line and no permanent `False`, read from the summary rather than inferred from the absence of a failure; (6) all eight entries of `static_runner_fleet` report Debian 13, read from the fleet; (7) in `private-ci`, `grep -n "'debian-1[12]'\|'debian-12-docker'\|'debian-gnome-12'" conductor/tests/test_web.py` returns nothing, and `test_build_known_image` names a label that `IMAGE_BUILDS` still contains -- **this is the check with no automatic backstop**, because `request_build` is mocked there and the suite passes either way (finding 4). The grep is known to fire: on `8816709` it returns four lines, `:179`, `:181`, `:182` and `:195`, so zero means 5a swept the file rather than meaning the pattern was wrong; (8) in `33fl`, `grep -n 'no retire tool' group_vars/all/static_runners.yml` returns nothing and `grep -n 'retire-gitlab-runners' group_vars/all/static_runners.yml` returns a line; (9) in this repository, `grep -n 'advertises no' docs/audits/eol-distro.md` returns nothing and its end-of-life table still lists all five banned labels; (10) `compliance.md`'s `eol-distro` section is byte-identical to the same section on the commit before 5a merged, ignoring the `*Generated ...*` line -- the phase changes no verdict, and this check is what distinguishes "unchanged" from "nobody looked"; (11) in `private-ci`, `grep -n 'debian-11\|debian-12\|debian-gnome-12' README.md AGENTS.md ARCHITECTURE.md` returns nothing. The third alternative is not redundant: `debian-12` does not match `debian-gnome-12`, so a pattern without it would pass while a mention of the retired desktop image survived -- the same label checks (1) and (7) name explicitly. Known to fire: on `bd70dca`, 5a's branch point, it returns **16** lines -- 13 in `ARCHITECTURE.md`, 2 in `AGENTS.md` and 1 in `README.md` -- so zero means the documentation was swept rather than meaning the pattern was wrong. **The labels are unquoted here where checks (1) and (7) quote them, deliberately: in documentation a bare prose mention is the thing being hunted, so this check forbids naming a retired label at all in these three files, including in a historical aside.** A future sentence that needs to name one belongs in `docs/` or in this plan, neither of which this check reads. This is the check 5a earned by sweeping files the plan had assigned to nobody, and it is here rather than nowhere because the done list grew a bullet for them. (12) in `actions`, `grep -n 'ubuntu:18.04' ansible/ci-dependencies.yml` returns nothing, and `grep -c 'Nothing currently boots this image from this cache' ansible/ci-dependencies.yml` returns **2**, not 3. Both halves are anchored on `08f69ec`, the commit before the removal, where the first returns **2** lines -- `:217` and `:218`, the URL and the name of a single entry, so a check written to expect one line would report half a removal as a pass -- and the second returns **3**. The second half is not decoration: the claim it counts was false of `ubuntu:20.04`, which the guest CI lane does boot from this cache, and a check that looked only for the deleted entry would pass while the comment that invited the wrong deletion survived six lines above it. Check (4) asserts the four kept entries are present; this one asserts the fifth is gone and that the reason the four are kept is stated truthfully. This is the check bullet 15 earned, and like 5f (11) it exists because the done list grew a bullet rather than because a step was planned to produce it. Report each check with its output. **A stale read looks healthy here too**: check (5) wants a cycle summary generated after 5a merged, not the most recent one you can find. **Then, and only if every check above agrees, close private-ci#38** (decision 5.6) with the before-and-after comment: what the fleet named when that inventory was written against what it names now, the "after" half quoting those outputs. If any check disagrees, leave it open and report. |

#### Risks and mitigations

**A label nothing appears to request may still be requested.**
Phase 4 cleared every `runs-on:` reference the fleet has, verified
across twenty-nine clones, and private-ci#40 says no workflow has
ever asked for `debian-11`. Both are greps of repositories, and a
static runner advertises only `self-hosted` and `static`, so a job
running on one names no operating system at all -- which is the
blindness 33fl#826 records. Mitigated by what the failure looks
like: a workflow asking for a label the conductor no longer offers
does not fail, it queues, so the signal is jobs pending with no
runner rather than a red check. 5f check (5) reads the cycle
summary, and the operator-visible symptom is a queue that does not
drain. Decision 5.2 is the other half of the mitigation: the
Debian 12 half is its own commit, so a revert is one commit.

**The test sweep can be satisfied by removing coverage.** Twenty-two
failing tests and a deadline is how a suite loses assertions. The
mitigation is a count rather than a judgement: no fewer passed and
the same skipped on 5a's merge commit as on its parent, named in 5a's
brief and re-checked in 5f check (2). Against the parent rather than
against 1267, because `master` moves while the phase runs. The
subtler version is the mocked boundary in finding 4,
which no count catches -- that one is mitigated by naming the file
and the line in the brief, because nothing else would find it.

**`actions` is live on merge.** The conductor reads
`ACTIONS_BRANCH = 'main'`, so 5b's playbook edit reaches the next
image build immediately, and every consumer of
`build-smoke-cluster` pins `@main` as well. This is the property
that turned 4h into two ordered pull requests. Mitigated by 5b's
ordering gate being a grep of the merged `private-ci` tree plus a
nightly cycle, not a sentence in this plan, and by the surviving-arm
check (`grep -c 'name: Add to ansible'` returns 2) being mechanical.

**The 33fl read may be unavailable when 5c runs.** It needs the
`static-runners` namespace on sfcbr to answer, and the survey could
not confirm from this host that `rundaily.sh --weekly` has run since
2026-09-12. If the read cannot be made, 5c stops and reports rather
than deleting instances on the strength of a stale issue -- deleting
a runner that is already on trixie costs a rebuild and an idle
queue for no gain.

**This phase cannot be confirmed by the audit.** Stated in the
status paragraph because it is the structural risk, not a caveat:
`compliance.md` reads the same before and after, so every check in
5f is a grep, a count or a log read. The phase that closes this gap
is phase 6, and the ordering is deliberate -- D4 retires producers
before they are measured, which means the measurement cannot be the
evidence that the retirement worked.

#### Definition of done

Fifteen bullets, and each one names its owner, because phase 4 closed
with four bullets no step had checked and that is recorded a thousand
lines above. 5f's twelve checks cover thirteen of them: bullets 1 and
2 are 5f (1), bullet 3 is 5f (2), bullet 5 is 5f (3), bullet 6 is
5f (4), bullet 7 is 5f (5), bullet 8 is 5f (6), bullet 4 is 5f (7),
bullet 9 is 5f (8), bullet 10 is 5f (9), bullet 13 is 5f (10),
bullet 14 is 5f (11) and bullet 15 is 5f (12). Bullet 12 and
the first half of bullet 11 -- the three issues closed with their merge
commits -- are 5e's own actions, verified by their being done. The
private-ci#38 half of bullet 11 is **5f's** final action, after its
checks agree, per decision 5.6. **If a bullet is added to this list,
5f gets a check for it in the same commit** -- the mapping is the
mechanism rather than the decoration. Four of these bullets had no
check when the phase was first written, which is the same gap phase 4
closed with, and it was found here only by counting them against 5f
(development#207).

- [x] In a fresh `private-ci` clone, `conductor/imagebuilder.py`'s
      `IMAGE_BUILDS` contains no `debian-11`, `debian-12`,
      `debian-12-docker` or `debian-gnome-12` entry, and
      `conductor/provisioner.py`'s `CI_IMAGES` contains none of the
      first three. Four entries and three entries, not seven and
      seven: the desktop images are not runner boot images
      (finding 2).
- [x] `grep -rn 'debian-12' .github/` in `private-ci` returns
      nothing. This is the one repository phase 4's equivalent
      bullet exempted, because it has no workflows for the
      declaration to govern; the declaration goes with the entries.
- [x] `pytest conductor/tests` on 5a's merge commit gives **no
      fewer passed and the same skipped** as on its parent. A green
      run with fewer passed fails this bullet; more passed is fine if
      5a's commit message names what was added. Measured
      against the parent rather than against a figure written here,
      because `master` moves while the phase runs; for scale,
      `8816709` gave 1267 passed and 2 skipped on 2026-10-02. The
      count is the criterion precisely because 22
      tests fail on the deletion alone and the cheapest ways to make
      them pass are all wrong (decision 5.3).
- [x] `conductor/tests/test_web.py`'s `test_build_known_image` names
      a label that `IMAGE_BUILDS` still contains. It does not fail
      when it stops doing so -- `request_build` is mocked -- which
      is why it is a bullet of its own rather than part of the one
      above.
- [x] In `shakenfist/actions`, `grep -n 'debian.11'
      ansible/ci-image.yml` returns nothing and `grep -c 'name: Add
      to ansible' ansible/ci-image.yml` returns **2**. The second
      half catches the inverse mistake of keeping the
      force-python3 arm instead of the detect arm, which a grep for
      the condition alone cannot see.
      `tools/ansible-syntax-check.sh` passes.
- [x] `ansible/ci-dependencies.yml`'s cached image list still
      contains `debian:11`, `debian:12`, `ubuntu:20.04` and
      `fedora:40`, and each carries a comment saying it is
      deliberately frozen and what would justify removing it. A
      grep whose passing output is non-empty, for the same reason
      phase 3's `debian:11` bullet was: a bullet asking for nothing
      would be satisfied by deleting the thing decision 5.4 keeps.
- [x] A conductor nightly cycle summary generated after 5a merged
      lists builds for the surviving labels only, with no
      `debian-11`, `debian-12`, `debian-12-docker` or
      `debian-gnome-12` line, and no permanent `False`. Read from
      the summary, not inferred from the absence of a failure
      issue.
- [x] All eight entries of `33fl`'s `static_runner_fleet` report
      Debian 13, read from the fleet rather than from
      `static_runner_debian_release`. The variable has said 13 since
      2026-09-12 and said nothing about what is running.
- [x] `group_vars/all/static_runners.yml` no longer says the GitLab
      runners have no retire tool, and names
      `tools/retire-gitlab-runners.py` and the weekly cycle
      instead. The paragraph about `eol-distro` being unable to see
      this fleet stays: it is still true and it is phase 6's.
- [x] `docs/audits/eol-distro.md` no longer says the conductor
      advertises no `debian-gnome-13` label, and its end-of-life
      table still lists all five labels it bans -- `debian-11`,
      `debian-11-docker`, `debian-12`, `debian-12-docker` and
      `debian-gnome-12`, which is not the set of four entries this
      phase retires: `debian-11-docker` has never been produced and
      `debian-gnome-12` is a desktop image rather than a runner boot
      image. Both
      halves: the table is the registry of what a workflow may not
      name, and emptying it as the labels go would make the
      criterion unable to report a regression.
- [x] private-ci#40, private-ci#45 and 33fl#826 are closed with
      their merge commits, in 5e. private-ci#38 is closed **by 5f**,
      after every one of its checks agrees, with a comment whose
      "after" half quotes their output -- not in 5e, because that
      comment asserts a state only 5f has established
      (decision 5.6).
- [x] An issue exists in `shakenfist/shakenfist` for the cache that
      is not serving reads (finding 6), naming
      `build-smoke-cluster/action.yml:257`,
      `ansible_module_ci/004.yml:120` and shakenfist#4000.
- [x] `compliance.md`'s `eol-distro` table is unchanged by this
      phase. Stated as a done criterion rather than omitted,
      because "the audit went green" is the evidence a reader will
      reach for and it is not available here: neither list this
      phase empties is read by any criterion until phase 6.
- [x] In `private-ci`,
      `grep -n 'debian-11\|debian-12\|debian-gnome-12' README.md
      AGENTS.md ARCHITECTURE.md` returns nothing -- all three
      alternatives, because `debian-12` does not match
      `debian-gnome-12`, and unquoted, because a bare prose mention
      is what this one hunts. 5a's, and added after 5a had already
      swept them: the plan assigned private-ci's own documentation
      to no step, and at `bd70dca`, 5a's branch point,
      `README.md:34` and `ARCHITECTURE.md:631` both documented
      `python3 -m conductor.imagebuilder --image debian-12`, a
      command that returns `False` once `IMAGE_BUILDS` loses the
      entry. `AGENTS.md:243` and `ARCHITECTURE.md:1603` additionally
      excerpted `CI_IMAGES` itself. Those line numbers are pinned to
      that commit because `master` moves while the phase runs, which
      is why 5f's check is a grep rather than a seek. A done list
      that did not mention these files is how the excerpts drifted
      in the first place.
- [x] In `shakenfist/actions`, `grep -n 'ubuntu:18.04'
      ansible/ci-dependencies.yml` returns nothing, and the cached
      list still carries the four entries decision 5.4 keeps. Not
      5b's and not any planned step's: decision 5.4 enumerated four
      frozen entries and `ubuntu:18.04` was a fifth, older one it
      did not reach, so 5b wrote four freeze comments and left this
      entry with none. It was unreferenced by every route checked --
      every `/srv/ci` path named anywhere in `shakenfist/*` and
      `Mach33Labs/*` resolves to three guest images
      (`ubuntu:20.04`, `debian:13`, `cirros`), the runner tarball
      and two testdata clones; no workflow, playbook or test outside
      the loop itself names `ubuntu:18.04` or `bionic`; and
      `images/build.sh:6` lists it under "Obsolete image builds (not
      built by default)", which is why what the dependency disk
      fetched nightly was an artifact last built on 2024-01-27, 786
      MiB of it per night onto a 50GB disk that asserts 2GB free.
      Removing it also corrected the `ubuntu:20.04` freeze comment,
      which 5b wrote claiming nothing boots that image from this
      cache: the guest CI lane in
      `.github/workflows/smoke-cluster.yml` uploads
      `/srv/ci/ubuntu:20.04` as the artifact `ubuntu-2004`, and
      `shakenfist`'s guest, smoke and cluster suites boot that name.
      Left as written it invited deleting a cache entry whose
      removal breaks tests, six lines above the entry whose removal
      saved a download. A done list that counted four frozen entries
      is how the fifth came to have no comment and no owner.

#### Back brief

Four things to agree before 5a starts, because each is cheap to
settle now and expensive to redo.

1. **`debian-gnome-12` is in this phase** (decision 5.1). If it is
   not, private-ci#45 cannot close here, decision 3.5's promise
   moves again, and 5a's fixture sweep splits differently -- the
   gnome fixtures in `test_imagebuilder.py` would have to stay.
   This is the finding that changed the phase's size, so it is the
   first thing to confirm.
2. **The frozen cache entries stay** (decision 5.4). The opposite
   call is defensible and it is a four-line deletion, but it
   forecloses the fix in finding 6 and it contradicts decision 4.7,
   which deferred the question here rather than deciding it. If they
   are to go, say so before 5b, because the comment it writes is the
   thing that would have to be unwritten. (5b's second commit, not
   5a's: 5a opens no pull request against `actions`. This said 5a when
   the phase was first written, which left the comments with no owning
   step at all; development#207 is where that was caught.)
3. **Two commits in `private-ci`, not two pull requests** (decision
   5.2). The revert granularity is the point; the cost is one review
   covering both halves.
4. **5c reads the fleet before deleting anything** (decision 5.5).
   The alternative is to follow 33fl#826 as written and delete the
   two GitLab instances, which costs a rebuild if they are already
   on trixie. This is only a gate because the issue, the config
   comment and this plan all say something the repository's own
   tooling contradicts.

### 6. Close the audit's blind spot

Closes: nothing filed. Depends on: phases 3-5, complete 2026-10-05,
so the producers are compliant before they are measured. Planning
effort: high, 2026-10-05.

**Status: in progress, 2026-10-07.** 6a merged as #222, 6b as
#226 and 6c as images#14, 6d filed images#13, and 6e ran on
2026-10-07 -- all eleven checks agreed, and what each one returned
is recorded under *What 6e confirmed* below. The phase still reads
`In progress` here and in the Execution table because a phase is
closed out in the first commit of the next phase and cannot record
its own merge commit; phase 7 carries that. A fully ticked done
list beside an in-progress status is the expected intermediate
state, not a contradiction. Each of 6a, 6b, 6d and 6e is amended in
its row below with what it turned out to need. The survey below was run on
2026-10-05, the day phase 5 closed, and it moved the phase in one
direction: the cost of measuring `images` is higher than this
section said, and the reason the section gave for that cost being
acceptable does not hold. The dependency, though, paid off exactly
as written -- `eol-distro` now reports **pass** on `images`, a real
scan rather than a skip, because phases 3 to 5 made the producer
compliant before anything measured it. Had this phase run first it
would have filed the finding it exists to prevent.

#### What this section asked for, 2026-09-13

Left as written, because the survey below contradicts parts of it
and the contradiction is the useful record:

The phase that stops the 80 findings coming back. Per the
structural finding above, all three producers sit outside the
criterion that bans what they produce.

* **shakenfist/images**: decide whether it joins the audit matrix,
  and on what terms. Scope is stated in three places and a change
  has to make all three agree in one commit, or `scope-coverage`
  reports the repository as decided nowhere and
  `AuditScopeIsStatedOnceTest` fails: the `repo:` matrix in
  `.github/workflows/consistency-audit.yml`, which is what
  actually runs, and the in-scope and excluded lists in
  `docs/audits/README.md`, which are what a reader is told.
  `REPO_OVERRIDES` in `scripts/audit/repo.py` is not a scope
  statement -- it narrows a repository already in the matrix -- so
  it is only edited if images is to be scoped to a subset.

  Joining is otherwise all-or-nothing: in the matrix means all 52
  criteria apply. Measured against the current clone on
  2026-09-13, images would report **8 fail, 6 pass, 38 not
  applicable**. The eight are `llm-context-lint-ci`, `renovate`,
  `ci-review-automation`, `pre-commit-config`, `export-repo-config`,
  `default-branch-naming`, `github-security` and
  `delete-branch-on-merge`. That is a morning of `consistency`
  issues rather than a wall, and every one of them is already an
  item in phase 5 of that repository's own plan, so the decision
  is whether to file them or do them first -- not whether the
  repository can survive being measured. Re-measure before acting;
  this number is from before phase 5 runs.
* **private-ci**: extend `only_checks` to cover a criterion that
  reads `IMAGE_BUILDS` and `CI_IMAGES` for end-of-life bases. It
  is currently scoped to four plan criteria and `sfui-vendor` --
  five `only_checks` entries in total.

  A partial scope is stated a fourth time, as the sentence in the
  partial-scope paragraph of `docs/audits/README.md` that
  `scripts/audit/scope.py` parses and holds against `only_checks`.
  Its docstring records that this is the statement with the worst
  track record: `only_checks` was widened once with the sentence,
  and two other documents, left behind and no test noticing. So
  the same commit updates the sentence in `docs/audits/README.md`,
  the `REPO_OVERRIDES` comment block in `scripts/audit/repo.py`,
  and the comment above `- private-ci` in the audit matrix -- which
  is already stale, still reading "Scoped to the sfui-vendor
  check".
* **33fl**: outside this organisation and this tooling. #826
  records the exposure in `group_vars/all/static_runners.yml`
  where the next reader will find it, which is the available
  answer; note here that a static runner fleet is structurally
  invisible to a label-based audit, so any future fleet of the
  same shape needs the same treatment.

**And images is not the only repository outside the matrix.**
Eight non-archived repositories are outside it: `images` itself,
plus `client-python-ova`, `divergulent-reviews`, `homebrew-tap`,
`performance`, `reproducables`, `sonobouy` and
`uefi-latency-guest`. None of them is invisible -- every one is on
the excluded list in `docs/audits/README.md`, and `scope-coverage`
reconciles that list and the matrix against the organisation every
morning, which is the criterion written precisely so that a
repository in neither list stops being silent. What is true is
narrower: an excluded repository gets no verdict from any
criterion, and these exclusions were decided before several of the
criteria that would now apply to them existed, `eol-distro`
included. Phase 4's survey ran the criterion over all eight by hand,
and the result narrows the question further than expected: seven of
them satisfy all three of the criterion's skip conditions -- no
`.github/workflows/`, no container build file anywhere in the tree
and no top-level `templates/` directory -- so `eol-distro` would
report `not applicable` for every one of them even if they were in
the matrix. All three were checked, not just the first: `EolDistro`
skips only when the three are empty together
(`scripts/audit/checks/distros.py:450`). `images` is the only one of
the eight where the exclusion costs a verdict, and it is the one this
phase was already about. So phase 6 is revisiting a recorded
decision rather than discovering an omission, and the other seven
need a sentence in `docs/audits/README.md` saying they are
excluded for having nothing to audit rather than for the reasons
the list currently gives -- which is a smaller and more honest
change than adding them. **That sentence goes in as prose.** The
excluded list sits inside the span `scripts/audit/scope.py` parses
between the literal phrases `are **excluded**` and ``The `actions`
repository``, and `bulleted_block()` collects only `* `-prefixed
lines: a plain prose line is ignored and is safe, but the same
sentence written as a bullet raises `ScopeParseError` for not being
a repository name, and a sub-heading introducing it raises it for
running the list past a heading. Either failure takes `scope-coverage`
down fleet-wide rather than locally, and `AuditScopeIsStatedOnceTest`
in `scripts/tests/test_registry.py` is what will say so at commit
time.

Which instrument does the measuring is Q1 above, and the default
there is a separate criterion.

#### What the survey found

Run 2026-10-05 against `main` at `13fec36`, with a fresh `images`
clone at `3423343` and the audit driven by
`scripts/audit-check.py`, which builds a real `GhCli` when no
client is passed (`scripts/audit/repo.py:163`) -- so the
GitHub-dependent verdicts below are genuine readings rather than
artifacts of running without a token. That distinction matters
here: a criterion failing because nobody asked GitHub looks
identical, in a summary, to one failing because the setting is
wrong.

**Confirmed, unchanged.** Scope is still stated in three places
and `AuditScopeIsStatedOnceTest` still holds them together
(`scripts/tests/test_registry.py:42`). `private-ci`'s `only_checks`
is still exactly five entries -- `plan-phase-references`,
`plan-source-references`, `plan-index`, `plan-template`,
`sfui-vendor` -- and `scope.documented_partial_scope()` parses the
sentence at `docs/audits/README.md:87-89` to that same five, so
the fourth statement and the code still agree. The matrix comment
above `- private-ci` is still stale in exactly the way the section
says, still reading "Scoped to the sfui-vendor check"
(`.github/workflows/consistency-audit.yml:53-54`). Eight
non-archived repositories are still outside the matrix, and they
are the same eight. All seven of the non-`images` ones still
satisfy all three of `eol-distro`'s skip conditions -- no
workflows, no container build file, no top-level `templates/` --
checked by clone rather than by assertion, so the criterion would
report `not applicable` for every one of them. The three producers
still sit outside the criterion that bans what they produce.
`33fl`'s audit-blindness paragraph is intact above
`static_runner_debian_release: 13`.

**The dependency worked.** `eol-distro` reports **pass** on
`images`, with the detail "No workflow runner label or container
image names any of the 3 retired distribution releases". That is a
scan, not a skip: `images` has workflows and build files, so the
three-way skip at `scripts/audit/checks/distros.py:453` did not
fire. The producer is compliant before it is measured, which is
what "Depends on: phases 3-5" was for.

**Stale, and corrected at source in this commit.**

* **52 criteria is now 58.** `registry.CHECKS` holds 58. Six
  criteria arrived between 2026-09-13 and today, so every
  arithmetic statement built on 52 was going to drift whether or
  not anybody noticed.
* **`images` measures 12 fail, 17 pass, 29 not applicable**, not
  8 / 6 / 38. The section told the reader to re-measure before
  acting, which is the instruction that saved this: the numbers
  were not merely stale, they were stale in the direction that
  makes the decision harder rather than easier.
* **`delete-branch-on-merge` now passes**, so the section's list
  of eight failures is wrong in membership as well as in count.
  The other seven it names all still fail.
* **Five failures the section does not name**: `llm-doc-structure`,
  `readme-structure`, `docs-external-links`, `plan-template` and
  `secret-scanning-ci`.
* **"every one of them is already an item in phase 5 of that
  repository's own plan" is false.** `images`' own Phase 5,
  "Repository standards"
  (`docs/plans/PLAN-image-build-modernisation.md:503-530`), names a
  `.pre-commit-config.yaml`, renovate configuration, and the
  `master` versus `develop` question -- which covers
  `pre-commit-config`, `renovate` and `default-branch-naming`, and
  touches `llm-doc-structure` by way of an `AGENTS.md` rewrite.
  **Eight of the twelve have no owner in that plan or in this
  one.** This was the load-bearing claim: it is what made
  "whether to file them or do them first" the whole decision, and
  it does not hold.
* **"Joining is otherwise all-or-nothing" is false**, and the same
  bullet names the mechanism that refutes it two sentences later.
  `only_checks` scopes a repository in the matrix to a subset, and
  `private-ci` is the live proof. The section even allows for
  editing `REPO_OVERRIDES` "if images is to be scoped to a
  subset", so the all-or-nothing framing contradicts its own next
  clause rather than the tree.
* **33fl#826 is closed**, by step 5e on 2026-10-05. The section
  says the issue "records the exposure ... where the next reader
  will find it", which conflated the issue with the file. The
  durable record is the comment block in
  `group_vars/all/static_runners.yml`, verified present; a closed
  issue is not where a next reader looks.
* `EolDistro`'s skip is at `distros.py:453` rather than `:450`.

#### Scope

In: a new consistency criterion that reads the runner-label
producer definitions `eol-distro` cannot see; the scope changes
that let it run against `private-ci` and that bring `images` under
`eol-distro`; one prose sentence correcting why seven repositories
are excluded; one correction to a comment in `images` whose stated
reason phase 5 removed; and one issue recording what measuring
`images` found.

Out: **fixing any of `images`' twelve findings.** Three of them
belong to `images`' own Phase 5, a fourth -- `llm-doc-structure` --
is touched by that phase's planned `AGENTS.md` rewrite without
obviously being closed by it, and the other eight belong to nobody
yet; this phase's job is to make the repository measurable
and to record the bill, not to pay it. Also out: widening
`eol-distro` itself (decision 6.1), banning the publication of
guest images on an end-of-life release (decision 6.3), bringing
the other seven excluded repositories into the matrix (they would
all report `not applicable`, so it would buy nothing), auditing
`33fl` (another organisation, decision 6.4), and the `master`
versus `develop` question for `images`, which is one of the twelve
and therefore out with the rest.

#### Decisions

**6.1 A separate criterion, not a wider `eol-distro`.** This
adopts Q1's stated default rather than re-opening it, and the
survey strengthens the reason: `eol-distro` now *passes* on
`images`, so widening it would convert a green verdict into a
mixed one whose title no longer describes what it measures. The
decisive argument is unchanged -- an issue title is the
fleet-wide idempotency key for filing and closing, frozen in
`scripts/tests/test_metadata.py` precisely because changing one
orphans every issue already open under the old title. The new
criterion is `eol-producers`: "this repository offers a banned
label", against `eol-distro`'s "this repository names one".

**6.2 The criterion measures runner-label producers, and nothing
else.** Decided 2026-10-05, and it is the decision the rest of the
phase hangs off. `eol-producers` reads `IMAGE_BUILDS` and
`CI_IMAGES` in `private-ci` -- the two definitions that turn a
published image into a bootable runner label, which is precisely
the exposure that produced the 80 findings in #123. It does not
read a build list, and it does not care what images exist.

The alternative was to measure production as well, and the survey
is what ruled it out. `images/build.sh:80` still builds
`debian:12`, `debian-docker:12`, `debian-gnome:12` and
`debian-xfce:12` by default, so a criterion reading the build list
would fail `images` on day one, four times, against a documented
deliberate choice: Debian LTS covers bookworm until 2028, and a
guest image somebody boots on purpose under LTS is not the same
defect as a runner label a workflow gets handed by default. **The
distinction the criterion encodes is who chooses.** A consumer
naming a runner label mostly inherits whatever the producer
decided; somebody booting a `debian:12` guest image chose that
release. The first is the fleet-wide exposure this plan exists to
close, and the second is a supported option with an expiry date
nobody is currently misreading.

**6.3 The stale justification in `images` is corrected, not
inherited.** `build.sh:41-45` keeps `debian:12` in the default
list because "private-ci bakes the debian-12 runner labels that
sixteen repositories boot on from it", and tells the reader to
"retire it as the eol-distro audit issues are closed, not
before". Step 5a removed those labels and #123 is closed, so both
halves of that sentence are now false while the conclusion it
supports -- keep building bookworm under LTS -- remains correct
for a different reason. Phase 6 rewrites the comment to give the
reason that still holds and to stop pointing at a condition that
has already arrived. **It does not change the build list.** That
is decision 6.2 applied to the same file: the list is right, the
explanation is wrong, and leaving a true conclusion resting on a
false premise is how the `ubuntu:20.04` freeze comment in phase 5
came to read as an invitation to delete something load-bearing.

**6.4 The criterion reads one producer, not three.** `private-ci`
is the only one of the three that defines runner labels.
`images` publishes guest images, which decision 6.2 puts out of
scope, and `33fl` is in another organisation and outside this
tooling -- no criterion can reach it, and this plan does not
prescribe how another organisation audits itself. What phase 6
adds there is the generalisation rather than a fix: a static
runner fleet that advertises only `self-hosted` and `static` is
structurally invisible to a label-based audit, so any future
fleet of that shape needs the same treatment. That sentence goes
in `docs/audits/eol-producers.md`, where a reader of the criterion
will find it, rather than in a closed issue.

**6.5 `images` joins the matrix scoped to `eol-distro` alone.**
This is the decision most likely to be argued with, so the
reasoning is stated at length. The case for joining fully is
real: `images` is built from every night, the fleet's CI depends
on its output, and it has been on the excluded list as a
"historical archive" which it demonstrably is not. The case
against is the survey's: joining fully files twelve findings, of
which eight have no owner in any plan. Phase 4 closed with four
done bullets no step had checked and this plan records that a
thousand lines above; filing eight findings with no owner is the
same mistake pointed outward, at a repository whose maintainers
did not ask for them.

So `images` enters the matrix with `only_checks: ['eol-distro']`.
What that buys is narrow and worth having: the pass this survey
measured by hand becomes a verdict the audit reports on a
schedule, so the next time a producer starts naming a retired
release the instrument says so instead of a person noticing. It
also leaves the full-audit decision where `images`' own plan
already put it -- "a deliberate decision rather than a side
effect of this plan". Narrowing honours that sentence; joining
fully overrides it as a side effect of this plan, which is the
thing it asked not to happen.

The counter-argument worth stating: a narrow scope is a decision
that can quietly become permanent, and `private-ci`'s stale
matrix comment is evidence of how that decays. The mitigation is
that step 6d files the bill as an issue, so widening later is a
decision against a written list rather than a rediscovery.

**6.6 The seven excluded repositories get one prose sentence, and
it must not be a bullet or a heading.** This is mechanically
forced rather than stylistic. The excluded list sits in the span
`scripts/audit/scope.py` parses between `are **excluded**` and
``The `actions` repository`` (`scope.py:46-48`), and
`bulleted_block()` collects only `* `-prefixed lines: a prose line
is ignored and is safe, the same sentence as a bullet raises
`ScopeParseError` for not being a repository name, and a
sub-heading introducing it raises it for running the list past a
heading. Either failure takes `scope-coverage` down fleet-wide
rather than locally. `AuditScopeIsStatedOnceTest` is what says so
at commit time, which is why every scope edit in this phase runs
`pre-commit` before it is committed rather than after.

**6.7 The twelve findings are filed as one issue, not twelve, and
not as audit findings.** The audit will file its own issues once
`images` is in the matrix, and only for the one criterion
`only_checks` admits -- `eol-distro`, which passes and is not one
of the twelve. So none of the twelve is an audit finding under
decision 6.5: they are what joining fully *would* cost, which is a
planning fact rather than a compliance one. One issue against
`images`, naming all twelve with an ownership verdict for each:
three its own Phase 5 owns, `llm-doc-structure` which that phase's
`AGENTS.md` rewrite bears on without obviously closing, and eight
nobody owns.

**6.8 Each scope statement changes in one commit with the thing it
describes.** `private-ci`'s widening touches four places and
`images`' joining touches three plus `REPO_OVERRIDES`; a commit
that moves some and not others leaves `scope-coverage` reporting a
repository as decided nowhere. The docstring for the partial-scope
sentence records that this is the statement with the worst track
record -- widened once with two other documents left behind and no
test noticing -- which is why the test exists and why this
decision is written down rather than assumed.

#### Step plan

Five steps. 6a is the substance and everything else depends on
it: `only_checks` names a criterion id, so the id has to exist in
`registry.CHECKS` before any scope statement can mention it. 6a
was planned as two commits and merged as four in #222, the two it
grew coming from review. 6b is one pull request against this
repository, planned as three commits and merged as five in #226,
because decision 6.8 wants each scope statement atomic and
decision 5.2's one-pull-request-per-repository habit is worth
keeping. The two it grew are recorded in its row: both are the
parse this phase is about failing to hold, found during 6b rather
than during the survey. 6c is the only step that touches another
repository's code.

**Line numbers in the briefs are at `13fec36`**, where the survey
ran. 6a and 6b edited several of the files they cite, so those
numbers have since moved; where a brief also names a symbol --
`EolDistro.run`'s skip, `bulleted_block()`,
`documented_partial_scope()` -- the symbol is the reference and the
line number is the context. The `images` references in 6c and 6d
are at `3423343`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | worktree | **The criterion, in this repository, one pull request.** Add `eol-producers`: a repository is non-compliant if a runner-label producer definition it owns offers a label the end-of-life table in `docs/audits/eol-distro.md` bans. Read that table for the banned set rather than hardcoding it -- `retired_releases()` in `scripts/audit/checks/distros.py` already parses it and reports "3 retired distribution releases", and a second hardcoded copy is the defect `eol-distro` was written to avoid. The producer definitions are `IMAGE_BUILDS` in `conductor/imagebuilder.py` and `CI_IMAGES` in `conductor/provisioner.py`, both in `private-ci`; parse them as Python rather than by grep, because a grep cannot tell a live entry from a label named in a comment and 5a's reworded `STALE_LABEL_SECONDS` comment is exactly that case -- the plan made 5a avoid single-quoting retired labels there precisely so a grep-shaped gate would not fire forever, and a criterion that repeats the mistake inherits the same false positive. Use `ast` and read the dict literals. **Decision 6.2 is the scope: runner labels only.** Do not read `images/build.sh`, do not read a build list, and do not report on published guest images. The deliverables are `docs/audits/eol-producers.md` (the specification, carrying decision 6.4's generalisation about label-less static fleets), the check class beside `EolDistro` in `scripts/audit/checks/distros.py`, its registration in `scripts/audit/registry.py`'s `CHECKS`, and the matching entries in `scripts/tests/test_metadata.py`'s `FROZEN_METADATA`, `FROZEN_ISSUE_TITLES` and `FROZEN_COLUMN_NAMES` -- `AUDIT_METADATA` and `ISSUE_TITLES` are derived in `scripts/audit_common.py:48-50`, so the frozen copies are what you edit and the test is what holds them together. Tests go in `scripts/tests/test_distros.py` using `CheckTestCase` from `scripts/tests/base.py:166`. **Prove the criterion fails, do not assert that it would.** It must report `fail` against a `private-ci` clone at `8816709`, naming `debian-11`, `debian-12`, `debian-12-docker` and `debian-gnome-12`, and `pass` against current `master`; run both and put the outputs in the commit message. A criterion that has only ever been seen to pass is indistinguishable from one whose pattern is wrong, which is the failure mode this plan has spent six phases on. Also assert the `not_applicable` path: a repository with no `conductor/` reports not applicable rather than passing, because "no producer here" and "a compliant producer here" are different answers and a criterion that conflates them will report the whole fleet green. Verify with `pre-commit run --all-files`, which runs all five test suites. Commit subjects: `Add the eol-producers criterion.` and `Register eol-producers in the audit.` **Amended 2026-10-06, after the step ran.** Merged as #222 in four commits, not two; both additions came from review. `Read eol-producers tables all or nothing.` makes an entry the parse cannot read -- not a dict literal, a non-literal key, zero or two label keys, a label that is not a string -- raise `ProducerParseError` rather than be skipped, and does the same for a module-level change to a table after the assignment that is read: skipping let a table pass on whichever entries happened to be literal, which is a vacuous pass in this plan's own vocabulary. `Narrow what the producer change guard claims.` then limits what that guard is documented to catch to changes made through the table's own name, rather than tracking aliases. The back brief's gate on the parse's shape was settled by keying off label strings: each release's runner labels live in the one `EOL_RELEASES` table `eol-distro` already reads, and `retired_labels()` inverts it, so the mapping is in one place. |
| 6b | medium | sonnet | worktree | **After 6a has merged**, gated on a check rather than on this sentence: `python3 -c "import sys; sys.path.insert(0,'scripts'); from audit import registry; print([c.id for c in registry.CHECKS])"` in a fresh clone must list `eol-producers`. **Three commits, one pull request, and each one must leave the tree passing `pre-commit`** -- `AuditScopeIsStatedOnceTest` holds the scope statements against each other at every commit, so a commit that moves three of four places fails at commit time rather than in review (decision 6.8). Commit one widens `private-ci`: add `eol-producers` to `only_checks` in `scripts/audit/repo.py:88-93`, making six entries, and update in the same commit the sentence at `docs/audits/README.md:87-89` that `scope.documented_partial_scope()` parses, the `REPO_OVERRIDES` comment block above the entry, and the comment above `- private-ci` in `.github/workflows/consistency-audit.yml:53-54` -- **which is already stale and must be fixed rather than appended to**: it reads "Scoped to the sfui-vendor check" while the scope has been five checks for some time. Commit two brings `images` into the matrix with `only_checks: ['eol-distro']` (decision 6.5): the `repo:` list in the audit matrix, a new sentence in the partial-scope paragraph of `docs/audits/README.md`, and a `REPO_OVERRIDES` entry with a stated reason, because that module's docstring requires one and "an unexplained override is indistinguishable from silencing a real finding". Commit three adds decision 6.6's prose sentence about the seven remaining excluded repositories. **It must be a prose line, not a bullet and not under a sub-heading**: `bulleted_block()` in `scripts/audit/scope.py:137` collects `* `-prefixed lines between `are **excluded**` and ``The `actions` repository``, so a bullet raises `ScopeParseError` for not being a repository name and a heading raises it for running the list past its end, and either takes `scope-coverage` down fleet-wide. The seven are `client-python-ova`, `divergulent-reviews`, `homebrew-tap`, `performance`, `reproducables`, `sonobouy` and `uefi-latency-guest`; all seven were confirmed on 2026-10-05 to have no `.github/workflows/`, no container build file and no top-level `templates/`, so `eol-distro` would report `not applicable` for every one -- say that is why they stay excluded, which is a narrower and truer claim than the reasons the list currently gives. Verify after each commit with `pre-commit run --all-files`, and afterwards confirm `scope.documented_partial_scope()` returns six entries for `private-ci` and one for `images`. Commit subjects: `Audit private-ci for eol-producers.`, `Bring images into the audit matrix.` and `Say why seven repositories are excluded.` **Amended 2026-10-06, after the step ran.** Three corrections, all of them this phase's own subject matter rather than incidental. (i) **The in-scope and excluded lists do not change.** An earlier draft of this row said `images` "moves from one to the other"; it does not, and doing it fails the suite. A partially scoped repository is in the matrix, in the excluded list, absent from the in-scope list, and named in the partial-scope paragraph -- which is what `private-ci` has been doing all along. `test_matrix_matches_the_documented_scope` subtracts the partially scoped set before comparing and `test_no_audited_repo_is_also_documented_as_excluded` subtracts it again, with a comment saying both statements being true is correct. (ii) **Two anchors in `scripts/audit/scope.py` could not survive the edit this step makes.** `IN_SCOPE_END` was the literal `'One project is in scope'`, which stops matching on precisely the edit that adds a second scoped repository, and `PARTIAL_SCOPE_END` matched only the plural `' checks, and nothing else.'`, so a repository scoped to exactly one check -- which `images` is, and which had never existed before -- could not be written in grammatical English and parsed. Both were fixed in their own commit before the documentation change that needed them, `Read scope anchors that do not count.`, with the anchor made count-free rather than bumped to "Two" so a third scoped repository needs no edit. (iii) **`only_checks` was held to nothing that executes.** Deleting a scoped repository's line from the audit matrix left all 1546 tests passing, for `private-ci` as well as `images`: the two comparisons above both subtract the scoped set, so the repository is on neither side of either equality, and `scope-coverage` asks `organisation - matrix - excluded`, where being excluded makes it decided whether or not anything runs it. Fixed in a fifth commit, `Hold only_checks to what the matrix runs.` All three were found by mutating the tree rather than by reading it, which is the only way to tell a statement that holds from one that cannot fail. |
| 6c | low | sonnet | worktree | **`shakenfist/images`, one commit, comment only.** Rewrite the paragraph at `build.sh:41-45` per decision 6.3. It currently keeps `debian:12` in the default build list because "private-ci bakes the debian-12 runner labels that sixteen repositories boot on from it, and Debian LTS covers bookworm until 2028", and instructs the reader to "retire it as the eol-distro audit issues are closed, not before". **Step 5a removed those runner labels and development#123 is closed, so the premise and the trigger are both spent while the conclusion is still right.** The new comment keeps bookworm in the list for the reason that survives -- Debian LTS covers it until 2028 and a guest image somebody boots on purpose is a supported option -- and states that the runner labels it used to feed were retired on 2026-10-03 by private-ci#100, so a future reader does not go looking for sixteen repositories that no longer boot it. Name the condition for removing the entry in terms that can actually arrive: the LTS end date, not an audit issue that is already closed. **Change no build list and no code.** `images` is not in the audit matrix when this step runs and the audit does not read `build.sh` under decision 6.2, so nothing here is gated on 6a or 6b; it is in this phase because the survey found it and the sentence it corrects is about this plan's own work. Do not touch the `fedora:43`/`fedora:44` paragraph or the `ubuntu:20.04` retirement block. Commit subject: `Say why bookworm is still built.` |
| 6d | medium | sonnet | none | **Housekeeping, no commit, outward-facing.** File one issue in `shakenfist/images` recording what measuring the repository found, so decision 6.5's narrow scope is a decision against a written list rather than something a later reader has to rediscover. Re-run the measurement rather than copying these numbers -- `python3 scripts/audit-check.py --repo-path <clone> --repo-name images` from a `development` checkout, which builds a real `GhCli` when none is passed, so the GitHub-dependent verdicts are genuine. On 2026-10-05 against `images` at `3423343` it gave 58 criteria, 17 pass, 12 fail, 29 not applicable. List the twelve, and **say for each whether anything owns it**: `pre-commit-config`, `renovate` and `default-branch-naming` are items in `images`' own Phase 5, "Repository standards" (`docs/plans/PLAN-image-build-modernisation.md:503-530`), which also plans an `AGENTS.md` rewrite that bears on `llm-doc-structure`; the other eight -- `llm-context-lint-ci`, `readme-structure`, `docs-external-links`, `plan-template`, `ci-review-automation`, `secret-scanning-ci`, `export-repo-config` and `github-security` -- have no owner in that plan or in this one. Say that `eol-distro` passes, and that `only_checks` admits it alone, so the audit will file none of the twelve -- `eol-distro` is not one of them. **Do not claim this plan will fix them** and do not open a pull request. **Never write a bot trigger phrase in any comment**: the match is a substring anywhere in the body, including inside backticks, and two of those phrases start workflows that push commits -- grep the body for `retest`, `re-review` and `recheck` before posting. **Amended 2026-10-06, after the step ran.** Filed as images#13 on 2026-10-05. It states an ownership verdict for each of the twelve, splitting them three, one and eight as above, but it inherited this row's earlier wording: three times it says "eleven" where it means the twelve -- the audit will not file "the other eleven" -- counting `eol-distro` as one of the twelve when it passes. 6e (10) checks for that wording, so the phase cannot close on it. |
| 6e | medium | sonnet | none | **Confirms the phase.** Run after 6a, 6b and 6c have merged and at least one scheduled audit has run. **Every check below, not a counted subset**, each reported with its output, and each anchored one is run on its anchor too -- an anchor is what distinguishes "the work landed" from "the pattern was wrong". **A check that names a command this environment refuses is a defect in the check**: phase 5's check (6) specified an `ansible` fact gather against live hosts, the classifier blocked it, and the step substituted evidence and reported twelve of twelve. Nothing below reaches outside git, GitHub and this repository's own scripts. (1) In a fresh `development` clone, `registry.CHECKS` includes `eol-producers`, and `docs/audits/eol-producers.md` exists. (2) `eol-producers` reports `fail` against a `private-ci` clone at `8816709` naming all four retired labels, and `pass` against current `master` -- both run, because the pass alone proves nothing. (3) `eol-producers` reports `not_applicable` against a clone with no `conductor/`; use this repository. (4) `scope.documented_partial_scope()` returns six entries for `private-ci` and `['eol-distro']` for `images`, and `scope.matrix_repos()` contains `images` -- 22 entries against 21 on `13fec36`, which is the anchor that catches an edit to the README that never reached the matrix. (5) `grep -n 'Scoped to the sfui-vendor check' .github/workflows/consistency-audit.yml` returns nothing; on `13fec36` it returns one line at `:53`. (6) `python3 -m pytest scripts/tests/test_registry.py` passes, which is `AuditScopeIsStatedOnceTest` agreeing that all four statements match. (7) `scope.documented_excluded()` still parses, returns the seven plus the archived repositories and **still contains `images`**, which is the convention `private-ci` follows and is why the bullet count below is unchanged rather than one short, and the new sentence about the seven is present as prose -- `grep -c '^\* ' ` over the excluded span is unchanged from `13fec36`, which is the check that catches the sentence having been written as a bullet. (8) An audit run after 6b merged -- scheduled, or dispatched where the weekly cadence leaves none in between, which `docs/consistency-audits.md` prescribes after a fix merges, to confirm the criterion passes -- reports `eol-producers` for `private-ci` and `eol-distro` for `images` on the compliance page, read from the generated page rather than inferred; name the run and its generation timestamp, and compare it in UTC against 6b's merge time rather than against the most recent page you can find. (9) In `images`, `grep -n 'sixteen repositories' build.sh` returns nothing and `grep -n 'debian:12' build.sh` still returns the default build list line -- the comment changed and the list did not, which is decision 6.3 in one check. (10) An issue exists in `shakenfist/images` listing all twelve findings, each with an ownership verdict -- three owned by its own Phase 5, `llm-doc-structure` touched by that phase's `AGENTS.md` rewrite, eight unowned -- and `gh issue view` on it does not contain the word `eleven`, which is the check that catches the issue counting `eol-distro` among findings it never was. (11) The three vacuous-pass fixes 6b grew are each held by a mutation rather than by a passing test: restore `PARTIAL_SCOPE_END_PATTERN` to the plural-only form and `test_a_one_check_partial_scope_sentence_parses` must fail; restore `IN_SCOPE_END` to `'One project is in scope'` and the lead-in change must break three tests; delete `- images` and then `- private-ci` from the audit matrix and `test_every_scoped_repo_is_in_the_audit_matrix` must fail naming that repository each time. Restore from a copy rather than with `git checkout`, and report what each mutation said. Report each with its output. **Then, and only if every check agrees**, tick this phase's done list and report; this phase closes no issue, so there is no gated outward-facing action. If any check disagrees, report and stop. **Amended 2026-10-07, after the step ran.** Check (8) originally required *a scheduled audit run* and nothing else. `33def50` had already moved the cadence to `0 18 * * 0`, so the next scheduled run fell on 2026-10-11 18:00 UTC, five days and eight hours after 6b merged, and the check as written could not be satisfied before then. A dispatched run was accepted instead, on the basis that `docs/consistency-audits.md` says to dispatch the workflow by hand after a fix merges, "to confirm the criterion passes, which also brings the compliance page up to date" -- 6b's `REPO_OVERRIDES` entry and matrix entry are such a fix, landing in *this* repository -- and the check's wording above was widened to say so. The widening and the tick were the same edit, which is why it is marked here rather than left to be inferred: a check loosened at the moment it passes is the shape this phase exists to report, and it is not exempt because the phase is the one doing it. What was *not* loosened is the comparison: the page's own generation timestamp is still read and still compared in UTC against 6b's merge. |

#### Risks and mitigations

* **The criterion passes vacuously.** A check that reads two dict
  literals and finds nothing can mean the producer is clean or
  that the parse found no dicts at all. 6a's brief requires a
  demonstrated `fail` on `8816709` and a separate
  `not_applicable` assertion, so all three answers are
  distinguished; 6e re-runs both rather than trusting the commit
  message. Checked by 6e (2) and (3).
* **A scope edit lands in three places out of four.** This is the
  statement with the worst recorded track record in the
  repository. `AuditScopeIsStatedOnceTest` fails at commit time
  rather than in review, and decision 6.8 keeps each statement in
  one commit. Checked by 6e (4) and (6).
* **The new sentence about the seven is written as a bullet** and
  takes `scope-coverage` down fleet-wide rather than locally.
  Mechanically caught: `bulleted_block()` raises
  `ScopeParseError`, and the same test suite runs it. Checked by
  6e (7), which counts bullets in the span rather than reading
  the sentence.
* **`images` joining files findings nobody expects.** Mitigated by
  decision 6.5 narrowing `only_checks` to one criterion that
  already passes, and by 6d filing the rest as one planning issue
  with ownership stated per item. The residual risk is that the
  narrow scope becomes permanent by inertia; the written list is
  the mitigation, and it is a weaker one than a date.
* **6c's comment rewrite drifts into a build-list change.** The
  conclusion in that comment is correct and only its reasons are
  stale, which is the shape most likely to invite "while I am
  here". The brief forbids touching the list and 6e (9) checks
  both halves: the old premise gone, the list still there.
* **A check cannot be run as written.** Phase 5 produced exactly
  this and it reported twelve of twelve. Every check in 6e is
  confined to git, GitHub and this repository's own scripts, with
  no command that reaches a live host.

#### Definition of done

Eleven bullets, and each one names its owner, because phase 4
closed with four bullets no step had checked and phase 5 grew two
more after its own list was written. This list grew one too, for
what 6b found: the rule below was followed and 6e has a check for
it. All eleven map to a 6e check; the tenth is 6d's action, and
6e (10) verifies it. What each check returned is under *What 6e
confirmed* below, because a ticked box records that somebody
looked and not what they saw.
**If a bullet is added to this list, 6e gets a check for it in the
same commit** -- the mapping is the mechanism rather than the
decoration.

- [x] `registry.CHECKS` includes `eol-producers` and
      `docs/audits/eol-producers.md` exists, carrying decision
      6.4's sentence about label-less static fleets. 6e (1).
- [x] `eol-producers` reports `fail` on a `private-ci` clone at
      `8816709`, naming `debian-11`, `debian-12`,
      `debian-12-docker` and `debian-gnome-12`, and `pass` on
      current `master`. Both run; the pass alone is not
      evidence. 6e (2).
- [x] `eol-producers` reports `not_applicable`, not `pass`,
      against a repository with no producer definition. 6e (3).
- [x] `private-ci`'s partial scope is six criteria, stated
      identically in `only_checks`, the sentence in
      `docs/audits/README.md`, the `REPO_OVERRIDES` comment and
      the audit matrix comment. 6e (4) and (6).
- [x] The audit matrix no longer says `private-ci` is "scoped to
      the sfui-vendor check". 6e (5).
- [x] `images` is in the matrix, scoped to `eol-distro`, named in
      the partial-scope paragraph of `docs/audits/README.md`, and
      still on the excluded list and still absent from the
      in-scope list -- the shape `private-ci` already has. 6e (4).
- [x] The seven remaining excluded repositories are explained by
      one prose line inside the excluded span, and the number of
      bullets in that span is unchanged. 6e (7).
- [x] An audit run after 6b merged reports `eol-producers` for
      `private-ci` and `eol-distro` for `images`, read from the
      generated compliance page. Satisfied 2026-10-07 by a
      **dispatched** run, `37560807580`, not a scheduled one: the
      cadence went weekly in `33def50`, so when 6e ran the next
      scheduled run was four days out, and
      `docs/consistency-audits.md` says to dispatch the workflow
      by hand after a fix merges, to confirm the criterion passes.
      6e (8).
- [x] `images`' `build.sh` no longer justifies bookworm by the
      runner labels phase 5 retired, and its default build list
      is unchanged. 6e (9).
- [x] An issue in `shakenfist/images` lists all twelve findings
      with an ownership verdict for each -- three owned by that
      repository's own plan, `llm-doc-structure` touched by it,
      eight owned by nobody -- and does not count `eol-distro`
      among them. 6d's action. 6e (10).
- [x] No scope statement passes vacuously: neither anchor in
      `scripts/audit/scope.py` encodes a count or a plural, and
      `only_checks` is held to the audit matrix. Each demonstrated
      by a mutation that fails, not by a test that passes. 6e
      (11).

#### What 6e confirmed

6e ran on 2026-10-07 (AEDT), against `main` at `bed667e`, a
`private-ci` clone at `8816709`, `shakenfist/images` at `707768f`
and the dispatched audit run `37560807580`. Bare dates here are
local and timestamps are UTC. All eleven checks agree. Recorded
here for the same reason phase 4 recorded 4j's: the phase is not
confirmed by its merges, the pull request conversation scrolls
away, and this is the part of the record a later reader cannot
reconstruct.

(1) `registry.CHECKS` includes `eol-producers` and
`docs/audits/eol-producers.md` exists, carrying decision 6.4's
sentence about label-less static fleets at `:106-107` -- a fleet
advertising only `self-hosted` and `static` is structurally
invisible to a label-based audit.

(2) Against the `private-ci` clone at `8816709`, `eol-producers`
reports **fail** with seven findings, naming all four retired
labels: `conductor/imagebuilder.py:62` (`debian-11`), `:68`
(`debian-12`), `:80` (`debian-12-docker`), `:123`
(`debian-gnome-12`), and `conductor/provisioner.py:47`
(`debian-11`), `:53` (`debian-12`), `:70` (`debian-12-docker`).
Against current `master` (`5c97e9e`) it reports **pass**. Both were
run, because the pass alone proves nothing -- and the pass is the
one that needed the `ast` parse rather than a grep: `grep -n
'ubuntu-2004' conductor/imagebuilder.py` on that same tree returns
`:83`, a comment recording the label's removal, so a grep-based
criterion would report fail on a clean tree.

(3) Against this repository, which has no `conductor/`,
`eol-producers` reports **not_applicable** rather than pass.

(4) `scope.documented_partial_scope()` returns six ids for
`private-ci` and `['eol-distro']` for `images`.
`scope.matrix_repos()` returns 22 entries including `images`,
against 21 on `13fec36` -- the anchor that catches a README edit
that never reached the matrix.

(5) `grep -n 'Scoped to the sfui-vendor check'
.github/workflows/consistency-audit.yml` returns nothing and exits
1. On `13fec36` it returns one line at `:53`.

(6) `python3 -m pytest scripts/tests/test_registry.py` passes: 56
tests and 31 subtests, which is `AuditScopeIsStatedOnceTest`
agreeing that all four scope statements match.

(7) `scope.documented_excluded()` parses without raising and
returns 19 entries, `images` among them. The `* `-prefixed bullet
count inside the excluded span is 19, unchanged from `13fec36`,
which is the check that catches the new sentence about the seven
having been written as a bullet. `images` staying on that list is
the convention `private-ci` follows and is why the count is
unchanged rather than one short.

(8) Satisfied by a **dispatched** run, `37560807580`, not a
scheduled one -- see the amendment on the 6e row for why, and note
that the page's two timestamps are different things. The page was
*generated* at 2026-10-07 03:03:29 UTC and *committed* as
`bed667e` at 03:35:04; both are after 6b's merge (`6163de8`,
2026-10-06 09:43:54 UTC), so the comparison holds on either, and the
generation timestamp is the one the check names. Under
`## eol-distro` the page reads `images | compliant`; under
`## eol-producers`, `private-ci | compliant`. Each is `N/A` under
the other criterion, which is the scoping working rather than a
gap, and `private-ci` is the only non-N/A row under
`eol-producers` because it is the only repository in the fleet
that defines producers at all.

(9) In `images`, `grep -n 'sixteen repositories' build.sh` returns
nothing, and `grep -n 'debian:12' build.sh` still returns the
default build list at `:85`. The comment changed and the list did
not, which is decision 6.3 in one check.

(10) images#13 lists all twelve findings with an ownership verdict
each: three owned by that repository's own phase 5
(`pre-commit-config`, `renovate`, `default-branch-naming`),
`llm-doc-structure` touched by the same phase's `AGENTS.md`
rewrite, and eight owned by nobody. `gh issue view` on it does not
contain the word `eleven`, which is the check that catches the
issue having counted `eol-distro` among findings it never was.

(11) Three mutations, each applied and reverted from a file copy
rather than with `git checkout`, and each one fails:

* `PARTIAL_SCOPE_END_PATTERN` restored to the plural-only form
  makes `test_a_one_check_partial_scope_sentence_parses` fail with
  `ScopeParseError`, naming the phrase it could not find.
* `IN_SCOPE_END` restored to `'One project is in scope'` breaks
  exactly three: `test_matrix_matches_the_documented_scope`,
  `test_the_in_scope_end_anchor_survives_a_second_partial_repo`
  and `test_the_parse_anchors_still_delimit_their_lists`.
* Deleting `- images` from the audit matrix makes
  `test_every_scoped_repo_is_in_the_audit_matrix` fail naming
  `images`; restored, deleting `- private-ci` fails the same test
  naming `private-ci`. That second one matters more than the
  first: it is the pre-existing instance, open since partial
  scoping was invented, and it is what made this a class rather
  than a slip in 6b.

The full suite was re-run clean after each revert.

#### Back brief

Gate before 6a writes any code: **the shape of the criterion's
parse.** Reading two dict literals out of Python with `ast` is
cheap to propose and expensive to redo, and there is a real
choice inside it -- whether the criterion keys off the label
strings the dicts contain or off the release names the end-of-life
table lists, with the mapping between them living in one place or
two. Agree that before editing, because getting it wrong produces
a criterion that works on today's dict layout and silently stops
working when somebody reshapes it, which is the failure
`eol-distro`'s quoted-label pattern was already chosen to avoid.
**Passed, and how is recorded in 6a's row**: the criterion keys off
label strings, with the label-to-release mapping kept only in
`EOL_RELEASES`.

No gate on 6b, 6c, 6d or 6e: each is mechanical against a brief
that names its files, and each has a check in 6e that fails
loudly rather than quietly.

### 7. Push audit

Per the push audit shared block in `PLAN-TEMPLATE.md`. Runs
`PUSH-AUDIT.md` over the accumulated diff of every phase against
`main`, not the diff of the last phase alone.

**There are no out-of-repository audits for this phase to cite.**
Phases landing elsewhere record `<repo> <sha> (#pr)`, and the
shared block allows citing an audit run as part of the pull request
that landed them. The finding under phase 2 checked all eleven
landings and found that not one carries such an audit, and that
none of the four repositories has a `PUSH-AUDIT.md` to have run.
An earlier draft of this paragraph asserted the citing as fact;
restating a policy in the past tense is how a plan comes to believe
it has evidence it never collected, which is the defect phase 1's
correction records.

Of the three options that finding names, **this phase takes the
first**: it runs the accumulated audit itself, once per repository,
against that repository's default branch. Deferring the three
`shakenfist/images` landings to that repository's own phase 6 would
leave actions#74 and 33fl#836 covered by nothing, and declining the
gap in writing buys nothing when running the audit is the work this
phase exists to do. There is no single accumulated diff spanning
four repositories, so the record must name which repository each
audit covered and the sha it was taken against.

**Which runbook, given that none of the four has one.** The shared
block's carve-out applies and is invoked here deliberately: a
repository with no `PUSH-AUDIT.md` still carries the phase, and the
phase says the runbook does not exist yet and what was done instead.
So this phase does not pretend to run a runbook that is absent, and
it does not borrow this repository's. That one is written for this
repository's blast radius -- `AGENTS.md` here is explicit that a
defect breaks sixteen other repositories quietly rather than
breaking a service -- which is the wrong question to ask of an image
build host or a Grafana repository.

Instead phase 7 carries **its own checklist, written into this
plan**, applied to each of the four repositories' accumulated diff:

1. Every landing's recorded sha is the merge commit of its pull
   request, with two parents, and the diff read is against the first
   parent. Already done once for all eleven, under phase 2 -- so
   this step is a re-read only if the Execution table has gained
   rows since.
2. Anything the diff added that runs unattended -- cron, a systemd
   timer, a nightly workflow -- either reports its own failure
   somewhere a human sees, or is named as not doing so. This is the
   check images#8 would have failed: a self-update under `errexit`
   whose one explanatory line went to a cron mailer that does not
   exist.
3. No secret, token or private hostname entered a public repository.
4. Anything the diff removed has no consumer left, checked by grep
   across the four repositories rather than by assumption -- the
   same check step 3f's cache-disk destination needed.
5. For `shakenfist/images` and `shakenfist/private-ci`, whether the
   change can fail closed: a broken build that publishes nothing is
   recoverable, a broken build that publishes something wrong is
   not.

Landing a `PUSH-AUDIT.md` in each of the four repositories would be
the better answer and is explicitly **not** this phase's work: four
runbooks written for four blast radii is its own plan, and phase 7
is not the place to discover that. Phase 7 files an issue proposing
it, against this repository, and says in the plan that it did.

**Audit images#8 first.** It is the one landing where this is not
bookkeeping: it broke the nightly build on its first night, and an
audit of the accumulated diff is precisely the instrument that
would have looked at a self-update running under errexit. The rest
is catch-up; that one is the demonstration that the catch-up is
worth doing.

Only phase 6 and this phase land in this repository, so the
accumulated diff against `main` here is the small part of the work.

## Agent guidance

Deliberately short. Six of the seven phases land in other
repositories, under their own conventions and, for
shakenfist/images, its own `PLAN-TEMPLATE.md` and the
project-specific checks in it. Per-step effort levels, model
recommendations and review checklists belong in the plan of the
repository the work lands in, not here, so this plan does not
carry the shared blocks `PLAN-TEMPLATE.md` offers for them.

The exception is phase 6, which lands here. It changes what the
fleet is measured against and reaches `scripts/audit/`, so it is
high effort per this repository's own guidance, and it is
exercised with `--dry-run` before anything files an issue.

## Risks and mitigations

**The migration is done and the detection is not.** The most
likely failure of this plan is that phases 3-5 close visible
issues, phases 1, 2 and 6 close none, and the plan is declared
finished. That is the ordering D1 exists to prevent, and it is why
phases 1 and 2 come first despite closing nothing.

**Phase 4's label half is long and boring, and that is the half that
moves.** Twelve repositories with an actionlint edit each, and the
daily audit files and closes the per-repository issues itself, so
progress is externally visible rather than tracked by hand -- the
fleet cleared 38 of the original 80 references that way before the
phase started, grew three new ones while doing it, and cleared a
thirteenth repository, `shakenfist/actions`, twenty-five minutes after
this plan merged. The risk moved next door twice. Phase 4's survey
found a second class of consumer the criterion cannot see, and being
boring is what made it easy to believe the criterion's count was the
whole job; then shakenfist#4280 stopped that second class moving at
all for three days, and shakenfist#4309 closed it before any step had
acted on the deferral -- so the phase was back to closing with the
count it was planned to close with, and phase 5 waiting on a phase
rather than on a bug. Amended 2026-09-26: that holds only if 4f's
default move survives. Its post-merge `kerbside` run failed, and a
revert would put the under-cloud sites back on `ci-images/debian-12`
with phase 5's Debian 12 half waiting on the diagnosis again. The
label half is still going well. It is no longer the whole phase, and
it was never the part worth watching. Amended 2026-09-29: the default
move survives. The `kerbside` failure did not recur and the second
verification run passed once actions#112 fixed the renderer inference
behind it, so phase 5 is again waiting on a phase rather than on a
bug. The risk that fired was not the one this paragraph names: what
broke was a playbook that had been reading the distribution version to
decide how to configure a network, in a repository this phase was not
editing, exposed by a default this phase moved.

**Retiring a label early breaks CI fleet-wide.** D4 and the phase 5
ordering exist for this. A `debian-12` retirement before phase 4
completes takes runners out from under jobs still requesting them.

**`debian:11` is already unrecoverable.** It cannot be rebuilt, so
the published copies are all that will ever exist. Phase 3 moves
the dependencies disk off it; until then, do not delete those
copies -- they are load-bearing and were deliberately kept when the
mislabelled images were deleted on 2026-09-12.

## Administration and logistics

### Success criteria

* Every one of the nine issues is closed, explicitly declined in
  writing here, or reduced to a named residual recorded in this
  plan. The residual case is not a loophole: phase 1 closes only
  part of private-ci#44 and says so, because that issue's fourth
  checkbox -- what actually caused the 2026-09-12 miss -- is a
  separate defect that the scheduler fix does not explain.
* An image that fails to build, or stops being built, produces a
  GitHub issue without a human looking for it -- demonstrated for
  each of the three producers.
* A published image that does not match its own name fails its
  build rather than publishing, demonstrated by a deliberate test.
* `eol-distro` reports zero findings across the fleet, and the
  producer definitions are inside something that measures them.
* No runner label is retired while any workflow still requests it.
* `pre-commit run --all-files` passes, and
  `scripts/audit-check.py` reports no new failures for this
  repository.

### Documentation index maintenance

One row in `docs/plans/index.md`, dated 2026-09-13, status kept
current as phases land. The row reaches `Complete` only when every
phase has completed, been abandoned or been superseded.

### Future work

* **Boot-testing published images.** Phase 2 asserts that an image
  matches the name it is published under; nothing asserts that it
  boots. That is the largest
  remaining quality gap in the pipeline and it is a plan of its
  own.
* **A Fedora image that builds.** `fedora:43` and `fedora:44` have
  never built -- both fail on `grpcio-tools` needing a C++
  compiler -- so Fedora is absent from the nightly list entirely
  and the `fedora` convenience symlink points at an end-of-life
  `fedora:42`.
* **The convenience symlinks.** `debian` points at `debian:12` and
  should probably point at `debian:13`; `debian-docker` points at
  `debian-docker:12`, which served Debian 11 for two years.
* **Checksums or signatures for published images.** Consumers
  fetch `latest.qcow2` over HTTPS with no way to verify what they
  received.
* **The ~70 MB of unexplained variance** between two
  `debian-13-docker` builds made hours apart from the same
  refreshed base (1243.3 MB then 1314.1 MB). It does not threaten
  the 174 MB staleness measurement, but it means single size
  measurements are noisier than they look.

### Bugs fixed during this work

Fixed as phases landed:

* **The conductor skipped a nightly rebuild on restart**
  (private-ci#49, phase 1). `builder_loop` recomputed `nightly_due`
  from the clock into a local at every start, so a conductor coming
  back after the nightly hour set the next rebuild to tomorrow and
  nothing recorded that tonight had not happened. The slot is now
  claimed in the database. This does **not** explain the 2026-09-12
  miss that prompted private-ci#44 -- that issue's fourth checkbox
  stays open as a possibly separate defect.
* **A nightly that failed halfway could be retried in full by every
  subsequent restart** (private-ci#49, phase 1). Found while fixing
  the above; the slot is claimed when the cycle starts rather than
  when it completes, while the staleness metric reads only
  completions.

Already fixed before this plan was written, and recorded here
because they are the evidence for it:
images#2 (the sixteen-day outage, the grub filesystem
incompatibility and the two-year bullseye mislabelling) and
actions#66 (`debian-13-docker` publishing without a docker client).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
