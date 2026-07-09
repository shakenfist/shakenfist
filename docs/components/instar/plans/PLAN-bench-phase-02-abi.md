# PLAN-bench phase 02: guest ABI (config, result, timing marker)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the shared ABI crate
`src/shared/src/lib.rs`, the call-table append discipline, the
guest-protocol proto/builder pairing, the core serial senders,
the mock call tables in `src/fuzz` and the qcow2 test suite), and
ground your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead. The
canonical recipe for this phase is the bitmap ABI addition
(commit `547104b`, VERSION 18→19) — read that commit's diff
before writing any code. Flag any uncertainty explicitly.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named `PLAN-bench-phase-NN-<descriptive>.md`.
The master plan is [PLAN-bench.md](/components/instar/plans/PLAN-bench/). This is the
second of eight phases; phase 1
([PLAN-bench-phase-01-crate.md](/components/instar/plans/PLAN-bench-phase-01-crate/))
landed the pure `crates/bench` schedule crate.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase lands the **host↔guest ABI** for bench: the
`BenchConfig` / `BenchResult` structs in `src/shared`, two new
call-table senders (`send_bench_start`, `send_bench_result`)
bumping `CallTable::VERSION` 19→20, the protobuf wire messages,
the core serial shims, and the compile-forced ripples (mock call
tables, vmm message rendering). It produces no behaviour change —
nothing calls the new senders until phase 3 (guest op) and
nothing harvests the messages until phase 4 (host CLI) — but
after this phase every later phase is purely additive on its own
side of the boundary.

Grounding (verified against the current tree on the `bench`
branch and the survey performed for this plan):

- **The shared ABI crate is the single source of truth.**
  `src/shared` is a path-dependency of the host (`src/vmm`), the
  guest core (`src/core`), and every guest op. `CallTable` is a
  `#[repr(C)]` table of `unsafe extern "C" fn` pointers at
  `src/shared/src/lib.rs:704-990`; core writes it to
  `CALL_TABLE_ADDR`, ops read it via `get_call_table()` and gate
  on `validate_call_table!` (`src/shared/src/lib.rs:4219-4230`).
  The last entry is `send_bitmap_result:
  unsafe extern "C" fn(*const BitmapResult)` at line 989. New
  entries are **always appended at the end** for offset
  back-compat; `pub const VERSION: u32 = 19` at line 1318
  carries a doc-comment history (lines 1309-1317) that each bump
  extends. A unit test pins the constant:
  `assert_eq!(CallTable::VERSION, 19)` at
  `src/shared/src/lib.rs:5608`.
- **The canonical recipe** is bitmap's VERSION 18→19 addition
  (commit `547104b`). Its touch list, which this phase replicates
  with two senders instead of one: shared struct + call-table
  field + VERSION bump + version-test update; proto message +
  oneof arm; guest-protocol builder; core serial sender + `ct_*`
  thunk + call-table-literal field; vmm `format_message` arm;
  mock-call-table stubs.
- **Config struct idiom** (`BitmapConfig`,
  `src/shared/src/lib.rs:3681-3775`): `#[repr(C)]
  #[derive(Clone, Copy)]`, a `MAGIC` u32, `FLAG_*` bit constants
  with `FLAG_VERBOSE = 1 << 31`, an `is_valid()` checking magic,
  a `_reserved` tail padding to a round size, and a compile-time
  `const _: () = assert!(size_of::<...>() <=
  OPERATION_CONFIG_MAX_SIZE)`. The host writes the struct
  field-by-field to `OPERATION_CONFIG_ADDR = 0x81000`
  (`src/shared/src/lib.rs:331`; 4 KiB max); the guest op casts
  the fixed address. Result struct idiom (`BitmapResult`,
  `src/shared/src/lib.rs:3785-3807`): magic, `error: u32` with
  `ERROR_*` constants rendered host-side, numeric fields only,
  `_reserved` padding to 64 bytes.
