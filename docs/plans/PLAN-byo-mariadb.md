# Bring-your-own MariaDB and `sf-database` as a tier

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the deploy machinery (`shakenfist/deploy/ansible/deploy.yml`,
`shakenfist/deploy/ansible/deploy.py`,
`shakenfist/deploy/ansible/roles/mariadb/`,
`shakenfist/deploy/ansible/roles/base/tasks/config.yml`,
`shakenfist/deploy/getsf`), the config bootstrap path
(`shakenfist/config.py`, in particular the
`MARIADB_HOST`-driven conditional and the
`DATABASE_NODE_IP` consumer plumbing), the database daemon
(`shakenfist/daemons/database/main.py`) and the SQL layer
it wraps (`shakenfist/mariadb.py` — especially
`ensure_schema()`, `ensure_data_migrations()`, and the
`TABLE_CREATION_LOCK` pattern), and the CI deployment
harness (`.github/workflows/`,
`shakenfist/deploy/shakenfist_ci/`,
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`). Ground
your answers in what the code actually does today rather than
guessing.

Where a question touches external concepts (gRPC client-
side load balancing policies, MariaDB / Galera clustering
behaviour, ansible-galaxy role discovery, mTLS interaction
with L4 vs client-side LBs), research as needed to give a
confident answer. Flag any uncertainty explicitly.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview and daemon structure. Consult `CLAUDE.md` for
build commands, project conventions, the MariaDB storage
pattern, the systemd service ordering, and the
"database daemon special case" around `MARIADB_HOST`.
Consult `PLAN-remove-primary.md` for the broader
sf-as-infra direction this plan sits inside; the MariaDB
and sf-database-tier elements that originally lived in
remove-primary phases 4-5 have been lifted out into this
plan because they are a big enough chunk of work to
justify their own master plan.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md`
extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit.

## Situation

Today the Shaken Fist deployer installs and operates the
MariaDB server itself. `roles/mariadb/tasks/bootstrap.yml`
(55 lines) runs only on the `etcd_master` ansible group;
it apt-installs `mariadb-server`, starts the service,
binds it to `node_mesh_ip`, drops in a tuning `.cnf`,
then issues `CREATE DATABASE shakenfist`, `CREATE USER`,
and `GRANT ALL ON shakenfist.*`. `deploy.py` generates a
24-character random password during the deploy and
threads it through to the `base` role's config template
so every SF daemon ends up with the same credential in
`/etc/sf/config`.

The deployer also bakes in a specific topology decision:
**MariaDB lives on `etcd_master[0]`, and so does the
sole `sf-database` daemon**. `config.py`'s bootstrap path
conflates "this node has MariaDB locally" with "this node
runs sf-database and therefore reads directly", via the
`MARIADB_HOST=localhost` direct-access hack documented in
`CLAUDE.md`. Every other daemon fetches cluster config via
gRPC from a single `DATABASE_NODE_IP`. There is no
provision for more than one sf-database instance; there
is no provision for MariaDB living anywhere else.

In practice, the operators who run Shaken Fist in
production already operate their own MariaDB clusters
(Galera, primary-replica, or managed services), already
operate their own load balancers, and increasingly want
SF to slot into an existing infrastructure rather than
prescribe one. The "Shaken Fist installs MariaDB for you"
path is doing work nobody asked for, and the
`etcd_master[0]` singleton for `sf-database` is a
needless SPOF.

`sf-database` itself is — pending two specific fixes,
described in Decisions — **stateless**. gRPC handlers
hold no per-client session state. The only background
loop refreshes a Prometheus gauge and does not mutate
data. Per-instance Prometheus counters and SQLAlchemy
table caches are fine under N>1. `record_start` /
`record_exit` write per-hostname rows that converge
under last-write-wins. There is no in-process truth
that another instance would also need to see.

The two exceptions worth calling out up front:

1. `_get_schema_versions_table()` at `mariadb.py:498-515`
   is the one table getter that does **not** wrap its
   SQLAlchemy `Table(...)` registration in
   `TABLE_CREATION_LOCK`. Two daemon processes calling
   `ensure_schema()` concurrently can race and hit
   SQLAlchemy's "Table already registered" exception.
   Every other table getter (`_get_object_states_table()`
   at `mariadb.py:565-589`, etc.) handles this correctly.
2. `ensure_data_migrations()` at `mariadb.py:2230` is
   explicit in its own docstring that it "assumes only
   one database daemon runs migrations at a time". Two
   concurrent starts will race on the per-table version
   bump in `_set_table_version()`, and although the
   migration *functions* are documented as idempotent,
   the version-pointer update is not atomic against a
   competitor.

These are the only two blockers to running N equal
`sf-database` instances against the same MariaDB cluster.

## Mission and problem statement

After this plan lands, the deployer no longer installs
MariaDB, and `sf-database` runs as a deployer-chosen
tier of equal instances rather than a singleton on
`etcd_master[0]`. Concretely:

- The `mariadb` ansible role is deleted. The deployer
  does not install, configure, or tune the MariaDB
  server. The bundled tuning `.cnf` is preserved as an
  *example* under `examples/mariadb-tuning.cnf` for
  operators who want to start from it.
- Operators bring their own MariaDB. Their cluster must
  satisfy a documented contract: a database named
  `shakenfist` (or operator's choice, exposed via
  config), a user with `ALL ON shakenfist.*`, reachable
  from every SF node on TCP 3306, supporting the
  `utf8mb4` character set. SF ships a single SQL snippet
  at `tools/bootstrap-mariadb.sql` that operators run
  against their cluster to apply the user/database/grant
  pieces; the snippet is idempotent (`CREATE ... IF NOT
  EXISTS`).
- A new `sf-ctl ensure-mariadb-schema` command becomes
  the **only** path through which SF tables are created
  or migrated. Operators run it once after creating the
  MariaDB user, and again whenever upgrading SF across a
  schema-changing release. `sf-database` no longer runs
  schema or migration on startup; it instead verifies
  that the recorded schema version matches its
  expectations and refuses to start if not. This
  sidesteps the migration-concurrency question
  completely: there is no concurrent migration because
  there is no per-daemon migration.
- `sf-database` runs as a deployer-chosen tier of N
  equal instances (N ≥ 1). All instances connect to the
  same MariaDB. None is "the leader"; all serve any
  inbound gRPC request. The schema-versions race in
  `_get_schema_versions_table()` is fixed defensively
  even though `ensure_schema()` no longer runs on
  startup, because future tier-internal table touches
  could still hit it.
- Every other SF daemon (`sf-api`, `sf-net`,
  `sf-cluster`, `sf-queues`, `sf-resources`,
  `sf-cleaner`, `sf-transfers`, `sf-privexec`) reaches
  the tier via a **client-side gRPC-native load-
  balanced channel** over a list of sf-database
  endpoints. Single-endpoint deploys are the
  degenerate case of the list. No L4 LB, VIP, or
  haproxy sits in front of the tier — that is a moving
  part the deployer would have to manage and could
  misconfigure, and an L4 LB also complicates the
  eventual mTLS work.
- Config naming changes to make the MariaDB requirement
  explicit and to reflect the tier-shaped reality.
  Open questions below capture the specific naming
  choice; the principle is "say MARIADB, not DATABASE,
  and use plurals where the underlying thing is a list."
- The `MARIADB_HOST=localhost` direct-access hack at
  config-bootstrap time is removed. Every daemon
  (including sf-database itself, for any non-bootstrap
  config it reads) goes through the gRPC tier. The
  bootstrap-time chicken-and-egg that the hack worked
  around is dissolved by `sf-ctl ensure-mariadb-schema`
  and `sf-ctl bootstrap-cluster` (from
  `PLAN-remove-primary` phase 2) running before any SF
  daemon starts.
- `getsf` is reworked. The interactive deployer prompts
  the operator for the MariaDB host(s), user, password,
  and database name. It no longer generates a password
  or installs a server. Operators who want a one-box
  evaluation run are documented to `apt install
  mariadb-server`, apply the SQL snippet, and then
  invoke getsf with the resulting credentials — but
  that path is **documented, not automated**.
- CI installs MariaDB in a workflow step that runs
  before getsf, then applies the SQL snippet, then
  invokes getsf with the resulting connection details.
  MariaDB install is no longer part of any role any
  daemon runs.
- CI proves N>1 sf-database works. The cluster CI gains
  a deployment shape in which two `sf-database`
  instances run against the same MariaDB, with daemon
  traffic load-balanced across them via the new
  client-side gRPC channel. Tests exercise the path
  end-to-end so a regression to "only one
  sf-database works" cannot land silently.

The principle is: **MariaDB is operator infrastructure
that SF connects to, not infrastructure SF installs.
`sf-database` is a stateless gRPC translation layer that
the deployer can run in any quantity they like.**

Per-caller authorisation on the sf-database gRPC surface
— restricting *what* a hypervisor can ask sf-database
for — is **out of scope** for this plan and is gated on
the mTLS work in `PLAN-embrace-tls.md` providing
trustworthy caller identity first. This plan establishes
the shape (a stateless tier of business-logic-bearing
gRPC endpoints) that the eventual authz work plugs into.

## Alternatives considered

### Pull in a community ansible role (e.g. `geerlingguy.mysql`)

A reasonable-sounding alternative is to keep the
deployer in the MariaDB-install business but delegate
the heavy lifting to a well-maintained community role
like `geerlingguy.mysql`, which supports MariaDB on
Debian/Ubuntu and exposes `mysql_databases` /
`mysql_users` variables that would replace the SF-
specific bootstrap almost line-for-line. We reject this
because the goal is **for SF to stop installing the data
tier at all**, not to install it via a different
mechanism. Pulling in a community role moves the problem
sideways: operators still inherit SF's opinion about how
MariaDB should be deployed, and we still have to decide
whether to use Galera, primary-replica, or single-node
inside the role. Documented prerequisites plus an
operator-applied SQL snippet is less code than any role
adoption and lets the operator's existing MariaDB
practice apply unmodified.

### Run a single `sf-database` with leader election

The original framing in `PLAN-remove-primary` phase 5 was
"elect `sf-database` like `sf-cluster`, with non-elected
candidates serving as discovery surface that refers
clients to the current leader." We reject this in favour
of a stateless tier because the only state inside
`sf-database` that would have justified election —
schema/migration execution at startup — is eliminated by
moving migration to an operator-run `sf-ctl ensure-
mariadb-schema` step. Without that, election introduces
a leader concept the daemon doesn't need and a redirect
mechanic that adds latency and a failure mode for no
benefit. The tier model is simpler in code, simpler for
operators, removes a moving part, and survives the
eventual mTLS work better (L4 LBs and TLS-terminating
fronts compose awkwardly with client-cert
authentication; client-side LB over mTLS channels does
not).

### L4 LB / VIP in front of the tier

A deployer-supplied L4 load balancer or VIP (haproxy,
keepalived, kube-svc, etc.) fronting the sf-database
tier would let clients use a single endpoint and let
the LB worry about which backend they hit. We reject
this in the BYO direction for the same reason the user
rejected it for the API tier: it adds a moving part
the deployer must manage and could misconfigure, and
it composes awkwardly with the mTLS direction. gRPC's
native client-side LB (round-robin policy over the
endpoint list, with health checks and the existing
keepalive machinery) covers the requirement without
introducing the LB.

## Decisions

1. **MariaDB is operator infrastructure.** The deployer
   does not install, cluster, or tune the MariaDB
   server. The current `roles/mariadb/` role is
   deleted, not demoted to opt-in. Single-box
   evaluators apt-install MariaDB themselves following
   a documented quickstart.

2. **Schema and migrations are operator-triggered.** A
   new `sf-ctl ensure-mariadb-schema` command becomes
   the only path through which SF schema is created
   or migrated. It is idempotent, takes a config-file-
   path argument so it can run without daemons being
   up, and is documented as a prerequisite for
   starting sf-database in BYO deployments. `sf-
   database` no longer runs `ensure_schema()` or
   `ensure_data_migrations()` on startup; it verifies
   the recorded schema version matches its
   expectations and refuses to start if not.
   `ensure-mariadb-schema` is also the only place where
   schema version is advanced; sf-database is purely a
   read-of-version-and-fail-fast consumer.

3. **`sf-database` is a stateless tier.** N ≥ 1 equal
   instances run against the same MariaDB. None is
   elected. All serve any inbound gRPC request. The
   per-instance Prometheus metrics surface is preserved
   (each instance scraped separately), per-instance
   table caches are preserved, per-hostname `record_*`
   rows are preserved.

4. **Schema-versions race is fixed defensively.**
   `_get_schema_versions_table()` at `mariadb.py:498-515`
   gains the same `TABLE_CREATION_LOCK` + double-check
   pattern used by `_get_object_states_table()` at
   `mariadb.py:565-589`. This is correct even though
   `ensure_schema()` no longer runs on startup, because
   `sf-ctl ensure-mariadb-schema` may run concurrently
   on more than one operator-driven control machine,
   and because future schema-touching code paths must
   not regress this property.

5. **Client-side gRPC native LB, not L4.** Every SF
   daemon's database channel becomes a gRPC channel
   constructed over a list of sf-database endpoints
   using the `round_robin` LB policy. The single-
   endpoint case is the degenerate list. gRPC's
   built-in subchannel health checking handles a
   failed instance; no external health check tier is
   required.

6. **No L4 LB or VIP in front of the tier.** Out of
   scope by design.

7. **Greenfields only; no migration story.** Consistent
   with the project's posture (see the
   `PLAN-eventlog-direct-mariadb.md` and
   `PLAN-remove-etcd.md` precedents and the
   `CLAUDE.md` note that the in-place upgrade path is
   being closed), this plan does not preserve
   compatibility with existing deployments. The sole
   operating cluster is rebuilt against the new shape.
   Release notes and `docs/operator_guide/database.md`
   call this out prominently.

