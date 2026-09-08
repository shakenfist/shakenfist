# Plan: a `scope-coverage` audit, reconciling the audit lists against the organisation

## Prompt

Before executing any part of this plan, read what it changes.
`docs/consistency-audits.md` is the reference for what a daily run
does, how a criterion is added, and how a repository is brought into
scope -- read it first. Then `AGENTS.md` for the invariants that are
not visible in the code, and `ARCHITECTURE.md` for how the pieces fit.
`PUSH-AUDIT.md` is the pre-push runbook this plan's last phase runs.

The two standing constraints from `PLAN-TEMPLATE.md` bind here. The
blast radius is other people's repositories: bringing three
repositories into the matrix files 43 issues on the next morning's
run, on repositories whose owners did not ask for them today. And this
repository is inside its own audit matrix, so the criterion added here
measures the repository it is added to, from the first run after it
merges.

Every count in this plan was measured on 2026-09-04 against
`gh repo list shakenfist --limit 200`. The organisation moves; re-derive
before relying on a number.

## Situation

Audit scope is written down in three places -- the `repo:` matrix in
`.github/workflows/consistency-audit.yml`, the in-scope list in
`docs/audits/README.md`, and the excluded list on the same page --
and `AuditScopeIsStatedOnceTest` in `scripts/tests/test_registry.py`
holds those three in agreement with each other.

Nothing compares any of them to the organisation. A repository in
none of the three is not audited, is not documented as excluded, and
produces no finding anywhere. The failure is silent by construction:
the only signal is a repository missing from a list nobody diffs.
This is issue #40.

Measured today, the organisation has 38 repositories. Five are in
neither list:

| Repository | Last push | Note |
|------------|-----------|------|
| divergulent-reviews | 2026-09-03 | review-tracking sidecar for divergulent |
| homebrew-tap | 2026-07-24 | packaging tap |
| kerbside-client | 2024-03-29 | one commit, 2024; never revisited |
| uncalibrated-sextant | 2026-08-16 | actively developed |
| visual-digest-rust | 2026-07-24 | actively developed |

The same blindness runs the other way: the excluded list names
`imago-testdata`, `imago-testdata-quarantine` and `occystrap-testdata`,
none of which exist under `shakenfist` any more. The imago test data
now lives on the private GitLab as `instar-testdata`. An exclusion for
a repository that does not exist is harmless in itself; it is the same
missing check.

`kerbside-client` was investigated rather than assumed. Its code did
not move: `shakenfist/kerbside` has no client package, nothing in this
repository references it, and the repository is a single "Initial
commit" from 2024-03-29 carrying real code -- `apiclient.py` (7.5KB)
and `main.py` (14KB) -- with an empty test tree. It was pushed once and
left.

## Mission and problem statement

Make the scope decidable against reality in both directions, and make
the five undecided repositories decided in writing.

Two properties, checked every morning:

* Every repository in the organisation is either in the audit matrix
  or on the excluded list.
* Every name in the matrix or on the excluded list still resolves to a
  repository in the organisation.

Neither is a judgement about whether a repository *should* be audited.
The check cannot make that call and does not try; what it removes is
the third state, where nobody made it either.

## Decisions

### D1. A registered `Check`, scoped to `development`

`scope-coverage` is an ordinary `Check` subclass whose `applies(repo)`
returns a skip reason unless `repo.name == 'development'`. The lists
live in this repository's clone, this repository is already in the
matrix and audits itself deliberately, and the audit step already
carries `GH_TOKEN: ${{ secrets.AUDIT_TOKEN }}`.

That buys the whole lifecycle from machinery that already exists:
`audit-manage-issues.py` files one `consistency` issue on `development`
when the lists drift and closes it when they do not, and the criterion
gets a section on the compliance page like any other.

The issue proposed two alternatives, and both were rejected:

* **A separate job in `consistency-audit.yml`.** It would need its own
  issue filing, closing and idempotency logic -- the part of
  `audit-manage-issues.py` that is easy to get subtly wrong -- and it
  would appear nowhere in the compliance tables.
* **A unit test calling the API.** `pre-commit` and the pull request
  gate have no token, so the test would either be skipped where it
  matters or make the gate depend on the network.

The precedent for a check that is N/A nearly everywhere is
`sfui-vendor`, which is real for the repositories that vendor sfui and
skipped for the rest.

### D2. No `isArchived` filter

Every archived repository in the organisation -- `ansible-modules`,
`client-go`, `client-js`, `deploy`, `jenkins-private`, `loadtest`,
`ostrich`, `symbolicmode`, `terraform-provider-shakenfist`, `website`
-- is already on the excluded list. So the strict reading costs nothing
to adopt: every repository appears in one of the lists, archived or
not, and there is no filter to write and no exemption to explain.

The issue floated `isArchived` as the obvious filter and named
`kerbside-client` as the case it would have got wrong -- dormant since
2024 and not archived. Requiring a decision for every repository
removes the class of problem rather than that one instance of it.

### D3. The five repositories, decided

| Repository | Decision | Reason |
|------------|----------|--------|
| divergulent-reviews | Excluded | A review-tracking sidecar, not a project in the sense the criteria mean |
| homebrew-tap | Excluded | A packaging tap; nothing to package, document or release |
| kerbside-client | In scope | Real client code held to the standard, not an archive |
| uncalibrated-sextant | In scope | Actively developed |
| visual-digest-rust | In scope | Actively developed |

The excluded list's rationale sentence says exclusions are "internal
only tooling or historical archive repositories". A review sidecar and
a packaging tap are neither, so the sentence widens to cover
repositories that are not projects in the sense the criteria mean.

