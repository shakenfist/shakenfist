# Phase 10: Notification → bug-report button (F2)

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/notifications.rs:56-100` (the `NotificationStore::push`
returns the entry id — that id is the natural key for a
snapshot-by-notification map), `ryll/src/bugreport.rs:38-77`
(`TrafficEntry` carries `pcap_frame: Arc<[u8]>` + `additional_segments:
Vec<Arc<[u8]>>` since Phase 07 / Phase 08 — clones are O(N atomic
refcount bumps)), `ryll/src/bugreport.rs:185-465` (the
`TrafficBuffers` type and its per-channel mutexes — needs a
`snapshot()` cheap-deep-copy helper), `ryll/src/bugreport.rs:1071-1115`
(`BugReport::new` / `assemble` constructor signatures, the
template for the new notification-report helper), and
`ryll/src/bugreport.rs:1449-1530` (`BugReport::write_disconnect`,
the closest existing zip-writer to mirror). The notifications
panel UI lives in `ryll/src/app.rs` around the
`show_notifications_panel` block — search for the heading
"Notifications" and you'll find the per-entry render. The
master plan's Open Question 1 resolution at
`docs/plans/PLAN-session-001-feedback.md:75-120` pre-decided the
UX shape ("always-fileable with `at_fire` / `post_event_only`
marker") — implement what it specifies rather than re-deriving.

This phase fixes feature request **F2** — "Turn this
notification into a bug report" button — identified from
session-001 source N2. The master plan classifies F2 as Low
severity ("quality-of-life") and notes the open-question
resolution that drives this plan's design. Phase 10 closes
out the master plan: every K-bug is resolved and both feature
requests are delivered after this commit lands.

## Situation

### What we already established

**The cheap-clone foundation is in place.** Phase 07
(`Arc<[u8]>` for `pcap_frame`) and Phase 08
(`additional_segments: Vec<Arc<[u8]>>`) make `TrafficEntry`
clones cost N atomic refcount bumps regardless of payload
size. A whole-`TrafficBuffers` snapshot is therefore
`(per-channel locks) × (Vec<TrafficEntry>::clone)` —
microseconds even on busy channels.

**Phase 06 sized the rings.** Display ring holds 32 MB (5 s
peak / 16 s typical of session-001 display traffic); other
channels comfortably cover any practical pre-fire window. A
60 s post-fire TTL on the snapshot is well under the
display's effective retention, so a snapshot captured at
notification fire holds the full pre-fire window for that
channel.

**Phase 09 reclassified connection-event notifications.**
Every connection transition now pushes a notification with
`NotificationSource::Connection`. F2's "file as bug report"
button is meant to apply to *every* notification, but the
Connection-source ones are the most common motivating case:
"the session disconnected and I want to file a report about
the moment it happened".

**The bug-report writer has a stable shape.** `BugReport::assemble`
takes `&TrafficBuffers` (plus everything else), assembles
metadata + session + channel-state + pcap + notifications +
runtime-metrics. `write_disconnect` is the closest existing
helper — it builds the assembly from a `DisconnectCause`
record and writes a zip with a sanitised filename.

### What we don't yet know

Nothing material. The master plan's Open Question 1
resolution pre-decided the UX:

- Snapshot captured at fire, retained for 60 s, max 5 active.
- Button always present on every notification entry.
- `at_fire` marker when a live snapshot exists; `post_event_only`
  otherwise.
- Visual indication of which state the button is in
  (dimmed / hover tooltip when post-event only).

The plan below is the straightforward implementation.

## Mission and problem statement

Give the user a one-click path from any notification entry
to a bug-report zip. When the snapshot system has a live
capture for that notification, the resulting zip contains
pcap traffic and channel state *from the moment the
notification fired*, so a maintainer reading the zip cold
can see the run-up to the event. When the snapshot has
expired (or fell off the bounded stack), the button still
produces a report — just one that contains only post-event
context, and the report's metadata says so.

The phase succeeds when:

- Every notification row in the side panel has a "File bug
  report" button.
- The button's visual state reflects whether a live
  snapshot exists for that notification (live = solid,
  expired = dimmed + tooltip).
- Clicking always produces a zip — never a no-op.
- The zip's metadata records `notification_snapshot:
  "at_fire"` when a live snapshot was used, `"post_event_only"`
  otherwise.
- The snapshot store self-bounds at 5 entries with a 60 s
  TTL; older or expired snapshots are pruned without explicit
  user action.
- No regression in any existing notification producer or
  bug-report writer.

## Approach

### `TrafficBuffers::snapshot()` cheap deep-copy

Add a `snapshot()` method on `TrafficBuffers` that returns a
fresh `TrafficBuffers` instance carrying cloned per-channel
ring contents. The implementation derives `Clone` on
`TrafficRingBuffer` and locks each channel briefly:

```rust
impl TrafficBuffers {
    /// Cheap deep-copy of the live ring state (Phase 10 / F2).
    /// Per-channel locks are held briefly to clone each ring;
    /// the TrafficEntry clones are O(N atomic refcount bumps)
    /// thanks to Phase 07's Arc<[u8]> for pcap_frame and
    /// Phase 08's Arc<[u8]> for each additional_segment.
    pub fn snapshot(&self) -> TrafficBuffers {
        TrafficBuffers {
            main:     Mutex::new(self.main.lock().unwrap().clone()),
            display:  Mutex::new(self.display.lock().unwrap().clone()),
            inputs:   Mutex::new(self.inputs.lock().unwrap().clone()),
            cursor:   Mutex::new(self.cursor.lock().unwrap().clone()),
            usbredir: Mutex::new(self.usbredir.lock().unwrap().clone()),
            playback: Mutex::new(self.playback.lock().unwrap().clone()),
            start:    self.start,
        }
    }
}