8. **CI proves N>1 sf-database works.** The cluster CI
   gains a deployment shape with two `sf-database`
   instances against the same MariaDB. Functional
   tests exercise the LB path end-to-end. A regression
   to "only one sf-database works" must not be able
   to land silently.

9. **Authz on the gRPC surface is deferred to post-
   mTLS.** This plan establishes the tier shape so
   per-caller scoping has somewhere to plug in later.
   It does not ship per-caller scoping itself. See
   `PLAN-embrace-tls.md`.

10. **Scope is lifted out of `PLAN-remove-primary`.**
    The mariadb-install and sf-database-tier elements
    that lived in remove-primary phases 4-5 are moved
    into this plan. Phase 01 of this plan updates
    `PLAN-remove-primary.md` to remove the overlapping
    scope and cross-reference this plan as a
    dependency for the surrounding work
    (`bootstrap_operations` table, galaxy-role
    packaging, naming rename).

11. **Phase 0 absorbs `PLAN-remove-etcd`.** Inspection
    confirms the etcd *data* drain is complete (the
    `DATA_MIGRATIONS` dict at `mariadb.py:2209` is empty,
    the drain entries are gone), but the *machinery*
    is still in tree: `shakenfist/etcd.py`,
    `shakenfist/protos/etcd_pb2_grpc.py`, the
    `etcd3gw==2.6.0` dependency in `pyproject.toml`, the
    empty `DATA_MIGRATIONS` framework in `mariadb.py`,
    the two drain test files
    (`tests/test_cluster_config_drain.py`,
    `tests/test_etcd_ops_queues_drain.py`), the
    `is_etcd_master` Python attribute on `Node`, the
    `show-etcd-config` / `set-etcd-config` hidden aliases
    on `sf-ctl`, scattered code comments referring to
    retired `sf-ctl migrate-*` commands, the
    `.claude/skills/migrate-etcd-to-mariadb.md` skill,
    and the `ETCDCTL_API=3` line in the developer guide.
    Phase 0 deletes all of it as a single sweep, fully
    superseding `PLAN-remove-etcd.md`. The
    `etcd_master` *ansible group name* rename remains
    with `PLAN-remove-primary` phase 7 — that is a
    deploy-naming change in the deployer's natural
    surface area, not part of the Python-side etcd
    cleanup.

