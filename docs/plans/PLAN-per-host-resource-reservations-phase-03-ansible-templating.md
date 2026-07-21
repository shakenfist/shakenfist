# Phase 3: Ansible templating and default computation

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Template the three new reservation keys per host into `/etc/sf/config`, computed
to reproduce today's effective reservation for each node, with an inventory
host_var override. Stop pushing `RAM_SYSTEM_RESERVATION` (and the other old
keys) via `sf-ctl set-config`. This is the phase that restores infra-role nodes'
extra reservation (which phase 1 dropped from server code) by computing it in
Ansible instead.

## Background (verify against the code)

- Per-host node config template:
  `shakenfist/deploy/collection/roles/node/templates/config` — rendered to
  `/etc/sf/config`. Existing per-node keys include `SHAKENFIST_NODE_IS_NETWORK_NODE`,
  `SHAKENFIST_NODE_IS_DATABASE_NODE`, etc. This is where the new keys go.
- Operator-override argument spec:
  `shakenfist/deploy/collection/roles/node/meta/argument_specs.yml` (currently
  declares `ram_system_reservation`).
- Cluster-global push and per-host RAM default:
  `examples/_shared/site.yml` — computes `ram_system_reservation` (~340-348) and
  pushes a single cluster value via `sf-ctl set-config RAM_SYSTEM_RESERVATION`
  (~518-524), sourced from `cluster_db_host` (`database_tier_hosts[0]`).
- Infra-role groups come from the deploy inventory (`network_node`,
  `database_node` groups), rendered per host in `site.yml` ~263-264.

## Default formulas

`infra_role = inventory_hostname in (network_node ∪ database_node)`

- `node_ram_reservation_gb` = `max(2.0, ansible_memtotal_mb / 1024.0 * 0.1) + (infra_role ? 4.0 : 0)`
- `node_cpu_reservation_threads` = `(1 + (infra_role ? 1 : 0)) * 2` → 2 plain, 4 infra
- `node_disk_reservation_gb` = `20`

Each is overridable: if the operator sets `node_ram_reservation_gb` /
`node_cpu_reservation_threads` / `node_disk_reservation_gb` in host_vars, that
value is used verbatim instead of the computed default.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Compute the three defaults per host (role defaults or a `set_fact` block in `examples/_shared/site.yml`, following how `ram_system_reservation` is defaulted today), each guarded by `when: <var> is not defined` so a host_var override wins. Derive `infra_role` from the same group membership `site.yml` already uses for `node_is_network_node` / `node_is_database_node`. |
| 3b | medium | sonnet | none | Add the three lines to `roles/node/templates/config`: `SHAKENFIST_NODE_RAM_RESERVATION_GB={{ node_ram_reservation_gb }}`, `SHAKENFIST_NODE_CPU_RESERVATION_THREADS={{ node_cpu_reservation_threads }}`, `SHAKENFIST_NODE_DISK_RESERVATION_GB={{ node_disk_reservation_gb }}`. Do not add any `set-config` calls for these keys. |
| 3c | medium | sonnet | none | Remove the `sf-ctl set-config RAM_SYSTEM_RESERVATION` play (`site.yml` ~518-524) and any now-dead `ram_system_reservation` plumbing that only fed it. Update `roles/node/meta/argument_specs.yml`: replace the `ram_system_reservation` entry with the three new optional override vars (`node_ram_reservation_gb`, `node_cpu_reservation_threads`, `node_disk_reservation_gb`). Grep the collection for other references to the old var names. |
| 3d | low | haiku | none | Grep the whole deploy collection and `examples/` for the four old `*_RESERVATION` names and `MINIMUM_FREE_DISK`; confirm none remain (the server no longer reads them, so a stray `set-config` would write an inert row). |

## Verification

- Render the node config for a plain hypervisor, an infra-role node, and a node
  with an override host_var (dry-run or check-mode inspection of `/etc/sf/config`).
- Plain node shows threads=2, ram=max(2.0, 10%); infra node shows threads=4,
  ram=+4.0 — i.e. **numerically identical** to what those nodes reserve today.
- Overridden node shows the operator's values.
- Confirm no `RAM_SYSTEM_RESERVATION` (or other old key) is written to
  `cluster_config` by the deploy.
