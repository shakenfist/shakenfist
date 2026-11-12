# QUIC against per-channel TCP on a shaped link

Kerbside's client leg is one TCP connection per SPICE channel. This page
asks whether carrying a session's channels as bidirectional streams on a
single QUIC connection would make the interactive round trip better or
worse, on the slow and lossy links where it would matter, and what each
arrangement gets through while it does.

The measurement carries no SPICE: no guest, no qemu and no
spice-server, because all three add far more variance than any
transport effect. It carries the traffic shape and nothing else — one
channel saturating with bulk writes, one exchanging small
request/response pairs whose round trips are recorded, and three open
but quiet. The tool is `tools/quic-bench/`. Measured on 2026-11-12.

## Summary

| Question | Answer |
|---|---|
| Interactive p95, 80 ms / 10 Mbit, 1% loss | TCP 82.8 ms, QUIC 144.3 ms. The run-to-run spread is under 1 ms in both arms, so the 62 ms gap is real. |
| Interactive p95, 80 ms / 10 Mbit, 2% loss | TCP 83.4 ms, QUIC 158.4 ms, but one TCP repeat in five came in at 383 ms, which widens that arm's spread to 300 ms and leaves the cell unresolved on its own. |
| Interactive p95 with no loss | QUIC 166.2 ms against TCP 171.0 ms at 80 ms / 10 Mbit, and 353.6 against 348.6 at 150 ms / 2 Mbit. A wash, resolvable but tiny. |
| Throughput | Equal within a few percent in every cell, both arms loss-limited the same way. |
| Why QUIC loses under loss | One connection is one congestion window. The saturating stream keeps it full, and the interactive stream's bytes wait for window to open rather than for a turn to be scheduled. |
| Does a stream send priority fix it? | No. Giving the interactive stream priority over the saturating one changed its p95 by 0.2 ms at 1% loss. Priority orders what is sent when the window allows; it does not create window. |
| Congestion control | Headline is Cubic against Cubic. The kernel's BBR carries six to ten times what its Cubic does under loss, for no latency cost on the fast profile and a worse one on the slow profile; quinn's BBR is much worse than quinn's Cubic. Both are reported separately. |
| Was segmentation offload really off? | Yes, verified per run from the queue's bytes per slot: 1436-1442 observed against 1442 expected, and 9669 in a deliberately-wrong control run. |
| Verdict | **No-go.** See below. |

## The verdict

The threshold this bench was built to test is whether QUIC's
interactive p95 is worse than TCP's by more than the bench's own
run-to-run spread, on either loss level of the 80 ms / 10 Mbit profile.

**It is, and the phase should stop here.** At 1% loss on that profile
QUIC's interactive p95 is 144.3 ms against TCP's 82.8 ms — 61.6 ms
worse, against a run-to-run spread of 0.9 ms in the QUIC arm and 0.1 ms
in the TCP arm. That is not a close call and it is not a noisy one: the
second, independent run made for the priority sensitivity arm
reproduced both figures to within 0.6 ms.

Two honest qualifications, neither of which rescues the result.

The 2% loss cell on the same profile does **not** cross the threshold
on its own arithmetic. QUIC is 75.0 ms worse there, but four of the
five TCP repeats recorded a p95 of about 83 ms and the fifth recorded
383 ms, so that arm's spread is 300 ms and swallows the difference. The
gate asks about either loss level, and the 1% level answers it.

The loss is a floor failure, not a ceiling failure. QUIC is not worse
everywhere: with no loss it is slightly *better* at 80 ms / 10 Mbit,
its throughput matches TCP's in every cell, and its connect time is
about five times shorter. The case for one connection per session was
never mainly about latency. But a transport that adds 60 to 75 ms to
the p95 of the channel a user feels, on exactly the links where a user
would notice, has to earn that back somewhere, and the operational
benefits are not worth 60 ms of input echo.

## Method

### Topology

