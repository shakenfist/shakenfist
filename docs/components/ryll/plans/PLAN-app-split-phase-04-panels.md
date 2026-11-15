# Phase 4: Split the draw code into `app/ui/`

Master plan: [PLAN-app-split.md](/components/ryll/plans/PLAN-app-split/).

## Planning effort

Planned at high effort. The phase looks like the moves phases 2 and
3 did, and most of it is, but three parts are not. Nesting a module
under `app/` needs a visibility form neither earlier phase used, and
getting it wrong is a compile error in every step at once. The
status bar is a single 373-line `ui.horizontal` closure that cannot
move as one function. And the central panel sets two flags that
later blocks read, so it is the one block whose extraction changes a
signature rather than just a location.

Review effort belongs on draw order. Everything else here is
checkable by script; the order the panels and windows are drawn in
is a contract with egui that no test in this repository exercises.

## Scope

In:

* `ryll/src/app/ui/` -- a new module directory with `mod.rs` and ten
  children: `cursor.rs`, `dialogs.rs`, `keys.rs`,
  `notifications_panel.rs`, `region_select.rs`, `stats_bar.rs`,
  `surface.rs`, `traffic_panel.rs`, `usb_panel.rs`,
  `webdav_panel.rs`.
* Reducing `eframe::App::ui` to the sequence of calls that drives
  them, with the draw order stated in a comment where it is
  load-bearing.
* The `&mut self` resize callers `maybe_send_monitors_resize` and
  `send_pending_guest_size`, plus the viewport-fit block at the top
  of `ui`, which join the pure helpers phase 2 already moved to
  `app/resize.rs` (survey finding 7).
* The eight tests in `app/mod.rs` that belong to moved code, moved
  verbatim with it.
* `ARCHITECTURE.md`'s `ryll/src/` tree, `app/mod.rs`'s file-header
  comment, and one line in `AGENTS.md` for the visibility rule a
  nested module introduces (decision 3).

Out:

* `logic`, `on_exit` and the `impl eframe::App for RyllApp` block
  itself, which stay in `app/mod.rs` (decision 2). `logic` is 156
  lines but under 100 once comments are discounted, so phase 6's
  lint will not flag it and this phase does not touch it (survey
  finding 8).
* `RyllApp::new` (243 lines by clippy's count) and
  `connection.rs`'s `reconnect` (154). They are the only two
  functions in `app/` that phase 6's lint will flag once `ui` is
  gone; `new` is phase 5's field
  regrouping and `reconnect` is phase 6's own problem (survey
  finding 9).
* Enabling `clippy::too_many_lines`. This phase runs it by hand as a
  done-criterion; phase 6 wires it into `make lint`.
* `clipboard`, `trigger_paste`, `push_notification`,
  `push_connection_event`, `handle_cadence` and `window_focused`,
  which stay in `app/mod.rs` (decision 8).
* Any behaviour change. Panels keep reaching into `self` exactly as
  they do today; grouping those fields is phase 5.

## What the survey found

Surveyed against `develop` at `32d8b22`, after #714 (phase 3) and
#717 (its review follow-ups) merged. `ryll/src/app/mod.rs` is 3,230
lines, of which `ui` is 1,691 (lines 1241-2931).

1. **The master plan's file list does not cover `ui`.** Its nine
   files account for about 1,100 of the 1,691 lines. The central
   panel -- the surface itself, 149 lines at 2364-2512 -- has no
   file in the list, and neither does the viewport-fit block at the
   top. The full inventory, with the destination this phase gives
   each block:

   | Lines | Block | Takes | Destination |
   |-------|-------|-------|-------------|
   | 1241-1255 | `ctx` binding, shutdown early return | ctx | stays in `ui` |
   | 1257-1261 | input gate while the bug dialog is open | ctx | `keys.rs` |
   | 1263-1289 | viewport fit, then `maybe_send_monitors_resize` | ctx | `resize.rs` |
   | 1291-1364 | hotkeys: Esc, F12, F11, F8, Esc, Ctrl+Alt+V | ctx | `keys.rs` |
   | 1366-1379 | `handle_input`, held-key end of frame, screenshot dialog | ctx | `keys.rs` |
   | 1381-1390 | traffic-entry refresh | -- | `traffic_panel.rs` |
   | 1392-1764 | status bar panel | ui, ctx | `stats_bar.rs` |
   | 1765-1855 | traffic viewer panel | ui | `traffic_panel.rs` |
   | 1857-2003 | notifications panel, then mark-all-read | ui | `notifications_panel.rs` |
   | 2005-2245 | USB panel, with its pre-draw polling | ui, ctx | `usb_panel.rs` |
   | 2247-2362 | WebDAV panel | ui, ctx | `webdav_panel.rs` |
   | 2364-2512 | central panel: the surface | ui, ctx | `surface.rs` |
   | 2514-2630 | bug report window | ctx | `dialogs.rs` |
   | 2632-2658 | paste error window | ctx | `dialogs.rs` |
   | 2660-2769 | region selection overlay | ctx | `region_select.rs` |
   | 2771-2836 | cursor texture and overlay | ctx | `cursor.rs` |
   | 2838-2859 | protocol gaps window | ctx | `dialogs.rs` |
   | 2860-2920 | reconnect modal | ctx | `dialogs.rs` |
   | 2922-2928 | hide the host cursor | ctx | `cursor.rs` |
   | 2930 | `observe_ui_state` | ctx | stays in `ui` |

   Four items outside `ui` move with their blocks: `handle_input`
   (928-962), `release_guest_keys` (963-974) and
   `keep_keyboard_for_guest` (1055-1063) to `keys.rs`;
   `send_pending_guest_size` (994-999) and
   `maybe_send_monitors_resize` (1000-1040) to `resize.rs`;
   `default_arrow_cursor` (2935-2985) to `cursor.rs`; and
   `TRAFFIC_VIEWER_MAX_ENTRIES` / `TRAFFIC_VIEWER_REFRESH_MS` (83,
   86) to `traffic_panel.rs`. Phase 3's scope section already
   promised the last two and `default_arrow_cursor` to this phase,
   and all three are referenced only from `app/mod.rs`, so nothing
   else has to change to move them. The master plan's phase 4
   section has been corrected to name `surface.rs` and to stop
   implying the list is complete.

