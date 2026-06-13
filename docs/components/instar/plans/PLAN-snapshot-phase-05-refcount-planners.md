# PLAN-snapshot phase 05: refcount mutators (pure planner crate)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the most recent
pure-planner crate `src/crates/commit/`, especially
`src/crates/commit/src/qcow2.rs::allocate_backing_cluster_qcow2`
and the `BackingAllocationState` cursor; the refcount-width
handling in `src/crates/resize/src/qcow2.rs::set_refcount` at
line 1898; the `qcow2::OFLAG_COPIED` constant and the L1 /
L2 entry layouts in `src/crates/qcow2/src/lib.rs`; the
classification helpers `classify_qcow2_l2_standard`,
`classify_qcow2_l2_extended`; the read-only `lookup_refcount`
helper at line 4613; the existing `crates/snapshot` workspace
slot — currently absent), and ground your answers in what the
code actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
qcow2 spec details (COPIED-flag invariant, sub-byte refcount
bit ordering, extended-L2 subcluster bitmaps), research as
needed — the qcow2 spec at
`docs/qcow2/qcow2-snapshots.md` and the qemu sources
(`block/qcow2-refcount.c::qcow2_update_snapshot_refcount`,
`block/qcow2.c::qcow2_alloc_clusters`) are the authoritative
references.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 5 of
fourteen — the **riskiest** phase per the master plan's
effort guidance.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1–4 landed everything needed for `instar snapshot -l`
end-to-end: wire ABI, qcow2 streaming parser, guest binary,
host CLI. Phase 5 starts the mutating-mode work by landing
the **pure mutator primitives** that phases 6–8
(create / delete / apply guest binaries and planners) will
compose into per-mode patch lists.

This phase has the highest correctness stakes in the whole
PLAN-snapshot family. The qcow2 refcount + COPIED-flag
invariant is the single biggest source of qcow2 corruption
bugs in qemu's history. Getting the mutators right here means
phases 6–8 inherit correctness for free; getting them wrong
means every later phase ships a corruption bug.

### Architectural shape: a new `src/crates/snapshot/` crate

The master plan's open question 3 recommended *extending*
`src/crates/qcow2/`. Phase 5 **overturns that recommendation**
in favour of a new `src/crates/snapshot/` crate parallel to
`src/crates/commit/` and `src/crates/rebase/`. Reasons:

1. The project's actual convention (verified against
   `src/crates/{commit,rebase,resize,measure,create}/`) is
   **one crate per mutating operation**. The qcow2 crate is
   read-mostly (parsers, lookups, classification); the
   per-operation crates own the format-specific mutation
   primitives.
2. The `commit` crate already demonstrates this pattern with
   `allocate_backing_cluster_qcow2`, `BackingAllocationState`,
   `Qcow2CommitContext`, etc. — pure functions over staged
   slices, no I/O, depends on `qcow2` for type / constant
   access (`L1_OFFSET_MASK`, `OFLAG_COPIED`,
   `classify_qcow2_l2_*`).
3. Putting the snapshot mutators inside the qcow2 crate would
   bloat the read-mostly read-API and force every read-only
   caller (`info`, `convert`, the phase-3 list guest binary)
   to compile against the mutation code paths. The
   `cargo test -p qcow2` surface should stay focused on the
   parser.
4. The crate name `snapshot` is distinct from the operation
   binary crate `snapshot-op` (the phase-3 binary's
   `Cargo.toml`), mirroring `commit` vs `commit-op` and
   `rebase` vs `rebase-op`.

Phase 5 documents this decision in the master plan (open
question 3 marked resolved, with a pointer to this phase) and
adds the crate to the workspace.

### What phase 5 builds on

- **Phase-1 ABI**: `SnapshotResult::ERROR_*` codes. Phase 5's
  `SnapshotError` enum maps 1:1 to these wire codes so
  phase 6+ guest binaries can translate via a thin matcher.
- **Phase-2 qcow2 surface**: `OFLAG_COPIED` constant,
  `L1_OFFSET_MASK`, `classify_qcow2_l2_standard`,
  `classify_qcow2_l2_extended`. Read-only API; phase 5
  depends on `qcow2` but does not touch it.
- **`set_refcount` in `src/crates/resize/src/qcow2.rs:1898`**
  — the canonical refcount-width-aware writer for 1/2/4/8/
  16/32/64-bit widths, including little-endian bit ordering
  within bytes for sub-byte widths. Phase 5 **moves /
  duplicates this function into the new snapshot crate** (or,
  better, lifts it into a `qcow2`-internal shared helper) so
  the snapshot mutators can call it without depending on
  `resize`. Open question 1 below picks one of those two
  paths.
- **`allocate_backing_cluster_qcow2` in
  `src/crates/commit/src/qcow2.rs:219`** — the closest analog
  to `alloc_cluster_for_snapshot`. Cursor-driven linear scan
  over staged refcount blocks, claims the first zero entry,
  marks the containing refblock dirty. Phase 5's allocator
  reuses the cursor pattern (`AllocCursor` struct mirrors
  `BackingAllocationState`).
- **`lookup_refcount` in
  `src/crates/qcow2/src/lib.rs:4613`** — read-only helper
  the phase 5 mutators do *not* use directly (they operate
  on staged slices) but the layout helper at line 4628
  (`cluster_index / entries_per_block` etc.) is the
  reference for the snapshot allocator's index math.

### What phase 5 produces

Eight pure functions plus three supporting types, all in the
new `src/crates/snapshot/` crate:

1. `SnapshotError` enum mapping 1:1 to phase-1 wire error
   codes.
2. `SnapshotPatch` / `SnapshotPlan` / `MAX_SNAPSHOT_PATCHES`
   types (used by phases 6–8 but landed here so the type
   surface is stable). v1 patch shape is `Write { byte_offset,
   bytes }` (Append unused — snapshot never grows the file
   in v1 because the bounded `MAX_SNAPSHOTS = 16` cap from
   phase 2 prevents the snapshot table from spilling beyond
   its initial cluster).
3. `read_refcount_in_block(block: &[u8], local_idx: u64,
   refcount_bits: u32) -> Option<u64>` — pure scalar read.
4. `set_refcount_in_block(block: &mut [u8], local_idx: u64,
   refcount_bits: u32, value: u64) -> Result<(), SnapshotError>`
   — pure scalar write, all widths. Lifted from
   `resize::qcow2::set_refcount`.
5. `check_refcount_after_addend(current: u64, addend: i32,
   refcount_bits: u32) -> Result<u64, SnapshotError>` —
   computes `current + addend`, returns the new value or
   `SnapshotError::RefcountOverflow` (positive addend) /
   `SnapshotError::ParseFailed` (negative addend below zero
   — defensive; should never happen if the caller paired
   inc/dec correctly).
6. `AllocCursor` struct + `alloc_cluster_in_refblocks(blocks:
   &mut [u8], cluster_size: u64, refcount_bits: u32,
   refblock_count: u64, host_refblocks_start: u64, cursor:
   &mut AllocCursor) -> Result<u64, SnapshotError>` —
   linear-scan allocator over the staged refcount-block
   region. Claims the first zero entry, sets it to 1,
   updates the cursor. Returns the host byte offset of the
   claimed cluster. Mirrors
   `allocate_backing_cluster_qcow2`. Refuses with
   `RefcountExhausted` if every block is full.
7. `rewrite_l1_entry_copied_flag(l1_bytes: &mut [u8],
   entry_idx: u32, set: bool) -> Result<(), SnapshotError>`
   — sets/clears `OFLAG_COPIED` on one L1 entry. Pure slice
   mutation.
8. `rewrite_l2_entry_copied_flag(l2_bytes: &mut [u8],
   entry_idx: u32, set: bool, extended_l2: bool) ->
   Result<(), SnapshotError>` — same for L2. Extended-L2
   entries have COPIED on the 8-byte type-and-offset half;
   the subcluster bitmap half is unaffected.
9. `for_each_cluster_in_l1<F>(l1_bytes: &[u8], cluster_bits:
   u32, mut l2_for_index: impl FnMut(u32) -> Option<&[u8]>,
   extended_l2: bool, mut visit: F) -> Result<(),
   SnapshotError> where F: FnMut(L1ClusterRef) -> bool` —
   visitor that walks the L1 entries, calling
   `l2_for_index(l1_idx) -> Option<&[u8]>` to fetch the
   pre-staged L2 bytes (the caller manages staging), then
   classifying each L2 entry and invoking `visit` with the
   per-cluster reference. `L1ClusterRef` is a small struct
   carrying `host_offset: u64, classification:
   L2Classification, l1_idx: u32, l2_idx: u32`. The
   visitor returns `false` to stop early. Returning
   `Err(SnapshotError)` from the function indicates a
   pre-stage error (caller didn't provide an L2 it should
   have).
10. `update_snapshot_refcount(active_l1: &[u8], snapshot_l1:
    &[u8], op: SnapshotRefcountOp, ...) -> Result<(),
    SnapshotError>` — composes `for_each_cluster_in_l1`
    twice (dry-run pass + apply pass) over an L1 to bump or
    decrement refcounts on every reachable cluster. The
    dry-run pass calls `check_refcount_after_addend` for
    every cluster; if any overflows the function returns
    `RefcountOverflow` *before* mutating any refblock byte.
    The apply pass calls `set_refcount_in_block`.
    `SnapshotRefcountOp` is an enum with variants
    `IncrementForCreate { snapshot_l1_clusters }`,
    `DecrementForDelete { snapshot_l1_clusters }`, and
    `SwapForApply { from_l1_clusters, to_l1_clusters }`
    (the apply variant decrements active and increments
    target).
11. `update_copied_flags_for_l1(l1_bytes: &mut [u8],
    cluster_bits: u32, mut l2_for_index: impl FnMut(u32) ->
    Option<&mut [u8]>, mut refcount_for_cluster: impl
    FnMut(u64) -> Option<u64>, extended_l2: bool) ->
    Result<u32, SnapshotError>` — walks the L1, for each
    reachable cluster computes whether refcount == 1 and
    sets/clears the COPIED flag on the L1 / L2 entry
    accordingly. Returns the number of entries rewritten.

Unit tests cover ~40 cases against synthetic byte arrays:
every refcount width, allocation cursor edge cases (block
full, all blocks full, sparse blocks), COPIED-flag idempotence
(setting set / clearing clear), extended-L2 vs standard,
visitor early-stop, dry-run overflow detection without
mutation, and the boundary conditions for
`check_refcount_after_addend` (max value, zero, negative
addend bookkeeping).

### What phase 5 does not change

- The wire ABI (frozen since phase 1).
- The qcow2 crate's read API (frozen since phase 2).
- The phase-3 list-mode guest binary.
- The phase-4 host CLI.
- The fuzz harness (phase 12).
- The integration tests (phase 11).

