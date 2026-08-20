# Web frontend for ryll (SPICE → browser transcoder)

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, the software framebuffer in
`src/display/surface.rs`, egui rendering, audio playback via
cpal, headless mode), and ground your answers in what the code
actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (SPICE protocol, QEMU, QXL, TLS/RSA, LZ/GLZ
compression, WebRTC, H.264/VP8/AV1 encoding, browser media
APIs, openh264, x264, webrtc-rs, rav1e, MediaSource Extensions,
Web Codecs API), research as needed to give a confident answer.
Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), `/srv/src-reference/spice/spice-html5/`
(the existing JavaScript browser client; useful for what *not*
to do as much as for prior art on the inputs/scancode mapping),
`/srv/src-reference/qemu/qemu/` (server-side SPICE in
`ui/spice-*`), and the existing `--capture` video-encode path
in `ryll/src/capture.rs` (already uses `openh264` and `mp4` and
is the closest in-tree precedent for an encoder pipeline).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the Execution section of this
document.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The motivating use case is desktop access from a web browser.
Today the operator runs Kasm Workspaces, which exposes a
Linux desktop session over RDP and uses Apache Guacamole to
transcode RDP into an HTML5 canvas with audio. Kasm works
fine, but it is a third-party stack: every desktop session
goes through RDP (foreign to the shakenfist universe) and
Guacamole (a Java service the operator does not otherwise
use). If ryll could play the same role for SPICE, the
operator could:

1. Run an `xspice` (or QEMU+SPICE) session on the dev desktop.
2. Point a ryll-flavoured transcoder at it.
3. Open the URL the transcoder prints to its console in
   any browser and get the same experience as Kasm —
   keyboard, mouse, audio, over the LAN or VPN.

The result is a fully shakenfist-native VDI loop: SPICE end
to end, with the browser as just another ryll display target.

### What ryll already gives us

The decode side of a SPICE→browser transcoder is essentially
done. Specifically:

- **Software framebuffer.** `DisplaySurface` in
  `ryll/src/display/surface.rs:13` owns an RGBA `Vec<u8>` and
  every SPICE draw op (`blit`, `fill_rect`, `copy_bits`,
  `blit_alpha`, `blit_chroma`, `invert_rect`, `fill_solid`)
  mutates that buffer directly in software. egui's role
  reduces to wrapping the buffer in a `ColorImage` at
  `surface.rs:465` and painting one textured quad per
  surface. The framebuffer is the universal substrate
  today; egui is one consumer of it. Adding a video encoder
  as a *second* consumer is the entire architectural move.
- **Dirty tracking.** `DisplaySurface::is_dirty()` already
  exists at `surface.rs:499` as a single bool. Per-rectangle
  dirty regions are not tracked yet but are a small
  extension of the existing draw-op call sites; this matters
  for partial-frame encodes and is deferred to future work.
- **Multi-surface, multi-monitor.** Multiple `DisplaySurface`
  instances are already maintained, indexed in `app.rs:207`
  by `(channel_id: u8, surface_id: u32)`, and the
  `--monitors N` flag drives multi-head configurations. Each
  maps cleanly to a separate video track, although surfaces
  and monitors are loosely coupled in the SPICE model
  rather than 1:1.
- **Audio pipeline.** `ryll/src/channels/playback.rs` already
  decodes the SPICE playback channel (raw PCM and Opus) and
  feeds a cpal output stream via a lock-free ring buffer.
  Today the Opus decode happens unconditionally before
  enqueue, so a pre-decode tap point is required to enable
  Opus passthrough — see Resolutions §5.
- **Inputs channel.** `ryll/src/channels/inputs.rs` already
  marshals SPICE keyboard scancodes and pointer events. The
  GUI sends pre-translated `InputEvent` enums into the
  channel. The transcoder needs only to deliver browser-side
  scancodes through the same path; see Resolutions §6.
- **Cursor channel.** `ryll/src/channels/cursor.rs` already
  parses SET / INIT / MOVE messages and produces a renderable
  cursor image, painted today as an egui overlay (a separate
  texture, not composited into the framebuffer).
- **Headless mode.** Already used for cadence/automated
  testing. The GUI/headless split is a clean dispatch in
  `main.rs` (line ~143–161) — `args.headless` calls
  `run_headless()`, otherwise `run_gui()` calls
  `eframe::run_native()`. Headless does not instantiate
  `RyllApp`. The existence of headless mode is also evidence
  that ryll was architected to support more than one
  frontend, which makes the web frontend a natural extension
  rather than a retrofit (see *Design philosophy* below).
- **Reconnect.** The reconnect lifecycle introduced for the
  GUI applies equally to a web session — a transcoder
  process can drop per-session state and re-handshake without
  exiting. For the web frontend the reconnect boundary is
  the WebRTC `RTCPeerConnection`, *not* the SPICE channels:
  the SPICE session stays up while we wait for the browser
  to re-attach.
- **`--capture` precedent.** `ryll/src/capture.rs` already
  pulls the dirty framebuffer through `openh264` into an
  `mp4` file. This is the closest existing analogue to the
  encoder half of the web pipeline; lessons (frame pacing,
  encoder lifecycle, Cargo feature gating) carry over.

### What is missing

1. **A video encoder running in real time, driven by
   framebuffer mutation events instead of a wall-clock
   timer.** `--capture` writes a file at fixed cadence;
   live streaming wants something closer to "encode on
   dirty, pace at 30 fps, force a keyframe on request".
2. **A browser-facing transport.** WebRTC, chosen for
   simultaneous low-latency video + audio + datachannel; see
   Resolutions §3.
3. **A browser shell.** Static HTML+JS that establishes the
   transport, attaches the video to a `<video>` element,
   captures keyboard/mouse, renders the cursor as a CSS
   overlay, and relays input events back. Small but real
   piece of work; served by the same Rust binary via
   `include_bytes!`.
4. **A small HTTP server.** Serves the browser shell, the
   signalling endpoint, and validates a per-launch URL
   token. Plain HTTP — the browser only *receives* media (no
   `getUserMedia`), and `RTCPeerConnection`, keyboard, and
   pointer events all work in non-secure contexts. HTTPS is
   deferred until a feature that demands a secure context
   lands (clipboard sync, Pointer Lock on Chrome; see
   Resolutions §8).
