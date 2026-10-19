# RDP frontend for ryll (SPICE → RDP transcoder)

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
`--web` mode end to end (`ryll/src/main.rs` `run_web`, and
everything under `ryll/src/web/`), because this plan builds the
same shape with a different transport. Read the renderer
surface it drives: `run_connection` in
`shakenfist-spice-renderer/src/session.rs`, `SurfaceMirror`,
`DisplaySurface`, the `ChannelEvent` enum, the inputs channel's
`make_scancode`, the main channel's resize path, `OpusPacketSink`
and `MemoryClipboard`. Ground your answers in what the code
actually does today. Do not speculate about the codebase when
you could read it instead.

The RDP side is IronRDP (`github.com/Devolutions/IronRDP`),
principally `ironrdp-server`, `ironrdp-acceptor`,
`ironrdp-egfx`, `ironrdp-cliprdr` and `ironrdp-rdpsnd`. The
closest prior art is `qemu-rdp`, which lives inside
`gitlab.com/marcandre.lureau/qemu-display` (the standalone
`qemu-rdp` URL needs a login) and serves RDP from qemu's D-Bus
display. IronRDP moves quickly: the claims below were read on
2026-10-19 against the 0.13.0 release and `main` at `62246b3`,
and differ between the two. Re-check each one before building
on it, against the version we have actually pinned. The
Microsoft protocol specifications are MS-RDPBCGR (core),
MS-RDPEGFX (graphics pipeline), MS-RDPEDISP (display control),
MS-RDPECLIP (clipboard), MS-RDPEA (audio output) and MS-CSSP
(CredSSP). Flag uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the crate map and concurrency
boundaries, `docs/web-mode-internals.md` for the nearest
precedent, `docs/multi-mode-parity.md` for the parity
discipline every mode follows, `docs/development.md` for
building (always in Docker, via the Makefile) and the local
QEMU test server, and `AGENTS.md` for conventions.

All planning documents go into `docs/plans/`. Detailed
planning gets one plan file per phase, named for this master
plan with `-phase-NN-descriptive` appended before `.md`, and
tracked in the Execution table below. One commit per logical
change, at minimum one per phase; each commit builds, passes
tests, and explains what changed and why.

## Situation

### Where this plan came from

This plan replaces `PLAN-rdp-gateway.md` in
`shakenfist/kerbside` (proposed 2026-09-27, now marked
Superseded there). That plan described a per-session Kerbside
gateway that terminates SPICE with ryll's renderer and serves
RDP with IronRDP, and its first phase was "an RDP prototype
outside Kerbside". On 2026-10-19 we decided to build the whole
thing in ryll first, as a fourth frontend mode, and move it
into Kerbside once it has proven itself. Kerbside's
integration work (its listener, token-derived credentials,
`.rdp` files, audit, termination and placement) is preserved
under *Moving to Kerbside* in Future work below, so that
nothing the Kerbside plan worked out is lost.

The idea arrived independently as "doaki": a Rust RDP server
that is also a SPICE client, for users whose corporate desktop
images ship an RDP client and forbid installing anything else.
The empty `shakenfist/doaki` repository created for it is not
used.

### The problem

Ryll reaches a SPICE console three ways today: the egui
desktop client, headless mode, and `--web`. All three need
something on the user's side that some users cannot have:

- **The GUI** needs ryll installed. Locked-down corporate
  images forbid installing client software.
- **`--web`** needs a modern browser and a network path that
  carries WebRTC media (UDP, or TURN). It works for many of
  those users, but not all, and it is not what their IT
  department supports.
- **Direct SPICE** needs port 5900-ish reachable. Corporate,
  hotel and customer-site networks often allow outbound RDP
  on 3389 and little else.

Almost every Windows desktop has `mstsc`, and macOS, iOS,
Android and Linux all have stock RDP clients. An RDP frontend
reaches users who have nothing else.

**This is about reachability, not quality.** A transcoded RDP
session loses SPICE features (USB redirection, folder sharing,
multiple monitors at first) and costs CPU on the ryll side. It
is a fallback for users who cannot use SPICE, not a better way
for users who can. Every piece of documentation this plan
produces must say so.

### What ryll already gives us

The `--web` mode proved the architecture. It drives a SPICE
session through one renderer call and a little glue:

- `run_connection` (renderer `session.rs`) runs one SPICE
  session and emits `ChannelEvent`s on an mpsc. `run_web`
  forwards those onto a `broadcast` bus and applies them to an
  `Arc<tokio::Mutex<SurfaceMirror>>` in a mirror task.