12. **Tier endpoint config name is `MARIADB_GATEWAY_HOSTS`.**
    The current singular `DATABASE_NODE_IP` becomes
    a plural list named `MARIADB_GATEWAY_HOSTS`.
    Reasoning: `MARIADB` (not `DATABASE`) reinforces
    that MariaDB is required infrastructure, not a
    generic SQL-lookalike; `GATEWAY` names the
    indirection explicitly (sf-database is the
    gateway in front of MariaDB) without
    understating its role the way `PROXY` would;
    plural matches the underlying-list shape. The
    existing `MARIADB_HOST` config (consumed only by
    sf-database itself for the direct MariaDB
    connection) is **not** renamed — keeping it as
    `MARIADB_HOST` avoids a larger rename and
    preserves the clean distinction "`MARIADB_HOST`
    is what sf-database uses to reach MariaDB;
    `MARIADB_GATEWAY_HOSTS` is what every other
    daemon uses to reach sf-database." Single-
    endpoint deployments set `MARIADB_GATEWAY_HOSTS`
    to a one-element list (or whatever the parser
    accepts for a singleton). Phase 02 owns the
    threaded rename across `config.py`, every
    daemon's consumer, every ansible template, every
    test, and every doc reference.

13. **`sf-ctl ensure-mariadb-schema` performs a hard
    compatibility check against the operator's
    MariaDB before doing any schema work.** Failed
    checks refuse to proceed with an explicit error;
    warnings are not used. The check covers: server
    version (against a pinned floor — the specific
    version is a phase 1 decision based on the JSON-
    column features SF actually depends on and the
    oldest version functionally tested in CI), the
    default storage engine (InnoDB), and the
    connection-default character set / collation
    (`utf8mb4` / a `utf8mb4_*` collation). Specific
    feature-flag introspection is deliberately *not*
    in scope — version-floor plus engine plus
    charset is enough to catch the realistic
    incompatibilities without becoming a maintenance
    burden. The same check runs at sf-database
    startup (cheap; one round-trip) so an operator
    swapping in a non-compliant MariaDB after
    `ensure-mariadb-schema` succeeded is still
    caught before the first JSON-column write fails
    at runtime. Documented in
    `docs/operator_guide/database.md` as part of
    the BYO prerequisites.

