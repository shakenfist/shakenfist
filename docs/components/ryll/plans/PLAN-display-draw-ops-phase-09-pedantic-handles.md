# Phase 9: Thread live bug-report handles to the pedantic observer

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns (SPICE
protocol handling, channel architecture, async task model,
bug-report flow, phase-8 pedantic wiring), and ground your
answers in what the code actually does today.

Key references for this phase:

* [ryll/src/main.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs) — phase-8e
  observer wiring block (look for "KNOWN LIMITATION").
  This is what we're moving.
* [ryll/src/app.rs:345](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L345) —
  `RyllApp::new` where live `TrafficBuffers`,
  `ChannelSnapshots`, and `app_snapshot` are built
  (lines 367-371 in the phase-8 state).
* [ryll/src/app.rs:2616](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2616) —
  `run_headless`, which builds its own live
  `TrafficBuffers`. Check where its equivalents for
  `ChannelSnapshots` and `app_snapshot` live (there may
  not be a full set — headless was never plumbed through
  the F8 bug-report path either, so a design decision
  is needed below).
* [ryll/src/bugreport.rs:525](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L525)
  — `ChannelSnapshots` struct definition. Fields are
  already `Arc<Mutex<..>>`, so deriving `Clone` is free.
* [ryll/src/bugreport.rs:901](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L901)
  — `BugReport::write_pedantic` signature (the helper
  phase 8b added; this phase wraps it in the observer
  factory).
* [shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs)
  — `register_gap_observer` with
  replay-on-late-registration. The tiny window between
  "session starts" and "observer registered" is covered
  by replay.

## Goal

Make `--pedantic` mode useful for post-hoc debugging:
each zip must contain the live traffic pcap and
channel-state snapshots at the moment the gap fired, not
an empty default. Gap-key metadata and session info are
already correct (phase 8); traffic + snapshots are what's
missing.

Why it matters: when a user files a pedantic report for,
say, `display:decode_failure:glz:decompress_failed`, the
gap key alone tells us *which* decoder failed, but the
traffic pcap tells us *which messages* it failed on — the
exact image-bearing DRAW_COPY payload that the GLZ
decompressor choked on. Without the pcap the report is
a lookup key into a haystack we don't have.

By the end of this phase:

* `ChannelSnapshots` is `Clone`.
* A new `BugReport::register_pedantic_observer(...)`
  helper in `bugreport.rs` owns the observer-closure
  construction — both the GUI and headless paths call it.
* Observer registration moves from `main.rs` into
  `app::RyllApp::new` (GUI) and `app::run_headless`
  (headless), after the live handles are built.
  `register_gap_observer`'s replay semantics cover any
  gaps that fired during the construction window.
* `main.rs` keeps the `mkdir -p` eager-failure check and
  the `Option<PedanticConfig>` plumbing — but drops the
  observer-closure block entirely.
* The KNOWN LIMITATION comment in `main.rs` is deleted.
* Master plan's future-work entry for this item is
  already removed (done in the master-plan edit that
  added phase 9).

## Non-goals

* Changing the shape of pedantic zips beyond now
  containing real traffic + snapshots. Filename
  convention, metadata.json contents, directory cap of
  50 — all unchanged.
* Refactoring `RyllApp::new` constructor beyond the
  observer-registration insertion. If threading the
  handles requires meaningful changes to how other
  subsystems reach them, flag it and stop.
* Any change to phase-8's decode-failure keys / status-
  bar widget / CLI flags.
* Plumbing F8 bug-reports through `run_headless` (it
  doesn't have the GUI path, and there's no clear F8-
  equivalent trigger). For headless, --pedantic is still
  the only way reports get written. That's fine: the
  pedantic observer registers regardless of interactive
  vs headless.

## Current state

Phase 8e put the observer registration in `main.rs` at
around line 123, inside a block that runs if
`args.pedantic`. Inside that block:

* `let traffic = Arc::new(TrafficBuffers::new());` ← STUB
* `let channel_snapshots = Arc::new(ChannelSnapshots::new());` ← STUB
* `let app_snapshot = Arc::new(Mutex::new(AppSnapshot::default()));` ← STUB

These three are *not* the handles that channel tasks
write to. The real handles are constructed independently
inside `RyllApp::new` (GUI) at lines 367-371 and inside
`run_headless` at line 2616 (and elsewhere nearby, if
the headless path also builds snapshots).

The phase-8e commit acknowledges this with a prominent
"KNOWN LIMITATION" comment; this phase removes the
stubs and the comment together.

## Implementation shape

### 1. `Clone` on `ChannelSnapshots`

In
[ryll/src/bugreport.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs):

```rust
#[derive(Clone)]
pub struct ChannelSnapshots {
    pub display: Arc<Mutex<DisplaySnapshot>>,
    pub inputs: Arc<Mutex<InputsSnapshot>>,
    pub cursor: Arc<Mutex<CursorSnapshot>>,
    pub main: Arc<Mutex<MainSnapshot>>,
}
```

Each field is already an `Arc<Mutex<..>>` so the
derive-generated clone is just four Arc clones. No
semantic change. Callers that previously did
`channel_snapshots.display.clone()` field-by-field keep
working.

### 2. `PedanticConfig` and the observer-factory helper

Add to
[ryll/src/bugreport.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs):

```rust
/// Configuration bundle passed from main.rs into the
/// app constructors when --pedantic is enabled.
#[derive(Debug, Clone)]
pub struct PedanticConfig {
    pub dir: std::path::PathBuf,
}

/// Cap on the number of pedantic reports per session.
/// Prevents disk-fill if the dedupe key set explodes for
/// some reason.
pub const PEDANTIC_REPORT_CAP: usize = 50;
```

And a new associated function on `BugReport`:

```rust
/// Build the --pedantic observer closure and register it
/// with the warn_once registry. Called from
/// `app::RyllApp::new` (GUI) and `app::run_headless`
/// after the live handles are built; rely on
/// `register_gap_observer`'s replay semantics to cover
/// the window between session start and observer
/// registration (empirically empty on the current code
/// paths; replay catches anything that slips in).
///
/// Spawns a tokio task per new gap so the firing thread
/// (usually a channel task) never blocks on disk I/O or
/// metrics sampling.
pub fn register_pedantic_observer(
    config: PedanticConfig,
    target_host: String,
    target_port: u16,
    traffic: Arc<TrafficBuffers>,
    channel_snapshots: ChannelSnapshots,  // cheap Clone (4 Arcs)
    app_snapshot: Arc<Mutex<AppSnapshot>>,
) {
    let dir = Arc::new(config.dir);
    let host = Arc::new(target_host);
    let counter = Arc::new(AtomicUsize::new(0));

    shakenfist_spice_protocol::logging::register_gap_observer(Arc::new(
        move |key: &'static str| {
            let n = counter.fetch_add(1, Ordering::SeqCst);
            if n >= PEDANTIC_REPORT_CAP {
                return;
            }
            let dir = dir.clone();
            let host = host.clone();
            let traffic = traffic.clone();
            let snaps = channel_snapshots.clone();
            let app_snap = app_snapshot.clone();
            let key_str = key.to_string();
            tokio::spawn(async move {
                let metrics = tokio::task::spawn_blocking(|| {
                    crate::metrics::sample(std::time::Duration::from_secs(1))
                })
                .await
                .unwrap_or_else(|_| {
                    crate::metrics::RuntimeMetrics::unavailable(
                        "spawn_blocking panicked during metrics sample",
                    )
                });
                match BugReport::write_pedantic(
                    &dir,
                    &key_str,
                    &host,
                    target_port,
                    &traffic,
                    &snaps,
                    &app_snap,
                    metrics,
                ) {
                    Ok(path) => tracing::info!("pedantic: wrote {}", path.display()),
                    Err(e) => tracing::warn!("pedantic: write failed for {}: {}", key_str, e),
                }
            });
        },
    ));

    tracing::info!("pedantic mode enabled; reports will land in {}", dir.display());
}
```

