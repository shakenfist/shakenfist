# Bug-report trigger-time snapshot (2026-04-23)

## Situation

Today the bug-report flow takes the screenshot at *submit*
time: the user hits F12, fills in the type + description,
clicks Capture, and (for Display reports) drags out a region;
`generate_bug_report` at `ryll/src/app.rs:923` then reads the
live surface pixels and encodes them.

This breaks for short-lived display artefacts. The user saw
"static" on the macbook on 2026-04-23, filled the form, and
by the time they submitted the glitch had already left the
screen. `screenshot.png` in
`ryll-bugreport-2026-04-23T08-22-47Z.zip` shows a clean
kernel-boot console, not the static the report is about.

The ring-buffer pcap covers the right time range, but without
a timestamp marker the analyst has no reliable way to locate
"when the user noticed it" in a long capture.

## Mission

Capture bug-report evidence at the moment the user opens the
dialog, not at submit time. Record a trigger timestamp in
metadata so the pcap can be correlated. Keep the submit-time
surface as a secondary image when the user does region
selection, so before/after is visible.

## Design

### When to trigger a capture

A trigger event is any of:

- F12 keypress (`ryll/src/app.rs:1244`).
- Status-bar bug-report button (`ryll/src/app.rs:1437`).
- USB menu bug-report shortcut (`ryll/src/app.rs:1783`).
- Channel-error auto-open (`ryll/src/app.rs:2063`).

The pedantic observer path (`bugreport.rs:995` /
`write_pedantic`) fires its own report with no user dialog;
it does not need the trigger-time mechanism because the
observer *is* the moment of interest. It still writes the
new metadata fields so the schema stays uniform.

### What to capture at trigger time

Only when the report type is (or defaults to) Display:

1. Pick the largest surface (same rule as
   `generate_bug_report` uses today).
2. Clone its RGBA pixels + `(width, height)`.
3. Record `SystemTime::now()` and
   `self.traffic.elapsed().as_secs_f64()`.
4. Hand the clone to a `std::thread::spawn` worker that
   PNG-encodes it via `bugreport::encode_png` and drops the
   output into an `Arc<Mutex<Option<Vec<u8>>>>` stored on the
   app.

