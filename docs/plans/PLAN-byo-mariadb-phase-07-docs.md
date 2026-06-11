# Phase 7 — Documentation

Parent plan:
[PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).
Predecessors:
[Phase 0](PLAN-byo-mariadb-phase-00-retire-etcd.md),
[Phase 1](PLAN-byo-mariadb-phase-01-statelessness.md),
[Phase 2](PLAN-byo-mariadb-phase-02-config-untangle.md),
[Phase 3](PLAN-byo-mariadb-phase-03-grpc-tier.md),
[Phase 4](PLAN-byo-mariadb-phase-04-deploy-byo.md),
[Phase 5](PLAN-byo-mariadb-phase-05-ci-byo.md),
[Phase 6](PLAN-byo-mariadb-phase-06-ci-tier.md).

## Scope

Phase 7 brings the project documentation in line with
the post-phase-6 code. After this phase ships:

- `docs/operator_guide/database.md` leads with the
  bring-your-own MariaDB story rather than retrofitting
  it as a subsection halfway down the page. The
  historical "## etcd" top-level section and the
  per-object-type "Migration from etcd" subsections
  are deleted; etcd left the tree in phase 0 and is no
  longer load-bearing context for an operator reading
  this page. The page's two
  `SHAKENFIST_MARIADB_HOST=localhost` bootstrap
  examples (the `sf-ctl initialise-node` and
  `sf-ctl register-daemon` snippets) are corrected —
  that direct-access hack was dissolved by phase 2 and
  phase 4, and the cleaner bootstrap shape is "run
  from a database-tier node where `/etc/sf/config`
  already has `MARIADB_HOST` set."
- The "Data Migrations" subsection under
  Administrative Commands is deleted (the
  `DATA_MIGRATIONS` framework left the tree in
  phase 0).
- The "etcd to MariaDB Migration Strategy" section
  is reframed to "MariaDB Table Inventory" or
  similar — the migration is past tense, but the
  per-object-type table inventory it documents is
  still useful reference content for developers.
  The migration-phases status table is dropped
  (every row says "Complete - <table>" so the
  signal is just "tables exist", which the
  inventory itself already says).
- The post-phase-6 multi-instance subsection
  under "MARIADB_HOST vs MARIADB_GATEWAY_HOSTS"
  is preserved as-is (it landed in phase 6 step 6
  and reads cleanly into the surrounding context).
- `ARCHITECTURE.md` notes the tier model: the
  Database Layer diagram already shows
  `sf-database` as a single box; a short paragraph
  below it clarifies that N≥1 instances can run
  against the same MariaDB and clients reach the
  tier through `MARIADB_GATEWAY_HOSTS`. The
  Daemons table's `sf-database` row gets a "runs
  on database-tier nodes" qualifier.
- `README.md` drops the
  `migrate-etcd-to-mariadb` skill bullet — that
  skill was deleted in phase 0. The existing
  `Prerequisites` section is verified accurate
  against the post-phase-6 reality (MariaDB
  10.6.0+, bootstrap snippet, getsf prompts).
- `AGENTS.md` is read end-to-end and any present-
  tense reference to deleted machinery is
  corrected. The survey expects no edits
  (`AGENTS.md` is CI- and merge-queue-focused
  prose with no database-layer content) but the
  step's brief includes the read so the
  expectation is recorded.
- `docs/release_notes/v07-v08.md` gains a new
  section, **Bring your own MariaDB**, that calls
  out the breaking changes for operators: the
  deployer no longer installs MariaDB; `getsf`
  prompts for connection details; `sf-ctl
  ensure-mariadb-schema` is the only path through
  which SF schema is created or migrated;
  `MARIADB_GATEWAY_HOSTS` (plural list) replaces
  `DATABASE_NODE_IP` (singular); the
  `roles/mariadb/` ansible role and a list of
  `sf-ctl migrate-*` commands are deleted;
  sf-database is a tier of N≥1 instances; rolling
  upgrades of the tier work via the gRPC
  health-protocol-aware client-side LB. The
  earlier-in-the-cycle `## Database` section is
  not rewritten — release notes are a historical
  record of the cycle, and the new section
  supersedes the earlier framing without erasing
  it.
