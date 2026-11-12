# Phase 3: Events, connection lifecycle and the bug-report flow

Master plan: [PLAN-app-split.md](/components/ryll/plans/PLAN-app-split/).

## Planning effort

Planned at medium effort. Five of the seven steps are moves with no
behaviour change, verified by the same move-check script phase 2
used. The two that are not -- turning the `ChannelEvent` match into
a dispatcher, and replacing its wildcard arm -- carry the phase's
only real risk, so the review effort belongs there rather than
spread across the moves.

## Scope

In:

* `ryll/src/app/connection.rs` -- the connect/reconnect/disconnect
  lifecycle and the auto-snapshot task lifecycle.
* `ryll/src/app/events.rs` -- `process_events` and the per-frame
  drain helpers that bracket it, then split into one `handle_*`
  method per arm group so the `match` becomes a dispatcher.
* `ryll/src/app/trigger_snapshot.rs` -- the trigger snapshot, the
  eframe window shot, and the region-selection state machine.
* `ryll/src/app/bug_report.rs` -- dialog open, submit, the pending
  report, the poll/write/flush path, and the disconnect snapshot.
* `ryll/src/app/screenshots.rs` -- the manual screenshot save path
  (survey finding 5 claims it for this phase).
* Replacing the `ChannelEvent` match's `_ => {}` arm with explicit
  arms (decision 5).
* Correcting `ARCHITECTURE.md`'s `ryll/src/` tree, which is already
  stale (survey finding 6).

Out:

* Anything the `eframe::App::ui` body draws. Panels, dialogs and
  overlays are phase 4; this phase moves the *state machines and
  flows* those panels drive, not the drawing.
* `default_arrow_cursor` and the `TRAFFIC_VIEWER_*` constants, which
  phase 4 takes with the cursor and traffic panels.
* Grouping `RyllApp`'s fields into sub-structs. Methods moved here
  keep reaching into `self` exactly as they do today; phase 5
  changes that.
* `push_notification` and `push_connection_event`, which stay in
  `mod.rs` (decision 4).
* The two `ChannelEvent` variants the GUI ignores. Decision 5 makes
  the omission explicit and reviewable; it does not start handling
  them.

## What the survey found

Surveyed against `develop` at `f56b371`, after #501 (phase 2)
merged. `ryll/src/app/mod.rs` is 5,592 lines.

1. **Every item the master plan's phase 3 section names still
   exists, and none of them touches egui.** `reconnect`,
   `reconnect_manual`, `handle_critical_disconnect`,
   `handle_connection_failed`, `build_connection_runtime`,
   `process_events`, `begin_trigger_snapshot`, `PendingBugReport`,
   `generate_bug_report`, `finish_bug_report`,
   `poll_pending_bug_report`, `file_notification_bug_report`,
   `maybe_write_disconnect_snapshot` and `validate_region` are all
   present. Across the whole phase-3 region there are exactly two
   `egui::` mentions in live code, both in `observe_ui_state`
   (`mod.rs:2359`): its `ctx: &egui::Context` parameter and the
   `egui::Popup::is_any_open(ctx)` call beside it. The only others
   are an `egui::Rect` in a comment in `reconnect`. So these are
   clean moves, not a decoupling exercise.

2. **The master plan names an `audio` arm group that does not
   exist.** `ChannelEvent` has thirty variants and not one of them
   is audio: volume and mute travel through `volume_control`, not
   the event queue. The arm groups actually present are session
   lifecycle, display draw ops, display mark, cursor, monitors
   config, ping/RTT, error, USB, WebDAV, paste, notification and
   disconnect. The master plan's phase 3 section is corrected in
   this commit.

3. **The master plan's last phase-3 bullet is already done.** It
   asks for the comments in `surface_mirror.rs`, `session.rs` and
   `bugreport.rs` that name `app.rs` to be updated. Phase 1 removed
   the "mirrors `process_events`" comment from `surface_mirror.rs`
   entirely, and phase 2's step 2e repointed `session.rs:18`,
   `session.rs:62`, `bugreport.rs:35` and `bugreport.rs:857` at
   `ryll/src/app/`. `git grep -n 'app\.rs' -- ':!docs/plans/'`
   finds nothing. The bullet is struck from the master plan in this
   commit.

4. **`process_events` has shrunk, and the `eframe::App` impl has
   grown a second half.** The master plan's Situation table says
   `process_events` is ~610 lines; it is 487
   (`mod.rs:1539-2025`). `eframe::App` now has both
   `logic` (`mod.rs:3200`, 156 lines, which is what calls
   `process_events`) and `ui` (`mod.rs:3356`, ~1,685 lines). The
   test module at the foot of `mod.rs` is 454 lines, not ~1,020, as
   phase 2 moved most of it out. Two modules the master plan never
   mentions have appeared since it was written --
   `app/agent_state.rs` (209 lines) and `app/ui_state.rs` (285) --
   both landed by other plans. None of this changes phase 3's
   scope, but phase 4's should be re-surveyed against `logic` as
   well as `ui`.

