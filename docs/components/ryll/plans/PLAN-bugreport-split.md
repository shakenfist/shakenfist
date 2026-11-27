# Splitting bugreport.rs

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

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, data flow, and code organisation.
Consult `docs/development.md` for build commands, and
`AGENTS.md` for project conventions and a table of protocol
reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

`ryll/src/bugreport.rs` is 5,110 lines. It is the largest file in
`ryll/src/`, the second largest in the workspace after
`shakenfist-spice-renderer/src/channels/display.rs` (6,347), and
the largest `ryll/src/` entry in `tools/source-size-baseline` --
the ratchet that `PLAN-app-split`'s open question 3 asked for and
that shakenfist/development's `PLAN-size-ratchet.md` delivered. The
ratchet stops the file growing. It does not shrink it, and it was
never meant to: splitting along a seam is a deliberate act, and
this plan is that act for this file.

`PLAN-app-split` is the precedent and this plan follows its
sequencing -- reuse first, pure moves second, restructure state
third -- with three differences, each argued below: the first
phase relocates tests rather than production code, the module
boundary does not keep the old module's name, and there is no
facade.

### Where the length is

Measured on `develop` at `c7793b9`. Code is lines 1-2,422;
`#[cfg(test)] mod tests` is lines 2,423-5,110. So two thirds of
the file is tests.

| Code region | Lines |
|---|---:|
| `TrafficBuffers`, per-channel caps, `TrafficSink` impl | 411 |
| Five zip writers | 414 |
| `TrafficDirection` / `TrafficEntry` / `TrafficViewEntry` / `TrafficRingBuffer` | 199 |
| `DisconnectCause`, `PerChannelDiagnostics`, `ChannelEnd`, `ChannelState` | 233 |
| `BugReport::assemble`, `set_window_screenshot` | 183 |
| Report-size budget, pcap trimming, `oversized_for_attachment` | 157 |
| `AppSnapshot`, `SurfaceInfo`, `UiSnapshot` / `UiState` / `OpenUiElement` / `UiChange` | 187 |
| `KeyboardSnapshot`, `HeldKeyRecord`, `KeyLogEntry`, `KeyOutcome`, `KeyModifiers`, `SentScancodes` | 130 |
| `chrono_now`, `filename_timestamp`, `encode_png`, `encode_region_png`, `format_size` | 125 |
| `BugReportType`, `NotificationSnapshotState` | 115 |
| `BugReport` struct, `BugReport::new` | 83 |
| `register_pedantic_observer`, `PedanticConfig` | 81 |
| `ReportMetadata`, `TriggerTimestamps`, `ReportRegion` | 61 |
| Module doc, imports, renderer re-exports | 43 |

| Test group | Lines |
|---|---:|
| `BugReport::assemble`, per report type | ~716 |
| Serialisation of types the **renderer crate** owns | ~645 |
| Zip writers and `DisconnectCause` | ~370 |
| Traffic ring buffer and per-channel caps | ~300 |
| ryll-owned snapshot types (`AppSnapshot`, `Ui*`, `Keyboard*`) | ~215 |
| Pcap budget | ~180 |
| Helpers (time, PNG, size formatting) | ~143 |
| `ReportMetadata` and `BugReportType` | ~111 |

### It is three concerns with three different owners

The module's name describes a third of it. The other two thirds
are session infrastructure that the report happens to serialise:

* **Live traffic capture.** `TrafficBuffers` is the
  `TrafficSink` the protocol layer records every message into,
  the backing store for the F11 traffic viewer, and the session
  clock: `session_start()` is what `HeldKeys` and
  `UiStateTracker` are constructed from in `app/connection.rs`
  and `app/mod.rs`, and what `app/ui/stats_bar.rs` hands
  `streaming_state::classify` to drive the streaming indicator
  and the flap heuristic. `app/notification_snapshots.rs` holds a
  `TrafficBuffers` per pending notification. None of those is a
  bug report, and every one of them imports from
  `crate::bugreport` to get there.

