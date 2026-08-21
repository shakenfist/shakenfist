# Health checks, readiness, and graceful drain for SF daemons

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (daemon
startup paths in `shakenfist/daemons/*/main.py`, the
`node_daemon_states` table and the daemon-state mechanism
in `shakenfist/daemons/daemon.py` and `shakenfist/mariadb.py`,
the gRPC server construction in `shakenfist/daemons/database/`
and the other gRPC daemons, the Flask app construction in
`shakenfist/external_api/app.py`, the SIGTERM / shutdown paths
in each daemon). Ground your answers in what the code does
today. Do not speculate when you could read it instead. Where
a question touches on external concepts (HTTP health-check
conventions, the gRPC Health Checking Protocol from
`grpc.health.v1`, load-balancer health-check expectations
across HAProxy / nginx / cloud LBs, Kubernetes' liveness /
readiness / startup-probe semantics as a vocabulary reference),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the daemon inventory and gRPC
channel structure. Consult `CLAUDE.md` for build commands,
project conventions, and the `node_daemon_states` table
description. Key references inside the repo include the
per-daemon `main.py` files in `shakenfist/daemons/*/`, the
shared daemon utilities in `shakenfist/daemons/daemon.py`,
the gRPC server bootstrap in `shakenfist/daemons/database/`,
the Flask app in `shakenfist/external_api/app.py`, and the
existing `node_daemon_states` writes in `shakenfist/mariadb.py`.

**Status: complete.** All five phases (0–4) have landed on the
`health-checks` branch. Phase 0 resolved the open questions into
the Decisions section below; phases 1–4 implemented them. The
text below is preserved as the plan of record; see the Decisions
section and the per-phase plan files for what was built.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named with `-phase-NN-descriptive`
appended.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained.

## Situation

`PLAN-remove-primary.md` moves Shaken Fist toward a model
where operators provide their own load balancer in front of
sf-api and run their own monitoring and log pipelines. The
moment that lands, operators need *health-check endpoints*
they can configure their LB and monitoring against. SF does
not currently expose those endpoints in a useful, consistent
shape.

Today the closest thing SF has to a health-check surface is:

- The `postinstall.yml` sanity check
  (`shakenfist/deploy/ansible/roles/primary/tasks/postinstall.yml`)
  curls `http://localhost:13000/auth/namespaces` and expects
  a 401. That confirms "the API is responding to *something*"
  but conflates liveness with readiness with auth.
- The `node_daemon_states` table (one row per
  `(node_uuid, daemon)`, per `CLAUDE.md`) records per-daemon
  startup and shutdown values written via direct MariaDB
  updates. It is an *eventual* signal — the daemon writes
  state, observers read it later — and so is unsuitable as
  an LB health-check substrate where the LB needs a
  real-time answer on every probe.
- The `Daemon` base class (`shakenfist/daemons/daemon.py`)
  *does* already wire systemd `Type=notify`: `record_start()`
  sends `READY=1` and writes `DAEMON_STATE_RUNNING`;
  `exit_gracefully()` (the SIGTERM handler) writes
  `DAEMON_STATE_STOPPING` and drops a `/run/sf/<daemon>.abort`
  file the main loop polls; `record_exit()` sends `STOPPING=1`
  and writes `DAEMON_STATE_STOPPED`. `WorkerPoolDaemon`
  additionally waits (in ~5s batches) for its worker threads
  to drain on shutdown. So a SIGTERM-to-exit lifecycle exists
  — what is missing is (a) an LB-facing readiness *flip* that
  happens *before* shutdown work begins, and (b) draining
  *in-flight request* connections rather than just worker
  threads. No `WATCHDOG=1` liveness heartbeat is wired.
- `grpc.health.v1.Health` already exists, but only on
  **sf-database**, and minimally: it was added by
  `PLAN-byo-mariadb.md` phase 3 (see
  `shakenfist/daemons/database/main.py` around the server
  bootstrap and `shakenfist/tests/test_database_health.py`).
  It registers only the empty-string overall-server service,
  supports only `Check` (not `Watch` — client-side Watch was
  removed after it deadlocked; see the comments in
  `shakenfist/util/grpc_channel.py`), and flips SERVING →
  NOT_SERVING only at startup / before graceful stop. It is a
  shutdown-signalling tool, not a dependency-aware readiness
  probe. **sf-database is the only daemon that runs a
  `grpc.server()` at all** — privexec and nodelock speak
  protobuf over unix sockets, sidechannel over vsock, and
  transfers over raw TCP; none implement `grpc.health.v1`.

A LB pointed at sf-api today gets a useful answer only by
probing an auth-protected endpoint and treating "401" as
healthy. That works as a hack; it is wrong as a contract.
And while a SIGTERM-to-exit path exists, there is no story
for *graceful drain that an LB participates in*: readiness
does not flip ahead of shutdown, so operators rolling daemons
during upgrade have no way to take a node out of the LB pool
before it stops serving.

## Mission and problem statement

SF exposes health in the vocabulary Kubernetes-style
operators already use — three semantics, but with very
different *scopes*, because only one SF surface is ever
load-balancer-routable:

- **liveness:** the process is running and its main loop
  is not deadlocked. Used by the *orchestrator* (systemd)
  to decide "restart this thing." This is the **universal**
  primitive — every non-trivial daemon has a main loop that
  can wedge, so every non-trivial daemon needs liveness
  (the natural carrier is the systemd `WATCHDOG` signal,
  petted by the main loop; see open question 11).
- **readiness:** the daemon's dependencies are satisfied and
  it is ready to *serve client traffic*. Used by the
  **load balancer** to decide "route here." This applies
  **only to sf-api** — see the routing principle below.
- **drain:** on SIGTERM, readiness flips to "not ready"
  immediately, the LB removes the node from its pool on its
  next health-check cycle, in-flight requests complete
  within a configurable grace period, and only then the
  process exits. Like readiness, this is an **sf-api**
  concern; everything else just needs a clean
  liveness-driven stop.

