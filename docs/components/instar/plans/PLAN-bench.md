# `instar bench` subcommand

## Status: Complete

Phases 1-8. `instar bench` — the reframed sandboxed-path measurement
(instar's own end-to-end guest→virtio→host I/O path, not qemu's block
layer) with byte-parity on the deterministic surface (header line,
validation, exit codes); reads on all five formats (raw, qcow2 incl.
backing chains, vmdk, vhd, vhdx) and writes on raw and qcow2 incl.
overlays; live-parity integration tests plus coverage and differential
fuzzing, explicitly no cross-version baselines (timing is not
comparable); and docs. instar now implements all 15 qemu-img
subcommands — the parity roster is closed.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (`qemu-img bench` semantics, KVM, virtio,
ioeventfd, TSC/clock sources), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-bench-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

Consult `ARCHITECTURE.md` for the overall system structure (host
VMM, KVM guest, call table, device emulation). Consult `AGENTS.md`
for build commands, project conventions, code organisation, and the
security-model summary. The closest prior art for this plan is
**`PLAN-dd.md`** (a host-orchestrated subcommand that reuses
existing guest read machinery plus a small pure `crates/dd` math
crate) and **`PLAN-map.md`** (a single-image read-only op with a
streamed result and a host renderer). Note one structural
difference from every prior subcommand plan: bench's product is a
*measurement*, so the byte-for-byte cross-validation-against-
`qemu-img` pattern applies only to the deterministic surface (the
startup header line, argument validation, exit codes, and `-w`
side effects on the image) — never to the timing value itself.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what changed
and why.

## Situation

