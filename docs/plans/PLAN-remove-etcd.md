# Retire etcd from the Shaken Fist codebase

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
`shakenfist/etcd.py` in full, the `DATA_MIGRATIONS`
machinery in `shakenfist/mariadb.py`, and grep for `etcd`
across the tree. Ground your answers in what the code
actually does today.

All planning documents should go into `docs/plans/`.

Consult `CLAUDE.md` for the existing commitment to remove
the etcd shim "in the next minor version" and the database
architecture summary. The key references for this plan are
`shakenfist/etcd.py`, the `_migrate_etcd_*` functions and
`DATA_MIGRATIONS` registry in `shakenfist/mariadb.py`,
`shakenfist/deploy/ansible/files/sfrc`,
`shakenfist/deploy/ansible/roles/base/defaults/main.yml`,
and any residual `from etcd...` comments throughout
`mariadb.py`.

This plan is **small and almost mechanical**. It exists as
a standalone plan because its preconditions are external
(another plan landing, then a production redeploy) and
deserve to be stated explicitly so the deletion does not
happen prematurely.

## Situation

Shaken Fist used etcd as its primary datastore until the
move to MariaDB. The `etcd.py` module and the
`DATA_MIGRATIONS` drain functions in `mariadb.py` exist
solely to migrate residual state out of etcd into MariaDB
on first boot against an old cluster. The module is
already documented in `CLAUDE.md` as "retained only to
service `DATA_MIGRATIONS` entries which drain leftover
etcd keys from older clusters" and "will be removed in the
next minor version."

