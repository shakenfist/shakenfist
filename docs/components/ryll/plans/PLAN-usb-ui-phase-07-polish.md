# Phase 7: Status feedback and polish

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Visual polish and edge case hardening for the USB panel.
The core event plumbing (`UsbConnectFailed`,
`UsbDeviceDisconnected`, usbredir channel disconnect) was
done in phases 2 and 5. This phase focuses on presentation
and robustness.

## Detailed steps

### Step 1: Connection timestamp

Add `usb_connected_at: Option<Instant>` to `RyllApp`. Set
it in the `UsbDeviceConnected` handler, clear it in
`UsbDeviceDisconnected` and usbredir `Disconnected`.

In the panel's "Connected:" line, append the elapsed time:

```rust
if let Some(ref desc) = self.usb_device_description {
    ui.separator();
    let elapsed = self
        .usb_connected_at
        .map(|t| t.elapsed().as_secs())
        .unwrap_or(0);
    let mins = elapsed / 60;
    let secs = elapsed % 60;
    ui.label(format!(
        "Connected: {} ({}m {}s)",
        desc, mins, secs,
    ));
}
```

Since egui repaints on events, the timer won't update
every second unless we request it. Add
`ctx.request_repaint_after(Duration::from_secs(1))` when
a device is connected so the elapsed time ticks.

### Step 2: Improve error presentation

Currently USB errors are a plain red label. Improve:

- Show a dismissible error by adding a small "X" button
  next to the error that clears `usb_error_message`.
- Also auto-clear errors after 10 seconds using an
  `usb_error_time: Option<Instant>` field. Check elapsed
  time each frame and clear if > 10s.
- The Refresh and Connect/Disconnect actions already
  clear the error (phase 5), so this mainly helps for
  errors the user doesn't act on.
- Add a "Report this as a bug" button next to the error.
  Clicking it opens the existing bug report dialog
  pre-populated with the error:
  - Set `show_bug_dialog = true`.
  - Set `bug_report_type = BugReportType::Usb` (new
    variant — see below).
  - Set `bug_description` to the error message text.
- Add a `Usb` variant to `BugReportType` in
  `src/bugreport.rs`. Its `channel_name()` returns
  `"usbredir"` so the bug report captures the usbredir
  channel's traffic ring buffer and state snapshot.
  This gives the report relevant protocol-level context
  for diagnosing USB failures.
- Use the two-pass pattern (collect action, execute
  after closure) since opening the dialog requires
  mutating fields that the panel closure borrows.

### Step 3: Disconnecting indicator

Currently clicking Disconnect sets `usb_connecting = true`
and shows "Connecting...". This is misleading for a
disconnect operation. Track the operation type:

Replace `usb_connecting: bool` with an enum or add a
second field. The simplest approach: add
`usb_disconnecting: bool`.

- Set `usb_disconnecting = true` when DisconnectDevice is
  sent, `usb_connecting = true` for connect commands.
- Clear both in `UsbDeviceConnected`,
  `UsbDeviceDisconnected`, `UsbConnectFailed`, and
  usbredir `Disconnected` handlers.
- Show "Disconnecting..." instead of "Connecting..." when
  `usb_disconnecting` is true.
- Disable buttons when either flag is true.

### Step 4: Visual consistency with traffic viewer

Match the traffic viewer's styling:

- The traffic viewer uses `default_width(350.0)`. The USB
  panel uses `default_width(300.0)`. These are reasonable
  defaults — the USB panel has less content so 300 is fine.
  No change needed.
- Both use `ui.heading()` for the panel title and
  `ui.separator()` between sections. Already consistent.
- The traffic viewer has `ui.label(format!(...))` for its
  message count next to the heading. The USB panel has
  a Refresh button there. Both are `ui.horizontal` with
  heading + small button/label. Consistent.

Verify: ensure the USB panel's ScrollArea behaves well
when the device list is long (many physical devices).
The `egui::ScrollArea::vertical()` call should handle
this. No change expected.

### Step 5: Guard against rapid command queueing

If the user clicks Connect, then quickly clicks Disconnect
before the first completes, two commands queue up. The
`usb_connecting` / `usb_disconnecting` flags disable
buttons, which prevents this in normal use. But verify
the channel buffer (size 16) can't accumulate stale
commands. Since each operation completes with an event
that clears the flags before the next click is possible,
this is not a real issue. Just verify manually.

If a problem is found, use `usb_tx.try_send()` which
returns `TrySendError::Full` if the channel is full —
already handled in phase 5 (it shows an error message).
No code change anticipated.

## Files changed

| File | Change |
|------|--------|
| `src/bugreport.rs` | Add `BugReportType::Usb` variant with `channel_name()` returning `"usbredir"` |
| `src/app.rs` | Add `usb_connected_at`, `usb_error_time`, `usb_disconnecting` fields; update event handlers; improve error display with dismiss button, auto-clear, and "Report this as a bug" button; add connection elapsed timer with repaint request; separate connecting/disconnecting indicators |

## What is NOT in scope

- Transfer statistics in the panel (future work).
- Keyboard shortcuts for the USB panel.
- Any changes to the channel handler or protocol.

## Testing

- `make build` — compiles without errors.
- `make test` — all existing tests pass.
- `pre-commit run --all-files` — clean.
- Manual: connect a virtual disk, verify elapsed timer
  ticks in the panel. Disconnect, verify "Disconnecting..."
  appears briefly. Trigger an error (e.g. unplug device
  between refresh and connect), verify red error with
  dismiss button appears and auto-clears after 10 seconds.

## Back brief

This phase adds connection timestamps with live elapsed
time, separates connecting/disconnecting indicators,
improves error display with dismiss button and auto-clear,
and verifies visual consistency with the traffic viewer.
All presentation changes, no protocol or plumbing changes.
