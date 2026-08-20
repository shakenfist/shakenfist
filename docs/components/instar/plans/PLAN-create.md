# `instar create` subcommand

## Status: Complete

Phases 1-11.

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
`PLAN-create-phase-NN-<descriptive>.md` alongside this file and
linked from the Execution table below. They are *not* added to
`docs/plans/order.yml` — only the master plan is.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img`
subcommands deferred from the convert effort. `measure` was the
first and shipped end-to-end through ten phases
(`PLAN-measure.md`). `create` is scheduled next ahead of
`resize`, `map`, `snapshot`, `rebase`, and `commit` because:

- It is the first *write-path* operation that produces a new
  image from nothing — every other write path (`convert`) starts
  from a parsed source. This forces the per-format metadata
  emitters out of convert's monolithic source file and into a
  shared, independently testable shape, which `resize` and
  `commit` will both reuse.
- It is lower-blast-radius than `resize`. `create` writes an
  entirely new file; an incorrect emitter is caught by
  round-tripping through `instar info` / `instar check` and by
  comparing to `qemu-img info` on the same `qemu-img create`
  invocation. `resize` mutates an existing image's L1/BAT in
  place — a bug there can corrupt user data.
- It exercises the same call-table boundary, guest-binary
  scaffolding, and clap-subcommand pattern that `measure`
  just established, so the scaffolding cost is small.
- `qemu-img create` accepts a backing-file reference
  (`-b BACKING [-F BACKING_FMT]`). Supporting this introduces
  the pattern of "the guest reads metadata from an input device
  but writes a brand-new image to the output device" — which is
  also what `rebase` and `commit` need.

The relevant existing infrastructure this plan builds on:

- VMM subcommand scaffolding in `src/vmm/src/main.rs`
  (clap `Commands` enum, per-op `*Args` struct, `run_*`
  function), call-table boundary in `src/shared/src/lib.rs`
  (`OPERATION_CONFIG_ADDR`, per-op `*Config` and `*Result`
  structs), and the protobuf wrapper in
  `crates/guest-protocol/proto/guest.proto`
  (`GuestMessage` oneof payload).
- The convert operation
  (`src/operations/convert/src/main.rs`, ~4640 lines)
  already contains per-format metadata writers:
  `write_qcow2_header`, `write_qcow2_metadata`,
  `write_refcount_table`, the VMDK descriptor / GD / GT
  writers, the VHD footer / dynamic-header / BAT writer,
  and the VHDX file-ID / region-table / metadata-region /
  BAT writer. They are currently *coupled to convert's
  data-copy loop* — extracting them is the bulk of phase 1.
- The `crates/measure/` size calculators
  (`src/crates/measure/src/lib.rs`) — `create` can pre-call
  these to size its allocations before emission, which keeps
  the size math in one place.
- The output-device wiring in `run_convert` and `run_copy`
  (`VirtioBlockDevice::new(..., is_input=false)`,
  `device_set.add_device(..., false)`) for the host side.
- The raw host-truncate path at the end of `run_convert`
  (around `src/vmm/src/main.rs:5380`).
- The cross-version baseline generator
  (`instar-testdata/scripts/generate-baselines.py`) and its
  `expected-outputs/{info,check,compare,measure-*}/` layout.
- The coverage-guided fuzz harnesses in `src/fuzz/` and the
  differential fuzzer (`scripts/differential-fuzz.py`).

## Mission and problem statement

Implement `instar create` such that:

1. It accepts the same surface area as `qemu-img create`:
   - `-f FMT` (default `raw`) for the target format.
   - `-o key=value,...` for per-format options
     (cluster_size / refcount_bits / extended_l2 /
     lazy_refcounts / compat / compression_type /
     preallocation for qcow2; subformat / adapter_type /
     hwversion / grain_size for vmdk; subformat / block_size
     for vhd / vhdx).
   - `-b BACKING_FILENAME [-F BACKING_FMT] [-u]` for backing
     file references on formats that support them
     (qcow2, vmdk, vhd, vhdx — qemu-img supports them on
     qcow2 only for the common case; instar should match
     qemu-img's permission set exactly).
   - `FILENAME` and `SIZE[bkKMGTPE]` positional arguments
     (`SIZE` is required unless `-b` is provided, in which
     case it defaults to the backing file's virtual size).
   - `-q` quiet mode (matches qemu-img output suppression).
   - `--object OBJDEF` is out of scope for v1 (defer with a
     clear error; see open questions).

2. All format-emission work runs entirely inside the KVM guest,
   exactly like every other instar operation. The host opens
   the output file (with `O_CREAT|O_TRUNC|O_RDWR`), attaches it
   to the output virtio-block device, and (for the backing
   case) also opens the backing file read-only and attaches it
   as input device 0. The guest then walks the backing file's
   header to extract `virtual_size`/`format` if needed, and
   writes the new image's metadata via `write_output_sector`.

3. For raw output, the host bypasses the guest entirely:
   `open` + `ftruncate(virtual_size)`. No guest launch is
   needed because there is no metadata to emit. (See open
   question 6 for why this is the only exception to the
   single-code-path rule.)

4. For qcow2, vmdk, vhd, vhdx outputs, the bytes written are
   *equivalent* to what `qemu-img create` writes — not byte-
   identical (qemu-img embeds random UUIDs, mtimes, and
   tool-version strings that we deliberately diverge on for
   reproducibility), but `instar info`, `qemu-img info`, and
   `instar check` all report identical metadata for the two
   files. This is the validation contract.

5. Round-trip parity holds: for every `(format, options)`
   combination we support, `instar create | instar info` and
   `qemu-img create | instar info` produce identical info
   output (ignoring fields whitelisted as legitimately
   non-deterministic — uuid, mtime, tool version). The
   integration tests assert this across the full qemu-img
   version matrix.

6. Coverage-guided fuzzing exercises each metadata writer
   directly (no guest, no virtio — just feed the writer a
   fuzzer-supplied `(virtual_size, options)` and assert no
   panics, no integer overflow, and that the emitted bytes
   round-trip through the matching parser).

7. The existing differential fuzzer is extended so that for
   each randomly generated `(format, options, size)` it runs
   both `instar create` and `qemu-img create`, then `instar
   info` on both outputs, and asserts info-equivalence.

## Design overview

### Architectural shape

The work decomposes into four layers:

1. **Pure metadata emitters** (per output format). Given
   `(virtual_size, options, optional backing reference)`
   produce a sequence of `(byte_offset, bytes)` writes that
   constitute a valid empty image. No I/O. These are pure
   functions on the output side. New crate
   `src/crates/create/` (no_std, no parser dependencies,
   depends on `shared` and on `crates/measure/` for the
   per-format size math). Mirrors the shape of
   `crates/measure/`.

   The emitters return a `MetadataPlan` value (a small
   bounded list of `(offset, &[u8])` slices backed by the
   emitter's internal staging buffer) rather than performing
   the writes themselves — this keeps the emitter testable
   without standing up a virtio device, and lets the guest
   wrapper handle sector alignment, the optional
   preallocation pass, and progress reporting in one place.

2. **Guest `create` operation binary**
   (`src/operations/create/`). Reads `CreateConfig` from
   `OPERATION_CONFIG_ADDR`. For backing-file-defaulted size
   (`SIZE` was not given), reads the backing image's header
   from input device 0 and extracts its `virtual_size` via
   the existing parser crate's header decoder. Calls
   `crates/create/` to build a `MetadataPlan`, then writes
   it sector-by-sector via `write_output_sector`. Sends a
   `CreateResultMessage` over the command channel with the
   resolved virtual size, the on-disk size of the metadata
   region, and (when preallocation is requested) the total
   allocated size.

3. **Host VMM subcommand**. `run_create()` in
   `src/vmm/src/main.rs`. Parses the clap surface, parses
   `-o` options via a shared `parse_o_options()` helper
   (introduced in measure phase 5), opens the output file
   with `O_CREAT|O_TRUNC|O_RDWR`, attaches it as the output
   virtio-block device, optionally opens the backing file
   read-only and attaches it as input device 0, builds
   `CreateConfig`, launches the guest, prints the result in
   qemu-img-compatible format. For raw output: short-circuit
   to `open` + `ftruncate` with no guest launch.

4. **Tests and fuzzers**. Integration tests covering the
   `(format × options × size × qemu-img version)` matrix;
   round-trip tests via `instar info`; coverage-guided
   fuzzers per emitter; differential fuzzer comparing
   `instar create | instar info` to
   `qemu-img create | instar info`.

Splitting layer 1 from layer 2 keeps the emitters
unit-testable in plain `cargo test` (no KVM, no fuzzer
needed) and keeps the fuzz harness for layer 1 trivial. It
also leaves a clean reuse point for `resize`, which will
need to *rewrite* L1 / BAT entries — those rewrites can
share the layout helpers from `crates/create/` even though
the orchestration is different.

### Convert refactor: opportunistic, not required

Convert currently embeds the qcow2 / vmdk / vhd / vhdx
metadata writers in its own source file. The clean
end-state is for convert to call into `crates/create/` for
the metadata emission phase and only retain the data-copy
loop. This is *not* a prerequisite for shipping `create` —
we can extract by copy first (phase 1), then refactor convert
to call the extracted code in a later phase
(see Future work). Doing the extraction in-place would
balloon this plan and conflate two separate risk profiles
(adding a feature vs. rewriting an existing one).

### Call-table and protobuf changes

- New `CreateConfig` in `src/shared/src/lib.rs` next to
  `MeasureConfig`, with magic `CREA`, fields:
  - `target_format: u32` (reuses `ImageFormat`)
  - `virtual_size: u64` (0 ⇒ default from backing file)
  - `cluster_size: u32`, `refcount_bits: u8`
  - `flags: u32` (extended_l2, lazy_refcounts, compat_v3,
    encrypt, preallocation bits, backing_unsafe)
  - `subformat: [u8; 32]` (vmdk / vhd)
  - `grain_size: u32`, `block_size: u32`
  - `backing_file_len: u32`, `backing_file: [u8; 1024]`
    (the path string written into the metadata — *not*
    the path the host opens; the host translates if needed)
  - `backing_format: u32` (ImageFormat, 0 ⇒ unset)
  - `input_device_count: u32` (0 or 1 — 1 iff backing file
    was supplied)
  - reserved padding for forward compat.

- New `CreateResultMessage` in `guest.proto`, added to
  `GuestMessage` oneof:
  ```
  message CreateResultMessage {
    uint64 resolved_virtual_size = 1;
    uint64 metadata_bytes_written = 2;
    uint64 file_size_after = 3;
    string target_format = 4;
    uint32 resolved_cluster_size = 5;
    uint32 resolved_block_size = 6;
  }
  ```

- The call table needs no new function pointers.
  `write_output_sector` already exists; `read_input_sector`
  already exists (used for the optional backing-header read).

### qemu-img output format

`qemu-img create` prints (to stderr by default, stdout in
some versions):

```
Formatting 'foo.qcow2', fmt=qcow2 cluster_size=65536 \
  extended_l2=off compression_type=zlib size=1073741824 \
  lazy_refcounts=off refcount_bits=16
