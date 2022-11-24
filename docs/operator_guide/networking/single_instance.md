# Networking Explained - Single Node

This page is [part of a series explaining how Shaken Fist networking works](overview.md). You might want to check out the other pages as well.

## The simplest case: a virtual network with no DHCP and no NAT, and a single remote instance

Let's assume you want a new virtual network with no network services. Its just two instances talking to each other.

The basic flow is like this -- you create a virtual network. We allocate you a VXLAN network id (called the vxid in various places in the code):

```
sf-1 # sf-client network create demonet 192.168.0.0/24 --no-dhcp --no-nat
uuid            : b41b067a-44b7-40c5-9ab8-7abdcf5fdc61
name            : demonet
vxlan id        : 2
netblock        : 192.168.0.0/24
provide dhcp    : False
provide nat     : False
floating gateway: None
namespace       : system
state           : initial

Metadata:
```

So in this case we were allocated VXLAN id 2, and have a network UUID of b41b067a-44b7-40c5-9ab8-7abdcf5fdc61. The state of the network is "initial" as it has not been created anywhere yet. If you wait a few seconds, you'll see it transition to a "created" state. You can see the new state with a show command:

```
sf-1 # sf-client network show b41b067a-44b7-40c5-9ab8-7abdcf5fdc61
uuid            : b41b067a-44b7-40c5-9ab8-7abdcf5fdc61
name            : demonet
vxlan id        : 2
netblock        : 192.168.0.0/24
provide dhcp    : False
provide nat     : False
floating gateway: None
namespace       : system
state           : created

Metadata:
```

And you can see the steps we went through to create the network in the events listing:

```
sf-1 # sf-client network events b41b067a-44b7-40c5-9ab8-7abdcf5fdc61
+----------------------------+------+------------------------+------------+----------------------+-----------+
|         timestamp          | node |       operation        |   phase    |       duration       |  message  |
+----------------------------+------+------------------------+------------+----------------------+-----------+
| 2020-08-01 06:49:07.753351 | sf-1 |          api           |   create   |         None         |    None   |
| 2020-08-01 06:49:07.792583 | sf-1 | create vxlan interface |   start    |         None         |    None   |
| 2020-08-01 06:49:07.819312 | sf-1 | create vxlan interface |   finish   | 0.025562286376953125 |    None   |
| 2020-08-01 06:49:07.840636 | sf-1 |  create vxlan bridge   |   start    |         None         |    None   |
| 2020-08-01 06:49:07.905165 | sf-1 |  create vxlan bridge   |   finish   |  0.0644679069519043  |    None   |
| 2020-08-01 06:49:07.910409 | sf-1 |      create netns      |   start    |         None         |    None   |
| 2020-08-01 06:49:07.997595 | sf-1 |      create netns      |   finish   |  0.0850062370300293  |    None   |
| 2020-08-01 06:49:08.016509 | sf-1 |   create router veth   |   start    |         None         |    None   |
| 2020-08-01 06:49:08.237167 | sf-1 |   create router veth   |   finish   |  0.2197411060333252  |    None   |
| 2020-08-01 06:49:08.254165 | sf-1 |  create physical veth  |   start    |         None         |    None   |
| 2020-08-01 06:49:08.342684 | sf-1 |  create physical veth  |   finish   | 0.08613038063049316  |    None   |
| 2020-08-01 06:49:08.371490 | sf-1 |   add mesh elements    |    None    |         None         | 10.2.1.11 |
| 2020-08-01 06:49:08.375682 | sf-1 |          api           |  created   |         None         |    None   |
| 2020-08-01 06:49:40.664204 | sf-1 |          api           |    get     |         None         |    None   |
| 2020-08-01 06:50:07.554738 | sf-1 |          api           | get events |         None         |    None   |
+----------------------------+------+------------------------+------------+----------------------+-----------+
```

You can see here that the network node (sf-1) has created some network elements, and an IP (10.2.1.11) has been added to the mesh. That IP is sf-1, and its part of the network node being joined to the mesh. If we look on sf-1, we should now have a VXLAN interface, a bridge, and one end of a veth.

