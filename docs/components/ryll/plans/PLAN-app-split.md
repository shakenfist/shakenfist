# Splitting app.rs

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

`ryll/src/app.rs` is 6,426 lines, the largest source file in the
repository and, at the time of shakenfist/development's
`PLAN-review-unit-size.md` survey, the largest in the fleet. It
holds the whole egui frontend: the `RyllApp` struct, the event
loop, every panel and dialog, the bug-report flow, the
auto-reconnect state machine, and about a thousand lines of tests
for all of them. It is GUI-only (`#[cfg(feature = "gui")] mod app;`
in `ryll/src/main.rs`); headless mode lives in `run_headless` in
`main.rs` and web mode under `ryll/src/web/`.

Where the length is, as of develop at `4d5f9e9`:

| Region | Lines | Notes |
|---|---:|---|
| `eframe::App::ui` | ~1,630 | One function drawing every panel, dialog, overlay and modal |
| `#[cfg(test)] mod tests` | ~1,020 | Tests for the pure helpers and state machines below |
| `RyllApp::process_events` | ~610 | One `match` over `ChannelEvent` |
| Bug-report and trigger-snapshot methods | ~500 | `begin_trigger_snapshot` through `poll_pending_bug_report` |
| `struct RyllApp` | ~380 | About 120 fields, grouped only by comments |
| Free helpers | ~270 | Resize decisions, lag-ring statistics, region validation |
| Reconnect state machine | ~170 | `ReconnectState`, `ReconnectPolicy`, `ModalVariant` |
| `NotificationSnapshotStore` | ~110 | Self-contained bounded store |

The field comments in `struct RyllApp` already name the seams:
cursor state, region selection, USB panel, WebDAV panel, traffic
viewer, notifications panel, bug-report dialog, auto-resize and
resolution-notification state, reconnection, auto-snapshot. Each
is a cluster of fields that one panel or one subsystem reads and
writes, sharing a struct with everything else only because the
file grew that way.

### Duplication with web mode

`shakenfist-spice-renderer/src/surface_mirror.rs` says of itself
that `SurfaceMirror::apply_event` "mirrors the display-bearing arms
of `ryll/src/app.rs::process_events`". Both dispatch the same eight
events -- `SurfaceCreated`, `SurfaceDestroyed`, `ImageReady`,
`ImageReadyChroma`, `ImageReadyAlpha`, `FillRect`, `CopyBits`,
`Invert` -- onto a `DisplaySurface`, the GUI through `GuiSurface`
in `ryll/src/display_gui.rs` (which adds only a texture handle).
Two hand-maintained copies of a draw-op dispatch are exactly how a
draw op ends up implemented in one mode and silently missing in
another, which is the failure `docs/multi-mode-parity.md` exists
to prevent. There may be more of this shape (headless frame
counting, channel sizing noted in `session.rs`); phase 1 finds out.

### Why prevention did not happen

`PUSH-AUDIT.md` already carries the fleet's `source-file-size`
shared block: files over roughly 800 lines are candidates to
split, over 1,500 want a stated reason. It did not stop this,
for two structural reasons rather than for want of wording:

* **A push audit sees one branch's diff.** No branch made
  `app.rs` 6,400 lines. Each added fifty to a file that was
  already over every threshold, and "this file was already huge"
  is not a finding against the branch in front of the reviewer.
  The block is phrased around files, but the audit is phrased
  around diffs.
* **It is advisory by design.** `PLAN-review-unit-size.md`
  deliberately made it "a candidate a reviewer may raise, never a
  gate". That is right for a hard cap, which would force splits
  at line numbers rather than seams. But advisory-only is
  structurally unable to stop slow growth, because the cost of
  each increment is always smaller than the cost of the split.

The problem is also as much about functions as files. A 1,630-line
`ui` and a 610-line `process_events` are the real defects; the file
length follows from them. Rust has a deterministic tool for that
half already: clippy's `too_many_lines` lint (in `clippy::pedantic`,
threshold configurable in `clippy.toml`), which ryll does not
enable.

