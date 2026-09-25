# Streaming and the qemu damage series on a shaped link

This page records a re-baseline of keypress-to-draw latency through
Kerbside with spice-server's video streaming turned on, and with the
qemu damage-path series and the Linux damage-clip fix carried in
kerbside-patches. It follows
[Proxy backpressure on a shaped link](/components/kerbside/performance/proxy-backpressure/), whose
rig, guest and link profiles it reuses, and it sizes the transcoding
work in [the SPICE performance plan](/components/kerbside/plans/PLAN-spice-performance/).
Measured on 2026-09-24.

## Summary

- **Most of the 2.1 s baseline was qemu's default configuration.**
  The backpressure measurement ran with `streaming-video` at qemu's
  default, off, which leaves spice-server nothing it can drop. With
  `streaming-video=all`, stock Debian qemu draws a keypress at
  80 ms / 10 Mbit under heavy activity in a 166 ms p50 (389 ms p95),
  against 2136 ms (2453 ms) with streaming off. No code changes are
  involved.
- **`streaming-video=filter` is not a fix.** It never streams the
  pure-noise heavy activity, which its heuristic classifies as
  non-photographic, so heavy stays at 2.1 s. On the smoother busy
  activity at 80 ms / 10 Mbit it was *worse* than streaming off
  (399 ms against 183 ms steady-state p50): it streams some of
  qemu's 32-pixel columns and sends the rest as bitmaps, and the
  mix carries more bytes than either.
- **The qemu damage series (v2) is neutral to better when
  streaming, and much worse when it is not.**
  - With `all`, its steady state matches or beats stock (80 ms /
    50 Mbit heavy: 92 ms p50 against 99 ms), with one stream
    instead of 21 column slivers.
  - Its whole-region drawables are 640x480 bitmaps of about 1.2 MB.
    spice-server's ACK window counts *messages*, not bytes, so
    before a stream forms, or when content never streams, the
    backlog is roughly twenty times larger than with 32-pixel
    columns. At 80 ms / 10 Mbit this costs about 5 s on the first
    presses of every run while spice-server waits for 20 frames
    before creating a stream (the first eight presses have a p50
    near 1 s). With `filter` and non-streamable content it costs
    8 s in steady state, against 2.1 s for stock.
  - This is also the most direct evidence so far for the
    backpressure page's explanation: the ACK window bounds the
    backlog in messages.
- **The kernel damage-clip fix made no difference here.** The
  keydraw guest flushes with `DRM_IOCTL_MODE_DIRTYFB` rectangles,
  and its results are the same with and without the rebuilt
  `drm_kms_helper.ko`. The fix matters for atomic-commit
  compositors, which this guest does not model.
- **What is left for transcoding is much smaller than 2 s.** The idle floor at 80 ms RTT is about 97 ms. With
  `streaming-video=all`, heavy activity at 80 ms / 10 Mbit sits
  about 70-100 ms above that floor at p50 and about 200-300 ms at
  p95. At 80 ms / 50 Mbit and at 20 ms / 50 Mbit it is at the
  floor.

## Caveats

