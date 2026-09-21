# Plan: the size of a review unit

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two documents matter more than usual for this plan.
`templates/shared-blocks/README.md` is the mechanism phase 1 uses,
including its warning about what adding a name to an enforced list
does to the fleet on the next morning's run. And
`scripts/tests/base.py` is the thing phases 2 to 4 finish: its
module docstring is the original statement of the problem, written
when the base class was introduced and the migration was
deliberately left part-done.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The daily
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  the next morning, so a plan should say how many repositories --
  including this one -- it newly fails.

Every number in the Situation section below was derived from the
tree at `5861c0a` and is re-derivable with the commands given
beside it. The tree moves; re-derive before relying on any of
them, and say so in the phase if the figure has shifted enough to
change a decision.

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

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

Human review in this repository is **whole-file**, and a review is
**discarded when the file changes**. `REVIEWS.md` records a
reviewer, a date and the blob SHA that was read;
`docs/audits/review-coverage.md` says coverage is recomputed
against `HEAD` rather than trusted from the committed file, so a
file that moves by one line owes its whole length to the next
review session.

That makes the cost of a review unit `size x churn`, paid
repeatedly, and it is concentrated in a handful of files:

| File | Lines | Commits, 6 months | Lines to re-read |
|---|---:|---:|---:|
| `scripts/tests/test_packaging.py` | 2660 | 12 | 31,920 |
| `scripts/audit/checks/packaging.py` | 2067 | 13 | 26,871 |
| `scripts/tests/test_plans.py` | 2754 | 9 | 24,786 |
| `scripts/tests/test_ci_workflows.py` | 2210 | 10 | 22,100 |
| `scripts/audit/checks/ci_workflows.py` | 1863 | 10 | 18,630 |

Across all 56 Python files under `scripts/`, the six-month
re-review obligation totals 268,288 line-reads. **The eight files
over 1,000 lines carry 168,753 of them -- 63% of the burden from
14% of the files.** Derive with:

```
for f in $(find scripts -name '*.py' -not -path '*__pycache__*'); do
    echo "$(wc -l < $f) $(git log --since=6.months --oneline -- $f | wc -l) $f"
done
```

Fleet-wide the same shape holds, and it is not a tests-only
problem. Across the five clones available locally (`actions`,
`development`, `divergulent`, `kerbside`, `ryll`), 58 source files
exceed 800 lines, 18 exceed 1,500 and 9 exceed 2,000; the largest
is `ryll/src/app.rs` at 6,131. Any sweep of this kind must exclude
vendored and generated trees -- the first pass of that survey
surfaced a 114,409-line `encoding_rs/data.rs` out of
`ryll/.cargo-cache/`, which is nobody's review unit.
`scripts/audit/text/python_source.py` already carries the
exclusion-list pattern for exactly this reason.

Nothing in the repository currently says any of that. `PUSH-AUDIT.md`
asks reviewers about comment proportion, functional test coverage,
Python version discipline and path traversal, and says nothing about
how big a file should get before it stops being reviewable.

### The largest single source of length is a part-done refactor

`scripts/tests/base.py` exists, and its module docstring states the
problem it was built for:

> Before this existed each test class built its own temporary
> directory, its own `_repo` helper and its own `_check` wrapper:
> about forty near-identical helpers over a hundred and seven
> temporary-directory sites. The duplication was not the worst of
> it -- the variation was. Two classes testing the same criterion
> could disagree about what a fixture repository looks like, and
> neither would fail.

`run_check()` in that file records why the migration stopped: the
moved tests "are kept verbatim -- they are the coverage this
refactor must not lose, and rewriting thousands of lines of
assertions by hand is how coverage goes missing quietly." That was
the right call for the restructure (see
`PLAN-audit-scripts-restructure.md`, which introduced the base
class). It is not a resting place.

The suite is 25 classes on `CheckTestCase` and 68 still on bare
`unittest.TestCase`:

| File | Lines | Old-style classes | in lines | `CheckTestCase` classes | in lines |
|---|---:|---:|---:|---:|---:|
| `test_plans.py` | 2754 | 9 | 2695 | 0 | 0 |
| `test_packaging.py` | 2660 | 6 | 1286 | 8 | 1328 |
| `test_ci_workflows.py` | 2210 | 7 | 1079 | 6 | 1090 |
| `test_docs_content.py` | 1916 | 7 | 1816 | 2 | 54 |
| `test_npm_dependencies.py` | 777 | 2 | 52 | 3 | 667 |
| `test_github_config.py` | 732 | 1 | 42 | 3 | 449 |
| `test_registry.py` | 706 | 5 | 671 | 0 | 0 |
| `test_llm_docs.py` | 562 | 3 | 515 | 0 | 0 |
| `test_distros.py` | 553 | 7 | 266 | 2 | 262 |
| `test_metadata.py` | 507 | 3 | 165 | 0 | 0 |
| `test_review.py` | 457 | 4 | 413 | 0 | 0 |
| `test_runners.py` | 414 | 3 | 342 | 1 | 37 |
| `test_markdown.py` | 196 | 4 | 177 | 0 | 0 |
| `test_hooks.py` | 227 | 1 | 37 | 0 | 0 |
| `test_manage_issues.py` | 120 | 2 | 87 | 0 | 0 |
| `test_repo.py` | 107 | 1 | 79 | 0 | 0 |
| `test_python_source.py` | 103 | 3 | 84 | 0 | 0 |
| **Total** | | **68** | **9806** | **25** | **3887** |

Not all 68 are migration candidates. `CheckTestCase` is for classes
that exercise a `Check` subclass; a class testing a pure helper
(`WorkflowJobBlocksTest`, most of `test_markdown.py`,
`test_python_source.py`, `test_metadata.py`) has nothing to inherit
and correctly stays as it is. A crude filter -- old-style classes
whose body mentions `run_check(` or `check_` -- puts the candidate
set at **36 classes spanning roughly 7,200 lines**, leaving 32
classes that stay. That filter is a heuristic and will
over-count; phase 2 re-derives it per file and the plan records
the real number.

The density difference between the two styles is the argument. In
`test_ci_workflows.py`, `WorkflowPermissionsTest` on `CheckTestCase`
runs five tests in 28 lines. `RetiredCommentAddresserTest` on bare
`unittest.TestCase` spends 26 lines on `_repo` and `_check` before
its first assertion, and those 26 lines are a private
reimplementation of `FixtureRepo` and `run_check` that nothing
compares against the original.

Counting `def _repo`, `def _check` and `TemporaryDirectory()` sites
still in `scripts/tests/`: 142, across 11 files.

### What is not wrong with these files

A large share of the length is rationale comments -- "The bug this
audit exists for: on merge_group `github.ref` is
`gh-readonly-queue/<base>/pr-N-<SHA>`, unique per rebuild, so
`cancel-in-progress` never matches." Those are the most valuable
lines in the file, they are what makes a two-thousand-line test
file reviewable at all, and the `comment-proportion` shared block
already protects them. Any guidance this plan writes has to say, in
the block itself, that the length is not to be met by deleting the
why. A rule that trades rationale for line count is worse than no
rule.

`testscenarios` was considered as the deduplication mechanism and
rejected. Nothing in the fleet uses it (`grep -rn testscenarios`
across these repositories returns nothing); it depends on
`testtools`, and `PLAN-stestr-testtools.md` is `Blocked` on a
`stestr` / `testtools` / `python-subunit` incompatibility we
already pin around, so adding a new `testtools` dependent is a poor
trade; and `scripts/` is stdlib-only by standing convention. The
stdlib `subTest` already does the parameterisation work and is
already used here -- `test_python_source.py:53`,
`test_registry.py:514`, `test_metadata.py:462`, and
`test_ci_workflows.py` in `test_the_spec_names_every_requirement`.
The pattern is adopted; the dependency is not.

## Mission and problem statement

Reduce what a review session has to re-read, in the two places the
measurements point at.

First, write down a guideline for how long a source file gets
before its size is itself a review finding, as a shared block so
that the fleet gets it through the mechanism that already keeps
this kind of wording from drifting. Frame it as re-review cost
rather than as taste, because that is the argument that is actually
true here, and make it advisory -- a candidate a reviewer may
raise, never a gate.

