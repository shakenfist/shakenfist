# Switching a display channel between relaying and transcoding

This page tests whether a live session's display channel can move between
relaying and the experimental
[transcoding display channel](/components/kerbside/proxy-architecture/#transcoding-the-display-channel-experimental)
while it runs, and what a move costs. It follows
[the transcoding measurements](/components/kerbside/performance/transcoding-measurements/), on the same
guest and link profiles, and
[the spike's verdict](/components/kerbside/performance/transcoding-spike/) draws on both. Measured on
2026-10-28.

## Summary

- **A switch works in both directions, for both clients, once two
  problems are out of the way, and neither problem is in the proxy.** In
  the final runs 56 switches all completed: 28 each way, with
  remote-viewer and Ryll, on an unshaped link and at 80 ms / 10 Mbit.
- **Transcode to relay needs the relay to avoid GLZ.** Each new backend
  display channel starts a fresh GLZ dictionary whose image ids start
  again at 0, while the client's GLZ window still holds the old channel's
  images. remote-viewer (spice-gtk 0.42) then grew its window on every
  image until the size overflowed, and died of a division by zero in
  `decode-glz.c`, about 4 s into the new relay spell, in 3 runs of 3. So
  a switching channel's relay asks spice-server for the client's
  compression with GLZ swapped for LZ, or for `auto-lz` if the client
  asked for nothing.
- **Transcode to relay also needs a client that restarts its ACK count on
  `SET_ACK`, and Ryll does not.** Ryll 0d140b3 counts ACKs from the last
  one it sent, not from the new channel's `SET_ACK`, so its first ACK
  comes early. spice-server's count of unacknowledged messages is
  unsigned and wraps below zero, and the channel then never sends again:
  Ryll froze on the first frame of every relay spell after the first,
  receiving 27 to 132 messages in 6 s where 3800 is normal. A one-line
  change to Ryll (restart the count at `SET_ACK`, as spice-gtk does)
  fixes it; with it, Ryll switched cleanly both ways.
- **On a LAN a switch costs one or two frames.** The proxy has the new
  mode running 22 to 37 ms after it stops the old one. Ryll's longest gap
  between draws across a switch was a median of 56 ms against a steady
  11 to 25 ms. remote-viewer's picture, captured at 30 fps, never paused
  longer than it does in steady state (67 ms), but it showed one black
  frame on 5 of 8 switches from relay to transcode.
- **On a slow link a switch costs one full-screen redraw.** At 80 ms /
  10 Mbit the picture was stale for 1.3 s on every switch to transcode
  and 0.97 s on every switch to relay: the time to send the whole
  1280x800 screen, which each new mode starts with. remote-viewer showed
  a black display for most of that 1.3 s instead of the old picture. Its
  cause is not established; it is not the transcoder's `SURFACE_CREATE`,
  `MONITORS_CONFIG` or reset messages (see [remote-viewer goes
  black](#remote-viewer-goes-black)).
- **The "duplicate channel" race never appeared.** spice-server refuses a
  display channel that arrives while the session's previous one is still
  closing, but in all 164 switches made, 56 of them in the final runs,
  the first dial was accepted every time. The proxy dials again after a
  refusal, which a test with a fake server covers.
- **So the mode could follow the link, but only between transcoding and
  a relay without GLZ or video streams, with clients fixed, and at the
  price of a full redraw per switch.** The configuration the
  measurements found best on most slow links, relaying with
  `streaming-video=all`, cannot take part: GLZ and streams both have to
  go, and spice-server 0.15.2 crashes when a display channel connects
  while streams exist, which every switch does. Choosing the mode once,
  at connect, avoids all of this.

## How a switch works

The hidden flag `--transcode-switch-after-secs N`, in builds with the
`transcode` feature, makes each session's first display channel switch
mode N seconds after it starts, and again each time N seconds pass in the
new mode. It starts transcoded with `--transcode-display` and relayed
without. It is an experiment trigger, not a product interface.

The client leg stays open throughout; the backend display channel is
closed and dialled again for the other mode. Both modes dial it with the
transcoder's capabilities: LZ4, and `MULTI_CODEC` with no codec, so
spice-server never streams to either. A relay spell therefore offers
spice-server the transcoder's capabilities, not the client's, and does not
warn about the difference. It assumes a client that can take LZ4 and
`MONITORS_CONFIG`, as remote-viewer and current Ryll can; a client
without them, such as Ryll 0.1.5 or earlier, which cannot decode LZ4,
could be sent what it never offered.

- **Relay to transcode.** The relay ends when its backend leg reads as
  closed, which its server-to-client pump sees only between messages, so
  the client never holds part of a message. The transcoder skips waiting
  for `DISPLAY_INIT`, which the client sent once at the start, and its
  first frame sends `STREAM_DESTROY_ALL`, `INVAL_ALL_PIXMAPS` and
  `INVAL_ALL_PALETTES`, then the whole picture losslessly. If the client's
  surface (from spice-server, as the proxy saw it written) is already the
  right size, the picture is drawn over it without a new
  `SURFACE_CREATE` or `MONITORS_CONFIG`.
- **Transcode to relay.** The transcoder stops between frames. The new
  backend channel waits for a `DISPLAY_INIT`, so the proxy replays the
  client's, with its compression preference minus GLZ, and treats the
  first bytes back as acceptance. spice-server then sends its own start
  sequence (`SET_ACK`, a surface, the whole picture, `MARK`), which the
  relay forwards; spice-gtk reuses its existing surface of the same size.
- **Refusals.** spice-server closes a refused channel straight after the
  link. A relay sees that as end of file before anything arrives, and a
  transcoder as its renderer ending before the client was sent anything.
  Either way the proxy waits 50 ms times the number of refusals so far
  and dials again, up to 10 times.

## Results

Each case ran one session for 50 s with a switch every 6 s, eight
switches, on the shaped-link rig's busy activity (a 640x480 gradient
with noise at 30 fps) and qemu `streaming-video=off`. "Ryll, fixed" is
Ryll 0d140b3 with the ACK change; "Ryll" is the same without it.

- **Proxy ready** is from the proxy logging the switch to the new mode
  producing: the transcoder handing the writer its first frame, or the
  re-dialled backend's first bytes reaching the relay. It includes
  stopping the old mode (0 ms on a LAN, up to 63 ms on the slow link,
  where the relay can be waiting to write to the client) and dialling.
- **Longest gap** is the longest interval without a change in the
  client's picture from 0.5 s before the switch to 5 s after it. For
  Ryll a change is a `surface_drawn` event from its control socket. For
  remote-viewer it is a captured frame of the activity area, at 30 fps,
  that differs from the one before.
- **Steady p95** is the 95th percentile of the same interval over the
  3 s before the switch, for comparison.

Medians, with the range in brackets, over the switches in each
direction.

| Client | Link | To | Switches | Proxy ready ms | Longest gap ms | Steady p95 ms |
|---|---|---|---|---|---|---|
| remote-viewer | unshaped | transcode | 8 | 27 (25-37) | 67 (67-67) | 67 |
|  |  | relay | 8 | 25 (23-32) | 67 (67-67) | 67 |
| Ryll, fixed | unshaped | transcode | 8 | 27 (22-34) | 56 (54-73) | 11 |
|  |  | relay | 8 | 26 (24-34) | 55 (53-81) | 25 |
| Ryll | unshaped | transcode | 4 | 31 (26-37) | 57 (55-63) | 12 |
|  |  | relay | 4 | 28 (26-30) | frozen | 25 |
| remote-viewer | 80 ms / 10 Mbit | transcode | 4 | 26 (23-33) | 1300 (1300-1300) | 34-67 |
|  |  | relay | 4 | 60 (52-72) | 967 (966-967) | 67 |
| Ryll, fixed | 80 ms / 10 Mbit | transcode | 4 | 27 (22-39) | 1313 (1312-1315) | 23 |
|  |  | relay | 4 | 55 (33-56) | 965 (963-967) | 50 |

Stock Ryll's gaps on switching to relay (55 to 87 ms) are the first
frame arriving; after it nothing more did, so its picture was frozen for
the rest of each relay spell. Half the unshaped cases started relayed and
half transcoded; the start made no difference. No dial was refused in any
case.

On an unshaped link remote-viewer's gap is the capture's own: a 30 fps
capture of a 30 fps animation shows a repeated frame now and then in
steady state too, so a switch was not visible as a pause. On the slow
link both clients waited the same time, which is the link's: the
transcoder's lossless picture of the whole screen, then spice-server's.

### Transcode to relay with GLZ

Before the relay was kept off GLZ, a switching session relayed whatever
compression the client asked for: spice-server's default `auto-glz` for
remote-viewer, and the same from Ryll's own preference. remote-viewer
received about 1960 to 1980 messages of the first relay spell after a
transcode spell, about 4 s, then died of `SIGFPE`. A backtrace with
spice-gtk's debug symbols puts it in `glz_decoder_window_resize`
(`decode-glz.c`, at `id % (w->nimages * 2)`), called from
`canvas_get_image_internal` for a `DRAW_COPY`. The window cannot release
images below its `oldest` id, which stays where the old channel left it,
so each new image with a low id lands on an occupied slot and doubles the
window, until the `uint32_t` size overflows to 0. It crashed in all three
runs that tried it. Ryll's freeze happened with or without GLZ, so it is
a separate problem.

### Ryll's ACK count

In a relay spell after a transcode spell, the proxy's ACK-window
observation showed Ryll sending its first ACK after 6 messages of a
window of 20, then spice-server sending nothing more. spice-server
subtracts a window from its count of unacknowledged messages for each
ACK (`red-channel-client.cpp`, `messages_window -= client_window`), and
the count is unsigned, so an early ACK wraps it to about 4 billion and the
channel waits for ACKs forever. Ryll sends an ACK when
`message_count - last_ack` reaches the window, and `SET_ACK` resets
neither, so after a switch its count carries over from messages the
transcoder sent. A fresh session never shows this, because `SET_ACK` is
the first message. Setting `last_ack` to the message before `SET_ACK`
when one arrives, which matches spice-server's count, made every relay
spell carry its normal 3800 messages.

### remote-viewer goes black

On every switch to transcode at 80 ms / 10 Mbit, remote-viewer showed a
black display from about 150 to 250 ms after the switch, when the
transcoder's first frame starts to arrive, until that frame had all
arrived, about 1.3 s later; then the full picture appeared at once. On
the unshaped link the same thing lasts one captured frame. It does not
happen on switches to relay. spice-gtk's debug log shows no surface
change and no `MARK` going false in that time. Removing the transcoder's
`SURFACE_CREATE` and `MONITORS_CONFIG` (as the transcoder now does when
the client's surface is already the right size), or any of the three
reset messages, did not change it. Its cause is not established. Ryll's
draw events cannot show a black frame, so whether Ryll does anything
similar is not known.

## What this means

- The proxy side of a switch is cheap and reliable: tens of
  milliseconds, no refusal seen, and the client never sees a broken
  message stream.
- The cost the user sees is a full-screen redraw per switch. On a LAN
  that is a frame or two. On a 10 Mbit link it is about a second of stale
  (or, in remote-viewer, black) screen, and a link slow enough to want
  the switch makes it longer.
- Both client failures come from state that outlives a display channel:
  the GLZ window, which belongs to the session in spice-gtk, and the ACK
  count. Neither can be reset by a protocol message: no message resets
  the client's GLZ window, and `SET_ACK` resets the ACK count only in
  clients that implement it that way. Every client a deployment meets
  would need checking, and spice-gtk's GLZ behaviour rules GLZ out of the
  relay for good.
- The relay that can take part is therefore a weaker one than the
  measurements' best: no GLZ (whose cross-image dictionary is what makes
  relaying text-heavy screens cheap on a slow link) and no video streams
  (the configuration that beat transcoding on most slow links). If the
  mode is to follow the link, it would choose between transcoding and
  that relay, not between transcoding and the best relay. Choosing once
  at connect keeps both options whole.

## Caveats

- The final binary differs from the one that showed the GLZ crash only
  in keeping the relay off GLZ and in drawing over a surface of the right
  size; the crash runs used the earlier one.
- Few switches per cell: four or eight. The proxy timings were
  consistent; the slow-link gaps varied by under 5 ms.
- remote-viewer ran inside the rig's client namespace on the host's X
  desktop at `--zoom=80`, and was captured from the root window, so a
  window covering it would have spoiled the capture. Only the activity
  area was compared, so the desktop's clock and load graph do not count.
- The guest is the rig's keydraw guest on virtio-vga, which spice-server
  serves as bitmap draws on the primary surface. A QXL guest, an
  off-screen surface or a second display was not tried.
- Switching with `streaming-video` on was not tried, because of the
  spice-server 0.15.2 crash above.

## Procedure

The topology is the shaped-link rig's (`tools/shaped-link/run-matrix.sh`,
described in [Testing](/components/kerbside/testing/)), driven by a one-case script kept
outside the tree: qemu with the keydraw guest (`kd.fps=30 kd.noise=3`,
`streaming-video=off`), the mock control plane and the proxy in a user
and network namespace, and the client in a nested namespace across a
veth pair shaped with netem. The proxy ran with
`--transcode-switch-after-secs 6`, plus `--transcode-display` for the
cases that start transcoded.

- **Ryll** ran headless with its control socket. A small script
  subscribed to `surface_drawn` and recorded each event's wall-clock time,
  and took screenshots to check the picture after switches.
- **remote-viewer** ran with `--zoom=80` so its window fitted the screen.
  ffmpeg captured the window's region of the root window at 30 fps
  (`-f x11grab -framerate 30 -copyts -c:v ffv1`), from outside the
  namespaces, because x11grab's shared-memory transfer fails from inside
  the user namespace. Frames were cropped to the activity area and
  compared with the one before, and frames with a mean below 8 counted
  as black.
- Switch times come from the proxy's info log: `switching the display
  channel`, `transcoding display channel stopped between frames` or
  `relay ended`, `dialled the backend display channel for the switch`,
  then `sending the client a new primary surface` or `the backend accepted
  the new display channel`. Refusals log `spice-server refused the new
  display channel`.
- The fixed Ryll is 0d140b3 with the rig's rect patch and this change to
  `shakenfist-spice-renderer/src/channels/display.rs`, in the `SET_ACK`
  handler: `self.last_ack = self.message_count.saturating_sub(1);`.
- The GLZ backtrace used spice-gtk's debug file from
  debuginfod.debian.net, matched by build id, and `addr2line`.

### Measured

- kerbside-proxy: this change on top of 16e6af8, release build with the
  `transcode` feature.
- Ryll 0d140b3 with `ryll-surface-drawn-rect.patch`, with and without
  the ACK change.
- remote-viewer 11.0 with spice-gtk 0.42 (Debian 0.42-3).
- Debian qemu 10.0.13 (libspice-server 0.15.2), KVM.
- Host: the same as [the transcoding measurements](/components/kerbside/performance/transcoding-measurements/#measured),
  with a one-minute load average of 1 to 3 during the final runs.
