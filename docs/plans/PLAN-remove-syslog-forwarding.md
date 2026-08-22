# Remove syslog forwarding and ship logs to Loki

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read the
existing log-shipping prior art — the local eventlog spool
(`shakenfist/eventlog_spool.py`), its batched drainer thread
(`shakenfist/eventlog_drainer.py`), how the drainer is started
from `shakenfist/daemons/daemon.py` and
`shakenfist/external_api/gunicorn_config.py`, and how
`mariadb.record_event_batch()` handles batch success/failure —
because the Loki shipper in this plan is modelled directly on
that machinery. Read the logging setup helper in the sibling
`shakenfist_utilities` library
(`library-utilities/shakenfist_utilities/logs.py`, the
`setup()` function and its `JsonFormatter` path). Read the
ansible deployer's syslog wiring
(`shakenfist/deploy/ansible/roles/base/tasks/syslog.yml`,
`roles/base/templates/rsyslog-client-01-sf.conf`,
`roles/primary/templates/rsyslog-server-01-sf.conf`,
`deploy.yml`, and the `sf-api.service` unit). Ground your
answers in what the code actually does today rather than
guessing.

Where a question touches on external concepts (the Loki HTTP
push API and its `/loki/api/v1/push` payload shape, Loki
labels vs structured metadata, multi-tenancy via the
`X-Scope-OrgID` header, journald/rsyslog behaviour, node-local
log agents such as Grafana Alloy / promtail / vector), research
as needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture overview
and the event logging subsystem. Consult `CLAUDE.md` for build
commands, project conventions, the BYO-infrastructure direction
(`MARIADB_GATEWAY_HOSTS` is the pattern to mirror for the Loki
endpoint), the systemd service ordering, and the
preserve-event-logging priority. This plan is the "Loki-shipper
story" that [`PLAN-remove-primary.md`](PLAN-remove-primary.md)
phase 1 is explicitly gated on (see its phase notes), and it
follows the same BYO-infrastructure philosophy as
[`PLAN-byo-mariadb.md`](PLAN-byo-mariadb.md).

