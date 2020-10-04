Shaken Fist: Opinionated to the point of being impolite
=======================================================

## What is this?

Shaken Fist is a deliberately minimal cloud. You can read more about Shaken Fist at https://github.com/shakenfist/shakenfist
and shakenfist.com -- this repository is the deployment and CI tooling for the project, and therefore not a great place to
start your journey.

## Installation

Build an acceptable deployment, noting that only Ubuntu is supported. Deployments are based on Hashicorp terraform
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
| AWS_REGION | aws, aws-single-node | The AWS region to deploy in |
| AWS_AVAILABILITY_ZONE | aws, aws-single-node | The AWS availability zone to deploy in |
| AWS_VPC_ID | aws, aws-single-node | The AWS VPC to use |
| AWS_SSH_KEY_NAME | aws, aws-single-node | The name of an SSH key in the AWS region to use for ansible |
| GCP_PROJECT | gcp | The GCP project id to deploy in |
| OS_SSH_KEY_NAME | openstack | The name of a SSH key in the OpenStack cloud to use for ansible |
| OS_FLAVOR_NAME | openstack | The OpenStack flavor to use for instances |
| OS_EXTERNAL_NET_NAME | openstack | The UUID of an OpenStack network with internet access |
| METAL_IP_SF1 | metal | The IP address of a baremetal machine |
| METAL_IP_SF2 | metal | The IP address of a baremetal machine |
| METAL_IP_SF3 | metal | The IP address of a baremetal machine |
| SHAKENFIST_KEY | shakenfist | The authentication key for a user on the shakenfist cluster to deploy in |
| SHAKENFIST_SSH_KEY | shakenfist | The _path_ to a SSH key to use for ansible |