5. **Cleaner separation between the SPICE substrate and
   the egui frontend.** `DisplaySurface`, the channel
   handlers in `ryll/src/channels/`, and the per-session
   orchestration today live inside the ryll binary crate.
   The good news: they are *already* decoupled at the type
   level — `egui::Context` is only referenced inside
   `app.rs`, and channel/display code communicates with the
   GUI via `mpsc ChannelEvent` enums. Extraction to a
   library crate is therefore mostly about *moving files*
   rather than *rewriting them*. See Resolutions §1 and
   Phase 1.

### Design philosophy: ryll is a multi-modal SPICE client

This plan formalises something that has been implicit in the
codebase since headless mode was introduced: **ryll is
intended to be a multi-modal SPICE client, not a
GUI-with-a-test-harness.** The ambition is that every
delivery mode is a first-class citizen and shares as much
functionality as the mode itself can physically support.

After this plan lands the supported modes are:

| Mode | Frontend | Primary use |
|------|----------|-------------|
| GUI | egui / eframe desktop window | Interactive day-to-day VDI access from the operator's own machine |
| Headless | none (stdout + metrics) | Automated testing, CI, cadence latency probing, scripted USB / WebDAV scenarios |
| Web (planned) | Browser via WebRTC | Interactive VDI access from any browser on the LAN, replacing Kasm + Guacamole |

The implication for design and review work going forward
is that **a feature is not "done" when it works in the GUI**;
it should also be reachable from headless and (once it
exists) from the web frontend, modulo features that are
intrinsic to one mode (e.g. egui-specific UI panels, or
browser-only clipboard APIs). When a feature *cannot*
exist in a given mode, the docs should say so explicitly
rather than leaving the gap unstated.

Today's codebase does not actually live up to this rule:
many features grew up GUI-first and have only partial (or
no) headless equivalents. The web frontend cannot meaningfully
be planned without first knowing where the GUI/headless
parity already drifts — otherwise the web frontend will just
inherit those gaps silently. Hence the dedicated audit
phase below (Phase 0). The audit produces a feature × mode
matrix (GUI / headless / web-planned) that:

- becomes input to the web-frontend phase plans (so each
  feature is delivered to the web alongside any catch-up
  work the other modes need);
- doubles as the to-do list for an independent
  **driving-down-the-gaps** workstream that does not need
  to wait for the web frontend to land.

Concrete consequences for this plan:

- **Phase 0 (parity audit) and Phase 1 (renderer extraction)
  are independent.** Phase 0 is a read-only artifact;
  Phase 1 is a code refactor. They may run in either order
  or in parallel. Both must land before the web-specific
  phases that depend on them (Phase 5 onward for Phase 0;
  Phase 2 onward for Phase 1).
- **Phase 1 (renderer extraction) is non-negotiable.** It
  turns "GUI vs headless" from a top-level branch in
  `main.rs` into a thin frontend layered over a shared
  library. Once extracted, the web frontend becomes "third
  consumer of the same library" rather than "second copy
  of the channel handlers".
- **Feature parity is a planning input, not an
  afterthought.** Each phase plan should explicitly call
  out which features it adds to the web frontend, and
  whether the GUI and headless paths need follow-up work
  to retain or regain parity. The Phase 0 matrix is the
  reference.
- **Documentation always names the mode.** Feature lists
  in the README, ARCHITECTURE, and AGENTS files should
  identify which modes a feature is available in, so the
  parity gaps are visible to operators and contributors.

### Why not something else

- **Apache Guacamole does not support SPICE** and never has.
  It supports RDP, VNC, SSH, telnet, and Kubernetes consoles.
  SPICE has been an open feature request on its issue
  tracker for years with no upstream interest. Adding SPICE
  to Guacamole means writing a new protocol module in Java
  for the `guacd` daemon, which is roughly the same scope
  as the work proposed here, in a stack the operator does
  not otherwise touch.
- **`spice-html5`** (the canonical JavaScript SPICE client at
  `/srv/src-reference/spice/spice-html5/`) is essentially
  unmaintained. It implements only a subset of the protocol
  in JavaScript — no audio, no Opus, no LZ4, no QUIC, no
  modern QXL draw ops (`DRAW_COMPOSITE` etc.), marginal
  cursor handling, no USB redirection. Bringing it up to
  parity with ryll means reimplementing in JS most of what
  ryll already does in Rust, with the result still running
  the heavy decode in a browser tab. The decode side has
  been done once; doing it again in a slower language is
  a poor trade.
- **VNC.** Cleanest native browser story (noVNC is mature),
  but VNC drops audio, USB redirection, and shared folders,
  and the operator's desktops already speak SPICE.
- **RDP via xrdp.** Works today via Kasm/Guacamole. Adopting
  SPICE end-to-end is the *whole point*: dogfood ryll, drop
  the Java service, keep one protocol family.

### Operational shape

Initial deployment is single-user, single-session, LAN
(or Tailscale) only:

- One `ryll` process running in `--web` mode runs on the
  desktop being shared.
- It is launched the same way `ryll` is today — a `.vv`
  file (or CLI flags) points at the SPICE server, so the
  operator-side connection is already authenticated. The
  only difference from a normal launch is the `--web`
  flag (matching the precedent of `--headless`).
- It listens for one browser at a time on plain HTTP, on a
  random ephemeral port chosen at launch. The full URL,
  including a per-launch random token (`http://<host>:<port>/?token=<random>`),
  is printed to stdout, the same way `jupyter notebook`
  advertises itself.
- There is **no** TLS in MVP. The threat model is "the
  operator runs this on a trusted LAN and copies the URL
  into their own browser"; the per-launch token defeats
  casual port-scanning, and HTTPS is deferred until a
  feature that demands a secure context lands. See
  Resolutions §8 and §9.
- Browsers will refuse a couple of nice-to-have APIs over
  plain HTTP (Pointer Lock on Chrome requires a secure
  context, async clipboard requires a secure context), but
  none of those are MVP features.

