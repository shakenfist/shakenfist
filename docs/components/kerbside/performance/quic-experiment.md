# A QUIC client leg: the experiment's verdict

Kerbside's client leg is one TCP connection per SPICE channel. The
experiment asked whether it should instead be one QUIC connection per
session, with a bidirectional stream per channel: one connection a load
balancer can keep on one proxy node, one handshake instead of TCP, TLS,
a link message and a ticket check per channel, and a session that
survives a client moving from Wi-Fi to LTE.

The answer is no, and it is a shorter answer than the question
deserved. The experiment put a latency floor in front of the
operational case — QUIC had to be no worse than today's arrangement
under packet loss before anything was built — and the floor was the
first thing measured. It failed, by 60 to 75 ms of interactive p95 on
the link profile that matters most, against a measurement whose own
run-to-run spread is under a millisecond. Nothing was built after
that, so the operational case was never priced.

This page gives the recommendation and argues it from one source,
without re-measuring anything:
[QUIC against per-channel TCP on a shaped link](/components/kerbside/performance/quic-transport-bench/),
a transport-only bench of five TCP connections against one QUIC
connection with five streams, over a shaped link with loss.

[Transcoding the display channel: the spike's verdict](/components/kerbside/performance/transcoding-spike/)
and [a SPICE helper on qemu's D-Bus display: the spike's
verdict](/components/kerbside/performance/dbus-display-spike/) are the previous two verdicts on the
same rig. Written on 2026-11-12.

## Verdict: no-go, on the latency floor alone

**Do not put the client leg on QUIC.** The verdict rests on three
criteria, set before the measurement, and go needed all three:

| Criterion | What go needed | Result |
|---|---|---|
| The latency floor | QUIC no worse than per-channel TCP on the interactive round trip under loss, where *worse* means outside the bench's own run-to-run spread | **Fails.** Interactive p95 of 144.3 ms against 82.8 ms at 80 ms / 10 Mbit with 1% loss, against spreads of 0.9 ms and 0.1 ms |
| The operational benefits | One connection is one session behind a load balancer; one handshake per session rather than four steps per channel; a session that survives a path change | **Never priced.** Only an end-to-end prototype could have measured these, and the floor failed before one was built |
| The cost | A UDP port to expose, a second listener in the proxy to maintain, and a fourth transport variant in a crate Kerbside and [ryll](https://github.com/shakenfist/ryll) share | **Never priced**, for the same reason. What it would have been, and the recommendation on exposing UDP, are set out below |

The honest shape of this verdict is that the experiment failed on the
floor it set itself. It is not that the operational benefits were
weighed against QUIC's latency and found too small: they were never
weighed at all. What the measurement establishes is the price of
admission, and that price is high enough to stop here — 60 to 75 ms
added to the p95 of the channel a user feels, on exactly the links
where a user notices, is not something one connection per session can
be expected to earn back.

The mechanism is the one thing a shared congestion controller was
always going to cost, and it is structural rather than a tuning
mistake. One QUIC connection is one congestion window. The saturating
stream — a display channel's shape — always has data ready, so the
window is always full, and the interactive stream's bytes wait for an
acknowledgement to open window, up to a round trip, before they can
leave at all. Five TCP connections give the interactive channel its
own window, which a 64-byte message never exhausts, so its packet
goes immediately. The bench sees that at the median and not only in
the tail: at 1% loss QUIC's
interactive p50 is 101.1 ms where TCP's is 80.4 ms, the bare path RTT.

The obvious lever does not work. Giving the interactive stream a send
priority above the saturating one moved its p95 by 0.2 ms at 1% loss.
Priority orders what is sent when the window allows; it does not
create window.

## Method, in brief

The bench page has the method, the commands, the host state and every
table. In outline: a standalone Rust bench runs both arms over a
shaped veth pair in an unprivileged network namespace, with one netem
qdisc at each end carrying half the profile's RTT as delay, the full
rate, a queue limit counted in packets from the bandwidth-delay
product, and the profile's packet loss. Loss is netem's default
uncorrelated per-packet model and each end drops independently, so
"1% loss" is a one-way rate applied in both directions rather than a
round-trip one.

Both arms run byte-for-byte the same exchange over five channels,
which is a fair session: one saturating with bulk writes towards the
client, one exchanging 64-byte request/response pairs whose round
trips are recorded, and three open but quiet. Congestion control is
pinned to the same algorithm on both sides for the headline, the
QUIC arm's segmentation offload is disabled and verified off from the
qdisc's bytes per queue slot, and each cell is five repeats of a 45
second window with the first five seconds discarded.

The bench carries no SPICE: no guest, no qemu and no spice-server,
because all three add far more variance than any transport effect.
That is what lets it resolve a 60 ms difference, and it is also why it
cannot say anything about how spice-server would react to the
transport.

## The questions it set out to answer

Each question below is answered as far as the work went, and labelled
with how far that is.

### Latency under loss — measured

**Measured, and it is the result that decides everything else.** At
80 ms / 10 Mbit with 1% loss the interactive p95 is 144.3 ms over one
QUIC connection against 82.8 ms over five TCP connections, with
run-to-run spreads of 0.9 ms and 0.1 ms. At 2% loss on the same
profile QUIC is 75.0 ms worse, but one TCP repeat in five recorded
383 ms where the other four agreed within a couple of milliseconds, so
that cell's spread is 300 ms and it resolves to nothing on its own. At
150 ms / 2 Mbit QUIC is 91 ms worse at 1% loss (unresolved, again on
one TCP repeat) and 145 ms worse at 2% (resolved). With no loss the
two are a wash: QUIC is 4.8 ms better on the fast profile and 5.0 ms
worse on the slow one, both about 1.5% of a round trip dominated by
the standing netem queue.

Throughput is equal within a few percent in every cell. Neither arm
fills a 10 Mbit link once there is any loss at all, which is why the
TCP arm's interactive channel sits at the bare path RTT in the lossy
cells: there is no standing queue for it to wait behind.

One caveat on scope. The bench measures a synthetic 64-byte round trip
on a quiet channel while another channel saturates. It does not
measure keypress-to-draw through a real SPICE session, which is what
the rig's other pages report, and the two numbers are not comparable.
For a floor that is the right instrument: it resolves differences the
full rig cannot, where a recorded p95 spread reaches 71 ms
([spice-server patches on a shaped link](/components/kerbside/performance/spice-server-patches/)).
But an end-to-end latency figure for a QUIC client leg does not exist.

### Connect time — a by-product only, and not a result

**Measured as a by-product, and the measurement understates the
difference it appears to show.** Over 30 connects per arm per profile
the QUIC arm's median is 84.3 ms against the TCP arm's 401.4 ms at
80 ms / 10 Mbit, and 164.0 ms against 755.9 ms at 150 ms / 2 Mbit:
one round trip against five sequential connects.

That ratio is not the one a deployment would see, and the bench page
says so itself. Its TCP arm is plain, with no TLS, no link message and
no ticket check, where the proxy's real client leg does all three per
channel. The real difference is therefore larger than these figures,
by an amount nobody has measured. The honest statement is that connect
time plainly favours one connection per session, that the experiment
produced no figure fit to quote for it, and that this was never in
doubt. Nor is 0-RTT resumption covered: these are the 1-RTT numbers.

### Behaviour across a path change — not established

**Not measured, and nothing here can measure it.** The benefit is the
one that most needs a demonstration — a client whose address changes
mid-session keeping its channels rather than tearing down and
reconnecting every one of them — and it is a property of a real
handshake sequence against a real server, not of a transport bench.
Measuring it needed a proxy that accepts QUIC and a client that dials
it, with the session live and the display busy, and neither was built.

What a measurement would have to report: whether the session survives
at all, how long the stall is, what the proxy logs, and the contrast
with the TCP arm, which will not survive and whose failure mode should
be written down rather than assumed.

### How a client decides QUIC has failed, and what the fallback costs — not established

**Not measured, and no fallback was written.** This question has no
answer because the thing that would answer it does not exist in either
repository.

What would have had to be built, so that a later attempt knows the
shape of it:

- a QUIC dialer in ryll's shared SPICE protocol crate
  (`shakenfist-spice-protocol`, whose `src/link.rs` holds the
  three-variant stream type a fourth variant would join), behind a
  default-off feature;
- a client that dials one connection per session and opens a stream
  per channel through it, with today's per-channel TCP path kept
  unchanged as the fallback;
- an explicit, instrumented decision point: a give-up timeout chosen
  with a reason, and the attempt, the outcome and the elapsed time
  logged where a harness can read them. A fallback slower than a
  user's patience is a worse outcome than never trying QUIC;
- a measurement of the fallback with UDP blocked in the client's
  namespace, timed from those logs.

None of that is hard, and none of it was funded, because the floor
failed first.

### Channel identity, the firewall and the audit trail — reasoned from the code

**Reasoned from the code, not measured.** No QUIC listener exists in
the proxy, so these are readings of the code as it stands rather than
observations of a running system. They are the part of the experiment
that survives it, because they are the design questions a later
attempt would start from.

**Channel identity does not change, and the firewall does not care.**
One QUIC stream would carry one SPICE channel, and that stream's own
link message states `connection_id`, the channel type and the channel
id exactly as a TCP connection's does. The proxy has one seam, and a
stream fits it: `listen.rs`'s secure listener is generic over its
handler, which takes a stream and a peer address, and
`session.rs`'s `handle_connection` does everything from the link
message onwards. Every component downstream of it — the two firewall
layers in `allowlist.rs` and `policy.rs`, the relay in `relay.rs`,
the ACK-window metrics in `ackwindow.rs`, the capability reply tables
in `caps.rs` and the session-termination registry in `session.rs` — is
written against that stream type rather than against a socket. A QUIC
receive and send stream joined into one read/write value is such a
type. So a QUIC-carried channel would reach the firewall as an
ordinary SPICE channel and be inspected identically, and the gRPC
registration in `kerbside/rpc/kerbside.proto` would not change.

**The audit trail changes meaning, in two ways, and neither breaks
anything.** The daemon records one row per channel, keyed on a fresh
UUID the proxy mints per channel (`session.rs`), which
`record_channel_info_by_ref` in `kerbside/db.py` upserts against.
Under one connection per session:

- every channel of a session would report the **same** client address
  and port — the single UDP four-tuple — where today each channel
  reports its own ephemeral TCP source port. An operator reading the
  rows can no longer tell the channels of a session apart by port;
- after a path change those recorded values are stale, and there is no
  code path that would ever update them. The row would name the
  address the client had when it connected, not the one it has.

Nothing joins on the client port, so neither is a bug. But an
implementation should record the QUIC connection id alongside the
address and port, and update the row on migration, or the audit trail
quietly becomes less useful than the one it replaced.

### The operational cost, and the UDP exposure question — a recommendation

**The recommendation is explicit: do not open a UDP port.** Not as
part of this work, and not speculatively ahead of a transport that
needs one.

There is no UDP anywhere in Kerbside today.
[network-ports.md](/components/kerbside/network-ports/) lists TCP 5900, TCP 5901, TCP
13002, loopback TCP 13003 and a unix socket, and nothing else;
`ansible/roles/kerbside/defaults/main.yml` exposes the two VDI TCP
ports, and `demo/docker-compose.yml` publishes the same two. Nothing
in this experiment produced anything
that would listen on UDP, and no deployment artifact was changed, so
there is no exposure to document and documenting one would be false.

It is the same decision a browser-facing or RDP-facing transport would
raise, and it is still unmade — the earlier transcoding and D-Bus
display spikes each ended no-go before needing it, and so does this
one. When something finally does need it, the shape of the decision is:

- **a separate UDP port, not 5900/udp or 5901/udp.** Reusing a TCP
  port number for a different protocol invites firewall rules that
  look right and are not, and the `.vv` contract already gives
  `port` and `tls-port` specific meanings that spice-gtk acts on;
- **load balancers need more than UDP forwarding.** "One connection
  is one session" only holds if the balancer routes on the QUIC
  connection id rather than the four-tuple; a four-tuple balancer
  sends a migrated client to a different node and loses the benefit
  the exposure was bought for;
- **NAT rebinding is the benefit, not an obstacle.** Surviving a
  rebind is the same mechanism as surviving a path change, which is
  what would have to be measured before it is claimed;
- **an operator who cannot open UDP keeps today's behaviour,
  unchanged.** Any QUIC leg has to be opt-in on both ends, with the
  per-channel TCP path as the default and the fallback.

The rest of the cost is small and was never the problem: a second
listener in the proxy to maintain, and a fourth variant in a stream
type that two repositories share, which means a coupled release every
time it changes.

### Whether a Rust QUIC stack fits the proxy's runtime and packaging — partly measured

**Partly measured, and the measured half came out well.** quinn
0.11.12 built and ran in the proxy's own `kerbside-proxy-dev` Docker
image, with `default-features = false` and
`runtime-tokio`, `rustls-ring` and `log` only.
`tools/quic-bench/Cargo.toml` records why each default feature is off:
`platform-verifier` is a client trust store a server does not need,
`bloom` is an amplification defence that would be noise in a bench,
and rustls' own defaults would pull aws-lc-rs — a second crypto
provider, built from C and assembly — alongside the ring provider the
proxy installs process-wide. With those off, nothing in the tree
builds C or assembly. `runtime-tokio` matches the proxy's tokio
multi-thread runtime and `rustls-ring` matches the proxy's own
`rustls` 0.23 with the `ring` feature, so there would be one rustls
and one provider.

The half that is **not** measured is the packaging itself. The proxy
was never built with quinn: there is no QUIC feature in
`rust/kerbside-proxy/Cargo.toml` and no listener behind one. So the
claim that the maturin binary wheel is unaffected — the thing that
keeps the transcoder's openh264 out of the wheel — is a reading of
quinn's feature graph, not an observation of a built wheel. It is very
likely right and it is not established.

## A TCP socket option that does not carry over

`--client-notsent-lowat` sets `TCP_NOTSENT_LOWAT` on the client leg's
sockets. It is a TCP socket option and means nothing on a QUIC leg,
which has its own stream and connection flow control instead. It
defaults off, so nothing would be lost; it is noted here so that a
reader comparing the two legs does not go looking for the equivalent.

## The finding that has nothing to do with QUIC

The most useful number the experiment produced is about the kernel's
TCP, and Kerbside can act on it without any transport change.

**On these shaped links the kernel's BBR beats its Cubic badly.** At
80 ms / 10 Mbit with 1% loss, BBR sustains 9.27 Mbit where Cubic
manages 1.64 — near the link's full rate against a sixth of it — while the
interactive p95 rises only from about 83 ms to about 104 ms. With no
loss on the same profile BBR *cuts* the interactive p95 from 171 ms to
104 ms, because it keeps the queue short instead of filling it.

The 150 ms / 2 Mbit profile shows the same throughput effect and a
worse latency trade: BBR carries 1.84 Mbit against Cubic's 0.87 at 1%
loss and 1.85 against 0.54 at 2%, and it cuts the no-loss interactive
p95 from 349 ms to 227 ms, but in the lossy cells its p95 sits above
Cubic's (217 against 164 ms at 1% loss). Cubic's interactive channel
is fast there precisely because Cubic has given up on filling the
link, so there is no queue in front of it. Which trade an operator
wants is exactly the question a proper investigation would settle.

That is a `net.ipv4.tcp_congestion_control` change on the proxy host,
it applies to today's per-channel TCP leg as it stands, and it
deserves its own investigation rather than a footnote on a QUIC page.
Two caveats go with it:

- **`tcp_bbr` was loaded by hand on the measuring host and the load is
  not persisted.** The module ships with the kernel but is not loaded
  by default, so `tcp_available_congestion_control` reads `reno cubic`
  on a fresh boot and these cells will not reproduce until root loads
  it again. A deployment recommendation has to say how the module gets
  loaded, not only which sysctl to set;
- **this is a kernel-TCP finding only.** quinn's own BBR was much
  *worse* than quinn's Cubic — interactive p95 of 336 to 1051 ms with
  run-to-run spreads up to 736 ms, the widest numbers on the bench
  page — so nothing here says BBR is right for QUIC. The fair reading
  is that quinn's implementation is immature, which the kernel's
  result in the same table supports.

Nothing was measured end to end with BBR, and nothing was changed on
any host.

## What would change the answer

The floor failed on a mechanism, not on a tuning choice, so the things
that would reopen it are specific:

- **A congestion arrangement that does not share one window across a
  session's channels.** QUIC gives each stream its own flow control
  but puts congestion control at the connection, which is what the
  protocol specifies rather than a quinn choice.
  One QUIC connection *per channel* would keep the faster handshake
  and path migration while giving each channel its own window, at the
  cost of the "one connection is one session" property that was the
  main operational argument. That is a different proposal and it has
  not been measured.
- **A display channel that does not saturate.** The bench's bulk
  stream writes as fast as the transport accepts. A transcoded or
  rate-limited display channel that stays below the link's capacity
  would leave window free for the interactive stream, and the gap
  should shrink or vanish. Both of the earlier spikes on this rig bear
  on that and both ended no-go, so it is not a plan, but it is the
  condition under which the floor would read differently.
- **Many sessions sharing a bottleneck.** Every cell is one session's
  channels against an otherwise idle link. One congestion controller
  per session rather than five is most plausibly a win where N
  sessions compete, because five controllers per session collectively
  overshoot where one cannot. That is the case the bench does not
  cover and the one most worth measuring if anyone revisits this.
- **A mature QUIC BBR.** quinn's was far worse than its Cubic here.
  A stack whose BBR behaved like the kernel's would be worth
  re-measuring, because the kernel's BBR keeps the queue short, which
  is the opposite of the condition that makes a shared window hurt.

A projection is not a result. Any of these needs the bench re-run, and
the bench exists and is cheap.

## What was not built, and what this page does not say

- **No code was written.** There is no QUIC listener in the proxy, no
  QUIC feature in its `Cargo.toml`, no stream variant or dialer in
  ryll, and nothing in any shipped build that speaks UDP. The release
  wheel is untouched.
- **No end-to-end measurement exists.** Keypress-to-draw latency,
  connect time through a real link handshake, the path change and the
  fallback cost were all to be measured with a prototype, and the
  prototype was not funded.
- **Nothing here argues against QUIC generally,** or against it for a
  transport whose traffic shape is not one saturating channel beside
  one latency-sensitive one. The result is about this traffic shape on
  these links.
- **The bench does not model what SPICE does.** spice-server's ACK
  window, its channel "blocked" state and its stream rate control all
  key off what the socket does, and none of that is present. A
  transport change that looked fine in this bench could still
  misbehave end to end — which cuts both ways, and is another reason
  the floor was placed before the prototype rather than after it.