- Inputs go in as `InputEvent`s on an mpsc. Keyboard events
  are PC scancodes built with `make_scancode(base, release)`,
  where extended keys are `0xE000 | sc`. RDP sends Set 1
  scancodes plus an `extended` flag, so the mapping is one
  line, and better than the browser's, which needs a table.
- Cursor shapes arrive as `ChannelEvent::CursorShape` with
  RGBA pixels and a hotspot; position and visibility as
  `CursorPosition { x, y, visible }`.
- Resize goes out as `DisplaySize::Requested(w, h)` on the
  main channel's resize sender, which debounces and sends a
  `VD_AGENT_MONITORS_CONFIG` once an agent is connected. The
  guest's answer arrives as `SurfaceCreated`.
- `MemoryClipboard` is an in-memory `ClipboardBackend` that a
  non-egui frontend can drive (`set_host_text`,
  `subscribe_guest_copies`).
- `OpusPacketSink` taps the playback channel's Opus packets
  (and PCM, in raw mode) before decode.
- An H.264 encoder already exists (`encoder/h264.rs`, over the
  compression crate's openh264 encoder), so the licensing
  decision Kerbside's plan worried about has already been
  taken for ryll binaries.
- The renderer and its dependencies are published on
  crates.io at 0.3.0, so a future Kerbside gateway can depend
  on released crates rather than a git pin.

### What is missing in the renderer

The survey on 2026-10-19 (ryll `629b1d8`) found these gaps.
All of them are renderer work, and some of them are shared
with Kerbside's transcoding spike (see *Coordination with the
Kerbside spike* below).

1. **No damage rectangles.** `DisplaySurface` keeps one dirty
   bool (`consume_dirty`). `RealFrameSource` copies the whole
   primary surface whenever it is set. The rectangles exist
   in the events (`ImageReady`, `FillRect`, `CopyBits`,
   `Invert` all carry one) but `SurfaceMirror::apply_event`
   reports only `DrawOutcome::Drawn { key }`. RDP wants dirty
   rects; sending the whole frame every time would be
   wasteful even though IronRDP re-diffs internally (below).
2. **Pointer translation lives in the frontends.** The
   absolute-versus-relative logic (track `mouse_mode`, keep
   `last_pos`, synthesise relative motion in server mode,
   suppress zero deltas because each motion costs an
   ack-window slot) exists twice: `ryll/src/web/inputs.rs`
   and `ryll/src/app/mod.rs`. RDP would be a third copy.
3. **The heavy build cannot be trimmed.** The renderer always
   builds the encoder (openh264 from C++) and compression's
   default `mozjpeg`, and always pulls in `nusb`,
   `dav-server` and `hyper`. Kerbside's spike step 3a planned a
   slim feature set for exactly this reason; phase 1 of this
   plan now does it for both.
4. **Audio is a tap, not a sink.** With the `audio` feature
   the playback channel decodes Opus and opens a local cpal
   output regardless of the tap, and decoded PCM is never
   offered to the sink. A server process wants Opus packets
   when the client accepts Opus, decoded PCM when it does not,
   and no local audio device in either case.
5. **Single primary surface.** The mirror keys surfaces by
   `(channel, surface)` but every consumer uses
   `primary_key()`, and the main channel's monitors config
   sends N identical side-by-side heads. Not needed for the
   MVP; recorded so nobody assumes multi-monitor is close.
6. **Mono and 4-bit cursors are refused**, not decoded
   (`channels/cursor.rs`). Guests that use them show no
   cursor shape in any mode. RDP's `ColorPointer` carries
   AND/XOR masks, so this is solvable, but it is a renderer
   fix that benefits every mode.

### The RDP side: IronRDP as of 2026-10-19

Read from 0.13.0 (released 2026-07-10) and `main` `62246b3`.
`main` is roughly 700 PRs ahead of the release and has no
newer release; items marked *(main)* are not in 0.13.0.

- **Shape.** `RdpServer::builder()` is a typestate chain:
  address, security (`with_no_security` / `with_tls` /
  `with_hybrid`), input handler, display handler, then
  optional cliprdr, sound, EGFX, codec, credential-validator
  and connection-handler factories. It needs tokio and uses
  tokio-rustls 0.26, as ryll already does.
