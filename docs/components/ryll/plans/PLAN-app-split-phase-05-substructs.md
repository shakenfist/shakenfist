# Phase 5: Panel state sub-structs

Phase 5 of [PLAN-app-split.md](/components/ryll/plans/PLAN-app-split/).

## Planning effort

**High.** Phase 4 was a move: the compiler proved it, and the
only judgement was where each block went. This phase changes who
owns state, which means picking a boundary per panel and living
with it. Two of the master plan's premises for the phase turned
out to be wrong — the two-pass workarounds do not go away
(finding 1), and the two panels it names as the self-contained
starting point are the least self-contained of the three
(finding 3). Deciding what the phase promises instead, and in
what order, is the planning work.

Review effort: **high**. The diff will read as mechanical and
mostly is, but three of its decisions are cheap to wave through
and expensive to reverse once three panels share the shape.

## Scope

In scope — three panel state clusters move out of `RyllApp` into
structs owned by their `app/ui/*.rs` modules, each with a `show`
method taking `&mut self` plus the shared state it needs as
explicit arguments:

* `TrafficViewer` — 11 fields, `mod.rs:344-354`.
* `WebdavPanel` — 11 fields, `mod.rs:331-341`.
* `UsbPanel` — 15 fields, `mod.rs:248-255`, `:320-323` and
  `:326-328`.

That is 37 of `RyllApp`'s fields. With them: a named
`pub(in crate::app)` method on each panel for every state
transition that today happens as a field write from `events.rs`
or `connection.rs` (41 of them — finding 2), the `is_open()`
accessor `observe_ui_state` needs, the `UsbPanelOutcome` type
that replaces two `&mut` out-params, and the documentation the
move makes stale.

Out of scope, each with its reason:

* **The notifications panel.** Its panel-local state is two
  fields (`show_notifications_panel`,
  `notifications_panel_was_open_last_frame`). `notifications` and
  `notification_snapshots` are shared with every channel and with
  the bug-report path, so they cannot move. A struct wrapping two
  bools costs more in indirection than it removes. Decision 1.
* **Region select.** Its three fields are written by
  `trigger_snapshot.rs:326` and `:344` and read by
  `bug_report.rs:576` — it is bug-report state that happens to
  draw an overlay, not panel state. Decision 1.
* **The stats bar, surface, cursor and keys clusters.** Session
  state that the bug-report snapshot reads, not panel state.
  Moving them into `app/ui/` would put the authority for `stats`,
  `bandwidth` and `held_keys` inside draw code. Out, and not
  merely deferred: this phase takes the position that they belong
  on `RyllApp`.
* **Removing the two-pass workarounds.** Finding 1. The phase
  keeps the deferral and changes where it lives.
* **`reconnect.rs` at 881 lines.** Finding 5. Phase 4 handed it
  forward and phase 7's section already requires checking `app/`
  for oversize files, so it has a home.
* **The paste-error dialog's `take()` tidy.** Finding 1, site 4.
  Three lines in a file this phase otherwise does not touch.
  Future work.

## What the survey found

The master plan's phase 5 section was written on 2026-10-10,
before phases 2, 3 and 4 executed and before `ui_state.rs`
arrived from an unrelated plan. Five premises were checked: two
hold, one is incomplete, and two are wrong. Each correction has
been made at source in the master plan's phase 5 section and in
the `docs/plans/index.md` row, so this plan explains them rather
than being the only record of them.

### Finding 1 — sub-structs do not remove the two-pass workarounds

The master plan says to "remove the two-pass render-then-act
workarounds where disjoint borrows make them unnecessary". There
are five such sites. Not one is caused by `RyllApp` being a god
struct, so not one is removed by splitting it up:

| Site | Why it defers | Sub-struct effect |
|------|---------------|-------------------|
| `ui/usb_panel.rs:35` (`usb_action`) | `Panel::show`'s closure holds `&mut self`; executing needs `usb_tx` and writes two error fields | **Becomes local.** Under decision 2 every field it touches is in `UsbPanel`, so the out-param goes; the deferral stays, inside one module |
| `ui/usb_panel.rs:55` (`open_usb_bug_report`) | Targets the *bug dialog's* state via `open_bug_dialog` | **Becomes explicit.** `UsbPanel` cannot reach the dialog, so it returns `UsbPanelOutcome` — the shape `draw_surface` already uses |
| `mod.rs:1124` (`outcome.open_channel_bug_report`) | Same cross-panel shape, already a returned outcome | Unchanged; it is the pattern being generalised |
| `ui/dialogs.rs:129` (paste error) | `if let Some(ref msg) = self.paste_error_message` holds `&self` across the closure that wants to clear it | **None.** The same borrow of the same field inside `impl DialogsState`. Fixed by `take()`, not by sub-structing |
| `ui/region_select.rs:19` | `ctx.input(|i| …)` holds egui's input lock while the completion needs `self.notifications` | **None.** Working under egui's lock is a deadlock risk at any struct shape |

So the phase cannot promise the two-pass goes away. What it can
promise, and what the definition of done measures, is that no
deferred action crosses a module boundary as a `&mut` out-param:
two sites change from an out-param to either a local or a named
return type, and the three that remain are deferred for reasons a
reviewer can read in a comment.

### Finding 2 — `events.rs` and `connection.rs` own most of the state transitions

