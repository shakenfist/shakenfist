# Per-network DNAT'd service ports for managed services

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the existing network model (`shakenfist/network/network.py`,
`shakenfist/network/interface.py`), how the network's egress
floating IP is allocated and where SNAT rules are programmed
(the network daemon under `shakenfist/daemons/network/`), the
existing managed-executable pattern that dnsmasq uses (look
for managed-process supervision code under `shakenfist/`
broadly — confirm where the managed-exec abstraction lives
today), and the privileged-execution daemon
(`shakenfist/daemons/privexec/`). Ground your answers in
what the code actually does today.

Where a question touches on external concepts (iptables /
nftables DNAT, TLS with SAN-on-IP versus per-port TLS,
token-bearer security patterns), research as needed to give
a confident answer. Flag any uncertainty explicitly.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview and the network subsystem. Consult `CLAUDE.md` for
build commands, project conventions, the cluster-lock leasing
pattern, and the data-stored-in-MariaDB pattern.

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

Shaken Fist networks already have a floating IP allocated
for SNAT egress. That IP is programmed on the network node,
provides outbound connectivity for the VMs on the network,
and is the natural client-visible address for that network.

There is no current mechanism to expose **transient, per-
session services** at that IP. Several near-term and future
features want exactly this:

- **Managed web consoles** (`ryll --web` as a managed
  executable per console session). A user requests a web
  console for an instance; SF spawns a managed ryll process
  that proxies SPICE to a browser; SF gives the user a URL
  to connect to. The URL needs to land on something SF
  controls without giving the user a per-node URL.
- **Single-shot transfer agents.** The thought-bubble from
  `PLAN-sticky-transfers` about replacing the streaming-proxy
  path with a small per-session managed agent. The client
  needs a stable address for the agent for the duration of
  the transfer.
