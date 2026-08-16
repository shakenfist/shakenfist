# Phase 5: rewrite `docs/installation.md`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at medium effort but assigned to **opus**: the page
must describe the whole system coherently, and it is the
deliverable the operator actually asked for.

Closes issue #3, "Add Installation Guide", open since
2024-04-23.

## Situation

The current page is 66 lines that stop at acquisition. What
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
Keystone-only (`kerbside/api.py:157`), and it refuses to
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

### Other files

- `docs/index.md:178` — the Installation bullet's description
  ("Packages, OS-level dependencies, and deployment
  pointers") no longer describes the page. Update it, and
  consider whether the Installation entry should move above
  Configuration in the Operator Documentation list, since it
  is now the entry point.
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
  `kerbside db` and `kerbside demo` command groups. `ARCHITECTURE.md:412`'s tree
  listing is already being touched by phase 1 step 1b; check
  it is consistent rather than editing it twice.
- `.claude/CLAUDE.md`'s Key Files table — add `demo/` if the
  table is the right granularity for it. Judgement call; a
  demo directory is arguably not a key file.
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
- `etc/kerbside.conf.example` — **candidate rename** to
  `etc/kerbside.ini.example`. Raised in the phase 2 review:
  the example's extension differs from the path it must be
  installed at (`/etc/kerbside/kerbside.ini`), which is a
  trap for anyone copying by pattern rather than reading the
  header, and is plausibly where AGENTS.md's since-corrected
  `/etc/kerbside/kerbside.conf` came from. Deliberately not
  done in phase 2, because `docs/configuration.md:5` and
  `ARCHITECTURE.md:345` both name `kerbside.conf.example` and
  phase 2 decision 6 forbade editing either file. This phase
  revisits both, so it is the cheapest place to do it — but
  it is optional, and the file's header already carries the
  weight in its first sentence. If renaming, `git grep -l
  'kerbside\.conf\.example'` finds every reference,
  including the tests, which locate the file by name.

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

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | Rewrite `docs/installation.md` per "Structure". Read, first: the current page, `docs/configuration.md`, `docs/use-cases/ovirt.md` (for the house style and especially its "Status and limitations" table, which `PLAN-use-case-docs.md` singles out as worth imitating), `demo/README.md`, and `tools/direct-qemu/start-kerbside.sh`. Then **run the demo yourself from a clean `docker compose down -v`** and write the walkthrough from what you actually saw, quoting real output. Do not write a command you have not run. Report the transcript in your result. |
| 5b | medium | sonnet | none | Update the surrounding files listed under "Other files": `docs/index.md` (the Installation bullet's description, the ordering question, and the static-source row), `docs/plans/PLAN-use-case-docs.md` (the ownership note), `AGENTS.md` and `ARCHITECTURE.md` (the `demo/` directory and the `kerbside db` group; verify phase 1 already fixed the `alembic/` tree line rather than fixing it again), and `.claude/CLAUDE.md`'s Key Files table if warranted. `README.md`: at most one sentence, and only if the install story genuinely changed — read the `readme-discipline` policy in `.claude/CLAUDE.md` before touching it, and prefer changing nothing. |
| 5c | medium | sonnet | none | Fresh-reader review. On a machine with no prior context and only docker installed, follow `docs/installation.md` literally, top to bottom, doing exactly what it says and nothing it does not say. Record every point where you had to guess, look something up, or run a command the page did not give you. Report those gaps; do not fix them — the gaps are the finding, and the management session decides which are real. |
| 5d | low | haiku | none | Close issue #3 with a comment linking `docs/installation.md` and naming what it now covers: the two-process model, the database and `kerbside db upgrade`, TLS material, the minimum configuration set, the compose demo, and the handoff to `docs/use-cases/`. Leave #300 (Keystone-only login) and #301 (the session JWT scheme) open, and add a comment to each noting that `docs/installation.md` now documents the token workaround and will need editing when they are fixed. |

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
* Issue #3 is closed.
