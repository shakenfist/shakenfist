# Phase 2: Config untangle and tier-endpoint rename

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these files so you understand the
current shape of the config-bootstrap conditional and
the daemon-startup code paths you will reshape:

- `shakenfist/config.py` lines 18-99
  (`load_cluster_config()` — the per-process bootstrap
  that populates `os.environ['SHAKENFIST_*']` from the
  cluster_config table; branches on `MARIADB_HOST` for
  direct access vs `DATABASE_NODE_IP` for gRPC).
- `shakenfist/config.py` lines 329-340 (the three
  `DATABASE_*` Field definitions that will be renamed)
  and lines 489-520 (the four `MARIADB_*` Field
  definitions for the actual MariaDB connection,
  unchanged in this phase).
- `shakenfist/config.py` `verify_config()` near line
  531 (called by the systemd `ExecStartPre` and by
  `sf-ctl verify-config`; emits errors if the new
  shape lands wrong).
- `shakenfist/mariadb.py` lines 291-329
  (`_use_database_service()` and
  `_get_database_stub()` — the runtime routing logic).
- `shakenfist/daemons/database/main.py` lines 5070-
  5215 (the daemon's startup, metrics-port bind, and
  gRPC-server bind; the gRPC bind currently reuses
  `DATABASE_NODE_IP` which is the client-facing
  config — that conflation has to break first).
- `shakenfist/deploy/ansible/deploy.yml` (the
  `database_node_ip` Ansible var and the `etcd_master`
  group inference) and
  `shakenfist/deploy/ansible/roles/primary/defaults/main.yml`
  (the loopback default).
- The two `roles/primary/tasks/cluster_config.yml` and
  `roles/base/tasks/register.yml` files for the
  `SHAKENFIST_MARIADB_HOST=localhost` ExecStartPre
  pattern.

This phase does not introduce client-side gRPC load
balancing — that is phase 3. It does change the type
of the tier-endpoint config from `str` to `list[str]`
so phase 3 can land the LB without further config
churn, but the phase-2 consumers simply use the first
entry. It also does not touch deploy.py / getsf — that
is phase 4.

One commit per step at minimum. Each commit must build
and `pre-commit run --all-files` must pass.

## Context

Today, the SF config layer expresses two distinct
ideas with one piece of state:

1. **Where the sf-database gRPC service lives.**
   Clients (every daemon that isn't `sf-database`)
   need an endpoint to connect to.
2. **What this process *is*.** `MARIADB_HOST` is set
   on the node running `sf-database`; the absence of
   it is treated as evidence that "you are a client,
   use the gRPC path."

The conflation has two operator-facing consequences:

- `daemons/database/main.py:5196,5209` reuses
  `DATABASE_NODE_IP` as its own gRPC-server bind
  address. A future tier of N sf-database instances
  cannot all bind on the *same* `DATABASE_NODE_IP`,
  because by definition each instance needs its own
  address.
- The docstrings around `load_cluster_config()` and
  `_use_database_service()` describe the
  `MARIADB_HOST`-vs-`DATABASE_NODE_IP` choice as
  "I am the database node" vs "I am a client", which
  is exactly the singleton-shaped framing the master
  plan is trying to retire. After phase 2, the
  framing is: `MARIADB_HOST` = "this process has
  direct MariaDB access available, prefer it" (true
  for `sf-database` and `sf-ctl ensure-mariadb-
  schema`); `MARIADB_GATEWAY_HOSTS` = "list of
  sf-database endpoints to reach when direct access
  isn't available." Each daemon makes its routing
  decision per-call from these two orthogonal
  facts.

The master plan's decision 12 commits to the
`MARIADB_GATEWAY_HOSTS` naming and the plural-list
type. Open question 4 (whether `MARIADB_HOST`'s
presence is an "I prefer direct" marker or whether
the two configs are entirely orthogonal) resolves
in favour of preference: `MARIADB_HOST` set means
"use direct access" because that avoids a hop through
the tier for the one daemon (`sf-database` itself)
that legitimately has direct access. This is
*sufficiently* orthogonal — there is no `USE_GATEWAY`
override flag; the daemon code asks "do I have
direct access available?" first and falls back to
the tier otherwise.

