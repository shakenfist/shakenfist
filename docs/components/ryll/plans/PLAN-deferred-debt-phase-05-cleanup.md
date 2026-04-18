# Phase 5: Code quality and cleanup

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Reduce noise, remove dead code, and improve consistency.
Six sub-tasks:

| Step | Description | Source |
|------|-------------|--------|
| 5a | Verbose logging cleanup | PLAN-remaining-issues.md #3 |
| 5b | Dead clipboard event removal | PLAN-pr20-followup.md #5 |
| 5c | Clipboard polling optimisation | PLAN-pr20-followup.md #7 |
| 5d | playback_client() deduplication | PLAN-pr23-followup.md #9 |
| 5e | Byte-parsing helpers | PLAN-display-iteration-followups.md |
| 5f | Minor consistency items | PLAN-pr23-followup.md #13-17 |

## 5a. Verbose logging cleanup

### Current state

14 `info!` calls fire on high-frequency events and
should be demoted to `debug!`:

**display.rs:**
- Line 849: `info!("display: draw_copy: surface=...")` --
  fires on every draw_copy message (the most frequent
  display operation).
- Line 1155: `info!("display: draw_copy: no pixels
  produced...")` -- fires when decompression fails.

**inputs.rs:**
- Line 364: `info!("inputs: key down: scancode=...")` --
  every key press.
- Line 382: `info!("inputs: key up: scancode=...")` --
  every key release.
- Line 478: `info!("inputs: mouse down: button=...")` --
  every mouse click.

**cursor.rs:**
- Line 157: `info!("cursor: init: pos=...")` -- every
  INIT message.
- Line 181: `info!("cursor: set: pos=...")` -- every
  SET message.
- Line 207: `info!("cursor: move: ...")` -- every MOVE
  message (very frequent).
- Line 251: `info!("cursor: invalidate_one: id=...")` --
  per invalidation.
- Line 336: `info!("cursor: shape: type=...")` -- every
  cursor shape parse.
- Line 351: `info!("cursor: using cached cursor id=...")` --
  every cache hit.
- Line 371: `info!("cursor: caching cursor id=...")` --
  every cache store.

**app.rs:**
- Line 499: `info!("app: cursor position: ...")` -- every
  cursor position update.
- Line 505: `info!("app: cursor shape: ...")` -- every
  cursor shape update.

**Note:** The `LZ4 header:` hex dump at
`shakenfist-spice-compression/src/lz4.rs:20` is already
at `debug!` level. No change needed.

### Fix

Change each of the 14 `info!` calls listed above to
`debug!`. These messages remain available with `-v` but
don't pollute default output.

### Files to modify

- `ryll/src/channels/display.rs` (2 calls)
- `ryll/src/channels/inputs.rs` (3 calls)
- `ryll/src/channels/cursor.rs` (7 calls)
- `ryll/src/app.rs` (2 calls)

### Complexity

Trivial -- 14 one-word changes (`info!` → `debug!`).

## 5b. Dead clipboard event removal

### Current state

`ChannelEvent::ClipboardReceived { text }` is defined
at channels/mod.rs:78-81 with `#[allow(dead_code)]`
and has a handler at app.rs:526-531. However, **no
channel ever sends it**. Clipboard data from the guest
is handled directly in `main_channel.rs:661-682` (the
`VD_AGENT_CLIPBOARD` handler), which calls
`arboard::Clipboard::new()` inline rather than routing
through the event system.

### Options

1. **Remove** the dead variant and its handler.
2. **Route clipboard through it** -- architecturally
   cleaner because it keeps the arboard side effect in
   the UI thread rather than the network task. This
   would mean `main_channel.rs` sends
   `ClipboardReceived { text }` instead of calling
   arboard directly, and app.rs handles it.

Option 2 is cleaner but touches more code and changes
the clipboard data path. Option 1 is safe and simple.

### Fix

**Option 1: remove the dead code.** Delete the
`ClipboardReceived` variant from `ChannelEvent` in
`channels/mod.rs`, delete the `#[allow(dead_code)]`
annotation, and delete the handler in `app.rs`. The
arboard call in `main_channel.rs` stays where it is.

If we want to route clipboard through events in the
future, that's a dedicated refactoring task, not a
cleanup item.

### Files to modify

- `ryll/src/channels/mod.rs` -- remove variant
- `ryll/src/app.rs` -- remove handler

### Complexity

Trivial.

## 5c. Clipboard polling optimisation

### Current state

`poll_host_clipboard` at main_channel.rs:725 creates a
fresh `arboard::Clipboard` on every call:

```rust
let text = match arboard::Clipboard::new()
    .and_then(|mut cb| cb.get_text()) {
```

