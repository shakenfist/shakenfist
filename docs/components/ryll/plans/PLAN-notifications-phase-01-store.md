# Phase 1: Notification store and ring buffer

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

The master plan for this work is
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/). Read it first
for context on the producers, the consumer surface, the
severity model, and the open questions whose recommended
answers this phase implements.

## Goal

Build the pure-Rust foundation that every other phase
depends on:

* The `NotificationEntry` value type and the supporting
  `NotificationSeverity`, `NotificationSource`, and
  `SpiceVisibility` enums.
* A bounded `NotificationStore` ring buffer (cap 500,
  FIFO eviction), with the read/unread state model and
  the 30-second deduplication rule from master-plan
  Q5 baked into `push`.
* Full serde round-trip support so phase 5 can drop the
  store contents into `notifications.json` in bug-report
  zips without further design.
* Unit tests covering dedup window, eviction, severity
  reduction, mark-read state transitions, and
  serialisation.

At the end of this phase, the store compiles, has tests, and
is ready for phases 2/3 to plug producers in. **No producers
are wired in this phase, and no GUI surface is added.** The
existing *Gaps* badge and bug-report transient remain
untouched until phase 3; phase 4 adds the bell and side
panel.

## Design

### Module layout

A new module `ryll/src/notifications.rs` holds the store
and its types. Three upstream changes in
`shakenfist-spice-protocol` make the design serde-friendly
and share types with existing protocol code:

1. `SpiceVisibility` enum in
   `shakenfist-spice-protocol/src/constants.rs`. The SPICE
   wire format defines three values
   (`SPICE_NOTIFY_VISIBILITY_LOW=0`, `MEDIUM=1`, `HIGH=2`,
   per `/srv/src-reference/spice/spice-protocol/spice/protocol.h`).
   Phase 2 will swap `Notify::visibility` from raw `u32`
   to `Option<SpiceVisibility>`; Phase 1 needs the type
   on `NotificationEntry`.
2. `serde` derives on `ChannelType`, `NotifySeverity`,
   and `SpiceVisibility`. This requires adding `serde`
   (with `derive`) as a normal dependency of
   `shakenfist-spice-protocol`. The cost is negligible;
   the alternative (custom `serialize_with` shims in
   ryll) duplicates the channel-name table for no benefit.
   Phase 5 will reuse these derives for bug-report JSON.
3. Additional derives on the existing `NotifySeverity`
   enum: `Hash, PartialOrd, Ord` (alongside `Serialize,
   Deserialize`). The notification store reuses
   `NotifySeverity` directly rather than defining a
   parallel `NotificationSeverity` — see §"Reuse of
   NotifySeverity" below.

### Reuse of NotifySeverity

The master plan was written before noticing that
`shakenfist-spice-protocol` already defines
`NotifySeverity { Info=0, Warn=1, Error=2 }` (in
`constants.rs`) and that `ryll/src/channels/main_channel.rs`
already parses `SPICE_MSG_NOTIFY` and routes it by severity
into the tracing log. The master plan's "Ryll does not
parse SPICE_MSG_NOTIFY at all today" claim is therefore
wrong on the main channel and only correct on the other
six channels.

Consequence for Phase 1: do not define a parallel
`NotificationSeverity` enum. Reuse
`shakenfist_spice_protocol::NotifySeverity` directly as the
`severity` field type on `NotificationEntry`. This is the
"prefer the correct fix" approach from the user's
CLAUDE.md — single source of truth, no conversion at the
SPICE-source boundary, no risk of the two enums drifting.

### Types

