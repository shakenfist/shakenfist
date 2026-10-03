# Audit: Exporting repo configuration changes

## What we check

### Measured

These are the requirements the check decides a pass or a failure on,
and the only ones that produce an issue.

* `.github/workflows/export-repo-config.yml` exists.
* No job passes `secrets: inherit` to `export-repo-config.yml`.

### Required, but confirmed by a reviewer

* The workflow delegates to the shared reusable workflow in
  `shakenfist/actions` and runs daily at 00:30 UTC.
* The workflow is project-agnostic and can be copied directly with
  no modifications.

## Why

**No `secrets: inherit`.** The shared workflow declares and reads no
secrets: it authenticates with `github.token`, which comes from the
calling workflow's `permissions:` block. Inheriting therefore buys
nothing while putting every secret the repository holds, publishing
tokens included, within reach of a workflow in another repository
called at a moving `@main`. The template passed it until September
2026, which is how every caller came to, so it is checked rather than
left to the template. The matcher is the one the `ci-review-automation`
criterion uses for `pr-auto-review.yml`: a quoted or trailing-commented
`inherit` is still a finding, a commented-out line is not, and the
explicit mapping form (naming each secret) is not matched.

## Template

Template: `templates/export-repo-config/`
See: `templates/export-repo-config/README.md`

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#export-repo-config).
