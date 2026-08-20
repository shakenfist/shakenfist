# `instar resize` subcommand

## Status: Complete

Phases 1-13.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats, `qemu-img` semantics), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-resize-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img`
subcommands deferred from the convert effort. `measure` shipped
first (`PLAN-measure.md`); `create` shipped second
(`PLAN-create.md`). `resize` is scheduled third — ahead of `map`,
`snapshot`, `rebase`, and `commit` — because:

- It is the first *mutation* operation on an existing image. Every
  shipped write path so far (`convert`, `create`) produces a new
  file; `resize` rewrites L1 / refcount / BAT / GD / metadata
  entries *in place* in the file the user named on the command
  line. A bug here can corrupt user data, so doing it next forces
  us to design the in-place-mutation idiom (atomic header swap,
  refcount-first ordering, sequence-number bumps) before the
  remaining four subcommands inherit it.
- It exercises a call-table primitive that does not yet exist:
  reading from the file being written. Every previous operation
  treats input and output as two distinct devices. Resize has one
  device that is both. Adding `read_output_sector` (or an
  equivalent same-fd read+write mode — see open question 1) is a
  one-time ABI extension that `rebase` and `commit` will reuse.
- It builds directly on `crates/create/`'s per-format sizing
  helpers (L1 size given virtual_size, refcount-table size, BAT
  size, VHD CHS geometry) and on `crates/measure/`'s allocation
  scanners (needed for qcow2 `--shrink` to refuse when allocated
  clusters live above the new virtual size).
- It is the lowest-risk path to the third deliverable on the
  v0.2 release plan. `map` is a read-only operation but its
  output format is a moving target across qemu-img versions;
  `resize` has a stable CLI surface and a clearer correctness
  contract (`qemu-img info` agrees before and after, with
  expected size changes).

The relevant existing infrastructure this plan builds on:

- VMM subcommand scaffolding in `src/vmm/src/main.rs`
  (clap `Commands` enum, per-op `*Args` struct, `run_*`
  function), call-table boundary in `src/shared/src/lib.rs`
  (`OPERATION_CONFIG_ADDR`, per-op `*Config` and `*Result`
  structs at lines 2045–2316), and the protobuf wrapper in
  `crates/guest-protocol/proto/guest.proto`
  (`GuestMessage` oneof payload).
- `crates/create/` (`src/crates/create/src/lib.rs`) — the
  `plan_qcow2` / `plan_vmdk` / `plan_vhd` / `plan_vhdx` planners
  produce `MetadataPlan` values keyed off `(virtual_size,
  per-format options)`. The internal sizing helpers
  (`compute_l1_size`, `compute_refcount_table_size`, VHD
  geometry, VHDX `calculate_bat_layout` at
  `src/crates/vhdx/src/lib.rs:1330`) are the load-bearing
  reusables.
- `crates/measure/` allocation scanners — qcow2 L1/L2 walkers
  (`count_allocated_in_l2_standard` at
  `src/crates/qcow2/src/lib.rs:1044`, `walk_l2_standard` at
  line 1110), VHD BAT counter
  (`src/crates/vhd/src/lib.rs:314`), VHDX BAT counter
  (`src/crates/vhdx/src/lib.rs:558`), VMDK GD/GT walker. Resize
  reuses these to validate `--shrink` requests and to update
  refcounts when extending tables.
- Format parsers (`crates/qcow2/`, `crates/vmdk/`, `crates/vhd/`,
  `crates/vhdx/`) — the resize guest binary parses the existing
  header before computing the patch list.
- The `parse_o_options` helper at `src/vmm/src/main.rs:5610`
  (introduced in measure phase 5, reused by create phase 4).
- The raw host-truncate shortcut pattern from `run_create_raw`
  at `src/vmm/src/main.rs:7528` (`file.set_len()` +
  `posix_fallocate`).
- The cross-version baseline harness in
  `instar-testdata/scripts/generate-baselines.py` and its
  `expected-outputs/{create,measure}-*/` layouts.
- The coverage-guided fuzz harnesses in `src/fuzz/` and the
  differential fuzzer (`scripts/differential-fuzz.py`).

## Mission and problem statement

Implement `instar resize` such that:

1. It accepts the same surface area as `qemu-img resize`:
   - `[-f FMT]` to force the format detection.
   - `[--shrink]` required when the new size is smaller than
     the current virtual size. Without it, instar must refuse
     just as qemu-img does.
   - `[--preallocation PREALLOC]` for the newly-added region
     only (`off` / `metadata` / `falloc` / `full`, matching
     qemu-img's gating rules per format).
   - `[--object OBJDEF]` — out of scope for v1 (defer with a
     clear error; same posture as create v1, see open
     question 7).
   - `[--image-opts]` — out of scope for v1 (same as
     `--object`).
   - `[-q]` quiet mode.
   - `FILENAME` and `[+-]SIZE[bkKMGTPE]` positional arguments.
     SIZE may be absolute (`1G`), additive (`+1G`), or
     subtractive (`-1G`); the latter two are computed against
     the parsed `virtual_size` of the existing image.

2. All metadata mutation runs entirely inside the KVM guest,
   exactly like every other instar operation. The host opens
   the image file with `O_RDWR` (no `O_CREAT`, no `O_TRUNC`),
   attaches it to the guest as a single read/write device,
   and lets the guest read the existing header, compute the
   patch list, and emit the writes. The host performs only:
   pre-launch existence + permission checks, post-launch file-
   size operations (`ftruncate` on grow/shrink finalisation,
   `posix_fallocate` / zero-fill for non-`metadata`
   preallocation), and result rendering.

3. For raw output, the host bypasses the guest entirely:
   `file.set_len(new_size)` plus the optional preallocation
   pass. Same single-code-path-exception rationale as create
   (no metadata to emit; see open question 2).

4. For qcow2 / vmdk monolithicSparse / vhd dynamic / vhd
   fixed / vhdx dynamic, the post-resize bytes are
   *equivalent* to what `qemu-img resize` produces — not
   byte-identical (qemu-img bumps `data_write_guid` on vhdx,
   the diff in random fields is expected) but `instar info`,
   `qemu-img info`, and `instar check` all report identical
   metadata on the two files. This is the validation
   contract.

5. Round-trip parity holds: for every `(format, options,
   start_size, end_size, preallocation)` combination we
   support, `qemu-img info` on
   `instar resize`-d images matches `qemu-img info` on
   `qemu-img resize`-d images (ignoring fields whitelisted
   as legitimately non-deterministic — vhdx GUIDs, mtime,
   tool version).

6. `instar check` on every post-resize image reports clean
   (no orphaned clusters, no refcount inconsistencies, no
   BAT entries pointing past EOF).

7. Coverage-guided fuzzing exercises each per-format resize
   planner directly with `(starting_header_bytes,
   new_virtual_size, options)` triples and asserts no
   panics, no integer overflow, every emitted write fits
   within the declared `total_file_size`, and the re-parsed
   image is well-formed.

8. The existing differential fuzzer is extended so that for
   each randomly generated `(format, options,
   starting_size, new_size, preallocation)` it runs
   `instar resize` and `qemu-img resize` against
   identically-seeded fixtures, then `qemu-img info
   --output=json` on both outputs, and asserts
   info-equivalence.

## Design overview

### Architectural shape

The work decomposes into four layers, mirroring create:

1. **Per-format resize planners** (`src/crates/resize/`,
   `no_std`, depends on `shared`, on `crates/create/` for
   sizing helpers, and on the relevant parser crates for
   header / table-offset extraction). Given the existing
   parsed header and `(new_virtual_size, options,
   preallocation)`, the planner returns a `ResizePlan` — a
   bounded list of byte-level patches plus a `total_file_size`
   (the new EOF the host should `ftruncate` to). Patches are
   typed:

   ```rust
   pub enum ResizePatch<'a> {
       /// Overwrite an existing byte range (header rewrite,
       /// footer copy update, L1-entry update, refcount-entry
       /// update).
       Write { byte_offset: u64, bytes: &'a [u8] },
       /// Append a region of known size with the given
       /// initial bytes (extended L1, extended refcount
       /// block, extended BAT). The byte_offset must equal
       /// the previous file size — the guest writes through
       /// the file's last sector first to make the append
       /// visible to subsequent reads.
       Append { byte_offset: u64, bytes: &'a [u8] },
       /// Zero a byte range without backing it with explicit
       /// payload bytes (lets the planner declare a hole
       /// without paying for the staging buffer).
       ZeroFill { byte_offset: u64, len: u64 },
   }

   pub struct ResizePlan<'a> {
       pub total_file_size: u64,
       pub patches: &'a [ResizePatch<'a>],
       pub action: ResizeAction, // Grow / Shrink / NoOp
   }
   ```

   Planners are pure functions on `(parsed_header, opts) ->
   Result<ResizePlan, ResizeError>`. Errors include
   `ShrinkWithoutFlag`, `ShrinkBelowAllocated`,
   `UnsupportedFormat`, `UnsupportedSubformat`,
   `Overflow`, `BackingFileMismatch` (qcow2 with a backing
   file whose virtual size is smaller than the new top-level
   size — see open question 4).

2. **Guest `resize` operation binary**
   (`src/operations/resize/`). Reads `ResizeConfig` from
   `OPERATION_CONFIG_ADDR`. Reads the existing image's header
   via the new `read_output_sector` call-table primitive
   (see open question 1), probes the format, parses the
   header, walks any allocation tables needed for `--shrink`
   validation, calls the appropriate `plan_resize_*` function,
   iterates the resulting `ResizePlan`, and writes each patch
   via `write_output_sector`. For appends, the guest is also
   responsible for ordering: refcount entries for new clusters
   are written *before* the cluster contents are committed
   anywhere they'd be visible, header rewrite is *last*,
   matching qemu's resize ordering for qcow2.

3. **Host VMM subcommand**. `run_resize()` in
   `src/vmm/src/main.rs`. Parses the clap surface, parses
   `[+-]SIZE`, opens the file with `O_RDWR`, probes the
   format host-side via `format_detection::detect_format_from_header`
   so the clap layer can route raw straight to host-side
   truncate without standing up a guest. For non-raw,
   attaches the file as device 0, populates `ResizeConfig`,
   launches the guest, on success runs the final host-side
   `ftruncate(total_file_size)` (the guest cannot grow the
   file beyond the device's declared size; the host must
   commit the new EOF) and the optional preallocation pass,
   prints the result.

4. **Tests and fuzzers**. Integration tests covering the
   `(format × options × start_size × end_size × preallocation
   × qemu-img version)` matrix; round-trip tests via
   `instar info` and `instar check`; coverage-guided
   fuzzers per planner; differential fuzzer comparing
   `instar resize` to `qemu-img resize` via
   info-equivalence on identical seed fixtures.

Splitting layer 1 from layer 2 keeps the planners
unit-testable in plain `cargo test` and keeps the fuzz harness
trivial. It also leaves a clean reuse point for `rebase` and
`commit`, both of which need to rewrite header fields and L1
entries.

### Call-table extension

Resize is the first instar operation that reads from the device
it writes to. There are three options (compared in detail in
open question 1); the recommendation is **option C: add
`read_output_sector` as a new call-table function pointer**:

```rust
// src/shared/src/lib.rs near line 536 (next to write_output_sector)
pub read_output_sector: unsafe extern "C" fn(u64, *mut u8, usize) -> bool,
```

The host implementation opens the file once with `O_RDWR` and
serves both reads and writes from the same `File`. Page-cache
coherence is the kernel's job; we do not need O_DIRECT. This
adds one function pointer, breaks no existing operation, and is
the cleanest semantic match: there is no "input device" for
resize.

### Per-format resize plans

For each format, the planner produces a `ResizePlan`. The shape
of that plan:

#### Raw

Handled host-side. `file.set_len(new_size)`. With
`preallocation=full`, follow with
`fallocate(FALLOC_FL_ZERO_RANGE)` or zero-write fallback.
With `preallocation=falloc`, `posix_fallocate`. No guest
involvement. Shrink is a `set_len` to a smaller value; data
above the new size is silently discarded, matching `qemu-img
resize -f raw` and matching plain `ftruncate`.

#### QCOW2 grow

The hardest case. Steps:

1. Compute new `l1_size` = `ceil(new_virtual_size /
   (cluster_size * l2_entries_per_cluster))`. If unchanged,
   skip steps 2–4 and proceed to step 5.
2. If new `l1_size > old l1_size`:
   - Allocate a *new* L1 region at the current end of file,
     extending the file by `l1_size * 8` bytes (rounded up
     to a cluster). Copy the old L1 contents into the start
     of the new L1 region; pad the tail with zeros.
   - The new L1 region requires refcount entries: extend
     the refcount table and write new refcount blocks to
     cover the new L1 region itself plus any other
     newly-allocated clusters from this resize. Refcount-
     extension is recursive — extending the refcount table
     may itself require allocating new clusters for the
     refcount block, which then need refcount entries of
     their own. Iterate until fixed-point. This is the
     same algorithm qemu's `qcow2_grow_refcount_table`
     uses; the implementation lives in
     `src/crates/resize/src/qcow2_grow.rs`.
3. Rewrite the header in-place: bump `size`, bump
   `l1_size`, bump `l1_table_offset` (to the new L1
   region), bump `refcount_table_offset` /
   `refcount_table_clusters` if the refcount table moved.
4. Decrement the refcount of the *old* L1 region to 0.
   (Until the header is rewritten in step 3, both L1 regions
   exist — atomic swap via header rewrite is the safety
   guarantee.)
5. If `preallocation=metadata`, also allocate L2 tables for
   the newly-addressable virtual range and zero data
   clusters for each L2 entry, populating L1/L2 entries
   accordingly. With `preallocation=falloc`/`full` the
   metadata stays minimal and the host post-pass fills the
   data region.
6. `total_file_size` = old EOF + new L1 region size + new
   refcount entries + (for `preallocation=metadata`) new
   L2 regions and zero data clusters.

The ordering matters for crash safety: step 2 must commit
before step 3, and step 4 must follow step 3. The planner
expresses this as a strictly-ordered patch list; the guest
must not reorder.

#### QCOW2 shrink

Steps:

1. Walk every L1 entry. For each non-zero L1 entry, walk the
   L2 table. For each L2 entry pointing to a cluster whose
   guest offset is `>= new_virtual_size`, mark the cluster
   for discard. Track the highest still-allocated cluster
   offset.
2. If any allocated cluster's guest offset is
   `>= new_virtual_size` *and* `--shrink` was not requested,
   return `ResizeError::ShrinkWithoutFlag`. (qemu's
   behaviour: refuse silently with that error message.)
3. With `--shrink`: for each cluster marked for discard,
   write zero into the L2 entry, and decrement the
   refcount entry. If a whole L2 table becomes all zero,
   zero the L1 entry too.
4. Reduce `header.size` to `new_virtual_size`.
5. Shrink `l1_size` if the new virtual size no longer
   requires all entries.
6. `total_file_size` = max(highest-still-allocated cluster
   end, header + refcount + L1 region end). Note: qemu
   does not currently truncate the file past metadata even
   after a shrink — orphaned cluster space stays inside
   the file as dead bytes. Match qemu's behaviour. Document
   in `docs/quirks.md`.

The L2-walk in step 1 may need to read many clusters; the
guest must use the call-table's read-cache. The planner
computes its patches without buffering the entire L1/L2
tree (one L2 cluster at a time), so memory stays bounded.

#### VMDK monolithicSparse grow

Steps:

1. Recompute `capacity_sectors = ceil(new_virtual_size /
   512)`.
2. Compute new `nb_grain_tables = ceil(capacity_sectors /
   (grain_size * grains_per_table))`.
3. If `nb_grain_tables > old_nb_grain_tables`:
   - Append zeroed grain tables at end of file (each is
     `grains_per_table * 4` bytes, cluster-aligned).
   - Append new GD entries (4 bytes each, pointing at the
     new GT offsets) — extend GD in place if it has
     reserved capacity; otherwise rewrite GD at end of
     file and update the header's GD offset. (qemu uses
     pre-reserved overhead in `vmdk4_create`; we should
     match by reading the header's `grain_offset` and
     header overhead and deciding which path to take.)
4. Rewrite the header to point at the new GD location and
   bump `capacity` and `grain_offset`.
5. Rewrite the embedded descriptor with the new
   `RW <new_sectors> SPARSE "filename"` extent line.
   The descriptor is at a header-pointed offset; its
   length may change, so the planner reserves growth
   slack as qemu does.
6. `total_file_size` = end of new metadata region.

#### VMDK monolithicSparse shrink

Reject for v1 with `UnsupportedShrink` (qemu-img shrinks
monolithicSparse only since 6.0 and the allocation-walk
correctness story is fiddly). Add to Future work.

#### VMDK other subformats

`streamOptimized`, `monolithicFlat`,
`twoGbMaxExtentSparse`, `twoGbMaxExtentFlat`: reject in v1
with a clear error pointing at qemu-img. Multi-file
subformats require the multi-device call-table change
already deferred under create's Future work. Defer here too.

#### VHD dynamic grow

Steps:

1. Recompute CHS geometry from `new_virtual_size` via the
   existing `compute_vhd_geometry` helper at
   `src/crates/vhd/src/lib.rs:248`.
2. Compute new `max_table_entries = ceil(new_virtual_size
   / block_size)`. If unchanged, only steps 4–5 apply.
3. If new BAT size > old BAT size: append BAT entries
   (4 bytes each, all `0xFFFFFFFF` initially), extending
   the BAT region. The BAT lives between the dynamic
   header and the first allocated block; if blocks are
   already allocated past the old BAT, qemu allocates a
   *new* BAT region at end of file and updates
   `dynamic_header.table_offset`. Match this — the
   planner detects the "BAT-tail collides with allocated
   block" case and emits an Append for a new BAT region
   plus a Write for the header.
4. Rewrite both footer copies (one at offset 0, one at end
   of file) with new `virtual_size` and new CHS bytes.
   Recompute the footer checksum.
5. Rewrite the dynamic header with new `max_table_entries`
   and (if relocated) `table_offset`. Recompute the
   dynamic-header checksum.

`total_file_size` = old EOF + new BAT bytes + 512 (footer
copy at EOF — stays at end).

#### VHD dynamic shrink

Reject in v1 with `UnsupportedShrink`. qemu-img does
support it (since 5.0 IIRC) but the allocation walk is
similar to qcow2's and worth a separate phase. Add to
Future work.

#### VHD fixed grow

Steps:

1. `total_file_size = new_virtual_size + 512`.
2. Move the existing footer from `old_size` to
   `new_virtual_size`. (Patch: Write the footer bytes at
   the new offset; the host's post-pass `set_len` discards
   the old footer slot.)
3. Rewrite the footer with new `virtual_size`, new CHS,
   new checksum.

The new region between `old_size - 512` (old footer
position) and `new_size` is sparse zeros after `set_len`;
preallocation modes apply if requested.

#### VHD fixed shrink

Similar but the planner refuses if any data byte in the
discarded range is non-zero. v1 implements grow only;
shrink in Future work.

#### VHDX dynamic grow

Steps (most complex format):

1. Bump the active header's `sequence_number`,
   `data_write_guid`, and recompute its checksum.
   Write the *inactive* header first (with bumped seq);
   instar's resize follows VHDX's two-header dance.
2. Update the `VirtualDiskSize` metadata entry to
   `new_virtual_size`. Recompute the metadata region's
   checksum.
3. Recompute BAT size via
   `calculate_bat_layout(virtual_disk_size, block_size)`
   at `src/crates/vhdx/src/lib.rs:1330`. If new BAT size
   exceeds the BAT region's pre-allocated capacity,
   append a new BAT region at end of file and update the
   region table. (Pre-allocated BAT capacity is typically
   ample — qemu sizes BAT to a fixed fraction of disk
   capacity at create time.)
4. Append new BAT entries (8 bytes each,
   `PAYLOAD_BLOCK_NOT_PRESENT`), extending the BAT region.
5. Recompute and rewrite the region table if BAT or
   metadata region moved. Both regions have CRCs.
6. Invalidate the WAL log (write `0` to the
   `log_offset`'s sequence number, matching qemu's
   "abandon any uncommitted log on resize").

`total_file_size` = end of new region table or BAT
extension or metadata extension, whichever is highest.

#### VHDX dynamic shrink

Not supported by qemu-img. Reject in v1; add nothing to
Future work (qemu has no implementation to mirror).

#### QED

Not supported. qemu-img can resize QED but instar's parser
is read-only. (Note, corrected by PLAN-format-coverage
phase 6: qemu does NOT deprecate QED — the read-only-parser
decision is instar's own scope choice, not a response to
qemu sunsetting the format.) Add to Future work.

#### LUKS

Not supported in v1. Encrypted resize needs the same
passphrase plumbing as encrypted create. Add to Future
work.

### Backing-file interaction

`qemu-img resize` does not modify backing-file references.
Resizing a qcow2 with a backing file does NOT touch the
backing file's `virtual_size`; if the new top-level size
exceeds the backing file's virtual size, reads from the
unbacked range return zero. Match qemu exactly:

- Resize ignores backing-file metadata entirely.
- No host-side open of the backing file.
- If the new size is smaller than the backing file's
  virtual size, qemu does not complain. Match this; emit
  no warning.

### qemu-img output format

`qemu-img resize` prints (to stderr by default):

```
Image resized.
```

Or for shrink without --shrink:

```
qemu-img: warning: Shrinking an image will delete all data beyond the shrunken image's end. Before proceeding, make sure there is no important data there.

