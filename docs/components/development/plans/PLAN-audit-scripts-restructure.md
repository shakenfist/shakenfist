# Plan: restructure the audit scripts into a package of check classes

## Prompt

Before executing any part of this plan, read the code it moves.
`scripts/audit-check.py` is 6,657 lines and its test file is 6,148;
neither can be held in your head, and this plan's central risk is a
line that changes while looking like it did not.

Read `docs/consistency-audits.md` first -- it is the reference for
what a daily run does and how a criterion is added -- then
`AGENTS.md` for the invariants that are not visible in the code, and
`ARCHITECTURE.md` for how the pieces fit. `PUSH-AUDIT.md` is the
pre-push runbook this plan's last phase runs, and its "Duplicated
logic" bullet currently tells reviewers something this plan changes.

The two standing constraints from `PLAN-TEMPLATE.md` bind hard here.
The blast radius is other people's repositories: this tooling files
and closes GitHub issues fleet-wide every morning, so a regression
does not produce a red build. And this repository is inside its own
audit matrix, so it is measured by the tooling it is changing while
the change is in flight.

Ground every claim in the code. Where this plan quotes a number --
45 checks, 21 `gh` calls, 20 untested checks -- re-derive it before
relying on it; the tree moves.

## Situation

`scripts/` is the whole of this repository's code, grown by
accretion one criterion at a time, and the growth has landed almost
entirely in two files:

| File | Lines | What is in it |
|---|---|---|
| `audit-check.py` | 6,657 | 45 check functions, ~90 helpers, ~40 module-level constant and regex blocks |
| `test_audit_check.py` | 6,148 | 45 `TestCase` classes, 107 temporary-directory sites, ~40 per-class `_repo`/`_check` helpers |
| `test_audit_update_docs.py` | 786 | |
| `test_review_tracking.py` | 736 | |
| `review-tracking.py` | 636 | a separate CLI, `cmd_*` dispatch |
| `test_issue_fix_extraction.py` | 530 | |
| `audit-update-docs.py` | 379 | |
| `audit-manage-issues.py` | 330 | |
| `audit_common.py` | 323 | `AUDIT_METADATA`, `ISSUE_TITLES` -- data only |
| `test_check_audit_smoke.py` | 132 | |
| `check-audit-smoke.py` | 90 | |
| `commit-audit-docs.sh` | 31 | |

Size on its own is not a reason to move code, and `PUSH-AUDIT.md`
says so to reviewers today: "`audit-check.py` is 5,000 lines of
check functions that resemble each other by design. Flag duplication
only where a helper already exists and was not used." That is right
about the check bodies and wrong about what surrounds them. Three
specific things are wrong, and each is measurable.

**Twenty of the forty-five checks have no direct test.** Counting
call sites of `audit_check.check_*(` in `test_audit_check.py`:

| Coverage | Checks |
|---|---|
| No direct test | `default-branch-naming`, `github-security`, `delete-branch-on-merge`, `merge-queue-config`, `export-repo-config`, `devpi-fallback`, `devpi-stale-ip`, `flake8wrap`, `llm-tooling`, `pre-commit-config`, `pyproject-usage`, `readme-absolute-links`, `release-process`, `rust-unwrap-lint`, `secret-scanning-ci`, `static-runner-tags`, `version-file-gitignore`, `workflow-permissions` (plus the two `check_file_*` helpers) |
| One or two | 21 checks |
| Three or more | 6 checks |

The untested set is not random. Every check that queries the GitHub
API is in it, because there is nothing to fake: `audit-check.py`
makes 21 direct `subprocess.run(['gh', ...])` calls, each carrying
its own copy of the timeout and `FileNotFoundError` handling. The
workaround already exists, applied by hand and exactly once --
`evaluate_merge_queue_rules()` was split out as a pure function so
`MergeQueueConfigTest` could test the ruleset logic, and the `gh`
wrapper around it stayed untested. That is the right instinct with
nowhere to put itself.

**The check id is a string literal repeated two to nine times per
check.** `'id': 'python-version-targeting'` appears nine times;
`'id': 'pyproject-usage'` eight. Roughly 180 literals that must
agree with each other and with the id registered in
`check_calls()`. A test asserts the last of those agreements,
because that is the one that would make a check unschedulable; the
rest are unguarded.

**A criterion spans four files, and the sync is enforced by tests
rather than by structure.** `AGENTS.md` opens with it and
`docs/consistency-audits.md` spells it out over five numbered steps,
noting that step 4 "is the one that bites" -- its absence broke the
2026-08-12 run, which rewrote every `docs/audits/*.md` before
crashing without committing any. The tests that now guard it are
good tests. They are also evidence that the shape is wrong: an
invariant needs a test because the structure cannot state it.

