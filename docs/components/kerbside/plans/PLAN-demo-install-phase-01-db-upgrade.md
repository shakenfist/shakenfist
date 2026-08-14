# Phase 1: package the migrations and add `kerbside db upgrade`

Master plan: [PLAN-demo-install.md](/components/kerbside/plans/PLAN-demo-install/)

Planned at high effort: this changes packaging and moves a
directory that nine migration files, three documents, one
skill, and two CI scripts reference.

## Situation

The migration tree is not packaged. Verified empirically at
`98bef5c` by building a wheel and listing its contents:

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

## Mission

A wheel-installed kerbside can create and migrate its own
schema with no repository present, via `kerbside db
upgrade`. The developer workflow (`alembic revision -m ...`,
`alembic upgrade head`, `alembic downgrade -1` from the
repository root) keeps working unchanged.

## Approach

Move the migration tree inside the package:

```
alembic/env.py            -> kerbside/migrations/env.py
alembic/script.py.mako    -> kerbside/migrations/script.py.mako
alembic/versions/*.py (9) -> kerbside/migrations/versions/
alembic.ini               -> stays at the repo root, script_location updated
                             (a copy also ships as kerbside/migrations/alembic.ini)
```

Use `git mv` so history follows the files.

Once under `kerbside/`, the file-finder ships them with no
`pyproject.toml` change — but **do not rely on that
silently**. Step 1e adds a test that asserts it, because the
whole point of this phase is that an unasserted packaging
assumption is what broke.

Two details that will bite:

1. **`env.py` calls `fileConfig(config.config_file_name)`**
   (`alembic/env.py:18`). When alembic is driven
   programmatically without an ini, `config_file_name` is
   `None` and `fileConfig(None)` raises. Guard it:
   `if config.config_file_name is not None:`. Do this even
   though step 1c passes an ini, because it makes `env.py`
   safe to drive either way.
2. **`prepend_sys_path = .`** in `alembic.ini` is what lets
   `env.py` do `from kerbside.config import config`. When
   run from an installed wheel, `kerbside` is already
   importable, so this matters only for the repo-root
   developer path. Leave it.

### The CLI command

Add a `db` group to `kerbside/main.py` beside the existing
`daemon` group (`main.py:41-45` is the pattern), with an
`upgrade` command:

```python
@click.group(help='Database commands')
def db():
    pass


cli.add_command(db)


@db.command(name='upgrade', help='Upgrade the database schema to head')
@click.option('--revision', default='head',
              help='Target revision, defaults to head')
@click.pass_context
def db_upgrade(ctx, revision):
    ...
```

The body builds an `alembic.config.Config` from the packaged
ini and overrides `script_location` to the packaged
directory, resolved via `importlib.resources`:

```python
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
import importlib.resources

migrations = importlib.resources.files('kerbside') / 'migrations'
alembic_cfg = AlembicConfig(str(migrations / 'alembic.ini'))
alembic_cfg.set_main_option('script_location', str(migrations))
alembic_command.upgrade(alembic_cfg, revision)
```

Note `importlib.resources.files()` returns a `Traversable`;
for a plain (non-zipped) wheel install it is a real path, and
kerbside is never installed as a zipimport egg. Do not add
`as_file()` gymnastics for a case that cannot occur, but do
`str()` it because alembic wants strings.

`env.py` already sets the URL from kerbside config
(`config.set_main_option('sqlalchemy.url',
kerbside_config.SQL_URL)`), so the command needs no
`SQL_URL` handling of its own — it inherits the same env/INI
resolution as everything else. Log the resolved target with
`LOG.with_fields({...}).info()` per the project convention,
but **never log `SQL_URL`**: it contains the database
password.

Also add `downgrade` while in the file. An operator who can
upgrade and not downgrade is one failed migration from
hand-editing SQL, and `docs/development.md:45` already
documents `alembic downgrade -1` as part of the workflow.

### The `kerbside demo token` command

Settled by the operator on 2026-08-14; see the master plan's
decision 1 for the full reasoning, which step 1f implements
verbatim. Summary of the contract:

```
kerbside demo token --subject demo-admin [--duration MINUTES]
```

A `demo` group, so "demonstration use only" is structural
rather than a warning string that can be diluted later.

