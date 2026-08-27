# Plan: split the generated compliance tables out of the audit specifications

## Context

`docs/audits/` holds 35 criterion specifications plus an index. Each
specification is hand-written prose -- what we check, why the rule
exists, what it deliberately does not cover, which template
implements it -- and 34 of the 35 also carry a machine-regenerated
per-project compliance table between `<!-- consistency-audit:begin
-->` and `<!-- consistency-audit:end -->` markers. `test-coverage.md`
is the exception: its criterion is delegated to the pre-push review
and has no check.

The generated block opens with a line that moves on every run:

```
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*
```

That timestamp is deliberate. It is how a reader tells a current
verdict from a stale one when the audit has silently stopped running,
and `.github/workflows/consistency-audit.yml` files an issue on a
failed run saying exactly that: "the tables still show the previous
run's verdicts, so the audit looks healthy from the outside".

But a whole-file review mark attests to file content by blob SHA, and
a file carrying a line that changes daily can never hold one. So
`.vscode/review-scope.toml` excludes the whole directory bar the
index:

```toml
exclude = [
    'docs/plans/*',
    'PLAN-*.md',  # unmatched-by-design: a guard against plans returning to the root
    'docs/audits/*',
    '!docs/audits/README.md',
]
```

The cost of that exclusion, measured:

| | Lines |
|---|---|
| The 35 specification files | 3,466 |
| Inside `consistency-audit` markers (generated) | 947 |
| Hand-written prose | 2,519 |

**27% of the directory is generated, and it is why the other 73% has
never been reviewable.** Those 2,519 lines are the statement of what
the fleet is held to -- the most consequential prose in the
repository after `docs/audits/README.md` and
`docs/consistency-audits.md` -- and human review cannot see any of
it. Mikal found this while trying to review it.

The exclusion was the right call when it was written; the comment
above it reasons the trade-off out at length and correctly concludes
that including the files as they stand would put permanently-stale
entries in the queue and hold a `review-coverage` issue open that no
amount of reviewing could close. The mistake is not the exclusion. It
is that two artifacts with opposite lifecycles -- prose that changes
when someone decides something, and a table that changes every
morning -- were put in one file, so the file inherits the churn of
its worst part.

## What "good" looks like

* Every criterion specification is hand-written from the first line
  to the last, changes only when a human changes it, and can hold a
  review mark.
* Per-project compliance is still published on shakenfist.com, still
  regenerated every morning, still carries the staleness timestamp.
* The daily bot commit touches one file, not 27.
* Nothing in the fleet has to learn a new format or a new location it
  cannot discover from the page it is already reading.

## Decisions

### D1. Rendered markdown, not a JSON sidecar

The obvious move -- and the one that prompted this plan -- is to put
each table in `docs/audits/<check-id>.json` beside its markdown.
Rejected, for one reason.

`docs/audits/README.md` justifies the directory's location this way:

> It sits under `docs/` so that it publishes to shakenfist.com with
> everything else. What we hold a project to is documentation, and a
> criterion nobody outside the fleet can read is a criterion nobody
> outside the fleet can meet.

A JSON file renders as nothing. Today a reader of the published
`export-repo-config` page sees which projects comply and which issue
tracks each failure; with the data in JSON, either the compliance
information leaves the published documentation entirely, or the
website repository grows a renderer for it. The website is
*excluded* from the consistency audits, so that would put a schema
contract across a repository boundary with nothing checking either
side of it -- and the format would then be load-bearing for
publishing rather than an implementation detail.

The generated output stays markdown. What changes is which file it
lives in.

There is no loss here. The machine-readable form already exists:
every run uploads `audit-result-<repo>.json` per repository as a
workflow artifact, and `audit-manage-issues.py` consumes those
directly. A committed JSON copy would be a third representation of
the same data with no reader. If one is ever wanted, see Future work.

### D2. One page, not 34 generated files

