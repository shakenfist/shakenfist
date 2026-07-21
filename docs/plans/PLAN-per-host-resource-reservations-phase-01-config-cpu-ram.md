# Phase 1: Config keys + CPU/RAM reservation math

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Introduce the three new `NODE_*` reservation keys, rewrite the CPU and RAM
reservation math in the resources daemon and scheduler to use single flat
per-node values (CPU in **threads**, no infra-role branching), and remove the
four old reservation Fields. The `NODE_DISK_RESERVATION_GB` Field is *added*
here (so the whole config surface lands in one commit) but stays unused until
phase 2; `MINIMUM_FREE_DISK` is **kept** until phase 2 converts its consumers.

This phase must be **behaviour-neutral** for CPU and RAM once phase 3 templates
the defaults: every node reserves exactly what it does today. (Between phase 1
and phase 3, nodes fall back to the new Field defaults — see Verification.)

All references below were verified against the `node-reservation-overrides`
worktree; re-confirm by symbol before editing.

## Verified code map

| What | Location |
|------|----------|
| Reservation Fields (contiguous block) | `shakenfist/config.py:230-260` — `RAM_SYSTEM_RESERVATION` (230), `RAM_INFRA_ROLE_RESERVATION` (234), `CPU_SYSTEM_RESERVATION` (242), `CPU_INFRA_ROLE_RESERVATION` (249), `MINIMUM_FREE_DISK` (257) |
| Prose mention of the CPU keys | `shakenfist/config.py:216` — inside the `CPU_OVERCOMMIT_RATIO` description |
| Field idiom | `NAME: type = Field(default, description='...')`; `from pydantic import Field` (config.py:12) |
| Role flag Fields | `NODE_IS_NETWORK_NODE` (config.py:579), `NODE_IS_DATABASE_NODE` (583) |
| `_compute_reservations` | `shakenfist/daemons/resources/main.py:98-130` |
| Its caller (in `Monitor._get_stats`) | `shakenfist/daemons/resources/main.py:169-192` |
| Role-flag metrics published | `main.py:153-157` (`is_network_node`, `is_database_node`, etc.) — **leave as-is** |
| Scheduler CPU routing (primary + fallback) | `shakenfist/scheduler.py:114-142` (`_schedulable_threads`) |
| Scheduler RAM reservation lookup | `shakenfist/scheduler.py:144-150` (`_memory_reserved_mb`) |
| `_has_sufficient_cpu` / `_has_sufficient_ram` | `scheduler.py:152-170` / `172-214` (delegate to the two helpers above) |
| `summarize_resources` (CPU/RAM mirror) | `scheduler.py:583-619` |
| Reservation tests (direct positional calls) | `shakenfist/tests/test_daemon_resources.py:82-170` (`ComputeReservationsTestCase`, 7 tests) |
| Scheduler tests (fake_config + fallbacks) | `shakenfist/tests/test_scheduler.py:13-20`, `:346`, `:368`, `:389-537` |
| Config env-parse test | `shakenfist/tests/test_config.py:26-34` (`RAM_SYSTEM_RESERVATION`) |
| Cluster-config test data | `shakenfist/tests/test_cluster_config.py:66,74` |

## Resolved design decisions