* **Session state snapshots.** `AppSnapshot`, the `Ui*` types
  and the `Keyboard*` types are written continuously during a
  session -- the keyboard records by `input_egui.rs`'s
  `HeldKeys`, the UI records by `app/ui_state.rs`'s
  `UiStateTracker` -- and read once, at report time. They are
  the ryll-side companion of
  `shakenfist-spice-renderer/src/snapshots.rs`, which holds the
  same shape for the channels.

* **The report.** `BugReportType`, `ReportMetadata`,
  `DisconnectCause`, `assemble`, the zip writers and the
  pedantic observer. This is the only part the module name fits.

### What the file duplicates

1. **The five zip writers are one function copied five times.**
   `write_zip`, `write_zip_named`, `write_pedantic`,
   `write_disconnect` and `write_notification` each create a
   `ZipWriter`, re-declare the same `SimpleFileOptions`, and
   re-list `metadata.json`, `session.json`,
   `channel-state.json`, `traffic.pcap`, `notifications.json`
   and `runtime-metrics.json`. The divergence this has already
   produced is live: `write_zip` and `write_zip_named` include
   `screenshot.png`, `screenshot-region.png` and
   `window-screenshot.png`; the three automatic paths silently
   do not, which is correct today only because none of them
   assembles a report that has them. Nothing in the code says
   so.

2. **The filename sanitiser is written twice, verbatim** -- the
   `':' | '/' | '\\' | ' ' => '-'` match in `write_pedantic`
   and again in `write_disconnect`.

3. **Five `#[allow(clippy::too_many_arguments)]`, all for the
   same parameter list.** `target`, `traffic`,
   `channel_snapshots`, `app_snapshot`, `notifications` and
   `runtime_metrics` travel together through `new` ->
   `assemble` -> each `write_*`. `assemble` takes twelve
   parameters, of which six are those handles.

4. **Every `assemble` test rebuilds the same fixture.**
   Twenty-one tests open with the same `TrafficBuffers::new()`,
   `ChannelSnapshots::new()`, `Mutex<AppSnapshot>`,
   `Mutex<NotificationStore>` bindings, eighteen go on to call
   `BugReport::assemble` with the same twelve arguments, and
   most then repeat the same block that opens the zip and
   collects its entry names. There is no shared test support,
   and there cannot be one while the tests are a single `mod
   tests` in a single file -- which is also what makes splitting
   the tests across files expensive if the fixture is not
   extracted first.

### 645 lines of tests belong to another crate

`test_display_snapshot_serialises` and its eight siblings
(`inputs`, `cursor`, `main`, `playback`, `playback_codec`,
`usbredir`, `webdav`, `channel_snapshots_new`) test
`DisplaySnapshot`, `MainSnapshot`, `PlaybackSnapshot`,
`UsbredirSnapshot`, `WebdavSnapshot` and `ChannelSnapshots` --
every one of them defined in
`shakenfist-spice-renderer/src/snapshots.rs`, which is 1,154
lines and carries exactly one test of its own. They are in ryll
because `bugreport.rs` re-exports those types, and the tests
were written next to the re-export rather than next to the
definition.

That re-export has already misled the documentation:
`docs/diagnostics.md` says "the `ChannelSnapshots` struct in
`ryll/src/bugreport.rs`", which is not where it is.

### What this plan is not

It is not a crate extraction, and it does not anticipate one.
`bugreport.rs` is already free of egui -- the only occurrences
are field names and comments -- and `mod bugreport;` in
`ryll/src/main.rs` is not feature-gated, so headless mode and
`--rdp` both compile and use it today. Nothing here is blocked
on `PLAN-crate-boundaries`, and nothing here is made harder by
it: a module that is already egui-free and already built in
every mode lifts into a crate unchanged if that plan ever wants
it.

Nor does it chase parity. `docs/multi-mode-parity.md` records
web mode's bug report as "missing (out of MVP scope)" in all
four rows, so there is no second consumer pulling any of this
into `shakenfist-spice-renderer` the way `PLAN-app-split`'s
phase 1 found for the draw-op dispatch. The one relocation this
plan does make -- the 645 lines of tests -- goes to the crate
that already owns the types, and moves no production code at
all.

