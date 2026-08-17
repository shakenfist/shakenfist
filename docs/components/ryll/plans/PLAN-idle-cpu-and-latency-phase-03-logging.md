# Phase 3: Demote protocol logging

Parent plan: [PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/)

## Goal

Two cleanups in [shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs)
and the channel handlers that call into it:

1. **Demote `log_message` and `log_unknown` from `info!` /
   `warn!` to `debug!`.**  Every received and sent message
   currently logs at INFO/WARN.  At the volumes a SPICE
   session generates (PINGs at multi-Hz × N channels, plus
   per-keystroke / per-mouse-move / per-frame traffic),
   that's pure noise in the default log.  Other channels
   already gate the call behind `is_verbose()` so the
   noise only leaks with `-v`; phase 3 makes the macro
   level itself match that intent so a stray future caller
   doesn't recreate the noise.

2. **Drop the embedded `[unix_timestamp]` from
   `log_message`, `log_unknown`, and `log_incomplete`.**
   `tracing-subscriber` already adds a wall-clock timestamp
   on emit (`2026-04-19T20:23:43.871004Z`).  The embedded
   `[1776630223.870958]` is redundant, ~50 µs older, and
   makes the line harder to read.  Remove it; the
   `format_timestamp()` helper itself can stay (only used
   here) or be deleted depending on whether anything else
   imports it.

3. **Add the missing `is_verbose()` guard at the two
   `playback.rs` call sites** (lines 419 and 600).  Phase 1
   noted these as the only unguarded `log_message` calls;
   on a server with active audio they would log at
   per-audio-frame rates.

## Background

Call sites confirmed via grep:

- `display.rs`: 3 `log_message` calls, all already
  guarded by `is_verbose()`.
- `inputs.rs`: 2 `log_message` calls, all already
  guarded.
- `cursor.rs`: 2 `log_message` calls, all already
  guarded.
- `main_channel.rs`: 6 `log_message` calls, all already
  guarded.
- `usbredir.rs`: 2 `log_message` calls, all already
  guarded.
- `webdav.rs`: 2 `log_message` calls, all already
  guarded.
- `playback.rs`: **2 unguarded** at lines 419 and 600.
- Several `log_unknown` calls scattered (rare path —
  unknown message types).  Already guarded or rare enough
  not to matter.

No external consumers of the log format: grep found no
references to `format_timestamp` or `"byte opcode"` in
`tools/`, scripts, or tests outside the logging module
itself.  Safe to change the format.

`format_timestamp()` and `timestamp()` are public in the
`shakenfist_spice_protocol` crate.  They are used only in
`logging.rs` itself.  After removing them from
`log_message` / `log_unknown` / `log_incomplete`, both
helpers become dead.  Decision: keep `timestamp()` as a
generic protocol-time helper (it's a one-liner around
`SystemTime::now()`, useful for any future protocol-level
timing), delete `format_timestamp()` if nothing else
imports it.

## Constraints and edge cases

- The `info!` macro inside `log_message` is shadowed by
  the import `use tracing::{debug, info, warn}`.  After
  the change, only `debug!` and `warn!` (and the `debug!`
  in `log_incomplete`) are needed; remove `info` from the
  import to keep clippy happy.
- `log_unknown` uses `warn!`.  An unknown message type is
  worth a louder log line than an expected one.  Leave it
  at `warn!` — it's rare by design.  Just drop the
  embedded timestamp.
- The fallback `request_repaint_after(1s)` from phase 2
  means any new log line still shows up promptly; logging
  level changes don't interact with it.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a   | low    | sonnet | none     | In [shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs): change `log_message` from `info!` to `debug!`; drop the leading `[{}] ` and the corresponding `format_timestamp()` argument from the format string (in `log_message`, `log_unknown`, and `log_incomplete`).  Result format: `"playback received 12 byte opcode 4 ping"` instead of `"[1776630223.870958] playback received 12 byte opcode 4 ping"`.  Remove `format_timestamp` if no other call site uses it (grep first; it's unused outside this file).  Keep `timestamp()` as a public helper.  Adjust the `use tracing::{...}` import to `{debug, warn}` (drop `info`) and verify clippy doesn't complain about unused imports. |
| 3b   | low    | sonnet | none     | In [ryll/src/channels/playback.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/playback.rs): wrap the two `logging::log_message(...)` calls at lines 419 and 600 in `if settings::is_verbose() { ... }` blocks, mirroring the pattern used in every other channel.  Verify by grepping for `log_message` in `ryll/src/channels/` and confirming all sites are guarded. |
| 3c   | low    | sonnet | none     | Run `pre-commit run --all-files` (must pass) and `make test` (must pass).  No code changes in this step — just verification. |

## Success criteria for this phase

- Default log output (no `-v`) contains no `byte opcode`
  protocol noise.
- With `-v`, protocol lines appear at debug level with no
  embedded `[unix_ts]` prefix.
- `pre-commit run --all-files` and `make test` pass.
- Single commit for steps 3a + 3b combined (small, related
  changes; together they form the "logging cleanup"
  unit).