Error: Use the --shrink option to perform a shrink operation.
```

Match the success line verbatim (one line, `Image
resized.`) under default verbosity; suppress under `-q`. For
errors, match qemu's wording closely enough that scripts
keyed off either tool's output will tolerate ours. `--output=json`
produces a small structured form:

```json
{
  "format": "qcow2",
  "old_virtual_size": 1073741824,
  "new_virtual_size": 2147483648,
  "old_file_size": 197120,
  "new_file_size": 393216,
  "action": "grow"
}
```

### Test matrix

| Format | Grow | Shrink | Preallocation modes | Notes |
|--------|------|--------|---------------------|-------|
| raw | ✓ | ✓ | off, falloc, full | host-side truncate |
| qcow2 | ✓ | ✓ (with `--shrink`) | off, metadata, falloc, full | full coverage |
| vmdk monolithicSparse | ✓ | deferred | off only | shrink to Future work |
| vmdk other | reject | reject | n/a | error pointing at convert |
| vhd dynamic | ✓ | deferred | off, falloc, full | shrink to Future work |
| vhd fixed | ✓ | deferred | off, falloc, full | shrink to Future work |
| vhdx dynamic | ✓ | reject | off, falloc, full, metadata | matches qemu (no shrink upstream) |
| qed | reject | reject | n/a | deferred |
| luks | reject | reject | n/a | deferred |

For each supported `(format, options, start_size, end_size)`,
verify:
- `instar info --output=json` after resize matches
  `qemu-img info --output=json` on a qemu-resize'd reference.
- `instar check` reports clean.
- File size on disk is exactly what the format requires
  (raw: equals new_virtual_size; qcow2: at least
  header + l1 + refcount + per-mode preallocation overhead;
  etc.).
- Existing allocated data is preserved (write a recognisable
  pattern before resize, verify it reads back unchanged
  after).

### Versioning and baseline strategy

Extend `instar-testdata/scripts/generate-baselines.py` with a
`resize` entry. For each
`(qemu-img version, format, options, start_size, end_size,
preallocation)`:

1. Run `qemu-img create` to produce a starting fixture.
2. Run `qemu-img resize` to apply the resize.
3. Run `qemu-img info --output=json` on the resized fixture
   and capture the JSON.
4. Compare to the JSON output of `qemu-img info` on the
   instar-resized image at integration-test time.

Size matrix:
- ~80 qemu-img versions
- 4 grow-capable formats × ~6 option combinations × 4
  size pairs × 4 preallocation modes ≈ ~400 tuples per
  version after gating out the invalid combinations.
- ~32k JSON baselines at <2 KiB each (~60 MiB total in
  the testdata repo). Above create's ~20 MiB; comparable
  in order of magnitude.

If size becomes a concern, we cull pre-emptively at
generation time using a per-format whitelist of option
combinations that observably behave differently across
qemu-img versions (we know from create's experience that
many `-o` combinations produce identical info output across
versions; we can collapse those).

## Open questions

1. **Same-file read+write semantics**. Resize is the first
   operation that reads from the file it writes to. Three
   options:
   - **A. Attach the file twice** (`open` read-only + `open`
     read-write) and expose it as input device 0 and output
     device 0 simultaneously. Pros: no ABI change. Cons:
     wastes a device slot, the read-only fd is semantically
     misleading (it observes writes through the page cache),
     and we'd need to ensure no operation accidentally
     mutates input device 0.
   - **B. Add a `read_write_input` device mode** — input
     device 0 is the same fd as the output device. Pros:
     conceptually clean. Cons: requires call-table fields
     for the mode bit and complicates device setup.
   - **C. Add `read_output_sector`** to the call table next
     to `write_output_sector`. Pros: one function pointer,
     semantically obvious (you read from what you write to
     because they are the same), trivial host impl
     (`File::read_at`), reusable by future in-place
     operations (`rebase`, `commit`, snapshot delete).
     Cons: ABI growth.
   - **Recommendation: C.** Single line of ABI growth; clear
     semantics; future operations need it anyway. The
     call-table struct is at `src/shared/src/lib.rs:498`
     and already has 10+ function pointers — one more is
     not a structural change. The host implementation is
     trivial because `BackingStore::Open` already exposes
     `read_at`.

2. **Raw + preallocation as host-only**. `instar create -f
   raw` shortcuts past the guest. Resize should match.
   `set_len` is identical to `ftruncate`, and the host
   already implements the preallocation post-pass for
   create's raw path. No guest launch for raw resize.
   Document as a deliberate asymmetry in `docs/resize.md`.

3. **`[+-]SIZE` parsing**. qemu-img accepts:
   - Absolute: `SIZE[bkKMGTPE]` → set `new_virtual_size`
     to that value.
   - Additive: `+SIZE[bkKMGTPE]` → `new = current + SIZE`.
   - Subtractive: `-SIZE[bkKMGTPE]` → `new = current -
     SIZE`. Subtractive implies shrink and is gated by
     `--shrink`.
   Plus the suffix set: `b`=512, `k`/`K`=KiB, `M`/`G`/`T`/`P`/`E`
   (binary). Absent suffix = bytes (some qemu versions warn).
   Implementation: extend the existing size-string parser in
   `parse_o_options` to recognise the `+`/`-` prefix; route
   the parsed delta vs. absolute through `ResizeArgs` as
   `Either<u64, i64>`. The host resolves to an absolute
   `new_virtual_size` only after probing the source format
   and reading its `virtual_size`.

4. **qcow2 backing-file size mismatch**. If a qcow2 has a
   backing file with virtual_size 1 GiB and the user resizes
   the qcow2 to 2 GiB, qemu does not warn. The unbacked
   range reads as zeros. *Recommendation*: match qemu, emit
   no warning. Document the behaviour in
   `docs/resize.md` and `docs/quirks.md` because OpenStack
   users hit this regularly.

5. **VHDX BAT-tail vs allocated-block collision**. Growing a
   VHDX where the BAT immediately abuts the first allocated
   payload block forces the BAT to be relocated. Matching
   qemu's relocation algorithm exactly (where does it put
   the new BAT? does it write zeros into the old BAT slot?)
   requires reading qemu's `vhdx_resize` in detail. Phase 5
   (VHDX) should do this research up front.

6. **vhd dynamic BAT layout invariants**. Same concern: the
   dynamic header points at a BAT region whose size is
   `ceil(max_table_entries * 4 / 512)` sectors. Grow may
   relocate. Phase 4 (VHD) does the same research.

7. **`--object` and `--image-opts` deferral**. v1 rejects
   both with a clear "not yet supported" error pointing at
   the convert path that handles encrypted images. Defer
   encrypted resize to a follow-up phase paired with
   encrypted-create. Same posture as create.

8. **`-q` scope**. Match qemu: under `-q`, suppress
   `"Image resized."` on success; print errors normally on
   stderr.

9. **Existing-file safety**. Resize mutates the user's file
   in place. There is no `O_TRUNC`; the file is opened
   `O_RDWR`. On grow, partial failure leaves an
   intermediate-state file. *Recommendation*: match qemu —
   no atomic-rename safety net for v1. Document in
   `docs/quirks.md`. The crash-safety guarantee is per-
   format (qcow2's atomic-header-swap; vhdx's
   double-header dance; vhd's footer-at-EOF + footer-at-0
   pair). Each planner emits patches in the order that
   preserves the format-level invariant. Document the
   invariant per format in `docs/resize.md`.

10. **Concurrency**. instar's existing operations assume
    the host has exclusive use of the file. Resize is no
    different. Document the assumption alongside convert's
    in `docs/security.md`.

11. **`ResizeConfig` field layout**. Mirror the
    `CreateConfig` layout. Magic `0x52455349` ("RESI" little-
    endian). Fields:
    - `target_format: u32`
    - `flags: u32` (bits: shrink, preallocation 2 bits,
      object_reserved, image_opts_reserved, quiet,
      backing_unsafe_reserved)
    - `sector_size: u32`
    - `current_virtual_size: u64` (the host computes this
      before launch by probing the file; the guest cross-
      checks against the parsed header)
    - `new_virtual_size: u64`
    - `qcow2_options`, `vmdk_options`, `vhd_options`,
      `vhdx_options` (small per-format structs, all zero
      if not applicable)
    - reserved padding for forward compat.

12. **`ResizeResult` field layout**. Protobuf-only, mirror
    `CreateResult` shape:
    ```
    message ResizeResultMessage {
        string target_format = 1;
        uint64 resolved_new_virtual_size = 2;
        uint64 file_size_after = 3;
        uint64 file_size_before = 4;
        string action = 5;  // "grow" / "shrink" / "noop"
        string error = 6;   // empty on success
    }
    ```

## Future work

Consolidated inventory of resize-related work deferred during
phases 1–12, with the originating phase pointer for each. Pulled
from the inline "Future work" mentions across the phase plans so
reviewers can find every queued item in one place. Mirrored in
`docs/resize.md`'s Future-work section for user-facing visibility.

### Planner gaps

- **qcow2 `Preallocation::Metadata`** (phase 2c). The qcow2
  grow planner returns `PreallocationUnsupported`; qemu
  supports it. Closing the gap needs the same
  `Qcow2Layout` extension that ships in create's metadata
  mode, adapted for the grow path.
- **vmdk shrink** (phase 6). monolithicSparse permits shrink
  but the planner doesn't implement the GD walk + grain
  deallocation. Rejected today with `UnsupportedShrink`.
- **vhd shrink** (phase 4). Fixed + dynamic grow ship; shrink
  is deferred. Same `UnsupportedShrink` rejection.
- **vhdx shrink** (phase 5). qemu has no upstream
  implementation to mirror, so neither do we. Same rejection.
- **Sparse-format data-region preallocation** (phase 9). For
  `falloc`/`full` on qcow2/vhdx/vmdk/vhd-dynamic, instar
  preallocates only the appended file region;
  qemu preallocates the entire data region. Closing the gap
  needs a per-format walk-and-populate pass — comparable in
  complexity to a half-create operation per format.
- **vmdk multi-extent subformats** (phase 6). `twoGbMaxExtent*`
  and `monolithicFlat` rejected with `UnsupportedSubformat`.
  Multi-file resize needs the same multi-output-device call-
  table extension create's roadmap already calls out.
- **Differencing VHD / VHDX as the resize target** (phases
  4 + 5). Currently rejected; needs the parent-locator
  update path.
- **qcow2 overlays with a backing file** (push-audit
  finding; phase 13). The grow / shrink planners take a
  `backing_file: Option<&[u8]>` and `backing_format:
  Option<&[u8]>` in `Qcow2ResizeOpts`, but the guest's
  pre-pass always passes `None` for both, so a resize
  rewrites the header without preserving the existing
  backing reference. Host-side `probe_resize_target`
  rejects overlays up-front with a clear message pending
  the proper fix — plumbing the existing backing bytes +
  format through `ResizeConfig` so the planner can pass
  them to `build_header`. Until lifted, users must
  flatten via `instar convert` (or resize the base image)
  before resizing the chain.

### Host CLI gaps

- **`--object OBJDEF`** (phase 8). Rejected with a "not yet
  supported" message; lands alongside the matching convert-
  side LUKS plumbing.
- **`--image-opts`** (phase 8). Same — rejected with a clear
  deferral message.

### Robustness / hardening

- **Tighten `QCOW2_MAX_RESIZE_SCRATCH` for non-default
  cluster sizes** (phase 12 finding). The current 32 MiB
  scratch is sized for default 64 KiB clusters; 2 MiB
  clusters overflow it with even modest virtual sizes
  (`image too large for the resize scratch buffer`). The
  differential fuzz picker filters the combination today.
- **Targeted shrink-side pre-pass** (followup-01 set the
  precedent for grow). Today the guest stages every non-zero
  refcount block before the L2 walk for the shrink path,
  retaining the per-cluster-size image-size ceiling
  followup-01 lifted for grow (~128 GiB at default cluster).
  Lifting it requires a two-phase shrink pre-pass: walk L2
  tables first to identify which clusters will be discarded,
  then stage only the refcount blocks containing those
  clusters.  Comparable design effort to followup-01.
- **Planner-side defensive checks for inconsistent host
  inputs** (phase 12 finding, partially addressed by
  followup-01d's vmdk fix). The VHDX planner can return
  `Ok(plan { total_file_size: 0 })` when the host passes
  impossibly small file sizes relative to the metadata
  region's offset. Not reachable from real callers (the host
  derives `current_file_size` from `stat()` on the actual
  file) but worth hardening; the coverage-fuzz target's
  input-clamp envelope avoids the path today.

### Fuzz coverage

- **Re-parse round-trip in `fuzz_resize_planners`** (phase 12
  open question 1). Reconstruct a faithful starting image
  from the fuzzer's synthetic existing-state bytes and
  re-parse with the matching format crate; mirrors
  `fuzz_create_emitters`'s contract.
- **Curated seed corpus for `fuzz_resize_planners`** (phase
  12 open question 8). `scripts/extract-fuzz-corpus.py` has
  no resize codepath today — input shape is a packed
  `(format_selector, opts, slices)` blob, not a raw image.
- **Populated-image differential coverage** (phase 12 open
  question 3). The differential harness creates empty start
  images today; populated variants become meaningful once
  data-region preallocation parity lands.
- **libyal-based vmdk/vhd/vhdx differential coverage**
  (phase 12 open question 5). If `vmdkinfo` / `vhdiinfo` ever
  gain resize support we get a third axis for the formats
  qemu can't compare against.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Resize planner crate skeleton (`crates/resize/`) + raw + shared types | [PLAN-resize-phase-01-skeleton.md](/components/instar/plans/PLAN-resize-phase-01-skeleton/) | Complete |
| 2. QCOW2 grow planner (L1 + refcount-table extension) | [PLAN-resize-phase-02-qcow2-grow.md](/components/instar/plans/PLAN-resize-phase-02-qcow2-grow/) | Complete except `Preallocation::Metadata` (deferred — see Future work) |
| 3. QCOW2 shrink planner (`--shrink` semantics, L2 walk, cluster discard) | [PLAN-resize-phase-03-qcow2-shrink.md](/components/instar/plans/PLAN-resize-phase-03-qcow2-shrink/) | Complete |
| 4. VHD resize planner (dynamic grow, fixed grow; shrink deferred) | [PLAN-resize-phase-04-vhd.md](/components/instar/plans/PLAN-resize-phase-04-vhd/) | Complete |
| 5. VHDX resize planner (dynamic grow; shrink unsupported upstream) | [PLAN-resize-phase-05-vhdx.md](/components/instar/plans/PLAN-resize-phase-05-vhdx/) | Complete |
| 6. VMDK resize planner (monolithicSparse grow; others rejected) | [PLAN-resize-phase-06-vmdk.md](/components/instar/plans/PLAN-resize-phase-06-vmdk/) | Complete |
| 7. Guest `resize` operation + protobuf + `read_output_sector` | [PLAN-resize-phase-07-guest-op.md](/components/instar/plans/PLAN-resize-phase-07-guest-op/) | Complete |
| 8. Host VMM subcommand + clap surface + `[+-]SIZE` parsing | [PLAN-resize-phase-08-host-cli.md](/components/instar/plans/PLAN-resize-phase-08-host-cli/) | Complete |
| 9. Preallocation modes (`off`/`metadata`/`falloc`/`full`) | [PLAN-resize-phase-09-preallocation.md](/components/instar/plans/PLAN-resize-phase-09-preallocation/) | Complete (sparse-format data-region preallocation deferred — see Future work) |
| 10. Cross-version baselines in `instar-testdata` | [PLAN-resize-phase-10-baselines.md](/components/instar/plans/PLAN-resize-phase-10-baselines/) | Complete (3,280 baselines × 80 qemu-img versions; vmdk/vhd/vhdx record qemu's "format does not support resize" rejection — phase 11 falls back to internal consistency checks for those) |
| 11. Integration tests (`tests/test_resize.py`) | [PLAN-resize-phase-11-integration-tests.md](/components/instar/plans/PLAN-resize-phase-11-integration-tests/) | Complete (114 tests: 83 pass + 31 documented skips; first run also surfaced two device-routing / CLI bugs fixed in b1d2dac) |
| 12. Coverage-guided + differential fuzz harnesses | [PLAN-resize-phase-12-fuzz.md](/components/instar/plans/PLAN-resize-phase-12-fuzz/) | Complete (fuzz_resize_planners: 730k iter / 5 min clean, cov 469, ft 594; differential op_resize: 200 iter / seed 42 clean; CI wired into coverage-fuzz.yml at 17 targets) |
| 13. Documentation, CHANGELOG, follow-ups | [PLAN-resize-phase-13-docs.md](/components/instar/plans/PLAN-resize-phase-13-docs/) | Complete (new docs/resize.md; CHANGELOG `[Unreleased]` entry; AGENTS/ARCHITECTURE/README mentions; docs/quirks.md `## resize subcommand quirks`; docs/index.md TOC; docs/format-coverage.md per-format table; docs/testing.md test-file enumeration; `~~resize~~` struck from PLAN-convert-followups.md; consolidated Future-work section below) |

