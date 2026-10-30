# spice-server's ACK window, observed from the proxy

[Proxy backpressure on a shaped link](/components/kerbside/performance/proxy-backpressure/) explained
two things by spice-server's per-channel ACK window, from reading
spice-server's source rather than from watching it: the roughly 2 s
display backlog at 80 ms / 10 Mbit, and a throughput cap at
80 ms / 50 Mbit. This page records the first direct observation of that
window, made from inside Kerbside's relay on the same shaped-link rig.
Measured on 2026-10-28.

## Summary

- **The mechanism is confirmed.** The relay follows spice-server's
  count of unacknowledged messages on each display and cursor channel.
  On every run that reached the limit, the count peaked at exactly
  2W+1 (81 with a window of 40, 41 with a window of 20) and never went
  above it. Channels sat at that limit for up to 92% of the time.
- **The window bounds the backlog, as claimed.** At 80 ms / 10 Mbit
  with incompressible activity and streaming off, the messages
  spice-server had counted came to about 82, which is 2W+1. Those
  messages averaged 42 KiB. About 2.4 MiB of them had not yet reached
  the client, which at 9.3 Mbit/s is the 2.1 s keypress latency.
- **The throughput cap at 80 ms / 50 Mbit is refuted.** The busy
  activity delivers 590 of the 635 display messages a second the guest
  produces, and the channel is at the limit only 36% of the time. The
  bitrate is low (16 Mbit/s) because the messages are small, not
  because the window holds them back. spice-server sends JPEG draws of
  about 3.6 KiB here, against 21 KiB lossless draws at 20 ms.
- **On both 80 ms links spice-server picks the wide window (40) and
  JPEG.** spice-server judges a link slow when its start-up bandwidth
  test measures under 10 Mibit/s. That verdict sets both the window of
  40 and lossy JPEG for draws. At 20 ms it chose 20 and lossless QUIC.
  The test times a 250 KiB transfer on a new TCP connection. Our
  reading of why it fails at 80 ms is that slow start alone stretches
  that transfer over several round trips. Kerbside relays the test end
  to end, so the verdict follows the client's real RTT.
- **Throughput does not track 2W+1 messages per round trip.** Where
  the window does bind, the measured rate is 48 messages per sRTT at
  W=40 and 11 at W=20, between a quarter and three fifths of 2W+1.
  Two things lower
  it. The client acknowledges in batches of W, so credit comes back
  W messages at a time. And each batch has to be serialised on the
  link. A ceiling of 2W / (sRTT + W × size / rate) fits the
  window-bound runs to within about 30%.
- **What this means for transcoding.** The half of the argument that
  rests on the backlog stands. spice-server keeps up to 2W+1 messages
  in flight, whatever their size, and on a slow link that is seconds
  of stale updates. The half that rests on a throughput cap on long,
  fast links is weakened. At 80 ms / 50 Mbit the window is not what
  limits throughput, so removing it would gain little there. In relay
  mode, spice-server also already sends lossy JPEG draws to any client
  80 ms away, at any bandwidth. Transcoding's image-quality comparison
  has to be made against that, not against lossless draws.

## How the relay observes the window

spice-server (`server/red-channel-client.cpp`, the same in 0.15.2 and
current master) does this on display and cursor channels:

1. It sends `SET_ACK` with a generation and a window W. Sending it
   resets its count of unacknowledged messages to zero. The `SET_ACK`
   itself is then counted, so the count is 1.
2. Each message it sends adds one to the count.
3. The client answers `SET_ACK` with `ACK_SYNC`, echoing the
   generation. After that it sends an `ACK` every W messages.
4. Each `ACK` takes W off the count. It only does so if the client's
   last `ACK_SYNC` matches the current generation; any other `ACK` is
   ignored.
5. spice-server stops sending while the count is above 2W
   (`waiting_for_ack`). At most 2W+1 messages are unacknowledged.

W is 20, or 40 if the main channel's bandwidth test judged the link
slow (`dcc.h`, `common-graphics-channel.cpp`).

