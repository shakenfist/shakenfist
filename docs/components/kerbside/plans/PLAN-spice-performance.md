# SPICE session performance, in Kerbside and upstream

## Prompt

Before acting on this plan, read the kerbside proxy relay
(`rust/kerbside-proxy/src/relay.rs`, `listen.rs`,
`backend.rs`), the latency loadtest (`loadtests/latency/`)
and the direct-qemu harness (`docs/direct-qemu-harness.md`).
The upstream claims below cite the reference clones under
`/srv/src-reference/` (qemu, spice, libvirt, the kernel) at
the commits named in the Situation section. Re-check a
citation before you build on it: these trees move, and a
line number is only a pointer. Ground every claim in the
code rather than in this document, and flag uncertainty
rather than guessing.

## Situation

On 2026-09-23 we asked: if we could change anything in qemu,
spice-server, libvirt or the Linux kernel, what would most
improve the performance or quality of SPICE sessions for
Kerbside and Ryll? Four research agents surveyed the display
pipeline, the transport and authentication model, the kernel,
and the pain points already recorded in Kerbside, Ryll and
kerbside-patches. The sources read were qemu 30e8a06b64
(2026-06-29), spice 91d42c4d (2026-06-30) and linux
c537e12daeec. The management session spot-checked the main
claims against source. What follows is the result, ranked.

### The findings that shape this plan

1. **Kerbside hides congestion from spice-server.** The proxy
   sets only `TCP_NODELAY` and keepalive
   (`rust/kerbside-proxy/src/listen.rs:100,169`). The relay
   pump reads, forwards each message with `write_all` and
   flushes (`relay.rs:211-352`). Backpressure therefore reaches
   spice-server only after two things fill: the client leg's
   autotuned send buffer and the backend leg's receive buffer.
   Both can reach megabytes, which is seconds of stale frames
   at WAN rates. spice-server's own defences, the channel
   "blocked" state and pipe frame dropping
   (`red-channel-client.cpp:665,685`, `video-stream.cpp:354`),
   rely on its socket stalling, and the proxy prevents that.
   The kernel already has the fix, `TCP_NOTSENT_LOWAT` plus
   capped socket buffers, through `socket2`, which we already
   depend on. No upstream change is needed.
2. **qemu's non-GL SPICE display path defeats spice-server's
   video detection.** This path serves virtio-gpu, std-vga and
   qxl in VGA mode.
   - `ui/spice-display.c:375-391` unions all damage into a
     single bounding box.
   - `qemu_spice_create_update` (`:194-258`) diffs that box
     against a mirror in 32-pixel columns and emits one
     `DRAW_COPY` per column run.
   - Each update is copied twice.
   - Updates wait for a fixed 30 ms timer
     (`include/ui/console.h:39`).
   - spice-server starts a stream only after 20 consecutive
     same-geometry copies of at least 96x96
     (`server/video-stream.h:32-38`). Column-shaped drawables
     rarely qualify, so video on virtio-gpu falls back to
     bitmaps. That explains Ryll's unmeasured OPEN-QUESTIONS Q3
     and undercuts "move off QXL" as a standalone answer. The
     move is still right, because QXL's command ring exhaustion
     and resolution cliff are structural, but it only pays off
     once this path is fixed.
3. **spice-server's authentication model is the main
   constraint on brokering.**
   - There is one password per VM (`reds.cpp:3931-3961`).
   - It is re-checked with `strcmp` against one global expiry
     on every channel link (`reds.cpp:2087-2110`).
   - It is carried in RSA-1024 OAEP with SHA-1 and limited to
     60 bytes.

   Minting a second ticket revokes the first viewer.
   `PLAN-proxmox-source.md` meets this directly, and it puts a
   ticket lifetime floor under every late-opened channel. Fixing
   it upstream means spice-server, qemu QMP, libvirt and every
   platform adopting the change: years.
