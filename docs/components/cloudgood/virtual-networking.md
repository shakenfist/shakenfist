---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/virtual-networking.md
---

# Virtual networking

Virtual machines need to communicate -- with each other, with services on
the network, and with the outside world. But cloud providers can't just
plug every VM into a physical switch: tenants need isolated networks,
overlapping IP ranges, and the ability to create and destroy network
topologies on demand. Virtual networking exists to solve this problem.

That said, the very first version of cloud computing *did* just plug VMs
into a shared network. When Amazon EC2 launched in August 2006, all
customer instances shared a single flat 10.0.0.0/8 network (1). There
were no per-tenant virtual networks, no VLANs, no isolation at the
network layer. The only thing preventing one customer's instance from
reaching another's was *security groups* -- a hypervisor-level packet
filter enforced in Xen's Dom0, configured as default-deny-all, active
before the guest OS even booted. Security groups were not a convenience
feature; they were the entire isolation mechanism for a shared network
segment.
{ .annotate }

1. Werner Vogels described EC2-Classic as "one giant network of
   10.0.0.0/8" in his [farewell post for EC2-Classic](https://www.allthingsdistributed.com/2023/09/farewell-ec2-classic.html)
   when the last Classic instance was shut down in August 2023,
   seventeen years after launch.

This worked, but it meant that a bug in the security group
implementation would expose every instance to every other instance on
the platform. Amazon introduced VPC (Virtual Private Cloud) in 2009,
adding actual per-tenant network isolation, and made it the default for
new accounts in 2013. The lesson was clear: defense in depth matters,
and network-level isolation should not depend on a single enforcement
point.

The core challenge is deceptively simple: make a group of VMs on
potentially different physical hosts behave as if they're all plugged into
the same Ethernet switch, while keeping that switch completely invisible
to VMs on other virtual networks. Every virtual networking implementation
is, at its heart, an answer to this question.

## The building blocks

Before examining specific implementations, it's worth understanding
the Linux primitives they all share. Every system we'll look at --
from the simplest to the most complex -- builds on the same kernel
features.

**Linux bridges** are the kernel's built-in virtual switch. A bridge
accepts multiple network interfaces as "ports" and forwards Ethernet
frames between them based on MAC addresses, just like a physical switch.
When a VM's TAP interface and a VXLAN tunnel endpoint are both attached to
the same bridge, the VM can communicate with remote VMs as if they were on
the same physical segment.

**VXLAN** (Virtual eXtensible LAN) is an overlay protocol that
encapsulates Ethernet frames inside UDP packets. Each virtual network gets
a 24-bit VNI (VXLAN Network Identifier), allowing up to ~16 million
isolated networks. The encapsulated frames travel over the physical
network infrastructure without any special switch configuration --
the physical network just sees ordinary UDP traffic. This decouples
virtual network topology from physical network topology entirely.

**Network namespaces** provide isolated network stacks within a single
Linux host. Each namespace has its own interfaces, routing table, iptables
rules, and ARP table. Virtual routers, DHCP servers, and NAT gateways
are typically implemented as processes running inside dedicated namespaces,
giving each tenant's network services complete isolation from others on
the same host.

**veth pairs** are virtual Ethernet cables -- creating one gives you two
interfaces that are directly connected. Whatever goes in one end comes out
the other. They're the standard mechanism for connecting a network
namespace to a bridge or to another namespace, since interfaces can be
moved between namespaces but bridges cannot span them.

**iptables** (and its successor nftables) provides packet filtering, NAT,
and traffic mangling. SNAT (Source NAT) rewrites source addresses to allow
instances to share a public IP for outbound traffic. DNAT (Destination
NAT) rewrites destination addresses to direct incoming traffic to the
right instance, which is how floating IPs work.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Linux bridge | [bridge(8) man page](https://man7.org/linux/man-pages/man8/bridge.8.html) |
    | VXLAN encapsulation | [RFC 7348: VXLAN](https://www.rfc-editor.org/rfc/rfc7348) |
    | Network namespaces | [network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) |
    | veth pairs | [veth(4) man page](https://www.man7.org/linux/man-pages/man4/veth.4.html) |
    | netfilter/iptables | [netfilter.org](https://www.netfilter.org/) |

## The design spectrum

Virtual networking implementations sit on a spectrum between two
philosophies:

**One bridge per network**: Create a separate Linux bridge for each
virtual network on each host. Each bridge gets its own VXLAN interface
with a unique VNI. Isolation is structural -- traffic from different
networks is on physically separate bridges and can never mix. This is
simple to understand and debug with standard Linux tools (`brctl show`,
`bridge fdb`, `tcpdump`), but creates more kernel objects as the number
of networks grows.

**One shared bridge per host**: Create a single bridge (or virtual
switch) that carries traffic for all virtual networks. Traffic is
distinguished using internal tags (typically VLAN IDs) that are local
to the bridge and translated to globally-unique identifiers (VXLAN VNIs)
at the tunnel boundary. This requires programmable flow rules (OpenFlow)
to manage the multiplexing, but keeps the number of kernel objects
constant regardless of how many networks exist.

Neither approach is inherently superior. The bridge-per-network model is
simpler and provides stronger isolation, while the shared-bridge model
scales better in the number of kernel objects and enables more
sophisticated traffic engineering. The implementations we'll examine in
the following chapters illustrate this trade-off concretely.

## Implementations

The following chapters walk through a progression of virtual networking
implementations, from the simplest to the most complex. Each builds on
the same Linux primitives but makes different trade-offs in complexity,
performance, and capability.

| Chapter | Approach | Status |
|---------|----------|--------|
| [Shaken Fist networking](/components/cloudgood/networking-shakenfist/) | Bridge per network, kernel VXLAN, single network node | In production |
| [Neutron with Linux bridge](/components/cloudgood/neutron-linuxbridge/) | Bridge per network, kernel VXLAN, within OpenStack | Removed in 2025.1 |
| [Neutron legacy routers (OVS)](/components/cloudgood/neutron-legacy/) | Shared OVS bridges, OpenFlow, centralized routing | Supported |
| [Neutron HA routers](/components/cloudgood/neutron-ha/) | Legacy + keepalived failover | Supported |
| [Neutron DVR](/components/cloudgood/neutron-dvr/) | Distributed routing on compute nodes | Supported |

Reading them in order shows how each step adds complexity to solve a
specific limitation of the previous approach -- and why simpler designs
remain perfectly valid for deployments that don't need the additional
capabilities.

## Planned topics

These topics will be covered in future revisions of this chapter or as
separate chapters in the series:

- Open vSwitch (OVS) internals and why it exists
- Software-defined networking concepts and OpenFlow
- Geneve and the evolution of overlay protocols
- eBPF and its role in modern networking
- Security groups and connection tracking
- SR-IOV and hardware passthrough for networking
- The performance costs of virtual networking
- OVN as the successor to Neutron's OVS agent
- Physical network orchestration (Cisco ACI and similar)
- Neutron's other extension points: ML2 mechanism drivers, L3 service
  plugins, L2 agent extensions, and how vendors (Cisco ACI, VMware NSX,
  Juniper, OpenDaylight, Calico) integrate at different layers rather
  than using the L3 agent extension API

--8<-- "docs-include/abbreviations.md"
