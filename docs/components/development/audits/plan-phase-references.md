# Audit: Plan phase references

## What we check

Documentation describes the current state of the software, not the
history of how it was built. `README.md`, `AGENTS.md`,
`ARCHITECTURE.md` and the files under `docs/` must not refer to the
phase numbers of implementation plans: wording like "feature YYY,
implemented in phase ZZZ" tells a reader nothing they need -- often
without even naming the plan the phase belongs to. Either the feature
is implemented, in which case the docs should describe it plainly, or
it is not, in which case the docs should link to the master plan in
`docs/plans/` rather than citing a phase.

`AGENTS.md` and `ARCHITECTURE.md` are in scope for the same reason
`README.md` is: they describe current behaviour to a reader who was
not present for the construction, so a
`## Phase 6: Bridge Lifecycle` section heading is as unhelpful there
as it would be in `docs/`. Their *shape* is a separate audit (`llm-doc-structure`).

The automated check greps the top-level `README.md`, `AGENTS.md` and
`ARCHITECTURE.md`, and every `.md` file under `docs/`, for
`phase <number>` (case-insensitive), skipping:

* any file under a `plans/` directory at any depth -- plan
  documents legitimately discuss their own phases;
* per-repository `doc_content_excludes` prefixes from
  `REPO_OVERRIDES` in `scripts/audit-check.py` -- shakenfist's
  `docs/components/` is an automated import of the other
  repositories' documentation, so auditing it would double-report
  findings that must be fixed at their source;
* fenced code blocks and inline code spans; and
* lines carrying an explicit `<!-- audit-ok: phase-reference -->`
  marker.

The word "phase" is reserved for plan documents. A procedural
document describing a live multi-stage process (a release runbook,
say) should call its stages "steps" or "stages"; the suppression
marker exists for the rare line where "phase <number>" is genuinely
not a plan reference.

Repositories with none of those files and no `docs/` directory are
reported as N/A.

The judgment half of the policy is enforced at the point where the
references are written: each repository's pre-push audit file
carries the canonical `plan-phase-references` shared block (see the
`push-audit` audit), which instructs the documentation reviewer to
keep plan history out of the docs.

This audit exists because feature documentation across the fleet
accreted phrasing like `since two-tier CI phase 3` and
`phase 6 is what makes...` -- references to the phase numbering of
historical plans that mean nothing to a reader who was not there
when the plan was executed.

## Template

No template -- reword the documentation to describe current
behaviour, moving any forward-looking material into a link to the
relevant master plan in `docs/plans/`. Consult the referenced plan
(`docs/plans/PLAN-*.md`) to work out what the wording should say
instead; the rewording must preserve the information, not delete
it.

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
| library-utilities | compliant | - |
| occystrap | compliant | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3732 |

Details for non-compliant projects:

- **shakenfist** (Status): 17 plan phase reference(s) in documentation (describe the current behaviour, or link the master plan in docs/plans/ instead of citing a phase number): ARCHITECTURE.md:199, docs/developer_guide/database_internals.md:315, docs/developer_guide/database_internals.md:319, docs/developer_guide/subsystem_internals.md:51, docs/developer_guide/subsystem_internals.md:151, docs/developer_guide/subsystem_internals.md:153, docs/developer_guide/subsystem_internals.md:172, docs/developer_guide/subsystem_internals.md:226, docs/developer_guide/subsystem_internals.md:243, docs/developer_guide/subsystem_internals.md:447 (+7 more)
<!-- consistency-audit:end -->
