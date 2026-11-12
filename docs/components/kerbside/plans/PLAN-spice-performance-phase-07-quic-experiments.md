# Phase 7: QUIC client leg, experiments

Master plan: [PLAN-spice-performance.md](/components/kerbside/plans/PLAN-spice-performance/).
Planning effort: high. The phase turns on a transport design
decision, crosses two repositories, and the master plan's
section for it was written on 2026-09-27 without reading the
code.

The master plan's phase 7 section is the brief this plan
refines: a time-boxed experiment whose output is a go or no-go
and, if go, a phase plan for phase 8. That stands. What follows
grounds it in the code as of 2026-11-11 and changes two things
the survey found wrong: what the deciding measurement actually
decides, and what the rig can measure today.

## Scope

In:

- a packet-loss term for the shaped-link rig, which it does not
  have and the phase's headline measurement needs;
- a transport-only comparison of per-channel TCP against one
  QUIC connection with a stream per channel, under loss, as a
  gate before any SPICE integration;
- past that gate, an end-to-end prototype behind a default-off
  cargo feature in Kerbside and a matching feature in ryll:
  one QUIC connection per session, one bidirectional stream per
  SPICE channel, client leg only;
- measurements of input-to-echo latency under loss, connect
  time, behaviour across a path change, and the cost of falling
  back to TCP;
- a written verdict, a recommendation on UDP exposure, and the
  audit-trail consequences recorded;
- if go, a phase plan for phase 8.

Out:

- the backend leg, which stays stock SPICE over TCP. No
  hypervisor, qemu or spice-server change (master plan, "The
  shape, as agreed");
- the `.vv` QUIC endpoint key. The prototype's client takes the
  endpoint from a command-line flag; `kerbside/api.py`'s
  template is phase 8's work (decision 9);
- any deployment change — no new ansible variable, no compose
  port, no `docs/network-ports.md` row. The feature is off in
  every build the project ships, so nothing is exposed
  (decision 8);
- RFC 9221 datagrams. The master plan lists them as "room for
  later", and they are not needed to answer phase 7's
  questions;
- 0-RTT resumption. Measured connect time is the 1-RTT case;
  0-RTT is a phase 8 option with its own replay questions;
- fixing anything the survey found wrong outside this phase's
  files. See *Future work*.

## What the survey found

Checked against `develop` at 4ed7a31 on 2026-11-11. Two of the
master plan's phase 7 claims did not survive, and two more
needed grounding. The corrections are made at source in the
master plan's phase 7 section and the `docs/plans/index.md`
row as part of this phase's planning commit.

### 1. The head-of-line-blocking argument is against a strawman

The master plan argues (phase 7 section, "Streams without
head-of-line blocking") that "multiplexing SPICE's channels
over one TCP stream would make a keystroke wait behind a
retransmitted display frame, on exactly the lossy links where
the other benefits matter".

That is true and it is not the comparison. Today Kerbside's
client leg is **one TCP connection per channel**
(`rust/kerbside-proxy/src/listen.rs:179`, one accept per
channel; `session.rs:159`, one `handle_connection` per
accepted stream), so inputs and display already travel on
independent connections with independent loss recovery. Nobody
has proposed a TCP multiplex. QUIC's stream independence
therefore buys nothing *against today's code*; it only avoids a
regression that a single-stream design would have introduced.

What is left of the latency case is narrower: whether quinn's
userspace loss recovery and congestion control beat the
kernel's TCP for this traffic shape, and whether sharing one
congestion controller across a session's channels is better or
worse than N independent ones. That is a real question — one
controller sees the whole session's bandwidth and cannot
collectively overshoot, N controllers each probe
independently — but it is not the slam dunk the section
implies, and it could go either way.

This changes what the phase decides. See decision 1.

### 2. The rig cannot inject packet loss

The master plan names the deciding measurement as latency
"on the shaped-link rig's profiles plus 1-2% loss".

`tools/shaped-link/run-matrix.sh` has no loss term.
`PROFILES` parses as `rtt_ms:rate_mbit` (line 138, and the
split at lines 618-620), and the qdisc it builds is
`netem delay <rtt/2>ms rate <N>mbit limit <pkts>`
(line 322). Nothing anywhere in the rig passes `loss`.

So the phase's headline measurement needs a rig change before
it can be taken at all. That is step 1, and it is the one piece
of work the master plan's section does not anticipate.

Two attendant details, both from the rig's own comments:

- the rig forces `gso_max_size 1500 gso_max_segs 1` and
  `ethtool -K tso off gso off gro off` on each veth end (lines
  267-268) because "netem's queue limit counts packets, so it
  must see MTU-sized packets" (line 259). quinn's UDP path uses
  GSO. Whether `gso_max_segs 1` segments UDP GSO writes before
  the qdisc the way it does TCP has to be *verified by packet
  count*, not assumed;
- `limit` is computed in packets from the bandwidth-delay
  product (line 321). Loss interacts with that queue, so the
  loss runs are a new baseline, not a comparison against
  anything already recorded.

### 3. The proxy has exactly one seam, and a QUIC stream fits it

This is the finding that makes an end-to-end prototype cheap.

- `listen::run_secure` is generic over its handler:
  `F: Fn(SpiceStream, SocketAddr) -> Fut`
  (`rust/kerbside-proxy/src/listen.rs:169-178`). The accept
  loop is the only TCP-specific code in it.
- `session::handle_connection(state, SpiceStream, SocketAddr)`
  (`session.rs:159`) takes it from there: link message, caps
  check, `RegisterChannel`, keypair, ticket, authorize, then
  `backend::run`.
- Everything downstream — the L0/L1 firewall
  (`allowlist.rs`, `policy.rs`), the relay (`relay.rs`), the
  ACK-window metrics (`ackwindow.rs`), the capability reply
  tables (`caps.rs`) and the session-termination registry
  (`session.rs`) — is written against `SpiceStream`, not
  against a socket.

So a quinn accept loop that yields one `SpiceStream` per
accepted bidirectional stream plugs in with no change to any of
that, and the master plan's claim that "inside the proxy each
QUIC stream becomes an ordinary SPICE channel before the
firewall inspects it" is architecturally true as written.

`SpiceStream` is ryll's, not Kerbside's:
`shakenfist-spice-protocol/src/link.rs:40`, a three-variant
enum (`Plain(TcpStream)`, `Tls(ClientTlsStream<TcpStream>)`,
`TlsServer(ServerTlsStream<TcpStream>)`) with inherent
`read_exact`/`write_all`/`flush` plus `AsyncRead`/`AsyncWrite`
delegations. A fourth variant is the change, and it has to be
made in ryll — hence a ryll pull request before the Kerbside
one.

### 4. quinn fits the proxy's runtime and packaging

The master plan asks whether "a Rust QUIC stack (`quinn` is the
obvious candidate) fits the proxy's runtime and packaging".
Checked against the copy already in this host's cargo registry,
quinn 0.11.12:

- its default features are
  `["log", "platform-verifier", "runtime-tokio", "rustls-ring", "bloom"]`.
  `runtime-tokio` matches the proxy's tokio multi-thread
  runtime, and `rustls-ring` matches both the proxy's
  `rustls = { version = "0.23", features = ["ring"] }` and the
  ring provider `main` installs process-wide. The resolved
  rustls in the proxy's lock is 0.23.46, which is quinn
  0.11's requirement, so there would be one rustls in the tree
  and one crypto provider;
- `impl tokio::io::AsyncRead for RecvStream`
  (`quinn-0.11.12/src/recv_stream.rs:512`) and
  `impl tokio::io::AsyncWrite for SendStream`
  (`src/send_stream.rs:326`) are both real, so
  `tokio::io::join(recv, send)` is a single
  `AsyncRead + AsyncWrite + Unpin` value — exactly what a
  `SpiceStream` variant needs;
- nothing in that feature set builds C or assembly, so the
  maturin bin wheel is unaffected — unlike `transcode`, which
  is kept out of the wheel because it builds openh264 from
  source (`rust/kerbside-proxy/Cargo.toml`, the `[features]`
  comment);
- ryll's `Cargo.lock` already resolves quinn 0.11.12 (pulled in
  by `reqwest`), and `webrtc` already pulls `quinn-udp`, so the
  ryll side adds no new build dependency at all.