After this phase lands, the following statements are
true:

- `DATABASE_NODE_IP`, `DATABASE_API_PORT`, and
  `DATABASE_METRICS_PORT` no longer exist as config
  fields. They are replaced by:
  - `MARIADB_GATEWAY_HOSTS: list[str]` — list of
    sf-database gRPC endpoints clients can reach. A
    single-instance deployment sets this to a
    one-element list. Pydantic parses comma-
    separated env-var values into the list (or, if
    Pydantic v2 needs a custom validator for that,
    the validator is added).
  - `MARIADB_GATEWAY_PORT: int` — port the
    sf-database gRPC service listens on (default
    13005, unchanged).
  - `MARIADB_GATEWAY_METRICS_PORT: int` — port the
    sf-database Prometheus metrics endpoint listens
    on (default 13006, unchanged).
- The `sf-database` daemon no longer reuses the
  client-facing config for its own bind address. It
  binds on `config.NODE_MESH_IP` (the per-node mesh
  IP, already configured per-node by the deployer)
  for both the gRPC server and the Prometheus
  metrics server.
- `load_cluster_config()`, `_use_database_service()`,
  and `_get_database_stub()` consult the new names.
  Phase-2-shaped consumers use
  `MARIADB_GATEWAY_HOSTS[0]` when they need a single
  endpoint string. Phase 3 reshapes the channel
  construction into client-side LB over the full
  list.
- Comments and docstrings across `config.py`,
  `mariadb.py`, and `daemons/database/main.py`
  describe the new mental model: "MARIADB_HOST
  available = direct path preferred;
  MARIADB_GATEWAY_HOSTS = tier endpoints for the
  rest of the cluster."
- The deployer-side ansible templates are updated
  to set the new env-var names on every daemon
  unit and on the systemd `ExecStartPre` lines.
  Ansible variable `database_node_ip` is renamed
  to `mariadb_gateway_hosts` (and accepts a list).
- `tests/` are updated to use the new field names
  in their `MockConfig` /
  `SFConfig`-replacement fixtures.
- `docs/operator_guide/database.md` and any other
  operator-facing doc that names
  `DATABASE_NODE_IP` is updated to the new names.

## Decisions (phase-local)

1. **`MARIADB_GATEWAY_HOSTS` is `list[str]`, not
   `str`.** Per master plan decision 12, the type is
   a list to forward-compatibility-prove phase 3's
   client-side LB work. Phase-2 consumers simply use
   `MARIADB_GATEWAY_HOSTS[0]` for the single-endpoint
   path. If Pydantic v2 requires a custom
   `BeforeValidator` to parse comma-separated env
   vars into a list, the validator lives next to
   the Field definition.

2. **`MARIADB_HOST` presence implies preference for
   direct access.** Resolves open question 4 in
   favour of preference. There is no `USE_GATEWAY`
   override flag. `_use_database_service()`'s
   contract becomes: "return True if
   `MARIADB_GATEWAY_HOSTS` is set AND `MARIADB_HOST`
   is not." The two configs are sufficiently
   orthogonal in code shape; the *preference*
   reflects the fact that the only daemon with
   direct access (sf-database itself) gains nothing
   by hopping through the tier to reach itself.

3. **`DATABASE_API_PORT` and `DATABASE_METRICS_PORT`
   are renamed for consistency.** The master plan
   only enumerated `MARIADB_GATEWAY_HOSTS`
   explicitly, but renaming the host config without
   renaming its sibling ports leaves the surface
   inconsistent. The new names are
   `MARIADB_GATEWAY_PORT` and
   `MARIADB_GATEWAY_METRICS_PORT`. Defaults
   unchanged (13005 / 13006).

4. **sf-database's own bind address moves from
   `DATABASE_NODE_IP` to `NODE_MESH_IP`.** The
   per-node mesh IP is already a config every node
   has set. Reusing the client-facing config as a
   server-bind address only made sense when there
   was exactly one sf-database in the cluster and
   it lived at the same IP every client connected
   to. With the tier model, the bind address is
   "this node's address"; clients reach it by
   having the endpoint in their gateway-hosts
   list. The Prometheus metrics server gets the
   same treatment.

