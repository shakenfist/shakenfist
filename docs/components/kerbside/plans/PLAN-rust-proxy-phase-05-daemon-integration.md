# Rust proxy phase 5: daemon integration + session termination

Part of the [Rust SPICE proxy master plan](/components/kerbside/plans/PLAN-rust-proxy/). Phase 4
made the Rust proxy an enforcing firewall. Phase 5 makes the Python
daemon **run** it: exec + supervise the Rust proxy as a child, and wire
API-driven session termination through the `ProxyControl` stream so
in-flight connections are actually dropped.

## Prompt

Ground every decision in the code. Read `kerbside/main.py` (`daemon_run`,
the supervisor), `kerbside/proxy.py` (the Python proxy it forks today),
`kerbside/rpc/{server,servicer}.py` (the gRPC UDS server + the
`ProxyControl` heartbeat stub), `kerbside/api.py` + `kerbside/db.py` (the
session-termination endpoints and their DB ops), `kerbside/config.py`
(the fields that become the proxy's CLI flags), and the Rust proxy
(`rust/kerbside-proxy/src/{main,session,relay,rpc}.rs`). Verify the
process model rather than assuming it. Flag uncertainty explicitly.

Planning effort: **high** — a cross-process control path (API → daemon →
proxy) with no existing push mechanism, plus subprocess supervision and
a Rust cancellation registry.

## Repository and branch logistics

All work is in **kerbside** (Python daemon + the Rust proxy crate); no
other repo is involved (the `ProxyControl`/`TerminateSession` proto
already exists from phase 2). Phase 5 builds on phase 4, which is
unmerged, so branch `rust-proxy-phase-5` from the phase-4 tip; rebase
onto `develop` once phases 4 (and its ryll dep, already merged) land.
This plan file lives on the phase-5 branch.

## Situation (verified 2026-07-07)

- **Process model — three processes.** `kerbside daemon run`
  (`main.py:cli` → `daemon_run`, `main.py:191-227`) is the **supervisor**:
  it forks the Python proxy as a `multiprocessing.Process(target=
  kerbside.proxy.run)`, then starts the gRPC UDS server
  (`rpc_server.serve()`), then loops polling child liveness — on child
  death it does `proxy.kill(); rpc_server.stop(); sys.exit(1)`. The
  **REST API** (`kerbside.api:app`) runs as a *separate gunicorn
  process*. The proxy is the forked child.
- **The Python proxy** (`proxy.py:run`, 560-643) binds the VDI ports and
  forks a `multiprocessing.Process` per accepted connection; a psutil
  sweep reaps dead workers against the `proxychannels` table. No SIGTERM
  handler; the daemon SIGKILLs it on shutdown.
- **The gRPC server** (`rpc/server.py:serve`) hosts `KerbsideProxyServicer`
  on a filesystem-guarded UDS (dir 0700 / socket 0600), in the daemon
  process, on a `ThreadPoolExecutor`. `ProxyControl` (`servicer.py:186`)
  is a **bare heartbeat generator** — it yields a `Heartbeat` every 30 s
  and knows nothing else. There is **no stream registry and no queue**;
  nothing outside the handler can push into a connected proxy's stream.
- **Session termination is DB-only.** `ConsolesTerminate`
  (`api.py:492`) and `SessionTerminate` (`api.py:668`) call
  `db.expire_token` and/or `db.remove_session` (which DELETES the
  `ConsoleToken` row) plus an audit event. They do **not** touch
  `proxychannels` and do **not** signal the proxy. Effect: new
  connections are denied (token gone), but **in-flight connections keep
  running until the client disconnects.** This is the gap phase 5 closes.
- **Config → CLI flags map 1:1** already: `VDI_ADDRESS`/`VDI_SECURE_PORT`/
  `VDI_INSECURE_PORT`/`CACERT_PATH`/`PROXY_HOST_CERT_PATH`/
  `PROXY_HOST_CERT_KEY_PATH`/`PROXY_HOST_SUBJECT`/`NODE_NAME`/
  `PROMETHEUS_METRICS_PORT`/`API_SOCKET_PATH`/`LOG_VERBOSE` →
  `--vdi-address`/`--secure-port`/`--insecure-port`/`--cacert`/`--cert`/
  `--cert-key`/`--host-subject`/`--node-name`/`--prometheus-port`/
  `--api-socket`/`--verbose`. The firewall knobs are delivered per
  connection over gRPC, NOT as CLI flags.
- **Absent today**: `find_proxy_bin()`, any Python↔Rust
  implementation-selection config, any `subprocess`/`Popen`/`execv` in
  `kerbside/`, and any `ProxyControl` push. All net-new here.
- **Rust side**: `main.rs` spawns a per-connection task with a semaphore
  permit held for its lifetime; there is **no session registry**.
  `shutdown_signal` unwinds the accept loops on SIGTERM but does not
  drain in-flight sessions (flagged a "phase-5 nicety"). `rpc.rs:
  run_proxy_control` already consumes the stream and matches
  `TerminateSession{session_id}` — currently a logged no-op.

## Mission

1. The daemon can run the **Rust** proxy as a supervised child
   (config-selected), deriving its argv from config, forwarding SIGTERM
   with a deadline, keeping exit-non-zero-on-child-death.
2. **API-driven session termination drops in-flight connections**: the
   `ProxyControl` stream delivers real `TerminateSession` events and the
   Rust proxy tears down the matching session's channels.
3. **Graceful drain on SIGTERM** (the deferred nicety): the proxy stops
   accepting and drains in-flight sessions within a deadline before exit.
4. Fold in the phase-3/4 deferred daemon-adjacent items: decouple the
   `/metrics` bind from the public VDI address, and validate the firewall
   config at daemon startup (complementing phase-4's per-request raise).

Out of scope (later phases): the maturin wheel + `find_proxy_bin()`
resolving an installed binary (phase 6 — here it resolves a dev/env
path); making Rust the default proxy and removing the Python proxy
(phase 8); OpenTelemetry.

## Design decisions

Decision 1 is settled with the operator (a `session_terminations` intent
table); decision 2 (the proxy default) is being confirmed. The rest are
the planner's calls, open to revision at back brief.

**Distributed-deployment constraint (settled, shapes decision 1).**
Kerbside can run distributed: the REST API and the proxy processes may
be on **different machines** (the CPU-heavy proxy is often on its own
hardware), and proxies can sit **behind a load balancer** so that
different channels of the SAME session land on **different proxy nodes**.
Therefore the ONLY shared component — the only bus — between the API and
the proxies, and between proxy nodes, is the **MariaDB database**. There
is no direct API→proxy or proxy→proxy IPC. The gRPC-over-UDS
`ProxyControl` channel is **local only** (a daemon and the single proxy
it supervises on the same host). Every cross-component signal must be a
DB record each consumer polls for its own scope, and a session-scoped
signal must be actioned independently by every node holding any of that
session's channels.

1. **Termination bridge: a `session_terminations` intent table (new,
   via an alembic migration).** The API — which may be on a different
   machine from any proxy — records termination as an explicit,
   session-scoped DB row; each proxy node's daemon polls it for the
   sessions IT has live channels for and pushes `TerminateSession` to
   its local proxy over the local UDS. Chosen over deriving termination
   from token-absence (implicit, conflates with expiry) and decisively
   over an API→daemon RPC (impossible across machines / behind a load
   balancer — see the constraint above). Concretely:
   - **Schema**: `session_terminations(session_id, requested_at,
     reason?)`, `session_id` indexed. An alembic migration + a `db.py`
     model and helpers.
   - **API** (`api.py` `ConsolesTerminate`/`SessionTerminate`): keep the
     existing token expire/remove (which blocks NEW connections) and
     ADD an insert into `session_terminations` (which signals dropping
     the IN-FLIGHT ones). Works regardless of which node(s) host the
     session.
   - **Daemon** (`servicer.py` `ProxyControl`, one stream per local
     proxy): each poll, emit `TerminateSession(session_id)` for every
     session that is BOTH in `session_terminations` AND in this node's
     live `proxychannels` (scoped to `NODE_NAME`), skipping ids already
     sent on this stream; interleave heartbeats. Natural token *expiry*
     is NOT a termination (matches today's Python proxy).
   - **Reaper**: a TTL sweep (in the daemon maintenance loop / token
     reaper) deletes `session_terminations` rows older than a few
     minutes — by then every node has polled and pushed; the Rust side
     is idempotent so a late/duplicate event is a no-op.
   - This keeps "Python owns the DB", needs no cross-machine RPC, and is
     correct when a session's channels are spread across nodes.

2. **Proxy selection: a config flag, default Python.** Add
   `PROXY_IMPLEMENTATION` (`python` default | `rust`). During the
   transition the daemon forks the Python proxy exactly as today unless
   configured for `rust`; phase 8 (cutover) flips the default and removes
   the Python path. Defaulting to Python means phase 5 is a no-op for
   existing behaviour until a deployment opts in. **Confirm the default.**

3. **Supervision via `subprocess.Popen`, not `os.execv`.** The daemon
   must keep running to host the gRPC server and supervise, so it spawns
   the Rust binary as a child (not exec-replace). A new
   `find_proxy_bin()` resolves the binary: `KERBSIDE_PROXY_BIN` env
   override → `PATH` → the dev build dir (`rust/kerbside-proxy/target/
   {release,debug}/kerbside-proxy`); the installed-wheel scripts dir is
   phase 6. The launcher builds argv from the config→flag table above,
   inherits stderr (so `tracing` lands in the daemon log stream), and on
   shutdown/child-death forwards SIGTERM then SIGKILL after a deadline,
   preserving exit-non-zero-on-child-death. The gRPC UDS must be bound
   before/around the child start (the Rust proxy dials it lazily and
   calls `ClearNodeChannels` at startup).

4. **Rust session registry + cancellation.** Add a shared
   `session_id -> CancellationToken` registry to `SharedState` (a
   `Mutex<HashMap<String, CancellationToken>>` plus a small refcount, or
   `tokio_util::sync::CancellationToken` children). After
   `AuthorizeConnection` returns a `Target` (which carries `session_id`),
   the connection registers under that id, obtaining/ cloning the
   session's token; the relay `select!`s on `token.cancelled()` alongside
   the two pumps, so cancelling the token tears the channel down. On
   teardown the connection deregisters (remove the entry when its last
   channel ends). `run_proxy_control`'s `TerminateSession` handler
   cancels the token for that `session_id` (idempotent if absent). This
   is the one place the phase-4 relay's `select!` gains a third arm.

5. **Graceful drain on SIGTERM (Rust).** On `shutdown_signal`: stop
   accepting new connections, then cancel-or-await in-flight sessions
   within a bounded deadline (e.g. a `--drain-timeout`, default ~10 s)
   before returning, so a supervised restart does not abruptly cut live
   sessions. Pairs with the Python supervisor's SIGTERM-then-SIGKILL.

## Open questions (to settle during the phase)

- The `ProxyControl` DB-poll interval (termination latency vs DB load);
  default ~1-2 s.
- Whether `find_proxy_bin()` should hard-fail at daemon startup if
  `PROXY_IMPLEMENTATION=rust` but no binary is found (recommended: yes,
  fail fast and loud).
- The drain deadline default and whether it is a proxy CLI flag or a
  daemon-passed one.
- Exact `db.py` query shape for "live channels whose session has no
  token" (anti-join vs two queries); and whether to scope it to the
  polling node only (yes).
- Whether `/metrics` gets a new `--metrics-address` flag (default
  loopback) or the daemon simply passes a management bind address.

## Execution

One commit per logical change. Every Rust step passes Docker `make lint`
+ `make test`; every Python step `tox -eflake8`/`-epy3`; `pre-commit
run --all-files` before each commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | worktree | **Rust session registry + cancellation.** Add a `session_id -> CancellationToken` registry to `SharedState` (`session.rs`) with refcounted insert/remove. After a successful `AuthorizeConnection` (`Target.session_id`), register the connection and pass a cloned token into `backend::run` → `relay::run`; add a third `select!` arm on `token.cancelled()` in `relay::run` that tears the channel down cleanly (like a `Terminate` teardown, no error). Deregister on teardown (remove the session entry when its last channel ends). Add `tokio_util` (CancellationToken) or implement with `tokio::sync` primitives. Unit-test: registering two channels of a session then cancelling drops both; deregister removes the entry; cancel of an unknown id is a no-op. Keep the phase-4 firewall behaviour unchanged. |
| 5b | high | opus | worktree | **Wire `TerminateSession` + graceful drain.** In `rpc.rs::run_proxy_control`, make the `TerminateSession` arm call the 5a registry to cancel the session (needs the registry handle threaded into the ProxyControl task via `SharedState`). In `main.rs`, on `shutdown_signal`: stop accepting, then drain in-flight sessions within a `--drain-timeout` (default ~10 s) before exit. Tests: a `TerminateSession` for a registered session cancels it; drain waits then exits. |
| 5c | high | opus | none | **Daemon supervises the Rust proxy.** Add `find_proxy_bin()` (env `KERBSIDE_PROXY_BIN` → PATH → dev target dir) and an argv builder from the config→flag table (Situation) in a new `kerbside/proxy_supervisor.py` (or within `main.py`). Add `PROXY_IMPLEMENTATION` to `config.py` (default `python`). In `main.py:daemon_run`, when `rust`, launch the binary via `subprocess.Popen` (inherit stderr) instead of forking `kerbside.proxy.run`; keep the gRPC-server-bound-first ordering, poll child liveness, and on shutdown/death forward SIGTERM then SIGKILL after a deadline, exit non-zero on death. Do NOT change the Python-proxy path when `python`. Tests: argv-from-config, find_proxy_bin resolution + hard-fail when `rust` and no binary, and the supervisor's SIGTERM-then-SIGKILL sequencing (mock the child). |
| 5d | high | opus | none | **Termination intent table + API + `ProxyControl` push (Design decision 1).** (a) Add a `session_terminations(session_id, requested_at, reason?)` model to `db.py` with an **alembic migration** (follow the existing migration style in `alembic/`), plus helpers: insert-intent, list-intents, delete-old-intents (TTL), and "session_ids terminated AND live on this node" (join `session_terminations` with `proxychannels` scoped to `NODE_NAME`). (b) In `api.py`, `ConsolesTerminate`/`SessionTerminate` keep the token expire/remove and ADD an intent insert. (c) Rewrite `servicer.py::ProxyControl` to poll the "terminated-and-live-here" helper each interval and yield `TerminateSession(session_id)` for each session not already sent on this stream, interleaved with heartbeats; robust to DB errors (log, continue). (d) Add a TTL reaper for old intent rows to the daemon maintenance loop. Tests: the API writes an intent; the stream yields `TerminateSession` for a terminated session live on this node but NOT for one only live on another node, NOT for a merely-expired token, and NOT twice; the reaper deletes aged rows. This may land as 2-3 commits (migration+model, API, servicer+reaper). |
| 5e | medium | opus | worktree | **Deferred-item cleanup.** Decouple `/metrics` from the public VDI address: add a proxy `--metrics-address` flag (default `127.0.0.1`) and bind the metrics server there; have the daemon pass it. Add daemon-startup validation of `FIREWALL_MODE`/`FIREWALL_PERMITTED_CHANNELS` (fail fast on a bad value, complementing phase-4's per-request raise). Optionally add per-unary gRPC deadlines in `rpc.rs` (NOT on the `ProxyControl` stream). Update `docs/configuration.md`. Tests as applicable. |
| 5f | high | opus | none | **End-to-end validation.** Extend `tools/direct-qemu` so a run can (a) launch the real daemon-supervised Rust proxy (not the mock), and (b) exercise **API-terminate → in-flight connection dropped**: connect a client, terminate its session, assert the proxy tears the connection down (metrics/log + the client disconnects). Add the deny/terminate assertions to the verify script. Document results in a VERIFY doc. If a full API+daemon live run is impractical in one pass, drive the `TerminateSession` path via the mock's ProxyControl (extend the mock to emit a TerminateSession) and record what was covered live vs mocked. |
| 5g | medium | sonnet | none | **Docs + Outcome.** Update `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `docs/proxy-architecture.md`, `docs/configuration.md` (the `PROXY_IMPLEMENTATION` knob, the supervision model, session-termination behaviour, `--metrics-address`). Write the Outcome; flip the phase-5 row in `PLAN-rust-proxy.md` + `docs/plans/index.md` to Complete after the pre-push audit. |

Sequencing: 5a → 5b (Rust cancellation) can proceed independently of 5c
→ 5d (Python daemon); 5e is small and independent; 5f validates the
whole; 5g last. 5a/5b and 5c/5d can be developed in parallel worktrees.

## Success criteria

- With `PROXY_IMPLEMENTATION=rust`, `kerbside daemon run` launches and
  supervises the Rust proxy: it comes up bound to the configured
  ports/certs, dials the gRPC UDS, and the daemon exits non-zero if it
  dies.
- Terminating a session via the REST API **drops that session's
  in-flight connections** promptly (previously they survived); natural
  token expiry does not (matching the Python proxy).
- SIGTERM to the daemon drains in-flight sessions within the deadline
  then exits cleanly.
- `PROXY_IMPLEMENTATION=python` (default) is unchanged behaviour.
- `/metrics` no longer binds the public VDI address by default.
- Rust `fmt`/`clippy -D warnings`/`test`; Python `flake8`/`py3`;
  pre-push audit clean; docs updated.

## Future work (recorded)

- Phase 6 packaging makes `find_proxy_bin()` resolve the installed wheel
  binary; phase 8 flips the default to `rust` and removes the Python
  proxy path.
- Policy-push over `ProxyControl` (live firewall-policy updates) — the
  oneof is already extensible; only `TerminateSession` is wired here.
- Lower-latency termination (push instead of poll) if the ~1-2 s DB-poll
  latency ever matters.
- Connection draining across proxy restarts (beyond a single SIGTERM
  deadline).

## Outcome

Completed 2026-07-08 on the kerbside `rust-proxy-phase-5` branch,
unmerged and unpushed pending operator review and the pre-push audit
(below). All planned steps landed. Every Rust step was built, linted
(`clippy -D warnings`), and tested in the Docker build (60 tests as of
5e, the last step to add Rust tests); every Python step passed
`flake8`/`py3`.

- 5a (`7f4c54b`): the Rust session cancellation registry —
  `SessionRegistry` (`session.rs`) mapping `session_id ->
  CancellationToken`, refcounted across a session's live channels and
  removed when the last one ends; threaded into `relay::run` as a
  third `select!` arm alongside the two pumps.
- 5b (`0f9eacd`): wired `TerminateSession` to the registry
  (`rpc.rs::run_proxy_control`, idempotent against a session this node
  does not host) and added graceful drain on shutdown — `main.rs`
  cancels every live session (`terminate_all()`) and waits up to a
  10-second `DRAIN_TIMEOUT` for the active-connection gauge to reach
  zero, replacing the phase-3 "detach and return" shutdown.
- 5c (`44f4519`): the daemon supervises the Rust proxy —
  `PROXY_IMPLEMENTATION` config (`python` default | `rust`),
  `kerbside/proxy_supervisor.py` (`find_proxy_bin()`,
  `build_proxy_argv()`, `launch_rust_proxy()`, `terminate_child()`), and
  `main.py:daemon_run`'s `rust` branch: bind the gRPC server first (a
  `Popen` child, unlike the multiprocessing fork, does not inherit its
  fds/threads, so the ordering constraint that protects the Python-proxy
  path does not apply here), launch, poll liveness, forward SIGTERM with
  a 15-second deadline then SIGKILL, exit non-zero on child death.
- 5d (`8664efe`): the cross-machine termination bridge — the
  `session_terminations` table (migration `c4e7a1b9d2f3`;
  `request_session_termination`, `get_terminations_for_node`,
  `reap_session_terminations` in `db.py`), `api.py`'s
  `ConsolesTerminate`/`SessionTerminate` recording the intent alongside
  the existing token expire/remove, and `servicer.py::ProxyControl`
  rewritten from a bare heartbeat generator to poll-and-push
  `TerminateSession` for sessions terminated and live on the polling
  node, interleaved with heartbeats and robust to transient DB errors.
- 5e (`256e973`): the deferred phase-3/4 cleanups — a proxy
  `--metrics-address` flag (default loopback, config
  `PROMETHEUS_METRICS_ADDRESS`) decoupling `/metrics` from the public
  VDI address, and daemon-startup firewall config validation
  (`build_firewall_policy()` called once in `daemon_run` so a bad
  `FIREWALL_PERMITTED_CHANNELS` fails fast and loud rather than failing
  every `AuthorizeConnection` at runtime). Per-unary gRPC deadlines
  stayed deferred (the UDS is local/trusted; a channel-wide timeout
  would break the long-lived `ProxyControl` stream).
- 5f (`fdc935d`): end-to-end live validation — a
  `--terminate-after-seconds` / `MOCK_GRPC_TERMINATE_AFTER` option added
  to `mock-grpc-server.py` so its `ProxyControl` stream emits a one-shot
  `TerminateSession` a configurable number of seconds after the first
  client authorization (firing reliably while a client is connected,
  regardless of connect timing), and
  `tools/direct-qemu/VERIFY-TERMINATION.md` recording the live result.

Notable decisions, all deliberate and none revised from planning:

- The two operator-level decisions held: a `session_terminations` intent
  table (not an implicit token-absence signal, and not an API→daemon
  RPC, which is impossible across machines / behind a load balancer),
  and `PROXY_IMPLEMENTATION` defaulting to `python` so phase 5 is a
  no-op for existing deployments until they opt in.
- **In-flight termination is effective only where a proxy consumes
  `ProxyControl` and acts on it — today, only the Rust proxy.** The
  API's intent-write (step 1 of the bridge) is unconditional, and the
  daemon's `ProxyControl` poll-and-push logic runs regardless of which
  local proxy is configured, but the Python proxy has no `ProxyControl`
  client at all, so a `PROXY_IMPLEMENTATION=python` node's in-flight
  connections are unaffected by termination, unchanged from before this
  phase — matching the plan's success criterion that the Python-proxy
  path stays unchanged.
- Subprocess supervision uses `subprocess.Popen`, not `os.execv`, per
  the plan (the daemon must keep running to host the gRPC server and
  the maintenance loop). The gRPC-server-bound-before-child-launch
  ordering is the *opposite* of the Python-proxy path's
  fork-before-server ordering, and for a different reason each way: the
  multiprocessing fork must not inherit the gRPC C-core's fds/threads,
  while the Rust child dials the UDS at its own startup and needs the
  socket to already exist.
- The drain deadline (10 s, Rust) and the daemon's SIGTERM-to-SIGKILL
  deadline (15 s) were fixed as plan-recommended defaults, with the
  daemon's deadline deliberately longer so the proxy gets first crack at
  draining gracefully before being forced.
- `find_proxy_bin()` hard-fails (`RuntimeError` naming every searched
  location) at launch time when `PROXY_IMPLEMENTATION=rust` and no
  binary is found, per the plan's recommended answer to that open
  question.

### Live termination validation (step 5f, 2026-07-08)

Full results were recorded in `tools/direct-qemu/VERIFY-TERMINATION.md`,
since folded into this Outcome (the harness documentation now lives in
`docs/direct-qemu-harness.md`). Run environment: release proxy binary
at ryll pin `1c6f19f`, `uncalibrated-sextant.qcow2` guest under qemu
(TCG), `MOCK_GRPC_TERMINATE_AFTER=15`, `remote-viewer` client.

`remote-viewer` was driven through the release proxy binary against the
`uncalibrated-sextant.qcow2` guest under qemu, establishing its 4 core
channels (`authorized_total=4`: main, display, inputs, cursor). 15
seconds after the first authorization the mock's `ProxyControl` stream
emitted `TerminateSession(session_id=rust-proxy-verify-session)`; the
proxy log showed the event received with `terminated=true` (the session
was found in the registry) followed by four `session terminated by
control plane; ending relay` lines, one per channel, and
`remote-viewer`'s process exited as its streams closed. **PASS**: a
live, connected session was dropped by a control-plane
`TerminateSession`, with the proxy log (not a coarse `/metrics` poll,
which could miss the simultaneous-teardown window) as the definitive
oracle. The Python half of the bridge (the intent table, the API write,
`ProxyControl`'s node-scoped selection, and the reaper) is covered by
the `kerbside/tests/unit` suite rather than this live run; the full
daemon+API+MariaDB path with a deterministic headless client (ryll) is
recorded as the natural phase-7 CI-lane driver.

### Pre-push audit (2026-07-08)

Ran the `PUSH-AUDIT.md` audit against the phase-5 diff (`git diff
rust-proxy-phase-4..HEAD`; phase 4 was audited separately). Wave 1
(mechanical) was clean: Docker `make lint`/`make test` and
`flake8`/`py3` green, and the only non-test `expect()`s are the
idiomatic `mutex.lock().expect("poisoned")` in the session registry (the
lock is held only across short synchronous map ops that cannot panic, so
it is never poisoned). Wave 2 was a security review and a
correctness/tests review of the phase-5 code. **No blocking, high, or
medium findings.**

The security pass verified: `subprocess.Popen` uses a list argv with no
`shell=True` and `close_fds` (no command injection from any config
value); the `db.py` helpers are fully parameterized SQLAlchemy (no
injection via `session_id`/`node`); the session refcount is balanced
across every `serve()` exit path with no underflow (`register` at one
site after authorize, `deregister` unconditionally after `backend::run`
on every outcome; the permit-denied path returns before registering);
the drain is hard-bounded (10 s, below the daemon's 15 s
SIGTERM→SIGKILL); `/metrics` on loopback is a net improvement; no
token/ticket is logged; and termination is node-scoped for the
distributed premise. The correctness review (with the trickiest items —
refcount and drain — corroborated by the security trace) found no
wrong-verdict/lost-count/hang/mapping bug.

Two low-severity findings were fixed before push (commit `74bc4f9`):
`find_proxy_bin` now treats an invalid `KERBSIDE_PROXY_BIN` override as a
hard error instead of falling through; and the `ProxyControl` `sent`
dedup set is bounded to the current active intents each poll.

Accepted / documented (not fixed): the relay cancel arm and
`drain_in_flight` at the `run()` level, and the `_run_rust_proxy`
daemon-supervises-Rust integration, are covered by their unit-tested
components plus the 5f live run; a full `kerbside daemon run`
(`PROXY_IMPLEMENTATION=rust`) integration test is left to the phase-7 CI
lane. All re-validated: Rust 60 tests + clippy clean, Python `flake8` +
`py3` (66 tests) clean, `pre-commit` clean.

## Back brief

Before executing any step, back brief the operator on the approach and
its alignment with this plan and the master plan. Decision 1 (the
termination bridge) is settled: a `session_terminations` intent table,
chosen for correctness under distributed deployment (API and proxies on
different machines, a session's channels possibly spread across
load-balanced nodes, DB as the only shared bus). Still to confirm:
decision 2, the `PROXY_IMPLEMENTATION` default (Python during the
transition vs Rust now).
