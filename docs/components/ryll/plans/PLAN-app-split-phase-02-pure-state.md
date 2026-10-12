# Phase 2: Module directory and pure state

Master plan: [PLAN-app-split.md](/components/ryll/plans/PLAN-app-split/).

## Planning effort

Planned at medium effort. Every step is a move with no behaviour
change, so the review effort goes into checking that nothing but
`use`, `mod` and visibility changed -- see the verification script
under *Definition of done*.

## Scope

In:

* Turn `ryll/src/app.rs` into `ryll/src/app/mod.rs`.
* Move the code that depends neither on egui nor on `RyllApp`'s
  fields into five child modules, each carrying its own tests.

Out:

* Anything that touches `RyllApp`'s fields or egui: `process_events`,
  the bug-report flow and connection lifecycle (phase 3), the `ui`
  panels (phase 4).
* Any edit to the moved code beyond `use`, `mod` and `pub(super)`.
  If a move reveals something worth fixing, record it in the master
  plan's close-out notes and leave the code alone.

## What the survey found

Surveyed against `develop` at `70232a2`, after #483 merged.

1. **Every item the master plan lists still exists, and none depends
   on egui or on `RyllApp`.** `SessionInitDecision` and
   `classify_session_initialized`, `NotificationSnapshotStore` (with
   `NotificationSnapshotEntry`), `ModalVariant`, `ReconnectState`,
   `ReconnectPolicy` and `modal_variant_notification`, `Statistics`,
   `BandwidthTracker` and `LatencyTracker`, and the free functions
   `is_primary_surface`, `push_with_cap`, `recent_lag_stats`,
   `auto_fit_size_acceptable`, `compute_auto_resize`,
   `compute_outgoing_resize` and `resolution_notification_due` are all
   top-level items in `app.rs`. The only `self.` references among them
   are inside their own `impl` blocks, and the only mention of egui is
   a doc comment on `compute_outgoing_resize`.
2. **The repository uses `mod.rs` style** (`ryll/src/web/mod.rs`,
   `shakenfist-spice-renderer/src/channels/mod.rs`), so
   `app/mod.rs` matches the master plan and the codebase.
3. **Several constants are shared between moved code and code that
   stays.** `STATS_BAR_HEIGHT` is used by both resize helpers and by
   `ui`; `MAX_AUTO_FIT_DIMENSION` by `auto_fit_size_acceptable` and by
   the oversized-surface warning in `process_events`;
   `MIN_SESSION_RESPAWN_INTERVAL` by `classify_session_initialized`
   and by `RyllApp`; `RECENT_LAG_RING_CAP` by `push_with_cap` and the
   bug-report snapshot; `RESOLUTION_NOTIFY_DEBOUNCE` by `ui`'s call to
   `resolution_notification_due`. Decision 3 settles where they live.
4. **Two pure items the master plan did not place.**
   `format_expiry_local` formats a `ReconnectPolicy` ticket expiry for
   the reconnect modal, so it belongs in `reconnect.rs` and moves in
   this phase. `event_drops_need_notice` and `EVENT_DROP_NOTICE` are
   pure but serve `process_events`, so they go to phase 3's
   `events.rs`. `screenshot_paths`, `validate_region` and
   `default_arrow_cursor` stay put for phases 3 and 4 as planned.
5. **The tests are one 1,000-line module** at the foot of `app.rs`,
   using `use super::*`. Some exercise egui directly
   (`logic_only_passes_keep_answering_repaint_requests`,
   `window_focused_is_current_in_a_logic_only_pass`) and must stay in
   `mod.rs`; the rest test the items above and move with them.
6. **Master plan open question 4 (blame continuity).** Pure-move
   commits are followed by `git blame -C -C` without help, and git's
   rename detection follows `app.rs` to `app/mod.rs` as long as the
   rename commit changes nothing else. A `.git-blame-ignore-revs`
   file is therefore not needed for this phase; decision 4 records
   the commit discipline that makes that true.

The master plan's phase 2 section is corrected in this commit to
place `format_expiry_local` and `event_drops_need_notice` (finding 4)
and to record the answer to open question 4.

## Decisions

1. **One commit per module, after a rename-only commit.** Step 2a is
   `git mv` and nothing else, so history follows the file. Each
   subsequent step moves one module. A reviewer can read each commit
   as "these lines left `mod.rs` and arrived here" without untangling
   five moves at once.
2. **Visibility is `pub(super)` and nothing wider.** Moved items are
   used only by `app/mod.rs` and its other children. `pub(super)`
   keeps them private to the `app` module, exactly as they are today
   as private items of `app.rs`. Fields of moved structs that `mod.rs`
   reads directly become `pub(super)` too; no accessor methods are
   added, because that would be an edit, not a move.