## Mission and problem statement

1. **Reuse first.** Before moving any code, find what `app.rs`
   duplicates elsewhere in the workspace, and decide where shared
   logic should live. Code that headless or web mode also needs
   belongs in `shakenfist-spice-renderer`, not in a new
   `ryll/src/app/` module, and moving it twice is waste.
2. **Split along existing seams.** Turn `app.rs` into an
   `app/` module directory, using multiple `impl RyllApp` blocks
   (a child module can see its parent's private fields, so this
   needs no visibility widening beyond `pub(super)` on methods
   called across files). Tests move with the code they test.
3. **Then restructure state.** Group the field clusters into
   sub-structs that own their behaviour (`UsbPanel`,
   `TrafficViewer`, `CursorState` and so on), each with a
   `show(&mut self, ui, ...)` method. Disjoint field borrows are
   what remove the "two-pass: render then act" workarounds in
   today's `ui`.
4. **Make recurrence visible deterministically.** Enable
   `clippy::too_many_lines` so a new god function fails lint
   unless it carries an explicit, reviewable
   `#[expect(clippy::too_many_lines, reason = "...")]`. File-level
   enforcement is a fleet decision and is handled outside this
   plan (see open question 3).

Traits are deliberately not the tool here. A `Panel` trait would
only pay off if panels were stored heterogeneously and iterated,
and they are not: each has a fixed position, toggle and set of
state it needs. A trait would force them through one
lowest-common-denominator context argument. Revisit only if a
second implementation appears (for example, panel logic shared
with web mode).

## Open questions

1. **Answered in phase 1 (decision 1): the GUI holds a
   `SurfaceMirror` and a separate texture cache.** Original question:
   does the shared draw-op dispatch move `GuiSurface` onto
   `SurfaceMirror`, or extract a common function both call?
   The GUI needs a texture invalidation per touched surface;
   the mirror does not. A `SurfaceMirror<S: SurfaceLike>` or a
   free `apply_draw_event(&mut DisplaySurface, &ChannelEvent)`
   returning which surface it touched are both plausible. Phase 1
   decides, with the renderer crate's egui-free rule (AGENTS.md)
   as a hard constraint: the shared code must not see a texture.
2. **Answered in phase 1 (decision 5): no, it stays in ryll.**
   Original question: does the reconnect state machine belong in
   the renderer crate? Today only the GUI auto-reconnects. If headless or web
   mode should (check `docs/multi-mode-parity.md` and
   `docs/session-lifecycle.md`), the pure `ReconnectState` belongs
   in `shakenfist-spice-renderer`. Phase 1 answers this; if the
   answer is "yes, but not now", phase 2 still places it in a
   self-contained module that can be lifted without edits.
3. **Fleet-wide file-size enforcement.** A per-repository ratchet
   (fail CI if a file over the threshold grows, or a new file
   crosses it, against a committed baseline that may only shrink)
   would close the gap described under *Why prevention did not
   happen* without being the hard cap `PLAN-review-unit-size.md`
   rejected. It is language-neutral, so it belongs in
   shakenfist/development as a consistency-audit check across all
   projects, not in this plan. Function-length lints are
   per-language (clippy `too_many_lines`; ruff's `PLR0915` for
   Python) and fit as a per-language rule in the same audit. The
   operator decides whether to open that plan; this plan's phase 6
   is the ryll pilot of the function-length half either way.
4. **`git blame` continuity.** Splitting a file breaks naive
   `git blame`. Mitigation: phases 2-4 make pure-move commits (no
   edits beyond `use`, `mod` and `pub(super)`) so
   `git blame -C -C` follows the lines, and record those commits
   in a `.git-blame-ignore-revs` file where they are mixed with
   edits. Confirm the convention before phase 2 lands.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Reuse survey and shared draw dispatch | [PLAN-app-split-phase-01-reuse.md](/components/ryll/plans/PLAN-app-split-phase-01-reuse/) | In progress | |
