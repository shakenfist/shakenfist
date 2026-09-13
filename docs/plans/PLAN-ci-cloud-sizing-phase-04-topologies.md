# Phase 4 -- Re-shape the topologies against the phase 2 data

Part of [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md). Phase 3
([PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md](PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md))
gated this phase and is Complete, so the gate is open.

**Planning effort:** high. The master plan asks for a decision about
what the clouds should be, and the survey below found the arithmetic
the master plan reasons from is attributed to a mechanism which does
not exist in the tree. That has to be settled before a shape is
chosen, not after.

## Scope

**In scope:**

* Establishing *why* two of `slim-tier`'s three hypervisors and one of
  `slim-primary`'s five publish `cpu_schedulable` 1 rather than 2. This
  is new work the master plan did not anticipate needing, and D1 makes
  it a gate rather than a step.
* Choosing a shape for `slim-primary` and `slim-tier` against the phase
  2 data and the phase 3 merge-run evidence, and applying it to the
  three topology files in `shakenfist/actions`.
* Revisiting `slim-tier`'s 70-minute job timeout, which exists because
  the tier is small.
* Re-reading the candidate-shapes table's arithmetic against what the
  survey found, and correcting it at source.

**Out of scope, explicitly:**

* **Changing `NODE_CPU_RESERVATION_THREADS`, `CPU_OVERCOMMIT_RATIO` or
  `SCHEDULER_TARGET_LOAD`,** in CI or anywhere. This phase changes the
  shape of the clouds, not the admission policy applied to them. D1
  exists precisely so that a surprising reading is explained rather
  than tuned away, and the master plan's D1 keeps the production
  reservation in CI deliberately.
* **The `demand` bound itself.** Phase 3's merge run found every guard
  refusal in the window falling on it (F3 below). Rescaling or
  disabling the D13 guard for small nodes is listed as fix (4) in the
  #3772 research and belongs to
  [PLAN-transient-capacity-refusals.md](PLAN-transient-capacity-refusals.md),
  not here. This phase only has to predict what the reshape does to
  that bound, and measure it afterwards.
* **Adding or removing a topology**, including creating the
  `ci-topology-slim-tier-released.yml` that F4 found does not exist.
  Its absence is a gap in release coverage, but closing it costs a
  sixth cloud per run and is a separate decision.
* **The `sf-absent` phantom.** It stays exactly as it is. Its own
  comment block says so in capitals, and F4 records that it lives in
  only one of the two `slim-primary` files -- which is also left alone.
* **Fixing whatever F2 turns out to be**, if it is a defect in the
  resources daemon rather than a property of the CI guests. The plan's
  job there is to record it and file an issue.

## What the survey found

The master plan's phase 4 section was written in August. Six of its
factual claims are wrong or incomplete, and one of them is the premise
the whole phase rests on. Corrected at source in the master plan's
phase 4 section and the `docs/plans/index.md` row as part of the
planning commit, so a later step does not redo it.

### F1 -- The ledger numbers are right, and now measured

Phase 3's first merge run publishes per-node ledgers directly, so this
is no longer inferred. Run
[34681505274](https://github.com/shakenfist/shakenfist/actions/runs/34681505274),
*Committed vCPU, per node*:

| Topology | Per-node ledgers | Total | Nodes at peak frac 1.000 |
|---|---|---|---|
| `slim-tier` (Debian 12 tier) | 3, 3, 6 | 12 | two of three |
| `slim-primary` (Ubuntu 24.04 cluster) | 3, 6, 6, 6, 6 | 27 | one of five |

The candidate-shapes table's `Ledger` column (`slim-primary` 27,
`slim-tier` ~12) is confirmed exactly, and so is the phase 4 section's
claim that the binding number is per node: `slim-tier`'s cluster p90
committed/ledger was 0.833 while its two small nodes sat at 1.000.

### F2 -- ~~the stated cause of the ledger-3 nodes does not exist~~ **WRONG, and corrected by 4a**

**This finding was mistaken, and the mistake is recorded rather than
deleted because it is the reason this phase has a gate at all.** See
*4a -- what the gate found* below for the truth: the infra-role
reservation bump is real, it is 4 threads, and it lives in
`examples/_shared/site.yml:359-363` in this repository -- the
deployment playbook CI actually runs
(`actions/tools/deploy-collection.sh:65`). The master plan's original
explanation was right.

What this finding got right: the measured ledgers (F1), that
`node_cpu_reservation_threads` is **2** in
`deploy/collection/roles/node/defaults/main.yml:24`, that nothing in
the collection or in `shakenfist/actions` overrides it, and that the
ledger-3 nodes correlate exactly with carrying an infra role.

What it got wrong, and why: it concluded from those greps that no 4
exists anywhere. The role default is only the fallback for a caller
that does not set the variable, and CI's caller always sets it, so the
value never appears in the role, the collection or the CI repository
at all -- it is computed by the playbook and arrives as a
caller-supplied fact. Two things in the tree actively encouraged the
wrong conclusion: `scheduler.py:213-214` states "there is no longer an
infra-role bump", which is true of the synthetic fallback it annotates
and false as a statement about the system; and `config.py:614`
describes the templating as "folding in any historical infra-role
bump", where "historical" reads as "since removed" rather than as
"still applied, by the deployer".

**The lesson for the rest of this plan, and for phase 5.** A grep over
the collection and the CI repository is not a survey of how a CI node
is configured: the answer was in a third place, the example deployment
playbook, which is neither the product nor the CI harness but is what
CI runs. Any later phase reasoning about a CI node's configuration has
to read `examples/_shared/site.yml` as well.