4. **qemu already offers a supported out-of-process display
   interface, and there is precedent for a Rust server on it.**
   - `-display dbus` (`ui/dbus-display1.xml`) exports:
     - scanout, and per-rectangle `Update`s;
     - shared-memory `ScanoutMap`/`UpdateMap`, and DMABUF;
     - cursor, keyboard, mouse and multitouch;
     - clipboard;
     - audio in and out;
     - chardevs, which covers vdagent and usbredir.
   - libvirt supports `<graphics type='dbus'/>` (since 8.4).
     Since 11.1 it launches an external Rust `qemu-rdp` helper
     against it (`docs/formatdomain.rst:6898`).
   - A SPICE server built the same way, as a per-VM helper
     reusing Ryll's `shakenfist-spice-protocol` server
     primitives, would sidestep findings 2 and 3 together:
     - it takes damage without going through
       `spice-display.c`;
     - it chooses its own codecs, including hardware H.264
       from the dmabuf;
     - it defines its own ticketing.
   - qemu's SPICE modules (`ui/meson.build:176-188`) are not an
     alternative plug point. `util/module.c:176-186` refuses
     modules from any other build.
   - A drop-in `libspice-server.so` is possible, since it is a
     stable ABI of about 99 symbols. It would still receive
     drawables that qemu has already mangled in finding 2.

### What the first measurements changed

The findings above are the research as written on 2026-09-23.
Measurement the same day overturned two of them and sharpened a
third. They are left in place so that the correction stays
visible.

