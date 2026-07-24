# Phase 4 — caller attribution on sf-database counters

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

**Status: Complete (shakenfist code); dashboard pending, deploy
measurement pending.** Landed: 4a caller-identity plumbing
(`util/caller_identity.py` + set at every process entry point); 4b a
client `UnaryUnaryClientInterceptor` in `grpc_channel.py` stamping
`caller-daemon`/`caller-node` on every call; 4c a server
`ServerInterceptor` in `daemons/database/main.py` incrementing the new
additive `database_requests_total{operation, caller_daemon}` (existing
counters untouched); 4e this docs pass. `pre-commit run --all-files`
green including mypy. The `operation` label is the raw PascalCase RPC
name (acronym RPCs like `GetIPAM` defeat a clean snake_case
conversion — noted in code and the commit). 4d (the 33fl Grafana
panel) is made in the working tree of the separate 33fl repo but left
for the operator to commit on a branch. Per-caller numbers for the hot
ops will be recorded once this deploys.

## Goal

Attribute each sf-database gRPC operation to the caller (which
daemon, on which node, issued it) so the metrics can answer "which
daemon drives the load on this operation". Phase 3 left exactly one
live gRPC client stack, so this is a single interceptor seam. The
result also doubles as the first concrete slice of the not-yet-drafted
OpenTelemetry thread (the caller-identity plumbing is what a later
span-propagation phase reuses) and is designed to compose with the
`PLAN-embrace-tls.md` mTLS work rather than duplicate it.

## Findings (the attribution surface)

* **One client chokepoint.** Every client channel to sf-database is
  built by `make_database_channel()` (`util/grpc_channel.py:59-90`,
  the single `grpc.insecure_channel(...)` return at line 90). All five
  call sites route through it: the `mariadb.py` stub path
  (`_get_database_stub`, mariadb.py:330), the `config.py` bootstrap
  one-shot (config.py:101), the `external_api/health.py` readiness
  check, and the `ctl.py` CLI. Wrapping the channel there with
  `grpc.intercept_channel(channel, _CallerMetadataInterceptor())`
  instruments all of them with no per-call-site change. All
  DatabaseService RPCs are unary-unary, so a single
  `UnaryUnaryClientInterceptor` covers 100% of traffic.
* **Node identity is always available; daemon identity is not.**
  `config.NODE_NAME` (default_factory `get_node_name`) is populated in
  every process from import. `config.NODE_UUID` is `None` until
  `Daemon._resolve_node_uuid()` runs, so it is unreliable at
  interceptor time — use `NODE_NAME`. There is **no** process-global
  that records the running daemon name for a library to read;
  `self.daemon_name` is per-instance state on `Daemon`
  (daemon.py:214). A small process-global setter is required.
* **Server counters are 183 hand-written `.inc()` sites, no central
  hook.** `Monitor.__init__` (daemons/database/main.py:~4917-5090)
  builds one unlabelled `Counter('database_<op>_total')` per op from a
  flat `operations` list; each of the ~150 RPC handlers calls
  `self.monitor.counters['<op>'].inc()` by hand. There is no base
  method, decorator, or interceptor today.
* **A server interceptor is a one-line addition and does everything
  needed.** `grpc.server(...)` (main.py:5277) takes an
  `interceptors=[...]` kwarg. A `grpc.ServerInterceptor` can read
  `handler_call_details.invocation_metadata` for the caller labels and
  derive the operation from `handler_call_details.method`
  (`/shakenfist.protos.DatabaseService/GetNode` → `GetNode`). The
  synchronous 64-thread servicer is fine (`Counter.inc()` is
  thread-safe); the health-Watch deadlock hazard is unrelated (it is a
  client-side `healthCheckConfig` issue and opens no streams here).
* **Attribution must be server-side.** Only three daemons run a
  Prometheus endpoint (sf-database :13006, sf-resources, sf-cluster).
  sf-api/net/queues/cleaner/transfers/sidechannel and the CLI expose
  none, so a client-side counter would be scrape-invisible for most
  callers. Counting on the sf-database endpoint (which every caller's
  RPC reaches) is the only topology that yields complete attribution.

