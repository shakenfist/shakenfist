# Phase 2 — trigger-time surface snapshot

Parent: [PLAN-bugreport-trigger-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot/).

## Goal

When the bug-report dialog opens, clone the current display
surface, spawn a background thread that PNG-encodes it, and
stash the encoded bytes on the app so `generate_bug_report`
can use them as `screenshot.png` at submit time — replacing
today's "read the live surface at submit" logic that loses
transient artefacts (e.g. the 2026-04-23 macbook "static").

Phase 1 added the metadata plumbing; this phase is what
actually makes `screenshot.png` show the bug.

## Scope

In scope:

- New `TriggerSnapshot` struct on `RyllApp` with the
  triggered-at timestamps from phase 1 and a
  `Arc<Mutex<Option<anyhow::Result<Vec<u8>>>>>` slot for the
  background-encoded PNG.
- A background `std::thread` worker that PNG-encodes via
  `bugreport::encode_png` and writes into the slot.
- Wiring every dialog-open path (F12, status-bar Report,
  USB-error auto-open, channel-error auto-open) to call
  `begin_trigger_snapshot`, and every close-without-submit
  path (F12 toggle-off, Escape, Cancel button) to call
  `discard_trigger_snapshot`.
- Extending `BugReport::new` / `assemble` /
  `write_pedantic` with a
  `precomputed_screenshot_png: Option<Vec<u8>>` parameter
  that short-circuits the live encode.
- Wiring `finish_bug_report` and `generate_bug_report` to
  consume the pending snapshot's timestamps and PNG bytes.

Out of scope (later phases):

- `screenshot-region.png` (phase 3).
- Documentation updates (phase 4).

## Grounding — what's there today

### Dialog open paths on `RyllApp`

Four places set `show_bug_dialog = true`:

| # | Location | Trigger | Default type |
|---|----------|---------|--------------|
| 1 | [app.rs:1247](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1247) | F12 key (toggles) | `BugReportType::Display` |
| 2 | [app.rs:1440](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1440) | Status-bar "Report" button | `BugReportType::Display` |
| 3 | [app.rs:1786](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1786) | USB error auto-open | `BugReportType::Usb` |
| 4 | [app.rs:2066](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2066) | Channel error auto-open | `BugReportType::Connection` |

### Dialog close-without-submit paths

| # | Location | Trigger |
|---|----------|---------|
| 1 | [app.rs:1247](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1247) | F12 again (toggles off) |
| 2 | [app.rs:1275](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1275) | Escape while dialog open |
| 3 | [app.rs:2155](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2155) | Cancel button |

### Submit paths

| # | Location | Trigger |
|---|----------|---------|
| 1 | [app.rs:2150](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2150) | Capture button for non-Display |
| 2 | [app.rs:2251](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2251) | Region-drag complete (Display) |
| 3 | [app.rs:1236](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1236) | Escape during region select (submits with no region) |

All three submit paths funnel through
[`finish_bug_report`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L965) →
[`generate_bug_report`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L923).

### Surface access

`DisplaySurface::pixels()` returns `&[u8]` (RGBA). `width` /
`height` are public fields. `generate_bug_report` today picks
the largest surface:

```rust
self.surfaces.values()
    .max_by_key(|s| (s.width as u64) * (s.height as u64))
    .map(|s| (s.pixels(), s.width, s.height))
```

### PNG encoder

[`bugreport::encode_png(pixels: &[u8], width: u32, height: u32)
-> anyhow::Result<Vec<u8>>`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L589)
is already `pub(crate)` and pure. No changes needed.

## Design

### `TriggerSnapshot` struct (app-side)

Lives in `ryll/src/app.rs` — app-specific state, not part of
the on-disk bug-report schema. The on-disk timestamps are
already represented by `bugreport::TriggerTimestamps` from
phase 1.

```rust
/// Captured on the UI thread when a bug-report dialog opens.
/// The raw RGBA is cloned and handed to a background
/// std::thread that PNG-encodes into `png_slot`; the UI
/// thread holds another clone of the `Arc` and consumes the
/// bytes at submit time (or drops the `Arc` on cancel,
/// letting the worker write into what becomes garbage).
struct TriggerSnapshot {
    triggered_at: String,
    triggered_uptime_secs: f64,
    /// Slot the encoder thread fills with either the PNG
    /// bytes or an `Err` on encode failure. `None` while the
    /// worker is still running. The `Arc<Mutex>` is shared
    /// with the encoder thread.
    png_slot: Arc<Mutex<Option<anyhow::Result<Vec<u8>>>>>,
}
```

Field on `RyllApp`:

```rust
/// Populated by `begin_trigger_snapshot` the moment a
/// bug-report dialog opens. Consumed at submit time, or
/// dropped on cancel.
pending_trigger: Option<TriggerSnapshot>,
```

Initialise to `None` in the constructor.

### Helper methods on `RyllApp`

```rust
/// Clone the largest surface's RGBA pixels, capture trigger
/// timestamps, and spawn a background thread to PNG-encode.
/// Idempotent: a no-op if `pending_trigger.is_some()`, so
/// every open path can call this unconditionally.
fn begin_trigger_snapshot(&mut self);

/// Drop the pending snapshot. The encoder thread continues
/// and eventually writes into a Mutex whose last Arc it holds,
/// which is then dropped. Safe on `None`.
fn discard_trigger_snapshot(&mut self);

/// Called from `finish_bug_report`. Returns the captured
/// timestamps (if any) and the encoded PNG bytes (if the
/// worker finished with `Ok`). Both are independently
/// `Option` so an `Err` or not-yet-finished encode falls
/// back to live encoding inside `BugReport::assemble`.
fn take_trigger_for_submit(
    &mut self,
) -> (Option<bugreport::TriggerTimestamps>, Option<Vec<u8>>);
```

`begin_trigger_snapshot` pseudocode:

```rust
fn begin_trigger_snapshot(&mut self) {
    if self.pending_trigger.is_some() {
        return; // idempotent
    }
    let Some(surface) = self
        .surfaces
        .values()
        .max_by_key(|s| (s.width as u64) * (s.height as u64))
    else {
        // No surface yet (pre-first-SURFACE_CREATE). Record
        // timestamps anyway so analysts still get them, but
        // leave the png_slot ready-with-None so fallback fires.
        let slot = Arc::new(Mutex::new(None));
        *slot.lock().unwrap() = Some(Err(anyhow::anyhow!(
            "no surface available at trigger time"
        )));
        self.pending_trigger = Some(TriggerSnapshot {
            triggered_at: bugreport::chrono_now(),
            triggered_uptime_secs: self.traffic.elapsed().as_secs_f64(),
            png_slot: slot,
        });
        return;
    };

    let pixels: Vec<u8> = surface.pixels().to_vec();
    let width = surface.width;
    let height = surface.height;
    let slot: Arc<Mutex<Option<anyhow::Result<Vec<u8>>>>> =
        Arc::new(Mutex::new(None));
    let slot_for_thread = Arc::clone(&slot);

    std::thread::Builder::new()
        .name("ryll-bugreport-png".to_string())
        .spawn(move || {
            let result = bugreport::encode_png(&pixels, width, height);
            // If the Mutex is poisoned the UI thread panicked;
            // nothing to write.
            if let Ok(mut guard) = slot_for_thread.lock() {
                *guard = Some(result);
            }
        })
        .ok(); // If spawn fails, png_slot stays None → submit
               // falls back to live encoding.

    self.pending_trigger = Some(TriggerSnapshot {
        triggered_at: bugreport::chrono_now(),
        triggered_uptime_secs: self.traffic.elapsed().as_secs_f64(),
        png_slot: slot,
    });
}
```

`discard_trigger_snapshot`:

```rust
fn discard_trigger_snapshot(&mut self) {
    let _ = self.pending_trigger.take();
}
```

`take_trigger_for_submit`:

```rust
fn take_trigger_for_submit(
    &mut self,
) -> (Option<bugreport::TriggerTimestamps>, Option<Vec<u8>>) {
    let Some(snap) = self.pending_trigger.take() else {
        return (None, None);
    };
    // try_lock, not lock: if the encoder is still holding
    // the mutex (rare but possible on a large surface), we'd
    // rather fall back than block the UI.
    let png = match snap.png_slot.try_lock() {
        Ok(mut guard) => guard.take().and_then(|r| r.ok()),
        Err(_) => None,
    };
    let trigger = bugreport::TriggerTimestamps {
        triggered_at: snap.triggered_at,
        triggered_uptime_secs: snap.triggered_uptime_secs,
    };
    (Some(trigger), png)
}
```

`bugreport::chrono_now` is currently `pub(crate)` — keep it
that way; `ryll/src/app.rs` is in the same crate.

### Wiring the dialog paths

Every open path becomes two lines (set flag + call begin):

```rust
// F12 toggle (app.rs:1247)
if f12_pressed {
    if self.show_bug_dialog {
        self.show_bug_dialog = false;
        self.discard_trigger_snapshot();
    } else {
        self.show_bug_dialog = true;
        self.bug_report_type = BugReportType::Display;
        self.bug_description.clear();
        self.begin_trigger_snapshot();
    }
}
```

