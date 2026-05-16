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

This plan is **partial**. Phase 0 will resolve the open
questions into a decisions document and the phase table below
may be re-cut accordingly.

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
- Individual `sf-*` daemons do not generally expose a
  dedicated health endpoint. gRPC servers do not currently
  implement the `grpc.health.v1.Health` service.

A LB pointed at sf-api today gets a useful answer only by
probing an auth-protected endpoint and treating "401" as
healthy. That works as a hack; it is wrong as a contract.
There is also no story for graceful drain on SIGTERM —
operators rolling daemons during upgrade have no way to
take a node out of the LB pool before it stops serving.

## Mission and problem statement

Every SF daemon exposes a consistent health-check surface
that distinguishes three semantics, in the vocabulary
Kubernetes-style operators already use:

- **liveness:** the process is running and its main loop
  is not deadlocked. Used by orchestrators to decide
  "restart this thing." Returns 200 as long as the
  daemon's main goroutine / thread is making progress.
- **readiness:** the daemon is ready to accept work. Its
  dependencies (database connection, leader election if
  applicable, required cluster config keys, ...) are
  satisfied. Used by the LB to decide "send this thing
  traffic." Returns 200 only when the daemon is genuinely
  serving; flips to 503 on shutdown initiation.
- **drain:** on SIGTERM, readiness flips to "not ready"
  immediately, the LB removes the daemon from its pool on
  its next health-check cycle, in-flight requests complete
  within a configurable grace period, and only then the
  process exits.

Concretely:

- **sf-api** exposes `/livez` and `/readyz` (and a `/healthz`
  alias) on its existing HTTP port (13000), unauthenticated.
  Probes never touch the database directly; readiness reads
  cached dependency state updated by a background checker.
- **gRPC daemons** (sf-database, sf-eventlog, others as
  surveyed during phase 0) implement the standard
  `grpc.health.v1.Health` service. Peers and operators can
  use the standard `grpc-health-probe` tool.
- **Non-network daemons** (sf-cleaner, sf-queues, sf-net,
  sf-resources, sf-cluster, sf-transfers, sf-privexec —
  inventory and decision per phase 0) expose either a tiny
  HTTP endpoint each, or contribute to a shared per-node
  endpoint owned by sf-resources. Phase 0 decides which.
- **Elected daemons** (sf-cluster today, sf-database after
  `PLAN-remove-primary.md` phase 5) have readiness semantics
  that distinguish "I am the leader and ready to serve" from
  "I am a standby candidate and ready to elect / answer who-
  is-leader." Both report ready=200, but the response body
  identifies the role so an LB can be configured to direct
  client traffic at the leader if desired.
- **SIGTERM** causes readiness to flip to 503 *before* any
  shutdown work begins, then a grace period (default and
  configuration per phase 0), then orderly shutdown.

The principle is: **health is a real-time probe, not a
state read.** `node_daemon_states` continues to serve its
eventual-consistency cluster-state role and is not the
substrate for LB probes. The two are orthogonal.

## Open questions

This plan is partial; phase 0 will resolve at least the
following:

1. **Per-daemon ports vs shared per-node endpoint.**
   Each daemon exposing its own tiny HTTP listener on its
   own port is simple but multiplies listeners and config.
   A single per-node health endpoint owned by sf-resources
   (which already runs everywhere and reports node metrics)
   that aggregates all local daemons is operationally
   cleaner but introduces a circular dependency
   (sf-resources reports the health of itself) and a single
   point of opacity (if sf-resources is down, operators see
   the whole node as down even if other daemons are fine).
   Probably the right answer is *both*: each daemon has its
   own liveness endpoint for the orchestrator, and a shared
   per-node readiness aggregator for the LB. Decide in phase 0.
2. **HTTP vs gRPC health protocol per daemon.** gRPC daemons
   should implement `grpc.health.v1.Health` regardless,
   because it's the standard and `grpc-health-probe` already
   exists. The question is whether they *also* expose HTTP
   health, which matters for operator LBs that only speak
   HTTP. Phase 0 decides.
3. **Readiness dependency model.** Each daemon enumerates
   what it depends on. sf-api depends on sf-database being
   reachable; sf-database depends on MariaDB being
   reachable; sf-net depends on sf-privexec; etc. The
   probe must be cheap (cached, refreshed by a background
   goroutine), not expensive (querying MariaDB on every
   probe would amplify LB-probe traffic into DB load).
   Phase 0 produces the dependency graph and the cache /
   refresh semantics.