`tools/quic-bench/run-bench.sh` re-executes itself under
`unshare -rn`, a user plus network namespace, and builds the same
shaped link `tools/shaped-link/run-matrix.sh` builds: a veth pair with
one netem qdisc on each end, half the profile's RTT as delay, the full
rate, a queue limit counted in packets from the bandwidth-delay
product, and the profile's loss percentage applied independently in
each direction. The bench's server runs in the outer namespace, the
client in a nested one, and only the veth pair between them is shaped.

It replicates that setup rather than calling it, because the full rig
brings a guest, a mock control plane and a SPICE client with it.

### The two arms

Both arms run byte-for-byte the same exchange; the workload code is
generic over `AsyncRead + AsyncWrite`, so the only difference between
them is the transport.

- **tcp**: five independent TCP connections, Nagle off on each, which
  is what the proxy's client leg does today.
- **quic**: one quinn connection with five bidirectional streams.

Five channels is a fair session: `main`, `display`, `inputs`, `cursor`
and `playback`. One channel saturates — the server writes 64 KiB
buffers towards the client as fast as the transport accepts them, which
is the direction a display channel loads. One exchanges 64-byte
requests that the server echoes verbatim, one outstanding at a time, a
20 ms gap after each response, and the round trip recorded; one
outstanding request is what makes a lost packet show up as a long round
trip rather than hiding behind a later one. The other three are opened,
exchange one byte so the server sees them, and then stay quiet, which
is what a real session's cursor, playback and record channels do most
of the time.

Each run is a 45 second window of which the first 5 seconds are
discarded, so the figures describe a congestion controller that has
finished ramping. Five repeats per cell per arm.

### What is pinned, and why

- **Congestion control on both sides.** The TCP arm takes the
  controller named by `tcp_congestion_control` in both namespaces,
  which the harness sets per cell; the QUIC arm is given the matching
  quinn controller factory. A Cubic-against-BBR comparison would be a
  confounded one. The controller actually in force is recorded per run
  from `ss -tim` on both sides, not assumed from the sysctl write.
- **quinn's segmentation offload, off.** See the next section.
- **Datagram size, 1400 bytes, with path MTU discovery disabled.** Two
  reasons: it makes the queue's expected bytes per slot a single
  number, which is how the offload check is read, and it stops MTU
  probing from adding its own loss events to a run that is measuring
  loss. TCP's MSS on the same link is 1448, so QUIC carries 3% less
  payload per packet; that is immaterial to a 64-byte round trip and
  worth a few percent on throughput.
- **Flow control well above the bandwidth-delay product** on the QUIC
  arm — 16 MB connection window, 8 MB stream window — so the
  comparison is about congestion control rather than receive buffers.
  The TCP arm uses the kernel's own autotuning, whose ceiling on this
  host is 6 MB.
- **TLS on the QUIC arm only.** QUIC's crypto is not optional; the TCP
  arm is plain. The asymmetry costs the QUIC arm some CPU, which at
  these rates is not the bottleneck, and it means the TCP arm's connect
  time understates what the proxy's TLS client leg really pays.
- **No stream priorities in the headline arm.** quinn leaves every
  stream at priority 0 and so does the headline, because a
  straightforward integration would not think to change it. A labelled
  sensitivity arm sets one; it is reported below.

### The commands

```
# Build the bench. Rust is built in Docker, reusing the proxy's image.
cd tools/quic-bench && make release

# The headline and BBR matrix: both profiles, three loss levels, both
# controllers, five repeats, plus the deliberately-wrong offload run.
WORKDIR=/path/to/results PROFILES="80:10 150:2" LOSSES="0 1 2" \
    CCS="cubic bbr" REPEATS=5 DURATION=45 WARMUP=5 \
    tools/quic-bench/run-bench.sh

# The stream-priority sensitivity arm.
WORKDIR=/path/to/priority PROFILES="80:10" LOSSES="0 1 2" CCS=cubic \
    REPEATS=5 DURATION=45 WARMUP=5 GSO_CONTROL=0 \
    QUIC_FLAGS=--priority-interactive tools/quic-bench/run-bench.sh

# The tables below.
tools/quic-bench/summarise.py /path/to/results

# The bench's own tests, and a check that they would notice a regression:
# each run breaks one tested property and requires the matching test to fail.
cd tools/quic-bench && make test && make mutants
```

