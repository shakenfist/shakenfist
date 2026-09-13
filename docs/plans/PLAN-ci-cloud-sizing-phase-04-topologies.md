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

### F2 -- ...but the stated cause of the ledger-3 nodes does not exist

**This is the finding that changes the phase.** The phase 4 section
explains those ledgers as:

> the binding constraint is `NODE_CPU_RESERVATION_THREADS=4` on the
> 4-thread infra nodes -> `cpu_schedulable=max(1,0)=1` -> limit 3

There is no 4 anywhere. `node_cpu_reservation_threads` is **2**, set
once at
`shakenfist/deploy/collection/roles/node/defaults/main.yml:24` and
templated once at `roles/node/templates/config:30`. Nothing in the
collection, nothing in `shakenfist/actions` (no match for
`reservation` in its `ansible/` or `.github/` trees), and nothing in
the CI inventory overrides it per host or per role.

The arithmetic is not in doubt:
`_compute_reservations()` publishes
`cpu_schedulable = max(1, cpu_threads - cpu_reservation_threads)`
(`shakenfist/daemons/resources/main.py:126`), and
`_derive_cpu_memory_limits()` takes
`limit_cpus = floor(cpu_schedulable x CPU_OVERCOMMIT_RATIO)` with the
ratio at 3.0 (`shakenfist/mariadb.py:24596`,
`shakenfist/config.py:568`). A 4-thread guest with a reservation of 2
therefore has `cpu_schedulable` 2 and a ledger of **6** -- which is
what four of `slim-primary`'s five hypervisors show. The ledger-3
nodes have `cpu_schedulable` 1, which needs `cpu_threads <= 3`.

Every topology file creates every node with `cpu: 4`. So identically
sized guests are reporting different thread counts, and the nodes that
report fewer are exactly the ones carrying an infra role beyond plain
hypervisor:

| Node | Groups | Measured ledger |
|---|---|---|
| `slim-primary` sf1 | `hypervisors, network_node, allsf` | 3 |
| `slim-primary` sf2-sf5 | `hypervisors, allsf` | 6 |
| `slim-tier` primary | `allsf,database_node,primary_node,network_node,hypervisors` | 3 |
| `slim-tier` sf1 | `hypervisors, database_node, allsf` | 3 |
| `slim-tier` sf2 | `hypervisors, allsf` | 6 |

The correlation is perfect and the mechanism is unknown. One candidate
worth checking first: `config.py:614`'s own description says the
Ansible templating folds in "any historical infra-role bump", which
reads like documentation of a mechanism that has since been removed --
so the bump may have been deleted while the observed behaviour has
another cause entirely.

**Why this gates the phase.** Phase 4's thesis is that giving the
infra hypervisors more vCPU raises their ledger. That follows only if
their `cpu_schedulable` tracks their guest vCPU count. If it does not
-- if something else is holding it at 1 -- then growing those guests
buys nothing on the nodes that actually refuse, and the phase spends
under-cloud capacity to move the four nodes that were never the
problem. Choosing a shape before this is known would be sizing against
an unexplained number, which is the same mistake the master plan's own
*A node can record twice its own ledger* section exists to prevent.

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

## Decisions

### D1 -- F2 is a gate, not a step

Step 4a establishes why the infra nodes publish `cpu_schedulable` 1,
and **no topology file is edited until it reports.** If it finds the
cause is the guest's thread count and that a larger guest raises it,
the rest of the phase proceeds as the master plan intended. If it
finds something else -- a resources-daemon defect, a libvirt topology
artefact, a stale metrics row -- then 4c's shape choice is made with
that knowledge, and the phase may correctly conclude that reshaping is
not the fix and stop.

The reviewer most likely to object will say this is over-caution for
an obvious change: four of five nodes behave as the arithmetic
predicts, so the fifth is probably a quirk that a bigger guest fixes
anyway. The counter-argument is that this phase's entire justification
is spending more under-cloud capacity per run to raise a specific
number on specific nodes, the master plan says in as many words that
"any shape that leaves an infra hypervisor at a ledger of 3 leaves the
failure in place", and the cited explanation for that ledger is
demonstrably absent from the tree. Spending the capacity and finding
out afterwards is the expensive order to do this in -- a merge run
costs six clouds, and the answer is one reading of `/admin/resources`
against one node's metrics row.

### D2 -- Land `slim-tier` first, and alone

The master plan says "land one topology at a time so a regression is
attributable". It is silent on order. `slim-tier` goes first because
it is where the evidence is strongest (two of three nodes pinned at
1.000, a 19% historic pass rate, and the smaller blast radius of one
job rather than three), and because at 3 x 6 vCPU it is the cheapest
shape in the table that changes anything: +6 vCPU, no extra RAM.

### D3 -- `slim-tier` becomes three 6 vCPU nodes

Of the menu, this is the smallest shape that raises the infra
hypervisors rather than only the total, which is the master plan's own
criterion. Predicted per-node ledgers 6 / 6 / 12 for a total of 24,
doubling the tier -- **conditional on F2 resolving in the direction D1
describes.** `tier as 3 x 8 vCPU` reaches 42 but costs +12 vCPU for a
ledger the tier has never needed; the measured p90 is 10 committed
vCPU against a ledger of 12, so 24 leaves real headroom without
buying a third more under-cloud CPU.

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
2. `grep -rn 'NODE_CPU_RESERVATION_THREADS=4\|reservation leaves'
   docs/plans/PLAN-ci-cloud-sizing.md` returns nothing: the phase 4
   section no longer attributes the ledger to a value that is not in
   the tree.
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