3. **A constant lives in the module that gives it meaning.**
   `RECONNECT_*` and `MAX_RECONNECT_ATTEMPTS` go to `reconnect.rs`;
   `NOTIFICATION_SNAPSHOT_*` to `notification_snapshots.rs`;
   `BANDWIDTH_HISTORY_LEN`, `LATENCY_HISTORY_LEN`, `FPS_WINDOW_SIZE`
   and `RECENT_LAG_RING_CAP` to `stats.rs`; `STATS_BAR_HEIGHT`,
   `MAX_AUTO_FIT_DIMENSION` and `RESOLUTION_NOTIFY_DEBOUNCE` to
   `resize.rs`; `MIN_SESSION_RESPAWN_INTERVAL` to `session_init.rs`.
   `mod.rs` imports what it still uses. `STATS_BAR_HEIGHT` is the one
   a reviewer might expect to stay with the UI; it moves because the
   resize arithmetic is what depends on its exact value, and `ui`
   merely lays out a bar of that height.
4. **Tests move with the code they test, and egui tests stay.** Each
   new module gets `#[cfg(test)] mod tests { use super::*; ... }`
   holding the tests for its items, moved verbatim, with test helpers
   (`err`, `no_policy`, `epoch`, `one_shot_policy`, `expiring_at`,
   `fresh_traffic`) moving with the tests that use them. A test that
   exercises items from two modules stays in `mod.rs`.
5. **The reconnect state machine stays in ryll** (phase 1, finding 6),
   so `reconnect.rs` is a child of `app`, not a renderer module.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | sonnet | none | Rename only: `git mv ryll/src/app.rs ryll/src/app/mod.rs`. No content change. |
| 2b | medium | sonnet | none | Move `session_init.rs` and `notification_snapshots.rs`. Detail below. |
| 2c | medium | sonnet | none | Move `reconnect.rs`. Detail below. |
| 2d | medium | sonnet | none | Move `stats.rs` and `resize.rs`. Detail below. |
| 2e | low | sonnet | none | Update live references to the old `ryll/src/app.rs` path. Added during execution; detail below. |

Every step: build and test with `make lint` and `make test` (cargo runs
in the devcontainer; never install Rust on the host), run
`pre-commit run --all-files`, and run the move-check script from
*Definition of done* against the step's diff. Do not commit; the
management session reviews and commits each step.

### Step 2a: rename

`git mv ryll/src/app.rs ryll/src/app/mod.rs`. `mod app;` in
`ryll/src/main.rs` resolves to the new path unchanged. Confirm
`git diff --cached -M --stat` shows a pure rename (100% similarity).

Commit: "Move app.rs to app/mod.rs."

### Step 2b: session_init.rs and notification_snapshots.rs

The two smallest modules, done together because each is a single
type and its tests.

* `ryll/src/app/session_init.rs`: `SessionInitDecision`,
  `classify_session_initialized`, `MIN_SESSION_RESPAWN_INTERVAL`, and
  the tests `the_first_session_is_always_accepted`,
  `re_announcing_the_held_session_is_a_duplicate`,
  `alternating_session_ids_are_rate_limited`,
  `a_genuine_relink_after_the_window_is_accepted` and
  `a_new_id_with_no_prior_acceptance_is_accepted`.
* `ryll/src/app/notification_snapshots.rs`:
  `NotificationSnapshotEntry`, `NotificationSnapshotStore`,
  `NOTIFICATION_SNAPSHOT_TTL`, `NOTIFICATION_SNAPSHOT_CAP`, the
  `snapshot_store_*` tests and their `fresh_traffic` helper.
  `notification_bug_report_type_serialises` tests a bugreport type,
  not the store: leave it in `mod.rs`.

Add `mod session_init;` and `mod notification_snapshots;` to
`mod.rs` with `use` lines for what it still references. Keep each
item's doc comment attached to it.

Commit: "Move session-init and snapshot store out of app."

### Step 2c: reconnect.rs

`ModalVariant`, `ReconnectState`, `ReconnectPolicy` and their
`impl`s, `modal_variant_notification`, `format_expiry_local`,
`MAX_RECONNECT_ATTEMPTS`, `RECONNECT_BACKOFF_SECS`,
`RECONNECT_CLUSTER_RESET`, and the `reconnect_*`, `ticket_*` and
`connection_event_message_format_*` tests with their helpers (`err`,
`no_policy`, `epoch`, `one_shot_policy`, `expiring_at`). Check each
helper's users before moving it; one used by tests that stay must
stay too.

Commit: "Move the reconnect state machine out of app."

### Step 2d: stats.rs and resize.rs