`platform-verifier` and `bloom` are not wanted — the first is a
client-side trust store we do not use, the second an
amplification defence for a public server. See decision 5.

### 5. There is no UDP anywhere in Kerbside today

`docs/network-ports.md` lists TCP 5900, TCP 5901, TCP 13002,
loopback TCP 13003 and the control-plane unix socket, and
nothing else. `ansible/roles/kerbside/defaults/main.yml:41-42`
exposes `kerbside_vdi_secure_port` and
`kerbside_vdi_insecure_port` only. `demo/docker-compose.yml`
publishes the same two.

So the UDP exposure question the master plan asks phase 7 to
"make or defer" is genuinely unmade, and no deployment artifact
has a UDP hole to widen. Phase 3's WebRTC output and phase 4's
transport input both ended no-go, so phase 7 is the first thing
to actually need the decision. See decision 8.

### 6. The audit trail records one row per channel, keyed on a per-channel UUID

`RegisterChannelRequest` carries `node`, `connection_ref`,
`client_ip`, `client_port`, `connection_id`, `channel_type`,
`channel_id` (`kerbside/rpc/kerbside.proto:187-195`).
`connection_ref` is a fresh UUID per channel
(`session.rs:160`), and `db.record_channel_info_by_ref`
(`kerbside/db.py:781-799`) upserts one `ProxyChannel` row per
`connection_ref`.

Under one QUIC connection per session, every channel of that
session reports the **same** `client_ip` and `client_port` — the
UDP four-tuple — where today each channel reports its own
ephemeral TCP source port. And after a QUIC path migration
those recorded values are stale, with no code path that would
ever update them.

Neither breaks anything: `connection_ref` keys the row, and
nothing joins on `client_port`. But it changes what the audit
trail means, and that is one of the questions the master plan
asks phase 7 to answer. See decision 7.

### 7. The rig already has the hooks the prototype needs

Better news than expected, and it removes a step. `run-matrix.sh`
takes `PROXY_BIN` (line 132), `RYLL_ARGS` (header line ~88) and
a `CONFIGS` entry whose optional fourth field is "extra proxy
flags separated by commas, each written --flag or --flag=value"
— the transcoder's own precedent is
`tx:0:0:--transcode-display,--transcode-ack-window=40`, with
the note that "the proxy must be built with FEATURES=transcode
for those".

A QUIC config is therefore `quic:0:0:--quic-port=5902` against a
`FEATURES=quic` build, with no rig change beyond step 1's loss
term. `rust/kerbside-proxy/Makefile:22-26` already threads
`FEATURES` into build, test and clippy.

### 8. Everything else checked out

The master plan's remaining phase 7 claims hold:

- channels of one session can land on different proxy nodes
  (`docs/proxy-architecture.md:805`, and the
  distributed-deployment note repeated in `session.rs`'s
  `SessionRegistry` doc comment), and session termination is a
  DB-mediated intent each node acts on;
- the client leg has no client-certificate auth
  (`rust/kerbside-proxy/src/tls.rs:43`,
  `.with_no_client_auth()`), so a quinn server config can reuse
  `tls.rs`'s PEM loaders;
- `docs/spice/console-vv-extensions.md` exists and is the right
  home for a QUIC endpoint key when phase 8 defines one. It
  currently defines one extension, `ticket-valid-until`, which
  Kerbside's own template does **not** emit — noted in *Future
  work*, not fixed here;
- finding 3's per-channel ticket re-check and late-channel
  ticket lifetime floor are backend-leg properties and are
  untouched by a client-leg transport change.

One thing phase 1 recorded that phase 7 should not forget:
`TCP_NOTSENT_LOWAT` on the client leg
(`--client-notsent-lowat`) is TCP-only and means nothing on a
QUIC leg. It defaults off, so nothing is lost; the write-up
should say so rather than leave a reader wondering.

### 9. The rig's ryll pin cannot carry new ryll work

Found on 2026-11-12 while checking step 6's premises, after the
plan was first written. It blocks step 6 as the plan was
drafted, and the fix is a new step.

`tools/shaped-link/build-ryll.sh` pins `RYLL_REF` to 0d140b3
and applies `ryll-surface-drawn-rect.patch`, which adds a
`rect` field to the control socket's `surface_drawn` event.
`keydraw-latency.py` needs that field to tell the guest's
key-box draw apart from the screen activity's draws — without
it the rig measures nothing. The script's own comment says the
pin is fixed because "the patch stopped applying to ryll
develop when control/server.rs moved after 0d140b3, so HEAD no
longer builds".

Confirmed against ryll `develop` at e381b1e:
`git apply --check ryll-surface-drawn-rect.patch` fails at
`shakenfist-spice-renderer/src/control/server.rs:480`, and
`surface_drawn` does not appear in ryll's `src/` at all.