**Routing principle — the LB probes exactly one surface.**
The operator's load balancer routes external client traffic
to exactly one service: **sf-api's REST API** (HTTP, port
13000). It is the only load-balancer-routable HTTP surface
in SF. Everything else is internal: sf-database speaks gRPC
to peers, blob transfers use a homegrown TCP protocol,
and the queue-worker and elected daemons service MariaDB
queues and present no client-facing API at all. The
Prometheus `/metrics` endpoints on sf-database, sf-cluster
and sf-resources are operator *scrape* targets, not
LB-routed services. Therefore **`/readyz` and drain belong
to sf-api alone**; gRPC health on sf-database is for peer
connection management; and every other daemon needs only
liveness (restart) plus, for elected daemons, lock
proof-of-life (open question 11).

Concretely:

- **sf-api** (gunicorn, port 13000, separate `sf-api.service`)
  exposes `/livez` and `/readyz` (and a `/healthz` alias) on
  its existing HTTP port, unauthenticated — registered the
  way the already-unauthenticated `Root` resource in
  `shakenfist/external_api/app.py` is. Probes never touch the
  database directly; readiness reads cached dependency state
  updated by a background checker, not a per-probe gRPC call
  to sf-database.
- **sf-database** (the only `grpc.server()` daemon) keeps and
  *extends* its existing `grpc.health.v1.Health` service from
  dependency-blind shutdown-signalling to a dependency-aware
  readiness status, so peers and operators can use the
  standard `grpc-health-probe` tool. (There is no sf-eventlog
  daemon — eventlog is library code in `shakenfist/eventlog.py`
  and `PLAN-eventlog-direct-mariadb.md` removes even that
  proxying role. The earlier draft of this plan named a
  phantom sf-eventlog; phase 0 corrects the inventory.)
- **Worker / periodic daemons** (sf-cleaner, sf-queues,
  sf-net, sf-resources, sf-transfers, sf-sidechannel) do not
  run an HTTP or gRPC server today — they are queue workers
  and periodic pollers, and the routing principle says they
  are never LB-probed. Their only health need is **liveness**
  (so systemd restarts a wedged loop), carried by the
  `WATCHDOG` signal. They do **not** get `/readyz`, and there
  is **no per-node readiness aggregator for the LB** — the LB
  has nothing on these nodes to route to. Open question 10's
  classification gates which of them are non-trivial enough
  to bother wiring `WATCHDOG` at all.
- **Elected daemons** — in practice just **sf-cluster** (it
  holds a `ClusterLock` via `_await_election`; one active per
  cluster). It services MariaDB queues and runs periodic
  maintenance under the lock; it presents **no REST or gRPC
  service**, so it is never LB-routed and has **no readiness
  probe**. What it needs is liveness *plus lock proof-of-life*
  (open question 11): a wedged-but-alive holder must lose its
  lease so a standby can take over. (Note: sf-database is
  **not** an elected daemon — `PLAN-byo-mariadb.md` already
  made it a stateless tier of equals reached by client-side
  gRPC LB, not a leader. The earlier draft's "elected
  sf-database after remove-primary phase 5" premise is
  obsolete; phase 0 records this.)
- **SIGTERM** causes readiness to flip to 503 *before* any
  shutdown work begins (extending the existing
  `exit_gracefully()` path, which today writes
  `DAEMON_STATE_STOPPING` but does not flip a real-time
  probe), then a grace period (default and configuration per
  phase 0), then orderly shutdown. The grace period must
  reconcile with the existing systemd `TimeoutStopSec=30s`
  cap and gunicorn's `--timeout 300` worker timeout, which
  currently disagree.

The principle is: **health is a real-time probe, not a
state read.** `node_daemon_states` continues to serve its
eventual-consistency cluster-state role and is not the
substrate for LB probes. The two are orthogonal.

## Open questions

This plan is partial; phase 0 will resolve at least the
following:

1. **Per-daemon ports vs shared per-node endpoint.**
   *Largely resolved by the routing principle, to be
   ratified in phase 0.* The shared per-node **readiness
   aggregator for the LB** is no longer wanted — the LB only
   routes to sf-api, so there is nothing per-node for it to
   aggregate. What remains is **liveness**, and the natural
   carrier is the systemd `WATCHDOG` signal (no HTTP listener,
   no extra port, no circular dependency on sf-resources)
   rather than a per-daemon HTTP endpoint. Phase 0 confirms
   `WATCHDOG` is sufficient and that we are *not* adding tiny
   HTTP listeners across the worker daemons.
2. **HTTP vs gRPC health protocol per daemon.** Mostly
   resolved by the routing principle. sf-database (the only
   gRPC-server daemon) keeps `grpc.health.v1.Health` — it's
   the standard, `grpc-health-probe` exists, and the servicer
   is already wired. The worker daemons get neither HTTP nor
   gRPC health, only `WATCHDOG` liveness (see open question 1).
   The one genuinely-open sub-question: does sf-database
   *also* need an HTTP health surface? Under the routing
   principle the answer is **no for the LB** (the LB never
   routes to sf-database), so HTTP health on sf-database would
   only be a convenience for an operator who wants to probe it
   with curl instead of `grpc-health-probe`. Phase 0 decides
   whether that convenience is worth any code at all. This
   also lines up with the future-work trajectory (REST as its
   own tier, hypervisors gRPC-only): HTTP health stays with
   the one HTTP-serving tier.
3. **Readiness dependency model.** Each daemon enumerates
   what it depends on. sf-api depends on sf-database being
   reachable; sf-database depends on MariaDB being
   reachable; sf-net depends on sf-privexec; etc. The
   probe must be cheap (cached, refreshed by a background
   goroutine), not expensive (querying MariaDB on every
   probe would amplify LB-probe traffic into DB load).
   Phase 0 produces the dependency graph and the cache /
   refresh semantics.
4. **Readiness for elected daemons.** *Resolved, pending
   phase-0 ratification.* The original framing — a leader /
   standby sf-database whose `/readyz` an LB reads to route to
   the leader — is obsolete on two counts: sf-database is a
   stateless tier of equals (not elected), and the only
   actually-elected daemon, sf-cluster, presents no API and is
   never LB-routed. So **elected daemons have no readiness
   probe.** Their election-related health need is
   *liveness-with-lock-failover*, which is open question 11
   (a wedged holder must shed its lease). There is no
   leader/standby HTTP body and no LB-directs-to-leader
   behaviour to design.
5. **Drain grace period.** Default value? Configurable per
   daemon or cluster-wide? What about long-running requests
   (e.g., a blob upload mid-stream) that won't complete
   within any reasonable grace? Probably needs both a
   configurable grace and a per-request "drainable" flag.