D1's gate therefore rested on a false premise, and was still worth
running -- not because the reshape was in doubt, but because the same
step established the exact arithmetic D3 needs, found that 6 vCPU only
brings an infra node up to where a plain hypervisor already sits, and
turned up the silent-clamp defect below. That is a weaker
justification than the one D1 was written with, and D1 says so now.

### F3 -- The bound that refused placements is not the one being resized

Phase 3's close-out recorded this and it bears directly here. In the
same run, **every** capacity-guard refusal on all three cluster jobs
fell on the `demand` dimension alone -- 176, 139 and 163 of them -- and
none on an allocation dimension. The attribution inverts with size:
measured load alone was already over the bound in 138 of 176 refusals
(78%) on the three-node job, where the D13 feedforward estimate carried
roughly two thirds on the five-hypervisor ones.

The demand bound is `SCHEDULER_TARGET_LOAD` (0.75,
`shakenfist/config.py:523`) per schedulable thread, so it scales with
the same `cpu_schedulable` the ledger does. On a ledger-3 node the
bound is **0.75 of one thread**, which is why that node refuses almost
everything. A reshape that lifts those nodes to `cpu_schedulable` 4
lifts the bound to 3.0 -- a fourfold improvement, larger in
proportional terms than the ledger gain.

So the reshape plausibly helps *more* than the master plan claims, but
through a mechanism the master plan never mentions, and entirely
conditional on F2. Both dimensions hang on the same unknown.

### F4 -- Only one `-released` variant exists, and it differs structurally

The phase 4 section says "(and the `-released` variants)", plural.
`shakenfist/actions/ansible/` holds:

* `ci-topology-slim-primary.yml` -- primary is **not** a hypervisor
  (`groups: allsf,database_node,primary_node`, `:84`); sf1 is the
  network node *and* a hypervisor; sf1-sf5 are the five hypervisors.
  Carries the `sf-absent` phantom (`:29`).
* `ci-topology-slim-tier.yml` -- primary carries every role including
  `hypervisors` and `network_node` (`:65`); sf1 is a hypervisor and the
  second database node; sf2 is a plain hypervisor. No phantom.
* `ci-topology-slim-primary-released.yml` -- primary is the
  `network_node` and is **not** in `hypervisors` (`:62`), which is a
  different shape from plain `slim-primary` where sf1 is the network
  node. No `sf-absent` phantom.

There is **no** `ci-topology-slim-tier-released.yml`. So the work is
three files, not four or more, and the released variant is not a copy
of the other -- a change applied mechanically to both would make
`slim-primary-released` wrong.

### F5 -- `functional-tests.yml` has no node lists, and is in the other repository

The phase 4 section asks for "updating any hardcoded node lists in
`functional-tests.yml`", in a sentence otherwise about actions-repo
topology files. That file is
`shakenfist/.github/workflows/functional-tests.yml` and its merge
matrix (`:436-484`) carries topology *names*, `concurrency: 5` and
per-job `timeout_minutes` -- no node names, no node counts, nothing a
reshape invalidates.

There is one thing there worth changing, and it is not a node list:
`slim-tier` runs at `timeout_minutes: 70` against `slim-primary`'s 60,
with a comment (`:471-474`) saying it needs the headroom because it
runs "the same cluster suite on roughly half `slim-primary`'s
hypervisor capacity". If the reshape works, that premise weakens and
the timeout should come back down -- which is also a falsifiable way
to tell whether the reshape worked.

### F6 -- The structural minimums are real, and `slim-tier` is already at one

All three hold, with two corrections:

| Requirement | Where | Correction |
|---|---|---|
| >=2 hypervisors that are not the network node | `cluster_ci_tests/test_network_lifecycle.py:53` | **`slim-tier` is exactly at this minimum today.** Its primary is the network node, so sf1 and sf2 are the only two candidates. Any consolidation of the tier onto two hypervisors makes this test skip. |
| >=3 nodes | `cluster_ci_tests/test_scheduler.py:128` and **`:292`** | The guard counts `get_nodes()`, that is **all** nodes rather than hypervisors, and **two** tests carry it, not the one the master plan names -- `test_affinity` and the weighted-ordering test. |
| >=2 database nodes | `cluster_ci_tests/test_database_tier.py:37` | As stated. |

### F7 -- Every file this phase edits is in a repository only the operator can push

All three topology files are in `shakenfist/actions`. Phase 3 met this
seam at 3e and it cost the phase a day of being un-closeable. Phase 4
is *entirely* that, plus the one timeout in this repository. D6 deals
with it rather than discovering it late.

### F8 -- The candidate-shapes table needs re-reading, not replacing

Its `vCPU` column counts every under-cloud instance, including
`slim-primary`'s primary, which F4 shows is not a hypervisor and
therefore contributes no ledger at all. "primary as 4 x 5 vCPU plus a
database node" is a change to sf1-sf5's count and size with the
primary left alone, not a change to the primary. The `Ledger` column
is right (F1) and the shapes remain the right menu; the reader just
has to know which column is scheduling capacity and which is
under-cloud spend.

### 4a -- what the gate found

**The mechanism exists, F2 was looking in the wrong repository, and the
master plan's original arithmetic was right all along.**
`NODE_CPU_RESERVATION_THREADS` really is **4** on the infra nodes. It
is not in the collection and not in `shakenfist/actions`; it is
computed by the deployment playbook in *this* repository, at
`examples/_shared/site.yml:359-363`:

```yaml
- name: Default the per-host CPU thread reservation
  ansible.builtin.set_fact:
    node_cpu_reservation_threads: >-
      {{ (1 + ((node_is_network_node or node_is_database_node) | ternary(1, 0))) * 2 }}
  when: node_cpu_reservation_threads is not defined
```

The infra-role bump was not removed. It was *moved* -- out of the
server and into the deployer -- which is exactly what
`config.py:614`'s "folding in any historical infra-role bump" is
describing, and what `scheduler.py:213-214`'s "there is no longer an
infra-role bump" means (it is a statement about the scheduler's
*synthetic fallback* only, covered by
`test_old_dialect_fallback_ignores_infra_role`). F2's grep of the
collection role and of `shakenfist/actions` could not find it because
the value reaches the role as a caller-supplied variable;
`roles/node/defaults/main.yml:24`'s 2 is only the fallback for a
caller that does not set it, and CI's caller always sets it
(`actions/tools/deploy-collection.sh:65` runs
`examples/_shared/site.yml`).

Proven from the run-34681505274 bundles, the rendered `/etc/sf/config`
of every node:

| Topology | Node | Roles | `..._CPU_RESERVATION_THREADS` | `cpu_schedulable` | Ledger |
|---|---|---|---|---|---|
| `slim-tier` | primary | hyp, network, database | 4 | 1 | 3 |
| `slim-tier` | sf1 | hyp, database | 4 | 1 | 3 |
| `slim-tier` | sf2 | hyp | 2 | 2 | 6 |
| `slim-primary` | primary | database (**not** hyp) | 4 | n/a | n/a |
| `slim-primary` | sf1 | hyp, network | 4 | 1 | 3 |
| `slim-primary` | sf2-sf5 | hyp | 2 | 2 | 6 |

So on the infra nodes `cpu_schedulable = max(1, 4 - 4) = max(1, 0) =
1` -- the degenerate case that
`test_schedulable_floors_at_one` exists to cover -- and
`limit_cpus = floor(1 x 3.0) = 3`. On the plain hypervisors
`max(1, 4 - 2) = 2` and `floor(2 x 3.0) = 6`. Nothing is
guessing, nothing is stale and nothing is on the scheduler's fallback:
`cpu_schedulable` is published and truthy on all five nodes in all 237
samples across the two series, `cpu_committed_row_present` is true
throughout, and `_get_hybrid_core_counts()`
(`shakenfist/daemons/resources/main.py:72-99`) returns only
`cpu_cores_performance` / `cpu_cores_efficiency` so it cannot overwrite
`cpu_schedulable`, `cpu_threads` or `cpu_cores`. `psutil` sees a
perfectly ordinary 4-thread guest; the cross-check is that all three
`slim-tier` nodes publish `ram_max` 35880 = 11960 x
`RAM_OVERCOMMIT_RATIO` 3.0, so they are identically sized and only the
reservation differs.

**The RAM reservation is clamped on the same nodes, for the same
reason.** `site.yml:352-357` gives infra nodes
`max(2.0, 10% of RAM) + 4.0` = 6.0 GB, and `_compute_reservations()`
caps a reservation at half the machine
(`shakenfist/daemons/resources/main.py:119-120`), so the published
`memory_reserved_mb` is 5980 = 11960 // 2 rather than 6144. Both of
the production reservations are saturating their safety clamps on
these guests. A 4 vCPU / 12 GB guest is simply too small to carry an
infra role at production reservation values: the bump assumes a node
where 4 threads and 6 GB are a modest slice, and on this guest they
are 100% of the CPU and 51% of the RAM.

**Answer to the question the phase turns on: yes, growing the guest
raises `cpu_schedulable`, linearly, because the reservation is
absolute and does not scale with the guest.** The bump is a fixed 4
threads whatever the node's size, so:

| Guest vCPU | Infra node (`res` 4) | Plain hypervisor (`res` 2) |
|---|---|---|
| 4 (today) | sched 1, ledger **3** | sched 2, ledger **6** |
| 6 | sched 2, ledger **6** | sched 4, ledger **12** |
| 8 | sched 4, ledger **12** | sched 6, ledger **18** |

Two consequences for D3 and D4, which are for the operator to rule on:

1. D3's three 6 vCPU `slim-tier` nodes give ledgers **6, 6, 12** (24
   total, up from 12), not 6, 6, 6. The infra nodes double, and the
   plain node quadruples as a side effect of the uniform size.
2. Six vCPU only lifts the infra nodes to where today's *plain*
   hypervisors already sit. If the intent is that no hypervisor is
   materially smaller than another, the infra nodes need **8** vCPU to
   reach a ledger of 12, or the topology needs a per-host
   `node_cpu_reservation_threads` in inventory -- which the playbook
   explicitly supports (`when: ... is not defined`) but which would be
   a deliberate departure from production values and therefore a D1
   question, not a free choice.

**Is it a defect?** The arithmetic is correct behaviour, and the
correlation with roles is a property of the deployment playbook rather
than of the CI under-cloud. Two small real defects fall out, neither
of which this phase fixes:

- `shakenfist/scheduler.py:213-214` asserts "there is no longer an
  infra-role bump". True of the fallback it is commented on, false as
  a statement about the system, and it is what sent F2 looking in the
  wrong place. Worth narrowing to "the *synthetic fallback* no longer
  applies a role-aware bump; the deployer does, per host".