Underneath all three is the same absence. There is no `Check` type,
no repository type and no GitHub client type, so there is nowhere
for an id, a spec path, an issue title, a cached file read or a fake
API response to live.

## Mission and problem statement

Turn `audit-check.py` into a package where a criterion is a class in
one module beside its siblings, every check is testable including
the ones that talk to GitHub, and the id is written once. Adding a
criterion should touch a check module and a spec page, not four
files kept in step by tests.

The measure of success is negative: the audit must say exactly what
it said yesterday about the real fleet, at every commit along the
way.

Deliberately out of scope:

* **Changing what any criterion means.** No check gets stricter,
  looser or newly applicable. A `details` string that is genuinely
  wrong changes in its own commit, with the compliance-page effect
  stated in the message.
* **`review-tracking.py`.** It shares a directory with the checker
  and nothing else. Recorded under Future work.
* **New dependencies.** The scripts are stdlib-only and stay that
  way.
* **Moving the entry point.** `scripts/audit-check.py` keeps its
  path, its arguments and its JSON.

## Open questions

* **Does `audit_common.py` survive as a module?** This plan keeps
  the name and its two exports so that `audit-manage-issues.py` and
  `audit-update-docs.py` are untouched, and makes the exports
  derived views. Folding it into `scripts/audit/registry.py` would
  be tidier and would spread the change into two more scripts.
  Default if nobody answers: keep the module.
* **Is one pull request per phase right for phase 3?** Eight commits
  in one pull request is a large review; eight pull requests is
  eight mornings of a half-migrated scheduler. Default: one pull
  request, eight commits, with the phase not complete until
  `check_calls()` is empty.
* **Should the snapshot harness live in `tools/` permanently, or be
  deleted with the plan?** It is useful beyond this work -- any
  change to a check wants it -- so the default is to keep it and
  document it in `docs/consistency-audits.md`.

## Decisions

### D1. Classes at three seams, and nowhere else

Three types earn their existence:

* **`Check`** -- an abstract base holding `id`, `spec`, `template`,
  `issue_title` and an optional `column` as class attributes, with
  `applies(repo)` returning a skip reason or `None`, and an abstract
  `run(repo)` returning a `Result`. `self.ok()`, `self.fail()` and
  `self.skip()` build the result dict, so the id is written once.
* **`Repo`** -- replaces the `(repo_path, props, repo_name, org)`
  tuple threaded through every signature, with cached accessors:
  `exists()`, `read()`, `workflows()`, `docs_markdown()`. Caching is
  not premature: `list_workflow_files()` is called from 14 places
  and there are 41 `open()` sites, so a daily run re-reads every
  workflow file a dozen times.
* **`GitHubClient`** -- a protocol with a `gh` CLI implementation
  and a fake. This is the one that buys the missing coverage; the
  other two are tidying.

Everything else stays a module-level function. `mask_source()`,
`evaluate_merge_queue_rules()`, `split_runner_labels()`,
`blank_generated_blocks()` and their kin are already the most
testable code in the file. They move to new homes unchanged. A
refactor that turns pure functions into methods to make a diagram
tidier makes the tests worse, and we would notice only afterwards.

### D2. Modules by spec family, not one file per check

Forty-five files would trade one unnavigable file for a directory
nobody can hold in their head, and it would fight the code: the plan
checks share their `PLAN_*` regexes, the runner checks share label
parsing, the LLM-doc checks share heading iteration. Eight modules
of four to seven hundred lines, grouped the way the specifications
are:

    scripts/audit/
      check.py            Check, Result, the status vocabulary
      repo.py             Repo, detect_repo_properties, REPO_OVERRIDES
      github.py           GitHubClient, GhCli, FakeGitHub
      registry.py         CHECKS, and the metadata views derived from it
      text/
        markdown.py       links, fences, headings, code stripping
        workflows.py      job blocks, runs-on labels, concurrency
        python_source.py  mask_source, class parsing, entry points
        shared_blocks.py  block extraction and validation
      checks/
        llm_docs.py  ci_workflows.py  runners.py  packaging.py
        docs_content.py  plans.py  review.py  github_config.py
    scripts/audit-check.py   a CLI shim: argument parsing, JSON out

The `text/` split is where the ~40 module-level regex blocks go, and
it is the part with real reuse in it today. It also ends the
`importlib.util.spec_from_file_location` dance the tests need,
because `audit-check.py` has a hyphen and cannot be imported.