## Design decisions

1. **Additive, not invasive.** Add ONE new labelled counter
   `database_requests_total{operation, caller_daemon}` incremented once
   in a server interceptor. The 183 existing unlabelled
   `database_<op>_total` counters, and every dashboard/alert built on
   them, are left exactly as-is. Zero handler edits.
2. **Client sends both `caller-daemon` and `caller-node` metadata;
   the counter labels on `caller_daemon` only.** Sending `caller-node`
   (from `config.NODE_NAME`) is cheap and is the value a future mTLS
   server cross-checks against the verified peer SAN (Decision 4 of
   the master plan). The counter omits `caller_node` as a label to
   control cardinality (see open question 4) — the server can still
   recover the peer ad hoc via `context.peer()`, and the label can be
   added later without a client change.
3. **`caller_daemon` set once at process startup, default
   `'unknown'`.** A tiny process-global in a new import-light module
   `shakenfist/util/caller_identity.py` (`set_caller_identity(name)` /
   `get_caller_daemon()`), read by the client interceptor at call
   time. Set from each process entry point. An unset/one-shot caller
   attributes to `'unknown'` — bounded and harmless, never an error.
4. **`operation` label matches existing snake_case naming.** Convert
   the PascalCase wire method (`GetNode`) to `get_node` so the new
   metric joins/compares cleanly with `database_get_node_total`.
5. **Never raise on the RPC path.** Both interceptors wrap their
   logic defensively; a metadata or parsing problem degrades to
   `'unknown'`/skip, never a failed RPC.

## Open question 4 — counter label shape (resolved)

Resolved to **`database_requests_total{operation, caller_daemon}`**
(caller_node sent as metadata but not labelled). Cardinality: the
`operation` axis (~150) dominates; realistic live series are in the
low hundreds because most daemons touch only a handful of ops.
Including `caller_node` (~6) would ~6× the series for modest value
given only six nodes and the recoverable `context.peer()`; defer it.
Dropping `operation` entirely (keeping only `caller_daemon`, ~12
series) was considered — rejected because "which daemon drives
get_node specifically" needs both axes, and that question is the
whole point of the phase.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 4a | medium | sonnet | none | **Caller-identity plumbing.** New `shakenfist/util/caller_identity.py` with a module-global (default `'unknown'`), `set_caller_identity(name)` and `get_caller_daemon()`. Import-light (no `config` import) to preserve the bootstrap no-cycle contract. Call `set_caller_identity(name)` from every process entry point: `Daemon.__init__` (daemon.py:214, right after `self.daemon_name = name` — covers net/queues/cluster/resources/cleaner/transfers/sidechannel/database), `gunicorn_config.post_fork` (`'api'`, alongside the existing `eventlog_drainer.start('sf-api')`), the `sf-ctl` CLI entry in `ctl.py` (`'ctl'`), and the non-Daemon mains that call the DB client (`nodelock`, `privexec`, `sentinel_first`, `sentinel_last`). Unit test get/set and the default. |
| 4b | high | opus | none | **Client metadata interceptor.** In `util/grpc_channel.py`, add a `grpc.UnaryUnaryClientInterceptor` whose `intercept_unary_unary` copies `client_call_details.metadata` (None → `[]`), appends `('caller-daemon', get_caller_daemon())` and `('caller-node', <config.NODE_NAME>)` (lazy `from shakenfist.config import config` inside the method, fallback `'unknown'`; keys MUST be lowercase), rebuilds an immutable `_ClientCallDetails` preserving `method/timeout/credentials/wait_for_ready`, and calls `continuation`. Wrap the channel at the single return: `return grpc.intercept_channel(channel, _CallerMetadataInterceptor())`. Verify the round_robin/keepalive options survive interception. Unit test: the interceptor appends both metadata keys and preserves timeout; a call with pre-existing metadata keeps it. |
| 4c | high | opus | none | **Server counting interceptor.** In `daemons/database/main.py`: define `database_requests_total = Counter('database_requests_total', ..., ['operation', 'caller_daemon'])` near the Monitor counter setup; add a `grpc.ServerInterceptor` whose `intercept_service` derives `operation` from `handler_call_details.method.rsplit('/',1)[-1]` via a CamelCase→snake_case helper, reads `caller-daemon` from `handler_call_details.invocation_metadata` (default `'unknown'`), skips `/grpc.health.v1.Health/*` methods, and `.inc()`s the counter once; then pass `interceptors=[_CallerMetricsInterceptor(...)]` to `grpc.server(...)` at main.py:5277. Do NOT touch existing counters/handlers. Unit test: the interceptor increments with the right `operation`/`caller_daemon`, defaults missing metadata to `'unknown'`, and skips health methods. |
| 4d | low | sonnet | none | **Dashboard (separate repo: mach33labs/33fl).** In `config/maui/grafana/dashboards/shakenfist.json`, add a "DB ops by caller" timeseries panel to the sf-database row: `topk(15, sum by (caller_daemon, operation) (rate(database_requests_total[5m])))`, plus a per-caller total `sum by (caller_daemon) (rate(database_requests_total[5m]))`. Confirm existing panels (which use `database_<op>_total`) are unaffected. This is a separate commit/PR in the 33fl repo, deployed by re-running the monitoring role. |
| 4e | low | sonnet | none | **Docs + measurement.** Add a short "caller attribution" note to `ARCHITECTURE.md`/`docs/operator_guide/database.md` (the new metric, its labels, mTLS-compatibility). After deploy, record here which daemons dominate the hot ops (`get_node`, `get_node_daemon_state`, `dequeue`, `get_ipam`) — the input to phase 5. |