2. **"each a `fn draw_*(&mut self, ui: &mut egui::Ui)`" is wrong for
   half of them.** egui 0.35 unified the panel types and made
   `Panel::show` take `&mut Ui`, but `Window::show` still takes
   `&Context`. Six blocks need the root `&mut Ui`: the status bar
   (`Panel::bottom(…).show_collapsible(ui, …)`, 1444), the traffic,
   notifications, USB and WebDAV side panels
   (`Panel::right(…).show(ui, …)` at 1767, 1859, 2070, 2284) and
   the central panel (2370). The four windows -- bug report, paste
   error, protocol gaps, reconnect modal -- take `ctx` and never
   touch the root `ui`, and neither do the hotkeys, the region
   overlay, the cursor overlay or the viewport fit, which work
   through `ctx.input`, `ctx.output_mut` and `ctx.layer_painter`.
   The master plan's section has been corrected. Both signatures
   are safe to pass together: `ctx` is a borrow of the `ctx_owned`
   clone made at 1249, not of `ui`, which is why that clone exists.

3. **Nesting needs a visibility form phases 2 and 3 never used.**
   The twelve existing children of `app/` are siblings of `mod.rs`,
   so `pub(super)` makes an item visible to `app` and therefore to
   every sibling. From `app::ui::stats_bar`, `pub(super)` means
   visible in `app::ui` only -- not in `app`, where `ui` itself
   lives and does the calling. The form that works is
   `pub(in crate::app)`, which grants exactly the scope `pub(super)`
   grants a direct child: module `app` and all its descendants. A
   probe compiled in the `ryll-dev` container confirms all three
   cases this phase needs -- a grandchild's inherent method called
   from `app`, the same method called from a *sibling* of `ui`
   (`app::screenshots`), and a grandchild calling a private method
   of `app` -- and is recorded in decision 3.

4. **Only two locals escape their block.** `pointer_on_surface` and
   `open_channel_bug_report` are declared at 2365-2366, written
   inside the central panel, and read afterwards at 2509 and 2926.
   Every other local in `ui` -- `is_maximized`, `show_stats`,
   `stats_frame`, `streaming_state`, `panel_frame`, `dialog_held`,
   `paste_triggered`, `modifiers`, `dialog_action`,
   `paste_dialog_action`, `wants_reconnect` -- is read only inside
   the block that declares it, so every other extraction is a pure
   move of a `{ … }` body.

5. **The status bar cannot move as one function.** 1392-1764 is one
   `ui.horizontal` closure (1445) whose right-hand group is a nested
   `with_layout(right_to_left, …)` (1536). At 373 lines it would be
   the largest function in `app/` the moment it landed, so it splits
   along the boundaries the code already has: metrics 1446-1535,
   volume 1537-1551, streaming indicator 1552-1636, agent indicator
   1637-1645, bandwidth sparkline 1646-1679, bell 1680-1707, and the
   `☰` menu of panel toggles 1708-1763. The `streaming_state`
   classification at 1405-1434 is logic, not drawing: it can push a
   notification, and the comment at 1401-1404 records that it must
   run *before* the bell draws so the bell sees it on the same
   frame. It becomes a method called at the top of the status bar's
   draw, which keeps that ordering by construction.

6. **The cursor code is in two pieces, separated in the draw order.**
   The texture setup and overlay are 2771-2836; hiding the host
   cursor is 2922-2928, after the protocol gaps window and the
   reconnect modal. `cursor.rs` therefore gets two entry points
   called at their present positions, not one.

7. **Phase 2 moved the resize helpers but left their callers.**
   `app/resize.rs` holds six free functions (`compute_auto_resize`,
   `compute_outgoing_resize`, `resolution_notification_due`,
   `send_guest_size`, `auto_fit_size_acceptable`,
   `is_primary_surface`) and twelve tests, but the `&mut self`
   methods that call them -- `maybe_send_monitors_resize`,
   `send_pending_guest_size` -- and the viewport-fit block in `ui`
   stayed in `mod.rs`. Finishing that seam costs 74 lines and no new
   file.

8. **`logic` does not need this phase.** It is 156 lines at
   1085-1240, but the inline work in it is three short blocks
   (ticket-expiry warning, auto-reconnect tick, resolution
   notification) and the rest is comments and delegation. Counted
   the way `clippy::too_many_lines` counts -- non-blank,
   non-comment body lines -- it is under 100 and the lint will not
   flag it. An earlier verbal estimate in this session said phase 4
   should cover `logic` as well as `ui`; measurement says otherwise.

