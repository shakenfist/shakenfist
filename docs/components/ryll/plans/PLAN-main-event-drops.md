# Main-channel event drops (issue #428)

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly and ground answers
in what the code does today. Consult `ARCHITECTURE.md` for the
event-queue design, `AGENTS.md` for conventions, and
`docs/diagnostics.md` for the bug-report snapshot format. Flag
uncertainty rather than guessing.

## Situation

`EventSink::emit` (`shakenfist-spice-renderer/src/channels/mod.rs:81`)
gives the main channel, and only the main channel, a 5 s send
timeout (`MAIN_EVENT_SEND_TIMEOUT`, `session.rs:65`, applied at
`session.rs:249`). When the UI stops draining the 1024-slot event
queue for longer than that, `tokio::time::timeout` cancels the
in-flight `send` and the event is discarded. All that remains is a
`warn!`. Nothing counts it, and `channel-state.json` is identical
whether it fired zero times or fifty.

The timeout exists for a good reason. K1 (session 001) was an
abandoned-receiver deadlock: main blocked forever on `send().await`
into a queue nobody drained, stopped answering PINGs, and the
server tore the session down. The root cause was fixed, and the
timeout was kept so that a recurrence shows up in the log rather
than as a silent hang. It also keeps main responsive while the UI
is stalled: in test session 011, main answered 615 of 615 PINGs
through a three-minute UI stall during a guest reboot (#430).

The cost is that main's events are not all disposable. Main emits
eight kinds (`main_channel.rs`):

| Event | Emit sites | Nature | Consequence of a drop |
|-------|-----------|--------|------------------------|
| `MouseMode` | 828, 870 | state | GUI keeps sending the old mode's messages until reconnect |
| `AgentConnected` | 813, 1038, 1045 | state | clipboard, paste and resize gating wrong until next transition |
| `Disconnected(Main)` | 511, 575, 1031 | terminal | GUI can sit on a dead session: nothing else reports a clean main exit, and after a keepalive timeout display never notices. Headless and web drain the queue through a fan-out task, so it does not fill there. Sent with `emit_terminal` (step 3) |
| `SessionInitialized` | 810 | one-shot | session-init bookkeeping skipped; UI is rarely stalled this early |
| `ChannelsAvailable` | 938 | one-shot | as above |
| `Notification` | 682, 1025 | user-visible, lossy | a notification never appears |
| `Latency` | 975 | sample | one sparkline point missing |
| `MonitorsConfig` | 549 | debug log only (`app.rs:2102`) | nothing |

`MouseMode` is the one that bit. The GUI decides per frame whether
to send relative `MouseMotion` or absolute `MouseMove` from
`self.mouse_mode` (`app.rs:4400`), which only `ChannelEvent::MouseMode`
updates (`app.rs:2093`). Session 011 sent 3279 `MOUSE_POSITION` and
zero `MOUSE_MOTION` after the server announced `supported_modes=1`
partway through, which is what a dropped server-mode transition
looks like. The guest ignored clicks for the rest of the session.

Two corrections to the issue as filed:

- `SurfaceCreated` and `SurfaceDestroyed` are display-channel
  events. The display channel's sink has no timeout, so they block
  rather than drop. They are out of scope.
- Web mode has the same defect by a different route. Its
  `run_mouse_mode_tracker` (`ryll/src/web/inputs.rs:112`) reads a
  `broadcast` channel and, on `Lagged`, can only warn that "mouse
  mode may be stale". Any fix that keeps mouse mode on an event bus
  leaves this hole open.

## Mission and problem statement

Make it impossible for the client's view of session state to drift
from the server's because a queue was full, and make every
remaining drop visible in a bug report.

The root of the bug is that state is being carried as events. An
event can be dropped or lagged past; the latest value of a piece
of state cannot, if it is published as state. So the fix is:

1. **Count drops.** `EventSink` records each timed-out send, by
   event kind, and the main channel publishes the totals in
   `MainSnapshot`. Do this first: it is independent, and it is the
   evidence #430 needs.
2. **Publish main's state through `tokio::sync::watch`, not the
   event queue.** Mouse mode and agent-connected become a
   `SessionState` handle that the renderer hands to every frontend.
   A `watch` channel holds only the latest value, never blocks the
   sender, and never lags, so the 5 s timeout no longer guards
   anything that matters. The GUI reads it each frame; the web
   tracker awaits `changed()` instead of reading the broadcast bus;
   headless reads it for its status verb.
3. **Make sure the terminal event cannot be lost.** Either confirm
   that session end already reaches the GUI another way (the
   connection thread's `run_connection` returning), or send
   `Disconnected(Main)` without the timeout. On that path the read
   loop is exiting, so blocking there cannot starve PINGs.
4. **Leave the timeout on the remaining lossy events**
   (`Latency`, `Notification`, `MonitorsConfig`), now counted.

### Rejected: treat a 5 s stall as fatal

The issue offers this as an option. It turns a UI stall, which
recovers on its own, into a lost session. The stall itself is
#430's problem. Ending the session would also make the evidence
for #430 disappear with it.

### Rejected: reconcile after the fact

For example, re-requesting mouse mode after a drop. SPICE has no
"tell me the current mouse mode" message: `MOUSE_MODE_REQUEST`
asks for a change, and the server only replies when the mode
changes. Re-reading surface state has the same problem. A watch
channel avoids needing any of this.

## Decisions

These were open questions when the plan was drafted. The
operator accepted the recommendation on each on 2026-10-02.

1. **Remove `ChannelEvent::MouseMode` and `AgentConnected`; do not
   keep them as log-only events.** Two sources of truth for one
   value is how this kind of bug comes back. The GUI's
   agent-connected notification (`app.rs:2211-2220`) moves to a
   `has_changed()` check on the watch. A connect→disconnect→connect
   sequence inside one stall then shows up as no change, which is
   correct for state and acceptable for a notification.
2. **The GUI raises a notification when the drop counter rises:**
   one coalesced Warn entry. The GUI reads the counter from the
   shared `MainSnapshot` mutex, not through the event queue, so it
   works while the queue is wedged. The text is fixed, so the
   varying count does not defeat coalescing (the #429 mistake).
3. **`SessionInitialized` and `ChannelsAvailable` are counted but
   not restructured.** They are emitted before the UI has had a
   chance to stall.

## Execution

One pull request, one commit per step.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Drop accounting. In `shakenfist-spice-renderer/src/channels/mod.rs`, give `EventSink` a shared `Arc<EventDropCounter>` (per-kind `AtomicU64`s keyed by a new `ChannelEvent::kind() -> &'static str`, plus a total and a last-drop timestamp), incremented in the timeout branch of `emit`. Add `events_dropped_count: u64`, `events_dropped_by_kind: BTreeMap<String, u64>` and `last_event_drop_ts_secs: Option<f64>` to `MainSnapshot` (`snapshots.rs:481`), with doc comments in the style of their neighbours, and fill them in `MainChannel::update_snapshot` (`main_channel.rs:1123`). Also add `server_mouse_mode: Option<u32>` to `MainSnapshot`, set from `MAIN_INIT` and `MOUSE_MODE`, so a report can show the server's mode next to the app's existing `mouse_mode` field (`app.rs:2412`). Test with a 1-slot channel and `tokio::time::pause()`: fill it, emit, advance past the timeout, assert the counters. Document the new fields in `docs/diagnostics.md`. |
| 2 | high | opus | none | State via `watch`. Add a `SessionState` type to the renderer: two `watch::Sender`s for mouse mode (`u32`) and agent connected (`bool`), with a cloneable receiver bundle. Create it in the caller and pass it into `run_connection` (`session.rs`), so the GUI, web (`ryll/src/main.rs:634-870`) and headless frontends each hold receivers before the session starts. This keeps the subscribe-before-spawn property `main.rs:667-678` relies on. Main publishes with `send_replace` at the `MouseMode`/`AgentConnected` emit sites. Remove those two `ChannelEvent` variants. In the GUI, replace `self.mouse_mode` updates with a read of the watch at the top of `process_events`, and port the agent-connected notification to a `has_changed()` check. Rewrite `run_mouse_mode_tracker` (`web/inputs.rs:112`) to loop on `changed()`, and replace web's and headless's `agent_connected` `AtomicBool` with the watch. Reconnect (`app.rs:1369-1396`) must get fresh receivers from the new session's `SessionState`, not stale ones. Tests: mouse mode published while the event queue is full is still seen by a receiver; the web tracker forwards a change that happens after a burst which would have lagged the broadcast. |
| 3 | high | opus | none | Terminal event. Find out whether the GUI's critical-disconnect handling (`app.rs:2263`) runs when `Disconnected(Main)` is dropped. Check whether the connection thread (`app.rs:1201`, `1481`) reports `run_connection` returning to the UI by some path other than the event queue. If it does not, give `EventSink` an `emit_terminal` that sends without the timeout, and use it at the three `Disconnected(Main)` sites. Explain in a comment why blocking there cannot recreate K1 (the read loop is exiting, and a dropped receiver returns `Err` immediately). Record the finding either way in the commit message. **Found:** no other path exists. The GUI drains with `try_recv`, ignores the queue closing, and the connection thread only logs `run_connection`'s result. Display and inputs only cover an EOF that reaches them too; after main's keepalive timeout, display has no deadline. `emit_terminal` warns at the deadline and keeps waiting, bounded by the cancel watcher's abort and by the receiver being dropped. The EOF and keepalive sites use it. The `DISCONNECTING` site keeps `emit`: the read loop carries on there, and the EOF that follows reports the end. |
| 4 | medium | sonnet | none | GUI notification for drops (decision 2): watch `events_dropped_count` in the GUI's existing per-frame snapshot read, and push one coalesced Warn notification with a fixed text when it rises. Update `ARCHITECTURE.md`'s `event_tx/event_rx` paragraph (line 268) in one or two sentences to say that session state now travels on a `watch`, and why, not on the event queue. Add a `docs/troubleshooting.md` entry for the `event send timed out` warning that points at the new snapshot fields and #430. |

## Administration and logistics

### Success criteria

- `pre-commit run --all-files` and `make test` pass.
- A mouse-mode or agent-connected change published while the event
  queue is full is still observed by the GUI, web and headless
  frontends (unit tests, steps 1–2).
- A dropped event is visible in `channel-state.json` by kind, and
  the server's mouse mode sits next to the app's.
- No `ChannelEvent` variant carries state that a frontend keeps as
  its source of truth.

### Relationship to other issues

- **#430** (UI stall during reboot) is the trigger and is not fixed
  here. After this lands, a stall costs a counted, harmless drop
  instead of a broken session. The counter is the evidence that
  issue lacks.
- **#431** (motion-ack throttle) is the competing explanation for
  the dead-mouse symptom. With `server_mouse_mode` next to the
  app's `mouse_mode` in a report, a future occurrence can tell the
  two apart.
- Close #428 from the pull request.

### Future work

- The display channel's sink has no timeout, so a UI stall
  back-pressures display reads instead. That is #430's territory.
- Agent state could carry more than a boolean (capabilities,
  `agent_caps_announced`) once it lives on a watch.
- Web's control queue to the browser (`CONTROL_QUEUE_DEPTH` 64) still
  drops when full, so a mid-session mouse-mode push to the browser can
  be lost. It affects only the browser's local cursor drawing, since
  server input reads the watch.
- Headless's stats loop can lag past `Disconnected(Main)` on its
  broadcast bus; it then falls back on `run_connection` returning.
- A browser that connects before MAIN_INIT is told mouse mode 0
  (unknown) and draws no local cursor overlay until the real mode
  arrives.
- spice-server never sends `SPICE_MSG_MAIN_DISCONNECTING`, so that
  handler keeps a timed `emit`.

### Back brief

Before executing any step, back-brief the operator on your
understanding of the plan and how the work aligns with it.
