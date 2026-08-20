# `instar map` subcommand

## Status: Complete

Phases 1-9.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats, qemu-img semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-map-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img`
subcommands deferred from the convert effort. `create`,
`measure`, `resize`, `rebase`, and `commit` have all shipped.
`map` and `snapshot` remain. `map` is being scheduled next
because:

- It is the natural follow-on to `measure`. Both are read-only
  operations that walk the source image's allocation structures.
  `measure` already extracted the per-format walks behind a
  `scan_allocation()` method on each parser crate; `map` needs
  the *same* walks but yielding per-extent records instead of a
  rolled-up `AllocationSummary`. Most of the parsing work is
  exposing what the existing walkers already iterate.
- It is read-only on the source image and produces no mutated
  output. There is no new on-disk writer to validate, no
  cross-version compatibility surface on the output side (the
  output is text), and no new call-table primitive — `map` only
  reads sectors, which is already supported.
- It is a known consumer requirement: `docs/usage.md` lists
  `map` as required by oVirt / VDSM / ovirt-imageio in the
  consumer-coverage matrix. None of the v0.2.0 customers
  currently need it, but the oVirt integration story does.
- It is bounded in scope. `qemu-img map` produces a list of
  contiguous extents with start / length / depth / present /
  zero / data / offset / filename. For a single-image (no
  backing chain) v1, depth is always 0, filename is constant,
  and the only non-trivial work is coalescing adjacent
  same-state extents and emitting them over the serial
  command channel without buffering the whole list in guest
  memory.

The relevant existing infrastructure this plan builds on:

- The per-parser `scan_allocation()` methods added in
  `PLAN-measure` phase 2
  (`src/crates/{qcow2,vmdk,vhd,vhdx,raw}/src/lib.rs`). These
  already walk every relevant on-disk allocation structure;
  `map` extends them with a detail-yielding variant that emits
  one record per source cluster / grain / block instead of a
  running total.
- VMM subcommand scaffolding in `src/vmm/src/main.rs`
  (clap `Commands` enum, per-op `*Args` struct, `run_*`
  function), call-table boundary in `src/shared/src/lib.rs`
  (`OPERATION_CONFIG_ADDR`, per-op `*Config` and `*Result`
  structs), and the protobuf wrapper in
  `crates/guest-protocol/proto/guest.proto`
  (`GuestMessage` oneof payload — next free tag is 15).
- The streaming guest-message channel used by `info`, `check`,
  `convert`, and `commit` for progress reporting, which is the
  natural transport for an unbounded extent stream.
- The cross-version baseline generator
  (`instar-testdata/scripts/generate-baselines.py`) and its
  `expected-outputs/{info,check,compare,measure}-{human,json}/`
  layout, which is the mechanism we extend for the qemu-img
  map matrix.
- The coverage-guided fuzz harnesses in `src/fuzz/` and the
  differential fuzzer (`scripts/differential-fuzz.py`) which
  already runs random qemu-img-generated images against
  `info`, `check`, `convert`, `measure`, and the rebase /
  commit chain.

## Mission and problem statement

Implement `instar map` such that:

1. It accepts the same surface area as `qemu-img map`:
   - A required source `FILENAME` (plus optional `-f FMT`).
   - `--output=json` or `--output=human` (default human),
     matching qemu-img's column layout and JSON field names
     byte-for-byte where the data is well-defined.
   - `--start-offset=OFFSET` and `--max-length=LEN`, mirroring
     qemu-img: start emission at the cluster covering
     `OFFSET`, and stop at the cluster covering
     `OFFSET + LEN`. The trimming happens at extent boundaries,
     not by splitting a single source extent across the bound
     (qemu-img matches this).
   - `--image-opts` explicitly rejected with a clear error,
     consistent with `measure`'s handling.
2. The format-parsing work runs entirely inside the KVM guest,
   exactly like every other operation. Untrusted input never
   touches the host.
3. For raw and qcow2 sources, results match `qemu-img map`
   byte-for-byte across the qemu-img-binaries matrix in
   `instar-testdata/qemu-img-binaries/x86_64/` (with
   documented, version-keyed expected divergences where
   qemu-img's own output changed between versions).
4. For vmdk / vhd / vhdx sources (which qemu-img *does*
   support for `map`), results match qemu-img where the
   underlying allocation walk is unambiguous. Known divergences
   (e.g. vhdx partial-present block state, vmdk multi-extent
   propagation — both listed as future work in `PLAN-measure`)
   are documented in `docs/quirks.md` rather than asserted.
5. Backing-chain composition (qemu-img's per-extent `depth`
   field walking through `info --chain`) is deferred to a
   follow-up phase. v1 emits depth=0 only and refuses sources
   with a backing file with a clear error pointing at the
   follow-up. Single-image map covers the oVirt use case
   (image upload / download sizing) and is the bulk of the
   work.
6. Coverage-guided fuzzing exercises the per-format extent
   iterators directly, and the existing differential fuzzer is
   extended to compare `instar map` against `qemu-img map` on
   every randomly generated image.

## Design overview

### Architectural shape

The work decomposes into three layers:

1. **Per-format extent iterators** on each parser crate.
   Given a parser-opened image, yield a stream of
   `MapExtent { virtual_offset, length, state, file_offset }`
   records, where `state` is one of `Data { file_offset } |
   ZeroAllocated | Hole`. Adjacent same-state extents are
   coalesced inside the parser (qemu-img coalesces at the same
   layer). The iterator does not buffer — it pulls one source
   cluster / grain / block at a time and yields when state
   transitions occur.

2. **Guest binary** `src/operations/map/`. Reads `MapConfig`
   from `OPERATION_CONFIG_ADDR`, opens device 0, dispatches on
   the detected source format to the matching extent iterator,
   filters by `start_offset` / `max_length`, and streams one
   `MapExtentMessage` per emitted extent over the serial
   command channel, terminated by a `MapResultMessage`
   summary (total extents emitted, error code).

3. **Host glue**: `run_map()` in `src/vmm/src/main.rs` that
   wires up clap args, builds `MapConfig`, launches the guest,
   accumulates the streamed `MapExtentMessage` records, and
   renders qemu-img-compatible human or JSON output on
   stdout.

Splitting the parser iterator from the guest binary keeps the
parsers `cargo test`-able with synthetic images, and keeps the
fuzz harness for the iterators trivial (no KVM, no serial
channel).

### Streaming vs. buffering

`qemu-img map` is unbounded in output size — a maximally
fragmented image with one allocated source cluster between
every hole produces O(virtual_size / cluster_size) extents.
A 1 TiB qcow2 at the 64 KiB default could in principle emit
~17 M extents. We cannot buffer that in the 384 KiB guest
binary's heap; we cannot reliably buffer it on the host
either. Therefore: one `MapExtentMessage` per coalesced
extent, written incrementally over the serial command
channel. The host renders each message as it arrives (for
human output) or accumulates them into the JSON array
incrementally (for JSON output, by writing `[`, then each
comma-separated object, then `]`).

The existing serial-channel framing already supports this
shape: `info`'s `--chain` output, `check`'s per-cluster error
reports, and `convert`'s progress messages all stream through
the same `GuestMessage`-per-line newline-delimited path.

### Call-table and protobuf changes

- New `MapConfig` in `src/shared/src/lib.rs` next to
  `MeasureConfig`, with magic `MAP_`, fields:
  - `start_offset: u64` (default 0)
  - `max_length: u64` (0 means "to end of image")
  - reserved padding for forward compat (chain depth, future
    backing-chain mode).
- New `MapExtentMessage` in `guest.proto`:
  ```
  message MapExtentMessage {
    uint64 start = 1;       // virtual offset in source image
    uint64 length = 2;      // extent length in bytes
    uint32 depth = 3;       // 0 in v1; reserved for chain
    bool present = 4;       // mapped to backing storage
    bool zero = 5;          // reads as zero
    bool data = 6;          // contains data (not zero-fill)
    uint64 file_offset = 7; // where in source file; 0 if !present
    bool has_file_offset = 8; // qemu-img omits when unallocated
  }
  ```
- New `MapResultMessage` in `guest.proto`:
  ```
  message MapResultMessage {
    uint64 extents_emitted = 1;
    uint64 virtual_size = 2;
    uint32 error = 3;       // mirrors MapResult::ERROR_* in shared
  }
  ```
- Both added to the `GuestMessage` oneof: `MapExtentMessage`
  as field 15, `MapResultMessage` as field 16.

The call table itself needs no new function pointers — `map`
only reads sectors, which is already supported.

### qemu-img output format

`qemu-img map --output=human` produces (note: hex offsets,
right-aligned fields, header row):

```
Offset          Length          Mapped to       File
0               0x100000        0x50000         /path/file.qcow2
0x100000        0x100000                        
```

Important quirks to match:
- Hex notation uses lowercase `0x` prefix.
- The `Mapped to` and `File` columns are blank when the extent
  is unallocated (not present in backing storage).
- The column widths are fixed (4 columns at 16 characters each
  with the filename column trailing).
- An unallocated trailing region at end-of-image is included
  as a final blank-mapping row.

`qemu-img map --output=json` produces a JSON array of
objects:

```json
[
  { "start": 0, "length": 1048576, "depth": 0, "present": true,
    "zero": false, "data": true, "offset": 327680,
    "filename": "/path/file.qcow2" },
  { "start": 1048576, "length": 1048576, "depth": 0,
    "present": false, "zero": true, "data": false }
]
```

The `offset` and `filename` fields are omitted (not null) when
the extent is unallocated. We must match this — qemu-img
parsers in downstream tools (oVirt) are field-presence
sensitive.

We document these quirks in `docs/map.md` and add a
`map-human` / `map-json` profile to the output-profile
machinery in `src/vmm/src/main.rs` if any qemu-img version in
the matrix diverges in formatting; spot-checks across recent
qemu versions show stable output back to 6.0.0 but the matrix
must be verified.

### Per-format extent iteration

Concrete walks the per-format iterators implement. All build
on the existing `scan_allocation` walks but yield per-cluster
records instead of summing.

- **raw**: one extent spanning the whole virtual size,
  `state = Data { file_offset: 0 }`. qemu-img on raw
  *does* probe `SEEK_HOLE` / `SEEK_DATA` to split into
  sparse / non-sparse extents; we match `measure`'s
  current behaviour and treat raw as fully allocated, with
  the qemu-img divergence documented (and the eventual
  `SEEK_HOLE` host-side prepass listed as future work,
  same as `measure`).
- **qcow2**: walk L1 → L2 in virtual-address order. For each
  L2 entry, classify:
  - Zero/unallocated (L2 entry 0): emit `Hole`.
  - `QCOW2_CLUSTER_ZERO_PLAIN` / `_ZERO_ALLOC`: emit
    `ZeroAllocated`.
  - Compressed cluster: emit `Data` with the compressed
    file offset; mark `data=true, zero=false`. qemu-img
    reports compressed clusters as data with a file offset.
  - Normal allocated cluster: emit `Data { file_offset }`
    with the L2 host-cluster offset.
  Coalesce consecutive same-state clusters when their
  file offsets are contiguous (data extents stop coalescing
  on non-contiguous file offsets, matching qemu-img).
  Extended L2 / subcluster bitmaps: emit at subcluster
  granularity when the bitmap is not uniform; otherwise
  coalesce the whole cluster.
- **vmdk monolithicSparse**: walk grain directory → grain
  table; one record per grain. Allocated grains emit `Data`
  with the grain file offset; unallocated emit `Hole`.
  Coalesce contiguous file-offset-adjacent grains.
- **vmdk monolithicFlat**: every byte is `Data` with
  the flat extent's file offset; one extent covering
  the virtual size.
- **vmdk streamOptimized**: read-only quirks: grain markers
  are interleaved with data. The existing parser already
  resolves grain → file offset for `convert`; reuse that
  resolver here.
- **vhd dynamic**: walk BAT; `0xFFFFFFFF` entries emit `Hole`,
  other entries emit `Data` with the block file offset.
- **vhd fixed**: one extent covering the virtual size,
  `Data { file_offset: 0 }`.
- **vhdx dynamic**: walk BAT, skipping the interleaved
  sector-bitmap entries. `PAYLOAD_BLOCK_FULLY_PRESENT`
  emits `Data` with the block file offset. `_ZERO` and
  `_UNMAPPED` emit `Hole`. `_PARTIALLY_PRESENT` is treated
  as `Data` in v1 (matches the existing
  `scan_allocation()` simplification; the per-sector
  bitmap walk is listed as future work).
- **luks** (out of scope for v1): defer.

The iterator return type is an `Iterator<Item = MapExtent>`
that the guest pulls one at a time, sends as
`MapExtentMessage`, and discards. No buffering required.

### Source allocation scope and chain composition

For v1: single-image only. The guest refuses sources with a
backing file pointer (`backing_file_offset != 0` for qcow2,
parent locator chains for vhdx, parent-locators on vhd, and
the multi-extent descriptor with non-zero file backing for
vmdk) with a clear error message that points at the chain
follow-up.

This matches the `PLAN-measure` v1 scope (which also only
reports the top layer) and gets us out the door without
having to design the depth-tracking protocol over the
extent stream.

The chain follow-up (single phase, post-v1) feeds multiple
parser iterators into a host-side or guest-side shadowing
walker that emits the correct `depth` field per extent.
The protobuf already reserves the `depth` field for it.

### qemu-img scope and test matrix

`qemu-img map` accepts every format we parse (no
"unsupported by block driver" error path like `measure` has
for vmdk/vhd/vhdx). The test matrix is therefore symmetric:

| Source format | Compare against |
|---------------|-----------------|
| raw           | qemu-img        |
| qcow2         | qemu-img        |
| vmdk          | qemu-img        |
| vhd           | qemu-img        |
| vhdx          | qemu-img        |

The matrix is qemu-img matrix × source images × output
format (human/json) × `--start-offset` / `--max-length`
combinations.

### Versioning and baseline strategy

We extend `instar-testdata/scripts/generate-baselines.py` to
add a `map` command entry alongside `info`, `check`,
`compare`, and `measure`. For each `(qemu-img version,
source image, start_offset, max_length, output type)` tuple
we capture stdout (human and json), stderr, and exit code
into `expected-outputs/map-{human,json}/<src_format>/<version>/<image-id>[--start-offset=N][--max-length=N].{stdout,stderr,meta.json}`.

The baseline matrix size:
- ~80 qemu-img versions
- 2 output types (human, json)
- ~30-40 representative source images (safe-tier only)
- A handful of `--start-offset` / `--max-length` combinations
  (default, mid-image start, end-of-image start, max-length
  smaller than one cluster, max-length larger than image)

That is ~50k files, comparable in scale to the existing
measure baseline tree. JSON outputs are bounded by the
extent count of the source image; the safe-tier images are
small enough that this stays manageable (largest expected
single output ~200 KiB).

### Why extend `scan_allocation` rather than write a new iterator from scratch

`scan_allocation` already walks every on-disk structure we
need. Refactoring it to expose the per-cluster records
behind the existing summary is much less code than writing
a parallel walker. The expected shape is:

```rust
pub trait AllocationIter {
    type Iter: Iterator<Item = MapExtent>;
    fn extents(&self) -> Self::Iter;
}
```

and `scan_allocation()` becomes a one-line consumer:
`self.extents().fold(AllocationSummary::default(),
|acc, e| acc.add(e))`. That refactor is the first sub-step
of phase 1.

## Open questions

1. **Backing-chain `depth` field**: Confirmed deferred to a
   follow-up. The v1 guest refuses sources with a backing
   pointer; the test matrix excludes chain images. The
   `depth` proto field is reserved and emitted as 0 in v1
   so the on-wire format is stable across the eventual
   chain support.

2. **`--image-opts`**: Same posture as `measure` — reject
   explicitly with a clear error; document in
   `docs/quirks.md`.

3. **Raw `SEEK_HOLE` / `SEEK_DATA` probing**: qemu-img map
   reports the on-disk sparseness of raw inputs. instar's
   no_std raw parser cannot do this from inside the guest
   (no syscall surface). Recommendation: v1 reports raw as
   one fully-allocated extent; document divergence in
   `docs/quirks.md`. Host-side prepass (VMM does `lseek`
   before launching the guest and passes a precomputed
   extent list via MapConfig) is the eventual fix —
   listed as future work alongside the analogous
   `measure` follow-up.

4. **vhdx `PAYLOAD_BLOCK_PARTIALLY_PRESENT`**: Treated as
   `Data` in v1 (consistent with `scan_allocation`'s
   current behaviour). qemu-img walks the per-sector
   bitmap and emits per-sector extents. Documented
   divergence; listed as future work.

5. **vmdk multi-extent descriptor propagation**: Same
   posture as `measure` — v1 reports the top extent only;
   multi-extent walks are future work.

6. **qcow2 extended-L2 subcluster bitmap**: qemu-img map
   walks the bitmap and emits one extent per uniform
   subcluster run. Confirm the rounding rule by reading
   `block/qcow2-cluster.c` `qcow2_co_block_status` and
   pinning the version range it's stable across (same
   read we did during `measure` phase 1).

7. **Streaming throughput**: Each `MapExtentMessage` over
   the serial channel is on the order of 50 bytes encoded.
   Worst-case 17 M extents = ~850 MiB serial traffic at
   ~30 minutes wall time. This is a known cost of the
   format; qemu-img on the same image takes about half
   that. If the differential fuzzer trips this, we cap
   the corpus at a smaller virtual size (the corpus
   generator already does this for runtime reasons).

8. **MapResult struct vs protobuf-only**: Every other
   operation has a `*Result` struct in
   `src/shared/src/lib.rs` for early/error states. For map
   the result is a one-shot summary at end-of-stream; the
   protobuf message is sufficient. Recommendation:
   mirror `measure` — protobuf only, no `MapResult`
   struct beyond an `ERROR_*` constant table on a
   marker struct. Confirm during phase 2.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Per-format extent iterators on parser crates | [PLAN-map-phase-01-extent-iterators.md](/components/instar/plans/PLAN-map-phase-01-extent-iterators/) | Complete (MapExtent / MapExtentState / MapExtentCoalescer in shared; map_extents walker on each of raw / qcow2 / vmdk / vhd / vhdx) |
| 2. Guest `map` operation + protobuf | [PLAN-map-phase-02-guest-op.md](/components/instar/plans/PLAN-map-phase-02-guest-op/) | Complete (operations/map guest binary at ~28 KiB / 384 KiB; MapExtentMessage + MapResultMessage protobufs; CallTable send_map_extent + send_map_result; VERSION 15→16) |
| 3. Host VMM subcommand + clap surface | [PLAN-map-phase-03-host-cli.md](/components/instar/plans/PLAN-map-phase-03-host-cli/) | Complete (MapArgs + Commands::Map; run_map dispatches into the guest; --image-opts / VMDK descriptor / invalid sector-size rejected host-side) |
| 4. Output formatting (human / JSON) | [PLAN-map-phase-04-output-formatting.md](/components/instar/plans/PLAN-map-phase-04-output-formatting/) | Complete (streaming MapRenderer<'a, W: Write>; human + JSON match qemu-img byte-for-byte modulo documented quirks; 21 byte-exact unit tests) |
| 5. Cross-version baseline generation in `instar-testdata` | [PLAN-map-phase-05-baselines.md](/components/instar/plans/PLAN-map-phase-05-baselines/) | Complete (instar-testdata commits `4e56008d8`, `8e0498ca3`, `315859c3d`, `0f972d5b1`; 80 versions; 1 map-human profile + 3 map-json profiles) |
| 6. Integration tests (`tests/test_map.py`) | [PLAN-map-phase-06-integration-tests.md](/components/instar/plans/PLAN-map-phase-06-integration-tests/) | Complete (95 active tests + 91 documented skips; two real bugs fixed mid-phase — JSON trailing newline, host-side start-offset check vs file-size) |
| 7. Coverage-guided fuzz harnesses | [PLAN-map-phase-07-fuzz-coverage.md](/components/instar/plans/PLAN-map-phase-07-fuzz-coverage/) | Complete (fuzz_map_iter target landed; 60s smoke ~4M runs, 0 crashes, ongoing coverage growth) |
| 8. Differential fuzzing extension | [PLAN-map-phase-08-fuzz-differential.md](/components/instar/plans/PLAN-map-phase-08-fuzz-differential/) | Complete (op_map landed; 200-iter smoke clean after a per-format `present`-field skip for vpc tracking the documented VHD-unallocated-block convention divergence) |
| 9. Documentation, CHANGELOG, follow-ups | [PLAN-map-phase-09-docs.md](/components/instar/plans/PLAN-map-phase-09-docs/) | Complete (docs/map.md authored; cross-document touch-ups landed; PLAN-convert-followups strikethrough applied; master plan / docs/plans/index.md flipped to Complete) |

### Phase notes (not yet detailed plans)

These are intentionally short — each gets its own phase plan
once the previous phase has landed and the working code has
clarified the brief.

**Phase 1 — Per-format extent iterators**. Add an
`AllocationIter`-style trait to each parser crate
(`qcow2`, `vmdk`, `vhd`, `vhdx`, `raw`) that yields
`MapExtent` records, and refactor the existing
`scan_allocation()` to consume the iterator. The
shape lives in `shared` so the guest can name it without
pulling every parser crate. Unit tests: feed a small
synthetic image of each format with a known allocation
pattern (5 contiguous allocated clusters, 3-cluster gap,
2 allocated clusters, 5-cluster trailing hole) and assert
the iterator emits exactly 4 extents with the expected
byte ranges. The qcow2 iterator additionally tests
extended-L2 subcluster bitmaps and compressed clusters.

Recommended effort: **high**. Recommended model: **opus**.
The qcow2 extended-L2 / compressed-cluster classification
is where bugs hide; the qemu-img reference behaviour is
in `block/qcow2-cluster.c` `qcow2_co_block_status` and
needs careful reading. The other four iterators are
mechanical but the trait shape needs to be right the
first time (it lives in `shared` and is hard to evolve).

**Phase 2 — Guest map operation**. New
`src/operations/map/` binary built like `info`, `check`,
and `measure`. Linker script identical to other
operations (load 0x20000, 384 KiB cap). Reads `MapConfig`
from `OPERATION_CONFIG_ADDR`, opens device 0, refuses
sources with a backing file pointer (clear error
message), runs the format-detected extent iterator with
the `start_offset` / `max_length` filter, and streams
one `MapExtentMessage` per emitted extent followed by a
final `MapResultMessage`. Add the `MapConfig` struct and
both protobuf messages. Add `map` to the workspace
`members` list and to the build scripts that copy guest
binaries into the VMM.

Recommended effort: **high** (touches call-table boundary,
two new proto fields, guest binary scaffolding, streaming
protocol). Recommended model: **opus**.

**Phase 3 — Host VMM subcommand**. Add `MapArgs` and
`Commands::Map(MapArgs)`, `run_map()`. clap surface
mirrors qemu-img: `FILENAME`, `-f FMT`,
`--output={human,json}`, `--start-offset=OFFSET`,
`--max-length=LEN`. The launching pattern follows
`run_measure`; the streaming-consumer pattern follows
`run_check` (which consumes per-error messages as they
arrive). Reject `--image-opts` with a clear error.

Recommended effort: **medium**. Recommended model:
**sonnet** with a brief that names `run_measure` for the
launching pattern and `run_check` for the streaming
consumer.

**Phase 4 — Output formatting**. Two renderers in
`src/vmm/src/main.rs` (or a new
`src/vmm/src/map_output.rs` if it gets long):
- Human: header row + per-extent row with hex-formatted
  start / length / file_offset, blank columns for
  unallocated extents. Column widths fixed at 16 chars.
- JSON: streaming array writer. Open `[`, write each
  extent object as it arrives with a leading comma after
  the first, close `]` with a trailing newline. Field
  presence rules: `offset` and `filename` omitted (not
  null) for unallocated extents.

Add `map-human` and `map-json` output profiles to the
existing profile machinery if the baseline matrix
reveals any version-to-version formatting drift.

Recommended effort: **medium**. Recommended model:
**sonnet** with a brief that names `print_info_result`
for the field-presence pattern.

**Phase 5 — Cross-version baselines**. In
`instar-testdata/scripts/generate-baselines.py`, add a
`map` command entry with `output_types = {map-human:
None, map-json: 'json'}` and a `build_cmd` that emits
the source-image queries with the
`--start-offset` / `--max-length` combinations listed
above. `supported_formats` for map is all formats.
Run the generator under every binary in
`qemu-img-binaries/x86_64/` to produce
`expected-outputs/map-{human,json}/`. Capture
deduplicated profile metadata via the existing
`detect-profiles.py` flow.

Recommended effort: **medium** for the script change,
**low** for the (long-running but mechanical) baseline
generation pass. Recommended model: **sonnet**.

**Phase 6 — Integration tests**. New `tests/test_map.py`
covering:
- For each safe-tier image in `manifest.json` and each
  installed qemu-img version: run `instar map`
  (json + human), compare to the matching baseline.
  Skip baselines that don't exist for the installed
  version (use the same version-keyed filtering as
  `test_oslo_crossval.py`).
- `--start-offset` / `--max-length` cases for each
  format: assert the bounds are honoured.
- Error paths: missing source, `--image-opts` rejected,
  source with backing file rejected with the expected
  message, oversized start-offset, conflicting flags.
- A stress case: a synthetic maximally-fragmented qcow2
  (alternating 1-cluster data / 1-cluster hole over a
  64 MiB virtual size) — verify the output is correct
  and that the streaming path doesn't OOM the guest.

Tests use the existing `InstarTestBase` helpers and the
manifest filtering used by `test_measure.py`.

Recommended effort: **medium**. Recommended model:
**sonnet**.

**Phase 7 — Coverage-guided fuzz harnesses**. New fuzz
target in `src/fuzz/fuzz_targets/`:
- `fuzz_map_iter.rs`: format-prefixed input feeds the
  existing fuzz mock CallTable; calls
  `<format>::extents()` for each parser and drains the
  iterator. Asserts no panics, no integer overflows, no
  unbounded loops, and that the emitted extents partition
  `[0, virtual_size)` (every byte covered exactly once).

The `partition` invariant is the key bug-finder — it
catches off-by-one cluster boundary errors that
`scan_allocation` summarised away.

Recommended effort: **medium**. Recommended model:
**opus** for harness design (the partition invariant
is subtle to express portably), **sonnet** for the
boilerplate following.

**Phase 8 — Differential fuzzing extension**. In
`scripts/differential-fuzz.py`, add `map` to the random
operation chain. For each generated image (where the
source format is map-supported by both instar and the
installed qemu-img): run `instar map --output=json` and
`qemu-img map --output=json`, parse both, and compare
extent-by-extent. Allowed divergences are an enumerated
list (raw `SEEK_HOLE` sparseness, vhdx partial-present,
vmdk multi-extent) — anything else is a bug. The CI
workflow needs no change beyond the script update.

Recommended effort: **medium**. Recommended model:
**sonnet**.

**Phase 9 — Documentation and CHANGELOG**. New
`docs/map.md` covering CLI surface, per-format extent
classification rules, qemu-img divergences (raw
sparseness, vhdx partial-present, vmdk multi-extent,
`--image-opts` rejected, backing-chain depth deferred).
Update `docs/usage.md`, `docs/quirks.md`,
`docs/index.md`, `README.md`, `AGENTS.md` (add the new
operation to the operations list), `ARCHITECTURE.md`
(Format Support section gets a "Mappable source formats"
line — all five), `CHANGELOG.md` (under Unreleased / next
version), and `PLAN-convert-followups.md` (mark map as
done, removing it from the deferred list; `snapshot`
becomes the sole remaining item).

Recommended effort: **low**. Recommended model:
**sonnet** or **haiku**.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making.

The workflow per step:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan.
3. **Review** the sub-agent's output in the management
   session. Read the actual files; don't trust the summary.
4. **Fix or retry** if the output is wrong.
5. **Commit** once the management session is satisfied.

Use `isolation: "worktree"` for risky steps (anything that
edits the call table or proto, anything that runs the
baseline generator across the qemu-img matrix). Steps that
only touch one new file in `src/operations/map/` or one new
test file can run in the main tree.

### Planning effort

This master plan is high-effort. Phases 1, 2, and 7 are
high effort. Phases 3, 4, 5, 6, 8 are medium. Phase 9 is
low.

### Step-level guidance

Each phase plan should fill in the table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
```