9. **Phase 6's baseline, measured with the lint itself.** Running
   `cargo clippy --frozen -p ryll --features gui,digest-decode --
   -W clippy::too_many_lines` in the devcontainer reports three
   functions under `ryll/src/app/`: `ui` (1,389/100),
   `RyllApp::new` (243/100) and `connection.rs`'s `reconnect`
   (154/100). After this phase the first is gone and nothing new
   exceeds 100, which leaves phase 6 two call sites in `app/` to
   decide about rather than three -- plus six elsewhere in `ryll`
   (`bugreport.rs` twice, `capture.rs`, `web/signalling.rs` and
   `main.rs` twice) and twenty-five in the sibling crates, which is
   phase 6's scoping problem and not this phase's.

10. **Eight tests in `mod.rs`, five of which move.** The three
    cursor tests and their `surface_contains_pointer` helper
    (3063-3131) go to `cursor.rs`; the two status-button tests with
    `key_press` and `status_button_clicks` (3132-3206) go to
    `keys.rs`, which is where `keep_keyboard_for_guest` -- the
    function they exercise -- is going. Note that
    `surface_contains_pointer` is a replica of the containment test,
    written inside the test module: those three tests pin egui's
    behaviour, not this crate's, which is worth knowing before
    treating them as coverage of `cursor.rs`. The two `logic` tests
    and `volume_control_round_trip` stay (decision 9).

11. **One method needs the cross-subtree form.**
    `release_guest_keys` is called from `app::screenshots`
    (`screenshots.rs`) as well as from the hotkey block, so once it
    is in `app::ui::keys` it is the one moved item whose caller is
    neither `app` nor a descendant of `app::ui`. `pub(in crate::app)`
    covers it; the probe in finding 3 includes exactly this case.

12. **The tree does not currently pass its own lint, for reasons
    that predate this phase.** `make lint` fails on `develop` under
    rustc/clippy 1.100.0 (2026-11-09) with `needless_range_loop` in
    `shakenfist-spice-usbredir/src/messages.rs`, and the CI Lint
    lane has failed on every pull request since the devcontainer
    image picked that toolchain up -- the last green run is #717's
    merge-queue run. The code is unchanged since the crate was
    extracted; clippy is what moved. It is nothing to do with
    `app.rs`, so this phase does not fix it: filed as
    shakenfist/ryll#722 with the one-line fix. It does gate the
    phase's pull request, because that pull request cannot go green
    until #722 lands, and it gates `pre-commit run --all-files`
    locally for anyone with a current image.

Nothing else the master plan's phase 4 section claims turned out to
be wrong: the nine file names it gives are all sound as far as they
go, the draw-order constraint it names is real and already
commented at 1392, and the panels do all still reach into `self`
the way it says.

## Decisions

1. **`app/ui/` as a nested directory, not flat `app/ui_*.rs`
   files.** The master plan said `app/ui/` and the alternative has
   got worse since: flat would put 23 files in one directory and
   would shelve ten `ui_*.rs` draw files next to `ui_state.rs`,
   which is state, not drawing. The cost is decision 3.

2. **`ui` stays in `app/mod.rs`; the `impl eframe::App for RyllApp`
   block is not moved.** A trait impl cannot be split across files,
   so moving `ui`'s new body into `app/ui/mod.rs` would drag
   `logic` and `on_exit` with it -- 180 lines of per-frame logic
   into a module called `ui`, and `window_focused` and two tests
   after them. Keeping the block whole has a price: because `ui`
   lives in `app` and the draw methods live in `app::ui::*`,
   `pub(super)` is not enough for them. That is decision 3, and it
   is the cheaper of the two prices.

3. **Every method `ui` calls across the boundary is
   `pub(in crate::app)`, and nothing is wider.** The master plan's
   decision 7 says "`pub(super)` and nothing wider"; this is the
   same scope with a different spelling -- module `app` and its
   descendants -- so it honours that decision rather than relaxing
   it. `pub(in super::super)` is also legal and was rejected as
   harder to read. No `pub(crate)` and no bare `pub` is added
   anywhere in `app/ui/`. Verified by probe before planning:

   ```rust
   mod app {
       pub struct RyllApp { n: u32 }
       mod screenshots {
           pub(super) fn save(a: &mut super::RyllApp) { a.release_guest_keys(); }
       }
       mod ui {
           mod keys {
               impl crate::app::RyllApp {
                   pub(in crate::app) fn release_guest_keys(&mut self) { self.n += 10; }
               }
           }
       }
   }
   ```

   compiles and runs, as does the grandchild calling a private
   method of `app`. Children import what they need explicitly --
   `use crate::app::RyllApp;` and so on -- matching phase 2's
   convention; no file-scope `use super::*;` glob is introduced,
   because the workspace has none.

4. **The central panel returns what escapes it; it does not grow
   two `RyllApp` fields.** `draw_surface` returns a small
   `SurfaceOutcome { pointer_on_surface: bool, open_channel_bug_report: bool }`
   declared in `surface.rs`. Adding fields would push against phase
   5, whose whole job is to shrink the struct, and would turn two
   frame-scoped flags into state with a lifetime longer than the
   frame.

5. **The status bar splits into seven draws plus a classification
   method** along the boundaries in survey finding 5. This is the
   one step that is not a block move, and the one where the diff
   will not read as a pure relocation: the bodies are unchanged but
   their indentation and their closure nesting are not.

