# Phase 8: --pedantic mode and status-bar gap counter

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns
(SPICE protocol handling, channel architecture, async
task model, egui rendering, the phase-2 warn_once
registry, the phase-7 log_unknown_once helper, the
existing F8 bug-report flow), and ground your answers in
what the code actually does today.

Key references for this phase:

* [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
  §"Phase 8: `--pedantic` mode and status-bar gap
  counter — sketch" — the master-plan sketch, including
  the four gap categories and dedupe-key shape.
* [STYLEGUIDE.md §"warn_once for protocol gaps"](https://github.com/shakenfist/ryll/blob/develop/STYLEGUIDE.md)
  — the convention codified in phase 7. Phase 8 turns
  that registry into a live observable.
* [shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs)
  — warn_once registry, `warn_once_impl`,
  `register_key`, `warn_once_keys()`, `log_unknown_once`
  (phase 7). Phase 8 adds an observer hook.
* [ryll/src/bugreport.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs)
  — existing F8 flow: `BugReport::assemble(...)` +
  `write_zip(&dir)`. Phase 8 adds a `Pedantic` variant
  and an auto-assemble helper.
* [ryll/src/app.rs:1276](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1276) —
  existing `TopBottomPanel::bottom("stats")`. Phase 8
  adds a `Gaps: N` widget here and a click-to-list
  popup.

## Goal

Make protocol-coverage gaps a first-class observable
quantity. Two layers:

* **Always-on status-bar counter** — `Gaps: N` in the
  bottom panel, where N is
  `logging::warn_once_count()`. Click opens an inline
  panel listing the keys seen so far.
* **`--pedantic` flag** — in addition to the counter,
  every *new* gap (first fire of a key) triggers a
  bug-report zip to be written to
  `--pedantic-dir` (default
  `./ryll-pedantic-reports/`). Capped at 50 per
  session to bound disk use if the dedupe key set
  explodes for some reason.

With this phase the four gap categories the master plan
identified are all surfaced:

1. **Truly unknown opcode** — every channel's
   `_ => log_unknown(...)` arm becomes
   `_ => log_unknown_once(...)` so it enters the
   registry once per (channel, msg_type).
2. **Known-but-unimplemented opcode** — already done
   in phase 7 for display (`"display:unimpl:draw_*"`).
3. **Implemented op with ignored sub-feature** —
   already done in phases 2-6
   (`"display:<op>:non_op_put"`, `"...:mask_present"`,
   etc.).
4. **Decode failure** — existing `warn!(...)` sites in
   channel handlers that recover from truncation /
   decompression / decode errors get converted to
   `warn_once!(...)` with stable keys.

## Non-goals

* Implementing more draw opcodes. Phases 2-7 already
  landed every opcode we plan to support; this phase
  is instrumentation only.
* Remote reporting. Pedantic-mode reports land on the
  local disk only; no upload, no auto-attach. The user
  still decides what to file.
* Retroactive gap capture. If a gap key fired before
  the observer was registered (e.g. during the
  pre-app handshake), the bug report is generated at
  observer-register time, not retroactively for every
  pre-existing key. Rationale in Open Questions.
* Cross-session gap persistence. The registry is
  session-scoped; restarting ryll resets it. A long-
  term "seen before" database is future work if
  useful.
* Tightening every pre-existing `warn!` into
  `warn_once!`. Phase 8 targets channel-handler
  decode-failure sites only; unrelated warnings (e.g.
  "failed to send event" from mpsc backpressure) stay
  plain `warn!`.

## Current state

* Registry (`HashSet<&'static str>`) in
  [logging.rs:10-13](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs#L10)
  holds phase-2+ keys. `register_key(key) -> bool`
  returns is_new; callers fire side-effects. No
  observer hook.
* `BugReportType` in
  [bugreport.rs:616-622](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L616)
  enumerates 5 channels. `BugReport::assemble(...)` is
  pub(crate); `write_zip(&dir)` already picks a unique
  filename. F8 flow calls these from the GUI.
* `_ => log_unknown(...)` arms live in:
  [cursor.rs:310](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/cursor.rs#L310),
  [display.rs:983](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L983),
  [inputs.rs:329](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L329),
  [main_channel.rs:533](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/main_channel.rs#L533),
  [playback.rs:525](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/playback.rs#L525),
  [usbredir.rs:246](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/usbredir.rs#L246),
  [webdav.rs:292](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/webdav.rs#L292).
  Display already uses the new `log_unknown_once` (via
  phase 7 for its four known-unimpl arms), but its
  `_ =>` fall-through still uses the old
  `log_unknown`. Display has four additional
  known-but-unimpl arms with `log_unknown_once` from
  phase 7.
* Decode-failure `warn!(...)` sites in
  `display.rs::handle_draw_copy` (and its helper
  `decode_image_and_emit`): "null src_bitmap",
  "payload too short", "no image data", "X
  decompression failed", "cache miss". None use
  `warn_once!`; they fire every time.
* `ryll/src/main.rs` and `ryll/src/config.rs` hold the
  `Args` struct. No `--pedantic` flag yet.

## Implementation shape

### 1. Registry observer hook (in `logging.rs`)

```rust
/// Callback fired when a brand-new key is inserted into
/// the warn_once registry. Observers are called in the
/// order they were registered, after the key insert is
/// already durable (subsequent calls to
/// `warn_once_keys()` will include the key). The
/// callback runs on the thread of the triggering
/// `warn_once_impl` / `register_key` call — be cheap
/// and non-blocking, or hand off to a background task.
pub type GapObserver = Arc<dyn Fn(&'static str) + Send + Sync + 'static>;

/// Register an observer. Observers that are registered
/// after a key has already fired are called-back once
/// per already-present key at registration time, so a
/// late-installed UI or --pedantic observer doesn't
/// miss gaps that fired during channel startup.
pub fn register_gap_observer(observer: GapObserver) { ... }

/// (internal) Called from `register_key` after a
/// successful insert. Walks the observer list, calls
/// each. Observers dropped from the list are skipped.
fn dispatch_new_gap(key: &'static str) { ... }
```

Storage: `OnceLock<Mutex<Vec<GapObserver>>>`. Two slots
of mutation:

* **Insert a new observer.** Lock, push, iterate current
  keys (under the registry lock — careful to not deadlock:
  snapshot keys via `warn_once_keys()` first, then release
  the registry lock, then call observers with the
  snapshot).
* **Fire observers on new key.** `register_key` calls
  `dispatch_new_gap(key)` on is_new=true. The dispatch
  locks the observer list briefly, clones the `Arc`
  pointers into a local Vec, releases the lock, and
  calls through the clones. This keeps the critical
  section tiny.

### 2. `BugReportType::Pedantic` + auto-assemble helper

```rust
pub enum BugReportType {
    Display, Input, Cursor, Connection, Usb,
    /// Auto-generated by --pedantic mode on first-seen
    /// gap. `gap_kind` is the warn_once key string.
    Pedantic { gap_key: String },
}
```

The `channel_name()` helper would parse the gap_key's
first colon-delimited segment (`display`, `cursor`, etc.)
to pick the channel to snapshot. If the key doesn't
parse cleanly, default to `Display`.

Add a public helper on `BugReport`:

```rust
/// Auto-generate a bug report for a just-seen gap, write
/// it to `dir`, and return the path. Used by pedantic
/// mode; shares all the assemble-and-write plumbing with
/// the F8 flow.
pub fn write_pedantic(
    dir: &std::path::Path,
    gap_key: &str,
    target_host: &str, target_port: u16,
    traffic: &TrafficBuffers,
    channel_snapshots: &ChannelSnapshots,
    app_snapshot: &Mutex<AppSnapshot>,
    runtime_metrics: RuntimeMetrics,
) -> anyhow::Result<std::path::PathBuf> {
    let report = Self::assemble(
        BugReportType::Pedantic { gap_key: gap_key.to_string() },
        format!("pedantic: {}", gap_key),
        None,
        target_host, target_port,
        traffic, channel_snapshots, app_snapshot,
        None,
        runtime_metrics,
    )?;
    report.write_zip(dir)
}
```

### 3. CLI flags (`main.rs` / `config.rs`)

```rust
/// Auto-write a bug report zip the first time each
/// distinct protocol gap is seen (up to
/// PEDANTIC_REPORT_CAP = 50 per session).
#[arg(long)]
pedantic: bool,

/// Directory for --pedantic-mode bug reports. Created
/// if missing.
#[arg(long, default_value = "./ryll-pedantic-reports")]
pedantic_dir: std::path::PathBuf,
```

Wire in `main.rs` after the `App` is constructed:

```rust
if args.pedantic {
    std::fs::create_dir_all(&args.pedantic_dir)?;
    let dir = args.pedantic_dir.clone();
    let state = pedantic_context_handles(...); // traffic, snapshots, metrics
    let counter = Arc::new(AtomicUsize::new(0));
    logging::register_gap_observer(Arc::new(move |key| {
        let n = counter.fetch_add(1, Ordering::SeqCst);
        if n >= PEDANTIC_REPORT_CAP {
            return;
        }
        // spawn a task so the warn_once call site doesn't
        // block on disk I/O + assembly.
        let dir = dir.clone();
        let state = state.clone();
        let key = key.to_string();
        tokio::spawn(async move {
            if let Err(e) = BugReport::write_pedantic(
                &dir, &key,
                &state.target_host, state.target_port,
                &state.traffic, &state.channel_snapshots,
                &state.app_snapshot, state.metrics.clone(),
            ) {
                warn!("pedantic: write failed for {}: {}", key, e);
            } else {
                info!("pedantic: wrote report for {}", key);
            }
        });
    }));
}
```

The closure captures `Arc`-wrapped handles (via a small
`PedanticContext` struct, constructed once). Exact shape
of the struct is a sub-agent concern — it's whatever the
existing F8 path holds, wrapped in `Arc` where needed for
`Send + Sync + 'static`.

### 4. Status-bar widget

In `app.rs`'s bottom-panel render (around the existing
`TopBottomPanel::bottom("stats")` at line 1276), add a
`Gaps: N` label plus a toggleable popup panel for the
key list. egui's `CollapsingHeader` or a simple
`ui.button` + conditional `egui::Window` fits.

State (on `App`):

```rust
pedantic_gap_count: usize,  // mirrors logging::warn_once_count() with UI-thread caching
pedantic_gap_keys: Vec<String>,  // same, but the snapshot
gaps_popup_open: bool,
```

Refresh the cached copies each frame (cheap — mutex +
`.len()`). Click on `Gaps: N` toggles `gaps_popup_open`.
Popup shows a scrollable list of keys, sorted.

The app also registers its own observer at startup
— even without `--pedantic` — so the counter includes
keys from any thread, not just the UI thread. The
observer's only job is to kick a `Notify` that ticks
the UI; the reading side polls the registry.

Actually simpler: don't register an observer at all.
The UI polls `warn_once_count()` once per frame
unconditionally. That's O(1) (locked `.len()` on a
hashset). No observer plumbing needed for the counter;
observers are only used by `--pedantic`.

### 5. Fall-through-arm conversions

Replace the seven remaining `logging::log_unknown(...)`
sites in channel handlers with
`logging::log_unknown_once(channel, msg_type, payload)`.
Mechanical change; each site reduces from five-argument
call to three-argument call. The "direction" parameter
was always `"received"` — log_unknown_once doesn't take
it because hex dumps only make sense for received data.

### 6. Decode-failure key conversions

In `display.rs::decode_image_and_emit` and
`handle_draw_copy`'s preamble, replace each bare
`warn!(...)` with a corresponding `warn_once!(...)`. Key
shape: `"display:<op_name>:<failure>"` matching the
STYLEGUIDE convention.

All keys use the flattened `display:decode_failure:…`
prefix (resolved question 7) so the second segment is
always the gap-category token. Concrete conversions:

| Site | Key |
|------|-----|
| `decode_image_and_emit` null src_bitmap | `display:decode_failure:{op}:null_src_bitmap` |
| payload too short for image descriptor | `display:decode_failure:{op}:short_payload_img_desc` |
| no image data | `display:decode_failure:{op}:no_image_data` |
| Pixmap format unsupported | `display:decode_failure:pixmap:format_unsupported` |
| GLZ decompression failed | `display:decode_failure:glz:decompress_failed` |
| LZ decompression failed | `display:decode_failure:lz:decompress_failed` |
| ZLIB_GLZ_RGB failures (both) | `display:decode_failure:zlib_glz:decompress_failed` |
| JPEG decode failed | `display:decode_failure:jpeg:decode_failed` |
| QUIC decode failed | `display:decode_failure:quic:decode_failed` |
| Image not in cache | `display:decode_failure:from_cache:miss` |
| Unknown image type byte | `display:decode_failure:image_type:unknown` |
| Unsupported types (LzPalette / Surface / FromCacheLossless / JpegAlpha) | `display:decode_failure:{type}:unsupported` |

Log-message strings keep their existing wording (only
the key format changes). The `op` token comes from
`decode_image_and_emit`'s `op_name` parameter so it's
already plumbed.

Note: `warn_once!` keys must be `&'static str`. For
op-parameterised keys, use `intern_key` from phase 7 so
dynamic strings can enter the registry:

```rust
logging::warn_once_impl(
    logging::intern_key(format!("display:decode_failure:{}:null_src_bitmap", op_name)),
    &format!("display: {}: null src_bitmap", op_name),
);
```

For the preamble (non-parameterised, always "draw_copy"
since it runs before `decode_image_and_emit`), the
`warn_once!` macro works directly with a literal key.

### 7. Tests

Additions:

1. `logging::register_gap_observer` — fire a test
   observer, emit a key, assert the observer saw the
   key. Use literal-name discipline per phase 2.
2. Observer late-registration replay — emit a key,
   then register an observer, assert the observer
   saw the already-present key.
3. `BugReport::write_pedantic` — call with a fake
   gap_key, confirm a zip file appears in the target
   dir with the expected name shape.
4. (Integration, manual) — run ryll against a test
   guest with `--pedantic` and confirm one zip per
   distinct gap lands in the output directory.

No new unit tests for the channel fall-through
conversions (they're mechanical `log_unknown` ↔
`log_unknown_once` swaps).

## Files touched

| File | Change |
|------|--------|
| `shakenfist-spice-protocol/src/logging.rs` | `GapObserver` type, `register_gap_observer`, `dispatch_new_gap`. Wire into `register_key`. Replay-on-register. Two new unit tests. |
| `ryll/src/bugreport.rs` | `BugReportType::Pedantic { gap_key }` variant, updated `channel_name()` dispatch, `BugReport::write_pedantic(...)` helper. One new unit test. |
| `ryll/src/config.rs` | `--pedantic` and `--pedantic-dir` flags on `Args`. |
| `ryll/src/main.rs` | If pedantic enabled: mkdir, assemble the `PedanticContext`, register the observer that spawns write tasks. |
| `ryll/src/app.rs` | Bottom-panel `Gaps: N` widget; click → collapsible panel with sorted key list. |
| `ryll/src/channels/cursor.rs`, `display.rs`, `inputs.rs`, `main_channel.rs`, `playback.rs`, `usbredir.rs`, `webdav.rs` | Convert `_ => log_unknown(...)` to `_ => log_unknown_once(...)`. |
| `ryll/src/channels/display.rs` | Convert ~12 decode-failure `warn!(...)` sites to `warn_once!(...)` per the table above. |
| `README.md` | Document `--pedantic` briefly (one paragraph; more detail in phase 9's full doc sweep). |

## Resolved questions

All nine resolved before execution.

1. **Callback observer**, not channel-based. Keeps the
   registry free of any tokio dependency; callers
   choose their own concurrency model.
2. **Replay on late registration** — yes. Matches
   `--pedantic` user intent; early-boot gaps shouldn't
   get lost just because the observer was slow to
   register.
3. **50-report cap, hardcoded**. `--pedantic-max=<n>`
   invites bikeshedding; 50 is plenty.
4. **Default dir `./ryll-pedantic-reports/`**. Explicit
   in the name, doesn't collide with F8 reports.
5. **Button styling** for the counter, not plain
   label — invites interaction.
6. **Floating `egui::Window`** for the key list —
   consistent with F8 dialog.
7. **Flatten decode-failure keys** to
   `display:decode_failure:<op>:<failure>` — keeps the
   second `:`-delimited segment consistent across all
   four categories (`unimpl`, `<op>` sub-feature,
   `decode_failure`, `hexdump`).
8. **Test observers filter by literal-name prefix** —
   matches the phase-2 parallel-test discipline.
9. **`log_unknown_once` observer dispatch is
   automatic**. It calls `warn_once_impl_if_new` which
   calls `register_key` which calls `dispatch_new_gap`.
   No extra wiring.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high   | opus   | none | In `shakenfist-spice-protocol/src/logging.rs`, add `GapObserver` type, `register_gap_observer`, and the `dispatch_new_gap` private helper. Wire `register_key` to call `dispatch_new_gap(key)` when inserting a new key. Implement replay-on-register: when `register_gap_observer` is called, snapshot the current registry, release the registry lock, then call the observer once per existing key. Never hold two locks simultaneously. Two new unit tests per "Tests" — observer-fires-on-new-key and replay-on-late-registration. Use literal-name key discipline from phase 2. Do NOT touch ryll/*. |
| 8b | medium | sonnet | none | In `ryll/src/bugreport.rs`, add `BugReportType::Pedantic { gap_key: String }` variant. Update `channel_name()` to parse the first `:`-delimited segment of `gap_key` (`display`, `cursor`, `inputs`, `main`, `usbredir`) and return the matching channel; fall back to `"display"` on unknown. Add `pub fn BugReport::write_pedantic(dir, gap_key, target_host, target_port, traffic, channel_snapshots, app_snapshot, runtime_metrics) -> anyhow::Result<PathBuf>` that wraps `assemble` + `write_zip`. One new test: calls write_pedantic with a fake gap_key, confirms a zip lands in a temp dir. |
| 8c | medium | sonnet | none | Convert every remaining `_ => logging::log_unknown(...)` fall-through arm in the channel handlers to `_ => logging::log_unknown_once(...)`. Files: `ryll/src/channels/{cursor,display,inputs,main_channel,playback,usbredir,webdav}.rs`. In display.rs this is the line 983-range arm (after the phase-7 DRAW_ROP3 / DRAW_STROKE / DRAW_TEXT / DRAW_COMPOSITE arms, which already use log_unknown_once). Do NOT change anything else. Purely mechanical. |
| 8d | high   | opus   | none | In `ryll/src/channels/display.rs`, convert every `warn!(...)` site inside `handle_draw_copy` preamble and `decode_image_and_emit` to `warn_once!(...)` per the "Decode-failure key conversions" table in this plan. All keys use the flattened `display:decode_failure:<op_or_category>:<failure>` prefix. For op-parameterised keys (those with `{op}` in the table), use `logging::warn_once_impl(logging::intern_key(format!(...)), &format!(...))` because the `warn_once!` macro only takes literal keys. Preserve the exact warn message strings (only the registry key format is new). Sites: null_src_bitmap, short payload for image descriptor, no_image_data, Pixmap format unsupported, GLZ decompress failed, LZ decompress failed, ZLIB_GLZ failures (both), JPEG decode failed, QUIC decode failed, image not in cache, unknown image type byte, LzPalette/Surface/FromCacheLossless/JpegAlpha unsupported. |
| 8e | medium | sonnet | none | Add `--pedantic` (`bool`) and `--pedantic-dir` (`PathBuf`, default `./ryll-pedantic-reports`) to `ryll/src/config.rs`'s `Args`. Wire up in `ryll/src/main.rs` (or wherever the app is constructed): if pedantic, create the directory, build a `PedanticContext` struct that Arc-wraps the handles passed to `BugReport::write_pedantic`, and call `logging::register_gap_observer` with a closure that spawns a tokio task calling `write_pedantic`. Cap at `const PEDANTIC_REPORT_CAP: usize = 50;` via an AtomicUsize counter. On write failure, log via `warn!`. Add minimal `--pedantic` mention to README.md. |
| 8f | medium | sonnet | none | In `ryll/src/app.rs`, add a `Gaps: N` button to the bottom `TopBottomPanel::bottom("stats")`. Poll `shakenfist_spice_protocol::logging::warn_once_count()` each frame. Clicking opens a floating `egui::Window` titled "Protocol gaps" listing `warn_once_keys()` sorted. Add `gaps_popup_open: bool` to `App` state. Headless mode: no-op (already no-op because the render path isn't called). Keep the widget compact — a single clickable label with red highlight when N > 0 is enough; no sparkline, no styling fanfare. |
| 8g | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`. If rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results. |

Sequencing: 8a → 8b → (8c + 8d in parallel) → (8e + 8f
in parallel) → 8g. 8a is a prerequisite for 8e
(observer hook); 8b is a prerequisite for 8e
(Pedantic variant). 8c and 8d are independent file
edits. 8e and 8f both touch different files and have
no ordering constraint after 8a+8b land.

Sequential execution is also fine if parallel Docker
contention is a problem.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All new unit tests pass; existing tests unchanged.
* With `--pedantic`, running against a guest that
  exercises a previously-unseen decode failure
  produces exactly one zip file in the pedantic
  directory per distinct gap. Repeated instances of
  the same gap do not add more zips.
* The `Gaps: N` counter is visible in the bottom
  panel. Clicking it opens a window with the sorted
  key list.
* Running without `--pedantic` produces no zips but
  the counter still works.
* Headless mode writes zips when `--pedantic` is set
  and does not attempt to render the counter.
* `--pedantic` and `--capture` can be passed
  together with no observable interaction.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
