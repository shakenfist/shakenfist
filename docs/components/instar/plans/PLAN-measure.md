# `instar measure` subcommand

## Status: Complete

Phases 1-10.

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
master plan are named
`PLAN-measure-phase-NN-<descriptive>.md` alongside this file and
linked from the Execution table below. They are *not* added to
`docs/plans/order.yml` — only the master plan is.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img`
subcommands deferred from the convert effort. `measure` is one of
them. It is deliberately scheduled before the other six because:

- It is the smallest in scope: no metadata mutation, no chain
  rewriting, no host-side image creation. It is read-only on
  the source image.
- It exercises every existing parser crate's allocation map
  (`qcow2` L1/L2 + refcount, `vmdk` grain directory/table,
  `vhd` BAT, `vhdx` BAT, `raw`) at the same time, which both
  validates the parsers in a new dimension and produces a small
  reusable library (`crates/measure/`) that `create`, `resize`,
  and `commit` will later reuse.
- `qemu-img` exposes `measure` for raw and qcow2 *outputs*
  only, but accepts every input format we already parse — so
  we get differential coverage on the read side for free, and
  every new instar-only target format (vmdk, vhd, vhdx) is a
  pure round-trip test against `convert`.
- The output is two integers and a small JSON envelope, which
  makes baseline-driven cross-version testing tractable across
  the full ~80-version qemu-img matrix already maintained in
  `instar-testdata/qemu-img-binaries/x86_64/`.

The relevant existing infrastructure this plan builds on:

- VMM subcommand scaffolding in `src/vmm/src/main.rs`
  (clap `Commands` enum, per-op `*Args` struct, `run_*`
  function), call-table boundary in `src/shared/src/lib.rs`
  (`OPERATION_CONFIG_ADDR`, per-op `*Config` and `*Result`
  structs), and the protobuf wrapper in
  `crates/guest-protocol/proto/guest.proto`
  (`GuestMessage` oneof payload).
- `no_std` parser crates (`src/crates/{qcow2,vmdk,vhd,vhdx,raw,luks}`)
  with their `cached_read!` sector readers — these already
  walk the on-disk allocation structures during `info` /
  `check` / `convert` and can yield an allocation map with a
  thin extension.
- The cross-version baseline generator
  (`instar-testdata/scripts/generate-baselines.py`) and its
  `expected-outputs/{info,check,compare}-{human,json}/` layout,
  which is the mechanism we extend to capture
  `qemu-img measure` baselines against the full version matrix
  (currently 6.0.0 through 10.2.0 on x86_64).
- The coverage-guided fuzz harnesses in `src/fuzz/` and the
  `cargo-fuzz` corpus produced by
  `scripts/extract-fuzz-corpus.py`.
- The differential fuzzer (`scripts/differential-fuzz.py`)
  which already runs random qemu-img-generated images against
  `info`, `check`, and `convert` chains and compares to the
  installed `qemu-img`.

## Mission and problem statement

Implement `instar measure` such that:

1. It accepts the same surface area as `qemu-img measure`:
   - Either a source `FILENAME` (plus optional `-f FMT`) or
     `--size SIZE`.
   - `-O target-format` (default `raw`).
   - `-o key=value,...` per-target-format options matching
     qemu-img where they exist (cluster_size / refcount_bits /
     extended_l2 / lazy_refcounts / compat for qcow2;
     subformat / grain_size for vmdk; block_size for
     vhd/vhdx; preallocation where meaningful).
   - `--output=json` and `--output=human` matching qemu-img's
     fields (`required`, `fully-allocated`) byte-for-byte where
     the math is well-defined, with documented divergences in
     `docs/quirks.md` where it isn't.
   - `-l SNAPSHOT` for QCOW2 source snapshots, mirroring
     `convert --snapshot` (deferrable to a follow-up phase
     if it complicates the initial cut; see Open questions).
2. The format-parsing work runs entirely inside the KVM guest,
   exactly like every other operation. Untrusted input never
   touches the host. The `--size` mode (no source image) does
   not need a source virtio device, but should still execute
   in the guest so there is one code path, not two.
3. For raw and qcow2 outputs, results match `qemu-img measure`
   across the full qemu-img-binaries matrix (with documented,
   version-keyed expected divergences where qemu-img's own
   answer changed between versions).
4. For vmdk / vhd / vhdx outputs (which `qemu-img measure` does
   not support), `instar measure` predicts the size that
   `instar convert -O <fmt>` would actually produce, with
   round-trip tests asserting predicted == actual.
5. Coverage-guided fuzzing exercises the size-calculator
   functions and the source-allocation scanner directly, and
   the existing differential fuzzer is extended to compare
   `instar measure` against `qemu-img measure` on every
   randomly generated image (where the target format is
   supported by both).

## Design overview

### Architectural shape

The work decomposes into three independent layers:

1. **Pure size calculators** (per output format). Given
   `(virtual_size, allocated_byte_extents, options)` return
   `(required, fully_allocated)`. No I/O. These are pure
   functions on the output side — `qemu-img`'s implementation
   lives in each block-driver's `bdrv_measure` callback and we
   mirror that shape. New crate `src/crates/measure/` (no_std,
   no parser dependencies) so it is independently fuzzable.

2. **Source-allocation scanners** (per input format). Given
   a parser-opened image, return a compact allocation map
   (an iterator yielding `(virtual_offset, length,
   allocated)` runs, or just a count of allocated bytes
   aligned to the *source* cluster/grain/block boundary).
   These extend the existing parser crates. QCOW2 already
   walks L1/L2 in `qcow2::lookup_cluster`; VMDK, VHD, VHDX
   already walk their grain/BAT tables in `convert`. The
   work is to expose those walks as a dedicated allocation
   iterator so they can be consumed without doing any data
   I/O.

3. **Glue**: a new guest binary `src/operations/measure/`
   that ties layers 1+2 together, talks to virtio-block
   through the call table, and emits a
   `MeasureResultMessage` over the serial command channel;
   plus a host-side `run_measure()` in
   `src/vmm/src/main.rs` that wires up clap args, builds
   `MeasureConfig`, launches the guest, and renders
   human/JSON output.

Splitting layer 1 from layer 2 keeps the math
unit-testable in plain `cargo test` (no KVM, no fuzzer
needed) and keeps the fuzz harness for layer 1 trivial.

### Call-table and protobuf changes

- New `MeasureConfig` in `src/shared/src/lib.rs` next to
  `ConvertConfig`, with magic `MEAS`, fields:
  - `target_format: u32` (reuses `ImageFormat` enum)
  - `virtual_size_override: u64` (non-zero ⇒ size-only mode,
    bypass source scan)
  - `cluster_size: u32`, `refcount_bits: u8`,
    `flags: u32` (extended_l2, lazy_refcounts,
    compress, compat_v3),
  - `subformat: [u8; 32]` (vmdk-specific, ASCII),
  - `grain_size: u32`, `block_size: u32`,
  - reserved padding for forward compat.
- New `MeasureResult` (or reuse the protobuf path only —
  see open question below).
- New `MeasureResultMessage` in `guest.proto`:
  ```
  message MeasureResultMessage {
    uint64 required = 1;
    uint64 fully_allocated = 2;
    string target_format = 3;
    // Optional: echo back the cluster_size / block_size used,
    // so JSON output can include the resolved values when
    // qemu-img defaults differ from instar defaults.
    uint32 resolved_cluster_size = 4;
    uint32 resolved_block_size = 5;
  }
  ```
  Added to the `GuestMessage` oneof as field 10.

The call table itself does not need new function pointers —
measure only reads sectors, which is already supported.

### qemu-img output format

`qemu-img measure --output=json` produces:

```json
{
    "required": 327680,
    "fully-allocated": 10813440
}
```

`--output=human` produces:

```
required size: 327680
fully allocated size: 10813440
```

Note the JSON key uses a hyphen (`fully-allocated`); the human
output uses two words. We must match exactly. The existing
output-profile machinery in `src/vmm/src/main.rs` (used by
`info` for cross-version compatibility) is the right place to
add a `measure-human` / `measure-json` profile if any
qemu-img version diverges in formatting — initial spot-checks
across recent qemu versions show stable output but the matrix
must be checked.

### Per-format math (target side)

Concrete formulas the measure crate implements. All of these
already appear implicitly in the convert writers; the goal is
to extract them into pure functions.

- **raw**: `required = fully_allocated = round_up(virtual_size, 512)`.
- **qcow2** (the non-trivial one):
  - header (1 cluster) +
  - L1 table size (`ceil(virtual_size / l2_coverage) * 8`,
    rounded up to one cluster) +
  - L2 tables for each populated L1 entry
    (one cluster per non-empty L2) +
  - data clusters: `required` counts only allocated source
    clusters, rounded up to the *output* cluster size and
    the L2-coverage boundary; `fully_allocated` counts every
    cluster in the virtual range.
  - refcount table + refcount blocks sized by iterating to a
    fixed point against the metadata + data cluster count
    (this is what the convert writer already does in
    `compute_refcount_table_size`; extract and share).
  - Compressed output: `required` shrinks by the per-cluster
    compression ratio bound (qemu-img assumes worst-case
    incompressible, so `required` does *not* shrink — match
    that). `fully_allocated` is unchanged.
  - Encrypted output (LUKS-in-qcow2): add the LUKS header
    overhead (16 MiB by default for v2; spec it carefully).
- **vmdk monolithicSparse**:
  header + descriptor (rounded to grain) + GD + GT (one
  GT per allocated GTE-coverage span) + grain data
  (allocated grains for `required`; all grains for
  `fully_allocated`).
- **vmdk streamOptimized**: same plus per-grain markers and
  end-of-stream footer + EOS marker.
- **vmdk monolithicFlat**: descriptor file + flat extent
  (`virtual_size`, fully allocated by definition).
- **vhd dynamic**: footer (×2, head and tail) +
  dynamic header + BAT (`ceil(virtual_size / block_size) * 4`,
  rounded to sector) + per-allocated-block
  (`block_size + sector_bitmap_size` rounded to sector).
- **vhd fixed**: `virtual_size + 512` (footer).
- **vhdx dynamic**: file ID (1 MiB region) + active and
  inactive headers (1 MiB each) + region table area + log
  region (1 MiB minimum) + metadata region (1 MiB) +
  BAT region (1 MiB-aligned, sized for the chosen
  block_size) + per-allocated-block payload.

Each formula gets a docstring with a short worked example,
both for the reader and so the unit tests can assert on
known sizes.

### Source-allocation scanning

For `--size`, no scan is needed. Otherwise:

- **raw input**: every byte is "allocated" for measurement
  purposes (qemu-img matches this); no scan needed.
- **qcow2 input**: walk L1 → L2; for every non-zero L2 entry,
  count one source cluster as allocated (with extended L2,
  count populated subclusters proportionally). Return the
  total *virtual* bytes that map to allocated source data.
- **vmdk input**: walk grain directory → grain table;
  count one source grain per non-zero GTE.
- **vhd input**: walk BAT; one block per non-`0xFFFFFFFF` BAT
  entry.
- **vhdx input**: walk BAT (skipping the interleaved sector
  bitmap entries); one block per `PAYLOAD_BLOCK_FULLY_PRESENT`
  state. Other states (`PAYLOAD_BLOCK_PARTIALLY_PRESENT`,
  `_UNDEFINED`, `_ZERO`, `_UNMAPPED`, `_NOT_PRESENT`) count
  as zero except `_PARTIALLY_PRESENT` which counts the
  per-sector bitmap.
- **luks input** (out of scope for v1): the inner image is
  what we'd measure; deferred to follow-up.

The return type from the scanner is a single
`AllocationSummary { virtual_size, allocated_bytes,
source_cluster_size }` — a full allocation map is not needed
because the per-target math only needs the total. (If a future
operation needs the full map, extend the iterator at that
point; YAGNI for measure.)

### qemu-img scope and divergence

`qemu-img measure` accepts source images in any format the
parsers handle but only emits sizes for raw and qcow2 outputs.
Verified empirically:

```
$ qemu-img measure --size 10M -O vmdk
qemu-img: Block driver 'vmdk' does not support size measurement
$ qemu-img measure --size 10M -O vpc
qemu-img: Block driver 'vpc' does not support size measurement
$ qemu-img measure --size 10M -O vhdx
qemu-img: Block driver 'vhdx' does not support size measurement
```

So the test matrix is:

| Source / Target | raw | qcow2 | vmdk | vhd | vhdx |
|------|-----|-------|------|-----|------|
| raw / qcow2 / vmdk / vhd / vhdx (input) | qemu-img cross-validation | qemu-img cross-validation | round-trip vs `convert` | round-trip vs `convert` | round-trip vs `convert` |

And `--size` mode is a column of its own (no source).

We document this in `docs/measure.md` and `docs/quirks.md`
under a new "Subcommands beyond qemu-img coverage" section.

### Versioning and baseline strategy

We extend `instar-testdata/scripts/generate-baselines.py` to
add a `measure` command entry alongside `info`, `check`, and
`compare`. For each `(qemu-img version, target format,
source image | --size value)` triple we capture stdout
(human and json), stderr, and exit code into
`expected-outputs/measure-{human,json}/<src_format>/<version>/<image-id>.{stdout,stderr,meta.json}`.

The number of baseline outputs is bounded:
- ~80 qemu-img versions
- 2 output types (human, json)
- 2 supported target formats (raw, qcow2)
- ~30-40 representative source images from the manifest
  (safe-tier only by default)
- A handful of `--size` values (1M, 1G, 1T) and qcow2
  option combinations (default cluster, 64KiB, 2MiB,
  refcount_bits=1/16, extended_l2)

That is ~40k files at <1 KiB each (~40 MiB of expected
outputs). Comparable in scale to existing
`expected-outputs/qemu-img-json/` and acceptable for
the testdata repo.

### Why a separate `crates/measure/` and not methods on each parser

The math is a function of the *output* format and is the same
regardless of where the allocation summary came from
(scan vs --size). Putting it on the input-side parsers
would force duplication or awkward cross-crate calls. The
output-side writers in `convert` *do* already contain this
math, but extracting it lets unit tests and fuzzers exercise
it without standing up the convert pipeline. Convert itself
should later be refactored to call into `crates/measure/`
where the logic overlaps; that is captured under Future work.

## Open questions

1. **Snapshot support (`-l SNAPSHOT`)**: `convert --snapshot`
   already exists. Should phase 1 ship `-l` parity, or defer
   it to a follow-up phase? Recommendation: defer. The
   measure result for a snapshot is meaningfully different
   from the active image, but the implementation is just
   "set `MeasureConfig.snapshot_id` and reuse the convert
   plumbing", which is small enough to be its own commit
   later. The qemu-img cross-validation matrix can ignore it
   in v1.

2. **`--image-opts` parsing**: `qemu-img measure --image-opts`
   accepts `driver=qcow2,file.filename=...` style descriptors.
   We do not support this anywhere else in instar. Recommend
   declining it explicitly with a clear error; document in
   `docs/quirks.md`.

3. **`-o preallocation=falloc|full|metadata`**: qemu-img
   adjusts `required` to match `fully_allocated` for
   `preallocation=falloc|full`, and to "metadata only" for
   `preallocation=metadata` (qcow2). instar does not currently
   pre-allocate output during `convert`. Recommendation:
   accept and honour the option for measure (math only) and
   note in docs that `convert` itself ignores it. The
   alternative (refusing the option) makes baseline diffing
   noisier without saving any code.

4. **Fully-allocated for qcow2 with extended L2**: the
   subcluster bitmap costs nothing extra for fully-allocated
   (every cluster is uniformly normal). But for `required`
   with sparse subcluster patterns the rounding rule needs
   to match qemu-img exactly — needs reading
   `block/qcow2-cluster.c` `qcow2_measure` carefully and
   capturing the version range it's stable across.

5. **Compressed output (`compress=on`)**: qemu-img reports
   `required` as if data were incompressible (matching the
   conservative behaviour). Confirm this is true on every
   qemu-img version in our matrix before relying on it.

6. **Should `measure` skip the guest entirely for `--size`?**
   It is technically safe — there is no untrusted input. But
   one code path is simpler to reason about and to test, and
   the guest startup cost (<200 ms) is acceptable for a
   non-interactive query. Recommendation: keep the single
   guest path. Reconsider only if measure becomes a hot path.

7. **MeasureResult struct vs protobuf-only**: every other
   operation has both a `*Result` struct in
   `src/shared/src/lib.rs` (written into
   `OPERATION_CONFIG_ADDR` after the config is consumed) and
   a `*ResultMessage` protobuf. The struct exists primarily
   for early/error states; for measure the result fits in
   one short protobuf message. Recommendation: protobuf
   only, mirroring the simplest operation
   (`compare`'s minimal result envelope). Confirm during
   phase 3.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Per-format size calculators (`crates/measure/`) | [PLAN-measure-phase-01-calculators.md](/components/instar/plans/PLAN-measure-phase-01-calculators/) | Complete |
| 2. Source-allocation scanners on parser crates | [PLAN-measure-phase-02-allocation-scanners.md](/components/instar/plans/PLAN-measure-phase-02-allocation-scanners/) | Complete |
| 3. Guest `measure` operation + protobuf | [PLAN-measure-phase-03-guest-op.md](/components/instar/plans/PLAN-measure-phase-03-guest-op/) | Complete |
| 4. Host VMM subcommand + clap surface | [PLAN-measure-phase-04-host-cli.md](/components/instar/plans/PLAN-measure-phase-04-host-cli/) | Complete |
| 5. `-o` option parsing and per-target options | [PLAN-measure-phase-05-target-options.md](/components/instar/plans/PLAN-measure-phase-05-target-options/) | Complete |
| 6. Cross-version baseline generation in `instar-testdata` | [PLAN-measure-phase-06-baselines.md](/components/instar/plans/PLAN-measure-phase-06-baselines/) | Complete |
| 7. Integration tests (`tests/test_measure.py`) | [PLAN-measure-phase-07-integration-tests.md](/components/instar/plans/PLAN-measure-phase-07-integration-tests/) | Complete |
| 8. Coverage-guided fuzz harnesses | [PLAN-measure-phase-08-fuzz-coverage.md](/components/instar/plans/PLAN-measure-phase-08-fuzz-coverage/) | Complete |
| 9. Differential fuzzing extension | [PLAN-measure-phase-09-fuzz-differential.md](/components/instar/plans/PLAN-measure-phase-09-fuzz-differential/) | Complete |
| 10. Documentation, CHANGELOG, follow-ups | [PLAN-measure-phase-10-docs.md](/components/instar/plans/PLAN-measure-phase-10-docs/) | Complete |

### Phase notes (not yet detailed plans)

These are intentionally short — each gets its own phase plan
once the previous phase has landed and the working code has
clarified the brief.

**Phase 1 — Per-format size calculators**. New crate
`src/crates/measure/` (no_std, no parser deps, depends only
on `shared` for byte-order helpers and the `ImageFormat`
enum). Public API:

```rust
pub struct AllocationSummary {
    pub virtual_size: u64,
    pub allocated_bytes: u64,
}
pub struct MeasureOutput {
    pub required: u64,
    pub fully_allocated: u64,
}
pub fn measure_raw(virtual_size: u64) -> MeasureOutput;
pub fn measure_qcow2(s: &AllocationSummary, opts: &Qcow2MeasureOpts) -> MeasureOutput;
pub fn measure_vmdk(s: &AllocationSummary, opts: &VmdkMeasureOpts) -> MeasureOutput;
pub fn measure_vhd(s: &AllocationSummary, opts: &VhdMeasureOpts) -> MeasureOutput;
pub fn measure_vhdx(s: &AllocationSummary, opts: &VhdxMeasureOpts) -> MeasureOutput;
```

Unit tests live next to the implementation; expected sizes
are pinned to qemu-img output for `--size 1M / 1G / 1T`
across every supported option combination, sourced from
spot-running qemu-img during plan authoring rather than from
the testdata baselines (those are added in phase 6).

Recommended effort: **high**. Recommended model: **opus**.
The qemu-img reference behaviour is in `block/qcow2.c`,
`block/vmdk.c`, etc. — getting the rounding and
fixed-point iteration on refcount metadata exactly right is
where bugs hide.

**Phase 2 — Source allocation scanners**. Add an
`AllocationSummary` producer to each parser crate:
`qcow2::scan_allocation()`, `vmdk::scan_allocation()`,
`vhd::scan_allocation()`, `vhdx::scan_allocation()`, plus
`raw::scan_allocation()` (returns `virtual_size,
allocated_bytes = virtual_size`). Each is a pure function
over the parser's existing readers — no new I/O primitives
in the call table. Unit tests: scan a small synthetic image
of each format with a known allocation pattern (5 clusters
allocated out of 100) and assert the count is exact.

Recommended effort: **medium**. Recommended model: **sonnet**
with a thorough brief that names the existing functions to
extend. Five small parallel changes; each parser already
walks its tables.

**Phase 3 — Guest measure operation**. New
`src/operations/measure/` binary built like `info` and
`check`. Linker script identical to other operations
(load 0x20000, 384 KiB cap). Reads `MeasureConfig` from
`OPERATION_CONFIG_ADDR`, opens device 0 unless
`virtual_size_override != 0`, calls
`<src>::scan_allocation()` for the detected source format,
calls `crates/measure/` for the target format, sends a
`MeasureResultMessage` over the command channel. Add the
`MeasureConfig` struct and `MeasureResultMessage` proto
field. Add `measure` to the workspace `members` list and to
the build scripts that copy guest binaries into the VMM.

Recommended effort: **high** (touches call-table boundary,
new proto field, guest binary scaffolding). Recommended
model: **opus**.

**Phase 4 — Host VMM subcommand**. Add `MeasureArgs` and
`Commands::Measure(MeasureArgs)`, `run_measure()`. clap
surface mirrors qemu-img: `[--size SIZE | FILENAME]`,
`-O TARGET`, `-f FMT`, `-o OPTIONS`, `--output {human,json}`.
Output formatting matches qemu-img exactly (JSON key
`fully-allocated`, human `required size: N` /
`fully allocated size: N`). For `--size` mode, build a
`MeasureConfig` with `virtual_size_override` set and skip
device setup for the source.

Recommended effort: **medium**. Recommended model: **sonnet**
with a brief that points at `run_convert` for the launching
pattern and `print_info_result` for the JSON formatting
pattern.

**Phase 5 — `-o` parsing and per-target options**. Reuse
`convert`'s existing `cluster_size`, `extended_l2`,
`block_size`, `subformat`, `grain_size`, `compress` flags —
but exposed via the qemu-img-style `-o key=value,...`
parser, not as individual long flags. Implement a small
`parse_o_options()` helper in the VMM that takes the
target format and returns a populated `MeasureConfig`,
rejecting unknown keys with a clear error. Coverage:
qcow2 (`cluster_size`, `compat=0.10|1.1`, `refcount_bits`,
`extended_l2=on|off`, `lazy_refcounts=on|off`,
`compression_type=zlib|zstd`, `preallocation=...`),
vmdk (`subformat=...`), vhd (`subformat=fixed|dynamic`),
vhdx (`block_size=...`).

Recommended effort: **medium**. Recommended model: **sonnet**.

**Phase 6 — Cross-version baselines**. In
`instar-testdata/scripts/generate-baselines.py`, add a
`measure` command entry with `output_types =
{measure-human: None, measure-json: 'json'}` and a
`build_cmd` that emits both `--size` queries and
source-image queries. `supported_formats` for measure:
`['raw', 'qcow2']` (target side), but every source format
is fed to a fixed `target=qcow2` pass and a `target=raw`
pass. Run the generator under every binary in
`qemu-img-binaries/x86_64/` to produce
`expected-outputs/measure-{human,json}/`. Capture
deduplicated profile metadata via the existing
`detect-profiles.py` flow.

Recommended effort: **medium** for the script change,
**low** for the (long-running but mechanical) baseline
generation pass. Run on a beefy host — current matrix
takes ~30 minutes for `info` baselines, expect similar.
Recommended model: **sonnet**.

**Phase 7 — Integration tests**. New
`tests/test_measure.py` covering:
- For each safe-tier image in `manifest.json` and each
  installed qemu-img version: run `instar measure
  -O qcow2` and `-O raw` (json + human), compare to the
  matching baseline. Skip baselines that don't exist for
  the installed version.
- For each `--size` value in a fixed list (1 M, 16 M,
  1 G, 1 T) crossed with a fixed list of qcow2 option
  combinations: same comparison.
- Round-trip tests for vmdk/vhd/vhdx: run
  `instar measure -O <fmt>`, then
  `instar convert -O <fmt>`, then
  `os.path.getsize()` — assert
  `actual <= measured.required` and
  `actual <= measured.fully_allocated`. (`required` is
  a lower bound on what convert produces; this catches
  underestimates.)
- Error paths: missing source, conflicting `--size` and
  `FILENAME`, unsupported `-o` key, oversized virtual
  size.

Tests use the existing `InstarTestBase` helpers and the
manifest filtering used by `test_oslo_crossval.py` for
version-keyed expected outputs.

Recommended effort: **medium**. Recommended model: **sonnet**.

**Phase 8 — Coverage-guided fuzz harnesses**. Two new
fuzz targets in `src/fuzz/fuzz_targets/`:
- `fuzz_measure_calc.rs`: takes a fuzzer-supplied
  `(target_format_id, virtual_size, allocated_bytes,
  options)` tuple and calls every public function in
  `crates/measure/`. Asserts no panics, no integer
  overflows, `required <= fully_allocated <=
  saturating_round_up_to_some_huge_bound`, and that
  `required >= virtual_size` for raw output.
- `fuzz_measure_scan.rs`: format-prefixed input feeds
  the existing fuzz mock CallTable; calls
  `<format>::scan_allocation()` for each parser.
  Coverage of allocation-walking code, cheap to run.
  May overlap with `fuzz_qcow2_l1l2` etc.; if the
  overlap is full, fold into the existing harnesses
  instead of creating new targets.

Recommended effort: **medium**. Recommended model: **opus**
for harness design (it has to define the right
invariants), **sonnet** for the routine
boilerplate-following second target.

**Phase 9 — Differential fuzzing extension**. In
`scripts/differential-fuzz.py`, add `measure` to the
random operation chain. For each generated image:
- For each target in `(raw, qcow2)`: run
  `instar measure -O <target>` and `qemu-img measure
  -O <target>`, compare numeric `required` and
  `fully-allocated`. Match exactly or document the
  divergence.
- For each target in `(vmdk, vhd, vhdx)`: run
  `instar measure -O <target>` and
  `instar convert -O <target>`, assert convert output
  size lies in `[required, fully_allocated]`.
The CI workflow `.github/workflows/differential-fuzz.yml`
needs no change beyond the script update.

Recommended effort: **medium**. Recommended model: **sonnet**.

**Phase 10 — Documentation and CHANGELOG**. New
`docs/measure.md` covering CLI surface, per-target
formula summaries, qemu-img divergences (vmdk/vhd/vhdx
not supported by qemu-img, `--image-opts` rejected,
preallocation handling). Update `docs/usage.md`,
`docs/quirks.md`, `docs/index.md`, `README.md`,
`AGENTS.md` (add the new operation to the operations
list), `ARCHITECTURE.md` (Format Support section gets a
"Measurable target formats" line), `CHANGELOG.md`
(under Unreleased / next version), and
`PLAN-convert-followups.md` (mark measure as done,
removing it from the deferred list).

Recommended effort: **low**. Recommended model: **sonnet**
or **haiku**.

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
only touch one new file in `crates/measure/` or one new
test file can run in the main tree.

### Planning effort

This master plan is high-effort. Phases 1, 3, and 8 are
high effort. Phases 2, 4, 5, 6, 7, 9 are medium. Phase 10 is
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

* All 10 phases complete and committed on the `measure`
  branch.
* `make instar` builds with `measure.bin` at ~25 KiB
  (6% of the 384 KiB operation-binary cap).
* `make lint` clean across the workspace.
* `make test-rust` passes; new tests in measure / shared /
  parser crates raise totals as documented in each phase
  plan.
* `make test-integration` includes `tests/test_measure.py`:
  345 tests, 209 pass, 136 skip with documented reasons.
* `make check-binary-sizes` includes `measure.bin`.
* `pre-commit run --all-files` clean throughout.
* For raw and qcow2 targets: `instar measure` matches
  `qemu-img measure` byte-for-byte (both `--output=human`
  and `--output=json`) across every qemu-img version in
  `instar-testdata/qemu-img-binaries/x86_64/` (6.0.0-10.2.0)
  per the baseline matrix.
* For vmdk / vpc / vhdx targets: `instar convert` output
  file size lies in `[?, fully_allocated + max(1 MiB,
  fully_allocated / 16)]` per the differential fuzzer's
  bound.
* 15 coverage-guided fuzz targets registered in nightly CI;
  differential fuzzer's random operation chain includes
  `measure`.
* `docs/measure.md`, `docs/quirks.md`, `docs/usage.md`,
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `CHANGELOG.md` all updated.
* `PLAN-convert-followups.md` strikes `measure` from the
  deferred-subcommand list.

### Future work

* **Extract a shared sector-walking helper for the parser
  `scan_allocation` methods.** The pre-push wave-2a code-quality
  review flagged near-verbatim duplication of the buf_start /
  buf_end / meaningful_len / per-sector-read loop between
  `vhd::VhdState::scan_allocation` and
  `vhdx::VhdxState::scan_allocation`. Both walk a contiguous BAT
  table; only the entry decoder and one cache-invalidation line
  differ. A shared `walk_table_sectors(call_table, byte_offset,
  byte_len, ..., FnMut(&[u8]))` helper in `shared` would
  eliminate ~80 LoC. Deferred because the `FnMut` closure + `&mut
  self` borrow interaction on `bat_cached_sector` adds
  non-trivial complexity for the marginal line-count win, and
  the existing direct loops are already test-covered and
  fuzz-exercised. The vmdk and qcow2 scanners have a two-level
  walk (GD→GT, L1→L2) that doesn't fit the same shape and would
  remain unrefactored. NOTE comments inline at both
  `scan_allocation` sites cross-reference each other.

* **Raw-source `SEEK_HOLE`/`SEEK_DATA` detection.** instar's
  no_std raw scanner returns `allocated_bytes = virtual_size`
  unconditionally. qemu-img scans the file's on-disk extents
  and reports a smaller `required` for sparse raw inputs.
  Right fix: VMM does the `lseek` scan before launching the
  guest and passes `allocated_bytes` via MeasureConfig; the
  guest skips the trivial raw scan.

* **VHDX scanner partial-block-state walk.** Phase 2's vhdx
  scanner treats every BAT block as fully allocated.
  qemu-img returns the actual block-state distribution
  (FULLY_PRESENT / PARTIALLY_PRESENT / ZERO / etc).

* **VMDK multi-extent sparse propagation.** Phase 2's vmdk
  scanner doesn't propagate the extent map fully for
  multi-extent layouts.

* **QCOW2 scanner backing-chain composition.** Phase 2's
  scanner reports the top layer only. The existing chain
  machinery (`info --chain`, `check --chain`) could feed
  multiple AllocationSummaries that the host or guest
  combines with shadowing.

* **QCOW2 compressed-cluster / extended-L2 subcluster
  overcount investigation.** Phase 7c found small numeric
  divergences for a handful of real-world qcow2 sources
  (`debian-12-sfagent`, `sf-vda`). Root cause unknown;
  needs deep inspection.

* **`encrypt.format=luks` aware sizing.** Phase 5 rejects
  `encrypt.*` keys with a "future work" message. A proper
  fix models the LUKS header overhead based on
  `encrypt.iter-time`, cipher choice, and slot count.

* **`-l SNAPSHOT` snapshot-targeted measurement.** Reuses
  convert's snapshot machinery (`--snapshot ID`).

* **`-o help` listing.** qemu-img prints the per-target
  option reference; instar errors out.

* **`--image-opts` parsing.** qemu-img accepts a descriptor-
  based source specification (`driver=qcow2,file.filename=`).
  Defer until a real user requests it.

* **`subformat=fixed` for vhdx target.** Phase 5 rejects it;
  phase 1's measure_vhdx supports only Dynamic.

* **VHD legacy CHS-only virtual_size.** Phase 7c found
  `virtualpc-vhd` reports a ~2 MiB different virtual_size
  than qemu-img. Likely a CHS-vs-current_size precedence
  mismatch.

### Bugs fixed during this work

* **`parse_memory_size` missing T suffix** (phase 7b).
  `instar measure --size 1T ...` failed with
  `invalid memory size: '1T'` because the helper handled
  K/M/G only. One-line fix; 4 previously-skipped
  `TestMeasureBaselineSize` cases now pass.

* **Missing `bitmaps` field emission for qcow2 v3 sources**
  (phase 7c). instar's measure JSON output omitted the
  `"bitmaps": 0` field that qemu-img emits whenever the
  target is qcow2 and the source is qcow2 v3. The gate
  was added as `peek_is_qcow2_v3()` in
  `src/vmm/src/chain.rs`. Without this fix, 46 source-image
  baseline comparisons failed.

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
