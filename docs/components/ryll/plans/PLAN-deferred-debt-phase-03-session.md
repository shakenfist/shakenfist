# Phase 3: Input and session correctness

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Fix bugs in keyboard input, connection lifecycle, and
mouse interaction. Three sub-tasks:

| Step | Description | Source |
|------|-------------|--------|
| 3a | Modifier keys duplicate events | PLAN-pr20-followup.md #4 |
| 3b | Disconnect event scope | PLAN-pr20-followup.md #2 |
| 3c | Mouse clicks through kerbside | PLAN-remaining-issues.md #2 |

## 3a. Modifier keys duplicate events

### Investigation result: no fix needed

The original concern was that `key_to_scancode()` might
handle modifier keys (Ctrl, Shift, Alt), causing the
explicit modifier tracking at app.rs:778-806 to produce
duplicate KeyDown/KeyUp events and stuck modifiers.

**This is not the case.** Investigation of the code
reveals:

1. `key_to_scancode()` (inputs.rs:556-656) does **not**
   contain any modifier key entries. The map covers
   letters, numbers, function keys, navigation, arrows,
   and punctuation only.

2. egui does not expose modifier keys as `egui::Key`
   variants. Modifiers are only available through
   `i.modifiers`, which is exactly what the explicit
   tracking at app.rs:778-806 uses.

3. The two code paths (Key event loop at lines 756-776
   and modifier tracking at lines 778-806) are
   non-overlapping by design: one handles printable/
   function keys via scancodes, the other handles
   modifier state changes via `i.modifiers`.

**Minor ordering note:** The modifier KeyDown is sent
*after* the key event loop processes all key events in
the same frame. This means for a Ctrl+C keystroke, the
'C' KeyDown may be queued before the Ctrl KeyDown. In
practice this is benign: both events are sent in the
same frame and arrive in sequence on the SPICE wire.
The SPICE server processes them as a batch. Reordering
to send modifier changes first would be a potential
improvement but is not needed to fix a correctness bug.

### Action

Mark this item as **verified -- no fix needed** in the
source plan (PLAN-pr20-followup.md). No code changes
required.

## 3b. Disconnect event scope

### Bug

The `ChannelEvent::Disconnected` handler at app.rs:605-627
unconditionally sets `self.connected = false` and shows
the disconnect dialog for **any** channel type, including
Usbredir, Webdav, Playback, and Cursor. These channels
have independent lifecycles:

- A USB device being unplugged triggers a Usbredir
  channel disconnect.
- A WebDAV sharing session ending triggers a Webdav
  channel disconnect.
- The Playback channel disconnects on audio stop.

In all these cases, the Main and Display channels remain
active, yet the user sees "Connection lost (Usbredir
channel disconnected)" and the display is replaced with
"Connecting..." (because `self.connected = false` at
app.rs:1560 gates display rendering).

The headless mode at app.rs:2317 already correctly
handles this -- it only exits on
`ChannelEvent::Disconnected(ChannelType::Main)`.

### Fix

Only set `self.connected = false` and show the disconnect
dialog for critical channels. The critical channels are:

- **Main** -- the control channel; loss means the session
  is over.
- **Display** -- without display data, the UI cannot
  render.

For non-critical channels (Usbredir, Webdav, Cursor,
Inputs, Playback), the handler should:

1. Log the disconnect at `info!` level.
2. Perform channel-specific cleanup (the USB and WebDAV
   cleanup at lines 615-626 already exists).
3. **Not** set `self.connected = false`.
4. **Not** show the disconnect dialog.

For `Inputs`, the channel is critical for interaction
but its loss doesn't necessarily mean the connection is
dead -- the display may still be rendering. However, if
the Inputs channel disconnects, the user can't interact
with the VM, so showing a status message (not the
disconnect dialog) would be appropriate. For simplicity,
treat Inputs as critical in this fix.

### Implementation

Replace the unconditional block at app.rs:605-614 with
a match on the channel type:

```rust
ChannelEvent::Disconnected(channel) => {
    info!("app: channel {} disconnected", channel.name());

    // Channel-specific cleanup
    if channel == ChannelType::Usbredir {
        self.usb_channel_ready = false;
        self.usb_device_description = None;
        self.clear_usb_operation_flags();
        self.usb_connected_at = None;
    }
    if channel == ChannelType::Webdav {
        self.webdav_channel_ready = false;
        self.webdav_shared_dir = None;
        self.webdav_sharing_active = false;
        self.webdav_connected_at = None;
    }

    // Only show disconnect dialog for critical channels
    match channel {
        ChannelType::Main
        | ChannelType::Display
        | ChannelType::Inputs => {
            self.connected = false;
            if !self.show_disconnect_dialog {
                self.show_disconnect_dialog = true;
                self.disconnect_reason = Some(format!(
                    "Connection lost ({} channel disconnected)",
                    channel.name()
                ));
            }
        }
        _ => {
            debug!(
                "app: non-critical channel {} disconnected, \
                 session continues",
                channel.name()
            );
        }
    }
}
```

### Files to modify

- `ryll/src/app.rs` -- `ChannelEvent::Disconnected`
  handler

### Testing