Each cell's directory keeps the per-repeat results, both ends' server
and client logs, the `ss -tim` dumps, the qdisc as it was applied, and
the queue samples taken while the run was in flight.

### How the numbers are aggregated

**No interactive exchange timed out, in any cell.** Across all 151
measured runs the count is zero, which `summarise.py` reports as a
`timeouts` column beside each cell and again in a warning section that
lists every cell with a timeout or a repeat it could not measure. For
these results that section is empty.

That matters because a timed-out exchange ends its channel: a repeat
that suffered one would have fewer samples than its neighbours and a
p95 computed over them, which is a silent truncation in an argument
that turns on sub-millisecond spreads.

A cell's p50, p95 and p99 are the **median of each repeat's own
percentile**, not a percentile of the pooled samples: pooling would let
one bad repeat dominate and would hide the run-to-run variation that
decides this comparison. The **spread** is the difference between the
highest and lowest repeat's p95 within that cell and arm, and the
threshold applied to a cell is the wider of its two arms' spreads.

A difference smaller than that spread is a difference this bench cannot
resolve, and saying so is a result.

## Host state

| | |
|---|---|
| Kernel | 6.12.115+deb13.1-amd64 |
| `tcp_available_congestion_control` | `reno cubic bbr` |
| `tcp_congestion_control` default | `cubic` |
| TCP autotuning | `tcp_rmem` 4096 131072 6291456, `tcp_wmem` 4096 16384 4194304 |
| `tcp_notsent_lowat` | unset (4294967295) |
| iproute2 | 6.15.0 |
| quinn | 0.11.12, default features off, `runtime-tokio` + `rustls-ring` + `log` |

One thing a later reader needs to know: **`tcp_bbr` was loaded by hand
on this host and the load is not persisted across a reboot.** The
module ships with the kernel but is not loaded by default, and an
unprivileged network namespace cannot load it, so
`tcp_available_congestion_control` reads `reno cubic` on a fresh boot
and the BBR cells below will not reproduce until root runs
`modprobe tcp_bbr` again. The harness checks the list and refuses to
start rather than silently falling back to Cubic.

## Was segmentation offload really off?

It has to be, and the check has to be a measurement.

netem's queue limit counts packets, so netem must see MTU-sized
packets for the queue depth — and therefore the loss term — to mean
what it says. Capping the device's GSO size achieves that for TCP,
where the device segments after the qdisc. It does nothing for UDP: a
sender that sets `UDP_SEGMENT` hands netem one super-packet per write
whatever the device's limits say, so the queue holds many times the
bytes it appears to and a loss term drops whole writes instead of
single datagrams. That is not a link anybody deploys.

quinn decides how many datagrams to pack into a write by asking its
socket's `max_transmit_segments()`, and there is no configuration
setter for it. The bench therefore wraps the runtime's socket in a
newtype that delegates every other method and returns 1.

The check: sample the server-side egress qdisc while the run is in
flight and divide the queue's backlog in bytes by its length in
packets, over samples where at least two packets were queued.

- **Expected**, with the datagram pinned at 1400 bytes: 1400 of UDP
  payload plus 8 UDP, 20 IP and 14 Ethernet, so **1442 bytes per
  slot**.
- **Observed**, across all twelve measured QUIC cells: **1436.1 to
  1441.9** bytes per slot, median 1441. That figure is a *mean* —
  total queued bytes over total queued packets, across every sample
  where at least two packets were in the queue — so it sits a little
  under 1442 because the server's own small packets share the same
  egress queue: the 64-byte interactive echoes, and acknowledgement-only
  datagrams. The *per-sample maximum* is exactly 1442 in every measured
  QUIC cell, which is the sharper statement: no queue sample ever held
  a slot larger than one pinned datagram, so no write was ever
  segmented into one. A mean below 1442 is small packets, not a short
  datagram.
- **TCP arms**, for contrast: 1384 to 1504, the 1500-byte data
  segments pulled down by 54-byte pure acknowledgements and the
  64-byte interactive packets, more so in the cells with more
  retransmission.
