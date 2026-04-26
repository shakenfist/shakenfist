# Phase 3: GUI gesture for paste-as-keystrokes

## Overview

Add the interactive GUI surface for paste-as-keystrokes: a
menu entry in the hamburger menu, a `Ctrl+Alt+V` keyboard
shortcut, and an error dialog for unrepresentable clipboard
contents. The entire surface is gated on the
`--enable-paste-as-keystrokes` CLI flag from Phase 2 — when
absent, the GUI is identical to today.

## Parent plan

[PLAN-paste-as-keystrokes.md](/components/ryll/plans/PLAN-paste-as-keystrokes/)

## Design

### Surface overview

Three new pieces of GUI:

1. **Menu entry**: A "Paste" button inside the hamburger
   menu (`app.rs:1598`), with shortcut text "Ctrl+Alt+V".
   Visible only when `self.enable_paste` is true. Disabled
   (greyed out) when vdagent is connected — the vdagent
   clipboard path is faster and Unicode-clean.

2. **Keyboard shortcut**: `Ctrl+Alt+V` detected in the
   `update()` method alongside the existing F8/F11/F12
   shortcuts (`app.rs:1430–1460`). Only honoured when
   `self.enable_paste` is true and no dialog or region
   selection is active.

3. **Error dialog**: An informational `egui::Window` shown
   when the clipboard contains unrepresentable characters
   (non-US-QWERTY). Follows the bug-report dialog pattern
   (`app.rs:2266`): centred, non-collapsible, with an OK
   button. Dismissed by OK or Escape.

### Agent-connected state

The `agent_connected` flag lives inside `MainChannel`
(`main_channel.rs:71`) and is currently private. Phase 3
needs it in `RyllApp` to disable the paste button when
vdagent is active.

The cleanest approach: add a new `ChannelEvent` variant
`AgentConnected(bool)` emitted by `MainChannel` whenever
the flag changes (on `AGENT_CONNECTED`, `AGENT_DISCONNECTED`,
and the initial `INIT` message). `RyllApp` handles it by
updating a new `agent_connected: bool` field. This follows
the existing pattern — `UsbChannelReady`, `MouseMode`, and
`WebdavChannelReady` all surface channel state the same way.

The paste menu entry is then disabled when
`self.agent_connected` is true. The Ctrl+Alt+V shortcut
is similarly gated: if vdagent is connected, the shortcut
is silently ignored (no error dialog — the operator should
use normal Ctrl+V via the vdagent path).

### Clipboard access

The host clipboard is read via `arboard::Clipboard`. The
`MainChannel` already caches a clipboard instance
(`main_channel.rs:81`), but that instance lives in a
different async task and is not accessible from `RyllApp`.

Phase 3 creates a separate `arboard::Clipboard` instance
in `RyllApp`, lazily initialised on first use (same pattern
as `MainChannel::clipboard()` at `main_channel.rs:146`).
This is safe — `arboard` supports multiple concurrent
clipboard instances. The clipboard is read with
`.get_text()` which returns `Result<String>`.

### Pre-validation and error dialog

Before sending `InputEvent::PasteText`, the GUI calls
`translate_paste()` on the clipboard text. If it returns
`Err(PasteError::Unrepresentable { count, sample })`,
the paste is aborted and an error dialog is shown:

> **Cannot paste as keystrokes**
>
> The clipboard contains N character(s) that have no
> US-QWERTY scancode mapping: U+XXXX, U+YYYY, ...

The dialog has a single "OK" button and can be dismissed
with Escape.

The `PasteFailed` event from the inputs channel also
triggers this dialog (e.g. if the truncation+translate
in the channel itself fails for some reason). The dialog
message is set from the `PasteFailed` reason string.

### Menu entry placement

The "Paste" entry goes inside the hamburger menu closure
(`app.rs:1598`), after the "Report" entry and before the
menu closure ends. It is rendered as a plain button (not
a checkbox — paste is a one-shot action). The shortcut
text "Ctrl+Alt+V" is shown to the right of the label.

When `self.agent_connected` is true, the button is
rendered with `ui.add_enabled(false, ...)` and a tooltip
explaining "vdagent is connected — use Ctrl+V instead".

### Shortcut detection

The `Ctrl+Alt+V` shortcut is detected in the `update()`
method, in the same block as F8/F11/F12 (around
`app.rs:1430`). It sits *before* the call to
`self.handle_input(ctx)` at `app.rs:1472`, which is
critical: `handle_input` translates raw key events into
SPICE scancodes. If `Ctrl+Alt+V` is detected first, we
must consume the event so `handle_input` does not also
send the `V` keypress to the guest.