## Open questions

These are deliberate. Each one is small enough that the
relevant phase plan can resolve it without rewriting
this master plan.

1. **Health-check interaction with `PLAN-health-
   checks.md`.** gRPC's native client-side LB picks
   subchannel health up via the gRPC health protocol
   (`grpc.health.v1.Health`). The health-checks plan
   already calls out a phase 2 to land that protocol
   on sf-database. We should sequence so the tier
   work either lands on a sf-database that already
   speaks the health protocol, or lands a minimal
   server-side health implementation as part of this
   plan and lets the health-checks plan expand it
   later. Phase 03 resolves this.

2. **`shakenfist-utilities` home for the LB channel
   factory.** A "construct a gRPC channel over a
   candidate list with the right options" helper
   wants to live next to the other utility code. If
   `shakenfist-utilities`'s release cadence is
   awkward, the helper stays in-tree under
   `shakenfist/util/`. Phase 03 picks.

3. **MariaDB version floor.** Decision 13 commits to
   a hard-refuse compatibility check; the specific
   minimum version is a phase 1 decision. Inputs to
   the choice: which JSON-column features SF
   actually depends on today (`JSON_EXTRACT`,
   `JSON_OBJECT`, generated columns over JSON
   fields, etc. — a grep of `mariadb.py` will
   enumerate), what versions the cluster CI
   exercises against, and what's available on the
   oldest still-supported Debian / Ubuntu LTS.
   Candidates likely fall in the 10.5-10.11 range
   but pinning needs the inventory done first.
   Phase 01 picks.