- **Display.** We implement `RdpServerDisplay` (`size`,
  `request_initial_size`, `updates`, `request_layout`) and
  return an `RdpServerDisplayUpdates` whose `next_update` must
  be cancel-safe. `DisplayUpdate` is `Resize`, `Bitmap`,
  `PointerPosition`, `ColorPointer`, `RGBAPointer` (up to
  96x96), `LargePointer` *(main)*, `HidePointer`,
  `DefaultPointer` and `CachedPointer`. `BitmapUpdate` takes
  `x, y, width, height, format, data, stride`, and
  `PixelFormat` includes `RgbA32`, so ryll's RGBA surface can
  be handed over without a swizzle (to be confirmed by
  testing that the encoders honour it).
- **Codec choice is the server's, not ours.** If the client
  lacks surface commands, the server sends RDP 6.0 planar
  bitmaps. Otherwise it picks, in order, QOIZ, QOI, RemoteFX,
  NSCodec (feature-gated) and uncompressed surface bits. QOI
  and QOIZ are IronRDP-private, so stock clients get RemoteFX
  if they advertise it (mstsc usually does at 32bpp; not
  verified) and planar otherwise. The macOS Windows App
  reportedly advertises only NSCodec. All output is fast-path.
- **IronRDP keeps its own framebuffer and re-diffs** every
  update in 64x64 tiles. Our rectangles bound the work; they
  do not replace its diff. There is a TODO upstream to make
  the diff optional.
- **H.264 exists, but beside the update loop.** `ironrdp-egfx`
  0.3.0 can send AVC420 and AVC444 frames, with surfaces and
  frame-ack flow control, behind the server's `egfx` feature.
  It is driven through a `GfxServerHandle`, not through
  `DisplayUpdate`. Its OpenH264 *encoder* is main-only; ryll
  has its own.
- **Input.** `RdpServerInputHandler` is synchronous
  (`keyboard`, `mouse`). Keyboard is `Pressed`/`Released
  { code, extended }`, `UnicodePressed/Released(u16)` and
  `Synchronize` (lock-key state). Mouse in 0.13.0 is flat
  per-button variants plus move and vertical wheel; main
  reshaped it into `Move`, `Button`, `VerticalScroll`,
  `HorizontalScroll`, `RelMove` and friends.
- **Security.** TLS (enhanced RDP security, credentials in the
  Client Info PDU inside TLS) and Hybrid/HybridEx CredSSP
  (NLA) are both implemented. NLA is NTLM-only and checks one
  preconfigured plaintext username and password; a pluggable
  `CredentialValidator` can only add authorisation on top.
  Per-user password checking against PAM or LDAP is therefore
  possible only without NLA. A client that requires NLA
  against a TLS-only server is refused cleanly.
- **Resize.** The display-control channel (MS-RDPEDISP) is
  offered by default; client layouts arrive in
  `request_layout`, and we complete a resize by emitting
  `DisplayUpdate::Resize`, which triggers deactivate and
  reactivate. Multi-monitor is effectively absent: the
  acceptor always advertises one primary monitor.
- **Channels.** cliprdr has a full server backend. rdpsnd is
  format-agnostic: we advertise formats and push pre-encoded
  waves, and Opus (`0x704F`) is defined but not encoded for
  us. Drive redirection (rdpdr) server-side is main-only and
  raw.
- **The routing token is discarded.** The acceptor keeps only
  the negotiated protocol from the X.224 connection request,
  and an opaque `loadbalanceinfo` token that is not a
  `Cookie:` line probably fails to parse (code reading, not
  tested). Not a problem for ryll, which serves one VM per
  process; it is a problem for a Kerbside gateway, which
  would have to read and strip the first TPKT itself before
  handing a replaying stream to `RdpServer::run_connection`.
- **No RD Gateway.** `ironrdp-mstsgu` is client-only.
- **Concurrency.** `RdpServer::run` serves one connection at
  a time. On main `RdpServer` is `!Send`, so it needs a
  `LocalSet` or its own thread; check 0.13.0.
- **Metadata.** MIT OR Apache-2.0. Edition 2024. MSRV 1.89
  for 0.13.0 and 1.94 on main; ryll's workspace declares
  1.88. Nearly every minor release has broken the server API
  (17 releases since November 2024).

### qemu-rdp as prior art

`qemu-rdp` feeds `ironrdp-server` from qemu's D-Bus display at
a pinned IronRDP git revision. Lessons worth taking:

- It **coalesces updates in a bounded queue** (capacity 30):
  only the latest pointer position and shape survive, a
  resize drops pending bitmaps, and overlapping bitmaps are
  split and masked. We need the same back-pressure story: a
  slow RDP client must never back up the SPICE side.
