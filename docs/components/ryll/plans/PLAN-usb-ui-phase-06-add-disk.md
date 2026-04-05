# Phase 6: Add virtual disk at runtime

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Allow users to add a new RAW disk image as a virtual USB
device from within the USB panel without restarting ryll.
Uses a native file picker dialog via the `rfd` crate.

After this phase:

- An "Add Disk..." button in the USB panel opens a native
  file picker.
- A "Read-only" checkbox controls whether the image is
  presented as read-only.
- The selected file is validated and added to the virtual
  disk list for the session.
- The device list re-enumerates to show the new device.
- The user can then connect it via the existing Connect
  button.

## Background

### rfd crate

`rfd` (Rusty File Dialog) v0.17 provides native file
dialogs on Linux (xdg-portal/D-Bus), macOS, and Windows.
The default `xdg-portal` backend requires no C library
build dependencies — it uses D-Bus at runtime. This is
ideal since the build container has no GTK3 dev headers.

**Key API:**

- `rfd::FileDialog::new()` — blocking, returns
  `Option<PathBuf>` from `.pick_file()`.
- `rfd::AsyncFileDialog::new()` — async, returns
  `Option<FileHandle>`.
- `.add_filter("name", &["ext1", "ext2"])` — file type
  filter.
- `.set_title("title")` — dialog title.

Since the egui render loop is synchronous, the blocking
`FileDialog` must run on a background thread. The pattern
is:

1. User clicks "Add Disk..." button.
2. Spawn `std::thread::spawn` with a `FileDialog`.
3. Send the result back via `std::sync::mpsc::channel`.
4. Each frame, `try_recv()` on the receiver to check
   for a result.

This matches the rfd + egui best practice from community
discussions and avoids freezing the UI.

### Runtime environment

The xdg-portal file dialog requires a desktop portal
service at runtime (e.g. `xdg-desktop-portal-gtk`). In
headless containers or minimal environments, the dialog
may fail to open. This is acceptable:

- The USB panel is a GUI-only feature.
- Headless users have `--usb-disk` CLI flags.
- If the dialog fails, the user sees no picker — they
  can still use CLI flags to add disks before launch.

### Validation

The same validation from `main.rs` / `config.rs` applies:

- Path must exist and be a regular file.
- File size must be >= 512 bytes.
- If size is not a multiple of 512 bytes, warn but allow.

## Detailed steps

### Step 1: Add rfd dependency

In `Cargo.toml`, add:

```toml
# Native file dialog for USB disk image selection
rfd = "0.15"
```

Use the default features (xdg-portal on Linux). No
`gtk3` feature needed. Use 0.15 rather than 0.17 since
we need a version that is available in the cargo
registry (the exact version will be resolved by cargo).

Verify it builds in the devcontainer by running
`make build`.

### Step 2: Add file picker state to RyllApp

Add fields to `RyllApp`:

```rust
// File picker for adding virtual disks
usb_add_disk_rx: Option<
    std::sync::mpsc::Receiver<Option<std::path::PathBuf>>,
>,
usb_add_disk_readonly: bool,
usb_add_disk_message: Option<String>,
```

- `usb_add_disk_rx` — receiver for the background thread
  result. `Some` while a dialog is open, `None` otherwise.
- `usb_add_disk_readonly` — checkbox state, persists
  between clicks.
- `usb_add_disk_message` — validation warning or info
  message after adding a disk (e.g. "Added: test.raw" or
  "Warning: file size not a multiple of 512").

Initialise in `RyllApp::new()`:

```rust
usb_add_disk_rx: None,
usb_add_disk_readonly: false,
usb_add_disk_message: None,
```

### Step 3: Add "Add Disk..." UI to the panel

After the ScrollArea (device list) in the USB panel, add
a section for adding virtual disks:

```rust
ui.separator();
ui.horizontal(|ui| {
    ui.checkbox(
        &mut self.usb_add_disk_readonly,
        "Read-only",
    );
    let picker_active =
        self.usb_add_disk_rx.is_some();
    if ui
        .add_enabled(
            !picker_active,
            egui::Button::new("Add Disk..."),
        )
        .clicked()
    {
        // Spawn file picker on background thread
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let result = rfd::FileDialog::new()
                .set_title("Select RAW disk image")
                .add_filter(
                    "Disk images",
                    &["raw", "img"],
                )
                .add_filter("All files", &["*"])
                .pick_file();
            let _ = tx.send(result);
        });
        self.usb_add_disk_rx = Some(rx);
    }
});
```

