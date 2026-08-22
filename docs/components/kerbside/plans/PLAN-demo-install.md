# A working installation path: the compose demo

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
Rust SPICE proxy in `rust/kerbside-proxy/` and the gRPC
control contract in `kerbside/rpc/`, the source driver
abstraction in `kerbside/sources/`, the REST API in
`kerbside/api.py`, the SQLAlchemy/alembic data model in
`kerbside/db.py` and `alembic/`, Pydantic-based config in
`kerbside/config.py`, audit logging, and the .vv file
generation path). Ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall proxy
architecture and `AGENTS.md` for build commands and
conventions. `tools/direct-qemu/start-kerbside.sh` is the
single most important reference for this plan: it is the
only place in the tree that spells out, end to end, what a
kerbside deployment actually needs in order to run.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

`docs/installation.md` documents how to acquire kerbside and
nothing about how to run it. It is 66 lines: `pip install
kerbside`, an explanation of the two-package split, a
`tox -e bindep` section, and three lines of deployment
pointers. A reader who follows it end to end has software on
disk and no path to a running system.

The gap was raised by the operator on 2026-08-14, framed as
"there is zero hope of that thing running and doing
something meaningful without a configuration and a mariadb
database setup". Investigation found the gap is wider than
configuration and a database. In full, the page omits:

- **That kerbside is two processes.** The REST API and web
  UI are served by `gunicorn kerbside.api:app`; the proxy
  supervisor is `kerbside daemon run`. Neither is mentioned
  in `docs/`, only inside `docs/plans/`. `kerbside daemon
  run` on its own yields a proxy with no API to mint tokens
  from, which is the state a diligent reader of the current
  page would reach.
- **The database.** A MySQL/MariaDB database and user, and
  `alembic upgrade head` to create the schema.
- **TLS material.** A CA certificate, a proxy certificate
  and key, and `PROXY_HOST_SUBJECT` set to a string that
  matches the proxy certificate's subject.
- **The minimum configuration set.** `SQL_URL`,
  `AUTH_SECRET_SEED`, `PUBLIC_FQDN`, `SOURCES_PATH`,
  `CACERT_PATH`, `PROXY_HOST_CERT_PATH`,
  `PROXY_HOST_CERT_KEY_PATH`.
- **A console source.** `sources.yaml`, for which
  `etc/example-static-sources.yaml` is a good starter.

Two supporting defects compound it:

- `docs/configuration.md:5` and `ARCHITECTURE.md:345` both
  refer the reader to `etc/kerbside.conf.example` for "a
  complete configuration example". **That file does not
  exist.** `etc/` contains only
  `example-static-sources.yaml` and
  `kolla-ci-globals-overlay.yml`. There are no systemd units
  either. Note that `etc/` does not ship in the wheel — the
  same file-finder rule that stranded the migrations applies
  — so phase 2 treats the example as documentation served by
  `docs/`, not as an artifact a wheel install can find. See
  decision 1 of that phase.
- **The migrations are not packaged.** Verified by building
  a wheel from `98bef5c` and listing it: `kerbside/api/`
  (68 files of templates and static assets) and
  `kerbside/sources/` both ship, because setuptools_scm's
  git file-finder includes every tracked file *under a
  package directory* as package data. `alembic/` sits at the
  repository root, outside `kerbside/`, so it ships in no
  artifact. The only CLI command is `kerbside daemon run`
  (`kerbside/main.py:259`); there is no `kerbside db
  upgrade`. Therefore **a pip-only install cannot create its
  own schema** — it must also clone the repository to get
  `alembic.ini` and `alembic/versions/`. Any installation
  document written today would have to instruct the reader
  to do that, which contradicts the packaging story the same
  page tells.

This closes long-standing issue #3, "Add Installation
Guide", open since 2024-04-23.

### Why a demo rather than a deployment guide

Per-deployment operator guides are already a tracked
work-stream: `PLAN-use-case-docs.md` owns seven pages under
`docs/use-cases/`, of which oVirt has landed. Duplicating
Shaken Fist, OpenStack, and oVirt setup on the installation
page would rot against those pages.

The operator's framing is the right split: installation.md
carries **the simplest possible demo that actually works**,
and defers to the use-case pages for real deployments. The
demo's job is to let a prospective operator see a SPICE
console proxied through kerbside within a few minutes, with
no cloud.

