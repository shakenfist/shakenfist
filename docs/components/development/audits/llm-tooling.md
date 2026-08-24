# Audit: LLM tooling

## What we check

Every project should have an `AGENTS.md` and an `ARCHITECTURE.md`.
Repetitive operations should be covered by a Claude skill where one
helps. Things that are likely to need a skill include remembering to
write unit or functional tests, updating documentation for
user-visible changes, and so forth.

## Template

No template -- these files are project-specific and must be written
with knowledge of the project's architecture and workflows.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | compliant | - |
| client-python | compliant | - |
| client-python-k3s | compliant | - |
| clingwrap | compliant | - |
| cloudgood | compliant | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#30 |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **library-utilities** (Status): Missing: AGENTS.md, ARCHITECTURE.md
<!-- consistency-audit:end -->
