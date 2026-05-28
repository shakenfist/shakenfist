# Remove the primary node and adopt a BYO-infrastructure deployer

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf,
ansible-galaxy roles), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Key references inside the repo
include `shakenfist/deploy/ansible/deploy.yml` (the playbook
under reform), `shakenfist/deploy/ansible/deploy.py` (the
topology-JSON to ansible-groups translator that will be
retired), `shakenfist/deploy/ansible/roles/primary/` (the
role to be removed), `shakenfist/deploy/ansible/roles/base/`
(daemon installation, which becomes the heart of the new
galaxy role), `shakenfist/mariadb.py` (database access and
the `DATA_MIGRATIONS` machinery that the bootstrap_operations
table will sit alongside), and `shakenfist/daemons/database/`
(the gRPC service that becomes electable).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The Shaken Fist deployer today installs and operates a stack
of supporting infrastructure on top of the SF daemons
themselves: rsyslog for log aggregation onto a primary node,
Apache as a reverse proxy / load balancer for the REST API,
and MariaDB as the cluster datastore. (Grafana and the
primary-node Prometheus server used to be in this list too;
both have already been removed as warmup work for this plan.
The sample Grafana dashboard lives at
`examples/grafana-dashboard.json` for operators who want to
import it into their own Grafana, and SF daemons continue to
expose their Prometheus metrics endpoints for operator-run
Prometheus to scrape.) All of these run inside the SF
deployer's purview and are configured by the ansible roles
in `shakenfist/deploy/ansible/`.

The `primary_node` role in particular is the visible focal
point: it hosts the rsyslog sink, the Apache reverse proxy,
and the ad-hoc ansible inventory. It
also nominally hosts the cluster-bootstrap orchestration,
though every `sf-ctl` call in `cluster_config.yml` is
`delegate_to: groups['etcd_master'][0]`, so the primary node
is the play's `hosts:` line and little else.

Over the last several years, the operators who run SF in
production have all turned out to bring their own monitoring,
log pipeline, load balancer, and (increasingly) their own
MariaDB. The primary node's job has become "installing things
nobody asked for." At the same time, the deployer's
topology-JSON-to-ansible-groups translator
(`shakenfist/deploy/ansible/deploy.py`) is duplicating logic
that ansible already provides as inventory groups.

The deployer also still uses the legacy group name
`etcd_master` for what is now the MariaDB / `sf-database`
node, and `shakenfist/etcd.py` remains in the tree to drain
residual etcd keys via `DATA_MIGRATIONS` — both holdovers
from the pre-MariaDB era.

## Mission and problem statement

Shaken Fist stops being a platform deployer and becomes an
**opinionated application that runs against operator-provided
infrastructure**. Concretely:

- The deployer ceases to install Loki/rsyslog aggregation,
  an API load balancer, or the MariaDB server. (Grafana and
  the primary-node Prometheus server are already gone — see
  the situation section.)
- The deployer is repackaged as an ansible-galaxy role that
  configures one SF node — installing packages, writing
  config, managing systemd — parameterised by which daemons
  that node should run.
- Operators consume the role from their own playbook,
  expressing topology in ansible inventory directly. The
  current single-node and CI deployments become *example
  consumers* of the role, not the product.
- Cluster-wide bootstrap (schema, `AUTH_SECRET_SEED`, admin
  namespace, cluster_config defaults) is exposed as a
  single idempotent `sf-ctl bootstrap-cluster` command that
  records completion in a new `bootstrap_operations` table,
  with each operation and its completion record written in
  the same transaction so partial-bootstrap states are
  impossible.
- The single-instance `sf-database` SPOF goes away: it
  becomes electable like `sf-cluster`, with non-elected
  candidates serving as discovery surface that refers
  clients to the current leader. The MariaDB *server* stays
  outside that election (operator concern, with HA via
  Galera / managed service / accepted SPOF for small
  deploys).
- Stale `etcd_master` naming throughout the deployer is
  renamed to `database_node`. (The `shakenfist/etcd.py`
  drain code itself remains, as already documented in
  `CLAUDE.md`, until the next minor.)