### D3. The registry becomes the source of truth, and the metadata is derived

`registry.py` holds `CHECKS`, a list of instances.
`audit_common.AUDIT_METADATA` and `ISSUE_TITLES` become views
computed from it, and `audit-update-docs.py`'s `COLUMN_NAMES`
derives from `Check.column`. Neither `audit-manage-issues.py` nor
`audit-update-docs.py` changes: they keep importing the same two
names from the same module and get the same dicts.

Adding a criterion then means a check class and a spec page. Steps 2
and 4 of `docs/consistency-audits.md` disappear, and the tests that
guard them become unnecessary rather than merely passing.

**`ISSUE_TITLES` is an idempotency key, not a label.** A renamed
entry orphans every open issue for that check across the fleet,
which is why `AGENTS.md` calls it an interface. Deriving it puts 45
strings within reach of an accidental edit, so the derivation is
pinned: a test holds today's `AUDIT_METADATA` and `ISSUE_TITLES` as
literal frozen dicts and asserts the derived views equal them
exactly. That is not maintenance -- a new check adds a line, and a
changed line is supposed to be hard.

### D4. `audit-check.py` keeps its path and its output

The daily workflow, `ci.yml`, `README.md` and three documentation
pages name the script by path. Making the package importable does
not require moving the entry point, and moving it would spread this
change into the workflows for no gain. It becomes a shim that parses
arguments, builds a `Repo`, runs the registry and prints the same
JSON.

The JSON is a contract in a stronger sense than most: `details`
strings are rendered into `docs/audits/compliance.md` and into issue
bodies filed fleet-wide. A reworded detail is a diff on the
compliance page and, worse, is invisible in review.

### D5. The regression net is a local before-and-after diff, not a committed fixture

Correctness here means "the audit says exactly what it said
yesterday about the real fleet". Synthetic fixtures cannot show
that; the real clones can.

So the net is `tools/audit-snapshot.sh <clones-dir> <out-dir>`,
which runs the checker over every clone and writes one JSON file
each, plus a comparison run after each phase. The snapshots are not
committed. Generated JSON in `scripts/` or `docs/` would land in
review scope -- `.vscode/review-scope.toml` includes `*.json` -- and
would either need an exclusion argued for or would sit permanently
stale in the review queue. A repeatable command in `tools/` and a
scratch directory get the same guarantee without that.

Two wrinkles, both real:

* The `timestamp` field moves on every run and is stripped before
  comparison.
* Six checks reach the network and so are not deterministic across
  two runs: a repository setting can genuinely change in between.
  They are `default-branch-naming`, `github-security`,
  `delete-branch-on-merge`, `merge-queue-config`,
  `merge-group-cancellation` (through `merge_queue_is_serial`) and
  `sfui-vendor` (which clones). Until the `GitHubClient` seam exists
  they are advisory in the diff. From phase 2 onward a recording
  implementation captures the `gh` responses on the "before" run and
  replays them on the "after" run, so the five API checks can be made
  exact.

  `sfui-vendor` cannot, and stays advisory permanently. It does not
  make a request and read a response; it clones the canonical
  repository into a temporary directory and runs that repository's
  own `tools/vendor.sh` against the working tree it produced.
  Recording a working tree is not the same problem as recording a
  response, and it already has the seam it needs: `canonical_url` is
  a parameter, and `SfuiVendorTest` drives it against a local fixture
  repository. It is among the better tested checks here, not one of
  the gaps.

  Note that this set is *not* the `github_config` family of phase 3.
  `export-repo-config` makes no API call, while
  `merge-group-cancellation` and `sfui-vendor` sit in other families
  that migrate earlier -- which is why phase 2 routes every `gh`
  call through `GhCli` rather than leaving it to the family that
  looks like it owns them.

### D6. `applies()` is separate from `run()` so scoping stays cheap

`check_calls()` returns deferred lambdas for a stated reason: a
repository scoped with `only_checks` must be able to skip a check
without paying for it, because several checks query the GitHub API
and those queries fail on a private repository for reasons that have
nothing to do with compliance. Splitting the cheap applicability
test from the expensive body preserves that: the scheduler consults
`only_checks`, then `applies()`, and calls `run()` only if both let
it through. It also lifts the `not_applicable` preamble out of most
of the 45 function bodies, which is where a good deal of the
repetition lives.

### D7. Tests move with their checks, on a shared fixture base

`scripts/tests/` mirrors `scripts/audit/`, one test module per check
module. Two pieces of shared machinery replace what is copied per
class today:

* `FixtureRepo` -- `write()`, `workflow()`, `git()`, `commit()`,
  wrapping the temporary directory. There are 107
  temporary-directory sites and about 40 near-identical
  `_repo`/`_check` helpers.
* `CheckTestCase` -- `assert_pass()`, `assert_fail(containing=...)`,
  `assert_skip()`.

And one thing that does not exist today: contract tests
parametrized over the whole registry. Every check has a spec file
that exists on disk; is registered exactly once; returns one of the
three statuses; and survives an empty repository, a docs-only
repository and a directory that is not a git checkout without
raising. That last group is the one with real value, and it is only
writable once the checks are uniform.

The `GIT_*` environment scrub at the top of `test_audit_check.py`
moves to the shared base. It exists because the pre-commit hook runs
these tests during `git commit`, when git exports `GIT_INDEX_FILE`
to hooks and the fixture subprocesses inherit it and write to the
real index. Losing it in the move would corrupt the index of
whoever commits next.

### D8. Sequencing against the human review of `scripts/`

A whole-file review mark attests to content by blob SHA and is
discarded when the file changes, so this restructure discards every
mark on every file it moves. A review of `scripts/` is in flight
right now -- `origin/reviews` carries a fresh mark on
`scripts/audit_common.py`, dated 2026-09-01 -- and `audit_common.py`
is precisely the file phase 4 turns into derived views.

The restructure goes first. Attesting to 6,657 lines that are about
to be split into eight modules spends the scarcest thing here on
content that will not survive, and the review is worth more against
the structure we intend to keep. Two consequences to accept openly:

* Review coverage in `REVIEWS.md` drops when this lands, and the
  `review-coverage` criterion may file an issue against this
  repository. That is the check working. It is not silenced.
* Findings from the in-flight review are not wasted and must not be
  dropped. Any still unaddressed when phase 3 starts are carried
  into "Bugs fixed during this work" and fixed in the module they
  land in.

## Execution

Work happens on `audit-scripts-restructure` off `main`; this plan
file lands with the change. There is no `develop` branch here --
`REPO_OVERRIDES` exempts this repository from the default-branch
criterion because it publishes no releases.

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Freeze today's behaviour | Complete | 76975d9..42565c2 |
| 2. Introduce the three seams | Complete | 76975d9..42565c2 |
| 3. Migrate the checks, one family per commit | Complete | 76975d9..42565c2 |
| 4. Make the registry the source of truth | Complete | 76975d9..42565c2 |
| 5. Close the coverage gap | Complete | 76975d9..42565c2 |
| 6. Push audit | Complete | 76975d9..42565c2 |

All six phases ship as a single pull request, so the `Merged` record
is one entry rather than six. The merge commit's SHA cannot be known
before the merge, and the plan is being closed with the work rather
than reopened afterwards to write it down, so the range is recorded
in the form `plan-push-audit-phase` allows for a phase that landed
directly: `76975d9..42565c2`, seventeen commits, the whole of this
branch. `git log --first-parent 76975d9~1..42565c2` is what a later
audit reads.

If the branch is squashed or rebased on the way in, those SHAs stop
resolving. Say so on the pull request if that happens, and replace
them with the merge commit -- that is the one edit worth a follow-up,
and it is a one-line one.

The branch also carries a merge of `origin/main`, taken after the push
audit because `main` moved while this was in flight and `REVIEWS.md`
conflicted. That is outside the audited range deliberately: the merge
resolves one generated file by regenerating it, and re-auditing to
cover a rendering of the review marks would say nothing. The audited
range is the one recorded above.

Phases 1 and 2 are small and ship together as one pull request.
Phase 3 is the bulk of the work and ships as its own, one commit per
check family, because a reviewer can read a family in one sitting
and cannot read eight. Phases 4 and 5 ship together: phase 4 deletes
the sync tests and phase 5 replaces the coverage they were standing
in for, and landing the deletion alone would leave the repository
briefly worse.

Phase 3 has a hard rule: it does not end with the registry and
`check_calls()` both populated. A half-migrated scheduler is the
failure mode this plan is most likely to produce, and the audit runs
every morning regardless of what we have finished.

### 1. Freeze today's behaviour

* **`tools/audit-snapshot.sh`** (new). Takes a directory of clones
  and an output directory, runs `scripts/audit-check.py` over each
  clone, writes `audit-result-<repo>.json` per repository, and
  strips the `timestamp` field. A `--diff <old> <new>` mode reports
  per-check differences, listing the six network-dependent checks
  separately as advisory until phase 2. The advisory list is
  re-derived from `audit-check.py` by a test, so a check that grows
  a `gh` call cannot leave it stale.
