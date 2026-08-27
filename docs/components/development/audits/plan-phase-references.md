# Audit: Plan phase references

## What we check

Documentation describes the current state of the software, not the
history of how it was built. `README.md`, `AGENTS.md`,
`ARCHITECTURE.md` and the files under `docs/` must not refer to the
phase numbers of implementation plans: wording like "feature YYY,
implemented in phase ZZZ" tells a reader nothing they need, often
without even naming the plan the phase belongs to. Either the feature
is implemented, in which case the docs describe it plainly, or it is
not, in which case they link to the master plan in `docs/plans/`.

`AGENTS.md` and `ARCHITECTURE.md` are in scope for the same reason
`README.md` is: they describe current behaviour to a reader who was not
present for the construction, so a `## Phase 6: Bridge Lifecycle`
heading is as unhelpful there as in `docs/`. Their *shape* is a
separate audit (`llm-doc-structure`).

The check greps the top-level `README.md`, `AGENTS.md` and
`ARCHITECTURE.md`, and every `.md` file under `docs/`, for
`phase <number>` (case-insensitive), skipping:

* any file under a `plans/` directory at any depth -- plan documents
  legitimately discuss their own phases;
* per-repository `doc_content_excludes` prefixes from `REPO_OVERRIDES`
  in `scripts/audit-check.py` -- shakenfist's `docs/components/` is an
  automated import of the other repositories' documentation, so
  auditing it would double-report findings that must be fixed at their
  source;
* fenced code blocks and inline code spans; and
* lines carrying an explicit `<!-- audit-ok: phase-reference -->`
  marker.

The word "phase" is reserved for plan documents. A procedural document
describing a live multi-stage process -- a release runbook, say --
should call its stages "steps"; the suppression marker exists for the
rare line where "phase <number>" is genuinely not a plan reference.

Repositories with none of those files and no `docs/` directory are N/A.

The judgment half of the policy is enforced where the references are
written: each repository's pre-push audit file carries the canonical
`plan-phase-references` shared block (see the `push-audit` audit),
which instructs the documentation reviewer to keep plan history out of
the docs.

## Template

No template -- reword the documentation to describe current behaviour,
moving any forward-looking material into a link to the relevant master
plan in `docs/plans/`. Consult the referenced plan to work out what the
wording should say instead; the rewording must preserve the
information, not delete it.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#plan-phase-references).
