# Replace exec'd network commands with native netlink operations

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
`shakenfist/daemons/privexec/main.py` and
`shakenfist/daemons/privexec/util.py` in full, since they
hold most of SF's exec'd network operations today. Read
`shakenfist/daemons/network/main.py` for the few exec sites
that live outside privexec. Read the network worker and the
single-mutator path being built in `PLAN-network-facade.md`,
since this plan changes the *mechanism* the single mutator
uses without changing *who* the mutator is. Ground your
answers in what the code does today. Do not speculate when
you could read it instead. Where a question touches on
external concepts (netlink, rtnetlink, NFT netlink, the
`pyroute2` library and its `IPRoute`/`NDB`/`nftables`
namespaces, the `python-nftables` JSON interface, the
`grpc.health.v1.Health` protocol, kernel ABI stability
guarantees), research as needed to give a confident answer.
Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the daemon inventory and the
role of `sf-privexec` as the privileged operations daemon.
Consult `CLAUDE.md` for build commands and project
conventions. Key references inside the repo include
`shakenfist/daemons/privexec/main.py:198` (the first
`iptables` exec site), `shakenfist/daemons/privexec/util.py`
(the `ip link`, `brctl`, `sysctl` exec helpers — see lines
94, 99, 133, 155, 183, 214, 219, 224, 229, 242, 247, 262
for representative examples), and
`shakenfist/daemons/network/main.py:138-150` (the egress
bridge setup that exec's `ip link set` and `iptables`
directly from `sf-net` rather than going through privexec).

This plan is **partial**. Phase 0 will resolve the open
questions into a decisions document and the phase table
below may be re-cut accordingly.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named with `-phase-NN-descriptive`
appended.

I prefer one commit per logical change, and at minimum one
commit per phase.

## Situation

Shaken Fist's network mutations are implemented today by
forking `iptables`, `ip`, `brctl`, `sysctl`, and (in
places) `arping` subprocesses. The bulk of this lives in
`shakenfist/daemons/privexec/`; a few sites in
`shakenfist/daemons/network/main.py` exec the same kinds of
commands directly without going through privexec. The
shape is the historical Linux pattern: build a command
line, run it under `subprocess`, parse the exit code (and
sometimes the stdout, for the `ip -pretty -json link show`
case in `privexec/util.py:99`) to figure out what
happened.

This pattern was the only sensible answer for a long time.
The kernel did not offer a stable, public, in-process API
for firewall management — the legacy iptables interface
was a binary `setsockopt` blob with version-sensitive
offsets, `libiptc` was never declared stable, and the
maintainers explicitly told everyone to shell out to the
CLI. Bridge and VLAN management had a stable rtnetlink
API in principle but the language ecosystem (especially
Python) had no obvious mature binding, so exec'ing
iproute2's `ip` command was the path of least resistance.

