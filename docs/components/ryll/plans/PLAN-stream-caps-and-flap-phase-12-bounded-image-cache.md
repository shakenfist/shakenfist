# Phase 12 — Bounded image cache

Phase 12 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
master plan, the existing image-cache touchpoints in
`shakenfist-spice-renderer/src/channels/display.rs` (the
field declaration around line 621, the `remove`/`clear`/`get`
/ `insert` call sites at lines 1209, 1236, 1242, 2049, 2224,
and the snapshot summary at 2650-2654), and the
`shakenfist-spice-renderer/src/snapshots.rs::DisplaySnapshot`
struct where the cache stats are exposed. Ground answers in
what the code does today. For the `lru` crate (if we
choose to use it), the latest stable is on crates.io;
inspect the actual API surface in
`~/.cargo/registry/src/index.crates.io-*/lru-*/` after
adding the dep.

Flag uncertainty explicitly rather than guessing.

## Goal

Replace the unbounded `HashMap<u64, Vec<u8>>` image cache
with a byte-bounded LRU that evicts oldest entries when
total bytes exceed a configurable cap. Driven by session
002g, where the cache grew at 30 MiB/s during full-frame
ZlibGlzRgb video playback (884 → 1843 → 2803 MiB across
three 30 s snapshots — would have OOMed in ~10 minutes).
Reinforced by session 002h showing the same pattern on a
60-second clip. Until this is fixed we cannot dogfood long
sessions without running out of RAM.

The fix is small in scope but high in real-world value: a
bounded cache is the minimum requirement for ryll being
usable as a long-running VDI client rather than a
short-burst diagnostic tool.

## Scope

In scope:

- A bounded LRU image-cache type in a new module
  `shakenfist-spice-renderer/src/image_cache.rs` (or in
  the existing `display/` module if cleaner — see Q1).
- Replace the `image_cache: HashMap<u64, Vec<u8>>` field
  on `DisplayChannel` with the new bounded type.
- Byte-based eviction: total bytes (sum of stored
  `Vec<u8>` lengths) capped at a configurable maximum;
  on each `insert`, evict the least-recently-used entries
  until the cap is satisfied.
- Configurable cap via a new CLI flag
  `--image-cache-cap-mib N` (default 256 MiB; CLI takes
  precedence over a `RYLL_IMAGE_CACHE_CAP_MIB` env var
  if we want that — defer that nicety unless trivial).
- Eviction counters on `DisplaySnapshot`:
  - `image_cache_evictions_total: u64` — count of
    individual entries evicted by the cap (separate
    from `inval_*` server-driven evictions, which stay
    in their own per-message log counters).
  - `image_cache_evicted_bytes_total: u64` — sum of
    bytes freed by the cap-driven evictions.
  - `image_cache_cap_bytes: u64` — the configured cap
    (so a bug report tells you what cap the session ran
    under without re-reading the CLI invocation).
- All existing `image_cache.remove(&id)` (inval_list,
  display.rs:1209), `image_cache.clear()` (inval_all,
  1236, 1242), and `image_cache.get(&img_id)` (lookup,
  2049) call sites work unchanged against the new type.
- Unit tests covering: insert under cap → no eviction;
  insert over cap → oldest entry evicted; mass insert
  → eviction count + bytes match expectations; explicit
  remove → respects user intent (decrements byte counter);
  clear → resets bytes to zero.

Out of scope:

- Generation-tracking or "warm vs cold" eviction
  heuristics. Pure LRU by access time is good enough
  for the SPICE workload (the server reuses recent
  cached images via `image_id` cache hits, so MRU stays
  hot naturally).
- Compressing cached entries. Each entry is already raw
  RGBA (~10.7 MiB for a 1920×1472 frame); compressing
  in-memory would add decompress cost to every cache
  hit. Skip.
- Eviction notifications. The eviction counter on the
  snapshot is enough for diagnostic visibility; no UI
  notification is needed.
- ~~Touching the GLZ dictionary cache (the
  `glz_dictionary` field). GLZ is bounded by SPICE
  protocol semantics already — it's a sliding window
  the server controls. Only the `image_cache` (which
  holds CACHE_ME-flagged decoded RGBA frames) needs the
  bound.~~ **THIS WAS WRONG**: 12D smoke test (session
  003) showed `image_cache_bytes` growing to 5 GiB
  despite the 256 MiB cap, because the snapshot field
  sums BOTH caches and the GLZ dictionary is also an
  unbounded `Mutex<HashMap<u64, Vec<u8>>>` with
  `insert` blindly appending and shrinking only on
  explicit `inval_*`. GLZ now back **in scope** —
  see steps 12E and 12F below.