Second, finish the `CheckTestCase` migration that
`PLAN-audit-scripts-restructure.md` started and deliberately left
half done. (The figure carried into this plan was 25 of 61
applicable classes; phase 3's inventory re-derived it from the
source rather than from a substring heuristic and got 25 of 55.
Phase 3's table is the number, not this sentence.) This is the
single largest source
of reviewable-but-pointless length in `scripts/`, and it also
closes the variation risk that `base.py` was written to close and
has only half closed.

Deliberately out of scope:

* **Splitting any large file.** The guideline says when to consider
  it; acting on it across `scripts/` is a different change with a
  different risk profile, and doing both at once would make the
  migration's "no test was lost" claim unverifiable. Recorded under
  Future work, with the seams named.
* **Sweeping the fleet.** Phase 2 turns enforcement on and the
  daily run files the issues; this plan does not open pull requests
  in the repositories those issues land in. That is a deliberate
  operator decision, recorded under Decisions below with the counts
  it was taken against.
* **Changing what any check decides.** The migration is a test-side
  refactor. No file under `scripts/audit/` changes behaviour, and
  `tools/audit-snapshot.sh --diff` is expected to be empty at every
  commit.
* **Migrating classes that do not test a `Check`.** They have
  nothing to inherit. Leaving them alone is the correct outcome,
  not an unfinished one.
* **New dependencies.** `scripts/` is stdlib-only and stays that
  way.

## Open questions

* **Which section of `PUSH-AUDIT.md` carries the block?**
  `comment-proportion` and `python-version-discipline` sit in
  *2a. Code quality*; `functional-test-coverage` sits in *2b. Test
  review*. File size applies to all source, and the test files are
  merely where it bites hardest here. **Default: 2a.**
* **Are 800 and 1,500 the right numbers?** They are chosen against
  the fleet distribution above: 800 makes 58 files across five
  repositories a candidate, 1,500 makes 18. Low enough to catch the
  real cases, high enough that a reviewer is not raising it weekly.
  **Default: 800 advisory, 1,500 wants a stated reason, no hard
  cap ever.**
* **Does `test_plans.py` get migrated in this plan or deferred?**
  It is 2,791 lines with zero migrated classes and six candidates
  spanning 2,618 of them -- a third of the whole job and the one
  file where the migration could plausibly go wrong quietly.
  **Default: in scope, migrated last, as its own commit, with the
  before-and-after test count recorded in the commit message.**
* **Does the migration touch `scripts/test_review_tracking.py` and
  the other root-level suites?** They test CLIs rather than checks
  and have no `Check` to run. **Default: no.**

## Decisions

### D1. Both blocks are enforced, in this plan

An advisory block is copied by the repositories that were already
going to copy it. `plan-phase-landing` was written on 2026-09-19
against a reported fleet-wide problem -- merge conflicts and
excessive pull requests, traced to pruning `REVIEWS.md` from a
branch and to holding a plan's status update back until the
implementing pull request had landed -- and a rule aimed at a
systemic problem that repositories may decline does not address
it. The same reasoning applies to `source-file-size` on its first
day, so it is enforced with the other rather than waiting for a
constituency.

The cost is one morning of issues, and it is worth stating exactly:

| Criterion | Applicable | Compliant today | Compliant after | New issues |
|---|---:|---:|---:|---:|
| `push-audit` (+ `source-file-size`) | 11 | 8 | 1 | 7 |
| `plan-template` (+ `plan-phase-landing`) | 10 | 9 | 1 | 8 |

Fifteen new issues across nine distinct repositories
(`client-python-k3s`, `divergulent`, `hunkydory`, `instar`,
`kerbside`, `occystrap`, `private-ci`, `ryll`, `shakenfist`), plus
existing issues on `sfui` and `uncalibrated-sextant` that gain a
line. `development` stays compliant on both, because phase 1 embeds
`source-file-size` here before phase 2 enforces it, and
`PLAN-TEMPLATE.md` has embedded `plan-phase-landing` since
2026-09-19.

Each issue is closed automatically by a verbatim block copy, and the
issue body already names the file to copy from. Re-derive the counts
from `docs/audits/compliance.md` before flipping the switch; the
table above was read at `5861c0a` and the fleet moves.

### D2. The fleet is not swept by this plan

Operator decision: enforcement goes live and the daily audit drives
the fixes, rather than this plan opening pull requests in nine other
repositories. The fix is mechanical and the `standards-alignment`
skill already covers its shape, so the work is better done per
repository by whoever is next in it than batched here -- and
batching it would put this plan's own push audit in the position of
auditing nine repositories' diffs.

The consequence to accept rather than discover: for as long as the
backlog is open, `docs/audits/compliance.md` shows both criteria
mostly red. That is the intended state of a newly enforced rule, not
a regression, and anyone reading the page during that window should
be able to find this decision from it.

### D3. Enforcement is a phase, not a commit inside phase 1

The canonical block has to exist and be embedded here before the
list that requires it changes, or `development` fails its own
criterion between two commits of the same branch. Separating them
also means the enforcement switch is one reviewable commit touching
`scripts/audit/checks/plans.py`, its spec pages and its fixtures --
which is what a reviewer wants to look at hardest.

## Execution

Work happens on `review-unit-size` off `main`, in the worktree
`../development-review-unit-size`; this plan file lands with the
change. There is no `develop` branch here -- `REPO_OVERRIDES`
exempts this repository from the default-branch criterion because
it publishes no releases.

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The `source-file-size` shared block | Complete | 06437a7..33a042a |
| 2. Enforce both blocks | Complete | 06437a7..33a042a |
| 3. Inventory, and the gaps in `CheckTestCase` | Complete | 06437a7..33a042a |
| 4. Migrate the check tests, one file per commit | Complete | 06437a7..33a042a |
| 5. Retire the helpers and record the convention | Complete | 06437a7..33a042a |
| 6. Push audit | Complete | |

**All six phases ship as a single pull request.** That is an
operator decision taken when this plan was written, and it has
precedent: `PLAN-audit-scripts-restructure.md` landed six phases
and seventeen commits the same way. It has three consequences the
shared blocks below would otherwise dictate differently, and they
are recorded here rather than left to be discovered.

* The `plan-phase-landing` rule -- which phase 2 makes binding on
  the fleet, so this plan had better follow it -- that a phase is
  closed out in the first commit of the next phase still applies
  *within the branch*:
  each phase's first commit sets the previous phase's `Status`
  cell. Nothing is closed out across a merge, because there is only
  one merge.
* The five implementation phases share one `Merged` range,
  `06437a7..33a042a`, written in the audit phase's own commit --
  the first point at which both ends are known, because the audit
  phase is what would otherwise move the tip. The audit phase
  records no `Merged` cell of its own; it is the last row, which
  `plan-phase-landing` permits to omit one, and nothing ever reads
  it. If the branch is squashed or rebased on the way in those
  SHAs stop resolving: say so on the pull request and replace the
  range with the merge commit.
* Phase 6 audits `origin/main...HEAD` on the branch before the
  merge, which is the accumulated diff of all six phases -- the
  thing `plan-push-audit-phase` asks for, reached more directly
  than a merged plan can reach it.

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

**In this repository.** The same term is written twice: once in
this plan's own phase table, and once in the row the plan carries
in `docs/plans/index.md`. The index row is the whole-plan status,
so it only reaches `Complete` once every phase has been
completed, abandoned or superseded. The `plan-index` criterion
reads that table, and this repository is inside its own audit
matrix, so a status that drifts out of the vocabulary fails our
own tooling before it fails anybody else's.

<!-- shared-block: plan-push-audit-phase v3 -->
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
  untriggered for as long as it did.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

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

**In this repository.** The plan index is `docs/plans/index.md`.
`review-tracking-tests` deliberately does not assert the `REVIEWS.md`
header count, so a phase that adds or removes an in-scope file needs
no regeneration commit; `prune-reviews` corrects the count on the
next push to main.

**For this plan specifically.** The migration will stale a large
number of review marks, because it edits most of `scripts/tests/`.
That is expected and is not this pull request's business to fix:
`prune-reviews` corrects `REVIEWS.md` on the next push to `main`,
and the `review-coverage` audit will raise the backlog when it is
worth a session. Do not run `stamp` or `prune` on this branch.

### 1. The `source-file-size` shared block

Write the guideline down, in the mechanism the fleet already uses
for wording that must not drift.

**Deliverables.**

* `templates/shared-blocks/source-file-size.md`, holding the
  canonical block with its markers, drafted below.
* The same block embedded verbatim in `PUSH-AUDIT.md`, in
  *2a. Code quality*, after the `comment-proportion` block --
  which is deliberate adjacency, because the two interact and a
  reviewer should meet them together.
* A line in `templates/shared-blocks/README.md` adding
  `source-file-size` to the list of blocks required in
  `PUSH-AUDIT.md`. The README's long `plan-phase-landing`
  paragraph is phase 2's to rewrite, not this phase's.

`CanonicalSharedBlocksTest.test_real_canonical_blocks_parse` in
`scripts/tests/test_plans.py` enumerates the directory and requires
each file to contain a block whose name matches its filename; a new
file is picked up automatically and needs no test change. Nothing
requires a canonical block to be enforced anywhere, which is what
makes the not-yet-enforced path clean.

**Do not touch** `PUSH_AUDIT_BLOCKS` in
`scripts/audit/checks/plans.py` or `docs/audits/push-audit.md` in
this phase. Phase 2 does both together, and splitting them is what
keeps `development` compliant at every commit: the spec page must
name every *enforced* block
(`PushAuditTest.test_every_required_block_is_named_in_the_spec`),
so naming it here -- before the check measures it -- would describe
a requirement that does not exist, which is the defect
`CiReviewAutomationSpecTest` exists to catch, in mirror image.

**Draft block.** Copy this out to the canonical file; it carries
its own markers, which is why it is fenced here rather than
embedded.

```markdown
<!-- shared-block: source-file-size v1 -->
Source file size (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/source-file-size.md`):

