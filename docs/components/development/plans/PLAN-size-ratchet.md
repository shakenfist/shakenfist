# Plan: a ratchet on source size

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every week
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The weekly
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  on the next run, so a plan should say how many repositories --
  including this one -- it newly fails.

Every number in the Situation section below was derived on
2026-10-10 from the default branches of the local clones under
`~/src/shakenfist/`, and is re-derivable with the commands given
beside it. The fleet moves; re-derive before relying on any of
them, and say so in the phase if a figure has shifted enough to
change a decision.

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

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

`source-file-size` has been enforced in every `PUSH-AUDIT.md` since
2026-09-20 (`PLAN-review-unit-size.md`, phases 1 and 2). It tells a
reviewer that a source file over roughly 800 lines is a candidate
to split and one over roughly 1,500 wants a stated reason to stay
whole, and it is deliberately advisory: "Neither is a gate, there
is no hard cap". Three weeks of evidence say the wording is right
and the mechanism does not hold.

**The largest files have kept growing since the block landed.**
Line counts at the last default-branch commit before 2026-09-20
against the default branch today:

| File | Before the block | Today | Landings since |
|---|---:|---:|---:|
| `shakenfist/mariadb.py` | 28,755 | 29,192 | 9 |
| `instar/src/vmm/src/main.rs` | 20,381 | 20,971 | 2 |
| `ryll/ryll/src/app.rs` | 6,131 | 6,426 | 6 |
| `ryll/ryll/src/bugreport.rs` | 4,211 | 4,275 | 3 |
| `development/scripts/tests/test_ci_workflows.py` | 2,124 | 2,683 | -- |
| `development/scripts/tests/test_plans.py` | 2,791 | 2,956 | -- |

The last two are this repository, which wrote the block. Derive
with `git rev-list -1 --first-parent --before=2026-09-20 <branch>`
and `git show <sha>:<path> | wc -l`.

**The growth is slow, which is why a per-diff review cannot see
it.** From 2026-06-01 to today, counting first-parent landings on
each default branch (`git log --first-parent -m --numstat
--since=2026-06-01 -- <path>`):

| File | Landings | Grew | Shrank | Median growth | Net |
|---|---:|---:|---:|---:|---:|
| `shakenfist/mariadb.py` | 74 | 73 | 1 | +36 | +9,107 |
| `ryll/src/app.rs` | 15 | 11 | 4 | +4 | +795 |
| `instar/src/vmm/src/main.rs` | 19 | 19 | 0 | +235 | +11,316 |

A push audit is scoped to one branch's diff against the default
branch. No branch in the first two rows made its file large; each
added a few dozen lines to a file that was already past every
threshold, and "this file was already huge" is not a finding
against the branch in front of the reviewer. The block is phrased
about files and the audit is phrased about diffs, and the gap
between them is exactly where slow growth lives. instar's row is
the other shape -- fewer, larger landings -- and is a reminder that
whatever closes the gap must catch a single 500-line jump as well
as fifty 10-line ones.

**The fleet distribution.** Tracked source (`*.py`, `*.rs`, `*.ts`,
`*.tsx`, `*.js`, `*.go`, `*.sh`, `*.c`, `*.h`, `*.css`) in each
primary clone under `~/src/shakenfist/`, via `git ls-files` so that
`target/`, `.cargo-cache/` and virtualenvs are never counted, with
ryll's vendored `sfui/` tree excluded:

| Repository | Files | > 800 | > 1,500 | > 2,000 | Largest |
|---|---:|---:|---:|---:|---|
| shakenfist | 597 | 75 | 28 | 18 | `shakenfist/mariadb.py` (29,192) |
| instar | 340 | 67 | 29 | 18 | `src/vmm/src/main.rs` (20,165 locally) |
| ryll | 149 | 35 | 12 | 7 | `ryll/src/app.rs` (6,426) |
| development | 72 | 14 | 8 | 6 | `scripts/tests/test_plans.py` (2,956) |
| divergulent | 122 | 12 | 2 | 0 | `divergulent/classify/review.py` (1,621) |
| kerbside | 153 | 6 | 0 | 0 | `kerbside/tests/unit/test_main.py` (1,132) |
| private-ci | 21 | 3 | 0 | 0 | 938 |
| client-python | 23 | 2 | 1 | 0 | `shakenfist_client/apiclient.py` (1,653) |
| actions | 64 | 1 | 0 | 0 | 930 |
| agent-python, client-python-k3s, hunkydory, kerbside-patches, sfui, visual-digest-rust | -- | 0 | 0 | 0 | under 800 |

The counts include files that are not anybody's review unit:
`shakenfist/protos/database_pb2_grpc.py` (10,302 lines) is
generated. Any tool here needs an exclusion mechanism from day one,
and the `NON_SOURCE_DIRS` pattern in
`scripts/audit/text/python_source.py` is the precedent -- pruned
during the walk, with each entry's reason written beside it.

**Function length is the other half, and it is per-language.** The
file that prompted this plan was ryll's `app.rs`, whose real
defects were a 1,630-line `eframe::App::ui` and a 610-line
`process_events`; the file's length followed from them. That file
no longer exists: shakenfist/ryll's `PLAN-app-split.md` phases 1
to 4 split it into `ryll/src/app/`, and as of 2026-11-21 the
longest function in the workspace is 649 lines by finding 8's
proxy, elsewhere. Phase 3's survey records what that does to this
plan; the argument is unaffected, but not because those two
functions moved -- they were split, and `app.rs` is gone. The
record is now held by
`shakenfist-spice-renderer/src/channels/display.rs::handle_message`,
which was never part of `app.rs`, so the per-function defect this
plan targets has changed address rather than gone away. What
each language's linter offers today:

* **Rust** (instar, ryll, uncalibrated-sextant, visual-digest-rust):
  clippy's `too_many_lines`, in `clippy::pedantic`, threshold set
  by `too-many-lines-threshold` in `clippy.toml`. All four already
  run clippy with `-D warnings` and already carry a `clippy.toml`
  for `rust-unwrap-lint`, which is the precedent for a lint the
  audit requires in `[workspace.lints.clippy]`.
* **Python**: the fleet runs flake8 (via pre-commit, and via
  `tox -e flake8` in kerbside and shakenfist). No repository runs
  ruff or pylint, so ruff's `PLR0915` is not available without
  changing linter. flake8 itself has no function-length rule; it
  has `max-complexity` (mccabe, `C901`) built in, and length needs
  a plugin (`flake8-functions`, `CFQ001`). See open question 2.
* **TypeScript** (hunkydory, sfui): biome. Not investigated in
  depth for this plan; see open question 3.

**How fleet tooling is distributed.** A template directory under
`templates/` holds the files an adopting repository copies verbatim
(`templates/mermaid-lint/` is the closest precedent: a script for
`tools/`, a workflow, and an optional per-repository exclude file),
and a criterion checks the copy is present and current
(`mermaid-lint-ci` in `scripts/audit/checks/docs_content.py`).
Code-level conventions with no template are checked in configuration
(`rust-unwrap-lint` in `scripts/audit/checks/packaging.py`).

## Mission and problem statement

Make it impossible for a file that is already over the size
threshold to grow, or for a new file to cross it, without a
reviewer seeing a deliberate decision to allow it -- and do the same
for functions, where each language's linter can.

**This revisits a decision.** `PLAN-review-unit-size.md` made file
size "a candidate a reviewer may raise, never a gate", and that was
argued rather than assumed. Its reasons, and why a ratchet does not
fall foul of them:

* **A hard cap forces splits at line numbers rather than seams.**
  The block's own words: "a file split at a line number rather than
  at a seam is worse than the long file it replaced". A ratchet
  forces no split. Every file already over the threshold may stay
  exactly as large as it is for ever; what it may not do is grow
  silently. Nobody is pushed into a bad split by a deadline.