Onboarding costs, from a dry run of `scripts/audit-check.py` against
each clone on 2026-09-04:

| Repository | pass | fail | n/a |
|------------|------|------|-----|
| uncalibrated-sextant | 9 | 19 | 21 |
| visual-digest-rust | 9 | 15 | 25 |
| kerbside-client | 5 | 9 | 35 |

43 issues on the first run after the matrix change. That is the point
of onboarding rather than a reason not to: they are the criteria the
rest of the fleet already meets. `standards-alignment` is the skill
for working the backlog down, and this plan does not do it.

### D4. One parser, not two

The parse of the three scope lists lives in
`AuditScopeIsStatedOnceTest` today: literal start and end phrases, a
bullet prefix, and a `REPO_NAME` guard that notices a parse which has
started collecting prose. The check needs exactly that parse.

It moves to `scripts/audit/scope.py` and the test imports it. A second
copy in the check would let the test and the check disagree about what
the lists say, which is the failure this criterion exists to prevent,
one level up.

The phrase-anchoring assertions move with it. They are the reason the
parse is trustworthy: a start phrase that is reworded away raises, and
an end phrase that is reworded away silently runs the block to the end
of the file.

### D5. `develop` branches before the matrix, not after

`uncalibrated-sextant` and `visual-digest-rust` both defaulted to
`main`, which `default-branch-naming` fails. Renaming after they enter
the matrix would file an issue on each and close it the next morning.

Both were renamed through the GitHub rename endpoint rather than
create-and-delete: it moves the default branch and retargets open pull
requests in one operation. Neither had open pull requests or rulesets,
and both now have `develop` as their only branch. `kerbside-client`
already defaulted to `develop`.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. `develop` branches for the onboarding repositories | Complete | n/a -- GitHub settings, no commit |
| 2. Reconcile the scope lists | Complete | 8b77b32 |
| 3. Lift the scope parsing into `audit/scope.py` | Complete | 8b77b32 |
| 4. Add the `scope-coverage` check | Complete | 8b77b32 |
| 5. Push audit | In progress | |

Phases 2 to 4 shipped as a single pull request, so the `Merged`
record is the same merge commit for all three: `8b77b32`, the merge
of pull request 93 on 2026-09-04, whose diff against its first
parent is the whole of this plan's work. That record was
reconstructed in phase 5 rather than written as the phases landed;
it comes from `gh pr view 93`, not from a path-filtered `git log`,
which cannot say which commits arrived under a merge.

### 1. `develop` branches for the onboarding repositories

`uncalibrated-sextant` and `visual-digest-rust` renamed `main` to
`develop` on GitHub, which moved the default branch with it. Local
clones updated: `develop` created tracking `origin/develop`,
fast-forwarded, and `origin/HEAD` re-pointed. No commit in this
repository.

Stale remote-tracking refs for branches deleted upstream before today
were left in place in both clones; pruning them is unrelated hygiene.

### 2. Reconcile the scope lists

One commit, and it lands before the check so that the check passes on
its first run rather than failing on the state it was written to
detect.

* `.github/workflows/consistency-audit.yml`: add `kerbside-client`,
  `uncalibrated-sextant` and `visual-digest-rust` to the matrix, in
  alphabetical order.
* `docs/audits/README.md`: the same three onto the in-scope list; add
  `divergulent-reviews` and `homebrew-tap` to the excluded list; remove
  `imago-testdata`, `imago-testdata-quarantine` and
  `occystrap-testdata`; widen the excluded list's rationale sentence
  per D3.
* Check `REPO_OVERRIDES` in `scripts/audit/repo.py` needs nothing for
  the three: they are ordinary Python and Rust repositories on
  `develop`, with no exemption to state. An override added here would
  be an exemption written for a repository nobody has tried to fix yet.

`python3 -m unittest tests.test_registry` is the gate: it compares all
three lists against each other and is the reason this phase is
separable at all.

### 3. Lift the scope parsing into `audit/scope.py`

One commit, no behaviour change. `matrix_repos()`,
`documented_in_scope()`, `documented_excluded()`, `bulleted_block()`
and `REPO_NAME` move out of `AuditScopeIsStatedOnceTest` into
`scripts/audit/scope.py`, taking a repository root rather than reading
`REPO_ROOT` from the test base. The test imports them and keeps its own
tests of the guards -- those are tests of the parser, and they follow
it.

The module raises rather than asserts: `unittest` assertions in
production code are a test framework leaking into the runner, and the
check has to turn a failed parse into a `fail()` result rather than a
traceback. `AuditScopeIsStatedOnceTest` keeps its assertion messages by
catching and re-raising, or by asserting on the exception text.

### 4. Add the `scope-coverage` check

One commit.

* `ScopeCoverage` in `scripts/audit/checks/github_config.py`:
  `id = 'scope-coverage'`, `spec = 'docs/audits/scope-coverage.md'`,
  `template = None`, an issue title, `applies()` per D1, and `run()`
  comparing the organisation listing against the two lists. Registered
  in `CHECKS` in `scripts/audit/registry.py` beside the rest of the
  `github_config` family.
* Both failure directions in one result, with the repository names in
  `missing=` so `audit-manage-issues.py` renders them as bullets.
* `docs/audits/scope-coverage.md`, following the structure in
  `docs/audits/README.md`, plus its line in that index table.