5. **`screenshot_paths` is the one pure item no phase has claimed.**
   Phase 2's finding 4 left it "for phases 3 and 4 as planned", but
   the master plan's phase 4 list (`stats_bar`, `traffic_panel`,
   `notifications_panel`, `usb_panel`, `webdav_panel`, `dialogs`,
   `region_select`, `cursor`, `keys`) has no home for it.
   `screenshot_paths` (`mod.rs:782`), `save_screenshots`
   (`mod.rs:3070`), `open_screenshot_dialog` (`mod.rs:3102`) and
   the five `screenshot_paths_*` tests are egui-free -- the dialog
   is `rfd`, a native panel -- so they are a phase-3 move, not a
   phase-4 one. Decision 6.

6. **`ARCHITECTURE.md`'s `ryll/src/` tree is stale already.** Its
   `app/` listing (`ARCHITECTURE.md:172` onwards) omits
   `agent_state.rs` and `ui_state.rs`, which have been on `develop`
   since phase 2 landed. Step 3g fixes that as well as adding this
   phase's files.

7. **The `ChannelEvent` match ends in `_ => {}`**
   (`mod.rs:1973`). Two variants fall into it. `DigestUpdated` is
   turned into the control socket's `digest_updated` wire event by
   `shakenfist-spice-renderer/src/control/server.rs`, and exists
   only under the renderer's `digest-decode` feature.
   `ChannelsAvailable` is consumed by nobody: `session::connect`
   learns the channel list from a oneshot that the main channel
   fires immediately before emitting the event
   (`channels/main_channel.rs` takes `channels_avail_signal` and
   then emits; `session.rs` awaits `channels_avail_rx`), so the
   event is informational. The GUI is right to ignore both, but a
   wildcard means the *next* variant is also ignored silently --
   which is the shape of failure this whole plan exists to make
   visible. Decision 5.

   Corrected during step 3g: this finding originally said
   `session.rs` consumed the `ChannelsAvailable` event, and did not
   notice that `DigestUpdated` is feature-gated. The second error
   is why the step's single arm does not compile.

8. **Three `continue` statements sit inside the
   `SessionInitialized` arm** (`mod.rs:1589`, `1597`, `1640`).
   They are the only control-flow escapes in the whole match, and
   extracting that arm into a method turns each into a `return`.
   Risk 1 covers this.

9. **No duplication with the renderer crate in the phase-3
   region.** Phase 1 already lifted the one real case (the draw-op
   dispatch) and the display arm is now a thin wrapper over
   `self.surfaces.apply_event`. `reconnect_policy.rs` already holds
   the SPICE retry rules with no GUI dependency, and phase 1's
   finding 6 settled that the GUI's `ReconnectState` stays in
   `ryll`. Nothing here wants to move to the renderer.

The master plan's phase 3 section is corrected in this commit for
findings 2, 3 and 5, and its Situation table for finding 4.

## Decisions

1. **Five files, not the master plan's three.** The master plan
   proposed `events.rs`, `bug_report.rs` and `connection.rs`. The
   bug-report material measures ~1,124 lines, which would create
   exactly the kind of oversize file this plan exists to remove and
   would breach the master plan's own success criterion of roughly
   800 lines. It splits on a real seam: capturing evidence (trigger
   snapshot, window shot, region selection) happens at dialog-open
   time and is driven by the UI, while assembling and writing the
   report happens at submit time and is driven by
   `crate::bugreport`. So `trigger_snapshot.rs` (~563 lines) and
   `bug_report.rs` (~561) rather than one file. `screenshots.rs`
   (~194) is finding 5. Measured sizes:

   | File | Lines | Was |
   |---|---:|---|
   | `app/connection.rs` | ~574 | new |
   | `app/events.rs` | ~584 | new |
   | `app/trigger_snapshot.rs` | ~563 | new |
   | `app/bug_report.rs` | ~561 | new |
   | `app/screenshots.rs` | ~194 | new |
   | `app/mod.rs` | ~3,176 | 5,592 |

   `mod.rs` stays oversize until phase 4 removes `ui`.

2. **Move first, restructure second, in separate commits.** Step 3b
   moves `process_events` verbatim into `events.rs`; step 3c then
   splits it into `handle_*` methods. Doing both in one commit
   would make the move check useless on the file that most needs
   it, and would leave a reviewer unable to tell a moved line from
   an edited one. The cost is one extra commit in which `events.rs`
   contains a 487-line function.

