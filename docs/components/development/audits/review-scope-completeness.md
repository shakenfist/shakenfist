# Audit: Human review scope completeness

## What we check

Repositories with the human review tracking tooling deployed
(see [docs/code-review-tracking.md](/components/development/code-review-tracking/))
must not leave tracked files out of review scope by accident. Every
tracked file has to be either in scope, or matched by an `exclude`
entry in `.vscode/review-scope.toml`. Repositories without a scope
config do not have the tooling deployed and are reported as not
applicable.

A file leaves the review queue by one of two routes, and only one of
them is a decision:

- It matches an `exclude` pattern. Somebody weighed it and can say
  why on the line beside it. Generated output, vendored trees and
  verbatim upstream text all belong here.
- It matches nothing in `include`. Nobody weighed anything. This is
  what happens when a file type arrives that the `include` list was
  written before, and it is silent and permanent.

The check runs `scripts/review-tracking.py scope-orphans` against the
clone and fails on the second case, listing every file it found. It
asks the tooling rather than parsing the scope config itself, so that
the audit and the tooling cannot disagree about what in-scope means:
the `!` re-include semantics and the built-in exclusion of the review
state files (`.vscode/*` and `REVIEWS.md`, which can never attest to
themselves) both live in that script.

A `!` re-include counts as a decision in reverse. A file put back by
one and then dropped anyway because `include` does not name it is
reported: the config asks for that file to be reviewed and it is not
being reviewed.

## Why this is separate from review-coverage

[review-coverage.md](/components/development/audits/review-coverage/) measures the backlog against
the scope. This measures the scope. The two fail in opposite
directions, and the gap between them is exploitable: narrowing
`include` is the cheapest way to make a review-coverage issue close,
and until this check existed nothing noticed a repository that reached
full coverage by shrinking what counted.

The case that prompted it was not adversarial, which is the point.
`templates/renovate/renovate.json` in the development repository is a
template copied across the fleet -- by that repository's own scope
config the highest-value thing in it -- and it sat outside review for
as long as the `include` list had no JSON pattern, because JSON simply
had not come up. No issue could have been filed about it: from
review-coverage's perspective the repository was fully measured.

## How to fix it

Either name the file types, or stop enumerating them:

- Add a pattern covering the file, if it should be reviewed. Prefer
  an extension pattern to naming one file, so the next file of that
  type is covered too.
- Add an `exclude` entry, if it should not be, with a comment giving
  the reason. Machine-rewritten files are the common case; a file
  whose content changes without a human deciding it should can never
  hold a review mark that means anything.
- Set `include = []`, which means every tracked file, and lean
  entirely on `exclude`. This satisfies the check permanently and is a
  reasonable choice for a small repository.

The trade-off between the last option and an enumerated list is which
way a new file type fails. With `include = []` it silently joins the
review queue, and somebody excludes it if that was wrong. With a list
it fails this audit, and somebody decides. The second is louder, which
is why the check exists, but it is more maintenance and the first is
not wrong.

## Template

No template -- compliance is restored by editing
`.vscode/review-scope.toml` in the repository, which is
per-repository by nature. See
[docs/code-review-tracking.md](/components/development/code-review-tracking/) for the
scope config format, including the `!` re-include syntax.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#review-scope-completeness).
