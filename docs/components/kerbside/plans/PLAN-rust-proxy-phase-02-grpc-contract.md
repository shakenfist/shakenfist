# Rust proxy phase 2: gRPC contract and Python UDS server

Parent plan: `PLAN-rust-proxy.md`. Planned at high effort (contract
design, a DB schema decision, and daemon-lifecycle work).

## Prompt

This phase lands entirely in the **kerbside** repository (Python),
on the `rust-proxy` branch, in the worktree
`/home/tars/src/shakenfist/kerbside-wt-rust-proxy`. Ground every
decision in the actual code — the exact function signatures and
return shapes below were verified against `develop` on 2026-07-05.

Reference implementations, in order of authority:

1. `kerbside/db.py`, `kerbside/proxy.py` (`ClientPassword` and the
   relay/reaper loop), `kerbside/main.py` (`daemon_run`),
   `kerbside/config.py`, `pyproject.toml`, `tox.ini` — the code
   being fronted and extended. This is the primary reference.
2. `shakenfist/shakenfist` gRPC patterns — mirror **only**
   `protos/database.proto` + `protos/_make_stubs.sh` + the
   `sf-database` servicer/lifecycle in
   `shakenfist/daemons/database/main.py` and the client factory in
   `shakenfist/util/grpc_channel.py`. Do **NOT** copy the
   privexec/nodelock/agent socket-envelope pattern (bare `oneof`
   messages, no `service`) — that is the unframed pattern this
   whole effort rejects (shakenfist/shakenfist#3315).
3. Phase 1 (merged, ryll `shakenfist-spice-protocol`) established
   what the proxy recovers before it needs the API: the decrypted
   token plaintext (from `decrypt_password`) and the channel
   identity (from `SpiceLinkMess`). Those are the inputs to the
   authorization RPC.

Consult `ARCHITECTURE.md`, `AGENTS.md`, `docs/proxy-architecture.md`,
and `docs/spice-link-protocol.md` for how the proxy uses the
database today.

## Repository and branch logistics

- Repo/worktree: `/home/tars/src/shakenfist/kerbside-wt-rust-proxy`
- Branch: `rust-proxy` (already carries the plan documents).
- All commits land here. The operator creates and merges the PR;
  never open one, and push only when asked.

## Situation

Kerbside is a Python system in which the SPICE proxy is already a
separate OS process whose only coupling to the rest of the system is
the shared MariaDB database (`kerbside/db.py`, a module-global
SQLAlchemy `ENGINE`). There is **no gRPC or protobuf anywhere in the
tree today** — this is a greenfield addition.

The proxy's entire database interaction over a connection's lifetime
is these calls (exact signatures/return shapes from `db.py`, call
order from `proxy.py`):

- Startup (proxy parent): `remove_node_channels(node)` — clears
  stale `proxychannels` rows.
- Per connection:
  - `record_channel_info(node, pid, client_ip=None, client_port=None,
    connection_id=None, channel_type=None, channel_id=None,
    session_id=None)` — a **partial upsert** on PK `(node, pid)` that
    writes only truthy fields; the proxy calls it **three times**
    (bare `(node,pid)`; then client/conn/channel fields; then
    `session_id`).
  - `get_token_by_token(token) -> {token, session_id, uuid, source,
    created, expires} | None` — filters server-side on
    `expires > now`, so expired tokens return `None`.
  - `get_source(name) -> {name, type, ca_cert, url, username,
    password, ...} | None` — proxy reads only `ca_cert`.
  - `get_console(source, uuid) -> {uuid, source, hypervisor,
    hypervisor_ip, insecure_port, secure_port, name, host_subject,
    ticket, discovered} | None` — note it filters by **uuid only**
    (the `source` arg is ignored).
  - `add_audit_event(source, uuid, session_id, channel, node, pid,
    message)` — proxy writes `'Channel created'`, hypervisor
    connect success/failure, and stall events.
  - Teardown (proxy parent reaper): `remove_proxy_channel(node, pid)`;
    the reaper also reads `get_node_channels(node) -> [ProxyChannel
    export dicts]`.

Relevant models (`db.py`): `ProxyChannel` (table `proxychannels`, PK
`(node, pid)`; `client_ip` is declared `Integer` but the proxy
passes a host **string** — a pre-existing latent inconsistency),
`ConsoleToken` (`created`/`expires` are UNIX epoch ints),
`Console`, `Source`, `AuditEvent` (PK `(source, uuid, timestamp)`,
`timestamp` a MySQL `DATETIME(fsp=6)` server default).

Process model: `kerbside daemon run` (`main.py:daemon_run`) is a
single-threaded process that `_parse_sources()` +
`_reap_expired_console_tokens()` every 60s and spawns the proxy as a
`multiprocessing.Process`; the proxy forks a worker per SPICE
channel. The daemon runs **no** server/listener today and has **no**
signal handler (it `sys.exit(1)` if the proxy child dies). The REST
API is a separate gunicorn process that owns token creation and
session termination.

Under the target architecture the Rust proxy (phase 3) replaces the
Python proxy and is a single tokio process, so the per-forked-worker
`pid` identity in `proxychannels` no longer maps.

## Mission

Add a gRPC service, exposed over a unix domain socket, that lets a
separate proxy process perform connection authorization and channel
/ audit bookkeeping without touching the database directly. Deliver:

1. A `.proto` contract for the service, with checked-in generated
   Python stubs and a codegen script + tox target.
2. A Python servicer implementing the unary RPCs against the
   existing `db.py`, preserving today's semantics (token expiry
   filter, audit events, channel bookkeeping).