`docs/audits/compliance.md`, one page, one section per specification.
The alternative -- 34 generated markdown files under
`docs/audits/generated/` -- preserves one-page-per-criterion and
needs a single exclusion glob, but it does not fix the churn: the bot
would still rewrite 27 files most mornings, which is what makes the
git log of this repository hard to read.

One page also has a per-run timestamp problem that resolves in its
favour. All 34 blocks currently carry the *same* timestamp -- 34
copies of one string, because `render_section` takes the maximum
timestamp across the whole result set and every spec sees the same
set. One page needs one note at the top, and nothing is lost.

The cost is one click from a specification to its compliance table
when you want both. Accepted, in exchange for a page that answers
"what is the fleet failing right now" in one place, which nothing
answers today.

### D3. What replaces the marker block in a specification

The `## Projects` section stays, and becomes a static line:

```markdown
## Projects

Per-project compliance for this criterion is regenerated every
morning by the consistency audit:
[compliance.md#export-repo-config](/components/development/plans/compliance/#export-repo-config).
```

Static, hand-written, reviewable, and it keeps the specification a
complete answer to "where do I find out who is failing this". The
anchor is the specification's basename, so it is derivable from the
filename and a test can assert every one of them resolves.

### D4. The new tell for "this criterion has no check"

`docs/consistency-audits.md` currently says marker-block absence is
how to find criteria with no automated check:

> A criterion with no check has no `consistency-audit` marker block in
> its spec file, which is how to find the current set -- at the time
> of writing, `test-coverage`.

With the markers gone from every specification that tell dies, and it
has to be replaced rather than dropped: it is the only thing in the
documentation that distinguishes "this rule is measured" from "this
rule is written down and judged by a human".

The replacement is better than what it replaces, because it is
visible to a reader rather than requiring a grep.
`compliance.md` grows a closing section naming the criteria that have
no check and why, and a specification with no check says so where its
compliance link would be. `AUDIT_METADATA` in
`scripts/audit_common.py` remains the machine-readable source of
truth -- it always was -- and a test asserts the three agree:
every spec in `AUDIT_METADATA` carries a compliance link and has a
section on the page, and every spec that is not in `AUDIT_METADATA`
carries neither and is named in the no-check section.

### D5. The specifications come into scope, and the coverage number moves

Measured on this branch by editing the scope file and running
`scripts/review-tracking.py status`:

| | In scope | Reviewed | Needing review |
|---|---|---|---|
| Today | 77 | 14 | 63 |
| After | 112 | 10 | 102 |

The `review-coverage` threshold is 5 and `development` is already
non-compliant against it (`shakenfist/development#45`, open). So this
files no new issue and changes no cell in any compliance table -- only
the count in the body of an issue that is already open. The backlog
this creates is the backlog that already existed and was not being
counted.

**Outcome: 10 of 112, 102 needing review**, four worse than
predicted, because the prediction only counted files entering scope and
not the marks this work would invalidate on the way. Three were pruned,
each for the same reason -- the branch edited a file somebody had
reviewed, and a mark attests to exact content:

| Pruned mark | Edited by |
|---|---|
| `.github/workflows/consistency-audit.yml` | Phase 1, the `update-docs` job's comments |
| `.github/workflows/prune-reviews.yml` | Phase 3, one comment line |
| `.claude/skills/standards-alignment/SKILL.md` | The push-audit fixes |

The fourth is not a prune: rebasing onto `main` late in the work
brought in two upstream prunes of its own, which moved the count again.
That is the tracking working rather than a miscount, and it is worth
predicting next time -- a change that sweeps wording across a
repository under review invalidates marks in proportion to how many
files it touches.

The new exclude list:

```toml
exclude = [
    'docs/plans/*',
    'PLAN-*.md',  # unmatched-by-design: a guard against plans returning to the root
    'docs/audits/compliance.md',
]
```

`!docs/audits/README.md` goes away with the glob that needed it, and
the negation support in `in_scope()` stays -- it is general
machinery, not scaffolding for this one case.

## Implementation

Work happens in a worktree off `shakenfist/development`; this plan
file lands with the change (per `CLAUDE.md`).