5. **No back-compat env-var aliases.** Per master
   plan decision 7 (greenfields only), the old
   `SHAKENFIST_DATABASE_NODE_IP` etc. simply stop
   working. Operators redeploy against the new
   names. Release notes (phase 7) call this out.

6. **Ansible variable rename is part of this
   phase.** `database_node_ip` in
   `roles/primary/defaults/main.yml` and the
   `deploy.yml` set-fact line becomes
   `mariadb_gateway_hosts` (a YAML list). The
   broader deployer rewrite (getsf prompts,
   roles/mariadb deletion) is still phase 4 — this
   phase only does the minimal ansible-template
   surgery required for the renamed env vars to be
   set correctly on every daemon unit.

## Steps

Four sequential steps. Each step must leave the tree
buildable and pre-commit-clean.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | worktree | Decouple sf-database's own bind addresses from the client-facing config. In `shakenfist/daemons/database/main.py`: at line ~5076, replace `config.DATABASE_METRICS_PORT` with `config.MARIADB_GATEWAY_METRICS_PORT` — wait, no, that field doesn't exist yet. Actually for step 1 the rename hasn't happened; step 1 *only* changes the bind address from `DATABASE_NODE_IP` to `NODE_MESH_IP`. So at lines ~5196 and ~5209, replace `config.DATABASE_NODE_IP` with `config.NODE_MESH_IP`. Both the `start_http_server(config.DATABASE_METRICS_PORT)` call and the `add_insecure_port(f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}')` call have the same shape — change the IP only, leave the port as-is for now. Read `shakenfist/config.py` first and confirm `NODE_MESH_IP` exists as a Field — it should. Update the surrounding comments to reflect that sf-database binds on the per-node mesh IP (not on a cluster-wide "database node IP" notion). No client-side changes in this step; clients still use `config.DATABASE_NODE_IP` to know where to connect. Verify pre-commit passes. One commit. |
| 2 | high | opus | worktree | The big rename. (a) In `shakenfist/config.py` lines ~329-340: replace the three `DATABASE_*` Field definitions with `MARIADB_GATEWAY_HOSTS: list[str] = Field(default_factory=list, description='List of sf-database tier endpoints (host:port pairs use MARIADB_GATEWAY_PORT). Single-endpoint deploys set a one-element list.')`, `MARIADB_GATEWAY_PORT: int = Field(13005, description='Port the sf-database gRPC service listens on.')`, `MARIADB_GATEWAY_METRICS_PORT: int = Field(13006, description='Prometheus metrics port for the sf-database daemon.')`. If Pydantic v2's automatic env-var-to-list parsing doesn't accept comma-separated strings, add a `field_validator` (BeforeValidator pattern) that splits on commas and strips whitespace; check pydantic-settings docs to confirm the behaviour. (b) In `shakenfist/config.py` `load_cluster_config()` around line 72: rename the `db_ip = os.getenv('SHAKENFIST_DATABASE_NODE_IP')` lookup to read `SHAKENFIST_MARIADB_GATEWAY_HOSTS`, parse the comma-separated value into a list, and use the first entry for the single gRPC connect call (`f'{hosts[0]}:{port}'`). Rename the `SHAKENFIST_DATABASE_API_PORT` env var reference on the next line to `SHAKENFIST_MARIADB_GATEWAY_PORT`. (c) In `shakenfist/mariadb.py` `_use_database_service()` (lines ~291-304): change the field check from `config.DATABASE_NODE_IP` to `config.MARIADB_GATEWAY_HOSTS` (truthy check works for an empty list too — `if not config.MARIADB_GATEWAY_HOSTS:`). (d) In `shakenfist/mariadb.py` `_get_database_stub()` (lines ~312-328): change the channel target from `f'{config.DATABASE_NODE_IP}:{config.DATABASE_API_PORT}'` to `f'{config.MARIADB_GATEWAY_HOSTS[0]}:{config.MARIADB_GATEWAY_PORT}'` (still single-endpoint; phase 3 reshapes this). (e) Update `daemons/database/main.py` to use `config.MARIADB_GATEWAY_PORT` and `config.MARIADB_GATEWAY_METRICS_PORT` in the two bind sites (the IPs were already moved to `NODE_MESH_IP` in step 1). (f) Grep `grep -rn 'DATABASE_NODE_IP\\|DATABASE_API_PORT\\|DATABASE_METRICS_PORT' --include='*.py' shakenfist/` — every remaining hit must be addressed (mostly test fixtures). Update each call site to use the new name. (g) `pre-commit run --all-files`. Worktree isolation because this touches the central runtime routing and config — easy to break startup. One commit. |
| 3 | medium | sonnet | none | Update docstrings, comments, and the ansible-template surface. (a) Rewrite the `load_cluster_config()` docstring (lines 18-32 of `config.py`) to describe the new mental model: "MARIADB_HOST set = process has direct MariaDB access (sf-database itself, or sf-ctl ensure-mariadb-schema); otherwise the gateway tier is consulted via gRPC." (b) Rewrite `_use_database_service()` docstring in `mariadb.py` similarly — replace the singleton framing ("Only the database daemon has MARIADB_HOST configured directly") with the orthogonal one ("MARIADB_HOST signals direct access is available and preferred; MARIADB_GATEWAY_HOSTS provides tier endpoints for clients without direct access"). (c) `shakenfist/deploy/ansible/deploy.yml` line 233: rename `database_node_ip:` set-fact to `mariadb_gateway_hosts:` and change the value from a single `node_mesh_ip` lookup to a single-element YAML list: `[ "{{ hostvars[groups['etcd_master'][0]]['node_mesh_ip'] }}" ]`. (d) `shakenfist/deploy/ansible/roles/primary/defaults/main.yml`: change `database_node_ip: "127.0.0.1"` to `mariadb_gateway_hosts: ["127.0.0.1"]`. (e) Grep `grep -rn 'database_node_ip\\|DATABASE_NODE_IP\\|DATABASE_API_PORT\\|DATABASE_METRICS_PORT' --include='*.yml' --include='*.j2' shakenfist/deploy/` — for each hit, update to the new name. The `SHAKENFIST_MARIADB_HOST=localhost` ExecStartPre lines in `roles/primary/tasks/cluster_config.yml` and `roles/base/tasks/register.yml` are unchanged in this phase — that direct-access escape hatch is dissolved in phase 4 when the broader deploy rework lands. (f) Update the comment in `deploy.yml` around line 221 if it references `DATABASE_NODE_IP`. (g) Verify with `cd shakenfist/deploy/ansible && ansible-playbook --syntax-check deploy.yml` if ansible-lint or syntax checks are part of pre-commit; otherwise rely on `pre-commit run --all-files` to catch yaml issues. One commit. |
| 4 | medium | sonnet | none | Documentation. (a) `docs/operator_guide/database.md`: search for `DATABASE_NODE_IP`, `DATABASE_API_PORT`, `DATABASE_METRICS_PORT` and update each to the new name. Add or update text describing the orthogonal `MARIADB_HOST` vs `MARIADB_GATEWAY_HOSTS` mental model — operators reading this section after phase 2 should come away with: "the process running `sf-database` has `MARIADB_HOST` set and uses MariaDB directly; every other daemon has `MARIADB_GATEWAY_HOSTS` set to the list of sf-database endpoints it can reach." (b) Search the rest of `docs/operator_guide/` and `docs/developer_guide/` for the old names — any hits get updated. (c) `CLAUDE.md`: search for `DATABASE_NODE_IP` etc. and update; also update the "Configuration options" block near `# Storage: MariaDB and the Database Service` if it lists the old names. (d) `ARCHITECTURE.md`, `README.md`, `AGENTS.md`: search for the old names and update if they appear. Most likely none of these reference the config field names directly. (e) Verify final state: `grep -rn 'DATABASE_NODE_IP\\|DATABASE_API_PORT\\|DATABASE_METRICS_PORT' --include='*.md' --include='*.py' --include='*.yml' --include='*.j2' shakenfist/ docs/ CLAUDE.md README.md ARCHITECTURE.md AGENTS.md 2>/dev/null` returns only hits in `docs/plans/` and `docs/release_notes/`. (f) `pre-commit run --all-files`. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
- After step 1: sf-database can still start (binding on
  `NODE_MESH_IP` instead of `DATABASE_NODE_IP`); clients
  can still reach it via the unchanged client-side path.
  Functional CI exercises the end-to-end path.
