# Placeable capacity: placement dry runs and shape holds on claims

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, the scheduler's candidate filters, the claims and
capacity ledger), and ground your answers in what the code
actually does today. Do not speculate about the codebase when you
could read it instead. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture overview,
`AGENTS.md` for project conventions and the index into `docs/`,
and `GOALS.md` for current development priorities. Key references
inside the repo include `shakenfist/scheduler.py` (candidate
filters and `summarize_resources()`), `shakenfist/mariadb.py` (the
claims and capacity ledger, `admit_instance_placement()`,
`_claim_capacity_guard()`), `shakenfist/external_api/instance.py`
and `shakenfist/operations/node_inst_netdesc_op.py` (the two
placement walks), and `shakenfist/external_api/app.py`
(`API_CAPABILITIES`).

This plan exists to serve callers across several repositories, and
its design input is external. It was commissioned by
shakenfist/private-ci's `PLAN-placeable-claims` plan, whose phase 0
produced a self-contained design brief — the "Brief for the
shakenfist/shakenfist plan" in
`docs/plans/PLAN-placeable-claims-phase-00-decisions.md` in that
repository. That brief is this plan's primary input: it restates
every figure, cites the code, surveys ten callers, and refines the
requirements. This document lifts it into a shakenfist/shakenfist
master plan; it does not replace it. Where this plan cites a
decision as "placeable-claims phase 0 `D2`", that `D` number is
that plan's, not this one's.

The brief's code citations are into shakenfist `develop` at
`ea39dff7c9` (2026-10-17). This repository has moved since: at the
time of writing `develop` was at `61a14af571`, and line numbers had
already drifted (for example `_claim_capacity_guard()` moved from
`shakenfist/mariadb.py:28044` to `:29563`, and by phase 0's survey at
`32de6cec7f` it was `:29927`). The structural facts hold —
`API_CAPABILITIES`/`advertised_capabilities()`,
`CLAIM_ENFORCEMENT_HARD = False`, `admit_instance_placement()`,
`find_candidates()` and the resources summary are all present — but
**every phase plan must re-survey its own citations against current
`develop`** rather than trusting the line numbers copied here from
the brief.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

This is a master plan. Each phase's steps are written as a phase
plan with `/next-phase` once the phase before it has landed,
because the per-node admission changes depend on what the placement
dry run measures (the live-memory question below), and because each
phase's survey depends on what the last actually shipped.

## Situation

Written 2026-11-04. A Shaken Fist claim reserves aggregate cluster
capacity; the scheduler places one instance onto one node at a
time, and never reads a claim. So a granted claim is a promise about
sums, and a set of instances that fits the sum can still fit on no
single node. On sfcbr this stopped being a theoretical case on
2026-10-16, when a memory overcommit change made the cluster's
residual room per node smaller than the instances CI builds.

The authority for everything in this section is placeable-claims
phase 0's brief and its baseline; the figures are restated here so
this plan stands alone, but the brief has the working.

### S1. A claim admits a sum; the scheduler places per node

- **Claims are aggregate.** `_claim_capacity_guard()`
  (`shakenfist/mariadb.py`) admits `claimed + requested +
  GREATEST(0, unclaimed_used - migrated) <= total` per dimension.
  No table ties a claim to a node — `scheduler_node_capacity`,
  `namespace_claims` and `cluster_capacity` have no node-scoped
  claim column. That is scheduler-reservations `D14`, by design.
- **The scheduler never reads claims.** `find_candidates()`
  (`shakenfist/scheduler.py`) filters each node on CPU, memory,
  free disk, disk bandwidth and queue depth, and has no reference
  to claims. `_has_sufficient_ram()` runs three checks and stops at
  a node's first failure: live `memory_available` less the
  reservation; the KSM ceiling `(memory_total_instance_actual +
  requested) / memory_max > RAM_OVERCOMMIT_RATIO`; and the
  committed memory ledger. Two of those read live state that no
  ledger entry can promise.
- **Claim limits are advisory.** `CLAIM_ENFORCEMENT_HARD` is
  `False` (`shakenfist/mariadb.py`), so nothing stops another
  namespace placing beyond its claim, and unclaimed instances take
  physical room the ledger cannot hold back.
- **Placement happens twice.** `POST /instances` walks candidates
  through `place_instance()` and answers 507 if none admits
  (`shakenfist/external_api/instance.py`); preflight then re-runs
  `find_candidates()` forced to the chosen node, redirects if it
  refuses, and aborts with a prose reason — no 507 — if no node
  admits (`shakenfist/operations/node_inst_netdesc_op.py`). The two
  walks must change together.