The principle is: SF deploys `sf-*` daemons on hosts you've
told it about, against infrastructure (DB, metrics, logs,
LB) you've told it the addresses of. Nothing else.

TLS between SF components — including mTLS for gRPC, TLS for
the MariaDB connection, and graceful cert reload on
rotation — is **out of scope for this plan** and is tracked
separately in `PLAN-embrace-tls.md`. This plan establishes
the BYO-PKI surface that the TLS plan then consumes.

## Alternatives considered

### Smart, cluster-state-aware load balancing on the primary node

An alternative direction would have retained the primary node
as a smart load balancer that knows where every blob and
which roles live on which nodes, routing each REST request
directly to a node holding the relevant data. We reject this:

- It preserves the central tier this plan is otherwise trying
  to remove, and re-introduces a SPOF that must be made HA
  and performant in its own right.
- It conflates role-routing (network operations → the network
  node) with data-routing (blob reads → any node holding a
  replica). These problems want different mechanisms; the
  network-facade plan already handles role-routing via
  queue-enqueue.
- It is a path no comparable distributed system has converged
  on. S3, HDFS, Cassandra, Ceph and etcd all push routing
  into the client or into HTTP-level redirects rather than
  into a central, state-aware tier. The closest mainstream
  example is GitHub's shard router, which is a custom
  behemoth requiring an engineering investment incompatible
  with SF's minimal-and-opinionated manifesto.

### Client-following HTTP 307 redirects to specific nodes

A second alternative would have each REST node redirect the
client to a specific peer node when it doesn't hold the
requested data — the pattern used by S3 (region routing) and
etcd (write redirect to leader). We reject this for SF
because it punches a hole through the operator's perimeter:
SF nodes today are reachable only via the operator's load
balancer (and any WAF / TLS terminator behind it). A 307 to
a per-node hostname or IP exposes cluster topology to
clients and bypasses the perimeter that operators rely on.
Threading the redirect back through the LB would require the
LB to understand a token that selects a backend, which is
the smart-LB option above by another route.

### Chosen direction for blob reads

Receiving REST nodes act as a streaming reverse proxy: when
sf-api is asked for a blob it doesn't hold, it opens a
connection to a peer that does and streams bytes through
without staging the blob to local storage. The bandwidth
cost is a double-hop on the *cluster mesh*, which is
typically an order of magnitude faster than the operator's
outer network (10 GbE within a rack or two versus 1 GbE for
the LAN/WAN that clients sit on is a common topology), so
the cost lands where the bandwidth is cheap. Latency on the
streamed bytes is unaffected once the first byte arrives.

Operators who want to eliminate the double-hop entirely can,
in a future iteration, opt into content-addressable blob
placement combined with consistent-hash routing on their
existing LB. That work is out of scope for this plan and
fits more naturally into the blob-storage roadmap.

### Sticky session affinity as a refinement

For multi-request transfer sessions — multi-chunk blob
uploads, and ranged downloads that the SF client retries on
connection drops — server-set sticky cookies on the
operator's load balancer offer a refinement that eliminates
the double-hop entirely for the session, without exposing
per-node URLs to clients. The first request in a session
lands on any node; that node decides which backend should
own the session and emits a `Set-Cookie` value that the LB
intercepts and honours for subsequent requests. The cookie
is opaque to the client, so the perimeter property
(clients see only the LB URL) is preserved. The streaming
proxy above remains the universal fallback when the
operator's LB does not support server-set sticky cookies
(open-source nginx is the notable real-world example).
This refinement has its own scope — LB-specific config,
cookie format decisions, fallback detection, interaction
with content-addressable placement — and is tracked
separately in `PLAN-sticky-transfers.md`.

## Open questions

1. **Galaxy-role packaging mechanics.** Does the SF project
   publish to ansible-galaxy proper, or distribute the role
   via the existing python package and document how to wire
   it into an operator's playbook? The latter is simpler
   and avoids a second release artefact; the former is more
   discoverable. Decide before phase 6.
