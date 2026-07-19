# Phase 3 — consolidate the gRPC client stacks (remove the orphan)

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Complete.** The step 3a gate re-verified at branch tip
that `shakenfist/database.py`'s only importer was its own unit test
(no dynamic import, no deploy/console-script reference), so the
module and `shakenfist/tests/test_database.py` were deleted and the
authoritative references updated (`ARCHITECTURE.md`, `CLAUDE.md`, the
`grpc_channel.py` docstring, the `config.py` bootstrap comment). Net
−387 lines. `pre-commit run --all-files` green including the full
unit suite (proving nothing dangled). This phase is a negative
control: it removes no live call path, so no `database_*_total`
counter rate is expected to move — confirm the sfcbr dashboard stays
flat across the deploy.

## Goal

Reduce the sf-database gRPC client surface to a single live
stack, so the phase 4 caller-attribution interceptor has
exactly one seam to hang on rather than two. Investigation
found this reduces to deleting one orphaned module rather
than merging two live ones.

## Findings (why this is a deletion, not a merge)

The apparent duplication was three channel-opening paths:

1. **`shakenfist/mariadb.py`** — `_get_database_stub()` +
   `_grpc_call()` (lines ~324–457). Cached thread-local
   stub, retries only `UNAVAILABLE`/`DEADLINE_EXCEEDED`,
   incident-hardened channel-rebuild logic (rebuild on
   `DEADLINE_EXCEEDED`, keep on `UNAVAILABLE` — issue #3430),
   `wait_for_ready=False` deliberately (June 2026 wedged-
   subchannel failures), raises
   `exceptions.DatabaseUnavailable` on exhaustion (#3373).
   **This is the live client.**
2. **`shakenfist/database.py`** — `get_database_client()` +
   `_retry_database` decorator. 200ms keepalive override,
   5-attempt exponential backoff, retries any `RpcError`,
   `wait_for_ready=True`, re-raises `RpcError` on exhaustion.
   Exposes `acquire_lock`, `release_lock`, `get_lock_holder`,
   `clear_stale_locks`, `get_existing_locks`,
   `get_cluster_config`, `set_cluster_config`,
   `is_available`, `reset_client`, `get_database_client`.
   **This is dead.**
3. **`config.load_cluster_config()`** — inline one-shot
   channel at import time (`config.py` ~line 101), no retry,
   closes after. Deliberate: it runs before `config` is
   initialised and cannot import `mariadb`/`database`.
   **Live; stays as-is.**

Evidence that (2) is orphaned:

* **No non-test importer.** A whole-repo sweep for
  `from shakenfist import database` / `import
  shakenfist.database` / `shakenfist.database.` finds only
  `shakenfist/tests/test_database.py`. (The `'database'`
  strings in `node.py` and `daemons/daemon.py` name the
  sf-**database daemon** — the server — not this client
  module.)
* **The live lock path bypasses it.** `shakenfist/locks.py`
  imports `mariadb` and calls `mariadb.acquire_cluster_lock`,
  `release_cluster_lock`, `refresh_cluster_lock`,
  `get_cluster_lock_holder`, `clear_stale_cluster_locks`,
  `get_all_cluster_locks` — all routed through `_grpc_call`.
* **The live cluster-config path bypasses it.**
  `mariadb.get_cluster_config()` / `set_cluster_config()`
  (mariadb.py ~18645) route through `_use_database_service()`
  to `_grpc_*` / `_direct_*`. `client/ctl.py` calls the
  `mariadb.*` versions, not `database.*`.
* **History confirms supersession.** Commit `e48d3257f`
  ("Route cluster locks through the three-layer mariadb API")
  moved locks onto `mariadb.py` and stranded `database.py`'s
  versions. The only later commits touching `database.py`
  were mechanical (`765c2662c` channel-factory centralise,
  `a6976a5fd` config rename, `2e11feb74` GOAWAY fix) — no new
  callers, no feature work, since the etcd-era migration.
* **The one remaining reference is stale docs.**
  `util/grpc_channel.py`'s docstring cites "the blob path in
  `shakenfist/database.py` shortens the keepalive timeout to
  200 ms" as its example of `extra_options`. That example
  dies with the module.

## Approach

Delete the orphan and its test, fix the stale docstring, and
prove nothing live changed path. No production code path is
altered — `database.py` is not on any of them today.

The import-time bootstrap one-shot in `config.py` is left
alone: it is the one genuinely separate client, exists for a
real reason (pre-`config` bootstrap), and already uses the
shared `make_database_channel()` factory. Folding it into a
"shared low-level client" would buy nothing and risks the
import-cycle it was written to avoid.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Re-verify the orphan claim at branch tip, then delete.** First re-run the dead-code sweep on this branch: `grep -rn` across the repo for `from shakenfist import database`, `import shakenfist.database`, `shakenfist\.database\.`, and any dynamic import (`import_module`, `__import__`) or deploy/console-script reference to the `database` *client* module (not `daemons/database/`, not `database_pb2`). Confirm `locks.py` and `mariadb.get_cluster_config`/`set_cluster_config` are the live paths. If and only if the sole importer is `shakenfist/tests/test_database.py`: delete `shakenfist/database.py` and `shakenfist/tests/test_database.py`. If any live consumer is found, STOP and report — do not delete; the master plan's fallback (shared-core extraction) applies. |
| 3b | low | haiku | none | **Fix the stale docstring.** In `shakenfist/util/grpc_channel.py`, the `make_database_channel` docstring and the `_DEFAULT_OPTIONS` comment reference `shakenfist/database.py` as the example `extra_options` caller. Replace with a generic wording (e.g. "a caller that needs faster failover can shorten the keepalive timeout") so no dangling reference to the deleted module remains. Do not change any option values or behaviour. |
| 3c | low | haiku | none | **Sweep for other dangling references.** `grep -rn "database\.py\|shakenfist\.database\b"` across `docs/`, `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `pyproject.toml`, and comments in `mariadb.py`/`locks.py`/`config.py`. Update or remove any that describe `database.py` as a live client. Report the list of files changed. |

## Verification

* `pre-commit run --all-files` green (flake8, stestr unit
  tests, mypy). The deleted test must not leave a dangling
  import or fixture reference elsewhere — the unit-test run
  proves the suite still collects.
* `grep -rn "shakenfist\.database\b\|from shakenfist import
  database\b"` returns nothing outside `database_pb2*` and
  the `daemons/database/` server directory.
* CI full-cluster run stays green — specifically the paths
  that exercise cluster locks (instance create/delete
  contention), cluster config reads (`sf-ctl`), and node
  bootstrap (`load_cluster_config`). These are the behaviours
  that would regress if the deletion were wrong; they must be
  observed passing, not assumed.
* No change to any `database_*_total` counter rate is
  expected (this phase removes no live calls). Confirm the
  sfcbr dashboard is flat across the deploy as a negative
  control.

## Risks and mitigations

* **A hidden live consumer** (dynamic import, out-of-tree
  caller, deploy glue). Mitigation: step 3a's mandatory
  re-verification and the CI gate. The module is import-light
  and its functions raise/return sentinels, so a missed
  caller would surface as an `ImportError` at startup or a
  test-collection failure, not a silent behaviour change.
* **Someone is mid-flight re-adopting `database.py`** as the
  intended future API (the reverse direction). History says
  no (all recent edits mechanical), but step 3a reporting
  back before the irreversible delete lets the management
  session catch this.

## Documentation

* `ARCHITECTURE.md`: if it describes two database client
  modules, collapse to one (the `mariadb.py` three-layer
  client) plus the bootstrap note.
* No operator-facing config or command changes — nothing for
  `docs/operator_guide/`.

## Success criteria

* `shakenfist/database.py` and its test are gone.
* No dangling references to the module remain in code or
  docs.
* The single live gRPC client is `mariadb.py`'s `_grpc_call`
  / `_get_database_stub`, ready for the phase 4 interceptor.
* `pre-commit` and CI green; sf-database counter rates
  unchanged across the deploy.
