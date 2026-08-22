# Phase 5: rewrite `docs/installation.md`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at medium effort but assigned to **opus**: the page
must describe the whole system coherently, and it is the
deliverable the operator actually asked for.

Closes issue #3, "Add Installation Guide", open since
2024-04-23.

## Situation

The current page is 75 lines that stop at acquisition. What
it omits is enumerated in the master plan's Situation
section; the short version is that it never mentions the two
processes, the database, the TLS material, the minimum
configuration set, or a console source, so a reader who
follows it exactly ends up with software and no running
system.

By the time this phase runs, phases 1-4 have made the
following true, and the page can rely on all of it:

- `kerbside db upgrade` creates the schema from a wheel
  install, and `kerbside demo token` mints a demo bearer
  token, refusing outside a purely static deployment
  (phase 1).
- `etc/kerbside.conf.example` exists and covers every
  setting (phase 2).
- `demo/` brings up a working stack with `docker compose up`
  (phase 3).
- A CI lane keeps that true, and the lane's path filter
  includes this page, so the two cannot drift (phase 4).

## Mission

A reader can go from nothing to a proxied SPICE console by
following the page in order, and knows where to go next for
their actual cloud.

## Scope

In scope: `docs/installation.md` itself, the surrounding
files listed under "Other files", the inbound links to
`demo/` that phase 3 left missing, closing issue #3, and
phase 4's one deferred checkbox (see "The path filter
demonstration").

Out of scope: platform setup for Shaken Fist, OpenStack or
oVirt beyond one-line pointers — `docs/use-cases/` owns
that; any change to `demo/` itself, which phase 3 built and
phase 4 pinned; and fixing the limitations the page will
document, since #300, #301 and #313 stay open and stay
described rather than repaired.

## What the survey found

Re-surveyed 2026-08-22 before implementation, because this
plan was drafted on 2026-08-16 — before phase 4 finished on
2026-08-18, and while `docs/installation.md` was being
edited by a different plan.

The load-bearing premises hold. `kerbside db upgrade` and
`downgrade` are registered at `kerbside/main.py:346` and
`:362`, `kerbside demo token` at `:530` with the
static-source refusal at `:428`; `etc/kerbside.conf.example`
is 10,888 bytes; `demo/` carries the compose file,
Dockerfile, entrypoint and README. Issues #3, #300 and #301
are all still open. The phase 3 review's complaint that
nothing links inward to `demo/` is still exactly true —
`git grep 'demo/' -- README.md docs/index.md
docs/installation.md ARCHITECTURE.md AGENTS.md` returns
nothing.

What had drifted is corrected in place above, rather than
left for the implementer to trip over:

* **The page is 75 lines, not 66, and the extra 16 came
  from another plan.** Commit `d315bff` (2026-08-15, the
  proxy dev releases plan's phase 4a) added the
  deployer-facing note on the dev-inclusive proxy floor and
  the startup contract-hash check, linking to
  `proxy-architecture.md`. That content is current and must
  survive the rewrite: "Installing with pip (keep, largely
  as-is)" now means keeping this too. See decision 1.
* **`kerbside/api.py:157` no longer shows Keystone-only
  login.** Line 157 renders `login.html`. The marker for the
  claim is now `kerbside/api.py:176`, the `TODO(mikal):
  Handle non-keystone auth as well` above the Keystone
  client setup.
* **`ARCHITECTURE.md:345` does not name
  `etc/kerbside.conf.example`.** Line 345 is in the
  Prometheus metric list; the reference is at
  `ARCHITECTURE.md:313`.
* **`ARCHITECTURE.md:412` is not the migrations tree line.**
  The `migrations/` entry is at `ARCHITECTURE.md:361`; 412
  is inside the `tools/sf-e2e/` listing. Phase 1 did update
  that entry, so the instruction to check it rather than
  edit it twice stands, at the corrected number.
* **`docs/configuration.md`** points at the example file
  from line 7, not line 5.
* **The `docs/index.md` ordering question is already
  settled.** Installation is the first entry in the Operator
  Documentation list, above Configuration, so only the
  description text needs work.

One thing also arrived after this plan was drafted: phase 4
completed on 2026-08-18 leaving a checkbox explicitly
addressed to this phase, which the draft does not mention at
all. It is now "The path filter demonstration" below, step
5e, and a success criterion.