* **Raising size is opening a question, not recording a defect.**
  The ratchet keeps that. Growing a baselined file is allowed: the
  pull request raises the file's entry in the baseline by hand, and
  that one-line diff is the question asked in a place a reviewer
  cannot scroll past. The "stated reason" the block already asks
  for above 1,500 lines gets somewhere to live.
* **Length must not be traded for comments.** The real risk of any
  counter, and the ratchet does not escape it: a contributor near
  their limit is tempted to delete rationale to make room. The v2
  block says so explicitly, the escape hatch above means nobody is
  ever forced to, and the push audit is told to treat a diff that
  shrinks a baselined file by deleting comments as a finding.

The same plan's D1 is also the argument for doing this at all:
"an advisory block is copied by the repositories that were already
going to copy it", and the growth table above is what advisory
looks like three weeks in, in the repository that wrote it.

Deliberately out of scope:

* **Splitting any file.** That is each repository's own work, under
  its own plan; ryll's is `PLAN-app-split.md` in shakenfist/ryll.
  The ratchet only stops the numbers getting worse.
* **A hard cap of any kind.** Not now and not as a later phase.
* **Counting lines differently for comments.** A comment-blind count
  would make deleting code the only way under the line, which is
  the right incentive, but it needs a lexer per language and the
  baseline escape hatch already removes the pressure to delete
  comments. Recorded under Future work.
* **New dependencies in `scripts/`.** The ratchet is stdlib Python.

## Open questions

1. **Where does the check run in an adopting repository: as a
   pre-commit hook, a CI lane, or both?** Pre-commit catches it at
   the author's desk and every repository already runs pre-commit
   in CI, so a hook alone gates merges too. **Default: a local
   pre-commit hook only, no separate workflow** -- unlike
   mermaid-lint it needs no container and runs in well under a
   second, so it does not earn its own lane.
2. **Python function length: plugin or complexity?**
   `flake8-functions` (`CFQ001`) is a direct length rule but is a
   third-party plugin in every Python repository's lint
   environment. mccabe `max-complexity` is built in but measures
   branching, not length, and a 600-line function of straight-line
   code passes it. Moving the fleet to ruff would give `PLR0915`
   and much else, but is a linter migration and a plan of its own.
   **Default: Rust only in this plan; Python and TypeScript become
   Future work with this analysis attached**, so the Python choice
   is made by someone measuring it rather than by default.
3. **TypeScript.** biome's rule set has moved quickly and this plan
   did not verify which length or complexity rules the fleet's
   pinned biome version offers. **Default: deferred with Python.**
4. **Thresholds.** 1,500 lines per file matches the block's
   "wants a stated reason" line, so the baseline is exactly the
   list of files that owe one. For `too-many-lines-threshold`,
   clippy's default of 100 would put a large share of instar and
   ryll into `#[expect]` on day one, burying the outliers that
   matter. **Default: choose per repository from the measured
   distribution during the pilot, recording the figure and its
   derivation in that repository's `clippy.toml` comment; the
   criterion requires the lint to be on, not a particular number.**
   Answered for ryll in phase 3, decision 3: 200, giving 13
   `#[expect]`s across about 4,145 functions. The worry in this
   question was specific to a pre-split `app.rs`; measured after
   the split, clippy's own default of 100 would have cost 40.
5. **Enforcement and the sweep.** Bumping `source-file-size` to v2
   and adding a template-adoption criterion makes most of the
   fleet non-compliant the next morning (see phase 4 for the
   counts). `PLAN-review-unit-size.md` D2 chose to let the daily
   audit drive the fixes rather than sweep the fleet. **Default:
   the same, except ryll, which is the pilot and lands before
   enforcement.**

## Execution