2. **`bootstrap_operations` granularity.** Is the
   granularity per-config-key (one row per
   `AUTH_SECRET_SEED`, `RAM_SYSTEM_RESERVATION`, etc.) or
   per-logical-step (one row per "set initial cluster
   config")? Per-key gives finer recovery; per-step gives
   a cleaner audit trail. Phase 2 should resolve this with
   a concrete schema proposal.
3. **`sf-database` leader discovery cache lifetime.** How
   long should a client cache "host-X is the leader" before
   asking again? Too short and we hammer the candidate list;
   too long and failover takes longer than necessary. The
   `cluster_locks` lease is 60s with a 20s refresh, so 30-60s
   on the client side is the obvious starting point, but the
   tradeoff against connection-pool churn needs measurement.
4. **Dev/test convenience preservation.** The single-node /
   "I just want a working SF on my laptop" experience needs
   to survive. Options: keep an opt-in `mariadb_local` task
   in the role, ship a separate `examples/single-node/`
   playbook that exercises every convenience together, or
   document a quickstart that uses docker for the
   convenience layer. Decide before phase 6.
5. **CI rig migration.** `shakenfist/deploy/shakenfist_ci`
   currently exercises the deployer end-to-end. As the
   deployer changes shape, the CI rig becomes one of the
   first consumers of the new galaxy role. Sequencing this
   so CI never goes dark for more than one phase needs care
   — every phase must leave CI green.
6. **Topology.json migration.** Existing operators with
   `topology.json` files need either a shim that translates
   them to ansible inventory, or a clear "here's how to
   rewrite this by hand" doc. Phase 7 should decide which.

## Execution

The work breaks into phases that can each land
independently, leaving CI green at every step. The early
phases are pure deletion / rename and carry low risk; the
later phases introduce new mechanism (bootstrap CLI,
elected `sf-database`, galaxy-role packaging) and need more
care.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Remove rsyslog aggregation from deployer | PLAN-remove-primary-phase-01-remove-monitoring.md | Not started |
| 2. `bootstrap_operations` table and idempotent `sf-ctl bootstrap-cluster` | PLAN-remove-primary-phase-02-bootstrap-cli.md | Not started |
| 3. Remove Apache reverse proxy from deployer | PLAN-remove-primary-phase-03-remove-lb.md | Not started |
| 4. Make MariaDB BYO; demote local mariadb role to opt-in convenience | PLAN-remove-primary-phase-04-byo-mariadb.md | Not started |
| 5. Elect `sf-database`; introduce candidate-list discovery library | PLAN-remove-primary-phase-05-elect-database.md | Not started |
| 6. Repackage deployer as a galaxy-style role; example consumers | PLAN-remove-primary-phase-06-galaxy-role.md | Not started |
| 7. Rename `etcd_master` → `database_node`; final cleanup | PLAN-remove-primary-phase-07-rename-cleanup.md | Not started |

Phase notes:

- **Phase 1** deletes the rsyslog forwarder configuration in
  `roles/base/tasks/syslog` and associated templates. (The
  primary-node Prometheus server install has already been
  removed as warmup; rsyslog removal is held back until SF
  has a Loki-shipper story so operators are not left without
  a log path.) Documents the metric and log endpoints
  operators must scrape / collect themselves. Also flips
  `shakenfist-utilities` JSON-formatted logging on by
  default (and removes the non-JSON code path on the SF
  side, on the assumption operators going to Loki / Elastic
  / Splunk want structured logs), and documents the SF
  log-record field-name contract so the operator's log
  pipeline can index it.
- **Phase 2** introduces the `bootstrap_operations` table
  in `mariadb.py`, the `sf-ctl bootstrap-cluster` subcommand,
  and replaces every `set-config` / `ensure-mariadb-schema` /
  admin-namespace task in `cluster_config.yml` and
  `register.yml` with a single call to the new command. The
  one-transaction-per-op invariant is the schema's central
  claim and must be enforced in the code, not just the docs.
  Phase 2 also records the cluster's SF version on first
  bootstrap (either as a `bootstrap_operations` row or as a
  dedicated `cluster_version` table — phase 2's design
  decision) and adds a startup check in every SF daemon
  that compares its own version against the recorded
  cluster version and refuses to start if outside the
  supported window. The compatibility policy (the proposal
  is "N-to-N+1 always supported; N-to-N+2 not assumed") is
  decided as part of phase 2 and documented for operators.
- **Phase 3** deletes `roles/primary/tasks/apache2.yml` and
  `files/apache-site-primary.conf`. Documents the
  load-balancing requirement for production operators and
  the localhost:13000 single-node escape hatch.
- **Phase 4** removes the mariadb install from the default
  deploy.yml flow and demotes `roles/mariadb` to an opt-in
  "for dev/test or evaluation" role. Removes the
  `SHAKENFIST_MARIADB_HOST=localhost` direct-access hack in
  the bootstrap path — bootstrap now goes through `sf-database`
  like every other database access.
- **Phase 5** is the largest. Adds `cluster_locks`-based
  election to the `sf-database` daemon, exposes a "who is
  the leader" gRPC endpoint that non-elected candidates can
  answer, introduces a candidate-list discovery client in
  `shakenfist-utilities` (or in-tree if the utilities repo
  release cadence is awkward), and migrates every consumer
  off the single `DATABASE_NODE_IP` config.
- **Phase 6** moves `roles/base` and its dependents into a
  galaxy-shaped layout, replaces `deploy.py`'s topology JSON
  translation with direct ansible inventory consumption, and
  recasts the single-node and CI playbooks as example
  consumers.
- **Phase 7** is mostly mechanical: rename `etcd_master` →
  `database_node` across `deploy.py`, `deploy.yml`, all
  roles, and comments. By the time phase 7 runs,
  `PLAN-remove-etcd.md` will already have landed and the
  drain code, `etcd_host` default, `ETCDCTL_API=3` line in
  `sfrc`, and `etcd3gw` dependency will be gone. Phase 7's
  scope is therefore *only* the deployer-level naming and
  comments — the ansible group rename, the inventory.yaml
  `etcd:` children-group rename, and the residual
  `etcd_master` mentions in role comments.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. Phases 2, 4 and 5 in particular touch
bootstrap correctness, the MariaDB access path, and
cross-daemon discovery — those should default to worktree
isolation. Phases 1, 3, and 7 are deletions / renames and
can work directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making judgment
calls about scope and sequencing.

Each phase plan should specify the recommended effort level
for planning that phase. Phases involving schema design
(phase 2), cross-daemon coordination (phase 5), or migration
safety (phase 4) should be planned at high effort. Phases
that are largely deletion / rename (phases 1, 3, 7) can be
planned at medium effort. Phase 6 (galaxy-role packaging) is
high effort because it changes the operator-facing API.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**

- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief.
- **low** — Purely mechanical changes (rename, reformat, add
  a log line, regenerate proto stubs).

**Model choice:**

- **opus** — Deep reasoning, cross-daemon architectural
  understanding, subtle correctness judgment (locking, state
  machines, migration), or complex protocol research.
- **sonnet** — Good default for well-briefed implementation
  work.
- **haiku** — Purely mechanical tasks: search-and-replace,
  regenerating proto stubs, adding log lines.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include what to change,
which files to touch, what patterns to follow, and any
non-obvious constraints. The better the brief, the lower the
effort level needed and the lighter the model that can
succeed.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] CI deploys still succeed on the cluster_ci rig (this
      plan is operator-facing; an internally-clean change
      that breaks the CI deploy is a regression).
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* `shakenfist/deploy/ansible/roles/primary/` no longer
  exists.
