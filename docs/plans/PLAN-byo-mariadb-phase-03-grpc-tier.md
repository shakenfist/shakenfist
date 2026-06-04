# Phase 3: gRPC tier — client-side LB and server-side health

Parent plan: [PLAN-byo-mariadb.md](PLAN-byo-mariadb.md).

## Prompt

Before responding, read these files so you understand
the current gRPC channel and server construction in
the codebase:

- `shakenfist/database.py` — the primary client
  library; its `get_database_client()` function
  builds a thread-local `grpc.insecure_channel` and
  uses an aggressive 200 ms keepalive timeout.
- `shakenfist/mariadb.py` lines 312-329
  (`_get_database_stub()`) — a second channel
  builder used internally by `mariadb.py` to route
  RPCs through the database service; uses 5000 ms
  keepalive timeout.
- `shakenfist/config.py` lines 18-114
  (`load_cluster_config()`) — the module-import-time
  bootstrap; builds a one-shot channel with no
  options to fetch cluster config.
- `shakenfist/daemons/database/main.py` lines 5180-
  5220 — the server-side construction. The
  `grpc.server(...)` call sets keepalive options
  that the *server* enforces; phase 3 adds the
  `grpc.health.v1.Health` servicer registration here.
- `pyproject.toml` lines 62-64 — the pinned
  `grpcio==1.70.0` family. Phase 3 adds
  `grpcio-health-checking==1.70.0` as a sibling.
- `shakenfist/util/` — the existing utility module
  layout. The channel factory lands here as
  `shakenfist/util/grpc_channel.py`, a small
  config-independent module callable from any of
  the three current consumers.

This phase does not change deploy machinery
(`getsf`, `roles/mariadb/`, the
`SHAKENFIST_MARIADB_HOST=localhost` escape hatch);
that is phase 4. CI multi-instance shape lands in
phase 6. The N>1 shape is *unlockable* after phase 3
in the sense that the config and channel plumbing
both support it, but no test exercises N>1 until
phase 6.

One commit per step. Each commit must build and
`pre-commit run --all-files` must pass.

## Context

Phase 2 of byo-mariadb made `MARIADB_GATEWAY_HOSTS`
a list (validated as `list[str]`) and added a
`BeforeValidator` that parses comma-separated env-
var values into the list shape. Phase-2 consumers
all take `MARIADB_GATEWAY_HOSTS[0]` because they
still build a single-endpoint gRPC channel. Phase 3
replaces every consumer with a load-balanced
channel over the full list and adds health-protocol
awareness on both sides.

Three current consumers:

1. `shakenfist/database.py:52` (`get_database_client`)
   — the main client library, thread-local channel,
   200 ms keepalive timeout, 100 MB max message
   size for blob-size operations.
2. `shakenfist/mariadb.py:328` (`_get_database_stub`)
   — internal `mariadb.py` routing helper, 5 s
   keepalive timeout, no max-message override.
3. `shakenfist/config.py:100` (`load_cluster_config`)
   — bootstrap-time one-shot, no options.

Three call sites with three different option sets
is an obvious refactor target on its own. Phase 3
consolidates them into one channel factory that
takes a small option dict for any per-caller
overrides (max message size for the blob client;
nothing for the bootstrap path). The shared
defaults are the keepalive set the others all
share already.

For the LB plumbing itself, gRPC-Python supports a
static-resolver-with-round-robin construction
without any external service-discovery dependency:

- The target string uses the `ipv4:` URI scheme
  with comma-separated `host:port` pairs:
  `ipv4:10.0.0.1:13005,10.0.0.2:13005,10.0.0.3:13005`.
  The static resolver parses this into a list of
  subchannels.
- The `grpc.service_config` channel option, passed
  as a JSON string, sets
  `loadBalancingConfig` to `[{"round_robin": {}}]`
  to enable the built-in round-robin policy across
  subchannels.
- The same `service_config` enables client-side
  health checking via `"healthCheckConfig":
  {"serviceName": ""}`. The empty-string service
  name corresponds to the server's "overall server
  health" status, which is what
  `health.HealthServicer().set('', SERVING)`
  publishes.

