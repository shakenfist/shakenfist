# Phase 5: docs and bug-report serialisation

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

The master plan is
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/). Phases 1–4
are complete (commits `a16f6781`, `b3e520b1`, `3780be03`,
`ed3f91db`). Phase 4 was smoke-tested against a TLS-off
QEMU; the bell tints visibly for Info / Warn / Error
unread, the side panel renders entries correctly, and the
mark-read-on-close edge fires.

## Goal

Close out the notifications work with the docs sweep and
bug-report zip serialisation the master plan reserved for
this phase. At the end of Phase 5:

* Bug-report zips (both F12-triggered and `--pedantic`)
  contain a `notifications.json` file with every
  `NotificationEntry` from the store at submit time.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` describe
  the notifications system, its producer/consumer model,
  and the recommended pattern for adding a new producer.
* `docs/plans/index.md` lists the notifications master
  plan as *Complete* with links to all five phase plans.
* `docs/plans/order.yml` includes the notifications
  master plan (it was missed when the master plan was
  created — the master plan's "Documentation index
  maintenance" step calls for it).

No new producer or GUI work; this phase is the wrap-up.

## Background

### Bug-report zip composition today

Two write paths, both in `ryll/src/bugreport.rs`:

* `BugReport::write_zip` (`bugreport.rs:1003`) — F12
  user-triggered. Uses `BugReport::new` (`bugreport.rs:808`)
  → `BugReport::assemble` (`bugreport.rs:849`).
* `BugReport::write_pedantic` (`bugreport.rs:1057`) —
  `--pedantic` automatic per-gap. Calls
  `BugReport::assemble` directly.

Both produce zips with the same shape: `metadata.json`,
`session.json`, `channel-state.json`,
`runtime-metrics.json`, plus optional `traffic.pcap`,
`screenshot.png`, and `screenshot-region.png`. Each
`zip.start_file(<name>, opts)` + `zip.write_all(<bytes>)`
adds one file; the pattern repeats for every entry.

`assemble` returns a `BugReport` struct that holds
already-rendered JSON strings:

```rust
pub struct BugReport {
    metadata_json: String,
    session_json: String,
    channel_state_json: String,
    pcap_bytes: Option<Vec<u8>>,
    screenshot_png: Option<Vec<u8>>,
    screenshot_region_png: Option<Vec<u8>>,
    runtime_metrics: RuntimeMetrics,
}
```

### Notification store snapshot already exists

Phase 1 added `NotificationStore::snapshot(&self) ->
Vec<NotificationEntry>` exactly so this phase could
serialise off-lock. `NotificationEntry` derives
`Serialize` and `Deserialize`. The Phase 1 unit test
`serde_round_trip_entries` pinned the format.

The store lives in `RyllApp` as
`notifications: SharedNotifications =
Arc<Mutex<NotificationStore>>`. `app.rs:1232` calls
`BugReport::new(...)` from `generate_bug_report`; that
call site is where the store reference is added.

### Documentation surfaces

Three top-level docs need touching:

* `README.md` — operator-facing. Has *Features* and
  *Usage* sections that should mention the notifications
  bell + side panel and the SPICE notifications it
  surfaces. Probably also mentions the keyboard shortcut
  story (F12 / F8 / Ctrl+Alt+V already documented).
* `ARCHITECTURE.md` — engineering-facing. Has sections
  for `--pedantic`, Display rendering, USB, WebDAV, etc.
  Phase 5 adds a *Notifications* section near the
  `--pedantic` section (they share the `warn_once_keys`
  source).
* `AGENTS.md` — AI-coding-assistant-facing. Has a
  numbered list of *Key Design Decisions* up to 20
  today; Phase 5 adds entry 21 for "notifications go
  through the unified store, not direct UI calls" and
  documents the recommended pattern for adding a new
  producer.

Plans index files:

* `docs/plans/index.md` — has the notifications master
  plan listed but with *Not started* status and "(phases
  not yet written)". Phase 5 updates the status to
  *Complete* and replaces that placeholder with links to
  the five phase plans.
* `docs/plans/order.yml` — does not include
  `PLAN-notifications.md` today. Phase 5 adds it. Phase
  files are not listed there per the file's own header
  comment.

## Design

### Bug-report serialisation

Add a `notifications_json: String` field to `BugReport`.
Populate it in `assemble` from a new `&Mutex<NotificationStore>`
parameter, mirroring the `app_snapshot: &Mutex<AppSnapshot>`
parameter that's already present:

```rust
pub(crate) fn assemble(
    report_type: BugReportType,
    description: String,
    region: Option<ReportRegion>,
    target_host: &str,
    target_port: u16,
    traffic: &TrafficBuffers,
    channel_snapshots: &ChannelSnapshots,
    app_snapshot: &Mutex<AppSnapshot>,
    notifications: &Mutex<NotificationStore>,   // NEW
    surface_pixels: Option<(&[u8], u32, u32)>,
    runtime_metrics: RuntimeMetrics,
    trigger: Option<TriggerTimestamps>,
    precomputed_screenshot_png: Option<Vec<u8>>,
) -> anyhow::Result<Self> {
    // ... existing body ...
    let snapshot = notifications.lock()
        .map(|s| s.snapshot())
        .unwrap_or_default();
    let notifications_json = serde_json::to_string_pretty(&snapshot)?;
    // ... fold into the returned BugReport struct ...
}
```

Position the new parameter immediately after
`app_snapshot` to keep "session-state inputs" grouped
together.

`BugReport::new` (`bugreport.rs:808`) needs the same new
parameter, threaded straight through to `assemble`.

`write_zip` and `write_pedantic` add one new entry each:

```rust
zip.start_file("notifications.json", opts)?;
zip.write_all(self.notifications_json.as_bytes())?;
```

Place it next to `runtime-metrics.json` (the last
unconditional entry) so the zip's file listing remains
alphabetical-ish: channel-state, metadata, notifications,
runtime-metrics, session.

### Caller updates

Two production callers:

1. `ryll/src/app.rs:1232` (`generate_bug_report`) —
   `BugReport::new(...)` call. Add `&self.notifications`
   as the new argument. `self.notifications` is
   `SharedNotifications = Arc<Mutex<NotificationStore>>`;
   `&*self.notifications` (or `self.notifications.as_ref()`)
   coerces to `&Mutex<NotificationStore>`.
2. `ryll/src/bugreport.rs:1146`
   (`register_pedantic_observer`) — closure builds the
   pedantic zip via `BugReport::write_pedantic`. The
   closure already captures `app_snapshot:
   Arc<Mutex<AppSnapshot>>`; capture
   `notifications: SharedNotifications` the same way.
   The function signature gains a `notifications:
   SharedNotifications` parameter; `app.rs:456` and
   `app.rs:3172` (the two registration sites) pass
   `notifications.clone()`.

### Lock-poison handling

`notifications.lock().map(|s| s.snapshot()).unwrap_or_default()`
matches the best-effort pattern used by the gap observer
and `push_notification`: if the mutex is poisoned the
report still writes, with an empty notifications list.
A poisoned lock indicates the GUI thread already
crashed; the bug report is the operator's diagnostic
tool, so producing one without notifications is
preferable to crashing the report writer.

The existing code uses `app_snapshot.lock().unwrap()`
(panicking on poison) at `bugreport.rs:864`, but that's
a gap I won't paper over here — keep the new code
consistent with the new pattern, leave the existing
unwrap alone.

### Test churn

`bugreport.rs` has seven `report.write_zip(&tmp).unwrap();`
test sites (search lines 1495, 1634, 1827, 2032, 2103,
2149, 2199) plus the corresponding `BugReport::new` /
`assemble` callers earlier in each test. Each test
needs a one-line addition:

```rust
let notifications = Mutex::new(NotificationStore::new());
```

passed as the new argument. Keep the construction
inline; the tests don't need a populated store.

Add ONE new unit test:

| Test | Asserts |
|------|---------|
| `bug_report_zip_includes_notifications_json` | Push two distinct entries (one Gap, one Spice) into a `NotificationStore`; assemble + write a zip; open the zip; assert `notifications.json` is present and `serde_json::from_str` round-trips to a `Vec<NotificationEntry>` of length 2 with the original messages. |

Place it next to the existing `write_zip` tests in
`bugreport.rs`.

### Pedantic observer signature change

`register_pedantic_observer` is called from two places:

* `RyllApp::new` at `app.rs:456` — `notifications` is
  in scope as a local just constructed.
* `run_headless` at `app.rs:3172` — same pattern; the
  local `notifications` is constructed adjacent to the
  call.

Both call sites add one argument: `notifications.clone()`.

The observer closure body already captures `app_snapshot:
Arc<Mutex<AppSnapshot>>` and threads it through to
`BugReport::write_pedantic`. Capture `notifications:
SharedNotifications` the same way and pass it through.

### Documentation: ARCHITECTURE.md

Add a new top-level section *Notifications* between the
existing *Display Rendering* and *Configuration*
sections (or wherever it fits best after reading the
file's flow). Outline:

```
## Notifications

