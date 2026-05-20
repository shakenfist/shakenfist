# PLAN-create phase 6: preallocation modes

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, KVM, virtio,
disk image formats, qemu-img semantics, `posix_fallocate`,
`fallocate(FALLOC_FL_ZERO_RANGE)`), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

This is a phase plan under `PLAN-create.md`. Phases 1–5 shipped
the metadata emitters, guest binary, host CLI, `-o` parser, and
backing-file polish. Phase 6 lifts the preallocation gate that
phase 3 deferred and 4 / 5 left in place.

## Mission

`instar create --preallocation MODE` (and the matching
`-o preallocation=MODE`) currently accepts `off` for every target
and `falloc` for raw. Everything else returns "phase 6 will ship
this". Phase 6 closes the gap for raw and qcow2 — the two
formats where preallocation is most useful — and explicitly
defers non-qcow2 sparse formats (vmdk / vhd / vhdx) to a
follow-up.

Concretely, after phase 6:

| Target | off | metadata | falloc | full |
|--------|-----|----------|--------|------|
| raw    | ✓ (default) | ✗ (n/a for raw) | ✓ (phase 3b) | ✓ (new) |
| qcow2  | ✓ (default) | ✓ (new — guest L1/L2/refcount populated) | ✓ (metadata + host falloc) | ✓ (metadata + host zero-write) |
| vmdk   | ✓ (default) | deferred — clear error | deferred — clear error | deferred — clear error |
| vpc    | ✓ (default) | deferred — clear error | deferred — clear error | deferred — clear error |
| vhdx   | ✓ (default) | deferred — clear error | deferred — clear error | deferred — clear error |

The non-qcow2 deferrals are tracked under PLAN-create.md's
Future-work section after phase 6 lands. Implementing them is
analogous to the qcow2 work — populate BAT entries up front
plus a host falloc/zero pass — but each format has its own
metadata layout to extend.

Note the n/a for raw+metadata: qemu-img doesn't accept
`-o preallocation=metadata -f raw` either (raw has no
"metadata" to preallocate); instar matches the rejection.

## What the survey turned up

### qemu-img semantics for qcow2 preallocation

- **off** (default): file = header + L1 (empty) + refcount table
  + refcount blocks. L2 tables are *not* allocated; reads of any
  virtual cluster return zero via qcow2's read-as-zero default.
  File is ~256 KiB for a 1 GiB virtual image.
- **metadata**: header + L1 (populated) + L2 tables (one per
  L1 entry, populated) + refcount table + refcount blocks (every
  used cluster marked) + data clusters (sequentially allocated;
  contents undefined but typically zero from filesystem). L2
  entries point at the corresponding data cluster offsets. File
  size = total metadata + data clusters. No fallocate.
- **falloc**: metadata mode + `posix_fallocate` on the data
  region so the filesystem reserves the blocks.
- **full**: metadata mode + actually write zeros to the data
  region (or `fallocate(FALLOC_FL_ZERO_RANGE)` when available).

So **metadata** is the foundation; **falloc** and **full** add
a host-side pass on top.

### Existing phase-3 / phase-4 surface

`validate_create_args` in `src/vmm/src/main.rs:~7530`:

```rust
match args.preallocation.as_str() {
    "off" => {}
    "falloc" if args.target_format == "raw" => {}
    "metadata" | "falloc" | "full" => {
        return Err(format!(
            "create: --preallocation={} is not yet supported \
             (preallocation modes land in phase 6 of PLAN-create.md)",
            args.preallocation
        )
        .into());
    }
    other => {
        return Err(format!("create: unknown --preallocation '{}'", other).into());
    }
}
```

`parse_create_o_options` for both raw and qcow2 rejects
metadata/falloc/full with the same phase-6 pointer.

`run_create_raw` calls `posix_fallocate` when
`args.preallocation == "falloc"` and the format is raw.

`run_create_nonraw` doesn't currently look at preallocation at
all — the modes are blocked at the validator.

### Existing preallocation surface in `crates/measure`