- `cargo test --workspace` must pass.
- Manual test: connect a USB device, then disconnect it.
  Verify the display continues to render and no
  disconnect dialog appears.

## 3c. Mouse clicks through kerbside proxy

### Investigation result

This is a deeper issue than originally suspected. The
investigation revealed the likely root cause:

**Ryll always sends `MOUSE_POSITION` (absolute, opcode
112) regardless of the server's mouse mode.** The SPICE
protocol has two mouse movement modes:

- **Server mode (mode=1):** The server controls the
  cursor. The client should send `MOUSE_MOTION` (opcode
  111) with relative deltas (dx, dy).
- **Client mode (mode=2):** The client controls the
  cursor. The client sends `MOUSE_POSITION` (opcode 112)
  with absolute coordinates (x, y).

When connecting through kerbside, the server may report
`current_mouse_mode=1` (server mode). In this mode, QEMU
drives the PS/2 or virtio mouse device with relative
motion. `MOUSE_POSITION` messages are ignored because the
server has no way to translate absolute coordinates to
relative motion without the SPICE agent.

The `mouse_mode` field is set at app.rs:519 from
`ChannelEvent::MouseMode`, but it is only used for a UI
label (app.rs:978). The mouse event generation code at
app.rs:1598-1668 and the wire-level handlers in
inputs.rs:366-478 unconditionally use `MOUSE_POSITION`.

Additionally, ryll never sends `MOUSE_MODE_REQUEST` on
the main channel to request client mode at startup.

### Plan

This is a multi-part fix:

**3c-i. Add logging for mouse mode.** When the server
sends `current_mouse_mode` in the INIT message, log it
prominently. This confirms the theory.

**3c-ii. Support server mouse mode.** When `mouse_mode`
is 1 (server mode):

- Track the previous mouse position.
- Compute deltas: `dx = x - prev_x`, `dy = y - prev_y`.
- Send `MOUSE_MOTION` (opcode 111) with (dx, dy) instead
  of `MOUSE_POSITION` (opcode 112) with (x, y).
- `MOUSE_PRESS` and `MOUSE_RELEASE` should still work
  with the current button state, but they need to be
  sent alongside `MOUSE_MOTION` instead of
  `MOUSE_POSITION` for pre-positioning.

This requires:
- Adding `MOUSE_MOTION` opcode to the protocol constants
  (if not already defined).
- Adding a `MouseMotion { dx, dy }` variant to
  `InputEvent`.
- Adding a handler in `InputsChannel` to send
  `MOUSE_MOTION` messages on the wire.
- Modifying app.rs mouse handling to check `mouse_mode`
  and dispatch accordingly.
- Passing `mouse_mode` to the input event generation
  code (currently it's only in `RyllApp`, not accessible
  from the input closure).

**3c-iii. Request client mouse mode.** On the main
channel, after receiving the server's INIT message, send
`MOUSE_MODE_REQUEST` to request client mode (mode=2).
If the server grants it (agent is running, guest supports
it), absolute positioning will work. If the server denies
it, fall back to server mode with relative motion from
step 3c-ii.

### Complexity and risk

This is the largest and most uncertain item in phase 3.
It involves:
- Protocol changes (new opcode, new message type).
- Behavioural changes across app.rs and inputs.rs.
- Testing against a real kerbside proxy to verify.

The root cause theory (server mode vs client mode) is
well-supported by the protocol specification and the
observed behaviour, but has not been confirmed with wire
captures.

### Recommended approach

Given the complexity and uncertainty, implement 3c in
stages:

1. **First commit: add mouse mode logging** (3c-i).
   Log the received `current_mouse_mode` prominently so
   future debugging can confirm the mode. This is a
   one-line change with zero risk.

2. **Second commit: request client mode** (3c-iii).
   Send `MOUSE_MODE_REQUEST` on startup. If the server
   grants it, absolute positioning works and no further
   changes are needed. This is the simplest fix if the
   server/agent supports it.

3. **Third commit: implement server mode** (3c-ii).
   Only needed if the server refuses client mode. This
   is the most complex change and can be deferred if
   step 2 resolves the issue.

### Files to modify

- `ryll/src/channels/main_channel.rs` -- mouse mode
  logging, MOUSE_MODE_REQUEST
- `ryll/src/app.rs` -- mouse event dispatch based on
  mode
- `ryll/src/channels/inputs.rs` -- MOUSE_MOTION handler
- `shakenfist-spice-protocol/src/constants.rs` -- new
  opcodes if needed

### Testing

- `cargo test --workspace` must pass.
- Manual test through kerbside: verify mouse clicks
  produce display responses after the fix.
- The investigation notes that the Python test tool
  also fails, so this may also require kerbside-side
  investigation if the ryll-side fix doesn't resolve it.

## Commit sequence

1. **3b**: Fix disconnect event scope (standalone,
   no dependencies).
2. **3c-i**: Add mouse mode logging (one-line, zero
   risk).
3. **3c-iii**: Send MOUSE_MODE_REQUEST on startup
   (if the protocol constant exists or can be added).
4. **3c-ii**: Implement server mouse mode (if step 3
   doesn't resolve the issue -- may be deferred).

Step 3a requires no code changes.

## Administration

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
