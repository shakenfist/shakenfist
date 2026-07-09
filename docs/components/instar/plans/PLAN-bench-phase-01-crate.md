# PLAN-bench phase 01: semantics pin + bench schedule crate

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the pure `no_std`
math-crate idiom in `src/crates/dd/src/lib.rs`, the inline
`#[cfg(test)]` style, the workspace registration in
`src/Cargo.toml`), and ground your answers in what the code
actually does today. Do not speculate about the codebase when you
could read it instead. Where a question touches on `qemu-img
bench` behaviour, consult the verified contract in the master
plan ([PLAN-bench.md](/components/instar/plans/PLAN-bench/)) and the qemu sources quoted
below rather than guessing; where the contract is still ambiguous,
verify empirically against the local `qemu-img` 10.0.8 binary.
Flag any uncertainty explicitly.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named `PLAN-bench-phase-NN-<descriptive>.md`.
The master plan is [PLAN-bench.md](/components/instar/plans/PLAN-bench/). This is the
first of eight phases.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase lands the **deterministic core** of `instar bench`: a
pure `no_std` `src/crates/bench/` crate that owns every piece of
bench behaviour that can be computed without I/O — parameter
validation bounds, the offset schedule and wrap rule, the
transfer-split plan for buffers larger than one virtio transfer,
and the flush cadence. It also runs the two investigations the
master plan gated on this phase: the commit-path reuse assessment
for allocating qcow2 writes (master-plan Open question 4, step
1d) and the empirical capture of qemu's exact error-message
contract (step 1e). It is a pure library phase — no ABI, no guest
op, no host CLI.

The reason this crate exists (master-plan Open question 8,
resolved yes): the wrap arithmetic and flush cadence are exactly
the kind of small, subtle, qemu-defined semantics that instar
pins in one testable, fuzzable place. The precedent is
`src/crates/dd/` — a dependency-free `#![no_std]` crate whose doc
comment says it "owns the exact upstream `qemu-img dd` window
semantics so the math can be unit-tested and fuzzed independently
of the vmm binary". `crates/bench` is the same shape.

Grounding (verified against the current tree on the `bench`
branch, tip `f89ef55`, and the qemu sources saved during
master-plan research):

- **The model crate.** `src/crates/dd/` is `#![no_std]`, has an
  empty `[dependencies]` section, `publish = false`, edition
  2021, version 0.2.0, and inline `#[cfg(test)] mod tests` noting
  "Pure `u64` arithmetic — host-only, no KVM or testdata
  required". Its workspace registration is the `members` array in
  `src/Cargo.toml:3` (`"crates/dd"` sits between
  `"crates/amend"` and `"crates/commit"`; add `"crates/bench"`
  in alphabetical-ish position near it). `make test-rust` is
  `cargo test --release --workspace`, so a registered crate's
  tests run with no further wiring.
- **Transfer cap constants.** `shared::MAX_SECTOR_SIZE = 65536`
  (`src/shared/src/lib.rs:434`) is the per-virtio-transfer cap;
  `shared::MAX_CLUSTER_SIZE = 2 * 1024 * 1024`
  (`src/shared/src/lib.rs:456`) is the established "large buffer
  handled in 64 KiB chunks" precedent. The bench crate stays
  dependency-free like dd: it takes `max_transfer` as a
  parameter and defines its own `BENCH_MAX_BUFSIZE` constant; the
  consumers (phases 3/4) assert the constants line up with
  `shared`'s at their boundary.
- **The verified qemu defaults** (master plan, "What qemu-img
  bench actually does"): count 75000, depth 64, bufsize 4096,
  step 0 (meaning "= bufsize"; a literal zero step is
  unobtainable), offset 0, pattern 0, flush-interval 0,
  drain-on-flush true, read test.
- **The verified validation bounds** (Debian 10.0.8 = master
  behaviour): count, depth, bufsize ∈ [1, 2147483647]; step ∈
  [0, 2147483647]; pattern ∈ [0, 255]; offset ∈ [0, i64::MAX].
  Exactly two cross-option rules: `--flush-interval` without `-w`
  is an error, and `flush_interval < depth` (when nonzero) is an
  error. `--pattern` without `-w` is **silently ignored** — not
  an error. There is **no** offset alignment requirement and
  **no** up-front bounds check of offset/count/step against the
  image size.
- **The submission/wrap/flush engine is one function**,
  `bench_cb` (qemu-img.c; v10.0.8 lines 4439–4505, quoted from
  the saved source). Completion path:
  ```c
  } else if (b->in_flight > 0) {
      int remaining = b->n - b->in_flight;
      b->n--;
      b->in_flight--;
      /* Time for flush? Drain queue if requested, then flush */
      if (b->flush_interval && remaining % b->flush_interval == 0) {
          if (!b->in_flight || !b->drain_on_flush) {
              ... blk_aio_flush(b->blk, cb, b); ...
          }
          if (b->drain_on_flush) { return; }
      }
  }
  ```
  Submission path:
  ```c
  while (b->n > b->in_flight && b->in_flight < b->nrreq) {
      int64_t offset = b->offset;
      b->in_flight++;
      b->offset += b->step;
      if (b->image_size == 0) {
          b->offset = 0;
      } else {
          b->offset %= b->image_size;          /* 10.0.8 rule */
      }
      ... blk_aio_pwritev/preadv(b->blk, offset, b->qiov, ...) ...
  }
  ```
  Key readings: `b->n` counts *uncompleted* requests (starts at
  count, decremented at completion); the **first request uses the
  raw `-o` offset unwrapped** (wrap applies only after the
  increment); master's fixed wrap rule (commit `ff2ab634`)
  replaces the modulo with
  `if (image_size <= bufsize) offset = 0;
  else offset %= image_size - bufsize;` so wrapped requests never
  overrun EOF. The master plan (Open question 7) decided instar
  adopts the **master rule**, with the 10.0.8 delta recorded as a
  divergence.
- **Flush cadence, exactly.** `remaining = n - in_flight` is
  computed *before* decrementing both. Under instar's serial
  execution (`in_flight` is 1 at every completion), the
  completing request is the `k`-th of `count`, `n = count-k+1`,
  so `remaining = count - k`: **flush after completion `k` iff
  `flush_interval != 0 && (count - k) % flush_interval == 0`**,
  for `k ∈ 1..=count`. This includes a trailing flush at
  `k = count` (`remaining = 0`, and `0 % x == 0`), inside the
  timed window. Total flushes =
  `(count - 1) / flush_interval + 1` (integer division). Worked
  vectors: count 100 / interval 50 → flushes after k=50,100
  (2 total); count 101 / interval 50 → k=1,51,101 (3 total —
  note the immediate flush after the *first* completion);
  count 100 / interval 100 → k=100 (1 total). At depth > 1 the
  flush *positions* shift to drain boundaries but the *count* is
  identical (the enforced `flush_interval >= depth` makes
  `remaining` hit each multiple exactly once — qemu's own
  invariant), so the serial formula is the correct v1 semantics
  for any accepted depth.
- **What "one request" means downstream.** Phase 3's guest loop
  executes, per schedule entry, either one virtio transfer
  (bufsize ≤ 64 KiB — the overwhelmingly common case; qemu's
  default is 4096) or a contiguous run of ≤ 64 KiB transfers
  produced by this crate's transfer split (master-plan Open
  question 2: split, v1 cap 2 MiB). The split is pure arithmetic
  and lands here.