Exact imports and path shapes are a sub-agent concern
— mirror the phase-8e block in `main.rs` which is
already correct modulo the stub handles.

### 3. Call the factory from `app::RyllApp::new`

In
[ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) `RyllApp::new`,
after the live handles exist (around line 371) and
before the struct literal return, insert:

```rust
if let Some(config) = pedantic_config {
    BugReport::register_pedantic_observer(
        config,
        target_host.clone(),
        target_port,
        traffic.clone(),
        channel_snapshots.clone(),
        app_snapshot.clone(),
    );
}
```

The `pedantic_config` parameter is a new
`Option<PedanticConfig>` that `RyllApp::new` gains. Its
call site in main.rs passes the config through.

### 4. Call the factory from `run_headless`

In
[ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) `run_headless`,
around line 2616 after `TrafficBuffers` is built:

The headless path may not currently build full
`ChannelSnapshots` / `AppSnapshot` objects — the F8 flow
never exercised headless. Options:

a. **Build them for pedantic's sake.** Construct
   `ChannelSnapshots::new()` and
   `AppSnapshot::default()` inside `run_headless`, wire
   to channel tasks the same way `RyllApp::new` does
   (the channels don't care whether they're writing to
   a GUI-owned or headless-owned set of snapshot
   Arcs). Then register the pedantic observer.

b. **Register with stubs for headless.** Headless's
   snapshots would be fresh empties (same problem
   phase 8e had, just localised to headless). Flag
   clearly.

c. **Disable pedantic in headless.** User-hostile —
   headless is exactly the long-running unattended
   mode where pedantic reports are most valuable.

