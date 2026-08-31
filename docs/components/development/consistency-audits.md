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

Four files, plus a fifth when the check shares a spec file with
another, and they have to stay in sync. The invariants that span them
are the ones that break, so they are the ones under test:
`scripts/test_audit_check.py` holds the `check_calls()` scheduling
test, and `scripts/test_audit_update_docs.py` holds the `COLUMN_NAMES`
ones.

1. **`scripts/audit-check.py`** -- add a `check_*()` function returning
   a dict with `id`, `status` (`pass` / `fail` / `not_applicable`) and
   `details`. Register it in `check_calls()`. The id written in
   `check_calls()` must be the id the function returns, and a test
   asserts it: the calls are deferred so that a scoped repository can
   skip a check without running it, which means the table is what
   schedules the check, not the function.
2. **`scripts/audit_common.py`** -- add the id to `AUDIT_METADATA`
   (spec file, optional template) and `ISSUE_TITLES`. Both
   `audit-manage-issues.py` and `audit-update-docs.py` read this
   module.
3. **`docs/audits/<check-id>.md`** -- the specification, following the
   structure in `docs/audits/README.md`. Under `## Projects`, link
   `compliance.md#<check-id>`; the section appears there at the first
   run. Do not add a `consistency-audit` marker block -- the tables
   live on the compliance page so that every specification stays
   reviewable, and `test_audit_update_docs.py` fails on a marker in a
   spec.
4. **`scripts/audit-update-docs.py`** -- only if the check joins an
   existing spec file rather than getting its own. Add a column heading
   for the id to `COLUMN_NAMES`.
5. **`docs/audits/README.md`** -- add the file to the index.

Step 4 is the one that bites. Its absence broke the 2026-08-12 run:
`review-marks-pre-commit` joined the workflow-standards spec without a
heading, and rendering crashed *after* rewriting every `docs/audits/*.md`
but before committing any -- so the whole fleet's tables silently stayed
a day stale. Both halves of that are now fixed. `column_name()` prints
an ugly heading and a warning rather than raising, because a run that
publishes a bad label beats a run that publishes nothing; and
`test_multi_check_specs_have_a_heading_for_every_check` in
`scripts/test_audit_update_docs.py` fails on the omission, so the
fallback should never be reached from a tested tree.

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
`scripts/test_audit_check.py` subtracts the scoped repositories before
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
python3 scripts/test_audit_check.py
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