## Approach

### Structure

```
# Installation

## Installing with pip          (keep, largely as-is)
## Checking OS package dependencies   (keep, trim)
## What a running Kerbside needs      (NEW)
## Try it: the demo stack             (NEW)
## Deploying for real                 (rewrite of "Deployment")
```

**"What a running Kerbside needs"** is the section whose
absence caused this plan. It is prose plus a table, not a
tutorial: the two processes and what each does, the database,
the TLS material and the `PROXY_HOST_SUBJECT` matching
requirement, the minimum configuration keys with a pointer to
`etc/kerbside.conf.example` and `configuration.md`, and a
console source with a pointer to `console-sources.md`. Keep
it to roughly a screen. Its job is to give the reader an
accurate mental model of the moving parts before any
commands.

Be explicit that the API and the proxy supervisor are
separate processes that must be co-located, because they
share `API_SOCKET_PATH` — `docs/configuration.md`'s
`API_SOCKET_PATH` row already says the proxy must be
co-located with the daemon, and this is the first place a
reader would learn why that matters.

**"Try it: the demo stack"** is the walkthrough: three or
four commands, what the reader should see after each, and the
BIOS-screen expectation stated up front so nobody concludes
it failed.

Where the token comes from needs a sentence of its own, not
a footnote. `kerbside demo token` exists because login is
Keystone-only (`kerbside/api.py:176`), and it refuses to
mint unless every configured source is `static` — so a
reader must not expect it to work in their real deployment,
and should understand that the refusal is deliberate rather
than a bug they can configure around. Say that where they
first run it, and cite #300.

Then the limits, plainly: no real authentication
(Keystone-only login, citing issues #300 and #301), a
self-signed CA,
loopback-only ports, two processes in one container, a static
source rather than a cloud. Point at `demo/README.md` for the
reference detail and do not duplicate it — the page is the
narrative, `demo/README.md` is the reference.

**"Deploying for real"** keeps the existing kerbside-patches
and Kolla-Ansible pointer and adds the use-case table's
links, so the page hands off rather than competing with
`docs/use-cases/`. This is the boundary the master plan's
decision 2 settles: this page owns the ten-minute
recipe, the use-case pages own their platforms.

### Non-negotiable verification

**Every command in the page must be executed, in order, on a
clean machine, by the agent writing it.** This page exists
because commands were documented and not run. A phase 5
result whose author has not run the commands is not
acceptable regardless of how good the prose is. Report the
transcript.

### The path filter demonstration

Phase 4 shipped `demo-compose.yml` with a path filter and
proved it fires. It left one checkbox, explicitly addressed
to this phase: that the lane does *not* run on a pull
request touching `docs/` outside `installation.md`. Phase 4
had no reason to raise a docs-only pull request purely to
watch nothing happen; this phase gets both directions for
free, but only if the negative one is taken at the right
moment.

`docs/installation.md` is in the filter
(`.github/workflows/demo-compose.yml:51`), so the positive
direction is automatic on this phase's pull request. The
negative direction is available exactly once, at plan
review: the planning commit touches only `docs/plans/`,
which matches nothing in the filter, so a pull request
opened on the plan alone — before any implementation commit
lands — must show no `demo-compose` run. **Record it there.**
The opportunity closes the moment `docs/installation.md`
joins the diff, and reopening it costs a throwaway pull
request.

Partial corroboration is already in the record and is worth
citing either way: PR #344 (sfui conversion phase 9, merged
2026-08-20, after the lane landed) touched `docs/`,
`docs/plans/` and `ARCHITECTURE.md` and no filtered path,
and the branch `sfui-conversion-phase-09` appears nowhere in
`gh run list --workflow=demo-compose.yml`. That is weaker
than the clean observation, because the pull request was not
docs-only, but it points the same way.

### Other files