- Adapting the cap dynamically based on host RAM. The
  CLI flag is enough; operators set the right value
  for their machine.

## Open questions

- **Q1 (decide in 12A): use the `lru` crate or hand-roll?**
  The `lru` crate is the de-facto standard, small (~500
  lines), no transitive deps, MIT-licensed. It's keyed
  by entry count, not by bytes, so we'd wrap it. Working
  proposal: **use `lru`** and wrap it. The wrapper holds
  the byte total alongside the `LruCache` and prunes after
  each insert. Hand-rolling buys nothing over an
  already-audited crate for this shape of cache.

- **Q2 (decide in 12A): module location?**
  - `shakenfist-spice-renderer/src/image_cache.rs` —
    small dedicated module next to the renderer's other
    helpers.
  - `shakenfist-spice-renderer/src/channels/display/image_cache.rs`
    — under the display channel since that's the only
    consumer.
  - `shakenfist-spice-compression` — wrong home; this
    isn't a compression algorithm.
  Recommend the first option. It keeps the channel
  module from growing and signals that the cache could
  be reused by another channel in principle.

- **Q3 (decide in 12B): default cap value?** 256 MiB feels
  right for a typical desktop session: enough to hold
  ~25 full-screen 1920×1472 frames (10.7 MiB each), well
  short of any reasonable host RAM constraint. 128 MiB
  would cap at ~12 entries — too aggressive for static-UI
  workloads where the server reuses cached bitmaps. 512
  MiB starts to matter on 8 GB hosts. **Recommend 256
  MiB** as the default; document the trade-offs in
  `docs/configuration.md`.

- **Q4 (open): should the cap default change for `--web`
  mode?** The web frontend runs server-side and serves
  multiple concurrent clients in principle. A per-session
  256 MiB cap may be too generous if many clients
  attach. Defer: ship the same default for both modes,
  revisit if the multi-client web case shows up.

