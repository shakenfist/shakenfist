# Audit: README absolute links

## What we check

Every link in the **top-level `README.md`** must be absolute. A link
target is acceptable if it is:

* a scheme-qualified URL (`https://`, `http://`, `mailto:`, `data:`,
  ...);
* a protocol-relative URL (`//host/...`); or
* a pure in-page anchor (`#section`), which resolves against the
  rendered page wherever it is shown.

Anything else -- `docs/x.md`, `./x.md`, `../x.md`, `/x`, `x.md` -- is
relative and is flagged.

Only the **top-level** `README.md` is audited. It is the file rendered
*off* the repository landing page: the PyPI long description,
crates.io, and README mirrors all display it in a context where a
relative link resolves against the wrong base and silently 404s.
READMEs in subdirectories are only ever viewed on the GitHub file
tree, where relative links resolve correctly, so they are
intentionally out of scope.

This audit exists because divergulent's first PyPI release rendered
with every relative link broken -- they pointed at `docs/…` relative
to the PyPI project page rather than the GitHub landing page. Absolute
`https://github.com/<org>/<repo>/blob/<branch>/…` URLs render
correctly on PyPI and still resolve on GitHub.

Links inside fenced code blocks and inline code spans are ignored: a
documented command that happens to contain `[x](y)` is sample text,
not a rendered link.

## Template

No template -- rewrite each relative link target to an absolute URL.
For links to other files in the same repository, use
`https://github.com/<org>/<repo>/blob/<default-branch>/<path>`.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-25T06:54:21.186929+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#107 |
| client-python | non-compliant | shakenfist/client-python#345 |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#40 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, docs/developer-guide.md, docs/index.md, docs/protocol.md
- **client-python** (Status): 3 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md
- **clingwrap** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md, docs/, docs/index.md
- **library-utilities** (Status): 1 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): docs/log-record-fields.md
<!-- consistency-audit:end -->