- `docs/plans/index.md` marks phase 7 of the
  byo-mariadb plan complete. The master plan's
  Execution table marks phase 7 complete and the
  master plan itself is marked Complete in the
  index's left column status (the row that points
  at `PLAN-byo-mariadb.md`).

## Out of scope

- Docs changes in *other* in-flight plan files
  that reference the now-retired etcd machinery or
  the `roles/mariadb/` role
  (`PLAN-remove-primary.md`,
  `PLAN-embrace-tls.md`, `PLAN-health-checks.md`).
  Those plans reference the machinery as part of
  their own design discussion; updating them
  belongs to the next iteration of those plans,
  not here. The `is_etcd_master` Python-attribute
  and `etcd_master` ansible-group renames are
  PLAN-remove-primary phase 7's scope.
- A rewrite of the historical earlier-cycle
  `## Database` section in `v07-v08.md`. Release
  notes are a record of what changed; the new
  "Bring your own MariaDB" section supersedes the
  earlier framing without rewriting it.
- A pass on `docs/components/` or
  `docs/developer_guide/` looking for stray
  references. Phase 7 leans on a final grep
  sweep at the end of step 7a; anything that
  surfaces in present tense gets fixed there,
  but per-page rewrites of developer- or
  component-level docs are out of scope.
- New content (a "deploying a database tier"
  how-to, a metrics-dashboard reference page,
  etc.). Phase 7 covers the operator and
  architecture surface; net-new operator
  documentation can be its own follow-up if
  warranted.

## Where the release notes go

The codebase uses per-version release-notes files
at `docs/release_notes/v{N-1}-v{N}.md`. The
current development version's file is
`v07-v08.md`. Phase 7 appends a section to that
file rather than creating a new one. The format
mirrors the existing sections — a heading, a short
prose paragraph describing the operator-visible
change, then a bullet list of concrete steps the
operator needs to take or notice.

Phase 7's section covers:

- The deployer no longer installs MariaDB. The
  `roles/mariadb/` ansible role is deleted.
  Operators provide MariaDB themselves.
- `tools/bootstrap-mariadb.sql` ships in the
  source tree and is what operators apply against
  their MariaDB to create the `shakenfist`
  database, user, and grants.
- `examples/mariadb-tuning.cnf` ships as an
  optional drop-in with starting-point InnoDB
  and connection-pool settings.
- `getsf` prompts for `GETSF_MARIADB_HOST`,
  `GETSF_MARIADB_PORT`, `GETSF_MARIADB_USER`,
  `GETSF_MARIADB_PASSWORD`, `GETSF_MARIADB_DATABASE`
  (or accepts them as env vars for non-
  interactive runs). The deployer does not
  generate the password any more — operators
  choose it when they apply the bootstrap SQL.
- `sf-ctl ensure-mariadb-schema` is the only
  path through which SF schema is created or
  migrated. `sf-database` no longer runs
  `ensure_schema()` or `ensure_data_migrations()`
  on startup; it verifies the recorded schema
  version matches its expectations and refuses
  to start on mismatch.
- The MariaDB compatibility check (server is
  MariaDB not MySQL, version 10.6.0+, default
  storage engine InnoDB, connection charset
  `utf8mb4`, collation `utf8mb4_*`) runs at both
  `sf-ctl ensure-mariadb-schema` and
  `sf-database` startup. Incompatible servers
  surface as a clear refusal-to-start with a
  multi-line error, not as a runtime failure
  on the first JSON-column write.
- `MARIADB_GATEWAY_HOSTS` (a comma-separated
  list of `sf-database` endpoints) replaces the
  singular `DATABASE_NODE_IP`. Single-instance
  deployments degenerate to a one-element
  list. `MARIADB_GATEWAY_PORT` and
  `MARIADB_GATEWAY_METRICS_PORT` are the gRPC
  and Prometheus ports each `sf-database`
  binds on.
