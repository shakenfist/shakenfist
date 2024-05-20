---
title: Installation
---
# Installing Shaken Fist

The purpose of this guide is to walk you through a Shaken Fist installation. Shaken Fist will work just fine on a single machine, although its also happy to run on clusters of machines. We'll discuss the general guidance for install options as we go.

[//]: # (Note that if you change the list of supported operating systems you must also update python_verison.md in this directory)

Shaken Fist only supports Ubuntu 20.04, Ubuntu 22.04, Debian 11, and Debian 12, so if you're running on localhost that implies that you must be running a recent Ubuntu or Debian on your development machine. Note as well that the deployer installs software and changes the configuration of your networking, so be careful when running it on machines you are fond of. Bug reports are welcome if you have any issues, and may be filed at https://github.com/shakenfist/shakenfist/issues

???+ note
    Debian 10 support was dropped in v0.8, as supporting older versions of ansible
    became burdensome. Ubuntu 22.04 and Debian 12 support was added in v0.8.

Each machine in the cluster should match this description:

* Have virtualization extensions enabled in the BIOS.
* Have jumbo frames enabled on the switch for the "mesh interface" for installations of more than one machine. Shaken Fist can optionally run internal traffic such as etcd and virtual network meshes on a separate interface to traffic egressing the cluster. Whichever interface you specify as being used for virtual network mesh traffic must have jumbo frames enabled for the virtual networks to function correctly.
* Have at least 1 gigabit connectivity on the "mesh interface". This is a requirement of etcd.
* Have a cloudadmin account setup with passwordless sudo, and a ssh key in its authorized_keys file. This is an ansible requirement, although the exact username is configurable in the SSH_USER variable.

We now have a fancy helper to help you install your first cluster, so let's give that a go:

```bash
curl https://raw.githubusercontent.com/shakenfist/shakenfist/develop/deploy/getsf -o getsf
chmod ugo+rx getsf
sudo ./getsf
```

This script will then walk you through the installation steps, asking questions as you go. The script leaves you with an installer configuration at `/root/sf-deploy`, which is the basis for later upgrades and cluster expansions.

You can script the answers to `getsf` by setting environment variables. For example:

```bash
export GETSF_FLOATING_BLOCK=192.168.10.0/24
export GETSF_DEPLOY_NAME=bonkerslab
export GETSF_RELEASE=pre-release
export GETSF_NODES=localhost
export GETSF_WARNING=yes
sudo --preserve-env ./getsf
```

## Notes for multi-node installations

Not every node needs to be an etcd_master. I'd select three in most situations. One node must be marked as the primary node, and one must be marked as the network node. It is not currently supported having more than one of each of those node types.

* The primary node runs an apache load balancer across the API servers in the cluster, and therefore needs to be accessable to your users on HTTP and HTTPS.
* The network node is the ingress and egress point for all virtual networks, and is where floating IPs live, so it needs to be setup as the gateway fro your floating IP block.

Some of the considerations here can be subtle. Please reach out if you need a hand.

`getsf` writes a configuration file called `sf-deploy`. For a more complicated installation, `sf-deploy` might like this:

```
#!/bin/bash

export ADMIN_PASSWORD=engeeF1o
export FLOATING_IP_BLOCK="192.168.10.0/24"
export DEPLOY_NAME="bonkerslab"
export SSH_USER="cloudadmin"
export SSH_KEY_FILENAME="/root/.ssh/id_rsa"

export KSM_ENABLED=1

# Topology is in JSON
read -r -d '' TOPOLOGY <<'EOF'
[
  {
    "name": "sf-primary",
    "node_egress_ip": "192.168.1.50",
    "node_egress_nic": "enp0s31f6",
    "node_mesh_ip": "192.168.21.50",
    "node_mesh_nic": "enp0s31f6:1",
    "primary_node": true,
    "api_url": "https://...your...install...here.com/api"
  },
  {
    "name": "sf-1",
    "node_egress_ip": "192.168.1.51",
    "node_egress_nic": "enp5s0",
    "node_mesh_ip": "192.168.21.51",
    "node_mesh_nic": "eno1",
    "etcd_master": true,
    "network_node": true,
    "hypervisor": true
  },
  {
    "name": "sf-2",
    "node_egress_ip": "192.168.1.52",
    "node_egress_nic": "enp5s0",
    "node_mesh_ip": "192.168.21.52",
    "node_mesh_nic": "eno1",
    "etcd_master": true,
    "hypervisor": true
  },
  {
    "name": "sf-3",
    "node_egress_ip": "192.168.1.53",
    "node_egress_nic": "enp5s0",
    "node_mesh_ip": "192.168.21.53",
    "node_mesh_nic": "eno1",
    "etcd_master": true,
    "hypervisor": true
  },
]
EOF
export TOPOLOGY

/srv/shakenfist/venv/share/shakenfist/installer/install
```

## Your first instance

Before you can start your first instance you'll need to authenticate to Shaken Fist, and create a network. Shaken Fist's python api client (as used by the command line client) looks for authentication details in the following locations:

* Command line flags
* Environment variables (prefixed with **SHAKENFIST_**)
* **~/.shakenfist**, a JSON formatted configuration file
* **/etc/sf/shakenfist.json**, the same file as above, but global

By default the installer creates **/etc/sf/sfrc**, which sets the required environment variables to authenticate.
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

Probably the easiest way to interact with this instance is to connect to its console port, which is the serial console of the instance over telnet. In the case above, that is available on port 31829 on localhost (my laptop is called marvin).


### Other caveats

The installer will also enforce the following sanity checks:

* That KVM will operate on your machines. This is generally fine unless you're using virtual machines at which point nested virtualization needs to be enabled.
* That your network interface MTU is greater than 2,000 bytes. This is required because the VXLAN mesh our virtual networks use add overhead to packets and a standard MTU of 1500 bytes for the physical network will result in packets being fragmented too frequently on the virtual networks. You can set a higher MTU if you desire, I generally select 9,000 bytes.

### Deployment variables

| Option | Description |
|--------|-------------|
| ADMIN_PASSWORD | The admin password for the cloud once installed |
| DNS_SERVER | The DNS server to configure instances with via DHCP. Defaults to 8.8.8.8 |
| HTTP_PROXY | A URL for a HTTP proxy to use for image downloads. For example http://localhost:3128 |
| INCLUDE_TRACEBACKS | Whether to include tracebacks in server 500 errors. Never set this to true in production! |
| FLOATING_IP_BLOCK | The IP range to use for the floating network |
| KSM_ENABLED | Set to 1 to enable KSM, 0 to disable |
| DEPLOY_NAME | The name of the deployment to use as an external label for prometheus |
| TOPOLOGY | The topology of the cluster, as described above |
| SSH_KEY_FILENAME | The path to a ssh private key file to use for authentication. It is assumed that the public key is at ```${SSH_KEY_FILENAME}.pub```. |
| SSH_USER | The username to ssh as. |
