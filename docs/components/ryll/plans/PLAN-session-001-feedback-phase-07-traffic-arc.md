# Phase 07: `Arc<[u8]>` refactor for `TrafficEntry::pcap_frame`

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/bugreport.rs:38-59` (the `TrafficEntry` struct where
`pcap_frame: Vec<u8>` lives), `:81-173` (the
`TrafficRingBuffer` that owns entries and accounts bytes via
`pcap_frame.len()`), `:355-388` (`build_frame` which currently
returns `Vec<u8>`, the only producer of the field), and the
two stub call sites at `:1576` and `:1592` inside the tests.
Cross-reference the master plan at
`docs/plans/PLAN-session-001-feedback.md:104-110` (Phase 06's
Open-Question-1 resolution explicitly notes "Cheap to clone
thanks to Phase 07 (`Arc<[u8]>` for `pcap_frame` makes
per-entry clone an atomic refcount bump)") and `:211-215`
(the Phase 08 hard-dependency note: "Phase 08 benefits from
Phase 07's `Arc<[u8]>` model — segmenting into multiple
entries is cleaner when each segment is a cheap
`Arc`-shared chunk").

This phase is purely structural — no user-visible behaviour
change, no new tests of business logic, no API surface added.
It exists to enable Phase 10's notification-snapshot UX
(cheap ring-buffer clones) and to make Phase 08's
segmentation refactor (entry-per-segment) sit cleanly on a
type that's already shared-by-default.

## Situation

### What we already established

**`TrafficEntry::pcap_frame` is the only large field in the
struct.** Every other field is either `Copy` (timestamp,
sizes, types, direction) or a `&'static str` pointer
(channel name, message name). At session-001's observed
display rates, each entry carries roughly 1–32 KB in
`pcap_frame`, depending on message size.

**The only producer is `TrafficBuffers::build_frame`**, which
returns `Vec<u8>` (`bugreport.rs:355-388`) from the
`capture::build_tcp_frame` helper. Both `record_received`
(`:298-318`) and `record_sent` (`:326-349`) call it and move
the result into a freshly-constructed `TrafficEntry`.

**The only consumers are local to this file.** The ring
buffer accounts bytes via `entry.pcap_frame.len()` and
writes the pcap export via `&entry.pcap_frame` deref-coerced
to `&[u8]`. Tests construct `TrafficEntry { pcap_frame:
vec![...] }` directly at two stub call sites.