- **Positive control**, one run made with the wrapper deliberately
  removed: **9669 bytes per slot**, 64 segments offered and 64 used.
  Its interactive p95 was 816 ms against the 166 ms the same cell gives
  with offload off — a queue holding about 6.7 times the bytes, which
  is exactly the failure mode.

So segmentation offload was off in every measured run, the check can
tell the difference, and `tc -s qdisc show`'s packet counter cannot:
it counts segments either way.

## Interactive round trip, Cubic against Cubic

The headline. Both arms on Cubic, the host's default. All figures in
milliseconds.

### 80 ms / 10 Mbit

| loss | arm | p50 | p95 | p99 | p95 spread | p95 range | samples/run |
|---|---|---|---|---|---|---|---|
| 0% | tcp | 154.1 | 171.0 | 172.2 | 1.2 | 169.8-171.0 | 232 |
| 0% | quic | 143.2 | **166.2** | 167.4 | 1.0 | 166.2-167.2 | 243 |
| 1% | tcp | 80.4 | 82.8 | 392.5 | 0.1 | 82.7-82.8 | 368 |
| 1% | quic | 101.1 | **144.3** | 228.2 | 0.9 | 143.8-144.7 | 309 |
| 2% | tcp | 80.4 | 83.4 | 405.4 | 300.5 | 82.7-383.2 | 347 |
| 2% | quic | 112.7 | **158.4** | 309.1 | 73.2 | 145.2-218.4 | 283 |

### 150 ms / 2 Mbit

| loss | arm | p50 | p95 | p99 | p95 spread | p95 range | samples/run |
|---|---|---|---|---|---|---|---|
| 0% | tcp | 312.2 | 348.6 | 349.3 | 0.2 | 348.5-348.8 | 123 |
| 0% | quic | 312.8 | **353.6** | 354.6 | 0.3 | 353.5-353.8 | 121 |
| 1% | tcp | 151.9 | 164.0 | 555.3 | 352.0 | 162.2-514.2 | 213 |
| 1% | quic | 183.8 | **255.4** | 526.7 | 54.3 | 248.7-303.0 | 182 |
| 2% | tcp | 151.5 | 163.9 | 541.3 | 12.0 | 161.0-173.1 | 219 |
| 2% | quic | 206.2 | **309.1** | 483.0 | 133.1 | 298.0-431.1 | 163 |

### Reading them

Per cell, QUIC against TCP, with the threshold as defined above:

| profile | loss | delta | threshold | resolves as |
|---|---|---|---|---|
| 80 ms / 10 Mbit | 0% | -4.8 | 1.2 | QUIC better |
| 80 ms / 10 Mbit | 1% | **+61.6** | **0.9** | **QUIC worse** |
| 80 ms / 10 Mbit | 2% | +75.0 | 300.5 | unresolved |
| 150 ms / 2 Mbit | 0% | +5.0 | 0.3 | QUIC worse |
| 150 ms / 2 Mbit | 1% | +91.3 | 352.0 | unresolved |
| 150 ms / 2 Mbit | 2% | +145.2 | 133.1 | QUIC worse |

Three things are worth separating out.

**The no-loss cells are a wash in substance.** At 80 ms / 10 Mbit QUIC
is 4.8 ms better and at 150 ms / 2 Mbit it is 5.0 ms worse; both are
larger than their cells' sub-millisecond spreads and so technically
resolvable, and both are about 1.5% of a round trip dominated by the
standing netem queue. Nothing follows from either.

**The two unresolved cells are unresolved because of one TCP repeat
each,** not because the arms are close. In both, four TCP repeats agree
to within a couple of milliseconds and the fifth is three to six times
higher — a retransmission timeout landing inside the measurement
window. The rule applied here takes the spread at face value, which is
the conservative thing to do, and those cells are therefore reported as
telling us nothing on their own.

**Where the median already differs, the tail is not the story.** At 1%
loss on the fast profile QUIC's *p50* is 101.1 ms against TCP's 80.4 —
TCP's interactive connection is sitting at the bare path RTT, and
QUIC's is 21 ms above it before any tail effect is counted.

