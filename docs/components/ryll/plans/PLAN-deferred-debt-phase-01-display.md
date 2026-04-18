# Phase 1: Display correctness

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Fix the bugs that produce visually wrong output on screen,
harden JPEG parsing against malformed input, and add
graceful handling for unsupported image types.

Four sub-tasks, each a separate commit:

| Step | Description | Source |
|------|-------------|--------|
| 1a | GLZ cross-frame image corruption | PLAN-remaining-issues.md #1 |
| 1b | GLZ cross-image retry loop optimisation | PLAN-display-iteration-followups.md |
| 1c | Missing image type stubs | PLAN-display-iteration-followups.md |
| 1d | Bounds check in inject_dht | PLAN-pr20-followup.md #3 |

## 1a. GLZ cross-frame image corruption

### Current state

The GLZ decompressor lives in
`shakenfist-spice-compression/src/glz.rs` (307 lines).
It decompresses SPICE GLZ images which can reference
pixels from previously decompressed images via a shared
dictionary of type `Arc<Mutex<HashMap<u64, Vec<u8>>>>`.

The symptom is random areas of incorrect pixels after
incremental display updates, specifically in regions
updated by ZLIB_GLZ_RGB or GLZ_RGB draw_copy messages.

### Root cause investigation

Comparing the Rust implementation against the Python
reference in `kerbside/kerbside/utilities/glz.py` (132
lines), the decompression algorithm itself appears
faithful. The control byte parsing, pixel_flag/image_flag
decoding, and pixel copy loops all match. However, the
investigation has revealed **three dictionary management
bugs** that are the likely cause of corruption:

**Bug 1: No dictionary eviction.** The GLZ header
contains a `win_head_dist` field (parsed at glz.rs:61)
that specifies the sliding window size for the
dictionary. Images older than `image_id - win_head_dist`
should be evicted. Currently `win_head_dist` is parsed
but discarded -- it is not in the `DecompressedImage`
struct and no eviction code exists anywhere. The
dictionary grows without bound until a `RESET` message
clears it entirely (display.rs:525).

Without eviction, the dictionary may hold stale images
that the server has already evicted from its own
dictionary. If a new image is compressed against a
server-side dictionary that does NOT contain an old
image, but our client-side dictionary still has it,
the decompressor could pick up stale pixel data.
Conversely, if the dictionary grows too large, memory
pressure could cause issues.

The Python reference in
`kerbside/testclient/ryll/display_types.py` has
eviction code that is **commented out** -- it was a
fixed-size (100 image) LRU that was disabled. The proxy
copy (`kerbside/kerbside/utilities/glz.py`) has no
eviction at all. So neither Python implementation
evicts either, suggesting this may not be the primary
corruption cause but is still incorrect behaviour.

**Bug 2: IMAGE_FLAGS_CACHE_ME is never checked.** The
SPICE protocol's `ImageDescriptor` has a `flags` field;
bit 0 (`IMAGE_FLAGS_CACHE_ME`) tells the client whether
to cache the decoded image. Currently display.rs caches
every decoded GLZ image unconditionally (lines 1117-1129),
regardless of this flag. The constant is defined in the
protocol crate (`constants.rs:312`) and the flag value
is logged (`display.rs:842`) but never tested.

If the server sends images without `CACHE_ME` set, we
should not insert them into the GLZ dictionary. Having
extra images in the dictionary should not corrupt
references to images that ARE in the dictionary, so this
is more of a memory waste / correctness hygiene issue.

**Bug 3: INVALIDATE_LIST does not clear GLZ dictionary.**
Display channel message handlers for `INVALIDATE_LIST`
(display.rs:480-512) and `INVAL_ALL_PIXMAPS`
(display.rs:514-519) only clear `self.image_cache`, not
`self.glz_dictionary`. Server-initiated cache
invalidation therefore does not affect GLZ-cached images.
If the server sends an invalidation and then reuses an
image ID with different content, the client will render
stale pixels.

### Plan

1. **Add `win_head_dist` to `DecompressedImage`.**
   Add a `pub win_head_dist: u32` field to the
   `DecompressedImage` struct in
   `shakenfist-spice-compression/src/lib.rs`.
   Populate it from the parsed header value in
   `glz.rs`. Update `DecompressedImage::new()` and
   all construction sites.

2. **Implement dictionary eviction in display.rs.**
   After inserting a GLZ image into the dictionary
   (display.rs:1122-1126), evict entries with
   `image_id < current_id - win_head_dist`. This
   matches the SPICE server's sliding window semantics.

