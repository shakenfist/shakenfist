# Replace dnsmasq with a Rust per-network service plane (sf-netserv)

**Status: placeholder / thoughtbubble.** This is not scheduled work. It
records a design direction so the thinking is not lost, and defines the
triggers that would promote it to a real plan. When one of the triggers
below fires, expand this document using the prompt section.

## Prompt

Before responding to questions or discussion points in this document,
explore the shakenfist codebase thoroughly. Read the current dnsmasq
integration end to end: the managed executable wrapper
(`shakenfist/managed_executables/dnsmasq.py` and
`managedexecutable.py`), the config templates
(`shakenfist/deploy/templates/dhcp.tmpl`, `dhcphosts.tmpl` and the
dnshosts handling), the `Network` methods that mutate dnsmasq state
(`update_dnsmasq`, `remove_dnsmasq`, `remove_dhcp_lease`,
`update_dns_entry`, `remove_dns_entry` in
`shakenfist/network/network.py`), and the operation-queue handlers that
invoke them (see `PLAN-network-facade-phase-04-dnsmasq.md`, which
migrated these onto the queue). Also read how network namespaces are
created and entered on the network node
(`shakenfist/network/bridged_vxlan_network.py`), and the gRPC patterns
used between daemons (`protos/`, `shakenfist/daemons/database/`).

Where a question touches external concepts (DHCP client-id vs chaddr
matching semantics, dnsmasq option behaviour, Rust library maturity for
DNS and DHCP, OVN's native DHCP responder as prior art), research as
needed and flag uncertainty explicitly rather than guessing.

All planning documents go into `docs/plans/`. Consult `ARCHITECTURE.md`
for the system overview and `CLAUDE.md` for conventions. Rust builds
must be wrapped in Docker rather than installing toolchains on
development hosts.

## The thoughtbubble

Shaken Fist uses a tiny slice of dnsmasq: static-reservation-only DHCP
(`dhcp-range=...,static` with a mac,name,ip hosts file — no dynamic
lease allocation), four DHCP options (netmask 1, DNS server 6, domain
15, MTU 26), and optionally a local authoritative DNS zone with
upstream forwarding when `provide_dns` is set. IPv4 only. No TFTP, no
PXE, no DHCPv6 or router advertisements.

That slice is small enough to reimplement as a purpose-built daemon in
Rust (working name `sf-netserv`), which would:

1. **Remove config-file regeneration entirely.** Instead of writing
   config files and signalling dnsmasq (SIGHUP for hosts changes, full
   restart for config changes), the daemon holds each network's host
   table in memory and receives updates over a streaming gRPC feed.
   Note the shape deliberately: not per-packet REST/gRPC queries — DHCP
   could tolerate that latency but DNS cannot, and polling is the wrong
   pattern anyway. Push/watch with a local cache means the packet path
   never touches an API.

2. **Collapse process-per-network into one daemon.** Today the network
   node runs one dnsmasq per network. A single daemon can enter each
   network namespace to open sockets (setns on a worker thread, pass
   the fd back) and serve every network from one supervised process.
   This removes most of the managed-executable lifecycle machinery for
   dnsmasq and helps the known network-node concentration problem.

3. **Create the per-network service plane.** This is the strongest
   argument. A metadata service (169.254.169.254 needs an HTTP
   responder inside every network — config drive cannot be updated
   after boot), SNTP, and syslog ingestion into the event system are
   all "small UDP/TCP service bound in each netns, backed by the same
   in-memory state cache". Once the first binary exists with the netns
   machinery and the streaming state feed, each additional service is
   incremental.

4. **Replace a C binary with a long CVE history** (DNSpooq et al.) with
   memory-safe Rust running with dropped privileges. Real, but a
   tiebreaker rather than a driver: Debian patches dnsmasq for us; we
   would own our own CVEs.

### Architectural constraints already decided

* The Rust daemon must **not** talk to the database gateway directly.
  Only sf-database touches MariaDB, and other daemons go through
  narrow, purpose-built services. The network daemon already knows
  everything dnsmasq needs; it should feed sf-netserv over a small
  protobuf contract on a local unix socket (tonic on the Rust side, our
  existing proto toolchain on the Python side).
* Rust builds are wrapped in Docker (matching the ryll / instar / imago
  pattern); no native toolchains on dev hosts.
* Ship behind a per-network or config opt-in flag alongside dnsmasq,
  with a CI job running the full functional suite against sf-netserv,
  before flipping the default and deleting the dnsmasq
  managed-executable code.

### Known risks

* **DHCP client interop.** dnsmasq embodies twenty years of client
  quirk workarounds: client-id vs chaddr matching, broadcast-flag
  handling, the Windows behaviours behind `filterwin2k`. This is where
  a reimplementation bleeds. Mitigation: the static-reservation model
  shrinks the surface a lot, functional CI boots real guests through
  DHCP, and the CI matrix should include cirros, Ubuntu, Debian and a
  Windows image before the default flips.
* Candidate building blocks needing evaluation at plan time:
  `hickory-dns` (mature Rust DNS), `dhcproto` / `dora` (Rust DHCP).
  Prior art: OVN replaced dnsmasq with native DHCP responses; Neutron
  still carries dnsmasq config-regeneration pain.

### Triggers that promote this to a real plan

Whichever comes first:

1. We want a metadata service (or any second in-network facility such
   as SNTP or syslog ingestion).
2. The config-regeneration / signal machinery causes another flake or
   race we have to root-cause.
3. Process-per-network scaling becomes a measured problem on the
   network node.

A 1:1 dnsmasq replacement with none of these drivers is judged to be
just below the build/borrow threshold: battle-tested C that Debian
maintains beats new Rust that we maintain, if the feature set is
frozen.

### Related plans

* `PLAN-network-facade-phase-04-dnsmasq.md` — migrated the dnsmasq
  mutation methods onto the operation queue; sf-netserv's update feed
  would replace the apply-side of those operations.
* `PLAN-remove-syslog-forwarding.md` — the syslog direction that a
  per-network syslog listener would eventually serve.

### Push audit

When this thoughtbubble is promoted to a real plan and phases are cut, the
last of them is the push audit. It runs `PUSH-AUDIT.md` over the accumulated
diff of every phase in the plan against `develop`, not the last phase's diff
alone. Findings land as their own pull request, and the plan is not complete
until each is resolved or declined in writing here. If the audit finds
nothing, that is recorded in one sentence.
