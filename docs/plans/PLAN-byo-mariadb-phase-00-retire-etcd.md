# Phase 0: Retire etcd machinery and migration-era scaffolding

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).
Supersedes the formerly-separate `PLAN-remove-etcd.md`
(absorbed by this plan; that file was deleted in commit
`76fa8ee8b`).

## Prompt

Before responding, read these source files end-to-end —
they are the substrate this phase deletes:

- `shakenfist/etcd.py` (473 lines; the entire etcd shim
  layer used to be the dependency surface for every
  drain function).
- `shakenfist/mariadb.py` lines 2364-2553 (the
  `DATA_MIGRATIONS` framework comment block,
  declaration, and `ensure_data_migrations()` function)
  and lines 2571-~4920 (the ~31 `_migrate_etcd_*` /
  `_cleanup_etcd_*` functions, the `_migrate_node_state
  _key` helper, and the `DATA_MIGRATIONS.update()`
  block at ~4923).
- `shakenfist/protos/etcd_pb2.py`, `etcd_pb2_grpc.py`
  and their `.pyi` siblings (generated stubs for the
  etcd v3 gRPC API).
- `protos/etcd.proto` (the proto source the stubs come
  from).
- `shakenfist/tests/test_cluster_config_drain.py` (117
  lines) and `shakenfist/tests/test_etcd_ops_queues_
  drain.py` (218 lines) — unit tests that exercise the
  drain path; both die when the code they cover dies.
- `shakenfist/client/ctl.py` lines 183-196 (the hidden
  `show-etcd-config` / `set-etcd-config` aliases
  preserved from the etcd era).
