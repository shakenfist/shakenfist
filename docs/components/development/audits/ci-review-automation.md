# Audit: CI review automation and developer automation

## What we check

### Automated review

* Claude Code automated review runs in the CI workflow, only after
  all other tests pass.
* The reviewer job must be a call to the shared reusable workflow
  `shakenfist/actions/.github/workflows/pr-auto-review.yml@main`,
  with the project's own test jobs in its `needs:` list. Writing the
  reviewer job out in full in the project's CI workflow is
  superseded: projects still carrying a hand-written
  `automated_reviewer` job should migrate to the reusable workflow
  and delete their `check-bot-commit` job, which the reusable
  workflow replaces with an API call.
* The reviewer must reach Claude Code through the shared action
  `shakenfist/actions/review-pr-with-claude@main` (not per-project
  scripts). The reusable workflow does this for its callers.
* The calling job needs `pull-requests: write` and `issues: write`
  permissions, because a cross-repository reusable workflow cannot
  grant itself more token scope than its caller has.
* The calling job must not pass `secrets: inherit`.
  `pr-auto-review.yml` declares no secrets and reads none -- it and
  `review-pr-with-claude` authenticate with `github.token`, which
  comes from the `permissions:` block above -- so inheriting buys the
  caller nothing while handing every secret the repository holds,
  publishing tokens included, to a workflow in another repository. The
  exposure is latent rather than active, but it means a bad change
  landing in `shakenfist/actions` would already have those secrets
  within reach. This applies to the `pr-auto-review.yml` call only:
  callers of `smoke-cluster.yml` and `export-repo-config.yml` do read
  secrets and inherit correctly.
* The automatic review must not pass `force` to the review action,
  so that a PR the bot has already reviewed is left alone.
  `pr-re-review.yml` is the only workflow which sets `force`, making
  an explicit human request the sole way to override an existing
  review.
* The reviewer runs Claude Code with
  `--dangerously-skip-permissions` while holding a write-capable
  token, and the PR diff is untrusted input, so the automatic review
  must be restricted to same-repository pull requests. Fork PRs are
  reviewed only on explicit human request.

### Developer automation

Projects should include bot-triggered workflows responding to
`@shakenfist-bot` comments from authorised users:

* `pr-re-review.yml` -- triggers another automated review (with
  `pull-requests: write` and `issues: write`).
* `pr-retest.yml` -- re-runs functional tests.

### The comment addresser is retired

`pr-address-comments.yml` answered `@shakenfist-bot please address
comments` by handing the review's items to Claude Code and pushing a
commit per item. It was retired in August 2026 because it went unused:
review items are worked through interactively with the reviewer
instead, and a bot authoring commits from a review no human had read
was the part that stopped anyone reaching for it.

Its remains are audited rather than ignored, because they are not
inert. The workflow triggers on `issue_comment`, so it holds
`contents: write` against the pull request branch for a feature nobody
wants; and it is the last thing in a project that calls
`render-review.py`, so the script and its schema are dead weight the
next project copies. The check therefore looks for the whole chain:

* `.github/workflows/pr-address-comments.yml`
* `address-comments-with-claude.sh`
* `render-review.py`
* `review-schema.json`

All four are searched for by basename anywhere in the tree. `tools/`
and `.github/workflows/` are the canonical homes, but deployments put
them elsewhere -- the check this replaced found a `contrib/` copy, and
a template directory carries the copy of the workflow the next project
installs -- and a dead file is dead wherever it sits. Naming only the
installed workflow would mean a maintainer who removes everything the
finding names deletes the scripts, leaves the template copy behind, and
passes the audit from then on while still handing the chain to the next
project.

Only a copy at `.github/workflows/pr-address-comments.yml` actually
runs, so only that one holds `contents: write` on the pull request
branch. The finding says so when it is present and calls the rest dead
weight when it is not, rather than asserting a privileged workflow the
maintainer would then go looking for and not find.

