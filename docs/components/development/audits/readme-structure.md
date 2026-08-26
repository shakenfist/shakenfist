# Audit: README structure

## What we check

The top-level `README.md` is a **pitch**, not a reference manual: what
the project is, who it is for, minimal installation instructions, a
small number of usage examples, and curated links into `docs/`.
Feature catalogues, CI workflow tables, build internals, architecture
descriptions, and dependency lists belong in `docs/`,
`ARCHITECTURE.md`, or `AGENTS.md` instead.

"Is this a good pitch" is a judgment call, so the automated check
enforces measurable proxies:

* `README.md` is at most **150 lines** and at most **1200 words**;
* if the repository has a `docs/` directory, at least one README link
  points into it (composes with the `readme-absolute-links` audit,
  which makes that link absolute).

Repositories without a top-level `README.md` are reported as N/A.

The judgment half of the policy is enforced at the point where README
bloat is actually created: the documentation-review section of each
repository's pre-push audit file carries the canonical
`readme-discipline` shared block (see the `push-audit` audit), which
instructs the reviewer to send new feature documentation to `docs/`
and to treat README growth as a finding.

This audit exists because our READMEs accreted a bullet per feature
per push -- ryll's reached 558 lines -- burying the pitch that a
human landing on the repository page actually wants, and duplicating
content that `docs/` already covers.

## Template

No template -- move detailed content into `docs/` (or
`ARCHITECTURE.md` / `AGENTS.md`), keep a short pitch, and link to the
moved content. The move must be a *move*, not a delete: verify the
detail survives somewhere before trimming the README.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-25T06:54:21.186929+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#353 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#22 |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): README.md has no link into docs/ despite a docs/ directory existing; add curated links to the detailed documentation
- **client-python-k3s** (Status): README.md has no link into docs/ despite a docs/ directory existing; add curated links to the detailed documentation
<!-- consistency-audit:end -->
