# Audit: Release merge queue

## What we check

A repository that publishes through `.github/workflows/release.yml`
merges to its default branch through a GitHub merge queue: the
default branch's effective rules include a `merge_queue` rule.

`release-process` gates every release on CI having passed on the
tagged commit, on the default branch. A merge queue is what makes that
commit one CI tested as merged, before it landed. Without a queue, a
pull request merges on the results of its own branch, the merged
commit is tested only afterwards, and the gate finds out about a
broken merge when somebody tries to release it.

A repository without `release.yml` reports `not_applicable`, and the
check asks GitHub nothing about it: the test reads the checkout.

The check reads the default branch from
`GET /repos/shakenfist/<repo>` and its effective rules from
`GET /repos/shakenfist/<repo>/rules/branches/<branch>`, the same two
calls `merge-queue-config` makes. A branch with no rules answers an
empty list, which is a failure here. Any error from either call, or
an answer that is not a list of rules, is a check error rather than a
failure: it says the audit could not ask, not that the queue is
missing.

The check does not judge the queue's settings. That is
[merge-queue-config](/components/development/audits/merge-queue-config/)'s job, which skips a
branch with no queue; this criterion is what fails that branch where
there are releases.

### Configuration, not liveness

The criterion passes a queue that is configured but has never built a
merge group. It does not ask whether the queue has run, for example
"a pull request merged in the last 30 days, so a `merge_group` run
should exist in that window", for three reasons.

* **No criterion reads history.** Every criterion judges what the
  repository and its settings say now. One that depended on traffic
  would flip for a quiet repository with nothing about the repository
  having changed.
* **A queue that never runs is loud on its own.** Every pull request
  sits in it, unmerged, until somebody looks. That was seen within
  three days the one time it happened. Nothing unsafe ships in the
  meantime either: with nothing merging, there is no new commit to
  release.
* **The audit is weekly.** That is the wrong cadence for an incident,
  which a stuck queue is.

## Template

No template -- this is a ruleset configuration change. The fleet
reference is client-python-k3s's "Develop branch" ruleset: a serial
queue that `merge-queue-config` accepts, with the workflows' "Can
enqueue" and "Can merge" aggregator jobs as its required checks.

Add the aggregator jobs and the `merge_group` trigger to the gated
workflows first, and the ruleset second: a queue whose required
checks never report blocks every merge. `RELEASE-SETUP.md` in
`templates/release-automation/` covers the order and the follow-up
of bringing open pull requests up to date.

To inspect the current configuration:

```bash
gh api repos/shakenfist/<repo>/rules/branches/develop \
    --jq '.[] | select(.type == "merge_queue") | .parameters'
```

Afterwards, trigger the repository's `export-repo-config` workflow so
the change is captured in `.github/exported-config/`.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#release-merge-queue).