6. **Draw order is the contract.** The call sequence in `ui` after
   this phase is, exactly: keyboard-focus gate, viewport fit,
   hotkeys, traffic refresh, status bar, traffic panel,
   notifications panel, USB panel, WebDAV panel, surface, bug
   report window, paste error window, region select, cursor
   overlay, protocol gaps window, reconnect modal, hide host
   cursor, `observe_ui_state`. Note the first two: the keyboard
   focus gate sits *before* the viewport fit today and the hotkeys
   *after* it, which is why `keys.rs` keeps two entry points
   instead of one (step 4f). The comment in `ui` says
   why the status bar must precede the central panel (egui reserves
   panel space in draw order) and that the window order is z-order.
   No step may reorder these, including the two steps that would
   read more naturally grouped -- the four windows are not adjacent
   and `dialogs.rs`'s four methods are called at four separate
   points.

7. **The resize callers go to `app/resize.rs`, not under `ui/`.**
   They are the missing half of a seam phase 2 opened, and the
   viewport fit is not drawing: it reads the viewport and sends a
   display size. `resize.rs` reaches 526 lines, within the 800-line
   ceiling the phase keeps.

8. **`keys.rs` takes the input subsystem, not just the hotkeys.**
   The master plan scoped it to "the global hotkey handling at the
   top of `ui`", which is 89 lines that call straight into
   `handle_input`, `release_guest_keys` and
   `keep_keyboard_for_guest` -- and the two tests that exist for any
   of this exercise the last of those. Splitting them across two
   files would leave the tests pointing at a different module from
   the code. `clipboard` and `trigger_paste` stay in `mod.rs`: they
   are clipboard plumbing that the Ctrl+Alt+V arm happens to call,
   and a descendant can call an ancestor's private method unchanged.

9. **`volume_control_round_trip` stays in `mod.rs`.** It pins
   `VolumeControl`'s get/set contract because `RyllApp::reconnect`
   must not reset the user's slider. The slider is in the status
   bar, but the behaviour it guards is in `connection.rs`, so
   neither `stats_bar.rs` nor a move makes it more findable than
   leaving it where the author put it.

10. **One commit per step, each one compiling.** Every step removes
    its block from `ui` and inserts the call in the same commit, so
    the tree builds at each one and the move check can run against
    `HEAD~1`. No step leaves a method defined but uncalled.

## Step plan

Steps run sequentially: every one of them edits `app/mod.rs`, so
they cannot share the worktree. Sub-agents work in the phase
worktree with no isolation, as in phases 2 and 3, and leave the work
uncommitted for review in the management session.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Create `app/ui/` and move the four side panels into it |
| 4b | high | opus | none | Move and split the status bar |
| 4c | high | opus | none | Move the central panel, returning what escapes it |
| 4d | medium | sonnet | none | Move the four windows into `dialogs.rs` |
| 4e | medium | sonnet | none | Move the region overlay and the cursor into their files |
| 4f | high | opus | none | Move the input subsystem into `keys.rs` |
| 4g | medium | opus | none | Finish `ui`, the resize seam, and the documentation |
| 4h | high | opus | none | Bring every draw method under the length lint |

### Step 4a: `app/ui/` and the four side panels

Create `ryll/src/app/ui/mod.rs` declaring its children
alphabetically, and add `mod ui;` to `app/mod.rs`'s alphabetical
`mod` list (between `trigger_snapshot` and `ui_state`). `app/ui/`
inherits `app`'s `#[cfg(feature = "gui")]` gate from the `mod app;`
declaration, so no file in it carries a feature attribute.

Move four blocks out of `eframe::App::ui` in `app/mod.rs`, each
into a `pub(in crate::app) fn` in its own file, replacing each with
a single call at the point the block was:

* 1381-1390 (the refresh gate) and 1765-1855 to
  `traffic_panel.rs`, as `refresh_traffic_entries` and
  `draw_traffic_panel`, plus the two `TRAFFIC_VIEWER_*` constants
  from lines 83 and 86 of `mod.rs`.
* 1857-2003 to `notifications_panel.rs` as `draw_notifications_panel`.
  Keep the mark-all-read transition at 1995-2003 inside it, in place:
  it must run after the panel and it reads
  `notifications_panel_was_open_last_frame`.
* 2005-2245 to `usb_panel.rs` as `draw_usb_panel`, including the
  pre-draw enumeration and file-picker polling at 2007-2065 -- it
  only runs when `show_usb_panel`, so it is part of the same block.
* 2247-2362 to `webdav_panel.rs` as `draw_webdav_panel`.

All four take `ui: &mut egui::Ui`; `draw_usb_panel` and
`draw_webdav_panel` also take `ctx: &egui::Context`, each for a
`request_repaint_after`. The `if
self.show_* {` test moves inside the method rather than staying at
the call site, so `ui` reads as an unconditional sequence. Import
explicitly (`use crate::app::RyllApp;` and the types each file
needs); do not add a `use super::*;` glob -- the workspace has
none. Bodies are moved verbatim: the only edits are the
dedent, the method header, and imports.

Verify with the move check in the definition of done, then `make
lint` and `make test`.

Commit subject: `Move the side panels into app/ui/.`

### Step 4b: the status bar

Move 1392-1764 to `app/ui/stats_bar.rs` as
`pub(in crate::app) fn draw_stats_bar(&mut self, ui: &mut egui::Ui)`,
splitting it as survey finding 5 and decision 5 describe: the
`streaming_state` computation at 1405-1434 becomes
`fn classify_streaming_state(&mut self) -> StreamingState` called
first, and the closure contents become private methods on `RyllApp`
in the same file -- metrics 1446-1535, volume 1537-1551, streaming
indicator 1552-1636 (taking `&StreamingState`), agent indicator
1637-1645, sparkline 1646-1679, bell 1680-1707, menu 1708-1763.
Each takes the `&mut egui::Ui` of the closure it came from, so the
nesting of `ui.horizontal` and `with_layout` stays exactly as it is
and only the bodies move out.

