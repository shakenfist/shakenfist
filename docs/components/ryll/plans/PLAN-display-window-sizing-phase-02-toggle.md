# Phase 2 — hamburger toggle and CLI flag

Parent: [PLAN-display-window-sizing.md](/components/ryll/plans/PLAN-display-window-sizing/).

## Goal

Give the operator an opt-out for the always-fit behaviour
landed in phase 1. The default stays "obey guest size hints"
(the bug-fix default), but two new affordances let the user
turn it off:

- A `Obey guest size hints` checkbox in the hamburger menu,
  next to the existing view toggles.
- A `--no-obey-guest-size` CLI flag for users (and CI) who
  want ryll to start with the toggle already off.

When the toggle is off, ryll behaves like the post-initial
phase 0 code did: the guest can change resolution all it
likes, ryll's window stays where the user put it, and the
surface renders at native size inside that window
(overflowing or letterboxed in empty space, same as today).

## Scope

In scope:

- `obey_guest_size: bool` field on `RyllApp`.
- `--no-obey-guest-size` flag added to `Args` in
  `ryll/src/config.rs`.
- A new `obey_guest_size: bool` parameter on
  `RyllApp::new` and on `app::run_headless`. Threaded from
  `main.rs` via `!args.no_obey_guest_size`.
- An `obey: bool` parameter on `compute_auto_resize`,
  consumed before the maximised/dedup checks. The function
  returns `None` whenever `!obey`.
- A `ui.checkbox(&mut self.obey_guest_size,
  "Obey guest size hints")` entry in the hamburger menu at
  `ryll/src/app.rs:1927-1963`.
- One extra case in the
  `compute_auto_resize_decisions` unit test covering the
  `obey = false` short-circuit.

Out of scope (later phases):

- Persisting the toggle across sessions. There is no
  on-disk settings layer today
  (`ryll/src/settings.rs` only holds CLI flag mirrors); a
  separate plan adds it.
- A "Fit window now" menu item for users who toggled obey
  off and want to one-shot fit. Future work.
- Broader state-machine tests across the toggle (phase 3).
- Doc updates (phase 4).
- Notification on resolution change (phase 5).

## Grounding — what's there today

After phase 1, the resize state lives in three places:

- `pending_resize: Option<(f32, f32)>` and
  `last_auto_resize: Option<(u32, u32)>` on `RyllApp`
  ([app.rs:241-251](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L241-L251)).
- The `compute_auto_resize(pending, last_auto, is_max)`
  free function
  ([app.rs:3036-3068](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L3036-L3068)).
- The call site in `RyllApp::update`
  ([app.rs:1674-1693](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1674-L1693)).

The hamburger menu lives at
[app.rs:1927-1963](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1927-L1963) and
already contains three view toggles via plain
`ui.checkbox`. Adding a fourth is a one-line change.

