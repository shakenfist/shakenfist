# Session lifecycle

A ryll session has to shut down without losing buffered work, and survive
a dropped connection without leaking the threads and sockets of the
attempt it replaced. This page covers the graceful shutdown path and the
reconnection machinery: what is recreated, what survives, how superseded
attempts are cancelled, and when reconnection happens automatically
rather than through a modal.

## Graceful Shutdown

Ryll installs a SIGINT handler (via `libc::signal`) in `main.rs` that sets a
global `AtomicBool` flag (`SHUTDOWN_REQUESTED`). This allows Ctrl+C to trigger
a clean shutdown instead of killing the process immediately.

- **GUI mode**: `RyllApp::logic` in `ryll/src/app/` checks the flag on every pass
  and calls `ctx.send_viewport_cmd(ViewportCommand::Close)` when set, which
  lets eframe run its normal teardown path and finalize the capture session.
  eframe calls `logic` even while the window is hidden, so Ctrl+C works on a
  minimised window too.
- **Headless mode**: The tokio `select!` loop polls the flag alongside channel
  events and breaks out cleanly when shutdown is requested. However the loop
  ends, `run_headless` then winds the connection task down and joins it
  (aborting it if it has not stopped within two seconds), so a connection
  error is always logged and returned, and a Ctrl+C shutdown still exits 0.
