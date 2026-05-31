# Phase 5 — Auto-snapshot bug-report mode

Phase 5 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. The bug-report
plumbing already exists end-to-end — your job is to wire a
new automated trigger to the existing assembly + write path,
not to redesign either. Specifically read:

- `ryll/src/bugreport.rs` — `BugReport::new` (assembly) and
  `BugReport::write_zip` (file emission).
- `ryll/src/app.rs:2186` — the call site that builds a
  `BugReport` for a manual F8 trigger; `manual_bug_report_dir`
  for the existing dir-resolution logic.
- `ryll/src/config.rs` — how CLI args are declared and
  threaded through.
- `ryll/src/main.rs:228` and `:772` — how `bug_report_dir`
  is resolved and handed to the renderer.

Flag uncertainty explicitly rather than guessing.

## Goal

Add a flight-data-recorder mode that fires a complete bug
report on a fixed cadence into a rolling subdirectory.
Operator sets it once at session start; whatever happens
during the run is captured by construction, regardless of
whether the operator notices a symptom in real time.

This directly addresses the session-002f problem: audio
worked for the entire run, so no bug-report was filed —
but if audio had gone silent for 30 seconds mid-session
the operator might not have caught it in time to trigger
a report manually. With auto-snapshot at a 30-second
cadence, the silent window would have been captured in
two reports either side and we could correlate playback
counters across the boundary.

## Scope

In scope:

- New CLI flag `--auto-snapshot-interval SECONDS` (0 or
  unset = disabled, anything ≥ 1 enables periodic capture).
- New CLI flag `--auto-snapshot-cap N` (default 20) — the
  maximum number of auto-snapshot zips kept on disk; oldest
  pruned when capacity is exceeded.
- A tokio interval task spawned at session start when the
  interval is set; ticks every N seconds; assembles a
  `BugReport` via the existing `BugReport::new` path and
  writes it via `write_zip` into a dedicated
  `auto-snapshots/` subdirectory of the operator's
  bug-report dir.
- Auto-generated description matching the manual-report
  convention: `"auto-snapshot T+47.3s"` (session uptime
  embedded so an operator can correlate across snapshots).
- The auto-snapshot's `BugReportType` is `Connection`
  (already exists; lightweight; carries the new playback
  diagnostics from phase 4 because the channel-state
  dispatch checks the report type to pick which channel's
  snapshot to embed). **Decide in 5A**: do we want
  Connection (lightweight, just main) or extend to a new
  `BugReportType::AutoSnapshot` that includes
  playback/usbredir/webdav too? Working answer: extend.
- Per operator direction, **pcap stays in auto-snapshot
  zips**. The disk cost (~700 KiB pcap + ~150 KiB JSON +
  screenshot if Display ≈ ~1 MiB per zip, ~20 MiB at the
  default cap of 20) is acceptable for the diagnostic
  value.
- Filename scheme:
  `auto-snapshots/ryll-auto-snapshot-<utc_iso>-T+<uptime_secs>.zip`
  — UTC ISO timestamp for sorting, uptime appended so the
  filename alone tells you when in the run it fired.
- **Operator awareness — startup notification.** When the
  auto-snapshot interval task spawns at session start, push
  one `NotifySeverity::Info` notification via
  `push_notification` with `NotificationSource::Internal`:
  `"Auto-snapshot mode enabled — every {N}s, max {cap}
  snapshots, saving to {path}"`. One-shot, never repeated,
  no cool-down (this is the operator's confirmation that
  the flag took effect). Without it, the operator has no
  in-app signal that the mode is active until the first
  zip lands on disk.
- **Operator awareness — live counter in the stats panel.**
  Surface `auto_snapshots_saved: u64` and
  `auto_snapshots_pruned: u64` on a snapshot the stats panel
  reads (`AppSnapshot` is the natural home; it already
  carries other session-wide counters). Render a single
  line in the existing stats panel as
  `"Auto-snapshot: {saved}/{cap}"` when the mode is enabled
  (hide the line entirely when disabled so it doesn't add
  visual noise to non-auto sessions). Operator can glance
  at it any time without scrolling notifications. Updated
  by the interval task after each successful write_zip
  (saved += 1) and after each prune (pruned += deleted).

