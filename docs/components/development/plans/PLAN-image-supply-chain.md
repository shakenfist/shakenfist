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
| [#123](https://github.com/shakenfist/development/issues/123) | development | 80 Debian 12 references in 15 repositories |
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
| 3. Unblock the migration | In progress | private-ci 392700d (#60) |
| 4. The consumer sweep | Not started | |
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

Closes: development#123, private-ci#38. Depends on: phase 3 for
anything naming `debian-gnome-12`.

private-ci#38 is the collated inventory of where the fleet uses
obsolete base images. It is a reference rather than a task, and it
closes when the thing it inventories is gone: this phase clears
the consumer half and phase 5 clears the producer half, so #38
closes at the end of phase 5 rather than when its own checklist is
ticked.

80 references in 15 repositories, repository by repository rather
than as one sweep, so each change carries its own actionlint edit
and is reviewed against the workflows it touches.

Two things make this less mechanical than the count suggests:

* **Each move is two files.** A runner label change also needs the
  replacement declared in that repository's
  `.github/actionlint.yaml` under `self-hosted-runner: labels:`,
  in the same commit. actionlint fails a workflow naming an
  undeclared label, so missing this turns a one-line fix into a
  failing lint.
* **Two findings are not label swaps.** instar boots `debian:12`
  in a functional-test matrix deliberately covering several
  distributions, which is the `audit-ok: eol-distro` case rather
  than a migration. kerbside has two bookworm-tagged rust images
  needing a tag bump, which is a different decision.

The daily audit already files a `consistency` issue per
repository, so this phase tracks those rather than duplicating
them.

### 5. Retire the end-of-life producers

Closes: private-ci#40, 33fl#826. Depends on: phase 4, for the
Debian 12 half only.

* **private-ci#40, `debian-11`**: remove from `IMAGE_BUILDS` and
  `CI_IMAGES`. No workflow requests it, so this can go as soon as
  phase 1 lands -- it also removes the permanent `False` in every
  nightly cycle summary.
* **private-ci#40 follow-up, `debian-12` and `debian-12-docker`**:
  only after phase 4, per D4.
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

**Phase 4 is long and boring.** 15 repositories with an actionlint
edit each. The mitigation is that the daily audit files and closes
the per-repository issues itself, so progress is externally visible
rather than tracked by hand.

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