`crates/measure/src/lib.rs:59-75`:

```rust
pub enum Preallocation {
    Off,
    Metadata,
    Falloc,
    Full,
}
```

`Qcow2Opts` has a `preallocation` field that affects the
size-calculator's output (metadata/falloc/full add data-cluster
bytes to `required`). The same enum should be lifted into
`crates/create::Qcow2CreateOpts` (the create crate currently has
no preallocation field).

### Existing `CreateConfig.flags` preallocation bits

`src/shared/src/lib.rs::CreateConfig` currently has only four
flag bits (`FLAG_EXTENDED_L2`, `FLAG_LAZY_REFCOUNTS`,
`FLAG_COMPAT_V3`, `FLAG_BACKING_UNSAFE`). It does *not* have
preallocation bits like `MeasureConfig`. Phase 6 adds:

```rust
pub const PREALLOC_MASK:     u32 = 0b11 << 4;
pub const PREALLOC_OFF:      u32 = 0 << 4;
pub const PREALLOC_METADATA: u32 = 1 << 4;
pub const PREALLOC_FALLOC:   u32 = 2 << 4;
pub const PREALLOC_FULL:     u32 = 3 << 4;
```

Matches the layout `MeasureConfig` already uses (bits 4-5 of
flags, two-bit value).

### qcow2 layout changes for `metadata`-mode emission

Phase 1b's `Qcow2Layout` covers header + L1 + reftable +
refblocks. For metadata mode it needs to also cover:

- **L2 tables**: one cluster per L1 entry (i.e. `l1_entries`
  L2 tables). Each L2 table holds `entries_per_l2` entries.
- **Data clusters**: one cluster per virtual cluster
  (`virtual_size / cluster_size` total).
- Updated `used_clusters_before_refcount` → includes L2 tables
  *and* data clusters.
- Updated `total_clusters` and `total_file_size` to extend past
  the data region.

A new `build_l2_table` function emits a populated L2 table whose
entries point at sequential data cluster offsets.

`build_l1_table` for metadata mode populates entries pointing at
L2 table offsets (currently just zero-fills).

`build_refcount_block` already iterates `total_clusters` to
mark refcount=1; updating `compute_layout`'s `total_clusters`
to include L2 + data clusters automatically extends the
refcount coverage.

This is the meaty part of the phase. Roughly +120 lines in
`qcow2::create`, plus matching unit tests.

### `falloc` / `full` host-side helpers

The raw path already uses `libc::posix_fallocate` directly. For
the new modes:

- **falloc**: extends to `posix_fallocate(fd, metadata_end,
  data_region_bytes)`. Same syscall; new offset.
- **full**: write zeros. Two implementations:
  - `fallocate(FALLOC_FL_ZERO_RANGE)` — Linux-specific, fast on
    btrfs / ext4 / xfs.
  - Write-loop fallback — reusable 64 KiB buffer of zeros,
    written sequentially via `pwrite`.
  - The plan recommends FALLOC_FL_ZERO_RANGE with the write-loop
    fallback if the kernel/FS returns `EOPNOTSUPP`.

For raw + full: same write-loop helper but from offset 0 to
virtual_size. Phase 6 introduces a single `fill_zeros(fd,
offset, length)` helper used by both raw + full and qcow2 +
full.

## Public surface added in phase 6

### `crates/create` API changes

```rust
// New: matches measure's enum.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Preallocation {
    #[default]
    Off,
    Metadata,
    Falloc,
    Full,
}

pub struct Qcow2CreateOpts<'a> {
    // ... existing fields ...
    /// Preallocation mode. `Off` (default) emits header + L1
    /// (empty) + refcount tables only — reads of any virtual
    /// cluster return zero via qcow2's read-as-zero default.
    /// `Metadata` extends emission to populate L1 + L2 + refcount
    /// for the full virtual range and lays out data clusters
    /// sequentially in the file. `Falloc` / `Full` produce the
    /// same metadata as `Metadata`; the host caller applies the
    /// `posix_fallocate` / zero-write pass on top.
    pub preallocation: Preallocation,
}
```