### Why `docker compose`, given a cheaper option existed

`tools/direct-qemu/` is already a working, CI-exercised,
single-host demo: MariaDB, TLS, a static source, a real qemu
SPICE guest and a real client, green on every pull request.
Writing the demo section around those scripts would have
been accurate for free.

It was rejected in favour of compose because the direct-qemu
scripts are a CI harness with CI's assumptions baked in
(`sudo systemctl start mariadb`, `sudo chmod a+rw /dev/kvm`,
apt packages installed into the host, hardcoded `/tmp`
workdirs, a Rust toolchain and a ryll build). Pointing a
prospective evaluator at them asks them to mutate their
machine to look like a GitHub runner. The operator chose the
compose route on 2026-08-14 with that trade-off stated.

The cost is honest: there is **no Python-side Dockerfile and
no published image** — `rust/kerbside-proxy/Dockerfile` is a
build container for the Rust wheel. So this is not a
documentation change, it is new deployment artifacts. An
untested demo path in docs is a liability, and this
repository's culture is that things that must keep working
get a CI lane. Hence phase 4.

## Mission and problem statement

Make `docs/installation.md` a page a reader can follow to a
running kerbside, by building the demo it describes and
fixing the two packaging and configuration defects that
would otherwise force the page to lie.

In scope:

1. Package the migrations and add `kerbside db upgrade`, so
   a wheel install can create its own schema.
2. Write `etc/kerbside.conf.example`, so the two documents
   that already point at it stop lying.
3. Build a `demo/` compose stack: MariaDB plus kerbside
   (API + daemon + proxy), self-bootstrapping TLS, a static
   source, and a documented way to obtain a bearer token.
4. Give the compose stack a CI lane, so it cannot rot.
5. Rewrite `docs/installation.md` around: acquire, minimum
   viable configuration, the demo, then pointers to the
   use-case pages.

Explicitly out of scope, and deferred to issues #300 and
#301, filed alongside this plan:

- **Implementing non-Keystone authentication (#300).** Login
  is Keystone-only: `kerbside/api.py:157` is still
  `# TODO(mikal): Handle non-keystone auth as well`. A
  static-source demo therefore has *no way into the web
  UI*. The direct-qemu lane sidesteps this by hand-minting a
  JWT with PyJWT against `AUTH_SECRET_SEED`
  (`tools/direct-qemu/lane-up.sh:136`). The demo documents
  that workaround; #300 tracks fixing it properly and
  records that `docs/installation.md` and `demo/` need
  editing when it lands.
- **Reworking the session JWT scheme (#301).** The symmetric
  HS256 seed doubles as a minting capability, tokens can
  outlive the Keystone token they encapsulate, and there is
  no revocation or issuance audit. The demo depends on
  exactly that property, so #301 also records the
  documentation impact.
- Production deployment shapes: systemd units, HA, load
  balancers, multi-node. The use-case pages own these.
- Publishing an image to a registry. The demo builds
  locally; publishing is a release-process change.

## Decisions

### 1. The JWT-minting workaround becomes `kerbside demo token` — SETTLED 2026-08-14

**Decided: yes, as a CLI subcommand, labelled demonstration-
only, refusing to mint when any non-`static` source is
configured.** Implemented as phase 1 step 1f.

The reasoning for having the command at all: it is not new
authentication. It mints the payload `flask-jwt-extended`
already accepts, from a seed the operator already possesses
— anyone holding `AUTH_SECRET_SEED` can do this in four
lines, which is precisely what
`tools/direct-qemu/lane-up.sh:129-161` does today. Packaging
it replaces a snippet duplicated between a shell script and
the docs with one tested code path, so the claim shape stops
being reverse-engineered from library internals in two
places.

The reasoning for the guard rails: a command that mints
admin credentials from a config file is a sharp edge in
production, and shipping it unguarded would reduce the
pressure to fix #300.

#### Naming: `kerbside demo token`, not `kerbside token issue`

The label must be structural, not a warning string. A `demo`
command group makes "demonstration use only" a property of
where the command lives, so it cannot be diluted later by
someone adding a second, serious-sounding command beside it.
`kerbside token issue` reads like a supported administrative
operation and would need its demonstration-only status
restated in help text, release notes, and every document
that mentions it.

```
kerbside demo token --subject demo-admin [--duration MINUTES]
```

#### The gate: what "static only" has to mean

A session JWT is **not scoped to a source**. `verify_token`
(`kerbside/api.py:68-78`) checks signature and expiry only,
and the resulting token authenticates the whole API,
including every console of every configured source. So
"refuse to mint tokens for any source other than static"
cannot be implemented per-source. The only coherent reading
is: **refuse unless every configured source is of type
`static`.** One oVirt source in `sources.yaml` and the
command refuses outright.

Three guards, all fail-closed, checked in this order:

1. **Sentinel seed.** Refuse if `AUTH_SECRET_SEED` is still
   `~~unconfigured~~` (`config.py:44`). Without this the
   command would happily mint a token signed with a constant
   that is public in this source tree — issue #131 as a
   feature.
2. **Sources readable.** Refuse if `SOURCES_PATH` is
   missing, unreadable, unparseable, or an empty list. An
   absent source list is not "no non-static sources", it is
   "unknown", and unknown fails closed.
3. **All sources static.** Refuse if any entry's `type` is
   not `static`, naming the offending source and its type in
   the error so the refusal is self-explanatory.

The gate reads `SOURCES_PATH` — the operator's declaration
of intent — rather than the `sources` table, deliberately.
The database can hold rows from a previous configuration
that `_parse_sources()` has not yet reconciled
(`kerbside/main.py:48-120`), and a stale row blocking a
legitimate demo would send people looking for a `--force`
flag, which is how guards die.

#### Implementation notes

- Mint via `flask_jwt_extended.create_access_token` inside
  `kerbside.api.app.app_context()`. Do **not** reimplement
  the payload. The whole point is one place that decides the
  claim shape; a second hand-rolled PyJWT call in the
  package would be the status quo with extra steps.
- Omit the `openstack_token` claim, which the Keystone path
  sets at `api.py:242`. Verified: **that claim is written
  and never read anywhere in the tree**, so a token without
  it is functionally identical. Noted in future work.
- Default `--duration` to `API_TOKEN_DURATION` so the demo
  token behaves like a real one.
- Warn on stderr on every mint; print only the token on
  stdout so it stays pipeable.
- **No audit event.** `AuditEvent.source` and
  `AuditEvent.uuid` are composite primary key columns
  (`kerbside/db.py:685-698`) and the table is console-
  scoped; a mint event would need sentinel values inside a
  primary key. Log loudly instead, and record on #301 that a
  proper issuance audit needs an event shape that is not
  console-scoped.

### 2. Does `installation.md` or the static use-case page own the compose stack? — SETTLED 2026-08-22

`PLAN-use-case-docs.md:49` reserves a "Standalone / static
source" page for the static driver, "for labs, demos, and
direct-qemu style fleets". That page and the demo section
overlap.

**Recommendation:** `installation.md` owns the ten-minute
recipe (the commands, in order, with the expected output).
The standalone use-case page, when written, owns the
framing — why you would run a static source, how it works,
what it cannot do — and links to the installation demo for
the mechanics rather than restating them. This matches how
the oVirt page already relates to `configuration.md`. Noted
here so the author of that page inherits the decision;
`PLAN-use-case-docs.md` gets a pointer in phase 5.

### 3. Loopback only — SETTLED 2026-08-14

**Decided: the demo publishes to the loopback interface
only.** The stack generates its own self-signed CA and seed
and has no real authentication, so binding to `0.0.0.0` on
an evaluator's laptop would expose an
unauthenticated-by-design service to their network.

Every published port is bound `127.0.0.1:` explicitly in
`demo/docker-compose.yml`, with a comment immediately above
saying why, so an evaluator who changes it knows what they
are accepting. `docs/installation.md` states it as a
limitation rather than burying it in the compose file.

This composes with decision 1: the demo token is only
mintable in a purely static deployment, and the thing it
unlocks is only reachable from the machine running it.

## Open questions

None outstanding. Decisions 1-3 above were settled by the
operator on 2026-08-14; phases 1 and 3 no longer have
anything to wait on.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Package migrations, `kerbside db upgrade` | [PLAN-demo-install-phase-01-db-upgrade.md](/components/kerbside/plans/PLAN-demo-install-phase-01-db-upgrade/) | Complete |
| 2. `etc/kerbside.conf.example` | [PLAN-demo-install-phase-02-conf-example.md](/components/kerbside/plans/PLAN-demo-install-phase-02-conf-example/) | Complete |
| 3. The compose demo | [PLAN-demo-install-phase-03-compose-demo.md](/components/kerbside/plans/PLAN-demo-install-phase-03-compose-demo/) | Complete |
| 4. CI lane for the demo | [PLAN-demo-install-phase-04-ci-lane.md](/components/kerbside/plans/PLAN-demo-install-phase-04-ci-lane/) | Complete |
| 5. Rewrite installation.md | [PLAN-demo-install-phase-05-docs.md](/components/kerbside/plans/PLAN-demo-install-phase-05-docs/) | Complete |

The ordering is a dependency chain, not a preference. Phase
3's container entrypoint calls `kerbside db upgrade` from
phase 1 and mounts a config derived from phase 2. Phase 4
tests phase 3. Phase 5 documents all of it and must be
written last, because a page that documents commands that do
not yet behave as described is how this situation arose in
the first place.

Phases 1 and 2 are independently useful and land first even
if the compose work stalls: they fix a packaging defect and
a broken documentation pointer that exist regardless of this
plan.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making.

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** per implementation step with the
   brief from the phase plan, at the recommended effort and
   model.
3. **Review** the output in the management session by
   reading the actual files. The sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if wrong. Diagnose whether the brief was
   insufficient (improve it) or the model too light (upgrade
   it).
5. **Commit** once the management session is satisfied.

Use `isolation: "worktree"` for risky or experimental
changes. Phase 1 touches packaging and every caller of
`alembic`, so it warrants a worktree.

### Verification is not optional in this plan

This plan exists because documentation drifted from
behaviour and nothing caught it. Every phase therefore has a
mechanical check, and a phase is not done until its check
passes:

| Phase | Check |
|-------|-------|
| 1 | Build a wheel, list it, assert the migration tree is inside; install the wheel into a clean venv against a scratch MariaDB and run `kerbside db upgrade`. Unit-test every `kerbside demo token` refusal path, since the guards are the whole value of that command |
| 2 | A unit test asserts every field on `Config` appears in `etc/kerbside.conf.example`, so a new setting cannot be added without documenting it, and the converse so a rename leaves no orphan key. The test is demonstrated to fail before it is trusted. Further assertions that no pasteable value for `auth_secret_seed`, `sql_url` or `public_fqdn` appears, and a separate test that an existing environment variable survives `load_ini_settings()` |
| 3 | `docker compose up` from a clean checkout reaches a proxied SPICE session |
| 4 | The lane is green, and red when the demo is broken deliberately |
| 5 | Every command in the page has been executed, in order, on a clean machine, by the agent writing it |

Phase 5's check is the one that matters most and the easiest
to skip. Do not accept a phase 5 result whose author has not
run the commands.

### Planning effort

Phase 1 is planned at **high** effort: it changes packaging
and moves a directory every migration references. Phase 3 is
**high**: container plumbing plus TLS plus two processes
under one entrypoint has many failure modes. Phases 2, 4 and
5 are **medium**, following patterns already in the tree.

### Model choice

Skew heavy. Phase 1 and 3 are opus. Phases 2 and 4 can be
sonnet given the briefs in their phase plans. Phase 5 is
opus — it must hold the whole system in context to describe
it, and it is the deliverable the operator actually asked
for.

### Management session review checklist

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `tox -eflake8` and `tox -epy3` pass.
- [ ] The phase's mechanical check from the table above
      passes.
- [ ] Commit message follows project conventions.

## Administration and logistics

### Success criteria

* `tox -eflake8` and `tox -epy3` pass.
* A wheel built from the tree contains the migration tree,
  and `kerbside db upgrade` creates the schema from a clean
  install with no repository checkout present.
* `etc/kerbside.conf.example` exists, covers every field on
  `Config`, and a test fails if a field is added without
  updating it.
* `kerbside demo token` mints a working token in the demo
  stack and refuses — with a message naming the reason — on
  a sentinel seed, an unreadable or empty source list, or
  any configured source that is not of type `static`.
* No PyJWT token-minting snippet remains in the tree:
  `tools/direct-qemu/lane-up.sh`'s copy is deleted and
  `demo/` never gains one.
* `docker compose up` in `demo/` on a machine with only
  docker installed reaches a SPICE session proxied by
  kerbside.
* A CI lane exercises that path and is required or
  advisory per `docs/testing.md`'s conventions.
* `docs/installation.md` documents: acquisition, the two
  processes, the minimum configuration, the demo, and
  pointers to the use-case pages — and every command in it
  has been run.
* `README.md`, `AGENTS.md`, `ARCHITECTURE.md`,
  `docs/index.md`, `docs/development.md`, and
  `.claude/skills/add-database-migration.md` are consistent
  with the new migration layout and CLI.
* Issue #3 is closed by this work.
* Lines wrapped at 80 characters in Python per
  `.claude/CLAUDE.md`; single quotes except docstrings; no
  trailing whitespace.

### Future work

* **Publish a container image.** The demo builds locally.
  Publishing `ghcr.io/shakenfist/kerbside` on release would
  turn the demo into `curl one file && docker compose up`.
  A release-process change, deliberately not attempted here.
* **systemd units.** Nothing in the tree helps an operator
  run kerbside as a service on a host. The use-case pages
  will each want this and should share one answer.
* **A startup guard for sentinel config.** Issue #131 asks
  for it for `AUTH_SECRET_SEED`; `config.py` uses the same
  `~~unconfigured~~` sentinel for four security-relevant
  fields. Out of scope here but the compose demo should not
  paper over it.
* **`docs/configuration.md` accuracy.** Issue #131 notes it
  documents `AUTH_SECRET_SEED` as "String (no default)" when
  the real default is the sentinel. Phase 2 will surface
  more of these as it enumerates `Config`; record them
  rather than silently fixing the table out from under the
  issue.
* **The `openstack_token` claim is dead weight.**
  `kerbside/api.py:242` puts the user's Keystone token into
  every session JWT and **nothing in the tree ever reads it**
  (verified by grep). So every token carries an encapsulated
  credential for no purpose, which is both a needless
  disclosure if a token leaks and the reason
  `kerbside demo token` can omit it without behavioural
  difference. Recorded on #301; removing it is a small,
  separate change that wants its own think about backwards
  compatibility for tokens already issued.

### Bugs fixed during this work

* The missing `etc/kerbside.conf.example`, referenced by
  `docs/configuration.md:5` and `ARCHITECTURE.md:345`
  (phase 2).
* Migrations absent from every built artifact, making
  `pip install kerbside` unable to create its schema
  (phase 1).
* `bindep.txt` was missing `python3-dev` / `python3-devel`,
  so `tox -e bindep` reported the dependency list complete
  while an install from it still failed in gcc: mysqlclient
  compiles a C extension and ships no wheel. Found while
  verifying phase 5's install instructions in clean
  `debian:trixie` and `rockylinux:10` containers, which is
  the only way it could have been found — the check the
  repository already had could not see it.

Issues filed while planning:

* **#300 Login is Keystone-only** — no auth path for static,
  oVirt, or Shaken Fist deployments. Records that
  `docs/installation.md` and `demo/` need editing when
  fixed.
* **#301 Session JWT scheme** — symmetric signing key
  doubles as a minting capability, no revocation or issuance
  audit. Same documentation impact.

Related open issues reviewed while planning:

* **#3 Add Installation Guide** — closed by phase 5.
* **#131 Forgeable JWT when `AUTH_SECRET_SEED` left at its
  sentinel default** — not fixed here, but the demo must
  generate a real seed rather than demonstrate the
  vulnerable path, and phase 2 must not contradict the
  issue's description of the current behaviour.
* **#134 Restrict `/console/direct` to admin users** and
  **#132 `GET /source/<name>` discloses cleartext backend
  credentials** — relevant context for how much the demo's
  single token is trusted. The demo has one user and no
  cloud credentials, so neither is exercised, but the docs
  should not encourage reusing the pattern in production.

### Back brief

Before executing any step of this plan, back brief the
operator on your understanding of the plan and how the work
you intend to do aligns with it.