3. **The auto-snapshot thread's whole lifecycle lands in
   `connection.rs`, including the spawn.** Today the spawn is ~75
   lines inlined in the `SessionInitialized` arm while
   `retire_auto_snapshot_task`, `signal_auto_snapshot_retire` and
   `reap_auto_snapshot_threads` are separate methods. Step 3c
   extracts the spawn as `spawn_auto_snapshot_task(&mut self,
   interval_secs: u64)` on `connection.rs`, called from
   `handle_session_initialized`. The alternative -- leaving the
   spawn in `events.rs` -- splits a four-part lifecycle across two
   files for no reason beyond where the code happens to sit today.

4. **`push_notification` and `push_connection_event` stay in
   `mod.rs`.** They have 22 and 12 call sites respectively, spread
   across every module this phase creates *and* the `ui` body that
   phase 4 will split. They are small, they touch no state but the
   notification store, and putting them in any one of the new files
   would make the other four depend on it. A `notify.rs` is
   defensible once phase 4 has moved the `ui` call sites; it is
   premature now.

5. **The wildcard arm is replaced with explicit arms, in its own
   commit.** `ChannelsAvailable` and `DigestUpdated` get a named
   arm with a one-line reason pointing at the consumer that does
   handle them (finding 7), and the `_ => {}` goes. After that a new
   `ChannelEvent` variant is a compile error in the GUI rather than
   a silent no-op. This is a behaviour-preserving edit, not a move,
   so it is step 3g and not folded into 3b or 3c -- the move check
   must stay meaningful. It is also the one decision a reviewer may
   argue with: it makes the renderer crate's event enum a
   breaking-change surface for the GUI, and someone adding a
   variant for headless mode alone must now touch `events.rs` to
   say so. That is the point.

6. **`screenshots.rs` is phase 3's, not phase 4's** (finding 5).
   It is egui-free, the dialog is a native `rfd` panel, and nothing
   in phase 4's per-panel list would hold it. Taking it now means
   phase 4's survey has one fewer loose end.

7. **Visibility is `pub(super)` and nothing wider**, as in phase 2.
   Items moved here are used only by `app/mod.rs` and its other
   children. No accessors are added to `RyllApp`; child modules see
   the parent's private fields directly, which is what makes this
   phase a move rather than a redesign.

8. **Tests move with the code they test.** A test exercising items
   from two modules stays in `mod.rs`. The three tests that drive
   egui directly
   (`logic_only_passes_keep_answering_repaint_requests`,
   `window_focused_is_current_in_a_logic_only_pass`,
   `host_cursor_*`) stay in `mod.rs` as they did in phase 2.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Move the connection and auto-snapshot lifecycle to `app/connection.rs`. Pure move. Detail below. |
| 3b | medium | sonnet | none | Move `process_events` and its drain helpers to `app/events.rs`. Pure move, no restructure. Detail below. |
| 3c | high | opus | none | Split `process_events` into a dispatcher plus `handle_*` methods, and extract the auto-snapshot spawn into `connection.rs`. This is the edit step. Detail below. |
| 3d | medium | sonnet | none | Move the trigger snapshot, window shot and region selection to `app/trigger_snapshot.rs`. Pure move. Detail below. |
| 3e | medium | sonnet | none | Move the bug-report dialog and submit flow to `app/bug_report.rs`. Pure move. Detail below. |
| 3f | low | sonnet | none | Move the manual screenshot path to `app/screenshots.rs`. Pure move. Detail below. |
| 3g | medium | sonnet | none | Replace the `ChannelEvent` wildcard arm with explicit arms, and update `ARCHITECTURE.md`. Detail below. |

Every step: build and test with `make lint` and `make test` (cargo
runs in the devcontainer; never install Rust on the host), run
`pre-commit run --all-files`, and -- for steps 3a, 3b, 3d, 3e and
3f -- run the move-check script from *Definition of done* against
the step's diff. Do not commit; the management session reviews and
commits each step.

Line numbers below are against `develop` at `f56b371` and shift as
earlier steps land. Find items by name, not by line.

### Step 3a: connection.rs

Move to `ryll/src/app/connection.rs`:

* `build_connection_runtime` (`mod.rs:750`) -- a free function, not
  a method.
* `MAX_RETIRING_AUTO_SNAPSHOT_THREADS` (`mod.rs:175`) with its
  comment.
* The methods `reconnect`, `reconnect_manual`,
  `signal_auto_snapshot_retire`, `reap_auto_snapshot_threads`,
  `retire_auto_snapshot_task`, `handle_critical_disconnect`,
  `reconnect_policy` and `handle_connection_failed` -- a contiguous
  run at `mod.rs:1113-1538` -- in a second `impl RyllApp` block.