So the rig cannot build a ryll that carries both the rect patch
and step 5's QUIC dialer, and step 6 has no client to measure
with. The fix is the one `build-ryll.sh` already names: land the
rect field in ryll's control socket protocol proper, drop the
patch, and move the pin. That is ryll#673's rect half, already
open and already listed in the master plan's *Future work*.
See steps 5a and 5b.

## Decisions

**1. Latency is a floor for this phase, not the case for it.**
The master plan says the loss-and-busy latency measurement "is
the measurement that decides the phase". Survey finding 1 says
that measurement is now a comparison between two designs that
both already avoid cross-channel head-of-line blocking, so a
tie is the most likely outcome and a tie is not a reason to
stop. The phase therefore decides on:

- **a latency floor**: QUIC must not be materially worse than
  per-channel TCP under loss, where *materially* means outside
  the run-to-run spread the rig itself measures. A clear loss
  is a no-go;
- **the operational benefits**, which are what the master plan's
  own case rests on and which only the prototype can price: one
  connection is one session behind a load balancer, one
  handshake instead of per-channel TCP+TLS+link+ticket, and a
  session that survives a path change;
- **the cost**: a UDP port to expose, a second listener to
  maintain, and a fourth `SpiceStream` variant in a crate ryll
  and Kerbside share.

This is the decision most likely to be argued with. The
counter-argument is real: a phase whose headline measurement is
expected to tie is a phase measuring the wrong thing, and the
honest response would be to abandon phases 7 and 8 and spend
the time on the four upstream items *Future work* says became
the next plan after phase 4's no-go. I am not taking that
option, for two reasons. First, the operational case stands on
its own and has nothing to do with latency: the
distributed-deployment constraint is a live piece of complexity
in the proxy's design today, and "one connection is one
session" removes it rather than working around it. Second, the
latency question is not actually settled — one congestion
controller per session against N per session is a genuine
unknown, and step 2 prices it for a fraction of a phase. But if
Michael would rather spend phases 7 and 8 on items 2, 6, 7 and
8 instead, this is the point to say so, and the back brief gates
on it.

**2. Step 2 is a hard gate, and it is cheap.** No SPICE
integration work starts until a transport-only bench has
compared N TCP connections against one QUIC connection with N
streams, over the shaped link with loss. The bench carries no
SPICE: one saturating stream plus one interactive
request/response stream, which is the traffic shape that
matters, with no guest, no qemu and no spice-server to add
variance. It resolves differences the full rig cannot — the
recorded p95 spread on the full rig reaches 71 ms at
150 ms / 2 Mbit (`docs/performance/spice-server-patches.md:220`),
which is wider than any plausible transport effect.

The gate: if QUIC's interactive p95 is worse than TCP's by more
than the bench's own run-to-run spread, on either loss level of
the 80 ms / 10 Mbit profile, the phase stops at step 2 and
writes up a no-go. That is a legitimate and cheap ending.

**3. Past the gate, the prototype is end to end, not synthetic.**
A replay harness cannot answer the remaining questions, because
spice-server reacts to the transport: its ACK window, its
channel "blocked" state and its stream rate control all key off
what the socket does (master plan, finding 1). Connect time and
path-change behaviour are likewise properties of the real
handshake sequence, not of a bench. Survey finding 3 says the
integration is small, which is what makes this affordable.

**4. The prototype plugs in at `handle_connection`, and
nothing else in the proxy changes.** A quinn endpoint, an
accept loop over connections and their bidirectional streams,
each stream joined into a new `SpiceStream::Quic` variant and
handed to the existing handler with the connection's current
peer address. The firewall, relay, caps, ACK-window metrics and
session registry are not touched. If a step finds itself
editing `relay.rs` or `policy.rs`, that is a signal the design
has gone wrong, and it stops for review.

Both sides are behind default-off cargo features — `quic` in
`rust/kerbside-proxy/Cargo.toml`, matching the `transcode`
precedent, and a `quic` feature in ryll's protocol crate. The
release wheel's build (`tools/build-proxy-wheel.sh`) is not to
mention either.

**5. quinn, with its default features off.**
`default-features = false, features = ["runtime-tokio", "rustls-ring", "log"]`.
`platform-verifier` is a client trust store this server does
not need and pulls a dependency tree with it; `bloom` is an
amplification defence worth having on a public endpoint but
noise in an experiment, and phase 8 can turn it on with a
reason. The server config is built TLS1.3-only with an ALPN of
our own choosing, from `tls.rs`'s existing PEM loaders —
`load_acceptor` itself is not reusable, because quinn needs a
`rustls::ServerConfig` rather than a `TlsAcceptor`, so `tls.rs`
grows a sibling constructor behind the feature.

**6. The rig's loss term is `rtt_ms:rate_mbit:loss_pct`, with
the third field optional.** An omitted loss field must produce
a qdisc line byte-identical to today's, so that every figure
already recorded in `docs/performance/` stays comparable and no
existing invocation changes behaviour. Loss applies in both
directions at the same percentage, which is what the rig's
one-netem-per-veth-end structure gives for free, and netem's
default uncorrelated per-packet model is what 1-2% means here.

**7. Channel identity stays the link message; the audit-trail
consequence is recorded, not fixed.** One QUIC stream carries
one SPICE channel, and that stream's own link message states
`connection_id`, channel type and channel id exactly as a TCP
connection's does. So `RegisterChannel` is unchanged and
`connection_ref` stays per-stream. Survey finding 6's two
consequences — every channel of a session reporting the same
client port, and those values going stale after a migration —
go in the write-up as findings, with the recommendation that
phase 8 record the QUIC connection id alongside them. Fixing
the audit model is phase 8's work if there is a phase 8, and
this plan does not pre-empt it.