Multi-user / multi-session / TURN-relay / hosted-fleet
shapes are explicitly future work — they are interesting
but they are *also* the place Kasm is currently strong, and
chasing them now would distort the MVP.

## Mission and problem statement

Add a `--web` mode to the existing `ryll` binary (and ship
an accompanying browser shell) that lets the operator
launch `ryll --web session.vv`, copy the printed
`http://<host>:<port>/?token=<random>` URL into a modern
browser, and interact with the SPICE-attached desktop with
parity to the GUI mode for the basics: display, audio,
keyboard, mouse, cursor. The single-binary stance is
deliberate: GUI, headless, and web are all runtime modes of
the same `ryll` (matching the precedent set by `--headless`),
keeping with the multi-modal philosophy declared above and
in `README.md` / `ARCHITECTURE.md`.

MVP scope:

1. New `--web` mode in the existing `ryll` binary,
   selected at startup the same way `--headless` is.
   Builds on Linux x86_64 with the existing toolchain;
   macOS / Windows builds get the mode "for free" since
   the binary is shared (cross-platform encoder
   availability is incidental — we use `openh264`, which
   is already cross-platform).
2. Connects to a SPICE server using the same `.vv` /
   CLI-flag plumbing as the other modes. The `.vv` file
   is consumed by `ryll` at launch — the browser never
   sees it.
3. Listens on plain HTTP on an ephemeral port chosen at
   launch and prints the full URL (including a per-launch
   random token) to stdout. Serves a static HTML+JS shell
   from that endpoint, gated behind the token.
4. Streams the SPICE display to the browser as a single
   video track (one monitor for MVP), pacing at a 30 fps
   cap with frame-skip when the framebuffer has not
   changed and a forced keyframe on first attach and on
   reconnect.
5. Streams SPICE audio to the browser. When the SPICE
   server negotiated Opus, forward Opus packets directly
   into the WebRTC audio track without re-encoding (the
   common case). When the server only offers raw PCM, fall
   back to encoding PCM → Opus via `audiopus`.
6. Captures keyboard and mouse in the browser and
   forwards them through the SPICE inputs channel. The
   browser builds `KeyboardEvent.code` → AT scancode
   itself; the Rust side relays raw scancodes unchanged.
7. Renders the SPICE cursor as a CSS / `<img>` overlay
   driven by datachannel updates, reusing the parsed
   cursor image already produced by
   `ryll/src/channels/cursor.rs`. Cursor latency is
   independent of the video encoder pipeline.
8. Reconnect-on-disconnect: if the browser tab closes or
   the `RTCPeerConnection` drops, the SPICE session is
   held open for ~30 seconds so re-opening the same URL
   resumes against the same SPICE session. The reconnect
   boundary is the PeerConnection layer, *not* the SPICE
   channel layer.
9. Documents how to launch `ryll --web` from a `.vv` file
   and open the printed URL.

Out of MVP scope (tracked in Future work):

- Multi-monitor (one video track in MVP; multi-track is a
  natural extension).
- USB redirection (browser USB story is fragile and
  Chrome-only).
- Folder sharing (the WebDAV channel as ryll uses it
  shares a *local* directory with the guest; "local" in a
  browser is ambiguous).
- Clipboard sync between browser and guest (vdagent
  clipboard).
- Hardware-accelerated encoding (NVENC / QSV / VAAPI).
- Multi-tenant / hosted / multi-session fleet.
- TURN servers / WAN traversal beyond what STUN gets us.
- TLS / HTTPS for the browser-facing endpoint.
- Login UI, OIDC, mTLS — anything beyond the per-launch
  URL token.
- Mobile-browser UX polish (touch gestures, on-screen
  keyboard).
- Recording / capture from the web side.
- Per-rectangle dirty tracking for partial-frame encodes.
- AV1 encode (`rav1e` / SVT-AV1).

## Resolutions

The original concept document carried 15 open questions. The
operator and the planning session resolved each before any
phase plan was written. The resolutions are recorded here
in the same numbering for traceability with prior reviews.

1. **Substrate code organisation.** Extract a new
   `shakenfist-spice-renderer` library crate containing
   `DisplaySurface`, the per-channel handler structs, and
   the per-session orchestration code. The substrate is
   already decoupled from egui at the type level
   (`egui::Context` is only referenced inside `app.rs`;
   channel and display code communicate with the GUI via
   `mpsc ChannelEvent` enums), so extraction is mostly file
   movement rather than rewriting. The dual-spec
   `path = "..", version = ".."` Cargo pattern used by the
   sibling `shakenfist-spice-{protocol,compression,usbredir}`
   crates applies. **Cargo features**: not gated in MVP — ship
   one fat binary first, revisit feature gating once the
   webrtc/encoder dep weight is concrete.

2. **Encoder.** `openh264`. Already a dependency of the
   `--capture` mode (`ryll/src/capture.rs`), pure-Rust
   bindings, BSD-licensed, well-understood lifecycle. **VP8
   contingency.** Phase 3 must explicitly verify that
   `webrtc-rs` packetises `openh264`-produced H.264 NAL
   units correctly into RTP for the major browsers; if that
   integration is painful, fall back to VP8 via
   `vpx-encode` as a single Phase 2 commit. The contingency
   is *not* a goal — it is a pre-flight to avoid Phase 2
   building NAL units that Phase 3 can't ship.

3. **Transport.** WebRTC via `webrtc-rs`. The only option
   that gives simultaneous low-latency video, low-latency
   audio, and a low-latency input return path inside one
   well-defined primitive. LAN-only assumption means STUN
   is sufficient and TURN is future work.

4. **Frame pacing.** 30 fps cap, encode-when-dirty within
   that budget, force keyframe on first frame and on
   reconnect. `DisplaySurface::is_dirty()` is a single bool
   today; per-rect tracking is future work.