This is the finding that decides what the phase is worth, and the
master plan does not mention it.

The panels do not drive their own state. Channel events do.
`self.usb_*` and `self.webdav_*` are written **41 times** from
outside `app/ui/`. "Outside `app/ui/`" is the count's definition and
it matters: three further writers live *inside* `app/ui/`, in other
panels' modules, and finding 3 covers them.

| Writer | USB | WebDAV |
|--------|-----|--------|
| `events.rs` — per-event handlers | 11 | 13 |
| `connection.rs:140-158` — reconnect reset | 9 | 8 |
| **Total** | **20** | **21** |

`events.rs` already groups them into named handlers —
`handle_usb_channel_ready` (`:318`),
`handle_usb_device_connected` (`:324`),
`handle_usb_device_disconnected` (`:332`),
`handle_usb_connect_failed` (`:340`),
`handle_webdav_channel_ready` (`:348`),
`handle_webdav_sharing_started` (`:354`),
`handle_webdav_sharing_stopped` (`:363`),
`handle_webdav_error` (`:371`), plus `handle_disconnected`
(`:427`) and `clear_usb_operation_flags` (`:604`). Each is a
handful of field writes with a name already on it.

That maps one-to-one onto a method per transition on the
sub-struct, which is the phase's real payoff and a better one
than encapsulation for its own sake: 41 scattered field
assignments become a named API per panel, and "what can change
the USB panel's state, and from where" becomes a question you
answer by reading one file instead of grepping three. It also
means the phase is bigger than the master plan's "move its field
cluster" implies — moving the fields without moving the
transitions would leave `events.rs` reaching into a sub-struct's
private fields, which is a rename, not an encapsulation.

### Finding 3 — the master plan's starting point is inverted

The master plan says to "start with the most self-contained (the
WebDAV and USB panels, whose fields are already prefixed)". The
prefixes are real. The premise is backwards: by finding 2's
count, WebDAV and USB are the *least* self-contained clusters in
`app/ui/`, with 21 and 20 external writers. The traffic viewer,
which the master plan never names, has **zero** — nothing outside
`ui/traffic_panel.rs` reads or writes any of its eleven fields.

USB's fields are also in three blocks separated by about 70 lines
of unrelated fields, not one cluster. WebDAV's eleven are
contiguous.

So the step order inverts: the traffic viewer goes first, because
it is the one with the least outside it and therefore the cheapest
place to settle the shape. WebDAV follows as the template for the
event API, and USB — three blocks, 20 writers, and the two
out-params — goes last.

**Correction, 2026-11-25.** This finding first said the traffic
viewer had no external writer at all, and that step 5a was "a pure
field move the compiler can prove". Both were wrong, and step 5a's
sub-agent stopped and said so before changing anything. The error
was in the survey's method: its grep excluded the whole of
`app/ui/`, which is where two of the writers live, so the result
supported only the narrower claim that nothing outside `app/ui/`
touches the fields.

Ten of the traffic viewer's eleven fields are genuinely local to
`ui/traffic_panel.rs`. The eleventh, the open flag, has four users
and only one is the panel:

* `ui/stats_bar.rs:359-361` — the hamburger panel menu, one
  `ui.checkbox(&mut self.show_*, …)` per panel. This needs a
  `&mut bool`, so a read-only accessor is not enough. It drives
  **all three** panels' flags, so 5b and 5c are affected too.
* `ui/keys.rs:48` — F11 toggles the traffic viewer. A read and a
  write.
* `trigger_snapshot.rs:276-278` — the bug-report snapshot reads
  all three.
* The panel itself, gating its own draw.

Decision 3 says how the flag is exposed. No step in this phase is
a pure field move.

### Finding 4 — `ui_state.rs` is a consumer the plan never saw