When we get to detailed planning, I prefer a separate plan file
per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md` extension.
Tracking of these sub-phases is done via the table in the
Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a single
commit. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

Shaken Fist daemons emit logs through a shared helper:
`from shakenfist_utilities import logs; LOG, _ =
logs.setup(__name__)`. Today `logs.setup()`
(`library-utilities/shakenfist_utilities/logs.py:178`) defaults
to a `SysLogHandler('/dev/log')`, so every daemon's log lines
land in the node's local syslog/journald as plain text
(`'%(levelname)s %(message)s'`). The API daemon additionally
passes `--log-syslog --log-syslog-prefix sf` to gunicorn
(`deploy/ansible/files/sf-api.service:33`). The library already
contains a `json=True` code path that emits structured JSON via
pylogrus `JsonFormatter` (`logs.py:209-222`), but nothing turns
it on, and `.with_fields(...)` structured context is therefore
flattened into the text message rather than carried as fields.

The deployer then aggregates those local logs onto the primary
node via rsyslog. Non-primary nodes get a forwarder config
(`roles/base/templates/rsyslog-client-01-sf.conf`) that ships
`*.*;*.!=debug` over TCP to the primary's mesh IP on port 514,
with a 10,000-entry linked-list queue and 100 retries. The
primary runs an rsyslog server (`roles/primary/templates/
rsyslog-server-01-sf.conf`) loading `imudp`/`imtcp` on port
514, dropping aggregated logs into its default
`/var/log/syslog`. The wiring is:

- `roles/base/tasks/bootstrap.yml` installs the `rsyslog`
  package on every node and enables the service.
- `roles/base/tasks/syslog.yml` deploys the client config to
  every node whose mesh IP differs from `syslog_target`.
- `roles/primary/tasks/bootstrap.yml` (lines 18-30) deploys
  the server config to the primary.
- `deploy.yml:185` sets `syslog_target` to the primary node's
  mesh IP; `roles/base/defaults/main.yml:13` defaults it to
  `"unknown"`.

There is **no** existing Loki, promtail, grafana-agent, vector,
or any other remote-shipping configuration anywhere in the
tree. The library has no HTTP, queue, buffer, or remote log
handler — only syslog, file, and stdout handlers.

Crucially, Shaken Fist *already has* a mature, on-disk,
crash-safe log-shipping pattern in the eventlog subsystem, and
it was explicitly built with this future use in mind:

- `shakenfist/eventlog_spool.py` is a per-daemon disk-backed
  SQLite spool (WAL mode) under `/srv/shakenfist/spool/
  eventlog/<daemon>-<pid>.db`. The spool is the durability
  boundary: `enqueue()` is a sub-millisecond insert that
  returns immediately. It enforces a high-water mark
  (`SPOOL_HIGH_WATER_MARK = 100_000`, ~50 MiB) and *drops with
  a counter increment + WARN* rather than blocking when full.
  On startup it scans for orphan spool files left by dead PIDs
  and migrates their rows into the live spool. Its own comment
  (`eventlog_spool.py:59-62`) anticipates "a future loki-style
  log-spool peer" growing under a sibling spool root.
- `shakenfist/eventlog_drainer.py` is a per-process daemon
  thread (`eventlog-drainer`) that reads batches of up to 100
  (`DRAIN_BATCH_SIZE`), polls every 100 ms, applies exponential
  backoff on failure (0.5 s → 30 s, ×2), leaves failed batches
  in the spool for retry, drops poison rows, and on
  `atexit` synchronously drains for up to 20 s.
- It is started once per daemon from
  `daemons/daemon.py:113` (`write_pid_file`) and once per
  gunicorn worker from
  `external_api/gunicorn_config.py:56` (`post_fork`), giving
  each process its own spool file and drainer thread.
- Spool depth and drop metrics are exposed on the existing
  Prometheus endpoint with no per-daemon bootstrap because
  `prometheus_client` uses one process-wide registry.

The `shakenfist_utilities` library (repo
`github.com/shakenfist/library-utilities`, local checkout at
`library-utilities/`, also referred to informally as
"library-python") is pinned in `shakenfist/pyproject.toml` as
`shakenfist-utilities==0.8.4` and consumed from PyPI, not a
local path. The library's `logs` module has effectively no unit
tests today (only an ad-hoc `test.py` smoke script).

## Mission and problem statement

Remove syslog forwarding to the primary node entirely and
replace it with shipping Shaken Fist's structured logs to a
**Loki** log store. This is the logging half of the
BYO-infrastructure direction: in production the operator brings
their own Loki and tells SF its endpoint; in CI we stand up a
Loki the same way we stand up MariaDB for the functional test
cluster.

Concretely, when this plan is done:

- SF daemons emit **structured JSON for every log line** — JSON
  becomes the *only* log format on the daemon side, not merely
  the default. The plain-text formatter paths used for daemon
  output are removed, so there is no mixed text/JSON output for
  an operator's pipeline to special-case. Every line carries the
  `.with_fields(...)` context as real fields, against a
  documented, stable log-record field-name contract that an
  operator's log pipeline can index. (The interactive
  `setup_console(...)` formatter used by human-facing CLI tools
  such as `sf-ctl` is a separate concern — phase 0 decides
  whether it too becomes JSON or stays human-readable; "all log
  lines" here means all *daemon/service* log emission.)
- SF ships those logs directly to Loki via an **in-process,
  on-disk-spooled, batched push** modelled on the eventlog
  spool/drainer, living in the `shakenfist` package (not the
  library — see Alternatives). The shipper is **off unless an
  operator configures a Loki endpoint**; when off, logs remain
  on local stdout/journald exactly as a node-local default.
- The deployer **no longer installs rsyslog**, no longer
  configures client forwarders or a primary-node rsyslog
  server, and no longer passes `--log-syslog` to gunicorn. The
  `syslog_target` variable and the primary's rsyslog role
  content are removed.
- CI stands up a Loki instance for the functional cluster and a
  functional test asserts that SF log lines actually arrive in
  Loki end-to-end (this is the coverage of the new, risky push
  path). The "no Loki configured → structured JSON locally"
  path is also exercised so a minimal/dev deployment is covered.
- CI's log assertions and debugging bundle are reworked to read
  structured logs from Loki (and from each node's *local*
  structured logs as a fallback for when the shipper itself is
  what failed) rather than grepping the primary node's
  aggregated `/var/log/syslog`. The reworked log-checking
  scripts ship as **new versioned entries** in the
  `shakenfist/actions` repo so existing external consumers
  pinned to the old behaviour are not broken.
- The operator documentation explains the BYO-Loki contract:
  what endpoint/tenant config to set, what labels SF applies,
  the field-name contract, buffering/backpressure behaviour,
  and what is lost (centralised plain-syslog on the primary) vs
  gained (structured, queryable logs).

The guiding principle matches the rest of SF's BYO turn: SF
emits well-structured logs and, if you tell it where your Loki
is, ships them there reliably; otherwise it logs locally and
stays out of your way. SF does not run a log store, and after
this plan it does not run a log *aggregator* either.

Concretely there are exactly two operating modes, and the log
*format* (structured JSON) is identical in both — only the
*destination* differs:

- **Preferred / first-class path — Loki configured.** With a
  Loki endpoint (and a configurable `X-Scope-OrgID` tenant) set,
  each daemon buffers its JSON log lines in a local SQLite spool
  and ships them to Loki, with retry and backpressure. It does
  **not** additionally write them to a local syslog/file sink —
  there is no deliberate second pipeline. The spool is the local
  durability buffer that covers transient Loki outages.
- **Fallback path — Loki not configured.** For operators who
  would rather run their own shipper (or run no central log
  store at all), leaving the endpoint unset means SF simply
  emits the same JSON to the node's local journal (journald) and
  ships nothing. A node-local agent such as promtail/Alloy can
  then scrape that journal if the operator wants central logs on
  their own terms.

(In both modes, systemd still captures a service's stdout/stderr
into journald — uncaught tracebacks and any output before
logging is configured — which is free, local-only, and not a
deliberate shipping pipeline.)

This plan delivers the "Loki-shipper story" that
[`PLAN-remove-primary.md`](PLAN-remove-primary.md) phase 1 is
waiting on. The actual deletion of the rsyslog deployer wiring
(remove-primary's phase 1 deliverable) is performed here in
phase 5, in the same way that
[`PLAN-remove-apache-lb.md`](PLAN-remove-apache-lb.md) realised
remove-primary's phase 3. When phase 5 lands, remove-primary
phase 1 should be marked complete with a pointer to this plan.

TLS to the Loki endpoint (operator-provided CA, client certs
for mutual auth) is **out of scope** here and belongs with
[`PLAN-embrace-tls.md`](PLAN-embrace-tls.md), which establishes
the BYO-PKI surface. This plan supports a plain-HTTP or
operator-terminated-HTTPS endpoint and a bearer/basic auth
header, and leaves a clean seam for mTLS later. OpenTelemetry /
tracing instrumentation is a separate, not-yet-drafted thread
and is not part of this plan; the field-name contract defined
here should be chosen to not actively conflict with it.

## Relationship to the eventlog (events vs logs)

Shaken Fist deliberately has *two* structured-record streams, and
this plan only owns one of them. Making the division explicit
matters because it determines what actually flows to Loki.

The authoring convention in the code (which this plan makes
explicit and which the docs should state) is:

- **If a message relates to one or more Shaken Fist objects**
  (instance, network, blob, artifact, …), emit an **event** via
  `eventlog.add_event(...)` / `add_event_multi(...)`, so the
  operator and the object's owner get the fully-structured,
  per-object record.
- **If a message has no directly-associated object** (daemon
  lifecycle, scheduler decisions, node-level conditions), emit a
  **log** line.

Crucially, events are *already* dual-destination today.
`eventlog.add_event_multi()` (`eventlog.py:80-116`) emits a
structured log line (`log.info('Added event')`, carrying
`event_type`, `duration`, `message`, `extra`, and each
`object_type→uuid` as fields) **and** enqueues the row into the
MariaDB eventlog spool. So every event already lands in two
places; this plan merely redirects the log-line copy from the
primary node's syslog to Loki. No new mechanism is needed to get
events into Loki — they ride the normal log stream as
`'Added event'` lines.

The two destinations serve different masters and have different
lifecycles, and that is the point — it is **not** redundant
double-storage of a system of record:

- **MariaDB eventlog = the authoritative, per-object system of
  record.** Its consumers are billing (must never drop, exact
  per-object aggregation over unbounded time), audit surfaced to
  owners/admins (per-object, namespace-access-controlled,
  retained), and progress information for a future dynamic UI
  (per-object, low-latency point reads). These are exactly the
  workloads MariaDB's `(object_type, object_uuid)` index serves
  and that the `eventlog-direct-mariadb` plan was built for.
- **Loki = an ephemeral, operational, interleaved-with-logs
  view** for debugging "what was this node doing at 14:03",
  where having events and logs in one time-ordered stream is
  valuable.

The duplicated copy therefore lands only in the cheap, droppable
store, never twice in the authoritative one. The legitimate cost
is Loki *volume* — progress events in particular can be chatty —
so the event→Loki echo should be **deliberate and
configurable** (see Open Question 11), letting a volume- or
cost-sensitive operator keep authoritative events in MariaDB
while suppressing their echo into Loki and recovering the
interleaved view at the dashboard layer instead.

What this plan explicitly does **not** do is move event storage
or the event-lookup REST APIs onto Loki — see the rejected
alternative below.

## Alternatives considered

### Move events into Loki and retire MariaDB event storage

A tempting consolidation: since events already emit a log line,
stop writing the MariaDB eventlog, point the event-lookup REST
APIs at Loki's `query_range`, and store everything once. We
reject this decisively. Events back billing, owner/admin audit,
and per-object progress UI — workloads that need durability (no
drops), unbounded-time per-object aggregation, multi-month/year
retention, and namespace-scoped access control. Loki is a
log store: it is retention-windowed and lossy under pressure,
its label model makes per-object (`instance_uuid`) lookups
either a cardinality explosion or a full time-window scan, it has
no authz aligned to SF namespaces, and routing REST reads through
it would couple API availability to Loki being up. The
`eventlog-direct-mariadb` plan just placed these workloads on a
correctly-indexed store; moving them to Loki would regress every
axis that matters for them. Events stay in MariaDB; Loki receives
only the operational echo.

### Node-local log agent (promtail / Grafana Alloy / vector)

The prevailing industry pattern is to log to stdout/journald
and let a node-local agent tail the journal and ship to Loki.
The deployer would install and configure that agent per node;
SF itself would gain only the JSON-default change. This keeps
all buffering, batching, backpressure, retry, and multi-tenancy
inside a mature, well-tested agent, and is arguably the most
"normal" choice.

We nonetheless **reject it as the primary mechanism**, for
reasons specific to SF:

- It reintroduces exactly the kind of deployer-managed
  supporting daemon that the BYO turn is trying to *remove*. We
  would be deleting rsyslog only to install Alloy/promtail in
  its place, and now own its config, its version, its failure
  modes, and its upgrade story. "SF installs things nobody
  asked for" is the problem remove-primary is solving.
- SF already owns a crash-safe spool/drainer abstraction
  (eventlog) that solves the identical problem — durable
  on-disk buffering with backpressure and retry — and its
  authors explicitly anticipated a "loki-style log-spool peer"
  (`eventlog_spool.py:59-62`). Reusing that machinery is less
  net new code than configuring and shipping an agent, and it
  keeps the operational surface to "set a config value".
- Direct push lets SF carry its structured fields as Loki
  labels / structured metadata deterministically, rather than
  re-parsing them back out of journald text on the agent side.

We keep the agent path available to operators as a *documented
alternative*: an operator who already runs Alloy/promtail can
simply leave SF's Loki shipper disabled and point their agent
at journald. The chosen direction is what the deployer does by
default, not the only thing that can work.

### Put the shipper in the `shakenfist_utilities` library

The logging helper lives in the library, so the natural instinct
is to add the Loki handler there too. We reject this for the
*buffering* component specifically. The durable spool/drainer is
non-trivial (SQLite WAL, high-water drop, orphan recovery,
atexit drain, Prometheus wiring, per-process lifecycle started
from both `daemon.py` and gunicorn `post_fork`), it depends on
SF conventions (the `/srv/shakenfist/spool` root, the SF config
system, the SF Prometheus registry), and the library cannot
depend on `shakenfist`. Forcing that machinery into a
general-purpose utility library would either bloat the library
with SF-specific assumptions or require an awkward inversion of
control. The operator explicitly accepted that this code can
live in `shakenfist` if buffering in a library is too hard — it
is, so it does.

The split we adopt: the **library** owns the *format* (flip JSON
on by default, define and document the field-name contract, and
gain the unit tests it currently lacks); **shakenfist** owns the
*transport* (spool, drainer, HTTP push, config, lifecycle). SF
attaches its own `logging.Handler` to the root logger after
calling `logs.setup()`, so the library never needs to know Loki
exists.

### Push from a single central collector instead of per-node

We could ship all nodes' logs to one SF node which then pushes
to Loki. This rebuilds the primary-node aggregator we are
deleting and reintroduces the SPOF. Rejected for the same
reasons remove-primary is removing the primary. Each node pushes
to Loki independently; Loki (operator-run, possibly HA) is the
aggregation point.

## Open questions

These should be resolved in phase 0 (the decisions phase) before
implementation begins. Each has a recommended answer; phase 0
either confirms it or records a different decision with
rationale.

1. **Endpoint configuration shape.** Mirror the MariaDB pattern
   with `LOKI_GATEWAY_HOSTS` (a list, for future client-side
   fan-out) plus `LOKI_GATEWAY_PORT`, or a single
   `LOKI_PUSH_URL`? *Recommendation:* a single base URL
   (`LOKI_BASE_URL`, empty = disabled) for v1 — Loki is
   operator-run and typically already fronted by the operator's
   own LB/ingress, so SF does not need to fan out across Loki
   replicas itself. Keep the config name generic enough that a
   future host-list does not require a rename.

2. **Multi-tenancy.** Loki uses `X-Scope-OrgID` for tenant
   separation. Do we expose `LOKI_TENANT` (single tenant for
   the whole cluster), or derive tenant per-namespace?
   *Recommendation (operator-confirmed):* a single cluster-wide
   `LOKI_TENANT` (optional `X-Scope-OrgID` header) for v1.
   Making the tenant configurable is a deliberate requirement,
   not just a nicety: an operator running SF against a shared
   homelab/production Loki does not want SF's (potentially
   high-volume) logs co-mingled with everything else's, both for
   query hygiene and because per-tenant volume limits and
   retention can then be set independently. Per-namespace log
   tenancy is a much larger design touching authz and is
   deferred to future work.

3. **Labels vs structured metadata.** Loki best practice is a
   *small, bounded* label set (host, job/daemon, maybe node
   role) and everything else as structured metadata or log-line
   JSON. What is the exact label set, and which `with_fields`
   keys (e.g. `instance_uuid`, `network_uuid`, `request_id`)
   become structured metadata vs stay in the JSON body?
   *Recommendation:* labels = `{job="shakenfist",
   daemon=<daemon>, host=<node>}`; high-cardinality identifiers
   stay in the JSON body and/or structured metadata, never
   labels. Phase 0 fixes the precise mapping and writes it down
   as the field contract.

4. **Auth to Loki.** Bearer token, basic auth, or none (rely on
   operator network perimeter)? *Recommendation:* support an
   optional `LOKI_AUTH_HEADER` (opaque, operator-supplied) and
   otherwise send nothing; leave mTLS to
   `PLAN-embrace-tls.md`.

5. **Spool sharing vs separation from eventlog.** Do we
   generalise `eventlog_spool.py` into a reusable spool
   primitive that both eventlog and logship use, or fork a
   second `logship_spool.py` that mirrors it? *Recommendation:*
   fork a sibling `logship_spool.py` / `logship_drainer.py` for
   v1 (lower risk to the event path, which is under the
   preserve-event-logging priority), and note "unify the two
   spools behind one primitive" as future work. Phase 0 makes
   the final call.

6. **Local copy vs Loki-only.** When a Loki endpoint *is*
   configured, does SF *also* write its structured log stream to
   a local handler (syslog/file) as a fallback, or push to Loki
   only? *Recommendation (operator-leaning, and I agree): push
   to Loki only — no deliberate second local pipeline.* The
   reasoning:
   - An operator who already collects the node's syslog/journald
     with their own promtail (the common homelab case) would
     otherwise get **two copies of every line in Loki** — one
     from SF's push and one from their promtail scrape of the
     local copy.
   - A deliberate local handler doubles logging I/O on every
     node for a copy that, under the disposable-hypervisor
     direction, we are trying *not* to depend on.
   - The on-disk spool already *is* the local-durability safety
     net: a line that returns from `enqueue()` is on disk and
     survives a process crash (orphan recovery re-drains it), so
     a brief Loki outage does not lose logs even with no
     separate local handler. Only an outage long enough to
     exceed the spool high-water mark drops the Loki copy (with
     a counter + WARN), which is the acceptable failure mode for
     a disposable node.

   Two residual nuances phase 0 must nail down rather than gloss
   over: (a) systemd still captures a service's stdout/stderr
   into journald regardless of our handlers — uncaught
   tracebacks and any output emitted before logging is
   configured — which is essentially free and *local-only*
   unless the operator points their own agent at journald; we do
   not try to suppress it, but it is not a deliberate "second
   shipping pipeline". (b) When **no** Loki endpoint is
   configured (shipper disabled), SF must still log *somewhere*
   locally (stdout→journald) so a single-box/dev deployment is
   not silent — Loki-only applies to the configured case, not
   the disabled case. An optional explicit local-fallback
   handler can exist but is **off by default**.

7. **CI Loki topology.** Single-binary Loki container on the
   under-cloud, or on an inner-cluster node? How is its address
   plumbed to `LOKI_BASE_URL` for the inner cluster? *See the
   memory note that CI runs two layers of SF.* Phase 0 sketches
   this; phase 3 implements it, mirroring how MariaDB is stood
   up for `cluster_ci`.

8. **Gunicorn/sf-api specifics.** gunicorn's own access/error
   logs currently go via `--log-syslog`. Once that flag is
   removed, do gunicorn's logs route through the same Python
   logging root (and therefore the Loki handler), or do they
   need separate handling? Phase 0 verifies gunicorn logging
   integration so phase 5 can safely drop the flag without
   losing API access logs. Note that gunicorn's own log records
   must also come out as JSON if we are committing to
   JSON-only daemon output.

9. **CLI console formatter.** `logs.setup_console(...)`
   (`logs.py:237`) produces colorised, human-readable output for
   interactive CLI tools such as `sf-ctl`. The "all daemon log
   lines are JSON" commitment is about *daemon/service* output;
   does the interactive CLI path also become JSON, or stay
   human-readable? *Recommendation:* keep `setup_console`
   human-readable — it is terminal UI for an operator, not a log
   stream that ships to Loki — and scope the JSON-only rule to
   the daemon `setup(...)` path. Phase 0 confirms.

10. **Does CI need a Loki at all, or just local structured
    logs?** A tempting simplification: in CI, leave Loki
    unconfigured, let SF emit structured JSON locally on each
    node (the disabled-shipper path from question 6), and have
    the CI tooling collect and assert on those per-node local
    logs — losing central aggregation but avoiding standing up a
    Loki. *Recommendation: do both, for different reasons, and
    do not drop the CI Loki.*
    - We **keep a CI Loki** because the push path (spool →
      drainer → HTTP push → backpressure) is the single most
      important, newest, and riskiest thing this plan adds, and
      the project prefers functional coverage. A CI that never
      pushes to Loki never exercises the feature. The end-to-end
      "logs reached Loki" assertion is the proof the shipper
      works, and querying one central Loki also preserves the
      ergonomics of today's single-point `/var/log/syslog` grep
      for the routine forbidden-string checks.
    - We **also collect each node's local structured logs into
      the debug bundle** (and add a small test that the
      disabled-shipper path produces local JSON), precisely
      because the failure we most need to debug — "the shipper
      is broken" — is exactly the case where a Loki dump would
      be empty. Local per-node logs are the belt-and-braces that
      makes a shipper failure debuggable. clingwrap already
      fetches a per-node bundle, so this is a natural fit.
    So CI exercises both paths: the push path is what we *test*,
    the local path is what we *fall back to* for debugging. Phase
    0 confirms; phases 3–4 implement.

11. **Event→Loki echo: configurable, and on by default?** Every
    event already emits an `'Added event'` log line that will
    flow to Loki (`eventlog.py:80-116`); MariaDB remains the
    authoritative event store either way (see "Relationship to
    the eventlog"). Should that echo be suppressible so a
    volume-sensitive operator can keep authoritative events in
    MariaDB without paying for them again in Loki?
    *Recommendation:* yes — a config toggle (e.g.
    `LOG_EVENTS_TO_LOKI`), **on by default** for the
    single-pane operational view, off for operators who care
    about Loki volume/cost and are happy to query events from
    MariaDB (or interleave at the Grafana dashboard layer). This
    is a small change to whether the `'Added event'` line is
    emitted; it does **not** touch the MariaDB write path. Phase
    0 decides the toggle name and default; the implementation
    can ride phase 2. Note the CI log-checks (phase 4) must know
    which mode CI runs in, since it changes whether event lines
    appear in Loki.

## Decisions (phase 0)

Phase 0 resolved every open question against the code. The
committed answers below bind phases 1–6. Each "Decision:" is
grounded in files the executing agents actually read; key
file:line anchors are retained.

### Config surface (OQ1, OQ2, OQ4, OQ11)

**Decision:** Four new Pydantic options in `config.py`, in the
`Field(default, description=...)` style (e.g.
`MARIADB_GATEWAY_PORT` at `config.py:372`). There is **no
`SecretStr`** anywhere in `config.py` — `MARIADB_PASSWORD`
(`config.py:540`) and `AUTH_SECRET_SEED` (`config.py:148`) are
plain `str` — so `LOKI_AUTH_HEADER` is plain `str` with a
"treat as secret / never log" prose note, matching existing
practice. The `env_prefix = 'SHAKENFIST_'` (`config.py:549`)
makes each overridable as `SHAKENFIST_LOKI_*`.

| Option | Type | Default | Secret | Meaning |
|--------|------|---------|--------|---------|
| `LOKI_BASE_URL` | str | `''` | no | Loki base URL (e.g. `http://loki:3100`); empty disables the shipper |
| `LOKI_TENANT` | str | `''` | no | `X-Scope-OrgID` value; empty omits the header |
| `LOKI_AUTH_HEADER` | str | `''` | yes (by convention) | Opaque `Authorization` header; empty sends none; mTLS deferred to `PLAN-embrace-tls.md` |
| `LOG_EVENTS_TO_LOKI` | bool | `True` | no | Whether the `'Added event'` line is emitted to the log stream; never touches the MariaDB event write |