5. **Audio path.** Opus passthrough preferred. Today
   `playback.rs` decodes Opus to PCM unconditionally before
   enqueue into the `rtrb` ring buffer; the web mode needs a
   *pre-decode tap* that captures the raw Opus packet
   stream and forwards it to the WebRTC audio track without
   re-encoding. Fallback: when the SPICE server negotiated
   raw PCM (mode 1), encode PCM → Opus via `audiopus` for
   the browser. This keeps quality and CPU optimal in the
   common case (Opus already in flight) without dropping
   support for PCM-only servers.

6. **Input scancode mapping.** The browser shell builds its
   own `KeyboardEvent.code` → AT-set-1 scancode table and
   sends raw scancodes over the datachannel. This is *not*
   an extension of the existing `char_to_scancode()` table
   in `inputs.rs:1016` — that table is char→scancode for
   paste and serves a different purpose. The browser-side
   table is fresh (~100 entries for ASCII letters, digits,
   shifted symbols, F-keys, arrows, modifiers, navigation
   keys). Pointer events go over the same datachannel as
   absolute coordinates.

7. **Cursor.** Datachannel-driven CSS overlay. Cursor
   shapes already parsed by `ryll/src/channels/cursor.rs`
   are forwarded over the datachannel and rendered in the
   browser as an `<img>` positioned over the `<video>`
   element. Cursor latency is bounded by the datachannel
   round-trip rather than the video encoder pipeline,
   giving a noticeably more responsive feel for desktop
   interaction. The cost is roughly 100–200 lines on each
   side; it does not require any new server-side parsing.

8. **HTTPS / TLS.** Plain HTTP for MVP. The browser only
   *receives* media; secure-context restrictions do not
   apply to the headline features. TLS is deferred until a
   feature that demands a secure context lands or until
   `--web` mode is exposed beyond a trusted LAN. When TLS
   does land, the proposed shape is a cert+key pair on the
   CLI with operator recipes for `mkcert` / `step-ca` /
   Let's Encrypt; ACME inside `--web` is further future
   work.

9. **Authentication.** Per-launch URL token in MVP. The
   `--web` mode generates a random 32-byte token at startup,
   embeds it in the printed URL as `?token=<token>`, and
   validates it on every HTTP request and on the WebRTC
   signalling exchange. This costs ~5 lines of Rust, defeats
   casual port-scanning, and matches the `jupyter notebook`
   user experience. No login UI, OIDC, or mTLS in MVP —
   those pair with the TLS work above. *Pointer Lock
   caveat*: Chrome requires a secure context for
   `requestPointerLock()`; Firefox does not. Without
   pointer lock, the browser can only deliver absolute
   pointer coordinates, which is fine for SPICE servers
   that have vdagent (the common case) but degrades
   relative-pointer use cases (games, drawing apps). MVP
   accepts this trade.

10. **Browser shell hosting.** Static HTML+JS bundle
    embedded in the binary via `include_bytes!`. The binary
    stays self-contained; the operator does not have to
    ship a sibling `static/` directory. There is no
    existing `include_bytes!` precedent in `ryll/src/` but
    it is a trivial Rust pattern.

11. **Multi-monitor in MVP.** Single monitor. The browser
    shell renders one `<video>` element bound to one WebRTC
    video track. The Rust side picks a primary surface —
    `(channel_id=0, surface_id=0)` if present, otherwise
    the lowest-keyed surface — and only encodes that one.
    Multi-monitor is the first post-MVP feature because the
    back end already supports multiple surfaces; it just
    needs one extra video track per surface.

12. **`xspice` vs QEMU+SPICE.** Agnostic. The `--web` mode
    is indifferent to what's behind the SPICE socket; the
    operator chooses based on what they want to share.

13. **Lifecycle and process supervision.** Ship a systemd
    unit example in `docs/web-frontend.md` and otherwise
    stay out of the supervision business. Long-running
    `ryll --web` processes are restarted by systemd on
    crash, on desktop reboot, and across SPICE-server
    restarts.

14. **CPU budget.** Instrument and measure in Phase 5.
    A 1080p30 encode in `openh264` "ultrafast" is roughly
    half a core under load. If the operator's desktop is
    also doing actual work, that may be too much. NVENC
    support (a future-work item) is the answer in that
    case, not "make the encoder smarter".

15. **Where the encoder lives.** Monolithic. The `--web`
    mode does encode + transport in one process; shared
    address space gives zero-copy access to the
    framebuffer. Multi-tenancy (one encoder, many
    transports) is a future-work shape, not an MVP
    constraint.

## Execution

The phase breakdown below derives from the resolutions
above. Per-phase plan files have not been written yet; this
master plan is the input to writing them.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Multi-mode parity audit (GUI vs headless today) | (executed inline; see `docs/multi-mode-parity.md`) | Complete |
| 1. Renderer extraction (`shakenfist-spice-renderer` crate) | [PLAN-web-frontend-phase-01-extract.md](/components/ryll/plans/PLAN-web-frontend-phase-01-extract/) | Complete |
| 2. Encoder pipeline (framebuffer → H.264 NAL units, with VP8 contingency) | [PLAN-web-frontend-phase-02-encoder.md](/components/ryll/plans/PLAN-web-frontend-phase-02-encoder/) | Complete (H.264 path; VP8 contingency not triggered) |
| 3. WebRTC plumbing (`webrtc-rs`, video track, audio track, datachannel) | [PLAN-web-frontend-phase-03-webrtc.md](/components/ryll/plans/PLAN-web-frontend-phase-03-webrtc/) | Complete |
| 4. HTTP server + token auth + signalling endpoint + browser shell | [PLAN-web-frontend-phase-04-server.md](/components/ryll/plans/PLAN-web-frontend-phase-04-server/) | Complete (synthetic source; real frames Phase 5) |
| 5. Inputs + cursor overlay + audio passthrough | [PLAN-web-frontend-phase-05-iac.md](/components/ryll/plans/PLAN-web-frontend-phase-05-iac/) | Complete |
| 6. Reconnect + lifecycle | [PLAN-web-frontend-phase-06-lifecycle.md](/components/ryll/plans/PLAN-web-frontend-phase-06-lifecycle/) | Complete (`7c6fa2fa` bridge dead signal, `a3aaa1b5` reaper + encoder stop, `e7c98f10` browser auto-reconnect, 6d docs) |
| 7. CI build + packaging | [PLAN-web-frontend-phase-07-ci.md](/components/ryll/plans/PLAN-web-frontend-phase-07-ci/) | Complete (`026a761e` publish-crates, `c6a0a94b` libopus-dev, `93ab083b` smoke test, `e59b8b13` deb/rpm deps, `efe9de60` docs sweep) |
| 8. Operator docs + systemd example | [PLAN-web-frontend-phase-08-docs.md](/components/ryll/plans/PLAN-web-frontend-phase-08-docs/) | Complete (`62ea23ba` native TLS/8a, `5da02936` systemd/8b, `9ba4df0f` TLS docs/8c, `adc5bed` kerbside cross-ref/8d in kerbside repo, this commit/8e) |

