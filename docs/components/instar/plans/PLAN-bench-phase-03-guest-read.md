# PLAN-bench phase 03: guest op read path

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the guest-op skeleton
in `src/operations/map/` and `src/operations/bitmap/`, convert's
chain setup and read loop in `src/operations/convert/src/main.rs`,
the `qcow2` crate's chain read dispatch), and ground your answers
in what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named `PLAN-bench-phase-NN-<descriptive>.md`.
The master plan is [PLAN-bench.md](/components/instar/plans/PLAN-bench/). This is the
third of eight phases; phase 1
([PLAN-bench-phase-01-crate.md](/components/instar/plans/PLAN-bench-phase-01-crate/))
landed the pure `crates/bench` schedule crate and phase 2
([PLAN-bench-phase-02-abi.md](/components/instar/plans/PLAN-bench-phase-02-abi/)) landed
the complete ABI (`BenchConfig`/`BenchResult` in `src/shared`,
`send_bench_start`/`send_bench_result` at call-table VERSION 20).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

This phase lands `src/operations/bench/` — the no_std guest
binary that executes the read benchmark. It consumes the phase-2
ABI (casts `BenchConfig`, calls `send_bench_start` /
`send_bench_result`) and the phase-1 schedule crate
(`BenchParams`, `OffsetSchedule`). It is **build- and
size-gated only**: nothing launches it until phase 4's host CLI,
so the gates are `make instar`, the 768 KiB op budget, lint, and
the existing test suites staying green.

Grounding (verified against the current tree; the memory-map lift
of 2026-07-06 applies — ops load at 0x30000 with a 768 KiB
budget):

- **The read dispatch already exists and is the whole game.**
  `qcow2::read_chain_virtual_range`
  (`src/crates/qcow2/src/lib.rs:6243`) fills exactly `len` bytes
  at an arbitrary `virtual_offset`, looping internally in
  cluster-bounded steps. Its inner
  `read_chain_virtual_cluster` (`:5531`) dispatches on
  `chain_config.devices[dev].detected_format()` (`:5551`) and
  handles every bench-relevant format in one walk: qcow2 with
  `ClusterLookup` and backing-chain fall-through
  (`Unallocated ⇒ continue` to the parent device, `:5570`),
  compressed clusters (deflate and zstd, `:5880-5980`, using a
  compressed-scratch and a staging buffer), subcluster recursion,
  vmdk grains incl. compressed (`:5984`), flat/descriptor vmdk
  via data devices (`:6162`), vhd fixed+dynamic (`:6078`), vhdx
  (`:6138`), and raw (`:6204` →
  `read_raw_sectors`/`read_cluster_sectors`, whose byte-accurate
  covering-sector path `:2717-2759` handles arbitrary unaligned
  offsets). **One bench request = one
  `read_chain_virtual_range` call.** This is also exactly
  Open question 12's requirement: the same cached machinery
  convert measures, no instar-only re-parsing per request.
- **Op-lifetime cached state**: `qcow2::ChainStates`
  (`:6362`) holds per-device `Qcow2State` (cached L1/L2 sectors)
  plus feature-gated vmdk/vhd/vhdx states.
  `qcow2::init_chain_states` (`:6388`) does the one-time header
  parse and table load for every chain device. Convert's `_start`
  is the reference setup sequence
  (`src/operations/convert/src/main.rs:191-288`): validate call
  table → read config → read `ChainConfig` at
  `CHAIN_CONFIG_ADDR` → `verify_sector_sizes`
  (`src/shared/src/lib.rs:4456`) → scratch layout →
  `ChainStates::default()` + `init_chain_states`.
