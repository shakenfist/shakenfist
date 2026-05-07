# Phase 1: Renderer extraction (`shakenfist-spice-renderer`)

## Prompt

Before responding to questions or making changes, explore the
ryll codebase thoroughly. Read the master plan at
`docs/plans/PLAN-web-frontend.md` and the prior crate-extraction
phases (`docs/plans/PLAN-crate-extraction-phase-04-protocol.md`
and `-phase-05-usbredir.md`) for precedent. Key files for this
phase:

- `Cargo.toml` (workspace) and `ryll/Cargo.toml`
- `ryll/src/display/surface.rs` (especially the egui-coupled
  `texture()` method around line 465)
- `ryll/src/channels/inputs.rs` (especially `key_to_scancode`
  around line 886, which takes `egui::Key`)
- `ryll/src/channels/mod.rs` (the public enums: `ChannelEvent`,
  `InputEvent`, `CursorImage`, `UsbCommand`, `WebdavCommand`)
- `ryll/src/app.rs` (especially `run_connection` ~line 3084 and
  `run_headless` ~line 3370 — both must move to the renderer
  crate; the rest of `RyllApp` stays in `ryll`)

Pattern-match against `shakenfist-spice-protocol/`,
`shakenfist-spice-compression/`, and `shakenfist-spice-usbredir/`
for crate skeleton and dependency style. The dual-spec
`{ path = "..", version = ".." }` Cargo pattern is the convention.

Flag any uncertainty rather than guessing.

## Goal

Extract a new `shakenfist-spice-renderer` library crate from the
`ryll` binary crate. After this phase:

- `shakenfist-spice-renderer` contains `DisplaySurface` (the pixel
  buffer + draw-op API), all SPICE channel handlers, the input /
  cursor / channel-event types, and the `run_connection` / 
  `run_headless` session orchestrator.
- `ryll` becomes a thin GUI / CLI binary that depends on the new
  renderer crate the same way it already depends on
  `shakenfist-spice-protocol` etc.
- The `--web` mode added in later phases will join as a third
  consumer of the renderer crate, alongside GUI and headless.
- All existing tests pass; GUI and headless modes continue to
  work unchanged on Linux.

No new functionality is added in this phase. No web-facing code
yet. This is pure refactor.

## Scope

In:

- Two in-place API refactors that decouple substrate from `egui`
  (steps 1a, 1b). These must land before file movement, because
  the renderer crate cannot depend on `egui`.
- New crate skeleton `shakenfist-spice-renderer/` with its own
  `Cargo.toml` and an empty `lib.rs` (step 1c).
- File moves: `display/`, `channels/`, and the
  `run_connection`/`run_headless` orchestrator from `app.rs`
  (steps 1d, 1e).
- Updating `ryll/Cargo.toml` and the workspace `Cargo.toml`.
- Updating `use` statements throughout `ryll/src/` to reference
  the new crate.

Out:

- Reserving the crate name on crates.io. That is a separate
  pre-step the operator handles before publishing (analogous to
  `PLAN-crate-extraction-phase-02-reserve-names.md`). For
  development the dual-spec dep with a `path` works without
  publication.
- Any feature gating (`--gui` / `--web` cargo features). Master
  plan defers feature gating to future work.
- Writing the parity audit (Phase 0). Phases 0 and 1 are
  independent; either may run first.
- Web-specific code. That starts in Phase 2.

## Approach

### Refactor 1: `DisplaySurface` pixel/texture split (step 1b)

`display/surface.rs:465` exposes
`pub fn texture(&mut self, ctx: &Context) -> &TextureHandle`,
which calls `ctx.load_texture()` and `tex.set()`. This couples
the pixel substrate to `egui::Context`. Two viable shapes:

- **(a) Trait abstraction.** Define a `TextureSink` trait in the
  renderer crate; the GUI provides an egui-backed impl. Renderer
  doesn't know about textures, just hands the dirty rectangle to
  whatever sink the consumer registers.
- **(b) Move the egui binding to `ryll`.** Renderer exposes only
  raw pixels + dirty flag (`pixels(&self) -> &[u8]`,
  `is_dirty(&self) -> bool`, `consume_dirty(&mut self)`). The
  GUI in `ryll` wraps a `DisplaySurface` and maintains its own
  egui `TextureHandle` cache.

**Choose (b).** It is mechanically simpler, removes the trait
indirection, and matches what every consumer actually does (each
frontend is going to build its own representation — egui texture
for GUI, openh264 input for the encoder, datachannel cursor for
web). Trait abstraction is over-engineering for three concrete
consumers.

The change in step 1b:

1. Delete `texture()` from `DisplaySurface`.
2. Add a `consume_dirty(&mut self) -> bool` that returns the
   dirty bit and clears it (the existing `is_dirty()` stays read-only).