The sub-methods are private (no `pub(in …)`): they are called only
from inside `stats_bar.rs`. Keep every comment with the code it
explains, including the three long ones at 1552-1560 (why the glyph
is `▶`), 1571-1573 (why the tooltip strings are built inside the
hover closure) and 1401-1404 (why the classification runs before
the bell).

No statement may change order, and no `if` condition may be
rewritten. Verify that `cargo clippy -W clippy::too_many_lines`
reports nothing in `stats_bar.rs`.

Commit subject: `Split the status bar into its own module.`

### Step 4c: the surface

Move 2364-2512 to `app/ui/surface.rs` as
`pub(in crate::app) fn draw_surface(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) -> SurfaceOutcome`,
where `SurfaceOutcome` is a `pub(in crate::app) struct` with the two
`bool` fields decision 4 names. `panel_frame` (2367) moves inside.
The follow-up at 2508-2512 -- opening the bug report dialog for a
channel error -- stays in `ui`, because it reads the returned flag
and runs before the dialog draws; it reads
`outcome.open_channel_bug_report` instead of the local.
`pointer_on_surface` is read by the host-cursor block at 2926, which
step 4e moves, so until then `ui` keeps it in a local bound from the
return value.

Nothing in the body changes except the two assignments to the
escaping locals, which become fields of the returned value. Check
that both reads still see the same value they do today: the flag is
written in several places inside the closure (2415, 2451, 2471).

Commit subject: `Move the surface panel into app/ui/.`

### Step 4d: the four windows

Move all four `egui::Window` blocks to `app/ui/dialogs.rs`, each a
`pub(in crate::app) fn` taking `ctx: &egui::Context`:

* 2514-2630 bug report, as `draw_bug_report_dialog`. Keep the
  two-pass shape: render into `dialog_action`, then act on it
  outside the closure (2603-2630). `dialog_held` (2516) moves in.
* 2632-2658 paste error, as `draw_paste_error_dialog`, including
  the Escape dismissal at 2652-2658.
* 2838-2859 protocol gaps, as `draw_protocol_gaps_window`.
* 2860-2920 reconnect modal, as `draw_reconnect_modal`, including
  the `wants_reconnect` follow-up at 2918-2920.

The four calls stay at the four positions they occupy today
(decision 6); they are not adjacent and must not be grouped.

Commit subject: `Move the dialog windows into app/ui/.`

### Step 4e: region select and the cursor

Move 2660-2769 to `app/ui/region_select.rs` as
`pub(in crate::app) fn draw_region_select(&mut self, ctx: &egui::Context)`.
Move 2771-2836 and 2922-2928 to `app/ui/cursor.rs` as two methods --
`draw_cursor_overlay(&mut self, ctx: &egui::Context)` and
`hide_host_cursor(&mut self, ctx: &egui::Context, pointer_on_surface: bool)`
-- called at their present positions, with the protocol gaps window
and the reconnect modal still between them (survey finding 6).
`default_arrow_cursor` (2935-2985) moves to `cursor.rs` as a private
free function, and the three cursor tests with their
`surface_contains_pointer` helper (3063-3131) move into a
`#[cfg(test)] mod tests` in the same file, verbatim, keeping their
section comment.

Commit subject: `Move the cursor and region overlays into app/ui/.`

### Step 4f: the input subsystem

Move to `app/ui/keys.rs`: the input gate at 1257-1261, the hotkey
block at 1291-1364, the `handle_input` call and held-key end of
frame at 1366-1373, the screenshot-dialog trigger at 1375-1379, and
the three functions `handle_input` (928-962), `release_guest_keys`
(963-974) and `keep_keyboard_for_guest` (1055-1063).

**Two entry points, not one.** The gate at 1257-1261 runs *before*
the viewport fit and the hotkeys run *after* it, so merging them
would reorder one against the other. `ui` keeps
`self.gate_keyboard_focus(ctx)` where the gate is today and
`self.handle_hotkeys(ctx)` where the hotkey block is today, with
the viewport fit between them. The remaining three blocks
(1366-1379) are contiguous with the hotkeys and fold into
`handle_hotkeys`.

`release_guest_keys` is called from `app::screenshots` as well, so
it -- like the two entry points -- is `pub(in crate::app)`;
`keep_keyboard_for_guest` is a free function used by
`handle_hotkeys` and by the tests, so it is private to `keys.rs`.
`handle_input` becomes private too: after the move its only caller
is in this file.

Move the two status-button tests with `key_press` and
`status_button_clicks` (3132-3206) into a `#[cfg(test)] mod tests`
in `keys.rs`, verbatim. `paste_triggered` and `modifiers` (1339,
1372) are locals of the moved block and move with it.

Doc comments on the moved functions reference items by name --
check every `[`…`]` link still resolves, because this is exactly
where phase 3 silently broke three of them. The rustdoc lane in
`make lint` is the check.

Commit subject: `Move the input and hotkey handling into app/ui/.`

### Step 4g: `ui`, the resize seam, and the documentation

Three things, in one commit because they are one change to the same
reader's mental model:

1. Move the viewport-fit block (1263-1289) and the two methods
   `send_pending_guest_size` (994-999) and
   `maybe_send_monitors_resize` (1000-1040) into `app/resize.rs`,
   as an `impl RyllApp` block with a single
   `pub(super) fn fit_viewport_to_surface(&mut self, ctx: &egui::Context)`
   entry point that ends by calling `maybe_send_monitors_resize`
   (both of those stay private to the file). `pub(super)` is right
   here: `resize.rs` is a direct child of `app`.