* The frozen lines in `scripts/tests/test_metadata.py` -- adding a
  criterion adds a line to `FROZEN_METADATA` and
  `FROZEN_ISSUE_TITLES`. The issue title is the idempotency key for
  filing and closing; choose it once.
* Tests in `scripts/tests/test_github_config.py` on `FakeGitHub`: a
  clean scope passes, an unlisted repository fails, a listed name that
  does not resolve fails, a truncated listing is caught, and a
  repository that is not `development` skips without an API call.

### 5. Push audit

**Planning effort:** medium. The runbook is written and phases 2 to
4 landed as one reviewed pull request, so the judgement in this
phase is about scoping it correctly rather than about design.
**Review effort:** high for the wave 2 agents, which is what the
runbook already specifies for 2d and what the size of the diff
justifies for 2a and 2c.

In scope: running `PUSH-AUDIT.md` over the accumulated diff of
phases 2 to 4, repairing the two places in that runbook which no
longer describe this repository, and closing out the three records
this plan left blank. Out of scope: the 43 onboarding issues, which
are `standards-alignment` work and were declared so in D3; porting
kerbside's `tools/audit/plan-range.sh` here; and fixing anything
wave 2 finds -- findings land as their own pull request, and this
plan is not complete until each is resolved or declined in writing
in this section. If the audit finds nothing, that is recorded here
in one sentence.

#### What the survey found

The deliverables of phases 2 to 4 are all present and the criterion
works in production: `scripts/audit/scope.py` and
`docs/audits/scope-coverage.md` exist, `ScopeCoverage` is
registered at `scripts/audit/registry.py:79`, the three onboarded
repositories are in the workflow matrix and on the in-scope list,
the three dead `*-testdata` exclusions are gone, and
`docs/audits/compliance.md` reports `scope-coverage` as `compliant`
on `development` with all other repositories `N/A`. That is the
success criterion "passes on the first run after merge", met by the
daily run rather than by a local dry run.

It also answers the question `03046b2` left open -- whether
`AUDIT_TOKEN` can see private repositories. A compliant verdict is
only reachable if `performance`, `private-ci` and `jenkins-private`
resolved, so the token can. The degradation path built for the case
where it cannot is still right to keep; it is no longer the path
being taken.

Four things the section did not say, all corrected at source in
this phase's commit so the next reader does not re-derive them:

**The `Merged` column was never filled.** Phases 2 to 4 landed in
pull request 93, merged 2026-09-04, as merge commit `8b77b32`. The
section instructs the audit to use "the merge commit recorded in
the Execution table" and the table recorded nothing, which is
exactly the failure `plan-push-audit-phase` describes: once the
phases have merged, a diff against `main` is empty and reads as a
clean audit. The record was reconstructed from `gh pr view 93`
rather than from a path-filtered `git log`, per the block, and the
answer is unambiguous here because one pull request carries all
three phases.

**`AGENTS.md` and `ARCHITECTURE.md` were not unchanged.** The
Documentation index maintenance section predicted that no
convention moved and the shape of the system did not change. Both
predictions were wrong, and correctly so: D4 moved the scope parse
out of a test and into a module, which is a convention change
(`AGENTS.md` now names `audit/scope.py` and `ScopeParseError` where
it named `AuditScopeIsStatedOnceTest`) and a new component
(`ARCHITECTURE.md` gained the `audit/scope.py` entry). The prose is
corrected to describe what happened.

**Bugs fixed during this work still said "To be filled in".** Two
review rounds on pull request 93 fixed real defects, and they are
now recorded below.

**`PUSH-AUDIT.md` no longer describes this repository in two
places, both of which bear on this plan specifically.** The 2a
brief states the four-file rule as "a check function in
`scripts/audit-check.py`, metadata in `scripts/audit_common.py`
(`AUDIT_METADATA` and `ISSUE_TITLES`)". Neither is true since
`PLAN-audit-scripts-restructure` landed: `scripts/audit-check.py`
is 71 lines and contains no check functions, and
`scripts/audit_common.py:48-50` *derives* `AUDIT_METADATA` and
`ISSUE_TITLES` from the registry rather than declaring them. The
wave 1 grep at `PUSH-AUDIT.md:64-67`, which watches
`scripts/audit_common.py` for changes to the issue-title interface,
is dead for the same reason -- adding a criterion now touches
`scripts/audit/checks/`, `scripts/audit/registry.py` and the frozen
lists in `scripts/tests/test_metadata.py`, and never that file. The
restructure updated the **Duplicated logic.** bullet to say
`scripts/audit/checks/` and missed these two. Line numbers in this
section are as of `8b77b32`; 5a's edit has since moved them. This
plan added a criterion, so it is the work those two paragraphs
exist to review.

Nothing else in the runbook disagreed with the tree.
`scripts/audit_common.py` still exists, so the grep is not
referencing a deleted file; the shared blocks embedded in
`PUSH-AUDIT.md` are unrelated to the stale prose and none of them
needs a version bump; and this repository has no
`tools/audit/plan-range.sh`, so the range is supplied by hand.

#### Decisions

**D5.1. The audit range is `8b77b32^1..8b77b32`.** Phase 1 changed
GitHub settings and produced no commit, so the merge of pull
request 93 against its first parent is the whole of this plan's
work: 20 files, 1,270 insertions, 295 deletions. Every diff command
in `PUSH-AUDIT.md` is written `main...HEAD`, which is empty here
because the work is already on `main`; each is read with
`$AUDIT_BASE $AUDIT_HEAD` substituted, exported once at the top of
the phase. The audit is retrospective and cannot gate a push that
already happened -- what it can still do is find what two rounds of
automated review did not, before the criterion has been running
long enough for anyone to trust it.

