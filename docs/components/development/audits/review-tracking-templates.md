# Audit: Review tracking templates

## What we check

Repositories with the human review tracking tooling deployed
(see [docs/code-review-tracking.md](/components/development/code-review-tracking/))
carry three CI files copied from
[templates/review-tracking/](https://github.com/shakenfist/development/tree/main/templates/review-tracking):

| Template | Copy |
|----------|------|
| `prune-reviews.yml` | `.github/workflows/prune-reviews.yml` |
| `ci-prune-reviews.sh` | `tools/ci-prune-reviews.sh` |
| `review-tracking.sh` | `tools/review-tracking.sh` |

Each copy must be byte-identical to its template on the audited
repository's default branch. The check compares bytes, not text, so a
copy that differs only in its line endings fails. A missing copy
fails too, since the adoption procedure installs all three, and so
does one the audit will not read: a link, dangling or out of the
checkout, a directory, or a file over the 2 MiB read cap. Repositories
without a `.vscode/review-scope.toml` do not have the tooling deployed
and are reported as not applicable.

The development repository's own `tools/review-tracking.sh` is the one
exception. It runs `scripts/review-tracking.py` from its own tree
instead of searching for a clone, so it is not compared. Its
`prune-reviews.yml` and `ci-prune-reviews.sh` are compared like any
other repository's.

## Why byte-identical

A review mark attests to a blob SHA. A copy that is byte-identical to
the template has the template's blob SHA, so `import` can mark it
reviewed from the development repository's review of the template,
and the file is reviewed once for the whole fleet. A copy that differs
by one byte is a different blob that has to be reviewed again in every
repository that carries it.

A drifted copy is also stale. The copies are not edited in place; they
fall behind when the template changes and a repository is missed in
the re-sync. The template's changes are usually fixes. Several isolate
the prune job from shared runner state, so a repository left behind
still runs the version without those fixes.

## How to fix it

Copy the file from `templates/review-tracking/` in shakenfist/development,
unmodified, and check it with `git hash-object` against the template.
Make changes in the template, not in a repository's copy, and then
re-sync every repository listed in the template's README.

## Template

[templates/review-tracking/](https://github.com/shakenfist/development/tree/main/templates/review-tracking).
Its README describes each file and what the adopting repository
needs besides them.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#review-tracking-templates).
