# Features

This is the detailed feature catalogue for ryll. For a quick overview
see the [README](https://github.com/shakenfist/ryll/blob/develop/README.md);
for how each subsystem works internally see
[docs/index.md](/components/ryll/index/).

## Rendering and display

- **Immediate mode rendering** - Uses egui for efficient display rendering without accumulating objects.
- **Full draw-op coverage** - Handles `DRAW_FILL`, `DRAW_OPAQUE`, `DRAW_COPY`, `DRAW_BLEND`, `DRAW_BLACKNESS`, `DRAW_WHITENESS`, `DRAW_INVERS`, `DRAW_TRANSPARENT`, `DRAW_ALPHA_BLEND`, and `COPY_BITS`. BIOS, GRUB, and kernel-console rendering paint correctly (solid backgrounds, clean scroll regions). Deferred ops (`DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT`, `DRAW_COMPOSITE`) warn once per session with a first-occurrence hex dump so gaps are visible without flooding the log.
- **Image decompression** - LZ, GLZ, ZLIB_GLZ_RGB, LZ4, JPEG, QUIC, and Pixmap image types; MJPEG via SPICE streaming with hardware-accelerated decoding where available (ImageIO on macOS, WIC on Windows, VA-API on Linux) and automatic fallback to software decoders.
- **Multi-monitor support** - Connect multiple display channels with `--monitors N` for multi-head configurations.
- **Window auto-fit** - The ryll window tracks the guest's display surface size: every primary `SURFACE_CREATE` resizes the window to match (modulo an 8-pixel alignment that mirrors what we send the guest via `VDAgentMonitorsConfig`). Maximised or fullscreen windows are left alone, and the surface renders at native size inside them. Toggle `Obey guest size hints` in the hamburger menu (or launch with `--no-obey-guest-size`) to pin the window — useful for fixed-size capture or for guests that flap between resolutions on their own. The toggle is a session-level preference and survives reconnect. Resolution changes are also surfaced as Info notifications ("Display resolution: WxH"), debounced over 500 ms so a boot-time mode probe storm or a drag-resize through many sizes collapses to a single entry rather than spamming the panel.
- **Cursor rendering** - Server cursor shapes with fallback default arrow.
- **Display channel capabilities** - Advertises COMPOSITE, MONITORS_CONFIG, SIZED_STREAM, and A8_SURFACE so the guest QXL driver uses efficient rendering paths instead of falling back to slow software blits.
- **Window persistence** - Window size and position are restored across launches via eframe's `persistence` feature, which writes the egui memory snapshot to the platform's per-app config directory (`~/.local/share/ryll/` on Linux, `~/Library/Application Support/ryll/` on macOS, `%APPDATA%\ryll\` on Windows).

## Audio

- **Audio playback** - SPICE playback channel with raw PCM and Opus codec support; lock-free ring buffer to dedicated audio thread via cpal.

## Input and interaction

- **Multi-channel support** - Handles main, display, cursor, inputs, playback, usbredir, and webdav channels.
- **Paste-as-keystrokes** - Type arbitrary text into guests without vdagent by translating characters into US-QWERTY scancode sequences. Cooperative timer-driven state machine keeps the inputs channel responsive during long pastes. Triggered via Ctrl+Alt+V shortcut or Menu → Paste in the GUI (when enabled). Automatically disabled when vdagent is connected. Characters are mapped to US-QWERTY scancodes; guests with a different keyboard layout will see different characters. Maximum paste length is 4096 characters. CLI flags: `--enable-paste-as-keystrokes`, `--paste-text TEXT`, `--paste-char-delay-ms N`.
- **Screenshot capture** - Press F8 or use Menu → Screenshot to save the current display as a PNG via a native file dialog. With multiple monitors, one PNG per surface is saved with `-1`, `-2` suffixes.

## Devices and sharing

- **USB device redirection** - Forward physical USB devices (Linux only) or present RAW disk images as virtual USB mass storage devices on all platforms. Interactive USB panel in the GUI for device enumeration, connect/disconnect, and adding disk images at runtime. CLI flags (`--usb-disk`, `--usb-disk-ro`) for headless/scripted use.
- **USB device management** - Use Menu → USB for a side panel to browse available devices, connect/disconnect physical or virtual USB devices, add RAW disk images via native file picker, and monitor connection status with elapsed time; USB errors integrate with bug reporting.
- **WebDAV folder sharing** - Share a local directory with the guest VM via the SPICE WebDAV channel. The guest mounts the share via `spice-webdavd` + `davfs2`. Supports read-write and read-only modes. Interactive "Folders" panel in the GUI for directory selection and share management. CLI flags (`--share-dir`, `--share-dir-ro`) for headless/scripted use.
- **Folder sharing panel** - Use Menu → Folders for a side panel to select a local directory to share with the guest, toggle read-only mode, and monitor sharing status with elapsed time.

## Connectivity and sessions

- **TLS support** - Secure connections with inline CA certificates from .vv files.
- **Reconnect on disconnect** - When a session ends unexpectedly, the disconnect dialog offers a Reconnect button that drops all per-session state and re-attempts the SPICE handshake against the same target without exiting the application. Preserves the configured virtual disk list, shared folder, paste-as-keystrokes toggle, and notification history; resets statistics, traffic buffers, and per-channel state. See [session-lifecycle.md](/components/ryll/session-lifecycle/) "Reconnection" for the full lifecycle.
- **Ticket lifecycle handling** - Honours `delete-this-file=1` (single-use ticket: auto-reconnect suppressed) and the ryll extension key `ticket-valid-until` (expiry warning and modal) in .vv files. See [configuration.md](/components/ryll/configuration/) for the full key reference.
- **Graceful Ctrl+C shutdown** - Cross-platform signal handling via `ctrlc` crate; the GUI and headless event loops check a flag and shut down cleanly, ensuring capture files are finalized.

## Diagnostics and debugging

- **Statistics tracking** - Sliding-window FPS (from MARK boundaries), throughput, and latency measurements.
- **Bandwidth sparkline** - Real-time bandwidth graph in the status bar showing rolling bytes/sec history.
- **Latency sparkline** - Bottom stats panel shows client-observed inter-PING interval from the main channel (lower variance is better; spikes indicate network or server stalls).
- **Streaming indicator** - Small triangle (▶) glyph in the status bar reflects the live SPICE display-stream state: grey (off), green (active), amber (a stream was destroyed in the last 5 s), red (≥3 destroys in 30 s with mean lifetime <3 s — fires a `Warn` notification once per minute). Hover for per-stream codec, dimensions, and decoded-frame counts. See [troubleshooting § Streaming indicator](/components/ryll/troubleshooting/#streaming-indicator).
- **Protocol-gap counter** - `Gaps: N` button in the status bar tracks the number of distinct protocol edge cases seen this session (unknown opcodes, deferred ops, recoverable decode failures). Highlights red when N > 0; click to open a floating window listing the keys. Complements `--pedantic` mode.
- **File logging** - Verbose mode writes to `/tmp/ryll.log` for debugging.
- **Unbuffered pcap I/O** - Packet writes go directly to disk so pcap data survives abrupt termination.
- **Bug reports** - Press F12 or use Menu → Report to capture a self-contained zip with metadata, channel state, pcap traffic, runtime metrics, and a screenshot taken the moment the dialog opened so transient display artefacts survive the form-filling delay; Display reports with a region selection also include a crop of the submit-time surface.
- **Live traffic viewer** - Press F11 or use Menu → Traffic for a real-time colour-coded feed of SPICE protocol messages with per-channel filters and pause/resume.
- **In-app notifications panel** - Surfaces protocol gaps, bug-report status, SPICE_MSG_NOTIFY messages (e.g. QEMU's "channel is insecure" warnings), and connection-state transitions (connect / reconnect cycle / channel drop / agent state) on a single bell + side-panel surface. Each entry carries a per-row "File…" button that produces a bug-report zip — when a live ring-buffer snapshot exists for the notification (≤ 60 s old, within the last 5 notifications), the report contains the pcap and channel state from the moment the notification fired; otherwise it falls back to post-event ring contents.

## Modes

- **Headless mode** - Run without GUI for automated testing and benchmarking.
- **Cadence mode** - Automatic keystroke injection every 2 seconds for latency testing.
- **Web mode** - Browser frontend over WebRTC with native TLS; see
  [web-frontend.md](/components/ryll/web-frontend/) for the operator guide.

The sections below describe the non-GUI modes in more detail.

### Capture mode

Record protocol traffic and display frames for debugging:

```bash
ryll --file connection.vv --capture /tmp/capture
```

This writes:

- `metadata.json` — session context (ryll version, platform, target host)
  for self-describing capture directories in bug reports
- `main.pcap`, `display.pcap`, `cursor.pcap`, `inputs.pcap`, `usbredir.pcap`,
  `webdav.pcap` — per-channel pcap files with fake TCP/IP headers, openable
  in Wireshark
- `display.mp4` — H.264 video of the display surface at real timing

See [STYLEGUIDE.md](https://github.com/shakenfist/ryll/blob/develop/STYLEGUIDE.md)
for capture conventions.

### Headless mode

For automated testing without a GUI:

```bash
ryll --file connection.vv --headless --cadence
```

This will:

- Connect to the SPICE server
- Process display updates (decompress images)
- Send automatic keystrokes every 2 seconds
- Print statistics periodically

Headless mode supports a Unix-socket control interface via
`--control-socket <path>` for driving the session from external
tools. See [control-socket-protocol.md](/components/ryll/control-socket-protocol/)
for the full verb and event reference, and
[`examples/control-socket-demo.py`](https://github.com/shakenfist/ryll/blob/develop/examples/control-socket-demo.py)
for a runnable Python example client.

### Web mode (`--web`)

```bash
ryll --web session.vv
```

Open the printed `http://<host>:<port>/?token=...` URL in
Firefox or Chrome. The desktop is visible and audible;
keyboard and mouse work; the cursor is rendered as an overlay.
Audio requires the SPICE server to negotiate Opus (xspice and
QEMU defaults). Click the volume button on the page to enable
audio after it loads (browser autoplay policy).

Optional flags:

- `--web-host 0.0.0.0` — bind address (defaults to loopback)
- `--web-port 8080` — port (defaults to ephemeral)
- `--web-tls-cert /path/cert.pem` + `--web-tls-key /path/key.pem` —
  serve over HTTPS with native TLS (both required together; see
  [web-frontend.md](/components/ryll/web-frontend/) for cert recipes)
- `--web-media-addr eth0` — address or interface name for the WebRTC
  media sockets (repeatable; defaults to every non-loopback interface
  address)
- `--web-media-port 41000` — pin the media UDP port so one firewall
  rule covers it (defaults to ephemeral)
- `--web-ice-server stun:stun.example.com:3478` — STUN/TURN server
  (repeatable; unnecessary when browser and host share a LAN)

See [configuration.md](/components/ryll/configuration/#web-mode) for the full flag
reference, including what each of these refuses and why.

Closing the browser tab reaps the bridge and encoder within ~1 second
(the SPICE session stays live); reopening the same URL establishes
a fresh connection. The browser auto-reconnects on transient
ICE failures with 1 s/2 s/4 s/8 s/16 s backoff. A reference
systemd unit is at `examples/ryll-web.service`. See
[web-frontend.md](/components/ryll/web-frontend/) for the full operator guide.

### Auto-snapshot mode (`--auto-snapshot-interval`)

When `--auto-snapshot-interval N` is set, ryll fires a complete
bug-report zip every N seconds into a rolling subdirectory
`<bug-report-dir>/auto-snapshots/`. This "flight-data-recorder"
mode captures full session state regardless of whether the
operator notices a symptom — useful for intermittent issues like
audio silences that last only 30 seconds mid-session.

A startup `Info` notification confirms the mode is active. The
status bar shows `Auto-snapshot: {saved}/{cap}` while the mode
is enabled. The default rolling cap is 20 zips; oldest are pruned
when the cap is exceeded.

Each zip is a full bug-report artefact (channel-state.json with
all channels merged, traffic.pcap covering all channels, metadata,
runtime-metrics, notifications) equivalent to a manual F12 report.

```bash
# Fire every 30 s, keep last 20 zips
ryll --file connection.vv --auto-snapshot-interval 30

# Custom cap and output directory
ryll --file connection.vv --auto-snapshot-interval 60 \
     --auto-snapshot-cap 10 --bug-report-dir /tmp/session

# Minimum recommended interval is 10 s (assembly blocks ~2 s
# for runtime-metrics sampling; shorter intervals cause
# overlapping samples, which is harmless but wasteful).
```

The zip filename encodes the UTC timestamp and session uptime:
`ryll-auto-snapshot-2026-05-18T20-37-42Z-T+47.3s.zip`

### `--pedantic` mode

When enabled with `--pedantic`, ryll writes a bug-report
zip to `./ryll-pedantic-reports/` (or the directory
specified with `--pedantic-dir <path>`) the first time
each distinct protocol gap is seen — unsupported opcodes,
unhandled sub-features, recoverable decode errors.
Capped at 50 reports per session. Useful for surfacing
implementation gaps against a specific guest workload.
The always-visible `Gaps: N` status-bar counter works
without `--pedantic` too; the counter only counts, it
doesn't write.

```bash
ryll --file connection.vv --pedantic
ryll --file connection.vv --pedantic --pedantic-dir /tmp/my-gaps
```

### Notifications

A bell icon in the status bar shows unread notifications. Click it to
open the side panel; closing the panel marks everything read. The bell
tints amber or red when there are unread Warn or Error-severity entries
(such as SPICE "channel is insecure" warnings from QEMU).

Entries are tagged with a source label — `Connection` for connect /
disconnect / reconnect cycle / channel error / guest-agent state
transitions, `Gap` for protocol gaps, `BugReport` for save status,
`SPICE/<channel>` for server NOTIFY messages, `Internal` for everything
else.

Every entry carries a per-row **File…** button that produces a bug-report
zip. ryll captures a snapshot of the traffic ring buffer at the moment a
notification fires. If a live snapshot exists when the button is clicked
(≤ 60 s old, within the last 5 notifications), the zip's pcap and channel
state come from the moment the notification fired and the metadata records
`notification_snapshot: "AtFire"`. After the snapshot has expired the
button still produces a report, using the current ring contents, with
`notification_snapshot: "PostEventOnly"` — the button's visual state
(solid vs. dimmed + hover tooltip) tells you which path a click would
take.

## Comparison with the Python prototype

Ryll started as a Python prototype (also called ryll); the Rust
rewrite is the maintained implementation.

| Feature | Python (ryll) | Rust (ryll) |
|---------|--------------|-------------|
| Threading | Python threads + queues | Tokio async + channels |
| GUI | Tkinter (retained mode) | egui (immediate mode) |
| Image handling | Pillow | Direct pixel buffers |
| Memory efficiency | Object accumulation issues | No accumulation |
| Deployment | Requires Python env | Single binary |
