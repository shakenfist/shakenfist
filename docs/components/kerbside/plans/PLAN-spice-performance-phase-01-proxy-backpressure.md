# Phase 1: proxy backpressure and a WAN baseline

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).
Planning effort: medium, per the master plan. The code change
is small. Most of the effort is in the measurement, which is
new ground for this repository.

## Situation

- **The relay pump** (`rust/kerbside-proxy/src/relay.rs:211-352`)
  reads up to 64 KiB, forwards each complete message with
  `write_all`, and flushes. It stops reading while a write is
  blocked, so the proxy itself queues at most one read's worth.
  The queueing that matters happens in the kernel:
  - on the client leg, unsent bytes pile up in the send buffer.
    It is autotuned up to `net.ipv4.tcp_wmem`'s maximum
    (commonly 4 MiB).
  - on the backend leg, the receive buffer is autotuned up to
    `tcp_rmem`'s maximum (commonly 6 MiB). It keeps the window
    to spice-server open.
- **The consequence.** At WAN rates the queued data is seconds
  of stale display updates. spice-server never sees its socket
  stall, so its "blocked" state and stream frame dropping
  (`red-channel-client.cpp:665,685`, `video-stream.cpp:354`,
  spice 91d42c4d) never engage. We expect keypress-to-draw
  latency during screen activity to grow with link delay
  multiplied by buffer depth, not link delay alone. That is a
  hypothesis, and this phase measures it.
- **Socket options today.** `listen.rs:100,169` set only
  `TCP_NODELAY` and keepalive on the client leg. The backend
  socket is dialled inside Ryll's `shakenfist-spice-protocol`
  crate. Its `SpiceStream` enum is public, with `Plain(TcpStream)`,
  `Tls(ClientTlsStream<TcpStream>)` and
  `TlsServer(ServerTlsStream<TcpStream>)` variants
  (`ryll/shakenfist-spice-protocol/src/link.rs:35-39`). So
  kerbside can reach both raw sockets via `get_ref()` without
  changing ryll.
- **Measurement.** Kerbside has no recorded latency figures.
  The latency loadtest (`loadtests/latency/orchestrator.py`,
  `docs/testing.md:377-388`) measures keypress-to-`surface_drawn`,
  but only on loopback in CI.
- **Shaping works without privileges.** Verified on 2026-09-23:
  on this host, `unshare -rn` gives an unprivileged user network
  namespace in which `ip link add ... type veth` and
  `tc qdisc ... netem delay 40ms rate 20mbit` both succeed. A
  shaped client leg therefore needs no root, and may be feasible
  in CI later.

## Mission

1. Set `TCP_NOTSENT_LOWAT` on the client-leg socket and cap
   `SO_RCVBUF` on the backend-leg socket. Both are set through
   `socket2::SockRef`, which is already a dependency.
2. Make both values proxy flags with sane defaults.
   - Suggested starting points: 128 KiB not-sent low-water
     mark, 256 KiB backend receive buffer. The measurement
     picks the defaults.
   - A value of 0 disables the option.
   - The Python side exposes them as optional config. It
     passes a flag only when the operator set it, so a newer
     daemon still launches an older binary (see the
     proxy-dev-releases plan's version-skew discussion).
3. Produce a repeatable shaped-link measurement procedure. Record
   before-and-after figures in `docs/`: p50 and p95
   keypress-to-draw latency and display-channel throughput. Take
   them at at least two link profiles (for example 20 ms/50
   Mbit and 80 ms/10 Mbit), with and without screen activity.
4. Measure per-socket congestion control (`TCP_CONGESTION`
   bbr) on the client leg. Adopt it only if it helps.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Implement the socket options, flags, Python config plumbing, tests and docs as described in Mission 1-2; see the brief notes below. |
| 1b | high | opus | none | Build the shaped-link rig and measure stock versus 1a at the link profiles in Mission 3-4; write the procedure and results into `docs/`. |

Brief notes for 1a:

- **Client leg.** Apply the options in `listen.rs` next to the
  existing `set_nodelay`. On the secure port, apply them before
  the TLS accept.
- **Backend leg.** Apply them in `backend.rs` once `connect_once`
  returns, by matching the `SpiceStream` variant and calling
  `get_ref()` (`.get_ref().0` for tokio-rustls streams).
  `SO_RCVBUF` set after connect cannot shrink the window scale
  already negotiated in the SYN, but it does bound the buffer.
  If the measurement shows that is not enough, record it: fixing
  it would need a ryll crate change to set the option before
  connect.
- **Failure handling.** Like keepalive, a set failure is logged
  and non-fatal.
- **Flags.** Add clap flags in `main.rs` and emit them from
  `kerbside/proxy_supervisor.py:build_proxy_argv` only when the
  new `kerbside/config.py` fields are set. Document the fields in
  `docs/configuration.md` and in `etc/kerbside.conf.example`,
  whose tests pin coverage of every `Config` field.
- **Builds.** Rust builds run through `make -C rust/kerbside-proxy`
  in Docker. Never use a host toolchain.
- **Checks before handing back.** `tox -e flake8`, `tox -e py3`,
  the crate's tests and clippy.

Brief notes for 1b:

- **Topology.** Everything runs inside `unshare -rn` (use
  `/usr/sbin/tc`). qemu with the SPICE module, the mock gRPC
  server and the proxy run on one side of a veth pair, and ryll
  runs in a nested namespace on the other. Only the client leg
  is shaped, with netem delay plus a rate limit on both
  directions of the veth.
- **Tools.** Reuse `tools/direct-qemu/` (the mock harness) and
  the latency orchestrator.
- **Screen activity.** The activity has to be enough to fill a
  shaped link. If the harness guest cannot produce it, say so
  and choose a guest that can. The qemu prototype in phase 2 is
  hunting for a similar workload, so check the scratchpad
  report if it exists.
- **Stock proxy.** Measure stock by setting both flags to 0.
- **Results.** Keep the rig scripts under `tools/`.

## Definition of done

- The options are applied, configurable, and covered by unit
  tests. This covers the argv builder and the conf example.
- A `docs/` page records the procedure and a results table with
  the link profiles, before and after, and the kernel's
  `tcp_wmem`/`tcp_rmem` values on the test host.
- The defaults are justified by the measurement. If the
  measurement shows no benefit, the phase says so and the
  options ship disabled or are dropped. A null result is a
  result.
- The master plan's Execution row is updated, with the merge
  commit in `Merged` once landed.