- **Refusal contracts differ.** An instance 507 carries `stage`,
  `transient` and `Retry-After: 15` (`capacity_error()`,
  `shakenfist/external_api/base.py`). A claim 507 is prose with
  none of them: `_claim_refusal()`
  (`shakenfist/external_api/auth.py`) folds the per-dimension detail
  into the message and returns a bare `sf_api.error()`. **No open
  issue tracks this.** #4207 was the issue cited here; it was closed
  on 2026-10-29 by the transient-capacity-refusals plan, which gave
  the *instance* 507 its contract and left the claim 507 alone. The
  requirement stands (`G7`, phase 5) and needs an issue of its own
  filed against it.

### S2. What changed, and what fragmentation looks like

sfcbr's `RAM_OVERCOMMIT_RATIO` went from 3.0 to 1.0 at 08:36 AEDT
on 2026-10-16 (mach33labs/33fl `group_vars/allsf.yml`), after
`kolla` guests dirtied unique pages KSM could not recover and sf-6
took OOM kills. That cut every node's ledger memory limit and KSM
ceiling to physical memory. Scheduler refusals went from one to
seven a day to 93 in one hour.

In one walked case the conductor's namespace claim was granted at
23:34:19.005Z and its CI runner (8 vCPU / 16384 MB / 460 GB) was
refused at 23:34:24.933Z, with 14,722, 12,759 and 12,428 MB free on
the three nodes that reached the memory stage: 39,909 MB in sum
against 16,384 asked, and no node that fit. A granted claim is a
promise about sums.

### S3. The baseline splits fragmentation from saturation

Read from sfcbr's scheduler audit stream by private-ci's
`tools/measure-placement-refusals.py`, over 2026-10-11 08:36 to
2026-10-17 16:00 AEDT (five days before the overcommit change, 1.31
days after). *Fragmentation*: room in sum, no node fit.
*Saturation*: no room in sum.

| Family | Before | After | After, by class |
|---|---|---|---|
| CI runner create refused after its claim was granted | 0 | 196 (149.8/day) | 190 fragmentation, 6 disk bandwidth |
| A CI job's own creates (nested test clouds) | 8 (1.6/day) | 37 (28.3/day) | 34 fragmentation, 3 disk bandwidth, none saturation |
| Accepted, then aborted at preflight | 0 | 1 | |
| Claim refused by the aggregate guard | 817 (`cpus`) | 18,669 (`memory_mb` 18,111) | all saturation |
| `ucs-lab-*` (standing, unclaimed) | 1 | 10 | 5 fragmentation, 5 cluster guard |

The refusals this plan addresses are **fragmentation**: a placeable
primitive buys back room present in sum but on no single node. It
cannot create memory for **saturation**, which is claim-coverage's
right-sizing problem. Over the window 40 of 45 in-job builds were
single-instance. Time to room after a fragmentation refusal: median
20 minutes, 90th percentile 65, longest proven 106; a one-minute
retry would have rescued none of 42.

## Mission and problem statement

When this plan has landed:

- Shaken Fist offers a **placement dry run** (`C`): a stateless,
  advisory "would these shapes fit now, and where?", in Shaken
  Fist's own vocabulary, behind a capability string, useful to
  every caller and to a client-side ledger model.
- Shaken Fist offers **shape holds on a claim** (`A`): a claim can
  carry a multiset of instance shapes, placed onto real nodes when
  the claim is made and kept booked, behind a second capability
  string. A create in that namespace consumes a matching hold
  without being told to, so an unchanged playbook gains the
  guarantee.
- A hold promises "not refused because another namespace took the
  room" — the ledger checks and, through the memory ledger, the KSM
  ceiling. It does not promise a node's live idle memory or disk
  bandwidth, so callers keep a bounded wait of their own
  (placeable-claims phase 0 `D2`).
- Both rest on **one shared set-placement core**: a multiset walked
  through the scheduler's own filters, all or nothing, each shape's
  tentative charge visible to the next.
- Aggregate claims behave exactly as they do today. The feature is
  additive and advertised, so an old client or server degrades to
  today's behaviour. Gang create (`B`) is **not** built.
- The design is checked against the ten callers placeable-claims
  phase 0 surveyed (`G2`), not only the conductor. At least one
  non-conductor caller uses it or is recorded as checked against it.

## What the primitive must be (requirements)

These are the requirements placeable-claims phase 0 refined from
the `G1`–`G8` list in its master plan. They are the acceptance bar
for the design, carried here so a phase plan need not leave the
repository to find them.