- **Every stream was H.264,** and Ryll cannot decode spice-server's
  H.264 (ryll#398). spice-server chose H.264 because Kerbside
  advertises Ryll's display capabilities to it, not the real
  client's (#477). Key-box latency is unaffected, because the key
  box is always a `DRAW_COPY` bitmap. But the video itself was not
  rendered, and spice-server's stream rate control ran on Ryll's
  stream reports for streams it never decoded, so the stream
  bitrates here may not be what a decoding client would see.
- **`streaming-video=all` streams everything that animates,**
  including scrolling text, which then arrives lossy. This rig
  measures latency, not image quality.
- **spice-server 0.15.2 segfaults when a client connects while
  streams already exist.** Every streaming case in the first
  attempt crashed qemu on connect. The rig now delays the guest's
  activity until the client has connected (`ACTIVITY_DELAY`). This
  would hit real deployments with streaming on: a user reconnecting
  to a VM that is playing video crashes the VM's qemu. It is not
  yet reproduced against spice-server's current master.
- The rig runs Ryll with `-v` for the stream counts below, which
  costs a little client CPU. The backpressure runs did not.
- One host, CUBIC, two passes of 40 presses per cell. The v2 and
  q11 qemu builds are 11.0.50 scratch builds; the Debian qemu is 10.0.13. The rows `q11-all` and
  `sys-all` agree, so the version difference does not explain the
  v2 results.

## Configurations

| Name | qemu | `streaming-video` | Guest `drm_kms_helper` |
|------|------|-------------------|------------------------|
| sys-off | Debian 10.0.13 | off (the backpressure page's configuration) | stock |
| sys-filter | Debian 10.0.13 | filter | stock |
| sys-all | Debian 10.0.13 | all | stock |
| q11-all | 11.0.50 at 30e8a06b64, unpatched | all | stock |
| q11-all-kfix | 11.0.50 at 30e8a06b64, unpatched | all | with the damage-clip fix |
| v2-all | 11.0.50 plus the v2 damage series | all | stock |
| v2-all-kfix | 11.0.50 plus the v2 damage series | all | with the damage-clip fix |
| v2-filter-kfix | 11.0.50 plus the v2 damage series | filter | with the damage-clip fix |

The qemu series and the kernel patch are in kerbside-patches under
`upstream/qemu/` and `upstream/linux/`. The proxy socket options
from the backpressure measurement are off in every case.

## Results

Keypress-to-draw latency in ms, 80 presses per cell. "First 8" is
the median of the first eight presses of each run, "steady" is
everything after them.

| Link | Activity | Display | p50 | p95 | max | First 8 p50 | Steady p50 | Steady p95 | Display Mbit/s |
|---|---|---|---|---|---|---|---|---|---|
| 20 ms / 50 Mbit | busy | sys-off | 139 | 171 | 197 | 133 | 140 | 171 | 43.6 |
| | | sys-filter | 41 | 55 | 59 | 42 | 41 | 55 | 22.2 |
| | | sys-all | 44 | 55 | 58 | 41 | 44 | 53 | 10.5 |
| | | q11-all | 38 | 55 | 57 | 37 | 39 | 55 | 10.6 |
| | | v2-all | 32 | 46 | 49 | 33 | 31 | 46 | 11.0 |
| | | v2-filter-kfix | 33 | 48 | 53 | 31 | 34 | 48 | 11.0 |
| | heavy | sys-off | 265 | 332 | 360 | 254 | 265 | 332 | 43.9 |
| | | sys-filter | 250 | 328 | 367 | 237 | 254 | 328 | 43.7 |
| | | sys-all | 39 | 52 | 57 | 39 | 39 | 52 | 18.9 |
| | | q11-all | 39 | 52 | 60 | 38 | 40 | 51 | 19.1 |
| | | v2-all | 34 | 56 | 68 | 39 | 33 | 56 | 12.2 |
| | | v2-filter-kfix | 1175 | 1267 | 1404 | 1165 | 1176 | 1291 | 45.5 |
| 80 ms / 10 Mbit | busy | sys-off | 183 | 224 | 237 | 182 | 183 | 219 | 8.8 |
| | | sys-filter | 396 | 466 | 526 | 387 | 399 | 466 | 8.9 |
| | | sys-all | 100 | 115 | 118 | 99 | 100 | 115 | 5.5 |
| | | q11-all | 102 | 115 | 120 | 102 | 102 | 115 | 5.6 |
| | | v2-all | 100 | 149 | 203 | 98 | 101 | 165 | 7.3 |
| | | v2-filter-kfix | 107 | 226 | 304 | 100 | 109 | 225 | 7.7 |
| | heavy | sys-off | 2136 | 2453 | 2467 | 1943 | 2166 | 2454 | 9.3 |
| | | sys-filter | 2112 | 2430 | 2472 | 1982 | 2140 | 2430 | 9.3 |
| | | sys-all | 166 | 387 | 474 | 161 | 166 | 389 | 8.2 |
| | | q11-all | 197 | 326 | 484 | 210 | 195 | 348 | 8.2 |
| | | v2-all | 211 | 2386 | 5863 | 990 | 191 | 317 | 7.5 |
| | | v2-filter-kfix | 7940 | 9082 | 9227 | 7940 | 7970 | 9080 | 9.5 |
| 80 ms / 50 Mbit | busy | sys-off | 101 | 115 | 118 | 100 | 101 | 114 | 16.4 |
| | | sys-filter | 101 | 115 | 118 | 106 | 101 | 116 | 14.1 |
| | | sys-all | 101 | 114 | 117 | 98 | 101 | 113 | 6.8 |
| | | q11-all | 99 | 114 | 117 | 96 | 100 | 115 | 6.9 |
| | | v2-all | 90 | 105 | 110 | 92 | 90 | 105 | 8.9 |
| | | v2-filter-kfix | 91 | 107 | 109 | 90 | 92 | 107 | 8.8 |
| | heavy | sys-off | 446 | 562 | 595 | 446 | 446 | 561 | 44.6 |
| | | sys-filter | 449 | 586 | 609 | 443 | 453 | 583 | 44.7 |
| | | sys-all | 101 | 113 | 114 | 103 | 99 | 113 | 13.6 |
| | | q11-all | 101 | 113 | 115 | 99 | 103 | 113 | 13.6 |
| | | v2-all | 94 | 136 | 165 | 95 | 93 | 136 | 8.1 |
| | | v2-filter-kfix | 1482 | 1666 | 1732 | 1480 | 1482 | 1667 | 45.9 |

The `-kfix` rows for q11-all and v2-all are within noise of the rows
without it and are left out; the rig's `summarise.py` prints them.

Display traffic, from Ryll's `-v` log (`ryllana.py` in
kerbside-patches' `upstream/qemu/rig/tools/`), per second of run,
for 80 ms / 10 Mbit:

| Activity | Display | Draws/s | Streams per run | Draw MB/s | Stream MB/s |
|---|---|---|---|---|---|
| busy | sys-off | 308.6 | 0 | 1.09 | 0 |
| | sys-filter | 48.6 | 19, 32x480 | 0.96 | 0.16 |
| | sys-all | 7.9 | 21, 32x480 | 0.02 | 0.65 |
| | v2-all | 1.4 | 1, 640x480 | 0.02 | 0.89 |
| heavy | sys-off | 26.9 | 0 | 1.16 | 0 |
| | sys-filter | 26.9 | 0 | 1.16 | 0 |
| | sys-all | 5.0 | 21, 32x480 | 0.08 | 0.94 |
| | v2-all | 0.9 | 1, 640x480 | 0.17 | 0.77 |
| | v2-filter-kfix | 1.4 | 0 | 1.18 | 0 |

## Rerunning

The rig is `tools/shaped-link/`, as described in
[Proxy backpressure on a shaped link](/components/kerbside/performance/proxy-backpressure/). This run
used its `DISPLAYS` dimension, which selects the qemu binary,
`streaming-video` and guest per case, and delayed the activity past
the client's connect:

```sh
tools/shaped-link/build-guest.sh /tmp/rb/guest-stock
KMS_HELPER=/path/to/fixed/drm_kms_helper.ko \
    tools/shaped-link/build-guest.sh /tmp/rb/guest-kfix
S=qemu-system-x86_64
Q11=/path/to/qemu-build-stock/qemu-system-x86_64
V2=/path/to/qemu-build-v2/qemu-system-x86_64
G=/tmp/rb/guest-stock; K=/tmp/rb/guest-kfix
DISPLAYS="sys-off:$S:off:$G sys-filter:$S:filter:$G sys-all:$S:all:$G \
    q11-all:$Q11:all:$G q11-all-kfix:$Q11:all:$K v2-all:$V2:all:$G \
    v2-all-kfix:$V2:all:$K v2-filter-kfix:$V2:filter:$K" \
ACTIVITY_DELAY=8 WARMUP=10 RYLL_ARGS=-v \
PROFILES='20:50 80:10 80:50' ACTIVITIES='busy:30:3 heavy:30:8' \
CONFIGS=stock:0:0 REPEATS=2 SAMPLES=40 \
RYLL_BIN=/tmp/sl/ryll/target/release/ryll WORKDIR=/tmp/rb/full \
    tools/shaped-link/run-matrix.sh
tools/shaped-link/summarise.py /tmp/rb/full
```

The 96 cases take about 100 minutes. The qemu builds and the rebuilt
kernel module come from kerbside-patches' `upstream/qemu/rig/` and
`upstream/linux/rig/`.