3. In `app.rs`, introduce a small `GuiSurface` wrapper (probably
   in a new `ryll/src/display_gui.rs`) that owns an
   `Option<TextureHandle>` and a reference (or owned wrapper)
   around `DisplaySurface`, and exposes
   `texture(&mut self, ctx: &Context)` that builds/refreshes the
   texture from `pixels()` when `consume_dirty()` returns true.
4. Update all GUI call sites (the egui paint loop) to use
   `GuiSurface::texture()` instead of
   `DisplaySurface::texture()`.

After this commit, `DisplaySurface` has no `egui` references and
`display/surface.rs` has no `eframe::egui` import.

### Refactor 2: scancode mapping (step 1a)

`channels/inputs.rs:886` exposes
`pub fn key_to_scancode(key: egui::Key) -> Option<(u32, bool)>`.
The function body is a giant `match` on `egui::Key` variants
mapping each to an AT scancode.

Replace with:

1. A neutral lookup function in the renderer-bound code:
   `pub fn scancode_for_logical_key(key: LogicalKey) -> Option<(u32, bool)>`,
   where `LogicalKey` is a small enum the renderer owns
   (`Letter('A'..'Z')`, `Digit(0..9)`, `Function(F1..F12)`,
   `Arrow(Up/Down/Left/Right)`, `Modifier(...)`, `Special(...)`).
2. An adapter function in `ryll` (probably in a new
   `ryll/src/input_egui.rs`) that takes `egui::Key` and returns
   `Option<LogicalKey>`. The adapter is then composed with
   `scancode_for_logical_key()` at the GUI call sites.
3. The web frontend (Phase 5) will provide a *different* adapter
   that takes a JS `KeyboardEvent.code` string (over the
   datachannel) and returns `Option<LogicalKey>` — same neutral
   pivot, two adapters.

This is more value than just stripping the `egui::Key` parameter
because it gives both GUI and web a typed neutral midpoint
instead of stringly-typed scancode plumbing.

After this commit, `channels/inputs.rs` has no `eframe::egui`
import.

### Refactor 3: ryll-side coupling indirection (step 1d′)

The original codebase research correctly identified that channel
code is decoupled from `egui`. It missed a second class of
coupling: channel handlers reach into seven non-egui ryll-side
modules — `ByteCounter` (in `app.rs`), `TrafficBuffers` and
snapshot types (in `bugreport.rs`), `CaptureSession` (in
`capture.rs`), `SharedNotifications` (in `notifications.rs`),
`settings::is_verbose()` flag, the USB device backends in
`ryll/src/usb/`, and the WebDAV server implementation in
`ryll/src/webdav/`. Each channel file has 11–22 such coupling
sites: constructor parameters, struct fields, hot-path calls.

**Publication intent.** The renderer crate will be published to
crates.io alongside `shakenfist-spice-{protocol, compression,
usbredir}` (which are at 0.1.4). That sets the cleanliness bar:
the renderer's API must be usable by a third party who does not
want ryll's bug-report ring buffer, capture pipeline, USB host
backends, or notification store. We invest in indirection now
so the published API is a substrate library, not a kitchen sink.

Step 1d′ introduces the indirection scheme **before** any file
movement. After 1d′, the channel files (still in `ryll/src/` at
this point) consume the renderer's trait surface for everything
ryll-side, and step 1d becomes the mechanical move it was
originally scoped to be.

The trait surface (defined in the renderer crate during 1d′,
even though channel files are still in ryll):

| Concern | Mechanism | Where defined | Where implemented |
|---|---|---|---|
| Per-channel byte/packet counts | move `ByteCounter` to renderer; channels store `Arc<ByteCounter>` directly (it is pure plumbing) | `shakenfist-spice-renderer/src/byte_counter.rs` | n/a (concrete) |
| Logging gate (`is_verbose` flag) | `LogConfig` value passed by const-ref or by clone into channel constructors | renderer | ryll constructs and passes in |
| Metrics counters | move `metrics` types to renderer | renderer | n/a |
| Notifications | `ChannelEvent::Notification(NotificationEntry)` variants emitted via the existing event channel; ryll's notification store observes the event stream | renderer (move `NotificationEntry`/`NotificationSource` data types; keep the store in ryll) | ryll's `NotificationStore` |
| Traffic ring buffers (raw protocol bytes for bug reports) | `TrafficSink` trait passed to channels as `Arc<dyn TrafficSink>` | renderer | ryll's `TrafficBuffers` (in `bugreport.rs`) implements it |
| Capture sink (pcap + video) | `CaptureSink` trait passed as `Arc<dyn CaptureSink>` | renderer | ryll's `CaptureSession` (in `capture.rs`) implements it |
| USB host backend (real devices, virtual MSC) | `UsbBackend` trait + concrete-config types (`VirtualDiskConfig`) move to renderer | renderer | ryll's `RealDevice`, `VirtualMsc` impls register at startup |
| WebDAV server | `WebdavBackend` trait + concrete-config types (`ShareDirConfig`) move to renderer | renderer | ryll's `MuxDemuxer` + `WebdavServer` impls register at startup |
| Channel-state snapshot types (`*Snapshot`, `CursorCacheEntry`, `InputEventRecord`, `DecodeResult`) | move from `bugreport.rs` to renderer (these describe channel state, not bug-report packaging) | renderer | n/a |

