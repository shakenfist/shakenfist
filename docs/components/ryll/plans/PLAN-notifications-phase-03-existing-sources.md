# Phase 3: migrate Gaps and bug-report status as sources

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

The master plan for this work is
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/). Phases 1
([PLAN-notifications-phase-01-store.md](/components/ryll/plans/PLAN-notifications-phase-01-store/))
and 2
([PLAN-notifications-phase-02-spice-notify.md](/components/ryll/plans/PLAN-notifications-phase-02-spice-notify/))
are complete (commits `a16f6781`, `b3e520b1`). Read the
master plan's "Discoveries during this work" section first
— two recorded discoveries affect Phase 3 indirectly.

## Goal

Migrate the two pre-existing in-app status surfaces onto the
Phase 1 notification store, replacing the current ad-hoc UI
slots with `NotificationStore::push` calls so that Phase 4's
bell + side panel can render everything from one source.

At the end of this phase:

* Every new `warn_once!` key registered in
  `shakenfist-spice-protocol/src/logging.rs` produces a
  `Warn`-severity `NotificationSource::Gap` entry in the
  store, via a `register_gap_observer` callback installed
  by `RyllApp::new` (and `run_headless`).
* The bug-report writer's success/failure paths push
  `Info`/`Error` `NotificationSource::BugReport` entries
  instead of writing to `bug_status_message`.
* The colour-coded `Gaps: N` button (currently at
  `app.rs:1744`) and the transient `bug_status_message`
  slot (currently rendered at `app.rs:1766`) are deleted
  from the status-bar layout block, along with their
  backing fields and the gaps-popup floating window.
* Three other producers that currently piggyback on
  `bug_status_message` (screenshot success/failure,
  pre-flight "no display surface" message, paste
  completion) are migrated to the store too — they have to
  go somewhere when the slot is removed, and the store is
  the only sensible destination. They use
  `NotificationSource::Internal` since they are ryll's own
  status, not bug-report or SPICE traffic.
* The `--pedantic` zip writer (existing
  `register_pedantic_observer` in `bugreport.rs:1146`) is
  unchanged. Both observers register independently against
  the same `warn_once_keys` source.
* Operators have **no UI surface** for the store at the
  end of this phase — that is acceptable, master-plan
  approved, and the gap is closed by Phase 4. Until
  Phase 4 lands, gaps continue to fire `tracing::warn!` and
  bug-report success/failure continue to fire `info!` /
  `error!` log lines, so nothing the operator currently
  observes via `RUST_LOG` or `--verbose` regresses.

## Background: what's in the codebase today

### The Gaps badge (status bar)

`ryll/src/app.rs:1744-1763`:

```rust
let gap_count = shakenfist_spice_protocol::logging::warn_once_count();
let gap_label = format!("Gaps: {}", gap_count);
let gap_response = if gap_count > 0 {
    ui.add(egui::Button::new(
        egui::RichText::new(&gap_label)
            .color(egui::Color32::from_rgb(200, 80, 80)),
    ))
} else {
    ui.add(egui::Button::new(&gap_label))
};
if gap_response.clicked() {
    self.gaps_popup_open = !self.gaps_popup_open;
}
if gap_response.hovered() {
    gap_response.on_hover_text(
        "Distinct protocol gaps seen this session \
         — click to list.\nPass --pedantic to write \
         a bug report per gap.",
    );
}
```

The button polls the registry every frame via
`warn_once_count()`. It opens a floating popup (rendered
elsewhere — see below) listing the gap keys.

### The Gaps popup (floating window)

`ryll/src/app.rs:2664-2684`:

```rust
egui::Window::new("Protocol gaps")
    .open(&mut self.gaps_popup_open)
    .resizable(true)
    .default_width(400.0)
    .default_height(300.0)
    .show(ctx, |ui| {
        let mut keys = shakenfist_spice_protocol::logging::warn_once_keys();
        keys.sort();
        if keys.is_empty() {
            ui.label("No protocol gaps seen this session.");
        } else {
            ui.label(format!("{} distinct gaps:", keys.len()));
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                for key in &keys {
                    ui.monospace(*key);
                }
            });
        }
    });
```

This window is reachable only by clicking the Gaps button.
Once the button is gone, the window is unreachable, so it
goes too. Phase 4's side panel will surface notifications
(including each Gap entry) with richer metadata — the popup
is functionally subsumed.

