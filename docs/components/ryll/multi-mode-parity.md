# Multi-mode feature parity

Ryll is one binary with four delivery modes: a **GUI** desktop window
(egui/eframe), a **headless** mode (no window; stdout, metrics, and an
optional control socket), a **web** mode (browser client over WebRTC
via `--web`), and an **RDP** mode (a stock RDP client via `--rdp`, in
builds with the `rdp` feature). Features usually land in one mode
first and reach the others later — or never, when a mode physically
can't support them. This page is the honest ledger of that drift: a
per-feature matrix of what is reachable in each mode today.

Use it two ways:

- **Choosing a mode.** "Can I do X in headless?" is answered by one
  cell lookup rather than by reading several documents or trying it.
- **Reviewing changes.** A new feature that only works in the mode its
  author happened to test in is a parity regression waiting to be
  discovered by a user. The matrix makes that visible at review time.

## The four modes

| Mode | Invocation | Description |
|------|-----------|-------------|
| GUI | (default) | Desktop window with menus, status bar, panels. See [Features](/components/ryll/features/). |
| Headless | `--headless` | No window. Draws are applied to an in-memory surface, a stats line is logged every 10 s, and an optional [control socket](/components/ryll/control-socket-protocol/) (`--control-socket`) lets an external driver send input, take screenshots, read the display counters, and subscribe to events. |
| Web | `--web` | Serves a browser client over WebRTC. Shipped; the MVP scope was declared in [PLAN-web-frontend.md](/components/ryll/plans/PLAN-web-frontend/) and items outside it are marked below. See the [web frontend operator guide](/components/ryll/web-frontend/). |
| RDP | `--rdp` | Serves the session to one stock RDP client at a time with IronRDP: a reachability fallback for users with no SPICE client, not a better path. Needs a build with the `rdp` feature, which releases leave out. See the [RDP frontend operator guide](/components/ryll/rdp-frontend/). |

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
  Those cells say so explicitly. The same holds for the Web and RDP
  columns.
- An **RDP** cell reads "available" when a stock RDP client can use
  the feature through `--rdp`. A feature ryll offers only to its own
  operator (a control socket verb, `--capture`, `--usb-disk`) is
  judged, as in the Web column, by what the mode wires up.

## Maintenance protocol

Reviewers are expected to keep this matrix honest:

- When a feature is added, add a row.
- When a mode gains or loses a feature, update the cell.
- When a "missing"/"partial" gap is closed, update the cell and remove
  the source link.
- `tools/check-parity-matrix.py`, run by pre-commit, checks that every
  row has a cell per mode and every cell starts with a verdict above.