4. **Whether `MARIADB_HOST` on a daemon is taken as
   evidence that the daemon should NOT use the gRPC
   tier (treating it as a "this daemon talks direct"
   marker), or whether the two configs are entirely
   orthogonal (a daemon may have both, and uses
   `MARIADB_HOST` only when it is `sf-database`).
   The current code conflates them; the cleanest
   end-state is the latter (orthogonal), but it has
   knock-on effects in `verify-config` and the
   systemd `ExecStartPre`. Phase 02 resolves this
   with a concrete refactor of `config.py`'s bootstrap
   conditional.

## Execution

The phases are ordered so CI remains green at every
landing. Phase 01 is preparation that does not change
operator-visible behaviour. Phases 02-03 reshape the
config and gRPC paths without yet changing how
operators deploy. Phases 04-05 are the operator-facing
cutover. Phase 06 proves the tier works under load.
Phase 07 documents the end state.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Retire etcd machinery and migration-era scaffolding (supersedes `PLAN-remove-etcd`; deletes `etcd.py`, `etcd3gw`, etcd protos, `DATA_MIGRATIONS` framework, drain tests, dead `sf-ctl` helpers and `show/set-etcd-config` aliases, stale `migrate-*` comments, `ETCDCTL_API` docs line, the `.claude/skills/migrate-etcd-to-mariadb.md` skill; `is_etcd_master` left for `PLAN-remove-primary` phase 7) | [PLAN-byo-mariadb-phase-00-retire-etcd.md](PLAN-byo-mariadb-phase-00-retire-etcd.md) | Complete |
| 1. PLAN-remove-primary scope lift; `_get_schema_versions_table()` lock fix; stop sf-database calling `ensure_schema()`/`ensure_data_migrations()` at startup; add a startup schema-version check that refuses to start on mismatch; add a hard MariaDB compatibility check (server version against a pinned floor, InnoDB engine, `utf8mb4` charset) to both `sf-ctl ensure-mariadb-schema` and sf-database startup | [PLAN-byo-mariadb-phase-01-statelessness.md](PLAN-byo-mariadb-phase-01-statelessness.md) | Complete |
| 2. Untangle `MARIADB_HOST` from "I am the database node"; rename `DATABASE_NODE_IP` → `MARIADB_GATEWAY_HOSTS` (plural list); rewrite the `config.py` bootstrap conditional | [PLAN-byo-mariadb-phase-02-config-untangle.md](PLAN-byo-mariadb-phase-02-config-untangle.md) | Complete |
| 3. Multi-endpoint client-side gRPC LB; minimal `grpc.health.v1.Health` server-side support on sf-database; channel factory helper | [PLAN-byo-mariadb-phase-03-grpc-tier.md](PLAN-byo-mariadb-phase-03-grpc-tier.md) | Complete |
| 4. `getsf` reworked to prompt for BYO connection details; `roles/mariadb/` deleted; `tools/bootstrap-mariadb.sql` shipped; `deploy.py` stops generating a password; tuning `.cnf` moved to `examples/` | [PLAN-byo-mariadb-phase-04-deploy-byo.md](PLAN-byo-mariadb-phase-04-deploy-byo.md) | Complete |
| 5. CI workflow step installs MariaDB and applies the SQL snippet before getsf runs; cluster_ci updated to drive the new path | [PLAN-byo-mariadb-phase-05-ci-byo.md](PLAN-byo-mariadb-phase-05-ci-byo.md) | Complete |
| 6. CI deployment shape with N=2 sf-database instances against the same MariaDB; functional test exercising the LB path; regression coverage | [PLAN-byo-mariadb-phase-06-ci-tier.md](PLAN-byo-mariadb-phase-06-ci-tier.md) | Complete |
| 7. Documentation: `docs/operator_guide/database.md` rewritten lead-with-BYO, `ARCHITECTURE.md` / `README.md` / `AGENTS.md` updates, release notes calling out the breaking change | [PLAN-byo-mariadb-phase-07-docs.md](PLAN-byo-mariadb-phase-07-docs.md) | Complete |

Sequencing constraints between phases:

- **Phase 00 lands first.** Pure deletion sweep; no
  behavioural change. Slims the surface area before
  later phases touch sf-ctl, `mariadb.py`, and the
  daemon-startup path. Independent of phases 1-7 in
  the sense that they could in principle land first,
  but the dependency graph is much easier to reason
  about against a post-etcd tree.