**8. Phase 7 makes the UDP exposure recommendation, but lands
no deployment change.** The master plan asks phase 7 to make
the decision or defer it to phase 3 or 4, both of which ended
no-go, so there is nothing left to defer to. The write-up
therefore states the recommendation — which port, why a
separate one rather than 5900/udp, what it means for load
balancers and NAT, and what an operator who cannot open UDP
gets (today's behaviour, unchanged). It does not touch
`docs/network-ports.md`, ansible or compose, because the
feature is off in every shipped build and documenting an
exposure that does not exist would be false.

**9. The `.vv` file is not touched.** The prototype's ryll side
takes `--quic` and an endpoint on the command line, which is
all the rig needs (`RYLL_ARGS` exists for exactly this). Adding
a conditional key to `kerbside/api.py`'s flat
`VIRTVIEWER_TEMPLATE` (line 463) and defining it in
`docs/spice/console-vv-extensions.md` is phase 8's work. This
keeps Python, the REST API and four `.vv` endpoints out of a
spike entirely. The template does have the right shape for it
when the time comes — `%(tls_port)s`, `%(ca_cert)s` and
`%(host_subject)s` are each a prebuilt string or empty.

**10. The time box is the step table, with one stop rule.**
Seven steps, of which 3 to 7 run only past step 2's gate. If
step 6's matrix has not produced a complete set of numbers
after two full passes, step 7 writes up what there is and says
which cells are missing. A spike that overruns into a third
matrix pass is a spike that has stopped being a spike.

## Steps

ryll steps land as a ryll pull request, following ryll's
`AGENTS.md`, in a ryll worktree on a branch named
`quic-spice-stream`. Kerbside steps land in this phase's
worktree. Rust builds run through each repository's Makefile in
Docker, never with a host toolchain. The rig runs in an
unprivileged user namespace on this host. No step touches a
live system.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | opus | none | kerbside: teach the shaped-link rig a packet-loss term. Commit: "Teach the shaped-link rig to inject packet loss." See brief notes. |
| 2 | high | opus | none | kerbside: the transport-only bench and its numbers. **This is the gate**; it stops for review. Commit: "Measure QUIC against per-channel TCP under loss." See brief notes. |
| 3 | high | opus | ryll worktree | ryll: a `SpiceStream::Quic` variant and a QUIC dialer in `shakenfist-spice-protocol`, behind a `quic` feature, with tests. Opens the ryll pull request. Commit (ryll): "Carry a SPICE channel over a QUIC stream." See brief notes. |
| 4 | high | opus | none | kerbside: the proxy's QUIC listener behind a `quic` feature, feeding the existing `handle_connection`. Commit: "Accept SPICE channels over QUIC behind a feature." See brief notes. |
| 5 | high | opus | ryll worktree | ryll: the client side — one QUIC connection per session, a stream per channel, and the TCP fallback. Commit (ryll): "Dial Kerbside over QUIC, falling back to TCP." See brief notes. |
| 5a | high | opus | ryll worktree | ryll: land the `surface_drawn` `rect` field in the control socket protocol proper, so the rig stops needing a carried patch (ryll#673's rect half). Commit (ryll): "Report the drawn rectangle on surface_drawn." See brief notes. |
| 5b | low | sonnet | none | kerbside: drop `ryll-surface-drawn-rect.patch` and move `build-ryll.sh`'s `RYLL_REF` pin to a ryll `develop` commit carrying 5a. Commit: "Drop the rig's carried ryll patch." See brief notes. |
| 6 | high | opus | none | kerbside: run the matrix under loss, plus connect time, a path change and the fallback cost. Commit: "Measure a QUIC client leg on the shaped-link rig." See brief notes. |
| 7 | high | opus | none | kerbside: the write-up, the verdict, the UDP recommendation and the master plan updates. Commit: "Write up the QUIC client-leg experiment." See brief notes. |

Order:

- **Step 1 runs first.** Step 2 needs its loss term.
- **Step 2 gates steps 3 to 7.** Nothing past it starts without
  Michael's decision at the back brief gate.
- **Step 3 runs before step 4**, because step 4 needs the
  `SpiceStream` variant. The proxy pins the ryll crate by exact
  `rev` and the comment in `Cargo.toml` says to keep it exact —
  `dev-proxy-wheel.yml` excludes `Cargo.lock` from its publish
  trigger on the grounds that every git dependency is pinned by
  rev. So step 4 bumps all three ryll `rev` pins to the merged
  ryll commit in the same commit as the listener; it does not
  introduce a path override.
- **Steps 5 and 5a may run in parallel with step 4.**
  Different repositories, and step 5 needs only step 3's API.
  Step 5a is independent of all of them — it touches ryll's
  control socket, not its transport — so it can start as soon
  as the gate opens.
- **Step 5b needs 5a merged**, because it moves the rig's pin
  onto a ryll commit that must exist.
- **Step 6 needs steps 4, 5 and 5b**, and both ryll pull
  requests merged. Without 5b the rig has no client that both
  speaks QUIC and reports the drawn rectangle, and so measures
  nothing (survey finding 9).
- **Step 7 needs step 6.**

### Brief notes

**Step 1.** Read survey finding 2 first, then
`tools/shaped-link/run-matrix.sh` lines 36-45 (the `PROFILES`
documentation), 138 (the default), 255-330 (the namespace and
qdisc setup) and 610-630 (the profile loop).

- Extend the `PROFILES` grammar to `rtt_ms:rate_mbit[:loss_pct]`.
  `loss_pct` is a decimal percentage; absent means no loss.
  Update the header comment to match, in the same style as the
  surrounding entries.
- Add `loss <pct>%` to the netem qdisc built at line 322, and
  **only** when a loss term was given. Decision 6 requires that
  a profile without one produces a qdisc line byte-identical to
  today's: prove it by capturing the `shaped:` log line from a
  short run on this branch and from one on `develop`, and
  diffing them.
- Carry the loss value into the per-case directory name and
  into whatever `summarise.py` groups on, so results at
  different loss levels do not collide or silently merge. Read
  `summarise.py` before deciding how; follow what it already
  does with rtt and rate rather than inventing a parallel
  scheme.
- **Verify UDP packet accounting.** The rig sets
  `gso_max_size 1500 gso_max_segs 1` and `ethtool -K ... gso off`
  (lines 267-268) so netem's packet-counted `limit` sees
  MTU-sized packets. Confirm that holds for UDP as well as TCP:
  send a known volume of UDP through the shaped veth with
  segmentation offload on the sender and compare the netem
  qdisc's packet counters (`tc -s qdisc show`) against the
  expected MTU-sized count. Record the method and the numbers
  in the commit message. If UDP GSO is *not* segmented before
  the qdisc, say so plainly and recommend disabling quinn's
  segmentation offload in step 2 rather than papering over it in
  the rig.
- A short sanity run at `80:10:1` showing loss in the qdisc
  stats is enough; a full matrix is step 6's job.

**Step 2.** This is the gate. Read decisions 1 and 2, and
survey findings 1, 2 and 4.

- Write a small bench — two binaries or one with a mode, under
  `tools/quic-bench/` as a standalone cargo project, not a
  member of the proxy's crate — that runs inside the rig's
  namespaces over the shaped veth.
- Two arms, same workload:
  - **tcp**: N separate TCP connections. One saturates with
    bulk writes; one does small request/response exchanges and
    records each round trip.
  - **quic**: one quinn connection, N bidirectional streams,
    same split.
  Use the channel count a real session has (read
  `rust/kerbside-proxy/src/caps.rs` or
  `docs/channel-protocols.md` for the list; main, display,
  inputs, cursor and usually cursor plus playback is a fair
  five).
- Report p50, p95 and p99 of the interactive round trip, the
  saturating arm's throughput, and the run-to-run spread over
  at least five repeats. Profiles: `80:10` and `150:2` at 0%,
  1% and 2% loss. The 0% cells are the control.
- **Pin the congestion controller on both sides** for the
  headline comparison. quinn defaults to Cubic; the kernel's
  default here is whatever `/proc/sys/net/ipv4/tcp_congestion_control`
  says, so read it and match it rather than assuming. A
  BBR-versus-Cubic comparison would be a confounded one, and
  the rig's own `CC` variable notes that TCP BBR needs the
  `tcp_bbr` module loaded as root, which phase 1 could not do.
  If quinn's BBR is available, run it as a **separate**
  sensitivity arm and label it as such — quinn doing BBR in
  userspace where the kernel cannot is itself a finding worth
  reporting, but it is not the headline.
- If step 1 found UDP GSO reaching netem unsegmented, disable
  quinn's segmentation offload here and say so in the results.
- Write the numbers to `docs/performance/quic-transport-bench.md`
  with the method, the commands, the host state that matters
  (kernel version, congestion controller, whether `tcp_bbr` was
  loaded), and an explicit verdict against decision 2's
  threshold.
- **Stop for review.** Do not start step 3.

**Step 3.** Read survey findings 3 and 4, and
`shakenfist-spice-protocol/src/link.rs:35-130` in ryll.

- Add a `quic` feature to `shakenfist-spice-protocol` and, behind
  it, a `SpiceStream::Quic` variant wrapping
  `tokio::io::Join<quinn::RecvStream, quinn::SendStream>`.
  Extend every `match` in `read_exact`, `write_all`, `flush`,
  `AsyncRead::poll_read`, `AsyncWrite::poll_write`,
  `poll_write_vectored`, `poll_flush` and `poll_shutdown`.
  Check for other matches on `SpiceStream` across the crate
  before assuming that list is complete.
- `quinn` as
  `default-features = false, features = ["runtime-tokio", "rustls-ring", "log"]`,
  optional, enabled by the `quic` feature. ryll's lock already
  carries quinn 0.11.12; match it rather than bumping it.
- Add a client-role connector: open a QUIC connection to an
  endpoint, verifying the server against a supplied CA in the
  same way the existing TLS client path does (find it —
  `client.rs`'s `connect_channel_with_caps` and the host-subject
  verifier are the precedent), then open one bidirectional
  stream per channel and return a `SpiceStream::Quic` for each.
  Note that a quinn stream is not created on the wire until
  something is written, so the handshake drivers' first write
  must happen before the server sees the stream — confirm that
  against quinn's docs and say so in a comment, because a
  reader will otherwise expect an accept-on-open.
- Choose the ALPN string and define it in one place both
  repositories can cite. `kerbside-spice/1` or similar; say in
  the doc comment that it is ours and not registered.
- Tests: a loopback test that runs a link handshake over
  `SpiceStream::Quic` and one that proves the variant's
  `read_exact` behaves like `Plain`'s on a short read. Follow
  the crate's existing test conventions.
- `cargo build` with default features must not pull quinn.
  Prove it with `cargo tree -i quinn`.
- Open the ryll pull request with ryll's own push audit, per
  ryll's `AGENTS.md`.

**Step 4.** Read survey finding 3 and decisions 4 and 5.

- Bump all three `shakenfist-spice-*` `rev` pins in
  `rust/kerbside-proxy/Cargo.toml` to step 3's merged ryll
  commit, in this same commit, and keep them exact. Read the
  comment above the first pin before touching it.
- Add a `quic` feature to `rust/kerbside-proxy/Cargo.toml`
  mirroring `transcode`'s shape and comment style: off by
  default, with a comment saying what it is for and that
  `tools/build-proxy-wheel.sh` never enables it.
- Add a `listen::run_quic` beside `run_secure`, behind the
  feature, with the same handler signature
  (`Fn(SpiceStream, SocketAddr) -> Fut`). Bind a quinn endpoint,
  accept connections, and for each accept bidirectional streams
  in a loop, handing each one to the handler with the
  connection's current `remote_address()`. One task per stream,
  as the TCP path does per connection.
- Build the quinn server config in `tls.rs`, behind the feature,
  from the same `load_certs`/`load_key` helpers: a TLS1.3-only
  `rustls::ServerConfig` with `.with_no_client_auth()` and the
  step 3 ALPN, wrapped for quinn. Do not disturb
  `load_acceptor`.
- Add `--quic-port` to `main.rs`'s clap config behind the
  feature, spawn the listener alongside the two TCP ones, and
  extend the concurrency-cap semaphore and the connection
  metrics to cover QUIC streams the same way. Read
  `main.rs`'s existing listener wiring and follow it.
- Do not touch `relay.rs`, `policy.rs`, `allowlist.rs`,
  `caps.rs`, `ackwindow.rs` or `backend.rs`. If you believe one
  of them needs changing, stop and report why instead.
- `make lint` and `make test` must pass both with no features
  and with `FEATURES=quic`. Confirm `cargo tree -i quinn` is
  empty on a default build.
- Note in `docs/proxy-architecture.md` that the feature exists
  and is experimental, in the same way the transcoder is noted
  there. One short paragraph, not a design document.

**Step 5.** Read decisions 3 and 9.

- On the ryll side, dial one QUIC connection per session and
  open a stream per channel through step 3's connector, driven
  by a `--quic <host:port>` flag. Find where ryll establishes
  its channels today and follow that structure.
- The fallback is today's code path, unchanged: if the QUIC
  connection cannot establish, connect per channel over TCP
  exactly as now. Make the decision point explicit and
  instrumented — log the attempt, the outcome and the elapsed
  time at a level the rig can read, because step 6 measures the
  fallback cost from those timestamps.
- Decide and document the give-up timeout, and say why that
  number. A fallback that takes longer than a user's patience is
  a worse outcome than never trying QUIC.
- Log connect timings for both paths, again where step 6 can
  read them.
- No `.vv` parsing changes. The endpoint comes from the flag.

**Step 5a.** Read survey finding 9 and
`tools/shaped-link/ryll-surface-drawn-rect.patch`, which is the
specification: it is what the rig has been carrying and what
`keydraw-latency.py` parses.

- Add the `rect` field (`[left, top, right, bottom]`) to the
  `surface_drawn` control-socket event in
  `shakenfist-spice-renderer/src/control/server.rs`. The patch
  targets around line 480 of that file as it stood at 0d140b3;
  the code has moved, so find the event's emission site rather
  than trusting the line number.
- It is an additive field, so nothing that reads the event today
  breaks. Follow whatever the control socket's other events do
  for geometry — do not invent a different encoding from the
  patch's, because `keydraw-latency.py` parses the patch's.
- Document it wherever ryll documents that protocol, and add a
  test that the event carries the rectangle of the region that
  was drawn, not the surface's bounds.
- Reference ryll#673 for the rect half only. The issue carries
  other deferred items; do not sweep them in.
- Open the ryll pull request with ryll's own push audit.

**Step 5b.** One mechanical change, after 5a has merged.

- Delete `tools/shaped-link/ryll-surface-drawn-rect.patch`.
- In `tools/shaped-link/build-ryll.sh`, remove the patch
  application and rewrite the `RYLL_REF` comment: the pin exists
  so every run measures the same client, and no longer because
  the patch fails to apply. Set `RYLL_REF` to the ryll `develop`
  commit that carries 5a, by SHA.
- Check nothing else in `tools/shaped-link/` references the
  patch: `grep -rn surface-drawn-rect tools/`.
- Confirm the rig still builds a ryll: run `build-ryll.sh` into
  a scratch directory and check the binary appears.

**Step 6.** Read decision 10 and survey finding 7.

- Build the proxy with `FEATURES=quic` and ryll from step 5's
  branch. The rig needs no change: add a `CONFIGS` entry
  `quic:0:0:--quic-port=<port>` and pass ryll's `--quic` through
  `RYLL_ARGS`. If that turns out not to work, fix the rig
  minimally and say what was missing.
- The matrix: profiles `80:10` and `150:2` at 0%, 1% and 2%
  loss, activities `idle` and `busy`, configs `stock` (TCP) and
  `quic`, `REPEATS=2`. Set `ACTIVITY_DELAY` as the rig's header
  instructs if any display streams, or the spice-server connect
  crash will eat the runs.
- Connect time: from ryll's own timestamps, at least 10
  connects per arm, reported with spread.
- Path change: with the session live and the display busy,
  change the client namespace's veth address
  (`ip addr change`) and record whether the session survives,
  how long the stall is, and what the proxy logged. Do the same
  on the TCP arm for the contrast — it will not survive, and
  the write-up should say how it fails.
- Fallback cost: block UDP in the client namespace
  (an nft or iptables rule on the egress veth), connect, and
  time ryll's fall back to TCP from its logs.
- Record everything in `docs/performance/quic-client-leg.md`:
  method, commands, host state, the tables, and the spread.
  Follow `docs/performance/dbus-helper-measurements.md`'s shape.
- Do not write the verdict here. That is step 7.

**Step 7.** Read decisions 1, 7 and 8, and both measurement
pages.

- Write `docs/performance/quic-experiment.md`: the question, the
  method, what the numbers say, and an explicit **go or no-go**
  against decision 1's three criteria. State plainly where a
  result tied, and where the spread was too wide to tell.
- It must cover, because the master plan asks for each:
  latency under loss; connect time; path change; how ryll
  decides QUIC has failed and what the fallback costs; how
  channel identity maps to streams and what that does to the
  firewall (nothing — survey finding 3) and the audit trail
  (survey finding 6, with phase 8 recommended to record the
  QUIC connection id); the operational cost and the **UDP
  exposure recommendation** per decision 8; and whether quinn
  fits the runtime and packaging (survey finding 4, now
  measured rather than predicted).
- Say that `--client-notsent-lowat` is TCP-only and inert on a
  QUIC leg, so a reader does not go looking.
- Update the master plan: phase 7's `Status`, its `Merged` cell
  left for the next phase's close-out, the phase 7 section
  amended with what was learned, and phase 8 either given a
  one-paragraph shape or marked `Abandoned` with the reason, per
  the master plan's own phase 8 text. Update the
  `docs/plans/index.md` row to match.
- If go, write `PLAN-spice-performance-phase-08-quic.md` as a
  phase plan, not a sketch: the `.vv` key, the audit-trail
  change, the deployment work (ports doc, ansible, compose) and
  the default-on decision all have owners.
- No code changes in this step.

## Risks and mitigations

- **The congestion controllers confound the comparison.** quinn
  defaults to Cubic and runs its controller in userspace; the
  kernel's TCP uses whatever is configured, and phase 1 found
  this host has no `tcp_bbr` module loadable from an
  unprivileged namespace. A QUIC-BBR against TCP-Cubic number
  would be meaningless. *Mitigation*: step 2's brief pins both
  arms to the same algorithm for the headline and runs any BBR
  arm separately and labelled. The management session checks
  the recorded controller on both sides before accepting step
  2's numbers.
- **UDP GSO defeats netem's packet accounting**, silently
  making the QUIC arm look better or worse than it is.
  *Mitigation*: step 1 verifies packet counts for UDP
  explicitly and reports the method; step 2 disables quinn's
  segmentation offload if it does not hold. The management
  session rejects step 1 if the verification is asserted rather
  than measured.
- **The spread swallows the result.** The full rig's p95 spread
  reaches 71 ms. *Mitigation*: the deciding comparison is step
  2's bench, which carries no guest and no spice-server; the
  end-to-end runs in step 6 are for the operational questions
  and for the floor, and step 7 is required to say where the
  spread was too wide to tell.
- **The ryll pin couples the repositories.** The proxy pins the
  ryll crates by exact `rev`, and `dev-proxy-wheel.yml` relies
  on that to decide whether to publish. *Mitigation*: step 3's
  pull request merges before step 4 bumps the pins, in one
  commit, with no path override at any point. The management
  session checks `Cargo.toml` and `Cargo.lock` agree and that
  the three revs match.
- **A feature flag that is not really off.** A `quic` feature
  that leaks into the wheel would ship a UDP listener nobody
  asked for. *Mitigation*: the definition of done requires
  `cargo tree -i quinn` to be empty on a default build and
  `tools/build-proxy-wheel.sh` to be free of the string `quic`;
  both are checked by the management session, not just claimed.
- **Scope creep into phase 8.** The `.vv` key, the audit-trail
  change and the deployment work are all one commit away at
  every step. *Mitigation*: decisions 7, 8 and 9 say no in
  writing, and step 4's brief lists the files not to touch. A
  step that edits `kerbside/api.py` or `ansible/` is wrong by
  construction.
- **The phase answers "it ties" and nobody is better off.**
  *Mitigation*: that is an acceptable outcome, written down as
  such in decision 1, and the back brief gates on whether
  Michael wants it bought at all before step 3 spends anything.

## Definition of done

Step 2's gate came back a no-go, so steps 3 to 6 never ran and
the items below that describe their output cannot be met. They
are not failures; they are void, and they are kept rather than
deleted so a reader can see what the go path would have had to
show. Which items were checked, and which are void, is recorded
with the gate's result in *Execution notes*.

- [ ] `PROFILES="80:10:1"` produces a netem qdisc line
      containing `loss 1%`, shown by the rig's own `shaped:` log
      line.
- [ ] `PROFILES="80:10"` produces a `shaped:` log line
      byte-identical to the same invocation on `develop`, shown
      by a diff of the two.
- [ ] Step 1's commit message records the UDP packet-count
      verification: the command, the expected count and the
      observed count.
- [ ] `docs/performance/quic-transport-bench.md` gives, per
      cell, interactive p50/p95/p99 and the run-to-run spread
      over at least five repeats, for both arms, at 0%, 1% and
      2% loss on two profiles; names the congestion controller
      used on each side; and states the verdict against decision
      2's threshold in one sentence.
- [ ] If step 2's verdict is a no-go, the phase stops there and
      step 7 runs over step 2's numbers alone. The master plan
      and `index.md` say so.
- [ ] `cargo tree -i quinn` is empty for a default build of
      `rust/kerbside-proxy` and non-empty with
      `--features quic`.
- [ ] `grep -ri quic tools/build-proxy-wheel.sh
      .github/workflows/dev-proxy-wheel.yml` finds nothing.
- [ ] `make lint && make test` pass in
      `rust/kerbside-proxy` both with no features and with
      `FEATURES=quic`.
- [ ] The three `shakenfist-spice-*` `rev` pins in
      `rust/kerbside-proxy/Cargo.toml` are identical to each
      other and resolve in ryll's `develop`.
- [ ] `git diff` for step 4 touches none of `relay.rs`,
      `policy.rs`, `allowlist.rs`, `caps.rs`, `ackwindow.rs` or
      `backend.rs`.
- [ ] `git diff` for the whole phase touches nothing under
      `kerbside/`, `ansible/` or `demo/`, and does not change
      `docs/network-ports.md`.
- [ ] A ryll client connects through the proxy over QUIC and
      draws the guest's screen: a `quic` config case in step 6's
      results has non-zero draw counts in its `keydraw.json`.
- [ ] `docs/performance/quic-client-leg.md` reports, with
      spread: input-to-echo latency per cell for both arms,
      connect time over at least 10 connects per arm, the
      path-change outcome for both arms, and the fallback time
      with UDP blocked.
- [ ] `docs/performance/quic-experiment.md` states a go or
      no-go, and answers each of the seven questions the master
      plan's phase 7 section asks, in order, with the UDP
      exposure recommendation among them.
- [ ] No fact about the loss profiles is stated differently in
      `run-matrix.sh`'s header comment, the two measurement
      pages and the write-up.
- [ ] The master plan's phase 7 section no longer claims
      head-of-line blocking as a benefit over per-channel TCP,
      and no longer implies the rig can inject loss without
      change. (Done in the planning commit; re-check it still
      reads correctly after step 7's amendments.)
- [ ] `tools/shaped-link/ryll-surface-drawn-rect.patch` does not
      exist, `grep -rn surface-drawn-rect tools/` finds nothing,
      and `build-ryll.sh`'s `RYLL_REF` names a ryll `develop`
      commit that `git log` resolves in ryll.
- [ ] If go, `PLAN-spice-performance-phase-08-quic.md` exists
      with a step table; if no-go, phase 8's row reads
      `Abandoned` with the reason in the section.
- [ ] `pre-commit run --all-files` passes.

## Execution notes

- 2026-11-12 AEDT, back brief. Michael kept decision 1 (the
  phase stays, reframed around the operational benefits with
  latency as a floor) rather than abandoning phases 7 and 8 for
  the four upstream items. He asked for the BBR sensitivity arm
  and said he would load `tcp_bbr` himself, so step 2 runs both
  the cubic-against-cubic headline and a labelled BBR arm. He
  chose to fix survey finding 9 by adding steps 5a and 5b now
  rather than deferring the decision to the gate. He gave this
  session permission to push and open the pull request when the
  gate is reached, with steps 3 to 7 still unstarted.
- Phase 1 recorded that this host "has no `tcp_bbr` module".
  That is wrong and is corrected in the master plan: the module
  is present at
  `/lib/modules/6.12.115+deb13.1-amd64/kernel/net/ipv4/tcp_bbr.ko.xz`
  but not loaded, and `tcp_available_congestion_control` offers
  only reno and cubic until it is. The half of phase 1's note
  that holds is that an unprivileged namespace cannot load it;
  root on the host can.
- Survey finding 9 was found after the plan was first written,
  while checking step 6's premises during the back brief. Steps
  5a and 5b, their brief notes and their definition-of-done
  item were added in the same commit as these notes.
- Michael loaded `tcp_bbr` by hand on 2026-11-12;
  `tcp_available_congestion_control` now reads
  `reno cubic bbr`, with cubic still the default. The load is
  not persisted across a reboot, so a later re-run may find the
  BBR cells unreproducible unless it is loaded again.
- 2026-11-12, step 1. The UDP GSO question step 1's brief left
  open is answered, and the answer is the unwelcome one: a UDP
  sender that sets `UDP_SEGMENT` hands netem one super-packet
  per write, whatever `gso_max_size`/`gso_max_segs` say on the
  device. At `limit 110` the queue held 1 586 200 bytes with a
  ten-segment sender against 158 620 with a plain one, and the
  management session reproduced it independently at `limit 1`,
  where one ten-segment write occupied a single slot and all ten
  datagrams arrived. `tc -s qdisc show`'s packet counter cannot
  detect this, because it counts segments either way. **Step 2
  must therefore disable quinn's segmentation offload and say so
  in its results**, which resolves the conditional its brief
  carries. The rig documents the constraint at its veth setup
  rather than working around it, because a rig that silently
  re-segmented a client's writes would measure a transport
  nobody ships.

- 2026-11-12, step 2 and the gate. The transport bench came
  back a **no-go**. At 80 ms / 10 Mbit with 1% loss the
  interactive p95 is 144.3 ms over one QUIC connection with
  five streams against 82.8 ms over five TCP connections, with
  run-to-run spreads of 0.9 ms and 0.1 ms, so decision 2's
  threshold is crossed by roughly seventy times the wider
  spread and the phase stops at step 2. The management session did not take the
  measuring agent's word for it: the headline percentiles were
  recomputed from the raw per-sample arrays with an independent
  nearest-rank implementation -- taking each run's steady-state
  samples from its `interactive.raw` array, discarding those
  inside the warmup by their offset, and then taking the median
  across the five repeats, which is the bench's own definition
  -- and reproduced to 0.1 ms on every headline cell; the
  queue's bytes per slot were re-derived from the backlog
  samples per sample rather than pooled, giving a *median and
  maximum* of exactly 1442 in every QUIC cell, so no queue
  sample ever held more than one pinned datagram, against about
  9700 mean and 11522 maximum in the deliberately-wrong
  control. The bench page's own 1436.1-1441.9 figure is the
  pooled mean over occupied slots, which sits under 1442
  because the 64-byte echoes and bare acknowledgements share
  the same egress queue; both statistics are now named on that
  page, because one of them read as a short datagram would
  undercut the check it exists to make. The
  congestion controller in force was read from each run's own
  `ss -tim` dumps on both sides rather than taken from the
  sysctl write; and `make lint`, `make test`, `shellcheck` and
  `pre-commit run --all-files` were re-run rather than trusted.
  The qdisc line and queue-limit formula in
  `tools/quic-bench/run-bench.sh` were diffed against
  `tools/shaped-link/run-matrix.sh`'s and are byte-identical,
  which is what decision 6 asked for.
- The result indicts the design, not the protocol. The TCP arm
  is five connections and the QUIC arm is one, because that is
  the arrangement this phase proposed, so what the bench prices
  is collapsing a session's channels onto one congestion window
  — 60 to 75 ms of interactive p95 under loss. The one case
  where a single window might win, N sessions sharing a
  bottleneck, is not measured, and both measurement pages say
  so.
- 2026-11-12, steps 3 to 6 were never run, per decision 2. No
  ryll work, no proxy QUIC listener, no `quic` feature, and so
  no answer to the path change or the fallback cost. Steps 5a
  and 5b, added at the back brief to unblock step 6, are void
  with it: `tools/shaped-link/ryll-surface-drawn-rect.patch`
  and `build-ryll.sh`'s `RYLL_REF` pin are untouched and the
  rig still carries the patch. That blocker is real and still
  open for whatever next needs the rig's draw counts.
- 2026-11-12, step 7 ran over step 2's numbers alone, as
  decision 2 provides. Three things changed in review. The
  write-up's claim that the kernel's BBR is "much better than
  its Cubic on these links" was too broad and is now
  profile-qualified in both measurement pages: on the slow
  profile BBR more than doubles throughput in the lossy cells
  but its interactive p95 there sits above Cubic's, 217 ms
  against 164 ms at 1% loss, because Cubic has stopped trying
  to fill the link and queues nothing in front of the
  interactive channel. The agent's narrowing of phase 9 to
  "Push audit of phase 7 (kerbside)" in the master plan's
  Execution table, and its removal of the sentence sending
  ryll's share of the work to ryll's own audit, were both kept:
  nothing landed in ryll. The phase plan's Definition of done
  was left as written, with a note at its head recording that
  the go-path items are void rather than failed.

## Bugs fixed during this work

None yet.

## Future work

- **The kernel's BBR, on the proxy host.** Step 2's BBR
  sensitivity arm produced the most useful number of the phase,
  and it has nothing to do with QUIC. On these shaped links the
  kernel's BBR beats its Cubic badly: 9.27 Mbit against 1.64 at
  1% loss on the 80 ms / 10 Mbit profile, and an interactive p95
  of 104 ms against 171 ms at no loss, because it keeps the queue
  short instead of filling it. That is a
  `net.ipv4.tcp_congestion_control` change on the proxy host and
  it applies to today's per-channel TCP client leg. This phase
  will not act on it: the measurement is transport-only, nothing
  was measured end to end with BBR, and the latency trade is not
  uniform — on 150 ms / 2 Mbit BBR more than doubles throughput
  in the lossy cells but its p95 there sits above Cubic's. Two
  caveats any follow-up has to carry: `tcp_bbr` was loaded by
  hand on
  this host and the load is not persisted, so a recommendation
  has to say how the module gets loaded as well as which sysctl
  to set; and this is a kernel-TCP finding only, because quinn's
  own BBR was much worse than quinn's Cubic. Worth its own plan,
  and `tools/quic-bench/` is the bench to re-run it with.
- Kerbside's `.vv` template does not emit `ticket-valid-until`,
  although `docs/spice/console-vv-extensions.md` defines it and
  recommends it for Kerbside specifically. `kerbside/api.py:463`
  has the right shape for it (`%(tls_port)s` and friends are
  already conditional strings) and the API knows the lifetime.
  Out of scope here by decision 9, which keeps Python out of the
  spike. Worth an issue.
- Phase 2's kernel fix is still unsubmitted to dri-devel, and
  phase 5's spice-server series is still unsent. Neither blocks
  this phase, and both are Michael's to send.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of it and how the work you intend to do
aligns with it.

Two gates beyond that:

- **Before step 1**, on decision 1. The survey found the
  latency case for QUIC is much weaker than the master plan's
  phase 7 section implies, because today's client leg is already
  one TCP connection per channel and so already free of
  cross-channel head-of-line blocking. This plan keeps the phase
  and reframes it around the operational benefits, with latency
  as a floor. The alternative is to abandon phases 7 and 8 and
  spend the time on the four upstream items that *Future work*
  in the master plan says became the next plan after phase 4's
  no-go. That is Michael's call and it is cheap to make now and
  expensive to make at step 6.
- **After step 2**, on its numbers. Steps 3 to 7 are the
  expensive part of the phase and they build code in two
  repositories. They start only once the gate's verdict is in
  front of Michael.