- `shakenfist/config.py` line 488 area (the `ETCD_HOST`
  config field, comment already says "remove in next
  release").
- `pyproject.toml` (`etcd3gw==2.6.0` dependency).
- `CLAUDE.md` lines mentioning etcd (58, 246, 369 by
  the latest count — find by string, not line).
- `docs/developer_guide/authentication.md` line 56
  (`ETCDCTL_API=3` example).

This phase deletes Python-side etcd machinery. It does
**not** touch:

- The `etcd_master` ansible group name in `deploy.py`,
  `deploy.yml`, or any ansible role. That rename
  belongs to `PLAN-remove-primary` phase 7.
- The `is_etcd_master` Python attribute on `Node`, the
  matching column on the `nodes` table, the gRPC
  protobuf field, or any other "is this the database
  node?" boolean threaded through the codebase. Per the
  master plan, that attribute is functionally identical
  to "is database node" today and will be renamed in
  `PLAN-remove-primary` phase 7 alongside the ansible
  group rename. Phase 0 leaves the attribute alone to
  avoid cross-scope churn.

One commit per step at minimum. Each commit must build,
pass `pre-commit run --all-files`, and have a clear
message.

## Context

The originally-separate `PLAN-remove-etcd.md` was
absorbed into this plan because the master-plan
exploration found that the etcd machinery was still in
tree even though the data drain had finished. The
master plan's decision 11 records this as the rationale.

That same exploration claimed `DATA_MIGRATIONS` was an
empty dict. The post-phase-1 inventory reveals this was
wrong: `DATA_MIGRATIONS` is declared empty at
`mariadb.py:2396` but populated via
`DATA_MIGRATIONS.update({...})` at line 4923 with 31
table-keyed migration functions that drain etcd into
MariaDB. Those functions never run on a cluster without
`ETCD_HOST` set (`ensure_data_migrations()` short-
circuits at lines 2440-2444 when `ETCD_HOST` is
empty), but they are still in tree as ~2400 lines of
dead code.

Phase 1 of this plan removed the only daemon-side
caller of `ensure_data_migrations()` (commit
`6a4ac7b69`). The function is now orphaned: no code
path invokes it. The orphan is harmless but conspicuous
— phase 0 deletes it along with the rest of the
machinery so the tree reflects reality.

After this phase lands, the following statements are
true:

- `shakenfist/etcd.py` no longer exists. No SF code
  imports etcd v3 gRPC stubs or `etcd3gw`. The
  `etcd3gw` PyPI dependency is gone.
- `ensure_data_migrations()`, the `DATA_MIGRATIONS`
  dict, every `_migrate_etcd_*` / `_cleanup_etcd_*`
  function, and the framework comment block are
  removed from `mariadb.py`. `ensure_schema()` is the
  only entry point for schema management.
- The `ETCD_HOST` config field is gone from
  `shakenfist/config.py`. `sf-ctl show-etcd-config` /
  `set-etcd-config` aliases are gone from
  `shakenfist/client/ctl.py`.
- Source comments referencing retired
  `sf-ctl migrate-*` commands (in `blob.py`,
  `upload.py`, `artifact.py`, `node.py`,
  `namespace.py`, `network/network.py`,
  `constants.py`) are removed or reworded.
- The two etcd-drain test files
  (`test_cluster_config_drain.py`,
  `test_etcd_ops_queues_drain.py`) are deleted.
- `CLAUDE.md` etcd notes (the "etcd.py module is
  retained only to service DATA_MIGRATIONS"
  paragraph and the `etcd3gw` line in the
  Dependencies list) are removed.
- `docs/developer_guide/authentication.md` no longer
  references `ETCDCTL_API=3`.
- The stale comment at `mariadb.py:16904-16906` that
  defers version-bumping to `ensure_data_migrations`
  is reworded (the v1→v2 ALTER TABLE for
  `instance_attributes.vsock_cids` is now standalone;
  the schema migration block immediately below
  bumps the version itself).
- The stale `_construct_key` cross-reference at
  `mariadb.py:1361-1362` is reworded — the comment
  explains lock-key naming and pointed at the
  defunct `etcd._construct_key` function for the
  historical justification.

## Decisions (phase-local)

1. **`is_etcd_master` Python attribute is left alone.**
   The attribute is read and written across `node.py`,
   `mariadb.py` (the `nodes` table column at line
   ~11200), `daemons/database/main.py` (gRPC proto
   round-trips), and `daemons/resources/main.py`. It
   is dead in the sense that no code path reacts to
   its value, but it is alive in the sense that
   removing it requires a coordinated schema migration,
   protobuf change, gRPC client recompile, and Node-
   accessor rewrite. The master plan already scopes
   the rename (`is_etcd_master` → `is_database_node`)
   to `PLAN-remove-primary` phase 7. Phase 0 keeps to
   that boundary.

2. **No staged removal across releases.** Per master
   plan decision 7 (greenfields only), there is no
   compatibility shim, no deprecation warning, no
   `--legacy` flag preserving the old behaviour. The
   single operating SF cluster will be redeployed
   against the post-phase-0 tree.

3. **Schema-version comment update is in-scope.** The
   comment at `mariadb.py:16904-16906` says "We do NOT
   bump the version here. The version is bumped by the
   data migration in `ensure_data_migrations()`." Once
   `ensure_data_migrations` is deleted, that comment
   is misleading. The actual code immediately below
   (lines 16913+) handles version bumping in a
   schema-only manner. Step 0a rewords the comment to
   reflect that the v1→v2 ALTER TABLE is now a pure
   schema migration with the version bump landing in
   the per-version block that follows.

4. **The `_construct_key` historical-reference comment
   is reworded, not deleted.** At `mariadb.py:1361-
   1362` a comment explains the lock_key column naming
   convention by referring to the defunct
   `etcd._construct_key(prefix='sflocks')` function.
   The convention is real and worth documenting; the
   etcd reference is stale. Step 0a rewords the
   comment to describe the convention directly
   without referring to the removed function.

5. **`tox -e genprotos` is not re-run as part of this
   phase.** Phase 0 deletes the etcd `.proto` source
   and its generated stubs by hand. Re-running
   `genprotos` would re-generate stubs for whatever
   `.proto` files remain, which is exactly what we
   want — but doing it after-the-fact in this phase
   risks regenerating other stubs as a side effect
   (whitespace, version markers, etc.). The phase
   leaves the rest of `shakenfist/protos/` untouched.

## Steps

Four steps. They are strictly sequential — step 0a
removes the `etcd` module's only Python importer,
which is the prerequisite for step 0b's actual
deletion of `etcd.py` and its protos. Steps 0c and 0d
follow after the imports are gone but are independent
of each other.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | medium | opus | worktree | Purge dead etcd-migration code from `shakenfist/mariadb.py`. Delete: (i) the `from shakenfist import etcd` import at the top of the file (find the exact line); (ii) the `DATA_MIGRATIONS` framework block at ~lines 2364-2553 — the multi-line docstring-comment intro, the `DATA_MIGRATIONS: dict[...] = {}` declaration, and the `ensure_data_migrations()` function in its entirety; (iii) every `_migrate_etcd_*` and `_cleanup_etcd_*` function at lines ~2571-~4920 (31 functions in total — confirm count with `grep -c '^def _migrate_etcd_\\|^def _cleanup_etcd_' shakenfist/mariadb.py` after the delete to verify zero remain); (iv) the `_migrate_node_state_key` helper at ~line 3325 (only called by other migration functions in the same block); (v) the `DATA_MIGRATIONS.update({...})` block at ~line 4923-~4960 that registers the functions. Then update two stale comments: (vi) at lines ~16904-16906, replace the "We do NOT bump the version here. The version is bumped by the data migration in ensure_data_migrations()" comment with one that describes the v1→v2 ALTER TABLE as a standalone schema migration (the per-version bump immediately below already handles the version increment); (vii) at lines ~1361-1362, replace the "Mirrors etcd._construct_key(prefix='sflocks')" comment with a direct explanation of the `/sflocks/{type}/{subtype}/{name}` convention without referencing the removed function. Verify with grep that no `etcd.` references remain in `mariadb.py`. Run `pre-commit run --all-files`; the existing test suite should pass (no test should rely on the removed migration functions — they are dead code; the dedicated drain tests are deleted in step 0b). Worktree isolation: this is the highest-volume single-step deletion in the phase (~2400 lines), and it is worth being able to discard if the boundary detection went wrong. One commit. |
| 0b | low | sonnet | none | Delete the etcd module, its protos, the proto source, and the drain tests. Files: `shakenfist/etcd.py`, `shakenfist/protos/etcd_pb2.py`, `shakenfist/protos/etcd_pb2.pyi`, `shakenfist/protos/etcd_pb2_grpc.py`, `shakenfist/protos/etcd_pb2_grpc.pyi`, `protos/etcd.proto`, `shakenfist/tests/test_cluster_config_drain.py`, `shakenfist/tests/test_etcd_ops_queues_drain.py`. Use `git rm` for each. After deletion, run `grep -rn 'shakenfist\\.etcd\\|shakenfist/etcd\\|protos\\.etcd_pb2\\|etcd_pb2_grpc' --include='*.py' shakenfist/` and confirm no hits — if anything remains, stop and report. Run `pre-commit run --all-files` to confirm the tree is consistent (no orphaned references). Note: `etcd3gw` is still listed in `pyproject.toml` after this step; step 0c removes it. The dependency staying in place across this step keeps the tree installable from `pyproject.toml` even though no code imports `etcd3gw` any more — sequencing the dep removal into a separate commit makes the bisect history clearer. One commit. |
| 0c | low | sonnet | none | Remove etcd-era surface area from config and the CLI. Three changes: (i) drop the `etcd3gw==2.6.0` entry from `pyproject.toml`'s dependencies list (find the exact line; preserve apparent formatting); (ii) remove the `ETCD_HOST` config field and its accompanying "etcd (retained only for DATA_MIGRATIONS drain — remove in next release)" comment from `shakenfist/config.py` (around line 488); (iii) remove the hidden `show-etcd-config` and `set-etcd-config` `@click.command(name=..., hidden=True)` blocks from `shakenfist/client/ctl.py` (lines ~183-196), AND remove the matching `cli.add_command(show_etcd_config)` / `cli.add_command(set_etcd_config)` entries further down in the file (around line ~350). Run `grep -rn 'ETCD_HOST\\|show-etcd-config\\|set-etcd-config\\|etcd3gw' --include='*.py' --include='*.toml' shakenfist/ pyproject.toml` and confirm only doc/comment references remain (those are step 0d's job). Run `pre-commit run --all-files`. One commit. |
| 0d | low | sonnet | none | Clean stale source comments and developer docs that reference the retired `sf-ctl migrate-*` commands and etcd. Source comments to remove or rephrase: `shakenfist/blob.py:137,143,149` (three "State migration to MariaDB is now handled by sf-ctl migrate-*" comments); `shakenfist/upload.py:54,60`; `shakenfist/artifact.py:121`; `shakenfist/node.py:138`; `shakenfist/namespace.py:101`; `shakenfist/network/network.py:106`; `shakenfist/constants.py:88` (the "Use 'sf-ctl migrate-floating-network-uuid' to migrate" line — the function it talks about no longer exists). For each: read the surrounding code to decide whether the comment can be deleted entirely (preferred when the comment was only there to document the migration path) or whether a brief rewrite preserves useful context. Docs: in `CLAUDE.md`, remove the "Note: the etcd.py module is retained only to service DATA_MIGRATIONS entries which drain leftover etcd keys from older clusters. The module will be removed in the next minor version." paragraph (currently around line 246) and remove the `etcd3gw - etcd client (retained for DATA_MIGRATIONS drain only)` bullet from the Dependencies list (around line 369). In `docs/developer_guide/authentication.md`, remove the `export ETCDCTL_API=3` line (around line 56) — read context to decide whether the surrounding paragraph also needs adjustment. Final verification: `grep -rn 'etcd' --include='*.py' --include='*.md' --include='*.toml' shakenfist/ docs/ CLAUDE.md README.md pyproject.toml 2>/dev/null | grep -v 'is_etcd_master\\|etcd_master' | head -30` should show only references in `docs/plans/` (the plan documents themselves), nothing in source code or operator docs. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
- Final state grep: `grep -rn 'from shakenfist import etcd\\|from shakenfist.etcd\\|import etcd3gw\\|from etcd3gw\\|DATA_MIGRATIONS\\|ensure_data_migrations\\|ETCD_HOST\\|show-etcd-config\\|set-etcd-config\\|_migrate_etcd_\\|_cleanup_etcd_\\|ETCDCTL_API' --include='*.py' --include='*.toml' --include='*.md' shakenfist/ docs/ CLAUDE.md pyproject.toml 2>/dev/null` should show only references in `docs/plans/` (this plan describing the deletion) and `docs/release_notes/` (historical record).
- `python -c "import shakenfist.mariadb; import shakenfist.config; import shakenfist.client.ctl"` succeeds. (Doesn't run the daemon, but proves the imports unwind cleanly.)
- The CI deploy path (cluster_ci) still stands up a cluster end-to-end. This is the integration check that nothing operator-visible regressed; CI is what catches the case where a deleted comment was actually load-bearing (e.g. someone parsed a docstring at runtime, which I don't believe happens but is worth verifying).
- The set of `is_etcd_master` references is unchanged before vs. after the phase. Verifies that scope was respected.

## Risks

- **Boundary-detection error in step 0a.** Deleting
  ~2400 lines from a 17,000-line file by line range
  risks slicing wrong: catching one line of a non-
  migration helper that happens to be in the middle
  of the migration block, or missing the trailing
  `}` of `DATA_MIGRATIONS.update({...})`. The brief
  for step 0a calls out the grep-after-delete check
  (`grep -c '^def _migrate_etcd_\|^def _cleanup_etcd_'`
  must return `0`) and the standalone-import check
  (`grep -n 'etcd\.' shakenfist/mariadb.py` must
  return nothing) as concrete verification steps.
  Worktree isolation lets the management session
  discard the sub-agent's output and re-spin with a
  better brief if the cut went wrong.
- **A migration helper turns out to be reachable
  from non-migration code.** Specifically:
  `_migrate_node_state_key` at line 3325 has the
  shape of a generic helper. The grep does say it
  is only called from within `_migrate_etcd_nodes`,
  but the brief tells the sub-agent to verify with a
  grep before deleting. If a non-migration caller
  exists, the sub-agent reports rather than deleting.
- **Dropping `ETCD_HOST` reveals other readers.** The
  master-plan-time grep showed `ETCD_HOST` consumed
  only by `mariadb.py:2442` (the short-circuit in
  `ensure_data_migrations`). Step 0a deletes that
  consumer first; step 0c then drops the config
  field. If another reader exists somewhere not yet
  identified (e.g. an ansible template), the
  pre-commit run on step 0c will not catch it (ansible
  templates aren't linted for Pydantic config
  references), but the cluster_ci validation will. If
  pre-commit fails on step 0c with a config-related
  error, that's the signal to find the unexpected
  reader.
- **The `# sf-ctl migrate-*` comments in step 0d are
  scattered.** It is easy to miss one. The final grep
  in the step 0d brief (`grep -rn 'sf-ctl migrate'
  --include='*.py' shakenfist/`) covers this.

## Out of scope

For clarity, none of these are touched by phase 0:

- `is_etcd_master` Python attribute on `Node`, the
  matching column on `nodes`, the gRPC field, any
  caller. → `PLAN-remove-primary` phase 7.
- `etcd_master` ansible-group name in `deploy.py`,
  `deploy.yml`, ansible roles, inventory templates,
  `installation.md` examples. → `PLAN-remove-primary`
  phase 7.
- The release-notes files under `docs/release_notes/`
  (e.g. `v07-v08.md`) that mention retired
  `sf-ctl migrate-*` commands. These are historical
  records; leaving them as-is is correct.
- The `.claude/skills/migrate-etcd-to-mariadb.md`
  skill — already deleted in master-plan commit
  `76fa8ee8b`.
- `PLAN-remove-etcd.md` — already deleted in master-
  plan commit `76fa8ee8b`.

## Back brief

Before executing this phase, please back brief the
operator on:

- The four steps in order, with the file or line-
  range each touches.
- The deliberate decision to leave `is_etcd_master`
  alone (left for `PLAN-remove-primary` phase 7), so
  the management session and the sub-agents do not
  accidentally widen scope.
- The corrected understanding that `DATA_MIGRATIONS`
  was *not* empty as the master plan claimed — step
  0a is removing 31 active migration-function
  registrations along with the function bodies. The
  functions never run on a cluster without
  `ETCD_HOST`, but they are not zero-byte stubs.
- The validation step that confirms `is_etcd_master`
  references are unchanged before and after the
  phase, as the cross-check that scope was respected.