* No part of the deployer installs Grafana, Prometheus,
  rsyslog server, Apache, or MariaDB by default.
* `sf-ctl bootstrap-cluster` exists, is idempotent, and is
  the only path through which a cluster's initial schema,
  auth secret, admin namespace, and default config are
  established. Re-running it on a bootstrapped cluster is
  a no-op.
* The `bootstrap_operations` table exists and is populated
  by every bootstrap step in the same transaction as that
  step's artefact.
* `sf-database` is electable; non-elected candidates can be
  queried for the current leader. Every other SF daemon
  obtains its database endpoint via a candidate list, not a
  single `DATABASE_NODE_IP`.
* The deployer is consumable as an ansible-galaxy role: an
  operator can write a one-page playbook that calls the
  role with a daemon mix and arrive at a working node
  configuration.
* The single-node and cluster_ci deployments are example
  consumers of the role, not the role itself.
* The `etcd_master` group name is gone from the deployer
  (excluding the `shakenfist/etcd.py` drain code, which is
  out of scope).
* Documentation in `docs/` is updated:
  `docs/operator_guide/` gains a "deploying SF against your
  own infrastructure" section; `docs/operator_guide/database.md`
  is updated for the BYO MariaDB story; `ARCHITECTURE.md`
  loses the primary-node references; `README.md` and
  `AGENTS.md` are updated to reflect the new deployment
  shape.