Out of scope:

- A hamburger-menu live toggle. CLI-only for this phase.
  Adding a UI toggle later is straightforward but doesn't
  add diagnostic value over the CLI flag (operator decides
  before the run whether they're hunting for an
  intermittent issue).
- Per-snapshot description customisation. The auto string
  is deterministic; an operator who wants a specific
  description should use the manual F8 path.
- Compression of the pcap before zip. The existing zip
  flow already compresses; extra work for marginal saving.
- Screenshot in auto-snapshots. Decision: **include the
  screenshot** if it can be captured cheaply from the
  existing trigger-snapshot ring (`AppSnapshot`'s
  `surfaces` already provide the latest frame). If
  reaching that data from a non-GUI thread is intrusive,
  drop the screenshot for auto reports — pcap +
  channel-state is the high-value payload. Document the
  decision in code comments.
- **A notification per snapshot.** At 30s × 10min that's
  20 notifications, which would bury everything else in
  the panel. The startup notification + live stats-panel
  counter cover awareness without the noise. If a future
  operator wants per-snapshot logging, a separate
  `--auto-snapshot-verbose` flag with a milestone-cadence
  notification (`"10 saved, 1 pruned"` every Nth tick) is
  the cheap-to-add follow-up; deferred deliberately.
- Disconnect-triggered auto-snapshot. The existing
  auto-disconnect path already fires a bug report on
  disconnect; auto-snapshot mode is independent of that.

## Open questions

- **Q1 (decide in 5A): how does the auto-snapshot task
  reach the data it needs?** `BugReport::new` takes
  references to `traffic`, `channel_snapshots`,
  `app_snapshot`, `notifications`, plus the target host/
  port and optional surface pixels. The GUI thread holds
  these on the `RyllApp` struct. Working proposal:
  - Introduce an `AutoSnapshotState` struct holding
    `Arc<TrafficCapture>`, `Arc<ChannelSnapshots>`,
    `Arc<Mutex<AppSnapshot>>`, `Arc<Mutex<NotificationStore>>`,
    plus the resolved target host/port, output dir, cap,
    and interval. Construct once on session bring-up
    after `RyllApp::new`.
  - The interval task owns the `AutoSnapshotState` and
    calls a new `BugReport::new_auto(...)` helper that
    wraps `BugReport::new` with the auto description
    and a `None` screenshot path.
  - All the existing fields on `RyllApp` are already
    `Arc`-backed (verify by reading `app.rs:2186` and
    nearby) — if not, this step bumps them to `Arc`.

