# Multi-mode feature parity

Ryll is one binary with three delivery modes: a **GUI** desktop window
(egui/eframe), a **headless** mode (no window; stdout, metrics, and an
optional control socket), and a **web** mode (browser client over
WebRTC via `--web`). Features usually land in one mode first and reach
the others later — or never, when a mode physically can't support them.
This page is the honest ledger of that drift: a per-feature matrix of
what is reachable in each mode today.

Use it two ways:

- **Choosing a mode.** "Can I do X in headless?" is answered by one
  cell lookup rather than by reading three documents or trying it.
- **Reviewing changes.** A new feature that only works in the mode its
  author happened to test in is a parity regression waiting to be
  discovered by a user. The matrix makes that visible at review time.

## The three modes

| Mode | Invocation | Description |
|------|-----------|-------------|
| GUI | (default) | Desktop window with menus, status bar, panels. See [Features](/components/ryll/features/). |
| Headless | `--headless` | No window. Frames are counted, stats print every 10 s, and an optional [control socket](/components/ryll/control-socket-protocol/) (`--control-socket`) lets an external driver send input, take screenshots, and subscribe to events. |
| Web | `--web` | Serves a browser client over WebRTC. Shipped; the MVP scope was declared in [PLAN-web-frontend.md](/components/ryll/plans/PLAN-web-frontend/) and items outside it are marked below. See the [web frontend operator guide](/components/ryll/web-frontend/). |

## How to read the matrix

| Cell value | Meaning |
|------------|---------|
| available | Feature is fully reachable in this mode |
| partial | Only some of the feature is reachable |
| missing | Feature is not reachable today |
| n/a — intrinsic | Feature physically cannot exist in this mode (justification in parentheses) |

Notes on the annotations:

- **Source references** (`file.rs:line`) point at the evidence for a
  "partial" or "missing" verdict. They are point-in-time: correct as
  of the audit that added them, and removed when the gap closes.
- **"Out of MVP scope"** in the Web column means the feature was
  deliberately excluded from the shipped web MVP; the list lives in
  PLAN-web-frontend.md §Out of MVP scope. These are future work, not
  oversights.
- Several headless cells are reachable only through the control
  socket, which requires launching with `--control-socket <path>`.
  Those cells say so explicitly.

## Maintenance protocol

Reviewers are expected to keep this matrix honest:

- When a feature is added, add a row.
- When a mode gains or loses a feature, update the cell.
- When a "missing"/"partial" gap is closed, update the cell and remove
  the source link.

This document records the baseline; it does not propose fixes. There
is no standing gap-closure plan — gaps get closed by mode-specific
work (the [web frontend plan](/components/ryll/plans/PLAN-web-frontend/) closed the
Web column's MVP set; the control socket work gave headless an
interactive driving surface). Last full audit: the web frontend
sweep; control-socket cells refreshed 2026-08-23 (web mode gained
the socket, its `status` and event rows, and the shared digest
poller), media socket binding added 2026-08-21.

## Parity matrix