### Phase notes (not yet detailed plans)

Each gets its own phase plan once the previous phase has
landed and the working code has clarified the brief.

**Phase 1 — Skeleton + raw + shared types**. New crate
`src/crates/resize/` (no_std; depends on `shared`, on
`crates/create/` for sizing helpers, and on each format
parser crate for header decoders). Public API:

```rust
pub enum ResizePatch<'a> { /* Write / Append / ZeroFill */ }
pub enum ResizeAction { Grow, Shrink, NoOp }
pub struct ResizePlan<'a> { /* total_file_size, patches, action */ }
pub enum ResizeError { ShrinkWithoutFlag, ShrinkBelowAllocated,
    UnsupportedFormat, UnsupportedSubformat, UnsupportedShrink,
    Overflow, ... }
pub fn plan_resize_raw(opts: &RawResizeOpts) -> Result<ResizePlan<'static>, ResizeError>;
// One stub per format, returning UnsupportedFormat for v1;
// real implementations land in phases 2-6.
```

Raw planner is in fact host-side only (returns `NoOp` with
`total_file_size = new_virtual_size`; the host does the
`set_len`). Including it in the crate keeps the surface
uniform. The crate is also where `ResizeConfig` /
`ResizeResult` field shapes live as Rust structs (they
already live in `shared/`, but the *encoding/decoding*
helpers belong with the planner).

