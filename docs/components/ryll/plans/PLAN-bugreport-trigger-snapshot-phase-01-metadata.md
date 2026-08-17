# Phase 1 — trigger-snapshot metadata plumbing

Parent: [PLAN-bugreport-trigger-snapshot.md](/components/ryll/plans/PLAN-bugreport-trigger-snapshot/).

## Goal

Thread trigger-time timestamps through the bug-report assembly
pipeline so `metadata.json` records **when the user opened the
dialog** in addition to **when the zip was written**. No
user-visible behaviour changes in this phase: every existing
call site passes `None` and `triggered_*` ends up equal to
submit time. Phase 2 is what actually splits the two.

This phase exists on its own so phase 2 (the cross-thread PNG
encode) can focus purely on snapshot capture without also
touching the metadata schema mid-flight.

## Scope

In scope:

- Two new fields in `ReportMetadata` (schema addition).
- A small `TriggerTimestamps` struct so call-site signatures
  stay readable.
- Threading `Option<TriggerTimestamps>` through
  `BugReport::new`, `BugReport::assemble`, and
  `BugReport::write_pedantic`.
- Updating the single app-side caller to pass `None` (phase 2
  replaces the `None` with a real value).
- Unit tests that prove the new fields land in the JSON with
  both `None` and `Some` inputs.

Out of scope (later phases):

- Actually capturing the surface at trigger time (phase 2).
- Region-crop secondary screenshot (phase 3).
- User-facing documentation (phase 4).

## Grounding — what's there today

`ReportMetadata` lives at
[ryll/src/bugreport.rs:674-688](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L674-L688)
and looks like:

```rust
pub struct ReportMetadata {
    pub ryll_version: String,
    pub platform_os: String,
    pub platform_arch: String,
    pub report_type: BugReportType,
    pub channel: String,
    pub description: String,
    pub region: Option<ReportRegion>,
    pub timestamp: String,          // ISO 8601 of zip write
    pub target_host: String,
    pub target_port: u16,
    pub session_uptime_secs: f64,   // session uptime at zip write
}
```

It's populated inside
[`BugReport::assemble`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L836-L848)
which is called from two public entry points:

- [`BugReport::new`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L715-L744) —
  user-triggered path, runs a 2-second metrics sample then
  delegates. Called from
  [`app.rs::generate_bug_report`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L940).
- [`BugReport::write_pedantic`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L913-L940)
  — gap-observer path, takes pre-sampled metrics because it
  runs from the observer closure. Called from the observer
  registered in
  [`register_pedantic_observer`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L1035).

Both call paths terminate in the same `assemble()`, so this
phase only has to change one struct, one function signature,
one JSON-writer block, and thread the values through two
wrappers.

## Design

### Timestamp struct

Add to `ryll/src/bugreport.rs`, near `ReportRegion`:

```rust
/// Captured at the moment a bug-report dialog opened, or at
/// the moment a --pedantic observer fired. Threaded through
/// `BugReport::new` / `BugReport::write_pedantic` so the
/// submitted zip can record when the user *saw* the bug in
/// addition to when they *submitted* the report.
///
/// Phase 1 plumbs this through with every caller passing
/// `None` (which falls back to submit time); phase 2 wires
/// up real trigger-time capture from the app.
#[derive(Debug, Clone)]
pub struct TriggerTimestamps {
    /// ISO 8601 UTC timestamp (same format as
    /// `ReportMetadata::timestamp`).
    pub triggered_at: String,
    /// Session uptime in seconds at the moment of trigger.
    pub triggered_uptime_secs: f64,
}
```

No `impl now()` helper in this phase — the "trigger = now"
fallback logic lives inline in `assemble()` so the callers
that do eventually supply a real value don't accidentally
shadow it with a fresh `now()` call.

### Schema change

Extend `ReportMetadata` with two always-present fields:

```rust
pub struct ReportMetadata {
    // ...existing fields unchanged...
    pub session_uptime_secs: f64,
    pub triggered_at: String,
    pub triggered_uptime_secs: f64,
}
```

`session_uptime_secs` stays as-is and keeps meaning "uptime at
submit" — this is the operator's "leaning" call from the
master plan's open question 1 so previously captured zips
still parse under any serde reader that ignores missing
fields. Analysts get the new fields on top rather than having
a renamed field disappear.

The two new fields are **never** JSON `null`. When the caller
passes `None`, `assemble()` substitutes the submit-time values
so downstream tooling can treat the fields as always present.

### Signature changes

```rust
impl BugReport {
    pub fn new(
        report_type: BugReportType,
        description: String,
        region: Option<ReportRegion>,
        target_host: &str,
        target_port: u16,
        traffic: &TrafficBuffers,
        channel_snapshots: &ChannelSnapshots,
        app_snapshot: &Mutex<AppSnapshot>,
        surface_pixels: Option<(&[u8], u32, u32)>,
        trigger: Option<TriggerTimestamps>,   // NEW
    ) -> anyhow::Result<Self> { ... }

    pub(crate) fn assemble(
        // ...existing params...
        runtime_metrics: RuntimeMetrics,
        trigger: Option<TriggerTimestamps>,   // NEW
    ) -> anyhow::Result<Self> { ... }

    pub fn write_pedantic(
        // ...existing params...
        runtime_metrics: RuntimeMetrics,
        trigger: Option<TriggerTimestamps>,   // NEW
    ) -> anyhow::Result<std::path::PathBuf> { ... }
}
```