- **Managed VPN endpoints.** A future feature: a network
  has a managed WireGuard endpoint that bridges the virtual
  network to a remote resource ("this virtual network has a
  managed VPN link to that resource over there"). The
  WireGuard server is a long-lived managed process on the
  network's carrier; its public endpoint needs to be at the
  network's egress IP.
- **Other managed network services** (pcap capture for a
  network, managed IDS, netflow exporter, etc.) all share
  the same shape: a per-network service that needs an
  externally reachable address.

All of these want the same primitive: **allocate a port on
the network's egress floating IP, DNAT it to wherever the
managed service runs, hand the client `(ip, port, token)`,
reap the allocation on expiry or explicit release.**

The mechanism is structurally similar to how VXLAN IDs are
allocated today (pick an unused value from a range, claim
it atomically), except many-per-network instead of one-per-
network. With `PLAN-generic-allocator` in place, the
allocation itself is a one-line call to that primitive. The
remaining work is the DNAT programming, the token issuance,
the reaper, and the calling-site API.

Notably, the DNAT *target* might be on a different node from
the network's carrier. A managed-web-ryll for instance X
probably wants to run on the hypervisor hosting instance X
(SPICE channel locality), even though the network's egress
IP lives on the carrier. So the DNAT rule routes traffic
from `<egress-ip>:<port>` across the mesh to `<target-
node>:<target-port>`. The mesh hop is cheap (intra-cluster
bandwidth) and is the same primitive SF already uses for
inter-node VM traffic.

## Mission and problem statement

Shaken Fist exposes a per-network primitive for allocating
ports on a network's egress floating IP and DNAT'ing them to
arbitrary in-cluster targets, with token-based access
control, leased TTL, and automatic reaping. The primitive is
the building block for managed web consoles, managed
transfer agents, managed VPN endpoints, and any future
managed network service.

Concretely, after this plan lands:

- A `network_service_ports` allocation pool, backed by the
  `PLAN-generic-allocator` primitive, exists per network
  with a configurable port range.
- A small calling-site API:
  `allocate_service_port(network_uuid, service_type,
  target_host, target_port, ttl) -> (egress_ip, port,
  token)` and `release_service_port(allocation_uuid)`.
- The network daemon programs the DNAT rule on the network's
  carrier node when an allocation is created, and removes it
  when the allocation is released or expires.
- A reconciler ensures DB state and iptables / nftables
  state agree, repairing drift in both directions (orphan
  rules removed; missing rules reprogrammed).
- Tokens are short-lived bearer credentials with the same
  TTL as the allocation; expired tokens are rejected by the
  managed service.
- Allocations survive network-carrier failover: when a new
  carrier takes the lease (per `PLAN-network-carrier-model`),
  it reads all the network's allocations from the table and
  reprograms its iptables / nftables.

The principle is: **per-session externally-reachable
endpoints are operator-perimeter-clean (no per-node URLs to
clients) and built on the IP machinery the network already
has.**

## Open questions

This plan is light on detail because almost every concrete
decision depends on a phase 0 research pass. The open
questions include at least:

1. **Default port range per network.** Probably
   30000-60000 by default, operator-configurable. Phase 0
   confirms there is no collision with other in-cluster
   listeners on the carrier (the carrier's own services
   shouldn't bind into the same range).
2. **Networks without `provide_nat=True`.** A network with
   no NAT has no egress floating IP today. Options:
   (a) feature unavailable for non-NAT networks; (b) feature
   triggers auto-allocation of an egress IP on first use;
   (c) explicit operator opt-in per network. Phase 0 picks.
3. **TLS on a shared IP.** Multiple services share the same
   public IP with different ports. TLS-by-hostname (SNI)
   doesn't help across ports. Options: (a) self-signed
   certs per allocation with the token-in-URL providing
   bearer auth (the certificate's job is integrity, not
   identity); (b) SAN-on-IP from an operator-provided CA;
   (c) per-port-per-session ACME against a wildcard DNS
   zone the operator provisions. (a) is by far the cheapest
   and is probably right for the console/transfer-agent use
   cases; phase 0 confirms.
4. **Token model.** Bearer tokens in URL query, in
   Authorization header, or in cookie. URL query is the
   simplest for a one-shot web-console handoff (the URL is
   the credential), but tokens-in-URLs leak in proxy access
   logs. Authorization headers are cleanest but require the
   client to be a programmatic agent, not a browser-naive
   click-the-link flow. Phase 0 picks per service-type or
   confirms a single default with documented exceptions.
5. **DNAT cross-node hop.** When the DNAT target is on a
   different node from the carrier, the carrier DNATs to
   the target's mesh address. Phase 0 confirms there is no
   reverse-path-filtering or asymmetric-routing wrinkle
   that breaks this (the return path from target to client
   has to flow back via the carrier, not directly from
   target's network namespace).
6. **Two-stage cleanup correctness.** Order of operations
   for add: DB INSERT, then iptables rule program. Order
   for remove: iptables rule remove, then DB DELETE. A
   reconciler watches both directions for drift. Phase 0
   confirms this is the right ordering and that the
   reconciler's repair semantics are safe (no transient
   "rule installed but DB says it shouldn't be" window
   where a stale token could still authenticate).
7. **Reaper cadence and source of truth.** The cluster
   daemon's maintenance loop runs the reaper on a cadence
   that handles the minutes-to-hours TTL range typical
   here. Phase 0 picks the cadence and confirms the reaper
   is the only source of `release_service_port` calls
   other than explicit release.
8. **Cross-tenant isolation.** Per-network scope handles
   this by construction — a port on network A's egress IP
   only exists in network A's namespace. Phase 0 confirms
   no edge case (shared egress IP across networks for
   small deployments, etc.) breaks this assumption.
9. **Coupling to `PLAN-network-carrier-model`.** This plan
   has to be honest about which carrier the DNAT rule is
   programmed on. Phase 0 of either plan should establish
   the contract so this plan does not have to know the
   details of carrier lease handoff, just "this network is
   currently carried by node X, install the rule there."
10. **First caller and validation surface.** This plan ships
    infrastructure with no caller. Phase 0 picks whether
    the validation harness is (a) a minimal smoke-test
    managed-executable that echoes connections back ("did
    the port get allocated, did the DNAT route, did the
    token gate"), or (b) waits for the first real caller
    (managed-web-ryll, managed transfer agent) to land in
    its own plan and validates end-to-end then. (a) ships
    sooner and proves the mechanism in isolation; (b)
    avoids a throwaway harness.
11. **Audit / event logging.** Every allocation, release,
    and reaper-driven reap should produce an event. Phase 0
    picks event types and confirms the existing eventlog
    abstraction is the right write path.
12. **Operator threat-surface change.** Today the egress IP
    exposes outbound NAT and ICMP. After this plan,
    operators see listening TCP ports. Phase 0 produces
    operator-facing documentation that names this
    explicitly and the firewall implications.

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-network-service-ports-phase-00-decisions.md | Not started |
| 1. Pool registration with the generic allocator | PLAN-network-service-ports-phase-01-pool.md | Not started |
| 2. `allocate_service_port` / `release_service_port` API and token issuance | PLAN-network-service-ports-phase-02-api.md | Not started |
| 3. Carrier-side DNAT programming via the network daemon | PLAN-network-service-ports-phase-03-dnat.md | Not started |
| 4. Reaper and reconciler | PLAN-network-service-ports-phase-04-reaper.md | Not started |
| 5. Validation surface (smoke-test caller or first real caller) | PLAN-network-service-ports-phase-05-validate.md | Not started |
| 6. Operator and developer documentation | PLAN-network-service-ports-phase-06-docs.md | Not started |

## Dependencies on other plans

- **Hard dependency on `PLAN-generic-allocator`.** The pool
  is registered with that primitive; this plan does not
  reimplement allocation.
- **Tight coupling with `PLAN-network-carrier-model`.** The
  DNAT rule programming has to know which node is currently
  carrying the network. Phase 0 of both plans should
  establish the contract jointly. This plan does not block
  on the carrier-model plan — a single-node carrier (today's
  network-node behaviour) is a valid degenerate case — but
  the two plans together unlock the full smeared-carrier
  story.
- **Coherent with `PLAN-network-facade` and
  `PLAN-replace-exec-with-netlink`.** DNAT rule programming
  is exactly the kind of single-mutator operation those
  plans are shaping the network daemon around. This plan
  programs rules *through* whatever interface those plans
  settle on, not around them.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md` and the other placeholder plans.

The DNAT programming and reconciler phases (3 and 4) touch
production network state and should be skewed toward
**opus at high effort**. Phases 0-2 are foundational and
also benefit from high effort for the API-shape decisions.
Phases 5-6 are lower stakes.

### Planning effort

The master plan itself is **medium effort** — it's a
placeholder converging on a clear direction. Phase 0
(research and decisions, especially the TLS-and-token
model and the carrier-coupling contract) is high effort.
Subsequent phases will be re-evaluated once phase 0 lands.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] Concurrent allocations against the same network's
      port pool do not collide (exercised by test, not
      asserted in docs).
- [ ] Two-stage cleanup correctness is exercised by tests
      that inject failures between the DB and iptables
      stages and confirm the reconciler repairs both
      directions.
- [ ] Reaper-driven release leaves no stale iptables
      rules and no stale DB rows after a TTL elapses.
- [ ] Carrier failover (simulated where carrier-model is
      not yet shipped) leaves all of the network's
      allocations correctly reprogrammed on the new
      carrier.
- [ ] Token expiry is enforced by the managed service
      side, not just the allocator side — expired tokens
      cannot drive traffic through an in-place DNAT rule.
- [ ] Object cleanup (`hard_delete()`) on a network
      releases all its service port allocations.
- [ ] mypy coverage for the new API surface is good from
      day one.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `allocate_service_port` and `release_service_port` exist as
  the single calling-site API for transient externally-
  reachable per-network services.
* Allocations are atomic via the generic allocator; DNAT
  rules are programmed via the network daemon's single
  mutator; tokens are short-lived and bound to the
  allocation TTL; the reaper cleans both DB and iptables on
  expiry.
* The reconciler detects and repairs drift in both
  directions and is exercised by tests.
* The validation surface (smoke-test or first real caller,
  per phase 0 decision) demonstrates an end-to-end allocate
  / connect / token-gate / reap cycle.
* Operator documentation describes the threat-surface
  change (listening ports on the egress IP), the firewall
  implications, and the per-network port range
  configuration knob.
* Functional coverage under
  `deploy/shakenfist_ci/cluster_ci_tests` exercises the allocation
  primitive end to end.
* `pre-commit run --all-files` passes.

### Future work

- **Managed web consoles via `ryll --web`.** The
  highest-priority follow-on caller. Wants its own plan
  once the primitive exists. Per-instance, per-session,
  short-lived, token-gated; the natural first user-visible
  application of the primitive.
- **Single-shot per-session transfer agents.** The shape
  floated in `PLAN-sticky-transfers` discussion as an
  alternative to cookie-based stickiness. Whether to
  pursue it depends on the OTel measurements that
  `PLAN-sticky-transfers` is deferred on; if pursued, this
  primitive is the foundation.
- **Managed WireGuard endpoints per network.** "This
  virtual network has a managed VPN link to that resource
  over there." A managed long-lived WireGuard server on
  the network's carrier, with its public endpoint
  allocated through this primitive. The TTL semantics
  shift from per-session-minutes to per-link-lifetime
  (operator-controlled), but the allocation mechanism is
  the same.
- **Managed network observability** (per-network pcap
  capture, netflow exporter, managed IDS). Same shape:
  long-lived managed process, externally-reachable
  endpoint allocated via this primitive.
- **Operator-facing port-management UI / API.** Listing
  current allocations per network, manually releasing
  stuck ones, etc. Out of scope here; trivial to add
  once the table is the source of truth.

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