Phase 5 is a pure addition: a new crate, no I/O, no host or
guest binary changes. Existing operation binaries (`info`,
`convert`, `snapshot` list mode, etc.) are byte-identical
after this commit.

### Why this is shippable as one commit

The crate is self-contained: nothing else imports it yet
(phases 6–8 will). It builds and tests independently of every
other crate. The build is green at every step. A single
commit lands the crate; phases 6–8 add per-mode planners on
top.

## Mission and problem statement

After phase 5 lands:

1. `src/crates/snapshot/Cargo.toml` exists with
   `name = "snapshot"`, deps `shared` + `qcow2` only. No
   `no_std` declaration because the crate inherits no_std
   from its dependencies via the `#![no_std]` attribute on
   `src/lib.rs`.

2. `src/Cargo.toml` adds `"crates/snapshot"` to the
   `members` array. Phase 6+ adds `snapshot` to the
   `dependencies` of `src/operations/snapshot/Cargo.toml`.

3. `src/crates/snapshot/src/lib.rs` declares:
   - `SnapshotError` enum (13 variants mirroring the wire
     error codes from phase 1).
   - `SnapshotPatch<'a>` (`Write { byte_offset, bytes }`).
   - `SnapshotPlan<'a>` with inline `MAX_SNAPSHOT_PATCHES`
     storage and `new` / `push` / `patches` helpers (mirrors
     `CommitPlan`).
   - `MAX_SNAPSHOT_PATCHES: usize = 64` (open question 2
     picks the value).
   - `pub mod qcow2;` re-exporting the qcow2 mutator
     primitives.

4. `src/crates/snapshot/src/qcow2.rs` declares the eight
   pure functions from mission item 3–11 above:
   - `read_refcount_in_block`
   - `set_refcount_in_block`
   - `check_refcount_after_addend`
   - `AllocCursor` (struct) + `alloc_cluster_in_refblocks`
   - `rewrite_l1_entry_copied_flag`
   - `rewrite_l2_entry_copied_flag`
   - `L1ClusterRef` (struct) + `for_each_cluster_in_l1`
   - `SnapshotRefcountOp` (enum) + `update_snapshot_refcount`
   - `update_copied_flags_for_l1`

5. Unit tests in `src/crates/snapshot/src/qcow2.rs` cover
   the ~40 cases enumerated in the Situation section.
   Tests are pure (no I/O, no mock CallTable) and run on
   synthetic byte buffers.

6. `set_refcount` is **lifted** from
   `src/crates/resize/src/qcow2.rs` into the new snapshot
   crate (renamed `set_refcount_in_block` to match the
   naming convention used in mission item 4). The resize
   crate gains a `pub use snapshot::qcow2::set_refcount_in_block
   as set_refcount;` re-export so its 14 existing call sites
   stay working without touching the resize source. Open
   question 1 resolves this directionally; the resize crate
   does *not* gain a runtime dependency on `snapshot` because
   the import is at the type-alias / re-export layer only.

   *Alternative considered and rejected*: lift `set_refcount`
   into the `qcow2` crate as a `pub(crate)`-or-`pub` helper.
   Rejected because the `qcow2` crate is read-mostly and
   adding mutators bloats its public surface. The snapshot
   crate is the natural home.

   *Alternative considered*: leave `set_refcount` in
   `resize` and make `snapshot` depend on `resize`. Rejected
   because that creates a `snapshot → resize → snapshot`
   logical loop (snapshot might want to use a future
   resize-planner helper) and because resize is a larger,
   slower-to-build crate.

7. `docs/qcow2/qcow2-snapshots.md` gains a short "Mutator
   surface" section describing the new crate's API
   (one paragraph + a list of the eight functions).

8. `docs/plans/PLAN-snapshot.md` updates:
   - Open question 3 marked resolved with the actual
     decision (`src/crates/snapshot/` crate, parallel to
     `commit` / `rebase`).
   - Open question 8 marked resolved (two-pass overflow
     check implemented in `update_snapshot_refcount`'s
     dry-run pass).
   - Phase 5 execution-table row updated to reference this
     phase plan.

9. `make instar` builds clean; `make test-rust` passes (the
   new `snapshot` crate's ~40 unit tests raise the workspace
   test count by ~40). `make check-binary-sizes` unchanged
   (no operation binary imports `snapshot` yet).
   `make lint` clean; `pre-commit run --all-files` clean.

10. No ABI changes, no host CLI changes, no guest binary
    changes. `instar snapshot -l` is byte-identical.

## Open questions

### 1. Lift `set_refcount` to snapshot, or duplicate?

Working answer: **lift, with a re-export from resize**. The
function is the canonical bit-level refcount-width writer
(seven widths, sub-byte little-endian ordering). Duplicating
risks the two copies drifting; lifting means one source of
truth.

The re-export shape is:

```rust
// In src/crates/resize/src/qcow2.rs
use snapshot::qcow2::set_refcount_in_block as set_refcount;
```

Resize's 14 existing call sites continue to compile. The
resize crate gains a `dependencies.snapshot` entry in its
Cargo.toml.

*Note on circular-dependency risk*: snapshot does not depend
on resize, so the dependency edge is one-way. If a future
phase wants to add a resize → snapshot dependency for some
other reason, the cycle would need breaking; for now it's
fine.

### 2. `MAX_SNAPSHOT_PATCHES` value