### Execution

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Split the generated output out | Complete | |
| 2. Bring the specifications into review scope | Complete | |
| 3. Documentation and runbooks | Complete | |
| 4. Push audit | Complete | |

The `Merged` column stays empty until the branch lands; all four
phases ship as one pull request rather than one each, because phase 2
cannot be reviewed without phase 1 in the tree and phase 3 corrects
prose that phase 1 makes wrong.

Phases 1 and 2 have a hard ordering dependency:
`test_review_tracking.py` asserts that every pattern in
`review-scope.toml` matches a path that exists unless it carries an
`unmatched-by-design` comment, so `docs/audits/compliance.md` has to
be committed before it can be named in the exclude list.

Phase 1 is one commit and cannot be smaller. The daily workflow
rewrites whatever the code tells it to rewrite, so the code change,
the removal of the marker blocks from 34 specifications, and the new
page have to land together or the next 06:00 UTC run either reverts
half of it or leaves 34 files carrying a stale table nothing updates.

### 1. Split the generated output out

* **`docs/audits/compliance.md`** (new). One page. A short
  hand-written preamble explaining what it is and that it is
  generated, then a single generated block between the existing
  markers containing:

  * the `*Generated <timestamp> from scripts/audit-check.py; do not
    edit.*` note, once, at the top;
  * one `## <spec-basename>` section per spec in `AUDIT_METADATA`, in
    the order `checks_by_spec()` yields sorted, each holding a link
    back to its specification and the table `render_section` renders
    today, unchanged in format;
  * a closing section naming the criteria with no automated check.

  The preamble sits *outside* the markers so it survives
  regeneration, the same way every specification's prose does today.

  The initial content is a faithful transcription of the 34 blocks
  currently in the specification files, not a fresh audit run.
  Generating it properly needs 17 repository clones and a `gh` token;
  transcription is exact, verifiable by diffing the moved blocks
  against what was removed, and it is replaced by real output at the
  next 06:00 UTC run anyway. A one-shot migration script does the
  move; it is not committed.

* **`scripts/audit-update-docs.py`**. `render_section` keeps its
  table-rendering logic and loses its per-spec framing: the markers,
  and the timestamp note, move up to a new page-level renderer.
  `update_spec_file` becomes `update_compliance_page` and writes one
  file. `main` stops iterating over spec files, so the
  `missing_markers` error path -- which exists to catch a spec whose
  markers were deleted -- collapses to a single check on the one page.

  Keep the `column_name` fallback and its warning exactly as they
  are. The comment on it records that one missing heading once
  stopped every project's table from publishing, and consolidating to
  one page raises that stake rather than lowering it: one page is one
  write, so a failure now takes out the whole fleet's compliance
  output in a single file rather than 34.

* **`scripts/audit_common.py`**. `AUDIT_METADATA[*]['spec']` still
  names the specification file -- it is what the compliance page
  links back to, what issue bodies point at, and what
  `test_audit_update_docs.py` checks the existence of. The markers
  stay where they are defined; only their number of uses changes.

* **`scripts/commit-audit-docs.sh`**. Narrow the diff check, the
  `git add` and the commit message from `docs/audits/` to
  `docs/audits/compliance.md`. This is a safety improvement beyond
  the tidying: today the bot runs `git add docs/audits/` on a
  checkout, so any other change present in that tree would be
  committed to `main` under the bot's name and the message
  "Regenerate audit compliance tables."

* **The 34 specification files**. Marker block out, static compliance
  link in, per D3.

* **`docs/audits/test-coverage.md`**. Already explains at length why
  it has no table. Reword the "There is no per-repository table on
  this page" sentence so it reads correctly now that no specification
  has one, and point at the no-check section of the compliance page.

