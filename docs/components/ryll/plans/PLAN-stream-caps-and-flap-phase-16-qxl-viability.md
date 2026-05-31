# Phase 16 — Evaluate guest driver options for video streaming

**Status: Proposed (concept). No code steps; investigation-shaped.**

Phase 16 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Why this exists

Phase 13's investigation assumes the goal is "make QXL stream
stably". Session 005 made it concrete that the QXL guest
driver's OOM events are evicting stream state on the server.
That's a *fixable* failure mode but might also be the wrong
problem to fix:

- The QXL guest driver is upstream-maintenance-only and likely
  to stay that way.
- `virtio-gpu`/`virtio-vga` is the actively-maintained path
  but reportedly doesn't engage spice-server's streaming
  heuristic at all (the streaming path is tightly coupled to
  the QXL command ring per
  `docs/libvirt-spice-recommendations.md`).
- That assertion ("virtio = no streaming, just bitmap blits")
  has never been measured against real ryll bug-report data.
  It may be true and yet the resulting UX is *less bad* than
  QXL's flap-and-fall-back, because at least bitmap blits
  ship every frame predictably.

So the prior question to phase 13 is: **which guest-driver
shape is the right substrate for video at all?** Phase 16
answers that empirically.

## Three experiments

Each is a session run with the auto-snapshot + spice-debug
template edit already established in 005:

| Run | Guest | Video device | Expected result |
|-----|-------|---------------|------------------|
| 16a | Debian 13 + QXL | qxl, 64 MiB VRAM | Does updating the QXL kernel driver alone change OOM rate / stream survival? |
| 16b | Debian 11 + virtio-vga | virtio, accel3d=no | Confirm/refute "no streaming". Compare frame rate, decode latency, bandwidth, and operator perception against 005b. |
| 16c | Debian 11 + virtio-vga + accel3d='yes' | virtio + virgl | Does GL acceleration change the picture? May need extra guest packages. |

Each at 1920×1440, same workload as 005b (10 min YouTube
clip), same harness. Same auto-snapshot cadence.

## Output

A "Guest driver decision matrix" section appended to
`docs/libvirt-spice-recommendations.md` with the actual
measurements per row, plus a one-sentence recommendation
for the four common operator situations:

- "Pure desktop / terminal workload" → x
- "Occasional video, mostly UI" → y
- "Sustained video playback" → z
- "3D / OpenGL" → w

If 16b or 16c shows virtio is the better video substrate
despite the loss of streaming, phase 13 narrows from
"fix the QXL OOM cycle" to "characterise the QXL failure
mode for the operator decision matrix and move on".

If 16a (newer QXL driver) reduces OOMs enough that
streaming survives, phase 13 narrows to a docs update:
"recommend Debian 13+ for QXL guests in this matrix".

## What this is NOT

- A code-delivery phase. No ryll changes are expected to
  fall out of phase 16 directly. Findings may inspire
  phase-7-or-later refinements (e.g. resolution-hint via
  `MONITORS_CONFIG`), but those land as their own phases
  if needed.
- A replacement for phase 13's source-read sub-steps
  (13A/13C). The QXL-driver source-read happens whether
  or not virtio turns out to be the better answer; the
  mechanism is worth understanding either way.

## When to plan in detail

After phase 13A's source-read characterises what
`display_channel_free_some` actually evicts. If 13A's
answer is "OOMs are unavoidable side-effects of QXL's
command-ring being small", phase 16 becomes urgent.
If 13A's answer is "OOMs are recoverable and the stream
should re-engage but a bug prevents it", phase 16 is
optional confirmation rather than the main path.

## Cross-references

- `PLAN-stream-caps-and-flap-phase-13-streaming-intermittency.md`
  — the investigation that has to land 13A before this
  phase is worth running.
- `docs/libvirt-spice-recommendations.md` — the
  "Recommended: virtio-vga" / "Not recommended: qxl"
  sections; this phase produces the data to back them up
  (or invalidate them).
- ryll-test-sessions `005.md` — the harness shape any
  `006.md` for phase 16 would follow.
