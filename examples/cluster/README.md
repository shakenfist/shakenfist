# Multi-node Shaken Fist cluster deploy

This example deploys Shaken Fist across several machines. The sample inventory
has three hosts:

| Host | Roles | Mesh IP |
|------|-------|---------|
| `sf-1` | network node **and** database tier (`database_node`) | `10.0.0.1` |
| `sf-2` | hypervisor | `10.0.0.2` |
| `sf-3` | hypervisor | `10.0.0.3` |

A host may belong to several capability groups (here `sf-1` is both the network
node and the database tier). To scale the database tier for high availability,
add more hosts to the `database_node` group — the playbook computes
`mariadb_gateway_hosts` from every member, and clients load-balance across them.

## What this example contains

| File | Purpose |
|------|---------|
| `inventory.yaml` | The three hosts and their capability-group membership. Per-host identity (`node_name`, `node_egress_*`, `node_mesh_*`) lives in each host entry. |
| `group_vars/all.yml` | Cluster-wide variables and secrets. **Edit the secrets and the MariaDB credentials before deploying.** |
| `site.yml` | A one-line wrapper that imports the shared playbook `../_shared/site.yml`. |

The deploy logic lives in `../_shared/site.yml`, shared with the single-node
example. That shared playbook is the only place that reads inventory group
names (`groups[...]`) and cross-host facts; the `shakenfist.shakenfist` roles
read only plain variables.

## Group names

The example inventory uses these groups:

* `allsf` — every Shaken Fist host;
* `hypervisors` — hosts that run instances (`node_is_hypervisor`);
* `network_node` — the single network node (`node_is_network_node`);
* `database_node` — the database tier (`node_is_database_node`).

The legacy `etcd_master` group name is still accepted, with a deprecation
warning, and is removed in the next release. No role reads any group name —
only this playbook does, and it maps the groups onto the role variables.

## Prerequisites

### 1. Install the collection

```bash
ansible-galaxy collection install ./shakenfist/deploy/collection
```

or set `ANSIBLE_COLLECTIONS_PATH` to a tree containing
`ansible_collections/shakenfist/shakenfist/`. Requires `ansible-core >= 2.15`.

### 2. SSH and become

Ansible must be able to reach each host (the sample uses `ansible_host` = the
mesh IP) and `become` root there.

### 3. Bring your own MariaDB

Provision MariaDB on (or reachable by) the database tier and point
`mariadb_host` / `mariadb_user` / `mariadb_password` / `mariadb_database` in
`group_vars/all.yml` at it (see `tools/bootstrap-mariadb.sql` and
`PLAN-byo-mariadb`). Only database-tier nodes render these into
`/etc/sf/config`. The playbook runs `sf-ctl ensure-mariadb-schema` itself
(once, on the first database host) before any node registers.

## Running

```bash
ansible-galaxy collection install ./shakenfist/deploy/collection   # once
ansible-playbook -i examples/cluster/inventory.yaml examples/cluster/site.yml
```

To deploy from local git checkouts instead of PyPI, add
`-e sf_build_local_wheels=true -e repo_path=... -e client_repo_path=...` (see
the single-node README).

## Ordering and the cross-node reductions

The shared playbook deliberately orders the plays so the dependency chain holds:

1. it computes the cross-node values **after** `validate_mtus` has populated
   `node_mtu` (so `max_hypervisor_mtu` = `min(node_mtu over hypervisors)` is
   correct);
2. it bootstraps/configures and registers the **database tier first**, so
   `sf-database` and the gRPC gateway are up before the hypervisors register
   over that gateway;
3. it writes `AUTH_SECRET_SEED` (and the rest of the cluster config) **before**
   any node registers, because registering starts `sf-database`, whose
   `ExecStartPre=verify-config` reads `AUTH_SECRET_SEED`.

For a 3-node inventory like this one, the computed reductions are:

```
network_node_ip       = 10.0.0.1
mariadb_gateway_hosts = ['10.0.0.1']
all_mesh_hosts        = {'sf-1': '10.0.0.1', 'sf-2': '10.0.0.2', 'sf-3': '10.0.0.3'}
max_hypervisor_mtu    = min(node_mtu of sf-2, sf-3)
```

## Log gathering and the cluster inventory

CI used to lean on a `/etc/sf/inventory.yaml` file that the old primary role
wrote on one node, scp it back, and use it as the ansible inventory for the
post-test log-gather step. That round-trip was a drifted convenience; the
playbook here does not write that file. CI (and operators) should point
log-gathering at the same inventory they deployed with — the one in this
directory — which already enumerates every host. CI does this via the shared
`smoke-cluster.yml` reusable workflow in the `shakenfist/actions` repository.