Detection approach: use `ctx.input(|i| ...)` to check
`i.modifiers.ctrl && i.modifiers.alt` combined with
`i.key_pressed(egui::Key::V)`. This is the same pattern
the existing modifier check at `app.rs:1209` uses.

When the shortcut fires:
1. Check `self.enable_paste` — if false, ignore.
2. Check `self.agent_connected` — if true, ignore.
3. Check `self.paste_error_message.is_some()` — if the
   error dialog is open, ignore (prevent re-trigger).
4. Read the host clipboard.
5. If clipboard read fails, set an error message and show
   the error dialog.
6. If clipboard is empty, ignore (nothing to paste).
7. Call `translate_paste()` on the clipboard text.
8. If translation fails, set the error message from the
   error details and show the dialog.
9. If translation succeeds, send
   `InputEvent::PasteText { text, char_delay_ms }` via
   `input_tx.try_send()`.

The shortcut must also set a `consume_next_v` flag (or
equivalent) so that the `V` keypress event is not
forwarded to the guest by `handle_input`. The simplest
approach: when `Ctrl+Alt+V` is detected, set
`self.suppress_next_input = true` and have `handle_input`
check and clear this flag, returning early for that frame.
Actually, since `handle_input` already returns early when
`self.show_bug_dialog` is true, and the shortcut detection
runs before `handle_input`, a simpler approach is to
consume the input event via egui's event consumption.
However, egui doesn't support consuming individual events
in the `input()` closure.

The cleanest solution: the shortcut check already runs
before `handle_input`. When Ctrl+Alt+V fires, we can
skip `handle_input` for this frame by gating the call:

```
if !paste_triggered_this_frame {
    self.handle_input(ctx);
}
```

This is safe because a frame where the operator triggers
a paste is a frame where normal input forwarding can be
skipped. The paste state machine in the inputs channel
will release modifiers anyway (Phase 2's modifier
save/restore).

### New RyllApp fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `agent_connected` | `bool` | `false` | Mirrors vdagent connection state |
| `cached_clipboard` | `Option<arboard::Clipboard>` | `None` | Lazy clipboard instance |
| `paste_error_message` | `Option<String>` | `None` | Error dialog message (None = dialog hidden) |

### Removing `#[allow(dead_code)]`

The `enable_paste` and `paste_char_delay_ms` fields on
`RyllApp` were marked `#[allow(dead_code)]` in Phase 2
with a comment "used in Phase 3". Phase 3 removes those
attributes since the fields are now used.

## Current code state and exact locations

### Hamburger menu (`app.rs:1598-1616`)

```rust
egui::menu::menu_button(ui, "☰", |ui| {
    ui.checkbox(&mut self.show_traffic_viewer, "Traffic");
    ui.checkbox(&mut self.show_usb_panel, "USB");
    ui.checkbox(&mut self.show_webdav_panel, "Folders");
    if ui
        .add(egui::Button::new("Screenshot").shortcut_text("F8"))
        .clicked()
    {
        self.open_screenshot_dialog();
        ui.close_menu();
    }
    if ui.button("Report").clicked() {
        // ... Report button logic ...
        ui.close_menu();
    }
});
```

### Keyboard shortcuts (`app.rs:1430-1472`)

```rust
// F12 toggles bug report dialog (not during region selection)
if !self.region_select_active {
    let f12_pressed = ctx.input(|i| i.key_pressed(egui::Key::F12));
    // ...
}
// F11 toggles traffic viewer
// F8 opens screenshot
// Escape closes dialog

// Handle input
self.handle_input(ctx);
```

### Bug report dialog pattern (`app.rs:2266-2354`)

Two-pass pattern: render the dialog window, collect an
action into a local `Option`, then execute the action
after the `egui::Window::show()` closure returns. This
avoids borrow conflicts between the `show()` closure and
`self`.

### PasteCompleted/PasteFailed handling (`app.rs:877-889`)

Already writes to `self.bug_status_message`. Phase 3
changes `PasteFailed` to also set the error dialog:

```rust
ChannelEvent::PasteFailed { reason } => {
    error!("app: paste failed: {}", reason);
    self.paste_error_message = Some(reason.clone());
}
```

### `agent_connected` in MainChannel (`main_channel.rs`)

Set on lines 324 (from INIT), 551 (AGENT_CONNECTED),
and 557 (AGENT_DISCONNECTED). Each site needs to emit
a `ChannelEvent::AgentConnected(bool)`.

### RyllApp struct fields (`app.rs:333-339`)

