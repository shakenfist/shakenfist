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

Dedicated content-scanner workflows (gitleaks, trufflehog,
detect-secrets) are exempt. Their whole point is to read the
human-written text a filter would skip: a secret lands in docs or
review marks as easily as in code.

*Dedicated* is measured per job: every job in the workflow must invoke
a scanner, outside of comments. The argument for the exemption is about
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
workflow file, ideally with a reason.

Repositories with neither a `docs/` directory nor review tracking have
nothing for a filter to exclude and are not applicable, as are
repositories whose PR-triggered workflows never use `vm` runners.

## Template

No template — the correct shape depends on whether the workflow backs a
required status check. Copy the `check_paths` pattern from kerbside's
smoke workflows for gating lanes, or add trigger-level `paths-ignore`
for advisory ones.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-25T06:54:21.186929+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#123 |
| client-python | compliant | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#29 |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#113 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#14 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **client-python-k3s** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **occystrap** (Status): 2 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering), python-unit-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **sfui** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
<!-- consistency-audit:end -->
