# Phase 6: GUI live traffic viewer

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Add a toggleable side panel that displays a live feed of
recent SPICE protocol messages from the ring buffer.

At the end of this phase:

1. Pressing F11 or clicking "Traffic" in the status bar
   toggles a right-side panel.
2. The panel shows a scrollable list of recent messages
   from all four channels, merged and sorted by timestamp.
3. Each row displays: relative timestamp, colour-coded
   channel name, direction arrow, message name, and size.
4. Channel filter checkboxes let the user hide individual
   channels (e.g. suppress noisy display traffic).
5. A "Pause" button freezes the displayed data for
   inspection.
6. The list auto-scrolls to the newest entry (unless
   paused).
7. F11 is consumed by ryll and not forwarded to the SPICE
   server.

## Design

### Lightweight view entry

`TrafficEntry` contains a potentially large `pcap_frame:
Vec<u8>`.  The traffic viewer only needs the metadata
fields.  A lightweight copy-free struct avoids cloning
pcap data on every refresh:

```rust
/// Lightweight traffic entry for the viewer (no pcap frame).
#[derive(Clone)]
pub struct TrafficViewEntry {
    pub timestamp: Duration,
    pub channel: &'static str,
    pub direction: TrafficDirection,
    pub message_type: u16,
    pub message_name: &'static str,
    pub wire_size: u32,
    pub payload_size: u32,
}
```

### Collecting entries from all channels

Add a method on `TrafficBuffers` that locks each channel's
ring buffer, copies metadata from the most recent entries,
merges across channels, and returns a sorted `Vec`:

```rust
impl TrafficBuffers {
    /// Collect recent entries from all channels for the
    /// traffic viewer.  Returns at most `max` entries,
    /// sorted by timestamp (oldest first).
    pub fn recent_view_entries(
        &self,
        max: usize,
    ) -> Vec<TrafficViewEntry> {
        let mut all = Vec::new();
        for name in &CHANNELS {
            if let Some(buf) = self.buffer_for(name) {
                let guard = buf.lock().unwrap();
                for entry in guard.entries().iter().rev()
                    .take(max)
                {
                    all.push(TrafficViewEntry {
                        timestamp: entry.timestamp,
                        channel: entry.channel,
                        direction: entry.direction,
                        message_type: entry.message_type,
                        message_name: entry.message_name,
                        wire_size: entry.wire_size,
                        payload_size: entry.payload_size,
                    });
                }
            }
        }
        all.sort_by_key(|e| e.timestamp);
        if all.len() > max {
            all.drain(..all.len() - max);
        }
        all
    }
}
```

Each mutex is held only for the duration of the iteration
(microseconds).  The returned `Vec` owns no references to
the ring buffer.

### Refresh strategy

Refreshing at 60fps (every frame) would lock all four
mutexes and allocate a `Vec` every 16ms.  Instead, cache
the entries in `RyllApp` and refresh every 250ms:

```rust
/// Maximum entries shown in the traffic viewer.
const TRAFFIC_VIEWER_MAX_ENTRIES: usize = 200;

/// How often the traffic viewer refreshes from the ring
/// buffers (milliseconds).
const TRAFFIC_VIEWER_REFRESH_MS: u64 = 250;
```

In `update()`:

```rust
if self.show_traffic_viewer
    && !self.traffic_viewer_paused
    && self.traffic_viewer_last_refresh.elapsed()
        >= Duration::from_millis(TRAFFIC_VIEWER_REFRESH_MS)
{
    self.traffic_viewer_entries = self.traffic
        .recent_view_entries(TRAFFIC_VIEWER_MAX_ENTRIES);
    self.traffic_viewer_last_refresh = Instant::now();
}
```

### New state fields on RyllApp

```rust
// Traffic viewer state
show_traffic_viewer: bool,
traffic_viewer_entries: Vec<TrafficViewEntry>,
traffic_viewer_last_refresh: Instant,
traffic_viewer_paused: bool,
traffic_filter_main: bool,
traffic_filter_display: bool,
traffic_filter_inputs: bool,
traffic_filter_cursor: bool,
```

Initialised: `false`, empty vec, `Instant::now()`, `false`,
all filters `true` (show all channels by default).

