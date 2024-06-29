# Usage

This page attempts to provide a summary of the minimum amount of information you
need to get started as a Shaken Fist user. The other pages in this section of
the documentation provide deeper information about specific sub areas.

## Clients

The primary Shaken Fist client is packaged and released in the `shakenfist-client`
package from pypi. This client is released at the same time as any major release
of Shaken Fist, but there is no guarantee that minor releases are done in sync.
However, the Shaken Fist client knows how to probe the server for capabilities,
so it is recommended that you keep the client as up to date as possible.

The client is installed on the primary node as part of the operation of the
Ansible based installer. Authentication details are also provided on the primary
node. That means that you can just get started on the primary node without any
further configuration.

The `shakenfist-client` package contains two clients at the time of writing:
a REST API client that can be imported at `shakenfist_client.apiclient`; and a
command line client implemented with this REST API client. The command line
client is exposed as `sf-client` once the python package is installed.

The command line client can produce output in three formats: the standard
"pretty" format, a mostly-csv format called "simple" (which is aimed at being
easy to parse in shell scripts), and JSON. You select the output format with a
flag like this:

```bash
sf-client --simple instance list
```

The default formatter is the "pretty" formatter, so you never need to specify
that on the command line.

???+ tip "Command line client output options"

    * `--pretty` (also the default): ASCII art tables where appropriate human
      readable output.
    * `--simple`: mostly comma delimited output intended to be easier to parse
      in a shell script.
    * `--json`: JSON format output intended to be parsed by other programs. In
      general this output is also exactly what the REST API returns, which can
      be handy when developing with that API.

You can explore what the command line client is capable of by asking it for help:

```bash
$ sf-client --help
Usage: sf-client [OPTIONS] COMMAND [ARGS]...

Options:
  --pretty
  --simple
  --json
  --verbose / --no-verbose
  --namespace TEXT
  --key TEXT
  --apiurl TEXT
  --async-strategy, --async [continue|pause|block]
  --help                          Show this message and exit.

Commands:
  admin      Admin commands
  ansible    Ansible commands, intended to be used as modules
  artifact   Artifact commands
  backup     Backup commands
  blob       Blob commands
  instance   Instance commands
  interface  Interface commands
  k3s        k3s orchestration commands
  label      Label commands
  namespace  Namespace commands
  network    Network commands
  node       Node commands
  version    Output the version of the client
```

This help is present at several levels, such as:

```bash
$ sf-client instance --help
Usage: sf-client instance [OPTIONS] COMMAND [ARGS]...

  Instance commands

Options:
  --help  Show this message and exit.

Commands:
  add-interface    Add a network interface to an instance
  await            Await an agent ready from the specified instance
  consoledata      Get console data for an instance
  consoledelete    Clear the console log for this instance
  create           Create an instance.
  delete           Delete an instance
  delete-all       Delete ALL instances
  delete-metadata  Delete a metadata item
  download         Download a file from an instance
  events           Display events for an instance
  execute          Execute a command on an instance
  list             List instances
  pause            Pause an instance
  poweroff         Power off an instance
  poweron          Power on an instance
  reboot           Reboot instance
  screenshot       Download a screenshot of the console of an instance
  set-metadata     Set a metadata item
  show             Show an instance
  snapshot         Snapshot instance
  unpause          Unpause an instance
  upload           Upload a file to an instance
  vdiconsole       Launch a VDI console for the instance
  vdiconsolefile   Download a .vv file for the VDI console
```

## Networking fundamentals

Virtual networks / micro segmentation is provided by VXLAN meshes between the
instances. Hypervisors are joined to a given mesh when they start their first
instance on that network. DHCP services are optionally offered from a "network
services" node, which is just a hypervisor node with some extra dnsmasq process.
NAT is also optionally available from the network services node. If your network
provides NAT, it consumes an IP address from the floating IP pool to do so, and
performs NAT in a network namespace on the network node.

You create a network on the command line like this:

```bash
sf-client network create mynet 192.168.1.0/24
```

Where "192.168.1.0/24" is the CIDR network address range to use, and "mynet" is
the name of the network. You'll get back output describing the network, including
the UUID of the network, which is used in later calls.

## Instances

### Config drive

By default every instance gets a config drive, this config drive is always
presented as a ISO9660 filesystem on a virtual hard drive. You can however
disable the config drive if you want.

???+ tip "Supported config drive types"

    * `openstack` (also the default): an OpenStack style configuration drive.
    * `none`: no config drive at all.