**D5.2. `tools/audit/plan-range.sh` is not ported in this phase.**
kerbside derives `AUDIT_RANGE`/`AUDIT_PATHS` from a plan's recorded
merge commits, which is the right shape and is why the `Merged`
column is worth filling. Building it here would be a second plan's
worth of work sitting inside an audit phase, and this phase needs
one range that fits on a line. Recorded under Future work.

**D5.3. The two stale `PUSH-AUDIT.md` paragraphs are fixed here,
before wave 1 runs.** This is the decision most likely to be argued
with, because an audit phase's job is to find findings, not to
repair the tool it is holding, and the staleness is
`PLAN-audit-scripts-restructure`'s leftover rather than this plan's.
The argument for fixing it here is that the two paragraphs are
precisely the ones that review a criterion addition, which is what
this plan did: leaving them stale spends the 2a agent's whole
budget checking a rule that no longer exists, and produces a
confident report about files the change could not have touched. The
edit is three lines of local prose and one grep. Neither sits
inside a shared block, so no version bumps and nothing in the fleet
becomes non-compliant overnight. Anything else wave 2 finds in the
runbook is reported, not fixed.

**D5.4. This plan's own closeout gaps are corrected in the planning
commit, not treated as audit findings.** The blank `Merged` column,
the false unchanged-documentation claim and the empty bugs section
are records this plan owes regardless of what the audit finds, and
the first of them is an input the audit cannot run without.

**D5.5. One brief addition beyond the runbook, for 2a and 2d.**
`ScopeCoverage.run()` had its failure classification rewritten
twice under review -- a 404 read as a deletion, then a 404 read as
invisibility -- and its suggested fix for the wrong branch is
destructive: delete the entry. Both agents are told to read that
resolution path as the highest-value part of the diff, and to
consider what the check reports when the token degrades mid-run
rather than at the start.

#### Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Correct the two stale passages in `PUSH-AUDIT.md`, and nothing else in that file. First, the four-file rule in the 2a brief at lines 125-127: it says a criterion spans a check function in `scripts/audit-check.py` and metadata in `scripts/audit_common.py` (`AUDIT_METADATA` and `ISSUE_TITLES`). Since `PLAN-audit-scripts-restructure` landed, a criterion spans a `Check` subclass in `scripts/audit/checks/<family>.py`, its registration in `CHECKS` in `scripts/audit/registry.py`, a spec in `docs/audits/<name>.md`, a row in `docs/audits/README.md`, and its frozen lines in `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`. Read `scripts/audit_common.py:35-50` before writing: `AUDIT_METADATA` and `ISSUE_TITLES` are now derived from the registry by `_metadata()` and `_issue_titles()`, so they cannot be edited directly and the issue title is still the fleet-wide idempotency key -- keep that warning, move it to where the title is now declared. Second, the wave 1 grep at lines 64-67 watches `scripts/audit_common.py` for `ISSUE_TITLES` changes and can no longer fire for the case it was written for; retarget it at `scripts/tests/test_metadata.py`. The **Duplicated logic.** bullet already says `scripts/audit/checks/` and is correct -- do not touch it. Every line number in this brief is as of `8b77b32`, before 5a's own edit moved them. Do not touch any `<!-- shared-block: -->` region: a bump files issues fleet-wide. Wrap at the file's existing width. Commit subject: "Correct the push audit runbook after the restructure." |
| 5b | medium | sonnet | none | Wave 1 of `PUSH-AUDIT.md`, over the recorded range. Export `AUDIT_BASE=8b77b32^1` and `AUDIT_HEAD=8b77b32` and read every `git diff main...HEAD` in the runbook as `git diff $AUDIT_BASE $AUDIT_HEAD`; a plain `main...HEAD` is empty here because this work is already merged, which would read as a clean audit. Run `pre-commit run --all-files` on the current tree first -- it is the whole of lint and test -- and run it with the tree clean, because `review-tracking.py stamp` takes its SHAs from the git **index**: an unstaged edit is attested at the staged content, not at what is on disk. An earlier draft of this caution named `test_every_stamp_matches_the_content_it_attests_to` as what gives the false pass; that assertion was deleted from this repository in `6b132a4` on 2026-08-29 for taxing every Renovate bump, and the name reached this brief from `PLAN-audit-compliance-split`'s record of the round that hit it. The clean-tree advice outlived the test. Then run each of the seven greps in the wave 1 block with the substituted range and report every hit with a verdict: hit, looked at, accepted or blocking, and why. Expect and explain rather than ignore: `docs/audits/compliance.md` is not in this range; `REVIEWS.md` is, and the pruning it records is the convention working. Report, do not fix. |
| 5c | high | sonnet | none | Wave 2a, code quality, per the brief in `PUSH-AUDIT.md` -- but read the corrected four-file rule from step 5a, not a cached copy. Diff is `git diff 8b77b32^1 8b77b32`. Take 5b's grep report as input and triage each hit. The highest-value reading is `ScopeCoverage.run()` and its name-resolution path in `scripts/audit/checks/github_config.py`: the classification of a failed lookup was rewritten twice under review, once because every non-zero return landed in "no longer exists" whose suggested fix is destructive, and once because a token that cannot see private repositories answers 404 for all of them. Ask what the check reports when the token degrades part-way through the resolution loop rather than before it, and whether the undecided set -- the half that needs no API access -- survives every failure path. Also check `scripts/audit/scope.py` against `scripts/tests/test_registry.py`: D4 says the parse exists once, so a second copy or a divergent guard is a finding. |
| 5d | high | sonnet | none | Wave 2b, test review, per the brief in `PUSH-AUDIT.md`, over `git diff 8b77b32^1 8b77b32`. `scripts/tests/test_github_config.py` gained 318 lines and `scripts/tests/test_registry.py` lost 179; establish that the second is a move to `scripts/audit/scope.py`'s own tests and not a loss of coverage, naming each assertion that did not survive. The five behaviours D1 and the risks section promised tests for are: a clean scope passes, an unlisted repository fails, a listed name that does not resolve fails, a truncated listing is caught before it is believed, and a repository that is not `development` skips without an API call. Verify each exists and actually tests what its name says on `FakeGitHub`. The truncation test is the one to read hardest -- the risk is a listing cut at the limit being read as eight repositories deleted from the organisation. |
| 5e | high | sonnet | none | Wave 2c, documentation review, per the brief in `PUSH-AUDIT.md`, over `git diff 8b77b32^1 8b77b32`. Four documents moved together and the question is whether they now say the same thing: `docs/audits/scope-coverage.md` (new, 135 lines), `docs/audits/README.md` (the two scope lists and the criterion index row), `AGENTS.md` (the parsed-prose convention) and `ARCHITECTURE.md` (the `audit/scope.py` entry). Check specifically that the excluded list's widened rationale sentence, per D3, actually covers `divergulent-reviews` and `homebrew-tap` rather than still saying "internal only tooling or historical archive repositories"; that no criterion spec has grown a generated status table; that `docs/consistency-audits.md` really did need no change, as the plan claims, by reading its "Adding a criterion" and "Bringing a repository into scope" sections against what phases 2 to 4 did; and that the `README.md` and `docs/ci-review-automation.md` deletions of the imago claim left no dangling reference. |
| 5f | high | opus | none | Wave 2d, security review, per the brief in `PUSH-AUDIT.md`, over `git diff 8b77b32^1 8b77b32`. Read the actual code. This diff adds a check that runs with `AUDIT_TOKEN` and reaches the GitHub API, so the surface is: what reaches a subprocess argument list in `scripts/audit/github.py` and `scripts/audit/checks/github_config.py`, whether any organisation-supplied string (a repository name from the listing) reaches a shell, a path or a URL without validation, and whether a token or any part of one can reach a log line, an issue body or the compliance page on a failure path. `scripts/audit/scope.py` parses `docs/audits/README.md` by literal phrase; consider what a crafted document does to it, bearing in mind the file is in this repository and changing it needs a merged pull request. Apply the `path-traversal-review` shared block. |
| 5g | high | opus | none | Management triage, in the session rather than a sub-agent: read all five reports, decide each finding blocking, advisory or declined, and write the outcome into this section under an **Outcome** heading -- what wave 1 found, what each wave 2 agent found, and for every finding either where it was fixed or why it was declined, in writing. Work the `PUSH-AUDIT.md` management checklist. Fixes land as their own pull request against `main`, not on this branch, per the shared block; this branch carries the plan record. Then set phase 5 to `Complete` in the Execution table with its own merge commit, and set the plan's `docs/plans/index.md` status to `Complete` once the findings pull request has merged -- not before, because the plan is not complete until each finding is resolved or declined. |