`plan_qcow2` returns a `MetadataPlan` whose `minimum_file_size`
extends past the data region for non-`Off` modes.

### `qcow2::create` extensions

- `Qcow2Layout` gains:
  - `l2_clusters: u64` — one per L1 entry; 0 in Off mode.
  - `l2_base_offset: u64` — byte offset of the first L2 table;
    only meaningful when `l2_clusters > 0`.
  - `data_base_offset: u64` — byte offset of the first data
    cluster; only meaningful in non-Off modes.
  - `data_clusters: u64` — `virtual_size / cluster_size` in non-
    Off modes, 0 otherwise.
- `compute_layout` takes an extra `Preallocation` argument and
  routes through a metadata-mode branch that increments
  `used_clusters_before_refcount` to include L2 + data.
- New `build_l2_table(buf, &layout, l1_index) -> &[u8]` — emits
  one L2 table populated with `entries_per_l2` entries, each
  pointing at `data_base_offset + (l1_index * entries_per_l2 +
  entry_index) * cluster_size`.
- New `build_l1_table_populated(buf, &layout) -> &[u8]` — used
  when L2 tables are populated; entries point at L2 table
  offsets. (Or extend `build_l1_table` with a Preallocation
  parameter — cleaner.)

### `CreateConfig` flag bits

```rust
impl CreateConfig {
    pub const PREALLOC_MASK:     u32 = 0b11 << 4;
    pub const PREALLOC_OFF:      u32 = 0 << 4;
    pub const PREALLOC_METADATA: u32 = 1 << 4;
    pub const PREALLOC_FALLOC:   u32 = 2 << 4;
    pub const PREALLOC_FULL:     u32 = 3 << 4;

    pub fn preallocation(&self) -> u32 {
        self.flags & Self::PREALLOC_MASK
    }
}
```

The existing flag bits (`FLAG_EXTENDED_L2 = 1 << 0` …
`FLAG_BACKING_UNSAFE = 1 << 3`) stay at bits 0-3; preallocation
takes bits 4-5; bits 6-31 remain unused.

### Host-side post-guest pass

A new helper `fn apply_preallocation(file: &File, mode: &str,
data_offset: u64, data_len: u64)` runs in `run_create_nonraw`
after the guest returns. It:

- No-ops for `off` and `metadata` (guest already did all the
  work in metadata mode; off has no data region).
- Calls `posix_fallocate(data_offset, data_len)` for `falloc`.
- For `full`: tries `fallocate(FALLOC_FL_ZERO_RANGE)` first;
  on `EOPNOTSUPP` falls back to a `pwrite` loop of a reusable
  64 KiB zero buffer.

`run_create_raw` gains the `full` mode in the same shape — call
`fill_zeros(fd, 0, virtual_size)` after `ftruncate`. The
function is shared between the raw and non-raw paths.

## Validation updates

`validate_create_args`:

- Accept `metadata` for target=qcow2; accept `falloc` and `full`
  for both raw and qcow2.
- Reject `metadata` / `falloc` / `full` for vmdk / vpc / vhdx
  with: `"create: --preallocation=N is not yet supported for
  TARGET (non-qcow2 preallocation is future work — see
  PLAN-create.md)"`.
- Continue to reject `metadata` for raw (qemu-img also rejects
  this — raw has no metadata to allocate).

`parse_create_o_options` mirrors the same accept / reject set.

## Open questions

These should be answered during execution; escalate to the
management session rather than guessing.