### Future work

- **mTLS for gRPC, TLS for MariaDB, graceful cert reload.**
  Tracked in `PLAN-embrace-tls.md`. This plan establishes the
  operator-provides-PKI surface that the TLS plan consumes.
- **Retiring `shakenfist/etcd.py` and the `DATA_MIGRATIONS`
  drain code.** Tracked in `PLAN-remove-etcd.md`. No longer
  blocked on this plan landing: the decision to close
  in-place upgrade from etcd-era SF means the drain code
  is dead weight forever and can be deleted at any time.
  Sequenced *before* this plan in `index.md` so the
  remove-primary work is not navigating misleading etcd
  references while it does the `etcd_master` rename in
  phase 7.
- **Health checks, readiness, and graceful drain semantics.**
  A precondition for the BYO-LB story being operationally
  honest: operators need a `/healthz` (or equivalent) that
  distinguishes "I'm up," "I'm ready to serve," and "I'm
  draining" so their LB can route correctly during rolling
  upgrades. Touches every daemon, has its own design
  depth (dependency-aware readiness: sf-api isn't ready
  until sf-database is reachable, etc.), and is useful even
  without remove-primary, so it wants its own plan. Should
  land alongside this plan — ideally before phase 6 (the
  galaxy role), so the role's documentation can point at
  well-defined health endpoints.
- **Remove the eventlog service entirely.** Tracked in
  [`PLAN-eventlog-direct-mariadb.md`](PLAN-eventlog-direct-mariadb.md).
  The original framing of this stub was "move sqlite to
  MariaDB and make the daemon electable," but on closer
  inspection `sf-eventlog` is a thin gRPC wrapper in front
  of sqlite whose only remaining job once storage moves is
  proxying writes that could go directly to `sf-database`.
  The plan therefore deletes the daemon, routes calling-site
  `eventlog.add_event*` calls directly through `sf-database`,
  moves pruning into the cluster daemon's maintenance loop,
  and removes the local-sqlite read path that today forces
  sf-api to be co-located with the eventlog node. No hard
  ordering against this plan, but the two are mutually
  reinforcing.
- **Network node failover.** Unlike `sf-database`, the
  network node owns layer-2/3 identities (egress NIC,
  floating IPs, NAT/DHCP state). Its HA story is *VIP
  failover*, not leader election — the well-known answer is
  operator-provided keepalived / corosync / BGP. SF should
  declare "exactly one node holds this role at a time" via
  `cluster_locks` and document the recommended FOSS
  failover mechanism. Its own plan, lower priority than
  the eventlog change.
- **OpenTelemetry instrumentation.** A systematic
  replacement of the homegrown `RecordedOperation` plus
  the ad-hoc prometheus exporters with otel spans and
  metrics. Buys cross-daemon trace visibility (a user
  operation traced from sf-api through sf-queues to
  sf-net visible as one trace in Jaeger / Tempo /
  Honeycomb) and stabilises the metrics surface as a
  documented contract. Subsumes the originally-named
  "metrics audit" thread. Its own plan.
- **Discovery library home.** If the candidate-list client
  is published from `shakenfist-utilities`, the utilities
  repo release cadence becomes a coupling. Reconsider
  whether utilities is the right home once phase 5 is
  designed in detail.
- **Sticky session affinity for blob transfers.** Tracked in
  `PLAN-sticky-transfers.md`. The streaming-proxy baseline
  this plan delivers is the universal fallback; sticky
  cookies are an optional refinement for multi-request
  transfer sessions on LBs that support them.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Plan Status* table
  with a link to the plan, its phase breakdown, initial
  status, and a one-line description.
* **`order.yml`** — add an entry for the new master plan so
  it appears in the documentation navigation in the
  intended order.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
