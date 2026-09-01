# Consistency audits

Every Shaken Fist project is expected to be packaged, tested and
automated the same way. The consistency audit is what makes that an
observable property rather than an intention: it measures each
repository against a set of criteria every morning, files an issue on
the repository for each criterion it fails, closes the issue when the
criterion passes again, and publishes the result as a table in this
repository.

This page is about the audit machinery itself -- how a run works, how
to add a criterion, how to bring a repository into scope, and how to
test a change before it reaches the fleet. What we audit *for*, and
why each rule exists, is in
[`docs/audits/`](/components/development/audits/README/), one page per criterion.

## The two layers

A criterion exists in two places, and both have to agree.

| Layer | Lives in | Audience |
|-------|----------|----------|
| Specification | `docs/audits/<check-id>.md` | Humans and agents. Why the rule exists, what is checked, what it does not cover, which template implements it, and a link to its section of the compliance page. Hand-written throughout. |
| Check | `scripts/audit-check.py` | The runner. A function returning `pass`, `fail` or `not_applicable` with a reason. |

The split is deliberate. The specification is where a rule explains
itself, and it is what an agent or a person reads when they pick up an
issue, so it links the template that implements the rule. The check is
what actually measures, and it is allowed to be narrower than the
specification: some criteria have no check at all, because judging
them takes reading rather than matching. The current set is listed
under "Criteria with no automated check" at the foot of
[`docs/audits/compliance.md`](/components/development/audits/compliance/), generated from
`AUDIT_METADATA` rather than written by hand -- at the time of
writing, `test-coverage`.

That listing replaced an older tell. Every specification used to
carry its own generated table, so a criterion with no check was the
one with no `consistency-audit` marker block in its file. Moving the
tables onto one page took that away, and the page states the set
instead, which a reader can see rather than having to grep for.

Not every criterion maps to exactly one check. `workflow-standards`
decomposes into several -- runner tags, permissions, linting, and more
-- which all render into its one section of the compliance page as
separate columns.

## What a daily run does

`.github/workflows/consistency-audit.yml` runs at 06:00 UTC, after
`export-repo-config` at 00:30, and can be started by hand with
`workflow_dispatch`. It has four jobs.

**1. `audit`** -- a matrix job per repository. Each leg shallow-clones
the target with `gh repo clone`, installs a pinned `skillsaw` into a
virtualenv, asserts that skillsaw answers at that pinned version, then
runs `scripts/audit-check.py` and uploads the result as an
`audit-result-<repo>.json` artifact.

Most checks read files out of the clone. A few (default branch,
security settings, repository visibility) query the GitHub API through
`gh`, and the git-hygiene checks shell out to `git` inside the clone.
Visibility is queried live rather than hardcoded because it changes.

The skillsaw pin is deliberate, and so is the assertion next to it.
`llm-context-lint` reports what skillsaw calls an error, and skillsaw's
rule set moves between releases, so an unpinned upgrade would change the
compliance page for reasons nobody chose.