`observe_ui_state` (`trigger_snapshot.rs:269`) reads eight
panel-open flags plus `reconnect_state` each frame and feeds them
to `UiStateTracker` for bug reports. It arrived in `e62a4a7`
("Let bug reports show ryll's own interface", #562), months after
the master plan's phase 5 section was written.

Three of its eight flags — `show_traffic_viewer` (`:276`),
`show_usb_panel` (`:277`), `show_webdav_panel` (`:278`) — are in
this phase's scope. The ten element *names* in that array are a
published contract: they are what bug reports contain and
`docs/diagnostics.md:241-242` lists them. Decision 3 keeps the
names and changes only what is read beside them.

### Finding 5 — `reconnect.rs` breaches the plan's own 800-line rule

Phase 4's definition of done requires that no file under
`ryll/src/app/` except `mod.rs` exceed 800 lines. `reconnect.rs`
is 881. Phase 4 recorded this honestly rather than ticking it: it
was 533 at phase 3's merge, and #718 added 350 lines to it on
`develop` itself, in a file phase 4 never touches.

Worth saying plainly because the usual guard does not catch it:
`tools/source-size-ratchet.py` fires at 1,500 lines, so 881 is
invisible to CI and only this plan's own criterion sees it. Out
of scope here (it is reconnect logic, not panel state); phase 7's
section already requires checking `app/` for new oversize files,
which is where it lands.

### What was checked and holds

* Every `app/ui/*.rs` file is an `impl RyllApp` block whose draw
  methods take `&mut self`. Phase 4 was the pure move it claimed
  to be, so the starting point is exactly what the master plan
  assumes.
* `eframe::App::ui` is 48 lines and constructs no panel or
  window, so the call sequence this phase edits is the short
  dispatcher phase 4 promised.
* `RyllApp::new` is 303 lines with a 133-field struct literal,
  and carries `#[expect(clippy::too_many_lines)]` (`mod.rs:467`)
  against a threshold of 200. Moving 37 fields into 3 sub-structs
  takes the literal to about 99 lines and `new` to about 269 —
  still over 200, so the exemption stays fulfilled and the build
  stays green. The margin is 69 lines; decision 5 handles the
  reason text, which stops being true well before the number
  does.
* `RyllApp::new` is the only one of the thirteen
  `#[expect(clippy::too_many_lines)]` functions in this phase's
  scope. (`c70d276`, which closed out phase 6, said "two of the
  thirteen annotated functions are the ones phase 5 would touch".
  The other twelve are in `bugreport.rs`, `main.rs`, `rdp.rs` and
  the renderer and compression crates; none is in `app/`. Noted
  because a commit message cannot be corrected.)
* `connection.rs:63` is 154 lines. Blocker note n8 lists it as an
  offender over "clippy's 100-line threshold" owned by phase 6 —
  but phase 6 measured the threshold at 200, so it is not an
  offender and needs nothing. Recorded so the next reader does
  not go looking for work that does not exist.

## Decisions

**1. Three clusters move, two do not.** The traffic viewer,
WebDAV and USB move; the notifications panel and region select
stay. The test applied was whether the cluster's state has a
reader outside its own module that is not `observe_ui_state`:
region select has two (`trigger_snapshot.rs`, `bug_report.rs`)
and they are the *drivers* of it, and the notifications panel's
substantive state is shared by every producer in the process. A
sub-struct that half the codebase reaches through is a rename,
not an encapsulation.

Note this is a different test from the one finding 3 applies.
USB and WebDAV have 41 external writers and still move, because
every one of those writers is a channel event that becomes a
named method on the panel (finding 2). Region select's external
writers are not transitions that belong to the panel — they are
the bug-report flow deciding when a region is being selected.

**2. The sub-struct owns its channel sender and every transition
that drives it.** `UsbPanel` holds `usb_tx` and `WebdavPanel`
holds `webdav_tx`; the 41 field writes in `events.rs` and
`connection.rs` become calls to `pub(in crate::app)` methods on
the panel, one per transition, named after the handler that calls
it. `connection.rs:140-158` becomes two calls —
`self.usb.reset_for_session(usb_tx)` and
`self.webdav.reset_for_session(webdav_tx)` — because that block
is one transition (a new session) and not seventeen.

This is the decision a reviewer is most likely to disagree with,
and the objection is sound: the sender's lifetime is the
connection's, not the panel's, so this puts two lifetimes in one
struct, and a panel struct replaced piecewise on reconnect is a
trap for whoever next adds a field to it.

Taken anyway. The panel is the sender's only consumer, so the
alternative — leaving `usb_tx` on `RyllApp` and passing
`&Option<Sender<UsbCommand>>` into `show` — keeps the field on
the struct this phase exists to shrink and adds an argument to
every call site to do it. And `reset_for_session` taking the new
sender is what makes the trap safe: the one method that installs
a sender is also the one method that resets the state, so a field
added to the struct is added in front of the code that must clear
it. That is strictly better than today, where the reset is
seventeen lines in another file.

**3. The open flag stays private behind `is_open` and `set_open`,
with `toggle` only where a hotkey flips it, and the element names
are pinned by a test.** Each moved sub-struct gets
`is_open(&self) -> bool` and `set_open(&mut self, bool)` as
`pub(in crate::app)`. The panel menu reads, offers and writes back —

```rust
let mut open = self.traffic_viewer.is_open();
if ui.checkbox(&mut open, "Traffic").changed() {
    self.traffic_viewer.set_open(open);
}
```

— which costs three lines per checkbox and keeps `ui.checkbox`'s
`&mut bool` requirement from forcing either a public field or an
`open_mut() -> &mut bool` getter.

`toggle(&mut self)` is added only to a panel that something flips
without reading it first, which in this phase is the traffic
viewer alone: F11 calls it. WebDAV and USB have no hotkey, so a
`toggle` on either would be an uncalled `pub(in crate::app)`
method — dead code, which `make lint` rejects. Writing one and
silencing the lint would be worse than not writing it.

Two alternatives were rejected. Making `open`
`pub(in crate::app)` is the smaller diff, but the first field of
the new struct being public is what the next field will cite.
Leaving the three flags on `RyllApp` and moving only the other
fields is defensible — three of the flag's four users are outside
the panel — but it puts a conditional per panel back into
`eframe::App::ui`, which phase 4 worked to make a flat sequence.

The array in
`observe_ui_state` keeps one line per element and its ten string
literals unchanged; only the right-hand side changes, from
`self.show_usb_panel` to `self.usb.is_open()`. A test asserts the
ten names in order, so a later field rename cannot silently
change what a bug report contains. Passing the sub-structs into
`observe_ui_state` instead was rejected because it makes a
bug-report function's signature grow with every panel that
moves.

**4. `UsbPanel::show` returns `UsbPanelOutcome`, not out-params.**
One field, `open_bug_report: bool`, mirroring `SurfaceOutcome`
from phase 4. `usb_action` disappears into the struct under
decision 2 and does not appear in the outcome. A named return
type rather than a bare `bool` because the surface's outcome set
that precedent and because the next cross-panel action to escape
USB should have somewhere to go that is not another parameter.

**5. `new`'s exemption reason is corrected in the same step that
breaks it.** The reason reads "one struct literal naming every
field", which stops being true the moment the first cluster
moves. Step 5d rewrites it. The done-criterion is `make lint`
passing, which mechanically proves the `#[expect]` is still
fulfilled; a figure in a comment would not.

**6. One pull request, four commits, with a shape gate after the
first panel.** The three moves share one pattern; three pull
requests would mean three reviews of it, and if the first panel's
shape is wrong the other two are wrong the same way and would be
redone regardless. So they land together — but the back brief
gates on the shape of `TrafficViewer` being agreed before WebDAV
and USB are written, which is the protection splitting the PR
would have bought at a quarter of the review. The gate sits after
the traffic viewer rather than after WebDAV because the traffic
viewer is the pure field move (finding 3): it settles the struct
shape without the event API confusing the question.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | `TrafficViewer`: move 11 fields, no external writers, settle the struct shape |
| 5b | high | opus | none | `WebdavPanel`: 11 fields plus the event API — 21 external writes become named methods |
| 5c | high | opus | none | `UsbPanel`: 15 fields from three blocks, 20 external writes, `UsbPanelOutcome`, `usb_action` made local |
| 5d | medium | sonnet | none | The name-contract test, `new`'s exemption reason, and the documentation |

Items in `app/ui/` are `pub(in crate::app)`, never `pub(super)`:
`pub(super)` from a grandchild of `app` reaches only `app::ui`
and the callers are in `app` itself. This is `AGENTS.md:240` and
phase 4 learned it the hard way. It applies to every step below.

Each panel module stays private and re-exports its struct at the
`ui` boundary, so `app/ui/mod.rs` gains one
`pub(in crate::app) use <module>::<Struct>;` per step and
`app/mod.rs` names the type as `ui::<Struct>`. Step 5a settled
this; 5b and 5c follow it rather than making the panel modules
public.

### Step 5a: `TrafficViewer`

The template for 5b and 5c, and the shape gate (decision 6). Do
not start 5b or 5c until the operator has agreed this step's
shape.

Create `pub(in crate::app) struct TrafficViewer` in
`ryll/src/app/ui/traffic_panel.rs` holding the eleven fields at
`ryll/src/app/mod.rs:344-354`: `show_traffic_viewer` (as `open`),
`traffic_viewer_entries` (as `entries`),
`traffic_viewer_last_refresh` (as `last_refresh`),
`traffic_viewer_paused` (as `paused`), and the seven
`traffic_filter_*` bools (as `filter_*`). Drop the prefixes
inside the struct — `self.traffic_viewer.paused`, not
`self.traffic_viewer.traffic_viewer_paused`.

The seven filters are **not** to become a nested
`TrafficFilters` struct: they are seven independent checkboxes
read once each, and a further level of field access buys no
reader anything.

Ten of these eleven fields are local to `ui/traffic_panel.rs`.
The eleventh, `open`, has three users outside it — see finding 3
and decision 3 — and they are all in this step:

* `ui/stats_bar.rs:359`
  (`ui.checkbox(&mut self.show_traffic_viewer, "Traffic")`, inside
  `draw_panel_menu`) becomes the three-line read-offer-write of
  decision 3. Leave the `obey_guest_size` checkbox above it and
  the USB and Folders checkboxes below it alone — those two are
  steps 5c and 5b.
* `ui/keys.rs:48`, the F11 handler, today
  `self.show_traffic_viewer = !self.show_traffic_viewer`, becomes
  `self.traffic_viewer.toggle();`. Leave
  the `region_select_active` guard around it as it is.
* `trigger_snapshot.rs:276`, below.

Replace lines 770-780 of the
struct literal in `RyllApp::new` with
`traffic_viewer: TrafficViewer::new(),` and give the struct a
`new()` that reproduces exactly those initialisers (note
`traffic_filter_*` initialise to `true`, and
`traffic_viewer_last_refresh` to an `Instant`, so `Default` will
not do).

`traffic: Arc<TrafficBuffers>` (`mod.rs:258`) **stays on
`RyllApp`**: it is shared with the bug-report path and the
channels, and it is the clearest case in the phase of a field a
panel reads but does not own. Both methods take it as an
argument:

* `refresh_traffic_entries(&mut self)` becomes
  `TrafficViewer::refresh(&mut self, traffic: &TrafficBuffers)`.
* `draw_traffic_panel(&mut self, ui)` becomes
  `TrafficViewer::show(&mut self, ui: &mut egui::Ui,
  traffic: &TrafficBuffers)` — read the body and pass only what
  it actually uses; do not pass `&mut RyllApp`.

The two call sites in `eframe::App::ui` are `mod.rs:1154`
(`self.refresh_traffic_entries()`) and `:1157`
(`self.draw_traffic_panel(ui)`). The refresh must stay at 1154,
before the panels: it is the refresh gate phase 4 moved out of
the old `ui` body, and the draw order in `ui` is a contract egui
enforces that no test in the tree can see.

Add `is_open`, `set_open` and `toggle`, all
`pub(in crate::app)`, per decision 3, and change
`trigger_snapshot.rs:276` to
`("traffic_viewer", self.traffic_viewer.is_open())`. The string
literal does not change.

### Step 5b: `WebdavPanel` and the event API

High effort and opus: this step decides the shape of the event
API that 5c then follows, and it rewrites transitions in a file
(`events.rs`) that the panel does not own.

Same struct treatment as 5a over the eleven contiguous fields at
`mod.rs:331-341` — `show_webdav_panel` (as `open`), `webdav_tx`
(as `tx`), `webdav_channel_ready`, `webdav_shared_dir`,
`webdav_read_only`, `webdav_sharing_active`,
`webdav_connected_at`, `webdav_error_message`,
`webdav_error_time`, `webdav_pick_dir_rx`,
`webdav_pick_dir_readonly`, each losing the `webdav_` prefix.
Struct literal lines to replace in `new`: `mod.rs:717`
(`webdav_tx`) and `:750-759`.

`draw_webdav_panel(&mut self, ui, ctx)` becomes
`WebdavPanel::show(&mut self, ui, ctx, …)`, called from
`mod.rs:1140`.

Then the part that is not a move. Twenty-one field writes outside
`ui/` become methods on `WebdavPanel`, each named for the handler
that calls it, each `pub(in crate::app)`:

* `events.rs:348` `handle_webdav_channel_ready` → one call.
* `events.rs:354` `handle_webdav_sharing_started(path, read_only)`
  → one call taking both arguments.
* `events.rs:363` `handle_webdav_sharing_stopped` → one call.
* `events.rs:371` `handle_webdav_error(err)` → one call.
* `events.rs:455-458`, inside `handle_disconnected` — the four
  writes that clear WebDAV state on channel disconnect. One
  method; name it for what it means (the channel went away), not
  for the four fields.
* `connection.rs:141` and `:153-158` → a single
  `reset_for_session(&mut self, tx: mpsc::Sender<WebdavCommand>)`
  that installs the sender and clears the state in one place.
  Decision 2 explains why these are one method and not two.

Each `events.rs` handler should end up as a one-line delegation.
Where a handler also touches non-WebDAV state (check each —
several push notifications), that part stays in `events.rs`.

Add `is_open` and `set_open` per decision 3, convert
the "Folders" checkbox at `ui/stats_bar.rs:363` to the
read-offer-write form step 5a established, and change
`trigger_snapshot.rs:278` to
`("folders_panel", self.webdav.is_open())` — the literal
`folders_panel` does not change. WebDAV has no hotkey, so there
is no `keys.rs` site for this panel.

Give the struct a doc comment saying which of its fields
`reset_for_session` clears and which survive a reconnect
(`pick_dir_rx` and `pick_dir_readonly` are file-picker state and
do not; check the rest against `connection.rs:153-158`). Risk 3
is what this comment is for.

### Step 5c: `UsbPanel`

High effort and opus: fifteen fields in three blocks, twenty
external writes, and two out-params to retire.

Fields, all into one `UsbPanel` — there is no case for three
structs:

* `mod.rs:248-255` — `usb_tx` (as `tx`), `usb_channel_ready`,
  `usb_connecting`, `usb_disconnecting`, `usb_error_message`,
  `usb_error_time`, `usb_device_description`, `usb_connected_at`.
* `mod.rs:320-323` — `show_usb_panel` (as `open`),
  `usb_available_devices`, `usb_virtual_disks`,
  `usb_devices_enumerated`.
* `mod.rs:326-328` — `usb_add_disk_rx`, `usb_add_disk_readonly`,
  `usb_add_disk_message`.

Struct literal lines in `new`: `:706` (`usb_tx`) and `:732-738`.

The eight private helpers in `usb_panel.rs` (`poll_usb_state`,
`draw_usb_header`, `draw_usb_channel_status`,
`draw_usb_connected_device`, `draw_usb_error_message`,
`draw_usb_operation_status`, `draw_usb_device_list`,
`draw_usb_add_disk`) become methods on `UsbPanel`, dropping the
`usb_` infix as the prefix leaves the fields.

Event API, following 5b's shape:

* `events.rs:318` `handle_usb_channel_ready`, `:324`
  `handle_usb_device_connected(desc)`, `:332`
  `handle_usb_device_disconnected`, `:340`
  `handle_usb_connect_failed(err)` → one method each.
* `events.rs:439-442`, inside `handle_disconnected` → one method.
* `events.rs:591` `clear_usb_operation_flags` → one method; it is
  already named for the transition.
* `connection.rs:140` and `:142-148` → one
  `reset_for_session(&mut self, tx: mpsc::Sender<UsbCommand>)`.

Two things change beyond the move and the API:

* `usb_action` (today `&mut Option<UsbCommand>` threaded into
  `draw_usb_device_list` and executed at `usb_panel.rs:35-53`)
  becomes a local inside `UsbPanel::show`. Every field its
  execution touches — `error_message`, `error_time`, `connecting`,
  `disconnecting`, `tx` — is now in the same struct, so nothing
  escapes. It stays a two-pass: `Panel::show` still holds the
  closure. Keep the comment and correct *why* it defers — the
  closure, not a borrow of `RyllApp`.
* `open_usb_bug_report` (today `&mut bool`) becomes
  `UsbPanelOutcome { open_bug_report: bool }`, returned from
  `show`. The caller in `eframe::App::ui` does what
  `usb_panel.rs:55-58` does today: read the panel's error message
  for the description and call
  `self.open_bug_dialog(BugReportType::Usb, description)`. Mirror
  how `mod.rs:1124-1128` handles
  `SurfaceOutcome::open_channel_bug_report`, including where it
  sits relative to the other draw calls.

The `show` call site is `mod.rs:1119`. Add `is_open` and
`set_open` per decision 3, convert the "USB" checkbox at
`ui/stats_bar.rs:363`, and change `trigger_snapshot.rs:277` to
`("usb_panel", self.usb.is_open())`. USB has no hotkey, so there
is no `keys.rs` site and no `toggle`. Add the reconnect doc comment risk 3 asks
for.

### Step 5d: the contract test, the exemption, and the docs

Three small things in one commit, all of which depend on 5a-c.

Add a test to `ryll/src/app/trigger_snapshot.rs`'s existing `mod
tests` pinning the ten ui-element names `observe_ui_state`
reports: build the array the same way and assert the names, in
order, against a literal list. A later field rename must not
silently change what a bug report contains.
`docs/diagnostics.md:241-243` publishes these names; this test is
what makes that page checkable.

Rewrite the `reason` on `RyllApp::new`'s
`#[expect(clippy::too_many_lines)]` (`mod.rs:467`). It currently
says "one struct literal naming every field", which is no longer
true: say that it names every field `RyllApp` still owns
directly, with the panel clusters constructed beside it. Do not
put a line count in the text — `clippy.toml` holds the threshold
and `make lint` checks the exemption.

Documentation:

* `docs/device-redirection.md:73-87` has a "**State tracking on
  RyllApp:**" list naming `usb_tx`, `usb_channel_ready`,
  `usb_connecting`/`usb_disconnecting`,
  `usb_device_description`, `usb_connected_at`,
  `usb_available_devices` and `usb_virtual_disks`. Every one of
  those names changes. Retitle the list for where the state now
  lives and rename the fields. The `usb_tx` entry says "created
  in `RyllApp::new()` and threaded through `run_connection()`",
  which stays true, but it is now installed by
  `UsbPanel::reset_for_session`. Line 70 of the same file says
  both panels use `egui::SidePanel::right`; egui 0.35 unified the
  panel types and the code has said `egui::Panel::right` since
  phase 4, so correct that too while the paragraph is open.
* `ARCHITECTURE.md:213-217`'s tree lists each `app/ui/*.rs` by
  what it draws — "USB redirection side panel" and the like. Add
  the state to the three entries that now hold it, one line each,
  because where state lives is what this inventory is for. Leave
  `mod.rs`'s entry at `:182` saying "RyllApp struct and fields" —
  it still does.
* `AGENTS.md`: **no change.** Nothing here is a convention an
  agent cannot infer from the code, and the one that is — items
  in `app/ui/` are `pub(in crate::app)` — is already at line 240.

## Risks and mitigations

**1. A transition is moved to a method and quietly changes
behaviour.** This is the phase's real hazard and it is not one
the compiler catches: `handle_webdav_sharing_started` writes four
fields in an order, and a method that writes three of them, or
writes them from a slightly different starting state, compiles
and is wrong. Mitigation: each moved handler is reviewed as a
before/after of the field writes, not as a diff of the call site
— the reviewer reads `git show develop:ryll/src/app/events.rs`
for the original body of every handler the step touches. The
field-write count per handler is in finding 2's table, so a
method that writes a different number of fields is visible
without reading both versions.

**2. The draw order changes and nothing notices.** egui reserves
panel space in draw order and windows are z-ordered; phase 4
preserved both and said so in a comment in `ui`. Changing
`self.draw_usb_panel(ui, ctx)` to `self.usb.show(ui, ctx)` is
where that gets quietly reordered, and `UsbPanelOutcome` adds a
statement after the call. Mitigation: the reviewer diffs the call
sequence in `eframe::App::ui` against `develop` line for line,
and the operator runs the result against a guest before the pull
request — which is the only check in existence for this, as phase
4 recorded.

**3. `UsbPanel` and `WebdavPanel` mix connection and panel
lifetimes.** Decision 2's accepted cost. A future field may need
clearing on reconnect and not get it. Mitigation: `reset_for_session`
is the single method that both installs the sender and clears the
state, so the reset is in front of anyone adding a field, plus
the doc comment steps 5b and 5c require. The reviewer checks that
comment exists and is accurate against `connection.rs:142-158`
as it stands on `develop`.

**4. A panel accessor is added that should not exist.** The
compiler forces a new `pub(in crate::app)` accessor for every
outside reader, and the easy fix for a reader that should not be
reading panel state is to add one. Mitigation: any accessor
beyond `is_open`, `set_open`, `reset_for_session` and the named
transitions
is argued for in the pull request. The done criteria make the
inventory greppable.

**5. The phase is larger than the master plan implies.** Finding
2 turns "move a field cluster" into "move a cluster and 41
transitions". That is more diff than the master plan budgeted,
and it is the operator's call before the work starts rather than
the reviewer's after: back brief point 1.

## Definition of done

* `ryll/src/app/mod.rs`'s `RyllApp` struct no longer names any of
  the 37 moved fields:

  ```
  grep -nE '^    (show_(usb|webdav|traffic_viewer)|usb_|webdav_|traffic_(viewer|filter)_)' \
      ryll/src/app/mod.rs
  ```

  prints nothing.

* No code outside a panel's own module assigns to its fields —
  the transitions go through named methods:

  ```
  grep -rnE 'self\.(usb|webdav|traffic_viewer)\.[a-z_]+ *=' \
      ryll/src/app/ --include='*.rs'
  ```

  prints nothing.

* `connection.rs` installs each sender and resets each panel in
  one call:
  `grep -nE 'self\.(usb|webdav)_' ryll/src/app/connection.rs`
  prints nothing.

* The three sub-structs exist and nothing wider than
  `pub(in crate::app)` escapes them:

  ```
  grep -nE 'pub(\([^)]*\))? (struct|fn)' \
      ryll/src/app/ui/{usb_panel,webdav_panel,traffic_panel}.rs \
      | grep -v 'pub(in crate::app)'
  ```

  prints nothing.

* No deferred action crosses a module boundary as an out-param.
  In `ryll/src/app/ui/`, no function signature takes `&mut bool`
  or `&mut Option<`:

  ```
  grep -nE 'fn [a-z_]+\([^)]*&mut (bool|Option<)' ryll/src/app/ui/*.rs
  ```

  prints nothing. (`UsbPanelOutcome` is a return type, not a
  parameter.)

* `RyllApp::new` is shorter but still exempt: `make lint` passes,
  which is only possible if its
  `#[expect(clippy::too_many_lines)]` is still fulfilled, and its
  `reason` no longer contains the phrase "naming every field".

* `observe_ui_state` still reports exactly these ten names, in
  this order, and a test asserts it:

  ```
  notifications_panel menu_or_popup traffic_viewer usb_panel
  folders_panel protocol_gaps bug_report_dialog region_select
  paste_error_dialog connection_modal
  ```

* The call sequence in `eframe::App::ui` is the same sequence of
  the same blocks as on `develop`, checked by the reviewer
  against `git show develop:ryll/src/app/mod.rs`, and `ui` is
  still under 60 lines and constructs no panel or window.

* `docs/device-redirection.md` names no field that no longer
  exists:

  ```
  for f in usb_tx usb_channel_ready usb_connecting usb_disconnecting \
           usb_device_description usb_connected_at \
           usb_available_devices usb_virtual_disks; do
      grep -q "\`$f\`" docs/device-redirection.md && echo "stale: $f"
  done
  ```

  prints nothing.

* `UsbPanel` and `WebdavPanel` each carry a doc comment saying
  which of their fields `reset_for_session` clears and which
  survive a reconnect (risk 3).

* `make test` and `make lint` pass, and `pre-commit run
  --all-files` is clean. The operator has run the build against a
  guest and confirmed the three panels still draw and still work
  (risk 2).

* No file under `ryll/src/app/` grows past 800 lines as a result
  of this phase. `reconnect.rs` is 881 before it starts and is
  not this phase's to fix (finding 5); the check is that nothing
  else joins it:

  ```
  find ryll/src/app -name '*.rs' ! -name mod.rs ! -name reconnect.rs \
      -exec wc -l {} + \
      | awk '$1 > 800 && $2 != "total" { print; bad=1 } END { exit bad }'
  ```

## Execution notes

**2026-11-25 (AEDT), back brief.** Decision 2 confirmed as
written: each panel owns its channel sender, installed and reset
by one `reset_for_session(tx)`. Decision 6's shape gate confirmed:
step 5a is reviewed before 5b and 5c are written. Back brief point
1 was answered when the plan was committed — full size, 37 fields
and 41 transitions.

**2026-11-25 (AEDT), finding 3 corrected before any code moved.**
Step 5a's sub-agent stopped without changing a file, because the
brief asserted that nothing outside `ui/traffic_panel.rs` touches
the eleven fields and that is false: the panel menu
(`ui/stats_bar.rs:359-361`) and F11 (`ui/keys.rs:48`) drive the
open flags, the menu for all three panels. The survey's grep had
excluded the whole of `app/ui/`, so it never saw two writers that
live in sibling panel modules, and the plan then over-stated what
the grep supported. Finding 3, decision 3 and the briefs for steps
5a, 5b and 5c are corrected above; no step in this phase is a pure
field move. The gate in decision 6 is what caught this, one step
before it would have been copied three times.

**2026-11-25 (AEDT), line references refreshed after 5a.** Every
`mod.rs` reference in the step briefs was recorded against the
pre-5a tree and is now stale by a fixed amount, because 5a
replaced eleven field declarations at `mod.rs:344` with one: the
struct literal in `new` shifted by ten lines and the draw calls in
`eframe::App::ui` by twenty. The field blocks 5b and 5c move sit
above line 344 and did not move. The briefs above now carry the
refreshed numbers. All fourteen `events.rs` and `connection.rs`
references were re-checked and hold exactly, as do the two
`usb_panel.rs` out-param signatures. Expect the same shift again:
5b removes eleven more field declarations above 5c's targets, so
5c's `mod.rs` numbers are re-checked against the tree before its
brief is sent rather than taken from this file. Grep for the
identifier, not the line.

**2026-11-25 (AEDT), decision 3 corrected: no `toggle` without a
hotkey.** Step 5b found decision 3 asking for `is_open`, `set_open`
and `toggle` on every moved panel while the same plan said, in both
the 5b and 5c briefs, that WebDAV and USB have no hotkey. A `toggle`
on those two is an uncalled `pub(in crate::app)` method, so
`make lint` fails on dead code. The agent dropped it rather than
silencing the lint, which is right. Decision 3, risk 4 and the 5b
and 5c briefs are corrected above; the traffic viewer keeps its
`toggle` because F11 calls it. 5c would have hit this identically,
so this is the second thing the decision 6 shape gate caught one
step before it was repeated.

**2026-11-25 (AEDT), line references refreshed again after 5b.** The
same ten-line and twenty-line shift as after 5a, for the same reason:
5b replaced eleven field declarations at `mod.rs:331` with one. Step
5c's and 5d's `mod.rs` numbers and three `events.rs` numbers are
refreshed above. Two notes for the record. The first refresh, after
5a, got the USB checkbox wrong — it reasoned the menu had grown by two
lines when the read-offer-write form adds three, so the checkbox is at
`ui/stats_bar.rs:363` and not `:362`. And the `events.rs` numbers that
held across 5a did not hold across 5b, because 5b edited that file
while 5a did not. Both say the same thing: a line number in a plan is
a snapshot, so the step briefs now tell the implementer to grep for
the identifier and treat the number as a hint. A future phase plan of
this shape would do better to cite identifiers and not lines.

**2026-11-25 (AEDT), two accessors on `UsbPanel`, argued per risk
4.** Step 5c added `device_description()` and `error_message()`,
both returning `Option<&str>`, beyond the inventory risk 4 allows
without argument.

`device_description` exists because `ui/stats_bar.rs` draws
"USB: {desc}" in the status bar. The survey counted writes outside
`ui/` and so never saw it: it is a read, from a sibling module
inside `ui/`, which is the same blind spot that stopped step 5a.
WebDAV has no equivalent reader, which is why 5b did not surface
it. The alternatives were leaving the field on `RyllApp`, which
defeats the phase, or dropping the indicator, which changes what
the user sees. A read-only accessor is the smallest surface that
keeps both.

`error_message` exists because decision 4 pins the outcome to one
bool, so the caller reads the error itself for the bug report's
description. The tighter alternative is an outcome of
`Option<String>` carrying the description, needing no accessor;
it was not taken because decision 4 names `SurfaceOutcome` as the
model and that type has exactly this shape, a bool whose caller
reads the description separately. Matching the pattern phase 4
established is worth more here than the encapsulation.

**2026-11-25 (AEDT), step 5d's premises checked before briefing.**
`RyllApp::new` is 269 lines after 5a-c, which is what the plan
predicted and comfortably over the threshold of 200, so its
`#[expect]` is still fulfilled and `make lint` stays green. Its line
reference and two documentation references were stale and are
refreshed above; `docs/device-redirection.md:73-87` was right.

One premise was wrong in substance rather than in line number. The
plan says the `ARCHITECTURE.md` tree lists each `app/ui/` file "by
what it holds"; it lists them by what they draw, and it names no
moved field anywhere, so nothing in it is stale. The instruction to
add the state to the three panel entries stands on its own merit
instead: where state lives is what a component inventory records,
and these three modules now own what they used to borrow.

Nothing constructs a `RyllApp` outside `main.rs`, so the contract
test cannot call `observe_ui_state`. The names have to be reachable
from a test without one, which is a design constraint the plan did
not see; 5d's brief carries it.

## Back brief

Before executing any step, back brief the operator. Three points
need an answer first, and the first is a decision about the
phase's size.

1. **Finding 2 changes what the phase costs and what it buys.**
   The master plan budgeted "move a field cluster per panel".
   The real work is 37 fields plus 41 state transitions becoming
   a named API on three structs. The benefit is correspondingly
   larger than encapsulation — it is that what can change a
   panel's state becomes answerable from one file — but so is the
   diff, and finding 1 removed the benefit the master plan
   actually advertised. Confirm the phase runs at this size, or
   pick an alternative: fields only, leaving `events.rs` reaching
   into the sub-structs (cheap, but a rename rather than an
   encapsulation); the traffic viewer alone, which is the only
   pure move; or close phase 5 as `Abandoned` with findings 1 and
   2 as the reason and go to phase 7.

   **Answered 2026-11-25: full size, as planned.** The operator
   chose the 37 fields plus the 41 transitions over the
   fields-only, traffic-viewer-only and abandon alternatives. Back
   brief this point as a confirmation rather than a question.
2. **Decision 2 is the one to argue with.** A panel owning a
   connection-scoped sender is what lets it execute its own
   actions, and it is also what mixes two lifetimes in one
   struct. Agree it with `reset_for_session` as the mitigation,
   or take the alternative — sender stays on `RyllApp`, passed
   into `show` — and accept that finding 1's first row stops
   improving.
3. **Step 5a is a shape gate (decision 6).** `TrafficViewer`
   lands and is reviewed before WebDAV and USB are written.
   Confirm the operator wants to review at that point rather than
   at the end: it is one review of an 11-field pure move instead
   of three reviews of 37 fields and 41 transitions.
