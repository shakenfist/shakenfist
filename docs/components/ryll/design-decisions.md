# Key design decisions

This page records why ryll is shaped the way it is. Each entry captures a
decision and the reasoning behind it, so a change that contradicts one is
a deliberate choice rather than an accident. See
[Architecture](https://github.com/shakenfist/ryll/blob/develop/ARCHITECTURE.md) for the structure these decisions
produced.


1. **Immediate mode rendering** - egui was chosen because SPICE sends bitmap
   tiles to blit onto surfaces. Retained-mode GUIs (like tkinter) accumulate
   objects, causing memory issues. egui just redraws the current surface state
   each frame.

2. **Async over threads** - The Python version used threads with queues. Rust
   uses tokio async tasks with mpsc channels, which is more idiomatic and
   efficient.

3. **Headless mode** - Essential for automated testing. Runs the full protocol
   stack without GUI overhead. Headless is also the first evidence of the
   project's broader **multi-modal client** stance: the SPICE stack is
   frontend-agnostic, and additional frontends (`--web` browser mode shipped
   end-to-end via the [web frontend plan](/components/ryll/plans/PLAN-web-frontend/)) are
   first-class peers of the GUI rather than retrofits. When you add or modify a feature, ask which
   modes it should be reachable from; if a mode physically cannot host the
   feature, say so in the docs rather than leaving the gap unstated.

4. **Cadence mode** - Sends automatic keystrokes every 2 seconds to generate
   predictable input→display latency measurements.

5. **Graceful Ctrl+C shutdown** - A SIGINT handler in `main.rs` sets a global
   `SHUTDOWN_REQUESTED` AtomicBool. The eframe update loop (`app.rs`) and the
   headless tokio select loop both poll this flag and shut down cleanly,
   ensuring capture sessions are finalized.

6. **Unbuffered capture I/O on dedicated tasks** - Pcap and MP4 writers in
   `capture.rs` write directly to `File` (no `BufWriter`), so written bytes are
   always on disk and survive SIGINT without explicit flush. Both writers run
   on **dedicated tokio tasks** (`pcap_writer_task`, `video_writer_task`); the
   channel handlers and the egui frame loop enqueue via non-blocking `try_send`
   so slow disk cannot back-pressure the SPICE socket or stall the GUI. Queue
   caps `PCAP_QUEUE_CAPACITY = 1024` and `VIDEO_QUEUE_CAPACITY = 8`; drops are
   counted in per-channel `writer_dropped_count` (channels) and
   `AppSnapshot::video_drop_count` (video). MP4 finalisation runs on the
   encoder task after the sender drops, so a bug report assembled within
   milliseconds of `CaptureSession::close()` may see an unfinalised MP4 — see
   the phase-3 plan for the trade-off.

7. **Display channel capabilities** - Ryll advertises COMPOSITE,
   MONITORS_CONFIG, SIZED_STREAM, A8_SURFACE, plus seven more added by the
   stream-caps-and-flap plan: STREAM_REPORT (4), LZ4_COMPRESSION (5),
   PREF_COMPRESSION (6), MULTI_CODEC (8), CODEC_MJPEG (9), CODEC_H264 (11),
   and PREF_VIDEO_CODEC_TYPE (12). Without COMPOSITE, the guest QXL driver
   falls back to a slow software rendering path that sends only raw Pixmap
   data via `draw_copy`, making keyboard input appear to have no effect
   because the client is overwhelmed with uncompressed frames. The newer
   caps cover stream-report feedback to the server's encoder, LZ4-compressed
   images, multi-codec video (H.264 plus the legacy MJPEG fallback), and
   per-codec / per-compression preference messages sent at link-up. See
   the "Display Channel Capabilities" table in [spice-protocol.md](/components/ryll/spice-protocol/)
   for the full
   bit list.

8. **GLZ win_head_dist eviction** - The GLZ dictionary evicts cached images
   based on the `win_head_dist` field from each GLZ header, rather than using
   a fixed cache size. This matches the server's reference window and prevents
   both premature eviction (corrupting cross-frame references) and unbounded
   memory growth.

9. **Pcap TCP segmentation** - Large SPICE messages are split into multiple
   TCP segments in the pcap writer to avoid exceeding the IPv4 maximum packet
   length (65535 bytes), which would panic in the header construction code.

10. **USB panel uses identity-based commands** - The GUI sends device identity
    (bus/address for physical, path/read-only for virtual) rather than
    pre-opened device handles via `UsbCommand`. The channel handler does async
    device lookup and open in its tokio context. This avoids async operations
    in the synchronous egui render loop and keeps device lifecycle management
    co-located in the channel handler. Physical USB device support
    (`RealDevice`, `DeviceSource::Physical`, `UsbCommand::ConnectPhysical`)
    is gated with `#[cfg(target_os = "linux")]` — on macOS/Windows only
    virtual disk devices are available. The file picker for adding virtual disks
    also runs on a background thread with results polled via `try_recv()`.

11. **WebDAV shares local directory via embedded HTTP server** - Each mux
    client gets a `tokio::io::DuplexStream`; hyper parses HTTP/1.1 and
    dav-server handles WebDAV operations against the local filesystem.
    Response data flows back to the main loop via `mpsc::Sender<MuxResponse>`,
    the same pattern used by usbredir's interrupt polling tasks. The Folders
    UI panel mirrors the USB panel structure.

12. **QUIC decoder is a bespoke pure-Rust port** - SPICE QUIC is a
    proprietary image codec (not the IETF QUIC network protocol). No
    pre-existing Rust crate provides SPICE QUIC decoding, so the
    decoder was ported from the canonical C source in
    `spice-common/common/quic.c`. Constant tables (TABRAND_CHAOS,
    BESTTRIGTAB, J) have been verified against the C reference.
    Golomb coding parameters are clamped to safe bounds before use
    to prevent out-of-bounds panics on malformed data.

13. **Multi-monitor via agent infrastructure** - Multiple display channels
    are opened (one per `--monitors N`) and the main channel sends
    `VDAgentMonitorsConfig` to the guest via the VDI port agent protocol.
    The GLZ dictionary is shared across display channels via a
    `GlzDictionary` struct (with notify-based cross-frame reference
    resolution). Surfaces are keyed by `(display_channel_id,
    surface_id)` to prevent cross-channel collisions.

14. **Dedicated audio thread with lock-free ring buffer** - The cpal audio
    output stream runs on a dedicated `std::thread`, not in the tokio
    runtime. This avoids the `unsafe impl Send` that was previously needed
    (cpal streams are `!Send` on macOS/Windows). The tokio network task
    pushes decoded PCM samples into an `rtrb` single-producer
    single-consumer ring buffer; the audio thread drains it into a local
    `VecDeque` for the resampler. This eliminates mutex contention in the
    real-time cpal callback.

15. **Paste-as-keystrokes: cooperative state machine in the select! loop** -
    The paste feature translates text to US-QWERTY scancodes and types them
    as synthetic key events. A `PasteState` struct tracks the current
    character index and sub-step (Press/Release). A conditional third arm in
    the inputs channel's `select!` loop uses `tokio::time::sleep_until` to
    fire at the right moment; between firings the other two arms (server reads
    and UI events) run normally. The `advance_paste` method sends one sub-step
    per invocation and updates the next-fire time. Modifier keys (Ctrl, Shift,
    Alt) are tracked via `KeyDown`/`KeyUp` observations and saved/restored
    around the paste. The `send_key_down`/`send_key_up` helpers bypass event
    recording and modifier tracking for synthetic paste events. Public API:
    `translate_paste(text: &str) -> Result<Vec<PasteKey>, PasteError>`,
    `PasteKey` (struct with press, release, shift fields), `PasteError`
    (enum with Unrepresentable variant).

16. **Mouse mode negotiation** - On session init, ryll requests client mouse
    mode (absolute positioning) via `MOUSE_MODE_REQUEST` if the server
    supports it. If the server remains in server mode (e.g. no SPICE agent),
    ryll sends relative `MOUSE_MOTION` messages instead of absolute
    `MOUSE_POSITION`. The mode is checked on every pointer move in app.rs.

17. **Event-driven egui repaints via `repaint_notify`** - egui only repaints
    when something asks it to. Channel handlers run on the tokio runtime
    and have no direct access to `egui::Context`. Every channel handler
    therefore holds an `Arc<tokio::sync::Notify>` (`repaint_notify`)
    alongside its `event_tx: mpsc::Sender<ChannelEvent>`, and a small
    "repaint bridge" tokio task (spawned from `RyllApp::new`) waits on
    `notify.notified().await` and calls `ctx.request_repaint()` whenever
    a notification arrives. **Convention: every `event_tx.send(...)` call
    in a channel handler must be immediately followed by
    `repaint_notify.notify_one()`.** A 1 Hz fallback in `update()` covers
    time-based UI like the bandwidth and latency sparklines. New channel
    handlers must accept `Arc<tokio::sync::Notify>` in their constructor
    and follow this pairing convention or idle CPU will silently regress.

18. **Draw-op coverage: one `decode_*` per opcode, warn-once everything
    skipped** - Every implemented `DRAW_*` opcode on the display channel
    follows the same shape: a pure `fn decode_<op>(payload) ->
    io::Result<<Op>Outcome>` classifier that parses the wire struct
    and returns an Outcome enum describing what to do (`Paint`,
    `SkipNonOpPut { rop }`, etc.), then an `async fn handle_<op>` shim
    that destructures the outcome, fires `warn_once!` on each skip
    variant, and emits a typed `ChannelEvent`. Any feature the handler
    deliberately ignores (non-`OP_PUT` ROP descriptors, non-solid
    brushes, non-null `SpiceQMask`, non-zero `alpha_flags`, etc.) must
    fire `warn_once!` with a stable colon-delimited static key so the
    gap enters the process-global warn_once registry. Unknown opcodes
    use `log_unknown_once` which registers the same way but includes a
    first-occurrence hex dump. See STYLEGUIDE.md §"warn_once for
    protocol gaps" for the full convention (key format, test
    discipline, append-only contract).