What stays in `ryll`:

- `bugreport.rs` (the bug-report ZIP writer and the
  `TrafficBuffers` ring buffer that implements `TrafficSink`).
- `capture.rs` (the `CaptureSession` that owns pcap + MP4 sinks
  and implements `CaptureSink`).
- `notifications.rs` (the `NotificationStore` that observes
  `ChannelEvent::Notification`).
- `app.rs`, `main.rs`, `display_gui.rs`, `input_egui.rs`.

What moves to the renderer in 1d′ (smaller, ahead of the big
1d move):

- `ByteCounter` (extract from `app.rs`).
- `metrics` module (small).
- `settings` module → reshape into a `LogConfig` value type
  rather than a globals-style module.
- The channel-state snapshot types from `bugreport.rs`.
- `usb/` directory (the SPICE-protocol-aligned device backends).
- `webdav/` directory (the SPICE port-channel mux + server).
- `VirtualDiskConfig`, `ShareDirConfig` from `config.rs` (or
  the protocol-relevant subset).
- New trait definitions: `TrafficSink`, `CaptureSink`,
  `UsbBackend`, `WebdavBackend`.
- New `NotificationEntry` and `NotificationSource` data types
  (moved from `notifications.rs`; the *store* stays in ryll).
- New `ChannelEvent::Notification(NotificationEntry)` variants
  (and any related `NotificationCleared` / `NotificationUpdated`
  variants that the existing notification flow needs).

Channel files in `ryll/src/channels/` are edited in place during
1d′: their constructors take new trait-object parameters; their
hot-path calls go through the trait methods or emit
`ChannelEvent::Notification` variants. The files do **not** move
in 1d′ — that is reserved for 1d.

After 1d′, `ryll/src/channels/*.rs` and `ryll/src/display/*.rs`
have **zero** `use crate::{app, bugreport, capture, notifications,
settings, config, usb, webdav}` references. They consume
renderer-side types and traits exclusively. 1d then becomes a
git mv + use-rewrite commit.

### Crate skeleton (step 1c)

Create `shakenfist-spice-renderer/` next to the other extracted
crates. Files:

- `Cargo.toml` — version `version.workspace = true`, edition
  matching the workspace, dependencies copied from the
  "definitely move" set in the codebase research:
  `tokio` (full), `tokio-rustls`, `rustls-pemfile`,
  `webpki-roots`, `bytes`, `byteorder`, `rsa`, `sha1`, `rand`,
  `cpal`, `opus-decoder`, `rtrb`, `socket2`, `nusb`,
  `lz4_flex`, `tracing`, `anyhow`, `thiserror`, `async-channel`,
  `serde`, `serde_json`, plus path-deps on the existing
  `shakenfist-spice-{protocol,compression,usbredir}` crates.
- `src/lib.rs` — initially empty (just `// shakenfist-spice-renderer`).

Add the new member to the workspace `Cargo.toml` member list.
Add a `path + version` dep entry in `ryll/Cargo.toml`. Confirm
`cargo build --workspace` succeeds. No code moves yet.

### File moves (steps 1d, 1e)

**Step 1d** — move `ryll/src/display/` and `ryll/src/channels/`
to `shakenfist-spice-renderer/src/display/` and `.../channels/`.
Re-export the public API from `lib.rs`:

```rust
pub mod channels;
pub mod display;

pub use channels::{
    ChannelEvent, CursorImage, InputEvent, LogicalKey,
    UsbCommand, WebdavCommand,
};
pub use display::DisplaySurface;
```

Update `use` statements throughout `ryll/src/`:

- `use crate::channels::*` → `use shakenfist_spice_renderer::*`
- `use crate::display::*` → `use shakenfist_spice_renderer::*`
- Internal references inside the moved modules
  (`use crate::*`) need to be rewritten to either
  `use crate::*` (referring to the new crate root) or absolute
  paths into other extracted crates.

**Step 1e** — extract `run_connection()` and `run_headless()`
from `app.rs` into `shakenfist-spice-renderer/src/session.rs`.
The signatures are already clean (they consume channel
senders/receivers and `Arc<AtomicBool>` cancel flags, none of
which are GUI types). Re-export them:

```rust
pub mod session;
pub use session::{run_connection, run_headless};
```

