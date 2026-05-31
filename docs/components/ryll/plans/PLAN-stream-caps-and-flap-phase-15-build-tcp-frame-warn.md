# Phase 15 — Track down `build_tcp_frame: payload too large` warns

**Status:** Not started.

**Driven by:** Live observation during session 004 H.264 follow-up:

```
2026-05-21T10:12:41.379152Z  WARN build_tcp_frame: payload too large for IPv4 (2246044 bytes), dropping
2026-05-21T10:12:41.716323Z  WARN build_tcp_frame: payload too large for IPv4 (2245427 bytes), dropping
2026-05-21T10:12:42.056672Z  WARN build_tcp_frame: payload too large for IPv4 (2245506 bytes), dropping
2026-05-21T10:12:42.394060Z  WARN build_tcp_frame: payload too large for IPv4 (2245502 bytes), dropping
```

`2245506` bytes implies `tcp_payload_len ≈ 2.2 MiB`, which matches
a single un-segmented display-channel message at ~1920×1440 RGBA
(1920 × 1440 × 4 = 11_059_200 bytes; a quarter-screen update or
a `ZlibGlzRgb` payload before decompression sit in this range).

## Why this should be impossible

`capture::segment_payload` (added in `d95d4b3c` on 2026-05-12 as
the K2 fix) is the only caller of `build_tcp_frame` in the
current tree:

```
ryll/src/capture.rs:142:        frames.push(build_tcp_frame(...chunk...));   // segmented chunk
ryll/src/capture.rs:157:        frames.push(build_tcp_frame(... data...));    // empty-data fallback
```

The loop at `:138-152` chunks at
`MAX_PAYLOAD = 65535 − 20 (IP) − 20 (TCP) = 65495`, so any
non-empty `data` produces ≥1 frame each with
`chunk.len() ≤ 65495`. The fallback at `:153-160` fires only
when `data.len() == 0` (in which case the warn cannot trigger).

Therefore `ip_payload_len = tcp.header_len() + tcp_payload_len`
should never exceed `20 + 65495 = 65515`, making the
`> 65515` warn at `capture.rs:183` defensively unreachable.

A grep across the full worktree (and the sibling
`shakenfist/ryll` and `shakenfist/ryll-release` clones) finds
no other callers of `build_tcp_frame`, no other uses of
`Ipv4Header::new`, and no `etherparse`-based frame builders.

So one of the following must be true:

1. **The Mac binary predates the K2 fix.** Pre-K2, the
   ring-buffer path in `bugreport.rs::build_frame` called
   `build_tcp_frame` directly with the whole SPICE message —
   exactly the symptom we're seeing. The user's recorded
   session-004 binary metadata shows sha `93474db2` which is
   post-K2, but the binary running *now* (after rebuild for
   the H.264 follow-up) may be different. **First diagnostic
   action: confirm the running binary's git sha.**
2. **There is a `build_tcp_frame` caller `grep` did not find.**
   Possibilities worth considering: a sub-binary I missed, a
   `cfg`-gated path, a hand-rolled frame builder, a path
   introduced by a recent in-flight change not yet
   committed. If so, route it through `segment_payload`.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 15A  | low    | n/a   | none      | Operator action: on the Mac running the warns, run `ryll --version` and report the git sha. If it predates `d95d4b3c` (2026-05-12), the fix is `git pull && cargo build`; close the phase. Otherwise proceed to 15B. |
| 15B  | low    | sonnet | none     | If 15A confirms the binary is post-K2, instrument the warn at `ryll/src/capture.rs:183` with a one-shot backtrace: on first hit, capture `std::backtrace::Backtrace::force_capture()` (use a `std::sync::OnceLock<()>` to ensure it fires exactly once, so a busy session doesn't spam thousands of stacks), include it in the warn message via `{:?}`. Build, run, reproduce; report the call site to the operator. |
| 15C  | low    | sonnet | none     | Based on 15B's identified call site: either fix the caller to route through `capture::segment_payload`, or — if the warn turns out to be reproducible only from `segment_payload` itself (which would indicate a real bug in chunking) — fix `segment_payload`. Add a regression test that round-trips a 2 MiB payload through whichever path was broken and asserts no warn fires. |
| 15D  | low    | sonnet | none     | Once 15C confirms segmentation is the only path, demote the `if ip_payload_len > 65515` check from `warn!` to `debug_assert!` + `debug!`. The check stays as defence-in-depth, but a fired condition is a code bug to crash on in tests rather than a runtime condition to log around. Update the doc comment on `build_tcp_frame` to note that it now relies on segmentation being correct. |

## What good looks like

After this phase:

- No `payload too large for IPv4` warns fire under normal
  operation, even at 1920×1440 with `streaming-video=all`
  triggering large display-channel messages.
- Test coverage for the 2 MiB-through-segment_payload path
  exists and is wired into `make test`.
- The defensive check in `build_tcp_frame` is honest about
  what it's defending against: a chunker bug, caught by
  `debug_assert!` in dev / CI rather than silently dropping
  packets in prod.

## Out of scope

- Replacing `etherparse` or rewriting the pcap framing. The
  framing is correct; the question is solely whether anyone
  is bypassing the segmentation wrapper.
- Adding IPv6 support to the pcap dump. Out of scope; the
  64 KiB limit is an IPv4-only property.
- Changing what `MAX_PAYLOAD` is set to. 65495 is the
  documented IPv4 ceiling for a no-options header; do not
  raise it without reading RFC 791 §3.2.

## Cross-references

- `ryll/src/capture.rs:126-162` — `segment_payload`.
- `ryll/src/capture.rs:165-220` — `build_tcp_frame`.
- `ryll/src/bugreport.rs:462-486` — bug-report ring path
  through `segment_payload`.
- Commit `d95d4b3c` — K2 fix (added `segment_payload`
  and routed both pcap-writing paths through it).
- Master plan §K2 / phase 08 of the original ring-buffer
  work — original context for the K2 bug.
