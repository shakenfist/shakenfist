# Audit: Scheduled workflow health

## Why

A scheduled workflow that has never worked looks exactly like one
nobody has needed yet. Both are silent. The Actions tab shows red, but
nobody opens the Actions tab of a repository where nothing seems to be
going wrong, and that is exactly the repository this happens in.

This is not hypothetical. `prune-reviews.yml` and `renovate.yml` were
installed in hunkydory on 2026-09-15 and failed every run for a week:
one was missing a secret, and the other's organisation secret had not
been granted to the repository. Once the cause was known, the fix took
five minutes. The week went on not knowing. Meanwhile `REVIEWS.md`
kept attesting a file that had been deleted, because the workflow that
should have pruned the row had never run successfully.

Every other criterion that looks at a workflow checks its *shape*, and
a workflow that is missing a secret has a perfect shape. A missing
secret isn't visible in the repository at all. It only shows up in the
failure of a run nobody reads. See shakenfist/development#165.

## What we check

For each workflow file in `.github/workflows/` that GitHub lists as
active, the check asks the Actions API for its run history on the
default branch. A workflow is a finding when both of these hold:

- **It has never succeeded** on the default branch, from any event. A
  manual `workflow_dispatch` run that worked counts as a success: it
  proves the installation was finished.
- **It has failed at least three times unattended**, meaning a
  `schedule` run or a `push` run on the default branch whose
  conclusion was `failure`, `startup_failure` or `timed_out`. These
  are the events that fire with nobody watching. A `pull_request` or
  `merge_group` failure fails in front of whoever opened the pull
  request.

Three failures is a pattern reached quickly by a workflow that runs
often, but the finding appears only when the audit next runs, which is
weekly. A daily workflow broken just after an audit is reported up to
about ten days later, and a weekly workflow only after its third
failed run, three weeks or more.

The finding names each workflow, how many of its runs failed, and a
link to the most recent failed run. It does not diagnose the cause.
The reason is in that run's log, and the usual one is an unfinished
installation: a missing secret, or an organisation secret not granted
to the repository.

## What we do not check

- **Workflows that worked once and then broke.** A workflow that ran
  fine for six months and broke yesterday is a different defect, with
  a different fix and a different urgency. Reporting both would make
  this criterion noisy enough to be ignored, which is the defect it
  exists to catch. "Never succeeded" is the case that hides best.
- **Workflows that have never fired.** `release.yml` in a project
  that has not released yet, or anything triggered only by
  `workflow_dispatch`, has no failures to count, and zero runs is not
  a defect.
- **Skipped and cancelled runs.** Concurrency groups cancel superseded
  runs as a matter of course. A run whose jobs were all skipped by
  their guards is a workflow choosing not to act, not one failing to.
  The review automation on a comment that was not a command is the
  common case.
- **Workflows with no file in the checkout.** GitHub keeps listing a
  workflow after its file is deleted, and lists dynamic ones such as
  Pages and Dependabot that have no file at all. Neither is the
  repository's to fix.

The run history comes from the most recent 100 runs of each workflow
on the default branch. Whether the workflow has *ever* succeeded is a
separate count over its whole retained history, so an old success is
still found even when a busy workflow has pushed it out of that
window.

## Template

No template. The fix is whatever the linked run's log says is
missing, usually a secret at the repository or organisation level.
Once the workflow succeeds once, the check passes.

If the workflow is not wanted, delete it. If it is wanted but
should not run on a schedule yet, remove the `schedule` trigger
until it can.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#scheduled-workflow-health).
