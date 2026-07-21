# Phase 1: Config keys + CPU/RAM reservation math

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Introduce the three new `NODE_*` reservation keys, rewrite the CPU and RAM
reservation math in the resources daemon and scheduler to use single flat
per-node values (no infra-role branching, CPU in threads), and remove the four
old reservation Fields. Disk is deliberately deferred to phase 2 — but the new
`NODE_DISK_RESERVATION_GB` Field is added here (unused until phase 2) so the
config surface lands in one place, and `MINIMUM_FREE_DISK` is **kept** until
phase 2 converts its consumers.

This phase must be **behaviour-neutral** for CPU and RAM: with the phase-3
Ansible defaults, every node reserves exactly what it does today.

## Design decisions to make in this phase

- **Scheduler legacy fallback** (`scheduler.py` ~139-142): the
  `reserved_cores * 2` path exists for stale/old metric rows that predate
  `cpu_schedulable`. Recommended: re-express it as
  `config.NODE_CPU_RESERVATION_THREADS` (already threads, no `* 2`), or drop the
  fallback if the primary metric is always present on a supported cluster.
  Decide by reading how `cpu_schedulable` is published and whether any code path
  can legitimately see a metrics row without it.
- **Infra-role metric flags**: audit whether `is_network_node` /
  `is_database_node` are consumed anywhere besides reservation math (grep). Keep
  publishing them if so; the reservation math itself must stop using them.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | In `shakenfist/config.py`, add three `Field`s: `NODE_RAM_RESERVATION_GB: float = 2.0`, `NODE_CPU_RESERVATION_THREADS: int = 2`, `NODE_DISK_RESERVATION_GB: float = 20.0`, each with a clear `description` (RAM in GB reserved for OS/system services on this node; CPU **hardware threads** — not cores — reserved on this node; free disk in GB to keep on every filesystem the resources daemon tracks). Remove `RAM_SYSTEM_RESERVATION`, `RAM_INFRA_ROLE_RESERVATION`, `CPU_SYSTEM_RESERVATION`, `CPU_INFRA_ROLE_RESERVATION`. Do **not** remove `MINIMUM_FREE_DISK` yet (phase 2). Grep the whole `shakenfist/` tree for each removed name to find every consumer; the resources daemon and scheduler are handled in 1b/1c, but confirm there are no others (a stray reference is an import-time `AttributeError`). |
| 1b | high | opus | none | In `shakenfist/daemons/resources/main.py`, rewrite `_compute_reservations` and its caller so CPU/RAM use the new flat values. CPU: `cpu_schedulable = max(1, cpu_threads - config.NODE_CPU_RESERVATION_THREADS)`; delete the `threads_per_core = ceil(cpu_threads / cpu_cores)` conversion (the reservation is already in threads). RAM: `memory_reserved_mb = config.NODE_RAM_RESERVATION_GB * 1024`. Remove the `is_infra_role` branch from the reservation arithmetic. Keep the `is_network_node` / `is_database_node` metric publication as-is (phase-2/step-1d audit decides their fate). Leave disk reporting untouched here. Preserve `cpu_cores_schedulable` if consumers still need it (check). |
| 1c | high | opus | none | In `shakenfist/scheduler.py`, update `_has_sufficient_cpu` to subtract `config.NODE_CPU_RESERVATION_THREADS` (or, preferably, keep reading the node-published `cpu_schedulable` metric and only touch the config fallback). Resolve the legacy fallback at ~139-142 per the design decision above. Update any RAM path that referenced the removed keys. Do **not** touch the disk path (`_has_sufficient_disk`, `summarize_resources`) — that is phase 2. |
| 1d | medium | sonnet | none | Add/adjust unit tests for the reservation math: CPU threads reserved subtracts directly (no `* 2` conversion), RAM reserved = GB×1024, and infra-role no longer changes CPU/RAM reservations. Follow the existing test module for the resources daemon / scheduler. Ensure `stestr` passes. |

## Verification

- `pre-commit run --all-files` green.
- Hand-check: for a node with N threads, `cpu_schedulable == N - NODE_CPU_RESERVATION_THREADS` (floored at 1); `memory_reserved_mb == NODE_RAM_RESERVATION_GB * 1024`.
- Grep confirms zero remaining references to the four removed keys.
- No behaviour change yet on a real cluster because phase 3 hasn't set the new
  keys — nodes fall back to the Field defaults (2 threads, 2.0 GB), which is the
  *pre-existing* default shape for a plain node. Note in the phase commit that
  infra-role nodes will not regain their +bump until phase 3 templates it.
