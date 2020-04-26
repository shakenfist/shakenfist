Shaken Fist: Opinionated to the point of being impolite
=======================================================

What is this?
-------------

Shaken Fist is a deliberately minimal cloud. Its also currently incomplete, so take statements here with a grain of salt. Shaken Fist is a personal research project which came about as a reaction to the increasing complexity of OpenStack, as well as a desire to experiment with alternative approaches to solving the problems that OpenStack Compute addresses. What I really wanted was a simple API to orchestrate virtual machines, but it needed to run with minimal resource overhead and be simple to deploy. I also wanted it to always work in a predictable way.

One of the reasons OpenStack is so complicated and its behaviour varies is because it has many options to configure. The solution seemed obvious to me -- a cloud that is super opinionated. For each different functional requirement there is one option, and the simplest option is chosen where possible. Read on for some examples.

Development choices
-------------------

If there is an existing library which does a thing, we use it. OpenStack suffered from being old (and having issues with re-writes being hard), as well as licensing constraints. We just use the code that others have provided to the community. Always.

Deployment choices
------------------

libvirt is the only supported hypervisor. Instances are specified to libvirt with simple templated XML. If your local requirements are different to what's in the template, you're welcome to change the template to meet your needs. If you're template changes break things, you're also welcome to debug what went wrong for yourself.

Usage guide
===========

Clients
-------

There is a command line client called "sf-client" deployed by ansible. It talks to Shaken Fist via a REST API. There is also a python API client library at shakenfist.clients.apiclient, which is what the command line client uses to call the API. The apiclient module also serves as useful example code for how to write your own client.

The command line client can produce output in three formats: the standard "pretty" format, a mostly-csv format called "simple" (which is aimed at being easy to parse in shell scripts), and JSON. You select the output format with a flag like this:

```bash
sf-client --simple instance list
```

The default formatter is the "pretty" formatter, so you never need to specify that on the command line.

You can explore what the command line client is capable of by asking it for help:

```bash
sf-client --help
```

Networking
----------

Virtual networks / micro segmentation is provided by VXLAN meshes betwen the instances. Hypervisors are joined to a given mesh when they start their first instance on that network. DHCP services are optionally offered from a "network services" node, which is just a hypervisor node with some extra dnsmasq process. NAT is also optionally available from the network services node. If your network provides NAT, it consumes an IP address from the
floating IP pool to do so, and performs NAT in a network namespace on the network node.

You create a network on the command line like this:

```bash
sf-client network create 192.168.1.0/24 mynet
```

Where "192.168.1.0/24" is the netblock to use, and "mynet" is the name of the network. You'll get back output describing the network, including the UUID of the network, which is used in later calls.

Instances
---------

Every instance gets a config drive. Its always an ISO9660 drive. Its always the second virtual disk attached to the VM (vdb on Linux). There is no metadata server. Additionally, there is no image service -- you specify the image to use by providing a URL. That URL is cached, but can be to any HTTP server anywhere. Even better, there are no flavors. You specify what resources your instance should have at boot time and that's what you get. No more being forced into a tshirt sized description of your needs.

Instances are always cattle. Any feature that made instances feel like pets has not been implemented. That said, you can snapshot an instance. Snapshots aren't reliable backups, just like they're not really reliable backups on OpenStack. There is a small but real chance that a snapshot will contain an inconsistent state if you're snapshotting a busy database or something like that. One minor difference from OpenStack -- when you snapshot your instance you can snapshot all of the virtual disks (except the config drive) if you want to. Snapshots are delivered as files you can download via a mechanism external to Shaken Fist (for example an HTTP server pointed at the snapshot directory).

You start an instance like this:

```bash
sf-client instance create "myinstance" 1 2 -d 8@cirros -n netuuid
```

Where "myinstance" is the name of the instance, it has 1 vCPU, 2gb of RAM, a single 8gb disk (more on this in a second) and a single network interface on the network with the UUID "netuuid".

"8@cirros" is a "disk specification". These are in the form size@image, where the @image is optional. You can specify more than one disk, so this is valid:

```bash
sf-client instance create "myinstance" 1 2 -d 8@cirros -d 8 -d 8 -n netuuid
```

In this case we have three disks, all of 8gb. The boot disk is imaged with cirros. The "cirros" here is shorthand. By default, you specify a URL for the image you want, so to boot a cirros instance you might use http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img -- that gets old though, so for common cloud images there is a shorthand format, where Shaken Fist knows how to generate the download URL from a short description. In this case "cirros" means "the latest release of cirros". You can also specify a version like this:

```bash
sf-client instance create "myinstance" 1 2 -d 8@cirros:0.5.1 -d 8 -d 8 -n netuuid
```

"Common cloud images" is currently defined as cirros and Ubuntu.

Similarly, networks are a "network specification", where you can specify the UUID of a network, but also optionally the IP address to use for the interface. You can also have more than one network interface, so this is valid:

```bash
sf-client instance create "myinstance" 1 2 -d 8@cirros -n netuuid1@192.168.1.2 \
    -n netuuid2@10.0.0.4
```

Missing documentation
---------------------

I really should document these as well:

* nodes
* networks: delete, list
* instance: show, delete, list, ssh keys, user data, reboots (hard and soft), poweroff, poweron, pause, unpause, snapshot
* images: pre-caching

Maybe one day I will.

Installation
============

Build an acceptable hypervisor node, noting that only Ubuntu is supported. On Google Cloud, this looks like this:

```bash
# Create an image with nested virt enabled (only once)
gcloud compute disks create sf-source-disk --image-project ubuntu-os-cloud \
    --image-family ubuntu-1804-lts --zone us-central1-b
gcloud compute images create sf-image \
  --source-disk sf-source-disk --source-disk-zone us-central1-b \
  --licenses "https://compute.googleapis.com/compute/v1/projects/vm-options/global/licenses/enable-vmx"

# Start our hypervisor node VMs, we'll have two for now
gcloud compute instances create sf-1 --zone us-central1-b \
    --min-cpu-platform "Intel Haswell" --image sf-image
gcloud compute instances create sf-2 --zone us-central1-b \
    --min-cpu-platform "Intel Haswell" --image sf-image

# And then we need a shared database backend
gcloud compute instances create sf-db --zone us-central1-b \
    --image-project ubuntu-os-cloud --image-family ubuntu-1804-lts
```

Update the contents of ansible/vars with locally valid values. Its a YAML file if that helps.

Now create your database and hypervisor nodes (where foo-1234 is my Google Compute project):

```bash
sudo apt-get install ansible
git clone https://github.com/mikalstill/shakenfist
cd ansible
ansible-playbook -i ansible/hosts-gcp -var project=foo-1234 ansible/deploy.yml
```

There are automated tests, which you can run like this:

```bash
ansible-playbook -i ansible/hosts-gcp -var project=foo-1234 ansible/test.yml
```

At the moment you interact with Shaken Fist via a command line client or the REST API. For now, do something like this to get some help:

```bash
sf-client --help
```

Features
========

Here's a simple feature matrix:

| Feature                                           | Implemented | Planned | Not Planned |
|---------------------------------------------------|-------------|---------|-------------|
| Servers / instances                               | Yes         |         |             |
| Networks                                          | Yes         |         |             |
| Multiple NICs for a given server                  | Yes         |         |             |
| Pre-cache a server image                          | Yes         |         |             |
| Floating IPs                                      |             | Yes     |             |
| Pause                                             | Yes         |         |             |
| Reboot (hard and soft)                            | Yes         |         |             |
| Security groups                                   |             | Yes     |             |
| Text console                                      | Yes         |         |             |
| VDI                                               | Yes         |         |             |
| User data                                         | Yes         |         |             |
| Keypairs                                          | Yes         |         |             |
| Virtual networks allow overlapping IP allocations | Yes         |         |             |
| REST API authentication and object ownership      |             | Yes     |             |
| Snapshots (of all disks)                          | Yes         |         |             |
| Central API service                               |             | Yes     |             |
| Scheduling                                        |             | Yes     |             |
| Volumes                                           |             |         | No plans    |
| Quotas                                            |             |         | No plans    |
| API versioning                                    |             |         | No plans    |
| Keystone style service lookup and URLs            |             |         | No plans    |
| Create multiple servers in a single request       |             |         | No plans    |
| Resize a server                                   |             |         | No plans    |
| Server groups                                     |             |         | No plans    |
| Change admin password                             |             |         | No plans    |
| Rebuild a server                                  |             |         | No plans    |
| Shelve / unshelve                                 |             |         | No plans    |
| Trigger crash dump                                |             |         | No plans    |
| Live migration                                    |             |         | No plans    |
| Flavors                                           |             |         | No plans    |
| Guest agents                                      |             |         | No plans    |
| Host aggregates                                   |             |         | No plans    |
| Server tags                                       |             |         | No plans    |