**2. `manage-issues`** -- downloads every artifact and runs
`scripts/audit-manage-issues.py`, which files and closes issues. See
[Issues are the work tracking](#issues-are-the-work-tracking) below.

**3. `update-docs`** -- runs `scripts/audit-update-docs.py`, which
rewrites everything between the `<!-- consistency-audit:begin -->` and
`<!-- consistency-audit:end -->` markers in
[`docs/audits/compliance.md`](/components/development/audits/compliance/) from the same
results -- one table per criterion, linking the issues the previous job
just filed. `scripts/commit-audit-docs.sh` then commits and pushes
that one file to `main` as `shakenfist-bot`, rebasing first in case
another push landed while the audit ran.

It writes that page and nothing else. The criterion specifications are
hand-written, and are in scope for whole-file human review because of
it; a generated block in one would carry a timestamp that changes
daily and no review mark could survive it.

The tables are therefore always a rendering of the most recent run.
Never edit one by hand: the next run overwrites it.

**4. `report-failure`** -- runs only when one of the above fails, and
files or updates an issue labelled `audit-failure` on this repository.

That last job exists because this pipeline's worst failure mode is a
quiet one. A scheduled workflow that fails emails whoever pushed last,
which is nobody's inbox in particular at 06:00 UTC -- and while the
audit is down the tables keep displaying the previous morning's
verdicts, so the audit looks healthy from the outside. In August 2026
that ran for a full day.

## Issues are the work tracking

`audit-manage-issues.py` files one issue per failing check, on the
repository the work has to happen in, labelled `consistency` (the label
is created if missing). The body names the failing check, quotes the
detail the check produced, and links the spec file and template so
whoever picks it up has the implementation to hand.

Aggregate across the fleet with:

```
gh search issues 'org:shakenfist label:consistency is:open'
```

Two properties matter when changing any of this.

**Issue titles are the idempotency key.** They are
`Consistency: <check name>`, matched exactly to decide whether an issue
already exists. Renaming a check name silently orphans every open issue
for it and files a fresh set, so treat `ISSUE_TITLES` in
`scripts/audit_common.py` as an interface, not a label.

**Repository renames are handled, but noisily on purpose.** Repo names
are resolved to their canonical form before searching, because GitHub's
issue search does not follow renames while issue creation does -- so a
stale matrix entry would otherwise file a duplicate every single
morning. A rename still fails the job, so the matrix actually gets
updated. If duplicates exist anyway, the oldest is kept and the rest are
closed.

A check that starts passing closes its issue. A check that becomes
`not_applicable` closes it too: "we decided this does not apply" and
"this now complies" are both reasons not to keep a work item open.

## Adding a criterion

Two files: the check and its specification.

1. **`scripts/audit/checks/<family>.py`** -- add a `Check` subclass to
   the module whose specifications it belongs with. It declares what it
   is as class attributes -- `id`, `spec`, `issue_title`, an optional
   `template`, and a `column` heading when it shares a specification
   page with another criterion -- and implements `run(repo)`, returning
   `self.ok()`, `self.fail()` or `self.skip()`. Register the instance in
   `CHECKS` in `scripts/audit/registry.py`, and add its id to
   `ORDER` where you want it reported.

   Put the applicability test in `applies(repo)` rather than at the top
   of `run()`. The scheduler asks the cheap question first, which is
   what lets a repository scoped by `only_checks` skip a criterion
   without paying for it -- several of them query the GitHub API, and
   on a private repository those calls fail for reasons that say
   nothing about compliance.

2. **`docs/audits/<check-id>.md`** -- the specification, following the
   structure in `docs/audits/README.md`, and a line for it in that
   index. Under `## Projects`, link `compliance.md#<check-id>`; the
   section appears there at the first run. Do not add a
   `consistency-audit` marker block -- the tables live on the
   compliance page so that every specification stays reviewable, and
   `test_audit_update_docs.py` fails on a marker in a spec.

`AUDIT_METADATA` and `ISSUE_TITLES` in `scripts/audit_common.py`, and
`COLUMN_NAMES` in `scripts/audit-update-docs.py`, are views over the
registry rather than tables to update. There is no longer a way to
schedule a criterion that is missing from one of them.

They are still an interface, though, which is why
`scripts/tests/test_metadata.py` freezes all three as literals. An
issue title is the idempotency key for filing and closing: renaming one
orphans every open issue for that criterion across the fleet. Adding a
criterion adds a line to the frozen table; changing an existing line is
meant to be difficult.

The column heading is the part with history. Before it was a class
attribute it was a fifth file to remember, and forgetting it broke the
2026-08-12 run: `review-marks-pre-commit` joined the workflow-standards
specification without a heading, and rendering crashed *after*
rewriting every `docs/audits/*.md` but before committing any, so the
whole fleet's tables silently stayed a day stale. Three things now
stand between that and a repeat. `column_name()` prints an ugly
heading and a warning rather than raising, because a run that
publishes a bad label beats a run that publishes nothing;
`test_multi_check_specs_have_a_heading_for_every_check` in
`scripts/test_audit_update_docs.py` fails on the omission; and
`test_criteria_sharing_a_spec_page_all_declare_a_column` in
`scripts/tests/test_metadata.py` fails on it from the other direction,
reading the registry rather than the documentation.

Add tests beside the check, in `scripts/tests/test_<family>.py`. A
criterion with no test module entry fails the contract tests in
`scripts/tests/test_metadata.py`.

A new criterion does not require a re-audit of anything else, and does
not require touching any project repository. The next morning's run
measures it everywhere and files the issues.

## Bringing a repository into scope

Add it to the matrix in `.github/workflows/consistency-audit.yml`,
and in `docs/audits/README.md` add it to the in-scope list and remove
it from the excluded list.

Adding a repository subjects it to every check at once, and every
failure becomes an issue on the next run. Check what that would file
before you commit:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/<repo> \
    --repo-name <repo> --github-org shakenfist
```

Repository properties that cannot be detected from a clone -- docs-only
repositories, repositories where Python is incidental -- are declared in
`REPO_OVERRIDES` in `scripts/audit-check.py`.

A repository that should be audited for some checks but not others
takes an `only_checks` list in the same place. `private-ci` is the
worked example: it is internal tooling and exempt from the conventions,
but it vendors sfui, and a vendored copy drifts silently. Checks outside
the list report `not_applicable` **with a reason** rather than being
omitted -- `audit-update-docs.py` renders a check it cannot find as
`unknown`, and "we decided not to" must not read as "we did not
measure".

A scoped repository does not follow the steps above. It goes in the
matrix, but stays *off* the in-scope list in `docs/audits/README.md`
and *on* the excluded list on the same page: both statements are true
of it, because it is excluded from the conventions and audited for one
thing anyway.
`test_matrix_matches_the_documented_scope` in
`scripts/tests/test_registry.py` subtracts the scoped repositories before
comparing, so onboarding one the way the steps above say will fail that
test.

## Testing a change

The full gate, which `ci.yml` also runs on every pull request:

```
pre-commit run --all-files
```

Every test suite under `scripts/` runs as a `local` pre-commit hook.
Most are triggered by any change under `scripts/`;
`review-tracking-tests` always runs, and `issue-fix-extraction-tests`
runs only for its own file and the template files it covers.
Individually, which is quicker while iterating:

```
python3 -m unittest discover -s scripts/tests -t scripts
python3 scripts/test_audit_seams.py
python3 scripts/test_audit_snapshot.py
python3 scripts/test_audit_update_docs.py
python3 scripts/test_review_tracking.py
python3 scripts/test_check_audit_smoke.py
python3 scripts/test_issue_fix_extraction.py
```

The tests cover the machinery, not what a check *decides* about a real
repository. Test that half against local clones:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/<repo> \
    --repo-name <repo> > /tmp/results/audit-result-<repo>.json
python3 scripts/audit-manage-issues.py --results-dir /tmp/results/ --dry-run
python3 scripts/audit-update-docs.py --results-dir /tmp/results/ \
    --no-issues --page /tmp/compliance.md
```

Always pass `--dry-run` to `audit-manage-issues.py`. Without it the
script creates and closes real issues on real repositories.

Pass `--page` as well, as above. A locally generated page only covers
the repositories you fed it, so it is never what you want to keep, and
without `--page` the script rewrites the real
`docs/audits/compliance.md` in place. The old advice here was to
discard the result with `git restore docs/audits/`, which is now
actively dangerous: the other 35 files in that directory are
hand-written, and a `git restore` of the directory throws away
whatever you were editing along with the generated page.

### Before and after a change to a check

The tests and a one-repository run both answer "does this do what I
meant". Neither answers "does everything else still say exactly what
it said yesterday", and that is the question that matters when a
change touches shared code: a `details` string is published to
`docs/audits/compliance.md` and into the body of every issue filed
fleet-wide, so a rewording is a fleet-wide diff that no test fails on.

Capture a baseline before the change, and compare after it:

```
tools/audit-snapshot.sh ~/src/shakenfist /tmp/snap/before
# ... make the change ...
tools/audit-snapshot.sh ~/src/shakenfist /tmp/snap/after
tools/audit-snapshot.sh --diff /tmp/snap/before /tmp/snap/after
```

The capture takes about forty seconds for a dozen clones. It audits
every checkout in the directory, skipping git worktrees, and strips
the `timestamp` field so that two runs of an unchanged tree compare
equal. `--diff` exits non-zero if anything differs and prints the
status and details on both sides.

Six checks reach the network -- `default-branch-naming`,
`github-security`, `delete-branch-on-merge`, `merge-queue-config`,
`merge-group-cancellation` and `sfui-vendor` -- so they can differ
between two runs because a repository setting changed rather than
because the code did. They are reported under their own heading and do
not affect the exit code. `scripts/test_audit_snapshot.py` re-derives
that list from `audit-check.py` and fails if a check grows a `gh` call
without joining it.

The snapshots are not committed. Generated JSON under `scripts/` or
`docs/` would land in review scope and sit permanently stale in the
review queue; a scratch directory and a repeatable command give the
same guarantee without that.

`ci.yml` also runs the audit against this repository as a smoke test,
via `scripts/check-audit-smoke.py`. Linting cannot reach the scheduled
workflow's runtime assumptions -- the 2026-08-20 outage was a bare
`pip install` meeting a runner that enforces PEP 668 -- so the smoke job
asserts the audit *measured* rather than that it approved. Which checks
fail against this repository is not its business; several fail here by
design.

## Related

- [`docs/audits/README.md`](/components/development/audits/README/) -- what we audit for: the
  criterion index, and which repositories are in scope, excluded, or
  scoped to part of the audit.
- [`ci-review-automation.md`](/components/development/ci-review-automation/) and
  [`automated-pr-review.md`](/components/development/automated-pr-review/) -- the review
  automation several criteria check for.
- [`code-review-tracking.md`](/components/development/code-review-tracking/) -- human review
  tracking, which the `review-coverage` criterion measures.
- The `standards-alignment` skill in `.claude/skills/` -- bringing a
  repository up to these standards, one change per commit.