Steps 5a and 5b are sequential -- 5c reads the runbook 5a
corrects, and 5b's grep report is 5c's input. Steps 5c to 5f are
independent and are spawned in parallel once 5b passes; the runbook
requires wave 1 to pass before wave 2 is worth spending on. All
steps run in this phase's worktree; `Isolation` says `none` because
none of them is risky enough to want a discardable tree, and 5b to
5f write no code at all.

#### Risks and mitigations

* **A retrospective audit has no gate.** The work is on `main` and
  the criterion has been filing and closing real issues since
  2026-09-04, so a blocking finding is a bug in production rather
  than a push that does not happen. Mitigation: 5g treats a
  blocking finding as a same-day pull request, and the check's
  blast radius is bounded -- `applies()` restricts it to
  `development`, so a defect files or withholds issues on this
  repository only.
* **Wave 1 read against the wrong range.** `main...HEAD` is empty
  on this branch and every grep would return nothing, which reads
  identically to a clean audit. Mitigation: 5b exports the range
  explicitly and its report is required to state the range it used
  and the file count it saw; 5g rejects a report whose file count
  is not 20.
* **5a's edit widens.** `PUSH-AUDIT.md` embeds eight shared blocks,
  and an edit inside one bumps nothing but makes every embedding
  repository's copy stale on the next daily run without the version
  changing. Mitigation: the brief names the exact line ranges and
  forbids touching a `<!-- shared-block: -->` region; 5g diffs
  `PUSH-AUDIT.md` and confirms no line inside a block moved.
* **The audit re-litigates D3.** The 43 onboarding issues are
  visible in `docs/audits/compliance.md` and an agent reading the
  fleet's state may report them as a regression this plan caused.
  Mitigation: they are declared out of scope in this section and in
  D3, and 5g declines any finding that amounts to "these three
  repositories fail many criteria" with that reference.

#### Definition of done

* The Execution table records `8b77b32` for phases 2, 3 and 4, and
  the reconstruction is stated as a reconstruction.
* `git diff --stat 8b77b32^1 8b77b32 | tail -1` reports 20 files
  changed, and 5b's report states that same figure as the range it
  audited.
* `grep -n 'audit-check.py\|audit_common' PUSH-AUDIT.md` returns no
  line claiming a criterion declares a check function or its
  metadata in either file.
