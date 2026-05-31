# Phase 13 — Investigate intermittent server-side streaming

Phase 13 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).
Driven by sessions 002e / 002g / 002h / 003 / 004 and **rewritten by
session 005** once the spice-debug surface fix made the qemu log
readable.

## What changed in session 005

For five sessions we believed the server's streaming heuristic
simply did not fire at 1920×1440 with a QXL guest. Client-side
`streams_created_total` was zero across every high-res run.
Once session 005 landed the
`<qemu:env name='G_MESSAGES_DEBUG' value='all'/>` template
edit and the qemu log filled with real spice-server
instrumentation, the picture changed completely:

- **Server creates the right stream.** At 005b's video start
  the server emitted
  `display_channel_create_stream: stream 6 1024x768 (0, 0) (1024, 768) 10 fps`
  — correctly detecting the YouTube video region inside the
  1920×1440 desktop.
- **Client decoded it.** 79 MJPEG frames over 8 seconds — the
  full client-side machinery (cap negotiation, STREAM_CREATE
  handling, MJPEG decode) worked first try.
- **Server destroyed the stream at 06:09:28** (~8 s after
  creation) and then **never recreated it for the remaining
  10 minutes of 005b or the 2 minutes of 005c**, despite the
  user continuing to play the same video in the same region.

So the bug is not "heuristic doesn't fire". The bug is
**single-shot teardown without re-engagement**.

## Likely mechanism (per source-read, to be confirmed)

The qemu log shows `display_channel_debug_oom` firing
**1140 times in 005b** (over 670 s, ~1.7/sec) and **1238 times
in 005c**. Reading the source:

- `display_channel_debug_oom` is called from `red-worker.cpp::handle_dev_oom`
  in two places: `OOM1` before recovery and `OOM2` after.
- `handle_dev_oom` runs `display_channel_free_some()` and
  `red_qxl_flush_resources()` between the two log lines —
  this is an emergency drop of pending drawables to free
  memory.
- The OOM message itself is `RedWorkerMessageOom`, sent by
  `spice_qxl_oom()` at `red-qxl.cpp:328`. Calling
  `spice_qxl_oom` is qemu's QXL device emulation telling
  the spice-server **"the guest QXL driver has run out of
  command-ring memory"**.

So this is **guest-driver memory pressure**, not a
spice-server encoder bandwidth problem. The chain:

```
guest QXL kernel driver out of command-ring slots
  → qemu QXL device emulation sees the out-of-memory notification
    → spice_qxl_oom() sends RedWorkerMessageOom to spice-worker
      → handle_dev_oom drops drawables + flushes (display_channel_free_some)
        → stream-tracking state evicted as a side effect
          → the video-region stream's frame-rate detector loses confidence
            → RED_STREAM_TIMEOUT after the next gap and the stream is destroyed
              → server stays in defensive fallback (ZlibGlzRgb) for that region
```

This is a hypothesis. It is consistent with what we see:

- Continuous OOM pressure throughout 005b/005c.
- Stream creation only at the very start (before the first OOM
  burst), never after.
- Session 004's "more VRAM doesn't help" result is also
  consistent — VRAM matters for streaming creation only via
  this indirect OOM-frequency path; the original
  Did-VRAM-fix-streaming test asked the wrong question.

## 13A findings (source read)

**Verdict: refuted in its strong form, partial in a more interesting
form.** OOM does *not* evict stream-detection state directly; in fact,
the OOM eviction path *populates* the per-region trace buffer that
re-engagement reads. The mechanism that prevents re-engagement is
subtler, and the binding constraint is the trace ring's tiny size
(8 entries) combined with the 200 ms detection window.

### What `handle_dev_oom` actually does

`server/red-worker.cpp:530-549`. Step by step:

1. Asserts the QXL device is running (line 537).
2. Emits `display_channel_debug_oom("OOM1")` (line 539) — the log
   line we count in session 005.
3. Drains pending QXL commands by looping
   `red_process_display()` (line 540) and pushing each batch to
   pipes via `display->push()` (line 541). This *consumes* fresh
   draws from the guest — it does not evict anything.