- **Q2 (decide in 5A): how do we prune to the cap?**
  Working proposal: after each successful `write_zip`,
  scan `auto-snapshots/` for `ryll-auto-snapshot-*.zip`,
  sort by filename (which is timestamp-ordered), keep the
  newest N, delete the rest. Single-pass, no need to track
  state in memory across ticks. If the operator deletes
  some zips between ticks, our prune still works correctly
  (it operates on whatever's on disk).

- **Q3 (decide in 5A): what happens on write_zip failure?**
  The existing manual path surfaces failures via the
  `NotificationStore`. The auto-snapshot task should NOT
  spam the UI on every failed tick (e.g. if disk is full,
  every 30 seconds would be too noisy). Working proposal:
  log at `warn` on the first failure, emit a single
  `NotifySeverity::Warn` notification with a 5-minute
  cool-down, log at `debug` thereafter, but never block
  the interval task — keep ticking in case the underlying
  problem clears.

- **Q4 (open): does auto-snapshot mode work without
  `--bug-report-dir`?** Working answer: the existing
  `manual_bug_report_dir()` fallback chain
  (--bug-report-dir → --capture/bug-reports/ → cwd)
  applies unchanged. Auto-snapshots go into
  `<that_dir>/auto-snapshots/`. If neither flag is set,
  zips land in `./auto-snapshots/` in the current working
  directory — operator can run `--auto-snapshot-interval 30`
  from any directory and find their data afterwards.

## Design notes

### Where it slots in

```
[ryll startup]
    │
    ├─ parse args (--auto-snapshot-interval N, --auto-snapshot-cap M)
    │
    └─ RyllApp::new(...) returns the GUI app
            │
            └─ on session start (post-handshake):
                    │
                    └─ if auto_snapshot_interval > 0:
                            │
                            ├─ resolve dir = <bug_report_dir>/auto-snapshots/
                            ├─ build AutoSnapshotState (Arc'd handles)
                            └─ tokio::spawn(auto_snapshot_loop(state))
                                    │
                                    └─ interval.tick() every N seconds:
                                            ├─ BugReport::new_auto(...)
                                            ├─ report.write_zip(dir)
                                            ├─ prune dir to cap
                                            └─ continue loop
```

### Filename scheme

Each zip:
```
auto-snapshots/ryll-auto-snapshot-2026-05-18T20-37-42Z-T+47.3s.zip
```

ISO-style UTC timestamp + session uptime. The directory
listing sorts chronologically; uptime tells the operator
how far into the session each snapshot landed without
opening the metadata.json.

### Disk pressure

Per snapshot (rough order of magnitude):
- channel-state.json: 5–15 KiB
- metadata.json, session.json, notifications.json,
  runtime-metrics.json: ~5 KiB combined
- pcap: ~700 KiB per 20-second window (varies with
  bandwidth)
- screenshot.png: ~700 KiB for a 1920×1472 surface
- screenshot-region.png: not applicable for auto-snapshots
  (no region selected)

Per zip after compression: 700 KiB – 1.5 MiB. At the
default cap of 20, total disk is ~30 MiB. Acceptable;
adjustable via `--auto-snapshot-cap`.

### Interaction with `--capture`

`--capture <dir>` already creates `<dir>/bug-reports/`
for manual reports. Auto-snapshots go into
`<dir>/bug-reports/auto-snapshots/` when --capture is set
and no explicit --bug-report-dir is provided. Documented
in 5B.

### Interaction with `BugReport::new`'s 2-second metric
### sample

`BugReport::new` blocks for 2 seconds to sample runtime
metrics. The interval task runs on its own tokio task; the
blocking sample happens on a `spawn_blocking` thread so the
tokio runtime stays responsive. If interval N < 3 seconds,
samples overlap (mostly harmless — each is independent and
the data is per-sample). Document a minimum recommended
interval of 10 s in the CLI help text.

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5A | medium | sonnet | none | **Core implementation.** Add `auto_snapshot_interval: Option<u64>` and `auto_snapshot_cap: Option<usize>` to `ryll/src/config.rs` (clap derive). Thread through `main.rs` to `RyllApp::new`. Add `AutoSnapshotState` struct in a new `ryll/src/auto_snapshot.rs` (or `bugreport.rs` if cleaner) holding Arc'd handles to traffic, channel_snapshots, app_snapshot, notifications, plus resolved target_host/port, output_dir, cap, interval. Per Q1 working proposal: if any of these fields on `RyllApp` aren't already `Arc`-backed, bump them. Add `BugReport::new_auto(...)` helper that wraps `BugReport::new` with auto-generated description (`format!("auto-snapshot T+{:.1}s", session_uptime_secs)`) and `BugReportType::AutoSnapshot` (new variant — defaults its channel name dispatch to include playback + main + display + cursor + inputs + usbredir + webdav so a single zip carries everything). Spawn the interval task from `RyllApp::on_session_ready` (or wherever the session-bring-up code lives) when `auto_snapshot_interval` is set. **At spawn time** push one `NotifySeverity::Info` notification (`NotificationSource::Internal`): `"Auto-snapshot mode enabled — every {N}s, max {cap} snapshots, saving to {path}"` — one-shot, no cool-down. Add `auto_snapshots_saved: u64` and `auto_snapshots_pruned: u64` to `AppSnapshot` (or the snapshot the stats panel reads); bump after each successful write_zip / prune respectively. Render in the existing stats panel as `"Auto-snapshot: {saved}/{cap}"` only when the mode is enabled (hide the line entirely when disabled). Implement the prune-to-cap step per Q2: glob `auto-snapshots/ryll-auto-snapshot-*.zip`, sort by filename, delete oldest beyond cap. Per Q3: handle write_zip errors with a notification cool-down (5 min, single warn log on first failure). Per Q4: subdirectory is `<bug_report_dir>/auto-snapshots/` using `manual_bug_report_dir`'s fallback chain. Verify `make build && make test && make lint && pre-commit run --all-files`. |
| 5B | low | haiku | none | **Docs touch-up.** Update `README.md` (if it covers CLI flags) and `docs/configuration.md` to document `--auto-snapshot-interval` and `--auto-snapshot-cap`. Add a short paragraph to `docs/troubleshooting.md` under or near the playback-observability section explaining when to enable auto-snapshot mode (intermittent issues, flight-data-recorder use case). Cross-link from `docs/libvirt-spice-recommendations.md`'s "Side-by-side testing recipe" section as an alternative to manual periodic reports. Run `pre-commit run --all-files`. |
| 5C | — | — | — | **Operator smoke test.** Run a ryll session against `sf-4` with `--auto-snapshot-interval 30 --auto-snapshot-cap 20`. Let it run for ≥ 3 minutes while doing typical workload. Confirm: (a) at session start the notification panel shows one `Info` notification confirming auto-snapshot mode is enabled with the interval, cap, and target path; (b) the stats panel shows `"Auto-snapshot: N/20"` and N increments by 1 every ~30 s; (c) `<bug-report-dir>/auto-snapshots/` contains 6+ zips spaced ~30 s apart; (d) each zip's `channel-state.json` shows playback fields populating differently across snapshots (proves the snapshot is being re-taken not just the same data re-zipped); (e) after the cap is exceeded, oldest zips are pruned and the stats counter still reads N/20 (no overflow); (f) pcap is present in each zip and contains the ~20 seconds of traffic preceding the snapshot; (g) the notification panel does NOT show one notification per snapshot (proves we didn't accidentally make it noisy). This is operator verification, not a code change. |

