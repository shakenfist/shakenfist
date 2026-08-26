# Audit: Pre-push audit file

## What we check

Repositories that carry a pre-push audit runbook must:

* name it **`PUSH-AUDIT.md`** -- the historical `PUSH-TEMPLATE.md` is
  flagged as legacy, because the file is a runbook the operator follows
  before pushing rather than a template that gets copied, and
  `-TEMPLATE` is reserved for true templates like `PLAN-TEMPLATE.md`;
* embed the current **`readme-discipline`**, **`llm-doc-discipline`**
  and **`plan-phase-references`** shared blocks in its
  documentation-review section (see the `readme-structure`,
  `llm-doc-structure` and `plan-phase-references` audits for the
  policies they enforce);
* embed the current **`comment-proportion`** shared block in its
  code-quality review section;
* embed the current **`path-traversal-review`**,
  **`python-version-discipline`** and **`functional-test-coverage`**
  shared blocks, which carry the three criteria delegated to the
  reviewer because no grep can judge them (see the
  [security-sanitization](/components/development/audits/security-sanitization/),
  [python-version](/components/development/audits/python-version/) and
  [test-coverage](/components/development/audits/test-coverage/) audits for the policies they
  enforce);
* keep every embedded block verbatim and at the current version; and
* be **referenced from `AGENTS.md`**, so a session can discover it.

Repositories with no pre-push audit file are N/A: whether every project
should have one is a separate decision, not smuggled in here.

### Why the reference is checked

Checking only the file's contents is how the runbook went untriggered.
In August 2026 the audit was current and correct in eight repositories
while three `AGENTS.md` files mentioned it at all, and exactly one of
those said *when* to run it. No `CLAUDE.md`, `PLAN-TEMPLATE.md`, git
hook or CI job pointed at it either, so it ran when the operator
remembered it and not otherwise.

`AGENTS.md` is the surface checked because it is loaded into every
session. The check is deliberately shallow -- it looks for the
filename, not for particular wording -- because the reference that
matters is the one a repository writes for itself. A repository still
on the legacy name is checked against that name, so it is told to
rename the file once rather than told twice about a file it does not
have.

The reference is necessary but not sufficient: discoverable is not run.
What makes it run is the `plan-push-audit-phase` shared block, which
puts a push-audit phase at the end of every master plan. That block
lives in `PLAN-TEMPLATE.md` and is enforced by the `plan-template`
audit, not this one.

### Why comment proportion is a judgment check

Comment volume has no honest mechanical threshold: the same twenty-line
docstring is right on a lock-ordering contract and wrong on a
three-line accessor. What can be mechanised is finding the
*candidates* -- runs of added comment lines, and comment blocks larger
than the body they precede -- which a repository may add to its wave-1
sweep as a report-only grep. The proportionality call belongs to the
code-quality judgment agent, which is why `comment-proportion` is
shared wording for a sub-agent brief rather than a check in
`audit-check.py`.

### Shared blocks

Shared blocks are canonical wording embedded verbatim across
repositories between `<!-- shared-block: <name> v<N> -->` and
`<!-- shared-block-end -->` markers; the canonical copies live in
`templates/shared-blocks/`, whose `README.md` describes the
mechanism. The check fails when a required block is missing, stale,
drifted from the canonical wording, unknown, or missing its end marker.

This audit exists because the pre-push audit files drifted
independently in each repository -- several still told the
documentation reviewer that "`README.md` reflects any new features",
which is the exact feedback loop that bloats READMEs.

## Template

Template: `templates/shared-blocks/`
See: `templates/shared-blocks/README.md`

To fix a non-compliant repository, copy each named block verbatim from
`templates/shared-blocks/` into `PUSH-AUDIT.md` and reference the file
from `AGENTS.md`; `templates/shared-blocks/README.md` describes the
markers and the version bump procedure.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#26 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#82 |
| instar | non-compliant | shakenfist/instar#491 |
| kerbside | non-compliant | shakenfist/kerbside#370 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#110 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#323 |
| sfui | non-compliant | shakenfist/sfui#15 |
| shakenfist | non-compliant | shakenfist/shakenfist#3911 |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **divergulent** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **instar** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **kerbside** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **occystrap** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **ryll** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
- **sfui** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository); missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository); AGENTS.md does not reference PUSH-AUDIT.md (an audit nothing points at does not get run)
- **shakenfist** (Status): missing shared block path-traversal-review (copy it verbatim from templates/shared-blocks/path-traversal-review.md in the development repository); missing shared block python-version-discipline (copy it verbatim from templates/shared-blocks/python-version-discipline.md in the development repository); missing shared block functional-test-coverage (copy it verbatim from templates/shared-blocks/functional-test-coverage.md in the development repository)
<!-- consistency-audit:end -->