- **Skeleton anatomy** (map is the minimal exemplar): per-op
  `Cargo.toml` (`[[bin]] name` drives the `.bin` name;
  `panic="abort"`, `opt-level="z"`, `lto=true`), `linker.ld`
  (`OPERATION_BASE = 0x30000`), `.cargo/` config
  (x86_64-unknown-none + linker script), `src/main.rs` with
  `#[panic_handler]`, `get_call_table()`,
  `validate_call_table!`, config cast, and a `u64` return (core
  HLTs after `_start` returns — **no** trailing infinite loop).
  Registration points: `src/Cargo.toml` workspace members;
  `src/build.sh` (three spots: build+objcopy block, copy to
  `target/release/`, size check); `scripts/check-binary-sizes.sh`
  op list (line ~120); `Makefile` `test-rust` `--exclude` list
  and `CARGO_TOML_FILES`. **Naming**: the pure crate is already
  package `bench`, so the op package must be `bench-op` with
  `[[bin]] name = "bench"` — precisely the `map`/`map-op`
  pattern.
- **Error/terminal contract** (bitmap/map pattern): every exit
  path sends the typed result (error code inside) and then an
  unconditional `send_complete(b"bench\0", bytes_read, ok)`;
  `send_error` is an optional mid-stream diagnostic, never the
  terminal signal. The phase-2 ABI contract additionally allows
  `BenchResult` without a preceding `BenchStart` for pre-loop
  failures.
- **Scratch conventions**: op buffers grow up from
  `SCRATCH_MEM_BASE` (0x300000) and must stay below
  `ALLOC_HEAP_BASE` (top-of-scratch 512 KiB bump heap);
  convert's `ScratchLayout` + compile-time assert
  (`convert/src/main.rs:63-117`) is the pattern. Decompression
  requires `shared::bump_allocator!()`, `extern crate alloc`,
  and a `HEAP_POS.store(0, ...)` reset before reads that may
  decompress (convert resets per read, `:1666`).
- **Progress**: `map` and `bitmap` send no progress messages at
  all — a progress-free op is idiomatic.
- The survey that grounded this plan predates none of the ABI:
  `BenchConfig` (128 B, flags/count/depth/bufsize/step/offset/
  flush_interval/pattern/target_format/sector_size),
  `BenchResult` (error, requests_completed, flushes_issued,
  error_detail) and both senders are already in the tree from
  phase 2 — this phase only consumes them.

## Mission

### 1. Guest flow (the `_start` sequence)

```
validate_call_table!(call_table, "bench")        // version 20
config = &*(OPERATION_CONFIG_ADDR as *const BenchConfig)
  !config.is_valid()          → result(ERROR_BAD_CONFIG), complete(false)
  config.flags & FLAG_WRITE   → result(ERROR_BAD_CONFIG), complete(false)
                                 // phase 5 replaces this branch with the
                                 // write path; the phase-4 host never sets
                                 // FLAG_WRITE until then. Comment it as such.
chain_config = &*(CHAIN_CONFIG_ADDR as *const ChainConfig)  + is_valid()
verify_sector_sizes(...)
probe: read sector 0 of device 0, detect_format_from_header, and
  cross-check against config.target_format and
  chain_config.devices[0].detected_format(); LUKS or any
  format outside {raw, qcow2, vmdk, vhd, vhdx}
                              → result(ERROR_UNSUPPORTED_FORMAT), complete(false)
image_size = chain_config.devices[0].virtual_size   // convert's source of truth
build BenchParams from config (raw step; effective_step() stays in the crate)
init: ChainStates::default() + init_chain_states(...)
  parse failure               → result(ERROR_PARSE_FAILED), complete(false)
schedule = OffsetSchedule::new(&params, image_size)
(call_table.send_bench_start)()        // ← the timing bracket opens HERE,
                                       // after ALL setup, before the first
                                       // request (phase-2 doc contract)
for offset in schedule:
    HEAP_POS reset (cheap; needed when the read decompresses)
    read_chain_virtual_range(..., offset, dest_buf, bufsize, ...)
      failure → send_error diagnostic (convert idiom), then
                result(ERROR_IO_READ, requests_completed=k,
                       error_detail=offset), complete(false)
result(ERROR_OK, requests_completed=count, flushes_issued=0)
complete(true)
```

