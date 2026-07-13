---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/networking-shakenfist.md
---

# Shaken Fist networking

Shaken Fist implements virtual networking using the simplest approach that
can work: one Linux bridge per virtual network, kernel-native VXLAN for
the overlay, and a single network node providing centralized services
(DHCP, NAT, floating IPs) inside Linux network namespaces. There is no
OpenFlow, no Open vSwitch, and no complex flow programming. Everything is
built from standard Linux primitives that can be inspected with `ip`,
`brctl`, `iptables`, and `tcpdump`.

This simplicity is deliberate. The Shaken Fist project describes its
networking as "complicated, but not as complicated as OpenStack Neutron --
it's more like the old OpenStack Compute nova-network implementation."
Understanding how it works provides an excellent foundation for
understanding the more complex systems that follow.

## The bridge-per-network model

The defining architectural decision is that each virtual network gets its
own Linux bridge on every host that participates in that network. If a
hypervisor has instances on three different virtual networks, it will have
three separate bridges, each with its own VXLAN interface.

This means isolation between networks is *structural* -- traffic from
network A is on a physically different bridge than traffic from network B.
There is no tagging, no flow rules, and no possibility of a
misconfiguration allowing traffic to leak between networks. The kernel
simply has no path for it.

The cost is more kernel objects: each network requires a bridge, a VXLAN
interface, and (on the network node) two veth pairs and a network
namespace. For deployments with hundreds of networks on a single host,
this adds up. But for the small-to-medium deployments that Shaken Fist
targets, it's an excellent trade-off of scalability for simplicity and
debuggability.

## Naming conventions

Shaken Fist identifies networks internally by their VXLAN ID, expressed
as a hexadecimal string. A network assigned VXLAN ID 14823439 becomes
`e2300f` in hex, and all associated interfaces include this identifier:

| Interface | Purpose |
|-----------|---------|
| `vxlan-e2300f` | VXLAN tunnel interface (kernel-native) |
| `br-vxlan-e2300f` | Linux bridge for this network |
| `veth-e2300f-o` | Outer end of veth to network namespace |
| `veth-e2300f-i` | Inner end of veth (inside namespace, holds router IP) |
| `egr-e2300f-o` | Outer end of egress veth (on egress bridge) |
| `egr-e2300f-i` | Inner end of egress veth (inside namespace, holds floating gateway IP) |
| `flt-XXXXXXXX` / `flt-XXXXXXXX-i` | Floating IP veth pair (hex of the floating IP address); note the outer end has no `-o` suffix, only the inner end is suffixed |
| `vnetN` | Libvirt TAP interface connecting a VM to the bridge |

This consistent naming makes it straightforward to trace any interface
back to its network and understand its role.

## What a network looks like on the wire

### Before any networks exist

A freshly installed Shaken Fist node has just two relevant interfaces:

```
eth0              10.0.0.74/24    Physical interface (mesh traffic)
egr-br-eth0       192.168.15.1/24 Egress bridge (empty, for external traffic)
```

The egress bridge (`egr-br-eth0`) is a Linux bridge with the first IP
from the cluster's *floating network* -- the pool of addresses used for
external connectivity. It starts empty, waiting for virtual networks to
attach their egress veth pairs.

### After creating a network

When a virtual network is created -- say, `172.16.0.0/24` with VXLAN ID
14823439 (`e2300f`) -- the following objects appear on the network node:

**Outside the namespace** (visible via `ip a`):

```
vxlan-e2300f       VXLAN interface (member of br-vxlan-e2300f)
br-vxlan-e2300f    Linux bridge for this network
veth-e2300f-o      → connects bridge to namespace
egr-e2300f-o       → connects egress bridge to namespace
```

**Inside the namespace** (visible via `ip netns exec <uuid> ip a`):

```
veth-e2300f-i      172.16.0.1/24      Router interface for the virtual network
egr-e2300f-i       192.168.15.194/24  Floating gateway address
```

The bridge membership tells the full story:

```
br-vxlan-e2300f:
    vxlan-e2300f      (VXLAN mesh to other hosts)
    veth-e2300f-o     (to network namespace)

egr-br-eth0:
    egr-e2300f-o      (to network namespace)
```

The network namespace is named with the network's UUID (e.g.
`17be6538-8f96-4ccb-b71e-a7e3022fead3`) and contains the inner ends
of both veth pairs. This namespace is where all the network intelligence
lives: the routing table, the iptables NAT rules, and the dnsmasq
process.

### After adding instances

When an instance boots on a network, libvirt creates a TAP interface
(`vnet0`, `vnet1`, etc.) and Shaken Fist adds it to the network's bridge:

```
br-vxlan-e2300f:
    vxlan-e2300f      (VXLAN mesh)
    veth-e2300f-o     (to namespace)
    vnet0             (first instance)
    vnet1             (second instance)
```

No changes are needed inside the namespace -- the dnsmasq process is
already connected to the virtual network via the veth pair and can
provide DHCP to new instances immediately.

## The network namespace

The network namespace is the central piece of Shaken Fist's networking.
It serves three roles simultaneously:

### Router

The namespace's routing table determines where traffic goes:

```
default via 192.168.15.1 dev egr-e2300f-i
172.16.0.0/24 dev veth-e2300f-i proto kernel scope link src 172.16.0.1
192.168.15.0/24 dev egr-e2300f-i proto kernel scope link src 192.168.15.194
```

Traffic destined for the virtual network (`172.16.0.0/24`) goes out
`veth-e2300f-i`, which emerges on the bridge and reaches instances
directly (or via the VXLAN mesh to remote hypervisors). Everything
else -- internet traffic, traffic to other floating IPs -- goes out
`egr-e2300f-i` to the egress bridge and onward to the physical
network.

### NAT gateway

Instances need to reach the outside world, but they have private
addresses that aren't routable on the physical network. The namespace
provides SNAT via a single iptables rule:

```
-A POSTROUTING -s 172.16.0.0/24 -j MASQUERADE
```

This rewrites the source address of outbound packets from the instance's
private IP to the network's floating gateway address
(`192.168.15.194`). Conntrack tracks the mapping so return traffic is
translated back automatically. This is the same mechanism that home
routers use -- simple, well-understood, and reliable.

### DHCP and DNS server

A `dnsmasq` process runs inside the namespace, bound to `veth-e2300f-i`.
It provides DHCP (assigning IP addresses to instances as they boot) and
optionally DNS. Because it runs inside the namespace, it's isolated from
other networks' DHCP servers -- there's no risk of DHCP offers from one
network reaching instances on another.

## Floating IPs

Floating IPs provide inbound connectivity to specific instances. Unlike
SNAT (which is one-to-many, with all instances sharing the network's
floating gateway address), a floating IP is a one-to-one mapping between
a public address and a specific instance.

When a floating IP is associated with an instance, Shaken Fist creates
an additional veth pair. The naming uses the hexadecimal representation
of the floating IP -- for example, floating IP `192.168.15.29`
(`c0a80f1d` in hex) creates the outer end `flt-c0a80f1d` in the root
namespace and the inner end `flt-c0a80f1d-i` inside the network
namespace. Unlike the other veth pairs, the outer end has no `-o`
suffix; only the inner end is suffixed. The add and remove paths derive
these names independently and must agree, so if you change the naming,
change it in the add path, the remove path, the tests, the operator
guide and this page together.

The inner end of the veth (inside the namespace) is configured with
the floating IP as a `/32` address:

```
flt-c0a80f1d-i    192.168.15.29/32
```

A DNAT rule in the namespace's PREROUTING chain rewrites the destination:

```
-A PREROUTING -d 192.168.15.29 -j DNAT --to-destination 172.16.0.37
```

The packet flow for inbound floating IP traffic is:

1. Packet addressed to `192.168.15.29` arrives at the host.
2. The kernel's routing table sees the `/32` address on the veth and
   delivers the packet into the namespace.
3. The DNAT rule rewrites the destination to `172.16.0.37`.
4. The namespace routes the rewritten packet out `veth-e2300f-i` to
   the bridge, where it reaches the instance.

Outbound traffic from the instance still uses the MASQUERADE rule and
exits via the floating gateway. Only inbound traffic uses the dedicated
floating IP veth.

!!! note

    The instance is never aware of its floating IP at the operating
    system level. Inside the VM, the only address on the interface is
    `172.16.0.37`. The DNAT happens entirely in the network namespace,
    outside the VM.

## Routed IPs

Shaken Fist v0.8 introduced *routed IPs* as an alternative to floating
IPs, primarily to support Kubernetes services via metallb. A routed IP
is an address from the floating pool that uses a simple routing rule
instead of DNAT:

```
ip route add 192.168.15.29/32 dev br-vxlan-e2300f
```

The key difference is that the service *inside* the virtual network must
be configured to answer ARP requests for the routed address. With a
floating IP, the instance doesn't know about the external address; with
a routed IP, the service must explicitly claim it. This works naturally
with metallb, which is designed to advertise service IPs via ARP.

## The VXLAN mesh

On a multi-node cluster, the VXLAN interface on each host provides
connectivity to other hosts participating in the same virtual network.
The mesh uses the kernel's native VXLAN implementation -- no OVS, no
userspace tunnel endpoints.