- It turned off `PointerPosition` updates because they
  "create weird effects on the client". Expect the same; send
  pointer shape and hide/show, and let the client own the
  position in client mouse mode.
- It **drops the `extended` key flag**, which breaks arrows,
  right Ctrl and right Alt. Do not copy that.
- It ignores lock-key `Synchronize` and Unicode events, maps
  the wheel to button presses, and does text-only clipboard
  and Opus-then-PCM audio. Its README says Opus "works with
  some clients, like FreeRDP", which suggests mstsc needs
  PCM.

### Why not something else

- **Attach to qemu's D-Bus display like qemu-rdp does.** Only
  reachable on the hypervisor host. Ryll reaches consoles
  through SPICE, including through Kerbside, Proxmox and
  oVirt, none of which will hand out a D-Bus socket. If
  Kerbside's "Son of SPICE" D-Bus helper ever ships, RDP from
  that helper is a natural follow-on, but it is not this plan.
- **VNC out.** Simpler to serve, but a network that blocks
  SPICE's port almost always blocks 5900 as well, so it
  solves the reachability problem for almost nobody.
- **A separate repository** (the original "doaki" idea). Most
  of the early work is renderer change that `--web` also
  wants, and in a separate repository every renderer change
  becomes a ryll PR, a release or a git pin, and a bump.
  Inside ryll it is one change. The RDP code still lives in
  its own crate, so it can move later.

### Coordination with the Kerbside spike

Kerbside's `PLAN-spice-performance.md` phase 3 is a spike that
terminates the SPICE display channel inside Kerbside with
ryll's renderer and re-emits SPICE. It is in progress (steps
1a and 1b have landed). Its step 3a is a ryll change that
includes **a slim renderer feature set** (gap 3 above), and
its step 3b accumulates **damage rectangles** from
`SurfaceMirror` events inside Kerbside (gap 1).

On 2026-10-19 we decided to pursue this plan now rather than
after the spike, and to have it do the renderer work the two
share, so that it moves the spike forward rather than
competing with it:

- **Phase 1 of this plan takes over the "slim features" part
  of spike step 3a**: depending on the renderer for display
  decode without usbredir, webdav, local audio output or
  openh264, reporting the resulting dependency tree, and
  saying so if openh264 cannot be separated cleanly from
  stream decode (MJPEG decode must remain). Step 3a keeps its
  Kerbside-only parts: server-role display serialisers, the
  LZ4 and JPEG encoders, and a configurable display channel.
  Kerbside's phase 3 plan has been updated to match.
- **Damage accumulation goes in the renderer**, not in either
  consumer, so the spike's step 3b uses phase 1's accumulator
  instead of writing its own in Kerbside.
- **The spike does not gate this plan, and this plan gates
  only step 3b of the spike.** If step 3a reaches the
  renderer's `Cargo.toml` first, phase 1 builds on whatever
  it did.
- **The spike's go/no-go verdict does not gate this plan.**
  That verdict is about whether Kerbside should transcode
  SPICE for latency. This plan is about reachability, and
  ryll already ships a transcoder in `--web`, so renderer
  accuracy is largely established by dogfooding. If the
  spike's CPU measurements say transcoding is too expensive
  per session, that matters to the eventual move into
  Kerbside (capacity, placement) more than to a single-user
  `ryll --rdp`.

### Feature mapping

| Feature | SPICE side | RDP side | Phase |
|---------|------------|----------|-------|
| Display | display channel via the renderer, damage from phase 1 | bitmap updates (planar, RemoteFX) | 2 |
| Keyboard | inputs channel, Set 1 scancodes | fast-path scancode with an extended flag | 2 |
| Lock keys | inputs channel modifiers | `Synchronize` | 2 |
| Unicode key events | none | `UnicodePressed`, sent by mobile clients | Open question 5 |
| Mouse | client mode (absolute) needs vdagent or a tablet; server mode is relative | absolute pointer, wheel | 2 |
| Cursor shape | cursor channel | `RGBAPointer`, `HidePointer` | 3 |
| Resize | vdagent monitors config | display control | 3 |
| Clipboard | vdagent over main, `MemoryClipboard` | cliprdr, text | 4 |
| Audio out | playback channel | rdpsnd, Opus or PCM | 4 |
| H.264 | none needed on the SPICE leg | EGFX AVC420 | Future work |
| Multiple monitors | several display channels | multi-monitor layouts | Future work |
| Microphone | record channel | audio input | Future work |
| USB, folder sharing, smartcard | usbredir, WebDAV, smartcard | rdpdr and friends | Not planned |

