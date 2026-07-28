# Single-node Shaken Fist deploy

This example deploys a complete Shaken Fist cluster onto a single machine.
`localhost` is a member of every capability group, so it is the hypervisor, the
network node, and the database tier all at once. It is the recommended
quickstart and the simplest thing to deploy.

## What this example contains

| File | Purpose |
|------|---------|
| `inventory.yaml` | `localhost` in every capability group (`hypervisors`, `network_node`, `database_node`) plus `allsf`. Per-host identity (`node_name`, `node_mesh_ip`, ...) lives in the host entry. |
| `group_vars/all.yml` | Cluster-wide variables and secrets (`auth_secret`, `system_key`, MariaDB credentials, package references). **Edit the secrets before deploying.** |
| `site.yml` | A one-line wrapper that imports the shared playbook `../_shared/site.yml`. |

The actual deploy logic lives in `../_shared/site.yml`, which both this example
and the cluster example import. That shared playbook is the only place that
reads inventory group names (`groups[...]`) and cross-host facts; the
`shakenfist.shakenfist` roles read only plain variables.

## Prerequisites

### 1. Install the collection

The playbook references the roles by FQCN (`shakenfist.shakenfist.node`, etc.),
so the `shakenfist.shakenfist` collection must be on the Ansible collections
path. Either:

```bash
ansible-galaxy collection install ./shakenfist/deploy/collection
```

or point `ANSIBLE_COLLECTIONS_PATH` at a tree that contains
`ansible_collections/shakenfist/shakenfist/` (a symlink to
`shakenfist/deploy/collection` works for local development).

You also need `ansible-core >= 2.15` and `ansible-lint` if you want to lint.

### 2. Provision MariaDB (byo-mariadb single-box prerequisite)

Shaken Fist no longer installs or manages MariaDB itself; you bring your own
database. On this single box:

```bash
# Install the server.
sudo apt install mariadb-server

# Create the shakenfist user, database and grants.
sudo mariadb < tools/bootstrap-mariadb.sql
```

`tools/bootstrap-mariadb.sql` creates the `shakenfist` user and `sf` database.
Make sure the `mariadb_user` / `mariadb_password` / `mariadb_database` values in
`group_vars/all.yml` match what that SQL provisions.

You do **not** need to create the schema by hand: this playbook runs
`sf-ctl ensure-mariadb-schema` itself (delegated to the database-tier host)
before any node registers.

## Running

```bash
ansible-galaxy collection install ./shakenfist/deploy/collection   # once
ansible-playbook -i examples/single-node/inventory.yaml examples/single-node/site.yml
```

Run it as a user that can `become` root on the target (the playbook uses
`become: true`).

## Deploying from a local git checkout (developers / CI)

By default the playbook installs the released `shakenfist` and
`shakenfist-client` packages from PyPI. To deploy the code in your local
checkouts instead, set the build flag and point at the two repos:

```bash
ansible-playbook -i examples/single-node/inventory.yaml examples/single-node/site.yml \
  -e sf_build_local_wheels=true \
  -e repo_path=/path/to/shakenfist \
  -e client_repo_path=/path/to/client-python
```

The first play then builds both wheels, distributes them to the host's `/tmp`,
and points `server_package` / `client_package` at the distributed wheels.

## What the deploy does (play order)

The shared playbook runs, in order:

1. (optional) build local wheels and distribute them;
2. validate mesh MTUs, then compute the capability flags, cluster-wide
   values (`network_node_ip`, `mariadb_gateway_hosts`, `max_hypervisor_mtu`,
   `all_mesh_hosts`) and per-host resource reservation defaults
   (`node_ram_reservation_gb`, `node_cpu_reservation_threads`,
   `node_disk_reservation_gb`);
3. validate KVM on the hypervisor;
4. bootstrap the internal CA and distribute per-host SPICE certificates;
5. bootstrap + configure the node (writes `/etc/sf/config` with the direct
   MariaDB block, because this box is the database tier);
6. `sf-ctl ensure-mariadb-schema` (once);
7. write the cluster config (`AUTH_SECRET_SEED`, `MAX_HYPERVISOR_MTU`,
   `DNS_SERVER`, ...) and the system namespace key
   **before** registering, so `sf-database`'s `verify-config` gate passes;
8. register the node and start the daemons;
9. run the sanity checks (sf-api/sf-queues active, API returns 401).

After it finishes, `sf-client` on the box is configured against the cluster's
own API and you can start creating instances.
