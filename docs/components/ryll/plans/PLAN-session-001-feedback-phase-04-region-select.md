# Phase 04: Region-select zero-width guard

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/app.rs:3504-3600` (the region-select drag handler
that constructs `ReportRegion` on `primary_released`),
`ryll/src/bugreport.rs:553-599` (the `encode_region_png`
lower-layer validator that already returns `Ok(None)` for
zero-area rectangles), and `ryll/src/bugreport.rs:685-694`
(the `ReportRegion` struct definition — a simple
`Debug, Clone, Serialize` record of four `u32`s). Consult
`ARCHITECTURE.md`'s region-select section (if present) and
`AGENTS.md` §20-style entries for the bug-report assembly
flow.

This phase fixes bug **K4** — "Region-select can produce a
zero-width rectangle" — identified from D2 metadata of B4 /
B6 during dogfooding session 001. The master plan flags this
as small ("Phase 04 — own plan (small); investigate before
deciding fix vs. validation") and notes the impact as "Low —
bad-data path in bug reports".

## Situation

### How region-select works today

The user enters region-select mode by clicking the "Region…"
button on the bug-report dialog. `RyllApp::region_select_active`
flips true; subsequent pointer input is interpreted as
selecting a sub-rectangle of the rendered SPICE surface:

```rust
// app.rs:3515-3534
if i.pointer.primary_pressed() { /* set drag_start = drag_end = pos */ }
if i.pointer.primary_down()    { /* update drag_end = pos */ }
if i.pointer.primary_released() && self.region_drag_start.is_some() {
    region_completed = true;
}
```

On `region_completed`, `app.rs:3584-3599` constructs a
`ReportRegion`:

```rust
let (sx, sy) = self.region_drag_start.unwrap();
let (ex, ey) = self.region_drag_end.unwrap();
let region = ReportRegion {
    left:   sx.min(ex),
    top:    sy.min(ey),
    right:  sx.max(ex),
    bottom: sy.max(ey),
};
self.finish_bug_report(report_type, description, Some(region));
```

…and hands it to the bug-report assembler.

### The K4 path

A pointer-press immediately followed by a pointer-release —
i.e. a click without dragging — produces
`region_drag_start == region_drag_end`. The `ReportRegion`
that results has `left == right` and `top == bottom`, a
degenerate 0×0 rectangle.

The lower layer at `bugreport.rs:570-576` *already* defends
against this:

```rust
if right <= left || bottom <= top {
    return Ok(None);   // no region PNG is encoded
}
```

…so the bug report's `screenshot-region.png` attachment is
simply absent. **The user-visible damage is in the metadata
JSON**: `region: { "left": N, "top": N, "right": N, "bottom": N }`
is still serialized into `report.json` even though no region
PNG accompanies it. A maintainer reading the zip cold sees a
zero-area "region of interest" pointer, can't square it with
the missing PNG, and wastes time chasing a ghost.

Impact is low because:

- No crash, no incorrect data — the wider bug report is still
  usable, just confusingly annotated.
- A click without drag is a relatively unusual user action in
  this mode; the prompt banner explicitly says "Click and
  drag to select the affected region".
- Existing tests at `bugreport.rs:2240-2259` confirm
  `encode_region_png` handles zero-area inputs correctly —
  the bug is purely above that boundary.

### What we don't yet know

Whether **mouse jitter** (a 1-pixel move during what the user
intended as a click) is a meaningful real-world source of
false-positive 1×1 regions. The plan below treats 1×1 as
deliberate (the user could be flagging a specific pixel) and
only rejects strict 0-area cases. If session-002 dogfooding
turns up evidence of jitter-driven 1×1 reports, a minimum
threshold of e.g. 4×4 px could be added in a follow-up
without changing the architecture established here.

## Mission and problem statement

Make ryll's region-select widget refuse to complete a bug
report when the user has not actually selected a region —
i.e. when the resulting rectangle has zero width OR zero
height. The user should get clear, immediate feedback so
they can try again; an accidental click must not produce a
silently-degenerate `report.json`.

The phase succeeds when:

- A click without drag during region-select shows a
  notification ("Drag a region with non-zero area, or press
  Escape to cancel") and the user remains in region-select
  mode with drag state reset, ready for another attempt.
- A proper drag (≥1px in each axis) completes the bug
  report as today.
- The Escape key cancels region-select cleanly as today.
- No regression in the lower-layer
  `encode_region_png` validation — it remains the
  defence-in-depth tier behind the new GUI check.

## Approach

The decision named in the master plan ("fix vs. validation")
resolves to **fix at the GUI layer with user-visible
feedback**, plus keep the existing
`encode_region_png` lower-layer validation as
defence-in-depth.

Reasoning:

- The GUI is the only real producer of `ReportRegion`. The
  other construction sites in the tree (~4) are all in tests
  exercising specific shapes. Fixing the GUI fixes the bug
  at its actual source.
- Promoting `ReportRegion` to an `Option`-returning
  constructor would change a simple `Debug, Clone, Serialize`
  public type's surface — invasive for a low-impact bug. The
  GUI-layer guard achieves the same invariant for every live
  call site without touching the type.
- The lower `encode_region_png` validator catches any future
  call site that bypasses the GUI guard. Two layers, no
  cost, no overlap.

### Concrete change

In `app.rs` near line 3584, replace the unconditional
completion block with a guarded one:

```rust
if region_completed {
    let (sx, sy) = self.region_drag_start.unwrap();
    let (ex, ey) = self.region_drag_end.unwrap();
    match validate_region(sx, sy, ex, ey) {
        Some(region) => {
            let report_type = self.bug_report_type.clone();
            let description = self.bug_description.clone();
            self.finish_bug_report(report_type, description, Some(region));
            self.region_select_active = false;
            self.region_drag_start = None;
            self.region_drag_end = None;
        }
        None => {
            // Click without drag, or jitter-only. Stay in
            // region-select mode; tell the user what went
            // wrong and reset the drag state for another go.
            self.push_notification(
                NotifySeverity::Warn,
                NotificationSource::BugReport,
                "Drag a region with non-zero area, or press \
                 Escape to cancel.".to_string(),
            );
            self.region_drag_start = None;
            self.region_drag_end = None;
        }
    }
}
```

`validate_region` is a new pure free function in `app.rs`:

```rust
/// Construct a `ReportRegion` from the raw drag-start /
/// drag-end coordinates iff the rectangle has strictly
/// positive area. Returns `None` for click-without-drag
/// (the K4 case) and for any other degenerate input.
///
/// "Strictly positive area" means `right > left AND
/// bottom > top` — 1×1 is allowed since a deliberate
/// 1-pixel drag is a valid pointer at a specific pixel,
/// and rejecting it would require a jitter threshold that
/// no current data justifies.
fn validate_region(sx: u32, sy: u32, ex: u32, ey: u32)
    -> Option<ReportRegion>
{
    let left   = sx.min(ex);
    let right  = sx.max(ex);
    let top    = sy.min(ey);
    let bottom = sy.max(ey);
    if right > left && bottom > top {
        Some(ReportRegion { left, top, right, bottom })
    } else {
        None
    }
}
```

Pure function, no I/O, no `self` — testable directly.

### Why a notification rather than ignoring silently

If we just reset state without telling the user, a click
that they expected to count as "select this pixel" would
silently do nothing. The user would re-click and re-click
expecting *something* to happen. The notification ties the
behaviour to a visible cause.

`NotifySeverity::Warn` (not `Error`) — this isn't a failure,
just an unfinished action. `NotificationSource::BugReport`
groups it under the same source as the rest of the
bug-report producer chain.

### Testing

- **Unit test in `app.rs::tests`**: `validate_region_*`
  cases — click without drag (returns None), 1×1 drag
  (returns Some with width=1, height=1), normal 10×10 drag
  (returns Some), zero-width / non-zero-height (None),
  non-zero-width / zero-height (None), reversed coordinates
  (drag from bottom-right to top-left → still produces a
  correctly oriented region).
- **No new test for the GUI completion handler itself** —
  the egui input loop is not unit-testable, but
  `validate_region` covers the decision logic completely,
  and the handler just dispatches on its Option.
- **Manual integration check**: open the bug-report dialog,
  click the Region button, then click once on the surface
  without dragging. Verify the notification appears and
  region-select stays active. Then do a proper drag; verify
  the report fires as before.

## Tasks

- [x] Add `validate_region(sx, sy, ex, ey) -> Option<ReportRegion>`
      as a free function in `ryll/src/app.rs` near the other
      pure helpers (above `default_arrow_cursor`). Doc comment
      explains the K4 context and the "strictly positive
      area" rule.
- [x] In the region-completion block at `app.rs:3584`,
      dispatch on `validate_region`. On `Some`, behave as
      today. On `None`, push a `NotifySeverity::Warn`
      notification, reset `region_drag_start` and
      `region_drag_end`, and leave `region_select_active`
      true so the user can try again.
- [x] Add unit tests in `ryll/src/app.rs::tests`:
  - `validate_region_click_without_drag_returns_none`
  - `validate_region_zero_width_returns_none`
  - `validate_region_zero_height_returns_none`
  - `validate_region_one_by_one_returns_some` (deliberate
    1-pixel region is a valid pointer)
  - `validate_region_normal_drag_returns_some` (the happy
    path)
  - `validate_region_reversed_drag_normalises` (drag from
    bottom-right to top-left should still produce a
    canonical {left ≤ right, top ≤ bottom} region)
- [x] Update `PLAN-session-001-feedback.md` K4 row in the
      "Confirmed bugs" table with the resolution pointer.
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 04 → Done.
- [ ] Manual integration check (deferred operator action,
      can bundle with Phase 02 and Phase 03 manual
      checklists): open the bug-report dialog (e.g. F8),
      click Region, single-click the surface without
      dragging — verify the notification appears via the
      bell, region-select remains active, drag state cleared.
      Then drag a non-zero rectangle and confirm the bug
      report writes normally with both `report.json`'s
      `region` and the `screenshot-region.png` attachment
      present.

## Out of scope

- **Minimum-size threshold beyond 1×1.** Jitter-based
  1-pixel false positives are not currently observed; adding
  a 4×4 px floor without data would just paper over user
  intent. Revisit if session-002 dogfooding shows
  jitter-driven 1×1 reports in the wild.
- **Promoting `ReportRegion` to an `Option`-returning
  constructor.** The simple-struct shape is fine; making it
  validating would change a public-ish surface for a small
  bug. The GUI guard is the right scope.
- **Other degenerate region inputs.** `encode_region_png`'s
  existing clamping handles oversized / off-surface regions
  gracefully (tests at `bugreport.rs:2228-2270` cover
  these). No additional GUI work for those cases.
- **Web-mode / headless region-select.** Neither mode has a
  region-select UX today; out of scope until they grow one.
- **A wider audit of `ReportRegion` validity invariants.**
  E.g. should `right <= width` be enforced at construction?
  The lower layer already clamps; pushing it up the stack
  is more architecture than this bug warrants.

## Open questions

1. **Should a single click on the surface in region-select
   mode immediately escape rather than reset?** Some users
   might expect "click cancels". Counter-argument: the
   banner explicitly says "Click and drag … Press Escape to
   skip", and inferring intent from a click is fragile. Stay
   with the explicit-Escape contract.

2. **Should the notification include a hint about what kind
   of drag is expected?** The current copy is concise and
   functional. If the manual check reveals user confusion,
   could expand to e.g. "Drag from one corner of the
   affected area to the opposite corner". Defer; current
   wording mirrors the banner.

## Companion docs

None new. `ARCHITECTURE.md` does not have a dedicated
region-select section, and adding one for this small fix
would be disproportionate. `AGENTS.md` likewise unchanged —
the pure-function-with-tests pattern fits the existing
project conventions without needing a new §-entry.