The button is disabled while a picker dialog is already
open (prevents multiple dialogs).

### Step 4: Poll for file picker result

At the top of the USB panel block (before the
`SidePanel::right` call), poll the receiver:

```rust
// Check for file picker result
if let Some(ref rx) = self.usb_add_disk_rx {
    if let Ok(result) = rx.try_recv() {
        self.usb_add_disk_rx = None;  // dialog closed
        if let Some(path) = result {
            // Validate and add the disk
            ...
        }
    }
}
```

Since `self.usb_add_disk_rx` is `Option<Receiver>` and
we need to set it to `None` after receiving, use a
two-step approach:

```rust
let mut picked_path = None;
if let Some(ref rx) = self.usb_add_disk_rx {
    if let Ok(result) = rx.try_recv() {
        picked_path = Some(result);
    }
}
if picked_path.is_some() {
    self.usb_add_disk_rx = None;
}
if let Some(Some(path)) = picked_path {
    // Validate and add
    ...
}
```

### Step 5: Validate the selected file

When a file is picked, validate it:

```rust
if let Some(Some(path)) = picked_path {
    self.usb_add_disk_message = None;
    match std::fs::metadata(&path) {
        Ok(meta) => {
            if !meta.is_file() {
                self.usb_add_disk_message = Some(
                    "Selected path is not a regular file."
                        .to_string(),
                );
            } else if meta.len() < 512 {
                self.usb_add_disk_message = Some(
                    "File is too small (< 512 bytes)."
                        .to_string(),
                );
            } else {
                // Add to virtual disks
                let read_only = self.usb_add_disk_readonly;
                self.usb_virtual_disks.push(
                    (path.clone(), read_only),
                );
                // Re-enumerate
                self.usb_available_devices =
                    usb::enumerate_devices(
                        &self.usb_virtual_disks,
                    );
                // Status message
                let warn = if meta.len() % 512 != 0 {
                    " (warning: size not a multiple of 512)"
                } else {
                    ""
                };
                let ro = if read_only { " [RO]" }
                    else { "" };
                self.usb_add_disk_message = Some(
                    format!(
                        "Added: {}{}{}",
                        path.display(),
                        ro,
                        warn,
                    ),
                );
            }
        }
        Err(e) => {
            self.usb_add_disk_message = Some(
                format!("Cannot read file: {}", e),
            );
        }
    }
}
```

### Step 6: Display add-disk messages

In the panel, after the "Add Disk..." button row, show
the message:

```rust
if let Some(ref msg) = self.usb_add_disk_message {
    if msg.starts_with("Added:") {
        ui.label(msg);
    } else {
        ui.colored_label(egui::Color32::RED, msg);
    }
}
```

Errors show in red, success messages in default colour.
The message persists until the next file picker result
replaces it.

## Files changed

| File | Change |
|------|--------|
| `Cargo.toml` | Add `rfd` dependency |
| `src/app.rs` | Add file picker state fields; add "Add Disk..." button and read-only checkbox; spawn file dialog on background thread; poll for result each frame; validate and add to virtual disks; display messages |

## What is NOT in scope

- Persisting added virtual disks across sessions.
- Removing virtual disks from the list (user can restart).
- Drag-and-drop file support.
- File picker for non-RAW formats (only RAW is supported
  by the virtual mass storage backend).

## Testing

- `make build` — compiles with new `rfd` dependency.
- `make test` — all existing tests pass.
- `pre-commit run --all-files` — clean.
- Manual: open USB panel, click "Add Disk...", verify
  native file picker opens. Select a RAW image, verify
  it appears in the device list. Try selecting a tiny
  file (< 512 bytes) — verify error message. Check the
  read-only checkbox, add another disk — verify [RO]
  label. Connect the added disk via the Connect button.
- If running in a container without a portal service,
  the dialog may not open — this is expected and should
  not crash.

## Back brief

This phase adds an "Add Disk..." button that spawns a
native file picker dialog on a background thread via the
`rfd` crate. The result is polled each frame via
`try_recv()`. The selected file is validated (exists,
regular file, >= 512 bytes) and added to the session's
virtual disk list for re-enumeration. A read-only
checkbox controls the disk mode. Success and error
messages appear inline in the panel.