Non-Display report types skip the snapshot entirely (space
saving — operator's call). They still record the trigger
timestamps.

If the user changes the report type in the dialog *after*
opening it, the snapshot taken at open-time may no longer be
appropriate:

- Opened as Display → switched to Input: discard the snapshot
  worker and its output on submit (don't include in zip).
- Opened as Input → switched to Display: we've missed the
  moment. Fall back to encoding the current surface at submit
  time, and mark in metadata
  (`triggered_uptime_secs: null`, perhaps, or an explicit
  `trigger_snapshot: "late"` field — to be decided in Phase
  2).

Simplest implementation: always take the snapshot on open,
regardless of selected type, and only *use* it for Display
submissions. Cheap and covers the type-switch edge case.

### What goes into the zip

Display reports:

- `screenshot.png` — **moment-of-trigger** surface. This is
  the substantive behavioural change.
- `screenshot-region.png` — region-crop of the **submit-time**
  surface, only when region selection produced a non-empty
  rectangle. Shows what was on screen when the user finished
  describing the issue.
- `region` stays in `metadata.json` as today (coordinates
  apply to both images if the surface size didn't change;
  otherwise to `screenshot-region.png` only).

Non-Display reports: no PNGs (unchanged).

### Metadata schema additions

`metadata.json` gains:

- `triggered_at` — ISO 8601 timestamp of when the dialog
  opened (for pedantic reports, same as `timestamp`).
- `triggered_uptime_secs` — session uptime at trigger.
- `submitted_uptime_secs` — session uptime at zip write.
  Existing `session_uptime_secs` is renamed to this for
  clarity, with a deprecation note. (Or keep
  `session_uptime_secs` as submit time and add only
  `triggered_*` — less churn; decide in Phase 1.)

### Fallback path

If the background PNG encode hasn't finished by submit time
(user clicked Capture faster than we encoded), fall back to
encoding the current surface synchronously at submit, same
as today. Log at debug. This keeps the code path bounded
and removes any risk of the UI thread blocking on
`Arc<Mutex<Option<Vec<u8>>>>`.

### Cancel path

On dialog cancel (`show_bug_dialog = false` with no
`dialog_action = Some(true)`): drop the `Arc`. The worker
finishes and its output is GC'd with the Arc. No explicit
cancellation primitive needed.

### RAM / disk cost

A 1024×768 RGBA buffer is ~3 MB. A 2560×1600 is ~16 MB.
These sit in RAM only from trigger until the worker finishes
encoding — on a modern CPU PNG-encoding ~16 MB is well under
a second, during which the user is typing a description.
After that we hold only the compressed bytes (tens to
hundreds of KB).

No temp file needed; if profiling later shows the clone cost
matters we can revisit. The trade-off was explicitly
discussed with the operator on 2026-04-23.

### Thread choice

`std::thread::spawn`, not `tokio::spawn_blocking`. PNG
encoding is CPU-bound, has no async dependencies, and should
stay out of the tokio reactor that drives the SPICE
channels. The thread ends when encoding returns and its
`JoinHandle` is dropped.

## Open questions

1. Do we rename `session_uptime_secs` or add new fields and
   keep it as submit time? Leaning "add new fields" — older
   bug reports in the wild will still parse.
2. Should `screenshot-region.png` be a crop of the
   submit-time surface (what I've proposed), or a cropped
   overlay on top of `screenshot.png`? The operator's
   original wording was "include that as a second image" —
   reading that literally means a separate image of the
   current surface, cropped to the region. Confirm in back-brief.
3. Where does the `Arc<Mutex<Option<Vec<u8>>>>` live on
   `struct App`? Natural home is next to the other
   bug-report UI fields at `ryll/src/app.rs:251-260`.
4. Does the traffic viewer (`PLAN-bug-reports-phase-06-traffic-viewer.md`)
   need to know about the new fields? Probably just for a
   "trigger" marker on the timeline — nice-to-have, not in
   scope here.

## Execution

Phased plan. Each phase lands as its own commit (or a small
stack of commits if the phase ends up larger than expected)
and its own PR. Phases run sequentially — phase 2 depends on
phase 1's metadata, phase 3 depends on phase 2's snapshot
plumbing, phase 4 documents the finished behaviour.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Metadata plumbing | [PLAN-bugreport-trigger-snapshot-phase-01-metadata.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot-phase-01-metadata/) | Complete |
| 2. Trigger snapshot  | [PLAN-bugreport-trigger-snapshot-phase-02-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot-phase-02-snapshot/) | Complete |
| 3. Region image      | [PLAN-bugreport-trigger-snapshot-phase-03-region.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot-phase-03-region/) | Complete |
| 4. Docs              | [PLAN-bugreport-trigger-snapshot-phase-04-docs.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot-phase-04-docs/) | Complete |

Phase 2 is the risky one (cross-thread lifetime management
around the Arc and the egui update loop); its detail plan
will call for worktree isolation so we can throw the
experiment away if the approach doesn't land cleanly. The
other phases are well-understood and can work directly in
the tree.

## Success criteria

* `pre-commit run --all-files` passes after each commit.
* `make test` passes.
* A display bug report captured during a transient artefact
  shows the artefact in `screenshot.png`, not the
  post-artefact clean surface.
* `metadata.json` contains a usable `triggered_uptime_secs`
  the analyst can subtract 2-3 seconds from to locate the
  event in the pcap.
* Non-Display reports are no larger than they were before.
* README and ARCHITECTURE describe the new behaviour so
  future contributors don't have to re-derive it.

## Future work

* Traffic-viewer marker for the trigger timestamp so the user
  can jump straight to the right point in `ryll --capture`
  playback.
* Optional: capture a short video loop (last N frames) at
  trigger time. Probably over-engineering unless the static
  comes back and the still snapshot still isn't enough.

## Bugs fixed during this work

To be filled in during implementation.

## Documentation index maintenance

Master plan — add a row under *Master plans* in
`docs/plans/index.md` dated 2026-04-23 with links to each
phase plan file, and add a line to `order.yml` so the plan
appears in the documentation navigation bar. Phase files
should *not* be added to `order.yml`.