```

(line continuations added for readability — qemu-img emits
one line). The exact key list depends on format and on
qemu-img version. Matching this output verbatim is *not* a
goal — there is no machine-readable consumer for it and the
key set has churned across versions. Instar will emit a
short, stable, machine-friendlier line:

```
Created: foo.qcow2 (format=qcow2, virtual_size=1073741824, \
  cluster_size=65536, ...)
```

with `--output=json` producing the canonical structured
form. The qemu-img-style line is suppressed under `-q`. Any
script that scrapes qemu-img's stderr is already brittle
across qemu-img versions; we document the divergence in
`docs/quirks.md` and provide `--output=json` as the
stable interface.

### Per-format metadata to emit

The emitter for each format produces *only* the empty-image
metadata; no data clusters / grains / blocks are written.
Concrete contents:

- **raw**: handled host-side. `open(O_CREAT|O_TRUNC|O_RDWR)`,
  `ftruncate(virtual_size)`. With `preallocation=full`,
  follow with `fallocate(FALLOC_FL_ZERO_RANGE, 0,
  virtual_size)` (or fall back to writing zero blocks if
  unsupported). With `preallocation=falloc`, use
  `posix_fallocate`. No guest involvement.
- **qcow2**:
  - cluster 0: header (with backing file string in the
    backing_file region if present)
  - cluster 1+: L1 table (sized to cover virtual_size)
  - cluster N+: refcount table + refcount blocks (sized
    to a fixed point via the existing
    `compute_refcount_table_size` logic, extracted from
    convert).
  - No L2 tables are written for an empty image
    (every L1 entry is zero).
  - With `preallocation=metadata`: also pre-allocate L2
    tables and the data clusters, and populate L1/L2
    entries with their cluster addresses.
  - With `preallocation=falloc`/`full`:
    metadata-only emission as above, then host follows
    up with `posix_fallocate` / zero-fill on the data
    region.
- **vmdk monolithicSparse**:
  - header (sector 0, 512 bytes)
  - GD (sector after header)
  - GT region reserved per the descriptor (left zero for
    empty image)
  - embedded descriptor (text, at the position the header
    points to)
- **vmdk monolithicFlat**: descriptor file (the named file),
  flat extent file (companion file), `ftruncate` on the
  flat extent. Two files written — the host side
  orchestrates this (see open question on multi-file layout).
- **vmdk twoGbMaxExtentSparse / twoGbMaxExtentFlat**:
  descriptor file + N extent files, each capped at 2 GiB.
- **vmdk streamOptimized**: header + descriptor + empty
  stream (zero grains, end-of-stream marker).
- **vhd dynamic**: footer (×2), dynamic header, BAT
  (`ceil(virtual_size / block_size) * 4` bytes, all entries
  = 0xFFFFFFFF), no data blocks.
- **vhd fixed**: handled like raw (truncate to
  `virtual_size + 512`, write footer at the end). Host-side
  for the truncate; guest emits the 512-byte footer.
- **vhdx dynamic**: file identifier (sector 0), two
  headers (sectors 1 and 2), region table (sectors 192/256
  per spec), log region (1 MiB), metadata region (1 MiB),
  BAT region (1 MiB-aligned, sized for block_size; all
  entries in `PAYLOAD_BLOCK_NOT_PRESENT` state).
- **vhdx fixed**: deferred (qemu-img does not produce
  fixed vhdx; instar's vhdx writer in convert produces
  dynamic only).

Each emitter ships with a docstring with a worked example
and tests asserting that re-parsing the emitted bytes
yields the expected `virtual_size`, `format`, and
`cluster_size`/`block_size`/`grain_size`.

### Backing-file handling

qemu-img defaults the new image's `virtual_size` to the
backing file's `virtual_size` when `-b` is given without a
`SIZE`. To do this without parsing untrusted input on the
host, the VMM:

1. Opens the backing file read-only.
2. Attaches it as input device 0 of the guest.
3. Sets `backing_format` in `CreateConfig` to the
   backing format (either user-specified via `-F` or
   inferred — see below).
4. The guest reads the first ~4 KiB of input device 0,
   probes the format (or uses `backing_format` directly),
   parses the header to extract `virtual_size`, and proceeds.

Format inference for the backing file: if `-F` is given,
trust it. If not given *and* `-u` is given, default to
`raw` and pass `backing_unsafe`. If neither is given, qemu-
img refuses the operation in newer versions ("Backing file
specified without backing format"). Match qemu-img's
current behavior: require either `-F` or `-u`.

Pitfall: writing the backing-file *path* into qcow2/vhd/vhdx
metadata. qemu-img writes the path the user typed, not the
resolved absolute path. Match this — the path string in
`CreateConfig.backing_file` is the user-typed path bytes,
verbatim, with a length limit checked on the host before
launch.

### Preallocation handling

qemu-img's `preallocation=` options:

- `off` (default): metadata only. Trivial.
- `metadata`: metadata + L2 / BAT entries populated, but
  data regions left as holes. qcow2 / vhdx only.
- `falloc`: metadata + `posix_fallocate` on the data region
  (reserves blocks but doesn't write).
- `full`: metadata + zeroed data region (
  `fallocate(FALLOC_FL_ZERO_RANGE)` then fall back to
  writing zeros).

For the v1 cut, instar handles `off`, `falloc`, `full`
host-side after the guest emits metadata (the host knows
the metadata footprint from `CreateResultMessage` and can
`fallocate` the rest). `metadata` is qcow2/vhdx-specific
and needs guest-side support to populate L2/BAT entries —
ship it for qcow2 in v1, defer vhdx to a follow-up phase.

### Test matrix

| Source | Target | Validation |
|--------|--------|------------|
| (no source) | raw | host truncate; `os.path.getsize == virtual_size`; `instar info` matches `qemu-img info` |
| (no source) | qcow2 | guest emit; `instar info` ≡ `qemu-img info` for the matching `qemu-img create -f qcow2 ...` |
| (no source) | vmdk | guest emit; `instar info` ≡ `qemu-img info` |
| (no source) | vhd | guest emit; `instar info` ≡ `qemu-img info` |
| (no source) | vhdx | guest emit; `instar info` ≡ `qemu-img info` |
| backing qcow2 | qcow2 with -b | virtual_size defaults from backing; backing_file path in header matches user input |
| backing raw | qcow2 with -b -F raw | same, with raw backing |
| backing raw, no -F, no -u | qcow2 | error: backing format required |

`(qcow2 options) ∈ {default, cluster_size=512..2M,
extended_l2=on, lazy_refcounts=on, refcount_bits∈{1,16},
compat∈{0.10,1.1}, preallocation∈{off,metadata,falloc,full}}`
— each combined with three representative sizes (1 MiB,
1 GiB, 1 TiB).

### Versioning and baseline strategy

We extend `instar-testdata/scripts/generate-baselines.py`
to add a `create` command entry. For each `(qemu-img
version, format, options, size)` triple we:

1. Run `qemu-img create` to produce a fixture image.
2. Run `qemu-img info --output=json` on the fixture and
   capture the JSON output (this is the comparable
   artifact, not the raw bytes of the image).
3. Compare to the JSON output of `qemu-img info` on the
   instar-produced image *at integration test time*
   (the baseline captures qemu-img's behaviour; the
   comparison happens against instar at runtime).

Capturing the qemu-img-produced image bytes wholesale
would balloon the testdata repo (every fixture is at
least one cluster). We only need the *info* output as
the baseline. The size matrix is:

- ~80 qemu-img versions
- 4 target formats (qcow2, vmdk, vhd, vhdx)
- ~10-15 option combinations per format
- 3 sizes
- = ~12k JSON baselines at <2 KiB each (~20 MiB)

Plus a small fixed number of bit-comparison baselines for
the cases where bit-equivalence *is* a goal — we don't
currently have any, so this set may stay empty.

## Open questions

1. **Multi-file VMDK layout**. `monolithicFlat`,
   `twoGbMaxExtentSparse`, and `twoGbMaxExtentFlat` produce
   multiple files (descriptor + N extents). The VMM
   currently opens *one* output file. For v1, recommendation:
   support only `monolithicSparse` and `streamOptimized`
   (single-file subformats) and reject the multi-file
   subformats with a clear error pointing at the existing
   `instar convert -O vmdk -o subformat=...` path
   (which handles them). Defer multi-file emission to a
   follow-up phase. Alternative: have the host open all
   the output files up-front and pass them as multiple
   output devices — but the call-table currently only
   supports one output device, so this is a bigger ABI
   change.

2. **`--object OBJDEF` for encryption keys**. qemu-img
   uses this for LUKS encryption keys via `secret`
   objects. instar's `convert` accepts a passphrase via
   `CONVERT_CONFIG_MAX_PASSPHRASE` and could do the same.
   Recommendation: defer encrypted-create to a follow-up
   phase. Reject `--object` and `encrypt.*` keys with a
   clear "not yet supported" error in v1.

3. **`-q` quiet mode scope**. qemu-img's `-q` suppresses
   the "Formatting ..." line on stderr but still prints
   errors. Match exactly: under `-q`, suppress the
   "Created: ..." line; print errors normally.

4. **Backing-file resolution**. When the user types
   `instar create -f qcow2 -b ../backing.qcow2 new.qcow2`,
   does the host *open* `../backing.qcow2` relative to its
   own cwd, or relative to the directory containing
   `new.qcow2`? qemu-img: relative to the new image's
   directory (so the resulting backing reference is
   portable). Match qemu-img. Document this in
   `docs/quirks.md` because it's a common gotcha.

5. **Raw + preallocation=full as host-only path**. If
   `-f raw -o preallocation=full SIZE` is large
   (terabytes), the host write loop blocks for a long
   time without instar's progress channel. Recommendation:
   for raw + `preallocation=full`, route through the guest
   so the existing progress reporting works. (raw + `off`
   and raw + `falloc` are quick enough to stay host-only.)
   Reconsider after benchmarking the host loop.

6. **Single-code-path principle vs. raw shortcut**.
   `measure` keeps one guest code path even for `--size`
   mode for code-path uniformity. For `create -f raw`,
   the metadata is zero bytes, so launching a guest to
   emit nothing is pure overhead. Recommendation:
   short-circuit raw (and vhd-fixed, see below) on the
   host. Document the asymmetry as deliberate in
   `docs/create.md`. The integration tests should
   still cover both host and guest paths.

7. **vhd fixed subformat**. `qemu-img create -f vpc
   -o subformat=fixed SIZE FILENAME` produces
   `virtual_size + 512` bytes — zeros plus a 512-byte
   footer at the end. The footer is the only metadata.
   Recommendation: host-side truncate + guest emits the
   footer alone, or host-side truncate + host writes the
   footer (the footer format is well-specified and
   doesn't require any parsing). Decide during phase 1
   when the writers are extracted; if the footer-emit
   function in `convert` is small and pure, lift it to
   `crates/create/` and call it from the host directly.

8. **CreateResult struct vs. protobuf-only**. measure
   went protobuf-only (open question 7 in PLAN-measure).
   create has similar shape — a small fixed-size summary
   result. Recommendation: protobuf-only; mirror measure.

9. **Existing-file handling**. qemu-img `create`
   overwrites without prompting. Match qemu-img. The
   host `open` uses `O_CREAT|O_TRUNC|O_RDWR`. No
   confirmation prompt.

10. **Concurrent-safety of the backing file open**.
    The host opens the backing file read-only and attaches
    it as a virtio device while a separate hand also
    has `new.qcow2` open for writing. There is no race
    because instar's VMM holds both file descriptors for
    the duration of the guest run. Document this
    assumption in `docs/security.md` for the threat-model
    section that already lists "backing files are trusted
    to be parseable but not trusted to be safe to read
    in-process" — same model as `convert`.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Per-format metadata emitters (`crates/create/`) | [PLAN-create-phase-01-emitters.md](/components/instar/plans/PLAN-create-phase-01-emitters/) | Complete |
| 2. Guest `create` operation + protobuf | [PLAN-create-phase-02-guest-op.md](/components/instar/plans/PLAN-create-phase-02-guest-op/) | Complete |
| 3. Host VMM subcommand + clap surface | [PLAN-create-phase-03-host-cli.md](/components/instar/plans/PLAN-create-phase-03-host-cli/) | Complete |
| 4. `-o` option parsing for create | [PLAN-create-phase-04-target-options.md](/components/instar/plans/PLAN-create-phase-04-target-options/) | Complete |
| 5. Backing-file support (`-b` / `-F` / `-u`) | [PLAN-create-phase-05-backing-file.md](/components/instar/plans/PLAN-create-phase-05-backing-file/) | Complete |
| 6. Preallocation modes (`off`/`metadata`/`falloc`/`full`) | [PLAN-create-phase-06-preallocation.md](/components/instar/plans/PLAN-create-phase-06-preallocation/) | Complete |
| 7. Cross-version baseline generation in `instar-testdata` | [PLAN-create-phase-07-baselines.md](/components/instar/plans/PLAN-create-phase-07-baselines/) | Complete |
| 8. Integration tests (`tests/test_create.py`) | [PLAN-create-phase-08-integration-tests.md](/components/instar/plans/PLAN-create-phase-08-integration-tests/) | Complete |
| 9. Coverage-guided fuzz harnesses for the emitters | [PLAN-create-phase-09-fuzz-coverage.md](/components/instar/plans/PLAN-create-phase-09-fuzz-coverage/) | Complete |
| 10. Differential fuzzing extension | [PLAN-create-phase-10-fuzz-differential.md](/components/instar/plans/PLAN-create-phase-10-fuzz-differential/) | Complete |
| 11. Documentation, CHANGELOG, follow-ups | [PLAN-create-phase-11-docs.md](/components/instar/plans/PLAN-create-phase-11-docs/) | Complete |

### Phase notes (not yet detailed plans)

These are intentionally short — each gets its own phase
plan once the previous phase has landed and the working
code has clarified the brief.

**Phase 1 — Per-format metadata emitters**. New crate
`src/crates/create/` (no_std, no parser deps; depends on
`shared` for `ImageFormat` and byte-order helpers, and on
`crates/measure/` for the sizing functions). Public API:

```rust
pub struct CreateOptions {
    // Common
    pub virtual_size: u64,
    pub backing_file: Option<&'static [u8]>,
    pub backing_format: Option<ImageFormat>,
    // Per-format opts re-using the same shape as Measure*Opts
    pub qcow2: Qcow2CreateOpts,
    pub vmdk:  VmdkCreateOpts,
    pub vhd:   VhdCreateOpts,
    pub vhdx:  VhdxCreateOpts,
}

