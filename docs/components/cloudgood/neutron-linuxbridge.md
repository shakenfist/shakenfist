---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/neutron-linuxbridge.md
---

# Neutron with Linux bridge

Before Open vSwitch became Neutron's dominant networking backend, the
*Linux bridge agent* offered a simpler alternative that used only
standard kernel networking primitives. Its architecture was remarkably
similar to Shaken Fist's: one bridge per virtual network, kernel-native
VXLAN, and iptables for security groups. No OpenFlow, no flow
programming, no OVS-specific tooling.

The Linux bridge agent was deprecated in the Wallaby release (2021),
marked as experimental due to lack of maintainers, and fully removed
from the codebase in the 2025.1 (Epoxy) release. Understanding it is
still valuable: it shows how the same bridge-per-network architecture
that Shaken Fist uses can be integrated into a larger orchestration
system, and it makes the motivations for switching to OVS much clearer.

## Architecture overview

Like Shaken Fist, the Linux bridge agent creates a separate bridge for
each virtual network present on a host. But where Shaken Fist manages
the entire networking stack itself, the Linux bridge agent is just one
layer in Neutron's architecture -- it handles L2 connectivity (getting
frames between instances and network services) while other Neutron agents
handle L3 routing, DHCP, and metadata.

The key components on each node:

- **One `brq*` bridge per network**: Named `brq<network-id>` using the
  first 11 characters of the Neutron network UUID.
- **VXLAN interfaces**: One `vxlan-<VNI>` per overlay network, attached
  to the corresponding bridge.
- **TAP interfaces**: One `tap<port-id>` per instance port, attached to
  the bridge.
- **No br-tun or br-ex**: There are no separate tunnel or external
  bridges. VXLAN interfaces attach directly to per-network bridges, and
  provider network connectivity uses VLAN sub-interfaces on the physical
  NIC (e.g. `eth1.101`) also attached directly to the bridge.

```
Compute node with two networks
┌─────────────────────────────────────────────────┐
│                                                 │
│ ┌───────────────────┐  ┌───────────────────┐    │
│ │ brq-aaaaaaaa-aa   │  │ brq-bbbbbbbb-bb   │    │
│ │  ├ vxlan-100      │  │  ├ vxlan-200      │    │
│ │  ├ tap-11111111   │  │  ├ tap-22222222   │    │
│ │  └ tap-33333333   │  │  └ tap-44444444   │    │
│ └─────────┬─────────┘  └─────────┬─────────┘    │
│           │ VNI 100              │ VNI 200       │
└───────────┼──────────────────────┼───────────────┘
            │   UDP encapsulated   │
            └──────────┬───────────┘
                   Physical NIC
```

## Naming conventions

| Interface | Convention | Example |
|-----------|-----------|---------|
| Bridge | `brq<network-id[:11]>` | `brq8fe8a32f-e6` |
| VM TAP device | `tap<port-id[:11]>` | `tap5e3f1a22-b7` |
| VXLAN interface | `vxlan-<VNI>` | `vxlan-101` |
| VLAN sub-interface | `<physdev>.<VLAN-ID>` | `eth1.101` |

Compare this with Shaken Fist's naming (which uses the hex VXLAN ID)
and OVS's naming (which uses role prefixes like `qr-`, `qg-`, `qvo-`).
The Linux bridge agent's convention is the most straightforward of the
three -- the bridge name directly encodes the Neutron network UUID.

## How it differs from Shaken Fist

The Linux bridge agent and Shaken Fist share the same fundamental
architecture -- bridge per network, kernel VXLAN, iptables -- but
they differ in what sits *above* the bridge layer:

| Aspect | Linux bridge agent | Shaken Fist |
|--------|-------------------|-------------|
| Bridge topology | One per network (same) | One per network (same) |
| VXLAN | Kernel-native (same) | Kernel-native (same) |
| DHCP | Separate `qdhcp-*` namespace per network, managed by Neutron's DHCP agent | Namespace per network with dnsmasq (same concept, different management) |
| Routing | Separate `qrouter-*` namespace, managed by Neutron's L3 agent | Combined in the network namespace |
| Security groups | iptables on bridge ports (per-instance filtering) | Network-level ACLs |
| External connectivity | Provider networks via VLAN sub-interfaces on dedicated bridges | Egress bridge with veth pairs |
| Network lifecycle | Neutron API → RPC → agent creates/deletes bridges | Shaken Fist daemon creates/deletes directly |

The most significant difference is the separation of concerns. In Shaken
Fist, the network namespace handles routing, NAT, and DHCP all in one
place. In Neutron, these are managed by separate agents (L3 agent, DHCP
agent) that each create their own namespaces and plug into the bridge
independently. This separation makes Neutron more modular -- you can
run DHCP on different hosts from routing, or replace the L3 agent
entirely -- but it also means more moving parts to coordinate.

## How it differs from OVS

The contrast with OVS is sharper and highlights why OpenStack eventually
moved away from the Linux bridge approach:

**Bridge topology**: This is the fundamental difference. The Linux bridge
agent creates one bridge per network; OVS uses a single `br-int` for all
networks with internal VLAN tags providing isolation. On a host with 50
networks, the Linux bridge agent has 50 bridges; OVS has one.

**Tunnel handling**: With Linux bridge, each VXLAN interface is created
by the kernel and attached directly to its network's bridge. With OVS,
a dedicated `br-tun` bridge uses OpenFlow rules to translate between
local VLAN tags (on `br-int`) and VNIs (on the wire). This indirection
is what enables the single-bridge model -- without it, there's no way
to demultiplex incoming tunnel traffic to the right network.