There is exactly one known SF deployment still running on
the etcd-era shape (Mikal's own production cluster). The
plan to rebuild that deployment against the new BYO-
infrastructure shape established by `PLAN-remove-primary.md`
is in flight. Once that rebuild is complete, the etcd
drain code is unambiguously dead weight: no remaining
cluster can ever exercise it, and keeping it around clouds
the codebase with stale references that have to be re-
explained to every new contributor.

## Mission and problem statement

Delete every reference to etcd from the Shaken Fist server
codebase in a single coordinated sweep. The work is small,
mechanical, and well-bounded; its only real complexity is
*timing*.

## Preconditions

This plan **must not start** until all of the following are
true:

1. **`PLAN-remove-primary.md` is complete** through at
   least phase 7 (the `etcd_master` → `database_node`
   rename and final cleanup). Phase 7 removes the stale
   *naming* throughout the deployer; this plan removes the
   stale *code* and the genuine etcd-era references that
   remain after the rename.
2. **The last known etcd-era SF deployment has been
   wiped and redeployed** against the new BYO shape. This
   is the real gating condition: as long as one cluster
   still needs the drain functions on next boot, the drain
   code stays in the tree.
3. **No new etcd-era SF clusters have appeared.** Confirm
   on the day of by asking in the project's normal
   channels.

If any of these is not yet true, this plan does not start.

## Scope

The deletion sweep removes:

- `shakenfist/etcd.py` (entire file).
- Every `_migrate_etcd_*` function in
  `shakenfist/mariadb.py` (currently includes
  `_migrate_etcd_object_states`,
  `_migrate_etcd_ipam_reservations`, and any siblings
  added since this plan was written — confirm by
  inspection at execution time).
- The `DATA_MIGRATIONS` registry entries pointing at
  those drain functions. The registry mechanism itself
  may stay if other (non-etcd) migrations use it; remove
  it entirely if it is left with no callers.
- The "No etcd server configured; marking pending data
  migrations as complete" log line and the surrounding
  short-circuit that exists today to avoid spamming
  "Cannot communicate with etcd" errors on fresh
  clusters.
- The `etcd_host: "127.0.0.1"` default in
  `shakenfist/deploy/ansible/roles/base/defaults/main.yml`.
- The `export ETCDCTL_API=3` line in
  `shakenfist/deploy/ansible/files/sfrc`.
- Every residual `# ... from etcd ...` style comment in
  `mariadb.py` and elsewhere that explains a table
  schema, key naming, or migration history in terms of
  the old etcd layout. After the sweep, the codebase
  should not have to mention etcd to explain itself.
- Any tests that exercise the drain functions or import
  `shakenfist.etcd`.
- The `etcd3gw` dependency in `pyproject.toml` (and any
  transitive references in `tox.ini` or CI config).
- Any documentation in `docs/` that still mentions etcd
  outside of a clearly-historical context. The
  operator guide's references to etcd ports and tuning
  go entirely; release notes / changelog mentions stay
  as historical record.

The sweep does **not** touch the `cluster_locks`,
`cluster_config`, or event-log tables in MariaDB, even
though their schema comments reference their etcd
predecessors. Update the comments rather than the schema.

## Open questions

1. **Single commit or split?** This plan's working
   assumption is one coordinated deletion commit, because
   the pieces are interdependent (deleting `etcd.py`
   without also deleting the importers breaks the build).
   If the deletion turns out to span more than ~500 lines
   of removed code, consider splitting into two commits:
   one for the drain code and module, one for the
   dependency / config / comment sweep. Decide on the day.
2. **Dependency version pinning.** If `etcd3gw` is the
   only consumer of any other transitive dependency,
   those drop out automatically when it does. Worth a
   `pip freeze` comparison before and after to confirm
   no unrelated package versions move.

## Execution

This plan is a single phase. Phase plans below the master
plan are unnecessary given the size.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Sweep | (no separate phase plan needed) | Blocked on preconditions |

### Execution steps

When the preconditions are satisfied:

1. Re-run the grep audit (`grep -rn etcd shakenfist/ docs/
   tools/`) to confirm the scope above is still accurate.
   Add anything that has appeared since this plan was
   written.
2. Make the deletions in one branch.
3. Run `pre-commit run --all-files` and `tox`. The build
   must pass cleanly with no stale imports, no dangling
   references in tests, and no documentation links
   pointing at removed sections.
4. Open the PR with a description that explicitly cites
   this plan and confirms the preconditions are met.
5. Update `CLAUDE.md` to remove the "retained only to
   service `DATA_MIGRATIONS`" paragraph in the database
   section, since the sentence is no longer true.

## Agent guidance

### Execution model

This plan is small enough to be executed by a single
sub-agent in one pass. Use `isolation: "worktree"` because
the deletion is irreversible and worth being able to
discard if the precondition check at the top of the work
turns up a surprise.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | worktree | Re-audit `grep -rn etcd shakenfist/ docs/ tools/` and produce a concrete list of every file and line to be touched. Output the list as a markdown checklist; do not modify any files in this step. |
| 1b   | medium | sonnet | worktree | Execute the deletion sweep against the audit from 1a. Files to delete: `shakenfist/etcd.py` in full; the `_migrate_etcd_*` functions and their `DATA_MIGRATIONS` registry entries in `shakenfist/mariadb.py`; the `etcd_host` default; the `ETCDCTL_API=3` line in `sfrc`; the `etcd3gw` dependency in `pyproject.toml`; residual comments. Update `CLAUDE.md` to remove the etcd shim paragraph. Run `pre-commit run --all-files` and `tox` and fix until they pass. |

### Management session review checklist

- [ ] `grep -rn etcd shakenfist/ docs/ tools/` returns no
      live-code matches after the sweep. Historical
      changelog / release-notes references are acceptable.
- [ ] `pre-commit run --all-files` passes.
- [ ] `tox` passes (unit tests, type checks, style).
- [ ] `pip freeze` before/after shows no unrelated
      dependency version drift.
- [ ] `CLAUDE.md` no longer claims the shim is "retained
      only to service `DATA_MIGRATIONS`".

## Administration and logistics

### Success criteria

* `grep -rn etcd shakenfist/ docs/ tools/` returns only
  historical changelog or release-notes matches.
* `shakenfist/etcd.py` does not exist.
* `etcd3gw` is no longer a project dependency.
* `pre-commit run --all-files` and `tox` pass.
* `CLAUDE.md` and `ARCHITECTURE.md` are updated to remove
  any remaining references to the etcd shim.
* No deployment can be broken by the removal because the
  preconditions guarantee no remaining cluster needs the
  drain code.

### Future work

None expected. This plan is terminal: once executed, the
etcd thread in Shaken Fist's history is closed.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

* **`index.md`** — add a row to the *Plan Status* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing this plan, please back brief the operator
as to your understanding of the preconditions and confirm
that they are met on the day.
