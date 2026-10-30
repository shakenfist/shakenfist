# A SPICE helper on qemu's D-Bus display: the spike's verdict

Kerbside asked whether a SPICE server outside qemu, fed by qemu's D-Bus
display (`-display dbus`), would be a better foundation for consoles
than the spice-server built into qemu. The helper measured is
[andris](https://github.com/shakenfist/andris), a Rust SPICE server,
with a qemu platform that takes picture, damage and cursor from the
D-Bus display and sends keyboard and mouse back to it. That platform
landed in andris behind its non-default `qemu` cargo feature, as
shakenfist/andris#56.

This page gives the recommendation and argues it from two sources,
without re-measuring anything:

- [a SPICE helper on qemu's D-Bus display, on a shaped link](/components/kerbside/performance/dbus-helper-measurements/),
  which compares the helper with qemu's own spice-server through
  Kerbside's proxy;
- a runtime trace of qemu's D-Bus display, taken while the helper's
  picture half was built, whose numbers are given
  [below](#what-qemus-d-bus-display-delivered).

[Transcoding the display channel: the spike's verdict](/components/kerbside/performance/transcoding-spike/)
is the previous spike on the same rig, and argued that this one need
not wait for it. Written on 2026-10-29.

## Verdict: no-go, as measured

**Do not productise a D-Bus helper on today's measurements.** The
verdict rests on three criteria, set before the measurements, and go
needed all three:

| Criterion | What go needed | Result |
|---|---|---|
| Latency | Level with or better than the better of `streaming-video=off` and `=all` in most cells, and never worse than `streaming-video=off` | **Fails.** Level or better in 9 of 14 cells, but worse than streaming off in 4 |
| Cost | Host CPU within a factor of two of in-qemu SPICE, idle and with heavy activity | **Passes.** At most 1.27 times the cheaper spice-server configuration |
| Reachable sources | At least one Kerbside source can attach a helper without upstream work in another project | **Passes, narrowly.** Shaken Fist could, with work only in Shaken Fist and andris; Nova and oVirt cannot |

The failure is not the D-Bus display's. qemu handed each update to the
helper 0.2 ms after the guest flushed it, its own CPU stayed flat on every
link, and its memory did not grow. Every cell the helper lost was one in
which andris never started a video stream, and in every cell where it did
start one it was level with `streaming-video=all`. andris decides to
stream from the frames it has sent, and on a slow link its ACK window
spaced those frames too far apart for any area to qualify. That is a
heuristic in andris, and it can be changed. See
[whose failure it is](#is-the-latency-failure-the-d-bus-displays-or-andriss).

So the no-go is conditional. [What would change the
answer](#what-would-change-the-answer) names two andris changes that
would plausibly turn it, and the measurement that would show whether
they did. Until then, nothing here argues for moving any deployment off
in-qemu SPICE, and nothing argues against the D-Bus display as a
foundation either.

### Latency, cell by cell

Keypress-to-draw p50 in ms, through the proxy, from the measurements
page. "Level" means within 5 ms of the better spice-server configuration.

| Link | Activity | Streaming off | Streaming all | Helper | Helper against the better | Helper against streaming off |
|---|---|---|---|---|---|---|
| unshaped | idle | 15 | 19 | 3 | better | better |
| | busy | 21 | 20 | 21 | level | level |
| | heavy | 33 | 22 | 22 | level | better |
| 20 ms / 50 Mbit | busy | 135 | 38 | 40 | level | better |
| | heavy | 250 | 39 | 43 | level | better |
| 80 ms / 50 Mbit | busy | 105 | 100 | 102 | level | level |
| | heavy | 462 | 99 | 98 | level | better |
| 80 ms / 10 Mbit | idle | 96 | 98 | 88 | better | better |
| | busy | 184 | 101 | 106 | level | better |
| | heavy | 2039 | 113 | 3492 | worse | **worse** |
| 80 ms / 5 Mbit | busy | 371 | 118 | 3263 | worse | **worse** |
| | heavy | 4952 | 112 | 6931 | worse | **worse** |
| 150 ms / 2 Mbit | busy | 869 | 185 | over 15 s | worse | **worse** |
| | heavy | over 15 s | 7315 | over 15 s | worse | not shown |

"Over 15 s" means that no repeat drew its first key press within the
rig's limit, in four tries. At 150 ms / 2 Mbit with heavy activity both
the helper and the relay with streaming off failed that way, so the
measurements cannot say which was worse.

Where the helper was level, it was level on p95 too (within 7 ms of
`all`), and idle it was faster than either spice-server configuration,
because andris sends a change as soon as qemu reports it rather than on
qemu's 30 ms display refresh.

### Cost

Cores, CPU seconds per second over the key presses, for qemu plus andris
against qemu with spice-server. The proxy is left out: it relays both,
and it used less with the helper.

| Link | Activity | Streaming off | Streaming all | qemu + andris | Against the cheaper |
|---|---|---|---|---|---|
| unshaped | idle | 0.019 | 0.020 | 0.022 | 1.16 |
| 80 ms / 10 Mbit | idle | 0.021 | 0.017 | 0.018 | 1.06 |
| unshaped | heavy | 0.677 | 0.173 | 0.195 | 1.13 |
| 20 ms / 50 Mbit | heavy | 0.230 | 0.179 | 0.213 | 1.19 |
| 80 ms / 50 Mbit | heavy | 0.296 | 0.158 | 0.200 | 1.27 |
| 80 ms / 10 Mbit | heavy | 0.126 | 0.120 | 0.149 | 1.24 |
| 80 ms / 5 Mbit | heavy | 0.135 | 0.135 | 0.140 | 1.04 |

There is no helper figure at 150 ms / 2 Mbit, where no repeat completed.
Outside the criterion:

- **Busy activity costs more.** andris encodes the smooth gradient as a
  larger H.264 stream, and qemu plus andris used 0.29 to 0.33 cores on
  the fast links, 1.8 times `all`'s 0.16 to 0.18. That is still inside a
  factor of two.
- **std-vga costs six times as much.** On std-vga qemu has no shared
  buffer to hand over, so it polls VRAM's dirty bitmap every 30 ms and
  copies each changed band over the socket. qemu alone used 0.76 to 0.78
  cores whatever the guest drew. A helper is a virtio-gpu proposition.
- **Memory moves rather than grows.** Without spice-server qemu's peak
  RSS was 19 to 43 MiB smaller, and andris peaked at 34 to 66 MiB.

The merged platform copies each update's rows out of qemu's shared
memory before copying them into its own screen, which the measured
commit did not do (see [Caveats](#caveats)). The trace measured one
full-screen copy at 0.65 to 0.73 ms at p50. If the second costs as much,
at the guest's 30 updates a second it adds about 0.02 cores, which would
take the worst heavy ratio from 1.27 to about 1.4.

### Reachable sources

What each of Kerbside's sources would need before it could put a helper
in front of a VM. Every libvirt-based source has one gap in common:
andris attaches only through qemu's QMP socket, and libvirt keeps that
socket for itself (see [Attaching under libvirt](#attaching-under-libvirt)).

| Source | Today | What it would need |
|---|---|---|
| OpenStack (Nova) | Nova's libvirt driver writes only VNC and SPICE graphics | Upstream Nova: a way to ask for `<graphics type='dbus'>`, and to report the helper's port as the spice-direct console. On the compute host, something to start andris per instance with the display's file descriptor. In andris, an attach mode that takes that descriptor |
| oVirt | The engine's graphics types are SPICE and VNC, and vdsm has no D-Bus display | Upstream ovirt-engine and vdsm changes, for the display, for launching a helper and for reporting its port |
| Shaken Fist | Writes its own libvirt domain template | A video type that writes `<graphics type='dbus' p2p='yes'/>`, which libvirt has accepted since 8.4.0; a per-instance andris on the hypervisor, given the display's descriptor, a ticket and its TLS material; and andris's descriptor attach mode. Kerbside's Shaken Fist source would need no change if Shaken Fist reported the helper's ports as the instance's console ports under a video type starting `spice` |
| Proxmox VE | qemu 11.1, but a D-Bus display only through a VM's raw `args:` | No change in Proxmox, but an unsupported configuration: `args:` (root only) for the display, a hookscript to start andris after the VM, and a way to reach andris that is not spiceproxy, which forwards to the VM's own SPICE port. Kerbside's Proxmox source would need a backend path and a ticket for the helper |

So the criterion passes on Shaken Fist, whose changes are all in Shaken
Fist and andris, projects that the same maintainers control. Kerbside's
static source could also list a helper started by hand next to a qemu
that is not under libvirt, as the rig does, which needs nothing new at
all; but that source exists for CI and debugging, not deployment. Nova
and oVirt deployments would keep in-qemu SPICE whatever the latency
verdict.

## Is the latency failure the D-Bus display's or andris's?

andris's. The evidence points one way:

- **qemu delivered updates promptly.** The trace found virtio-gpu
  2D flushes reaching the helper 0.20 ms after the guest flushed at p50,
  and under 0.3 ms at p99, idle and busy. Nothing in qemu's path waits
  on the link.
- **qemu's side did not change with the link.** qemu used 0.075 to 0.104
  cores with activity on every link, against 0.11 to 0.68 with
  spice-server, and its peak RSS stayed at 181 to 194 MiB. A queue
  building inside qemu would have shown in both.
- **The backlog sat in andris.** andris's own session summaries put its
  mean ACK lag at 3.5 s at 80 ms / 10 Mbit heavy, 3.4 to 6.6 s at
  80 ms / 5 Mbit and 7 to 11 s at 150 ms / 2 Mbit: messages waiting for
  the client, not updates waiting in qemu.
- **Streaming decided every cell.** andris streamed in all seven cells
  with activity where it was level, and in none of the five where it was
  worse.

Why it did not stream comes from andris's stream heuristic
(`andris/src/server/updates/heat.rs`, at the measured commit 440b782). A
64-pixel tile is hot when it changed in 20 frames within 2 s, with no gap
over 200 ms, and a cluster of hot, mostly photographic tiles starts a
stream. Those numbers are spice-server's. But andris records a frame in
the heat map when it *sends* it (`detect` in
`andris/src/server/display.rs`), and it sends a frame only when its ACK
window allows. On a link that cannot carry the activity's first JPEG
frames as fast as the guest draws them, the window spaces the sends more
than 200 ms apart, every tile's count restarts, and no stream ever
starts. The activity then goes as JPEG tiles that fill the link, which
keeps the sends spaced: a loop with no exit. spice-server counts the
drawing commands qemu hands it, before they reach its send queue, so its
detection does not depend on the link, and `all` streamed on every link.

A second andris behaviour makes the no-stream path worse than
spice-server's streaming off. andris paces on an ACK window counted in
messages (20), not bytes. With the activity's JPEG tiles each tens of
kilobytes, the messages it lets out hold seconds of a 5 Mbit link, and
the key box's draw waits behind it. spice-server with streaming off sent
the same activity at 80 ms / 5 Mbit busy with a p50 of 371 ms against
andris's 3263 ms. The transcoding spike measured pacing on the client
socket beating an ACK window by up to two times wherever the link was
the limit; this is the same lever.

Neither behaviour belongs to the D-Bus display. A helper fed by it sees
pixels and damage rather than drawing commands, so it has to infer video
from change, as andris does for X11. But it sees every change as qemu
delivers it, and can count from that rather than from what it sent. One
complication is on the rig's side: the guest's kernel flushes its whole
plane on every frame, so damage alone says nothing about which tiles
changed. andris would have to diff each update as it copies it into its
own screen, which it already reads every pixel of.

### What would change the answer

Two andris changes, then a re-measure:

1. **Count stream heat from what qemu delivers, not from what andris
   sent.** For example, diff each incoming update against the platform's
   screen as it is copied in, and record the changed tiles in the heat
   map at qemu's rate. A stream could then start within 20 guest frames
   on any link, as spice-server's does.
2. **Pace on the socket, or on a window counted in bytes.** That bounds
   the backlog for change that is not photographic and so never streams,
   such as scrolling text.

Then re-run the four cells that failed, and 150 ms / 2 Mbit heavy, with
the rig's helper configuration at the new andris commit. If the helper
is no longer worse than streaming off in any cell, the latency criterion
passes, the other two already do, and the verdict becomes go. A
projection is not a pass. Where andris did stream, at 80 ms / 10 Mbit
busy, it was within 5 ms of `all`, which suggests the change would be
enough, but the first 20 frames still go as JPEG before a stream starts,
and on a 2 Mbit link that warm-up was not measured.

## What qemu's D-Bus display delivered

The trace ran on this host's qemu 10.0.13 with the rig's guest, on
virtio-gpu 2D and on std-vga, under a host load average of 5 to 21 on 16
hardware threads. It compared qemu's `virtio_gpu_cmd_res_flush` trace
event with the helper's own timestamps.

- **Promptness.** virtio-gpu 2D flush to listener: 0.20 ms at p50 and
  0.25 to 0.28 ms at p99, idle, busy and heavy. On a guest that flips
  between buffers, the p50 was 0.31 ms but the p99 5.2 ms, because each
  flip costs a round trip (below).
- **Precision.** Every rectangle the listener received matched the
  guest's flush: 145, 1017 and 1017 of each in the idle, busy and heavy
  runs. qemu neither merges nor splits them. The guest always flushed
  its whole 1280x800 plane, because its kernel carries the bug that
  ignores damage clips; that affects spice-server equally.
- **The copy.** The helper keeps its own copy of the screen, so that a
  capture sees each update whole, and to bound tearing from memory the
  guest may still be drawing into. Copying a full-plane update cost 0.65
  to 0.73 ms at p50 and about 0.9 to 1.0 ms at p99.
- **Page flips.** A virtio-gpu page flip makes a new surface, and qemu
  then makes a blocking `ScanoutMap` call to every listener, which its
  main loop waits for. The call reached the helper 0.22 ms after qemu's
  display update at p50 (0.52 ms at p99). The helper took 0.93 ms
  (1.46 ms) to map and copy the new screen, and the update's own
  rectangle followed 1.4 ms after qemu's update at p50, 6.2 ms at p99.
- **std-vga.** qemu polled every 30 ms and sent bands of whole scanlines
  at full width, copied over the socket because VRAM has no shared
  handle.
- **A stalled listener.** With the helper stopped for 10 s, qemu's
  memory stayed flat on virtio-gpu's shared-memory path without flips.
  On std-vga, and on a guest that flips, qemu grew by 184 to 262 MiB/s
  and reached 2.0 to 2.8 GiB, because qemu sends display messages
  without waiting for a reply and queues what the listener has not read.
  A stalled `ScanoutMap` froze a flipping guest for 1 s, after which qemu
  sent that listener whole frames in each message for good. Dropping and
  re-registering the listener frees qemu's queue at once.

The last point is the one a productised helper must design for: qemu is
a hypervisor process, and a helper that stops reading can grow it by
gigabytes. A helper needs a watchdog that notices its listener falling
behind and re-registers, and the guest types that flip pages need it
most.

## Attaching under libvirt

On the rig andris owns qemu's QMP socket: it passes qemu one end of a
socket pair with `getfd` and `add_client`, for `-display dbus,p2p=on`,
and then closes QMP. Under libvirt that socket is libvirt's, so a helper
needs one of two other modes:

- **An inherited descriptor.** libvirt's `<graphics type='dbus'
  p2p='yes'/>` makes the display peer-to-peer, and
  `virDomainOpenGraphicsFD` hands a client the descriptor. A launcher on
  the host could open it and pass it to andris, which would need a mode
  that takes a descriptor instead of a QMP path.
- **A bus address.** Without `p2p`, libvirt starts a D-Bus daemon for
  the VM and qemu exports its display on it. andris would need a mode
  that connects to that bus.

libvirt launches exactly one display helper itself, qemu-rdp, for
`<graphics type='rdp'>`, and only on the bus-address mode. It does not
launch an arbitrary one. Either something outside libvirt starts andris,
or libvirt learns to start a SPICE helper as it starts qemu-rdp. The
first needs no libvirt change; the second is the tidier upstream story,
and is what a Nova integration would want.

## Should the bindings be ours?

**Yes, for a productised helper.** The platform uses the qemu-display
crate's zbus bindings, pinned at git 6ffedb9. That was right for a
spike: the bindings are thin, cover every interface the helper needs,
and already run on tokio in qemu-rdp. It is not what a component in
front of every session should depend on:

- the project is one maintainer's work in progress, and its crates.io
  release, 0.2.1, lags the repository and links the system's
  libusbredirhost and libusb unconditionally, which is why the git pin;
- it is built on zbus 5, which brings three unmaintained-crate advisories
  (RUSTSEC-2026-0370, -0371 and -0372: zvariant, zbus_names and
  zvariant_derive, absorbed into zbus 6) that andris now ignores in both
  `deny.toml` and `.cargo/audit.toml`.

The interface the helper uses is small: `Console.RegisterListener`, the
`Listener` and `Listener.Unix.Map` interfaces, `Keyboard` and `Mouse`.
Proxies generated from qemu's `ui/dbus-display1.xml` on zbus 6 would
remove the advisories and the pin. Contributing a zbus 6 port and an
optional usbredir to qemu-display is the alternative, and either is
mechanical.

## What a GPU-frame path would add

Nothing measured here handles a frame that lives on the GPU. andris
compares and encodes CPU frames held in a shadow, and qemu-display's own
RDP and VNC servers stub their dmabuf handlers. A GPU path, for guests
with virgl or venus, would be a second pipeline beside andris's:

- importing qemu's `ScanoutDMABUF` (or `ScanoutDMABUF2`, qemu 10.1 and
  later) rather than mapping a memfd;
- converting to YUV and encoding in hardware (VA-API, NVENC), with no CPU
  readback, which also means no tile diff and no lossless JPEG/LZ4 path:
  a stream with lossless refinement when idle;
- sitting on qemu's critical path. With GL, qemu's `UpdateDMABUF` blocks
  the console until the slowest listener replies, with a 1 s timeout, so
  a slow helper slows the guest, unlike the 2D path measured here. And
  virtio-gpu with GL sends the whole scanout on every update, whatever
  the guest flushed.

None of this is needed to answer the 2D question, and none of it is
ruled out by the choices made here.

## Upstream: qemu-vnc, qemu-rdp and libvirt's RDP mode

The helper is the SPICE member of a family that upstream is already
building on the same interface:

- **qemu-vnc** ships in qemu 11.1 (`tools/qemu-vnc`), a standalone VNC
  server on the D-Bus display. It uses the shared-memory interface and
  accepts peer-to-peer descriptors, the same choices andris's platform
  made.
- **qemu-rdp**, in the qemu-display project, is an RDP server on the same
  bindings, and libvirt runs it for `<graphics type='rdp'>` since libvirt
  11.1.0.
- **No SPICE server** existed on the interface before andris#56.

So the attach models, the shared-memory path and the stalled-listener
problem are shared with those projects, and anything learned about
qemu's side (the queue that does not shed, the blocking `ScanoutMap`) is
worth raising with qemu rather than working around in one helper. It
also means that a libvirt "launch a display helper" mechanism, if it
were generalised beyond qemu-rdp, would serve andris without a
SPICE-specific change.

## Kerbside in front of a helper

Kerbside fronted the helper exactly as it fronts any SPICE server: the
proxy relayed andris through the same code path, with no flags, and
used less CPU doing so because andris sent less. andris takes its ticket
from a private file, so whatever launches andris must give Kerbside's
source the same ticket. Nothing in Kerbside's proxy or firewall needed
to change, and nothing would for a go.

## Caveats

- **Measured and merged code differ.** The measurements used andris
  440b782. The pull request merged later, as 9a997a3, with two rounds of
  review changes. The one that matters for cost copies each update's
  rows out of qemu's shared memory through raw pointers before copying
  them into andris's screen, because reading memory the guest writes
  through a Rust slice is undefined behaviour. That second copy was not
  measured; [Cost](#cost) bounds it.
- **spice-server's codec differed from the re-baseline.** With
  `streaming-video=all`, spice-server sent MJPEG in all 28 of its repeats
  here, and andris sent H.264 in all 14 of the helper's.
  [The re-baseline](/components/kerbside/performance/streaming-rebaseline/) saw H.264 because its older
  Ryll asked for H.264 first; the Ryll used here (0d140b3), like the one
  behind the other pages on this rig, asks for MJPEG first (see
  [the spice-server patches](/components/kerbside/performance/spice-server-patches/#which-codec-the-streams-used)).
  So here Ryll decoded `all`'s streams, 16 of the 21 it was offered, and
  `all`'s latency was much as on the transcoding pages (113 against 118 ms at
  80 ms / 10 Mbit heavy, 7315 against 7328 ms at 150 ms / 2 Mbit heavy).
- **The guest flushes its whole plane.** Every configuration saw
  full-plane damage. qemu's spice-display diffs it in 32-pixel columns
  before spice-server sees it; andris diffs it in 64-pixel tiles.
- **Small samples on a shared host.** Two repeats of 40 presses per cell,
  four at 150 ms / 2 Mbit, and the host's load average passed 16 for 8%
  of the run. The measurements page shows the quiet repeats separately
  where it mattered.
- **One activity shape.** A 640x480 area at 30 fps on a 1280x800 guest.
  The stream heuristic's failure depends on the link's ratio to the
  activity's JPEG rate, so a smaller or less noisy area would fail on
  slower links, and a larger one on faster.

## What comes next

Nothing here has been filed.

- **Andris: count stream heat from the platform, not from frames sent.**
  As [above](#what-would-change-the-answer). This is the change most
  likely to reverse the verdict, and it would help andris's X11 platform
  on slow links too.
- **Andris: socket pacing, and the JPEG fringe.** Already proposed from
  the transcoding spike; this spike adds the slow-link backlog as a
  second reason.
- **Andris: a lag watchdog for the qemu platform,** which re-registers
  the listener when it falls behind, before a stall grows qemu.
- **Andris: a descriptor or bus-address attach mode,** so that a helper
  can attach under libvirt.
- **Re-measure the failing cells** once the first two land, with the
  rig's helper configuration. If they pass, write the proposal for a
  separate plan that a go calls for: bindings of our own, the watchdog,
  the attach mode, a Shaken Fist integration first, and a libvirt
  helper-launch mechanism for Nova.
- **Which codec spice-server chooses for `streaming-video=all`** is
  answered in [the spice-server patches](/components/kerbside/performance/spice-server-patches/#which-codec-the-streams-used):
  the client's preference decides it, and this Ryll asks for MJPEG
  first.
- **Ticketing, link hints, observability and token authentication**
  stay spice-server and qemu work. A helper would let Kerbside define
  them itself, but only for sources that can run one, and Nova and oVirt
  deployments will keep in-qemu SPICE either way.
