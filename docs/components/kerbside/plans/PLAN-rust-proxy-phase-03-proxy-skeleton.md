# Rust proxy phase 3: proxy crate skeleton

Parent plan: `PLAN-rust-proxy.md`. Planned at high effort — this is
the largest phase: it stands up the Rust proxy binary and gets a
SPICE client talking through it to a real hypervisor, end to end.

## Prompt

This phase lands in the **kerbside** repository (a new Rust binary
crate at `rust/kerbside-proxy/`), on branch `rust-proxy-phase-3`,
worktree `/home/tars/src/shakenfist/kerbside-wt-rust-proxy`. The
kerbside repo is Python today and has no Rust; this phase introduces
the first Rust crate. Ground every decision in the actual code — the
signatures and facts below were verified 2026-07-06.

References, in order of authority:

1. **The ryll `shakenfist-spice-protocol` crate** (phase 1, merged to
   ryll `develop`). This phase consumes it as a **git dependency**.
   The proxy reuses: the server-role handshake drivers
   (`read_link_mess`, `send_link_reply`, `send_need_secured`,
   `read_auth_ticket`, `send_auth_result`, `generate_ticket_keypair`,
   `decrypt_password`), the client connector (`SpiceClient`,
   `ConnectionConfig`), `SpiceStream` (incl. the `TlsServer`
   variant), `constants` (`ChannelType`, `SpiceError`,
   `AUTH_MECHANISM_SPICE`, `capabilities`), the mini-header framing
   (`messages::MessageHeader`, `make_message`, `SIZE = 6`), and
   `reader`/`LinkError`. Read the crate at the pinned rev before
   coding; exact signatures are in the Design section.
2. `kerbside/proxy.py` (`SpiceTLSSession`, `ClientPassword`, the
   relay loop, the `SpiceClient` backend connect + `need_secured`
   retry) — the behaviour being reproduced in Rust.
3. `kerbside/rpc/kerbside.proto` — the gRPC contract (phase 2) the
   proxy calls over the UDS; `kerbside/rpc/server.py` for the socket
   (`unix:` bind, dir 0700 / socket 0600).
4. `kerbside/config.py` — the config the proxy consumes, which
   becomes the proxy's CLI flags (the Python daemon passes them in
   phase 5).
5. `tools/direct-qemu/` (`lane-up.sh`, `start-qemu.sh`,
   `generate-tls.sh`, `start-kerbside.sh`, `smoke-client.py`) — the
   proxy-agnostic end-to-end harness used to verify the proxy.
6. ryll's build tooling: `.devcontainer/Dockerfile`, `Makefile`
   (cargo-cache mount pattern), `.github/workflows/ci.yml`
   (lint/build jobs), and `ryll/src/web/server.rs` for the
   rustls-server PEM-load + `ring` provider-install idiom.

## Repository and branch logistics

- Repo/worktree: `/home/tars/src/shakenfist/kerbside-wt-rust-proxy`
- Branch: `rust-proxy-phase-3` (off the post-phase-2 `develop`).
- All commits land here. The operator creates/merges the PR and
  pushes only when asked.
- No ryll changes are needed; ryll is consumed read-only as a git
  dependency.

## Situation

The Python proxy (`kerbside/proxy.py`) accepts SPICE clients on a TLS
port (5900) and a plaintext port (5901, redirect-to-secure),
terminates client TLS, does the SPICE link handshake (generating a
per-connection RSA key, decrypting the token), authorises against the
database, connects to the hypervisor's SPICE server (re-originating
TLS, re-encrypting the hypervisor ticket), and relays. Phase 2
replaced the proxy's direct DB access with the `KerbsideProxy` gRPC
service over a unix socket. Phases 1+2 provide everything the Rust
proxy needs: the SPICE server/client primitives (ryll crate) and the
authorization/bookkeeping RPCs (kerbside gRPC service).

Nothing about the Rust proxy exists yet. The Python proxy remains the
active proxy; this phase builds the Rust binary and verifies it
end-to-end **standalone** (run by hand against the direct-qemu
harness), without touching the daemon's spawn path — the daemon
exec-ing the Rust binary is phase 5.

## Mission

