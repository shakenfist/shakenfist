# Consistency audit compliance

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the CI
workflows under `.github/workflows/`, the pre-commit
configuration in `.pre-commit-config.yaml`, the review
tracking tooling in `tools/review-tracking.sh` and
`REVIEWS.md`, the vendored sfui copy under
`kerbside/api/static/sfui/`, and the shared blocks embedded
in `PLAN-TEMPLATE.md` and `PUSH-AUDIT.md`). Ground your
answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall proxy architecture
and `AGENTS.md` for build commands, project conventions, and
code organisation. This plan is unusual in that most of its
authority lives in another repository -- read the audit
specification before implementing any phase, because the
issue text is a generated summary of it and has already been
shown to be stale or wrong:

- `shakenfist/development` -- the canonical audit
  specifications under `docs/audits/`, the canonical shared
  blocks under `templates/shared-blocks/`, and the checker
  itself in `scripts/audit-check.py`. A finding is only as
  good as the check that produced it, and one of the six
  findings this plan opens with turned out to be a defect in
  the checker rather than in kerbside.
- `shakenfist/sfui` -- the canonical design system, vendored
  into `kerbside/api/static/sfui/` by that repository's
  `tools/vendor.sh`.
- `shakenfist/actions` -- the shared Claude Code review
  automation that supersedes the retired comment addresser.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

A daily consistency audit runs in `shakenfist/development`
against every repository in the ecosystem, checking each
against a set of specifications under that repository's
`docs/audits/`. Where a repository fails a check, the audit
files a GitHub issue labelled `consistency` naming the
audit, linking its specification, and quoting the automated
check's own description of what is missing.

Kerbside carries six such open issues, filed between
2026-08-03 and 2026-08-28:

| Issue | Audit | Finding as filed |
|-------|-------|------------------|
| [#227](https://github.com/shakenfist/kerbside/issues/227) | Human review coverage | `0 of 152 in-scope files reviewed at HEAD; 152 need review (threshold 5)` |
| [#359](https://github.com/shakenfist/kerbside/issues/359) | LLM context linting in pre-commit and CI | `skillsaw does not run from a CI workflow` |
| [#360](https://github.com/shakenfist/kerbside/issues/360) | CI review automation | `the retired comment addresser is still deployed` |
| [#368](https://github.com/shakenfist/kerbside/issues/368) | Plan template | `missing shared block plan-push-audit-phase` |
| [#370](https://github.com/shakenfist/kerbside/issues/370) | Pre-push audit file | `missing shared blocks path-traversal-review, python-version-discipline, functional-test-coverage` |
| [#373](https://github.com/shakenfist/kerbside/issues/373) | sfui vendored copy | `2 commit(s) behind canonical` |

This plan file previously existed under a different remit.
From 2026-07-16 it was a standalone plan called *Consistency
audit deferred work*, tracking five checkboxes for GitHub
settings that could only be changed through the web
interface. Three were completed and verified in July; two
remained. That plan never grew to cover the audit findings
themselves, so the six issues above accumulated with no
planning document tracking them, while a document named for
the consistency audit sat at `In progress` describing
something much narrower. This rewrite promotes the file to a
master plan covering the whole compliance backlog, and folds
the two surviving checkboxes into phase 1 as the residue
they are.

### What the survey found

Every finding above was checked against the tree on
2026-08-29 before this plan was written, rather than being
taken from the issue text. Four of the six survived intact.
Two did not, and both corrections change what the work is.

**#359 is a defect in the checker, not in kerbside.**
Kerbside runs skillsaw in CI.
`.github/workflows/functional-tests.yml` lines 267-272
install `skillsaw==0.18.0` into the test venv and run
`skillsaw --no-custom-rules .` in the `sanity_checks` job,
and `.pre-commit-config.yaml` lines 54-57 carry the hook at
the matching rev. The audit's own specification says
outright that "*how* skillsaw is invoked is deliberately not
pinned", but `check_llm_context_lint_ci()` in
`scripts/audit-check.py` (lines 5571-5626) passes the CI
half only if a workflow mentions the literal string
`stbenjam/skillsaw` outside a comment, or runs `pre-commit
run`. Kerbside does neither: it installs from PyPI, and the
one mention of `stbenjam/skillsaw@v0` in the workflow is
inside a comment, which `file_mentions()` deliberately
excludes. The checker already carries an escape hatch for
exactly this shape -- its comment at lines 5396-5402 notes
that `development`'s own `consistency-audit.yml` "installs
skillsaw from PyPI and so never names the upstream
repository either" -- but that hatch is spelled as
`pre-commit run`, which kerbside does not use. Kerbside's
workflow explains at length (lines 249-255) why it cannot
use the composite action: `stbenjam/skillsaw@v0` opens with
`actions/setup-python` pinned to 3.11, `actions/python-versions`
publishes no Debian 12 build, and these runners carry no
tool cache, so the action fails before it lints anything.
That reason is real and is not kerbside's to fix. The remedy
is upstream, in `development`, and phase 3 carries it.

**#227 is badly out of date, in kerbside's favour.** The
issue was filed on 2026-08-03 and says `0 of 152 in-scope
files reviewed`. `tools/review-tracking.sh status` at HEAD
reports **124 of 194 in-scope files reviewed, 70 needing
review**. The backlog is real -- the threshold is 5 -- but
it is a third of what the issue claims, and the issue's list
of missing files names paths that no longer exist
(`.claude/skills/add-database-migration.md` is now
`.claude/skills/add-database-migration/SKILL.md`, and
`alembic/versions/` moved into the package during
demo-install phase 1). Nothing needs correcting at the
source here: the audit recomputes coverage against HEAD on
every run and re-states the issue body, so the staleness is
in the rendered issue text rather than in any file this
repository owns. It is recorded here so that a reader sizing
phase 4 does not budget for 152 files.

The other four held, with two details worth carrying into
the phases:

- **#373 is a provenance stamp bump and nothing else.**
  `kerbside/api/static/sfui/.sfui-commit` records
  `190383aecb31`; sfui's default branch (`develop`, not
  `main`) is at `c3f65ae0aa0d`. The diff between them
  touches exactly one file,
  `.github/workflows/renovate.yml`, which
  `tools/vendor.sh` does not distribute. Re-vendoring will
  therefore change the stamp and no asset. That makes the
  phase cheap, but it also means the usual justification
  for re-vendoring -- propagating an improvement -- does
  not apply, and the change must be defended on provenance
  grounds alone.
- **#360's deletion is wider than the four files it
  names.** Beyond `.github/workflows/pr-address-comments.yml`,
  `tools/address-comments-with-claude.sh`,
  `tools/render-review.py` and `tools/review-schema.json`,
  the tree also carries
  `kerbside/tests/unit/test_render_review.py`, which
  imports `tools/render-review.py` by path and would fail
  at collection the moment the script goes; a row in
  `.claude/CLAUDE.md` (line 139) listing the workflow;
  three cross-references in
  `.github/workflows/pr-re-review.yml` (lines 10, 34 and
  106); one in `tools/shellcheck-wrap.sh` (line 7); and
  four review marks plus their weaudit entries. Deleting
  only the four named files leaves a repository whose unit
  tests do not run.

  *Corrected during phase 2 planning:* this bullet
  originally named one `pr-re-review.yml` reference and no
  `shellcheck-wrap.sh` reference. The line 34 one is the
  awkward case -- it is the stated reason for a live
  `if:` guard that must outlive the workflow it cites.

The two surviving GitHub-settings checkboxes were also
re-checked against the API rather than the web interface:

- **Delete branch on merge** -- already enabled
  (`delete_branch_on_merge: true`). The checkbox was simply
  never ticked.
- **Allow auto-merge** -- genuinely disabled
  (`allow_auto_merge: false`). Decided against in this plan
  rather than left open; see decision 1.

## Mission and problem statement

Bring kerbside into compliance with every consistency audit
it currently fails, and leave behind a planning document
that tracks the audit backlog as a whole rather than a
subset of it.

One audit is an exception, deliberately. `review-coverage`
is not failing because anything in the repository is
missing; it is failing because a hundred files have not yet
been read by a person, and no amount of planning shortens
that. This plan builds what the reading needs and leaves the
reading to #227. Decision 5 sets out why.

The problem is not any individual finding -- five of the six
are small, and one of those is not kerbside's bug at all.
The problem is that the backlog has no owner. Issues arrive
daily from another repository, land in a label, and are
worked on when somebody notices them; the two oldest have
been open since early August. A repository whose compliance
drifts silently is exactly the failure mode the audit exists
to prevent, so the audit's own findings deserve the same
treatment as any other planned work.

A second, narrower problem: the audits delegate real content
to shared blocks, and kerbside is missing four of them.
`PUSH-AUDIT.md` is the checklist run over every plan's
accumulated diff before it is pushed, so three missing
blocks there mean three criteria -- path traversal review,
Python version discipline, functional test coverage -- are
not being applied to any of this repository's work.
`PLAN-TEMPLATE.md` is missing `plan-push-audit-phase`, which
is the block that makes the push audit phase mandatory and
defines the `Merged` column that records what to audit.
Those are not cosmetic gaps.

## Open questions

None outstanding. The two that existed when this file was a
standalone plan -- whether to enable auto-merge, and whether
the settings work was worth tracking at all -- are settled
by decision 1 and by this rewrite respectively.

The question phase 3 turns on is not open so much as
unanswerable from inside this repository: whether
`development` would rather widen the skillsaw check or have
kerbside change its invocation. Phase 3 proposes the former
and says why, but the decision belongs to that repository's
maintainer and the phase is scoped to making the case, not
to forcing it.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Shared blocks, vendor stamp and settings closeout | [PLAN-consistency-audit-phase-01-blocks-and-stamp.md](/components/kerbside/plans/PLAN-consistency-audit-phase-01-blocks-and-stamp/) | Complete | cbca9b1 |
| 2. Retire the comment addresser | [PLAN-consistency-audit-phase-02-retire-addresser.md](/components/kerbside/plans/PLAN-consistency-audit-phase-02-retire-addresser/) | Complete | 5f3c80c |
| 3. Skillsaw CI detection, upstream | [PLAN-consistency-audit-phase-03-skillsaw-detection.md](/components/kerbside/plans/PLAN-consistency-audit-phase-03-skillsaw-detection/) | Complete | 16e6173 |
| 4. Review scope and session scaffolding | [PLAN-consistency-audit-phase-04-review-coverage.md](/components/kerbside/plans/PLAN-consistency-audit-phase-04-review-coverage/) | Complete | ade2788 |
| 5. Diagram discipline and mermaid linting | PLAN-consistency-audit-phase-05-diagram-discipline.md | Not started | |
| 6. Push audit | PLAN-consistency-audit-phase-06-push-audit.md | Not started | |

Phase sketches (to be expanded into per-phase plans):

**Phase 1 -- shared blocks, vendor stamp and settings
closeout.** Resolves #368, #370 and #373. Copy the four
missing shared blocks verbatim from
`shakenfist/development`'s `templates/shared-blocks/`:
`plan-push-audit-phase` into `PLAN-TEMPLATE.md`, and
`path-traversal-review`, `python-version-discipline` and
`functional-test-coverage` into `PUSH-AUDIT.md`. Re-vendor
sfui from a current checkout so `.sfui-commit` names
canonical HEAD. Tick the *delete branch on merge* checkbox
against the API evidence and record *allow auto-merge* as
decided against. Docs and static assets only; no Python
changes and no behaviour change.

**Phase 2 -- retire the comment addresser.** Resolves #360.
Delete `.github/workflows/pr-address-comments.yml`,
`tools/address-comments-with-claude.sh`,
`tools/render-review.py` and `tools/review-schema.json` in
one commit, as the audit requires, along with
`kerbside/tests/unit/test_render_review.py` which cannot
survive them. Remove the `.claude/CLAUDE.md` row and repair
the three `pr-re-review.yml` cross-references and the one in
`tools/shellcheck-wrap.sh`, keeping the bot-comment guard
whose justification one of them is. Prune the four review
marks. The security argument is the point of the phase and
belongs in the commit message: the workflow holds
`contents: write` on the pull request branch, for automation
that has been superseded by
`shakenfist/actions/review-pr-with-claude@main` and is no
longer used.

**Phase 3 -- skillsaw CI detection, upstream.** Addresses
#359, but the change lands in `shakenfist/development`, not
here. Widen `check_llm_context_lint_ci()` so a workflow that
demonstrably runs the linter satisfies the CI half however
it installs it, matching the specification's own stated
intent. Add a test to `scripts/test_audit_check.py` covering
kerbside's shape -- PyPI install, no `pre-commit run`, the
upstream name appearing only in a comment. Kerbside's own
issue closes when the next audit run passes; nothing in this
repository changes. If `development` declines, the fallback
is to record the divergence here and ask for a
`REPO_OVERRIDES` exemption rather than to break a CI step
that works.

**Phase 4 -- review scope and session scaffolding.**
Resolves the `review-scope-completeness` check and builds
the scaffolding the human review runs on: a scope
configuration that names every tracked file, a session
recipe in `docs/development.md` that a reader can follow
without opening the upstream document, and a tranche order
that front-loads the files where a review is most likely to
find something (`kerbside/api.py`,
`kerbside/proxy_supervisor.py`, `kerbside/sf_token.py`,
`kerbside/sources/ovirt.py`, and the Jinja templates that
render the endpoints the open security issues concern).
Every mark is a signed commit, so the phase settles where
the signing configuration lives before any reading starts.

**The reading itself is out of scope, and #227 is not this
plan's to close.** See decision 5. The phase delivers the
scaffolding and stops; the 104 files are read in separate
sessions on their own clock, tracked by the issue alone.

*Corrected during phase 4 planning:* three of this sketch's
claims did not survive contact with the tree.

- The backlog is **77 files, not 70** -- the 70 was measured
  on 2026-08-29, before phases 2 and 3 and the renovate
  merges landed.
- **The bulk is not `docs/spice/`.** Those 9 files are the
  smallest group of the six. The distribution is `kerbside/`
  25, `tools/` 17, `docs/` 17, and 7 repository-root files
  including `AGENTS.md` and `PUSH-AUDIT.md`.
- **The signing prerequisite passes.** *This bullet
  originally said the opposite, and was corrected on
  2026-09-02.* The survey read `N` from `git log
  --format='%h %G? %s'` as "unsigned", but `%G?` verifies
  against the current clone's `gpg.format`, and a
  development clone with none set cannot parse gitsign's
  x509 signature and reports `N` for a valid one. Testing
  the commit object instead (`git cat-file commit <sha> |
  grep '^gpgsig'`) finds **30 signed** mark-adding commits,
  continuously since 2026-08-14, alongside 37 correctly
  unsigned bot prunes. Three marks from before that date
  are unsigned. The real question the phase had to settle
  was which clone holds the configuration, not whether
  anyone had ever run it.

The phase also absorbs a check that did not exist when this
sketch was written. `review-scope-completeness` landed
upstream on 2026-08-30 and fails with 44 orphaned files; it
is folded in here rather than given a phase, because
narrowing scope is the cheapest way to close a
review-coverage issue and settling scope after the grind
would mean redoing part of it. Fixing scope moves the
in-scope count from 192 to 227 and the backlog from 77 to
112, which is the honest number and a worse-looking one.

**Phase 5 -- diagram discipline and mermaid linting.**
Resolves #370 and #381, both of which arrived on 2026-08-29
while phase 2 was in flight, when `shakenfist/development`
added a `diagram-discipline` shared block and an accompanying
`mermaid-lint-ci` audit. Copy the `diagram-discipline` block
verbatim into `PUSH-AUDIT.md`, and copy
`templates/mermaid-lint/` to give the repository a
`tools/mermaid-lint.sh` and a workflow that runs it. Note that
#370's issue body is stale: it still lists the three shared
blocks phase 1 added, all of which are present on develop, and
the audit does not refresh an open issue's body. Its only live
finding is the missing `diagram-discipline` block. These two
issues are one upstream change and are deliberately kept in one
phase rather than split to clear a failure count sooner.

**Phase 6 -- push audit.** Work through `PUSH-AUDIT.md` over
the accumulated diff of phases 1, 2 and 4 against `develop`
-- phase 3 lands in another repository and is audited there,
as part of the pull request that lands it. Name the commit
range explicitly, from the `Merged` column above, and
substitute it wherever `PUSH-AUDIT.md` says `git diff
develop...HEAD`. Note the ordering hazard: phase 1 adds
three criteria to `PUSH-AUDIT.md`, so this phase runs a
checklist that phase 1 changed, and must be run from the
version of the file that phase 1 produced.

### Phase status

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

## Decisions

**Decision 1 -- auto-merge stays disabled.** The standalone
plan's checkbox asked us to confirm *Allow auto-merge* was
enabled; the API says it is not. Rather than enable it to
satisfy a checkbox written before the merge queue existed,
this plan records it as decided against. Auto-merge lands a
pull request the moment its required checks pass; the merge
queue, live since 2026-08-09, exists precisely so that
nothing lands without being retested against the develop tip
it will actually sit on, and two-tier CI puts the oVirt and
OpenStack lanes *only* in the queue. Enabling auto-merge
alongside it adds a second landing path that skips the merge
tier's entire point. No consistency audit asks for it --
there is a `delete-branch-on-merge` audit and a
`merge-queue-config` audit, and neither mentions auto-merge.
The checkbox is retired, not ticked.

**Decision 2 -- #359 is fixed upstream, not worked around.**
The tempting cheap fix is to make kerbside's CI step name
`stbenjam/skillsaw` so the checker's string match succeeds --
a `uses:` line, or even editing the comment. Both are
refused. The composite action genuinely does not work on
these runners for a documented reason, so switching to it
would trade a green audit for a red CI lane; and satisfying
a substring check by rewording a comment is precisely the
kind of compliance theatre that makes an audit worthless.
The check is wrong against its own specification, so the
check is what changes. This is the decision most likely to
be argued with, because it makes kerbside's issue depend on
another repository's maintainer accepting a patch, and
leaves #359 open in the meantime. The alternative is worse:
a repository that games the audit teaches every other
repository to do the same.

**Decision 3 -- phase 1 is grouped by risk, not by issue
age.** The natural grouping would put all five small
findings in one phase. Instead #360 is split out, because
deleting a workflow that holds `contents: write`, four
tools, and a unit test is a different kind of change from
copying a documentation block, and it deserves its own
commit message making the security argument and its own
review. Phase 1 is the changes where the diff can be
verified by comparison against a canonical source; phase 2
is the change where something has to be reasoned about.

**Decision 4 -- the master plan is rewritten in place rather
than superseded.** `PLAN-consistency-audit.md` keeps its
filename, its 2026-07-16 date and its index row, moving from
the *Standalone plans* table to *Master plans*. Superseding
it with a new file would strand the three completed security
checkboxes and lose the history of why they were tracked.
The old remit survives as phase 1's settings closeout.

**Decision 5 -- the human reading is out of scope; this
plan builds the scaffolding for it.** Phase 4 was originally
written to run until the review backlog dropped below five,
which would have kept this plan `In progress` for as long as
it takes one person to read a hundred files -- weeks, at a
rate nobody had measured. That is the wrong instrument. A
plan tracks work that planning makes go faster, and reading
source code is not that: the sequencing is worth deciding
once, but after that the plan has nothing left to contribute
and only reports a number the audit already reports better.

So the boundary is drawn at the scaffolding. In scope: a
scope configuration that names every tracked file, a
documented session recipe, a tranche order, and the settled
question of where signing configuration lives. Out of scope:
the reading. #227 stays open and is sufficient on its own --
it is recomputed against HEAD daily, it names exactly which
files remain, and it closes itself when a passing audit run
says so. Duplicating that into a status column adds a second
place to be stale.

Two consequences a reader should not be surprised by. This
plan can reach `Complete` while `review-coverage` is still
failing, which looks wrong against the mission statement and
is why the mission now states the exception outright. And
the tranche table in the phase 4 plan becomes a reference
document rather than a progress tracker -- nothing updates
it as tranches are worked, and nothing should.

This is the decision most likely to be argued with, because
the plan opens by complaining that the audit backlog has no
owner and this hands the oldest issue in it back to the
label. The distinction is that #227 does have an owner and a
next action; what it lacked was scope that made the work
possible to start, and that is what phase 4 delivered.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files -- the sub-agent's
   summary describes what it intended, not necessarily what
   it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This plan has an unusual verification property that the
review step should lean on: for every shared block and for
the vendored sfui copy, a canonical source exists and the
correct answer is a byte-for-byte comparison against it.
Where that is true, review by `diff`, not by reading.

### Planning effort

The master plan was created at high effort. Phase 1 is
mechanical and can be planned at medium effort -- its
difficulty is entirely in getting the block boundaries and
the vendoring procedure exactly right, which is a matter of
care rather than judgment. Phase 2 should be planned at
medium effort, with attention to the full reference set the
survey found rather than the four files the issue names.
Phase 3 should be planned at high effort: it changes another
repository's checker, and a widened check that accidentally
passes a repository running no linter at all is worse than
the false negative it fixes. Phase 4 was planned at high effort,
because the scope configuration it writes is load-bearing
for an audit and expensive to redo once reading has started
against it. Phase 5 follows `PUSH-AUDIT.md`.

### Step-level guidance

Each phase plan includes the step table described in
`PLAN-TEMPLATE.md` (step, effort, model, isolation, brief),
with briefs written so a colleague who has never seen the
codebase could execute them. Front-load the research from
this master plan into the briefs -- a brief for phase 2
should name `kerbside/tests/unit/test_render_review.py` and
`.claude/CLAUDE.md` line 139 outright, rather than leaving
the implementing agent to rediscover that the four files the
issue names are not the whole deletion.

For every phase in this plan, read the audit specification
in `shakenfist/development/docs/audits/` before starting.
The issue body is a generated summary and has already been
wrong twice.

### Management session review checklist

After a sub-agent completes, the management session
verifies:

- [ ] The files that were supposed to change actually
      changed -- read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] Shared blocks are byte-identical to their canonical
      copies, including the version number in the opening
      marker; verified with `diff`, not by eye.
- [ ] The code passes `tox -eflake8` and `tox -epy3`.
- [ ] `pre-commit run --all-files` passes.
- [ ] Workflow changes pass actionlint.
- [ ] The commit message follows project conventions,
      including the `Co-Authored-By` line recording model,
      context window and effort level.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* Issues #360, #368, #370 and #373 are closed, each by a
  consistency audit run that passes rather than by hand.
  #227 is deliberately absent: see decision 5.
* #359 is closed, or -- if `development` declines the
  upstream change -- kerbside carries a recorded exemption
  and this plan says so in writing.
* `PUSH-AUDIT.md` carries the `path-traversal-review`,
  `python-version-discipline` and `functional-test-coverage`
  shared blocks, byte-identical to their canonical copies.
* `PLAN-TEMPLATE.md` carries the `plan-push-audit-phase`
  shared block, byte-identical to its canonical copy.
* `kerbside/api/static/sfui/.sfui-commit` names the commit
  at sfui's `develop` HEAD, and `tools/vendor.sh --check`
  from a checkout at that commit reports no difference.
* No file matching `pr-address-comments.yml`,
  `address-comments-with-claude.sh`, `render-review.py` or
  `review-schema.json` exists anywhere in the tree, and
  `tox -epy3` still passes.
* `./tools/review-tracking.sh scope-orphans` exits zero, and
  the `review-scope-completeness` audit passes: every tracked
  file is either in review scope or explicitly excluded, with
  a stated reason for each exclusion.
* `docs/development.md` documents the review session recipe,
  and the command it gives for listing a tranche's
  outstanding files runs and produces file paths.
* The code passes `tox -eflake8` and `tox -epy3`, and
  `pre-commit run --all-files` is clean.
* The `PUSH-AUDIT.md` audit has been run over the plan's
  accumulated diff, and every finding it raised has been
  fixed or declined in writing in this plan.

### Documentation index maintenance

`docs/plans/index.md` carries this plan's row. The rewrite
moves it from the *Standalone plans* table to the *Master
plans* table, keeping the 2026-07-16 date, and adds the
phase links. When all phases are complete, set the status to
`Complete`.

### Future work

* The audit backlog is a standing queue, not a one-off. When
  this plan completes, new `consistency`-labelled issues
  will keep arriving. Consider whether the right end state
  is a further phase, a recurring session, or simply closing
  this plan and treating each new issue on its merits.
* The `review-coverage` audit runs `review-tracking.py
  status`, which reads the sidecar and never checks whether
  the commit carrying a mark was signed. A repository can
  therefore pass the audit with no attestation at all.
  Kerbside signs anyway, by convention rather than because
  anything enforces it, so the gap here is latent rather
  than live -- but the signature is the attestation in this
  scheme, and an audit that ignores it measures bookkeeping
  rather than review. Worth raising as an issue on
  `shakenfist/development`, alongside the two
  `github-security` defects below.
* #227's underlying problem is that review coverage decays
  with every merge -- the `prune-reviews` workflow
  invalidates a mark whenever its file changes. Neither this
  plan nor the reading sessions that follow it stop the
  backlog regrowing; they only empty it once. A standing
  review budget, of the kind `shakenfist/actions`'s
  reviewer-budget work explores, would be the durable fix
  and is out of scope here.
* The three GitHub security settings ticked in July
  (Dependabot security updates, secret scanning, push
  protection) were verified once, by hand, on 2026-07-18.
  This rewrite drops the checkboxes that recorded them, on
  the assumption that the `github-security` audit in
  `development` re-checks all three continuously. Phase 1
  step 1d checked that assumption and it is **wrong in both
  directions**, so the settings are still uncovered:

    - **Dependabot security updates is not checked at all.**
      `docs/audits/github-security.md` line 12 lists it as
      required, but `dependabot` appears nowhere in
      `scripts/audit-check.py`. The specification and the
      implementation disagree.
    - **The check silently passes when it cannot reach the
      GitHub API.** `check_github_security()` guards on `if
      result.returncode == 0 and result.stdout.strip():` and
      appends nothing when that fails, so `security` stays
      `None`, the `if security:` block is skipped, and the
      function returns `pass`. Only a timeout or a missing
      `gh` binary is reported; an auth failure, a rate limit
      or a 404 reads as compliant.

  Both belong upstream, alongside phase 3's fix, and both
  should be raised as issues on `shakenfist/development`
  rather than worked around here. Until they are, secret
  scanning and push protection are covered only when the
  audit's API call happens to succeed, and Dependabot is not
  covered at all.

### Bugs fixed during this work

Three defects so far, all of them in the audit tooling in
`shakenfist/development/scripts/audit-check.py` rather than
in kerbside, and all of the same family -- a check that does
not do what its specification says:

1. `check_llm_context_lint_ci()` contradicts its own
   specification by requiring skillsaw to be invoked in one
   of two specific ways, which is what #359 reports against
   kerbside. Found by the survey; phase 3 fixes it.
2. `check_github_security()` does not check Dependabot
   security updates, which its specification requires.
   Found by phase 1 step 1d.
3. `check_github_security()` returns `pass` when its
   `gh api` call fails with a non-zero exit status, so an
   auth failure or a rate limit reads as compliant. Found
   by phase 1 step 1d.

Only the first is in this plan's scope, because only the
first is why kerbside carries an open issue. Defects 2 and 3
should be filed against `shakenfist/development`; phase 3
already goes there and is the natural place to raise them.

#227's stale issue body is not a bug: it is a rendering
artifact of an old run. The live checker agreed with the
tree at 124 of 194 when this was written, and at 123 of 227
once phase 4 widened the scope.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