`ryll/src/main.rs` updates: `app::run_headless` →
`shakenfist_spice_renderer::run_headless`. `ryll/src/app.rs`
updates: `RyllApp::reconnect()` calls
`shakenfist_spice_renderer::run_connection` instead of the
local function. The `RyllApp` struct itself, the `eframe::App`
impl, and the GUI event loop stay in `ryll`.

## Prerequisites

- Confirm the master plan (`PLAN-web-frontend.md`) is committed
  on `thought-bubble`. (It is, as of commit `4d8f9443`.)
- **Publication intent.** The renderer crate ships to crates.io
  alongside `shakenfist-spice-{protocol, compression, usbredir}`
  (currently 0.1.4, 27 downloads on protocol). This sets the
  cleanliness bar that motivates step 1d′: the published API
  must be usable by a third-party SPICE client implementor who
  does not want ryll's bug-report ZIP writer, capture pipeline,
  USB host backends, or notification store. Reserve the name
  on crates.io before publishing the first release that
  contains it; treat the reservation as a separate one-line
  task outside this phase, following the precedent of
  `PLAN-crate-extraction-phase-02-reserve-names.md`.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none | Refactor `key_to_scancode(egui::Key)` in `ryll/src/channels/inputs.rs:886`. Introduce a `LogicalKey` enum in `inputs.rs` (or a new sibling `keys.rs`) covering Letters, Digits, Function keys F1–F12, Arrows, modifiers, and the navigation/control keys currently in the match. Add `scancode_for_logical_key(LogicalKey) -> Option<(u32, bool)>` containing the existing scancode table. Add an adapter `egui_key_to_logical(egui::Key) -> Option<LogicalKey>` in a new `ryll/src/input_egui.rs`. Update all callers in `app.rs` to compose the two. Delete the old `key_to_scancode` from `inputs.rs`. Verify `inputs.rs` no longer imports `eframe::egui`. Run `cargo test --workspace` and `pre-commit run --all-files`. Single commit. |