Working answer: **64**. Snapshot planners (phases 6–8) emit
patches for:
- The snapshot-table header rewrite (1 patch).
- Header `nb_snapshots` / `snapshots_offset` update (1).
- Snapshot-table entry insertion / removal (1).
- Per-modified-refblock writeback: bounded by the number of
  refblocks touched by the L1 walk, ~6 in typical 1 MiB
  image / 64 KiB cluster cases.
- Per-modified-L2 writeback for COPIED-flag rewrites: same
  bound.
- The new snapshot L1 cluster allocation (1) and its
  refcount adjustment (already counted).

A 16-entry image (the v1 cap from the bounded
`parse_snapshot_table`) realistically tops out at ~30
patches. 64 is conservative headroom. The `CommitPlan` uses
16 but commits are simpler. Phase 6 confirms the cap is
sufficient or raises it; the constant is a single
declaration.

### 3. Should `SnapshotPatch` have an `Append` variant?

Working answer: **no** for v1. The bounded `MAX_SNAPSHOTS =
16` cap from phase 2 ensures the snapshot table never spills
beyond its initial cluster. The L1 / L2 tables can be
replaced in-place because `-a` reuses the active L1's
cluster and `-c` allocates a *new* cluster for the snapshot's
L1 (via `alloc_cluster_in_refblocks`) — this allocation
comes from existing zero-refcount entries in already-staged
refblocks, not from a file grow.

If a future phase raises `MAX_SNAPSHOTS` to a value that
forces snapshot-table growth across cluster boundaries (the
spec allows 65536), `Append` lands then. Tracked as future
work in the master plan.

### 4. Should the dry-run pass return the proposed values or just yes/no?

Working answer: **yes/no plus the cluster offset of the
overflow**. Returning the proposed values would require
allocation (a `Vec<(u64, u64)>` of `(host_offset, new_rc)`
pairs) and v1 is no_alloc. The dry-run pass returns
`Err(SnapshotError::RefcountOverflow { at_host_offset })`
on the first overflow it detects, before any mutation.
The apply pass trusts the caller to only invoke it after a
successful dry-run.

Trade-off: the caller can't preview the full set of
modifications before applying. Acceptable for v1; phases
6–8 don't need a preview.

### 5. Should `update_snapshot_refcount` take per-mode arguments via an enum or via separate functions?

Working answer: **enum** (`SnapshotRefcountOp`). The three
variants share the L1-walk skeleton; splitting into three
public functions would either duplicate the walk
(maintenance burden) or expose a shared `walk_l1` helper
that's still parameterised by the variant. The enum is
cleaner.

```rust
pub enum SnapshotRefcountOp<'a> {
    IncrementForCreate { snapshot_l1: &'a [u8] },
    DecrementForDelete { snapshot_l1: &'a [u8] },
    SwapForApply { from_l1: &'a [u8], to_l1: &'a [u8] },
}
```

### 6. Should `update_copied_flags_for_l1` walk the L1 itself or just take the changed-cluster set?

Working answer: **walk the L1**. The caller provides the
`refcount_for_cluster` closure that returns the current
refcount; the walker classifies each cluster's L2 entry and
calls `rewrite_l2_entry_copied_flag` (or the L1 equivalent
for the L1 entry itself) when the refcount-1 boundary is
crossed in either direction.

Alternative considered: take a `&[u64]` of `(host_offset,
new_refcount)` pairs that just had their refcount change.
Rejected because building this slice requires alloc.

The walker handles both directions: refcount=1 means COPIED
should be set; refcount>1 means COPIED should be cleared.
Idempotent.

### 7. Does `alloc_cluster_in_refblocks` handle refcount-table growth?

Working answer: **no, returns `RefcountExhausted` if the
staged blocks are full**. Refcount-table growth is a
separate concern; resize-grow phase 2 has the pattern
(grow the refcount table, allocate new refblocks). Snapshot
v1 punts: if every existing refblock entry is non-zero, the
allocation fails. This is a real but rare case in practice
(images with very high allocation pressure); the user falls
back to running `qemu-img resize` first to grow the image.

Tracked as future work: phase 6 can pull in resize's
refcount-table-growth helper if it materially expands the
set of supported source images.

### 8. Should `for_each_cluster_in_l1` visit unallocated L2 entries?

Working answer: **no**. The visitor skips unallocated
clusters (L2 entry == 0) because they have no host offset
to bump a refcount for. The dry-run / apply pass for
refcount adjustment only cares about allocated clusters.

The COPIED-flag walker (`update_copied_flags_for_l1`) also
skips unallocated entries — there's no host cluster whose
refcount could have crossed the 1-boundary.

### 9. Should the L1 entry's COPIED flag rewrite use the same helper as L2's?

Working answer: **no, separate functions**. The L1 entry is
a u64 with `OFLAG_COPIED` at bit 63 and offset at bits 9..55
(L1_OFFSET_MASK). The L2 entry has the same layout for
standard L2 but extended L2 has subcluster bitmap halves
that the L1 doesn't. Separate functions keep the
extended-L2 conditional out of the L1 path.

### 10. Should `SnapshotError` map 1:1 to wire codes, or have additional internal variants?

Working answer: **1:1 to wire codes plus a couple of
internal variants for misuse**. The 13 wire codes from
phase 1 cover the user-facing failure modes. Phase 5 adds
two non-wire variants:
- `MisalignedAccess` — caller passed `local_idx` out of
  range for the block, or `entry_idx` out of range for the
  L1/L2 table. Indicates a planner bug, not a wire
  condition.