* No line inside a `<!-- shared-block: -->` region of
  `PUSH-AUDIT.md` differs from `templates/shared-blocks/`:
  `python3 scripts/audit-check.py --repo-path . --repo-name
  development` still reports `push-audit` passing.
* Every wave 2 finding appears in the Outcome below with a
  disposition, and every disposition that is "declined" says why.
* The Documentation index maintenance section describes the
  `AGENTS.md` and `ARCHITECTURE.md` edits that happened, and Bugs
  fixed during this work is not a placeholder.
* `pre-commit run --all-files` passes.

#### Back brief

One gate, before 5a edits anything: the sub-agent restates the
current four-file rule in its own words, from having read
`scripts/audit_common.py` and `scripts/audit/registry.py`, and
names which file the issue title is declared in now. Getting that
wrong writes a new wrong rule over an old one, and every later
audit in the fleet reads it. The rest of the phase is cheap to
redo, and 5b to 5f produce reports rather than edits.

**Outcome.** Run 2026-09-07 over `8b77b32^1..8b77b32`, the range D5.1
names. Wave 1 passed and the diffstat matched the figure this section
recorded in advance -- 20 files, 1,270 insertions, 295 deletions --
which is the check that the audit read the merged range rather than an
empty `main...HEAD`. `pre-commit` was clean on a verified-clean tree.
Of the seven greps, five returned nothing and two hit and were
accepted: the new imports are stdlib or this repository's own `audit`
package, and the one `# noqa: E402` is a pre-existing suppression that
the diff only added a name to. `templates/shared-blocks/` is untouched,
so nothing in the fleet became non-compliant from this work. `REVIEWS.md`
was regenerated correctly: 13 stale marks pruned and none re-stamped,
172 of 174 becoming 159 of 174 as the two newly in-scope files arrived
unreviewed.

Wave 2 found one blocking defect, five low-severity security findings,
and four advisories. No critical or high security findings.

**2a-1, blocking: an unhandled `UnicodeDecodeError` in
`scripts/audit/scope.py:63`.** `scope.read()` opens with a bare
`open()`. `ScopeCoverage.run()` catches `ScopeParseError` and `OSError`;
`UnicodeDecodeError` subclasses `ValueError`, so it escapes, and
`registry.run_all()` has no per-check exception handling. One non-UTF-8
byte in `docs/audits/README.md` or the audit workflow would abort the
whole `development` leg of the daily run and take issue filing and the
compliance page with it. What makes this more than theoretical is that
`Repo.read()` at `repo.py:124` already reads with `errors='replace'`
and its docstring gives the reason -- "a check that crashes on one file
reports nothing about any of the other criteria." The new module did
not follow the convention its own package had already written down. It
is the same defect class as the `describe_failure()` `IndexError` the
review rounds caught, in a second place, which is the argument for
auditing an accumulated range rather than trusting the review that
looked at each commit.

**2d-3.1, low, and the one worth more than its rating: the rename
suppression is owner-blind.** `github_config.py:459` subtracts
`canonical.split('/')[-1]`, discarding the owner. A repository
transferred out of the organisation still redirects, so its canonical
name can be `otherowner/target`; if `shakenfist/target` exists and is
in neither list, its genuine finding is silently suppressed. That is
the third state this criterion exists to remove, reappearing inside the
criterion.

**2d-3.2, low: a transfer out of the organisation is reported as a
rename**, and told to write the new name into the matrix -- which
`consistency-audit.yml:52` cannot express, because it clones
`shakenfist/${{ matrix.repo }}`. The correct advice there is to remove
the entry.

**Two stale documentation references, found by the sweep 2c was asked
to run rather than by the diff.** `docs/consistency-audits.md:24` still
calls a criterion "a function" in `scripts/audit-check.py` and
contradicts its own "Adding a criterion" section further down the same
page; and `templates/ci-review-automation/README.md:428` still names
imago in the present tense, after the rename to instar. Neither was
introduced by this work. Both are the same staleness class that step 5a
fixed in `PUSH-AUDIT.md`, which says the restructure's sweep was
narrower than it looked.

Those four are fixed on `scope-coverage-audit-findings`, branched from
`main` rather than from this phase's branch, because the shared block
requires findings to land as their own pull request. Four commits, one
per fix; the three code fixes each carry a regression test confirmed to
fail against the unfixed code, and the documentation fix carries none,
being prose. `scope.read()` now replaces undecodable bytes the way
`Repo.read()` does; the rename classification sorts three ways --
invisible, renamed inside the organisation, moved out of it -- which
makes the suppression safe by construction rather than by a guard, and
gives a transfer out the advice that fits it; and the two stale
documentation references are corrected. At `812d87a`, `pre-commit` was
clean, `scope-coverage` still passed on this repository, and the audit
package suite ran 874 tests. The 871 first recorded here was `main`'s
count rather than the branch's -- the same class of unchecked figure
this phase exists to catch, found by re-running it. Six review marks
were pruned, each in the commit that invalidated it, which is why
`review-coverage` reported 22 files needing review rather than 16.

The automated review of that pull request added three more commits, and
one of them is a finding in its own right. Two review marks had lost
their stamps: two of the four fixes pruned by hand, deleting the
sidecar entry and leaving the `auditedFiles` entry behind. Nothing
could see it -- `prune` only removes a mark whose stamp is stale and
there was no stamp to be stale, and `regen` reproduces the blank
attestation columns faithfully, so the reproducibility test compared
equal -- while `REVIEWS.md` counted both files as reviewed and
`review-tracking.py status` counted them as needing review. The marks
are dropped, and a test now fails when any mark carries no stamp. The
other two commits take the review's suggestions: the stale
`REPO_OVERRIDES` reference it found in `docs/consistency-audits.md` is
corrected together with two more of the same class that a grep turned
up, and the organisation-prefix comparison and the succeed half of the
`errors='replace'` promise each gain the test that pins them.