3. **Check IMAGE_FLAGS_CACHE_ME before caching.**
   At display.rs:1117, check `img_desc.flags` against
   `IMAGE_FLAGS_CACHE_ME` before inserting into either
   cache. Non-GLZ images already go through `image_cache`
   which is subject to invalidation, so the main impact
   is on GLZ images.

   **Note:** The GLZ dictionary is special -- GLZ
   cross-frame references require the referenced image
   to be present. The server should only generate
   cross-frame references to images it told us to cache.
   If we start skipping un-flagged images and then get a
   cross-frame reference to one, we'll hit the retry/
   miss path. This needs careful testing. If it causes
   regressions, gate it behind a log warning instead
   and keep caching everything. However, for GLZ images
   specifically, the server manages the dictionary
   window via `win_head_dist` and the protocol
   guarantees that cross-frame references only target
   images within the window. So we should always cache
   GLZ images (they're part of the dictionary by
   definition) but can skip caching for non-GLZ types
   when the flag is not set.

4. **Extend INVALIDATE_LIST to cover GLZ dictionary.**
   In the `INVALIDATE_LIST` handler (display.rs:480-512),
   also remove matching IDs from `self.glz_dictionary`.
   Similarly, in `INVAL_ALL_PIXMAPS` (display.rs:514-519),
   clear both caches. This ensures server-initiated
   invalidation works for all image types.

5. **Add debug logging for cross-frame references.**
   In the cross-frame reference path (glz.rs:209-248),
   add `debug!` logging that shows:
   - The source image ID being looked up
   - Whether it was found
   - The pixel_offset and length being copied
   - The dictionary size at lookup time

   This will help diagnose any remaining corruption
   after the dictionary fixes land.

### Files to modify

- `shakenfist-spice-compression/src/lib.rs` --
  add `win_head_dist` to `DecompressedImage`
- `shakenfist-spice-compression/src/glz.rs` --
  populate `win_head_dist`, add debug logging
- `ryll/src/channels/display.rs` -- eviction,
  CACHE_ME check, INVALIDATE_LIST fix

### Testing

- `cargo test --workspace` must pass.
- Manual test with a real VM session: connect, move
  windows, scroll content, and verify no pixel
  corruption in GLZ-decoded regions.
- Monitor logs for cross-frame reference warnings
  (which would indicate dictionary misses).

### Risk

Medium. The dictionary management changes are
functionally new code in a correctness-critical path.
The eviction and invalidation changes should make
behaviour MORE correct (matching what the server
expects), but if the eviction window is too aggressive,
we could evict images that are still referenced. The
`win_head_dist` value from the header should be the
authoritative window size, matching the server's own
eviction policy.

## 1b. GLZ cross-image retry loop optimisation

### Current state