**Decision:** Spool/batch tunables are **module constants**, not
config, mirroring `eventlog_spool.py` /`eventlog_drainer.py`
exactly (`SPOOL_HIGH_WATER_MARK = 100_000` at
`eventlog_spool.py:72`; `DRAIN_BATCH_SIZE = 100`,
`BACKOFF_*`, `SHUTDOWN_DRAIN_TIMEOUT` at
`eventlog_drainer.py:50,53-63`). The new modules carry the same
names. `SPOOL_HIGH_WATER_MARK` may be promoted to config later
if a concrete operator need appears.

### Loki push wire format (OQ3, part A)

**Decision:** Ship via `POST /loki/api/v1/push` (JSON body). A
drained batch is grouped by identical label set into `streams`;
each record is a `values` pair `["<nanosecond-unix-ts string>",
"<JSON line>"]` where the line is the full pylogrus
`JsonFormatter` output. The timestamp **must** be a
nanosecond string (a number is rejected with HTTP 400). In
practice a single process emits one label set, so a batch is
usually one stream.

**Decision:** **No structured metadata in v1.** The optional
third per-value element (structured metadata) requires Loki
chunk format V4 / schema **v13** / tsdb index — experimental in
Loki 2.9, default-on only in **Loki 3.0**, and a 3.x server on
an older schema actively *rejects* such pushes. Since SF is
BYO-Loki and must work against whatever the operator runs, all
high-cardinality identifiers live in the JSON line **body** in
v1 (filterable with LogQL `| json | instance_uuid="..."` on any
version). Moving the hottest filter keys into structured
metadata behind a version/config check is recorded as future
work.

