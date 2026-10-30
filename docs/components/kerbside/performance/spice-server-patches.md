# spice-server patches on a shaped link

This page records what was found, and patched, in spice-server itself
while measuring SPICE on Kerbside's shaped-link rig: which video codec
the streams on the earlier pages really used, why every released
spice-server crashes when certain clients connect to a VM that is
already streaming, and which of four small spice-server changes made a
measurable difference. It uses the rig of
[Proxy backpressure on a shaped link](/components/kerbside/performance/proxy-backpressure/) and the
streaming configurations of
[Streaming and the qemu damage series on a shaped link](/components/kerbside/performance/streaming-rebaseline/).
Written on 2026-10-30.

The patches that earned their place form a series against spice-server
master, ready to send upstream but not yet sent. It lives in
[kerbside-patches](https://github.com/shakenfist/kerbside-patches)
under `upstream/spice/`: the series in `series/v1/`, the build script
in `rig/build-spice.sh`, the generated tables in
`results/patch-matrix.md`, and the two dropped patches in `dropped/`.

## Summary

| Question | Answer |
|---|---|
| Which codec did `streaming-video=all` use? | MJPEG on every page since the re-baseline. Ryll changed the order it asks for codecs; the proxy relayed the request correctly. |
| Does the connect crash survive in current spice-server? | Yes. 0.15.2, 0.16.0 and master (adeb620a04, 2026-10-29) each crashed in 20 of 20 connects. A 16-line patch takes master to 0 of 20. |
| Which clients trigger it? | A client that sends its codec preference as the display channel comes up, as Ryll does. remote-viewer does not. |
| Hardware encoding on any GPU | Kept. vah264enc on this AMD GPU cut qemu's CPU by about 15% and, on one loaded link, p50 latency by 40% and p95 by nearly 80%, for 56 to 64 MiB of qemu memory paid only when a GStreamer encoder is built. |
| Trace ring 8 to 64 | Dropped: no measurable effect. |
| Frame-rate ceiling 30 to 60 | Dropped: no measurable effect. Rate control, not the ceiling, sets the frame rate on these links. |
| Upgrading 0.15.2 to master | Helps in 9 of 15 cells, chiefly qemu CPU and, with `streaming-video=all`, latency and bandwidth. It does not fix the crash. |

## Which codec the streams used

The re-baseline reported every `streaming-video=all` stream as H.264,
and the pages after it repeated that. Only the re-baseline was right.
The raw runs behind the ACK-window, transcoding-measurement and
transcoding-spike pages recorded MJPEG (codec 1) stream creations, 252
in one set of runs and 2887 in the other, and no H.264 one. Those pages
have been corrected.

qemu 10.0.13 has no option to choose a codec, so spice-server starts
from its default order, `spice:mjpeg` first, and reorders it only when
the client sends `SPICE_MSGC_DISPLAY_PREFERRED_VIDEO_CODEC_TYPE`. What
changed was the client's request:

| Ryll commit | Date | Codecs it asks for |
|---|---|---|
| 50a9cc0, used by the re-baseline | 2026-09-24 | H.264, then MJPEG |
| 24a80c0 | 2026-10-11 | stops offering H.264 |
| 15cd776, in the 0d140b3 every later page used | 2026-10-16 | MJPEG first |

spice-server's debug log shows the result directly. With Ryll 0d140b3's
defaults it logs `Preferred video-codecs: spice:mjpeg gstreamer:mjpeg
gstreamer:h264 gstreamer:vp8 gstreamer:vp9`; with
`--experimental-h264` it logs `gstreamer:h264` first and builds x264enc
pipelines, through the proxy. kerbside-proxy relays the message
whenever the client's capabilities carry `PREF_VIDEO_CODEC_TYPE`, and
did so here.

So the MJPEG pages measured streams that Ryll decodes, and their stream
rate control ran on reports from a client that drew the stream. Ryll
still does not decode spice-server's H.264 (ryll#398). The H.264 rows
below therefore measure latency, CPU and whether the hardware encoder
engaged, not the rendered video.

## The connect crash

### What crashes

With `streaming-video=all` and the guest already streaming when Ryll
connected, each of 0.15.2 (Debian 13's package), 0.16.0 and master
adeb620a04 crashed qemu in 20 of 20 connects. With the patch, master
crashed in 0 of 20, and the assembled series in 0 of 5. 0.16.0, from
2025-05-05, is the latest release, so no released spice-server has
the fix.

### Why

A new display client joins the channel's client list in `dcc_new()`,
but is given the streams that already exist only at the end of
`dcc_start()`. In between, spice-server reads the client's messages
synchronously while it waits for `SPICE_MSGC_DISPLAY_INIT`
(`display_channel_client_wait_for_init()`). A codec preference handled
there calls `video_stream_detach_and_stop()`, which queues a
`STREAM_CLIP` and a `STREAM_DESTROY` for every stream to every client,
the new one included. The new client's stream agents still have a NULL
display-client pointer, which only stream creation sets. When the first
`STREAM_CLIP` has been sent and released, its destructor dereferences
that pointer, and qemu dies.

The patch records in each stream agent whether the client has been
given the stream, as spice-server already does for surfaces. Clip and
destroy messages, and the stream's visible region, are only produced
for a client that has the stream, and the agent's display-client
pointer is set when the client is created rather than when a stream is.

### Which clients trigger it

Only the new client's own codec preference can do it, and only if it
arrives before `SPICE_MSGC_DISPLAY_INIT` or in the same read:

- **Ryll does.** It sends its preference at link-up, and spice-server's
  log shows the preference handled before the client's display
  encoders are created, in every connect.
- **spice-gtk does not, so remote-viewer does not.** spice-gtk's display
  channel sends only `SPICE_MSGC_DISPLAY_INIT`, and optionally a
  preferred image compression, when it comes up
  (`spice_display_channel_up()` in `src/channel-display.c`). It sends a
  codec preference only when the application calls
  `spice_display_channel_change_preferred_video_codec_types()`.
  remote-viewer (virt-viewer) never calls it; spice-gtk's test client,
  spicy, calls it from a menu, after connect.
- **Nothing else stops streams in that window.** spice-server's other
  callers of `video_stream_detach_and_stop()` (a codec change from the
  host application, migration) and its stream timeout all run on the
  display worker thread, which is blocked in the wait for
  `SPICE_MSGC_DISPLAY_INIT` until it ends. The client's preferred image
  compression only sets a field.

A preferred image compression from spice-gtk
(`--spice-preferred-compression`) is therefore harmless, and stock
remote-viewer users will not see this crash. Any client that states its
codec preference at connect will, on every connect to a streaming VM.

## The patches

Each was measured in one run of the rig with the builds interleaved
inside every pass, against master plus the crash fix. A difference
counts only when every repeat of one build sits on the same side of
every repeat of the other and the gap exceeds both the wider per-repeat
range and a floor (2 ms, 0.02 cores or 8 MiB). A null check, between two
builds expected to behave identically, flagged 1 of 15 cells, so a lone
single-measure flag is within what the rig produces by chance.

Stock qemu cannot exercise the frame-rate or hardware encoder patches.
Its non-GL display pushes damage on a fixed 30 ms timer, so no stream
exceeds about 33 fps, and it cuts damage into 32-pixel-wide columns,
narrower than this host's vah264enc accepts (at least 128x128). Those
two were measured on a qemu with the damage series from the re-baseline,
which sends each damaged region whole, here as one 640x480 stream, and
paces by `max-refresh-rate`.

### Hardware encoding on any GPU: kept

spice-server looked for hardware encoding plugins only when udev
reported an Intel GPU, although GStreamer's `va` plugin works on any
VA-API GPU. The series probes for them whenever the host has a DRM
render node, and falls back to the software encoder when a hardware
one cannot be built, started or encode, or cannot take the frame size.
It is two patches in the series: the fallback, then the broader probe.

On an AMD Radeon 780M (radeonsi, Mesa 25.0.7, GStreamer 1.26), with
Ryll asking for H.264, at 30 frames per second:

| Link | Activity | p50 ms, x264enc / vah264enc | p95 ms | qemu cores | qemu peak RSS MiB |
|---|---|---|---|---|---|
| 20 ms / 50 Mbit | busy | 28 / 31 | 46 / 46 | 0.15 / 0.13 | 222 / 278 |
| 20 ms / 50 Mbit | heavy | 33 / 34 | 54 / 50 | 0.16 / 0.13 | 240 / 297 |
| 80 ms / 10 Mbit | busy | 91 / 91 | 109 / 106 | 0.15 / 0.13 | 219 / 283 |
| 80 ms / 10 Mbit | heavy | 186 / 107 | 1262 / 275 | 0.15 / 0.13 | 239 / 295 |

- vah264enc engaged on every H.264 stream, read from spice-server's
  log, with no fallbacks.
- The CPU saving was measurable in three of the four cells.
- At 80 ms / 10 Mbit heavy, vah264enc's smaller output (5.5 against
  7.3 Mbit/s) also cut latency. Elsewhere latency did not change.
- The memory is the VA driver loaded into qemu. It is paid only once a
  GStreamer encoder is built: with MJPEG streams the series made no
  difference to qemu's RSS.

That cost is why the patch is kept only conditionally: a deployment
pays it only on a host with a usable GPU, and only when a client asks
for a GStreamer codec. With `/dev/dri` hidden from qemu, or holding a
render node that cannot be opened, streams were encoded by x264enc, as
were streams 32 pixels wide.

### Trace ring from 8 to 64: dropped

When a streamable drawable leaves the tree without a stream,
spice-server remembers it in an 8-entry ring, so that a successor of
the same shape carries on its frame count towards starting a stream.
Stock qemu sends up to 19 columns per update, more than the ring holds.
Raising it to 64 changed nothing measurable: 14 of 15 cells under stock
qemu with MJPEG showed no difference, and every build created the same
streams in every cell (21 per repeat with `all`, 19 with `filter`). The
two flags were single measures at the noise rate (peak RSS -8 MiB,
spread 6; an idle p50 +5 ms, spread 4, where nothing streams). The ring
is consulted only when many drawables leave the tree at once, and in
these runs that never delayed a stream.

### Frame-rate ceiling from 30 to 60: dropped

The patch worked as written: with qemu at `max-refresh-rate=60` and a
60 fps guest activity, streams were created at 60 fps instead of 30.
But latency, qemu CPU, bandwidth and display messages per second were
unchanged in all 12 cells, with H.264 and with MJPEG, and nothing
regressed on the slow links. The client received 4 to 12 display
messages a second for every build: spice-server's rate control, not
the ceiling, sets the delivered frame rate on these links.

## What upgrading 0.15.2 to master gains

Against Debian's 0.15.2 under stock qemu with MJPEG, master (with the
crash fix, which these runs never needed) helped in 9 of 15 cells,
showed nothing in 4 and regressed in 2. Plain master would measure the
same: the activity started after the connect, so the crash never
fired.