## Mission and problem statement

1. **Relocate before moving.** The renderer's tests go to the
   renderer. Moving them into a new ryll file first, and out
   again later, is the waste `PLAN-app-split`'s phase 1 exists
   to avoid.

2. **Delete the duplication before splitting.** One zip-writing
   core, one filename sanitiser, one test fixture module. Done
   while the code is still in one file, each is a self-contained
   refactor a reviewer can check by reading one diff; done after
   the split, each is a diff across four new files whose
   contents the reviewer has not seen before.

3. **Name the modules after their concerns.** Three siblings --
   `ryll/src/traffic/`, `ryll/src/snapshot/`, and
   `ryll/src/bugreport/` -- not one `bugreport/` tree. A status
   bar that imports its session clock from a module called
   `bugreport` is the defect; a subdirectory with the same name
   on it preserves the defect in a tidier shape.

4. **No facade.** `PLAN-app-split` kept `pub(in crate::app)`
   re-exports because `pub(super)` from a grandchild of `app`
   does not reach `app::ui`. Nothing here is private across the
   new boundaries, and `git grep -c 'crate::bugreport' --
   ryll/src` finds 24 references in 17 files besides the module
   itself, so the imports get repointed at the module that owns
   the type.

   The `pub use shakenfist_spice_renderer::snapshots::{...}`
   block at the top goes too. It re-exports fifteen names, of
   which exactly one -- `ChannelSnapshots`, in
   `app/connection.rs`, `auto_snapshot.rs`, `main.rs` and
   `rdp.rs` -- is used anywhere outside `bugreport.rs`. The
   other fourteen are re-exported for nothing, which is what the
   `#[allow(unused_imports)]` above the block is suppressing,
   and the block is why `docs/diagnostics.md` believes
   `ChannelSnapshots` lives in ryll. Four imports change; an
   allow and fourteen dead re-exports go.

5. **Pure moves.** Phase 3 is `git mv` plus `use`, `mod` and
   visibility edits and nothing else, so `git blame -C -C`
   follows the lines and no `.git-blame-ignore-revs` is needed
   -- the convention `PLAN-app-split`'s phase 2 confirmed
   (its open question 4).

The target is that no resulting file exceeds roughly 800 lines,
and that `ryll/src/bugreport.rs` leaves `tools/source-size-baseline`
without any of its successors arriving there -- nothing in the
resulting set is over 1,500, so the baseline loses an entry and
gains none.

### Target layout

Line counts are the measured regions above plus the test groups
they own; they are estimates for review, not a specification.

| File | Code | Tests |
|---|---:|---:|
| `traffic/ring.rs` -- direction, entries, `TrafficRingBuffer` | 199 | ~200 |
| `traffic/mod.rs` -- `TrafficBuffers`, caps, `TrafficSink` | 411 | ~100 |
| `traffic/budget.rs` -- report budget, pcap trimming, `oversized_for_attachment` | 157 | ~180 |
| `snapshot/mod.rs` -- `AppSnapshot`, `SurfaceInfo`, `Ui*` | 187 | ~160 |
| `snapshot/keyboard.rs` -- `Keyboard*`, `SentScancodes` | 130 | ~65 |
| `bugreport/mod.rs` -- module docs, `BugReport`, `new` | ~90 | - |
| `bugreport/metadata.rs` -- `BugReportType`, `ReportMetadata`, region, trigger | 144 | ~111 |
| `bugreport/disconnect.rs` -- `DisconnectCause`, `PerChannelDiagnostics` | 233 | ~111 |
| `bugreport/assemble.rs` | ~230 | ~450 |
| `bugreport/zip.rs` -- one writer core plus five entry points | ~200 | ~260 |
| `bugreport/pedantic.rs` | 81 | - |
| `bugreport/testsupport.rs` (`#[cfg(test)]`) | - | ~80 |
| `png.rs` (crate root) -- `encode_png`, `encode_region_png` | 110 | ~340 |
| `format.rs` (crate root) -- `chrono_now`, `filename_timestamp`, `format_size` | 50 | ~50 |