- **RDP mode**: a bridge task turns the flag into the connection's cancel
  flag, and the wait loop stops the RDP server first, so no client is left
  looking at a session being torn down. Every connected client is asked to
  end, the listener closes, and the SPICE side is then stopped. This works
  with a client connected, and exits 0. See [RDP mode](#rdp-mode).

### Unbuffered capture I/O

The pcap channel writers (`PcapChannelWriter` in `capture.rs`) write directly
to `File` without `BufWriter`. This means every packet is persisted to disk
immediately, so pcap data is never lost if the process is interrupted by
SIGINT or any other signal.

These writes happen on a dedicated pcap writer thread, fed
by a bounded channel from the channel handlers. The hot path on each channel
is a non-blocking `try_send`; queue-full means the packet is dropped and
the per-channel `writer_dropped_count` counter is bumped. Slow disk no
longer back-pressures the SPICE socket.

The MP4 video writer also uses unbuffered `File` I/O. The encoder runs
on a dedicated thread and the egui frame loop enqueues frames via
`try_send`. MP4 finalisation (`write_end` for the moov atom) runs on
that thread after the sender drops. Every exit path in `main` (GUI,
headless and web) calls `CaptureSession::close_and_wait`, which blocks
until both writer threads have drained, so a clean exit (window close
or SIGINT) leaves a playable MP4. The wait gives up after five seconds,
so a wedged encoder or a stalled disk cannot stop the process exiting,
at the cost of that MP4. A process that dies without returning through
`main` (SIGKILL, a crash) can also leave it unfinalised. See
[the troubleshooting guide](/components/ryll/troubleshooting/) for the trade-off.

## Reconnection

When the SPICE main channel closes or any secondary channel
reports an unrecoverable error, ryll surfaces a "Disconnected"
dialog with two buttons: Close and Reconnect. The Reconnect
path is implemented in
`RyllApp::reconnect` (`ryll/src/app/`) and is a user gesture.
Before that dialog appears the GUI retries on its own, as
described under [Auto-reconnect with
backoff](#auto-reconnect-with-backoff). `--rdp` does the same
without a dialog (see [RDP mode](#rdp-mode)); `--web` and
`--headless` never reconnect.

### What is recreated

Every reconnect allocates a fresh copy of the per-session
machinery. This is what makes a reconnect equivalent to a
clean session against the same target rather than a
"resume":

- All five mpsc channels (`event`, `input`, `usb`, `webdav`,
  `resize`).
- A new `tokio::runtime::Runtime` inside a freshly spawned
  `std::thread::spawn`, with its own repaint-bridge task.
- A new `Arc<Notify>` for repaint wake-ups, a new
  `ByteCounter`, new `TrafficBuffers`, new
  `ChannelSnapshots`, a new `BandwidthTracker`, and a new
  `VolumeControl`.

Per-session UI state is reset in place: surfaces, cursor
position / visibility / image / texture, the cached
surface rectangle, statistics, last-cadence-key timestamp,
mouse mode, mouse-button state, modifier state,
last-sent resize, pending resize, USB connection state
(channel-ready, connecting / disconnecting flags, error
message, device description, connected-at), WebDAV
connection state (channel-ready, shared-dir, sharing
flag, connected-at, error message), and the disconnect
dialog itself.

### What survives

A reader investigating "did my settings carry over?" wants
this list first. The reconnect path **does not** touch:

- The parsed CLI configuration (target host, port, TLS,
  monitor count).
- The configured virtual-disk list and the configured
  shared folder. Both are stashed in
  `RyllApp::reconnect_virtual_disks` and
  `reconnect_share_dir` at construction so they survive
  the reset.
- The paste-as-keystrokes toggle and inter-character
  delay.
- The "Obey guest size hints" toggle. It is a
  session-level preference (set via the hamburger menu
  or `--no-obey-guest-size`) and is not touched by the
  reconnect path, so a reconnect inherits whatever
  value the user last left.
- The in-app notification store (history of past
  notifications). The store is an `Arc<Mutex<…>>` and
  the same `Arc` is handed to the new connection.
- The egui `Context`, which means window position and
  size, dock layouts, and any open side panels survive
  the reconnect — the Reconnect button feels like the
  same window resuming, not a new one.
- The active capture session, if any.

Anything not in either list above is unintentional and
should be considered a documentation bug; cross-check
against `RyllApp::reconnect` if in doubt.

### Threading and runtime lifecycle

Each reconnect spawns a fresh OS thread with a fresh
`tokio::runtime::Runtime`. The previous attempt is
explicitly cancelled before the new thread spawns:
`RyllApp` holds an `Arc<AtomicBool>` per attempt
(`connection_cancel`); `reconnect()` raises the previous
flag via `prev.store(true, Ordering::Relaxed)`, allocates
a fresh flag, and passes it to the new `run_connection`.

Inside `run_connection`, a small cancel-watcher task
polls the flag every 100 ms and calls `abort()` on the
`AbortHandle` of every channel `JoinHandle` once the flag
is set. The wait loop sees the channel tasks complete
with cancelled `JoinError`s, returns from
`run_connection`, the spawned thread's `block_on`
returns, and the tokio runtime is dropped. End to end,
a superseded attempt exits within roughly 100 ms of the
flag being raised, regardless of whether the underlying
TCP socket is responsive — well inside human reaction
time, so spamming Reconnect no longer accumulates threads
or sockets.

This mirrors the cooperative-cancel pattern used for
`SHUTDOWN_REQUESTED` (the global SIGINT flag). Both
signals share the same 100 ms poll cadence; both rely on
the runtime drop to release the underlying sockets.


### Auto-reconnect with backoff

When a critical channel (Main, Display, Inputs) goes down
mid-session, ryll attempts to recover transparently rather
than presenting a modal immediately. The `ReconnectState`
enum (`ryll/src/app/reconnect.rs`), held by `RyllApp`, drives
the flow:

- `Idle` — connected normally, or never disconnected.
- `Pending { attempt, next_at, latest_error }` — retry
  scheduled. `attempt` ∈ 1..=3; `next_at` is the wall time
  the next `reconnect()` call should fire.
- `Modal(ModalVariant)` — auto-retry has given up and the
  user has to intervene. The variant determines copy and
  available buttons.

Backoff is `[1s, 4s, 16s]` — short first attempt for blip
recovery, longer windows for server restarts; worst-case
~21 s before the modal pops. After three failures the state
machine lands in `Modal(Generic { latest_error })`. The
`latest_error` field carries the most recent attempt's
failure string into the modal body.

The rules themselves (`RECONNECT_BACKOFF_SECS`,
`MAX_RECONNECT_ATTEMPTS`, `RECONNECT_CLUSTER_RESET`, and
`next_step()`, which returns `RetryAfter(d)` or
`GiveUp(Exhausted | Ticket(..))`) are in
`ryll/src/reconnect_policy.rs`; `ReconnectState` only turns
their answer into a state.

A `last_modal_at: Option<Instant>` field tracks when the
modal last opened; further disconnects within 5 minutes
skip the retry budget and go straight back to Modal — a
flapping server cannot make ryll bang away forever. The
manual Reconnect button clears `last_modal_at` so a
user-initiated retry re-arms the full 3-attempt budget.

The transition function `ReconnectState::on_disconnect()`
is pure — it takes the current state, an `awaiting_outcome`
bool, the cluster-reset timestamp, the wall clock, a
`ReconnectPolicy`, and the latest error string, and returns
the next state (or `None` if the event should be ignored as
a channel-storm duplicate). Side effects — pushing
notifications, bumping `auto_reconnect_count`, writing the
disconnect snapshot — live at the call site in
`RyllApp::handle_critical_disconnect`. This keeps the state
machine unit-testable without spinning up the full app.

The `awaiting_outcome` flag distinguishes "the in-flight
reconnect attempt just failed" (advance the attempt
counter) from "another channel in the storm just
disconnected" (no-op). It is set to `true` when the
GUI-tick poll calls `reconnect()` from a `Pending` state
and cleared on the next event (success or failure).

### Connection attempts that fail before a session exists

`run_connection` returns an error only when it could not set a session
up: the dial, TLS, link or authentication failed or timed out (each
stage is bounded, see `DIAL_TIMEOUT` and `HANDSHAKE_TIMEOUT` in
`shakenfist-spice-protocol/src/client.rs`), or a secondary channel
would not open. Once a session exists, channel failures arrive as
`ChannelEvent::Error` instead. The GUI's connection thread sends that
error to the UI on a per-attempt oneshot, `connection_failure_rx`,
which `reconnect()` replaces just as it replaces `event_rx`. A
superseded attempt's failure therefore goes nowhere.

`process_events` acts on the failure only once `event_rx` is empty.
The failure travels on its own channel, so it can arrive before events
the attempt sent ahead of it, and a `SessionInitialized` among those
decides how the failure is handled.
`RyllApp::handle_connection_failed` then routes the failure in one of
two ways:

- If a session was established, or the attempt was an auto-retry, the
  failure goes to `handle_critical_disconnect` like any other
  disconnect. The ticket policy and the retry budget apply.
- Otherwise this was a first attempt or a manual Reconnect that never
  reached a session, so it lands in `Modal(ConnectFailed)`. It does not
  retry in the background. With `delete-this-file=1` it also does not
  claim that a session which never existed consumed the ticket. These
  failures are usually a mistyped port or a server that is down, and
  there is no traffic to capture. They therefore write a disconnect
  snapshot only when `--bug-report-dir` or `--capture` is set, whereas
  the first branch always writes one, as any disconnect does.

### Modal variants and console.vv ticket keys

The `Modal` variant carries one of four discriminants:

- `Generic { latest_error }` — auto-reconnect budget
  exhausted on a reusable ticket. Buttons: Reconnect, Close.
- `OneShotConsumed` — the .vv file set `delete-this-file=1`,
  which ryll interprets as a single-use ticket signal. The
  first link consumed the ticket; auto-reconnect skips
  `Pending` entirely. Buttons: Close only.
- `TicketExpired { expired_at }` — the .vv file set
  `ticket-valid-until=<unix-ts>` and that wall time has
  passed. Auto-reconnect is suppressed and the modal shows
  the expiry time. Buttons: Close only.
- `ConnectFailed { error }` — a first attempt or a manual
  Reconnect failed before it reached a session (see above).
  Buttons: Reconnect, Close, whatever the ticket policy says,
  since nothing was consumed that ryll knows of.

`ReconnectPolicy::forbid_retry(now_wall)` consults the two
ticket-related `Config` fields and says why retry would be
doomed; the GUI maps that onto the matching `ModalVariant`.
The state-machine
transition consults it first, so a single-use ticket
disconnects directly to Modal without burning the budget.

The GUI tick also re-checks the policy at every `Pending`
fire — a long Pending window can outlive
`ticket-valid-until`, and there is no point firing a
reconnect we know the server will reject. A pre-expiry
notification ("Session ticket expires in 30 seconds.")
fires once at T-30s, latched via `ticket_expiry_warned`.

The non-spec `delete-this-file=1` interpretation and the new
`ticket-valid-until` extension key are documented for
producers (Kerbside, oVirt, custom gateways) in
[`console-vv-extensions.md`](https://github.com/shakenfist/kerbside-wt-docs/blob/main/docs/spice/console-vv-extensions.md)
in the kerbside-wt-docs repository.

`auto_reconnect_count: u32` on `AppSnapshot` (serialized into
the bug-report `session.json`) increments on every entry into
`Pending`, so a future zip shows how rocky the session was.

## RDP mode

`--rdp` serves one SPICE session to an RDP client. The RDP listener and
the SPICE connection have separate lifetimes: a client can come and go
while SPICE stays up, and SPICE can be lost and re-established while the
client stays connected. `--web` and `--headless` do not reconnect: `--web`
keeps serving HTTP over a dead session and `--headless` exits.

### When SPICE is lost

Once a SPICE session has been established and is then lost, `--rdp`
reconnects on its own, using the same rules as the GUI's
[auto-reconnect](#auto-reconnect-with-backoff). They live in
`ryll/src/reconnect_policy.rs`, which both call:

- The wait before retry 1, 2 and 3 is 1, 4 and 16 seconds. A retry that
  fails moves on to the next wait; after the third has failed ryll gives
  up. A retry that reaches a session restores all three, so a session that
  is lost again later gets the full budget.
- If the `.vv` file's ticket is single-use (`delete-this-file=1`) or past
  `ticket-valid-until`, there is no retry at all. The ticket is checked
  again after each wait, since a long wait can outlive it.
- The GUI's five-minute rule for a disconnect soon after giving up never
  applies here, because giving up ends the process.

What the RDP client sees:

- It stays connected. The RDP listener keeps accepting clients too, and a
  client that connects during the outage is shown the same darkened frame.
- It is shown the last frame at half brightness, sent once as a whole-surface
  update. A frozen frame alone would look like a hang. There is no text
  overlay. If the guest's last frame was a different size from the client's
  window, the client is resized to it first. If SPICE was lost before any
  frame arrived, the client keeps whatever it was showing.
- When the new session's first surface arrives, the client is sent the whole
  surface at full brightness, resized first if the new session's surface is
  another size. A session that comes back too quickly for the darkened
  frame to be noticed still gets this full resend.
- Keyboard and mouse input, and resize requests, made during the outage are
  discarded, not queued for a session that does not exist yet. ryll counts
  them, and logs how many were dropped once a session is back.
- Text copied on the client or the guest stays on ryll's clipboard for the
  whole run, so text set during the outage reaches the guest's agent once it
  reconnects.

The retry progress is in the log: each attempt, each wait ("reconnecting to
SPICE in 4s (retry 2 of 3)") and, at the end, why ryll gave up.

### When ryll exits

`--rdp` stops serving, disconnects the client and closes the port when:

- The first SPICE attempt never reaches a session (a mistyped port, a server
  that is down). It does not retry, as in the GUI, and exits with the
  attempt's error.
- The retries are used up. This always exits non-zero, even if the last
  attempt ended without an error of its own.
- The ticket is single-use or expired when the session is lost, or expires
  during a wait. No retry is made. The exit status follows the lost
  session's result: non-zero if it ended with an error, zero if it ended
  cleanly.
- Ctrl+C is pressed. This exits 0 (see [Graceful Shutdown](#graceful-shutdown)).

In every case the RDP server is stopped before the SPICE side is torn
down, and the process exits when both have stopped. A stop that stalls is
abandoned after two seconds rather than hanging the exit.

### Takeover

One client is served at a time per SPICE session, and the one that
authenticated last wins:

- A new client takes over only once it has authenticated, in either security
  mode, and before it is sent any pixels. The client it replaces is asked to
  end, stops forwarding input and taking frames at that moment, and has any
  keys or buttons it was holding released in the guest.
- A client that fails to authenticate does not disturb the one being served.
- Only one client at a time may be between connecting and authenticating.
  Others wait in the listen backlog without being accepted. A client that
  has not authenticated within 30 seconds of being accepted is dropped, so a
  stalled or half-open connection cannot hold up the next client for long, nor
  tie up a TLS handshake and an encoder.
- Each connection has its own IronRDP server and event channel, so nothing a
  leaving connection queued (a clipboard message, say) reaches
  the next client.