```rust
/// Whether paste-as-keystrokes is enabled.
#[allow(dead_code)] // used in Phase 3 (GUI paste button)
enable_paste: bool,

/// Inter-character delay for paste-as-keystrokes in ms.
#[allow(dead_code)] // used in Phase 3 (GUI paste button)
paste_char_delay_ms: u32,
```

### Input event sending pattern (`app.rs:1179-1204`)

```rust
let input_tx = match &self.input_tx {
    Some(tx) => tx.clone(),
    None => return,
};
// ...
let _ = input_tx.try_send(ev);
```

## Implementation steps

### Step 1: Add `AgentConnected` event to `channels/mod.rs`

Add a new variant to `ChannelEvent`:

```rust
/// vdagent connection state changed.
AgentConnected(bool),
```

### Step 2: Emit `AgentConnected` from `MainChannel`

In `main_channel.rs`, emit `ChannelEvent::AgentConnected`
at the three sites where `self.agent_connected` changes:

- Line 324 (after `self.agent_connected = init.agent_connected != 0`):
  ```rust
  self.event_tx
      .send(ChannelEvent::AgentConnected(self.agent_connected))
      .await
      .ok();
  ```

- Line 551 (after `self.agent_connected = true`):
  ```rust
  self.event_tx
      .send(ChannelEvent::AgentConnected(true))
      .await
      .ok();
  ```

- Line 557 (after `self.agent_connected = false`):
  ```rust
  self.event_tx
      .send(ChannelEvent::AgentConnected(false))
      .await
      .ok();
  ```

### Step 3: Add new fields to `RyllApp`

Add to the struct definition (after `paste_char_delay_ms`):

```rust
/// Whether the guest has a vdagent connected (disables
/// paste-as-keystrokes in favour of the clipboard path).
agent_connected: bool,

/// Cached clipboard instance for reading host clipboard.
cached_clipboard: Option<arboard::Clipboard>,

/// Error message for the paste error dialog (None = hidden).
paste_error_message: Option<String>,
```

Initialise all three in `RyllApp::new()`:
- `agent_connected: false`
- `cached_clipboard: None`
- `paste_error_message: None`

Remove the `#[allow(dead_code)]` attributes from
`enable_paste` and `paste_char_delay_ms`.

### Step 4: Handle `AgentConnected` in `process_events()`

Add a match arm in `process_events()`:

```rust
ChannelEvent::AgentConnected(connected) => {
    info!("app: vdagent connected={}", connected);
    self.agent_connected = connected;
}
```

### Step 5: Update `PasteFailed` handling in `process_events()`

Change the existing `PasteFailed` arm to also set the
error dialog:

```rust
ChannelEvent::PasteFailed { reason } => {
    error!("app: paste failed: {}", reason);
    self.paste_error_message = Some(reason);
}
```

Remove the `bug_status_message` line — paste failures now
show in the error dialog instead of a transient status
message.

### Step 6: Handle `AgentConnected` in headless event loop

In `app.rs::run_headless()`, add a match arm for
`AgentConnected` in the headless event loop. Just log it:

```rust
ChannelEvent::AgentConnected(connected) => {
    info!("headless: vdagent connected={}", connected);
}
```

### Step 7: Add clipboard helper method

Add a method to `RyllApp`:

```rust
/// Get or create the cached clipboard instance.
fn clipboard(&mut self) -> Option<&mut arboard::Clipboard> {
    if self.cached_clipboard.is_none() {
        match arboard::Clipboard::new() {
            Ok(cb) => self.cached_clipboard = Some(cb),
            Err(e) => {
                warn!("app: failed to open clipboard: {}", e);
                return None;
            }
        }
    }
    self.cached_clipboard.as_mut()
}
```

### Step 8: Add `trigger_paste` method

Add a method that encapsulates the paste trigger logic,
called by both the menu entry and the keyboard shortcut:

```rust
/// Attempt to paste the host clipboard as keystrokes.
/// Returns true if a paste was triggered (or an error
/// dialog was shown), false if there was nothing to do.
fn trigger_paste(&mut self) -> bool {
    if !self.enable_paste || self.agent_connected {
        return false;
    }

    // Read clipboard
    let text = match self.clipboard() {
        Some(cb) => match cb.get_text() {
            Ok(t) if !t.is_empty() => t,
            Ok(_) => return false, // empty clipboard
            Err(e) => {
                self.paste_error_message = Some(
                    format!("Failed to read clipboard: {}", e),
                );
                return true;
            }
        },
        None => {
            self.paste_error_message = Some(
                "No clipboard available".to_string(),
            );
            return true;
        }
    };

    // Pre-validate with the translator
    use crate::channels::inputs::{translate_paste, PasteError};
    match translate_paste(&text) {
        Ok(_) => {
            // Translation will succeed — send to the
            // inputs channel.
            if let Some(tx) = &self.input_tx {
                let _ = tx.try_send(InputEvent::PasteText {
                    text,
                    char_delay_ms: self.paste_char_delay_ms,
                });
            }
        }
        Err(PasteError::Unrepresentable { count, sample }) => {
            let sample_str: String = sample
                .iter()
                .map(|c| format!("U+{:04X}", *c as u32))
                .collect::<Vec<_>>()
                .join(", ");
            self.paste_error_message = Some(format!(
                "The clipboard contains {} character(s) that \
                 have no US-QWERTY scancode mapping: {}",
                count, sample_str,
            ));
            return true;
        }
    }

    true
}
```

**Visibility note**: `translate_paste` and `PasteError`
are currently private to `channels::inputs`. Phase 3
must make them `pub` (and `PasteKey` too, since
`translate_paste` returns `Vec<PasteKey>`). Add `pub`
to `translate_paste`, `PasteError`, and `PasteKey` in
`inputs.rs`.

### Step 9: Add Ctrl+Alt+V shortcut detection

In the `update()` method, in the shortcut block after
F8 and before the `self.handle_input(ctx)` call
(`app.rs:1460-1472`), add:

```rust
// Ctrl+Alt+V triggers paste-as-keystrokes (not during
// region selection or dialogs)
let mut paste_triggered = false;
if !self.region_select_active
    && !self.show_bug_dialog
    && self.paste_error_message.is_none()
{
    let ctrl_alt_v = ctx.input(|i| {
        i.modifiers.ctrl
            && i.modifiers.alt
            && i.key_pressed(egui::Key::V)
    });
    if ctrl_alt_v {
        paste_triggered = self.trigger_paste();
    }
}
```

Then gate the `handle_input` call:

```rust
if !paste_triggered {
    self.handle_input(ctx);
}
```

### Step 10: Add "Paste" entry to hamburger menu

Inside the `menu_button("☰", ...)` closure, after the
"Report" entry, add:

```rust
if self.enable_paste {
    ui.separator();
    let label = egui::Button::new("Paste")
        .shortcut_text("Ctrl+Alt+V");
    let enabled = !self.agent_connected;
    let response = ui.add_enabled(enabled, label);
    if response.clicked() {
        self.trigger_paste();
        ui.close_menu();
    }
    if !enabled {
        response.on_disabled_hover_text(
            "vdagent is connected — use Ctrl+V instead",
        );
    }
}
```

### Step 11: Add paste error dialog

After the bug-report dialog block (around `app.rs:2354`),
add a paste error dialog following the same pattern:

```rust
// Paste error dialog (two-pass: render then act)
let mut paste_dialog_action = None;
if let Some(ref msg) = self.paste_error_message {
    egui::Window::new("Cannot paste as keystrokes")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            ui.set_min_width(350.0);
            ui.label(msg);
            ui.add_space(8.0);
            if ui.button("OK").clicked() {
                paste_dialog_action = Some(());
            }
        });
}
if paste_dialog_action.is_some() {
    self.paste_error_message = None;
}

// Escape also dismisses the paste error dialog
if self.paste_error_message.is_some() {
    let esc = ctx.input(|i| i.key_pressed(egui::Key::Escape));
    if esc {
        self.paste_error_message = None;
    }
}
```

### Step 12: Make translator items public

In `channels/inputs.rs`, change the visibility of:

- `pub struct PasteKey` (was private)
- `pub enum PasteError` (was private)
- `pub fn translate_paste` (was private)

Also add `pub use inputs::{translate_paste, PasteError, PasteKey};`
to `channels/mod.rs` so the app can import them.

### Step 13: Update documentation

**README.md**: Add a line about `Ctrl+Alt+V` and the
menu entry under the paste-as-keystrokes feature
description.

**ARCHITECTURE.md**: Add a note about the GUI surface
(menu entry, shortcut, error dialog) under the
paste-as-keystrokes section.

**AGENTS.md**: Add `PasteKey`, `PasteError`, and
`translate_paste` to the public API list if one exists.

### Step 14: Run validation

```bash
pre-commit run --all-files
make test
```

Fix any rustfmt, clippy, or test failures.

## Files to modify

