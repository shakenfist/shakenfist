# Phase 0: decisions, and an inventory of what scarcity catches

Parent plan: [PLAN-ci-cloud-sizing.md](PLAN-ci-cloud-sizing.md).

**Planning effort:** high. Nothing here is mechanical -- every item is
a judgement about what CI is for and what a failure means, and the
inventory determines what phase 3 has to build before any cloud is
allowed to grow.

## Context

The master plan established that the nested CI clouds are sized on the
wrong axis: what binds is the scheduler's admission ledger
(`cpu_schedulable x CPU_OVERCOMMIT_RATIO`), not real CPU or memory,
which sit at roughly 18% and 50% of allocation with zero swap-out
anywhere. `slim-primary` has a 27 vCPU ledger and passes ~81% of merge
runs; `slim-tier` has about 12 and passes 19%, its failures being the
`507 sufficient_idle_cpu` family.

Phase 0 does not change any topology. It exists because two of the
plan's later phases are only safe if a set of questions are answered
first, and because the coverage the undersized clouds give us today is
about to be removed by making them bigger. This phase decides the
questions and writes down what that coverage actually is.

## Scope

**In scope:**

* Answering the master plan's six open questions as numbered decisions
  (D1-D6), plus two the survey added (D7-D8).
* A *scarcity inventory*: every distinct failure signature the current
  clouds produce because they are small, each with a disposition of
  *defect to fix*, *behaviour to assert*, or *test bug*.
* Correcting, at source, the master plan claims the survey found to be
  wrong.
* Filing or annotating issues so that no signature in the inventory
  depends on a small cloud to be remembered.

**Out of scope:**

* Any change to `ci-topology-*.yml`, to `smoke-cluster.yml`, or to the
  test suites. Phase 1 instruments, phase 3 asserts, phase 4 reshapes.
* Fixing #3772, #3565 or #3496. This phase gives each a disposition and
  makes sure a bigger cloud cannot silently close it. Fixing them is
  scheduler work on its own axis.
* Anything about the under-cloud's own capacity. #3696 benefits from
  this plan and is not a deliverable of it (D8).

## What the survey found

The master plan was written yesterday, so most of it still holds. Three
claims did not survive checking, and they are corrected here and at
source.

1. **`memory_available` is `MemAvailable`, not `MemFree`.** Open
   question 5 asked whether page cache inflates the denominator in
   `Scheduler._has_sufficient_ram`. It does not: the resources daemon
   publishes `psutil.virtual_memory().available`
   (`shakenfist/daemons/resources/main.py:293`), which already excludes
   reclaimable cache. A `sufficient_idle_memory` refusal therefore means
   the node genuinely had little memory available, and the question
   becomes a different one -- see D5.

2. **CI cannot set a per-host reservation without a code change.**
   Open question 1 assumed `node_cpu_reservation_threads` could simply
   be overridden from CI's inventory. `examples/_shared/site.yml:359`
   does honour a pre-set value, but CI's inventory is *generated* by
   `tools/ci-make-inventory.py` in `shakenfist/actions`, whose
   `render_node_vars()` emits a fixed block (`node_name`,
   `node_egress_ip`, `node_egress_nic`, `node_mesh_ip`,
   `node_mesh_nic`) with no hook for arbitrary host vars. Lowering the
   reservation in CI would therefore mean either extending that
   generator or passing a cluster-wide `--extra-vars`, which is not the
   same thing. This makes the option strictly more expensive than the
   master plan assumed, and it feeds D1.

3. **The tier's ledger is 12 by derivation and 10 by observation, and
   nothing explains the gap.** `_derive_cpu_memory_limits`
   (`shakenfist/mariadb.py:23969`) computes
   `limit_cpus = floor(cpu_schedulable x CPU_OVERCOMMIT_RATIO)`, which
   mirrors `_has_sufficient_cpu` exactly, so `slim-tier`'s three
   hypervisors should reconcile to 3 + 3 + 6 = 12. Issue #3907 records
   the `cluster_capacity` singleton reporting `limit 10`. The obvious
   candidate explanation -- that a node without a capacity row
   contributes nothing to `total_cpus`, which the reconciler restricts
   to `capacity_nodes` -- cannot produce 10 from 12, because dropping a
   node subtracts 3 or 6. The discrepancy is real and unexplained; D7
   assigns it to phase 2 rather than guessing now.

