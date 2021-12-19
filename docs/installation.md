---
title: Installation
---
# Installing Shaken Fist

The purpose of this guide is to walk you through a Shaken Fist installation. Shaken Fist will work just fine on a single machine, although its also happy to run on clusters of machines. We'll discuss the general guidance for install options as we go.

Shaken Fist only supports Ubuntu 20.04, Debian 10, and Debian 11, so if you're running on localhost that implies that you must be running a recent Ubuntu or Debian on your development machine. Note as well that the deployer installs software and changes the configuration of your networking, so be careful when running it on machines you are fond of. This documentation was most recently tested against Debian 11, in November 2021. Bug reports are welcome if you have any issues, and may be filed at https://github.com/shakenfist/shakenfist/issues

Each machine in the cluster should match this description:

* Have virtualization extensions enabled in the BIOS.
* Have jumbo frames enabled on the switch for the "mesh interface" for installations of more than one machine. Shaken Fist can optionally run internal traffic such as etcd and virtual network meshes on a separate interface to traffic egressing the cluster. Whichever interface you specify as being used for virtual network mesh traffic must have jumbo frames enabled for the virtual networks to function correctly.
* Have at least 1 gigabit connectivity on the "mesh interface". This is a requirement of etcd.
* Have a cloudadmin account setup with passwordless sudo, and a ssh key in its authorized_keys file. This is an ansible requirement, although the exact username is configurable in the SSH_USER variable.

Note that we used to recommend deployers run the installer from git, but we've outgrown that approach. If you see that mentioned in the documentation, you are likely reading outdated guides.

Finally, Shaken Fist is run as root, so we install it as root as well. Make sure you're installing as the right user as you follow these instructions!

First install some dependencies:

```bash
sudo apt-get update
sudo apt-get -y dist-upgrade
sudo apt-get -y install ansible git tox build-essential python3-dev python3-wheel \
    python3-pip python3-venv
```

On Ubuntu 20.04, you need a more modern version of Ansible than what is packaged. Refer to https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html#installing-ansible-on-ubuntu for instructions on how to setup the Ansible PPA.

We require that Shaken Fist be installed into a venv at /srv/shakenfist/venv on each Shaken Fist machine. This is done outside of the installer process. Create that venv now:

```
sudo mkdir -p /srv/shakenfist/venv
sudo python3 -mvenv --system-site-packages /srv/shakenfist/venv
```

Next install your desired Shaken Fist pip package. The default should be the latest release.

```
/srv/shakenfist/venv/bin/pip install -U shakenfist shakenfist_client
```

Because we're fancy, we should also create a symlink to the `sf-client` command so its easy to use without arguing with the virtual environment:

```
sudo ln -s /srv/shakenfist/venv/bin/sf-client /usr/local/bin/sf-client
```

We need to install required ansible-galaxy roles, which are described by a requirements file packaged with the server package. Do that like this:

```
sudo ansible-galaxy install -r /srv/shakenfist/venv/share/shakenfist/installer/requirements.yml
```

And then run the installer. Generally I create a small shell script called `sf-deploy.sh`, which contains the details of the installation. For my home cluster, it looks like this:

```
#!/bin/bash

export ADMIN_PASSWORD=...a...password...
export FLOATING_IP_BLOCK="192.168.10.0/24"
export DEPLOY_NAME="bonkerslab"
export SSH_USER="cloudadmin"
export SSH_KEY_FILENAME="/root/.ssh/id_rsa"

export KSM_ENABLED=1

# Topology is in JSON
read -r -d '' TOPOLOGY <<'EOF'
[
    {
        "name": "sf-1",
        "node_egress_ip": "10.0.0.1",
        "node_egress_nic": "eth0",
        "node_mesh_ip": "10.0.1.1",
        "node_mesh_nic": "eth1",
        "etcd_master": true,
        "primary_node": true,
        "network_node": true,
        "hypervisor": true,
        #!/bin/bash

ansible-galaxy install -r /srv/shakenfist/venv/share/shakenfist/installer/requirements.yml

=export ADMIN_PASSWORD=engeeF1o
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

Not every node needs to be an etcd_master. I'd select three in most situations. One node must be marked as the primary node, and one must be marked as the network node. It is not currently supported having more than one of each of those node types.

* The primary node runs an apache load balancer across the API servers in the cluster, and therefore needs to be accessable to your users on HTTP and HTTPS.
* The network node is the ingress and egress point for all virtual networks, and is where floating IPs live, so it needs to be setup as the gateway fro your floating IP block.

Some of the considerations here can be subtle. Please reach out if you need a hand.

For a single machine installation, `sf-deploy.sh` looks like this:

```
#!/bin/bash

export ADMIN_PASSWORD=engeeF1o
export FLOATING_IP_BLOCK="192.168.10.0/24"
export BOOTDELAY=0
export DEPLOY_NAME="bonkerslab"

export KSM_ENABLED=1

# Topology is in JSON
read -r -d '' TOPOLOGY <<'EOF'
[
  {
    "name": "localhost",
    "node_egress_ip": "127.0.0.1",
    "node_egress_nic": "lo",
    "node_mesh_ip": "127.0.0.1",
    "node_mesh_nic": "lo",
    "primary_node": true,
    "network_node": true,
    "etcd_master": true,
    "hypervisor": true,
    "api_url": "http://localhost:13000"
  },
]
EOF
export TOPOLOGY

/srv/shakenfist/venv/share/shakenfist/installer/install
```

And then we can run the installer:

```
chmod +x sf-deploy.sh
sudo ./sf-deploy.sh
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