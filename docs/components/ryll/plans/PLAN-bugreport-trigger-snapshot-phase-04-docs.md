# Phase 4 — documentation

Parent: [PLAN-bugreport-trigger-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot/).

## Goal

Update the user-facing and contributor-facing documentation
so readers know the bug-report zip now carries trigger-time
timestamps and a second image for Display reports with a
region selection. Close out the master plan.

No behavioural code changes in this phase — everything
shipped in phases 1–3. This phase is prose plus plan-index
housekeeping.

## Scope

In scope:

- Update `docs/troubleshooting.md`'s "Bug Reports" section
  (user-facing) so the zip-contents list and the flow
  description match reality.
- Update `ARCHITECTURE.md`'s "Bug Report Assembly" and "Bug
  Report Dialog" sections (contributor-facing) so the zip
  layout, the always-capture-on-open behaviour, and the
  background PNG encoder get mentioned.
- Tweak the `README.md` feature-list bullet so the one-line
  summary reflects the "catch transient artefacts" angle,
  not just "captures a zip".
- Leave a breadcrumb in the original
  `docs/plans/PLAN-bug-reports.md` pointing at the
  trigger-snapshot plan so a future reader looking at the
  zip-layout diagram there knows the schema has moved on.
- Mark every phase of `PLAN-bugreport-trigger-snapshot.md`
  *Complete* and update the master-plan row in
  `docs/plans/index.md` accordingly.

Out of scope:

- Any SPICE-protocol-facing docs (e.g. `kerbside/docs/`) —
  this phase doesn't touch protocol behaviour.
- `AGENTS.md` module-layout comments — still accurate.
- `docs/configuration.md` — the F12 keyboard entry is
  still correct; no dialog keybinds changed.

## Grounding — what's there today

### `docs/troubleshooting.md` lines 344–413

Primary user-facing doc. Relevant passages:

- **Line 362-368** — report-type enumeration; lists
  Display/Input/Cursor/Connection/USB.