The remainder were declined, in writing, here:

* **2d-2.1, private repository names reach a public issue.** When the
  check fails, the names of undecided repositories -- private ones
  included -- land in a public issue on this repository, in the public
  workflow log, and in a 30-day artifact. Contents never leak, only
  names. Accepted as a deliberate design consequence rather than
  fixed: the excluded list in `docs/audits/README.md` is itself public
  and already names `jenkins-private`, `private-ci` and `deploy`, so
  the edit the check asks for publishes the name anyway. Filtering the
  names out while keeping the count would make the issue harder to act
  on and would not change what the fix discloses. That argument covers
  a repository already named in a public list; it does not cover the
  case this criterion exists for, a repository created and in neither
  list, whose name reaches the public issue automatically at the next
  06:00 UTC run before anyone has decided anything about it. The
  declination still holds, because every resolution the criterion can
  ask for -- a matrix row or an excluded-list entry -- publishes the
  name in a public document, so the audit shortens the delay rather
  than creating the disclosure; a repository whose existence must stay
  quiet needs its decision recorded before it is created. If that ever
  stops being acceptable, the alternative is reporting undecided
  repositories by count in the issue and by name only in the workflow
  log.
* **2d-4.1, un-neutralised subprocess stderr into an issue body.**
  Real, and shared with five pre-existing checks rather than introduced
  here; the compliance page path is already protected by `defuse()` in
  `audit-update-docs.py`. Fixing one instance of a fleet-wide pattern
  inside an audit phase would leave the other five and misrepresent the
  problem as solved. Declined here and worth its own sweep.
* **2d-7.1, a fine-grained token can still reach the destructive
  branch.** `sees_private` is a bulk signal and cannot distinguish a
  token that sees all private repositories from one that sees some, so
  a fine-grained PAT scoped to selected repositories would 404 on the
  rest and report them as deleted. Declined as a code change: it needs
  someone to rotate `AUDIT_TOKEN` to a fine-grained PAT, no
  attacker-influenced path reaches it, and `audit-manage-issues.py`
  acts on none of this -- the destructive recommendation is prose a
  person reads. Recorded under Future work.
* **2b, `scripts/audit/scope.py` has no `test_scope.py`.** Declined.
  Its behaviour is covered across `test_registry.py` and
  `test_github_config.py`, and 2b confirmed that coverage by mutation
  rather than by reading. This is a file-naming convention note, not a
  gap.
* **2a-2 and 2a-3, two duplicated-logic notes.** Both declined, and 2a
  argued against acting on them itself. `matrix_repos()` deliberately
  does not reuse the indentation-agnostic `indented_block()`, because
  this module wants a reindent to raise rather than to keep working;
  and `gh_canonical_repo()` cannot be reused because it silently falls
  back to the input name on any failure, which is precisely the
  ambiguity between a 404, a rate limit and a deletion that this check
  exists to resolve.

Two things the audit confirmed that are worth recording because they
were the plan's own risks. The truncation guard is real: 2b mutated it
to count unique names instead of raw entries, and only the test written
for its pre-deduplication property caught the change. And the
`applies()` guard genuinely costs nothing on the other nineteen
repositories -- neutering it made the test fail, because `FakeGitHub`
records every call and the test asserts there were none.

The 179-line reduction in `scripts/tests/test_registry.py` was checked
assertion by assertion and is a faithful move into
`scripts/audit/scope.py`, with the phrase-anchoring guards intact and
now raising `ScopeParseError` rather than asserting. All five
regression tests from the review rounds kill their mutants. 868 tests
pass.

Editing `PUSH-AUDIT.md` staled its own review mark, and 5a did not
prune it -- the sibling findings branch pruned six marks in the commits
that invalidated them, and this branch should have done the same.
Pruned here instead, so `PUSH-AUDIT.md` now needs re-reading.
`prune-reviews.yml` reaps a stale mark on the next push to `main`
either way, so the end state was never in doubt; what a missing prune
costs is that the pull request does not show its reviewer that a
reviewed file has changed under them.

## Risks and mitigations

* **`gh repo list` truncates at 30 by default.** The organisation has
  38 repositories, so the default limit silently loses eight and the
  check reports them as unlisted. Pass an explicit high limit, or
  paginate `orgs/<org>/repos`. Covered by a test that scripts a
  listing at the limit.
* **A token that cannot see private repositories.** `performance`,
  `private-ci` and `jenkins-private` are private and on the excluded
  list. A listing without them would report three dead exclusions
  every morning, and the fix that implies -- deleting the exclusions
  -- would be actively harmful. Planned as a heuristic (fail if the
  listing contains no private repositories at all); built instead as
  evidence, because the heuristic is wrong in both directions in an
  organisation that has none. Every name missing from the listing is
  resolved directly against the API, which answers the question
  rather than guessing at it.
* **A renamed repository.** It appears in the listing under its new
  name and in the matrix under its old one. Resolving the old name
  follows the rename redirect, so the finding names the new name and
  says what to write down, rather than reporting a repository that
  does not exist. This is the same trap `gh_canonical_repo()` exists
  for: the API follows a rename, issue listing and search do not.