4. Calls `red_qxl_flush_resources(qxl)` (line 543), a thin
   wrapper at `server/red-qxl.cpp:780-785` that calls into the
   qemu QXL device's `flush_resources` callback. This is the
   qemu-side release-ring drain; no spice-server state is
   touched. If it returns non-zero (released >= 1 resource),
   step 5 is skipped.
5. Fallback only if flush released zero resources:
   `display_channel_free_some(display)` (line 544) followed by a
   second `red_qxl_flush_resources` (line 545).
6. Emits `display_channel_debug_oom("OOM2")` (line 547) and
   clears the worker's pending-OOM bit (line 548).

`display_channel_free_some` (`server/display-channel.cpp:1481-1507`)
does two things: (a) for each DCC, releases GLZ dictionary
drawables held by the encoder (line 1494); (b) walks
`display->priv->current_list` from the tail and calls
`free_one_drawable(display, force_glz_free=TRUE)` up to
`RED_RELEASE_BUNCH_SIZE` times (line 1498).
`free_one_drawable` (line 1451-1471) renders the oldest pending
drawable to the canvas via `drawable_draw`, then calls
`current_remove_drawable` (line 1468).

### Does it touch stream-tracking state?

It touches stream state — but **constructively, not
destructively**. `current_remove_drawable`
(`server/display-channel.cpp:365-374`) calls
`video_stream_trace_add_drawable` (line 368) on every evicted
drawable. That function (`server/video-stream.cpp:1049-1068`)
records the drawable's geometry, `frames_count`,
`first_frame_time`, and `gradual_frames_count` into one slot of
the ring buffer `display->priv->items_trace`, indexed by
`next_item_trace++ & ITEMS_TRACE_MASK`. The eviction filter at
line 1054 skips drawables that are already attached to a stream
(`item->stream`) or are not streamable (`!item->streamable`),
so OOM eviction can only ever *add* candidate frames to the
trace — never overwrite live stream metadata.

The trace ring is `std::array<ItemTrace, NUM_TRACE_ITEMS>` with
`NUM_TRACE_ITEMS = 1 << 3 = 8`
(`server/display-channel-private.h:23-25,115-116`). That is
**the critical constant** — only the eight most recently evicted
streamable drawables are remembered. The trace is reset to
zero only in `stop_streams` (line 226-227), which is itself
only called from `display_channel_surface_unref` when the
primary surface is destroyed (line 230-241). OOM does not
trigger surface destruction. Active `VideoStream` instances
themselves live on `display->priv->streams` and are torn down
by `video_stream_timeout` (`server/video-stream.cpp:1031-1047`)
when their `last_time + RED_STREAM_TIMEOUT` (1 s) has passed —
an inactivity timer, not an OOM-driven path.

### What `display_channel_create_stream` actually requires

Caller chain
(`server/video-stream.cpp:419,585,559-590,628-666,668-707`):

- `display_channel_process_draw` →
  `display_channel_add_drawable` (line 1317-1364) sets
  `drawable->streamable = drawable_can_stream(...)` (line 1353).
  `drawable_can_stream` (line 1044-1080) requires: stream-video
  mode enabled, primary surface, `QXL_EFFECT_OPAQUE`,
  `QXL_DRAW_COPY` with `SPICE_ROPD_OP_PUT`, a
  `SPICE_IMAGE_TYPE_BITMAP` source, and (in FILTER mode)
  area ≥ `RED_STREAM_MIN_SIZE` (96×96).
- `current_add` calls `video_stream_trace_update`
  (`server/display-channel.cpp:1019`).
- `video_stream_trace_update` (`server/video-stream.cpp:628-666`)
  first scans active streams; if none match, it scans the
  eight-slot `items_trace`. For each trace entry,
  `is_next_stream_frame` (line 213-270) checks: same
  src-width/height, identical bbox, and
  `candidate->creation_time - trace.time` ≤
  `RED_STREAM_DETECTION_MAX_DELTA` (NSEC_PER_SEC / 5 = **200 ms**,
  `server/video-stream.h:32`). On match,
  `video_stream_add_frame` increments `frame_drawable->frames_count`
  from `trace.frames_count + 1` (line 568) and tests
  `is_stream_start` (line 182-187), which needs
  `frames_count ≥ RED_STREAM_FRAMES_START_CONDITION = 20` and
  20 % gradual-quality coverage (`server/video-stream.h:35-36`).
