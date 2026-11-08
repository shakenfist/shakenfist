# Open questions awaiting data

Symptoms we have seen in bug reports or test sessions that we
can't yet characterise, and where speculative synthetic testing
has not been paying off. The job of this page is to be a single
review surface so periodic re-checks — "do we now have enough
data to close any of these?" — have one place to look instead of
needing to re-walk every phase plan.

This is not a TODO list. Known gaps, deferred robustness items,
and missing tests belong in the plan that owns the work. This
page is for *open questions*: symptoms whose cause we don't yet
understand well enough to act on.

## How to use this page

- **Read at the start of each session closeout** — after landing
  the work that came out of a new test-session bundle, walk the
  entries and ask whether the new bundle moves any of them. If
  an entry has accumulated new evidence, move that evidence into
  the linked phase plan and either close the question or sharpen
  it.
- **Add an entry** when a bug report or test session surfaces a
  symptom we can't yet act on. Keep entries thin: symptom in 1–2
  sentences, what data would close it, link out to the relevant
  plan. Don't restate plan content here.
- **Remove an entry** when the linked phase plan reaches a
  conclusion (server bug filed, workaround documented, or
  client-side change shipped). The plan retains the history;
  this page only carries the open question.

## Open questions

### Q1 — Why does spice-server stop re-creating MJPEG streams on QXL guests?

**Linked plan:**
[Phase 13 — streaming intermittency](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-13-streaming-intermittency/)
(parked).

**Symptom history:**

- Session 002e — streamed 17 s / 135 frames at 7.9 fps.
- Sessions 002g / 002h — **same** Debian 11 QXL guest, **same**
  `streaming-video=all` config, zero streams created.
- Session 005b — server created stream 6 at the correct region,
  destroyed it ~8 s later, never re-created it over the
  remaining 10 minutes of the same workload.
- Session 006 — the actual 1024×768 YouTube video crosses
  `is_stream_start` 1–2× per 10 minutes vs ~100 cursor /
  scrollbar create cycles in the same window.

**What would close it:** a side-by-side `G_MESSAGES_DEBUG=all`
qemu log of a streaming and a non-streaming session of the
same workload, read against `spice/server/video-stream.cpp`
heuristics; or a minimal reproducer of either outcome.

**Why parked:** investigation-shaped, server-side. Synthetic
client-level reproduction has not converged across multiple
sessions, and the user-side cost of running another
exploratory session has stopped paying off relative to waiting
for more organic bundles.

### Q2 — Are `build_tcp_frame: payload too large for IPv4` warns reachable post-K2?

**Linked plan:**
[Phase 15 — build_tcp_frame warn](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-15-build-tcp-frame-warn/)
(15B landed; awaiting reproduction).

**Symptom history:**

- Session 004 (Mac, H.264 follow-up) — four warns at ~2.2 MiB
  payload. K2 segmentation was already in tree by then;
  `segment_payload` is the only known caller of
  `build_tcp_frame`, which should make a 2.2 MiB payload
  defensively unreachable.

**Current state:** 15B (one-shot backtrace + caller capture)
landed. Zero fires across the 006 bundles. Two hypotheses
remain: a stale binary on the Mac at session 004, or a caller
`grep` missed.

**What would close it:** one fresh reproduction with the
backtrace firing (identifies the caller), or 3+ further
sessions without a fire (in which case demote the defensive
check at `capture.rs:183` to `debug_assert!` and close).

### Q3 — Which guest-driver shape is the right substrate for video at all?

**Linked plan:**
[Phase 16 — guest-driver viability](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-16-qxl-viability/)
(parked, depends on Q1).

**Symptom history:**

- Sessions 002–005 show QXL hitting stream-flap + OOM eviction.
- `docs/libvirt-spice-recommendations.md` asserts that
  virtio-vga doesn't engage the streaming heuristic at all.
  That assertion has never been measured against real ryll
  bug-report data — it may be true and the resulting UX may
  still be *less bad* than QXL's flap-and-fall-back, because
  predictable bitmap blits ship every frame.

**What would close it:** the three test sessions described in
the phase 16 plan (Debian 13 + QXL, Debian 11 + virtio-vga,
virtio-vga + accel3d) with comparable bundle output, written
up as a decision matrix in
`docs/libvirt-spice-recommendations.md`.

**Why parked:** depends on Q1. If Q1 finds the QXL mechanism
is recoverable, Q3 is confirmation work; if not, Q3 becomes
the main path.

### Q4 — Does bumping `NUM_TRACE_ITEMS` from 8 to 128 fix stream re-engagement?

**Linked plan:**
[Phase 17 — patched libspice validation](/components/ryll/plans/PLAN-stream-caps-and-flap-phase-17-patched-libspice-validation/)
(parked, gated on Q1).

**Symptom history:**

- Phase 13A source read identified `NUM_TRACE_ITEMS = 8`
  (`spice/server/display-channel-private.h:23`) as the binding
  constraint on stream re-engagement under OOM pressure.
- Session 006 confirmed the trace-ring / VRAM diminishing-
  returns curve (165 → 85 → 77 OOMs/min at 64 / 128 / 256 MiB
  VRAM) but did not separate trace-ring contention from
  workload-driven command-ring exhaustion.

**What would close it:** session 007a-patched per phase 17B
(operator install on one hypervisor + 006-shaped workload),
showing or refuting an improvement in stream re-engagement
after the patched libspice-server is in place.