`Args` in
[ryll/src/config.rs:10-92](https://github.com/shakenfist/ryll/blob/develop/ryll/src/config.rs#L10-L92)
uses the standard clap-derive style. Boolean flags follow
the pattern `#[arg(long, default_value_t = false)] pub
foo: bool` (see `headless`, `cadence`, `pedantic`,
`enable_paste_as_keystrokes`). The opt-out pattern
`--no-foo` is just a boolean named `no_foo` that is then
inverted at the call site.

`RyllApp::new` is called from `run_gui` in
[ryll/src/main.rs:217-234](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs#L217-L234)
with positional arguments. `app::run_headless` is called
from `run_headless` in
[ryll/src/main.rs:178-194](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs#L178-L194).

`run_headless` does not own a `RyllApp` — it has its own
parallel "headless app" code path in `ryll/src/app.rs`. We
pass `obey_guest_size` to it for symmetry and so the CLI
flag can be unconditionally accepted, but headless has no
window to resize, so the value is recorded and ignored.
Concretely: store it on the headless-side state struct (if
one exists) or accept-and-drop in the function signature.
The phase plan brief asks the implementing agent to pick
the cleanest of the two after reading
[`app::run_headless`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs).

## Design

### State

Add to `RyllApp`, near `pending_resize`:

```rust
// User-controlled opt-out of the always-fit behaviour.
// True (default) => auto-fit the window to every primary
// SurfaceCreated. False => leave the window alone; the
// surface renders at native pixel size inside whatever
// window the user has chosen (may overflow or letterbox).
// Toggled live via the hamburger menu; initial value comes
// from `--no-obey-guest-size` (inverted).
obey_guest_size: bool,
```

Initialise from the new constructor parameter in
`RyllApp::new`. Do **not** reset in
`RyllApp::reconnect` — this is a session-level user
preference, not a connection-level one, so it survives a
reconnect.

### CLI flag

Append to `Args` in `ryll/src/config.rs`, under the
existing display-related flags (e.g. after `--monitors`):

```rust
/// Start with "obey guest size hints" turned off so the
/// window does not auto-fit when the guest changes
/// resolution. Equivalent to opening the hamburger menu
/// and unchecking the checkbox after launch.
#[arg(long, default_value_t = false)]
pub no_obey_guest_size: bool,
```

The default is `false` (i.e. the flag is absent), which
means `obey_guest_size = true` at startup — matching the
default behaviour landed in phase 1.

Compute the initial value in `main.rs` next to the other
CLI-derived flags (`enable_paste`, `cadence`, etc.) and
thread it through `run_gui` / `run_headless`:

```rust
let obey_guest_size = !args.no_obey_guest_size;
```

### Helper signature

Extend `compute_auto_resize`:

```rust
fn compute_auto_resize(
    pending: Option<(f32, f32)>,
    last_auto: Option<(u32, u32)>,
    is_max: bool,
    obey: bool,
) -> Option<(f32, f32, u32, u32)> {
    let (w, h) = pending?;
    if !obey || is_max {
        return None;
    }
    let aligned_w = ((w as u32).max(8) / 8) * 8;
    let aligned_h = ((h as u32).max(8) / 8) * 8;
    if last_auto == Some((aligned_w, aligned_h)) {
        return None;
    }
    Some((w, h, aligned_w, aligned_h))
}
```

`!obey` short-circuits ahead of the alignment / dedup work
because the value is the simplest gate and consuming
`pending` (via `?`) is the only side effect we want to
preserve when `obey` is false. Consuming `pending` matters:
the caller does `self.pending_resize.take()` before
calling, so a stale value gets dropped whether obey is on
or off — preventing a toggle-on later from picking up a
five-minutes-ago surface size.

Update the doc comment to mention the new parameter.

### Call site

In `RyllApp::update`, change the call to:

```rust
if let Some((w, h, aw, ah)) = compute_auto_resize(
    pending,
    self.last_auto_resize,
    is_max,
    self.obey_guest_size,
) {
    // …unchanged…
}
```

No other logic changes here.

### Hamburger menu

Inside the `egui::menu::menu_button(ui, "☰", |ui| { … })`
block at
[app.rs:1927-1963](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1927-L1963), add
the checkbox **above** the three existing view toggles so
the display-related entry is visually grouped near the
top:

```rust
egui::menu::menu_button(ui, "☰", |ui| {
    ui.checkbox(
        &mut self.obey_guest_size,
        "Obey guest size hints",
    );
    ui.separator();
    ui.checkbox(&mut self.show_traffic_viewer, "Traffic");
    ui.checkbox(&mut self.show_usb_panel, "USB");
    ui.checkbox(&mut self.show_webdav_panel, "Folders");
    // …rest unchanged…
});
```

The separator is so the new display toggle reads as its
own group rather than an unlabelled fourth view toggle. No
other reordering. The existing `if self.enable_paste {
ui.separator(); … }` paste block stays in place.

### Headless

`app::run_headless` accepts an `obey_guest_size: bool`
parameter for CLI symmetry. Headless mode has no GUI
window, so the flag does nothing. Two acceptable
implementations — the agent picks the cleaner one after
reading the function:

1. Add `obey_guest_size: bool` to the signature, prefix
   the binding with `_obey_guest_size` so clippy is happy,
   add a one-line comment that headless ignores it.
2. If headless has a state struct similar to `RyllApp`,
   carry the field on it for parity even though nothing
   reads it.

Either is fine; option 1 is the minimum-diff path.

### Tests

Extend `compute_auto_resize_decisions` (the existing test
in `ryll/src/app.rs`) by:

1. Adding the `obey: bool` argument to every existing
   call. Pass `true` (matches phase 1 behaviour) for all
   existing cases.
2. Adding two new cases at the end:

```rust
// obey = false short-circuits even when the target
// differs and the window is not maximised.
assert_eq!(
    compute_auto_resize(
        Some((1024.0, 768.0)),
        None,
        false,
        false,
    ),
    None,
);

// obey = false short-circuits even when the target equals
// last_auto (would dedup anyway, but we want the obey
// gate to be the reason).
assert_eq!(
    compute_auto_resize(
        Some((1024.0, 768.0)),
        Some((1024, 768)),
        false,
        false,
    ),
    None,
);
```

End-to-end "click toggle off, drive a SurfaceCreated, see
no resize" tests are phase 3.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 2a   | medium | sonnet | none      | Implement everything in this phase as described in *Design* and *Tests*. Files touched: `ryll/src/app.rs`, `ryll/src/config.rs`, `ryll/src/main.rs`. Concretely: (1) add `obey_guest_size: bool` field to `RyllApp` near `pending_resize`, with the docstring from the Design section; (2) add an `obey_guest_size: bool` parameter to `RyllApp::new`, initialise the field from it; do **not** touch `RyllApp::reconnect`; (3) add `obey_guest_size: bool` (or `_obey_guest_size: bool`) to `app::run_headless` — pick whichever yields the cleaner diff after reading the function — and propagate from `main.rs`; (4) add `obey: bool` to `compute_auto_resize`, with the body change shown in the Design section, and update the doc comment; (5) update the call site in `RyllApp::update` to pass `self.obey_guest_size`; (6) add the `--no-obey-guest-size` flag to `Args` in `config.rs` with the docstring from the Design section; (7) compute `let obey_guest_size = !args.no_obey_guest_size;` in `main.rs` and pass it into both `run_gui`/`RyllApp::new` and `run_headless`/`app::run_headless`; (8) add the `ui.checkbox(&mut self.obey_guest_size, "Obey guest size hints")` plus separator at the top of the hamburger menu block; (9) update `compute_auto_resize_decisions` to pass the new `obey` argument in every case (existing cases use `true`) and add the two new `obey = false` cases. Run `pre-commit run --all-files` and `make test`. Single commit titled `Add "Obey guest size hints" toggle and CLI flag.`. Do not commit until the management session has reviewed. |

Medium effort and sonnet because the change is mechanical
across three files and the brief gives every diff explicitly.
Opus would not add value. No worktree isolation: the diff is
small and easy to revert.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, including the two new
  `compute_auto_resize_decisions` cases.
* `ryll --help` lists `--no-obey-guest-size`.
* Launching ryll with no flags shows "Obey guest size
  hints" checked in the hamburger menu, and the window
  auto-fits to guest surface changes.
* Launching ryll with `--no-obey-guest-size` shows the
  checkbox unchecked, and the window stays the size the
  user puts it at regardless of guest mode changes.
* Toggling the checkbox during a session immediately takes
  effect on the next `SurfaceCreated` (no reconnect
  needed).

## Review checklist

- [ ] `obey_guest_size: bool` field added with docstring;
      initialised in `RyllApp::new`; **not** touched in
      `reconnect`.
- [ ] `--no-obey-guest-size` flag in `Args`, default off.
- [ ] `main.rs` computes `let obey_guest_size =
      !args.no_obey_guest_size;` once and threads to both
      GUI and headless paths.
- [ ] `compute_auto_resize` takes `obey: bool` and
      short-circuits to `None` when `!obey`.
- [ ] Call site in `update` passes `self.obey_guest_size`.
- [ ] Hamburger menu has the checkbox at the top, with a
      separator between it and the view toggles.
- [ ] Test cases for `obey = false` exist; existing cases
      pass `true`.
- [ ] No changes to `reconnect`, `maybe_send_monitors_resize`,
      or anywhere else outside the briefed scope.

## Follow-up

Phase 3 adds tests that drive `RyllApp` with synthesised
channel events to cover the toggle interacting with the
full resize pipeline (including `last_sent_resize` round-trip
through `maybe_send_monitors_resize`). Phase 4 documents
the toggle in `README.md` and `ARCHITECTURE.md`. Phase 5
adds resolution-change notifications independently of this
toggle.
