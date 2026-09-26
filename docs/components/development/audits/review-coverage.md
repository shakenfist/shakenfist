# Audit: Human review coverage

## What we check

Repositories that have the human review tracking tooling deployed
(see [docs/code-review-tracking.md](/components/development/code-review-tracking/))
should keep the review backlog small: fewer than 5 in-scope files
needing review. Repositories without a `.vscode/review-scope.toml`
scope config do not have the tooling deployed and are reported as
not applicable; see the compliance page for which repositories
currently carry it.

The check runs `scripts/review-tracking.py status` against the
clone. A file needs review if it has never received a whole-file
review, or if its content at HEAD no longer matches the blob SHA
stamped for its last review (a stale review). Coverage is
deliberately recomputed against HEAD rather than trusted from the
committed `REVIEWS.md`: that file is only accurate immediately
after a prune, so a missed prune run cannot inflate the coverage
this audit sees.

Reviews imported from shakenfist/development (see "Importing
reviews from this repository" in
[docs/code-review-tracking.md](/components/development/code-review-tracking/)) count
toward coverage on the same terms as a native review: their blob
SHA must still match HEAD. They count because they attest to
identical bytes -- a review of a blob is a review of every copy of
it -- and `import` verified the signed commit that introduced each
one before recording it (unless explicitly told not to with
`--no-verify`, which `REVIEWS.md` then shows). The details line names how many of the
reviewed files were imported, when any were, so that coverage
arriving by import is not mistaken for a review session.

A run that cannot be started at all -- an unusable checkout, an
interpreter that will not execute -- is reported as a failure of
this criterion against that repository, naming the error. It is
not allowed to raise, because the audit runs every criterion for a
repository in one process and an exception would take every other
criterion for that repository down with it.

Expect routine churn near the threshold: a single feature PR can
touch five in-scope files, so the issue this audit files acts as a
standing work-queue nudge -- it lists the files needing review,
and closes automatically once a review session brings the backlog
back under the threshold. That list is written when the issue is
filed and is not refreshed afterwards, so a long-lived issue
understates the backlog it names (shakenfist/development#138).

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