- **qemu CPU** fell 0.03 to 0.04 cores, about 20%, in five cells (both
  activities at 20 ms / 50 Mbit with `all`, busy with `filter` there,
  and busy at 80 ms with `all`).
- **With `streaming-video=all`**, p50 fell 12 to 18 ms and p95 21 to
  264 ms at 80 ms / 10 Mbit heavy, 80 ms / 5 Mbit and 150 ms / 2 Mbit
  busy. Display bandwidth fell to a third or less, for example 34.4 to
  9.8 Mbit/s at 20 ms / 50 Mbit heavy, with a third or fewer of the
  display messages. Image quality was not measured, so whether that is
  leaner encoding or fewer frames is open.
- **The regressions** were p50 +19 ms (spread 18) at 80 ms / 10 Mbit busy
  with `filter`, and p95 +83 ms (spread 71) at 150 ms / 2 Mbit heavy with
  `all`, a saturated cell whose p50 is 7.3 s.
- Idle showed nothing.

So upgrading is worth having for its own sake, but it does not make
`streaming-video=all` safe with clients that state a codec preference
at connect. No released spice-server has the fix.

## Caveats

- **One host and one GPU.** An AMD Ryzen 7 8845HS with a Radeon 780M,
  Debian 13, under a desktop session. Intel's msdk and vaapi paths are
  unchanged by the series and were not measured.
