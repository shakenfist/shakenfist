# Lease-based per-network carrier model with VIP advertisement

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the network daemon (`shakenfist/daemons/network/`), the
existing "network node" model (where the singleton role is
configured and which daemons are gated on it), the floating
IP allocation and SNAT programming, the DHCP/DNS state
managed via dnsmasq, the cluster-lock leasing pattern in
`shakenfist/locks.py`, and the leader-election pattern that
`PLAN-remove-primary` phase 5 introduces for `sf-database`.
Ground your answers in what the code actually does today.

Where a question touches on external concepts (BGP for VIP
advertisement, gratuitous ARP / VRRP for L2 failover, the
operational differences between routed and bridged cloud
deployments, embedded BGP speakers such as GoBGP, BIRD, FRR),
research as needed to give a confident answer. Flag any
uncertainty explicitly.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, especially the network subsystem and the existing
network node role. Consult `CLAUDE.md` for build commands,
project conventions, the cluster-lock leasing pattern, the
single-mutator direction in `PLAN-network-facade`, and the
exec-to-netlink direction in `PLAN-replace-exec-with-netlink`.

This plan is a **placeholder**. It captures intent and the
known open questions and is intentionally light on detail.
Phase 0 will resolve the open questions into a decisions
section and the phase table below will be re-cut accordingly.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit.

## Situation

Shaken Fist's network node is the last named singleton role
in the architecture. After `PLAN-remove-primary` phase 5
makes `sf-database` electable, every other daemon will
either run on every node or elect a leader from a pool. The
network node is the holdout: it is configured (not elected),
hosts every virtual network's egress floating IP, programs
every network's SNAT / DHCP / DNS, and is a single failure
domain for external connectivity across the entire cluster.

The original `PLAN-remove-primary` "future work" stub for
this was "network node failover, with operator-provided
keepalived / corosync / BGP." That framing is honest about
the L2-vs-L3 mechanism question but understates the
architectural opportunity. A richer answer:

**Smear the carrier role across a configurable pool of
nodes, per network.** Each network independently leases its
egress floating IP and gateway role to one carrier from an
eligible pool. Different networks can have different
carriers. Total egress bandwidth scales with the pool
instead of bottlenecking at one node. Blast radius shrinks
to "the networks currently leased to the failed carrier,"
not "every network in the cluster." Locality becomes
possible — a network whose VMs mostly live on node X can
have its carrier on node X, avoiding the mesh hop for the
common-case traffic.

The architectural framing that ties this together is
**"network-node state is data, the carrier is a renderer."**
Every piece of state that today lives as configured kernel
state on the singleton network node — SNAT rules, DHCP
leases, DNS records, the DNAT'd service ports from
`PLAN-network-service-ports`, the floating-IP itself —
becomes a row (or rows) in MariaDB. The carrier is a
process that, on lease acquire, reads its leased networks'
state and materialises it into kernel state. On lease loss,
it tears that state down. On failover, the new carrier
reads the same data and reprograms. Survivability of
in-progress state across failover is a property of the
data, not of any kernel-state-sync handshake.

Two advertisement mechanisms cover the operational
landscape:

- **BGP advertisement** — each carrier advertises the
  floating IPs it currently holds via BGP, either through
  an embedded speaker (GoBGP / BIRD / FRR managed by SF) or
  by configuring an operator-external speaker. Failover
  converges in seconds. Required for L3-routed deployments
  (cloud, modern on-prem); requires operator network gear
  to peer.
- **L2 advertisement** (gratuitous ARP, optionally VRRP-
  style) — the new carrier fires GARP for the floating IP;
  L2 switches converge. No BGP required. Works only when
  all candidate carriers share an L2 segment with the
  operator's upstream router; slower failover; doesn't
  cross L3 boundaries.

Phase 0 decides which modes ship in v1 and how the
operator selects between them.