- **Finding 1 does not hold. Phase 1's result is null.** At
  80 ms / 10 Mbit with a busy display, a keypress takes about
  2.1 s to draw, so the symptom is real. But capping the
  proxy's buffers made no measurable difference to latency or
  throughput. The reason is spice-server's per-channel ACK
  window: it stops sending once twice its window (20 messages,
  or 40 in low-bandwidth mode) is unacknowledged
  (`red-channel-client.cpp:636-641,1142`). The acknowledgements
  come end to end from the client, so the proxy's buffers only
  change *where* the roughly 2.5 MiB backlog sits (proxy or
  qemu), not how large it is. Frame dropping also engages while
  waiting for an ACK, so no socket stall is needed for it. The
  window also caps throughput on high-BDP links: 80 ms / 50 Mbit
  carried only 17.9 Mbit/s of an activity that fills the link at
  20 ms. The lever that could matter is the size of that window:
  byte- or RTT-aware ACK pacing, either in Kerbside (which
  relays the client's ACKs) or upstream. See
  `docs/performance/proxy-backpressure.md`.
- **Finding 2 was right about fragmentation and wrong about
  its shape.**
  - `RED_STREAM_MIN_SIZE` is an *area* (96x96 pixels), not a
    per-dimension minimum. So qemu's 32x360 columns do become
    streams, just 18-19 separate 32-pixel-wide ones rather than
    none.
  - qemu also defaults to `streaming-video=off`
    (`ui/spice-core.c:811`). Out of the box nothing streams, so
    deployments should set `filter`.
  - The phase 2 prototype replaced those 19 slivers with a single
    480x360 stream. It cut display bytes about 4x, halved qemu's
    CPU use, and took damage-to-command latency inside qemu from
    a 14 ms p50 to 0.2 ms.
- **A kernel bug sits underneath finding 2.**
  - From 6.8 onwards, Linux virtio-gpu guests report the whole
    plane as damage on every commit. `virtgpu_plane.c:119` sets
    `ignore_damage_clips` whenever the framebuffer changes, the
    duplicate-state helper copies it forward
    (`drm_atomic_state_helper.c:353`), and nothing clears it.
  - vmwgfx has the same pattern (`vmwgfx_kms.c:121`).
  - A one-line fix was verified with a rebuilt module.
  - This, not qxl damage clips (ranked item 5), is the kernel
    change that matters.
- **Most of the 2.1 s was qemu's default configuration**
  (re-baseline, 2026-09-24,
  `docs/performance/streaming-rebaseline.md`).
  - With `streaming-video=all`, stock qemu draws a keypress at
    80 ms / 10 Mbit under heavy activity in 166 ms p50 (389 ms
    p95), against 2136 ms with streaming off. At 80 ms / 50 Mbit
    and 20 ms / 50 Mbit it reaches the idle floor.
  - `filter` does not help. It never streams the pure-noise
    activity, and on the smoother one at 80 ms / 10 Mbit it was
    worse than streaming off (399 ms against 183 ms).
  - The qemu v2 series regresses whenever content is not
    streamed. Its whole-region drawables are about 1.2 MB each,
    and spice-server's ACK window counts messages, not bytes, so
    the backlog grows about twentyfold: about 5 s at the start of
    every stream at 80 ms / 10 Mbit, and 8 s in steady state for
    non-streamable content. That is the most direct evidence yet
    for phase 1's ACK-window explanation.
  - The kernel fix made no difference to this DIRTYFB-based
    guest.

### The wider ranked list

These are not all phases of this plan; see Execution and
Future work for what is scheduled.

| # | Change | Where | Upstream odds |
|---|--------|-------|---------------|
| 0 | Proxy socket backpressure (disproven by phase 1) | Kerbside | Ours |
| 1 | Rect-list damage, damage-driven pacing, no column slicing, one copy | qemu `ui/spice-display.c` | Good |
| 2 | Per-session, scoped, one-time tickets; secondary channels authenticate by `connection_id` | spice-server, qemu, libvirt | Medium |
| 3 | Lossless refinement when idle, and real damage for remote `gl=on` | spice-server, qemu | Plausible |
| 4 | Vendor-neutral hardware encode, enable the dormant H.265 path, 60 fps ceilings, faster bitrate ramp | spice-server `gstreamer-encoder.c` | Very plausible |
| 5 | Atomic `FB_DAMAGE_CLIPS` in the guest qxl driver | Linux `drivers/gpu/drm/qxl` | Slow (dormant driver) |
| 6 | Link-quality hint from the proxy; client-initiated RTT ping | spice-server, spice-protocol | Medium-high |
| 7 | Disconnect reasons, ticket id and per-channel stats in QMP events | spice-server, qemu, libvirt | High |
| 8 | Token auth inside TLS replacing RSA-1024/SHA-1 | spice-protocol, spice-server | Medium-high |
| 9 | `NUM_TRACE_ITEMS` 8 to 64; GLZ invalidations in the zlib fallback | spice-server | High |

Ruled out: kTLS, splice, `MSG_ZEROCOPY` and io_uring
zero-copy in the proxy. Kerbside must parse plaintext and
re-encrypt each leg, and SPICE runs at tens of Mbit/s, so
copies are not the bottleneck. A virtio-gpu "this region is
video" hint was also ruled out: it needs a virtio spec change
plus compositor cooperation, and precise damage gets most of
the benefit.

Things that already work and we do not use:
- remote `gl=on` with hardware H.264 (spice-server 0.15.3 or
  later);
- `virDomainOpenGraphicsFD` with skipauth, for a co-located
  proxy. It is blocked only because `reds_security_check`
  treats AF_UNIX as insecure (`reds.cpp:2193`);
- TLS session resumption on the backend leg.

## Mission

Make SPICE sessions through Kerbside measurably faster and
better looking, in three horizons:

- **now**, in our own code (the proxy);
- **soon**, through small upstream patches to the in-qemu
  SPICE path that existing Nova and oVirt deployments will run
  for years, and by having Kerbside re-encode the display for
  clients on slow links, which works against any qemu;
- **later**, by testing whether a Rust SPICE server on qemu's
  D-Bus display is a better foundation than spice-server.

Every change is measured before and after; Kerbside has no
recorded latency figures today (the latency loadtest only
uploads CI artifacts), so producing a baseline is part of the
first phase.

## Open questions

1. **How do we measure a WAN link?** Answered by phase 1: see
   `tools/shaped-link/`. The rig runs entirely inside an
   unprivileged user network namespace, with netem on both ends
   of a veth pair. It also needs `gso_max_size 1500
   gso_max_segs 1`, or netem counts 64 KiB GSO packets as
   single packets. Running it in CI is still an open choice.
2. **Where do upstream patch series live?** Decided on
   2026-09-23: in shakenfist/kerbside-patches, next to the
   patches we already carry.
   - That repository's tooling is shaped around OpenStack and
     discovers projects as `./<project>/config.yaml`. The
     series therefore live one level deeper, under
     `upstream/qemu/` and `upstream/linux/`, where none of it
     looks. A `release` value of their own was considered and
     rejected, because not every tool filters on release.
   - Those projects also need a non-OpenStack test path
     (build, then the measurement rig) and a mailing-list
     submission path (`git send-email`/b4, not Gerrit).
   - Adapting kerbside-patches is planned in that repository.
   - The son-of-SPICE helper, being a program rather than a
     patch series, will still want its own repository if phase 4
     says go.
3. **Does Kerbside's role change if phase 4 succeeds?** Partly
   decided on 2026-09-24: Kerbside stays in front of any
   helper, because hypervisor ports are never exposed to
   clients. What remains open is how much of ticketing and
   firewalling moves into the helper, and that is an output of
   phase 4, not an input.
4. **Transcode or relay: who decides, and when?** Phase 3's
   question. Once Kerbside re-encodes a display channel it owns
   the client's cache state (pixmap cache, GLZ dictionary,
   stream IDs). The plan does not yet know whether a session
   can move back to relay mid-flight: reconnecting the backend
   display channel, plus `INVAL_ALL_PIXMAPS` and a surface
   destroy and recreate on the client, might do it. If it
   cannot, the choice is made per session at connect, from
   measured RTT, a client hint, operator policy per source, or
   some mix, and a session that moves from office Wi-Fi to LTE
   keeps its first choice for its lifetime. The alternative,
   always transcoding with a near-lossless LAN mode, removes
   the choice at the cost of CPU on every session.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Proxy backpressure and a WAN baseline | [PLAN-spice-performance-phase-01-proxy-backpressure.md](/components/kerbside/plans/PLAN-spice-performance-phase-01-proxy-backpressure/) | Complete | 52046167f8 (#478) |
| 2. qemu damage path: prototype and measure; carry qemu, submit the kernel fix | | In progress | kerbside-patches b53aa39c7e (#1748) |
| 3. Display transcoding in Kerbside: feasibility spike | [PLAN-spice-performance-phase-03-transcoding.md](/components/kerbside/plans/PLAN-spice-performance-phase-03-transcoding/) | In progress | |
| 4. Son of SPICE: D-Bus display feasibility spike | | Not started | |
| 5. Small spice-server patches (item 9, and item 4 if still wanted) | | Not started | |
| 6. Push audit (kerbside and kerbside-patches) | | Not started | |

**Phase 1** sets `TCP_NOTSENT_LOWAT` on the client leg and caps
the backend leg's receive buffer, with the values made
configurable. It measures keypress-to-draw latency over a shaped
link, before and after, which produces Kerbside's first recorded
latency figures.
- The result is null (see "What the first measurements
  changed"). The options ship defaulted off.
- The lasting outputs are the shaped-link rig and
  `docs/performance/proxy-backpressure.md`.
- BBR was not measured: the host has no `tcp_bbr` module, and
  an unprivileged namespace cannot load one.

**Phase 2** started on 2026-09-23 as an out-of-tree prototype.
It is a patch series against qemu `ui/spice-display.c`,
measured with Ryll in headless mode against a virtio-gpu guest.
The prototype, its v2 and a companion kernel patch for the
virtio-gpu damage-clip bug have all reported (below). Both
series and their rigs were imported into kerbside-patches
(`upstream/`), which merged on 2026-09-24 as b53aa39c7e (#1748).
What remains is the dri-devel submission of the kernel fix;
qemu is carried downstream, not submitted (below).

Its `Merged` cell records `kerbside-patches <sha> (#pr)` for the
import, and `linux <sha>` once the kernel fix lands. The push
audit cites kerbside-patches' own audit for the import and the
dri-devel review for the kernel fix. The carried qemu series
has no upstream review, so its audit is kerbside-patches' own.

Both have now reported, and each upstream has its own
contribution rule.

- **qemu v2 is finished, but it cannot go upstream as written.**
  - What v2 contains:
    - four patches, with the damage list split out into
      `ui/spice-damage.c`;
    - a 12-case unit test;
    - pacing through the existing `max-refresh-rate` option;
    - a new split on column gaps.
  - How it performs:
    - it is as fast as or faster than upstream's column diff in
      every worst case benchmarked;
    - it improves std-vga, qxl-VGA and two-head virtio, with no
      regressions.
  - The blocker is policy. qemu's
    `docs/devel/code-provenance.rst:293-297` declines any
    contribution believed to include or derive from AI-generated
    content, and names Claude. Research use is explicitly
    allowed.
  - The routes open are:
    - send the problem statement and measurements, and let a
      human write the code;
    - rewrite the series by hand, using this as a reference;
    - ask qemu-devel for an exception.
  - Decided 2026-09-24: carry the series downstream in
    kerbside-patches (`upstream/qemu/`, per its
    `docs/plans/upstream-series.md`) while Michael gets familiar
    with qemu. A hand rewrite for upstream stays possible later,
    and phase 4 may make it moot: a D-Bus helper bypasses
    `ui/spice-display.c` entirely, although Nova and oVirt
    deployments would keep running in-qemu SPICE.
  - The SPICE section of qemu's MAINTAINERS is orphaned. The only
    recipient `get_maintainer.pl` returns is Marc-André Lureau,
    as Graphics "odd fixer".
- **The kernel accepts AI-assisted patches under conditions.**
  - `Documentation/process/coding-assistants.rst` accepts them
    with an `Assisted-by:` tag, provided the human adds their own
    `Signed-off-by`.
  - drm-misc-next already fixes virtio-gpu: a9cc9905ddb7, which
    depends on 730f8554f35c. Neither commit is tagged for stable,
    so 6.8-7.3 stay broken, and vmwgfx is still affected.
  - The drafted patch is a one-line core fix: clear the flag in
    `__drm_atomic_helper_plane_duplicate_state`, as that helper
    already does for `fb_damage_clips`. It is tagged Fixes
    35ed38d58257 and Cc stable v6.8+.
  - It was verified on 6.12.101 with a rebuilt
    `drm_kms_helper.ko`. With the fix, the plane flushes 480x360
    and 80x28 regions instead of the full 1280x800.
  - Maintainers may prefer a vmwgfx-only patch plus a stable
    backport request for the misc-next pair.

**The v2 series needs a size cap before anyone runs it on a
slow link.** The re-baseline showed it regressing badly whenever
a large region is sent as a bitmap rather than a stream (see
"What the first measurements changed"). Splitting damage larger
than some byte budget into bands would keep v2's single stream
while bounding what one ACK-window message can carry. This
applies to the series carried in kerbside-patches.

The prototype also turned up these problems:
- spice-server 0.15.2 crashes in `VideoStreamClipItem`'s
  destructor when a client connects while streams already
  exist. The re-baseline hit it on every streaming case until
  the rig delayed the guest's activity past the connect. In
  production it means a user reconnecting to a VM that is
  playing video crashes that VM's qemu, which matters more now
  that streaming is the main lever (Future work).
- Ryll has three issues: H.264 streams from spice-server never
  decode (ryll#398, openh264 `dsNoParamSets`); `--capture`
  panics outside a runtime (ryll#399); and `display.pcap` loses
  message alignment (ryll#400).

**Phase 3** (added 2026-09-24) is a time-boxed spike in which
Kerbside re-encodes the display channel for clients on slow
links, instead of relaying it unchanged. It comes before the
Son of SPICE spike for three reasons:

- **It works against the qemu that is already deployed.** Nova
  and oVirt run in-qemu SPICE and will for years; this helps
  them with no hypervisor change.
- **It fixes what phase 1 found.** Today spice-server's ACK
  window spans the client's link, so a long, fat link caps the
  server and the pipe fills with stale frames. If Kerbside
  terminates the display channel, it acknowledges spice-server
  at LAN speed. It then sends the client the latest screen
  state whenever the client socket can take more, dropping what
  the link cannot carry. `TCP_NOTSENT_LOWAT`, which did nothing
  in relay mode, becomes the pacing signal. Frame dropping moves
  to the one component that sees both links. For transcoded
  sessions this replaces the ACK-pacing item in Future work.
- **It builds the output half of Son of SPICE.** The component
  that turns screen state into an encoded stream suited to the
  link is the same one a D-Bus helper needs. With it in hand,
  phase 4 reduces to swapping the input side: D-Bus scanouts
  instead of a SPICE display channel.

**Re-scoped on 2026-09-24** after step 0's re-baseline
(`docs/performance/streaming-rebaseline.md`). The second reason
above is much weaker than it was written: `streaming-video=all`
on stock qemu already takes the 2.1 s case to 166 ms p50, leaving
transcoding about 70-100 ms of p50 and 200-300 ms of p95 to win
on the worst link measured. The phase stays, argued instead on:
- **image quality:** `all` streams animated text lossily, while
  a transcoder can send text losslessly and video lossily;
- **codec choice that does not depend on what spice-server and
  the client negotiate,** including when the client cannot
  decode what the server would pick (ryll#398, #477);
- **links slower than 10 Mbit,** which the rig has not measured
  yet;
- the second half of Son of SPICE, unchanged.

Cheaper work comes first, because the re-baseline showed where
it pays: fixing #477; a band-size cap for the carried v2 qemu
series; reproducing the spice-server 0.15.2 connect crash on
spice-server master; and a `streaming-video` recommendation for
operators (Future work). The spike's measurements add a
sub-10 Mbit profile and an image-quality score for `all` against
transcoding, since those are now its case.

Most of the pieces exist in Ryll:
- `shakenfist-spice-renderer` keeps SPICE surfaces current (its
  `display/` and `surface_mirror.rs`), and headless mode already
  runs it without a window;
- `encoder/` has an openh264 encoder task behind a `FrameSource`
  trait;
- `shakenfist-spice-webrtc` is the browser bridge `ryll --web`
  uses.

Kerbside already depends on Ryll's protocol crate, and the two
legs are already negotiated separately, but not usefully. The
client leg answers with fixed caps (`session.rs`), and the
backend leg always advertises Ryll's `DEFAULT_DISPLAY`
(`shakenfist-spice-protocol/src/link.rs:596`), whatever the real
client said. That is a bug in today's product (#477), and every
measurement so far used Ryll's capabilities. Transcoding needs
the same fix: capabilities chosen per connection, which the ryll
crate cannot take today. The new code beyond that is the half
that emits a SPICE display channel to the client.

Output modes, in the order to build them:

1. **Relay** (today's behaviour), for LAN clients. No added
   latency.
2. **SPICE out, re-encoded.** Kerbside emits its own display
   channel. It streams the busy region in whichever codec the
   client advertises (MJPEG, VP8 or H.264), uses lossy JPEG for
   static drawing, and drops frames the link cannot absorb.
   - Stock remote-viewer works unchanged.
   - Only display channels are terminated. Cursor, inputs,
     audio and USB stay plain relays, so this is much smaller
     than `ryll --web`.
   - On the backend leg Kerbside advertises `MULTI_CODEC` with
     no `CODEC_*` bits, so spice-server has no stream encoder to
     pick and the picture is compressed lossily only once. This
     has to be precise: a client without `MULTI_CODEC` still
     gets MJPEG (`server/video-stream.cpp:801-835`). What
     spice-server does when no encoder is available, which is
     presumably to keep sending plain drawing, is not yet
     verified. It also sidesteps ryll#398, the broken H.264
     decode.
3. **WebRTC out,** for browsers: `ryll --web` moved into
   Kerbside. It brings UDP and loss handling, but Kerbside
   would have to terminate every channel, so it is a later
   step and is not in this spike. It raises the same UDP
   exposure question as phase 4's QUIC design input (ports,
   NAT and TURN, firewall operations). That is one transport
   decision for Kerbside, to be made once for both phases.

The spike:
0. **Validate the premise and build a fair baseline** before
   building anything.
   - Phase 1's 2.1 s p50 (80 ms / 10 Mbit, heavy activity) was
     measured with qemu's `streaming-video` at its default, off
     (`docs/performance/proxy-backpressure.md:112`). Without
     streams nothing is droppable: spice-server's frame dropping
     while it waits for an ACK applies only to stream frames.
     Re-measure the rig with `streaming-video=filter`, and again
     with the phase 2 qemu series and the kernel fix. If those
     close most of the gap, the case for transcoding is
     re-argued before continuing.
   - **Done 2026-09-24**
     (`docs/performance/streaming-rebaseline.md`). They close
     most of it: `streaming-video=all` on stock qemu takes heavy
     activity at 80 ms / 10 Mbit from 2136 ms to 166 ms p50.
     What remains for transcoding is about 70-100 ms above the
     97 ms idle floor at p50 and 200-300 ms at p95, on that
     profile only. Transcoding's remaining case is image quality
     (`all` makes animated text lossy), codec control that does
     not depend on the client, and links worse than 10 Mbit,
     rather than latency on the links measured here.
   - Phase 1 attributes the throughput cap to spice-server's ACK
     window from reading the code, not from observing it.
     Confirm it directly, either by tracing `waiting_for_ack` or
     by having Kerbside acknowledge on the client's behalf in
     relay mode on the rig. That cap lifting at 80 ms / 50 Mbit
     would confirm the mechanism transcoding depends on.
1. make capabilities per connection (#477), in the ryll crate
   and in Kerbside;
2. terminate one display channel in the proxy with Ryll's
   renderer, and re-emit it as SPICE: an MJPEG stream (which
   remote-viewer decodes without GStreamer) for the busy region
   plus JPEG drawing, paced by the client socket;
3. measure it against step 0's baseline matrix. Record per
   session:
   - keypress-to-draw latency;
   - CPU on the Kerbside host, including on an unshaped LAN
     link, which is the cost of the always-transcode option in
     Open question 4;
   - image quality as SSIM and PSNR against a lossless capture
     of the same run, plus a check that text stays legible;
   - the guest's damage path (atomic damage clips or DIRTYFB),
     because of the kernel bug in finding 2.
   H.264 output cannot be measured this way until ryll#398 is
   fixed, and remote-viewer reports no timings, so the spike
   measures MJPEG only;
4. check that remote-viewer and Ryll both render the result
   correctly;
5. test whether a session can move between relay and
   transcoding mid-flight (Open question 4).

Out of scope for the spike, and each needs an answer before a
productising plan:
- multiple monitors, meaning several display channels;
- remote `gl=on`, where spice-server sends only a video
  stream, so Kerbside would have to decode H.264 (ryll#398) and
  the picture would be compressed lossily twice;
- QXL off-screen surfaces;
- seamless migration. Kerbside's allowlist relays the
  `MIGRATE*` messages, and a transcoder that holds state must
  either handle them or refuse them;
- how the firewall policy's L0 size and rate caps apply once
  the display channel is terminated rather than relayed, and to
  which leg.

Risks the spike must report on:

- **Mode switching.** Once Kerbside re-encodes, the client's
  pixmap cache, GLZ dictionary and stream IDs belong to
  Kerbside. Whether a session can go back to relay is step 5's
  question, not an assumption. The spike proposes how the mode
  is chosen (Open question 4). Adapting codec, quality and frame
  rate within a transcoded session is unaffected either way.
- **CPU and capacity.** Software H.264 at 1080p30 costs about a
  core per busy session. Idle desktops cost almost nothing,
  because the encoder only runs on damage. Kerbside nodes are
  not hypervisors, so a GPU for VA-API or NVENC there is an
  easier ask than in the compute fleet. Either way, Kerbside
  would need capacity planning per node for the first time. That
  includes what happens on a saturated node (refuse the session,
  or fall back to relay at connect) and how a multi-node
  deployment places sessions by CPU.
- **Security.** It suits the inspection-first firewall: the
  client receives only pixels Kerbside produced, which is
  content disarm by re-rendering. In exchange, Kerbside decodes
  compressed images (LZ, GLZ, QUIC, JPEG, LZ4) from
  spice-server, whose content the guest influences. Fuzzing is
  necessary but not sufficient. The decoders would otherwise run
  in the process that holds every session and the TLS keys, so a
  productised design runs one sandboxed transcoder worker per
  session: a separate process under seccomp, which also gives
  CPU accounting and a clean kill on overload. Kerbside already
  sees plaintext traffic, so it learns nothing new.
- **H.264 patent licensing.** Cisco's openh264 licence covers
  only Cisco's own binary. A build from source, shipped in the
  kerbside-proxy wheels on PyPI, would have no such cover. MJPEG
  and VP8 avoid the question. The spike can use openh264, but
  shipping H.264 output needs a licensing decision first.
- **Rendering accuracy.** A bug in Ryll's renderer becomes a
  visible artefact for every transcoded user, not only for Ryll
  users.

Its output is a go or no-go recommendation, with phase plans
for productising the SPICE-out mode, and WebRTC out, if the
answer is go. The phase 2 qemu series still pays off under
transcoding, because fewer, larger draws are cheaper to decode.

The phase plan
([PLAN-spice-performance-phase-03-transcoding.md](/components/kerbside/plans/PLAN-spice-performance-phase-03-transcoding/),
2026-09-26) grounds this sketch in the code and changes it in
four places:
- the first output is JPEG and lossless draws rather than an
  MJPEG stream. spice-gtk schedules stream frames against a
  multimedia clock that spice-server 0.16 no longer offsets, and
  a queued frame can paint over a later draw;
- MULTI_CODEC with no CODEC_* bits suppresses streams but not
  lossy draws. The backend also needs PREFERRED_COMPRESSION set
  to LZ4 to be lossless;
- the client-leg link reply is sent before authorisation and
  before the backend is dialled, so #477's fix can forward the
  client's caps to the backend but not the backend's to the
  client;
- the renderer builds openh264 from source, so the transcoder
  sits behind a build feature that the release wheels do not
  enable.

**Phase 4** is a time-boxed spike, with three steps:
1. confirm that D-Bus `Update` rectangles for virtio-gpu
   arrive promptly and precisely, which is not yet traced
   through every device's refresh path;
2. build a minimal helper (main, display, inputs, cursor,
   LZ4, one client) on Ryll's protocol crate;
3. compare it against in-qemu SPICE on the phase 2 workload.

If phase 3 says go, the helper reuses phase 3's output half
rather than growing its own encoder. Its output is a go or
no-go recommendation, with a proposal for a separate master
plan if the answer is go.

Design inputs for phase 4, from the low-latency streaming
stacks: NVIDIA GameStream and its open reimplementation
Sunshine/Moonlight, Amazon DCV, PCoIP and Parsec. The spike
does not build these, but it must not make choices that rule
them out:

- **The frame stays on the GPU.** Capture scanout as a dmabuf
  (D-Bus `ScanoutDMABUF`, or udmabuf for a 2D guest), convert
  RGB to YUV on the GPU, and encode in hardware (NVENC, VA-API,
  V4L2 m2m). There is no CPU readback.
- **Encoder tuned for latency, not efficiency.** That means:
  - no B-frames;
  - an infinite GOP with intra-refresh;
  - a VBV of about one frame;
  - slice output, so the first slices are sent before the frame
    finishes encoding;
  - HEVC or AV1 4:4:4 (or lossless refinement when idle) so
    that text stays sharp.
- **Loss recovery without keyframes.** The client acknowledges
  frames, and on loss the encoder invalidates reference frames
  (NVENC `nvEncInvalidateRefFrames`) and falls back to the last
  acknowledged one.
- **A UDP transport.** QUIC or DTLS-SRTP, with FEC and
  congestion control driven by continuous RTT and loss feedback.
  This avoids TCP head-of-line blocking. It is the biggest
  departure from SPICE, and it means Kerbside would need a
  QUIC-terminating relay path next to its TCP one. Kerbside
  still fronts the helper either way: hypervisor ports are
  never exposed to clients.
- **Frame pacing.** Frames are paced to the client's display
  rather than to a server timer. The cursor stays client-side.
  Input travels on its own low-latency path.

**Phase 5** takes the upstream items that are cheap and
server-internal, in two parts:
- **Item 9, a larger stream trace ring and GLZ
  invalidations,** does not depend on either spike. Relay
  sessions keep using spice-server, and under transcoding
  spice-server still encodes the LAN leg. It can start
  whenever there is capacity.
- **Item 4, vendor-neutral hardware encoder probing, H.265
  and 60 fps ceilings,** waits for phases 3 and 4, because a
  go in either moves encoding for slow links out of
  spice-server and reduces its value.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

## Agent guidance

Phases follow `PLAN-TEMPLATE.md`'s sub-agent execution model:
implementation by sub-agents, review and commits in the
management session. Phases 2, 3 and 4 involve protocol and
upstream-codebase judgement and are planned at high effort;
phases 1 and 5 at medium. Rust builds run in Docker, per
the proxy's Makefile, never with a native toolchain on the
host.

## Future work

These upstream items from the ranked list are deliberately not
scheduled. Items 2, 6, 7 and 8 all depend on phase 4's
verdict: if a Rust helper owns the SPICE server, we define
ticketing, link hints and observability ourselves and never
need spice-server to change. If phase 4 is a no-go, they
become the next plan:

- per-session, scoped, one-time tickets (item 2);
- a proxy link-quality hint and a client RTT ping (item 6);
- richer QMP session events (item 7);
- token authentication inside TLS (item 8). This would also
  unpin Ryll from SHA-1-era RustCrypto crates (ryll #93).

Other deferred items:

- lossless refinement for remote `gl=on` (item 3);
- `FB_DAMAGE_CLIPS` for the guest qxl driver (item 5). It is
  only worth doing if QXL guests stay common;
- treating AF_UNIX as secure in `reds_security_check`, which
  would unlock fd-passed backend attachment for a per-hypervisor
  Kerbside;
- TLS session resumption on the proxy's backend leg, which
  needs no upstream change.
- **ACK pacing, the lever phase 1 found.** Kerbside relays the
  client's display-channel ACKs, so it could hold them back
  against its measured client-leg backlog and effectively shrink
  spice-server's message window on slow links. The upstream
  equivalent is a byte- or RTT-aware ACK window in
  spice-server. Either needs measuring on the shaped-link rig
  before it is believed, and throughput on high-BDP links is
  the trade-off to watch. Phase 3's transcoding makes it moot
  for transcoded sessions, so it matters only for sessions left
  in relay mode.
- Recommend a `streaming-video` setting in the use-case pages
  and Ryll's libvirt recommendations, since qemu defaults it to
  off. The re-baseline favours `all` over `filter` for latency,
  but `all` makes animated text lossy and needs the spice-server
  0.15.2 connect crash fixed or avoided first, so the
  recommendation should state both.
- Upstream the rig's small Ryll patch, which adds a `rect` to the
  control socket's `surface_drawn` event
  (`tools/shaped-link/ryll-surface-drawn-rect.patch`). Until
  then the rig carries it as a patch.
- Report spice-server 0.15.2's crash when a client connects
  while streams already exist, once it has been reproduced
  against spice-server's current master.

## Bugs fixed during this work

None yet.

## Back brief

Before executing any step of this plan, back brief the
operator on your understanding of the plan and how the work
you intend to do aligns with it.