## Mission and problem statement

Add a `ryll --rdp` mode that serves one SPICE session to a
stock RDP client (mstsc, the Windows App, FreeRDP, Remmina).
The user gets a usable desktop: display, keyboard, mouse,
cursor, resize, text clipboard and audio playback. Specifically:

1. Close the renderer gaps that every non-egui frontend
   shares (damage, pointer translation, a slim build, a
   device-free audio path), so `--web` and a future Kerbside
   gateway benefit too.
2. Put the IronRDP glue in a new library crate,
   `shakenfist-spice-rdp`, alongside `shakenfist-spice-webrtc`,
   with an API that is not tied to ryll's CLI. A Kerbside
   gateway should later be a thin binary over it.
3. Hide how a connection is mapped to a SPICE session behind
   a small seam. In ryll that is "the one session this process
   was started with"; in Kerbside it will be "what the console
   token says".
4. Answer the questions only a real RDP client can answer,
   with evidence: what mstsc negotiates for security and
   codecs, how both mouse modes feel, and what the extra hop
   costs in latency and CPU.
5. Test it in CI with a real RDP client, and record it in
   `docs/multi-mode-parity.md` as a fourth mode.
6. Document it as a reachability fallback, with its costs and
   gaps.

## Open questions

1. **Which IronRDP?**
   - 0.13.0 from crates.io is publishable (crates.io crates
     cannot depend on git revisions) and needs MSRV 1.89.
   - `main` has things we would like (the reshaped mouse
     events with horizontal scroll and relative moves, the
     credential-origin validator, `LargePointer`, the EGFX
     H.264 encoder trait) but needs MSRV 1.94, a git pin, and
     `publish = false` on our crate until IronRDP releases.
   - Either way, expect to absorb breaking changes on every
     IronRDP release; keep the IronRDP-facing code in one
     crate so the churn stays there.
   - **Recommendation:** start on 0.13.0, raise the RDP
     crate's `rust-version` to 1.89 (per crate, leaving the
     workspace default alone if possible), and move to the
     next release when it ships. Phase 2 confirms 0.13.0 is
     enough for the MVP; if it is not, it says which `main`
     feature forced the switch.
2. **Security default.**
   - Both TLS and NLA work, against one credential pair.
   - Ryll's `--web` already mints a per-launch token and
     prints it. The natural equivalent is a generated
     password (and a fixed username such as `ryll`), printed
     at startup, with flags to set them explicitly.
   - **Recommendation:** offer Hybrid (NLA) and TLS together
     so the client chooses, which is what mstsc expects;
     `--rdp-security tls` forces TLS-only for clients that
     mishandle CredSSP. Reuse `--web`'s `--web-tls-cert` /
     `--web-tls-key` handling. Unlike `--web`, which can serve
     plaintext HTTP, RDP always needs TLS, so with no
     certificate given `--rdp` generates a self-signed one at
     startup (`rcgen`, today a test-only dependency of the
     binary) and prints its fingerprint. Phase 2 tests what
     mstsc does with it.
3. **Server mouse mode.**
   - RDP pointers are absolute. SPICE absolute input needs
     client mouse mode, which the server grants only with
     vdagent or a tablet device. Cloud images without vdagent
     are common.
   - In server mode ryll must synthesise relative motion
     from absolute positions, which gives the familiar "two
     cursors" experience unless we hide the client's cursor.
   - **Recommendation:** support it degraded rather than
     refuse it, using the shared pointer translator from
     phase 1, and say so in the docs. If the pinned IronRDP
     offers relative-mouse input, try it in phase 2.
4. **One client at a time, or takeover?** `RdpServer::run`
   serves one connection at a time, and one SPICE session can
   only sensibly have one driver. **Recommendation:** a new
   authenticated connection takes over and the old one is
   disconnected, which matches `--web`'s bridge replacement
   and the way RDP's own session reconnection behaves.
5. **Unicode key events.** Mobile clients and some IMEs send
   `UnicodePressed` with no scancode. **Recommendation:**
   drop them with a `warn_once!` in the MVP; map the ASCII
   range through the paste-as-keystrokes translator later,
   which already turns characters into scancodes.
6. **Audio formats.** rdpsnd takes whatever we advertise.
   Opus passthrough via `OpusPacketSink` costs nothing, but
   reportedly only some clients take Opus. PCM needs decoded
   samples without opening a local device (gap 4).
   **Recommendation:** advertise Opus and PCM 48 kHz stereo,
   and let the client pick; phase 4 checks what mstsc picks.