| 2. Module directory and pure state | PLAN-app-split-phase-02-pure-state.md | Not started | |
| 3. Events and bug-report flow | PLAN-app-split-phase-03-events.md | Not started | |
| 4. Split `ui` into per-panel files | PLAN-app-split-phase-04-panels.md | Not started | |
| 5. Panel state sub-structs | PLAN-app-split-phase-05-substructs.md | Not started | |
| 6. Function-length lint | PLAN-app-split-phase-06-lint.md | Not started | |
| 7. Push audit | This file, below | Not started | |

Phase plans are written at the start of each phase (see the
`next-phase` skill), so each can react to what the previous one
found.

### Phase 1: Reuse survey and shared draw dispatch

Survey `app.rs` against `shakenfist-spice-renderer` (especially
`surface_mirror.rs`, `session.rs`), `ryll/src/main.rs`
(`run_headless`) and `ryll/src/web/` for logic implemented more
than once. Record each finding in the phase plan with a decision:
share now, share later (and where it would go), or genuinely
mode-specific. Answer open questions 1 and 2.

Land the draw-op dispatch deduplication in this phase, because it
is the known case and because phase 3 would otherwise move the
GUI copy only to delete it later. Add tests in the renderer crate
for the shared dispatch, and update `docs/rendering-pipeline.md`
if the shape of the pipeline description changes.

### Phase 2: Module directory and pure state

`git mv ryll/src/app.rs ryll/src/app/mod.rs`, then move out the
code that has no dependency on egui or on `RyllApp`'s fields:

* `app/reconnect.rs` -- `ReconnectState`, `ReconnectPolicy`,
  `ModalVariant`, `modal_variant_notification`, and their tests
  (or the renderer crate, per open question 2).
* `app/stats.rs` -- `Statistics`, `BandwidthTracker`,
  `LatencyTracker`, `push_with_cap`, `recent_lag_stats`.
* `app/resize.rs` -- `compute_auto_resize`,
  `compute_outgoing_resize`, `auto_fit_size_acceptable`,
  `resolution_notification_due`, `is_primary_surface`.
* `app/notification_snapshots.rs` -- `NotificationSnapshotStore`.
* `app/session_init.rs` -- `SessionInitDecision` and
  `classify_session_initialized`.

Pure moves only; each file carries its own `#[cfg(test)] mod
tests`. Expected reduction in `mod.rs`: roughly 1,500 lines.

### Phase 3: Events and bug-report flow

* `app/events.rs` -- `process_events`, with each arm group
  (display, cursor, audio, USB, WebDAV, notifications, session
  lifecycle) extracted into its own `handle_*` method so the
  `match` becomes a dispatcher.
* `app/bug_report.rs` -- trigger snapshot, `PendingBugReport`,
  `generate_bug_report`, `finish_bug_report`,
  `poll_pending_bug_report`, `file_notification_bug_report`,
  `maybe_write_disconnect_snapshot`, `validate_region`.
* `app/connection.rs` -- `reconnect`, `reconnect_manual`,
  `handle_critical_disconnect`, `handle_connection_failed`, the
  auto-snapshot task lifecycle, `build_connection_runtime`.

Update the comments in `surface_mirror.rs`, `session.rs` and
`bugreport.rs` that point at `app.rs` by name.

### Phase 4: Split `ui` into per-panel files

Reduce `eframe::App::ui` to a sequence of calls, one per panel,
each a `fn draw_*(&mut self, ui: &mut egui::Ui)` in `app/ui/`:
`stats_bar.rs`, `traffic_panel.rs`, `notifications_panel.rs`,
`usb_panel.rs`, `webdav_panel.rs`, `dialogs.rs` (bug report, paste
error, protocol gaps, reconnect modal), `region_select.rs`,
`cursor.rs`, and `keys.rs` for the global hotkey handling at the
top of `ui`. Still a move, not a redesign: panels keep reaching
into `self` as they do today. Panel draw order matters to egui
(the stats panel must precede `CentralPanel`); preserve it and
say so in a comment where `ui` calls them.

### Phase 5: Panel state sub-structs