## Mission

Create `src/crates/bench/` — package name `bench`, `#![no_std]`,
zero dependencies, registered in the `src/Cargo.toml` workspace
`members` — exposing, with inline unit tests:

1. **Defaults and bounds constants.**
   ```
   DEFAULT_COUNT: u32          = 75000
   DEFAULT_DEPTH: u32          = 64
   DEFAULT_BUFSIZE: u64        = 4096
   QEMU_BENCH_ARG_MAX: u64     = 2_147_483_647   // INT_MAX bound on count/depth/bufsize/step
   BENCH_MAX_BUFSIZE: u64      = 2 * 1024 * 1024 // instar v1 -s cap (= shared::MAX_CLUSTER_SIZE)
   ```
   (Step 0 semantics — "0 means bufsize" — are a method, not a
   constant; see `BenchParams::effective_step`.)

2. **`BenchParams` + validation.**
   ```rust
   pub struct BenchParams {
       pub count: u32,          // requests
       pub depth: u32,          // accepted, echoed; serialized in v1
       pub bufsize: u64,        // bytes per request
       pub step: u64,           // 0 => bufsize
       pub offset: u64,         // first-request byte offset
       pub is_write: bool,
       pub pattern: u8,
       pub flush_interval: u32, // 0 => never
       pub no_drain: bool,
   }
   ```
   `Default` yields the qemu defaults. `effective_step()` returns
   `if step == 0 { bufsize } else { step }`.
   `validate(&self) -> Result<(), BenchParamError>` with
   ```rust
   pub enum BenchParamError {
       CountOutOfRange,               // count < 1 (u32 caps the top)
       DepthOutOfRange,               // depth < 1
       BufsizeOutOfRange,             // bufsize < 1 || > QEMU_BENCH_ARG_MAX
       StepOutOfRange,                // step > QEMU_BENCH_ARG_MAX
       FlushRequiresWrite,            // flush_interval != 0 && !is_write
       FlushIntervalSmallerThanDepth, // flush_interval != 0 && < depth
       BufsizeAboveInstarCap,         // bufsize > BENCH_MAX_BUFSIZE (instar-only)
   }
   ```
   Notes: pattern needs no range variant (`u8` makes >0xff
   unrepresentable — the host parser owns that refusal); there is
   deliberately **no** pattern-without-write error and **no**
   offset/image-size check (qemu has neither); `no_drain` without
   `flush_interval` is valid and irrelevant (qemu posture). The
   host (phase 4) maps each variant to the captured qemu message
   (step 1e); the instar-only `BufsizeAboveInstarCap` gets an
   instar-worded "not yet supported above 2 MiB" message.

3. **Offset schedule.** The pure advance rule (master wrap
   semantics, Open question 7):
   ```rust
   pub fn next_offset(cur: u64, step: u64, image_size: u64, bufsize: u64) -> u64
   // let n = cur.saturating_add(step);
   // if image_size <= bufsize { 0 } else { n % (image_size - bufsize) }
   ```
   and `OffsetSchedule`, an `Iterator<Item = u64>` over the
   `count` request offsets: yields the **raw initial offset
   first** (unwrapped, exactly like qemu), then repeated
   `next_offset` applications with `effective_step()`. Wrapped
   offsets land in `[0, image_size - bufsize)` and may be
   unaligned — that is correct; do not round.

4. **Transfer split.** `TransferSplit::new(offset, len,
   max_transfer)` — an `Iterator<Item = (u64, u64)>` of
   `(offset, len)` chunks covering `[offset, offset + len)` in
   order, each chunk `<= max_transfer`, all but the last exactly
   `max_transfer`. `max_transfer` is a parameter (the guest
   passes `shared::MAX_SECTOR_SIZE as u64`); `max_transfer == 0`
   yields nothing (defensive; callers never pass it).