One exemption. A directory holding an `action.yml` or `action.yaml` is
a composite action's own source rather than a deployed copy, and is
skipped.
`shakenfist/actions` is in the matrix and is where
`review-pr-with-claude/render-review.py` and its schema actually live
-- the copies every project's reviewer runs, and the ones this
retirement sends projects to instead of their own. Without the
exemption the finding would name them, and the instruction below is to
remove everything it names in one commit, which would delete the
renderer out from under the reviewer in every repository at once. The
exemption covers the directory the manifest sits in and nothing below
it, and `shakenfist/actions` is still reported for the leftovers it
genuinely carries elsewhere in its tree.

Everything the finding names goes in one commit. Deleting the workflow
and keeping the scripts leaves the copy that gets propagated. The
reviewer is otherwise unaffected: it reaches `render-review.py`
through `shakenfist/actions/review-pr-with-claude@main`, which carries
its own copy and its own schema.

### The trigger handling must be the shared action

`pr-re-review.yml` must reach `shakenfist/actions/pr-bot-trigger@main`
rather than hand-rolling the phrase match, permission lookup, reaction
and refusal reply in inline shell. `pr-retest.yml` already does.

This is a security requirement, not a tidiness one. `pr-bot-trigger`
refuses pull requests from forks, and a hand-rolled copy does not
inherit that. The action's `pr-ref` output is `.head.ref` -- the branch
name in the *head* repository, carrying nothing to say which repository
that is -- and callers hand it to `actions/checkout` and to
`git push origin HEAD:refs/heads/<ref>` against **their own**
repository. Fork pull requests are commonly opened from the fork's
default branch, so `.head.ref` is literally `main`: the checkout
succeeds against the target's `main`, the bot commits to it, and the
push lands unreviewed commits there. No malice is required -- a
maintainer typing the trigger phrase on a fork pull request is enough.

Because the guard lives in the action, every workflow that uses it
picked the fix up at `@main` with no change on its side. That is the
whole argument for the requirement: a shared action is how a fix
reaches ten repositories at once, and a local copy is how one of them
misses it.

An earlier version of the template open-coded this, which is why every
deployment needs replacing rather than editing. The template copy had
also drifted in ways that matter less but point the same way: it
reacted with `+1` instead of `rocket`, worded its refusal differently,
and never checked the trigger phrase itself, so it could not distinguish
"phrase not matched" from "not authorized".

The check reports nothing when `pr-re-review.yml` is absent -- that is
already a finding on its own, and reporting both would be two findings
for one missing file.

### Test drift fixing (optional)

Projects with large test suites prone to drift should also add:

* `pr-fix-tests.yml` + `test-drift-fix.yml` -- triggers Claude Code
  to fix CI failures.

These use shared composite actions from the `actions/` repository:

* `shakenfist/actions/pr-bot-trigger@main`
* `shakenfist/actions/review-pr-with-claude@main`

### Automated reviewer prompt

The automated reviewer's prompt should ensure it checks that
documentation in the `docs/` directory has been updated for any
user-visible changes.

## Template

Template: `templates/ci-review-automation/`
See: `templates/ci-review-automation/README.md`
Docs: `docs/ci-review-automation.md`, `docs/automated-pr-review.md`

Test drift fixing template: `templates/test-drift-fix/`
See: `templates/test-drift-fix/README.md`

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#36 |
| agent-python | non-compliant | shakenfist/agent-python#126 |
| client-python | non-compliant | shakenfist/client-python#367 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#36 |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#36 |
| instar | non-compliant | shakenfist/instar#515 |
| kerbside | non-compliant | shakenfist/kerbside#360 |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#32 |
| occystrap | non-compliant | shakenfist/occystrap#120 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#303 |
| sfui | non-compliant | shakenfist/sfui#26 |
| shakenfist | non-compliant | shakenfist/shakenfist#3314 |

Details for non-compliant projects:

- **actions** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh); it is unused, and its workflow holds contents: write on the pull request branch
- **agent-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python-k3s** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh); it is unused, and its workflow holds contents: write on the pull request branch
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **cloudgood** (Status): Missing workflows: pr-re-review.yml
- **divergulent** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **instar** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **kerbside** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **library-utilities** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **occystrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **ryll** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py); it is unused, and its workflow holds contents: write on the pull request branch
- **shakenfist** (Status): Missing pr-retest.yml; pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
<!-- consistency-audit:end -->