- **Line 372-375** — region-selection description ("drag a
  rectangle over the area of corruption").
- **Line 377-387** — zip-contents diagram. Currently says
  `screenshot.png — full display surface (Display reports
  only)` and has no mention of `screenshot-region.png` or
  the trigger timestamps.
- **Line 389-392** — output-location note ("capture dir or
  cwd").

### `ARCHITECTURE.md` lines 821–893

Contributor-facing. Relevant passages:

- **Line 821-852** — "Bug Report Assembly": has the
  canonical zip-layout diagram and describes
  `BugReport::new()` → `write_zip()`. Zip diagram lists
  `screenshot.png` only; doesn't mention
  `screenshot-region.png` or the new metadata fields.
- **Line 854–876** — "Bug Report Dialog": describes the
  dialog's two-pass pattern, F12 always being consumed, etc.
  No mention of `begin_trigger_snapshot` or the encoder
  thread.
- **Line 878-892** — "Display region selection": describes
  the drag UX; no mention that the region now produces a
  second image.

### `README.md` line 28

Single feature-list bullet:

```
- **Bug reports** - Press F12 or click "Report" to capture a
  self-contained zip with metadata, channel state, pcap
  traffic, screenshots, and runtime metrics ...; Display
  reports include interactive region selection to highlight
  corruption
```

Phrasing is accurate but buries the substantive phase 2–3
win: the zip now catches artefacts that fade before the user
submits.

### `docs/plans/PLAN-bug-reports.md` line 280-289

Historical plan. Has a zip-layout diagram listing
`screenshot.png` only. The plan is marked Complete — don't
rewrite history, just leave a forward-pointing note so a
reader looking at the old diagram doesn't think it's
authoritative.

### `docs/plans/PLAN-bugreport-trigger-snapshot.md` phase table

Status shows phase 1, 2, 3 as Complete; phase 4 is "Not
started". This phase's commit flips phase 4 to Complete and
(once the PR lands) the master-plan row in `index.md` moves
from "In progress" to "Complete".

## Design

### `docs/troubleshooting.md`

Replace the "What the zip file contains" fence with the
current layout, adding `screenshot-region.png` and the
trigger-timestamp pair inside `metadata.json`:

```
ryll-bugreport-YYYY-MM-DDTHH-MM-SSZ.zip
├── metadata.json         — report type, description, ryll
│                            version, platform, target,
│                            timestamp (submit), triggered_at
│                            (dialog-open), submitted uptime
│                            and triggered uptime
├── session.json          — FPS, bandwidth, surfaces, uptime
├── channel-state.json    — snapshot of the affected channel
├── traffic.pcap          — recent protocol traffic
├── screenshot.png        — full display surface captured at
│                            the moment the dialog opened
│                            (Display reports only)
├── screenshot-region.png — crop of the submit-time surface
│                            at the selected region (Display
│                            reports only, when a region was
│                            drawn)
└── runtime-metrics.json  — process / per-thread CPU + RSS
```

Also add, immediately below the file list, a short
"Timestamps" paragraph:

> `metadata.json` carries two timestamps: `timestamp` /
> `session_uptime_secs` are when the zip was written;
> `triggered_at` / `triggered_uptime_secs` are when the
> user opened the dialog. For short-lived artefacts the
> gap between the two is where you should be looking in
> `traffic.pcap` — subtract a few seconds from
> `triggered_uptime_secs`.

And add a second paragraph explaining the two-image layout
for Display reports, so the user knows which image shows
what:

> `screenshot.png` is captured the moment you open the
> dialog (before the artefact can fade); `screenshot-region.png`
> is produced after you drag the region and shows what
> was on screen at submit time, cropped to your selection.
> Compare the two to see what changed while you were
> typing the description.

### `ARCHITECTURE.md` "Bug Report Assembly"

Replace the zip-layout fence with the same updated layout
(without the verbose line-wrapped comments — match the
existing architecture-doc style), and add a short paragraph
explaining the trigger-vs-submit split:

> Display bug reports carry two PNGs. `screenshot.png` is
> the surface captured the moment the dialog opens — a
> background `std::thread` PNG-encodes the cloned RGBA
> while the user types a description. `screenshot-region.png`
> (when a region was drawn) is a crop of the submit-time
> surface at the selected rectangle, encoded on the UI
> thread after the user finishes the drag. The two images
> are deliberately different moments in time.
>
> Non-Display submissions drop the precomputed PNG even if
> one was captured — the dialog captures unconditionally
> on open (so an artefact doesn't fade while the user
> decides what to submit), but only includes the PNG when
> the user actually submits as Display.

Also update the report-types list ("Display, Input, Cursor,
and Connection") — it's missing Usb and Pedantic, though
that's a pre-existing oversight; out of scope unless the
author wants a drive-by. Recommendation: fix it as a
drive-by since it's on the same page.

### `ARCHITECTURE.md` "Bug Report Dialog"

Add a single paragraph at the end of the section noting the
trigger-snapshot state:

> On dialog open, `RyllApp::begin_trigger_snapshot` clones
> the largest surface's RGBA and spawns a named
> `std::thread` (`ryll-bugreport-png`) that PNG-encodes
> into a shared `Arc<Mutex<Option<Result<Vec<u8>>>>>`. The
> submit path (`finish_bug_report` →
> `take_trigger_for_submit`) consumes the encoded bytes
> via `try_lock`, falling back to a live encode if the
> encoder hasn't finished. Close-without-submit paths
> (Escape, Cancel, F12 toggle-off) drop the `Arc`; the
> thread finishes into what becomes garbage.

### `README.md` line 28

Replace the bug-reports bullet with a tighter version that
leads on what makes phase 2–3 worth documenting:

```
- **Bug reports** - Press F12 or click "Report" to capture a
  self-contained zip with metadata, channel state, pcap
  traffic, runtime metrics, and a screenshot taken the
  moment the dialog opened so transient display artefacts
  survive the form-filling delay; Display reports with a
  region selection also include a crop of the submit-time
  surface
```

Wrap at 120 cols; the existing bullets in the list hit
~150 on the widest lines so matching that is fine.

### `docs/plans/PLAN-bug-reports.md`

Minimal breadcrumb — add one paragraph above the zip-layout
fence:

> **Note (2026-04-23):** the zip schema below was the phase
> 1–7 shape. The trigger-snapshot master plan
> ([PLAN-bugreport-trigger-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot/))
> added a second image (`screenshot-region.png`) and two
> new `metadata.json` fields (`triggered_at`,
> `triggered_uptime_secs`). See `docs/troubleshooting.md`
> for the current user-facing layout and `ARCHITECTURE.md`
> for the contributor view.

Don't edit the existing diagram; leave it as a historical
record of the phase-1 design.

### Phase table + index status

In `PLAN-bugreport-trigger-snapshot.md`:

- Flip phase 4's row from "Not started" → "Complete".

In `docs/plans/index.md`:

- The master-plan row for Bug-report trigger snapshot moves
  from "In progress" to "Complete".

These updates land **in the same commit as the doc
changes** (because they describe the same landed work), not
in a separate housekeeping commit.

## Open questions

None. The doc-update list and the wording are concrete and
reviewable; nothing architectural to decide.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 4a   | low    | haiku  | none      | Apply the doc changes in the *Design* section verbatim: update `docs/troubleshooting.md`'s "Bug Reports" section with the new zip layout, the timestamps paragraph, and the two-image paragraph; update `ARCHITECTURE.md`'s "Bug Report Assembly" and "Bug Report Dialog" sections; tighten the `README.md` bug-reports feature bullet; add the breadcrumb paragraph to `docs/plans/PLAN-bug-reports.md`; flip phase 4's status to Complete in `PLAN-bugreport-trigger-snapshot.md`; flip the trigger-snapshot master-plan row to Complete in `docs/plans/index.md`. Single commit with message `Document trigger-time snapshot in bug reports.`. Run `pre-commit run --all-files` (shellcheck and gitleaks still run on doc PRs). No `make test` needed — no Rust changed. |

Low effort, haiku is sufficient — the prose is prescribed
and the edits are mechanical. If haiku struggles with the
Markdown fences (unlikely), escalate to sonnet.

### No new tests

Doc-only phase. Existing tests stay green by construction
(nothing in the code changes).

## Success criteria

* `pre-commit run --all-files` passes (shellcheck and
  gitleaks still run).
* `docs/troubleshooting.md`'s zip-layout fence lists
  `screenshot-region.png` and the trigger-timestamp
  context.
* `ARCHITECTURE.md` describes both images and the
  background encoder thread.
* `README.md`'s bug-reports bullet mentions the
  trigger-time snapshot.
* `PLAN-bugreport-trigger-snapshot.md` shows all four
  phases as Complete.
* `docs/plans/index.md` shows the master plan row as
  Complete.
* No code files were modified.

## Review checklist

- [ ] `docs/troubleshooting.md` zip-layout updated; two new
      paragraphs on timestamps and the two-image layout.
- [ ] `ARCHITECTURE.md` zip-layout updated; paragraph on
      trigger-vs-submit added under "Bug Report Assembly";
      paragraph on the encoder thread added under "Bug
      Report Dialog".
- [ ] `README.md` bullet tightened.
- [ ] `docs/plans/PLAN-bug-reports.md` breadcrumb present.
- [ ] `PLAN-bugreport-trigger-snapshot.md` phase table
      shows Complete for all four phases.
- [ ] `docs/plans/index.md` master-plan row shows
      Complete.
- [ ] Commit message is "Document trigger-time snapshot in
      bug reports." with the standard trailer.
- [ ] No `.rs` file touched in this commit.

## Follow-up

None — this is the last phase of the master plan. After
this lands:

- The trigger-snapshot master plan is Complete.
- The static-artefact debugging workflow from
  `PLAN-macbook-bugreport-fixes.md`'s follow-up section is
  unblocked: a future macbook "static" report will contain a
  trigger-time screenshot that actually captures the
  artefact, and analysts can use `triggered_uptime_secs` to
  jump to the right spot in the pcap.