Recommended effort: **high**. Recommended model: **opus**.

**Phase 2 — QCOW2 grow planner**. Implement
`plan_resize_qcow2_grow(header, opts) -> ResizePlan`. The
core complexity is in the refcount-table extension's
fixed-point algorithm. Tests: unit tests at every
combination of `(cluster_size ∈ {512, 4K, 64K, 1M},
refcount_bits ∈ {1, 16, 32}, l1_grows or stays,
refcount_table_grows or stays)`. Compare emitted patches
against qemu's actual byte-by-byte output on a small
fixture matrix. Recommended effort: **high**. Recommended
model: **opus**. Isolation: **worktree**.

**Phase 3 — QCOW2 shrink planner**. Implement
`plan_resize_qcow2_shrink(header, opts, allow_shrink) ->
ResizePlan`. Touches the L2 walker, refcount-decrement
logic, and L1-entry zeroing. Tests: `--shrink` without
the flag must error; `--shrink` to a size that discards
zero clusters; `--shrink` to a size that discards data;
shrink to zero (rejected; min image size is one cluster).
Recommended effort: **high**. Recommended model: **opus**.
Isolation: **worktree**.

**Phase 4 — VHD resize planner**. Dynamic grow (BAT
extension + footer rewrite + dynamic header rewrite),
fixed grow (footer move + rewrite). Shrink deferred to
Future work. Tests: per-subformat grow at multiple sizes,
verify CHS geometry round-trips, verify checksums.
Recommended effort: **high**. Recommended model: **opus**.

