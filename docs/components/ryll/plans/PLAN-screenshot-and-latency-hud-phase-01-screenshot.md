# Phase 1: Screenshot hotkey and save dialog

Parent plan: [PLAN-screenshot-and-latency-hud.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud/)

## Goal

Add a way for the user to save the current display surface(s)
as PNG files, triggered by either F8 or a status-bar button,
using a native file save dialog. With multi-monitor sessions,
write one PNG per surface with `-1`, `-2`, ... suffixes
appended before the extension.

## Background

All building blocks already exist:

- `bugreport::encode_png(pixels, w, h) -> anyhow::Result<Vec<u8>>`
  at [ryll/src/bugreport.rs:584](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L584).
  It is `pub(crate)` already; no visibility change needed.
- `Surface::pixels() -> &[u8]` at
  [ryll/src/display/surface.rs:107](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L107)
  returns the RGBA buffer. `Surface` also has public `width`
  and `height` fields.
- `App.surfaces` is a `HashMap<(u8, u32), DisplaySurface>`
  (the key is `(channel_id, surface_id)`; see
  [app.rs:134](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L134)). Iteration order is
  unspecified — for multi-monitor output we should sort by
  the key tuple so the `-1`/`-2` suffixes are deterministic.
- `rfd::FileDialog` is in use at
  [ryll/src/app.rs:1378](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1378) and
  [ryll/src/app.rs:1523](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1523) — copy that
  pattern (`save_file()` rather than `pick_file()` /
  `pick_folder()`).
- F11 (Traffic) and F12 (Report) are already bound at
  [ryll/src/app.rs:917-931](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L917-L931). Add
  F8 alongside, and follow the same exclusion: don't trigger
  during region-selection mode.
- The bottom-right of the top status bar is where buttons
  live ([app.rs:1064-1078](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1064-L1078)) —
  add a "Screenshot" button next to "Report".

## Constraints and edge cases

- **Headless mode:** out of scope (per master plan open
  question 1). The keybinding and button only exist in the
  GUI build path. Headless mode shares some code; make sure
  any new keyboard handler stays inside the egui `App`.
- **No surfaces yet:** if `App.surfaces` is empty (very
  early in the session), the hotkey/button should show a
  transient status message rather than save an empty file.
  Use the existing `bug_status_message: Option<(String,
  Instant)>` mechanism at [app.rs:1080-1086](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1080-L1086)
  for user feedback.
- **Save dialog cancelled:** `rfd::FileDialog::save_file()`
  returns `Option<PathBuf>`. None means user cancelled — do
  nothing, no error message.
- **Default filename:** `ryll-screenshot-{timestamp}.png`
  using `bugreport::filename_timestamp()` at
  [bugreport.rs:577](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L577) (already
  filename-safe — colons replaced with hyphens). The project
  deliberately avoids the `chrono` crate; do **not** add it.
- **Multi-monitor naming:** if `surfaces.len() == 1`, save
  exactly to the user-chosen path. If `> 1`, strip the
  extension from the chosen path and append `-1.png`,
  `-2.png`, ... in surface-id order. Document this in the
  status message after save ("Saved 2 PNGs:
  /tmp/foo-1.png, /tmp/foo-2.png").
- **Errors:** `encode_png` and `std::fs::write` can both
  fail. Surface failures via the `bug_status_message`
  pattern, not panics. Match the error-handling style at
  [app.rs around bug report save](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | Add a `save_screenshots(&self, base_path: PathBuf) -> anyhow::Result<Vec<PathBuf>>` method on `RyllApp` in [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs). Iterate `self.surfaces` collected and sorted by key (`(u8, u32)`); call `bugreport::encode_png(s.pixels(), s.width, s.height)` for each. If `surfaces.len() == 1`, write directly to `base_path`. Otherwise, derive per-surface paths via the helper from step 1d. Use `std::fs::write` for file writes. Return `anyhow::bail!` if `surfaces` is empty. |
| 1b   | medium | sonnet | none     | In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs), add an F8 hotkey handler next to the existing F11/F12 handlers around line 917-931. F8 should not trigger during region-selection mode (mirror the F11/F12 exclusion). When pressed, open `rfd::FileDialog::new().set_file_name(&default_name).save_file()` where `default_name` is `format!("ryll-screenshot-{}.png", bugreport::filename_timestamp())`. If the user picks a path, call `save_screenshots()` and set `self.bug_status_message = Some((msg, Instant::now()))` with a success or failure string (mirror the existing bug-report save pattern — search for `bug_status_message =` to find precedent). On empty surfaces, set the status message to "No display surface to capture yet". |
| 1c   | low    | sonnet | none     | In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) around line 1064-1078 (the status-bar button row containing "Traffic", "USB", "Folders", "Report"), add a `ui.small_button("Screenshot")` between "Folders" and "Report". On click, perform the same flow as the F8 handler — refactor the F8 handler body into a private `fn open_screenshot_dialog(&mut self)` helper so the button click and the keypress share one code path. |
| 1d   | low    | sonnet | none     | Add unit tests in [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) (or wherever the helper lands) for the multi-surface filename logic. Test cases: `("foo.png", 1) → ["foo.png"]`, `("foo.png", 3) → ["foo-1.png", "foo-2.png", "foo-3.png"]`, `("foo", 2) → ["foo-1.png", "foo-2.png"]` (no extension to strip), `("foo.bar.png", 2) → ["foo.bar-1.png", "foo.bar-2.png"]` (only strip the last extension). Pull the path-derivation logic into a small pure helper to make it testable without touching the filesystem. |

## Success criteria for this phase

- F8 in the GUI opens a save dialog, writes PNG(s), shows a
  status message in the bottom-right of the top status bar.
- "Screenshot" button next to "Report" in the status bar
  does the same thing.
- With `--monitors 2`, F8 writes two PNGs with `-1`/`-2`
  suffixes.
- Cancelling the dialog leaves no files and no error.
- Empty-surfaces case shows a status message, doesn't panic.
- `pre-commit run --all-files` passes; `make test` passes.
- Commits: one for the helper + tests (1a + 1d combined),
  one for the GUI wiring (1b + 1c combined). Two commits
  total for this phase, both following the project's
  Co-Authored-By format with model and effort level.