- **Phase 01 lands before any later phase.** Without
  the schema_versions race fix and the daemon-
  startup-check change, N>1 cannot work and the
  subsequent phases cannot be validated. The
  PLAN-remove-primary scope-lift edit also belongs
  here so the two plans don't contradict each other
  in review.
- **Phase 02 must land before phase 03.** The LB
  channel needs an endpoint-list config to consume.
- **Phase 03 must land before phase 04.** Operators
  who BYO MariaDB also need to be able to point at a
  list of sf-database instances; the deploy-facing
  rework in phase 04 assumes the LB plumbing is
  already there.
- **Phase 04 and 05 should land together** (or 05
  immediately after 04). The moment the bundled
  `roles/mariadb/` is deleted, CI must already have
  the workflow-step install in place, or CI goes
  red.
- **Phase 06 lands after 03 and 05.** It needs the
  LB plumbing (03) and the CI workflow-step install
  (05) to be in place.
- **Phase 07 can run in parallel with any other
  phase but must be re-checked at the end for
  accuracy.**

## Dependencies on other plans

- **`PLAN-remove-primary.md`** — sibling plan in the
  sf-as-infra direction. This plan lifts MariaDB-
  install and sf-database-tier scope out of remove-
  primary. Phase 01 of this plan edits remove-primary
  to remove the now-overlapping content and cross-
  reference here. After this plan lands, remove-
  primary's remaining scope is rsyslog removal, the
  bootstrap CLI, Apache LB removal, galaxy-role
  packaging, and the `etcd_master` → `database_node`
  rename. The galaxy-role packaging is still useful
  and is not absorbed into this plan.
- **`PLAN-health-checks.md`** — soft dependency.
  Phase 03 of this plan needs gRPC client-side LB
  health-checking to work, which in turn wants the
  `grpc.health.v1.Health` protocol available on
  sf-database. Phase 03 of this plan lands a
  minimal server implementation if health-checks
  has not landed first; if health-checks lands
  first, phase 03 just consumes it.
- **`PLAN-embrace-tls.md`** — downstream consumer.
  Per-caller authz on the sf-database gRPC surface
  becomes possible once mTLS provides trustworthy
  caller identity. The tier shape this plan
  establishes is what the eventual authz layer
  plugs into. No bidirectional dependency at
  ship-time, but cross-referenced in both docs.
- **`PLAN-remove-etcd.md`** — **superseded by phase 0
  of this plan.** Inspection confirmed the etcd data
  drain is already complete (the `DATA_MIGRATIONS`
  dict is empty) but the supporting machinery is
  still in tree; phase 0 deletes the machinery in a
  single sweep. The remove-etcd plan file is itself
  deleted as part of phase 0, and `index.md` and
  `order.yml` are updated accordingly.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session
(this conversation) is reserved for planning, review,
and decision-making. This keeps the management
context lean and avoids drowning it in implementation
diffs.

The workflow is:

1. **Plan** at high effort in the management session,
   producing the per-phase plan file before any code
   is written.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the phase plan, at the
   recommended effort level and model.
3. **Review** the sub-agent's output in the
   management session. Check the actual files — the
   sub-agent's summary describes what it intended,
   not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it)
   or the model was too light (upgrade it), then
   re-run.
5. **Commit** once the management session is
   satisfied with the result.

Use `isolation: "worktree"` for sub-agents on phases
01, 02, and 04 — those touch bootstrap correctness,
config plumbing, or operator-visible deploy state,
and benefit from a discardable worktree if the output
is unsatisfactory. Phases 03, 05, 06, and 07 can work
directly in the main tree unless the management
session has a reason to be cautious on a specific
step.

### Planning effort

This master plan is **high effort** — schema-version
correctness, config-bootstrap untangling, CI shape
changes, and the deploy-facing cutover all require
careful reasoning. Per-phase planning effort:

- Phase 00 (retire etcd machinery): **medium effort,
  sonnet**. Pure deletion sweep across a known list
  of files and references. Worth opus only for the
  `DATA_MIGRATIONS` framework removal from
  `mariadb.py` (the dict is empty but the framework
  has function signatures other code may still
  import); sonnet with a thorough brief should
  cover the rest.
- Phase 01 (statelessness + scope shift): **high
  effort, opus**. The schema-version race is small,
  but switching `sf-database`'s startup from
  "run the schema and migrations" to "verify the
  version and refuse to start on mismatch" has
  correctness implications for every future deploy.
- Phase 02 (config untangle + rename): **high
  effort, opus**. The `MARIADB_HOST`-driven
  bootstrap conditional is the riskiest config code
  in the daemon; getting it wrong breaks every
  startup. Naming choice is decided here and
  threaded through `config.py`, every daemon's
  consumer, every ansible template, and every doc.