1. **Scratch budget for metadata mode.** `Qcow2Layout`'s
   per-mode scratch consumption grows substantially in
   metadata mode — L2 tables alone add `l1_entries *
   cluster_size` bytes. For default `cluster_size=64K` and
   `virtual_size=1 GiB`, that's `2 * 64K = 128 KiB`. For
   `virtual_size=1 TiB`, `2048 * 64K = 128 MiB` — far over
   the guest's `GUEST_CREATE_SCRATCH_LIMIT = 8 MiB`.

   Recommendation: stream the L2 tables — the planner already
   coalesces refcount blocks into one write (phase 1g
   workaround). Add the same coalescing for L2: the guest
   emits each L2 table sequentially into a reusable single-
   cluster scratch slot and writes it via `write_output_sector`
   without keeping it in the `MetadataPlan`. This requires
   either (a) extending `MetadataPlan` to hold a "streamed
   region" alongside the inline writes, or (b) the guest binary
   handling L2 emission outside the plan loop. Pick (b) — keeps
   the `crates/create` API surface stable. Document the design
   in 6b.

2. **Data region in `MetadataPlan`.** For metadata mode, the
   *data clusters* aren't emitted — the file just needs to
   extend to cover them (so L2 entries are valid). Two
   options:
   - Emit a single zero-sector write at `data_base_offset +
     data_len - sector_size` to grow the file.
   - Return `minimum_file_size = data_base_offset + data_len`
     and let the guest's write loop know to extend the output
     via `set_len` after the writes.

   Option B is cleaner. The guest binary already has access
   to the output device; extending it via a final
   `write_output_sector` at the last sector with zeros achieves
   the same effect without extending `MetadataPlan`'s contract.

3. **`fallocate` availability detection.** Some filesystems
   (tmpfs, NFS, certain FUSE) don't support fallocate. Both
   `posix_fallocate` and `fallocate(FALLOC_FL_ZERO_RANGE)` can
   return `EOPNOTSUPP` (or `posix_fallocate` may emulate via
   write loop on glibc — slow). Recommendation: for `full`,
   try the kernel fast path first, fall back to the manual
   write loop on `EOPNOTSUPP`. For `falloc`, `posix_fallocate`'s
   glibc-emulation already handles the unsupported case; accept
   the slower behaviour and document it.

4. **Reusing the zero buffer.** A 64 KiB stack-allocated zero
   buffer is fine for the write loop. Allocating on the stack
   keeps it cache-warm and avoids allocator pressure. Recommend
   64 KiB.

5. **Should `full` mode for raw also use
   `fallocate(FALLOC_FL_ZERO_RANGE)` instead of the existing
   `posix_fallocate`?** Currently raw + falloc uses
   `posix_fallocate` (allocates blocks but doesn't write
   zeros). raw + full should write zeros — easiest is the
   same write loop helper as qcow2 + full. Recommendation:
   share `fill_zeros(fd, offset, length)` between raw + full
   and qcow2 + full.

6. **vmdk monolithicFlat as a "preallocated" subformat.**
   monolithicFlat is fully allocated by definition (no
   sparse extent). Phase 1d defers it to phase 5 follow-up
   anyway. Recommend: out of scope for phase 6.

7. **Reporting `metadata_bytes_written` and `file_size_after`
   from the guest.** In metadata mode, the guest's
   `CreateResult` should reflect the post-extend file size.
   Recommend: include the post-extend size in
   `CreateResult.file_size_after` so the host renderer's JSON
   output is correct.