`reconnect` has one `egui::Rect` mention, in a comment; nothing
here needs an egui import. `reconnect_policy` reads
`ReconnectPolicy` from `app/reconnect.rs`, which phase 2 placed, so
import it from `super::reconnect`. There are no tests for these
methods today; do not write any (that would be an edit, and the
phase is a move).

Commit: "Move the connection lifecycle out of app."

### Step 3b: events.rs

Move to `ryll/src/app/events.rs`, verbatim:

* `process_events` (`mod.rs:1539-2025`), including the lag-sampling
  pre-match and the post-drain tail that calls
  `maybe_write_disconnect_snapshot`, `sync_session_state`,
  `check_event_drops`, `handle_connection_failed` and
  `update_app_snapshot`.
* `check_event_drops` (`mod.rs:2026`), `sync_session_state`
  (`mod.rs:2051`), `clear_usb_operation_flags` (`mod.rs:2085`) and
  `update_app_snapshot` (`mod.rs:2162`).
* `EVENT_DROP_NOTICE` and `event_drops_need_notice`
  (`mod.rs:5041-5066`), which phase 2's finding 4 reserved for this
  phase, plus the test
  `event_drop_notice_only_when_count_rises` (`mod.rs:5142`).

`update_app_snapshot` has a single caller, the tail of
`process_events`, and belongs to the same per-frame drain; it moves
here rather than to a module of its own.

Do not change `process_events`'s body in this step -- not even to
reflow a line. Step 3c does that.

Commit: "Move event processing out of app."

### Step 3c: process_events becomes a dispatcher

This is the only step that edits rather than moves. Behaviour must
not change.

In `events.rs`, extract one `pub(super) fn handle_*(&mut self, ...)`
per arm group, leaving `process_events` as the drain loop, the
lag-sampling pre-match, a `match` of one line per arm, and the
post-drain tail. Target: `process_events` under 80 lines.

The arm groups, with today's line ranges for reference:

| Arm group | Today | Suggested method |
|---|---|---|
| `SessionInitialized` | 1570-1708 | `handle_session_initialized(session_id)` |
| `SurfaceCreated`..`Invert` | 1709-1762 | `handle_draw_event(&event)` |
| `DisplayMark` | 1763-1787 | `handle_display_mark()` |
| `CursorPosition`, `CursorShape` | 1788-1802 | inline; two and eight lines |
| `MonitorsConfig`, `PingInterval`, `InputRoundTrip` | 1803-1816 | inline; three lines each |
| `Error` | 1817-1834 | `handle_channel_error(channel, message)` |
| `UsbChannelReady`..`UsbConnectFailed` | 1835-1860 | `handle_usb_event` or inline per arm |
| `WebdavChannelReady`..`WebdavError` | 1861-1886 | `handle_webdav_event` or inline per arm |
| `PasteCompleted`, `PasteFailed` (two arms) | 1887-1920 | inline |
| `Notification` | 1903-1908 | inline |
| `Disconnected` | 1909-1972 | `handle_disconnected(channel, reason)` |

Extract where the body earns it. An arm of three lines is clearer
inline than behind a call, and the master plan's goal is a readable
dispatcher, not one method per variant.

Two specific constraints:

* **The three `continue`s become `return`s.** `mod.rs:1589`, `1597`
  and `1640` are all inside the `SessionInitialized` arm, and
  nothing follows that arm in the loop body, so `return` from
  `handle_session_initialized` is exactly equivalent. A `break`
  would stop draining the queue; a fallthrough would re-announce a
  duplicate session and respawn the auto-snapshot task. Read the
  comments at `mod.rs:1570-1578` before touching this.
* **The auto-snapshot spawn moves to `connection.rs`** as
  `pub(super) fn spawn_auto_snapshot_task(&mut self, interval_secs:
  u64) -> bool` (decision 3), returning whether it spawned so
  `handle_session_initialized` can return early on a refused
  retire, as the `continue` at `mod.rs:1640` does today. The whole
  `if let Some(interval_secs) = self.auto_snapshot_interval { ... }`
  block at `mod.rs:1630-1707` goes, body unchanged beyond the
  `continue`/`return` conversion.

`handle_draw_event` takes `&ChannelEvent` because
`self.surfaces.apply_event` does; the arm matches on
`ChannelEvent::SurfaceCreated { .. } | ...` with no bindings today,
so pass the event through rather than destructuring it.

Verify with `make test` that the test count is unchanged and that
no test needed editing. If a test had to change, stop and report:
that means behaviour changed.

Commit: "Split process_events into per-group handlers."

### Step 3d: trigger_snapshot.rs