`bugreport/assemble.rs` is the file to watch: its tests are the
largest group in the file and the fixture module is what keeps
it under the target. If phase 2's extraction does not cut them
as far as the estimate, phase 3 decides between a further split
and a stated reason, and says which it chose.

The last two rows are not bug-report code at all.
`app/screenshots.rs` calls `encode_png` and `filename_timestamp`
for F8 screenshots, `app/ui/traffic_panel.rs` calls
`format_size`, `app/trigger_snapshot.rs` calls `chrono_now` and
`encode_png`, and `capture.rs` carries a wrapper whose own
comment reads "Re-export from bugreport module to avoid
duplication" -- which is the symptom, not the fix.

## Open questions

1. **Three sibling modules, or one `bugreport/` tree?** The
   mission states siblings and the consumer list above is the
   argument, but it is the one decision in this plan that
   changes every import site, so phase 1 confirms it against the
   full consumer list before phase 3 acts on it. The fallback,
   if the traffic and snapshot consumers turn out to be thinner
   than they look, is `bugreport/traffic/` and
   `bugreport/snapshot/` with the same file split -- cheaper to
   do and worse to live with.

2. **Where do the five helpers go?** `png.rs` and `format.rs` at
   the crate root is the proposal. The alternative is to leave
   them in `bugreport/` and accept that three non-report callers
   import from there, which is the status quo. A third option --
   a single `util.rs` -- is rejected: a module named for nothing
   accretes.

3. **Integration test or unit test for the relocated snapshot
   tests?** `shakenfist-spice-renderer/tests/snapshots.rs`
   (the crate already has `tests/control_socket.rs`) keeps
   `src/snapshots.rs` at 1,154 lines and off the baseline;
   folding them into `src/snapshots.rs` instead puts it at
   roughly 1,800 and onto the baseline, trading one baseline
   entry for another. The tests serialise public types, so the
   integration form should compile unchanged -- phase 1 confirms
   that every type and field they touch is `pub`, and falls back
   to the unit form (with a baseline entry and a reason) if not.

4. **`ReportSources<'_>` struct or a builder?** A borrowed
   struct of the six handles is the smaller change and removes
   all five `#[allow(clippy::too_many_arguments)]`. A builder
   would also let `assemble`'s optional arguments
   (`surface_pixels`, `trigger`, `precomputed_screenshot_png`,
   `region`) stop being four `None`s at most call sites. Phase 4
   decides; the struct is the default.

5. **Does phase 4 wait for `PLAN-app-split` phase 5?** Phase 4
   changes signatures that `app/bug_report.rs`,
   `auto_snapshot.rs`, `rdp.rs` and `main.rs` all call, and
   `PLAN-app-split` phase 5 is reshaping `app/bug_report.rs`'s
   callers on an unpushed branch. Phases 1 to 3 do not collide
   with it. The operator decides whether phase 4 waits; if it
   does, record it with the `plan-blockers` skill rather than in
   prose here.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Relocate the renderer's tests | PLAN-bugreport-split-phase-01-renderer-tests.md | Not started | |
| 2. Collapse the duplication | PLAN-bugreport-split-phase-02-dedup.md | Not started | |
| 3. Three modules | PLAN-bugreport-split-phase-03-modules.md | Not started | |
| 4. `ReportSources` | PLAN-bugreport-split-phase-04-sources.md | Not started | |
| 5. Crate-root helpers | PLAN-bugreport-split-phase-05-helpers.md | Not started | |
| 6. Push audit | This file, below | Not started | |

Phase plans are written at the start of each phase (see the
`next-phase` skill), so each can react to what the previous one
found.