**These three corrections are applied to the master plan in this
same commit**, so no later step should redo them: open questions 1
and 5 carry their corrected premise, the Situation section quotes
the tier's ledger as "12 by derivation, 10 by observation", and
every open question now points at the decision below that settled
it.

Everything else checked out: the `sf-absent` phantom is present and
commented in `ci-topology-slim-primary.yml`; `functional-tests.yml`
does hardcode `10.0.0.20`-`10.0.0.24` for its random upload target;
`nodelifecycletests.sh` does require a script host, a network node and
two further victims, all distinct; and `AdminResourcesEndpoint`
(`shakenfist/external_api/admin.py:103`) is admin-only and returns
`Scheduler.summarize_resources()`, which publishes `cpu_hard_max`,
`cpu_measured`, `cpu_committed` and the memory equivalents per node --
so phase 1's probe needs no new server code.

## Decision items

### D1 -- Widen the nodes; keep the production reservation (open question 1)

**Decision:** CI nodes gain vCPUs. `NODE_CPU_RESERVATION_THREADS` and
the `examples/_shared/site.yml` derivation stay at their production
defaults in CI.

**Reasoning:** the reservation arithmetic on small nodes is not
incidental to our defects, it *is* one of them -- #3813 was
unsatisfiable precisely at `cpu_schedulable < 4`, which is the value
every CI hypervisor publishes. A CI fleet that no longer produces small
`cpu_schedulable` values would not have caught it. Survey finding 2
also shows the override is not free. Widening costs under-cloud vCPU,
which is the resource we have (88 of 234 free at measurement) rather
than the one we do not (RAM).

**The counter-argument, for the record:** a reviewer could reasonably
say that CI's job is to test the software, not the deployment sizing,
and that buying ledger with a config override is cheaper than buying it
with vCPUs. The answer is that the reservation *is* software here --
it is an input to an admission decision that has already been wrong
once -- and that keeping one fleet where `cpu_schedulable` is small is
worth more than the vCPUs it costs.

### D2 -- `slim-tier` is database-tier coverage, sized for parity (open question 2)

**Decision:** `slim-tier` exists to exercise a multi-instance
`sf-database` tier. It is sized for a ledger comparable to the other
cluster topologies, and it stops being the fleet's scarcity topology.

**Reasoning:** at 19% it is not delivering the coverage it was built
for. `test_database_tier` skips when it sees fewer than two database
nodes, so the assertion it exists to make is only reached on a run that
gets that far -- and four runs in five do not. A job that usually fails
also stops being read, which is a slower and worse failure than a job
that is honestly deleted.

### D3 -- The headroom band's *form* now, its numbers in phase 2 (open question 3)

**Decision:** the band is expressed over a whole job as:

* `p90(committed vCPU cluster-wide) / ledger` -- warn above an upper
  bound (oversubscribed, 507 risk) and below a lower bound (oversized).
* any capacity-stage refusal observed during a job that otherwise
  passes is itself a warning, independent of the ratio. There are
  four such stages, not three: `sufficient_idle_cpu`,
  `sufficient_idle_memory`, `sufficient_free_disk` (disk *space*)
  and `sufficient_idle_disk` (disk *bandwidth*). This clause
  originally named three, using the bandwidth stage where it meant
  disk capacity; the phase 1 survey corrected it.

Provisional bounds are 0.70 and 0.35. Phase 2 replaces them with
numbers derived from the measured distribution, or keeps them and says
why.

**Reasoning:** committing to the *form* now is what lets phase 1 build
the summary; committing to the *numbers* now would be inventing them.
The second clause matters more than the first: a refusal is a
point-in-time fact that a p90 can hide entirely.

### D4 -- Node lifecycle keeps five hypervisors; the IP list goes anyway (open question 4)

**Decision:** the `Node lifecycle` job keeps its current node count
through this plan. Independently of that, the hardcoded
`10.0.0.20`-`10.0.0.24` upload-target list in `functional-tests.yml` is
replaced with a pick derived from the API, in phase 4.