One panel at a time, move its field cluster out of `RyllApp` into
a struct owned by its `app/ui/*.rs` module, with a `show` method
that takes `&mut self` plus the specific shared state it needs
as separate arguments. Start with the most self-contained (the
WebDAV and USB panels, whose fields are already prefixed) and
remove the two-pass render-then-act workarounds where disjoint
borrows make them unnecessary. This is the phase that changes
code rather than moving it, so it may land as more than one pull
request; the phase plan decides the grouping.

### Phase 6: Function-length lint

Enable `clippy::too_many_lines` workspace-wide (via
`[workspace.lints.clippy]` in the root `Cargo.toml`) and set
`too-many-lines-threshold` in `clippy.toml`. Pick the threshold
from data -- the distribution of function lengths after phase 5 --
not from the lint's default of 100. Every remaining offender
across the workspace gets
`#[expect(clippy::too_many_lines, reason = "...")]` with a real
reason, so the exemptions are a reviewable inventory rather than a
blanket allow. `#[expect]` rather than `#[allow]`, so that an
exemption that stops being needed fails and is removed.

Record the convention in `AGENTS.md` (it is one an agent cannot
infer from the code) and the rationale in
`docs/development.md`. If the operator opens the fleet plan
from open question 3, link it from here.

### Phase 7: Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of phases 1-6 as
the shared block below describes. In addition to the standard
waves, check that no `app/` file has itself become a new
oversize file, and that every `#[expect(clippy::too_many_lines)]`
reason still holds.

<!-- shared-block: plan-push-audit-phase v3 -->
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
  untriggered for as long as it did.
<!-- shared-block-end -->

In this repository the Execution phases are the *Phase order*
table each master plan carries, and `Merged` is its last column,
after `Status` — last because a row that omits it must still
reach `Status`. Every phase here lands as its own pull request,
so the cell holds one merge commit; the block's other shapes are
for repositories where a phase lands directly. See *Two ways this
runbook is invoked* in `PUSH-AUDIT.md` for what the push-audit
phase then does with those commits.

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

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `ryll/src/app.rs` no longer exists; `ryll/src/app/mod.rs`
  holds the struct, its constructor and a thin `eframe::App`
  impl, and no file under `ryll/src/app/` exceeds roughly
  800 lines without a stated reason.
* The GUI and web mode share one draw-op dispatch, and the
  "mirrors process_events" comment in `surface_mirror.rs` is
  gone because there is nothing left to mirror.
* `clippy::too_many_lines` is enabled workspace-wide and every
  exemption is an `#[expect]` with a reason.
* No behaviour changes in phases 2-4: the existing test suite
  passes unchanged apart from the tests' own module moves.
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
  new features or configuration options — including
  `docs/spice-protocol.md` if the change adds or modifies
  channels, message types, or compression algorithms.
* `ARCHITECTURE.md` has been updated only if the shape of
  the system changed, and `AGENTS.md` only if a convention
  changed. Both are a summary and an index into `docs/`.
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

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

Plan-specific close-out notes:

* **Future work.** `ryll/src/bugreport.rs` (4,275 lines) and
  `shakenfist-spice-renderer/src/channels/display.rs` (4,789) are
  the next largest files and want the same treatment;
  `shakenfist-spice-webrtc/src/bridge.rs` (3,089) after them. The
  fleet-wide file-size ratchet (open question 3) belongs in
  shakenfist/development.
* **Bugs fixed.** Phase 1: the GUI's `ImageReady` auto-create
  path sized the surface with an unchecked `left + width`, which
  panics a debug build on overflow; the shared mirror uses
  `saturating_add`.
* **Issues filed.** Phase 1 filed
  [#479](https://github.com/shakenfist/ryll/issues/479):
  `frames_received` counts different events in GUI and headless
  mode.
* **Related issues.** #468 (documentation names code by symbol,
  not line number): `docs/multi-mode-parity.md` cites
  `ryll/src/app.rs:NNNN` line numbers that are already stale and
  that this plan will invalidate entirely. Phases that move the
  cited code rewrite those citations by symbol, which should close
  #468's findings for this file.