* **`docs/consistency-audits.md`** gains a short subsection under
  "Testing a change" describing the before-and-after procedure, so
  it is available to work after this plan as well.
* Capture the baseline over the fleet clones under
  `~/src/shakenfist/` and keep it for the duration.

Nothing else changes. This phase is the reason the rest can be
judged.

### 2. Introduce the three seams

* **`scripts/audit/check.py`** -- `Result`, the status vocabulary,
  and the `Check` base with `applies()`/`run()` and the
  `ok`/`fail`/`skip` constructors.
* **`scripts/audit/repo.py`** -- `Repo` with the cached accessors,
  plus `detect_repo_properties()` and `REPO_OVERRIDES` moved
  verbatim. `Repo.props` keeps the existing dict so migrated and
  unmigrated checks read the same thing.
* **`scripts/audit/github.py`** -- `GitHubClient`, `GhCli` holding
  the timeout and `FileNotFoundError` handling once, `FakeGitHub`
  taking a dict of responses and able to inject failures, and
  `RecordingGitHubClient` for the snapshot harness.
* **`scripts/audit/registry.py`** -- `CHECKS`, empty, and a
  scheduler that runs the registry and then the surviving
  `check_calls()` entries.
* **`scripts/audit-check.py`** becomes the shim. `run_all_checks()`
  keeps its name and signature; `test_check_audit_smoke.py` and
  `ci.yml` both drive it.

Behaviour is unchanged and the snapshot must be identical. From here
the API checks replay recorded responses, so the diff is exact for
all 45.

### 3. Migrate the checks, one family per commit

Eight commits, in ascending order of risk, so the pattern is settled
before it meets the checks that matter most:

1. `llm_docs.py` -- `llm-tooling`, `llm-doc-structure`,
   `llm-context-lint`, `llm-context-lint-ci`.
2. `docs_content.py` -- `readme-structure`, `readme-absolute-links`,
   `docs-external-links`, `diagram-format`, `mermaid-lint-ci`.
3. `plans.py` -- `plan-index`, `plan-template`,
   `plan-source-references`, `plan-phase-references`, `push-audit`.
4. `packaging.py` -- `pyproject-usage`, `version-file-gitignore`,
   `python-version-targeting`, `pin-indirect-dependencies`,
   `dependency-name-normalization`, `renovate`, `release-process`,
   `rust-unwrap-lint`, `flake8wrap`, `console-logging`,
   `header-sanitization`.
5. `runners.py` -- `self-hosted-runners`, `static-runner-tags`,
   `vm-runner-size`.
6. `ci_workflows.py` -- `workflow-permissions`, `pre-commit-config`,
   `expensive-lane-path-filter`, `merge-group-cancellation`,
   `ci-review-automation`, `secret-scanning-ci`, `devpi-fallback`,
   `devpi-stale-ip`.
7. `review.py` -- `review-coverage`, `review-scope-completeness`,
   `review-marks-pre-commit`, `sfui-vendor`.
8. `github_config.py` -- `default-branch-naming`, `github-security`,
   `delete-branch-on-merge`, `merge-queue-config`,
   `export-repo-config`. Last, because all five are untested and
   four of them query the GitHub API, so by now there is a fake to
   write them against. `export-repo-config` is in the family for
   what it is about rather than for how it works; it reads the
   filesystem.

Each commit moves the checks, moves their shared constants into
`text/`, moves their tests to `scripts/tests/`, converts those tests
to the shared fixture base, and empties the corresponding entries
from `check_calls()`. Each commit runs `pre-commit run --all-files`
and the snapshot diff, and each is expected to produce an empty
diff.

Detail strings are copied, not rewritten.

### 4. Make the registry the source of truth

* `AUDIT_METADATA` and `ISSUE_TITLES` become derived views in
  `audit_common.py`, computed from `registry.CHECKS`. The module
  keeps its name and its exports.
* `COLUMN_NAMES` in `audit-update-docs.py` derives from
  `Check.column`. `column_name()` keeps its warning-and-ugly-heading
  fallback: a run that publishes a bad label still beats a run that
  publishes nothing.
* A frozen-snapshot test pins both derived dicts to today's values.
* The cross-file sync tests that are now structurally impossible to
  break are deleted, and the ones that are not are kept and said to
  be kept.
* Documentation: `docs/consistency-audits.md` "Adding a criterion"
  becomes two steps; `AGENTS.md` loses the four-files paragraph and
  keeps the `ISSUE_TITLES` warning, repointed; `ARCHITECTURE.md`
  gets the new module inventory; `README.md`'s link to
  `scripts/audit-check.py` still resolves and stays;
  `PUSH-AUDIT.md`'s "Duplicated logic" bullet is rewritten, because
  it currently tells reviewers that these functions resemble each
  other by design.

