# Phase 6 -- Documentation, and the instrument seam the propagation half turned out to be

Planning effort: **medium**, per the master plan's *Planning effort*
note. The arithmetic is settled by now and the edits are mostly
documentation, but the phase is cross-repository, so 6d -- the one step
that reaches `shakenfist/actions` -- names its files there explicitly and
prepares its diff rather than applying it, because only the operator can
push that repository (phase 4's F7).

Review effort: medium. One step (6b) is a large move whose shape should
be agreed before editing starts; the *Back brief* gates it.

## Scope

**In scope:**

* Documenting the **sizing model**: the admission-ledger arithmetic, the
  two cluster topologies' actual shapes and the ledgers they yield, why
  those shapes were chosen, and how to re-measure the whole thing from
  scratch.
* **Splitting the sizing and headroom material out of
  `docs/developer_guide/ci.md`** into its own page, leaving a summary and
  a link. Gated -- see D2 and the *Back brief*.
* **Closing half the instrument seam**: giving the `Ansible modules` job
  the headroom probe, by widening one `if:` condition in
  `shakenfist/actions`'s `smoke-cluster.yml`, together with the matching
  `tools/ci_headroom_harvest.py` bundle-table entry that change *requires*
  (D3).
* **Correcting the stale claims the survey found at their source**,
  including two Future work entries in the master plan which assert a
  state of the world that no longer exists (F1), and the master plan's
  Situation table, which still records `slim-tier`'s ledger as 12 (F2).
* Filing the issue for the half of the seam this phase does not close,
  and recording it in Future work (D4).

**Out of scope, explicitly:**

* **Propagating topology shapes to downstream repositories.** This was
  half of what the master plan assigned to this phase and there is
  nothing left to do: F1 found exactly one copy of the topologies, in
  `shakenfist/actions/ansible/`, and every cluster-deploying call site in
  the fleet reads it from there. D1 replaces the task rather than
  deleting it.
* **Arming the gate on any new job shape.** No window exists for one, and
  three separate things would have to change before a window could even
  be harvested (F5). Recorded as Future work.
* **Instrumenting `Node lifecycle` or kerbside's deploy.** Both reach the
  cluster through the `build-smoke-cluster` composite action rather than
  the reusable workflow, so moving the probe steps to reach them changes
  how *every* caller deploys, at `@main`, unpinned. D4 files an issue
  instead.
* **Reshaping `slim-primary` because it reads OVERSIZED.** Phase 5 handed
  this over as a question (D6). Answering it is a topology change with a
  prediction attached, which is phase 4's kind of work and needs its own
  decision.
* **Teaching `tools/ci_headroom_harvest.py` to read `pull_request` runs**,
  or another repository's bundle names.
* **[#4320](https://github.com/shakenfist/shakenfist/issues/4320)**, the
  hardcoded `10.0.0.20`-`.24` upload-target list. Phase 5's D9 gave it an
  issue and this phase does not reopen that.
* **Publishing `ci.md` (or its successor) to the documentation site.** F6
  found it is not in the `mkdocs.yml` nav, along with five other
  `developer_guide` pages. Whether CI internals belong on a public site is
  a call for the operator, not a side effect of a documentation phase.

## What the survey found

The master plan's phase 6 section was written before phases 1-5 ran, and
one of its two halves has been overtaken by work in another plan. Every
claim below was checked against the tree rather than read.

**Two of these corrections are already made.** The planning commit
rewrote the master plan's *Phase 6* section and the `docs/plans/index.md`
row against F1 and F2, because a reader reaching either of those before
this phase implements anything would otherwise be told to propagate
topologies that nobody forks. Step 6a does **not** redo them; what is
left for it is the Situation-table note, the two Future work entries, the
seams-test docstring and the drifted line references.

### F1 -- Nothing forks the topologies any more, so there is nothing to propagate

The master plan's phase 6 section says to "propagate the reshaped
topologies to the downstream repositories that consume the reusable
workflow, per the copy-paste-drift finding in `project-sf-ecosystem-ci`".
That drift is gone. Enumerating every workflow in every Shaken Fist
ecosystem checkout for a `uses:` of either `smoke-cluster.yml` or the
`build-smoke-cluster` composite action gives the complete inventory of
places a Shaken Fist cloud is built in CI:

| Repository | Call site | Route | Topology | `headroom_gate` |
|---|---|---|---|---|
| shakenfist | `functional-tests.yml:376` | reusable workflow | smoke, single node | `false` (`:388`) |
| shakenfist | `functional-tests.yml:495` | reusable workflow | `slim-primary` x3, `slim-tier` x1 (merge matrix) | off switch (`:505`) -- **the only armed site** |
| shakenfist | `functional-tests.yml:528` | reusable workflow | `slim-primary`, `test_kind: ansible-modules` | `false` (`:535`) |
| shakenfist | `functional-tests.yml:581` | **composite action** | `slim-primary` (`Node lifecycle`) | n/a -- no probe |
| shakenfist | `scheduled-tests.yml:70` | reusable workflow | dispatch matrix | `false` (`:81`) |
| client-python | `functional-tests.yml:80` | reusable workflow | smoke, single node | not passed -- takes the default |
| kerbside | `sf-e2e-functional.yml:104` | **composite action** | `localhost` | n/a -- no probe |
| actions | `canary.yml:82` | reusable workflow (relative) | smoke, single node | `false` (`:92`) |

There are exactly six topology files, all in
`shakenfist/actions/ansible/ci-topology-*.yml`, and no other repository
has one. The reusable-workflow migration -- `remove-primary` phase 8, per
the comment at `client-python/.github/workflows/functional-tests.yml:62-68`
-- already fixed the drift this phase was going to fix by hand.

What the table does show is a different gap, and it is the same concern
in its structural form: **three of the eight call sites build a cloud the
probe never sees.** Two use the composite action directly, and the third
(`Ansible modules`) reaches the workflow but with `test_kind:
ansible-modules`, which every probe step is gated against. D1 makes that
gap this phase's second half.

### F2 -- The master plan records `slim-tier`'s ledger as 12, and nowhere records that it is now 24

`PLAN-ci-cloud-sizing.md:128` still carries the baseline table:

| Topology | ... | Inner ledger | Measured over |
|---|---|---|---|
| `slim-primary` | ... | **27 vCPU** (1x3 + 4x6) | 154 job-runs, every one |
| `slim-tier` | ... | **12 vCPU** (3+3+6) | 50 job-runs, every one |

Phase 4 reshaped `slim-tier` from three 4 vCPU nodes to three 6 vCPU
nodes and doubled that ledger to 24. `grep -n 'ledger of 24\|ledger to 24\|12 to 24'`
over the master plan returns nothing: the figure appears nowhere in it.
The only two places in the tree that state it are a comment on
`MINIMUM_HYPERVISOR_LEDGER` in
`shakenfist/deploy/shakenfist_ci/sizing.py`, and phase 4's own Outcome
section.

So the single headline number of the whole plan -- what the CI clouds'
ledgers actually are -- is correct in one Python comment and one phase
plan's appendix, and wrong in the document a reader opens first. This is
the strongest single argument for the phase, and D7 decides how to fix it
without destroying a measurement record.

### F3 -- The ledger arithmetic is nowhere in `ci.md`, and is reproducible from source

`docs/developer_guide/ci.md` uses the ledger as the band's denominator
twenty times (`:279`, `:422`, `:448`, `:463`, `:576` and so on) and states
the structural floor of 24 at `:576`, but never says what the ledger *is*
or how a topology's shape produces one. The arithmetic lives in three
places, none of them a document about CI:

* `Scheduler._has_sufficient_cpu` admits while
  `max(measured, committed) + requested <= cpu_schedulable x CPU_OVERCOMMIT_RATIO`.
* `shakenfist/daemons/resources/main.py:136` publishes
  `cpu_schedulable = max(1, cpu_threads - cpu_reservation_threads)`.
* `examples/_shared/site.yml:360-363` defaults that reservation to
  `(1 + (network_or_database ? 1 : 0)) * 2` -- **2 threads, or 4 on a
  network or database node**.

`CPU_OVERCOMMIT_RATIO` defaults to 3.0
(`docs/operator_guide/scheduler.md:656`). Those three rules reproduce both
ledgers exactly from the topology files, which is what makes this
documentable rather than merely assertable -- see *The arithmetic, checked*
below.

### F4 -- The seams test's docstring says the reusable workflow defaults to gating; it does not

`shakenfist/tests/test_headroom_gate_workflow_seams.py:13-16` reads:

> The reusable workflow defaults to gating, so a call site which passes
> nothing is gated with no way to switch it off, and nothing about the job
> would say so until the day the switch was needed.

shakenfist/actions#102 (`e2243a554`) set `headroom_gate`'s default to
`false` (`smoke-cluster.yml:73`). The rule the test enforces is unchanged
and still right -- a call site should state its policy because the default
lives in another repository at `@main` -- but the sentence justifying it is
now false. The same round corrected the assertion messages, `ci.md:499-502`
and the phase 5 plan; this docstring paragraph is what it missed.

### F5 -- Three things block harvesting a downstream window, before any question of a gate

Phase 5 handed over that arming the smoke tier "needs a window of pull
request runs, and `tools/ci_headroom_harvest.py` reads only `merge_group`
runs today". It is worse than one blocker:

1. `list_runs()` hardcodes `event=merge_group` in the API path
   (`tools/ci_headroom_harvest.py:421`).
2. `client-python/.github/workflows/functional-tests.yml:11-12` triggers
   on `pull_request:` only -- that repository has no merge queue, so
   there are no `merge_group` runs to read even if the filter moved.
3. `BUNDLE_TOPOLOGIES` (`:133-143`) is keyed on shakenfist's own bundle
   artifact names, and an unrecognised name raises `UnknownBundleError`
   by design. `--repo` exists, but pointing it at another repository
   stops the harvest on the first bundle.

This is why D5 keeps every downstream gate out of scope. It is three
changes and a window, not a decision.

### F6 -- `ci.md` is not in the `mkdocs.yml` nav

Neither are `coding_rules.md`, `database_internals.md`,
`security_model.md`, `subsystem_internals.md`, `io_performance_tuning.md`
or `writing_an_endpoint.md`. So a new page under `developer_guide/` is
equally unpublished, which removes site navigation as an argument either
way in D2, and means D2 is purely about how the repository's files read.
Recorded rather than fixed: publishing CI internals is not this phase's
call to make.

### F7 -- Three line references in the harvest have drifted

`tools/ci_headroom_harvest.py`'s comments cite
`functional-tests.yml:436-480` for the merge matrix (`:119`),
`functional-tests.yml:514` for the Ansible modules job (`:151`) and
`functional-tests.yml:554-557` for the `Node lifecycle` composite-action
call (`:156`). The real lines are roughly `440-495`, `528` and `581`;
phase 5's edits to that workflow moved them. Each comment's *claim* is
still true, which is why this is a small finding rather than an F2.

### What the survey did not find

`python3 tools/check-plan-status.py` reports agreement, so phase 5's
status is consistent in both places and no half-finished closeout is
hiding. `docs/developer_guide/ci.md`'s account of the gate is accurate,
including the actions#102 default change at `:499-502` -- phase 5's review
rounds kept it current, and this phase does not have to repair it.

## The arithmetic, checked

Run against `shakenfist/actions` at `main`, the three rules in F3
reproduce both ledgers with no fitting:

```
ci-topology-slim-tier.yml: ledger 24
    cpu 6  allsf,database_node,primary_node,network_node,hypervisors  reserve 4 -> schedulable 2   6
    cpu 6  hypervisors, database_node, allsf                          reserve 4 -> schedulable 2   6
    cpu 6  hypervisors, allsf                                         reserve 2 -> schedulable 4  12
ci-topology-slim-primary.yml: ledger 27
    cpu 4  allsf,database_node,primary_node                           not a hypervisor             0
    cpu 4  hypervisors, network_node, allsf                           reserve 4 -> schedulable 1   3
    cpu 4  hypervisors, allsf                          (x4)           reserve 2 -> schedulable 2   6
```

24 is exactly `MINIMUM_HYPERVISOR_LEDGER`, and 27 is exactly what phase
2 measured in all 154 `slim-primary` job-runs. Two things in that table
are worth saying out loud in the documentation because they are not
obvious from a topology file:

* **`slim-primary`'s primary contributes nothing.** It carries
  `database_node,primary_node` and not `hypervisors`, so it is absent
  from `/admin/resources`'s `per_node` mapping entirely. Six VMs, five
  of them in the ledger.
* **The reservation is a fixed per-node tax, so small nodes are
  disproportionately expensive.** A 4 vCPU node gives away half its
  threads; a 6 vCPU node a third. On a 4 vCPU *network or database* node
  the reservation floors `cpu_schedulable` at 1, which is why
  `slim-primary`'s `sf1` yields 3 where its siblings yield 6. This is the
  whole reason phase 4 widened nodes rather than adding them.

The script that produced it lives here rather than in `tools/`: it parses
a topology file in another repository by regex, which is fine for a check
run twice and not something to maintain. Run it from a
`shakenfist/actions` checkout at `main`.

```python
import re
import sys

RATIO = 3.0  # CPU_OVERCOMMIT_RATIO default


def ledger(path):
    text = open(path).read()
    cpus = [int(m) for m in re.findall(r'^\s+cpu:\s*(\d+)\s*$', text, re.M)]
    groups = re.findall(r'^\s+groups:\s*(.+)$', text, re.M)
    if len(cpus) != len(groups):
        sys.exit('%s: %d cpu values but %d groups' % (path, len(cpus), len(groups)))
    total = 0
    for cpu, group in zip(cpus, groups):
        g = [x.strip() for x in group.split(',')]
        if 'hypervisors' not in g:
            continue
        network_or_database = 'network_node' in g or 'database_node' in g
        reservation = 2 * (1 + (1 if network_or_database else 0))
        total += int(max(1, cpu - reservation) * RATIO)
    return total


for path in sys.argv[1:]:
    print('%s: ledger %d' % (path, ledger(path)))
```

The `cpu:`-to-`groups:` pairing works because each topology file creates
an instance and adds it to ansible in the same order, and it exits loudly
rather than mispairing silently if that stops being true. `sf-absent` in
`slim-primary` is not an `sf_instance` at all -- it lives in
`absent_deploy_hypervisors` -- so it correctly never appears.

## Decisions

### D1 -- The propagation half is replaced by the instrument seam, not deleted

The master plan assigned two halves to this phase. F1 voids the second
one: there is nothing to propagate, because the reusable-workflow
migration already removed every fork. Deleting the half and shipping a
documentation-only phase would be the easy reading, and it would lose the
concern the half existed to serve -- that clouds built outside this
repository are not measured.

So the second half becomes: **close the instrument seam where closing it
does not change how every caller deploys.** That is exactly one of the
three uninstrumented call sites (D3), and the other two get an issue
(D4). The master plan's *Phase 6* section was corrected at source by the
planning commit; its two Future work entries which assert downstream
forks are 6a's job. Either way the correction is made, so the next reader
does not re-derive F1.

### D2 -- The sizing and headroom material moves to its own page

`docs/developer_guide/ci.md` is 1326 lines, of which `:229-668` -- 440
lines, a third of the file -- is already the headroom instrument, the
band, the gate, the harvest and the structural assertion. This phase adds
the sizing model to that, which would make it two fifths of a document
whose stated job (per the index in `AGENTS.md:20`) is "how does CI work,
what gates a PR, what bot commands exist".

The material moves to **`docs/developer_guide/ci_cloud_sizing.md`**, with
a short summary and a link left in `ci.md`. Three reasons, in order of
weight:

1. **Someone asking "why is the CI cloud this size?" is not asking about
   bot commands.** The sizing model, the ledger arithmetic and the band
   are one subject with one audience.
2. **Phase 7's audit has to check that the sizing model, the ledger
   arithmetic and the band all say the same thing.** The master plan's
   phase 7 section says so explicitly. That is a different job when the
   three are one page than when they are interleaved with the merge
   queue and the delinter.
3. CLAUDE.md's documentation policy puts a deep dive on one subsystem in
   its own file under `docs/`, with a summary and a link left behind.

**The argument against, which a reviewer may well prefer:** a 441-line
move is a large diff that buries the new prose it is supposed to make
room for, and F6 shows the split buys nothing in the published site
because neither page is in the nav. The mitigation is procedural rather
than rhetorical -- step 6b is a **pure move plus the stub, in its own
commit, with no content changes at all**, so `git diff -M` reads as a
rename and step 6c's new prose is reviewable on its own. If the move is
rejected at the back brief, 6c writes into `ci.md` instead and nothing
else in the plan changes.

### D3 -- `Ansible modules` gets the probe, and the harvest table entry lands with it

Widening `if: inputs.test_kind == 'functional'` on the probe steps in
`shakenfist/actions`'s `smoke-cluster.yml` is a one-condition change that
instruments a real `slim-primary` cluster job. It is safe to arm: that
call site passes `headroom_gate: false` (`functional-tests.yml:535`), the
launch step is `continue-on-error: true`, and the collect step is `if:
always()`, so the worst case is a job that reports a verdict nobody gates
on.

The coupling is the part worth front-loading. `Ansible modules` already
appears in `UNINSTRUMENTED_BUNDLES`
(`tools/ci_headroom_harvest.py:166-169`) as a bundle deliberately skipped
by name. Once the probe runs there, that bundle carries a series and must
move into `BUNDLE_TOPOLOGIES` with a `job_prefix`, or the next harvest
skips real data -- and if the artifact name changes at the same time, the
harvest raises `UnknownBundleError` and stops, which is the designed
behaviour and not a bug.

**Order:** the `actions` change may land first, unlike phase 5's. The
harvest is a hand-run tool rather than CI, so a mismatch breaks the next
harvest rather than every run in flight, and no window is open right now
for it to corrupt. This is stated because phase 5 established the
opposite order for the gate, and the reason it differs is the blast
radius, not the repository.

### D4 -- `Node lifecycle` and kerbside stay uninstrumented, with an issue

Both reach a cluster through `build-smoke-cluster` and never touch the
workflow the probe steps live in. Reaching them means moving or
duplicating those steps into the composite action, which changes what
happens on every deploy for every caller -- shakenfist's five call sites,
client-python's, kerbside's and the canary's -- through an action consumed
at `@main` with no pin. That is a larger irreversible surface than a
documentation phase should take, and it is the same shape of risk phase 5
spent a whole decision managing.

It gets an issue naming both call sites, the composite action as the
seam, and the two Future work entries that already describe it. The cost
of leaving it is recorded and unchanged: `Node lifecycle` is the best
performer in the failure table and the utilisation-versus-failure
correlation cannot speak to it.

### D5 -- No new shape is armed, and no downstream window is opened

F5 makes this arithmetic rather than judgement: arming any downstream or
single-node shape needs a `pull_request` harvest mode, a bundle-name
table that is not shakenfist-specific, and either a merge queue in
client-python or a window of pull request runs. Three changes before a
window, and a window before a decision. Recorded as Future work with F5's
three blockers named, so the next attempt starts from the list rather
than the surprise.

### D6 -- `slim-primary`'s OVERSIZED reading is documented as an open question

Phase 5 measured 25 of 30 job-runs OVERSIZED on `slim-primary` against
`slim-tier`'s 5 of 10, and handed over whether that is worth acting on.
Acting on it means shrinking a topology, which needs a prediction, a
falsification criterion and a window -- phase 4's pattern, not a
documentation phase's. The new page records the reading, names the
harvest command that asks the question over a window, and says what a
shrink would have to establish first. Whether to do it is left as Future
work.

The reason this is not simply deferred silently: the band's lower bound
exists to detect exactly this, it has now detected it, and a plan that
instruments something, gets an answer and then does not write the answer
down has wasted the instrument.

### D7 -- The Situation table is annotated, not corrected

F2's table is a measurement record: its last column says "154 job-runs,
every one" and "50 job-runs, every one", and those readings are what the
window actually measured before phase 4 changed the cloud. Overwriting 12
with 24 would make the row claim a measurement nobody took.

So the row keeps its numbers and gains a note directly beneath saying
`slim-tier`'s ledger is now 24 after phase 4's reshape, citing phase 4's
4d, and pointing at the new page for the current shapes. The reshaped
figures are stated *once*, on the new page, with the topology files and
`MINIMUM_HYPERVISOR_LEDGER` named as the source of truth, so the next
reshape has one place to update rather than five.

**A reviewer may reasonably want the table simply corrected**, on the
grounds that a reader who misses a note is worse off than a reader given
the current number. The counter is that the table is cited by phases 2, 3
and 4 as the baseline they reasoned from, and a baseline that silently
tracks the present is not a baseline. The note is placed above the table's
own provenance paragraph, where the reader is already being told what the
column means.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | **Correct the stale claims F1, F2, F4 and F7 at their source.** The master plan's *Phase 6* section and the `docs/plans/index.md` row were already corrected by the planning commit -- do not touch either. In `docs/plans/PLAN-ci-cloud-sizing.md`: add D7's note beneath the Situation table (the `slim-tier` row is `:129`), above the "Those ledgers are not derived on paper" paragraph, saying `slim-tier`'s ledger is now 24 after phase 4's 4d and pointing at `docs/developer_guide/ci_cloud_sizing.md`; in Future work, correct the **Generalise to the other repositories' clouds** entry (`:1676-1692`) -- "the downstream repositories fork these topologies" is false, replace it with F1's finding that the fork is gone and what remains is the probe seam, keeping the second sentence about the reusable workflow which is still the fix; and correct the **Instrument the two cluster jobs the probe cannot see** entry (`:1694-1710`), whose `functional-tests.yml:514` and `:554-557` references have drifted to `:528` and `:581`, marking the `Ansible modules` half discharged by this phase's 6d if 6d lands. In `shakenfist/tests/test_headroom_gate_workflow_seams.py:13-16`, delete the sentence claiming the reusable workflow defaults to gating and replace it with the true reason the rule exists -- the default is `false` (`smoke-cluster.yml:73`, shakenfist/actions#102) and lives in another repository at `@main`, so a call site states its own policy where a change to it shows up in review. In `docs/plans/PLAN-ci-cloud-sizing-phase-05-guardrails.md:836-843`, correct *What phase 6 inherits*'s claim that "the downstream repositories fork these topologies" the same way -- it is a statement addressed to this phase, so it is in scope despite that plan having merged, and leaving it would have the next reader re-derive F1. In `tools/ci_headroom_harvest.py`, fix the three drifted line references at `:119`, `:151` and `:156`. Do not restate phase 4's or phase 5's reasoning anywhere; link to it. |
| 6b | medium | sonnet | none | **The move, and nothing else (D2). Do not start until the back brief clears it.** Create `docs/developer_guide/ci_cloud_sizing.md` and move `docs/developer_guide/ci.md:229-668` into it verbatim -- the whole `## CI headroom instrumentation` section through to the end of `### The probe's traffic is exempted from the idle-load check`, inclusive, promoting the heading levels by one so the page has a single `#` title. **No wording changes, no reordering, no new content**; the commit must read as a move. In `ci.md`, leave a three-to-four sentence `## CI cloud sizing and headroom` stub in the same position saying what the topic is (the clouds are sized against the scheduler's admission ledger; one bound gates; the probe measures every cluster job) and linking to the new page. Then: add the new page to the `Key docs` list in `AGENTS.md` beside the existing `ci.md` row if and only if a row there already names a comparable page, and check `grep -rn 'ci\.md#' --include=* .` still resolves -- there is exactly one inbound anchor today (`AGENTS.md:143`, `#creating-instances-in-the-functional-suite`) and it is **not** in the moved range, so it must still resolve afterwards. Do **not** add either page to `mkdocs.yml` (F6, and out of scope). Run the `Check documentation links and anchors resolve` pre-commit hook specifically. |
| 6c | medium | opus | none | **Write the sizing model (F2, F3, D6).** New `## How the clouds are sized` material on the page 6b created (or in `ci.md` if the move was rejected), placed *before* the headroom instrument, because the ledger is the band's denominator and currently the band is explained first. Cover, in this order: (1) what admission actually tests -- `max(measured, committed) + requested <= cpu_schedulable x CPU_OVERCOMMIT_RATIO`, from `Scheduler._has_sufficient_cpu`; (2) where each term comes from -- `cpu_schedulable = max(1, cpu_threads - cpu_reservation_threads)` (`shakenfist/daemons/resources/main.py:136`), the reservation defaulting to `(1 + (network_or_database ? 1 : 0)) * 2` (`examples/_shared/site.yml:360-363`), `CPU_OVERCOMMIT_RATIO` 3.0 (`docs/operator_guide/scheduler.md:656`); (3) the two cluster topologies as a table reproducing *The arithmetic, checked* above, naming `shakenfist/actions/ansible/ci-topology-slim-tier.yml` and `-slim-primary.yml` as the source of truth and `MINIMUM_HYPERVISOR_LEDGER` in `shakenfist/deploy/shakenfist_ci/sizing.py` as what enforces the floor; (4) the two non-obvious consequences -- `slim-primary`'s primary is not a hypervisor and contributes nothing, and the reservation is a fixed per-node tax that makes small nodes disproportionately expensive, which is why phase 4 widened nodes instead of adding them; (5) **how to re-measure**, end to end: harvest a window with `tools/ci_headroom_harvest.py` (naming both ends of the window, per the existing warning), read the band verdicts, and recompute a topology's ledger from its file by hand using (2). State the reshaped figures **once** on this page and nowhere else. Then add D6's open question as its own short subsection: `slim-primary` read OVERSIZED in 25 of 30 phase 5 job-runs against `slim-tier`'s 5 of 10, the existing *Asking whether a cloud is oversized* harvest command is how to ask it over a window, and a shrink would need a prediction and a falsification criterion first. Contradict nothing in `docs/operator_guide/scheduler.md`; where it already explains a term, link rather than restate. |
| 6d | medium | sonnet | none | **Instrument `Ansible modules` (D3).** Two halves, in two repositories. In `shakenfist/actions` -- which only the operator can push, so prepare this as a *Prepared changes* section in this plan with the exact diff, not as an applied edit -- widen the probe steps' condition in `.github/workflows/smoke-cluster.yml` from `if: inputs.test_kind == 'functional'` to also admit `ansible-modules`. **There are seven such gates, not two, and picking the right subset is the whole of this step's judgement.** They are: `:222` *Make the traces directory*, `:235` *Authorise the primary to reach other nodes over the mesh*, `:262` *Trust a throwaway JWKS certificate authority*, `:299` *Start the cluster headroom probe*, `:309` *Run functional tests*, `:388` *List slowest tests* and `:432` *Collect the cluster headroom series...*. `:299` and `:432` are the probe. `:222` is very probably needed too and is the trap: `tools/ci_headroom_launch.sh:69-71` does its own `mkdir -p /srv/ci/traces 2>/dev/null || true` with a comment saying the workflow already created it, but the workflow step does that with `sudo` **and a `chown`**, while the script's own attempt runs unprivileged and swallows failure -- so without `:222` the probe would start, fail to write, and exit 0. Verify that rather than assuming it, by checking whether `/srv/ci` is writable by `base_image_user` on a freshly deployed node. Leave `:309` and `:388` alone -- those genuinely are the functional suite. Judge `:235` and `:262` on whether the collect step's Loki census and the report need them; the default is to leave them alone, and to say in the diff why. In this repository, move `'bundle-shakenfist-full-ansible-modules'` out of `UNINSTRUMENTED_BUNDLES` (`tools/ci_headroom_harvest.py:166-169`) and into `BUNDLE_TOPOLOGIES` (`:133-143`) as a `BundleKind` with topology `slim-primary` and the `job_prefix` GitHub actually reports for that job -- **read it from a real run's `runs/<id>/jobs` listing, do not derive it**, because the comment at `:126-132` records that the derivation broke once already. Update that comment's count of "four jobs" and the `UNINSTRUMENTED_BUNDLES` docstring, which explains both skips and will then explain one. Add or extend a unit test so the table's shape is pinned. |
| 6e | medium | sonnet | none | **File D4's issue and record D5.** Open an issue against `shakenfist/shakenfist` for instrumenting the two cluster deploys that reach `build-smoke-cluster` directly -- `Node lifecycle` (`functional-tests.yml:581`) and kerbside's `sf-e2e-functional.yml:104` -- naming the composite action as the seam, why the probe steps living in the reusable workflow is the cause, and that the fix changes every caller's deploy through an action consumed at `@main` with no pin. Cross-reference the two existing Future work entries and note the overlap with [#4320](https://github.com/shakenfist/shakenfist/issues/4320), which is about the same job. Record the number in this plan and in the master plan's Future work. Separately, add a Future work entry for D5 naming F5's three blockers to a downstream window -- the `event=merge_group` filter at `tools/ci_headroom_harvest.py:421`, client-python having no merge queue (`functional-tests.yml:11-12`), and `BUNDLE_TOPOLOGIES` being keyed on shakenfist's own artifact names -- so the next attempt starts from the list. |
| 6f | medium | sonnet | none | **Close-out.** Set this phase `Complete` in the master plan's Execution table and update the `docs/plans/index.md` row's description to what the survey found and what shipped. The count becomes `7 of 8`: `tools/check-plan-status.py` derives it from how many Execution rows read `Complete`, so it moves when this row does, and the empty `Merged` cell does not affect it. That cell stays `—` until phase 7's planning appends this phase's merge commit, which the note under the Execution table already says. Write *What phase 7 inherits*, naming: the uninstrumented composite-action deploys and their issue; D5's three blockers; D6's open `slim-primary` question; and that phase 7's audit of the `shakenfist/actions` half runs against that repository's default branch per the master plan's phase 7 section. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`, and confirm every Definition of done item **by running it**, not by reading it. |

6a is independent and can start immediately. 6b is gated on the back
brief and must land before 6c, which writes into the page 6b creates.
6d's two halves are independent of everything else but the harvest half
should not merge long before the actions half is pushed, or the next
harvest skips a bundle that now has data. 6e is independent. 6f is last.

## Prepared changes

6d's `shakenfist/actions` half, for the operator to review and push. Only
the operator can push that repository (phase 4's F7), so this is a diff to
apply, not an applied edit.

### The seven gates, judged

| Line | Step | Verdict |
|---|---|---|
| `:222` | Make the traces directory | **Widen.** The trap: without it the probe starts, fails to write, and the job still exits 0. Evidence below. |
| `:235` | Authorise the primary to reach other nodes over the mesh | **Leave alone.** Serves the functional suite's own cross-node assertions (`test_federation.py` and friends inspecting host state on *other* cluster nodes); neither `ci_headroom_launch.sh` nor `ci_headroom_collect.sh` ever ssh anywhere but the primary. |
| `:262` | Trust a throwaway JWKS certificate authority | **Leave alone.** Already double-gated on `inputs.stestr_config == 'cluster-ci.conf'`, and the Ansible modules call site (`functional-tests.yml:528-538`) never sets `stestr_config` (it defaults to `smoke-ci.conf`, and the input is ignored for `test_kind: ansible-modules` regardless), so widening `test_kind` alone cannot turn this step on. |
| `:299` | Start the cluster headroom probe | **Widen.** One of the two probe steps proper. |
| `:309` | Run functional tests | Leave alone -- this is the functional suite itself. |
| `:388` | List slowest tests | Leave alone -- this is the functional suite itself. |
| `:432` | Collect the cluster headroom series, refusal census and capacity waits | **Widen.** The other probe step; already `if: always() && ...`. |

### `:222` is the trap -- verified, not assumed

`/srv/ci` is created by the `Make /srv/ci` ansible task in every
`ansible/ci-topology-*.yml` (e.g. `ci-topology-slim-primary.yml:302-306`),
inside the play at `ci-topology-slim-primary.yml:256` (`hosts: allsf,
become: true`). The `file` module runs as root under `become: true` with no
`owner:`/`group:` override, and `mode: u+rw,g+rw,o-rwx` -- so `/srv/ci` ends
up `root:root`, mode `0660`. `base_image_user` (`debian`) is in neither the
owning user nor group, so it has **no** access to that directory at all --
not even to list it, let alone create a subdirectory under it.

`tools/ci_headroom_launch.sh:69-71` does:

```bash
# Already created and chowned by the workflow's "Make the traces directory"
# step; this is belt and braces for a caller that skipped it.
mkdir -p /srv/ci/traces 2>/dev/null || true
```

Unprivileged, and it swallows its own failure. Without `:222`'s `sudo mkdir
-p /srv/ci/traces; sudo chown -R debian:debian /srv/ci/traces`, that `mkdir`
fails with permission denied and is silently ignored. The probe launcher
then backgrounds `ci_headroom_probe.py` with
`>/srv/ci/traces/headroom-probe.log 2>&1` -- redirecting into a directory
that does not exist -- which fails the `nohup` invocation, but the whole
remote heredoc is itself wrapped in `... <<'REMOTE_EOF' || true` in
`ci_headroom_launch.sh`, and the script's last line is `exit 0`
unconditionally. So the failure is invisible at every layer: the step
succeeds, the job succeeds, and the collect step (`:432`, `if: always()`)
finds no `headroom.jsonl` and reports "no probe" rather than an error. That
is a worse outcome than the gate staying narrow, because a harvest of that
run cannot tell "probe never ran" from "this specific run's traces
directory was never made writable" -- both already collapse to the same
`absent_reason` in `tools/ci_headroom_harvest.py`, which is exactly why
`:222` has to be included rather than left to the belt-and-braces fallback.

### The diff

```diff
--- a/.github/workflows/smoke-cluster.yml
+++ b/.github/workflows/smoke-cluster.yml
@@ -219,7 +219,16 @@
               "${setup} cirros /srv/ci/cirros --shared"
 
       - name: Make the traces directory
-        if: inputs.test_kind == 'functional'
+        # Widened for D3 (PLAN-ci-cloud-sizing-phase-06-docs.md): the Ansible
+        # modules job also runs the probe (see the "Start the cluster
+        # headroom probe" step below) and needs a writable /srv/ci/traces
+        # first. /srv/ci itself is created root:root, mode 0660
+        # (ansible/ci-topology-*.yml's "Make /srv/ci" task), so an
+        # unprivileged mkdir from ci_headroom_launch.sh cannot create the
+        # traces/ subdirectory under it -- only this sudo mkdir + chown can.
+        # Without this step the probe silently fails to write and the job
+        # still exits 0, which is worse than not probing at all.
+        if: inputs.test_kind == 'functional' || inputs.test_kind == 'ansible-modules'
         run: |
           . ${GITHUB_WORKSPACE}/ci-environment.sh
           ssh -i /srv/github/id_ci -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
@@ -296,7 +305,9 @@
       # would poll a cancelled job's leaked cluster. It caps itself because
       # cancel-in-progress means the stop step below is not guaranteed to run.
       - name: Start the cluster headroom probe
-        if: inputs.test_kind == 'functional'
+        # Widened for D3: this is one of the two probe steps the Ansible
+        # modules job was missing (the other is the collect step below).
+        if: inputs.test_kind == 'functional' || inputs.test_kind == 'ansible-modules'
         continue-on-error: true
         run: |
           . ${GITHUB_WORKSPACE}/ci-environment.sh
@@ -429,7 +440,8 @@
       # CI_HEADROOM_GATE. See also D7 in
       # https://github.com/shakenfist/shakenfist/blob/develop/docs/plans/PLAN-ci-cloud-sizing-phase-05-guardrails.md
       - name: Collect the cluster headroom series, refusal census and capacity waits
-        if: always() && inputs.test_kind == 'functional'
+        # Widened for D3, matching the probe start step above.
+        if: always() && (inputs.test_kind == 'functional' || inputs.test_kind == 'ansible-modules')
         env:
           CI_HEADROOM_GATE: ${{ inputs.headroom_gate }}
         run: |
```

Verified: the resulting file parses as valid YAML, and `:235` and `:262`
are untouched so the functional-suite-only steps still run only for
`test_kind: functional`.

### Why this is safe to push

Unchanged from D3's own reasoning: the Ansible modules call site
(`functional-tests.yml:535`) passes `headroom_gate: false`, `:299`'s launch
step carries `continue-on-error: true`, and `:432`'s collect step already
runs `if: always()`. Nothing here changes the suite the job runs
(`ansiblemoduletests.sh`, gated separately at line ~346), only adds a
side-channel probe and its supporting directory. Worst case is an
uninformative headroom record, not a failed or slower job.

### After pushing

One `Ansible modules (collection)` merge-group run after the push should
carry `traces/headroom.jsonl` and `traces/headroom-census.json` in its
bundle. `tools/ci_headroom_harvest.py` (6d's applied half, see below) is
ready to read it the moment it exists; until then a harvest still succeeds
and records the bundle with `series_present: false` and an `absent_reason`
naming the missing series, per Definition of done item 8.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The 440-line move in 6b buries the new content and makes review harder. | 6b is a pure move in its own commit with no wording changes, so `git diff -M` reads as a rename; 6c's prose is a separate commit. The back brief gates the move before any editing starts. |
| The move breaks an inbound link or anchor. | There is exactly one inbound anchor in the tree (`AGENTS.md:143`) and it is outside the moved range. The `Check documentation links and anchors resolve` pre-commit hook is run explicitly in 6b, not just at commit time. |
| Widening the probe gate in 6d breaks or slows the `Ansible modules` job. | That call site passes `headroom_gate: false`, the launch step is `continue-on-error: true` and the collect step is `if: always()`, so a probe failure cannot fail the job. The operator watches one merge run after pushing the actions change; the revert is the one `if:` condition. |
| 6d's harvest-table half lands without the actions half, or long after it. | The harvest is hand-run, not CI, so a mismatch cannot break a run. No window is open. 6f's Definition of done requires a harvest over one merge run *after* the actions change, which fails if the halves disagree. |
| The documented arithmetic goes stale the next time a topology changes. | 6c states the reshaped figures exactly once and names the topology files and `MINIMUM_HYPERVISOR_LEDGER` as the source of truth; the Definition of done includes a script that recomputes both ledgers from the topology files and compares them to what the page says. |
| Documenting the ledger contradicts `docs/operator_guide/scheduler.md`, which already explains overcommit. | 6c links rather than restates wherever that page already covers a term, and the Definition of done greps both pages for the reservation rule and the overcommit ratio to confirm they agree. |
| D6's question is recorded and then never answered, so the band's lower bound stays a fact nobody acts on. | It goes in Future work with the harvest command and the falsification criterion a shrink would need, and in *What phase 7 inherits*, so the push audit reads it. |

## Definition of done

Falsifiable, in order. Run them; do not read them.

1. `docs/developer_guide/ci_cloud_sizing.md` exists (or, if the back
   brief rejected D2, `ci.md` gained the same material and every item
   below reads against `ci.md` instead).
2. The page states both cluster ledgers, and the script in *The
   arithmetic, checked* -- run against `shakenfist/actions` at `main` --
   reproduces them from `ansible/ci-topology-slim-tier.yml` and
   `ansible/ci-topology-slim-primary.yml` as **24** and **27**. The page
   and the script agree.
3. The page names `MINIMUM_HYPERVISOR_LEDGER` and
   `shakenfist/deploy/shakenfist_ci/sizing.py`, so a reader can find what
   enforces the floor: `grep -c 'MINIMUM_HYPERVISOR_LEDGER'` is at least 1.
4. No fact about either topology's *current* ledger is stated
   differently in two places. Scope the check to where a reader looks for
   the present tense -- `grep -rn 'ledger' docs/developer_guide/
   tools/ shakenfist/deploy/ AGENTS.md ARCHITECTURE.md` plus the master
   plan -- and confirm every figure reads 24 for `slim-tier` and 27 for
   `slim-primary`. **Do not widen this to `docs/plans/`**: phases 2, 3 and
   4 correctly state the pre-reshape ledger of 12 dozens of times as the
   thing they measured and reasoned against, and rewriting those would
   destroy the record. The Situation table is the one present-tense
   exception, and D7 annotates it rather than changing it.
5. The seams-test docstring no longer claims the reusable workflow
   gates by default. **`grep -c 'defaults to gating'` is not the check**:
   the phrase wraps across lines `13` and `14`, so a line-oriented grep
   returns 0 today and would pass before anything was done. Use
   `python3 -c "import io,re; print(len(re.findall(r'defaults\s+to\s+gating',
   io.open('shakenfist/tests/test_headroom_gate_workflow_seams.py').read())))"`,
   which returns 1 now and must return 0. The test still passes
   afterwards.
6. Every `functional-tests.yml:NNN` reference in
   `tools/ci_headroom_harvest.py` points at what its comment says it
   does, checked line by line against the file.
7. `grep -rn 'fork these topologies\|repositories fork' docs/ --include=*.md
   | grep -v phase-06-docs` returns nothing. It matches **two** places
   today, not one: the master plan's Future work entry at
   `PLAN-ci-cloud-sizing.md:1677`, and phase 5's *What phase 6 inherits*
   at `PLAN-ci-cloud-sizing-phase-05-guardrails.md:839`. The second is a
   claim addressed to this phase, so correcting it is in scope even though
   that plan has merged. The exclusion is needed because this plan quotes
   the phrase in F1 and in this item. The master plan's *Phase 6* section
   was already corrected by the planning commit.
8. If 6d landed: `'bundle-shakenfist-full-ansible-modules'` is in
   `BUNDLE_TOPOLOGIES` and absent from `UNINSTRUMENTED_BUNDLES`; and a
   `tools/ci_headroom_harvest.py` run over one `merge_group` run created
   *after* the actions change enumerates five instrumented cluster
   bundles with no `UnknownBundleError` and no bundle recorded with an
   empty series.
9. D4's issue exists, and its number appears in this plan and in the
   master plan's Future work. D5's Future work entry names all three of
   F5's blockers.
10. `docs/plans/index.md`'s row for this plan describes what shipped, and
    `python3 tools/check-plan-status.py` reports agreement.
11. `pre-commit run --all-files` passes.

## Back brief

**One gate, before any editing starts.** D2 moves 440 lines out of
`docs/developer_guide/ci.md` into a new
`docs/developer_guide/ci_cloud_sizing.md`. It is cheap to propose and
annoying to redo, it is the one structural change in an otherwise additive
phase, and F6 removes the tidiest argument for it by showing neither page
is published to the site. Step 6b does not begin until that is agreed.
If it is rejected, 6c writes into `ci.md` and the rest of the plan is
unchanged.

Two things worth disagreeing with, both argued in place rather than
hidden:

* **D7 annotates the Situation table instead of correcting it.** A reader
  who misses the note reads a stale ledger. The case for the note is that
  the table is what phases 2, 3 and 4 reasoned from, and a baseline that
  tracks the present is not a baseline.
* **D4 leaves two cluster deploys uninstrumented.** The fix is known and
  the cost of not doing it is already being paid in the failure table.
  The case for the issue is blast radius: the probe steps would have to
  move into a composite action consumed at `@main` with no pin, which
  changes every caller's deploy, and phase 5 spent a whole decision
  establishing that irreversible cross-repository acts belong where a
  revert can reach them.

Everything else in the phase is additive documentation and one `if:`
condition, and does not need a gate.