7. **Is a bitmap codec good enough?** Planar and RemoteFX are
   fine for desktops and terminals and poor for video, which
   ryll would decode and re-encode. **Recommendation:**
   measure in phase 2 (bytes per second and CPU for a typing
   session, a scrolling page and a video), and promote EGFX
   H.264 from Future work to a phase only if the numbers say
   users need it.
8. **Packaging.** Should the `rdp` feature be on in release
   builds (deb, rpm, Homebrew, the PyPI wheel)?
   **Recommendation:** off by default until the CI lane in
   phase 5 is green and stable, then on for Linux builds.
   macOS and Windows builds follow once someone has tried
   them.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Renderer prep: damage, pointer translation, slim build, device-free audio | - | Not started | |
| 2. RDP skeleton: `shakenfist-spice-rdp`, `--rdp`, display, keyboard, mouse, security | - | Not started | |
| 3. Cursor and resize | - | Not started | |
| 4. Clipboard and audio | - | Not started | |
| 5. Lifecycle and CI: reconnect, takeover, an `rdp-smoke` lane | - | Not started | |
| 6. Documentation and parity | - | Not started | |
| 7. Push audit | - | Not started | |

Phase plans are written one at a time with `/next-phase`.
Phase 1 can start now; Kerbside spike step 3b waits for it
(see *Coordination with the Kerbside spike*). Phases 3 and 4
can run in parallel once phase 2 has merged. Phase 5 can start
once phase 2 has merged and should, so that phases 3 and 4
land against a working lane; it then extends its assertions
as they arrive.

### Phase 1: Renderer prep

No RDP code yet. Close renderer gaps 1 to 4, including the
slim feature set taken over from Kerbside spike step 3a:

- **Damage.** `SurfaceMirror::apply_event` (or the
  `DisplaySurface` draw ops underneath it) records the
  rectangle each draw touched, in surface coordinates, and a
  consumer takes the accumulated region with one call, the
  way `consume_dirty` works for the bool today. Union nearby
  rectangles so the region stays small; bound it (fall back
  to "whole surface" past N rectangles). `RealFrameSource`
  keeps working unchanged.
- **Pointer translation.** Extract the absolute/relative
  logic from `ryll/src/web/inputs.rs` and `ryll/src/app/`
  into one renderer type, and switch both callers to it.
- **Slim build.** Features for the encoder (openh264),
  `usbredir`, `webdav` and local audio output, so a server
  frontend (RDP here, the Kerbside transcoder there) can
  depend on display decode alone. Report the resulting
  dependency tree in the phase plan. If openh264 cannot be
  separated cleanly from stream decode, say so; MJPEG decode
  must remain. Check that `--web`, GUI and headless builds are
  unchanged.
- **Device-free audio.** A playback mode that hands the sink
  Opus packets and, optionally, decoded PCM, without opening
  a cpal device. `--web` should use it too, since a
  server-side web session has no use for local audio either.
- A small helper that spawns the forwarder and mirror-apply
  tasks `run_web` writes by hand, so RDP does not copy them.

**Acceptance:** existing tests pass on all platforms; a
renderer build with default features off and only display
decode enabled compiles without openh264, nusb, dav-server or
cpal (or the phase plan records why one of them remains); new
unit tests cover damage accumulation (including `CopyBits`,
whose destination is the damage, and the bound) and the
pointer translator in both modes; `--web` uses the new pieces
with no behaviour change.

### Phase 2: RDP skeleton

The MVP, and the point at which this plan learns most:

- A new crate `shakenfist-spice-rdp`, depending on
  `ironrdp-server`, structured like `shakenfist-spice-webrtc`:
  a library with no CLI knowledge. It implements
  `RdpServerDisplay` over the mirror and phase 1's damage,
  and `RdpServerInputHandler` over the renderer's input
  sender and pointer translator.
- **A bounded, coalescing update queue** between the mirror
  and IronRDP, as qemu-rdp has, paced by the client: damage
  accumulates while the client is behind, and superseded
  pixels are never sent. A slow RDP client must not back up
  the SPICE side.
- **A session seam:** a trait that turns an accepted,
  authenticated connection into the session to serve. Ryll's
  implementation returns the one session it started with.
- `ryll --rdp` in the binary behind an `rdp` cargo feature:
  `--rdp-host`, `--rdp-port` (default 3389, falling back to
  ephemeral if it cannot bind), the credential and security
  flags from open question 2, and `--web`'s TLS flags. It
  prints the address and credentials like `--web` prints its
  URL.
