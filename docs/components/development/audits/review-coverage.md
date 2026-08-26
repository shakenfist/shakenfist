# Audit: Human review coverage

## What we check

Repositories that have the human review tracking tooling deployed
(see [docs/code-review-tracking.md](/components/development/code-review-tracking/))
should keep the review backlog small: fewer than 5 in-scope files
needing review. Repositories without a `.vscode/review-scope.toml`
scope config do not have the tooling deployed and are reported as
not applicable; currently ryll and kerbside do.

The check runs `scripts/review-tracking.py status` against the
clone. A file needs review if it has never received a whole-file
review, or if its content at HEAD no longer matches the blob SHA
stamped for its last review (a stale review). Coverage is
deliberately recomputed against HEAD rather than trusted from the
committed `REVIEWS.md`: that file is only accurate immediately
after a prune, so a missed prune run cannot inflate the coverage
this audit sees.

Expect routine churn near the threshold: a single feature PR can
touch five in-scope files, so the issue this audit files acts as a
standing work-queue nudge -- it lists the files needing review,
and closes automatically once a review session brings the backlog
back under the threshold.

## Template

No template -- compliance is restored by doing review sessions,
not by copying files. See
[docs/code-review-tracking.md](/components/development/code-review-tracking/)
for the session workflow. Staleness on the default branch is
normally pruned automatically by the adopting repository's
`prune-reviews` workflow; this audit is the backstop that notices
when the backlog has grown regardless.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | non-compliant | shakenfist/actions#29 |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | non-compliant | shakenfist/development#45 |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#227 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#304 |
| sfui | N/A | - |
| shakenfist | N/A | - |

Details for non-compliant projects:

- **actions** (Status): 0 of 89 in-scope files reviewed at HEAD; 89 need review (threshold 5)
- **development** (Status): 14 of 77 in-scope files reviewed at HEAD; 63 need review (threshold 5)
- **kerbside** (Status): 125 of 194 in-scope files reviewed at HEAD; 69 need review (threshold 5)
- **ryll** (Status): 97 of 178 in-scope files reviewed at HEAD; 81 need review (threshold 5)
<!-- consistency-audit:end -->