```
sf-1 # ip addr show vxlan-2
287: vxlan-2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 8950 qdisc noqueue master br-vxlan-2 state UNKNOWN group default qlen 1000
    link/ether de:c4:ab:3d:79:fb brd ff:ff:ff:ff:ff:ff
    inet6 fe80::dcc4:abff:fe3d:79fb/64 scope link 
       valid_lft forever preferred_lft forever

sf-1 # ip addr show br-vxlan-2
288: br-vxlan-2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether be:c1:1b:20:b8:f7 brd ff:ff:ff:ff:ff:ff
    inet6 fe80::bcc1:1bff:fe20:b8f7/64 scope link 
       valid_lft forever preferred_lft forever

sf-1 # ip addr show veth-2-o
290: veth-2-o@if289: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master br-vxlan-2 state UP group default qlen 1000
    link/ether be:c1:1b:20:b8:f7 brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet6 fe80::bcc1:1bff:fe20:b8f7/64 scope link 
       valid_lft forever preferred_lft forever
```

The vxlan-2 interface is the VXLAN mesh, and the br-vxlan-2 bridge is how VMs and veths will connect to the mesh on this local machine. Its important to note that MTU matters here. The MTU for the mesh network is 1500 bytes, and most client VMs will default to that as well. Therefore the underlying network needs to have a MTU greater than that. We default to an MTU of 9000 bytes in our installs, but 1550 would in fact be sufficient in this case. You can see this in the MTU for vxlan-2, which is our 9000 byte underlying MTU, with 50 bytes deducted for the VXLAN encapsulation.

We can also ask the mesh for its current state:

```
sf-1 # bridge fdb show brport vxlan-2
de:c4:ab:3d:79:fb master br-vxlan-2 permanent
de:c4:ab:3d:79:fb vlan 1 master br-vxlan-2 permanent
00:00:00:00:00:00 dst 10.2.1.11 self permanent
de:c4:ab:3d:79:fb dst 127.0.0.1 self 
be:c1:1b:20:b8:f7 dst 127.0.0.1 self 
e2:2d:32:6d:95:70 dst 127.0.0.1 self
```

The current members of the mesh are:

* de:c4:ab:3d:79:fb: this is the mac address for vxlan-2.
* 00:00:00:00:00:00 dst 10.2.1.11: this is a mesh entry for the node with IP 10.2.1.11 (sf-1)
* be:c1:1b:20:b8:f7: this is the outside mac address of a veth between br-vxlan-2 and a network namespace on sf-1
* e2:2d:32:6d:95:70: is the inside mac address of the veth between br-vxlan-2 and the network namespace

What is this network namespace? Well, Shaken Fist needs to create a network namespace to contain routing, NAT, and DHCP for the virtual network. It's actually not strictly required in this simplest case, but we always create it. It is named for the UUID of the virtual network:

```
sf-1 # ip netns exec b41b067a-44b7-40c5-9ab8-7abdcf5fdc61 ip addr list
1: lo: <LOOPBACK> mtu 65536 qdisc noop state DOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
289: veth-2-i@if290: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether e2:2d:32:6d:95:70 brd ff:ff:ff:ff:ff:ff link-netnsid 0
    inet 192.168.0.1/24 scope global veth-2-i
       valid_lft forever preferred_lft forever
    inet6 fe80::e02d:32ff:fe6d:9570/64 scope link 
       valid_lft forever preferred_lft forever
291: phy-2-i@if292: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN group default qlen 1000
    link/ether fe:18:8e:f8:d1:5c brd ff:ff:ff:ff:ff:ff link-netnsid 0
```

The veth between the VXLAN mesh and this namespace is named veth-2-i (the interface inside the network namespace) and veth-2-o (the interface outside the network namespace). There is another veth named phy-2-i and phy-2-o, which is a link between the namespace and the outside world, but we'll talk about that more when we enable NAT. For those who are new to veths, think of them like patch cables -- so what we have here is a VXLAN mesh, which is patched into a network namespace, which is in turn patched into the outside world.

We also do some things with iptables, especially around NAT. Here's the current state of iptables in the network namespace:

```
sf-1 # ip netns exec b41b067a-44b7-40c5-9ab8-7abdcf5fdc61 iptables -L -t nat
Chain PREROUTING (policy ACCEPT)
target     prot opt source               destination         

Chain INPUT (policy ACCEPT)
target     prot opt source               destination         

Chain OUTPUT (policy ACCEPT)
target     prot opt source               destination         

Chain POSTROUTING (policy ACCEPT)
target     prot opt source               destination 
```

That's empty for now because we're not doing any NAT yet, but watch this space. Next let's now start an instance on sf-2. This instance can't use DHCP to get an address because we have that disabled for this network.

```
sf-1 # sf-client instance create inst-on-sf-2 1 1024 -d 20@ubuntu:18.04 -n b41b067a-44b7-40c5-9ab8-7abdcf5fdc61 -p sf-2
uuid        : d53b345f-9d5d-493c-a1a3-7e7d6513cfa2
name        : inst-on-sf-2
namespace   : system
cpus        : 1
memory      : 1024
disk spec   : type=disk   bus=None  size=20  base=ubuntu:18.04  
video       : model=cirrus  memory=16384  
node        : sf-2
power state : on
state       : created
console port: 48420
vdi port    : 32122

ssh key     : None
user data   : None

Metadata:

Interfaces:

    uuid    : db372968-1c74-4033-8423-8a698e9d4900
    network : b41b067a-44b7-40c5-9ab8-7abdcf5fdc61
    macaddr : 00:00:00:34:8d:90
    order   : 0
    ipv4    : 192.168.0.3
    floating: None
    model   : virtio
```

You can see that our instance (inst-on-sf-2) has been placed on sf-2 because we asked nicely (the -p is a placement option to the command), and has been allocated an IP (192.168.0.3). The virtual network still allocates IPs, even if DHCP is disabled. It has also been allocated a MAC address (00:00:00:34:8d:90). What is the state of the mesh on the network node now?

```
sf-1 # bridge fdb show brport vxlan-2
de:c4:ab:3d:79:fb master br-vxlan-2 permanent
de:c4:ab:3d:79:fb vlan 1 master br-vxlan-2 permanent
00:00:00:00:00:00 dst 10.2.1.11 self permanent
0a:ce:c4:f7:cb:dc dst 10.2.1.12 self 
de:c4:ab:3d:79:fb dst 127.0.0.1 self 
be:c1:1b:20:b8:f7 dst 127.0.0.1 self 
00:00:00:34:8d:90 dst 10.2.1.12 self 
e2:2d:32:6d:95:70 dst 127.0.0.1 self
```

The following entries there are new:

```
0a:ce:c4:f7:cb:dc dst 10.2.1.12 self 
00:00:00:34:8d:90 dst 10.2.1.12 self 
```

These new entries:

* Add our new instance to the mesh (00:00:00:34:8d:90)
* And add vxlan-2 on sf-2 to the mesh (0a:ce:c4:f7:cb:dc)

To repeat some commands from above but on sf-2, we now have two new network interfaces over there:

```
sf-2 # ip addr list vxlan-2
127: vxlan-2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 8950 qdisc noqueue master br-vxlan-2 state UNKNOWN group default qlen 1000
    link/ether 0a:ce:c4:f7:cb:dc brd ff:ff:ff:ff:ff:ff
    inet6 fe80::8ce:c4ff:fef7:cbdc/64 scope link 
       valid_lft forever preferred_lft forever

sf-2 # ip addr show br-vxlan-2
128: br-vxlan-2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 8950 qdisc noqueue state UP group default qlen 1000
    link/ether 0a:ce:c4:f7:cb:dc brd ff:ff:ff:ff:ff:ff
    inet6 fe80::8ce:c4ff:fef7:cbdc/64 scope link 
       valid_lft forever preferred_lft forever
```

And the mesh looks like this:

```
sf-2 # bridge fdb show brport vxlan-2
0a:ce:c4:f7:cb:dc master br-vxlan-2 permanent
0a:ce:c4:f7:cb:dc vlan 1 master br-vxlan-2 permanent
00:00:00:00:00:00 dst 10.2.1.11 self permanent
00:00:00:00:00:00 dst 10.2.1.12 self permanent
0a:ce:c4:f7:cb:dc dst 127.0.0.1 self 
00:00:00:34:8d:90 dst 127.0.0.1 self 
e2:2d:32:6d:95:70 dst 10.2.1.11 self
```