Status-bar Report button, USB-error, channel-error: call
`self.begin_trigger_snapshot()` immediately after
`self.show_bug_dialog = true;`.

Close-without-submit: Escape handler at app.rs:1275 and
Cancel branch at app.rs:2155 both gain a
`self.discard_trigger_snapshot()` call.

### Wiring the submit path

`finish_bug_report` becomes:

```rust
fn finish_bug_report(
    &mut self,
    report_type: BugReportType,
    description: String,
    region: Option<ReportRegion>,
) {
    let (trigger, precomputed_png) = self.take_trigger_for_submit();
    match self.generate_bug_report(
        report_type,
        description,
        region,
        trigger,
        precomputed_png,
    ) {
        Ok(path) => { /* unchanged */ }
        Err(e) => { /* unchanged */ }
    }
}
```

`generate_bug_report` signature grows by two parameters; it
becomes:

```rust
pub fn generate_bug_report(
    &self,
    report_type: BugReportType,
    description: String,
    region: Option<ReportRegion>,
    trigger: Option<bugreport::TriggerTimestamps>,
    precomputed_screenshot_png: Option<Vec<u8>>,
) -> anyhow::Result<std::path::PathBuf>
```

and passes both through to `BugReport::new`. The existing
`surface_data` read stays — phase 3 will use it for the
submit-time region crop, and it's also the fallback when
`precomputed_screenshot_png` is `None`.

### `BugReport::new` / `assemble` / `write_pedantic` extension

Add a `precomputed_screenshot_png: Option<Vec<u8>>` parameter
to all three. Inside `assemble`, replace:

```rust
let screenshot_png = if report_type == BugReportType::Display {
    if let Some((pixels, w, h)) = surface_pixels {
        Some(encode_png(pixels, w, h)?)
    } else {
        None
    }
} else {
    None
};
```

with:

```rust
let screenshot_png = if report_type == BugReportType::Display {
    if let Some(bytes) = precomputed_screenshot_png {
        Some(bytes)
    } else if let Some((pixels, w, h)) = surface_pixels {
        Some(encode_png(pixels, w, h)?)
    } else {
        None
    }
} else {
    // Non-Display submissions drop the precomputed PNG on
    // the floor. The operator's rule is to always *capture*
    // at dialog-open time (so we get the artefact before it
    // fades) and only *include* the PNG when the user
    // actually submits a Display report. A non-Display
    // submission with a pending snapshot means the user
    // changed the type after open-time; the capture is
    // thrown away rather than bloating an Input / Cursor /
    // Connection / Usb zip with an image that won't help.
    None
};
```

`write_pedantic` stays an "observer fires now, no user delay"
path — it always passes `None` for both the trigger and the
precomputed PNG. Phase 1 already left the `trigger: None`
call in place; this phase adds the parallel
`precomputed_screenshot_png: None`.

### `pub(crate)` exposure for app.rs

`chrono_now` is already `pub(crate)`. `TriggerTimestamps` is
already `pub` (phase 1). `encode_png` is already
`pub(crate)`. No additional visibility changes needed.

## Capture-vs-include policy

The operator's rule: **always capture on dialog open**,
regardless of which report type the dialog was opened with,
because by the time the user decides what they're actually
reporting the artefact might already be gone. The screenshot
is then **included in the zip only if the final submitted
type is Display** — non-Display submissions drop the PNG so
they don't bloat the bug report with an image that won't
help.

Concretely:

- `begin_trigger_snapshot` fires unconditionally from every
  open path. The type selected in the dialog at open time is
  irrelevant to whether we snapshot.
- `BugReport::assemble` decides whether to include the PNG
  based on `report_type`. Non-Display: drop. Display: use
  the precomputed bytes, or fall back to a live encode of
  `surface_pixels`, or `None` if neither is available.

Do **not** add an "only snapshot when the type is Display"
guard at open time — that would reintroduce the very
submit-time lag this phase exists to eliminate.

The CPU cost of running the encoder thread on an open that
ends up submitted as non-Display is accepted: the snapshot
itself is already a clone, the encoder runs off the UI
thread, and bug-report dialog opens are rare events.

## Open questions

1. **Do we need worktree isolation?** The master plan
   originally flagged phase 2 as needing it on the assumption
   that cross-thread lifetime management would be fiddly.
   Having walked the code, the thread model is clean
   (single-producer write into `Arc<Mutex<Option<T>>>`, no
   egui Context crossing threads, no async bridging), so I'm
   recommending **no worktree isolation** — the change is
   reviewable in-tree and a bad landing reverts cleanly.