If a config drive is configured, it is always the second virtual disk attached
to the VM (vdb on Linux if you're using virtio disks). There is no metadata
server.

### Image service, instance flavors or types

Additionally, there is no image service -- you specify the image to use by
providing a URL. That URL is cached, but can be to any HTTP server anywhere.
Even better, there are no flavors. You specify what resources your instance
should have at boot time and that's what you get. No more being forced into a
t-shirt sized description of your needs.

### Instance reliability features

Instances are always cattle. Any feature that made instances feel like pets has
not been implemented. That said, you can snapshot an instance. Snapshots aren't
reliable backups, just like they're not really reliable backups on OpenStack.
There is a small but real chance that a snapshot will contain an inconsistent
state if you're snapshotting a busy database or something like that. One minor
difference from OpenStack is that when you snapshot your instance you can
snapshot all of the virtual disks (except the config drive) if you want to.
Snapshots are delivered as artifacts much like other objects, and can be
downloaded via the REST API and command line client.

### Starting your first instance

You start an instance like this:

```bash
sf-client instance create myinstance 1 2048 -d 8@cirros -n netuuid
```

Where myinstance is the name of the instance, in this example it has 1 vCPU,
2048MB of RAM, a single 8gb disk (more on this soon) and a single network
interface on the network with the UUID "netuuid".

### Disk specifications

"8@cirros" is a "short disk specification". These are in the form `size@image`,
where the `@image` is optional. You can specify more than one disk, so this is
valid:

```bash
sf-client instance create myinstance 1 2048 -d 8@cirros -d 8 -d 8 -n netuuid
```

In this case we have three disks, all of 8gb. The boot disk is imaged with
cirros. The "cirros" here is shorthand. By default, you specify a URL for the
image you want, so to boot a cirros instance you might use
http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img -- that gets
old though, so for common cloud images there is a shorthand format, where
Shaken Fist knows how to generate the download URL from a short description.
In this case "cirros" means "the latest release of cirros". You can also specify
a version like this:

```bash
sf-client instance create myinstance 1 2048 -d 8@cirros:0.5.1 -d 8 -d 8 -n netuuid
```

While Cirros is special cased, there are a variety of other images you can use
this shorthand format with. The list changes as different OSes are added, and
now unsupported options are removed. The current list is those listed at
https://images.shakenfist.com, which is where the images are fetched from. These
images (apart from Cirros) also include the Shaken Fist in-guest agent
pre-installed for your convenience.

You can also use a "detailed disk specification", which is what fancy people use.
Its syntax is similar:

```bash
sf-client instance create myinstance 1 2048 \
    -D size=8,base=cirros,bus=ide,type=cdrom -d 8 -d 8 -n netuuid
```

The specification is composed of a series of key-value pairs. Valid keys are:
size; base; bus; and type. If you don't specify a key, you'll get a reasonable
default. Here's how the keys work:

* _size_ as per the shorthand notation.
* _base_ as per the shorthand notation, including version specification.
* _bus_ is any valid disk bus for libvirt, which is virtio, ide, scsi, usb. Use
  virtio unless you have a really good reason otherwise -- the performance of the
  others are terrible. An example of a good reason is to install virtio drivers
  into legacy operating systems that lack them natively.
* _type_ can be one of disk or cdrom. Note that cdroms are excluded from snapshots.

### Network specifications

Similarly, networks have a "short network specification", where you can specify
the UUID or name of a network, but also optionally the IP address to use for the
interface. You can also have more than one network interface, so this is valid:

```bash
sf-client instance create myinstance 1 2048 -d 8@cirros -n netuuid1 \
    -n netuuid2@10.0.0.4
```

Where netuuid1 and netuuid2 are both UUIDs of networks. You can also use the
name of a network, so long as that name is unique in the namespace you are
operating in. So for example, this is valid too:

```bash
sf-client network create testnet 10.0.0.0/24
sf-client instance create testinstance 2 2048 -d 20@debian:12 -n testnet
```

Again, you can still assign a network address while using the network name, such
as `testnet@10.0.0.42`.

There is also a shorthand "short network specification" which implies immediately
floating the interface. A "floating" interface is a routable IP address which is
packet mangled to arrive at your virtual network address, much like in
OpenStack. The details are the same as `-n`, except the flag is `-f`:

```bash
sf-client instance create myinstance 1 2048 -d 8@cirros -f netuuid1
```

There is a "detailed network specification" as well at `-N`, which is composed
of the following keys:

* _network_uuid_ is the UUID of the network to use.
* _address_ is the IPv4 network address to use, if free. If its not free the
  instance will fail to start. If you don't want an address on this interface,
  use "none" as the value for address. If you do not specify any value for
  address, an address on the network will be assigned to you.
* _macaddress_ the mac address to use for the interface.
* _model_ is the model of the network device, with options being ne2k_isa,
  i82551, i82557b, i82559er, ne2k_pci, pcnet, rtl8139, e1000, and virtio. The
  default model is virtio.
* _float_ if true indicates to immediately float the interface once the instance
  is created.

So for example, this is valid:

```bash
sf-client instance create testinstance 2 2048 -d 20@debian:12 \
    -N network_uuid=testnet,address=10.0.0.99,float=true
```

## Missing documentation

I really should document these as well:

* nodes
* networks: delete, list
* instance: show, delete, list, ssh keys, user data, reboots (hard and soft), poweroff, poweron, pause, unpause, snapshot
* images: pre-caching
* metadata
* authentication

Maybe one day I will.