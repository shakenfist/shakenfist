# Audit: LLM tooling

## What we check

Two rules. One is measured; the other is a judgment call and is not.

### AGENTS.md and ARCHITECTURE.md

**Measured** by `llm-tooling`. Every project should have an
`AGENTS.md` and an `ARCHITECTURE.md`, and the check fails on either
being missing from the repository root. It reads only whether they
exist; their shape is
[`llm-doc-structure`](/components/development/audits/llm-doc-structure/)'s to measure.

### Skills for repetitive operations

**Reviewed, not measured.** Repetitive operations should be covered
by a Claude skill where one helps. Things that are likely to need a
skill include remembering to write unit or functional tests, updating
documentation for user-visible changes, and so forth. Whether an
operation is repetitive enough to earn one is a judgment no check
makes, so a repository with no skills passes this criterion.

## Template

No template -- these files are project-specific and must be written
with knowledge of the project's architecture and workflows.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#llm-tooling).