2. **`try_lock` versus `lock` in `take_trigger_for_submit`?**
   `try_lock` — see code comment above. Submitting the form
   shouldn't block on the encoder for large displays.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | medium | sonnet | none      | Implement phase 2 exactly as described in the *Design* section: add `TriggerSnapshot` on `RyllApp`; add the three helper methods; call `begin_trigger_snapshot` / `discard_trigger_snapshot` from the four open paths and three close-without-submit paths respectively; update `finish_bug_report` and `generate_bug_report` to pipe the trigger and pre-computed PNG through; extend `BugReport::new` / `assemble` / `write_pedantic` with the `precomputed_screenshot_png: Option<Vec<u8>>` parameter; update existing tests to pass `None`; add the three new tests listed below. Use `std::thread::Builder::new().name("ryll-bugreport-png")...spawn(...)`, not `tokio::spawn_blocking`. Run `pre-commit run --all-files` and `make test` before handing back. Single commit with message `Snapshot the display surface when the bug-report dialog opens.`. Do not commit until the management session has reviewed. |

Medium effort rather than high because the design is
fully worked through above. Sonnet is sufficient given the
detail of the brief. No worktree isolation (see open
question 2).

### Tests

1. **`test_bug_report_uses_precomputed_png_when_provided`** —
   call `assemble` with `precomputed_screenshot_png:
   Some(vec![0x89, b'P', b'N', b'G', ...sentinel bytes])` and
   `surface_pixels: Some((&raw, 2, 2))` and assert the
   `screenshot_png` field on the returned `BugReport` equals
   the sentinel bytes *verbatim* (i.e. `encode_png` was not
   re-run on the raw pixels).
2. **`test_bug_report_falls_back_to_live_encoding_when_no_precomputed_png`** —
   regression: `precomputed_screenshot_png: None`,
   `surface_pixels: Some(...)` still produces a valid PNG in
   `screenshot_png` starting with `\x89PNG`.
3. **`test_trigger_snapshot_worker_encodes_png`** — construct
   a `TriggerSnapshot`-shaped `Arc<Mutex<Option<anyhow::Result<Vec<u8>>>>>`
   directly, call `encode_png` on a std::thread with an Arc
   clone, join the thread (or poll the slot with a small
   timeout), assert the stored bytes start with `\x89PNG`.
   Tests the thread contract without needing `RyllApp` /
   egui. Can live in `app.rs` under `#[cfg(test)]` if the
   TriggerSnapshot type stays there; or in `bugreport.rs` if
   we move the slot type there. Prefer keeping it in
   `app.rs` since `TriggerSnapshot` is app-specific.

Existing tests that call `BugReport::assemble`,
`BugReport::new`, or `BugReport::write_pedantic` must pass
the new `precomputed_screenshot_png: None` argument (mirrors
the phase 1 test updates).

**Not tested at this phase:** the egui-integrated dialog
open / cancel / submit flow. That would require an egui
harness; cost/benefit doesn't favour it. The helper methods
are small enough that manual smoke testing (run ryll, hit
F12, observe `screenshot.png` matches the trigger moment) is
sufficient.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, with the three new tests present.
* Manual smoke test: open the dialog while something
  distinctive is on screen, then wait for the surface to
  change, then submit; `screenshot.png` in the resulting zip
  shows the original content, not the post-change content.
* Cancel paths leave no lingering `pending_trigger` — visible
  in a debug build via the slot's Drop or by opening and
  cancelling repeatedly without memory growth.
* `metadata.json`'s `triggered_at` / `triggered_uptime_secs`
  now reflect dialog-open time, not submit time.

## Review checklist

- [ ] `TriggerSnapshot` struct added to `RyllApp`; initialised
      `None` in the constructor.
- [ ] All four dialog-open sites call
      `begin_trigger_snapshot`.
- [ ] All three close-without-submit sites call
      `discard_trigger_snapshot`.
- [ ] `finish_bug_report` consumes the trigger via
      `take_trigger_for_submit` and passes both values into
      `generate_bug_report`.
- [ ] `BugReport::new` / `assemble` / `write_pedantic` all
      accept `precomputed_screenshot_png: Option<Vec<u8>>`.
- [ ] Thread uses `std::thread::Builder::new().name(...)`,
      not `tokio::spawn_blocking`.
- [ ] `try_lock` (not `lock`) in `take_trigger_for_submit`.
- [ ] Existing tests updated to pass `None` for the new
      parameter.
- [ ] Three new tests present and passing.
- [ ] Single commit with message "Snapshot the display
      surface when the bug-report dialog opens." and the
      standard Signed-off-by / Assisted-By / Co-Authored-By
      trailer.

## Follow-up

Phase 3 adds `screenshot-region.png` (cropped from the
submit-time surface) when the user does region selection.
Phase 4 documents the completed behaviour in the user-facing
docs.