* **`scripts/test_audit_update_docs.py`**. The existing tests keep
  their intent and change their target:

  * `test_every_generated_block_opens_with_the_current_note` --
    currently walks every spec, asserts the note format, and requires
    it checked more than 20 files so that a pass on nothing is
    impossible. Retarget to the single page; keep the guard by
    asserting the page's section count equals
    `len(checks_by_spec())`, which is the same protection against a
    silent pass on nothing.
  * `test_every_spec_file_is_named_in_the_index` -- unchanged, and
    `compliance.md` must not trip it. It lists every `*.md` bar
    `README.md` and requires each to be linked from the index, so the
    new page is either excluded alongside `README.md` or linked from
    the index. Link it: it should be discoverable there anyway.
  * New: every spec in `AUDIT_METADATA` carries a compliance link
    whose anchor matches its basename and has a matching section on
    the page; every spec file *not* in `AUDIT_METADATA` carries no
    such link and is named in the no-check section. This is what
    keeps D4's tell honest.
  * New: no specification file contains a `consistency-audit` marker.
    Cheap, and it catches the reintroduction this plan exists to
    prevent.

### 2. Bring the specifications into review scope

* **`.vscode/review-scope.toml`**. The exclude list per D5. The long
  prose comment above it is the substance of this phase, not an
  afterthought: most of it argues for an exclusion that no longer
  applies, and it should be rewritten to record what the directory
  now looks like and why one generated page is excluded -- keeping
  the paragraph about *why* the timestamp exists, which is the part
  that stays true and the part a future reader will otherwise
  re-derive.

  Delete the paragraph about `audits/*` having silently matched
  nothing after the tree moved under `docs/`, and the one about
  `test-coverage` being left excluded rather than named as an
  exception. Both describe a bookkeeping problem that this change
  removes.

* **`REVIEWS.md`**. Regenerated, not hand-edited. Expect
  `112 in-scope files` and no change to the reviewed list.

* Confirm `scripts/review-tracking.py status` reports 112 files in
  scope and that `compliance.md` is not among them. The reviewed count
  is not fixed in advance: it falls as later phases edit files that
  carry marks. It ended at 10 of 112 -- see D5.

### 3. Documentation and runbooks

* **`docs/audits/README.md`**. The "File structure" section shows a
  specification's skeleton including the markers; replace the marker
  block with the static compliance link. Add `compliance.md` to the
  audit index, or to the prose above it, so it is discoverable from
  the directory's front page. The opening paragraph says each file
  "carr[ies] a per-project compliance table regenerated every
  morning" -- reword.

* **`docs/consistency-audits.md`**. The "two layers" table describes
  the specification layer as holding "a generated per-project
  compliance table"; that becomes a link, and the generated page is
  arguably a third row. Replace the marker-absence tell per D4. Check
  the "how to add a criterion" list, which the README says touches
  five files: adding a criterion no longer means adding a marker
  block, and the count may change.

* **`PUSH-AUDIT.md`**. The wave 1 grep for hand-edited compliance
  tables is keyed on `docs/audits/*.md` and matches marker lines and
  status rows. Retarget it: a status row appearing in a *specification*
  is now the defect worth grepping for, and an edit to
  `compliance.md` is the other. Both are worth a line.

* **`ARCHITECTURE.md`** and **`AGENTS.md`**. Check only. The
  component inventory does not change and no convention changes, so
  the expected outcome is no edit -- but `docs/audits/` is
  prominent enough in both that a stale sentence about tables living
  in the specifications is likely, and a grep for `consistency-audit`
  and for `docs/audits` across the repository is the cheap way to
  find every remaining one.

  **Outcome: both needed an edit, and the grep found eleven more
  files.** `ARCHITECTURE.md` described every spec as carrying a table;
  `AGENTS.md` said the tables sit in `docs/audits/*.md`. The rest were
  one-line wording fixes plus four pointers that named a spec file as
  the place to read fleet state, which now name the compliance page
  and its anchor: `README.md`, `docs/automated-pr-review.md`,
  `docs/ci-review-automation.md` and
  `templates/ci-review-automation/README.md`.

  `blank_generated_blocks` in `scripts/audit-check.py` was the one
  worth reading rather than editing. It blanks generated blocks before
  the `docs-external-links` and `plan-phase-references` checks scan
  this repository's own markdown, so harvested detail strings from
  other repositories are not judged as our prose. It still does
  exactly that, over one file instead of thirty-four; only its
  docstring needed the correction.

  One inconsistency turned up that predates this work.
  `docs/audits/README.md` said a new criterion touches "five files
  (six if it shares a spec file)", while the numbered list in
  `docs/consistency-audits.md` gives four plus a conditional fifth.
  The list is right, so the README now says four. Neither count
  changes as a result of the split: a criterion still gets one spec
  file, and it is the compliance page that gains a section
  automatically.

