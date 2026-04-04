# Phase 4: GUI report button and description dialog

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Add an interactive bug report trigger to the GUI: a "Report"
button in the status bar, a keyboard shortcut (F12), and a
modal dialog for selecting the report type, entering a
description, and generating the report.

At the end of this phase:

1. Pressing F12 or clicking "Report" in the status bar
   opens a centred dialog window.
2. The dialog contains a privacy warning, radio-button
   channel selector (Display, Input, Cursor, Connection),
   an optional description text field, and Capture/Cancel
   buttons.
3. Clicking "Capture" calls `generate_bug_report()` from
   Phase 3 and writes the zip file.
4. A transient status message ("Bug report saved to ...")
   appears in the status bar for 5 seconds after a
   successful report.
5. While the dialog is open, keyboard and mouse input is
   not forwarded to the SPICE server.
6. For Display reports, region selection is not yet
   implemented — the report is generated with
   `region: None`.  Phase 5 adds region selection.

## Design

### New state fields on RyllApp

```rust
// Bug report dialog state
show_bug_dialog: bool,
bug_report_type: BugReportType,
bug_description: String,
bug_status_message: Option<(String, Instant)>,
```

All initialised to defaults (`false`, `BugReportType::Display`,
empty string, `None`) in `RyllApp::new()`.

### F12 keyboard shortcut

F12 is currently mapped to scancode 0x58 in the inputs
channel and forwarded to the SPICE server.  Phase 4 must:

1. Intercept F12 in the `update()` method before
   `handle_input()` runs.
2. Toggle `show_bug_dialog` on F12 press.
3. In `handle_input()`, skip forwarding F12 to the server
   (so the remote guest never sees it).
4. When the dialog is open, suppress all keyboard
   forwarding so description text entry doesn't leak
   keystrokes to the server.

Implementation in `update()`, before `handle_input()`:

```rust
// F12 toggles bug report dialog
let f12_pressed = ctx.input(|i| {
    i.key_pressed(egui::Key::F12)
});
if f12_pressed {
    self.show_bug_dialog = !self.show_bug_dialog;
    if self.show_bug_dialog {
        // Reset dialog state on open
        self.bug_report_type = BugReportType::Display;
        self.bug_description.clear();
    }
}
```

In `handle_input()`, at the top:

```rust
// Don't forward input to the SPICE server when
// the bug report dialog is open.
if self.show_bug_dialog {
    return;
}
```

And inside the key event loop, skip F12:

```rust
if *key == egui::Key::F12 {
    continue;
}
```

### Mouse input suppression

When the dialog is open, mouse clicks and moves should not
be forwarded to the SPICE server.  The current mouse
handling code lives inside the surface rendering loop in
`update()`.  Wrap it with:

```rust
if !self.show_bug_dialog {
    // ... existing mouse position and button handling
}
```

This prevents mouse events from reaching the SPICE server
while the dialog is visible.

### "Report" button in the status bar

The status bar is rendered in a `TopBottomPanel::bottom`.
The right-aligned section currently contains the bandwidth
sparkline and label.  Add a "Report" button inside the
right-to-left layout, before the bandwidth display:

```rust
ui.with_layout(
    egui::Layout::right_to_left(egui::Align::Center),
    |ui| {
        // Bandwidth sparkline (existing)
        ui.label(self.bandwidth.label());
        // ... sparkline drawing ...

        // Bug report button
        ui.separator();
        if ui.small_button("Report").clicked() {
            self.show_bug_dialog = true;
            self.bug_report_type = BugReportType::Display;
            self.bug_description.clear();
        }

        // Status message (auto-dismiss after 5s)
        // ...
    },
);
```

Since the layout is right-to-left, the button will appear
to the left of the bandwidth display.

### Status message display

After a report is generated (success or failure), set:

```rust
self.bug_status_message = Some((message, Instant::now()));
```

In the status bar's right-to-left section, display the
message if it's less than 5 seconds old:

```rust
if let Some((ref msg, created)) = self.bug_status_message {
    if created.elapsed() < Duration::from_secs(5) {
        ui.separator();
        ui.label(msg);
    }
}
```

Clear expired messages at the start of `update()`:

```rust
if let Some((_, created)) = &self.bug_status_message {
    if created.elapsed() >= Duration::from_secs(5) {
        self.bug_status_message = None;
    }
}
```

### Bug report dialog window

The dialog is an `egui::Window` shown conditionally when
`self.show_bug_dialog` is true.  It is rendered after the
main display area and status bar but before the cursor
overlay.

```rust
if self.show_bug_dialog {
    egui::Window::new("Bug Report")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            self.draw_bug_dialog(ui);
        });
}
```

The `draw_bug_dialog()` method contains the dialog content:

1. **Privacy warning** (wrapped label):
   "Bug reports may contain sensitive data including screen
   contents, typed keystrokes, and protocol traffic. Review
   the report before sharing and ensure no confidential
   information is visible on screen or was recently typed."

2. **Channel selector** (radio buttons):
   ```rust
   ui.radio_value(
       &mut self.bug_report_type,
       BugReportType::Display,
       "Display (screenshot + image state)",
   );
   ui.radio_value(
       &mut self.bug_report_type,
       BugReportType::Input,
       "Input (keyboard + mouse state)",
   );
   ui.radio_value(
       &mut self.bug_report_type,
       BugReportType::Cursor,
       "Cursor (cursor cache + position)",
   );
   ui.radio_value(
       &mut self.bug_report_type,
       BugReportType::Connection,
       "Connection (session + main channel)",
   );
   ```

3. **Description** (single-line text edit):
   ```rust
   ui.label("Description (optional):");
   ui.text_edit_singleline(&mut self.bug_description);
   ```