Move to `ryll/src/app/trigger_snapshot.rs`:

* `WINDOW_SHOT_WAIT`, `struct WindowShot`, `encode_window_shot`
  (`mod.rs:108-147`).
* `REGION_SELECT_PROMPT`, `REGION_SELECT_PENDING`
  (`mod.rs:152-172`).
* `struct TriggerSnapshot` (`mod.rs:193`), `enum RegionSelectEnd`
  and its `impl` (`mod.rs:222-265`).
* The methods `begin_trigger_snapshot`, `discard_trigger_snapshot`,
  `request_window_shot`, `window_shot_holds_dialog`,
  `take_window_shot_png`, `observe_ui_state`,
  `take_trigger_for_submit` (`mod.rs:2241-2426`),
  `begin_region_select` and `end_region_select`
  (`mod.rs:2928-2955`).
* `validate_region` (`mod.rs:5067`), a free function.
* The tests `region_select_end_only_a_drag_carries_a_region`,
  `region_select_end_says_unfinished_selections_still_save` and
  `validate_region_*` (six of them), at `mod.rs:5445-5542`.

`observe_ui_state` is the one method here that touches egui: it
takes `ctx: &egui::Context` and calls `ctx.any_popup_open()` and
`egui::Popup::is_any_open(ctx)`. Keep the signature; the file needs
no other egui use. Everything called from the `ui` body --
`observe_ui_state`, `begin_region_select`, `end_region_select`,
`window_shot_holds_dialog`, `take_window_shot_png`,
`discard_trigger_snapshot`, `take_trigger_for_submit` -- needs
`pub(super)`.

`end_region_select` calls `self.finish_bug_report`, which step 3e
moves to `bug_report.rs`. A sibling module call needs that method
`pub(super)` too; whichever of 3d and 3e lands second adds it.

Commit: "Move trigger snapshot and region select out of app."

### Step 3e: bug_report.rs

Move to `ryll/src/app/bug_report.rs`:

* `BUG_REPORT_METRICS_WINDOW`, `BUG_REPORT_METRICS_DEADLINE`
  (`mod.rs:85-107`), `BUG_REPORT_EXIT_GRACE` (`mod.rs:148`).
* `struct PendingDisconnectReport` (`mod.rs:208`),
  `wait_for_metrics_sample` (`mod.rs:266`), `struct
  PendingBugReport` (`mod.rs:293-319`).
* The methods `generate_bug_report`, `manual_bug_report_dir`,
  `note_channel_end`, `request_disconnect_report`,
  `maybe_write_disconnect_snapshot` (`mod.rs:2427-2582`),
  `file_notification_bug_report`, `finish_bug_report`,
  `poll_pending_bug_report`, `warn_if_too_big_to_attach`,
  `write_pending_bug_report`, `flush_pending_bug_report_at_exit`
  and `open_bug_dialog` (`mod.rs:2620-2927`).
* The tests `wait_for_metrics_sample_*` (three) and
  `notification_bug_report_type_serialises`
  (`mod.rs:5543-5592`). Phase 2's step 2b left the last of those in
  `mod.rs` deliberately because it tests a bugreport type rather
  than the snapshot store; this is the file it was waiting for.

`manual_bug_report_dir` is also called by step 3c's
`spawn_auto_snapshot_task` in `connection.rs`, so it needs
`pub(super)`. So do `open_bug_dialog`, `poll_pending_bug_report`
and `flush_pending_bug_report_at_exit`, which the `ui` and
`on_exit` bodies call.

Commit: "Move the bug-report flow out of app."

### Step 3f: screenshots.rs

Move to `ryll/src/app/screenshots.rs` (survey finding 5):

* `screenshot_paths` (`mod.rs:782`), a free function.
* The methods `save_screenshots` (`mod.rs:3070`) and
  `open_screenshot_dialog` (`mod.rs:3102`). The latter is the one
  method in this phase that lives inside the `impl RyllApp` block
  rather than before it; take it from there.
* The five `screenshot_paths_*` tests (`mod.rs:5364-5417`).

`open_screenshot_dialog` calls `self.release_guest_keys()` and
`self.push_notification`, both of which stay in `mod.rs`, so no
visibility change is needed on them -- a child module sees its
parent's private methods. `open_screenshot_dialog` itself needs
`pub(super)`, being called from the `ui` body.

Commit: "Move the screenshot save path out of app."

### Step 3g: explicit event arms, and the architecture tree

Two edits, one commit.

First, in `events.rs`, replace the `match event` arm `_ => {}`
(`mod.rs:1973` today) with explicit arms for the two variants that
fall into it (survey finding 7):