- `docs/index.md:178` — the Installation bullet's description
  ("Packages, OS-level dependencies, and deployment
  pointers") no longer describes the page. Update it. The
  draft also asked whether the entry should move above
  Configuration; the survey found it is already first in the
  Operator Documentation list, so there is nothing to move.
- `docs/index.md`'s use-case table — the "Standalone / static
  source" row can now point at the demo section as its
  worked example, even though the use-case page itself is
  unwritten.
- `README.md` — the install section at lines 24-33 is a
  pitch and stays a pitch. It gains **at most** a sentence
  that a compose demo exists, and only because the install
  story genuinely changed, which is the one condition
  `readme-discipline` allows. Do not add feature bullets. If
  in doubt, change nothing.
- `docs/plans/PLAN-use-case-docs.md` — add a note under the
  "Standalone / static source" row recording that the
  installation page owns the demo mechanics and that page
  owns the framing, per master plan decision 2. The
  author of that page should inherit the decision rather
  than rediscover the overlap.
- `AGENTS.md` and `ARCHITECTURE.md` — reflect the new `demo/`
  directory, the migration relocation from phase 1, and the
  `kerbside db` and `kerbside demo` command groups. `ARCHITECTURE.md:361`'s tree
  listing was updated by phase 1; check it is consistent
  rather than editing it twice.
- `.claude/CLAUDE.md`'s Key Files table — **leave it alone**
  (decision 3). `AGENTS.md` and `ARCHITECTURE.md` carry
  `demo/`, and that file is loaded into every session, so a
  demo directory does not earn a row there.
- **Inbound links to `demo/`.** Raised in the phase 3
  review: `demo/README.md` links outward to
  `docs/installation.md`, but nothing links inward. Neither
  the top-level `README.md`, nor `docs/index.md`, nor
  `docs/installation.md` mentions `demo/` at all, so a
  reader arriving at the repository has no path to the thing
  phase 3 built. This phase owns the fix, and it is
  explicitly in scope rather than implied by "rewrite
  installation.md": `docs/index.md` needs an entry, and
  `README.md` may take the one sentence `readme-discipline`
  allows, since the install story genuinely changed.
- `etc/kerbside.conf.example` — the phase 2 review's
  candidate rename to `etc/kerbside.ini.example`. **Declined;
  see decision 2.** Do not rename it, and do not reopen the
  question inside this phase.

### What not to do

Do not document Shaken Fist, OpenStack, or oVirt setup on
this page. Three of the seven use-case pages are unwritten,
and material placed here will rot against them when they are
written. A one-line pointer per platform is the correct
amount.

Do not turn the "What a running Kerbside needs" section into
a second copy of `configuration.md`. Name the keys that are
required in practice and link for the rest. The test in phase
2 guarantees `etc/kerbside.conf.example` is complete, so this
page never has to be.

## What implementation and review found

Merged as [PR #351](https://github.com/shakenfist/kerbside/pull/351)
on 2026-08-22.

Running every command from a clean clone, as the plan
required, found four things the prose had wrong. Two were
mine, from writing before running:

* `docker compose ps` does not print the columns the draft
  claimed. The page now uses
  `--format 'table {{.Service}}\t{{.Status}}'` and quotes
  its real output.
* `ss ... | wc -l` counts its own header, so the socket
  split read 13 and 1 rather than 12 and 0 — which would
  have told a reader the plaintext port was in use. Fixed
  with `-H`, and the page says why that flag matters.
* `pip install` does not give you `demo/`, so `cd demo`
  fails for exactly the reader the page had just created.
  The walkthrough starts with `git clone`.
* `remote-viewer` was never installed anywhere. The page now
  names `virt-viewer` with apt and dnf commands.

Review then found a fifth, and it was the load-bearing one:
the page put `pip install` before the OS packages that
install depends on. Confirming it needed a container rather
than an argument — on a clean `debian:trixie`,
`pip install kerbside` fails with `Can not find valid
pkg-config name`, because `mysqlclient` compiles a C
extension and publishes no wheel. The sections were
reordered and the Debian, Ubuntu and RHEL package lists are
now inline, because at that point in the page the reader has
neither a checkout nor `tox`, and so cannot run
`tox -e bindep`.

**That verification found a defect in `bindep.txt` itself.**
With pkg-config satisfied the build still failed, in gcc:
`python3-dev` and `python3-devel` were absent from the file.
So `tox -e bindep` would have reported the dependency list
complete while an install driven from it could not succeed.
Confirmed on both `debian:trixie` and `rockylinux:10`, and
fixed here rather than deferred, because the reordering
above depends on `bindep.txt` being honest. This is the
second time this plan has found a check that could not see
the thing it was supposed to check, after phase 4's
startup-log tautology.

Step 5e's negative observation was taken in its window: the
`demo-compose` lane did not run while the pull request held
only the planning commit, and ran within seconds of
`docs/installation.md` joining the diff. The first such run
was then cancelled by the workflow's own concurrency group
when the next commit arrived, which is why the phase 4
checkbox records two run URLs and states that what is
demonstrated is that the filter fires, not what the lane
concluded.

Also added in review: a test in
`kerbside/tests/unit/test_demo_stack.py` asserting the page
still quotes the demo it documents, demonstrated to fail
before being trusted; and a correction to a stale
description in `README.md` that predated this phase.

## Decisions

1. **The dev-floor and contract-hash note stays on the
   page.** `d315bff` added it after this plan was drafted, so
   the draft's "keep, largely as-is" did not have it in view.
   It is deployer-facing — it explains what `pip install`
   actually resolves — and `proxy-architecture.md` holds the
   mechanism it links to, so the split is already right. Keep
   the prose, keep the link, do not relocate it and do not
   expand it.
2. **Do not rename `etc/kerbside.conf.example`.** The phase 2
   review was right that the extension mismatches
   `/etc/kerbside/kerbside.ini`, and the draft called this
   the cheapest place to fix it. It is no longer cheap: the
   file carries a human review mark (`REVIEWS.md:51`, mikal,
   2026-08-18, sha `60dad322b15b`), and renaming the file
   drops that attestation for a cosmetic gain, on a file
   whose header already states the install path in its first
   sentence. `kerbside/tests/unit/test_conf_example.py:41`
   also locates it by name. This is the decision most likely
   to be argued with — the reviewer who raised it wanted it
   fixed, and it stays unfixed.
3. **`.claude/CLAUDE.md` gains nothing.** It is loaded into
   every session, so a row costs context on every task
   thereafter; `AGENTS.md` and `ARCHITECTURE.md` are the
   right homes for `demo/`.
4. **One pull request, with the plan pushed first.** The
   phase stays a single pull request per the repository's
   CI-cost policy, but the planning commit is pushed and the
   pull request opened before implementation starts, so
   decision-quality review happens on the plan and the
   negative path-filter observation is available. This is a
   sequencing constraint, not a second pull request.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | Rewrite `docs/installation.md` per "Structure". Read, first: the current page, `docs/configuration.md`, `docs/use-cases/ovirt.md` (for the house style and especially its "Status and limitations" table, which `PLAN-use-case-docs.md` singles out as worth imitating), `demo/README.md`, and `tools/direct-qemu/start-kerbside.sh`. Then **run the demo yourself from a clean `docker compose down -v`** and write the walkthrough from what you actually saw, quoting real output. Do not write a command you have not run. Report the transcript in your result. |
| 5b | medium | sonnet | none | Update the surrounding files listed under "Other files": `docs/index.md` (the Installation bullet's description, the ordering question, and the static-source row), `docs/plans/PLAN-use-case-docs.md` (the ownership note), `AGENTS.md` and `ARCHITECTURE.md` (the `demo/` directory and the `kerbside db` group; verify phase 1 already fixed the `alembic/` tree line rather than fixing it again), and `.claude/CLAUDE.md`'s Key Files table if warranted. `README.md`: at most one sentence, and only if the install story genuinely changed — read the `readme-discipline` policy in `.claude/CLAUDE.md` before touching it, and prefer changing nothing. |
| 5c | medium | sonnet | none | Fresh-reader review. On a machine with no prior context and only docker installed, follow `docs/installation.md` literally, top to bottom, doing exactly what it says and nothing it does not say. Record every point where you had to guess, look something up, or run a command the page did not give you. Report those gaps; do not fix them — the gaps are the finding, and the management session decides which are real. |
| 5d | low | haiku | none | Close issue #3 with a comment linking `docs/installation.md` and naming what it now covers: the two-process model, the database and `kerbside db upgrade`, TLS material, the minimum configuration set, the compose demo, and the handoff to `docs/use-cases/`. Leave #300 (Keystone-only login) and #301 (the session JWT scheme) open, and add a comment to each noting that `docs/installation.md` now documents the token workaround and will need editing when they are fixed. |
| 5e | low | sonnet | none | Close phase 4's deferred checkbox. Two observations, both from `gh run list --workflow=demo-compose.yml --json headBranch,event,conclusion`: the lane did **not** run for this branch while the pull request contained only the planning commit (recorded at plan review, per "The path filter demonstration" — if that window was missed, say so plainly rather than asserting it), and it **did** run once `docs/installation.md` joined the diff, with its conclusion. Tick the outstanding item in `PLAN-demo-install-phase-04-ci-lane.md` under "Still outstanding, and deliberately left for phase 5", quoting both observations and the run URLs, and change the master plan Execution table's phase 4 status from "Complete bar one item deferred to phase 5" to "Complete". Update the phase 4 sentence in `docs/plans/index.md` to match. |

## Success criteria

* Every command on the page has been executed in order on a
  clean machine by its author, with the transcript reported.
* The page covers: acquisition, OS dependencies, the two
  processes, the database, TLS, the minimum configuration,
  the demo, and where to go for a real deployment.
* Limitations are stated plainly, including that login is
  Keystone-only, and that `kerbside demo token` is a
  demonstration affordance that refuses outside a purely
  static deployment.
* No platform-specific setup on the page beyond one-line
  pointers.
* 5c's fresh-reader pass finds no gap that stops a reader
  reaching a console.
* `docs/index.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `PLAN-use-case-docs.md` are consistent with the result;
  `README.md` is unchanged or one sentence longer.
* The dev-floor and contract-hash paragraphs `d315bff`
  added still appear on the page, and still link to
  `proxy-architecture.md` (decision 1). `git log -p
  d315bff -- docs/installation.md` is the reference for what
  must survive.
* Phase 4's deferred checkbox is ticked with evidence: a
  named `demo-compose` run for the pull request once
  `docs/installation.md` was in the diff, and the recorded
  absence of one while it was not. Phase 4's status reads
  "Complete" in both the master plan and `docs/plans/index.md`.
* `etc/kerbside.conf.example` still has that name, and
  `REVIEWS.md:51` still refers to a file that exists.
* Issue #3 is closed.

## Risks and mitigations

* **The negative path-filter observation is missed.** It is
  available only while the pull request holds nothing but
  the planning commit, and the natural instinct is to push
  plan and implementation together. Mitigation: decision 4
  makes the plan-first push a constraint of the phase, and
  step 5e requires the implementer to say so plainly if the
  window was missed rather than assert an observation nobody
  made.
* **The rewrite silently drops the `d315bff` content**,
  because "keep, largely as-is" reads as permission to
  paraphrase and the drafter of this plan had not seen that
  text. Mitigation: decision 1 names it, a success criterion
  pins it, and the diff for that section is small enough to
  read directly at review.
* **The demo does not come up on the writer's machine**, so
  the walkthrough gets written from `demo/README.md` instead
  of from a run — the exact failure this whole plan exists
  to correct. Mitigation: step 5a must report a transcript,
  and the management session should refuse a result without
  one. `demo-compose.yml` passing on the pull request is
  corroboration, not a substitute: it proves the stack came
  up in CI, not that the page's commands are the ones that
  did it.
* **The fresh-reader pass (5c) turns into a fixing pass**,
  which destroys its value as evidence. Mitigation: the
  brief already forbids fixing; the management session
  decides which gaps are real, and any fix lands as a
  separate, named edit.

## Registration

Recorded in the master plan's Execution table and in
`docs/plans/index.md`. `docs/plans/order.yml` is for master
plans only; this repository has no `order.yml` at all, as
the phase 3 and phase 4 plans both recorded.

## Back brief

Before implementation starts, the implementing session
should confirm:

* It has read `demo/README.md`, `docs/use-cases/ovirt.md`
  and the current `docs/installation.md`, and can state what
  the `d315bff` paragraphs say and where they will live in
  the new structure.
* It can run the demo. `docker compose` v2 and a working
  docker daemon are prerequisites; if the machine cannot run
  it, stop and say so rather than writing the walkthrough
  from the README.
* It understands that decisions 2 and 3 are settled and are
  not to be relitigated inside the phase.

**Gate:** the structure in "Structure" — five sections, two
of them new — is agreed before any prose is written. It is
cheap to propose and expensive to redo, and it is the part a
reviewer is most likely to want moved around. Propose the
section list and the one-line purpose of each, and wait.