### Phase 0: Multi-mode parity audit

Survey the existing codebase and produce a single read-only
artifact, `docs/multi-mode-parity.md`, that lists every
user-facing ryll feature in a row and marks for each mode
(GUI / headless / web-planned) one of:

- **available** — feature is fully reachable in this mode;
- **partial** — only some of the feature is reachable
  (e.g. CLI flag exists but no runtime control);
- **missing** — feature is not reachable today;
- **n/a — intrinsic** — the feature physically cannot
  exist in this mode (justification required).

Source material: walk `ryll/src/`, every `--*` CLI flag,
the menu entries in `app.rs`, the side panels (USB,
Folders, Notifications, Traffic), the bug-report and
screenshot hotkeys, the cadence/paste-as-keystrokes
machinery, and the entries in `README.md`'s features list.
Cross-check against the ARCHITECTURE.md mode table added
alongside this plan. For every "missing" or "partial"
cell, link to the relevant source location so a follow-on
plan can be written without rediscovering the gap.

The audit deliberately does **not** propose fixes — it is
a baseline. Closing the gaps is tracked in a separate
follow-on plan (`PLAN-multi-mode-parity-driveup.md`,
written after Phase 0 lands) so the web-frontend phases
do not accidentally absorb headless-feature backlog work.
The artifact is expected to be a living document: when a
feature is added, its row is added; when a mode gains a
feature, the cell is updated; reviewers are expected to
keep it honest.

This phase is independent of Phase 1 and may run in either
order or in parallel.

**Acceptance:** the matrix exists, every README feature
appears in it, and every cell has a value.

### Phase 1: Renderer extraction

Pull `DisplaySurface`, the per-channel handler structs, and
the per-session orchestration code out of the `ryll` binary
crate into a new `shakenfist-spice-renderer` library crate
sitting alongside `shakenfist-spice-{protocol,compression,usbredir}`.
The egui frontend continues to live in `ryll` but as a thin
layer over the substrate; the headless mode does the same;
the (yet-to-exist) `--web` mode will join as a third peer in
later phases. No web-facing code yet — this phase is "prove
the existing GUI and headless modes still work after the
refactor, with all existing tests passing on all three
platforms".

If extraction-as-a-crate turns out messier than expected
(circular deps, lifetime headaches), fall back to in-place
refactor inside the `ryll` crate as a single commit — same
file motion, no new crate. The decision can be revisited
once the substrate is moved.

This phase is independent of Phase 0 and may run in either
order or in parallel.

**Acceptance:** `cargo test --workspace` passes,
`pre-commit run --all-files` passes, GUI and headless modes
work unchanged on Linux.

### Phase 2: Encoder pipeline

Add a `shakenfist-spice-encoder` module (inside the new
renderer crate) that takes a `&DisplaySurface`, encodes the
dirty framebuffer at the 30 fps cap, and emits NAL units.
Reuse the `openh264` lessons from `capture.rs`. Wire
keyframe-on-demand, since WebRTC needs a keyframe whenever a
new viewer attaches. No network code in this phase — feeding
the encoder from a test harness and dumping NAL units to a
file is the acceptance criterion.

**VP8 contingency.** Before declaring Phase 2 done, run a
small integration test through `webrtc-rs`'s H.264
packetiser to confirm browser playback works. If H.264
turns out painful, swap to VP8 via `vpx-encode` as a single
contingency commit. This pre-flight prevents Phase 3 from
discovering an integration block after Phase 2 has been
declared complete.

**Acceptance:** the encoder produces NAL units (or VP8
frames) that play in a browser when fed through a manual
`webrtc-rs` example harness.

### Phase 3: WebRTC plumbing

Bring up `webrtc-rs`. Build a dummy server that, given an
SDP offer over a local TCP socket, negotiates a
`PeerConnection` with one video track wired to the encoder
from Phase 2, one audio track wired to a synthetic Opus
stream, and a datachannel for inputs and cursor updates.
Acceptance: a manual test harness or the `webrtc-rs`
examples receive video, play audio, and round-trip
datachannel messages.

### Phase 4: HTTP server + signalling + browser shell

Add a tokio HTTP server (`hyper` or `axum`) bound to an
ephemeral port; generate a per-launch random 32-byte token
and print the resulting `http://<host>:<port>/?token=<token>`
URL to stdout at startup. Validate the token on every HTTP
request and on the WebRTC signalling exchange. Serve a
static HTML+JS bundle (embedded with `include_bytes!`),
expose a `POST /offer` endpoint for SDP exchange, and hand
the resulting `PeerConnection` off to the WebRTC machinery
from Phase 3. The browser shell is small — `<video>`,
`<img>` cursor overlay, `RTCPeerConnection`, keyboard/mouse
capture.

**Acceptance:** launch `ryll --web` with a `.vv` file, open
the printed URL in Firefox/Chrome, see the test pattern
from Phase 2 playing in the browser. Without the token, the
HTTP handler returns 401.

### Phase 5: Inputs, cursor overlay, audio

This phase delivers the three remaining wire-ups in three
separate commits:

- **5a Inputs.** Wire the browser-side keyboard/mouse
  handlers into the datachannel. Build the
  `KeyboardEvent.code` → AT-scancode table in JS. Plumb
  pointer motion through. On the Rust side, deliver
  scancodes and pointer events to the existing `inputs`
  channel handler. Browser-side resize events feed
  `maybe_send_monitors_resize()` (`app.rs:1539`-equivalent
  in the web frontend) so guest resolution can follow the
  browser viewport.
