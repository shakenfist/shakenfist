---
title: Usage guide
---

# Clients

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

# Networking

Virtual networks / micro segmentation is provided by VXLAN meshes between the instances. Hypervisors are joined to a given mesh when they start their first instance on that network. DHCP services are optionally offered from a "network services" node, which is just a hypervisor node with some extra dnsmasq process. NAT is also optionally available from the network services node. If your network provides NAT, it consumes an IP address from the
floating IP pool to do so, and performs NAT in a network namespace on the network node.

You create a network on the command line like this:

```bash
sf-client network create mynet 192.168.1.0/24
```

Where "192.168.1.0/24" is the netblock to use, and "mynet" is the name of the network. You'll get back output describing the network, including the UUID of the network, which is used in later calls.

# Instances

Every instance gets a config drive. Its always an ISO9660 drive. It's always the second virtual disk attached to the VM (vdb on Linux). There is no metadata server. Additionally, there is no image service -- you specify the image to use by providing a URL. That URL is cached, but can be to any HTTP server anywhere. Even better, there are no flavors. You specify what resources your instance should have at boot time and that's what you get. No more being forced into a t-shirt sized description of your needs.

Instances are always cattle. Any feature that made instances feel like pets has not been implemented. That said, you can snapshot an instance. Snapshots aren't reliable backups, just like they're not really reliable backups on OpenStack. There is a small but real chance that a snapshot will contain an inconsistent state if you're snapshotting a busy database or something like that. One minor difference from OpenStack -- when you snapshot your instance you can snapshot all of the virtual disks (except the config drive) if you want to. Snapshots are delivered as files you can download via a mechanism external to Shaken Fist (for example an HTTP server pointed at the snapshot directory).

You start an instance like this:

```bash
sf-client instance create "myinstance" 1 2048 -d 8@cirros -n netuuid
```

Where "myinstance" is the name of the instance, it has 1 vCPU, 2048MB of RAM, a single 8gb disk (more on this in a second) and a single network interface on the network with the UUID "netuuid".

"8@cirros" is a "short disk specification". These are in the form size@image, where the @image is optional. You can specify more than one disk, so this is valid:

```bash
sf-client instance create "myinstance" 1 2048 -d 8@cirros -d 8 -d 8 -n netuuid
```

In this case we have three disks, all of 8gb. The boot disk is imaged with cirros. The "cirros" here is shorthand. By default, you specify a URL for the image you want, so to boot a cirros instance you might use http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img -- that gets old though, so for common cloud images there is a shorthand format, where Shaken Fist knows how to generate the download URL from a short description. In this case "cirros" means "the latest release of cirros". You can also specify a version like this:

```bash
sf-client instance create "myinstance" 1 2048 -d 8@cirros:0.5.1 -d 8 -d 8 -n netuuid
```

"Common cloud images" is currently defined as cirros and Ubuntu. You can also use a "detailed disk specification", which is what fancy people use. Its syntax is similar:

```bash
sf-client instance create "myinstance" 1 2048 -D size=8,base=cirros,bus=ide,type=cdrom -d 8 -d 8 -n netuuid
```

The specification is composed of a series of key-value pairs. Valid keys are: size; base; bus; and type. If you don't specify a key, you'll get a reasonable default. Here's how the keys work:

* _size_ as per the shorthand notation.
* _base_ as per the shorthand notation, including version specification.
* _bus_ is any valid disk bus for libvirt, which is virtio, ide, scsi, usb. Use virtio unless you have a really good reason otherwise -- the performance of the others are terrible. An example of a good reason is to install virtio drivers into legacy operating systems that lack them natively.
* _type_ can be one of disk or cdrom. Note that cdroms are excluded from snapshots.

Similarly, networks have a "short network specification", where you can specify the UUID of a network, but also optionally the IP address to use for the interface. You can also have more than one network interface, so this is valid:

```bash
sf-client instance create "myinstance" 1 2048 -d 8@cirros -n netuuid1@192.168.1.2 \
    -n netuuid2@10.0.0.4
```

There is a "detailed network specification" as well, which is composed of the following keys:

* _network_uuid_ is the UUID of the network to use.
* _address_ is the IPv4 network address to use, if free. If its not free the instance will fail to start.
* _macaddress_ the mac address to use for the interface.

# Missing documentation

I really should document these as well:

* nodes
* networks: delete, list
* instance: show, delete, list, ssh keys, user data, reboots (hard and soft), poweroff, poweron, pause, unpause, snapshot
* images: pre-caching
* metadata
* authentication

Maybe one day I will.