**Why parked:** gated on Q1 confirming the trace-ring-
contention model. If a 006-style follow-up shows the
workload-driven command ring is the real floor, Q4 won't help
and phase 17 is cancelled rather than executed.

### Q5 — Why does the web stream never recover from sustained surface churn?

**Linked plan:**
[webrtc-0.20 phase 01 pre-work, "Found during execution"](/components/ryll/plans/PLAN-webrtc-0.20-upgrade-phase-01-prework/)
(where the symptom was recorded during the baseline soaks).

**Symptom history:**

- During the phase-01 baseline soaks (August 2026), the
  uefi-latency-guest performed a full video-mode reset (SPICE
  surface destroy → 640x480 → recreate 1280x800) on every
  colour change, and the browser stream took a few seconds to
  recover from each one.
- Driving a colour change every 5 s — faster than recovery —
  wedged the viewer on black *permanently*, even though ryll's
  logs showed the surfaces cycling and the encoder restarting.
  At a 30 s cadence, recovery completed every time.

**What would close it:** identifying which layer stops
delivering — encoder output, RTP pump, or browser-side decode —
when a mode-set interrupts an in-progress recovery, and a fix
making recovery convergent at any churn cadence. The wedge
reproduces on demand: `make test-qemu`, `ryll --web`, and a QMP
`sendkey` every 5 s.

**Why parked:** real desktops rarely mode-set repeatedly, so
this is a robustness gap rather than a dogfooding blocker, and
the webrtc-0.20 port (phases 02–04) rewrites the adjacent code
paths — diagnosis before the port would likely be throwaway
work.

### Q6 — What corrupts GLZ cross-frame images during guest boot?

**Linked plan:** none live. The investigation was
[phase 1 of the deferred-debt plan](https://github.com/shakenfist/ryll/blob/7487d2f4cd6767a63bd630d08e2c177dee4d04d0/docs/plans/PLAN-deferred-debt-phase-01-display.md),
retired once its three fixes landed; that pinned copy is the
evidence trail, and the eviction rule it produced is design
decision 8.

**Symptom:** during the early BIOS/GRUB/kernel-boot window a
coloured-noise band appears across the top of the primary
surface. A `tools/pcap-inspect.py draw-copy` over the captured
window showed 9237 DRAW_COPY into surface 0 and nothing else —
no DRAW_FILL, no COPY_BITS — of which 5286 were ZLIB_GLZ_RGB
and 3904 GLZ_RGB, so ~99% of the window is GLZ-family images.
Still listed as a known issue in
[troubleshooting.md](/components/ryll/troubleshooting/).

**Already eliminated:** the three dictionary-management bugs
this was first written around have all landed — `win_head_dist`
eviction, the `IMAGE_FLAGS_CACHE_ME` test before caching, and
the INVALIDATE_LIST and INVAL_ALL_PIXMAPS handlers that clear
the dictionary — and the band still reproduces. The decoder
itself was ruled out separately: control-byte parsing,
pixel_flag and image_flag decoding and the pixel copy loops
were compared against kerbside's Python `glz.py` and found
faithful. A hunt that restarts from either is repeating work.

**Surviving hypotheses,** in the order the retired plan ranked
them:

- **Stride or dimension confusion** between decode and blit —
  the band has horizontal-scanline structure consistent with
  pixels written at width W and read at width W'. Check
  width×height×4 against the scratch-buffer allocation and the
  blit stride.
- **A dictionary entry mutated after insertion.** Eviction
  bounds the dictionary but says nothing about content: if a
  cached image's backing `Vec` is reused or overwritten, a
  reference returns garbled pixels. Check that insertion stores
  a fresh owned `Vec` and that values are never mutated after.
- **ZLIB_GLZ_RGB specifically** — the majority flavour here. A
  short zlib output-buffer estimate would truncate before the
  GLZ footer, rendering the head of the frame and garbage after
  it.
- **Top-down versus bottom-up row order,** which would show
  uninitialised bytes as the garbage rows.
- **Cross-channel dictionary visibility.** The dictionary is
  behind a mutex and callers clone rather than borrow across
  release points, so this is the least likely, but it was never
  verified.

**What would close it:** `tools/pcap-inspect.py timeline
--since-last N` against a bug report's pcap ring buffer to
isolate the DRAW_COPYs that produced the corruption, then a
unit test feeding those image blobs to the GLZ decoder in
isolation and comparing against a reference decode of the same
input.

## When to add a new entry

Add a Q-entry here when **all** of the following hold:

1. A bug report or test session surfaces a symptom we noticed
   but can't yet act on.
2. The cause is not obvious from the bundle, and reading the
   relevant source has not closed it either.
3. We've decided **not** to chase it with a synthetic
   reproduction right now — either because the synthetic
   wouldn't be representative or because the cost-to-signal
   ratio is poor.

If the symptom maps cleanly to existing phase work, link to
that phase. If it doesn't, file a new standalone plan stub and
link to it from the entry. Entries without a linked plan are a
smell — the plan is where the history lives.

One exception: where the investigation came from a plan that has
since been retired, there is no live plan to link, so the entry
carries the evidence inline and links the retired plan's pinned
copy as the trail. Such an entry is longer than the others on
purpose, and it cannot be closed by the linked plan reaching a
conclusion — closing it means folding its content into the
`docs/` page that owns the topic. Q6 is the current example.
