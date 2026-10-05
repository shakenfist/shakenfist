# Audit: Secrets passed to reusable workflows

## What we check

### Measured

These are the requirements the check decides a pass or a failure on,
and the only ones that produce an issue.

* No job that calls a reusable workflow passes `secrets: inherit`.
  This covers callees in other repositories (`uses:
  shakenfist/actions/.github/workflows/...@main`) and callees in the
  same repository (`uses: ./.github/workflows/...`) alike.

A quoted or trailing-commented `inherit` is still a finding; a
commented-out line is not. The explicit mapping form, naming each
secret, is what the criterion asks for and is never matched.

Two callees are left to the criteria that already own them, so that
one line does not file two issues: `export-repo-config.yml`
([export-repo-config](/components/development/audits/export-repo-config/)) and
`pr-auto-review.yml` ([ci-review-automation](/components/development/audits/ci-review-automation/)).

### Required, but confirmed by a reviewer

* A reusable workflow that reads a secret declares it under
  `on.workflow_call.secrets`, with a description of what it is used
  for, and the caller passes exactly that:

  ```yaml
  # The reusable workflow
  on:
    workflow_call:
      secrets:
        PYPI_TOKEN:
          description: 'Publishes the release to PyPI.'
          required: true

  # The caller
  jobs:
    publish:
      uses: shakenfist/actions/.github/workflows/publish.yml@main
      secrets:
        PYPI_TOKEN: ${{ secrets.PYPI_TOKEN }}
  ```

* A reusable workflow that reads no secret is passed nothing.
  `github.token` is available to a called workflow without being
  passed, and carries the caller's `permissions:` block, so needing to
  talk to GitHub is not a reason to pass anything.

## Why

`secrets: inherit` hands the called workflow every secret the calling
job can see: publishing tokens, deploy keys, anything the repository or
organisation has granted it. Most of the fleet's reusable workflows
live in `shakenfist/actions` and are called at `@main`, a moving ref.
Together that means whatever lands on `actions`' `main` next can read
every secret in every calling repository, and nothing in the calling
repository was reviewed to allow it
([shakenfist/development#153](https://github.com/shakenfist/development/issues/153)).

Naming the secrets narrows that to the ones the callee was written to
use, and makes a callee starting to read a new secret a visible change
in two places: its declaration, and every caller that has to start
passing it. A callee that reads a secret it was not passed gets an
empty string, so the failure is loud rather than a quiet widening.

When this criterion was added on 2026-09-29 it was believed that no
reusable workflow in the fleet read a secret. A survey on 2026-10-02
found one exception. The shared callees in shakenfist/actions and the
`test-drift-fix.yml` template read none, but instar's local copy of
`test-drift-fix.yml` reads `GITLAB_TESTDATA_TOKEN` in its "Prepare
instar-testdata" step. The fix proposed for that copy, in
[shakenfist/instar#617](https://github.com/shakenfist/instar/pull/617),
uses the named form described under "Required, but confirmed by a
reviewer" above: the callee declares the secret under
`on.workflow_call.secrets` with `required: false`, and
`pr-fix-tests.yml` passes it by name. The same pull request deletes
instar's `export-repo-config.yml` inherit. Every other inherit the
criterion found -- `export-repo-config.yml` callers in eight more
repositories, `smoke-cluster.yml` callers in shakenfist and
client-python, and the `test-drift-fix.yml` callers that
`templates/test-drift-fix/` had put in shakenfist and occystrap --
was fixed by deleting the line. Those fixes also moved the shakenfist
and occystrap copies of `test-drift-fix.yml` from
`secrets.GITHUB_TOKEN` to `github.token`, as the template already
has, so that they do not depend on the secrets context of a called
workflow at all; instar#617 does the same for instar's copy.

Local callees are measured even though the moving-ref half of the
argument does not apply to them: the declared list is still what makes
a secret being read visible in review, and one rule with no exceptions
is easier to hold across the fleet than one with a carve-out.

## What this does not cover

Pinning reusable workflows or third-party actions to a commit sha
rather than `@main` or a tag. That is the other half of #153, and a
separate decision: it trades the moving ref for a bump in every calling
repository on every change to `actions`.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#reusable-workflow-secrets).