**Reasoning:** the lifecycle test needs a script host, a network node
and two distinct victims, and it kills two of them; it is the one job
where node count is load-bearing for the test rather than for capacity.
It is also the healthiest job in the fleet at 92%, so there is nothing
to buy. The IP list is a separate matter: it is a coupling between a
workflow file and a topology file that will break silently the moment
any topology changes, which is exactly what phase 4 does.

### D5 -- Memory is a real second constraint (open question 5, corrected)

**Decision:** treat memory as a genuine binding dimension, not an
artefact. No topology's per-node RAM is reduced below the measured p90
plus a margin phase 2 sets, and phase 1's probe records memory headroom
alongside CPU. Phase 2 must also measure capacity refusals per stage --
cpu, memory, disk space and disk bandwidth counted separately, not as
one combined number -- since a reshape that relaxes one stage can
tighten another. The phase 1 survey corrected this item too: it
originally said "cpu, memory and disk", which collapses the two
distinct disk stages, only one of which is a capacity check at all.

**Reasoning:** survey finding 1 removes the reason to disbelieve the
one observed `sufficient_idle_memory` refusal. The measured peak usage
(4.9-7.6 GB of 12 GB on `slim-primary`, 7.5-10.2 GB on the tier) says
there is *some* room, but it also says a three-hypervisor topology uses
most of a 12 GB node -- so the RAM saving comes from having fewer nodes,
not from making each node smaller.

### D6 -- The `sf-absent` phantom stays, and every phase says so (open question 6)

**Decision:** `slim-primary` keeps its unreachable `sf-absent`
hypervisor. Every phase plan that touches a topology file restates this
in its own scope section rather than relying on the comment in the
topology file.

**Reasoning:** it is the regression guard for the 2026-07-20 deploy
where one dead hypervisor aborted the deploy on every healthy node. A
node that exists only to be absent is exactly the kind of thing a
reshaping pass deletes as an oversight, and the comment in the file has
already had to say "DO NOT FIX THIS" in capitals once.

### D7 -- The 12-versus-10 ledger discrepancy is phase 2's to resolve (survey)

**Decision:** phase 2 reconciles the live derivation in
`Scheduler._has_sufficient_cpu` against the reconciled
`cluster_capacity.total_cpus` for the same cluster, and reports which
is right. Until it does, this plan quotes both and asserts neither.

**Reasoning:** a sizing decision made against the wrong ledger is wrong
by construction, and the two figures disagree by an amount no candidate
explanation produces. #3882 (the reconciler does not log the drift it
corrects) is why this cannot be settled from logs after the fact, and
is a reason to do it while a cluster is live.

### D8 -- #3696 is a beneficiary, not a deliverable (survey)

**Decision:** the under-cloud saturation issue -- self-hosted runners
losing communication mid-test when concurrent merge groups saturate the
under-cloud -- is named in this plan as something the RAM reduction
should help, and is not scoped as work here.

**Reasoning:** a merge run allocating ~95% of the under-cloud's
physical memory is a plausible contributor, and a plan that quietly
claimed to fix it would take credit for a correlation. Phase 2's
measurement is the place to say whether the reduction moved it.

## The scarcity inventory

The deliverable phase 3 is built from. Each signature is what a small
cloud produces today, and what happens to that coverage when the cloud
grows.

