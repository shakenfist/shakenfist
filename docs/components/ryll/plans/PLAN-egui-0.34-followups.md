# egui / eframe 0.34 follow-up cleanup

## Status: COMPLETED (eframe 0.35 bump, PR #131)

The deprecated-API cleanup this plan describes was carried out as
part of the eframe 0.34 → 0.35 upgrade, because 0.35 *removed* the
APIs that 0.34 only deprecated. Done in `ryll/src/app.rs`:

- `TopBottomPanel`/`SidePanel` → unified `Panel` (`Panel::top` /
  `::bottom` / `::left` / `::right`); `default_width` → `default_size`.
- Every panel `show(ctx, …)` → `show(ui, …)`, fed by the root `&mut Ui`
  from `App::ui`; `ctx` is now a borrow of a cloned (Arc-backed)
  `Context` so the remaining `Window::show(ctx, …)` and `ctx.method()`
  sites are unchanged. `Window`s still take `&Context`.
- `show_animated(ctx, bool, …)` → `show_collapsible(ui, &mut bool, …)`.
- `Frame::none()` → `Frame::NONE`; `ctx.style()` →
  `ctx.style_of(ctx.theme())`; `InputState::screen_rect()` →
  `viewport_rect()`; `Context::wants_pointer_input()` →
  `egui_wants_pointer_input()`; `menu::menu_button(ui, …)` →
  `ui.menu_button(…)`; `Ui::close_menu()` → `Ui::close()`.
- The module-wide `#![allow(deprecated)]` shim was removed; clippy
  passes under `-D warnings` with no remaining deprecated usage.

The historical analysis below is retained for context.

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (egui rendering,
panel layout, input handling), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (egui 0.34 API surface,
`Context` vs `Ui` semantics, viewport vs content rect),
research as needed against the upstream changelog and docs.
Flag any uncertainty explicitly rather than guessing.

Consult `AGENTS.md` for build commands, project conventions,
code organisation, and a table of protocol reference sources.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should build, pass tests, and
have a clear commit message explaining what changed and why.

## Situation