Commits: one per step (5A, 5B). 5C is operator verification.

## Test plan

Automated (5A):

- Unit test for the filename generator: given (utc_now, uptime_secs), produces the expected pattern.
- Unit test for the prune helper: given a list of N+5 fake filenames, deletes the 5 oldest (by lexical sort, which is timestamp sort by construction).
- Integration test (if feasible): construct a small `AutoSnapshotState`, run the interval loop for 2 ticks (interval = 1 s), assert two zips appear in the target dir and both deserialise without error.

Manual (5C):

- Operator-driven; the smoke test in 5C is the contract.

## Documentation impact

- `README.md` / `docs/configuration.md`: new CLI flag docs (5B).
- `docs/troubleshooting.md`: paragraph on auto-snapshot mode and when to use it (5B).
- `docs/libvirt-spice-recommendations.md`: brief reference from the side-by-side testing recipe (5B).
- Phase 10 (documentation phase) will further consolidate.

## Success criteria

- `--auto-snapshot-interval 30` produces a new zip every
  ~30 seconds in `<bug-report-dir>/auto-snapshots/`.
- Each zip is a full bug-report artefact equivalent to a
  manual F8 trigger (channel-state.json with all phase-4
  diagnostics, pcap, metadata, runtime-metrics).
- Rolling cap enforced; oldest zips pruned when cap is
  exceeded.
- Disk usage stays bounded at roughly
  `cap × ~1 MiB per zip`.
- The auto-snapshot task does not interfere with the GUI
  thread, the audio thread, or the manual F8 report path.
- Operator awareness: one `Info` notification at session
  start confirms the mode is enabled (with interval, cap,
  and target path). A live counter in the stats panel
  shows `"Auto-snapshot: {saved}/{cap}"` and increments
  on each successful write; hidden when the mode is
  disabled. No per-snapshot notifications.
- `make build && make test && make lint && pre-commit run
  --all-files` clean.

## Back brief

Before executing 5A, the implementing sub-agent should
back-brief the operator with:

- Which fields on `RyllApp` need to become `Arc`-backed
  (if any).
- Whether they're introducing a new `BugReportType::AutoSnapshot`
  variant or piggybacking on `Connection`.
- The filename scheme (confirm UTC ISO + uptime).
- How they're handling the cpal/audio-thread thread-safety
  concern (should be a non-issue if `channel_snapshots` is
  already `Arc<ChannelSnapshots>` — verify).