- **H.264 was not rendered.** Ryll does not decode spice-server's H.264
  (ryll#398), so in the H.264 rows the key-box latency is valid (the
  key box is a `DRAW_COPY`), but the stream reports that drive rate
  control describe streams the client failed to decode, the same for
  every build.
- **Small samples.** Three repeats of 40 presses per cell (two in one
  MJPEG run). At 150 ms / 2 Mbit heavy with `filter` too few repeats
  completed to judge.
- **The crash fix has no automated test.** Nothing in spice-server's
  test suite can link a display channel as a client; the evidence is
  the reproducer.

## What comes next

Nothing here has been sent or filed.

- **Send the series upstream.** It applies to spice-server master as of
  2026-10-29 and passes `meson test` after each patch. Until it lands
  and reaches distributions, `streaming-video=all` crashes qemu for
  clients that send their codec preference at connect.
- **H.265.** spice-server names it but rejects it, and Ryll cannot
  decode it, so it was not measured.
- **Rate control.** Delivered stream frames held at 4 to 12 a second
  whatever the frame-rate ceiling. That is the lead for any work on how
  quickly spice-server's bitrate ramps up.
- **Decode H.264 in Ryll** (ryll#398), so the H.264 rows can be
  re-measured with a client that draws them.
- **Have Ryll send its codec preference after
  `SPICE_MSGC_DISPLAY_INIT`.** The crash needs the preference to
  arrive before spice-server has handled the init message, so a Ryll
  that waits until then would not trigger it on any released
  spice-server.
