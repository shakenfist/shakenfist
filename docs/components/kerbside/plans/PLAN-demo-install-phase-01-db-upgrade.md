# Phase 1: package the migrations and add `kerbside db upgrade`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

**Planning effort:** high. This changes packaging and moves
a directory that nine migration files, four documents, one
skill, and two CI scripts reference.

**Review effort:** high. The dangerous failure is silent —
see Risks.

## Situation

The migration tree is not packaged. Verified at `98bef5c` by
building a wheel and listing it:

```
   68  kerbside/api          <- templates and static assets, shipped
    4  kerbside/sources      <- shipped
    8  kerbside/rpc          <- shipped
        alembic/             <- ABSENT
```

`pyproject.toml` declares only `packages = ["kerbside",
"kerbside.rpc"]`, yet `kerbside/api/` and
`kerbside/sources/` ship anyway. The mechanism is
setuptools_scm's git file-finder, which contributes every
git-tracked file *beneath a package directory* as package
data. `alembic/` is at the repository root, outside
`kerbside/`, so no artifact contains it.

Consequences:

- `pip install kerbside` cannot create its schema. There is
  no `kerbside db upgrade`; the only CLI command is
  `kerbside daemon run` (`kerbside/main.py:259`). Running
  `alembic upgrade head` requires `alembic.ini` and
  `alembic/versions/`, i.e. a repository checkout.
- `tools/direct-qemu/start-kerbside.sh:104-133` works around
  this by walking parent directories looking for
  `alembic.ini` and running `alembic upgrade head` from the
  repository root. That is why the CI lanes work and a real
  install would not.

`alembic` is already a runtime dependency
(`pyproject.toml`, `alembic==1.19.1`), so no dependency
change is needed.

## Scope

**In scope:**

- Relocating the migration tree into the package.
- `kerbside db upgrade` and `kerbside db downgrade`.
- `kerbside demo token`, per master plan decision 1.
- Updating every path reference and the two CI scripts that
  invoke `alembic` directly.
- Deleting the duplicated PyJWT minting from
  `tools/direct-qemu/lane-up.sh`.

**Out of scope, deliberately:**

