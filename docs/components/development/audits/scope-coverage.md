# Audit: audit scope against the organisation

## What we check

Every repository in the `shakenfist` organisation is either in the
audit matrix in `.github/workflows/consistency-audit.yml` or on the
excluded list in [README.md](/components/development/audits/README/), and every name in either of
those still resolves to a repository in the organisation.

This is the only criterion that measures the fleet rather than the
repository it was handed. It runs against the `development`
repository, because that is where the lists are written, and reports
`not applicable` everywhere else.

## Why this exists

Scope is written down three times: the `repo:` matrix, the in-scope
list and the excluded list. `AuditScopeIsStatedOnceTest` in
`scripts/tests/test_registry.py` holds those three in agreement with
each other, which catches a repository added to the matrix but not to
the documentation, or dropped from the matrix while the documentation
still claims it.

Nothing compared any of them to the organisation. A repository in none
of the three was not audited, was not documented as excluded, and
produced no finding anywhere -- silent by construction, because the
only signal was a repository missing from a list nobody diffs. When
this check was written, five repositories were in that state and three
names on the excluded list had not existed for over a year.

Both directions are the same missing reconciliation:

- **A repository nobody decided about.** Auditing it and excluding it
  are both fine. Having made neither decision is not, and it is
  indistinguishable from the outside from a decision to exclude.
- **A name that no longer resolves.** Harmless in itself -- an
  exclusion for a repository that does not exist excludes nothing --
  but it means the list has never been reconciled against reality, and
  a list nobody reconciles is one nobody can trust in the other
  direction either.

## Archived repositories are not exempt

`isArchived` is the obvious filter and is deliberately not used. Every
archived repository in the organisation is already on the excluded
list, so requiring a decision for all of them costs nothing to adopt.

The case that settles it is a repository dormant for years that nobody
archived: an `isArchived` filter passes it silently, which is the exact
failure this criterion exists to remove. Where a repository really is
finished, archiving it and listing it as excluded are both cheap, and
between them they say so in two places a reader can see.

## How to fix it

For a repository the check names as undecided, one of:

- Add it to the matrix in `.github/workflows/consistency-audit.yml`
  and to the in-scope list in [README.md](/components/development/audits/README/). See "Bringing a
  repository into scope" in
  [docs/consistency-audits.md](/components/development/consistency-audits/), and expect
  every failing criterion to file an issue on the next run.
- Add it to the excluded list in [README.md](/components/development/audits/README/), which is a
  decision with a reason attached rather than an omission.

For a name that is not in the listing, the check asks GitHub about it
directly rather than assuming, and says which of four things happened:

- **It no longer exists** -- the API answered 404. The entry goes with
  it, subject to the caveat below.
- **It was renamed.** The finding names the new name; write that in
  the matrix or the list. The API follows a rename redirect while
  issue listing and search do not, which is why a stale name is worth
  fixing rather than tolerating: `audit-manage-issues.py` has its own
  warning for the same trap.
- **It exists but the listing did not return it.** Then the lists are
  right and the listing is short. Nothing about the scope is wrong,
  and the finding says to check the token, because a listing that
  cannot see part of the organisation cannot answer the first question
  either.
- **It could not be resolved either way** -- an expired token, rate
  limiting, a timeout, or a 404 this token cannot interpret (see
  below). Reported as exactly that. "Gone" is the one conclusion whose
  suggested fix is destructive, so nothing arrives at it by accident.

Each name is resolved on its own, and a failure on one is recorded
against that name rather than abandoning the run. The undecided
question needs no API access at all, and it is the half of the check
that matters: a slow call must not discard it.

## The 404 that means nothing

GitHub answers 404, not 403, for a private repository the token cannot
see, so one name at a time a blind token is indistinguishable from a
deletion. What tells them apart is the listing: if it returned no
private repositories at all, then this token 404s on every private
repository in the organisation, and a 404 carries no information.

Nothing is reported as gone in that state. The finding names the token
instead, because granting it private-repository read is the only edit
that can clear it -- and a criterion that is permanently red for a
reason nobody can act on is one people learn to skip past.

## Partially scoped repositories

A repository audited for a subset of the checks -- `private-ci` is the
worked example -- is decided by being in the matrix and on the excluded
list at once, which is what
[docs/consistency-audits.md](/components/development/consistency-audits/) already tells
you to do when you scope one. The "in scope for part of the audit
only" paragraph on [README.md](/components/development/audits/README/) is prose for a reader; it is
not one of the lists this check reads. A repository documented only
there would be reported as undecided, correctly: an `only_checks`
entry in `REPO_OVERRIDES` narrows what runs, it does not put a
repository in the audit.

## What this does not check

Whether a repository *should* be audited. That is a judgement about
the project, and the check makes no attempt at it: what it removes is
the third state, where nobody made the judgement either way.

It also knows nothing about repositories outside the GitHub
organisation. Projects on the private GitLab are invisible to it.

## Template

No template -- compliance is restored by editing the matrix and the
lists, which is per-decision by nature.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#scope-coverage).