- Keyboard with the extended flag, lock-key `Synchronize`,
  mouse buttons and vertical wheel, in both mouse modes.
- A manual test against the local QEMU test server with
  FreeRDP and, if one is available, mstsc. **Record the
  results in this plan:** negotiated security, negotiated
  codec, latency and CPU for the three workloads in open
  question 7, and any client that would not connect.

**Acceptance:** FreeRDP connects, authenticates and drives
the guest's desktop with keyboard and mouse; the results
above are recorded; open questions 1, 2, 3 and 7 have
evidence-based answers in the plan.

### Phase 3: Cursor and resize

- Cursor shapes as `RGBAPointer` (larger than 96x96 needs
  `LargePointer` or scaling), hide as `HidePointer`. Leave
  `PointerPosition` out unless testing shows a reason, per
  qemu-rdp's experience.
- Decode mono and 4-bit SPICE cursors in the renderer (gap 6),
  which also fixes them in the other modes.
- Resize: the client's initial desktop size via
  `request_initial_size`, then display-control layouts via
  `request_layout`, both mapped to `DisplaySize::Requested`
  (aligned as the GUI aligns them). The guest's answer, a
  `SurfaceCreated` of a new size, becomes
  `DisplayUpdate::Resize`. Without an agent, the client gets
  the guest's fixed size.

### Phase 4: Clipboard and audio

- Text clipboard both ways between cliprdr
  (`CF_UNICODETEXT`) and `MemoryClipboard`. Rich formats and
  files are Future work.
- Audio through rdpsnd, from phase 1's device-free playback:
  Opus passthrough where the client picks Opus, PCM where it
  does not. Record what mstsc and FreeRDP pick.

### Phase 5: Lifecycle and CI

- SPICE reconnect while an RDP client stays connected (show
  the client something sensible meanwhile), RDP client
  disconnect and reconnect, the takeover rule from open
  question 4, and clean shutdown on Ctrl+C. Follow
  `docs/session-lifecycle.md` and the `--web` reaper's
  patterns.
- An `rdp-smoke` lane modelled on `web-smoke`
  (`tools/web-smoke.sh`, `make web-smoke`): FreeRDP, headless
  under Xvfb or with a screenshot option, against the local
  QEMU test server; assert that it authenticates, that a
  frame arrives, and that an input event reaches the guest.
  Start it advisory, and decide whether it joins the smoke
  tier once it has a flake record.

### Phase 6: Documentation and parity

- `docs/rdp-frontend.md`, an operator guide matching
  `docs/web-frontend.md`: flags, credentials, TLS, which
  clients were tested, what is lost, and the "fallback, not a
  better way" framing.
- An RDP column in `docs/multi-mode-parity.md`, with every
  cell filled and intrinsic gaps justified.
- `docs/configuration.md` for the new flags,
  `ARCHITECTURE.md` for the new crate and mode (the shape of
  the system changes), and a README link and usage example.
- `docs/rdp-mode-internals.md` if the internals warrant it,
  as `docs/web-mode-internals.md` does for `--web`.

### Phase 7: Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1 to
6, using the merge commits recorded in the `Merged` column,
as `PLAN-TEMPLATE.md` describes.

## Agent guidance

Phases follow the sub-agent execution model, planning-effort,
step-guidance, model-roster and review-checklist conventions
in `PLAN-TEMPLATE.md`: implementation by sub-agents, review
and commits in the management session.

- **Planning effort.** Phases 1 and 2 are planned at high
  effort: phase 1 changes renderer internals every mode
  depends on and must coordinate with Kerbside's spike, and
  phase 2 turns on RDP security negotiation, IronRDP's
  concurrency model and back-pressure. Phases 3 to 6 follow
  patterns the earlier phases set, and are planned at medium
  effort, except that phase 5's reconnect work should be
  planned at high effort if it touches the session
  orchestrator.
- **IronRDP claims.** Every brief that relies on an IronRDP
  behaviour names the version it was checked against, and
  the sub-agent re-checks it in the pinned crate's source
  before relying on it.
- **Builds.** Rust builds run in Docker through the Makefile,
  never with a native toolchain on the host. A real RDP client
  for manual testing runs on the host or in a container;
  FreeRDP is packaged for Debian.

## Administration and logistics

### Success criteria

We will know this plan has been implemented successfully
because the following statements will be true:

* `ryll --rdp connection.vv` serves the session to FreeRDP
  and mstsc with display, keyboard, mouse, cursor, resize,
  text clipboard and audio playback.
