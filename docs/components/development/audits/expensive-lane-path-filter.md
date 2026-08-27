# Audit: Expensive lane path filtering

## What we check

Ephemeral VM runners (the `vm` label) are the expensive pool: the lanes
that run on them build entire clouds or boot guests, and a single run
costs tens of minutes to hours of capacity. A pull request or merge
queue entry touching only content no lane exercises should not pay for
them — the `docs/` directory, and the review-tracking state
(`REVIEWS.md` and the `.vscode` weaudit files).

Every workflow running `vm`-runner jobs on `pull_request` or
`merge_group` must be path-filtered, and the filter must exclude a
`docs/**` pattern where the repository has a `docs/` directory, and a
`REVIEWS.md` pattern where it carries `.vscode/review-scope.toml`.

Two mechanisms count:

- A workflow backing no required status check may use trigger-level
  `paths:` / `paths-ignore:`. An inclusion-style `paths:` list (naming
  what the lane exercises, as the rust workflows do) excludes
  everything else by construction and passes without pattern checks.
- A workflow backing a required status check must use a filter job
  instead — `dorny/paths-filter` feeding job-level `if:` conditions, as
  kerbside's `check_paths` jobs do. Trigger-level filtering cannot
  coexist with required checks: a required check in a `paths-ignore`'d
  workflow never reports on a filtered PR, and a required check that
  never reports blocks the merge forever, while a skipped one satisfies
  it.

kerbside is the worked example: `functional-tests.yml`,
`direct-qemu-functional.yml` and `sf-e2e-functional.yml` each carry a
`check_paths` filter job, `functional-tests.yml` also runs its filter
on `merge_group`, and
[kerbside's docs/testing.md](https://github.com/shakenfist/kerbside/blob/develop/docs/testing.md)
documents the design. When adopting it, note dorny/paths-filter's
`predicate-quantifier: 'every'` trap: the default ANY-match semantics
make a `'**'` pattern defeat every exclusion.

Dedicated content-scanner workflows are exempt. Their whole point is to
read the human-written text a filter would skip: a secret lands in docs
or review marks as easily as in code, and so does an instruction
smuggled into text an agent will load.

The tools that count are `CONTENT_SCANNERS` in `scripts/audit-check.py`
— the credential scanners (gitleaks, trufflehog, detect-secrets) plus
`skillsaw`, which lints the agent context. skillsaw earns the exemption
for the same reason the credential scanners do, not because it is a
secret scanner: `CONTENT_SCANNERS` is deliberately a superset of
`SECRET_SCANNERS`, so a repository whose only scanner is skillsaw still
fails [the secret scanning audit](/components/development/audits/secret-handling/).

*Dedicated* is measured per job: every job in the workflow must invoke
one of those tools, outside of comments. The argument for the exemption is about
the scanner job, not the file it lives in. Asking merely whether a
scanner appeared anywhere in the file gave `shakenfist/actions` a pass
for a `ci.yml` that ran lint, unit tests and the LLM reviewer on
ephemeral VMs for every documentation typo, on the strength of the
gitleaks job sitting beside them.

A mixed workflow is therefore held to the exclusion requirements
whether or not it already carries a filter, and its scanner jobs should
not consume the filter's output. Ryll's `ci.yml` is the worked example
of the shape, and currently of the mistake too: its gitleaks job
carries the same `check_paths` condition as the expensive lanes, so
ryll's secret scan skips documentation-only pull requests.

Other deliberate exceptions — a lane that must run even for docs-only
changes — take an `audit-ok: no-path-filter` comment anywhere in the
workflow file, ideally with a reason. client-python needed one when it
first put its agent-context lint beside its credential scan, before
this audit knew what skillsaw was; that marker is now redundant there.

Repositories with neither a `docs/` directory nor review tracking have
nothing for a filter to exclude and are not applicable, as are
repositories whose PR-triggered workflows never use `vm` runners.

## Template

No template — the correct shape depends on whether the workflow backs a
required status check. Copy the `check_paths` pattern from kerbside's
smoke workflows for gating lanes, or add trigger-level `paths-ignore`
for advisory ones.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#expensive-lane-path-filter).