- `MARIADB_HOST` is only set on database-tier
  nodes (those running `sf-database`) and on
  any node where an operator manually runs
  `sf-ctl ensure-mariadb-schema`. The previous
  `MARIADB_HOST=localhost` direct-access hack
  at config-bootstrap time is gone.
- `sf-database` is a tier of N≥1 instances.
  All instances connect to the same MariaDB.
  None is elected; all serve any inbound gRPC
  request. Every other SF daemon reaches the
  tier through a client-side load-balanced
  gRPC channel that round-robins requests
  across endpoints. Unhealthy endpoints are
  skipped via the gRPC health protocol
  (`grpc.health.v1.Health`) — no external L4
  LB needed. Rolling upgrades of the tier
  work cleanly because each `sf-database`
  flips its health status to `NOT_SERVING`
  before stopping.
- N>1 sf-database deployments are exercised
  by CI on every merge-queue run via the
  `slim-tier` topology, so operators can
  rely on the multi-instance shape as a
  supported production configuration.
- The following `sf-ctl migrate-*` commands
  have been deleted (along with their
  helpers): `migrate-state-to-mariadb`,
  `migrate-floating-network-uuid`, and any
  other `sf-ctl migrate-*` that was tied to
  the etcd era. Operators on the new shape
  do not run any migration command.
- The `migrate-etcd-to-mariadb` Claude Code
  skill in `.claude/skills/` has been
  removed (it is no longer reachable code).
- **Greenfields only.** This plan does not
  preserve compatibility with deployments
  that took the earlier-in-the-v0.8-cycle
  shape (deployer-installed MariaDB,
  `DATABASE_NODE_IP` singular, etc.).
  Operators rebuild against the new shape.

## database.md restructure

`docs/operator_guide/database.md` is the load-
bearing operator-facing reference for the database
layer. The restructure is structural, not just
prose-level cleanup. Concretely:

### Sections to delete

- **`## etcd` (lines 19-69 of the pre-phase-7
  file).** The top-level section's content is
  entirely about etcd as an active or transitional
  data store. Phase 0 retired the machinery; the
  section is no longer useful operator context.
  Delete the whole `## etcd` section including
  the "Historical Key Structure" and "Object
  Types (historical etcd paths)" subsections.

- **"### Migration from etcd" subsections.**
  Each of the per-object-type sections
  (Object State Storage, IPAM Reservation
  Storage, possibly others) carries a
  "Migration from etcd" subsection describing
  the on-startup drain. Phase 0 removed the
  drain. Delete every "### Migration from
  etcd" subsection.

- **"### Data Migrations" under Administrative
  Commands (around line 669).** Describes the
  `DATA_MIGRATIONS` framework as if it still
  runs at sf-database startup. The framework
  was deleted in phase 0. Delete the
  subsection.

- **The migration-phases status table in
  "etcd to MariaDB Migration Strategy"
  (around lines 752-773).** Every row reads
  "Complete - <table>". The signal "tables
  exist" is already provided by the per-
  table content below it. Delete the table.

### Sections to correct

- **`sf-ctl initialise-node` example
  (around line 640).** The current example
  prefixes the command with
  `SHAKENFIST_MARIADB_HOST=localhost \`.
  That hack was dissolved by phases 2 and 4 —
  the bootstrap path now expects `/etc/sf/config`
  on a database-tier node to already carry
  `MARIADB_HOST`. Rewrite the example to
  drop the env-var prefix and the
  "useful during deployment when the
  database service isn't running yet" framing,
  which still hand-waves at the chicken-and-egg
  the hack was working around.

- **`sf-ctl register-daemon` example (around
  line 660).** Same edit: drop the
  `SHAKENFIST_MARIADB_HOST=localhost` prefix.

- **"### Why Migrate?" subsection (around line
  734).** The "etcd for simple key-value
  lookups" framing is dated. Either drop the
  section entirely (the migration is past
  tense and operators do not need the
  why-we-moved rationale) or recast it as a
  paragraph paragraph-level note that
  introduces the inventory below.

### Sections to keep, in changed order

The page should lead with the BYO setup story —
that is what a new operator needs first —
followed by the architecture explanation. The
current `## Bring-your-own MariaDB setup`
section (lines 470-525 of the pre-phase-7
file) gets moved up; the `## Overview`,
`## MariaDB` and `## MariaDB compatibility
requirements` sections collapse into a single
"## Why MariaDB and what it stores" section
that summarises requirements at the top and
defers the table inventory to later.