8. **Should the host also handle qcow2 + full via a single
   shared write loop, even though the guest could in principle
   emit zero sectors itself?** Yes — the host has the existing
   file descriptor open and can use `pwrite` directly without
   round-tripping through virtio. Guest's job ends at
   "metadata + extend"; host's job is "actually fill with
   zeros if requested".

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high   | opus   | none | Extend `crates/create::qcow2::create` to support metadata-mode emission. Add `Preallocation` enum to the qcow2 crate (mirroring `measure::Preallocation` but local — same pattern measure follows). Extend `Qcow2Layout` with `l2_clusters`, `l2_base_offset`, `data_clusters`, `data_base_offset`. Update `compute_layout` to take a `Preallocation` argument and route through a metadata-mode branch that grows `used_clusters_before_refcount` to include L2 tables and data clusters before the fixed-point refcount calculation. Add `build_l2_table(buf, &layout, l1_index)` that emits one populated L2 table (entries point at `data_base_offset + (l1_index * entries_per_l2 + entry_index) * cluster_size`); the OFLAG_COPIED bit (`1 << 63`) gets set per qcow2 spec. Extend `build_l1_table` with a `mode: Preallocation` argument: in metadata-mode, populate entries pointing at `l2_base_offset + l1_index * cluster_size`. Add unit tests covering: layout grows correctly for cluster_size=64K + virtual_size=1G in metadata mode (`total_file_size` ≈ 1 GiB + metadata); L1 + every L2 round-trip through QcowHeader::parse + L1/L2 lookup; refcount blocks have refcount=1 for every used cluster including the data region. Run `cargo test -p qcow2 --features create` to confirm. |
| 6b | high   | opus   | none | Wire preallocation through `crates/create::Qcow2CreateOpts` → `plan_qcow2` → the create guest. Add `preallocation: Preallocation` to `Qcow2CreateOpts` with a `Default` of `Off`. In `plan_qcow2`, call the extended `qcow2::create::compute_layout` with the requested mode; in metadata-mode, call `build_l1_table` with the populated variant and loop over `build_l2_table` for each L1 entry. Because L2 tables can total far more than `GUEST_CREATE_SCRATCH_LIMIT` (e.g. 128 MiB at 1 TiB virtual with 64K clusters), the L2 tables must *stream* rather than land in the `MetadataPlan`'s inline-writes array — design recommendation: have `plan_qcow2` emit a special `MetadataPlan` shape where the data + L2 region is described by `minimum_file_size` and the guest binary's write loop handles the L2 emission via a per-L1 reusable scratch buffer outside the plan. Alternative: expose a `plan_qcow2_streaming` API that the guest drives in a loop. Pick whichever is cleanest; document the choice in the commit message. Add `PREALLOC_*` constants to `CreateConfig` in `src/shared/src/lib.rs` (mirror `MeasureConfig`'s layout at bits 4-5). Translate `CreateConfig.preallocation()` into the `Qcow2CreateOpts.preallocation` field in `qcow2_opts_from`. The guest binary's `_start` extends the output file via a final `write_output_sector` at the last sector with zeros so its size matches `MetadataPlan::minimum_file_size`. Unit tests in the create crate add metadata-mode cases for the existing round-trip integration sweep. |
| 6c | medium | sonnet | none | Host-side post-guest pass for falloc/full + the raw + full path. Add a shared `fn fill_zeros(fd: i32, offset: u64, length: u64) -> io::Result<()>` helper in `src/vmm/src/main.rs` that tries `libc::fallocate(fd, FALLOC_FL_ZERO_RANGE, offset, length)` first, falls back to a `pwrite` loop with a 64 KiB stack-allocated zero buffer on `EOPNOTSUPP`. Add `fn apply_preallocation(file: &File, mode: &str, data_offset: u64, data_len: u64)` that no-ops for `off` and `metadata`, calls `posix_fallocate` for `falloc`, and calls `fill_zeros` for `full`. Wire it into `run_create_nonraw` after the guest's `CreateResult.file_size_after` is known — call `apply_preallocation(&output_file, &args.preallocation, metadata_end, file_size_after - metadata_end)`. For raw, extend `run_create_raw` to call `fill_zeros(fd, 0, virtual_size)` when `args.preallocation == "full"`. |
| 6d | medium | sonnet | none | Validator updates + integration tests. Replace the phase-3 `validate_create_args` preallocation match with the phase-6 accept set: `off` (any), `metadata` (qcow2 only — raw rejects with "raw has no metadata to preallocate"), `falloc` (raw or qcow2), `full` (raw or qcow2). vmdk/vpc/vhdx + metadata/falloc/full return `"create: --preallocation=MODE is not yet supported for TARGET (non-qcow2 preallocation is future work — see PLAN-create.md)"`. Same set wired through `parse_create_o_options`. Add 7 integration tests to `tests/test_create.py` (new class `TestCreatePreallocation`): (1) raw + full → file size = virtual_size, st_blocks > 0; (2) qcow2 + off → small sparse file, instar info reports virtual_size unchanged; (3) qcow2 + metadata → file size = metadata + virtual_size, sparse (st_blocks small); (4) qcow2 + falloc → file size = metadata + virtual_size, st_blocks ≈ virtual_size / 512; (5) qcow2 + full → as falloc, plus reading via dd of='/dev/null' bs=1M count=virtual_size shows all-zero content; (6) raw + metadata → error "raw has no metadata"; (7) vmdk + metadata → error "non-qcow2 preallocation is future work". |
| 6e | low    | sonnet | none | Internal docs: (1) `CHANGELOG.md` — extend the Unreleased "instar create" entry to mention preallocation modes now work for raw + qcow2; remove the "preallocation modes beyond off and raw's falloc" item from the deferred list; add the non-qcow2 preallocation deferral to future-work. (2) `ARCHITECTURE.md` — update operations/create paragraph noting the metadata-mode L2/refcount population and the host post-pass for falloc/full. (3) Mark phase 6 complete in PLAN-create.md's execution table. (4) Add a new entry to PLAN-create.md's "Future work" section: "Preallocation for vmdk / vpc / vhdx — analogous to qcow2 metadata mode but each format has its own BAT-population pattern". |

## Out of scope for phase 6

Reminders so a sub-agent doesn't drift:

- **vmdk / vpc / vhdx preallocation**. Each format needs its
  own BAT-population pattern + host post-pass. Deferred to a
  future phase (likely phase 6 follow-up after a user asks).
  Plan adds these to PLAN-create.md's Future-work section.
- **`compression_type` plumbing through `CreateConfig.flags`**.
  Phase 4's accept-ignore stays accept-ignore — preallocation
  is a separate flag-bit allocation that doesn't touch
  `compression_type`. Defer the compression-bit work to a
  future phase.
- **`--sector-size > 512`**. Still deferred behind the planner
  changes the master plan's Future-work section mentions.
- **Differencing VHD / VHDX target output**. Tracked separately.
- **Encryption**. Tracked separately.

## Success criteria

- `make instar` builds cleanly. `create.bin` size still under
  the 384 KiB cap (expect ~5 KiB growth from the L2 / L1-
  populated builders; current ~36 KiB → ~41 KiB).
- `make lint` clean.
- `make test-rust` passes — new unit tests in `crates/create`
  and `crates/qcow2` covering metadata-mode layout and L2
  round-trip.
- `make test-integration` includes the new
  `TestCreatePreallocation` cases (7 added; total
  `tests/test_create.py` count grows from 29 to 36) and they
  all pass.
- `pre-commit run --all-files` clean.
- All four end-to-end manual smoke checks pass:
  - `instar create -f raw --preallocation full /tmp/r.raw 4M`
    → file is 4 MiB, fully allocated (st_blocks > 0),
    content is all zeros.
  - `instar create -f qcow2 -o preallocation=metadata /tmp/q.qcow2 64M`
    → `qemu-img info /tmp/q.qcow2` reports virtual size 64
    MiB; `du -h /tmp/q.qcow2` shows ~64 MiB (sparse depending
    on filesystem); `qemu-img check /tmp/q.qcow2` passes
    (every cluster allocated).
  - `instar create -f qcow2 -o preallocation=falloc /tmp/q.qcow2 64M`
    → file allocated (st_blocks ≈ virtual_size / 512).
  - `instar create -f qcow2 -o preallocation=full /tmp/q.qcow2 4M`
    → file allocated and all-zero (verifiable with `cmp -s
    /tmp/q.qcow2 /dev/zero` after skipping the metadata
    region).
- `git diff --stat phase-6-base..HEAD -- src/operations/` shows
  changes only to `src/operations/create/` — phase 6 doesn't
  touch any other operation.

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with the brief. In particular, flag if
the brief refers to file/line locations that don't match what
you find when you read them (the survey was a snapshot; the
codebase may have moved).