- **Wire protocol**: protobuf `GuestMessage` with a
  `oneof payload`; the tag roster ends at `bitmap_result = 20`
  (`crates/guest-protocol/proto/guest.proto:448`). Builders live
  in `crates/guest-protocol/src/lib.rs` (e.g.
  `bitmap_result_message` at line 750). Core serial senders
  (`src/core/src/serial.rs`, e.g. `send_bitmap_result` at 630)
  are deliberately **numeric-passthrough — no string or lookup
  tables** — because core.bin is near its 72 KiB ceiling.
  Thunks (`ct_send_bitmap_result`,
  `src/core/src/main.rs:719`) null-check and delegate; the
  call-table literal (`src/core/src/main.rs:266-305`) registers
  them.
- **Compile-forced ripples.** Adding a `CallTable` field breaks
  every call-table literal: the fuzz mock
  (`src/fuzz/src/lib.rs:96`, stub fns near line 314, plus the
  version assertion near line 390) and the qcow2 test mock
  (`src/crates/qcow2/src/lib.rs:4804`). Adding proto oneof
  variants breaks the vmm's `format_message`
  (`src/vmm/src/main.rs:824-1000`) — its payload match is
  exhaustive over `Some(...)` variants (verified: only the level
  match and `None` have catch-alls), so the two new render arms
  land in this phase even though the harvest loop is phase 4.
- **Mid-run marker precedent is strong.** map streams
  `send_map_extent` before a terminal `send_map_result`
  (`src/shared/src/lib.rs:935,943`); snapshot streams
  `send_snapshot_entry` before `send_snapshot_result` (952, 962);
  `send_progress` (756) is emitted mid-operation by the long ops.
  A `send_bench_start` marker followed by a terminal
  `send_bench_result` mirrors this shape exactly.