* Run `pre-commit run --all-files` -- actionlint, shellcheck, flake8,
  skillsaw and the four Python suites -- which is the whole of lint
  and test here.

### 4. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of the first three
phases against `main`. Findings land as their own pull request; the
plan is not complete until each is resolved or declined in writing,
with the reason recorded here. If the audit finds nothing, say so in
one sentence.

The interesting brief for wave 2 here is the documentation one: this
change edits 34 specification files mechanically and rewrites prose
in five more, and a mechanical edit repeated 34 times is exactly
where a wrong anchor or a dropped section heading hides.

**Outcome.** Wave 1 passed: `pre-commit` clean at the time, all seven
greps empty. One correction to the runbook is owed and is recorded
under Future work -- every diff command in `PUSH-AUDIT.md` is written
against `main...HEAD`, and a stale local `main` silently widens the
diff to unrelated history. The first run of the greps here reported
hits from commits that were already on `origin/main`.

Wave 2 found no critical or high security findings and six things
worth fixing. All six are fixed on this branch rather than deferred,
because five of them were introduced by phases 1 to 3 and the sixth
is a two-line change to code this plan already touches.

**2c-1 / 2d-1: a stale review attestation, shipped.** Phase 3 edited
`.github/workflows/prune-reviews.yml`, which carried a review mark, and
no prune followed -- so `REVIEWS.md` published it as reviewed at
content nobody had read, and `test_every_stamp_matches_the_content_it_
attests_to` failed. Pruned, and `REVIEWS.md` regenerated.

Worth recording *why* this got past the pre-commit run at the end of
phase 3, because it will happen again. That test compares the stamp
against `blob_sha(':<path>')`, which reads the git **index**. The file
was edited but not staged, so the test compared the stamp against the
old staged blob and passed; `git add` moved the new content into the
index and the same test then failed. Running `pre-commit
run --all-files` on an unstaged tree gives a false pass for exactly
the check that guards the attestations.

**2c-2: a file the phase 3 sweep missed.**
`.claude/skills/standards-alignment/SKILL.md` said "The per-audit
status tables in `docs/audits/*.md` regenerate daily". The sweep
grepped for "compliance table"; this file says "status tables". Fixed.

**2c-3: the index row.** `docs/plans/index.md` registered this plan as
`Not started` while its own Execution table had three phases
`Complete`. `plan-index` checks the vocabulary, not the accuracy, so
CI passed on a visibly wrong row. Fixed.

**2b-1: the new link test broke the documented "Adding a criterion"
recipe.** `test_measured_specs_link_a_section_that_exists` required a
spec's anchor to already have a section on `compliance.md`. But the
page is generated daily and lags the specs by one run, so a criterion
registered today has no section until tomorrow -- which is precisely
what step 3 of that recipe produces. The test would have failed the
first commit of every future criterion pull request, and the class it
replaced had explicitly tolerated the transitional state.