### 5. Close the coverage gap

Write the missing tests, against the seams rather than around them.
The 18 checks with no test get one each at minimum -- pass, fail and
not-applicable. The five that call the GitHub API additionally get
the failure paths that have never been exercised: API error,
timeout, `gh` absent, private repository. `sfui-vendor` is not among
them -- it reaches the network but already has its own seam and its
own tests.

Then the registry contract tests from D7.

The measure is not a coverage percentage. It is that every check in
`CHECKS` appears in a test module, asserted by a contract test, so
the next check cannot arrive untested.

### 6. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1 to 5
against `main`, not phase by phase. Findings land as their own pull
request; the plan is not complete until each is resolved or declined
in writing, with the reason recorded here. If the audit finds
nothing, say so in one sentence.

The brief worth stating in advance: this is a move of roughly 13,000
lines, and the failure mode of a move is a line that changed while
looking like it did not. The audit should read for behaviour drift
in detail strings and in regex constants above everything else, and
should check that no check lost its `applies()` guard on the way
into a class.

**Outcome.** Wave 1 passed. `pre-commit` clean; no line over 120
characters; no import outside the standard library and this package;
no edit to `docs/audits/compliance.md`; no generated block in a
specification; `templates/shared-blocks/` untouched, so nothing is
newly non-compliant fleet-wide. Two greps hit and both were looked
at: `ISSUE_TITLES` changed, which is phase 4's derivation and is
pinned by `scripts/tests/test_metadata.py`; and two `TODO` matches
are pre-existing fixture text moved verbatim rather than new debt.
`REVIEWS.md` is current and every mark on an edited file was pruned
rather than re-stamped.

The brief's own question was answered directly rather than by
reading. Seven criteria have neither an `applies()` method nor a
`skip()` path -- `llm-tooling`, `renovate`, `ci-review-automation`,
`pre-commit-config`, `export-repo-config`, `github-security` and
`delete-branch-on-merge`. Checking each against the phase 1 tree:
none of the seven ever reported `not_applicable`, so no guard was
lost. They are pass-or-fail criteria by design.

One finding, fixed here rather than deferred because this work
introduced it: `docs/audits/README.md` still told a reader that
adding a criterion touches four files. Every other document that
said so was updated in phase 4; that one was missed because it
describes the specification directory rather than the tooling.

Two things the audit surfaced that are *not* findings against this
work, recorded so the next reader does not re-derive them.
`scripts/check-audit-smoke.py` fails locally because `skillsaw` is
not installed outside the audit's venv, which is the smoke checker
working as designed -- `llm-context-lint` reporting `not_applicable`
for want of its tool is exactly what it exists to catch. And
`PUSH-AUDIT.md` still writes every diff against `main...HEAD`; this
audit was run against `origin/main...HEAD` because local `main` was
four commits stale, which would have silently widened the diff. That
is the correction already recorded under Future work in
`PLAN-audit-compliance-split.md` and it is still owed.

## Risks and mitigations

* **The daily run files and closes real issues fleet-wide.** A
  regression does not produce a red build; it produces issues in
  other people's repositories, or silently closes ones that should
  be open. Mitigations: the snapshot diff is required to be empty at
  every commit; `audit-manage-issues.py` is run only with
  `--dry-run` by hand; each pull request lands with a morning
  between it and the next, and the first 06:00 UTC run after each
  landing is checked before the next phase starts.
* **Review marks on `scripts/` are discarded.** Accepted
  deliberately in D8. Coverage drops, an issue may be filed, and the
  re-review is worth more against the new structure. Not silenced.
* **`ISSUE_TITLES` drifts and orphans open issues.** The frozen
  snapshot test in phase 4, and the fact that a title change must
  edit a literal dict to pass.
* **Detail strings reworded by accident.** The snapshot compares
  them byte-for-byte, which is the only reason it is worth running
  against real clones rather than fixtures.
* **The migration stalls half-done.** The most likely bad outcome: a
  hybrid scheduler is a working state, so nothing forces the second
  half. Phase 3 is defined as not complete until `check_calls()` is
  empty, and ships as one pull request, so a stall is an unlanded
  branch rather than a landed half-measure.
* **The changed reviewer guidance.** `PUSH-AUDIT.md` currently
  instructs reviewers to expect resemblance between check functions.
  Phase 4 updates it; until then a reviewer working from the old
  wording will argue with the refactor, and phase 3's pull request
  description says so.