- **5b Cursor overlay.** Forward parsed cursor shapes from
  `ryll/src/channels/cursor.rs` over the datachannel; render
  in the browser as an `<img>` positioned over the
  `<video>`.
- **5c Audio.** Add the pre-decode tap point in
  `playback.rs` so Opus packets can be forwarded to the
  WebRTC audio track without re-encoding. When the SPICE
  server negotiated raw PCM, encode PCM → Opus via
  `audiopus`. RTP timestamps are derived from the SPICE
  audio packet timing so A/V sync survives the transport.

**Acceptance:** open the URL, the desktop is visible and
audible with no perceptible A/V skew, the cursor follows
guest cursor motion with no encoder-induced lag, and
typing/clicking work.

### Phase 6: Reconnect + lifecycle

Reconnect-on-disconnect: when the `RTCPeerConnection`
drops, hold the SPICE session open for ~30 seconds so the
browser can re-open the same URL and resume. The reconnect
boundary is the PeerConnection layer, *not* the SPICE
channel layer — SPICE channels stay alive; only the WebRTC
machinery is rebuilt on re-attach. Graceful shutdown on
SIGTERM (the existing `ctrlc` handling carries over).

**Acceptance:** close the browser tab, wait 10 seconds,
re-open the same URL, observe the same SPICE session
resumes without the SPICE server seeing a reconnect.

### Phase 7: CI + packaging

The existing `cargo build -p ryll --release` workflow
already produces the binary that hosts every mode, so
packaging-wise this phase mostly verifies that the new
dependencies (encoder, webrtc-rs, hyper/axum) build
cleanly on each platform and that the existing `.deb`
artifact still ships unchanged. macOS and Windows: confirm
`--web` mode at least *links* on each; runtime testing on
those platforms is future work since the operator's
deployment target is Linux.

### Phase 8: Docs

- New `docs/web-frontend.md` covering: what the `--web`
  mode is, how to launch it from a `.vv` file, where to
  find the printed URL (and the per-launch token), how to
  run it as a systemd service, troubleshooting WebRTC
  connectivity, and a clear *Security* note that the MVP
  listens on plain HTTP and is intended for trusted-LAN
  use only.
- `README.md` — flip the multi-modal table so the web
  mode moves from Concept plan to Shipping; add a brief
  pointer to `docs/web-frontend.md`.
- `ARCHITECTURE.md` — extend the multi-modal section to
  explain the renderer extraction and where the encoder +
  WebRTC transport sit in the data flow.
- `AGENTS.md` — note any new build-time considerations
  (encoder dependency setup, WebRTC native libs) and
  flag `--web` in the modes list.
- `docs/portability.md` — record that the `--web` mode is
  verified on Linux only for MVP, with a note that the
  encoder code is portable.
- `kerbside/docs/` — review whether kerbside's
  documentation should mention ryll's `--web` mode as a
  deployment pattern.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this conversation)
is reserved for planning, review, and decision-making. This
keeps the management context lean and avoids drowning it in
implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and
   model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files — the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was too
   light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with the
   result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental — Phase 1 (renderer extraction) and the
VP8 contingency in Phase 2 are the obvious candidates. For
safe, well-understood changes, sub-agents can work directly in
the main tree.

### Planning effort

The master plan itself was created at **high effort**: it
required cross-referencing the `channels/`, `display/`,
`capture.rs`, and `app.rs` source files, plus external
research on WebRTC / encoder / browser-API tradeoffs.

Per-phase planning effort recommendations:

| Phase | Planning effort | Rationale |
|-------|-----------------|-----------|
| 0 | medium | Mechanical survey; output structure is dictated by the matrix template. |
| 1 | high | Extracting a crate from a live codebase has many subtle invariants; needs careful module-graph thinking. |
| 2 | high | Encoder / packetiser interaction has nontrivial failure modes; the VP8 contingency needs to be planned in. |
| 3 | high | WebRTC SDP / ICE / DTLS / SRTP have many ways to go wrong even on a LAN. |
| 4 | medium | Conventional HTTP + WebRTC signalling pattern; main risk is token-validation correctness. |
| 5 | high (5c), medium (5a, 5b) | Audio passthrough requires touching the existing playback pipeline carefully; inputs and cursor are well-defined wire-ups. |
| 6 | medium | Reconnect semantics are well-defined; just enforce the layer boundary. |
| 7 | medium | Largely a CI/packaging change. |
| 8 | low | Docs, with the substance already nailed down by Phases 0–7. |

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**

- **high** — reading multiple files, judgment calls,
  non-obvious invariants, external-reference research,
  edge-case reasoning.
- **medium** — well-defined approach; sub-agent follows a
  clear brief and may need to read a few files.
- **low** — purely mechanical (rename, reformat, add a log
  line); the brief is a complete instruction.

**Model choice:** the planner recommends a model per step.
Skew to the more capable model when in doubt. A failed or
low-quality implementation wastes more time than the heavier
model would have cost.

- **opus** — deep reasoning, cross-file architectural
  understanding, subtle correctness, complex protocol
  research, intricate implementation where mistakes are
  costly.
- **sonnet** — well-briefed implementation work; faster and
  cheaper. Works well when the brief front-loads the
  research.
- **haiku** — purely mechanical tasks; the brief must be a
  near-complete instruction.

The model also determines context window: opus has 1M tokens,
sonnet and haiku 200K. Steps that need to hold many files
simultaneously may need opus for that reason alone.

**Brief for sub-agent:** the key field. Write it as if briefing
a colleague who has never seen the codebase. Include: what to
change, which files to touch, what patterns to follow, and any
non-obvious constraints. Front-load the research the planner
already did so the sub-agent doesn't repeat it.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code builds (`pre-commit run --all-files`).
- [ ] Tests pass (`cargo test --workspace`).
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions, including
      the `Co-Authored-By` line with model, context window,
      effort level, and other settings.

## Administration and logistics

### Success criteria

We will know the MVP has landed when:

- `cargo build -p ryll --release` continues to produce the
  existing single ryll binary, now with `--web` mode compiled
  in alongside GUI and headless.
- `ryll --web session.vv` (or the equivalent CLI flags)
  starts, connects to the SPICE server, and prints a
  `http://<host>:<port>/?token=<token>` URL to stdout.
- Opening that URL in Firefox or Chrome on a peer machine
  shows the remote desktop. Opening the URL *without* the
  token returns 401.
- Keyboard input from the browser produces correct
  characters in the guest, including shifted symbols and
  arrow keys.
- Mouse input from the browser produces correct cursor
  motion and clicks in the guest.
- The SPICE cursor follows guest cursor motion with no
  encoder-induced lag (datachannel overlay path).
- Audio from the guest plays in the browser with acceptable
  sync, and (in the common Opus-negotiated case) the audio
  reaches the browser without re-encode.
- The browser tab can be closed and re-opened (same URL)
  within ~30 seconds and the SPICE session resumes without
  a server-side reconnect.
- `docs/multi-mode-parity.md` (the Phase 0 artifact) exists
  and every feature listed in `README.md` appears in the
  matrix with a value in every mode column.
- `pre-commit run --all-files` passes.
- `cargo test --workspace` passes — the existing `ryll`
  binary continues to work unchanged after Phase 1.
- `docs/web-frontend.md` exists and is sufficient for the
  operator to bring up a session from scratch, including a
  systemd unit example.

### Future work

- **Drive down GUI ↔ headless ↔ web parity gaps.** The
  Phase 0 audit is a baseline, not a fix. Each gap that the
  audit surfaces should spawn its own follow-on plan
  (collected under a `PLAN-multi-mode-parity-driveup.md`
  master plan written after Phase 0 lands). This work
  proceeds in parallel with the rest of this plan and is
  *not* a prerequisite for the web frontend MVP — the web
  frontend deliberately ships with a minimal feature set
  in MVP and the parity work catches up incrementally.
- **HTTPS / TLS.** Take a cert+key pair on the CLI; document
  `mkcert` / `step-ca` / Let's Encrypt recipes. Required
  before any feature that wants a secure context (clipboard
  sync, Pointer Lock on Chrome) can land. ACME inside the
  `--web` mode is further future work.
- **Browser-side authentication beyond the URL token.**
  Login UI, OIDC, mTLS as bigger follow-ups. Natural pairing
  with the TLS work above.
- **Multi-monitor.** Add one video track per SPICE display
  surface; arrange them in the browser shell. Most of the
  back-end is already multi-surface.
- **USB redirection** via WebUSB (Chrome/Edge only) or a
  small companion native helper.
- **Clipboard sync** between browser and guest, via the
  async clipboard API and the SPICE vdagent clipboard
  channel.
- **Folder sharing.** Probably via a browser-side
  drag-and-drop area that uploads files into a temporary
  WebDAV mount on the guest. Bigger than it looks.
- **Hardware encoding** (NVENC / QSV / VAAPI) for desktops
  with capable GPUs. Drops encoder CPU to near zero and
  lets the operator run multiple sessions per machine.
- **Per-rect dirty tracking** for partial-frame encodes.
  Meaningful CPU/bandwidth win on mostly-static desktops.
- **RTCP PLI (Picture Loss Indication) handling.** The
  WebRTC bring-up deliberately stubbed this: the bridge
  forces a keyframe when the peer connection reaches
  `Connected`, but never in response to a viewer-initiated
  refresh, so a browser that loses the IDR has to reconnect
  to recover. Wiring the incoming PLI through to
  `EncoderControl::RequestKeyframe` closes that gap.
- **Multi-session / multi-tenant** mode — one daemon, many
  desktops, many viewers. Hard-blocked on the
  authentication and TLS items above.
- **TURN support** for WAN access where STUN cannot
  traverse the NAT. Pair with `coturn` deployment notes.
- **AV1 encode** when `rav1e` becomes fast enough for
  real-time desktop resolutions, or via SVT-AV1 FFI.
- **Mobile UX.** Touch gestures, on-screen keyboard
  toggle, pointer-precision indicator.
- **Recording.** Reuse the encoder pipeline to dump a
  session to disk in MP4, paralleling the existing
  `--capture` feature.
- **Cargo feature gating** for `--gui`, `--web`, and
  `--capture` so a server build can drop the egui dep tree
  and a desktop build can drop the WebRTC dep tree. Ship
  one fat binary first; revisit once dep weight is concrete.
- **Replace Kasm in the operator's deployment.** Concretely:
  bring up xspice on the dev desktop, point `ryll --web` at
  it, retire the Kasm container. Treat as the MVP-acceptance
  milestone *for the operator*, not a general-availability
  claim.

#### Items deferred from the post-Phase-3 pre-push audit

Tracked from a `PUSH-AUDIT.md` audit run after Phases 0–3
landed. Blocking items (malformed-SDP test, doc gaps, rustls
provider init, unwrap → expect polish) were addressed before
the audit's push gate; the items below are advisory and
deferred:

- **`split_annex_b` / `find_start_code` re-export.** Phase 2
  step 2b added private helpers in
  `shakenfist-spice-renderer/src/encoder/h264.rs` that parse
  Annex-B-framed NAL streams. A second consumer (e.g. an HLS
  muxer or a future client-side viewer) would re-derive the
  same logic. Re-export from the renderer when a second
  consumer materialises.
- **`capture.rs` odd-dimension repack** (`ryll/src/capture.rs`
  ~line 223). When the source surface has odd dimensions,
  `&pixels[..pixel_count * 4]` reads slightly into the next
  row — pre-existing behaviour preserved across the Phase 2
  capture cleanup. Worst case is a single-pixel horizontal
  smear in the captured `display.mp4`. Tracked as a TODO
  comment in the source. Fix by row-by-row repacking when
  source dims are odd.
- **`H264Encoder::encode` wrong-buffer-size unit test.** The
  validation code is correct by inspection; an adversarial
  unit test (passing `width * height * 3` bytes) would make
  the contract explicit and catch a future regression if the
  check is ever moved.
