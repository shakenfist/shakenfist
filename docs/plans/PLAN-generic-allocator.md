# Unify finite-resource allocation onto one primitive

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the existing allocators — VXLAN ID allocation in
`shakenfist/network/network.py` (`Network.allocate_vxid` and
its retry-on-IntegrityError pattern in `Network.new`), console
and VDI port allocation in
`shakenfist/instance.py` (`_allocate_console_port` and
`allocate_instance_ports`), vsock CID allocation in
`shakenfist/instance.py` (`_allocate_vsock_cid` and its use of
a global `ClusterLock`), MAC address generation in
`shakenfist/util/network.py` (`random_macaddr`), and the IPAM
reservation pattern in `shakenfist/ipam.py` and the
`ipam_reservations` table — and confirm the exact mechanism
each one uses. Ground your answers in what the code actually
does today.

Where a question touches on external concepts (database
isolation levels, the conditional-INSERT idiom under MariaDB
/ InnoDB, allocator-design tradeoffs between random-retry and
deterministic-scan strategies), research as needed to give a
confident answer. Flag any uncertainty explicitly.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture overview
and the existing data-stored-in-MariaDB pattern. Consult
`CLAUDE.md` for build commands, project conventions, the
existing "push filtering down to the SQL layer" rule, and the
cluster-lock leasing pattern in `shakenfist/locks.py`.

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

Shaken Fist today implements "pick an unused value from this
finite pool" five different ways, with five different
correctness models:

| Allocator | Range | Mechanism | Correctness |
|-----------|-------|-----------|-------------|
| VXLAN ID (`network/network.py:258`) | 1..16,777,215 | random + UNIQUE constraint on `networks.vxid` + retry on IntegrityError | DB UNIQUE |
| Console / VDI ports (`instance.py:1029`) | 30000..50000, **per node** | random + `mariadb.get_consumed_ports_for_node()` + `socket.bind()` to verify locally | hybrid; per-node scoping bounds the race |
| vsock CID (`instance.py:1385`) | 3..2^32-1 | random + `mariadb.is_vsock_cid_in_use()` under a **global** `ClusterLock` | global lock (heavier than needed) |
| MAC address (`util/network.py:261`) | random 52:54:xx:xx:xx:xx | pure random, no uniqueness check | probabilistic only — not actually correct |
| IPAM IPs (`ipam.py` + `ipam_reservations`) | per-network CIDR | dedicated allocation table | atomic via the table |

Three of these (VXLAN, console ports, vsock CID) are the same
conceptual operation reimplemented three times with three
different correctness models. MAC allocation isn't safe — it
relies on the birthday-paradox math working out, with no
detection if it doesn't. IPAM is already the right shape but
is CIDR-specific and is not a candidate for generalisation in
this plan.

