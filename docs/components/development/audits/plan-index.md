# Audit: Plan index

## What we check

`docs/plans/index.md` is the one page that answers "what has this
repository planned, and what still wants attention". It is read by
people picking up work and by tooling deciding what to surface, and
neither can read it if every repository shapes it differently -- three
layouts had grown across the fleet, so a reader had to work out which
one it was looking at before finding the status column.

Repositories with a `docs/plans/` directory must satisfy all of:

* **An index exists.** `docs/plans/index.md` is present whenever
  `docs/plans/` holds any plan.
* **Plans are listed in tables**, not as prose or a bullet list.
* **Every table leads with `Date` then `Plan`.** Later columns are the
  repository's own business; `Intent`, `Status` and `Phases` are the
  common ones. A `Status` column is optional -- a standalone plan
  listing that tracks no status is registered, just not tracked.
* **Dates are `YYYY-MM-DD`**, and rows run oldest first. Ordering is
  checked within each table, not across them, so a repository may keep
  separate master, standalone and consolidation tables.
* **Every master plan is listed.** A plan file the index never links is
  invisible: it was drafted and then forgotten. Phase plans are exempt
  -- they are named after their master plan and tracked inside it.
* **Status cells come from the shared vocabulary**, below.

Repositories with no `docs/plans/` directory are N/A. Whether every
project should plan this way is a separate decision, not smuggled in
here.

### The status vocabulary

A status cell holds exactly one of `Proposed`, `Not started`,
`In progress`, `Blocked`, `Complete`, `Abandoned` or `Superseded`, and
nothing else. Matching is case-insensitive, so `In Progress` passes,
but the canonical spelling is the one to write.

The "nothing else" is strict because that is the part that decayed:
status cells had grown into whole paragraphs carrying dates, phase
arithmetic and summaries of what happened. That is useful writing in
the wrong column. A status is read to decide whether a plan still wants
attention, and neither a person scanning the table nor a script can get
that out of a paragraph. The detail belongs in the plan file, and a
one-line summary in the index's own `Intent` column.

The vocabulary is a versioned shared block, `plan-status-vocabulary`,
which every `PLAN-TEMPLATE.md` must carry (see the `plan-template`
audit) so plans are written to it rather than corrected afterwards. It
governs the master plan's own Execution phase table as well as the
index row. A test in `scripts/test_audit_check.py` asserts the block
and the list the audit enforces name the same terms, so the wording
repositories are handed cannot drift from the wording they are measured
against.

### Overlap with the session hook, which is deliberate

An unregistered plan is also reported by the local SessionStart
plan-status hook. The two are not redundant: the hook is a per-session
nudge that disappears when the session ends, and the audit is what
turns a persistent gap into a tracked issue. Format, ordering and
vocabulary are the audit's alone -- the hook only reads the index, it
does not police it.

## Template

No template. `templates/shared-blocks/plan-status-vocabulary.md` holds
the canonical status vocabulary; copy it verbatim, markers included,
into the repository's `PLAN-TEMPLATE.md`.

To convert an index to the canonical layout, move the date to the first
column, sort each table oldest first, and move everything that is not a
vocabulary term out of the status column -- into `Intent` if it is a
one-line summary, into the plan file if it is longer. Add a row for any
plan the index does not list; consult the plan to work out its real
status rather than guessing from the file's existence.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | non-compliant | shakenfist/client-python#365 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#32 |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#506 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | shakenfist/library-utilities#42 |
| occystrap | non-compliant | shakenfist/occystrap#116 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | non-compliant | shakenfist/sfui#24 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **client-python** (Status): docs/plans/index.md is missing, so none of the 1 plan(s) in docs/plans/ are registered
- **client-python-k3s** (Status): docs/plans/index.md is missing, so none of the 2 plan(s) in docs/plans/ are registered
- **instar** (Status): 1 status cell(s) outside the shared vocabulary (Proposed, Not started, In progress, Blocked, Complete, Abandoned, Superseded): instar amend subcommand ("1.1 (qcow2 v2⇔v3 version transition, ...")
- **library-utilities** (Status): docs/plans/index.md is missing, so none of the 1 plan(s) in docs/plans/ are registered
- **occystrap** (Status): index has no plan table (it must list plans in a table led by Date and Plan columns, not as prose or a bullet list); 4 master plan(s) not listed in the index: PLAN-make-the-speed.md, PLAN-post-write-verification.md, PLAN-registry-proxy.md, PLAN-structured-logging.md
- **sfui** (Status): docs/plans/index.md is missing, so none of the 3 plan(s) in docs/plans/ are registered
<!-- consistency-audit:end -->