- **core.bin budget.** `scripts/check-binary-sizes.sh` measures
  the `.bss`-inclusive ELF memory extent against the core region
  `[0x10000, 0x22000)` = 72 KiB, with an 85% early-warning. Last
  measured (bitmap's addition, commit `547104b`): ~66.7 KiB,
  ~5.3 KiB headroom — already past the warning threshold, so the
  warning line is expected; the hard gate is 72 KiB. Each sender
  costs a thunk, a serial builder, and a proto message type in
  core. This phase re-measures after the addition (step 2c). The
  script's hardcoded op list does **not** gain `bench` in this
  phase — `bench.bin` first exists in phase 3.
- **Flush needs no new I/O ABI.** `fsync_input` (VERSION 17,
  `src/shared/src/lib.rs:975`) fdatasyncs an RW-attached input
  slot; bitmap already uses the RW-input-slot-0 + `fsync_input(0)`
  idiom (`src/operations/bitmap/src/main.rs:1272` etc.). Bench
  write tests will attach the image RW-input and reuse it
  verbatim.

## Mission

### 1. Resolve Open question 3: dedicated start-marker slot

**Decision: a dedicated `send_bench_start` call-table slot, with
the sentinel-progress fallback held in reserve.** Rationale:

- The timing bracket is the load-bearing element of the whole
  subcommand — the number bench prints is defined as "host
  wall-clock between BenchStart receipt and BenchResult receipt".
  A dedicated, typed, self-documenting message is worth one table
  slot; overloading `send_progress` with a sentinel value would
  make the single most important event in the protocol implicit.
- It is the cheapest possible sender: no arguments, an empty
  proto message (the arrival time *is* the payload), a two-line
  thunk, a three-line serial builder. If any pair of senders
  fits the remaining headroom, this pair does.
- The streaming-before-terminator shape (map, snapshot) is
  established; verbose runs render a clean
  `bench start` line for free.

**Fallback, only if step 2c measures core.bin over the 72 KiB
hard gate:** drop the `send_bench_start` slot and field (before
anything ships there is no compat cost), emit a reserved
`send_progress(BENCH_START_SENTINEL)` instead, and record the
decision here. Do not pre-emptively take the fallback; measure
first.

### 2. Resolve Open question 5: flush wiring

**Decision: no new I/O ABI.** Bench attaches the image as input
device slot 0 — read-only for read tests, RW (bitmap-style) for
`-w` — and `--flush-interval` uses the existing `fsync_input(0)`.
`read_input_sector` / `write_input_sector` / `fsync_input` cover
everything phases 3 and 5 need. This phase adds **only** the two
result-path senders.

### 3. `BenchConfig` in `src/shared`

`#[repr(C)] #[derive(Clone, Copy)]`, immediately after
`BitmapConfig`'s block, following its idiom exactly. Bench's
config is **pure CLI parameters — deliberately no host-probed
image cross-checks** (unlike `BitmapConfig`): bench runs on all
five input formats and the guest derives `image_size` from its
own format parse (phase 3), so there is nothing the host can
probe without host-side parsing that the security model exists to
avoid. Layout (56 payload bytes + reserved tail = 128 total; u32
block / u64 block / u32 block ordering leaves no implicit
padding):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct BenchConfig {
    /// Magic (`0x424E4348` = "BNCH").
    pub magic: u32,
    /// Flags. See `FLAG_*`.
    pub flags: u32,
    /// Number of requests (1..=0x7fffffff, validated host-side).
    pub count: u32,
    /// Requested queue depth. Echoed in the header line and JSON
    /// only; the guest ignores it — v1 execution is serial
    /// (master plan Open question 1).
    pub depth: u32,

    /// Bytes per request (1..=BENCH_MAX_BUFSIZE).
    pub bufsize: u64,
    /// Raw step; `0` means "= bufsize" (`crates/bench`
    /// `effective_step()` — the guest resolves it, keeping the
    /// wrap arithmetic in one place).
    pub step: u64,
    /// Initial offset, raw and unwrapped (first request uses it
    /// as-is, matching qemu).
    pub offset: u64,

    /// Flush every N completions; `0` = never. Nonzero only with
    /// FLAG_WRITE (validated host-side).
    pub flush_interval: u32,
    /// Write-pattern byte in the low 8 bits.
    pub pattern: u32,
    /// Input format hint (`ImageFormat as u32`); the guest
    /// verifies against its own parse.
    pub target_format: u32,
    /// Sector size for I/O (matches host sector_size).
    pub sector_size: u32,

    /// Reserved padding for forward compatibility (zero-init);
    /// pads the total to a round 128 bytes.
    pub _reserved: [u8; 72],
}
```

Constants: `MAGIC`, `FLAG_WRITE: u32 = 1 << 0`,
`FLAG_NO_DRAIN: u32 = 1 << 1`, `FLAG_VERBOSE: u32 = 1 << 31`
(house convention), `is_valid()` checking magic, and the
compile-time `OPERATION_CONFIG_MAX_SIZE` assert. Also assert
`size_of::<BenchConfig>() == 128` the way the layout-pinning
tests for other configs do (check how `BitmapConfig`'s 3584-byte
layout is asserted and mirror it).

Note: because nothing consumes `BenchConfig` until phase 3,
field additions discovered during phases 3-5 are free — but they
must go into `_reserved`, never reorder existing fields.

### 4. `BenchResult` in `src/shared`

Mirrors `BitmapResult` (numeric-only, host renders strings):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct BenchResult {
    /// Magic (`0x424E5253` = "BNRS").
    pub magic: u32,
    /// Error code. See `ERROR_*`.
    pub error: u32,

    /// Requests fully completed (== count on success; the count
    /// reached when a mid-run request failed otherwise).
    pub requests_completed: u64,
    /// Flushes issued (`crates/bench` `total_flushes()` on
    /// success).
    pub flushes_issued: u64,
    /// Error detail: the byte offset of the failing request for
    /// the I/O errors, else 0.
    pub error_detail: u64,

    /// Reserved padding for forward compatibility (zero-init);
    /// pads the total to 64 bytes.
    pub _reserved: [u8; 32],
}
```

Initial `ERROR_*` roster (phases 3 and 5 append; values are
stable once assigned): `ERROR_OK = 0`, `ERROR_BAD_CONFIG = 1`
(magic/flag validation failed in the guest),
`ERROR_UNSUPPORTED_FORMAT = 2`, `ERROR_PARSE_FAILED = 3`,
`ERROR_IO_READ = 4`, `ERROR_IO_WRITE = 5`, `ERROR_IO_FLUSH = 6`.

### 5. Call-table append + VERSION 20

Append after `send_bitmap_result` (line 989), in this order:

```rust
pub send_bench_start: unsafe extern "C" fn(),
pub send_bench_result: unsafe extern "C" fn(*const BenchResult),
```

with doc comments in the house style ("Appended at the end of
`CallTable` for the same back-compat reason as ..."). The
`send_bench_start` doc comment must pin the **timing contract**,
because it is the ABI-level meaning of the message:

- The guest emits it after config validation, format
  probe/open, cached-state setup, and buffer/transfer-plan
  setup — immediately before submitting the first request
  (mirroring qemu's `gettimeofday` bracket placement).
- The host records `Instant::now()` on receipt; elapsed is
  measured to `BenchResult` receipt. The trailing flush (when
  `flush_interval` divides such that the final completion
  flushes) is inside the bracket, per phase 1's verified cadence.
- `BenchResult` **may arrive without a preceding
  `BenchStart`** (validation/probe failure before the timed
  loop): the host must render the error without a timing line,
  never assume the bracket opened.
- Marker cost (a few serial-port IoOut vmexits, ~µs) is noise at
  bench's ms-to-s scale — recorded so nobody "optimizes" it.

Bump `VERSION` to 20, extend the doc history (lines 1309-1317)
with "PLAN-bench phase 2 appended `send_bench_start` and
`send_bench_result` for the bench subcommand's timing bracket",
and update the pin test at line 5608.

### 6. Proto messages, builders, core shims, ripples

- `crates/guest-protocol/proto/guest.proto`: append

  ```proto
  // Bench timing-bracket start marker. Deliberately empty: the
  // host timestamps its arrival; there is no payload.
  message BenchStartMessage {
  }

  message BenchResultMessage {
    // Error code: 0 = ok, non-zero mirrors BenchResult::ERROR_*
    uint32 error = 1;
    uint64 requests_completed = 2;
    uint64 flushes_issued = 3;
    uint64 error_detail = 4;
  }
  ```

  with oneof arms `bench_start = 21` and `bench_result = 22`
  (append-only tag discipline).
- `crates/guest-protocol/src/lib.rs`: `bench_start_message()`
  and `bench_result_message(...)` builders mirroring
  `bitmap_result_message` (line 750). Levels: mirror the house
  pattern — check what level the existing `*_result` builders
  set (result messages) and what `progress_message` sets, and
  use Progress-analog for the start marker, result-analog for
  the result.
- `src/core/src/serial.rs`: `send_bench_start()` and
  `send_bench_result(&shared::BenchResult)` after
  `send_bitmap_result` (line 630) — numeric passthrough, no
  string tables (the 72 KiB ceiling comment there applies
  verbatim).
- `src/core/src/main.rs`: `ct_send_bench_start` /
  `ct_send_bench_result` thunks after `ct_send_bitmap_result`
  (line 719; null-check idiom), registered at the end of the
  call-table literal (line 304).
- `src/fuzz/src/lib.rs`: mock stubs for both fields (defs near
  line 314, literal at 96) and the version assertion near line
  390.
- `src/crates/qcow2/src/lib.rs:4804`: test-mock stubs for both
  fields.
- `src/vmm/src/main.rs` `format_message`: render arms for
  `Payload::BenchStart` ("bench start") and
  `Payload::BenchResult` (error, requests_completed,
  flushes_issued, error_detail), matching the surrounding style
  near line 935. **No** `BenchArgs`, no `run_bench`, no harvest
  loop — that is phase 4. *Scope correction found during 2a:
  these arms belong to step 2a's commit, not 2b's — the proto
  oneof addition alone makes the exhaustive match non-exhaustive,
  so the workspace does not build without them.*

### 7. Re-measure core.bin (Open question 10)

After the addition: `make instar`, then
`scripts/check-binary-sizes.sh`. Record the measured
`.bss`-inclusive core footprint and remaining headroom in this
plan (Captured measurements section, added by step 2c) and in
the master plan's execution-table status. If the hard 72 KiB
gate fails, take the §1 fallback and re-measure. `make lint`,
`make test-rust`, and `pre-commit run --all-files` must all
pass; guest-side integration behaviour is untestable until
phase 4 (the existing integration suite must stay green — this
phase changes no behaviour).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Append `BenchStartMessage` (empty) and `BenchResultMessage` to `crates/guest-protocol/proto/guest.proto` with oneof tags 21/22, plus `bench_start_message()` / `bench_result_message()` builders in `crates/guest-protocol/src/lib.rs`, mirroring the bitmap pair (proto line 323/448, builder line 750) including message-level choices copied from `progress_message` / the `*_result` builders. Also the two `format_message` render arms in `src/vmm/src/main.rs` (~line 935) — compile-forced by the oneof addition, so they cannot wait for 2b. Commit 1. |
| 2b | high | opus | none | The lockstep compile unit, following commit `547104b`'s recipe with the exact layouts and doc-comment contracts in Mission §3-§6: `BenchConfig`/`BenchResult` + constants + asserts in `src/shared/src/lib.rs`; call-table append + VERSION 19→20 + history doc + pin-test update; `send_bench_start`/`send_bench_result` in `src/core/src/serial.rs`; thunks + literal registration in `src/core/src/main.rs`; mock stubs + version assert in `src/fuzz/src/lib.rs`; test-mock stubs in `src/crates/qcow2/src/lib.rs` (the `format_message` arms already landed with 2a). High effort because the `send_bench_start` doc comment carries the timing contract, the struct layouts are pinned to the byte, and a missed mock literal or version assert breaks the workspace build in non-obvious places. `make instar` + `make lint` + `make test-rust` green. Commit 2. |
| 2c | low | sonnet | none | Build, run `scripts/check-binary-sizes.sh`, append a "Captured measurements" section to this plan (core.bin `.bss`-inclusive footprint before/after, headroom vs the 72 KiB gate, whether the 85% warning fired), update the master-plan execution row and `docs/plans/index.md` phase link/status. If the hard gate failed, stop and report — the §1 fallback is a management-session decision, not a mechanical fix. Commit 3. |

## Resolved open questions

- **OQ3 (start marker):** dedicated `send_bench_start` slot;
  sentinel-progress fallback only if the 72 KiB hard gate fails
  (Mission §1).
- **OQ5 (flush wiring):** RW-input attach + existing
  `fsync_input`; zero new I/O ABI (Mission §2).
- **OQ10 (ABI footprint):** `BenchConfig` fixed at 128 bytes
  (well under the 4 KiB config page), `BenchResult` at 64 bytes,
  two appended slots, VERSION 19→20; core.bin re-measured by
  step 2c with the fallback path predefined (Mission §7).

## Captured measurements (step 2c)

- **core.bin before this phase** (bitmap's addition, commit
  `547104b`): ~66.7 KiB .bss-inclusive extent, ~5.3 KiB headroom
  against the 72 KiB core region (Situation, "core.bin budget").
- **core.bin after this phase:** `make instar` followed by
  `scripts/check-binary-sizes.sh` on the tree with steps 2a and 2b
  landed produces:

  ```
  WARN: core (0x10000-0x22000) - 68KB / 72KB (94%, .bin=68416B)
  ```

  i.e. a 68416-byte flat `.bin`, 94% of the 72 KiB region, ~4.4 KiB
  headroom remaining. The growth versus the pre-phase measurement
  is attributable to the two senders landed in steps 2a/2b: the
  `ct_send_bench_start` / `ct_send_bench_result` thunks
  (`src/core/src/main.rs`), the two new `serial.rs` builders
  (`send_bench_start`, `send_bench_result`), and the two new proto
  message types (`BenchStartMessage`, `BenchResultMessage`)
  compiled into core's protobuf encoding path.
- The script's 85% early-warning fires — as it already was firing
  before this phase, per the bitmap-era ~5.3 KiB-headroom
  measurement above — and the 72 KiB hard gate passes:
  `scripts/check-binary-sizes.sh`'s final line reads "All binaries
  fit within their memory regions."
- **Consequence for Mission §1 (OQ3):** the hard gate held, so the
  dedicated `send_bench_start` call-table slot stands as landed;
  the sentinel-`send_progress` fallback described there was **not**
  taken.
- **Note for phase 3:** ~4.4 KiB of core headroom remains. The
  bench guest op itself adds nothing further to core — the two
  thunks above are the full cost already counted here — so the
  next pressure on this budget is whichever future plan appends
  the next call-table sender. Check the actual field count in
  `CallTable` (`src/shared/src/lib.rs`) before citing a slot
  number in a future plan rather than trusting this document's
  count to still be current; simplest is to just say "the next
  call-table append".