- Where a repository tracks whole-file human review, a file's cost
  is its length times how often it is touched: every change
  discards the review of the whole file, and the next session
  re-reads all of it. That, rather than taste, is why length is
  worth raising in review at all.
- Treat a source file over roughly 800 lines as a candidate to
  split, and one over roughly 1,500 as wanting a stated reason to
  stay whole. Both are advisory. Neither is a gate, there is no
  hard cap, and a reviewer who raises one is opening a question,
  not recording a defect.
- Generated files, vendored trees and protocol or data tables are
  exempt: they are not read the way source is, and a tool that
  counts them is measuring the wrong thing.
- Split along a seam that already exists -- one module's public
  entry point, one check, one subcommand, one endpoint -- so that
  a later change touches one of the pieces rather than all of
  them. A file split at a line number rather than at a seam is
  worse than the long file it replaced.
- Length is never reduced by deleting the comments and docstrings
  that explain why the code is the way it is. Those are what make
  a long file reviewable, and trading them for a line count makes
  the review worse while making the number better. Cut duplicated
  scaffolding first; see `comment-proportion` for what earns its
  length.
<!-- shared-block-end -->
```

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Create `templates/shared-blocks/source-file-size.md` containing exactly the fenced draft block above, markers included and nothing else. Embed the identical text in `PUSH-AUDIT.md` immediately after the `comment-proportion` block's `<!-- shared-block-end -->` in section *2a. Code quality* (currently around line 204), separated by one blank line, matching the surrounding style. In `templates/shared-blocks/README.md`, add `source-file-size` to the sentence under "How the audit uses these" that lists the blocks required in `PUSH-AUDIT.md`. Do not edit `scripts/audit/checks/plans.py` or `docs/audits/push-audit.md` -- phase 2 owns both -- and do not touch the README's `plan-phase-landing` paragraphs. Then run `python3 -m unittest discover -s scripts/tests -t scripts` and `pre-commit run --all-files`. |

**Verification.** `pre-commit run --all-files` passes;
`python3 scripts/audit-check.py --repo-path . --repo-name
development` reports the same `push-audit` and `plan-template`
verdicts as before the change. Both must still be `pass` -- this
phase adds a block nothing yet requires, so nothing can move.

### 2. Enforce both blocks

One commit. Add `source-file-size` to `PUSH_AUDIT_BLOCKS` and
`plan-phase-landing` to `PLAN_TEMPLATE_BLOCKS`, both in
`scripts/audit/checks/plans.py`, with everything each list drags
behind it.

**The two lists are not symmetrical, and this is the trap.**
`PlanTemplateTest.setUp` builds its fixture by looping over
`PLAN_TEMPLATE_BLOCKS`, so adding a name there needs no fixture
change at all. `PushAuditTest.setUp` hard-codes its eight blocks as
named attributes and assembles `self.canonical` from them by hand,
so adding a name to `PUSH_AUDIT_BLOCKS` without touching the
fixture fails `test_the_fixture_covers_every_required_block` --
which is exactly what that test is for, and its comment says so.
A sub-agent that "fixes" the failure by editing the assertion has
defeated the guard.

**Deliverables.**

* `PUSH_AUDIT_BLOCKS` gains `'source-file-size'`;
  `PLAN_TEMPLATE_BLOCKS` gains `'plan-phase-landing'`.
* `docs/audits/push-audit.md` names `source-file-size` in its
  bullet list of required blocks, with a clause saying what the
  block is for.
  `PushAuditTest.test_every_required_block_is_named_in_the_spec`
  requires this; it is not optional tidying.
* `docs/audits/plan-template.md` gains a `plan-phase-landing`
  bullet in the same list, after `plan-push-audit-phase`, which it
  amends. No test forces this one -- the asymmetry is itself worth
  a note in the commit message -- but a page that describes eight
  of nine required blocks is the drift these specs exist to
  prevent.
* `templates/shared-blocks/README.md`: rewrite the two
  `plan-phase-landing` paragraphs. The first says the block is
  deliberately *not* in `PLAN_TEMPLATE_BLOCKS` and explains why;
  that is now false and is replaced by the decision and its date.
  The second says "the fleet carries the second without the first
  until the sweep lands", which describes a state this phase ends.
* Two tests mirroring the existing
  `test_push_audit_phase_is_required` and
  `test_plan_templates_must_carry_the_block`: one asserting
  `'source-file-size' in PUSH_AUDIT_BLOCKS`, one asserting
  `'plan-phase-landing' in PLAN_TEMPLATE_BLOCKS`, each with a
  comment naming the fleet consequence the way
  `test_push_audit_phase_is_required` does ("the line of this
  change with the widest fleet consequence").
* `PushAuditTest.setUp` gains a `self.size_block`, its entry in the
  write loop, and its place in `self.canonical`.

**Neither block's version number is bumped.** They are v1 and stay
v1. A bump marks every repository carrying the *current* wording
non-compliant for a change to wording nobody has read; enforcement
is a change to who is measured, not to what they are measured
against. `templates/shared-blocks/README.md` already records this
reasoning for the `eb806f6` correction to `plan-phase-landing`.

**Expected verdict movement, which is the point of the phase.**
`development` stays `pass` on both `push-audit` and
`plan-template`. Every other repository's verdict is computed from
its own tree and does not move until the next daily run. Confirm
locally against clones before merging, with `--dry-run` if
`audit-manage-issues.py` is run at all:

```
python3 scripts/audit-check.py --repo-path . --repo-name development
for r in ryll kerbside divergulent; do
    python3 scripts/audit-check.py \
        --repo-path ~/reviews/shakenfist/$r --repo-name $r \
        > /tmp/results/audit-result-$r.json
done
python3 scripts/audit-manage-issues.py --results-dir /tmp/results/ --dry-run
```

The dry run should propose exactly the issues D1 predicts and no
others. If it proposes an issue against a criterion this plan did
not touch, stop: something else moved.

**Do not** regenerate `docs/audits/compliance.md`. It is produced by
the daily run against the real fleet; a locally generated page only
covers the repositories fed to it, and
`docs/consistency-audits.md` is explicit that rewriting it in place
is the failure mode to avoid. Always pass `--page /tmp/compliance.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | In `scripts/audit/checks/plans.py`, add `'source-file-size'` to `PUSH_AUDIT_BLOCKS` and `'plan-phase-landing'` to `PLAN_TEMPLATE_BLOCKS`. Then make every consequence listed in the deliverables above: the two spec pages, the two `templates/shared-blocks/README.md` paragraphs, the two new assertions, and `PushAuditTest.setUp` (which hard-codes its blocks and will fail `test_the_fixture_covers_every_required_block` otherwise -- fix the fixture, never the assertion). Do not bump either block's version marker. Do not regenerate `docs/audits/compliance.md`. Run `python3 -m unittest discover -s scripts/tests -t scripts`, then `python3 scripts/audit-check.py --repo-path . --repo-name development` and confirm `push-audit` and `plan-template` both still report `pass`. Report the before-and-after status of those two checks explicitly. |

### 3. Inventory, and the gaps in `CheckTestCase`

Two jobs, both cheap, both of which de-risk phase 4.

**The inventory, re-derived by reading every class** in
`scripts/tests/test_*.py` declared as `(unittest.TestCase)` (a
class already migrated onto `CheckTestCase` is excluded, since it
is not old-style), rather than by the substring filter the
Situation section used:

| File | Class | Lines | Candidate | Reason |
|------|-------|-------|-----------|--------|
| test_ci_workflows.py | CiReviewAutomationSpecTest | 42-80 | No | Compares the spec page's "Measured" section to constants; never runs `CiReviewAutomation` |
| test_ci_workflows.py | ExpensiveLanePathFilterTest | 81-222 | Yes | `ExpensiveLanePathFilter`, via `check_expensive_lane_path_filter()` |
| test_ci_workflows.py | WorkflowJobBlocksTest | 223-251 | No | Tests `workflow_job_blocks()` / `is_dedicated_scanner_workflow()`, pure text helpers |
| test_ci_workflows.py | RetiredCommentAddresserTest | 252-466 | Yes | `CiReviewAutomation`, via `check_ci_review_automation()` |
| test_ci_workflows.py | PrReReviewTriggerTest | 467-545 | Yes | `CiReviewAutomation`, via `check_ci_review_automation()` |
| test_ci_workflows.py | PrAutoReviewSecretsInheritTest | 546-681 | Yes | `CiReviewAutomation`, via `check_ci_review_automation()` |
| test_ci_workflows.py | MergeGroupCancellationTest | 682-1120 | Yes | `MergeGroupCancellation`, via `check_merge_group_cancellation()` |
| test_distros.py | ImageReferenceTest | 26-113 | No | Tests `image_release()`, a pure helper |
| test_distros.py | RetiredReleaseTest | 114-145 | No | Tests `retired_releases()`, a pure helper |
| test_distros.py | RunnerLabelMatchTest | 146-186 | No | Tests the `RUNNER_LABEL_RE` regex directly |
| test_distros.py | SpecificationTest | 449-488 | No | Compares `eol-distro.md` wording to `EOL_RELEASES`/`README.md`; no check run |
| test_distros.py | ExceptionMarkerTest | 489-503 | No | Tests the `EXCEPTION_RE` regex directly |
| test_distros.py | DockerfileNamingTest | 504-517 | No | Tests `is_dockerfile()`, a pure helper |
| test_distros.py | RegressionGuardTest | 518-551 | No | Asserts on `RUNNER_LABEL_RE.pattern`'s text |
| test_docs_content.py | ReadmeStructureTest | 47-102 | Yes | `ReadmeStructure`, via `check_readme_structure()` |
| test_docs_content.py | DocsExternalLinksTest | 103-305 | Yes | `DocsExternalLinks`, via `check_docs_external_links()` |
| test_docs_content.py | DiagramFormatTest | 306-573 | Yes | `DiagramFormat`, via `check_diagram_format()` |
| test_docs_content.py | MermaidLintCiTest | 574-671 | Yes | `MermaidLintCi`, via `check_mermaid_lint_ci()` |
| test_docs_content.py | IssueLinkCheckDeploymentTest | 672-707 | No | Byte-compares the deployed workflow to its template; no check run |
| test_docs_content.py | MermaidLintDeploymentTest | 708-807 | No | Byte-compares deployed script/workflow to templates; no check run |
| test_docs_content.py | MermaidLintScriptTest | 808-1862 | No | Runs `tools/mermaid-lint.sh` itself via subprocess against a stub docker; no `Check` involved |
| test_github_config.py | MergeQueueConfigTest | 23-63 | No | Tests `evaluate_merge_queue_rules()`, a helper `MergeQueueConfig` calls internally; never instantiates the check |
| test_hooks.py | HookTriggerTest | 191-225 | No | Checks `.pre-commit-config.yaml` file-patterns against each suite's own dependencies; no `checks/` module involved |
| test_llm_docs.py | LlmDocStructureTest | 48-201 | Yes | `LlmDocStructure`, via `check_llm_doc_structure()` |
| test_llm_docs.py | OrphanSkillMarkdownTest | 203-280 | Ambiguous | Four methods call `orphan_skill_markdown()` directly (a pure helper); the other three (`test_repo_without_skills_is_not_applicable`, `test_orphans_fail_the_check`, `test_missing_skillsaw_is_not_applicable`) call `check_llm_context_lint()`, exercising `LlmContextLint`'s `applies`/`skip`/`fail` wiring rather than the helper -- close to an even split, not a clean yes or no |
| test_llm_docs.py | LlmContextLintCiTest | 282-559 | Yes | `LlmContextLintCi`, via `check_llm_context_lint_ci()` |
| test_manage_issues.py | IssueBodyTest | 34-81 | No | Tests `build_issue_body()`, an issue-formatting helper; no check run |
| test_manage_issues.py | IssueBodyLimitTest | 82-118 | No | Same helper, tests its truncation behaviour |
| test_markdown.py | StripMarkdownCodeTest | 20-49 | No | Tests `strip_markdown_code()`, a pure helper |
| test_markdown.py | IterLinesOutsideFencesTest | 50-78 | No | Tests `iter_lines_outside_fences()`, a pure helper |
| test_markdown.py | MarkdownHeadingTest | 79-121 | No | Tests `markdown_heading()` / `iter_markdown_headings()`, pure helpers |
| test_markdown.py | IterMarkdownTableRowsTest | 122-193 | No | Tests `iter_markdown_table_rows()`, a pure helper |
| test_metadata.py | FrozenMetadataTest | 343-354 | No | Compares derived metadata tables to frozen literals |
| test_metadata.py | DerivationTest | 355-421 | No | Asserts `registry.CHECKS` metadata invariants (spec/id/column); never calls `.run()`/`.applies()` |
| test_metadata.py | ContractTest | 422-503 | No, ambiguous | Calls `.applies()`/`.run()` on *every* registered check to test a fleet-wide contract, not any one check's behaviour -- does not fit a single `check_class` |
| test_npm_dependencies.py | CommentStrippingTest | 726-746 | No | Tests `strip_comments()`, a pure helper |
| test_npm_dependencies.py | SpecificationTest | 747-774 | No | Tests `specifier_package()`, a pure helper |
| test_packaging.py | DependencyNameNormalizationTest | 47-150 | Yes | `DependencyNameNormalization`, via `check_dependency_name_normalization()` |
| test_packaging.py | PinIndirectDepsScopeTest | 151-245 | Yes | `PinIndirectDependencies`, via `check_pin_indirect_deps()` |
| test_packaging.py | RenovatePreCommitManagerTest | 246-339 | Yes | `Renovate`, via `check_renovate()` |
| test_packaging.py | ConsoleLoggingTest | 340-791 | Yes | `ConsoleLogging`, via `check_console_logging()` |
| test_packaging.py | HeaderSanitizationTest | 792-1184 | Yes | `HeaderSanitization`, via `check_header_sanitization()` |
| test_packaging.py | PythonVersionTargetingTest | 1185-1332 | Yes | `PythonVersionTargeting`, via `check_python_version_targeting()` |
| test_plans.py | PlanPhaseReferencesTest | 60-355 | Yes | `PlanPhaseReferences`, via `check_plan_phase_references()` |
| test_plans.py | PlanSourceReferenceTest | 356-443 | Yes | `PlanSourceReferences`, via `check_plan_source_references()` |
| test_plans.py | PlanIndexTest | 444-741 | Yes | `PlanIndex`, via `check_plan_index()` |
| test_plans.py | PlanAuditPhaseTest | 742-2212 | Yes | `PlanAuditPhase`, via `check_plan_audit_phase()` |
| test_plans.py | PushAuditTest | 2213-2558 | Yes, mixed | Most of its ~20 methods go through `_check()` -> `check_push_audit()` -> `PushAudit`; four (`test_source_file_size_is_required`, `test_every_required_block_has_a_canonical_copy`, `test_every_required_block_is_named_in_the_spec`, `test_the_fixture_covers_every_required_block`) instead assert directly against the `PUSH_AUDIT_BLOCKS` constant and canonical block files, with no check run -- those four would need to travel separately at migration time |
| test_plans.py | PlanTemplateTest | 2559-2662 | Ambiguous | Four of its eight methods go through `_check()` -> `check_plan_template()` -> `PlanTemplate`; the other four (`test_push_audit_phase_is_required`, `test_phase_landing_is_required`, `test_every_required_block_is_named_in_the_spec`, `test_every_required_block_has_a_canonical_copy`) assert directly against `PLAN_TEMPLATE_BLOCKS` and canonical block files, with no check run -- an even split, closer to non-candidate than `PushAuditTest` |
| test_plans.py | CanonicalSharedBlocksTest | 2663-2682 | No | Confirms every `templates/shared-blocks/*.md` file parses as its own named block; no check run |
| test_plans.py | PlanStatusVocabularyBlockTest | 2683-2717 | No | Compares the `plan-status-vocabulary` canonical block to `PLAN_STATUSES`/`PLAN_TEMPLATE_BLOCKS` |
| test_plans.py | PushAuditPhaseBlockTest | 2718-2773 | No | Compares the `plan-push-audit-phase` canonical block's wording to named rule phrases |
| test_python_source.py | CanonicalNameTest | 20-28 | No | Tests `canonical_dependency_name()`, a plain function, not a check |
| test_python_source.py | MaskCommentsAndStringsTest | 29-69 | No | Tests `mask_comments_and_strings()`, a pure helper |
| test_python_source.py | MaskStringsTest | 70-101 | No | Tests `mask_strings()`, a pure helper |
| test_registry.py | AuditScopeIsStatedOnceTest | 36-329 | No | Tests `audit.scope`'s doc/matrix parsing helpers; no check |
| test_registry.py | RepoOverridesTest | 330-401 | No | Tests `detect_repo_properties()`; no check |
| test_registry.py | CheckScopeTest | 402-482 | No, ambiguous | Calls `registry.run_all()` (every registered check) to test the `only_checks` scoping mechanism itself, not one check's behaviour |
| test_registry.py | GitHooksDisabledTest | 483-598 | No | Greps this repository's own workflow files for a security control; no `checks/` module for it |
| test_registry.py | MergeRefResolutionTest | 599-703 | No | Greps this repository's own workflow files for the resolve-ref pattern; no `checks/` module for it |
| test_repo.py | RepoReadTest | 29-104 | No | Tests `Repo.read()` directly, not any check |
| test_review.py | ReviewMarksPreCommitTest | 45-147 | Yes | `ReviewMarksPreCommit`, via `check_review_marks_pre_commit()` |
| test_review.py | ReviewCoverageTest | 149-242 | Yes | `ReviewCoverage`, via `check_review_coverage()` |
| test_review.py | ReviewScopeCompletenessTest | 244-330 | Yes | `ReviewScopeCompleteness`, via `check_review_scope_completeness()` |
| test_review.py | SfuiVendorTest | 332-455 | Yes | `SfuiVendor`, via `check_sfui_vendor()` |
| test_runners.py | RunnerLabelParsingTest | 36-79 | No | Tests `parse_runner_labels()`/`literal_runner_labels()`, pure helpers |
| test_runners.py | VmRunnerSizeTest | 81-268 | Yes, mostly | `VmRunnerSize`, via `check_vm_runner_size()`; one method compares the spec's size list to `VM_SIZE_LABELS` instead |
| test_runners.py | SelfHostedRunnerLabelPositionTest | 271-376 | Yes | `SelfHostedRunners`, via `check_self_hosted_runners()` |