| Signature | Issues | Disposition |
|-----------|--------|-------------|
| Instance create refused `507 sufficient_idle_cpu` under suite concurrency | #3772 (umbrella, open); #3498, #3602, #3670, #3728, #3749, #3767 (closed members) | **Behaviour to assert.** Phase 3 fills a cluster deliberately and asserts what the server does today. #3772 stays open: its own verdict is that a bare 507 is the wrong answer to a transient condition, and that is unchanged by a bigger cloud. |
| Instance create refused `507 sufficient_idle_disk`, a rate predicate rather than a ledger | #3772 (umbrella, open) | **Behaviour to assert; sizing does not address it.** `_has_idle_disk_bandwidth()` (`shakenfist/scheduler.py:329`) checks disk-busy rate against a metric unrelated to `cpu_schedulable` or memory. A 2026-08-21 comment on #3772 records an occurrence at exactly this stage on the single-node smoke topology, under a `pull_request` event, with the node hosting only two instances and every other capacity stage passing -- a cluster nowhere near full. Widening vCPUs does nothing for it. Phase 3's saturation test must cover this stage directly rather than assume the CPU-stage test also exercises it. |
| Claim growth refused because the concurrent suite holds the cluster | #3907 (closed) | **Test bug, already fixed**, by routing success-asserted claim requests through a transient-tolerant retry. Phase 3 keeps a test that exercises the refusal path directly, so the retry cannot mask a genuine regression in claim admission. |
| Soft affinity loses to resource filters; instances that should share a node do not | #3565 (closed 2026-08-31) | **Defect to fix**, not here. This is the most frequent `slim-primary` failure in the sampled window. A 2026-08-26 comment on the issue traces the most recent occurrence to `sufficient_idle_memory`, not `sufficient_idle_cpu` -- the candidate shapes consolidate onto fewer, larger hypervisors, which raises instances per node, so growing the cloud may relax the CPU filter while tightening the memory one instead of making the test uniformly greener. Fixing the scheduler is still not this plan's job. Phase 3 gives it a deterministic reproduction (fill the affinity target, then place) so it stops depending on ambient load. *Correction (2026-09-01):* the disposition "defect to fix" did not survive contact with a full trace. scheduler-reservations phase 6 closed #3565 on a test change: the candidate set had collapsed to a single node before affinity was scored, so there was no tiebreak to lose and no scheduler defect behind it. The deterministic reproduction phase 3 owes it is still worth having -- it is now coverage of a documented guarantee rather than a hunt for a bug -- but this row's classification should be read as *test bug*. |
| Targeted create silently lands on a different node | #3496 (open) | **Defect to fix**, not here. Related but distinct: `force_placement` tests cannot fall back to a less loaded node, so they fail first under scarcity. Record the interaction; phase 3 asserts the documented behaviour of a targeted create against a full node. Deliberately **not** given a heads-up comment, unlike #3565 and #3772: this issue's recorded occurrence is a wrong-node placement with no refusal and no stage trace at all, and the one capacity mode it names it hands off to #3772, so a note predicting that it will reproduce less often would assert a connection its own evidence does not carry. |
| Demand guard refuses every placement below four schedulable threads | #3813 (closed) | **Fixed, and the reason for D1.** No further coverage needed beyond keeping a fleet where `cpu_schedulable` is small, which D1 does. |
| Runners lose communication when concurrent merge groups saturate the under-cloud | #3696, #3718 (open) | **Out of scope** per D8; the family has two observed shapes -- a runner dying mid-test, and a nested node never booting far enough to answer SSH. Named so that a later reduction in footprint can be checked against it. In the same window as the two dead runners, three other attempts by the same PR failed with `507 sufficient_idle_cpu` (citing #3498 and #3670), which suggests the under-cloud shortage and the inner-cloud 507s are the same shortage observed at two levels -- a reading the issue itself calls "well supported by the surrounding evidence" rather than proven. |

The sweep also turned up hits that are not scarcity signatures, and
are deliberately not rows above. #3516 (sf-sidechannel orphans agent
operations in `executing`) is a stuck-state defect with no capacity
trigger. #3770 (the Guests suite stalling because
`_await_agent_state`'s deadline is renewed by any event, not only the
one it is actually waiting for) is a logic bug in the wait itself, not
a resource refusal. #3720 (`ImageMissingFromCache` on a redirected
start) is a cache-consistency defect independent of cluster size.
#3652 and #3669 are deterministic CI test bugs that fail regardless of
how much capacity is available. All five matched the search only
because the searched words appear somewhere in their text, not because
they describe scarcity.

One further trap is worth recording here. #3602 (closed) and #3603
(open) are the same test, `test_disappearing_source_instance`, with
two entirely different causes: #3602 is the `507` scarcity failure
already counted as a member of the #3772 umbrella above, while #3603
is a genuine defect where an instance built from a cached image whose
source URL has since gone missing enters an error state. #3603 is not
a scarcity signature and does not belong in this table. Matching by
test name rather than by cause would conflate the two.

Two properties of this table are the point of it:

* Nothing in it is closed by growing a cloud. Two entries are defects
  that a bigger cloud makes *less visible*, which is the failure mode
  the user asked to avoid.
