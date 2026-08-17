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

- **GUI mode**: The `eframe::App::update()` loop in `app.rs` checks the flag
  each frame and calls `ctx.send_viewport_cmd(ViewportCommand::Close)` when
  set, which lets eframe run its normal teardown path and finalize the capture
  session.
- **Headless mode**: The tokio `select!` loop polls the flag alongside channel
  events and breaks out cleanly when shutdown is requested.

### Unbuffered capture I/O

The pcap channel writers (`PcapChannelWriter` in `capture.rs`) write directly
to `File` without `BufWriter`. This means every packet is persisted to disk
immediately, so pcap data is never lost if the process is interrupted by
SIGINT or any other signal.

These writes happen on a dedicated `pcap_writer_task`, fed
by a bounded mpsc from the channel handlers. The hot path on each channel
is a non-blocking `try_send`; queue-full means the packet is dropped and
the per-channel `writer_dropped_count` counter is bumped. Slow disk no
longer back-pressures the SPICE socket.

The MP4 video writer also uses unbuffered `File` I/O. The encoder runs
on a dedicated `video_writer_task` and the egui frame loop enqueues
frames via `try_send`. MP4 finalisation (`write_end` for the moov atom)
runs on the task after the sender drops; under SIGINT or abrupt
shutdown the MP4 may be left unfinalised. See
[the troubleshooting guide](/components/ryll/troubleshooting/) for the trade-off.

## Reconnection

When the SPICE main channel closes or any secondary channel
reports an unrecoverable error, ryll surfaces a "Disconnected"
dialog with two buttons: Close and Reconnect. The Reconnect
path is implemented in
`RyllApp::reconnect` (`ryll/src/app.rs`) and is a user gesture
— ryll never auto-reconnects.

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
enum on `RyllApp` (`ryll/src/app.rs`) drives the flow:

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

### Modal variants and console.vv ticket keys

The `Modal` variant carries one of three discriminants:

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

`ReconnectPolicy::forbid_retry(now_wall)` consults the two
ticket-related `Config` fields and returns the appropriate
`ModalVariant` when retry would be doomed. The state-machine
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