`EolDistroTest` and `DateGateTest` in `test_distros.py`,
`GithubConfigTestCase` and its subclasses in
`test_github_config.py`, and `ReadmeAbsoluteLinksTest` /
`LlmToolingTest` in `test_docs_content.py` already subclass
`CheckTestCase` and are excluded from the table above as
already migrated, not as non-candidates.

Five classes are marked ambiguous or mixed rather than forced to
a clean yes or no. `ContractTest` (`test_metadata.py`) and
`CheckScopeTest` (`test_registry.py`) both call
`.applies()`/`.run()`, but on every registered check at once, to
test a fleet-wide contract or the scoping mechanism itself --
neither fits a single `check_class`, so both are counted as
non-candidates here. `OrphanSkillMarkdownTest`
(`test_llm_docs.py`) and `PlanTemplateTest` (`test_plans.py`)
split close to evenly between methods that call a pure helper or
assert against a constant and methods that run a real check
(`LlmContextLint`, `PlanTemplate` respectively) -- both are
counted as candidates here on the strength of the check-running
methods, but a migration will need to decide where the other
half of each class's methods land. `PushAuditTest`
(`test_plans.py`) has the same split but lopsided the other way
(four non-check methods out of roughly twenty), so it is a
cleaner candidate. `VmRunnerSizeTest` (`test_runners.py`) is
mostly a candidate with one spec-comparison method that runs no
check. `test_plans.py` was being edited concurrently by phase 2 while
this table was produced, growing from 2,754 to 2,791 lines; the
line numbers above are what was actually read, not a fixed
count. Its six candidate classes span 2,618 of those lines.

**The result:** 30 candidates across 68 old-style classes,
totalling 6,765 lines -- against the Situation section's
substring-filter figure of 36 classes / ~7,200 lines. The class
count was overstated by about a sixth; the line count, dominated
by a few very large classes (`PlanAuditPhaseTest` alone is 1,471
lines), was close to right. 38 classes are correctly not
candidates, each for a stated reason above: most test a pure
helper (markdown, python-source, npm-dependency, runner-label
parsing), some compare a specification page or canonical
shared-block to a table or constant, and a few read this
repository's own files directly for a security or template
control that has no `Check` subclass at all. Three of the four
files phase 4 groups as "small candidate sets, one commit each"
(`test_manage_issues.py`, `test_registry.py`, `test_metadata.py`)
in fact have zero candidates each -- only `test_runners.py`
(two candidates) has anything to migrate from that group; worth
weighing when phase 4 is briefed.

**Then find what `CheckTestCase` cannot yet do**, by reading the
old-style classes rather than by guessing. From a first pass, the
base class in `scripts/tests/base.py` already covers more than it
looks: `check(**props)` supplies repository properties including
`is_docs_only` and `has_workflows_dir`, `check(name=, org=)`
supplies the repository identity that `MergeGroupCancellationTest`
passes by hand, `FixtureRepo.workflow()` covers the per-class
`_repo` helpers, and `assert_pass` / `assert_fail` / `assert_skip`
cover the assertion triples. Two gaps look real:

* several `_repo` helpers write a dict of workflows in a loop, so
  a `workflows(mapping)` bulk helper on `FixtureRepo` would absorb
  them; and
* the spec-agreement tests (`CiReviewAutomationSpecTest._measured`
  and its siblings) each re-implement "read a page under
  `docs/audits/` and slice one `###` section out of it", which is
  a `REPO_ROOT`-relative helper belonging beside `repo_file()`.

**Extend the base class only where a migration in phase 4 actually
demands it.** A speculative helper added here and used twice is
this plan reproducing the problem it is fixing. If the inventory
shows a gap is real in one class only, leave it in that class.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | For every class in `scripts/tests/test_*.py` declared as `(unittest.TestCase)`, determine whether it instantiates a `Check` subclass from `scripts/audit/checks/` (directly, via a module-level `check_*` wrapper, or via `run_check` from `tests/base.py`). Produce a markdown table with columns File, Class, Lines, Candidate (yes/no), Reason. Do not change any code. Write the table into `docs/plans/PLAN-review-unit-size.md` under the phase 3 section, replacing the paragraph that says the inventory is a heuristic. |
| 3b | high | opus | none | Using the phase 3a table, read every candidate class and list precisely what `scripts/tests/base.py` does not yet provide. Then implement only those additions to `FixtureRepo` / `CheckTestCase`, with a docstring on each saying which classes needed it, matching the existing style in that file (single quotes, double-quoted docstrings, 120 columns). Add tests for new helpers to the appropriate suite. Do not migrate any class yet. Do not add a helper used by fewer than two classes. Run `python3 -m unittest discover -s scripts/tests -t scripts`. |

**Verification.** The test count before and after phase 3 is
identical (`python3 -m unittest discover -s scripts/tests -t
scripts -v 2>&1 | tail -3`), since nothing has been migrated yet.

### 4. Migrate the check tests, one file per commit

The bulk of the work, and the phase where coverage can go missing
quietly. One commit per test file, in ascending order of risk, so
that a bad commit is bisectable to one file.

**The order**, easiest first, which is also most-precedent-first --
the early files already contain migrated siblings to copy the shape
from:

The grouping below was rewritten against the phase 3 inventory,
which found that three of the four files this phase originally put
in its first group -- `test_manage_issues.py`, `test_registry.py`
and `test_metadata.py` -- have no candidates at all. Ten of the
seventeen test files turn out to have nothing to migrate. Naming
them here anyway, as files deliberately not touched, is what stops
the next reader reading their absence as an oversight.

1. `test_runners.py` -- two candidates over ~297 lines
   (`VmRunnerSizeTest`, `SelfHostedRunnerLabelPositionTest`), with
   one migrated sibling in the file. The smallest real migration,
   so it goes first and sets the shape cheaply.
2. `test_review.py`, `test_llm_docs.py` -- four and three
   candidates, no migrated siblings, still small enough to read
   whole.