- **A startup guard for the `~~unconfigured~~` sentinel**
  (#131). `kerbside demo token` refuses on the sentinel, but
  that is one command guarding itself, not the daemon-wide
  fail-closed check the issue asks for. Doing it here would
  mean deciding what happens to deployments running on the
  sentinel today, which is an operational question, not a
  packaging one.
- **Removing the `openstack_token` claim** (`api.py:242`,
  never read anywhere). Recorded in the master plan's future
  work. It needs a decision about tokens already issued.
- **`alembic autogenerate` support.** `alembic/env.py:24`
  sets `target_metadata = None`, so autogenerate does not
  work today and this phase does not change that.
- Anything in phases 2-5.

## What the survey found

Surveyed 2026-08-14, against `98bef5c` plus the master plan
commit `e9d6497`. Every factual claim in this plan's
original draft was re-checked. Three findings, two of them
corrections.

### The core assumption is now proven, not assumed

The plan rests on "move the tree under `kerbside/` and
setuptools_scm ships it", which was inferred from why
`kerbside/api/` ships. It has now been tested directly: in a
throwaway copy of the tree the `git mv` was performed and a
wheel built. Result, with **no `pyproject.toml` change**:

```
kerbside/migrations/alembic.ini        shipped
kerbside/migrations/env.py             shipped
kerbside/migrations/script.py.mako     shipped
kerbside/migrations/versions/*.py      9 of 9 shipped
```

That wheel was installed into a clean venv and, **run from
`/tmp` with no checkout present**, this resolved:

```
importlib.resources.files('kerbside') / 'migrations'
  -> .../site-packages/kerbside/migrations
ScriptDirectory.from_config(cfg).walk_revisions()  -> 9 revisions
sd.get_current_head()                              -> cdb5c3529858
```

So step 1c's design is known to work before anyone writes
it. This removes the phase's main unknown.

### Correction: the packaged ini must drop `prepend_sys_path`

`alembic.ini:15` sets `prepend_sys_path = .`, and
`ScriptDirectory.from_config()` **honours it**, verified by
inspecting `sys.path` either side of the call:

```
prepend_sys_path value: '.'
paths added to sys.path by from_config: ['.']
```

In a development checkout that is correct and necessary — it
is what lets `env.py` do `from kerbside.config import
config`. In an *installed* deployment it means `kerbside db
upgrade`, run from any directory, puts that directory on
`sys.path`, so a stray `yaml.py` or `os.py` in the
operator's CWD becomes importable by anything loaded
afterwards. Modest, but gratuitous: an installed `kerbside`
is already importable and needs no path manipulation.

**The packaged copy of `alembic.ini` therefore omits
`prepend_sys_path` entirely.** The root copy keeps it. That
is a real behavioural difference between two otherwise
near-identical files, so both need a comment saying so, or
someone will reconcile it away.

### Correction: tests live in `kerbside/tests/unit/`

The original draft said `kerbside/tests/`. The actual layout
is `kerbside/tests/{unit,functional}/`, with unit tests as
`kerbside/tests/unit/test_*.py` using `testtools` (`class
FooTestCase(testtools.TestCase)`), each carrying a docstring
explaining which defect the test guards against. Corrected
in the step table below, and at source in phase 2's plan,
which had inherited the same error.

### The `env.py` guard is defensive, not load-bearing

The draft called for guarding
`fileConfig(config.config_file_name)` against `None`.
Because step 1c always constructs the `Config` from the
packaged ini, `config_file_name` is always set — confirmed
in the install test. Keep the guard, as it costs one line
and makes `env.py` safe to drive programmatically, but it is
belt-and-braces rather than a fix for a live failure. A
reviewer should not treat it as a blocker.

### Everything else verified true

`env.py:18` is the `fileConfig` call; 9 revisions; the
`daemon` group pattern at `main.py:40-45`; `main.py:259`;
the sentinel at `config.py:43-44`; `verify_token` at
`api.py:68-78`; the Keystone TODO at `api.py:157`;
`openstack_token` at `api.py:242` with `create_access_token`
imported at `api.py:12`; `AuditEvent.source` and `.uuid` as
primary key columns at `db.py:688-689`; and all six stale
path references. No `db`, `demo`, or `token` command group
exists yet. Nothing in the master plan's phase 1 section
needed correcting.

## Decisions

1. **Relocate to `kerbside/migrations/`, keeping
   `alembic.ini` at the repo root.** The alternative — a
   `package_data` or `MANIFEST.in` entry pointing at a
   root-level `alembic/` — keeps files where every document
   already says they are, but ships data outside the package
   that `importlib.resources` cannot then address, which
   defeats the purpose. Relocation costs six path edits
   once; the alternative costs a resolution hack forever.

2. **Ship a *copy* of `alembic.ini` inside the package
   rather than a symlink.** Wheels do not preserve symlinks.
   The copy differs deliberately (`script_location = .`, no
   `prepend_sys_path`), so both files carry a header comment
   naming the other and stating what differs.

3. **The developer workflow does not change.** `alembic
   revision -m ...`, `alembic upgrade head` and `alembic
   downgrade -1` keep working from the repository root,
   because the root `alembic.ini` remains and simply points
   at the new `script_location`. Only paths in prose change.

   This is the decision most likely to be argued with: it
   would be tidier to route developers through `kerbside db
   upgrade` too and delete the root ini. Rejected because
   `alembic revision` has no `kerbside` equivalent and
   inventing one is scope creep — developers would need both
   tools anyway, and a half-migrated workflow is worse than
   an unchanged one.

4. **`downgrade` requires an explicit `--revision`;
   `upgrade` defaults to `head`.** A downgrade with an
   implicit target is a foot-gun against a production
   database.

5. **`kerbside demo token` is a `demo` command group** with
   three fail-closed guards, per master plan decision 1,
   which this phase implements verbatim.

6. **No audit event on token mint.** `AuditEvent.source` and
   `.uuid` are composite primary key columns and the table
   is console-scoped, so a mint event would need sentinels
   inside a primary key. Log loudly; record the gap on #301.

## The CLI commands

### `kerbside db upgrade` / `downgrade`

Add a `db` group beside the existing `daemon` group
(`main.py:40-45` is the pattern to copy). The body builds an
`alembic.config.Config` from the packaged ini and overrides
`script_location`, exactly as the survey verified:

```python
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
import importlib.resources

migrations = importlib.resources.files('kerbside') / 'migrations'
alembic_cfg = AlembicConfig(str(migrations / 'alembic.ini'))
alembic_cfg.set_main_option('script_location', str(migrations))
alembic_command.upgrade(alembic_cfg, revision)
```

`importlib.resources.files()` returns a `Traversable`; for a
plain wheel install it is a real path and kerbside is never
installed as a zipimport egg, so no `as_file()` gymnastics —
but `str()` it, because alembic wants strings.

`env.py` already sets the URL from kerbside config
(`config.set_main_option('sqlalchemy.url',
kerbside_config.SQL_URL)`), so the command needs no
`SQL_URL` handling of its own. Log the resolved target with
`LOG.with_fields({...}).info()`, but **never log `SQL_URL`**
— it contains the database password.

### `kerbside demo token`

```
kerbside demo token --subject demo-admin [--duration MINUTES]
```

A `demo` group, so "demonstration use only" is structural
rather than a warning string that can be diluted later.

Three fail-closed guards, in this order:

1. Refuse if `AUTH_SECRET_SEED` is still `~~unconfigured~~`
   (`config.py:43-44`) — otherwise the command mints tokens
   signed with a constant that is public in this source
   tree.
2. Refuse if `SOURCES_PATH` is missing, unreadable,
   unparseable, or an empty list. Unknown fails closed.
3. Refuse if any configured source's `type` is not
   `static`, naming the offender and its type.

Guard 3 is whole-deployment, not per-source, because a
session JWT is not source-scoped: `verify_token`
(`api.py:68-78`) checks signature and expiry only, and the
token then authenticates every console of every source. Read
`SOURCES_PATH`, not the `sources` table — the table can hold
rows `_parse_sources()` has not reconciled
(`main.py:48-120`), and a stale row blocking a legitimate
demo is how a guard acquires a `--force` flag.

Mint through `flask_jwt_extended.create_access_token`
(imported at `api.py:12`) inside
`kerbside.api.app.app_context()`. Do not reimplement the
payload: one place deciding the claim shape is the entire
justification for this command existing rather than a shell
snippet.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | worktree | `git mv alembic/env.py alembic/script.py.mako alembic/versions kerbside/migrations/` (create the directory first; no `__init__.py` — alembic loads `env.py` by path, and making it a package would put `kerbside.migrations.versions` on the import path for no benefit). Update the root `alembic.ini`: `script_location = kerbside/migrations`, leaving `prepend_sys_path = .` alone. Create `kerbside/migrations/alembic.ini` as a **copy** with `script_location = .` and **`prepend_sys_path` removed** — the survey proved `ScriptDirectory.from_config` honours it and would put the CWD on `sys.path` in an installed deployment. Give both copies a header comment naming the other and stating that difference, so nobody "reconciles" them. In `kerbside/migrations/env.py`, guard line 18 as `if config.config_file_name is not None: fileConfig(config.config_file_name)`. Do not touch the nine migration files' contents. Verify `alembic upgrade head`, `alembic downgrade -1`, and back up, from the repo root against a scratch MariaDB. Commit subject: "Move the migration tree into the package." |
| 1b | low | haiku | worktree | Update the six stale path references, all verified present: `tools/audit/wave2-mechanical.sh:78` (`'alembic/versions/*.py'`), `ARCHITECTURE.md:412` (the `alembic/` tree line), `docs/development.md:11,20`, `AGENTS.md:65`, `.claude/skills/add-database-migration.md:11,14`. Do **not** change the commands in those documents — `alembic revision -m` and `alembic upgrade head` from the repo root still work and remain the documented developer workflow (decision 3). Only directory paths change. Grep for `alembic/` afterwards to confirm nothing was missed, ignoring `docs/plans/` (historical plans are not retrofitted). Commit subject: "Update migration paths after the move." |
| 1c | high | opus | worktree | Add the `db` command group to `kerbside/main.py` with `upgrade` and `downgrade`, following "The CLI commands" above verbatim — the `importlib.resources` resolution, the `script_location` override, `downgrade` requiring `--revision`, `upgrade` defaulting to `head`, and no logging of `SQL_URL`. Place the group after the `daemon` group and its `add_command` call. Wrap alembic's exceptions so a failure exits non-zero with a readable message rather than a traceback, matching how `daemon_run` handles `build_firewall_policy` failure at `main.py:270-274`. Python lines wrap at 80 characters, single quotes. The survey already proved this resolution works from an installed wheel; if it does not work for you, something else is wrong — do not redesign it. Commit subject: "Add kerbside db upgrade and downgrade." |
| 1d | medium | sonnet | worktree | Point the CI scripts at the new command — the change that proves it works on the path a real deployment uses. In `tools/direct-qemu/start-kerbside.sh`, delete the repo-root-walking block (lines 105-121: the `alembic.ini` search, its error path, and the "Using repo root" echo) and replace `(cd "${REPO_ROOT}" && alembic upgrade head)` at line 133 with `kerbside db upgrade`. `REPO_ROOT` is referenced only at lines 108-121 and 133, all of which go, so delete the variable; the survey confirmed there are no other uses. Update the header comment at lines 20-21, which describes the in-place alembic run. In `tools/ovirt-e2e/deploy-kerbside.sh:154` a comment lists `alembic` among the binaries that must resolve from the venv — `alembic` is no longer invoked by `start-kerbside.sh`, so drop it from that list. Commit subject: "Use kerbside db upgrade in the CI lanes." |
| 1e | medium | sonnet | worktree | Add the packaging assertion this phase exists to establish, in `kerbside/tests/unit/test_migrations.py` (note **`tests/unit/`**, not `tests/`; follow the `testtools.TestCase` style and the explanatory-docstring convention of the neighbouring files). Assert that `importlib.resources.files('kerbside') / 'migrations' / 'alembic.ini'` exists; that `versions/` holds at least the nine current revisions; and — the assertion that actually matters — that `alembic.script.ScriptDirectory.from_config` can load the packaged ini and enumerate revisions without touching a database, because that is what breaks if `script_location` resolution regresses. Also assert the packaged ini has no `prepend_sys_path`, so the survey's finding cannot be silently undone. Commit subject: "Test that the migration tree is packaged." |
| 1f | high | opus | worktree | Add the `demo` command group to `kerbside/main.py` with a `token` subcommand, implementing "The CLI commands" above and master plan decision 1 exactly — three fail-closed guards in order, the whole-deployment static check reading `SOURCES_PATH`, minting via `create_access_token` inside `kerbside.api.app.app_context()`, `--duration` defaulting to `API_TOKEN_DURATION`, a stderr warning on every mint, and the token alone on stdout so it stays pipeable. Omit the `openstack_token` claim `api.py:242` sets; it is written and never read anywhere in the tree, verified by grep, so its absence changes nothing — say so in a comment so the next reader does not "fix" it. Do **not** add an audit event (decision 6). Both the group's and the command's help text say demonstration use only. Commit subject: "Add kerbside demo token." |
| 1g | medium | sonnet | worktree | Unit-test the guards in `kerbside/tests/unit/`, which is where this command's value lies — the minting itself is one library call. Cover: sentinel seed refused; missing `SOURCES_PATH` refused; unparseable YAML refused; empty list refused; a single oVirt source refused; a mixed static+oVirt list refused; an all-static list succeeds and returns a token `flask_jwt_extended`'s own verification accepts. Each refusal exits non-zero and names its reason. Then replace the PyJWT heredoc at `tools/direct-qemu/lane-up.sh:129-161` with `kerbside demo token --subject kerbside-ci`, deleting the snippet and its comment about reconstructing the payload. That deletion is the proof the command subsumes the workaround; the direct-qemu lane going green is the test. Commit subject: "Test the demo token guards and adopt it in CI." |
| 1h | low | haiku | none | Comment on issue #301 with two things found here: (i) a proper token-issuance audit needs an event shape that is not console-scoped, because `AuditEvent.source` and `.uuid` are composite primary key columns (`db.py:688-689`), so `kerbside demo token` logs loudly instead of emitting an audit event; (ii) the `openstack_token` claim written at `api.py:242` is never read anywhere in the tree, so every session JWT carries an unused encapsulated Keystone credential. Do not close the issue. |
| 1i | medium | sonnet | none | Final verification on the real artifact, from a scratch directory **outside any checkout** so a stray `alembic.ini` cannot mask a failure. `python -m build --wheel`; assert with `zipfile` that `kerbside/migrations/versions/` (9 files), `env.py`, `script.py.mako` and `alembic.ini` are present; create a clean venv, `pip install` the wheel, `cd /tmp`, set `KERBSIDE_SQL_URL` at a scratch MariaDB, run `kerbside db upgrade`, confirm the tables exist and `alembic_version` is at head (`cdb5c3529858` today). Then confirm `kerbside demo token` refuses with no `SOURCES_PATH` and succeeds against a static-only one. Report the wheel listing and the command output. This is a verification step, not a code change: if it fails, report precisely how rather than patching around it. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| **`script_location` resolves in the dev tree but not in an install** — the wheel builds, tests pass, and it fails only for real users. The phase's most dangerous failure, because everything looks green. | Step 1i runs from outside a checkout against an installed wheel, and the management session checks 1i's evidence *first*. The survey has already proven the happy path, so a failure here means a deviation from the specified design, not an unknown. |
| **The two `alembic.ini` copies drift**, and someone "reconciles" the deliberate `prepend_sys_path` difference away. | Header comments in both files naming the other and the difference; step 1e asserts the packaged copy has no `prepend_sys_path`, so a reconciliation fails the suite. Reviewer checks that assertion exists. |
| **A stale path reference is missed**, leaving a document pointing at `alembic/`. | Step 1b ends with a repo-wide grep excluding `docs/plans/`; the reviewer re-runs it rather than trusting the report. |
| **The `demo token` guard is wrong in a way unit tests miss**, because fixtures are not deployments. | Phases 3 and 4 both exercise the refusal against a running stack with a real non-static source. Phase 1 is not the last word on it. |
| **Deleting the `lane-up.sh` snippet breaks the direct-qemu lane**, blocking unrelated work. | 1g is the last code step and the lane runs on the PR. If it goes red the revert is one file. |

## Definition of done

Falsifiable items only:

- [ ] `python -m build --wheel` produces a wheel containing
      `kerbside/migrations/alembic.ini`, `env.py`,
      `script.py.mako`, and exactly 9 files under
      `versions/`.
- [ ] That wheel, installed into a clean venv, runs
      `kerbside db upgrade` successfully from `/tmp` with no
      `alembic.ini` in the CWD or any parent, and
      `alembic_version` afterwards reads `cdb5c3529858`.
- [ ] `kerbside db downgrade` exits non-zero when given no
      `--revision`.
- [ ] `grep -rn 'alembic/' --exclude-dir=plans docs/ *.md
      tools/ .claude/` returns no hit referring to the old
      location.
- [ ] `alembic revision -m 'x'`, `alembic upgrade head` and
      `alembic downgrade -1` still work from the repository
      root.
- [ ] `kerbside/migrations/alembic.ini` contains no
      `prepend_sys_path` line, and a test asserts it.
- [ ] `kerbside demo token` exits non-zero, naming the
      reason, for each of: sentinel seed, absent
      `SOURCES_PATH`, unparseable YAML, empty list, any
      non-`static` source.
- [x] `grep -rn 'import jwt\|pyjwt' tools/direct-qemu/`
      returns nothing. **Corrected during implementation** —
      the original wording said `tools/`, on the assumption
      that `lane-up.sh` held the only copy. It does not:
      `tools/sf-e2e/drive-happy-path.py`,
      `tools/ovirt-e2e/drive-console.py` and
      `tools/sf-e2e/drive-adversarial.py` each carry one
      too. The first two front a Shaken Fist and an oVirt
      source, so `kerbside demo token` **refuses** for them
      by design, and the third deliberately crafts malformed
      tokens, which is the one thing a shared minting helper
      must not do. Those three keep their snippets; see the
      note below.
- [ ] `tox -eflake8` and `tox -epy3` pass.
- [ ] The direct-qemu lane is green on the PR.
- [ ] Issue #301 has the comment from step 1h.

### Note: the static-only guard limits how far this can be shared

Decision 5's guard has a consequence worth recording, found
while implementing rather than while planning. Four scripts
in `tools/` mint a JWT by hand, not one:

| Script | Source type | Can adopt the command? |
|--------|-------------|------------------------|
| `direct-qemu/lane-up.sh` | static | Yes — done in 1g |
| `direct-qemu/verify-terminate-live.sh` | static | Yes — done in 1g, by reusing lane-up.sh's token file rather than minting again |
| `sf-e2e/drive-happy-path.py` | shakenfist | **No** — the guard refuses |
| `ovirt-e2e/drive-console.py` | ovirt | **No** — the guard refuses |
| `sf-e2e/drive-adversarial.py` | shakenfist | **No**, and should not — it crafts deliberately malformed tokens |

So "one tested code path for the claim shape" is achieved
for the static lanes only. That is the guard working as
specified, not a defect: a command that would mint
credentials for the oVirt lane is exactly what decision 5
set out to prevent. The residual duplication is the price,
and it is the right price — but it means issue #300 remains
the thing that would actually consolidate these, since a
real local-auth mechanism would work for every source type.

## Back brief

Before executing any step, back brief the operator on your
understanding of this plan and how the work aligns with it.

**Gate before step 1a.** The `git mv` touches twelve files
and every subsequent step builds on the resulting layout;
redoing it later means rewriting six path edits and two CI
scripts. Confirm the target layout —
`kerbside/migrations/{env.py,script.py.mako,alembic.ini,versions/}`
with the root `alembic.ini` retained — before any file
moves. No gate is needed on the later steps.

## Registration note

The master plan's Execution table and `docs/plans/index.md`
were updated in the same commit as this plan. The survey
found nothing false in the master plan's phase 1 section, so
no correction was needed there. Of the two corrections it
did find, the tests-directory error was also fixed at source
in phase 2's plan, which had inherited it; the
`prepend_sys_path` finding is new and lives here.
