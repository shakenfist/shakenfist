---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/neutron-legacy.md
---

# Neutron legacy mode

OpenStack Neutron provides virtual networking for cloud instances. One of its
most fundamental abstractions is the *router* -- a virtual device that connects
tenant networks together and optionally provides access to external networks.
This chapter examines how Neutron's "legacy" (non-distributed, non-HA) routers
work under the hood: how they're built from Linux network namespaces, how
interfaces are plumbed, and how iptables rules provide NAT and floating IP
functionality.

Legacy routers run on dedicated *network nodes* (1), not on the compute
hypervisors where instances live. All routed traffic -- between subnets,
to external networks, and through floating IPs -- traverses the network
from the hypervisor to the network node, through the router namespace,
and back. This is the key architectural distinction from DVR (Distributed
Virtual Router), which moves routing onto the compute nodes themselves.
{ .annotate }

1. Neutron's L3 agent configuration makes this explicit. The `agent_mode`
   setting defaults to `legacy`, described as "deployed on a centralized
   networking node to provide L3 services like DNAT and SNAT." The DVR
   modes (`dvr` and `dvr_no_external`) are the ones that run on compute
   hosts.

## How traffic reaches the network node

Getting packets from an instance on a compute hypervisor to a router
namespace on a different network node requires a transport layer. Neutron
most commonly uses Open vSwitch (OVS) for this, arranged as a topology of
three bridges on each node (1):
{ .annotate }

1. A critical architectural point: there is exactly one of each bridge
   per node, not one per virtual network. All tenant networks on a node
   share the same br-int, br-tun, and br-ex instances, with local VLAN
   tags inside br-int providing the isolation between networks. This is
   fundamentally different from designs that create a separate bridge per
   virtual network -- it trades simplicity of bridge management for
   complexity in the OpenFlow rules that multiplex traffic.

**br-int (integration bridge):** The central switching point. Every
virtual port on the node -- instance TAP devices, router `qr-` and `qg-`
ports, DHCP namespace ports -- connects here. Traffic within br-int is
tagged with *local VLANs* (1), which are per-node VLAN IDs that identify
tenant networks within that bridge. These local VLANs have no meaning
outside the node.
{ .annotate }

1. Local VLANs are allocated dynamically from the range 1-4094 by the
   OVS agent on each node. The same tenant network might be local VLAN
   100 on one hypervisor and local VLAN 37 on another -- the tunnel
   bridge translates between them.

The Neutron server has no knowledge of local VLAN assignments -- they
are managed entirely by the OVS agent on each node. At startup, the
agent creates a pool of all 4094 VLAN IDs. When a network first needs
representation on that node (because a VM port appears),
`provision_local_vlan()` pops an ID from the pool and installs OpenFlow
rules mapping between the local VLAN and the network's tunnel
segmentation ID. The mapping is held in memory by a `LocalVlanManager`
singleton and also written into the OVSDB port records (`other_config`
and `tag` columns), so it can be recovered across agent restarts
without churn. If all 4094 local VLANs are exhausted (unlikely -- each
distinct network with at least one port on the node consumes one), the
agent logs an error and the new network's ports simply have no
connectivity; existing networks are unaffected.

**br-tun (tunnel bridge):** Connected to br-int via a *patch port* pair
(`patch-tun` on br-int, `patch-int` on br-tun). This bridge handles
encapsulation and decapsulation of tunnel traffic (typically VXLAN, though
GRE and Geneve are also supported). OpenFlow rules on br-tun perform the
translation between local VLAN tags and tunnel IDs (1):
{ .annotate }

1. The tunnel ID is the network's segmentation ID -- for VXLAN this is
   the VNI (VXLAN Network Identifier), a 24-bit value that uniquely
   identifies the tenant network across the entire deployment.