6. **Authentication on health endpoints.** Public
   unauthenticated is normal for LB probes but means
   anyone can scan for SF clusters by probing
   `/livez` on 13000. Mitigations: restrict probes to
   mesh-IP source addresses, document operator firewall
   expectations, or put health on a separate port that's
   only opened to the LB. Phase 0.
7. **Interaction with `node_daemon_states`.** Today daemons
   write startup / shutdown rows there. After this plan,
   health is *also* visible via real-time probes. The two
   should be consistent. Phase 0: should the real-time
   probe write a heartbeat into `node_daemon_states` too,
   or is that table reserved for orderly state transitions
   and not health pulses?
8. **Startup probe semantics.** First-boot bootstrap can
   take real time (sf-database waits for MariaDB schema;
   sf-api waits for sf-database; everyone waits for cluster
   config). Kubernetes-style "startup probe" decouples
   "still initialising" from "stuck" — for an operator's
   LB, the same effect is just "readiness stays 503 until
   ready," which is correct here. Confirm in phase 0 that
   we don't need a separate startup endpoint.
9. **Interaction with `PLAN-embrace-tls.md`.** *Largely
   resolved by the routing principle, with one operator-doc
   nuance for phase 4.* There are two decoupled PKI trust
   domains: the **edge** cert the REST endpoint presents to
   external clients (operator's public CA / ACME, terminated
   at the LB) and the **mesh** mTLS that embrace-tls puts on
   the gRPC channels (internal CA / machine identity). They
   *may* converge later if SF grows a unified machine-identity
   CA, but they need not, and this plan assumes they are
   separate. Because the LB probes **only sf-api**, the health
   probe is just another request on whatever backend leg the
   LB already uses to route REST traffic to sf-api — it
   inherits that leg's TLS posture (terminate-and-plaintext on
   a trusted private net, or terminate-and-re-encrypt trusting
   a backend CA — the operator's choice). gRPC health on
   sf-database likewise rides the same mesh-mTLS channel peers
   already use. So **no dedicated plaintext health port and no
   health-only client cert are needed** — the earlier draft's
   answer is dropped. The only residual is a phase-4
   documentation note: under L4 TLS *passthrough* (LB can't
   read an HTTP status on the backend) the operator must use a
   TCP-level probe or terminate at the LB; SF does not need to
   build anything for this.
10. **Daemon inventory classification.** There are presently
    thirteen `sf-*` systemd units, and the operator's
    standing question is whether that count is itself a
    mistake to be cleaned up. This plan does not consolidate
    daemons, but it must not silently build health-check
    surface for daemons that should not exist. Phase 0
    therefore produces a classification of every daemon into
    one of three buckets, as an explicit artifact:
    - **sentinel / trivial** — pure systemd-ordering or
      lock-holding units (`sentinel-first`, `sentinel-last`,
      and any others that serve no requests) that need *no*
      health surface at all; the decisions document records
      why each needs nothing.
    - **permanent boundary** — daemons whose separateness is
      load-bearing and not a candidate for merging, with the
      reason stated (e.g. `privexec` is a privilege-
      separation boundary; `database` is a deliberate tier
      per `PLAN-byo-mariadb.md`).
    - **merge candidate** — daemons thin enough that a future
      `PLAN-consolidate-daemons.md` might fold them together;
      flagged here, *not* acted on here.
    This classification both scopes the health-check work
    (only non-trivial daemons get a probe) and becomes the
    evidence base for a later, separate consolidation
    decision. It does not block any health-check phase.
11. **Proof-of-life and cluster-lock holding.** The
    pre-MariaDB cluster suffered a bug where the election
    winner died without releasing its lock and the cluster
    went weird; the leased `ClusterLock`
    (`shakenfist/locks.py`, server-side `expires_at` +
    20s refresher + steal-after-60s) already fixes that for
    *process death*. The residual gap is the liveness /
    process-existence distinction this plan is about: the
    refresher is an independent daemon thread
    (`_refresh_loop`) that renews the lease as long as it can
    reach MariaDB, with **no awareness of whether the
    holder's main work loop is making progress**. A wedged-
    but-alive elected daemon therefore keeps its lock forever.
    Phase 0 decides how health-check liveness closes this:
    - **Preferred / low-risk:** wire systemd `WATCHDOG=1`
      (currently unwired — `daemon.py` sends only `READY=1` /
      `STOPPING=1`) with the daemon's main loop as the thing
      that pets it. A wedged loop then fails to pet the
      watchdog, systemd `SIGKILL`s the process, the refresher
      dies with it, and the existing lease-expiry path
      performs failover. No change to `locks.py`.
    - **Belt-and-suspenders (separate decision):** have the
      refresher consult the same main-loop liveness heartbeat
      and stop renewing when it goes stale, shedding the lock
      without killing the whole process. This is a
      correctness change to a load-bearing primitive and
      carries election-thrash risk (a long legitimate
      iteration must not drop the lease), so if adopted it
      gets its **own isolated phase** with hysteresis tests —
      it is not folded into a daemon health phase.
    Either way, the *liveness heartbeat primitive* (main-loop
    progress, feeding `/livez` and `WATCHDOG`) is in scope for
    this plan; coupling it to lock renewal is the part phase 0
    gates.

## Execution

Phase 0 is complete; the table below is re-cut against its
Decisions section above and is no longer provisional.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | [PLAN-health-checks-phase-00-decisions.md](PLAN-health-checks-phase-00-decisions.md) | Complete |
| 1. sf-api `/livez` `/readyz` `/healthz` + readiness checker + SIGTERM drain | [PLAN-health-checks-phase-01-sf-api.md](PLAN-health-checks-phase-01-sf-api.md) | Complete |
| 2. Dependency-aware `grpc.health.v1` on sf-database | [PLAN-health-checks-phase-02-grpc-health.md](PLAN-health-checks-phase-02-grpc-health.md) | Complete |
| 3. `WATCHDOG` liveness wiring (worker + elected daemons) | [PLAN-health-checks-phase-03-watchdog.md](PLAN-health-checks-phase-03-watchdog.md) | Complete |
| 4. Operator documentation and LB-config examples | [PLAN-health-checks-phase-04-operator-docs.md](PLAN-health-checks-phase-04-operator-docs.md) | Complete |

Notes on sequencing:

- **Phase 0 is decisions.** No code. Output is appended to
  this master plan as a "Decisions" section and the phase
  table is re-cut against it. One required artifact of that
  section is the daemon inventory classification from open
  question 10 (sentinel / permanent boundary / merge
  candidate), which scopes which daemons get a probe at all.
- **Phase 1 is the canary.** sf-api is the most operator-
  visible daemon and the one the LB actually probes; if
  the pattern doesn't work for sf-api it doesn't work
  anywhere. Land it first and use it as the template.
- **Phase 2 is the gRPC pattern.** The `grpc.health.v1.Health`
  servicer already exists on sf-database (byo-mariadb phase
  3); phase 2 *evolves* it from "SERVING at startup,
  NOT_SERVING before stop" into a dependency-aware readiness
  status. Two hard constraints carry over from that earlier
  work and must be honoured: **do not reintroduce `Watch`**
  (it deadlocked the synchronous servicer / single
  event-dispatch thread and was deliberately removed), and
  keep the client channel factory in
  `shakenfist/util/grpc_channel.py` free of `healthCheckConfig`
  (it relies on keepalive + connectivity state for failover,
  by design). The interesting design work is the dependency
  model for sf-database (is MariaDB reachable? is the schema
  at the expected version?) — *not* election semantics:
  sf-database is a stateless tier of equals, not a leader, so
  there is no leader/standby readiness to model.
- **Phase 3** is now small. With the routing principle in
  place, "remaining daemons" means wiring the systemd
  `WATCHDOG` liveness signal (open question 11) into the
  non-trivial worker and elected daemons — there are no
  `/readyz` endpoints or per-node aggregators to build. Phase
  0's classification says which daemons are in scope.
- **Phase 4 writes the operator-facing docs**: HAProxy /
  nginx / Envoy / cloud-LB example configs pointing at the
  endpoints, plus the rolling-upgrade procedure that uses
  them.

## Dependencies on other plans

- **`PLAN-remove-primary.md`:** parallel-compatible. This
  plan delivers the surface the BYO-LB story in
  remove-primary needs; ideally it finishes before
  remove-primary phase 6 (galaxy role) so the role's
  documentation can point at well-defined endpoints. The
  per-plan sequencing in `index.md` puts health-checks
  before remove-primary for exactly this reason.
- **`PLAN-embrace-tls.md`:** interaction is now minimal (see
  open question 9). The health probe rides the same LB→sf-api
  backend leg as REST traffic and gRPC health rides the mesh
  mTLS channel, so health checks add no TLS surface of their
  own and impose no constraint the TLS plan must honour. The
  two PKI domains (edge vs mesh) stay decoupled; embrace-tls
  owns the mesh one.
- **`PLAN-byo-mariadb.md` (sf-database as a stateless
  tier):** already landed, and it removes a dependency this
  plan once thought it had. There is no sf-database leader
  election, so phase 2's gRPC-health work has no election
  shape to wait on and can proceed independently. (This
  supersedes an earlier dependency on a since-abandoned
  "remove-primary phase 5 sf-database election.")

## Decisions (phase 0)

Phase 0 resolved every open question against the code. The
values below are **committed** and bind the implementation
phases; each rests on cited code, with fuller working in the
phase-0 sub-agent records. Where a value carries an assumption
(e.g. the LB probe interval) it is flagged.

### Daemon classification (OQ10)

| Daemon | Bucket | Transport | Health surface | Reason |
|--------|--------|-----------|----------------|--------|
| sentinel-first / sentinel-last | sentinel/trivial | none | none | Pure systemd-ordering; mark node state only (`sentinel_first/main.py:41`). |
| nodelock | node-local boundary | unix socket | none (probed by consumers) | Node-local lock server; health already exercised by `health_check_nodelock()` (`daemon.py:143`) and the sf-queues supervisor (`queues/main.py:22-74`). |
| privexec | permanent boundary | unix socket | none (probed by consumers) | Privilege-separation boundary; probed via `health_check_privexec()` (`daemon.py:126`). |
| database | permanent boundary (gRPC tier) | gRPC + Prom metrics | grpc.health.v1 (primary) + WATCHDOG | Deliberate tier per `PLAN-byo-mariadb.md`; already serves `Check` (`database/main.py:5230`). |
| cleaner, queues, network, resources, transfers, sidechannel | worker/periodic | none / vsock / TCP | WATCHDOG | Queue workers and periodic pollers; never LB-routed. |
| cluster | elected | none | WATCHDOG + lock proof-of-life | Single elected maintainer via `_await_election` (`cluster/main.py:53`). |

*Merge candidates* (advisory only, for a future
`PLAN-consolidate-daemons.md`; not acted on here):
sentinel-first + sentinel-last (near-identical ~56-line
loops), and nodelock + privexec (both node-local unix-socket
protobuf servers).

### sf-api readiness (OQ3)

Per-worker in-memory ready flag + timestamp, maintained by a
background checker started in gunicorn's `post_fork` hook
(`gunicorn_config.py:23` — threads do not survive the
`--preload` fork, so the checker must start post-fork, not at
import). `/readyz` reads only the flag, so a probe burst makes
**zero** DB/gRPC calls. Single dependency edge: **sf-api ready
⇔ sf-database `Check('')` SERVING**, which phase 2 makes mean
MariaDB-reachable + schema-current. No other hard runtime
dependency (the auth secret is an import-time invariant;
NODE_UUID is best-effort). Committed values: poll **5s**,
per-`Check` timeout **2s**, hysteresis **K=3** failures →
not-ready and **1** success → ready, staleness bound **15s**
(a stale flag returns 503, catching a dead checker thread).
Health routes reuse `_is_health_probe()` (`app.py:125`) to
bypass the eventlog audit hooks.

### sf-api drain (OQ5)

On SIGTERM: flip the per-worker draining flag (→ `/readyz`
503) **first**, keep serving for `API_DRAIN_GRACE`, then let
gunicorn finish in-flight work, then exit. Seam: a SIGTERM
handler installed in gunicorn's **`post_worker_init`** hook
(`post_fork` is clobbered by gunicorn's `init_signals`;
`worker_int` only fires on SIGQUIT, which systemd does not
send). The handler arms a deadline via a **timer thread**, not
`sleep()` in the handler, so requests keep being served during
the drain. New knob **`API_DRAIN_GRACE` = 25s** (`config.py`).
Reconciled timeouts on `sf-api.service`: **`TimeoutStopSec`
30s → 70s**, add gunicorn **`--graceful-timeout 30`**,
**`--timeout 300` unchanged** (it is the per-request worker
watchdog, *not* the shutdown grace). Invariant:
`TimeoutStopSec > API_DRAIN_GRACE + graceful_timeout +
margin`. The generic `sf.service` is unchanged. Long requests
(mid-stream blob upload) are not gracefully drained — the
client retries; a per-request "drainable" flag is future work.
*Assumption:* 25s/70s assume an LB probe interval ≈10s;
re-derive if the deployed LB differs.

