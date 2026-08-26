# Audit: CI review automation and developer automation

## What we check

### Measured

These are the requirements the check decides a pass or a failure on,
and the only ones that produce an issue. The rest of this section is
part of the same standard; it is simply not decidable from the
workflow files alone, so a reviewer confirms it instead.

* Both developer workflows are present: `pr-re-review.yml`, which
  triggers another review, and `pr-retest.yml`, which re-runs the
  functional tests.
* Some workflow reaches Claude Code through the shared action
  `shakenfist/actions/review-pr-with-claude@main`, rather than a
  per-project script. Calling the reusable workflow below satisfies
  this, because that is how the reusable workflow reaches it.
* `pr-re-review.yml` reaches `shakenfist/actions/pr-bot-trigger@main`
  rather than open-coding the phrase match, permission lookup, reaction
  and refusal reply.
* No caller passes `secrets: inherit`.
* The retired comment addresser is gone from the tree, as below.

### Required, but confirmed by a reviewer

* The reviewer job is a call to the reusable workflow
  `shakenfist/actions/.github/workflows/pr-auto-review.yml@main`, with
  the project's test jobs in its `needs:`. Hand-written
  `automated_reviewer` jobs are superseded; migrating deletes the
  project's `check-bot-commit` job too. A compliant hand-written job
  still passes, because only the shared action above is measured.
* The calling job sets `pull-requests: write` and `issues: write`.
* The automatic review does not pass `force`, and runs on
  same-repository pull requests only.
* Optional, for suites prone to drift: `pr-fix-tests.yml` +
  `test-drift-fix.yml`.
* The reviewer prompt asks it to check that `docs/` was updated for
  user-visible changes.

### The comment addresser is retired

Measured. None of these may be present, anywhere in the tree:
`.github/workflows/pr-address-comments.yml`,
`address-comments-with-claude.sh`, `render-review.py`,
`review-schema.json`. Remove all four in one commit. A directory
holding an `action.yml`/`action.yaml` is a composite action's own
source and is exempt, which is what keeps the finding off
`shakenfist/actions/review-pr-with-claude/`.

## Why

**No `secrets: inherit` on `pr-auto-review.yml`.** It declares and
reads no secrets -- it and `review-pr-with-claude` authenticate with
`github.token` from the caller's `permissions:` block -- so inheriting
buys nothing while putting every secret the repository holds, including
publishing tokens, within reach of a workflow in another repository.
Callers of `smoke-cluster.yml` and `export-repo-config.yml` do read
secrets and inherit correctly.

**Same-repository pull requests only.** The reviewer runs Claude Code
with `--dangerously-skip-permissions` while holding a write-capable
token, and the diff is untrusted input. Fork PRs are reviewed on
explicit human request.

**The shared trigger action is a security requirement.**
`pr-bot-trigger` refuses fork pull requests; a hand-rolled copy does
not inherit that. Its `pr-ref` output is `.head.ref` -- a branch name
with nothing to say which repository it belongs to -- and callers hand
it to `actions/checkout` and to `git push origin HEAD:refs/heads/<ref>`
against *their own* repository. Fork PRs are commonly opened from the
fork's default branch, so `.head.ref` is literally `main`: the bot
commits to the target's `main` and pushes unreviewed. A maintainer
typing the trigger phrase on a fork PR is enough; no malice required.
Because the guard lives in the action, every user picked the fix up at
`@main` -- which is the argument for the rule. Deployments predating
the fix need replacing, not editing.

**The retired addresser is not inert.** It triggers on `issue_comment`,
so it holds `contents: write` on the PR branch for a feature nobody
wants, and it is the last caller of `render-review.py`, so the script
and schema are dead weight the next project copies. Deleting the
workflow but keeping the scripts leaves exactly the copy that
propagates. Background: `templates/ci-review-automation/README.md`.

## Template

Template: `templates/ci-review-automation/`
See: `templates/ci-review-automation/README.md`
Docs: `docs/ci-review-automation.md`, `docs/automated-pr-review.md`

Test drift fixing template: `templates/test-drift-fix/`
See: `templates/test-drift-fix/README.md`

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#126 |
| client-python | non-compliant | shakenfist/client-python#367 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#36 |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | compliant | - |
| divergulent | compliant | - |
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

- **agent-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml); it is unused, and its workflow holds contents: write on the pull request branch
- **client-python-k3s** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh); it is unused, and its workflow holds contents: write on the pull request branch
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **cloudgood** (Status): Missing workflows: pr-re-review.yml
- **instar** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **kerbside** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **library-utilities** (Status): Missing pr-re-review.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **occystrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **ryll** (Status): the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py); it is unused, and its workflow holds contents: write on the pull request branch
- **shakenfist** (Status): Missing pr-retest.yml; pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; the retired comment addresser is still deployed (.github/workflows/pr-address-comments.yml, tools/address-comments-with-claude.sh, tools/render-review.py, tools/review-schema.json); it is unused, and its workflow holds contents: write on the pull request branch
<!-- consistency-audit:end -->
