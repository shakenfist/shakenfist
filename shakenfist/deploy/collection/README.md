# Shaken Fist Ansible collection (`shakenfist.shakenfist`)

This collection deploys [Shaken Fist](https://shakenfist.com/) — an
opinionated, minimal cloud orchestration platform for VM and network
management — onto hosts you already manage with Ansible.

The guiding principle is that Shaken Fist deploys its `sf-*` daemons on the
hosts you tell it about, against infrastructure (MariaDB, the network node)
whose addresses you tell it. The roles express *what a node runs* as plain
role variables; **your** playbook maps your inventory groups onto those
variables. No role in this collection reads an inventory group name or
another host's facts, so the roles compose cleanly with any inventory layout.

## Roles

| Role | Purpose |
|------|---------|
| `shakenfist.shakenfist.node` | Core per-node setup: OS packages, `/etc/sf/config`, `sfrc`, the global auth file, all `sf-*` systemd units, and registration of the node and its daemons. Folds in the database capability: when `node_is_database_node` is true it also writes, registers and starts `sf-database`. Has `bootstrap`, `config` and `register` entry points (run them as separate plays to order database-tier hosts first). The `bootstrap` entry point creates the `/srv/shakenfist` virtualenv and installs the `shakenfist` server and client packages (override `server_package`/`client_package`/`pip_extra` to install local wheels for local/CI). Re-running the role is **restart-on-change**: it only restarts a node's daemons when the installed code, `/etc/sf/config`, or a systemd unit actually changed (see [Idempotence and restart-on-change](#idempotence-and-restart-on-change)). |
| `shakenfist.shakenfist.hypervisor` | Hypervisor host preparation: nested KVM detection/enable, KSM, `vhost_vsock`, SPICE TLS, and the libvirt AppArmor/config tweaks. Apply only to hosts where `node_is_hypervisor` is true. |
| `shakenfist.shakenfist.network` | Network node preparation: removes the distro `dnsmasq` unit, installs the DHCP/DNS templates, enables IPv4 forwarding, and validates the mesh interface MTU. Apply only to hosts where `node_is_network_node` is true. |
| `shakenfist.shakenfist.internal_ca` | Internal certificate authority: generates a CA on the control node, issues a per-host SPICE TLS certificate, and distributes the certificates to each host. |

## Idempotence and restart-on-change

Re-running the `node` role is safe to do routinely — for example from a daily
`manage.yml`-style rotation that keeps every node on the tip of a branch. The
role restarts a node's `sf-*` daemons **only when something they depend on
actually changed**, so a redeploy on a day nothing moved leaves the running
cluster completely undisturbed. A daemon is restarted when any of the following
changed on its node:

* **The installed Shaken Fist code.** The `bootstrap` entry point hashes the
  *installed* `shakenfist` / `shakenfist_client` package files into a marker
  (`/srv/shakenfist/.deployed-code.sha256`) after each install and restarts if
  the hash moved. It deliberately hashes the installed files rather than the
  wheel: the wheels are rebuilt from source every deploy and embed build
  timestamps, so their bytes differ every run even when the source did not,
  whereas identical source always installs byte-identical files. This detects a
  real code change on a branch bump, on a same-version dirty rebuild (CI), and
  on a first install (marker absent), while staying quiet on a genuine no-op.
* **`/etc/sf/config`.** It is the daemons' shared systemd `EnvironmentFile`, so
  a change to it restarts every daemon on the node. The config is re-rendered
  every run and changes when a value moved or the node's role did (most
  importantly joining or leaving the database tier).
* **A systemd unit file.** A rewritten `sf-*.service`/`sf.target`, or removal of
  an obsolete unit, restarts the affected daemons. `systemctl daemon-reload`
  runs in the same play that writes the units, before any restart.

The decision is per node and is logged during `register` as a
`restart_needed=… (code=… config=… units=…)` line. Granularity is
node-wide: because the code and config are shared by every daemon on the host,
any change restarts all of that node's daemons rather than a subset. On a
database-tier node the restart still honours the ordering the role guarantees —
`sf-database` first, then a wait until its gRPC gateway (port 13005) is
accepting connections, then the remaining daemons — so a rolling redeploy of a
redundant database tier stays outage-free. Registration of the node and its
daemons is idempotent and runs every time regardless, which is what heals a
node whose database rows have drifted.

## Modules

The collection ships four native Ansible modules (under
`plugins/modules/`) for managing Shaken Fist resources from a playbook. They
import the `shakenfist_client` SDK and call the Shaken Fist REST API directly
— they do **not** shell out to `sf-client`.

| Module | Purpose |
|--------|---------|
| `shakenfist.shakenfist.sf_namespace` | Idempotently create or delete a namespace (`name`, `state`). |
| `shakenfist.shakenfist.sf_network` | Idempotently create or delete a network (`name`/`uuid`, `netblock`, `nat`, `dhcp`, `dns`, `state`). A changed specification deletes and recreates the network. |
| `shakenfist.shakenfist.sf_instance` | Idempotently create, replace or delete an instance (`name`/`uuid`, `cpu`, `ram`, `disks`/`diskspecs`, `networks`/`networkspecs`, `metadata`, `await`, `state`). A changed specification deletes and recreates the instance. |
| `shakenfist.shakenfist.sf_snapshot` | Snapshot an instance's disks (optionally updating a label) or delete a snapshot artifact (`instance_uuid`/`uuid`, `all`, `label`, `state`). |

Every module accepts optional `api_url`, `namespace` and `key` connection
parameters. When all three are supplied they are used verbatim; when omitted,
the module auto-discovers credentials from the environment and
`sfrc`/`~/.shakenfist`/`/etc/sf/shakenfist.json` exactly like the `sf-client`
CLI. Each module returns `changed`, `failed`, a `meta` object describing the
resource, and a `log` list of progress messages for debugging.

## Requirements

The modules require the Shaken Fist client SDK on the Ansible control node:

```bash
pip install shakenfist-client
```

(or `pip install -r requirements.txt` from the collection root). The control
node never needs the Shaken Fist server package. The roles additionally
require `ansible >= 2.15` (see `meta/runtime.yml`).

The `internal_ca` role generates certificates on the control node with
`certtool` from the `gnutls-bin` package (Debian/Ubuntu). The role installs it
via apt when its control-node tasks run with root; rootless deploys must
install it beforehand.

## Consuming the collection

Install the published collection on your Ansible control node:

```bash
ansible-galaxy collection install shakenfist.shakenfist
```

Then reference the roles by their fully-qualified collection name (FQCN) from
your own playbook, gating the capability roles on the relevant variables:

```yaml
- hosts: all
  become: true
  roles:
    - role: shakenfist.shakenfist.node

- hosts: hypervisors
  become: true
  roles:
    - role: shakenfist.shakenfist.hypervisor

- hosts: network_node
  become: true
  roles:
    - role: shakenfist.shakenfist.network

- hosts: all
  become: true
  roles:
    - role: shakenfist.shakenfist.internal_ca
```

Your playbook is responsible for computing each role's input variables (for
example the per-node capability flags and any cluster-wide values) from your
inventory and passing them in. Example playbooks that demonstrate this mapping
ship under `examples/` in the Shaken Fist repository.

See each role's `meta/argument_specs.yml` for its full, documented variable
set.

## License

Apache-2.0. Copyright 2019 Michael Still and contributors.