- **Outbound** (toward the wire): Traffic arriving from br-int via
  `patch-int` is matched by local VLAN. The VLAN tag is stripped, a
  tunnel ID is set (the network's segmentation ID), and the packet is
  sent out the appropriate tunnel port (e.g. `vxlan-<remote_ip>`). OVS
  wraps it in a VXLAN header and sends it as a UDP packet to the remote
  node.

- **Inbound** (from the wire): Encapsulated packets arrive on a tunnel
  port. OpenFlow rules match on the tunnel ID, push a local VLAN tag,
  and forward the packet to br-int via `patch-int`. A LEARN action
  records the source MAC and tunnel port so that future unicast traffic
  to that MAC can go directly to the right tunnel without flooding.

**br-ex (external bridge):** Connects to the physical network for
provider/external networks. On a network node, this is where the router's
`qg-` interface ultimately reaches the outside world -- traffic exits
br-int, crosses a patch port to br-ex, and leaves through a physical NIC.

The result is that br-int on the compute hypervisor and br-int on the
network node behave as if they were one big switch for each tenant
network, even though the nodes might be physically distant. An instance's
packet tagged with local VLAN 100 on the compute node enters br-tun, gets
wrapped in a VXLAN envelope, crosses the physical network, is unwrapped by
br-tun on the network node, retagged with whatever local VLAN that node
uses, and arrives at br-int -- where the router's `qr-` port is waiting.

```
Compute node                          Network node
┌──────────────────────┐              ┌──────────────────────┐
│ Instance              │              │ qrouter-<id>         │
│   ↓ TAP               │              │   ↑ qr-  ↓ qg-      │
│ ┌──────────┐          │              │ ┌──────────┐         │
│ │  br-int  │          │              │ │  br-int  │─────┐   │
│ └────┬─────┘          │              │ └────┬─────┘     │   │
│      │ patch-tun      │              │      │ patch-tun │   │
│ ┌────┴─────┐          │              │ ┌────┴─────┐  ┌──┴─┐ │
│ │  br-tun  │          │              │ │  br-tun  │  │br-ex│ │
│ └────┬─────┘          │              │ └────┬─────┘  └──┬─┘ │
│      │ vxlan port     │              │      │ vxlan     │   │
└──────┼────────────────┘              └──────┼───────────┼───┘
       │         VXLAN tunnel                 │           │
       └──────────────────────────────────────┘      Physical
                   (UDP encapsulated)                 network
```

Understanding legacy routers is valuable even if you never run OpenStack,
because the implementation is essentially a guided tour of Linux network
namespace mechanics. Every concept here -- namespaces, veth pairs, iptables
NAT, gratuitous ARP -- applies to any system that builds virtual networks
from Linux primitives.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Open vSwitch documentation | [OVS Documentation](https://docs.openvswitch.org/en/latest/intro/what-is-ovs/) |
    | VXLAN encapsulation | [RFC 7348: VXLAN](https://www.rfc-editor.org/rfc/rfc7348) |
    | OpenFlow specification | [Open Networking Foundation Specifications](https://opennetworking.org/software-defined-standards/specifications/) |
    | OVS agent internals (local VLAN management) | [Open vSwitch L2 Agent](https://docs.openstack.org/neutron/latest/contributor/internals/openvswitch_agent.html) |
    | OVS agent source code | [ovs_neutron_agent.py](https://github.com/openstack/neutron/blob/master/neutron/plugins/ml2/drivers/openvswitch/agent/ovs_neutron_agent.py) -- `provision_local_vlan()`, `LocalVlanManager`, and OpenFlow rule installation |

## What is a legacy router?

Neutron supports several router types, selected by a combination of feature
flags:

| `distributed` | `ha` | Router type | Class |
|:-:|:-:|---|---|
| false | false | Legacy | `LegacyRouter` |
| false | true | HA (keepalived) | `HaRouter` |
| true | false | Distributed (DVR) | `DvrLocalRouter` / `DvrEdgeRouter` |
| true | true | DVR + HA | `DvrEdgeHaRouter` |

A legacy router is the simplest case: a single network namespace on a single
network node (1), containing virtual interfaces, iptables rules, and routes.
All tenant traffic that needs routing -- between subnets, to the external
network, or through floating IPs -- passes through this one namespace.
{ .annotate }

1. The "network node" is whichever host runs the Neutron L3 agent that owns
   this router. In small deployments this is often the same machine as the
   controller; in larger deployments it's a dedicated node.

This centralized design is simple to understand and debug, but creates a
single point of failure and a potential bottleneck. That's why the HA and
DVR variants exist -- but they build on the same foundational concepts,
so understanding legacy routers first makes the others much easier to
follow.

## The L3 agent and router factory

The Neutron L3 agent (`neutron.agent.l3.agent`) is a long-running daemon
that manages router lifecycles on its host. When it receives a notification
that a router needs to be created or updated, it goes through a factory
pattern to select the right router class.

The factory maps feature sets to classes:

```python
def _register_router_cls(self, factory):
    factory.register([], legacy_router.LegacyRouter)
    factory.register(['ha'], ha_router.HaRouter)
    # ... DVR variants for dvr_snat mode ...
```

For a legacy router, the feature list is empty -- no `distributed`, no `ha`.
The factory creates a `LegacyRouter` instance and the agent calls
`initialize()` on it, which triggers namespace creation.

## Namespace creation

Every legacy router gets its own Linux network namespace, named with the
prefix `qrouter-` followed by the router's UUID:

```
qrouter-a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

The naming constants are defined in `neutron.agent.l3.namespaces`:

```python
NS_PREFIX = 'qrouter-'
INTERNAL_DEV_PREFIX = 'qr-'
EXTERNAL_DEV_PREFIX = 'qg-'
```

When `RouterInfo.initialize()` runs, it calls `router_namespace.create()`,
which does two things:

1. **Creates the Linux namespace** via `ip netns add` (or its pyroute2
   equivalent).

2. **Configures sysctl parameters** inside the namespace:

| sysctl | Value | Purpose |
|--------|-------|---------|
| `net.ipv4.ip_forward` | 1 | Enable IPv4 packet forwarding (essential for routing) |
| `net.ipv4.conf.all.arp_ignore` | 1 | Only respond to ARP for addresses on the receiving interface |
| `net.ipv4.conf.all.arp_announce` | 2 | Always use the best local address for ARP source |
| `net.netfilter.nf_conntrack_tcp_be_liberal` | 1 | Accept TCP packets that might be slightly out-of-window |
| `net.ipv6.conf.all.forwarding` | 0 or 1 | IPv6 forwarding, conditional on configuration |

These sysctl settings are critical. Without `ip_forward=1`, the namespace
won't forward packets between interfaces at all -- it would behave like an
endpoint, not a router. The ARP settings prevent confusing responses when
multiple interfaces share overlapping subnets.

!!! note

    The `nf_conntrack_tcp_be_liberal` setting deserves special mention.
    Conntrack normally drops TCP segments that don't match its state table
    precisely. In a cloud environment where packets might arrive slightly
    reordered or where asymmetric routing can occur, this strictness causes
    spurious connection resets. The "liberal" mode accepts these edge cases.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Linux network namespaces | [network_namespaces(7) man page](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) |
    | Managing namespaces with ip-netns | [ip-netns(8) man page](https://www.man7.org/linux/man-pages/man8/ip-netns.8.html) |
    | IP sysctl parameters (ip_forward, arp_ignore, etc.) | [Linux Kernel IP Sysctl](https://docs.kernel.org/networking/ip-sysctl.html) |

## Interface naming and plumbing

A router has two kinds of interfaces:

**Internal interfaces** (`qr-` prefix): These connect the router to tenant
subnets. Each internal port gets a device named `qr-<port_id>` (1), where
the port ID is truncated to fit Linux's device name length limit.
{ .annotate }

1. Linux network device names are limited to 15 characters (IFNAMSIZ).
   The prefix `qr-` is 3 characters, leaving 12 for the port UUID,
   which is enough to be unique in practice.

**External interface** (`qg-` prefix): This connects the router to the
external (provider) network, enabling outbound traffic and floating IPs.
The device is named `qg-<port_id>`.

When the L3 agent adds an internal port, `internal_network_added()` performs
three steps:

1. **Plug the interface**: The interface driver (typically OVS or Linux
   bridge) creates a veth pair, connects one end to the integration bridge,
   and places the other end inside the router namespace.

2. **Configure IP addresses**: The port's fixed IPs are applied to the
   interface as CIDR addresses via `init_router_port()`.

3. **Send gratuitous ARP**: For each IP address, a GARP (1) is broadcast
   to ensure that nearby switches and hosts update their ARP tables to
   point to the router's MAC address.
   { .annotate }

    1. A gratuitous ARP is an ARP reply that nobody asked for. The sender
       announces its own IP-to-MAC mapping, which forces other hosts on
       the segment to update their caches. This is important after a
       failover or when a new interface appears.

The external gateway follows a similar pattern via
`external_gateway_added()`, but with additional steps:

- **Default gateway configuration**: The subnet's gateway IP is installed
  as the namespace's default route.
- **Stale gateway cleanup**: If the gateway IP has changed, old default
  routes are removed before the new one is added.
- **Conntrack cleanup**: Existing connection tracking entries are flushed
  (`clean_connections=True`) to ensure traffic flows through the new
  gateway cleanly.
- **IPv6 configuration**: Router Advertisement parameters are configured
  on the external interface if IPv6 subnets are present.

### What it looks like

Inside a running legacy router namespace, you'd see something like this:

```bash title="Examining a legacy router namespace"
$ ip netns exec qrouter-a1b2c3d4-... ip addr
1: lo: <LOOPBACK,UP,LOWER_UP>
    inet 127.0.0.1/8 scope host lo
2: qr-11111111-11: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 10.0.0.1/24 scope global qr-11111111-11
3: qr-22222222-22: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 10.0.1.1/24 scope global qr-22222222-22
4: qg-33333333-33: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 203.0.113.10/24 scope global qg-33333333-33
```

The `qr-` interfaces are the router's feet in each tenant subnet. The
`qg-` interface is its connection to the outside world. Packets arriving
on one `qr-` interface destined for another subnet get forwarded through
the namespace's routing table to the appropriate `qr-` interface. Packets
destined for external addresses go out through `qg-`.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | veth virtual Ethernet pairs | [veth(4) man page](https://www.man7.org/linux/man-pages/man4/veth.4.html) |
    | Network device name limits (IFNAMSIZ) | [netdevice(7) man page](https://man7.org/linux/man-pages/man7/netdevice.7.html) |
    | Gratuitous ARP | [RFC 5227: IPv4 Address Conflict Detection](https://datatracker.ietf.org/doc/html/rfc5227) |

## NAT and iptables rules

The router namespace uses iptables extensively for SNAT (Source NAT), DNAT
(Destination NAT), and floating IP address translation. The rules are
managed by Neutron's `IptablesManager`, which batches updates and applies
them atomically.

### SNAT for outbound traffic

When a tenant instance sends traffic to the internet and the router has
`enable_snat=True`, the router performs source NAT on the `qg-` interface:

```
-A neutron-l3-agent-snat -o qg-33333333-33 \
    -j SNAT --to-source 203.0.113.10
```

This rewrites the source IP of outbound packets from the instance's private
address (e.g. `10.0.0.5`) to the router's external address
(`203.0.113.10`). Conntrack tracks the mapping so return traffic is
translated back automatically.

The `--random-fully` flag is appended when available, which randomizes the
source port selection for SNAT. This prevents port prediction attacks and
avoids collisions when many instances share the same external IP.

### Floating IP rules

Floating IPs use a pair of DNAT and SNAT rules to create a bidirectional
one-to-one NAT between a public IP and a private IP:

```
# Inbound: rewrite destination from floating to fixed
-A neutron-l3-agent-PREROUTING -d 203.0.113.50/32 \
    -j DNAT --to-destination 10.0.0.5

# Outbound from router itself
-A neutron-l3-agent-OUTPUT -d 203.0.113.50/32 \
    -j DNAT --to-destination 10.0.0.5

# Outbound: rewrite source from fixed to floating
-A neutron-l3-agent-float-snat -s 10.0.0.5/32 \
    -j SNAT --to-source 203.0.113.50
```

The `PREROUTING` DNAT rule handles traffic arriving from outside -- packets
addressed to the floating IP get their destination rewritten to the
instance's private IP before the routing decision. The `float-snat` rule
handles the reverse: when the instance sends traffic, its private source
address is rewritten to the floating IP.

The `OUTPUT` chain rule is interesting -- it handles the case where
something *inside the router namespace* (like a metadata proxy) tries
to reach a floating IP. Without this rule, traffic originating from the
namespace itself would bypass `PREROUTING`.

### DNAT'd traffic and SNAT interaction

There's a subtle interaction between floating IP SNAT and the general SNAT
rule. Neutron adds a rule to prevent double-NAT:

```
-A neutron-l3-agent-POSTROUTING ! -o qg-33333333-33 \
    -m conntrack ! --ctstate DNAT -j ACCEPT
```

This says: if traffic is leaving through an internal interface and wasn't
DNAT'd (i.e. it's not a floating IP reply), accept it without further NAT.
This prevents the general SNAT rule from interfering with inter-subnet
traffic that doesn't need address translation.

### Address scope enforcement

Neutron's address scopes feature controls which networks can communicate
directly. The router uses iptables mark rules to enforce scope boundaries:

1. **Mangle chain** (`scope`): Incoming traffic on each interface is marked
   with its address scope ID using `MARK --set-xmark`.

2. **Filter chain** (`scope`): Outgoing traffic on each interface is
   checked -- if the mark doesn't match the interface's scope, the packet
   is dropped.

This ensures that traffic can only flow between networks that share an
address scope, unless a floating IP provides the translation.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | netfilter/iptables project | [netfilter.org](https://www.netfilter.org/) |
    | Connection tracking tools | [conntrack-tools User Manual](https://conntrack-tools.netfilter.org/manual.html) |
    | IP sysctl parameters | [Linux Kernel IP Sysctl](https://docs.kernel.org/networking/ip-sysctl.html) |

## Floating IP address management

Floating IPs aren't just iptables rules -- the floating IP address must
also be configured on the external interface so the namespace can receive
traffic for it.

When a floating IP is associated, the `LegacyRouter.add_floating_ip()`
method:

1. Adds the floating IP address to the `qg-` interface (as a `/32` or
   `/128` secondary address).
2. Sends a gratuitous ARP for the floating IP, announcing the router's
   MAC address as the owner.

This is why you'll see multiple IP addresses on the external interface --
the router's own external IP plus one address per floating IP:

```bash
$ ip netns exec qrouter-... ip addr show qg-33333333-33
4: qg-33333333-33: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 203.0.113.10/24 scope global qg-33333333-33
    inet 203.0.113.50/32 scope global qg-33333333-33
    inet 203.0.113.51/32 scope global qg-33333333-33
```

When floating IPs are disassociated, the address is removed from the
interface and conntrack state for that IP is flushed.

## Extending the router with custom rules

Having seen how Neutron builds its iptables rules for SNAT, floating IPs,
and address scopes, a natural question arises: can you add your own? For
example, you might want to provide a shared service (a DNS resolver, a
license server, an internal API) to all tenant networks, reachable at a
well-known address like `169.254.100.1`, with DNAT hiding the service's
real location.

Neutron doesn't expose a generic "add arbitrary iptables rules" API, but
it does offer several mechanisms for extending what happens inside the
router namespace.

### Port forwarding

The **port forwarding** extension is the most accessible option for
tenants. It creates DNAT rules that forward specific ports on a floating
IP to specific ports on an instance:

```
-A neutron-l3-agent-pf-<id> -d 203.0.113.50/32 \
    -p tcp --dport 8443 \
    -j DNAT --to-destination 10.0.0.5:443
```

This is tenant-facing -- users create port forwarding rules via the
Neutron API, and the L3 agent installs the corresponding iptables rules
in the router namespace. It's useful for exposing specific services
without consuming a full floating IP per instance, but it only works for
inbound traffic from the external network.

### L3 agent extensions

For more powerful customization, Neutron provides an **L3 agent extension
framework**, introduced in the Newton release (2016) (1). Extensions are
Python classes that hook into the router lifecycle -- `add_router()`,
`update_router()`, `delete_router()` -- and receive full access to the
router's `iptables_manager`. This means an extension can add rules to any
table (`nat`, `filter`, `mangle`, `raw`) in any chain:
{ .annotate }

1. The framework was modeled after the L2 agent extension mechanism from
   Liberty and was primarily driven by engineers from Comcast and Intel.
   FWaaS v2 (Firewall as a Service) was the original motivating use
   case. The design was presented at the OpenStack Summit Barcelona in
   October 2016 as "Under the Trenchcoat: Neutron Agent Extensions."

```python
ri.iptables_manager.ipv4['nat'].add_rule(
    'PREROUTING',
    '-d 169.254.100.1/32 -p tcp --dport 443 '
    '-j DNAT --to-destination 10.99.0.5:443'
)
```

Extensions are loaded via stevedore entry points and configured in the
L3 agent's configuration file -- this is an operator-level mechanism,
not tenant-facing. Several of Neutron's own features are implemented as
L3 agent extensions:

| Extension | What it does | iptables table |
|-----------|-------------|----------------|
| Port forwarding | Per-port DNAT on floating IPs | `nat` |
| Conntrack helper | Protocol-specific conntrack (FTP, TFTP) | `raw` |
| NDP proxy | IPv6 neighbor discovery proxying | `filter` |
| QoS (FIP/gateway) | Bandwidth limiting | Uses `tc`, not iptables |
| SNAT logging | Log SNAT translations | `nat`/`filter` |

### The metadata proxy as a model

The metadata proxy (discussed below) is a good example of this pattern
in practice: hiding a service behind a well-known address. Instances
send HTTP requests to `169.254.169.254`, and iptables rules in
the router namespace intercept the traffic and redirect it to a local
proxy process. The proxy adds tenant identification headers and forwards
the request to Nova's metadata service. The instance never knows where
the metadata service actually runs.

An L3 agent extension could implement exactly the same pattern for a
custom service: intercept traffic to a link-local or well-known address
using iptables DNAT or REDIRECT rules, and forward it to the real
service. Since the extension runs on every router update, it can
maintain rules as routers are created and destroyed.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | L3 agent extensions documentation | [L3 Agent Extensions](https://docs.openstack.org/neutron/latest/contributor/internals/l3_agent_extensions.html) |
    | Original design specification | [L3 Agent Extension Framework Spec (Newton)](https://specs.openstack.org/openstack/neutron-specs/specs/newton/l3-agent-extension.html) |
    | Conference talk on agent extensions | [Under the Trenchcoat: Neutron Agent Extensions (Barcelona 2016)](https://www.openstack.org/videos/barcelona-2016/under-the-trenchcoat-neutron-agent-extensions) |
    | Skeleton extension (tutorial) | [neutron-skeleton-extension](https://github.com/davidsha-Intel/neutron-skeleton-extension) -- reference implementation by one of the framework's authors |
    | Third-party example: WireGuard VPN | [neutron-wireguard](https://github.com/arnaudmorin/neutron-wireguard) -- WireGuard tunnels in router namespaces for cross-region interconnection |
    | Port forwarding configuration | [Floating IP Port Forwarding](https://docs.openstack.org/neutron/latest/admin/config-fip-port-forwardings.html) |
    | Stevedore plugin framework | [Stevedore Documentation](https://docs.openstack.org/stevedore/latest/) |
    | Metadata proxy implementation | [neutron/agent/metadata/](https://github.com/openstack/neutron/tree/master/neutron/agent/metadata) -- `driver.py` spawns the proxy inside router namespaces; `agent.py` is the proxy itself |
    | L3 agent configuration | [L3 Agent Configuration](https://docs.openstack.org/neutron/latest/configuration/l3-agent.html) |

## Static route management

Tenant users can add static routes to their routers via the Neutron API.
These appear in the router's data as a list of `{destination, nexthop}`
dictionaries.

The L3 agent processes route changes on each update cycle by diffing the
old and new route lists:

- **Added routes**: Installed via `ip route add` (or `replace`, which
  creates or updates).
- **Removed routes**: Deleted via `ip route del`.
- **ECMP routes**: When multiple routes share the same destination but
  have different next hops, they're installed as a single multipath
  route.

Route failures are silently ignored -- this is intentional, because a
route might reference a next hop that hasn't been plumbed yet, and the
next processing cycle will retry.

## Metadata proxy

Instances often need to reach the metadata service at `169.254.169.254`
to retrieve their configuration (SSH keys, user data, etc.). In a routed
environment, the router namespace intercepts this traffic using iptables
mark rules:

```
-A neutron-l3-agent-PREROUTING -d 169.254.169.254/32 \
    -i qr-+ -p tcp --dport 80 \
    -j MARK --set-xmark <metadata_mark>
```

The `qr-+` wildcard matches all internal interfaces. Traffic matching
this rule is marked and redirected to a metadata proxy process running
inside the namespace, which forwards requests to the Nova metadata
service with the appropriate tenant identification headers.

An IPv6 equivalent exists using the link-local address `fe80::a9fe:a9fe`.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Neutron metadata service architecture | [Metadata Service Architectural Overview](https://docs.openstack.org/neutron/latest/contributor/internals/metadata.html) |
    | EC2 instance metadata (origin of 169.254.169.254) | [AWS EC2 Instance Metadata](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-metadata.html) |

## The processing loop

The L3 agent runs a continuous processing loop that handles router
updates. When a router update arrives (from an RPC notification or
periodic sync), the agent:

1. Fetches the latest router data from the Neutron server.
2. Calls `ri.process()` on the `RouterInfo` (or `LegacyRouter`) instance.

The `process()` method orchestrates all the steps we've discussed:

```python
def process(self):
    self._process_internal_ports()    # Add/remove/update qr- interfaces
    self.process_external()           # Gateway, floating IPs, NAT rules
    self.process_address_scope()      # Address scope marking/filtering
    self.routes_updated(...)          # Static routes
```

Each of these methods is idempotent -- they compare the desired state
(from the router data) with the current state (in the namespace) and
make only the necessary changes. This means the agent can recover from
partial failures by simply re-processing the router.

### Concurrency

The agent uses a thread pool (32 workers by default) to process router
updates concurrently. Each router's `process_external()` method is
synchronized with a per-namespace lock:

```python
@coordination.synchronized('router-lock-ns-{self.ns_name}')
def process_external(self):
    ...
```

This ensures that floating IP and gateway updates for the same router
are serialized, while different routers can be processed in parallel.

## Router deletion

When a router is deleted, the cleanup proceeds in reverse:

1. **Clear data**: The router's gateway, internal ports, and floating
   IPs are set to empty in the router dict.

2. **Process deletion**: `process_delete()` removes internal ports and
   the external gateway gracefully, unplugging interfaces from the
   bridge and removing IP addresses.

3. **Stop daemons**: The radvd daemon (IPv6 Router Advertisement) is
   stopped if running.

4. **Delete namespace**: `router_namespace.delete()` performs final
   cleanup:
   - Enumerates all devices in the namespace.
   - Unplugs `qr-*` interfaces (internal).
   - Deletes `rfp-*` veth pairs (if any, from DVR).
   - Unplugs `qg-*` interfaces (external).
   - Removes the namespace itself.

### Stale namespace cleanup

The `NamespaceManager` provides a safety net for namespaces that outlive
their routers. During a full sync, the agent marks all active routers'
namespaces as "keep". After the sync, any `qrouter-*` namespace not
marked is cleaned up. This handles cases where the agent crashed during
router deletion or missed a delete notification.

## The LegacyRouter class itself

After all this discussion, you might expect `LegacyRouter` to be a
substantial class. It's not. The entire class is:

```python
class LegacyRouter(router.RouterInfo):
    def add_floating_ip(self, fip, interface_name, device):
        if not self._add_fip_addr_to_device(fip, device):
            return lib_constants.FLOATINGIP_STATUS_ERROR

        ip_lib.send_ip_addr_adv_notif(
            self.ns_name, interface_name,
            fip['floating_ip_address'])
        return lib_constants.FLOATINGIP_STATUS_ACTIVE
```

That's it. All the heavy lifting -- namespace creation, interface
plumbing, iptables management, route handling, metadata proxying -- lives
in the parent `RouterInfo` class. `LegacyRouter` only overrides
`add_floating_ip()` to add the floating IP address to the device and
send a gratuitous ARP notification.

This makes sense architecturally: `RouterInfo` implements the common
router behavior, and the subclasses (`LegacyRouter`, `HaRouter`,
`DvrLocalRouter`, etc.) only override what differs. For legacy routers,
almost nothing differs from the base implementation.

## Summary: the life of a packet

To tie everything together, let's trace a packet from an instance on a
compute hypervisor through a legacy router on a network node to the
outside world. This covers the full path -- OVS bridges, tunnels,
namespace, and NAT.

### Outbound: instance to internet

1. The instance sends a packet to `8.8.8.8`. Its default gateway is
   `10.0.0.1` (the router's `qr-` interface address). The instance ARPs
   for `10.0.0.1` and gets the router port's MAC address.

2. The packet leaves the instance's TAP device and enters **br-int on the
   compute node**, tagged with the local VLAN for the tenant network
   (say, VLAN 100).

3. br-int forwards the packet to **br-tun** via the `patch-tun` port.
   OpenFlow rules on br-tun match the local VLAN, strip it, set the
   tunnel ID to the network's segmentation ID (say, VNI 5000), and send
   the packet out the VXLAN tunnel port toward the network node.

4. The packet crosses the **physical network** as a UDP-encapsulated
   VXLAN frame.

5. **br-tun on the network node** receives the encapsulated packet,
   strips the VXLAN header, reads the tunnel ID (5000), pushes a local
   VLAN tag (whatever the network node uses -- say, VLAN 37), and
   forwards the packet to **br-int** via `patch-int`. The LEARN action
   records the source MAC and tunnel port for return traffic.

6. **br-int on the network node** sees a packet tagged VLAN 37 destined
   for the router's MAC address. It delivers the packet to the `qr-`
   port, which is an internal OVS port inside the `qrouter-*` namespace.

7. Inside the **router namespace**, the kernel's routing table says
   `8.8.8.8` should go via the default route, out the `qg-` interface.

8. iptables `POSTROUTING` **SNAT** rewrites the source IP from
   `10.0.0.5` to `203.0.113.10` (the router's external IP). Conntrack
   records the mapping.

9. The packet exits through `qg-` back into **br-int**, which forwards
   it to **br-ex** via a patch port. br-ex sends it out the physical NIC
   to the **external network**.

### Return: internet to instance

1. The reply arrives at the network node's physical NIC, enters
   **br-ex**, crosses to **br-int**, and reaches the `qg-` port in the
   router namespace.

2. Conntrack recognizes the packet and **reverses the SNAT**, rewriting
   the destination from `203.0.113.10` back to `10.0.0.5`.

3. The kernel routes the packet out the appropriate `qr-` interface.
   The packet enters **br-int** tagged with the tenant network's local
   VLAN.

4. br-int forwards the packet to **br-tun**, which looks up the
   destination MAC in its learned flow table, finds the tunnel port for
   the compute node, strips the local VLAN, sets the tunnel ID, and
   sends it as a **VXLAN frame** across the physical network.

5. **br-tun on the compute node** decapsulates the packet, pushes the
   local VLAN tag, and forwards it to **br-int**, which delivers it to
   the instance's TAP device.

### Floating IPs

For floating IPs the flow adds an extra NAT step. Incoming packets
addressed to the floating IP arrive at `qg-` and hit a **DNAT** rule in
`PREROUTING` that rewrites the destination to the fixed IP -- before the
routing decision. Outgoing packets from the instance are rewritten from
the fixed IP to the floating IP in `float-snat`. The OVS transport path
is the same in both cases.

### The full picture

The NAT and routing inside the namespace is pure Linux networking --
namespaces, iptables, and routing tables. But the transport between nodes
is pure OVS: local VLAN tagging, OpenFlow-driven tunnel
encapsulation/decapsulation, and MAC learning. Neither layer knows much
about the other, which is part of what makes the design clean -- the
router namespace just sees Ethernet interfaces, and OVS just sees MAC
addresses and VLAN tags.

## The cost of centralization

Tracing the packet path makes the performance cost of legacy routing
obvious. Consider two instances on the *same compute hypervisor* that
are on different subnets. Even though the instances share a physical
host, every packet between them must:

1. Leave the compute node via a VXLAN tunnel to the network node.
2. Enter the router namespace, be routed (and possibly NAT'd), and exit.
3. Return via a VXLAN tunnel to the same compute node it started on.

That's two tunnel encapsulations, two tunnel decapsulations, and a
round trip across the physical network -- for traffic that never needed
to leave the hypervisor. Each tunnel hop adds latency (typically
hundreds of microseconds to low milliseconds depending on the
network) and consumes bandwidth on the physical links.

The network node itself becomes a bottleneck: every routed packet from
every tenant on every compute node funnels through it. A deployment
with hundreds of instances generating moderate inter-subnet traffic
can saturate the network node's NICs or CPU long before the compute
nodes are stressed.

This is the fundamental motivation for DVR (Distributed Virtual
Routing). DVR moves the routing function onto the compute nodes
themselves, so inter-subnet traffic between instances on the same
hypervisor never hits the wire at all. East-west traffic (between
subnets) is routed locally, and only north-south traffic (to external
networks) needs to reach a centralized SNAT node. The trade-off is
significantly more complexity -- DVR requires additional namespaces,
extra OVS flow rules, and careful coordination of floating IP
ownership -- but for large deployments the performance difference is
substantial.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | DVR deployment guide | [Open vSwitch: High Availability using DVR](https://docs.openstack.org/neutron/latest/admin/deploy-ovs-ha-dvr.html) |
    | DVR design specification | [Neutron OVS DVR Specification](https://specs.openstack.org/openstack/neutron-specs/specs/juno/neutron-ovs-dvr.html) |

## What else is on the network?

The router namespace isn't the only thing connected to br-int. Two
other components are worth mentioning because they're part of the
traffic path that gets an instance to the point where it can use a
router.

### DHCP namespaces

Before an instance can send traffic to its default gateway, it needs
an IP address. Neutron provides DHCP service through *DHCP namespaces*,
named `qdhcp-<network_id>`, which follow the same pattern as router
namespaces. Each tenant network gets a namespace on the network node
containing a `dnsmasq` process bound to a `tap-` interface on br-int.
When an instance boots and sends a DHCP DISCOVER, it reaches this
namespace via the same VXLAN tunnel path, receives an IP address and
default gateway, and can then begin routing traffic.

### Security groups

Security groups are iptables rules (1) applied to instance ports on the
*compute node*, not in the router namespace. They act as a stateful
firewall at the instance level, filtering traffic before it ever reaches
br-tun. If a security group blocks outbound traffic to port 443, that
packet is dropped on the compute node -- it never enters a tunnel, never
reaches the network node, and the router never sees it.
{ .annotate }

1. On newer deployments using the OVS firewall driver, security groups
   are implemented as OpenFlow rules on br-int rather than iptables.
   The effect is the same -- filtering at the instance port -- but the
   mechanism is different.

This means the full traffic path is actually: instance → security group
filtering → br-int → br-tun → tunnel → network node → router namespace.
On the return path, the router sends the packet back through the tunnel,
and security group rules on the compute node filter it again before
delivery to the instance.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Neutron security group internals | [Security Group API](https://docs.openstack.org/neutron/latest/contributor/internals/security_group_api.html) |
    | DHCP agent configuration | [Neutron DHCP Agent](https://docs.openstack.org/neutron/latest/configuration/dhcp-agent.html) |
    | Neutron networking guide | [OpenStack Networking Guide](https://docs.openstack.org/neutron/latest/admin/) |

## Planned topics

These will be covered in separate documents later.

- OpenFlow table details on br-tun: the full table chain from
  PATCH_LV_TO_TUN through UCAST_TO_TUN, FLOOD_TO_TUN, and LEARN_FROM_TUN
- How the metadata proxy process works inside the namespace in detail
- Security groups: iptables vs OVS firewall driver implementations
- HA routers: how keepalived adds active/passive failover to the same
  namespace model
- DVR routers: the distributed model, its additional namespaces (qsnat-,
  fip-), and the trade-offs in detail
- Comparison with other virtual routing approaches (VPP, eBPF-based,
  OVN)

--8<-- "docs-include/abbreviations.md"
