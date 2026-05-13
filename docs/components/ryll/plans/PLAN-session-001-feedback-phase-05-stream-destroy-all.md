# Phase 05: Handle `SPICE_MSG_DISPLAY_STREAM_DESTROY_ALL`

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`shakenfist-spice-renderer/src/channels/display.rs:135-143`
(the `StreamState` struct — plain data, no OS resources),
`shakenfist-spice-renderer/src/channels/display.rs:1002-1152`
(the display-channel message dispatch; STREAM_CREATE / DATA /
CLIP / DESTROY / DATA_SIZED / ACTIVATE_REPORT are handled,
DESTROY_ALL is not),
`shakenfist-spice-renderer/src/channels/display.rs:517`
(`streams: HashMap<u32, StreamState>` — the only state the
handler needs to touch), and
`shakenfist-spice-protocol/src/constants.rs:179-211` (the
`display_server` opcode module; STREAM_DESTROY_ALL is
missing). Cross-reference the SPICE protocol at
`/srv/src-reference/spice/spice-protocol/spice/enums.h:497`
(`SPICE_MSG_DISPLAY_STREAM_DESTROY_ALL` = 126),
`/srv/src-reference/spice/spice-common/spice.proto:787`
(empty payload), and the one-line equivalent in
`spice-gtk/src/channel-display.c:1878-1881` which calls
`clear_streams(channel)` — itself a `for-loop over c->streams`
that destroys each and zeroes `c->nstreams`. ryll's
equivalent is `self.streams.clear()`.