This combination is the gRPC-native answer to "I
have a list of equal backends, distribute my
requests across them, route around any one that's
unhealthy." No external LB tier, no haproxy / VIP,
no separate service discovery. The behaviour is
deterministic and well-documented in gRPC's own
docs.

For the server side, the minimal health
implementation is half a dozen lines on top of
the existing `grpc.server(...)` call. The
canonical Python helper is
`grpc_health.v1.health.HealthServicer` shipped by
the `grpcio-health-checking` package — pinned at
the same version as `grpcio` itself. Adding it as
a dependency is straightforward.

After this phase lands, the following statements
are true:

- `shakenfist/util/grpc_channel.py` exists. It
  exports `make_database_channel(hosts: list[str],
  port: int, options: dict | None = None)` which
  constructs a gRPC channel using the `ipv4:`
  static resolver, `round_robin` policy, and
  health-checking against the empty service name.
  The function takes hosts and port as arguments
  rather than reading `config.*` directly so it
  can be safely imported from
  `shakenfist/config.py` itself without an import
  cycle.
- `shakenfist/database.py`, `shakenfist/mariadb.py`,
  and `shakenfist/config.py` all use the new
  factory. Per-call option overrides for max
  message size (the blob path) and any other
  caller-specific tweaks pass through the `options`
  dict.
- `sf-database` registers `grpc_health.v1.Health`
  on its server and publishes
  `''` → `SERVING` immediately after server
  startup, before the daemon's main run loop
  begins serving requests. The health status flips
  to `NOT_SERVING` in the daemon's shutdown path
  so in-flight clients can drain rather than seeing
  an abrupt connection refusal.
- `grpcio-health-checking==1.70.0` is added to
  `pyproject.toml`.
- A single-element `MARIADB_GATEWAY_HOSTS` value
  still works — the `ipv4:host:port` (no comma)
  shape is the degenerate one-element case.
- Unit tests cover the factory's target-string
  construction for both single-element and multi-
  element lists, and a smoke test instantiates the
  `HealthServicer` and verifies the `Check` RPC
  returns `SERVING` for the empty service name.
- `docs/operator_guide/database.md` mentions the
  client-side LB behavior and the health-protocol
  surface in the "MARIADB_HOST vs
  MARIADB_GATEWAY_HOSTS" subsection that phase 2
  added. The new content is brief — operators do
  not need a gRPC tutorial, just confirmation that
  setting `MARIADB_GATEWAY_HOSTS` to a multi-
  element list "just works".

This phase satisfies the two open questions the
master plan left for phase 3:

1. **Health-check interaction with
   `PLAN-health-checks.md`.** Phase 3 lands a
   minimal `grpc.health.v1.Health` server
   implementation. The health-checks plan's phase
   2 (which was originally scoped to "land the
   protocol on sf-database") becomes "extend the
   existing minimal implementation with whatever
   per-service health states the broader health-
   checks plan needs" — a smaller follow-on
   rather than a from-scratch implementation. The
   PLAN-health-checks.md file is **not** edited
   in this phase; a note in the phase plan flags
   that the broader plan should be revised when
   it lands, and we trust the operator to do
   that in the management session at the
   appropriate time.

2. **`shakenfist-utilities` home for the LB
   channel factory.** Resolved in favour of
   in-tree (`shakenfist/util/grpc_channel.py`)
   rather than the shared-utilities repo. The
   factory is small (~30 lines), has no other
   consumers, and the utilities-repo release
   cadence would couple this work awkwardly to
   an external release. If a future plan needs
   the same factory in another SF-family
   project, it can be lifted out then.

## Decisions (phase-local)

1. **Channel factory lives in
   `shakenfist/util/grpc_channel.py`** (not
   `shakenfist-utilities`). Resolves master-plan
   open question 2. The factory is small and
   has no consumers outside SF today; keeping
   it in-tree avoids a cross-repo release
   coupling.

2. **The factory takes `hosts` and `port` as
   arguments**, not via direct `config.*`
   reads. This avoids an import cycle:
   `config.py` needs to call the factory at
   module-import time during
   `load_cluster_config()`, but cannot import
   anything that imports `config`. By making
   the factory parameter-driven, it sits below
   `config` in the dependency graph.

3. **gRPC-native round-robin LB via the `ipv4:`
   static resolver and `service_config` JSON**.
   No external LB, VIP, haproxy, or service-
   discovery integration. The target string for a
   three-instance tier is:
   `ipv4:10.0.0.1:13005,10.0.0.2:13005,10.0.0.3:13005`.
   The `service_config` JSON sets:
   `{"loadBalancingConfig": [{"round_robin":
   {}}], "healthCheckConfig": {"serviceName":
   ""}}`.

4. **Client-side health-checking against the
   empty-string service name**. This is the
   gRPC convention for "overall server health".
   The server publishes its single SERVING
   status against the empty string; clients
   check against the same name. Per-service
   granularity (one health entry per RPC
   service) is *out of scope* for this phase —
   the broader `PLAN-health-checks` plan owns
   per-service granularity if it wants it.

5. **The minimal server-side `HealthServicer`
   lives in `daemons/database/main.py`'s
   `main()` function**, alongside the existing
   `grpc.server(...)` call. Registering and
   setting status are five lines of code; no
   abstraction is justified at this size.

6. **`grpcio-health-checking==1.70.0` is added
   to `pyproject.toml`** as a dependency. The
   version pins to match the existing
   `grpcio` / `grpcio-status` / `grpcio-tools`
   pins.

7. **Per-caller channel-option overrides via a
   small `options` dict**. The blob-path client
   in `shakenfist/database.py` needs max-message-
   size set to 100 MB; the bootstrap path needs
   no overrides; the `_get_database_stub` path
   needs nothing today. The factory's signature
   becomes
   `make_database_channel(hosts, port,
   extra_options: list[tuple[str, Any]] |
   None = None)`. The factory adds its default
   options first and any extras after; gRPC
   takes the last value for any duplicated key.

8. **No changes to `PLAN-health-checks.md`
   itself in this phase.** Phase 3 lands the
   minimal health-protocol implementation;
   PLAN-health-checks's phase 2 brief will need
   a small revision the next time it is touched
   to acknowledge that the protocol already
   exists and the work is now *extension*
   rather than *creation*. We trust the
   management session to make that change when
   the broader health-checks plan re-opens.

9. **The shutdown-side `NOT_SERVING` flip is
   in scope.** When sf-database receives a
   SIGTERM and starts shutting down, the
   health servicer flips the empty-service
   status to NOT_SERVING *before* the server
   stops accepting requests, so the LB-aware
   clients drain to other backends rather than
   seeing connection refusals. This is a small
   addition to the existing shutdown path and
   is the operator-friendliest behaviour for a
   rolling-upgrade scenario.

## Steps

Three sequential steps. Each step must leave the
tree buildable and pre-commit-clean.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | opus | worktree | Create `shakenfist/util/grpc_channel.py` with the `make_database_channel(hosts: list[str], port: int, extra_options: list[tuple[str, Any]] \| None = None) -> grpc.Channel` factory and switch every consumer to use it. The factory's body: construct `target = 'ipv4:' + ','.join(f'{h}:{port}' for h in hosts)`, build the default options list (the keepalive set shared by today's `mariadb.py` consumer plus the `grpc.service_config` JSON), append `extra_options` if given, return `grpc.insecure_channel(target, options=defaults + extra_options or [])`. The default options list (in order): `('grpc.keepalive_time_ms', 10000)`, `('grpc.keepalive_timeout_ms', 5000)`, `('grpc.http2.max_pings_without_data', 0)`, `('grpc.keepalive_permit_without_calls', 1)`, `('grpc.service_config', json.dumps({'loadBalancingConfig': [{'round_robin': {}}], 'healthCheckConfig': {'serviceName': ''}}))`. Use `import json` at the top. Add a module-level constant `_DEFAULT_OPTIONS` so the test can introspect what defaults look like. Migrate three consumers to use the factory: (a) `shakenfist/database.py:52` — replace the `grpc.insecure_channel(...)` call with `make_database_channel(config.MARIADB_GATEWAY_HOSTS, config.MARIADB_GATEWAY_PORT, extra_options=[('grpc.keepalive_timeout_ms', 200), ('grpc.max_send_message_length', 100000000), ('grpc.max_receive_message_length', 100000000)])`. Note the 200 ms timeout override — `database.py` wants a faster timeout than the factory default; passing it as an extra option duplicates the key and gRPC takes the last value. (b) `shakenfist/mariadb.py:328` — replace with `make_database_channel(config.MARIADB_GATEWAY_HOSTS, config.MARIADB_GATEWAY_PORT)`. No overrides. (c) `shakenfist/config.py:100` — the bootstrap-time channel. Replace the inline construction with `make_database_channel(hosts, int(db_port))` (`hosts` and `db_port` are the local variables already parsed in `load_cluster_config`). The bootstrap path runs at module-import time; verify the factory import does not introduce a cycle by checking that `shakenfist/util/grpc_channel.py` imports only `grpc` and `json`. Verify with `pre-commit run --all-files`. Worktree isolation because this rewires three load-bearing channel construction sites; getting any one wrong silently degrades the cluster. One commit. |
| 2 | high | opus | worktree | Server-side `grpc.health.v1.Health` registration in `shakenfist/daemons/database/main.py`. Add `grpcio-health-checking==1.70.0` to `pyproject.toml` in the same block as the existing `grpcio` pins. Add imports at the top of `daemons/database/main.py`: `from grpc_health.v1 import health`, `from grpc_health.v1 import health_pb2`, `from grpc_health.v1 import health_pb2_grpc`. In `main()` immediately after the `server = grpc.server(...)` block (around line ~5200) and before the `server.add_insecure_port(...)` call: instantiate `health_servicer = health.HealthServicer()`, register it with `health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)`, and mark the empty-string service SERVING with `health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)`. Add a short comment block explaining the empty-service-name convention. In the shutdown path: find where the existing daemon stops accepting requests (the `server.stop(1).wait()` near line 5220 and the `record_exit` call in the `Monitor` class). Add a `health_servicer.set('', health_pb2.HealthCheckResponse.NOT_SERVING)` call *before* `server.stop(1).wait()` so LB-aware clients see the status flip before the connection refusals start. The `health_servicer` reference needs to be reachable from the shutdown site — make it a local in `main()` and pass to the `Monitor` instance if necessary, or store on the Monitor. Choose whatever is least intrusive. Worktree isolation because this changes startup and shutdown ordering on the central daemon. One commit. |
| 3 | medium | sonnet | none | Tests and docs. (a) Unit tests in `shakenfist/tests/test_grpc_channel.py` (new file). Cover: `make_database_channel(['10.0.0.1'], 13005)` produces a channel object (assert it's a `grpc.Channel`); the target string is `'ipv4:10.0.0.1:13005'` (introspect the channel's target attribute if accessible, otherwise mock `grpc.insecure_channel` and assert the first positional arg); multi-element case produces `'ipv4:10.0.0.1:13005,10.0.0.2:13005'`; an empty hosts list raises `ValueError` (the factory should validate this); `extra_options` are appended to the defaults; the service_config JSON is parsed and contains `round_robin` and the empty-string `healthCheckConfig.serviceName`. (b) Smoke test for `HealthServicer` registration in a new `shakenfist/tests/test_database_health.py` (or extend `test_database.py` if there is one): instantiate `health.HealthServicer()`, register on a real `grpc.server()`, mark empty service SERVING, then use a `health_pb2_grpc.HealthStub` against the in-process channel to call `Check(HealthCheckRequest(service=''))` and assert `status == SERVING`. Then flip to NOT_SERVING and assert the next Check returns NOT_SERVING. This proves the wiring; the daemon-side integration is exercised in cluster_ci. (c) Docs: update the "MARIADB_HOST vs MARIADB_GATEWAY_HOSTS" subsection in `docs/operator_guide/database.md` (added in phase 2) with one short paragraph about the LB behavior — operators setting MARIADB_GATEWAY_HOSTS to a multi-element list get round-robin distribution across the endpoints, with unhealthy endpoints automatically skipped via the gRPC health protocol. Keep it brief; the operator-facing surface is "just set the list". (d) `pre-commit run --all-files`. One commit. |