**Phase 5 — VHDX resize planner**. Dynamic grow with the
two-header dance, BAT extension, metadata-entry update,
WAL invalidation. The trickiest of the non-qcow2 formats
because of the region-table CRC and the
sequence-number-bump protocol. Tests: grow at multiple
sizes, verify region table CRC, verify VirtualDiskSize
metadata entry, verify log invalidation. Recommended
effort: **high**. Recommended model: **opus**.

**Phase 6 — VMDK resize planner**. monolithicSparse grow
only. Reject other subformats with a clear error.
Recommended effort: **high**. Recommended model: **opus**.

**Phase 7 — Guest `resize` operation**. New
`src/operations/resize/` binary. Reads `ResizeConfig` from
`OPERATION_CONFIG_ADDR`. Probes the format from sector 0,
parses the header, dispatches to the matching
`plan_resize_*`, iterates the plan, writes each patch.
Add `ResizeConfig` and `ResizeResult` to
`src/shared/src/lib.rs`, `ResizeResultMessage` to
`guest.proto` (oneof `GuestMessage`), add
`read_output_sector` to the call table at
`src/shared/src/lib.rs:536` and to the host-side call-
table implementation in the VMM, wire the new binary into
the workspace `members` list and the build scripts that
copy guest binaries into the VMM. Recommended effort:
**high**. Recommended model: **opus**.