Ryll surfaces three categories of operator-relevant events
through a unified in-memory store and a single GUI surface:

1. **Protocol gaps** — distinct `warn_once!` keys
   registered in `shakenfist-spice-protocol/src/logging.rs`.
   Each new key produces one Warn-severity Gap entry via
   the gap observer registered in `notifications.rs`.
2. **SPICE_MSG_NOTIFY** — opcode 7 messages parsed on
   every channel handler; each push as a Spice-source
   entry tagged with the receiving channel and the SPICE
   `what` enum value.
3. **Internal status** — bug-report writer success/failure,
   screenshot Ok/Err/no-surface, paste-completed.

The store (`ryll/src/notifications.rs`) is a 500-entry
`VecDeque<NotificationEntry>` behind
`Arc<Mutex<NotificationStore>>`. Pushes apply a 30-second
deduplication window: identical
`(source, severity, message, visibility)` tuples within
the window fold into the most recent entry's `count`,
incrementing the `[N×]` suffix the side panel renders.

The bell glyph in the status-bar right-edge cluster
tints by the highest-severity unread entry's colour
(sky-blue for Info, amber for Warn, muted red for
Error). Low-visibility SPICE entries are excluded from
the bell colour calculation per master plan Q4 — they
record but do not flash. Clicking the bell toggles a
right-side Notifications panel that lists entries
newest first; closing the panel marks every visible
entry read.