## Validation

- `pre-commit run --all-files` passes after each step.
- After step 1: single-endpoint deploys still work
  (cluster_ci's existing single-instance pipeline
  is the integration check); manual grep for
  `grpc.insecure_channel` in `shakenfist/` returns
  zero hits outside `shakenfist/util/grpc_channel.py`.
- After step 2: sf-database starts cleanly; a manual
  `grpcurl` against `grpc.health.v1.Health/Check`
  returns `{"status": "SERVING"}` during normal
  operation. (`grpcurl` is a developer tool, not a
  production dependency.)
- After step 3: the unit tests pass and exercise
  the target-string + service-config behaviour
  explicitly so a regression to the wrong LB
  policy or wrong empty-service-name convention
  fails loudly.
- Phase 6 of byo-mariadb is the integration check
  for N>1 sf-database. Phase 3 itself does not
  exercise N>1 in CI — only the plumbing is in
  place.

## Risks

- **The `ipv4:` URI scheme + `service_config`
  combination is gRPC-Python specific.** It does
  work with `grpcio==1.70.0` (the version this
  project pins), but the brief for step 1 includes
  a smoke check: after construction, the channel's
  string representation includes the right host
  list. If the scheme has been renamed in this
  grpcio version (it shouldn't be), step 1's
  sub-agent should stop and report rather than
  shipping a silently-broken channel.
- **`json.dumps` of the `service_config` dict**:
  the JSON must use lowercase `loadBalancingConfig`
  / `healthCheckConfig` / `serviceName` —
  camelCase as documented by gRPC. The brief
  literal-quotes the JSON shape so a transcription
  typo is easier to spot in review.
- **Channel-target introspection in tests**: gRPC-
  Python may not expose the target string on the
  channel object in a way that's introspectable
  without mocking. If the unit test in step 3
  cannot read it directly, the test mocks
  `grpc.insecure_channel` and asserts on the call
  args. This is fine; it's still testing the
  factory's behavior.
- **Per-caller option overrides duplicating keys**:
  gRPC's behaviour for duplicate keys in the
  options list is "last value wins". The brief
  for step 1 calls this out for the `database.py`
  blob-path consumer that overrides
  `keepalive_timeout_ms`. If gRPC-Python at this
  version actually raises on duplicate keys
  rather than letting the last value win, the
  factory's signature changes from "append
  extras" to "deduplicate, then append". Step 1
  sub-agent should verify by smoke-testing the
  three-line snippet that produces a channel with
  a duplicated key, and report if duplicates are
  rejected.
- **Shutdown-time `NOT_SERVING` flip needs the
  `health_servicer` reference reachable from the
  shutdown site**. The brief gives the sub-agent
  flexibility in how to plumb that — local in
  `main()` and pass to Monitor, or stash on
  Monitor itself. The risk is forgetting the flip
  entirely; the brief calls it out as a discrete
  bullet so the sub-agent does not lose it
  during the bigger startup-side change.

## Out of scope

- `getsf` prompts, `roles/mariadb/` deletion,
  `tools/bootstrap-mariadb.sql`, and the broader
  deploy rewrite — phase 4.
- CI workflow step installing MariaDB outside
  `getsf` — phase 5.
- Multi-instance sf-database CI shape and N>1
  functional tests — phase 6.
- Per-service health granularity (one health
  entry per RPC service) — PLAN-health-checks
  if it wants it.
- Editing `PLAN-health-checks.md` itself — the
  next operator session that touches the
  health-checks plan should revise its phase 2
  brief to acknowledge the minimal protocol
  exists.
- `etcd_master` ansible group rename — PLAN-
  remove-primary phase 7.
- The `SHAKENFIST_MARIADB_HOST=localhost`
  ExecStartPre escape hatch — phase 4 dissolves
  it.

## Back brief

Before executing this phase, please back brief
the operator on:

- The three steps in order with the file and
  line boundaries for each.
- The decision to put the factory at
  `shakenfist/util/grpc_channel.py` (in-tree,
  not `shakenfist-utilities`).
- The decision to use the gRPC-native
  `ipv4:` static-resolver + `round_robin` +
  `healthCheckConfig` JSON combination, no
  external LB or service-discovery layer.
- The decision to land a minimal server-side
  health implementation here rather than wait
  for PLAN-health-checks (and the implication
  that PLAN-health-checks phase 2 becomes
  *extension* rather than *creation*).
- The decision to land the shutdown-time
  NOT_SERVING flip in scope here so rolling
  upgrades drain rather than fail abruptly.
