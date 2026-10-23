# Measure database polling on an idle cluster

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

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`AGENTS.md` for project conventions and the index into
`docs/`, including build commands and database access
patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

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

## Situation

`test_no_unbudgeted_fixed_rate_database_polling`
(`shakenfist/deploy/shakenfist_ci/database_tier.py`) is the review-time
detector for the August 2026 database load regression, which was
fixed-rate polling on an idle cluster: about 5 `Dequeue`/s per node and 19
`GetExistingLocks`/s cluster-wide with nothing to do (#3499). It runs
inside the parallel functional suite, so the cluster is never idle while it
measures. It therefore has to *infer* which traffic is a poll from four
60 second windows of a busy cluster, using two filters in
`shakenfist/deploy/shakenfist_ci/load_budget.py`:

* `fixed_rate()` keeps a pair whose busiest window is within 2.83x of its
  quietest.
* `independent_of_activity()` keeps a pair whose share of the tier's
  traffic moved more than its rate did, and the test skips unless the
  tier's total varied by at least 1.8x.

Neither filter can do the job. The tier total that the second filter
compares against is set by whichever test spiked in one window, so a
workload pair driven by any *other* test reads as independent of it. In
#4021's twenty recorded failures the check certified object creates and
deletes (`(SetObjectState, api)`), network listings, lock traffic from the
queue worker, and more as new polls. Across the family (#3975, #3999,
#4014, #4028, #4039, #4072, #4092, #4110, #4176, #4359, #4401, #4450, #4464,
#4575) neither of the test's assertions has yet found a real new
server-side poll. Every finding has been the harness's own polling, suite
workload or a gap in the model, and each was answered with an exemption
(`HARNESS_DRIVEN_PAIRS`) or a model change.

Meanwhile a genuine poll is far more regular than the filters require. In
run 39847969049 every `GetNodeDaemonState` positive control held within
1.6% across the four windows (about ±1 event in 90). On an idle cluster
that regularity is the *only* thing left to see, which is why the
regression the test exists for was an idle-cluster regression in the first
place.

The fix for #4021 (branch `issue-4021-unbudgeted-api`) is an interim
measure that does not depend on this plan. It reports `api`-caller pairs
under `request_driven` instead of failing on them, records per-window rates
for every flagged pair, and evaluates both assertions in one run (#4177).
It clears the dominant false positive. It does not touch the queues, net
and transfers pairs (#4401, #4450, #4464), and it widens CI's blind spot
to every `api` pair. This plan removes the reason for both.

The functional suite is run by the `Run functional tests` step of
`shakenfist/actions/.github/workflows/smoke-cluster.yml` as one
`stestr run --config <stestr_config> --concurrency=<n>` over ssh to the
primary. The CI headroom probe is stopped in a later step of the same job.

## Mission and problem statement

Measure database polling where polling is the only thing happening: on the
CI cluster after the functional suite has finished and the cluster has
settled. Then replace the in-suite inference with that direct observation.

On an idle cluster nearly every request the tier serves comes from a
loop: the daemons' own polls, the cleaner and cluster sweeps, and
sidechannel monitoring of whatever instances are standing. A pair that is
present at a steady rate with no client connected *is* a poll. No activity
covariate, spread gate or harness exemption is needed to say so, and that
includes `api` pairs, which become visible again. The budget's poll terms
can be held to the rate that configuration dictates. A small number of
deliberately standing instances keeps the per-instance terms in play, so
they are measured rather than evaluated at zero.

Success means:

* the idle check gates every merge-queue functional job that today runs
  the in-suite test, and fails on an unbudgeted steady pair or a budgeted
  pair over its ceiling;
* the in-suite unbudgeted assertion, `independent_of_activity()`, the
  activity spread gate, `REQUEST_DRIVEN_CALLERS` and the `HARNESS_DRIVEN_PAIRS`
  entries that only exist because the suite was running are retired;
* the open issues in this test's family that are activity artefacts are
  closed as superseded rather than fixed one exemption at a time.

## Open questions

1. **What does "settled" mean, and how long can it take?** After the last
   test, teardown deletes, the queue worker drains, and the cleaner and the
   deleted-object sweep catch up. The idle check must wait for an
   observable condition rather than a sleep: no instance, network or
   namespace in a transitional state, an empty queue, and perhaps two
   consecutive scrapes with no `Create*`/`Delete*` traffic. The wait needs
   an upper bound, and what happens when it is hit (fail, or skip and say
   so) is a decision.
2. **Which jobs run it?** Every merge-queue functional job (about 2–4
   minutes each, on the critical path), or one per topology family. The
   1-node smoke topology in PR CI is the cheapest place to find a new poll
   before it reaches the queue.
3. **Does it run when the suite failed?** Running it after a failed suite
   gives more evidence per run. It also risks measuring a cluster left in a
   broken state, which the settle condition may never satisfy.
4. **How long a measurement?** On an idle cluster two windows may be
   enough to show steadiness, and fewer windows cost less. The
   straddle allowance in `FIXED_RATE_MAX_SPREAD` exists for slow loops
   under the ceiling, and its value should be revisited, not inherited.
5. **What becomes of the in-suite test?** Its `over_budget` half checks
   load against the model under a realistic workload, which the idle check
   cannot. Keep that half (non-gating or gating), or retire the whole test
   once the idle check has soaked.
6. **Where does the code live?** A new stestr directory and config in this
   repository (`idle_ci_tests/`, `idle-ci.conf`), reusing `load_budget.py`,
   keeps the measurement beside the budget it reads. The workflow step that
   runs it lives in `shakenfist/actions`, so the change spans two
   repositories. The step needs an input that defaults off, so the
   downstream repositories that call `smoke-cluster.yml` are unaffected.

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

!!! note "In this project"

    The same term is written twice: once in the phase table
    below, and once in the row this plan carries in
    `docs/plans/index.md`. Keep them in step -- the index row is
    the whole-plan status, so it only reaches `Complete` once
    every phase has been completed, abandoned or superseded.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 0. Decisions: settle condition, job coverage, fate of the in-suite test | — | Not started | — |
| 1. Idle load check as its own stestr suite in this repository | — | Not started | — |
| 2. Run it from `smoke-cluster.yml` behind a default-off input, and enable it in this repository's merge jobs | — | Not started | — |
| 3. Soak, then retire the in-suite unbudgeted assertion and the activity filter | — | Not started | — |
| 4. Push audit | — | Not started | — |

Phase 1 is the measurement: a settle helper with a bounded wait, a fixture
that leaves a small known number of instances standing, the positive
control carried over from the in-suite test, and both budget assertions
with no activity filter. It also needs unit tests for the settle logic in
`shakenfist/tests/`, so the harness is checked on every commit and not
only when a cluster is built. It can be run by hand against a CI cluster
before any workflow calls it.

Phase 2 touches `shakenfist/actions`. Its step runs after the headroom
probe is stopped, so the probe's pairs are not part of the idle
measurement, and the exemption for them is not needed there.

Phase 3 begins with a soak. The idle check gates, and the in-suite test
keeps running for comparison. Only then are `independent_of_activity()`,
`activity_spread()`, `ACTIVITY_DISCRIMINATION_SPREAD`,
`REQUEST_DRIVEN_CALLERS` and the suite-only `HARNESS_DRIVEN_PAIRS` entries
deleted, along with the tests that hold them up and the prose in
`docs/developer_guide/ci_cloud_sizing.md` that justifies them. The
probe's obligation to trim its exemption on retirement goes with them.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

!!! note "In this project"

    The runbook is `PUSH-AUDIT.md` at the repository root, and
    it reads its `$RANGE` from the `Merged` column, so a plan
    without one cannot be audited once its phases have merged.

    The column goes in the master plan's Execution table, last:

        | Phase | Plan | Status | Merged |

    `tools/check-plan-status.py` reads the `Status` column by
    name rather than by position, so a `Merged` column added
    after it does not disturb the index arithmetic. A phase
    which has not landed reads `—` -- in a table cell or a
    `**Merged**:` line alike -- rather than an empty cell (as
    the worked example in the `plan-file-conventions` block
    above leaves it), `TBD` or `n/a`.

    A few of this repository's older roadmaps write their
    phases as prose sections rather than as a table:
    `blob-storage-roadmap.md`, `api-query-batching-roadmap.md`
    and `PLAN-attribute-field-masks.md`, which are the three
    named in `check-plan-status.py`'s `HAND_COUNTED`. The
    first two carry a `**Merged**:` line in the phase's own
    section -- beside `**Status**` where the phase has one,
    otherwise directly under the heading. The third carries no
    such line, which is a decision rather than an oversight:
    it is `Complete` and has no push-audit phase, so the
    shared block's do-not-reopen rule leaves it alone. Nothing
    checks either form, so a wrong SHA is caught only in
    review.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    This repository does not track human review, so it has no
    `REVIEWS.md` and no `prune-reviews` workflow; the third rule
    has nothing to act on here. The first two apply as written.

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

    Phases involving schema design, cross-daemon coordination,
    protocol changes, or subtle correctness questions (locking,
    consistency, migration safety) should be planned at high
    effort. Phases that mirror an already-established pattern --
    adding a new MariaDB accessor alongside an existing one, for
    example -- can be planned at medium effort.

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

    High effort typically means a new object type lifecycle, a
    cross-daemon protocol change, or migration logic; medium
    effort typically means a new gRPC endpoint parallel to an
    existing one, or a new MariaDB accessor; low effort typically
    means a rename, a log line, or regenerating proto stubs.

    A worked brief for this codebase: instead of "add MariaDB
    functions for the new object", write "add direct, gRPC, and
    public wrappers for `get_widget` in `shakenfist/mariadb.py`,
    mirroring the `get_artifact` trio at lines
    11129/11797/11811. The direct path uses
    `_get_widgets_table()`; the gRPC wrapper goes through
    `GetWidget` on the database daemon; register the counter in
    the Monitor operations list in
    `shakenfist/daemons/database/main.py`."

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

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* Object or attribute filtering is pushed down to the
  MariaDB SQL layer where indexes can make it faster, rather
  than materialising everything and filtering in Python.
* There are unit tests for core logic and preferably
  functional test coverage as well (see
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests`).
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* gRPC proto changes (if any) have been regenerated with
  `tox -e genprotos`.
* Documentation in `docs/` has been updated to describe any
  new features, commands, or database changes. In particular
  `docs/operator_guide/database.md` has been updated for
  schema changes.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules, daemons,
  or object types.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans*
  table: the date the plan was written, a link to it, a
  one-line intent, its status from the vocabulary above,
  and its phase arithmetic (`0 of 7`, or `—` for a plan
  with no phases). One row per master plan, never one per
  phase — the phases are tracked in this plan's own
  Execution table, and duplicating them in the index is
  how the two drift apart. Rows run oldest first.
* **`order.yml`** — add an entry for the new master plan,
  in the intended reading order. This registers the plan
  and is what the plan-status check reads; it does **not**
  by itself put the plan in the published site navigation,
  which `mkdocs.yml.tmpl` currently lists by hand. The file
  is not ours: its format and its meaning as a per-directory
  navigation allowlist come from `tools/sync_component_docs.py`
  in shakenfist/actions, which is how a repository's docs are
  synced into a documentation site. Keeping it complete is
  what would make generating the nav from it a mechanical
  change later. Phase files should *not* be added to
  `order.yml`; they are linked from the master plan's
  Execution table only, which makes that table the only path
  to them.

The index row carries the whole-plan status, so it only
reaches `Complete` once every phase has been completed,
abandoned or superseded. Update it as the arithmetic
changes, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* The `over_budget` siblings (#4072, #4176, and the model issues behind
  #3999 and #4092) are a modelling problem rather than a discrimination
  problem, and this plan does not address them except through the phase 0
  decision on the in-suite test.
* Once the idle check exists, the unbudgeted ceiling it uses could be
  lowered, since an idle cluster has no workload noise to clear.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

* #4021: workload certified as polls by the activity filter. The interim
  fix is on `issue-4021-unbudgeted-api`; this plan is the root-cause fix.
* #4177: only the first failing category was reported. Fixed on the same
  branch.
* #4110 and #4575 (`api` pairs) are closed by the interim fix. #4401,
  #4450 and #4464 (queues, net and transfers pairs) are expected to be
  superseded by phase 3.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