3. The server hosted over a UDS inside the kerbside daemon process,
   configurable and with a clean lifecycle, without disturbing the
   still-running Python proxy (there is no Rust client until
   phase 3).
4. The streaming `ProxyControl` RPC **defined** in the contract and
   minimally stubbed, so phase 3 can pin a stable contract; its full
   session-termination/policy-push logic is phase 5.

Python owns policy and the database; the proxy will consult this
service. This phase is testable end-to-end in-process (a Python gRPC
client hitting the servicer over a temp socket) even though the Rust
client does not exist yet.

## Contract shape (proposed)

One `service` (mirroring `database.proto`'s single-service style),
`XxxRequest`/`XxxReply` messages, a generic
`StatusReply { bool success = 1; string error = 2; }`, and errors
signalled via `context.set_code()/set_details()` rather than enum
fields. Proposed methods:

- `AuthorizeConnection(AuthorizeConnectionRequest) ->
  AuthorizeConnectionReply` — the core call. Request carries the
  decrypted `token` plaintext, a proxy-generated `connection_ref`,
  `client_ip`, `client_port`, `connection_id`, `channel_type`,
  `channel_id`. The servicer runs `get_token_by_token` (expiry
  filter preserved) → `get_source` → `get_console`, and on success
  records the channel's `session_id` and writes the `'Channel
  created'` audit event, exactly as `ClientPassword` does today. The
  reply is a `oneof { Denied denied; Target target }` where `Target`
  carries `hypervisor, hypervisor_ip, insecure_port, secure_port,
  ticket, ca_cert, host_subject, source, uuid, session_id` and
  `Denied` carries a reason. This collapses today's three separate
  DB reads into one round trip.
- `RegisterChannel(RegisterChannelRequest) -> StatusReply` — the
  pre-authorization channel record (the first two
  `record_channel_info` calls): `node`, `connection_ref`,
  `client_ip`, `client_port`, `connection_id`, `channel_type`,
  `channel_id`.
- `RecordAuditEvent(AuditEventRequest) -> StatusReply` — `source`,
  `uuid`, `session_id`, `channel`, `node`, `connection_ref`,
  `message`.
- `DeregisterChannel(DeregisterChannelRequest) -> StatusReply` —
  `node`, `connection_ref` (teardown; maps to
  `remove_proxy_channel`).
- `ClearNodeChannels(ClearNodeChannelsRequest) -> StatusReply` —
  `node` (startup; maps to `remove_node_channels`).
- `ProxyControl(ProxyControlRequest) returns (stream
  ProxyControlEvent)` — server-streaming channel for session
  termination and policy pushes. **Defined now, stubbed now** (open
  the stream, optional keepalive); real events land in phase 5.

## Design decisions (settled)

1. **Mirror shakenfist's gRPC codegen, not its socket envelopes.**
   A single-`service` `.proto`; `grpc_tools.protoc` with
   `--python_out --grpc_python_out --mypy_out --mypy_grpc_out`; the
   `sed` import-rewrite to package-qualify the generated
   `import *_pb2` lines; wired to a `tox -egenprotos` target;
   generated `_pb2.py`/`_pb2_grpc.py`/`.pyi` **checked in**. Write
   the codegen as a script under `tools/` (per repo convention),
   invoked by the tox env.
2. **Transport: gRPC over a unix domain socket.** Server binds
   `add_insecure_port(f'unix:{socket_path}')`; the socket path is a
   new pydantic config setting. The server unlinks a stale socket
   before bind and the containing directory is created with
   restrictive (0700) permissions. Insecure gRPC creds are
   acceptable because the socket is local and filesystem-guarded
   (mirroring shakenfist's trusted-local-peer model); `SO_PEERCRED`
   is noted as future hardening.
3. **Host the server in the daemon process on a background thread.**
   The daemon (`daemon_run`) owns the DB engine and the business
   logic and only `sleep(1)`s, so a synchronous
   `grpc.server(ThreadPoolExecutor(...))` runs on its own
   thread(s) there. Register the servicer **before** `server.start()`
   and `server.stop(grace).wait()` on shutdown, per the shakenfist
   lifecycle. During this phase the daemon still spawns the Python
   proxy unchanged — the server simply coexists with no client yet.
4. **`AuthorizeConnection` collapses the three authorization reads**
   and performs the success-path `record_channel_info(session_id)`
   and `'Channel created'` audit, so the proxy needs one round trip
   at the decision point instead of four DB calls.
5. **Generated code is packaged.** Add the generated module to the
   `[tool.setuptools]` package list / discovery so
   `pip install kerbside` ships it, and add the runtime gRPC/protobuf
   dependencies to `pyproject.toml` (codegen tools to the dev/test
   extra).
6. **`ProxyControl` is contract-first.** It appears in the `.proto`
   so phase 3 pins a complete contract, but phase 2 only stubs the
   server side. This avoids a contract break when phase 5 adds the
   real push logic.

## Open questions

- **`proxychannels` identity / migration — DECIDED.** The table is
  PK `(node, pid)`; the Rust proxy is one process with no
  per-connection pid. Refined decision (operator, 2026-07-06): since
  kerbside will not be deployed until this whole effort lands, there
  is no production coexistence risk — but the Python proxy and its
  tests/CI must stay green through the transition, and its reaper
  genuinely needs OS pids (it matches psutil children to
  `proxychannels` rows to kill strays). So the migration is a
  **non-breaking superset** (the recommended "surrogate id PK + both
  keys" shape): add an autoincrement `id` primary key; keep `node`
  and `pid` (now nullable) for the Python-proxy path and its reaper;
  add a nullable `connection_ref` for the gRPC/Rust path; and fix the
  pre-existing `client_ip` `Integer`→string mismatch in the same
  migration. The gRPC servicer keys its rows by `(node,
  connection_ref)`; the Python proxy continues keying by
  `(node, pid)`. Readers (`get_sessions`, `get_console(detailed=True)`)
  key each channel on `node` + (`connection_ref` or `pid`). The
  `pid` column and the Python-proxy reaper are removed at the phase-8
  cutover, not now. (Rejected: replacing `pid` outright, which would
  drag phase-8 reaper work into this phase; and overloading the
  integer `pid` column with a synthetic id.)
- **`db.py` thread-safety from the gRPC threadpool.** `db.py` uses a
  module-global `ENGINE`; verify each servicer call uses its own
  Session/connection safely across the threadpool (the current code
  is called from single-threaded contexts). May need a
  `scoped_session` or per-call session discipline.
- **grpc/protobuf versions vs Python floor.** kerbside declares
  `requires-python >=3.7`; shakenfist runs grpcio 1.81 on py3.11.
  Pick grpcio/protobuf versions compatible with kerbside's
  deployment Python, likely bumping `requires-python`. Decide the
  floor against the direct-qemu CI and Kolla target.
- **Does the reaper move now?** The Python proxy parent currently
  reaps dead workers (`get_node_channels` + `remove_proxy_channel`).
  Under the Rust proxy the reaper changes; decide whether phase 2
  exposes `get_node_channels` / a reaper RPC now or leaves it to
  phase 5's daemon integration. Lean: leave reaping to phase 5;
  phase 2 exposes `DeregisterChannel` + `ClearNodeChannels` only.
- **Socket path default and deployment.** Choose a default (e.g.
  `/run/kerbside/api.sock`) and confirm the Kolla/direct-qemu layout
  co-locates the daemon and the proxy so they share the socket
  (they do today; the proxy is a child of the daemon).
- **`AuthorizeConnection` audit-on-denial.** Preserve today's audit
  writes on invalid token/console (proxy.py currently audits some
  denials). Decide which denials are audited and with what fields
  (note the existing `self.console['source']`-on-None latent bug in
  proxy.py must not be reproduced).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Design and write the `.proto` (e.g. `protos/kerbside.proto`, `syntax=proto3`, a package name) defining the single service with the methods in "Contract shape": `AuthorizeConnection` (oneof Denied/Target reply carrying every `Console`/`Source` field the proxy reads — see Situation), `RegisterChannel`, `RecordAuditEvent`, `DeregisterChannel`, `ClearNodeChannels`, and the server-streaming `ProxyControl`; a generic `StatusReply {bool success=1; string error=2;}`. Write `tools/gen-protos.sh` adapted from shakenfist's `protos/_make_stubs.sh` (grpc_tools.protoc with `--python_out --grpc_python_out --mypy_out --mypy_grpc_out`, then the sed import-rewrite to package-qualify `import *_pb2`). Add a `tox -egenprotos` target that runs it. Run it and check in the generated `_pb2.py`/`_pb2_grpc.py`/`.pyi`. Do NOT copy shakenfist's oneof/Errors-enum socket pattern. |
| 2b | medium | sonnet | none | Add dependencies to `pyproject.toml`: runtime `grpcio` + `protobuf` (versions compatible with the chosen Python floor — see Open questions; coordinate with the operator's decision), and `grpcio-tools` + `mypy-protobuf` to the `test`/dev extra. Ensure the generated proto module is included by `[tool.setuptools]` packaging so `pip install` ships it. Confirm `tox -epy3`/`-eflake8` still resolve. Do not change the build backend. |
| 2c | high | opus | none | (Gated on the Open-questions migration decision.) Implement the chosen `proxychannels` identity change as an alembic migration in `alembic/` following the existing migration style: add `connection_ref` (and re-key or index accordingly) and fix `client_ip` to a string column. Exercise both `alembic upgrade head` and `downgrade`. Update the `ProxyChannel` model in `db.py` and any reader (`get_console(detailed=True)`, admin UI) that references the changed columns. If the operator selects option (a) (reuse `pid`), this step is instead a small `db.py`/doc change with no migration — follow the decision recorded in the plan. |
| 2d | high | opus | none | Implement the gRPC servicer (e.g. `kerbside/rpc.py`) subclassing the generated servicer. Implement each unary RPC against `db.py`, preserving current semantics: `AuthorizeConnection` runs `get_token_by_token` (keep the `expires>now` filter), then `get_source`, then `get_console` (uuid-keyed), returns `Denied` with a reason on any miss (auditing denials per the Open-questions decision, WITHOUT the proxy.py None-deref bug), and on success records the channel `session_id` and writes the `'Channel created'` audit, returning a `Target`. `RegisterChannel`/`RecordAuditEvent`/`DeregisterChannel`/`ClearNodeChannels` map to `record_channel_info`/`add_audit_event`/`remove_proxy_channel`/`remove_node_channels` keyed by `connection_ref`. Signal internal errors via `context.set_code(grpc.StatusCode.*)/set_details()`. Ensure each call uses a safe per-call DB session (Open questions: db.py thread-safety). |
| 2e | high | opus | none | Host the servicer over a UDS in the daemon. Add config settings to `kerbside/config.py` (pydantic-settings pattern): the socket path (default e.g. `/run/kerbside/api.sock`) and threadpool size. In `main.py daemon_run`, start `grpc.server(futures.ThreadPoolExecutor(max_workers=N))`, register the servicer before `server.start()`, `add_insecure_port(f'unix:{path}')`, creating the socket dir 0700 and unlinking a stale socket first; run it on a background thread so the 1s maintenance loop is unaffected; stop it with `server.stop(grace).wait()` on daemon exit. Do NOT change how the Python proxy is spawned — the server coexists with it this phase. |
| 2f | medium | sonnet | none | Implement the `ProxyControl` server-streaming method as a minimal stub: accept the request, open the stream, and either return immediately or emit periodic keepalives; document that session-termination/policy events are added in phase 5. Keep the message types complete in the proto (2a) so the contract is stable. |
| 2g | high | opus | none | Tests under `kerbside/tests/unit`: spin the servicer on a temporary UDS in-process, connect a Python `grpc.insecure_channel(f'unix:{tmp}')` client, and cover every unary RPC — `AuthorizeConnection` success (valid token→Target), denial paths (unknown/expired token, unknown console), the expiry filter, `RegisterChannel`/`RecordAuditEvent`/`DeregisterChannel`/`ClearNodeChannels` writing the expected rows, and audit-on-denial. Use the existing test DB fixtures/patterns (stestr; check how db.py is tested today — likely a sqlite or mocked engine). If a migration landed in 2c, add an upgrade/downgrade test. Assert `context` error codes on failure inputs. |
| 2h | medium | sonnet | none | Documentation + tracking: update `ARCHITECTURE.md` (the new gRPC service and its place in the process model), `AGENTS.md` (the proto/codegen workflow, `tox -egenprotos`, the servicer location), `docs/configuration.md` (the new socket-path setting), and `README.md` if it lists components. Add an Outcome section to this phase plan and flip the master-plan/index status. |

Sequencing: 2a first (everything depends on the generated stubs).
2b after 2a (packaging the generated module). The migration
decision (2c) gates 2c and 2d's channel-keying; resolve it before
2d. 2d needs 2a+2c; 2e needs 2d; 2f needs 2a; 2g needs 2d+2e (+2c if
a migration landed); 2h last. 2b and the 2c decision can be settled
in parallel with 2a's proto drafting.

## Success criteria

* `tox -egenprotos` regenerates the stubs deterministically; the
  checked-in generated code matches; `tox -eflake8` and `tox -epy3`
  pass.
* A Python gRPC client over a temporary UDS drives every unary RPC;
  `AuthorizeConnection` returns a correct `Target` for a valid token
  and `Denied` for unknown/expired tokens and unknown consoles, with
  the `expires>now` behaviour preserved.
* Channel/audit RPCs write the same `proxychannels`/`auditevents`
  rows the Python proxy writes today (keyed by the chosen
  connection identity), and `'Channel created'` plus the hypervisor
  connect/stall audit events remain expressible.
* The daemon hosts the server over the configured UDS on a
  background thread without disturbing the Python proxy or the
  maintenance loop, creates the socket dir 0700, unlinks a stale
  socket, and shuts the server down cleanly.
* If a migration landed, `alembic upgrade head` and `downgrade` both
  succeed and the API's `proxychannels` readers still work.
* `ProxyControl` is present in the contract and stubbed; the
  contract is complete enough for phase 3 to pin.
* `ARCHITECTURE.md`, `AGENTS.md`, and `docs/configuration.md` reflect
  the new service and setting.

## Future work / handoff to later phases

- Phase 3 (Rust proxy skeleton) consumes this contract via a tonic
  UDS client (`connect_with_connector` + `tokio::net::UnixStream`);
  the `.proto` is shared or vendored into the proxy crate.
- Phase 5 implements the real `ProxyControl` events (session
  termination, policy push) and moves the reaper off the Python
  proxy.
- `SO_PEERCRED` peer-credential checks on the socket are deferred
  hardening.

## Outcome

Completed 2026-07-06 on the kerbside `rust-proxy` branch, commits
`d6fe28f`..(2h), unmerged and unpushed pending operator review. All
eight steps landed and pre-commit (flake8 + the full stestr unit
suite) passes.

- 2a: `kerbside/rpc/kerbside.proto` + `tools/gen-protos.sh` +
  `tox -egenprotos`; generated stubs checked in and verified to
  import as `kerbside.rpc`.
- 2b: `grpcio==1.81.1`/`protobuf==6.33.6` runtime pins,
  `grpcio-tools`/`mypy-protobuf` dev pins, `kerbside.rpc` packaged,
  `requires-python` bumped to `>=3.9`.
- 2c: alembic migration `9a3f1c7b2e40` (surrogate `id` PK, nullable
  `node`/`pid`, `connection_ref`, `client_ip`→string). Exercised
  upgrade/downgrade/re-upgrade against MariaDB 11.4.
- 2d: `kerbside/rpc/servicer.py` (five unary RPCs) +
  `record_channel_info_by_ref`/`remove_channel_by_ref` db helpers.
- 2e: `kerbside/rpc/server.py` serve()/stop() + daemon wiring +
  `API_SOCKET_PATH`/`API_GRPC_WORKERS` config; smoke-tested live.
- 2f: `ProxyControl` keepalive stub.
- 2g: `kerbside/tests/unit/test_rpc.py`, 10 tests over a temp UDS
  with the db layer mocked; all pass.
- 2h: this Outcome plus ARCHITECTURE.md / AGENTS.md /
  docs/configuration.md updates.

Deviations from the plan, all deliberate:

- **Migration shape** refined from "re-key to `(node, connection_ref)`"
  to the non-breaking "surrogate `id` PK, keep `pid`" superset (see
  the DECIDED note in Open questions), so the Python proxy and its
  pid reaper keep working until the phase-8 cutover.
- **`client_ip`** was already `String(15)` in the DB (only the ORM
  model wrongly said `Integer`); the migration reconciles the model
  and widens the column rather than doing a type conversion.
- **`requires-python`** raised to `>=3.9` (grpcio 1.81 and existing
  pins already required it; the old `>=3.7` was inaccurate).
- The **reaper** stays on the Python proxy (deferred to phase 5, per
  the plan's lean); phase 2 exposes only `DeregisterChannel` /
  `ClearNodeChannels`.

A harness-environment note (not a code issue): the system `tox`
rejects `FORCE_COLOR=3`, so pre-commit was run with `FORCE_COLOR`
normalized.

## Back brief

Before executing any step, back brief the operator on the intended
approach for that step and how it aligns with this plan and the
master plan. The `proxychannels` migration decision is settled
(option b — see Open questions); the remaining unsettled items
(db.py thread-safety approach, the grpc/protobuf version and Python
floor, and whether the reaper moves now) should be confirmed as
their steps come up.