The exact new section order should read:

1. Bring-your-own MariaDB setup (lead)
2. MariaDB compatibility requirements
3. Why MariaDB and what it stores (short
   prose summary)
4. MARIADB_HOST vs MARIADB_GATEWAY_HOSTS
   (already updated in phase 6 step 6;
   preserved)
5. Administrative Commands (with the
   corrected examples and the "Data
   Migrations" subsection deleted)
6. MariaDB Table Inventory (reframed from
   the old "etcd to MariaDB Migration
   Strategy"; covers shared tables,
   high-churn dedicated tables, per-type
   static-value tables, per-type
   attribute tables, etc. — the
   existing developer-reference content
   below the section heading is preserved
   in place)
7. Schema System (Pydantic source, SQLAlchemy
   generation, type mapping, index annotations,
   table lifecycle — preserved)
8. Best Practices (Schema Evolution, Rolling
   Deployments, Performance Considerations
   — with the "etcd for simple key-value
   lookups" line corrected)

The page is long (~1062 lines) and rearranging
prose-heavy sections is the riskiest part of
this phase. The implementing agent must read the
*whole* file before making structural cuts, and
must run `mkdocs build` (or fall back to
pre-commit's docs hooks if mkdocs is not
installed locally) to confirm the rendered page
still works.

## ARCHITECTURE.md updates

Three concrete edits:

- **Daemon table (~line 19).** The
  `sf-database | Database microservice (MariaDB
  access) | 13005` row gets a "runs on
  database-tier nodes" qualifier in the Purpose
  column so the reader knows it does not run
  everywhere.
- **Database Layer diagram (~lines 30-68).** The
  diagram shows `sf-database` as a single box.
  Either replace the box with a stacked-boxes
  representation showing N>=1 instances, or
  (less invasive) add a one-paragraph note
  immediately below the diagram saying "The
  `sf-database` box is a tier of N>=1 instances;
  clients reach the tier through a client-side
  load-balanced gRPC channel over the
  `MARIADB_GATEWAY_HOSTS` list."
- **MariaDB schema box content (~lines 46-66).**
  The bracketed list of tables is accurate
  post-phase-6; verify it still matches and
  add `node_daemon_states` and `cluster_locks`
  if they are not already listed. (The eventlog
  phase already added `events` and
  `event_objects`; do not duplicate.)

## README.md updates

Two edits:

- **Drop the `migrate-etcd-to-mariadb` skill
  bullet.** The skill file was deleted in
  phase 0; the bullet describing it is stale
  and confusing to a contributor scanning the
  README.
- **Verify the Prerequisites section against
  reality.** It currently says "Shaken Fist
  requires an operator-provided MariaDB
  10.6.0+ server. ... See
  `docs/operator_guide/database.md` for the
  complete setup workflow." That sentence is
  accurate post-phase-7 if the database.md
  restructure leads with BYO setup. The
  internal link should resolve to the new
  lead section, not to an anchor that moved
  during the restructure. If the lead section
  is named `## Bring-your-own MariaDB setup`,
  the link `docs/operator_guide/database.md`
  by default lands at the top of the page,
  which is correct. No anchor link needed.

## AGENTS.md updates

Survey-only. `AGENTS.md` is CI- and
merge-queue-focused prose with no
database-layer content. The brief includes
a read end-to-end with cleanup of any stale
reference to deleted machinery (etcd, the
mariadb role, etc.). Expectation: no edits.

## Stale-reference cleanup

The implementing agent runs the following grep
at the end of step 7a after the per-file edits:

```bash
grep -ri '\\bDATA_MIGRATIONS\\|migrate-etcd-to-mariadb\\|migrate-state-to-mariadb\\|migrate-floating-network-uuid\\|roles/mariadb\\|DATABASE_NODE_IP\\|MARIADB_HOST=localhost\\|ensure_data_migrations' docs/ README.md ARCHITECTURE.md AGENTS.md CLAUDE.md
```

Any match in present tense gets a one-line
fix in the same commit. Matches inside
plan files (`docs/plans/PLAN-*.md`) are
deliberately retained — those plans are a
historical record and rewriting them in
this phase would corrupt the audit trail.

A second sweep for the `## etcd` content:

```bash
grep -rn '^## etcd\\|^### etcd\\|^# etcd' docs/operator_guide/
```

Should return only one result (the section in
`database.md` that this phase deletes) — if a
sibling page has a similar section, it gets
called out for review.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | worktree | `docs/operator_guide/database.md` restructure per the "database.md restructure" section of this phase plan. Delete the `## etcd` top-level section, the per-object-type `### Migration from etcd` subsections, the `### Data Migrations` subsection under Administrative Commands, and the migration-phases status table inside the "etcd to MariaDB Migration Strategy" section (now reframed as "MariaDB Table Inventory"). Correct the two `SHAKENFIST_MARIADB_HOST=localhost` bootstrap examples. Reorder sections so the page leads with BYO setup; collapse the Overview/MariaDB/MariaDB-Required prologue into a short "Why MariaDB and what it stores" section. Preserve the post-phase-6 multi-instance subsection under "MARIADB_HOST vs MARIADB_GATEWAY_HOSTS". Read the entire file before making structural cuts. Run `mkdocs build` from the repo root if mkdocs is available — falling back to `pre-commit run --all-files` if not (the docs hooks in pre-commit do not currently render mkdocs, but the YAML/markdown linters in the chain catch broken markdown). One commit, message subject: `docs: lead with BYO in operator_guide/database.md.` Worktree isolation because the file is large and the structural rearrangement is the riskiest piece of this phase. |
| 7b | medium | sonnet | none | Sweep `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` per the corresponding subsections of this phase plan. Drop the `migrate-etcd-to-mariadb` skill bullet from README. Add the tier-model qualifier to the `sf-database` row of the ARCHITECTURE Daemons table and the post-diagram paragraph about the tier shape. Verify the MariaDB schema box in ARCHITECTURE lists the current tables. Run the stale-reference grep at the end (per the "Stale-reference cleanup" section); fix any present-tense match in `docs/`, `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, or `CLAUDE.md`. Do NOT touch `docs/plans/PLAN-*.md` files except `PLAN-byo-mariadb.md` (which is the master plan). `pre-commit run --all-files`. One commit, message subject: `docs: ARCHITECTURE, README, AGENTS sweep for BYO MariaDB.` |
| 7c | medium | sonnet | none | Append the **Bring your own MariaDB** section to `docs/release_notes/v07-v08.md` per the "Where the release notes go" section of this phase plan. Cover every bullet listed there: deployer-no-longer-installs, bootstrap SQL ships, tuning .cnf example, getsf prompts, ensure-mariadb-schema as the only path, compat check, MARIADB_GATEWAY_HOSTS, MARIADB_HOST scope, sf-database tier model, CI coverage of N>1, migrate-* command deletions, skill deletion, greenfields-only posture. Read existing sections to match the file's prose voice (declarative, operator-facing, no marketing language). Place the new section AFTER the existing `## Database` section and BEFORE `## Event logging migrated to MariaDB` so the database-related changes are grouped. Do not rewrite or edit existing sections — release notes are a historical record. `pre-commit run --all-files`. One commit, message subject: `release notes: bring-your-own MariaDB cutover.` |
| 7d | low | sonnet | none | Mark phase 7 complete in `docs/plans/PLAN-byo-mariadb.md` (Execution table) and `docs/plans/index.md` (the matching row). Also update the master-plan row in `docs/plans/index.md` itself to show the plan as Complete in its left-column status. Pattern-match on prior "mark phase N complete" commits in this branch (e.g. 35dce8e93, d55bd7c45) for the commit message structure. `pre-commit run --all-files`. One commit, message subject: `plans: mark PLAN-byo-mariadb phase 7 and the master plan complete.` |

Ordering: 7a → 7b → 7c → 7d. Each step is
independent in content but committing in order
keeps the narrative clean (the operator guide
reflects the new shape, then the architecture/
README/AGENTS sweep brings the rest of the
docs in line, then the release notes announce
it, then the plan-status table is marked
complete).

## Risks and mitigations

- **Risk:** Restructuring `database.md` breaks
  inbound links from other docs or external
  references.
  **Mitigation:** Anchor-link search before
  cuts. The grep `grep -rn 'database.md#' docs/`
  enumerates known intra-doc anchors; the
  brief tells the implementing agent to
  preserve those anchors where possible
  (Markdown anchors are derived from heading
  text, so a heading rename breaks the
  anchor). README and ARCHITECTURE links to
  `database.md` without anchors are preserved
  by the lead-section change.

- **Risk:** The release-notes section is
  written for the wrong version's file.
  **Mitigation:** Confirm the in-development
  file is `v07-v08.md` by reading the mkdocs
  nav block before editing. If the active
  version has rolled over since this plan was
  written (unlikely on this timeline), use the
  new file.

- **Risk:** A stale reference is missed
  during the grep sweep because of a
  non-obvious phrasing ("the database
  microservice node", "the etcd master",
  etc.).
  **Mitigation:** Acceptable — phase 7 is
  not a guarantee that no reference
  survives, it is a best-effort cleanup. If
  a reference is found post-phase-7 it can
  be fixed in a one-line follow-up commit.

- **Risk:** Marking the master plan
  Complete in `docs/plans/index.md` before
  this branch merges is technically wrong
  (the plan ships when the branch merges to
  develop, not when the commits land on the
  branch).
  **Mitigation:** The status reflects the
  plan's implementation status. When the
  branch merges to develop, the rows are
  already marked Complete and no follow-up
  is needed. If for some reason the branch
  never merges, this is an obvious anomaly
  that would be caught at PR review.

- **Risk:** The implementing agent
  rewrites the historical earlier-cycle
  `## Database` section in `v07-v08.md`
  rather than appending a new section.
  **Mitigation:** Step 7c's brief is
  explicit: "Do not rewrite or edit
  existing sections — release notes are a
  historical record."

## Definition of done

- [ ] `docs/operator_guide/database.md` leads
      with BYO MariaDB setup.
- [ ] The `## etcd` top-level section, the
      `### Migration from etcd` subsections,
      and the `### Data Migrations`
      subsection are deleted.
- [ ] The two `SHAKENFIST_MARIADB_HOST=localhost`
      bootstrap examples are corrected.
- [ ] `ARCHITECTURE.md` notes the tier model
      and updates the `sf-database` daemon-
      table row.
- [ ] `README.md` no longer references the
      `migrate-etcd-to-mariadb` skill.
- [ ] `AGENTS.md` has no present-tense
      reference to deleted machinery.
- [ ] `docs/release_notes/v07-v08.md`
      contains the **Bring your own MariaDB**
      section.
- [ ] `docs/plans/PLAN-byo-mariadb.md` and
      `docs/plans/index.md` show phase 7 and
      the master plan as Complete.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each commit is self-contained; commit
      messages follow project conventions
      including the Prompt paragraph and
      Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the
implementing sub-agent should back-brief the
management session on its understanding of the
brief and the surrounding context — in particular
the restructure shape of `database.md` (which
sections move, which delete, which stay).
