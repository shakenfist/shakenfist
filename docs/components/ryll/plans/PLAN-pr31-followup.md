# PR 31 follow-up: UI improvements, reconnect, and protocol fixes

## Situation

PR 31 (hhding) added a reconnect button + `reconnect()`
implementation, runtime mouse-mode negotiation, write-side
clipboard echo prevention, physical key mapping for non-US
keyboards, eframe window persistence, fullscreen stats-bar
auto-hide, traffic viewer playback-channel filter, a
protocol gaps floating window, and the
`VD_AGENT_ANNOUNCE_CAPABILITIES = 6` constant fix.

The PR went through two rebase rounds against develop.
During the second round (2026-04-27) develop had landed
two commits that overlapped PR 31 directly:

- `10f19477` — added the u16 MOUSE_MODE wire-format parser,
  a `mouse_mode_request_pending` request-loop guard, and a
  `maybe_request_client_mouse_mode` helper called from both
  INIT and MOUSE_MODE handlers. Strictly better than PR 31's
  `set_mouse_mode` helper.
- `b0cac3ba` — added a MULTI_MEDIA_TIME handler equivalent
  to PR 31's.

The second-rebase merge commit took develop's resolution in
both places, removed PR 31's now-duplicate match arms, and
dropped its now-dead `set_mouse_mode` and `request_mouse_mode`
helpers. A separate one-liner on the same branch corrected
the `MOUSE_MODE_REQUEST` wire format to u16 (the bug that
was originally blocking #3 in the review, which also existed
on develop after `10f19477` landed).

The contributor did not re-engage between 2026-04-22 and
2026-04-27. We chose to land PR 31 with the immediate fixes
above and capture the remaining items here.

## Must fix

All blocking items from the original review have been resolved:

1. ~~`app.rs` keyboard repeat filter dropped — auto-repeat
   double-fires.~~ Fixed by the contributor in `b14ccbd4`
   (restored `repeat: false` in the key-event match).
2. ~~`main_channel.rs` `set_mouse_mode()` lacks request-loop
   guard.~~ Resolved by develop's `10f19477` introducing
   `mouse_mode_request_pending` and the second-rebase merge
   adopting that helper.
3. ~~`main_channel.rs` `MOUSE_MODE_REQUEST` writes u32
   should be u16.~~ Fixed in
   `ryll/src/channels/main_channel.rs` `maybe_request_client_mouse_mode`
   on the rebase branch (one-line change, `write_u16` with
   `MOUSE_MODE_CLIENT as u16`).

## Should fix

1. **ANNOUNCE_CAPABILITIES = 6 regression test.** Develop
   carried `VD_AGENT_ANNOUNCE_CAPABILITIES = 1` for some
   time, which collided with `VD_AGENT_MOUSE_STATE = 1`
   and broke capability negotiation. PR 31 corrected the
   constant to 6 but added no regression guard. Add a unit
   test in `shakenfist-spice-protocol/src/constants.rs`
   that asserts `VD_AGENT_ANNOUNCE_CAPABILITIES != VD_AGENT_MOUSE_STATE`
   and that the constant value matches the spec
   (`spice-protocol/spice/vd_agent.h`).

2. **`MOUSE_MODE_REQUEST` wire-format regression test.**
   The blocking #3 fix is a one-liner that's easy to
   silently revert during a future refactor. Add a test in
   `ryll/src/channels/main_channel.rs` that builds a
   `MOUSE_MODE_REQUEST` payload via the same path
   `maybe_request_client_mouse_mode` uses and asserts the
   body is exactly 2 bytes. The existing
   `parse_mouse_mode_payload` tests cover the read side;
   this covers the write side.

3. **README: reconnect button + window persistence.** PR 31
   added user-visible features that aren't documented.
   Update `README.md` to describe the Reconnect button in
   the disconnect dialog and the eframe window persistence
   (window position/size restored on next launch). Mention
   that persistence pulls in ~4 transitive crates (audited
   when the PR landed).

4. **ARCHITECTURE: reconnect flow.** `ARCHITECTURE.md` does
   not yet describe how `reconnect()` tears down channels,
   spawns a fresh tokio runtime, and rebuilds the
   `SpiceClient`. The block belongs alongside the existing
   "Connection lifecycle" section. Note the relationship
   to `RyllApp::reconnect_virtual_disks` /
   `reconnect_share_dir` for state preservation across
   reconnects.