This phase fixes bug **K5** — "Unhandled
`SPICE_MSG_DISPLAY_STREAM_DESTROY_ALL` (msg type 126)" —
identified from the pedantic zip at 08:36Z during dogfooding
session 001. The master plan flags this as small ("Phase 05
— own plan (small); empty payload, one-line equivalent in
spice-gtk").

## Situation

### What we already established

**The opcode and payload.** SPICE display message 126 is
`SPICE_MSG_DISPLAY_STREAM_DESTROY_ALL` with an empty body
(the `.proto` declares it as `Empty stream_destroy_all`). The
server emits it whenever it wants to tear down every active
stream at once — typically immediately before a resolution
change or surface reconfiguration, where each individual
`STREAM_DESTROY` (msg 125) would be redundant book-keeping.

**ryll currently treats it as an unknown opcode.** The
display-channel dispatch at
`shakenfist-spice-renderer/src/channels/display.rs:1148`
catches anything not explicitly matched and calls
`logging::log_unknown_once("display", msg_type, payload)`.
That registers the gap key `display:hexdump:126` in the
warn_once registry the very first time msg 126 arrives.
session 001's pedantic zip recorded exactly that key.

**Stream state lives in a single field.** Each
`DisplayChannel` owns
`streams: HashMap<u32, StreamState>` (`display.rs:517`).
`StreamState` is plain data — surface id, codec, dest
rectangle, optional cached DHT segments (`display.rs:135-143`).
No OS handles, no decoder state, nothing to clean up beyond
the `Drop` impl that runs implicitly when the `HashMap` is
cleared. spice-gtk has `clear_streams(channel)` which does
the same thing plus an explicit decoder teardown; the ryll
equivalent really is `self.streams.clear()`.

**The graceful-degrade backstop already works.** The
`STREAM_DATA` / `STREAM_DATA_SIZED` handler at
`display.rs:1072` looks up the stream via
`self.streams.get_mut(&stream_id)` and silently no-ops on
miss. So a `STREAM_DATA` for a stream that DESTROY_ALL would
have removed is harmless in the current code — provided the
server doesn't reuse the stream_id for a new stream with
different parameters before re-issuing `STREAM_CREATE`.

### What we don't yet know

Whether ryll has ever experienced the stream-id-reuse hazard
in the wild. Servers normally send `STREAM_CREATE` for the
new generation immediately after `STREAM_DESTROY_ALL`, which
overwrites the stale entry in our HashMap and recovers
correct behaviour. So the worst observable today is:

- A small per-resolution-change memory leak (one
  `StreamState` per orphaned stream — bounded by the server's
  stream count, typically 1-2).
- The `display:hexdump:126` gap key persisting in
  `--pedantic` reports until the fix lands, distracting
  reviewers from real gaps.

Neither rises above "low impact" in the master plan's K5
classification.

## Mission and problem statement

Make ryll handle `SPICE_MSG_DISPLAY_STREAM_DESTROY_ALL`
explicitly, matching spice-gtk's `clear_streams` semantic:
drop every entry from the in-channel `streams` HashMap and
log an `info!` line so a maintainer reading the trace can
see the event. The `display:hexdump:126` gap key must stop
appearing.

The phase succeeds when:

- A guest-driven resolution change (the most common trigger
  for msg 126) does **not** add `display:hexdump:126` to the
  warn_once registry.
- The stream HashMap is empty immediately after the
  DESTROY_ALL is processed, so any subsequent STREAM_CREATE
  starts from a clean slate.
- No regression in the existing `STREAM_DESTROY` handler
  (which still removes a single stream by id).
- No new dependencies or refactors — the change is a
  single match arm plus a constant declaration.

## Approach

### The fix

Add the opcode to `shakenfist-spice-protocol/src/constants.rs`'s
`display_server` module:

```rust
pub const STREAM_DESTROY_ALL: u16 = 126;
```

Slot it between `STREAM_DESTROY` (125) and the next contiguous
opcode (`MIGRATE` if defined, otherwise the gap before draw
opcodes at 302). This keeps the numeric order obvious to
anyone scanning the enum.

Add a new arm to the display-channel dispatch at
`display.rs:1136` (right after `STREAM_DESTROY`):

```rust
display_server::STREAM_DESTROY_ALL => {
    // Empty payload — server signals "tear down every
    // active stream", typically before a resolution change
    // or surface reconfiguration. Equivalent to spice-gtk's
    // clear_streams(channel) at channel-display.c:1855.
    info!(
        "display: stream_destroy_all (clearing {} streams)",
        self.streams.len()
    );
    self.streams.clear();
}
```

That's the entire functional change. Two locations, both
mechanical.

### Add it to the protocol's message-name table

`shakenfist-spice-protocol/src/logging.rs:383` already maps
`STREAM_DESTROY` → `"stream_destroy"` for the human-readable
trace logging. Add the parallel entry for `STREAM_DESTROY_ALL`
→ `"stream_destroy_all"` so trace output stays consistent.

### Tests

The display channel's message dispatch is `&mut self` over a
fully-constructed `DisplayChannel`, which needs mpsc channels,
Arcs, snapshots, capture, byte counter, and notification
plumbing. Building a test harness for this single match arm
would be disproportionate.

The right level of testing for K5:

- **Protocol-constant test in
  `shakenfist-spice-protocol/src/constants.rs::tests`** (or
  a fresh `tests` mod if none exists in that file) — a
  one-line assertion that
  `display_server::STREAM_DESTROY_ALL == 126`. Pins the value
  against future typos / accidental edits. Cheap.
- **Manual integration check**: trigger a resolution change
  on the guest (e.g. `xrandr --output ... --mode 1024x768`
  then back to native), confirm `--pedantic` does not log
  `display:hexdump:126` after the fix, and a debug-level
  trace shows the new `stream_destroy_all (clearing N streams)`
  line.

No unit test for the `self.streams.clear()` side effect is
worth its construction cost. The HashMap's `clear` is
trivially correct, and the integration check confirms the
end-to-end path.

### Why not also clear surfaces?

A natural question: should DESTROY_ALL also drop any
`self.surfaces` state that the streams referenced? spice-gtk's
`clear_streams` *only* touches streams — surfaces are managed
by a separate `SURFACE_DESTROY` message family. Mirror that
narrow scope; expanding to surfaces would be speculative.

### Why not also reset codec / cache state?

The cached DHT segments inside each `StreamState` are
already dropped by the `HashMap::clear` call (they're owned
`Option<Vec<u8>>` fields). The shared GLZ dictionary and the
image cache are per-channel state independent of streams,
and the SPICE server is responsible for sending appropriate
invalidation messages for those (`INVAL_ALL_PIXMAPS` and
similar) — folding their reset into the DESTROY_ALL handler
would invent semantics the protocol doesn't guarantee.

## Tasks

- [x] Add `pub const STREAM_DESTROY_ALL: u16 = 126;` to
      `shakenfist-spice-protocol/src/constants.rs` in the
      `display_server` module, immediately after
      `STREAM_DESTROY`.
- [x] Add `display_server::STREAM_DESTROY_ALL => "stream_destroy_all"`
      to the message-name lookup in
      `shakenfist-spice-protocol/src/logging.rs`.
- [x] Add a new match arm in
      `shakenfist-spice-renderer/src/channels/display.rs`
      after the existing `STREAM_DESTROY` arm. Body:
      `info!` log with current stream count, then
      `self.streams.clear()`. Comment cites the spice-gtk
      equivalent for traceability.
- [x] Add a sanity unit test in `shakenfist-spice-protocol`
      (`constants::tests::display_stream_destroy_all_opcode_pinned`)
      asserting `display_server::STREAM_DESTROY_ALL == 126`.
- [x] Update `PLAN-session-001-feedback.md` K5 row in the
      "Confirmed bugs" table with the resolution pointer.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 05 → Done.
- [ ] Manual integration check (deferred operator action,
      bundles with the running Phase 02–04 manual
      checklist): connect to a guest that supports resolution
      changes, trigger one (`xrandr` or display-settings UI),
      then dump the warn_once registry (the gap-popup in the
      bottom status bar) and confirm `display:hexdump:126`
      is absent. With `--verbose`, also confirm the new
      `display: stream_destroy_all (clearing N streams)`
      info line appears in the trace.

## Out of scope

- **Wider audit of unhandled display opcodes.** The
  `--pedantic` mode already does this systematically by
  registering every `display:hexdump:*` gap key; K5 is the
  one entry from session 001 that warranted its own phase.
  Future-session gaps surface naturally without preemptive
  audit.
- **Clearing surfaces or codec caches in the same handler.**
  Different protocol messages own those concerns; folding
  them in invents semantics. See "Why not…" rationale above.
- **Refactoring `STREAM_DESTROY` and `STREAM_DESTROY_ALL` to
  share an implementation.** They operate on different shapes
  (one entry by id vs. clear all); merging would obscure
  rather than simplify.
- **Headless / web mode tests.** The display channel is the
  same code on every frontend; the protocol-constant guard
  covers the only ryll-specific invariant. No mode-specific
  surface.
- **Telemetry on stream lifecycle.** Counting destroys /
  destroy-alls in the snapshot would be informative for
  diagnosing churn but is independent of K5's "handle the
  opcode" fix. Revisit only if a future bug points at stream
  churn as a root cause.

## Open questions

1. **Should the `info!` log become `debug!` once the fix has
   been live for one dogfooding session?** `info!` is right
   for now — it's the most visible signal that the fix is
   reaching the handler, and the message is rare (one per
   resolution change). If a future session shows it as
   noise, demote in a follow-up.

2. **Should we proactively also handle other documented
   display opcodes that have empty payloads or trivial
   semantics?** No — drive-by additions break the "session
   001 → phase plan → fix" provenance chain that makes the
   master plan auditable. Each unhandled opcode that turns
   up in a future pedantic zip gets its own narrow fix.

## Companion docs

None new. `ARCHITECTURE.md` already describes the display
channel and its stream / surface separation; this change
operates entirely within the established model. `AGENTS.md`
§20 ("`--pedantic` mode: registry observer pattern") already
covers how the warn_once registry surfaces gaps like
`display:hexdump:126` — the fix's effect is to make that gap
disappear from the registry, which §20's machinery handles
automatically once the opcode is matched explicitly.