1. **Scheduler legacy fallback (OQ1 — resolved).** The scheduler's primary path
   already reads the node-published `cpu_schedulable` / `memory_reserved_mb`
   metrics; the config keys survive only in the fallback used for metrics rows
   that predate those metrics (rolling upgrade). Re-express both fallbacks
   against the new keys and **drop the infra-role branch** — the per-host value
   already folds in any infra bump, the scheduler cannot know a remote node's
   exact per-host value anyway, and the fallback is transient (it stops the
   moment that node's daemon restarts and republishes `cpu_schedulable`). Also
   drop the hardcoded `reserved_cores * 2`: the new key is already in threads.
2. **Infra-role metrics (OQ2 — resolved).** `is_network_node` /
   `is_database_node` are load-bearing node identity (node.py, database tier,
   API, schema) and **keep being published**. Only their use in reservation math
   goes: the `scheduler.py:140` fallback branch and the `infra_role` local in
   `main.py:179-180` (a throwaway local, never a metric/DB/API field).
3. **Preserve the half-machine memory cap.** `_compute_reservations` caps
   `memory_reserved_mb` at `memory_total_mb // 2` (main.py:121-122) so a small
   all-roles node can still schedule. Keep this — it is also the safety net if
   an operator sets an oversized override.
4. **Keep the informational core metrics, derived.** `cpu_cores_reserved` and
   `cpu_cores_schedulable` are still published, now derived from the thread
   reservation: `cpu_cores_reserved = ceil(cpu_reservation_threads /
   threads_per_core)`. Grep first to confirm nothing schedules on them (the
   scheduler uses `cpu_schedulable`); if truly unused they may be dropped, but
   default to keeping them so dashboards/metrics stay stable.

## Target implementation (reference, not prescription)

New `_compute_reservations` — thread-denominated, no infra args:

```python
def _compute_reservations(cpu_cores, cpu_threads, cpu_reservation_threads,
                          ram_reservation_gb, memory_total_mb):
    memory_reserved_mb = int(ram_reservation_gb * 1024)
    if memory_total_mb:
        memory_reserved_mb = min(memory_reserved_mb, memory_total_mb // 2)

    threads_per_core = math.ceil(cpu_threads / cpu_cores)
    cpu_cores_reserved = math.ceil(cpu_reservation_threads / threads_per_core)
    return {
        'cpu_cores_reserved': cpu_cores_reserved,
        'cpu_schedulable': max(1, cpu_threads - cpu_reservation_threads),
        'cpu_cores_schedulable': max(1, cpu_cores - cpu_cores_reserved),
        'memory_reserved_mb': memory_reserved_mb,
    }
```

Caller (`main.py:178-191`) drops the `infra_role` local and passes the two new
config values:

```python
retval.update(_compute_reservations(
    cpu_cores, cpu_threads,
    config.NODE_CPU_RESERVATION_THREADS,
    config.NODE_RAM_RESERVATION_GB,
    psutil.virtual_memory().total // 1024 // 1024))
```

Scheduler fallbacks:

```python
# _schedulable_threads (was scheduler.py:139-142)
return max(1, cpu_max - config.NODE_CPU_RESERVATION_THREADS), True

# _memory_reserved_mb (was scheduler.py:150)
return self.metrics[node].get(
    'memory_reserved_mb', int(config.NODE_RAM_RESERVATION_GB * 1024))
```

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | opus | none | In `shakenfist/config.py`, in the reservation block at lines 230-260, add three Fields matching the surrounding idiom: `NODE_RAM_RESERVATION_GB: float = Field(2.0, description=...)` (RAM in GB reserved for the OS and host system services on this node), `NODE_CPU_RESERVATION_THREADS: int = Field(2, description=...)` (hardware **threads** — not cores — reserved on this node), `NODE_DISK_RESERVATION_GB: float = Field(20.0, description=...)` (free disk in GB to keep on every filesystem the resources daemon tracks; unused until phase 2). Remove `RAM_SYSTEM_RESERVATION` (230), `RAM_INFRA_ROLE_RESERVATION` (234), `CPU_SYSTEM_RESERVATION` (242), `CPU_INFRA_ROLE_RESERVATION` (249). Do **not** remove `MINIMUM_FREE_DISK`. Update the prose in the `CPU_OVERCOMMIT_RATIO` description at line 216 that names the old CPU keys. |
| 1b | high | opus | none | Rewrite `_compute_reservations` and its caller in `shakenfist/daemons/resources/main.py` per "Target implementation" above: new thread-denominated signature, preserve the half-machine memory cap, derive `cpu_cores_reserved` from the thread reservation, keep publishing `cpu_cores_schedulable`. In the caller (169-192) delete the `infra_role` local (179-180) and pass `config.NODE_CPU_RESERVATION_THREADS` / `config.NODE_RAM_RESERVATION_GB`. Leave the `is_network_node`/`is_database_node` metric publication at 153-157 untouched. Before finalising, grep for consumers of `cpu_cores_reserved`/`cpu_cores_schedulable` to confirm keeping-derived is safe. |
| 1c | high | opus | none | In `shakenfist/scheduler.py`, update the two fallbacks only (the primary metric-reading paths are unchanged): `_schedulable_threads` fallback (139-142) → `max(1, cpu_max - config.NODE_CPU_RESERVATION_THREADS), True` (drop the infra-role branch and the `* 2`); `_memory_reserved_mb` default (150) → `int(config.NODE_RAM_RESERVATION_GB * 1024)`. Update the explanatory comments (122-129, 145-148) to describe threads-based reservation without infra bump. Confirm no other `*_RESERVATION` reference remains in the file. |
| 1d | medium | opus | none | Rewrite `ComputeReservationsTestCase` in `shakenfist/tests/test_daemon_resources.py:82-170` for the new signature/semantics: CPU reservation is threads subtracted directly (no `* threads_per_core`), infra role no longer a parameter, memory cap and floors preserved. Update `shakenfist/tests/test_scheduler.py` — the `fake_config` (13-20: replace `RAM_SYSTEM_RESERVATION=5.0` with `NODE_RAM_RESERVATION_GB=5.0` and add `NODE_CPU_RESERVATION_THREADS`), and the fallback tests `test_missing_cpu_schedulable_falls_back_to_synthetic` (346), `test_old_dialect_infra_node_not_favoured` (368), `test_ram_check_falls_back_to_config_reservation` (460) to the new fallback arithmetic (note: infra nodes no longer reserve more via the *fallback* — `test_old_dialect_infra_node_not_favoured` must be re-thought or retired). Update `test_config.py:26-34` to parse `SHAKENFIST_NODE_RAM_RESERVATION_GB`, and `test_cluster_config.py:66,74`. Run `tox -e py3` until green. |

Steps run in order (1a→1d); 1b and 1c both depend on 1a's key rename. This is
one logical change → **one commit** for the phase.

## Verification

- `pre-commit run --all-files` green (this touches `shakenfist/` Python, so
  flake8/py3/mypy all run for real here — unlike the docs-only plan commit).
- Grep confirms zero remaining references to the four removed keys anywhere in
  `shakenfist/` (production and tests).
- Hand-check the arithmetic: for a node with `T` threads,
  `cpu_schedulable == max(1, T - NODE_CPU_RESERVATION_THREADS)`;
  `memory_reserved_mb == min(NODE_RAM_RESERVATION_GB * 1024, total_mb // 2)`.
- **Interim-state note for the commit message:** until phase 3 templates the
  per-host values, every node reads the Field defaults (2 threads, 2.0 GB), so
  infra-role nodes temporarily lose their historical +bump. This is expected and
  is restored in phase 3; do not deploy the branch to production between phase 1
  and phase 3.
