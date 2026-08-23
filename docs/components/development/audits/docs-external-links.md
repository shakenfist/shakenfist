# Audit: Links out of docs/ are absolute

## What we check

Every relative markdown link in a repository's `docs/` tree must
resolve to a file that exists **inside** `docs/`. Anything pointing
outside it must be an absolute URL.

`docs/` is not only rendered on the GitHub file tree. It is
synchronised into `shakenfist/shakenfist` under `docs/components/<repo>/`
and published on shakenfist.com, where the tree above `docs/` does not
exist. A link like `[release.yml](../.github/workflows/release.yml)`
resolves to `docs/components/.github/workflows/release.yml` there and
404s, while the identical link renders correctly on GitHub -- so
nothing catches it in the source repository.

Two shapes are flagged:

* **Escaping relative links.** The target resolves above `docs/`:
  `../README.md`, `../../ryll/src/app.rs`.
* **Relative links that resolve nowhere.** The target stays under
  `docs/` but names no file that exists. In practice this is almost
  always a link out of `docs/` written against the repository root
  (`ryll/src/app.rs` rather than `../../ryll/src/app.rs`) -- the same
  defect wearing a different spelling, and dead on GitHub too.

Links whose target stays inside `docs/` and resolves are fine, and
should stay relative: they move with the tree and work in both
renderings. Pure in-page anchors (`#section`), scheme-qualified URLs
and protocol-relative `//host` URLs are absolute already and are not
flagged.

Site-root-absolute targets (`/operator_guide/locks/`) are also left
alone. They are the mkdocs convention for addressing another page of
the same site, and they resolve on the published site -- which is the
rendering this audit exists to protect. They do not resolve on the
GitHub file tree, but that is a separate trade-off the mkdocs-hosted
repositories have already made, not a regression this audit should
manufacture issues about.

`docs/plans/` is **in scope**. Plans are synchronised to the
documentation site along with everything else, so a broken link there
is broken for a reader whether or not anyone still maintains the file.
A plan that references a path which has since moved should still carry
an absolute URL; the link form is the thing being audited, not whether
a historical path still resolves.

A repository's `doc_content_excludes` prefixes are skipped. For
`shakenfist` that is `docs/components/` itself: those are imported
copies of other repositories' documentation, audited at their source,
and flagging the import would double-report every finding.

Links inside fenced code blocks and inline code spans are ignored: a
documented command that happens to contain `[x](y)` is sample text,
not a rendered link.

This audit composes with `readme-absolute-links`, which covers the
top-level `README.md` for the same underlying reason -- a file that is
rendered somewhere other than where it lives cannot use relative
links.

## Template

No template -- rewrite each offending link target to an absolute URL.
For links to other files in the same repository, use
`https://github.com/<org>/<repo>/blob/<default-branch>/<path>`.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-23T06:45:38.740880+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#7 |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#68 |
| instar | non-compliant | shakenfist/instar#502 |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3792 |

Details for non-compliant projects:

- **cloudgood** (Status): 2 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/index.md -> more-fundamentals.md, docs/virtualization-history.md -> more-fundamentals.md
- **divergulent** (Status): 8 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/plans/PLAN-faster-full-run.md -> ../../PLAN-TEMPLATE.md, docs/plans/PLAN-full-machine-run.md -> ../../PLAN-TEMPLATE.md, docs/plans/PLAN-generated-marking.md -> PLAN-maintenance-health.md, docs/plans/PLAN-initial.md -> ../../PLAN-TEMPLATE.md, docs/plans/PLAN-patch-classification.md -> ../../PLAN-TEMPLATE.md, docs/plans/PLAN-published-cache.md -> ../../PLAN-TEMPLATE.md, docs/plans/PLAN-release-1.0.md -> ../../PLAN-TEMPLATE.md, docs/plans/index.md -> ../../PLAN-TEMPLATE.md
- **instar** (Status): 44 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/amend.md -> ../src/crates/amend/src/qcow2.rs, docs/amend.md -> ../tests/test_amend.py, docs/bench.md -> ../src/crates/bench/, docs/bench.md -> ../src/crates/qcow2-write-exec/, docs/bench.md -> ../src/crates/qcow2-write/, docs/bench.md -> ../src/operations/bench/, docs/bench.md -> ../tests/test_bench.py, docs/bitmap.md -> ../src/crates/bitmap/, docs/bitmap.md -> ../src/operations/bitmap/, docs/bitmap.md -> ../tests/test_bitmap.py (+34 more)
- **shakenfist** (Status): 13 relative link(s) in docs/ that do not resolve to a file inside docs/ (use absolute https://github.com/... URLs, which survive the docs site import): docs/operator_guide/database.md -> ../../shakenfist/schema/object_filter.py, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/instance.py#L177-L183, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/instance.py#L344, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/network/network.py#L182, docs/plans/PLAN-sql-pushdown-filtering-phase-03-instance-network.md -> shakenfist/network/network.py#L964, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/artifact.py#L609, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/artifact.py#L387, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/instance.py#L893, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/external_api/network.py#L292, docs/plans/PLAN-sql-pushdown-filtering-phase-04-iterators.md -> shakenfist/instance.py#L2137 (+3 more)
<!-- consistency-audit:end -->
