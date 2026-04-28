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

1. ~~**ANNOUNCE_CAPABILITIES = 6 regression test.**~~
   Landed in Phase 1 — see
   [PLAN-pr31-followup-phase-01-tests.md](/components/ryll/plans/PLAN-pr31-followup-phase-01-tests/).
   Test lives next to the constants in
   `ryll/src/channels/main_channel.rs` rather than the
   shared protocol crate (constants are still file-private
   in main_channel.rs; rationale in the phase plan).

2. ~~**`MOUSE_MODE_REQUEST` wire-format regression test.**~~
   Landed in Phase 1 — see
   [PLAN-pr31-followup-phase-01-tests.md](/components/ryll/plans/PLAN-pr31-followup-phase-01-tests/).
   Encoding extracted into
   `build_mouse_mode_request_payload` so the byte shape
   can be asserted without constructing a `MainChannel`.

3. ~~**README: reconnect button + window persistence.**~~
   Landed in Phase 2 — see
   [PLAN-pr31-followup-phase-02-docs.md](/components/ryll/plans/PLAN-pr31-followup-phase-02-docs/).
   Two new Features bullets (Reconnect on disconnect,
   Window persistence) cover the user-visible behaviour;
   the persistence bullet names the per-platform config
   directory rather than counting transitive crates,
   which turned out to be the more useful detail.

4. ~~**ARCHITECTURE: reconnect flow.**~~ Landed in Phase 2 —
   see
   [PLAN-pr31-followup-phase-02-docs.md](/components/ryll/plans/PLAN-pr31-followup-phase-02-docs/).
   New top-level `## Reconnection` section after Graceful
   Shutdown, structured as Trigger / What is recreated /
   What survives / Threading and runtime. Cross-links to
   item 6 below for the cancellation-token follow-up.

5. ~~**ARCHITECTURE: mouse-mode model.**~~ Landed in Phase 2
   — see
   [PLAN-pr31-followup-phase-02-docs.md](/components/ryll/plans/PLAN-pr31-followup-phase-02-docs/).
   New `### Mouse-Mode Negotiation` subsection under
   `## SPICE Protocol` covers the two modes, both wire-
   format directions, the negotiation flow at INIT and
   on subsequent server-driven mode changes, and the
   `mouse_mode_request_pending` guard. The previous
   inline mention in the input-channel bullet is now a
   pointer to the canonical text.

## Should consider

6. ~~**`reconnect()` resource leak under spam.**~~ Landed
   in Phase 3 — see
   [PLAN-pr31-followup-phase-03-polish.md](/components/ryll/plans/PLAN-pr31-followup-phase-03-polish/).
   Implementation uses an `Arc<AtomicBool>` mirroring the
   existing `SHUTDOWN_REQUESTED` cooperative-cancel
   pattern; `RyllApp::reconnect` raises the previous flag
   before spawning the next attempt and a small watcher
   task aborts the channel JoinHandles when the flag is
   set.

7. ~~**Cursor-hide regression for popups over the surface.**~~
   Landed in Phase 3 — see
   [PLAN-pr31-followup-phase-03-polish.md](/components/ryll/plans/PLAN-pr31-followup-phase-03-polish/).
   Resolved by adding `!ctx.wants_pointer_input()` to the
   AND chain in the cursor-hide predicate.

8. ~~**Clipboard echo CRLF/LF normalization.**~~ Landed in
   Phase 3 — see
   [PLAN-pr31-followup-phase-03-polish.md](/components/ryll/plans/PLAN-pr31-followup-phase-03-polish/).
   `last_clipboard_text: Option<String>` became
   `last_clipboard_hash: Option<u64>` storing a hash of
   the normalised text; three new unit tests guard the
   invariants.

9. ~~**Three-arm match in MOUSE_MODE log line.**~~ Closed
   without code changes — verified against current
   `main_channel.rs`. Develop's `10f19477` and our
   second-rebase merge (`9aa12b9e`) replaced both INIT
   and MOUSE_MODE handlers wholesale; both now use
   three-arm matches. The buggy `if current == 1 ... else
   "client"` line PR 31 had no longer exists.

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

- ~~**One PR for items 1-2** (regression tests).~~ Phase 1
  landed; see
  [PLAN-pr31-followup-phase-01-tests.md](/components/ryll/plans/PLAN-pr31-followup-phase-01-tests/).
- ~~**One PR for items 3-5** (docs sweep).~~ Phase 2
  landed; see
  [PLAN-pr31-followup-phase-02-docs.md](/components/ryll/plans/PLAN-pr31-followup-phase-02-docs/).
- ~~**One PR per item in Should consider** (6, 7, 8, 9).~~
  Phase 3 landed three commits (one per live item:
  reconnect cancellation, cursor-hide overlay, clipboard
  hash); item 9 closed without code as it was made moot
  by develop's `10f19477`. See
  [PLAN-pr31-followup-phase-03-polish.md](/components/ryll/plans/PLAN-pr31-followup-phase-03-polish/).

### Context

Created during the second-rebase merge of PR 31
(`hhding/all-features` → `develop`). The original review
findings live in:

- https://github.com/shakenfist/ryll/pull/31#issuecomment-4275275195
  (initial findings)
- https://github.com/shakenfist/ryll/pull/31#issuecomment-4295262488
  (follow-up after b14ccbd4)