### Log-record field-name contract (OQ3, part B)

**Decision — the label set is fixed, bounded, and the ONLY
labels:** `{job="shakenfist", daemon=<daemon name>,
host=config.NODE_NAME}` (`config.py:473`). Stream cardinality is
therefore bounded by `daemons × nodes` (low hundreds). **No
per-object UUID, `request_id`, IP, or other high-cardinality
value is ever a label** — promoting any would make the Loki
index grow with the number of objects/requests ever seen
(cardinality explosion, per Grafana's labels guidance).

**Decision — everything else is JSON body in v1.** The base
JSON fields come from `JsonFormatter.enabled_fields`
(`logs.py:209-222`): `logger_name`, `ts`, `level`,
`thread_name`, `message`, `exception_class`, `stack_trace`,
`module`, `function`. All `with_fields(...)` keys (lower-cased
by `logs.py:80`; objects auto-unwrapped to `.uuid` strings by
`logs.py:92-101`) are body. The high-value filter keys that are
**candidates for promotion to structured metadata later** are:
`instance_uuid`/`instance`, `network_uuid`/`network`,
`interface_uuid`, `blob_uuid`/`blob`, `artifact_uuid`/`artifact`,
`node_uuid`, `request_id`/`request-id`, and the
`operation_uuid`/`object_uuid` family. The full key enumeration
(~80 keys across `shakenfist/`) was produced during phase 0; the
operative rule is "3 labels, everything else body in v1, the
list above are the SM-later candidates."

**Known wart (phase 1 cleanup):** `eventlog.py:82,105` sets
`fqdn = config.NODE_NAME`, duplicating the `host` label value in
the body. Phase 1 should drop the redundant `fqdn` from the
producer rather than carry the node name twice.

### Spool and handler module factoring (OQ5)

**Decision — fork, do not generalise.** Three new
`shakenfist/` modules, each a near line-for-line fork of its
eventlog twin so the event path (under the preserve-event-logging
priority) is untouched:

- `logship_spool.py` — mirror of `eventlog_spool.py`; spool root
  `/srv/shakenfist/spool/logship/<daemon>-<pid>.db`; same WAL
  sqlite, high-water drop+counter, orphan recovery. Payload row
  carries `{labels, ts_ns, line}`.
- `logship_drainer.py` — mirror of `eventlog_drainer.py`; same
  batch/poll/backoff constants and `atexit` drain. **Only
  divergence is the sink:** replace `mariadb.record_event_batch`
  (whose bool contract is documented at `mariadb.py:4565`) with
  `_push_to_loki(streams) -> bool` (True on 2xx; False on
  non-2xx/timeout/connection error). Failed batches stay spooled
  and retry with backoff — identical durability contract.
- `logship.py` — net-new (no eventlog twin): the
  `logging.Handler` subclass plus the idempotent
  `start(daemon_name)`. `emit()` formats via the library
  `JsonFormatter`, builds the `{labels, ts_ns, line}` row, and
  calls `logship_spool.enqueue()`; it is exception-safe
  (`handleError`) so logging never raises into a caller.

**Decision — metrics** on the process-wide default registry
(like `eventlog_spool.py:45-56`): `logship_spool_depth` (gauge
via `set_function`), `logship_spool_dropped_total` (counter),
`logship_push_total{result=...}` (counter), and optional
`logship_push_seconds` (histogram). Unifying the two spools is
future work.

### JSON-only logging, gunicorn, console (OQ8, OQ9)

**Decision — JSON-only is a real change, not a tidy-up.** All
**103** `logs.setup(...)` call sites in `shakenfist/` use the
defaults today (`syslog=True, json=False`) → every daemon
currently logs **text over `/dev/log`**, not JSON. Approach (as
implemented in `v0.8.5`): make `setup()` install the
`JsonFormatter` **unconditionally** and remove the `TextFormatter`
branches (`logs.py:223-231`), so every SF `logs.setup(__name__)`
call gets JSON with no per-call-site edits; the `json` parameter
(now a deprecated no-op), the `SHAKENFIST_LOG_TO_STDOUT` test
branch, and `setup_console()` survive. This is a breaking change
for non-SF text consumers, shipped pragmatically as a 0.8.x patch
bump.

**Decision — handler attachment.** SF attaches its Loki handler
to the **root** logger inside `logship.start()` (so the library
never imports `shakenfist`), only when `LOKI_BASE_URL` is set,
from the two confirmed seams beside the existing
`eventlog_drainer.start(...)`: `daemon.py:112-113`
(`write_pid_file`) and `gunicorn_config.py:55-56`
(`post_fork`, the only fork-safe moment for the drainer thread).

**Decision — gunicorn (the least pre-decided item).** gunicorn
sets `propagate = False` on `gunicorn.error`/`gunicorn.access`
in `Logger.__init__`, so propagation-to-root does **not** work
out of the box. Use a custom **`logger_class`** subclass of
`gunicorn.glogging.Logger` that re-enables propagation and
clears gunicorn's own stream handlers, so both loggers flow to
the root → JSON formatter → Loki handler. This makes dropping
`--log-syslog` from `sf-api.service:33` (phase 5) lossless.
(gunicorn access lines carry no `extra` fields, so access-log
JSON is limited to the rendered message — richer access fields
are future work.)

**Decision — console.** `setup_console()` (`logs.py:237`) stays
human-readable/colorized; JSON-only scopes strictly to the
daemon `setup()` path.

### Two-mode behaviour (OQ6)

**Decision — exactly two modes, never silent, never a
deliberate double-ship.**

- **Loki configured (`LOKI_BASE_URL` set):** one handler — the
  Loki spool handler. No deliberate second local handler. The
  WAL spool is the local-durability buffer; a Loki outage loses
  the Loki copy only past the high-water mark (counter + WARN).
- **Loki unconfigured:** one local handler so the node is not
  silent — `SysLogHandler('/dev/log')` with the JSON formatter
  (preferred over stdout because it preserves syslog priority
  for `journalctl -p`, is the existing default at `logs.py:203`,
  and keeps stdout clean).

**Decision — `/dev/log` survives rsyslog removal.** `/dev/log`
is owned by `systemd-journald-dev-log.socket` (`Symlinks=/dev/log`),
part of systemd, not the `rsyslog` package (which binds a
separate `syslog.socket`). Uninstalling rsyslog (phase 5) leaves
`/dev/log → /run/systemd/journal/dev-log` intact, so the
unconfigured-mode handler still lands in journald. Residual:
systemd captures service stdout/stderr to journald regardless
(pre-logging tracebacks) — free, local-only, not a shipping
pipeline.

### Events relationship and echo toggle (OQ11)

**Decision — events stay authoritative in MariaDB; the toggle
gates only the echo.** In `add_event_multi()`, the echo
(`eventlog.py:80-93`, `log.info('Added event')`) and the
authoritative MariaDB enqueue (`eventlog.py:101-116`,
`eventlog_spool.enqueue(payload)`) are cleanly separable — `log`
is not referenced after line 93, and the payload uses only raw
locals. `LOG_EVENTS_TO_LOKI=False` wraps **only** lines 80–93
and must not touch anything from line 95 on, nor `event_uuid`
(`:71`) or `request_id` (`:75-78`). The guard sits **after** the
existing `suppress_event_logging` early-return (`:45-46`) so
that flag keeps suppressing everything. `add_event()` (`:20-33`)
delegates to `add_event_multi()` and needs no separate guard.
Event storage and event-lookup REST APIs do **not** move to
Loki (rejected alternative ratified).

### CI Loki topology and dual-path coverage (OQ7, OQ10)

**Decision — stand up Loki the way CI MariaDB is.** A
single-binary Loki, installed by a new `tools/ci-install-loki.sh`
`scp`'d and `sudo`-run on the inner-cluster `${primary}` node
(co-located with the CI MariaDB, so the inner cluster already
routes to it), listening on `0.0.0.0:3100`. This mirrors
`tools/ci-install-mariadb.sh` (`functional-tests.yml:330-346`)
exactly; placing it on the under-cloud is rejected for parity.

**Decision — plumb as `LOKI_BASE_URL`** via a new
`GETSF_LOKI_BASE_URL` (+ `GETSF_LOKI_TENANT` /
`GETSF_LOKI_AUTH_HEADER`) env group on `getsf-wrapper`, mirroring
the `GETSF_MARIADB_*` flow (`functional-tests.yml:356-361` →
`getsf:534-612,979-983` → `deploy.py` → `roles/base/templates/
config`). Unlike `MARIADB_HOST`, `SHAKENFIST_LOKI_BASE_URL` is
rendered **unconditionally on every node** (like
`MARIADB_GATEWAY_HOSTS`), not gated on the database group. A
lower-effort phase-3 bootstrap is to set it through the existing
`GETSF_EXTRA_CONFIG` JSON list (no getsf/template change); the
dedicated group is the recommended end state.

**Decision — keep the CI Loki AND collect local logs.** Keep
Loki + an end-to-end "logs reached Loki" assertion (the only
functional coverage of the push path); the central Loki also
replaces the soon-removed primary syslog grep
(`functional-tests.yml:540-554`), which phase 4 reworks into
Loki queries. **Also** extend `ci-gather-logs.yml`
(`functional-tests.yml:607-631`) to pull each node's local
structured JSON into the clingwrap bundle, so a shipper failure
stays debuggable. Add a cheap unit/local test that the
disabled-shipper path emits local JSON.

**Decision — CI echo mode.** The primary functional run sets
`LOG_EVENTS_TO_LOKI=on` (the shipped default), so the phase-4
Loki log-checks see `'Added event'` lines; the off-path is
covered by the cheap disabled-shipper test (and optionally a
scheduled-test variant).

## Execution

The work breaks into phases that can each land independently and
leave CI green at every step. Phases 0–2 add the new mechanism
(structured logs + Loki shipper) while syslog forwarding is
still in place, so nothing is lost mid-flight. Phase 3 proves
the push path end-to-end in CI. Phase 4 reworks the CI log
assertions and debug bundle to read from Loki (and local logs)
so that phase 5 can delete the rsyslog wiring without leaving CI
blind. By the time phase 5 removes rsyslog, logs already flow to
Loki and CI already checks them there. Phase 6 documents.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions and design (config surface, label/field contract, spool factoring, CI Loki topology, auth) | [PLAN-remove-syslog-forwarding-phase-00-decisions.md](PLAN-remove-syslog-forwarding-phase-00-decisions.md) | Complete |
| 1. Library: default structured JSON logging + field-name contract + logs-module tests + release and pin bump | [PLAN-remove-syslog-forwarding-phase-01-json-logging.md](PLAN-remove-syslog-forwarding-phase-01-json-logging.md) | Complete |
| 2. Loki shipper in shakenfist: spool, drainer, HTTP push handler, config, lifecycle wiring, metrics, unit tests | [PLAN-remove-syslog-forwarding-phase-02-loki-shipper.md](PLAN-remove-syslog-forwarding-phase-02-loki-shipper.md) | Complete |
| 3. CI Loki: stand up Loki for the functional cluster and add an end-to-end "logs reach Loki" functional test | [PLAN-remove-syslog-forwarding-phase-03-ci-loki.md](PLAN-remove-syslog-forwarding-phase-03-ci-loki.md) | Complete |
| 4. Rework CI log-checks and clingwrap bundle for Loki + local logs (new versioned `shakenfist/actions`) | [PLAN-remove-syslog-forwarding-phase-04-ci-tooling.md](PLAN-remove-syslog-forwarding-phase-04-ci-tooling.md) | Complete |
| 5. Remove rsyslog forwarding from the deployer (realises remove-primary phase 1) | [PLAN-remove-syslog-forwarding-phase-05-remove-rsyslog.md](PLAN-remove-syslog-forwarding-phase-05-remove-rsyslog.md) | Complete |
| 6. Documentation: operator guide, field contract, ARCHITECTURE/README/AGENTS, index/order | [PLAN-remove-syslog-forwarding-phase-06-docs.md](PLAN-remove-syslog-forwarding-phase-06-docs.md) | Complete |

### Landing record

- **Phase 0**'s decisions are the Decisions section above.
- **Phase 1** shipped as `shakenfist-utilities==0.8.5`, released to
  PyPI and pinned here.
- **Phase 4** landed on `shakenfist/actions` as the `loki-ci-tooling`
  branch, merged to `main`.
- **Phase 5** additionally required the `loki-node-checks` branch on
  `shakenfist/actions` to merge before it could go green.
- **Phases 2, 3 and 5** each carried a "pending CI" caveat while they
  were in flight. All three have since merged to `develop`, which the
  merge queue gates on CI, so the caveat is spent rather than dropped.

### Phase notes

- **Phase 0** is a decisions phase in the style of
  `PLAN-health-checks-phase-00-decisions.md`. It resolves every
  Open Question above and commits concrete values: the config
  option names and defaults, the exact Loki label set, the
  full field-name contract (which `with_fields` keys map to
  labels, structured metadata, or JSON body), the auth model,
  whether the spool is forked or shared, the Loki-only vs
  local-fallback decision, the CI Loki-vs-local approach, and a
  sketch of the CI Loki topology. Its output is
  a short design doc plus updated Open Questions answers in this
  master plan. High effort, opus: it is pure judgment and
  cross-system design.

- **Phase 1** works mostly in the sibling `library-utilities`
  repo and makes JSON the *only* daemon log format, not just the
  default. Flip `logs.setup()` so SF daemon logging is JSON
  (either by changing the default of the `json` parameter or by
  having SF pass `json=True` explicitly — phase 0 decides which
  keeps non-SF library consumers safe) and **remove the
  plain-text formatter paths used for daemon output** (the
  syslog/file `TextFormatter` branches at `logs.py:223-231`), so
  no daemon can emit non-JSON lines. Ensure `.with_fields(...)`
  context is emitted as real JSON fields. Define the documented
  field-name contract in the library (this is the producer side
  of the contract phase 0 specified). Decide and implement the
  `setup_console(...)` question from phase 0 (human-readable vs
  JSON for CLI tools). Add the unit tests the `logs` module
  currently lacks, including a test asserting daemon output is
  always valid JSON. Cut a `shakenfist-utilities` release and
  bump the pin in `shakenfist/pyproject.toml`. Note: changes to
  a separately released library plus a pin bump — the management
  session must coordinate the release. Medium–high effort.

- **Phase 2** is the core implementation and lives entirely in
  `shakenfist`. Build `shakenfist/logship_spool.py` and
  `shakenfist/logship_drainer.py` modelled directly on
  `eventlog_spool.py` / `eventlog_drainer.py`: a per-daemon
  SQLite WAL spool under `/srv/shakenfist/spool/logship/`, a
  daemon thread that batches records and POSTs them to Loki's
  `/loki/api/v1/push` endpoint, exponential backoff on failure,
  high-water-mark drop with a counter, orphan recovery, atexit
  drain, and Prometheus depth/drop/push metrics on the existing
  registry. Add a `logging.Handler` subclass that formats each
  record into a Loki stream entry (labels from phase 0, body =
  the JSON record) and enqueues it into the spool. Add the
  config options (`LOKI_BASE_URL` etc., disabled when empty).
  Wire `logship.start(daemon_name)` into
  `daemons/daemon.py:write_pid_file` and
  `external_api/gunicorn_config.py:post_fork`, alongside the
  existing `eventlog_drainer.start(...)` calls. The handler is
  attached only when a Loki endpoint is configured. Unit tests
  for spool, drainer batching/backoff, high-water drop, and the
  push payload shape (mock the HTTP endpoint). High effort,
  opus: crash-safety, backpressure, and per-process lifecycle
  are subtle and the prior art must be matched faithfully.

- **Phase 3** stands up Loki for the functional CI cluster the
  same way MariaDB is provisioned (see `shakenfist/deploy/
  cluster_ci` and how `MARIADB_*` is plumbed). A single-binary
  Loki is sufficient. Plumb its address into `LOKI_BASE_URL`
  for the inner cluster (mind the two-layer CI topology — see
  the project memory). Add a functional test that runs an
  operation generating known log lines and then queries Loki to
  assert they arrived with the expected labels/fields. We
  prefer functional coverage to unit coverage where we can only
  have one, and this is the end-to-end proof that the shipper
  works. High effort: CI rig plumbing across the two SF layers.

- **Phase 4** reworks the CI tooling that today depends on a
  central, syslog-aggregated `/var/log/syslog` on the primary,
  so that phase 5 can delete rsyslog without leaving CI blind.
  Two strands, both done as *new versioned* artefacts so other
  consumers of `shakenfist/actions` are not broken (its actions
  are consumed as `shakenfist/actions/<name>@main` / `@ref`, and
  the `tools/ci_*.sh` scripts are run on the primary via
  `tools/run_remote`):
  - **Log-check scripts.** `actions/tools/ci_log_checks.sh`
    (greps `/var/log/syslog` + `/var/log/syslog.1` for forbidden
    strings) and the inline syslog greps in the main repo's
    `functional-tests.yml` / `scheduled-tests.yml` are replaced
    by new versions that query Loki's `query_range` for the same
    forbidden/required patterns over the run window. Ship them as
    new files / a new ref rather than mutating the existing ones
    in place. (`ci_event_checks.sh` is etcd-centric and is being
    overtaken by the BYO-MariaDB etcd removal; coordinate, don't
    duplicate dead checks.)
  - **Debug bundle (clingwrap).** clingwrap is YAML-driven
    (`.cwd` job configs; job types `directory`/`file`/`shell`/
    `shell_emitter` in `clingwrap/main.py`'s `JOB_MAP`); the
    `shakenfist-ci-failure.cwd` config currently collects
    `/var/log/syslog` as a `file` job. Add a Loki dump — simplest
    is a `shell` job that `curl`s `query_range` for the cluster's
    logs into the bundle (no clingwrap code change), or a small
    new `HttpJob` type if we want it first-class. **Also** keep
    collecting each node's *local* structured logs (journald for
    the `sf*` units, and/or the on-disk spool) so a bundle is
    still useful when the Loki push itself is the failure. Note:
    once rsyslog is gone (phase 5) the `/var/log/syslog` `file`
    job mostly stops being useful — repoint it at journald.
  Decide whether the new `.cwd` config and Loki-query logic live
  in the clingwrap repo (released) or are shipped from
  `shakenfist`/`actions` as a custom target. Medium–high effort:
  spans two external repos and the main repo's workflows.

- **Phase 5** deletes the rsyslog deployer surface enumerated in
  the Situation: `roles/base/tasks/syslog.yml`, the client and
  server `rsyslog-*.conf` templates, the rsyslog package
  install and service enablement in `roles/base/tasks/
  bootstrap.yml`, the server setup in `roles/primary/tasks/
  bootstrap.yml`, the `syslog_target` variable
  (`deploy.yml:185`, `roles/base/defaults/main.yml:13`), and
  the `--log-syslog --log-syslog-prefix sf` flags in
  `sf-api.service`. The `LOKI_BASE_URL` deploy plumbing (getsf →
  deploy.py → the `SHAKENFIST_LOKI_BASE_URL` config-template line)
  is already done in phase 3; phase 5 adds the remaining
  `LOKI_TENANT` / `LOKI_AUTH_HEADER` operator plumbing (mind the
  all-node secret rendering for the auth header) and switches the
  main-repo workflows over to the new Loki-aware CI checks from
  phase 4. Local journald stays (it is free systemd
  behaviour, not a second shipping pipeline — see Open Question
  6). This phase realises remove-primary phase 1; update that
  plan's status table and phase note to point here. Medium
  effort: deletion plus config-rendering, with the new mechanism
  already proven in phases 2–4.

- **Phase 6** writes `docs/operator_guide/logging.md` (BYO-Loki
  contract: config options, labels, tenancy, field-name
  contract, buffering/backpressure/drop semantics, the Loki-only
  vs local-fallback decision, the documented node-agent
  alternative, and what changed vs the old primary-node syslog
  sink). It also documents the **events-vs-logs authoring
  convention** (objects → event, no object → log) and the
  dual-destination reality (MariaDB authoritative eventlog +
  optional Loki echo via `LOG_EVENTS_TO_LOKI`) — primarily in
  `AGENTS.md` / the developer guide for the convention, and in
  `docs/operator_guide/logging.md` for the operator-facing
  "where do my events vs logs live, and how do I interleave
  them" story. Updates `installation.md`, `ARCHITECTURE.md`,
  `README.md`, `AGENTS.md`, this plan's status,
  `docs/plans/index.md`, and `docs/plans/order.yml`.
  Cross-links from `PLAN-remove-primary.md`. Low–medium effort.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this conversation)