With `bitmap` merged (PR #386), **`bench` is the single remaining
qemu-img subcommand instar does not implement** — instar covers 14
of `qemu-img 10.0.8`'s 15 subcommands, plus the two instar-only
extras `copy` and `config`. Every prior plan deliberately deferred
bench behind a design spike: *"is benchmarking through the KVM
sandbox meaningful?"* (`PLAN-dd.md`, `PLAN-bitmap.md` Future work).

**This plan resolves the design spike in the affirmative, with an
honest reframing.** `instar bench` does not measure what
`qemu-img bench` measures (qemu's block layer over the host page
cache and AIO stack). It measures **instar's own end-to-end
sandboxed I/O path**: guest format-layer request → virtio-block →
ioeventfd → host I/O thread → file I/O. That is exactly the number
that matters to instar's users and developers, for three reasons:

1. **CLI-surface completeness.** With bench, instar becomes a
   drop-in replacement for every qemu-img invocation. Proxmox test
   scripts and Ceph documentation invoke bench (`docs/usage.md`
   lines 288, 510, 909 — priority "Low" but nonzero).
2. **The sandbox-overhead claim becomes reproducible.** The
   OpenStack announcement (`docs/openstack-announcement-email.md`)
   says "early benchmarks suggest performance overhead from the KVM
   sandbox is modest" — but no committed harness produced those
   numbers; there is no criterion bench, no `benches/` dir, no perf
   script anywhere in the repo. `instar bench` vs `qemu-img bench`
   on the same image *is* the sandbox-overhead measurement.
3. **A first-party perf-regression harness.** instar has no way to
   detect an I/O-path performance regression today. A deterministic
   request generator with machine-readable output gives CI and
   developers a stable probe.

**What `qemu-img bench` actually does** (verified against the qemu
source at v10.0.8 and master, and empirically against the local
Debian 10.0.8 binary):

```
qemu-img bench [-c COUNT] [-d DEPTH] [-f FMT] [--flush-interval=N]
               [-i AIO] [-n] [--no-drain] [-o OFFSET]
               [--pattern=PATTERN] [-q] [-s BUFFER_SIZE]
               [-S STEP_SIZE] [-t CACHE] [-w] [-U] FILENAME
```

It issues COUNT format-aware I/O requests (reads by default; writes
with `-w`) of BUFFER_SIZE bytes each, DEPTH in parallel via AIO
callbacks, starting at OFFSET and advancing by STEP_SIZE per
request, wrapping modulo the image size. Key facts the
implementation must honour:

- **Defaults:** count 75000, depth 64, bufsize 4096, step 0
  (meaning "= bufsize"; a literal zero step is unobtainable),
  offset 0, pattern 0, flush-interval 0 (never), drain-on-flush
  true, read-only, cache `writeback`, AIO `threads`.
- **Output is exactly three printf lines**, none suppressed by
  `-q` (verified: `-q` is a no-op for bench):
  ```
  Sending %d %s requests, %d bytes each, %d in parallel (starting at offset %PRId64, step size %d)
  Sending flush every %d requests          [only if flush-interval]
  Run completed in %3.3f seconds.
  ```
  where `%s` is `read`/`write`. No IOPS, no MiB/s, no per-request
  statistics have ever been printed by upstream.
- **Validation is minimal.** Exactly two cross-option checks:
  `--flush-interval` requires `-w` ("--flush-interval is only
  available in write tests"), and `flush_interval < depth` is
  rejected ("Flush interval can't be smaller than depth"). The
  Debian 10.0.8 binary (with backported fuzz fixes, matching
  master) enforces count/depth/bufsize ≥ 1 and pattern ∈ [0,255]
  ("Invalid %s specified. Must be between 1 and 2147483647.").
  `--pattern` without `-w` is **silently ignored**; `--no-drain`
  without `--flush-interval` is silently irrelevant; there is **no
  offset-alignment requirement and no up-front bounds check** of
  offset/count/step against the image size.
- **Offset wrap:** after each request, `offset += step` then wraps.
  10.0.8 wraps `offset %= image_size`, which can leave a request
  overrunning EOF → mid-run EIO, exit 1 (empirically confirmed).
  Master (commit `ff2ab634`, 2025-05) wraps
  `offset %= image_size - bufsize` so wrapped requests never
  overrun. Zero-size images: offset pinned to 0 (10.0.8+).
- **Timing:** `gettimeofday` bracketing *only* the request loop —
  after image open, buffer allocation, and buffer registration;
  before the first submission. The elapsed wall-clock time is the
  sole statistic.
- **Writes are ordinary format-layer writes**: on qcow2 they
  allocate clusters, update L2 tables and refcounts (verified via
  disk-size growth). The write buffer is filled with the pattern
  byte. Quirk: at any depth all in-flight requests share the same
  first-bufsize-bytes buffer slice (`qiov[0]`) — harmless, but it
  means qemu never writes request-distinguishable data.
- **Flush semantics:** with `--flush-interval N` (write tests
  only), after every N completions qemu drains all in-flight
  requests, issues a flush, and resumes; with `--no-drain` the
  flush is issued fire-and-forget without draining.
- **Exit codes:** 0 on success, 1 on every error (validation,
  open failure, mid-run I/O failure).
- **`-t`/`-i`/`-n`** map to qemu block-layer open flags
  (`BDRV_O_NOCACHE`, `BDRV_O_NO_FLUSH`, writethrough,
  `BDRV_O_NATIVE_AIO`, `BDRV_O_IO_URING`). `-U` is force-share.

**What instar's machinery offers and where it constrains bench**
(surveyed on this branch, tip `cdfc974`):

- **Subcommand anatomy is well-trodden.** `enum Commands` +
  `BenchArgs` + `run_bench` in `src/vmm/src/main.rs`, a new
  `src/operations/bench/` no_std guest op loaded at
  `OPERATION_LOAD_ADDR` (0x30000, 768 KiB budget since the
  2026-07-06 memory-map lift; 0x22000/376 KiB at survey time),
  config in via
  `OPERATION_CONFIG_ADDR` (4 KiB max), results back via a typed
  protobuf message over the serial channel. Touch list: op crate +
  linker.ld, `src/Cargo.toml` workspace members, `src/build.sh`
  stanzas, `scripts/check-binary-sizes.sh` op list,
  `src/shared/src/lib.rs` (`BenchConfig`/`BenchResult` +
  call-table sender), `crates/guest-protocol/proto/guest.proto`,
  `src/core/src/main.rs` shim, `src/vmm/src/main.rs`.
- **The guest block I/O path is strictly synchronous.** The guest
  virtio driver (`src/core/src/virtio.rs`) issues one request at a
  time through a single fixed DMA bounce buffer (header + data +
  status at `DMA_POOL_BASE`), kicks, then busy-waits on the used
  ring. There is no interrupt-driven completion, no second DMA
  slot, and therefore **no queue depth**. The host side is also
  serial: one I/O thread per device drains available descriptors
  in-order with synchronous seek+`read_exact`/`write_all` on a
  plain `File` (`src/vmm/src/io_thread.rs`,
  `src/vmm/src/virtio/block.rs`, `src/vmm/src/backing.rs`). A
  faithful `-d` needs a driver rework on both sides.
- **A single virtio transfer caps at 64 KiB** (`MAX_SECTOR_SIZE`,
  `src/shared/src/lib.rs`); qemu accepts `-s` up to 2 GiB. Larger
  bench buffers must be composed from multiple transfers.
- **The guest has no time source at all.** No rdtsc, no kvmclock,
  no PIT/HPET anywhere in guest code; the host does not program
  TSC. The host has `VmmStats` (`src/vmm/src/stats.rs`) with
  `Instant`-based whole-run elapsed only. Bench timing must be
  host-measured, bracketed by guest-emitted markers to exclude
  boot/setup — which conveniently mirrors qemu's own bracket
  placement.
- **Format-aware reads are reusable.** The qcow2 crate's
  `read_bytes_cached`/backing-chain read path and the shared
  `read_cluster_sectors` helper give byte-accurate virtual-range
  reads for every supported format; `convert`/`dd` are the
  reference consumers. Format-aware **allocating writes into an
  existing image** exist only inside specific ops (commit writes
  overlay clusters into the backing image; bitmap/snapshot
  allocate metadata clusters) — there is no general "write
  virtual range" facility, so `-w` on structured formats needs a
  reuse assessment (Open question 4).
- **Call table discipline:** new senders append at the end;
  `CallTable::VERSION` is 19 (bitmap bumped 18→19). Bench appends
  and bumps 19→20. `core.bin` sits at 66.7 KiB of its 72 KiB
  budget (~5.3 KiB headroom) — one more sender shim should fit but
  must be re-measured (the bitmap plan flagged this exact risk).
  *(Superseded after phase 2: the shims fit at 94% of 72 KiB, and
  the whole memory map was then lifted on 2026-07-06 — core budget
  now 128 KiB at 53%, op region 768 KiB, data pages at 0xF0000;
  see the OPERATION_LOAD_ADDR history in `src/shared/src/lib.rs`.)*
- **CLI posture precedents:** `--image-opts` rejected everywhere;
  `-U` accepted as a documented no-op (no locking exists); dd's
  `-f` warn-then-ignore is the "accepted but deferred" pattern;
  `-t`/`-i`/`--aio` are accepted nowhere today.
- **No image locking** exists anywhere in instar; `-w` mutates the
  target image with no confirmation in qemu, and parity says we do
  the same (documented loudly in `docs/bench.md`).

## Mission and problem statement

Implement `instar bench` such that:

1. It accepts the `qemu-img bench` 10.0.8 surface:
   ```
   instar bench [-c COUNT] [-d DEPTH] [-f FMT] [--flush-interval=N]
                [--no-drain] [-o OFFSET] [--pattern=PATTERN] [-q]
                [-s BUFFER_SIZE] [-S STEP_SIZE] [-w] [-U]
                [--output {human,json}] FILENAME
   ```
   with instar's established postures for the flags that have no
   analog: `--image-opts`, `-t CACHE` (other than the default
   `writeback`), `-i AIO`, and `-n` are **rejected at runtime**
   with a clear "not yet supported" error (Open question 6); `-U`
   and `-q` are accepted no-ops (matching instar's no-locking
   posture and qemu bench's own unguarded printfs respectively).
   Defaults match qemu exactly: count 75000, depth 64, bufsize
   4096, step = bufsize, offset 0, read test, pattern 0.

2. Its deterministic output is **byte-identical to qemu-img**: the
   `Sending N read|write requests, B bytes each, D in parallel
   (starting at offset O, step size S)` header, the optional
   `Sending flush every N requests` line, and the
   `Run completed in %3.3f seconds.` completion line (value
   differs; format identical). Validation errors carry
   qemu-comparable messages and every failure exits 1.

3. The benchmark runs **inside the KVM guest through the format
   layer** for every supported input format (raw, qcow2 incl.
   backing chains and compressed clusters, vmdk, vhd, vhdx), so the
   reported number measures instar's real sandboxed I/O path.
   There is deliberately **no host-only raw fast path** (unlike
   `create`/`resize`): comparability across formats and honesty of
   the measurement outrank speed (Open question 9).

4. Timing is measured on the host with `Instant`, bracketed
   between a guest "bench starting" marker (emitted after config
   parse, format probe, and buffer setup — mirroring qemu's
   bracket) and the `BenchResult` message. VM boot, binary load,
   and device setup are excluded.

5. `-d DEPTH` is accepted and echoed in the header line for output
   parity, but v1 executes requests **serially** (the guest driver
   is synchronous); the divergence is documented prominently and
   surfaced in `--output json` as `"effective-depth": 1` (Open
   question 1). True queue depth is Future work.

6. `-w` performs real format-layer writes with the pattern byte:
   raw and qcow2 in v1 (allocating writes included, reusing the
   commit-path machinery per the Phase 1 assessment), vmdk / vhd /
   vhdx refused with "not yet supported" (Open question 4).
   `--flush-interval` issues a real flush (fsync via the call
   table) every N requests; `--no-drain` is accepted (with serial
   execution the queue is always drained — a documented no-op).

7. `--output json` is an instar-only extension (same posture as
   bitmap's): the default path prints qemu's three lines and
   nothing else; `--output json` emits a machine-readable object
   (parameters, elapsed seconds, derived requests/sec and
   bytes/sec, effective depth, flush count) for the
   perf-regression use-case.

8. Correctness is tested where determinism allows: a pure
   `crates/bench` request-schedule crate with unit tests and a
   coverage-fuzz target; integration tests asserting header-line
   byte-parity against the local qemu-img, validation/exit-code
   contracts, and `-w` side effects (pattern actually lands,
   `qemu-img check` clean afterwards, disk-size growth on qcow2);
   a differential-fuzz `op_bench` limited to the deterministic
   surface. **Cross-version baselines are explicitly N/A** —
   the completion line embeds a timing value and dedup would be
   meaningless (Open question 13 records the decision).

**Out of scope for v1** (see Future work): true queue depth >1;
guest-side per-request latency statistics (TSC plumbing); `-t
none`/`directsync` via `O_DIRECT`; write tests for vmdk/vhd/vhdx;
`-i`/`-n` AIO backends (no analog); buffer sizes above the v1 cap
(Open question 2); a CI perf-tracking job consuming
`bench --output json`.

## Open questions

These need resolution during detailed (phase) planning. Each is a
real fork grounded in what the code does today and the verified
qemu 10.0.8/master behaviour.

1. **Depth semantics (`-d`).** qemu defaults to 64 requests in
   flight; instar's guest driver is submit-one-spin-until-done
   with a single DMA bounce buffer, and the host services
   descriptors serially anyway. Fork: (a) accept `-d`, echo it in
   the header for byte-parity, execute serially, document the
   divergence; (b) refuse `-d > 1` (breaks the default invocation
   and every real-world example, e.g. the Ceph `-d 16` line); (c)
   rework the guest driver (per-slot DMA buffers, batched avail
   entries, deferred completion polling) and the host (parallel
   request service) for real depth. **Leaning strongly to (a) for
   v1** with (c) recorded as Future work — (c) is a rewrite of the
   most safety-critical guest/host boundary code for a Low-priority
   subcommand, and even a batched guest submits to a host that
   processes serially. Confirm in Phase 1 that the header echoes
   the *requested* depth (parity) while JSON reports effective
   depth.

2. **Buffer sizes above 64 KiB (`-s`).** `MAX_SECTOR_SIZE` caps a
   single virtio transfer at 64 KiB; qemu accepts up to 2 GiB.
   Fork: split one bench request into ceil(bufsize/64Ki)
   sequential transfers (counts as one request, contiguous
   offsets) vs refuse `-s > 65536`. **Leaning to split, with a v1
   cap of 2 MiB** (`MAX_CLUSTER_SIZE`, comfortably inside the
   12.9 MiB scratch region for staging) and a clear refusal above
   the cap. The split preserves request semantics (one request =
   bufsize bytes at one offset) at the cost of extra round trips —
   a documented measurement caveat. Decide the exact cap and the
   scratch-buffer placement (check's top-of-scratch idiom) in
   Phase 1/3.

3. **Timing protocol.** No guest clock exists. Fork: (a) host
   `Instant` bracketing between a guest start-marker and the
   result message — matches qemu's bracket placement (post-setup,
   pre-first-submission), needs only a marker mechanism; (b) TSC
   plumbing (`KVM_GET_TSC_KHZ` → `VMM_PARAMS_ADDR`, guest
   `rdtsc`) enabling per-request latencies. **Leaning to (a) for
   v1; (b) is Future work** (it would enable percentile stats in
   JSON, but adds net-new time-source plumbing and calibration
   risk for v1's total-elapsed-only output). Decide in Phase 2
   whether the start marker is a dedicated call-table slot
   (`send_bench_start`, cleanest, +1 slot) or a sentinel
   `send_progress` message (zero ABI growth, slightly hacky).
   Serial-port vmexit latency (~µs) is noise at bench's ms-to-s
   scale.

4. **Write-test format coverage (`-w`).** raw writes are trivial
   (`write_output_sector` on the attached RW device). qcow2 writes
   to arbitrary virtual offsets need allocate-on-write (L2 update,
   refcount increment, file growth) against an *existing* image —
   machinery that exists today only inside the commit op's
   overlay-into-backing path and the bitmap/snapshot metadata
   allocators. **Assessed in step 1d — see 'Findings:
   allocating-write reuse' under Administration and logistics.**
   Decision at master-plan review before Phase 5 is planned. If
   cheap: v1 ships `-w` for raw + qcow2 (the two
   formats real bench invocations use — the Ceph example is
   `-w` on qcow2). If not: v1 ships `-w` raw-only, qcow2 write
   becomes its own follow-up, and the plan's phase table is
   adjusted at review. vmdk/vhd/vhdx writes are out of scope
   either way.

5. **Flush wiring.** `--flush-interval` needs a flush primitive on
   the *benched* device. The call table has `fsync_input`
   (snapshot's write barrier, VERSION 17). Confirm in Phase 2
   whether bench attaches the image as an RW input device (commit/
   snapshot/bitmap style — then `fsync_input` just works) or as
   the output device (then an `fsync_output` slot may be needed).
   Leaning to the RW-input attach for `-w` (it is the established
   in-place-mutation idiom) and read-only input attach for read
   tests, making `fsync_input` sufficient with zero new I/O ABI.

6. **`-t` / `-i` / `-n` posture.** No analog exists: instar's
   backing store is a plain `File` with seek+read/write; qemu's
   cache modes toggle `O_DIRECT`/flush-suppression/writethrough at
   image open. Fork: reject non-default values vs accept-and-warn
   (dd's `-f` pattern) vs implement `-t none` via `O_DIRECT`.
   **Leaning: accept `-t writeback` (the qemu default) silently,
   reject every other cache mode and all of `-i`/`-n` with "not
   yet supported"** — accept-and-warn would make e.g. `-t none`
   numbers silently mean something different from what the caller
   asked for, which in a measurement tool is worse than refusal.
   `O_DIRECT` support is Future work (alignment constraints on
   the host buffer path).

7. **Offset wrap semantics.** 10.0.8 wraps `% image_size` and can
   EIO mid-run when a wrapped request overruns EOF; master fixed
   this (`% (image_size - bufsize)`, commit `ff2ab634`). instar's
   baseline oracle is 10.0.8, but replicating a known qemu bug
   that turns a benchmark into an error seems perverse. **Leaning:
   implement master's fixed semantics, record the 10.0.8 delta in
   the divergence registry, and keep integration tests out of the
   wrap-overrun window when cross-validating against the local
   10.0.8 binary.** Also replicate the zero-size-image guard
   (offset pinned to 0) and 10.0.8's raw first-`-o` behaviour
   (the initial offset is *not* wrapped; `-o` at/past usable EOF
   fails the first request — match the master behaviour here too,
   decide exact error text in Phase 1).

8. **Request-schedule crate.** A pure `no_std` `crates/bench`
   holding: the offset-sequence iterator (initial offset, step,
   wrap rule), the transfer-split plan for bufsize > 64 KiB, the
   flush-point predicate (`remaining % interval == 0` semantics
   with qemu's exact remaining-computation), and all numeric
   validation — shared by host (validation) and guest (execution),
   unit-tested and coverage-fuzzed. Small, but it pins the wrap
   arithmetic and flush cadence in one testable place (the
   `crates/dd` precedent). **Leaning yes.** Confirm crate shape in
   Phase 1 (it may be ~200 lines — that is fine).

9. **Always-through-the-guest, even raw.** `create`/`resize` have
   host-only raw fast paths (no untrusted parsing needed). For
   bench the measurement *is* the product: a host-only raw path
   would report numbers that bypass the sandbox and be
   incomparable with every other format's numbers. **Leaning:
   always launch the guest, all formats.** The cost (a ~few-ms
   boot excluded from timing anyway) is irrelevant. Confirm nobody
   needs a "measure the host path" mode — if they do, that is an
   explicit instar-only flag, Future work.

10. **ABI footprint and `core.bin` budget.** `BenchConfig` (magic,
    count, depth, bufsize, step, offset, flags: is_write /
    no_drain / quiet, pattern, flush_interval, format hint,
    host-probed cross-checks) fits trivially in the 4 KiB config
    page. `BenchResult` (error code, requests completed, flushes
    issued, failed-request errno-analog). One or two appended
    call-table slots (`send_bench_result`, optionally
    `send_bench_start` per Open question 3) bump VERSION 19→20.
    `core.bin` has ~5.3 KiB headroom; **Phase 2 must re-measure**
    (`scripts/check-binary-sizes.sh`) and if the shim(s) overflow,
    choose between trimming, the sentinel-progress start marker
    (saves one shim), or the memory-map budget lift that bitmap's
    Future work already anticipates.

11. **`--output json` schema.** Proposed:
    `{"filename", "format", "count", "depth", "effective-depth",
    "buffer-size", "step-size", "offset", "write", "pattern",
    "flush-interval", "no-drain", "flushes-issued",
    "elapsed-seconds", "requests-per-second", "bytes-per-second"}`.
    qemu has no JSON for bench, so keys are instar's choice —
    follow the hyphenated-key house style (`check`/`measure`).
    Decide in Phase 4 whether derived rates belong in the output
    or are left to consumers (leaning: include them; the
    perf-tracking use-case wants them and recomputation invites
    rounding drift).

12. **What does the guest do between transfers of one request?**
    For structured formats a single bench request may resolve to
    multiple host-cluster reads (compressed clusters, chain
    fallthrough, sub-cluster reads) — qemu's requests hit the same
    machinery, so this is *correct* measurement, but Phase 3 must
    ensure the per-request work excludes avoidable instar-only
    overhead (e.g. re-probing the format or re-walking L1 per
    request instead of reusing the op-lifetime cached state the
    format crates already provide — `read_bytes_cached` exists for
    exactly this). The bench loop must reuse cached mapping state
    across requests like convert does within a run.

13. **Testing posture for a nondeterministic command.**
    Decided-by-leaning here, confirm at review: (a) integration
    tests never assert timing values — they assert the header line
    byte-equal to the local qemu-img's for identical arguments,
    completion-line *shape* (regex on `Run completed in \d+\.\d{3}
    seconds\.`), exit codes, refusal messages, and `-w` side
    effects (pattern read-back via `qemu-img compare`/instar
    `compare`, post-op `qemu-img check` clean, qcow2 allocation
    growth); (b) **no cross-version baseline matrix** — the
    deterministic header depends only on arguments, not qemu
    version (verified stable v6→master modulo the master-only CLI
    refactor), so baselines would record only the timing line,
    which cannot be compared; (c) differential fuzz `op_bench`
    compares header lines, exit codes, and post-`-w` image state
    (via `qemu-img check` + content hash) across randomized
    argument sets, never durations; (d) the coverage-fuzz target
    exercises `crates/bench`'s schedule/validation invariants
    (offsets in bounds under wrap, transfer splits cover exactly
    bufsize, flush cadence matches the qemu remaining-rule).

## Execution

Phase plans are written one at a time, at the recommended effort,
and reviewed before the next is drafted. The shape is lighter than
the mutation subcommands (no net-new on-disk format parsing, no
refcount surgery in the core path) but adds two bench-specific
elements: the timing bracket in the ABI phase and the
write-machinery reuse assessment in Phase 1.

| Phase | Plan | Status |
|-------|------|--------|
| 1. Semantics pin + `crates/bench` schedule crate: encode the verified qemu contract (defaults, validation bounds and messages, wrap rule per OQ7, flush cadence, transfer-split plan per OQ2) as a pure `no_std` crate with inline unit tests; assess commit-path reuse for allocating qcow2 writes (OQ4) and settle OQ1/2/7/8 | [PLAN-bench-phase-01-crate.md](/components/instar/plans/PLAN-bench-phase-01-crate/) | Complete (steps 1a-1e; `crates/bench` at 43 tests; flush formula verified against the live binary; qemu message contract captured; OQ4 findings recorded — qcow2 `-w` via reuse-and-compose recommended for Phase 5) |
| 2. ABI: `BenchConfig`/`BenchResult` in `src/shared`, `send_bench_result` (+ start-marker decision, OQ3/5) appended to the call table (VERSION 19→20), proto message, core shim; re-measure `core.bin` budget (OQ10) | [PLAN-bench-phase-02-abi.md](/components/instar/plans/PLAN-bench-phase-02-abi/) | Complete (steps 2a-2c; ABI landed at call-table VERSION 20; BenchConfig 128 B / BenchResult 64 B; core.bin at 68416 B = 94% of the 72 KiB region, hard gate passes, dedicated send_bench_start slot kept) |
| 3. Guest op read path (`src/operations/bench/`): config parse, format probe, cached-state request loop over the schedule crate for raw/qcow2(+chains,+compressed)/vmdk/vhd/vhdx, start-marker + result emission; build + size gate only (not functionally testable until Phase 4) (OQ12) | [PLAN-bench-phase-03-guest-read.md](/components/instar/plans/PLAN-bench-phase-03-guest-read/) | Complete (steps 3a-3c; one `read_chain_virtual_range` call per request over cached `ChainStates`; timing bracket and no-progress contract implemented; EOF pre-check compensates for the chain reader's zero-fill-past-EOF contract; bench.bin 148352 B = 18% of the 768 KiB region; functional smoke test lands with phase 4) |
| 4. Host CLI: `BenchArgs` clap surface, qemu-parity validation (Debian-10.0.8 bounds + the two cross-option rules), rejection postures (`--image-opts`/`-t`≠writeback/`-i`/`-n`, OQ6), `run_bench` (chain discovery, device attach, config populate, `Instant` bracket, header/completion rendering, `--output json` per OQ11); **first end-to-end smoke test** vs qemu-img | [PLAN-bench-phase-04-host-cli.md](/components/instar/plans/PLAN-bench-phase-04-host-cli/) | Complete (steps 4a-4c; OQ6 resolved: `-t writeback` silent, other cache modes / `-i` / `-n` refused; OQ11 resolved: json replaces human output, derived rates included; validation messages byte-pinned to the 1e capture including the corrected echo/range forms from Supplement 2; elapsed captured at BenchResult arrival; end-to-end verified against local qemu-img 10.0.8 on all five formats (raw incl. multi-transfer split and offset/step variants, qcow2 plain, qcow2+backing chain, compressed qcow2, vmdk, vhd, vhdx) with byte-identical headers; smoke parity results and a `KNOWN_BENCH_DIVERGENCES` starter list captured in the phase-4 plan's "Captured smoke results" section; a CLI-wiring defect found by the smoke run — negative numeric option values (`-c -1`, `-o -1`, etc.) hit a clap usage error instead of the qemu-parity message — was fixed in-phase with `allow_hyphen_values` on all seven numeric options plus clap-level regression tests, and all affected rows re-verified as parity) |
| 5. Write test: `-w`/`--pattern`/`--flush-interval`/`--no-drain` for raw + qcow2 (allocating writes per the Phase 1 assessment, OQ4), RW attach + `fsync_input` flush wiring (OQ5), vmdk/vhd/vhdx refusal, post-write self-check | [PLAN-bench-phase-05-write.md](/components/instar/plans/PLAN-bench-phase-05-write/) | Complete (steps 5a-5c: raw write path and qcow2 allocating writes both land, byte-identical to `qemu-img bench` on raw and virtual-view-identical (`qemu-img compare`) plus `qemu-img check`-clean on qcow2/overlay across the full {raw, fresh qcow2, populated qcow2, overlay} × {defaults, `--pattern`, `--flush-interval -d 1`, `--no-drain`, cluster-straddling unaligned} matrix; overlay COW-fill proven with a spot-proof read outside the patch window; every write-envelope gate (snapshot-bearing, `refcount_bits=1`, compressed, vmdk/vhd/vhdx, LUKS) refuses without touching the image (sha256-confirmed); `--flush-interval` without `-w` still gets qemu's cross-option message; `flushes-issued` matches `crates/bench::total_flushes` end-to-end in `--output json`; one pre-existing qemu 10.0.8 wrap-rule divergence recorded on raw, not a defect — see the phase-5 plan's "Captured write verification" section) |
| 6. Integration tests (`tests/test_bench.py`): header-line byte-parity matrix vs local qemu-img, validation/exit-code contracts matched to host messages, `-w` side-effect verification (pattern read-back, `qemu-img check` clean, qcow2 growth), wrap-window behaviour, `KNOWN_BENCH_DIVERGENCES` registry (depth serialization, wrap rule vs 10.0.8, cache-mode refusals); explicit no-baselines decision recorded (OQ13) | [PLAN-bench-phase-06-integration.md](/components/instar/plans/PLAN-bench-phase-06-integration/) | Complete (steps 6a-6b; 62 tests in seven classes converting the 4c/5c capture matrices, file-scoped 62/62 in ~2.4 s; KNOWN_BENCH_DIVERGENCES with 11 entries plus a map-style divergence-regression class; live parity against the host qemu-img replaces baselines per OQ13; full suite green at 2476 tests / 0 failures in 13m0s 16-way — bench tests order-free across 10 workers, no cross-test interference) |
| 7. Fuzz: `fuzz_bench_schedule` coverage target (schedule/validation invariants) registered in the nightly tiers; differential `op_bench` in `scripts/differential-fuzz.py` over the deterministic surface only (OQ13) | [PLAN-bench-phase-07-fuzz.md](/components/instar/plans/PLAN-bench-phase-07-fuzz/) | Complete (steps 7a-7c; fuzz_bench_schedule in the fast tier, 1.3M crash-free smoke execs; op_bench registered with a schedule-linear steer-around picker, 100-iteration local run clean) |
| 8. Docs (closes the plan): `docs/bench.md` user guide — **what the number means** (sandboxed-path measurement, sandbox-overhead methodology vs `qemu-img bench`, serialized-depth caveat), divergence table, destructive-`-w` warning; `docs/usage.md` section + parity-table row update; `ARCHITECTURE.md`/`AGENTS.md`/`README.md`/`CHANGELOG.md`; plans index status → Complete | [PLAN-bench-phase-08-docs.md](/components/instar/plans/PLAN-bench-phase-08-docs/) | Complete (steps 8a-8c; docs/bench.md landed; root docs wired incl. the ARCHITECTURE memory-map and VERSION repairs; success criteria verified) |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. The workflow per step:
**plan** (high effort) → **spawn a sub-agent** with the brief →
**review the actual files** (the summary describes intent, not
necessarily what changed) → **fix or retry** (improve the brief or
upgrade the model) → **commit** once satisfied. Use
`isolation: "worktree"` for risky/experimental steps.

### Planning effort

The master plan is created at **high effort**. Phase 1 (the
semantics pin, wrap-rule fork, and the commit-path write-reuse
assessment) and Phase 2 (ABI, timing-bracket design, core budget)
should be planned at **high effort**. Phase 5 (allocating qcow2
writes) is **high effort** if the Phase 1 assessment finds the
reuse nontrivial, medium otherwise. Phases 3, 4, 6, 7, and 8
follow well-established patterns (map/dd lifecycles, the
established test/fuzz/docs shapes) and can be planned at **medium
effort** once the briefs front-load the research above.

### Step-level guidance

Each phase plan includes a step table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels** — high: multiple files, judgment calls,
non-obvious invariants, external spec research. medium: clear
brief, well-defined approach. low: purely mechanical.

**Model choice** — opus for deep reasoning / cross-file
architectural understanding / subtle correctness (the timing
bracket across the vCPU loop, the allocating-write reuse, anything
touching `src/core/src/virtio.rs`). sonnet for well-briefed
implementation. haiku for mechanical tasks. **When in doubt, skew
to the more capable model** — a failed implementation wastes more
than a heavier model costs. A detailed brief compensates for a
lighter model.

**Brief for sub-agent** — write it as if briefing a colleague who
has never seen the codebase: what to change, which files to touch,
what patterns to follow, and the non-obvious constraints (the
768 KiB guest binary cap and 128 KiB core budget, the `no_std`
requirement of `crates/bench`, the call-table append-only
discipline, the 64 KiB `MAX_SECTOR_SIZE` transfer cap, the
single-DMA-buffer synchronous driver, byte-for-byte output parity
on the deterministic lines only). Front-load the research this
plan already did — e.g. instead of "print the qemu header", give
the exact printf contract: `Sending %d %s requests, %d bytes
each, %d in parallel (starting at offset %PRId64, step size %d)`
with `%s` ∈ {`read`,`write`}, unguarded by `-q`, offset rendered
as a raw decimal byte value.

### Management session review checklist

After a sub-agent completes, verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (768 KiB op
      budget; `bench.bin` registered in the script's op list), and
      `core.bin` still fits its 128 KiB `.bss`-inclusive budget
      after the call-table addition.
- [ ] `make test-rust` (including the new `bench` crate) passes.
- [ ] Relevant `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — semantically,
      not just syntactically.
- [ ] Commit message follows project conventions (Co-Authored-By
      with model, context window, effort level, and other
      settings; `Signed-off-by`; `Prompt:` paragraph).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented because
the following statements will be true:

* `instar bench <image>` runs the default read benchmark (75000 ×
  4096-byte requests) through the KVM guest on raw, qcow2
  (including backing chains), vmdk, vhd, and vhdx images, and its
  first and last stdout lines are format-identical to
  `qemu-img bench`'s (the header byte-equal for equal arguments,
  the completion line differing only in the elapsed value).
* `instar bench -w --pattern 65 -c N -s 4096 <image>` on raw and
  qcow2 writes the pattern through the format layer:
  the pattern is verifiable by read-back, `qemu-img check` is
  clean afterwards, and a fresh qcow2's disk size grows —
  matching the empirically verified qemu behaviour.
* `--flush-interval N` issues real flushes every N requests in
  write tests; `--flush-interval` without `-w` and
  `flush_interval < depth` are refused with qemu's messages;
  count/depth/bufsize < 1 and pattern > 0xff are refused with the
  Debian-10.0.8-compatible bounds messages; `--pattern` without
  `-w` is silently ignored; every failure exits 1.
* Running `instar bench` and `qemu-img bench` with identical
  arguments on the same image constitutes a reproducible
  sandbox-overhead measurement, and `docs/bench.md` documents the
  methodology and its caveats (serialized depth, transfer
  splitting, what is and is not included in the bracket).
* `--output json` emits the agreed schema; the default output is
  exactly qemu's lines.
* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes`; `bench.bin` is
  registered in `scripts/check-binary-sizes.sh`; `core.bin` fits
  its 128 KiB budget after the VERSION 19→20 call-table append.
* All Rust unit tests pass (`make test-rust`), including
  `crates/bench`.
* All Python integration tests pass (`make test-integration`),
  including `tests/test_bench.py`.
* `fuzz_bench_schedule` runs clean in the nightly tiers;
  differential `op_bench` runs clean over the deterministic
  surface.
* `pre-commit run --all-files` passes.
* Documentation is updated: `docs/bench.md`, `docs/usage.md`,
  `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`,
  `docs/plans/index.md`, `docs/plans/order.yml`.
* With this plan complete, **instar implements all 15 qemu-img
  subcommands** — the parity roster is closed.

### Future work

Obvious extensions deferred from v1 (the last three were added by
the pre-push audit's advisory findings):

* **True queue depth.** Rework the guest virtio driver (per-slot
  DMA buffers, batched avail-ring submission, completion polling
  across slots) and the host service path (parallel request
  processing per device) so `-d` means what it means in qemu. This
  is a rewrite of the most security-sensitive guest/host boundary
  code and should be its own plan with its own audit — it would
  also benefit convert/dd throughput, which is the stronger
  motivation.
* **Guest-side latency statistics.** TSC plumbing
  (`KVM_GET_TSC_KHZ` → `VMM_PARAMS_ADDR`, guest `rdtsc`
  bracketing each request) enabling min/max/percentile latencies
  in `--output json`.
* **`-t none` / `directsync` via `O_DIRECT`** on the host backing
  store (alignment constraints on the bounce path), making cache
  mode a real variable in the measurement.
* **Write tests for vmdk / vhd / vhdx** (each needs its own
  allocate-on-write path into existing images).
* **A CI perf-tracking job** running a pinned
  `bench --output json` matrix on the self-hosted runners and
  alerting on regression thresholds — the actual consumer of the
  regression-harness use-case.
* **Buffer sizes above the v1 cap**, if a real workload asks for
  them (needs a streaming transfer plan rather than a staged
  scratch buffer).
* **A host-path measurement mode** (explicit instar-only flag), if
  a concrete need to separate sandbox overhead from file-I/O cost
  appears (Open question 9 decided v1 is guest-only).
* **Extract the byte-range RMW helper.** bench's
  `write_input_byte_range`/`read_input_byte_range` are the third
  and fourth near-identical copies of the ~35-line
  sector-straddling read-modify-write primitive (commit and
  bitmap carry the others). A shared helper (likely in
  `src/shared`) should replace the copies; the allocator
  precedent (`snapshot::qcow2`) shows the shape. (Pre-push audit
  2a.)
* **Cap `rw_capacity_hint` at an absolute ceiling.** The bench
  `-w` capacity hint derives from a hostile qcow2 header's
  `virtual_size` (`max(file, virtual)·2`, floored at 1 GiB).
  Real growth is bounded by the guest allocator's
  `WRITE_MAX_REFBLOCKS` coverage (~64 GiB) and exceeding it needs
  a guest RCE, matching the sister RW ops' posture — but a sane
  absolute cap would be cheap defense-in-depth. (Pre-push audit
  2d, rated low.)
* **Exercise the unhit write-error paths.** `ERROR_ALLOC_EXHAUSTED`
  (needs an image whose populated refblocks exceed the staging
  budget), mid-run `ERROR_IO_WRITE`/`ERROR_IO_FLUSH` (need fault
  injection), and the write-loop EOF-overrun branch have no test
  coverage at any level; they share logic with exhaustively
  tested paths but carry zero direct evidence. (Pre-push audit
  2b.)

### Findings: allocating-write reuse for `-w` on qcow2 (OQ4, step 1d)

Investigation-only reading of the commit op end-to-end
(`src/operations/commit/src/main.rs`, `src/crates/commit/`), the
snapshot allocator/refcount machinery (`src/crates/snapshot/`),
the bitmap guest op's staged-write pattern
(`src/operations/bitmap/src/main.rs`), and convert's fresh-image
writer for contrast. **Verdict: reuse is real and cheap.** There
is no single callable "write N bytes at virtual offset X with
allocation" function today, but every constituent primitive
already exists as pure `no_std` code and is already reused across
commit / snapshot / bitmap / check. The commit guest op at
`src/operations/commit/src/main.rs:665-785` is a working,
tested, end-to-end inlined composition of exactly the
allocate-on-write sequence `-w` needs (allocate + zero an L2
table if the L1 slot is empty, allocate a data cluster if the L2
slot is empty, write the data, set both `OFLAG_COPIED` flags,
then flush metadata in dependency order). It differs from bench
only in that it *copies overlay cluster data* rather than filling
a pattern buffer, and targets a *separate backing device* rather
than the image under test. Lifting that sequence into the bench
write loop is medium effort with **zero new I/O ABI**.

| Machinery | Reusable for bench `-w`? | Evidence (file:line) | Notes |
|-----------|--------------------------|----------------------|-------|
| **commit planner path** | **Yes** — as the working template plus its pure allocator + offset helpers | pure allocator `allocate_backing_cluster_qcow2` `src/crates/commit/src/qcow2.rs:219-280`; L2 disk-offset math `overlay_l2_byte_offset_qcow2` `:296-298`; refcount disk-offset math `overlay_refcount_byte_offset_qcow2` `:313-321`; the inlined allocate-on-write loop (L2-table alloc, data-cluster alloc, data write, COPIED flags) `src/operations/commit/src/main.rs:711-785`; metadata flush ordering `:787-834` | Allocator claims free entries only inside *existing* refcount blocks — `RefcountExhausted` when full (`qcow2.rs:279`); never grows the refcount table. File growth is implicit (a `write_output_sector` past EOF extends the file). The allocator is welded into `Qcow2CommitOpts`/`Qcow2CommitContext`, which require *both* overlay and backing headers — the guest-op composition, not the crate API, is what bench copies. |
| **snapshot allocator / refcount mutators** | **Yes** — best-factored, already a shared pure API | first-fit allocator with contiguous-run + `AllocCursor` + `host_refblocks_start` basis `src/crates/snapshot/src/qcow2.rs:344-435`; `alloc_cluster_in_refblocks` wrapper `:299-316`; refcount accessors `read_refcount_in_block` `:51` / `set_refcount_in_block` `:152` / overflow check `check_refcount_after_addend` `:239`; COPIED-flag rewriters `rewrite_l1_entry_copied_flag` `:450-483` / `rewrite_l2_entry_copied_flag` (handles extended-L2 16-byte stride) `:495-532`; two-pass dry-run-then-apply `update_snapshot_refcount` `:764-876`, `dry_run_refcount_pass` `:968-1093`, `apply_refcount_pass` `:1098-1206` | `refcount_bits == 16` only (`:353-355`); "Refcount-table growth is a separate concern" (`:341-343`, `RefcountExhausted` `:434`). Every current consumer allocates only *metadata* clusters pointed at by a header/table field — **never a data cluster wired into an active L2 entry**. That wiring is the one genuinely net-new piece. |
| **bitmap staging pattern** | **Yes** — the closest precedent for bench's RW-attach + flush model (OQ5) | reuses snapshot's allocator verbatim: imports `alloc_contiguous_clusters_in_refblocks, set_refcount_in_block, AllocCursor` `src/operations/bitmap/src/main.rs:75`; RW-input attach + `read_input_sector`/`write_input_sector`/`fsync_input` `:48-49,:249,:258`; `fsync_input(0)` as the write barrier between metadata groups `:1272,:1304,:1316,:1326`; shared `AllocCursor`/refblocks buffer + double-buffered directory `:17,:951,:1213,:1715-1717`; refuses `refcount_bits != 16` `:521-523`; crate-side allocation `src/crates/bitmap/src/action.rs:346-353` | Confirms the RW-input-slot-0 + `fsync_input` idiom that OQ5 leans toward works with zero new I/O ABI. Like snapshot it only allocates table (metadata) clusters. |
| **convert writer** | **No** — fresh-image only | linear bump allocator `let mut next_free` "Linear cluster allocator. Cluster 0 is the header." `src/operations/convert/src/main.rs:2369-2371`, bumped per L2/data cluster `:2487-2495` | Writes a *brand-new* qcow2 in one linear pass: no free-cluster search, no read-modify-write of an existing L2/refcount, never touches a pre-existing allocation. Structurally inapplicable to in-place writes into an existing image. |

**(a) Callable primitive today?** No. Only per-op inlined
compositions. The full allocate-on-write sequence is assembled in
exactly one place — the commit guest op
(`src/operations/commit/src/main.rs:711-785`) — and it is bound
to commit's overlay→backing copy (its `Qcow2CommitOpts` demand
both headers). `crates/snapshot` exposes the cleanest *pure*
pieces (allocator, refcount two-pass, COPIED rewriters) but only
ever assembles them for metadata allocation.

**(b) Directly reusable vs net-new.** Directly reusable: the
free-cluster allocator (prefer snapshot's
`alloc_contiguous_clusters_in_refblocks` — it already supports
contiguous runs for a bufsize>cluster split and carries an
`AllocCursor`); the refcount-block RMW accessors + overflow
check; the L1/L2 disk-offset math
(`overlay_l2_byte_offset_qcow2`,
`overlay_refcount_byte_offset_qcow2`); the COPIED-flag rewriters;
and the call-table I/O primitives — `read_output_sector` /
`write_output_sector` (commit's backing model) **and**
`read_input_sector` / `write_input_sector` / `fsync_input`
(bitmap's RW-input model) all already exist, so either attach
posture works with **no new ABI**. Net-new: (1) the small driver
that, per virtual offset, reads the active L1, allocates+zeros an
L2 table when the L1 slot is empty, allocates a data cluster when
the L2 slot is empty, writes the pattern, and sets both COPIED
flags — i.e. commit's loop specialized to one target image
writing a pattern; (2) the overwrite-in-place fast path
(allocated + COPIED ⇒ write data only, no metadata touch) —
trivial but not pre-written, because commit always allocates; (3)
staging the *target's own* refcount table/blocks into guest
scratch (commit stages the backing's — the host pre-read plumbing
is directly analogous); (4) **sub-cluster RMW inside a
freshly-allocated cluster** — commit always writes a whole
`cluster_size` slice, but bench's default bufsize is 4096 bytes
(smaller than a 64 KiB cluster), so a bench write that allocates
a new cluster must zero-fill it and write the pattern at the
correct in-cluster offset (`write_output_byte_range` already does
sub-*sector* RMW; the cluster-level zero-then-patch is the new
bit); (5) **copy-on-write / `OFLAG_COPIED` handling** — commit
blind-overwrites an existing backing cluster without checking
COPIED (`src/operations/commit/src/main.rs:761-762`) because it
assumes unique ownership; a general write into an image with
internal snapshots (refcount > 1, not COPIED) would corrupt the
snapshot, so bench must either COW or (v1, simpler) gate such
images out.

**(c) Inherited limitations, and the bench v1 gates.** Yes — reuse
inherits the sister mutators' exact envelope: `refcount_bits ==
16` only (commit `src/crates/commit/src/qcow2.rs:137-139,223-225`;
snapshot `src/crates/snapshot/src/qcow2.rs:353-355`; bitmap
`src/operations/bitmap/src/main.rs:521-523`); **no refcount-table
growth and no new refcount-block append** — allocation only
claims free entries inside the already-existing refcount blocks
and returns `RefcountExhausted` when they are full (commit
`qcow2.rs:279`; snapshot `qcow2.rs:341-343,434`). This is
acceptable for bench v1. `bench -w` would adopt the **union of the
commit/snapshot gates**: refuse external-data-file, LUKS /
encryption, compression, dirty/corrupt, extended-L2 (commit
`src/operations/commit/src/main.rs:489-492`), and `refcount_bits
!= 16`; **plus one gate the sister ops do not carry — refuse
images with internal snapshots (`nb_snapshots > 0`)**, because
bench overwrites in place and commit's blind-overwrite
(`main.rs:761-762`, no COPIED check) would corrupt clusters
shared with a snapshot. Surface `RefcountExhausted` as a clean
"image too large for in-place bench write" refusal. Practical bound: one
16-bit refcount block on a 64 KiB-cluster image covers 32768
clusters = 2 GiB of address space, so the no-table-growth limit
only bites when the write set exceeds the image's existing
refcount coverage — a documented caveat, not a common failure.
Refusing the same images the other mutators refuse is the right
posture (master plan leaned yes; confirmed).

**(d) Phase-5 shape.** Option **(i) reuse-and-compose inside the
bench guest op — medium effort.** The mechanism already exists as
a working, tested composition (commit `main.rs:711-785`); Phase 5
lifts that sequence into the bench write loop, retargets it from
backing→single image, swaps "read overlay cluster" for "fill the
pattern buffer", adds the trivial overwrite-in-place branch, and
reuses snapshot's allocator / refcount / COPIED primitives
verbatim. A new `crates/bench-write` mini-planner (option ii,
high effort) is **not warranted** — the pure math already lives in
`crates/snapshot` and `crates/commit`; bench needs a guest-op
composition, not another pure crate. Deferring qcow2 `-w` (option
iii) is unnecessary. Main risk: metadata-write **atomicity
ordering** — the data cluster must be durable (`fsync_input`)
before the L2/L1 pointer that makes it reachable, with refcount
blocks flushed last — must mirror commit's proven order
(`main.rs:787-834`) or a crash mid-bench could corrupt the image;
since `-w` is explicitly destructive and copies commit's ordering
this is a code-review concern, not a design risk. Secondary risk:
staging the target's own refcount table into the guest scratch
budget for large images, bounded exactly as snapshot/bitmap bound
theirs (`MAX_REFBLOCKS` / `REFBLOCKS_LIMIT`).

**Recommendation for Phase 5: ship qcow2 `-w` in v1 via option
(i) — reuse-and-compose the commit guest op's allocate-on-write
sequence in the bench write loop, backed by `crates/snapshot`'s
allocator + refcount + COPIED-flag primitives, gated to the same
images the sister mutators refuse (`refcount_bits == 16`, no
compression / extended-L2 / external-data / LUKS / dirty-corrupt)
with `RefcountExhausted` surfaced as a clean refusal. Medium
effort, no new ABI, no new crate; keep Phase 5 at high effort only
for the crash-atomicity ordering review.**

vmdk / vhd / vhdx (falls out for free, future work either way):
commit already carries a vmdk in-place grain allocator analogue
(`allocate_backing_grain_vmdk`, `VmdkCommitContext` in
`src/crates/commit/src/vmdk.rs`), so an allocating write into an
existing vmdk monolithicSparse image is demonstrably feasible;
vhd/vhdx have no in-place allocating-write machinery anywhere in
the tree. All three stay out of scope for bench `-w` v1 per the
master plan.

### Bugs fixed during this work

* **`info` human output wrong for VMDK monolithicFlat images**
  (found 2026-07-06 by the memory-map lift's full integration run,
  which surfaced never-committed human goldens in instar-testdata
  encoding the correct qemu output). The human formatter's extents
  section (`src/vmm/src/main.rs`) hardcoded a single `[0]` block
  built from the descriptor (its own path, empty format, spurious
  `cluster size: 0`), omitted the `Child node '/extents.N'`
  blocks, and reported the raw descriptor byte size where qemu
  reports the sector-rounded protocol-node length. The JSON path
  was already correct via `ResolvedVmdkDescriptor.flat_extents`;
  the human path now mirrors it (per-extent resolved filename +
  `format: FLAT`, version-gated `/extents.N` children, 512-byte
  rounding reusing the raw-format idiom). The five-profile human
  goldens for `vmdk-flat-{1m,10m}` are now committed in
  instar-testdata (the JSON half landed in `91c8d457c`; the human
  half had been left uncommitted because instar could not match
  it). `test_info_safe` went 526/536 → 536/536.

* **`dd` unaligned windows corrupted or failed structured output**
  (found 2026-07-08 by PR #394's differential-fuzz CI run — issue
  #396, `exit_code_divergence`, seed 2630842467 iteration 96; a
  pre-existing develop bug, confirmed by reproducing against a
  develop-built binary — the bench branch only exposed it by
  changing the fuzzer's random draw sequence). A dd window whose
  start is not a multiple of the input cluster size shifts every
  guest read off cluster alignment, so each chunk straddles an
  input-cluster boundary; `convert_to_qcow2` /
  `convert_to_qcow2_compressed` fed such chunks to
  `read_chain_virtual_cluster`, whose single-cluster contract then
  (a) zero-filled a whole chunk when only its first byte fell in a
  hole, (b) read a cluster's tail from the physically-adjacent
  file cluster — silent wrong data whenever physical order differs
  from virtual order, and (c) ran past EOF when the allocated
  cluster was physically last (the CI failure). Fixed by clamping
  each read at input-cluster boundaries in both loops — the exact
  clamp `convert_to_raw` already carried. Regression tests:
  `tests/test_dd.py::TestDdUnalignedClusterCrossing` (hole-first
  and out-of-order-cluster inputs, `bs=1000 skip=1`, full parity
  across all four structured formats); the seeded fuzz run now
  passes 97/97.

List any bugs encountered and fixed during development here. At
the start of Phase 1, scan the GitHub issue tracker for any open
performance/virtio/io_thread issues this work should resolve or be
aware of. Two upstream qemu quirks discovered during planning
research are recorded here so nobody mistakes them for instar
bugs: (a) `qemu-img bench` at any depth submits every request from
the same `qiov[0]` buffer slice (the per-slot iovecs are dead
code); (b) 10.0.8's offset wrap can EIO near EOF on small images
(fixed upstream by `ff2ab634`; instar adopts the fixed rule per
Open question 7).

### Documentation index maintenance

When this master plan is created, update:

* **`docs/plans/index.md`** — add a row to the *Master plans* table
  (creation date, link to this plan, one-line intent, initial
  status, links to each phase plan as they are written), in
  chronological order.
* **`docs/plans/order.yml`** — add an entry so this master plan
  appears in the docs navigation. Phase files are *not* added to
  `order.yml`.

When all phases are complete, update the status column in
`index.md` to *Complete*.

### Back brief

Before executing any step of this plan, the executing agent should
back brief the operator as to its understanding of the plan and how
the intended work aligns with it.