**Phase 8 — Host VMM subcommand**. Add
`Commands::Resize(ResizeArgs)` and `run_resize()`. clap
surface: `[-f FMT] [--shrink] [--preallocation PREALLOC]
[--object OBJDEF] [--image-opts] [-q] FILENAME
[+-]SIZE[bkKMGTPE]`. SIZE parsing handles absolute /
additive / subtractive. For `-f raw`: short-circuit to
host-side `set_len` (+ `posix_fallocate`). For all other
formats: open the output with `O_RDWR`, attach as device
0, populate `ResizeConfig`, launch the guest, then run
the host-side `set_len(total_file_size)` and optional
preallocation pass, render the result. Output: terse
human line by default (`"Image resized."`); suppress
under `-q`; `--output=json` produces the structured form.
Reject `--object` and `--image-opts` with a clear "not
yet supported" error. Recommended effort: **medium**.
Recommended model: **sonnet** with a brief pointing at
`run_create_raw` for the truncate pattern and
`run_create_nonraw` for the device-attachment pattern.

**Phase 9 — Preallocation modes**. Implement `off`,
`falloc`, `full` host-side as a post-guest pass (the
host knows `total_file_size` from `ResizeResult`).
qcow2-specific `metadata` mode is handled guest-side by
the qcow2 grow planner already populating L2 entries
during the resize emission. vhdx `metadata` similarly.
Tests: file size after resize matches the mode's
expectation; for `full`, the new region is zeroed; the
`falloc → write-loop` fallback path is exercised by
disabling `posix_fallocate` in a test wrapper.
Recommended effort: **medium**. Recommended model:
**sonnet**.

