---
title: Installation
---
# Installing Shaken Fist

The purpose of this guide is to walk you through a Shaken Fist installation.
Shaken Fist will work just fine on a single machine, although it is also happy
to run on clusters of machines. We'll discuss the general guidance for install
options as we go.

Shaken Fist is deployed with the `shakenfist.shakenfist` Ansible collection:
you write an inventory describing your machines, set a handful of variables,
and run a playbook. Ready-to-use example inventories and playbooks ship in the
[`examples/`](https://github.com/shakenfist/shakenfist/tree/develop/examples)
directory of the repository — `examples/single-node/` is the recommended
quickstart for a single box, and `examples/cluster/` shows a multi-node
cluster.

[//]: # (Note that if you change the list of supported operating systems you must also update python_verison.md in this directory)

Shaken Fist only supports Ubuntu 24.04, and Debian 12, so if you're running on
localhost that implies that you must be running a recent Ubuntu or Debian on
your development machine. Note as well that the deployer installs software and
changes the configuration of your networking, so be careful when running it on
machines you are fond of. Bug reports are welcome if you have any issues, and
may be filed at https://github.com/shakenfist/shakenfist/issues

???+ note
    Debian 10, Debian 11 and Ubuntu 22.04 support were dropped in v0.8, as supporting
    older versions of ansible became burdensome. Debian 12 support was added
    in v0.8.

    This means the minimum supported Python version for Shaken Fist is now 3.11.

## Prerequisites

Each machine in the cluster should match this description:

* Runs a supported operating system (see above) and is reachable over ssh as
  a user that can `become` root. This is an ansible requirement; the exact
  username is up to you and is configured in your inventory like any other
  ansible deployment.
* Has virtualization extensions enabled in the BIOS.
* Has jumbo frames enabled on the switch for the "mesh interface" for
  installations of more than one machine. Shaken Fist can optionally run
  internal traffic such as database access and virtual network meshes on a
  separate interface to traffic egressing the cluster. Whichever interface you
  specify as being used for virtual network mesh traffic must have jumbo
  frames enabled for the virtual networks to function correctly. The deploy
  validates that the mesh interface MTU is greater than 2,000 bytes, because
  the VXLAN mesh our virtual networks use adds overhead to packets and a
  standard MTU of 1,500 bytes would result in fragmentation. I generally
  select 9,000 bytes.
* Has at least 1 gigabit connectivity on the "mesh interface".
* Has the iptables connection tracking match (`xt_conntrack`, what
  `iptables -m conntrack` loads) available if it will be a network node. It
  is present in any stock Ubuntu or Debian kernel, so this only bites a
  custom or heavily stripped kernel. A network node without it cannot bring
  up a NAT providing virtual network at all, rather than degrading -- see
  the floating IP discussion in [the networking overview](networking/overview.md)
  for why the rule needs it.

Your ansible control node needs `ansible-core >= 2.15`.

## Deploying against your own infrastructure

Shaken Fist deploys its daemons onto the hosts you tell it about, against
infrastructure whose addresses you tell it. It deliberately does not install
or manage that infrastructure for you:

* **MariaDB**: Shaken Fist does not install or manage its database server.
  Provision a MariaDB 10.11.0+ instance reachable from the database-tier
  nodes before deploying. The repository ships
  `tools/bootstrap-mariadb.sql` to create the user, database and grants, and
  `examples/mariadb-tuning.cnf` as optional starting-point tuning. See
  [Database](database.md) for the complete setup workflow and compatibility
  requirements.
* **A load balancer** (multi-node installations): Shaken Fist does not
  install a load balancer. You must place your own reverse proxy or load
  balancer in front of the cluster's `sf-api` daemons, which listen on port
  13000. See [Load Balancing](load_balancing.md) for details.
* **(Optional) Loki**: structured logs can be shipped to an
  operator-provided Loki. See [Logging](logging.md).

## Install the collection

Install the published collection on your ansible control node:

```bash
ansible-galaxy collection install shakenfist.shakenfist
```

To work from a git checkout of the repository instead, install the collection
from source:

```bash
ansible-galaxy collection install ./shakenfist/deploy/collection
```

(`tools/build-collection.py` builds a distributable collection tarball from
the same source, which is how the published collection is made.)

## Write an inventory

The example playbooks map inventory groups onto the collection's role
variables. The groups are:

* `allsf` — every Shaken Fist host;
* `hypervisors` — hosts that run instances;
* `network_node` — the single network node, which is the ingress and egress
  point for all virtual networks, and is where floating IPs live — so it
  needs to be set up as the gateway for your floating IP block;
* `database_node` — the database tier: hosts that run `sf-database`, the
  gRPC gateway between the cluster and your MariaDB. The legacy `etcd_master`
  name for this group is still accepted, with a deprecation warning, and is
  removed in the next release.

A host may belong to several capability groups. Not every node needs to be in
the database tier — one is fine for small clusters, and you can add more
hosts to the group later for higher availability. It is not currently
supported to have more than one network node.

The deploy tolerates a hypervisor that is temporarily absent: a preflight step
probes every host, quarantines any that do not answer, and deploys around them,
so a single powered-off or failed hypervisor no longer aborts the run for the
rest of the cluster. It reports which nodes it skipped, and you re-run once they
return — you do not have to edit the inventory. The network node and the
database tier are not optional, so the deploy stops with a clear error if either
is unreachable.

Per-host identity lives on each host entry: `node_name`, `node_egress_ip`,
`node_egress_nic`, `node_mesh_ip` and `node_mesh_nic`. A three node example
(from `examples/cluster/inventory.yaml`) looks like this:

```yaml
all:
  children:
    allsf:
      hosts:
        sf-1:
          ansible_host: 10.0.0.1
          node_name: sf-1
          node_egress_ip: 192.168.1.1
          node_egress_nic: eth0
          node_mesh_ip: 10.0.0.1
          node_mesh_nic: eth1
        sf-2:
          ansible_host: 10.0.0.2
          node_name: sf-2
          node_egress_ip: 192.168.1.2
          node_egress_nic: eth0
          node_mesh_ip: 10.0.0.2
          node_mesh_nic: eth1
        sf-3:
          ansible_host: 10.0.0.3
          node_name: sf-3
          node_egress_ip: 192.168.1.3
          node_egress_nic: eth0
          node_mesh_ip: 10.0.0.3
          node_mesh_nic: eth1

    network_node:
      hosts:
        sf-1:

    database_node:
      hosts:
        sf-1:

    hypervisors:
      hosts:
        sf-2:
        sf-3:
```

The mesh network carries east/west traffic between nodes, so it need not be
routable from anywhere else. `node_egress_ip` is the north/south address, and
it is the hypervisor address that users are given in `virt-viewer` files from
the `vdiconsolehelper` API call. Set it to an IPv4 address that console clients
can reach, and make sure the hypervisor's VDI port range (the `vdi` and
`vdi_tls` ports) is open on that address from wherever those clients run.
Firewall rules written when console clients reached hypervisors over the mesh
may cover only that network. SPICE and VNC listen on IPv4 only, so an IPv6 address is not
used. If it is left unset, set to a loopback address such as the deployer's
default of `127.0.0.1`, or set to anything else a client cannot dial (an
unspecified, link-local or multicast address, or a value that is not an IP
address), those files fall back to the node's mesh IP. Values other than unset
and loopback are logged as a warning when that happens.

For a single machine, put `localhost` in every group — see
`examples/single-node/inventory.yaml` for exactly that.

## Set the deployment variables

Cluster-wide variables and secrets live in `group_vars/all.yml` beside your
inventory. The examples ship a commented template; the important variables
are:

| Variable | Description |
|----------|-------------|
| `deploy_name` | The name of the deployment, used as an external label for prometheus. |
| `api_url` | The URL clients should use to reach the API. For a multi-node cluster this is the address of your own load balancer, which must proxy to the cluster's `sf-api` daemons as described on the [Load Balancing](load_balancing.md) page. |
| `auth_secret` | Seeds `AUTH_SECRET_SEED`, which signs JWT authentication tokens. Treat as a secret and change it from the example value. |
| `system_key` | The authentication key for the "system" namespace, written into `sfrc` on each node. Treat as a secret. |
| `floating_network_ipblock` | The IP range to use for the floating network. |
| `dns_server` | The DNS server to configure instances with via DHCP. Defaults to 8.8.8.8. |
| `http_proxy` | A URL for a HTTP proxy to use for image downloads, for example `http://localhost:3128`. Optional. |
| `mariadb_host`, `mariadb_port`, `mariadb_user`, `mariadb_password`, `mariadb_database` | Connection details for your MariaDB server. Only database-tier nodes render these into `/etc/sf/config`. They must match what you provisioned with `tools/bootstrap-mariadb.sql`. |
| `server_package`, `client_package` | The pip package references to install; default to the released `shakenfist` and `shakenfist-client` on PyPI. |
| `loki_base_url`, `loki_tenant`, `loki_auth_header` | Optional log shipping to an operator-provided Loki. See [Logging](logging.md). |
| `kerbside_url` | Base URL of an operator-deployed Kerbside VDI console proxy. Optional; empty leaves the proxied-console integration off. See the [VDI console tokens operator guide](vdi_console_tokens.md). |
| `kerbside_token_duration` | Lifetime in seconds of a minted console token. Defaults to 300. Rendered only when `kerbside_url` is set. |
| `kerbside_system_key` | The key Kerbside authenticates to Shaken Fist with, minted as the `kerbside` key in the system namespace on every deploy. Optional; requires `kerbside_url`, must differ from `system_key`, and must be at least 16 characters. Treat as a secret. |
| `kerbside_public_fqdn` | Only to deploy Kerbside onto an optional `kerbside` inventory group. The name SPICE clients use to reach the proxy; also the proxy certificate's CN. An IPv4 or IPv6 address is given an IP SAN, and a name a DNS SAN. |
| `kerbside_sql_url` | Only with a `kerbside` group. The URL of Kerbside's own MySQL or MariaDB database, for example `mysql://kerbside:PASSWORD@db.example.com/kerbside`. Treat as a secret. |
| `kerbside_auth_secret_seed` | Only with a `kerbside` group. Seeds Kerbside's token signing. At least 32 characters, and not Kerbside's placeholder `~~unconfigured~~`. Treat as a secret. |
| `kerbside_package` | Only with a `kerbside` group. Defaults to `kerbside>=0.7.0`; until 0.7.0 is released, set a version or a wheel built from a git checkout. |
| `extra_config` | A JSON list of additional cluster configuration settings, for example `[{"name": "INCLUDE_TRACEBACKS", "value": "1"}]`. Optional. |

To offer users proxied graphical consoles via Kerbside, set `kerbside_url`
and, optionally, `kerbside_token_duration` and `kerbside_system_key`. The
deploy then ensures the console token signing key exists before any daemon
restarts, and mints the Kerbside credential if you set `kerbside_system_key`.
The deploy stops if a `KERBSIDE_URL` or `KERBSIDE_TOKEN_DURATION`
`cluster_config` row differs from its variable; see the
[VDI console tokens operator guide](vdi_console_tokens.md) for the fix and for
key rotation. The integration stays disabled while `kerbside_url` is unset.

To have the deploy install Kerbside too, add hosts to an optional `kerbside`
inventory group. Members may be Shaken Fist nodes or dedicated hosts. Set
`kerbside_url`, `kerbside_system_key`, `kerbside_public_fqdn`,
`kerbside_sql_url` and `kerbside_auth_secret_seed`. `kerbside_url` and
`kerbside_system_key` must be visible to both the Shaken Fist and the Kerbside
hosts (put them in `group_vars/all`), and every Kerbside port must be below
30000 and distinct. On a Kerbside host which is also a Shaken Fist node, no
Kerbside port may be 13000, 13001, 13005, 13006 or 13007, which Shaken Fist's
daemons listen on. On a Kerbside host which is not a Shaken Fist node,
`api_url` must not be a loopback address. Each Kerbside host is checked with its own
variables, so a `host_vars` override is checked too. Kerbside's certificate is issued from the
deployment's internal CA unless you set all three of `kerbside_proxy_cert_path`,
`kerbside_proxy_key_path` and `kerbside_cacert_path`.

Kerbside needs a MySQL or MariaDB database of its own (it does not support
sqlite), which you create before deploying: a `kerbside` database and a user
with all privileges on it.

```sql
CREATE DATABASE kerbside;
CREATE USER 'kerbside'@'%' IDENTIFIED BY 'PASSWORD';
GRANT ALL PRIVILEGES ON kerbside.* TO 'kerbside'@'%';
```

The deploy runs Kerbside's migrations and then waits, for up to about three
minutes, for Kerbside to fetch the console token signing key. Without a
`kerbside` group ansible prints `Could not match supplied host pattern,
ignoring: kerbside`, which is harmless; an empty `kerbside:` group in the
inventory silences it. Kerbside's admin login is Keystone-only and the
deploy configures no Keystone, so its admin UI is unavailable; consoles work. See the
[collection README](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/deploy/collection/README.md#kerbside)
for the details.

## Run the playbook

The examples share a single playbook, `examples/_shared/site.yml`, which maps
your inventory groups onto the collection's roles, computes the cluster-wide
values, runs `sf-ctl ensure-mariadb-schema` against your MariaDB, seeds the
cluster configuration, and starts the daemons in the correct order. Each
example's `site.yml` is a one-line wrapper importing it. Run it as a user
that can `become` root on the targets:

```bash
ansible-playbook -i examples/single-node/inventory.yaml examples/single-node/site.yml
```

or for the multi-node example:

```bash
ansible-playbook -i examples/cluster/inventory.yaml examples/cluster/site.yml
```

You can copy the example directory and edit it, or write your own playbook
against the collection's roles — the roles read only plain variables, so
they compose with any inventory layout. See the
[collection README](https://github.com/shakenfist/shakenfist/tree/develop/shakenfist/deploy/collection)
and each role's `meta/argument_specs.yml` for the full variable set.

To deploy from a local git checkout instead of released PyPI packages (for
development or CI), add:

```bash
ansible-playbook -i examples/single-node/inventory.yaml examples/single-node/site.yml \
  -e sf_build_local_wheels=true \
  -e repo_path=/path/to/shakenfist \
  -e client_repo_path=/path/to/client-python
```

## Post-deploy checks

The playbook finishes with its own sanity checks (`sf-api` and `sf-queues`
active, the API answering). To confirm the cluster is healthy yourself,
source the authentication file the deploy wrote and list the nodes:

```bash
. /etc/sf/sfrc
sf-client node list
```

Every node you deployed should be listed, in the `created` state.

## Your first instance

Before you can start your first instance you'll need to authenticate to Shaken Fist, and create a network. Shaken Fist's python api client (as used by the command line client) looks for authentication details in the following locations:

* Command line flags
* Environment variables (prefixed with **SHAKENFIST_**)
* **~/.shakenfist**, a JSON formatted configuration file
* **/etc/sf/shakenfist.json**, the same file as above, but global

The deploy creates **/etc/sf/sfrc** on each node, which sets the required environment variables to authenticate.
It is customized per installation, setting the following variables:

* **SHAKENFIST_NAMESPACE**, the namespace to create resources in
* **SHAKENFIST_KEY**, an authentication key for that namespace
* **SHAKENFIST_API_URL**, a URL to the Shaken Fist API server

Before interacting with Shaken Fist, we need to source the rc file.

```bash
. /etc/sf/sfrc
```

Instances must be launched attached to a network.

Create your first network:
```bash
sf-client network create mynet 192.168.42.0/24
```

You can get help for the command line client by running ```sf-client --help``. The above command creates a new network called "mynet", with the IP block 192.168.42.0/24. You will receive some descriptive output back:

```bash
$ sf-client network create mynet 192.168.42.0/24
uuid            : 16baa325-5adf-473f-8e7a-75710a822d45
name            : mynet
vxlan id        : 2
netblock        : 192.168.42.0/24
provide dhcp    : True
provide nat     : True
floating gateway: None
namespace       : system
state           : initial

Metadata:
```

The UUID is important, as that is how we will refer to the network elsewhere. Let's now create a simple first instance (you'll need to change this to use your actual network UUID):

```bash
$ sf-client instance create myvm 1 1024 -d 8@cirros -n 16baa325-5adf-473f-8e7a-75710a822d45
uuid        : c6c4ba94-ed34-497d-8964-c223489dee3e
name        : myvm
namespace   : system
cpus        : 1
memory      : 1024
disk spec   : type=disk   bus=None  size=8   base=cirros
video       : model=cirrus  memory=16384
node        : marvin
power state : on
state       : created
console port: 31839
vdi port    : 34442

ssh key     : None
user data   : None

Metadata:

Interfaces:

    uuid    : e56b3c7b-8056-4645-b5b5-1779721ff21d
    network : 16baa325-5adf-473f-8e7a-75710a822d45
    macaddr : ae:15:4d:9c:d8:c0
    order   : 0
    ipv4    : 192.168.42.76
    floating: None
    model   : virtio
```

Probably the easiest way to interact with this instance is to connect to its console port, which is the serial console of the instance over telnet. In the case above, that is available on port 31839 on localhost (my laptop is called marvin).