The phases are ordered by what each one makes cheaper for the
next, and that ordering is load-bearing for phases 1 to 3: phase
1 removes a quarter of the file so phase 2 is deduplicating less
code, and phase 2 collapses the duplication so phase 3 moves
2,400 lines rather than 3,000 and moves no copy it would later
delete. Phases 4 and 5 depend only on phase 3 having landed, and
can land in either order or be deferred -- the file-size result
this plan exists for is complete at the end of phase 3, and
phases 4 and 5 are the parts that make the result honest rather
than merely smaller.

### Phase 1: Relocate the renderer's tests

**Planning effort: medium.** The work is mechanical; the
judgement is in confirming the destination.

Answer open questions 1 and 3. Move the nine snapshot
serialisation tests (~645 lines) to
`shakenfist-spice-renderer/tests/snapshots.rs`, keeping the
`#[expect(clippy::too_many_lines)]` on
`test_display_snapshot_serialises` and its reason verbatim --
the reason is an argument about the fixture, and it survives the
move intact. Check whether any of them duplicates the one test
already in `src/snapshots.rs`.

Also in this phase, because it is the same question asked of the
other direction: survey every consumer of
`crate::bugreport::*` across `ryll/src/` and record, per
imported name, which of the three concerns it belongs to. That
table is what phase 3 executes, and it is what settles open
question 1. `git grep -c 'crate::bugreport' -- ryll/src` found
24 references in 17 files besides the module itself at the time
of writing.

Expected: `bugreport.rs` down to roughly 4,465 lines, one
baseline entry lowered, no new files in ryll, no production code
touched.

### Phase 2: Collapse the duplication

**Planning effort: high.** Deciding what the shared zip core's
signature is, and which of the five callers' divergences are
intentional, is the whole of the work.

* One `write_entries`-style core plus five thin entry points.
  The core takes the destination path and the extra entries the
  caller adds (`disconnect-cause.json` is the only one today).
  Settle explicitly whether the three automatic paths *should*
  be including the screenshots they currently omit; if the
  answer is no, the core says so in a comment and the entry
  point passes the omission as an argument rather than by not
  writing the code.
* One filename sanitiser, called by both writers that need it.
* A `#[cfg(test)] mod testsupport` with the `assemble` fixture,
  a temp-directory helper, and a "list the entries in this zip"
  helper, and every test moved onto them.

Still one file at the end of this phase. Expected: roughly
`-400` lines of code and `-200` of tests, so `bugreport.rs`
around 3,865.

### Phase 3: Three modules

**Planning effort: medium**, if phase 1's consumer table is
good; high if it raised surprises.

`git mv ryll/src/bugreport.rs ryll/src/bugreport/mod.rs`, create
`ryll/src/traffic/` and `ryll/src/snapshot/`, and split to the
target layout above, tests moving with the code they test. Drop
the `pub use shakenfist_spice_renderer::snapshots::{...}` block
and repoint the four files that import `ChannelSnapshots`
through it at the renderer crate. Pure
moves: `use`, `mod`, visibility and nothing else, so the commits
are reviewable as moves and `git blame -C -C` follows.

Expected: `bugreport.rs` gone; twelve files -- the target
layout's rows less the two crate-root helpers phase 5 adds --
none over roughly 800 lines. `tools/source-size-baseline` loses its
`ryll/src/bugreport.rs` entry and gains nothing, which
`tools/source-size-ratchet.py --update` does on its own.

### Phase 4: `ReportSources`

**Planning effort: high.** It changes a signature four other
modules call, and open question 5 may hold it.

Answer open question 4. Bundle `target`, `traffic`,
`channel_snapshots`, `app_snapshot`, `notifications` and
`runtime_metrics` into one borrowed struct, and remove all five
`#[allow(clippy::too_many_arguments)]`. Callers to update:
`app/bug_report.rs`, `auto_snapshot.rs`, `rdp.rs`, `main.rs`,
and `register_pedantic_observer` in `bugreport/pedantic.rs`,
which holds the owned equivalents (`Arc<TrafficBuffers>`, a
cloned `ChannelSnapshots`, `Arc<Mutex<AppSnapshot>>`,
`SharedNotifications`) and will need a way to lend them.

### Phase 5: Crate-root helpers

**Planning effort: low.** Mechanical once phase 3 has landed.