**Phase 10 — Cross-version baselines**. In
`instar-testdata/scripts/generate-baselines.py`, add a
`resize` entry that runs `qemu-img create` → `qemu-img
resize` → `qemu-img info --output=json` and captures the
info JSON. Output layout:
`expected-outputs/resize-info-json/<format>/<version>/<options-hash>.json`
keyed on a stable hash of `(format, options,
start_size, end_size, preallocation)`. Recommended
effort: **medium** for the script change; **low** for
the long-running but mechanical baseline pass.
Recommended model: **sonnet**.

**Phase 11 — Integration tests**. New
`tests/test_resize.py` covering:
- For each `(format, options, start, end,
  preallocation)` in the matrix and each installed
  qemu-img version: write a known pattern, run
  `instar resize`, then `instar info --output=json`,
  compare to the matching qemu-img-derived baseline,
  verify the known pattern reads back from the
  preserved region.
- Round-trip: `instar resize` then `instar check`
  reports clean.
- `qemu-img resize` then `instar info` matches
  `instar resize` then `instar info`, field-by-field
  except the divergence whitelist (vhdx GUIDs, mtime,
  tool version).
- Shrink tests: `--shrink` required for negative size
  delta; shrink that would discard allocated data
  errors; shrink that only loses unallocated space
  succeeds.
- Error paths: invalid size (negative absolute, zero,
  larger than format max), invalid option key,
  `--object` rejected, unsupported format rejected,
  unsupported subformat rejected.

Tests use `InstarTestBase` and the manifest filtering
used by `test_create.py`. Recommended effort: **medium**.
Recommended model: **sonnet**.

**Phase 12 — Fuzz harnesses**. Two harnesses:

1. *Coverage-guided fuzz target*
   `fuzz_resize_planners.rs` in `src/fuzz/fuzz_targets/`.
   Takes a fuzzer-supplied `(format_id, starting_header_bytes,
   new_virtual_size, options_packed, shrink_flag)` tuple,
   calls the matching `plan_resize_*` function. Asserts
   no panics, no integer overflow, every patch byte range
   fits within `total_file_size`, no write overlaps with a
   later write at the same offset (planner-internal
   invariant), and the re-parsed image is well-formed
   when the patches are applied to the input bytes.
2. *Differential fuzz extension* in
   `scripts/differential-fuzz.py`. Add `resize` to the
   random operation chain. For each generated
   `(format, options, start_size, end_size,
   preallocation)`: create an identical fixture with
   `qemu-img create`, run `instar resize` against one
   copy and `qemu-img resize` against another, then
   `instar info --output=json` on both, assert
   field-by-field equivalence with the documented
   divergence whitelist.

Recommended effort: **medium**. Recommended model:
**opus** for harness design, **sonnet** for boilerplate.