The `register_gap_observer` hook in
`shakenfist-spice-protocol/src/logging.rs` supports
multiple observers, so the `--pedantic` zip writer and
the notifications observer coexist independently.
```

The above is a sketch — adapt voice and line breaks
to match the surrounding ARCHITECTURE.md style during
the edit.

### Documentation: AGENTS.md

Add a new *Key Design Decisions* entry as item 21
(after item 20 "`--pedantic` mode: registry observer
pattern", which is thematically adjacent):

```
21. **Notifications go through the unified store, not
    direct UI calls** - The notification store at
    `ryll/src/notifications.rs` is the single producer
    boundary. Channel handlers, the bug-report writer,
    the screenshot dialog, and the gap observer all push
    `NotificationEntry` values via
    `Arc<Mutex<NotificationStore>>`; the GUI side panel
    and the status-bar bell read from the same store.
    Adding a new notification producer means: build a
    `NotificationEntry::new(severity, source, message)`
    (optionally `.with_visibility(v)`), then
    `notifications.lock().push(entry)`. New
    `NotificationSource` variants are added to the enum
    in `notifications.rs`; the side panel's
    `NotificationSource::label()` impl dictates how the
    new variant renders. Bug-report zips automatically
    include any new entries via `notifications.json`.
```

### Documentation: README.md

Two short additions:

* In the *Features* list, one bullet for the in-app
  notifications system. Suggested phrasing: "*In-app
  notifications panel surfaces protocol gaps,
  bug-report status, and SPICE_MSG_NOTIFY messages
  (e.g. QEMU's "channel is insecure" warnings) on a
  single bell + side-panel surface.*"
* In *Usage* or alongside the existing F12 / F8
  documentation, a note that the bell icon in the
  status bar shows unread notifications and clicking it
  opens the side panel.

Don't expand README.md beyond those two touches —
ARCHITECTURE.md is the right place for engineering
detail.

### Documentation: docs/plans/index.md

The current row reads (approximately):

```
| 2026-04-25 | [Notifications system](/components/ryll/plans/PLAN-notifications/) | In-app notifications surface for protocol gaps, bug-report status, and SPICE_MSG_NOTIFY messages that ryll currently drops on the floor | Not started | (phases not yet written) |
```

Update to:

* Status → *Complete*.
* Phase column → links to all five phase plans:
  `[1. Store](/components/ryll/plans/PLAN-notifications-phase-01-store/), [2.
  SPICE_MSG_NOTIFY](/components/ryll/plans/PLAN-notifications-phase-02-spice-notify/),
  [3. Existing sources](/components/ryll/plans/PLAN-notifications-phase-03-existing-sources/),
  [4. GUI](/components/ryll/plans/PLAN-notifications-phase-04-gui/), [5.
  Docs](/components/ryll/plans/PLAN-notifications-phase-05-docs/)`.

Use the formatting style of the other *Complete* plan
rows in the table (the *Hamburger menu* row is the
nearest precedent).

### Documentation: docs/plans/order.yml

Add `- PLAN-notifications.md: Notifications system`
in alphabetical or chronological order — match
whatever convention the file already uses. The file's
header comment notes phase files are not listed there;
this is a master-plan-only entry.

## Steps

### Step 1: bug-report serialisation

In `ryll/src/bugreport.rs`:

1. Add `notifications_json: String` field to
   `BugReport`.
2. Add `notifications: &Mutex<NotificationStore>`
   parameter to `BugReport::new` and
   `BugReport::assemble`, positioned immediately after
   `app_snapshot`.
3. In `assemble`, build the JSON via
   `notifications.lock().map(|s|
   s.snapshot()).unwrap_or_default();
   serde_json::to_string_pretty(&snapshot)?;`.
4. In `write_zip` and `write_pedantic`, add a
   `notifications.json` entry next to
   `runtime-metrics.json`.
5. Add `notifications: SharedNotifications` parameter
   to `register_pedantic_observer`; capture in the
   closure; pass through to `write_pedantic`.

In `ryll/src/app.rs`:

6. Update `generate_bug_report` (`app.rs:1211`) to pass
   `&self.notifications` as the new argument to
   `BugReport::new`. (`SharedNotifications` is `Arc<Mutex<...>>`;
   `&*self.notifications` coerces to `&Mutex<...>`.)
7. Update both `register_pedantic_observer` call sites
   (`app.rs:456` in `RyllApp::new` and `app.rs:3172` in
   `run_headless`) to pass `notifications.clone()`.

In `ryll/src/bugreport.rs::tests`:

8. Update each of the seven existing `BugReport::new` /
   `assemble` test setups to construct a local `let
   notifications = Mutex::new(NotificationStore::new());`
   and pass it.
9. Add the `bug_report_zip_includes_notifications_json`
   test per §"Test churn".

10. `make build`, `make test`, `pre-commit run --all-files`
    must all pass. The 220-test ryll count grows to 221.

### Step 2: documentation sweep

1. `ARCHITECTURE.md` — add the *Notifications* section
   per §"Documentation: ARCHITECTURE.md".
2. `AGENTS.md` — add design decision 21 per
   §"Documentation: AGENTS.md".
3. `README.md` — add the two touches per
   §"Documentation: README.md".
4. `docs/plans/index.md` — update the notifications row
   per §"Documentation: docs/plans/index.md".
5. `docs/plans/order.yml` — add the master-plan entry
   per §"Documentation: docs/plans/order.yml".
6. Update the master plan's *Execution* table row 5
   from "Not started" to "Complete (<this phase's
   commit>)".
7. `pre-commit run --all-files` — must pass (the bidi
   and zero-width unicode scan and rustfmt have no
   bearing on prose, but secrets and shellcheck will
   run).

## Sub-agent execution table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 (bug-report serialisation) | medium | sonnet | none | In `ryll/src/bugreport.rs`: add `notifications_json: String` field to `BugReport`; add `notifications: &Mutex<NotificationStore>` parameter to `BugReport::new` and `BugReport::assemble` immediately after `app_snapshot`; populate via `notifications.lock().map(|s| s.snapshot()).unwrap_or_default()` then `serde_json::to_string_pretty`; in `write_zip` (`bugreport.rs:1003`) and `write_pedantic` (`bugreport.rs:1057`) add a `zip.start_file("notifications.json", opts)? + write_all` entry next to `runtime-metrics.json`. Add `notifications: SharedNotifications` parameter to `register_pedantic_observer` (`bugreport.rs:1146`); capture in the closure; pass through to `write_pedantic`. In `ryll/src/app.rs`: update `generate_bug_report` call site (`app.rs:1232`) and both `register_pedantic_observer` call sites (`app.rs:456`, `app.rs:3172`) to thread `notifications` through. Update the SEVEN test sites in `bugreport.rs` (lines 1495, 1634, 1827, 2032, 2103, 2149, 2199 — each has its own `BugReport::new`/`assemble` call to update). Add ONE new test `bug_report_zip_includes_notifications_json` that pushes two distinct entries (one `Gap`, one `Spice`), assembles + writes a zip, opens it, and verifies `notifications.json` is present and round-trips to a `Vec<NotificationEntry>` of length 2 with the original messages preserved. Use the existing `zip` crate read API for verification (look at how the existing tests open written zips). Run `make build`, `make test`, `pre-commit run --all-files`. Rust toolchain runs in Docker via `make`. The 220-test ryll count grows to 221. |
| 2 (documentation sweep) | low | sonnet | none | Update four documentation files per PLAN-notifications-phase-05-docs.md: (1) `ARCHITECTURE.md` — add a new *Notifications* section between *Display Rendering* and *Configuration* (or wherever the surrounding flow places it best); use the sketch in §"Documentation: ARCHITECTURE.md" as content scaffolding but adapt voice and line wrapping to the file's existing style. (2) `AGENTS.md` — append item 21 to the *Key Design Decisions* numbered list per §"Documentation: AGENTS.md". (3) `README.md` — add two touches: a *Features* bullet about the in-app notifications panel, and a brief mention of the bell icon next to the existing F8/F12 documentation (don't go beyond those two touches). (4) `docs/plans/index.md` — update the notifications master plan row from *Not started* to *Complete* and replace "(phases not yet written)" with links to all five phase plans (use the *Hamburger menu* row's formatting as the precedent). (5) `docs/plans/order.yml` — add `- PLAN-notifications.md: Notifications system` matching whatever ordering convention is already in use. (6) Update the master plan's *Execution* table row 5 from "Not started" to "Complete (<commit>)" — leave a `<commit>` placeholder; the management session will fill in the hash after committing. Run `pre-commit run --all-files`. No code changes. |

Step 1 lands first because step 2's master-plan status
update should reflect a working build. Both land in
one phase commit.

## Administration and logistics

### Success criteria

* `BugReport::assemble` and `BugReport::new` take a
  `&Mutex<NotificationStore>` parameter; both production
  zip-writing paths (`write_zip` and `write_pedantic`)
  emit `notifications.json`.
* New test
  `bug_report_zip_includes_notifications_json` passes;
  ryll test count grows from 220 to 221.
* `--pedantic` zips include `notifications.json`
  alongside `traffic.pcap` and the existing JSONs.
* `ARCHITECTURE.md` gains a *Notifications* section.
* `AGENTS.md` gains design decision 21.
* `README.md` mentions the bell + side panel and the
  SPICE notifications it surfaces.
* `docs/plans/index.md` lists the master plan as
  *Complete* with links to all five phase plans.
* `docs/plans/order.yml` includes the master plan.
* `make build`, `make test`, `pre-commit run --all-files`
  all pass.
* The master plan's *Execution* table marks all five
  rows *Complete*.

### Risks

* **`assemble` parameter creep.** This function already
  carries an `#[allow(clippy::too_many_arguments)]` —
  one more parameter doesn't change the situation.
  Long-term refactor (introduce a `BugReportInputs`
  struct) is Future Work; out of scope here.