Each hypervisor has the same bridge structure as the network node (a
`br-vxlan-` bridge containing the VXLAN interface and instance TAP
devices) but *without* the network namespace -- no DHCP server, no NAT
rules, no routing. The namespace exists only on the network node. DHCP
requests from instances on remote hypervisors reach the network node's
dnsmasq through the VXLAN mesh, just as they would if the instance were
local.

```
Network node                      Hypervisor node
┌───────────────────────┐         ┌───────────────────────┐
│ namespace (per net)   │         │                       │
│   dnsmasq, NAT, routes│         │                       │
│   ↕ veth pair         │         │                       │
│ ┌───────────────┐     │         │ ┌───────────────┐     │
│ │ br-vxlan-NNN  │     │         │ │ br-vxlan-NNN  │     │
│ │  ├ vxlan-NNN  │     │         │ │  ├ vxlan-NNN  │     │
│ │  ├ veth-NNN-o │     │         │ │  ├ vnet0      │     │
│ │  └ vnet0      │     │         │ │  └ vnet1      │     │
│ └───────┬───────┘     │         │ └───────┬───────┘     │
│         │ VXLAN       │         │         │ VXLAN       │
└─────────┼─────────────┘         └─────────┼─────────────┘
          │        VXLAN tunnel             │
          └─────────────────────────────────┘
              (UDP encapsulated, same VNI)
```

This means all routed and NAT'd traffic must traverse the VXLAN mesh to
reach the network node, which is the same centralization trade-off that
Neutron's legacy routers make. The difference is in the transport: Shaken
Fist uses a simple kernel VXLAN mesh with standard Linux bridges, while
Neutron uses OVS with OpenFlow rules for VLAN-to-VNI translation.

## Network node vs hypervisor node

The distinction between the network node and hypervisor nodes is the
most important architectural boundary in Shaken Fist networking:

| | Network node | Hypervisor node |
|---|---|---|
| Bridge | Yes (`br-vxlan-*`) | Yes (`br-vxlan-*`) |
| VXLAN interface | Yes | Yes |
| Network namespace | Yes (one per network) | No |
| dnsmasq (DHCP/DNS) | Yes (in namespace) | No |
| NAT/routing rules | Yes (in namespace) | No |
| Floating IP veths | Yes | No |
| Egress bridge | Yes (`egr-br-eth0`) | No |

A hypervisor node's involvement in a virtual network is minimal: a VXLAN
interface, a bridge, and TAP devices for local instances. All the
intelligence is centralized on the network node.

## Bridge configuration

Shaken Fist configures its bridges with three notable settings:

**No forwarding delay** (`brctl setfd br-vxlan-NNN 0`) means ports
transition to forwarding state immediately when added. Normally, bridges
wait 15-30 seconds (listening and learning) before forwarding traffic --
sensible for physical networks where loops are possible, but unnecessary
in a controlled virtual environment where the topology is known.

**No STP** (`brctl stp br-vxlan-NNN off`) disables loop detection for
the same reason. The topology is tree-shaped by construction (VXLAN
interfaces, veths, and TAP devices don't create loops), so STP's
overhead and convergence delays are unnecessary.

**No MAC ageing** (`brctl setageing br-vxlan-NNN 0`) means learned MAC
addresses never expire from the forwarding table. In a virtual
environment where MAC addresses are assigned deterministically and don't
change, this avoids periodic flooding when entries would otherwise time
out.

## The cost of simplicity

Shaken Fist's networking model has the same centralization cost as
Neutron legacy routers: all routed traffic between subnets (or to the
outside world) must traverse the network node, even if the communicating
instances are on the same hypervisor. The [Neutron legacy routers
chapter](/components/cloudgood/neutron-legacy/) discusses this cost in detail.

The bridge-per-network model also means that kernel object counts scale
linearly with the number of active networks. A host participating in 100
networks has 100 bridges and 100 VXLAN interfaces. In practice this is
fine for Shaken Fist's target deployment sizes, but it's one of the
motivations for the shared-bridge approach used by OVS-based systems.

What Shaken Fist gains in return is transparency. Every piece of the
networking stack is visible with standard tools, every packet path can
be traced with `tcpdump` on the relevant bridge, and failures tend to
be obvious rather than hidden behind OpenFlow table misses. For operators
who need to debug networking issues at 3am, this has real value.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Shaken Fist networking guide | [Networking Overview](https://shakenfist.com/operator_guide/networking/overview/) |
    | VXLAN kernel implementation | [VXLAN kernel documentation](https://docs.kernel.org/networking/vxlan.html) |
    | Linux bridge administration | [brctl(8) man page](https://man7.org/linux/man-pages/man8/brctl.8.html) |
    | dnsmasq | [dnsmasq documentation](https://thekelleys.org.uk/dnsmasq/doc.html) |

--8<-- "docs-include/abbreviations.md"
