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
file that prompted this plan is ryll's `app.rs`, whose real defects
are a 1,630-line `eframe::App::ui` and a 610-line
`process_events`; the file's length follows from them. What each
language's linter offers today:

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
5. **Enforcement and the sweep.** Bumping `source-file-size` to v2
   and adding a template-adoption criterion makes most of the
   fleet non-compliant the next morning (see phase 4 for the
   counts). `PLAN-review-unit-size.md` D2 chose to let the daily
   audit drive the fixes rather than sweep the fleet. **Default:
   the same, except ryll, which is the pilot and lands before
   enforcement.**

## Execution

Work happens on `size-ratchet` off `main`, in the worktree
`../development-size-ratchet`. Phases are the sections below
rather than separate files, following this repository's
convention. The ryll pilot (phase 3) lands in shakenfist/ryll and
is recorded as `ryll <sha> (#pr)`.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The ratchet template | In progress | |
| 2. `source-file-size` v2 | In progress | |
| 3. ryll pilot | Not started | |
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

In shakenfist/ryll, as the clippy half of `PLAN-app-split.md`'s
phase 6 plus the file half from here. One pull request:

* Copy `tools/source-size-ratchet.py` and the hook stanza;
  `--init` the baseline (twelve files over 1,500 today); write
  `tools/source-size-exclude` for `ryll/src/web/assets/sfui/`.
* Re-copy `source-file-size` v2 into `PUSH-AUDIT.md`.
* `too_many_lines = "warn"` in `[workspace.lints.clippy]`, a
  measured `too-many-lines-threshold` in `clippy.toml` with its
  derivation in a comment, and
  `#[expect(clippy::too_many_lines, reason = "...")]` on every
  remaining offender. `#[expect]` rather than `#[allow]` so an
  exemption that stops being needed fails and is removed.

What the pilot reports back here, before phase 4 starts: the
function-length distribution and the threshold chosen, the number
of `#[expect]`s it needed, anything about the ratchet's ergonomics
that should change the template, and how long the hook takes.
Template changes it motivates land here first, and the pilot
re-copies.

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
* **`rust-function-length-lint`**
  (`docs/audits/rust-function-length-lint.md`): modelled on
  `rust-unwrap-lint` and living beside it in
  `scripts/audit/checks/packaging.py`. Requires `too_many_lines`
  at `warn` or `deny` in the root `Cargo.toml`, inherited by every
  first-party crate (same fuzz-crate exemption), and a
  `too-many-lines-threshold` in `clippy.toml`. It does not judge
  the number (open question 4). It does flag `#[allow(
  clippy::too_many_lines)]`, which should be `#[expect]`.

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

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1, 2 and 4
here; cite the ryll pilot's own push audit for phase 3 rather than
re-running it. In addition to the standard waves: run the ratchet
against every local clone with `--init` into a scratch directory
and confirm the exclusion list leaves no generated file in any
baseline.


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

