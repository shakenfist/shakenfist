Shaken Fist: Opinionated to the point of being impolite
=======================================================

What is this?
-------------

Shaken Fist is a deliberately minimal cloud. Its also currently incomplete, so take statements here with a grain of salt. Shaken Fist came about as a reaction to the increasing complexity of OpenStack, as well as a desire to experiment with alternative approaches to solving the problems that OpenStack Compute addresses. What I really wanted was a simple API to orchestrate virtual machines, but it needed to run with minimal resource overhead and be simple to deploy. I also wanted it to always work in a predictable way.

One of the reasons OpenStack is so complicated and its behaviour varies is because it has many options to configure. The solution seemed obvious to me -- a cloud that is super opinionated. For each different functional requirement there is one option, and the simplest option is chosen where possible. Read on for some examples.

Development choices
-------------------

If there is an existing library which does a thing, we use it. OpenStack suffered from being old (and having issues with re-writes being hard), as well as licensing constraints. We just use the code that others have provided to the community. Always.

Deployment choices
------------------

libvirt is the only supported hypervisor. Instances are specified to libvirt with simple templated XML. If your local requirements are different to what's in the template, you're welcome to change the template to meet your needs.

Instances
---------

Every instance gets a config drive. Its always an ISO9660 drive. Its always the second virtual disk attached to the VM (vdb on Linux). There is no metadata server. Additionally, there is no image service -- you specify the image to use by providing a URL. That URL is cached, but can be to any HTTP server anywhere. Even better, there are no flavors. You specify what resources your instance should have at boot time and that's what you get. No more being forced into a tshirt sized description of your needs.

Instances are always cattle. Any feature that made instances feel like pets has not been implemented. That said, you can snapshot an instance. Snapshots aren't reliable backups, just like they're not really reliable backups on OpenStack. There is a small but real chance that a snapshot will contain an inconsistent state if you're snapshotting a busy database or something like that. One minor difference from OpenStack -- when you snapshot your instance you can snapshot all of the virtual disks (except the config drive) if you want to. Snapshots are delivered as files you can download via a mechanism external to Shaken Fist (for example an HTTP server pointed at the snapshot directory).

Networking
----------

Virtual networks / micro segmentation is provided by VXLAN meshes betwen the instances. Hypervisors are joined to a given mesh when they start their first instance on that network. DHCP services are optionally offered from a "network services" node, which is just a hypervisor node with some extra Docker containers. NAT is also optionally available from the network services node.

Installation
============

Build an acceptable hypervisor node, noting that only Debian and Ubuntu are supported. On Google Cloud, this looks like this:

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

Now on the hypervisor node (omit the --extra-vars for production environments, which are assumed to not be NAT'ed):

```bash
sudo apt-get install ansible
git clone https://github.com/mikalstill/shakenfist
cd shakenfist
ansible-playbook -i ansible/hosts-gcp ansible/deploy.yml
```

At the moment you interact with Shaken Fist via a command line client as I haven't written an API layer yet. When I do, it will probably be graphql. For now, do something like this:

```bash
. /etc/sf/sfrc
sf-client --help
```