The N-implementations-of-one-pattern problem is also
**spreading**, not stable. The scheduler-reservations plan
introduces a sixth (capacity reservation on a node). The
network-service-ports plan introduces a seventh (DNAT'd ports
on a network's egress IP). The network-carrier-model plan
introduces an eighth (carrier lease per network). Each will
land with its own ad-hoc implementation unless the foundation
is in place first.

The clean answer is one primitive. A `resource_pool_allocations`
table with `(pool_name, value, owner_type, owner_uuid,
allocated_at, expires_at NULL)`, UNIQUE on `(pool_name,
value)`, with a conditional-INSERT allocator (the same
pattern phase 0 of `PLAN-scheduler-reservations` is exploring).
Per-pool policy (range, allocation strategy, leased vs
permanent) is configured per pool name. Existing ad-hoc
allocators are ported one by one with no behaviour change
visible to callers.

## Mission and problem statement

Shaken Fist has one allocator. Every "pick an unused value
from a finite pool" call site uses the same primitive against
the same table, with per-pool policy declared declaratively.
The three reimplementations are gone, MAC allocation becomes
actually-correct rather than probabilistic, and the future
plans that want this primitive (scheduler-reservations,
network-service-ports, network-carrier-model) build on a
foundation that already exists.

Concretely, after this plan lands:

- A `resource_pool_allocations` table exists with the
  schema above, indexed for fast pool-scoped scans and
  expiry-driven reaping.
- A small primitive — `allocate_from_pool(pool_name,
  owner_type, owner_uuid, ttl=None) -> value` and
  `release_allocation(allocation_uuid)` — that implements
  the conditional-INSERT-with-retry idiom in one place.
- Per-pool policy (value range, allocation strategy,
  permanent vs leased) is registered declaratively, probably
  as a module-level config or a small registry, not by
  scattering hard-coded ranges across the codebase.
- VXLAN ID allocation uses the new primitive. The UNIQUE
  constraint on `networks.vxid` is either dropped (the new
  table is the source of truth) or kept as a belt-and-braces
  cross-check; phase 0 decides.
- Console / VDI port allocation uses the new primitive,
  with the local `socket.bind()` race-check dropped as
  unnecessary.
- vsock CID allocation uses the new primitive, with the
  global `ClusterLock` dropped — atomicity comes from the
  conditional INSERT, not from serialising every allocation
  through one cluster-wide lock.
- MAC allocation uses the new primitive against a
  collision-detecting table, fixing today's probabilistic-
  only correctness.
- IPAM stays unchanged. Its CIDR-aware allocation semantics
  are not a fit for the generic pool primitive and the
  `ipam_reservations` table already does its job correctly.

The principle is: **one correct implementation in one place,
all callers benefit, future callers don't reinvent it.**

## Open questions

This plan is light on detail because almost every concrete
decision depends on a phase 0 research pass. The open
questions include at least:

1. **Allocation strategy per pool.** Random-with-retry is the
   simplest and matches today's VXLAN/vsock behaviour.
   Lowest-unused-value is deterministic and friendlier to
   debuggers ("port 30000 is always the first console port on
   a fresh node"). Highest-recently-freed reduces the chance
   of a recently-released value being immediately re-used
   (which can matter for TCP-port reuse and for log-grep
   sanity). Phase 0 picks the default and confirms whether
   per-pool overrides are needed.
2. **Per-pool range / policy configuration shape.** Code
   constants in a registry module, dedicated config keys,
   declarative dataclasses keyed by pool name, or a
   `resource_pools` metadata table that the primitive reads
   at allocation time. The metadata-table form scales to
   operator-configurable ranges (e.g. "give me port range
   40000-45000 for console ports on these hardware-locked
   nodes") but adds a join to the allocator. Phase 0 picks.
3. **Permanent vs leased semantics.** Today's allocators
   are all permanent-until-explicitly-freed. Future callers
   (network-service-ports, network-carrier-model) want
   leases with TTL and reaper. The schema supports both via
   `expires_at NULL` for permanent; phase 0 confirms the
   primitive's API surface for both forms is clean and
   doesn't accidentally invite leak-by-default.
4. **Migration of existing rows.** Three options for each
   ported allocator: (a) at first start, read the
   authoritative existing table (e.g. `networks.vxid`,
   `instance_attributes.ports`, etc.) and seed
   `resource_pool_allocations` from it; (b) dual-write
   during a transition window with reconciliation; (c)
   cutover with no migration and accept that
   currently-allocated values exist outside the new table
   until they're released and re-acquired. (a) is cleanest;
   (c) is honest about the cost; phase 0 picks per-pool.
5. **Whether to drop or keep the existing UNIQUE
   constraints.** Once `resource_pool_allocations` is
   authoritative for VXLAN IDs, the `networks.vxid` UNIQUE
   constraint becomes redundant. Dropping it is cleaner;
   keeping it is belt-and-braces and might catch bugs in
   the new primitive during early life. Phase 0 picks per
   ported allocator.
6. **Failure handling when retries exhaust.** Today's VXLAN
   allocator retries 10 times then raises. The new primitive
   needs a documented retry budget and a documented
   exception for "pool exhausted (or hopelessly contended)."
   Phase 0 picks the budget and the exception shape.
7. **Audit / event logging.** Every allocation and release
   should produce an event (existing project priority on
   event log coverage). Phase 0 confirms the event-type to
   use and whether the existing eventlog abstraction is the
   right write path or whether it needs a small dedicated
   "allocator-audit" event type.
8. **Per-pool reaper cadence.** Permanent pools (VXLAN,
   vsock, MAC, console ports) need no reaper. Leased pools
   (future callers) need their `expires_at` swept. The
   cluster-daemon maintenance loop is the natural home, but
   phase 0 should pick a cadence that handles the
   short-TTL service-port case (probably minutes) without
   spamming the loop.
9. **Whether to subsume IPAM.** Probably not — IPAM's
   CIDR-aware allocation, gateway-vs-host distinction, and
   per-network ownership are not a clean fit for the
   generic primitive. But phase 0 should confirm and
   document why, so a future maintainer doesn't try to
   merge them and discover the reason the hard way.
10. **Interaction with `cluster_locks`.** Today's vsock
    allocator holds a global cluster lock for the
    check-then-act sequence. Once the new primitive's
    conditional INSERT provides the atomicity, the lock is
    no longer needed. Confirm no caller depends on the
    side effects of holding that lock for any other reason.

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-generic-allocator-phase-00-decisions.md | Not started |
| 1. `resource_pool_allocations` schema and primitive | PLAN-generic-allocator-phase-01-primitive.md | Not started |
| 2. Port VXLAN ID allocator | PLAN-generic-allocator-phase-02-vxlan.md | Not started |
| 3. Port console / VDI port allocator | PLAN-generic-allocator-phase-03-ports.md | Not started |
| 4. Port vsock CID allocator | PLAN-generic-allocator-phase-04-vsock.md | Not started |
| 5. Port MAC allocator | PLAN-generic-allocator-phase-05-mac.md | Not started |
| 6. Documentation and audit-log surface | PLAN-generic-allocator-phase-06-docs.md | Not started |
| 7. Push audit | PLAN-generic-allocator-phase-07-push-audit.md | Not started |

**Phase 7 — push audit.** Runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as
their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit
finds nothing, that is recorded in one sentence.

## Dependencies on other plans

- **No hard dependencies** on other plans. The allocator is
  foundational and is intentionally light on integration
  surface — it touches existing allocator call sites and
  nothing else.
- **Hard dependency from `PLAN-network-service-ports`** and
  **`PLAN-network-carrier-model`** on this plan. Both expect
  the generic primitive to exist before they layer their
  own pool semantics on top.
- **Coherent with `PLAN-scheduler-reservations`.** The
  scheduler reservation table is structurally a separate
  pool (per-node capacity isn't a "pick an unused value
  from a range" shape) so it does not migrate to this
  primitive. But both plans use the same conditional-INSERT
  idiom and should share the underlying SQL pattern. Phase 0
  of this plan and phase 0 of scheduler-reservations should
  cross-read each other's decisions documents so the pattern
  stays coherent.
- **Coherent with `PLAN-replace-last-cluster-operation`**
  insofar as both are about removing redundant single-
  pointer mechanisms in favour of typed table-driven state.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md`, `PLAN-sticky-transfers.md`,
`PLAN-scheduler-reservations.md`, and
`PLAN-eventlog-direct-mariadb.md`: plan in the management
session, spawn a sub-agent per implementation step, review
in the management session, fix or retry, commit when
satisfied.

The destructive cleanup phases (each "port X allocator"
phase removes an existing implementation) should be skewed
toward **opus at high effort** for the first one (VXLAN, the
simplest, sets the template). Subsequent allocator ports can
use lower-effort sub-agents once the template is established.

### Planning effort

The master plan itself is **medium effort** — it's a
placeholder with a clear pattern and a well-bounded scope.
Phase 0 (research and decisions, especially the allocation-
strategy and per-pool-policy-shape decisions) is high
effort. Subsequent phases are mechanical refactors with
small per-phase scope.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] Each ported allocator has a test that exercises
      concurrent allocation against the same pool and
      confirms no duplicates.
- [ ] The MAC allocator's correctness improvement is
      exercised by a test that forces a collision and
      confirms the retry path handles it, not by trusting
      the birthday math.
- [ ] The vsock allocator's `ClusterLock` removal is
      exercised by a concurrent-allocation test that would
      have raced under the old implementation.
- [ ] The local `socket.bind()` race-check on console
      ports is removed cleanly, with no remaining callers
      relying on it.
- [ ] Object cleanup (`hard_delete()`) on an object that
      holds permanent allocations correctly releases them
      back to the pool.
- [ ] mypy coverage for the new primitive is at least as
      good as the allocators it replaces, ideally better.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The `resource_pool_allocations` table exists, is the source
  of truth for VXLAN IDs, console/VDI ports, vsock CIDs, and
  MAC addresses, and is consulted via one shared primitive
  with one shared correctness model.
* The three reimplementations of "pick an unused value" are
  gone; the codebase has one implementation of the pattern.
* MAC allocation no longer relies on the birthday paradox.
* The global vsock-allocation cluster lock is removed.
* The local `socket.bind()` race-check for console ports is
  removed.
* IPAM remains untouched, with a documented note explaining
  why it is not subsumed.
* Functional coverage under
  `deploy/shakenfist_ci/cluster_ci_tests` exercises a
  concurrent-allocation case for at least one pool.
* `pre-commit run --all-files` passes.

### Future work

- **Operator-configurable pool ranges.** Today's ranges are
  code constants. Once the metadata-driven option from
  question 2 lands, operators can shape pool ranges to
  match local constraints (regulated MAC OUIs, restricted
  port ranges, etc.). Out of scope here unless phase 0 picks
  the metadata-table form, in which case it lands as part
  of phase 1.
- **Per-pool metrics.** Pool occupancy is operationally
  interesting (a pool 90% allocated is something to alert
  on). Out of scope here but easy to add once the table is
  the source of truth.
- **Cross-pool sharing of the primitive.** If a future
  caller's semantics turn out to be a poor fit, the
  primitive should be extensible without forcing it into
  shape. Out of scope until a real caller surfaces.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed. The MAC-collision case in
particular is a latent bug today; if we encounter evidence
of an actual collision in the wild during this work, it
goes here.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