* The `rdp-smoke` lane passes in CI.
* `docs/multi-mode-parity.md` has an RDP column with every
  cell filled.
* The RDP code is a library crate that a Kerbside gateway
  could depend on without the ryll binary.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck), `make test` passes,
  and new logic has unit tests.
* Documentation in `docs/` describes the mode as a
  reachability fallback; `ARCHITECTURE.md` reflects the new
  crate and mode; `AGENTS.md` changes only if a convention
  changed.
* The Kerbside documentation that mentions RDP out points
  here.

### Future work

#### Moving to Kerbside

The reason for building this as a library is that Kerbside
will want it. Kerbside's superseded `PLAN-rdp-gateway.md`
worked these out, and they are recorded here so they survive:

- **Process shape.** A separate gateway binary in Kerbside's
  Rust tree, one sandboxed process per session (seccomp, CPU
  accounting, a clean kill, a crash that takes down one
  session rather than the proxy), over `shakenfist-spice-rdp`.
  Open: does the proxy accept and authenticate the TCP
  connection and hand the socket to a worker, or does a
  spawned worker ask the proxy to authorise it? Accepting in
  the proxy keeps the listener, TLS and gRPC client in one
  place.
- **Credentials.** Username `<session_id>`, password
  `<token>` from `consoletoken.create_token`: stored,
  expiring, audited and revocable. NLA's NTLM needs the
  plaintext secret, which Kerbside has. mstsc cannot take a
  plaintext password from a `.rdp` file, so the user types
  the token; the web UI must show it, or exchange a shorter
  one-time code for it server-side.
- **Authorisation and audit.** One RDP connection becomes
  several backend SPICE channels. Either authorise once with
  a new `channel_type` and register the backend channels
  under the same `session_id`, or add an
  `AuthorizeRdpSession` RPC. Either changes the gRPC contract
  hash. The firewall policy's `permitted_channels` should
  decide which RDP virtual channels are enabled.
- **Control plane.** An off-by-default `VDI_RDP_PORT` and
  `PUBLIC_RDP_PORT`, TLS from the proxy's certificate, audit
  events matching the SPICE path's wording, registration in
  the session-termination registry so `TerminateSession`
  kills RDP sessions, per-protocol metrics, and a
  `console.rdp` endpoint beside the `.vv` one.
- **Routing.** If one listener serves many consoles, the
  `.rdp` file's `loadbalanceinfo` token must be read and
  stripped from the first TPKT before IronRDP's acceptor sees
  it (see the IronRDP notes above).
- **Placement and capacity.** Transcoded sessions cost CPU
  that relayed ones do not. Kerbside's spike asks how a
  saturated node behaves and how a multi-node deployment
  places sessions; the gateway inherits that answer.
- **Testing.** A FreeRDP lane through Kerbside against the
  direct-qemu harness, asserting with the existing QR digest
  oracle and the serial drain.
- **Framing.** Kerbside's `docs/index.md` pitches Kerbside as
  the way to avoid transcoding. RDP out must be documented
  there as an opt-in fallback, with SPICE relay staying the
  default.
- **The same question for `--web`.** Whether ryll's web mode
  should also move under Kerbside's control plane (Kerbside
  supervising transcoder workers, owning auth and routing) is
  worth deciding at the same time.

#### Other extensions

- **H.264 through EGFX** (AVC420 first), using ryll's
  existing encoder, if phase 2's measurements say video
  matters to the users this serves.
- **RD Gateway (MS-TSGU) over 443**, for networks that allow
  HTTPS and not 3389. IronRDP has only a client. `--web` is
  the other answer for HTTPS-only networks; which fits
  depends on whether the users there have an RDP client.
- **Multiple monitors**, once the renderer and IronRDP's
  acceptor both support more than one.
- **Microphone** (the record channel to RDP audio input).
- **Rich clipboard** formats and file transfer.
- **Auto-reconnect cookies**, so a client reconnecting after
  a network blip does not prompt again (main-only in IronRDP
  today).
- **Unicode key events** through the paste-as-keystrokes
  translator (open question 5).
- **VNC out**, only if a real network is found that allows
  VNC and not SPICE.

### Bugs fixed during this work

None yet.

### Documentation index maintenance

This plan has a row in `docs/plans/index.md` and an entry in
`docs/plans/order.yml`. Phase plans are linked from the index
row and from the Execution table, not from `order.yml`. When
every phase, including the push audit and any findings it
raised, is complete, set the index row to `Complete`.

### Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with it.