is reserved for planning, review, and decision-making. This
keeps the management context lean and avoids drowning it in
implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and
   model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files — the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too
   light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with the
   result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the right
model, that's a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. Note that phase 1 touches the *sibling*
`library-utilities` repo, not this one — that work cannot share
a worktree of this repo and must be coordinated as a separate
checkout/release.

### Planning effort

The master plan itself is created at **high effort**. Per-phase
recommended planning effort:

- **Phase 0** — high (opus): pure cross-system design and
  judgment.
- **Phase 1** — medium–high: library code is small but the
  field contract and the JSON-default switch have
  backward-compatibility implications for non-SF consumers.
- **Phase 2** — high (opus): crash-safe buffering, backpressure,
  per-process lifecycle, faithful reuse of the eventlog
  pattern.
- **Phase 3** — high: CI rig plumbing across the two-layer SF
  topology.
- **Phase 4** — medium–high: spans `shakenfist/actions` and
  clingwrap plus the main-repo workflows; new versioned
  artefacts and Loki query logic.
- **Phase 5** — medium: mostly deletion plus config rendering
  and switching CI over to the phase 4 checks.
- **Phase 6** — low–medium: documentation.

### Step-level guidance

Each phase plan should include a step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | high   | opus   | worktree | Build logship_spool.py mirroring eventlog_spool.py: ... |
| 2b   | medium | sonnet | none     | Wire logship.start() into daemon.py and gunicorn_config.py: ... |
```

**Effort levels:**
- **high** — Requires reading multiple files, judgment calls,
  non-obvious invariants (crash-safety, backpressure,
  per-process lifecycle), or external research (Loki push API).
- **medium** — The plan provides enough context to follow a
  clear brief; may need to read a few files.
- **low** — Purely mechanical (delete a template, remove a
  flag, add a config option, regenerate stubs).

**Model choice:**
- **opus** — phase 0 design, the phase 2 spool/drainer/handler,
  CI plumbing in phase 3.
- **sonnet** — well-briefed wiring, deletion, and
  config-rendering steps.
- **haiku** — pure mechanical deletions/renames with a complete
  instruction.

**When in doubt, skew to the more capable model.** A failed
implementation wastes more time than a heavier model costs. Only
recommend a lighter model when the brief is detailed enough for
it to succeed.

A good brief front-loads the research the planner already did.
For phase 2, that means: "mirror `eventlog_spool.py` — SQLite
WAL spool under `/srv/shakenfist/spool/logship/<daemon>-<pid>.db`,
`SPOOL_HIGH_WATER_MARK` drop-with-counter, orphan recovery on
startup; mirror `eventlog_drainer.py:161-319` for the daemon
thread, `DRAIN_BATCH_SIZE`/poll/backoff constants, and the
`atexit` drain; expose Prometheus `logship_spool_depth` /
`logship_spool_dropped_total` / `logship_push_*` on the default
registry as `eventlog_spool.py:50-56` does; start it from
`daemons/daemon.py:113` and `external_api/gunicorn_config.py:56`
next to `eventlog_drainer.start(...)`."

### Management session review checklist

After a sub-agent completes, verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] For phase 1, the `shakenfist-utilities` release was cut
      and the pin in `shakenfist/pyproject.toml` was bumped to
      match.
- [ ] The changes match the intent of the brief — semantically
      right, not just syntactically correct. In particular the
      logship spool must preserve the eventlog spool's
      crash-safety and drop-don't-block posture.
- [ ] Commit message follows project conventions (including the
      Co-Authored-By line with model, context window, effort
      level, and other settings).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented because
the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8, stestr
  unit tests, and mypy type checking).
* SF daemons emit structured JSON for *every* log line — JSON is
  the only daemon log format, the plain-text formatter paths are
  gone, and `.with_fields(...)` context is carried as real
  fields against a documented field-name contract.
* SF ships logs to Loki via an in-process, on-disk-spooled,
  batched push that is crash-safe, applies backpressure by
  dropping at a high-water mark (with a counter + WARN), retries
  with exponential backoff, recovers orphaned spools, and drains
  on shutdown — matching the eventlog spool/drainer guarantees.
* When a Loki endpoint is configured the shipper is the sink
  (no deliberate second local pipeline); the on-disk spool is
  the local-durability buffer across transient outages. The
  tenant (`X-Scope-OrgID`) is operator-configurable so SF logs
  do not co-mingle with other tenants' logs.
* The Loki shipper is disabled when no endpoint is configured,
  enabling it is a single operator config value, and with it
  disabled SF still emits structured JSON locally (a dev/single
  box is never silent).
* CI exercises both paths: a Loki standup plus a functional test
  asserting SF log lines arrive in Loki end-to-end (the push
  path), and coverage that the disabled-shipper path emits local
  JSON. The CI log-checks and clingwrap debug bundle read from
  Loki and from each node's local logs, shipped as new versioned
  `shakenfist/actions` artefacts that do not break existing
  consumers.
* New code follows existing patterns: the spool/drainer mirrors
  the eventlog prior art; config options follow the BYO pattern
  in `config.py`; Prometheus metrics use the process-wide
  registry.
* The deployer no longer installs or configures rsyslog and no
  longer passes `--log-syslog` to gunicorn; `syslog_target` and
  the primary's rsyslog role content are gone.
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* Documentation in `docs/` describes the new BYO-Loki logging
  contract (`docs/operator_guide/logging.md`), and
  `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` are updated.
* `PLAN-remove-primary.md` phase 1 is marked complete with a
  pointer here.

### Future work

* **Unify the eventlog and logship spools** behind one reusable
  durable-spool primitive (phase 0 deliberately forks them for
  lower risk).
* **mTLS / TLS to the Loki endpoint**, consuming the BYO-PKI
  surface from `PLAN-embrace-tls.md`.
* **Per-namespace log tenancy** (per-tenant `X-Scope-OrgID`),
  which intersects authz and is much larger than the
  cluster-wide tenant shipped here.
* **Client-side fan-out across multiple Loki endpoints** if
  operators want SF to balance across Loki replicas rather than
  rely on their own ingress.
* **OpenTelemetry / tracing** instrumentation is a separate
  thread; align the field-name contract with it when it lands.
* **Revisit shipping DEBUG centrally (with OpenTelemetry).** v1
  ships only `INFO`+ to Loki and keeps `DEBUG` local to each
  node's journald — matching the old rsyslog `*.!=debug`
  forwarder and keeping the high-volume DEBUG stream off the
  spool/push path (a measured performance win: shipping every
  DEBUG line via the per-daemon sqlite spool slowed the
  multi-node CI cluster ~2x). The cost is that deep diagnosis of
  a past incident on a now-recycled/disposable node loses its
  DEBUG detail. Once SF has OpenTelemetry-based tracing — a
  better-suited transport for high-volume, sampleable diagnostic
  detail — reconsider centrally capturing DEBUG/trace-level data
  so on-node access is not required. The level cut lives in
  `shakenfist/logship.py` (`start()` sets the Loki handler to
  `INFO`); revisiting it means changing that level and re-checking
  the volume/perf impact.
* **A first-class clingwrap `HttpJob`** (instead of a `shell`
  job shelling out to `curl`) if the Loki-dump collector proves
  worth making reusable across other HTTP-fetched diagnostics.
* **A Grafana dashboard that interleaves events and logs** by
  putting a MariaDB-datasource events panel alongside a Loki
  logs panel, time-aligned — the way to recover a single-pane
  operational view *without* echoing events into Loki, for
  operators who run with `LOG_EVENTS_TO_LOKI` off.
* **Metrics shipping** is explicitly *not* in scope — SF already
  exposes Prometheus endpoints for the operator to scrape; this
  plan is logs only.

### Bugs fixed during this work

To be filled in as we go. At the start of each phase, scan the
`shakenfist/shakenfist` GitHub issue tracker for open issues
related to logging, syslog, or log forwarding and either resolve
them as part of the relevant phase or note them here.

### Documentation index maintenance

When this master plan is created from the template:

* **`docs/plans/index.md`** — add one row to the *Master
  plans* table for this plan, with a link, its status and a
  one-line intent. Phases are tracked in the Execution table
  above, not in the index. Add a
  line to the *Plan sequencing* section noting that this plan
  delivers the Loki-shipper story remove-primary phase 1 depends
  on, and that it can land in parallel with the other BYO work.
* **`docs/plans/order.yml`** — add an entry for this master
  plan, in the intended reading order. Phase
  files are *not* added to `order.yml`; they are linked from the
  Execution table only, which makes that table the only path
  to them.

`order.yml` does not by itself reach this repository's site
navigation: `mkdocs.yml.tmpl` lists the Plans nav by hand.
The entry is registration and reading order.

When all phases are complete, update the status column in
`docs/plans/index.md` and mark `PLAN-remove-primary.md` phase 1
complete.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
</content>
</invoke>