* Every entry that is "behaviour to assert" names a test phase 3 owes,
  and phase 4 is gated on those tests existing.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | medium | sonnet | none | Sweep the issue tracker for capacity-shaped CI failures beyond the six already in the inventory table above: search closed and open issues for `sufficient_idle_cpu`, `507`, `InsufficientResources`, and for the phrase "suite concurrency". For each hit not already in the table, decide whether it is the same signature as an existing row (add the number to that row) or a new one (add a row, with a disposition drawn from the three permitted values). Do not open or modify any issue in this step -- report candidates instead. Edit only `docs/plans/PLAN-ci-cloud-sizing-phase-00-decisions.md`. |
| 0b | high | opus | none | Post a comment on #3565 and on the #3772 umbrella recording that a CI topology change is coming which will reduce how often the issue reproduces, linking this phase plan, and stating the deterministic reproduction phase 3 owes it. Do not change state or labels. #3496 is deliberately excluded -- see its inventory row for why -- so a draft written for it should be discarded rather than posted. Judgement is needed on the wording: these must read as "this will get quieter, and that is not a fix", not as a status update. **The management session reviews the exact text before anything is posted** -- this writes to a public tracker. |
| 0c | low | haiku | none | Set the phase 0 row to `Complete` in the master plan's Execution table and update the `docs/plans/index.md` arithmetic to `1 of 7`, then run `python3 tools/check-plan-status.py`. Do this only after 0a and 0b are reviewed. |

The survey corrections that would otherwise have been a step here
were made in the planning commit, per the note under *What the
survey found*.

## Risks and mitigations

* **The inventory is incomplete, and phase 4 removes coverage nobody
  wrote down.** Step 0a sweeps the tracker, but the tracker only holds
  what someone bothered to file. *Mitigation:* phase 1 lands before
  phase 4 and counts refusals per run, so a signature that only ever
  showed up as an unfiled flake still shows up as a number. The
  management session checks that phase 2's refusal counts have no
  signature the inventory lacks.
* **D1 is the decision most likely to be argued with.** Buying ledger
  with vCPUs when a config override would do is a real cost.
  *Mitigation:* the reasoning is written above rather than implied, and
  the survey finding that the override needs a generator change is
  recorded so the trade can be re-opened with the true price on it.
* **Deciding the band's numbers in phase 2 could slip into never.**
  *Mitigation:* D3 fixes provisional bounds now, so phase 5 has
  something to enforce even if phase 2's analysis is thinner than
  hoped.
* **Commenting on issues is outward-facing.** Step 0b writes to a
  public tracker. *Mitigation:* the management session reviews the exact
  comment text before it is posted, and the step is explicitly barred
  from changing state or labels.

## Definition of done

Falsifiable, in order:

1. `docs/plans/PLAN-ci-cloud-sizing.md` contains no statement that
   contradicts the three survey findings above. Specifically: no
   sentence asks whether page cache inflates `memory_available`; open
   question 1 names `ci-make-inventory.py`; and the tier's ledger is
   never given as a single unqualified number. *(Done in the planning
   commit; re-check rather than redo.)*
2. Every open question 1-6 in the master plan carries a pointer to
   the decision D1-D6 that settled it. *(Done in the planning
   commit.)*
3. The inventory table has a row for every issue returned by a tracker
   search for `sufficient_idle_cpu`, and each row's disposition is one
   of the three permitted values.
4. #3565 and #3772 each carry a comment linking this phase plan and
   stating that a topology change will change their reproduction rate
   without fixing them, and neither had its state or labels changed.
   #3496 carries no such comment, and the reason is recorded in its
   inventory row rather than left as an omission.
5. No file outside `docs/plans/` is modified by this phase.
6. `python3 tools/check-plan-status.py` passes, and
   `pre-commit run --all-files` passes.

## What phase 1 inherits

* D3's band form, which is what phase 1's summary must compute:
  `p90(committed vCPU) / ledger` per job, plus a refusal count.
* The confirmation that `/admin/resources` already publishes every
  input phase 1 needs, so phase 1 is a workflow change in
  `shakenfist/actions` and needs no server-side work.
* D7, which makes "compare the live derivation against
  `cluster_capacity`" an explicit thing phase 1's series must make
  possible -- the probe should record both, not just the live figure.

## Back brief

Before executing any step of this plan, back brief the operator on
the understanding of it, and in particular on the wording intended
for step 0b before any comment is posted to a public issue.
