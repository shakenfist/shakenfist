# Phase 5: UI panel

## Overview

Add a "Folders" panel to the egui interface matching the
look and feel of the existing USB panel. The panel provides
directory selection via a native file picker, sharing
status, connect/disconnect controls, and error display with
bug report integration.

Also add a "WebDAV" traffic filter checkbox to the traffic
viewer and a colour for the webdav channel.

## Files changed

| File | Change |
|------|--------|
| `src/app.rs` | Add state fields, status bar button, panel rendering, directory picker, traffic filter |

## Detailed steps

### Step 1: Add state fields to `RyllApp`

Add alongside the existing WebDAV fields (which already
include `webdav_tx`, `webdav_channel_ready`,
`webdav_shared_dir`, `webdav_read_only`,
`webdav_sharing_active`, `webdav_connected_at`,
`webdav_error_message`, `webdav_error_time`):

```rust
// Folders panel visibility
show_webdav_panel: bool,

// Directory picker state
webdav_pick_dir_rx:
    Option<std::sync::mpsc::Receiver<Option<PathBuf>>>,
webdav_pick_dir_readonly: bool,
webdav_pick_dir_message: Option<String>,

// Traffic viewer filter
traffic_filter_webdav: bool,
```

Initialise all to `false`/`None` in the constructor,
except `traffic_filter_webdav: true`.

### Step 2: Add status bar button

In the status bar section (around line 917-927), add a
"Folders" toggle button alongside the existing "Traffic",
"USB", and "Report" buttons:

```rust
if ui.small_button("Folders").clicked() {
    self.show_webdav_panel = !self.show_webdav_panel;
}
```

Place it after the "USB" button and before "Report".

### Step 3: Add traffic viewer filter

Add a `traffic_filter_webdav` checkbox alongside the
existing channel filter checkboxes (around line 962-968):

```rust
ui.checkbox(
    &mut self.traffic_filter_webdav,
    "WebDAV",
);
```

Add to the filter application match (around line 977-986):

```rust
"webdav" => self.traffic_filter_webdav,
```

Add a colour for the webdav channel in the colour mapping
(around line 997-1004):

```rust
"webdav" => egui::Color32::from_rgb(100, 200, 200),
```

Use a teal/cyan shade to distinguish from the existing
channel colours.

### Step 4: Render the Folders panel

Add the panel rendering block after the USB panel block
(which ends around line 1271). Follow the exact same
structure as the USB panel:

**Auto-clear and repaint logic** (before the panel `if`):

```rust
// Auto-clear WebDAV errors after 10 seconds
if let Some(error_time) = self.webdav_error_time {
    if error_time.elapsed() > Duration::from_secs(10) {
        self.webdav_error_message = None;
        self.webdav_error_time = None;
    }
}

// Request repaint for elapsed timer and error clear
if self.webdav_connected_at.is_some()
    || self.webdav_error_time.is_some()
{
    ctx.request_repaint_after(Duration::from_secs(1));
}
```

**Panel body:**