Fixed by deleting the wrong half of the assertion rather than adding a
skip. The anchor must match the spec's own basename, which is the typo
this can actually catch; that the section will exist is guaranteed by
`test_the_page_has_a_section_for_every_spec`, which asserts against the
renderer instead of against yesterday's file. `test_the_page_names_
every_unmeasured_spec` had the same coupling and moved to the renderer
for the same reason. Verified by deleting a section from the page and
confirming the suite still passes.

**2d-2: a harvested detail string could restructure the page.**
Detail strings are written out of what a check found in an audited
repository and rendered as bare prose. `update_compliance_page` found
the end marker with a substring search, so a detail carrying that
marker truncated the next run's splice -- leaving that run's tables
*outside* the block, where every later run preserved them again. The
page grows without bound, publishing stale verdicts that
`blank_generated_blocks` no longer exempts from this repository's own
`docs-external-links` and `plan-phase-references` checks. A merged
workflow file named for the marker is enough to trigger it, so it
takes commit access to a fleet repository.

Pre-existing, and the consolidation escalated it from corrupting one
spec's block to corrupting the single page carrying all 34 tables.
Fixed at both ends: `defuse()` collapses newlines and neutralises the
HTML comment opener before interpolation, and the splice is now
anchored to whole lines the way `blank_generated_blocks` already was,
so it does not depend on the defusing having worked. Newline collapsing
also fixes the accidental route, a subprocess traceback reaching a
detail string.

**2d-3: the documented local-test recipe destroyed hand-written work.**
`docs/consistency-audits.md` told the reader to clean up with `git
restore docs/audits/`. That was safe when the directory was generated;
it now throws away edits to any of the 35 hand-written specifications.
The recipe uses `--page /tmp/compliance.md` instead.

**2a-1 and 2d-3 disagreed, and the disagreement was productive.** 2a
called the new `--page` flag dead, untested surface whose value was
decoupled from `AUDITS_DIR` -- so `unmeasured_specs()` would describe
the real `docs/audits/` even when writing elsewhere. 2d found the
recipe above, for which `--page` is exactly the right answer. Keeping
it satisfies both: the flag now has a documented caller and CLI
coverage, and is named in the module's usage block.

The decoupling 2a complained about turned out to be correct, and
"fixing" it was a mistake -- see the review round below.

**Coverage added**, per 2b and 2d: an isolated unit test of
`unmeasured_specs()` against a fabricated directory, which nothing
pinned before -- all three callers treated its output as ground truth;
`update_compliance_page()` and `main()`, which had no coverage on
either side of the split and are the only path that writes the file
the privileged bot commits, including the missing-marker and
markers-out-of-order guards; and `defuse()`. The suite goes from 20
tests to 33.

**Declined, with reasons.** `render_table`'s `unknown` cell,
`load_results` on an empty directory, and `render_page`'s
zero-unmeasured branch are untested. All three predate this change and
none is touched by it; naming them here is the functional-test-coverage
block's instruction rather than a reason to widen the change. Two
informational findings about harvested detail strings reaching other
sinks are recorded under Future work instead of fixed, because they are
outside this page.

### The review round on pull request #57

The automated reviewer raised four `fix` items, two `consider` and two
informational. All eight are addressed; the two `consider` items were
taken because one is a defect this branch introduced and the other is
its comment.

**The interesting one is that it caught a fix that made things worse.**
Responding to 2a-1 above, `render_page` was changed to derive the
audits directory from the `--page` path, so that a redirected page
would be "self-consistent". The reviewer ran the recipe this branch
documents, `--page /tmp/compliance.md`, and found it publishing two
unrelated scratch files as criteria nobody measures; in a clean output
directory the section vanishes instead. Which criteria have no check is
a property of *this repository*, not of wherever the output happens to
go, so the scan is anchored back at `AUDITS_DIR` and a test renders
into a directory seeded with unrelated `.md` files to pin it. Nothing
reached the published page -- the workflow never passes `--page` -- but
it defeated the mitigation this plan names for the risky unattended
06:00 run, which is to render locally and diff before landing.

The other three `fix` items were documentation the phase 3 sweep
missed: two specs (`python-version`, `security-sanitization`) kept a
sentence introducing "the table below" with no table below it, which
was the only thing telling a reader that just one of the standards on
each page is measured; a second stale pointer in
`templates/ci-review-automation/README.md`, in a section that did
predate the sweep; and a comment in `scripts/audit_common.py` whose
edit dropped the noun its preposition governed. Plus the stale numbers
in D5, corrected above.

## Risks and mitigations

* **The next daily run publishes nothing, or publishes to the wrong
  place.** The real risk of the phase. The `update-docs` job runs at
  06:00 UTC unattended and pushes to `main`; a mistake is discovered
  by a missing table, not by a red run. Mitigation: run
  `audit-update-docs.py --no-issues` against a saved results
  directory before the phase lands and diff the output, and watch the
  first real run rather than assuming it.

* **The website does not render the anchors.** The compliance links
  are the whole of the connection between a specification and its
  table, and they are markdown heading anchors. Mitigation: check one
  published page after the first deploy. If the site generator does
  not emit heading anchors, the links still resolve to the page and
  degrade to a scroll, which is the acceptable failure.

* **The 35 files arrive in the review queue and nothing reviews
  them.** The exclusion at least was honest about not covering them;
  a queue entry that sits for a year is worse than an exclusion,
  because it makes the coverage number a number nobody acts on.
  Mitigation: none in tooling -- `review-coverage` already files the
  nudge and `development#45` is already open. This is the phase
  making a real backlog visible, which is what was asked for.