4. **Buttons** (horizontal layout):
   ```rust
   ui.horizontal(|ui| {
       if ui.button("Capture").clicked() {
           // Generate the report
       }
       if ui.button("Cancel").clicked() {
           self.show_bug_dialog = false;
       }
   });
   ```

### Escape to close

Escape closes the dialog if it is open.  Check in
`update()` alongside the F12 handling:

```rust
if self.show_bug_dialog {
    let esc = ctx.input(|i| i.key_pressed(egui::Key::Escape));
    if esc {
        self.show_bug_dialog = false;
    }
}
```

### Capture button action

When "Capture" is clicked:

```rust
let result = self.generate_bug_report(
    self.bug_report_type,
    self.bug_description.clone(),
    None, // region selection is Phase 5
);
match result {
    Ok(path) => {
        let msg = format!(
            "Bug report saved to {}",
            path.display()
        );
        info!("app: {}", msg);
        self.bug_status_message =
            Some((msg, Instant::now()));
    }
    Err(e) => {
        let msg = format!("Bug report failed: {}", e);
        error!("app: {}", msg);
        self.bug_status_message =
            Some((msg, Instant::now()));
    }
}
self.show_bug_dialog = false;
```

### Borrowing considerations

The `draw_bug_dialog()` method and the capture action both
need `&mut self` access.  Since egui closures borrow the
UI, and the dialog window's closure captures `self`, the
dialog rendering and the capture action cannot be in the
same closure without careful structuring.

The cleanest approach is to use a two-pass pattern:

1. **Render pass**: draw the dialog and collect what action
   the user took (Capture, Cancel, or nothing) as a local
   variable.
2. **Action pass**: after the dialog window closure returns,
   execute the action on `self`.

```rust
let mut dialog_action = None;
if self.show_bug_dialog {
    egui::Window::new("Bug Report")
        .collapsible(false)
        .resizable(false)
        .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
        .show(ctx, |ui| {
            // ... draw dialog ...
            if ui.button("Capture").clicked() {
                dialog_action = Some(true);
            }
            if ui.button("Cancel").clicked() {
                dialog_action = Some(false);
            }
        });
}
match dialog_action {
    Some(true) => {
        // Generate report using self
        // ...
        self.show_bug_dialog = false;
    }
    Some(false) => {
        self.show_bug_dialog = false;
    }
    None => {}
}
```

This avoids borrow conflicts by separating the UI
rendering from the mutation of self.

**Note**: the radio_value and text_edit widgets need
`&mut self.bug_report_type` and `&mut self.bug_description`
inside the closure.  Since these are separate fields from
the ones used in `generate_bug_report()`, we can borrow
them independently if needed.  However, the simplest
approach is the two-pass pattern above, where the closure
only borrows the dialog-specific fields and the action is
executed after the closure returns.

## Steps

### Step 1: Add dialog state fields to RyllApp

1. Add `show_bug_dialog: bool`, `bug_report_type: BugReportType`,
   `bug_description: String`, and
   `bug_status_message: Option<(String, Instant)>` fields.
2. Initialise in `RyllApp::new()`.

### Step 2: Add F12 and Escape key handling

1. In `update()`, before `handle_input()`, check for F12
   press and toggle `show_bug_dialog`.
2. Check for Escape press when dialog is open and close it.
3. In `handle_input()`, return early if dialog is open.
4. In `handle_input()`, skip forwarding F12 events.

### Step 3: Suppress mouse forwarding when dialog is open

Wrap the mouse position and button handling code in
`update()` with `if !self.show_bug_dialog { ... }`.

### Step 4: Add "Report" button to status bar

Add a `small_button("Report")` in the right-to-left
section of the stats panel.  Clicking it opens the dialog
(same state reset as F12).

### Step 5: Add status message display

1. Clear expired messages (>5s) at the start of `update()`.
2. Display the message in the status bar's right-to-left
   section if present.

### Step 6: Implement the bug report dialog window

1. Add the `egui::Window` with the two-pass pattern.
2. Draw: privacy warning, radio buttons, text edit,
   Capture/Cancel buttons.
3. On Capture: call `generate_bug_report()`, set status
   message, close dialog.
4. On Cancel: close dialog.

### Step 7: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. `make test` — all tests pass.
4. Manual verification against a SPICE server (if
   available): press F12, select report type, click
   Capture, verify zip file is written.

### Step 8: Update documentation

1. Update `ARCHITECTURE.md` to describe the bug report
   dialog and F12 shortcut.
2. Update `AGENTS.md` to note the dialog state fields.
3. Update `README.md` to document the F12 shortcut and
   Report button.

## Administration and logistics

### Success criteria

* F12 opens/closes the bug report dialog.
* Escape closes the dialog.
* The "Report" button in the status bar opens the dialog.
* While the dialog is open, keyboard and mouse input are
  not forwarded to the SPICE server.
* Selecting a report type and clicking "Capture" writes
  a valid zip file (same as Phase 3 unit tests verify).
* A transient status message is shown for 5 seconds.
* The dialog resets its state each time it opens.
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.

### Risks

* **egui borrow checker**: The two-pass pattern (render
  then act) avoids the common egui pitfall of trying to
  mutate `self` inside a UI closure that already borrows
  it.  This pattern is well-established in egui codebases.

* **F12 interception**: F12 is currently forwarded to the
  SPICE server.  After this change, F12 is consumed by
  ryll and never reaches the guest.  This is intentional —
  F12 is rarely needed in guest VMs and the bug report
  shortcut is more useful.  If a user needs to send F12
  to the guest, they can use the on-screen keyboard in
  the guest.

* **Text input in description field**: When the dialog is
  open and the text edit has focus, egui handles the key
  events for text input.  Since `handle_input()` returns
  early when the dialog is open, no keystrokes are
  forwarded to the server.  This is correct.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