Three fail-closed guards, in order — refuse if
`AUTH_SECRET_SEED` is still the `~~unconfigured~~` sentinel;
refuse if `SOURCES_PATH` is missing, unreadable,
unparseable, or an empty list; refuse if any configured
source's `type` is not `static`, naming the offender.

The third guard is whole-deployment, not per-source, because
a session JWT is not source-scoped: `verify_token`
(`api.py:68-78`) checks signature and expiry only, and the
token then authenticates every console of every source. One
non-static source disables the command entirely. Read
`SOURCES_PATH`, not the `sources` table — the table can hold
rows `_parse_sources()` has not yet reconciled, and a stale
row blocking a legitimate demo is how a guard acquires a
`--force` flag.

Mint through `flask_jwt_extended.create_access_token` inside
`kerbside.api.app.app_context()`. Do not reimplement the
payload: one place deciding the claim shape is the entire
justification for this command existing rather than a shell
snippet.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | worktree | `git mv alembic/env.py alembic/script.py.mako alembic/versions kerbside/migrations/` (create the directory first; no `__init__.py` — alembic loads `env.py` by path, and making it a package would put `kerbside.migrations.versions` on the import path for no benefit). Update `alembic.ini` at the repo root: `script_location = kerbside/migrations`. Copy the ini to `kerbside/migrations/alembic.ini` — it must be a copy, not a symlink, because wheels do not preserve symlinks; give the copy `script_location = .` and add a header comment saying the root copy is for the developer `alembic` CLI and this copy is what `kerbside db upgrade` loads, so changes go in both. In `kerbside/migrations/env.py`, guard line 18 as `if config.config_file_name is not None: fileConfig(config.config_file_name)`. Do not touch the nine migration files' contents. Verify with `alembic upgrade head` against a scratch MariaDB from the repo root, then `alembic downgrade -1` and back up. |
| 1b | low | haiku | worktree | Update the stale path references left by 1a. `tools/audit/wave2-mechanical.sh:78` (`'alembic/versions/*.py'` → `'kerbside/migrations/versions/*.py'`), `ARCHITECTURE.md:412` (the `alembic/` line in the tree listing), `docs/development.md:11,20` (`alembic/versions/`), `AGENTS.md:65`, and `.claude/skills/add-database-migration.md:11,14`. Do not change the *commands* in those documents — `alembic revision -m` and `alembic upgrade head` from the repo root still work and remain the documented developer workflow. Only the directory paths change. Grep for `alembic/` afterwards to confirm nothing was missed, ignoring `docs/plans/` (historical plans are not retrofitted). |
| 1c | high | opus | worktree | Add the `db` command group to `kerbside/main.py` with `upgrade` and `downgrade` subcommands, following the design in the "The CLI command" section above verbatim — including the `importlib.resources` resolution, the `script_location` override, and the prohibition on logging `SQL_URL`. Place the group after the `daemon` group and its `add_command` call so the file's existing shape is preserved. `downgrade` takes `--revision` with **no default**, required, because a downgrade with an implicit target is a foot-gun; `upgrade` defaults to `head`. Wrap alembic's exceptions so a failure exits non-zero with a readable message rather than a traceback, matching how `daemon_run` handles `build_firewall_policy` failure at `main.py:270-274`. Python lines wrap at 80 characters. |
| 1d | medium | sonnet | worktree | Point the CI scripts at the new command, which is the change that proves it works on the path a real deployment uses. In `tools/direct-qemu/start-kerbside.sh`, delete the repo-root-walking block (lines ~104-127, the `alembic.ini` search and its error path) and replace `(cd "${REPO_ROOT}" && alembic upgrade head)` at line 133 with `kerbside db upgrade`. `REPO_ROOT` is used only for the alembic lookup — confirm that by grepping the script before deleting it, and delete the now-dead variable too. Update the header comment at lines 20-21 which describes the in-place alembic run. Check `tools/ovirt-e2e/deploy-kerbside.sh:154`, which lists the binaries it needs on PATH and mentions alembic; `alembic` may no longer be needed there. |
| 1e | medium | sonnet | worktree | Add the packaging assertion that this phase exists to establish. In `kerbside/tests/`, following the existing test style there, add a test that the migration tree is importable-and-present as installed data: assert `importlib.resources.files('kerbside') / 'migrations' / 'alembic.ini'` exists, that `versions/` contains at least the nine current revisions, and that `alembic.config.Config` can load the packaged ini and enumerate revisions via `alembic.script.ScriptDirectory.from_config` without touching a database. That last assertion is the valuable one: it fails if `script_location` resolution breaks, which is the actual failure mode. |
| 1f | high | opus | worktree | Add the `demo` command group to `kerbside/main.py` with a `token` subcommand, implementing the contract in "The `kerbside demo token` command" above and the master plan's decision 1 exactly — the three fail-closed guards in order, the whole-deployment static check reading `SOURCES_PATH`, minting via `create_access_token` inside `kerbside.api.app.app_context()`, `--duration` defaulting to `API_TOKEN_DURATION`, a stderr warning on every mint, and the token alone on stdout. Omit the `openstack_token` claim that `api.py:242` sets; it is written and never read anywhere in the tree, verified by grep, so its absence changes nothing — say so in a comment so the next reader does not "fix" it. Do **not** add an audit event: `AuditEvent.source` and `.uuid` are composite primary key columns (`db.py:685-698`) and the table is console-scoped, so a mint event would need sentinels inside a primary key. The group's help text and the command's help text both say demonstration use only. |
| 1g | medium | sonnet | worktree | Unit-test the guards in `kerbside/tests/`, which is where the value of this command is — the minting itself is one library call. Cover: sentinel seed refused; missing `SOURCES_PATH` refused; unparseable YAML refused; empty source list refused; a single oVirt source refused; a mixed static+oVirt list refused; an all-static list succeeds and returns a token that `flask_jwt_extended`'s own verification accepts. Each refusal must exit non-zero and name its reason. Then update `tools/direct-qemu/lane-up.sh` to call `kerbside demo token --subject kerbside-ci` in place of the PyJWT heredoc at lines 129-161, deleting the snippet and its now-obsolete comment about reconstructing the payload. That deletion is the proof the command subsumes the workaround; the direct-qemu lane going green is the test. |
| 1h | low | haiku | none | Comment on issue #301 recording two things found while implementing: (i) a proper token-issuance audit needs an event shape that is not console-scoped, because `AuditEvent`'s `source` and `uuid` are composite primary key columns, so `kerbside demo token` logs loudly instead of emitting an audit event; (ii) the `openstack_token` claim written at `api.py:242` is never read anywhere in the tree, so every session JWT carries an unused encapsulated Keystone token — relevant to that issue's point about tokens outliving the credential they encapsulate. Do not close the issue. |
| 1i | medium | sonnet | none | End-to-end verification on the real artifact, in a scratch directory *outside* any checkout so a stray `alembic.ini` cannot mask a failure: `python -m build --wheel`; assert with `zipfile` that `kerbside/migrations/versions/` and `kerbside/migrations/alembic.ini` are in the wheel; create a clean venv, `pip install` the wheel, `cd /tmp`, set `KERBSIDE_SQL_URL` at a scratch MariaDB, run `kerbside db upgrade`, confirm the tables exist and `alembic_version` is at head. Report the wheel's file listing in the result. This is a verification step, not a code change — if it fails, report precisely how rather than patching around it. |

## Success criteria

* A wheel built from the tree contains
  `kerbside/migrations/alembic.ini`,
  `kerbside/migrations/env.py`,
  `kerbside/migrations/script.py.mako`, and all nine
  revisions.
* `kerbside db upgrade` creates the schema from a clean venv
  install, run from a directory containing no `alembic.ini`.
* `kerbside db downgrade --revision <rev>` works and
  requires an explicit revision.
* `alembic revision -m ...`, `alembic upgrade head`, and
  `alembic downgrade -1` still work from the repository root
  for developers.
* The direct-qemu lane is green with `kerbside db upgrade`
  in place of the repo-root alembic invocation.
* `tox -eflake8` and `tox -epy3` pass; the new test in 1e
  passes.
* No file outside the listed set changed.

## Notes for review

The high-risk failure mode is silent: the wheel builds, the
tests pass, and `script_location` resolves to a path that
happens to exist in the development tree but not in an
install. Step 1i exists specifically to catch that, and it
must be run from outside a checkout. If a reviewer is short
of time, check 1i's evidence first.