There is no network namespace on sf-2:

```
sf-2 # ls /var/run/netns
ls: cannot access '/var/run/netns': No such file or directory
```

If we grep the console log of the instance on sf-2, we can see what cloud-init thought about the networking:

```
sf-1 # grep ci-info /srv/shakenfist/instances/d53b345f-9d5d-493c-a1a3-7e7d6513cfa2/console.log 
[    9.851378] cloud-init[643]: ci-info: +++++++++++++++++++++++++++++++++++++Net device info++++++++++++++++++++++++++++++++++++++
[    9.856401] cloud-init[643]: ci-info: +--------+------+---------------------------+---------------+--------+-------------------+
[    9.858628] cloud-init[643]: ci-info: | Device |  Up  |          Address          |      Mask     | Scope  |     Hw-Address    |
[    9.862566] cloud-init[643]: ci-info: +--------+------+---------------------------+---------------+--------+-------------------+
[    9.873611] cloud-init[643]: ci-info: |  eth0  | True |        192.168.0.3        | 255.255.255.0 | global | 00:00:00:34:8d:90 |
[    9.884575] cloud-init[643]: ci-info: |  eth0  | True | fe80::200:ff:fe34:8d90/64 |       .       |  link  | 00:00:00:34:8d:90 |
[    9.896315] cloud-init[643]: ci-info: |   lo   | True |         127.0.0.1         |   255.0.0.0   |  host  |         .         |
[    9.899272] cloud-init[643]: ci-info: |   lo   | True |          ::1/128          |       .       |  host  |         .         |
[    9.904106] cloud-init[643]: ci-info: +--------+------+---------------------------+---------------+--------+-------------------+
[    9.907816] cloud-init[643]: ci-info: +++++++++++++++++++++++++++++Route IPv4 info+++++++++++++++++++++++++++++
[    9.912118] cloud-init[643]: ci-info: +-------+-------------+-------------+---------------+-----------+-------+
[    9.913889] cloud-init[643]: ci-info: | Route | Destination |   Gateway   |    Genmask    | Interface | Flags |
[    9.917853] cloud-init[643]: ci-info: +-------+-------------+-------------+---------------+-----------+-------+
[    9.920102] cloud-init[643]: ci-info: |   0   |   0.0.0.0   | 192.168.0.1 |    0.0.0.0    |    eth0   |   UG  |
[    9.926243] cloud-init[643]: ci-info: |   1   | 192.168.0.0 |   0.0.0.0   | 255.255.255.0 |    eth0   |   U   |
[    9.929182] cloud-init[643]: ci-info: +-------+-------------+-------------+---------------+-----------+-------+
[    9.935249] cloud-init[643]: ci-info: +++++++++++++++++++Route IPv6 info+++++++++++++++++++
[    9.942774] cloud-init[643]: ci-info: +-------+-------------+---------+-----------+-------+
[    9.956377] cloud-init[643]: ci-info: | Route | Destination | Gateway | Interface | Flags |
[    9.959651] cloud-init[643]: ci-info: +-------+-------------+---------+-----------+-------+
[    9.965082] cloud-init[643]: ci-info: |   1   |  fe80::/64  |    ::   |    eth0   |   U   |
[    9.968116] cloud-init[643]: ci-info: |   3   |    local    |    ::   |    eth0   |   U   |
[    9.970423] cloud-init[643]: ci-info: |   4   |   ff00::/8  |    ::   |    eth0   |   U   |
[    9.974926] cloud-init[643]: ci-info: +-------+-------------+---------+-----------+-------+
ci-info: no authorized SSH keys fingerprints found for user ubuntu.
[   69.743452] cloud-init[970]: ci-info: no authorized SSH keys fingerprints found for user ubuntu.
```

Here you can see that instance has an interface named eth0, which has the IP address that Shaken Fist allocated earlier. How did it get an IP address without DHCP? Well, Shaken Fist always attaches a config drive to the instance, and this contains a JSON file with the IP address in it. cloud-init running on boot of Ubuntu has used this to configure the interface. Before we poke more at this instance, let's start another instance on sf-3 so we can do some more testing...