A future direction worth naming explicitly so the
eligibility model is shaped to handle it: **mapping
external VLANs into specific virtual networks.** That
makes carrier eligibility per-network and operator-
provisioned ("this carrier has VLAN 42 on its trunk; only
networks bound to VLAN 42 can be carried here"). This plan
does not implement VLAN mapping — that's its own future
plan — but the eligibility-filter design must not paint
itself into a corner that VLAN trunking would force a
rewrite of.

## Mission and problem statement

Shaken Fist's network role is no longer a configured
singleton. Each network leases its gateway role to one
carrier from an operator-configured pool. Carriers
materialise network state from MariaDB into kernel state on
lease acquire; tear it down on lease loss. Floating IPs are
advertised via BGP or L2 depending on operator choice.
Locality-aware placement prefers carriers where the
network's instances already run. The architecture has no
named-singleton roles left.

Concretely, after this plan lands:

- Operators designate a **carrier pool** — the set of
  nodes eligible to carry virtual networks. Pool membership
  is config-driven; defaults to all nodes for small
  deployments, narrows to a privileged subset for larger
  ones where only some nodes have the network
  configuration to be carriers.
- Per-network **carrier leases**, backed by the
  `cluster_locks` leasing pattern (refresh-while-alive,
  steal-on-expiry, `lost_event` signal on confirmed loss).
- A **carrier process** on each pool member that watches
  for leased networks, materialises their state on acquire,
  and tears it down on loss. The process is a renderer:
  state flows from MariaDB to kernel, not the other way.
- All network-node-resident state lives in MariaDB:
  - The egress floating IP itself (as an allocation,
    via `PLAN-generic-allocator`).
  - DNAT'd service ports (via
    `PLAN-network-service-ports`).
  - DHCP leases (so a carrier change does not lose the
    lease database).
  - DNS records.
  - SNAT-policy state (derived from network config plus
    the egress IP allocation, not separately persisted).
- **Locality-aware placement** for carrier leases. Filter
  pool members by eligibility; score by locality (instances
  on this network running on this carrier) and load (number
  of networks already carried); tie-break by round-robin.
  Same conditional-INSERT pattern as
  `PLAN-scheduler-reservations`.