That situation has changed. The kernel's answer to "we
need a programmable firewall API real software can call
directly" was **nftables on netlink** — a stable,
documented, public kernel API with native transaction
support (multiple operations across multiple tables
committed atomically), extensibility without breaking
older clients, an async subscription model for ruleset
changes, and language-agnostic bindings. Around the same
time, the **`pyroute2`** Python library matured into a
production-quality wrapper around rtnetlink, NFT netlink,
generic netlink, and the network-namespace API. It is now
used in real production code (OpenStack Neutron's
agent-side dataplane, parts of Calico's Felix, and others).

Three modern paths exist for a project moving off
exec'd network commands:

1. **nftables via netlink** (plus rtnetlink for link / addr
   / route work). The most incremental: same conceptual
   model as the current iptables approach, but typed,
   transactional, and library-call-shaped rather than
   exec-shaped.
2. **Open vSwitch / OVN.** Bypass netfilter entirely and
   express network policy as OpenFlow rules in a userspace
   datapath. This is where OpenStack drifted with the OVN
   mech driver. Much bigger architectural change.
3. **eBPF.** Attach programs directly to XDP, tc, and
   socket hooks; skip the netfilter framework. Cilium-style.
   Highest performance ceiling, biggest learning curve, and
   a fundamentally different operational model.

For SF, **path 1 is the obvious choice.** It preserves the
mental model SF already has (filter rules, NAT rules,
bridges, VXLANs), it lands incrementally (one rule type or
one operation at a time), and it has a mature single-
library answer in `pyroute2`. Paths 2 and 3 are real
options but are scope-creep relative to what this plan
needs to solve, and would bind SF to a much larger
operational and dependency surface.

There is also a **privilege separation opportunity** that
becomes available once `PLAN-network-facade.md` lands.
Today `sf-privexec` is a single root-privileged daemon
that any local SF process can ask to run arbitrary
privileged operations. After network-facade, all network
mutations are funnelled through a single mutator — the
`net-worker` thread inside `sf-net`, reading from the
operation queue — and `net-worker` is the only thing that
ever needs to invoke network-privileged operations. That
narrows both the caller set and the operation set enough
that splitting `sf-privexec` becomes worth doing: a small
dedicated daemon (call it `sf-net-privexec` for now —
phase 0 picks the name) holds only `CAP_NET_ADMIN`,
exposes only typed network operations, and accepts
requests only from `net-worker`. Whatever residual
privileged work `sf-privexec` does today (libvirt-side VM
operations, on-disk image manipulation, qemu interactions)
stays in the existing daemon. Each daemon ends up with a
smaller blast radius than the combined one has today.

The three changes — exec to netlink, untyped to typed RPC,
and one privileged daemon to two — reinforce each other.
Doing them together costs less than doing them
sequentially, because each touches the same files in
`shakenfist/daemons/privexec/` and the same RPC contract,
and because the typed-RPC shape and the split-daemon shape
both fall naturally out of an in-process netlink
implementation. This plan therefore folds them into one
arc, gated on `PLAN-network-facade.md` having landed so
the "exactly one caller of network privilege" property
holds.

## Mission and problem statement

Every network mutation Shaken Fist performs today by
exec'ing `iptables`, `ip`, `brctl`, `sysctl`, or `arping`
is replaced by a native netlink call through `pyroute2`.
The replacement is incremental — each operation moves
independently — and ends with no remaining exec sites for
network commands in the server tree.

Concretely:

- **iptables / ip6tables** rules currently installed by
  privexec move to nftables tables and chains, installed
  via netlink in atomic transactions.
- **`ip link` / `ip addr` / `ip route` / `ip neigh` /
  `ip netns`** calls move to `pyroute2.IPRoute()` (or the
  higher-level `NDB`) — typed APIs returning structured
  results instead of stdout to be parsed.
- **VXLAN bridge FDB management (`bridge fdb show / del /
  append`)** is the single largest contributor to exec
  churn in SF today and the highest-leverage target of this
  plan. Each ensure_mesh call shells out one `bridge fdb
  show` plus one `bridge fdb del` or `append` *per delta*
  (see `_ensure_mesh` in `shakenfist/daemons/privexec/main.py:292`).
  After the ensure_mesh fan-out landed on the network-
  facade branch — one op per participating hypervisor, not
  one cluster-wide — an instance start on an N-node mesh
  produces O(N²) forks across the cluster for FDB work
  alone, on top of the gRPC round-trip each one already
  carries. Bridge FDB lives in the rtnetlink `neigh` family
  (`RTM_NEWNEIGH` / `RTM_DELNEIGH` with `NDA_DST` for the
  VXLAN destination and `NTF_SELF` to scope to the bridge-
  port FDB), so the move is squarely inside the pyroute2
  surface; the encoding is one of the fiddlier
  `RTM_NEWNEIGH` shapes and is called out as a subtask of
  phase 1 rather than rolled in with the plain `ip neigh`
  work.
- **`brctl`** calls (currently used in
  `privexec/util.py:219-229` to disable STP, set forward
  delay, and zero the ageing timer on each VXLAN bridge)
  move to the equivalent rtnetlink `IFLA_BR_*` link
  attributes. Modern iproute2 already implements `brctl`
  this way — `brctl` itself is deprecated.
- **`sysctl`** calls remain a corner case: pyroute2 does
  not abstract `/proc/sys/net`. Either we keep the few
  remaining sysctl writes as direct file writes (no exec
  but no netlink either) or accept exec for that narrow
  surface. Phase 0 decides.
- **`arping`** (gratuitous ARP after floating IP
  assignment) does not have a clean library path. The
  honest answer is probably a small in-process ARP-frame
  send via raw socket, or accept exec for this narrow
  surface. Phase 0.
- **`sf-privexec`'s gRPC API surface** is reshaped from
  "exec this command for me" to typed operations
  (`AddNATRule`, `CreateBridge`, `AddInterfaceToBridge`,
  …). The reshape is a security improvement on top of the
  performance and reliability wins: fewer paths for
  callers to construct arbitrary kernel mutations.
- **Network-privileged work moves into a dedicated
  daemon** (working name `sf-net-privexec`; phase 0 picks
  the final name). This new daemon holds only
  `CAP_NET_ADMIN`, exposes only typed network operations,
  and accepts requests only from `net-worker` (the single
  mutator established by `PLAN-network-facade.md`). The
  existing `sf-privexec` daemon retains its non-network
  responsibilities (libvirt, on-disk image manipulation,
  qemu interactions) but drops `CAP_NET_ADMIN` once the
  split is complete. The result is two daemons each with
  a strictly smaller blast radius than today's combined
  one.

The principle is: **use the stable in-process kernel API
that exists in 2026, and stop forking subprocesses for
work the kernel can do in one netlink syscall.** The
correctness and atomicity gains (transactional nft
rulesets, structured error reporting, no shell-escaping
hazards, no stdout-parsing) are at least as valuable as
the performance gains.

The **process-churn motivation** is concrete and worth
calling out separately from the correctness motivation.
SF's network mutation path today is dominated by short-
lived helper processes: `_ensure_mesh` alone forks one
`bridge fdb show` plus one `bridge fdb del/append` per
FDB delta, and ensure_mesh runs on every instance start
on every participating hypervisor. Profiling on the
network-facade branch traces a single instance start to
double-digit forks of bridge/ip/iptables binaries on the
hypervisor, each one paying the fork+exec+library-load
tax (~5-15 ms on a warm host, more under memory
pressure) and routing through `sf-privexec`'s gRPC
channel on top. The netlink replacement collapses each
"one syscall per delta" exec storm into a single
netlink-batch syscall and removes the privexec hop where
the worker is already in-process. The cluster_ci
"6 instances on 3 hypervisors" scenario should see a
visible drop in per-start wall time as a result; that
metric is worth capturing pre/post as part of phase 1's
acceptance criteria.

## Alternatives considered

### iptables-nft compatibility shim

Modern distros ship `iptables-nft`, a compatibility binary
that takes the legacy iptables CLI and translates it into
nftables operations under the hood. SF could continue to
exec `iptables`, and on a recent distro the rules would
land in nftables anyway. We reject this:

- It still pays the exec / fork / parse cost on every
  mutation.
- It is still vulnerable to shell-escaping bugs if any
  user-controllable string ever reaches the command line.
- It still cannot do transactional multi-rule commits
  atomically across multiple tables, because the iptables
  CLI does not expose that concept.
- It keeps SF dependent on a CLI tool that is now itself a
  thin shim — moving the wrapper one layer up rather than
  removing it.

The compatibility shim is correct for legacy *software* SF
does not control. For SF's own code, going through it is
worse than going to nftables directly.

### Open vSwitch / OVN

Replace SF's VXLAN-mesh-plus-iptables dataplane with an
OVS-managed bridge controlled by an OVN northbound. We
reject this for this plan:

- It is a very large architectural change touching the
  entire network design, not a mechanism replacement.
- SF's manifesto values minimality; OVN is not minimal.
- OVS / OVN have their own operational characteristics
  (dataplane crashes, flow-table churn, controller HA)
  that SF would inherit.
- If SF ever wanted to support workloads that exceed
  what netfilter-plus-VXLAN handles well, this is the
  right destination — but that is its own future plan,
  not this one.

### eBPF dataplane

Attach eBPF programs to tc / XDP / socket hooks and skip
the netfilter framework entirely. Cilium-style. We reject
this here for the same scope reason as OVS/OVN, plus the
additional cost that eBPF expertise is a real operator
burden and the toolchain footprint (clang for verifier-
acceptable bytecode generation, kernel version gating,
verifier failures as a runtime concern) is high. A
candidate for "if SF ever needs per-workload microsecond-
latency network policy," not for this plan.

### Chosen direction

`pyroute2` for rtnetlink (link, addr, route, neigh,
netns) plus either pyroute2's nftables submodule or the
official `python-nftables` JSON interface for the firewall
rules. Phase 0 picks between the two nftables options.

## Open questions

1. **`pyroute2.nftables` vs `python-nftables`.** pyroute2's
   nftables submodule speaks netlink directly; python-nftables
   speaks JSON to the kernel via libnftables (which then
   speaks netlink). The pyroute2 path is one fewer dependency
   and one fewer process boundary; the python-nftables path
   is canonical and may match operator mental models better.
   Phase 0 decides based on API ergonomics and maintenance
   activity.
2. **sysctl handling.** Direct file writes to `/proc/sys/net`
   work but lose the "uniform mutation API" property. Phase 0
   chooses between direct writes, exec'ing `sysctl` for the
   narrow remaining cases, or in-process via netlink's
   `RTM_SETLINK` for the link-scoped sysctls only.
3. **Gratuitous ARP after floating-IP assignment.** No
   clean netlink answer. Options: open a raw socket and
   build an ARP frame in-process (~20 lines of Python),
   keep exec'ing `arping` for this one case, or rely on
   the kernel's automatic ARP behaviour on address
   addition (`arp_notify=1` already gets set per
   `privexec/util.py:214`). Phase 0.
4. **Privexec API redesign — narrow or broad?** A narrow
   reshape keeps the existing RPC names and changes only
   the internals. A broad reshape introduces typed RPCs
   (`AddNATRule`, `CreateBridge`, `EnsureVXLANInterface`,
   …) and removes the "run this command line for me"
   primitive entirely. Broad is a security improvement
   but a bigger compat surface to manage. With the
   daemon-split in scope (question 10), broad is the more
   natural choice because the new daemon's API is being
   defined fresh anyway. Phase 0 confirms.
5. **Network namespace handling.** Today privexec exec's
   `ip netns exec NS iptables …` to push rules into a
   namespace. pyroute2 has `NetNS` for namespace-scoped
   netlink operations; rtnetlink and nftables both work
   inside namespaces this way. Confirm the per-namespace
   socket-open cost is not pathological for SF's typical
   namespace count.
6. **Bridge attribute setting via `IFLA_BR_*`.** SF's
   current `brctl` calls set forward-delay, STP off, and
   ageing-time zero. All three map directly to rtnetlink
   bridge link attributes, but the encoding (nested
   netlink attributes inside `IFLA_INFO_DATA`) is fiddly.
   Phase 0 confirms pyroute2 exposes these cleanly.
7. **Migration strategy: per-rule, per-table, or wholesale.**
   nftables and iptables-legacy *can* coexist on the same
   kernel (different netfilter hooks), but it is fragile
   and confusing. Phase 0 decides whether to migrate one
   table at a time or do the whole switch in one phase
   once the implementation is ready.
8. **Failure mode surface.** netlink errors come back as
   structured `NLMSG_ERROR` with a kernel `errno`. The
   current code path produces stderr strings from CLI
   tools. The privexec gRPC error reply currently includes
   the captured stderr (per `privexec/main.py:186`). Phase 0
   decides on the structured-error reply shape.
9. **Testing strategy.** Unit tests need either a mockable
   netlink surface or network-namespace fixtures that
   exercise the real kernel. cluster_ci will eventually
   exercise real code paths, but per-PR feedback wants
   faster tests. Phase 0 decides between pyroute2's mock
   helpers, scoped network namespaces in CI, or both.
10. **Scope of the privexec split.** The new
    `sf-net-privexec` daemon owns network operations.
    What exactly counts as a network operation? Bridge,
    VXLAN, IP, route, neighbour, nftables — yes. Network-
    namespace creation and teardown — almost certainly yes.
    sysctl writes that target network parameters — probably
    yes, but the boundary is fuzzy. Phase 0 produces the
    operation inventory and a clean split.
11. **Authentication for the new daemon.** Today
    `sf-privexec` accepts gRPC on a local Unix socket (or
    loopback — confirm during phase 0); permission is
    implicit in who can reach the socket. After the split,
    `sf-net-privexec` could keep the same shape (Unix
    socket, file-mode authentication) or move to mTLS
    keyed to a `net-worker`-specific cert
    (`PLAN-embrace-tls.md`'s territory). Phase 0 decides
    whether to defer the mTLS work to embrace-tls or pull
    it in here.
12. **Naming and packaging of the new daemon.**
    `sf-net-privexec` is a working name. Final name plus
    systemd unit, package layout, and the deployer-side
    install for the new daemon are phase 0 outputs.

## Execution

Provisional. Phase 0 may re-cut the phase table.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-replace-exec-with-netlink-phase-00-decisions.md | Not started |
| 1. Introduce `pyroute2` dependency; port `ip link / addr / route / neigh` operations | PLAN-replace-exec-with-netlink-phase-01-rtnetlink.md | Not started |
| 2. Port bridge management off `brctl` via `IFLA_BR_*` | PLAN-replace-exec-with-netlink-phase-02-bridges.md | Not started |
| 3. Port iptables rules to nftables via netlink, table by table | PLAN-replace-exec-with-netlink-phase-03-nftables.md | Not started |
| 4. Stand up `sf-net-privexec` with a typed network API; net-worker becomes its only client | PLAN-replace-exec-with-netlink-phase-04-net-privexec.md | Not started |
| 5. Drop `CAP_NET_ADMIN` from `sf-privexec`; remove the network RPCs from its surface | PLAN-replace-exec-with-netlink-phase-05-privexec-shrink.md | Not started |
| 6. Close out the `sf-net` direct-exec sites and the remaining narrow corners (sysctl, arping) | PLAN-replace-exec-with-netlink-phase-06-cleanup.md | Not started |

Notes on sequencing:

- **Phase 0 is decisions.** No code. Output is appended to
  this master plan and the phase table is re-cut.
- **Phase 1 is the easiest canary and contains the single
  highest-leverage piece of work in the plan.** `ip link /
  addr / route / neigh` map almost one-for-one to pyroute2
  calls, the existing semantics survive intact, and the
  wins (typed errors, no stdout parsing, no process fork)
  show up immediately. **VXLAN bridge FDB management is
  carved out as a dedicated subtask within this phase** —
  same rtnetlink family as `ip neigh` and so naturally
  grouped, but the dominant per-instance-start fork source
  in production traces and the largest single reduction in
  process churn that this plan delivers. The phase plan
  should treat the FDB subtask as its own commit and its
  own review pass, and capture before/after fork counts
  for an N-node ensure_mesh on a controlled test cluster
  as evidence the optimisation is real.
- **Phase 2 (bridge attributes)** is small but its own
  step because the rtnetlink encoding for `IFLA_BR_*` is
  the first non-trivial netlink-message-construction work
  and worth landing alone so it can be reviewed carefully.
- **Phase 3 is the largest.** nftables rules replace the
  iptables rules currently installed in
  `privexec/main.py:198-258` and the `daemons/network/main.py:144-150`
  egress bridge setup. The atomic-transaction property of
  nftables is the headline correctness improvement and the
  phase plan should call out which rule groups must
  commit together.
- **Phase 4 stands up the new daemon.** `sf-net-privexec`
  is implemented as a separate process with the typed
  network API decided in phase 0, internally backed by the
  pyroute2 / nftables work from phases 1-3. `net-worker`
  is ported to call it instead of `sf-privexec` for
  network operations. The old privexec RPC names continue
  to work during this phase so the migration is
  reversible; nothing is removed yet. The new daemon
  starts holding `CAP_NET_ADMIN`; the old one keeps it
  too, temporarily.
- **Phase 5 shrinks the old daemon.** Once phase 4 has
  bedded in and `net-worker` is exclusively using
  `sf-net-privexec`, the network RPCs are removed from
  `sf-privexec` and `CAP_NET_ADMIN` is dropped from its
  systemd unit. The end state: two daemons, each with
  smaller blast radius than the original combined one.
  This phase is the security payoff; do not skip it.
- **Phase 6 is the sweep.** Anything left exec'ing a
  network command after phases 1-5 gets ported or
  explicitly documented as "intentionally still exec'd
  because…" — for example, an in-process ARP-frame send
  might be deemed not worth the complexity for one site.

## Dependencies on other plans

- **`PLAN-network-facade.md`** is in progress in a separate
  work session and **must land before this plan starts**.
  Two reasons. First, network-facade settles *who* mutates
  network state (the single-mutator `net-worker` pattern)
  while this plan changes *how* the mutator does the
  mutation; doing them in parallel would fight over the
  same files. Second, the privilege-separation phases (4
  and 5) depend on the "exactly one caller of network
  privilege" property that network-facade establishes —
  without it, splitting `sf-privexec` would not actually
  shrink the caller set and the security payoff would be
  weaker.
- **`PLAN-remove-primary.md`** is independent. Network code
  lives in `sf-net` and `sf-privexec`, neither of which is
  affected by primary-node removal. This plan can land
  before, during, or after remove-primary.
- **`PLAN-embrace-tls.md`** is independent. mTLS for the
  privexec gRPC channel is orthogonal to the privexec API
  reshape, though phase 4 of this plan and the embrace-tls
  work both touch privexec's wire surface and should
  coordinate so the reshape happens once.

This plan sits in the **"not strictly ordered"** group in
`index.md` along with `PLAN-embrace-tls.md`,
`PLAN-sticky-transfers.md`, and the not-yet-drafted
eventlog / network-node-failover / OpenTelemetry threads.
The triage decision for which of these lands first is
deferred until `PLAN-remove-primary.md` is close to
landing.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors the other plans:
plan in the management session, spawn a sub-agent per
implementation step, review in the management session, fix
or retry, commit when satisfied.

Phase 0 (decisions) is **opus at high effort** — the
choices propagate through every later phase. Phase 3
(nftables) is **opus at high effort, worktree isolation**
because the transactional semantics and rule-equivalence-
during-migration are subtle and getting it wrong in a way
that silently passes tests is plausible. Phases 1, 2, 4, 5
are likely **sonnet at medium effort** once phase 0 and
phase 3's patterns are established.

### Step-level guidance

Each phase plan should include a step table with effort,
model, isolation, and brief columns in the format used by
`PLAN-remove-primary.md`.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] No remaining `subprocess` / `Popen` invocations of
      `iptables`, `ip`, `brctl`, `sysctl`, or `arping` in
      the server tree after the phase completes
      (verified by grep, not by assertion).
- [ ] Netlink errors surface with the kernel errno
      attached, not as opaque "operation failed" strings.
- [ ] cluster_ci continues to pass end-to-end — this work
      changes the bytes-on-the-wire-to-the-kernel but
      should not change observable network behaviour.
- [ ] For the nftables phase specifically: rules installed
      by the new path are equivalent (verified by
      `nft list ruleset` snapshot comparison against a
      pre-migration capture, on a controlled test cluster)
      to the rules the previous iptables path installed.
- [ ] The privexec gRPC API change (phase 4) does not
      regress any caller. Confirm by grep that every old
      RPC name is either still present or has every caller
      ported.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `grep -rn 'iptables\|brctl\|locate_command.*ip\b' shakenfist/`
  returns no live exec sites for network commands. Remaining
  matches (if any) are comments, tests, or documented
  intentional exceptions.
* `pyroute2` is a project dependency; `iptables`, `brctl`
  (and their package dependencies) are no longer required
  on hypervisor hosts at runtime.
* Firewall rules are installed as nftables tables and
  chains, and `nft list ruleset` on a running hypervisor
  shows the SF rule set in nftables form.
* The deployer no longer installs `iptables` / `brctl` /
  `iproute2-extras` on hypervisor nodes (the `ip` binary
  itself remains useful for operators and is left alone).
* `sf-net-privexec` exists as a separate daemon holding
  `CAP_NET_ADMIN`, exposing a typed network-operation
  gRPC API, with `net-worker` as its only client.
* `sf-privexec` no longer holds `CAP_NET_ADMIN` and no
  longer exposes network RPCs. The two daemons have
  strictly smaller blast radii than the original combined
  one.
* cluster_ci passes end-to-end on the new path.
* `pre-commit run --all-files` passes.
* Documentation in `docs/operator_guide/` describes the
  nftables rule layout for operators wanting to inspect
  or troubleshoot rules on a host.

### Future work

- **Open vSwitch / OVN dataplane.** A future direction if SF
  ever needs network features that exceed what netfilter-
  plus-VXLAN can deliver. Not on the roadmap.
- **eBPF policy enforcement.** Same character; out of
  scope.
- **Cross-host nftables ruleset coherence.** Once rules
  are in nftables, the per-host rule sets can be diffed
  via `nft list ruleset` cheaply. An operational tool to
  surface drift between hypervisors becomes feasible and
  might be worth a small future plan.
- **Removing the legacy iptables kernel modules from
  deployer-managed hypervisors.** Possible after the
  migration; operators may prefer keeping them around as
  break-glass. Worth deciding deliberately later, not now.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