5. **Flush cadence.**
   ```rust
   pub fn flush_after_completion(count: u32, completed: u32, flush_interval: u32) -> bool
   // flush_interval != 0 && completed >= 1 && (count - completed) % flush_interval == 0
   pub fn total_flushes(count: u32, flush_interval: u32) -> u32
   // if flush_interval == 0 { 0 } else { (count - 1) / flush_interval + 1 }
   ```
   with the derivation from `bench_cb` (quoted above) restated in
   the doc comment, including the serial-emulation argument and
   the trailing-flush-at-`remaining == 0` fact.

Everything is panic-free (`saturating_*`/`checked_*`; no
indexing, no division by unchecked zero) — the crate is fuzzed in
phase 7 (`fuzz_bench_schedule`) and its inputs are
user-controlled.

## Out of scope for this phase

- The ABI (`BenchConfig`/`BenchResult`, call-table sender, start
  marker) — phase 2.
- The guest op and any I/O — phase 3. This crate never reads or
  writes anything.
- The host CLI, size-suffix parsing (`-s 64k`, `-o 1G` — qemu's
  `qemu_strtosz` grammar is a *host* concern), and error-message
  rendering — phase 4. The crate deals in already-parsed numbers.
- Any `-w` write machinery — phase 5. Step 1d only *assesses*
  reuse options and reports; it changes no product code.