19. **Colour conversion in the channel, not the surface** - SPICE
    colour fields (brush colours, chroma keys, BGRX image pixels) are
    BGRX on the wire; `DisplaySurface` stores pixels as RGBA. The
    conversion lives exclusively in the channel handler (before event
    emission) so surface helpers trust their inputs are already RGBA.
    Concretely: `FillRect.colour`, `ImageReadyChroma.chroma_rgba`, and
    every `ImageReady*.pixels` buffer reach `app.rs` pre-converted.
    The idiom at the channel site is `[(c>>16)&0xff, (c>>8)&0xff,
    c&0xff, 0xff]` for a wire `u32` colour. Do NOT add BGRX handling
    inside `DisplaySurface` — surfaces are RGBA-only.

20. **`--pedantic` mode: registry observer pattern** - The warn_once
    registry is a process-global `HashSet<&'static str>` with a
    `register_gap_observer(Fn(&'static str))` hook. The observer fires
    once per newly-inserted key (with replay-on-late-registration so
    observers don't miss keys fired before they registered). Two
    layers sit on top today: an always-visible `Gaps: N` status-bar
    widget that polls `warn_once_count()` each frame (no observer
    needed), and `--pedantic` mode which registers an observer that
    spawns a tokio task per new gap to write a bug-report zip via
    `BugReport::write_pedantic`. The observer is registered inside
    `RyllApp::new` / `run_headless` so it captures live
    `TrafficBuffers` and `ChannelSnapshots` rather than stubs — this
    matters because the traffic pcap is what makes a pedantic report
    actionable for debugging.

21. **Auto-disconnect snapshots and the bug-report directory chain** -
    Every `ChannelEvent::Error` / `ChannelEvent::Disconnected` calls
    `RyllApp::maybe_write_disconnect_snapshot`, which builds a
    `bugreport::DisconnectCause` (channel name, error message,
    keepalive-timeout flag from `MainSnapshot`, session uptime,
    per-channel diagnostics map) and invokes
    `BugReport::write_disconnect`. The fire-on-every-channel scope
    is deliberate: under ticket-based deployments (oVirt, Kerbside)
    every channel disconnect is permanent, so the data must be
    captured at the moment of failure. A 60 s cooldown is enforced
    via `RyllApp::last_disconnect_report_at` and is updated even on
    write failure to avoid retry storms. Output directory resolution
    (shared with the manual F12 / Menu → Report path via
    `manual_bug_report_dir`):
    `--bug-report-dir` → `<--capture>/bug-reports/` → CWD. The
    `--pedantic-dir` flag falls back through the same chain when
    unspecified: `--pedantic-dir` → `--bug-report-dir` →
    `./ryll-pedantic-reports/`. Runtime metrics are deliberately
    `RuntimeMetrics::unavailable(...)` here — sampling on the GUI
    thread blocks the render loop for ~1 s.

22. **Notifications go through the unified store, not direct UI
    calls** - The notification store at `ryll/src/notifications.rs`
    is the single producer boundary. Channel handlers, the bug-report
    writer, the screenshot dialog, and the gap observer all push
    `NotificationEntry` values via `Arc<Mutex<NotificationStore>>`; the
    GUI side panel and the status-bar bell read from the same store.
    Adding a new notification producer means: build a
    `NotificationEntry::new(severity, source, message)` (optionally
    `.with_visibility(v)`), then `notifications.lock().push(entry)`.
    New `NotificationSource` variants are added to the enum in
    `notifications.rs`; the side panel's `NotificationSource::label()`
    impl dictates how the new variant renders. Bug-report zips
    automatically include any new entries via `notifications.json`.
    Current source inventory: `Gap`, `BugReport`, `Spice {channel,
    what}`, `Internal`, `Connection` (every
    connection-state transition, pushed via the
    `RyllApp::push_connection_event` helper).

    Prefer `RyllApp::push_notification` over a bare
    `notifications.lock().push(entry)` from inside `RyllApp`:
    the wrapper *also* captures a `TrafficBuffers` snapshot
    keyed by the new entry's id. That
    snapshot is what the "File…" button on each
    notification row consumes to produce an at-fire bug
    report. Producers outside `RyllApp` (channel handlers,
    pedantic observer) still go through the raw store —
    they don't have access to the snapshot store, and the
    button falls back gracefully to post-event-only when
    no snapshot exists.

23. **Auto-reconnect: pure state-machine transition, side effects
    at the call site** - The `ReconnectState` enum on `RyllApp`
    (`ryll/src/app.rs`) replaces the old `show_disconnect_dialog`
    boolean. `Idle` / `Pending { attempt, next_at, latest_error }` /
    `Modal(ModalVariant)`. The transition function
    `ReconnectState::on_disconnect()` is pure — it takes the current
    state, an `awaiting_outcome` bool, the cluster-reset timestamp,
    the wall clock, a `ReconnectPolicy`, and the latest error
    string, and returns the next state (or `None` for a duplicate
    storm event to ignore). Side effects — pushing notifications,
    bumping `auto_reconnect_count`, writing the disconnect snapshot,
    logging clock-skew warnings — live at the call site in
    `RyllApp::handle_critical_disconnect`, never inside the
    transition function. This keeps the state machine unit-testable
    (see `app.rs::tests::reconnect_*` and `ticket_*` tests) without
    building a full `RyllApp`. When extending: pure transitions add
    branches to `on_disconnect`; side effects go in the handler. The
    `awaiting_reconnect_outcome` flag on `RyllApp` is the gate that
    distinguishes "the in-flight retry just failed" from "another
    channel in the same storm just dropped" — set when the
    GUI-tick poll calls `reconnect()`, cleared on the next event.
    Three modal variants exist (`Generic { latest_error }`,
    `OneShotConsumed`, `TicketExpired { expired_at }`) driven by
    `ReconnectPolicy` derived from the `.vv` file's
    `delete-this-file` and `ticket-valid-until` keys; the policy
    short-circuits the state machine straight to the matching
    Modal when retry would be doomed.