**Phase 13 — Documentation and CHANGELOG**. New
`docs/resize.md` covering CLI surface, per-format
metadata changes summarised, qemu-img divergences (multi-
file vmdk deferred, `--object` deferred, shrink scope per
format, vhdx GUID divergence, json output shape). Update
`docs/usage.md`, `docs/quirks.md`, `docs/index.md`,
`README.md`, `AGENTS.md` (add resize to operations
list), `ARCHITECTURE.md` (resize wiring + the new
`read_output_sector` call-table primitive),
`CHANGELOG.md` (under Unreleased / next version), and
`PLAN-convert-followups.md` (mark resize as done, strike
it from the deferred list). Recommended effort: **low**.
Recommended model: **sonnet** or **haiku**.

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
edits the call table, anything that adds a proto field,
anything that mutates an on-disk format in a way that could
corrupt existing fixtures, and the baseline-generator
across the qemu-img matrix). Steps that only touch one new
file in `crates/resize/` or one new test file can run in
the main tree.

### Planning effort

This master plan is high-effort. Phases 1, 2, 3, 4, 5, 6, 7
are high effort (planner correctness is load-bearing).
Phases 8, 9, 10, 11, 12 are medium. Phase 13 is low.

### Step-level guidance

Each phase plan should fill in a table like:

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
- [ ] Guest binaries pass `make check-binary-sizes` (384 KiB
      limit per operation).
- [ ] `make test-rust` and the relevant
      `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief — semantically
      right, not just syntactically.
- [ ] For mutation phases (2–6): a *post-resize* fixture round-
      trips through `instar info` and `instar check` clean.
- [ ] For mutation phases: a known data pattern written before
      resize reads back unchanged after.
- [ ] Commit message follows project conventions
      (Co-Authored-By with model + context window + effort,
      Signed-off-by, Prompt paragraph).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented when:

* All 13 phases complete and committed on the `resize` branch.
* `make instar` builds with `resize.bin` under the 384 KiB
  operation-binary cap.
* `make lint` clean across the workspace.
* `make test-rust` passes; new tests in resize / shared /
  parser crates raise totals as documented in each phase plan.
* `make test-integration` includes `tests/test_resize.py`
  exercising the full matrix; failures and skips have
  documented reasons.
* `make check-binary-sizes` includes `resize.bin`.
* `pre-commit run --all-files` clean throughout.
* For raw / qcow2 / vmdk monolithicSparse / vhd dynamic / vhd
  fixed / vhdx dynamic targets: `instar info --output=json`
  on instar-resized images matches `instar info
  --output=json` on qemu-img-resized images (modulo a
  documented divergence whitelist of non-deterministic
  fields) across every qemu-img version in
  `instar-testdata/qemu-img-binaries/x86_64/` per the
  baseline matrix.
* `instar check` reports clean on every post-resize image
  in the integration test matrix.
* Coverage-guided fuzz target `fuzz_resize_planners`
  registered in nightly CI.
* Differential fuzzer extended to compare instar resize
  output to qemu-img resize output via info-equivalence.
* `docs/resize.md`, `docs/quirks.md`, `docs/usage.md`,
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `CHANGELOG.md` all updated.
* `PLAN-convert-followups.md` strikes `resize` from the
  deferred-subcommand list.

### Future work

* **QCOW2 grow with `Preallocation::Metadata`.** Phase 2c
  deferred the metadata-mode population path; the planner
  currently rejects `Preallocation::Metadata` with
  `PreallocationUnsupported`. The work involves appending L2
  tables for new L1 entries (zero-filled, since every L2 entry
  is 0 for an empty range), populating new L1 entries with
  L2-table offsets + `OFLAG_COPIED`, optionally appending zero
  data clusters to match qemu's `disk size` reported value, and
  extending the refcount entries to cover the new L2 (and
  optionally data) clusters. Either land as a 2e sub-step under
  the existing phase plan, or roll into phase 9's preallocation
  work.
* **QCOW2 internal snapshot virtual_size adjustment.** qemu-
  img resize leaves internal snapshots at their snapshot-
  time virtual_size; the top-level resize affects only the
  active L1 table. Match qemu. If users complain, expose a
  flag.
* **VMDK shrink** for monolithicSparse. Same allocation-
  walk story as qcow2 shrink; defer because the convert
  path is the more common operational shape.
* **VMDK multi-file subformats**
  (`monolithicFlat`, `twoGbMaxExtentSparse`,
  `twoGbMaxExtentFlat`, `streamOptimized` for grow).
  Requires multi-output-device support in the call table.
  Same deferral as create's multi-file VMDK.
* **VHD dynamic shrink.** qemu supports it; instar doesn't
  in v1 because the allocation-walk implementation parallels
  qcow2 shrink and warrants its own phase.
* **VHD fixed shrink.** Trivial in principle (footer move +
  set_len) but the allocation walk to refuse on non-zero
  data above the new size adds complexity.
* **QED resize.** instar's QED parser is read-only; defer
  unless a user requests it. (Not because qemu deprecates
  QED — see PLAN-format-coverage phase 6.)
* **LUKS / encrypted resize.** Pair with encrypted-create
  support; needs the same passphrase-through-config
  plumbing convert already has, plus per-format
  integration. Same deferral as create's `--object`.
* **`--image-opts`.** Defer with encrypted resize.
* **`-l SNAPSHOT` interaction.** qemu-img resize does not
  accept this; mentioned only for parallelism with measure.
* **Atomic-rename safety for the file being resized.**
  instar mutates in place; if the guest crashes mid-
  emission, the file is in an intermediate state. Each
  format planner emits patches in an order that preserves
  the format-level invariant (qcow2's atomic header swap,
  vhdx's two-header dance, vhd's footer pair), so the
  worst case is "resize did not happen" rather than
  "image is corrupt". Match qemu; revisit if the
  invariant proves insufficient under fault injection.
* **Resize-by-cluster-eviction for qcow2.** qemu has a
  TRIM/discard pathway that could free clusters that
  happen to be all-zero. instar resize doesn't currently
  scan for these. Add as an optimisation if file-size
  growth becomes a complaint.
* **Lift `read_output_sector` from a per-operation
  primitive to a general "device opens" model.** Today the
  guest distinguishes input-only and output-only devices;
  resize is the first read-write user. As `rebase` and
  `commit` land, we should consider whether the
  input/output distinction is still pulling its weight, or
  whether a single `device_io(dev_idx, op, offset, buf,
  len)` primitive would be cleaner. Defer the audit until
  at least one of rebase/commit has shipped.
* **Differential fuzz for in-place mutation invariants.**
  Phase 12's differential fuzz checks `info` equivalence.
  A stronger property — every byte that wasn't supposed
  to change is byte-identical to its pre-resize value —
  would catch a class of bugs where instar accidentally
  rewrites a cluster outside the metadata patch list.
  Defer because it requires whole-file diffing infra we
  don't have yet.

### Bugs fixed during this work

This section will list bugs encountered during development
that we fixed.

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