## Correctness invariants

* Existing `database_<op>_total` counters and all dashboards/alerts
  built on them are byte-for-byte unchanged (the new counter is
  additive).
* Neither interceptor can fail an RPC: metadata/parse errors degrade
  to `'unknown'`/skip inside a `try`.
* Metadata keys are lowercase (`caller-daemon`, `caller-node`).
* The client interceptor preserves every `ClientCallDetails` field,
  and the channel's round_robin/keepalive options survive
  `intercept_channel`.
* Label cardinality is bounded: `caller_daemon` is drawn from a fixed
  set plus `'unknown'`; `operation` from the fixed RPC set.

## mTLS composition (Decision 4)

The `caller-node` metadata is sent now but not yet trusted. When
`PLAN-embrace-tls.md` lands per-node peer certificates, the server
interceptor can additionally compare the `caller-node` metadata claim
to the verified peer SAN and reject or flag a mismatch — hardening
this same path rather than introducing a second attribution
mechanism. No client change is needed at that point.

## Verification

* `pre-commit run --all-files` green (flake8, unit suite, mypy
  `--strict`).
* Unit tests per step; the full-cluster CI stays green (interceptors
  are on the hot RPC path — a defect would surface as failed DB
  calls).
* On deploy to sfcbr: `database_requests_total` appears on the
  sf-database :13006 endpoint with sensible `caller_daemon` values (no
  unexpected `'unknown'` dominance, which would indicate a missed
  entry point), and its per-operation sums track the existing
  `database_<op>_total` rates. Record the per-caller breakdown of the
  hot ops here.

## Risks

* **`'unknown'` dominance** — a process entry point missed in 4a.
  Mitigation: 4a enumerates every entry point; the deploy check above
  catches a miss quickly.
* **Interceptor latency/overhead** on every RPC. Negligible (two
  metadata appends client-side; one dict lookup + `.inc()`
  server-side), but noted.
* **Two-repo coordination** — the shakenfist code (4a-4c, 4e) and the
  33fl dashboard (4d) are separate PRs; the dashboard is only useful
  once the code deploys, so land code first.

## Success criteria

* `database_requests_total{operation, caller_daemon}` is scraped from
  sf-database and drives a per-caller dashboard panel.
* No existing counter, query, or alert changed behaviour.
* The design is mTLS-ready (caller-node metadata present) with no
  second attribution path.
* `pre-commit` and CI green; the hot ops have a clear per-caller
  breakdown feeding phase 5.