Answer open question 2. `ryll/src/png.rs` takes `encode_png`
and `encode_region_png`; `ryll/src/format.rs` takes
`chrono_now`, `filename_timestamp` and `format_size`. Delete
`capture.rs`'s `chrono_now` wrapper and its "to avoid
duplication" comment, and repoint `app/screenshots.rs`,
`app/trigger_snapshot.rs` and `app/ui/traffic_panel.rs`.

This is last rather than part of phase 3 because it is the only
phase whose value is naming rather than size, and because
keeping it out of the move keeps phase 3's commits pure.

### Phase 6: Push audit
<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

In this repository the Execution phases are the *Phase order*
table each master plan carries, and `Merged` is its last column,
after `Status` — last because a row that omits it must still
reach `Status`. Every phase here lands as its own pull request,
so the cell holds one merge commit; the block's other shapes are
for repositories where a phase lands directly. See *Two ways this
runbook is invoked* in `PUSH-AUDIT.md` for what the push-audit
phase then does with those commits.

This plan's audit leans hard on the runbook's fourth floor check
-- that nothing the diff removed or renamed has a consumer left.
Almost every commit in phases 3 and 5 renames a path that
something else imports, and phase 3 deletes a re-export block
of fifteen names. The greps that establish there is no
consumer left are the evidence this audit exists to produce, and
they are stated rather than asserted. Nothing in the plan
publishes anything, runs unattended, or touches a credential, so
the runbook's other items are expected to be short.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving deep protocol research, algorithm
    understanding, or architectural decisions should be planned
    at high effort. Phases that are mechanical or follow
    well-established patterns can be planned at medium effort.

Each phase above states its own recommended planning effort.
Phase 2 is the high one: deciding the shared zip core's signature
means deciding which of the five writers' divergences are
intentional, and that is a judgement about what a bug report is
supposed to contain, not a refactor. Phase 4 is high because it
changes a signature four modules call. Phases 1, 3 and 5 are
moves.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    A worked brief for this codebase: instead of "add tests for
    the QUIC decoder", write "add tests for `quic_decode()` in
    `shakenfist-spice-compression/src/quic.rs`. Test vectors: a
    2x2 RGBA image encoded with the reference C encoder at
    `/srv/src-reference/spice/spice-common/...`. The function
    takes `(data, width, height)` and returns
    `Option<Vec<u8>>` of RGBA pixels."

For this plan specifically: a brief for a move step names the
source line range and the destination file, and says explicitly
that nothing else may change -- a sub-agent given "split this
file up" will reformat, rename and improve as it goes, and a
move commit that also contains improvements cannot be reviewed
as a move or followed by `git blame -C -C`. Where a step does
mean to change behaviour, the brief says which behaviour and
nothing more.

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code builds (`pre-commit run --all-files` or
          equivalent).
    - [ ] Tests pass (`cargo test --workspace` or equivalent).

Use `make` rather than `cargo` directly in this repository; the
Makefile wraps cargo in the devcontainer.

Every phase of this plan is a candidate for the same mistake --
a move or an extraction that quietly changes behaviour -- so the
review of each step reads the diff for edits that are not the
move, and `make test` is not evidence on its own: the tests move
with the code, so a test deleted in the move still leaves a
green run.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* No file that came out of `ryll/src/bugreport.rs` exceeds
  roughly 800 lines, and `tools/source-size-baseline` has lost
  its `ryll/src/bugreport.rs` entry without gaining a successor.
  Where a file is over the target anyway, the plan says which
  one and why.
* The zip layout a bug report produces is byte-for-byte what it
  was before phase 2, except for any divergence between the five
  writers that phase 2 deliberately resolved -- and each such
  resolution is named in the plan with the reason.
* There is one zip-writing core, one filename sanitiser, and one
  `assemble` test fixture; no
  `#[allow(clippy::too_many_arguments)]` remains on any
  bug-report function.
* No module in `ryll/src/` imports a type from `crate::bugreport`
  that is not about bug reports, and no call site imports a
  renderer-crate type through a ryll re-export.
