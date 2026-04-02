# Phase 2: Render cursor as egui overlay

## Overview

Draw the server-provided cursor image on screen as an egui
overlay at the position reported by the server, and hide
the native OS cursor when hovering over the SPICE surface.

## Current state after phase 1

- `CursorShape(CursorImage)` events arrive in `app.rs`
  with RGBA pixel data, dimensions, and hot_spot offsets.
- `CursorPosition { x, y, visible }` events update
  `self.cursor_pos` and `self.cursor_visible`.
- `MouseMode(u32)` events update `self.mouse_mode`.
- The app logs cursor shape events but does not store or
  render them.

## Design

### Where to render

Use `egui::Area` with `fixed_pos` to draw the cursor at
absolute screen coordinates, after the CentralPanel. This
avoids interfering with the surface layout and allows the
cursor to float freely.

### Coordinate mapping

In server mouse mode (mode 1), the server controls the
cursor position via `CursorPosition` events. The cursor
image should be drawn at:

```
screen_x = surface_rect.min.x + cursor_pos.x - hot_spot_x
screen_y = surface_rect.min.y + cursor_pos.y - hot_spot_y
```

In client mouse mode (mode 2), the cursor position tracks
the local mouse. The server may still send a custom shape.
Draw at the local mouse position minus the hot_spot.

### Texture management

Create the cursor `TextureHandle` once when a `CursorShape`
arrives, using `ctx.load_texture()`. Update it with
`texture.set()` when the shape changes. Use
`TextureFilter::Nearest` for crisp pixel edges at small
cursor sizes.

### OS cursor hiding

When the mouse hovers over the SPICE surface, set
`ctx.output_mut(|o| o.cursor_icon = CursorIcon::None)` to
hide the native cursor. Restore the default cursor when
the mouse leaves the surface.

## Implementation steps

### Step 1: Store cursor shape in RyllApp

Add fields to `RyllApp`:

```rust
cursor_image: Option<CursorImage>,
cursor_texture: Option<TextureHandle>,
surface_rect: egui::Rect,  // bounding rect of the rendered surface
```

In the `CursorShape` handler, store the image and mark the
texture as needing update (set `cursor_texture = None` so
it gets recreated on next frame).

### Step 2: Track the surface rect

In the CentralPanel render loop, after calling `ui.add()`
for the surface image, save `response.rect` into
`self.surface_rect`. This gives us the screen-space bounds
of the SPICE surface for coordinate mapping.

### Step 3: Create/update cursor texture

In `update()`, after `process_events()`, check whether
`cursor_image` is Some and `cursor_texture` is None. If
so, create the texture from `CursorImage.pixels` using
`ctx.load_texture()` with `TextureFilter::Nearest`.

When `CursorShape` arrives and replaces `cursor_image`,
set `cursor_texture = None` to force recreation.

### Step 4: Draw cursor overlay

After the CentralPanel and stats panel, if the cursor is
visible and we have a texture:

```rust
if self.cursor_visible {
    if let Some(ref tex) = self.cursor_texture {
        if let Some(ref img) = self.cursor_image {
            let x = self.surface_rect.min.x
                + self.cursor_pos.0 as f32
                - img.hot_spot_x as f32;
            let y = self.surface_rect.min.y
                + self.cursor_pos.1 as f32
                - img.hot_spot_y as f32;

            egui::Area::new(egui::Id::new("spice_cursor"))
                .fixed_pos(egui::pos2(x, y))
                .interactable(false)
                .order(egui::Order::Foreground)
                .show(ctx, |ui| {
                    let size = egui::vec2(
                        img.width as f32,
                        img.height as f32,
                    );
                    ui.add(
                        egui::Image::new(tex)
                            .fit_to_exact_size(size),
                    );
                });
        }
    }
}
```

Key properties:
- `interactable(false)` — clicks pass through to the
  surface below.
- `Order::Foreground` — renders on top of everything.
- `fixed_pos` — absolute positioning.

### Step 5: Hide OS cursor over the surface

In the CentralPanel, when `response.hovered()` is true,
hide the OS cursor:

```rust
if response.hovered() {
    ctx.output_mut(|o| o.cursor_icon = egui::CursorIcon::None);
}
```

This only hides the cursor when hovering over the SPICE
surface — the normal cursor appears over the stats bar or
outside the window.

### Step 6: Handle cursor visibility

- `CursorPosition { visible: false }` or `HIDE` message:
  don't draw the overlay.
- `CursorPosition { visible: true }`: draw the overlay.
- No `CursorShape` received yet: don't draw (no image).

### Step 7: Client mouse mode

In client mode (mode 2), the cursor position should
follow the local mouse rather than the server's position.
Use `self.last_mouse_pos` instead of `self.cursor_pos`
when `self.mouse_mode == 2`.

## Files to modify

| File | Changes |
|------|---------|
| `src/app.rs` | Store cursor image/texture, render overlay, hide OS cursor |

`channels/cursor.rs` and `channels/mod.rs` should not
need changes — phase 1 already provides everything needed.

## Success criteria

- A cursor image appears on screen when connected to a
  real SPICE server in server mouse mode.
- The cursor tracks the server-reported position.
- The cursor shape updates when the server sends SET.
- The native OS cursor is hidden over the SPICE surface.
- Hot_spot offsets are applied correctly (the click point
  aligns with the pointed-to location).
- No visible flicker when the cursor shape changes.
- `pre-commit run --all-files` passes.
- The UEFI latency guest (`make test-qemu`) still works
  correctly (it uses LZ, not cursor overlays, but should
  not regress).
