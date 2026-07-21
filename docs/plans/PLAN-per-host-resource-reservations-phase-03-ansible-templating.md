# Phase 3: Ansible templating and default computation

Master plan: [PLAN-per-host-resource-reservations.md](PLAN-per-host-resource-reservations.md)

## Goal

Template the three new reservation keys per host into `/etc/sf/config`, computed
to reproduce today's *intended* reservation for each node (system baseline plus
the infra-role bump), with an inventory host_var override. Stop pushing
`RAM_SYSTEM_RESERVATION` via `sf-ctl set-config`. This restores the infra-role
bump that phase 1 removed from server code by computing it in Ansible instead.

All references verified against the `node-reservation-overrides` worktree; the
deploy collection lives at `shakenfist/deploy/collection/` and the playbook that
uses it at `examples/_shared/site.yml`.

## Verified code map

| What | Location |
|------|----------|
| Node config template (→ `/etc/sf/config`) | `shakenfist/deploy/collection/roles/node/templates/config` — no reservation key today |
| Numeric idiom to copy | `SHAKENFIST_MARIADB_PORT={{ mariadb_port }}` (unquoted int) |
| Bool/var idiom | `SHAKENFIST_NODE_IS_NETWORK_NODE` ← `node_is_network_node` (config:19-23) |
| Role defaults (flat plain vars) | `roles/node/defaults/main.yml:15-18` (capability flags); no `vars/` dir |
| Arg spec (single `&node_options` anchor, reused by 4 entry points) | `roles/node/meta/argument_specs.yml:15` |
| Config-write task (reads only role vars) | `roles/node/tasks/config.yml:76-84` |
| Per-host capability facts | `examples/_shared/site.yml:260-264` (`node_is_network_node`, `node_is_database_node`); `database_tier_hosts` at 234-238 |
| Old RAM default `set_fact` | `examples/_shared/site.yml:340-348` (`when: ram_system_reservation is not defined`) |
| Old `set-config RAM_SYSTEM_RESERVATION` push | `examples/_shared/site.yml:518-523` (delegated to `cluster_db_host`, single cluster-wide row) |
| Plays that render the template (facts gathered) | `site.yml:411-414`, `426-429` (`gather_facts: true`) → `ansible_memtotal_mb` available at template time |
| Old-key references to clean up | `site.yml:341,343,347,348,521`; `examples/single-node/README.md:93,99` |
| ansible-lint config (`profile: basic`, `no-role-prefix` skipped) | `shakenfist/deploy/collection/.ansible-lint` — lints the collection (role) but NOT `examples/site.yml` |

Zero deploy references to `RAM_INFRA_ROLE_RESERVATION`, `CPU_SYSTEM_RESERVATION`,
`CPU_INFRA_ROLE_RESERVATION`, or `MINIMUM_FREE_DISK` — no CPU or disk reservation
is pushed by the deploy today; those ran on server defaults.

## Resolved design decisions

1. **Compute in `site.yml` Play 1 as `set_fact`, guarded by `when: … is not
   defined`.** Matches the existing `ram_system_reservation` idiom, reuses the
   already-computed `node_is_network_node`/`node_is_database_node` facts in the
   same play, and keeps the node role a pure plain-var consumer (its documented
   contract — the role must not read inventory groups or facts). An inventory
   host_var/group_var of the same name overrides via the `when` guard.
2. **Literal fallbacks in the role defaults** (`node_ram_reservation_gb: 2.0`,
   `node_cpu_reservation_threads: 2`, `node_disk_reservation_gb: 20`) so a
   standalone/role-isolated run still renders, matching the server Field
   defaults. Document them in `argument_specs.yml` under the `&node_options`
   anchor (add once → inherited by all four entry points).
3. **Remove** the old `ram_system_reservation` `set_fact` (site.yml:340-348) and
   the `set-config RAM_SYSTEM_RESERVATION` task (site.yml:518-523). Do **not**
   add `set-config` for the new keys — they must stay Layer-1 (`/etc/sf/config`)
   only.

## Behaviour-neutrality — honest accounting

This phase is *not* a strict numeric no-op for every node; it reproduces today's
*intended* reservation while correcting two quirks. Call these out in the commit:

- **RAM (a correction):** today the deploy pushes **one** cluster-wide
  `RAM_SYSTEM_RESERVATION` equal to the *db-host's* 10%, applied to every node
  (plus the server's +4 GB on infra nodes). The new default computes **each
  node's own** 10% + infra bump. On a cluster with uniform RAM this is
  identical; on heterogeneous-RAM nodes the new value is the correct per-host
  one and will differ from the old single value.
- **CPU (accepted approximation):** the new default reserves
  `(1 + infra?1:0) * 2` threads. This equals today's `reserved_cores` converted
  at 2 threads/core on hyperthreaded/hybrid hardware, but reserves ~1 extra
  thread on non-hyperthreaded nodes (1 core → 1 thread today vs 2 threads now).
  Operator previously accepted "assume 2 threads/core, close enough."
- **Disk:** `20` GB per host = the retired `MINIMUM_FREE_DISK` default —
  identical.

## Target implementation (reference)

`site.yml` Play 1, replacing the `ram_system_reservation` block (340-348):

```yaml
    - name: Default the per-host resource reservations
      ansible.builtin.set_fact:
        node_ram_reservation_gb: >-
          {{ ([2.0, (ansible_memtotal_mb / 1024.0 * 0.1) | round(2)] | max)
             + ((node_is_network_node or node_is_database_node) | ternary(4.0, 0.0)) }}
      when: node_ram_reservation_gb is not defined

    - name: Default the per-host CPU thread reservation
      ansible.builtin.set_fact:
        node_cpu_reservation_threads: >-
          {{ (1 + ((node_is_network_node or node_is_database_node) | ternary(1, 0))) * 2 }}
      when: node_cpu_reservation_threads is not defined

    - name: Default the per-host disk reservation
      ansible.builtin.set_fact:
        node_disk_reservation_gb: 20
      when: node_disk_reservation_gb is not defined
```

`roles/node/templates/config` (append, unquoted numeric idiom):

```
SHAKENFIST_NODE_RAM_RESERVATION_GB={{ node_ram_reservation_gb }}
SHAKENFIST_NODE_CPU_RESERVATION_THREADS={{ node_cpu_reservation_threads }}
SHAKENFIST_NODE_DISK_RESERVATION_GB={{ node_disk_reservation_gb }}
```

`roles/node/defaults/main.yml` (literal fallbacks) and `argument_specs.yml`
(three options under `&node_options`, `type: float`/`int`, with descriptions
naming the `SHAKENFIST_NODE_*` key each becomes).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | In `examples/_shared/site.yml`, replace the `ram_system_reservation` `set_fact` (340-348) with the three `set_fact` tasks from "Target implementation" (each `when: <var> is not defined`), placed in the same Play 1 after the capability-flag facts (260-264) so `node_is_network_node`/`node_is_database_node` and `ansible_memtotal_mb` are available. Keep the explanatory comment updated (per-host, folds in the infra-role bump). |
| 3b | low | haiku | none | In `examples/_shared/site.yml`, delete the "Set the RAM system reservation" `set-config` task (518-523). Leave the other set-config tasks (AUTH_SECRET_SEED, MAX_HYPERVISOR_MTU, DNS_SERVER, HTTP_PROXY, extra_config) untouched. |
| 3c | medium | sonnet | none | In `roles/node/templates/config`, append the three `SHAKENFIST_NODE_*_RESERVATION*` lines (unquoted numeric idiom, referencing `node_ram_reservation_gb` / `node_cpu_reservation_threads` / `node_disk_reservation_gb`), grouped with a comment near the other NODE_* keys. |
| 3d | medium | sonnet | none | In `roles/node/defaults/main.yml` add literal fallbacks `node_ram_reservation_gb: 2.0`, `node_cpu_reservation_threads: 2`, `node_disk_reservation_gb: 20` (a "Reservations" comment banner). In `roles/node/meta/argument_specs.yml` add the three options once under the `&node_options` anchor (`type: float`/`int`/`float`, matching defaults, description names the `SHAKENFIST_NODE_*` key). Follow the existing option format exactly. |
| 3e | low | haiku | none | Update `examples/single-node/README.md:93,99` to drop the `ram_system_reservation` / `RAM_SYSTEM_RESERVATION` mentions (or reframe as the per-host `node_*_reservation_*` vars). Grep `shakenfist/deploy/` and `examples/` for all six old-key tokens (`RAM_SYSTEM_RESERVATION`, `RAM_INFRA_ROLE_RESERVATION`, `CPU_SYSTEM_RESERVATION`, `CPU_INFRA_ROLE_RESERVATION`, `MINIMUM_FREE_DISK`, `ram_system_reservation`) and confirm zero remain. |

Steps 3a–3e are independent edits to the same logical change → **one commit**.

## Verification

- `pre-commit run --files shakenfist/deploy/collection/...` runs `ansible-lint`
  on the role changes (3c/3d); it does not lint `examples/site.yml` (3a/3b) —
  eyeball those against the `ansible.builtin.` / capitalized-task-name style.
  (`tox -e py3`/flake8 are not triggered — no Python changes.)
- Render/inspect `/etc/sf/config` (check mode or a scratch render) for three
  node shapes: a plain hypervisor (`threads=2`, `ram=max(2.0, 10%)`, `disk=20`);
  an infra-role node (`threads=4`, `ram=+4.0`); and a node with a host_var
  override (operator values verbatim). Confirm the three
  `SHAKENFIST_NODE_*_RESERVATION*` lines render with numeric values.
- Confirm no `RAM_SYSTEM_RESERVATION` (or other old key) is written to
  `cluster_config` by the deploy (the set-config task is gone).
- End-to-end (real cluster): `ansible-playbook sfcbr.yml -e
  shakenfist_mirror_branch=node-reservation-overrides` from 33fl, then
  `sf-ctl show-config` (new keys absent from cluster_config) and per-node
  `/etc/sf/config` + node metrics reflect the expected per-host values.
