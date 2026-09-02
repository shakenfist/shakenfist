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
| `extra_config` | A JSON list of additional cluster configuration settings, for example `[{"name": "INCLUDE_TRACEBACKS", "value": "1"}]`. Optional. |

To offer users proxied graphical consoles via Kerbside, set `kerbside_url`
(and optionally `KERBSIDE_TOKEN_DURATION` through `extra_config`), then
bootstrap and rotate the signing key as described in the
[VDI console tokens operator guide](vdi_console_tokens.md). This is a manual,
opt-in integration; it stays disabled while `KERBSIDE_URL` is unset.

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