* The 645 lines of snapshot serialisation tests run in
  `shakenfist-spice-renderer`, and `ryll` no longer tests types
  it does not define.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* Documentation in `docs/` has been updated to describe any
  new features or configuration options — for this plan that is
  `docs/diagnostics.md`, which names `ryll/src/bugreport.rs` in
  four places and names it wrongly in one (it says
  `ChannelSnapshots` lives there), and
  `docs/rendering-pipeline.md`, which cites
  `bugreport::filename_timestamp`.
* `ARCHITECTURE.md` has been updated only if the shape of
  the system changed, and `AGENTS.md` only if a convention
  changed. Both are a summary and an index into `docs/`.
  Phase 3 does change the code organisation, so
  `ARCHITECTURE.md` is in scope for it: both its `ryll/src/`
  tree and the `TrafficSink` row of its implementor table, which
  names `bugreport::TrafficBuffers`. No phase here changes a
  convention, so `AGENTS.md` should not be touched at all.
* If the changes affect SPICE protocol behaviour, the
  relevant documentation in `shakenfist/kerbside/docs/` has
  also been reviewed and updated if needed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete — including the
push-audit phase, and every finding it raised — update the
status column in `index.md` to *Complete*.

Every phase of this plan lands in ryll, so nothing here needs a
row in another repository's index.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

Already known, and deliberately out of scope:

* **`ryll/src/capture.rs` (1,783 lines) and
  `ryll/src/input_egui.rs` (1,746) are the next two entries in
  the baseline for `ryll/src/`.** Phase 5 touches the first of
  them only to delete one wrapper. `capture.rs` shares the pcap
  framing helpers (`segment_payload`, `build_tcp_frame`,
  `pcap_header`) with what becomes `traffic/`, so it is the
  natural next file and a plan of its own.
* **Web mode has no bug report** (`docs/multi-mode-parity.md`:
  "missing (out of MVP scope)" in all four rows). If that ever
  changes, the parts this plan places in `ryll/src/` are already
  egui-free and would move to `shakenfist-spice-renderer`
  wholesale -- but the decision belongs to whatever plan adds
  the feature, not to this one.
* **`BugReportType::Connection` reports carry only
  `MainSnapshot`.** The `TODO` in `assemble` notes that
  `UsbredirSnapshot` and `WebdavSnapshot` carry diagnostic
  fields a connection report could also surface. This plan moves
  that `TODO`; it does not resolve it.
* **The webdav channel records no traffic**, so `TrafficBuffers`
  deliberately has no webdav ring while `ChannelSnapshots` has a
  webdav field. The asymmetry is documented in `buffer_for`'s
  doc comment and is preserved by the move.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

Found while planning, to be resolved by phase 2 rather than left
to be found again:

* **The five zip writers disagree about screenshots.**
  `write_zip` and `write_zip_named` write `screenshot.png`,
  `screenshot-region.png` and `window-screenshot.png`;
  `write_pedantic`, `write_disconnect` and `write_notification`
  do not. That is harmless today only because none of those
  three assembles a report that has them: `assemble` leaves
  `screenshot_png` and `screenshot_region_png` `None` unless the
  report is `BugReportType::Display`, all three automatic paths
  pass `None` for `surface_pixels`, and `window_screenshot_png`
  is only ever set afterwards by `set_window_screenshot`, which
  only the GUI calls. It is a latent bug rather than a design:
  the next automatic report type that does carry a screenshot
  will silently drop it, and nothing in the three writers says
  they are omitting anything.
* **`docs/diagnostics.md` says `ChannelSnapshots` is a struct in
  `ryll/src/bugreport.rs`.** It is defined in
  `shakenfist-spice-renderer/src/snapshots.rs` and only
  re-exported. Phase 3 removes the re-export; the documentation
  fix goes with it.

The issue tracker has not been scanned for this plan yet. Phase
1 does that scan and records the result here, together with any
open issue that names `bugreport.rs`, the traffic viewer or the
report zip layout.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