### F11 keyboard shortcut

Same pattern as F12: intercept in `update()` before
`handle_input()`, toggle `show_traffic_viewer`.  In
`handle_input()`, skip forwarding F11 events to the SPICE
server.

```rust
// F11 toggles traffic viewer
if !self.region_select_active {
    let f11_pressed = ctx.input(|i| {
        i.key_pressed(egui::Key::F11)
    });
    if f11_pressed {
        self.show_traffic_viewer =
            !self.show_traffic_viewer;
    }
}
```

In `handle_input()`, add F11 to the key-skip check:

```rust
if *key == egui::Key::F12
    || *key == egui::Key::F11
{
    continue;
}
```

### "Traffic" button in the status bar

Add a `small_button("Traffic")` next to the existing
"Report" button in the right-to-left section:

```rust
if ui.small_button("Traffic").clicked() {
    self.show_traffic_viewer =
        !self.show_traffic_viewer;
}
```

### Side panel

Use `egui::SidePanel::right("traffic_viewer")` with a
default width of 350px.  Show it conditionally when
`show_traffic_viewer` is true.  The panel must be rendered
**before** the `CentralPanel` so egui reserves the space
correctly.

```rust
if self.show_traffic_viewer {
    egui::SidePanel::right("traffic_viewer")
        .default_width(350.0)
        .show(ctx, |ui| {
            self.draw_traffic_viewer(ui);
        });
}
```

### Panel content

The `draw_traffic_viewer()` method renders:

1. **Header**: title + pause button + entry count.

   ```rust
   ui.horizontal(|ui| {
       ui.heading("Traffic");
       if ui.small_button(
           if self.traffic_viewer_paused { "Resume" }
           else { "Pause" }
       ).clicked() {
           self.traffic_viewer_paused =
               !self.traffic_viewer_paused;
       }
       ui.label(format!(
           "{} messages", self.traffic_viewer_entries.len()
       ));
   });
   ```

2. **Channel filters**: horizontal row of checkboxes.

   ```rust
   ui.horizontal(|ui| {
       ui.checkbox(&mut self.traffic_filter_main, "Main");
       ui.checkbox(
           &mut self.traffic_filter_display, "Display",
       );
       ui.checkbox(
           &mut self.traffic_filter_inputs, "Inputs",
       );
       ui.checkbox(
           &mut self.traffic_filter_cursor, "Cursor",
       );
   });
   ```

3. **Scrollable message list**: `egui::ScrollArea::vertical()`
   with `stick_to_bottom(true)` (auto-scroll disabled when
   paused).

   ```rust
   let stick = !self.traffic_viewer_paused;
   egui::ScrollArea::vertical()
       .stick_to_bottom(stick)
       .show(ui, |ui| {
           // ... render rows
       });
   ```

### Row format

Each row is a horizontal layout with fixed-width columns
using `ui.monospace()` for alignment:

```
-2.3s  main     ← init           1,204
-1.8s  display  ← draw_copy     45,230
-0.5s  inputs   → key_down          12
```

Columns:
- **Timestamp**: relative to current time, right-aligned,
  format `{:>7.1}s` (e.g. "  -2.3s").
- **Channel**: left-aligned, colour-coded.
- **Direction**: `←` for received, `→` for sent.
- **Message name**: left-aligned.
- **Size**: right-aligned, comma-formatted wire_size.

Channel colours (using `egui::Color32`):
- main: `(120, 160, 255)` — light blue
- display: `(100, 200, 100)` — green
- inputs: `(255, 180, 80)` — orange
- cursor: `(200, 130, 255)` — purple

The row is filtered based on the channel checkboxes:

```rust
if !self.channel_visible(entry.channel) {
    continue;
}
```

Where `channel_visible()` checks the corresponding filter
boolean.

### Formatting helpers

Add a helper to format byte sizes with commas:

```rust
fn format_size(bytes: u32) -> String {
    if bytes >= 1_000_000 {
        format!("{:.1}M", bytes as f64 / 1_000_000.0)
    } else if bytes >= 10_000 {
        format!("{:.1}K", bytes as f64 / 1_000.0)
    } else {
        format!("{}", bytes)
    }
}
```