The relay sees every one of these messages. It keeps the same count
per channel, starting at the first `SET_ACK`
(`rust/kerbside-proxy/src/ackwindow.rs`). It reads the framed bytes it
has already buffered and forwards them unchanged. A unit test checks
that the observed relay's output is byte-identical to its input. The
counts are exported as Prometheus metrics, listed in
[configuration](/components/kerbside/configuration/#logging-and-monitoring-settings),
and each channel logs a one-line summary when its relay ends.

**Metrics rather than a debug log.** The existing proxy metrics are
process-wide counters with a few low-cardinality labels. The window
counters have the same shape: one `channel` label, plus a `window`
label limited to `20`, `40` and `other`. They answer an operator
question, "are my display channels waiting on the window?", in
production and across sessions, without turning on verbose logging. A
per-message debug log would run to hundreds of lines a second per
session, and enabling it would change the timing being measured. The
rig also already scrapes `/metrics`. The rare per-channel events (a
`SET_ACK`, an `ACK_SYNC`, the end-of-relay summary) are logged.

**The relay's count is a lower bound.** spice-server counts a message
when it hands the message to its socket. The relay counts it only once
it has read the whole message. Some messages spice-server has counted
are still in qemu's socket send buffer or in the proxy's backend
receive buffer. The relay cannot see those. When the client link is
congested, the proxy stops reading from the backend while it waits to
write to the client, so those buffers fill. The `ACK`s travel the other
way over the loopback, so the relay sees them at about the moment
spice-server does. The results table therefore gives the relay's count
on its own, and an estimate of spice-server's count. The estimate adds
the bytes in those two buffers, from `ss`, divided by the mean message
size.

## Procedure

The rig is `tools/shaped-link/`, run exactly as in
[the streaming re-baseline](/components/kerbside/performance/streaming-rebaseline/). Stock Debian qemu
10.0.13 (libspice-server 0.15.2) ran with `streaming-video=all` and
`off`. The guest was the keydraw guest with busy (3 noise bits) and
heavy (8 noise bits) activity, and the proxy socket options were off.
Busy activity is included alongside the heavy activity the measurement
set out to cover, because the throughput claim was made about busy
activity. For this measurement:

- `run-matrix.sh` now samples the proxy's
  `kerbside_proxy_ack_window_*` counters into `ackwin.txt` every 0.5 s,
  alongside `ss`.
- `summarise.py` prints a second table from those samples, covering the
  key-press window only and excluding warm-up.
- `make -C rust/kerbside-proxy release` builds the release proxy in
  Docker.
- `ryll-surface-drawn-rect.patch` no longer applies to ryll develop. It
  stopped applying when `control/server.rs` changed, after ryll
  0d140b3. ryll was therefore built at 0d140b3 with `RYLL_REF`.

```sh
tools/shaped-link/build-guest.sh /tmp/ack/guest
RYLL_REF=0d140b3 tools/shaped-link/build-ryll.sh /tmp/ack/sl
make -C rust/kerbside-proxy release
G=/tmp/ack/guest
DISPLAYS="sys-all:qemu-system-x86_64:all:$G sys-off:qemu-system-x86_64:off:$G" \
ACTIVITY_DELAY=8 WARMUP=10 CONFIGS=stock:0:0 \
PROFILES='80:50 80:10' ACTIVITIES='busy:30:3 heavy:30:8' REPEATS=2 SAMPLES=40 \
RYLL_BIN=/tmp/ack/sl/ryll/target/release/ryll WORKDIR=/tmp/ack/main \
    tools/shaped-link/run-matrix.sh
tools/shaped-link/summarise.py /tmp/ack/main
```

The 16 cases take about 25 minutes. A reference pass used the same
command with `PROFILES='0:0 20:50' REPEATS=1 SAMPLES=20`. The unshaped
link gives the rate at which the guest offers display messages. 20 ms
is a case where spice-server picks the narrow window. A last pass with
`RYLL_ARGS=-v` checked the image types spice-server sent.

The host and its sysctls are as on
[the backpressure page](/components/kerbside/performance/proxy-backpressure/#test-host), on kernel
6.12.113.

## Results

Display channel, during the key presses. "Ceiling" is
2W / (sRTT + W × size / rate). "In flight" is the median unacknowledged
count, first as the relay sees it, then with spice-server's unread
backlog added. Rows at 80 ms pool two repeats of 40 presses; the
reference rows are one repeat of 20.

| Link | Activity | Streaming | W | Display Mbit/s | Msgs/s | Mean KiB | sRTT ms | Msgs per sRTT (2W+1) | Ceiling msgs/s | At threshold | Stalls/s | In flight p50 | Keypress p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 80 ms / 50 Mbit | busy | all | 40 | 6.3 | 572 | 1.4 | 84 | 48 (81) | 862 | 20% | 8.8 | 62 / 77 | 100 |
| | | off | 40 | 16.0 | 590 | 3.6 | 88 | 52 (81) | 718 | 36% | 13.4 | 63 / 76 | 107 |
| | heavy | all | 40 | 17.3 | 268 | 8.6 | 88 | 24 (81) | 553 | 0% | 0.0 | 46 / 46 | 100 |
| | | off | 40 | 44.8 | 135 | 42.9 | 140 | 19 (81) | 190 | 17% | 2.0 | 64 / 72 | 462 |
| 80 ms / 10 Mbit | busy | all | 40 | 5.6 | 513 | 1.4 | 93 | 48 (81) | 575 | 19% | 9.4 | 62 / 74 | 101 |
| | | off | 40 | 8.9 | 337 | 3.4 | 142 | 48 (81) | 315 | 82% | 8.4 | 81 / 81 | 186 |
| | heavy | all | 40 | 8.1 | 167 | 6.2 | 114 | 19 (81) | 251 | 2% | 0.5 | 43 / 45 | 118 |
| | | off | 40 | 9.3 | 28 | 41.8 | 150 | 4 (81) | 53 | 0% | 0.0 | 36 / 82 | 2102 |
| 20 ms / 50 Mbit | busy | all | 20 | 7.7 | 629 | 1.5 | 24 | 15 (41) | 1391 | 0% | 0.2 | 27 / 43 | 38 |
| | | off | 20 | 41.2 | 266 | 21.4 | 40 | 11 (41) | 363 | 92% | 13.3 | 41 / 41 | 137 |
| | heavy | all | 20 | 32.4 | 466 | 9.7 | 29 | 14 (41) | 653 | 3% | 6.1 | 24 / 25 | 35 |
| | | off | 20 | 41.3 | 134 | 42.5 | 39 | 5 (41) | 224 | 2% | 3.9 | 28 / 42 | 277 |
| unshaped | busy, heavy | all, off | 20 | 9-192 | 634-635 | 1.5-42.5 | 0-1 | - | - | 0% | 0.0-0.1 | 11-13 | 13-19 |

Every display and cursor channel saw exactly one `SET_ACK`, and no
client `ACK` was ignored. The relay's highest in-flight count was 81
in 14 of the 16 runs at 80 ms. The other two, 80 ms / 10 Mbit heavy
with streaming off, peaked at 62, because the backlog sat behind the
proxy's read side. It was 41 in all four runs at 20 ms. Unshaped, the guest offers
635 messages a second (about 21 column draws per frame at 30 fps)
whatever the activity and streaming setting.

Image types from Ryll's `-v` log, busy activity with streaming off:
at 80 ms / 50 Mbit, 5980 JPEG, 561 ZLIB_GLZ and 15 other; at
20 ms / 50 Mbit, 2632 QUIC, 170 GLZ and 1 LZ.

## What the numbers say

Each case falls into one of four regimes:

- **The window binds.** At 20 ms / 50 Mbit busy with streaming off,
  the channel is at the threshold 92% of the time. It delivers 266 of
  the 635 offered messages, while the link is 82% used. At
  80 ms / 10 Mbit busy with streaming off it is at the threshold 82% of
  the time, at the ceiling's rate, with the link 89% used. In both, the
  window costs some throughput and the link is close to full anyway.
- **The link binds and the window bounds the backlog.** At
  80 ms / 10 Mbit heavy with streaming off, the link is 93% used. The
  relay sees the channel at the threshold 0% of the time, because the
  proxy reads from the backend only as fast as it can write to the
  client. Adding spice-server's unread backlog gives a median of 82
  messages in flight, which is 2W+1. This is the clearest case for the
  backlog claim: the backlog the user waits behind is the window's
  worth of 42 KiB draws, not a buffer Kerbside could shrink. At
  80 ms / 50 Mbit heavy with streaming off, the link is 90% used and
  the in-flight count sits a little under the limit.
- **Neither binds.** At 80 ms / 50 Mbit busy with streaming off, 590
  of 635 offered messages get through. The window is reached on bursts:
  each frame's 21 draws arrive together. The link is a third used. The
  earlier conclusion that the window caps this case does not hold. The
  delivered bitrate is the offered message rate times the size of a
  JPEG draw. What made it look capped was the comparison with 20 ms,
  where spice-server sends lossless draws six times larger.
- **Streaming.** With `streaming-video=all` the messages are small
  stream frames. The channel reaches the threshold at most a fifth of
  the time, and keypress latency is within about 20 ms of the idle
  floor for the RTT, as in [the re-baseline](/components/kerbside/performance/streaming-rebaseline/).

On 2W+1 per round trip: none of the window-bound runs gets near it. At
W=40 the busy runs deliver 48 to 52 messages per sRTT against 81. At
W=20 the window-bound run delivers 11 against 41. Most of the gap is
the batching described in the summary. The ceiling it predicts is 315
messages a second at 80 ms / 10 Mbit (337 measured) and 363 at
20 ms / 50 Mbit (266 measured). The model leaves out the client's
processing time and TCP's own pacing. This measurement did not isolate
which of those accounts for the second shortfall.

## Caveats

- The relay's in-flight count and its time at the threshold are lower
  bounds whenever spice-server's buffers or the proxy's backend receive
  buffer hold a backlog. The "with backlog" estimate pairs 0.5 s
  `ss` samples with metric samples and uses the mean message size, so
  it is approximate.
- One host, CUBIC, Ryll as the only client. Ryll counts the `SET_ACK`
  towards its first `ACK` and spice-gtk does not, so Ryll acknowledges
  one message earlier. That makes no difference at this scale.
- The reference rows are a single repeat of 20 presses each.
- That the bandwidth test misjudges 80 ms links because of slow start
  is inferred, not observed. spice-server logs its result only at debug
  level, and this rig does not capture it. The window and the JPEG
  draws are what was observed.
- `streaming-video=all` streams were MJPEG, which Ryll decodes, not the
  re-baseline's H.264: the Ryll used here asks for MJPEG first (see
  [the spice-server patches](/components/kerbside/performance/spice-server-patches/#which-codec-the-streams-used)).