The renovate bump to eframe 0.34 (PR #61) was landed with
the minimum diff required to make the workspace compile
and pass clippy under the project's `-D warnings` policy.
That minimum diff consisted of:

1. Renaming `RyllApp::update` to `RyllApp::ui`, taking the
   provided `Ui` and immediately pulling the `Context` out
   of it with `let ctx = ui.ctx();`. The original body
   continues to drive `TopBottomPanel` / `SidePanel` /
   `CentralPanel` against that `Context`, so the supplied
   `Ui` is intentionally unused at the top level.
2. Casting `Margin::symmetric(4.0, 2.0)` to `Margin::symmetric(4, 2)`.
3. Passing `egui::StrokeKind::Middle` as the fourth argument
   to `Painter::rect_stroke` (preserves pre-0.34 behaviour
   where the stroke was centred on the rect edge).
4. Replacing the `LicenseRef-UFL-1.0` cargo-deny exception
   for `epaint_default_fonts` with `Ubuntu-font-1.0`, which
   is the SPDX identifier the crate now emits.
5. Adding `#![allow(deprecated)]` at the top of
   `ryll/src/app.rs` so the 24 deprecation warnings the
   bump introduces do not trip clippy's `-D warnings`.

That last point is the debt this plan addresses. The
module-wide `#![allow(deprecated)]` also masks any future,
unrelated deprecations that show up in `app.rs`, which is
the largest file in the crate. We want it gone.

## Mission and problem statement

Migrate `ryll/src/app.rs` (and any other call sites that
surface during the work) off the deprecated egui 0.34 APIs
listed below, then remove the module-wide
`#![allow(deprecated)]` so the next deprecation in this
file is once again loud at lint time.

## Scope

The deprecated APIs the eframe 0.34 bump flagged in
`ryll/src/app.rs`, grouped by area:

### App trait shape
- `eframe::App::update(&mut self, ctx, frame)` is deprecated
  in favour of `ui(&mut self, ui, frame)` (currently
  satisfied by the trivial wrapper that extracts `ctx` from
  the supplied `Ui`). Decide whether the app should
  restructure to use the provided `Ui` directly (drop the
  outer panels and put widgets straight on the root `Ui`)
  or keep the panel-driven layout and only update the
  signature. The current layout assumes panels, so the
  minimum-correct migration is "keep the wrapper, just
  remove `#[allow(deprecated)]` on the `update` shim if we
  reintroduce one".

### Panels
- `egui::TopBottomPanel`, `egui::SidePanel`, `egui::CentralPanel`
  are now type aliases marked deprecated; the canonical names
  live under `egui::containers::panel`. The aliases still
  resolve, so this is purely a path/import rename.
- `Panel::show`, `Panel::show_animated`, `Panel::default_width`
  are deprecated. Replacements (per the upstream changelog)
  are `show_inside` (when nesting inside an existing `Ui`)
  or fresh `Panel::*` builders. Audit each call site — the
  right replacement depends on whether the panel currently
  paints into the root viewport or into another `Ui`.

### Frame / margins
- `egui::Frame::none()` is deprecated; use `Frame::NONE` (a
  const) or `Frame::new()`. Mechanical replacement.

### Context-level helpers
- `egui::Context::style` is deprecated; renamed to
  `global_style`. Mechanical rename.
- `egui::Context::wants_pointer_input` is deprecated; renamed
  to `egui_wants_pointer_input`. Mechanical rename. Verify
  there is not also a `wants_pointer_input` on `InputState`
  that we should switch to instead.

### Input state
- `egui::InputState::screen_rect` is deprecated and was
  split into `viewport_rect()` and `content_rect()`. The
  deprecation note says callers "likely" want
  `content_rect()`. Audit the single call site in `app.rs`
  to confirm whether it cares about the OS window or the
  drawable content area — guessing wrong here changes
  positioning behaviour on platforms with a title bar /
  menu bar.

### Menus
- `egui::menu::menu_button` is deprecated; the new container
  lives under `egui::containers::menu`. Likely a
  non-trivial rewrite of the hamburger menu site because
  the new API uses a builder rather than a free function.
- `egui::Ui::close_menu` is deprecated. Find the replacement
  in the new menu container API and update the close path
  accordingly.

## Approach

The migration is mechanical at most call sites, but two
items need real judgement and should be done first so
later steps can lean on the decisions:

1. **`screen_rect` → `viewport_rect` vs `content_rect`.**
   Read the one call site, work out whether the consumer
   cares about the OS window or the drawable surface, and
   document the choice. The wrong choice is silent — it
   just produces subtly wrong layout on platforms where
   the two differ.
2. **Menu rewrite.** The new `egui::containers::menu`
   container is a different shape from the old
   `menu::menu_button` free function. Sketch the
   replacement against the hamburger menu site first so
   we know whether other menu sites need the same
   treatment.

Everything else (panel paths, `Frame::none`, `Context::style`,
`wants_pointer_input`, `default_width`, `show` → `show_inside`)
is mechanical. Once the call sites are migrated, delete the
`#![allow(deprecated)]` at the top of `ryll/src/app.rs` and
re-run `make lint` to confirm clippy is clean without it.

## Acceptance

- `ryll/src/app.rs` no longer contains `#![allow(deprecated)]`.
- `make lint` passes with `-D warnings`.
- `make test` passes.
- The hamburger menu, latency stats panel, bug-report
  region-select overlay, and any layout that depended on
  `screen_rect` all behave the same as before, verified
  by smoke-running the GUI against a QEMU SPICE target.

## Open questions

- Is there a planned eframe 0.35 / 0.36 that further
  reshapes the App trait (e.g. removes `update` entirely)?
  If so, the menu rewrite ought to happen ahead of that
  bump rather than after, to avoid double-migration.
- Does the `screen_rect` consumer want viewport or content
  semantics? See **Approach** point 1.
