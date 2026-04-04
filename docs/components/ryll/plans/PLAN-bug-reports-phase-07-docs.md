# Phase 7: Documentation and testing

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

## Goal

Final documentation polish and additional test coverage for
the bug report feature.  Each earlier phase updated docs
incrementally, so the main tasks here are:

1. Add a dedicated "Bug Reports" section to
   `docs/troubleshooting.md` explaining how to use the
   feature.
2. Add keyboard shortcuts (F11, F12) to
   `docs/configuration.md`.
3. Consolidate the README feature list to be concise and
   accurate.
4. Add missing unit tests for edge cases.
5. Final review pass over ARCHITECTURE.md and AGENTS.md
   for accuracy.

At the end of this phase the bug report feature is fully
documented and tested.

## Design

### Documentation gaps

Phases 1-6 updated ARCHITECTURE.md, AGENTS.md, and
README.md incrementally.  The following gaps remain:

1. **docs/troubleshooting.md** — no mention of bug reports.
   Users encountering display corruption or input issues
   should be told to press F12 and generate a report.

2. **docs/configuration.md** — no mention of F11/F12
   keyboard shortcuts.  These are not CLI options but are
   runtime shortcuts that belong in a "Keyboard Shortcuts"
   section.

3. **README.md** — the feature list accumulated individual
   bullet points for ring buffer, snapshots, dialog, and
   traffic viewer.  These should be consolidated into two
   clear bullets: one for bug reports, one for the traffic
   viewer.

4. **ARCHITECTURE.md** — review all bug report sections for
   accuracy against the final code.  Remove any references
   to "Phase N" or "later phases" that are no longer future.

5. **AGENTS.md** — review the code organisation tree and
   dependencies table for completeness.

### Test gaps

The existing 22 tests cover:

- Ring buffer push/eviction (1 test)
- Traffic buffer routing (2 tests)
- Snapshot serialisation for all 5 types (5 tests)
- Channel snapshots construction (1 test)
- Chrono/timestamp utilities (2 tests)
- PNG encoding (1 test)
- Report metadata serialisation (1 test)
- Bug report zip assembly for display + input (2 tests)
- BugReportType channel name mapping (1 test)
- Decompression (2 tests, pre-existing)
- Cursor decoding (4 tests, pre-existing)

Missing tests:

1. **Traffic viewer entries** — `recent_view_entries()`
   returns merged, sorted entries from multiple channels
   and respects the max limit.

2. **Ring buffer byte cap** — verify that `total_bytes()`
   never exceeds `max_bytes` after a push that triggers
   eviction (the existing test checks `<=` but a tighter
   test with exact sizes is useful).

3. **Bug report for Cursor and Connection types** — only
   Display and Input are tested.  Add tests for the
   remaining two types to verify the correct snapshot is
   included.

4. **encode_png dimensions** — verify that encoding a 1x1
   image works (minimum dimensions) and that a 0x0 image
   returns an error.

5. **format_size helper** — verify the human-readable size
   formatting (bytes, KB, MB).

6. **drain_channel_pcap_bytes** — verify that draining an
   unknown channel returns `None`.

## Steps

### Step 1: Add bug report section to troubleshooting.md

Add a "Bug Reports" section between "Display Issues" and
"Input Issues" (or at the end before "Getting Help"):

- Explain when to use bug reports (display corruption,
  input not working, unexpected behaviour).
- Step-by-step: press F12, select type, enter description,
  click Capture.
- For display bugs: explain the region selection step.
- Explain where the zip file is saved (capture dir or cwd).
- List what the zip contains.
- Mention F11 traffic viewer for live debugging.

### Step 2: Add keyboard shortcuts to configuration.md

Add a "Keyboard Shortcuts" section:

| Shortcut | Action |
|----------|--------|
| F11 | Toggle live traffic viewer |
| F12 | Open/close bug report dialog |
| Escape | Close dialog or skip region selection |

Note that F11 and F12 are consumed by ryll and not sent to
the guest VM.

### Step 3: Consolidate README feature list

Replace the separate ring buffer, snapshot, dialog, and
traffic viewer bullets with:

- **Bug reports** — Press F12 or click "Report" to capture
  a self-contained zip with metadata, channel state, pcap
  traffic, and screenshots.  Display reports include
  interactive region selection.
- **Live traffic viewer** — Press F11 or click "Traffic" for
  a real-time colour-coded feed of SPICE protocol messages
  with per-channel filters.

### Step 4: Review ARCHITECTURE.md

Read all bug report sections and:
- Remove `#[allow(dead_code)]` references or "Phase N"
  language.
- Verify struct names and method names match the code.
- Ensure the zip file structure listing is accurate.

### Step 5: Review AGENTS.md

- Verify the code organisation tree matches the actual
  module descriptions.
- Verify the dependencies table includes zip, png, serde,
  serde_json.

### Step 6: Add missing unit tests

Add to `bugreport.rs` tests:

1. `test_recent_view_entries` — push entries to multiple
   channels, call `recent_view_entries()`, verify merged
   sort order and max limit.
2. `test_bug_report_assemble_cursor` — assemble a Cursor
   report, verify zip contains expected files.
3. `test_bug_report_assemble_connection` — assemble a
   Connection report.
4. `test_drain_unknown_channel` — verify
   `drain_channel_pcap_bytes("nonexistent")` returns None.

Add to `app.rs` (or keep in `bugreport.rs`):

5. `test_format_size` — verify format_size() output for
   0, 500, 15000, 2500000.
6. `test_encode_png_1x1` — encode a 1x1 RGBA pixel.

### Step 7: Build and validate

1. `pre-commit run --all-files` must pass.
2. `make build` must succeed.
3. `make test` — all tests pass.

### Step 8: Mark Phase 7 complete in master plan

Update `PLAN-bug-reports.md` to mark Phase 7 as Complete.

## Administration and logistics

### Success criteria

* `docs/troubleshooting.md` has a bug report section.
* `docs/configuration.md` lists F11/F12 shortcuts.
* README feature list is consolidated and accurate.
* ARCHITECTURE.md has no stale "Phase N" references.
* AGENTS.md matches the current code.
* At least 6 new tests are added and all pass.
* `pre-commit run --all-files` passes.
* `make build` succeeds on the first attempt.
* All phases in the master plan are marked Complete.

### Risks

* **Documentation accuracy**: The docs describe behaviour
  that can only be fully verified with a live SPICE server.
  The descriptions are based on reading the code, which is
  the best we can do without a running server.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