This document records the baseline; it does not propose fixes. There
is no standing gap-closure plan — gaps get closed by mode-specific
work (the [web frontend plan](/components/ryll/plans/PLAN-web-frontend/) closed the
Web column's MVP set; the control socket work gave headless an
interactive driving surface). Last full audit: the web frontend
sweep; control-socket cells refreshed 2026-08-23 (web mode gained
the socket, its `status` and event rows, and the shared digest
poller), media socket binding added 2026-08-21, clipboard and
resize rows refreshed 2026-10-14 (control protocol 1.3), display,
streaming and statistics rows refreshed 2026-10-16 (control protocol
1.4's `stats` verb), RDP column and *RDP frontend* section added
2026-10-27, each cell judged from `run_rdp`.

## Parity matrix

Headless cells name two functions. The renderer's `run_headless()`,
in `shakenfist-spice-renderer/src/session.rs`, runs the session and its
event loop. Ryll's `run_headless()`, in `ryll/src/main.rs`, builds the
notification store and registers the `--pedantic` and gap observers
around it.

RDP cells name `run_rdp`, in `ryll/src/rdp.rs`. It builds the SPICE
side as `run_web` does (event bus, surface mirror, optional control
socket), supervises SPICE reconnects, and hands the session to the
`shakenfist-spice-rdp` library, which serves it to the client. The
[RDP mode internals](/components/ryll/rdp-mode-internals/) page describes both.

| Feature | GUI | Headless | Web | RDP |
|---------|-----|----------|-----|-----|
| **Connection** | | | | |
| Connect via .vv file (`--file`) | available | available | available | available |
| Connect via URL (`--url`) | available | available | available | available |
| Connect via direct host:port (`--direct`) | available | available | available | available |
| TLS / secure channel (inline CA from .vv) | available | available | available | available |
| HTTP CONNECT proxy (`proxy=` in .vv) | available | available | available (every mode builds the same `ConnectionConfig`) | available (`run_rdp` builds the same `ConnectionConfig` as the other modes) |
| Password authentication | available | available | available | available |
| **Control socket** | | | | |
| Control socket (`--control-socket`) | n/a — intrinsic (the window owns input and the surface; combining with the GUI is a CLI error) | available | available | available (wired to the session's fixed ends, so a SPICE reconnect does not disturb it) |
| Session state query (`status` verb) | n/a — intrinsic | available | available (`run_web` builds a `SessionStatus` from the same `spice_connected` flag, `SessionState` agent watch and surface mirror headless uses) | available (`run_rdp` builds a `SessionStatus` from a `connected` flag and agent watch that each SPICE attempt updates, so `status` reports SPICE down during an outage and up again after a reconnect) |
| Display and video-stream counters (`stats` verb) | n/a — intrinsic | available | available (`run_web` hands the socket the same display snapshot it gives the session) | available (`run_rdp` hands the socket the display and playback snapshots it gives each SPICE attempt) |
| Event subscription (`latency`, `agent_connected`, `paste_*`, `surface_drawn`, `clipboard_changed`, `digest_updated`) | n/a — intrinsic | available (`digest_updated` needs the `digest-decode` build feature) | available (same `event_broadcast_tx`; `digest_updated` needs the `digest-decode` build feature, and the poller is spawned only when `--control-socket` is given so an ordinary browser session does not pay to decode QR) | available (same event bus as web mode; `paste_*` never fires because paste-as-keystrokes is off; `digest_updated` needs the `digest-decode` build feature and a `--control-socket`) |
| **Display** | | | | |
| Display framebuffer (render SPICE draw ops) | available | available (the renderer's `run_headless()` applies every draw op to a `SurfaceMirror` as it arrives, whether or not a control socket is open; nothing displays it, but the `screenshot` verb reads it) | available (single monitor, MVP) | available (the primary surface, sent to the client as bitmaps of the damage read from the `SurfaceMirror`) |
| Multi-monitor (`--monitors N`) | available | partial (`--monitors` reaches the session as in the GUI, and the renderer's `run_headless()` applies every display channel's draws to the `SurfaceMirror`, which creates a surface for each `SurfaceCreated`; `status` lists them all with their channel, but `screenshot` takes only a `surface_id` and so reaches only the primary's display channel. Multi-monitor headless is unverified end to end: [ryll#549](https://github.com/shakenfist/ryll/issues/549)) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) | partial (`--monitors` reaches the SPICE session and the mirror keeps every surface, but the display handler serves only the primary, and a client's multi-monitor layout is reduced to its primary monitor by `layout_size()` in `shakenfist-spice-rdp/src/display.rs`) |
| Full draw-op coverage (FILL, COPY, BLEND, etc.) | available | available (the same `SurfaceMirror::apply_event` dispatch as the GUI and web mode) | available (inherited from shared framebuffer) | available (the same `SurfaceMirror` as web mode) |
| SPICE video streams (MJPEG, H.264) | available | available (decoded frames are applied to the `SurfaceMirror` like any draw; the `stats` verb reports each stream's codec and decode counts) | available (inherited) | available (decoded frames are applied to the mirror and reach the client as bitmaps like any draw) |
| Display capabilities advertisement (COMPOSITE, MONITORS_CONFIG, etc.) | available | available | available | available |
| Prefer H.264 from the server (`--experimental-h264`) | available | available | available (affects only the SPICE side; the WebRTC encoder is unchanged) | available (affects only the SPICE side; the RDP client is sent bitmaps either way) |
| Do not offer H.264 to the server (`--no-h264`) | available | available | available (affects only the SPICE side; the WebRTC encoder is unchanged) | available (affects only the SPICE side; the RDP client is sent bitmaps either way) |
| Window auto-resize to surface size | available | n/a — intrinsic (no window) | n/a — intrinsic (browser viewport is user-controlled) | available (when the guest's surface changes size the client's desktop is resized to match; whether its window follows is the client's choice) |
| Obey guest size hints toggle (`--no-obey-guest-size` / hamburger toggle) | available | n/a — intrinsic (no window; flag accepted in headless for CLI symmetry but ignored, `_obey_guest_size` in ryll's `run_headless()`) | missing (out of MVP scope; the web frontend's "guest fits browser viewport" inverse is planned) | n/a — intrinsic (the RDP desktop is the guest's surface, so the client is always resized to it; the flag is accepted and ignored, as `main.rs` does not pass it to `run_rdp`) |
| Guest resolution follows viewport (`VDAgentMonitorsConfig`) | available | available (via the control socket `resize` verb, rounded to the GUI's 8-pixel grid; there is no viewport to follow) | available (also via the control socket `resize` verb, which shares the browser's resize queue) | available (the size the client connects with and every layout it sends are asked of the guest, rounded down to a multiple of 8; the control socket `resize` verb shares the queue) |
| **Cursor** | | | | |
| Server cursor rendering (SET/INIT) | available | missing (the renderer's `run_headless()` — `CursorChannel` runs, events arrive, but its event loop has no handler for `CursorImage`/`CursorPos` events) | available (datachannel CSS overlay; the host cursor is only hidden once a real SPICE shape has arrived, so a guest that never sends one leaves the browser's own pointer visible rather than none at all) | available (sent as an RDP pointer, scaled down to fit 96x96 when larger) |
| Cursor position tracking | available | missing (same as above) | available (MVP) | missing (the cursor relay, `apply_event()` in `shakenfist-spice-rdp/src/cursor.rs`, keeps only the shape and visibility, so the client draws the pointer where its own mouse is; in server mouse mode the two drift apart) |
| Cursor hide/show | available | missing (same as above) | available (MVP) | available |
| Default arrow fallback cursor | available | n/a — intrinsic (no display) | available | available (the client is told to use its default pointer until the guest sends a shape) |
| **Audio** | | | | |
| Audio playback (PCM and Opus decoding) | available | partial (`shakenfist-spice-renderer/src/channels/playback.rs` — `PlaybackChannel` connects and decodes, but always spawns a cpal thread (the headless runner plays locally); with no audio device present it raises one notification and retries with a backoff of up to 30 s; headless does not use a playback sink, so there is no discard path there) | available (MVP; Opus passthrough; raw-PCM servers give silent audio. The session uses a playback sink, so no host cpal thread or audio device is opened and the `output_*` snapshot fields stay empty) | available (Opus passed through, or 48 kHz stereo PCM, whichever the client accepts first; no host audio device is opened. Raw PCM from the server is not played to a client that chose Opus) |
| Volume control (slider + mute) | available | missing (the renderer's `run_headless()` — a `VolumeControl` is passed to `run_connection()` but no CLI flag, stdout interface, or control-socket verb exposes it in headless) | missing (out of MVP scope; not listed in PLAN-web-frontend.md MVP) | n/a — intrinsic (the RDP client plays the audio, so its own volume control sets the level; ryll sends the client no volume) |
| Audio volume/mute persisted across reconnect | available | n/a — intrinsic (no GUI; VolumeControl is re-created on reconnect anyway) | missing (out of MVP scope) | n/a — intrinsic (ryll has no volume of its own in this mode; the client's is untouched by a SPICE reconnect) |
| **Keyboard / Mouse Input** | | | | |
| Keyboard input (key down/up, scancodes) | available | available (via the control socket `send_key` verb — down/up/press with AT-set-1 scancodes; without `--control-socket` only cadence and `--paste-text` generate input) | available (MVP; browser-side scancode table) | available (scancodes from the client; Unicode key events, which carry no scancode, are dropped — see the operator guide) |
| Mouse input (position and buttons) | available | missing (the control socket declares mouse verbs a non-goal until a test needs them; no other pointer path exists in headless) | available (MVP) | partial (position, left and right buttons work in both mouse modes; a client that sends the middle button on the standard mouse message loses it (the `mouse()` handler in `shakenfist-spice-rdp/src/input.rs`), [ryll#604](https://github.com/shakenfist/ryll/issues/604). FreeRDP sends it on its own channel and is unaffected) |
| Mouse wheel | available (egui scroll events become SPICE wheel clicks) | missing (no pointer path in headless; the control socket has no wheel verb) | missing (`BrowserMsg` has no wheel message, so the browser shell does not forward scrolling) | available (vertical, accumulated into whole notches; horizontal scrolling is dropped, as SPICE has no horizontal wheel) |
| Mouse mode negotiation (CLIENT/SERVER) | available (both modes drive the guest pointer. In server mode the relative deltas come from host hover positions with no pointer grab, so they drift under guest pointer acceleration until targets near the window edge cannot be reached) | available (negotiation runs in `MainChannel` regardless of mode) | available (both modes drive the guest pointer: absolute `MouseMove` in client mode, relative `MouseMotion` derived from consecutive browser positions in server mode. Pointer Lock is still absent, so the derived deltas drift under guest pointer acceleration and dead-zone at the window edge — see docs/web-frontend.md) | available (both modes drive the guest pointer; in server mode the deltas derived from the client's positions drift under guest pointer acceleration, as in web mode) |
| Extended key prefix (E0 for nav cluster) | available | available (the control socket `send_key` verb accepts 0xE0-prefixed 16-bit scancodes in logical form and converts them; protocol 1.1 and earlier sent them prefix-second and unusable — see docs/control-socket-protocol.md. Cadence alone only sends space) | available (browser table is in logical form; ryll converts via `make_scancode`) | available (a key RDP flags as extended is sent with the 0xE0 prefix) |
| macOS Cmd chords (Cmd+Q, Cmd+W, ...) | available (treated as host shortcuts: the chord key is withheld from the guest, press and release; Cmd is not mapped to the guest's Super key) | n/a — intrinsic (no keyboard) | available, by a different policy (Cmd is forwarded as the guest's Super key — `MetaLeft`/`MetaRight` are in the browser scancode table — so the guest sees a Super chord rather than a bare letter) | n/a — intrinsic (the RDP client decides what a Cmd chord sends; ryll forwards the scancodes it receives) |
| Modifier state tracking (Ctrl/Shift/Alt) | available | partial (`InputsChannel` in `shakenfist-spice-renderer/src/channels/inputs.rs` — tracked internally but only used by the paste state machine; cadence does not restore modifiers) | available (MVP) | available (modifiers arrive as ordinary key events; Scroll, Num and Caps Lock follow the client's synchronize event; keys still held when a client leaves or is taken over are released) |
| **Cadence mode** | | | | |
| Cadence mode (`--cadence`) | available | available | missing (out of MVP scope; no test-automation use case in browser) | missing (`run_rdp` in `ryll/src/rdp.rs` never reads `--cadence`; only the GUI and the renderer's `run_headless()` run it) |
| **Clipboard** | | | | |
| Clipboard sync with the guest (vdagent, CLIPBOARD selection, UTF-8 text) | available (host clipboard via `arboard`) | available (via the control socket: `clipboard_set` offers text to the guest, `clipboard_get` reads it back, `clipboard_changed` reports guest copies; the in-memory clipboard exists only with `--control-socket`) | partial (the browser has no clipboard sync — out of MVP scope; the control socket's clipboard verbs work as in headless) | available (text both ways through the RDP clipboard channel, CRLF and LF converted, via one `MemoryClipboard` for the run; the control socket's clipboard verbs share it. Rich formats and files are not shared) |
| **Paste-as-keystrokes** | | | | |
| Paste-as-keystrokes (enable flag) | available | available (`--enable-paste-as-keystrokes`) | missing (out of MVP scope; clipboard sync is in Future work list in PLAN-web-frontend.md) | missing (every SPICE attempt is started with `enable_paste` false, in `SpiceAttempts::run()` in `ryll/src/rdp.rs`) |
| Paste-as-keystrokes (runtime trigger via Ctrl+Alt+V) | available | available (via the control socket `paste` verb; Ctrl+Alt+V itself is GUI-only) | missing (out of MVP scope) | missing (paste is off, so the inputs channel's `handle_input_event()` ignores the control socket `paste` verb) |
| Paste-as-keystrokes (headless `--paste-text`) | n/a — intrinsic (GUI reads clipboard; `--paste-text` is headless-only per README) | available | missing (out of MVP scope) | missing (`run_rdp` never reads `--paste-text`; paste is off) |
| Paste-as-keystrokes (inter-character delay `--paste-char-delay-ms`) | available | available (also settable per-request via the control socket `paste` verb's `char_delay_ms`) | missing (out of MVP scope) | missing (paste is off) |
| Paste-as-keystrokes (vdagent auto-disable) | available (`RyllApp::trigger_paste()` in `ryll/src/app/`) | available (vdagent state logged from the session's `SessionState` watch; `enable_paste` flag passed through) | missing (out of MVP scope) | missing (paste is off) |
| Paste character validation / error dialog | available | partial (the renderer's `run_headless()` — `PasteFailed` logged and causes non-zero exit; control-socket clients get a `paste_failed` event; no interactive dialog) | missing (out of MVP scope) | missing (paste is off) |
| **USB Redirection** | | | | |
| USB redirection channel (protocol layer) | available | available (`--usb-disk`, `--usb-disk-ro` CLI flags work in headless) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) | available (the channel links; `--usb-disk` and `--usb-disk-ro` reach it, as below. The RDP client cannot drive it) |
| USB interactive panel (Menu → USB) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Connect physical USB device (Linux only) | available | missing (the renderer's `run_headless()` — `_usb_tx` is created but `usb_rx` is never sent a ConnectPhysical command in headless; no CLI flag for physical devices) | missing (out of MVP scope) | missing (the USB command channel is created closed in `SpiceAttempts::run()`, and there is no flag for a physical device) |
| Connect virtual USB disk (`--usb-disk`) | available | available | missing (out of MVP scope) | available (`run_rdp` hands the disks to every SPICE attempt, and the usbredir channel connects them at its hello; not yet tested through `--rdp`) |
| Connect read-only virtual USB disk (`--usb-disk-ro`) | available | available | missing (out of MVP scope) | available (as `--usb-disk`) |
| Runtime USB disk addition (file picker) | available | n/a — intrinsic (no GUI file picker) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| USB error reporting (bug report integration) | available | partial (the renderer's `run_headless()` — errors logged; no zip written unless `--pedantic`) | missing (out of MVP scope) | partial (errors are logged; `run_rdp` writes bug reports only from the `--pedantic` observer) |
| **Folder Sharing (WebDAV)** | | | | |
| WebDAV folder sharing channel (protocol layer) | available | available (`--share-dir`, `--share-dir-ro` CLI flags work in headless) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope) | available (the channel links; `--share-dir` and `--share-dir-ro` reach it, as below. The RDP client cannot drive it) |
| WebDAV interactive panel (Menu → Folders) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Share directory (`--share-dir`) | available | available | missing (out of MVP scope) | available (`run_rdp` hands the share to every SPICE attempt, and the WebDAV channel serves it from the start; not yet tested through `--rdp`) |
| Read-only share (`--share-dir-ro`) | available | available | missing (out of MVP scope) | available (as `--share-dir`) |
| Runtime directory picker (native file dialog) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| **Statistics and Instrumentation** | | | | |
| FPS tracking (sliding window from DisplayMark) | available | partial (no FPS is computed; the control socket's `stats` verb returns `marks_received`, whose rate between polls is the GUI's measure) | partial (no stats panel in the browser MVP; the control socket's `stats` verb works as in headless) | partial (no FPS is computed; the control socket's `stats` verb returns `marks_received`) |
| Bandwidth sparkline (rolling bytes/sec graph) | available | partial (no graph; the control socket's `stats` verb returns the display channel's `bytes_in`, whose rate between polls is its bandwidth) | partial (no graph; the control socket's `stats` verb works as in headless) | partial (no graph; the control socket's `stats` verb returns the display channel's `bytes_in`) |
| PING interval sparkline (inter-PING interval graph) | available | partial (no graph; raw samples are reachable as control-socket `latency` events) | missing (out of MVP scope) | partial (no graph; raw samples are reachable as control-socket `latency` events) |
| Inputs round trip (`RTT` in the status bar) | available | missing (headless sends no pointer input, and a round trip is only measured from pointer messages) | partial (no stats panel; the browser's pointer drives control-socket `input_rtt` events) | partial (no stats panel; the RDP client's pointer drives control-socket `input_rtt` events) |
| Periodic stats to stdout (headless only) | n/a — intrinsic (GUI shows live stats in status bar) | available (every 10 s: DISPLAY_MARKs, image draws, and bytes in and out across every channel) | n/a — intrinsic (browser context) | missing (`run_rdp` logs no periodic stats line) |
| `--latency-file` latency measurements export | missing (`Args::latency_file` in `ryll/src/config.rs` — flag declared but never read or acted upon anywhere in the codebase) | missing (same — flag is declared but unused; control-socket `latency` events are the working alternative) | missing (out of MVP scope) | missing (same — `Args::latency_file` in `ryll/src/config.rs` is declared and never read) |
| Channel state snapshots (for bug reports) | available | available | missing (out of MVP scope) | available (`run_rdp` keeps `ChannelSnapshots` for every attempt; `--pedantic` reports and the `stats` verb read them) |
| Traffic ring buffer (always active) | available | available | missing (out of MVP scope) | available (`run_rdp` builds the `TrafficBuffers` every attempt records into) |
| **Protocol Gap Tracking** | | | | |
| Protocol gap counter (`Gaps: N` status bar) | available | missing (ryll's `run_headless()` — gap observer registered; warn_once fires; but the renderer's event loop has no handler to surface the count) | missing (out of MVP scope) | missing (`run_rdp` registers the gap observer but nothing surfaces the count) |
| Protocol gap floating window (click Gaps button) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| `--pedantic` mode (auto bug-report per gap) | available | available (partial: ryll's `run_headless()` — `app_snapshot` is an empty default, so pedantic zips omit surface/bandwidth/latency data) | missing (out of MVP scope) | partial (`run_rdp` registers the observer with an empty `app_snapshot`, so pedantic zips omit surface, bandwidth and latency data) |
| `--pedantic-dir` directory selection | available | available | missing (out of MVP scope) | available |
| **Bug Reports** | | | | |
| Bug report dialog (F12 / Menu → Report) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Bug report zip assembly (metadata, pcap, screenshot, metrics) | available | partial (ryll's `run_headless()` — `--pedantic` zips have an empty `app_snapshot`; no interactive zip generation in headless) | missing (out of MVP scope) | partial (only `--pedantic` writes zips, with an empty `app_snapshot`; there is no other trigger) |
| Bug report display region selection | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Bug report trigger-time screenshot capture | available | n/a — intrinsic (no continuously-painted display) | missing (out of MVP scope) | missing (the only reports `--rdp` writes are pedantic ones, which take no screenshot: see `register_pedantic_observer()` in `ryll/src/bugreport.rs`) |
| Runtime metrics in bug reports (Linux `/proc`) | available | available (sampled in `metrics.rs`; used by pedantic observer) | missing (out of MVP scope) | available (sampled for each pedantic report) |
| **Screenshot** | | | | |
| Screenshot (F8 / Menu → Screenshot) | available | available (via the control socket `screenshot` verb — PNG or raw RGBA of any live surface; the backing `SurfaceMirror` is created on first use) | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope "Recording / capture") | available (via the control socket `screenshot` verb, against the mirror the RDP client is served from) |
| Multi-surface screenshot (suffix `-1`, `-2`, …) | available | available (the `screenshot` verb takes a `surface_id` parameter; one call per surface) | missing (out of MVP scope) | available (the `screenshot` verb takes a `surface_id`; one call per surface) |
| **Live Traffic Viewer** | | | | |
| Traffic viewer panel (F11 / Menu → Traffic) | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Traffic viewer channel filters | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| Traffic viewer pause/resume | available | n/a — intrinsic (no GUI) | missing (out of MVP scope) | n/a — intrinsic (no ryll UI inside an RDP session) |
| **Notifications** | | | | |
| In-app notifications panel (bell / Menu bell) | available | missing (ryll's `run_headless()` — store exists and is populated, but no bell or side panel in headless; no stdout drain) | missing (out of MVP scope) | missing (`run_rdp` builds a notification store but nothing shows it to the client or prints it) |
| SPICE_MSG_NOTIFY surfacing | available | partial (stored in notification store; printed nowhere in headless event loop unless captured by `--pedantic`) | missing (out of MVP scope) | partial (logged by the main channel's `handle_message()`; nothing on `run_rdp`'s event bus stores or shows it) |
| Gap observer notification feed | available | partial (registered by `register_gap_notification_observer()` in ryll's `run_headless()`; notifications accumulate in store but are never printed to stdout) | missing (out of MVP scope) | partial (registered by `run_rdp`; notifications accumulate in the store but are never shown or printed) |
| **Reconnect / Lifecycle** | | | | |
| Reconnect on disconnect (Reconnect button) | available | n/a — intrinsic (no GUI; headless exits when main channel disconnects) | available (SPICE session held while PeerConnection is dead; browser auto-reconnects with 1 s/2 s/4 s/8 s/16 s backoff, max 5 attempts then a manual button) | available (automatic, with no button: SPICE is retried after 1, 4 and 16 s while the RDP client stays connected; see the RDP frontend section below) |
| State preserved across reconnect (disks, share dir, paste toggle) | available | n/a — intrinsic (headless does not reconnect) | partial (SPICE session continuity is preserved — the bridge is reaped and rebuilt, leaving the SPICE channels untouched; per-tab UI state is browser-managed) | available (every SPICE attempt is given the same disks and share, clipboard text is kept, and the RDP client stays connected; paste is never on) |
| Notification history preserved across reconnect | available | n/a — intrinsic | n/a — intrinsic (browser tab holds its own page state) | partial (one store, built by `run_rdp`, lasts the whole run, so a SPICE reconnect keeps it, but nothing shows it) |
| Disconnect dialog | available | n/a — intrinsic (no GUI) | missing (not in MVP scope; browser tab closure is the UX) | partial (during an outage the client is shown the last frame at half brightness with no text saying why (`poll_outage()` in `shakenfist-spice-rdp/src/display.rs`); when ryll gives up it disconnects the client, which shows its own message) |
| Graceful Ctrl+C shutdown | available | available | available (SIGTERM/Ctrl-C drains axum then explicitly closes the active bridge so DTLS/SRTP tears down cleanly before the process exits) | available (SIGINT only: the RDP server is stopped first, each client is asked to end, then SPICE is torn down and ryll exits 0) |
| Cancel superseded connection attempt | available | n/a — intrinsic (headless does not reconnect) | n/a — intrinsic (single-session MVP) | n/a — intrinsic (nothing starts a SPICE attempt while another runs: the supervisor runs them one after another) |
| **Window / Session Persistence** | | | | |
| Window size/position persistence (eframe persistence) | available | n/a — intrinsic (no window) | n/a — intrinsic (browser manages its own viewport) | n/a — intrinsic (the RDP client owns its window) |
| Open side panels survive reconnect | available | n/a — intrinsic (no GUI) | n/a — intrinsic (no side panels in web MVP) | n/a — intrinsic (no ryll panels inside an RDP session) |
| **Capture Mode** | | | | |
| Protocol pcap capture (`--capture`) | available | available | missing (out of MVP scope; see PLAN-web-frontend.md §Out of MVP scope "Recording / capture") | available (every SPICE attempt writes to the one capture session) |
| Display video capture (`display.mp4` via openh264) | available | missing (only the GUI's frame loop calls `CaptureSink::frame`; headless keeps no continuously-painted surface to encode, so `--capture` writes the pcaps but no `display.mp4`) | missing (out of MVP scope) | missing (only the GUI's frame loop calls `CaptureSink::frame`, so `--capture` writes the pcaps but no `display.mp4`) |
| Session metadata (`metadata.json`) | available | available | missing (out of MVP scope) | available |
| **Diagnostics / Verbose Logging** | | | | |
| Verbose logging to stderr (`-v`) | available | available | available (stdout logging works regardless of mode) | available |
| File logging to `/tmp/ryll.log` (`-v`) | available | available | available | available |
| Intimate logging (`--intimate`, keystrokes/mouse) | available | available | missing (no interactive input to log in web MVP that wouldn't also appear in the browser console) | available (`run_rdp` passes the global log config to each attempt, so the inputs channel logs the RDP client's input as it does the GUI's) |
| **Connection Source** | | | | |
| VDAgent connection status (agent_connected flag) | available (status-bar `Agent` indicator; a server with no agent is notified as such, not as a disconnect) | available (read from the session's `SessionState` watch and logged; also a control-socket event and `status` field) | available (wired through same channel path) | available (each attempt's agent state is copied into a watch the input handler and the control socket's `status` and `agent_connected` event read) |
| TCP keepalive on all channel sockets | available | available | available (inherited from SpiceClient) | available (inherited from SpiceClient) |
| **Authentication / Session Token** | | | | |
| Per-launch URL token (web mode only) | n/a — intrinsic (no HTTP server) | n/a — intrinsic (no HTTP server) | available (MVP; random 32-byte token printed to stdout) | n/a — intrinsic (no HTTP server; the RDP counterpart is the generated password in the RDP frontend section) |
| TLS for browser-facing endpoint | n/a — intrinsic | n/a — intrinsic | available (`--web-tls-cert` / `--web-tls-key` flags; axum-server with rustls) | n/a — intrinsic (no browser; RDP's own TLS is in the RDP frontend section) |
| WebRTC media socket binding | n/a — intrinsic (no WebRTC) | n/a — intrinsic (no WebRTC) | available (`--web-media-addr` takes an address or an interface name, `--web-media-port` pins the UDP port for a firewall rule, `--web-ice-server` supplies STUN/TURN URLs) | n/a — intrinsic (no WebRTC) |
| **Web frontend (--web mode)** | | | | |
| HTTP server with per-launch URL token | n/a — intrinsic | n/a — intrinsic | available (`ryll/src/web/server.rs`) | n/a — intrinsic (no HTTP server) |
| Embedded browser shell (HTML/JS/CSS via include_bytes!) | n/a — intrinsic | n/a — intrinsic | available | n/a — intrinsic (no browser) |
| WebRTC SDP signalling endpoint (`POST /offer`) | n/a — intrinsic | n/a — intrinsic | available | n/a — intrinsic (no WebRTC) |
| Synthetic video stream over WebRTC (test pattern) | n/a — intrinsic | n/a — intrinsic | available (SyntheticFrameSource) | n/a — intrinsic (no WebRTC) |
| Synthetic audio stream over WebRTC (440 Hz tone) | n/a — intrinsic | n/a — intrinsic | available | n/a — intrinsic (no WebRTC) |
| Real SPICE display frames over WebRTC | available (egui paint loop) | n/a — intrinsic (no encoder driven from real frames) | available (RealFrameSource over SurfaceMirror) | n/a — intrinsic (no WebRTC; frames reach the RDP client as bitmaps, see Display) |
| Real SPICE audio over WebRTC (Opus passthrough) | available (cpal sink) | n/a — intrinsic (no audio sink) | available (Opus path; PCM-only servers give silent audio with a one-time warn, as `WebPlaybackSink` discards raw PCM and does not encode it) | n/a — intrinsic (no WebRTC; audio reaches the RDP client over its audio channel, see Audio) |
| Browser-side keyboard/mouse input over datachannel | available (egui inputs) | n/a — intrinsic (no input device) | available | n/a — intrinsic (no browser; RDP input is under Keyboard / Mouse Input) |
| Browser-side cursor overlay (datachannel) | available (egui overlay) | n/a — intrinsic | available | n/a — intrinsic (no browser; the RDP pointer is under Cursor) |
| **RDP frontend (--rdp mode)** | | | | |
| RDP listener (`--rdp-host`, `--rdp-port`) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (loopback by default; port 3389, or an ephemeral port if 3389 is taken, unless a port is named; needs a build with the `rdp` feature) |
| Login credentials (`--rdp-user`, `--rdp-password`) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (user `ryll` and a password generated at each launch unless given; both printed to stdout at launch, and compared in constant time) |
| TLS security mode (`--rdp-security tls`, the default) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (the username and password are checked by ryll inside TLS; the domain a client sends is ignored) |
| NLA security mode (`--rdp-security nla`) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (CredSSP; a client that cannot do NLA is refused) |
| Generated certificate and printed SHA-256 fingerprint | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (a self-signed certificate made at each launch, held in memory; its fingerprint is printed in the form RDP clients show) |
| Own certificate (`--rdp-tls-cert`, `--rdp-tls-key`) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (PEM, read once at startup; an unreadable or mismatched pair fails the launch) |
| One client at a time, taken over by the next to authenticate | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (the connected client is disconnected only once the new one has authenticated) |
| Bounded handshake (one unauthenticated client at a time, 30 s limit) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available |
| Held keys and buttons released when a client leaves or is taken over | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available |
| Outage frame (last frame at half brightness while SPICE is down) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | n/a — intrinsic (no RDP listener) | available (input and resize requests made meanwhile are dropped, not replayed) |
| SPICE auto-reconnect | available (the GUI retries with the same rules before showing its disconnect dialog, `ryll/src/app/reconnect.rs`) | missing (ryll's `run_headless()` runs the session once and exits when it ends) | missing (`run_web` in `ryll/src/main.rs` runs the session once; a lost SPICE session is not retried) | available (retries after 1, 4 and 16 s, honouring a single-use or expired `.vv` ticket; the client and the listener stay up throughout) |