- `Unsupported` — non-16-bit refcount width in the
  allocator (mirrors commit's `UnsupportedFormat`). Maps to
  wire `ERROR_UNSUPPORTED_FEATURE` at the planner →
  wire-code translation layer in phase 6+.

The `From<SnapshotError> for u32` impl translates each
variant to the matching `SnapshotResult::ERROR_*` constant
from phase 1; `MisalignedAccess` and `Unsupported` map to
`ERROR_PARSE_FAILED` and `ERROR_UNSUPPORTED_FEATURE`
respectively.

### 11. Should phase 5 emit any `SnapshotPatch` entries?

Working answer: **no**. Phase 5 lands the types (so phases
6–8 can use them) but the mutators operate on staged slices
in place. Patches are emitted by the per-mode planners
(phases 6/7/8) when they translate the mutated slice state
into "write these bytes here" instructions.

This matches the `commit` crate's shape:
`allocate_backing_cluster_qcow2` mutates `backing_refblocks`
in place and the per-mode planner (`plan_commit_qcow2`)
emits patches based on the dirty-bit tracking.

### 12. Should phase 5 land `free_cluster` as a distinct function?

Working answer: **no, fold it into `set_refcount_in_block`
with value=0**. `free_cluster` is a one-liner that calls
`set_refcount_in_block(block, local_idx, refcount_bits, 0)`.
A separate function adds API surface without value. Open
question 12 is essentially "should we have an alias?" —
answer: no, the call site can write `set_refcount_in_block(..., 0)`
directly.

If a future phase finds the explicit name aids readability,
adding `free_cluster_in_block` as a one-line wrapper is
trivial.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | worktree | Create the new `src/crates/snapshot/` crate. (i) `Cargo.toml`: copy `src/crates/commit/Cargo.toml` as a template, change `name = "snapshot"`, change `description = "Pure planner crate for qcow2 snapshot mutator primitives"`. Dependencies are `shared = { path = "../../shared" }` and `qcow2 = { path = "../qcow2" }`. Keep the `[lib]` declarations identical. (ii) `src/lib.rs`: `#![no_std]`, declare `pub mod qcow2;`, the `SnapshotError` enum (13 wire-mapped variants from open question 10 plus `MisalignedAccess` and `Unsupported`), the `SnapshotPatch<'a>` enum (`Write { byte_offset: u64, bytes: &'a [u8] }` only, no `Append` per open question 3), the `SnapshotPlan<'a>` struct mirroring `CommitPlan` (with `MAX_SNAPSHOT_PATCHES: usize = 64` per open question 2, `new` / `push` / `patches` methods), and the `From<SnapshotError> for u32` impl mapping each variant to the matching `shared::SnapshotResult::ERROR_*` constant. (iii) `src/qcow2.rs`: empty stub with `use shared::SnapshotResult;` and `use qcow2::{OFLAG_COPIED, L1_OFFSET_MASK};` plus a `#[cfg(test)] mod tests` skeleton. (iv) Add `"crates/snapshot"` to `src/Cargo.toml` `members`. (v) Build clean with `cargo build -p snapshot` and `cargo test -p snapshot`. |
| 5b | high | opus | worktree | Lift the `set_refcount` function from `src/crates/resize/src/qcow2.rs:1898` into the new crate. (i) In `src/crates/snapshot/src/qcow2.rs`, define `pub fn set_refcount_in_block(block: &mut [u8], local_idx: u64, refcount_bits: u32, value: u64) -> Result<(), SnapshotError>` with the exact bit-level logic from `resize::set_refcount` (all seven widths 1/2/4/8/16/32/64, sub-byte little-endian ordering). The signature changes `refcount_bits: u8` to `u32` to match the rest of the snapshot API. Error on unsupported widths with `SnapshotError::Unsupported`; on `local_idx` out of range for the block with `SnapshotError::MisalignedAccess`. (ii) Add `pub fn read_refcount_in_block(block: &[u8], local_idx: u64, refcount_bits: u32) -> Result<u64, SnapshotError>` — the reverse direction, same widths. Mirror the read-side bit-pos / mask logic from `qcow2::lookup_refcount` at line 4656. (iii) Add `pub fn check_refcount_after_addend(current: u64, addend: i32, refcount_bits: u32) -> Result<u64, SnapshotError>` per mission item 5: returns the new value or `RefcountOverflow` / `MisalignedAccess` (for underflow below zero). Compute the max as `(1u64 << refcount_bits).saturating_sub(1)`; for `refcount_bits >= 64` the max is `u64::MAX`. (iv) In `src/crates/resize/src/qcow2.rs`, replace the `set_refcount` definition with `use snapshot::qcow2::set_refcount_in_block as set_refcount;` at the top of the file. Update the resize crate's `Cargo.toml` to add `snapshot = { path = "../snapshot" }` as a dependency. The 14 existing call sites should compile without further changes because the function name remains `set_refcount`. (v) Add ~15 unit tests in `src/crates/snapshot/src/qcow2.rs::tests` covering: every supported width round-trips a value via set→read; out-of-range index errors with MisalignedAccess; unsupported widths (0, 3, 5, 7, 128) error with Unsupported; sub-byte widths handle bit-position math correctly (write to entry 0 / entry 3 / entry 7 in a 1-bit block); `check_refcount_after_addend` returns max for `current = max, addend = 0`, errors on `current = max, addend = +1`, errors on `current = 0, addend = -1`, returns 0 on `current = 1, addend = -1`. Use opus: bit-level width handling has off-by-one and endianness traps; the resize crate's `set_refcount` has 14 callers that must keep working byte-identically. |
| 5c | high | opus | worktree | Implement `AllocCursor` and `alloc_cluster_in_refblocks` in `src/crates/snapshot/src/qcow2.rs`. (i) Define `pub struct AllocCursor { pub next_refblock: u64, pub next_entry_in_refblock: u64, pub allocated: u64 }` with `Default` derived (mirrors `BackingAllocationState`). (ii) Define `pub fn alloc_cluster_in_refblocks(blocks: &mut [u8], cluster_size: u64, refcount_bits: u32, refblock_count: u64, host_refblocks_start: u64, cursor: &mut AllocCursor) -> Result<u64, SnapshotError>` per mission item 6 and open question 7. Algorithm: loop through `cursor.next_refblock` and `cursor.next_entry_in_refblock`; for each candidate entry call `read_refcount_in_block` (passing a slice of `blocks` corresponding to that refblock); on the first zero entry, call `set_refcount_in_block(..., 1)`, update the cursor, compute the host offset as `(refblock_idx * entries_per_refblock + entry_in_refblock) * cluster_size + host_refblocks_start`, return. On full traversal without finding a zero, return `SnapshotError::RefcountExhausted`. v1 supports `refcount_bits == 16` only — other widths return `Unsupported` (matches commit's allocator scope). (iii) Add ~10 unit tests: alloc one from an all-zero 16-bit block (returns the first cluster offset, refcount[0] becomes 1); alloc successive entries (cursor advances correctly); alloc skips a non-zero entry; alloc from full block returns Exhausted; alloc with cursor pointing past end returns Exhausted; non-16-bit width errors with Unsupported; the `host_refblocks_start` parameter affects the returned offset linearly. Use opus: cursor state interaction with the per-entry read/write loop is the bit that breaks subtly if the cursor advances wrong on an existing-allocation skip. |
| 5d | high | opus | worktree | Implement `rewrite_l1_entry_copied_flag` and `rewrite_l2_entry_copied_flag` in `src/crates/snapshot/src/qcow2.rs`. (i) `pub fn rewrite_l1_entry_copied_flag(l1_bytes: &mut [u8], entry_idx: u32, set: bool) -> Result<(), SnapshotError>` per mission item 7. The L1 entry is a big-endian u64 at byte offset `entry_idx * 8`. Read the existing entry, set or clear bit 63 (`OFLAG_COPIED = 1 << 63`), write back. Errors with MisalignedAccess if `entry_idx * 8 + 8 > l1_bytes.len()`. (ii) `pub fn rewrite_l2_entry_copied_flag(l2_bytes: &mut [u8], entry_idx: u32, set: bool, extended_l2: bool) -> Result<(), SnapshotError>` per mission item 8. Standard L2 entries are 8 bytes (one BE u64 at `entry_idx * 8`); extended L2 entries are 16 bytes (a BE u64 type-and-offset half at `entry_idx * 16`, then an 8-byte subcluster bitmap). The COPIED flag lives on the type-and-offset half in both cases; the subcluster bitmap is untouched. Errors with MisalignedAccess if the entry extends past `l2_bytes.len()`. (iii) Add ~10 unit tests: L1 rewrite sets/clears COPIED idempotently (set then set, clear then clear); L1 rewrite preserves the offset bits below 55; L1 rewrite errors out-of-range; L2 standard rewrite sets/clears COPIED; L2 standard rewrite preserves offset and compressed-cluster bits; L2 extended rewrite sets/clears COPIED on the offset half only (subcluster bitmap unchanged); L2 extended rewrite preserves the subcluster bitmap bit-for-bit; L2 extended rewrite uses the 16-byte stride (entry_idx=1 writes offset 16 not 8). Use opus: the standard vs extended-L2 layout split is easy to get wrong by a factor of 2; a unit test that sets COPIED on extended L2 entry 1 and asserts entry 0 is untouched catches the dominant bug. |
| 5e | high | opus | worktree | Implement `for_each_cluster_in_l1` visitor in `src/crates/snapshot/src/qcow2.rs`. (i) Define `pub struct L1ClusterRef { pub l1_idx: u32, pub l2_idx: u32, pub host_offset: u64, pub classification: qcow2::L2Classification }` (or whatever name `qcow2::classify_qcow2_l2_*` returns — read `src/crates/qcow2/src/lib.rs` to confirm; commit crate uses the same classification surface). (ii) Define `pub fn for_each_cluster_in_l1<L2F, VisitF>(l1_bytes: &[u8], cluster_bits: u32, mut l2_for_index: L2F, extended_l2: bool, mut visit: VisitF) -> Result<(), SnapshotError> where L2F: FnMut(u32) -> Option<&[u8]>, VisitF: FnMut(L1ClusterRef) -> bool`. Algorithm: iterate L1 entries (each is 8 bytes BE u64); for each non-zero entry, mask out OFLAG_COPIED and extract the L2 table offset (`l1_entry & L1_OFFSET_MASK`); call `l2_for_index(l1_idx)` to get the staged L2 bytes; if `None`, error with `SnapshotError::MisalignedAccess`; iterate L2 entries (8 or 16 bytes each per `extended_l2`); for each, call the appropriate classifier (`classify_qcow2_l2_standard` or `classify_qcow2_l2_extended`); skip Unallocated and Zero entries (per open question 8); for Standard / Compressed, build `L1ClusterRef` and call `visit(ref)`; if visit returns `false`, stop iterating and return Ok. (iii) Add ~10 unit tests: walk a 2-entry L1 with synthetic L2s, assert visitor sees the right number of clusters; walk skips unallocated L1 entries (l1_entry == 0); walk skips unallocated L2 entries (l2_entry == 0); walk visits Standard clusters with the correct host_offset; walk visits Compressed clusters; visitor early-stop (return false from visit) halts the walk; missing L2 bytes (l2_for_index returns None for an allocated L1 entry) errors with MisalignedAccess; extended-L2 walk uses 16-byte stride. Use opus: the L2 classifier dispatch is the kind of integration point that compiles but silently misclassifies (counts compressed as Standard, miscounts subcluster bitmaps as allocation). |
| 5f | high | opus | worktree | Implement the two high-level composed mutators in `src/crates/snapshot/src/qcow2.rs`: `update_snapshot_refcount` and `update_copied_flags_for_l1`. (i) Define `pub enum SnapshotRefcountOp<'a> { IncrementForCreate { snapshot_l1: &'a [u8] }, DecrementForDelete { snapshot_l1: &'a [u8] }, SwapForApply { from_l1: &'a [u8], to_l1: &'a [u8] } }`. (ii) Define `pub fn update_snapshot_refcount<L2F, RBF>(op: SnapshotRefcountOp<'_>, refblocks: &mut [u8], cluster_size: u64, refcount_bits: u32, refblock_count: u64, host_refblocks_start: u64, mut l2_for_index: L2F, extended_l2: bool, mut refblock_byte_offset_for_cluster: RBF) -> Result<(), SnapshotError> where L2F: FnMut(u32) -> Option<&[u8]>, RBF: FnMut(u64) -> Option<(usize, u64)>` — the `refblock_byte_offset_for_cluster` closure maps a host_offset to `(refblock_byte_offset, entry_local_idx)` so the function can address the right entry in the staged `refblocks` slice. Algorithm: **two passes**. **Pass 1 (dry-run)**: for each cluster the L1(s) reach (use `for_each_cluster_in_l1` keyed off the relevant L1 variant), call `read_refcount_in_block` to get the current refcount, call `check_refcount_after_addend` with the per-op addend; if any returns `RefcountOverflow`, return it immediately *without* mutating any refblock. **Pass 2 (apply)**: walk the same L1(s) again, call `set_refcount_in_block` with the new value. For `SwapForApply`, pass 1 checks decrement on `from_l1` and increment on `to_l1`; pass 2 applies both. (iii) Define `pub fn update_copied_flags_for_l1<L2MF, RCF>(l1_bytes: &mut [u8], cluster_bits: u32, mut l2_for_index: L2MF, mut refcount_for_cluster: RCF, extended_l2: bool) -> Result<u32, SnapshotError> where L2MF: FnMut(u32) -> Option<&mut [u8]>, RCF: FnMut(u64) -> Option<u64>` per mission item 11. Walk the L1; for each L1 entry call `refcount_for_cluster` on the L1 cluster's own host_offset; if refcount == 1 set COPIED on the L1 entry, else clear. Then for each L2 entry in the L2 cluster, get its host_offset, get its refcount, and rewrite the L2 entry's COPIED flag. Return the count of entries rewritten. (iv) Add ~15 unit tests covering: pass 1 detects overflow without mutating refblocks (assert refblocks slice is unchanged after a failed dry-run); pass 2 applies all bumps successfully; `IncrementForCreate` increments only `snapshot_l1`'s clusters; `DecrementForDelete` decrements only; `SwapForApply` decrements from + increments to with no double-counting on shared clusters; COPIED-flag rewrite sets when refcount=1; COPIED-flag rewrite clears when refcount>1; COPIED-flag rewrite is idempotent (running twice produces the same bytes); COPIED-flag rewrite handles extended L2 entries; the count returned matches the number of entries actually changed. Use opus: this is the load-bearing composition step that phases 6–8 will lean on; the dry-run vs apply separation must be byte-exact (no mutation in pass 1) and the L2 visitor + refcount lookup interaction is where subtle bugs hide. |
| 5g | low | sonnet | worktree | Documentation updates. (i) `docs/qcow2/qcow2-snapshots.md`: add a new section "Mutator surface" after the existing "Parser surface" section (added in phase 2). One paragraph describing the new `src/crates/snapshot/` crate purpose, plus a bulleted list of the eight public functions with one-line summaries. Keep the section under 35 lines. (ii) `docs/plans/PLAN-snapshot.md`: mark open question 3 resolved with the actual decision (`src/crates/snapshot/` crate, parallel to commit/rebase; lift `set_refcount` from resize; reasons in this phase plan). Mark open question 8 resolved (two-pass overflow check is implemented in `update_snapshot_refcount`'s dry-run pass; the dry run reads-only via `read_refcount_in_block` and aborts on the first overflow without touching the refblocks buffer). Update the phase 5 execution-table row to reference this phase plan and note that it landed. Keep all edits under 25 lines total. |
| 5h | low | sonnet | worktree | Run `make instar`, `make test-rust`, `make check-binary-sizes`, `make lint`, and `pre-commit run --all-files` from the worktree root. Confirm `snapshot.bin` and every other operation binary is byte-identical to its pre-phase size (the new crate is not yet imported by any operation binary). Confirm the new `snapshot` crate's ~60 unit tests all pass (~15 from step 5b + ~10 from 5c + ~10 from 5d + ~10 from 5e + ~15 from 5f). Confirm resize's tests still pass (the `set_refcount` re-export must keep the 14 existing call sites byte-compatible). Stage and present a single commit covering all of steps 5a–5g with the commit-message convention from `~/.claude/CLAUDE.md` (50-char first line ending in `.`, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By line with model + context window + effort + any other active settings). The commit message should explain that this lands the pure mutator primitives in a new `src/crates/snapshot/` crate parallel to `commit` / `rebase`, that `set_refcount` was lifted from resize into snapshot with a re-export keeping resize's call sites working, and that no operation binary imports the new crate yet — phases 6–8 wire the per-mode planners on top. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session (this
conversation) is reserved for review and decision-making.
After each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

**All steps in this phase use `isolation: "worktree"`.** Phase
5 is the highest-risk phase in the plan family. A worktree
per attempt means if any step has to be retried, the main
checkout stays clean.

### Model and effort notes

- Steps 5a, 5g, 5h are mechanical extensions of well-established
  patterns. Sonnet at medium / low effort with the briefs
  above is enough.
- Steps **5b through 5f are all high-effort opus**. Phase 5
  is the riskiest phase per the master plan. Each step has
  bit-level / invariant correctness requirements that the
  unit tests *will* surface if got wrong — but unit-test
  triage is expensive in this codebase, so getting it right
  first try matters. Opus' context window also lets each
  step hold the qcow2 spec + the resize / commit reference
  implementations + the test fixtures simultaneously.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's
      summary.
- [ ] No unrelated files modified.
- [ ] `cargo build -p snapshot` (every step).
- [ ] `cargo test -p snapshot` (steps 5b–5f).
- [ ] `cargo build -p resize` and `cargo test -p resize`
      (step 5b — the re-export must not break resize).
- [ ] `make instar` (step 5h); confirm `snapshot.bin` and
      every operation binary is byte-identical to its
      pre-phase size.
- [ ] `make check-binary-sizes` (step 5h).
- [ ] `make lint` (step 5h).
- [ ] `pre-commit run --all-files` (step 5h).
- [ ] The new functions are documented with `# Safety`
      blocks where they take `*mut u8` (none in phase 5;
      pure slice functions only).
- [ ] `SnapshotError` maps 1:1 to the wire codes from
      phase 1 (open question 10).
- [ ] The dry-run pass in `update_snapshot_refcount`
      provably does not mutate `refblocks` on failure
      (open question 4 / mission item 6 / step 5f tests).
- [ ] The L1 / L2 COPIED-flag helpers handle extended L2
      correctly (16-byte stride; subcluster bitmap untouched).
- [ ] No `unsafe` outside what the existing qcow2 / resize
      crates already require (snapshot crate should be safe
      Rust top-to-bottom).

### Pre-commit verification ritual (step 5h)

The single commit at the end of step 5h must build cleanly
through the entire stack:

1. `make instar` — full host VMM + core + all guest
   operation binaries. Every binary byte-identical to its
   pre-phase 5 size (no operation imports the new crate).
2. `make test-rust` — workspace unit tests, including the
   ~60 new snapshot crate tests and the unchanged resize
   crate tests (the `set_refcount` lift must keep resize's
   coverage passing).
3. `make check-binary-sizes` — every binary within budget,
   no deltas.
4. `make lint` / `pre-commit run --all-files`.

If any of these fail, fix the failure in the *same* commit
(we are not amending a published commit; this is the
original commit's pre-push verification). Do not split into
a follow-up.

## Administration and logistics

### Success criteria

Phase 5 is complete when:

* All eight steps above land in one commit on the `snapshot`
  branch.
* The new `src/crates/snapshot/` crate compiles and tests
  cleanly with ~60 unit tests.
* `src/crates/resize/` continues to build and pass its
  existing tests (the `set_refcount` lift is invisible to
  resize call sites).
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass.
* No operation binary changed size (no imports yet).
* `docs/qcow2/qcow2-snapshots.md` documents the mutator
  surface.
* `docs/plans/PLAN-snapshot.md` open questions 3 and 8 are
  marked resolved.

### Future work created by this phase

- **Refcount-table growth in the allocator.** Phase 5's
  `alloc_cluster_in_refblocks` returns `RefcountExhausted`
  if every existing refblock is full. Future work pulls in
  resize's refcount-table-growth helper if a snapshot
  workflow demands it.
- **`Append` patch variant.** Phase 5's `SnapshotPatch`
  has no `Append`; the bounded `MAX_SNAPSHOTS = 16` cap
  keeps the snapshot table inside its initial cluster. If
  the master plan raises the cap, `Append` lands.
- **Dry-run preview.** Phase 5's dry-run pass returns the
  first overflow's offset, not the full set of would-be
  refcount changes. If a future phase wants to expose a
  user-facing "what would happen" preview, the dry-run
  pass extends to populate a caller-provided slice.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing. The resize crate's
`set_refcount` is the canonical reference for refcount-
width handling; if the lift surfaces correctness gaps in
that function (e.g. the sub-byte little-endian ordering
disagrees with `qcow2::lookup_refcount`'s reader), fixing
them is in-scope and counts as a bug fixed here.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not**
added to `docs/plans/order.yml` per the convention. The
master plan links to it from the Execution table at
`docs/plans/PLAN-snapshot.md:866-882`; step 5g updates that
row to point at this file.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