### The bug-report transient slot

`ryll/src/app.rs:1766-1771`:

```rust
if let Some((ref msg, created)) = self.bug_status_message {
    if created.elapsed() < Duration::from_secs(5) {
        ui.separator();
        ui.label(msg);
    }
}
```

Backed by the `bug_status_message: Option<(String,
Instant)>` field declared at `app.rs:287` and expired by
the tick at `app.rs:1509-1513`. Written from **four**
distinct call sites today — broader than the master plan's
"the bug-report writer" framing suggests:

| Call site | Line | Today's use |
|-----------|------|-------------|
| `finish_bug_report` (Ok path) | 1260 | `"Bug report saved to {path}"` |
| `finish_bug_report` (Err path) | 1265 | `"Bug report failed: {e}"` |
| `open_screenshot_dialog` (no surfaces) | 1425 | `"No display surface to capture yet"` |
| `open_screenshot_dialog` (Ok path) | 1452 | `"Saved screenshot to {path}"` (or pluralised) |
| `open_screenshot_dialog` (Err path) | 1457 | `"Screenshot failed: {e}"` |
| `ChannelEvent::PasteCompleted` handler | 904 | `"Pasted {chars} chars ({elapsed_ms}ms)"` |

If the slot is removed without migrating all six writes,
those messages silently disappear from the GUI. The master
plan deletes the slot, so this phase migrates all six.

### The `register_gap_observer` hook

Already used by `--pedantic` at
`ryll/src/bugreport.rs:1162`. The registry supports
multiple observers; each new key fires every observer in
order. Replay semantics: a freshly-registered observer is
immediately invoked once per key that has already fired —
so if Phase 1's gap observer registers after a few gaps
have already accumulated during channel handshake, the
store catches up.

The observer's closure type is `Arc<dyn Fn(&'static str)
+ Send + Sync + 'static>`.

## Design

### Gap observer

Add a new constructor to `ryll/src/notifications.rs`:

```rust
/// Register a `register_gap_observer` callback that pushes
/// a `Warn`-severity `Gap` notification for each new key.
///
/// Replay-safe: keys that fired before registration are
/// pushed during `register_gap_observer`'s replay walk.
/// The store's dedup window collapses any pre-replay
/// duplicates; distinct gap keys produce distinct entries.
pub fn register_gap_notification_observer(
    notifications: SharedNotifications,
) {
    shakenfist_spice_protocol::logging::register_gap_observer(
        Arc::new(move |key: &'static str| {
            let entry = NotificationEntry::new(
                NotifySeverity::Warn,
                NotificationSource::Gap,
                key,
            );
            if let Ok(mut guard) = notifications.lock() {
                guard.push(entry);
            }
            // A poisoned lock is unrecoverable here; the
            // observer is best-effort logging-shaped, so we
            // swallow rather than panic on the firing
            // thread.
        }),
    );
}
```

Two design points to flag:

* **Lock poisoning is swallowed.** Producers must never
  panic the firing thread; a poisoned notifications mutex
  means the GUI thread already crashed while holding the
  lock, and the right behaviour at that point is to lose
  the entry, not to amplify the failure. The
  `--pedantic` observer takes a similar best-effort
  stance.
* **Replay synchronisation.** `register_gap_observer`
  walks the existing key set on the registering thread.
  If many gaps have already fired, the registering thread
  briefly takes the notifications lock once per gap.
  In practice this is a handful of keys at session start.
  No mitigation needed.

The function is called from `RyllApp::new` immediately
after `notifications` is constructed (at the same point
the `--pedantic` observer is conditionally registered, so
the two register adjacent to each other) and from
`run_headless`. Headless registration is consistent with
Phase 5's bug-report serialisation needs the store
populated regardless of GUI presence.

### Bug-report producer migration

`finish_bug_report` (`app.rs:1249-1268`):

```rust
fn finish_bug_report(
    &mut self,
    report_type: BugReportType,
    description: String,
    region: Option<ReportRegion>,
) {
    let (trigger, precomputed_png) = self.take_trigger_for_submit();
    match self.generate_bug_report(report_type, description,
                                    region, trigger, precomputed_png) {
        Ok(path) => {
            let msg = format!("Bug report saved to {}", path.display());
            info!("app: {}", msg);
            self.push_notification(NotifySeverity::Info,
                                   NotificationSource::BugReport, msg);
        }
        Err(e) => {
            let msg = format!("Bug report failed: {}", e);
            error!("app: {}", msg);
            self.push_notification(NotifySeverity::Error,
                                   NotificationSource::BugReport, msg);
        }
    }
}
```

