# Phase 3 — submit-time region crop

Parent: [PLAN-bugreport-trigger-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot/).

## Goal

When a Display bug report goes through region selection,
write a second image into the zip: `screenshot-region.png` —
a crop of the **submit-time** surface at the user-selected
rectangle. Paired with phase 2's trigger-time
`screenshot.png`, this gives the reviewer a "then vs. now"
view: the artefact caught in the act, plus a zoomed-in look
at exactly the area the user flagged after the form-filling
delay.

## Scope

In scope:

- New `screenshot_region_png: Option<Vec<u8>>` field on
  `BugReport`.
- Crop-and-encode logic in `BugReport::assemble`: when the
  report is Display, region is `Some`, and `surface_pixels`
  is `Some`, extract the rectangle from the pixel buffer,
  clamp to surface bounds, and PNG-encode.
- `write_zip` writes `screenshot-region.png` when the field
  is `Some`.
- `write_pedantic`'s zip-writer block stays untouched —
  pedantic reports never carry a region. (`screenshot_region_png`
  on the `BugReport` struct will just stay `None` for that
  code path.)

Out of scope (phase 4):

- User-facing documentation changes.

Explicitly out of scope (forever, not a later phase):

- Cropping the trigger-time PNG. The region coordinates are
  in **submit-time** surface space; the trigger-time surface
  may have been a different size. Trying to re-crop the
  trigger PNG would require decoding, coordinate-mapping,
  and re-encoding, and the result would be nonsense whenever
  the guest resized mid-dialog. The two images deliberately
  represent two different moments.

## Grounding — what's there today

### Region selection

Region-select mode at
[app.rs:2161-2253](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2161-L2253) clamps
the drag to the surface bounds during the drag (`.min(surf_w)`
/ `.min(surf_h)`) and builds a `ReportRegion` from
`region_drag_start` and `region_drag_end`:

```rust
let region = ReportRegion {
    left: sx.min(ex),
    top:  sy.min(ey),
    right:  sx.max(ex),
    bottom: sy.max(ey),
};
```

The coordinates are in **surface pixel space** (they are
`pos.x - self.surface_rect.min.x` etc., then cast to `u32`).
Region selection only runs when `bug_report_type ==
BugReportType::Display` — see the dispatch in
[app.rs:2287-2302](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2287-L2302):

```rust
if self.bug_report_type == BugReportType::Display {
    self.region_select_active = true;
    ...
} else {
    // Non-display: generate immediately
    ...
}
```

So `finish_bug_report(..., Some(region))` only ever reaches
`BugReport::assemble` with a Display report type. Phase 3
still guards on both — a belt-and-braces check is cheap and
defends against future callers.

### `surface_pixels` at submit

[`generate_bug_report`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L955) picks the
largest surface at submit time and passes
`Some((pixels, width, height))` for Display reports. That's
the same surface buffer phase 3 will crop. Non-Display
reports pass `None`, so the crop path short-circuits.

### `BugReport` zip layout

[`write_zip`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L922-L960) writes
files in this order:

1. `metadata.json`
2. `session.json`
3. `channel-state.json`
4. `traffic.pcap` (if present)
5. `screenshot.png` (if present)
6. `runtime-metrics.json`

Phase 3 inserts `screenshot-region.png` between #5 and #6
when the field is `Some`.

### Existing cropping code

[`display::surface::DisplaySurface::copy_subrect`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L440-L460)
already does a clipped, aliasing-safe sub-rect copy within
a single surface. Phase 3's crop is simpler (source and
destination are different buffers, so no aliasing concern)
but the row-by-row stride loop is the same pattern.

## Design

### `BugReport` struct addition

```rust
pub struct BugReport {
    metadata_json: String,
    session_json: String,
    channel_state_json: String,
    pcap_bytes: Option<Vec<u8>>,
    screenshot_png: Option<Vec<u8>>,
    /// PNG bytes for `screenshot-region.png`: a crop of the
    /// submit-time surface at `ReportMetadata::region`.
    /// `None` when the report isn't Display, no region was
    /// selected, or the clamped region is degenerate.
    screenshot_region_png: Option<Vec<u8>>,
    runtime_metrics: RuntimeMetrics,
}
```

### Crop helper

A small `pub(crate)` function on `bugreport.rs`:

```rust
/// Extract the RGBA sub-rectangle `region` from a
/// `width × height` RGBA buffer and PNG-encode it.
///
/// `region` is clamped to the surface bounds. Returns
/// `Ok(None)` when the clamped rectangle is empty (e.g. a
/// zero-width click, or a region entirely outside the
/// surface after clamping). Returns `Err` only on PNG
/// encoder failure.
pub(crate) fn encode_region_png(
    pixels: &[u8],
    width: u32,
    height: u32,
    region: &ReportRegion,
) -> anyhow::Result<Option<Vec<u8>>> {
    let left   = region.left.min(width);
    let top    = region.top.min(height);
    let right  = region.right.min(width);
    let bottom = region.bottom.min(height);
    if right <= left || bottom <= top {
        return Ok(None);
    }
    let crop_w = (right - left) as usize;
    let crop_h = (bottom - top) as usize;

    let src_stride = (width as usize) * 4;
    let dst_stride = crop_w * 4;
    // Defensive: caller should always pass a correctly-sized
    // buffer, but skip silently rather than indexing out of
    // range if they don't.
    if pixels.len() < src_stride * (height as usize) {
        return Ok(None);
    }

    let mut out = vec![0u8; dst_stride * crop_h];
    for row in 0..crop_h {
        let src_y = (top as usize) + row;
        let src_start = src_y * src_stride + (left as usize) * 4;
        let dst_start = row * dst_stride;
        out[dst_start..dst_start + dst_stride]
            .copy_from_slice(&pixels[src_start..src_start + dst_stride]);
    }

    Ok(Some(encode_png(&out, crop_w as u32, crop_h as u32)?))
}
```

Behaviour notes:

- Clamping uses `min(width)` / `min(height)`, not `min(width
  - 1)`, because `ReportRegion`'s right / bottom are
  exclusive in the current convention (compare with the
  region's use in metadata).
- A clamped-to-empty region yields `Ok(None)`, not `Err`.
  Degenerate clicks are user error, not bug-report-pipeline
  error.
- Encoder errors bubble up as `Err`, matching how the full
  screenshot path handles encoder failure today.

### `assemble()` changes

Add a new block parallel to the existing screenshot block:

```rust
// Region crop (display reports with a non-empty region only).
// Uses the submit-time surface pixels — deliberately *not*
// the precomputed trigger PNG, whose dimensions may not
// match. See phase 3 plan for rationale.
let screenshot_region_png = if report_type == BugReportType::Display {
    match (&region, surface_pixels) {
        (Some(r), Some((pixels, w, h))) => encode_region_png(pixels, w, h, r)?,
        _ => None,
    }
} else {
    None
};
```

`surface_pixels` is `Option<(&[u8], u32, u32)>`, i.e. all
components are `Copy`, so using it here after the existing
screenshot block already consumed it in a pattern match is
fine.

Assign `screenshot_region_png` into the returned
`BugReport` struct literal.

### `write_zip` changes

Insert one `if let Some(...)` block after the
`screenshot.png` block:

```rust
if let Some(ref png) = self.screenshot_png {
    zip.start_file("screenshot.png", opts)?;
    zip.write_all(png)?;
}

if let Some(ref png) = self.screenshot_region_png {
    zip.start_file("screenshot-region.png", opts)?;
    zip.write_all(png)?;
}
```

### `write_pedantic`'s zip-writer block

No change. `screenshot_region_png` on the assembled
`BugReport` is always `None` for pedantic reports
(`region: None`, `surface_pixels: None`), so even if
`write_pedantic` mirrored the full `write_zip` layout it
would write nothing. Leaving its zip-writer block unchanged
keeps the two paths minimal.

### `region` in `metadata.json`

Unchanged. The coordinates describe the user's intent in
submit-time surface space. They index into
`screenshot-region.png` directly (the whole file IS the
region), and index into the full trigger-time `screenshot.png`
only as far as the surface geometry at trigger time matched
submit time. Phase 4 documents this interpretation for
analysts; phase 3 makes no schema changes.

## Capture-vs-include reminder

The operator's phase 2 rule still applies to this phase:

- Always *capture* at dialog-open (phase 2's job).
- Only *include* the trigger-time `screenshot.png` for
  Display submissions.
- Phase 3's `screenshot-region.png` is additionally gated on
  region selection producing a non-empty rectangle. It
  cannot exist without a Display submission, because
  non-Display dispatch paths never enter region-select mode.

## Open questions

1. **Do we keep the JPEG-compression-mode choice the same?**
   `write_zip` uses `CompressionMethod::Stored` (no
   compression) because PNG is already compressed and the
   zip would bloat. Region-crop PNG inherits this. No
   decision required — just calling it out.
2. **Do we need worktree isolation?** No — the change is
   localised to `BugReport::assemble`, one helper function,
   and one zip-writer block. Nothing cross-thread, nothing
   lifetime-tricky.
3. **What if the region is 1×1 (a click without drag)?**
   The crop produces a 1×1 PNG. That's intentional — the
   user asked for it. No special-case guard beyond the
   `right <= left || bottom <= top` degeneracy check.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a   | medium | sonnet | none      | Implement phase 3 exactly as described in the *Design* section: add `screenshot_region_png: Option<Vec<u8>>` to the `BugReport` struct, add the `pub(crate) fn encode_region_png` helper to `bugreport.rs`, extend `assemble()` to build the region crop and populate the new field, and extend `write_zip` to write `screenshot-region.png` after `screenshot.png` when the field is `Some`. `write_pedantic`'s zip writer block stays unchanged. Add the four tests listed below. Run `pre-commit run --all-files` and `make test` before handing back. Single commit with message `Add screenshot-region.png to Display bug reports.`. Do not commit until the management session has reviewed. |

Medium effort — the design is fully worked through. Sonnet
is fine. No worktree isolation (see open question 2).

### Tests

All in `ryll/src/bugreport.rs` tests module.

1. **`test_bug_report_writes_region_crop_when_region_present`** —
   Display report, `Some(region)` inside a 2×2 surface (e.g.
   `region = {left:0,top:0,right:2,bottom:2}`), assert the
   resulting zip contains both `screenshot.png` and
   `screenshot-region.png`, both starting with `\x89PNG`.
2. **`test_bug_report_no_region_crop_without_region`** —
   Display report, `region: None`: zip has `screenshot.png`
   but not `screenshot-region.png`.
3. **`test_bug_report_no_region_crop_for_non_display`** —
   Input report with a region set in the metadata struct
   (simulating a buggy caller): zip has neither
   screenshot file. Belt-and-braces coverage for the
   report-type guard.
4. **`test_encode_region_png_clamps_and_skips_degenerate`** —
   unit test on the helper: region extending past the
   surface clamps and produces `Ok(Some(_))`; zero-width
   region produces `Ok(None)`; region entirely outside the
   surface produces `Ok(None)`.
5. **`test_encode_region_png_pixels_match_source`** — unit
   test on the helper with a handcrafted 4×4 RGBA buffer and
   a 2×2 sub-rect; decode the output PNG with
   `png::Decoder` and assert the decoded RGBA equals the
   expected 2×2 slice. Proves the stride math is right.

Existing tests stay as-is — none of them exercise
`screenshot_region_png`, and the new field defaults to
`None` everywhere they construct a `BugReport`.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, with the five new tests present.
* Manual smoke test: capture a Display bug report with a
  region drag. The resulting zip contains both
  `screenshot.png` (full surface, trigger-time) and
  `screenshot-region.png` (cropped, submit-time). The
  cropped image shows the rectangle the user drew.
* No existing zip entry is reordered or renamed.

## Review checklist

- [ ] `BugReport` struct has new `screenshot_region_png`
      field; it's populated in `assemble()` and consumed in
      `write_zip`.
- [ ] `encode_region_png` is `pub(crate)`, clamps to surface
      bounds, returns `Ok(None)` on a degenerate rect, and
      `Err` only on PNG encoder failure.
- [ ] `assemble()` guards on `report_type ==
      BugReportType::Display` as well as `region.is_some()`
      and `surface_pixels.is_some()`.
- [ ] `write_zip` writes `screenshot-region.png` after
      `screenshot.png` using the same
      `CompressionMethod::Stored` options.
- [ ] `write_pedantic` zip-writer block unchanged.
- [ ] Five new tests present and passing.
- [ ] Single commit with message "Add screenshot-region.png
      to Display bug reports." and the standard
      Signed-off-by / Assisted-By / Co-Authored-By trailer.

## Follow-up

Phase 4 updates `README.md`, `ARCHITECTURE.md`, and
`docs/plans/PLAN-bug-reports.md` (or whichever user-facing
docs describe the zip contents) to explain the two-image
layout and the "region applies to submit-time surface
coordinates" convention.