- `video_stream_maintenance` (line 668-707) is the other entry
  point, fired when an opaque drawable replaces a previous one
  at the same tree position (`current_add_equal`, line 488).

So re-engagement of a torn-down stream requires twenty
consecutive matching frames at the same bbox, with each
successive draw arriving inside 200 ms of the previous, with
the per-region history threaded through a ring buffer that
holds only eight entries total *across all surfaces and
regions*.

### Verdict

**Partial.** OOM eviction does not directly clobber stream
state — `video_stream_stop` and `display_channel_free_some` are
disjoint paths, and the trace ring is only zeroed on primary
surface teardown. What kills re-engagement is the interaction
between OOM eviction *of unrelated drawables* and the trace
ring's 8-entry capacity. At 1.7 OOMs/sec each releasing up to
`RED_RELEASE_BUNCH_SIZE` (commonly tens of) drawables from the
tail of `current_list`, *every* streamable drawable that gets
evicted writes to the same shared 8-slot ring. With a busy
1920×1440 desktop (chrome, taskbar, cursor blink, every
streamable bitmap blit anywhere) the video region's trace
entries are flushed out of the ring before twenty consecutive
matching draws can accumulate within the 200 ms window — and
the video draws themselves, if they hit `current_list` during
an OOM burst, end up *in the trace* rather than *attached to a
stream* because there is no active stream to attach to. The
heuristic is starved by trace contention, not by state
eviction.

The session 005 evidence is consistent: once the original
1024×768 stream is torn down by `video_stream_timeout` (1 s
gap from the encoder pipeline being throttled by OOM
back-pressure is plausible), every subsequent video frame
arrives into a tree where (a) no active stream matches it and
(b) the trace ring is dominated by recently-evicted desktop
chrome. The video region's own trace entries either never
accumulate enough consecutive within-200 ms hits or are
displaced by other streamable evictions in the same OOM cycle.

**Resolved:** `RED_RELEASE_BUNCH_SIZE = 64`
(`server/image-encoders.h:221`). Each OOM-driven
`display_channel_free_some` evicts up to 64 drawables from
the tail of `current_list`, which is *eight times* the
8-slot `items_trace` ring. A single OOM cycle can therefore
fully overwrite the trace ring multiple times if enough of
the evicted drawables are `streamable`. The trace-contention
argument above is firmly grounded.

**Resolved:** `red_stream_input_fps_timeout_callback` does
not exist in this spice tree — the 13A brief referenced a
function from an older or downstream-patched spice. The FPS
estimate is computed inline in `attach_stream`
(`server/video-stream.cpp:282-292`) using
`RED_STREAM_INPUT_FPS_TIMEOUT = 5 s`. No separate timer
callback path to read.

### Implications for 13B