When a GLZ image references a previous image that is not
yet in the shared dictionary (because another display
channel task hasn't finished decompressing it), the
decompressor retries up to 20 times with 5ms sleeps
(glz.rs:214-216). This adds up to 100ms latency per
miss.

The retry loop also holds the dictionary mutex lock for
the entire pixel copy operation (glz.rs:219-234),
blocking other channels from inserting into the
dictionary during that time.

### Plan

1. **Replace polling with `tokio::sync::Notify`.**
   Add a `Notify` alongside the dictionary:
   ```rust
   pub struct GlzDictionary {
       images: Mutex<HashMap<u64, Vec<u8>>>,
       notify: Notify,
   }
   ```
   When an image is inserted into the dictionary
   (display.rs:1122-1126), call `notify.notify_waiters()`.
   In the cross-frame reference path, instead of
   sleeping 5ms, await `notify.notified()` with a
   timeout. This wakes the decompressor immediately
   when the referenced image becomes available.

2. **Minimise lock duration.** Clone the referenced
   image's pixel data while holding the lock, then
   drop the lock before copying pixels into the output
   buffer. This reduces contention with other channels.
   The clone is a memory cost but GLZ images are
   typically small tiles, not full-frame.

3. **Keep a timeout.** Use
   `tokio::time::timeout(Duration::from_millis(100), ...)`
   around the notify wait loop as a safety net. If the
   image never arrives (e.g. because the server sent a
   bad reference), fail after 100ms as before rather
   than blocking forever.

4. **Update the shared dictionary type.** The type alias
   `SharedGlzDictionary` in display.rs:139 changes from
   `Arc<Mutex<HashMap<u64, Vec<u8>>>>` to
   `Arc<GlzDictionary>`. Update all usage sites.

### Files to modify

- `shakenfist-spice-compression/src/glz.rs` -- new
  dictionary type, notify-based wait
- `shakenfist-spice-compression/src/lib.rs` -- export
  `GlzDictionary`
- `ryll/src/channels/display.rs` -- update type alias,
  notify on insert, update clear/evict calls

### Dependency

This step changes the dictionary type, so it must be
coordinated with step 1a (which adds eviction logic).
Both steps touch the same code paths. Order: 1a first
(fix correctness), 1b second (improve performance).
It is acceptable to combine 1a and 1b into a single
commit if the diff is cleaner that way.

### Testing

- `cargo test --workspace` must pass.
- Manual test: cross-frame references should resolve
  without visible delay.
- Check logs for timeout warnings (which would indicate
  the 100ms fallback being hit).

## 1c. Missing image type stubs

### Current state

The `ImageType` enum defines 12 types. The display
channel handles 8 of them. The remaining 4 fall through
to a `warn!` at display.rs:1089-1095:

- `LzPalette` (100) -- paletted LZ images
- `Surface` (104) -- surface-to-surface copy
- `FromCacheLossless` (106) -- lossless cache variant
- `JpegAlpha` (108) -- JPEG with separate alpha plane

### Plan

Add explicit match arms for each type that log a
descriptive warning and return `None`. The current
catch-all already does this, but explicit arms:

1. Document that we know about these types.
2. Allow type-specific log messages (e.g. "LzPalette
   images require palette data which is not yet
   implemented").
3. Make the `_ =>` arm truly unreachable for known
   protocol values, catching any future additions.

This is a small, self-contained change.

### Files to modify

- `ryll/src/channels/display.rs` -- add 4 match arms
  in the image type dispatch (~line 1089)

### Testing

- `cargo test --workspace` must pass.
- No functional change unless one of these types is
  actually encountered in a session.

## 1d. Bounds check in inject_dht

### Current state

`inject_dht()` (display.rs:58-78) injects cached DHT
(Huffman table) segments into JPEG streams that lack
them. The function walks past APP0/APP1 markers by
reading segment lengths, but does not validate that the
computed segment length stays within bounds:

```rust
while i + 3 < jpeg.len()
    && jpeg[i] == 0xFF
    && (jpeg[i + 1] == 0xE0 || jpeg[i + 1] == 0xE1)
{
    let seg_len = u16::from_be_bytes(
        [jpeg[i + 2], jpeg[i + 3]]
    ) as usize + 2;
    if i + seg_len > jpeg.len() {  // this check exists!
        break;
    }
    out.extend_from_slice(&jpeg[i..i + seg_len]);
    i += seg_len;
}
```

Re-reading the code, the bounds check `i + seg_len >
jpeg.len()` IS present (line 69-71). The original
auto-reviewer flagged this because `extract_dht_segments`
has the check and the reviewer expected `inject_dht`
might not -- but it does. The check prevents the panic.

However, there is still a minor issue: `seg_len` is
computed as `u16 + 2`, which can be at most 65537. If
the length field contains 0xFFFF, `seg_len` = 65537
and `i + seg_len` could overflow on 32-bit platforms
(though ryll targets 64-bit only). This is not a
practical concern but can be tightened with a
`saturating_add`.

### Plan

1. **Add `saturating_add` for `seg_len` computation.**
   Replace `as usize + 2` with `.saturating_add(2)`.
   This is a one-line defensive change.

2. **Add a brief comment** noting that this matches the
   bounds checking in `extract_dht_segments`.

This is minimal -- the code is already correct for all
practical inputs.

### Files to modify

- `ryll/src/channels/display.rs` -- line 68

### Testing

- `cargo test --workspace` must pass.
- Phase 6 of the master plan will add dedicated JPEG
  parsing tests; this phase does not add tests.

## Administration

### Commit sequence

Four commits, one per sub-task:

1. `1a`: GLZ dictionary management fixes (eviction,
   CACHE_ME, invalidation)
2. `1b`: GLZ retry loop → notify-based wake
3. `1c`: Explicit match arms for unsupported image types
4. `1d`: saturating_add in inject_dht

Steps 1c and 1d are independent and can be done in any
order. Steps 1a and 1b are ordered (1a first).

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