- **Pluggable advertisement**:
  - BGP via embedded speaker, OR
  - BGP via operator-external speaker (SF programs routes
    via the speaker's API), OR
  - L2 GARP / VRRP-style, OR
  - Manual (operator handles it; SF emits events but
    doesn't program advertisement)
- **Cluster-wide network work queue uses assignment-at-
  enqueue routing.** The enqueuer looks up the network's
  current carrier and stamps `assigned_node_uuid` on the
  queue row at insert time. Carriers pull
  `WHERE assigned_node_uuid = me AND status = pending` —
  a single equality predicate, cost independent of how many
  networks a carrier ends up holding. Carrier change
  triggers a re-route step on lease acquire: the new
  carrier issues `UPDATE ... WHERE network_uuid = ? AND
  status = pending SET assigned_node_uuid = me`, bounded by
  in-flight queue depth. Chosen over filter-on-read
  (`WHERE network_uuid IN (...)`) — which works but has a
  cost-grows-with-fan-out shape — and over hash-partitioned
  queues, which carry rebalance overhead this design
  doesn't need.
- The legacy "network node" config is removed (or kept as
  the degenerate-single-carrier case for small
  deployments).

The principle is: **every persistent network role is a
leased rendering of data, not a configured kernel state.**

## Open questions

This plan is the largest of the three in this thread and
has the most open questions. Phase 0 will resolve at least:

1. **Carrier pool configuration shape.** Ansible group
   (parallels how `etcd_master` is configured today)?
   Per-node config flag (`CARRIER_ELIGIBLE=true`)?
   A node attribute in MariaDB? Phase 0 picks one, with
   the criterion that operator-driven changes to pool
   membership should be picked up without restarts.
2. **Lease TTL, refresh cadence, and `lost_event`
   semantics.** Mirror `cluster_locks` (60s expiry, 20s
   refresh) or pick different numbers based on the
   failover-time requirements? The advertisement mode
   matters: BGP convergence is seconds and tolerates short
   leases; L2 GARP is faster but flap-prone if the lease
   is too short.
3. **Advertisement mode selection.** Per-cluster default?
   Per-network override (some networks BGP-advertised,
   others L2)? Phase 0 confirms whether mixing modes in
   one cluster is a feature or a footgun.
4. **Embedded BGP speaker choice.** GoBGP (mature, library-
   friendly, easy embedding), BIRD (standard but
   process-oriented), FRR (very fully featured, heaviest).
   If embedded BGP ships in v1, phase 0 picks one and
   documents the reasoning. If embedded BGP is v2 and v1
   ships operator-external BGP only, phase 0 says so.
5. **L2 advertisement specifics.** GARP only (simple, no
   election, just announce on acquire) or VRRP-style
   (peers elect the master, advertise via VRRP). GARP-only
   is cheaper but requires SF's lease to be the source of
   truth (since there's no L2 election to break ties).
   VRRP adds a second consensus mechanism that has to
   agree with SF's lease, which is risky. Phase 0 picks.
6. **DHCP state across carrier change.** Today dnsmasq's
   lease file is a local file on the network node. If the
   carrier changes, naive failover loses the lease
   database — clients get new IPs on renewal. Options:
   (a) persist leases to MariaDB and have the new carrier
   rebuild dnsmasq's lease file at acquire; (b) accept
   transient DHCP problems on failover and document; (c)
   use a dnsmasq config that survives reload more
   gracefully. Phase 0 picks; (a) is the right answer for
   "carrier failure is invisible" but is the most work.
7. **DNS state across carrier change.** Same shape as
   DHCP. Records are derived from network config plus
   instance state, so probably rebuildable from data
   without persistence. Confirm.
8. **In-flight TCP across carrier change.** A long-lived
   connection through DNAT survives carrier failover only
   if the new carrier installs identical conntrack state.
   Almost certainly **not** in scope for v1; document the
   operator-visible behaviour clearly ("active TCP
   sessions through DNAT'd service ports may reset on
   carrier failover; SSH or browser-console sessions
   typically reconnect transparently"). Phase 0 confirms.
9. **VLAN-trunking forward compatibility.** Eligibility
   filtering today is "is this node in the carrier pool?"
   Future VLAN-trunking adds "...and does this node have
   the right VLAN on its trunk?" The eligibility model
   must not bake in "all carriers are equivalent." Phase
   0 designs the eligibility-filter query to accept future
   per-network constraints without rewrite. Out of scope
   to *implement* VLAN trunking here; in scope to *not
   block* it.
10. **Migration from singleton network node.** Rolling
    cutover: the existing network node is initially the
    only pool member. Operators expand the pool, networks
    rebalance via lease churn or operator-triggered
    rebalance. Phase 0 designs the migration story and
    confirms it does not require simultaneous downtime
    across all networks.
11. **Single-node-cluster behaviour.** Carrier pool of
    one degenerates to "current behaviour, but
    lease-managed." Confirm this case is not pessimised
    (no unnecessary lease churn, no false-failover
    detection in a one-member pool).
12. **Interaction with `PLAN-network-facade` and
    `PLAN-replace-exec-with-netlink`.** The carrier is
    exactly the single-mutator that `PLAN-network-facade`
    is shaping `net-worker` to be — but per-carrier-node,
    not cluster-wide singleton. Phase 0 confirms whether
    `net-worker` remains a cluster-wide singleton with
    the carrier role layered on top, or whether
    `net-worker` itself shards per carrier. The
    `sf-net-privexec` daemon from
    `PLAN-replace-exec-with-netlink` runs on every pool
    member so this composes naturally; confirm.
13. **Floating-IP-as-allocation.** The egress floating IP
    is currently a per-network attribute. Once it's an
    allocation in `PLAN-generic-allocator`, the per-
    network attribute can be derived from the allocation
    or removed in favour of querying the allocation
    directly. Phase 0 picks.
14. **Locality scoring inputs.** "Instances on this
    network running on this carrier" is the obvious
    locality signal. Other plausible inputs: current
    number of networks the carrier leases (balance);
    current egress bandwidth on the carrier (load);
    operator-declared affinity hints. Phase 0 picks the
    initial scoring function with room to grow.
15. **Network-node config removal.** Once carriers ship,
    the legacy `network_node` ansible group / config flag
    is at best dead code, at worst an attractive nuisance
    for misconfiguration. Phase 0 decides whether v1
    removes it or whether removal is a separate cleanup
    plan.

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-network-carrier-model-phase-00-decisions.md | Not started |
| 1. Carrier pool configuration and node-capability declaration | PLAN-network-carrier-model-phase-01-pool.md | Not started |
| 2. Per-network carrier lease primitive | PLAN-network-carrier-model-phase-02-lease.md | Not started |
| 3. Carrier renderer process | PLAN-network-carrier-model-phase-03-renderer.md | Not started |
| 4. SNAT and floating-IP programming via the renderer | PLAN-network-carrier-model-phase-04-snat.md | Not started |
| 5. DNAT'd service ports via the renderer (carrier-side hookup) | PLAN-network-carrier-model-phase-05-service-ports.md | Not started |
| 6. DHCP state persisted and rebuilt on carrier change | PLAN-network-carrier-model-phase-06-dhcp.md | Not started |
| 7. DNS state via the renderer | PLAN-network-carrier-model-phase-07-dns.md | Not started |
| 8. BGP advertisement mode | PLAN-network-carrier-model-phase-08-bgp.md | Not started |
| 9. L2 / GARP advertisement mode | PLAN-network-carrier-model-phase-09-l2.md | Not started |
| 10. Migration from singleton network node | PLAN-network-carrier-model-phase-10-migration.md | Not started |
| 11. Operator documentation for VIP failover and pool sizing | PLAN-network-carrier-model-phase-11-docs.md | Not started |

## Dependencies on other plans

- **Hard dependency on `PLAN-generic-allocator`.** The
  carrier lease, the floating-IP allocation, the DNAT'd
  service ports — all use the generic allocator primitive.
- **Hard dependency on `PLAN-network-service-ports`.** The
  carrier reprograms DNAT'd service ports on lease
  acquire; this plan does not invent its own DNAT
  mechanism.
- **Tight coherence with `PLAN-network-facade` and
  `PLAN-replace-exec-with-netlink`.** The carrier is the
  natural home of single-mutator network operations and of
  netlink-driven privileged programming. Either both land
  before this plan does, or this plan accepts that the
  carrier renderer is the first non-trivial consumer of
  those plans' interfaces and reviews the interface fit
  during its own phase 0.
- **Parallel-track to `PLAN-remove-primary`.** Both reduce
  named-singleton roles, but they target different
  singletons (database vs network) with different
  mechanisms (election vs lease-and-render). Either order
  is fine; neither blocks the other.
- **Compatible with `PLAN-embrace-tls`.** Carrier-to-
  carrier and carrier-to-`sf-database` traffic flows over
  the same mTLS channels mTLS lands across; this plan does
  not interact with TLS material.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md` and the other placeholder plans.

This is a deeply architectural plan touching production
network state, failover semantics, and live client traffic.
**Every implementation phase should default to opus at high
effort.** The advertisement-mode phases (8 and 9) and the
DHCP-persistence phase (6) are the most subtle and deserve
the most careful review.

### Planning effort

The master plan itself is **high effort** despite being a
placeholder, because the open-questions list is the
load-bearing part of the plan and getting any of them wrong
shapes a lot of subsequent work. Phase 0 (research and
decisions) is the highest-effort decisions document in this
thread.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] Carrier failover is exercised by an end-to-end test
      that kills the current carrier and confirms the new
      carrier reprograms all of the network's state from
      data. Not stubbed.
- [ ] DHCP lease survival across carrier change is
      exercised by a test that has a live client lease
      before the failover and confirms the lease survives
      it.
- [ ] At least one advertisement mode (BGP or L2) is
      exercised in `deploy/shakenfist_ci/cluster_ci_tests`
      end-to-end, with a real upstream / switch in the path.
- [ ] Smearing is exercised: a multi-network setup
      confirms different networks end up on different
      carriers and that the locality-scoring heuristic
      behaves as designed.
- [ ] The eligibility-filter design is reviewed against
      the future VLAN-trunking constraint to confirm no
      rewrite is forced by it.
- [ ] Single-node-cluster behaviour is exercised to
      confirm no unnecessary lease churn or false
      failover.
- [ ] Object cleanup (`hard_delete()`) on a network
      releases all its carrier-side state cleanly.
- [ ] mypy coverage for the new carrier renderer is good
      from day one; this is new code, no excuse for
      thinly-typed surfaces.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* No virtual network is bound to a specific configured
  "network node." Every network leases its carrier role
  from an eligible pool.
* Carrier failure causes the carrier's leased networks to
  fail over to other pool members within the documented
  failover-time bound, without operator intervention.
* Total egress bandwidth scales with the carrier pool, not
  with one node.
* All network-node-resident state — egress IP, SNAT, DNAT'd
  service ports, DHCP leases, DNS records — lives in
  MariaDB and is rendered into kernel state by the
  carrier.
* At least one advertisement mode (BGP or L2) ships in v1
  and is verified end-to-end in `deploy/shakenfist_ci/cluster_ci_tests`.
* Locality-aware placement demonstrably prefers carriers
  where the network's instances run, with documented
  scoring inputs.
* The eligibility-filter design accepts future per-network
  hard constraints (e.g. VLAN-trunking) without requiring
  a rewrite.
* Migration from the singleton network-node configuration
  to a smeared carrier pool is exercised and documented.
* `pre-commit run --all-files` passes.

### Future work

- **VLAN trunking from external networks into virtual
  networks.** The motivating future feature that drives
  the eligibility-filter design here. Wants its own plan
  once the carrier model is in place — at minimum it adds
  a `node_network_capabilities` table or equivalent, a
  carrier-side configuration for the trunk, and the
  per-network eligibility constraint in the placement
  query.
- **Carrier rebalance.** Once smearing is in place,
  operator-triggered rebalance ("re-pick carriers for all
  networks using current locality / load data") is a
  natural follow-on. Out of scope here.
- **Per-carrier metrics.** Bandwidth, lease count, lease
  churn rate, advertisement health. Probably falls out of
  the OpenTelemetry thread.
- **Cross-carrier conntrack sync for stateful failover.**
  Out of scope here, would land in a separate plan with a
  hard "do we actually want this" decision up front
  (conntrack sync is operationally heavy).
- **BGP route filters and AS-path policies.** If embedded
  BGP ships, operator-controllable route filtering is a
  natural follow-on. Out of scope here.

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
