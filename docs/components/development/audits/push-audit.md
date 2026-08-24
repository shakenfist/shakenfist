# Audit: Pre-push audit file

## What we check

Repositories that carry a pre-push audit runbook must:

* name it **`PUSH-AUDIT.md`** -- the historical name
  `PUSH-TEMPLATE.md` is flagged as legacy (the file is a runbook the
  operator follows before pushing, not a template that gets copied,
  and the `-TEMPLATE` suffix is reserved for true templates like
  `PLAN-TEMPLATE.md`);
* embed the current **`readme-discipline` shared block** in its
  documentation-review section;
* embed the current **`llm-doc-discipline` shared block** in its
  documentation-review section (see the `llm-doc-structure` audit
  for the policy it enforces);
* embed the current **`comment-proportion` shared block** in its
  code-quality review section;
* embed the current **`plan-phase-references` shared block** in its
  documentation-review section (see the `plan-phase-references`
  audit for the policy it enforces);
* keep every embedded shared block verbatim and at the current
  version; and
* be **referenced from `AGENTS.md`**, so a session has some way of
  discovering it.

Repositories with no pre-push audit file at all are reported as N/A:
whether every project should have one is a separate decision, not
smuggled in here.

### Why the reference is checked

Checking only the file's contents is how the runbook went
untriggered. In August 2026 the audit was current and correct in
eight repositories while three `AGENTS.md` files mentioned it at
all, and exactly one of those said *when* to run it -- the other
two were passive index entries. No `CLAUDE.md`, no
`PLAN-TEMPLATE.md`, no git hook and no CI job pointed at it either,
so the audit ran when the operator remembered it and not otherwise.

Mention is what this check measures, so three is the number it
moves; one is the number that had a trigger. The check closes the
discoverability half of that gap, not the trigger half.

`AGENTS.md` is the surface checked because it is loaded into every
session, which makes it the one place a reference is certain to be
read. The check is deliberately shallow -- it looks for the
filename, not for particular wording -- because the reference that
matters is the one a repository writes for itself. A repository
still on the legacy `PUSH-TEMPLATE.md` name is checked against that
name, so it is told to rename the file once rather than told twice
about a file it does not have.

The reference is necessary but not sufficient: discoverable is not
the same as run. What makes it run is the `plan-push-audit-phase`
shared block, which puts a push-audit phase at the end of every
master plan; that block lives in `PLAN-TEMPLATE.md` and is enforced
by the `plan-template` audit rather than by this one.

### Shared blocks

A shared block is canonical wording embedded verbatim across
repositories, delimited by versioned markers:

```markdown
<!-- shared-block: <name> v<N> -->
...canonical wording...
<!-- shared-block-end -->
```

Canonical copies live in `templates/shared-blocks/<name>.md` in this
repository (markers included); see
`templates/shared-blocks/README.md` for the mechanism. The check
fails when an embedded block is missing where required, carries a
stale version, has drifted from the canonical wording, is unknown
(no canonical file), or is missing its end marker.

To update shared wording: edit the canonical file, bump its version,
and commit. The next daily audit run marks every repository carrying
the old version non-compliant and files issues automatically.

This audit exists because the pre-push audit files drifted
independently in each repository -- several still instructed the
documentation reviewer that "`README.md` reflects any new features",
which is the exact feedback loop that bloats READMEs (see the
`readme-structure` audit for the policy those instructions now
enforce instead).

### Why comment proportion is a judgment check

Comment volume has no honest mechanical threshold: the same
twenty-line docstring is right on a lock-ordering contract and
wrong on a three-line accessor. What can be mechanised is finding
the *candidates* -- runs of added comment lines, and comment blocks
larger than the body they precede -- which a repository may add to
its wave-1 sweep as a report-only grep. The proportionality call
itself belongs to the code-quality judgment agent, which is why
`comment-proportion` is shared wording for a sub-agent brief rather
than a check in `audit-check.py`. The audit verifies the wording is
present and current; it does not try to score comments.

## Template

Template: `templates/shared-blocks/`
See: `templates/shared-blocks/README.md`

To fix a non-compliant repository: rename `PUSH-TEMPLATE.md` to
`PUSH-AUDIT.md` (updating references in `AGENTS.md`,
`MERGE-TEMPLATE.md`, `tools/audit/`, and plan documents), paste
the current contents of
`templates/shared-blocks/readme-discipline.md` verbatim into the
documentation-review section, replacing any older README guidance it
contradicts, paste
`templates/shared-blocks/comment-proportion.md` verbatim into the
brief for the code-quality review agent, and paste
`templates/shared-blocks/plan-phase-references.md` verbatim into the
documentation-review section.

To fix a missing reference: add a line to `AGENTS.md` naming
`PUSH-AUDIT.md` and saying when it is run. Where the repository
indexes its runbooks, that index is the natural home; the check only
requires the filename to appear.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-24T07:04:16.593679+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#26 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#65 |
| instar | non-compliant | shakenfist/instar#491 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#110 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#15 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **client-python-k3s** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository)
- **divergulent** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository)
- **instar** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository)
- **occystrap** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository)
- **sfui** (Status): missing shared block llm-doc-discipline (copy it verbatim from templates/shared-blocks/llm-doc-discipline.md in the development repository); missing shared block plan-phase-references (copy it verbatim from templates/shared-blocks/plan-phase-references.md in the development repository)
<!-- consistency-audit:end -->