pub struct MetadataWrite<'a> {
    pub byte_offset: u64,
    pub bytes: &'a [u8],
}

pub struct MetadataPlan<'a> {
    pub total_file_size: u64,
    pub writes: &'a [MetadataWrite<'a>],
}

pub fn plan_qcow2<'a>(opts: &Qcow2CreateOpts, scratch: &'a mut [u8]) -> Result<MetadataPlan<'a>, CreateError>;
pub fn plan_vmdk_monolithic_sparse<'a>(opts: &VmdkCreateOpts, scratch: &'a mut [u8]) -> Result<MetadataPlan<'a>, CreateError>;
pub fn plan_vhd_dynamic<'a>(opts: &VhdCreateOpts, scratch: &'a mut [u8]) -> Result<MetadataPlan<'a>, CreateError>;
pub fn plan_vhd_fixed_footer(opts: &VhdCreateOpts, footer_buf: &mut [u8; 512]);
pub fn plan_vhdx_dynamic<'a>(opts: &VhdxCreateOpts, scratch: &'a mut [u8]) -> Result<MetadataPlan<'a>, CreateError>;
```

Initial extraction is by copy from `src/operations/convert/
src/main.rs` (~700 lines of writer code). The convert
operation continues to use its private copies; converting
it to call into `crates/create/` is captured under Future
work.

Unit tests assert that re-parsing the emitted bytes with
the matching parser yields the input options (round-trip),
and that the total file size matches the corresponding
`crates/measure/` calculator output for the empty case
(`allocated_bytes = 0`).

Recommended effort: **high**. Recommended model: **opus**.
The bit-level metadata layouts for vhd / vhdx in particular
have version-encoded checksums and CRC fields that are easy
to get subtly wrong; convert's existing implementation is
the reference but lifting it cleanly requires care.

**Phase 2 — Guest `create` operation**. New
`src/operations/create/` binary, linker script identical
to other operations (load 0x20000, 384 KiB cap). Reads
`CreateConfig` from `OPERATION_CONFIG_ADDR`. If
`backing_file_len > 0` and `virtual_size == 0`, reads
the backing image's header from input device 0 and parses
it with the appropriate parser crate to extract
`virtual_size`. Calls the right `plan_*` function from
`crates/create/`. Iterates the `MetadataPlan` and writes
each chunk via `write_output_sector`. Sends a
`CreateResultMessage`. Add `CreateConfig` to
`src/shared/src/lib.rs`, `CreateResultMessage` to
`guest.proto`, wire the new binary into the workspace
`members` list and the build scripts that copy guest
binaries into the VMM.

Recommended effort: **high** (touches the call-table
boundary, new proto field, guest binary scaffolding).
Recommended model: **opus**.

**Phase 3 — Host VMM subcommand**. Add
`Commands::Create(CreateArgs)` and `run_create()`.
clap surface: `[-f FMT] [-o OPTIONS] [-b BACKING [-F BF]
[-u]] [--object OBJDEF] [-q] FILENAME [SIZE]`. For
`-f raw` with no preallocation or with
`preallocation=falloc`: short-circuit to host-side
`open` + `ftruncate` (+ `posix_fallocate`). For all other
formats: open output with `O_CREAT|O_TRUNC|O_RDWR`,
attach as output virtio device, optionally open backing
read-only and attach as input device 0, populate
`CreateConfig`, launch the guest, render the result.
Output formatting: terse human line by default;
suppress under `-q`; `--output=json` produces the
canonical structured form.

Recommended effort: **medium**. Recommended model:
**sonnet** with a brief that points at `run_convert` for
the device-attachment pattern and `run_measure` for the
result-rendering pattern.

**Phase 4 — `-o` option parsing**. Reuse the
`parse_o_options()` helper introduced in measure phase 5
and the per-format option shapes from `crates/measure/`.
Add create-only keys (`preallocation`, `backing_file`,
`backing_fmt` — the latter two are accepted alongside the
`-b` / `-F` flags for qemu-img compatibility). Reject
unknown keys with a clear error naming all accepted keys
for the chosen format. Recommended effort: **medium**.
Recommended model: **sonnet**.

**Phase 5 — Backing-file support**. End-to-end wiring:
clap surface for `-b` / `-F` / `-u`; backing-file
resolution (relative to the new image's directory);
attaching the backing as input device 0; guest-side
header parsing to extract `virtual_size`; embedding the
user-typed path string in the new image's metadata. Tests:
backing-defaulted size, backing chain (`-b` pointing at a
qcow2 that itself has a backing file — instar should
*not* recurse, just record the immediate backing
reference). Error cases: missing backing file, backing
format not specified and not `-u`, backing file larger
than addressable in the target format. Recommended
effort: **high** (security-sensitive — backing-file
opening on the host is one of the few host-side I/O
paths that touch a user-named file other than the output).
Recommended model: **opus**.

**Phase 6 — Preallocation modes**. Implement `off`,
`falloc`, `full` host-side as a post-guest pass; qcow2-
specific `metadata` mode handled guest-side by populating
L2 / refcount entries during the create emission.
Tests: file size after create matches expectations for
each mode; for `full`, written bytes are zero;
`fallocate` not available → falls back to write loop with
a single zero buffer reused (still trips the OS's
zero-page sharing if applicable). Recommended effort:
**medium**. Recommended model: **sonnet**.

**Phase 7 — Cross-version baselines**. In
`instar-testdata/scripts/generate-baselines.py`, add a
`create` entry that runs `qemu-img create` followed by
`qemu-img info --output=json` and captures the info JSON.
`expected-outputs/create-info-json/<format>/<version>/
<options-hash>.json` keyed by a stable hash of the
`(format, options, size)` triple. Recommended effort:
**medium** for the script change; **low** for the long-
running but mechanical baseline generation pass.
Recommended model: **sonnet**.

**Phase 8 — Integration tests**. New
`tests/test_create.py` covering:
- For each `(format, options, size)` in the matrix and
  each installed qemu-img version: run `instar create`,
  then `instar info --output=json` (host-side), compare
  to the matching qemu-img-derived baseline. Tolerate
  documented diverging fields (uuid, mtime, tool
  version).
- Round-trip: `instar create` then `instar check`
  reports clean.
- `qemu-img create | instar info` ≡ `instar create |
  instar info` field-by-field except the divergence
  whitelist.
- Backing-file tests: `-b` with `-F`, `-b` with `-u`,
  `-b` without either (error), `-b` defaulting size,
  `-b` with explicit size override.
- Preallocation tests per mode.
- Error paths: invalid size, invalid option key,
  conflicting flags.

Tests use `InstarTestBase` and the manifest filtering
used by `test_oslo_crossval.py` for version-keyed
expected outputs. Recommended effort: **medium**.
Recommended model: **sonnet**.

**Phase 9 — Coverage-guided fuzz harnesses**. New
fuzz target `fuzz_create_emitters.rs` in
`src/fuzz/fuzz_targets/`. Takes a fuzzer-supplied
`(format_id, virtual_size, options_packed)` tuple,
calls the matching `plan_*` function. Asserts no
panics, no integer overflow, every emitted write fits
within the declared `total_file_size`, and the
re-parsed image is well-formed (calls the parser
crate's header-validate function on the emitted bytes
buffered in a `BackingStore::Memory`). Recommended
effort: **medium**. Recommended model: **opus** for the
harness design, **sonnet** for the boilerplate.

**Phase 10 — Differential fuzzing extension**. In
`scripts/differential-fuzz.py`, add `create` to the
random operation chain. For each generated
`(format, options, size)`: run `instar create` and
`qemu-img create` (when the option set is qemu-img-
compatible), then `instar info --output=json` on
both, assert field-by-field equivalence with the
documented divergence whitelist. Recommended effort:
**medium**. Recommended model: **sonnet**.

**Phase 11 — Documentation and CHANGELOG**. New
`docs/create.md` covering CLI surface, per-format
metadata summaries, qemu-img divergences (multi-file
vmdk deferred, `--object` deferred, json output
shape). Update `docs/usage.md`, `docs/quirks.md`,
`docs/index.md`, `README.md`, `AGENTS.md` (add the new
operation to the operations list), `ARCHITECTURE.md`
(Format Support section), `CHANGELOG.md` (under
Unreleased / next version), and
`PLAN-convert-followups.md` (mark create as done,
strike it from the deferred list). Recommended effort:
**low**. Recommended model: **sonnet** or **haiku**.

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
only touch one new file in `crates/create/` or one new
test file can run in the main tree.

### Planning effort

This master plan is high-effort. Phases 1, 2, 5 are
high effort. Phases 3, 4, 6, 7, 8, 9, 10 are medium.
Phase 11 is low.

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

Status: **Complete** (all 11 phases shipped on the `create`
branch). The plan was complete when:

* All 11 phases complete and committed on the `create`
  branch.
* `make instar` builds with `create.bin` under the 384 KiB
  operation-binary cap.
* `make lint` clean across the workspace.
* `make test-rust` passes; new tests in create / shared /
  parser crates raise totals as documented in each phase plan.
* `make test-integration` includes `tests/test_create.py`
  exercising the full matrix; failures and skips have
  documented reasons.
* `make check-binary-sizes` includes `create.bin`.
* `pre-commit run --all-files` clean throughout.
* For qcow2 / vmdk monolithicSparse / vhd dynamic / vhd
  fixed / vhdx dynamic targets: `instar info --output=json`
  on instar-created images matches `instar info --output=json`
  on qemu-img-created images (modulo a documented divergence
  whitelist of non-deterministic fields) across every
  qemu-img version in
  `instar-testdata/qemu-img-binaries/x86_64/` per the
  baseline matrix.
* For raw output: file size equals requested virtual size;
  with preallocation modes, the file is allocated as
  expected.
* Coverage-guided fuzz target `fuzz_create_emitters`
  registered in nightly CI.
* Differential fuzzer extended to compare instar create
  output to qemu-img create output via info-equivalence.
* `docs/create.md`, `docs/quirks.md`, `docs/usage.md`,
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `CHANGELOG.md` all updated.
* `PLAN-convert-followups.md` strikes `create` from the
  deferred-subcommand list.

### Future work

* **Refactor convert to call into `crates/create/`** for
  metadata emission. Phase 1 extracts the writers by copy;
  convert's copies should later be deleted and replaced
  with calls into `crates/create/`. Deferred because it
  conflates feature delivery with refactoring of an
  existing, tested code path. Track as a follow-up phase
  once create has soaked.
* **Multi-file VMDK subformats** (`monolithicFlat`,
  `twoGbMaxExtentSparse`, `twoGbMaxExtentFlat`). Requires
  multi-output-device support in the call table.
  Workaround in v1: route users to `instar convert -O
  vmdk -o subformat=...` which already supports these.
* **`--object OBJDEF` for LUKS encryption keys.** Pair
  with encrypted-create support; needs the same passphrase-
  through-config plumbing convert already has plus the
  per-format integration.
* **vhdx fixed subformat.** qemu-img does not produce
  this; instar's vhdx writer in convert is dynamic-only.
  Add if a user requests it.
* **Preallocation for vmdk / vpc / vhdx.** Phase 6 implements
  `metadata` / `falloc` / `full` for qcow2 (plus `falloc` /
  `full` for raw); non-qcow2 sparse formats reject non-`off`
  preallocation with a "future work" pointer. Each format
  needs its own BAT-population pattern up front plus the same
  host `apply_preallocation` post-pass that qcow2 already
  uses. Analogous to the qcow2 metadata-mode work in
  [PLAN-create-phase-06-preallocation.md](/components/instar/plans/PLAN-create-phase-06-preallocation/).
* **`compression_type=zstd` aware create.** qcow2 zstd is
  supported by convert; create should default-decline
  zstd compression in metadata clusters if the runtime
  doesn't have zstd, with a clear error.
* **`-l SNAPSHOT` interaction with create**. qemu-img
  doesn't have this for create; mentioned only for
  parallelism with measure.
* **Atomic-rename safety for the output file.** instar
  currently writes in place; if the guest crashes mid-
  emission, the partial file is left behind. Consider
  writing to `FILENAME.tmp` and `rename()` on guest
  success. Match qemu-img (which does *not* do this) for
  v1, revisit if users complain.
* **VHD CHS-geometry `virtual_size` round-trip.** qemu-img
  rounds the user's requested virtual_size up to the next
  CHS-aligned multiple; instar emits exact bytes. Closes
  every `vhd` entry in
  `tests/test_create.py::KNOWN_WRITER_DIVERGENCES`.
* **qcow2 `refcount_bits` parameterisation.** The writer
  hardcodes `refcount_order=4` (=> 16-bit refcount entries
  on disk) regardless of `-o refcount_bits=...`. Driving
  the L1/L2/refcount math off the user's choice closes
  three `KNOWN_WRITER_DIVERGENCES` entries plus the
  `1G-rb-64` `KNOWN_CHECK_FAILURES` entry.
* **qcow2 `compat=0.10` honouring.** Writer hardcodes
  `compat=1.1`. qemu-img emits both depending on the user's
  option.
* **zstd-aware qcow2 create.** Currently accept-ignored;
  emit the zstd `incompatible_features` bit so subsequent
  writes can land zstd-compressed clusters.
* **vhdx default `block_size` matching qemu's 32 MiB** at
  virtual sizes ≤ 1 GiB. Closes the three
  `KNOWN_WRITER_DIVERGENCES` entries for vhdx defaults.
* **`detect-profiles.py` collision fix in `instar-testdata`.**
  Phase 7's flat-copy logic at
  `copy_multi_bucket_version_to_profile()` assumes case
  names encode the target, but for create-info-json
  `1M-default`, `64M-default`, and `1G-default` collide
  across qcow2 / vmdk / vhd / vhdx / raw. Phase 8 sidesteps
  by reading the raw per-target bucket directly; the fix
  (target-prefix the destination filename, or prefix case
  names at generation time for symmetry with measure) is
  queued for the next testdata regeneration in
  `instar-testdata`.
* **File a tracking issue for the qcow2 `1G-rb-64` check
  failure** (`tests/test_create.py::KNOWN_CHECK_FAILURES`).
  Surfaced by the wave 2b pre-push audit: this is a real
  writer bug (the header records the user's requested
  `refcount_bits` but the on-disk entries are always 16-bit
  because `crates/qcow2::create::build_header` hardcodes
  `refcount_order=4`). The bug is already tracked
  conceptually under the broader "qcow2 refcount_bits
  parameterisation" item above; the follow-up is just to
  add an issue link to the `KNOWN_CHECK_FAILURES` rationale
  so the skip points at a tracking number rather than
  a comment.
* **Named unit tests for the `CreateError::BackingFileTooLong`
  and `CreateError::Overflow` rejection paths.** Wave 2b
  flagged that both are currently exercised only by
  `fuzz_create_emitters`. The fuzz coverage is solid, but a
  named test in `src/crates/create/tests/round_trip.rs` (or
  the per-format inline test modules in
  `src/crates/create/src/lib.rs`) would give
  self-describing test failures if either rejection path
  ever regresses. Low priority — fuzz catches it either way.
* **Named rejection unit tests for the two deferred VMDK
  subformats** (`TwoGbMaxExtentSparse`,
  `TwoGbMaxExtentFlat`). The existing
  `plan_vmdk_rejects_deferred_subformat` test only
  exercises `MonolithicFlat`; the other two variants are
  fuzz-covered only. Same low-priority rationale as the
  `BackingFileTooLong` item above.
* **Spec citations on stable-offset assertions.** Wave 2b
  noted that
  `plan_vhd_dynamic_bat_all_unallocated` hardcodes
  `bat_start = 1536` and
  `plan_vhdx_region_table_points_at_bat_and_metadata`
  asserts `e.file_offset == 0x20_0000` without comments
  citing the VHD / VHDX specs. The offsets are
  spec-mandated, but a one-line comment per assertion would
  keep the tests grep-friendly for future spec readers.
* **Backing-file TOCTOU between `is_file` check and
  `BackingStore::open`.** PR #298 review item #3. The host
  currently `std::fs::metadata`s the resolved backing path
  before `BackingStore::open` reopens it; a symlink swap in
  between would let an attacker substitute a different
  file. Practical risk is low (instar runs as the invoking
  user), but defence-in-depth says we should fstat the
  opened fd instead. Cleanest fix: add
  `BackingStore::is_regular_file()` that calls
  `file.metadata()?.file_type().is_file()` on the inner
  `File` and drop the path-side check. Phase 11's audit
  follow-up deduped the redundant second metadata call;
  closing the TOCTOU window is the next layer.
* **Factor the divergence-whitelist normaliser out of
  `scripts/differential-fuzz.py`.** PR #298 review item
  #9. The fuzzer currently inlines a near-copy of
  `tests/helpers/info_json.py` with a "keep in sync"
  comment. Cheap follow-up: factor into a shared module
  under `scripts/lib/` (or `tests/helpers/`) and have both
  call sites import it. Alternative: add a cross-check
  test that imports both copies and asserts the whitelist
  constants match — surfaces drift instead of preventing
  it.

### Bugs fixed during this work

* **Phase 6c — host-side `flags` assembly didn't set the
  preallocation bits.** Phase 6b added the
  `CreateConfig::preallocation()` decoder for the guest
  side but the host's flag-pack code never set bits 4–5.
  The validator gate prevented non-`Off` from reaching the
  guest anyway, so the gap was latent until 6c lifted the
  validator. Fixed as part of 6c.
* **Phase 9a — fuzz crate's mock `CallTable` was missing
  `send_create_result`.** Phase 2 of create added the
  field to `shared::CallTable`, but no pre-9 fuzz target
  pulled in the create crate transitively, so the gap was
  latent. Surfaced when phase 9a introduced the new
  dependency; added the field plus a no-op mock function
  pointer.
* **Phase 9b — re-parse round-trip assertion was too
  strict.** The libFuzzer harness's strict
  `parsed_virtual_size == requested_virtual_size`
  assertion fired on the first non-grain-aligned input
  (16 813 824 bytes for a vmdk with default 64 KiB grain),
  which the planner correctly rounded up to the next grain
  boundary (16 842 752). Documented behaviour, not a bug.
  Relaxed the assertion to
  `parsed >= requested && parsed - requested < 512 MiB`
  in 9b. Loose enough to absorb any format's alignment
  rounding, tight enough that endianness / offset bugs
  still surface as orders-of-magnitude mismatches.

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