```rust
// ryll/src/notifications.rs

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use serde::{Deserialize, Serialize};
use shakenfist_spice_protocol::{
    ChannelType, NotifySeverity, SpiceVisibility,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash,
         Serialize, Deserialize)]
pub enum NotificationSource {
    /// Protocol gap registered via `warn_once!`.
    Gap,
    /// Bug-report writer success/failure status.
    BugReport,
    /// SPICE_MSG_NOTIFY received on a channel. `what` is the
    /// SPICE notify-what enum value (kept verbatim for Phase 2
    /// dedup; the UI does not interpret it yet).
    Spice { channel: ChannelType, what: u32 },
    /// Internally generated notification (reserved for future
    /// use; e.g. ryll's own warnings).
    Internal,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NotificationEntry {
    /// Monotonic id assigned by the store at push time. Zero
    /// before assignment; producers must not rely on it before
    /// `push` returns.
    pub id: u64,
    /// Wall-clock time the producer observed the event. Used
    /// both for display ("3s ago") and as the "now" anchor for
    /// the dedup window — see `NotificationStore::push`.
    pub when: SystemTime,
    pub severity: NotifySeverity,
    pub source: NotificationSource,
    pub message: String,
    /// Number of times this notification has been seen,
    /// inclusive of the original. 1 for a fresh entry; >1 for
    /// entries that absorbed dedups (UI renders as `[3×]`).
    pub count: u32,
    /// SPICE visibility hint where applicable (Q4: low-vis
    /// notifications still record but skip the bell flash).
    pub visibility: Option<SpiceVisibility>,
    pub read: bool,
}

impl NotificationEntry {
    /// Build a fresh entry with `when = SystemTime::now()`,
    /// `count = 1`, `read = false`, `id = 0`. Producers
    /// construct via this and pass to `NotificationStore::push`,
    /// which stamps the id.
    pub fn new(
        severity: NotifySeverity,
        source: NotificationSource,
        message: impl Into<String>,
    ) -> Self;

    /// Builder-style setter for SPICE visibility.
    pub fn with_visibility(mut self, v: SpiceVisibility) -> Self;
}

/// Maximum entries before FIFO eviction (Q1: single shared cap).
pub const NOTIFICATION_STORE_CAP: usize = 500;

/// Window inside which a duplicate `(source, severity, message,
/// visibility)` tuple is folded into the most recent matching
/// entry rather than producing a new one (Q5).
pub const NOTIFICATION_DEDUP_WINDOW: Duration =
    Duration::from_secs(30);

pub struct NotificationStore {
    entries: VecDeque<NotificationEntry>,
    next_id: AtomicU64,
    cap: usize,
    dedup_window: Duration,
}
```

### Store API

```rust
impl NotificationStore {
    /// Construct a store with default cap (500) and dedup
    /// window (30s).
    pub fn new() -> Self;

    /// Construct with custom cap / dedup window. Used by tests
    /// to drive eviction with a small cap or a zero window.
    pub fn with_config(cap: usize, dedup_window: Duration) -> Self;

    /// Insert an entry. If the most recent entry within
    /// `dedup_window` (anchored on the new entry's `when`) has
    /// the same `(source, severity, message, visibility)` tuple,
    /// fold the new entry into it (`count += 1`, refresh
    /// `when` to the new entry's `when`, mark `read = false`)
    /// and return that existing entry's id. Otherwise stamp a
    /// fresh id, push to the back, evict from the front if the
    /// cap is exceeded, and return the new id.
    pub fn push(&mut self, entry: NotificationEntry) -> u64;

    /// Mark a single entry read. No-op if id is unknown.
    pub fn mark_read(&mut self, id: u64);

    /// Mark every entry read.
    pub fn mark_all_read(&mut self);

    /// Drop every entry (the UI's *Clear all* action).
    pub fn clear(&mut self);

    /// Number of unread entries.
    pub fn unread_count(&self) -> usize;

    /// Total number of entries (read + unread).
    pub fn len(&self) -> usize;

    pub fn is_empty(&self) -> bool;

    /// Iterate unread entries, newest first.
    pub fn iter_unread(&self)
        -> impl Iterator<Item = &NotificationEntry>;

    /// Iterate every entry, newest first. Used by the side
    /// panel.
    pub fn iter_newest_first(&self)
        -> impl Iterator<Item = &NotificationEntry>;

    /// Highest severity among unread entries, or `None` when
    /// `unread_count() == 0`. Phase 4's bell uses this to colour
    /// the unread dot.
    pub fn highest_unread_severity(&self)
        -> Option<NotifySeverity>;
}

impl Default for NotificationStore {
    fn default() -> Self { Self::new() }
}

/// Convenience type alias used everywhere a producer or the
/// UI needs to share the store.
pub type SharedNotifications = Arc<Mutex<NotificationStore>>;
```

### Dedup algorithm

`push` walks the deque from the back (newest) to the front
(oldest). For each entry:

* If `entry.when` (existing) is more than `dedup_window`
  older than the new entry's `when`, stop scanning — every
  earlier entry is also outside the window.
* If the existing entry matches `(source, severity, message,
  visibility)`, fold the new entry into it: `count += 1`,
  set `when = new_entry.when`, `read = false`. Return the
  existing id.

If no fold candidate is found, assign a fresh id from
`next_id.fetch_add(1, Ordering::Relaxed)` (start at 1; 0 is
the unset sentinel), set the entry's id, `push_back`, and
evict from the front while `entries.len() > cap`. Return the
new id.

Edge cases the tests must cover:

* Dedup window of zero → no folding ever happens.
* `entry.when` in the past relative to existing entries
  (clock skew, manual construction): use absolute distance,
  not signed. Implementation should compute `existing.when
  .duration_since(new.when).or_else(|_| new.when
  .duration_since(existing.when))` and compare against
  `dedup_window`.
* SPICE's `visibility` is part of the dedup key, so two
  otherwise-identical notifications with different visibility
  do not fold.

### Sharing pattern

The store lives behind `Arc<Mutex<NotificationStore>>` so
producers (channel handlers in tokio tasks, the bug-report
writer, the gap observer) can `lock().push(...)` from any
thread. Egui reads on the main thread via the same lock at
~60 FPS. The store's operations are O(n) at worst (the dedup
scan walks at most one window's worth of entries), which is
fine: 500 entries × a 30-second window means the typical
scan is single-digit entries.

Phase 1 does not create the shared instance — that happens
in Phase 3 alongside the gap observer registration. Phase 1
ships only the type and its tests.

### Serde considerations

Every type derives `Serialize` and `Deserialize`. Phase 5's
bug-report inclusion will serialize the *entries* as a JSON
array, not the whole store wrapper (the cap and dedup window
are configuration, not state). Provide a serde-friendly
helper:

```rust
impl NotificationStore {
    /// Snapshot the entries for serialisation. Returns owned
    /// clones (the bug-report writer runs off the lock).
    pub fn snapshot(&self) -> Vec<NotificationEntry>;
}
```

The Phase 1 serde test round-trips a `Vec<NotificationEntry>`,
which is what Phase 5 will write.

`SystemTime` serializes via serde's built-in support
(`serde` feature on `std` types is included by default); no
extra crate is needed.

### Why these constants

* **Cap of 500**: master-plan Q1's recommendation. Headroom
  far above any realistic source rate. Bounded memory:
  500 × ~200 bytes per entry ≈ 100 KiB.
* **Dedup window of 30 s**: master-plan Q5's recommendation.
  Long enough to fold the QEMU per-channel TLS-insecure
  bursts (which all arrive within milliseconds of session
  setup) into a single entry, short enough that genuinely
  recurring events are not silently merged.

## Steps

### Step 1: Protocol crate additions

In `shakenfist-spice-protocol`:

1. Add `serde` (with `derive`) as a non-optional dependency
   in `shakenfist-spice-protocol/Cargo.toml`.
2. In `shakenfist-spice-protocol/src/constants.rs`:
   * Derive `Serialize, Deserialize` on `ChannelType`.
   * Add `Hash, PartialOrd, Ord, Serialize, Deserialize`
     to the existing `NotifySeverity` derive list. The
     ordinal ordering Info < Warn < Error follows from
     declaration order, which matches the desired
     "highest severity wins" semantics.
   * Add `SpiceVisibility { Low = 0, Medium = 1, High = 2 }`
     deriving `Debug, Clone, Copy, PartialEq, Eq, Hash,
     Serialize, Deserialize`. Provide `from_u32(value: u32)
     -> Option<Self>` and `name(&self) -> &'static str`.
3. Re-export `SpiceVisibility` from
   `shakenfist-spice-protocol/src/lib.rs` next to the
   existing `ChannelType` re-export.
4. Add a small unit test in `constants.rs` covering
   `from_u32` round-trip for each variant plus `from_u32(99)
   == None`.

Phase 2 will populate `SpiceVisibility` from the wire
format; Phase 1 only needs the type.

### Step 2: Notification store module

In `ryll/src`:

1. Create `ryll/src/notifications.rs` with the types and
   API described in the Design section. Constants
   (`NOTIFICATION_STORE_CAP`, `NOTIFICATION_DEDUP_WINDOW`)
   are `pub const`.
2. Add `pub mod notifications;` to `ryll/src/main.rs`.
3. Implement `NotificationEntry::new`, `with_visibility`.
4. Implement every method on `NotificationStore` listed in
   the API table, including the dedup walk in `push`.
5. Provide the `SharedNotifications` type alias.
6. No other ryll files change in this phase. Producers and
   GUI integration land in phases 3 and 4.

### Step 3: Unit tests

Inside `notifications.rs`, a `#[cfg(test)] mod tests`
covering:

| Test | What it asserts |
|------|-----------------|
| `new_store_is_empty` | `len() == 0`, `unread_count() == 0`, `highest_unread_severity().is_none()`. |
| `push_assigns_monotonic_ids` | Three pushes produce ids 1, 2, 3. |
| `push_evicts_at_cap` | With `with_config(cap=3, ...)`, push 5 entries; `len() == 3`, oldest two are gone, ids 3/4/5 remain in newest-last order. |
| `dedup_within_window_folds` | Two identical entries with `when` 5s apart and a 30s window yield `len() == 1`, `count == 2`, the surviving entry has the second `when`, and `read == false` even after the first was marked read. |
| `dedup_outside_window_does_not_fold` | Two identical entries with `when` 31s apart and a 30s window yield `len() == 2`. |
| `dedup_zero_window_never_folds` | Two identical entries with a zero window yield `len() == 2`. |
| `dedup_distinct_visibility_does_not_fold` | Same source/severity/message but different `Some(visibility)` values yield two entries. |
| `dedup_distinct_source_does_not_fold` | Same severity/message/visibility but different source yield two entries. |
| `mark_read_clears_unread` | Push entry, `unread_count == 1`; `mark_read(id)`, `unread_count == 0`. |
| `mark_read_unknown_id_is_noop` | `mark_read(999)` against an empty store does not panic and does not change state. |
| `mark_all_read` | Push three, `mark_all_read()`, every entry has `read == true` and `unread_count == 0`. |
| `clear_drops_everything` | Push three, `clear()`, `len() == 0`, but `next_id` continues monotonically (a subsequent push gets id 4, not 1). |
| `iter_unread_skips_read` | Push three, mark middle read, `iter_unread().count() == 2`, both yielded entries have `read == false`. |
| `iter_newest_first_orders_correctly` | Push three with distinct ids; iterator yields ids 3, 2, 1. |
| `highest_unread_severity_picks_max` | Push `NotifySeverity::Info`, `Warn`, `Error`; `highest_unread_severity() == Some(NotifySeverity::Error)`. Mark the Error entry read; result becomes `Some(NotifySeverity::Warn)`. |
| `serde_round_trip_entries` | Build a `Vec<NotificationEntry>` covering each `NotificationSource` variant (Gap, BugReport, Spice with each `ChannelType`, Internal) and each `NotificationSeverity`, plus one entry with `Some(visibility)` and one with `None`. `serde_json::to_string` then `from_str` yields a `Vec` that compares equal to the original. |

Use deterministic `SystemTime::UNIX_EPOCH +
Duration::from_secs(...)` values for `when` so dedup-window
tests are fully deterministic (no `SystemTime::now`).

### Step 4: Build and pre-commit

1. `cargo build --workspace`.
2. `cargo test -p ryll notifications --` (or
   `cargo test --workspace`).
3. `pre-commit run --all-files`.
4. Confirm clippy is clean with `-D warnings` (the
   workspace's existing setting).

No documentation changes in this phase — `ARCHITECTURE.md`,
`AGENTS.md`, and `README.md` are updated in Phase 5 once the
full pipeline is in place.

## Sub-agent execution table

Per master-plan §"Step-level guidance":

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 (protocol prep) | medium | sonnet | none | Add `serde = { version = "1", features = ["derive"] }` to `shakenfist-spice-protocol/Cargo.toml`. In `src/constants.rs`, derive `Serialize, Deserialize` on `ChannelType`; add `Hash, PartialOrd, Ord, Serialize, Deserialize` to the existing `NotifySeverity`'s derive list (declaration order Info < Warn < Error gives the right ordering); add a new `SpiceVisibility { Low=0, Medium=1, High=2 }` enum (derives `Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize`) with `from_u32(u32) -> Option<Self>` and `name(&self) -> &'static str`. Re-export `SpiceVisibility` from `src/lib.rs`. Add a unit test for `SpiceVisibility::from_u32` covering each variant and one invalid value. Run `cargo test -p shakenfist-spice-protocol` and `pre-commit run --all-files`. |
| 2 (store + tests) | high | opus | none | Create `ryll/src/notifications.rs` with the types and API exactly as specified in PLAN-notifications-phase-01-store.md §Design. Reuse `shakenfist_spice_protocol::NotifySeverity` directly as the severity type — do **not** define a parallel `NotificationSeverity` enum. Implement the dedup algorithm exactly as specified in §"Dedup algorithm" — walk newest-to-oldest, stop when the existing entry is outside the window, use absolute distance to handle out-of-order `when` values. The `next_id` counter starts at 1 and persists across `clear()`. Add `pub mod notifications;` to `ryll/src/main.rs`. Write the unit tests listed in the §"Unit tests" table; use deterministic `SystemTime::UNIX_EPOCH + Duration::from_secs(...)` values for every `when`. Run `cargo test -p ryll` and `pre-commit run --all-files`. Do not modify any other ryll files. |

Step 1 lands first so step 2 can `use shakenfist_spice_protocol::{NotifySeverity, SpiceVisibility}`. Both steps land in the same phase commit.

## Administration and logistics

### Success criteria

* `shakenfist-spice-protocol` exports `SpiceVisibility` and
  derives `Serialize, Deserialize` on it and on `ChannelType`.
* `ryll/src/notifications.rs` exists and exposes
  `NotificationEntry`, `NotificationSeverity`,
  `NotificationSource`, `NotificationStore`,
  `SharedNotifications`, `NOTIFICATION_STORE_CAP`,
  `NOTIFICATION_DEDUP_WINDOW`.
* `NotificationStore::push` honours the 30-second dedup
  rule (folding `(source, severity, message, visibility)`
  matches into the most recent in-window entry, with
  `count` increment and `read = false`).
* The store evicts FIFO at the cap.
* Every test in the §"Unit tests" table passes.
* `cargo test --workspace` and `pre-commit run
  --all-files` both succeed.
* No producer or GUI plumbing has been introduced; the
  *Gaps* badge and bug-report transient at `app.rs:1582`
  and `app.rs:1610` are unchanged.

### Risks

* **Serde dep on the protocol crate.** Adds a small build
  cost to `shakenfist-spice-protocol` consumers. Acceptable:
  serde is already a transitive dep through ryll, and Phase
  5 will need it on these types regardless.
* **Lock contention** between the egui main loop (reads at
  60 FPS) and producers. Operations are O(n) over at most
  one dedup window; in practice a handful of entries.
  `Arc<Mutex<>>` is the same pattern `TrafficBuffers` uses
  successfully today. Revisit only if a future profile shows
  contention.
* **Clock skew.** SPICE notifications carry a server
  timestamp but Phase 1 uses local `SystemTime` for `when`.
  Phase 2 will decide whether to surface the server
  timestamp separately; Phase 1 stays with local time so
  dedup behaves predictably regardless of server clock
  drift.
* **`next_id` does not reset on `clear()`.** Tests pin this
  behaviour. The reasoning: any UI element holding an id
  from before a clear (e.g. a hover that survived the
  *Clear all* button click) should observe a clean miss
  on `mark_read`, not accidentally hit a recycled entry.

### Future work (deferred from this phase)

* Persistence across sessions (master plan §"Future work").
* Per-source / per-severity filters on the panel.
* Severity threshold for bell flash (master plan §"Future
  work" item 5).

### Documentation index maintenance

This phase plan is referenced from the master plan's
*Execution* table. Once Phase 1 lands, update that table's
*Status* column from "Not started" to "Complete" with a link
to the merge commit.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