* **43 issues on the first run.** Expected, per D3. Worth saying out
  loud in the pull request so it is not read as the audit having
  broken.

## Administration and logistics

### Success criteria

* Every one of the 38 repositories in the organisation is in exactly
  one of the two lists, and the three dead exclusions are gone.
* `scope-coverage` passes on `development` on the first run after
  merge, and fails if a repository is added to the organisation
  without a decision.
* `python3 -m unittest discover -s scripts -t scripts` and
  `pre-commit run --all-files` both pass.
* The parse of the scope lists exists once.

### Documentation index maintenance

`docs/audits/README.md` gains the criterion's index line in phase 4 and
its scope changes in phase 2. `docs/consistency-audits.md` needs no
change: "Adding a criterion" and "Bringing a repository into scope"
both already describe what these phases do.

This section originally predicted that `AGENTS.md` and
`ARCHITECTURE.md` would be unchanged, on the grounds that no
convention moves and the shape of the system does not change. Both
predictions were wrong, and D4 is why. Lifting the scope parse out of
`AuditScopeIsStatedOnceTest` and into `scripts/audit/scope.py` moved a
convention -- `AGENTS.md` now tells an agent that `audit/scope.py`
reads the scope lists and raises `ScopeParseError`, where it named the
test -- and added a component, so `ARCHITECTURE.md` gained the
`audit/scope.py` entry beside `audit/registry.py`. Both edits landed
in `8b77b32` and both belong where they are; it is the prediction that
was wrong, corrected here in phase 5.

### Future work

* The dry runs measured `uncalibrated-sextant` and `visual-digest-rust`
  on feature branches rather than their default branch, and without
  `skillsaw` installed, so `llm-context-lint` was N/A in both. The
  first real run is the authoritative count.
* The 43 issues are a backlog for `standards-alignment`, not for this
  plan.
* Nothing checks the *private* GitLab projects the same way. The same
  blindness exists there and the fix does not transfer, since the
  audit only knows about GitHub.
* This repository has no `tools/audit/plan-range.sh`. kerbside built
  one, turning a plan's recorded merge commits into `AUDIT_RANGE` and
  `AUDIT_PATHS`, which is what makes auditing an accumulated merged
  range a script's input rather than a note for a reader. Phase 5
  supplies its range by hand instead; see D5.2 for why porting it is
  not this plan's work.
* Every diff command in `PUSH-AUDIT.md` is still written
  `main...HEAD`, so a stale local `main` silently widens the audit and
  a merged range produces an empty diff that reads as a clean audit.
  Recorded first under Future work in `PLAN-audit-compliance-split.md`
  and still owed; phase 5 is the second audit to work around it by
  hand. Phase 5 deliberately does not fix it -- D5.3 fixes only the
  two passages that describe this repository incorrectly, and the
  `main...HEAD` question belongs with whoever ports `plan-range.sh`.
* `sees_private` is a bulk signal. A fine-grained token scoped to
  selected repositories sees at least one private repository, so the
  guard passes, and then 404s on every repository it was not scoped
  to -- which the check reports as deleted. Nobody can reach this from
  outside; it needs `AUDIT_TOKEN` rotated to a fine-grained PAT. Worth
  answering before that rotation happens rather than after.
* Un-neutralised subprocess stderr reaches an issue body from six
  checks, of which `scope-coverage` is one. The compliance page is
  already protected by `defuse()` in `audit-update-docs.py`; the issue
  path is not. A sweep of all six, rather than a fix to one.
* `REPO_NAME` in `scripts/audit/scope.py` rejects uppercase letters and
  underscores, both of which are legal in a GitHub repository name. No
  repository in the organisation uses either today, so the check
  measures correctly; the first one that does would make the criterion
  fail and the unit test error rather than report anything useful.

### Bugs fixed during this work

All five were found by the two rounds of automated review on pull
request 93 and fixed in `a7a100b` and `03046b2`, before the criterion
had ever run for real.

* **Every failed name resolution was classified as a deletion.** A
  non-zero return from the API landed in "this repository no longer
  exists", whose suggested fix -- delete the entry from the list -- is
  the one destructive action the check can recommend. GitHub answers
  404 rather than 403 for a private repository a token cannot see, so
  an expired token, a rate limit and a genuinely deleted repository
  all arrived as the same finding.
* **A 404 was still read as a deletion when the token could see no
  private repositories at all.** In that state a 404 carries no
  information, because the token answers 404 for every private
  repository in the organisation; the check would have reported the
  three private exclusions as dead every morning, with no edit to the
  lists that could clear it. Those names are now reported as
  unresolvable, and the finding names the token.
* **`describe_failure()` raised `IndexError` on whitespace-only
  stderr.** The guard tested `result.stderr` for truthiness and then
  indexed the first line of its stripped form, which is empty for
  `'   \n'`. `registry.run_all()` has no per-check exception handling,
  so this would have aborted the whole `development` leg of the daily
  run and taken issue filing and the compliance page with it.
* **One slow API call discarded the undecided set.** The resolution
  loop caught exceptions around the whole loop rather than per name,
  so a single failure threw away the half of the result that needs no
  API access -- which is the half the criterion exists for.
* **An empty listing was reported as every name being invisible**
  rather than refused, and a renamed repository whose new name is in
  neither list was reported twice, once as a rename and once as
  undecided, when both findings ask for the same single edit.

### Back brief

Before phase 2 begins, confirm the D3 table is still what Mikal wants:
it is the only part of this plan that cannot be derived from the
repository, and every later phase assumes it.