- **Q5 (open): logging on eviction?** Logging every
  eviction at info would spam the log on video workloads
  (one eviction per frame past the cap). Logging at
  debug is harmless and useful. Recommend: log first
  eviction in a session at info ("image_cache: cap N
  MiB reached; oldest entries will be evicted"),
  subsequent evictions at debug.

## Design notes

### The cache type

```rust
// shakenfist-spice-renderer/src/image_cache.rs (new)

use lru::LruCache;

pub struct BoundedImageCache {
    inner: LruCache<u64, Vec<u8>>,
    bytes: usize,
    cap_bytes: usize,
    evictions_total: u64,
    evicted_bytes_total: u64,
    first_eviction_logged: bool,
}

impl BoundedImageCache {
    pub fn new(cap_bytes: usize) -> Self { ... }

    /// Insert (or replace) an entry. May trigger LRU
    /// eviction. If the single entry's bytes exceed the
    /// cap, it is rejected and counted as a refusal —
    /// the cache cannot hold it.
    pub fn insert(&mut self, key: u64, value: Vec<u8>) -> InsertOutcome { ... }

    /// Returns Some and bumps to MRU on hit. None on miss.
    pub fn get(&mut self, key: &u64) -> Option<&Vec<u8>> { ... }

    /// Returns true iff the key was present.
    pub fn remove(&mut self, key: &u64) -> bool { ... }

    pub fn clear(&mut self) { ... }

    pub fn len(&self) -> usize { self.inner.len() }
    pub fn bytes(&self) -> usize { self.bytes }
    pub fn cap_bytes(&self) -> usize { self.cap_bytes }
    pub fn keys(&self) -> impl Iterator<Item = &u64> + '_ { self.inner.iter().map(|(k, _)| k) }
    pub fn evictions_total(&self) -> u64 { self.evictions_total }
    pub fn evicted_bytes_total(&self) -> u64 { self.evicted_bytes_total }
}

pub enum InsertOutcome {
    Inserted,
    InsertedAfterEviction { evicted: u32, freed_bytes: usize },
    Refused { reason: RefusedReason },
}

pub enum RefusedReason {
    EntryLargerThanCap,
}
```

The `Refused` variant matters: if a single image's RGBA
is larger than the entire cap (e.g. operator set the cap
to 8 MiB and a 1920×1472 frame at 10.7 MiB arrives), the
cache should refuse rather than thrash the LRU evicting
everything before inserting the giant entry. The caller
(display.rs) treats refusal as "this image isn't cached
but the draw still works" — same outcome as no CACHE_ME
flag.

### DisplayChannel integration

The existing field declaration:

```rust
image_cache: HashMap<u64, Vec<u8>>,
```

becomes:

```rust
image_cache: BoundedImageCache,
```

All five call sites (lines 1209, 1236, 1242, 2049, 2224)
need minimal adaptation:

- `remove(&id)` → unchanged signature (`bool` return).
- `clear()` → unchanged.
- `get(&img_desc.image_id)` → unchanged signature
  (`Option<&Vec<u8>>`); the LRU also bumps to MRU on
  hit, which is what we want.
- `insert(img.image_id, img.pixels.clone())` → returns
  `InsertOutcome` instead of `Option<Vec<u8>>`; update
  the caller to handle the eviction count or just
  ignore the return (the counters live on the cache).
- The `update_snapshot` summary (lines 2650-2654) reads
  `len()`, `bytes()`, `keys()` — all available on the
  new type.

### Snapshot field additions

```rust
// shakenfist-spice-renderer/src/snapshots.rs / DisplaySnapshot

/// Cumulative count of image-cache entries evicted by the
/// LRU cap since session start. Separate from server-driven
/// invalidations (inval_list / inval_all).
pub image_cache_evictions_total: u64,

/// Cumulative bytes freed by LRU evictions since session
/// start. Reading this alongside `image_cache_bytes` tells
/// you whether the cache is steady-state under churn (high
/// evicted_bytes_total with stable image_cache_bytes) or
/// growing toward cap (low evicted_bytes_total).
pub image_cache_evicted_bytes_total: u64,

/// The configured cap in bytes for this session. Mirrors
/// the value passed via --image-cache-cap-mib (default
/// 256 MiB). Surfaced so a bug report tells you what cap
/// the session ran under.
pub image_cache_cap_bytes: u64,
```

### CLI flag

```rust
// ryll/src/config.rs

/// Maximum total bytes for the SPICE display image cache,
/// in MiB. Defaults to 256. The cache holds decoded RGBA
/// for images the server flagged with CACHE_ME; without a
/// cap, video workloads can consume gigabytes (see
/// session-002g).
#[arg(long, default_value_t = 256)]
pub image_cache_cap_mib: u64,
```

Threaded through `main.rs` and `RyllApp::new` to
`DisplayChannel::new`, multiplied by `1024 * 1024` to get
bytes.

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12A | medium | sonnet | none | **BoundedImageCache type + unit tests.** Add `lru = "0.12"` (or current stable) to `shakenfist-spice-renderer/Cargo.toml`. Create `shakenfist-spice-renderer/src/image_cache.rs` implementing `BoundedImageCache` per *Design notes / The cache type*. The wrapped `lru::LruCache` is sized by `NonZeroUsize` entry count (the crate's design) — we cap by *bytes* on top: set the LruCache's entry cap to `usize::MAX` (or a very large number) and enforce the byte cap ourselves by evicting from the back of the LRU iterator after each insert until `bytes <= cap_bytes`. Implement `insert`/`get`/`remove`/`clear`/`len`/`bytes`/`cap_bytes`/`keys`/`evictions_total`/`evicted_bytes_total`. Decide on `InsertOutcome` and `RefusedReason` enums per the design notes. Per Q5: log first eviction in a session at info ("image_cache: cap NN MiB reached; oldest entries will be evicted"), subsequent evictions at debug. Add unit tests: (a) insert under cap → no eviction; (b) insert over cap → oldest entry evicted with correct byte accounting; (c) repeated inserts of same key replace (not double-count) bytes; (d) `get` on hit bumps to MRU (next eviction targets the previously-inserted entry); (e) single entry larger than cap → `Refused`; (f) `clear` resets all counters except `evictions_total` and `evicted_bytes_total` (those are session-wide, not cache-wide). Verify `make build && make test && make lint`. |
| 12B | medium | sonnet | none | **Wire into DisplayChannel + snapshot fields + CLI flag.** Replace `image_cache: HashMap<u64, Vec<u8>>` with `image_cache: BoundedImageCache` on `DisplayChannel`. Update the five call sites (display.rs:1209, 1236, 1242, 2049, 2224) — only the `insert` site changes return-type handling. Add `image_cache_cap_mib: u64` (default 256) to `ryll/src/config.rs`, thread through `main.rs` → `RyllApp::new` → `DisplayChannel::new` (multiply by 1024*1024 to get bytes). Add the three new fields to `DisplaySnapshot` (`image_cache_evictions_total`, `image_cache_evicted_bytes_total`, `image_cache_cap_bytes`) per *Design notes / Snapshot field additions*. Wire `update_snapshot` writes. Extend `ryll/src/bugreport.rs::test_display_snapshot_serialises` to populate non-zero values and assert each new field appears in JSON. Verify `make build && make test && make lint`. |
| 12C | low | haiku | none | **Docs touch-up.** Add the `--image-cache-cap-mib` flag to `docs/configuration.md`. Add a section to `docs/troubleshooting.md` (next to or near the auto-snapshot section) explaining when to lower the cap (small RAM hosts) or raise it (heavy CACHE_ME workloads), and what the eviction counters in a bug report mean. Cross-link from `docs/libvirt-spice-recommendations.md` under the streaming-video discussion (the leak is server-driven via `IMAGE_FLAGS_CACHE_ME` on video frames, so server-side knobs that reduce streaming-video activity also reduce cache pressure). Update `ARCHITECTURE.md` if it documents the image cache (it likely doesn't yet — add a one-sentence note). Verify `pre-commit run --all-files`. |
| 12D | — | — | — | **Operator smoke test (FAILED 2026-05-20 in session 003).** Re-run the 002h workload (Debian 11 QXL guest, video playback that triggers the full-frame ZlibGlzRgb fallback pattern) with the default `--image-cache-cap-mib 256` and auto-snapshot enabled. Original criteria: (a) `image_cache_bytes` stays at or below 256 MiB across the full session; (b) `image_cache_evictions_total` and `image_cache_evicted_bytes_total` increment past the first snapshot (proves eviction is firing); (c) `image_cache_cap_bytes` reads `268435456` (256 MiB); (d) the session can run for at least 10 minutes without the process growing past the cap + the rest of ryll's footprint. **What actually happened**: 003a's last snapshot showed `image_cache_bytes: 5068 MiB / 256 MiB cap, evictions: 0`. Phase 12A's `BoundedImageCache` works correctly — the leak is in `GlzDictionary`, which the snapshot field sums into `image_cache_bytes` without distinguishing the two caches. Remediation: 12E (bound the GLZ dictionary) and 12F (split the snapshot fields) below. 12D will be re-run after 12E+12F land. |
| 12E ✅ landed (`49cb5e81`) | medium | sonnet | none | **Bound the GLZ dictionary.** `GlzDictionary` at `shakenfist-spice-compression/src/glz_dictionary.rs` (or wherever it lives — grep) is currently a `Mutex<HashMap<u64, Vec<u8>>>` with `insert`/`remove`/`clear`/`len`/`total_bytes` and no eviction policy. Apply the same byte-cap + LRU eviction pattern as `BoundedImageCache`. Cleanest path: refactor `BoundedImageCache` from `shakenfist-spice-renderer/src/image_cache.rs` into a generic `ByteBoundedLru<K, V: AsRef<[u8]>>` (or just `ByteBoundedLru` keyed `u64 -> Vec<u8>` since both consumers want exactly that) that both the renderer's `image_cache` and the compression crate's `GlzDictionary` wrap; move the type into `shakenfist-spice-compression` (it's a data structure, not a renderer concern). Maintain `GlzDictionary`'s existing `Send+Sync` semantics by keeping its outer `Mutex` and protecting the inner `ByteBoundedLru` with it. Add a CLI flag `--glz-dictionary-cap-mib N` (default 256 — same as image_cache; GLZ payloads are the same shape) threaded through `RyllApp` → `DisplayChannel::new_shared_glz_dictionary` (currently zero-arg; takes the cap now). Unit-test that explicit GLZ inserts past the cap evict oldest entries; the existing GlzDictionary tests should be extended, not replaced. Verify `make build && make test && make lint`. |
| 12F ✅ landed (`0f13292b`) | low | sonnet | none | **Split the snapshot fields so the two caches are distinguishable.** Today `DisplaySnapshot.image_cache_bytes = self.image_cache.bytes() + self.glz_dictionary.total_bytes()` (display.rs:2653) and the matching `_entries` / `_ids` fields likewise sum both caches. That summing is what made the 12D failure mode confusing. Refactor so: `image_cache_bytes` / `_entries` / `_ids` reflect ONLY the renderer's image_cache (BoundedImageCache); add NEW fields `glz_dictionary_bytes` / `glz_dictionary_entries` / `glz_dictionary_cap_bytes` / `glz_dictionary_evictions_total` / `glz_dictionary_evicted_bytes_total` for the GLZ dictionary's state. Update `update_snapshot`, the `DisplaySnapshot` struct in `snapshots.rs`, and `test_display_snapshot_serialises` accordingly. This is a JSON-schema breaking change to `image_cache_bytes` (it'll be ~12× smaller than what historical bug reports show); document it in `docs/troubleshooting.md` and add an upgrade note. Verify `make build && make test && make lint`. |
| 12G ✅ landed (`1505c26e`) | low | haiku | none | **Docs catchup for 12E + 12F.** Document `--glz-dictionary-cap-mib` in `docs/configuration.md`. Add a "Glz dictionary pressure" subsection to `docs/troubleshooting.md` parallel to the existing "Display image cache pressure" section. Note the `image_cache_bytes` field-semantics change explicitly (it no longer includes GLZ). Cross-link from `docs/libvirt-spice-recommendations.md`. Verify `pre-commit run --all-files`. |
| 12H | — | — | — | **Re-run 12D's smoke test against the 12E/12F binary.** Same workload (a full-screen ZlibGlzRgb-heavy session for ≥10 min). Verify: (a) `image_cache_bytes ≤ image_cache_cap_bytes`; (b) `glz_dictionary_bytes ≤ glz_dictionary_cap_bytes`; (c) eviction counters on both caches increment; (d) process RSS bounded. Operator verification, not a code change. |

Commits: one per step (12A, 12B, 12C). 12D is operator verification.

## Test plan

Automated (12A–12B):

- `BoundedImageCache` unit tests as enumerated in 12A's
  brief.
- `test_display_snapshot_serialises` extended for the
  three new fields (12B).

Manual (12D):

- The smoke test recipe in 12D's brief. Auto-snapshot
  mode (phase 5) makes this cheap: enable
  `--auto-snapshot-interval 30 --auto-snapshot-cap 20`
  alongside the cache cap and just walk away. Diff
  `image_cache_bytes` across snapshots to confirm the
  cap holds.

## Documentation impact

- `docs/configuration.md` — `--image-cache-cap-mib` flag
  (12C).
- `docs/troubleshooting.md` — eviction-counter
  interpretation (12C).
- `docs/libvirt-spice-recommendations.md` — cross-link
  (12C).
- `ARCHITECTURE.md` — one-sentence note on the cache
  (12C).
- Phase 10 (docs) will further consolidate.

## Success criteria

- `BoundedImageCache::insert` evicts oldest entries until
  total bytes ≤ cap.
- `BoundedImageCache::get` is O(1) and bumps to MRU on
  hit.
- All existing `image_cache.{remove,clear,get}` call
  sites work unchanged against the new type.
- `DisplaySnapshot.image_cache_bytes` never exceeds
  `DisplaySnapshot.image_cache_cap_bytes` in any bug
  report.
- The 002h-class workload runs for 10+ minutes without
  the process RSS growing past the cache cap + ~500 MiB
  of base ryll footprint.
- `make build && make test && make lint && pre-commit
  run --all-files` clean.

## Back brief

Before executing 12A, the implementing sub-agent should
back-brief the operator with:

- Decision on Q1 (`lru` crate vs hand-roll).
- The chosen LruCache entry-cap value (working proposal:
  `usize::MAX` so byte cap is the only bound).
- Whether `InsertOutcome` is needed or whether a simpler
  `Option<usize>` return (bytes evicted) is sufficient.
