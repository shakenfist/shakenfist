# Right-size the CI test clouds

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

This plan additionally spans the `shakenfist/actions`
repository, where the CI topologies and the reusable
`smoke-cluster` workflow live, and reads two external data
sources: GitHub Actions job and step timings (via `gh api` or
the `ci-status` helper), and the private-ci conductor's
per-instance resource samples. Both are described under
*Reproducing the measurements* below. Do not restate a number
from this document without checking it still holds -- the whole
point of the plan is that these numbers were never being
watched.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/scheduler.py` (the admission filters whose
arithmetic this plan is about), `shakenfist/mariadb.py`
(`_derive_cpu_memory_limits`, the capacity counters and the
demand guard), `shakenfist/config.py` (`CPU_OVERCOMMIT_RATIO`,
`NODE_CPU_RESERVATION_THREADS`), `examples/_shared/site.yml`
(where the per-host reservation defaults are computed at deploy
time) and `shakenfist/deploy/shakenfist_ci/` (the functional
suites the clouds exist to run).

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

The nested test clouds that merge-queue CI builds are sized by a
decision nobody recorded. Five hypervisors plus a primary, each
node 4 vCPU and 12 GB, is what `ci-topology-slim-primary.yml` has
always said. Until now there was no measurement either way, and no
mechanism that would notice if the sizing stopped being right.

There is now. The numbers below were gathered on 2026-08-27 from
98 `merge_group` runs of `functional-tests.yml` spanning
2026-08-12 to 2026-08-27, and from the private-ci conductor's
per-instance samples over the same period.

### What actually binds is the admission ledger

The scheduler admits a create only if
`max(measured, committed) + requested <= cpu_schedulable x CPU_OVERCOMMIT_RATIO`
(`Scheduler._has_sufficient_cpu`, `shakenfist/scheduler.py`), and
the resources daemon publishes
`cpu_schedulable = max(1, threads - reservation)`
(`shakenfist/daemons/resources/main.py`). `examples/_shared/site.yml`
defaults that reservation to **2 threads, or 4 on a network or
database node**.

On a 4 vCPU CI node that is 2 schedulable threads, so a ledger of
**6 admitted vCPU**; on a 4 vCPU *network* node it floors at 1
thread, so **3**. The reservation is a fixed per-node tax, which
makes small nodes disproportionately expensive: a 4 vCPU node
gives away half its threads, a 6 vCPU node a third.

| Topology | VMs | Under-cloud cost | Inner ledger |
|----------|-----|------------------|--------------|
| `slim-primary` | 6 (1 database + 5 hypervisors) | 24 vCPU / 64 GB | **27 vCPU** (1x3 + 4x6) |
| `slim-tier` | 3 (all hypervisors) | 12 vCPU / 36 GB | **~12 vCPU** (3+3+6) |

Both ledgers were confirmed against real scheduler audit events
captured in CI job logs, not derived on paper: `slim-primary`
publishes one node with `cpu_schedulable: 1` and four with
`cpu_schedulable: 2`; `slim-tier` publishes two with `1` and one
with `2`. Issue #3907 quotes the `cluster_capacity` singleton
reporting the tier's total as `limit 10`, so the reconciled figure
may sit a little below the live derivation -- worth pinning down
in phase 2 rather than assuming.

### The three-node topology is demonstrably too small

Excluding cancellations, over those 98 merge runs:

| Job | Topology | Passed |
|-----|----------|--------|
| Node lifecycle | `slim-primary` | 81 / 88 (92%) |
| Ubuntu 24.04 cluster | `slim-primary` | 75 / 89 (84%) |
| Debian 12 cluster | `slim-primary` | 72 / 89 (81%) |
| Guests | `slim-primary` | 71 / 90 (79%) |
| **Debian 12 tier** | **`slim-tier`** | **17 / 89 (19%)** |

The tier's failures are not spread across causes: 69 of them died
in the `Run functional tests` step, and the sampled logs show
`507 No nodes remaining at scheduling stage sufficient_idle_cpu`
with `current_cpus: 3` against `limit_cpus: 3` -- nodes genuinely
at their ledger. The sampled `slim-primary` failures contain no
such refusal at all. This is the same failure family as the #3772
umbrella and #3907.

Two things this is *not*:

- It is not slowness. Median wall clock is 43.7 minutes for the
  tier against 46.1 for the Debian 12 cluster, and the median
  functional-test step is 31.0 minutes against 30.4. The
  `test_timeout_minutes: 70` and the comment in
  `functional-tests.yml` claiming the tier "runs slower" are not
  borne out by the data.
- It is not the #3813 demand guard, which was fixed on
  2026-08-22. Splitting the window at that fix, the tier went from
  17% to 23% -- unchanged within noise. The refusals now come from
  the allocation dimension, which means the cluster really is
  full.

### Allocation is roughly double actual usage

From the conductor's per-instance samples (`workflow_cost_samples`,
filtered to `is_runner = 0`, n ~ 550 VMs per job):

- **CPU**: a 4 vCPU cluster VM averages **0.71 cores** across a
  `slim-primary` run (p90 1.03, max 1.46) -- about 18% of its
  allocation. The same VM in the tier averages 1.22 cores.
- **Memory**: peak memory in use on a 12 GB node is
  **4.9-7.6 GB** on `slim-primary` and **7.5-10.2 GB** on the
  tier, which is what carrying the same work on three nodes
  rather than five looks like. Swap-out is **zero on every node
  of every job**, so nothing is under memory pressure anywhere.
- The 4 GB primary in `slim-primary` is the tightest node in the
  fleet at 3.9 GB in use. It never swaps, but it has no slack.

Read together with the ledger arithmetic, the finding is that the
clouds are sized on the wrong axis. Real CPU is idle, real memory
is half used, and the thing that runs out is a derived allocation
limit that a wider node would relieve for free.

### The under-cloud budget this spends

`sfcbr` publishes a 234 vCPU admission ledger across six
hypervisors and has 376 GB of physical RAM. One merge run builds
**six** nested clouds (`Debian 12 cluster`, `Ubuntu 24.04
cluster`, `Guests`, `Ansible modules` and `Node lifecycle` on
`slim-primary`; `Debian 12 tier` on `slim-tier`), so it allocates
about 132 vCPU and 356 GB of guest RAM, plus a 1 vCPU / 2 GB
runner apiece.

That is ~95% of the under-cloud's physical memory for a single
merge run, which is why the queue is throttled to two parallel
builds and why contention shows up as unrelated-looking flakes.
**RAM, not vCPU, is the scarce under-cloud resource**, and the CI
clouds are the largest consumer of it.

### Reproducing the measurements

Nothing here came from a tool we own, which is the deeper problem
this plan exists to fix. For now:

- Job and step timings: `gh api
  "repos/shakenfist/shakenfist/actions/workflows/functional-tests.yml/runs?event=merge_group"`
  then `.../runs/<id>/jobs`, whose `steps[]` carry `started_at`
  and `completed_at`. The `ci-status` helper summarises jobs but
  strips step timestamps.
- Ledger and refusal evidence: `ci-status shakenfist/shakenfist
  logs <job id>`, then grep for `cpu_schedulable`, `limit_cpus`
  and `No nodes remaining at scheduling stage`. The scheduler's
  per-candidate audit payload only reaches the log when a test
  fails and attaches it, so a green run tells you nothing.
- Per-instance utilisation: the conductor database, copied as
  described in the private-ci access notes; join
  `workflow_cost_samples` to `workflow_costs` on `namespace` and
  filter `is_runner = 0`. The conductor's own published *sizing
  recommendations* remain untrustworthy (they aggregate a whole
  nested cloud into the runner's namespace and read a guest's RSS
  as its working set); the raw per-instance samples used here are
  not affected by that, because they are not aggregated.
- Live under-cloud capacity: `GET /admin/resources` on `sfcbr`,
  which is also the endpoint phase 1 makes CI sample.

## Mission and problem statement

Make the size of a CI test cloud a measured, continuously-checked
property rather than a historical accident, and re-shape the
topologies so that every job has enough admission ledger for its
assertions to mean what they say -- at lower under-cloud memory
cost than today.

The problem has three parts, and they must be solved in this
order:

1. **We cannot see headroom.** No CI job records how full its
   cloud got. A topology can drift into being too small, or stay
   twice as large as it needs to be, and the only symptom is a
   flake attributed to whichever test drew the short straw. That
   ambiguity is what turned one capacity shortage into six
   per-test issues before #3772 unified them.
2. **We would lose coverage by growing.** A cloud that is too
   small exercises the system's behaviour under exhaustion for
   free. Some of what it catches are real defects -- #3772's own
   verdict is that "nothing in the system, server or client,
   treats 'no capacity right now' as the transient condition it
   is". Growing the clouds would silence that without fixing it.
3. **The shape is wrong before the count is.** Fewer, wider nodes
   buy more ledger for the same spend, and 12 GB per node buys
   nothing at all on a five-hypervisor topology.

### Yes, the tier should get bigger -- with a condition

`slim-tier` exists to give multi-instance `sf-database` coverage:
two `database_node` members, and `test_database_tier` asserting
both see a share of inbound gRPC. Being the smallest cloud in the
fleet is incidental to that purpose, and at a 19% pass rate it is
not delivering the coverage it was built for -- a job that fails
four runs in five teaches the reader to ignore it, and every
merge it ejects costs a full rebuild of six clouds.

Spending more per run to lower the failure rate is the right call
here, and it is cheap: three 6 vCPU nodes double the tier's ledger
to 24 for **+6 vCPU and no extra RAM**. But it is only defensible
with a condition attached, so this plan makes that condition
structural rather than a promise:

> **Phase 3 must land before phase 4.** Every failure signature
> the small clouds currently produce is either (a) reproduced by
> an explicit test that fills a cluster deliberately and asserts
> the documented behaviour, or (b) written down as a known defect
> with an open issue, before any topology grows.

Concretely, the coverage at risk is: the `507
sufficient_idle_cpu` path itself (#3772); the claim-admission
refusal path (#3907, now tolerated by a retry wrapper); and
`test_affinity`, which is the most frequent `slim-primary` failure
and whose signature -- "instances that should share a node do
not" -- is exactly what a full cluster produces when affinity
loses to load ordering (#3565). Growing the cloud will make that
test greener without anyone having decided whether the scheduler
was right. That is the lemon squeezed too far, and phase 3 is
where we stop squeezing and start asserting.

### Candidate shapes

Illustrative only -- phase 2 supplies the peak-demand figure that
turns these into a decision.

| Topology | Ledger | vCPU | RAM GB |
|----------|--------|------|--------|
| `slim-primary` today | 27 | 24 | 64 |
| `slim-tier` today | ~12 | 12 | 36 |
| primary as 4 x 5 vCPU plus a database node | 30 | 24 | 54 |
| primary as 3 x 6 vCPU plus a database node | 30 | 22 | 42 |
| tier as 3 x 6 vCPU | 24 | 18 | 36 |
| tier as 3 x 8 vCPU | 42 | 24 | 36 |

A plausible landing point -- `slim-primary` at four 5 vCPU
hypervisors with the database node bumped to 6 GB, `slim-tier` at
three 6 vCPU nodes -- costs a merge run **+6 vCPU and -50 GB of
RAM**, raises every cloud's ledger, and spends the saving on the
resource the under-cloud actually runs out of.

## Open questions

These are the phase 0 agenda. None should be answered from this
document alone.

1. **Widen the nodes, or lower the reservation?**
   `node_cpu_reservation_threads` is overridable per host from
   inventory, so CI could set it to 1 and gain 50% more ledger for
   nothing. The cost is that CI would stop exercising the
   production reservation arithmetic -- which is precisely where
   #3813 lived. The recommendation is to widen and keep the
   default, but it is a real trade and should be decided
   explicitly.
2. **What is `slim-tier` for?** If it is `sf-database` tier
   coverage, it should be sized for parity and stop being the
   scarcity topology. If it is deliberately the small one, that
   should be written down and its suite trimmed to what a small
   cloud can run.
3. **What is the right headroom band?** Proposed starting point:
   peak committed vCPU at or below 70% of ledger at p90 across a
   run, and zero `sufficient_idle_cpu` refusals in a green run.
   Phase 2 supplies the distribution that makes those numbers
   honest, or replaces them.
4. **Does anything still need five hypervisors?**
   `nodelifecycletests.sh` needs a script host, a network node and
   two distinct victims, and `functional-tests.yml` hardcodes
   `10.0.0.20`-`10.0.0.24` when picking a random upload target.
   That job may keep its current shape while the others shrink.
5. **Is memory a second binding constraint?** One post-#3813 run
   refused with `sufficient_idle_memory`, which `_has_sufficient_ram`
   measures against live free memory rather than against a ledger.
   Whether page cache inflates that denominator needs checking
   before RAM per node is reduced.
6. **How much of the phantom stays?** `slim-primary` deliberately
   lists an unreachable `sf-absent` hypervisor as the regression
   guard for the 2026-07-20 absent-node deploy failure. Any
   reshaping keeps it; the phase plans must say so explicitly so
   that nobody "tidies" it away.

## Execution

!!! note "In this project"

    A status cell holds exactly one term from the vocabulary in
    `PLAN-TEMPLATE.md` and nothing else. The same term is written
    twice: once in the phase table below, and once in the row this
    plan carries in `docs/plans/index.md`. Keep them in step --
    the index row is the whole-plan status, so it only reaches
    `Complete` once every phase has been completed, abandoned or
    superseded.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions: what each topology is for, widen-versus-reservation, and an inventory of what scarcity currently catches | PLAN-ci-cloud-sizing-phase-00-decisions.md | Not started |
| 1. Headroom instrumentation: sample `/admin/resources` through every cluster job and publish the series | PLAN-ci-cloud-sizing-phase-01-headroom-probe.md | Not started |
| 2. Baseline measurement window: the peak-demand distribution that has never existed | PLAN-ci-cloud-sizing-phase-02-baseline.md | Not started |
| 3. Explicit saturation coverage, so that growing a cloud cannot silence a defect | PLAN-ci-cloud-sizing-phase-03-saturation-coverage.md | Not started |
| 4. Re-shape the topologies against the phase 2 data | PLAN-ci-cloud-sizing-phase-04-topologies.md | Not started |
| 5. Guardrails: the headroom band, and a structural-minimum assertion that names the ledger | PLAN-ci-cloud-sizing-phase-05-guardrails.md | Not started |
| 6. Documentation and downstream propagation | PLAN-ci-cloud-sizing-phase-06-docs.md | Not started |

### Phase 0 -- Decisions and scarcity inventory

Settle the open questions above, and produce the inventory that
phase 3 is built from: every distinct failure signature the
current clouds produce because they are small, each classified as
*defect we must fix*, *behaviour we must assert*, or *test bug*.
The #3772 umbrella, #3907, #3565 and the closed #3813 are the
starting corpus; the merge-CI triage history is the source.

Deliverable: the decisions written into this plan, and a table of
signatures with a disposition each.

### Phase 1 -- Headroom instrumentation

Sample `GET /admin/resources` (admin-only, already implemented by
`AdminResourcesEndpoint` -> `Scheduler.summarize_resources()`)
throughout the functional-test step, and emit a per-run summary:
peak and p90 committed vCPU cluster-wide and per node, both as
absolute numbers and as a fraction of ledger; the same for memory;
and a count of `sufficient_idle_cpu` / `sufficient_idle_memory`
refusals. Upload the raw series as a job artifact and print the
summary in the log.

No gating, no topology change. The endpoint already publishes both
inputs to the admission decision (`cpu_measured` and
`cpu_committed`, plus `cpu_hard_max`) precisely so "this node
measures idle but is refusing work" is answerable, which is what
makes it the right probe.

Lives in `shakenfist/actions` (the reusable `smoke-cluster`
workflow and `build-smoke-cluster`), so it needs an operator push
and a real CI run to prove.

### Phase 2 -- Baseline measurement window

Leave phase 1 running for an agreed number of merge runs, then
publish the distribution: what peak utilisation actually is per
job, on both topologies, and how it correlates with the failures.
This is the number that has never existed, and it is what turns
the candidate shapes above into a decision.

Expect it to also answer open question 5 (whether memory refusals
are real) and to confirm or correct the tier's reconciled ledger
against the live derivation.

### Phase 3 -- Explicit saturation coverage

**Gate on this phase before phase 4.** Convert the scarcity
coverage we get by accident into coverage we get on purpose: a
functional test that deliberately fills a cluster to its ledger
and asserts the documented behaviour at the boundary, and issues
filed for every signature phase 0 classified as a defect. Where
the documented behaviour is itself wrong -- #3772 argues a bare
507 is the wrong answer to a transient condition -- the test
asserts what the system does today and the issue records what it
should do, so growing the clouds cannot quietly close it.

### Phase 4 -- Re-shape the topologies

Apply the phase 2 decision to `ci-topology-slim-primary.yml` and
`ci-topology-slim-tier.yml` (and the `-released` variants), keeping
the `sf-absent` phantom, honouring the structural minimums
(`test_network_lifecycle` needs two hypervisors that are not the
network node; `test_affinity` needs three nodes;
`test_database_tier` needs two database nodes), and updating any
hardcoded node lists in `functional-tests.yml`. Land one topology
at a time so a regression is attributable.

### Phase 5 -- Guardrails

Turn phase 1's summary into a check: warn outside the agreed
headroom band, and add a cluster-CI assertion on the deployed
topology's hypervisor count and total ledger so that a future
topology edit which halves capacity fails as itself rather than as
a flake in an unrelated test. Follow the warn-window-then-gate
pattern the API-validation plan used.

### Phase 6 -- Documentation and downstream propagation

Document the sizing model in `docs/developer_guide/ci.md` --
the ledger arithmetic, the band, and how to re-measure -- and
propagate the reshaped topologies to the downstream repositories
that consume the reusable workflow, per the copy-paste-drift
finding in `project-sf-ecosystem-ci`.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

!!! note "In this project"

    Phases 1, 4 and 6 touch `shakenfist/actions`, which only the
    operator pushes, and which cannot be proven green except by a
    real CI run. Those phases follow the pattern
    `PLAN-remove-primary-phase-06-step5b-ci-design.md`
    established: design and review the change in full here, hand
    the operator a reviewed diff, then verify against a live run
    rather than treating a merged commit as done.

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases 0, 2, 3 and 5 are high effort: they turn on judgment
    about what CI is for and what a failure means. Phases 1, 4 and
    6 are medium -- the arithmetic is settled by then and the
    edits are mechanical, but they are cross-repository, so the
    brief must name the files in `shakenfist/actions` explicitly.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    A worked brief for this plan: instead of "sample cluster
    capacity during the test run", write "add a step to
    `.github/workflows/smoke-cluster.yml` in `shakenfist/actions`
    which, before the `Run functional tests` step, starts a
    background poller on the primary that curls
    `http://localhost:13000/admin/resources` with a system-namespace
    bearer token every 15 seconds into
    `/srv/ci/headroom.jsonl`; the endpoint is
    `AdminResourcesEndpoint` in
    `shakenfist/external_api/admin.py` and its shape is whatever
    `Scheduler.summarize_resources()`
    (`shakenfist/scheduler.py`) returns, so read that for the
    field names rather than guessing. Summarise the series in a
    step after the tests and upload it with the other artifacts."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy).
    - [ ] If proto files changed, stubs were regenerated with
          `tox -e genprotos` and committed.
    - [ ] Any claim about a pass rate, a duration or a ledger was
          recomputed, not copied from this document. Every number
          here has a date on it for that reason.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Every merge-queue cluster job publishes, as a job artifact and
  a log summary, how full its cloud got -- peak and p90 committed
  vCPU and memory against ledger, and a count of capacity
  refusals.