| Feature | GUI | Headless | Web |
|---------|-----|----------|-----|
| **Connection** | | | |
| Connect via .vv file (`--file`) | available | available | available |
| Connect via URL (`--url`) | available | available | available |
| Connect via direct host:port (`--direct`) | available | available | available |
| TLS / secure channel (inline CA from .vv) | available | available | available |
| Password authentication | available | available | available |
| **Control socket** | | | |
| Control socket (`--control-socket`) | n/a — intrinsic (the window owns input and the surface; combining with the GUI is a CLI error) | available | available |
| Session state query (`status` verb) | n/a — intrinsic | available | available (`run_web` builds a `SessionStatus` from the same `spice_connected` / `agent_connected` flags and surface mirror headless uses) |
| Event subscription (`latency`, `agent_connected`, `paste_*`, `surface_drawn`, `digest_updated`) | n/a — intrinsic | available (`digest_updated` needs the `digest-decode` build feature) | available (same `event_broadcast_tx`; `digest_updated` needs the `digest-decode` build feature, and the poller is spawned only when `--control-socket` is given so an ordinary browser session does not pay to decode QR) |
| **Display** | | | |
| Display framebuffer (render SPICE draw ops) | available | partial (`ryll/src/app.rs:3513-3516` — headless counts frames but never paints continuously; with `--control-socket`, a `SurfaceMirror` renders draw ops on demand for the `screenshot` verb, instantiated on first use) | available (single monitor, MVP) |
| Multi-monitor (`--monitors N`) | available | partial (`ryll/src/app.rs:3513-3516` — channel connects but frames are silently dropped; surfaces are never created in headless) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) |
| Full draw-op coverage (FILL, COPY, BLEND, etc.) | available | partial (same as display framebuffer — channel runs but output is discarded outside the control-socket mirror) | available (inherited from shared framebuffer) |
| SPICE streaming / MJPEG | available | partial (decoded messages counted as frames, not rendered) | available (inherited) |
| Display capabilities advertisement (COMPOSITE, MONITORS_CONFIG, etc.) | available | available | available |
| Window auto-resize to surface size | available | n/a — intrinsic (no window; resize channel exists but resize_tx is never connected to a window, `ryll/src/app.rs:3391`) | n/a — intrinsic (browser viewport is user-controlled) |
| Obey guest size hints toggle (`--no-obey-guest-size` / hamburger toggle) | available | n/a — intrinsic (no window; flag accepted in headless for CLI symmetry but ignored, `ryll/src/main.rs:195`) | missing (out of MVP scope; the web frontend's "guest fits browser viewport" inverse is planned) |
| Guest resolution follows viewport (`VDAgentMonitorsConfig`) | available | missing (`ryll/src/app.rs:3391` — `_resize_tx` is created but never used to drive resize logic in headless) | available |
| **Cursor** | | | |
| Server cursor rendering (SET/INIT) | available | missing (`ryll/src/app.rs:3563` — `CursorChannel` runs, events arrive, but headless event loop has no handler for `CursorImage`/`CursorPos` events) | available (datachannel CSS overlay; the host cursor is only hidden once a real SPICE shape has arrived, so a guest that never sends one leaves the browser's own pointer visible rather than none at all) |
| Cursor position tracking | available | missing (same as above) | available (MVP) |
| Cursor hide/show | available | missing (same as above) | available (MVP) |
| Default arrow fallback cursor | available | n/a — intrinsic (no display) | available |
| **Audio** | | | |
| Audio playback (PCM and Opus decoding) | available | partial (`ryll/src/channels/playback.rs:156-292` — `PlaybackChannel` connects and decodes, but spawns a cpal thread that will fail silently if no audio device is present; no headless-safe "discard" path exists) | available (MVP; Opus passthrough preferred, PCM→Opus fallback) |
| Volume control (slider + mute) | available | missing (`ryll/src/app.rs:1843-1856` — `VolumeControl` Arc is wired in but no CLI flag, stdout interface, or control-socket verb exposes it in headless) | missing (out of MVP scope; not listed in PLAN-web-frontend.md MVP) |
| Audio volume/mute persisted across reconnect | available | n/a — intrinsic (no GUI; VolumeControl is re-created on reconnect anyway) | missing (out of MVP scope) |
| **Keyboard / Mouse Input** | | | |
| Keyboard input (key down/up, scancodes) | available | available (via the control socket `send_key` verb — down/up/press with AT-set-1 scancodes; without `--control-socket` only cadence and `--paste-text` generate input) | available (MVP; browser-side scancode table) |
| Mouse input (position and buttons) | available | missing (the control socket declares mouse verbs a non-goal until a test needs them; no other pointer path exists in headless) | available (MVP) |
| Mouse mode negotiation (CLIENT/SERVER) | available | available (negotiation runs in `MainChannel` regardless of mode) | available (both modes drive the guest pointer: absolute `MouseMove` in client mode, relative `MouseMotion` derived from consecutive browser positions in server mode. Pointer Lock is still absent, so the derived deltas drift under guest pointer acceleration and dead-zone at the window edge — see docs/web-frontend.md) |
| Extended key prefix (E0 for nav cluster) | available | available (the control socket `send_key` verb accepts 0xE0-prefixed 16-bit scancodes in logical form and converts them; protocol 1.1 and earlier sent them prefix-second and unusable — see docs/control-socket-protocol.md. Cadence alone only sends space) | available (browser table is in logical form; ryll converts via `make_scancode`) |
| Modifier state tracking (Ctrl/Shift/Alt) | available | partial (`ryll/src/channels/inputs.rs:77-82` — tracked internally but only used by paste state machine; cadence does not restore modifiers) | available (MVP) |
| **Cadence mode** | | | |
| Cadence mode (`--cadence`) | available | available | missing (out of MVP scope; no test-automation use case in browser) |
| **Paste-as-keystrokes** | | | |
| Paste-as-keystrokes (enable flag) | available | available (`--enable-paste-as-keystrokes`) | missing (out of MVP scope; clipboard sync is in Future work list in PLAN-web-frontend.md) |
| Paste-as-keystrokes (runtime trigger via Ctrl+Alt+V) | available | available (via the control socket `paste` verb; Ctrl+Alt+V itself is GUI-only) | missing (out of MVP scope) |
| Paste-as-keystrokes (headless `--paste-text`) | n/a — intrinsic (GUI reads clipboard; `--paste-text` is headless-only per README) | available | missing (out of MVP scope) |
| Paste-as-keystrokes (inter-character delay `--paste-char-delay-ms`) | available | available (also settable per-request via the control socket `paste` verb's `char_delay_ms`) | missing (out of MVP scope) |
| Paste-as-keystrokes (vdagent auto-disable) | available (`ryll/src/app.rs:1077-1078`) | available (AgentConnected event logged; `enable_paste` flag passed through) | missing (out of MVP scope) |
| Paste character validation / error dialog | available | partial (`ryll/src/app.rs:3553-3556` — PasteFailed logged and causes non-zero exit; control-socket clients get a `paste_failed` event; no interactive dialog) | missing (out of MVP scope) |
| **USB Redirection** | | | |
| USB redirection channel (protocol layer) | available | available (`--usb-disk`, `--usb-disk-ro` CLI flags work in headless) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) |
| USB interactive panel (Menu → USB) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Connect physical USB device (Linux only) | available | missing (`ryll/src/app.rs:3389` — `usb_tx` is created but `usb_rx` is never sent a ConnectPhysical command in headless; no CLI flag for physical devices) | missing (out of MVP scope) |
| Connect virtual USB disk (`--usb-disk`) | available | available | missing (out of MVP scope) |
| Connect read-only virtual USB disk (`--usb-disk-ro`) | available | available | missing (out of MVP scope) |
| Runtime USB disk addition (file picker) | available | n/a — intrinsic (no GUI file picker) | missing (out of MVP scope) |
| USB error reporting (bug report integration) | available | partial (`ryll/src/app.rs:3527-3534` — errors logged; no zip written unless `--pedantic`) | missing (out of MVP scope) |
| **Folder Sharing (WebDAV)** | | | |
| WebDAV folder sharing channel (protocol layer) | available | available (`--share-dir`, `--share-dir-ro` CLI flags work in headless) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) |
| WebDAV interactive panel (Menu → Folders) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Share directory (`--share-dir`) | available | available | missing (out of MVP scope) |
| Read-only share (`--share-dir-ro`) | available | available | missing (out of MVP scope) |
| Runtime directory picker (native file dialog) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| **Statistics and Instrumentation** | | | |
| FPS tracking (sliding window from DisplayMark) | available | partial (`ryll/src/app.rs:3516` — frame count incremented but FPS not computed; only raw count printed every 10 s) | missing (out of MVP scope; no stats panel in browser MVP) |
| Bandwidth sparkline (rolling bytes/sec graph) | available | missing (`ryll/src/app.rs:3392-3394` — `byte_counter` wired in but never sampled or reported in headless) | missing (out of MVP scope) |
| Latency sparkline (inter-PING interval graph) | available | partial (no graph; raw latency samples are reachable as control-socket `latency` events, and bytes_in/bytes_out are tracked, `ryll/src/app.rs:3518-3521`) | missing (out of MVP scope) |
| Periodic stats to stdout (headless only) | n/a — intrinsic (GUI shows live stats in status bar) | available (every 10 s: frames, bytes_in, bytes_out) | n/a — intrinsic (browser context) |
| `--latency-file` latency measurements export | missing (`ryll/src/config.rs:65` — flag declared but never read or acted upon anywhere in the codebase) | missing (same — flag is declared but unused; control-socket `latency` events are the working alternative) | missing (out of MVP scope) |
| Channel state snapshots (for bug reports) | available | available | missing (out of MVP scope) |
| Traffic ring buffer (always active) | available | available | missing (out of MVP scope) |
| **Protocol Gap Tracking** | | | |
| Protocol gap counter (`Gaps: N` status bar) | available | missing (`ryll/src/app.rs:3563` — gap observer registered; warn_once fires; but headless event loop has no handler to surface the count) | missing (out of MVP scope) |
| Protocol gap floating window (click Gaps button) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| `--pedantic` mode (auto bug-report per gap) | available | available (partial: `ryll/src/app.rs:3419` — app_snapshot is an empty default, so pedantic zips omit surface/bandwidth/latency data) | missing (out of MVP scope) |
| `--pedantic-dir` directory selection | available | available | missing (out of MVP scope) |
| **Bug Reports** | | | |
| Bug report dialog (F12 / Menu → Report) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Bug report zip assembly (metadata, pcap, screenshot, metrics) | available | partial (`ryll/src/app.rs:3419` — `--pedantic` zips have empty app_snapshot; no interactive zip generation in headless) | missing (out of MVP scope) |
| Bug report display region selection | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Bug report trigger-time screenshot capture | available | n/a — intrinsic (no continuously-painted display) | missing (out of MVP scope) |
| Runtime metrics in bug reports (Linux `/proc`) | available | available (sampled in `metrics.rs`; used by pedantic observer) | missing (out of MVP scope) |
| **Screenshot** | | | |
| Screenshot (F8 / Menu → Screenshot) | available | available (via the control socket `screenshot` verb — PNG or raw RGBA of any live surface; the backing `SurfaceMirror` is created on first use) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope "Recording / capture") |
| Multi-surface screenshot (suffix `-1`, `-2`, …) | available | available (the `screenshot` verb takes a `surface_id` parameter; one call per surface) | missing (out of MVP scope) |
| **Live Traffic Viewer** | | | |
| Traffic viewer panel (F11 / Menu → Traffic) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Traffic viewer channel filters | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| Traffic viewer pause/resume | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) |
| **Notifications** | | | |
| In-app notifications panel (bell / Menu bell) | available | missing (`ryll/src/app.rs:3400-3402` — store exists and is populated, but no bell or side panel in headless; no stdout drain) | missing (out of MVP scope) |
| SPICE_MSG_NOTIFY surfacing | available | partial (stored in notification store; printed nowhere in headless event loop unless captured by `--pedantic`) | missing (out of MVP scope) |
| Gap observer notification feed | available | partial (registered in headless at `ryll/src/app.rs:3431`; notifications accumulate in store but are never printed to stdout) | missing (out of MVP scope) |
| **Reconnect / Lifecycle** | | | |
| Reconnect on disconnect (Reconnect button) | available | n/a — intrinsic (no GUI; headless exits when main channel disconnects) | available (SPICE session held while PeerConnection is dead; browser auto-reconnects with 1 s/2 s/4 s/8 s/16 s backoff, max 5 attempts then a manual button) |
| State preserved across reconnect (disks, share dir, paste toggle) | available | n/a — intrinsic (headless does not reconnect) | partial (SPICE session continuity is preserved — the bridge is reaped and rebuilt, leaving the SPICE channels untouched; per-tab UI state is browser-managed) |
| Notification history preserved across reconnect | available | n/a — intrinsic | n/a — intrinsic (browser tab holds its own page state) |
| Disconnect dialog | available | n/a — intrinsic (no GUI) | missing (not in MVP scope; browser tab closure is the UX) |
| Graceful Ctrl+C shutdown | available | available | available (SIGTERM/Ctrl-C drains axum then explicitly closes the active bridge so DTLS/SRTP tears down cleanly before the process exits) |
| Cancel superseded connection attempt | available | n/a — intrinsic (headless does not reconnect) | n/a — intrinsic (single-session MVP) |
| **Window / Session Persistence** | | | |
| Window size/position persistence (eframe persistence) | available | n/a — intrinsic (no window) | n/a — intrinsic (browser manages its own viewport) |
| Open side panels survive reconnect | available | n/a — intrinsic (no GUI) | n/a — intrinsic (no side panels in web MVP) |
| **Capture Mode** | | | |
| Protocol pcap capture (`--capture`) | available | available | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope "Recording / capture") |
| Display video capture (`display.mp4` via openh264) | available | available (capture session is passed through and VideoWriter fires on DisplayMark events even in headless) | missing (out of MVP scope) |
| Session metadata (`metadata.json`) | available | available | missing (out of MVP scope) |
| **Diagnostics / Verbose Logging** | | | |
| Verbose logging to stderr (`-v`) | available | available | available (stdout logging works regardless of mode) |
| File logging to `/tmp/ryll.log` (`-v`) | available | available | available |
| Intimate logging (`--intimate`, keystrokes/mouse) | available | available | missing (no interactive input to log in web MVP that wouldn't also appear in the browser console) |
| **Connection Source** | | | |
| VDAgent connection status (agent_connected flag) | available | available (AgentConnected event handled and logged; also a control-socket event and `status` field) | available (wired through same channel path) |
| TCP keepalive on all channel sockets | available | available | available (inherited from SpiceClient) |
| **Authentication / Session Token** | | | |
| Per-launch URL token (web mode only) | n/a — intrinsic (no HTTP server) | n/a — intrinsic (no HTTP server) | available (MVP; random 32-byte token printed to stdout) |
| TLS for browser-facing endpoint | n/a — intrinsic | n/a — intrinsic | available (`--web-tls-cert` / `--web-tls-key` flags; axum-server with rustls) |
| WebRTC media socket binding | n/a — intrinsic (no WebRTC) | n/a — intrinsic (no WebRTC) | available (`--web-media-addr` takes an address or an interface name, `--web-media-port` pins the UDP port for a firewall rule, `--web-ice-server` supplies STUN/TURN URLs) |
| **Web frontend (--web mode)** | | | |
| HTTP server with per-launch URL token | n/a — intrinsic | n/a — intrinsic | available (`ryll/src/web/server.rs`) |
| Embedded browser shell (HTML/JS/CSS via include_bytes!) | n/a — intrinsic | n/a — intrinsic | available |
| WebRTC SDP signalling endpoint (`POST /offer`) | n/a — intrinsic | n/a — intrinsic | available |
| Synthetic video stream over WebRTC (test pattern) | n/a — intrinsic | n/a — intrinsic | available (SyntheticFrameSource) |
| Synthetic audio stream over WebRTC (440 Hz tone) | n/a — intrinsic | n/a — intrinsic | available |
| Real SPICE display frames over WebRTC | available (egui paint loop) | n/a — intrinsic (no encoder driven from real frames) | available (RealFrameSource over SurfaceMirror) |
| Real SPICE audio over WebRTC (Opus passthrough) | available (cpal sink) | n/a — intrinsic (no audio sink) | available (Opus path; PCM-only servers fall back to silent audio with a warn) |
| Browser-side keyboard/mouse input over datachannel | available (egui inputs) | n/a — intrinsic (no input device) | available |
| Browser-side cursor overlay (datachannel) | available (egui overlay) | n/a — intrinsic | available |