```rust
// Consumed in the renderer crate, not here: `ChannelsAvailable`
// by `session::connect` when it spawns the per-channel tasks,
// and `DigestUpdated` by the control socket's `digest_updated`
// event. Named rather than matched by `_` so a new
// `ChannelEvent` variant fails to compile here until someone
// decides whether the GUI should act on it.
ChannelEvent::ChannelsAvailable(_) | ChannelEvent::DigestUpdated { .. } => {}
```

Confirm there is no `_` arm left in that `match` and that
`cargo build` is clean -- if it is not, the enum has a variant
neither this arm nor an earlier one covers, which is itself the
finding.

Second, in `ARCHITECTURE.md`, update the `ryll/src/` tree
(`ARCHITECTURE.md:172` onwards) to list the `app/` children in
alphabetical order with a one-line comment each: `agent_state.rs`
and `ui_state.rs`, which are already missing (survey finding 6),
plus `bug_report.rs`, `connection.rs`, `events.rs`,
`screenshots.rs` and `trigger_snapshot.rs`. Narrow `mod.rs`'s own
comment, which currently claims "event loop, ... connection
lifecycle" -- neither is in it after this phase.

Check also that `AGENTS.md:119` ("Handle in `process_events()` in
`ryll/src/app/`") still reads correctly; name `app/events.rs` if it
is clearer. Do not add anything to `AGENTS.md`: no convention
changed in this phase.

Commit: "Name the events the GUI ignores."

## Risks and mitigations

1. **The `continue` to `return` conversion in
   `handle_session_initialized`.** A `break` would silently stop
   draining the event queue for that frame; dropping the escape
   entirely would re-announce the session and respawn the
   auto-snapshot thread on every duplicate `SPICE_MSG_MAIN_INIT`,
   which is the exact bug `classify_session_initialized` exists to
   prevent. The session-init tests in `app/session_init.rs` cover
   the classifier but not the arm that acts on it, so they would
   not catch this. *Mitigation:* the step 3c brief calls it out
   explicitly; the management session reads the diff of that arm
   line by line before committing, and checks `grep -n 'break'
   ryll/src/app/events.rs` is empty.

2. **A move that quietly becomes an edit.** Five of the seven steps
   claim to be pure moves, and a sub-agent that reflows a line or
   "tidies" an import while moving it makes the claim false and the
   review much more expensive. *Mitigation:* the move-check script
   under *Definition of done*, run by the sub-agent on its own diff
   before handing back, and again by the management session.

3. **Explicit arms turn out to be unmaintainable in practice.** If
   the renderer crate's `ChannelEvent` grows variants that only
   headless or web mode care about, every one costs a GUI edit.
   *Mitigation:* that cost is the intended signal, and it is one
   line. If it becomes genuinely painful the arm can go back to `_`
   in one commit, and the master plan's close-out notes should
   record it if so.

4. **`mod.rs` is still 3,176 lines after this phase.** A reviewer
   may reasonably read that as the phase having failed. *Mitigation:*
   decision 1's table states the expected size, and `ui` alone is
   ~1,685 of the remainder -- phase 4's work, by design.

5. **Seven commits on one branch is a lot to review.** *Mitigation:*
   each commit is one file's worth of movement with a subject that
   says so, and the five move commits can be read with the move
   check rather than by eye. Steps 3c and 3g are the two that want
   real attention, and they are last-but-one and last.

## Definition of done

* `ryll/src/app/` holds exactly: `mod.rs`, `agent_state.rs`,
  `bug_report.rs`, `connection.rs`, `events.rs`,
  `notification_snapshots.rs`, `reconnect.rs`, `resize.rs`,
  `screenshots.rs`, `session_init.rs`, `stats.rs`,
  `trigger_snapshot.rs`, `ui_state.rs`.

* None of the moved items is defined in `app/mod.rs` any more:

  ```
  grep -nE '^(    )?(pub(\(super\))? )?(async )?(fn|struct|enum|const) (process_events|check_event_drops|sync_session_state|update_app_snapshot|event_drops_need_notice|reconnect|reconnect_manual|reconnect_policy|handle_critical_disconnect|handle_connection_failed|build_connection_runtime|retire_auto_snapshot_task|begin_trigger_snapshot|take_trigger_for_submit|observe_ui_state|validate_region|RegionSelectEnd|WindowShot|TriggerSnapshot|PendingBugReport|PendingDisconnectReport|generate_bug_report|finish_bug_report|poll_pending_bug_report|file_notification_bug_report|maybe_write_disconnect_snapshot|wait_for_metrics_sample|screenshot_paths|save_screenshots|open_screenshot_dialog)\b' ryll/src/app/mod.rs
  ```

  prints nothing.

* No file under `ryll/src/app/` except `mod.rs` exceeds 800 lines:

  ```
  find ryll/src/app -name '*.rs' ! -name mod.rs -exec wc -l {} + \
      | awk '$1 > 800 && $2 != "total" { print; bad=1 } END { exit bad }'
  ```

* `ryll/src/app/mod.rs` is under 3,400 lines (it is 5,592 today).

* `process_events` in `ryll/src/app/events.rs` is under 80 lines,
  and no `handle_*` method in that file exceeds 150.

* `grep -n 'break' ryll/src/app/events.rs` prints nothing, and the
  dispatch `match` has no wildcard arm. The lag-sampling pre-match
  that brackets it keeps its own `_ => {}`: it samples four of the
  thirty `ChannelEvent` variants and must ignore the rest, so
  naming them there would add twenty-six no-op arms and a second
  place to edit for every new variant. The check is therefore
  scoped to the dispatch:

  ```
  awk '/^            match event \{/,/^            \}/' ryll/src/app/events.rs \
      | grep -c '_ => {}'
  ```

  is 0, and `grep -c '_ => {}' ryll/src/app/events.rs` is 1.

* The `ryll` binary's test count is unchanged from `develop`:
  `make test` reports the same number of passing tests for the
  `ryll` binary before and after the branch, and no existing test
  body was edited (`git diff develop -- ryll/src/app/ | grep '^-'`
  contains no line from inside a `#[test]` body that is not also
  added verbatim elsewhere).

* The move check passes for steps 3a, 3b, 3d, 3e and 3f. It
  compares the multiset of non-blank, non-`use`/`mod`/visibility-only
  lines removed from `mod.rs` with those added to the new files:

  ```
  d=$(mktemp -d)
  git diff HEAD~1 -U0 -- ryll/src/app/mod.rs | grep '^-[^-]' | sed 's/^-//' \
      | sed -E 's/pub\(super\) //' | grep -vE '^\s*(use |mod |$)' | sort > "$d/removed"
  git diff HEAD~1 -U0 -- ryll/src/app/ ':!ryll/src/app/mod.rs' | grep '^+[^+]' | sed 's/^+//' \
      | sed -E 's/pub\(super\) //' | grep -vE '^\s*(use |mod |$)' | sort > "$d/added"
  diff "$d/removed" "$d/added"
  ```

  The only lines it may report are the new files' `#[cfg(test)] mod
  tests {` wrappers and their closing braces.

* `ARCHITECTURE.md`'s `ryll/src/` tree lists every file that exists
  under `ryll/src/app/`. On `develop` this prints `agent_state.rs`
  and `ui_state.rs` (survey finding 6); after step 3g it prints
  nothing:

  ```
  comm -23 <(ls ryll/src/app/*.rs | xargs -n1 basename | sort) \
           <(sed -n '/^ryll\/src\/$/,/^shakenfist-spice-renderer\/src\/$/p' ARCHITECTURE.md \
             | grep -oE '[a-z_]+\.rs' | sort -u)
  ```

  The three crate trees share one fenced block in
  `ARCHITECTURE.md`, which is why the range ends at the next crate
  header rather than at a closing fence. The check is one-way: the
  reverse direction would flag `ryll/src/`'s other files, which
  belong in the tree. A reviewer confirms by eye that the `app/`
  sub-block names no file that does not exist.

* `make lint`, `make test` and `pre-commit run --all-files` pass.

* No behaviour change, so no live-guest test is needed; the CI GUI
  build and unit tests are the evidence. Step 3c is the one step
  where that claim is a judgement rather than a script, and risk 1
  says how it is checked.

## Execution notes

- **Back brief, 2026-11-12 (AEDT).** Mikal chose to implement this
  plan as written rather than re-survey it, having found it had
  been planned on 2026-11-05 and never executed; its branch was
  rebased from `f56b371` onto `e381b1e` first, which replayed both
  planning commits unchanged. He gave permission to push and open
  the pull request once the definition of done passes, and ruled
  that the definition-of-done wildcard check be scoped to the
  dispatch `match` rather than made to cover the lag-sampling
  pre-match as well.

- **Three of the plan's premises had shifted** between the survey
  at `f56b371` and execution at `e381b1e`, all corrected above at
  their source rather than noted here alone. `42dec90` added a
  second `ChannelEvent::PasteFailed` arm, for
  `PasteFailureKind::InProgress`, so step 3c's arm table named one
  arm where the code has two and every line reference after 1895
  shifted by eighteen. The `ryll/src/` tree in `ARCHITECTURE.md`
  moved from line 148 to line 172, though survey finding 6 still
  holds: `agent_state.rs` and `ui_state.rs` are still absent from
  it. The definition of done asked for no `_ => {}` anywhere in
  `events.rs`, which `process_events` cannot satisfy, as above.

- **Baseline for the test-count criterion**: the `ryll` binary ran
  437 tests on the rebased branch before any step, with the whole
  workspace green.

- **Step 3a** (Sonnet, medium effort) moved the connection
  lifecycle with no line of any body changed; the move check
  reported only the `impl RyllApp {` and `}` wrapper lines. It
  first opened the file with `use super::*;`, which was sent back:
  every module the previous phase created uses explicit `use`
  lines, the workspace has no file-scope glob import anywhere, and
  `clippy::wildcard_imports` would flag it if the workspace adopts
  the pedantic group. `connection.rs` needs thirty-two names
  explicitly, which is the real cost of the rule, and the
  remaining four files this phase creates follow it. The step also
  widened `EVENT_CHANNEL_SIZE`, `INPUT_CHANNEL_SIZE` and
  `RegionSelectEnd` to `pub(super)`. Note for step 3d:
  `connection.rs` imports `RegionSelectEnd` from `super`, so that
  import must follow the enum into `trigger_snapshot.rs`.

- **Steps 3b to 3f** (Sonnet, medium effort, 3f low) were pure
  moves, each verified by comparing the whole diff's tokens rather
  than only its lines: for every step the only differences outside
  `use`, `mod` and visibility were rustfmt rewraps forced by
  prepending `pub(super)` to a line already near a hundred columns
  (`take_trigger_for_submit`'s signature and the
  `REGION_SELECT_PENDING` const in 3d,
  `maybe_write_disconnect_snapshot`'s signature in 3e) and, in 3f,
  two blank lines `cargo fmt` would not leave behind. Step 3b's
  `process_events` was checked body-for-body: 497 lines,
  byte-identical. Decision 1's size estimates held, within thirty
  lines on every file.

- **Step 3c** (Opus, high effort) extracted two methods the step's
  own list did not have, `sample_event_lag` and
  `finish_event_drain`, because the arms total 425 lines and
  leaving the subsystem arms inline lands the dispatcher at about
  148 whatever else is done. Mikal approved that shape at the
  plan's gate in preference to relaxing the eighty-line target or
  grouping the USB, WebDAV and paste arms behind handlers that
  would each need an `unreachable!()`. `process_events` came out at
  77 lines. The trailing `return` on a refused auto-snapshot
  retire carries an `#[expect]` rather than the `#[allow]` the step
  first used, per the master plan's own rule for exemptions; it is
  the workspace's first `#[expect]` against seventy-eight
  `#[allow]`s, which is the last phase's inventory to sweep.

- **Step 3g found the plan's finding 7 wrong in two ways**, both
  recorded above at the finding. `DigestUpdated` is
  `#[cfg(feature = "digest-decode")]`, so the arm the step
  specified does not compile; it is two arms, the second carrying
  the same `cfg`, which is what the renderer's own `event_name`
  match does for the same variant. `ryll`'s `digest-decode`
  feature is the only thing in the workspace that enables the
  renderer's, so the two gates cannot diverge in a workspace
  build. `make lint` compiles neither state of that arm, so the
  management session compiled `ryll` with
  `--features digest-decode,rdp` separately: clean, no warnings
  under `-D warnings`. And nothing consumes `ChannelsAvailable` at
  all, which the step's comment now says instead.

- **Definition of done, run on completion.** Ten of the eleven
  items passed as written. Item 5 failed on the first run:
  `process_events` was 77 lines after step 3c and 89 after step
  3g, whose two named arms and their twelve-line explanation land
  inside the function. Mikal chose to move the prose rather than
  relax the bar, so the explanation is now a note above
  `impl RyllApp` at the top of `events.rs` and the function is 79.
  Getting from 81 to 79 cost the one-line pointer beside the arms,
  which is worth restoring if the bar is ever moved to 80.

  Item 9 is reported against a stronger check than the plan's own
  script, which cannot see continuation lines of a multi-line
  `use` or a rustfmt rewrap forced by `pub(super)`, and so reports
  four to seventeen residual lines on each move commit. Each
  diff was instead compared as a whitespace-insensitive multiset
  of tokens, which is clean for all five moves: every token
  removed is re-added, bar imports, the three `continue` to
  `return` conversions and three rustfmt rejoins. Step 3b's
  `process_events` was additionally compared body-for-body, 497
  lines byte-identical. A future phase should fix the script
  rather than the expectation.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

**Gate before step 3c.** Steps 3a-3b and 3d-3f are moves whose
shape is settled; run them without stopping. Step 3c decides which
arms become methods and which stay inline, and redoing that after
the fact means re-reviewing a 487-line function. Before editing,
post the proposed method list -- name, signature and which arms it
absorbs -- and wait for agreement.