## Throughput of the saturating channel

Median over five repeats, megabits per second, Cubic.

| profile | loss | tcp | quic |
|---|---|---|---|
| 80 ms / 10 Mbit | 0% | 9.56 | 9.48 |
| 80 ms / 10 Mbit | 1% | 1.64 | 1.56 |
| 80 ms / 10 Mbit | 2% | 0.94 | 1.11 |
| 150 ms / 2 Mbit | 0% | 1.91 | 1.89 |
| 150 ms / 2 Mbit | 1% | 0.87 | 0.84 |
| 150 ms / 2 Mbit | 2% | 0.54 | 0.54 |

Equal within a few percent everywhere, and the few percent runs both
ways. Both arms are loss-limited in the same way and neither fills the
10 Mbit link once there is any loss at all, which is why the
interactive channel sees the bare path RTT in the TCP arm's 1% and 2%
cells: there is no standing queue for it to wait behind.

## Why QUIC loses, and what does not fix it

One connection is one congestion window. The saturating stream always
has data ready, so the window is always full, and the interactive
stream's bytes wait for an acknowledgement to open window — up to a
round trip — before they can go out at all. In the TCP arm the
interactive channel is its own connection with its own window, which
for a 64-byte message is never exhausted, so its packet leaves
immediately.

That is the mechanism consistent with every figure above: the gap
appears exactly in the cells where loss keeps the bulk flow's window
small and busy, it is visible at the median and not just in the tail,
and it closes in the no-loss cells where both arms are dominated by the
same standing queue instead.

It is also the one thing a shared controller was always going to cost,
and it is the open question this bench existed to price. The answer is
60 to 75 ms of interactive p95 on the fast profile.

### Sensitivity arm: a stream send priority

The obvious lever is quinn's per-stream send priority: tell the
connection to serve the interactive stream before the saturating one.
A separate run of the 80 ms / 10 Mbit profile, Cubic, five repeats, set
the interactive stream's priority above the default on both ends.

| loss | quic p95, headline | quic p95, with priority | tcp p95 in the same run |
|---|---|---|---|
| 0% | 166.2 | 165.1 | 171.0 |
| 1% | 144.3 | 144.1 | 83.2 |
| 2% | 158.4 | 148.0 | 83.0 |

It does not help. At 1% loss the p95 moves by 0.2 ms; at 2% it moves
10.4 ms, which is well inside that cell's 73 ms spread. This is what
the mechanism predicts: priority orders what gets sent when the window
allows, and the interactive stream is not waiting for a turn, it is
waiting for window.

The run is also a useful replication. Its TCP arm, measured
independently a little over an hour later, reproduced the headline TCP
figures to within 0.6 ms in all three cells.

## Sensitivity arm: BBR

Clearly separate from the headline, and not a substitute for it. Both
arms were switched to BBR by name — the kernel's `tcp_bbr` for the TCP
arm, quinn's own `BbrConfig` for the QUIC arm. The two are different
implementations of the same idea, which is the point of running it as a
sensitivity arm rather than folding it in.

| profile | loss | arm | p50 | p95 | p95 spread | Mbit/s |
|---|---|---|---|---|---|---|
| 80 ms / 10 Mbit | 0% | tcp | 86.2 | 104.4 | 4.9 | 9.28 |
| 80 ms / 10 Mbit | 0% | quic | 167.4 | 335.8 | 1.1 | 9.47 |
| 80 ms / 10 Mbit | 1% | tcp | 85.6 | 104.4 | 2.6 | 9.27 |
| 80 ms / 10 Mbit | 1% | quic | 167.4 | 355.5 | 168.5 | 9.38 |
| 80 ms / 10 Mbit | 2% | tcp | 82.1 | 102.0 | 272.9 | 9.19 |
| 80 ms / 10 Mbit | 2% | quic | 167.4 | 503.0 | 167.1 | 9.43 |
| 150 ms / 2 Mbit | 0% | tcp | 190.5 | 227.0 | 12.3 | 1.83 |
| 150 ms / 2 Mbit | 0% | quic | 353.6 | 711.2 | 735.9 | 1.87 |
| 150 ms / 2 Mbit | 1% | tcp | 185.2 | 216.5 | 10.8 | 1.84 |
| 150 ms / 2 Mbit | 1% | quic | 353.6 | 1051.4 | 346.1 | 1.86 |
| 150 ms / 2 Mbit | 2% | tcp | 179.2 | 221.5 | 369.8 | 1.85 |
| 150 ms / 2 Mbit | 2% | quic | 353.5 | 706.4 | 702.3 | 1.88 |