3. `test_ci_workflows.py` -- five candidates over ~1,011 lines,
   with six already-migrated classes in the same file as the
   pattern to follow. This is the reference migration for the
   larger files; let it set the shape before the two biggest.
4. `test_packaging.py` -- six candidates over ~1,286 lines, eight
   migrated siblings.
5. `test_docs_content.py` -- four candidates over ~625 lines, two
   migrated siblings.
6. `test_plans.py` -- six candidates over ~2,618 lines, no migrated
   siblings. Last, alone, and with the most care.

Not touched, because they have no class that runs a `Check`:
`test_distros.py`, `test_github_config.py`, `test_hooks.py`,
`test_manage_issues.py`, `test_markdown.py`, `test_metadata.py`,
`test_npm_dependencies.py`, `test_python_source.py`,
`test_registry.py`, `test_repo.py`. Their old-style classes test
pure helpers, regexes, spec-page wording or the registry contract,
and have nothing to inherit.

**The invariant that governs every commit: no assertion changes.**
A migration moves a class onto `CheckTestCase`, deletes its private
`_repo` / `_check` / temporary-directory scaffolding, and rewrites
its calls to use `self.fixture`, `self.check()` and the
`assert_pass` / `assert_fail` / `assert_skip` triple. The assertion
*subjects* stay identical. Where a migration appears to require
changing what a test asserts, that is either a latent bug or a real
difference between the private helper and `run_check` -- stop,
and say which in the commit message, rather than adjusting the
assertion to fit.

**Rationale comments move with their tests, verbatim.** They are
the most valuable lines in these files and phase 1 has just
written down that they are not what gets cut.

**Per-commit verification**, all three, every time:

```
python3 -m unittest discover -s scripts/tests -t scripts -v 2>&1 | tail -3
python3 scripts/audit-check.py --repo-path . --repo-name development
pre-commit run --all-files
```

The test count must not fall. Record the before and after count in
each commit message; a migration that silently drops a test is the
one failure mode this phase cannot detect any other way, because
the deleted code and the deleted test look alike in a diff.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | worktree | Migrate the phase 3a candidates in `scripts/tests/test_ci_workflows.py` to `CheckTestCase`. Read `scripts/tests/base.py` and the already-migrated classes in the same file (`WorkflowPermissionsTest` at line 1121 onwards) first; they are the target shape. For each candidate: set `check_class`, delete the private `_repo`/`_check`/`_job` helpers and `tempfile.TemporaryDirectory()` sites in favour of `self.fixture` and `self.check(**props)`, and replace `self.assertEqual(result['status'], ...)` with `assert_pass`/`assert_fail`/`assert_skip`. Keep every rationale comment verbatim and keep every assertion subject identical. `MergeGroupCancellationTest` monkeypatches `ci_workflows.merge_queue_is_serial` in `setUp`; keep that patch in the class, using `addCleanup` rather than `tearDown`. Report the test count before and after -- it must be unchanged. |
| 4b | high | opus | worktree | The same migration for `test_runners.py` (2 candidates), `test_review.py` (4) and `test_llm_docs.py` (3), following the shape 4a established. One commit per file. `test_manage_issues.py`, `test_registry.py` and `test_metadata.py` are NOT in this step -- the phase 3 inventory found they have no candidates. Same invariants: no assertion subject changes, comments verbatim, test count recorded per commit. |
| 4c | high | opus | worktree | The same migration for `test_packaging.py` and `test_docs_content.py`. One commit each. |
| 4d | xhigh | opus | worktree | The same migration for `test_plans.py`: six candidate classes over roughly 2,618 lines with no migrated sibling in the file to copy from. Read the whole file before changing any of it. This is the largest single migration in the plan and the one where a dropped test is least likely to be noticed, so record the per-class test count, not just the file total. |

### 5. Retire the helpers and record the convention

Close out phase 4 and make the convention discoverable, so the
migration does not have to be done a third time.

**Results.**

The helper census, re-run over `scripts/tests/`:

| Metric | At plan start | Now |
|---|---|---|
| `TemporaryDirectory()` sites | 112 | 21 |
| `def _repo` helpers | 10 | 2 |
| `def _check` helpers | 20 | 24 |
| Old-style `(unittest.TestCase)` classes | 68 | 44 |
| `CheckTestCase` classes | 25 | 58 |

The `Now` column is the merged tree, not the branch before the
merge, because that is the tree the pull request asks anyone to
read. The two class counts are the ones this matters to: `main`
moved 34 commits while this plan ran and added five old-style
classes -- `DefuseTest` (`test_audit_common.py`), `DetailsLimitTest`
and `ItemDefusingTest` (`test_manage_issues.py`),
`MarkdownTableCellsTest` (`test_markdown.py`) and
`PushAuditRunbookRangeTest` (`test_plans.py`). None of the five runs
a `Check`; each tests a pure helper, so each is a non-candidate by
the phase 3a rule rather than a miss, and the 39 that phase 4 left
plus those five is the 44 above. The `CheckTestCase` count is 58,
which is what phase 5 recorded. An earlier revision of this
paragraph said 59 and called 58 an undercount; that was wrong. The
59th is `class Case(CheckTestCase)`, declared inside one of
`TempdirTest`'s methods to observe cleanup from outside, and an
`ast.walk` counts it because `walk` descends into nested
definitions. Only classes declared at module level are suite
classes. Three counts of this number have now been taken by three
different methods and the disagreements were all in the method, not
the tree, which is its own argument for `test_metadata.py`-style
contract tests over census paragraphs.

Two of those numbers need explaining rather than presenting bare.

`def _check` went *up*, 20 to 24. That is the shape decision
recorded in commit `39ccfcf`: each migrated class keeps one thin
helper holding the class's repository shape -- what a compliant
fixture looks like for that criterion -- while the scaffolding
underneath it (the tempdir, the `makedirs`, the write loop, the
module-level `check_*` wrapper) is what got deleted. The count of
`_check` helpers was never the right measure of this migration; the
`TemporaryDirectory()` count is, and it fell by four fifths, 112 to
21.

The 21 remaining `TemporaryDirectory()` sites break down as
follows, counted per class rather than asserted:

| Where | Sites | Why it stays |
|---|---:|---|
| `CheckTestCase.setUp` / `.tempdir()` in `base.py` | 2 | The machinery itself, not a survivor of it |
| `AuditScopeIsStatedOnceTest`, `CheckScopeTest` (`test_registry.py`) | 7 | Non-candidates: the registry contract and audit scope, no `Check` to run |
| `MermaidLintScriptTest` (`test_docs_content.py`) | 3 | Non-candidate: drives the deployed script directly |
| `RepoReadTest` (`test_repo.py`) | 2 | Non-candidate: tests `Repo.read` itself |
| `ContractTest` (`test_metadata.py`) | 1 | Non-candidate: a fleet-wide contract over every registered check |
| `OrphanSkillMarkdownTest` (`test_llm_docs.py`) | 4 | Migrated, but four of its seven methods call a pure helper against a plain directory (see `796ce96`) |
| `FuzzNightlyReportingTest` (`test_ci_workflows.py`) | 2 | Already on `CheckTestCase` before this plan, and still builds its own directories |

So six of the 21 sit inside `CheckTestCase` subclasses rather than in
old-style classes, which is worth saying because the tempting summary
-- "what is left is all in the classes we did not migrate" -- is not
true. Four of those six are deliberate and documented;
`FuzzNightlyReportingTest` is neither, and is the one place a later
pass could still take something out. It is recorded under Future work.

None of this is a gap in `CheckTestCase` for the work this plan
scoped: every class the phase 3 inventory named as a migration
candidate has been migrated, and no candidate was left with
hand-rolled scaffolding.

The five candidate-bearing files from the Situation table, plus
`test_runners.py` and `test_review.py`, which phase 4 migrated
first:

| File | Before | After phase 4 | On the merged tree |
|---|---|---|---|
| `test_ci_workflows.py` | 2210 | 2124 | 2130 |
| `test_packaging.py` | 2660 | 2531 | 2531 |
| `test_plans.py` | 2754 | 2719 | 2871 |
| `test_docs_content.py` | 1916 | 1892 | 1893 |
| `test_runners.py` | 414 | 366 | 366 |
| `test_review.py` | 457 | 363 | 427 |
| `test_llm_docs.py` | 562 | 496 | 496 |

