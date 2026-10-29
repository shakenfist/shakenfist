# A SPICE helper on qemu's D-Bus display, on a shaped link

This page compares two ways of serving a qemu guest's console over SPICE
through Kerbside's proxy:

- qemu's own spice-server, with `streaming-video=off` and with
  `streaming-video=all`;
- [andris](https://github.com/shakenfist/andris), a standalone SPICE server,
  attached to qemu's D-Bus display (`-display dbus,p2p=on`) with its qemu
  platform, as a helper process next to qemu.

The proxy relays both in exactly the same way. It uses the shaped-link rig,
the guest, the keypress-to-draw metric and the image-quality scoring of
[the transcoding measurements](/components/kerbside/performance/transcoding-measurements/), with the same
link profiles, so the tables line up with that page's. Measured on
2026-10-29.

## Summary

- **Where andris streams, the helper's latency is level with
  `streaming-video=all`.** On the unshaped, 20 ms / 50 Mbit and
  80 ms / 50 Mbit links, with busy and heavy activity, and at
  80 ms / 10 Mbit with busy activity, the helper's p50 was within 5 ms of
  `all`'s, and its p95 within 7 ms. With heavy activity on the 50 Mbit links
  it was 5 to 6 times faster than `streaming-video=off`: 43 against
  250 ms at 20 ms / 50 Mbit and 98 against 462 ms at 80 ms / 50 Mbit.
- **Where andris does not stream, the helper is the slowest of the three.**
  andris sends a moving area as H.264 only after 20 frames of change within
  2 s, and on the slower links its ACK window held it below that rate, so
  it sent the activity as JPEG tiles that filled the link. At
  80 ms / 10 Mbit heavy its p50 was 3492 ms, against 2039 ms with streaming
  off and 113 ms with `all`. At 80 ms / 5 Mbit it was 3263 ms (busy) and
  6931 ms (heavy), against 371 and 4952 ms with streaming off. At
  150 ms / 2 Mbit no helper repeat drew its first key press within the
  rig's 15 s limit, in four tries of each activity. Neither did the relay
  with streaming off with heavy activity.
- **Idle, the helper draws a key press sooner.** Its p50 was 3 ms on the
  unshaped link, against 15 and 19 ms for spice-server, and 88 ms at
  80 ms / 10 Mbit, against 96 and 98 ms.
- **qemu plus andris costs about what qemu with spice-server costs, except
  with busy activity on fast links, where it costs up to twice as much as
  qemu with `streaming-video=all`.** With activity, qemu itself used 0.08
  to 0.10 cores behind the helper, against 0.12 to 0.68 with streaming off
  and 0.11 to 0.18 with `all`. andris added 0.004 cores idle, 0.10 to 0.11
  for a heavy activity it streamed, and 0.17 to 0.23 for a busy one, which
  it encodes as a larger H.264 stream. Idle, the sum was 0.018 to 0.022
  cores against 0.017 to 0.021. With heavy activity on a LAN it was 0.195
  cores, against 0.677 with streaming off and 0.173 with `all`. With busy
  activity on the 50 Mbit links it was 0.33, against 0.18 to 0.22.
- **Memory moves from qemu to the helper.** qemu's peak RSS was 181 to
  194 MiB without spice-server, against 207 to 228 MiB with it. andris
  peaked at 34 to 66 MiB, so the pair's peaks summed to 222 to 250 MiB.
- **The helper sends a tenth of the display traffic where it streams.** 0.9
  to 2.2 Mbit/s, against 5.5 to 48 Mbit/s for `all` and up to 151 Mbit/s for
  streaming off.
- **The helper puts lossy pixels on the text next to the activity, and
  repairs them.** Mid-animation, 53248 background pixels next to the
  activity differed from the reference in every helper repeat: the
  64-pixel tiles along the activity's left and top edges. Over the whole
  background that was 41.7 dB for a busy activity andris streamed, 33.0
  to 36.5 dB for a heavy one, and 39.3 to 39.4 dB where it sent JPEG
  tiles. `all` left its 16-pixel strip (7680 pixels, 46.4 to 47.2 dB).
  Every helper repeat's frozen frame matched the lossless reference
  exactly.
- **The std-vga row costs qemu 0.77 cores whatever the guest draws.**
  qemu polls std-vga's dirty bitmap every 30 ms and copies each changed
  band over the D-Bus socket. Its latency followed the virtio-gpu helper's,
  22 to 30 ms slower where andris streamed, and like it, it was seconds
  behind at 80 ms / 10 Mbit heavy (4400 ms p50, one repeat of two).
- **No server crashed.** No qemu or andris exited in any of the 108
  matrix repeats or the 10 reference repeats, and spice-server 0.15.2's
  crash on connecting while streams exist did not recur. The 13 repeats
  lost were all first key presses that took over 15 s. See
  [Caveats](#caveats).

## Configurations

| Name | Display server | qemu display | Pacing |
|------|----------------|--------------|--------|
| SPICE, streaming off | spice-server in qemu | `-spice`, `streaming-video=off` | spice-server's ACK window |
| SPICE, streaming all | spice-server in qemu | `-spice`, `streaming-video=all` | spice-server's ACK window |
| helper | andris, `--platform qemu` | `-display dbus,p2p=on`, no `-spice` | andris's ACK window (20) |
| std-vga helper | as helper | as helper, with qemu's std-vga | as helper |

Every case runs through the proxy, relayed, with no proxy flags, and with
the same Ryll. andris ran with its defaults: frames at least 33 ms apart,
JPEG quality 85 for photographic tiles with a lossless resend after 0.5 s
of stillness, and H.264 streams for areas it judges to be video. It
attaches through qemu's QMP socket, takes picture and damage through
qemu's shared-memory `Unix.Map` interface on virtio-gpu, and listens where
spice-server would, with the same ticket.

The guest is the rig's keydraw guest, as in
[the transcoding measurements](/components/kerbside/performance/transcoding-measurements/#configurations):
1280x800, a 96x96 key box, and a 640x480 activity rectangle at (624, 160).
Idle draws nothing but the key box; busy animates a smooth gradient with
3 bits of noise at 30 fps; heavy uses 8 bits of noise. The guest runs on
virtio-gpu 2D (`virtio-vga`) except in the std-vga rows, where it runs on
qemu's std-vga, driven by bochs-drm. On virtio-gpu its damage path is
`DRM_IOCTL_MODE_DIRTYFB`. bochs-drm has no dirty-framebuffer hook, so on
std-vga qemu finds the damage itself, from VRAM's dirty bitmap.

## Results

Latency is keypress-to-draw of the key box, in ms, pooled over the repeats
counted. The quality columns compare the client's own lossless screenshot
with the reference (see [Procedure](#procedure)). "Background exact"
counts repeats whose static background, everything outside the activity
and the key box, matched the reference exactly mid-animation, with the
most pixels that differed in any repeat. The frozen-frame columns compare
the whole frame (less the key box) once the activity has stopped and the
display has settled. 1.0000 and `inf` mean identical. Display Mbit/s is
what the proxy relayed to the client during the key presses.

| Link | Activity | Config | Repeats | p50 ms | p95 ms | Timeouts | Display Mbit/s | Background exact (most px differing) | Frozen SSIM min | Frozen PSNR min dB |
|---|---|---|---|---|---|---|---|---|---|---|
| unshaped | idle | SPICE, streaming off | 2 | 15 | 30 | 0 | 0.0 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 19 | 30 | 0 | 0.0 | 2 of 2 (0) | 1.0000 | inf |
|  |  | helper | 2 | 3 | 5 | 0 | 0.0 | 2 of 2 (0) | 1.0000 | inf |
|  | busy | SPICE, streaming off | 2 | 21 | 32 | 0 | 103.8 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 20 | 32 | 0 | 8.7 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 21 | 35 | 0 | 1.2 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | std-vga helper | 4 | 45 | 245 | 1 | 5.8 | 0 of 4 (53248) | 1.0000 | inf |
|  | heavy | SPICE, streaming off | 2 | 33 | 173 | 0 | 151.0 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 22 | 42 | 0 | 47.8 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 22 | 36 | 0 | 2.1 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | std-vga helper | 4 | 52 | 580 | 0 | 15.8 | 0 of 4 (53248) | 1.0000 | inf |
| 20 ms / 50 Mbit | busy | SPICE, streaming off | 2 | 135 | 172 | 0 | 43.4 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 38 | 52 | 0 | 7.6 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 40 | 56 | 0 | 1.3 | 0 of 2 (53248) | 1.0000 | inf |
|  | heavy | SPICE, streaming off | 2 | 250 | 319 | 0 | 43.8 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 39 | 53 | 0 | 35.0 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 43 | 56 | 0 | 1.9 | 0 of 2 (53248) | 1.0000 | inf |
| 80 ms / 50 Mbit | busy | SPICE, streaming off | 2 | 105 | 118 | 0 | 16.1 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | SPICE, streaming all | 2 | 100 | 111 | 0 | 6.4 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 102 | 116 | 0 | 1.3 | 0 of 2 (53248) | 1.0000 | inf |
|  | heavy | SPICE, streaming off | 2 | 462 | 583 | 0 | 44.3 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 99 | 111 | 0 | 18.1 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 98 | 118 | 0 | 1.5 | 0 of 2 (53248) | 1.0000 | inf |
| 80 ms / 10 Mbit | idle | SPICE, streaming off | 2 | 96 | 111 | 0 | 0.1 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 98 | 111 | 0 | 0.1 | 2 of 2 (0) | 1.0000 | inf |
|  |  | helper | 2 | 88 | 88 | 0 | 0.1 | 2 of 2 (0) | 1.0000 | inf |
|  | busy | SPICE, streaming off | 2 | 184 | 216 | 0 | 8.7 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | SPICE, streaming all | 2 | 101 | 114 | 0 | 5.5 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 106 | 119 | 0 | 0.9 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | std-vga helper | 2 | 135 | 160 | 0 | 6.1 | 0 of 2 (53248) | 1.0000 | inf |
|  | heavy | SPICE, streaming off | 2 | 2039 | 2444 | 0 | 9.3 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 113 | 277 | 0 | 7.9 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 3492 | 4238 | 0 | 9.4 | 0 of 2 (53248) | 1.0000 | inf |
|  |  | std-vga helper | 1 of 2 | 4400 | 5872 | 0 | 9.4 | 0 of 1 (53248) | 1.0000 | inf |
| 80 ms / 5 Mbit | busy | SPICE, streaming off | 2 | 371 | 453 | 0 | 4.5 | 2 of 2 (0) | 0.9888 | 43.9 |
|  |  | SPICE, streaming all | 2 | 118 | 144 | 0 | 4.2 | 0 of 2 (7680) | 1.0000 | inf |
|  |  | helper | 2 | 3263 | 7313 | 0 | 4.7 | 0 of 2 (53248) | 1.0000 | inf |
|  | heavy | SPICE, streaming off | 2 | 4952 | 5302 | 0 | 4.7 | 2 of 2 (0) | 1.0000 | inf |
|  |  | SPICE, streaming all | 2 | 112 | 470 | 0 | 3.8 | 2 of 2 (0) | 1.0000 | inf |
|  |  | helper | 2 | 6931 | 8586 | 0 | 4.7 | 0 of 2 (53248) | 1.0000 | inf |
| 150 ms / 2 Mbit | busy | SPICE, streaming off | 4 | 869 | 1113 | 0 | 1.8 | 4 of 4 (0) | 0.9888 | 43.9 |
|  |  | SPICE, streaming all | 4 | 185 | 239 | 0 | 1.6 | 0 of 4 (7680) | 1.0000 | inf |
|  |  | helper | 0 of 4 | over 15 s | - | - | - | - | - | - |
|  | heavy | SPICE, streaming off | 0 of 4 | over 15 s | - | - | - | - | - | - |
|  |  | SPICE, streaming all | 4 | 7315 | 7901 | 0 | 1.9 | 4 of 4 (0) | 1.0000 | inf |
|  |  | helper | 0 of 4 | over 15 s | - | - | - | - | - | - |

Repeats are out of two, and out of four at 150 ms / 2 Mbit, which ran
twice (see [Procedure](#procedure)). "0 of 4, over 15 s" means that in
every repeat the rig's unmeasured probe press, which follows the warm-up,
drew nothing in the key box within 15 s, so the case recorded no samples.
The unshaped std-vga rows also have four repeats: two taken while the
load average was 10 to 26, and two taken again once it was 2.0 to 2.2.
The quiet pair alone measured 44 / 71 ms (busy) and 45 / 66 ms (heavy).
The one timeout, a press whose draw took over 15 s, was in a loaded
repeat (load 26).

### Cost

CPU is in cores (CPU seconds per second) over the key presses, averaged over
the repeats. Peak RSS covers the whole case. "qemu + andris" is the helper's
whole cost on the host, to set against qemu's alone in the spice-server
rows.

| Link | Activity | Config | Proxy cores | qemu cores | andris cores | qemu + andris cores | Proxy peak MiB | qemu peak MiB | andris peak MiB |
|---|---|---|---|---|---|---|---|---|---|
| unshaped | idle | SPICE, streaming off | 0.001 | 0.019 | - | - | 9 | 208 | - |
|  |  | SPICE, streaming all | 0.001 | 0.020 | - | - | 9 | 208 | - |
|  |  | helper | 0.001 | 0.018 | 0.004 | 0.022 | 9 | 188 | 37 |
|  | busy | SPICE, streaming off | 0.054 | 0.312 | - | - | 9 | 224 | - |
|  |  | SPICE, streaming all | 0.014 | 0.161 | - | - | 9 | 212 | - |
|  |  | helper | 0.004 | 0.087 | 0.207 | 0.294 | 10 | 189 | 52 |
|  |  | std-vga helper | 0.008 | 0.775 | 0.221 | 0.996 | 10 | 198 | 65 |
|  | heavy | SPICE, streaming off | 0.076 | 0.677 | - | - | 10 | 223 | - |
|  |  | SPICE, streaming all | 0.029 | 0.173 | - | - | 9 | 219 | - |
|  |  | helper | 0.002 | 0.085 | 0.110 | 0.195 | 10 | 184 | 66 |
|  |  | std-vga helper | 0.010 | 0.777 | 0.156 | 0.933 | 10 | 200 | 58 |
| 20 ms / 50 Mbit | busy | SPICE, streaming off | 0.011 | 0.196 | - | - | 9 | 223 | - |
|  |  | SPICE, streaming all | 0.013 | 0.179 | - | - | 9 | 212 | - |
|  |  | helper | 0.004 | 0.100 | 0.227 | 0.327 | 10 | 189 | 49 |
|  | heavy | SPICE, streaming off | 0.011 | 0.230 | - | - | 10 | 222 | - |
|  |  | SPICE, streaming all | 0.018 | 0.179 | - | - | 9 | 217 | - |
|  |  | helper | 0.002 | 0.103 | 0.111 | 0.213 | 10 | 193 | 48 |
| 80 ms / 50 Mbit | busy | SPICE, streaming off | 0.013 | 0.221 | - | - | 9 | 228 | - |
|  |  | SPICE, streaming all | 0.014 | 0.179 | - | - | 9 | 222 | - |
|  |  | helper | 0.004 | 0.099 | 0.229 | 0.328 | 10 | 190 | 46 |
|  | heavy | SPICE, streaming off | 0.010 | 0.296 | - | - | 10 | 228 | - |
|  |  | SPICE, streaming all | 0.014 | 0.158 | - | - | 9 | 221 | - |
|  |  | helper | 0.002 | 0.104 | 0.096 | 0.200 | 10 | 192 | 51 |
| 80 ms / 10 Mbit | idle | SPICE, streaming off | 0.001 | 0.021 | - | - | 9 | 208 | - |
|  |  | SPICE, streaming all | 0.001 | 0.017 | - | - | 9 | 207 | - |
|  |  | helper | 0.001 | 0.014 | 0.004 | 0.018 | 9 | 188 | 34 |
|  | busy | SPICE, streaming off | 0.005 | 0.145 | - | - | 9 | 224 | - |
|  |  | SPICE, streaming all | 0.010 | 0.139 | - | - | 9 | 211 | - |
|  |  | helper | 0.003 | 0.075 | 0.174 | 0.250 | 10 | 181 | 56 |
|  |  | std-vga helper | 0.006 | 0.774 | 0.213 | 0.986 | 9 | 193 | 58 |
|  | heavy | SPICE, streaming off | 0.002 | 0.126 | - | - | 10 | 222 | - |
|  |  | SPICE, streaming all | 0.007 | 0.120 | - | - | 10 | 213 | - |
|  |  | helper | 0.003 | 0.096 | 0.054 | 0.149 | 10 | 189 | 42 |
|  |  | std-vga helper | 0.002 | 0.764 | 0.060 | 0.824 | 10 | 198 | 51 |
| 80 ms / 5 Mbit | busy | SPICE, streaming off | 0.003 | 0.146 | - | - | 9 | 222 | - |
|  |  | SPICE, streaming all | 0.011 | 0.160 | - | - | 9 | 221 | - |
|  |  | helper | 0.002 | 0.102 | 0.062 | 0.163 | 10 | 194 | 45 |
|  | heavy | SPICE, streaming off | 0.002 | 0.135 | - | - | 10 | 228 | - |
|  |  | SPICE, streaming all | 0.007 | 0.135 | - | - | 9 | 225 | - |
|  |  | helper | 0.002 | 0.101 | 0.039 | 0.140 | 10 | 194 | 38 |
| 150 ms / 2 Mbit | busy | SPICE, streaming off | 0.002 | 0.128 | - | - | 9 | 227 | - |
|  |  | SPICE, streaming all | 0.008 | 0.141 | - | - | 9 | 224 | - |
|  | heavy | SPICE, streaming all | 0.001 | 0.107 | - | - | 10 | 228 | - |

Cases with no completed repeat have no cost figures, since CPU is measured
over the key presses.

**qemu.** Without spice-server, qemu used 0.075 to 0.104 cores with
activity, much the same on every link, because it no longer compresses
anything: on virtio-gpu it shares the guest's framebuffer with andris
and sends it only each flush's rectangle over the D-Bus socket. With
spice-server it used more on fast links, where it sent more: 0.31 to 0.68
cores with streaming off on a LAN, 0.16 to 0.17 with `all`.

**andris.** andris's CPU follows what it encodes. A live H.264 stream of the
640x480 activity costs it 0.17 to 0.23 cores for busy activity and 0.10 to
0.11 for heavy. The stream ran at 525 to 910 kbit/s, except with heavy
activity at 80 ms / 50 Mbit, where andris's encoder held it to
290 kbit/s. When it sends JPEG tiles instead, as on the slow links, the
link limits how many it encodes, and its CPU falls to 0.04 to 0.06
cores.

**The proxy** relays both alike: 0.001 to 0.08 cores. It used less with the
helper, because the helper sent less.

**Memory.** Without spice-server, qemu's peak RSS was 19 to 43 MiB smaller.
andris's peak, 34 to 66 MiB, holds its copy of the framebuffer and its
shadow (4 MiB each at 1280x800), its encoders, and the stream's state.

### What andris sent

andris logs a summary of each session. Its stream column is the H.264
stream it started, if any; its ACK lag is how long, on average, its
messages waited for the client's acknowledgement.

| Link | Activity | Stream | Stream kbit/s | Display kbit/s | JPEG draws | ACK lag ms |
|---|---|---|---|---|---|---|
| unshaped | busy | yes | 860 to 880 | 1370 to 1390 | 20 | 2 |
|  | heavy | yes | 890 to 910 | 2140 to 2200 | 20 | 3 to 4 |
| 20 ms / 50 Mbit | busy | yes | 870 | 1400 | 20 | 24 to 27 |
|  | heavy | yes | 680 to 750 | 1950 to 2010 | 20 | 58 to 63 |
| 80 ms / 50 Mbit | busy | yes | 880 | 1380 to 1410 | 20 | 86 to 91 |
|  | heavy | yes | 290 to 300 | 1540 to 1610 | 20 | 195 to 199 |
| 80 ms / 10 Mbit | busy | yes | 525 to 545 | 1030 to 1050 | 20 | 97 to 98 |
|  | heavy | no | - | 9280 | 657 to 685 | 3530 to 3535 |
| 80 ms / 5 Mbit | busy | no | - | 4650 | 912 to 929 | 3403 to 3439 |
|  | heavy | no | - | 4700 | 596 | 6460 to 6609 |
| 150 ms / 2 Mbit | busy | no | - | 2010 to 2330 | 53 | 7067 to 7143 |
|  | heavy | no | - | 2900 to 3380 | 30 | 10658 to 10806 |

At 150 ms / 2 Mbit the sessions ended about 25 s in, after the failed
probe press, so they sent fewer draws.

Every session that streamed sent 20 JPEG draws of the activity's first
frames before its stream began. andris starts a stream on an area whose
64-pixel tiles have each changed in 20 frames within 2 s, with no gap
over 200 ms, and are mostly photographic. Where the link could not carry
those first JPEG frames as fast as the guest drew them, andris's ACK
window throttled it below that rate, so no tile ever qualified, and it
went on sending every frame of the activity as JPEG tiles into a link
that could not carry them. Its ACK lag then grew to seconds,
and the key box's draws queued behind those tiles.

### Unshaped latency

On the unshaped link the latency is host time. The first of each cell's two
repeats ran while other work on the host pushed the load average to 6 to
23; the second ran with it at 0.9 to 5.2. The second repeats alone:

| Activity | Config | Load average | p50 ms | p95 ms |
|---|---|---|---|---|
| idle | SPICE, streaming off | 1.4 | 14 | 29 |
| | SPICE, streaming all | 1.4 | 20 | 30 |
| | helper | 1.0 | 4 | 5 |
| busy | SPICE, streaming off | 0.9 | 24 | 33 |
| | SPICE, streaming all | 0.9 | 18 | 33 |
| | helper | 1.0 | 22 | 36 |
| heavy | SPICE, streaming off | 1.6 | 22 | 94 |
| | SPICE, streaming all | 3.0 | 20 | 33 |
| | helper | 5.2 | 22 | 36 |

The helper's idle latency is lower because andris sends a change as soon
as it is told of it, at most 33 ms after the previous frame. qemu hands
spice-server its updates on its display refresh timer, every 30 ms by
default, which would add 15 ms on average; the difference here was 10
to 16 ms.

### Image quality

The reference is the frozen frame of an unshaped case relayed from
spice-server with streaming off, where every draw is lossless. It was taken
twice per activity, and the two captures were byte-identical for each of
idle, busy and heavy. A std-vga reference, taken the same way for busy and
heavy, was byte-identical to the virtio-gpu one, so the std-vga rows are
scored against the same reference.

**During animation.** andris works in 64-pixel tiles. As on the
transcoding page, the activity's left and top edges, at x 624 and y 160,
do not fall on tile boundaries, so the tiles along them are mostly text.
andris's stream covers whole tiles, 704x512 at (576, 128), and so do its
JPEG tiles, so 48 pixels to the left of the activity and 32 above it go
lossy with it: 53248 pixels. PSNR over the whole static background
(707584 pixels) was 41.7 dB for a busy activity andris streamed and 33.0
to 36.5 dB for a heavy one, whose noise its H.264 encoder spread onto the
text at under 1 Mbit/s. Where it sent JPEG tiles, at quality 85, it was
39.3 to 39.4 dB. With `all`, spice-server's streams are 32 pixels wide,
and the 16-pixel strip to the left of the activity is lossy (7680
pixels, 46.4 to 47.2 dB over the background). With streaming off the
background stays exact.

The crops below run from 64 pixels left of and 48 above the activity's
top-left corner, enlarged two times, taken while the activity ran. The last
panel is the helper's difference from the reference, amplified eight times,
with the activity masked. At 80 ms / 10 Mbit andris streamed:

![Mid-animation crops at 80 ms / 10 Mbit](dbus-helper/legibility-80ms-10mbit.png)

At 80 ms / 5 Mbit it sent JPEG tiles:

![Mid-animation crops at 80 ms / 5 Mbit](dbus-helper/legibility-80ms-5mbit.png)

**After the activity stops.** Every helper repeat, on virtio-gpu and on
std-vga, converged to the reference exactly: andris resends a lossy area
losslessly once it has been still for 0.5 s. `all` also ended exact. The
relay with streaming off kept spice-server's last JPEG draw with busy
activity on every 80 ms and 150 ms link (SSIM 0.9888, 43.9 dB), as on the
transcoding page.

How long the display took to settle after the freeze, median over busy and
heavy: about 3 s on every link up to 80 ms / 50 Mbit for all three, which
is the rig's own minimum. At 80 ms / 10 Mbit it was 3.6 s with streaming
off, 3.1 s with `all` and 4.6 s for the helper. At 80 ms / 5 Mbit it was
5.6, 3.6 and 7.7 s. At 150 ms / 2 Mbit it was 8.7 s with `all` and 3.0 s
with streaming off (busy only; the helper has no completed repeat there).

### std-vga

On std-vga qemu has no shared buffer to hand andris. It polls the dirty
bitmap every 30 ms and sends each changed band of whole scanlines over the
D-Bus socket. qemu used 0.76 to 0.78 cores in every std-vga case, against
0.08 to 0.10 for the same activity on virtio-gpu, and andris 0.13 to 0.23
with a stream and 0.06 without. The bands are full-width, so andris
diffed them against its shadow, and in most sessions sent 800 to 2600
JPEG draws where it sent 20 on virtio-gpu. It still streamed the activity
where the link allowed, and its latency at 80 ms / 10 Mbit busy was 135 ms
p50, 29 ms behind the virtio-gpu helper.

## Caveats

- **The client decoded andris's streams, and spice-server's that it
  accepted.** andris sent H.264, which Ryll decoded: 10442 frames received
  and 10442 decoded, in the 14 streams of the virtio-gpu helper repeats.
  spice-server with `streaming-video=all` sent MJPEG, not H.264, in every
  case. It offered 21 streams at once, one for each 32-pixel column of the
  activity (with heavy activity at 80 ms / 5 Mbit and 150 ms / 2 Mbit it
  also ended and recreated them, 36 and 983 in one repeat each). Ryll
  accepted 16, its limit on open streams, and refused the rest, and it
  decoded every frame of the streams it accepted (302549 of 302549). So
  with `all` Ryll did not decode five columns of the activity while it
  moved, and spice-server's rate control had reports from 16 of its 21
  streams. The key box is never streamed, so its latency is unaffected.
  The earlier pages on this rig, with the same Ryll and the same proxy,
  report spice-server's streams as H.264 that Ryll did not decode. Why
  spice-server chose MJPEG here was not established.
- **The guest flushes its whole plane.** The guest's kernel (Linux 6.12,
  with the stock `drm_kms_helper`) carries the bug that ignores damage
  clips, so every flush from this guest covers all 1280x800 pixels,
  whatever it drew. A runtime trace of qemu's `virtio_gpu_cmd_res_flush`
  event against andris's listener showed this, and showed qemu passing
  each flush on unchanged. This holds for every configuration. qemu's
  spice-display diffs each update against its own copy in 32-pixel columns
  before spice-server sees it; with the D-Bus display, andris diffs in
  64-pixel tiles instead.
- **Host load.** This host is shared. The one-minute load average had a
  median of 1.3 over the run and passed 16, the number of hardware
  threads, for 8% of it, peaking at 27, all between 19:44 and 20:13. That
  window covered the references, the std-vga row and the first repeat of
  each unshaped case. No repeat was lost to it, and none showed the client
  stall that the transcoding page excluded: the client leg's
  `rwnd_limited` share was under 50% in every repeat. LAN latency moves
  with load, which is why the second, quiet repeats are shown separately.
  The std-vga row's unshaped cases had run entirely inside it, so they
  were run again at a load of about 2 and pooled. The load average at the
  start and end of each invocation:

  | Invocation | Start | End |
  |---|---|---|
  | references (virtio-gpu) | 4.2 | 7.5 |
  | references (std-vga) | 5.5 | 11.4 |
  | std-vga helper | 11.4 | 4.7 |
  | matrix, unshaped and 80 ms / 10 Mbit | 4.7 | 1.2 |
  | matrix, the other four links | 1.2 | 1.6 |
  | 150 ms / 2 Mbit again | 1.5 | 1.7 |
  | std-vga helper, unshaped again | 1.7 | 2.1 |
- **First key presses over 15 s.** The rig gives up on a case whose probe
  press draws nothing within 15 s. At 150 ms / 2 Mbit that happened in all
  four repeats of the helper, busy and heavy, and of the relay with
  streaming off, heavy, at load averages of 0.6 to 1.5. andris reported
  ACK lags of 7 and 11 s there. The transcoding page measured the relay
  with streaming off at a p50 of 13.6 s on that cell, so its first press
  is near the limit. The same happened in one std-vga repeat at
  80 ms / 10 Mbit heavy, at load 22.
- **No crashes.** No qemu or andris exited during a case. spice-server
  0.15.2's crash on connecting while streams exist did not recur in any
  `all` repeat, with the activity delayed 8 s, or 12 s on the second
  150 ms / 2 Mbit run.
- **Repeat counts are small.** Two repeats of 40 presses per cell, four at
  150 ms / 2 Mbit.
- **The helper's listener has no lag watchdog.** If andris stopped reading,
  qemu would queue its display messages without bound on std-vga and on a
  guest that flips pages. qemu's peak RSS in the std-vga cases, 193 to
  200 MiB, shows that no such queue built up here: the std-vga row's
  slow-link latency comes from andris's ACK window, not from qemu.

## Procedure

The rig is `tools/shaped-link/`, described in [Testing](/components/kerbside/testing/);
the header of `run-matrix.sh` documents every variable, including the
helper display, `ANDRIS_BIN` and `GUEST_VGA`. Builds:

```sh
make -C rust/kerbside-proxy release
tools/shaped-link/build-ryll.sh /tmp/dh/build     # ryll 0d140b3 plus the rect patch
tools/shaped-link/build-andris.sh /tmp/dh/build   # andris 440b782, qemu feature
tools/shaped-link/build-guest.sh /tmp/dh/guest
```

Common settings for every run:

```sh
export PROXY_BIN=rust/kerbside-proxy/target/release/kerbside-proxy \
    RYLL_BIN=/tmp/dh/build/ryll/target/release/ryll \
    ANDRIS_BIN=/tmp/dh/build/andris/target/release/andris \
    MOCK_PYTHON=.tox/py3/bin/python QUALITY=1 \
    ACTIVITY_DELAY=8 WARMUP=10 REPEATS=2 SAMPLES=40
G=/tmp/dh/guest Q=qemu-system-x86_64
```

The references, unshaped and relayed with streaming off, twice per
activity, then compared with `cmp`:

```sh
DISPLAYS="off:$Q:off:$G" CONFIGS=relay:0:0 PROFILES=0:0 \
ACTIVITIES='idle:0:0 busy:30:3 heavy:30:8' WORKDIR=/tmp/dh/ref \
    tools/shaped-link/run-matrix.sh
GUEST_VGA=std DISPLAYS="stdoff:$Q:off:$G" CONFIGS=relay:0:0 PROFILES=0:0 \
ACTIVITIES='busy:30:3 heavy:30:8' WORKDIR=/tmp/dh/stdref \
    tools/shaped-link/run-matrix.sh
```

The std-vga row:

```sh
GUEST_VGA=std DISPLAYS="stdhelper:$Q:helper:$G" CONFIGS=relay:0:0 \
PROFILES='0:0 80:10' ACTIVITIES='busy:30:3 heavy:30:8' WORKDIR=/tmp/dh/std \
    tools/shaped-link/run-matrix.sh
```

The matrix, as two invocations because idle runs on two links only:

```sh
export DISPLAYS="off:$Q:off:$G all:$Q:all:$G helper:$Q:helper:$G" CONFIGS=relay:0:0
PROFILES='0:0 80:10' ACTIVITIES='idle:0:0 busy:30:3 heavy:30:8' \
    WORKDIR=/tmp/dh/main-a tools/shaped-link/run-matrix.sh
PROFILES='20:50 80:50 80:5 150:2' ACTIVITIES='busy:30:3 heavy:30:8' \
    WORKDIR=/tmp/dh/main-b tools/shaped-link/run-matrix.sh
```

Three cells at 150 ms / 2 Mbit lost every repeat to a first press over
15 s. That link was run again in full, into a third `WORKDIR`, with
`ACTIVITY_DELAY=12 WARMUP=14`, as the transcoding page's rerun was; the
same three cells failed the same way. The std-vga row's unshaped link,
whose repeats had all run during the load spike, was also run again,
into a fourth `WORKDIR`, once the host was quiet. The tables pool every
completed repeat. A repeat would count as stalled, and be excluded, if the
last `ss` snapshot of the proxy's client-leg display socket showed
`rwnd_limited` over 50%; none did. Then:

```sh
tools/shaped-link/quality.sh cases \
    --reference /tmp/dh/ref/0ms-0mbit/busy/off-relay-r1/frozen.png /tmp/dh/main-*/*/busy
# ... and the same for heavy and idle, each with its own reference
tools/shaped-link/summarise.py /tmp/dh/pooled
```

The whole run took about 2 h 40 min: the 36 cases of the first matrix
invocation 40 minutes, the 48 of the second 1 h 15 min, and the slowest
cases, at 80 ms / 5 Mbit and 150 ms / 2 Mbit, 3 to 5 minutes each.
[`dbus-helper/summary.md`](/components/kerbside/performance/dbus-helper/summary/) is `summarise.py`'s
full output over the pooled repeats, and
[`dbus-helper/andris-sessions.txt`](dbus-helper/andris-sessions.txt) is
andris's session summary for each helper repeat.

### Measured

- kerbside-proxy built from kerbside 177fb7e (its `rust/` tree is unchanged
  since 72f06db), release build, default features.
- andris 440b782, from andris's pull request 56 (branch
  `qemu-dbus-platform`), built by `build-andris.sh` with the `qemu` feature
  and the qemu-display bindings at git 6ffedb9. The pull request merged
  later, as 9a997a3, with review changes; one of them copies each update's
  rows out of qemu's shared memory, which this run does not include.
- ryll 0d140b3 with `ryll-surface-drawn-rect.patch`, built by
  `build-ryll.sh`.
- Debian qemu 10.0.13 (`1:10.0.13+ds-0+deb13u1`, with `ui-dbus` from
  `qemu-system-modules-opengl`) and libspice-server 0.15.2, KVM.
- The guest from `build-guest.sh`, on the host's kernel, 6.12.113, with
  its stock `drm_kms_helper`.
- Host: AMD Ryzen 7 8845HS (8 cores, 16 threads), 28 GiB, Linux
  6.12.113, CUBIC, the sysctls as on
  [the backpressure page](/components/kerbside/performance/proxy-backpressure/#test-host).