5. **ARCHITECTURE: mouse-mode model.** The mouse-mode model
   was significantly expanded by `10f19477` and PR 31's
   reconnect flow interacts with it. Document:
   - The two modes (SERVER / relative, CLIENT / absolute).
   - The negotiation: server sends supported + current at
     INIT, client requests CLIENT mode if supported but
     not current.
   - The `mouse_mode_request_pending` guard preventing
     request-loop storms.
   - The post-reboot recovery via the MOUSE_MODE handler
     calling `maybe_request_client_mouse_mode` again.

## Should consider

6. **`reconnect()` resource leak under spam.** Spamming the
   Reconnect button against an unresponsive server spawns a
   new connection thread + tokio runtime + SPICE socket
   without explicitly tearing down the previous attempt.
   The previous attempt's socket eventually times out, but
   threads and runtimes accumulate in the meantime. Fix:
   give `reconnect()` a `CancellationToken` (or a tracked
   `JoinHandle`) and signal cancel on the previous attempt
   before spawning the next.
   Location: `ryll/src/app.rs::reconnect`.

7. **Cursor-hide regression for popups over the surface.**
   PR 31 changed the cursor-hide predicate from
   `response.hovered()` to
   `surface_rect.contains(hover_pos)`. The new predicate
   keeps the cursor hidden even when a popup, tooltip, or
   the bug-report dialog is positioned over the surface.
   For overlays the host cursor should reappear so the
   user can interact with the overlay. Fix: combine the
   two predicates — hide only when hovering the surface
   *and* no overlay is consuming pointer events. egui's
   `ctx.wants_pointer_input()` is the conventional probe.
   Location: `ryll/src/app.rs` (search for
   `surface_rect.contains`).

8. **Clipboard echo CRLF/LF normalization.** The clipboard
   echo-prevention dedup compares raw text. On Windows and
   some Wayland compositors the round-tripped text differs
   in line endings (CRLF vs LF) and trailing whitespace,
   which causes the echo guard to miss and the clipboard
   to ping-pong. Fix: normalize line endings and trim
   trailing whitespace before comparing, or store a hash
   of the normalized text. Storing the hash is preferable
   for memory.
   Location: `ryll/src/channels/main_channel.rs`
   (`last_clipboard_text` and the comparison sites).

9. **Three-arm match in MOUSE_MODE log line.** A leftover
   `if current == 1 { "server" } else { "client" }` in
   PR 31's MOUSE_MODE log line reports `"client"` for
   `current == 0` (which a server can legitimately send
   during transitions). Convert to a three-arm match
   matching the INIT handler's pattern (`1 => "server"`,
   `2 => "client"`, `_ => "unknown"`). Note: this may
   already be moot if the second-rebase merge replaced
   that log line wholesale; verify before opening the
   item.
   Location: `ryll/src/channels/main_channel.rs` MOUSE_MODE
   handler. **Verify still relevant** before scheduling.

## Informational

These came up in the original review but do not require
action; recording them so they don't resurface as questions:

- **`MouseMotion` struct mentioned in the PR description
  but not in the diff.** The PR description listed it as a
  contribution, but it never appeared. ryll has no relative
  mouse motion path, and CLIENT (absolute) mode is the
  preferred negotiation outcome anyway, so this is not a
  gap. No action.
- **`MULTI_MEDIA_TIME` debug-logged but not consumed for
  A/V sync.** Develop's `b0cac3ba` adopted the same
  shape — debug-log only, no consumer. ryll has no A/V
  sync logic at all (playback runs free), so the message
  type exists primarily to satisfy the `--pedantic` gap
  observer. If A/V sync is implemented later, the consumer
  belongs in `playback.rs`.

## Test coverage gaps

Beyond the two regression tests in Should fix items 1-2,
no further coverage gaps were identified during this
review. Develop's `10f19477` already added tests for
`parse_mouse_mode_payload` and `should_request_client_mouse_mode`
at `ryll/src/channels/main_channel.rs:998-1046`.

## Administration

### Tracking

Items in this plan are independent and can be picked up in
any order. Suggested batching for follow-up PRs:

- **One PR for items 1-2** (regression tests). Small,
  self-contained, no behaviour change.
- **One PR for items 3-5** (docs sweep). README +
  ARCHITECTURE updates only.
- **One PR per item in Should consider** (6, 7, 8, 9).
  Each has independent failure modes and should be
  reviewable on its own.

### Context

Created during the second-rebase merge of PR 31
(`hhding/all-features` → `develop`). The original review
findings live in:

- https://github.com/shakenfist/ryll/pull/31#issuecomment-4275275195
  (initial findings)
- https://github.com/shakenfist/ryll/pull/31#issuecomment-4295262488
  (follow-up after b14ccbd4)
