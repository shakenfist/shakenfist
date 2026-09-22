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
| 4. The consumer sweep | In progress | |
| 5. Retire the end-of-life producers | Not started | |
| 6. Close the audit's blind spot | Not started | |
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

**Status: in progress, and its guest-image half is blocked.**
One step landed out of band, one marker the phase was going to
add turned out to be already there and broken, and a bug in
another repository blocks the under-cloud half. All three are
recorded under *Amended 2026-09-21* at the end of the survey, and
the affected steps carry the correction inline.

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
  does not reach.
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
half this phase.** shakenfist#4280, filed 2026-09-20 03:53 out of the
canary `actions` ran for its own migration. Instances booted on a
Debian 13 hypervisor never reach agent ready -- `agent_state: "not
ready (no contact)"`, `agent_start_time: null` -- and the three
`test_agentop_deadlines` tests went from 137, 192 and 207 seconds to
roughly 1820 each, consuming the step's whole 45-minute budget so
that the rest of the suite never ran. Two canary runs six hours
apart on the same topology, `35471083618` green and `35483225701`
red, with the under-cloud image the only difference between them.
`actions` reverted its own default in `2e0d32a` ("Keep the
under-cloud on bookworm.") and wrote the evidence into the input's
comment at `.github/workflows/smoke-cluster.yml:55-65`. This plan
does not fix it: D5's reasoning applies, and a plan that closes a
migration is not the place to debug a hypervisor's side channel.

**The blocker splits the guest-image half in two, and only one half
is stuck.** The survey treated "the guest images the CI clusters
boot" as one thing. #4280's evidence separates them, and that
separation is the useful part of it:

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
`sf://upload/system/debian` asserts no release, so the 43 references
can be renamed while #4280 is open and the artifact's contents can
follow later without a second fleet-wide edit. The decision was
argued for the next desktop bump; it earns its keep sooner than that.

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
| 4b | low | sonnet | worktree | Two repositories, same shape as 4a, separated only because each has more than two lines. `shakenfist/sfui` (`functional-tests.yml:18`, `:51`) and `client-python-k3s` (`functional-tests.yml:103`, `:225`, `release.yml:73`, `supply-chain.yml:67`). Both already declare `debian-13`; delete `debian-12` from each `actionlint.yaml`. Same commit subject and same verification as 4a. **`shakenfist/actions` was the third repository here and is already done** -- `e2a56bd` moved its three `runs-on:` lines and deleted both retired declarations, the stray `debian-12-docker` included, on 2026-09-20 in answer to actions#69. Confirm that rather than assume it, because this step's original brief is the record of what was needed. The check is `grep -rn 'debian-12' .github/` in a fresh clone, and the one hit it should return is the under-cloud default at `.github/workflows/smoke-cluster.yml:67`, which is a guest image rather than a runner label and is blocked by shakenfist#4280 -- do not "finish" the migration by moving it. That was still one hit on 2026-09-21: `2e0d32a` wrote an eleven-line reason above that input at `:55-65`, and it quotes `ci-images/debian-13`, the move it is refusing, rather than the `-12` default below it, which is the convention 4g's item (1) carries to the other six sites. Read the hit rather than the count, though: a hit in a `runs-on:` line, or a `debian-12` list item in `.github/actionlint.yaml`, is what a partial label migration looks like and is what this step completes. A hit inside a comment is a reason, not a finding. Do not re-edit what is already correct. |
| 4c | medium | sonnet | worktree | The two `*-docker` repositories, one pull request each. `shakenfist/ryll`: five `debian-12` (`ci.yml:209`, `:290`, `:351`, `:385`, `supply-chain.yml:48`) and twelve `debian-12-docker` (`ci.yml:83`, `:115`, `:138`, `:235`, `fuzz.yml:60`, `manual-build.yml:99`, `mermaid-lint.yml:77`, `release.yml:76`, `:272`, `:319`, `:392`, `supply-chain.yml:66`). `shakenfist/occystrap`: four `debian-12` (`functional-tests.yml:50`, `python-unit-tests.yml:47`, `release.yml:71`, `supply-chain.yml:76`) and one `debian-12-docker` (`mermaid-lint.yml:77`). Both declare `debian-13` but neither declares `debian-13-docker`; add it, and delete both Debian 12 declarations. Medium rather than low because these are the repositories whose jobs actually use the docker daemon: the `debian-13-docker` image ships `docker.io` *and* `docker-cli` only since shakenfist/actions#66, and the build runs `docker version` so a broken image fails rather than publishing -- so if a docker job misbehaves on the new label, report it rather than working around it, because it means that fix regressed. Commit subject: "Move CI onto the Debian 13 runner pool." |
| 4d | low | sonnet | worktree | **The repositories no other step edits**, as two pull requests. First, one line in `shakenfist/instar`. `.github/workflows/functional-tests.yml:597` is `image: 'debian:12'`, deliberate test input in a matrix that covers several releases (decision 4.3). **A marker already exists and does not work; move it, do not add a second.** `:588-595` is a comment beginning `# audit-ok: eol-distro -- Debian 12 is a SUPPORTED TARGET here,` which explains the matrix entry better than any wording this plan would have proposed. A range rather than a length, because the range is self-checking against the `:596`/`:597` anchors below it: re-read on 2026-09-21 at instar `e3239f5`, it is eight lines, and an earlier draft of this brief said eleven, which is `actions`' block at `smoke-cluster.yml:55-65` and would have swallowed both anchors. It has never taken effect, because `is_excepted()` reads the finding's own line and exactly one line above it (`scripts/audit/checks/distros.py:275-279`) and this marker sits nine lines up. Keep the prose where it is -- a reader needs it at the top of the entry -- and move a marker **carrying its own short reason** onto `:596`, the `- name: 'Debian 12'` line, or directly above `:597`, indented to match: `# audit-ok: eol-distro -- supported target, see the note above` or similar. Not the bare token. `docs/audits/eol-distro.md:169-176` specifies the shape as "Mark the line, or the line above it, with the reason", and its worked example carries one; a dangling `-- ` satisfies `EXCEPTION_RE` and would leave the fleet's canonical instance of this marker as a token with nothing after it. Check the result with the criterion rather than by eye: re-run this plan's scan snippet against the edited clone and confirm instar returns zero findings. Change nothing else: instar moved its runner labels on 2026-09-16 and this is its only remaining finding. Its `.github/actionlint.yaml` declared `debian-13`, `debian-13-docker` and neither retired label on 2026-09-19, so there is nothing to delete -- but read it rather than trusting that sentence, and if a retired label is declared, delete it in this commit and say so, since no other step in the phase touches this repository. Commit subject: "Put the eol-distro marker where the audit reads it." -- the image has been marked deliberate since before this phase was planned, and what this commit changes is where the mark sits, which is also the general lesson and the second time the fleet has hit it. Second, `shakenfist/kerbside-patches`: it has fourteen workflows, every one of them already on `debian-13`, and its `.github/actionlint.yaml` still declares `debian-12`. It is not one of the repositories the criterion lists, because a declaration produces no finding -- it is decision 4.2's failure state in the repository that most recently migrated, and nothing else in this phase looks at it. Delete the declaration; there is no workflow line to change. Commit subject: "Stop declaring a retired runner label." `private-ci` also declares it and is deliberately left alone: it has no workflows at all, so the declaration governs nothing, and the repository is excluded from this criterion. Say that in the pull request rather than leaving it looking unnoticed. |
| 4e | medium | sonnet | worktree | `shakenfist/shakenfist`, runner labels only. Five lines: `.github/workflows/functional-tests.yml:536`, `:726`, `mermaid-lint.yml:94` (`debian-12-docker`), `pin-indirect-dependencies.yml:55`, `release.yml:105`. `.github/actionlint.yaml` declares neither replacement: add `debian-13` and `debian-13-docker`, delete `debian-12` and `debian-12-docker`. **Runner labels only.** The same workflow file also names the guest image `sf://label/ci-images/debian-12` at `:443`, `:463`, `:477`, `:515`, and those are step 4g -- moving them here would put a guest-image change into a pull request reviewed as a runner move. Medium because this repository's functional tests are the heaviest in the fleet and a provisioning failure here is expensive to diagnose from a red matrix. Commit subject: "Move CI onto the Debian 13 runner pool." |
| 4f | high | opus | worktree | **Additive, and it must merge before 4g. Reduced by shakenfist#4280: the guest-image defaults this step was also going to move are blocked, and only the release-neutral upload remains.** In `shakenfist/actions`, `build-smoke-cluster/action.yml`. The action uploads the cached image into the cluster it just built as artifact `debian-12`, and 43 references in `shakenfist` read it back as `sf://upload/system/debian-12`. Decision 4.5 moves that name to `debian`, with no release in it. **The upload line does not read the way a grep for the command would expect.** `build-smoke-cluster/action.yml` assembles `sf-client artifact upload` into a `${setup}` shell variable at `:260` and invokes it at `:263`, which literally reads `"${setup} debian-12 /srv/ci/debian:12 --shared --no-checksum"`. The artifact name and the source path are on `:263`; the verb is not. Those numbers are from 2026-09-21 and moved by fourteen lines when `e2a56bd` rewrote the comment above them, which is the reminder that they are a grep target rather than an address. Do it in two landings so neither repository is ever reading a name the other does not write: this step adds a *second* upload under the name `debian`, sourced from `/srv/ci/debian:13`, which phase 3 put on the cache disk (`ansible/ci-dependencies.yml:194`); 4h removes the old one after 4g has landed. **`/srv/ci/debian:13` is the right path and `/srv/ci/cached/debian:13` is not**: the builder mounts the dependencies disk at `/srv/ci/cached` and writes `{{item.name}}` into it, while the cluster nodes that run this upload mount the same disk at `/srv/ci` (the `Mount /srv/ci` tasks in the `ci-topology-*.yml` playbooks), so the file the builder wrote as `/srv/ci/cached/debian:13` is `/srv/ci/debian:13` on the node doing the uploading. Do not "correct" the path to the builder's spelling. **Leave the existing `debian-12` upload exactly as it is, source path included.** Additive has to mean the content too: repointing `:263` at `/srv/ci/debian:13` would leave an artifact called `debian-12` containing trixie, and the 43 references in `shakenfist` would start exercising Debian 13 one merge before the repository whose tests would explain a failure. Decision 4.7 keeps `/srv/ci/debian:12` cached for exactly this. **The two guest-image defaults this step was also going to move are blocked and must not be touched.** `build-smoke-cluster/action.yml:32` and `.github/workflows/smoke-cluster.yml:67` stay on `sf://label/ci-images/debian-12` until shakenfist#4280 closes, and a pull request that removes their comments while moving the default is the mistake this sentence exists to prevent. **The two comments are not the same comment, and this step levels them up.** `2e0d32a` wrote the full evidence above the workflow input at `.github/workflows/smoke-cluster.yml:55-65`, naming `shakenfist/shakenfist#4280`; the same commit wrote a three-line pointer above the action input at `build-smoke-cluster/action.yml:28-30` which says to see the workflow for the evidence and **does not name the issue**. Verified on 2026-09-21: `grep -rn 4280` over a fresh `actions` clone returns the workflow comment and `docs/actions.md`, and nothing in `action.yml`. Bare rather than anchored there on purpose -- the substring trap the risks section documents is in `shakenfist`, not in this repository, and a bare grep over one clone is the wider net. The set check across the fleet is the anchored one. Add the issue reference to that pointer comment in this step's landing -- a comment-only edit that does not move the default, so it does not touch the blocked half. **Name `shakenfist/shakenfist#4280`, and do not quote the image string in the comment**, which is 4g item (1)'s rule and is here for 4g's reason: the definition of done counts these sites with a `ci-images/debian-12` grep that counts lines, so a comment repeating the name reports a compliant site as the ninth one that fails. It is not cosmetic: the definition of done requires each of the eight deferred sites to carry a comment naming the issue, and the risks section's mitigation is that the eight are greppable as a set -- with `grep -rn --exclude='*.md' --exclude-dir=.git 'shakenfist#4280' .`, and not with the bare `grep -rn 4280`. Both halves of that command matter for the reasons the risks section gives: the anchor because the bare form matches an epoch timestamp in `agentoperation.py`, and the `--exclude` because `development` is in the clone set and this plan names the issue a couple of dozen times. Seven of the eight are covered by 4g's item (1) and by the comment `2e0d32a` already wrote at `smoke-cluster.yml:55-65`; this site is the eighth, and 4f is the only step that reaches it. Leave it pointing at the issue by hop rather than by name and the bullet is unsatisfiable whatever else lands, which is the shape of miss the whole deferral depends on not happening. The blocked half is the under-cloud; the upload below is the guest artifact, which #4280's own evidence holds constant. Every consumer of this repository pins `@main`, so what does land here lands for the whole fleet the moment it merges; say so in the pull request. **After this merges, trigger `kerbside`'s `sf-e2e-functional` on its default branch and read the run to completion.** It takes the guest-image default, and no other step in this phase touches that repository, so it is the one consumer whose breakage this landing could cause and nothing else in the phase would surface; a failure is a revert of this commit. That run is a definition-of-done bullet and this sentence is what causes it -- the deferred paragraph below re-triggers the same workflow when the defaults move, which is a second run and not this one. **What the deferred default move will need, recorded here so it is not re-derived.** When #4280 closes, moving those two defaults is not additive and two consumers take them: `shakenfist` `functional-tests.yml:558`, the node-lifecycle job, which passes no `base_image` and is not in 4g's edit list, and `kerbside` `sf-e2e-functional.yml:104`, in a repository no step in this phase touches. Both must be re-triggered on their default branches after that merge and read to completion, because waiting for whenever a pull request happens to run them is not the same thing. The gate before editing is a published blob: read the conductor's `sf-client label update "ci-images/debian-13"` line rather than the `IMAGE_BUILDS` entry, because an entry is not a blob and phase 3 lost a night to exactly that distinction. Keep the upload a single commit so its revert is one commit. The transitional double upload this step creates is bounded by 4h, and 4j check (5) is what proves 4h happened. It is not free while it lasts: every smoke cluster build uploads the cached image twice instead of once, against the same primary, so the window costs one extra image copy per cluster rather than one extra name. That is an argument for 4g and 4h following 4f promptly, not for skipping the transition -- the alternative is a flag day across two repositories that pin `@main`. The line numbers in this brief were re-read on 2026-09-21, and no consistency issue lists these sites -- they are the half the criterion cannot see -- so locate them with `grep -rn 'ci-images/debian-12' .` and `grep -rn '/srv/ci/debian:12' .`, and treat the one upload as the expected result rather than the instruction, and the two defaults as sites to leave alone. Grep the source path rather than the command: the literal string `artifact upload debian-12` appears nowhere in the fleet, for the `${setup}` reason above, so a grep for it returns nothing and would license the conclusion that this step has nothing to do. Two commits in one pull request, split the way 4g splits its comment-only item out under its own subject: "Upload the cluster base image under a release-neutral name." for the upload, and "Name the blocking issue at the under-cloud default." for the comment edit. The upload stays one commit, so its revert stays one commit; the comment is the one edit in this step that touches a line adjacent to the blocked default, which is the change a later reader is most likely to go looking for by subject. The "Boot smoke clusters on Debian 13." commit this step used to carry is deferred with the defaults. |
| 4g | high | opus | worktree | **After 4f has merged, and item (1) is blocked by shakenfist#4280.** `shakenfist/shakenfist`, the guest-image half, in one pull request but not one commit. (1) **Deferred, not dropped.** The four `base_image: 'sf://label/ci-images/debian-12'` in `.github/workflows/functional-tests.yml` (`:443`, `:463`, `:477`, `:515`) and the two in `.github/workflows/scheduled-tests.yml` (`:42`, `:52`) are the under-cloud the hypervisor VMs boot, and shakenfist#4280 is the bug that stops them moving: on a trixie under-cloud every instance's agent reports no contact. They become `debian-13` when #4280 closes, `base_image_user` staying `debian`; they do not move in this phase. Leave a comment at each site naming the issue, in the shape `actions` used at `.github/workflows/smoke-cluster.yml:55-65`, so that the next reader of a bookworm reference here finds the reason rather than an apparent audit miss. **The comment is for human readers and is not an `eol-distro` exception; do not write an `audit-ok` token here.** An earlier draft of this brief claimed it would serve as one, and neither half of that held. The criterion's exception is the literal token `audit-ok: eol-distro` (`EXCEPTION_RE`, `scripts/audit/checks/distros.py:197`), which this brief does not ask for; and `is_excepted()` reads only the finding's own line and the one above it, so a multi-line block in the shape `actions` used would sit out of range -- which is the instar defect this same amendment discovered, re-specified for six new sites. It is also moot: `docs/audits/eol-distro.md`'s "What this does not cover" puts guest images outside the criterion deliberately, so widening it is a specification change phase 6 would have to argue for, and phase 5 cannot start until these six have moved anyway, so a marker here would guard a window this plan's own ordering closes first. If phase 6 does widen the criterion, the marker shape for a guest-image site is that phase's decision to make. **Name `shakenfist/shakenfist#4280`, and do not quote the image string in the comment.** The definition of done counts sites with a `ci-images/debian-12` grep that counts lines, so a comment that repeats the name a second time turns a passing site into an apparent ninth one; naming only the issue keeps each site exactly one hit of that grep and exactly one hit of the risks section's set check, which is `grep -rn --exclude='*.md' --exclude-dir=.git 'shakenfist#4280' .` -- anchored on the `shakenfist#` prefix, because the bare `grep -rn 4280` also matches the epoch timestamp `1787428090` in three docstrings of `shakenfist/external_api/agentoperation.py`, which is a file in the repository you are editing. Run the anchored form; the bare one will look like it found four extra sites. The model comment obeys this already -- it names `ci-images/debian-13`, the move it is refusing, rather than the `-12` default it sits above. That comment is this item's whole content for now, and it is the one part of item (1) that lands. (2) The matrix lane names at `functional-tests.yml:441` and `:475` are "Debian 12 cluster" and "Debian 12 tier", and `tools/ci_headroom_harvest.py:134` and `:141` key `BUNDLE_TOPOLOGIES` off those strings *and* off the derived GitHub job names in the same entries; that file's own comment says the derivation would break silently if the names changed. Rename lanes and tool in the same commit, and update `tests/test_ci_headroom_harvest.py:61-62`. (3) Replace the 43 `sf://upload/system/debian-12` references with `sf://upload/system/debian` -- 12 files under `deploy/shakenfist_ci/` and `tests/`, plus `deploy/nodelifecycletests.sh:132` -- and route them through `CLUSTER_CI_IMAGE` (`deploy/shakenfist_ci/base.py:37`) wherever the file already imports from `base`, so the next release is one line. A literal that the constant does not reach is the phase 3 failure this step is repeating on purpose; the definition of done greps for the old name, so leaving any is not passing. (4) `functional-tests.yml:583` uploads the image itself for the node-lifecycle job, the same command as the action's upload line (`:263` on 2026-09-21, and a grep target rather than an address -- `e2a56bd` moved it by fourteen lines): move it to `debian` and `/srv/ci/debian:13` too. Commit subjects, one per numbered item that lands, beginning "Boot the cluster lanes on Debian 13." -- except that item (1) no longer boots anything on Debian 13, so its commit is "Say why the under-cloud is still bookworm.". **Every line number in this brief was read on 2026-09-19 in `shakenfist`, and no commit in this phase renumbers them -- but ordinary work in that repository does: `functional-tests.yml:477` and `:515` had become `:479` and `:517` by 2026-09-22, which is why the definition of done lists them at the later numbers. They are a grep target rather than an address, and that matters most in item (1), whose whole remaining content is adding a comment at each of six sites. The one `actions` number, in item (4), was re-read on 2026-09-21 after `e2a56bd` moved it. None of these sites appears in any consistency issue, because they are the half the criterion cannot see.** 4g runs after 4e has edited two of the same files and after however much ordinary work has landed in between, so locate the work with `grep -rn 'ci-images/debian-12' .github/workflows/`, `grep -rn 'sf://upload/system/debian-12' .` and `grep -rn 'Debian 12 cluster\|Debian 12 tier' .`, and read the counts here -- six `base_image` sites, 43 references, two lane names -- as the expected result of those greps. A count that disagrees is a finding to report, not a number to reconcile silently. **Report what it disagrees by, though, and do not stop on growth alone**: these counts were read on 2026-09-19 and the reference count had reached 48 in 13 files by 2026-09-22, because a test suite under active development keeps adding readers of a name this step is renaming. More references than expected means the survey aged and the edit is larger; *fewer*, or a `base_image` or lane-name count that moves at all, means something else edited these sites and is the finding worth halting for. High effort because item (3) is 43 sites in a test suite whose failures are slow to read, and because item (2) fails silently rather than loudly. Item (1)'s deferral does not reduce that: items (2) to (4) rename what the tests boot and what the tool keys off, and #4280 leaves both untouched. |
| 4h | medium | sonnet | worktree | **After 4g has merged, and gated on a grep rather than on this sentence.** In `shakenfist/actions`, remove the pre-existing `debian-12` upload that 4f deliberately left in place in `build-smoke-cluster/action.yml`, leaving only the `debian` one that 4f added. 4f adds a name and removes none; this step removes the old name. If the diff you are about to write deletes the line that says `debian`, you have the wrong one. Before editing, grep the fleet for `sf://upload/system/debian-12` across fresh clones of every non-archived repository in the organisation -- not just `shakenfist`, which is where 4g worked -- and **stop and report instead of editing if any live reference remains**. Scope the grep with `--exclude='*.md' --exclude-dir=.git`: this plan is in one of those clones and names the string a dozen times, and a gate that halts on its own plan file is a gate an agent learns to override. Prose is out of scope here for the same reason `eol-distro` exempts it -- a document describing a migration is not a dependency on it. **Exclude prose rather than allow-listing extensions.** An allow-list of `.py`, `.yml`, `.yaml` and `.sh` reads well and silently drops `.j2` -- of which `actions/ansible/` is full -- along with extensionless scripts and `Makefile`. This gate's failure is destructive by omission: it deletes the upload while a consumer it could not see still reads the name. Phase 3's retrospective is that a gate stated in a plan file is not a gate; this one is a command whose output decides the step. Commit subject: "Drop the transitional cluster image name." |
| 4i | low | haiku | none | Housekeeping, no commit in this repository. Close development#123 with the merge commits, noting that the fleet cleared 38 of its 80 references through ordinary repository work answering the daily audit before this phase began, and grew new ones in the same fortnight. That is a statement about the period before the phase, which no later merge can falsify; do not turn it into a claim about the final split, because the compliance page the next sentence sends you to shows the current count and not who cleared what, and by the time 4i runs this phase will have cleared most of the remainder itself. **Read the closing number off `docs/audits/compliance.md` when this step runs rather than from this plan**, per the survey's own rule that the inventory is the compliance page and never a number in this document: the survey counted 45 references in 13 repositories on 2026-09-19, the 2026-09-21 amendment made that 42 in 12 when `actions` cleared itself, and 4i runs after the eight remaining steps -- sixteen pull requests, by those steps' own briefs -- have merged. The net is the less interesting half: a fleet that grows references while clearing them is why decision 4.2 deletes the declaration with the last user. Comment on private-ci#38 with the guest-image inventory this survey found, since #38 is the collated reference and did not have it: the eight `sf://label/ci-images/debian-12` sites, the upload name, and the eight references in `private-ci`'s own `conductor/tests/test_imagebuilder.py` (seven naming the label, one the `-docker` variant) that follow `IMAGE_BUILDS` in phase 5. Leave #38 open; it closes at the end of phase 5. Do not close the per-repository consistency issues by hand -- the audit closes them itself when the repository goes compliant, and closing one by hand hides a repository that did not. `actions`' own issue will already have been closed that way, on 2026-09-20, before this step runs. |
| 4j | medium | sonnet | none | **Confirms the phase, which nothing else does.** Medium and sonnet rather than the mechanical pair its first draft had: five checks, three of which read a run log or a live grep across twenty-nine clones and decide whether the answer agrees with a diff. Observation step, no commit, run after every pull request above has merged and at least one morning's audit has run. (1) Read `docs/audits/compliance.md` on this repository's `main` and confirm the `eol-distro` table has no `non-compliant` row, and that its generation timestamp is after the last merge -- a stale page looks healthy, which the page's own header warns about. (2) Confirm the renamed artifact is both written and read, which takes two different runs because 4f and 4g land in different repositories. **Writing:** in a smoke cluster build after 4f, an upload line names the artifact `debian` with nothing after it. **Booting:** in a completed `shakenfist` functional-tests run after 4g, an instance boots `sf://upload/system/debian` with nothing after `debian`. **Anchor both ends, because the old names contain the new ones**: `debian-12` contains `debian` and `sf://upload/system/debian-12` contains `sf://upload/system/debian`, so an unanchored read passes on the pre-rename line. Between 4f and 4h the expected state of the build log is *two* upload lines, the old name and the new one, which is worth counting: finding one is itself a finding, and which one it is says whether 4f has not landed or 4h has landed early. Read both out of run logs rather than out of the workflow files, which only prove what was asked for. Keep the halves apart: 4f adds an upload in `actions` and renames nothing that `shakenfist` reads, so a run between 4f and 4g shows the write and cannot show the boot, and reporting the upload line as if it were the boot is a pass on the wrong assertion. The definition of done states the booting half only, and after 4g. This check used to read the under-cloud image and expect `debian-13`; shakenfist#4280 blocks that move, so the under-cloud in either log should still say `debian-12` and a run that says otherwise is a finding, not a pass. (3) Confirm `tools/ci_headroom_harvest.py` still matches its bundles after the lane rename, by running it against a merge run that completed after 4g. (4) `grep -E "^[[:space:]]*-[[:space:]]*['\"]?debian-12" <clone>/.github/actionlint.yaml`, the quote optional because `- "debian-12"` and `- 'debian-12'` are both valid YAML and these very files already quote their shellcheck entries that way, across fresh clones of **every** non-archived repository in the organisation, not the ones the criterion lists -- a stale declaration produces no finding in any criterion, so this is the only check in the phase that would catch one, and the repositories most likely to carry one are the ones that already migrated and so are not listed at all. The list-item anchor is load-bearing: this repository and `hunkydory` both name `debian-12` in a comment explaining why it is *not* declared, and a plain substring grep reports both. `private-ci` is the one expected hit and is step 4d's stated exception, for having no workflows at all. (5) `grep -rn --exclude='*.md' --exclude-dir=.git '/srv/ci/debian:12' .` over the same clones must return nothing, and `build-smoke-cluster/action.yml` must carry exactly one line naming `/srv/ci/debian:13`. The `--exclude` scoping is 4h's, for 4h's reason and in 4h's shape -- exclude prose, do not allow-list extensions: this plan file is in one of those clones and names the path several times, and a gate that halts on its own plan file is a gate an agent learns to override. It does not collide with decision 4.7, which was checked rather than assumed: `ci-dependencies.yml` spells its cache list as `name: "debian:12"` against an `images.shakenfist.com` URL, and checked against `actions` at the same commit as the rest of this survey, `build-smoke-cluster/action.yml:249` as the survey read it -- `:263` since `e2a56bd`, and found by grep rather than by line -- was the only line in that repository naming a `/srv/ci/debian` path at all, so the bookworm cache entry 4.7 keeps is not a hit. Grep the source path, not the command: the upload is assembled from a `${setup}` variable, so the literal string `artifact upload debian-12` appears nowhere in the fleet and a check looking for it passes whether or not 4h ran -- which is the shape of silent skip this step exists to catch, found by running the grep rather than reading it -- nothing else in the phase can tell whether 4h ran, because the definition of done's other greps match the reader spelling in `shakenfist` rather than the writer line in `actions`, and a skipped 4h leaves every smoke cluster in the fleet uploading a bookworm image forever with no finding anywhere. The `exactly one` half catches the inverse mistake 4h's brief warns about. Report all five; if (2) to (5) disagrees with the diff, say so rather than filing it, because the phase is not over until they agree. |

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
`build-smoke-cluster@main` is what every consumer pins, so 4f is
live everywhere the moment it merges, including for `kerbside`,
which takes the default. Amended 2026-09-21: the sharp half of this
risk is deferred with the default move, because shakenfist#4280
blocks it. What 4f still lands is additive -- the old name and its
bookworm source stay untouched until 4h -- so no consumer changes
underneath, and the residual risk is the cost of the transitional
window rather than a behaviour change. Mitigated by 4h closing the
window, by 4j check (5) proving it closed, and by 4f triggering
`kerbside`'s `sf-e2e-functional` on its default branch after merge
-- the only run in the phase that exercises a consumer taking the
default, in the only repository no step here edits.

**The blocker is load bearing and nothing in this plan watches it.**
shakenfist#4280 now gates 4f's defaults, 4g's item (1) and phase
5's Debian 12 half -- not phase 5 entire, whose `debian-11` bullet
has been startable since phase 1 -- and it is an open bug in another
repository with no owner named here. The failure mode is not that
it stays open -- it is that it closes and nobody notices, so the
eight deferred sites
sit on a retired image with a comment explaining a reason that has
expired. Mitigated by the definition of done requiring a comment at
every one of the eight, which makes them greppable as a set -- **as
`grep -rn --exclude='*.md' --exclude-dir=.git 'shakenfist#4280' .`,
anchored and scoped off prose, and not as `grep -rn 4280`**. Both
halves are load bearing. The `--exclude` is 4h's and the definition
of done's, for their reason: `development` is in the clone set and
this plan names the issue a couple of dozen times, so the unscoped
form returns its own plan file before it returns any of the eight,
and a gate that halts on its own plan file is a gate an agent
learns to override. The anchor is for a different collision: run
bare over `shakenfist` on 2026-09-22, the unanchored form returns
one real hit and three lines of
`shakenfist/external_api/agentoperation.py`, where the epoch
timestamp `1787428090` in three docstrings contains the digits, in
the file this bug is about. It is the same substring trap 4j check
(4) anchors around for `debian-12` and the same one the
`ci-images/debian-12` bullet notes for `hunkydory`. Mitigated
further by phase 5's first step reading the issue before it reads
this plan. That is a weaker mitigation than a check that fails, and
phase 7 should ask whether the plan index needs a blocked-on column
rather than a sentence. The analysis to inherit rather than redo is
that a term already exists and does not fit: `plan-status-vocabulary`
defines `Blocked` as "cannot proceed until something outside the plan
changes", and `scripts/audit/checks/plans.py` accepts it, but phase 4
is half blocked and most of it moves, so `In progress` is the right
cell, and phase 5's `debian-11` half is genuinely startable, so
`Not started` is right there too. What is missing is nowhere to say
*what* the block is, because the vocabulary's rule is that the term
is the whole cell. A column is what that argues for.

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
should refuse to start until 4j has reported every check it
runs agreeing. Amended 2026-09-21: 4j agreeing is no longer
sufficient. Phase 5 removes `debian-12` from `IMAGE_BUILDS` and
`CI_IMAGES`, and eight under-cloud sites will still name
`ci-images/debian-12` when this phase closes, so phase 5's Debian
12 follow-up cannot start until shakenfist#4280 closes *and* those
eight have moved -- which is a piece of work this phase no longer
contains. Where it goes is the back brief's fifth question. The
`debian-11` bullet is unaffected and startable now; this gate is on
one bullet of phase 5, not on the phase.

#### Definition of done

- [ ] `docs/audits/compliance.md`'s `eol-distro` table has no
      `non-compliant` row, on a page generated after the last merge.
- [ ] `grep -E "^[[:space:]]*-[[:space:]]*['\"]?debian-12"` over the
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
- [ ] `grep -rn --exclude='*.md' --exclude-dir=.git
      "ci-images/debian-12"` over fresh clones of every non-archived
      repository returns nothing outside
      `private-ci/conductor/tests/test_imagebuilder.py`, whose eight
      references -- seven naming the label and one the `-docker`
      variant -- phase 5 moves with the `IMAGE_BUILDS` entries,
      **and the eight under-cloud sites shakenfist#4280 blocks**:
      `actions` `build-smoke-cluster/action.yml:32` and
      `.github/workflows/smoke-cluster.yml:67`, and `shakenfist`
      `functional-tests.yml:443`, `:463`, `:479`, `:517` and
      `scheduled-tests.yml:42`, `:52`. Two of those `shakenfist`
      numbers were `:477` and `:515` when the survey read them on
      2026-09-19 and had moved two lines by 2026-09-22, which is
      what the file and the count are for: this bullet asserts two
      sites in `actions` and six in `shakenfist`, found by the grep
      it names, and the line numbers locate them on the day rather
      than addressing them. Each must carry a comment naming the
      issue, which is what makes this bullet falsifiable rather than
      an exemption list: a ninth site, or one of these eight without
      the comment, fails it. **The comment names shakenfist#4280 and
      not the image string**, so that each site stays exactly one
      hit of this grep -- the bullet counts sites and the grep
      counts lines, and a comment that quotes the name again reports
      a compliant site as the ninth one that fails. Naming only the
      issue also makes the risks section's `grep -rn
      'shakenfist#4280'` a one-to-one set check -- the anchor is
      load-bearing there for the reason that section gives -- and
      under the same `--exclude` scoping as this bullet it returns
      exactly these eight lines, and a hit elsewhere is a finding to
      report rather than a failure. 4f's landing is what brings
      `build-smoke-cluster/action.yml:28-30` up to this: `2e0d32a`
      left it pointing at `smoke-cluster.yml` for the evidence
      instead of naming the issue, so it is the one site of the
      eight that no other step reaches. Read that as coverage and
      not as a running count: 4g writes six of the eight and lands
      after 4f, so the set grep returns one before 4f, two after
      it and eight after 4g, and never seven. The substring
      rather than the `sf://label/` form so that both spellings are
      caught; that file happens to use the prefixed one. The
      `--exclude` is what keeps the grep off prose -- this plan
      names the string a dozen times, and `development` is in the
      clone set -- and it is the same exemption `eol-distro` makes
      for a document describing a migration. It excludes prose
      rather than allow-listing extensions, so a reference in a
      `.j2` template or an extensionless script is still read.
      `private-ci` is in the clone set deliberately: it is excluded
      from the criterion, so it is exactly the repository a
      criterion-shaped check would miss.
- [ ] The same grep for `sf://upload/system/debian-12` returns
      nothing at all.
- [ ] The same grep for `/srv/ci/debian:12` returns nothing, and
      `actions/build-smoke-cluster/action.yml` carries exactly one
      line naming `/srv/ci/debian:13`. This is the only check that
      can tell whether 4h ran: the two bullets above match the
      reader spelling in `shakenfist`, not the writer line in
      `actions`. It greps the source path rather than `artifact
      upload debian-12`, because the command is assembled from a
      `${setup}` variable and that literal exists nowhere -- a
      bullet asking for it would pass vacuously. Decision 4.7's
      bookworm cache entry is not a hit: `ci-dependencies.yml`
      spells its cache list as `name: "debian:12"` against an
      `images.shakenfist.com` URL, not as a `/srv/ci/` path.
- [ ] `kerbside`'s `sf-e2e-functional` workflow has completed
      successfully on its default branch after 4f merged, triggered
      by 4f, which is the step that owns this bullet. It is the
      one consumer that no other step re-runs. It took the
      guest-image default this step was going to move, which
      shakenfist#4280 has deferred, so what this now proves is the
      narrower thing: that the release-neutral upload did not break
      a consumer nobody else exercises.
- [ ] `instar`'s `functional-tests.yml` carries the `audit-ok:
      eol-distro` marker **within one line of the finding, and with
      a reason after it**, and instar#564 is closed by the audit
      rather than by hand. The two requirements are independent and
      this phase only had to add the first: a marker with a reason
      has been in that file since before this phase was planned,
      nine lines away, so proximity is what was missing and the
      reason is what `docs/audits/eol-distro.md:169-176` has always
      asked for. Dropping either leaves the fleet's canonical
      instance of this marker wrong in one of the two ways.
- [ ] A completed `shakenfist` functional-tests run after 4g shows
      instances booting `sf://upload/system/debian`, read from the
      run's log. The under-cloud in that same log still reads
      `ci-images/debian-12` and that is correct until
      shakenfist#4280 closes; this bullet asserted the opposite
      before that bug was found.
- [ ] `tools/ci_headroom_harvest.py` matches its bundles on a merge
      run completed after 4g.
- [ ] development#123 is closed; private-ci#38 is still open and
      carries the guest-image inventory.

#### Back brief

Five things to agree before the remaining steps run, because each
is cheap to propose and expensive to redo. The first three were
written when the phase was planned; the last two come from the
2026-09-21 amendment, and they are the ones that decide what this
phase now is:

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
   exist yet.
5. **Where the eight deferred under-cloud sites go.** They are not
   in this phase any more and they are not in phase 5, which is
   producers. Three options: reopen this phase when #4280 closes,
   add a phase 4b, or fold them into phase 5's first step as its
   entry gate. The third is tempting and I think wrong -- it makes
   a producer phase start with a consumer edit, which is the exact
   blurring D4 exists to prevent -- but it is the cheapest, and the
   choice belongs to whoever is holding the plan when #4280 closes
   rather than to this amendment.

### 5. Retire the end-of-life producers

Closes: private-ci#40, 33fl#826. Depends on: phase 4, and on
shakenfist#4280 plus the eight deferred under-cloud sites, for the
Debian 12 half only.

* **private-ci#40, `debian-11`**: remove from `IMAGE_BUILDS` and
  `CI_IMAGES`. No workflow requests it, so this can go as soon as
  phase 1 lands -- it also removes the permanent `False` in every
  nightly cycle summary.
* **private-ci#40 follow-up, `debian-12` and `debian-12-docker`**:
  only after phase 4, per D4 -- and, since 2026-09-21, only after
  shakenfist#4280 as well. Phase 4 now closes with eight
  under-cloud sites still naming `ci-images/debian-12`, because a
  trixie under-cloud leaves every instance's agent with no contact.
  Removing the producer while those eight still name it is the
  breakage D4 exists to prevent, so this bullet's gate is "phase 4
  complete *and* the eight moved", not "phase 4 complete". Phase
  4's back brief asks where that work is scheduled; whatever the
  answer, this bullet waits for it. The `debian-11` bullet above is
  unaffected and has been unblocked since phase 1 completed.
* **33fl#826**: delete the two GitLab static runner instances so
  `static_runner.yml` rebuilds them on `debian:13`, and confirm
  the six GitHub runners have rolled over. Consider a retire tool
  so this is not manual next time.

### 6. Close the audit's blind spot

Closes: nothing filed. Depends on: phases 3-5, so the producers
are compliant before they are measured.

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

**Phase 4's label half is long and boring, and that is the half
that moves.** Twelve repositories with an actionlint edit each, and
the daily audit files and closes the per-repository issues itself,
so progress is externally visible rather than tracked by hand -- the
fleet cleared 38 of the original 80 references that way before the
phase started, grew three new ones while doing it, and cleared a
thirteenth repository, `shakenfist/actions`, twenty-five minutes
after this plan merged. The risk moved next door twice. Phase 4's
survey found a second class of consumer the criterion cannot see,
and being boring is what made it easy to believe the criterion's
count was the whole job; then shakenfist#4280 stopped that second
class moving at all, so the phase will close with eight under-cloud
sites still naming the retired image and phase 5 waiting on a bug
rather than on a phase. The label half is still going well. It is
no longer the whole phase.

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