2. Give `ui` its final shape: the `ctx` binding, the shutdown
   return, the eighteen calls of decision 6 in that order, and one
   comment block saying that panel order is how egui reserves space
   (status bar before the central panel) and that window order is
   z-order. Rewrite `app/mod.rs`'s file-header comment (lines 1-10),
   which currently says the file implements the panels and carries
   the egui 0.35 migration notes -- the migration notes move to
   `app/ui/mod.rs`, which is where those call sites now live.

3. Documentation. `ARCHITECTURE.md`'s `ryll/src/` tree gains the
   `app/ui/` subtree with a one-line description per file, in the
   same style as the existing `app/` entries, placed between
   `trigger_snapshot.rs` and `ui_state.rs`; `mod.rs`'s entry stops
   claiming "panels and dialogs". `AGENTS.md` gains one line, in the
   feature-gating or conventions area, recording that items in
   `app/ui/` are `pub(in crate::app)` because `pub(super)` reaches
   only `app::ui` -- a convention an agent cannot infer from one
   file. Nothing else in `AGENTS.md` changes, and nothing is added
   to `docs/` for this phase: no user-visible behaviour changed.

Commit subject: `Reduce ui to its draw sequence.`

### Step 4h: under the length lint

Added during execution; see the execution notes for why. After 4g,
run

```
cargo clippy --frozen -p ryll --features gui,digest-decode \
    -- -W clippy::too_many_lines
```

and split every function it names under `ryll/src/app/ui/` until it
names none. Two are known in advance, both created by 4a:
`draw_usb_panel` at 210 lines and `draw_notifications_panel` at
123. The USB panel's first 60 lines are not drawing at all -- first-
open enumeration, the file-picker poll, the error auto-clear and a
repaint request -- so they become a `poll_usb_state` method called
at the top of the draw; what is left splits along the panel's own
header, device-list and virtual-disk seams. The notifications panel
splits at its header and list boundary. Whatever 4c to 4f leave over
100 is split the same way, along a seam the code already has.

The rules of 4b apply unchanged: bodies verbatim, sub-methods
private, no statement reordered, and the re-inlining check must
reproduce each original block token for token.

`RyllApp::new` and `connection.rs`'s `reconnect` are out of scope
and stay over the threshold: they are phase 5's and phase 6's.

Commit subject: `Bring every draw under the length lint.`

## Risks and mitigations

1. **A reordered draw changes what the user sees, and no test
   notices.** Panel space reservation and window z-order are both
   positional, and this repository has no test that renders a
   frame. Mitigation: decision 6 writes the order down in the plan;
   the reviewer diffs the final call order in `ui` against the
   block order in `git show develop:ryll/src/app/mod.rs` before the
   last commit; and the move check proves no line inside a block
   moved relative to its neighbours. Residual risk is real but
   small, and it is the one thing in this phase worth a manual run
   against the desktop test guest -- see the back brief.

2. **Extracting a closure body hits the borrow checker.** The
   bodies run inside `ui.horizontal(|ui| …)` and
   `Panel::show(ui, |ui| …)` closures that capture `&mut self`
   while `ui` is separately borrowed. Calling `self.draw_x(ui)` from
   inside such a closure is fine -- `self` and the closure's `ui`
   are disjoint -- but a sub-method that needs both the closure's
   `ui` and the outer `ctx` must take `ctx` as a parameter rather
   than reach for `ui.ctx()`, which borrows `ui`. That is why line
   1249 clones the context, and the comment explaining it stays.
   Mitigation: the compiler, and the step briefs say which
   parameters each method takes.

3. **The move check is weaker here than in phase 3.** Reindentation
   changes every moved line, and steps 4b and 4c add function
   headers and a struct. Mitigation: the check strips leading
   whitespace before comparing, and the definition of done
   enumerates what the diff may report. For 4b and 4c the reviewer
   additionally compares the token multiset of the whole diff, as
   #714's review did, rather than relying on the line check alone.

4. **Moving an item silently breaks a rustdoc intra-doc link.**
   Phase 3 broke three and no lint caught them until `cargo doc`
   was run by hand; #717 added the rustdoc lane to `make lint` for
   exactly this. Mitigation: `make lint` now fails on
   `rustdoc::broken_intra_doc_links`, and step 4f's brief names it.

5. **`app/ui/` is not covered by the `gui` feature lane.** It would
   be invisible to CI if the gate were wrong. Mitigation: `mod ui;`
   sits inside `mod app`, which `main.rs` gates on `gui`, so the
   files inherit it and need no attribute of their own; and
   `make lint`'s `-p ryll --features gui,digest-decode` lane
   (added in phase 3) compiles them with warnings denied.

6. **A sub-agent "tidies" a body while moving it.** Mitigation: the
   briefs say verbatim, the move check is run per step, and the
   management session reads each diff rather than the report.

## Definition of done

* `ryll/src/app/ui/` holds exactly these eleven files:

  ```
  cursor.rs dialogs.rs keys.rs mod.rs notifications_panel.rs
  region_select.rs stats_bar.rs surface.rs traffic_panel.rs
  usb_panel.rs webdav_panel.rs
  ```

* `ryll/src/app/mod.rs` is under 1,400 lines (it is 3,230 today),
  and no file under `ryll/src/app/` except `mod.rs` exceeds 800
  lines:

  ```
  find ryll/src/app -name '*.rs' ! -name mod.rs -exec wc -l {} + \
      | awk '$1 > 800 && $2 != "total" { print; bad=1 } END { exit bad }'
  ```

