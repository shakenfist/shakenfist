# Phase 5: GUI display region selection

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

When the user generates a Display bug report, insert a
region selection step between the dialog and the report
generation.  The user drags a rectangle over the area of
corruption; the coordinates are recorded in the report
metadata.  The user can skip selection to capture without
a highlighted region.

At the end of this phase:

1. Clicking "Capture" with Display selected closes the
   dialog and enters region selection mode.
2. A translucent instruction banner appears at the top of
   the surface: "Click and drag to select the affected
   region.  Press Escape to skip."
3. The OS cursor is replaced with a crosshair while in
   selection mode.
4. While dragging, a translucent red rectangle is drawn as
   an overlay on the surface.
5. On mouse release, the bug report is generated with the
   selected region coordinates in the metadata.
6. Pressing Escape during selection skips it and generates
   the report with `region: None`.
7. Non-Display report types are unaffected — they generate
   immediately as before.

## Design

### State machine

The bug report flow becomes a three-state machine:

```
Idle ──F12/Report──▶ Dialog ──Capture(Display)──▶ RegionSelect
  ▲                    │                              │
  │                  Cancel                     drag-release
  │                  Escape                       or Escape
  │                    │                              │
  └────────────────────┴──────────────────────────────┘
```

For non-Display types, "Capture" goes directly back to
Idle (same as Phase 4).

### New state fields

```rust
/// Whether the app is in region selection mode.
region_select_active: bool,
/// Drag start point in surface pixel coordinates.
region_drag_start: Option<(u32, u32)>,
/// Current drag end point (tracks mouse while dragging).
region_drag_end: Option<(u32, u32)>,
```

These are added to `RyllApp` alongside the existing
bug report dialog fields.  Initialised to `false`,
`None`, `None`.

### Modified dialog Capture action

In the Phase 4 two-pass action handler, the
`Some(true)` (Capture) branch currently calls
`generate_bug_report()` directly.  Phase 5 changes this:

```rust
Some(true) => {
    if self.bug_report_type == BugReportType::Display {
        // Enter region selection mode
        self.region_select_active = true;
        self.region_drag_start = None;
        self.region_drag_end = None;
    } else {
        // Non-display: generate immediately
        let report_type = self.bug_report_type;
        let description = self.bug_description.clone();
        match self.generate_bug_report(
            report_type, description, None,
        ) {
            // ... existing success/error handling
        }
    }
    self.show_bug_dialog = false;
}
```

### Instruction banner

When `region_select_active` is true and
`surface_rect != egui::Rect::NOTHING`, draw a
semi-transparent banner at the top of the surface using
the foreground painter:

```rust
let painter = ctx.layer_painter(egui::LayerId::new(
    egui::Order::Foreground,
    egui::Id::new("region_select_banner"),
));
let banner_rect = egui::Rect::from_min_size(
    self.surface_rect.min,
    egui::vec2(self.surface_rect.width(), 28.0),
);
painter.rect_filled(
    banner_rect,
    0.0,
    egui::Color32::from_rgba_unmultiplied(0, 0, 0, 180),
);
painter.text(
    banner_rect.center(),
    egui::Align2::CENTER_CENTER,
    "Click and drag to select the affected region. \
     Press Escape to skip.",
    egui::FontId::proportional(13.0),
    egui::Color32::WHITE,
);
```

### Crosshair cursor

When `region_select_active` is true and the pointer is
over the surface, set the cursor to crosshair:

```rust
if self.region_select_active
    && self.surface_rect != egui::Rect::NOTHING
{
    ctx.output_mut(|o| {
        o.cursor_icon = egui::CursorIcon::Crosshair;
    });
}
```

This replaces the normal cursor-hiding behaviour (which
hides the OS cursor and draws the SPICE cursor overlay).
When in region selection mode, the SPICE cursor overlay
should not be drawn — only the crosshair.

### Drag handling

During region selection, mouse events must be tracked
for drag selection but NOT forwarded to the SPICE server.
The existing `show_bug_dialog` suppression already handles
the dialog phase.  For the region selection phase, add a
similar guard:

```rust
if self.region_select_active {
    return; // in handle_input()
}
```

