# Consistency audits

Every Shaken Fist project is expected to be packaged, tested and
automated the same way. The consistency audit is what makes that an
observable property rather than an intention: it measures each
repository against a set of criteria once a week, files an issue on
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
| Check | `scripts/audit/checks/<family>.py` | The runner. A `Check` subclass returning `self.ok()`, `self.fail()` or `self.skip()` with a reason, or raising when it cannot measure (see [What each status means](#what-each-status-means)), registered in `CHECKS` in `scripts/audit/registry.py`. `scripts/audit-check.py` is the entry point that runs the schedule and prints the results. |

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

## What a run does

`.github/workflows/consistency-audit.yml` runs weekly at 18:00 UTC on
Sunday -- early on Monday morning in Canberra -- and can be started by
hand with `workflow_dispatch`. `export-repo-config`, whose output some
checks read, still runs daily at 00:30 UTC.

The audit used to run daily. It filed and closed issues faster than the
fleet could absorb them: in September 2026 consistency fixes were the
largest single category of PR in kerbside and ryll, and crowded out the
work those repositories exist for. Weekly turns that into a small
backlog to start the working week on.

One consequence is that an issue fixed mid-week stays open until the
next run unless the fixing PR closes it with `Fixes #N` in the PR body,
which the `standards-alignment` skill in `.claude/skills/` asks for. A
closed issue is not a passing criterion, though: the issue manager only
searches open issues, so if the check still fails after the merge -- a
partial fix, or a second finding under the same criterion -- the next
run files a new issue rather than reopening the old one, and the
discussion on it is left behind. After merging such a fix, dispatch the
workflow by hand to confirm the criterion passes, which also brings the
compliance page up to date.

`Fixes #N` only closes an issue when the PR merges into the repository
the issue was filed in. A fix that lands in this repository instead --
an override, a corrected check, a new matrix entry -- closes nothing
elsewhere, so dispatch the workflow after merging it and let the run
close the issues the fix resolved.

The audit workflow has four jobs.

**1. `audit`** -- a matrix job per repository. Each leg shallow-clones
the target with `gh repo clone`, installs a pinned `skillsaw` into a
virtualenv, asserts that skillsaw answers at that pinned version, then
runs `scripts/audit-check.py` and uploads its result,
`audit-result-<repo>.json`, as an artifact named `audit-<repo>`. The
two later jobs download every artifact matching `audit-*`, so that
prefix is what ties a leg's result to them.

Most checks read files out of the clone, several of them by asking
`git` inside it what the index holds. A few reach the network instead:
they query the GitHub API through `gh`, or clone another repository to
compare against. That set is `NETWORK_CHECKS` in
`scripts/audit_snapshot.py`, which a test keeps in step with the code
(see [Before and after a change to a
check](#before-and-after-a-change-to-a-check)). Repository visibility
is among what they query, live rather than hardcoded, because it
changes.

The skillsaw pin is deliberate, and so is the assertion next to it.
`llm-context-lint` reports what skillsaw calls an error, and skillsaw's
rule set moves between releases, so an unpinned upgrade would change the
compliance page for reasons nobody chose. skillsaw's venv is its own
and stays off `PATH`: the audit step names the binary in
`AUDIT_SKILLSAW`, so its unpinned dependencies never load into the
interpreter running the checks (see
[llm-context-lint](/components/development/audits/llm-context-lint/)).

The workflow holds two tokens, and the legs get the weaker one. The
checks read files the audited repositories control, and some run
programs over them, so the legs hold `AUDIT_READ_TOKEN`, a
fine-grained personal access token that can read every repository in
the organisation and write to none. `AUDIT_TOKEN`, which files and
closes issues across the organisation, appears only in
`manage-issues` and `update-docs`. `AUDIT_READ_TOKEN` is minted with
the `shakenfist` organisation as its resource owner, access to all
repositories, no organisation permissions, and these repository
permissions, each read-only:

* **Metadata** -- the repository itself (default branch, visibility),
  its branch rules, and the organisation's repository listing.
* **Contents** -- cloning the private repositories.
* **Actions** -- the workflows and their runs, which
  `scheduled-workflow-health` reads.
* **Administration** -- `security_and_analysis`, which GitHub leaves
  out of the REST repository answer for a caller without admin access,
  and `deleteBranchOnMerge`. The REST answer leaves that one, and every
  other merge setting, out even for this token, so
  `delete-branch-on-merge` reads it through GraphQL, which answers it.

The token's resource owner must be the `shakenfist` organisation, not
the account that creates it. Created under the account, "All
repositories" means that account's own repositories: the token still
reads the organisation's public ones, as any token can, but its
Administration permission does not reach them, and the probe below
fails with "cannot see repository settings". The organisation also
refuses a fine-grained token whose lifetime is over 366 days.

The minting account's own role matters as much. GitHub returns
`security_and_analysis` only to a caller with admin access to the
repository, and a fine-grained token never exceeds its account's
access. The token is `shakenfist-bot`'s, and the bot stopped being
an organisation owner on 2026-10-30; it reads the setting now
because the bot is an organisation **security manager**, through
the `sf-security-managers` team. That role reads every repository,
writes every security alert (code scanning, Dependabot and secret
scanning, including reading leaked values and dismissing alerts)
and can change code security settings such as secret scanning and
push protection at organisation and repository level; it grants no
ruleset, environment or branch right. It is wider than the audit
needs, and it belongs to the account, so every bot token whose
scopes allow it carries that reach too. A token re-minted under an
account without the role, or the team dropped from the security
managers, fails every leg at "Check the read token". The way out
is a read-only App identity for the legs, #352.

These are enough for `release-environment-protection` too: the token
was confirmed on 2026-10-19 to read both
`environments/<name>` and its `deployment-branch-policies` without a
separate Environments permission.

Each leg checks first, with `tools/check-audit-read-token.sh`, that
the read token is set -- an unset secret is an empty string, and `gh`
with an empty `GH_TOKEN` falls back to whatever login the runner's
user holds -- and that it can see those last two settings, each the
way its check reads it. Without them `delete-branch-on-merge` would
report `error` for every repository, and `github-security` would
stop checking secret scanning without saying so, so a token that
cannot see them fails every leg instead.

`AUDIT_READ_TOKEN` is a repository secret on this repository, and a
fine-grained token always has an expiry date. When it lapses, every leg
fails at "Check the read token" and `report-failure` files its issue
pointing at the run. Mint a replacement with the permissions above and
replace the secret, then dispatch a run of `main` to confirm it.

`AUDIT_TOKEN` is also a fine-grained personal access token, with the
`shakenfist` organisation as its resource owner, access to all
repositories and no organisation permissions. It needs two repository
permissions beyond the Metadata every token gets: **Issues**, read
and write, which covers listing, filing, editing, commenting on and
closing issues, and creating the `consistency` label; and
**Contents**, read only, because `gh issue create` reads the
repository's default branch before it creates, and on a private
repository that read needs Contents. Issues alone was enough while
the bot was an organisation owner; once it became a member on
2026-10-30 the token could no longer file in private-ci (#349), and
Contents read was added to it on 2026-10-31; audit run 41140043392
then filed there again. `update-docs` uses it only to look up the
open issues the page links to; the page itself is pushed by
`scripts/commit-audit-docs.sh` with the job's own `GITHUB_TOKEN`,
which `actions/checkout` leaves in the clone and the job's
`contents: write` permission lets push to this repository alone. The
same lifetime limit applies. When it lapses, `manage-issues` fails
and `report-failure` files its issue, and `update-docs` still pushes
the page but renders each issue link it could not look up as `?`.

`AUDIT_TOKEN` is not a repository secret. It is an environment secret
in the `audit-write` environment, whose deployment branch policy
allows only `main`, and `manage-issues` and `update-docs` are the only
jobs that name that environment (`test_audit_workflow.py` pins both).
A repository secret goes to every run that names it, including a
dispatch of any branch; an environment secret goes only to a job the
environment admits. So a branch that edits the `main`-only condition
out of its copy of a writing job fails at the deployment check, and
one that names `AUDIT_TOKEN` in an audit leg gets an empty string. The
environment is set up once, by a repository admin:

1. Settings, Environments, New environment, named `audit-write`.
2. Deployment branches and tags: Selected branches and tags, with the
   single rule `main` (a branch rule, not a tag rule).
3. Add the token as the environment secret `AUDIT_TOKEN`. GitHub
   cannot copy a secret from one scope to another, so this means
   pasting the token again, or minting a replacement.
4. Delete the repository secret `AUDIT_TOKEN`, so that nothing outside
   the environment can name it.

Do the first three before merging a change that adds the
`environment:` lines, and the fourth after: in between, both copies
exist and the workflow works whichever one it reads. Dispatch a run of
`main` to confirm, and check that a dispatch of a branch still shows
the writing jobs as skipped. Leave out required reviewers and wait
timers: the scheduled run has nobody to approve it.

The split between jobs is also a split between hosts. The audit legs
run on the persistent `[self-hosted, static]` runners, as one user,
and only `runner.temp` is emptied between jobs. Code an audit leg runs
-- skillsaw's unpinned dependencies, or anything an audited repository
can steer -- could leave something in that user's home directory (a
`.pth` file in the user site-packages, a `~/.gitconfig` hook) for a
later job there to run. So `manage-issues` and `update-docs` run on
ephemeral `[self-hosted, vm, debian-13, s]` runners instead: each
starts from the image and is thrown away after one job, so nothing a
leg wrote reaches a job holding `AUDIT_TOKEN`. The image has no `gh`,
so both jobs install it from Debian first.
`scripts/tests/test_audit_workflow.py` fails if either job moves back
to `static`.

Within a leg, the isolation is weaker. skillsaw runs as the same user
as `audit-check.py`, so taking the token out of its environment is
defence in depth, not isolation: it could still read its parent's.
That token is `AUDIT_READ_TOKEN`, though, and the split keeps
`AUDIT_TOKEN` out of every process the legs start.

**2. `manage-issues`** -- downloads every artifact and runs
`scripts/audit-manage-issues.py`, which files, updates and closes issues. See
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
every run and no review mark could survive it.

The tables are therefore always a rendering of the most recent run.
Never edit one by hand: the next run overwrites it.

**4. `report-failure`** -- runs only when one of the above fails, and
files or updates an issue labelled `audit-failure` on this repository,
with `tools/report-audit-failure.sh`.

That last job exists because this pipeline's worst failure mode is a
quiet one. A scheduled workflow that fails emails whoever pushed last,
which is nobody's inbox in particular before anyone is up -- and while
the audit is down the tables keep displaying the previous run's
verdicts, so the audit looks healthy from the outside. In August 2026
that ran for a full day; on a weekly cadence the same failure would
last a week. Nothing retries a failed run, so the issue says how to
start one by hand once the cause is fixed.

Only a run of `main` writes anything. Dispatched on another branch
(`gh workflow run consistency-audit.yml --ref <branch>`), the audit
matrix runs that branch's checks against the whole fleet and uploads
its results as artifacts, and `manage-issues`, `update-docs` and
`report-failure` are skipped. That is the way to measure a change to
the checks against the fleet before merging it. The `main`-only
condition catches a dispatch of the wrong ref; the `audit-write`
environment above is what keeps `AUDIT_TOKEN` from a branch that edits
the condition out. The read token is another matter: a branch run
needs it to measure anything, and a branch controls the code that runs
with it, so a branch can read every private repository in the
organisation. For reading, the trust boundary is push access to this
repository, as it always was. Two runs of one ref never overlap: the
second waits for the first to finish. Runs of different refs do not
wait for each other, so a branch run can never hold up or displace the
scheduled run of `main`.

## Issues are the work tracking

`audit-manage-issues.py` files one issue per failing check, on the
repository the work has to happen in, labelled `consistency` (the label
is created if missing). The body names the failing check, quotes the
detail the check produced, and links the spec file and template so
whoever picks it up has the implementation to hand. The detail and
every listed item are quoted as code spans: they are text out of the
audited repository, and as prose an `@org/team` in them would notify,
and their markdown would render, under the bot's identity.

A check whose fix is per item can carry the items as well. Returning a
`missing` list renders them as bullets under **Missing items**, and a
`findings` list under **Findings** -- the same mechanism, worded for a
check that reports things present which should not be rather than
things absent. `details` still has to stand alone, because that is what
the compliance page prints into a table cell, so it carries the count
and the guidance while the list carries the locations. The worked
examples are `review-scope-completeness` and `eol-distro`. Those two
keys are the only ones the body renders, so a list under any other
name reaches the result JSON and nobody else;
`test_every_extra_result_key_is_one_the_issue_renders` in
`scripts/tests/test_metadata.py` refuses one.

Aggregate across the fleet with:

```
gh search issues 'org:shakenfist label:consistency is:open'
```

Two properties matter when changing any of this.

**Issue titles are the idempotency key.** They are
`Consistency: <issue_title>`, from the check's `issue_title` class
attribute, built by `issue_title_for()` in `scripts/audit_common.py`
and matched exactly to decide whether an issue already exists --
exactly, because several titles are prefixes of others, and "Workflow
standards" must not close a "Workflow standards (flake8wrap)" issue.
Renaming one silently orphans every open issue for it and files a
fresh set, so treat a check's `issue_title` as an interface, not a
label; retiring the old title, described under adding a criterion
below, is what closes them. `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py` holds every one as a literal, so a
rename fails the tests until it is made there too, on purpose.
(`ISSUE_TITLES` in `scripts/audit_common.py` is a view over the
registry, not the place to change one.)

**A failed listing is not an empty one.** Each repository's open
`consistency` issues are listed once per run and matched by title in
Python. If the listing fails, that repository's issues are not managed
at all -- read as "no issues", every failing check would file a
duplicate -- and `audit-manage-issues.py` carries on with the other
repositories and exits non-zero at the end. On the compliance page the
same failure renders the repository's Issue cells as `?` rather than
`-`, which would claim that no issue is open.

**A refused write fails the run.** A create, edit or close that GitHub
refuses, or that times out, is counted, and once every repository has
been managed the script exits non-zero and names them. An issue is
closed before its closing comment is posted, so a close that fails
leaves nothing behind for the next run to post again. A failed
`manage-issues` does not hold back the compliance page: `update-docs`
runs once it has finished, whatever its outcome, as long as every
`audit` leg passed, because a refused write does not make a verdict
wrong.

**Repository renames are handled, but noisily on purpose.** Repo names
are resolved to their canonical form before searching, because GitHub's
issue search does not follow renames while issue creation does -- so a
stale matrix entry would otherwise file a duplicate on every single
run. A rename still fails the job, so the matrix actually gets
updated. If duplicates exist anyway, the oldest is kept and the rest are
closed.

**An open issue's body is regenerated on every run.** While a check
keeps failing, its issue's body is re-rendered from the latest result
and rewritten with `gh issue edit` whenever it differs, so the counts,
the per-item lists and the spec links stay current rather than
remaining the snapshot taken at filing. `body_is_current()` in
`scripts/audit-manage-issues.py` ignores CRLF line endings and
whitespace at the start or end of the whole body, which GitHub
introduces without the content changing, so an unchanged result edits
nothing. Any other difference, including trailing whitespace on a line
inside the body, is an edit.
Hand edits to a body are not preserved -- anything worth saying to the
next reader belongs in a comment.

### What each status means

Every result carries one of four statuses. The first three are
verdicts about the audited repository; the fourth is a statement about
the audit.

| Status | Means | Effect on the criterion's issue |
|--------|-------|---------------------------------|
| `pass` | The repository meets the criterion. | Closes an open one. |
| `fail` | The repository does not, and `details` says how. | Files one, or refreshes the open one's body. |
| `not_applicable` | The criterion does not apply here, for the reason in `details`. | Closes an open one. |
| `error` | The audit could not reach a verdict. | None. |

A check that starts passing closes its issue. A check that becomes
`not_applicable` closes it too: "we decided this does not apply" and
"this now complies" are both reasons not to keep a work item open.

`error` means the audit failed rather than the repository, and it is
not a verdict: it touches no issue either way. Filing one would blame
the audited repository for a bug in the audit, and closing one would
read as the repository having fixed it. A check never returns it.
`registry.run_check()` reports it for a check that raised -- one
raising criterion costs that criterion rather than all of them for
the repository -- and prints the traceback to the audit step's log.
The leg then fails on its *Fail if any check raised* step
(`scripts/audit-raised.py`), after the results are uploaded, so
`manage-issues` still manages every other criterion for that
repository while `report-failure` makes the bug visible and
`update-docs` holds back the compliance page until it is fixed.

**When a check's own tooling fails, the status is `error`.** That
covers the GitHub API returning an error, `git ls-files` failing
(`audit.files.tracked_paths()` returning `None`), a clone failing,
and a subprocess timing out. The check has learned nothing about the
repository, so it has no verdict to give: it raises, and
`run_check()` reports `error`. It does not return `self.fail()`, and
it does not return `self.skip()`. Each of those is a claim about the
repository. A `fail` files an issue on it, under the bot's identity,
for an outage on our side. A `not_applicable` closes the issue it
already has, and does so for every repository the outage reached at
once. The line is whether the tool answered: an answer is evidence a
verdict can rest on, including an answer that the thing asked about
does not exist, while a failure to answer is not.

The exception a check raises for this is `audit.check.ToolFailure`,
whose message becomes the `details` of the `error` result -- so it
says what could not be asked and why, without the traceback a bug
gets, and the result carries `tool_failure: true`. Three helpers raise
it so a check does not have to: `require_tracked_paths()` in
`audit/files.py` for the index, `answer()` and `api_answer()` on the
GitHub client for a call whose every failure is GitHub not answering,
and `run_tool()` in `audit/check.py` for any other subprocess, which
raises for a timeout or a tool that cannot start and leaves the exit
status to the caller, since only the caller knows which statuses are
answers. `check-audit-smoke.py`, which runs in CI without a token,
excuses a `tool_failure` from the checks that ask GitHub and nothing
else.

The scheduled run excuses the same results, and only those
(`github_did_not_answer()` in `scripts/audit_snapshot.py` is the one
rule both scripts use). For a network check whose tooling could not
ask GitHub, *Fail if any check raised* prints a workflow warning and
does not fail the leg. A failed leg holds back the compliance page for
the whole fleet, which one transient 502 or rate limit in one
repository is not worth. The page shows that cell as a check error
saying what could not be asked, and the criterion's issue is left as it
was. An outage that lasts is not hidden by this. A token that has
expired or lost access fails every leg at *Check the read token*,
before anything is measured. A tool failure from any other check is
the runner's own git, skillsaw or `review-tracking.py` not answering,
which the next run would meet too, so it still fails the leg.

The tests call `run()` directly and assert a verdict, so a check that
raises unexpectedly still fails them. A test of a tool-failure path
asserts that `run()` raises ToolFailure instead, with
`CheckTestCase.assert_tool_failure()`.

## Adding a criterion

Six files, all in this repository. This is the one list of them:
`PUSH-AUDIT.md` and `AGENTS.md` link here rather than keep their own,
so a step added to it reaches both.

1. **`scripts/audit/checks/<family>.py`** -- add a `Check` subclass to
   the module whose specifications it belongs with. It declares what it
   is as class attributes -- `id`, `spec`, `issue_title`, an optional
   `template`, and a `column` heading when it shares a specification
   page with another criterion -- and implements `run(repo)`, returning
   `self.ok()`, `self.fail()` or `self.skip()`.

   Put the applicability test in `applies(repo)` rather than at the top
   of `run()`. The scheduler asks the cheap question first, which is
   what lets a repository scoped by `only_checks` skip a criterion
   without paying for it -- several of them query the GitHub API, and
   on a private repository those calls fail for reasons that say
   nothing about compliance.

   Read the audited repository through the helpers rather than around
   them. `audit.files.tracked_paths(path, *pathspec)` is how to ask git
   what the index holds: it lists with `-z`, because `git ls-files`
   otherwise C-quotes any non-ASCII path into a name that opens
   nothing, and it returns `None` for a listing that failed, which is
   not the same answer as an empty index. `repo.read()` is how to open
   a repository file, and the only way: it returns `None` for anything
   that is not a regular file inside the checkout once symlinks are
   followed -- a dangling link, a directory, a link out of the clone,
   a device -- and for anything over `READ_MAX_BYTES` (2 MiB), so none
   of them is raised on, followed or read in part. `repo.isfile()`
   asks the same question without wanting the content,
   `repo.exists()` allows a directory but not a link out of the clone,
   and `repo.workflows()` lists only workflows `repo.workflow()` will
   read. A tree walk hands what it finds to `repo.read()` through
   `repo.relative()` rather than opening it.
   `scripts/tests/test_containment.py` runs every scheduled check with
   the files it read replaced by links out of the checkout, dangling
   links and directories, and fails on any `open()` of the checkout
   that did not come through that one guard. It also parses the
   package for any call that opens a file -- `open()`, `os.open()`,
   `Path.read_text()` and the like -- outside a short allowlist, which
   covers the branches no fixture reaches. A read is decoded as UTF-8
   whatever the runner's locale.

2. **`scripts/audit/registry.py`** -- register the instance in
   `CHECKS`, beside the rest of its family. That list is the order the
   results are reported in, and there is no second table to add the id
   to.

3. **`scripts/tests/test_<family>.py`** -- tests beside the check,
   following the `CheckTestCase` convention in
   [Testing a change](#testing-a-change). A criterion no test module
   instantiates fails
   `test_every_check_is_reachable_from_a_test_module` in
   `scripts/tests/test_metadata.py`.

4. **`docs/audits/<check-id>.md`** -- the specification, following the
   structure in `docs/audits/README.md`. Under `## Projects`, link
   `compliance.md#<check-id>`; the section appears there at the first
   run. Do not add a `consistency-audit` marker block -- the tables
   live on the compliance page so that every specification stays
   reviewable, and `test_audit_update_docs.py` fails on a marker in a
   spec.

5. **`docs/audits/README.md`** -- a line for the specification in the
   criterion index. `test_every_spec_file_is_named_in_the_index` in
   `scripts/tests/test_audit_update_docs.py` fails on a spec the index
   does not name.

6. **`scripts/tests/test_metadata.py`** -- the criterion's lines in
   `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES`, and in
   `FROZEN_COLUMN_NAMES` when it declares a `column`. Why these are
   frozen is below.

A criterion that reaches the network needs one more line: its id in
`NETWORK_CHECKS` in `scripts/audit_snapshot.py`. `NetworkCheckListTest`
fails until it is there, and
[Before and after a change to a check](#before-and-after-a-change-to-a-check)
says what the list is for.

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

Removing a criterion orphans its issues the same way: no later run
measures it, so nothing would ever close them. Retire it instead, in
one commit:

1. Delete the check -- its class, its line in `CHECKS`, its lines in
   the frozen tables, its specification and its row in
   `docs/audits/README.md`.
2. Add its `issue_title`, spelled exactly as it was and without the
   `Consistency: ` prefix, to `RETIRED_ISSUE_TITLES` in
   `scripts/audit_common.py`.

Every later run of `audit-manage-issues.py` closes any open issue
carrying a retired title, with a comment saying the criterion was
retired. Leave the entry in place afterwards: it costs one comparison
per repository, and a repository whose audit leg failed on the first
run after the retirement is closed on a later one. Retirement only
reaches repositories still in the audit matrix, since it runs over
each run's results: issues in a repository dropped from scope have to
be closed by hand, as its live criteria's issues do. A retired title can
never be filed again by a live criterion, and
`RetiredIssueTitlesTest` in `scripts/tests/test_metadata.py` fails if
one is. Renaming a title safely is the second step on its own: change
the `issue_title` and its frozen line, retire the old spelling, and the
next run closes the old issues and files under the new title.

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
`scripts/tests/test_audit_update_docs.py` fails on the omission; and
`test_criteria_sharing_a_spec_page_all_declare_a_column` in
`scripts/tests/test_metadata.py` fails on it from the other direction,
reading the registry rather than the documentation.

A new criterion does not require a re-audit of anything else, and does
not require touching any project repository. The next run measures it
everywhere and files the issues; dispatch the workflow by hand if you
want to see that before Monday.

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
`REPO_OVERRIDES` in `scripts/audit/repo.py`.

A repository that should be audited for some checks but not others
takes an `only_checks` list in the same place. `private-ci` is the
worked example: it is internal tooling and exempt from the conventions,
but it vendors sfui, and a vendored copy drifts silently. The list is
expected to grow as a repository adopts more of the fleet's
conventions -- private-ci started at `sfui-vendor` alone and picked up
the four plan criteria when it adopted the plan template and index --
so widening it is a normal edit rather than a sign the scoping was
wrong. Each addition is still a decision that has to be stated in the
comment above the override, and checked against a real clone first.
Checks outside the list report `not_applicable` **with a reason**
rather than being omitted -- `audit-update-docs.py` renders a check it cannot find as
`unknown`, and "we decided not to" must not read as "we did not
measure".

A scoped repository does not follow the steps above. It goes in the
matrix, but stays *off* the in-scope list in `docs/audits/README.md`
and *on* the excluded list on the same page: both statements are true
of it, because it is excluded from the conventions and audited for a
few criteria anyway.
`test_matrix_matches_the_documented_scope` in
`scripts/tests/test_registry.py` subtracts the scoped repositories before
comparing, so onboarding one the way the steps above say will fail that
test.

## Testing a change

The full gate, which `ci.yml` also runs on every pull request:

```
pre-commit run --all-files
```

The scripts are standard library only and run on whatever `python3`
is on the path, which has to be Python 3.11 or later: the audit and
`review-tracking.py` read TOML with `tomllib`, which 3.11 added to the
standard library. That floor applies to a local run and to the
runners the workflows use alike, and an older interpreter fails at
import rather than measuring anything.

Every test suite lives in `scripts/tests/`, including the suites for
the scripts around the package -- `test_review_tracking.py`,
`test_audit_update_docs.py`, `test_audit_snapshot.py`,
`test_check_audit_smoke.py`, `test_manage_issues.py`,
`test_audit_clis.py`, `test_issue_fix_extraction.py` and
`test_audit_seams.py`. They sat in `scripts/` until there were two
directories with no rule telling them apart. A suite written anywhere
else is a suite no hook runs, and `SuiteLocationTest` in
`scripts/tests/test_hooks.py` fails on one.

The issue scripts are tested against `FakeIssueTracker` in
`scripts/tests/fake_issues.py`, which answers their `gh` invocations
out of an in-memory tracker and records every write, so a test asserts
what a run would do to a repository's issues rather than which helper
it called.

Two `local` pre-commit hooks run them. `audit-package-tests` discovers
the directory and runs all of it, on a change to any Python file under
`scripts/`, anything under `tools/` or `templates/`, a workflow,
`.pre-commit-config.yaml` or any `.md`. `review-tracking-tests` runs
that one suite again on every commit, whatever it touches: it is the
only hook with `always_run`, because it asserts properties of the
whole tree and a review-only commit matches nothing the other pattern
names. A commit that matches both runs that suite twice. The patterns
themselves are in `.pre-commit-config.yaml`, which is the authority
when this paragraph and it disagree.

One module at a time, which is quicker while iterating, and each runs
standalone:

```
python3 -m unittest discover -s scripts/tests -t scripts
python3 scripts/tests/test_review_tracking.py
python3 scripts/tests/test_plans.py
```

A test for a check subclasses `CheckTestCase` from
`scripts/tests/base.py` rather than building its own fixture and
assertion scaffolding. It provides `self.fixture`, a `FixtureRepo`
over a throwaway checkout (`write()`, `write_all()`, `workflow()`,
`workflows()`, `init_git()`, `commit()`); `self.fresh_fixture()`,
which replaces it with an empty one; `self.check(**props)`,
which runs `check_class` against the fixture with repository
properties supplied directly rather than detected; `check_args=` on
`self.check()` for the three checks whose constructors take an
argument (`PushAudit`, `PlanTemplate` and `SfuiVendor` -- a set
`ContractTest` pins against the registry, so a fourth cannot
appear without this sentence failing); and the
`assert_pass(result)` / `assert_fail(result, containing=)` /
`assert_skip(result, containing=)` triple in place of a bare
`assertEqual` against `result['status']`. A class that tests a pure
helper rather than a `Check` subclass -- a regex, a parsing function,
a spec-page comparison -- correctly does not use it; it stays a plain
`unittest.TestCase`, since there is no check to run and nothing
`CheckTestCase` would add.

Two conventions in `write_all()` are worth knowing before writing a
fixture. A `None` content means the file is *absent* and is skipped;
an empty file is spelled `''`. Both readings were in use before the
helper existed, and absence is the one that fails silently if a
caller guesses wrong -- a check that distinguishes a missing file
from an empty one would quietly pass. And a helper that runs the
check more than once in a single test method must rebuild the
fixture first, by calling `self.fresh_fixture()`, or the first
call's files are still on disk for the second.

The tests cover the machinery, not what a check *decides* about a real
repository. Test that half against local clones:

```
mkdir -p /tmp/results
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/<repo> \
    --repo-name <repo> > /tmp/results/audit-result-<repo>.json
python3 scripts/audit-manage-issues.py --results-dir /tmp/results/ --dry-run
python3 scripts/audit-update-docs.py --results-dir /tmp/results/ \
    --no-issues --page /tmp/compliance.md
```

Always pass `--dry-run` to `audit-manage-issues.py`. Without it the
script creates, edits and closes real issues on real repositories.

Run them from the root of this repository, and pass `--page` as well,
as above. A locally generated page only covers the repositories you
fed it, so it is never what you want to keep, and without `--page` the
script rewrites the real `docs/audits/compliance.md` in place. A
`--page` that does not exist yet is seeded with a copy of the real
page, because the generated block is spliced between markers the
hand-written page carries; an existing one, such as the output of an
earlier run, is rewritten.

Do not discard a stray result with `git restore docs/audits/`. Every
other file in that directory is hand-written, and a `git restore` of
the directory throws away whatever you were editing along with the
generated page.

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

The checks in `NETWORK_CHECKS` in `scripts/audit_snapshot.py` reach
the network, so they can differ between two runs because something
outside the tree moved -- a repository setting, a workflow run, a new
repository in the organisation -- rather than because the code did.
They are reported under their own heading and do not affect the exit
code. Read the list there rather than from a copy on this page, which
is how this paragraph once fell two checks behind.

`NetworkCheckListTest` in `scripts/tests/test_audit_snapshot.py`
keeps the list honest. It takes the schedule from `CHECKS` in
`scripts/audit/registry.py`, and for each check follows its class
body, its base classes and the helpers it calls under
`scripts/audit/`, looking for the GitHub client (`_github()`,
`repo.github`, `GhCli(`) or a `git clone`. It fails if the set it
derives differs from `NETWORK_CHECKS` in either direction, so a check
that grows a GitHub call has to join the list, and one that loses its
last call has to leave it.

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
design. A check reporting `error` has not measured anything -- it
raised -- so the smoke job fails on one, as the scheduled run's
`audit-raised.py` step would.

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