The third column is the honest one to quote at anyone asking what
this did to the files, and it is worse than the second: `main` added
152 lines to `test_plans.py` and 64 to `test_review.py` while the
migration was running, so the largest file in the set finishes 117
lines *longer* than it started despite being migrated. The
migration's own effect is the second column. Both are true, and
reporting only the second would be exactly the kind of number this
plan was written to distrust.

The big files barely moved: `test_plans.py` fell 35 lines and
`test_packaging.py` 129, against a Situation section that framed
this work as reducing re-review cost. Said plainly, per the phase 1
guideline: the migration removed duplicated scaffolding, not bulk.
These files stay long because they carry many test cases and their
rationale comments, which the `source-file-size` block this plan
wrote explicitly says must not be cut to hit a number. A real
reduction for `test_plans.py` and `test_packaging.py` would mean
splitting them along the seams already named in this plan's Future
work section, which this plan deliberately did not do.

39 old-style classes remain. 38 are the non-candidates the phase 3
inventory named -- they test pure helpers, regexes, spec-page
wording or the registry contract and have no `Check` to inherit.
The 39th is `RepoTextTest`, added in commit `e08d667` (phase 3b) to
test the new `repo_text()` helper, and correctly left as a plain
`unittest.TestCase` for the same reason: it is not testing a
`Check`. Every candidate the phase 3 inventory identified -- all 30
of them, across `test_ci_workflows.py`, `test_docs_content.py`,
`test_llm_docs.py`, `test_packaging.py`, `test_plans.py`,
`test_review.py` and `test_runners.py` -- has been migrated onto
`CheckTestCase`.

The convention paragraph is in `docs/consistency-audits.md`, under
*Testing a change*, with its one-line pointer in `AGENTS.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Re-run the two censuses named above and write the results into the phase 5 section of `docs/plans/PLAN-review-unit-size.md`, including a per-survivor note for any remaining `_repo`/`_check` helper. Do not change any code under `scripts/`. |
| 5b | medium | sonnet | none | Add the `CheckTestCase` convention paragraph to `docs/consistency-audits.md` under *Testing a change*, and a single pointer line to `AGENTS.md`. Read `templates/shared-blocks/llm-doc-discipline.md` first: `AGENTS.md` gets a pointer, never the content. Do not touch `ARCHITECTURE.md` -- the shape of the system has not changed. |

### 6. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1 to 5,
before the pull request merges, reading every `main...HEAD` in the
runbook as `origin/main...HEAD` after a fetch.

Three things in the runbook bear directly on this diff and should
be given extra weight:

* **The shared-blocks wave.** `PUSH-AUDIT.md` already asks, at its
  line 78, for `git diff main...HEAD -- 'templates/shared-blocks/*.md'`
  and for confirmation that any version marker change was
  deliberate. Phase 1 adds a file rather than changing one; the
  check that matters here is that the embedded copy in
  `PUSH-AUDIT.md` is byte-identical to the canonical file, which is
  what every other repository will be measured against if the block
  is ever enforced.
* **The `comment-proportion` block, applied to this plan's own
  output.** Phase 4 moves a great many rationale comments. The
  audit should sample them and confirm they arrived intact rather
  than paraphrased.
* **Templates are shipped code.** The new block is copied into ten
  repositories if it is ever enforced, so it is judged as their
  text: no reference to paths that exist only here, and nothing
  that assumes this repository's tooling.


**Result: no blocking findings.** Wave 1 was mechanically clean and
all four wave 2 sections reported nothing blocking, which is
recorded here rather than left as an absence. The security section
found no vulnerabilities at any severity: it traced the path from
the two new list entries to an issue body and established that the
only repository-controlled value reaching a `details` string is the
block name, captured by `[a-z0-9-]+`, so no backtick, angle
bracket, newline, `@` or `#` can pass into ten other repositories'
issue trackers.

Three of the plan's own claims were checked mechanically rather
than accepted. The embedded copy of `source-file-size` in
`PUSH-AUDIT.md` is byte-identical to the canonical file by
`sha256`, and so are all thirty other block embeddings in the tree.
The fleet-impact numbers this plan states in four places -- 8 of 10
on `plan-template`, 7 of 11 on `push-audit` -- reconcile exactly
against `docs/audits/compliance.md`. And the coverage claim was
settled by diffing the test-identity list rather than the count:
1,093 to 1,108, with zero identities disappearing and fifteen
added.

**Findings, all advisory.** Two were fixed in this commit because
they are errors in this plan file: the `def _repo` count said 3
where it is 2 (the census grep also matched `def _reporter`), and
the Future work bullet omitted `scripts/audit/checks/plans.py`,
which is over the threshold the block this plan wrote sets and is
the one file under `scripts/audit/` this plan edits.

The rest were recorded for a decision rather than fixed in the
audit commit, since `plan-phase-landing` lands findings as their
own pull request. All four were then settled during the automated
review rounds on the pull request -- two taken, two declined in
writing -- which is why this phase can close itself out here rather
than waiting on a follow-up:

* `scripts/tests/base.py` -- `write()` and `write_all()` do a bare
  `os.path.join(self.path, relative)`. Every call site passes a
  string literal from a test module so no escape is reachable, but
  this repository's own `path-traversal-review` block asks that a
  join which is correct by construction say so. **Taken** in the
  review round rather than deferred: the automated review raised it
  independently, and it is a comment, so it costs nothing to
  answer where it was asked.
* `scripts/tests/base.py` -- the `repo_text` and `write_all`
  docstrings enumerate ten and three affected class names, which
  will rot. Keep the why, trim the lists. **Taken** in the second
  review round, together with the same list in `workflows()` that
  the audit had missed. Each now says the count and points at the
  grep that is current instead of naming the classes.
* `scripts/audit-manage-issues.py` -- `build_issue_body()` runs
  only where no open issue exists, so a repository already holding
  an open `push-audit` issue never sees the new requirement in its
  body. Pre-existing, but this plan is what makes it bite: it
  changes the required-block set for fifteen check/repository pairs
  in one morning. Four issues are open against the two criteria
  today and will each carry a body that names every missing block
  except the one this plan adds: sfui#15,
  uncalibrated-sextant#11 and client-python-k3s#46 on
  `push-audit`, uncalibrated-sextant#12 on `plan-template`. Whoever fixes those three from the issue text
  alone will re-run and still fail, with nothing telling them why.
  Recorded under Future work as three manual body edits, which is
  the cheap half; the durable half is refreshing a changed body on
  an already-open issue, and that is a separate pull request
  against code this plan does not touch. **Declined here** on that
  ground, and the review agreed with the split.
* Repository-wide, out of scope and not introduced here: there are
  no type annotations on any of the 1,796 `def`s under `scripts/`,
  while the `python-version-discipline` block this repository ships
  requires them of ten others. **Declined here.** Annotating 1,796
  definitions is its own plan, and doing a tenth of it in a
  migration's pull request would leave the repository in the state
  that is hardest to reason about: annotated enough to look
  checked, not enough to be.

**What gates the push is not a finding.** `origin/main` moved 34
commits during this plan and `git merge origin/main` conflicts in
two files. `docs/plans/index.md` is trivial. `scripts/tests/test_plans.py`
is not: `main` added a symlink-escape fixture to the
`plan_audit_phase` helper, with parameters that write *above* the
repository root and symlink into `docs/plans/` to prove a symlinked
plan out of tree is not read -- and this plan migrated that same
helper onto `write_all()`, which joins under `self.path` and cannot
express either. That resolution is real work, not an ours/theirs
pick, and `main` also added five classes the phase 3 inventory does
not know about -- see the phase 5 results, which are re-derived over
the merged tree for that reason.

The third review round widened the block's second bullet so its
thresholds stand where a repository does not track review per
file, and left the version at v1 rather than bumping it: no
repository embeds the block yet, so there is no old copy for a
bump to mark stale, and bumping text that has never shipped would
file issues about a version nobody carries. A bump becomes
mandatory the moment this merges.

Findings land as their own pull request against `main`, and the
plan is not complete until they are resolved or declined in
writing. Each of the four above now carries that decision, so the
condition is met without a follow-up pull request -- which is the
outcome `plan-phase-landing` is for. Per that block this row is
the one permitted to omit a `Merged` cell, and it sets its own
`Status`, and the plan's index row, to `Complete` in this pull
request.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | Run `PUSH-AUDIT.md` end to end against `origin/main...HEAD` after `git fetch origin`. Follow the runbook's own wave structure and its per-section model and effort settings. Report findings as a bullet list: file, line, blocking or advisory. Do not fix anything -- findings are triaged in the management session. |

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

