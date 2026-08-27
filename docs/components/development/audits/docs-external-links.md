# Audit: Links out of docs/ are absolute

## What we check

Every relative markdown link in a repository's `docs/` tree must
resolve to a file that exists **inside** `docs/`. Anything pointing
outside it must be an absolute URL.

`docs/` is not only rendered on the GitHub file tree. It is
synchronised into `shakenfist/shakenfist` under
`docs/components/<repo>/` and published on shakenfist.com, where the
tree above `docs/` does not exist. A link like
`[release.yml](../.github/workflows/release.yml)` resolves to
`docs/components/.github/workflows/release.yml` there and 404s, while
the identical link renders correctly on GitHub -- so nothing catches it
in the source repository.

Two shapes are flagged:

* **Escaping relative links**, whose target resolves above `docs/`:
  `../README.md`, `../../ryll/src/app.rs`.
* **Relative links that resolve nowhere.** The target stays under
  `docs/` but names no file that exists. In practice this is almost
  always a link out of `docs/` written against the repository root
  (`ryll/src/app.rs` rather than `../../ryll/src/app.rs`) -- the same
  defect wearing a different spelling, and dead on GitHub too.

Links whose target stays inside `docs/` and resolves are fine and
should stay relative: they move with the tree and work in both
renderings. Pure in-page anchors (`#section`), scheme-qualified URLs
and protocol-relative `//host` URLs are absolute already.

Site-root-absolute targets (`/operator_guide/locks/`) are left alone.
They are the mkdocs convention for addressing another page of the same
site and they resolve on the published site, which is the rendering
this audit exists to protect. That they do not resolve on the GitHub
file tree is a trade-off the mkdocs-hosted repositories have already
made, not a regression this audit should manufacture issues about.

`docs/plans/` is **in scope**. Plans are synchronised to the site along
with everything else, so a broken link there is broken for a reader
whether or not anyone still maintains the file. The link form is what
is audited, not whether a historical path still resolves.

A repository's `doc_content_excludes` prefixes are skipped -- for
`shakenfist` that is `docs/components/` itself, imported copies audited
at their source.

Links inside fenced code blocks and inline code spans are ignored: a
documented command containing `[x](y)` is sample text, not a rendered
link.

This audit composes with `readme-absolute-links`, which covers the
top-level `README.md` for the same underlying reason -- a file rendered
somewhere other than where it lives cannot use relative links.

## Template

No template -- rewrite each offending link target to an absolute URL.
For links to other files in the same repository, use
`https://github.com/<org>/<repo>/blob/<default-branch>/<path>`.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#docs-external-links).