* `ryll/src/app/stats.rs`: `Statistics`, `BandwidthTracker`,
  `LatencyTracker`, `push_with_cap`, `recent_lag_stats`,
  `BANDWIDTH_HISTORY_LEN`, `LATENCY_HISTORY_LEN`, `FPS_WINDOW_SIZE`,
  `RECENT_LAG_RING_CAP`, and the `latency_tracker_*`,
  `recent_lag_stats_*` and `push_with_cap_*` tests. The
  `pub use shakenfist_spice_renderer::ByteCounter;` re-export next to
  `Statistics` stays in `mod.rs` unchanged. The survey found no
  importer of `crate::app::ByteCounter` outside `app.rs`, so it is
  probably vestigial, but removing it is an edit, not a move;
  `stats.rs` imports `ByteCounter` from the renderer crate directly.
* `ryll/src/app/resize.rs`: `is_primary_surface`,
  `auto_fit_size_acceptable`, `compute_auto_resize`,
  `compute_outgoing_resize`, `resolution_notification_due`,
  `STATS_BAR_HEIGHT`, `MAX_AUTO_FIT_DIMENSION`,
  `RESOLUTION_NOTIFY_DEBOUNCE`, and the `compute_auto_resize_*`,
  `compute_outgoing_resize_*`, `round_trip_*`,
  `auto_fit_size_acceptable_*`, `is_primary_surface_*` and
  `resolution_notification_due_*` tests.

Commit: "Move stats and resize helpers out of app."

### Step 2e: references to the old path

Added during execution: the rename in step 2a left 27 references to
`ryll/src/app.rs` in live documentation and code comments (found
with `git grep -n 'app\.rs' -- ':!docs/plans/'`). Plan files are
historical records and keep their references.

* Items that moved in this phase name their new file
  (`LatencyTracker` in `ryll/src/app/stats.rs`, the reconnect tests
  in `ryll/src/app/reconnect.rs`, and so on).
* Items still in `mod.rs` are named by symbol with the module
  directory (`RyllApp::reconnect` in `ryll/src/app/`), so phases 3
  and 4 do not break the reference again.
* `ARCHITECTURE.md`'s source tree shows the `app/` directory and its
  modules.

Commit: "Point references at the app/ module."

## Risks and mitigations

* **A "move" that quietly edits code.** Mitigation: the move-check
  script below, run by the management session on every step's diff
  before committing, plus reading every hunk that is not a pure
  addition or deletion.
* **A test silently dropped in transit.** Mitigation: the management
  session compares the number of tests `make test` reports for the
  `ryll` binary before step 2a and after step 2d; they must be equal.
* **Over-broad visibility.** Mitigation: `grep -rn 'pub fn\|pub struct\|pub enum\|pub const' ryll/src/app/` after
  each step must show only `pub(super)` on moved items (the
  `ByteCounter` re-export excepted).
* **`STATS_BAR_HEIGHT` moving away from the UI surprises a reader.**
  Mitigation: decision 3 states why; the reviewer checks `ui` imports
  it from `resize` rather than redefining it.

## Definition of done

* `git grep -n 'app\.rs' -- ':!docs/plans/'` finds no reference
  to the old path.
* `ryll/src/app.rs` does not exist; `ryll/src/app/` holds `mod.rs`,
  `session_init.rs`, `notification_snapshots.rs`, `reconnect.rs`,
  `stats.rs` and `resize.rs`.
* None of the items listed in the step briefs is defined in
  `app/mod.rs`:
  `grep -nE '^(pub(\(super\))? )?(struct|enum|fn|const) (SessionInitDecision|NotificationSnapshotStore|ReconnectState|ReconnectPolicy|ModalVariant|Statistics|BandwidthTracker|LatencyTracker|compute_auto_resize|compute_outgoing_resize|STATS_BAR_HEIGHT)\b' ryll/src/app/mod.rs`
  prints nothing.
* The `ryll` binary's test count is unchanged from `develop`.
* The move check passes for every step. It compares the multiset of
  non-blank, non-`use`/`mod`/visibility-only lines removed from
  `mod.rs` with those added to the new files:

  ```
  d=$(mktemp -d)
  git diff HEAD~1 -U0 -- ryll/src/app/mod.rs | grep '^-[^-]' | sed 's/^-//' \
      | sed -E 's/pub\(super\) //' | grep -vE '^\s*(use |mod |$)' | sort > "$d/removed"
  git diff HEAD~1 -U0 -- ryll/src/app/ ':!ryll/src/app/mod.rs' | grep '^+[^+]' | sed 's/^+//' \
      | sed -E 's/pub\(super\) //' | grep -vE '^\s*(use |mod |$)' | sort > "$d/added"
  diff "$d/removed" "$d/added"
  ```

  The only lines it may report are the new files' `#[cfg(test)] mod
  tests {` wrappers and their closing braces.
* `make lint`, `make test` and `pre-commit run --all-files` pass.
* No behaviour change, so no live-guest test is needed for this
  phase; the CI GUI build and unit tests are the evidence.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