- The wrap-divergence registry entry and integration tests —
  phase 6 (but step 1e's captured messages feed them).

## Resolved open questions (from the master plan)

- **OQ1 (depth):** decision (a) confirmed — `-d` is accepted and
  validated (≥ 1), carried in `BenchParams` for the header echo,
  and has **no effect on the schedule** in v1 (serial execution).
  The flush-cadence formula is depth-independent (see the
  grounding derivation), so nothing else in this crate cares.
- **OQ2 (large buffers):** split into ≤ 64 KiB transfers via
  `TransferSplit`; v1 cap `BENCH_MAX_BUFSIZE = 2 MiB` enforced as
  `BufsizeAboveInstarCap` — chosen to match
  `shared::MAX_CLUSTER_SIZE`, the existing "large buffer in
  64 KiB chunks" precedent, and comfortably inside the guest's
  12.9 MiB scratch region for phase 3's staging.
- **OQ7 (wrap rule):** master's fixed rule
  (`% (image_size - bufsize)`, zero/degenerate sizes pin to 0),
  raw unwrapped first offset, and **no up-front bounds check** —
  a first request past usable EOF fails at request time (phase
  3's concern), matching qemu. The 10.0.8 `% image_size` delta
  goes in phase 6's divergence registry.
- **OQ8 (crate):** resolved yes — this phase.
- **OQ4 (allocating qcow2 writes):** *assessed* here (step 1d),
  decided at master-plan review before phase 5 is planned.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Create `src/crates/bench/` mirroring `src/crates/dd/` exactly in shape: `Cargo.toml` (`name = "bench"`, version 0.2.0, edition 2021, `description = "Compute the bench request schedule, transfer split, and flush cadence (no I/O)"`, `license = "Apache-2.0"`, `publish = false`, empty `[dependencies]`) and `src/lib.rs` with `#![no_std]`, a crate doc comment modeled on dd's ("owns the exact upstream `qemu-img bench` … semantics so the math can be unit-tested and fuzzed independently"). Add `"crates/bench"` to the workspace `members` array in `src/Cargo.toml:3` (next to `"crates/dd"`). Implement Mission §1 (constants), §2 (`BenchParams`, `Default`, `effective_step`, `validate`, `BenchParamError` with `#[derive(Debug, PartialEq, Eq, Clone, Copy)]`) precisely as specified — copy the bounds and the deliberate non-checks (no pattern-without-write error, no offset/image check) from this plan's Mission into doc comments so the qemu-parity intent survives review. Inline tests: defaults match qemu (75000/64/4096/step→bufsize); each error variant fires at its boundary (count 0 vs 1; depth 0 vs 1; bufsize 0, 1, `QEMU_BENCH_ARG_MAX`, `QEMU_BENCH_ARG_MAX`+1, `BENCH_MAX_BUFSIZE` vs +1 — note the cap fires *before* the qemu bound is reachable, decide precedence: qemu-range check first, then instar cap, so an absurd `-s 3G` gets the qemu-shaped error); step 0 valid (means bufsize), step at/above bound; flush 0 always valid; flush without write rejected; flush 49 with depth 50 rejected, flush 50 with depth 50 valid (qemu: strictly-smaller is the error); `no_drain` alone valid. Run `cargo test -p bench`, `make lint`. Constraint: `no_std`, zero deps, panic-free. |
| 1b | high | opus | none | In `src/crates/bench/src/lib.rs`, implement Mission §3 (`next_offset`, `OffsetSchedule`) and §4 (`TransferSplit`) with the doc comments quoting the qemu submission-loop lines and naming the master-rule commit (`ff2ab634`) and the 10.0.8 divergence. Subtleties to honour: the first yielded offset is the **raw** `params.offset` even if it is past EOF (no wrap, no clamp — qemu submits it and lets the request fail); wrapping uses `effective_step()`, `saturating_add`, and the `image_size <= bufsize ⇒ 0` degenerate rule (covers image_size 0 — qemu's separate zero guard collapses into it); wrapped offsets are *not* aligned to anything. Tests (hand-computed vectors): default params on a 1 MiB image (offsets 0, 4096, 8192, …); wrap on a 10240-byte image with 4096-byte requests and step 4096 — with the master rule offsets cycle within [0, 6144) (contrast with the 10.0.8 EIO case in the master plan; assert our third offset is `8192 % 6144 = 2048`, not 8192); step > image size; offset near u64::MAX with large step (saturation, no panic); image_size == bufsize and image_size < bufsize (pin to 0); image_size 0; unaligned initial offset and step (odd bytes) pass through unrounded; schedule length is exactly `count`. `TransferSplit`: 4096/64 KiB → one chunk; 2 MiB/64 KiB → 32 chunks each 64 KiB, contiguous, in order; 100 KiB/64 KiB → 64 KiB + 36 KiB; len 0 → empty; max_transfer 0 → empty; invariants (sum of lens == len, each ≤ max, offsets contiguous ascending) asserted in a reusable test helper — phase 7's fuzz target reuses these exact invariants. `no_std`, panic-free, zero deps. |
| 1c | high | opus | none | Implement Mission §5 (`flush_after_completion`, `total_flushes`) with the `bench_cb`-derivation doc comment, then **verify the formula empirically** against the local qemu-img 10.0.8 before finalising the tests. Method: create a small raw image (`qemu-img create -f raw /tmp/b.raw 10M`); for each (count, interval) in {(100,50), (101,50), (100,100), (75,25), (1,1)} run `strace -f -e trace=fdatasync,fsync qemu-img bench -w -d 1 --flush-interval I -c C -s 4096 -t writeback /tmp/b.raw` and count flush syscalls; subtract the close-path flushes measured by a baseline `strace … qemu-img bench -w -d 1 -c C -s 4096 …` run with no `--flush-interval` (image close may flush regardless — the *difference* is the bench-issued count). Confirm each matches `total_flushes`; if any disagrees, stop and report the discrepancy with the raw strace counts rather than adjusting the formula to fit (the formula is derived from source; a mismatch means the derivation or the measurement method is wrong and the management session decides). Record the measured table in a `## Captured flush-count verification` section appended to `docs/plans/PLAN-bench-phase-01-crate.md`. Unit tests: the five vectors above plus interval 0 → never/0, completed 0 → false, count == interval, interval > count (flush only at the final completion — `(count-count) % i == 0`), and a property-style loop asserting `total_flushes` equals the number of `k ∈ 1..=count` with `flush_after_completion(count, k, i)` for a grid of small counts/intervals. `no_std`, panic-free. |
| 1d | high | opus | none | **Investigation only — no product code.** Resolve master-plan Open question 4: can `instar bench -w` on qcow2 reuse existing machinery for allocate-on-write at arbitrary virtual offsets into an *existing* image? Read, at minimum: the commit op end-to-end (`src/operations/commit/src/main.rs` and `src/crates/commit/` — how a cluster of overlay data lands in the backing file: existing-cluster overwrite vs new-cluster allocate, L2 update, refcount update, file growth, COPIED flags); the snapshot crate's allocator/refcount mutators (`src/crates/snapshot/` — the free-cluster search and refcount-block growth bitmap reused); how bitmap's guest op stages and writes back allocated clusters (`src/operations/bitmap/src/main.rs`); and convert's writer for contrast (linear fresh-image allocation — presumably *not* reusable for in-place). Answer concretely: (a) is there a callable "write N bytes at virtual offset X with allocation" path today, or only per-op compositions? (b) which pieces (allocator, refcount mutators, L2 RMW) are directly reusable and which are net-new? (c) does the reuse require the refcount-table-growth limitation (snapshot/bitmap refuse `refcount_bits != 16` and never grow the refcount *table*) and is that acceptable for bench v1 (leaning: yes — refuse the same images the other mutators refuse)? (d) estimate the phase-5 shape: reuse-and-compose (medium) vs new mini-planner crate (high) vs defer qcow2 `-w` (drop to raw-only). Deliverable: a `## Findings: allocating-write reuse (OQ4)` section appended to [PLAN-bench.md](/components/instar/plans/PLAN-bench/) — table of candidate machinery with file:line evidence, verdict per (a)–(d), and a recommendation for phase 5 — plus updating OQ4's text to point at the findings. Follow the precedent of PLAN-bitmap's "Findings: pre-existing bitmap preservation" section. |
| 1e | low | sonnet | none | **Empirical capture, no code.** Pin the qemu-img 10.0.8 bench message contract for phase 4/6. Against the local binary, run each of: `-c 0`, `-c -1`, `-d 0`, `-s 0`, `-s` huge (`3G`), `--pattern 256`, `--pattern -1`, `--flush-interval 50` without `-w`, `-w -d 64 --flush-interval 32` (interval < depth), `--pattern 65` without `-w` (expect silent success), `-t bogus`, `-i bogus`, `-t none` (works in qemu — we will *refuse*; capture qemu's success for the divergence registry), `--image-opts` with `-f` both given, no filename, two filenames, `-o -1`, `-o 1k` (suffix accepted), zero-byte image file, and a plain successful run capturing the exact three stdout lines. Use a scratch raw image; record for every invocation the exact argv, stdout, stderr, and exit code. Deliverable: a `## Captured qemu-img 10.0.8 message contract` section appended to `docs/plans/PLAN-bench-phase-01-crate.md`, one fenced block per invocation, with a short table up top mapping `BenchParamError` variants → the captured message text. No source changes. |

Steps 1a → 1b → 1c are sequential (same file); 1d and 1e are
independent of the crate work and can run in parallel with it.

## Verification (management-session review checklist)

After each step, verify in the management session:

- [ ] The intended files changed and no others (read them).
- [ ] `cargo test -p bench` passes; `make test-rust` passes
      (workspace registration correct).
- [ ] `make lint` is clean; `make instar` still builds (the crate
      is not yet linked by any binary, but the workspace build
      must stay green).
- [ ] `pre-commit run --all-files` passes.
- [ ] The crate is `#![no_std]`, has zero dependencies, and every
      public function is panic-free on adversarial inputs
      (u64::MAX offsets/steps, zero sizes) — phase 7 fuzzes it.
- [ ] The doc comments carry the qemu derivations (bench_cb
      quotes, wrap-rule commit hash, serial-emulation argument) —
      the code must stay explainable without re-research.
- [ ] 1c's empirical flush table matches `total_flushes` for all
      five vectors (or the discrepancy was escalated, not
      papered over).
- [ ] 1d's findings section exists in PLAN-bench.md with
      file:line evidence and a clear phase-5 recommendation; 1e's
      message contract section exists in this file.
- [ ] Commit messages follow project conventions (Co-Authored-By
      with model / context window / effort level / settings;
      `Signed-off-by`; `Prompt:` paragraph).

## Success criteria

This phase is complete when:

- `src/crates/bench/` exists, is registered in the workspace, and
  exports the constants, `BenchParams`/`BenchParamError`/
  `validate`, `next_offset`/`OffsetSchedule`, `TransferSplit`,
  and `flush_after_completion`/`total_flushes` — all `no_std`,
  dependency-free, panic-free.
- The validation encodes exactly the Debian-10.0.8 bounds and the
  two cross-option rules, plus the single instar-only cap — and
  encodes the deliberate *non*-checks as documented behaviour.
- The offset schedule reproduces qemu master's wrap rule with the
  raw first offset, verified by hand-computed vectors including
  the wrap case that EIOs on 10.0.8.
- The flush cadence formula is verified against the real binary
  via strace differencing, and the captured table is in this
  file.
- The OQ4 findings section exists in the master plan with a
  concrete phase-5 recommendation (reuse / mini-planner / defer),
  and the qemu message contract is captured in this file.
- `make test-rust`, `make lint`, `make instar`, and
  `pre-commit run --all-files` are all clean.

## Back brief

Before executing any step, the executing agent should back brief
the operator on its understanding of this phase and how the work
aligns with it — in particular: this is a **pure-math library
phase** (no I/O, no ABI, no CLI); the crate encodes *qemu's*
semantics (master wrap rule, serial flush cadence), not
instar-convenient approximations; steps 1d and 1e are
investigation/capture deliverables that append to plan documents
rather than changing product code; and a flush-formula mismatch
in 1c is escalated, never absorbed.

## Captured qemu-img 10.0.8 message contract (step 1e)

Empirical capture only — no source changes. Ran the local
`qemu-img bench` binary against a scratch 10 MiB raw image
(`probe.raw`, created with `qemu-img create -f raw probe.raw
10M`) and a zero-byte file (`empty.raw`, `truncate -s 0`), both
in a session scratchpad directory, then discarded.

```
$ qemu-img --version
qemu-img version 10.0.8 (Debian 1:10.0.8+ds-0+deb13u1+b2)
Copyright (c) 2003-2025 Fabrice Bellard and the QEMU Project developers
```

### `BenchParamError` → captured qemu message

| `BenchParamError` variant | Captured qemu-img 10.0.8 message | Invocation |
|---|---|---|
| `CountOutOfRange` | `Invalid request count specified. Must be between 1 and 2147483647.` | 1, 2 |
| `DepthOutOfRange` | `Invalid queue depth specified. Must be between 1 and 2147483647.` | 3 |
| `BufsizeOutOfRange` | `Invalid buffer size specified. Must be between 1 and 2147483647.` | 4, 5 |
| `StepOutOfRange` | `Invalid step size specified. Must be between 0 and 2147483647.` | 22, 23 (supplement) |
| `FlushRequiresWrite` | `--flush-interval is only available in write tests` | 8 |
| `FlushIntervalSmallerThanDepth` | `Flush interval can't be smaller than depth` | 9 |
| `BufsizeAboveInstarCap` | instar-only — no qemu analog. qemu's own range check runs first, so an absurd `-s 3G` never reaches instar's 2 MiB cap; it surfaces qemu's `BufsizeOutOfRange` text (see invocation 5). Phase 4 must order its checks the same way (qemu bound first) so this variant only ever fires for `bufsize` values *inside* qemu's [1, 2147483647] range but above `BENCH_MAX_BUFSIZE`. | n/a |

Pattern out-of-range (invocations 6, 7) is **not** a
`BenchParamError` variant per Mission §2 — `pattern: u8` makes
values above 255 unrepresentable in `BenchParams`, so that
refusal belongs to the phase-4 host parser, not this crate. Its
captured text (`Invalid pattern byte specified. Must be between
0 and 255.`) still feeds phase 4's message table, just outside
the `BenchParamError` enum.

Two more captured messages have no `BenchParamError` mapping at
all but are needed by phase 4/6 regardless: the cache-mode
refusal (`-t bogus`, invocation 11) and the aio-backend refusal
(`-i bogus`, invocation 12) — both are qemu option-parsing errors
that instar's CLI will need its own text for, since `-t`/`-i`
aren't part of `BenchParams`.

### Per-invocation captures

#### 1. `qemu-img bench -c 0 probe.raw`

```
$ qemu-img bench -c 0 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid request count specified. Must be between 1 and 2147483647.
```

#### 2. `qemu-img bench -c -1 probe.raw`

```
$ qemu-img bench -c -1 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid request count specified. Must be between 1 and 2147483647.
```

Identical message to `-c 0`: qemu does not distinguish "zero" from
"negative" in the count parser — both collapse to the same
range-check text. `BenchParamError::CountOutOfRange` should do
the same (one variant, one message, regardless of which side of
the range was violated — `count` is `u32` in `BenchParams` so a
literal negative can't reach the type anyway; the phase-4 parser
owns rejecting `-1` before it becomes a `u32`).

#### 3. `qemu-img bench -d 0 probe.raw`

```
$ qemu-img bench -d 0 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid queue depth specified. Must be between 1 and 2147483647.
```

#### 4. `qemu-img bench -s 0 probe.raw`

```
$ qemu-img bench -s 0 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid buffer size specified. Must be between 1 and 2147483647.
```

#### 5. `qemu-img bench -s 3G probe.raw`

```
$ qemu-img bench -s 3G probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid buffer size specified. Must be between 1 and 2147483647.
```

3 GiB (`3221225472`) exceeds `QEMU_BENCH_ARG_MAX`
(`2147483647`), so this is rejected by qemu's own range check —
it never reaches instar's `BENCH_MAX_BUFSIZE` (2 MiB) cap. This
confirms the Mission's stated precedence (qemu-range check
first, then instar cap) is also what qemu itself does: there is
only one bound at this size, and it is qemu's. Phase 4 must not
short-circuit on the instar cap before the qemu bound, or a `-s
3G` request would get the wrong (instar-worded) message.

#### 6. `qemu-img bench --pattern 256 -w -c 10 probe.raw`

```
$ qemu-img bench --pattern 256 -w -c 10 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid pattern byte specified. Must be between 0 and 255.
```

#### 7. `qemu-img bench --pattern -1 -w -c 10 probe.raw`

```
$ qemu-img bench --pattern -1 -w -c 10 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid pattern byte specified. Must be between 0 and 255.
```

Same message for both the above-range and negative case, same
pattern as `-c`/`-c -1` above.

#### 8. `qemu-img bench --flush-interval 50 -c 100 probe.raw`

```
$ qemu-img bench --flush-interval 50 -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: --flush-interval is only available in write tests
```

#### 9. `qemu-img bench -w -d 64 --flush-interval 32 -c 100 probe.raw`

```
$ qemu-img bench -w -d 64 --flush-interval 32 -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Flush interval can't be smaller than depth
```

#### 10. `qemu-img bench --pattern 65 -c 100 probe.raw`

```
$ qemu-img bench --pattern 65 -c 100 probe.raw
exit: 0
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.001 seconds.
stderr:
```

Confirms the Mission's documented non-check: `--pattern` without
`-w` is silently accepted (exit 0), not an error. The pattern
value has no visible effect on a read test's output — as
expected, since qemu only uses `pattern` to fill write buffers.

#### 11. `qemu-img bench -t bogus -c 100 probe.raw`

```
$ qemu-img bench -t bogus -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid cache mode
```

Terser than the numeric-range messages — no "must be one of"
list of valid cache-mode names is included.

#### 12. `qemu-img bench -i bogus -c 100 probe.raw`

```
$ qemu-img bench -i bogus -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid aio option: bogus
```

Unlike `-t`, this message echoes the offending value back
(`bogus`) rather than being generic.

#### 13. `qemu-img bench -t none -c 100 probe.raw`

```
$ qemu-img bench -t none -c 100 probe.raw
exit: 0
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.001 seconds.
stderr:
```

Divergence-registry material: qemu accepts `-t none` (no host
page cache, direct I/O) and runs the benchmark normally; instar
v1 will refuse this cache mode outright. This is qemu's captured
*success* behaviour for the case instar diverges on.

#### 14. `qemu-img bench --image-opts -f raw -c 100 probe.raw`

```
$ qemu-img bench --image-opts -f raw -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: --image-opts and --format are mutually exclusive
```

#### 15. `qemu-img bench`

```
$ qemu-img bench
exit: 1
stdout:
stderr:
qemu-img: Expecting one image file name
Try 'qemu-img bench --help' for more info
```

Two stderr lines: the error itself, plus a "Try --help" hint
line. Both are needed if instar's message table reproduces this
text verbatim.

#### 16. `qemu-img bench -c 100 probe.raw probe.raw`

```
$ qemu-img bench -c 100 probe.raw probe.raw
exit: 1
stdout:
stderr:
qemu-img: Expecting one image file name
Try 'qemu-img bench --help' for more info
```

Identical text to the no-filename case (15) — qemu does not
distinguish "zero filenames" from "too many filenames" in its
message.

#### 17. `qemu-img bench -o -1 -c 100 probe.raw`

```
$ qemu-img bench -o -1 -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid offset specified. Must be between 0 and 9223372036854775807.
```

Upper bound is `i64::MAX` (`9223372036854775807`), matching the
Mission's stated offset bound of `[0, i64::MAX]` — distinct from
the `u32`-ish `2147483647` bound used for count/depth/bufsize/step.

#### 18. `qemu-img bench -o 1k -c 100 probe.raw`

```
$ qemu-img bench -o 1k -c 100 probe.raw
exit: 0
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 1024, step size 4096)
Run completed in 0.001 seconds.
stderr:
```

Confirms `qemu_strtosz`-style suffixes are accepted for `-o`
(`1k` → 1024) and the parsed value is echoed in decimal bytes in
the "starting at offset" header — this is a *host* (phase 4)
parsing concern per the Mission's "Out of scope" list, captured
here only to pin the header's rendering of the parsed value.

#### 19. `qemu-img bench -c 100 empty.raw`

```
$ qemu-img bench -c 100 empty.raw
exit: 1
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
stderr:
qemu-img: Failed request: Input/output error
```

No up-front size check against the (zero-byte) image, confirming
the Mission's "no offset/count/step bounds check against image
size" note — the header line is printed unconditionally, and the
image-size violation only surfaces once the first request is
issued, as an I/O failure rather than a validation error. No
"Run completed" line is printed on this path.

#### 20. `qemu-img bench -c 100 -s 4096 probe.raw`

```
$ qemu-img bench -c 100 -s 4096 probe.raw
exit: 0
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.001 seconds.
stderr:
```

Plain successful read test: exactly two stdout lines (no flush
line, since `--flush-interval` was not given). Timing precision
is exactly 3 decimal places (`0.001 seconds.`), matching qemu's
`%0.3f` format.

#### 21. `qemu-img bench -w --flush-interval 50 -c 100 -d 1 probe.raw`

```
$ qemu-img bench -w --flush-interval 50 -c 100 -d 1 probe.raw
exit: 0
stdout:
Sending 100 write requests, 4096 bytes each, 1 in parallel (starting at offset 0, step size 4096)
Sending flush every 50 requests
Run completed in 0.004 seconds.
stderr:
WARNING: Image format was not specified for 'probe.raw' and probing guessed raw.
         Automatically detecting the format is dangerous for raw images, write operations on block 0 will be restricted.
         Specify the 'raw' format explicitly to remove the restrictions.
```

Three stdout lines when a write test has a nonzero
`--flush-interval`: the "Sending N write requests..." header,
then a distinct "Sending flush every N requests" line, then "Run
completed". This third-line case is what the task brief calls
"all three stdout lines" — plain reads (20) only ever show two.
Note also the unrelated stderr WARNING: because no `-f raw` was
given on a *write* test against a raw image, qemu's raw-format
auto-detection warning appears on stderr. This is not part of the
bench message contract itself but phase 4's CLI should pass an
explicit format to keep this noise out of `-w` runs.

### Supplement: `StepOutOfRange` (not in the original matrix)

The assigned invocation matrix did not include a `-S`/`--step-size`
out-of-range case, but `BenchParamError::StepOutOfRange` needs a
captured message like every other variant. Three extra probes
were run to fill this gap:

#### 22. `qemu-img bench -S -1 -c 100 probe.raw`

```
$ qemu-img bench -S -1 -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid step size specified. Must be between 0 and 2147483647.
```

#### 23. `qemu-img bench -S 2147483648 -c 100 probe.raw`

```
$ qemu-img bench -S 2147483648 -c 100 probe.raw
exit: 1
stdout:
stderr:
qemu-img: Invalid step size specified. Must be between 0 and 2147483647.
```

One past `QEMU_BENCH_ARG_MAX` (`2147483648`) triggers the same
text as a negative step — same zero/negative-collapse pattern
seen in `-c`/`--pattern` above. Note the lower bound in the
message is `0`, not `1` — step is the only one of the four
integer options where 0 is a valid, meaningful value ("= bufsize"),
consistent with `StepOutOfRange` only firing above the bound.

#### 24. `qemu-img bench -S 0 -c 100 probe.raw`

```
$ qemu-img bench -S 0 -c 100 probe.raw
exit: 0
stdout:
Sending 100 read requests, 4096 bytes each, 64 in parallel (starting at offset 0, step size 4096)
Run completed in 0.001 seconds.
stderr:
```

Confirms step 0 is valid and displays as the *effective* step
(`4096`, equal to bufsize) in the "step size" header field, not
as a literal `0` — direct empirical confirmation of
`effective_step()`'s "0 means bufsize" semantics reaching all the
way to qemu's own rendered output.

### Observed divergences from the master plan's expectations

- `-s 3G` is rejected by qemu's own `[1, 2147483647]` bufsize
  range check (invocation 5), not accepted and then separately
  capped — there is no scenario where qemu accepts a `-s` value
  instar's `BENCH_MAX_BUFSIZE` would reject, since qemu's own
  bound is already stricter for absurd sizes. This matches the
  Mission's stated precedence exactly; no divergence here, but it
  is worth confirming empirically rather than assuming.
- The timing line's precision is exactly 3 decimal digits
  (`%0.3f`-shaped: `0.001 seconds.`, `0.004 seconds.`) in every
  successful run observed — no divergence from the master plan's
  assumption.
- `-c 0` and `-c -1` (and likewise `--pattern 256`/`--pattern -1`,
  and the new `-S -1`/`-S 2147483648` supplement) produce
  byte-identical messages for the "too low" and "too high" sides
  of each range — qemu does not have distinct over/under messages
  per option. `BenchParamError`'s one-variant-one-message design
  already matches this.
- The "no filename" and "two filenames" cases (15, 16) are also
  byte-identical to each other, both routed through qemu's
  generic "Expecting one image file name" arg-count check.
- The zero-byte-image case (19) confirms there is genuinely no
  up-front size validation: the request header prints
  unconditionally before the first (failing) I/O is attempted,
  and the failure surfaces as `Failed request: Input/output
  error` with no "Run completed" line — exactly as the Mission's
  "no up-front bounds check... fails at request time" note
  predicts.
- Unplanned but relevant: a `-w` run against a raw image without
  an explicit `-f raw` prints an unrelated stderr WARNING about
  format auto-detection (invocation 21). This is real qemu-img
  output that would pollute a captured-message comparison if
  phase 4/6 don't pin the format explicitly in their own
  invocations; noting it here so later phases don't mistake it
  for part of the bench contract.

### Supplement 2 (phase 4a): unparseable values and the flush-interval range

Discovered during step 4a implementation (the original matrix
only probed out-of-range *numbers*, never non-numeric input, and
never probed `--flush-interval`'s own range). Re-verified against
the same local 10.0.8 binary, all exit 1:

- **Unparseable values do NOT collapse into the range message.**
  A genuinely non-numeric value produces a distinct, value-echoing
  form, uniformly across all seven numeric options:

  ```
  $ qemu-img bench -c abc probe.raw
  qemu-img: Invalid request count specified: 'abc'.
  ```

  and likewise `Invalid queue depth specified: 'abc'.`,
  `Invalid buffer size specified: 'abc'.`, `Invalid step size
  specified: 'abc'.`, `Invalid offset specified: 'abc'.`,
  `Invalid pattern byte specified: 'abc'.`, `Invalid flush
  interval specified: 'abc'.` — the option's display name, a
  colon, the offending value in single quotes, trailing period.
  The phase-4 plan's original "parse failures collapse into the
  range message" assumption was **wrong** and its §2 table has
  been corrected.

- **`--flush-interval` has its own range message**, absent from
  the original matrix:

  ```
  $ qemu-img bench --flush-interval -1 -w -c 10 -d 1 probe.raw
  qemu-img: Invalid flush interval specified. Must be between 0 and 2147483647.
  ```

  Bounds are `[0, 2147483647]` (0 = never flush, and
  `--flush-interval 0 -w` runs successfully). This range check
  fires before the cross-option rules.

- **Suffix-multiply overflow takes the range form, not the echo
  form** (qemu's cvtnum returns ERANGE for it, same as an
  out-of-range number):

  ```
  $ qemu-img bench -o 200000000000000G probe.raw
  qemu-img: Invalid offset specified. Must be between 0 and 9223372036854775807.
  $ qemu-img bench -s 200000000000000G probe.raw
  qemu-img: Invalid buffer size specified. Must be between 1 and 2147483647.
  ```

  instar's classifier maps `parse_qemu_img_size`'s overflow
  outcome to the range form accordingly; only genuinely
  non-numeric input gets the `: '<v>'.` echo form.

## Captured flush-count verification (step 1c)

Method: `strace -f -e trace=fdatasync,fsync` differencing against a
scratch 10 MiB raw image (`qemu-img create -f raw b.raw 10M`), running a
baseline `qemu-img bench -f raw -w -d 1 -c C -s 4096 -t writeback b.raw`
(no `--flush-interval`) and a flush run of the same command plus
`--flush-interval I`, counting `fdatasync`/`fsync` syscall lines in each.
All counts were stable across repeated runs.

| (count, interval) | baseline syscalls | flush-run syscalls | difference | `total_flushes()` | match? |
|---|---|---|---|---|---|
| (100, 50)  | 1 | 2 | 1 | 2 | yes (flush-run total) |
| (101, 50)  | 1 | 3 | 2 | 3 | yes (flush-run total) |
| (100, 100) | 1 | 1 | 0 | 1 | yes (flush-run total) |
| (75, 25)   | 1 | 3 | 2 | 3 | yes (flush-run total) |
| (1, 1)     | 1 | 1 | 0 | 1 | yes (flush-run total) |

Measurement-method note (formula NOT adjusted): the naive
`flush-run − baseline` difference lands one short of `total_flushes()`
in every vector, because the assumption behind differencing — that the
image-close flush fires unconditionally in both runs — is false. qemu's
close-path flush is dirty-conditional: a **read** test issues 0 flushes
total (nothing dirty at close), a **write** test with no
`--flush-interval` issues exactly 1 (the image is dirty, so close flushes
once — this is the baseline's single syscall), and a write **flush** run
always ends with the trailing bench flush at `k == count`
(`remaining == 0`, `0 % interval == 0`), which leaves the image clean and
therefore **suppresses** the close-path flush. So the correct comparison
is the flush-run *total* against `total_flushes()`, not the difference —
and the flush-run total matches exactly for all five vectors (2, 3, 1, 3,
1). The one-flush baseline is the close flush that the flush run no longer
needs. The formula is confirmed against the live qemu-img 10.0.8 binary;
the derivation from `bench_cb` stands, and only the differencing method
(which double-assumes an unconditional close flush) was corrected.

One epistemic footnote (management review): the flush-run totals alone
cannot distinguish "`total_flushes()` bench flushes with the close
flush suppressed" from "`total_flushes() − 1` bench flushes plus a
dirty-close flush" — both hypotheses predict identical syscall totals
on every vector above. The distinction is settled by the source, not
the strace: `bench_cb` unambiguously issues the trailing flush at
`remaining == 0` (`0 % x == 0` is true and `in_flight == 0` on the
serial path), and the trailing flush's placement *inside the timed
window* likewise comes from the source (the main loop only exits once
the flush completion has re-entered `bench_cb`). The empirical table
confirms the per-run flush *total*, which is the contract instar must
reproduce; the position claims rest on the quoted C.