* A topology change that materially reduces a cloud's ledger
  fails a check that names the ledger, rather than surfacing as a
  flake in an unrelated test.
* The `Debian 12 tier` job's pass rate is comparable to the other
  cluster jobs, and its remaining failures are not
  `sufficient_idle_cpu`.
* Every capacity-related failure signature that the pre-change
  clouds produced is either covered by an explicit test or has an
  open issue naming it. No issue was closed merely because a
  bigger cloud stopped reproducing it.
* A merge run's under-cloud memory footprint is lower than the
  356 GB it was on 2026-08-27, and the sizing of each topology is
  justified in `docs/developer_guide/ci.md` by a measurement
  rather than by history.
* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, and mypy type checking).
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* Documentation in `docs/` has been updated. `ARCHITECTURE.md`
  and `AGENTS.md` are updated only if a convention or the shape
  of the system changed -- a CI sizing model is a `docs/` matter.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` (one row, in the
*Master plans* table) and in `docs/plans/order.yml`. Phase files
are linked from the Execution table above and appear in neither,
which is what `tools/check-plan-status.py` enforces.

### Future work

- **Sample the under-cloud too.** This plan measures the *inner*
  cloud's headroom. The outer one -- `sfcbr` -- is the resource
  actually being competed for, and the same probe pointed at it
  would say whether a merge run is contending with a deploy or
  with another merge run. That is the mechanism behind the
  redeploy-races-merge-CI family, so it is worth doing once the
  inner probe exists.
- **Per-suite concurrency rather than a fleet-wide 5.**
  `test_concurrency` is 5 for every cluster job regardless of
  topology. Demand and ledger could be reconciled from the other
  end: derive the worker count from the deployed ledger instead of
  hardcoding it. Deferred because it makes runs less comparable
  across topologies, which phase 2 needs.
- **`test_coalescing` is the peak.** It bursts `BURST = 6`
  instances at once, which is a whole node's ledger on the current
  shape, while four other workers are creating. If phase 2 shows
  it dominates the peak, isolating it (or lowering `BURST` and
  raising it again once the topology grows) is a cheap lever.
- **Retire the `test_timeout_minutes: 70` special case.** The
  tier's extra ten minutes was granted for slowness the data does
  not show. It is harmless, but it encodes a belief that is
  false, and beliefs like that are why the sizing went unexamined.
- **Generalise to the other repositories' clouds.** The
  downstream repositories fork these topologies. Phase 6
  propagates the shapes; making the headroom probe part of the
  reusable workflow means they inherit the measurement too.

### Bugs fixed during this work

Related issues to resolve or be aware of while planning. None of
these is closed by growing a cloud, and phase 3 exists to make
sure none of them is closed by accident:

- **#3772** (open, umbrella) -- instance creates refused with
  `507 sufficient_idle_cpu` under suite concurrency. Its own
  verdict is that a bare 507 is the wrong answer to a transient
  condition. Growing the clouds reduces how often it fires without
  addressing that; the issue must stay open and gain an explicit
  test.
- **#3907** (closed 2026-08-26) -- the claims suite asserted
  cluster headroom it never reserved. Fixed by tolerating the
  transient refusal, which is the right fix for the test and is
  also evidence that `slim-tier` is at its ledger routinely.
- **#3813** (closed 2026-08-22) -- the demand guard was
  arithmetically unsatisfiable below four schedulable threads.
  Directly caused by the CI node shape this plan is changing, and
  the reason open question 1 leans towards widening nodes rather
  than lowering the reservation.
- **#3565** (open) -- `test_affinity`. Its title already says
  "soft affinity loses to resource filters under suite
  concurrency". The most frequent `slim-primary` failure in the
  sampled window, always the same signature: instances that
  should share a node do not. A bigger cloud makes
  it greener without anyone deciding whether the scheduler was
  right, so it needs a disposition in phase 0 before phase 4.
- **#3882** (open) -- reconciler drift is not provable from logs.
  Phase 2 wants to compare the live ledger derivation against the
  reconciled `cluster_capacity` figure; if they disagree, this is
  why it is hard to tell which is wrong.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
