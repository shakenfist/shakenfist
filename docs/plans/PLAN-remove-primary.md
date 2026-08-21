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
galaxy role), `shakenfist/mariadb.py` (database access, in
particular `set_cluster_config`'s idempotent upsert that the
role's config tasks rely on), and `shakenfist/daemons/database/`
(the gRPC service that becomes a deployer-chosen tier of
equal stateless instances — see
[`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md) for that work).

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
and MariaDB as the cluster datastore. (Grafana, the
primary-node Prometheus server, and `prometheus-node-exporter`
on every node used to be in this list too; all three have
been removed as warmup work for this plan. The sample
Grafana dashboard lives at `examples/grafana-dashboard.json`
for operators who want to import it into their own Grafana.
SF daemons continue to expose their own Prometheus metrics
endpoints (13001 / 13002 / 13006), but node-level metrics
are now an operator concern — they choose node_exporter,
telegraf, or whatever else their monitoring stack uses.) All
of these run inside the SF deployer's purview and are
configured by the ansible roles in
`shakenfist/deploy/ansible/`.

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

- The deployer ceases to install Loki/rsyslog aggregation
  or an API load balancer. (Grafana and the primary-node
  Prometheus server are already gone — see the situation
  section. MariaDB-server install and the `sf-database`
  SPOF removal have been lifted out of this plan and are
  tracked separately in
  [`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md), which makes
  MariaDB operator-provided infrastructure and reshapes
  `sf-database` into a deployer-chosen tier of equal
  instances.)
- The deployer is repackaged as an ansible-galaxy
  **collection** (`shakenfist.shakenfist`): a parameterised
  core `node` role that installs the daemon swarm, writes
  config, and manages systemd on one SF node, plus a small
  number of component roles (`hypervisor`, `network`,
  `internal_ca`) for the genuinely divergent package/config
  concerns. Which daemons and capabilities a node runs are
  expressed as **role variables**, not by the role reading
  inventory group names. The database tier is a capability
  flag, not a separate role. See "Galaxy collection
  structure" under the phase notes for the full rationale.
- Operators consume the collection from their own playbook,
  mapping their inventory groups to role invocations and
  variables. The current single-node and CI deployments
  become *example consumers* of the collection, not the
  product.
- Cluster-wide bootstrap stays a handful of small,
  idempotent steps rather than a new orchestrating command.
  Cluster config (including per-node defaults the role
  computes from gathered facts via `set_fact`) is applied
  idempotently by the role on every run via `sf-ctl
  set-config` (`set_cluster_config` is an upsert).
  `AUTH_SECRET_SEED` remains caller-supplied — SF never
  generates it, so it stays stable even if a prior cluster
  goes undetected. The `system` namespace and its initial
  operator-supplied key are established by the existing
  idempotent `sf-ctl bootstrap-system-key`. Schema
  initialisation and migration are handled by the separate
  `sf-ctl ensure-mariadb-schema` command introduced in
  `PLAN-byo-mariadb.md`. Because every step is idempotent, a
  partially-applied bootstrap self-heals on the next role
  run — no `bootstrap_operations` table or transaction
  bracketing is required. (This reverses an earlier design
  that added a `bootstrap_operations` table and a single
  `sf-ctl bootstrap-cluster` command; see the phase 2 note.)
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

1. **Galaxy collection publishing channel.** The collection
   *shape* is now decided (see "Galaxy collection structure"
   below): one `shakenfist.shakenfist` collection with a
   parameterised core `node` role plus `hypervisor` /
   `network` / `internal_ca` component roles. What remains
   open is the *publishing channel*: does SF publish the
   collection to ansible-galaxy / Automation Hub proper, or
   ship it inside the existing python package and document
   how operators wire it into their playbook? The latter is
   simpler and avoids a second release artefact; the former
   is more discoverable. Decide before phase 6. This choice
   also drives the release pipeline: if the collection is
   published to galaxy proper, phase 6 must add a
   collection-build-and-publish job to
   `.github/workflows/release.yml` alongside the existing
   PyPI job; if it ships inside the pip package, no
   `release.yml` change is needed.
2. **Dev/test convenience preservation.** The single-node /
   "I just want a working SF on my laptop" experience needs
   to survive. Options: ship a separate `examples/single-
   node/` playbook that exercises every convenience together,
   or document a quickstart that wires the new galaxy role
   together with the documented BYO-MariaDB single-box flow
   from `PLAN-byo-mariadb.md`. Decide before phase 6.
3. **CI rig migration.** `shakenfist/deploy/shakenfist_ci`
   currently exercises the deployer end-to-end. As the
   deployer changes shape, the CI rig becomes one of the
   first consumers of the new galaxy role. Sequencing this
   so CI never goes dark for more than one phase needs care
   — every phase must leave CI green.
4. **Topology.json migration.** Existing operators with
   `topology.json` files need either a shim that translates
   them to ansible inventory, or a clear "here's how to
   rewrite this by hand" doc. Phase 7 should decide which.

The `bootstrap_operations` granularity question that used
to sit here is resolved: the table is gone (see the phase 2
note), so there is no granularity to decide.

## Execution

The work breaks into phases that can each land
independently, leaving CI green at every step. The early
phases are pure deletion / rename and carry low risk; the
remaining new mechanism lives almost entirely in the
galaxy-collection packaging (phase 6) and needs the most
care.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Remove rsyslog aggregation from deployer | _(realised by [PLAN-remove-syslog-forwarding.md](PLAN-remove-syslog-forwarding.md) phase 5)_ | Complete |
| 2. ~~`bootstrap_operations` table and idempotent `sf-ctl bootstrap-cluster`~~ | _(dissolved — see phase notes; the role-config-idempotency remainder folds into phase 6)_ | Abandoned |
| 3. Remove Apache reverse proxy from deployer | _(realised by [PLAN-remove-apache-lb.md](PLAN-remove-apache-lb.md))_ | Complete |
| 4-5. _(MariaDB BYO and sf-database tier — moved to [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md))_ | _(separate plan)_ | Superseded |
| 6. Repackage deployer as the `shakenfist.shakenfist` galaxy collection; delete the getsf installer chain; example consumers | [PLAN-remove-primary-phase-06-galaxy-role.md](PLAN-remove-primary-phase-06-galaxy-role.md) | Complete |
| 7. Rename `etcd_master` → `database_node`; final cleanup | [PLAN-remove-primary-phase-07-rename-cleanup.md](PLAN-remove-primary-phase-07-rename-cleanup.md) | Complete |
| 8. Roll the reusable smoke-cluster CI workflow out to the downstream repos (the workflow itself is authored in phase 6 step 5) | [PLAN-remove-primary-phase-08-shared-ci.md](PLAN-remove-primary-phase-08-shared-ci.md) | Complete |

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
  pipeline can index it. **Realised by
  [`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md)
  phase 5**, which delivers the Loki-shipper story this phase
  was gated on and then deletes the rsyslog deployer surface
  (`roles/base/tasks/syslog.yml`, the client/server
  `rsyslog-*.conf` templates, the `syslog_target` variable, the
  `rsyslog` package + service enablement, and the
  `--log-syslog` gunicorn flag), in the same way phase 3 was
  realised by `PLAN-remove-apache-lb.md`.
- **Phase 2 (dissolved).** This phase originally introduced
  a `bootstrap_operations` table and an idempotent
  `sf-ctl bootstrap-cluster` command that subsumed every
  `set-config` / admin-namespace bootstrap task, plus a
  recorded cluster version and a per-daemon version-compat
  startup check. Detailed planning reassessed the scope to
  nothing:
  - **Config stays in the role.** Several current
    `set-config` values (`MAX_HYPERVISOR_MTU`, `DNS_SERVER`,
    `HTTP_PROXY`) are recomputed by ansible on every deploy,
    so folding them into a one-time bootstrap would stop
    those updates propagating. Config — including per-node
    defaults computed via `set_fact` from gathered facts —
    is applied idempotently by the role each run. The
    role-config idempotency tidy-up folds into phase 6.
  - **No new bootstrap command or table.** `AUTH_SECRET_SEED`
    is a caller-supplied `set-config`; the `system` namespace
    and its operator-supplied key are the existing idempotent
    `bootstrap-system-key` (`Namespace.new` is idempotent and
    `add_key` is keyed by name, so a re-run overwrites rather
    than duplicates). Idempotency already makes a partial
    bootstrap self-healing on the next run, which was the
    only thing the one-transaction `bootstrap_operations`
    table existed to guarantee.
  - **No cluster-version table or per-daemon version check.**
    byo-mariadb's `verify_schema_versions` already refuses to
    start a daemon whose build disagrees with the DB schema,
    covering the real compatibility hazard. A statically
    bootstrap-recorded version would only catch
    schema-invariant skew and would go stale on the first
    rolling upgrade; if that protection is ever wanted it can
    be derived from the per-node `installed_version` already
    stored in node attributes, with no new schema. Dropped.
- **Phase 3** deletes `roles/primary/tasks/apache2.yml` and
  `files/apache-site-primary.conf`. Documents the
  load-balancing requirement for production operators and
  the localhost:13000 single-node escape hatch.
- **Phases 4 and 5** (MariaDB BYO and the sf-database
  tier model) have been lifted out of this plan and are
  tracked in [`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md).
  That plan removes the MariaDB-server install from the
  deployer entirely (no opt-in demotion — the role is
  deleted), reshapes `sf-database` into a deployer-chosen
  tier of equal stateless instances reached via client-
  side gRPC load balancing (not leader election), and
  carves schema/migration execution out of daemon startup
  into a new `sf-ctl ensure-mariadb-schema` command.
  Phase 6 below assumes the BYO-MariaDB plan has landed
  first (the galaxy role's documented quickstart for a
  single-box deploy points at byo-mariadb's
  `tools/bootstrap-mariadb.sql` and `apt install
  mariadb-server` flow).
- **Phase 6** moves `roles/base` and its dependents into the
  `shakenfist.shakenfist` collection described under "Galaxy
  collection structure" below: a parameterised core `node`
  role plus the divergent `hypervisor` / `network` /
  `internal_ca` component roles. It replaces `deploy.py`'s
  topology-JSON translation with direct ansible inventory
  consumption, converts the role's capability selection so it
  no longer reads `groups['hypervisors']` /
  `groups['network_node']` / `groups['etcd_master']`
  internally (host membership becomes role variables), and
  recasts the single-node and CI playbooks as example
  consumers. The legacy installer chain — `getsf`, its
  generated `/root/sf-deploy`, the topology JSON, `deploy.py`,
  and the monolithic `deploy.yml` — is **deleted** in this
  phase, not slimmed; the example playbooks replace it. This
  removes the `deploy.py` / `deploy.yml` files that phase 7
  was originally scoped to rename `etcd_master` in, so
  phase 7's remaining scope is only the residual
  `etcd_master` mentions in surviving roles, templates, CI,
  and comments.

  Phase 6 also folds in the ansible integration. The SF
  ansible modules (`sf_instance` / `sf_network` /
  `sf_namespace` / `sf_snapshot`) move into the collection as
  native `AnsibleModule`s under `plugins/modules/`,
  auto-discovered as `shakenfist.shakenfist.sf_*`. This
  retires the bash shims, the `sf-client ansible …`
  subcommand they shell out to, and the awkward
  `postinstall.yml` install dance
  (`sf-client admin ansible_module_path` introspection plus a
  manual copy into `/usr/share/ansible/plugins/modules/` with
  hardcoded fallback paths). The modules call
  `shakenfist_client` as their API SDK — the same pattern as
  `community.aws` over `boto3` — so the REST/API logic stays
  in the client-python repo and only the ansible glue lives
  in the collection. The collection declares a compatible
  `shakenfist_client` version range as a Python requirement;
  an ansible control node needs `ansible-galaxy collection
  install shakenfist.shakenfist` plus `pip install
  shakenfist_client`, never the server package. Note the
  cross-repo coupling: the current module source lives in
  client-python, so this step either vendors the modules into
  the collection or is coordinated with a client-python
  change.
- **Phase 7** is mostly mechanical: rename `etcd_master` →
  `database_node` across the surviving collection roles,
  example playbooks, templates, CI, and comments (the
  `deploy.py` / `deploy.yml` occurrences are gone with those
  files in phase 6). By the time phase 7 runs,
  `PLAN-remove-etcd.md` will already have landed and the
  drain code, `etcd_host` default, `ETCDCTL_API=3` line in
  `sfrc`, and `etcd3gw` dependency will be gone. Phase 7's
  scope is therefore *only* the deployer-level naming and
  comments — the ansible group rename and the residual
  `etcd_master` mentions in the example playbooks and role
  comments.
- **Phase 8** rolls the reusable smoke-cluster CI workflow
  out to the downstream SF ecosystem repos. (At execution
  time the real consumers were `client-python` plus a
  composite-action mode for kerbside-shaped repos;
  `library-python` no longer exists -- see the phase 8
  sub-plan.) The historical pattern
  was to cut-and-paste shakenfist's cluster-build CI into
  each downstream repo, which drifts (the
  `/etc/sf/inventory.yaml` log-gather step is one symptom —
  see the dropped write in phase 6) and over-pays: the
  downstream repos only need the cheap smoke tier, not the
  full merge CI. **The reusable workflow itself is authored
  in phase 6 step 5, not here** — to avoid writing any
  throwaway intermediate CI, the `smoke-cluster.yml`
  (`workflow_call`) in `shakenfist/actions` and shakenfist's
  own cutover onto it are pulled forward into phase 6 (the
  decision: don't build a getsf-shaped stopgap and then
  replace it). Phase 8 is therefore *only* the rollout:
  replace each downstream repo's copy-pasted cluster-build
  workflow with a few-line `uses:
  shakenfist/actions/.github/workflows/smoke-cluster.yml@…`
  call, one repo at a time, passing the component/ref/tier
  inputs. Depends on phase 6 step 5 (the reusable workflow
  must exist and be proven on shakenfist's own CI first);
  independent of phase 7. The downstream-repo changes are
  committed to `main` and pushed by the operator — the agent
  prepares the diffs but cannot push them. If it grows, this
  phase can graduate to its own master plan, the way the old
  phases 4-5 became `PLAN-byo-mariadb.md`.

### Galaxy collection structure

The deployer becomes a single ansible-galaxy **collection**,
`shakenfist.shakenfist`, rather than a constellation of
independently-published roles. The structure and its
rationale:

- **One collection as the distribution unit.** Ansible's
  community good-practice bundles related roles at the
  "type or landscape level" into a namespaced, versioned
  collection that can share plugins — which matches SF
  wanting one cohesive, versioned deployment artefact.
- **A core `node` role, parameterised.** Every SF node runs
  the same daemon swarm today (`roles/base` writes identical
  systemd units on every host; only `sf-database` is
  conditional). Differentiation is three capability flags
  (`NODE_IS_HYPERVISOR`, `NODE_IS_NETWORK_NODE`,
  `NODE_IS_DATABASE_NODE`) plus a few package sets. Because
  the daemon set is uniform, the bulk of the deployer is one
  parameterised role, not one role per node type — splitting
  by node type would mostly produce roles that install the
  same thing, which the good-practice guide warns against
  ("don't create multiple roles if one parameterised role
  suffices").
- **Component roles only where work genuinely diverges.**
  `hypervisor` (libvirt/KVM), `network` (VXLAN), and
  `internal_ca` (PKI) install materially different packages
  and config, so they stay as separate roles within the
  collection — the "promote a component to its own role when
  it diverges" rule. The database tier is *not* a separate
  role: `PLAN-byo-mariadb.md` already made `sf-database` a
  stateless daemon, so "database node" is just a capability
  flag that enables that daemon.
- **No inventory group names inside roles.** Today the
  config template reads `groups['hypervisors']`,
  `groups['network_node']`, and `groups['etcd_master']`
  directly. Ansible good-practice is explicit that roles
  should not embed host-group names — host membership is
  passed as variables. Phase 6 converts these to role
  variables; the operator's playbook is what maps their
  inventory groups to those variables.
- **Example playbooks map groups → roles.** The single-node
  and cluster_ci playbooks become example consumers that
  assign hosts to the collection's roles with the right
  variables — the same pattern Kubespray and ceph-ansible
  use, where the playbook plus inventory decides which hosts
  run which role.
- **Ansible modules ship as collection content.** The SF
  ansible modules become native `AnsibleModule`s under the
  collection's `plugins/modules/`, replacing the bash-shim +
  `postinstall.yml` copy install. Bundling roles and modules
  in one collection is exactly what the collection format is
  for; operators get them via `ansible-galaxy collection
  install`, with `shakenfist_client` as the only Python
  dependency on the control node. See the phase 6 note for
  the cross-repo coupling with client-python.

Precedent: ceph-ansible and Kubespray both split into
multiple roles, but they split by *functional component /
software* (etcd, container-engine, mon, osd) and let the
playbook map inventory groups to those roles — they do not
embed group names in role internals. SF differs in that its
per-node software is near-uniform, so the split is lighter:
one core role plus a few divergent component roles.

References: Red Hat "Good Practices for Ansible"
(`redhat-cop.github.io/automation-good-practices`),
ceph-ansible (`github.com/ceph/ceph-ansible`), Kubespray
(`github.com/kubernetes-sigs/kubespray`).

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
risky or experimental. Phase 6 in particular reshapes the
deployer into a galaxy collection and rewrites the ansible
modules — its more invasive steps should default to worktree
isolation. Phase 7 is a rename / cleanup and can work
directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making judgment
calls about scope and sequencing.

Each phase plan should specify the recommended effort level
for planning that phase. Phase 6 (galaxy-collection
packaging, including the native ansible modules) is high
effort because it changes the operator-facing API. Phase 7
(the `etcd_master` → `database_node` rename and final
cleanup) is largely mechanical and can be planned at medium
effort. Phase 8 (the shared reusable CI workflow) is high
effort because it designs a cross-repo CI contract consumed
by several ecosystem repos. (Phases 1 and 3 are already
realised by other plans; phase 2 is dissolved; phases 4-5
moved to `PLAN-byo-mariadb.md`.)

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
  rsyslog server, or Apache by default. (MariaDB-install
  removal is handled by
  [`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md) and is
  outside this plan's scope.)
* Cluster bootstrap is a set of idempotent steps with no
  dedicated orchestrating command or table: cluster config
  (including per-node defaults) is applied by the role on
  every run via `set-config`, `AUTH_SECRET_SEED` is
  caller-supplied, and the `system` namespace plus its
  initial operator-supplied key are established by the
  existing idempotent `sf-ctl bootstrap-system-key`.
  Re-running the deploy on a bootstrapped cluster is a no-op.
  (Schema initialisation is handled by the separate
  `sf-ctl ensure-mariadb-schema` command introduced in
  `PLAN-byo-mariadb.md`.)
* The SF ansible modules are native collection content under
  `plugins/modules/` (`shakenfist.shakenfist.sf_*`), with no
  shim / copy-into-`/usr/share/ansible` install step, and
  depend only on `shakenfist_client` on the control node.
* The deployer is consumable as an ansible-galaxy
  **collection** (`shakenfist.shakenfist`): an operator can
  write a one-page playbook that assigns hosts to the
  collection's `node` role (plus `hypervisor` / `network` /
  `internal_ca` component roles as needed), passing the
  daemon/capability mix as role variables, and arrive at a
  working node configuration. No role reads inventory group
  names internally.
* The single-node and cluster_ci deployments are example
  consumers of the collection, not the collection itself.
* The legacy interactive installer and its topology
  machinery are gone: `shakenfist/deploy/getsf`, the
  `/root/sf-deploy` script it generated, the topology-JSON
  input that drove it, `deploy.py` (the topology → inventory
  translator), and the monolithic `deploy.yml` no longer
  exist. Deployment is driven entirely by operator-authored
  (or example) playbooks consuming the collection.
* The `etcd_master` group name is gone from the deployer
  (excluding the `shakenfist/etcd.py` drain code, which is
  out of scope).
* The SF ecosystem repos that stand up a cluster in CI
  (`client-python`, `library-python`, `kerbside`, …) consume
  a single reusable smoke-cluster workflow from
  `shakenfist/actions` instead of copy-pasting shakenfist's
  CI, and shakenfist's own CI is the first caller of that
  workflow. No downstream repo carries a forked copy of the
  cluster-build CI.
* Documentation in `docs/` is updated:
  `docs/operator_guide/` gains a "deploying SF against your
  own infrastructure" section; `ARCHITECTURE.md` loses the
  primary-node references; `README.md` and `AGENTS.md` are
  updated to reflect the new deployment shape. (BYO-MariaDB
  updates to `docs/operator_guide/database.md` are owned
  by `PLAN-byo-mariadb.md`.)

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
- **MariaDB BYO and `sf-database` as a tier.** Tracked in
  [`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md). Removes
  MariaDB-server install from the deployer entirely,
  reshapes `sf-database` into a deployer-chosen tier of
  equal stateless instances reached via client-side gRPC
  load balancing, and carves schema/migration execution
  out of daemon startup into a new
  `sf-ctl ensure-mariadb-schema` command. Was originally
  phases 4-5 of this plan.
- **Sticky session affinity for blob transfers.** Tracked in
  `PLAN-sticky-transfers.md`. The streaming-proxy baseline
  this plan delivers is the universal fallback; sticky
  cookies are an optional refinement for multi-request
  transfer sessions on LBs that support them.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

- **Cross-attribute lost updates on instances, networks and
  artifacts** (PRs #3337 and #3338). The attribute-update
  path read the whole attribute row, modified one field and
  wrote the whole row back, so two daemons concurrently
  updating *different* attributes of the same object could
  silently clobber each other. Surfaced by the phase-6/8 CI
  as the "agent operation enqueue lost to a concurrent
  power-state update" wedge; fixed by masking updates to only
  the named columns.
- **Cluster secrets echoed by the deploy plays.** The
  collection's `site.yml` originally logged `sf-ctl`
  bootstrap output containing cluster secrets; `no_log` was
  added to those tasks, `sf-ctl show-config` now redacts
  secret-classed keys by default, and the secrets are passed
  on stdin rather than argv (pre-release audit findings).
- **`/etc/sf/config` written world-readable** by the node
  role while containing the Loki auth header and (on
  database-tier nodes) the MariaDB password; tightened to
  `root:sudo` `0440` to match the global auth file
  (pre-release audit finding).

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table
  with a link to the plan, its initial status, its phase
  arithmetic, and a one-line intent.
* **`order.yml`** — add an entry for the new master plan so
  it appears in the documentation navigation in the
  intended order.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