Two findings, pointing in opposite directions.

**The kernel's BBR carries far more than its Cubic on both profiles,
and on the fast one it does so without costing latency — which is the
more interesting result on this page.** At 1% loss on the fast profile
it sustains 9.27 Mbit where Cubic manages 1.64, and at 2% loss 9.19
against 0.94 — six to ten times the throughput — while its interactive
p95 rises only from about 83 ms to about 104 ms. At no loss it cuts the
interactive p95 from 171 ms to 104 ms, because it keeps the queue short
instead of filling it.

The slow profile shows the same throughput effect and a worse latency
trade. BBR carries 1.84 Mbit against Cubic's 0.87 at 1% loss and 1.85
against 0.54 at 2%, and at no loss it cuts the interactive p95 from
349 ms to 227 ms — but in the lossy cells its p95 sits *above* Cubic's,
217 ms against 164 ms at 1% loss. Cubic's interactive channel is quick
there precisely because Cubic has stopped trying to fill the link, so
nothing is queued in front of it. Which of those an operator wants is
a real question and this bench does not settle it.

None of that needs QUIC. It needs `net.ipv4.tcp_congestion_control`
set to `bbr` on the proxy host, and it is worth investigating on its
own.

**quinn's BBR is much worse than quinn's Cubic**, and should not be
taken as what QUIC-with-BBR would do. It reaches the same throughput
the kernel does, so it fills the pipe, but its interactive p95 is 336
to 1051 ms with run-to-run spreads up to 736 ms — the widest numbers
anywhere on this page. It keeps a large window and a deep queue, which
is the worst possible combination with a shared congestion window, and
its spread makes several of its cells unresolvable anyway. A fair
reading is that quinn's BBR is immature rather than that BBR is wrong
for this traffic; the kernel's result in the same table says as much.

## By-products

Two numbers fell out of the runs. Neither was what this bench was built
to measure, and both deserve a better measurement before being relied
on.

**Connect time**, 30 connects per arm per profile, median and range:

| profile | tcp | quic |
|---|---|---|
| 80 ms / 10 Mbit | 401.4 ms (401.1-2426.5) | 84.3 ms (83.4-1245.6) |
| 150 ms / 2 Mbit | 755.9 ms (754.4-1778.6) | 164.0 ms (162.7-1473.0) |

QUIC is one round trip; the TCP arm is five sequential connects. The
ratio understates the real difference, because the TCP arm here is
plain while the proxy's client leg also does a TLS handshake, a link
message and a ticket check per channel. The wide upper ends are cells
where a loss landed in the handshake.

**`--client-notsent-lowat`** is a TCP socket option and would mean
nothing on a QUIC leg. It is off by default, so nothing is lost; it is
noted here so a reader does not go looking.

## What this bench does not measure

- **Anything SPICE reacts to.** spice-server's ACK window, its channel
  "blocked" state and its stream rate control all key off what the
  socket does, and none of that is here. A transport change that looked
  fine in this bench could still misbehave end to end.
- **Path migration**, which is one of the things a single connection
  would buy and which this bench has no way to exercise.
- **CPU and memory**, beyond noting that neither arm came close to
  saturating a core at 10 Mbit.
- **More than one session at a time.** Every cell is one session's
  worth of channels against an otherwise idle link, so nothing here
  says how N sessions sharing a bottleneck would behave — which is the
  case where one congestion window per session, rather than five, might
  actually win.
- **0-RTT resumption.** The connect figures are the 1-RTT case.