Both `new` and `write_pedantic` already have
`#[allow(clippy::too_many_arguments)]`; no clippy allow needs
adding.

### `assemble()` behaviour

Inside `assemble()`, before building `ReportMetadata`:

```rust
let submit_iso = chrono_now();
let submit_uptime = session.uptime_secs;
let (triggered_at, triggered_uptime_secs) = match trigger {
    Some(t) => (t.triggered_at, t.triggered_uptime_secs),
    None => (submit_iso.clone(), submit_uptime),
};
```

Then populate `timestamp: submit_iso` and the two new fields
from the locals. One call to `chrono_now()` either way, so
the submit timestamp and the fallback trigger timestamp
always match byte-for-byte.

### Caller updates

- `app.rs::generate_bug_report` passes `None` for now. Phase
  2 replaces that with the real trigger value stashed on the
  app struct.
- `bugreport.rs::register_pedantic_observer` passes `None`.
  Observer fires synchronously on `register_key`, so
  trigger-time and submit-time are genuinely the same moment;
  `None` gives correct behaviour with no extra plumbing.
- Every existing unit-test call to `BugReport::assemble`
  passes `None` as the trigger. No behavioural changes in the
  tests other than the added parameter.

### Tests

1. **Extend `test_report_metadata_serialises`** to include
   the new fields in the constructed `ReportMetadata` and
   assert their JSON keys appear in the serialised output.
2. **New test
   `test_bug_report_assemble_defaults_trigger_to_submit`**:
   call `assemble(..., trigger: None)` and parse the resulting
   `metadata_json` with `serde_json::Value`; assert
   `triggered_at == timestamp` and
   `triggered_uptime_secs == session_uptime_secs`.
3. **New test
   `test_bug_report_assemble_propagates_explicit_trigger`**:
   call `assemble(..., trigger: Some(TriggerTimestamps {
   triggered_at: "2020-01-01T00:00:00Z", triggered_uptime_secs:
   1.5 }))` and assert both fields appear literally in the
   JSON and that `timestamp` / `session_uptime_secs` are
   distinct values.
4. **Extend `test_bug_report_runtime_metrics_in_zip`** or add
   a focused variant to confirm the zip's `metadata.json`
   round-trips through `ZipArchive` and `serde_json::Value`
   with both new fields present.

No integration test — phase 2's snapshot plumbing is where
end-to-end behaviour is worth validating.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none      | Implement everything in this phase as described in the *Design* section above. Add the `TriggerTimestamps` struct and the two new `ReportMetadata` fields. Thread `Option<TriggerTimestamps>` through `BugReport::new`, `BugReport::assemble`, and `BugReport::write_pedantic`. Apply the submit-time fallback inside `assemble()`. Update `app.rs::generate_bug_report` and the pedantic observer to pass `None`. Update all four affected call sites in existing tests to pass `None`, and add the three new tests listed above. Run `pre-commit run --all-files` and `make test` before handing back. Single commit with message `Record trigger and submit timestamps in bug-report metadata.`. Do not commit until the management session has reviewed. |

Medium effort because the change is well-scoped but touches
three public functions and five or six tests; a light brief
would risk missing a call site. Sonnet is sufficient — no
deep protocol or architectural reasoning. No worktree
isolation: the change is small and easy to abandon via a
plain revert if something goes wrong.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, with the three new tests present in the
  output.
* `metadata.json` in a freshly-captured bug report contains
  non-empty `triggered_at` and `triggered_uptime_secs` fields
  that equal `timestamp` and `session_uptime_secs` respectively
  (until phase 2 changes what the app passes).
* No existing field names changed — older zips' metadata
  still deserialise against the unchanged keys.

## Review checklist

- [ ] `ReportMetadata` has both new fields, always-present
      (not `Option<String>` / `Option<f64>`).
- [ ] `BugReport::new`, `assemble`, and `write_pedantic` all
      take the new `Option<TriggerTimestamps>` parameter.
- [ ] `app.rs::generate_bug_report` and the pedantic observer
      pass `None`.
- [ ] Single call to `chrono_now()` in `assemble()` when
      `trigger` is `None` — not one for `timestamp` and
      another for the fallback.
- [ ] The three new tests exist and pass.
- [ ] Existing assemble-test call sites updated to pass
      `None` and still pass.

## Follow-up

Phase 2 replaces the `None` passed from `generate_bug_report`
with a real `TriggerTimestamps` captured when the dialog
opens. Planning for phase 2 happens after phase 1 lands.