**Cloning a `TrafficEntry` today is O(payload bytes).** The
derive `#[derive(Clone)]` on `TrafficEntry` delegates each
field's clone, and `Vec<u8>::clone` is a heap allocation
plus byte copy. The Phase 10 notification-snapshot flow ("on
notification fire, snapshot the ring buffer to a bounded
side-buffer") will need to clone N entries; with Vec<u8>
that's O(total ring bytes per snapshot). With Arc<[u8]> it
collapses to N atomic refcount bumps.

### What we don't yet know

Nothing material. The change is structural, well-bounded, and
each call site adjustment is mechanical. The only design
question is whether to also change `build_frame`'s return
type from `Vec<u8>` to `Arc<[u8]>` — resolved under "Approach"
below.

## Mission and problem statement

Switch `TrafficEntry::pcap_frame` from `Vec<u8>` to
`Arc<[u8]>` so that:

- `TrafficEntry::clone()` becomes a refcount-bump on the
  payload rather than a heap copy.
- The ring-buffer byte-accounting (`entry.pcap_frame.len()`)
  still works unchanged (Arc<[u8]> derefs to [u8]).
- The pcap export path still works unchanged after a
  one-line deref tweak.
- The `TrafficViewEntry` (`bugreport.rs:62-79`) is unaffected
  — it never carried the pcap bytes and is already
  cheap-to-clone.

No new behaviour. No new tests of business logic. Phase 08
and Phase 10 will exercise the cheap-clone property; Phase
07's responsibility is just to expose it.

The phase succeeds when:

- `git grep "pcap_frame: Vec<u8>"` finds nothing.
- `TrafficEntry::clone` is provably refcount-only on the
  payload (pinned via `Arc::ptr_eq` in a small unit test).
- Every existing test passes without modification (other
  than the two stub literal call sites that construct
  `pcap_frame: vec![0u8; 20]`).
- `make build`, `make lint`, `make test` all clean.

## Approach

### Type choice: `Arc<[u8]>`, not `Arc<Vec<u8>>`

`Arc<[u8]>` is the canonical Rust idiom for a shared
immutable byte buffer:

- Single heap allocation (Arc header + payload inline).
  `Arc<Vec<u8>>` would be two — the Arc heap node and the
  Vec's separate heap allocation.
- `From<Vec<u8>> for Arc<[u8]>` is in std; the conversion
  reuses the Vec's heap allocation by going via
  `Box<[u8]>`. So building from `build_frame`'s `Vec<u8>`
  return value is a single move, not a copy.
- Deref to `[u8]` is automatic, so existing `.len()` and
  slice-borrow call sites keep working.

### Leave `build_frame` returning `Vec<u8>`

The conversion to `Arc<[u8]>` is a one-line `.into()` at the
two call sites (`record_received` and `record_sent`).
Pushing it down into `build_frame` would force any future
caller that wants a mutable buffer (e.g. for in-place editing
before sealing into an entry) to round-trip through
`Vec::from(arc.as_ref())`. Convert at the call site —
keeps `build_frame` as a plain builder and `TrafficEntry`'s
construction site as the place that decides "this is
done, freeze it".

### Concrete diff sketch

**Type change** (`bugreport.rs:38-59`):

```rust
use std::sync::Arc;  // new import

#[derive(Debug, Clone)]
pub struct TrafficEntry {
    // ... unchanged fields ...

    /// Full pcap frame bytes (Ethernet + IP + TCP + SPICE
    /// payload). `Arc<[u8]>` so Phase 10's
    /// snapshot-on-notification path can clone the ring
    /// buffer's entries in O(N atomic increments) rather
    /// than O(total bytes).
    pub pcap_frame: Arc<[u8]>,
}
```

**Producer call sites** (`bugreport.rs:306, 338`):

```rust
let pcap_frame: Arc<[u8]> = self
    .build_frame(channel, false, raw_message, &mut guard)
    .into();
// ... entry literal uses the new binding unchanged ...
```

**Pcap-export read site** (`bugreport.rs:152-153`): the
existing `&entry.pcap_frame` produces `&Arc<[u8]>`, which
won't deref-coerce to `&[u8]` directly. Two adjustments are
equivalent; use the slice-borrow form for explicitness:

```rust
let packet = PcapPacket::new(
    entry.timestamp,
    entry.pcap_frame.len() as u32,
    &entry.pcap_frame[..],   // was: &entry.pcap_frame
);
```

**Test stub literals** (`bugreport.rs:1576, 1592`):

```rust
pcap_frame: Arc::from(vec![0u8; 20]),
// or, equivalently:
pcap_frame: vec![0u8; 20].into(),
```

The `.into()` form is shorter but slightly less obvious to a
reader; pick `Arc::from` for the explicitness.

### Pin the cheap-clone property in a test

Add one small unit test in `bugreport::tests`:

```rust
#[test]
fn traffic_entry_clone_shares_pcap_frame_via_arc() {
    // Phase 07 (no user-visible behaviour change) guard.
    // The cheap-clone property is the whole point of the
    // refactor — without this test a future change that
    // turns pcap_frame back into Vec<u8> would compile and
    // silently regress the Phase 10 snapshot path's
    // cost model.
    let entry = TrafficEntry {
        timestamp: Duration::from_millis(0),
        channel: "main",
        direction: TrafficDirection::Received,
        message_type: 1,
        message_name: "test",
        wire_size: 20,
        payload_size: 14,
        pcap_frame: Arc::from(vec![0u8; 20]),
    };
    let cloned = entry.clone();
    assert!(
        Arc::ptr_eq(&entry.pcap_frame, &cloned.pcap_frame),
        "Clone must share the payload allocation; if this \
         fires, pcap_frame's type was changed back to Vec<u8> \
         (or another deep-copy type) and Phase 10's snapshot \
         cost model is broken."
    );
}
```

`Arc::ptr_eq` returns true iff both Arcs point at the same
allocation — the exact "no deep copy happened" invariant we
want to pin. Cheap, single-file, no new fixtures.

### No new public-API surface

`TrafficEntry::pcap_frame` is `pub` today and stays `pub`.
The type change is technically a breaking change for any
external consumer, but ryll's `bugreport` module isn't
exported as a library, and grep confirms no consumer outside
the crate. The doc comment update is the only API-surface
adjustment.

## Tasks

- [x] `use std::sync::Arc;` was already imported at
      `bugreport.rs:11` — no addition needed.
- [x] Change `TrafficEntry::pcap_frame` from `Vec<u8>` to
      `Arc<[u8]>`. Doc comment updated with the
      snapshot-cost rationale and a pointer to the
      cheap-clone test.
- [x] At the two producer sites (`record_received`,
      `record_sent`), `build_frame`'s `Vec<u8>` return is
      converted with `let pcap_frame: Arc<[u8]> = ....into();`
      Same form at both sites.
- [x] At the pcap-export read site
      (`TrafficRingBuffer::write_pcap_to`), changed
      `&entry.pcap_frame` to `&entry.pcap_frame[..]` so the
      slice borrow is explicit (auto-deref would also work
      here, but the explicit form documents the intent
      after the type change).
- [x] Updated the two test stub literals at
      `bugreport.rs:1589, 1605` to construct
      `pcap_frame: Arc::from(vec![0u8; 20])`. Grep confirmed
      no other test sites construct `TrafficEntry`.
- [x] Added `traffic_entry_clone_shares_pcap_frame_via_arc`
      in `bugreport::tests`. Asserts `Arc::ptr_eq` on the
      original and clone — pins the cheap-clone invariant.
- [x] Updated `PLAN-session-001-feedback.md` Execution
      table row for Phase 07 → Done. No K-bug row to
      update (Phase 07 is an enabler).

## Out of scope

- **Actually exercising the cheap clone.** Phase 10 is the
  consumer; Phase 07's only job is to expose the property.
  Wiring `snapshot_at_fire` into the notification flow is
  the Phase 10 deliverable and uses this phase's foundation.
- **Changing `build_frame`'s return type.** Keeps the builder
  flexible for future mutable-buffer needs. The call-site
  conversion is one line.
- **`Arc<[u8]>` for other byte-bearing types in the
  bug-report tree.** `BugReport`'s `screenshot_region_png:
  Option<Vec<u8>>`, the channel-state JSON payloads, the
  capture pcaps written to disk — none of these are cloned
  hot-path; converting them would be churn without benefit.
- **Pool / arena allocation.** A future "ring buffer of
  ring-allocated TrafficEntries" might reuse Arc<[u8]>
  payload slots, but that's a separate master-plan item.
  Phase 07's job is just the type change.
- **`Arc<TrafficEntry>` instead of `Arc<[u8]>` inside an
  owning entry.** Wrapping the whole entry in Arc would
  also make clones cheap, but at the cost of forcing every
  consumer through `Arc::deref` and breaking the existing
  by-value APIs. The payload-only change is the minimum
  necessary to enable Phase 10; that scope is the right
  one.

## Open questions

1. **Should the new test live in `bugreport::tests` or in
   the `tests/` integration directory?** In-module — it's a
   structural invariant on `TrafficEntry` specifically, not
   a workflow test. Same shape as the other in-module unit
   tests.

2. **Should the doc comment on `pcap_frame` cite Phase 10 by
   name?** Yes — it's the consumer of the cheap-clone
   property and the reason the type was chosen. A future
   maintainer reading the field declaration cold should be
   able to find the design rationale without grepping the
   plans.

## Companion docs

None new. The `ARCHITECTURE.md` description of the ring
buffer (if present) describes the byte-cap eviction
behaviour, which is unchanged. `AGENTS.md` has no entry to
update — the refactor doesn't introduce a new pattern; it
just chooses a slightly more idiomatic type for the existing
one.
