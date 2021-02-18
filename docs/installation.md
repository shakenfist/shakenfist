---
title: Installation
---
# Installing Shaken Fist

This guide will assume that you want to install Shaken Fist on a single local machine (that is, the one you're going to run ansible on). This is by no means the only installation option, but is the most simple to get people started.

Shaken Fist only supports Ubuntu 18.04 or later, so if you're running on localhost that implies that you must be running a recent Ubuntu on your development machine. Note as well that the deployer installs software and changes the configuration of your networking, so be careful when running it on machines you are fond of.

Note that we used to recommend deployers run the installer from git, but we've outgrown that approach. If you
see that mentioned in the documentation, you are likely reading outdated guides.

First install some dependancies:

```bash
sudo apt-get update
sudo apt-get -y dist-upgrade
sudo apt-get -y install ansible tox pwgen build-essential python3-dev python3-wheel \
    python3-pip curl ansible vim git pwgen
sudo ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform \
    andrewrothstein.go
```

And then manually upgrade pip:

```
sudo pip3 install -U pip
sudo apt-get remove -y python3-pip
```

Next install your desired Shaken Fist pip package. The default should be the latest release.

```
sudo pip install -U shakenfist shakenfist_client
```

And then run the installer. We describe the correct invocation for a local development environment in the section below.
## Local Development

Shaken Fist uses ansible as its installer, with terraform to bring up cloud resources. Because we're going to install Shaken Fist on localhost, there isn't much terraform in this example. Installation is run by a simple wrapper called "install.sh".

We also make the assumption that developer laptops move around more than servers. In a traditional install we detect the primary NIC of the machine and then use that to build VXLAN meshes. For localhost single node deploys we instead create a bridge called "brsf" and then use that as our primary NIC. This means your machine can move around and have different routes to the internet over time, but it also means its fiddly to convert a localhost install into a real production cluster. Please only use localhost installs for development purposes.

```
sudo CLOUD=localhost /usr/local/share/shakenfist/installer/install.sh
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

## Real world cluster deployments

It is probably best to start with a localhost deployment first to become familiar with Shaken Fist. From that, you can build out your real world deployment. Deployments are based on Hashicorp terraform
configuration found in deploy/ansible/terraform/<cloud> where "cloud" is one of the following at the time of writing:

* aws: Amazon EC2 bare metal (3 node cluster)
* aws-single-node: Amazon EC2 bare metal (1 node cluster), note that the CI tests will not pass on a single node cluster
* gcp: Google cloud, using nested virtualization (see additional steps below). **This deployment option is not for production use because of its lack of bare metal support and low MTUs affecting virtual network performance. We mainly use it for CI testing.**
* metal: Baremetal provisioned outside of terraform
* openstack: OpenStack, using nested virtualization
* shakenfist: Shaken Fist can self host, this is useful for CI for example

### Common first steps

```bash
sudo apt-get install ansible tox pwgen build-essential python3-dev python3-wheel curl
git clone https://github.com/shakenfist/deploy
ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform andrewrothstein.go
```

### Google Cloud additional first steps

On Google Cloud, you need to enable nested virt first:

```bash
# Create an image with nested virt enabled (only once)
gcloud compute disks create sf-source-disk --image-project ubuntu-os-cloud \
    --image-family ubuntu-1804-lts --zone us-central1-b
gcloud compute images create sf-image \
  --source-disk sf-source-disk --source-disk-zone us-central1-b \
  --licenses "https://compute.googleapis.com/compute/v1/projects/vm-options/global/licenses/enable-vmx"
```

Please note that the gcp-xl cloud is a special definition used for larger scale CI testing. You're welcome to use it, but it does assume that the node performing the deployment is a Google cloud instance.

### VMWare ESXi additional first steps

The "metal" installation option can be used to create a test cluster on VMWare ESXi hypervisors.

Virtual machines hosted under ESXi need two CPU options enabled.

```
Hardware virtualization:
    Expose hardware assisted virtualization to the guest OS

Performance counters:
    Enable virtualized CPU performance counters
```

### Other caveats

The installer will also enforce the following sanity checks:

* That KVM will operate on your machines. This is generally fine unless you're using virtual machines at which point nested virtualization needs to be enabled.
* That your network interface MTU is greater than 2,000 bytes. This is required because the VXLAN mesh our virtual networks use add overhead to packets and a standard MTU of 1500 bytes for the physical network will result in packets being fragmented too frequently on the virtual networks. You can set a higher MTU if you desire, I generally select 9,000 bytes.

### Deployment

Each deployment takes slight different arguments. I tend to write a small shell script to wrap these up for
convenience. Here's an example for a baremetal deployment:

```bash
$ cat ~/sf-deploys/cbr-remote.sh 
#!/bin/bash

export CLOUD=metal
export ADMIN_PASSWORD="p4ssw0rd"
export FLOATING_IP_BLOCK="192.168.20.0/24"
export BOOTDELAY=0

# Tests are not currently safe for clusters whose data you are fond of
export SKIP_SF_TESTS=1

./deployandtest.sh
```

I then execute that script from the deploy/ansible directory to do a deployment or upgrade. Note that running the
CI suite will destroy the contents of your cloud so be sure to use SKIP_SF_TESTS if you're upgrading a cluster
with real users.

### Deployment variables

| Option | Terraform definition | Description |
|--------|----------------------|-------------|
| MODE | All | Options are "deploy" (the default) or "hotfix". Deploy performs a full install, whereas hotfix skips steps to try and push only changes to Shaken Fist code as quickly as possible. |
| RELEASE | All | Which release to run. This can be a version number from pypi, the keyword "git:" for the current state of master, or "git:branch" to use a branch called "branch" from your local git. |
| CLOUD | All | The terraform definition to use |
| ADMIN_PASSWORD | All | The admin password for the cloud once installed |
| FLOATING_IP_BLOCK | All | The IP range to use for the floating network |
| BOOTDELAY | All | How long to wait for terraform deployed instances to boot before continuing with install, in minutes |
| SKIP_SF_TEST | All | Set to 1 to skip running destructive testing of the cloud |
| KSM_ENABLED | All | Set to 1 to enable KSM, 0 to disable |
| DEPLOY_NAME | All | The name of the deployment to use as an external label for prometheus |
| AWS_REGION | aws, aws-single-node | The AWS region to deploy in |
| AWS_AVAILABILITY_ZONE | aws, aws-single-node | The AWS availability zone to deploy in |
| AWS_VPC_ID | aws, aws-single-node | The AWS VPC to use |
| AWS_SSH_KEY_NAME | aws, aws-single-node | The name of an SSH key in the AWS region to use for ansible |
| GCP_PROJECT | gcp, gcp-xl | The GCP project id to deploy in |
| GCP_SSH_KEY_FILENAME | gcp, gcp-xl | The path to a ssh private key file to use for authentication. It is assumed that the public key is at ```${GCP_SSH_KEY_FILENAME}.pub```. (optional, only required if not using gcloud). |
| GCP_SSH_USER | gcp, gcp-xl | The username to add the GCP_SSH_KEY to. (optional, only used if GCPSSH_KEY_FILENAME is set). |
| OS_SSH_KEY_NAME | openstack | The name of a SSH key in the OpenStack cloud to use for ansible |
| OS_FLAVOR_NAME | openstack | The OpenStack flavor to use for instances |
| OS_EXTERNAL_NET_NAME | openstack | The UUID of an OpenStack network with internet access |
| METAL_IP_SF1 | metal | The IP address of a baremetal machine |
| METAL_IP_SF2 | metal | The IP address of a baremetal machine |
| METAL_IP_SF3 | metal | The IP address of a baremetal machine |
| SHAKENFIST_KEY | shakenfist | The authentication key for a user on the shakenfist cluster to deploy in |
| SHAKENFIST_SSH_KEY | shakenfist | The _path_ to a SSH key to use for ansible |
