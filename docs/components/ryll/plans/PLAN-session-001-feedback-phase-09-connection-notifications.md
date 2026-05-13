# Phase 09: Connection events in the notifications pane (F1)

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`shakenfist-spice-renderer/src/notification.rs:16-39` (the
`NotificationSource` enum — currently `Gap`, `BugReport`,
`Spice { channel, what }`, `Internal`; needs a new
`Connection` variant), `ryll/src/notifications.rs:22-100`
(the `NotificationStore` with its 30 s dedup window — a
duplicate `(source, severity, message, visibility)` tuple
inside the window folds into the existing entry and bumps
its `count`), `ryll/src/app.rs:2021-2031`
(`RyllApp::push_notification` — the producer-side helper),
and the `process_events` event handlers around
`ryll/src/app.rs:1078-1232` (`SessionInitialized`, the
auto-reconnect transitions, `ChannelEvent::Error`,
`ChannelEvent::Disconnected`, `ChannelEvent::AgentConnected`
— the surfaces where notifications need to be pushed).
Cross-reference `AGENTS.md` §21 ("Notifications go through
the unified store, not direct UI calls") for the established
producer pattern.

This phase fixes feature request **F1** — "Surface
(re)connect events in the notifications pane" — identified
from session-001 sources N1 and D1, where multiple
bug-report-zip submissions showed no record of the
connection transitions surrounding the failure. The master
plan classifies F1 as Medium severity ("visible UX gap,
multiple reports show no transitions").

## Situation

### What we already established

**The notification machinery is fully wired.** ryll has a
shared `NotificationStore` (`ryll/src/notifications.rs`)
behind an `Arc<Mutex<…>>` that producers push into and the
side panel + bell read from. The store dedupes within a 30 s
window on `(source, severity, message, visibility)`, so a
burst of identical events folds into one entry with a bumped
`count`. The producer pattern is established by Gap reports,
F8 bug-report status, and SPICE-server NOTIFY messages —
documented as §21 in `AGENTS.md`.

**Connection events are mostly silent today.** The
event-handler arms in `RyllApp::process_events`
(`ryll/src/app.rs`) either log to `tracing` only or update
internal state without surfacing anything to the user:

| Event | Today |
|-------|-------|
| `SessionInitialized` | sets `connected = true`, clears `reconnect_state`, logs `info!` — no notification |
| Critical channel disconnect | drives state machine + modal — no notification |
| Non-critical channel disconnect | `debug!` log only — invisible to the user |
| `ChannelEvent::Error` | drives state machine + modal — no notification |
| Auto-reconnect attempt fires | `info!` log only |
| Auto-reconnect Modal entry | implicit (the modal pops) — no notification |
| Manual Reconnect button | `info!` log only |
| `AgentConnected` | sets `agent_connected` field — no notification |

**The two existing connection-related pushes use the wrong
source.** `RyllApp::handle_critical_disconnect`
(`app.rs:~1180-1203`) pushes `Reconnect attempt N failed: …`
notifications via `NotificationSource::BugReport`. That's a
category error — those are connection events, not
bug-report producer messages — but the source enum had no
better fit at the time the Phase 02 work landed.

**Dedup is enough; no extra fold logic needed.** The 30 s
window naturally handles flap scenarios: 5 reconnects in
30 s collapse to one "Connected to host:port" with count=5.
For events that should always show individually (per-attempt
failure messages), the embedded attempt number makes each
message text distinct so they don't fold.

### What we don't yet know

Nothing material. The user-facing decision is *which* events
deserve notifications and at what severity; resolved under
"Approach" below.

A latent design choice — filtering / source-grouping in the
side panel ("show only Connection events") — would be
useful but is beyond F1. Noted as out of scope.

## Mission and problem statement

Make ryll surface every meaningful connection-state
transition in the notifications pane, so that a user reading
the bell history (or filing a bug report) can see the
session's connection narrative without having to consult the
trace log.

The phase succeeds when:

- Each connection event listed under "Approach" pushes a
  notification with `NotificationSource::Connection` and an
  appropriate severity.
- Existing `BugReport`-sourced reconnect-attempt-failure
  notifications are reclassified as `Connection`.
- The notifications panel surfaces the new
  `Connection` source label distinctly from `BugReport`,
  `Gap`, `Internal`, `Spice/…`.
- The 30 s dedup window handles flap suppression naturally
  — no new fold logic.
- No behaviour regression on existing notification producers
  (Gap, BugReport, Spice, Internal).

## Approach

### Add `NotificationSource::Connection`

Extend the enum in
`shakenfist-spice-renderer/src/notification.rs`:

```rust
pub enum NotificationSource {
    Gap,
    BugReport,
    Spice { channel: ChannelType, what: u32 },
    Internal,
    /// Connection-state transition: initial connect,
    /// reconnect attempt, disconnect, agent up/down. The
    /// `Connection` source groups every event that tells the
    /// user "something changed about the link to the server".
    Connection,
}

impl NotificationSource {
    pub fn label(&self) -> String {
        match self {
            NotificationSource::Gap => "Gap".to_string(),
            NotificationSource::BugReport => "BugReport".to_string(),
            NotificationSource::Internal => "Internal".to_string(),
            NotificationSource::Spice { channel, .. } => {
                format!("SPICE/{}", channel.name())
            }
            NotificationSource::Connection => "Connection".to_string(),
        }
    }
}
```

The side panel already renders any `NotificationSource` via
`label()`; no UI changes needed beyond the variant.

### Event-by-event surface table

| Event | Severity | Message | Notes |
|-------|----------|---------|-------|
| Initial / reconnect `SessionInitialized` | `Info` | `"Connected to <host>:<port>"` | One notification per session start; dedup collapses storm reconnects |
| Auto-reconnect transition Idle → Pending(1) | `Warn` | `"Connection lost — reconnecting…"` | Single line capturing the start of a retry cycle |
| Auto-reconnect attempt fire (tick → reconnect()) | `Info` | `"Reconnect attempt N/3…"` | Fires from the GUI-tick poll just before `reconnect()` is called |
| Auto-reconnect attempt failure | `Warn` | `"Reconnect attempt N failed: <error>"` | **Reclassify** existing push from `BugReport` to `Connection`; same message |
| Auto-reconnect Modal entry (Generic variant) | `Error` | `"Auto-reconnect failed after 3 attempts"` | New — currently only the modal pops |
| Auto-reconnect Modal entry (OneShotConsumed) | `Error` | `"Connection ended — single-use ticket consumed"` | Matches the modal title |
| Auto-reconnect Modal entry (TicketExpired) | `Error` | `"Connection ended — ticket expired"` | Matches the modal title |
| Manual reconnect (button click → `reconnect_manual()`) | `Info` | `"Reconnecting (manual)…"` | Confirms the click registered before connection completes |
| Critical channel disconnect (`Disconnected(Main\|Display\|Inputs)`) | `Warn` | `"Connection lost (<channel> channel disconnected)"` | Existing modal `disconnect_reason` text — folds with the state-machine push |
| `ChannelEvent::Error { channel, message }` | `Error` | `"<channel> channel error: <message>"` | Surfaces the channel-attributed error from Phase 02 Step 4 |
| Non-critical channel disconnect (`Disconnected(Cursor\|Playback\|Usbredir\|Webdav)`) | `Info` | `"<channel> channel disconnected"` | Was `debug!`-only; promote to a low-noise Info entry |
| `AgentConnected(true)` | `Info` | `"Guest agent connected"` | New surface |
| `AgentConnected(false)` | `Info` | `"Guest agent disconnected"` | New surface |

Two notes:

- The "auto-reconnect attempt fire" and "failure" entries
  share a number embedded in the message text, so the
  dedup window doesn't collapse them across attempts.
  Per-cycle they read as a story: "Reconnect attempt 1/3…"
  → "Reconnect attempt 1 failed: …" → "Reconnect attempt
  2/3…" etc.
- The Modal-entry notification fires *in addition to* the
  modal itself. Some users dismiss modals reflexively; the
  notification preserves the event in the bell history.

### Helper method to keep producer call sites tidy

Add a small wrapper next to `push_notification`:

```rust
fn push_connection_event(
    &self,
    severity: NotifySeverity,
    message: impl Into<String>,
) {
    self.push_notification(severity, NotificationSource::Connection, message);
}
```

Used at every connection-event site. Keeps the source
attribution consistent and makes a future grep for
"connection events" cheap.

### Producer call sites (concrete)

| File | Event | New push |
|------|-------|----------|
| `app.rs::process_events` `SessionInitialized` arm | initial / reconnect | `self.push_connection_event(NotifySeverity::Info, format!("Connected to {}:{}", host, port));` |
| `app.rs::handle_critical_disconnect` | Idle→Pending(1) branch | `self.push_connection_event(NotifySeverity::Warn, "Connection lost — reconnecting…".to_string());` |
| `app.rs::handle_critical_disconnect` | reclassify the existing Pending(N+1) attempt-failure push | same message, `NotificationSource::Connection` instead of `BugReport` |
| `app.rs::handle_critical_disconnect` | Modal entry branch | new push, severity / text per variant table above |
| `app.rs::update()` auto-reconnect tick | just before `self.reconnect()` | `self.push_connection_event(NotifySeverity::Info, format!("Reconnect attempt {}/{}…", attempt, MAX_RECONNECT_ATTEMPTS));` |
| `app.rs::reconnect_manual()` | top of method | `self.push_connection_event(NotifySeverity::Info, "Reconnecting (manual)…".to_string());` |
| `app.rs::process_events` `Disconnected(channel)` arm | non-critical channels | `self.push_connection_event(NotifySeverity::Info, format!("{} channel disconnected", channel.name()));` |
| `app.rs::process_events` `Disconnected(channel)` arm | critical channels — channel-name annotated | Existing modal `disconnect_reason` becomes the notification message via `handle_critical_disconnect`'s `Warn` push (one notification, not two — the disconnect message itself is the connection event) |
| `app.rs::process_events` `Error { channel, message }` arm | new push | `self.push_connection_event(NotifySeverity::Error, format!("{} channel error: {}", channel.name(), message));` |
| `app.rs::process_events` `AgentConnected(bool)` arm | both branches | `self.push_connection_event(NotifySeverity::Info, if connected { "Guest agent connected" } else { "Guest agent disconnected" }.to_string());` |

### Reclassification of the Phase 02 pushes

`handle_critical_disconnect` currently pushes attempt-failure
notifications via `NotificationSource::BugReport`. Change to
`NotificationSource::Connection`. The message text and
severity are unchanged.

The `NotificationSource` enum is `Hash`-equal-on-variant, so
dedup behaviour is preserved: a reclassified
attempt-failure notification still folds with itself (same
source-variant + same message), but no longer folds with
unrelated BugReport-source pushes.

### Misattribution cleanup

While in the area, fix the cross-file references that
mistakenly named Phase 09 as the snapshot-on-notification
phase. The notification-snapshot UX is Phase 10 (F2 —
"Notification → bug-report button"); Phase 09 is F1 (this
phase). Affected files:

- `docs/plans/PLAN-session-001-feedback-phase-06-channel-rebalance.md`
  (multiple references)
- `docs/plans/PLAN-session-001-feedback-phase-07-traffic-arc.md`
  (multiple references; both prose and doc comments)
- `docs/plans/PLAN-session-001-feedback-phase-08-ring-segmentation.md`
  (a couple of references)
- `ryll/src/bugreport.rs` (doc comments on `pcap_frame` and
  `additional_segments`; the cheap-clone test comment)

Replace "Phase 09" with "Phase 10" in each connection-snapshot
context. Leave Phase 09 references that genuinely point at
F1 work (e.g. the Phase 01 plan's note "channel lost —
reconnect to restore" deferral to F1) untouched.

### Testing

The notification store already has unit tests
(`notifications.rs::tests`). Phase 09's work is to wire up
new producers, which is straightforward integration glue.
Two new tests in `app.rs::tests` cover the small amount of
new pure logic:

- `connection_event_message_format_attempt_fire` — pure
  string-format check that the new "Reconnect attempt N/3"
  message matches the per-attempt template. Catches typos.
- `connection_event_message_format_modal_variants` — pure
  check that each `ModalVariant` produces the
  documented modal-entry notification text. Catches drift
  between modal copy and notification copy.

End-to-end wiring (notifications actually push when events
fire) is integration territory and the GUI-event loop is
not unit-testable without disproportionate scaffolding —
manual verification owns that surface.

### Manual verification

Bundles with the running Phase 02–08 manual checklist:

1. Start a session; check the bell — one "Connected to
   host:port" Info entry should be visible.
2. Stop the SPICE server. Watch the bell:
   - One "Connection lost — reconnecting…" Warn.
   - Three "Reconnect attempt N/3…" Info entries.
   - Three "Reconnect attempt N failed: …" Warn entries.
   - One "Auto-reconnect failed after 3 attempts" Error.
3. Click Reconnect on the modal.
   - One "Reconnecting (manual)…" Info.
   - On success, one "Connected to host:port" Info
     (folded with step 1 if within 30 s).
4. Disable / enable the guest's spice-vdagent.
   - One "Guest agent connected" / "Guest agent disconnected"
     Info per transition.
5. Cause a non-critical channel disconnect (e.g. detach a
   usb-redirected device).
   - One "usbredir channel disconnected" Info.
6. Open the notifications panel, confirm the source labels
   render as "Connection" for every step-2/3/4/5 entry.

## Tasks

- [x] Add `NotificationSource::Connection` variant in
      `shakenfist-spice-renderer/src/notification.rs` with
      matching `label()` arm.
- [x] Add `RyllApp::push_connection_event(&self, severity,
      message)` wrapper in `ryll/src/app.rs` next to
      `push_notification`.
- [x] At each producer call site listed in the table
      under "Approach", insert the documented
      `push_connection_event` call.
- [x] Reclassify the two existing reconnect-attempt-failure
      pushes in `handle_critical_disconnect` from
      `NotificationSource::BugReport` to
      `NotificationSource::Connection`. Message text and
      severity unchanged.
- [x] In `reconnect_manual()`, push "Reconnecting (manual)…"
      Info at the top of the method (before the call to
      `reconnect()`).
- [x] In the auto-reconnect tick poll inside `update()`,
      push "Reconnect attempt N/3…" Info just before the
      `self.reconnect()` call.
- [x] In `process_events` for the `SessionInitialized` arm,
      push "Connected to <host>:<port>" Info after
      `connected = true`.
- [x] In `process_events` for the `AgentConnected(bool)`
      arm, push the appropriate Info notification.
- [x] In `process_events` for the `ChannelEvent::Error` arm,
      push "<channel> channel error: <message>" Error
      *in addition to* the existing
      `handle_critical_disconnect` dispatch.
- [x] In `process_events` for the `Disconnected(channel)`
      arm, push "<channel> channel disconnected" Info for
      non-critical channels.
- [x] In `handle_critical_disconnect`'s Modal-entry branch,
      push the documented per-variant Error notification.
- [x] Add two new unit tests in `app.rs::tests`:
  - `connection_event_message_format_attempt_fire`
  - `connection_event_message_format_modal_variants`
- [x] Misattribution cleanup: replace "Phase 09" with
      "Phase 10" in the connection-snapshot contexts in:
  - `docs/plans/PLAN-session-001-feedback-phase-06-channel-rebalance.md`
  - `docs/plans/PLAN-session-001-feedback-phase-07-traffic-arc.md`
  - `docs/plans/PLAN-session-001-feedback-phase-08-ring-segmentation.md`
  - `ryll/src/bugreport.rs` (doc comments and the
    cheap-clone test message)
- [x] Update `PLAN-session-001-feedback.md` F1 row in the
      "Confirmed feature requests" table with the
      resolution pointer.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 09 → Done.
- [ ] Manual integration check (deferred operator action,
      bundles with the running Phase 02–08 manual
      checklist): walk through the 6-step manual sequence
      above against a real SPICE session.

## Out of scope

- **Filtering / grouping in the notifications panel.** A
  "Show: All / Connection / Gap / BugReport / Spice"
  dropdown would be useful but is independent of F1's
  "surface the events" deliverable. Revisit once the
  unread count gets noisy enough to justify it.
- **Persisting connection-event history across ryll
  restarts.** The notification store is per-session;
  cross-process persistence belongs to the same future
  settings-file phase that was deferred from K3 (Phase 03).
- **Distinguishing initial connect from reconnect in the
  message text.** "Connected to host:port" works for both
  — the bell history's chronological order makes the
  distinction obvious from context. Adding "(reconnect)" /
  "(initial)" suffixes would invite text inconsistency for
  no real user benefit.
- **The notification-snapshot UX itself** ("on notification
  fire, snapshot the ring buffer to a bounded
  side-buffer"). That's **Phase 10** (F2) and is what
  several earlier phase plans misattributed to Phase 09.
- **Surfacing snapshot-save / region-select / paste events
  as `Connection`.** Those already have their own sources
  (`BugReport`, `Internal`). The reclassification rule is
  narrow: only reconnect / disconnect / error /
  agent-state goes to `Connection`.
- **Programmatic API to consume Connection events.** No
  external consumer is in scope; the bell + side panel are
  the only readers.

## Open questions

1. **Should the "Reconnect attempt N/3…" Info entry
   include the backoff window (e.g. "Reconnect attempt
   2/3 in 4 s…")?** No — the entry fires *at the moment
   of* the attempt, so the backoff is already over. Adding
   it would invite the user to read a stale "in 4 s"
   message. The status-bar widget from Phase 02 already
   shows the per-attempt status during the backoff window.

2. **Should the `Disconnected(channel)` notification for
   the Webdav channel use Info or Warn?** WebDAV
   disconnects don't affect session usability except for
   the shared-folder feature itself. Info is right; the
   user's clipboard sync, audio, and display all keep
   working.

3. **Should the existing manual F8 "Bug report saved to …"
   notification also become `Connection`-sourced when the
   report is a disconnect snapshot?** No — bug-report
   *files* are produced by the bug-report writer, which is
   distinct from the connection event. The connection
   transition itself will be in the same notification list
   either way; conflating sources would obscure the
   producer-pattern boundary.

## Companion docs

- **`AGENTS.md` §21** ("Notifications go through the
  unified store, not direct UI calls") gains a one-line
  mention of `NotificationSource::Connection` as the
  newest producer-source variant. Doesn't reshape the
  guidance, just keeps the inventory current.
- **`ARCHITECTURE.md`** has no notifications-specific
  section to update; if a future phase adds one, Phase 09's
  Connection variant should land there.