* `eframe::App::ui` is under 60 lines and constructs no egui panel
  or window:

  ```
  awk '/^    fn ui\(&mut self/,/^    \}$/' ryll/src/app/mod.rs \
      | grep -cE 'egui::(Panel|Window|CentralPanel)'
  ```

  is 0, and the same range is under 60 lines.

* The call order in `ui` is the order decision 6 lists, and it
  matches the block order on `develop`. Checked by the reviewer
  against `git show develop:ryll/src/app/mod.rs`; the expected
  sequence is in decision 6 and in the comment `ui` carries.

* No moved item is defined in `app/mod.rs` any more:

  ```
  grep -nE '^(    )?(pub(\(super\)|\(in crate::app\))? )?fn (handle_input|release_guest_keys|keep_keyboard_for_guest|send_pending_guest_size|maybe_send_monitors_resize|default_arrow_cursor)\b' \
      ryll/src/app/mod.rs
  ```

  prints nothing, and `grep -c 'TRAFFIC_VIEWER_' ryll/src/app/mod.rs`
  is 0.

* Nothing in `app/ui/` is wider than `pub(in crate::app)`:

  ```
  grep -nE '^\s*(pub|pub\(crate\))\s' ryll/src/app/ui/*.rs
  ```

  prints nothing.

* No function under `ryll/src/app/` exceeds 100 lines by clippy's
  count except `RyllApp::new` and `connection.rs`'s `reconnect`:

  ```
  cargo clippy --frozen -p ryll --features gui,digest-decode \
      -- -W clippy::too_many_lines 2>&1 \
      | grep -A1 'has too many lines' | grep 'ryll/src/app/'
  ```

  names those two and nothing else, at 243/100 and 154/100. Run it
  through the devcontainer the way `make lint` does
  (`DOCKER_RUN_OFFLINE`, after `make fetch`), and `touch
  ryll/src/main.rs` first: cargo replays cached diagnostics, so a
  second run on a warm cache prints nothing and the check passes
  vacuously; the figures in survey
  finding 9 come from running exactly this during planning. The
  implementer records the list in the execution notes.

* The move check passes for steps 4a, 4d, 4e and 4f. It compares
  the multiset of moved lines, leading whitespace stripped, so that
  a dedent does not register as a change:

  ```
  d=$(mktemp -d)
  git diff HEAD~1 -U0 -- ryll/src/app/mod.rs | grep '^-[^-]' | sed 's/^-//' \
      | sed -E 's/^[[:space:]]+//' | grep -vE '^(use |mod |$)' | sort > "$d/removed"
  git diff HEAD~1 -U0 -- ryll/src/app/ | grep '^+[^+]' | sed 's/^+//' \
      | sed -E 's/^[[:space:]]+//' | grep -vE '^(use |mod |$)' | sort > "$d/added"
  diff "$d/removed" "$d/added"
  ```

  Every line it reports must be a brace- or paren-only line, a new
  `fn`/`impl`/`struct` header, a comment, or the call that replaced
  the moved block. The implementer lists them in the execution
  notes; anything else is a body change and fails the step.

* For steps 4b and 4c, where the line check cannot be exact, the
  token multiset of the whole diff differs only by the new headers,
  the `SurfaceOutcome` struct and its two field reads, and the
  `}`/`)` lines the re-nesting adds. Stated in the execution notes
  with the comparison that was run.

* The `ryll` binary's test count is unchanged from `develop`, and
  no moved test body was edited: `git diff develop -- ryll/src/app/
  | grep '^-'` contains no line from inside a `#[test]` body that
  is not also added verbatim elsewhere. The two `logic` tests and
  `volume_control_round_trip` are still in `app/mod.rs`; the three
  cursor tests are in `app/ui/cursor.rs`; the two status-button
  tests are in `app/ui/keys.rs`.

* `ARCHITECTURE.md`'s `ryll/src/` tree lists every file that exists
  under `ryll/src/app/` and `ryll/src/app/ui/`:

  ```
  comm -23 <(ls ryll/src/app/*.rs ryll/src/app/ui/*.rs | xargs -n1 basename | sort -u) \
           <(sed -n '/^ryll\/src\/$/,/^shakenfist-spice-renderer\/src\/$/p' ARCHITECTURE.md \
             | grep -oE '[a-z_]+\.rs' | sort -u)
  ```

  prints nothing. `mod.rs` appears once in both lists, so the check
  cannot distinguish `app/mod.rs` from `app/ui/mod.rs`; a reviewer
  confirms by eye that both are described, and that the `app/ui/`
  sub-block names no file that does not exist.

* `app/mod.rs`'s file-header comment no longer says the file
  implements the panels, and `AGENTS.md` records the
  `pub(in crate::app)` rule in one line.

* `make lint`, `make test` and `pre-commit run --all-files` pass.
  Note survey finding 12: on a toolchain of 1.100.0 or later they
  cannot pass until shakenfist/ryll#722 is fixed, which is not this
  phase's work. If #722 is still open when the steps run, the
  implementer records each check's result *with* that one error
  excluded and says so, rather than reporting a pass it did not
  get.

* No behaviour change, so the CI GUI build and the unit tests are
  the evidence. **What that evidence does not cover:** nothing in
  the tree renders a frame, so no test distinguishes the current
  draw order from a wrong one, and no test constructs a `RyllApp`
  at all (phase 3 recorded the same gap for its two edited paths).
  Risk 1 says what is done instead. If a manual run against the
  desktop test guest is wanted, the back brief asks for it.