following `PLAN-TEMPLATE.md` conventions.

### Management session review checklist

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed
      (read them).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (384 KB
      limit per operation).
- [ ] `make test-rust` and the relevant
      `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — semantically
      right, not just syntactically.
- [ ] Commit message follows project conventions
      (Co-Authored-By with model + context window + effort,
      Signed-off-by, Prompt paragraph).

## Administration and logistics

### Success criteria

The plan is complete when:

* All 9 phases complete and committed on the `map` branch.
* `make instar` builds with `map.bin` within the 384 KiB
  operation-binary cap.
* `make lint` clean across the workspace.
* `make test-rust` passes; new tests in shared / parser
  crates raise totals as documented in each phase plan.
* `make test-integration` includes `tests/test_map.py`;
  test count and pass/skip breakdown documented in each
  phase plan.
* `make check-binary-sizes` includes `map.bin`.
* `pre-commit run --all-files` clean throughout.
* For all five source formats: `instar map` matches
  `qemu-img map` extent-for-extent (both `--output=human`
  and `--output=json`) across every qemu-img version in
  `instar-testdata/qemu-img-binaries/x86_64/`
  (6.0.0–10.2.0) per the baseline matrix, modulo the
  enumerated divergences (raw sparseness, vhdx
  partial-present, vmdk multi-extent).
* Coverage-guided fuzz target `fuzz_map_iter` registered
  in nightly CI; differential fuzzer's random operation
  chain includes `map`.
* `docs/map.md`, `docs/quirks.md`, `docs/usage.md`,
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `CHANGELOG.md` all updated.
* `PLAN-convert-followups.md` strikes `map` from the
  deferred-subcommand list.

### Future work

* **Backing-chain `depth` composition.** v1 refuses chain
  sources. The follow-up phase feeds multiple parser
  iterators into a shadowing walker that emits the
  correct `depth` per extent. The protobuf already
  reserves the field.

* **Raw-source `SEEK_HOLE` / `SEEK_DATA` detection.** Same
  shape as the analogous `measure` follow-up: VMM does
  the `lseek` scan before launching the guest and passes
  a precomputed sparse-extent list via MapConfig; the
  guest skips the trivial raw walk.

* **VHDX partial-present per-sector bitmap.** Phase 1's
  vhdx iterator treats `PAYLOAD_BLOCK_PARTIALLY_PRESENT`
  as fully present. qemu-img walks the per-sector bitmap
  and emits per-sector extents.

* **VMDK multi-extent descriptor propagation.** Phase 1's
  vmdk iterator handles single-extent monolithic layouts.
  Multi-extent (e.g. 2GbMaxExtentSparse) needs the
  descriptor-driven extent map.

* **Compressed-cluster sub-classification.** v1 emits
  compressed qcow2 clusters as `Data` with the
  compressed file offset. qemu-img reports them with a
  distinguishable marker (the high-bit-set offset
  convention) that some downstream tools depend on.
  Investigate and match if needed.

* **`-l SNAPSHOT` snapshot-targeted mapping.** Reuses
  convert's snapshot machinery (`--snapshot ID`).

* **`--image-opts` parsing.** Defer until a real user
  requests it.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` and
`docs/plans/order.yml`. Phase files are linked from the
Execution table above and are *not* added to `order.yml`.

When all phases are complete, update the row in
`index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