The hypothesis-as-written ("more VRAM lowers OOM rate, lower
OOM rate lets the heuristic re-fire") is still correct in
direction but the *mechanism* is trace-ring contention rather
than state eviction. The 13B prediction is the same: at
sufficiently high VRAM the guest QXL driver should stop
issuing OOMs, the trace ring stops being repopulated by
unrelated drawables, the video region's trace entries persist
long enough for `video_stream_trace_update` to find a match,
and stream re-engagement should follow within ~1 s of the
first 20 video frames after teardown.

Recommendation for 13B's session: run with 64 MiB / 128 MiB /
256 MiB QXL `vram_size`, all on the same 1920×1440 workload as
005b. Grep qemu logs for: `display_channel_debug_oom` counts
per 60 s, `display_channel_create_stream` events, and
`video_stream_stop`-adjacent destroy lines. The expected
signature of confirmation is OOM count and stream-re-engagement
count being inversely correlated. If 256 MiB still shows
hundreds of OOMs/min, the guest driver is the source and 13C
(read the QXL kernel driver) becomes the next step.

### Implications for 16 / upstream

If 13B confirms, this is a clean upstream bug-report against
spice-server: "On hosts under sustained QXL OOM pressure, the
8-entry `items_trace` ring buffer is fully overwritten faster
than `RED_STREAM_FRAMES_START_CONDITION` consecutive frames
can accumulate for a single region, preventing video stream
re-engagement after `video_stream_timeout` tears the first
stream down. Reproduction: 1920×1440 QXL guest, 64 MiB vram,
windowed YouTube playback for >2 min; observe single
`display_channel_create_stream` event followed by zero
re-engagements over the remainder of the session despite
continuous matching draws." A one-line server-side mitigation
would be to grow `NUM_TRACE_ITEMS` from 8 to (say) 64 — the
trace entries are tiny and the cost is a few hundred bytes per
display channel.

Client-side mitigation (phase 13E candidate): the trace ring
is server-internal; the client cannot directly seed it. The
phase 13E "more aggressive STREAM_REPORT" option only affects
*surviving* streams (it feeds bit-rate / drop accounting in
`mjpeg_encoder_handle_positive_client_stream_report`); it does
nothing for a stream that has already been destroyed. A more
useful client lever, if upstream won't move, is to *avoid the
1 s teardown gap* by ensuring the client doesn't induce
back-pressure — but that is speculation pending 13B's OOM-vs-
VRAM curve.

## 13C findings (source read)

**Verdict: this is fundamentally a guest-driver design constraint, not
a tuning problem.** The OOM signal is not a "% free" threshold or a
periodic check — it is a side-effect of any kernel sleep in
`qxl_fence_wait`, and the underlying scarce resource is the **32-slot
QXL command ring** plus the host-side release backlog feeding into the
**8-slot release ring**. Higher `vram_size` will reduce some kinds of
OOM (the TTM-eviction kind) but cannot make the command ring deeper.

### What is the OOM trigger?

There are only two call sites for `qxl_io_notify_oom` in the driver
(`qxl_cmd.c:355-358`). One is `qxl_device_fini` shutdown
(`qxl_kms.c:305`); irrelevant to runtime. The runtime trigger is
inside `qxl_fence_wait` (`qxl_release.c:59-77`):

```c
if (!wait_event_timeout(qdev->release_event,
            (dma_fence_is_signaled(fence) ||
             (qxl_io_notify_oom(qdev), 0)),
            timeout))
    return 0;
```

The comma-expression hides the trick: every time `wait_event_timeout`
re-evaluates its predicate (on each wake-up of `release_event`, which
the IRQ handler fires on `QXL_INTERRUPT_DISPLAY`,
`qxl_irq.c:46-50,55-58`, ultimately calling `wake_up_all` in
`qxl_garbage_collect`, `qxl_cmd.c:250`), and the fence is still not
signalled, the guest pokes the host via the `QXL_IO_NOTIFY_OOM` port.
This is not a threshold check — it is a **liveness ping fired every
time the guest blocks on a host-side release that hasn't come back
yet**. The host receives it (`qemu/hw/display/qxl.c:1760` →
`qxl_spice_oom` at line 210-214 → `spice_qxl_oom`) and forwards it
to the spice-worker as `RedWorkerMessageOom`.

What triggers a fence wait? Any TTM operation that needs to evict a
BO carrying an unsignalled release fence: the most common path is
`qxl_bo_create` (`qxl_object.c:104-154`) under VRAM pressure, called
from `qxl_release_bo_alloc` (`qxl_release.c:162-168`) every
`RELEASES_PER_BO = PAGE_SIZE/256 = 16` releases (line 41), and from
`qxl_alloc_bo_reserved` (`qxl_cmd.c:256-278`) for every per-draw
image/payload BO. Surface-ID exhaustion adds a second path:
`qxl_reap_surf` does an explicit `dma_resv_wait_timeout(..., 15 * HZ)`
(`qxl_cmd.c:591-593`), reached when `handle >= rom->n_surfaces`
(`qxl_cmd.c:435-441`).

### What's the relevant size constant?

Three rings are statically sized in `qxl_dev.h:324-326`:

- `QXL_COMMAND_RING_SIZE = 32` — the actual draw command pipeline.
- `QXL_CURSOR_RING_SIZE = 32`.
- `QXL_RELEASE_RING_SIZE = 8` — how many freed-resource ids the host
  can hand back per round-trip.

These are baked into the QXL device contract: they live in the
`QXLRam` struct as fixed-size arrays (`qxl_dev.h:351-356`) shared
between guest and host. **Neither the operator's `ram=` nor `vram=`
nor `vgamem=` knob changes them**; only the qemu/spice-server source
can (and the on-wire ABI would forbid it). Guest VRAM
(`qdev->vram_size` from `rom->surface0_area_size`, `qxl_kms.c:54`)
gates how many release-BOs and image-payload-BOs can coexist before
TTM eviction starts; `num_surfaces` (qemu default 1024,
`qemu/hw/display/qxl.c:2494`) gates the surface-ID pool. Larger
`vram=` raises the TTM ceiling; larger `surfaces=` raises the
surface-ID ceiling. **Neither raises the 32-slot command-ring or the
8-slot release-ring ceiling.**

### Does workload shape matter?

Yes, strongly, and in exactly the direction 13A predicted. Each
draw operation produces:

- one `QXL_RELEASE_DRAWABLE` slot in the release-BO arena (consuming
  1/16 of a page; a new BO is allocated every 16 draws,
  `qxl_release.c:323-339`),
- one or more per-draw `qxl_alloc_bo_reserved` image BOs
  (`qxl_cmd.c:256-278`) sized to the image payload,
- one slot in the 32-deep command ring (`qxl_cmd.c:105-150`).

The command-ring slot count is **per draw**, not per pixel: a 4×4
cursor blink and a 1920×1440 full-screen blit each consume one slot.
A windowed video at 30 fps competing with chrome animations,
taskbar clock, and cursor blink on a 1920×1440 desktop will fill 32
slots far faster than a quiescent 1024×768 desktop showing the same
video, even if the video's pixel rate is unchanged. When
`qxl_ring_push` finds `prod - cons == num_items` it sleeps on
`push_event` (`qxl_cmd.c:113-134`); that sleep doesn't *itself* fire
OOM, but it back-pressures the X server, which in turn means the
next allocation of a per-draw payload BO is more likely to hit a
not-yet-recycled release-BO fence and trigger the
`qxl_fence_wait` OOM ping. Workload **fragmentation**, not pixel
volume, is the dominant input.

This matches the session 005 observation: 1024×768 streams the same
YouTube cleanly; 1920×1440 thrashes. At 1024×768 the video region
is ~78% of the desktop, the surrounding chrome contributes a small
relative fraction of draws, and the command ring is largely "the
video region". At 1920×1440 the same video is ~22% of the desktop,
and the chrome / cursor / browser-UI draws dominate the 32-slot ring
contention.

### Verdict on the 13A prediction

Partial support. Larger `vram=` does reduce the TTM-eviction
fence-wait path (more headroom for release-BO arenas before
eviction), which should reduce OOM rate up to a point — but it
**cannot remove** the back-pressure from a 32-slot command ring
under a draw-heavy workload. We should expect 13B to show a
diminishing-returns curve: 64→128 MiB likely helps measurably,
128→256 MiB likely helps less, and beyond some `vram=` value OOMs
will plateau at a workload-determined floor set by command-ring
depth and draw rate. To go below that floor the operator would
have to either (a) reduce the number of *distinct* draws per second
(disable taskbar animations, cursor blink, browser smooth scroll,
window compositor effects) or (b) replace the guest driver with
one that doesn't share the QXL device's ring sizing.

### Implications for 13B's test design

Beyond the OOM counts and stream-create counts already in the 13B
brief, 006 should capture:

- **`/sys/kernel/debug/dri/<n>/qxl_release_info`** —
  `qxl_debugfs.c:1-127` registers debugfs nodes. Need to confirm
  exact names against the running Debian kernel; the file lists
  outstanding releases per type, which directly measures the
  release-arena pressure that drives the fence-wait path.
  **Unresolved:** I have not verified the exact debugfs node name
  in the Debian-shipped QXL build; check `ls /sys/kernel/debug/dri/0/`
  on the guest before relying on it.
- **`/proc/interrupts | grep qxl`** — IRQ rate per minute is a
  direct proxy for release-ring round-trips; if larger `vram=`
  reduces OOMs but the IRQ rate stays high, command-ring depth
  is the floor.
- **dmesg `DRM_DEBUG_DRIVER` lines** — `qxl_release_free` emits a
  per-release debug line (`qxl_release.c:140`); enabling
  `drm.debug=0x04` on the guest kernel cmdline turns these on and
  makes the release rate measurable directly.
- **Workload variant**: re-run 005b once with the desktop chrome
  quiesced (close taskbar widgets, disable cursor blink, fullscreen
  the browser). If OOMs drop substantially at the *same* `vram=`,
  workload-shape is the dominant input and the 13A trace-contention
  story is confirmed end-to-end.

### Implications for phase 16 (guest driver alternatives)

This is the strong-escalation case. `QXL_COMMAND_RING_SIZE = 32` is
part of the QXL device ABI in `qxl_dev.h:324`; it is not a tunable
in any released kernel and not a tunable in qemu either. Newer
Linux kernels do not change it (the file's commit history shows the
value has been 32 since the driver was upstreamed). The driver's
OOM-on-fence-wait pattern is structural, not a recent regression.
Therefore "QXL on Debian 13 might work; test that" is unlikely to
help materially — Debian 13 ships the same upstream driver against
the same device ABI. Phase 16 should plan around **replacing QXL**
at high resolution, not tuning it: virtio-gpu (no command ring of
this style; uses virtqueues with operator-controllable depth) or
falling back to plain VGA + spice-vdagent for the cases where 3D
acceleration isn't required. Ryll-side mitigations are out of
scope for this section per the brief; the takeaway for phase 16 is
that the substrate, not the configuration, is the limit.

## What to investigate (in order)

### 13A — Confirm the OOM-evicts-stream-state mechanism

**Effort: medium. Output: a writeup in this file.**

Read the spice-server source carefully:

- `red-worker.cpp::handle_dev_oom` (the OOM handler).
- `display-channel.cpp::display_channel_free_some` (what
  it actually frees — does it touch `Stream` instances /
  `StreamCreateDestroyItem` / stream tracking structures?).
- `red_qxl_flush_resources`.
- `video-stream.cpp` — specifically the per-region frame-rate
  detector (`is_next_stream_frame`,
  `red_stream_input_fps_timeout_callback`) and the conditions
  under which it would *re-engage* after a teardown.

Answer: does an OOM-driven `display_channel_free_some` evict
the per-region frame statistics that the heuristic needs to
re-fire? Or does the stream-create heuristic require some
state that gets cleared on each OOM cycle? Or is it that the
QXL guest driver under memory pressure produces draws of a
different op-type that fail the bitmap-opaque filter (the
original 004 hypothesis, just re-framed as an effect of OOM
rather than of resolution)?

If the mechanism is "OOM evicts stream state, recreation needs
N frames of fresh statistics, OOMs fire faster than N frames
of stats can be gathered" — that's a server-side bug worth
filing upstream against spice-server with a minimal
reproducer.

### 13B — Quantify the OOM-rate dependency on guest VRAM

**Effort: low-medium. Output: a small results table for this file.**

Re-run the 005b workload (1920×1440, ≥3 min) at three guest
VRAM values, all with the spice-debug template edit in place:

| Run | guest VRAM | OOM count over run | 1024×768 stream creates | Stream re-engagements |
|-----|-----------|--------------------|--------------------------|------------------------|
| 005b (already done) | 64 MiB | 1140 over 670 s (1.7/s) | 1 | 0 |
| (next) | 128 MiB | ? | ? | ? |
| (next) | 256 MiB | ? | ? | ? |

If OOM rate scales inversely with VRAM AND stream
re-engagements scale up — the diagnosis is locked. If OOMs
stay high regardless of VRAM, look elsewhere
(`ram`/`vgamem`, qemu's QXL device sizing, guest-driver
allocation pattern). The instructions for this go into a
follow-up `006.md` in `ryll-test-sessions`.

### 13C — Read the guest QXL driver

**Effort: medium. Output: a writeup in this file.**

The relevant guest-side source lives at:

- `/srv/src-reference/torvalds/linux/drivers/gpu/drm/qxl/qxl_release.c`
- `/srv/src-reference/torvalds/linux/drivers/gpu/drm/qxl/qxl_drv.h`
- `/srv/src-reference/torvalds/linux/drivers/gpu/drm/qxl/qxl_cmd.c`

(Confirm paths exist before relying on them; this repo
mirrors several kernel trees and the QXL driver is small.)

What we want to learn: under what conditions does the QXL
guest driver call its `out_of_memory` notification (the thing
that triggers `spice_qxl_oom` on the host)? Is there a
threshold like "<N% command-ring free"? Does it scale with
draw-op size (i.e. would more 4K-tile draws produce more
OOMs than fewer full-screen blits)?

If the trigger is small-and-frequent-draws, the workload
shape matters: a fullscreen video produces large in-place
draws and few OOMs; a windowed-video-on-busy-desktop
produces small partial draws and many OOMs. That would
explain why the same machine streams fine at 1024×768
(everything is windowed-into-a-small-desktop, so the video
takes up *more relative area* in the command ring) and
badly at 1920×1440 (the video shares the ring with all the
desktop chrome behind it).

### 13D — Reduce OOM frequency from the qemu device side

**Effort: out of scope; document only.**

The relevant qemu knob is the QXL device's `ram_size` /
`vram_size` parameters (the `<video><model type='qxl'
ram='65536' vram='65536' ...` block in the libvirt XML).
Larger values give the guest driver more headroom before
it triggers OOM.

Phase 13B above measures the effect; the documentation
follow-up is to update `docs/libvirt-spice-recommendations.md`
with what we actually found, replacing the now-disproven
"VRAM doesn't help" guidance with the more precise truth:
**VRAM doesn't unlock streaming directly, but it reduces
the OOM rate that tears streams down**. Those are not the
same statement.

### 13E — Mitigation candidates if upstream fix isn't in reach

**Effort: medium; depends on 13A's findings.**

Possible mitigations the client could attempt:

1. **More aggressive STREAM_REPORT cadence** — phase 1 of
   the master plan landed STREAM_REPORT. If we send positive
   feedback more often when a stream is healthy, the server's
   stream-detector may resist teardown longer. Read
   `mjpeg_encoder_handle_positive_client_stream_report`
   (we see it in the 005 log) to confirm the report is
   actually feeding the survival decision.

2. **Resolution adaptation hint** — if at high res streams
   always die, ryll could send a `MONITORS_CONFIG` suggesting
   the guest use a resolution we know works (1280×800 in
   the 004 matrix). Heavy-handed; not without operator
   consent. Defer.

3. **Codec preference** — phase 7 (PREF_VIDEO_CODEC_TYPE)
   isn't yet implemented. Once it is, biasing toward H.264
   may reduce per-frame encode time on the server enough
   that the OOM cycle decouples from stream survival. Speculative.

None of these go into code until 13A confirms the mechanism.

## Out of scope

- Patching spice-server. If we find a bug, file upstream
  with the minimal reproducer (013B's data set).
- Patching qemu's QXL emulation. Same.
- Patching the guest kernel QXL driver. Same.
- Rebuilding the spice-server with statistics/recorder enabled
  (`--enable-recorder`). Probably useful eventually but
  unnecessary while spice_debug works.

## Cross-references

- [docs/troubleshooting.md § Streaming indicator](/components/ryll/troubleshooting/#streaming-indicator)
  — the live status-bar indicator added in phase 8 is the
  cheapest signal for the OOM-vs-survival investigation here.
  Amber after every short workload run is the visual cue that
  the 005-style "stream lives N seconds then never returns"
  pattern is reproducing; red means the flap heuristic has
  fired and a `Warn` notification with the destroy/lifetime
  numbers is in the bell. Watch it during the 13A reproducer
  rather than scraping snapshots after the fact.
- `/srv/src-reference/spice/spice/server/red-worker.cpp:520-548`
  — `handle_dev_oom` (the OOM handler).
- `/srv/src-reference/spice/spice/server/red-qxl.cpp:328`
  — `spice_qxl_oom` (qemu→worker dispatch).
- `/srv/src-reference/spice/spice/server/display-channel.cpp:2411`
  — `display_channel_debug_oom` (the log line we see).
- Session 005 bundles in
  `private:ryll-test-sessions/sessions/test-session-005{a,b,c}.tar.gz`.
- `docs/libvirt-spice-recommendations.md` — VRAM-vs-streaming
  guidance that needs updating per 13D.
- `ryll-test-sessions/manual-test-instructions/005.md` — the
  instructions that produced the 005 data set.

## Success criterion

Phase 13 is complete when the OOM-vs-streaming relationship
is characterised well enough that:

- We can predict (qualitatively) which guest configurations
  will produce stable streaming and which won't.
- Either (a) an upstream issue is filed with a minimal
  reproducer, or (b) operator guidance for VRAM sizing in
  `docs/libvirt-spice-recommendations.md` is precise enough
  to be load-bearing, or (c) a client-side mitigation that
  measurably improves stream lifetime is identified and
  filed as its own follow-up phase.

"We shipped code" is not the success criterion. "We
understand the failure mode" is.

## Session 006 findings — phase 13B data set

Sessions 006a/b/c ran the prediction matrix at 64/128/256 MiB
VRAM (006d, the fullscreen workload-shape test, was skipped).
Steady-state OOMs/min over 9 minutes of YouTube playback:

| Tag | VRAM | OOMs/min | free_some/run | Δ vs prev |
|-----|------|----------|----------------|-----------|
| 006a | 64 MiB  | **165** | 45 | baseline |
| 006b | 128 MiB | **85**  | 19 | **−48%** |
| 006c | 256 MiB | **77**  | 7  | **−9%** (plateau) |

The diminishing-returns curve predicted by the trace-ring-
contention model held: VRAM helps until ~128 MiB, then
plateaus. `free_some` (work per OOM cycle) dropped sharply
even where OOM count plateaued — the per-OOM eviction depth
is shallower with more VRAM, but the cycle count stops
decreasing.

**Bigger finding from 006:** the YouTube video almost never
crosses `is_stream_start`. Per-tag stream-create breakdown
(server-side `display_channel_create_stream` log lines):

| Tag | 32×10 widget streams | 1024×768 video streams |
|-----|----------------------|------------------------|
| 006a | 99 | **2** |
| 006b | 96 | **1** |
| 006c | 100 | **1** |

So the ~100 stream creates per run are all cursor / scrollbar
flicker; the actual 1024×768 YouTube video gets `STREAM_CREATE`
only 1–2 times in 10 minutes. The video is being delivered as
a bitmap flood: `decode_total_count` is 1500–1600 per run with
bandwidth 2.8–3.5 GB/run, and `streams_created_total = 0`
client-side across all four bundles (006a/b/c/e). The
trace-ring patch (phase 17) would help cursor / scrollbar
flap re-engagement; it does **not** address why the video
itself isn't a stream.

That's a different bottleneck than the one this phase set
out to characterise. Two paths forward, neither cheap, both
parked until the rest of the master plan closes:

1. **Server-side stream-create predicate is hostile to QXL's
   draw shape.** `is_stream_start` requires 20 consecutive
   frames within 200 ms in the same per-region trace slot.
   QXL's batched surface blits may not surface as per-region
   updates at the per-frame cadence the predicate expects.
   Test: read `red_get_streamable_drawable` + the per-frame
   trace-update path; correlate with the QXL command-ring
   walk on a non-streaming run.
2. **Client-side message drop.** Server's
   `display_channel_create_stream` log is at the internal
   stream-create site, before the per-client send decision.
   Would explain why `streams_created_total = 0` despite
   ~100 server-side creates. Test: instrument ryll's
   `MSG_DISPLAY_STREAM_CREATE` handler with a one-shot
   warn-if-not-received-after-T-seconds. Cheap.

Phase 17 (patched libspice) value is now uncertain:
bumping `NUM_TRACE_ITEMS` from 8 to 128 lets more of the
cursor / scrollbar flicker stay engaged, but does not change
whether the YouTube video qualifies as a stream in the first
place. Hold off on building the .deb until the upstream
question is whether the predicate itself is the problem.

## Status — parked

This phase, plus phase 16 (QXL viability) and phase 17
(patched libspice) sit on the video bottleneck. The
non-video work in the master plan should land before any of
the three resumes. The findings above are the snapshot of
what we know at park time; resume by re-reading them and
the open-question-1/2 tests above.