`bytes_read` (the `send_complete` payload) accumulates through
the existing read helpers' `bytes_read` out-params as in convert.

### 2. One request = one `read_chain_virtual_range` call

Phase 1's `TransferSplit` is **not used in the read path**, and
this is a deliberate, recorded decision (settling the phase-1
leaning for the read case): `read_chain_virtual_range` already
performs the sub-request chunking internally at cluster/sector
granularity — every virtio transfer it issues is ≤
`MAX_SECTOR_SIZE` by construction — so wrapping it in an outer
64 KiB split would add loop overhead and nothing else. One
request keeps its qemu meaning (bufsize contiguous bytes at one
offset). `TransferSplit` remains load-bearing for the phase-5
write path (which drives sector writes directly) and the phase-7
fuzz invariants. Consequences the implementation must honour:

- The destination buffer is a fixed `BENCH_MAX_BUFSIZE` (2 MiB)
  scratch region; the read data is deliberately discarded
  (qemu's is too).
- A request whose `[offset, offset + bufsize)` extends past
  `image_size` must fail as `ERROR_IO_READ` with
  `error_detail = offset` — reachable only via a raw `-o` at or
  near EOF, since the master-rule wrap keeps every subsequent
  offset inside `[0, image_size - bufsize]`. Verify
  `read_chain_virtual_range`'s behaviour on out-of-range reads
  during implementation (it bounds-checks device reads; the op
  must turn a `false`/short return into the error result, not
  panic, and must pre-check `offset + bufsize > image_size`
  itself if the helper's contract is looser).
- `image_size == 0` (schedule pins offsets to 0): the first read
  of `bufsize ≥ 1` bytes fails the same way — matching qemu's
  header-then-`Failed request` behaviour captured in phase 1e.

### 3. No progress messages inside the timed window

The request loop sends **no** `send_progress` (and does not call
`get_progress_interval`). Two reasons, recorded here so nobody
adds it later: qemu-img bench prints nothing between the header
and completion lines, and a progress message is serial-port
vmexits *inside the measured bracket* — the measurement must not
carry instar-only chatter proportional to request count. map and
bitmap are the progress-free precedents. (`--verbose` runs still
see the start marker and result render from phase 2's
`format_message` arms.)

### 4. Scratch layout

Constants at the top of `main.rs`, convert-style, with a
compile-time guard:

```
BUF_DEST        = SCRATCH_MEM_BASE                     (BENCH_MAX_BUFSIZE = 2 MiB)
BUF_COMPRESSED  = BUF_DEST + BENCH_MAX_BUFSIZE         (COMPRESSED_BUF_SIZE)
BUF_STAGING     = BUF_COMPRESSED + COMPRESSED_BUF_SIZE (MAX_CLUSTER_SIZE)
DYNAMIC_START   = BUF_STAGING + MAX_CLUSTER_SIZE       (per-device L1/L2 caches,
                                                        2 × MAX_SECTOR_SIZE ×
                                                        MAX_CHAIN_DEVICES)
const _: () = assert!(DYNAMIC_START + 2*MAX_SECTOR_SIZE*MAX_CHAIN_DEVICES
                      <= ALLOC_HEAP_BASE);
```

(~8.2 MiB of the ~12.4 MiB below the heap — comfortable.) The
crate asserts `bench::BENCH_MAX_BUFSIZE == shared::MAX_CLUSTER_SIZE
as u64` at the consumer boundary, per the phase-1 contract. Sector-0
probing may reuse `BUF_DEST`. `bump_allocator!()` + `extern crate
alloc` are required because the qcow2/vmdk deps carry the
decompression features (mirror convert's `Cargo.toml` feature
list).

### 5. Registration and naming

Package `bench-op`, `[[bin]] name = "bench"` → `bench.bin`.
Touch list: `src/operations/bench/{Cargo.toml, linker.ld,
.cargo/, src/main.rs}` (copy linker.ld/.cargo verbatim from map);
`src/Cargo.toml` members (`"operations/bench"`); `src/build.sh`
build+objcopy block, `target/release/` copy, and size-check
entry; `scripts/check-binary-sizes.sh` op loop; `Makefile`
`test-rust --exclude bench-op` and `CARGO_TOML_FILES`. Expected
size: convert (all formats + write machinery) is 299 KiB; a
read-only bench with the same format features should land well
under that in the 768 KiB budget — record the measured number in
this plan.

### 6. Resolved open question

**OQ12 (per-request overhead):** resolved by construction — the
request loop reuses convert's exact cached path
(`init_chain_states` once, `ChainStates` + 
`read_chain_virtual_range` per request). No format re-probe, no
L1 re-walk, no allocation beyond the per-request `HEAP_POS`
reset. The bracket excludes all setup (Mission §1) and contains
no instar-only per-request messages (Mission §3).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Op skeleton + all registrations (Mission §5): `bench-op` package with map's linker.ld/.cargo/profile copied verbatim, a minimal `main.rs` that validates the call table + config and emits `BenchResult(ERROR_BAD_CONFIG)` + `send_complete` (a stub that builds and runs the full emission path), workspace member, the three `src/build.sh` spots, `scripts/check-binary-sizes.sh` op list, both Makefile lists. Gates: `make instar` builds `bench.bin`, size check lists it OK, `make lint` + `make test-rust` green. Commit 1. |
| 3b | high | opus | none | The real op per Mission §1-§4: chain-config read + `verify_sector_sizes`, sector-0 probe + format gate, scratch layout with the compile-time guard, `init_chain_states`, `BenchParams`/`OffsetSchedule` from the config, `send_bench_start` placement exactly per the phase-2 doc contract, the request loop (one `read_chain_virtual_range` per offset, `HEAP_POS` reset, EOF/short-read → `ERROR_IO_READ` with `error_detail=offset`), every exit path ending in result + `send_complete`. High effort: bracket placement is the product's semantics, the EOF edge behaviour must match the phase-1e capture, and the read-helper failure contract must be verified, not assumed. Gates: build + size + lint + test-rust. Commit 2. |
| 3c | low | sonnet | none | Record the measured `bench.bin` footprint in this plan (Captured measurements section), update the master-plan phase-3 row and `docs/plans/index.md`. `pre-commit run --all-files`. Commit 3. |

Functional verification is deliberately deferred to phase 4's
first end-to-end smoke test — there is no way to launch this op
until `run_bench` exists.

## Captured measurements (step 3c)

`scripts/check-binary-sizes.sh` on the tree at the end of step 3b
(`aeffeb9`), with `make instar` confirmed a no-op beforehand:

```
OK:   bench (0x30000-0xF0000) - 144KB / 768KB (18%, .bin=148352B)
```

and the script's final verdict, `All binaries fit within their
memory regions.`

This is the read-only op with convert's full qcow2 feature set
linked in (decompress + zstd, plus vmdk/vhd/vhdx input support) —
the same format coverage Mission §5's estimate was measured
against. The comparison anchor from that estimate: convert (all
formats *and* the write machinery bench does not need) is 299 KiB
of the same 768 KiB budget. bench lands at under half of convert's
footprint while covering every read-side format, which is the
expected shape for a read-only op sharing convert's parsing and
decompression code but carrying none of its allocating-write path.

Scratch footprint as built matches Mission §4's layout: `BUF_DEST`
(2 MiB) + `BUF_COMPRESSED` + `BUF_STAGING` (one `MAX_CLUSTER_SIZE`
each) + `DYNAMIC_START`'s per-device L1/L2 caches (2 ×
`MAX_SECTOR_SIZE` × `MAX_CHAIN_DEVICES`) come to a ~8.2 MiB
high-water mark below `ALLOC_HEAP_BASE`, exactly as sized in the
Mission §4 estimate, and the compile-time `assert!` at the bottom
of that layout enforces the bound at build time rather than
leaving it as a runtime hope.