| 1b   | high   | opus  | worktree | Refactor `DisplaySurface::texture(ctx: &egui::Context)` in `ryll/src/display/surface.rs:465`. Delete the method. Add `consume_dirty(&mut self) -> bool` (returns the dirty bit and clears it; existing `is_dirty()` stays). Create `ryll/src/display_gui.rs` with a `GuiSurface` wrapper that owns the `DisplaySurface` plus an `Option<TextureHandle>`, and exposes `texture(&mut self, ctx: &egui::Context) -> &TextureHandle` reading `pixels()` and `consume_dirty()` from the inner surface. Update `app.rs` paint loop call sites — search for `.texture(ctx` to find them — to use `GuiSurface` instead. The `RyllApp` `surfaces` HashMap likely needs to change from `HashMap<(u8,u32), DisplaySurface>` to `HashMap<(u8,u32), GuiSurface>`. Verify `surface.rs` no longer imports `eframe::egui`. Run cargo tests + pre-commit. Worktree because this restructures the GUI's main paint hot path; if the wrapper turns out wrong we want to throw it away cleanly. Single commit. |
| 1c   | low    | sonnet | none | Create the `shakenfist-spice-renderer` crate skeleton. Add `shakenfist-spice-renderer/Cargo.toml` (mirror the structure of `shakenfist-spice-protocol/Cargo.toml`, deps listed in the master plan's Phase 1 Approach: `tokio`, `tokio-rustls`, `rustls-pemfile`, `webpki-roots`, `bytes`, `byteorder`, `rsa`, `sha1`, `rand`, `cpal`, `opus-decoder`, `rtrb`, `socket2`, `nusb`, `lz4_flex`, `tracing`, `anyhow`, `thiserror`, `async-channel`, `serde`, `serde_json`, plus `path + version` deps on the three existing extracted crates). Add `shakenfist-spice-renderer/src/lib.rs` with just a doc comment. Add the crate to the workspace `Cargo.toml` `members` list. Add a `path + version` dep entry in `ryll/Cargo.toml`. Run `cargo build --workspace` and `pre-commit run --all-files`. No code moves yet. Single commit. |
| 1d′  | high   | opus  | worktree | Introduce the trait/event indirection scheme described in Approach §"Refactor 3". In the renderer crate (still without channel/display files): define `TrafficSink`, `CaptureSink`, `UsbBackend`, `WebdavBackend` traits; define `LogConfig`, `NotificationEntry`, `NotificationSource` data types; add a `ChannelEvent::Notification(NotificationEntry)` variant (plus any related variants the existing notification flow needs). Move `ByteCounter` from `app.rs`, `metrics` module, `settings` module (reshape to `LogConfig`), the channel-state snapshot types from `bugreport.rs`, the `usb/` directory, the `webdav/` directory, and `VirtualDiskConfig`/`ShareDirConfig` from `config.rs` into the renderer. In `ryll/src/`, implement `TrafficSink` for `TrafficBuffers`, `CaptureSink` for `CaptureSession`, `UsbBackend` for the existing real/virtual_msc impls, `WebdavBackend` for the existing mux+server impls. Edit channel constructors in `ryll/src/channels/*.rs` to take the new trait objects + `LogConfig` instead of reaching into `crate::*` ryll-side modules. Replace direct notification-store calls with `ChannelEvent::Notification(...)` emissions; have ryll's notification store observe the event stream. The channel files stay in `ryll/src/channels/` — no file moves yet. After this commit: `grep -r "crate::\(app\|bugreport\|capture\|notifications\|settings\|config\|usb\|webdav\)" ryll/src/channels/ ryll/src/display/` returns nothing. Worktree (large diff, easy to abandon). Single commit. |
| 1d   | medium | sonnet | worktree | After 1d′ has landed, move `ryll/src/channels/` and `ryll/src/display/` directory trees into `shakenfist-spice-renderer/src/`. Update `shakenfist-spice-renderer/src/lib.rs` with `pub mod channels; pub mod display;` and re-exports for `ChannelEvent`, `CursorImage`, `InputEvent`, `LogicalKey`, `UsbCommand`, `WebdavCommand`, `DisplaySurface`, `scancode_for_logical_key`. The trait surface from 1d′ should already mean the moved modules consume only renderer-internal `use crate::...` paths, so the move is mechanical. In `ryll/src/`, replace every `use crate::channels::...` with `use shakenfist_spice_renderer::...`, same for `crate::display::`. Update `ryll/src/main.rs` to delete the `mod channels;` and `mod display;` lines. If 1d′ did its job correctly, no new indirection is needed in 1d — if you find any ryll-side coupling that 1d′ missed, pause and report. Worktree. Single commit. |
| 1e   | high   | opus  | worktree | Extract `run_connection()` (around `app.rs:3084`) and `run_headless()` (around `app.rs:3370`) and their helper functions from `ryll/src/app.rs` into a new `shakenfist-spice-renderer/src/session.rs`. Re-export from the renderer's `lib.rs`. Update `ryll/src/main.rs` (around line 164) to call `shakenfist_spice_renderer::run_headless` instead of `app::run_headless`. Update `ryll/src/app.rs::reconnect()` (around line 711) to call `shakenfist_spice_renderer::run_connection` instead of the local function. The `RyllApp` struct, `eframe::App` impl, GUI event loop, and side-panel handling stay in `app.rs`. **Be careful**: any helper functions called by `run_connection`/`run_headless` need to move with them; any helpers called by both `run_connection` and the GUI event loop need to stay in `app.rs` and be referenced from the renderer (likely via callback or by being moved entirely to the renderer if they're substrate). The signatures of `run_connection`/`run_headless` should stay identical so call sites only change the path. Run cargo tests + pre-commit. Both `cargo run -p ryll -- --headless ...` and `cargo run -p ryll` (GUI) should still work end-to-end. Worktree. Single commit. |

