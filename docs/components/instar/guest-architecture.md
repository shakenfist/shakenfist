# Guest and VMM architecture

How instar actually parses a disk image: a host-side virtual machine
monitor drives a purpose-built bare-metal guest, and the parsers run
inside that guest where a malicious image cannot reach the host.

This page covers the approaches considered, the design that was chosen,
the guest's structure, its call table, and its memory map. See
[Architecture](https://github.com/shakenfist/instar/blob/develop/ARCHITECTURE.md) for how it fits the rest of the tool,
and [prototypes/](https://github.com/shakenfist/instar/tree/develop/prototypes) for the experiments that led here.

## Prototype Approaches

## Approach A: Minimal Linux Guest

Use a tiny Linux distribution (like Alpine or a custom initramfs) running
inside KVM. The guest runs a conversion daemon that communicates with the
host via virtio-vsock.

Pros:
- Can reuse existing libraries (e.g., qemu-img inside the guest)
- Familiar debugging environment
- Flexible

Cons:
- Larger attack surface (full Linux kernel)
- Higher memory/CPU overhead
- Boot time latency

## Approach B: Unikernel

Build a unikernel that only contains the conversion logic. No separate
kernel/userspace distinction.

Pros:
- Minimal attack surface
- Fast boot times
- Lower resource usage

Cons:
- More complex development
- Limited library ecosystem
- Harder to debug

## Approach C: Custom Bare-Metal (Active)

Write a minimal bare-metal program that runs directly under KVM with no OS.
Just enough code to handle virtio communication and format conversion.

**This is the approach being actively explored.**

Pros:
- Absolute minimum attack surface
- Fastest possible boot/execution
- Complete control

Cons:
- Significant development effort
- Must implement everything from scratch
- No existing tooling

**Progress:**
- [helloworld](https://github.com/shakenfist/instar/tree/develop/prototypes/helloworld) - Minimal KVM VMM with serial output
- [helloworld2](https://github.com/shakenfist/instar/tree/develop/prototypes/helloworld2) - Uses vm-memory crate for safer memory
- [virtio-block](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block) - Virtio-block device emulation with file copy
- [virtio-block2](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block2) - Adds guest-protocol (protobuf) integration
- [virtio-block3](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block3) - Adds configurable sector sizes
- [virtio-block4](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block4) - Adds performance statistics tracking
- [virtio-block5](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block5) - Adds ioeventfd optimization
- [virtio-block6](https://github.com/shakenfist/instar/tree/develop/prototypes/virtio-block6) - Adds sparse/dynamic output file support
- [pluggable](https://github.com/shakenfist/instar/tree/develop/prototypes/pluggable) - Modular operations architecture
- [pluggable2](https://github.com/shakenfist/instar/tree/develop/prototypes/pluggable2) - Separate binary loading for operations
- [info](https://github.com/shakenfist/instar/tree/develop/prototypes/info) - Image format detection (qemu-img info equivalent)

**Current Implementation:**
The `info` prototype has been promoted to the main implementation in `src/`. This
provides a modular architecture with:
- **vmm/** - Host-side virtual machine monitor
- **core/** - Guest initialization (device init, call table). Also installs
 a minimal IDT (`core/src/idt.rs`) covering the CPU exception vectors
 (0..=31) as its first boot step, so any guest CPU exception — an invalid
 opcode (`#UD`) from a codegen miscompile, a page fault from a stray
 pointer — is caught and reported to the host as a clean `cpu-exception`
 error (naming the vector and faulting RIP) instead of escalating to a
 silent triple fault. See [issue #375](https://github.com/shakenfist/instar/issues/375).
- **crates/qcow2/** - Shared QCOW2 format crate: header parsing, L1/L2
 cluster lookup (including extended L2 with 16-byte entries
 and full subcluster bitmap parsing), subcluster bitmap validation
 (`validate_subcluster_bitmap()` enforcing QCOW2 spec
 invalid-combination rules), compressed cluster decompression (zlib
 via `decompress` feature, ZSTD via `decompress-zstd` feature using
 ruzstd), cluster compression (behind `compress` feature flag using
 raw deflate via miniz_oxide), refcount table reading (all widths:
 1/2/4/8/16/32/64-bit), compressed L2 entry parsing, backing file
 extraction, header extension parsing, incompatible feature bit
 validation. The chain reader honours the `QCOW_OFLAG_ZERO` (bit 0)
 flag on classic (non-extended) L2 entries: `cluster_lookup` returns
 a `ClusterLookup::Zero` verdict and the chain reader zero-fills for
 it, for both host == 0 and host != 0 (the phase-7 step-7z fix for
 issue #432; previously a zero-flagged chain cluster read as
 fall-through or stale host bytes — silent active-view corruption
 affecting rebase / convert / compare / bench). Supports cluster
 sizes from 512B to 2MB (cluster_bits 9-21). Used by info, check, compare, convert, and measure
 operations. Also exposes `Qcow2State::scan_allocation` plus the
 pure helpers `count_allocated_in_l2_standard` /
 `count_allocated_in_l2_extended` to produce a
 `shared::AllocationSummary` consumed by the `measure` subcommand.
- **crates/raw/** - Shared RAW format crate: MBR/GPT partition table
 detection. Used by info operation. Also exposes a trivial
 `scan_allocation` (allocated_bytes == virtual_size) for the measure
 subcommand.
- **crates/vmdk/** - Shared VMDK format crate: VMDK4 binary header parsing
 (basic and full), descriptor I/O and text parsing, grain directory/table
 reading with sector-cached lookups, streamOptimized footer reading,
 grain marker handling, and write helpers for monolithicSparse and
 streamOptimized output. Used by info, check, convert, and compare
 operations. Also exposes `VmdkState::scan_allocation` plus
 `count_populated_gd_entries` / `count_allocated_in_gt` for the measure
 subcommand.
- **crates/vhd/** - Shared VHD/VPC format crate: footer parsing and
 validation (conectix cookie, CHS geometry, disk type), dynamic header
 parsing (cxsparse cookie, BAT offset, block size), BAT reading with
 sector-cached lookups, block-level data access via BlockLookup enum,
 VhdState for stateful block I/O, sub-sector-aligned read support
 (`read_offset_sectors` for VHD data spanning device sector boundaries),
 and write helpers (build_footer, build_dynamic_header,
 compute_vhd_geometry, plus footer_geometry / chs_rounded_geometry:
 build_footer writes qemu's upward-search CHS for qemu-roundable
 sizes — which can differ from the floor geometry of the same byte
 count, issue #413 — and the VHD-spec floor CHS for verbatim sizes
 qemu-img would never declare). Used by info, check, convert, and compare
 operations. Also exposes `VhdState::scan_allocation` plus the pure
 helper `count_allocated_in_bat` for the measure subcommand.
- **crates/vhdx/** - Shared VHDX format crate: CRC-32C (Castagnoli)
 checksum implementation, dual header parsing with sequence number
 selection, region table parsing with CRC validation, GUID-based
 metadata item lookup, 64-bit BAT reading with interleaved sector
 bitmap entry handling, VhdxState for stateful block I/O, and output
 builders (file identifier, headers, region table, metadata, BAT
 entries). Used by check, convert, and compare operations. Also
 exposes `VhdxState::scan_allocation` plus `count_allocated_in_bat`
 (which handles the chunk_ratio bitmap interleaving) for the measure
 subcommand.
- **crates/vdi/** - Shared VDI (VirtualBox Disk Image) format crate:
 header parsing and validation against qemu's twelve `vdi_open`
 rules (signature/version/geometry checks, odd `disk_size` rounded
 up to 512 rather than rejected, any `image_type` accepted,
 `block_extra` parsed but unused), allocation-order block-map
 reading with sector-cached lookups, and `VdiState` for stateful
 block I/O (`init`/`block_lookup`, mirroring `vhd::VhdState`).
 Read-only: no write/output support. Linked into the qcow2 crate's
 chain reader behind the `vdi-input` feature and used by convert,
 compare, bench, and rebase (the PLAN-format-coverage work).
- **crates/parallels/** - Shared Parallels Disk Image format crate:
 header parsing and validation against qemu's RO `parallels_open`
 rules (both magics, version check, `tracks`/`bat_entries` limits,
 `ext_off != 0` refused), per-magic BAT decoding (sector-valued
 entries under the legacy `WithoutFreeSpace` magic, cluster-valued
 entries under `WithouFreSpacExt`), the v1-only 32-bit `nb_sectors`
 mask, and `ParallelsState` for stateful block I/O
 (`init`/`block_lookup`, mirroring `vdi::VdiState`). Read-only: no
 write/output support. Linked into the qcow2 crate's chain reader
 behind the `parallels-input` feature and used by convert, compare,
 bench, and rebase (the PLAN-format-coverage work).
- **crates/qcow1/** - Shared QCOW1 ("qcow", qemu's original
 copy-on-write format, superseded by qcow2 but not formally
 deprecated by qemu) crate: header parsing and validation against qemu's exact
 RO `qcow_open` rules (magic + version == 1, `cluster_bits`/`l2_bits`
 ranges, size bounds including the empirically-pinned "Image too
 large" boundary, `crypt_method` <= 1 at parse, backing-file-name
 length), two-level L1/L2 block lookup (entries are absolute byte
 offsets; bit 63 marks a compressed cluster with a byte-granular
 `{host_offset, csize}` pair), and `Qcow1State` for stateful block
 I/O (`init`/`block_lookup`, mirroring `parallels::ParallelsState`;
 `init` additionally refuses `crypt_method != 0`, while `parse`
 stays lenient for info's benefit). Read-only: no write/output
 support. Linked into the qcow2 crate's chain reader behind the
 `qcow1-input` feature (which also pulls in the `decompress`
 feature for raw-DEFLATE compressed-cluster inflation) and used by
 convert, compare, bench, and rebase; the reader arm is the first
 non-QCOW2 format to support backing-chain fall-through, mirroring
 the QCOW2 arm's own unallocated-cluster recursion instead of the
 VDI/Parallels arms' zero-fill (the PLAN-format-coverage work).
- **crates/dmg/** - Shared DMG (Apple UDIF) format crate: koly-trailer
 parsing (reusing the phase-1 shared trailer helpers), chunk-table
 assembly from either the XML-plist path (string-scanned `<data>`
 blocks, decoded with a byte-for-byte port of glib's lenient base64)
 or the old resource-fork path, mish/BLKX chunk-entry parsing into a
 sorted, verified lookup table, and `DmgState` for stateful per-
 sector chunk lookup (`init`/`chunk_lookup`, returning span-typed
 Zero/Raw/Zlib results). Codec scope is zero/raw/ignore/zlib
 (zlib-WRAPPED inflate, unlike QCOW1's raw-deflate); ADC/bzip2/
 lzfse/zstd/unknown chunk types get a typed init refusal naming the
 code rather than qemu's drop-then-EIO shape, and a chunk table that
 parses to zero entries is refused cleanly at init (where qemu
 SIGSEGVs on every version tested). Enforces its own bounded-memory
 caps (`DMG_REGION_STAGE_CAP`, `DMG_MAX_CHUNKS`,
 `DMG_MAX_STAGED_SECTOR_COUNT`), distinct from qemu's own larger
 legal range, as typed refusals. Read-only: no write/output support;
 chunk *decompression* and byte copies live in the reader arm, not
 this crate. Linked into the qcow2 crate's chain reader behind the
 `dmg-input` feature (which also pulls in the `decompress` feature)
 and used by convert, compare, bench, and rebase; unlike every other
 format-coverage reader, DMG reads a missing/truncated span as an
 ERROR rather than zero-filling, matching qemu exactly
 (the PLAN-format-coverage work).
- **crates/luks/** - Shared LUKS format crate: LUKS v1/v2 header
 constants, header parsing, PBKDF2 key derivation, Argon2id key
 derivation (behind `kdf-argon2` feature), AFsplitter key recovery,
 master key verification, and AES-XTS payload decryption (behind
 `decrypt` feature). Used by info and convert operations.
- **crates/measure/** - Shared size-calculator crate (`no_std`, no I/O):
 per-output-format estimators (raw / qcow2 / vmdk / vhd / vhdx) for the
 `required` and `fully-allocated` byte counts that `qemu-img measure`
 emits. The qcow2 estimator matches qemu-img's worst-case sizing
 semantics (L2 tables sized for the full virtual range; refcount layout
 sized once for the fully-allocated cluster count and reused for the
 sparse case). `AllocationSummary` has been moved to `crates/shared` so
 format crates can produce it without depending on `measure`; a
 back-compat re-export remains in this crate. Consumed by the
 `measure` operation in `src/operations/measure/` and by the
 size-estimation helpers shared with `create` and `resize`.
- **crates/qcow2-write/** - Shared qcow2 write-planner crate (`no_std`,
 no I/O, no guest addresses): the windowed step-program planner for
 "write N bytes at virtual offset X into an existing qcow2, allocating
 as needed" (the PLAN-qcow2-write-infrastructure work). `plan_write`
 classifies each touched cluster from staged metadata (owned in-place
 overwrite with zero metadata churn / fresh allocation with
 sub-cluster zero-fill, including a fresh L2 table when the L1 slot is
 empty / typed refusals for compressed, snapshot-shared,
 unknown-bit-pattern and backing-fill shapes) and emits typed `Step`s
 (`#[repr(C)]`, const-asserted at 48 bytes or less) into a
 caller-provided `StepBuf`; the executor runs each window literally
 and resumes on `BufFull`, which doubles as the staged-L2 window's
 load boundary (the planner emits `LoadCluster` and closes the window,
 because the slot's bytes exist only after execution). Steps are
 address-free — staged buffers are named by `RegionId` + offset and
 devices by `TargetDevice` (`Input0`/`Output`) — and each planning
 call borrows a `StagedRegions` view of the executor's staged L1 /
 L2-window / refcount-table / refblock buffers, of which only the
 refblocks are mutable: the planner mutates staged refcounts in place
 at plan time (bench's single-copy model) while L1/L2 mutations stay
 `PatchEntryU64` steps, and `plan_flush` emits the epoch's write-backs
 refcounts-last. Barriers are explicit steps with
 `BarrierClass::{Ordering, Durability}`; because the call table
 exposes only `fsync_input`, executors map `Durability` to fsync on RW
 input devices and degrade it to `Ordering` where no fsync primitive
 exists (matching commit/rebase's current no-fsync output-device
 reality). The crash-ordering contract — data written before the L2
 patch that reaches it, fresh-L2 init before the L1 patch, refcount
 write-backs only at flush and last, Durability barriers between flush
 groups — is emission-order data, pinned mechanically by an
 ordering-contract property suite (window-invariance across buffer
 capacities down to a 1-step buffer) and a SimDisk simulation harness
 that replays the step journal truncated at every Durability barrier.
 Envelope gates (qcow2 v2/v3, 16-bit refcounts, no
 unknown-incompatible bits, no extended-L2 / external data /
 encryption, not dirty/corrupt, no internal snapshots) run at state
 construction, so a gated image can never yield a write plan. Three
 ops consume it: commit (, 2026-07-13 — the qcow2
 backing-side write path), rebase safe mode including safe
 detach (, 2026-07-13 — the overlay-side copy path, with an
 op-side skip probe against original pre-run L2 state deciding
 which clusters reach the planner at all), and bench `-w` (,
 2026-07-13 — the qcow2 write-benchmark path). All are planned by
 this crate and executed through `crates/qcow2-write-exec`, proven
 byte-invisible by the `scripts/migration-proof.py` before/after
 harness (73/73, 69/69 and — for bench, whose oracle is
 compare + check rather than byte identity — 56/56 fixture combos,
 300-iteration differential fuzz clean each; rebase carries one
 sanctioned beyond-EOV raw divergence with proven virtual equality,
 and bench's allocating shapes are content-equivalent but not
 byte-identical by design). The crate also owns the pure
 refcount-growth planner in its `growth` module (`plan_refcount_growth`,
 `GrowthCaps`, `RefcountGrowthPlan`, `GrowthOverflow`), moved out of
 `crates/bench`; growth execution moved to
 `crates/qcow2-write-exec` (see below). A later change
 (2026-07-13) added the crate's **copy-on-write branch**, lifting the
 three ops' interim snapshot-refusal gates (issues #420 / #421 / #423
 resolved). A COW-capable caller builds its `WriteState` via
 `new_state_cow` and relaxes the envelope with
 `check_envelope_with(hdr, allow_snapshots = true)`; the classifier
 then turns the `SnapshotShared` / `SnapshotSharedL2Table` verdicts
 from refusals into COW emission. Data-cluster COW copies the shared
 `D → D'`, repoints the L2, sets `rc(D')=1` and decrements `rc(D)`
 (the old cluster is never freed — the snapshot holds it); L2-table
 COW copies `T → T'`, repoints the L1, sets `rc(T')=1`, decrements
 `rc(T)`, and — critically — leaves the child data-cluster refcounts
 untouched (qemu eagerly bumps every reachable cluster to rc ≥ 2 at
 snapshot-creation time, so a child-increment would corrupt to rc 3;
 the children already classify shared and COW per-write). This needs
 a net-new refcount-**decrement** primitive (`dec_refcount`; v1 only
 ever incremented on allocation), whose underflow maps to
 `WriteError::RefcountInconsistent`. The zero-flag WRITE-target policy
 (decision 6): host == 0 allocates fresh, host != 0 rc 1 overwrites
 in place clearing the zero bit, host != 0 rc > 1 COWs — qemu never
 frees the old offset. No new `StepKind`. The COW output is proven
 qemu-parity, never byte-identical to qemu (C11). The crate's
 Vec-backed simulation harness (`TestImg` + the executor role +
 `run_write` / `run_flush` `BufFull`-resume loops + the COW fixtures +
 the `rc_of` / `max_rc` assertion helpers) lives in a feature-gated
 `#[cfg(any(test, feature = "sim"))] pub mod sim`: the crate's
 own unit tests import it, the `sim` feature is OFF in the production
 build (it needs `std`, and the guest ops are `no_std`
 `x86_64-unknown-none`, so the ops' `.bin` sizes are unchanged), and the
 `fuzz_qcow2_write` coverage target enables it to fuzz the planner
 (see Coverage-Guided Fuzzing below).
- **crates/qcow2-write growth-execution move.** The
 imperative refcount-growth EXECUTION (previously in the bench op) is
 now the shared, region-agnostic `growth::grow_refcounts` in
 `crates/qcow2-write-exec`, so commit and rebase can grow the
 refcount structures during COW, not just bench. Behaviour is
 byte-identical to bench's prior execution (the #433 materialization
 fix and the single-fsync census are preserved).
- **crates/qcow2-write-exec/** - Shared guest-side step executor for
 `crates/qcow2-write` step programs (`no_std`,
 the PLAN-qcow2-write-infrastructure work): a literal interpreter of
 the `StepKind` doc contracts with zero planning logic —
 `execute(steps, regions, devices)` applies one planned window in
 emission order and aborts on the first failure with the step index
 and a typed cause (nothing panics; every region access is
 bounds-checked). The `DeviceIo` trait abstracts the per-device
 call-table entry points; `CallTableIo` maps `Input0` to
 `read/write_input_sector(0)` + `fsync_input(0)` and `Output` to
 `read/write_output_sector` with no fsync capability. Its byte-range
 layer (`read_bytes` / `write_bytes` / `fill_bytes`) sits over the
 strictly sector-addressed call table — whole aligned sectors
 transfer directly, sub-sector head/tail goes through
 read-modify-write on a caller-provided bounce sector — and is
 exposed as the shared replacement for the byte-range helpers the
 commit / rebase / bench / bitmap ops each hand-roll. `Regions`
 maps each planner `RegionId` to a caller-carved scratch slice
 (never `static`) plus the two executor service sectors (one shared
 RMW bounce — safe because all call-table I/O is synchronous and
 steps execute serially — and a fill-synthesis sector). Barrier
 policy: `Ordering` is a no-op (issue order is completion order),
 `Durability` fsyncs where the capability exists and degrades to
 `Ordering` elsewhere (matching commit/rebase's no-fsync
 output-device reality). Host-unit-tested against a mock `DeviceIo`
 with journals and failure injection, including end-to-end
 compositions driving `plan_write` / `plan_flush` through the
 executor over a model disk. Consumed by the commit op,
 the rebase op's safe mode, and the bench op's qcow2 `-w`
 path, which also drives its refcount-growth I/O through
 the byte-range layer with the executor's fsync disabled so bench
 keeps its own single-fsync-per-cadence-point census). The
 shared `growth::grow_refcounts` lives here (moved out of the bench op)
 so all three ops can grow the refcount structures during
 copy-on-write, and all three now build COW-capable write states that
 route the crate's `SnapshotShared` / `SnapshotSharedL2Table` COW
 steps through this executor.
- **operations/info/** - Format detection operation
- **operations/copy/** - File copy operation
- **operations/check/** - Image integrity validation operation (with
 optional `--chain` backing chain validation, and optional in-place
 qcow2 repair via `--repair[=leaks|all]`: the safe `leaks` tier
 reclaims unreferenced clusters, the lossy `all` tier rebuilds
 refcounts and reconciles COPIED flags under a crash-safe
 `corrupt`-bit write ordering — set bit → correct refcounts →
 reconcile COPIED → clear bit, each fsync-separated — reusing the
 `crates/check` planner crate and `crates/snapshot`'s refcount
 mutators; refuses on snapshotted/compressed/corrupt images)
- **operations/compare/** - Image comparison operation (format-aware virtual
 content comparison between two images, supporting raw, QCOW2, VMDK,
 VHD, and VHDX inputs including compressed clusters, backing chain
 flattening, and LUKS-in-QCOW2 decryption via `--luks-passphrase`)
- **operations/convert/** - Image conversion operation (any input to raw,
 QCOW2 v3, VMDK, VHD, or VHDX output, with backing chain flattening
 and compressed cluster decompression). Scratch memory layout is computed
 at runtime via `ScratchLayout` based on output cluster size, enabling
 QCOW2 output with cluster sizes from 512B to 2MB. Three conceptual
 buffers (header, L2 table, refcount block) share a single multipurpose
 buffer since they are used in non-overlapping phases. QCOW2 writer
 uses linear cluster allocation with OFLAG_COPIED, 16-bit refcounts,
 and iterative convergence for refcount metadata sizing. Sparse output
 is the default (skip zero-filled clusters, matching `qemu-img convert`);
 use `--no-skip-zeros` for dense output. Optional compressed output
 (`-c` flag) packs clusters at sector granularity using raw deflate
 (via miniz_oxide), with fallback to uncompressed for incompressible
 data. VMDK writer emits monolithicSparse, streamOptimized, or
 monolithicFlat output (via `--subformat monolithicFlat`) with
 configurable grain size (4KB-64KB via `--grain-size`, default
 64KB) for sparse/streamOptimized. VHD writer emits dynamic VHD with configurable block size
 (512KB+ via `--block-size`, default 2MB), sector bitmaps, and BAT
 rewriting (blocks aligned to output sector size with carry-buffer
 assembly to handle bitmap+data spanning sector boundaries). VHDX
 writer emits dynamic VHDX with configurable block size (1MB-256MB
 via `--block-size`, default 32MB), 1MB-aligned structures, CRC-32C
 checksums, and BAT rewriting.
- **operations/measure/** - Image-size measurement operation. Predicts
 `required` (sparse, holes skipped) and `fully-allocated` (every
 cluster/grain/block written) byte counts for a target output format.
 Supported targets: raw, qcow2, vmdk, vpc (VHD), vhdx. For raw and
 qcow2 targets the host CLI's output (human and `--output=json`)
 matches `qemu-img measure` byte-for-byte; vmdk, vpc, and vhdx are
 instar-only because `qemu-img measure` does not support them. CLI
 flags mirror qemu-img (`--size SIZE | FILENAME`, `-O target-format`,
 `-f source-format`, `--output {human,json}`) plus per-target options
 as individual flags (`--cluster-size`, `--refcount-bits`,
 `--extended-l2`, `--compat`, `--preallocation`, `--subformat`,
 `--grain-size`, `--block-size`). Accepts both individual flags and
 `-o key=value,...` (qemu-img parity); `-o` values override individual
 flags when both are given.
 Single-source-device only; backing-chain composition and VMDK
 monolithicFlat sources are deferred. Integration tests in
 `tests/test_measure.py` cross-validate `instar measure` against
 the `qemu-img measure` baselines in
 `instar-testdata/expected-outputs/measure-*` for every safe-tier
 image and every curated `--size` case, plus round-trip the
 vmdk / vpc / vhdx outputs through `instar convert` to verify the
 predicted size bounds. Known scanner-divergence cases (raw
 SEEK_HOLE detection, qcow2/vhdx/vmdk overcounts on some real-world
 images, VHD CHS rounding) are skipped with documented reasons
 pending follow-up work.
- **operations/create/** - Empty-image creation operation. Reads a
 `CreateConfig` (target format, virtual size, per-format options,
 optional backing reference) from `OPERATION_CONFIG_ADDR`, optionally
 recovers the virtual size from a backing image's header on input
 device 0, calls the matching `crates/create::plan_*` to build a
 `MetadataPlan`, and writes every entry to the output device via
 `write_output_sector`. Backing-file lookup supports raw, qcow2,
 vmdk, vhd, and vhdx source headers (the vhdx path goes
 via `vhdx::VhdxState::init`'s metadata-region walk). When the
 target and backing are both vmdk, the guest also reads the
 parent's descriptor via `vmdk::read_and_parse_descriptor` and
 plumbs the real `parentCID` into the new image's descriptor
 (no longer the phase-1d deadbeef sentinel). The host CLI
 (`run_create` in `src/vmm/src/main.rs`, wired) handles
 the raw target entirely host-side via open + ftruncate +
 optional posix_fallocate; for every other format it opens the
 output as a writable virtio device, optionally attaches the
 backing file as input device 0, populates `CreateConfig`, and
 launches `create.bin`. Result rendering supports human
 ("Created:..."), JSON (`--output=json`), and quiet (`-q`)
 modes. The qemu-img-style
 `-o KEY=VAL,...` parser (`parse_create_o_options` +
 `apply_create_overrides` in `src/vmm/src/main.rs`) so the
 full per-format option matrix is reachable via either
 individual `--flag` forms or qemu-img-compatible `-o`
 syntax; `-o` wins on conflict. Two further error codes exist —
 `ERROR_BACKING_FORMAT_UNSUPPORTED` (recognised format but
 size extraction not implemented) and
 `ERROR_BACKING_SIZE_TOO_LARGE` (pre-flight ceiling check
 surfaces a clearer "try a larger cluster size" hint instead
 of plan_*'s generic `InvalidVirtualSize`). There are also
 preallocation modes for raw and qcow2: for qcow2,
 `Preallocation::{Metadata,Falloc,Full}` (any non-Off mode)
 extends the `qcow2::create::Qcow2Layout` to cover L2 tables
 and a data region, populates L1 entries with L2 offsets
 (each with `OFLAG_COPIED`), and marks every used cluster
 (header + L1 + reftable + refblocks + L2 + data) refcount=1.
 The L2 tables are emitted by the guest *outside* the
 `MetadataPlan` (via a reusable single-cluster scratch slot)
 because they can total far more than
 `GUEST_CREATE_SCRATCH_LIMIT` (128 MiB at 1 TiB virtual with
 64 KiB clusters); the plan's `minimum_file_size` carries
 the total file size so the guest also writes a final
 trailing zero sector to extend the file. `Falloc` and
 `Full` lay out the same metadata as `Metadata`; the host's
 `apply_preallocation` helper (`src/vmm/src/main.rs`) layers
 `posix_fallocate` or a `fill_zeros` pass (tries
 `fallocate(FALLOC_FL_ZERO_RANGE)` first, falls back to a
 `pwrite` loop with a 64 KiB zero buffer) over the data
 region. Raw also gains the same `full` zero-fill path via
 `fill_zeros(fd, 0, virtual_size)`. Non-qcow2 sparse formats
 (vmdk / vpc / vhdx) reject non-`off` preallocation with a
 "future work" pointer — each format would need its own
 BAT-population pattern. The host enforces
 `--sector-size=512` because the `crates/create` MetadataPlan
 entries are 512-byte aligned but not always to larger sector
 sizes — relaxing this needs a planner-side change to emit
 coalesced sector-sized writes; tracked in PLAN-create.md's
 Future-work section. The binary builds at ~36 KiB / 384 KiB
 and is excluded from `cargo test --workspace` like the
 other `no_main` operation binaries. Integration tests in
 `tests/test_create.py` cross-validate the create writer on
 three surfaces: per-`(target, case)` comparison via
 `qemu-img info` against the recorded baselines
 (the `create-info-json` profile matching the host's
 qemu-img, whose files are named `<target>-<case>`);
 runtime cross-validation creating the same image twice
 (instar + system qemu-img) and comparing via `instar info`;
 and full-matrix `instar check` round-trip for writer/reader
 self-consistency. The normalisation filter in
 `tests/helpers/info_json.py` strips the divergence whitelist
 (filename, actual-size, vmdk cid + parent-cid, vhdx log-size,
 the wrapping-file physical size, cache hints) before
 comparison; remaining writer divergences (qcow2 compat
 hardcode, zstd accept-ignore, vhdx default block_size, vhd
 CHS-rounded virtual_size) are documented as per-case skips
 rather than whitelist extensions so each gap stays visible.
- **operations/resize/** - In-place virtual-size mutation
 operation. Reads a `ResizeConfig` (target format, current and
 new virtual sizes, current file size, per-format hints from the
 existing header, preallocation mode, `--shrink` flag) from
 `OPERATION_CONFIG_ADDR`, reads sector 0 to confirm the format,
 walks the existing header / L1 / refcount / BAT / descriptor
 via the matching parser crate, calls the matching
 `crates/resize::plan_resize_*` to build a `ResizePlan` of up
 to 128 `ResizePatch` entries (`Write` / `Append` / `ZeroFill`),
 then applies each patch via `write_output_sector` plus the
 new phase-7 `read_output_sector` call-table primitive (the
 resize op is the first reader of the output device — future
 in-place operations like `rebase` / `commit` will reuse it).
 Per-format support: raw is host-only (`open(O_RDWR) +
 ftruncate` plus optional preallocation post-pass; no guest
 launch); qcow2 grows and shrinks (L1 + refcount-table
 extension via `qcow2::plan_grow`, L2 walk + cluster discard
 via `qcow2::plan_shrink`); vmdk monolithicSparse grows
 (sparse extent header rewrite + descriptor update + GD
 relocate via `vmdk::plan_grow`); vhd dynamic + fixed grow
 (BAT extension + footer + dynamic-header rewrite); vhdx
 dynamic grow (two-header sequence-number protocol +
 metadata `VirtualDiskSize` update + BAT extension). vmdk /
 vhd / vhdx shrink is rejected (`UnsupportedShrink`). The
 host CLI (`run_resize` / `run_resize_raw` / `run_resize_nonraw`
 in `src/vmm/src/main.rs`, wired) parses the
 qemu-img-compatible `[+-]SIZE` end-spec grammar
 (`parse_resize_size`), opens the output `O_RDWR` (the same
 file is both input and output — the guest reads via
 `read_output_sector` and writes via `write_output_sector` to
 the device at slot 1; the stub-input-at-slot-0 pattern
 satisfies core's unconditional input-device probe, mirroring
 `run_create_nonraw`), launches `resize.bin`, and applies
 the phase-9 preallocation post-pass via the shared op-agnostic
 `apply_preallocation` helper (`falloc` ⇒ `posix_fallocate` on
 the newly-appended file region; `full` ⇒ `fill_zeros`
 on the same range). Deliberate divergence from qemu: instar
 preallocates only the appended region, not the entire data
 region of the new virtual size; full parity is queued under
 Future work. `--preallocation=falloc|full` combined with
 shrink is rejected for clarity; `--preallocation=metadata`
 on raw is rejected (raw has no metadata to populate); qcow2
 `metadata` preallocation is rejected by the planner
 (`PreallocationUnsupported`, deferred). Output
 rendering supports human (`Image resized.`, matches
 qemu byte-for-byte), `--output=json` (filename / format /
 action / old & new virtual sizes / new file size), and
 `-q` quiet. Integration tests in `tests/test_resize.py`
 cover six surfaces — schema-drift tripwire, cross-version
 baseline matrix (qcow2 + raw), live cross-validation, full-
 matrix round-trip check, internal consistency for
 vmdk/vpc/vhdx (the formats qemu rejects), and targeted
 error-path tests — totalling 114 tests (83 active +
 31 documented skips). Coverage and differential fuzz live in
 `src/fuzz/fuzz_targets/fuzz_resize_planners.rs` and
 `scripts/differential-fuzz.py`'s `op_resize`. The binary
 builds at ~73 KiB / 384 KiB.
- **operations/map/** - Allocation-map operation. Reads a
 `MapConfig` (sector_size, input_device_count, start_offset,
 max_length window) from `OPERATION_CONFIG_ADDR`, detects the
 source format on input device 0, refuses sources with chain
 composition (qcow2 backing-file references, vhd differencing
 disks; vhdx differencing is already rejected by
 `VhdxState::init`; vmdk multi-extent layouts fail the
 binary-header parse naturally), and dispatches to the matching
 per-format `<Format>State::map_extents` walker from the PLAN-m workap. Streams one `MapExtentRecord` per coalesced extent
 through the call table's `send_map_extent` function pointer,
 followed by a `MapResult` summary through `send_map_result`.
 The emit closure clips each extent against the configured
 window (with file-offset adjustment for front-trimmed Data
 extents) and signals walker abort once the window is
 exhausted. Single-image v1; chain composition is a follow-up.
 Binary builds at ~28 KiB / 384 KiB (7%). Host CLI (the PLAN-m workap) wires `instar map [-f FMT] [--output={human,json}]
 [--start-offset=OFFSET] [--max-length=LEN] [--sector-size=N]
 FILENAME`: `run_map` in `src/vmm/src/main.rs` parses args
 (refusing `--image-opts`, VMDK monolithicFlat sources via
 `peek_is_vmdk_descriptor`, and `--start-offset >= file_size`
 on the host before launching the guest), writes `MapConfig`
 per-field at `OPERATION_CONFIG_ADDR`, attaches the source
 read-only as input device 0, and runs the vCPU loop. The PLAN-m workap ships the streaming `MapRenderer<'a, W: Write>`
 that writes each extent to stdout (via a `BufWriter` over
 `stdout().lock()`) as the `MapExtentMessage` arrives in the
 vCPU loop; host memory stays O(1) regardless of how
 fragmented the source is. Human and JSON output match
 `qemu-img map` byte-for-byte modulo the divergences
 documented in `docs/quirks.md` (raw `SEEK_HOLE` not
 implemented, qcow2 compressed clusters reported as
 `compressed: false`, VHDX partially-present treated as data,
 no backing-chain depth in v1). BrokenPipe on stdout (user
 piped into `head`) short-circuits cleanly with exit 0.
 Integration tests in `tests/test_map.py` cross-validate
 `instar map` against the `qemu-img map` baselines in
 `instar-testdata/expected-outputs/map-*` for every safe-tier
 image, plus in-test fixtures for window-filter behaviour,
 host-side error paths (`--image-opts` refusal, chain image
 refusal, invalid sector size), and a divergence-regression
 suite that catches accidental fixes to known instar-vs-
 qemu-img gaps so `KNOWN_MAP_DIVERGENCES` doesn't go stale.
 Current baseline: 95 active tests + 91 documented skips.
- **operations/snapshot/** - Internal-snapshot operation
 (PLAN-snapshot, qcow2-only like `qemu-img snapshot`). Reads a
 `SnapshotConfig` (mode discriminator, argument bytes, flags,
 and for create the host-stamped `date_sec`/`date_nsec`) from
 `OPERATION_CONFIG_ADDR`, opens the image RW as input device 0,
 and dispatches on mode. MODE_LIST streams one
 `SnapshotEntryRecord` per table entry via the qcow2 crate's
 `for_each_snapshot_entry` (no in-memory cap; one entry
 resident at a time) followed by a `SnapshotResult` terminator;
 the host renderer produces byte-identical
 `qemu-img snapshot -l` output (modern ≥9.0 layout, local-time
 DATE column, byte-measured ID/TAG padding) or the
 `--output=json` QMP-keyed extension. The mutating modes
 (MODE_CREATE / MODE_DELETE / MODE_APPLY) compose the
 `src/crates/snapshot/` planner primitives — two-pass
 dry-run-then-apply refcount mutators, the COPIED-flag walker,
 the contiguous-cluster allocator, and the table
 serialisation/compaction helpers — into per-mode
 `fsync_input`-separated write groups with a single commit
 point each (create/delete: the 12-byte header write at offset
 60; apply: the raw L1 overwrite). Delete matches by name only;
 apply and `convert --snapshot` match ID-then-name in two full
 passes (qemu's asymmetry — docs/quirks.md). Uniform feature
 gates refuse `refcount_bits != 16`, compressed clusters,
 encryption, external data files, bitmaps, and dirty images;
 v1 caps the table at 16 snapshots and never grows the
 refcount structures. Post-op images are bit-for-bit identical
 to qemu-img's under `file.discard=ignore` (see
 docs/qcow2/qcow2-snapshots.md for the write orderings and
 docs/snapshot.md for the user reference). Binary builds at
 ~55 KiB / 384 KiB. Verification: seven shell harnesses
 (`tools/snapshot-*.sh`, 241 assertions, `make
 snapshot-harnesses`, run in CI by functional-tests);
 `tests/test_snapshot.py` adds 94 tests covering
 the five snapshot families: list-matrix (12 images, TZ=UTC,
 profile-resolved), JSON goldens with structural cross-check
 and QMP-key schema pin, mutation round-trips
 (create/delete/apply with post-op qemu-img check), error
 paths and qcow2-only enforcement, and empty-table behaviour
 (JSON goldens live in `tests/golden/snapshot-list/`); two
 coverage-guided fuzz targets (`fuzz_snapshot_parse`,
 `fuzz_snapshot_refcount`); and the differential fuzzer's
 `op_snapshot` chain (byte-identity after every element).
- **operations/amend/** - In-place qcow2 header amendment operation
 (PLAN-amend, qcow2-only). Reads an `AmendConfig` (target compat
 version and/or lazy_refcounts flag) from `OPERATION_CONFIG_ADDR`,
 opens the image RW as the output device, reads the existing header
 to determine the current version and feature state, runs the
 `crates/amend` planner to derive a `AmendPlan` (a handful of
 byte-range patches to the header cluster), and applies them via
 `write_output_sector` — only the header cluster is rewritten; no
 cluster or refcount data is touched. v1 gates: v3→v2 downgrade
 refused if the image carries a v3-only incompatible feature
 (dirty, corrupt, external data, compression type, extended L2) or
 uses `refcount_bits != 16`; `lazy_refcounts=on` requires v3;
 header-extension relocation across the version change is
 unsupported. Needs `/dev/kvm` (launches a guest VMM). See
 [docs/amend.md](/components/instar/amend/) for the full user reference.
- **operations/dd/** - Windowed block-copy operation (PLAN-dd,
 qemu-img dd compatible). Implemented host-side in `run_dd`
 (`src/vmm/src/main.rs`): parses `name=value` operands (`if=`,
 `of=`, `bs=`, `count=`, `skip=`) and the `-O` output-format flag
 (default **raw**, not the input format), computes the input byte
 window via `crates/dd::compute_dd_window` (count-then-skip
 semantics: `count` clamps down, `skip` subtracts from the front,
 skip-past-EOF ⇒ empty output with exit 0), then launches the
 existing `convert.bin` guest with a windowed `ConvertConfig`
 (input byte-window + dense output). The new `crates/dd` crate
 provides the pure window-math helper used by both the host CLI
 and tests. The structured writers (qcow2, vmdk, vhd, vhdx) were
 hardened during this phase via `qcow2::read_chain_virtual_range`
 to correctly fill output grains/blocks that span multiple input
 qcow2 clusters (fixing a pre-existing sub-cluster data-loss bug
 in `convert`). Output is byte- and size-identical to `qemu-img
 dd` for all five output formats (raw, qcow2, vmdk, vpc, vhdx).
 Known divergences: vhdx default block size (32 MiB vs qemu's 8
 MiB for small images), count=0 vmdk/vhdx edge cases. See
 [docs/dd.md](/components/instar/dd/) for the full user reference.
- **operations/bitmap/** - qcow2 persistent-dirty-bitmap management
 operation (PLAN-bitmap, qcow2 v3-only). The host side (`run_bitmap`
 in `src/vmm/src/main.rs`) validates the CLI surface (the repeatable
 CLI-order actions `--add`/`--remove`/`--clear`/`--enable`/
 `--disable`/`--merge`, the `-g` granularity, rejected qemu-only
 flags), pre-probes the image, and hands a `BitmapConfig` to the
 guest op, which mutates the image in place. The pure `no_std`
 `crates/bitmap` planner provides the bitmap directory/table/action/
 merge logic, reusing the snapshot refcount mutators to allocate and
 free bitmap-table clusters. The guest applies each action under the
 crash-safe **autoclear** dance (clearing the header's
 `bitmaps` autoclear bit while the extension is inconsistent and
 restoring it once the write settles) so a crash mid-update leaves
 the image safe rather than corrupt. Needs `/dev/kvm` (launches a
 guest VMM). The ABI appends one call-table callback
 (`send_bitmap_result`), bumping `CallTable::VERSION` from 18 to 19
 (same append-at-end discipline as amend's 17→18). Coverage:
 `tests/test_bitmap.py` integration parity against `qemu-img
 bitmap`, cross-version baselines, and fuzzing. See
 [docs/bitmap.md](/components/instar/bitmap/).
- **operations/bench/** - I/O benchmark operation (PLAN-bench), the
 sandboxed equivalent of `qemu-img bench`. Measures instar's own
 end-to-end sandboxed path (guest format layer → virtio-block →
 ioeventfd → host I/O thread → file I/O) rather than qemu's block
 layer over the page cache; running both tools against the same
 image and arguments is the reproducible sandbox-overhead
 measurement (see [docs/bench.md](/components/instar/bench/)). The host side
 validates the full option surface (echoed-but-unobeyed `-d`,
 buffer-size cap, cache/aio/image-opts postures) before launching
 the guest with a `BenchConfig`; the guest driver is synchronous
 and single-buffer in v1 (`effective-depth` always `1`), submitting
 each scheduled request in turn and timing the run between the
 `send_bench_start` marker (emitted once setup completes) and the
 terminal `send_bench_result`. Reads all five formats; write tests
 (`-w`) are supported on raw and qcow2 only (including qcow2
 overlays); a mid-run crash leaves at worst a repairable leak. Since
 the phase-6 migration (PLAN-qcow2-write-infrastructure), the qcow2
 `-w` allocate-on-write path runs on the shared `crates/qcow2-write`
 planner and `crates/qcow2-write-exec` executor — bench is the third
 consumer after commit and rebase — staging metadata and writing it
 back refcounts-last at each flush epoch. qcow2 write setup
 preemptively grows the image's refcount structures to the
 schedule's worst-case coverage before the timing bracket opens (new
 refblocks at the file end; refcount-table relocation with an
 fsync-ordered header flip — `PLAN-bench-refcount-growth`); the pure
 growth planner moved into `crates/qcow2-write`'s `growth` module, though growth execution stays op-side. bench keeps its own
 fsync census (the executor's fsync is disabled; the op issues one
 `fsync_input(0)` per `--flush-interval` cadence point). The pure
 `no_std` `crates/bench` crate provides the request-schedule math
 (and `worst_case_touched`, which stays BenchParams-coupled) shared
 by the guest, host CLI and tests. `bench.bin` builds at ~173 KiB of
 the 768 KiB operation-region budget. The ABI appends two
 call-table callbacks (`send_bench_start`, `send_bench_result`),
 bumping `CallTable::VERSION` from 19 to 20. Coverage:
 `tests/test_bench.py` (76 tests), the `fuzz_bench_schedule`
 coverage fuzzer, and the differential fuzzer's `op_bench` arm. See
 [docs/bench.md](/components/instar/bench/).
- **shared/** - Shared library code between components (call table, configs,
 format detection, memory layout constants, shared utilities,
 `bump_allocator!` macro for operations needing heap allocation,
 centralized byte-order helpers: `be_u16/32/64`, `le_u16/32/64`,
 `write_be_u16/32/64`, `write_le_u16/32/64`). Also defines
 `AllocationSummary`, the common result type produced by each format
 crate's `scan_allocation` function and consumed by the `measure`
 subcommand. `MeasureConfig` and `MeasureResult` structs carry
 options and results across OPERATION_CONFIG_ADDR and the
 `send_measure_result` CallTable callback (CallTable VERSION 14).
 The PLAN-c workreate.md` adds `CreateConfig` / `CreateResult` /
 `GUEST_CREATE_SCRATCH_LIMIT` here and a new `send_create_result`
 CallTable function pointer (appended at the end of the struct so
 existing operation binaries keep working unchanged). The PLAN-r workesize.md` adds `ResizeConfig` / `ResizeResult` plus two
 more CallTable function pointers: `read_output_sector` (lets a
 guest read from the same device it writes to — the first
 in-place-mutation primitive, reusable by `rebase` / `commit`
 / snapshot-delete) and `send_resize_result`. Same
 append-at-end discipline. The PLAN-s worknapshot.md` adds
 `SnapshotConfig` (magic `b"SNAP"`, carrying the mode, the
 snapshot name/needle argument, and the create-mode
 `date_sec`/`date_nsec` wall-clock fields) / `SnapshotResult` /
 the `SnapshotEntryRecord` wire record, and three more CallTable
 entries — `send_snapshot_entry` (streams one listed snapshot
 per call), `send_snapshot_result`, and `fsync_input` (the
 guest-visible write barrier the mutating modes use between
 write groups) — bumping CallTable VERSION from 16 to 17, same
 append-at-end discipline. `PLAN-amend.md` and `PLAN-bitmap.md`
 each append one more entry (`send_amend_result`,
 `send_bitmap_result`), bumping VERSION 17→18→19; `PLAN-bench.md`
 appends two — `send_bench_start` (the timing-bracket start marker)
 and `send_bench_result` (the terminal result) — bumping VERSION
 from 19 to 20, same append-at-end discipline throughout.

**Chain validation in check (`--chain`):**
The check operation supports an optional `--chain` flag that uses the host-side
chain discovery infrastructure (same as `instar info --chain`) to discover the
full backing chain, then sets up each image as a separate virtio-block device
in the KVM guest. The guest validates each backing image for format consistency,
non-zero virtual size, and QCOW2 header integrity (magic, version,
cluster_bits, L1/refcount table bounds, corrupt feature flag). Backing file
paths are validated against the security allowlist before being opened. Chain
errors are reported separately from primary image errors.

The rust-vmm project provides crates that reduce implementation effort by 70%+:
- `kvm-ioctls` - Safe KVM API wrappers
- `kvm-bindings` - KVM bindings
- `vm-memory` - Guest memory abstraction
- `virtio-queue` - Virtqueue implementation
- `virtio-bindings` - Virtio protocol bindings

## Guest Memory Map

The guest runs in 32 MiB of physical memory (`GUEST_MEM_SIZE = 0x2000000`).
Constants are defined in `src/shared/src/lib.rs` with compile-time overlap
checks. The core and operation regions, and the data pages that follow
them, were lifted on 2026-07-06 (commit `3a5e1e2`) to give both budgets
headroom after `core.bin` reached 94% of its previous 72 KiB limit
following the bench ABI additions; nothing at or above the virtqueue
region (`VQ_BASE_START`) moved.

```
Address         Size    Region
──────────────  ──────  ─────────────────────────────────────────
0x0000_1000             GDT
0x0000_2000             Page tables
0x0001_0000    128 KiB  core.bin (guest entry point)
0x0003_0000    768 KiB  Operation binary (whichever op is loaded)
0x000F_0000      4 KiB  Call table
0x000F_1000      4 KiB  Operation config
0x000F_2000      1 KiB  Chain config
0x000F_3000      4 KiB  VMM params
0x000F_4000     48 KiB  ── guard gap ──
0x0010_0000      1 MiB  Virtqueue memory (16 devices × 64 KiB)
0x0020_0000     64 KiB  DMA pool
0x0030_0000   12.9 MiB  Scratch memory (temporary bitmaps/buffers)
0x00FF_0000     64 KiB  ── guard gap ──
0x0100_0000      4 MiB  Stack (grows down from STACK_TOP)
0x0140_0000   12.0 MiB  (unused)
0x0200_0000             End of guest memory
```

`GUEST_CODE_BASE`/core loads at `0x10000` and may extend to
`OPERATION_LOAD_ADDR` (`0x30000`, 128 KiB max); the operation binary
loads at `0x30000` and may extend to `CALL_TABLE_ADDR` (`0xF0000`,
768 KiB max). `scripts/check-binary-sizes.sh` enforces both budgets
against each binary's `.bss`-inclusive ELF memory extent, not just the
flat `.bin` file size. The four data pages (call table, operation
config, chain config, VMM params) occupy `[0xF0000, 0xF4000)`,
followed by a 48 KiB guard gap up to `VQ_BASE_START` (`0x100000`).
Virtqueue memory and everything above it (DMA pool, scratch, the
64 KiB pre-stack guard gap, and the stack) is unchanged by the lift.

See [docs/chain-config.md](/components/instar/chain-config/) for the chain config
structure layout and VMM-to-guest data flow.

