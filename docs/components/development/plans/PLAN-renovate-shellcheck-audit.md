# Require the shellcheck-py versioning rule

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

`shellcheck-py/shellcheck-py` tags four numeric components --
`v0.11.0.1` is shellcheck `0.11.0` at packaging revision 1 -- which is
not semver. Renovate's `github-tags` datasource, which is what sits
behind the `pre-commit` manager for that hook, cannot order those tags:
the lookup fails with `Can't find version matching` on the dependency
dashboard and no update is ever proposed. Nothing goes red. The hook
revision simply stops moving, and the repository looks fully
renovate-managed the whole time.

A `versioning` `packageRule` fixes it, and the fleet has had one since
`dc2f6ed` (#275, 2026-10-18). `PLAN-renovate-cadence.md` phase 1 moved
it into the shared preset `renovate-fleet.json`, where
`RenovateFleetPresetTest` now holds it in place. What never happened is
the other half: the `renovate` criterion does not require the rule, and
says so in `docs/audits/renovate.md:312-316` -- "This check does not
look for that rule". So the rule spread by hand, through the template
and the consistency sweeps, and stopped where the sweeps stopped.

Measured against the 24 repositories in the audit matrix on 2026-11-21,
reading each default branch through the GitHub API:

| Repositories | shellcheck hook | Rule reachable | shellcheck-py pin |
|---|---|---|---|
| `development` | yes | via the preset | `v0.11.0.1` |
| 14 repositories | yes | local copy | `v0.11.0.1` |
| `agent-python`, `client-python`, `client-python-k3s`, `client-python-ova` | yes | **no** | `v0.10.0.1` |
| `images`, `private-ci` | yes | **no** | `v0.11.0.1`, `v0.10.0.1` |
| `shakenfist`, `visual-digest-rust` | no | n/a | n/a |
| `cloudgood` | no | local copy | n/a |

The 14 local copies are byte-identical to the preset's rule, `build`
group and `$` anchor included, so there is no wrong-shaped copy in the
fleet to correct. `v0.11.0.1` was tagged on 2025-08-09 and `v0.10.0.1`
on 2024-03-17, so the four repositories in the third row are pinned to
a hook revision 20 months old and 15 months behind the current one,
while every other dependency in those same trees is current. That is
the measurement this plan exists to make impossible to accumulate
again: the linter gating every commit in four repositories is itself
the one dependency nobody is watching.

`images` and `private-ci` are scoped out of the `renovate` criterion in
`REPO_OVERRIDES` (`scripts/audit/repo.py:127-148`) -- `private-ci` to
seven plan and vendor checks, `images` to `eol-distro` alone -- so no
criterion will ever report them, whatever this plan does. They are
recorded in *Future work* rather than fixed here.

## Mission and problem statement

Make the `renovate` criterion require the `shellcheck-py` versioning
rule wherever the hook is used, accepting the rule reached through
`local>shakenfist/development:renovate-fleet` on equal terms with a
local copy, so a repository whose shellcheck pin cannot be looked up is
reported rather than discovered. The deliverable is the criterion and
its specification; it newly fails four repositories, and the issues the
weekly audit then files are handed to `consistency-fix` like any
other.

Out of scope: fixing those four repositories here (the audit files the
issues, `consistency-fix` closes them); requiring the preset itself,
which `docs/audits/renovate.md:15-19` deliberately does not and
`PLAN-renovate-cadence.md` phase 3 achieves by sweeping every
`renovate.json` onto it; `images` and `private-ci`, which are scoped
out of the criterion; and any change to the rule's own shape, which is
settled and tested.

## Open questions

1. **Does this want to be its own criterion, or a finding inside
   `renovate`?** Default taken, and decision 1 argues it: a finding
   inside `renovate`. A new criterion would need five files, a frozen
   metadata row and an issue title of its own, and would file a second
   issue against the same four repositories about the same
   `renovate.json`, for a defect the existing issue is already the
   right place to describe.

2. **Should this wait for `PLAN-renovate-cadence.md` phase 3?** Default
   taken, and decision 4 argues it: no. That sweep fixes all four
   repositories as a side effect, which makes the criterion look
   redundant and is exactly why it should land first -- a criterion
   that only ever passes has never been shown to fail.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The criterion and its specification | Complete | `b28ba45` (#433) |
| 2. Push audit | In progress | |

Phase 1 is one pull request. Phase 2 audits it against `PUSH-AUDIT.md`
once it has landed, for the reasons the `plan-push-audit-phase` block
below gives.

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


### Execution notes

* 2026-11-21 (AEDT): plan written on branch
  `renovate-shellcheck-audit-phase-01-criterion`, worktree
  `../development-wt-rsa-p1`. The plan and phase 1 were written
  together, since the plan has two phases and one of them is the push
  audit.
* 2026-11-21 (AEDT): back brief answered. Both gates stand as
  planned: the phase lands ahead of `PLAN-renovate-cadence.md` phase 3
  (decision 4) and the work is a finding inside the `renovate`
  criterion rather than a criterion of its own (decision 1). This
  session may push the branch and open the pull request.
* 2026-11-21 (AEDT): four corrections to the plan, each made at its
  source rather than noted here alone.
  - The step table and the definition of done both said `pytest`.
    There is no pytest in this repository: the suite runs as
    `python3 -m unittest discover -s scripts/tests -t scripts`, which
    is what the `audit package and script tests` pre-commit hook
    invokes. Corrected before step 1a ran.
  - Step 1a's brief said a malformed `renovate.json` "is not this
    finding". Wrong, and the step reported it: the `config is None`
    guard the same brief specifies returns False, so the finding
    fires whenever the hook is present, exactly as the pre-commit
    manager finding beside it does. The brief now says so. The
    semantics are deliberate -- a config that cannot be parsed does
    not carry the rule -- and the test asserts the run reports rather
    than raises.
  - Step 1a grew `scripts/audit/checks/packaging.py` from 2,413 to
    2,476 lines, which the `source size ratchet` pre-commit hook
    fails. The plan did not anticipate it. The entry in
    `tools/source-size-baseline` is raised by hand, which is the
    escape that file's header documents, and the pull request
    description carries the justification it asks for. A
    `checks/renovate.py` seam is the alternative and is not taken
    here: it would be a 2,400-line restructure around a 63-line
    change. Filed as #434 and recorded in *Future work*, mirroring
    #412 for the test module, which the tests already split along
    that line.
  - The first review round took the file to 2,493 lines: the
    shellcheck helper now reads rules through `rule_covers()` and
    documents the matchers it deliberately does not read (decision 3).
    The baseline entry is raised again for the same reason.
  - Step 1b's brief warned that a stale clone was its failure mode.
    It is, and two repositories prove it: measuring the finding
    against the local working trees reports `clingwrap` and
    `uncalibrated-sextant` failing, and both are checkouts behind
    their `develop` default branch, which carries the rule. The
    recorded measurement reads each repository's default branch, not
    whatever its working tree holds.

* 2026-11-21 (AEDT): step 1b measured the finding. The audit was at
  commit `9ed124f` (step 1a landed as `83d9565`), run by
  `step-1b/measure.py` under the session scratchpad, which does not
  survive a cleanup -- the method below is the durable record -- over
  each of the 24 `audit` job matrix repositories. For each, the
  script asked GitHub for the default branch, ran `git fetch origin
  <branch>` in the local clone, exported `origin/<branch>` with `git
  archive` to a scratch directory and audited that, never touching a
  checkout. All 24 fetched and measured; none is unmeasured. The
  prediction held in both directions. The verdicts below were
  measured that morning at `9ed124f`, before `f373abc` cleared the
  template drift that afternoon.

  | Verdict for the `renovate` check | Count | Repositories |
  |---|---|---|
  | Fails, shellcheck finding present | 4 | `agent-python`, `client-python`, `client-python-k3s`, `client-python-ova` |
  | Not applicable, scoped out by `REPO_OVERRIDES` | 2 | `images`, `private-ci` |
  | Fails, other findings only, no shellcheck finding | 18 | `actions`, `andris`, `clingwrap`, `cloudgood`, `development`, `divergulent`, `hunkydory`, `instar`, `kerbside`, `kerbside-client`, `kerbside-patches`, `library-utilities`, `occystrap`, `ryll`, `shakenfist`, `sfui`, `uncalibrated-sextant`, `visual-digest-rust` |
  | Passes outright | 0 | |

  Three of the four shellcheck failures also fail on other `renovate`
  findings. That was true of all four when measured, and the tree
  moved the same day: `f373abc` ("Revert the rust-toolchain pin #426
  merged", 2026-11-21T14:23 AEDT) landed four hours after the
  measurement and took `with: toolchain: stable` back out of the
  template, so the drift finding below cleared fleet-wide. By
  2026-11-22 `client-python-ova`'s only `renovate` finding is the
  shellcheck one, which phase 2's survey item 4 records. The 18
  "other findings only" failures are not this phase's doing and were
  verified as predating it: `90f1ea8`, already
  on the default branch before this branch was cut, added `with:
  toolchain: stable` to `templates/renovate/renovate.yml`, so every
  fleet copy of `renovate.yml` drifted from the template and `ryll`
  reported "the template's `with:` is missing before line 58".
  The `dtolnay/rust-toolchain` rule in this repository's own
  `renovate.json` predicted it in terms -- 16 repositories newly
  failing the criterion, for two files that `PLAN-renovate-cadence.md`
  disables and then deletes -- so it is a known trade with a recorded
  end, not a finding for this plan to file. The script classifies a
  failure by whether any part of its detail names `shellcheck-py`.
  `clingwrap` and `uncalibrated-sextant`, measured
  at `develop`, carry the rule and report no shellcheck finding.
  `images` and `private-ci` report `not_applicable` with their scope
  reason, which is "nothing" for the criterion's purposes. The
  exported trees are not git checkouts, so unrelated history-based
  checks report that they could not measure `private-ci`; the
  `renovate` check does not read history.

### Phase 1: the criterion and its specification

**Planning effort:** medium. This adds a finding to an existing
criterion following the shape of the finding beside it, which is what
`plan-planning-effort`'s "In this repository" note calls medium. The
part that needed high effort was the survey, and it is done and
recorded below.

**Review effort:** high. The criterion decides what the fleet is held
to, and the blast radius is other people's issue trackers.

#### What the survey found

The work was recorded on 2026-10-18 as a `plan-blockers` note against
an unwritten plan, and again in `PLAN-renovate-cadence.md`'s survey
item 7 and *Future work*. Four of those premises no longer hold, and
one is now impossible to satisfy:

1. **The list of repositories lacking the rule is wrong.** The note
   names `actions`, `agent-python`, `client-python`,
   `client-python-k3s`, `divergulent`, `instar`, `kerbside-patches`,
   `occystrap`, `ryll` and `sfui`. Measured on 2026-11-21, six of those
   ten now carry the rule: `actions`, `divergulent`, `instar`,
   `kerbside-patches`, `occystrap`, `ryll` and `sfui` all have it, as do
   `andris`, `clingwrap`, `hunkydory`, `kerbside`, `kerbside-client`,
   `library-utilities` and `uncalibrated-sextant`. The consistency
   sweeps carried it most of the way. What the note missed is
   `client-python-ova`, `images` and `private-ci`. The real set the
   criterion would newly fail is four: `agent-python`,
   `client-python`, `client-python-k3s` and `client-python-ova`.

2. **"A test that the rule is identical in `renovate.json` and the
   template" cannot be written, because there is now one copy.** The
   note's second ask was written when `dc2f6ed` had put the rule in two
   places. `PLAN-renovate-cadence.md` phase 1 (`47086db`) moved it into
   `renovate-fleet.json` and reduced both configs to an `extends`:
   `templates/renovate/renovate.json` is now four lines and holds
   nothing but the extend, which
   `test_the_template_holds_nothing_but_the_extend`
   (`scripts/tests/test_packaging_renovate.py:532`) asserts. There is
   no second copy to compare, and the duplication the test would have
   guarded is gone. The guard that replaced it is
   `test_the_real_preset_keeps_the_shellcheck_versioning_rule`
   (`:363`), which loads the real preset. This item of the note is
   discharged, not deferred.

3. **`hunkydory#98` is not a prerequisite.** The note says it fixes
   `hunkydory`. `hunkydory` carries the rule today and its pin is
   `v0.11.0.1`, so whatever closed that issue, it is closed as far as
   this plan can tell from the tree.

4. **Two of the repositories the note counts can never be reported.**
   `images` and `private-ci` are scoped by `only_checks` in
   `REPO_OVERRIDES` (`scripts/audit/repo.py:127-148`) to seven
   plan/vendor checks and to `eol-distro` respectively; neither list
   contains `renovate`. `private-ci` has no `renovate.json` at all, and
   is not renovate-managed. Including them in a count of what the
   criterion fails would have been wrong.

5. **The scope correction in `PLAN-renovate-cadence.md` must be read
   as a requirement, not a note.** That plan's phase 1 decision 1
   requires the criterion to accept the preset form, because a
   criterion reading only the local file "holds the fleet to a line the
   policy has moved out of". Today only `development` extends the
   preset, so a local-file-only criterion would fail 23 repositories,
   not four. Step 1a's brief is written to the helper pair that already
   solves this.

**Nothing else in the premises was wrong.** The rule's shape, its
`build` group name and its `$` anchor are as recorded; all 14 local
copies are byte-identical to the preset's; the pre-commit manager is
enabled in every repository that carries the hook except `private-ci`;
and `docs/audits/renovate.md:312-316` still states the gap in the words
this plan closes. Items 1, 4 and 5 are corrected at source in the same
commit as this plan: the `plan-blockers` note is replaced, and
`PLAN-renovate-cadence.md`'s *Future work* entry gains the
repository count and the discharge of its "identical in two places"
half.

#### Decisions

1. **A fifth finding inside the `renovate` criterion, not a new
   criterion.** `Renovate.run`
   (`scripts/audit/checks/packaging.py:481-539`) already accumulates
   four findings into one `issues` list and one issue titled
   "Renovate", and the finding beside this one -- the pre-commit
   manager -- is conditional on reading the same
   `.pre-commit-config.yaml`. A new criterion would cost a spec page, a
   `CHECKS` registration, a `docs/audits/README.md` row, a
   `FROZEN_METADATA` and a `FROZEN_ISSUE_TITLES` line, and would open a
   second issue on `agent-python` describing a second defect in the
   file the first issue is already about. The cost of the finding form
   is that it cannot be scoped out independently, which nothing wants.

2. **The condition is the hook, not the pin.** The finding fires when
   `.pre-commit-config.yaml` references
   `shellcheck-py/shellcheck-py`, regardless of what revision it
   currently pins. `images` is the worked argument: its pin is
   `v0.11.0.1`, the current release, and it has no rule -- so a
   criterion that measured staleness would pass it today and fail it
   whenever upstream next tags, reporting a defect that has been
   present for months as if it had just appeared. What is wrong is the
   missing rule, which is a fact about the configuration and is true
   now.

3. **Both forms pass, by the helper pair the two findings beside it
   use.** `renovate_config_versions_shellcheck_py(config)` reads the
   parsed dict, and `renovate_versions_shellcheck_py(repo)` returns
   that `or extends_fleet_preset(config)`, exactly as
   `renovate_config_manages_pre_commit` /
   `renovate_manages_pre_commit` do
   (`scripts/audit/checks/packaging.py:63-99`). The preset is trusted
   by name in the second helper and nowhere else, and the real file is
   held to carrying the rule by a test that loads it -- the pattern
   `RENOVATE_FLEET_PRESET`'s comment (`:46-54`) describes and
   `PLAN-renovate-cadence.md` phase 1 decision 2 established.

   The first review round changed what "naming the hook" means. The
   helper first accepted only a literal entry in `matchPackageNames`,
   which would have reported a repository whose rule Renovate applies
   -- written with `matchDepNames`, a glob, a `/regex/` or a
   deprecated spelling -- in that repository's tracker. It now reads
   the rule through `rule_covers()`, the matcher the lockstep criterion
   already uses. The opposite hole is accepted deliberately: a rule
   narrowed away from the `pre-commit` manager, or carrying
   `enabled: false`, still passes. That errs toward a pin left
   unwatched rather than an issue filed outward, and closing it would
   mean modelling the rest of Renovate's matchers.

4. **This lands before `PLAN-renovate-cadence.md` phase 3, not after.**
   That phase sweeps every `renovate.json` onto the preset, which gives
   all four failing repositories the rule as a side effect. Waiting for
   it would mean the criterion's first scheduled run reports compliant
   fleet-wide -- a check that has never been seen to fail, guarding a
   bug nobody can reproduce any more. Landing first gets the four
   repositories reported now, fixed by whichever arrives first, and
   leaves a criterion with a known failing set recorded in this plan.
   The cost is four audit issues that `consistency-fix` may close
   redundantly if the sweep lands between the issue and the fix, which
   is cheap and visible. This is the decision most likely to be argued
   with; the counter-argument is in the back brief.

5. **The four repositories are not fixed by this plan.** The audit
   files the issues and `consistency-fix` closes them, which is the
   established route for a criterion newly failing a repository and is
   what the original note asked for. A sweep phase here would duplicate
   `PLAN-renovate-cadence.md` phase 3's pull requests in the same four
   trees.

#### Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add the finding. In `scripts/audit/checks/packaging.py`: (i) a module constant `SHELLCHECK_PY_HOOK = 'shellcheck-py/shellcheck-py'` beside `RENOVATE_FLEET_PRESET` (`:54`), with a comment saying why four-component tags need a `versioning` rule -- take the reasoning from the rule's own `description` in `renovate-fleet.json`, do not restate it at length; (ii) `uses_shellcheck_py_hook(repo)`, modelled on `uses_remote_pre_commit_hooks` (`:33-44`), using `check_file_contains(repo, '.pre-commit-config.yaml', ...)` with a pattern matching the hook repository URL; (iii) `renovate_config_versions_shellcheck_py(config)`, returning True when any entry in `config.get('packageRules')` is a dict whose `matchPackageNames` list contains `SHELLCHECK_PY_HOOK` and whose `versioning` is a non-empty string -- do not match the regex itself, since the rule's exact string is the preset's business and is tested there; (iv) `renovate_versions_shellcheck_py(repo)`, returning `renovate_config_versions_shellcheck_py(config) or extends_fleet_preset(config)` with the same `config is None` guard as `renovate_manages_pre_commit` (`:88-99`). Then add a fifth `issues.append` to `Renovate.run`, after the pre-commit manager one (`:528-535`), conditional on `uses_shellcheck_py_hook(repo) and not renovate_versions_shellcheck_py(repo)`, whose message names the hook, says the `github-tags` lookup fails so the revision is never bumped, and names `RENOVATE_FLEET_PRESET` as a way to satisfy it -- match the voice of the four messages above it. Order matters only in that the `missing` early return (`:496`) must still come first. Tests go in `scripts/tests/test_packaging_renovate.py` as a new `RenovateShellcheckVersioningTest(CheckTestCase)` placed after `RenovatePreCommitManagerTest` (`:26-96`), copying its `_repo` helper shape exactly -- including adding `customManagers:githubActionsVersions` to `extends` and writing `RENOVATE_WORKFLOW`, since the other findings are separate and every fixture must carry both. Cover: the hook with no rule fails; a local rule passes; the fleet preset alone passes; a rule naming a different package does not pass; a rule naming the hook with no `versioning` key does not pass; no `.pre-commit-config.yaml` passes; a `.pre-commit-config.yaml` without the hook passes; a malformed `renovate.json` reports the finding rather than raising, because the `config is None` guard returns False exactly as the pre-commit pair's does (mirror `test_a_malformed_renovate_config_does_not_abort_the_run`, `scripts/tests/test_packaging.py:1122`); and `missing` is still reported first. Add one assertion to `RenovateFleetPresetTest` (`:98`) that extending the preset alone satisfies this check too, beside `test_extending_the_preset_satisfies_each_check_alone` (`:136`) -- extend that test rather than adding a parallel one if it enumerates the checks. Do not add a `FROZEN_METADATA` or `FROZEN_ISSUE_TITLES` row: no new criterion id is created, and adding one would fail `scripts/tests/test_metadata.py`. Run the repository's suite with `python3 -m unittest discover -s scripts/tests -t scripts` -- which is what the `audit package and script tests` pre-commit hook runs, and the only runner here; there is no pytest -- and report the counts. Commit subject: `Require the shellcheck-py versioning rule.` |
| 1b | medium | sonnet | none | Prove the finding fires on exactly the four repositories and no others, with a script rather than by reading. Write it under this session's scratchpad (not the tree): for each of the 24 repositories in the `audit` job matrix in `.github/workflows/consistency-audit.yml`, run `python3 scripts/audit-check.py --repo-path <local clone> --repo-name <repo> --github-org shakenfist` filtered to the `renovate` check, against clones under `~/src/shakenfist/<repo>` fetched to their default branch first -- a stale clone is the failure mode here, so fetch and report any clone you could not update rather than reporting its verdict. Expect: `agent-python`, `client-python`, `client-python-k3s` and `client-python-ova` fail naming the shellcheck finding; `images` and `private-ci` report nothing for `renovate` at all because `REPO_OVERRIDES` scopes them out (`scripts/audit/repo.py:127-148`); `development` and the 14 with local copies pass this finding; `shakenfist`, `visual-digest-rust` and `cloudgood` pass because they have no hook. Repositories failing `renovate` for one of the other four findings are expected and are not a problem -- report them separately so the count is readable. Record the result as a table in the phase's execution notes in `docs/plans/PLAN-renovate-shellcheck-audit.md`, including anything that contradicted the prediction, and say which commit of the audit you measured. Run anything long under `timeout`. Commit subject: `Measure what the shellcheck finding newly fails.` |
| 1c | medium | sonnet | none | Document it. In `docs/audits/renovate.md`: add a bullet to "What we check" (`:3-27`) after the pre-commit manager bullet, saying that a repository whose `.pre-commit-config.yaml` uses `shellcheck-py/shellcheck-py` carries a `versioning` `packageRule` for it, naming it directly or by extending the fleet preset -- match the two bullets above it, which both spell out that either form passes. Then rewrite the paragraph at `:312-316`, which currently says "This check does not look for that rule" and points the reader at the template README: it now does look, so say what it requires and keep the link to [the template README](https://github.com/shakenfist/development/blob/main/templates/renovate/README.md#shellcheck-pys-four-part-tags) as the explanation of why, rather than restating the four-component reasoning on a third page. Note there that `images` and `private-ci` are scoped out of this criterion and their pins are therefore unwatched, with a pointer to this plan's *Future work*. Do not touch `templates/renovate/README.md`'s own explanation of the tags, which is the canonical one, and do not change the "What we check" bullet at `:15-19` that says the preset is not required -- it is still not. `AGENTS.md` and `ARCHITECTURE.md`: no change. A finding added to an existing criterion is neither a new convention nor a change to the shape of the system; say so in the commit rather than leaving the omission to be noticed. Commit subject: `Specify the shellcheck versioning requirement.` |

#### Risks and mitigations

* **The criterion fails more than four repositories, and the audit
  files issues outward at trees nobody expected.** This is the risk the
  repository's own plan prompt warns about: a wrong change here does
  not go red, it files issues in other people's trackers. Step 1b is
  the mitigation and it runs before the pull request is pushed, not
  after: the management session reads its table and refuses the phase
  if the failing set is not exactly the four. A fifth repository in
  that table is a stop-and-ask, not a finding to fix in flight.

* **A repository satisfies the check with a rule that does not
  work.** `renovate_config_versions_shellcheck_py` deliberately does
  not validate the regex, so a rule naming the hook with a `versioning`
  string that mis-orders tags would pass. Accepted, with the reasoning
  in decision 3: the rule's exact shape is the preset's business and
  `test_the_real_preset_keeps_the_shellcheck_versioning_rule` holds it
  there, and all 14 local copies were measured byte-identical to it on
  2026-11-21 (survey, "Nothing else"). The mitigation if that stops
  being true is to require the preset form, which
  `PLAN-renovate-cadence.md` phase 3 makes possible and which this plan
  deliberately does not do.

* **`PLAN-renovate-cadence.md` phase 3 lands between this phase and the
  four fixes, and `consistency-fix` closes issues that are already
  fixed.** Low cost and visible: the sweep gives each repository the
  preset, the next audit run closes the issue itself, and the only
  waste is a session reading a closed issue. Decision 4 accepts it. The
  mitigation is the plan-blockers note, which this plan's close-out
  updates to say the criterion has landed, so whoever picks up that
  sweep knows the issues exist.

* **The rule's `description` is 90 words of reasoning in a JSON
  string, and the finding's message, the spec bullet and the paragraph
  at `:312` are three more places that could restate it.** Step 1c is
  written to point rather than restate, and the definition of done has
  a falsifiable check for it.

#### Definition of done

1. `python3 -m unittest discover -s scripts/tests -t scripts` passes,
   and `pre-commit run --all-files` passes.
2. `RenovateShellcheckVersioningTest` exists in
   `scripts/tests/test_packaging_renovate.py` with at least the nine
   cases step 1a lists, and deleting the `issues.append` block from
   `Renovate.run` makes it fail.
3. `grep -c shellcheck-py scripts/tests/test_metadata.py` prints `0`:
   no new criterion id was created.
4. The phase's execution notes carry step 1b's table, naming the
   commit of the audit it was measured against, and the repositories it
   reports failing the shellcheck finding are exactly `agent-python`,
   `client-python`, `client-python-k3s` and `client-python-ova`.
5. `docs/audits/renovate.md` contains no sentence saying the check does
   not look for the rule: `grep -n 'does not look for that rule'
   docs/audits/renovate.md` prints nothing.
6. `docs/audits/renovate.md` states the requirement and links the
   template README for the reason, without restating the tag format a
   third time:
   `grep -n 'fourth\|v0\.11\.0\.1' docs/audits/renovate.md | grep -v
   'templates/renovate/README.md'` prints nothing, and
   `grep -c 'shellcheck-pys-four-part-tags' docs/audits/renovate.md`
   prints `1`. This replaces a grep for `packaging revision`, which
   was unfalsifiable: `templates/renovate/README.md` does carry the
   full explanation, but wraps it as "tags a fourth, packaging,
   component", so no single-line grep for the phrase could ever have
   matched it. A done-criterion grep must not span a line wrap.
7. The `plan-index` and `plan-template` criteria pass for this
   repository: `python3 scripts/audit-check.py --repo-path . --repo-name
   development --github-org shakenfist` reports no new finding for
   either.

#### Back brief

Phase 1 is three steps in one pull request and nothing in it is
expensive to redo, so there is no mid-phase gate. The two things to
answer before step 1a starts, both of which change the phase if the
answer is no:

1. **Decision 4 -- land before `PLAN-renovate-cadence.md` phase 3.**
   The counter-argument is real: that sweep gives all four
   repositories the rule, so if it is imminent this criterion files
   four issues that the sweep closes, and the whole plan could be one
   line added to that phase's audit change instead. Against that:
   `PLAN-renovate-cadence.md` phase 2 is still `In progress` and its
   cutover is itself sequenced behind work in
   `PLAN-workflow-ref-trust.md`, so "imminent" is not established; and
   that plan's phase 1 scope paragraph deliberately excludes this
   criterion and names it as a separate plan, which is a decision
   already taken rather than one this plan is free to revisit. If the
   answer is to wait, this plan becomes `Blocked` on that phase and
   phase 1's steps move into its audit change.

2. **Decision 1 -- a finding rather than a criterion.** The note that
   started this said "criterion", and a criterion of its own would be
   separately scopable, separately overridable and separately
   reportable on the compliance page. If that separability is wanted,
   step 1a grows the five files `plan-template`'s "In this repository"
   worked brief enumerates, and the frozen-metadata rows this plan's
   definition of done currently asserts the absence of.

### Phase 2: push audit

**Planning effort:** medium, as the master plan set it. The phase runs
a runbook that already exists over a range that is already recorded;
the judgement went into the survey, which is below.

**Review effort:** medium. The phase changes no code and its diff is
this plan file. Its one real failure mode is an audit that reads the
wrong range and reports a clean result over an empty diff, which
`PUSH-AUDIT.md:50-64` warns about at length and decision 2 answers.

Planned on 2026-11-22 (AEDT) against `91df689`, on the branch
`renovate-shellcheck-audit-phase-02-push-audit`. This section replaces
the sketch the master plan carried, two of whose three factual claims
the survey found false.

#### Scope

In: running `PUSH-AUDIT.md`'s wave 1 and wave 2 over `b28ba45`, the
merge that landed phase 1; reading what the first scheduled audit run
to carry the criterion did to the four repositories it fails; the
record of both; and this phase's own close-out.

Out: fixing anything the audit finds, which lands as its own pull
request afterwards, per `plan-push-audit-phase`; the four repositories
themselves, which are `consistency-fix`'s work from the issues the
audit files; the `checks/renovate.py` split (#434) and the test-module
split (#412), which are filed deferrals rather than findings; and the
eighteen repositories measured on 2026-11-21 as failing `renovate` on
the template drift `90f1ea8` introduced. That drift predated this
plan, is recorded in the phase 1 execution notes as a known trade, and
cleared with `f373abc`; step 2f reads the current state rather than
that measurement.

#### What the survey found

Read on 2026-11-22 AEDT against `91df689` and the GitHub API. The
sketch for this phase made three factual claims. One holds, two do
not, and both of the false ones are corrected at their source rather
than only here.

1. **The caveat the sketch put to the auditor about step 1b is wrong
   twice over.** It said the measurement "was taken from local clones
   rather than from the API the audit itself reads". Neither half
   holds. The recorded method is the opposite of a local clone read:
   for each of the 24 matrix repositories the script asked GitHub for
   the default branch, fetched it, exported `origin/<branch>` with
   `git archive` into a scratch directory and audited the export,
   never touching a checkout -- the execution notes say so, and say
   that the earlier working-tree reading is what produced the two
   false failures (`clingwrap`, `uncalibrated-sextant`) that forced
   the method. Nor does the scheduled audit read an API: it clones
   each repository with `gh repo clone shakenfist/<repo> --
   --filter=blob:none` (`.github/workflows/consistency-audit.yml:137`)
   and runs the same checks over that tree. The one real difference
   is that an exported tree is not a git repository, so history-
   reading checks report that they could not measure it; `renovate`
   reads no history, and the execution notes already record the
   effect. There is no measurement caveat for the auditor here, and
   the sketch's sentence is replaced by what is genuinely worth
   attention, below.

2. **Decision 3 now carries two deliberate holes, and the sketch
   names one.** The sketch offered the auditor "decision 3's choice
   not to validate the `versioning` regex". The first review round
   added a second, in the same decision: the helper reads the rule
   through `rule_covers()`, so every matcher spelling Renovate
   applies is accepted, but a rule narrowed away from the
   `pre-commit` manager, or carrying `enabled: false`, still passes.
   That was taken deliberately -- it errs toward an unwatched pin
   rather than an issue filed into someone else's tracker -- and it
   is the more interesting of the two for an auditor, because it is
   the one that lets a repository the criterion is supposed to catch
   go on passing.

3. **The audit is weekly, not daily, and the first run carrying this
   criterion has not happened yet.** The master plan's mission said
   "the daily audit then files"; `consistency-audit.yml:14` is
   `cron: '0 18 * * 0'`, and the file explains the change at length:
   a daily run "filed and closed issues faster than the fleet could
   absorb them". Corrected at source. The three most recent runs
   started at 2026-11-01T21:27Z, 2026-11-08T21:26Z and
   2026-11-15T22:14Z, three to four hours after the cron time, so the
   first run to carry the criterion is the one due 2026-11-22T18:00Z
   and in practice likely to start around 21:30-22:15Z -- Monday
   morning AEDT. Phase 1 merged at 2026-11-21T22:44Z, after the last
   run. The most recent `workflow_dispatch` of the same workflow is
   `#41140043392`, 2026-10-30T22:44:00Z, which definition of done
   item 6 uses as its baseline. Decision 1 is about what that means
   for executing this phase.

4. **One of the four repositories fails `renovate` on the shellcheck
   finding alone, and its issue is closed.** Measured on 2026-11-22
   by step 1b's own method -- fetch the default branch, `git archive`
   it to a scratch directory, audit the export:

   | Repository | `renovate` findings | Open `Consistency: Renovate` issue |
   |---|---|---|
   | `agent-python` | container action, missing `extends`, shellcheck | #159, last updated 2026-11-15 |
   | `client-python` | container action, missing `extends`, shellcheck | #437, last updated 2026-11-15 |
   | `client-python-k3s` | container action, missing `extends`, shellcheck | #158, last updated 2026-11-15 |
   | `client-python-ova` | **shellcheck only** | none; #50 closed 2026-11-03 |

   The phase 1 execution note says "The four shellcheck failures
   also fail on other `renovate` findings", which was true when
   step 1b measured it and is not true now. The tree moved rather
   than the measurement being wrong: step 1b ran with the template
   at `9ed124f`, where `templates/renovate/renovate.yml:57-59`
   still carried the pin and its `with: toolchain: stable`, and
   `f373abc` reverted that four hours later on 2026-11-21, clearing
   the drift finding across the fleet.
   `test_only_the_doomed_renovate_copies_keep_rust_toolchain_unpinned`
   now holds the template to the unpinned reference. An earlier
   round of this phase recorded that as phase 1 having "originally
   got wrong"; that was this phase's error, and the note is restored
   to the dated measurement it is, with what moved recorded beside
   it. The consequence stands either way: the next scheduled run
   adds the shellcheck sentence to three existing issues and files a
   new issue against `client-python-ova`, whose verdict the criterion
   now decides by itself. That filing is the only evidence that will
   ever exist that this criterion works outside its own test suite,
   and step 2f reads it.

   Both halves of that prediction follow from the issue manager
   rather than from assumption: an open issue whose body has drifted
   is rewritten rather than left alone
   (`scripts/audit-manage-issues.py:476`, guarded by
   `body_is_current()` at `:105`), and only open issues are listed at
   all (`scripts/audit_common.py:160` passes `--state open`), so
   `client-python-ova`'s closed #50 is not reopened and a new issue is
   filed instead. What step 2f may count as confirmation is narrower
   than that, though, and not for the reason an earlier round of this
   phase gave. That round argued the drift revert in `f373abc`
   removes a sentence from those same three bodies, so the next run
   rewrites them regardless; the dates refute it. `90f1ea8`
   introduced the drift on 2026-11-21 at 10:36 AEDT and `f373abc`
   reverted it at 14:23 the same day, both five days after the
   2026-11-15T22:14Z run that last wrote those bodies, so the drift
   sentence never reached an issue body and the revert removes
   nothing from one. The weaker claim survives and is enough: a body
   is rewritten for any drift at all, and four repositories accrue
   unrelated findings over a week, while `body_is_current()`
   suppresses the rewrite where nothing changed -- so a timestamp on
   its own neither confirms nor refutes the criterion. The test is
   the presence of the shellcheck sentence itself in each of the
   three, and a new issue number carrying it for the fourth, which
   holds under either account and is the more conservative of the
   two.

5. **The range is one clean merge, as the sketch said.** `b28ba45`
   has two parents (`6aefd63` and `4ddc693`); the eight commits of
   the branch are its second-parent history; `git diff --stat
   b28ba45^1..b28ba45` is 7 files, +1412 -16; and `git log
   b28ba45..main` over the phase's paths is empty, so nothing of
   phase 1 landed outside it. `AUDIT_RANGE=b28ba45^1..b28ba45` is the
   whole phase, and `plan-push-audit-phase`'s accumulated-diff
   reasoning reduces to that one merge with nothing left over.

6. **"No `Merged` cell for this phase" is conditional, not flat.**
   The sketch stated it absolutely. `plan-phase-landing` in
   `PLAN-TEMPLATE.md` permits the omission only where the
   audit finds nothing: where it finds something, the findings pull
   request is the carrier and records this phase's merge commit, and
   the plan is not complete until those findings are resolved or
   declined in writing. Step 2g follows whichever case the audit
   produces.

7. **`PUSH-AUDIT.md` is as the phase assumes.** Wave 1 is
   `pre-commit run --all-files` plus the grep-level checks, which
   report rather than block; wave 2 is four independent sub-agents --
   2a code quality, 2b test review and 2c documentation review at
   sonnet and medium effort, 2d security review at opus and high
   (`PUSH-AUDIT.md:208-209, 409-410, 476-477, 613-614`). The runbook
   also carries the warning decision 2 is built on, and the rule that
   `REVIEWS.md` is named rather than pruned.

#### Decisions

1. **Execute this phase after the run due 2026-11-22T18:00Z, not
   before.** The audit of the diff could run today; the fleet check
   in step 2f cannot, because the run that first applies the
   criterion has not happened. Waiting costs a day of a plan that is
   otherwise finished, and buys the one observation nothing else in
   this plan can make: whether a criterion written against fixtures
   does to four real repositories what it was predicted to do. The
   alternative -- audit now, leave step 2f to the findings pull
   request -- is worse in a specific way: where the audit finds
   nothing there is no findings pull request, so there is no carrier
   left and the check quietly never happens. That is how the original
   note from 2026-10-18 came to sit unexecuted for a month.

2. **Every wave 2 brief carries the literal range, and every report
   states its totals.** `PUSH-AUDIT.md:50-64` is explicit that a
   brief handed over with `${AUDIT_RANGE:-origin/main...HEAD}` still
   in it expands, in the agent's own shell, to a diff against
   `origin/main` -- which for landed work is empty and reads as a
   clean review. So each brief is written with `b28ba45^1..b28ba45`
   substituted, names the range in prose, and requires the report to
   state insertions and deletions. A report that does not say `7
   files, +1412 -16` is re-run rather than triaged: it is the only
   check here that does not depend on the agent noticing its own
   mistake.

3. **Step 2f is read-only, and this phase dispatches nothing.** It
   reads the workflow run and the four issues. It does not file,
   close, comment on or edit an issue, and it does not
   `workflow_dispatch` the audit: a dispatch files and closes issues
   across twenty-four repositories, which is an outward action on
   other people's trackers and Mikal's to authorise. If the
   scheduled run has not happened or has failed when execution
   reaches this step, the record says so and states the prediction
   unverified; back brief gate 2 puts the dispatch question to him
   rather than this plan taking it.

4. **The five-item floor in `plan-push-audit-phase` is not run.** It
   applies where a repository has no `PUSH-AUDIT.md`; this one has
   one, and every phase of this plan landed here, so there is no
   out-of-repository landing to audit against the floor either. Said
   explicitly because a phase that silently skips a checklist and one
   that correctly does not need it look the same afterwards.

5. **Four stale claims are corrected in the planning commit**, not
   left for a step: the two in the sketch (survey items 1 and 2), the
   "daily audit" in the mission (item 3), and the execution note about
   the four repositories (item 4). A later step that re-derived them
   would be re-doing the survey.

#### Steps

Steps 2b to 2e are read-only readers and run in parallel, after 2a
passes; the runbook's own rule is that wave 2 is only worth spending
on if wave 1 passes. None of 2a to 2f commits anything: the
management session writes the record, and that record is the commit,
as step 2g.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a (wave 1) | medium | opus | none | Management session, inline: wave 1 of `PUSH-AUDIT.md`. `git fetch origin` first, then `export AUDIT_RANGE=b28ba45^1..b28ba45` and run every wave 1 command with it set -- `pre-commit run --all-files` on this branch, then each grep-level check over that range. Record every result, including the empty ones, because an empty grep is a result and a skipped grep is not. Confirm `git diff --stat "$AUDIT_RANGE" \| tail -1` reads `7 files changed, 1412 insertions(+), 16 deletions(-)`. A different line is not by itself a range error, so corroborate the range independently before concluding anything: `git rev-list --parents -n1 b28ba45` gives the two parents `6aefd63` and `4ddc693`, and `git rev-list --count b28ba45^1..b28ba45^2` gives 8, the commit count survey item 5 records. Where both hold and only the diffstat differs, the recorded figure is wrong rather than the range -- correct it in this plan and carry the corrected total into every wave 2 brief, because a halt here reads as a range error and stops a phase whose range is in fact right. Where either disagrees, the range is wrong and nothing below is worth running. Measure the second-parent count as `^1..^2` and not as `b28ba45^2..b28ba45`, which reads 15: it collects the first parent's commits since the merge base as well, so it fails this gate on a correct range. The range touches no `templates/shared-blocks/` file, so the version-bump question does not arise; say so rather than omitting it. Name the review marks the range staled rather than pruning or regenerating `REVIEWS.md` -- for a merged range that is naming history, since `prune-reviews` already healed them on the push to main, so it is a line of the record rather than work to do. Keep the grep output verbatim: step 2b's runbook section opens by taking it as input (`PUSH-AUDIT.md:213-217`), and a sub-agent inherits nothing, so what is not pasted into that brief does not reach it. |
| 2b (runbook 2a, code quality) | medium | sonnet | none | Section 2a of `PUSH-AUDIT.md`, code quality, read-only, at the models the runbook sets. Substitute `b28ba45^1..b28ba45` into every command in the brief; do not leave `${AUDIT_RANGE:-origin/main...HEAD}` anywhere, and state the insertion and deletion totals measured. Paste step 2a's wave 1 results into the brief inline -- the TODO/FIXME hits, the new suppressions, the new third-party import lines, and the `wc -l` list of touched files over 800 lines, which for this range is `scripts/audit/checks/packaging.py` at 2,493 and `scripts/tests/test_packaging_renovate.py` at 1,140 -- because the section opens by taking that report as input and triaging each item, and an agent handed no report will either re-derive the greps or skip the triage the section is built around. The six-file rule is the one to read closely here: phase 1 deliberately added a finding to the existing `renovate` criterion rather than a criterion of its own, so five of the six files are *correctly* untouched and `scripts/tests/test_metadata.py` is correctly unchanged -- the defect to look for is the opposite one, a finding whose message or helper behaves like a separate criterion. Read `scripts/audit/checks/packaging.py` whole, not only its hunks: it is 2,493 lines, over the 1,500-line line, and its baseline entry was raised twice in this range. The split is filed as #434 and is not a finding. |
| 2c (runbook 2b, test review) | medium | sonnet | none | Section 2b of `PUSH-AUDIT.md`, test review, read-only, same substitution and the same totals requirement. The tests in range are `RenovateShellcheckVersioningTest` in `scripts/tests/test_packaging_renovate.py` (19 cases) and the additions to `RenovateFleetPresetTest`. Worth testing against the claim rather than the count: the phase asserts that deleting the `issues.append` block from `Renovate.run` fails these tests, and that the preset-loading test holds the real `renovate-fleet.json` to carrying the rule. Say which of the 19 would survive the deletion. The two holes decision 3 leaves open -- an unvalidated `versioning` string, and a rule narrowed away from the `pre-commit` manager or disabled -- are pinned by tests on purpose; judge whether the tests pin what the decision says they pin. |
| 2d (runbook 2c, documentation review) | medium | sonnet | none | Section 2c of `PUSH-AUDIT.md`, documentation review, read-only, same substitution and totals. In scope: `docs/audits/renovate.md` and the plan file. The specific question is whether the specification and the code say the same thing after the first review round changed the matcher. The spec already describes the `rule_covers()` behaviour (`docs/audits/renovate.md:330-348`: any non-empty `versioning` string, exact, glob and `/regex/` entries, `matchDepNames`, the migrated spellings, and both deliberate holes), so the work is not to find out whether it was updated but to compare it line by line against `renovate_config_versions_shellcheck_py` (`scripts/audit/checks/packaging.py:125-158`) and `rule_covers` (`:924`) and report any claim the code does not keep. The plan's own code citations are in scope here too: two of phase 1's survey citations had drifted by one review round and were corrected in this phase, which is the failure mode to look for rather than a settled one. Check also that the four-component tag reasoning is stated in full in exactly one place outside the plan and the preset's own `description` -- the plan's own definition of done asserts this with two greps, and a doc reviewer reading the prose may find a third statement the greps miss. |
| 2e (runbook 2d, security review) | high | opus | none | Section 2d of `PUSH-AUDIT.md`, security review, read-only, same substitution and totals. The blast radius here is other people's issue trackers: a criterion that reports wrongly files an issue in twenty-four repositories, and a criterion message is quoted into those issues verbatim. Two specifics beyond the runbook's brief: whether any finding text in the range can carry content from a repository under audit into an issue body unescaped, and whether the message added here contains anything that acts as a bot trigger when quoted into a GitHub issue, as the `ci-review-automation` finding text does. Nothing in range touches a token, a workflow permission or a template the fleet copies; confirm that rather than assuming it. |
| 2f (fleet check) | medium | opus | none | Management session, inline, read-only. The absence of a shellcheck sentence is evidence only once three preconditions hold; where any of them cannot be established the verdict is "unverified", recorded as such and handed to back brief gate 2, never "prediction falsified". The preconditions are listed rather than the ways of getting the verdict wrong, because three separate holes of that shape were found one at a time while this phase was being planned, and a list defaults the next one to unverified rather than to falsified. **(i) A run that carries the criterion exists.** `gh run list --workflow consistency-audit.yml --branch main -L 5 --json databaseId,createdAt,event,conclusion,status`, with no filter on the event, and the newest row's `createdAt` must be later than phase 1's merge at 2026-11-21T22:44Z. A dispatch authorised through back brief gate 2 satisfies this precondition exactly as a scheduled run does, and the listing must be able to see it: `manage-issues` is gated on `github.ref == 'refs/heads/main'` rather than on the event (`.github/workflows/consistency-audit.yml:250`), so a dispatch of main applies the criterion and files the issues, and `--event schedule` would hide the one run the gate exists to authorise and record unverified what was in fact verified. Record the run's event beside its id either way. Anything older predates the criterion -- as of planning the newest is 2026-11-15T22:14:48Z, `#43768205317`, finished and successful, so it passes a liveness check while having never applied the criterion -- and the record reads "the run that first applies the criterion has not happened". **(ii) That run reached issue management.** `gh run view <id> --json jobs`, and the `manage-issues` job (`.github/workflows/consistency-audit.yml:221`) must have concluded `success`. A leg of the 24-repository `audit` matrix can fail, the run can be cancelled by its concurrency group, and a run that stopped before `audit-manage-issues.py` leaves four untouched issues that look exactly like a falsified prediction, so the run's conclusion gates the verdict rather than merely being recorded beside it. **(iii) The repository was measurable.** Where the shellcheck sentence is absent for a repository, re-measure that repository's default branch by step 1b's method -- fetch it, `git archive` it to a scratch directory, run `audit-check.py` against the export -- before writing anything down. Phase 1's own run produced real "could not measure" results, so a re-measurement that errors is this precondition failing and not a criterion passing. With all three holding, record the run's id and conclusion, then for each of `agent-python`, `client-python`, `client-python-k3s` and `client-python-ova` the `Consistency: Renovate` issue number, its `updatedAt`, and whether its body carries the shellcheck sentence. Survey item 4 says what counts as confirmation for each: an edited body with a newer `updatedAt` for the first three, a newly filed issue number for `client-python-ova`. Absent evidence is not a falsification even with the preconditions met: phase 1's risk 3 and decision 4 both contemplate `PLAN-renovate-cadence.md` phase 3 or `consistency-fix` giving one of the four the rule first, so "rule acquired since planning, finding correctly absent" is a third outcome beside confirmation and falsification. Then sweep the other half of the success criterion, which says the finding appears in those four "and of no others": derive the repository list from `scripts/audit/scope.py`'s `matrix_repos()` rather than by hand, since it raises rather than returning a partial list, and for each of the 18 remaining repositories run `gh issue list --repo shakenfist/<repo> --label consistency --state open --json number,title,body` and grep the `Consistency: Renovate` body for `shellcheck-py`. Record the repositories swept by name with their count, and record `images` and `private-ci` as scoped out by `REPO_OVERRIDES` rather than as swept, so a repository the sweep missed is visible in the record instead of hidden inside a negative result -- the standard step 2a sets for an empty grep. File nothing, close nothing, comment on nothing, and do not dispatch the workflow (decision 3). |
| 2g (record) | medium | opus | none | Management session, inline: the record and the close-out. One commit, subject `Record the renovate shellcheck push audit.` Write what each wave found into the *Record* section below, including "nothing", which `plan-push-audit-phase` says is worth a sentence of its own; head each wave 2 verdict with the runbook's own section name -- code quality, test review, documentation review, security review -- rather than a letter, because the plan's step letters and the runbook's section letters are offset by one and the record outlives the mapping. Set this phase's `Status` to `Complete` in the Execution table either way. The plan's row in `docs/plans/index.md` is conditional, and so is the `Merged` cell: where the audit found nothing, set the index row to `Complete` and leave `Merged` empty; where it found something, leave the index row `In progress`, because `plan-push-audit-phase` says the plan is not complete until the findings are resolved or declined in writing, and say in the record that the findings pull request is the carrier for both that row and this phase's merge commit (survey item 6). Also record which of the runbook's ten management-session checklist items (`PUSH-AUDIT.md:695-716`) the audit answered and which are void because the work is already pushed -- generated files, criterion specs, shared-block bumps and review marks still apply to a landed range; "fixed and re-verified before push", "branch up to date" and "ready to push" cannot, and saying so is the same standard decision 4 sets for the five-item floor. Run `pre-commit run --all-files` before proposing the commit. |

#### Record

Written by step 2g. It holds wave 1's results including the empty
greps, one verdict per wave 2 reader headed by the runbook's own
section name -- code quality, test review, documentation review,
security review -- the fleet check from step 2f, and, if the audit
found nothing, the one sentence saying so.

#### Risks and mitigations

* **A wave 2 agent reads an empty diff and reports a clean review.**
  The failure is silent and it looks exactly like success. Decision 2
  is the mitigation and the management session checks it: a report
  that does not state `7 files, +1412 -16` is re-run, not triaged.
* **The scheduled run slips, fails, or lands while the phase is
  mid-flight.** Observed starts are three to four hours after the
  cron time, and a failed run is possible. Step 2f gates its verdict
  on three preconditions -- a run newer than phase 1's merge, a
  `manage-issues` job that concluded `success`, and a repository that
  re-measures -- so a slipped, failed or cancelled run is an
  unverified prediction rather than a wrong record, and back brief
  gate 2 decides whether to wait or to ask Mikal for a dispatch.
* **A pre-existing problem is recorded as a finding of this plan.**
  The template-drift failures measured on 2026-11-21 (since cleared
  by `f373abc`), #434 and #412 all sit in the range's neighbourhood
  and none is this plan's doing. Every wave 2
  brief names its boundary, and the management session checks each
  finding against `b28ba45^1..b28ba45` before writing it down.
* **This branch's own diff is one plan file, so wave 1 run without
  the range reads as nothing to audit.** `AUDIT_RANGE` is set
  explicitly in 2a before any command runs, and the file-count check
  in the same step fails loudly if it is not.

#### Definition of done

1. `pre-commit run --all-files` passes on this branch.
2. Wave 1 ran with `AUDIT_RANGE=b28ba45^1..b28ba45`, and the record
   carries a result for every wave 1 grep, including each one that
   had no hits.
3. The diffstat of `b28ba45^1..b28ba45` was measured and recorded --
   `7 files changed, 1412 insertions(+), 16 deletions(-)` as
   transcribed at planning, or a corrected figure with the correction
   noted -- and each of the four wave 2 reports states totals
   matching the recorded figure. Any report that did not was re-run,
   and the record says so. The range itself was corroborated
   independently -- two parents, `6aefd63` and `4ddc693`, and eight
   commits in `b28ba45^1..b28ba45^2` -- so a diffstat mismatch while
   those two hold is a wrong figure to correct rather than a wrong
   range.
4. The record carries a verdict headed by each of the runbook's four
   wave 2 section names -- code quality, test review, documentation
   review, security review -- with "nothing found" written out where
   that is the result.
5. The record names the run it read, by id and event, with a
   `createdAt` later than 2026-11-21T22:44Z, and then for each of
   `agent-python`, `client-python`, `client-python-k3s` and
   `client-python-ova` the issue number, its `updatedAt` and whether
   its body carries the shellcheck finding -- the sentence itself,
   not merely a newer `updatedAt`, which any unrelated drift in the
   body moves. The record also carries one line for the other half of the
   success criterion: that no other in-scope repository's
   `Consistency: Renovate` issue mentions `shellcheck-py`, naming the
   repositories swept and their count, with `images` and `private-ci`
   recorded as scoped out rather than swept. Each of step 2f's three
   preconditions is recorded as met or not met, and where any is not
   met the item is unverified rather than passed. None of a run older
   than the merge, a run whose `manage-issues` job did not conclude
   `success`, and a repository that could not be re-measured counts as
   a verification.
6. Nothing was written outward without authority: no issue in the
   four repositories has a comment or edit from this session, and
   this session dispatched nothing on its own. Where the operator did
   not authorise a dispatch, `gh run list --workflow
   consistency-audit.yml --event workflow_dispatch -L 3` shows no run
   created after this phase was planned -- the newest at planning was
   `#41140043392`, 2026-10-30T22:44:00Z, recorded in survey item 3.
   Where he did, through back brief gate 2, the record names who
   authorised it, when, and the run id, and that run satisfies this
   item rather than breaching it.
7. The Execution table reads phase 1 `Complete` with `b28ba45` in its
   `Merged` cell, and phase 2 `Complete`. Where the audit found
   nothing, phase 2's `Merged` cell is empty and the plan's row in
   `docs/plans/index.md` reads `Complete`. Where it found something,
   that row still reads `In progress` and the record names the
   findings pull request that carries both it and the `Merged` cell.
8. `python3 scripts/audit-check.py --repo-path . --repo-name
   development --github-org shakenfist` reports no new finding for
   `plan-index` or `plan-template`. `plan-phase-references` is not
   named: it skips any file under a `plans/` directory at any depth
   (`docs/audits/plan-phase-references.md:25-26`), and this diff is
   confined to `docs/plans/`, so it could only ever pass.

#### Back brief

Before executing any step of this phase, back brief the operator.
Two things need answering, and each changes the phase:

1. **Decision 1 -- wait for the 2026-11-22T18:00Z run.** The phase as
   planned does the diff audit and then waits for the weekly run
   before step 2f, which puts its completion on Monday morning AEDT.
   The alternative is to audit the diff now and finish, leaving the
   fleet check to the findings pull request -- which does not exist
   if the audit finds nothing.
2. **Decision 3 -- what to do if the run slips or fails.** The phase
   will not `workflow_dispatch` the audit on its own, because a
   dispatch files and closes issues across twenty-four repositories.
   If the run has not landed when execution reaches step 2f, or has
   landed without its `manage-issues` job concluding `success`, the
   options are to wait for the next one, to record the prediction
   unverified, or for Mikal to authorise a dispatch.

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

The plan is done when the `renovate` criterion reports the missing
`shellcheck-py` versioning rule, the specification says so, and the
repositories it fails are a recorded number rather than a guess:

* A repository using the `shellcheck-py/shellcheck-py` pre-commit hook
  without a `versioning` `packageRule` for it -- locally or through
  `local>shakenfist/development:renovate-fleet` -- fails the `renovate`
  criterion, and the message says the `github-tags` lookup fails so the
  revision is never bumped.
* Extending the fleet preset alone satisfies the check, held by a test
  that loads the real `renovate-fleet.json`.
* No new criterion id, spec page, `docs/audits/README.md` row or
  frozen-metadata row was created.
* The four repositories the criterion newly fails are named in the
  plan, measured by running the audit rather than by reading configs.
  The shellcheck finding appears in the `renovate` issue of those four
  and of no others -- not that only four issues are filed: 18
  repositories failed that criterion on the template drift `90f1ea8`
  introduced when this was written, cleared by `f373abc` on
  2026-11-21, so counting issues would measure somebody else's
  work.
* The four-component tag reasoning is stated in full in one place
  outside the plan and the preset's own `description`.
* `images` and `private-ci` are recorded in *Future work* as unwatched
  rather than left to be rediscovered, with the reason they cannot be
  reported.

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

Once a plan has been finished for 90 days it is retired as
`docs/plan-retirement.md` describes: its reasoning is moved into
docs/ and its *Future work* is filed as issues. Write both so they
survive being read by someone who was not here.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

* **`images` and `private-ci` carry the hook and no rule, and no
  criterion can report them.** Both are scoped by `only_checks` in
  `REPO_OVERRIDES` (`scripts/audit/repo.py:127-148`): `images` to
  `eol-distro` alone, `private-ci` to seven plan and vendor checks.
  `private-ci` has no `renovate.json` at all and is not
  renovate-managed, so its `v0.10.0.1` pin will never move; `images`
  is at `v0.11.0.1` by hand and will not move again. Widening either
  repository's `only_checks` is a decision about the exemption rather
  than about this criterion -- both exemptions were taken deliberately
  and are argued at length in that file -- so it is not this plan's to
  take. Worth revisiting when `private-ci` next adopts a fleet
  convention, which the plan-family note in `repo.py` shows it
  periodically does.
* **The criterion accepts a `versioning` rule it has not proven
  works.** Decision 3 and the second risk record why. Requiring the
  preset form instead becomes possible once
  `PLAN-renovate-cadence.md` phase 3 has swept every `renovate.json`
  onto it, and would collapse this finding into "extends the preset",
  which that plan's own *Future work* already contemplates for other
  reasons.
* **`scripts/audit/checks/packaging.py` has no renovate seam.** This
  phase took it from 2,413 to 2,493 lines and raised its
  `tools/source-size-baseline` entry by hand rather than split it,
  because a 2,400-line restructure around a 63-line change is the
  wrong diff to review together. The seam is obvious and the tests
  already use it -- `scripts/tests/test_packaging_renovate.py` exists
  beside `test_packaging.py` -- so a `checks/renovate.py` holding the
  renovate constants and helpers, `Renovate` and
  `RenovateLockstepGroups` would follow the split the tests already
  made. Measured at 579 lines across five regions, none of them
  called from outside the set: moving them leaves `packaging.py` at
  1,914 lines, still in the baseline, and the new module under the
  threshold. Every line the first review round added fell inside
  those regions, so the remainder is unchanged by it. Filed as #434,
  which carries the measurement, as #412 is filed for the test
  module.
* **Nothing checks that a repository's local copy of the rule matches
  the preset's.** Measured byte-identical across all 14 on 2026-11-21,
  and `PLAN-renovate-cadence.md` phase 3 deletes the local copies, so
  a drift check would be built just in time to become dead. Recorded
  rather than filed.

### Bugs fixed during this work

* Renovate never bumps `shellcheck-py/shellcheck-py` in a repository
  without the `versioning` rule, so the hook's revision ages silently
  while every other dependency in the tree stays current. Fixed for
  the fleet's configuration by `dc2f6ed` (#275) and
  `PLAN-renovate-cadence.md` phase 1 (`47086db`); this plan makes the
  absence reportable, which is what stopped it being fixed in
  `agent-python`, `client-python`, `client-python-k3s` and
  `client-python-ova`, whose pins have been 15 months behind since
  2025-08-09.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
