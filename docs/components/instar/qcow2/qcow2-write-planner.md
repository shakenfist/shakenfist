# QCOW2 Write Planner and Executor

Maintainer reference for the shared qcow2 write infrastructure: how
`instar` mutates an existing qcow2 image in place — allocating clusters,
copying snapshot-shared clusters before modifying them, growing the
refcount structures, and writing metadata back in a crash-safe order.

This is a **current-state implementation-notes** document: it describes
how the code works today, keyed to the crate API. It is not a changelog;
the phase-by-phase history lives in the plan files cited in the
[provenance footnote](#provenance). For the read-side format details this
document builds on, see [qcow2-format.md](/components/instar/qcow2/qcow2-format/),
[qcow2-l1l2-tables.md](/components/instar/qcow2/qcow2-l1l2-tables/),
[qcow2-refcount.md](/components/instar/qcow2/qcow2-refcount/) and
[qcow2-snapshots.md](/components/instar/qcow2/qcow2-snapshots/).

## The problem and the crate boundary

qemu-img mutates a qcow2 image by allocating clusters, patching L1/L2
tables, adjusting refcounts and (when snapshots share clusters) copying
before writing. `instar` performs the same mutations, but from inside a
bare-metal KVM guest whose only I/O primitive is a strictly
sector-addressed call table and whose panic handler is `loop {}` —
refusing beats panicking. Three operations need this: `commit`, `rebase`
safe mode, and `bench -w`. Rather than each re-deriving the allocation
and copy-on-write logic, the machinery lives in two shared `no_std`
crates:

- **`crates/qcow2-write`** — the **planner**. Pure, I/O-free and
  address-free: it turns "write N bytes at virtual offset X into this
  qcow2" into a typed *step program*. It performs no I/O and never sees
  a guest address; it reads image metadata through caller-lent staging
  slices and names buffers symbolically. This is what makes it
  exhaustively host-unit-testable and fuzzable.
- **`crates/qcow2-write-exec`** — the **executor**. The literal
  interpreter of the step program against real devices: it owns the
  `RegionId` → slice mapping, the `TargetDevice` → call-table mapping,
  the byte-range layer over the sector-addressed call table, and the
  barrier/fsync policy. It contains zero planning, classification or
  allocation logic.

Refcount growth is split the same way: `qcow2_write::growth` decides
*how much* to grow (pure arithmetic) and `qcow2_write_exec::growth`
*performs* the growth against a device.

### The three consumers

Each op supplies the device its writes target and the scratch-carved
staging buffers, then drives the same plan/execute loop. All three build
a **copy-on-write** state (`new_state_cow`), so snapshot-shared clusters
are copied rather than refused.

| Op | Write device | Staged image | Snapshot-view contract |
|----|--------------|--------------|------------------------|
| `commit` | `Output` (the backing) | backing metadata | backing snapshots **preserved** bit-identically |
| `rebase` safe mode | `Output` (the overlay) | overlay metadata | active view only; snapshots **read through the new backing** |
| `bench -w` | `Input0` (its own image) | that image's metadata | snapshots **preserved** like commit |

The per-op behaviour is documented in [commit.md](/components/instar/commit/),
[rebase.md](/components/instar/rebase/) and [bench.md](/components/instar/bench/); this reference
covers the shared path they all funnel through.

## The step-program ABI

A plan is a sequence of `Step` values — plain `#[repr(C)]` Rust data, not
a serialized byte encoding, because the planner and executor share a
binary. `Step` is const-asserted at ≤ `STEP_SIZE_LIMIT` (48) bytes, so a
64 KiB step buffer holds 1300+ steps.

### `StepKind`

Each step carries a `StepKind` tag selecting which of its fields
(`device`, `region`, `region_offset`, `disk_offset`, `len`, `value`)
apply:

| `StepKind` | Meaning |
|------------|---------|
| `ReadCluster` | Read `len` bytes from `device`@`disk_offset` into `region`@`region_offset` (COW/RMW pre-image reads into `Bounce`). |
| `WriteRange` | Write `len` bytes from `region`@`region_offset` to `device`@`disk_offset`. |
| `ZeroRange` | Write `len` zero bytes to `device`@`disk_offset` (no region). |
| `FillRange` | Write `len` bytes of the fill byte (low 8 bits of `value`) to `device`@`disk_offset`. Lowers `DataSource::Fill` so patterned writes never bounce through a staged buffer. |
| `PatchEntryU64` | Store `value` as a big-endian u64 at `region`@`region_offset` — an in-memory L1/L2 pointer patch that reaches disk only via a later write-back. |
| `LoadCluster` | Load one whole cluster from `device`@`disk_offset` into an L2 window slot (`len == cluster_size`). |
| `WritebackCluster` | Write one whole cluster from an L2 window slot to `device`@`disk_offset`. |
| `ZeroRegion` | Zero `len` bytes *within* `region` (no device I/O) — initialises a freshly allocated L2 window slot before entries are patched into it. |
| `Barrier { class }` | Ordering/durability boundary; consumes no other fields. |

Unused numeric fields are zero and an unused `region` is `Bounce`, so
emitted programs are bit-deterministic (the window-invariance property
compares whole `Step` values).

### `RegionId` and `TargetDevice`

The planner is address-free: a step names a staged buffer by `RegionId`
(`L1`, `L2Slot(u16)`, `RefcountTable`, `Refblocks`, `Bounce`,
`CallerData`) and a device by `TargetDevice` (`Input0`, `Output`). The
executor's `Regions` struct maps each `RegionId` to a caller-carved slice
(bounds-checked per access; `L2Slot(i)` resolves against slot `i`'s
cluster-sized window, never the whole staging array), and `CallTableIo`
maps each `TargetDevice` to its call-table entry points (`Input0` →
`read/write_input_sector(0, …)` + `fsync_input(0)`; `Output` →
`read/write_output_sector`, with **no** fsync primitive). Host unit tests
substitute a Vec-backed mock for both.

### Windowed emission and `BufFull` resume

A full-image program is never materialised — commit's envelope can reach
~2M clusters. Instead `plan_write` / `plan_flush` emit into a
caller-provided `StepBuf` (a `&mut [Step]` plus an emitted cursor). When
the buffer fills — **or** when the planner emits a `LoadCluster` whose
loaded bytes it must read before it can continue — the call returns
`WriteError::BufFull`. This is the normal **resume signal**, not a
failure: the caller executes the emitted window, calls `StepBuf::reset`,
and calls the planner again *with the same arguments*. `WriteState` holds
the resume point, and the concatenation of windows is byte-identical to
what one unbounded buffer would have produced (window-invariance). The
caller **must** execute each window before the next planner call, because
classification reads image metadata through the staging slices the
previous window mutated.

The executor's counterpart is `execute(steps, regions, devices)`, which
applies each step literally in emission order and stops at the first
failure with the failing step index and a typed `ExecError` (steps are
not transactional — the call table gives no rollback).

## The write envelope and classification

### Envelope gates

Before any step can exist, `new_state` / `new_state_cow` run
`check_envelope_with`, a pure header predicate (no I/O). Holding a
`WriteState` is proof the image passed the envelope. The gates, in check
order, with their stable `Gate::code()`:

| Gate | Code | Refuses when |
|------|------|--------------|
| `UnsupportedVersion` | 8 | `version` is not 2 or 3 (defends direct struct construction). |
| `RefcountWidth` | 1 | `refcount_bits != 16` (v1 supports 16-bit refcounts only). |
| `UnknownIncompatible` | 2 | the `INCOMPAT_COMPRESSION` (zstd) bit or any unknown incompatible bit is set. |
| `ExtendedL2` | 3 | the extended-L2 bit is set (the planner assumes 8-byte L2 entries). |
| `ExternalDataFile` | 4 | the external-data-file bit is set. |
| `Encryption` | 5 | `crypt_method != 0`. |
| `DirtyCorrupt` | 6 | the dirty or corrupt bit is set. |
| `HasSnapshots` | 7 | `nb_snapshots != 0` **and** copy-on-write is not enabled. |
| `InvalidStagingConfig` | 9 | the caller's `StagingConfig` is out of range (a caller bug, not an image property). |

Codes 1–7 deliberately equal bench's historical `wgate` constants.
`check_envelope` is the `allow_snapshots == false` specialisation;
`check_envelope_with(hdr, true)` (used by `new_state_cow`) relaxes **only**
`HasSnapshots` — the caller then undertakes to copy-on-write every
snapshot-shared cluster it touches. Every other gate is unconditional.

One condition is deliberately *not* a header gate: per-cluster zlib
compression carries no header bit, so a compressed cluster is refused at
plan time on encounter (`WriteError::CompressedCluster`).

### Per-cluster classification

For each cluster the write touches, `classify_l2_entry` (and
`validate_l1_entry` for the L2 table itself) maps the staged metadata to
a verdict:

| L2 entry state | Verdict |
|----------------|---------|
| zero (unmapped) | **Unallocated** → allocate a data cluster (fresh L2 first when the L1 slot is empty) |
| `OFLAG_COMPRESSED` set | `CompressedCluster` |
| reserved bits / flags-only / misaligned offset | `UnknownL2Entry` |
| allocated, `OFLAG_COPIED` set, staged refcount 1 | **Owned** → overwrite in place, no metadata change |
| allocated, `OFLAG_COPIED` clear, or staged refcount > 1 | **CowData** (COW) — else `SnapshotShared` refusal |
| allocated, staged refcount 0 | `RefcountInconsistent` |
| allocated, refcount not in the staged set | `RefcountCoverage` |

The `WriteError` families a classification can raise: `SnapshotShared`
(8), `SnapshotSharedL2Table` (9), `UnknownL1Entry` (10), `UnknownL2Entry`
(11), `RefcountInconsistent` (12), `RefcountCoverage` (13),
`NeedsBackingFill` (14). The snapshot-shared refusals are unreachable on
a consistent non-COW image (the `HasSnapshots` gate stops it earlier) and
are kept as defence in depth; under `new_state_cow` they become the C1/C2
COW paths instead.

### The zero-flag target policy

A v3 `QCOW_OFLAG_ZERO` (bit 0) *write target* is classified by its host
offset and refcount, matching qemu exactly (qemu does **not** free the
old host offset):

- **host == 0** → treated as unallocated (allocate fresh / zero-fill).
- **host != 0, refcount 1** → **OwnedZero**: reuse the host cluster in
  place, but the eventual L2 patch clears the zero flag (`host | COPIED`)
  and the uncovered head/tail are zero-filled (the pre-image is logically
  zero). Unlike an unallocated cluster this never raises
  `NeedsBackingFill`, because the pre-image is zero regardless of any
  backing chain.
- **host != 0, refcount > 1** → data-cluster COW with an **all-zero**
  pre-image (or `SnapshotShared` when COW is off).

Reserved bits outside `{OFLAG_COPIED, offset mask, QCOW_OFLAG_ZERO}`
still refuse as `UnknownL2Entry`.

## Allocate-on-write

When classification calls for a fresh cluster, the planner emits in
decision-7 order:

1. **L2 table first.** If the covering L1 slot is empty, allocate a fresh
   L2 cluster, emit `ZeroRegion` to zero its window slot, then
   `PatchEntryU64` the L1 to `l2_host | OFLAG_COPIED`. The
   window-invariance rule places the slot init *before* the L1 patch in
   the stream; on disk the ordering holds because `plan_flush` writes the
   L2 slots back before the L1.
2. **Allocate the data cluster** via the `snapshot` crate's allocator
   (`alloc_cluster_in_refblocks` over an `AllocCursor`), which sets the
   claimed refcount to 1 in place in the single staged refblock copy and
   marks the covering refblock dirty.
3. **Sub-cluster RMW.** Emit a `ZeroRange` for any uncovered head
   (`in_off > 0`) and tail (`in_off + win_len < cluster_size`) around the
   body write, so a partial write leaves a well-defined cluster.
4. **Body write**, then `PatchEntryU64` the L2 entry to
   `data_host | OFLAG_COPIED` (data before the pointer that reaches it).

### EOV clamp and the backing-fill protocol

The uncovered-range fill is **zeros**, which is the correct virtual
pre-image only for an *unallocated* cluster in a **backing-less** image.
On an image *with* a backing file the uncovered remainder's virtual
content comes from the backing chain — a read the pure planner cannot
perform — so a partial allocating write refuses with
`NeedsBackingFill`; the caller (which can read the chain) resubmits a
full cluster.

Coverage is judged against the **effective cluster end**,
`min(cluster_size, virtual_size − cluster_virtual_base)`. On the final
(tail) cluster of an unaligned virtual size, a write from the cluster
base whose coverage reaches `virtual_size` is *full* coverage: bytes
beyond EOV are not virtual content, so the beyond-EOV zero-fill is the
correct pre-image regardless of backing. The refusal fires only for a
genuinely partial write below EOV (including a head gap below EOV on the
tail cluster).

## Copy-on-write

Under a COW-enabled state, a snapshot-shared cluster is copied before
modification. The governing invariant is **`max_rc < 3`**: qemu bumps
every reachable cluster to refcount ≥ 2 at *snapshot-creation* time, so a
shared cluster already sits at rc 2 with `OFLAG_COPIED` clear; any code
path that drove a shared child to rc 3 would make `qemu-img check` dirty.
This is the single most important correctness property and the primary
fuzz oracle.

### Data-cluster COW (C1)

For a shared data cluster `D` (`CowData`): allocate a fresh `D'`,
establish its pre-image, write the body, patch the L2 entry to
`D' | OFLAG_COPIED`, then **decrement `rc(D)`**. The old `D` is never
freed — a snapshot still references it (its post-decrement count is ≥ 1
on any consistent image). Pre-image establishment:

- **full-cluster overwrite** (`in_off == 0 && win_len == cluster_size`):
  none needed, every byte is replaced;
- **zero-flag source**: `ZeroRange` the fresh `D'` (the pre-image is
  logically zero);
- **standard shared source**: `ReadCluster` old `D` into `Bounce`, then
  `WriteRange` `Bounce → D'` — a same-device copy, no backing read.

### L2-table COW (C2)

For a shared L2 table `T` (`validate_l1_entry` → `CowL2`): allocate a
fresh `T'`, `LoadCluster` old `T` into the window slot, re-point the slot
and the L1 entry to `T' | OFLAG_COPIED`, and **decrement `rc(T)`** — with
**no change to any child refcount**. This is the subtlety worth flagging:
the children are already rc ≥ 2 from snapshot creation, so copying
`T → T'` merely redistributes the reference (net child delta 0); a
literal "increment every child" would drive them to rc 3. The children
keep `OFLAG_COPIED` clear, so a subsequent write *through* `T'` into a
child fires the per-child data COW.

After the L2-table COW the planner emits a `BufFull` window boundary so
the executor runs the `LoadCluster` before the resumed call classifies
the (still snapshot-shared) child — **nested COW** (C2 then per-child C1)
falls out of the resume loop rather than being a special case.

### The decrement primitive

`dec_refcount` is the net-new refcount operation the allocator does not
expose (the allocator only ever increments). It reads the covering staged
refblock, decrements through the `snapshot` scalar accessors
(`check_refcount_after_addend(cur, −1, …)`), writes it back in place and
marks it dirty for the refcounts-last flush. A decrement that would go
below zero is an image inconsistency (`RefcountInconsistent`), never a
silent wrap and never a free of a live cluster.

## Refcount growth

Allocation can exhaust the on-disk refcount structures. Rather than grow
mid-run, the ops provision the whole run's worst case once, up front.

### Planning — `qcow2_write::growth::plan_refcount_growth`

Pure saturating `u64` arithmetic over caller geometry and staging caps;
no I/O, no panics. New structures are placed contiguously at the
cluster-aligned current file end:

```text
[ existing file … E ) [ new RT (only if relocating) ) [ new refblocks … )
```

A **coverage-based no-growth short-circuit** returns immediately when the
already-staged refblocks cover `file_end + worst_case_new_clusters`.
Otherwise a **fixed-point iteration** converges on `needed_slots`,
because the new RT clusters and refblocks need refcount coverage
themselves and feed back into the end estimate. The loop is bounded by
`GROWTH_FIXED_POINT_ROUNDS` (8); it converges in ≤ 3 rounds for any real
qcow2 geometry (`entries_per_refblock ≥ 256`). A converged plan exceeding
any staging cap — total slots, staged refblock bytes, staged RT slots, or
the staged RT image in whole clusters — returns `GrowthOverflow`, which
the op maps to its own capacity refusal.

### Execution — `qcow2_write_exec::growth::grow_refcounts`

The imperative twin performs the growth against a device, in write order
that mirrors snapshot-create's grouped fsync discipline: stage the
extended refcount table + new refblocks, refcount every new structure
cluster to 1, flush the dirty refblocks, write the refcount table,
**fsync**, flip the header pointer (relocation only), **fsync**, then
stage-and-persist the old table's free. A crash in the header-flip window
leaves the old table refcounted-but-unreferenced — a repairable leak, the
same benign class as any mid-run crash.

Two invariants carry weight here:

- **#433 — materialize every provisioned refblock.** Every
  RT-referenced refblock is marked dirty before the eager flush, so even
  the refblocks an overwrite-only run leaves at all-zero refcounts are
  written out; an unwritten but RT-referenced refblock would be a dangling
  pointer.
- **The fsync census follows the device's fsync capability.** A
  fsync-capable `Input0` (bench) performs **real** fsyncs — 1 on the
  in-place path, 2 on the relocating path — while an `Output` with no
  fsync primitive degrades every durability fsync to ordering (see the
  crash-ordering contract).

All three write consumers now call the shared growth planner and
executor; growth is dormant (no-growth short-circuit, zero extra writes)
whenever the run's worst case already fits.

## The crash-ordering contract

The emission order encodes a durability contract: **data reaches disk
before the pointer that makes it reachable, and refcounts are flushed
last.** `plan_flush` emits, with `BarrierClass::Durability` barriers at
each boundary (empty groups vanish, adjacent barriers collapse
deterministically over the dirty state, never over the buffer size):

```text
[Barrier] WritebackCluster*   — dirty L2 window slots, ascending
[Barrier] WriteRange(L1)      — the staged L1, if dirty
[Barrier] WriteRange(Refblocks)*  — dirty refblocks, ascending, refcounts LAST
[Barrier]                     — close the epoch
```

Consequently a crash mid-run leaves, at worst, **leaked clusters**
(allocated and reachable, but under-counted in the refcount table —
reported and repaired cleanly by `qemu-img check`) and **never** a
dangling L2 pointer into unallocated space — the same benign artifact
class a qemu-img crash produces.

Barrier strength degrades by device capability: the executor maps
`Durability` to `fsync(device)`, which is a real `fsync_input(0)` on a
fsync-capable `Input0` and **degrades to `Ordering`** (a silent success)
where no fsync primitive exists — which is the reality for commit's and
rebase's `Output` device today. `Ordering` itself is a no-op because all
call-table I/O is synchronous (issue order is completion order). Adding a
`fsync_output` primitive is recorded future work; the write path must not
assume it.

## Verification posture

Two correctness models apply, by phase of the write path:

- **Migration byte-identity** (allocate-on-write, no snapshots). commit,
  rebase safe mode and bench were migrated onto this crate with the
  contract that their output stays byte-for-byte what the hand-rolled
  predecessor produced — verified against pinned qemu-img versions in the
  integration suites.
- **qemu-parity** (copy-on-write, snapshot-bearing). COW is net-new
  behaviour, so the bar is qemu-*parity*, not before/after byte
  identity: `qemu-img check` clean + the active view
  `qemu-img compare`-identical to a qemu twin + a snapshot **read-back
  oracle** (extract each pre-existing snapshot's virtual view, convert to
  raw, sha256, compare to qemu's result for the same op). Byte placement
  of the COW output is explicitly not constrained. This is
  `tests/test_cow_cross_version.py` plus `scripts/cow-soak.py`; see the
  ["Copy-on-write for snapshot-bearing qcow2 images"](/components/instar/quirks/)
  section of the quirks doc.

The planner itself is exercised directly by its own Vec-backed
`sim` harness (feature-gated, off in the guest build) and the
`fuzz_qcow2_write` cargo-fuzz target, which drives `plan_write` /
`plan_flush` through the `BufFull`-resume loop over clean / backing /
shared-data (C1) / nested shared-L2 (C2/C3) / owned-L2 / zero-flag-target
fixtures and, after **every** operation, asserts `max_rc < 3`,
snapshot-shared clusters byte-frozen and never freed, metadata liveness,
no dangling pointer, and — after a clean flush — `OFLAG_COPIED`-implies-
refcount-1.

## Known limitations and future work

- **commit does not byte-empty a snapshot-bearing overlay.** The
  post-commit overlay-clear pass is skipped when the overlay carries
  internal snapshots (zeroing shared active metadata in place would
  corrupt a snapshot's view — issue #423). The committed clusters stay
  mapped in the overlay's active L2 and read identically through the new
  backing; the active view and snapshots are correct, but full
  byte-emptying parity would need an **overlay-side COW-clear primitive**
  the crate does not yet provide.
- **rebase's COW growth is coarsely sized** at `2 × overlay_cluster_count`
  (rebase writes into unowned clusters). The over-provisioned refblocks
  are check-clean via the #433 materialization fix; a tighter bound is
  follow-up work.
- **The growth non-convergence guard is debug-only.** `plan_refcount_growth`'s
  fixed-point loop cannot fail to converge for real geometry; a
  non-converging degenerate input trips a `debug_assert!` and otherwise
  returns `GrowthOverflow` (it never panics or returns an
  under-provisioned plan in release).
- **Byte-parity is a non-goal for COW output** (C11): the correctness
  proof is qemu-parity + the read-back oracle, not image-byte identity,
  because qemu's own COW placement is nondeterministic at small cluster
  sizes.
- **Growth is not yet adopted by the other mutating ops.** `resize`,
  `amend`, `snapshot` and `bitmap` still hand-roll their own refcount
  management; only the three write consumers use the shared growth
  planner/executor so far.

## Provenance

This document distils the current state; the phase-by-phase history and
the empirical pins behind these decisions live in the plan files:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/)
(the master plan, its per-phase Findings and the defect register) and its
per-phase companions (the crate, the commit/rebase/bench
migrations, copy-on-write, refcount growth and fuzzing), plus
[PLAN-bench-refcount-growth.md](/components/instar/plans/PLAN-bench-refcount-growth/)
for the growth algorithm. Source:
[`src/crates/qcow2-write/`](https://github.com/shakenfist/instar/tree/develop/src/crates/qcow2-write) and
[`src/crates/qcow2-write-exec/`](https://github.com/shakenfist/instar/tree/develop/src/crates/qcow2-write-exec).