```rust
if self.show_webdav_panel {
    // Poll directory picker (same pattern as USB)
    // ...

    egui::SidePanel::right("webdav_panel")
        .default_width(300.0)
        .show(ctx, |ui| {
            // 1. Heading
            ui.heading("Shared Folders");
            ui.separator();

            // 2. Channel status
            if self.webdav_channel_ready {
                ui.label("Channel: Ready");
            } else {
                ui.colored_label(
                    egui::Color32::GRAY,
                    "Channel: Not available",
                );
            }

            // 3. Active share display with elapsed timer
            if self.webdav_sharing_active {
                if let Some(ref dir) = self.webdav_shared_dir {
                    ui.separator();
                    let elapsed = self
                        .webdav_connected_at
                        .map(|t| t.elapsed().as_secs())
                        .unwrap_or(0);
                    let mins = elapsed / 60;
                    let secs = elapsed % 60;
                    let ro = if self.webdav_read_only {
                        " [RO]"
                    } else {
                        ""
                    };
                    ui.label(format!(
                        "Sharing: {}{} ({}m {}s)",
                        dir, ro, mins, secs
                    ));
                    if ui.button("Stop Sharing").clicked() {
                        webdav_action =
                            Some(WebdavCommand::StopSharing);
                    }
                }
            }

            // 4. Error display
            if self.webdav_error_message.is_some() {
                ui.separator();
                ui.colored_label(
                    egui::Color32::RED,
                    self.webdav_error_message
                        .as_ref().unwrap(),
                );
                ui.horizontal(|ui| {
                    if ui.small_button("Dismiss").clicked()
                    {
                        self.webdav_error_message = None;
                        self.webdav_error_time = None;
                    }
                });
            }

            // 5. Share controls (when not sharing)
            if !self.webdav_sharing_active {
                ui.separator();
                ui.horizontal(|ui| {
                    ui.checkbox(
                        &mut self.webdav_pick_dir_readonly,
                        "Read-only",
                    );
                    let picker_active =
                        self.webdav_pick_dir_rx.is_some();
                    let enabled =
                        self.webdav_channel_ready
                        && !picker_active;
                    if ui
                        .add_enabled(
                            enabled,
                            egui::Button::new(
                                "Share Directory...",
                            ),
                        )
                        .clicked()
                    {
                        // Spawn directory picker on
                        // background thread
                    }
                });
            }

            // 6. Picker status message
            if let Some(ref msg) =
                self.webdav_pick_dir_message
            {
                ui.label(msg);
            }
        });
}
```

### Step 5: Directory picker

Follow the USB file picker pattern exactly, but use
`pick_folder()` instead of `pick_file()`:

**Spawning:**

```rust
let (tx, rx) = std::sync::mpsc::channel();
std::thread::spawn(move || {
    let result = rfd::FileDialog::new()
        .set_title("Select directory to share")
        .pick_folder();
    let _ = tx.send(result);
});
self.webdav_pick_dir_rx = Some(rx);
```

**Polling** (before the panel `if`):

```rust
let mut picked_dir = None;
if let Some(ref rx) = self.webdav_pick_dir_rx {
    if let Ok(result) = rx.try_recv() {
        picked_dir = Some(result);
    }
}
if picked_dir.is_some() {
    self.webdav_pick_dir_rx = None;
}
if let Some(Some(path)) = picked_dir {
    // Validate it's a directory (it should be from
    // pick_folder, but verify)
    if path.is_dir() {
        webdav_action =
            Some(WebdavCommand::ShareDirectory {
                path,
                read_only: self.webdav_pick_dir_readonly,
            });
    } else {
        self.webdav_pick_dir_message =
            Some(format!("Not a directory: {}",
                path.display()));
    }
}
```

### Step 6: Execute WebDAV action

After the panel closure, execute the pending action (same
pattern as the USB panel's `usb_action`):

```rust
if let Some(cmd) = webdav_action {
    self.webdav_error_message = None;
    self.webdav_error_time = None;
    if let Some(ref tx) = self.webdav_tx {
        if let Err(e) = tx.try_send(cmd) {
            self.webdav_error_message =
                Some(format!(
                    "Failed to send command: {}", e
                ));
            self.webdav_error_time =
                Some(Instant::now());
        }
    }
}
```

## Testing

- `make test` passes (all existing tests unbroken).
- `cargo fmt --check` and `cargo clippy -- -D warnings`
  pass.
- The "Folders" button appears in the status bar.
- Clicking it opens a right-side panel showing channel
  status.
- The directory picker opens a native folder selection
  dialog.
- After selecting a directory, the panel shows sharing
  status with an elapsed timer.
- "Stop Sharing" stops sharing and returns to the picker
  state.
- The traffic viewer shows a "WebDAV" filter checkbox and
  webdav traffic entries in teal.
- Error messages appear in red and auto-clear after
  10 seconds.

## Back brief

Before executing, please confirm your understanding of:
1. The panel mirrors the USB panel's structure exactly:
   heading, channel status, active share display with
   elapsed timer, error display, and action controls.
2. The directory picker uses `rfd::FileDialog::pick_folder`
   spawned on a background thread with `std::sync::mpsc`
   polling, matching the USB file picker pattern.
3. The `webdav_action` variable is used to defer command
   sending until after the panel closure to avoid borrow
   conflicts with `self`.
4. Traffic viewer changes are minimal: one filter checkbox,
   one match arm, one colour entry.