### Panel layout order

In egui, side panels and top/bottom panels must be shown
before CentralPanel so the layout engine can reserve space.
The traffic viewer `SidePanel::right` should be rendered
before the existing `CentralPanel`.  The existing
`TopBottomPanel::bottom` for the stats bar is currently
rendered after CentralPanel; this works in practice but is
technically incorrect.  Phase 6 will move it to render
before CentralPanel as well, to be correct.

The new rendering order in `update()`:

1. Event processing, key handling, input handling
2. `TopBottomPanel::bottom("stats")` — stats bar
3. `SidePanel::right("traffic_viewer")` — traffic panel
   (conditional)
4. `CentralPanel::default()` — surface display
5. Bug report dialog window
6. Region selection overlays
7. Cursor overlay

### Removing dead_code annotations

The `entries()` method on `TrafficRingBuffer` and the
`CHANNELS` constant both have `#[allow(dead_code)]` annotations
from Phase 1.  They are now used by
`recent_view_entries()`.  Remove the annotations.

## Steps

### Step 1: Add TrafficViewEntry and recent_view_entries()

1. Add `TrafficViewEntry` struct to `bugreport.rs`.
2. Add `TrafficBuffers::recent_view_entries()` method.
3. Remove `#[allow(dead_code)]` from `entries()` on
   `TrafficRingBuffer` and `CHANNELS`.

### Step 2: Add traffic viewer state fields to RyllApp

Add `show_traffic_viewer`, `traffic_viewer_entries`,
`traffic_viewer_last_refresh`, `traffic_viewer_paused`,
and the four `traffic_filter_*` booleans.  Initialise in
`RyllApp::new()`.

### Step 3: Add F11 key handling

1. In `update()`, intercept F11 to toggle the viewer.
2. In `handle_input()`, skip forwarding F11 events.

### Step 4: Add "Traffic" button to status bar

Add `small_button("Traffic")` next to the "Report" button
in the right-to-left section of the stats panel.

### Step 5: Add periodic refresh logic

In `update()`, before rendering the panel, refresh the
cached entries if not paused and enough time has elapsed.

### Step 6: Move stats panel before CentralPanel

Move the `TopBottomPanel::bottom("stats")` block to render
before the `CentralPanel` block.  This is the correct egui
panel ordering and is required for the side panel to
coexist without layout glitches.

### Step 7: Add traffic viewer side panel

1. Add `SidePanel::right("traffic_viewer")` before
   CentralPanel, conditional on `show_traffic_viewer`.
2. Render header with title, Pause/Resume button, entry
   count.
3. Render channel filter checkboxes.
4. Render scrollable message list with colour-coded rows.

### Step 8: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. `make test` — all tests pass.

### Step 9: Update documentation

1. Update `ARCHITECTURE.md` to describe the traffic viewer.
2. Update `AGENTS.md` to note the `TrafficViewEntry` type.
3. Update `README.md` to document the F11 shortcut and
   Traffic button.

## Administration and logistics

### Success criteria

* F11 toggles the traffic viewer panel.
* The "Traffic" button in the status bar toggles the panel.
* The panel shows merged, time-sorted entries from all
  four channels.
* Channel filter checkboxes hide/show individual channels.
* The Pause button freezes the displayed data.
* Auto-scroll sticks to the bottom when not paused.
* F11 is not forwarded to the SPICE server.
* The viewer refreshes every 250ms (not every frame).
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.

### Risks

* **Lock contention**: `recent_view_entries()` locks all
  four channel mutexes sequentially.  Each lock is held
  for O(n) where n is the entries iterated (up to 200 per
  channel).  At 4Hz refresh rate this is negligible
  contention with the channel handler tasks that push at
  message rate.

* **Memory**: 200 `TrafficViewEntry` structs are ~10 KB
  total (no pcap frames).  Negligible.

* **Panel layout**: Moving the stats TopBottomPanel before
  CentralPanel is the correct egui ordering and should not
  change visual behaviour.  If it does, the change is
  trivially reversible.

* **Sort cost**: Sorting 800 entries (200 x 4 channels)
  by timestamp at 4Hz is microseconds.  Not a concern.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
