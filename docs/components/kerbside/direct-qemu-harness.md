# The direct-qemu mock harness

`kerbside-proxy` (the Rust SPICE proxy, `rust/kerbside-proxy/`) can be
verified end to end without MariaDB or the full Python daemon, using a
real qemu SPICE server, a small mock of the `KerbsideProxy` gRPC control
service, and a real SPICE client. This exercises the whole proxy path:
TLS termination, the SPICE link handshake, token decryption, the
`AuthorizeConnection` round trip over the unix socket, the backend
connect to the hypervisor, and the bidirectional inspection-first relay.

The full ryll-driven direct-qemu lane (with surface/digest assertions)
is CI. This standalone harness is for local verification.

## Two ways to exercise the Rust proxy

There are two distinct paths, for two purposes:

- **Mock harness (this document): local, MariaDB-free.**
  `tools/direct-qemu/start-rust-proxy.sh` launches the binary directly
  against `mock-grpc-server.py`, and `verify-rust-proxy.sh` asserts via
  `/metrics`. No daemon, no REST API, no database — fast to iterate on
  the proxy itself.
- **Daemon lane: the real integration path, in CI.** The direct-qemu
  functional lane runs the actual `kerbside daemon run` supervising the
  wheel-installed `kerbside-proxy` binary (resolved by
  `find_proxy_bin()` on `PATH`), against real MariaDB + the REST API +
  ryll, and asserts the full `run-scenario.sh` Sextant scenario plus a
  live API-terminate test and a non-gating loadtest. Bring one up
  locally with `tools/direct-qemu/lane-up.sh` (the `kerbside-proxy`
  wheel installed, or `KERBSIDE_PROXY_BIN` set).

## Pieces

- `tools/direct-qemu/mock-grpc-server.py` — a standalone
  `KerbsideProxy` gRPC server on a unix socket that authorises every
  token and returns a `Target` pointing at the qemu SPICE port with the
  qemu ticket. It mirrors the real servicer's contract
  (`kerbside/rpc/servicer.py`) so the proxy cannot tell the difference.
  Run it with a Python that has `grpcio` installed and the `kerbside`
  package importable (`pip install -e .` or `PYTHONPATH=<repo>`).
- `tools/direct-qemu/start-rust-proxy.sh` — launches the built
  `kerbside-proxy` binary with the CI TLS material and the mock socket.
- `tools/direct-qemu/verify-rust-proxy.sh` — orchestrates qemu + mock +
  proxy (`up`), asserts relay activity via the proxy's Prometheus
  `/metrics` (`assert` and `assert-firewall`), and tears everything
  down (`down`). The client step is pluggable: `remote-viewer
  <workdir>/console.vv` (GUI), or ryll headless + `smoke-client.py`.

Build the binary first (Docker, per the crate Makefile):
`make -C rust/kerbside-proxy build` (debug) or a `--release` build.

## The metrics assertion

After a client connects, `verify-rust-proxy.sh assert` polls
`http://127.0.0.1:<prometheus-port>/metrics` and requires
`kerbside_proxy_authorized_total >= 1` and
`kerbside_proxy_bytes_relayed_total > 0` for **both** the
`client_to_server` and `server_to_client` directions — i.e. a
connection was authorised and real SPICE traffic flowed in both
directions through the framed relay.

## Validating the SPICE firewall

The proxy takes its firewall policy **only** from the
`AuthorizeConnection` gRPC reply (`FirewallPolicy`). So the firewall is
validated by having the mock control plane deliver a **warn-only**
policy and then scraping Prometheus: a full legitimate session that
trips **zero** firewall verdicts proves the compiled allowlist and size
caps cover real traffic. No Rust flags are involved — the mock is the
policy source.

### The knobs the mock delivers

`mock-grpc-server.py` attaches a `FirewallPolicy` to every SUCCESS
reply and can deny connections:

- `--firewall-mode {enforce,warn}` / `MOCK_GRPC_FIREWALL_MODE` —
  `FirewallPolicy.mode`. `warn` = WARN_ONLY: blocking verdicts are
  downgraded to forward+log, counted as `action=observed`. The session
  is never actually blocked — this is the safe capture mode. Default
  `enforce`.
- `--permitted-channels CSV` / `MOCK_GRPC_PERMITTED_CHANNELS` — channel
  NAMES (`main,display,inputs,cursor,playback,record,tunnel,smartcard,usbredir,port,webdav`)
  mapped to `ChannelType` discriminants 1..11, exactly like
  `kerbside/rpc/servicer.py`. Empty (default) = permit all channels.
- `--deny-token TOKEN` (repeatable) / `MOCK_GRPC_DENY_TOKEN` (CSV) —
  return `Denied(reason=...)` instead of a `Target` when the decrypted
  plaintext token matches — drives the proxy's
  `send_auth_result(PermissionDenied)` path. Denied replies carry NO
  `firewall_policy`.
- `--deny-all` / `MOCK_GRPC_DENY_ALL` — deny every
  `AuthorizeConnection` unconditionally.

`verify-rust-proxy.sh` threads these in as env vars: `FIREWALL_MODE`,
`PERMITTED_CHANNELS`, `DENY_TOKEN`, `DENY_ALL`. (`start-rust-proxy.sh`
is unchanged by them — it launches the proxy binary, which has no
firewall CLI surface; policy arrives over gRPC.)

### Running a warn-only capture session

Bring the path up with the mock delivering WARN_ONLY, connect a real
client, then assert the session was clean:

```sh
# 1. Bring up qemu + mock (warn-only) + proxy, write console.vv.
FIREWALL_MODE=warn tools/direct-qemu/verify-rust-proxy.sh up

# 2. Connect a real SPICE client through the proxy and drive a full
#    session (log in, move the mouse, type, resize — exercise every
#    channel):
remote-viewer /tmp/kerbside-rust-proxy-verify/console.vv
#    ...or virt-viewer, or ryll headless.

# 3. Assert the legitimate session tripped ZERO firewall verdicts.
tools/direct-qemu/verify-rust-proxy.sh assert-firewall   # FIREWALL_EXPECT=clean (default)

# 4. Tear down.
tools/direct-qemu/verify-rust-proxy.sh down
```

Repeat step 2 for each supported client: **virt-viewer**,
**remote-viewer**, and **ryll headless**.

`assert-firewall` (with the default `FIREWALL_EXPECT=clean`) waits for
a real session — `kerbside_proxy_authorized_total >= 1` and
`kerbside_proxy_bytes_relayed_total > 0` in both directions — then
reports the `kerbside_proxy_firewall_verdicts_total` series split into
`enforced` vs `observed` and **passes only if both sums are 0**:

```
[verify-rust-proxy] authorized=4 denied=0 bytes{c2s}=... bytes{s2c}=... verdicts{enforced}=0 verdicts{observed}=0
[verify-rust-proxy] verdict series:
[verify-rust-proxy]   (no firewall_verdicts_total series present -- zero verdicts)
[verify-rust-proxy] PASS: full session relayed with ZERO firewall verdicts (allowlist + caps cover all observed traffic)
```

Because the run is WARN_ONLY, any verdict shows up as
`action=observed`: it tells you exactly what `Enforce` *would* have
blocked on legitimate traffic — a false positive. The fix is to widen
the compiled allowlist table or the size cap for the offending
`(channel, direction, rule)` printed in the failure — never to weaken
the verdict. Feed observed peak message sizes back into the compiled
size caps.

### Running the deny-mode check

To drive the `PermissionDenied` path, deny the token in `console.vv`
(its `password=` is the plaintext token after the proxy decrypts it) or
deny everything:

```sh
# Deny everything (simplest):
DENY_ALL=1 tools/direct-qemu/verify-rust-proxy.sh up
remote-viewer /tmp/kerbside-rust-proxy-verify/console.vv   # should be refused
FIREWALL_EXPECT=deny tools/direct-qemu/verify-rust-proxy.sh assert-firewall
tools/direct-qemu/verify-rust-proxy.sh down

# Or deny one specific token:
DENY_TOKEN=rust-proxy-verify-any-token-works \
    tools/direct-qemu/verify-rust-proxy.sh up
```

With `FIREWALL_EXPECT=deny`, the assertion requires
`kerbside_proxy_denied_total >= 1` (it does **not** require bytes
relayed, since the session is legitimately refused before relay). This
is the expectation flag that distinguishes a deny-mode run from a clean
capture run.

## Validating session termination

API-driven session termination drops in-flight SPICE connections. In
production the REST API writes a `session_terminations` intent row,
each proxy node's daemon polls it and pushes a `TerminateSession` over
the local `ProxyControl` gRPC stream, and the Rust proxy cancels every
channel of that session. The harness proves the proxy end of that path
live, without a full daemon+API+DB stack, by having the mock gRPC
server emit the `TerminateSession`.

`mock-grpc-server.py --terminate-after-seconds N` (or
`MOCK_GRPC_TERMINATE_AFTER=N`) makes the mock's `ProxyControl` stream
emit a one-shot `TerminateSession(session_id)` **N seconds after the
first client authorization** — so it fires while a client is reliably
connected, regardless of when the client connects relative to the proxy
starting.

```sh
export WORKDIR=/tmp/k5term
export MOCK_GRPC_PYTHON="$PWD/.tox/py3/bin/python" PYTHONPATH="$PWD"
export RUST_PROXY_BINARY="$PWD/rust/kerbside-proxy/target/release/kerbside-proxy"
export MOCK_GRPC_TERMINATE_AFTER=15
tools/direct-qemu/verify-rust-proxy.sh up
remote-viewer "$WORKDIR/console.vv"    # connects; 15s later it is dropped
# ... the client is disconnected; then:
tools/direct-qemu/verify-rust-proxy.sh down
```

A clean result is visible in the proxy log (`$WORKDIR/rust-proxy.log`):
the proxy receives the event and the registry matches the session, then
every channel's relay tears down — `terminated=true` plus one
`session terminated by control plane; ending relay` line per channel.
(A `/metrics` `active_connections` sampler is a poor oracle here: all
relays end simultaneously, so a coarse poll can miss the connected
window. The proxy log is the definitive signal.)

The Python half of the bridge — the `session_terminations` table, the
API writing the intent, `ProxyControl` selecting the sessions live on
this node and pushing them, and the TTL reaper — is covered by the
`kerbside/tests/unit` suite, and the full daemon+API+MariaDB path by
the direct-qemu CI lane's `verify-terminate-live.sh`.

## Caveat: unix socket path length (SUN_LEN)

The gRPC control socket is an `AF_UNIX` path, limited to ~108 bytes
(`SUN_LEN`). A deep working directory will overflow it and both the
mock's bind and the proxy's connect fail with `path must be shorter
than SUN_LEN`. Keep the socket path short — `verify-rust-proxy.sh`
defaults it under `$XDG_RUNTIME_DIR` (or `/tmp`), not under the
(possibly deep) workdir, and errors early if a `GRPC_SOCKET` override
is too long.