- Phase 03 (gRPC LB + health protocol): **high
  effort, opus**. Channel-construction correctness
  is subtle (keepalive interaction, retry policy,
  resolver caching). The health-protocol decision
  about whether to fold in a minimal implementation
  here or wait for `PLAN-health-checks.md` is a
  scope call.
- Phase 04 (deploy BYO): **medium effort, opus**.
  Mostly deletions and reshaping, but the
  `getsf` rework changes the operator-facing surface
  and benefits from opus-class judgment on the
  prompts.
- Phase 05 (CI workflow step): **medium effort,
  sonnet**. Mechanical once the shape is decided.
- Phase 06 (CI tier coverage): **high effort,
  opus**. The cluster_ci shape change is small but
  the functional test design is load-bearing —
  this is the regression net for "the tier works".
- Phase 07 (docs): **medium effort, sonnet**.
  Mostly prose and index updates, with the final
  read of the code as a documentation-correctness
  check.

### Step-level guidance

Each phase plan should include a step table in the
same format as `PLAN-eventlog-direct-mariadb.md`,
with effort, model, isolation, and brief columns.
**When in doubt, skew to the more capable model** —
saving money only matters if the outcome is still
acceptable.

The brief is the load-bearing field. It should
front-load the research the planner already did
(file paths, line numbers, existing patterns to
mirror), so the implementing agent doesn't repeat
it. For example, instead of "fix the schema-version
race", write "wrap the `Table(...)` registration in
`_get_schema_versions_table()` (`mariadb.py:501-515`)
in `with TABLE_CREATION_LOCK:` with the same
double-check pattern used by
`_get_object_states_table()` at
`mariadb.py:565-589` — re-check `_schema_versions_table
is None` inside the lock before constructing."

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`,
plus:

- [ ] The `MARIADB_HOST=localhost` direct-access
      hack is removed from every code path, not just
      the headline bootstrap call.
- [ ] No daemon code path calls `ensure_schema()` or
      `ensure_data_migrations()` after this plan
      lands. The only caller is `sf-ctl
      ensure-mariadb-schema`.
- [ ] Every consumer of the old `DATABASE_NODE_IP`
      singular config is moved to the plural-list
      consumer (channel factory). Grep across the
      tree confirms no lingering callers.
- [ ] The deleted `roles/mariadb/` is genuinely
      gone (directory removed, references in
      `deploy.yml` and `deploy.py` removed),
      `tools/bootstrap-mariadb.sql` exists and is
      tested by phase 05's CI workflow step
      applying it.
- [ ] CI exercises both the single-endpoint and
      N>1 sf-database deployment shapes. The N>1
      shape exercises a daemon hitting different
      sf-database instances across consecutive
      requests (verified by metrics or log
      inspection in the functional test).
- [ ] `PLAN-remove-primary.md` is edited in phase
      01 to remove the now-overlapping scope and
      cross-reference this plan. `index.md` and
      `order.yml` are updated to reflect the new
      plan and the changed remove-primary scope.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will
be true:

* The code passes `pre-commit run --all-files`
  (flake8, stestr unit tests, and mypy type
  checking).
* `shakenfist/deploy/ansible/roles/mariadb/` no
  longer exists.
* `deploy.yml` does not include any task that
  installs, configures, or tunes MariaDB.
* `deploy.py` does not generate a MariaDB
  password; operators supply credentials via
  `getsf` prompts or topology JSON / inventory.
* `tools/bootstrap-mariadb.sql` exists, is
  idempotent, and is what operators apply against
  their MariaDB to create the SF database, user,
  and grants. CI applies this snippet as part of
  its workflow.
* `sf-ctl ensure-mariadb-schema` exists, is
  idempotent, and is the only path through which
  SF schema is created or migrated. Re-running it
  on an up-to-date schema is a no-op.
* `sf-ctl ensure-mariadb-schema` performs a hard
  MariaDB-compatibility check (server version
  against a pinned floor, InnoDB as the default
  engine, `utf8mb4` connection charset / a
  `utf8mb4_*` collation) before doing any schema
  work, and refuses to proceed with an explicit
  error on mismatch. The same check runs at
  sf-database startup.
* `sf-database` daemon does not call
  `ensure_schema()` or `ensure_data_migrations()`
  at startup; instead it verifies the recorded
  schema version matches its expectations and
  refuses to start if not, and verifies MariaDB
  compatibility before serving any request.
* `_get_schema_versions_table()` uses the
  `TABLE_CREATION_LOCK` pattern with double-
  check, matching the other table getters.
* Every SF daemon's database gRPC channel is a
  client-side load-balanced channel over the
  `MARIADB_GATEWAY_HOSTS` list of sf-database
  endpoints. Single-endpoint deployments work as
  the degenerate one-element-list case. The
  singular `DATABASE_NODE_IP` config no longer
  exists.
* The `MARIADB_HOST=localhost` direct-access
  bootstrap hack is removed from `config.py`. The
  bootstrap conditional treats "this daemon
  receives a MariaDB credential" and "this daemon
  serves the sf-database gRPC surface" as
  orthogonal facts.
* The cluster_ci deployment harness can stand up
  N=2 sf-database instances against a single
  MariaDB, and a functional test exercises the
  LB path end to end (a daemon hitting
  different sf-database instances across
  successive requests).
* `docs/operator_guide/database.md` leads with
  the BYO MariaDB story; `ARCHITECTURE.md`,
  `README.md`, and `AGENTS.md` are updated to
  reflect the new deployment shape; release
  notes call out that the deployer no longer
  installs MariaDB.
* `PLAN-remove-primary.md` no longer carries the
  MariaDB-install or sf-database-tier scope; it
  cross-references this plan. `docs/plans/
  index.md` reflects the change.
* `PLAN-remove-etcd.md` no longer exists in
  `docs/plans/`; its scope was absorbed into phase
  0 of this plan. `docs/plans/index.md`,
  `docs/plans/order.yml`, and any cross-references
  from other plans have been updated to reflect
  that phase 0 is the canonical retirement of the
  etcd machinery.
* `shakenfist/etcd.py`,
  `shakenfist/protos/etcd_pb2_grpc.py` (and any
  sibling etcd proto files), `etcd3gw` from
  `pyproject.toml`,
  `shakenfist/tests/test_cluster_config_drain.py`,
  `shakenfist/tests/test_etcd_ops_queues_drain.py`,
  the `DATA_MIGRATIONS` framework block in
  `shakenfist/mariadb.py`, the `is_etcd_master`
  Python attribute on `Node`, the
  `show-etcd-config` / `set-etcd-config` hidden
  `sf-ctl` aliases, the `MigrationStats` /
  `migration_precheck` / `migration_postcheck` /
  `parse_uuid` dead helpers in
  `shakenfist/client/ctl.py`, the corresponding
  test classes in `shakenfist/tests/test_ctl.py`,
  the `ETCDCTL_API=3` line in
  `docs/developer_guide/authentication.md`, the
  etcd block in `shakenfist/config.py`, the etcd
  notes in `CLAUDE.md`, the stale `sf-ctl migrate-*`
  comments in `blob.py`, `upload.py`, `artifact.py`,
  `node.py`, `namespace.py`, `network/network.py`,
  and `constants.py`, and the
  `.claude/skills/migrate-etcd-to-mariadb.md`
  skill are all gone. (The `etcd_master` ansible
  group name remains for `PLAN-remove-primary`
  phase 7 to rename in deploy scope.)

### Future work

- **Per-caller authorisation on the sf-database
  gRPC surface.** Restricting *what* a hypervisor
  daemon can ask sf-database for (e.g. it may
  read its own instance attributes but not write
  to a peer's `node_daemon_states`). Requires
  trustworthy caller identity, which mTLS provides.
  Tracked under `PLAN-embrace-tls.md` as the
  natural follow-on.
- **In-memory caches on sf-database for stable
  fields.** Object identities and the contents of
  primary object tables (uuid, name, namespace,
  type, etc.) are constants for the lifetime of
  the object — that is why they live on the
  static table rather than `*_attributes`. A
  read-through cache keyed on object uuid could
  return those fields without a SQL round-trip,
  invalidated only on explicit delete. Each
  sf-database instance can have its own such
  cache without cross-instance coordination,
  because the invariant is "this never changes".
  Worth a dedicated plan once the tier shape is
  proven; the gain is per-request latency on the
  hottest paths.
- **Per-namespace MariaDB connection pools.**
  For multi-tenant deployments with very
  different access patterns per namespace, a
  per-namespace pool would let one tenant's
  long-running query not starve another's. Out
  of scope; revisit if measured contention
  justifies it.
- **Operator-facing health and capacity
  metrics.** sf-database already exposes per-
  instance Prometheus metrics. A documented
  "what to alert on" page for the tier model
  (per-instance error rates, channel queue
  depth, lag between instances if any) helps
  operators run the tier confidently. Folds into
  the OpenTelemetry direction once that lands.

### Bugs fixed during this work

This section should list any bugs we encounter
during development that we fixed.

### Documentation index maintenance

When creating this master plan from the template,
update the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master
  plans* table for this master plan, keyed to a
  one-line intent and its current status. Phases
  are tracked in this plan's own Execution table,
  not in the index. Also update the *Plan
  sequencing* prose to reflect that this plan
  now carries the MariaDB scope and that remove-
  primary's scope has shrunk accordingly.
* **`order.yml`** — add an entry for this master
  plan. Phase files are *not* added to
  `order.yml`; they are linked from the
  Execution table and from `index.md` only.

The site navigation in `mkdocs.yml` is produced
from `mkdocs.yml.tmpl` by the docs-sync workflow,
which consumes `order.yml`. No manual
`mkdocs.yml` edits are needed.

When all phases are complete, update the status
column in `docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please
back brief the operator as to your understanding
of the plan and how the work you intend to do
aligns with that plan.