* **Test fan-out.** Seven existing test sites each get
  one new line. Mechanical and low-risk; the new
  parameter is positional so the compiler will catch
  every caller that's missed.
* **`notifications.json` format stability.** Phase 1
  pinned the format via `serde_round_trip_entries`. A
  future store-shape change (e.g. adding fields to
  `NotificationEntry`) would invalidate
  bug-report-zip parsers if any external tooling
  starts to depend on the file. Mitigated by serde's
  forwards-compatible defaults (`#[serde(default)]`
  on new fields when the time comes).

### Future work (deferred from this phase)

* **`BugReportInputs` aggregator struct** to tame
  `assemble`'s 12-parameter signature. Pure
  refactor; not blocking.
* **Per-session persistence** of notifications to
  `~/.local/share/ryll/`. Master plan §"Future work";
  out of scope.
* **External notifications-zip parser.** The format is
  documented by the structure of `NotificationEntry`;
  if another tool wants to consume the JSON, that
  tool's authors can serde-deserialise directly.

### Documentation index maintenance

When this phase lands, update the master plan's
*Execution* table row 5 from "Not started" to "Complete
(<this phase's commit>)" — the docs sub-agent inserts a
`<commit>` placeholder; the management session fills
in the hash post-commit.

The notifications master plan's overall status in
`docs/plans/index.md` is also updated by this phase
(step 2 (4)).

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