- A node whose reservation meets or exceeds its thread count is
  rescued silently by `max(1, ...)`, and a node whose RAM reservation
  exceeds half the machine is rescued silently by the `// 2` cap.
  Nothing logs, events or publishes a flag when either clamp engages,
  so a node reserving 100% of its CPU looks indistinguishable from a
  node with one spare thread. Had either clamp emitted an audit event,
  this gate would have been a single `/admin/resources` read. Filed as
  [#4201](https://github.com/shakenfist/shakenfist/issues/4201), whose
  body says explicitly that the clamps themselves are the right
  failure mode and must not be removed, that the CI reservation must
  not be lowered to dodge them, and that the scheduler must not be
  made to tolerate a zero ledger -- the defect is that they are quiet.
  Out of scope here by this plan's Scope section.

## Decisions

### D1 -- F2 was a gate, and it has been discharged

**Status: closed by 4a on 2026-09-13, and the premise it rested on was
wrong.** The gate said no topology file could be edited until the
mechanism behind `cpu_schedulable` 1 was established. 4a established
it: the infra-role reservation bump is real, is 4 threads, and lives at
`examples/_shared/site.yml:359-363`. The master plan's original
explanation was correct and F2's contradiction of it was a bad survey,
so the question the gate existed to answer did not actually need
asking.

The record stays rather than being tidied away, because the honest
account of the cost is useful. The gate was justified on "the cited
cause is demonstrably absent from the tree", which was false. It was
*worth running anyway*, for three things that were not in the plan
before it: the exact per-guest-size arithmetic D3 now uses; the
finding that 6 vCPU lifts an infra node only to where a plain
hypervisor already sits, which changes the shape question; and the
silent-clamp defect. But "the gate paid for itself" is not the same
claim as "the gate was correctly motivated", and only the first is
true here.

What a reviewer should take from it: the cheap check that would have
avoided the whole detour is reading `examples/_shared/site.yml`
alongside the collection, because that playbook is what CI deploys
with. F2 now says so, and phase 5 inherits it.
### D2 -- Land `slim-tier` first, and alone

The master plan says "land one topology at a time so a regression is
attributable". It is silent on order. `slim-tier` goes first because
it is where the evidence is strongest (two of three nodes pinned at
1.000, a 19% historic pass rate, and the smaller blast radius of one
job rather than three), and because at 3 x 6 vCPU it is the cheapest
shape in the table that changes anything: +6 vCPU, no extra RAM.

### D3 -- `slim-tier` becomes three 6 vCPU nodes, and the case for 8 is real

Of the menu, three 6 vCPU nodes is the smallest shape that raises the
infra hypervisors rather than only the total, which is the master
plan's own criterion. With 4a's arithmetic the predicted per-node
ledgers are **6 / 6 / 12** for a total of 24, doubling the tier:
the two infra nodes go `max(1, 6-4) = 2` so `floor(2 x 3) = 6`, and
the plain node goes `max(1, 6-2) = 4` so `floor(4 x 3) = 12`. The
plain node gains proportionally more, which the candidate table's flat
"Ledger 24" does not show.

**The argument against, which 4a surfaced and which a reviewer may
prefer:** 6 vCPU only lifts an infra hypervisor to a ledger of 6,
which is exactly where a *plain* hypervisor sits today. If the goal is
that no hypervisor is materially smaller than its siblings -- and the
master plan's criterion is that any shape leaving an infra hypervisor
small "leaves the failure in place" -- then 8 vCPU is the shape that
delivers it: ledgers 12 / 12 / 18, and a `SCHEDULER_TARGET_LOAD` bound
of 3.0 rather than 1.5 on the nodes that refuse.

This decision stays at 6 for now, for two reasons, and it is the
decision in this plan most likely to be argued with. First, cost: 8
vCPU is +12 vCPU per tier cloud against 6's +6, on an under-cloud the
master plan's *The under-cloud budget this spends* section treats as
the binding resource. Second, evidence: the measured p90 is 10
committed vCPU against a ledger of 12, so 24 already leaves real
headroom, and the refusals in phase 3's window were on the demand
bound (F3), where 6 vCPU already triples the infra nodes' bound from
0.75 to 1.5. If 4d's merge runs show demand refusals persisting at 6
vCPU, the answer is 8 -- and D3 should be revised then, on evidence,
rather than guessed at now.

A third option exists and is deliberately not taken: setting
`node_cpu_reservation_threads` per host in the CI inventory, which
`examples/_shared/site.yml:363`'s `when: ... is not defined` explicitly
supports. That would raise the infra nodes' ledger without buying any
vCPU at all. It is rejected because it departs from production
reservation values in CI, which the master plan's D1 keeps deliberately
-- the clouds are meant to admit the way a real cluster admits. It is
recorded here because it is the cheapest option and a reviewer should
see that it was considered and why it lost.

Node count stays at three, which F6 shows is forced: two would break
both `test_network_lifecycle` and the two `>=3 nodes` scheduler tests,
and the tier's purpose is its two database nodes.
### D4 -- `slim-primary` is a separate step, and may not happen in this phase

It has one ledger-3 node out of five, a cluster p90 fraction well
below the tier's, and it is three of the four cluster jobs. Its shape
choice should be made against what landing `slim-tier` actually
showed, not against the same table read twice. 4e therefore *decides*
whether to reshape `slim-primary` and records the reasoning either
way; a documented "not yet, and here is the evidence" is a valid
outcome of this phase.

### D5 -- The `slim-tier` timeout comes down in a later step, not the same one

Dropping `timeout_minutes` from 70 to 60 in the same change as the
reshape would make a timeout failure ambiguous between "the reshape
did not help" and "the timeout was cut too early". 4f does it after at
least five merge runs on the new shape, and only if the observed
wall-clock supports it.

### D6 -- The topology changes are prepared as a reviewable diff in this repository

F7 means this phase cannot push its own central change. Rather than
repeat 3e's shape -- work sitting uncommitted in another checkout,
blocking a Definition of done item for a day -- each actions-repo
change is written into this plan file as a complete diff under a
*Prepared changes* heading, with the operator applying it. The plan is
then self-contained and reviewable here, and the actions-repo commit
is a transcription rather than a re-derivation.

### D7 -- The phase measures itself against the phase 1 instrument

The falsifiable question is not "is the YAML changed" but "did the
ledger move and did the refusals stop". Both are already published
per run by the headroom probe (*Committed vCPU, per node*) and the
census (*Capacity guard census*), so every done-criterion about effect
reads those sections rather than inventing a measurement. This is also
what makes F3 checkable: if the demand refusals do not fall after the
reshape, the reshape did not address what was refusing.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | **Gate (D1). Establish why some CI hypervisors publish `cpu_schedulable` 1.** Read `_compute_reservations()` at `shakenfist/daemons/resources/main.py:102-129` and its caller at `:255-271`; the value is `max(1, cpu_threads - cpu_reservation_threads)` where `cpu_threads` is `psutil.cpu_count(logical=True)` *inside the guest* and the reservation is 2 everywhere (F2 lists every definition site). Then establish what the ledger-3 nodes actually report. Cheapest route first: read `/admin/resources` and `GET /nodes` from a live CI cluster, or the node metrics row, for a node known to be at ledger 3 -- `slim-tier`'s primary or sf1 -- and compare `cpu_threads`, `cpu_cores`, `cpu_max` and `cpu_schedulable` against a ledger-6 node like `slim-tier`'s sf2. Note that `_get_hybrid_core_counts()` runs `retval.update()` *after* `_compute_reservations()` at `:271`, so check whether it can overwrite any of these keys. Also check whether `cpu_schedulable_from_fallback` is set for those nodes (`shakenfist/scheduler.py:216-224`, `:383`, `:940`), because the fallback path approximates from `cpu_max` instead and a node taking it is answering a different question. Do not change any configuration value (Scope). Report the mechanism, whether a larger guest raises it, and if the cause is a defect, file an issue and record the number here. |
| 4b | medium | sonnet | none | Correct the master plan's phase 4 section against F1-F8: replace the `NODE_CPU_RESERVATION_THREADS=4` explanation with what 4a found, fix "(and the `-released` variants)" to name the one that exists, move the `functional-tests.yml` sentence out of the actions-repo list and restate it as the timeout question, note that `slim-primary`'s primary is not a hypervisor so the candidate table's `vCPU` column is under-cloud spend rather than scheduling capacity, and correct the structural-minimum row to name both `>=3 nodes` tests and to say `slim-tier` already sits at the `test_network_lifecycle` minimum. Cite file and line for each. Do not restate F2's reasoning at length -- link to this plan. |
| 4c | high | opus | none | **Decide and prepare the `slim-tier` reshape (D2, D3, D6).** Write the complete diff for `shakenfist/actions/ansible/ci-topology-slim-tier.yml` taking all three nodes from `cpu: 4` to `cpu: 6`, leaving `ram: 12288` and every `groups:` line untouched, into a *Prepared changes* section of this plan. The three instance blocks are at `:35`, `:72` and `:102`. State the predicted per-node ledgers and the predicted `SCHEDULER_TARGET_LOAD` bound per node, both derived from what 4a established rather than from F2's guess, so the first merge run can falsify the prediction. If 4a found a larger guest does *not* raise `cpu_schedulable` on those nodes, do not write the diff: record why the reshape would not work and what would. |
| 4d | medium | sonnet | none | After the operator applies 4c, read the first three merge runs on the new `slim-tier` and record, in this plan's Outcome: per-node ledgers from *Committed vCPU, per node*; the guard refusal count and dimension split from *Capacity guard census*; whether any `sufficient_idle_cpu` stage abort remains; and whether `test_network_lifecycle`, `test_affinity` and `test_database_tier` still run rather than skip (F6). Compare against 4c's predictions explicitly and say which were wrong. |
| 4e | high | opus | none | **Decide `slim-primary` (D4).** With 4d's evidence, choose between the two `slim-primary` candidate shapes, a smaller change, or none. If reshaping, prepare diffs for **both** `ci-topology-slim-primary.yml` and `ci-topology-slim-primary-released.yml`, which F4 shows are not structurally identical -- the released variant's primary is the network node and is not a hypervisor, so a mechanically copied diff would be wrong -- and leave the `sf-absent` phantom block untouched in the file that has it. If not reshaping, record the evidence for not doing so; that is a valid outcome. |
| 4f | low | sonnet | none | **Only after five merge runs on the reshaped tier, and only if wall-clock supports it (D5).** Drop `timeout_minutes` from 70 to 60 for the `Debian 12 tier` matrix entry in `.github/workflows/functional-tests.yml:482`, and rewrite the comment at `:471-474` which currently explains the bump by the tier having half `slim-primary`'s capacity. Record the observed run durations that justify it. If they do not, leave it and say so. |
| 4g | medium | sonnet | none | Close-out: set this phase `Complete` in the master plan's Execution table and the `docs/plans/index.md` row (4 of 8 becomes 5 of 8), write *What phase 5 inherits*, run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`, and confirm every Definition of done item by running it rather than reading it. |

4a gates everything. 4b can run in parallel with 4a only for the
claims 4a does not touch (F4, F5, F6, F8); the F2 correction waits.
4c needs 4a. 4d needs 4c applied and a merge run. 4e needs 4d. 4f
needs five runs after 4c. 4g is last.

## Prepared changes

### `slim-tier`: three 4 vCPU nodes become three 6 vCPU nodes

Prepared by 4c under D6, because F7 means this phase cannot push the
change itself. **Repository: `shakenfist/actions`** -- not this one --
**file: `ansible/ci-topology-slim-tier.yml`.** Three lines change, one
`cpu:` value per `sf_instance` block, and nothing else: `ram: 12288`
and every `groups:` line are deliberately untouched, because the role
assignments are what make primary and sf-1 infra nodes and F6 records
that the tier is already exactly at `test_network_lifecycle`'s minimum
of two hypervisors that are not the network node.

```diff
--- a/ansible/ci-topology-slim-tier.yml
+++ b/ansible/ci-topology-slim-tier.yml
@@ -35,7 +35,7 @@
     - name: Create a primary instance
       shakenfist.shakenfist.sf_instance:
         name: "t-{{instance_suffix}}-primary"
-        cpu: 4
+        cpu: 6
         ram: 12288
         disks:
           - "100@{{base_image}}"
@@ -72,7 +72,7 @@
     - name: Create sf-1
       shakenfist.shakenfist.sf_instance:
         name: "t-{{instance_suffix}}-1"
-        cpu: 4
+        cpu: 6
         ram: 12288
         disks:
           - "100@{{base_image}}"
@@ -102,7 +102,7 @@
     - name: Create sf-2
       shakenfist.shakenfist.sf_instance:
         name: "t-{{instance_suffix}}-2"
-        cpu: 4
+        cpu: 6
         ram: 12288
         disks:
           - "100@{{base_image}}"
```

Nothing else in the file scales with `cpu`. Both disks are fixed sizes
(`100@{{base_image}}` and `60@sf://label/ci-images/dependencies`), the
two `networkspecs` entries are fixed addresses, and the remaining
plays configure apt, pip, the mesh interface and the wheels. There is
no flavour indirection to satisfy -- `sf_instance` takes `cpu` and
`ram` directly.

### Predictions, and the arithmetic they come from

Three formulae, each verified in this worktree as part of writing this
section:

* `cpu_schedulable = max(1, cpu_threads - cpu_reservation_threads)`,
  `shakenfist/daemons/resources/main.py:126`. `cpu_threads` is
  `psutil.cpu_count(logical=True)` inside the guest, so it is the
  under-cloud instance's `cpu`.
* `limit_cpus = floor(cpu_schedulable * CPU_OVERCOMMIT_RATIO)`,
  `shakenfist/mariadb.py:24596`, with the ratio defaulting to **3.0**
  at `shakenfist/config.py:568`.
* The demand bound is `SCHEDULER_TARGET_LOAD * cpu_schedulable`, with
  the target defaulting to **0.75** at `shakenfist/config.py:523`. The
  guarded comparison is `cpu_load_1 + expected_demand <= target_load *
  cpu_schedulable` in `_demand_guard_clause()`
  (`shakenfist/mariadb.py:26024-26078`, the clause at `:26076-26077`).

The reservation itself is `(1 + ((network or database) ? 1 : 0)) * 2`
at `examples/_shared/site.yml:359-363` per 4a, so 4 threads on primary
and sf-1 and 2 on sf-2, and it is absolute: it does not move when the
guest grows.

| Node | Roles | Reserved threads | `cpu_schedulable` before -> after | `limit_cpus` (ledger) before -> after | Demand bound before -> after |
|---|---|---|---|---|---|
| primary | hypervisor, network, database | 4 | 1 -> 2 | 3 -> 6 | 0.75 -> 1.50 |
| sf-1 | hypervisor, database | 4 | 1 -> 2 | 3 -> 6 | 0.75 -> 1.50 |
| sf-2 | hypervisor | 2 | 2 -> 4 | 6 -> 12 | 1.50 -> 3.00 |

The "before" column is not a derivation: it is what run
[34681505274](https://github.com/shakenfist/shakenfist/actions/runs/34681505274)
measured (F1: ledgers 3, 3, 6), so the model reproduces the observed
state before it is used to predict the new one.

**Cluster totals.** Ledger **12 -> 24**, schedulable threads **4 -> 8**,
summed demand bound **3.00 -> 6.00**. Under-cloud spend rises from 12
to 18 vCPU for the tier cloud, with RAM unchanged at 3 x 12288 MiB.

**What 4d should see.** In the first merge run on the new shape, the
headroom probe's *Committed vCPU, per node* section should report
per-node ledgers of **6, 6 and 12** for a cluster total of **24**,
where the same section reported 3, 3 and 6 for a total of 12.

One under-cloud side effect to be aware of, since it is a consequence
of changing `cpu` specifically and not of the tier getting bigger:
each of these three under-cloud instances now draws 6 rather than 4
vCPU from its under-cloud hypervisor's ledger, and the under-cloud's
own feedforward term `demand_add = cpus * SCHEDULER_DEMAND_PER_VCPU`
(0.6, `shakenfist/config.py:532-533`) rises from 2.4 to 3.6 per
instance. That makes an under-cloud 507 on *creating* the tier
marginally more likely, which is the #3772 family rather than anything
this phase introduces, and it would show up as an ansible failure in
the `Create a primary instance` / `Create sf-1` / `Create sf-2` tasks
rather than as a test failure.

### How this prediction is falsified

Three distinguishable readings of the first merge run after the diff
is applied, which point at three different next actions:

1. **The model is wrong.** *Committed vCPU, per node* reports anything
   other than 6, 6, 12 for the three `slim-tier` nodes -- in
   particular, primary and sf-1 still reporting a ledger of **3**
   while sf-2 moves to 12, which would mean the infra-role reservation
   is not the absolute 4 threads 4a measured but something that scales
   with guest size. Next action: re-read the rendered `/etc/sf/config`
   and the `node_metrics` row for primary, not buy more vCPU. The
   sharper single reading is `cpu_schedulable` itself: it must be 2 on
   primary and sf-1 and 4 on sf-2.
2. **The model is right and F3's bound is what binds.** The ledgers
   read 6, 6, 12 *and* the *Capacity guard census* still shows
   refusals of the same order as the 176 / 139 / 163 in F3's window,
   still falling on the `demand` dimension alone. The reshape did what
   it predicted and was not enough; next action is D3's recorded
   revision to 8 vCPU (ledgers 12, 12, 18; bound 3.00 on the infra
   nodes) or the sibling plan's guard work, not a re-derivation of the
   arithmetic.
3. **The binding dimension moved.** The ledgers read 6, 6, 12 and the
   census refusals shift off `demand` onto an allocation dimension
   (`cpus`, `memory_mb` or `disk_gb`), which F3 saw *none* of. That
   would be a genuinely new finding -- the tier would then be limited
   by the ledger or by RAM rather than by load -- and it is an
   argument about `ram: 12288`, which this diff deliberately does not
   touch.

A job that still fails while the ledgers read 6, 6, 12 is reading 2 or
3, not a falsification of this section on its own; the census
dimension split is what separates them.

### Operator checklist

D2 lands one topology at a time so a regression is attributable, and
F7 means every step below happens in a repository this session cannot
push to.

1. In the **`shakenfist/actions`** checkout -- a separate repository
   with its own pull request, not this one -- branch off `main`.
2. Apply the diff above to `ansible/ci-topology-slim-tier.yml`: three
   `cpu: 4` lines become `cpu: 6`, at the `primary`, `sf-1` and `sf-2`
   `sf_instance` blocks (`:35`, `:72`, `:102`).
3. Confirm the change is exactly three lines in one file:
   `git diff --stat` shows `1 file changed, 3 insertions(+), 3
   deletions(-)`, and `git diff | grep -E '^[+-] *(ram:|groups:)'`
   prints nothing.
4. **Do not change `ci-topology-slim-primary.yml` or
   `ci-topology-slim-primary-released.yml` in this commit or this pull
   request.** They are 4e's decision (D4), F4 records that they are
   not structurally identical to each other, and landing them together
   would make a regression unattributable.
5. Open and merge the pull request in `shakenfist/actions`. Note that
   `.github/workflows/functional-tests.yml` calls
   `shakenfist/actions/.github/workflows/smoke-cluster.yml@main`, so
   there is no pin to bump here and the new shape is live for the very
   next PR and merge-queue run, including any already in flight --
   merge it when the queue is quiet.
6. Tell 4d which merge run is the first on the new shape, so it reads
   the right three runs.
7. Leave `timeout_minutes: 70` on the `Debian 12 tier` matrix entry
   alone (D5); 4f revisits it after five merge runs.

## Risks and mitigations

| Risk | Mitigation | Who checks |
|------|-----------|------------|
| The reshape raises the four nodes that were never the problem and leaves the infra nodes at ledger 3, spending under-cloud capacity for nothing. | D1's gate. 4a establishes the mechanism before any YAML changes, and 4c is instructed not to write a diff if a larger guest would not move those nodes. | 4a, and the operator before applying 4c. |
| The reshape works on the ledger and the jobs still fail, because F3's demand bound is what actually refuses. | 4c predicts the new `SCHEDULER_TARGET_LOAD` bound per node as well as the ledger, and 4d reads the census dimension split rather than only the pass/fail. If demand refusals persist at the new size, that is evidence for the sibling plan's guard work and against more vCPU, and it is recorded as such rather than answered with another resize. | 4d. |
| Consolidating the tier breaks a structural minimum and a test starts skipping, so the topology looks greener because less is asserted. | D3 fixes the node count at three, and 4d's done-criteria name the three tests by file and require that they **ran**, not that the job passed. F6 records that `slim-tier` is already at `test_network_lifecycle`'s minimum, so there is no slack to spend. | 4d. |
| A diff applied mechanically to both `slim-primary` files breaks the released variant, whose primary has a different role set. | F4 records the difference and 4e's brief names it. The released variant also has no `sf-absent` phantom, so a copied block would introduce one. | 4e, and the operator applying it. |
| The actions-repo changes sit unpushed and block close-out, as 3e did. | D6: every actions-repo change is a complete diff inside this plan, reviewable here, with the operator transcribing. Nothing in this repository waits on it except 4d, which waits on a merge run anyway. | The operator. |
| Dropping the timeout too early turns a capacity finding into a timeout failure. | D5 separates the two changes by at least five merge runs and makes 4f conditional on observed duration. | 4f. |
| 4a finds a defect and the phase grows a bug fix. | Out of scope by Scope; 4a files an issue and records the number. If the defect means reshaping cannot work, the phase's correct outcome is to say so, not to fix the daemon. | 4a. |

## Definition of done

Falsifiable, in order:

1. This plan records, with file and line references, the mechanism by
   which a CI hypervisor comes to publish `cpu_schedulable` 1, and
   states whether raising its guest `cpu` raises it. A reader can
   check the claim against the named code without re-deriving it.
   **Met by 4a:** `examples/_shared/site.yml:359-363` and
   `daemons/resources/main.py:126`, with the per-guest-size table in
   D3.
2. The master plan's phase 4 section attributes the ledger to the
   line that actually sets it, so a reader is not sent to the wrong
   repository as F2 was. `grep -c 'examples/_shared/site.yml'
   docs/plans/PLAN-ci-cloud-sizing.md` is at least 1, and
   `grep -c 'is 2 everywhere' docs/plans/PLAN-ci-cloud-sizing.md` is
   0.
3. The master plan's phase 4 section names exactly the topology files
   that exist. `ls shakenfist/actions/ansible/ci-topology-slim-*` has
   three entries matching `slim-primary`, `slim-tier` and
   `slim-primary-released`, and the section names those three and no
   others.
4. The master plan's structural-minimum list names both tests that
   require three nodes, and `grep -c 'len(nodes) < 3'
   shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_scheduler.py`
   equals the number it claims.
5. Either `ci-topology-slim-tier.yml` has three nodes at `cpu: 6` and
   a merge run on it reports per-node ledgers of 6, 6 and 12, or this
   plan records why the reshape was not made with the 4a evidence that
   decided it. Not both, and not neither.
6. If the tier was reshaped: a merge run after it shows
   `test_network_lifecycle`, `test_affinity` and
   `test_database_tier` each reporting a verdict other than
   `SKIPPED`. Read from the stestr output, not inferred from the job
   being green.
7. If the tier was reshaped: this plan records the *Capacity guard
   census* refusal count and dimension split for three merge runs
   before and three after, and states whether the demand refusals
   fell. A reshape that doubled the ledger and did not move the
   refusals is a finding, and saying so satisfies this item.
8. `slim-primary`'s disposition is recorded either as an applied diff
   for both of its files or as a decision not to reshape with the
   evidence behind it. An unanswered `slim-primary` does not satisfy
   this.
9. The `slim-tier` `timeout_minutes` is either 60 with the observed
   durations recorded, or still 70 with the reason recorded.
10. No configuration default changed: `git diff develop -- shakenfist/config.py
    shakenfist/deploy/collection/roles/node/defaults/main.yml` is
    empty for this phase's commits.
11. `python3 tools/check-plan-status.py` passes, and `pre-commit run
    --all-files` passes in the main repository.

## Outcome

**In progress.** Three of seven steps have landed; the remaining four
are blocked on the operator and on merge runs, not on work this
session can do.

| Step | State |
|---|---|
| 4a | **Done.** Gate discharged, and it corrected F2 rather than the master plan. See *4a -- what the gate found*. Filed [#4201](https://github.com/shakenfist/shakenfist/issues/4201). |
| 4b | **Done**, though not as the step table specifies -- see the deviation below. |
| 4c | **Done.** The diff is in *Prepared changes*, with predictions, a three-way falsification statement and the operator's checklist. |
| 4d | **Blocked** on the operator applying 4c to `shakenfist/actions` and on three merge runs after it. |
| 4e | Blocked behind 4d. |
| 4f | Blocked: D5 requires five merge runs on the new shape first. |
| 4g | Blocked: its Definition of done items depend on 4d-4f. |

**Deviation from the step plan, declared rather than buried.** 4b is
listed as a `sonnet` sub-agent step. It was done inline by the
management session instead, because by the time it ran its content had
changed: 4a had shown that the text 4b was meant to correct was text
*this plan's own survey* had got wrong, and the correction touched the
same three files the session was already holding open. Dispatching it
would have raced those edits for no benefit. 4a and 4c went to
sub-agents as the table specifies.

**What the operator owes, in order.** Apply *Prepared changes* to
`shakenfist/actions` as its own pull request, when the merge queue is
quiet -- `.github/workflows/functional-tests.yml:485` references
`shakenfist/actions/.../smoke-cluster.yml@main` unpinned, so the new
shape takes effect on the next run, including runs already in flight.
Then 4d reads the first three merge runs.

**One thing 4c found that the plan did not anticipate**, recorded here
because it is a cost of this specific change rather than of reshaping
in general: the under-cloud charges each instance a feedforward demand
of `cpus x SCHEDULER_DEMAND_PER_VCPU` (0.6, `config.py:532`), so a tier
node's charge against the *under-cloud's* scheduler rises from 2.4 to
3.6 and its under-cloud ledger draw from 4 to 6. A refusal there is the
#3772 family arriving one level up, and it would surface as an ansible
failure in the `Create a primary instance` / `Create sf-1` / `Create
sf-2` tasks rather than as a test failure. Only `primary` carries
`await: true`, so sf-1 and sf-2 would fail at create-API time.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan, and in particular on:

* **F2, and whether D1's gate is the right call.** This is the
  decision the phase rests on and the one a reviewer should push back
  on. State what you expect 4a to find and what reading would change
  D3's shape choice. If you think the gate is over-caution, say so
  before 4a runs rather than after.
* **Whether the `slim-tier` shape should be 6 or 8 vCPU**, given that
  the measured p90 is 10 committed vCPU against a ledger of 12 and
  that the demand bound (F3), not the ledger, is what refused
  everything in the window. D3 chose 6; the case for 8 is that it
  quadruples the bound on the small nodes rather than tripling it.
* **That 4a must not change a configuration value to see what
  happens.** The reservation and the overcommit ratio are deliberately
  production values in CI (master plan D1), and an experiment that
  changes them answers a different question than the one asked.
* **D6's diff-in-the-plan arrangement**, which is new. Confirm the
  operator is willing to transcribe, or propose a better seam for a
  phase whose central change lives in a repository this session
  cannot push.
