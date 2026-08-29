# Idle CPU reduction and real latency measurement

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

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources.

## Situation

Two related issues observed during interactive use of ryll
on branch `screenshot`:

### Issue 1 — Ryll consumes ~6 CPU cores at idle

Just sitting connected to a SPICE session with nothing
happening, ryll burns roughly 6 cores. This is unacceptable
for a release. Initial investigation points to two likely
contributors, neither yet confirmed as the dominant cause:

1. **Forced 60 FPS repaint.** [ryll/src/app.rs:2169](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2169)
   calls `ctx.request_repaint_after(Duration::from_millis(16))`
   unconditionally at the end of every `update()`. egui then
   re-renders the entire UI (including all sparklines, the
   surface texture, etc.) 60 times a second whether anything
   changed or not.

2. **INFO-level logging on every protocol message.** Every
   channel logs every message it sends or receives via
   `shakenfist_spice_protocol::logging::log_message`
   ([logging.rs:22-32](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs#L22-L32))
   at `info!` level. Each line allocates a
   `format_timestamp()` String, takes the global tracing
   subscriber lock, formats the line, and writes to
   `/tmp/ryll.log`. PINGs alone arrive multiple times per
   second on every channel; the inputs/cursor channels add
   per-event traffic. This was the symptom that originally
   prompted PLAN-remaining-issues item 3 ("Verbose logging
   cleanup"), and many but not all sites have already been
   demoted.

3. **Channel read loops.** Each channel has its own tokio
   task with a `tokio::select!` loop. If any of those select
   arms wakes spuriously or yields without backoff, the
   task burns CPU. Worth profiling, not assumed.

The evidence visible to the user is log output like:

```
2026-04-19T20:23:43.871004Z INFO [1776630223.870958] playback received 12 byte opcode 4 ping
2026-04-19T20:23:43.871080Z INFO [1776630223.871078] playback sent 12 byte opcode 3 pong
2026-04-19T20:23:43.871153Z INFO [1776630223.871151] playback received 12 byte opcode 4 ping
2026-04-19T20:23:43.871192Z INFO [1776630223.871192] playback sent 12 byte opcode 3 pong
```

Two timestamps per line — `2026-04-19T20:23:43.871004Z` is
added by `tracing-subscriber`, `[1776630223.870958]` is
added inside `log_message` itself. Redundant.

### Issue 2 — Latency measurement is broken

The current latency code at
[channels/inputs.rs:341-346](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L341-L346)
sets `last_key_time = Some(Instant::now())` and on the very
next line reads `last_key_time.unwrap().elapsed()`. That
measures essentially zero (microseconds at most). The
`Latency: Xms` label and the latency sparkline added on
this branch consequently always read zero.

The naive fix — measure round-trip from keystroke to next
display update — is unsound: a "screen update" right after
a keystroke might be a clock tick, an MJPEG video stream
frame, a cursor blink, or anything else unrelated. There is
no way to tell whether a given draw_copy was caused by the
keystroke we just sent.

**Better approach found by protocol research:** SPICE has a
built-in PING/PONG mechanism on every channel.

- `SPICE_MSG_PING = 4` (server→client) and
  `SPICE_MSGC_PONG = 3` (client→server) are base-channel
  messages, mandatory since v1, no capability negotiation.
- Payload: `uint32 id` + `uint64 timestamp` (sender's
  monotonic ns). PONG echoes both fields verbatim — perfect
  for unambiguous correlation.
- Direction is asymmetric: there is no `SPICE_MSGC_PING`,
  so ryll cannot originate probes. The server's own
  per-channel ping timer (`red-channel-client.cpp` in
  spice-server) is the source. spice-gtk also only
  responds, never originates.
- Constants and the message struct already exist in this
  codebase: `shakenfist-spice-protocol/src/constants.rs`
  (lines 141, 158, plus per-channel duplicates) and
  `shakenfist-spice-protocol/src/messages.rs:78-108`. The
  main-channel handler at
  `ryll/src/channels/main_channel.rs:378-400` already
  parses PING and emits PONG — it just doesn't measure
  anything.
- Kerbside passes PING/PONG through unchanged
  (kerbside/docs/channel-protocols.md:538-554).

So the latency story is: the server is *already* sending us
the probe data; we just throw it away. We need to record
`Instant::now()` when a PING arrives, optionally compare
against the server timestamp, and emit a real
`ChannelEvent::Latency` event from there. This gives
**network RTT + client receive-loop turnaround**, not the
human-perceived input-to-display latency, but it's a real
number and it's the same metric spice-server itself uses
internally.

True input-to-display latency would require a different
mechanism (e.g. a bug-report flow where the user marks a
known glyph appearing on screen, or an instrumented test
guest). Out of scope for this plan.

## Mission and problem statement

1. **Get idle CPU down to a reasonable level.** Target:
   under 10% of one core when connected to an idle SPICE
   session, with no user input and no display updates. We
   should profile rather than guess: confirm the dominant
   contributor, fix it, measure again.

2. **Fix the latency measurement** by tapping the existing
   PING handlers, recording timestamps, and emitting a real
   `ChannelEvent::Latency` from there. Remove the broken
   keystroke-based code.

3. **Reduce log noise** as a CPU-and-readability win. PING
   and PONG logs in particular should be `debug!`, not
   `info!`. Drop the embedded `[unix_ts]` timestamp from
   `log_message` since `tracing-subscriber` already adds
   one.

## Open questions

1. **Is the 60 FPS forced repaint actually necessary?** The
   `request_repaint_after(16ms)` at app.rs:2169 looks like
   a workaround for some egui interaction where state
   changed but didn't trigger a repaint. We should
   investigate whether replacing it with event-driven
   repaints (call `ctx.request_repaint()` from the channel
   event handler when a frame, cursor move, or status
   change actually arrives) is sufficient. The bandwidth
   sparkline ticks once per second — that needs a repaint
   at 1 Hz, not 60 Hz. Recommendation: try event-driven +
   1 Hz fallback; benchmark.

2. **Should we measure latency on all channels or just
   one?** Server sends PING per-channel. Latency
   characteristics may differ (display channel has bigger
   buffers; inputs is sparser). Recommendation: measure on
   the main channel only for the status bar number, but
   record per-channel latency in the bug-report snapshot
   for diagnostic value. If main is too sparse, fall back
   to display.

3. **Drop the embedded timestamp from `log_message`?**
   Yes — but it changes the format of every protocol log
   line, which may break grep patterns in
   `tools/replay_*.py` or any external log analysis. Need
   to grep for consumers before changing.

4. **Should we add a CLI flag to enable verbose protocol
   logging on demand?** With everything demoted to debug,
   `-v` already enables it via the existing verbose mode
   ([app.rs:settings::is_verbose()] check sites). No new
   flag needed.

5. **Is there a smarter idle strategy than slowing repaint
   cadence?** egui supports `ctx.request_repaint_after`
   with a longer duration when nothing's changed. We could
   track "frames since last meaningful event" and back off
   to 1 Hz after, say, 250 ms of nothing. That gives snappy
   interactive feel and low idle CPU.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Profile idle CPU | [PLAN-idle-cpu-and-latency-phase-01-profile.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-01-profile/) | Complete | 6d52665 |
| 2. Repaint cadence fix | [PLAN-idle-cpu-and-latency-phase-02-repaint.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-02-repaint/) | Complete | 6d52665 |
| 3. Demote protocol logging | [PLAN-idle-cpu-and-latency-phase-03-logging.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-03-logging/) | Complete | 6d52665 |
| 4. Real latency from PING/PONG | [PLAN-idle-cpu-and-latency-phase-04-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-04-latency/) | Complete | 6d52665 |
| 5. Capture runtime metrics in bug reports | [PLAN-idle-cpu-and-latency-phase-05-metrics.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-05-metrics/) | Complete | 6d52665 |
| 6. Push audit | [PLAN-idle-cpu-and-latency-phase-06-push-audit.md](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-06-push-audit/) | Complete | bf7a16f, with the findings fixed in 6fecb50 and 192265f |

Phase 1 informs phase 2: if profiling shows logging is the
dominant cost, swap their order.  *Profiling result: the
unconditional 60 Hz repaint at app.rs:2169 drives ~6 of
the 6.24 idle cores via llvmpipe rasteriser threads;
logging is a rounding error.  Phase 2 is the only fix that
moves the CPU needle.  Phase 3 is still worth doing for log
readability and a missing is_verbose() guard on the
playback channel.*  Phase 4 is independent of 1-3 but
should land last so its sparkline data is visible once the
other CPU-eating problems are fixed.

Phase 2 was held at `In progress` for four months after its
code landed, because the CPU reduction it is supposed to
deliver had not been confirmed on the reporter's machine.
*Verified 2026-08-27 on that machine: 2.80% of one core
idle, against the 6.24-core baseline, with a synthetic
pointer sweep taking the process to 269.5% and back to
prove egui still wakes on input.*  The numbers and the
method are in the phase 2 plan under *Step 2b result*.

Phase 5 was added after the original incident: had ryll
captured its own per-thread CPU into bug reports, the user
report would have included the llvmpipe breakdown directly
and the profiling phase would have been unnecessary.  This
phase makes future incidents self-debugging.

Phase 6 is the pre-push audit, and it runs last.  It works
`PUSH-AUDIT.md` over the accumulated diff of phases 1-5
against `develop`, not the last phase's diff alone, so it
sees what the repaint, logging, latency and metrics changes
did to each other.  Findings land as their own PR against
`develop`, recorded here under an *Items deferred from the
push audit* heading — the shape `PLAN-web-frontend.md` uses
for its *Items deferred from the post-Phase-N pre-push audit*
sections, minus the phase number, because this phase audits
the whole plan rather than a range of it.  This plan is not
complete until every one is fixed or declined in writing.  If
the audit finds nothing, that gets recorded here in one
sentence.

Phases 1-5 have already merged, so `develop...HEAD` is empty
on the audit branch and the accumulated diff has to be
assembled from where those phases landed.  The `Merged`
column above now records it, reconstructed 2026-08-27, and
the answer is not the one this paragraph assumed: all five
phases landed in a *single* merge, PR #36 (`6d52665`), not
one merge each.  `PUSH-AUDIT.md` cites this plan as its
worked example of a range that cannot be derived after the
fact, and on the per-phase question it is right — but the
plan's commits turn out to run contiguously on the
`screenshot` branch, from `90a954b` (the master plan) to
`1c28d6f`, so the audit range is exactly
`AUDIT_BASE=90a954b^1`, `AUDIT_HEAD=1c28d6f`.

Two things that range does not cover, and phase 6 must
handle explicitly.  `85bc901` ("Address automated reviewer
feedback on PR #36") sits above `AUDIT_HEAD` and may touch
this plan's code, so it has to be inspected separately.
And PR #36 also carried the screenshot-and-latency-HUD
plan's work, which sits *below* `AUDIT_BASE` — excluded by
the bound, but a reminder that the merge diff of `6d52665`
is the wrong patch to hand the judgment agents.

The larger caveat is age.  These commits are from April; the
crate extraction has since moved most of this code into
`shakenfist-spice-renderer`, and the web frontend was built
on the same event path.  Findings drawn from the April diff
have to be re-checked against current `develop` before they
are actionable.  *Two ways this runbook is invoked* in
`PUSH-AUDIT.md` has the rest.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session.

### Planning effort

Phase 1 (profiling) is **medium effort** — the work is
mechanical (run perf/flamegraph, read output) but the
interpretation requires judgment.

Phase 2 (repaint) is **medium-to-high effort** — depends on
egui internals and risks regressing interactivity.

Phase 3 (logging) is **low effort** — search-and-demote.

Phase 4 (latency) is **medium effort** — well-scoped given
the protocol research above, but touches multiple channels
and needs care around PING handler placement.

### Step-level guidance

Per phase. Default model recommendation: **sonnet** for
phases 1, 3, 4; **opus** for phase 2 (egui repaint logic
is subtle).

### Management session review checklist

After each phase:

- [ ] CPU benchmark before/after (for phases 1, 2, 3).
- [ ] `pre-commit run --all-files` passes.
- [ ] `make test` passes.
- [ ] Manual smoke against `make test-qemu`: connect, type,
      mouse, disconnect — nothing regresses.

## Administration and logistics

### Success criteria

Checked 2026-08-27, at the point phase 2 closed.

* **Met.**  Idle CPU under 10% of one core (measured:
  connected, no input, no display activity).  2.80%, with
  the pointer over the window rather than outside it.  See
  *Step 2b result* in the phase 2 plan.
* **Met, but this criterion was mis-stated.**  The
  `Latency: Xms` label and sparkline show real non-zero
  numbers.  They are *not* a PING round-trip, and could
  never have been: `SPICE_MSG_PING` is server→client only,
  so the client has no probe to time a round trip with, a
  point this plan's own *Situation* section makes and its
  success criterion then forgot.  What shipped is the
  interval between consecutive server PINGs
  (`main_channel.rs`, the `main_server::PING` arm), which
  `docs/diagnostics.md` documents accurately.  The
  criterion is corrected here rather than left to read as
  though something else was delivered.
* **Met.**  Protocol logs at `debug!` level by default,
  visible with `-v`; the embedded `[unix_ts]` is gone
  (`shakenfist-spice-protocol/src/logging.rs:251`).
* **Met.**  `pre-commit run --all-files` and `make test`
  both pass.
* **Met, in a different file.**  The criterion names
  `README.md`'s verbose-logging section; the README-as-pitch
  restructure since moved that material out, and `-v` is now
  documented in `docs/configuration.md:79` and `:255`, with
  usage in `docs/troubleshooting.md:343`.  Again the
  intended shape, not a miss.
* **Met, in a different file.**  The criterion names
  `ARCHITECTURE.md`'s "Statistics and Instrumentation"
  section; the doc-discipline restructure since moved that
  material to `docs/diagnostics.md`, where the PING-based
  latency source is described at line 18.  `ARCHITECTURE.md`
  indexes it.  That is the intended shape, not a miss.
* **Met.**  Bug-report ZIPs carry `runtime-metrics.json`
  with process CPU%, per-thread CPU and names, and RSS
  (`bugreport.rs`, `shakenfist_spice_renderer::metrics`;
  covered by `test_bug_report_runtime_metrics_in_zip`).
  Linux-first, with an `unavailable` variant elsewhere.

One observation for phase 6 rather than a criterion: on the
loopback test guest the label read `Latency: 0.1ms`, which
is a gap *within* a server ping burst, not a steady-state
cadence — phase 1 recorded sf-3 sending "a burst of 2 pings
at connect time then going quiet".  A statistic whose value
depends on whether the server happens to be bursting is
worth a look before release.  Recorded, not fixed: it is
phase 4's design, not phase 2's, and changing it is outside
what closing phase 2 authorises.

### Items deferred from the push audit

Phase 6 produced 22 findings.  Every one is fixed or
declined below, which is what the phase's definition of
done requires.  The findings table, with current locations
and triage status, is in
[the phase 6 plan](/components/ryll/plans/PLAN-idle-cpu-and-latency-phase-06-push-audit/).

**Fixed in #325.**

* **T1** — `test-audit-range.sh` inherited `AUDIT_BASE` /
  `AUDIT_HEAD`, so following `PUSH-AUDIT.md` failed wave 1.
  Landed in #325.
* **T5** — wave 1's fatal `println!` check scanned four
  hardcoded crates and had stopped seeing 46% of the
  workspace.  Landed in #325, along with the test-region
  filter it depended on.
* **T4** — the renderer watchdog's `eprintln!` calls are
  deliberate (the tracing subscriber cannot be trusted when
  the main thread is wedged) and now carry an
  `audit-allow-println` marker.  Review of #325 pointed out
  that the marker exempts a whole file, so on
  `main_channel.rs` it blinded the fatal print check to the
  largest file in the crate the same PR exists to make
  visible.  The watchdog moved to
  `channels/watchdog.rs`, scoping the exemption from 1934
  lines to about 130.  Landed in #325.
**Fixed in #327**, except T2 and T3, which folded into
#325 because it already owned that file.

* **T2, T3** — the unguarded-`log_message` check greps a
  directory the crate extraction deleted, and keys on the
  `is_verbose()` convention all seven channels abandoned.
  Repointed at the renderer's channels and rewritten
  against `log_config.verbose`.  The heuristic itself was
  also inverted -- it cleared its flag on a guard and
  re-set it on the `log_message` line that followed, so a
  guard *above* a call, which is every guard in the
  codebase, never counted -- and it reported at most one
  site.  Both fixed.
* **D1** — the 2 s `thread::sleep` in `BugReport::new` ran
  on the egui UI thread.  Moved off it, and the comment
  claiming the path is "gated on a file dialog" is deleted:
  it was never true for `generate_bug_report`, which
  resolves `--bug-report-dir`, `<capture>/bug-reports/`, or
  `current_dir()`.

  A note for anyone reading the finding rather than the
  fix: the audit recommended `tokio::task::spawn_blocking`,
  copying the two call sites that already use it.  That
  would panic.  `eframe::run_native` is called outside any
  tokio runtime, and every runtime in the GUI build is
  created inside a `std::thread::spawn`, so there is no
  runtime on the UI thread to spawn onto.  The pattern for
  this side is a named `std::thread::Builder` writing into
  a slot the UI polls, which
  `PLAN-bugreport-trigger-snapshot-phase-02-snapshot.md:422`
  already spells out — "not `tokio::spawn_blocking`".

  Two things the finding did not mention turned up in the
  doing.  No guard stopped a second bug report starting
  while the first was sampling — all three submit paths
  clear the dialog state immediately, so F12 was live again
  at once; `pending_bug_report.is_some()` is now that
  guard.  And deferring the report by two seconds would
  have cropped `screenshot-region.png` from a frame two
  seconds after the user drew the region, so the surface
  pixels are copied at submit time.
* **D2** — the PING handler emitted `Latency` before
  writing PONG.  Moved below the PONG write.
* **D3** — the status bar said `Latency:` for a
  server-chosen inter-PING interval.  Relabelled *PING
  interval*, and the sample now follows `Ping::read()` so a
  truncated PING produces none.
* **D4** — `sample()`'s unguarded `window_secs` divisor and
  its non-total `partial_cmp` sort.  Fixed by mirroring the
  macOS path, which already had the guard, `sort_by_key`
  and a zero-window regression test.
* **D6** — `libc` is dead in `ryll` (no `libc::` reference
  remains) and was deleted there; the renderer's live copy
  is gated `cfg(any(target_os = "linux", target_os =
  "macos"))`.  Note the audit's original suggestion of
  `cfg(target_os = "linux")` would have broken the macOS
  build.
* **A1** — the hand-paired `send_event(...)` /
  `notify_one()` sites collapsed into a single `EventSink`
  whose `emit()` does both, so a missed pairing can no
  longer be written.  Three judgment agents found the
  duplication independently and two proposed this fix.
  **All three also reported the pairing complete, and it
  was not.**  Implementing the fix turned up 62 event sends
  against 59 notifies on `develop`: `main_channel.rs`
  emitted `AgentConnected(true)` and `AgentConnected(false)`
  with no notify at all, so the UI did not repaint on guest
  agent connect or disconnect until the 1 Hz fallback
  fired.  Low severity — bounded to a second of staleness —
  but a real defect, and exactly the silent failure this
  refactor makes unwriteable.  Worth recording that three
  independent enumerations agreeing did not make the claim
  true; the structural fix found what the reading did not.
* **A2, A3, A4** — the `Notify as RepaintNotify` import
  inconsistency, the twice-written CPU-percent formula, and
  the `SAFETY` comment that justified an FFI call with
  async-signal-safety, which is the wrong concept.
* **B1, B5** — `linux::sample()` and the PING interval
  computation were untestable because they were fused to
  I/O and to a message-dispatch arm.  Extracted as pure
  `diff_snapshots()` and `ping_interval_ms()` and unit
  tested.

**Declined, with reasons.**

* **B2, B3, B4, B8** — remaining test gaps: adversarial
  `/proc` inputs (truncated lines, non-numeric fields, no
  parens), the bug-report ZIP test covering only the
  `Unavailable` variant, and tests asserting on raw JSON
  substrings rather than deserialising.  Declined for now:
  every `/proc` failure path already degrades to
  `RuntimeMetrics::unavailable` through `?` and `.ok()`
  rather than panicking, so these are coverage gaps rather
  than latent defects, and B1's extraction makes the
  arithmetic they would guard testable directly.  Worth
  revisiting if the metrics module grows.
* **D7** — demoting protocol logging `info!` → `debug!` did
  not shrink `/tmp/ryll.log`, because the file layer is
  constructed only under `--verbose` and filtered at
  `DEBUG`.  The demotion quieted non-verbose stderr, which
  is where the CPU concern was.  The `EnvFilter` question
  is already tracked as #313; declined here as a duplicate.

**Already fixed before the audit ran.**

* **B7** — playback gated `log_message` on
  `settings::is_verbose()` while the other six channels did
  not.  All seven have since converged on
  `self.log_config.verbose`.  This is also why T3's
  heuristic became a false-positive generator.

### Bugs fixed during this work

* The UI did not repaint when the guest agent connected or
  disconnected.  `main_channel.rs` sent
  `ChannelEvent::AgentConnected` twice without the paired
  repaint notification, so the status bar showed the change
  only when the 1 Hz fallback next fired.  Found while
  implementing A1's `EventSink`, not by the audit passes
  that read the same code.  Fixed by construction.

Beyond that, the audit found no bug that phases 1-5
introduced into shipped behaviour.  What it did find, and what #325 fixes,
is that the audit harness itself had not been updated when
the crates were split: three of wave 1's four range-scoped
checks were pointed at paths that no longer existed, and
the only check that can fail a build had stopped seeing
46% of the workspace.  The remaining code findings are
robustness and honesty fixes rather than defects with a
reproducer, and are listed above.

### Future work

* True keystroke-to-display latency measurement (would
  need an instrumented guest or known-glyph detection).
* Per-channel latency breakdown in the bug-report
  snapshot.
* Adaptive repaint cadence based on observed input/output
  activity.
* Investigate whether the channel select! loops can be
  collapsed to fewer tokio tasks (one task per channel
  may be overkill for sparse channels like cursor/inputs).


### Documentation index maintenance

When created: add to `docs/plans/index.md` Master plans
table and `order.yml`. Mark Complete when all phases land.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