**In this repository, for this plan.** Phase 4's steps are marked
`worktree` because a migration that loses a test is easier to
discard than to unpick, and because the management session's review
of each one is a test-count comparison plus a read of the diff --
both of which work fine against a worktree. Phases 1, 3 and 5 are
small and legible enough to run in the main tree. Phase 2 runs in
the main tree too, but it is the one step in this plan the
management session should read line by line rather than trust: it
is what files issues in nine repositories.

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

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

**For this plan.** Phase 2 changes who a criterion is measured
against, which is the high-effort bar in this repository even
though the diff is two list entries -- the fleet consequence is the
work, not the code. Phases 3 to 5 touch only test-side code, and
the migrations are high because the risk there is silent coverage
loss rather than design.

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

**In this repository.** A worked brief: instead of "add a check
that plans are indexed", write "add a `PlanIndex` class to
`scripts/audit/checks/plans.py` declaring the id `plan-index`, its
`spec` and its `issue_title` as class attributes and implementing
`run(repo)`, register the instance in `CHECKS` in
`scripts/audit/registry.py`, write `docs/audits/plan-index.md`
following the structure in `docs/audits/README.md` and linking to
`compliance.md#plan-index`, add the file to
`docs/audits/README.md`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable."

A check that does not apply reports
`not_applicable` with a reason rather than being omitted, because
an omitted check renders as `unknown`.

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

**In this repository.** The project-specific checks referred to
above are:

- [ ] `pre-commit run --all-files` passes. It runs actionlint,
      shellcheck, flake8, skillsaw and all five test suites, and
      `ci.yml` runs the same command on every pull request.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the
      change, or the plan says why the verdict moved.
- [ ] If the change touches issue filing, it was exercised with
      `--dry-run` only.

**For this plan**, one more, and it is the one that matters most:

- [ ] The test count from `python3 -m unittest discover -s
      scripts/tests -t scripts -v 2>&1 | tail -3` has not fallen,
      at every commit of phase 4.

`test_plans.py` is both a file this plan migrates and the file
holding the tests for the shared-block machinery phase 1 uses. A
phase 4 commit that breaks it will look like a phase 1 regression.
Bisect before diagnosing.

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

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `pre-commit run --all-files` passes.
* `scripts/audit-check.py` run against this repository reports no
  new failures, or the plan states which verdicts moved and why.
* Any new or changed criterion has all four of its parts in step:
  the check function and its registration, the entries in
  `AUDIT_METADATA` and `ISSUE_TITLES`, the specification under
  `docs/audits/`, and its line in `docs/audits/README.md` -- plus
  a column heading in `audit-update-docs.py` where it shares a
  spec page. *(This plan adds no criterion -- it adds a required
  block to two existing ones. If a phase finds itself editing
  `scripts/audit/registry.py`, it has left scope.)*
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Any entry added to `REPO_OVERRIDES` carries a stated reason.
* Anything under `templates/` is judged as the code it will
  become in ten other repositories: placeholders consistent, no
  reference to paths that only exist here, and the README beside
  it saying what to substitute.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.
* Documentation in `docs/` describes any user-visible change.
  `AGENTS.md` changes only if a convention changed;
  `ARCHITECTURE.md` only if the shape of the system changed;
  `README.md` only if the pitch, the install story or the
  documentation links changed.

And specific to this plan:

* `templates/shared-blocks/source-file-size.md` exists and its
  embedded copy in `PUSH-AUDIT.md` is byte-identical to it.
* `PUSH_AUDIT_BLOCKS` contains `source-file-size` and
  `PLAN_TEMPLATE_BLOCKS` contains `plan-phase-landing`; both spec
  pages name their new block; `templates/shared-blocks/README.md`
  no longer says either is unenforced; and neither block's version
  marker was bumped.
* `python3 scripts/audit-check.py --repo-path . --repo-name
  development` reports `pass` for both `push-audit` and
  `plan-template` at every commit on the branch -- this repository
  is inside its own audit matrix and does not get to fail a rule it
  is imposing on nine others.
* A `--dry-run` of `audit-manage-issues.py` over local clones
  proposes the issues D1 predicts and no others.
* Every old-style class in `scripts/tests/` that instantiates a
  `Check` either subclasses `CheckTestCase` or is named in this
  plan with the reason it does not.
* The suite's test count is greater than or equal to the count at
  `5861c0a`, and no `assertEqual`/`assertIn` subject was changed
  by a migration commit.
* `tools/audit-snapshot.sh --diff` against a baseline taken at
  `5861c0a` is empty. Nothing under `scripts/audit/` changed
  behaviour, so nothing the fleet is measured by moved.
* The five files in the Situation table have their new line counts
  recorded in phase 4, whatever those turn out to be.

### Documentation index maintenance

When creating a new master plan from this template, add one row to
the table in `docs/plans/index.md`: the date the plan was written,
a link to it, a one-line intent, and its status from the
vocabulary above. Rows run oldest first. One row per master plan,
never one per phase -- the phases are tracked in the plan's own
Execution table, and duplicating them in the index is how the two
drift apart.

There is no phase-arithmetic column and no `order.yml` here; both
belong to repositories whose documentation is published through a
generated navigation. The `plan-index` criterion checks the
columns this index actually has.

The index row carries the whole-plan status, so it only reaches
`Complete` once every phase has been completed, abandoned or
superseded. Update it as the plan progresses, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

**Future work, as of writing.**

* **The rollout backlog.** D2 leaves fifteen issues open across
  nine repositories, each closed by a verbatim block copy. The
  `standards-alignment` skill covers the shape. Worth watching
  whether they close in days or sit for months: that answer is the
  evidence for or against enforcing the next block on its first
  day, and nothing else this plan does will produce it.
* **Four issue bodies that will not update themselves.** sfui#15,
  uncalibrated-sextant#11, uncalibrated-sextant#12 and
  client-python-k3s#46 are open against the two criteria this plan
  changes, and
  `audit-manage-issues.py` only builds a body when no issue is
  open -- so after enforcement each will list every missing block
  except the newly required one. Edit those four bodies by hand
  once enforcement is live, or fix the refresh; the finding is
  recorded in the phase 6 audit above. The list was three until the
  third review round found the fourth, which is the argument for
  deriving it from `docs/audits/compliance.md` on the day rather
  than from a list written in advance.
* **Split the files the guideline now names.** The seams are
  already visible and are the same in each case -- a module that
  is a bag of independent checks. `scripts/audit/checks/ci_workflows.py`
  (1,863 lines, ~10 independent checks) and
  `scripts/audit/checks/packaging.py` (2,067) would each become a
  package with one module per check and one test file per check,
  which also makes the re-review cost proportional to what
  changed. `scripts/audit/checks/plans.py` belongs on that list
  too, and its omission was caught by the push audit rather than
  by the author: it is 1,508 lines here and 1,605 on `main`, over
  the 1,500 the block this plan wrote says wants a stated reason
  to stay whole -- and it is the one file under `scripts/audit/`
  this plan edits. So are the two largest test files,
  `test_plans.py` (2,719) and `test_docs_content.py` (1,892),
  which the "one test file per check" phrasing only covers by
  implication. Naming them is the block applied to its own
  author. `python3 -m unittest discover -s scripts/tests -t
  scripts` already discovers new files, so no pre-commit or CI
  configuration moves.
* **`FuzzNightlyReportingTest`.** It is already on `CheckTestCase`
  and still builds two of its own `TemporaryDirectory()` sites, which
  is the last unexplained scaffolding in a migrated class. Phase 5
  counted it rather than fixing it, because it predates this plan and
  changing it is not a migration. One class, probably one commit.
* **The non-check suites.** `scripts/test_review_tracking.py` (879
  lines) and `scripts/test_audit_update_docs.py` (796) test CLIs
  rather than checks, so they have no `CheckTestCase` to inherit
  and no shared fixture at all. Whether they want their own base
  class is a separate question this plan does not answer.
* **A measurement, not a rule.** If the guideline turns out to be
  worth enforcing mechanically, the thing to measure is not raw
  line count -- it is `size x churn` over a window, which is the
  quantity the Situation section actually computes and the one
  that would rank the fleet's files usefully. That would be a new
  criterion rather than a shared block, and it needs a story for
  generated and vendored trees first.

**Bugs fixed during this work.** Nothing yet. The open
consistency-audit issue against this repository at the time of
writing is shakenfist/development#147 (human review coverage),
which this plan will make worse before the next review session
makes it better: the migration stales a large number of marks in
`scripts/tests/`. That is expected, it is what `prune-reviews` and
the `review-coverage` audit are for, and it is not a reason to
stamp from this branch.