The `push_notification` helper is a small new method on
`RyllApp` to keep the call sites tidy:

```rust
fn push_notification(
    &self,
    severity: NotifySeverity,
    source: NotificationSource,
    message: impl Into<String>,
) {
    let entry = NotificationEntry::new(severity, source, message);
    if let Ok(mut guard) = self.notifications.lock() {
        guard.push(entry);
    }
}
```

`info!` / `error!` tracing calls are preserved — they
were the only signal an operator running with `--verbose`
saw, and dropping them silently regresses non-GUI
debugging.

### Screenshot, paste, no-surface migration

`open_screenshot_dialog` (`app.rs:1425, 1452, 1457`):

| Old call | New call | Severity | Source |
|----------|----------|----------|--------|
| `bug_status_message = Some(("No display surface to capture yet", now))` | `push_notification(Warn, Internal, "No display surface to capture yet")` | Warn | Internal |
| `bug_status_message = Some((msg, now))` after `Ok(paths)` | `push_notification(Info, Internal, msg)` | Info | Internal |
| `bug_status_message = Some((msg, now))` after `Err(e)` | `push_notification(Error, Internal, msg)` | Error | Internal |

`ChannelEvent::PasteCompleted` (`app.rs:902-908`):

| Old | New | Severity | Source |
|-----|-----|----------|--------|
| `bug_status_message = Some((format!("Pasted {} chars ({}ms)"...), now))` | `push_notification(Info, Internal, format!("Pasted {} chars ({}ms)"...))` | Info | Internal |

Use of `Internal` for these reflects "ryll's own
operational status" — neither bug reports (the writer)
nor protocol gaps. The Phase 1 plan reserved `Internal`
for exactly this kind of producer.

The `tracing::info!` / `error!` calls already present at
each site stay. They are the only operator-observable
signal until Phase 4's GUI ships.

### Deletions

After producers are migrated, the following are removed:

| File:line | What |
|-----------|------|
| `app.rs:1744-1763` | `Gaps: N` button + tooltip + popup-toggle. |
| `app.rs:1766-1771` | `bug_status_message` rendering in the status bar. |
| `app.rs:1509-1513` | `bug_status_message` 5-second expiry tick. |
| `app.rs:2664-2684` | The "Protocol gaps" floating window. |
| `app.rs:287` | The `bug_status_message: Option<(String, Instant)>` field declaration. |
| `app.rs:580` | The `bug_status_message: None,` initialiser in `RyllApp::new`. |
| `app.rs` (search) | The `gaps_popup_open: bool` field declaration and its initialiser. |

After these deletes the surrounding `ui.horizontal(|ui|
{...})` block in the status bar may end with a trailing
piece of layout that no longer needs its leading separator
or padding — leave the surrounding code alone unless
something is structurally wrong, but do verify the status
bar still renders with no orphan separators.

### Headless mode

