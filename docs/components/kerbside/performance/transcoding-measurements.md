# The transcoding display channel on a shaped link

This page measures the proxy's experimental
[transcoding display channel](/components/kerbside/proxy-architecture/#transcoding-the-display-channel-experimental)
against plain relaying on the shaped-link rig. It uses the same rig, guest
and keypress-to-draw metric as
[the streaming re-baseline](/components/kerbside/performance/streaming-rebaseline/) and
[the ACK-window observation](/components/kerbside/performance/ack-window/), adds two links slower than
10 Mbit, and also records the proxy's CPU and memory and the picture the
client ends up holding. Measured on 2026-10-28.

## Summary

- **On incompressible activity with streaming off, transcoding keeps the
  latency bounded where relaying does not.** With the heavy activity, the
  relay's p50 grows with the link's slowness: 2047 ms at 80 ms / 10 Mbit,
  4967 ms at 80 ms / 5 Mbit and 13570 ms at 150 ms / 2 Mbit. Transcoding
  paced on the socket gives 335 ms, 501 ms and 1459 ms. At 150 ms / 2 Mbit
  it also beats relaying with `streaming-video=all` (7328 ms).
- **Against relaying with `streaming-video=all`, transcoding loses
  everywhere else.** With busy activity on the two 50 Mbit links they are
  level. In every other cell from 20 ms / 50 Mbit to 80 ms / 5 Mbit,
  streaming relay is two to five times faster. For example, 118 ms against
  335 ms at 80 ms / 10 Mbit heavy, 101 ms against 244 ms there with busy
  activity, and 98 ms against 197 ms at 80 ms / 50 Mbit heavy.
- **On the smoother busy activity it is no better than relaying with
  streaming off on a slow link.** At 80 ms / 10 Mbit it is worse (244 ms
  against 192 ms). At 80 ms / 5 Mbit and 150 ms / 2 Mbit it is a little
  better (319 against 362 ms, 688 against 831 ms).
- **Pacing on a SET_ACK window of 40 is worse than pacing on the socket
  wherever the link is the limit,** by up to two times (531 against 244 ms
  at 80 ms / 10 Mbit busy, 382 against 197 ms at 80 ms / 50 Mbit heavy).
  The two are level with busy activity on the 50 Mbit links.
- **A transcoded session costs about a quarter of a core on a LAN and
  three to five times the memory of a relayed one.** It uses 0.27 to 0.29
  cores against 0.05 to 0.07 for the relay with streaming off. On 2 to 5 Mbit
  links, where fewer frames get through, that falls to 0.04 to 0.11. Peak
  RSS was 29 MiB idle and up to 51 MiB busy, against 11 MiB. The
  renderer's pixmap cache was never used: its peak was 0 bytes in every
  case.
- **Transcoding puts JPEG on text next to the activity, and then repairs
  it.** While the activity runs, text in the 64-pixel tiles the activity
  only partly covers is sent as JPEG: a fringe up to 48 pixels wide, 53248
  pixels at about 28 dB over the pixels that differ. Text further away is
  exact. After the activity
  stops, every transcoded repeat converged to the lossless reference
  exactly. Relaying with streaming on leaves a 16-pixel lossy strip
  during animation and is also exact afterwards. Relaying with streaming
  off keeps the text exact. But with busy activity on every link of
  80 ms or more, and on the 20 ms link when spice-server judged it slow,
  the activity's last frame keeps its JPEG (SSIM 0.9888, 43.9 dB).
- **The run was noisy.** Other work on this shared host pushed its load
  average past its 16 cores for a third of the run. Unshaped latency is
  skewed by that; the slow links are not. Separately, in 18 of 184
  repeats the client stopped reading its display socket, in relay and
  transcode alike. Those repeats measure ryll, not the proxy, and are
  excluded. See [Caveats](#caveats).

Whether a session can move between relaying and transcoding while it
runs, and what that costs, is on its own page:
[switching a display channel](/components/kerbside/performance/transcoding-switching/). What both pages
mean for productising the transcoder is in
[the spike's verdict](/components/kerbside/performance/transcoding-spike/).

## Configurations

| Name | Display channel | qemu `streaming-video` | Pacing |
|------|-----------------|------------------------|--------|
| relay, streaming off | relayed | off (qemu's default) | spice-server's ACK window |
| relay, streaming all | relayed | all | spice-server's ACK window |
| transcode, socket | transcoded (`--transcode-display`) | off | the client socket (`TCP_NOTSENT_LOWAT` 64 KiB) |
| transcode, ACK 40 | transcoded, `--transcode-ack-window=40` | off | a SET_ACK window of 40 messages |

The transcoder dials qemu with no video codec, so spice-server never streams
to it whatever `streaming-video` says. Transcoded cases therefore run only
with streaming off. Every transcoded case used the transcoder's defaults:
JPEG quality 85 and a lossless refresh after 500 ms without change.

The guest is the rig's keydraw guest, 1280x800, with a 96x96 key box and a
640x480 activity rectangle at (624, 160). Idle draws nothing but the key
box; busy animates a smooth gradient with 3 bits of noise at 30 fps; heavy
uses 8 bits of noise, which no codec compresses. **The guest's damage path
is `DRM_IOCTL_MODE_DIRTYFB`:** it flushes the rectangles it drew through
the legacy dirty-framebuffer ioctl, not through atomic commits with damage
clips. No atomic-damage guest was measured.

## Results

Latency is keypress-to-draw of the key box, in ms, pooled over the repeats
counted. Proxy cores is the proxy's CPU time per second over the key
presses. Peak RSS covers the whole case. The quality columns compare the
client's own lossless screenshot with the reference (see
[Procedure](#procedure)). "Background exact" counts repeats whose static
background, everything outside the activity and the key box, matched the
reference exactly mid-animation, with the most pixels that differed in any
repeat. The frozen-frame columns compare the whole frame (less the key box)
once the activity has stopped and the display has settled. 1.0000 and
`inf` mean identical.

| Link | Activity | Config | Repeats | p50 ms | p95 ms | Timeouts | Display Mbit/s | Proxy cores | Proxy peak RSS MiB | Background exact (most px differing) | Frozen SSIM min | Frozen PSNR min dB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| unshaped | idle | relay, streaming off | 4 | 20 | 31 | 0 | 0.0 | 0.001 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 4 | 23 | 720 | 0 | 0.0 | 0.003 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | transcode, socket | 4 | 22 | 2021 | 0 | 0.0 | 0.003 | 29 | 4 of 4 (0) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 4 | 22 | 922 | 0 | 0.0 | 0.003 | 29 | 4 of 4 (0) | 1.0000 | inf |
|  | busy | relay, streaming off | 4 | 28 | 227 | 0 | 87.7 | 0.046 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 4 | 30 | 147 | 0 | 7.4 | 0.011 | 11 | 0 of 4 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 4 | 27 | 85 | 0 | 16.6 | 0.286 | 50 | 0 of 4 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 4 | 29 | 150 | 0 | 16.7 | 0.269 | 51 | 0 of 4 (53248) | 1.0000 | inf |
|  | heavy | relay, streaming off | 4 | 100 | 529 | 0 | 113.3 | 0.066 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 4 | 22 | 45 | 0 | 42.4 | 0.025 | 11 | 0 of 4 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 4 | 35 | 139 | 0 | 61.7 | 0.277 | 46 | 0 of 4 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 4 | 30 | 89 | 0 | 64.0 | 0.284 | 48 | 0 of 4 (53248) | 1.0000 | inf |
| 20 ms / 50 Mbit | busy | relay, streaming off | 3 | 116 | 173 | 0 | 34.6 | 0.010 | 11 | 3 of 3 (0) | 0.9888 | 43.9 |
|  |  | relay, streaming all | 3 | 39 | 64 | 0 | 6.6 | 0.011 | 11 | 0 of 3 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 3 | 38 | 52 | 0 | 19.8 | 0.247 | 37 | 0 of 3 (28160) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 4 | 39 | 52 | 0 | 19.9 | 0.239 | 41 | 0 of 4 (28160) | 1.0000 | inf |
|  | heavy | relay, streaming off | 4 | 329 | 567 | 0 | 44.3 | 0.010 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 4 | 38 | 52 | 0 | 34.8 | 0.016 | 11 | 0 of 4 (15360) | 1.0000 | inf |
|  |  | transcode, socket | 3 | 92 | 119 | 0 | 42.7 | 0.191 | 40 | 0 of 3 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 3 | 172 | 208 | 0 | 43.9 | 0.181 | 42 | 0 of 3 (53248) | 1.0000 | inf |
| 80 ms / 50 Mbit | busy | relay, streaming off | 3 | 101 | 117 | 0 | 16.5 | 0.011 | 11 | 3 of 3 (0) | 0.9888 | 43.9 |
|  |  | relay, streaming all | 4 | 99 | 117 | 0 | 5.1 | 0.009 | 11 | 0 of 4 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 4 | 102 | 124 | 0 | 19.7 | 0.259 | 37 | 0 of 4 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 4 | 99 | 122 | 0 | 19.1 | 0.265 | 48 | 0 of 4 (28160) | 1.0000 | inf |
|  | heavy | relay, streaming off | 4 | 472 | 577 | 0 | 45.0 | 0.010 | 11 | 4 of 4 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 3 | 98 | 114 | 0 | 13.7 | 0.010 | 11 | 1 of 3 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 4 | 197 | 244 | 0 | 41.6 | 0.195 | 41 | 0 of 4 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 3 | 382 | 428 | 0 | 44.3 | 0.161 | 43 | 0 of 3 (53248) | 1.0000 | inf |
| 80 ms / 10 Mbit | idle | relay, streaming off | 2 | 99 | 113 | 0 | 0.1 | 0.001 | 11 | 2 of 2 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 2 | 102 | 121 | 0 | 0.1 | 0.001 | 11 | 2 of 2 (0) | 1.0000 | inf |
|  |  | transcode, socket | 2 | 105 | 124 | 0 | 0.1 | 0.001 | 29 | 2 of 2 (0) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 105 | 115 | 0 | 0.1 | 0.001 | 29 | 2 of 2 (0) | 1.0000 | inf |
|  | busy | relay, streaming off | 2 | 192 | 232 | 0 | 8.5 | 0.005 | 11 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | relay, streaming all | 2 | 101 | 126 | 0 | 5.4 | 0.011 | 11 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 2 | 244 | 290 | 0 | 8.5 | 0.206 | 44 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 531 | 631 | 0 | 9.0 | 0.159 | 44 | 0 of 2 (28160) | 1.0000 | inf |
|  | heavy | relay, streaming off | 2 | 2047 | 2428 | 0 | 9.2 | 0.002 | 11 | 2 of 2 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 2 | 118 | 287 | 0 | 8.0 | 0.007 | 11 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 2 | 335 | 439 | 0 | 8.9 | 0.064 | 36 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 671 | 783 | 0 | 9.0 | 0.067 | 34 | 0 of 2 (53248) | 1.0000 | inf |
| 80 ms / 5 Mbit | busy | relay, streaming off | 2 | 362 | 448 | 0 | 4.4 | 0.003 | 11 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | relay, streaming all | 2 | 112 | 150 | 0 | 3.5 | 0.010 | 11 | 1 of 2 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 2 | 319 | 375 | 0 | 4.6 | 0.108 | 38 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 609 | 720 | 0 | 4.6 | 0.113 | 44 | 0 of 2 (53248) | 1.0000 | inf |
|  | heavy | relay, streaming off | 2 | 4967 | 5306 | 0 | 4.7 | 0.002 | 11 | 2 of 2 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 1 | 109 | 606 | 0 | 3.6 | 0.006 | 11 | 1 of 1 (0) | 1.0000 | inf |
|  |  | transcode, socket | 2 | 501 | 2975 | 0 | 3.8 | 0.049 | 41 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 871 | 1154 | 0 | 4.7 | 0.057 | 33 | 0 of 2 (53248) | 1.0000 | inf |
| 150 ms / 2 Mbit | busy | relay, streaming off | 2 | 831 | 1142 | 0 | 1.8 | 0.002 | 11 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | relay, streaming all | 3 | 182 | 254 | 0 | 1.5 | 0.006 | 11 | 0 of 3 (7680) | 1.0000 | inf |
|  |  | transcode, socket | 3 | 688 | 826 | 0 | 1.8 | 0.079 | 37 | 0 of 3 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 3 | 1227 | 2179 | 0 | 1.7 | 0.070 | 40 | 0 of 3 (53248) | 1.0000 | inf |
|  | heavy | relay, streaming off | 3 | 13570 | 13939 | 1 | 1.9 | 0.001 | 11 | 3 of 3 (0) | 1.0000 | inf |
|  |  | relay, streaming all | 2 | 7328 | 7834 | 0 | 1.9 | 0.001 | 11 | 2 of 2 (0) | 1.0000 | inf |
|  |  | transcode, socket | 3 | 1459 | 1797 | 0 | 1.9 | 0.045 | 37 | 0 of 3 (53248) | 1.0000 | inf |
|  |  | transcode, ACK 40 | 2 | 1557 | 1939 | 0 | 1.9 | 0.040 | 34 | 0 of 2 (53248) | 1.0000 | inf |

Repeats are out of up to four on the links measured twice (see
[Procedure](#procedure)) and two on 80 ms / 10 Mbit and 80 ms / 5 Mbit.
The one timeout, at 150 ms / 2 Mbit heavy with streaming off, is a press
whose draw took over 15 s; its p50 is a lower bound.

### Unshaped latency

On the unshaped link the latency is all host time, so it moves with the
host's load: the p95s above, and the 2 s p95 of idle cases that draw almost
nothing, come from repeats measured while the load average was 25 to 60.
The least-loaded repeat of each cell gives a better idea of the LAN cost:

| Activity | Config | Load average | p50 ms | p95 ms |
|---|---|---|---|---|
| idle | relay, streaming off | 0.9 | 22 | 30 |
| | relay, streaming all | 1.5 | 15 | 31 |
| | transcode, socket | 0.9 | 13 | 31 |
| | transcode, ACK 40 | 1.2 | 21 | 30 |
| busy | relay, streaming off | 1.8 | 21 | 32 |
| | relay, streaming all | 3.4 | 16 | 31 |
| | transcode, socket | 2.2 | 15 | 31 |
| | transcode, ACK 40 | 2.7 | 18 | 29 |
| heavy | relay, streaming off | 15.7 | 79 | 119 |
| | relay, streaming all | 12.7 | 16 | 31 |
| | transcode, socket | 20.5 | 30 | 61 |
| | transcode, ACK 40 | 15.6 | 30 | 56 |

The two reference runs, on a nearly idle host, measured relay with
streaming off at 19 / 31 ms (busy) and 20 / 32 ms (heavy). So on a LAN,
transcoding adds nothing measurable to latency while the host is quiet.

### CPU

The proxy's CPU follows how many frames the transcoder sends. On a LAN it
sends every frame the guest draws, about 0.28 cores per session for this
640x480 activity at 30 fps. On slow links the socket holds it back and
damage merges in the mirror, so it falls: 0.21 cores at 80 ms / 10 Mbit
busy, 0.06 heavy, and 0.04 to 0.08 at 150 ms / 2 Mbit. A relayed display
channel costs 0.001 to 0.07 cores.

qemu's own CPU was lower with transcoding than with the relay with
streaming off: 0.13 cores against 0.68 for heavy activity on a LAN, and
0.13 against 0.31 at 80 ms / 50 Mbit. The transcoder's backend leg asks
spice-server for LZ4 rather than leaving it to choose QUIC or JPEG, and
reads at LAN speed, which is the likely reason. With streaming on, qemu
used 0.08 to 0.16 cores.

### Memory and the pixmap cache

A transcoded session's proxy peaked at 29 MiB idle and 33 to 51 MiB with
activity, against 11 MiB for a relayed one. That is the mirror's RGBA
surface and the BGRX shadow (4 MiB each at 1280x800), the encoders' working
memory, and the renderer's state.

The renderer's pixmap cache, capped at 256 MiB, **peaked at 0 bytes and 0
entries in every transcoded case.** qemu's display path never asks the
client to cache an image, so the cache costs nothing here, and its cap
could be cut a long way without effect on qemu guests. The same goes for
the GLZ dictionary: the backend leg is never offered GLZ.

### Image quality

The reference is the frozen frame of an unshaped, relayed case with
streaming off, where spice-server sends only lossless draws. It was taken
twice per activity, and the two captures were byte-identical for each of
idle, busy and heavy (`cmp`, and equal SHA-256).

**During animation.** The relay with streaming off sends only what
changed, so the background stays exact. Its draws of the activity itself
are JPEG on the 80 ms links, which this comparison leaves out. With
streaming on, a column 16 pixels wide next to the activity (7680 pixels,
twice that in one repeat) goes lossy, at 17 to 28 dB over those pixels;
spice-server's stream covers a little more than the activity. The
transcoder works in 64-pixel tiles. The activity's edges at x 624 and y
160 do not fall on tile boundaries, so the tiles along its left and top
edges are mostly text. When a tile holds noise it fails the LZ4 test and
goes as JPEG, text and all: 48 pixels to the left of the activity and 32
above, 53248 pixels at about 28 dB. Where some of those tiles went as LZ4
the count is 28160. The transcoder does this on every link, the unshaped
one included, because it chooses JPEG by how well a tile compresses, not
by the link.

The crop below is 64 pixels to the left of and 48 above the activity's
top-left corner, enlarged two times, from 80 ms / 5 Mbit busy, while the
activity ran. The last panel is the transcoded frame's difference from the
reference, amplified eight times, with the activity itself masked. At this
quality (85) the text in the fringe is still readable, but it is visibly
not the reference.

![Mid-animation crops at 80 ms / 5 Mbit](transcoding/legibility-80ms-5mbit.png)

**After the activity stops.** Every transcoded repeat converged to the
reference exactly once the activity had been still for longer than the
500 ms lossless delay. The relay with streaming on also ended exact,
because spice-server redraws losslessly once a stream ends. The relay with
streaming off keeps whatever it last sent. With busy activity on every
link of 80 ms or more, and at 20 ms when spice-server's bandwidth test
called the link slow, that was JPEG: SSIM 0.9888 and 43.9 dB. The heavy activity's last frame was
exact in every configuration, presumably because JPEG does not pay on
noise either and spice-server sent it losslessly.

How long the display took to settle after the freeze, median over busy and
heavy: about 3 s on every link up to 80 ms / 10 Mbit, which is the rig's
own minimum. At 80 ms / 5 Mbit it was 4.1 to 5.4 s for all four. At
150 ms / 2 Mbit it was 17.5 s for the relay with streaming off (its backlog
drains first), 8.2 s for both transcoded configurations, and 5.3 s for the
relay with streaming on.

### remote-viewer

The rig's timing client is Ryll, headless. To check that a real SPICE
client draws the transcoder's output correctly, remote-viewer (virt-viewer
11, spice-gtk 0.42) was run inside the rig's client namespace on the shaped
80 ms / 5 Mbit link, for one transcoded case of each pacing mode with busy
activity. The X server's socket is a file, so it is reachable from the
namespace. It drew the session correctly in both: the text, the key box and
the animating activity, as Ryll does.

![remote-viewer showing a transcoded session at 80 ms / 5 Mbit](transcoding/remote-viewer-80ms-5mbit.jpg)

## What this shows

- Transcoding does what it was built to do on the case it was built for. With
  incompressible activity and streaming off, a slow link no longer builds a
  backlog of seconds: socket pacing keeps the key's latency within a few
  hundred ms of the link's round trip at p50 on links down to 5 Mbit, and
  under 1.5 s at 150 ms / 2 Mbit, where the relay takes 13.6 s.
- It loses to the configuration change Kerbside could make instead.
  Relaying with `streaming-video=all` is as fast or two to five times faster
  on every link except 150 ms / 2 Mbit heavy. It costs the proxy almost
  nothing, and its loss of text is confined to a 16-pixel strip during
  animation. It also has a cost transcoding avoids: spice-server 0.15.2
  crashes qemu when a client connects while a stream exists, which
  happened here twice.
- It loses to relaying with streaming off on smooth, compressible activity
  at 80 ms / 10 Mbit, and only roughly matches it at lower rates. Both fill
  the link there.
- Pacing on a SET_ACK window is never better than pacing on the socket. The
  window costs round trips the socket does not.
- Its price is CPU and the text next to moving content. A quarter of a core
  per busy session on a LAN is six times the relay with streaming off and
  over twenty times the relay with streaming on. During
  animation, text up to 48 pixels from the activity is JPEG at about 28 dB.
  The memory is modest, and the pixmap cache that dominated the estimate is
  unused on qemu guests.
- After motion stops, transcoding is the only configuration measured that
  always ends with the exact picture, and the only one that settles quickly
  on a 2 Mbit link with streaming off.

## Caveats

- **Host load.** This host is shared, and other work ran throughout. The
  one-minute load average had a median of about 9 and passed 16, the
  number of hardware threads, for about a third of the run, peaking at 115.
  Cases limited by the link agreed between repeats regardless; for example,
  80 ms / 10 Mbit heavy with streaming off measured 2029 and 2059 ms p50 at
  loads of 20 and 31. LAN latency did not, which is why the least-loaded
  repeats are shown separately. CPU time is less sensitive than latency,
  but the CPU figures come from the same loaded runs.
- **Client stalls.** In 18 repeats, Ryll stopped reading its display
  socket for most of the case: the proxy's socket was limited by the
  client's receive window for 51% to 100% of its busy time, often
  delivering well under 1 Mbit/s whatever the link. It happened with relay
  and transcode, on four of the five shaped links, at high and low load.
  Those repeats are excluded, because they measure Ryll, not the proxy.
  Most of the lost repeats at 150 ms / 2 Mbit failed this way before their
  first key press. The cause is not established. One candidate is the
  control socket: the rig reads every `surface_drawn` event through it,
  hundreds a second.
- **qemu crashed twice** at 150 ms / 2 Mbit with streaming on, the known
  spice-server 0.15.2 crash on connecting while streams exist. Connecting
  over that link took longer than the 8 s activity delay. The rerun of that
  link used a 12 s delay.
- **The bandwidth test varies at 20 ms / 50 Mbit.** spice-server chose an
  ACK window of 20 in some relayed repeats there and 40 in others, so the
  relay's busy latency there mixes repeats at about 140 ms (window 20,
  lossless draws) and about 45 ms (window 40, JPEG draws).
- **Repeat counts are small.** Two repeats of 40 presses at
  80 ms / 10 Mbit and 80 ms / 5 Mbit, and one valid repeat for
  80 ms / 5 Mbit heavy with streaming on.
- The relay with streaming on sent MJPEG, not the re-baseline's H.264:
  the Ryll used here asks for MJPEG first (see
  [the spice-server patches](/components/kerbside/performance/spice-server-patches/#which-codec-the-streams-used)).
  Ryll decodes MJPEG, so the stream's rate control ran on reports from a
  client that drew the stream.

## Procedure

The rig is `tools/shaped-link/`, described in [Testing](/components/kerbside/testing/);
the header of `run-matrix.sh` documents every variable. Builds:

```sh
make -C rust/kerbside-proxy release FEATURES=transcode
cp rust/kerbside-proxy/target/release/kerbside-proxy /tmp/tx/kerbside-proxy-transcode
tools/shaped-link/build-ryll.sh /tmp/tx/sl     # ryll 0d140b3 plus the rect patch
tools/shaped-link/build-guest.sh /tmp/tx/guest
```

Common settings for every run:

```sh
export PROXY_BIN=/tmp/tx/kerbside-proxy-transcode \
    RYLL_BIN=/tmp/tx/sl/ryll/target/release/ryll \
    MOCK_PYTHON=/path/to/python-with-grpcio QUALITY=1 \
    ACTIVITY_DELAY=8 WARMUP=10 REPEATS=2 SAMPLES=40
G=/tmp/tx/guest Q=qemu-system-x86_64
```

The reference, unshaped and relayed with streaming off, twice per activity:

```sh
DISPLAYS="off:$Q:off:$G" CONFIGS=relay:0:0 PROFILES=0:0 \
ACTIVITIES='idle:0:0 busy:30:3 heavy:30:8' WORKDIR=/tmp/tx/ref \
    tools/shaped-link/run-matrix.sh
for a in idle busy heavy; do
    cmp /tmp/tx/ref/0ms-0mbit/$a/off-relay-r1/frozen.png \
        /tmp/tx/ref/0ms-0mbit/$a/off-relay-r2/frozen.png
done
```

The matrix, as two invocations because idle runs on two links only:

```sh
export DISPLAYS="off:$Q:off:$G all:$Q:all:$G"
export CONFIGS='relay:0:0::off,all tsock:0:0:--transcode-display:off
    tack:0:0:--transcode-display,--transcode-ack-window=40:off'
PROFILES='0:0 80:10' ACTIVITIES='idle:0:0 busy:30:3 heavy:30:8' \
    WORKDIR=/tmp/tx/main tools/shaped-link/run-matrix.sh
PROFILES='20:50 80:50 80:5 150:2' ACTIVITIES='busy:30:3 heavy:30:8' \
    WORKDIR=/tmp/tx/main tools/shaped-link/run-matrix.sh
```

Load spiked during the first matrix and lost every 150 ms / 2 Mbit repeat
of its first pass. So the unshaped, 20 ms / 50 Mbit, 80 ms / 50 Mbit and
150 ms / 2 Mbit links were run again in full, into a second `WORKDIR`. The
second run used the same commands, with `ACTIVITY_DELAY=12 WARMUP=14` for
150 ms / 2 Mbit only. The tables pool every completed repeat of both runs,
except those in which the client stalled. A repeat counts as stalled if the
last `ss` snapshot of the proxy's client-leg display socket shows
`rwnd_limited` over 50%. Then:

```sh
tools/shaped-link/quality.sh cases \
    --reference /tmp/tx/ref/0ms-0mbit/busy/off-relay-r1/frozen.png /tmp/tx/main/*/busy
# ... and the same for heavy and idle, each with its own reference
tools/shaped-link/summarise.py /tmp/tx/main
```

The first matrix's 112 cases took 2 h 15 min, and the second run's 72 cases
1 h 56 min. The slow links take longest: a case whose presses take 13 s
each takes about 10 minutes.

### Measured

- kerbside-proxy at d2f3d01, release build with the `transcode` feature.
- ryll 0d140b3 with `ryll-surface-drawn-rect.patch`, built by
  `build-ryll.sh`.
- Debian qemu 10.0.13 (libspice-server 0.15.2), KVM.
- Host: AMD Ryzen 7 8845HS (8 cores, 16 threads), 28 GiB, Linux
  6.12.113, CUBIC, the sysctls as on
  [the backpressure page](/components/kerbside/performance/proxy-backpressure/#test-host).