Recommend **option (a)**. The channel handlers already
accept snapshot Arcs via their constructors; headless
just needs to build the same Arcs, plumb them through,
and pass them to the pedantic factory. If a sub-agent
finds this needs more than ~30 lines of plumbing in
`run_headless`, fall back to option (b) with a warning
log on startup ("pedantic mode in headless currently
captures gap_key and session info only — traffic pcap
is empty; see PLAN-display-draw-ops-phase-09...").

### 5. Update `main.rs`

Strip the phase-8e observer-closure block. Keep:

```rust
let pedantic_config = if args.pedantic {
    std::fs::create_dir_all(&args.pedantic_dir).with_context(|| {
        format!("failed to create pedantic directory {}", args.pedantic_dir.display())
    })?;
    Some(PedanticConfig { dir: args.pedantic_dir.clone() })
} else {
    None
};
```

Thread `pedantic_config` through to both `run_gui`
(added parameter) and `run_headless` (added
parameter).

Remove the KNOWN LIMITATION comment, the stub
constructors, and the phase-8e `register_gap_observer`
call entirely.

### 6. Verification

The phase-8e tests (BugReport::write_pedantic unit test)
still cover the write-the-zip path. No new unit tests
are strictly required — the change here is *where* the
observer is registered, not *what* it does.

Manual check: run ryll with `--pedantic` against a
guest that exercises an unknown opcode or a GLZ decode
failure. Unzip the resulting report. Confirm:
* `metadata.json` contains gap_key and session info
  (already worked in phase 8).
* `traffic.pcap` is non-empty (phase 9 fix).
* `channel-state.json` has real field values, not
  defaults (phase 9 fix).

## Files touched

| File | Change |
|------|--------|
| `ryll/src/bugreport.rs` | Derive `Clone` on `ChannelSnapshots`. Add `PedanticConfig` struct, `PEDANTIC_REPORT_CAP` constant, `BugReport::register_pedantic_observer(...)` factory method. |
| `ryll/src/main.rs` | Replace the phase-8e observer block with a `PedanticConfig`-builder. Thread `Option<PedanticConfig>` into `run_gui` and `run_headless`. Delete KNOWN LIMITATION comment. |
| `ryll/src/app.rs` | `RyllApp::new` gains `pedantic_config: Option<PedanticConfig>`, registers the observer after live handles are built. `run_headless` gains the same parameter + (option a) builds live `ChannelSnapshots` / `AppSnapshot` and registers, or (option b fallback) registers with stubs + warns. |

## Open questions

1. **Headless fallback**: option (a) full live handles
   or option (b) stub + warn. Plan recommends (a) but
   flags (b) as acceptable fallback if the refactor
   is invasive. Sub-agent to report which it took.

2. **Exposure of `PedanticConfig` / `PEDANTIC_REPORT_CAP`
   as public API**: they live in `bugreport.rs` and
   are called from `main.rs` + `app.rs`. Recommend
   `pub(crate)` since nothing outside the ryll binary
   needs them. If a future test harness wants to
   exercise pedantic mode, can be promoted to `pub`.

3. **Dir stored as `Arc<PathBuf>` vs `PathBuf` cloned
   per invocation**: phase 8e used `Arc<PathBuf>`
   inside the closure. Keep that pattern — it avoids
   per-gap PathBuf clones. Paths are short strings but
   there's no reason to clone them.

4. **Replay double-fire during observer registration.**
   The phase-8a observer hook documents that a key
   firing *during* registration may fire the observer
   twice (once via dispatch, once via replay). For
   pedantic mode this would mean two zips for the same
   gap. Should we dedupe inside the closure (track
   written keys in a HashSet)? Recommend no: the
   dispatch-vs-replay window is microsecond-scale and
   this is a rare edge case. If it bites in practice,
   add a HashSet<String> in the closure state. Not
   worth the complexity for phase 9.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | sonnet | none | In `ryll/src/bugreport.rs`: derive `Clone` on `ChannelSnapshots`; add `pub(crate) struct PedanticConfig { pub dir: PathBuf }`; add `pub(crate) const PEDANTIC_REPORT_CAP: usize = 50;`; add `impl BugReport { pub(crate) fn register_pedantic_observer(config, target_host, target_port, traffic, channel_snapshots, app_snapshot) { ... } }` per the "PedanticConfig and the observer-factory helper" section. The closure body is lifted verbatim from the current phase-8e block in main.rs. Do NOT touch main.rs or app.rs yet. |
| 9b | high   | opus   | none | Move observer registration out of `main.rs` into `app.rs`. Specifically: (i) In main.rs, replace the phase-8e block with a `pedantic_config: Option<PedanticConfig>` build (mkdir stays for eager-failure), then thread that Option through to `run_gui` and `run_headless` as a new parameter. Delete the KNOWN LIMITATION comment, the stub handles, and the phase-8e `register_gap_observer` call entirely. (ii) In `RyllApp::new`, add `pedantic_config: Option<PedanticConfig>` parameter. After the existing live handle construction (~line 371), if `Some(config)`, call `BugReport::register_pedantic_observer(config, target_host.clone(), target_port, traffic.clone(), channel_snapshots.clone(), app_snapshot.clone())`. (iii) In `run_headless`, add the same parameter and the same call site. For the snapshots required by the observer, prefer **option (a)**: build live `ChannelSnapshots` / `Arc<Mutex<AppSnapshot>>` inside run_headless, the same way RyllApp::new does. If that requires more than ~30 lines of plumbing to pass the snapshot Arcs into the channel tasks, fall back to **option (b)**: register the observer with `ChannelSnapshots::new()` + fresh `AppSnapshot` and emit a `warn!` log at pedantic-enable time explaining the headless limitation. Report which option you took. |
| 9c | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`. If rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results; do not fix other failures. |

Sequencing: 9a → 9b → 9c. 9b depends on 9a's factory
helper.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All existing tests unchanged.
* The phrase "KNOWN LIMITATION" no longer appears in
  main.rs.
* Master plan's Future-Work list no longer mentions
  threading live handles to pedantic (already removed).
* Manual check: `ryll --pedantic` against a guest that
  fires a gap produces a zip whose `traffic.pcap` is
  non-empty and whose `channel-state.json` contains
  real field values. Gap key, filename, session
  metadata unchanged from phase 8.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