And in the mouse handling section:

```rust
if !self.show_bug_dialog && !self.region_select_active {
    // ... existing mouse forwarding code
}
```

Drag tracking uses egui's pointer state:

```rust
if self.region_select_active {
    ctx.input(|i| {
        if i.pointer.primary_pressed() {
            // Start drag
            if let Some(pos) = i.pointer.interact_pos() {
                let x = (pos.x - self.surface_rect.min.x)
                    .max(0.0) as u32;
                let y = (pos.y - self.surface_rect.min.y)
                    .max(0.0) as u32;
                self.region_drag_start = Some((x, y));
                self.region_drag_end = Some((x, y));
            }
        }
        if i.pointer.primary_down() {
            // Update drag end while button held
            if let Some(pos) = i.pointer.interact_pos() {
                let x = (pos.x - self.surface_rect.min.x)
                    .max(0.0) as u32;
                let y = (pos.y - self.surface_rect.min.y)
                    .max(0.0) as u32;
                self.region_drag_end = Some((x, y));
            }
        }
        if i.pointer.primary_released()
            && self.region_drag_start.is_some()
        {
            // Drag complete — generate report
        }
    });
}
```

### Selection rectangle overlay

While dragging (both `region_drag_start` and
`region_drag_end` are `Some`), draw a translucent red
rectangle on the foreground painter:

```rust
if let (Some((sx, sy)), Some((ex, ey))) = (
    self.region_drag_start,
    self.region_drag_end,
) {
    let left = sx.min(ex) as f32
        + self.surface_rect.min.x;
    let top = sy.min(ey) as f32
        + self.surface_rect.min.y;
    let right = sx.max(ex) as f32
        + self.surface_rect.min.x;
    let bottom = sy.max(ey) as f32
        + self.surface_rect.min.y;
    let sel_rect = egui::Rect::from_min_max(
        egui::pos2(left, top),
        egui::pos2(right, bottom),
    );
    let painter = ctx.layer_painter(
        egui::LayerId::new(
            egui::Order::Foreground,
            egui::Id::new("region_select_rect"),
        ),
    );
    painter.rect_filled(
        sel_rect,
        0.0,
        egui::Color32::from_rgba_unmultiplied(
            255, 0, 0, 60,
        ),
    );
    painter.rect_stroke(
        sel_rect,
        0.0,
        egui::Stroke::new(
            2.0,
            egui::Color32::from_rgb(255, 0, 0),
        ),
    );
}
```

### Report generation on drag release

When the primary button is released and a drag was in
progress, compute the region in surface pixel coordinates
and generate the report:

```rust
let (sx, sy) = self.region_drag_start.unwrap();
let (ex, ey) = self.region_drag_end.unwrap();
let region = ReportRegion {
    left: sx.min(ex),
    top: sy.min(ey),
    right: sx.max(ex),
    bottom: sy.max(ey),
};

let report_type = self.bug_report_type;
let description = self.bug_description.clone();
match self.generate_bug_report(
    report_type,
    description,
    Some(region),
) {
    Ok(path) => { /* status message */ }
    Err(e) => { /* error message */ }
}

self.region_select_active = false;
self.region_drag_start = None;
self.region_drag_end = None;
```

This must happen outside the `ctx.input()` closure to
avoid borrowing conflicts.  Use the same two-pass pattern:
collect a `region_complete` flag inside the closure, then
act on it outside.

### Escape during region selection

Escape skips the region selection and generates the report
with `region: None`:

```rust
if self.region_select_active {
    let esc = ctx.input(|i| {
        i.key_pressed(egui::Key::Escape)
    });
    if esc {
        let report_type = self.bug_report_type;
        let description = self.bug_description.clone();
        match self.generate_bug_report(
            report_type, description, None,
        ) {
            // ... status message handling
        }
        self.region_select_active = false;
        self.region_drag_start = None;
        self.region_drag_end = None;
    }
}
```

This check should happen early in `update()`, before the
existing Escape-closes-dialog check.  When in region
selection mode, Escape exits that mode (not the dialog,
which is already closed).

### Input suppression during region selection

Keyboard and mouse forwarding to the SPICE server must be
suppressed during region selection, just as they are during
the dialog:

1. `handle_input()`: return early if
   `self.region_select_active`.
2. Mouse forwarding: guard with
   `!self.region_select_active`.
3. SPICE cursor overlay: don't draw when
   `region_select_active`.
4. OS cursor hiding: don't hide when
   `region_select_active` (so the crosshair is visible).

### Removing `#[allow(dead_code)]` from generate_bug_report

The `generate_bug_report()` method was marked
`#[allow(dead_code)]` in Phase 3 since no GUI trigger
existed.  Phase 4 calls it from the dialog action handler.
The attribute can now be removed.

## Steps

### Step 1: Add region selection state fields

Add `region_select_active: bool`,
`region_drag_start: Option<(u32, u32)>`, and
`region_drag_end: Option<(u32, u32)>` to `RyllApp`.
Initialise to defaults in `RyllApp::new()`.

### Step 2: Modify Capture action for Display type

In the dialog action handler (`Some(true)` branch),
check `self.bug_report_type`.  For Display, enter region
selection mode instead of generating immediately.  For
other types, generate as before.

### Step 3: Add Escape handling for region selection

In `update()`, before the existing Escape-closes-dialog
check, add a check: if `region_select_active` and Escape
pressed, generate the report with `region: None` and exit
selection mode.

### Step 4: Add input suppression during region selection

1. In `handle_input()`, return early if
   `region_select_active`.
2. In the mouse forwarding guard, add
   `&& !self.region_select_active`.
3. In the cursor overlay section, skip drawing the SPICE
   cursor when `region_select_active`.
4. In the cursor-hiding section, skip hiding when
   `region_select_active`.

### Step 5: Add drag tracking and crosshair cursor

In `update()`, after the dialog rendering but before the
cursor overlay:

1. Set crosshair cursor when hovering over the surface.
2. Track drag start on primary press.
3. Update drag end while primary is held.
4. Detect primary release to complete the selection.

Use the two-pass pattern: collect a `region_completed`
flag in the input closure, act on it outside.

### Step 6: Draw instruction banner and selection overlay

Using the foreground `layer_painter`:

1. Draw the instruction banner at the top of the surface.
2. Draw the translucent red selection rectangle while
   dragging.

### Step 7: Generate report on drag release

On drag release (`region_completed`), compute the
`ReportRegion` from the drag start/end points and call
`generate_bug_report()`.  Show the status message.
Clear region selection state.

### Step 8: Remove dead_code annotation

Remove `#[allow(dead_code)]` from `generate_bug_report()`
since it's now called from the GUI.

### Step 9: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. `make test` — all tests pass.

### Step 10: Update documentation

1. Update `ARCHITECTURE.md` to describe region selection.
2. Update `README.md` to document the region selection
   feature.

## Administration and logistics

### Success criteria

* Clicking Capture with Display selected enters region
  selection mode (dialog closes, banner appears).
* Dragging draws a translucent red rectangle.
* Mouse release generates the report with region coords.
* Escape during selection generates without a region.
* Non-Display types generate immediately (no regression).
* Keyboard and mouse input is not forwarded during
  selection.
* The SPICE cursor overlay is hidden during selection;
  the OS crosshair cursor is shown instead.
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.

### Risks

* **Surface rect tracking**: The selection coordinates
  are computed relative to `self.surface_rect`, which
  is set during the surface rendering loop.  If no
  surface exists (e.g. before the first frame), the rect
  is `egui::Rect::NOTHING` and selection would produce
  nonsensical coordinates.  Mitigated by checking
  `surface_rect != egui::Rect::NOTHING` before entering
  selection mode.

* **Drag outside surface bounds**: The user might start
  dragging on the surface and move the cursor outside it.
  Clamping coordinates to `[0, surface_width]` and
  `[0, surface_height]` handles this.  The current code
  already uses `.max(0.0)` for the floor; adding
  `.min(width)` / `.min(height)` prevents overflow.

* **egui pointer state**: `primary_pressed()` fires once
  on the frame the button goes down.  `primary_down()`
  is true while held.  `primary_released()` fires once
  on release.  This is the standard egui drag pattern
  and should work reliably.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