Phases are the sections below rather than separate files,
following this repository's convention. Phases 1 and 2 shipped
together from the branch `size-ratchet`; from phase 3 on, each
phase takes a branch named for it, matching the rest of the fleet
(`size-ratchet-phase-NN-descriptive`). The ryll pilot (phase 3)
does its work in shakenfist/ryll and is recorded as `ryll <sha>
(#pr)`; the development-side branch for that phase carries the
plan, the close-out of phases 1 and 2, and the pilot's report.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The ratchet template | Complete | dbcf540 (#405) |
| 2. `source-file-size` v2 | Complete | dbcf540 (#405) |
| 3. ryll pilot | In progress | |
| 4. Criteria and enforcement | Not started | |
| 5. Push audit | Not started | |

Phases 1 and 2 ship as one pull request here, and phase 4 as a
second, because the pilot has to land in ryll between them: the
criteria should go live only once there is a real adopter to test
them against, not just this repository and the fixtures.

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

### 1. The ratchet template

A new `templates/source-size-ratchet/`, following
`templates/mermaid-lint/`'s shape:

| File | Destination | Description |
|------|-------------|-------------|
| `source-size-ratchet.py` | `tools/source-size-ratchet.py` | The check; stdlib Python 3, copied verbatim |
| `pre-commit-hook.yaml` | merged into `.pre-commit-config.yaml` | A `repo: local` hook stanza, `pass_filenames: false` |
| -- | `tools/source-size-baseline` | Written by the adopting repository on adoption: one `<lines> <path>` per oversize file, sorted by path |
| -- | `tools/source-size-exclude` | Optional, written by the adopting repository: generated or vendored trees, one glob per line, each with a `#` comment saying why |
| `README.md` | -- | What it does, how to adopt it, and how to grow a baselined file |

Behaviour, all of it decidable from the tree with no git history:

* Enumerate files with `git ls-files`, which keeps `target/`,
  `.cargo-cache/`, virtualenvs and anything else untracked out
  without a list. Count only source extensions (the list in the
  Situation table, held in one constant). Apply a built-in
  exclusion list for things that are never a review unit anywhere
  in the fleet -- `*_pb2.py`, `*_pb2_grpc.py`, `*.min.js` and the
  like -- each with its reason in a comment, as `NON_SOURCE_DIRS`
  does; then the repository's own `tools/source-size-exclude`.
  (Lock files need no entry: none has a source extension.)
* Fail if a file over the threshold (1,500, one constant) is not in
  the baseline, or is longer than its baseline entry. The failure
  message names the file, both numbers, and the two honest fixes:
  shrink it, or raise its entry in the baseline in this pull
  request and say why in the description.
* `--update` rewrites the baseline, but only downwards: it lowers
  entries that have shrunk and removes files that dropped under the
  threshold or no longer exist. It never raises an entry or adds a
  file -- growth is a hand edit by design, because the hand edit is
  the reviewable decision. A baseline entry that is higher than its
  file is not a failure (it would make every shrink a two-step
  dance), but `--update` tightens it.
* `--init` writes the baseline from scratch, for adoption only.

Tests in `scripts/tests/test_source_size_ratchet.py`, against
temporary git repositories built in the test, running the
template as a subprocess (it is a script, not a `Check`, so
`CheckTestCase` does not apply): growth fails, shrink passes, new
file over threshold fails, excluded path ignored, untracked file
ignored, `--update` never raises an entry.

This repository adopts it in the same phase: it is in its own audit
matrix, and its baseline is the first real one. It holds twelve
files, all under `scripts/`, re-derived on 2026-11-15; the
Situation table above counted eight on 2026-10-10, and
`test_plans.py` alone has since grown from 2,956 lines to 4,526.

### 2. `source-file-size` v2

Rewrite `templates/shared-blocks/source-file-size.md` and bump it
to v2. Keep everything v1 says that is still true -- re-review cost
as the argument, 800 and 1,500, the exemptions, seams not line
numbers, never trading comments for length -- and change three
things:

* Replace "Neither is a gate, there is no hard cap" with what is
  now true: there is still no cap and no file is ever forced to
  split, but a file over 1,500 lines may not grow, and no file may
  cross 1,500, without a reviewed edit to the repository's
  baseline. Point at `templates/source-size-ratchet/`.
* Tell the push auditor to read **the files the diff touches, not
  only the diff**: a branch that grows a file already over 800
  lines is a finding to raise even when the branch's own addition
  is small, because per-diff review is how slow growth gets past.
  A raised baseline entry is reviewed as the decision it is, and
  its reason belongs in the pull request description.
* Tell the auditor that a diff shrinking a baselined file by
  deleting rationale comments is a finding, not a win.

Embed v2 in this repository's `PUSH-AUDIT.md` in the same commit
as the bump, so `development` stays compliant.

**What the bump does to the fleet**, re-derived on 2026-11-15 by
running this branch's `scripts/audit-check.py` against a fresh
clone of each repository's default branch, then
`scripts/audit-manage-issues.py --dry-run` over the results:
`push-audit` applies to 13 repositories, and on `main` all 13
carry v1 (the first count, from the 2026-11-08 compliance page,
had 12 and three already failing; `andris` has since come into
scope and `instar`, `sfui` and `uncalibrated-sextant` have since
passed). With v2 this one passes and the other 12 fail with
exactly one finding -- `source-file-size` "v1 embedded, v2
current; copy it verbatim from
templates/shared-blocks/source-file-size.md" -- and none has an
open `push-audit` issue, so the next run files 12 new issues:
`actions`, `andris`, `client-python-k3s`, `divergulent`,
`hunkydory`, `instar`, `kerbside`, `occystrap`, `ryll`, `sfui`,
`shakenfist`, `uncalibrated-sextant`. Each is closed by a verbatim
copy. ryll's is closed by the pilot.

### 3. ryll pilot

**Planning effort: high.** It reaches into another repository's
plan to take a phase off it, and it turns on a lint in a build
that runs clippy eleven times with different feature sets and
`-D warnings` on each, where `#[expect]` has a failure mode that
`#[allow]` does not.

One pull request in shakenfist/ryll does the work; the
development-side branch `size-ratchet-phase-03-ryll-pilot` carries
this plan, the close-out of phases 1 and 2, and the pilot's report,
and is pushed after the ryll pull request merges so that it can
carry that report.

**In scope.** Adopting the ratchet template in ryll (script, hook,
exclude file, baseline, review-scope entries); enabling
`clippy::too_many_lines` workspace-wide with a measured threshold
and an `#[expect]` on every remaining offender; the convention and
rationale documentation that shakenfist/ryll's `PLAN-app-split.md`
phase 6 asks for; closing out that phase and its phase 4; and
reporting back here what phase 4 needs.

**Out of scope.** Splitting any file or any function. The baseline
is a list of files that already owe a reason, not a work queue, and
nothing here asks anyone to shorten one. Also out: ryll's
`PLAN-app-split.md` phase 5, which is unplanned and stays that way;
the six repositories still holding an open `push-audit` issue, which
the daily audit drives (open question 5); and anything about Python
or TypeScript function length (open questions 2 and 3, Future work).

#### What the survey found

Surveyed 2026-11-21 against shakenfist/ryll `origin/develop` at
f845f45, and shakenfist/development `main` at 2f0c62e, unless
stated. The phase section below was written on 2026-10-10 and
re-touched on 2026-11-15, and six of its factual claims have
moved. Corrections to the Situation and Mission sections were made
at their source in the same commit as this rewrite, so they do not
need making again; the dated figures in the Situation table were
left alone, as that section's own instructions require, and the
shifts are recorded here.

1. **`ryll/src/app.rs` no longer exists.** `PLAN-app-split.md`
   phases 1 to 4 split it into `ryll/src/app/` and
   `ryll/src/app/ui/`, 23 files, none over 1,500 lines. Phase 4
   merged as ecf54b5 (#726); its Execution table still reads
   `In progress` only because no phase 5 ever branched to close it
   out. The 1,630-line `eframe::App::ui` and the 610-line
   `process_events` that prompted this plan are both gone: the
   longest function under `app/` is `app/mod.rs::new` at 246 lines.
   This is the finding that reopens the sequencing question, and
   decision 1 settles it.

2. **ryll has 20 source files over 1,500 lines, not twelve.**
   Measured with the template's own `SOURCE_EXTENSIONS` list and
   the vendored sfui tree excluded: 221 tracked source files, 52
   over 800, 20 over 1,500, largest
   `shakenfist-spice-renderer/src/channels/display.rs` at 6,330.
   At this plan's own 2026-10-10 datum (ryll 16cb826) the same
   measurement gives 160 files, 39 over 800 and 12 over 1,500 --
   so the twelve was right when it was written, and the count has
   grown by two thirds in six weeks. `ryll/src/bugreport.rs` went
   4,275 to 5,101 and `display.rs` 5,356 to 6,330 over that
   window, while `app.rs` went from 6,308 to nothing. The plan's
   thesis gained its best evidence while the plan was being
   written.

3. **ryll already carries `source-file-size` v2, verbatim.**
   `PUSH-AUDIT.md:333`, landed by `consistency-fixes-2026-11-19`
   (#740, merge a6028d0); diffed against
   `templates/shared-blocks/source-file-size.md` and identical.
   ryll's `push-audit` issue #733 is closed. The step that said to
   re-copy it is already done, by the mechanism phase 2 set up
   rather than by this phase -- see decision 2.

4. **Phase 2's fleet prediction held exactly.** Twelve
   `Consistency: Pre-push audit file` issues were filed; six are
   closed (ryll, shakenfist, hunkydory, instar, sfui, andris) and
   six remain open (occystrap, client-python-k3s, kerbside,
   divergulent, actions, uncalibrated-sextant). The audit-driven
   approach from open question 5 is working and needs no
   intervention from this phase.

5. **The sfui exclusion is not load-bearing.** The largest
   vendored file is `ryll/src/web/assets/sfui/sf.css` at 822
   lines; nothing under that tree is over 1,500, so the baseline
   would be correct without an exclude file. Written anyway, for
   the reason in decision 5.

6. **ryll's clippy configuration is as this plan described it.**
   `clippy.toml` holds only `allow-unwrap-in-tests = true`;
   `[workspace.lints.clippy]` holds only `unwrap_used = "warn"`.
   Seven first-party crates carry `[lints] workspace = true`;
   `shakenfist-spice-protocol/fuzz` and `tools/gen-swatches-jpeg`
   do not. The second is a second exemption, where phase 4's
   section anticipates only the fuzz crate -- recorded for phase 4
   rather than fixed here.

7. **`make lint` runs clippy eleven times, each with
   `-D warnings`.** `Makefile:337-340` is one chained `sh -c`: the
   workspace with `--all-targets` and
   `shakenfist-spice-compression/encode,ryll/rdp`; `-p ryll
   --features gui,digest-decode`; a rustdoc pass, which is not
   clippy; `-p shakenfist-spice-compression --no-default-features
   --features encode`; then `tools/check-slim-renderer.sh`, which is
   itself eight more clippy invocations -- the renderer with
   `--no-default-features --all-targets`, then once again for each
   of `audio`, `usbredir`, `webdav`, `encoder`, `h264-decode`,
   `mozjpeg` and `digest-decode`. This is the finding that most
   changes the step plan. `#[expect]` emits
   `unfulfilled_lint_expectation` when its lint does not fire, and
   `-D warnings` makes that an error, so a function whose measured
   length crosses the threshold in one feature configuration and
   not another cannot carry an `#[expect]` at all. Headroom above
   the threshold is a correctness property here, not a matter of
   taste, and it is the strongest argument for the lower of the
   two candidate thresholds -- more so than the first count of this
   finding implied, because `shakenfist-spice-renderer`, which holds
   the two longest functions in the workspace, is linted in eight
   feature configurations rather than one -- nine of the eleven
   lanes in all, those eight plus the workspace lane.

   Step 3b retired the headroom clause as well: the lint counts the
   body's source span, so every lane that lints a function reports
   the same length and headroom is not a correctness property.
   Decision 3 carries the consequence. The rest of this finding
   stands as the dated record of what was believed before the
   measurement.

8. **The function-length distribution.** A proxy measurement
   (brace-matching, counting non-blank, non-comment body lines,
   which is the shape `clippy::too_many_lines` counts) over the
   seven lint-inheriting crates: 4,151 functions, of which 44 are
   over 100 lines, 30 over 120, **22 over 150**, 18 over 175, 14
   over 200, 11 over 250 and 5 over 300. Exactly one of the 22 is
   a test. The longest are
   `shakenfist-spice-renderer/src/channels/display.rs::handle_message`
   (649) and `::decode_image_and_emit` (529); nothing else exceeds
   308. It is a proxy, so step 3b confirmed it against clippy
   itself before step 3c annotated anything. Clippy reports 40 over
   100, 28 over 120, 21 over 150, 17 over 175 and 13 over 200, and
   a reconstruction of this proxy lands within one of clippy at
   every threshold. The residual gap is boundary rounding of a line
   or two either way, plus one Windows-gated function the proxy
   counts and no Linux lane can see.

9. **This repository's own ratchet is live and passing.**
   `python3 -I tools/source-size-ratchet.py` exits 0 on
   `development` main, over a baseline of twelve files. Phases 1
   and 2 are holding, which is what makes ryll a pilot rather than
   a first attempt.

10. **Two adoption details the template README covers but the
    phase section did not.** ryll's `.pre-commit-config.yaml`
    already has four `repo: local` entries (lines 2, 32, 129,
    142), so the hook stanza merges into one of them rather than
    adding a fifth. And ryll's `.vscode/review-scope.toml`
    `include` list matches by extension plus a handful of names,
    none of which matches an extensionless
    `tools/source-size-exclude`, so adopting with an exclude file
    needs an `include` entry or `review-scope-completeness`
    reports it as an orphan.

#### Decisions

1. **Both halves land in phase 3, in one ryll pull request, and
   `PLAN-app-split.md` phase 6 is closed out as `Complete`
   pointing here.** `PLAN-app-split.md` phase 6 says to pick the
   threshold from the distribution *after* its phase 5, and phase
   5 is unplanned. Deferring to it would block this plan's phase 4
   on another repository's unplanned refactor and would land
   `rust-function-length-lint` with no compliant adopter at all --
   the phase 4 section predicts 3 of 4 Rust repositories failing,
   which is only true if ryll passes. Against that, the ordering
   phase 6 wanted is not load-bearing: `#[expect]` is precisely
   the mechanism that makes a stale exemption a build failure, so
   if phase 5 later shortens `app/mod.rs::new` or
   `app/connection.rs::reconnect`, the annotation fails and is
   removed rather than quietly surviving. Taking the phase is
   therefore cheaper than waiting for it, and the two functions
   phase 5 would touch are the two the mechanism protects. At the
   threshold step 3b settled on, 200, that last clause is only
   half true: of the two, just `app/mod.rs::new` carries an
   `#[expect]`, while `app/connection.rs::reconnect` sits below
   200 and carries none, so the build-failure guarantee covers one
   function rather than both. This is the decision a reviewer is
   most likely to argue with, because
   it takes a phase off a plan in another repository; the
   alternative, and its cost, are stated above.

2. **The "re-copy `source-file-size` v2" step is dropped as
   already done, not re-performed.** Finding 3. Phase 3 verifies
   the embedded copy is byte-identical to the template and records
   that it arrived through the daily audit, which is a result worth
   having: it is the first evidence that open question 5's
   audit-driven approach closes a v2 bump without a sweep.

3. **`too-many-lines-threshold = 200`**, giving 13 `#[expect]`s.
   Planning proposed 150 and 22 annotations, reading finding 8's
   proxy; Mikal chose 200 once step 3b had measured the real
   distribution, and the execution notes record the change. The
   derivation is clippy's own count over all eleven lanes: of about
   4,145 functions in the seven lint-inheriting crates, 40 are over
   100 lines, 21 over 150 and 13 over 200. The hit counts are
   clippy's; the denominator is not, and cannot be -- clippy
   reports only the functions over the threshold, never a total.
   It comes from a proxy count over the same seven crates, which
   finding 8 gives as 4,151; the 4,145 written here and in ryll's
   `clippy.toml` and `docs/development.md` is six short of it and
   the discrepancy was never run down. Nothing rests on the
   denominator -- the inventory size is what the threshold was
   chosen on -- but it should not be read as clippy's figure.
   Thirteen reasons are an
   inventory a reviewer reads in one sitting; clippy's default of
   100 would have cost 40.

   The argument that had rejected 200 was finding 7's, that the
   functions nearest the line -- `main.rs::main` at 193 and
   `main_channel.rs::run_loop` at 192 -- sit in crates built under
   several feature sets, which is the shape that produces an
   `unfulfilled_lint_expectation` in one lane and a `too_many_lines`
   in another. Step 3b disproved it: the lint counts the lines of
   the body's source span, not the code left after `cfg` stripping,
   so every function's reported length is identical in every lane
   that lints it, and a function that is compiled out loses its
   `#[expect]` with it. Headroom above the threshold is therefore
   not the correctness property finding 7 took it for, and the
   choice between 150 and 200 is a judgement about how long an
   inventory stays readable. The cost of the wider figure is that
   `main` has seven lines of slack: growing it past 200 fails
   `make lint` until someone annotates it, which is the same
   decision-at-the-point-of-growth the baseline asks of a file.

4. **`#[expect]` throughout, with `#[allow]` plus a stated reason
   kept as a documented escape that ryll does not use.** The escape
   was written for a function whose length is feature-sensitive
   across the threshold, which cannot carry an `#[expect]` under
   `-D warnings` (finding 7). Step 3b looked for one in all eleven
   lanes and found that none can exist: the lint measures the body's
   source span, so a feature flag changes only whether a function is
   linted, never its reported length. ryll therefore carries
   thirteen `#[expect]`s and no `#[allow]`, and `docs/development.md`
   records the mechanism rather than leaving the next reader to
   rediscover it.

   Phase 4's criterion should still accept an `#[allow]` that
   carries a stated reason, even though ryll gives it nothing to
   accept: the hazard has no instance in a tree clippy measures
   this way, but a macro-generated or build-script-generated body
   in another repository may yet need the escape, and a criterion
   that refuses one outright turns that into an argument about the
   criterion. Phase 3 records the requirement; phase 4 implements
   it.

5. **The exclude file is written even though no sfui file is over
   the threshold.** Finding 5. It is correct independently of
   today's line counts -- the tree is vendored from shakenfist/sfui
   by that repository's `tools/vendor.sh` and reviewed there, which
   `.vscode/review-scope.toml:69-74` already says -- and adding it
   after a vendored file crosses 1,500 would mean editing the
   baseline to remove an entry, which reads like a concession.

6. **The development-side pull request is pushed after the ryll
   pull request merges.** The pilot's report is phase 3's
   deliverable and phase 4 reads it, so it belongs in this phase's
   own pull request rather than in phase 4's close-out commit. The
   branch therefore holds unpushed commits for as long as the ryll
   review takes, which is the established pattern here.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | worktree | In a shakenfist/ryll worktree off `develop`, adopt the source-size ratchet, following `templates/source-size-ratchet/README.md` "Adopting it" in shakenfist/development. Copy `templates/source-size-ratchet/source-size-ratchet.py` to `tools/source-size-ratchet.py` byte for byte, keeping the executable bit. Merge the hook from `templates/source-size-ratchet/pre-commit-hook.yaml` into the `hooks:` list of an existing `repo: local` entry in `.pre-commit-config.yaml` -- there are four, at lines 2 (`rust-check`), 32 (`audit-range-test`), 129 (`bidi-check`) and 142 (`docs-line-references`); the last is the closest neighbour, a stdlib-Python tree-wide check -- rather than adding a fifth entry, and keep the stanza's comments. Write `tools/source-size-exclude` with one entry, `ryll/src/web/assets/sfui/`, and a comment saying the tree is vendored from shakenfist/sfui by that repository's `tools/vendor.sh` and reviewed there; `.vscode/review-scope.toml:69-74` has the wording. Then run `python3 -I tools/source-size-ratchet.py --init` and `--init` only once. In `.vscode/review-scope.toml`, add `tools/source-size-baseline` to `exclude` and `tools/source-size-exclude` to `include`, each with a comment; `shakenfist/development`'s own `.vscode/review-scope.toml:78-111` has both reasons written out, and the include entry is needed because the file has no extension and nothing in ryll's include list would match it. Do not touch `REVIEWS.md`. Verify: the baseline holds exactly 20 entries, `grep -c 'assets/sfui/' tools/source-size-baseline` is 0, no `*_pb2*` or minified file appears in it, `python3 -I tools/source-size-ratchet.py` exits 0, and `pre-commit run --all-files` passes. Report the baseline and the hook's wall-clock time (`time python3 -I tools/source-size-ratchet.py`, best of three). Commit subject: `Adopt the source-size ratchet.` |
| 3b | high | opus | worktree | Same ryll worktree. Measure the real `clippy::too_many_lines` distribution before anything is annotated. Add `too_many_lines = "warn"` to `[workspace.lints.clippy]` in the root `Cargo.toml` and `too-many-lines-threshold = 100` to `clippy.toml` *temporarily*, then run each of the eleven clippy invocations in `make lint` (`Makefile:337-340`, eight of them inside `tools/check-slim-renderer.sh`) separately, without `-D warnings`, capturing every `too_many_lines` hit with its file, function and reported line count. The build is Dockerised: use the `make` targets, do not install a toolchain on the host. Produce one table of every function clippy reports over 100, per lane, noting any function that appears in one lane and not another or with a different count -- that set is what decision 4's `#[allow]` escape exists for, and it is the single most important output of this step. Compare against the proxy measurement in finding 8 (4,151 functions; 44 over 100, 22 over 150, 14 over 200) and say where clippy disagrees with it and why. Then state whether decision 3's threshold of 150 still gives the 22-function inventory it predicts, or what figure does. (The 150 and the 22 are the figures this brief was issued with; at the gate Mikal chose 200, giving 13, and decision 3 is corrected at source.) Revert the temporary `clippy.toml` value before finishing; leave the `Cargo.toml` line in place. This step commits nothing: it reports. |
| 3c | high | opus | worktree | Same ryll worktree, after the back brief gate on 3b's numbers. Set `too-many-lines-threshold` in `clippy.toml` to the confirmed figure, with a comment giving the figure, the date it was measured and the counts it came from (the `clippy.toml` already carries `allow-unwrap-in-tests`, so follow its comment style). Put `#[expect(clippy::too_many_lines, reason = "...")]` on every function 3b reported over that threshold. Each reason says why *this* function is long and what splitting it would cost -- a dispatch `match` over a wire protocol whose arms belong together, a decode loop whose state is shared, a `main` that is sequential setup -- and never restates the lint. For any function 3b flagged as feature-sensitive across the threshold, use `#[allow(clippy::too_many_lines)]` with an adjacent comment naming the lane that forced it, per decision 4. Then record the convention in `AGENTS.md` -- `#[expect]` rather than `#[allow]`, so an exemption that stops being needed fails the build and is removed -- and the rationale in `docs/development.md`, which is what shakenfist/ryll's `PLAN-app-split.md` phase 6 asks for. Verify with the full `make lint`: all eleven clippy lanes must pass with `-D warnings` and no `unfulfilled_lint_expectation`. Commit subject: `Require a function-length lint.` |
| 3d | medium | sonnet | worktree | Same ryll worktree. Close out shakenfist/ryll's `docs/plans/PLAN-app-split.md`: set phase 4's row to `Complete` with `ecf54b5 (#726)` in its `Merged` cell (it merged on 2026-11-14 and no phase 5 branched to close it out, so this pull request is the first to touch the table since), and set phase 6's row to `Complete`, leaving its `Merged` cell to be filled with this pull request's own merge commit at close-out. Add two or three sentences to phase 6's section saying the phase was executed as shakenfist/development `PLAN-size-ratchet.md` phase 3, that the threshold was therefore measured before app-split phase 5 rather than after, and why that is safe (`#[expect]` turns a stale exemption into a build failure). Update ryll's `docs/plans/index.md` row for `PLAN-app-split.md` if its status changes. Do not mark the plan itself `Complete`: its phases 5 and 7 are outstanding. Commit subject: `Close out the function-length lint phase.` |
| 3e | medium | sonnet | none | In the shakenfist/development worktree `development-wt-sr-p3`, after the ryll pull request merges. Add a `#### Pilot report` subsection at the end of this phase 3 section recording, as falsifiable statements: the threshold chosen and its derivation, the number of `#[expect]`s and the number of `#[allow]`s with the lane that forced each, the hook's wall-clock time on ryll's tree, the baseline size, and either the template changes the pilot motivates or one sentence saying it motivated none. Where it motivates a template change, make that change in `templates/source-size-ratchet/` in the same commit and say whether adopters must re-copy, per this section's original instruction that template changes land here first. Also correct phase 4's section with what the pilot establishes: the `tools/gen-swatches-jpeg` exemption from finding 6, and decision 4's requirement that the criterion accept an `#[allow]` carrying a stated reason. The pilot has already motivated two template changes, both recorded in the execution notes: the README's claim that no workflow is needed, and that adopting alongside a function-length lint raises baseline entries. Commit subject: `Report the ryll source-size pilot.` |

#### Risks and mitigations

* **`unfulfilled_lint_expectation` in one feature lane.** The
  mechanism is finding 7 and it would show up as a red `make lint`
  in a lane nobody was looking at. Step 3b retired it rather than
  mitigating it: every lane that lints a function reports the same
  count, so the hazard has no instance. Decision 3's headroom,
  which this bullet first gave as the mitigation, is not what
  rules it out -- decision 3 now disclaims headroom as a
  correctness property. What stands is step 3b measuring every
  lane separately *before* anything is annotated, and step 3c's
  verification being the full `make lint` rather than one clippy
  run. The management
  session reads the lanes' output, not the sub-agent's
  summary of it.
* **Thirteen reasons written to fill a slot.** A reason that
  restates the lint ("this function is long") is worse than no
  annotation, because it makes the inventory unreadable while
  looking complete. Mitigated by the management session reading all
  thirteen and rejecting any that does not say what splitting
  would cost; this is the review step most likely to send 3c back.
* **The Docker build is slow and 3b runs clippy eleven times at a
  low threshold.** Mitigated by 3b and 3c sharing one worktree and
  one image, and by 3b capturing the full hit list once rather than
  iterating on the threshold.
* **ryll's `develop` moves while the branch is open.** It merged
  ten commits in the week before this survey. If `develop` moves
  under the branch, rebase and re-run `--update`, never `--init`:
  `--init` silently accepts whatever has grown since, which is the
  drift the tool exists to stop, and the template README says so.
* **The baseline freezes 20 files, two of which are growing
  fast.** `bugreport.rs` and `display.rs` gained 1,800 lines
  between them in six weeks. The ratchet stops them growing
  further, which is the point, and no split is asked for; but the
  pull request description should say that the two are the reason
  the count moved from twelve to twenty, so the baseline is not
  read as an endorsement.
* **Taking a phase off another repository's plan.** If Mikal
  prefers `PLAN-app-split.md` phase 6 to stay open, step 3d's
  close-out is the only part that changes and decision 1's gate in
  the back brief is where to say so.

#### Definition of done

* `python3 -I tools/source-size-ratchet.py` exits 0 in ryll, and
  `diff` between ryll's `tools/source-size-ratchet.py` and this
  repository's `templates/source-size-ratchet/source-size-ratchet.py`
  is empty.
* ryll's `tools/source-size-baseline` holds exactly the number of
  entries step 3a reported, `grep -c 'assets/sfui/'` on it is 0,
  and no entry matches `*_pb2*`, `*.min.js` or `*.min.css`.
* `pre-commit run --all-files` passes in ryll, and `make lint`
  passes with all eleven clippy invocations run and no
  `unfulfilled_lint_expectation` in any of them.
* ryll's `clippy.toml` holds a `too-many-lines-threshold` whose
  adjacent comment states the figure, the date it was measured and
  the counts it came from.
* ryll carries exactly thirteen
  `#[expect(clippy::too_many_lines, reason = "...")]` annotations and
  no `#[allow(clippy::too_many_lines)]` at all, which step 3b's
  measurement established it does not need. Should a later tree need
  one, it carries an adjacent comment naming the lane that forced it;
  there are no bare ones.
* ryll's `.vscode/review-scope.toml` lists
  `tools/source-size-baseline` under `exclude` and
  `tools/source-size-exclude` under `include`, each with a reason,
  and `python3 scripts/audit-check.py` against ryll reports no new
  `review-scope-completeness` failure.
* ryll's `PLAN-app-split.md` phase 4 row reads `Complete` with
  `ecf54b5 (#726)`, and its phase 6 row reads `Complete`.
* ryll's `AGENTS.md` states the `#[expect]`-not-`#[allow]`
  convention and `docs/development.md` carries the rationale.
* This section carries a `Pilot report` subsection, and the
  threshold figure appears identically in it, in ryll's
  `clippy.toml` comment and in ryll's `docs/development.md` -- no
  fact about the threshold is stated differently in two places.
* `pre-commit run --all-files` passes in shakenfist/development,
  and `python3 scripts/audit-check.py --repo-path . --repo-name
  development` reports what it reported before this phase.

#### Back brief

Two gates, both because the work after them is cheap to propose
and expensive to redo:

* **After step 3b, before 3c annotates anything.** Report clippy's
  own hit list, the set of feature-sensitive functions, and whether
  decision 3's threshold of 150 survives contact with it. Twenty-two
  hand-written reasons against the wrong number is the expensive
  mistake available here. (It did not survive: the gate chose 200
  and thirteen reasons. The figures here are what the gate was
  asked, not what it answered.)
* **Before step 3d closes out `PLAN-app-split.md` phase 6.**
  Confirm `Complete` pointing here, rather than `Superseded` or
  leaving it open.

#### Execution notes

2026-11-21 (AEDT). The back brief confirmed decision 1 --
shakenfist/ryll's `PLAN-app-split.md` phase 6 is closed out
`Complete` pointing here, rather than `Superseded` or left open --
and gave this session permission to push the ryll branch and open
its pull request once the definition of done passes.

ryll's `develop` had moved from the surveyed f845f45 to 10dfdc1 by
the time execution started, so the branch `size-ratchet-pilot`
(worktree `ryll-wt-size-ratchet`) was cut from that. The baseline
is 20 entries on either base, so finding 2's count is unaffected.

| Commit | Step |
|--------|------|
| ryll `965a14f` | 3a: the script, the hook, a CI job, the exclude file, the baseline and the review-scope entries |
| ryll `a1cfada` | 3c: the threshold, thirteen `#[expect]`s, `AGENTS.md`, `docs/development.md`, six raised baseline entries |
| ryll `c70d276` | 3d: `PLAN-app-split.md` phases 4 and 6 closed out |

* **`make lint` runs clippy eleven times, not four.** Finding 7
  counted the commands chained in the `lint` target and did not look
  inside `tools/check-slim-renderer.sh`, which is eight clippy
  invocations of its own. Corrected at source in finding 7, in this
  phase's preamble, in 3b's and 3c's briefs, in the risks and in the
  definition of done. It strengthens rather than weakens decision 3:
  `shakenfist-spice-renderer` holds the two longest functions in the
  workspace and is linted under eight feature configurations --
  nine lanes counting the workspace lane -- which is the exact
  shape that makes an `#[expect]` fire in one lane and fail as
  `unfulfilled_lint_expectation` in another.
* **ryll's CI does not run `pre-commit run --all-files`, so the
  template's step 2 is wrong about this repository.** The template
  README says there is no workflow to add, because the fleet's CI
  runs every hook through pre-commit. ryll runs one hook that way
  (`skillsaw`, for reasons its job comment gives) and gives every
  other hook a CI job of its own, deliberately, so that a failure is
  reported under one name and holds one runner. Adopting by the
  README alone would therefore have gated commits by anyone with
  hooks installed and nothing else -- not merges, and not a branch
  pushed with `--no-verify`. Step 3a's commit adds a
  `source-size-ratchet` job mirroring the bidi scan, with the same
  runner size and the same fork guard, since it too runs a script
  from the checkout that a fork can edit, and adds it to the `Can
  enqueue` and `Automated reviewer` gates. This is the first template
  change the pilot motivates and step 3e lands it: the README cannot
  assert `pre-commit run --all-files` fleet-wide, and must tell an
  adopter to check how its CI runs hooks and add a job where they are
  run individually. The `source-size-ratchet` criterion in phase 4
  reads `.pre-commit-config.yaml` and would not notice the
  difference, which is worth deciding there rather than here.
* The sub-agent's hook comment asserted the CI run the template
  promises. It was corrected before the commit rather than after,
  which is the reason to read a brief's factual premises before
  sending it and the diff before committing it.
* `audit-check.py` against the branch moves one verdict against the
  local clone, `push-audit` from fail to pass, because that clone
  predates ryll's adoption of `source-file-size` v2. That is finding
  3 confirmed from the other direction rather than an effect of this
  work; `review-scope-completeness` and `review-scope-ci` both pass.
* **The threshold is 200, not the 150 decision 3 proposed.** At the
  first back-brief gate Mikal took the wider figure, so ryll carries
  thirteen `#[expect]`s rather than twenty-one. Decision 3 is
  corrected at source, along with open question 4's answer and the
  risk that counted the reasons. The planning argument for 150 rested
  on headroom being a correctness property; step 3b removed that
  basis, which left inventory size as the whole question and 200 a
  defensible answer to it.
* **The feature-sensitive set is empty, and cannot be otherwise.**
  Step 3b ran all eleven lanes at a threshold of 100 and found 40
  functions over it, with every function's count identical in every
  lane that lints it. `clippy::too_many_lines` measures the lines of
  the body's source span, not the code left after `cfg` stripping:
  `shakenfist-spice-renderer/src/session.rs::run_connection` holds
  four `#[cfg(feature = ...)]` statements inside its body and reports
  268 lines in all nine lanes that lint it -- the workspace lane
  plus the eight in `tools/check-slim-renderer.sh`, since the
  other two clippy invocations select `ryll` and
  `shakenfist-spice-compression` and lint neither the renderer nor
  it -- including the lane with no features and the two that turn
  those features on. A feature flag
  changes only whether a function is linted, and a function compiled
  out loses its `#[expect]` with it. Decision 4's escape therefore
  has no instance here; it stays documented, and phase 4's criterion
  stays permissive, per the gate's second answer.
* The management session re-parsed the raw per-lane JSON rather than
  taking the counts on report, and reproduced all of them: 40 over
  100, 28 over 120, 21 over 150, 17 over 175, 13 over 200, and no
  function with a differing count. One lane, `-p
  shakenfist-spice-compression --no-default-features --features
  encode`, reports nothing at all, because that crate gates every
  long module behind features the lane does not enable.
* **The ratchet's first catch was the pull request that installed
  it.** The thirteen exemptions add 70 lines of prose to six files
  that are already in the baseline, so step 3c's first
  `pre-commit run --all-files` failed on the `source-size-ratchet`
  hook adopted two commits earlier: `bugreport.rs` 5,101 to 5,110,
  `display.rs` 6,330 to 6,347, `inputs.rs` 3,201 to 3,210,
  `main_channel.rs` 3,921 to 3,929, `playback.rs` 2,331 to 2,340 and
  `session.rs` 1,663 to 1,681. The six entries were raised by hand,
  which is the flow the baseline's own header prescribes and the only
  one available, since `--update` never raises an entry. Two things
  worth carrying forward: the reason for the growth has to be in the
  pull request description, which is where the baseline design puts
  the judgement, and the two halves of this plan interact in a way
  neither phase predicted -- a function-length lint makes files
  longer, because the exemptions live next to the functions they
  excuse. An adopter taking both halves in one pull request should
  expect to raise entries in it, and the template should say so.
  Every raised entry equals its file's current length exactly, which
  the management session checked rather than assumed.
* 3a's commit hash in this table was corrected after an amend:
  `15e5538` became `965a14f`, and nothing outside this table had
  cited it.
* Step 3d found that `PLAN-app-split.md` phase 6's `Plan` cell named
  a phase plan file that was never written and now never will be, so
  it names shakenfist/development's `PLAN-size-ratchet.md` phase 3
  instead. Nothing reads that column: this repository's plan
  criteria take a phase's label from the first cell and its state
  from the `Status` column, and ryll has no test that reads it. The
  phase 6 section also *instructed* a reader to measure after
  app-split phase 5, which is no longer what happened, so that
  instruction was corrected rather than left to contradict the
  record.

#### Pilot report

Phase 4 reads this subsection. Each statement is checkable.

* **Landing.** shakenfist/ryll pull request #746, "Adopt the
  source-size ratchet and require a function-length lint", merged
  2026-11-22 as `debf8b1` onto `develop`. Commits `965a14f` (3a),
  `a1cfada` (3c) and `c70d276` (3d); step 3b committed nothing, by
  design.
* **Threshold: 200**, derived from clippy's own per-lane counts, not
  the proxy. Of about 4,145 functions in the seven crates that
  inherit the workspace lints, 40 are over 100 lines, 28 over 120,
  21 over 150, 17 over 175 and 13 over 200. The management session
  re-parsed the raw per-lane JSON and reproduced every count.
  Planning proposed 150; Mikal took 200 at the first back-brief
  gate once 3b had measured. Clippy's default of 100 would have
  cost 40 exemptions.
* **Exemptions: 13 `#[expect(clippy::too_many_lines, reason =
  "...")]`, and zero `#[allow]`.** As the pilot landed them, at
  `debf8b1`, by file and function: `app/mod.rs::new`,
  `bugreport.rs::test_display_snapshot_serialises`,
  `main.rs::run_web`, `rdp.rs::run_rdp`,
  `glz.rs::decompress_glz`, `channels/display.rs::handle_message`
  and `::decode_image_and_emit`,
  `channels/inputs.rs::handle_input_event`,
  `channels/main_channel.rs::handle_message`,
  `channels/playback.rs::process_messages`,
  `channels/usbredir.rs::handle_usbredir_message`,
  `session.rs::run_connection` and `::run_headless`. Exactly one
  is a test.

  The functions matter, not just the files: `main.rs` is annotated
  on `run_web` and `main_channel.rs` on `handle_message`, so
  decision 3's observation that `main` (193) and `run_loop` (192)
  sit below the threshold carrying no annotation is consistent
  with this list rather than contradicted by it.

  The annotations are multi-line, so a single-line grep for
  `expect(clippy::too_many_lines` finds none and under-reports to
  zero. What does reproduce the count is an occurrence count of the
  lint's name, one per annotation:
  `grep -rn 'clippy::too_many_lines' --include='*.rs' . | wc -l`
  prints 13.

  ryll has moved since. The `bugreport.rs` annotation sat on
  `test_display_snapshot_serialises`, and ryll #823 (`213f53c`,
  merged 2026-11-27) moved the renderer's snapshot tests out of
  that file, so on `origin/develop` the same thirteen read
  `shakenfist-spice-renderer/tests/snapshots.rs` in place of
  `ryll/src/bugreport.rs`. The count is thirteen on both trees and
  exactly one is still a test. An earlier draft of this report
  listed the `origin/develop` set while attributing it to the
  pilot, which made `bugreport.rs` appear to have been raised in
  the baseline without carrying an exemption; it carried one.
* **The `#[allow]` count is zero, and mechanically so.**
  `clippy::too_many_lines` measures the lines of the function
  body's source span, not the code left after `cfg` stripping, so a
  feature flag changes only whether a function is linted, never its
  reported length. Every lane that lints a function reports the
  same count, and a function compiled out loses its `#[expect]`
  with it. 3b ran all eleven lanes at a threshold of 100 and found
  no function with a differing count.
  `shakenfist-spice-renderer/src/session.rs::run_connection` holds
  four `#[cfg(feature = ...)]` statements inside its body and
  reports 268 lines in all nine lanes that lint the renderer: the
  workspace lane plus the eight in `tools/check-slim-renderer.sh`.
  Nine, not the ten the first draft of this report and the
  execution notes both said -- `cargo clippy -p <pkg>` lints only
  the selected package, so the `ryll` and
  `shakenfist-spice-compression` lanes do not see the renderer at
  all. The property the conclusion rests on is unaffected: the
  count is identical in every lane that lints it. Decision 4's
  escape therefore has no instance here; it stays documented and
  phase 4's criterion stays permissive.

  This is how clippy counts today, not a documented guarantee, and
  ryll pins no toolchain -- `.devcontainer/Dockerfile` installs
  rustup `stable`, and `Cargo.toml`'s `rust-version = "1.89"` is
  only the MSRV -- so the measurement is against whatever `stable`
  was on 2026-11-21 and the next bump can move it. A toolchain
  that counted post-expansion lines would reintroduce exactly the
  hazard finding 7 described, which is the standing reason decision
  4 keeps the escape documented for a tree that has no use for it.
  Worth re-checking on a toolchain bump; phase 4 should not write a
  criterion that assumes the set stays empty.
* **The Windows-gated gap in finding 8 is not a hole.** Finding 8
  noted one Windows-gated function the proxy counts and no Linux
  lane can see. It is in the only file carrying Windows `cfg`s in
  the lint-inheriting crates,
  `shakenfist-spice-compression/src/jpeg.rs`: the longest such
  function is `decode` at `jpeg.rs:715`, 209 lines of span and 120
  counted body lines. That is over the threshold of 100 step 3b
  measured at, which is why the proxy saw it and Linux clippy did
  not, and well under the 200 finally chosen -- so no
  Windows-only exemption is missing, and "exactly thirteen" is not
  a Linux-only claim. Checked at `debf8b1`.
* **Hook cost: 0.169s**, best of five runs (range 0.169-0.181s),
  for `python3 -I tools/source-size-ratchet.py` at `debf8b1`, exit
  0, over 230 tracked files carrying one of the tool's source
  extensions. Measured by the management session on the kasm host,
  so it is an order-of-magnitude figure for a developer machine
  rather than a CI guarantee.
* **Baseline: 20 entries**; `grep -c 'assets/sfui/'` on it is 0;
  and ryll's `tools/source-size-ratchet.py` is byte-identical to
  `templates/source-size-ratchet/source-size-ratchet.py`.
* **`source-file-size` v2 arrived through the daily audit, not this
  phase.** ryll's `PUSH-AUDIT.md:333` is identical to
  `templates/shared-blocks/source-file-size.md`, landed by #740
  (merge `a6028d0`), and ryll's `push-audit` issue #733 is closed.
  That is the first evidence that open question 5's audit-driven
  approach closes a shared-block version bump without a sweep.
* **Two template changes were motivated and landed with this
  report**, both in `templates/source-size-ratchet/README.md`:
  step 2 of "Adopting it" claimed the fleet's CI runs
  `pre-commit run --all-files`, which is false for ryll (it runs
  `skillsaw` through pre-commit and gives every other hook a job of
  its own), and "Growing a baselined file" now says an adopter
  taking a function-length lint should expect to raise entries.
  `source-size-ratchet.py` itself is **unchanged**, so no adopter,
  ryll included, needs to re-copy it; only the README's adoption
  guidance changed. Step 3e's brief anticipated a re-copy, and
  there is none to do.

  The same finding leaves phase 4 a decision, recorded in its own
  section: a `source-size-ratchet` criterion that checks only for
  the hook in `.pre-commit-config.yaml` passes a repository whose
  hooks never run in CI, which is the state ryll was in before
  this pilot.
* **The interaction neither phase predicted.** A function-length
  lint makes files longer, because the exemption reasons live next
  to the functions they excuse. The thirteen reasons added 70 lines
  of prose to six files already in the baseline, so step 3c's first
  `pre-commit run --all-files` failed on the hook adopted two
  commits earlier: `bugreport.rs` 5,101 to 5,110, `display.rs`
  6,330 to 6,347, `inputs.rs` 3,201 to 3,210, `main_channel.rs`
  3,921 to 3,929, `playback.rs` 2,331 to 2,340 and `session.rs`
  1,663 to 1,681. The entries were raised by hand, since `--update`
  never raises one, and every raised entry equals its file's
  current length exactly. Phase 4 and every future adopter inherit
  this.

### 4. Criteria and enforcement

Two new criteria, each with all five of its files in step (see
Success criteria):

* **`source-size-ratchet`** (`docs/audits/source-size-ratchet.md`):
  applicable to every in-scope repository with tracked source.
  Requires `tools/source-size-ratchet.py` to match the template,
  `tools/source-size-baseline` to exist, and
  `.pre-commit-config.yaml` to invoke the script. Does **not**
  re-run the ratchet: whether the baseline is honoured is the
  hook's job on every pull request, and an audit that re-judged it
  would only disagree with CI.

  The pilot found the hole in that sketch, and phase 4 has to
  settle it: the hook being in `.pre-commit-config.yaml` does not
  mean it gates anything. ryll runs each hook as its own CI job
  rather than `pre-commit run --all-files`, so until phase 3's
  pilot added a `source-size-ratchet` job the hook gated only
  commits by people with hooks installed -- not merges, and not a
  branch pushed with `--no-verify`. A criterion that reads only
  the pre-commit config passes such a repository. Phase 4 decides
  whether it must also require a CI invocation, or state why
  hook-presence is enough; the second is defensible only if
  something else establishes that hooks run in CI.
* **`rust-function-length-lint`**
  (`docs/audits/rust-function-length-lint.md`): modelled on
  `rust-unwrap-lint` and living beside it in
  `scripts/audit/checks/packaging.py`. Requires `too_many_lines`
  at `warn` or `deny` in the root `Cargo.toml`, inherited by every
  first-party crate except those that cannot: the pilot found two
  non-inheriting crates in ryll, `shakenfist-spice-protocol/fuzz`
  and `tools/gen-swatches-jpeg`, neither of which has `[lints]
  workspace = true`, so the criterion must allow for more than one
  exemption or it fails ryll, the one repository the pilot made
  compliant. It also requires a `too-many-lines-threshold` in
  `clippy.toml`. It does not judge the number (open question 4).
  Per decision 4 it accepts an `#[allow(clippy::too_many_lines)]`
  that carries a stated reason and flags only a bare one: ryll needs
  no `#[allow]` and the hazard has no instance in a tree clippy
  measures this way, but a macro- or build-script-generated body
  elsewhere may need the escape, and a criterion that refuses one
  outright turns that into an argument about the criterion.

**What switching them on does**, to be re-derived from the dry run
before merging: `rust-function-length-lint` applies to 4
repositories and, after the pilot, fails 3 (`instar`,
`uncalibrated-sextant`, `visual-digest-rust`).
`source-size-ratchet` fails every one of the 22 in-scope
repositories that has tracked source, except `development` and
`ryll`; the dry run gives the exact number. As in `PLAN-review-unit-size.md` D2, the
daily audit drives the fixes; this plan opens no pull requests in
those repositories, and `docs/audits/compliance.md` showing both
criteria mostly red for a while is the intended state of a newly
enforced rule.

Verify with `audit-check.py` against local clones of ryll, instar
and kerbside and `audit-manage-issues.py --dry-run`, per the
procedure in `PLAN-review-unit-size.md` phase 2. Never regenerate
`docs/audits/compliance.md` in place.

### 5. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1, 2,
phase 3's development-side landing and 4 here; cite the ryll
pilot's own push audit for the ryll commits of phase 3 rather than
re-running those. Phase 3 landed in two repositories, and the
development-side half includes the change to
`templates/source-size-ratchet/README.md` -- adoption guidance
shipped to the whole fleet, which ryll's audit cannot cover
because it is not in ryll. Audit it here.

In addition to the standard waves: run the ratchet against every
local clone with `--init` into a scratch directory and confirm the
exclusion list leaves no generated file in any baseline.


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

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

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
`scripts/audit/registry.py` beside the other plan checks, write
`docs/audits/plan-index.md` following the structure in
`docs/audits/README.md` and linking to `compliance.md#plan-index`,
add the file to `docs/audits/README.md`, add its lines to
`FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable." A brief that names four of the criterion's five
files is the characteristic defect here, and the frozen tables are
the one most often left out.

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
* Any new or changed criterion has all five of its files in step:
  the `Check` subclass in `scripts/audit/checks/<family>.py`, its
  registration in `CHECKS` in `scripts/audit/registry.py`, the
  specification under `docs/audits/`, its row in
  `docs/audits/README.md`, and its frozen lines in
  `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py` -- plus a
  `FROZEN_COLUMN_NAMES` line where a spec page carries more than
  one check. `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES`
  are derived from the registry rather than tables anybody edits;
  the issue title is the fleet-wide idempotency key, so renaming
  one orphans every open issue for that check across the fleet.
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

* **A comment-blind line count.** Counting only code would make
  deleting code the only way under the line, but needs a lexer per
  language; the baseline escape hatch removes the pressure to delete
  comments meanwhile. Out of scope above.
* **Watch where the ratchet pushes code, not only comments.** On its
  first commit it moved a test out of its natural home:
  `SourceSizeRatchetDeploymentTest` lives in
  `scripts/tests/test_source_size_ratchet.py` rather than beside
  `MermaidLintDeploymentTest`, because `test_docs_content.py` is
  baselined. The plan predicted pressure on rationale comments, not
  on placement. Look for both during the ryll pilot.
* **`--init` over an existing baseline.** It rewrites the baseline
  upwards and exits 0, so it can erase a failure in one command.
  The defence is that the baseline diff is reviewed; refusing to
  raise an entry without `--force` would make it checked rather than
  stated. Deferred as a behaviour change of its own.

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

Plan-specific close-out notes:

* **Future work.** Python function length (open question 2): choose
  between `flake8-functions` and a move to ruff, measured against
  shakenfist's and divergulent's distributions. TypeScript the same
  for biome (open question 3). A comment-blind line count, if the
  ratchet turns out to put pressure on rationale comments in
  practice. Reporting baseline totals per repository over time on
  the compliance page, so shrinkage is visible as well as growth.
* **Related.** shakenfist/ryll `docs/plans/PLAN-app-split.md`
  (the split that prompted this, and whose phase 6 is the clippy
  half of the pilot). `PLAN-review-unit-size.md`, whose advisory
  decision this plan revisits.