- After step 2: every consumer of the old config names
  is migrated. A grep for the old names returns only doc
  references that step 4 will sweep.
- After step 3: a fresh `getsf` run still produces a
  working ansible-deployed cluster with the new env-var
  names set correctly on each daemon unit. (Tested via
  cluster_ci's functional pipeline.)
- After step 4: final-state grep returns only
  docs/plans/ and docs/release_notes/ references to the
  old names.
- Both single-endpoint (cluster_ci default) and the
  conceptual multi-endpoint case work — the latter is
  exercised in phase 6 once phase 3 lands the LB
  channel. Phase 2's responsibility is only that the
  type / config plumbing is in place for phase 3 to
  build on; functional N>1 coverage is phase 6.

## Risks

- **Pydantic v2 list-from-env-var parsing.** Pydantic-
  settings may not natively split `SHAKENFIST_MARIADB
  _GATEWAY_HOSTS="10.0.0.1,10.0.0.2"` into a list.
  The sub-agent brief tells the agent to check the
  pydantic-settings docs and add a `BeforeValidator`
  if needed. If the validator misbehaves on a
  single-element value, the single-endpoint
  deployments break. Mitigation: phase-2 step 2's
  pre-commit should include a test that passes a
  single-element env-var value and verifies it parses
  into a one-element list; the sub-agent's brief
  asks for this. If pre-commit doesn't have a test,
  the management-session review checks it before
  committing.
- **`load_cluster_config()` runs at module-import
  time.** Any error there crashes every daemon at
  startup. The function already has broad `except`
  blocks for bootstrap-time tolerance; step 2's
  changes must preserve those.
- **Ansible-template typo.** A typo like
  `mariadb_gateway_hosts` vs `mariadb_gateway_host`
  in one place but not another causes every daemon
  on the affected node to silently fail to reach
  the tier. Mitigation: step 3's grep verifies
  consistency across templates.
- **`is_etcd_master` ansible group still referenced
  in `deploy.yml:233`.** Step 3's rewrite of the
  `database_node_ip` line continues to reference
  `groups['etcd_master'][0]`. That string is
  PLAN-remove-primary phase 7's scope — leave the
  group name alone, just rename the *Ansible
  variable* it gets bound to. The brief for step 3
  is explicit about this boundary.

## Out of scope

- Client-side gRPC load balancing — phase 3.
- The `grpc.health.v1.Health` server-side
  implementation — phase 3.
- `getsf` prompts for operator-supplied MariaDB
  credentials, `roles/mariadb/` deletion,
  `tools/bootstrap-mariadb.sql` — phase 4.
- CI workflow step to install MariaDB outside
  `getsf` — phase 5.
- N>1 functional test coverage — phase 6.
- `etcd_master` ansible group rename to
  `database_node` — PLAN-remove-primary phase 7.
- The `SHAKENFIST_MARIADB_HOST=localhost`
  ExecStartPre escape hatch in
  `roles/primary/tasks/cluster_config.yml` — that
  dissolves in phase 4's deploy rewrite, not here.

## Back brief

Before executing this phase, please back brief the
operator on:

- The four steps in order with the file boundaries
  for each.
- The decision to move sf-database's bind address
  from `DATABASE_NODE_IP` to `NODE_MESH_IP` (step
  1, standalone). Confirm this aligns with the
  operator's expectation that each sf-database
  instance in a future tier binds on its own
  per-node address.
- The resolution of open question 4 — `MARIADB_HOST`
  set implies preference for direct access, not a
  strict-orthogonal split. No `USE_GATEWAY`
  override flag.
- The list-type change for `MARIADB_GATEWAY_HOSTS`
  and the phase-2-uses-first-entry / phase-3-does-
  real-LB sequencing.
- The deliberate boundary against
  PLAN-remove-primary phase 7 (the `etcd_master`
  ansible-group name stays in `deploy.yml:233`;
  step 3 renames only the variable bound to it).