### sf-api endpoints and auth (OQ2 surface, OQ6)

Three unauthenticated GET routes on port 13000, registered
like the existing `Root` resource (no `@verify_token`;
`app.py:223`): **`/livez`** (always 200 `ok`, no dependency
check), **`/readyz`** (200 `ready` / 503 `not ready` from the
cached flag), **`/healthz` ≡ `/readyz`** (documented alias — an
LB hitting the conventional `/healthz` wants a routing =
readiness answer). Minimal `text/plain` bodies, no
version/topology leak — strictly less than the unauthenticated
`Root` API catalogue already exposes (version lives behind
`/apidocs`). Extend `_is_health_probe()` and the `log_request`
`path=='/'` downgrade (`base.py:587`) to the health set. **No
separate health port**; the operator firewalls 13000 to the LB
subnet (documented, not built). **sf-database gets no HTTP
health** — `grpc-health-probe` suffices and nothing LB-routes
to it (closes OQ2's residual).

### Liveness watchdog and lock proof-of-life (OQ11)

Emit `WATCHDOG=1` via the existing notify helper
(`daemon.py:354`) from the base-class `idle()` tick — already
a 0.2s internal loop (`daemon.py:322`), so it covers long
logical sleeps like cleaner's `idle(60)` trivially —
rate-limited to ~10s. **`WatchdogSec=60s`** in `sf.service`
(`Restart=on-failure` is already set; a missed watchdog →
SIGABRT → restart). Pets must **also** be added inside the
heavy *work* iterators of cleaner (`_maintain_blobs` glob) and
cluster (`_cluster_wide_cleanup` per-blob/artifact/node
loops), and per dispatch tick in `WorkerPoolDaemon` (which
does not route through `idle()`) — a single work pass on a
large node can otherwise exceed 60s. Lock proof-of-life chain:
a wedged cluster daemon stops petting → systemd kills it → its
independent lease refresher thread (`locks.py:199`, renews
every `REFRESH_INTERVAL=20s`) dies → the lease
(`CLUSTER_LOCK_LEASE_SECONDS=60`, `constants.py:21`) expires →
a standby steals it. Worst-case failover ≈ `WatchdogSec` +
lease ≈ **120s**. **No `locks.py` change.** The
belt-and-suspenders option (refresher sheds the lease on
heartbeat staleness, without waiting for the kill) is
**deferred** to its own micro-plan with hysteresis tests.

### node_daemon_states and startup (OQ7, OQ8)

**OQ7:** the real-time probe does **not** write
`node_daemon_states`. That table stays the orderly-transition
/ kill-signal substrate (read by `check_daemon_state()`
`daemon.py:299`, `get_degraded_daemons()` `node.py:430`,
`external_view()`, and `sf-ctl`); health pulses there would
pollute the degraded-node logic and could mask a STOPPING
signal. The two are orthogonal — eventual cluster state vs
real-time routing/liveness. **OQ8:** no separate startup probe
— `/readyz` staying 503 until ready already distinguishes
startup from stuck. Operator-doc note: set the LB start-period
≥60s and healthy-threshold to 2. No `API_READYZ_START_PERIOD`
knob is needed: schema migration is **not** in the daemon
startup path. `PLAN-byo-mariadb.md` (landed) already moved
migration to the explicit, deployer-run `sf-ctl
ensure-mariadb-schema`; sf-database only runs the fast
`verify_mariadb_compat` + `verify_schema_versions` checks
(`daemons/database/main.py:5157-5169`) and **refuses to start**
on mismatch. So sf-database is either up with a current schema
(SERVING) or not running at all — readiness is never gated on
a migration, and "schema is current" is a startup precondition
rather than something `/readyz` waits through. (An earlier
phase-0 note wrongly assumed the daemon migrates at startup.)

### Ratifications (OQ1, OQ4, OQ9)

- **OQ1:** no per-node readiness aggregator; worker/elected
  liveness is carried by `WATCHDOG`, and no new HTTP listeners
  are added to any worker daemon.
- **OQ4:** elected daemons have no readiness probe; sf-cluster
  presents no client API and is never LB-routed; sf-database
  is a stateless tier of equals, not a leader, so there is no
  leader/standby readiness to model.
- **OQ9:** health adds no TLS surface of its own — the probe
  rides the LB→sf-api backend leg and gRPC health rides the
  mesh channel. No dedicated health port or health-only cert.
  Phase-4 doc note covers L4 passthrough only.

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
risky or experimental. Phase 1 (sf-api drain) and phase 2
(reshaping the live sf-database health servicer) touch
running request paths and the gRPC bootstrap, so they should
default to worktree isolation. Phase 0 (no code) and phase 4
(docs) can work directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making judgment
calls about scope and sequencing.

Each phase plan specifies the recommended effort level for
planning that phase:

- **Phase 0 (decisions)** — high effort, though lighter than
  first scoped: management-session discussion has already
  resolved or largely resolved questions 1, 2, 4 and 9 (the
  routing principle — LB probes only sf-api — collapsed
  them). Phase 0 ratifies those and resolves the genuinely-
  open remainder (3 dependency model, 5 drain grace, 6 auth,
  7 `node_daemon_states`, 8 startup, 11 lock proof-of-life)
  plus the daemon classification. Its output still drives
  every later phase.
- **Phase 1 (sf-api)** — high effort. The drain semantics
  (readiness-flip-before-shutdown, the grace-period
  reconciliation between gunicorn `--timeout 300` and systemd
  `TimeoutStopSec=30s`) carry subtle correctness, and the
  pattern becomes the template the rest copy from.
- **Phase 2 (sf-database gRPC health)** — high effort. The
  dependency model (MariaDB reachability, schema version) is
  subtle, and the Watch-deadlock / channel-factory
  constraints make this easy to get wrong. (No election
  readiness — sf-database is a stateless tier.)
- **Phase 3 (remaining daemons)** — medium effort once the
  phase-1 pattern exists and is documented as a brief; the
  work is largely mechanical replication, modulo each
  daemon's dependency set.
- **Phase 4 (operator docs)** — medium effort.

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
  calls, understanding non-obvious invariants (the SIGTERM /
  abort-path lifecycle, the Watch-deadlock constraint,
  dependency-aware readiness without per-probe DB load), or
  researching external references (the gRPC health protocol,
  LB probe conventions).
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief: adding a `/livez`
  route parallel to an existing unauthenticated resource,
  replicating the phase-1 pattern onto another daemon.
- **low** — Purely mechanical changes (add a log line,
  regenerate proto stubs, copy an LB-config example).

**Model choice:**

- **opus** — Deep reasoning, cross-daemon architectural
  understanding, subtle correctness (drain ordering, lock
  proof-of-life / `WATCHDOG` failover, the gRPC health
  servicer's threading constraints).
- **sonnet** — Good default for well-briefed implementation
  work once the phase-1 pattern is established.
- **haiku** — Purely mechanical tasks: regenerating proto
  stubs, adding log lines, copying doc snippets.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable. A
failed implementation of drain ordering wastes more time than
a heavier model would have cost.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include what to change,
which files to touch (with the `file:line` anchors this plan
and phase 0 have already gathered — e.g. the `Root` resource
registration in `external_api/app.py`, `exit_gracefully()`
and `record_exit()` in `daemons/daemon.py`, the health
servicer registration in `daemons/database/main.py`), what
patterns to follow, and any non-obvious constraints. The
better the brief, the lower the effort level needed and the
lighter the model that can succeed.

### Management session review checklist

After a sub-agent completes, the management session should
verify the standard items:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

Plus the health-check-specific items:

- [ ] Probes are cheap. A burst of `/readyz` requests does
      not amplify into a burst of MariaDB / sf-database
      queries.
- [ ] SIGTERM-then-probe flips readiness immediately
      (verified by test), and the process does not exit
      until either the grace period elapses or all
      in-flight requests complete.
- [ ] The dependency-readiness logic does not deadlock or
      flap on momentary peer hiccups (some hysteresis is
      required).
- [ ] gRPC daemons respond correctly to
      `grpc-health-probe`.
- [ ] No `Watch` was reintroduced on the sf-database health
      servicer, and no `healthCheckConfig` was added to the
      client channel factory in
      `shakenfist/util/grpc_channel.py` — both previously
      deadlocked and were deliberately removed.
- [ ] The cluster_ci rig exercises rolling-upgrade-with-
      drain end-to-end.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The health surface matches the routing principle: sf-api
  exposes HTTP `/livez` and `/readyz` (the only
  LB-routable surface); sf-database exposes the extended
  dependency-aware `grpc.health.v1.Health`; every other
  non-trivial daemon (worker and elected) carries a systemd
  `WATCHDOG` liveness signal and no `/readyz`. Daemons
  classified as sentinel/trivial are documented as
  deliberately health-surface-less. No per-node readiness
  aggregator is built.
* A wedged-but-alive elected daemon (sf-cluster) loses its
  cluster lock and a standby takes over, via the open
  question 11 mechanism (`WATCHDOG` kill → lease expiry, at
  minimum).
* The sf-database health work *extends* the existing servicer
  (byo-mariadb phase 3) into a dependency-aware readiness
  status; it does not duplicate it, and it reintroduces
  neither `Watch` nor client-side `healthCheckConfig`.
* `/readyz` flips to 503 immediately on SIGTERM, before any
  shutdown work begins.
* In-flight requests complete (within a configurable grace
  period) before the process exits, and that grace period is
  reconciled with the existing systemd `TimeoutStopSec=30s`
  and gunicorn `--timeout 300` settings rather than silently
  contradicting them.
* `node_daemon_states` continues to record orderly state
  transitions but is no longer relied on for real-time
  health.
* Operator documentation describes the endpoints, the
  expected return codes, and example LB configurations for
  at least HAProxy, nginx (FOSS), and one major cloud LB.
* The cluster_ci rig demonstrates a successful rolling
  upgrade: sf-api drains out of the LB pool before stopping,
  and the remaining daemons stop and restart cleanly under
  their liveness signal without orphaning a cluster lock.
* `pre-commit run --all-files` passes.

### Future work

- **Live CI demonstration of watchdog / lock failover.** Phase
  4's `ci_drain_check.sh` proves the sf-api *drain* end-to-end on
  a real node (`/readyz`→503-before-exit, then recovery), and the
  rolling-upgrade-with-drain procedure is documented. What is
  *not* exercised live in CI is the phase-3 watchdog path: a
  genuinely *wedged* (not cleanly stopped) elected `sf-cluster`
  being SIGABRT-killed by systemd and a standby stealing the
  lock. That has unit coverage for the pet logic
  (`test_daemon_watchdog.py`) and the failover chain is
  documented, but a live test would need to *stall* a daemon
  (e.g. `SIGSTOP` the cluster leader) and observe the
  ~120s-worst-case failover — more invasive than the clean
  drain check. Worth adding as a scheduled/soak CI test rather
  than per-PR.
- **Kubernetes-style startup probe.** If first-boot
  bootstrap latencies start tripping operators' LBs that
  expect readiness within seconds, a dedicated startup
  endpoint may be worth adding. The current plan trusts
  "/readyz stays 503 until truly ready" to be sufficient,
  but operators may push back.
- **Health-aware client retry.** SF's own client libraries
  could consume `/readyz` responses to bias retry behaviour
  (don't hammer a node that just told you it isn't ready).
  Out of scope here.
- **Schema-stale readiness state (decide in phase 2).** Today
  sf-database *refuses to start* on a schema mismatch
  (`verify_schema_versions`,
  `daemons/database/main.py:5157-5169`) — fail-fast and
  unambiguous, but the process is down, so a probe cannot
  distinguish "schema stale" from "crashed," and systemd
  crash-loops it until the operator runs
  `ensure-mariadb-schema`. An alternative is a distinct
  readiness state: sf-database starts, its gRPC health `Check`
  returns `NOT_SERVING` with a reason ("schema out of date;
  awaiting `ensure-mariadb-schema`"), re-checks periodically,
  and flips to `SERVING` once migrated — enabling a
  deploy-then-migrate workflow and observable, automatable
  upgrades without crash-loop noise. Two obstacles to weigh in
  phase 2: (1) it reverses `PLAN-byo-mariadb.md`'s deliberate
  refuse-to-start choice; (2) the client channel is
  intentionally *not* health-aware (no `healthCheckConfig`, to
  avoid the Watch deadlock — `util/grpc_channel.py`), so an
  up-but-`NOT_SERVING` sf-database would still receive RPCs via
  `round_robin` and its handlers would have to actively reject
  requests while waiting. A cheaper middle ground that keeps
  refuse-to-start but adds observability: emit an `sd_notify
  STATUS=` line so `systemctl status sf-database` shows the
  reason without the daemon serving. Phase 2 decides.
- **Per-request "drainable" flag.** A long-running blob
  upload cannot reasonably be drained inside a 30-second
  grace period. Either the client knows to retry from
  scratch, or the request carries a drainability hint that
  the server can honour by failing fast on SIGTERM rather
  than holding the connection open. Phase 0 may name this
  as an explicit decision; otherwise it falls out as
  future work.
- **REST API as its own tier; hypervisors gRPC-only.** The
  natural continuation of the tier-splitting in
  `PLAN-byo-mariadb.md` (sf-database tier) and
  `PLAN-remove-primary.md` (operator-owned LB): make sf-api
  its own stateless tier so external clients and the
  operator's LB never talk HTTP to a hypervisor, and
  hypervisor nodes expose only mesh gRPC. For the large
  clusters SF does not target today this shrinks the
  hypervisor's external surface and lets the LB fan out
  across a small API tier instead of every hypervisor; it
  also composes cleanly with mTLS-everywhere from
  `PLAN-embrace-tls.md`. **Its own plan, deferred** — but it
  is the trajectory open question 2 above is told to design
  toward, so this plan should not bake in an assumption that
  every hypervisor-resident daemon needs an HTTP health
  surface.
- **Decompose `sf-cluster`'s `_cluster_wide_cleanup`.** Phase 3
  added thirteen `pet_watchdog()` calls to this one method —
  a good proxy for the fact that it is a ~335-line god-method
  (`daemons/cluster/main.py`) doing at least eight unrelated
  concerns: the lease bail-out, stale-transfer cleanup,
  operation-history pruning, orphan-IPAM cleanup, floating-IP
  reservation cleanup, artifact cleanup, the large blob-
  replication reconciliation, and dead-node reaping. Splitting
  it into one method per concern
  (`_cleanup_orphan_ipams`, `_cleanup_floating_reservations`,
  `_cleanup_artifacts`, `_reconcile_blob_replication`,
  `_reap_dead_nodes`, …) would make each individually testable
  (phase 3's tests had to mock heavily to exercise a single
  loop), localise each watchdog pet, and make the maintenance
  pass readable. Not health-check work — flagged here because
  we hit it here. The natural owner is
  [`PLAN-recurring-operations.md`](PLAN-recurring-operations.md),
  which already aims to restructure cluster maintenance
  (absorbing `scheduled_tasks.py` / `network/maintain.py`):
  the decomposed cleanups are natural candidates to become
  individually-scheduled recurring operations.

### Bugs fixed during this work

This section lists bugs fixed during development, plus a scan
of the GitHub tracker for directly related issues this plan
should resolve or be aware of. The scan was done at planning
time (2026-06); re-run it before each phase, as the tracker
moves.

**Open issues this plan should resolve or explicitly own.**

- **#730 — "dnsmasq health check should verify it is serving
  DHCP, not just that the process is alive."** Already
  rescoped (2026-06) to point at *this* plan:
  `is_dnsmasq_running()` (`network/network.py:657`) only calls
  `pid_exists`, so a wedged-but-alive dnsmasq passes. This is
  the canonical liveness-vs-deep-readiness case and the
  concrete instance of open question 11's "process exists ≠
  work is happening" for a *managed child process* rather than
  a daemon's own loop. Phase 0 decides whether sf-net gaining
  a real DHCP-serving probe is in this plan's scope or stays a
  tracked follow-on; either way this plan owns the *model* the
  fix must follow, so #730 should be cross-referenced and
  closed-or-reassigned as part of phase 0.
- **#1364 — "Add lame-duck and evacuate node lifecycle."**
  The lame-duck / maintenance / drain *node state* half
  overlaps this plan's drain semantics directly — a node
  entering lame-duck is exactly "sf-api `/readyz` flips to 503
  and the LB drains it." Coordinate: this plan can deliver the
  per-daemon drain primitive that a node-level lame-duck state
  is later built on. The *evacuate* half (migrating instances
  off a node) is out of scope here and stays on #1364.

**Closed issues to be aware of (precedent / regression
guards).**

- **#1985 — "Cleanup cluster lock on exit"** (`Lock held by
  missing process on this node`). The pre-MariaDB stale-lock
  bug the operator recalled; fixed by the leased-lock design
  (`expires_at` + refresher + steal-on-expiry). It is the
  historical anchor for open question 11 — note that the
  *process-death* case it covered is solved, and OQ11 is only
  about the *wedged-but-alive* residual.
- **#1206 — "Daemons are slow to shutdown"** (cleaner's 60s
  sleep). Closed, but the lesson is live: any drain/liveness
  loop must wake promptly on the stop signal (the
  `lock.lost_event.wait(60)` pattern) rather than sleeping
  through it, or the grace period is dominated by sleep
  latency. A regression here would resurface as slow drain.
- **#2186 — "Expiring lock during instance setup"** and
  **#1906 — "cluster_wide_cleanup experiences lock timeouts."**
  Both are lease-expiry-under-long-operation cases. They are
  the cautionary evidence behind OQ11's belt-and-suspenders
  warning: coupling lock renewal to a liveness heartbeat must
  not drop the lease during a legitimately long iteration.
- **#1362 / #1363 — graceful-shutdown CI tests** (closed).
  Prior art for the cluster_ci rolling-upgrade-with-drain
  coverage this plan's success criteria require; check whether
  those tests still exist or were lost (see the CI backlog
  tracker #3278) before writing new ones.

**Latent bugs surfaced during planning (to fix in-plan).**

- **gunicorn `--timeout 300` vs systemd `TimeoutStopSec=30s`.**
  These disagree today: systemd will `SIGKILL` sf-api 30s into
  a shutdown even though gunicorn believes it has 300s to
  drain in-flight requests. Any honest drain-grace value must
  reconcile the two (phase 1). Recorded here so the
  reconciliation is treated as a bug fix, not an incidental
  tweak.
- **Phantom `sf-eventlog` daemon** in the earlier draft of
  this plan. There is no such daemon; corrected in the mission
  and phase table during planning. Noted so the error is not
  reintroduced from stale notes.

**Bugs fixed during implementation.** _(none yet — populate as
phases land.)_

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans* table and
  update the *Plan sequencing* section to reflect that
  the health-checks master plan now exists.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.

## Follow-on: database-tier graceful rolling restart (2026-07, #3430)

This plan's success criteria call for a clean rolling upgrade
of the database tier, and `PLAN-byo-mariadb.md` phase 3/6 landed
the `serial: 1` roll (`examples/_shared/site.yml` play 6b) plus
a per-node `wait_for` on port 13005. Live operation of the sfcbr
cluster then showed that roll is **necessary but not
sufficient**: every `manage.yml`/`site.yml` redeploy still
storms the mariadb-broker gRPC tier with `UNAVAILABLE`
("connections to all backends failing") for 10-20 minutes,
cascading into `DatabaseUnavailable`, an eventlog-drainer thread
exit, and the lookup-race of #3373. The storm reproduced on
shakenfist `61e1ad171`, which already contained the `serial: 1`
roll — so the gap is real, not a missing deploy.

**Diagnosis (three interacting gaps).**

1. *The client amplifies a single-gateway blip into an outage.*
   `mariadb._grpc_call` rebuilt the whole gRPC channel
   (`_reset_database_stub`) on **every** `UNAVAILABLE`. That
   discards a warm `round_robin` channel — including the healthy
   subchannel to a still-serving gateway — for a cold one whose
   own first RPC fails `UNAVAILABLE` (no connected subchannel
   yet, `wait_for_ready=False` by design), so one gateway's
   restart cascades into repeated failures and can exhaust the
   three retries into `DatabaseUnavailable`. Visible as a
   steady-state signature too: sf-cluster's lock-refresh failing
   `UNAVAILABLE` → "retrying with a fresh channel" every ~23s
   *outside* any deploy.
2. *The deploy gate proves the wrong thing.* `wait_for` on port
   13005 confirms only that the socket is listening, not that
   the gateway is serving (MariaDB reachable) nor that cluster
   clients have re-established their `round_robin` subchannels
   to the recovered gateway. If the next node in the roll drops
   its gateway while a peer is still mid-reconnect, that peer
   momentarily sees no READY backend.
3. *No graceful drain.* `daemons/database/main.py` stopped the
   gRPC server with a one-second grace, cutting in-flight RPCs
   (surfacing as client `UNAVAILABLE`/`CANCELLED`).

**Fix (landed on the `db-gateway-rolling-restart` branch).**

- **Client (`mariadb._grpc_call`).** Split the retry by code.
  `UNAVAILABLE` → retry on the *same* channel and let
  `round_robin` serve from a surviving peer while the failed
  subchannel reconnects in the background. `DEADLINE_EXCEEDED`
  (the wedged-subchannel signature the `wait_for_ready` note
  guards against) still rebuilds the channel to shed the wedged
  backend. This is the highest-leverage change and also cures
  the ~23s steady-state churn.
- **Deploy gate (`node` role `register.yml`).** After the port
  `wait_for`, gate the roll on a new `sf-ctl gateway-health`
  command that probes this node's own `grpc.health.v1` `Check`
  for SERVING (which on sf-database means MariaDB reachable +
  schema current), then a short, configurable
  (`sf_database_roll_settle_seconds`, default 10s) settle so
  peers reconnect before the next node's gateway stops.
- **Server drain (`daemons/database/main.py`).** Replace the
  one-second stop with `server.stop(config.DATABASE_DRAIN_GRACE)`
  (new config knob, default 10s, kept below the generic
  `sf.service` `TimeoutStopSec=30s`) after the existing
  NOT_SERVING flip, so in-flight RPCs finish instead of being
  cut.

This honours the two carried-over constraints (no `Watch` on
the servicer, no `healthCheckConfig` on the client channel) —
the gate reads `Check` from the deploy, not client-side Watch,
and the client fix works purely through `round_robin`
connectivity state. It is the concrete "rolling upgrade drains
cleanly" success criterion, extended from sf-api (phase 1) to
the sf-database tier. Recorded here rather than as a new plan
because it is a hardening of the health/drain model this plan
already owns; tracked in #3430.

**Second round (2026-07-26): reconnect backoff.** With all of
the above deployed, sfcbr still showed 1-2 minute
"connections to all backends failing" bursts on some deploys
(2026-07-20, 2026-07-23) — much shorter than the original
10-20 minutes, but still a storm. The residual mechanism is
gRPC's default subchannel reconnect backoff: it grows from 1s
by 1.6x per failed dial to a **120s** ceiling, and
`round_robin` does not redial a TRANSIENT_FAILURE subchannel
early while another backend is READY. So after gateway A's
roll, peers can sit for up to two minutes without redialling
the recovered A; when the roll then stops gateway B inside
that window, those peers see no READY backend at all, and the
client's ~1.5s of total retry patience (three attempts, 0.5/1s
sleeps) exhausts into a `DatabaseUnavailable` burst that lasts
until the backoff timers expire. The 07-23 deploy showed this
exactly: sf-1 rolled at 12:25:48, sf-2 at ~12:36, storm at
12:35-12:36. The 10s settle assumed reconnect-within-10s; the
default backoff cap made that assumption false. Fix, two
layers: (a) cap the client channel's reconnect backoff
(`grpc.initial_reconnect_backoff_ms=1000`,
`grpc.max_reconnect_backoff_ms=5000` in
`util/grpc_channel.py`) so a recovered gateway is redialled
within 5s and the 10s settle genuinely covers the reconnect
window — the settle must always stay longer than the cap; and
(b) give the fast-failing `UNAVAILABLE` path more patience in
`_grpc_call` (`GRPC_UNAVAILABLE_RETRIES=6`, ~7.5s of
escalating sleeps, outlasting the 5s cap) so a brief window
with no READY backend is ridden out rather than amplified,
while `DEADLINE_EXCEEDED` attempts stay capped at three so the
worst-case wall time stays bounded.