This runs every 500ms (clipboard polling interval).
On X11/Wayland this involves repeated system calls to
open the clipboard connection. Two additional inline
`arboard::Clipboard::new()` calls exist at lines 677
and 691 for guest-to-host clipboard handling.

### Fix

Cache the `arboard::Clipboard` instance as an
`Option<arboard::Clipboard>` field in `MainChannel`.
Create it lazily on first use and reuse it for all three
call sites (poll, set_text, get_text).

If a cached instance returns an error (e.g. the display
server restarted), drop it and recreate on the next
call. This handles platform-specific staleness.

```rust
fn clipboard(&mut self) -> Option<&mut arboard::Clipboard> {
    if self.cached_clipboard.is_none() {
        self.cached_clipboard = arboard::Clipboard::new().ok();
    }
    self.cached_clipboard.as_mut()
}
```

### Files to modify

- `ryll/src/channels/main_channel.rs` -- add field,
  add helper, update 3 call sites

### Complexity

Low.

## 5d. playback_client() deduplication

### Status: already fixed

Verified in phase 2 planning and confirmed again now.
`playback_client()` at logging.rs:264 already delegates
to `common_client().unwrap_or("unknown")`. No work
needed.

## 5e. Byte-parsing helpers

### Current state

68 instances of manual `u32::from_le_bytes([data[offset],
data[offset+1], ...])` patterns across the codebase:

| File | Count |
|------|-------|
| display.rs | 46 |
| main_channel.rs | 8 |
| playback.rs | 5 |
| cursor.rs | 3 |
| inputs.rs | 1 |
| messages.rs | 5 |
| **Total** | **68** |

### Fix

Add a small set of inline helper functions to the
protocol crate:

```rust
/// Read a little-endian u16 from `data` at `offset`.
pub fn read_u16_le(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([data[offset], data[offset + 1]])
}

pub fn read_u32_le(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

pub fn read_u64_le(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes([
        data[offset], data[offset + 1],
        data[offset + 2], data[offset + 3],
        data[offset + 4], data[offset + 5],
        data[offset + 6], data[offset + 7],
    ])
}

pub fn read_i32_le(data: &[u8], offset: usize) -> i32 {
    i32::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}
```

Then convert `display.rs` (46 instances, the biggest
win). Other files can be converted in follow-up work to
keep the diff manageable.

### Files to modify

- `shakenfist-spice-protocol/src/lib.rs` -- add helpers
  (or a new `parse` module)
- `ryll/src/channels/display.rs` -- convert 46 call
  sites

### Complexity

Low per change, but high volume. The conversions are
mechanical: each 4-line block becomes a 1-line call.
The risk is low since the logic is identical.

## 5f. Minor consistency items

### 5f-i. PlaybackChannel re-export

`channels/mod.rs` re-exports every channel struct
except `PlaybackChannel`. Add:

```rust
pub use playback::{PlaybackChannel, VolumeControl};
```

Then update `app.rs` to import via the short path.

### 5f-ii. Poisoned mutex recovery

**Resolved.** The phase 4 refactor removed all Mutex
usage from the playback channel. No `into_inner()`
patterns remain. No work needed.

### 5f-iii. STATS_BAR_HEIGHT

`app.rs:32` hardcodes `const STATS_BAR_HEIGHT: f32 =
20.0`. The original suggestion was to auto-size based
on content. However, auto-sizing requires measuring
the status bar content in egui, which adds complexity
for minimal gain. The hardcoded value works. **Defer.**

### 5f-iv. MODE message byte offset comment

`playback.rs` MODE handler reads `payload[4..5]`
(skipping a 4-byte timestamp) with no comment. Add:

```rust
// SpiceMsgPlaybackMode: time(u32) + mode(u16).
// Skip the 4-byte multimedia timestamp.
self.audio_mode = u16::from_le_bytes([
    payload[4], payload[5]
]);
```

### 5f-v. DATA timestamp skip comment

`playback.rs` DATA handler uses `&payload[4..]` with
no comment. Add:

```rust
// SpiceMsgPlaybackPacket: time(u32) + data.
// Skip the 4-byte multimedia timestamp.
let audio_data = &payload[4..];
```

## Commit sequence

1. **5a**: Demote 14 info! calls to debug! (one commit
   touching 4 files).
2. **5b**: Remove dead ClipboardReceived variant and
   handler.
3. **5c**: Cache arboard::Clipboard instance.
4. **5e**: Add byte-parsing helpers and convert
   display.rs.
5. **5f**: PlaybackChannel re-export, MODE/DATA
   comments (one commit for all minor items).

Step 5d requires no work. Steps 5f-ii and 5f-iii are
resolved or deferred.

## Administration

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