- **`EncoderTask` encoder-error exit-path test.** When
  `encoder.encode` returns Err, the task exits with Err. No
  test covers that path. Inject a fake `FrameSource` whose
  RGBA slice is wrong-sized to trigger the encoder error and
  assert the `JoinHandle` resolves to Err.
- **`NotificationStoreSink::push` direct test.** The thin
  newtype wrapper in `ryll/src/notifications.rs` that adapts
  `Arc<Mutex<NotificationStore>>` to the renderer's
  `NotificationSink` trait has no direct unit test — only
  transitively covered via the channel-handler tests.
- **`ArboardClipboard` test coverage.** Hard to unit-test in
  CI without a display server. Options: a mock
  `ClipboardBackend` impl for tests; or a
  `#[cfg(any(target_os = "linux"))]` test that probes for
  `$DISPLAY` and skips otherwise.
- **`ArboardClipboard::set_text` poisoning policy.** The
  `get_text` path returns `None` on Mutex poisoning;
  `set_text` propagates the poison error string. Inconsistent.
  Align both paths (return None / reset the inner Option), or
  switch to `parking_lot::Mutex` to avoid poisoning entirely.
  Severity is low because reaching a poisoned lock requires
  a panic inside `arboard::Clipboard::set_text`, which is
  rare.
- **Bound clipboard payload size at the SPICE-channel layer.**
  `ArboardClipboard::set_text` does not bound `text` length,
  so a malicious guest could ship a multi-GiB clipboard
  payload via the SPICE main channel and OOM the host. Cap
  at e.g. 16 MiB inside
  `shakenfist-spice-renderer/src/channels/main_channel.rs`
  before delegating to `ClipboardBackend::set_text`.
  Pre-existing risk class (the SPICE main channel design
  inherits this from the protocol), not introduced by the
  web-frontend work, but worth tracking now.

#### Items deferred from the post-Phase-8 pre-push audit

Tracked from a `PUSH-AUDIT.md` audit run after Phases 0–8
landed. Three blocking security findings (token leaking into
structured logs, reaper-vs-/offer race, no rate limit on
/offer) were addressed before push as commits `0d2ed6e0`,
`a9601da5`, `a468de26`. The items below are advisory and
deferred:

- **Orphan `_notifications_sink` in `ryll/src/main.rs::run_web`**
  (around line 419). `run_web` builds an
  `Arc<dyn NotificationSink>` and immediately discards it via
  the `_` prefix; `run_connection` does not accept a sink
  parameter, unlike the headless path. Either wire the sink
  into `run_connection` so web-mode channel handlers route
  notifications through the unified store (Decision #21), or
  delete the orphan and replace with a `// TODO(web-notifications):`
  marker. Low-risk because the gap-observer at the same call
  site already populates the store.
- **Accumulated `#[allow(dead_code)]` annotations.** Three on
  `WebState` (`input_tx`, `resize_tx`, `event_tx`) annotated
  "wired in 5b/5c/5d/5e", plus several on `ChannelEvent`
  variants and per-event `image_id` fields in the renderer's
  `channels/mod.rs`. With Phase 5 complete these forward
  references should be revisited — most are now actually used
  by `run_web` and the relays, so the annotations can be
  removed.
- **RTP header helper extraction in `shakenfist-spice-webrtc/src/bridge.rs`.**
  `run_video_pump`, `run_synthetic_audio_pump`, and
  `run_audio_pump` all build a 7-field `Header` struct literal
  inline. A `fn make_rtp_header(pt, seq, ts, ssrc) -> Header`
  helper would eliminate the triple copy-paste.
- **`WebState::teardown_bridge()` extraction before multi-viewer.**
  `run_bridge_reaper` and the implicit teardown sequence in
  `post_offer` share the same lock-ordered "close bridge → stop
  encoder → clear opus_active_tx" shape. When multi-viewer
  support is added a `WebState::teardown_bridge()` method
  would prevent a third copy.
- **`denormalise` adversarial test in `ryll/src/web/inputs.rs`.**
  The browser-side denormalisation function clamps coordinates
  defensively but has no unit test for `NaN`, `±∞`, or
  out-of-range float inputs. NaN comparisons in `clamp`
  produce platform-dependent surprises. Add a parameterised
  unit test.
- **Targeted unit test for `run_bridge_reaper`'s generation-
  counter fast path** (`ryll/src/web/lifecycle.rs`). The
  Phase 6b note about "real WebrtcBridge required for
  unit-testing the reaper" is now partially relaxed — the
  generation-counter path could be tested without a live
  bridge by injecting a `WebState` and bumping
  `bridge_generation` between subscription and signal.
  Existing lifecycle integration test covers the no-regression
  case but a targeted unit test would harden the race-handling
  logic cheaply.

### Bugs fixed during this work

- **`capture.rs` H.264 NAL extraction** (Phase 2 follow-up,
  commit `7871259e`). The pre-extraction `capture.rs`
  primary NAL loop read `nal[0] & 0x1F` as the NAL type, but
  openh264 0.6's `layer.nal_unit(i)` returns NALs with the
  Annex-B start code prepended, so `nal[0]` was always
  `0x00`. Every NAL silently fell into the default arm and
  got concatenated with its start code into the frame
  buffer. A fallback path re-extracted SPS/PPS via start-
  code scanning but never repaired the frame buffer. Plus
  `length_prefix_nal` was being called once per frame on the
  whole concatenated buffer rather than per-NAL — producing
  invalid AVCC framing. Decoders were tolerant enough that
  the resulting `display.mp4` mostly played, but it was not
  standards-compliant. Routing through the new H264Encoder
  eliminated both bugs.

### Documentation index maintenance

When Phase 1 lands, update `docs/plans/index.md` row for
this plan to track progress (e.g. *In progress* with phases
crossed off as their plan files are written and executed).
Phase plan files are linked from this master plan's
Execution table; they are *not* added to `docs/plans/order.yml`.
When all phases are complete, set the index status to
*Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan. Open
questions are now resolved (see Resolutions §1–15); the
next step is writing the per-phase plan files starting with
Phase 0 or Phase 1 (independent — either order or in
parallel).