- **G1. Shaken Fist's vocabulary.** A shape is what `POST
  /instances` takes: `cpus`, `memory` and the disk list, matched on
  the summed disk size. No runner, job, label or topology concept
  crosses the API.
- **G2. Every caller, checked by table** (see *Callers* below), not
  only the conductor.
- **G3. Unchanged creates benefit.** A create in a namespace with
  unconsumed holds consumes, without being told, the smallest hold
  it fits within on every dimension (best fit, ordered by memory,
  then vCPUs, then disk), on a node satisfying any hard affinity it
  has. An optional hold reference on `POST /instances` takes that
  hold or is refused with a non-capacity, non-transient error naming
  why — never a silent fallback. A create larger than every hold is
  an unheld create. Exact matching is rejected: disk layouts,
  optional cache disks and measured peaks all drift.
- **G4. Placement policy in the existing affinity model**
  (`docs/user_guide/affinity.md`), `prefer_*` / `require_*`, not the
  deprecated weighted form.
- **G5. Additive and advertised.** One capability string for `C`
  and one for `A`. No new refusal stage names: a held create's
  refusal adds fields rather than minting stages, so callers that
  switch on `stage` keep working.
- **G6. A hold binds other namespaces whatever claim enforcement
  says.** The node guard becomes `used + held + request <= limit`
  for every create, the holder's own unheld creates included; a
  consuming create excludes its hold and moves it from held to used
  in one statement. That is the node guard's switch, not
  `CLAIM_ENFORCEMENT_HARD`'s, and the two stay apart.
- **G7. Machine-readable refusals.** A hold that cannot be booked is
  a 507 naming the shape and stage, transient by the same sets as
  `capacity_error()`. A held create refused on a live check gets
  that check's stage, `transient: true`, `Retry-After: 15`, and
  fields naming the hold and saying it was retained. Claim refusals
  gain the contract too — see `S1`: the issue that used to track
  that gap (#4207) is closed and an issue still needs filing.
- **G8. Inspectable.** Held room per node, consumed and unconsumed,
  and displaced holds with the node they left and why, in the
  claim's view and in `GET /admin/resources`; `summarize_resources()`
  must net holds out, or every client-side ledger model counts
  booked room as free.

### What a hold promises, and what it does not (`D2`)

A held create still runs the scheduler's live checks — they are the
only guard against the OOM kills that set the ratio to 1.0, so
skipping them for held creates is rejected. A hold promises the
ledger charges (committed vCPUs, memory ledger) and, through the
memory ledger sitting below it, the KSM ceiling while the ratio
stands. It does not promise live idle memory, free filesystem disk,
or the disk-bandwidth and queue-depth load-shedding stages. Two
narrowings reduce the live gap without pretending to close it:
every other create's live check counts a node's unconsumed holds as
if resident, and a hold's own booking counts memory placed but not
yet running. What stays out of reach is resident-set growth and the
host's own use — which is why callers keep their own bounded wait.

### Callers (`G2`)

The conductor is one caller of ten. The design is checked against
all; placeable-claims phase 0's `P4` table has the judgement.

| Caller | Shapes (vCPU/MB/GB) | Lifetime | Gets from `A` | Gets from `C` |
|---|---|---|---|---|
| private-ci conductor, CI runners | 2/4096/100, 4/8192/200, 6/12288/300 (+60 GB); ~10 per pass | one job; claim re-dated every 15 min | runner + job shapes booked before the runner boots | "would it fit?" before making a namespace |
| private-ci ci-images builder | 2×4/4096/100 (+50/+40), never together | standing claim, nightly | room kept across delete and recreate | little |
| 33fl `static-runners` | 8×4/8192/150, spread | 30-day claim, rebuilt weekly | room kept across the weekly rebuild | a pre-flight task |
| 33fl `static-ci` (k3s) | 4/4096/50 control, 4/8192/50 worker | 30-day claim; future scaler | both nodes booked | advisory |
| shakenfist/actions CI topologies | `localhost` 1×12/16384; `slim-tier` 3×6/12288; `slim-primary` 1×4/4096 + 5×4/12288 | one job | draw on the runner's claim; the conductor holds | advisory |
| kerbside lanes | 1×12/16384/400 | one job | as above | advisory |
| kerbside-patches | 1×12/16384/400, or 8 instances 34/55296/3200 | one job, bound | as above | advisory |
| client-python-k3s CI | peak 13/13312/150; churns within a job | one job | holds that return on delete fit its churn | advisory |
| operator building a cloud by hand | whatever is typed | hours–days | "book it, build at leisure" (scheduler-reservations `Q17`) | "would this fit?" before ten creates |
| lame-duck / evacuate (#1364, not built) | the source node's instances | one drain | the per-node mechanism, but needs an admin-scoped hold | "fit with node N excluded?" |

`A` is the only candidate under which an unchanged create path
gains: the holder changes, the creators do not. A design that only
the conductor can use is the failure `G2` exists to catch.

## Open questions

Load-bearing for the phase table:

- **Q1. Does the live question come first?** The plan's order is
  measure, then `C`, then `A`. Phase 0 reads `C`'s shadow counts
  before `A`'s admission changes are written: if `C`'s "fits"
  becomes a live-memory refusal at anything like the baseline's live
  share (24 of 68 in-job and 229 of 453 runner-create memory
  node-drops were live after the change), the live-memory question
  is sequenced ahead of `A`. Phase 0's sfcbr measurement (below)
  is what answers this. **Answered by phase 0 (`D13`), in that
  phase's Results: no — `A` proceeds and this plan's order is
  unamended.** The live share of memory node-drops has collapsed to
  4.1% in-job and 9.3% runner-create over 2026-10-30 to 11-06, and to
  1.3% and 9.1% over 2026-11-01 to 11-06; and in phase 0's per-sample
  reading the live check was the sole reason a node was dropped once
  in 4968 node-samples. The question is deferred, not closed: phase
  2's shadow counts re-apply this test before phase 3 is written.
- **Q2. Is the live/ledger memory margin double-counted?** The live
  check subtracts the reservation from psutil's `available` (already
  net of host use); the ledger subtracts it from physical memory. On
  a ratio of 1.0 the host may be counted twice. **Answered by phase
  0 (`D14`), in that phase's Results: the double subtraction is
  real, but not between the live check and the ledger.** At a ratio
  of 1.0 those two reservation terms cancel algebraically, so the
  live/ledger margin was never a function of the reservation; the two
  places the reservation is subtracted are the committed ledger and
  the KSM ceiling. It costs about 27% of per-node memory refusals of
  a 16 GiB shape on sfcbr, an upper bound, all of it through
  `limit_memory_mb`, and about one node-sample in 4968 through the
  live check. The retune is handed to 33fl with one lever, per-host
  `node_ram_reservation_gb`, and it does **not** gate `A`; the only
  coupling runs the other way, in that a retune relaxes the binding
  check and so requires `Q1`'s shares to be re-read.
- **Q3. The hold reference format and lifetime, and the consumption
  rule's edges** (`D3`): what a `POST /instances` hold reference
  looks like, when a deleted instance's hold returns and for how
  long, and what happens to a create larger than every hold.
- **Q4. The per-node held column's shape.** A ledger dimension
  beside `used_*`, its reconciler recomputation, and how claim
  migration carries bookings. This is the real cost `A` adds and the
  schema decision the plan turns on.
- **Q5. Whether `degraded` nodes stop placement** (`D8`). Node
  states today are `missing`, `stopping`, `stopped`, `degraded`;
  which of them stop accepting holds, and the re-place-or-displace
  rule when a node leaves service.

Refinements:

- **Q6. The admin-scoped hold for evacuate**, designed with #1364
  rather than guessed at here.
- **Q7. How `sf_claim` and `sf-client` express shapes**, and the
  stale statements that no release carries the claim verbs.
  **Answered by phase 0 (`D11`).** The premise this question was
  written on has since changed twice over: the claim verbs *are*
  released — client-python `v0.8.4` was tagged on 2026-10-28 — and
  `docs/operator_guide/scheduler.md:445-449` has already been
  corrected to say the verbs need `shakenfist-client` 0.8.4 or
  later, so the statement named here is no longer stale. The
  statements that *are* still stale, and are therefore what phase 6
  fixes, are `docs/user_guide/ansible.md:24-30`,
  `shakenfist/deploy/collection/requirements.txt:16-20` and
  `shakenfist/deploy/collection/plugins/modules/sf_claim.py:27-32`
  and `:275-302`, all of which still tell the operator to install
  the client from git.
- **Q8. Whether `C` is retired once `A` ships** or kept as the
  stateless query it also serves other callers as. The expectation
  is kept: `C` is a general query, not scaffolding for `A`.

## Execution

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

The `Merged` column is last, and records what put each phase on
`develop` for the push-audit phase to read. A phase that has not
landed reads `—`. Phases that land in client-python record
`client-python <sha> (#pr)` and are audited there.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 0. Decisions and the live-memory baseline | [PLAN-placeable-capacity-phase-00-decisions.md](PLAN-placeable-capacity-phase-00-decisions.md) | Complete | `ab19cf67e7` (#4822) |
| 1. Extract the shared set-placement core | [PLAN-placeable-capacity-phase-01-placement-core.md](PLAN-placeable-capacity-phase-01-placement-core.md) | Complete | `d20a7d7516` (#4835) |
| 2. The placement dry run (`C`) | [PLAN-placeable-capacity-phase-02-dry-run.md](PLAN-placeable-capacity-phase-02-dry-run.md) | In progress | — |
| 3. Shape holds: schema and the node guard (`A`) | PLAN-placeable-capacity-phase-03-holds-schema.md (to be written) | Not started | — |
| 4. Shape holds: consumption, release and displacement | PLAN-placeable-capacity-phase-04-holds-lifecycle.md (to be written) | Not started | — |
| 5. The refusal contract | PLAN-placeable-capacity-phase-05-refusal-contract.md (to be written) | Not started | — |
| 6. Client, modules and documentation | PLAN-placeable-capacity-phase-06-client-docs.md (to be written) | Not started | — |
| 7. Push audit | Phase 7 section below | Not started | — |

The order follows placeable-claims phase 0's `D1` and `D9`: `C`
first, `A` second, on one core, and the live-memory measurement
before either admission change.

- **Phase 0** resolves the open questions above and takes the sfcbr
  live-memory-against-ledger reading (`D9`). It ships no production
  code. It gated the sequence, and **the gate is open**: its `D13`
  answers `Q1` by leaving this order as it stands, so the
  live-memory question is not sequenced ahead of `A`. Its `D14`
  hands 33fl a per-host-reservation retune, which 33fl owns and
  which is not a prerequisite for any later phase. Its `D15` carries
  in a design constraint from placeable-claims phase 4a: a hold must
  defend a slot for a *shape* on a node, because the scheduler's
  node ranking deliberately spreads allocation away from the most
  committed nodes (shakenfist#3937, correctly), which is what turns
  aggregate headroom into per-node fragments. Phases 3 and 4 own
  that constraint.
- **Phase 1** generalises the single-instance placement
  pipeline into one all-or-nothing multiset walk through the
  scheduler's filters, with no behaviour change. It no longer
  de-duplicates anything: the walk this plan was written to
  extract was shared by scheduler-reservations phase 5 as
  `Instance.place_on_candidates()` on 2026-11-05, the day after
  this plan was written, so what is left is the shape
  denomination and the tentative charge. Both `C` and `A` build
  on it. See the phase plan's `F1`.
- **Phase 2** builds `C` on that core: a stateless, advisory query
  behind its own capability string, surfaced in the API and in a
  client method, but not in `GET /admin/resources` -- see the
  section below. No writes, so it cannot strand capacity, deadlock
  admission or desynchronise the reconciler. private-ci scores it
  in shadow against what jobs' creates met minutes later, in a
  harness it has already shipped; those counts are read before
  phase 3. Phase 0's `D13` makes that reading load-bearing rather
  than merely useful: it is where `Q1`'s live-memory test is
  re-applied, so a live share anywhere near the baseline's reopens
  `Q1` before phase 3 is written. The reading is private-ci's and
  gates phase 3, not phase 2.
- **Phase 3** gives the core the right to book: the per-node held
  column, the node guard term (`G6`), claim-limit validation
  against the sum of holds, and the reconciler and release paths.
  Phase 0's `D15` constrains it: a hold row books one shape onto one
  node and the node guard is what defends that slot, so neither may
  be weakened into an aggregate reservation.
- **Phase 4** is the hold lifecycle: implicit best-fit and explicit
  consumption (`G3`), holds that return on delete for the claim's
  life, re-placement in the same transaction or displacement when a
  node leaves service (`D8`), and ground-truth overbooking. A
  displaced hold defends no slot, which is why phase 0's `D15` has
  it reported rather than quietly retained as an aggregate.
- **Phase 5** gives claim refusals the `stage`/`transient`/
  `Retry-After` contract (no open issue; see `S1`), adds the
  held-create refusal fields (`G7`), and states the split of claim
  denials out of the transient stages that `CLAIM_ENFORCEMENT_HARD`
  flipping would require, keeping `G6`'s node-guard switch apart
  from it.
- **Phase 6** adds the `sf_claim`/`sf-client` shape verbs and the
  affinity expression (`G4`), writes the operator and user guides,
  and fixes the stale claim-verbs documentation (`Q7`).
- **Phase 7** runs `PUSH-AUDIT.md` over the whole plan's diff.

Phases 3, 4 and 5 change what the ledger and the admission guards
do, and are high effort. Phase 1 is a behaviour-preserving refactor
of a correctness-critical path and is high effort for that reason.
Phase 2 is high effort for the API and capability surface; phase 0
is high effort because it is the design. Phase 6 is medium where it
mirrors existing client verbs and doc pages.

### Phase 0: decisions and the live-memory baseline

Answer `Q1`–`Q8`, or say which are deferred and why. Take the
measurement placeable-claims phase 0 `D9` specifies: on each sfcbr
hypervisor, sample together over at least a working day
`memory_available`, `memory_max`, `memory_reserved_mb`, the nominal
allocation and the guests' summed resident memory
(`shakenfist/daemons/resources/main.py`), and compare host use with
the reservation and live room with ledger room per node. 33fl
provides node access and owns any retune that follows. The phase
ends with the design decisions the later phases cite, and with a
clear statement of whether the live-memory question comes before
`A`.

### Phase 1: extract the shared set-placement core

This section originally read that `POST /instances` and preflight
run two placement walks that "must change together", and said to
extract them. **They no longer do.** scheduler-reservations phase 5
shared the walk as `Instance.place_on_candidates()` in
`036f255758` on 2026-11-05, one day after this plan was written,
and left each caller only its own candidate construction and
after-handling, which is the right split. The phase plan's `F1`
records the correction and this paragraph was rewritten from it.

What remains is the part that was always the point. The placement
pipeline evaluates one `Instance`: build one function that walks a
multiset of requested *shapes* through the scheduler's own filters,
all or nothing, each shape's tentative charge visible to the next,
with no behaviour change and the existing single-instance callers
routed through it at one shape. This is the core both `C` and `A`
reuse, and doing it first keeps the dry run and the holds from
forking the placement logic.

### Phase 2: the placement dry run (`C`)

Build the stateless query on the core: given a multiset of shapes,
answer whether they fit now and on which nodes, with no state
written. Advertise it with a capability string (`API_CAPABILITIES`
in `shakenfist/external_api/app.py`) and surface it in a client
method. Not in `GET /admin/resources`: that was this section's
original instruction and it cannot be followed literally, because a
per-node verdict is a verdict about a shape and a GET with no body
has none to be about. The phase plan's `D6` records the decision
and what was rejected with it. It is racy and advisory by
construction.

private-ci scores it in shadow against what a job's creates
actually met minutes later -- which measures how often room
present at claim time is gone at create time, and on which check.
That harness is not phase 2's to build: placeable-claims phase 3
shipped it already, scoring a client-side model of the same
per-node checks rebuilt from `GET /admin/resources`, so what phase
2 does is let that model become a call. The switch, the soak and
the reading of the counts belong to private-ci and gate phase 3
rather than phase 2 (the phase plan's `F1` and `D9`). Read those
counts before writing phase 3.

Phase 1's core returns a whole ordered candidate list per shape but
charged only the head of each, and phase 2 is the first caller which
could have more than one shape to place. A caller which walks past a
head — which `Instance.place_on_candidates()` does today when
admission refuses it — leaves the set's arithmetic behind, because
every later shape was ranked as though the skipped shape were on the
node it skipped. Phase 1 states that rule in the entry point's
docstring rather than enforcing it in the return type, on the grounds
that the type to return is the one phase 2's real consumer wants.
Decide it there: either phase 2 takes only heads, or the core returns
the chosen node distinctly from its fallbacks.

Two costs to measure there rather than guess at here. The core calls
`add_event_multi()` around fourteen times per shape and builds each
`extra=` payload — `affinity_detail`, `load_detail`, `by_affinity`,
the per-candidate weights — before the call, whether or not the
caller gave it any event targets. A dry run gives none, so all of
that is assembled and discarded, per shape and per candidate. It is
dict construction with no I/O, so there is nothing to win until a dry
run is measured as hot; if it is, guard the construction once at the
top of `_find_candidates_for_shape()` rather than at fourteen call
sites. And a refused set leaves the audit events of the shapes it had
already assigned, which phase 3's hold evaluation has to decide what
to do about.

### Phase 3: shape holds — schema and the node guard (`A`)

Add a claim's ability to carry shapes and book them onto nodes. The
schema decision (`Q4`) is the per-node held column beside `used_*`:
its shape, its reconciler recomputation from hold rows, and how
claim migration carries bookings. The node guard becomes `used +
held + request <= limit` for every create (`G6`), a consuming
create excluding its own hold in the same statement; this is the
node guard's switch, kept apart from `CLAIM_ENFORCEMENT_HARD`. Claim
limits are validated, at creation and update, to be at least the sum
of the claim's holds. The claim branch of
`admit_instance_placement()` is otherwise unchanged, since holds sit
inside `claimed`.

### Phase 4: shape holds — consumption, release and displacement

The lifecycle (`D3`, `D8`): a create consumes the smallest hold it
fits within (best fit, memory then vCPUs then disk) on a node
meeting its hard affinity; an optional explicit hold reference takes
that hold or is refused, never silently falling back; a deleted held
instance returns its hold, booked where it last was, until the claim
is deleted, shrunk below it or expires; a refused create consumes
nothing; a moved instance takes its booking with it. When a node
stops accepting placements its unconsumed holds are re-placed onto
another node in the same transaction if they fit (most recently
booked first), otherwise *displaced*: inside the claim's aggregate
limits, booked on no node, reported, an event on the claim — never
left on a node that cannot host them, never dropped, never re-tried
by the server. The holder re-books. Ground-truth writers that admit
with `enforce=False` may book past holds; ground truth wins and the
node is then overbooked, which the reconciler records.

### Phase 5: the refusal contract

Give claim refusals the `stage`/`transient`/`Retry-After` contract
instance creates already have (`capacity_error()`). This phase opens
by filing the issue for that gap: #4207 used to track it and was
closed on 2026-10-29 having fixed only the instance side, so the
requirement is live and untracked (`S1`). A held
create refused on a live check gets that check's stage, `transient:
true`, `Retry-After: 15`, and fields naming the hold and saying it
was retained (`G7`). No new stage names (`G5`). State the split of
claim denials out of the transient stages that flipping
`CLAIM_ENFORCEMENT_HARD` would require (scheduler-reservations phase
5's territory), so the two switches do not get conflated.

### Phase 6: client, modules and documentation

Add shape expression to `sf_claim`/`sf-client` (`Q7`, phase 0's
`D11`) and the affinity model (`G4`, `prefer_*`/`require_*`).
Document both capabilities in the operator and user guides,
including `GET /admin/resources`' new fields and the affinity
semantics. Correct the stale claim-verb statements, which are **not**
in `docs/operator_guide/scheduler.md` — that page was fixed before
phase 0 read it — but in `docs/user_guide/ansible.md:24-30`,
`shakenfist/deploy/collection/requirements.txt:16-20` and
`shakenfist/deploy/collection/plugins/modules/sf_claim.py:27-32`
and `:275-302`: each still says no release carries the verbs and
tells the operator to install client-python from git, where `v0.8.4`
(2026-10-28) carries them. `_require_claim_verbs()` itself stays — it
is a probe against the client that was actually built — and only its
prose changes. `docs/operator_guide/database.md` covers the schema
change.

### Phase 7: push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase against
`develop`, reading each phase's `Merged` cell for its range. Phases
that landed in client-python are audited in their own pull requests
and cited here. Findings land as their own pull request; the plan is
not complete until they are resolved or declined in writing.

## Relationship to other plans

- **shakenfist/private-ci `PLAN-placeable-claims`.** This plan is
  its phase 5. private-ci's phase 3 (per-node fit pre-check, in
  shadow) scores `C`; its phase 6 (reserve bound cloud jobs before
  the runner boots) consumes `A`, behind capability checks, falling
  back to today's aggregate claim. private-ci's phase 4 wait in the
  collection's `sf_instance` (30 minutes single-instance, 10
  multi-instance, on `transient: true` only; with #4474) is a
  separate change that is **not** part of this plan. Its phase 4a
  (`PLAN-placeable-claims-phase-04a-regression.md`) repaired the
  instrument the baseline above was taken with and referred one
  design constraint inward, which this plan's phase 0 absorbed as
  `D15`; its `F8` is also half the evidence for phase 0's `D13`.
- **`PLAN-scheduler-reservations`** (`In progress`). `D14`, its step
  3 addendum and its "Node-scoped claims" future work are the
  decisions this plan revisits: aggregate claims stay, holds are
  added beside them. Its `D8`/`D19` deferral of batch create stands
  — `B` is not built. Its phase 5 (hard claim enforcement) decides
  what capping a claim's own limits means; `G6` binds other
  namespaces through the node guard, a different switch, so this plan
  states the interaction rather than waiting. `Q17`/`D15` (the
  operator's one-line path) and `D17` (unclaimed namespaces compete
  for `total - claimed`, unchanged by holds) carry forward.
- **`PLAN-transient-capacity-refusals`** (`Complete`). Its `OQ5`
  costed a node-scoped claim; its first objection (a small window at
  concurrency 5) does not fit a window of minutes between a claim and
  a job's creates on a cluster that refused 93 creates in an hour,
  and its third (a claim-blind pre-filter becoming a ledger reader)
  is overtaken since the CPU and memory filters already read the node
  row. Its second objection (a ledger dimension, reconciler, claim
  migration, the guard) stands and is `A`'s real cost. Its phase 5
  answered open question 8 "no server-side queue", so waiting stays
  in callers and no hold is re-tried server-side.
- **`PLAN-per-host-resource-reservations`** (`Complete`). The
  per-host reservation overrides are the lever 33fl would use if
  phase 0's live-memory reading shows the host counted twice.

## Out of scope

- **Gang create (`B`).** A `POST` of instances placed together or
  not at all. In the baseline window it would have changed at most 3
  of 37 in-job refusals and found room for none; its all-or-nothing
  ends at the API because preflight re-places each member alone.
  scheduler-reservations `D8`'s deferral stands. Revisit only if
  private-ci's build waits record two multi-instance builds waiting
  into each other's holdings, or the multi-instance CI topologies
  cannot be held.
- **A server-side placement queue or re-placement wait.**
  transient-capacity-refusals phase 5 decided against it; waiting
  stays in callers.
- **Holds that promise live memory.** The live checks stay; a hold
  promises the ledger, not a node's live idle memory.
- **Hard claim enforcement** (`CLAIM_ENFORCEMENT_HARD`). That is
  scheduler-reservations phase 5.
- **CI concepts in the API** (`G1`). Topology, runner, job and label
  stay in the conductor.

## Agent guidance

The execution model, planning-effort rule, step guidance, model
roster and management-session review checklist in `PLAN-TEMPLATE.md`
apply unchanged, including its project-specific checks (`pre-commit
run --all-files` for flake8, stestr unit tests and mypy; `tox -e
genprotos` if proto files change). The phase-by-phase effort
guidance is in the Execution section above: phases 0 to 5 are high
effort because they are the design or change the admission path;
phase 6 is medium where it mirrors existing patterns.

Each phase plan **re-surveys its own citations against current
`develop`** before writing steps, because the line numbers carried
into this plan from placeable-claims phase 0's brief are already
stale (see the Prompt).

## Administration and logistics

### Success criteria

- The `PLAN-TEMPLATE.md` success criteria hold for every phase:
  `pre-commit run --all-files` passes; new code follows the object
  lifecycle, three-layer MariaDB and Pydantic-schema patterns;
  filtering is pushed into SQL; there are unit tests and preferably
  functional coverage; proto changes are regenerated.
- Both capabilities are advertised through capability strings, so an
  old client or server degrades to today's aggregate-claim
  behaviour.
- `C` is scored in shadow by private-ci, and its counts are read
  before `A`'s admission changes are written.
- A hold binds other namespaces through the node guard, independent
  of `CLAIM_ENFORCEMENT_HARD`.
- Holds and displaced holds are inspectable per node in the claim's
  view and `GET /admin/resources`, and `summarize_resources()` nets
  them out.
- The primitive is documented in the operator and user guides, with
  at least one non-conductor `G2` caller using it or recorded as
  checked against it.
- The change is reasoned about against a running cluster, not only
  unit tests.

### Future work

- **Lame-duck and evacuate** (#1364) on the same per-node
  mechanism, with an admin-scoped hold (`Q6`).
- **The `G2` holders adopt holds:** 33fl's `static-runners` and
  `static-ci`, and the conductor's `ci-images` builder, are natural
  shape reservations once `A` is proven. Adopting them is their
  owners' change.
- **Gang create (`B`)**, if the multi-instance condition above
  arises.
- **The `ucs-lab-*` namespaces' owner** — placeable-claims phase 0
  `D10` found 10 post-change refusals in standing `ucs-lab-*`
  namespaces and no record of who creates them; worth knowing before
  the design is called "every caller".
- **Affinity across a set's own members.** Phase 1's `D6` charges
  capacity across a multiset but leaves the hard affinity filter
  and the scorer blind to the set's tentatively placed members,
  because both read placed `Instance` rows and a shape has none.
  No known caller needs it; the eight-instance `kerbside-patches`
  topology is the candidate if one does, and phase 2's shadow
  scoring is the instrument that would show it.

### Bugs fixed during this work

- **A `POST /instances` with a JSON string vCPU or memory count
  returned 500, and still does.** Phase 1 closed the scheduler's
  half of it and not the create. The API deliberately accepts
  `{"cpus": "2"}` -- `shakenfist/external_api/validation.py`
  declines to refuse it on the grounds that the handler copes -- and
  the create path passes it to `Instance.new()` untouched, where
  `Instance.__init__` stores static values as handed. The typed
  column means a later read gives an integer back, so the string
  survives exactly as long as the in-memory object a create's
  scheduling decision runs against, and `inst.cpus > max_cpu` raised
  `TypeError` in the `cpu_max_per_instance` filter. Phase 1's shape
  factory types those figures, because it has to in order to sort a
  set largest first, so the walk now completes. **Admission then
  raises on the same string**: `Instance._capacity_claim` returns
  `self.cpus` as handed and `mariadb.admit_instance_placement()`
  computes `cpus * config.SCHEDULER_DEMAND_PER_VCPU`, a float, so
  `'2' * 0.6` raises `TypeError` and the create still 500s -- now
  after the whole scheduling walk has run and published its audit
  events. Phase 1 deliberately did not cast there as well: a second
  cast in a third module would make the hole look closed while
  `Instance.cpus` stayed a string for every other reader of it.
  The root cause is a request value reaching a handler untyped,
  which is
  [PLAN-api-input-validation.md](PLAN-api-input-validation.md)'s
  subject -- but that plan is `Complete` and never covered this
  site, so phase 2's survey filed the bug as
  shakenfist/shakenfist#4842 rather than leave it deferred to a
  closed plan. Two notes recorded there. `validation.py`'s
  justification for accepting the string -- that pydantic's lax
  mode converts it -- is true of the database row and not of the
  object the scheduler and the admission path read. And the
  one-line fix is to type the value where the request becomes an
  instance (`shakenfist/external_api/instance.py:1121`), not at
  either reader: both of the casts a reader could add leave the
  next reader to find the next one.

None yet. Directly related open issues: shakenfist/shakenfist#1364
(lame-duck and evacuate), shakenfist/shakenfist#4474 (the functional
suite's wrapper waits on the exception class, ignoring `transient`).

**An issue still needs filing**, and phase 5 owns filing it: claim
refusals carry no machine-readable contract. shakenfist#4207 was
cited for this throughout an earlier draft of this plan, but it was
closed as completed on 2026-10-29 by the transient-capacity-refusals
plan, which gave the *instance* 507 its `stage`/`transient`/
`Retry-After` fields and did not touch the claim 507. Phase 0
re-checked the code: `_claim_refusal()` still returns a bare
`sf_api.error()` with the per-dimension detail folded into the prose
message. The gap is real and the requirement (`G7`, phase 5) stands;
only the issue that tracked it is gone. Phase 0 deliberately did not
file it — that is phase 5's first task, so the issue is written
against the design phase 5 actually ships.

The baseline and caller survey are shakenfist/private-ci
`PLAN-placeable-claims` phase 0.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