## Agent guidance

### Execution model

The standard model from `PLAN-TEMPLATE.md`: implementation by
sub-agents, review in the management session, read the files rather
than trusting the summary.

Two adjustments for this plan. Phase 3's commits are mechanical in
shape and subtle in consequence, so the management session's review
is not "does it look right" but "does the snapshot diff empty" --
run it, do not accept a sub-agent's report of it. And phases 2, 4
and 6 stay in the management session's hands for the decisions
inside them, even where a sub-agent does the typing.

### Planning effort

The master plan is high effort. Per phase:

* **High**: phases 2 and 3. Phase 2 is the design; phase 3 carries
  the fleet-wide risk across 45 checks.
* **Medium**: phases 1, 4 and 5. Mechanical, or following the
  pattern phase 2 establishes.
* Phase 6 takes its effort from `PUSH-AUDIT.md`.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Write `tools/audit-snapshot.sh`: run `scripts/audit-check.py` over each clone in a directory, write one JSON per repo with `timestamp` stripped, and a `--diff` mode reporting per-check differences with the six network-dependent checks listed separately, plus a test re-deriving that list from the checker |
| 1b | low | haiku | none | Add the before-and-after procedure to `docs/consistency-audits.md` under "Testing a change" |
| 2a | high | opus | worktree | Create `scripts/audit/` with `check.py`, `repo.py`, `github.py`, `registry.py` per D1 and D6. No checks move. `run_all_checks()` keeps its name and signature and runs the empty registry then `check_calls()`; snapshot must be identical |
| 2b | high | opus | worktree | Add `FakeGitHub` and `RecordingGitHubClient`, and route all 21 `gh` calls through `GhCli` without moving the checks that make them |
| 3a-3h | high | opus | worktree | One per family in the order listed under phase 3. Move the checks to classes, their constants to `text/`, their tests to `scripts/tests/` on the shared base, and empty their `check_calls()` entries. Detail strings copied verbatim. `pre-commit run --all-files` and an empty snapshot diff before handing back |
| 4a | medium | sonnet | none | Derive `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES` from the registry; add the frozen-snapshot test pinning the first two to today's literal values |
| 4b | medium | sonnet | none | Update `docs/consistency-audits.md`, `AGENTS.md`, `ARCHITECTURE.md` and `PUSH-AUDIT.md` per phase 4 |
| 5a | medium | sonnet | none | Tests for the 18 checks with none: pass, fail, not-applicable each |
| 5b | medium | opus | none | Failure-path tests for the five checks that call the GitHub API, and the registry contract tests from D7 |
| 6a | high | opus | none | Run `PUSH-AUDIT.md` over the accumulated diff of phases 1-5 against `main` |

A brief that says "move the plan checks" is not enough. Name the
check ids, the destination module, the constants that move with
them, and the test classes that follow -- the ids are listed per
family under phase 3 precisely so a brief can quote them.

### Model choice

Opus for anything that moves a check or designs a seam; the risk is
a silent behaviour change, and that is exactly what a lighter model
misses while producing plausible-looking code. Sonnet for the
documentation and the mechanical test-writing in phases 4 and 5.
Haiku only for 1b.

The project-specific checks for this plan are:

- [ ] `pre-commit run --all-files` passes.
- [ ] `tools/audit-snapshot.sh --diff` between the phase 1 baseline
      and the current tree reports no differences.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` reports what it reported before, allowing for the
      known `review-coverage` movement from D8.
- [ ] `audit-manage-issues.py` has been run with `--dry-run` only.

### Management session review checklist

The standard checklist from `PLAN-TEMPLATE.md`, plus:

- [ ] The snapshot diff was run by the management session, not
      reported by the sub-agent.
- [ ] No `details` string changed. Where one did, it is its own
      commit and the message says what moves on the compliance page.
- [ ] Every check that had an `applies()`-equivalent early return
      still has one; a lost `not_applicable` guard reads as a new
      failure across the fleet.
- [ ] `check_calls()` is smaller than it was, and empty by the end
      of phase 3.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `scripts/audit/` holds the package described in D2, and
  `scripts/audit-check.py` is a shim under 100 lines with unchanged
  arguments and JSON.
* `check_calls()` no longer exists; `registry.CHECKS` schedules all
  45 checks.
* `AUDIT_METADATA` and `ISSUE_TITLES` are derived and pinned by a
  frozen-snapshot test.
* Every check in `CHECKS` has a test module entry, asserted by a
  contract test; the 18 previously untested checks have pass, fail
  and not-applicable cases; the five checks that call the GitHub API
  have their failure paths.
* `tools/audit-snapshot.sh --diff` over the fleet clones reports no
  differences between the phase 1 baseline and the final tree.
* `pre-commit run --all-files` passes.
* One 06:00 UTC run has completed after the final landing, and
  `docs/audits/compliance.md` is unchanged by it apart from its
  timestamp.
* `docs/consistency-audits.md`, `AGENTS.md`, `ARCHITECTURE.md` and
  `PUSH-AUDIT.md` describe the structure that exists.
* No script has acquired a dependency outside the standard library.
* Phase 6's findings are resolved or declined in writing.

### Documentation index maintenance

This plan carries a row in `docs/plans/index.md` -- date, link,
one-line intent, status. The index row is the whole-plan status and
only reaches `Complete` once every phase in the Execution table has
been completed, abandoned or superseded. Update it as the plan
progresses, not only at the end.

### Future work

* **`review-tracking.py`.** 636 lines, a clean `cmd_*` dispatch, and
  it would read better with a `Scope` and a `ReviewState` object
  behind the commands. It is not hurting anyone today and it shares
  nothing with the audit checker but a directory, so it is a
  separate change. Its tests are also the ones most entangled with
  real git fixtures, and the `FixtureRepo` helper from D7 is what it
  would want first.
* **`check_review_coverage` can put a subprocess traceback into a
  detail string.** Noted already under Future work in
  `PLAN-audit-compliance-split.md`. Once `details` construction goes
  through `Result`, capping and sanitising it is a change in one
  place, which is the argument for doing it after this plan rather
  than during.
* **Adopt `Repo.read()` in the checks.** The content cache exists
  and is tested, but production does not reach it: the check modules
  make 35 direct `open()` calls and none go through `read()`, so the
  only caller is `workflow()`, which only the seam tests use. The
  directory-listing cache argued for in D2 was realised; the read
  cache was not. Converting the 21 repository-relative sites is
  mechanical -- `exists()` then `open()` collapses into one `read()`
  returning `None` -- but it is 21 behaviour-preserving edits across
  eight modules, which wants its own snapshot run rather than being
  bolted onto this one. Raised in review of PR #79.
* **Parallelising the checks.** Uniform `Check` objects with a
  declared GitHub dependency make it possible to run the filesystem
  checks concurrently. The daily run is not slow enough to justify
  it, and concurrency would make the snapshot diff harder to trust.
  Recorded because it becomes cheap, not because it is wanted.
* **A check with no spec page, or a spec page with no check.** The
  contract tests in phase 5 catch the first; `unmeasured_specs()`
  already does the second. Worth folding together once both live in
  the same place.
* **`PLAN-TEMPLATE.md` arrived with this plan.** It was written
  because this repository holds the fleet to the `plan-template`
  criterion and had no template of its own, so `development` was
  `N/A` on a standard it enforces. Nothing else here depends on it.

* **Shared blocks propagate to this repository first and lag
  everywhere else.** Both places a block is required show the same
  pattern, from `docs/audits/compliance.md` rather than from local
  clones, which are stale enough to give the opposite answer:

  * `push-audit`: all eight repositories that carry a
    `PUSH-AUDIT.md` are non-compliant, and every one of them is
    missing `diagram-discipline` -- added here on 2026-08-29 and
    nowhere else yet. Five also lack `path-traversal-review`,
    `python-version-discipline` and `functional-test-coverage`;
    occystrap and sfui additionally do not reference
    `PUSH-AUDIT.md` from `AGENTS.md`, so their audit is one nothing
    points at.
  * `plan-template`: three of the seven repositories with a
    template are non-compliant -- divergulent, occystrap and
    shakenfist -- all missing `plan-push-audit-phase`.

  Every one of these already has an open tracking issue, so the
  criteria are working as designed and this is a backlog rather
  than a discovery. It is worth recording here only because adding
  a required block is a fleet-wide edit that lands as one commit
  in this repository and eight pull requests elsewhere, and the
  lag between the two is invisible from inside this repository.
  Whether the block-refresh should be templated or scripted is a
  separate question from this plan.

### Bugs fixed during this work

To be filled in as the work proceeds. Anything still open from the
in-flight review of `scripts/` on `origin/reviews` when phase 3
starts is carried here, per D8.

### Back brief

Before phase 1 begins, the executing session back briefs Mikal on
its understanding of this plan. Three things are worth confirming
before any code moves, because they are cheap now and expensive
later: the module grouping in D2, the decision in D3 to derive
`AUDIT_METADATA` and `ISSUE_TITLES` rather than keep them
hand-written, and the sequencing against the human review in D8.
