# Phase 4: Docs, plan index, and cleanup

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Bring the documentation in line with the shipped model (per-node `NODE_*`
reservation keys, CPU in threads, per-node disk reservation, values set via
`/etc/sf/config` not `sf-ctl set-config`), keep the plan index current, and —
optionally, on the operator's call — build `sf-ctl unset-config` to retire the
now-inert `RAM_SYSTEM_RESERVATION` cluster_config row.

All references verified against the `node-reservation-overrides` worktree.

## Verified code/doc map

| What | Location |
|------|----------|
| Operator scheduling page (the main rewrite) | `docs/operator_guide/scheduler.md` — stale at L32, L42-72 (System reservations), L113-114, L122-129 (config table), L165-171 (mixed-version) |
| Unreleased release notes | `docs/release_notes/v07-v08.md:429-470` (old keys narrative) |
| Architecture summary | `ARCHITECTURE.md:789-793` ("plus more on nodes carrying cluster-wide roles" — now false); L795-796 CPU_OVERCOMMIT still accurate |
| Agent notes | `AGENTS.md:227-239` (stale "role-aware reservation" fallback; missing `disk_reservation_gb` metric) |
| README.md | no reservation content — no change |
| Docs build gate | `tox -e docs` → `mkdocs build` (tox.ini:39-44) |
| Plan nav | `docs/plans/order.yml:25` already lists the master plan (correct); phase files correctly excluded |
| Scheduler page nav | **orphaned** — `docs/operator_guide/scheduler.md` is not in `mkdocs.yml`/`mkdocs.yml.tmpl` Operator Guide nav (pre-existing) |

No generated `SHAKENFIST_*` config-key reference exists; the only structured
table is the hand-maintained one in `scheduler.md:122-129`. Other docs mention
`SHAKENFIST_*` only for unrelated keys — no change needed.

## Operator decisions (confirmed)

- **Release notes: rewrite in place.** v0.7→v0.8 is unreleased, so
  `v07-v08.md:429-470` is rewritten to describe the *shipped* model (three
  per-node `NODE_*` keys, CPU in threads, per-node disk reservation) rather than
  layering a second migration note on top of the old one.
- **`sf-ctl unset-config` (step 4f): build it now.** The operator opted to build
  it and retire the inert `RAM_SYSTEM_RESERVATION` row in this phase, rather than
  deferring. Small and low-risk (see below).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Rewrite `docs/operator_guide/scheduler.md` for the shipped model. (1) Disk admission line (~L32): per-node `NODE_DISK_RESERVATION_GB`, published as the `disk_reservation_gb` metric, applied at the instances/blobs allocation points. (2) "System reservations" section (~L42-72): CPU reservation is now in **hardware threads** via `NODE_CPU_RESERVATION_THREADS` (no cores→threads conversion, no infra-role bump); RAM via `NODE_RAM_RESERVATION_GB`; disk via `NODE_DISK_RESERVATION_GB`. State clearly these are **per-node** values set through `/etc/sf/config` (templated per host by the deploy) and **never** via `sf-ctl set-config`. (3) Config table (~L122-129): replace the four old rows with `NODE_RAM_RESERVATION_GB` (2.0), `NODE_CPU_RESERVATION_THREADS` (2), and ADD a `NODE_DISK_RESERVATION_GB` (20.0) row. (4) Overcommit-restore note (~L113-114): update key names. (5) Mixed-version paragraph (~L165-171): the fallback now subtracts `NODE_CPU_RESERVATION_THREADS` with no infra-role bump (`scheduler.py` `_schedulable_threads`). |
| 4b | low | sonnet | none | Rewrite the "Load-aware scheduling and system reservations" section of `docs/release_notes/v07-v08.md:429-470` to the shipped model (per-node `NODE_*` keys, CPU in threads, per-node disk reservation, `/etc/sf/config` delivery). Keep the `CPU_OVERCOMMIT_RATIO` semantic-change note but update the key names it references. Do not add a second migration note — describe the end state. |
| 4c | low | haiku | none | One-line corrections: `ARCHITECTURE.md:789-793` — drop the "plus more on nodes carrying cluster-wide roles (network node, database node)" clause (the infra-role bump is gone from server code; it is now folded into the Ansible-computed per-host default). `AGENTS.md:227-239` — drop the "role-aware reservation" claim in the CPU-fallback sentence, and add `disk_reservation_gb` to the list of published metrics. Leave the still-accurate parts (helper names, `cpu_schedulable`/`memory_reserved_mb`, `CPU_OVERCOMMIT_RATIO`). |
| 4d | low | sonnet | none | Docs consistency: confirm all four phase rows exist in `docs/plans/index.md` (they do — keep the status column current). Add the missing Scheduler nav entry — `- "Scheduler": operator_guide/scheduler.md` — to the Operator Guide nav in BOTH `mkdocs.yml` and `mkdocs.yml.tmpl` (pre-existing orphan; bundling it makes the rewritten page reachable). Run `tox -e docs` and confirm `mkdocs build` is green with no broken links. |
| 4e | low | sonnet | none | Grep `docs/` (excluding `docs/plans/`) for the five removed key names once more; confirm the only remaining references are the ones 4a/4b rewrote. Sanity-check that the `NODE_*` key names and defaults quoted in the docs match `shakenfist/config.py` exactly. |
| 4f | high | opus | none | Build `sf-ctl unset-config <KEY>` and retire the inert row. The direct DB delete already exists (`mariadb.py:18613-18630`, `_direct_delete_cluster_config`, currently dead). Add: (a) `_grpc_delete_cluster_config` + public `delete_cluster_config` in `mariadb.py` mirroring the `set_cluster_config` trio; (b) proto — one `DeleteClusterConfig` RPC on `DatabaseService` reusing the existing `StatusReply`, plus a `DeleteClusterConfigRequest{ string key_name = 1; }` message in `protos/database.proto`; regenerate with `tox -e genprotos` and commit the four stub files; (c) a `DeleteClusterConfig` servicer in `daemons/database/main.py` next to `SetClusterConfig` (mirror exactly, register a counter); (d) an `unset_config` command in `client/ctl.py` mirroring `set_config`, registered in the `cli.add_command(...)` tail. Optionally add a delegated one-off deploy task (or operator note) to run `sf-ctl unset-config RAM_SYSTEM_RESERVATION`. `pre-commit run --all-files` green; regenerated stubs committed. |

Steps 4a-4e are documentation → **one commit**. Step 4f, if done, is a separate
logical change → its own commit (it touches proto/daemon/CLI, not docs).

## Verification

- `tox -e docs` (`mkdocs build`) green; the rewritten Scheduler page renders and
  is reachable from the nav; no broken internal links.
- Grep confirms `docs/` (outside `docs/plans/`) has no stale references to the
  five removed keys; the `NODE_*` names/defaults quoted match `config.py`.
- If 4f done: `sf-ctl show-config` no longer lists `RAM_SYSTEM_RESERVATION` after
  the unset runs; `tox -e genprotos` output committed; `pre-commit run
  --all-files` green.

## Note — end of the code plan

After phase 4 (docs), the shakenfist branch is code-complete. Remaining
non-code work lives in Future work on the master plan: the end-to-end deploy
validation (`ansible-playbook sfcbr.yml -e
shakenfist_mirror_branch=node-reservation-overrides` from 33fl, then inspect
`/etc/sf/config` and node metrics on a plain, an infra-role, and the overridden
node), and the separate 33fl `host_vars/sf-1.yml` override that actually gives
the zeek sensor its headroom.