**No OpenFlow**: The Linux bridge agent uses standard kernel forwarding
(MAC learning) with no programmable flow tables. This means debugging
uses standard tools (`brctl show`, `bridge fdb`, `tcpdump`) rather than
OVS-specific commands (`ovs-ofctl dump-flows`, `ovs-dpctl`). It also
means there's no mechanism for the sophisticated traffic engineering that
OpenFlow enables.

**Security groups**: The Linux bridge agent uses iptables rules applied
to bridge ports for security group enforcement. This is
well-understood and reliable, but has known performance issues at scale
because each rule must be evaluated by the kernel's netfilter subsystem.
OVS's native firewall driver uses conntrack integrated into the OpenFlow
pipeline, which performs better with large rule sets.

**External networks**: OVS uses a dedicated `br-ex` bridge (one per
physical network mapping) connected to `br-int` via patch ports. The
Linux bridge agent attaches VLAN sub-interfaces directly to per-network
bridges -- simpler plumbing, but less flexible.

## VXLAN and L2 population

The Linux bridge agent uses the kernel's native VXLAN implementation
with UDP port 8472 by default (1). The tunnel endpoint address (VTEP) is
configured via the `local_ip` setting in the agent's `[vxlan]`
configuration section.
{ .annotate }

1. The kernel default is 8472, while the IANA-assigned port for VXLAN
   is 4789. OVS defaults to 4789. This mismatch means Linux bridge
   and OVS nodes in the same deployment couldn't communicate over
   VXLAN without explicit port configuration -- another reason not to
   mix the two.

An important optimization is **L2 population** (`l2_population = True`
in the agent configuration). Without it, the VXLAN interface must flood
broadcast and unknown-unicast traffic to all tunnel endpoints in the
mesh, which scales poorly. With L2 population enabled, Neutron's server
pushes MAC-to-VTEP mappings directly to each agent via RPC, so the
kernel's forwarding database is pre-populated and flooding is avoided.

This is conceptually similar to OVS's LEARN actions on `br-tun`, but the
mechanism is different: OVS learns dynamically from traffic, while L2
population is a centrally-coordinated push from the Neutron server.

## What couldn't it do?

The features that the Linux bridge agent *couldn't* support were
ultimately what drove its deprecation:

**No DVR**: Distributed Virtual Routing requires programmatic control
over packet forwarding that OpenFlow provides and kernel bridges don't.
Without DVR, all inter-subnet traffic must traverse a centralized network
node -- the same bottleneck discussed in the [Shaken Fist
chapter](/components/cloudgood/networking-shakenfist/) and the [Neutron legacy routers
chapter](/components/cloudgood/neutron-legacy/).

**No Geneve or GRE**: Only VXLAN was supported for overlay networks.
Geneve is the preferred tunnel protocol for OVN (Neutron's next-
generation backend), and GRE was sometimes used in older deployments.

**No DPDK**: OVS-DPDK moves packet processing to userspace for
dramatically higher throughput. There's no equivalent for kernel bridges.

**No hardware offload**: Modern SmartNICs can offload OVS flow
processing to hardware. Linux bridges have no such offload path in the
Neutron context.

**No SDN controller integration**: OVS can be managed by external SDN
controllers (Cisco ACI, VMware NSX) via OpenFlow. Linux bridges have no
equivalent programmable interface.

None of these limitations are fundamental flaws in the bridge-per-network
architecture. They reflect the fact that the Linux kernel's bridge
implementation is a simple L2 switch, while OVS is a programmable
datapath. For deployments that didn't need DVR, DPDK, or hardware
offload, the Linux bridge agent worked well and was easier to operate.
But as OpenStack deployments grew larger and more demanding, the
community's development effort concentrated on OVS (and later OVN),
and there simply weren't enough maintainers to keep the Linux bridge
agent tested and current.

## The deprecation timeline

| Release | Status |
|---------|--------|
| Wallaby (2021) | Marked experimental; required `[experimental] linuxbridge = True` to use |
| 2023.1 (Antelope) | Experimental status reiterated; no new features |
| 2025.1 (Epoxy) | Fully removed from codebase; migration to OVS or OVN required |

The deprecation notice advised operators to migrate to OVS or
"preferably, OVN" before upgrading. No automated migration tooling was
provided -- the bridge architectures are different enough that migration
requires rebuilding the networking layer.

## Lessons for simpler systems

The Linux bridge agent's history illustrates an important point about
virtual networking design: the bridge-per-network model with kernel VXLAN
is a perfectly sound architecture for many use cases. It was not
deprecated because it was broken or insecure, but because it couldn't
support the advanced features that large OpenStack deployments demanded.

For systems like Shaken Fist that target smaller deployments and
prioritize operational simplicity, the same architectural choices remain
valid. The trade-off is clear: you give up DVR, hardware offload, and
SDN integration in exchange for a networking stack that any Linux
administrator can understand and debug with standard tools.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Linux bridge self-service networks (archived) | [Deploy Linux Bridge: Self-service Networks](https://docs.openstack.org/neutron/2023.1/admin/deploy-lb-selfservice.html) |
    | Linux bridge provider networks (archived) | [Deploy Linux Bridge: Provider Networks](https://docs.openstack.org/neutron/2023.1/admin/deploy-lb-provider.html) |
    | Neutron 2025.1 release notes (removal) | [Epoxy Release Notes](https://docs.openstack.org/releasenotes/neutron/2025.1.html) |
    | OVS vs Linux bridge comparison | [Cumulus Networks: Battle of the Switches](https://kumul.us/switches-ovs-vs-linux-bridge-simplicity-rules/) |

--8<-- "docs-include/abbreviations.md"