#[derive(Clone)]
pub struct TrafficRingBuffer { /* unchanged fields */ }
```

The snapshot is a standalone `TrafficBuffers` value, so it
plugs into `BugReport::assemble`'s existing
`traffic: &TrafficBuffers` parameter with no further
plumbing.

### `NotificationSnapshotStore` on `RyllApp`

A small bounded store keyed by `NotificationEntry::id`:

```rust
struct NotificationSnapshotEntry {
    captured_at: Instant,
    traffic: TrafficBuffers,
}

const NOTIFICATION_SNAPSHOT_TTL: Duration = Duration::from_secs(60);
const NOTIFICATION_SNAPSHOT_CAP: usize = 5;

struct NotificationSnapshotStore {
    /// id → snapshot. Insertion order tracked separately so
    /// "evict oldest when over cap" is O(1).
    by_id: HashMap<u64, NotificationSnapshotEntry>,
    /// Notification ids in insertion order, oldest first.
    insertion_order: VecDeque<u64>,
}
```

Operations:

- **`capture(id, traffic_snapshot)`**: insert a fresh
  snapshot. If `id` already present (re-fire of a folded
  notification within the dedup window), replace the entry
  and refresh its `captured_at`. Otherwise append to
  `insertion_order`; if length > 5, pop the oldest and drop
  its entry from `by_id`. Prune expired entries
  opportunistically (cheap O(N) walk; at most 5 entries).
- **`lookup(id) -> Option<&TrafficBuffers>`**: prune expired
  first, then return `Some(&entry.traffic)` if present.
  Lookup is the GUI button's check for "is a live snapshot
  available?".
- **`prune()`**: drop any entries older than the TTL. Called
  from the GUI tick at most once per second so the side
  panel reflects expiration in real time.

Pruning order matters: when over capacity, evict by
`insertion_order` (oldest); when over TTL, drop by age.
Both rules can fire at the same `capture` call; the
implementation handles them in that order.

### Snapshot capture timing

Captured *at the producer side* — at every
`RyllApp::push_notification` call. The wrapper after the
existing `push()`:

```rust
fn push_notification(
    &self,
    severity: NotifySeverity,
    source: NotificationSource,
    message: impl Into<String>,
) {
    let entry = NotificationEntry::new(severity, source, message);
    if let Ok(mut guard) = self.notifications.lock() {
        let id = guard.push(entry);
        // Phase 10 (F2): capture a snapshot of the live ring
        // state keyed by this notification's id. If the push
        // folded into an existing entry, the same id is
        // returned and we refresh the snapshot — the user
        // sees the most recent fire's context, which matches
        // the (intentionally) replaced timestamp on the
        // folded notification.
        if let Ok(mut store) = self.notification_snapshots.lock() {
            store.capture(id, self.traffic.snapshot());
        }
    }
}
```

The "fold replaces snapshot" behaviour is correct: a folded
notification carries the timestamp of the most recent fire,
so the matching snapshot should also be from that moment.

`push_connection_event` (Phase 09) goes through
`push_notification`, so connection events get snapshots for
free.

### New `BugReportType::Notification` variant

```rust
pub enum BugReportType {
    // ... existing variants ...
    /// User clicked "File bug report" on a notification entry.
    /// `notification_id` is the entry's stable id;
    /// `snapshot_state` records whether the report includes
    /// a live snapshot from notification-fire time or only
    /// post-event ring contents.
    Notification {
        notification_id: u64,
        snapshot_state: NotificationSnapshotState,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum NotificationSnapshotState {
    /// The snapshot store had a live capture for this
    /// notification. The bug-report zip's pcap and
    /// channel-state JSON reflect ring contents from the
    /// moment the notification fired.
    AtFire,
    /// The snapshot expired (>60 s old) or fell off the
    /// 5-entry stack. The bug-report zip's pcap and
    /// channel-state JSON reflect post-event ring contents.
    PostEventOnly,
}
```

`BugReportType::channel_name()` falls back to `"main"` for
the new variant — the pcap covers all channels regardless,
and the channel-state.json picks "main" as a sensible
session-level default.

### New `BugReport::write_notification` helper

Mirrors `write_disconnect`:

```rust
#[allow(clippy::too_many_arguments)]
pub fn write_notification(
    dir: &std::path::Path,
    notification: &NotificationEntry,
    snapshot_state: NotificationSnapshotState,
    target_host: &str,
    target_port: u16,
    traffic: &TrafficBuffers,  // either the snapshot or self.traffic
    channel_snapshots: &ChannelSnapshots,
    app_snapshot: &Mutex<AppSnapshot>,
    notifications: &Mutex<NotificationStore>,
    runtime_metrics: RuntimeMetrics,
) -> anyhow::Result<std::path::PathBuf> {
    let description = format!(
        "notification: [{}] {}",
        match notification.severity {
            NotifySeverity::Info  => "info",
            NotifySeverity::Warn  => "warn",
            NotifySeverity::Error => "error",
        },
        notification.message,
    );
    let report = Self::assemble(
        BugReportType::Notification {
            notification_id: notification.id,
            snapshot_state,
        },
        description,
        // ... other args ...
    )?;
    // ... zip writing analogous to write_disconnect ...
    let filename = format!(
        "ryll-notification-{}-{}-{}.zip",
        notification.id,
        match snapshot_state {
            NotificationSnapshotState::AtFire => "atfire",
            NotificationSnapshotState::PostEventOnly => "postevent",
        },
        filename_timestamp(),
    );
    // ...
}
```

The metadata.json that `assemble` builds will include
`report_type` with the new variant — which serializes the
`notification_id` and `snapshot_state` into the JSON. No
new `ReportMetadata` field needed; the existing `report_type`
field carries the new information.

### Producer call site (the button)

In the notification-panel render loop (`app.rs` around the
"Notifications" heading block):

```rust
for entry in &snapshot {
    ui.horizontal(|ui| {
        // ... existing severity icon, source label, message ...

        // Phase 10 (F2): File-as-bug-report button.
        let snapshot_live = self
            .notification_snapshots
            .lock()
            .map(|s| s.has_live(entry.id))
            .unwrap_or(false);
        let (icon, tooltip) = if snapshot_live {
            ("🐛", "File bug report (at-fire snapshot available)")
        } else {
            ("○", "File bug report (post-event context only — snapshot expired)")
        };
        if ui
            .small_button(icon)
            .on_hover_text(tooltip)
            .clicked()
        {
            pending_bug_report_id = Some(entry.id);
        }
    });
}

// After the panel render, outside the borrow scope:
if let Some(id) = pending_bug_report_id {
    self.file_notification_bug_report(id);
}
```

`file_notification_bug_report` does the lookup, picks the
snapshot or falls back to `self.traffic`, and calls
`BugReport::write_notification`. Pushes a `BugReport`-source
notification on success ("Bug report saved to …") matching
the existing F8 flow.

The icon glyphs (🐛 / ○) are placeholders — the actual
egui-rendering can use built-in symbols or simple text;
keep the visual difference subtle but clear. Tooltip text is
the load-bearing affordance.

### Tick-time pruning

In the existing `update()` loop, once per second (cheap
check via `Instant::now() - last_prune > 1s`):

```rust
if let Ok(mut store) = self.notification_snapshots.lock() {
    store.prune();
}
```

Without this, an expired snapshot's button visual would
remain "live" until the next user click that fires a lookup.
The once-per-second prune is enough for the UI to stay
honest.

### Testing

Pure-state tests on `NotificationSnapshotStore`:

- `snapshot_store_evicts_oldest_when_over_cap` — push 6
  snapshots, verify the first is gone.
- `snapshot_store_drops_expired_entries_on_prune` — push 2
  snapshots, advance "now" past TTL via a constructor that
  takes the time-source as a parameter, prune, verify both
  are gone.
- `snapshot_store_replaces_on_same_id_fold` — push id=42
  twice, verify only one entry, captured_at refreshed.
- `snapshot_store_lookup_returns_none_after_ttl` — same as
  the prune test but via lookup (which prunes on the way).
- `notification_snapshot_state_metadata_serialises` —
  serialise a `BugReportType::Notification { … }` and
  confirm both fields land in the JSON.

End-to-end wiring of the snapshot from push-time to
button-click is integration territory and tested manually.

### Manual verification

Bundles with the running Phase 02–09 manual checklist:

1. Connect to a guest. Confirm "Connected to host:port"
   notification appears with a 🐛 (or chosen "live") button.
2. Click the button immediately. Verify a zip lands in the
   bug-report directory named `ryll-notification-<id>-atfire-<ts>.zip`.
   Open `metadata.json`, confirm `report_type` is
   `Notification { notification_id, snapshot_state: "AtFire" }`.
3. Idle for ≥ 60 s. Confirm the button on the same entry
   has dimmed to the post-event indicator.
4. Click the dimmed button. Verify the resulting zip's
   filename contains `postevent`, and `snapshot_state` in
   metadata is `"PostEventOnly"`.
5. Push 6 consecutive notifications (e.g. trigger 6 quick
   connect/disconnect cycles). Confirm only the most recent
   5 have live-snapshot buttons; the oldest has dimmed.

## Tasks

- [x] Derive `Clone` on `TrafficRingBuffer` in
      `ryll/src/bugreport.rs`. All field types already
      implement `Clone`; `VecDeque<TrafficEntry>::clone`
      hits the cheap Phase-07 / Phase-08 paths.
- [x] Add `TrafficBuffers::snapshot(&self) -> TrafficBuffers`
      that clones each per-channel `Mutex<TrafficRingBuffer>`
      under brief lock acquisition.
- [x] Add `NotificationSnapshotState { AtFire, PostEventOnly }`
      enum next to `BugReportType` (Debug, Clone, Copy,
      PartialEq, Eq, Serialize).
- [x] Add `BugReportType::Notification { notification_id: u64,
      snapshot_state: NotificationSnapshotState }` variant.
      Extend `channel_name()` impl: returns `"main"` for the
      new variant.
- [x] Extend `assemble`'s channel-state.json match to cover
      `BugReportType::Notification { .. }` — picks
      `channel_snapshots.main` (consistent with the
      `channel_name()` fallback).
- [x] Add `BugReport::write_notification` in `bugreport.rs`,
      mirroring `write_disconnect`: takes a
      `NotificationEntry`, a snapshot-state, traffic
      buffers (live or snapshot), channel snapshots, app
      snapshot, notifications, runtime metrics; writes a
      zip with filename
      `ryll-notification-<id>-<atfire|postevent>-<ts>.zip`.
      Description is `"notification: [<sev>] <message>"`.
- [x] Define `NotificationSnapshotStore` in `ryll/src/app.rs`
      (or a new module) with:
  - `capture(id, traffic)` — insert/refresh, evict oldest
    on overflow, prune expired.
  - `lookup(id) -> Option<&TrafficBuffers>` — prune then
    return.
  - `has_live(id) -> bool` — prune then test presence.
  - `prune()` — drop expired only.
  - Internal time-source pluggable for tests (defaults to
    `Instant::now()`).
- [x] Add `RyllApp::notification_snapshots: Mutex<NotificationSnapshotStore>`
      field; initialise in `RyllApp::new`.
- [x] Modify `RyllApp::push_notification` to call
      `notification_snapshots.lock().capture(id, traffic.snapshot())`
      after the push.
- [x] Add per-row "File bug report" button in the
      notifications side-panel render loop. State driven
      by `has_live(entry.id)`. Click sets a deferred
      `pending_bug_report_id` so the borrow ends before
      the file-and-write work runs.
- [x] Add `RyllApp::file_notification_bug_report(id)` that:
  - looks up the snapshot (if any),
  - resolves the notification entry by id (for description
    and severity),
  - resolves the output directory via the same chain as
    F8 (`manual_bug_report_dir()`),
  - calls `BugReport::write_notification` with the
    appropriate `snapshot_state` and traffic source,
  - pushes a `BugReport`-source info notification on
    success ("Bug report saved to …"), error notification
    on failure.
- [x] Add tick-time prune call in `RyllApp::update`, gated
      on a 1-second interval via
      `last_snapshot_prune: Instant` field.
- [x] Five new unit tests covering `NotificationSnapshotStore`
      behaviour (cap, TTL, fold-replace, lookup-after-TTL,
      metadata serialisation).
- [x] Update `PLAN-session-001-feedback.md` F2 row in the
      "Confirmed feature requests" table with the
      resolution pointer.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 10 → Done.
- [ ] Manual integration check (deferred operator action,
      final entry in the running Phase 02–09 manual
      checklist): walk through the 5-step manual sequence
      above against a real SPICE session.

## Out of scope

- **Per-source filtering on which notifications get the
  button.** The master plan's resolution says "always
  present, every notification". Adding source-aware
  filtering (e.g. only Connection-source notifications get
  a button) would be a UX-policy decision; deferred.
- **Snapshotting the display surface PNG at fire time.**
  The current ring-buffer snapshot covers pcap and
  channel-state but not the rendered surface. A
  surface-at-fire snapshot is useful for video-lag
  complaints but expensive to capture (RGBA copy of the
  whole display) and not in the master plan's resolution.
  Revisit if dogfooding turns up a need.
- **A "preview the snapshot" UI** showing which channels
  have how many entries in the captured snapshot. Useful
  for debugging the snapshot store itself; not user-facing
  value. Defer.
- **Time-bounded snapshot retention by severity.** All
  notifications get the same 60 s / 5-entry policy. A
  "Error severity retains for longer" rule is plausible
  but speculative; the master plan didn't specify it.
- **Persisting snapshots across ryll restarts.** Notifications
  themselves don't persist; their snapshots wouldn't either.
  Same future-settings-file phase that Phases 02 / 03 / 09
  pushed out applies here.
- **Multi-channel `channel-state.json` for notification
  reports.** A future expansion could embed every
  channel's snapshot in the zip (mirroring write_disconnect's
  per_channel diagnostics). Phase 10 picks `main` and
  documents the limitation in the report description.
- **Surface a "snapshot expired N seconds ago" toast.**
  The dimmed-button tooltip already communicates the state;
  a toast on every expiry would be noise.

## Open questions

1. **Should the button push a notification on success?** Yes
   — matches the existing F8 flow ("Bug report saved to
   <path>"). The created entry will itself have a button,
   which would let the user file a meta-report. That's
   fine — it'll show as post-event-only (no snapshot for
   itself), the click produces a self-referential zip
   that's just additional context.

2. **Should the button on the success-confirmation
   notification be suppressed?** No — see (1). Allowing it
   is harmless and a special case would invite
   inconsistency.

3. **Should snapshot capture be skipped when `--web` mode is
   active?** Web mode runs a SPICE-to-WebRTC bridge and
   doesn't currently surface ryll's notifications panel.
   The capture happens regardless (snapshot-on-push is the
   same code path), but the button is GUI-only so the
   snapshots never get consumed. Minor wasted work; the
   bounded 5-entry / 60 s TTL caps it. Not worth a special
   case.

4. **Should the button work for the existing pedantic-mode
   auto-generated reports?** Those don't push notifications
   for the report itself (only for the underlying gap),
   so the button on the gap notification would produce a
   per-gap report distinct from the pedantic auto-report.
   That's a different shape (manual vs. automatic) and
   useful — both reports may end up in the same directory
   with different filenames. Acceptable.

5. **Should the 60 s TTL be configurable?** No — the
   master plan pinned the value. If real-world data shows
   the window is too short, a dedicated phase can revisit
   alongside the dynamic-sizing question for the ring
   buffer itself.

## Companion docs

- **`ARCHITECTURE.md`** gains a short subsection under the
  existing "Auto-snapshot on channel disconnect" describing
  the notification-snapshot store: the at-fire / post-event
  contract, the bounded 5-entry / 60 s TTL policy, and the
  link to Phase 07's `Arc<[u8]>` cheap-clone foundation.
- **`AGENTS.md`** §22 (auto-reconnect pattern) gets no
  change; §21 (notifications) already has a current source
  inventory. The snapshot store is RyllApp-internal and
  doesn't need a new §-entry.
- **README** has no relevant section that needs updating —
  the button is a side-panel affordance that's documented
  by its tooltip.

## Wrap-up

Phase 10 closes out the session-001 feedback master plan.
After this commit lands:

- All five confirmed bugs (K1–K5) marked Resolved.
- Both confirmed feature requests (F1, F2) marked Resolved.
- The master plan's Execution table is all "Done".
- The K1-investigation side-quest backlog (Step 2d, Step
  22, Step 23) remains as captured deferrals — picked up
  if and when a future session brings fresh motivation.
- The running manual integration checklist (Phase 02–10)
  is the only outstanding item; bundles into one operator
  session against a real SPICE server.

A short master-plan wrap-up paragraph noting "all phases
complete" should land in `PLAN-session-001-feedback.md` as
part of this phase's commit, paired with the Phase 10
Execution row flip.