After 1e, the `ryll` crate's `src/` should contain roughly:
`main.rs`, `app.rs` (GUI only, plus thin trait impls),
`config.rs` (the CLI-shaped fields that didn't move),
`notifications.rs` (the store; the data types are in the
renderer), `bugreport.rs` (the ZIP writer + `TrafficBuffers`
implementing `TrafficSink`), `capture.rs` (`CaptureSession`
implementing `CaptureSink`), `display_gui.rs`, `input_egui.rs`,
plus thin `UsbBackend` and `WebdavBackend` impl wrappers
around what used to live in `ryll/src/usb/` and
`ryll/src/webdav/` (those directories themselves now live in
the renderer crate). Everything else is renderer.

## Step details

### Step 1a expanded brief

The current `key_to_scancode` is a flat match on `egui::Key`
variants returning `Option<(u32, bool)>` where the bool is the
"shift required" flag. The new structure keeps the same return
type but pivots through `LogicalKey`.

Suggested `LogicalKey` variants (verify against the existing
match arms — only emit variants for keys that *appear* in the
existing match; do not invent new ones):

```rust
pub enum LogicalKey {
    Letter(char),         // 'A'..='Z'
    Digit(u8),            // 0..=9
    Function(u8),         // 1..=12 (F1..F12)
    Arrow(Direction),     // Up/Down/Left/Right
    Modifier(Modifier),   // Shift/Ctrl/Alt/Meta + L/R variants if present
    Navigation(NavKey),   // Home/End/PageUp/PageDown/Insert/Delete
    Whitespace(WSKey),    // Space/Tab/Enter/Backspace
    Escape,
}
```

Keep the existing scancode values verbatim; this is a pivot, not
a rewrite of the table.

### Step 1b expanded brief

The texture-binding refactor is the most disruptive of the three
in-place changes because it touches the GUI paint hot path. Read
the existing `texture()` body (`surface.rs:465`–~489) carefully
before writing the wrapper — it caches a `TextureHandle` and only
calls `tex.set()` when dirty. The wrapper must preserve this
caching behaviour or the GUI will allocate a new texture every
frame.

Suggested `GuiSurface` shape:

```rust
pub struct GuiSurface {
    inner: DisplaySurface,
    texture: Option<TextureHandle>,
}

impl GuiSurface {
    pub fn new(id: u32, width: u32, height: u32) -> Self { ... }

    pub fn surface(&self) -> &DisplaySurface { &self.inner }
    pub fn surface_mut(&mut self) -> &mut DisplaySurface { &mut self.inner }

    pub fn texture(&mut self, ctx: &egui::Context) -> &TextureHandle {
        let dirty = self.inner.consume_dirty();
        match (self.texture.as_mut(), dirty) {
            (None, _) => {
                // initial allocation
                let img = ColorImage::from_rgba_unmultiplied(
                    [self.inner.size().0 as usize, self.inner.size().1 as usize],
                    self.inner.pixels(),
                );
                self.texture = Some(ctx.load_texture(
                    format!("surface-{}", self.inner.id()),
                    img,
                    TextureOptions { magnification: TextureFilter::Nearest, ..Default::default() },
                ));
            }
            (Some(_tex), true) => {
                let img = ColorImage::from_rgba_unmultiplied(...);
                self.texture.as_mut().unwrap().set(img, TextureOptions { ... });
            }
            (Some(_), false) => { /* reuse existing texture */ }
        }
        self.texture.as_ref().unwrap()
    }
}
```

The `RyllApp::surfaces` HashMap value type needs to change from
`DisplaySurface` to `GuiSurface`. Search for every place that
calls a method on a surface from `surfaces.get_mut(...)` and
verify it routes through `surface_mut()` or moves to a
`GuiSurface` method.

### Step 1d′ expanded brief

This is the largest commit in Phase 1 — likely 1200–1800 lines
of churn across the channel files plus new trait/data-type
definitions in the renderer. Do it in a worktree.

Suggested order of operations:

1. **Renderer-side scaffolding first.** Define
   `TrafficSink`, `CaptureSink`, `UsbBackend`, `WebdavBackend`
   in new modules under `shakenfist-spice-renderer/src/`. Define
   `LogConfig`, `NotificationEntry`, `NotificationSource` data
   types. Re-export from `lib.rs`. The trait method signatures
   should match what channel code currently calls on the
   ryll-side modules — read each call site and design the
   trait surface from those usages. Examples (verify against
   the actual code):
   ```rust
   pub trait TrafficSink: Send + Sync {
       fn record_sent(&self, channel: &str, bytes: &[u8]);
       fn record_received(&self, channel: &str, bytes: &[u8]);
   }
   pub trait CaptureSink: Send + Sync {
       fn packet_sent(&self, channel: &str, bytes: &[u8]);
       fn packet_received(&self, channel: &str, bytes: &[u8]);
       fn frame(&self, surface_id: u32, pixels: &[u8], w: u32, h: u32);
   }
   ```
2. **Move the substrate-shaped modules.** `git mv` for `usb/`,
   `webdav/`, `metrics`. Extract `ByteCounter` from `app.rs`
   into a new renderer-side `byte_counter.rs`. Reshape
   `settings.rs` into a `LogConfig` value type owned by the
   renderer. Move `VirtualDiskConfig`, `ShareDirConfig` from
   `config.rs` (decide what to do with the rest of `Config` —
   probably it stays in ryll as it's CLI-shaped). Move the
   channel-state snapshot types (`*Snapshot`, `CursorCacheEntry`,
   `InputEventRecord`, `DecodeResult`) out of `bugreport.rs`
   into the renderer (they describe channel state). `bugreport.rs`
   keeps the ZIP writer and the `TrafficBuffers` ring buffer.
3. **Add `ChannelEvent::Notification` variants.** Use the
   existing notification flow as the spec — every place
   ryll-side notification code currently observes (gap warnings,
   SPICE_MSG_NOTIFY, channel-status changes) needs an event
   variant. Probably one
   `ChannelEvent::Notification(NotificationEntry)` covers it,
   but check the existing `register_gap_notification_observer`
   path to be sure.
4. **Refactor channel constructors.** Each channel struct in
   `ryll/src/channels/*.rs` gains parameters of the form:
   ```rust
   pub fn new(
       // existing params...
       traffic: Arc<dyn TrafficSink>,
       capture: Arc<dyn CaptureSink>,
       log: LogConfig,
       // for usbredir:
       usb_backend: Arc<dyn UsbBackend>,
       // for webdav:
       webdav_backend: Arc<dyn WebdavBackend>,
   ) -> Self
   ```
   Internal hot-path calls (`self.byte_counter.add(...)`,
   `traffic.push_sent(...)`, `if settings::is_verbose() {...}`,
   `notifications.lock().push(...)`) become trait calls or
   `event_tx.send(ChannelEvent::Notification(...))`. The
   `ByteCounter` is part of `TrafficSink`'s contract or a
   separate field — either is fine; whichever needs less
   plumbing.
5. **Implement the traits in `ryll/src/`.** `bugreport.rs` adds
   `impl TrafficSink for TrafficBuffers`. `capture.rs` adds
   `impl CaptureSink for CaptureSession`. `usb/mod.rs` adds
   `impl UsbBackend for ...` (probably a new wrapper struct
   that delegates to the existing real/virtual_msc impls).
   Same for webdav.
6. **Wire up at session start.** `app.rs::reconnect()` (and
   the headless path in `app.rs::run_headless()`) now construct
   the trait objects and pass them into `run_connection`.
   `run_connection` passes them into the channel constructors.
7. **Notification observer.** ryll's notification store, which
   today is populated by direct `notifications::push(...)`
   calls inside channels, now observes
   `ChannelEvent::Notification` from the event channel that
   `app.rs::process_events()` already drains.
8. **Verify** `grep -r "crate::\(app\|bugreport\|capture\|notifications\|settings\|config\|usb\|webdav\)" ryll/src/channels/ ryll/src/display/`
   returns nothing.
9. `make build`, `make lint`, `make test`, `pre-commit run --all-files`.
10. Single commit.

**Hot-path concern:** trait dispatch on `Arc<dyn TrafficSink>`
adds a vtable indirection per protocol byte. If profiling shows
this is meaningful, the contingency is to fall back to a typed
`TrafficBuffers` parameter generic over a `T: TrafficSink` —
monomorphisation eliminates the vtable. For MVP, keep it simple
with `dyn` and revisit if needed. Don't pre-optimise.

**Do not move channel/display files in 1d′.** That is reserved
for 1d. Keeping the files in place during 1d′ keeps the commit
diff focused on the indirection itself rather than the
relocation.

**If you find more coupling than the table in Approach §"Refactor 3"
documents**, pause and report rather than improvising a bigger
trait surface. The table was built from the previous
sub-agent's discovery; a third-pass discovery would be a
planning bug worth surfacing.

### Step 1d expanded brief

After 1d′ has landed, this step is a near-mechanical move.

Order of operations:

1. `git mv ryll/src/channels shakenfist-spice-renderer/src/channels`
2. `git mv ryll/src/display shakenfist-spice-renderer/src/display`
3. Update `shakenfist-spice-renderer/src/lib.rs`:
   ```rust
   pub mod channels;
   pub mod display;
   pub use channels::{
       ChannelEvent, CursorImage, InputEvent, LogicalKey,
       UsbCommand, WebdavCommand,
       inputs::scancode_for_logical_key,
       // plus anything else ryll/src/ currently imports from channels
   };
   pub use display::DisplaySurface;
   ```
4. Inside the moved files, `use crate::*` paths resolve relative
   to the renderer crate root. The 1d′ commit should mean all
   `use crate::{app, bugreport, capture, notifications, settings,
   config, usb, webdav}` references are gone; if any remain,
   pause — that's evidence 1d′ missed coupling.
5. In every remaining `ryll/src/*.rs`, replace
   `use crate::channels::...` with
   `use shakenfist_spice_renderer::...`, same for `crate::display::`.
6. Delete `mod channels;` and `mod display;` from
   `ryll/src/main.rs`.
7. `make build`, `make lint`, `make test`, `pre-commit run --all-files`.
8. Single commit.

If `make build` produces any error of the form
`unresolved import \`crate::xxx\`` from inside the moved files,
do **not** paper over it by adding a back-dep on `ryll`. Stop
and report — this means 1d′ left a coupling site behind.

### Step 1e expanded brief

`run_connection` is the function `RyllApp::reconnect()` spawns
on a fresh tokio runtime in a new thread (around `app.rs:734`).
Its signature roughly takes:

- A `Config` (or its loaded equivalent)
- An `Arc<AtomicBool>` cancel flag
- `mpsc` senders for `ChannelEvent`s (one per channel type)
- `mpsc` receivers for `InputEvent`, `UsbCommand`,
  `WebdavCommand`, monitor-resize events
- An `Arc<Notify>` for repaint
- Audio output handles (cpal stream)

All of these are either renderer-owned types or generic
sync primitives. None are `eframe`/`egui` types. Verify before
moving — if a parameter type is GUI-side, that's a refactor to
do first.

`run_headless` is similar but constructs null receivers/senders
internally. Both should be `pub async fn` in
`shakenfist-spice-renderer/src/session.rs` and re-exported from
`lib.rs`.

The audio side is worth attention: `cpal::Stream` ownership in
the GUI today might live on the `RyllApp`. If so, decide whether
the renderer constructs the stream and hands a handle back, or
whether the consumer constructs the stream and hands it in. The
latter is cleaner (renderer doesn't depend on cpal output device
management) — pass in an `rtrb::Producer<i16>` and let the
consumer wire that up to its own audio sink. If today's code
already does it that way, great; if not, this is a small
additional refactor inside step 1e.

## Acceptance criteria

- `pre-commit run --all-files` passes after each of 1a, 1b, 1c,
  1d, 1e.
- `cargo test --workspace` passes after each step.
- `cargo build --workspace` produces both the `ryll` binary and
  a `libshakenfist_spice_renderer.rlib`.
- `ryll --headless <vv-file>` connects, runs cadence, and exits
  cleanly — equivalent to the current behaviour.
- `ryll <vv-file>` (GUI) connects, displays the desktop, accepts
  keyboard/mouse input, and reconnects on disconnect —
  equivalent to current behaviour.
- `shakenfist-spice-renderer/src/` contains zero `egui` /
  `eframe` references (`grep -r "egui\|eframe" shakenfist-spice-renderer/src/`
  returns nothing).
- Each of 1a–1e is a single commit on `thought-bubble` with a
  message that follows project conventions (operator's
  `~/.claude/CLAUDE.md`: 50-char first line ending in a period,
  75-char wrap, Prompt paragraph, Signed-off-by, Co-Authored-By
  with model+context+effort).

## Risks

- **Hidden coupling between channel code and ryll-side
  modules** (notifications, bug-report ring buffer, metrics).
  Step 1d may discover that channels emit notifications by
  direct module call rather than via `ChannelEvent`. If so,
  introduce a `ChannelEvent` variant first (commit), then do
  the move (commit). Worktree isolation makes the abort cheap.
- **`cpal::Stream` ownership location.** If the audio output
  stream is owned on the GUI side and the renderer expects to
  hand pre-decoded PCM upward, this is fine. If it's owned
  inside the channel code today, step 1e will need to extract
  the stream construction into the consumer. Read
  `playback.rs` early to know which world we're in.
- **`thought-bubble` branch divergence from `main`.** The
  master plan was committed on `thought-bubble`; if
  `main` advances during this phase, rebase before each step
  rather than at the end. Five commits will rebase cleanly;
  one giant commit will not.
- **`include_bytes!` of GUI assets in moved files.** Codebase
  research found none, but a fresh grep before step 1d is
  cheap insurance.
- **eframe shutdown semantics.** The GUI uses
  `SHUTDOWN_REQUESTED` `AtomicBool` to coordinate eframe exit
  and channel cancellation. After 1e the renderer-side
  `run_connection` keeps the cancel flag; the GUI-side eframe
  shutdown writes to that flag. The contract is unchanged but
  worth verifying after the move.

## Documentation updates

After step 1e, update:

- `ARCHITECTURE.md` — note the new crate, its responsibilities,
  and the relationship between the renderer and the three
  frontends (GUI, headless, planned web). Likely a new section
  or a refactored "Crate organisation" subsection.
- `AGENTS.md` — add the renderer to the crate list / build
  notes if a list exists.
- `README.md` — only if it mentions the crate structure. The
  user-facing description is unchanged.
- `docs/plans/PLAN-web-frontend.md` — flip the Phase 1 row in
  the Execution table from *Not written* to *In progress* when
  starting, *Complete* when done.
- `docs/plans/index.md` — flip the Web frontend status from
  *Proposed* to *In progress* on first phase commit.

These doc updates can be batched into the step 1e commit, or
done as a follow-up commit on top of 1e — operator's
preference.

## Back brief

Before executing 1a, the implementing agent should back-brief:
which files will change in 1a, what `LogicalKey` variants will
exist, and what the call-site update pattern will look like. Do
not start editing without the back-brief.

Subsequent steps (1b–1e) follow the same pattern: back-brief
first, edit second.

## Estimated total scope

Roughly 4,000–5,500 lines of churn across **six** commits
(1a, 1b, 1c, 1d′, 1d, 1e), revised upward from the original
estimate after step 1d execution discovered that channel
handlers couple to seven non-egui ryll-side modules in
addition to egui. The genuinely new code is concentrated in
1d′ (trait surface, `LogConfig`, notification data types,
implementations of the traits in ryll, and the constructor
plumbing) — likely 1,500–2,000 of the total churn lines. 1d
itself becomes a near-mechanical commit once 1d′ has landed.