* **A specification regrows a table.** An agent picking up an audit
  issue, or a future version of this repository's own tooling, adds
  one back. Mitigation: the phase 1 test asserting no specification
  contains a marker, plus the retargeted `PUSH-AUDIT.md` grep.

## Definition of done

* No file in `docs/audits/` other than `compliance.md` contains a
  `consistency-audit` marker, and every specification links to its
  section on the compliance page.
* `compliance.md` carries one timestamp note, a section for each of
  the 34 specifications with a check, and a section naming the ones
  without.
* `.vscode/review-scope.toml` excludes `docs/audits/compliance.md`
  and nothing else in that directory; `review-tracking.py status`
  reports 112 in scope.
* `pre-commit run --all-files` passes.
* One 06:00 UTC run has completed after the change, its commit
  touched only `docs/audits/compliance.md`, and the published page on
  shakenfist.com shows the tables with working anchors from at least
  one specification.
* Phase 4's findings are resolved or declined in writing.

## Future work

* **`compliance.json`.** If something ever wants the fleet-wide
  verdicts machine-readably from a checkout rather than from workflow
  artifacts, `audit-update-docs.py` can emit it alongside the page
  from the same in-memory results, excluded from review by the same
  pattern extended to `docs/audits/compliance.*`. Not now: it would
  have no reader, and an unread generated file drifts.

* **Reviewing the 35 specifications.** The point of the change, and
  not part of it. The first session on them is where we find out
  whether the prose in a directory nobody could review has the
  problems that suggests.

* **The same shape elsewhere in the fleet.** If any other repository
  has generated content interleaved with reviewable prose, it has
  this problem too. Worth a look during the next review session
  rather than a check of its own -- one instance is not a pattern.

* **`PUSH-AUDIT.md` is written against `main...HEAD`.** Every diff
  command in it names the local `main`, which is whatever was last
  fetched into the working clone. A stale `main` silently widens the
  audit's diff to unrelated history, which is what happened on the
  first run of wave 1 here. It should be `origin/main...HEAD`, or the
  runbook should open by asserting the two agree. This is a change to
  the runbook rather than to this plan's subject, and it affects the
  eight repositories that carry one, so it belongs in its own change.

* **Detail strings reach two sinks this change does not cover.**
  `defuse()` protects the compliance page. The same unsanitised
  strings go into issue bodies filed fleet-wide with `AUDIT_TOKEN` by
  `audit-manage-issues.py`, where an `@mention` harvested out of an
  audited repository notifies a real person; and
  `check_review_coverage` can put a multi-line subprocess traceback
  into a detail string. Both predate this change and neither is on the
  page, so both are left alone here. The issue-body one is the more
  interesting of the two and should probably reuse `defuse()`.

## Back brief

Before phase 1 begins, the management session confirms with Mikal:
the single-page layout and its anchor scheme (D2, D3), and the
wording that replaces the marker-absence tell (D4). Both are cheap to
propose now and expensive to redo across 39 files.