| File | Changes |
|------|---------|
| `ryll/src/channels/mod.rs` | Add `AgentConnected(bool)` to `ChannelEvent`; re-export `translate_paste`, `PasteError`, `PasteKey` |
| `ryll/src/channels/main_channel.rs` | Emit `AgentConnected` at the three sites where `agent_connected` changes |
| `ryll/src/channels/inputs.rs` | Make `PasteKey`, `PasteError`, `translate_paste` public |
| `ryll/src/app.rs` | Add `agent_connected`, `cached_clipboard`, `paste_error_message` fields; remove `#[allow(dead_code)]` from paste fields; add `clipboard()` and `trigger_paste()` methods; add Ctrl+Alt+V shortcut; add menu entry; add error dialog; handle `AgentConnected` in `process_events()` and headless; update `PasteFailed` to use error dialog |
| `README.md` | Document Ctrl+Alt+V shortcut and menu entry |
| `ARCHITECTURE.md` | Document GUI paste surface |
| `AGENTS.md` | Document public paste API |

## Step-level guidance

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1-14 | medium | sonnet | none | Implement all steps as a single pass — the changes are interdependent. The plan provides exact code snippets and file locations. Key judgement calls: (a) the Ctrl+Alt+V detection must run before `handle_input` to prevent the V keypress reaching the guest; (b) `translate_paste` and `PasteError` must be made `pub` for the pre-validation in `trigger_paste`; (c) the `AgentConnected` event follows the `UsbChannelReady` pattern. |

**Recommended execution**: Single sub-agent invocation
(sonnet, medium effort). The plan is detailed enough that
sonnet can follow it. The changes are well-precedented
(the error dialog mirrors the bug-report dialog; the
menu entry mirrors the existing entries; the shortcut
mirrors F8/F11/F12; the `AgentConnected` event mirrors
`UsbChannelReady`).

## Risks and mitigations

- **Ctrl+Alt+V reaching the guest**: If the shortcut
  detection runs after `handle_input`, the `V` key event
  will be sent to the guest as a keypress. Mitigated by
  detecting the shortcut before `handle_input` and
  skipping `handle_input` on that frame.

- **Clipboard access failure on headless/Wayland**: The
  `arboard::Clipboard::new()` call may fail on headless
  systems or under certain Wayland compositors. Mitigated
  by the lazy-init pattern with error handling — the error
  dialog informs the operator.

- **arboard dependency already present**: `arboard` is
  already a dependency (used in `main_channel.rs`). No
  new dependency needed.

- **`translate_paste` double-validation**: The GUI pre-
  validates the clipboard text, then the inputs channel
  validates again (in the `PasteText` handler). This is
  intentional — the channel must remain self-contained
  and safe regardless of what the GUI sends. The double
  validation costs nothing measurable.

## Success criteria

- When `--enable-paste-as-keystrokes` is passed:
  - The hamburger menu shows a "Paste" entry with
    "Ctrl+Alt+V" shortcut text.
  - Clicking "Paste" reads the host clipboard and
    types it as keystrokes.
  - Pressing `Ctrl+Alt+V` does the same.
  - The `V` keypress does not reach the guest when
    the shortcut fires.
  - When vdagent is connected, the menu entry is
    greyed out with a tooltip.
  - When the clipboard contains non-ASCII, an error
    dialog appears.
  - `PasteCompleted` shows as a transient status
    message.
  - `PasteFailed` shows as an error dialog.

- When `--enable-paste-as-keystrokes` is NOT passed:
  - No "Paste" entry in the menu.
  - `Ctrl+Alt+V` does nothing.

- `pre-commit run --all-files` passes.
- `cargo test --workspace` passes.

## Sub-agent brief

**Effort**: medium | **Model**: sonnet | **Isolation**: none

Implement Steps 1-14 of this plan. The plan provides
exact code snippets and file locations for every change.
Key files: `channels/mod.rs`, `channels/main_channel.rs`,
`channels/inputs.rs`, `app.rs`, `README.md`,
`ARCHITECTURE.md`, `AGENTS.md`. Read this plan thoroughly
before starting. Adapt the snippets to fit the actual code
structure you find when reading the files — line numbers
are approximate.

Critical points:
- The `Ctrl+Alt+V` shortcut must be detected *before*
  `self.handle_input(ctx)` to prevent the V key reaching
  the guest.
- `translate_paste`, `PasteError`, and `PasteKey` must be
  made `pub` in `inputs.rs` and re-exported from
  `channels/mod.rs`.
- The `AgentConnected` event must be emitted at all three
  sites in `main_channel.rs` where `agent_connected`
  changes.
- The error dialog follows the bug-report dialog's
  two-pass pattern.
- The paste menu entry is gated on `self.enable_paste`
  and disabled when `self.agent_connected` is true.