Produce a working `kerbside-proxy` Rust binary that a real SPICE
client (ryll headless) can connect *through* to a real hypervisor
(qemu), authorised via the gRPC service — an inspection-first framed
relay with a permissive (no-op) policy. Firewall enforcement (L0/L1)
is phase 4; daemon integration and session-termination push are
phase 5; the CI direct-qemu integration and loadtest are phase 7.
This phase delivers the crate, its Docker build + basic Rust CI
(fmt/clippy/test), and a documented repeatable end-to-end
verification.

## Design decisions (settled)

1. **ryll dependency: git-pinned.** Depend on the workspace-member
   crate by name against a pinned rev:
   ```toml
   shakenfist-spice-protocol = { git = "https://github.com/shakenfist/ryll", rev = "62d6737e8b2441b7beadefa5aedb42b1399aac27" }
   ```
   That rev is ryll `develop` and contains the server primitives (PR
   #138) and the later "Fix RSA public key parsing for QEMU SPICE
   auth". Cargo resolves the member crate by package name and builds
   only it + its light dependency subtree (not the ryll GUI binary
   or the detached fuzz crate). Switch to a tagged/published release
   when ryll publishes one (future work).
2. **gRPC client: tonic over the UDS.** Generate Rust stubs from
   `kerbside/rpc/kerbside.proto` with `tonic-build` in `build.rs`
   (reference the proto by relative path `../../kerbside/rpc/`).
   Connect with `Endpoint::try_from("http://[::]")?.connect_with_connector(
   service_fn(|_| UnixStream::connect(socket_path)))` (the URI
   authority is a dummy; the connector dials the socket). Use a
   **vendored protoc** (`protoc-bin-vendored` wired into `build.rs`)
   so the build needs no system protoc.
3. **rustls with the `ring` provider.** Add `rustls = { version =
   "0.23", features = ["ring"] }` and call
   `rustls::crypto::ring::default_provider().install_default()` once
   at startup (the crate's `SpiceClient` requires a process-default
   provider and errors otherwise). Match ryll's TLS stack: tokio 1.x,
   tokio-rustls 0.26.x, rustls 0.23.x, edition 2021.
3. **Client-facing handshake reproduces the Python proxy exactly.**
   Per connection: `generate_ticket_keypair()` →
   `read_link_mess()` → `RegisterChannel` RPC → success
   `SpiceLinkReply { error: Ok, pub_key: der, common_caps: vec![11],
   channel_caps: vec![9] }` via `send_link_reply()` →
   `read_auth_ticket()` (recovers the token plaintext) →
   `AuthorizeConnection` RPC → on `Denied`, `send_auth_result(
   PermissionDenied)` and close; on `Target`, `send_auth_result(Ok)`
   and proceed. The plaintext insecure listener does
   `read_link_mess()` then `send_need_secured()` and closes.
4. **Backend leg via `SpiceClient`, with a proxy-side `need_secured`
   retry.** Build `ConnectionConfig` from the `Target` (host =
   `hypervisor_ip` or `hypervisor`, `tls_port = secure_port`, `port =
   insecure_port`, `password = ticket`, `ca_cert`, `host_subject`)
   and call `connect_channel(connection_id, channel_type,
   channel_id)`. The crate does **not** retry on `NeedSecured` (it
   returns an error), so the proxy implements the retry: attempt the
   configured leg and, on the `NeedSecured` error, retry with TLS —
   mirroring `proxy.py`'s `RetrySecured`.
5. **Inspection-first framed relay with a no-op policy.** Relay is
   bidirectional, framing each SPICE message by its 6-byte
   mini-header (`MessageHeader`), passing every message through a
   `Policy` trait whose only v1 implementation returns `Forward`.
   This is the seam phase 4 fills with L0/L1 enforcement; it is *not*
   an opaque `copy_bidirectional`, per the master plan's
   inspection-first decision. One accepted client connection = one
   backend channel connection = one relayed SPICE channel (SPICE
   opens a TCP connection per channel).
6. **Bookkeeping over gRPC.** `ClearNodeChannels(node)` once at
   startup; `RegisterChannel` after the link message; the `Channel
   created` audit and session recording happen server-side inside
   `AuthorizeConnection`; `DeregisterChannel(connection_ref)` on
   teardown. `connection_ref` is a per-connection UUID the proxy
   generates. Additional audit events (hypervisor connect
   success/failure) via `RecordAuditEvent`.
7. **Metrics: a Rust-native Prometheus `/metrics` endpoint** on
   `PROMETHEUS_METRICS_PORT` (replacing the Python multiprocessing
   queue). `tracing` for logs from day one (master-plan decision 7).
8. **Concurrency:** tokio task per accepted connection. The handshake
   read drivers bound memory but not time, so the accept path wraps
   them in `tokio::time::timeout` and the proxy caps concurrent
   connections (the crate docs require the caller to own this).
9. **CLI bootstrap flags** (clap), defaults matching `config.py`:
   `--vdi-address` (0.0.0.0), `--secure-port` (5900),
   `--insecure-port` (5901), `--cert` (PROXY_HOST_CERT_PATH),
   `--cert-key` (PROXY_HOST_CERT_KEY_PATH), `--cacert` (CACERT_PATH),
   `--host-subject`, `--node-name` (kerbside), `--prometheus-port`
   (13003), `--api-socket` (/run/kerbside/api.sock), `--verbose`. No
   `--sql-url` (the gRPC service owns the DB).
10. **Build: Docker locally, rustup in CI.** Local builds use a
    small `kerbside-proxy-dev` Docker image (rust stable +
    build-essential + pkg-config; the `ring` provider needs no
    OpenSSL/cmake) driven by a Makefile mirroring ryll's cargo-cache
    mount pattern — matching the operator's "wrap Rust builds in
    Docker" preference. CI uses `dtolnay/rust-toolchain@stable` +
    `Swatinem/rust-cache@v2` directly on the runner (matching how
    kerbside CI already bootstraps Rust to build the ryll client),
    with a vendored protoc so no extra system package is needed.

## Key API facts (from the pinned ryll rev)

- Server drivers are generic `where S: AsyncRead + AsyncWrite +
  Unpin + Send`; `SpiceStream` implements those traits, so
  `SpiceStream::TlsServer(acceptor.accept(tcp).await?)` drops in.
  There is **no** server-side accept helper — the proxy builds the
  `TlsAcceptor` and wraps the stream itself.
- `generate_ticket_keypair() -> Result<(RsaPrivateKey, Vec<u8>)>`
  (DER is exactly 162 bytes). `decrypt_password(&RsaPrivateKey,
  &[u8; 128]) -> Result<String, LinkError>`. `read_auth_ticket`
  returns the password `String` and stops so the caller can
  authorize.
- `SpiceClient::new(ConnectionConfig) -> Result<Self>`;
  `connect_channel(connection_id, channel_type, channel_id) ->
  Result<SpiceStream>`. TLS iff `tls_port.is_some()`. On a
  `NeedSecured` reply it returns `Err(...)` (no retry). `host_subject`
  is **not enforced** (the CA verifier accepts hostname mismatch);
  see Open questions.
- `ChannelType` maps `Main=1 … Webdav=11`; server success caps are
  `common=11, channel=9` (matching `linkmessages.py`).
- Provider install is mandatory before any TLS
  (`ring::default_provider().install_default()`), idempotent via
  `let _ =`.
- CI TLS certs must be X.509 **v3** — rustls/webpki rejects v1
  (`generate-tls.sh` already emits v3 for exactly this reason).

## Open questions

- **`host_subject` enforcement — DECIDED (accept for the skeleton).**
  The Python proxy verified the hypervisor cert's subject against
  `console['host_subject']`; the ryll `SpiceClient` does not (it
  relaxes hostname checks and treats `host_subject` as
  informational). Decision (operator, 2026-07-06): accept the
  relaxation for the phase-3 skeleton and track real subject pinning
  as future work in the master plan (likely a ryll-crate change so
  ryll benefits too). Step 3e documents the non-enforcement inline
  with a TODO referencing that master-plan item; the security review
  should note it as a known, accepted gap rather than a new finding.
- **protoc for `tonic-build`.** Vendored (`protoc-bin-vendored`,
  hermetic) vs system protoc. Recommended: vendored, so neither the
  Docker image nor CI needs a protoc package.
- **Where the `.proto` lives for codegen.** `build.rs` references
  `../../kerbside/rpc/kerbside.proto` in-tree (couples the crate
  build to the repo layout — acceptable, same repo) vs vendoring a
  copy into the crate. Recommended: reference in-tree, with a build
  assertion that the file exists, so the Rust and Python stubs never
  diverge from one source.
- **Relay policy seam shape.** Confirm the `Policy` trait signature
  (`fn inspect(&mut self, dir: Direction, channel: ChannelType, msg:
  &FramedMessage) -> Verdict` with `Verdict::{Forward, Drop,
  Terminate}`) so phase 4 slots L0/L1 in without reshaping the relay.
- **Metrics library.** `prometheus` crate + a tiny hyper server, vs
  the `metrics` facade. Recommended: the `prometheus` crate with a
  minimal hyper `/metrics` handler (few deps, matches the Python
  exposition format).
- **CI runner for the Rust job.** ubuntu-latest hosted (like ryll's
  ci.yml lint/build) vs the kerbside self-hosted debian-12 runners.
  Decide in 3i; hosted ubuntu-latest is simplest for fmt/clippy/test.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Create the `rust/kerbside-proxy/` binary crate: `Cargo.toml` (edition 2021; deps per Design — git-pinned shakenfist-spice-protocol at rev 62d6737…, tokio 1 full, tokio-rustls 0.26, rustls 0.23 features ring, tonic + prost, clap, anyhow, thiserror, tracing + tracing-subscriber, rustls-pemfile, prometheus, hyper, uuid; build-deps tonic-build + protoc-bin-vendored), a `build.rs` that runs tonic-build against `../../kerbside/rpc/kerbside.proto` using the vendored protoc, and a `src/main.rs` skeleton that installs the ring provider, parses the CLI flags (Design decision 9), and inits tracing. Add a `.devcontainer`/Docker image + Makefile mirroring ryll's cargo-cache mount pattern, and confirm `cargo build`/`fmt`/`clippy` are green in the container. This step establishes the whole build; get the dependency resolution and tonic codegen working first. |
| 3b | high | opus | none | Implement the gRPC client module (`src/rpc.rs`): a tonic client over the UDS (`connect_with_connector` + `tokio::net::UnixStream` to `--api-socket`), with typed async wrappers for AuthorizeConnection, RegisterChannel, RecordAuditEvent, DeregisterChannel, ClearNodeChannels, and a ProxyControl stream consumer that logs events and is otherwise a no-op this phase. Map the `AuthorizeConnectionReply` oneof to a Rust `enum { Denied(String), Target(...) }`. Handle connection errors/reconnection to the socket. Unit-test against a stub server if practical, else structure for the phase-3h end-to-end test. |
| 3c | medium | sonnet | none | Implement the listeners + TLS (`src/listen.rs`): build a `tokio_rustls::TlsAcceptor` from the `--cert`/`--cert-key` PEM (rustls ServerConfig, `with_no_client_auth().with_single_cert(...)`, loaded via rustls-pemfile) — model the PEM load on ryll `src/web/server.rs`. Bind two `tokio::net::TcpListener`s (secure/insecure) on `--vdi-address`. The insecure listener, per accepted connection, wraps the plaintext stream and does `read_link_mess` then `send_need_secured` then closes. The secure listener hands off `SpiceStream::TlsServer(acceptor.accept(tcp).await?)` to the connection handler (3d). Wrap accept-path reads in `tokio::time::timeout`. |
| 3d | high | opus | none | Implement the client-facing handshake + authorization (`src/session.rs`), reproducing `proxy.py` ClientPassword over the crate drivers (Design decision 3): generate a `connection_ref` UUID; `generate_ticket_keypair`; `read_link_mess` (gives connection_id/channel_type/channel_id); `RegisterChannel` RPC with the client addr + channel fields; `send_link_reply` with a success reply (der pubkey, caps 11/9); `read_auth_ticket` → token plaintext; `AuthorizeConnection` RPC; on Denied → `send_auth_result(PermissionDenied)` + close (+ optional audit); on Target → `send_auth_result(Ok)` and pass the Target to the backend leg (3e). Reproduce the exact success caps and the AUTH_MECHANISM check the crate already enforces. Wire-compat is critical. |
| 3e | high | opus | none | Implement the backend leg (`src/backend.rs`): build `ConnectionConfig` from the `Target` (Design decision 4), `SpiceClient::new` + `connect_channel(connection_id, channel_type, channel_id)`; implement the `need_secured` retry the crate lacks (attempt then, on the NeedSecured error, retry via the TLS port). Emit `RecordAuditEvent` for hypervisor connect success/failure (mirroring proxy.py's audit messages). Return the connected backend `SpiceStream`. Document the host_subject non-enforcement (Open questions) with a clear TODO referencing the follow-up. |
| 3f | high | opus | none | Implement the inspection-first relay (`src/relay.rs`): a bidirectional relay between the client `SpiceStream` and the backend `SpiceStream`, framing each direction by the 6-byte `MessageHeader` (accumulate, read header, read `message_size` body, dispatch). Define the `Policy` trait + `Verdict::{Forward,Drop,Terminate}` seam (Open questions) and a `PermissivePolicy` that always Forwards. Forward framed messages unchanged this phase. Handle half-close/EOF and errors on either side cleanly; on teardown call `DeregisterChannel`. This is the seam phase 4 fills — get the framing and the Policy signature right. |
| 3g | medium | sonnet | none | Wire the lifecycle + metrics (`src/main.rs` + `src/metrics.rs`): `ClearNodeChannels` at startup; the accept loop spawning a tokio task per connection that runs 3d→3e→3f; a concurrency cap (semaphore); a Prometheus `/metrics` hyper server on `--prometheus-port` exposing at least connection counts and bytes relayed; SIGTERM handling for graceful shutdown. Assemble the binary so `kerbside-proxy --help` and a dry run (no clients) come up and bind cleanly. |
| 3h | high | opus | none | End-to-end verification: write `tools/direct-qemu/start-rust-proxy.sh` (or extend the lane) that runs the built `kerbside-proxy` standalone against the harness — reuse `start-qemu.sh` (qemu SPICE server), `generate-tls.sh` (v3 certs), and a running `kerbside daemon`/API for the gRPC socket + a static console + a token. Point ryll headless at a proxy `.vv` and assert surfaces via the control socket like `smoke-client.py`. Confirm the full path ryll → kerbside-proxy → qemu works with authorization via the gRPC service. Capture the procedure as a script + doc so it is repeatable. (Full CI integration is phase 7.) |
| 3i | medium | sonnet | none | CI + docs: add a GitHub Actions workflow (`.github/workflows/rust.yml`) with fmt/clippy(-D warnings)/test/build jobs (dtolnay/rust-toolchain@stable + Swatinem/rust-cache, vendored protoc), scoped to `rust/**`. Update ARCHITECTURE.md (the Rust proxy component + the eventual replacement of the Python proxy), AGENTS.md (the new Rust crate, build via Docker/Makefile, the git-pin), README.md, and `docs/proxy-architecture.md`. Add the Outcome section and flip the master-plan/index status. |

Sequencing: 3a first (nothing builds without it). 3b, 3c can proceed
in parallel after 3a. 3d needs 3b+3c; 3e needs 3b (RPC) + the crate;
3f needs 3d+3e; 3g needs 3d+3e+3f; 3h needs a working binary (3g);
3i last. Each step commits self-contained, passing `cargo fmt
--check`, `cargo clippy -- -D warnings`, and `cargo test` in the
Docker build; the management session runs those (the operator's host
stays Rust-free per their preference).

## Success criteria

* `cargo build --release`, `cargo fmt --check`, `cargo clippy -- -D
  warnings`, and `cargo test` pass for `rust/kerbside-proxy/` in the
  Docker build.
* A real SPICE client (ryll headless) connects through the running
  `kerbside-proxy` to a real qemu SPICE server and renders surfaces,
  with authorization performed via the `KerbsideProxy` gRPC service
  over the UDS — demonstrated by the phase-3h verification.
* The insecure port redirects clients to TLS (`need_secured`); the
  secure port terminates TLS, performs the handshake, authorises,
  connects to the hypervisor (with the `need_secured` retry), and
  relays.
* Channel bookkeeping and audit events appear via the gRPC service
  (RegisterChannel/AuthorizeConnection/DeregisterChannel, `Channel
  created`), matching what the Python proxy records.
* Relay is framed per-message through the `Policy` seam (not opaque
  splicing), with a permissive policy; the seam is shaped for phase
  4's L0/L1 enforcement.
* A `/metrics` endpoint serves Prometheus exposition; logs go through
  `tracing`.
* A Rust CI workflow runs fmt/clippy/test/build on every PR touching
  `rust/**`.
* `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, and
  `docs/proxy-architecture.md` describe the Rust proxy and its build.

## Future work / handoff to later phases

- Phase 4: fill the `Policy` seam with L0 (framing/size/rate limits)
  and L1 (per-channel/direction message-type allowlists) enforcement.
- Phase 5: the Python daemon exec()s this binary (passing the CLI
  flags), and the `ProxyControl` stream drives session termination.
- Phase 6: maturin bin-wheel packaging so `pip install kerbside`
  ships the binary.
- Phase 7: run the direct-qemu lane against the Rust proxy in CI, and
  the latency loadtest comparison.
- **`host_subject` enforcement** (Open questions) — add real
  hypervisor-cert subject pinning, likely in the ryll crate, before
  production. Tracked as a follow-up.
- Switch the ryll dependency from a git rev to a published/tagged
  release when ryll publishes one.

## Outcome

Completed 2026-07-06 on the kerbside `rust-proxy-phase-3` branch,
commits `ce10ef7`..(3i), unmerged and unpushed pending operator
review. The nine steps landed as planned; every Rust step was built,
linted (`clippy -D warnings`), and tested in the Docker build.

- 3a: `rust/kerbside-proxy/` crate skeleton, git-pinned ryll dep,
  tonic codegen (vendored protoc), Docker build + Makefile.
- 3b: tonic gRPC client over the UDS (mock-server tests).
- 3c: TLS acceptor + dual listeners (plaintext `need_secured`
  redirect; secure handler seam).
- 3d: client-facing handshake + `AuthorizeConnection`.
- 3e: backend leg via `SpiceClient` with the insecure-first
  `need_secured` retry.
- 3f: inspection-first framed relay + `Policy`/`Verdict` seam
  (permissive).
- 3g: lifecycle wiring, concurrency cap, Prometheus `/metrics`,
  SIGTERM shutdown.
- 3h: end-to-end verification harness (mock gRPC + qemu + proxy),
  documented today in `docs/direct-qemu-harness.md`. A live run
  (2026-07-06) drove `remote-viewer` through the proxy to a booted
  qemu SPICE server: 4 channels (main, display, inputs, cursor)
  authorised via the gRPC service and ~345 KB of display data relayed
  server→client (plus input client→server), with the mock logging
  `AuthorizeConnection` per channel and `DeregisterChannel` on
  teardown — the full client → proxy → hypervisor path.
- 3i: this Outcome, the `.github/workflows/rust.yml` CI (fmt / clippy
  / test / build), and doc updates.

Notable deviations / findings, all deliberate:

- **`protoc-bin-vendored` is at 3.x, not 0.5** (a sub-agent guessed);
  corrected during 3a.
- **`host_subject` not enforced** — the ryll `SpiceClient` relaxes
  subject matching; accepted for the skeleton and tracked in the
  master plan (Future work), documented inline at `backend.rs`.
- **`need_secured` retry uses a fragile string match** on the crate's
  error, since the crate has no typed `NeedSecured` error — noted for
  a future ryll-crate improvement.
- **AF_UNIX `SUN_LEN` (~108 bytes)** — the gRPC socket path must stay
  short; discovered during 3h and guarded in `verify-rust-proxy.sh`.
- CI runs on the repo's self-hosted debian-12 runners (matching the
  existing workflows), not hosted ubuntu.

### Pre-push audit (2026-07-06)

Ran the `PUSH-AUDIT.md` pre-push audit against
`git diff origin/develop..HEAD` (kerbside only; ryll is a pinned
dependency, unchanged this phase). Wave 1 (mechanical) was clean: Docker
`make lint` (fmt + `clippy -D warnings`) and `make test` green, flake8
clean on both phase-3 Python files, no `unsafe`, all `expect()` on
non-network invariants. Wave 2 was four judgment agents (code quality,
tests, docs, security-opus). **No blocking, critical, or high findings.**
The security pass confirmed the skeleton is memory-safe, panic-safe
against a hostile client, credential-safe (the decrypted token and
hypervisor ticket are never logged), and TLS fail-closed.

Two findings were fixed before push:

- **Unused `thiserror` dependency** (code quality) — dropped from
  `Cargo.toml`; the crate is consistently `anyhow`-based.
- **Backend connect had no timeout** (security, MEDIUM — the top
  permit-pinning DoS: a hypervisor that accepts TCP but stalls the SPICE
  handshake would hold a concurrency permit forever). Wrapped each
  `connect_once` attempt in a 30 s `BACKEND_CONNECT_TIMEOUT`
  (`backend.rs`); the timeout message deliberately does not match
  `is_need_secured`, so a stalled insecure attempt is not mistaken for a
  TLS-required signal. Also extracted the `ConnectionConfig`-from-`Target`
  mapping into a testable `build_config` and added unit tests for it
  (host fallback, always-`Some` ticket, empty-string → `None`) plus the
  timeout-message check — closing the test-review's cheapest regression
  gap. Test count 14 → 19.

Deferred (non-blocking; hardening/coverage for phase 4/5, not the
phase-3 charter):

- **Relay idle timeout / client TCP keepalive** (security, MEDIUM/LOW) —
  an authorized-but-silent client (or a client whose network is yanked)
  can pin a permit; the backend leg already has keepalive, the client leg
  only `set_nodelay`. A generous idle bound wants real interactive-SPICE
  traffic to pick a safe value → phase 4/5.
- **gRPC per-call deadlines** (security, LOW) — a hung (not crashed)
  daemon would stall permit-holding tasks; the UDS is local/trusted. Must
  be per-unary-call, not a channel-wide timeout (that would kill the
  long-lived `ProxyControl` stream).
- **`/metrics` bind defaults to the public VDI address, unauthenticated,
  no connection cap** (security, LOW) — soft DoS surface, no credential
  leak (aggregate counts only). Consider a loopback/management-address
  default and an accept cap; keep the "firewall the metrics port" note
  loud in deploy docs.
- **Metrics-server bind failure tears down the SPICE listeners** via the
  shared `main.rs` `select!` (code quality) — confirm this
  "any-startup-failure-is-fatal" coupling is intentional, or make the
  metrics server non-fatal.
- **Session denial/error signalling paths untested** (tests) — neither
  unit tests nor the 3h harness drive a `Denied`/RPC-error outcome (the
  mock always returns `Target`), so `send_auth_result(PermissionDenied)`
  vs `Error` back to the client is unverified end-to-end. Best closed by
  a "deny this token" mode in the mock gRPC server (phase-7 e2e) and/or a
  `serve` unit test.
- **`need_secured` retry orchestration untested** (tests) — the string
  match is well covered, but the retry-triggering flow in `run()` is
  exercised nowhere (the mock's `secure_port` defaults to 0). Wants a
  TLS-required backend leg in the harness.
- **`ConnectionGuard` gauge-balance test** (tests) and advisory polish
  (`rpc.rs` `StatusReply{success:false}` branch, `tls.rs` missing-key /
  empty-cert branches, `listen.rs` timeout/malformed-stream paths).
- **Struct-ify the repeated identity params** (code quality) — five
  `#[allow(clippy::too_many_arguments)]` functions share a
  loosely-typed `client_port`/`connection_id`/`channel_id` `u32` bundle;
  a `ConnectionContext` struct would remove the allows and a latent
  transposition risk. Phase 4.
- **Relay reassembly buffer capacity is never shrunk** (code quality) —
  bounded and freed on close, but a direction that relays one ~16 MiB
  message retains that capacity for the connection's life; revisit if
  phase 4 tightens memory budgets.

## Back brief

Before executing any step, back brief the operator on the intended
approach for that step and how it aligns with this plan and the
master plan. Confirm the remaining Open questions (host_subject
handling, protoc vendoring, the Policy seam signature, metrics
library, and the CI runner choice) as their steps come up.