## Execution notes

Implementation began 2026-11-14 (AEDT), in
`../ryll-wt-app-p04` on `app-split-phase-04-panels`.

**Rebased before starting.** The branch was planned on `32d8b22`
and rebased onto `da0e593`, which carries #723. Without that fix
`make lint` cannot pass on rustc 1.100.0, so every step's checks
would have failed for a reason outside the phase (survey finding
12). #722 is closed.

**Back brief answers.**

1. The module shape is as planned: nested `app/ui/`, `ui` stays in
   `app/mod.rs`, and the draw methods are `pub(in crate::app)`
   (decisions 1-3). The two alternatives offered -- flat
   `app/ui_*.rs` files keeping `pub(super)`, and moving the whole
   `impl eframe::App` block into `app/ui/mod.rs` -- were declined.
2. A manual run against the desktop test guest is wanted before
   the pull request. The phase therefore stops after step 4g with
   the branch built, for the operator to check the panels, dialogs
   and cursor by eye. This is the mitigation risk 1 asks for.
3. This session may push and open the pull request, after that
   check.

**Three cells of the inventory table's "Takes" column were
wrong, all the same way.** The status bar, the WebDAV panel and the
central panel each need `ctx` as well as `ui` -- for a repaint
request, a texture lookup and an input read. The survey measured
`ctx` use per block correctly; the error was in transcribing those
measurements into the table and into three step briefs, where "the
panels take `ui`" became a rule instead of a per-block fact. All
three cells and the step 4c signature are corrected above, and the
rule to draw from it is that the column is derived from the tree,
not from the kind of egui widget a block uses.

**The lint check needs a cold cache.** cargo replays cached
diagnostics for a crate it considers fresh, so running the
`-W clippy::too_many_lines` command twice prints the warnings the
first time and nothing the second. Touch a file in the crate --
`touch ryll/src/main.rs` -- before each run, or the check passes by
printing nothing at all. The definition of done says so now.

**Step 4a corrected the plan.** `draw_webdav_panel` needs
`ctx: &egui::Context` as well as `ui`: its body calls
`ctx.request_repaint_after`. Survey finding 1's inventory table and
the step 4a brief both said `ui` alone, which is wrong and would
not compile; both are corrected above. The survey's own
measurements had it right, so this was a transcription error in the
plan rather than a wrong reading of the tree.

**A step was added: 4h.** The definition of done says no function
under `ryll/src/app/` may exceed 100 lines by clippy's count except
`RyllApp::new` and `reconnect`, but the step table had no step that
could deliver it. Step 4a's `draw_usb_panel` is 210 lines and
`draw_notifications_panel` 123 -- they were always that long, hidden
inside a 1,390-line `ui`, and the survey measured them before the
criterion was written. The criterion was the error, not the step.
Offered the choice between splitting them and recording them as
exceptions for phase 6, the operator chose to split, so step 4h
sweeps whatever clippy still names once the moves are done. The
definition of done stands as written.

`app/mod.rs` also loses four imports in step 4a -- `format_size`,
`TrafficDirection`, `usb::{self, DeviceSource}` -- because their
only users moved. That is not a body edit; clippy denies unused
imports, so the step could not have left them.

Rebased onto `develop` at 8381d96 on 2026-11-15 (AEDT), after
session-relay reported #726 CONFLICTING: #718
(session-follows-server) and #725 (input accounting) landed while
the pull request was open. One conflict, in `app/mod.rs`, from the
one file both sides changed. #718 added a `ModalVariant::ServerEnded`
arm and wrapped the modal body in a `ScrollArea` -- inside the
reconnect modal that step 4d had already moved to
`app/ui/dialogs.rs`. Resolved by taking the move in `mod.rs` and
porting both additions onto the moved copy, with
`server_ended_body` imported there instead; `mod.rs` keeps only
`forbid_retry_modal` and `ReconnectState` from `reconnect`. The
resolution sits in 4d's own commit, so the moved body still matches
the base line for line: 70 lines either side of the move, nothing
lost and nothing added.

That rebase also breaks one definition-of-done item, and not
because of this phase: no file under `ryll/src/app/` except
`mod.rs` may exceed 800 lines, and `reconnect.rs` is now 881 on
`develop` itself, where #718 added 350 lines to it. Phase 4 never
touches that file. Recorded here rather than ticked, relaxed or
swept into this phase's scope; `reconnect.rs` belongs to a later
phase's inventory.

The manual guest run (back brief point 2) happened on 2026-11-15
against the XFCE desktop guest, and the panels, dialogs and cursor
were confirmed by hand. It needed `make test-qemu-desktop
QEMU_SPICE_PORT=5901`: andris holds 127.0.0.1:5900 on this host, and
the target's default port is 5900, so qemu exits before it
daemonizes and `make` still prints a connect line for a guest that
is not there.

## Back brief

Before executing any step, back brief the operator on your
understanding of the plan. Three points need an answer first:

1. **The file set and the draw order** (the inventory table in
   survey finding 1 and the sequence in decision 6). This is cheap
   to agree and expensive to redo: once the blocks are moved,
   changing which file one lives in is another full move.

2. **Whether a manual run against the desktop test guest is wanted
   before the pull request.** Risk 1 is the phase's only
   test-invisible risk, and `make desktop-guest` plus a GUI run is
   the only way to see it. It needs a human at the desktop session,
   so it is the operator's call, not a step.

3. **Whether this session may push and open the pull request.**