4. **Readiness for elected daemons.** A non-leader
   sf-database candidate is "ready to elect" but not
   "ready to serve client RPCs as the leader." Does
   `/readyz` return 200 for both? Probably yes, with body
   distinguishing the roles. Or does the LB only see
   leader-ready as 200 and standbys as 503, with the
   candidate-list discovery library handling who-is-leader
   separately? Phase 0.
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
9. **Interaction with `PLAN-embrace-tls.md`.** When inter-
   daemon channels go mTLS, the LB still needs to probe SF
   over plain HTTP (operators terminate TLS at the LB).
   Either health endpoints stay on a non-mTLS port, or the
   operator's LB has its own client cert for health probes
   only. The cleanest answer is a dedicated, plaintext-OK
   health port the operator opens only to their LB. Phase 0.

## Execution

Provisional. Phase 0 may re-cut the phase table.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions document | PLAN-health-checks-phase-00-decisions.md | Not started |
| 1. sf-api health endpoints and SIGTERM drain | PLAN-health-checks-phase-01-sf-api.md | Not started |
| 2. gRPC health protocol on sf-database and sf-eventlog | PLAN-health-checks-phase-02-grpc-health.md | Not started |
| 3. Remaining daemons | PLAN-health-checks-phase-03-other-daemons.md | Not started |
| 4. Operator documentation and LB-config examples | PLAN-health-checks-phase-04-operator-docs.md | Not started |

Notes on sequencing:

- **Phase 0 is decisions.** No code. Output is appended to
  this master plan as a "Decisions" section and the phase
  table is re-cut against it.
- **Phase 1 is the canary.** sf-api is the most operator-
  visible daemon and the one the LB actually probes; if
  the pattern doesn't work for sf-api it doesn't work
  anywhere. Land it first and use it as the template.
- **Phase 2 is the gRPC pattern.** Implementing
  `grpc.health.v1.Health` is small; the interesting bit is
  defining the dependency model for sf-database
  specifically given its election semantics
  (post-`PLAN-remove-primary.md` phase 5).
- **Phase 3 extends the pattern** to every other daemon.
  Largely mechanical once phases 1-2 are done.
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
- **`PLAN-embrace-tls.md`:** has an interaction (open
  question 9 above) — health endpoints should remain
  reachable by an LB that doesn't speak mTLS. Phase 0 of
  health-checks must produce an answer that the TLS plan
  can later honour.
- **`PLAN-remove-primary.md` phase 5 (sf-database
  election):** influences open question 4 (readiness for
  elected daemons). This plan can land its phases 0-1
  before phase 5 lands; phase 2 of this plan (gRPC health
  on sf-database) should land *after* phase 5 of
  remove-primary so the readiness semantics already account
  for the elected shape.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors the other plans:
plan in the management session, spawn a sub-agent per
implementation step, review in the management session, fix
or retry, commit when satisfied.

Phase 0 (decisions) is **opus at high effort** because the
output drives every later phase and the questions are
genuinely open. Phase 1 (sf-api) is **opus at high effort**
because the pattern it establishes becomes the template
the rest copy from; getting it right early saves rework
across every daemon. Phases 2-3 are likely **sonnet at
medium effort** once phase 1's pattern is established and
documented as a brief.

### Step-level guidance

Each phase plan should include a step table with effort,
model, isolation, and brief columns in the format used by
`PLAN-remove-primary.md`.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

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
- [ ] The cluster_ci rig exercises rolling-upgrade-with-
      drain end-to-end.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Every SF daemon exposes a real-time health probe — HTTP
  `/livez` and `/readyz` for HTTP daemons,
  `grpc.health.v1.Health` for gRPC daemons, the phase-0
  decision for the rest.
* `/readyz` flips to 503 immediately on SIGTERM, before any
  shutdown work begins.
* In-flight requests complete (within a configurable grace
  period) before the process exits.
* `node_daemon_states` continues to record orderly state
  transitions but is no longer relied on for real-time
  health.
* Operator documentation describes the endpoints, the
  expected return codes, and example LB configurations for
  at least HAProxy, nginx (FOSS), and one major cloud LB.
* The cluster_ci rig demonstrates a successful rolling
  upgrade with drain across every daemon.
* `pre-commit run --all-files` passes.

### Future work

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
- **Per-request "drainable" flag.** A long-running blob
  upload cannot reasonably be drained inside a 30-second
  grace period. Either the client knows to retry from
  scratch, or the request carries a drainability hint that
  the server can honour by failing fast on SIGTERM rather
  than holding the connection open. Phase 0 may name this
  as an explicit decision; otherwise it falls out as
  future work.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add rows to the *Plan Status* table and
  update the *Plan sequencing* section to reflect that
  the health-checks master plan now exists.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