`run_headless` already plumbs `SharedNotifications` per
Phase 2. Add `register_gap_notification_observer(
notifications.clone())` immediately after the store is
created. The bug-report writer is not invoked from
headless mode today (it's a GUI affordance), so no
producer migration is needed there. Screenshot and paste
producers are also GUI-only.

### Tests

| Test | File | Asserts |
|------|------|---------|
| `gap_observer_pushes_warn_entry` | `ryll/src/notifications.rs` | After calling `register_gap_notification_observer(store)`, fire a `warn_once!("phase3_test:k1", "msg")`; the store has one entry with `severity == Warn`, `source == Gap`, `message == "phase3_test:k1"`. |
| `gap_observer_replay_pushes_existing_keys` | `ryll/src/notifications.rs` | Fire `warn_once!("phase3_test:k_replay", "...")` first, then call `register_gap_notification_observer(store)`; the store has at least one entry whose `message == "phase3_test:k_replay"` (replay semantics). |
| `gap_observer_distinct_keys_produce_distinct_entries` | `ryll/src/notifications.rs` | Register observer, fire two distinct `warn_once!` keys; store has two entries with different `message` fields. |

The bug-report and screenshot producers are not unit
tested in isolation — they involve filesystem I/O and the
existing `Result`-shaped contract. Their store push
happens immediately after the existing tracing call, so
correctness is verifiable by inspection. If a regression
test is wanted later, factor `finish_bug_report` to take
an injected `&mut NotificationStore` and assert on it;
not in scope here.

The `warn_once!` registry is process-global (per the
existing logging tests), so the new gap-observer tests
must use unique `phase3_test:` key prefixes to avoid
cross-test interference. Mirror the convention used by
`logging.rs::tests::register_gap_observer_fires_on_new_key`.

### Backwards compatibility

* `gaps_popup_open: bool` is removed. Anything depending
  on it (search the file) goes away.
* `bug_status_message` is removed. The four other
  internal call sites are migrated in this phase.
* The keyboard shortcut to open the gaps popup did not
  exist (the popup was only reachable via the Gaps
  button), so no shortcut churn.
* The `tracing::warn!` from each `warn_once!` and the
  `tracing::info!` / `error!` from each producer remain;
  log scrapers are unaffected.

## Steps

### Step 1: gap observer in the notifications module

In `ryll/src/notifications.rs`:

1. Add `pub fn register_gap_notification_observer(
   notifications: SharedNotifications)` per §"Gap
   observer".
2. Add the three tests from the §"Tests" table to the
   existing `#[cfg(test)] mod tests` block. Use
   `phase3_test:` key prefixes and a fresh
   `NotificationStore` per test.
3. Remove the module-top `#![allow(dead_code)]` if every
   item is now used after Phase 3 lands. (Likely still
   needed — `iter_unread`, `mark_*`, `clear`, `snapshot`
   land in Phase 4. Leave the allow until then.)

### Step 2: producer migrations in app.rs

In `ryll/src/app.rs`:

1. Add the `push_notification` helper method on `RyllApp`
   per §"Bug-report producer migration".
2. In `RyllApp::new`, immediately after the
   `let notifications = ...` construction, call
   `notifications::register_gap_notification_observer(
   notifications.clone())`. Place it adjacent to the
   `--pedantic` observer registration so the two are
   visually grouped.
3. In `run_headless`, do the same after `notifications`
   is created.
4. Migrate `finish_bug_report` (Ok and Err paths) to
   `push_notification(Info, BugReport, ...)` and
   `push_notification(Error, BugReport, ...)`.
5. Migrate the three `open_screenshot_dialog` writes to
   `push_notification(Warn|Info|Error, Internal, ...)`
   per the table in §"Screenshot, paste, no-surface
   migration".
6. Migrate the `ChannelEvent::PasteCompleted` writer to
   `push_notification(Info, Internal, ...)`.
7. Verify nothing else in the file references
   `bug_status_message` (search) — no remaining writes,
   no remaining reads.

### Step 3: GUI deletions

Delete:

1. The `Gaps: N` button block at `app.rs:1744-1763`.
2. The `bug_status_message` rendering at `app.rs:1766-1771`.
3. The expiry tick at `app.rs:1509-1513`.
4. The "Protocol gaps" popup window at `app.rs:2664-2684`.
5. The `bug_status_message: Option<(String, Instant)>`
   field at `app.rs:287` and its `None` initialiser at
   `app.rs:580`.
6. The `gaps_popup_open: bool` field declaration and
   initialiser (find via `grep`).

After deletions, `cargo build` may surface an unused-import
warning for `Instant` if it's only used for
`bug_status_message`'s timestamp. Re-check imports and
clean up; do not add `#[allow(unused)]`.

### Step 4: build, test, manual smoke

1. `make build` — must succeed.
2. `make test` — every existing test plus the three new
   gap-observer tests passes (ryll test count grows from
   210 to 213).
3. `pre-commit run --all-files` — must pass.
4. Manual smoke (record outcome in commit message): boot
   `make test-qemu`, run `./target/debug/ryll --verbose
   --direct localhost:5900`, exercise inputs briefly, then
   run a bug-report (F12) and a screenshot (F8). Confirm
   `/tmp/ryll.log` shows the existing `info!` /
   `error!` lines unchanged, and that the *Gaps: N*
   button and any transient status text are visibly
   absent from the status bar.

## Sub-agent execution table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 (observer + tests) | medium | sonnet | none | In `ryll/src/notifications.rs`, add `pub fn register_gap_notification_observer(notifications: SharedNotifications)` per §"Gap observer" — uses `shakenfist_spice_protocol::logging::register_gap_observer`, builds a `NotificationEntry::new(NotifySeverity::Warn, NotificationSource::Gap, key)`, and pushes; swallow lock-poison errors silently. Add the three tests from PLAN-notifications-phase-03-existing-sources.md §"Tests" to the existing `#[cfg(test)] mod tests` block. Use `phase3_test:` key prefixes for the `warn_once!` keys to avoid clashes with other tests in the registry. Run `make test`, `pre-commit run --all-files`. Rust toolchain runs in Docker via the project Makefile. |
| 2+3 (producer migrations + GUI deletions) | medium | sonnet | none | In `ryll/src/app.rs`, perform the producer migrations and GUI deletions described in PLAN-notifications-phase-03-existing-sources.md §"Steps 2 and 3". Add the `push_notification` helper. Register the gap observer in `RyllApp::new` and `run_headless` adjacent to the existing `--pedantic` registration. Migrate ALL six call sites that today write to `bug_status_message` (the table in §"The bug-report transient slot" enumerates them). Delete the `Gaps: N` button (lines around 1744-1763), the bug_status_message rendering (around 1766-1771), the 5-second expiry tick (around 1509-1513), the "Protocol gaps" floating window (around 2664-2684), the `bug_status_message` and `gaps_popup_open` field declarations and their `None`/`false` initialisers. Verify with `grep -n bug_status_message ryll/src/app.rs` and `grep -n gaps_popup_open ryll/src/app.rs` returning zero matches at the end. Run `make build`, `make test`, `pre-commit run --all-files`. The line numbers above are approximate — read the surrounding context before editing because the master plan's earlier line numbers turned out to be stale. Do not commit. Rust toolchain runs in Docker via the project Makefile. |

Step 1 lands first because step 2 calls
`register_gap_notification_observer`. Both land in one
phase commit.

## Administration and logistics

### Success criteria

* `register_gap_notification_observer` exists and pushes
  Warn/Gap entries on each new `warn_once!` key; tests
  cover replay and distinct-key behaviour.
* The bug-report writer's success and failure paths push
  Info/BugReport and Error/BugReport entries respectively.
* The screenshot, paste, and no-surface paths push to the
  store (Internal source) and no longer write to a
  removed status field.
* The `Gaps: N` button, its popup, and the
  `bug_status_message` slot are gone from the GUI.
* `bug_status_message` and `gaps_popup_open` fields are
  fully removed; no compiler or clippy warnings about
  unused imports remain.
* `make build`, `make test`, `pre-commit run --all-files`
  pass.
* `tracing::info!` / `error!` / `warn!` lines for the
  migrated producers continue to fire — `--verbose`
  output is unchanged for non-GUI users.

### Risks

* **Operator visibility regression.** With Phase 3
  landed and Phase 4 not yet, the GUI has no surface for
  the store. Master-plan accepted; mitigated because
  all migrated producers still log via `tracing`. The
  feature branch should not merge to `main` until Phase 4
  is in.
* **Status-bar layout reflow.** Removing the Gaps
  button and the transient may leave a trailing
  separator or unbalanced spacing. Verify the status-bar
  layout visually still looks right after deletion.
* **Replay-window race.** A `warn_once!` that fires
  between `register_gap_observer` pushing the observer
  onto the list and the registering thread snapshotting
  the registry may be observed twice. The store's
  30-second dedup window absorbs the duplicate
  (identical `(source, severity, message, visibility)`
  tuple), so the store ends up with the right state.
  This matches the contract documented at
  `logging.rs:79-80`.

### Future work (deferred from this phase)

* **Raw gap-keys debug view.** The popup window we
  delete here was useful for inspecting the registry
  directly. If a future need arises, add a debug-only
  panel that dumps `warn_once_keys()` — out of scope
  here because Phase 4's side panel covers the operator
  use case.
* **Refactor `finish_bug_report` for testability.**
  Could take the store as a parameter so the success
  and failure paths are unit-testable without
  filesystem I/O. Out of scope; existing structure is
  fine.
* **Channel disconnect/reconnect events.** Master plan
  §"Future work". Deliberately deferred.

### Documentation index maintenance

When this phase lands, update the master plan's
*Execution* table row 3 from "Not started" to "Complete"
with a link to the merge commit.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
