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

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#review-coverage).
