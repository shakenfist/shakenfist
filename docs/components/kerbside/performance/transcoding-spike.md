# Transcoding the display channel: the spike's verdict

Kerbside built an experimental
[transcoding display channel](/components/kerbside/proxy-architecture/#transcoding-the-display-channel-experimental)
to find out whether terminating and re-encoding the display channel
beats relaying it for clients on slow links. This page gives the
recommendation and argues it from three measurement pages, without
re-measuring anything:

- [spice-server's ACK window, observed from the proxy](/components/kerbside/performance/ack-window/);
- [the transcoding display channel on a shaped link](/components/kerbside/performance/transcoding-measurements/);
- [switching a display channel between relaying and transcoding](/components/kerbside/performance/transcoding-switching/).

[The streaming re-baseline](/components/kerbside/performance/streaming-rebaseline/) is the context
for all three, and [the SPICE performance plan](/components/kerbside/plans/PLAN-spice-performance/)
is where the work was scheduled. Written on 2026-10-28.

## Verdict: no-go

**Do not productise transcoding for SPICE clients.** It wins clearly on
one kind of case: incompressible motion, with qemu's streaming off, on
links of 10 Mbit or less. Relaying with qemu's
`streaming-video=all` is level with it or faster almost everywhere
else. That relay costs a small fraction of the CPU and needs no code,
only a hypervisor setting. The transcoder's other promised gains
either did not appear or come with a weaker relay. Better text during
motion did not appear. Moving between modes as the link changes works,
but only against a relay without GLZ or streams.

Leave the transcoder behind its build feature as an experiment, and
spend the effort instead on making `streaming-video=all` safe to
recommend. The obstacle there is spice-server 0.15.2's crash when a
client connects while a stream exists.
[What would change the answer](#what-would-change-the-answer) lists the
evidence that would reopen the question.

### The case for productising

- **It fixes the case it was built for.** With the heavy (pure noise)
  activity and streaming off, relaying's keypress p50 grows with the
  link's slowness, and transcoding paced on the socket does not:

  | Link | Relay, streaming off | Relay, streaming all | Transcode, socket |
  |---|---|---|---|
  | 80 ms / 10 Mbit | 2047 ms | 118 ms | 335 ms |
  | 80 ms / 5 Mbit | 4967 ms | 109 ms | 501 ms |
  | 150 ms / 2 Mbit | 13570 ms | 7328 ms | 1459 ms |

  At 150 ms / 2 Mbit it beats every configuration measured, streaming
  included.
- **Streaming off is what deployments run by default.** It is qemu's
  default, and the one setting that avoids the spice-server 0.15.2
  connect crash. That crash killed qemu twice during the measurements.
  Against relay with streaming off, transcoding wins or ties in most
  cells:
  - 38 against 116 ms (20 ms / 50 Mbit, busy);
  - 92 against 329 ms (20 ms / 50 Mbit, heavy);
  - 197 against 472 ms (80 ms / 50 Mbit, heavy);
  - 319 against 362 ms (80 ms / 5 Mbit, busy);
  - 688 against 831 ms (150 ms / 2 Mbit, busy);
  - level at 102 against 101 ms (80 ms / 50 Mbit, busy).

  It loses there only at 80 ms / 10 Mbit busy (244 against 192 ms).
- **It always ends with the exact picture.** Every transcoded repeat
  converged to the lossless reference once motion stopped. Relay with
  streaming off kept its JPEG with busy activity on every link of 80 ms
  or more, and on the 20 ms link when spice-server judged it slow
  (frozen-frame SSIM 0.9888, 43.9 dB). On the 2 Mbit link the transcoder settled in 8.2 s,
  where relay with streaming off took 17.5 s.
- **It moves work off the hypervisor.** qemu used 0.13 cores with the
  transcoder against 0.68 with relay and streaming off, for heavy
  activity on a LAN, probably because the transcoder asks spice-server
  for LZ4 and reads at LAN speed.
- **It needs no video codec on either leg.** It sends no streams, so it
  does not depend on the client decoding H.264 (ryll#398) or on any
  codec licence.

### The case against

- **The cheaper lever wins almost everywhere else.** Relay with
  `streaming-video=all` is level with transcoding for busy activity on
  the two 50 Mbit links. In every other cell from 20 ms / 50 Mbit to
  80 ms / 5 Mbit, and for busy activity at 150 ms / 2 Mbit, it is two
  to five times faster. For example:
  - 101 against 244 ms (80 ms / 10 Mbit, busy);
  - 98 against 197 ms (80 ms / 50 Mbit, heavy);
  - 112 against 319 ms (80 ms / 5 Mbit, busy);
  - 182 against 688 ms (150 ms / 2 Mbit, busy).
- **It costs the proxy far more CPU.** A transcoded session with busy or
  heavy activity uses 0.27 to 0.29 cores on a LAN. That is six times the
  relay with streaming off and over twenty times the relay with
  streaming on. A relayed display channel costs 0.001 to 0.07 cores.
- **The image-quality argument did not hold.** The plan argued
  transcoding on better text during motion, because `all` streams
  animated text lossily. During motion, the transcoder's JPEG reaches
  text next to the activity: a fringe up to 48 pixels wide, 53248
  pixels at about 28 dB. Streaming spoils a 16-pixel strip (7680
  pixels). Once motion stops both are exact.
- **The mode cannot follow the link to the best relay.** A switch
  works in both directions. But the relay that can take part has no
  GLZ and no streams, which is not the relay that beat transcoding. See
  [who decides](#relay-or-transcode-who-decides-and-when).
- **Productising is a large body of work,** listed under
  [what would change the answer](#what-would-change-the-answer). It
  includes a sandboxed decoder worker per session, because a
  transcoded backend leg skips the firewall.
- **The win is narrow, and nothing shows demand for it.** Against
  streaming relay, transcoding won one cell: heavy activity at
  150 ms / 2 Mbit. Against relay with streaming off, its clear wins
  are heavy activity on links of 10 Mbit or less. Kerbside has no
  evidence that its users run incompressible motion over those links.
  Even there, the transcoded p95 at 80 ms / 5 Mbit heavy was 2975 ms,
  from two repeats.

### Weighing them

The strongest argument for going ahead is the spice-server crash.
While `streaming-video=all` can crash qemu when a user reconnects to a
VM showing video, the setting that wins cannot be recommended without
a warning. A deployment that keeps streaming off has only the
transcoder to cut slow-link latency. But the crash can be fixed or
avoided far more cheaply than a transcoder can be productised. It
needs reproducing against spice-server's current master, then
reporting upstream or patching, with any patch carried in
kerbside-patches like the qemu damage series. That is a much smaller
job than the productising list below. And once the crash is gone, the
transcoder's remaining unique win is the heavy activity on the slowest
link.

In every `streaming-video=all` measurement here the streams were
MJPEG, the codec the Ryll used asks for first, and Ryll decodes MJPEG
(see [the spice-server patches](/components/kerbside/performance/spice-server-patches/#which-codec-the-streams-used)).
So spice-server's stream rate control ran on reports from a client
that drew the stream. A client that asks for H.264 might see different
contention, and Ryll does not yet decode spice-server's H.264
(ryll#398), so that is not measured (see [follow-ups](#follow-ups)).

## The evidence, by question

### Does spice-server's ACK window explain the slow-link backlog?

Yes, for the backlog, and no, for throughput. Observed from inside the
relay, spice-server's count of unacknowledged display messages peaked
at exactly 2W+1 (81 with a window of 40) on every run that reached the
limit, and never went higher. At 80 ms / 10 Mbit with heavy activity and
streaming off, that window's worth of 42 KiB draws is about 2.4 MiB
that has not reached the client, which at 9.3 Mbit/s is the 2.1 s
keypress latency. The claimed throughput cap on long, fast links is
refuted. At 80 ms / 50 Mbit the busy activity delivers 590 of the 635
messages a second the guest offers, and sits at the window's limit
only 36% of the time.

That leaves half of transcoding's original latency argument. The
backlog is real, but streaming removes it more cheaply: spice-server
drops stream frames while it waits for an ACK, and streams are small.
The same observation found that spice-server judges every 80 ms link
slow, so a relayed client at that distance already gets lossy JPEG
draws, at any bandwidth.

### Is transcoding faster on slow links?

Only on incompressible activity with streaming off, as tabulated
above. Pacing on the client socket beat pacing on a SET_ACK window of
40 wherever the link was the limit, by up to two times (531 against
244 ms at 80 ms / 10 Mbit busy, 382 against 197 ms at 80 ms / 50 Mbit
heavy). On an unshaped link, transcoding added nothing measurable to
latency while the host was quiet.

### Does the user see a better picture?

At rest, yes, against relay with streaming off when busy activity ran on
a slow link, and the same as relay with streaming on. During motion, no.
The transcoder works in 64-pixel tiles. A tile the activity only partly
covers fails the LZ4 test and goes as JPEG, text and all. At quality 85
that fringe stays readable but is visibly not the reference. Text
further from the activity is exact. The transcoder does this on every
link, the unshaped one included, because it chooses JPEG by how well a
tile compresses, not by the link. remote-viewer drew the transcoded
session correctly.

### Can a session move between modes?

Yes, with conditions that decide the design. In the final runs all 56
switches completed, 28 each way, with remote-viewer and Ryll. The
proxy had the new mode running 22 to 37 ms after a switch on a LAN.
The user pays one full-screen redraw: on a LAN that is a frame or two,
and at 80 ms / 10 Mbit the screen was stale for 1.3 s (to transcode)
or 0.97 s (to relay). remote-viewer showed black instead of the old
picture for most of that, for reasons not established. Two problems
had to be removed first, and neither was in the proxy:

- GLZ does not survive a switch. remote-viewer died of a division by
  zero in its GLZ window about 4 s after each transcode-to-relay
  switch, in 3 runs of 3, so the switching relay is kept off GLZ.
- Ryll does not restart its ACK count on `SET_ACK`. spice-server's
  unsigned count then wraps, and Ryll froze after every switch to
  relay, receiving 27 to 132 messages in 6 s where 3800 is normal. A
  one-line Ryll change fixes it.

## CPU and memory per session

| Measure | Transcoded | Relayed |
|---|---|---|
| Proxy cores, LAN, busy or heavy | 0.27 to 0.29 | 0.046 to 0.066 (streaming off), 0.011 to 0.025 (streaming all) |
| Proxy cores, 80 ms / 10 Mbit | 0.206 (busy), 0.064 (heavy) | 0.002 to 0.011 |
| Proxy cores, 2 to 5 Mbit links | 0.04 to 0.11 | 0.001 to 0.010 |
| qemu cores, LAN, heavy | 0.13 | 0.68 (streaming off) |
| Proxy peak RSS | 29 MiB idle, 33 to 51 MiB active | 11 MiB |
| Renderer pixmap cache peak | 0 bytes in every case | not applicable |

The transcoder's CPU follows the frames it sends. On a LAN it sends
every frame the guest draws. On a slow link the socket holds it back
and damage merges in the mirror, so it costs less. The figures are for
a 640x480 activity at 30 fps on a 1280x800 guest. A larger moving area
would cost more, probably in proportion to the pixels changed, but
that was not measured. The cost moves from the hypervisor to the
Kerbside node: qemu works less, probably because it sends LZ4 at LAN
speed instead of choosing QUIC or JPEG for a slow client. Kerbside
nodes have never needed CPU capacity planning, because a relayed
session costs almost nothing. A transcoding node
would.

Memory is modest. The mirror and the shadow are 4 MiB each at
1280x800. The renderer's pixmap cache, capped at 256 MiB, was never
used: qemu's display path never asks the client to cache an image.

## Relay or transcode: who decides, and when

The plan asked who chooses between the two modes, and when, and
whether a session could move back to relay once Kerbside owned the
client's cache state.

- **When: once, at connect.** The proxy can switch a live display
  channel cheaply, but the relay that can take part is a weak one. The
  GLZ window outlives a display channel in spice-gtk, and no protocol
  message resets it, so GLZ is ruled out of a switching relay for
  good. Streams are ruled out too, because spice-server 0.15.2 crashes
  when a display channel connects while streams exist, which every
  switch does. A mode that follows the link would choose between
  transcoding and a relay without GLZ or streams, at the price of a
  full redraw per switch, and only with clients that restart their ACK
  count on `SET_ACK`. Choosing once keeps both modes whole.
- **Not always-transcode.** The near-lossless LAN mode the plan
  imagined exists: on a quiet LAN the transcoder adds no measurable
  latency. But it costs 0.27 to 0.29 cores per busy session, against
  0.011 to 0.066 for the relay, for nothing the user sees.
- **Who: today, nobody needs to.** With the no-go every session is
  relayed, and the choice that matters is qemu's `streaming-video`,
  which the operator makes per hypervisor. If transcoding is ever
  productised, the decision belongs to operator policy per source,
  carried to the proxy in the authorisation reply and applied when the
  display channel connects. One signal is already in hand at that
  point: the main channel's bandwidth test is relayed end to end, and
  spice-server's verdict shows in the display channel's window (20 or
  40). It is coarse, because spice-server calls every 80 ms link slow.

## What the spike's copy of andris's pipeline missed

The transcoder's update pipeline (the shadow, the tile diff, lossy
tracking and the encode classifier) is copied from
[andris](https://github.com/shakenfist/andris) at commit e29929fa88,
as each copied file's header says. Checked on 2026-10-28 against
andris develop 568a43bfc0, after `git fetch origin` in an andris
clone:

- **No drift.** `git log e29929fa88..origin/develop` lists 14 commits,
  and none touches `andris/src/server/updates/`,
  `andris/src/platform/mod.rs` or `andris/src/platform/damage.rs`.
  `git diff --stat` over the same range changes nothing under
  `andris/src/` at all: the 14 are documentation, CI and review
  tooling. No remote andris branch has a change to those files either.
- **The copy is what its headers say.** `diff -u` of each copied
  `updates/` file against andris develop shows only the changes the
  headers list. Those are module paths, let-chains rewritten for
  edition 2021, and the parts not copied. Every function in
  `geometry.rs` exists in andris's two platform files.

What the copy left out on purpose, and what that cost:

- **The video stream path (`heat.rs`, `stream.rs`).** This is the
  biggest omission, and it is what the transcoder lost on. Andris finds
  tiles that keep changing and look photographic, and sends that area
  as a video stream. Without it, the spike's transcoder sends every
  frame of an animation as JPEG or LZ4 draws. So the measurements
  compared a transcoder with no stream path against a relay with one,
  and the relay won wherever the activity was smooth enough to stream
  well. The spike could not have taken the path as it is. Andris
  streams only H.264 through openh264, which is the licensing question
  the plan raised. It also stamps frames with multimedia time, which
  brings back the client clock problem that made the spike start with
  draws.
- **The scrub (`UpdateEncoder::differences`, `Shadow::compare`).**
  Andris re-reads the screen to catch damage X11 did not report. Ryll's
  renderer records the damage of every draw it decodes, so nothing was
  missed here.
- **`LossyTiles::mark` and `make_due`,** which only streams use.

Two findings flow back the other way, to andris:

- Andris paces on a SPICE ACK window of 20 (`ACK_WINDOW` in
  `andris/src/server/channel.rs`). The spike measured socket
  pacing beating an ACK window of 40 by up to two times wherever the
  link was the limit. A narrower window would only add round trips.
- The JPEG fringe is andris's behaviour too. Andris classifies merged
  rectangles of 64-pixel tiles, so text in a tile that moving content
  only partly covers goes as JPEG. The plan's risk that andris's
  thresholds, tuned on X11 desktops, would not suit a guest's console
  text showed only there; text elsewhere stayed exact.

Because the verdict is no-go, the copy is not extracted into a shared
crate. Andris's copy stays the maintained one, and Kerbside's stays
frozen at e29929fa88 for as long as the experiment is kept.

## What this means for a Rust SPICE server on qemu's D-Bus display

The plan's later spike asks whether a Rust SPICE server on qemu's
D-Bus display is a better foundation than spice-server. It was to
reuse this spike's output half if this spike said go. It need not
wait for that, because the output half already exists in andris, a
Rust SPICE server that exports an X11 desktop. Andris has the update
pipeline measured here, plus the stream path, an agent, audio,
inputs and cursor. The helper that spike describes is andris with a
D-Bus input instead of an X11 one, and the plan already says to start
there.

This spike is the first measurement of andris's pipeline fed by qemu's
display through spice-server rather than by X11 damage. It re-encodes
correctly and converges to the exact picture. remote-viewer draws it.
It costs about a quarter of a core for a 640x480 area at 30 fps. The
D-Bus spike can carry over three lessons: pace on the socket, fix the
tile fringe, and expect its stream path to decide the result on
animated content. The no-go here does not weaken the D-Bus spike. Its
case rests on qemu's damage path and spice-server's ticketing, not on
slow links. Kerbside would stay in front of such a helper as a relay,
which is the mode this page recommends.

## RDP out

The plan's *RDP out* item asks whether renderer accuracy and CPU per
session would support moving ryll's RDP output into Kerbside.

- **Renderer accuracy is good enough for virtio-gpu and std-vga
  guests.** Ryll's renderer decoded spice-server's lossless draws
  exactly: every transcoded repeat converged to the lossless
  reference. It is not good enough for QXL guests.
  ROP3, stroke, text and composite draws, palette and surface images,
  and off-screen surfaces are unimplemented, and the transcoder can
  only count and log them, leaving a stale region. An RDP gateway
  would inherit the same limit.
- **CPU per session is acceptable for an opt-in gateway, not for a
  default.** Decoding and re-encoding cost about a quarter of a core
  per busy session on a LAN. RDP's own encoder would cost more or less
  than this spike's JPEG and LZ4, which was not measured. That cost
  rules out transcoding every SPICE session. RDP out is different: its
  case is reachability for clients that do not speak SPICE, and the
  client's protocol makes the choice at connect. It would still make
  CPU a constraint on Kerbside nodes for the first time, so it needs
  capacity planning per node.
- **The no-go does not block RDP out, but RDP out inherits everything
  productising needs** (below). That includes the sandboxed worker,
  because an RDP gateway decodes the same guest-influenced data. It
  also terminates every channel, not only display.

## Does a productised transcoder need its own CI lane?

No separate lane. The `transcode` feature is kept out of the release
build because its compression dependency's `encode` feature builds
openh264 from source. A productised transcoder would first split that feature
(ryll#499), so that the JPEG and LZ4 encoders build without openh264.
It would then be in the release wheel, either as a default feature or
enabled by `tools/build-proxy-wheel.sh`. The existing `Rust proxy`
workflow job would then build, lint and test it with no new lane and
no new gate job.

While the experiment stays in the tree unproductised, the existing
job also runs clippy and the tests with `--features transcode`, so a
ryll pin bump cannot break it unnoticed. Those are steps, not a new
job, so they rename no required check. Their cost is building openh264
on a cache miss, which the job's cargo cache otherwise keeps.

## What would change the answer

Any of these would reopen the question:

- **`streaming-video=all` proves undeployable.** The spice-server
  crash might reproduce against current master and resist a fix or
  workaround. Or operators might be unable to set qemu's streaming
  option. Either way, deployments would be left on streaming off,
  where the transcoder wins or ties in most cells.
- **Users turn out to need very slow links with heavy motion.**
  Evidence that Kerbside's users run incompressible motion over links
  like 150 ms / 2 Mbit, the one case where the transcoder beat
  streaming relay (1459 against 7328 ms).
- **An H.264 client changes the streaming numbers.** The measurements
  streamed MJPEG. If relay with `streaming-video=all` is markedly slower
  with a client that asks for H.264 and decodes it, the comparison above
  has to be redone.
- **A transcoder with a stream path ties streaming relay.** Suppose
  andris's stream path came across with a codec that needs no
  licensing decision (MJPEG or VP8), and the client clock problem
  were solved. If that version matched relay with streaming on in
  latency, it would give streaming-class latency to deployments that
  keep qemu's streaming off, so no stream would ever reach
  spice-server's crash. The spike did not build this.
- **Kerbside has to terminate every channel anyway.** A browser
  client (WebRTC out) or RDP out pays for decoding and re-encoding by
  definition, and once those costs exist, SPICE-out reuses them.

Productising would then need, in order:

1. extract andris's update pipeline into a crate both projects use,
   deleting Kerbside's copy;
2. split the compression crate's `encode` feature so the image
   encoders build without openh264 (ryll#499);
3. run the transcoder in one sandboxed worker process per session. A
   transcoded backend leg skips the relay's L0 and L1 inspection and
   its 16 MiB framing cap, so ryll's renderer limits are all that
   bound guest-influenced decode;
4. carry the mode per session in the authorisation reply, chosen by
   operator policy per source, instead of a process-wide flag;
5. plan capacity per node, including what a saturated node does
   (refuse the session or relay it) and how a multi-node deployment
   places sessions;
6. answer the out-of-scope list: several display channels, remote
   `gl=on`, QXL off-screen surfaces and unimplemented draws, seamless
   migration, and how the L0 size and rate caps apply to each leg;
7. decide codec licensing before any stream output that uses H.264.

## Follow-ups

Nothing here has been filed.

- **Ryll: restart the ACK count on `SET_ACK`.** Ryll counts ACKs from
  the last one it sent, so when a display channel reconnects within a
  session its first ACK comes early and the channel freezes. A fresh
  session never shows it. The fix is one line in the `SET_ACK`
  handler, `self.last_ack = self.message_count.saturating_sub(1);`,
  which counts the window as spice-gtk and spice-server do. It was
  confirmed on ryll 0d140b3. Ryll develop at e49ddce, as last fetched
  locally, still lacks it in both `channels/display.rs` and
  `channels/cursor.rs`; `channels/playback.rs` already resets its
  count.
- **Ryll: the client stall.** In 18 of 184 measured repeats Ryll
  stopped reading its display socket, in relay and transcode alike.
  The cause is not established. One candidate is the control socket's
  stream of `surface_drawn` events.
- **Reproduce spice-server 0.15.2's connect crash against current
  master,** then report or patch it. Done: it reproduces on 0.16.0
  and master, only for clients that send their codec preference at
  connect (Ryll, not remote-viewer), and a patch is ready to send
  ([spice-server patches](/components/kerbside/performance/spice-server-patches/)).
- **Measure `streaming-video=all` with a client that decodes H.264.**
  The measurements here streamed MJPEG, which Ryll decodes; Ryll does
  not yet decode spice-server's H.264 (ryll#398).
- **Recommend a `streaming-video` setting to operators,** stating the
  crash, once the two items above are settled.
- **Andris: socket pacing, and the JPEG fringe** at the edges of
  moving content, as described above.
- **remote-viewer's black display** on a switch to transcode was not
  explained. It matters only if switching is ever revisited.
