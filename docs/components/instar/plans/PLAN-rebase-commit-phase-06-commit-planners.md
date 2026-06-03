# PLAN-rebase-commit phase 06: commit planners

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read
`src/crates/rebase/src/lib.rs` (the structural template),
`src/crates/rebase/src/qcow2.rs` (`allocate_overlay_cluster_qcow2`,
`AllocationState`, `RebaseQcow2SafeContext` — the closest
existing allocator and context pair), the qcow2 cluster /
refcount primitives in `src/crates/qcow2/src/lib.rs`
(`walk_l2_standard`, `QcowHeader::parse`, `L1_OFFSET_MASK`,
`L2_OFFSET_MASK`, `OFLAG_COPIED`, `REFCOUNT_TABLE_OFFSET_OFFSET`,
`REFCOUNT_ORDER_OFFSET`, `lookup_refcount`), the vmdk grain
helpers in `src/crates/vmdk/src/lib.rs`, the resize crate
(`src/crates/resize/`) as a structural sibling, and the phase 1
`CommitConfig` / `CommitResult` in `src/shared/src/lib.rs`.
Ground your answers in what the code actually does today. Do
not speculate when you can read it instead. Where a question
touches external concepts (`qemu-img commit` semantics, qcow2
backing-chain commit, vmdk monolithicSparse parent rewrite),
research as needed to give a confident answer.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master plan
is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). Phases 1–5
shipped the rebase half end-to-end; this is the sixth phase of
twelve and starts the commit half.

I prefer one commit per logical step. The step table below
identifies five steps; this phase can land step by step or as a
single consolidated commit. Either way each commit must be
self-contained: build, lint, pass tests.

## Situation

Phase 1 shipped `CommitConfig`, `CommitResult`, the
`send_commit_result` and `write_input_sector` call-table
entries, and the host's `open_chain_devices_rw` helper. The
ABI is dormant until a planner produces something for the
guest to act on. This phase delivers that planner.

Commit's structural shape is a near-twin of the rebase phase 2
work, with one informative difference: commit has no unsafe
mode. Every commit is data-aware — there is always overlay
cluster data to read and write into the backing — so the planner
does not branch on a mode flag the way rebase does. That makes
the public surface simpler than rebase's:

```rust
pub fn plan_commit_qcow2<'a>(
    opts: &Qcow2CommitOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<Qcow2CommitContext<'a>, CommitError>;
```

returns a context the guest drives through a per-cluster loop;
there is no `Qcow2CommitOutput::Unsafe` arm.

The work itself decomposes into three movements per format:

1. **Overlay walk.** The guest iterates the overlay's L1 / L2
   tables (qcow2) or grain directory / grain tables (vmdk) to
   enumerate every cluster the overlay has allocated. The
   planner provides the geometry the guest needs and the pure
   decoder helpers for the format's L2 / GT entry shape; the
   guest does the I/O.

2. **Backing write.** For each allocated overlay cluster the
   guest reads the data (via `read_input_sector` against the
   overlay) and writes it into the backing (via
   `write_output_sector`). If the backing's L2 already has an
   allocated cluster at that guest offset the guest reuses it;
   otherwise the guest calls
   `allocate_backing_cluster_qcow2(ctx, state) -> u64` to
   claim a fresh host offset from the backing's refcount
   blocks (the planner has pre-staged them in scratch). The
   guest then updates the backing's L2 entry to point at the
   data. For vmdk the analogue is
   `allocate_backing_grain_vmdk(ctx, state) -> u64`, which
   advances an EOF cursor by `backing_grain_size_sectors`.

3. **Overlay clear.** After the data writes complete, the
   guest re-walks the overlay's allocated clusters and (a)
   zeros the L2 entry (qcow2) or GTE (vmdk), (b) decrements
   the matching refcount entry from 1 to 0 (qcow2 only — vmdk
   has no refcount table). These writes use
   `write_input_sector(0, ...)` because the overlay is
   attached as input device slot 0 in RW mode. The planner
   does not pre-compute the clear patches; the guest emits
   them inline using helpers the planner exposes (e.g.
   `overlay_l2_entry_byte_offset(cluster_idx)`).

This split keeps the planner pure and `no_std` (every
`*Context` borrows from scratch; no I/O) and matches the
"planner stages, guest drives" pattern phase 3 step 3e
established for rebase safe mode.

The relevant existing infrastructure this phase builds on:

- **Rebase crate as the structural template**
  (`src/crates/rebase/`). The crate layout — `Cargo.toml`
  with `no_std`, `lib.rs` declaring `CommitPatch` /
  `CommitPlan` / `CommitError` / `*Opts` / `*Output` /
  `*Context` / `AllocationState` types and re-exporting
  per-format symbols, plus per-format private modules
  (`qcow2.rs`, `vmdk.rs`) — mirrors phase 2 step 2a
  one-for-one. The unit-test layout under
  `src/crates/rebase/tests/` (one file per format-mode pair
  plus a `tests/common/mod.rs` helper) is also the template.
- **Rebase qcow2 allocator** (`allocate_overlay_cluster_qcow2`
  at `src/crates/rebase/src/qcow2.rs`). The backing-side
  allocator is structurally identical: scan staged refcount
  blocks for the first zero entry, bump it to 1, mark the
  containing refblock dirty, return the host byte offset.
  The differences are (a) the input is the *backing's*
  refcount blocks, not the overlay's, and (b) the host
  offset goes into the backing's L2 entry rather than
  becoming the new home of an overlay cluster. The internal
  logic is the same and can be a near-copy.
- **Rebase vmdk allocator**
  (`allocate_overlay_grain_vmdk`). The backing-side
  vmdk allocator is *exactly* the same: bump
  `next_grain_sector` by `grain_size_sectors`, return host
  bytes. The only thing that changes is which file's EOF
  the cursor is anchored on; commit anchors on the backing.
- **qcow2 walk helpers** (`walk_l2_standard` at
  `src/crates/qcow2/src/lib.rs:1131`,
  `count_allocated_in_l2_standard` at line 1044). These are
  pure functions taking a byte slice; the planner exposes
  them as the canonical per-L2-table iteration shape so the
  guest can walk both the overlay (to find committed
  clusters) and the backing (to find existing host offsets)
  without rolling its own bit-twiddling.
- **qcow2 header geometry parsing**
  (`QcowHeader::parse` at `src/crates/qcow2/src/lib.rs:334`).
  Already used by rebase. The planner re-parses both the
  overlay header and the backing header so the host's
  declared geometry can't desync from the on-disk truth.
- **vmdk geometry parsing**
  (`Vmdk4HeaderFull::parse` and `Vmdk4Header::parse`). Both
  used by rebase phase 2.
- **Phase 1 ABI**
  (`src/shared/src/lib.rs`). `CommitConfig` carries
  `overlay_cluster_size`, `backing_cluster_size`,
  `overlay_virtual_size`, `backing_virtual_size`, and
  `backing_chain_first` / `backing_chain_count` for the
  optional "skip clusters the backing chain already
  provides" mode (deferred — see open question 6).
  `CommitResult::ERROR_*` codes 0–7 are append-only; phase
  6 maps the planner's `CommitError` variants to those wire
  codes (the phase 7 guest binary does the mapping).
- **Fuzz target shape**
  (`src/fuzz/fuzz_targets/fuzz_rebase_planners.rs`). Phase
  10 will add `fuzz_commit_planners.rs` against the public
  entry points this phase ships, so the planner must expose
  fuzz-friendly entry points (pure functions on raw byte
  slices, no `CallTable`).

## Mission and problem statement

After phase 6 lands:

1. A new `no_std` crate `src/crates/commit/` exists, declared
   in the workspace `src/Cargo.toml` and depending on
   `shared`, `qcow2`, `vmdk`, and `create` (for any
   descriptor / header layout constants commit needs to write
   into). Mirrors `src/crates/rebase/Cargo.toml` line-by-line.

2. The crate's public surface in `src/crates/commit/src/lib.rs`
   exposes:

   - `CommitPatch<'a>` — `Write { byte_offset, bytes }`
     (the only variant commit needs; the overlay-clear pass
     uses Write patches against the overlay, and the backing
     L2 / refcount updates are similarly Write patches against
     the output device).
   - `CommitPlan<'a>` with an inline
     `[CommitPatch; MAX_COMMIT_PATCHES]` (16 is generous; the
     planner only emits header-rewrite patches for the
     deferred-apply path, see point 5 below).
   - `CommitError` enum mapping one-to-one to
     `CommitResult::ERROR_*` from phase 1 where applicable,
     plus the planner-internal variants the rebase phase 2
     enum uses: `UnsupportedFormat`, `UnsupportedSubformat`,
     `NoBacking`, `ExternalDataFile`, `LuksUnsupported`,
     `BackingTooSmall`, `OverlayLargerThanBacking`,
     `HeaderMismatch`, `OverlayCorrupt`, `BackingCorrupt`,
     `ScratchTooSmall`, `RefcountExhausted`, `ParseFailed`,
     `Overflow`. `#[derive(Debug, Clone, Copy, PartialEq,
     Eq)]`.
   - `Qcow2CommitOpts<'a>` — see point 3 below.
   - `VmdkCommitOpts<'a>` — see point 4 below.
   - `plan_commit_qcow2(opts, scratch) ->
     Result<Qcow2CommitContext<'a>, CommitError>`.
   - `plan_commit_vmdk(opts, scratch) ->
     Result<VmdkCommitContext<'a>, CommitError>`.
   - `allocate_backing_cluster_qcow2(ctx, state) ->
     Result<u64, CommitError>` — pure allocator returning the
     backing's host byte offset of the freshly claimed
     cluster.
   - `allocate_backing_grain_vmdk(ctx, state) ->
     Result<u64, CommitError>` — analogous cursor-bump for
     vmdk grains.
   - `BackingAllocationState` (qcow2) and
     `BackingGrainAllocationState` (vmdk) — distinct from
     the rebase `AllocationState` and
     `GrainAllocationState` so the two crates can drift
     independently. Both `#[derive(Debug, Clone, Copy,
     Default)]`.

3. `Qcow2CommitOpts<'a>` carries the inputs the planner needs
   from the host's pre-probe pass:

   - `overlay_header: &'a [u8]` — at least 4 KiB of the
     overlay's first cluster (the planner re-parses).
   - `overlay_file_size: u64`.
   - `backing_header: &'a [u8]` — at least 4 KiB of the
     backing's first cluster.
   - `backing_file_size: u64`.
   - `backing_refcount_table: &'a [u8]` — the backing's
     refcount-table bytes (the planner uses these to figure
     out which refblock host offsets are valid; mirrors
     rebase phase 2 step 2c).
   - `backing_refblock_host_offsets: &'a [u64]` — length
     equals `backing_refblock_count`.
   - `backing_refcount_blocks: &'a [u8]` — concatenated
     refcount-block bytes the planner copies into scratch.
   - `backing_refblock_count: u32`.

4. `VmdkCommitOpts<'a>` carries:

   - `overlay_header: &'a [u8]` — first sector parsed.
   - `overlay_descriptor: &'a [u8]`.
   - `overlay_grain_size_sectors: u32`,
     `overlay_num_gtes_per_gt: u32`,
     `overlay_num_gd_entries: u32`,
     `overlay_gd_offset_sectors: u64`.
   - `overlay_grain_directory: &'a [u8]` — staged GD bytes.
   - `overlay_grain_tables: &'a [u8]` — concatenated GT
     bytes (one block per allocated GT).
   - `overlay_allocated_gt_host_sectors: &'a [u64]`.
   - `overlay_allocated_gt_count: u32`.
   - `overlay_virtual_size: u64`.
   - `overlay_file_size: u64`.
   - … and the same mirror set for the backing:
     `backing_header`, `backing_descriptor`,
     `backing_grain_size_sectors`, etc., plus
     `backing_file_size: u64` (anchor for the EOF cursor).

5. The planner does not emit a list of overlay-clear patches.
   The guest discovers which clusters are allocated at
   runtime via `walk_l2_standard` (qcow2) / GT iteration
   (vmdk) and issues the clear writes itself using the
   per-format helpers the planner exposes:

   - `overlay_l2_byte_offset(ctx, l1_idx, l2_idx_in_table)
     -> u64` — where the entry to zero lives on disk.
   - `overlay_refcount_byte_offset_qcow2(ctx, host_offset)
     -> (u64, u8 /* bit_in_byte for non-byte-aligned
     widths */)` — where to decrement the overlay's
     refcount entry. v1 supports `refcount_bits == 16` only
     (same constraint as rebase phase 2 step 2c), so the
     "bit_in_byte" return is always 0; the signature
     anticipates the 1/2/4-bit follow-up.
   - `overlay_gte_byte_offset_vmdk(ctx, gt_host_sector,
     gte_idx_in_gt) -> u64` — vmdk equivalent.

   This split keeps the planner small: it stages the
   backing's refcount blocks (which the allocator mutates),
   echoes the geometry the guest needs, and exposes pure
   decoder functions. Nothing on the overlay side needs
   pre-staging because the overlay's mutations are
   single-entry, byte-localised writes the guest can issue
   directly.

6. `Qcow2CommitContext<'a>` carries:

   - `overlay_cluster_size: u32`,
     `overlay_cluster_count: u64`,
     `overlay_l1_table_offset: u64`,
     `overlay_l1_size: u32`,
     `overlay_refcount_table_offset: u64`,
     `overlay_refcount_table_clusters: u32`,
     `overlay_refcount_bits: u32`.
   - `backing_cluster_size: u32`,
     `backing_cluster_count: u64`,
     `backing_l1_table_offset: u64`,
     `backing_l1_size: u32`,
     `backing_refcount_bits: u32`,
     `backing_entries_per_refblock: u64`,
     `backing_refblock_count: u32`.
   - `backing_refblocks: &'a mut [u8]` — staged in scratch
     by the planner; mutated by
     `allocate_backing_cluster_qcow2`.
   - `backing_refblock_host_offsets: &'a [u64]` — echoed
     from opts so the guest can flush dirty blocks back.
   - `backing_dirty: &'a mut [u8]` — per-refblock dirty
     bitmap, length `(backing_refblock_count + 7) / 8`.

7. `VmdkCommitContext<'a>` mirrors the rebase
   `RebaseVmdkSafeContext` shape: staged backing GD bytes,
   concatenated backing GT bytes, per-GT host sectors,
   per-GT dirty bitmap, GD dirty flag, plus
   `backing_grain_size_sectors`,
   `backing_num_gtes_per_gt`, `backing_num_gd_entries`,
   `backing_gd_offset_sectors`, `backing_file_size`. v1 only
   allocates grains in GTs the backing already has — the
   GD-extension follow-up is tracked under "Future work"
   (same constraint as rebase phase 2 step 2e).

8. Unit and integration tests under
   `src/crates/commit/tests/` cover, at minimum:

   - **qcow2 in-place commit smoke**: build a backing + an
     overlay via `create::plan_qcow2` (overlay with a
     backing reference), hand-write one allocated cluster
     into the overlay, plan commit, drive the allocator
     once, assert the host offset is a fresh cluster in the
     backing, assert the staged backing refblock has its
     entry bumped.
   - **qcow2 commit allocator advances across calls**:
     mirror the rebase `allocator_advances_across_calls`
     test pattern.
   - **qcow2 commit rejects external data file**.
   - **qcow2 commit rejects LUKS**.
   - **qcow2 commit rejects backing smaller than overlay**
     (`OverlayLargerThanBacking`).
   - **vmdk in-place commit smoke**: build a backing + an
     overlay, plan commit, drive the EOF grain allocator,
     assert the host sector lands at or above the
     backing's pre-commit EOF and is aligned to a grain
     boundary.
   - **vmdk commit allocator lands at EOF**: direct port of
     the rebase `safe_mode_allocator_lands_at_eof_and_advances_by_grain`
     test against the backing-side allocator.
   - **Cross-format error path**: passing a vmdk
     `Qcow2CommitOpts` (wrong opts type) is a compile-time
     error; document the smoke test as evidence that the
     type surface forces a correct caller.

9. `make instar`, `make lint`, `make test-rust`,
   `make check-binary-sizes`, and `pre-commit run
   --all-files` all pass.

Nothing in phase 6 changes user-visible behaviour. The crate
is consumed only by phase 7 (the commit guest binary) and
phase 10 (the fuzz harness).

## Open questions

### 1. Unified entry point per format vs split?

Already constrained by the situation: commit has no unsafe
mode, so the dispatch question is moot. There is one entry
point per format. The internal helpers (`backing_geometry`,
`overlay_geometry`, etc.) are `pub(crate)` for unit tests.

### 2. Output shape: context-only vs enum?

Working choice: **context-only**, with the planner returning
the `Qcow2CommitContext` / `VmdkCommitContext` directly
(`Result<Context, Err>`). Rebase used an `Output` enum to
distinguish unsafe and safe modes; commit doesn't have that
split, so the enum would be a single-arm wrapper with no
information content.

Tracked as a documentation item: if phase 8 finds the host
CLI wants a `--dry-run` shape, we can introduce a
`Qcow2CommitOutput::Plan(CommitPlan)` arm at that point and
keep the existing context-returning shape as
`Qcow2CommitOutput::Live(Context)`. Today, no.

### 3. "Copy every allocated cluster" vs "copy only differing"?

The master plan flagged this as an open subquestion. Working
choice: **copy every allocated overlay cluster
unconditionally**, matching qemu-img's default behaviour.

Rationale:
- Simpler planner. No need to expose the backing-chain
  metadata at the planner layer; the guest just walks the
  overlay's L2 and writes everything.
- Matches qemu-img bit-for-bit, which means the phase 9
  baseline matrix can compare `qemu-img info` outputs
  cleanly. A "copy only differing" variant would diverge in
  cluster counts and disk-size.
- The "skip when the backing chain already provides the
  same data" optimisation needs the chain reader from rebase
  step 3f, which is currently local to the rebase guest
  binary. Promoting it to a shared crate is its own
  scoped follow-up; deferring keeps phase 6 small.

The optimisation lives under "Future work created by this
phase".

### 4. Refcount-width coverage in v1

Same constraint as rebase phase 2 step 2c: v1 supports
`refcount_bits == 16` only on the backing. Other widths
return `UnsupportedFormat`. Both the backing allocator and
the overlay-clear refcount decrement live on the same code
path here, so widening to 1/2/4/8/32/64-bit support is a
single follow-up across both sides.

### 5. Cluster-size mismatch between overlay and backing

qcow2 allows the overlay to have a different `cluster_bits`
than the backing. qemu-img commit reads at the overlay's
cluster size and writes at the overlay's cluster size, even
when the backing's cluster size is larger or smaller.

Working choice: **match qemu-img exactly**. The planner
exposes `overlay_cluster_size` and `backing_cluster_size`
separately; the guest's commit loop works at the overlay's
cluster size, and the planner's `allocate_backing_cluster_qcow2`
returns offsets that may be intra-cluster relative to the
backing's larger cluster. Document this in the safe-mode
context so the phase 7 guest binary doesn't get clever and
try to align writes to the backing's cluster size.

The v1 test matrix uses matching cluster sizes; cross-size
cases are an explicit follow-up in step 6d's test plan.

### 6. Backing chain depth and the "skip when chain provides" question

The phase 1 `CommitConfig` carries `backing_chain_first` and
`backing_chain_count`. v1 ignores them in the planner — the
"copy every allocated cluster" mode (open question 3 above)
doesn't need them. The fields stay populated by the host so
the guest could in principle still walk the chain if a
future commit mode wants to.

### 7. Compressed clusters in the overlay

qcow2 commit on a compressed overlay needs to decompress the
clusters before writing them into the backing (the backing
stores standard, uncompressed clusters). Decompression lives
in the qcow2 crate's existing `read_compressed_cluster` and
is feature-gated behind `decompress`.

Working choice: **planner does not handle compression**. The
guest binary is responsible for decoding compressed L2
entries via the qcow2 crate's helpers when it walks the
overlay. The planner just exposes geometry; whether the
cluster the guest reads is compressed-then-decompressed or
standard is invisible to the allocator.

If the commit binary ships without the `decompress` feature
on its qcow2 dependency (matching the rebase binary), the
guest binary returns `ERROR_UNSUPPORTED_FORMAT` when it
encounters a compressed L2 entry. Document this in phase 7.

### 8. Allocator failure mode

Same answer as rebase phase 2 step 2c open question 5: v1
returns `RefcountExhausted` when the backing's existing
refcount blocks are full. The user can either run `qemu-img
commit` for now or run `qemu-img check -r` on the backing to
reclaim leaked clusters before retrying. The bound is large
in practice (16 KiB of refcount entries covers ~1 GiB of
clusters at 64 KiB each).

Refcount-block extension is the natural symmetry follow-up
with rebase's deferred extension.

### 9. Should `CommitPatch` be a copy of `RebasePatch`?

Working choice: **separate copy**. Two reasons:
- Commit only needs `Write` (no `Append`; no `ZeroFill`).
  Even though `RebasePatch` is also Write-only at the time
  of writing, it carries an `Append` variant for the
  long-path-relocation follow-up. Pulling that into the
  commit surface would advertise a capability commit
  doesn't have.
- Adding a `commit -> rebase` crate dependency would link
  every commit-only consumer (phase 7, phase 10 fuzz
  target) to rebase's whole public surface unnecessarily.

If a third planner (e.g. a future snapshot planner) wants
the same `Write` variant, the cleanup is to promote
`Write { byte_offset, bytes }` to a shared `crates/patch/`
crate.

### 10. How does the guest "iterate guest clusters" at commit
time?

Not a phase 6 concern — phase 7. But the planner's
`Qcow2CommitContext` must expose enough information that the
guest can do it efficiently. Minimum fields are listed in
Mission point 6 above.

## Execution

The phase plan recommends five steps. Each step is small
enough to review independently; consolidating into one or
two commits is also fine. The step table below is for
sub-agent assignment.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | **Shipped** as `34333e9`. Scaffold `src/crates/commit/`: `Cargo.toml` mirroring rebase, `src/lib.rs` declaring `CommitPatch`, `CommitPlan` (`MAX_COMMIT_PATCHES = 16`), `CommitError` enum, and stub per-format modules. Workspace members list at `src/Cargo.toml` updated. Two unit tests cover patch construction + plan bound. |
| 6b | high | opus | none | **Shipped** as `da0b974`. `plan_commit_qcow2` + `allocate_backing_cluster_qcow2` + pure decoder helpers (`overlay_l2_byte_offset_qcow2`, `overlay_refcount_byte_offset_qcow2`). Validation arms cover overlay/backing parse, dirty/corrupt, external data file, LUKS, backing-smaller-than-overlay, non-16-bit refcount widths, zero cluster size, and mismatched `backing_refblock_host_offsets` / count. Eleven new unit tests across all validation arms plus the allocator's claim/advance/exhausted/unsupported-width paths. |
| 6c | high | opus | none | **Shipped** as `db4d389`. `plan_commit_vmdk` + `allocate_backing_grain_vmdk` + `overlay_gte_byte_offset_vmdk`. Cursor-bump allocator anchored on the backing's EOF (`BackingGrainAllocationState::at_eof`). Validation arms cover parse failure, compression on either side (refused), zero grain size / GTEs per GT / GD entries on either side, backing smaller than overlay, mismatched GT host-sectors arrays, and insufficient staged GD/GT bytes. Six new unit tests. |
| 6d | medium | sonnet | none | **Shipped** as `26ca765`. Integration tests under `src/crates/commit/tests/`: `qcow2.rs` (4 tests: geometry smoke, allocator claim sequence, backing-smaller-than-overlay, mismatched refblock count), `vmdk.rs` (3 tests: geometry smoke, allocator lands at backing EOF + advances by grain, backing-smaller-than-overlay), and `tests/common/mod.rs` exposing `materialise_create` (shape borrowed from the rebase crate). Total: 26 tests across the crate (2 lib + 13 qcow2 unit + 6 vmdk unit + 4 qcow2 integration + 3 vmdk integration). |
| 6e | low | sonnet | none | **Partial.** Pre-commit clean. Plan docs updated to reflect all five steps shipped. |

## Agent guidance

### Execution model

Same model as phases 1–5: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should also use opus when working on steps 6b and 6c because
the cross-format refcount / grain-allocator reasoning load
is real even when the operator is doing the typing.

### Planning effort

The master plan flagged this phase as **high effort**. The
high-effort steps within the phase are 6b and 6c. Steps 6a,
6d, 6e are medium-low.

### Step ordering

Strict dependency: 6a → 6b → 6c → 6d → 6e. 6c could
interleave with 6b because they touch different format paths
and don't share a `lib.rs` helper. 6d depends on both 6b and
6c shipping (the `tests/common/mod.rs` is shared, and the
per-format test files exercise each format's planner). 6e
is the wrap-up.

If the operator prefers landing 6b and 6c as a single commit
because they're tightly coupled in the public surface (both
add types to `lib.rs`), that's fine — the rebase phase 2
analogue 2b+2c ended up shipping that way (`0e4c4b9`).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed (read them).
- [ ] No unrelated files modified.
- [ ] The new crate compiles in isolation (`cargo build -p
      commit` via the lint container or pre-commit).
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make test-rust` passes (and the new tests are
      actually exercised, not silently skipped).
- [ ] `make check-binary-sizes` is unchanged (no guest
      binary depends on the commit crate until phase 7).
- [ ] `pre-commit run --all-files` clean.
- [ ] No `pub use` from commit re-exports anything that
      should stay private to qcow2 / vmdk / create. Promote
      a `pub(crate)` to `pub` in the format crate if needed;
      do not work around visibility by copying.
- [ ] Validation arms in step 6b have at least one negative
      test each. The phase 2 rebase tests are the bar:
      every `RebaseError` variant a planner can return has a
      test that hits it.
- [ ] The qcow2 backing allocator's scratch layout follows
      the rebase phase 2 step 2c shape: `path_buf`
      analogue is omitted (commit has no path to write), but
      `header_rewrite_buf`, `dirty_bytes`, and
      `refblocks_buf` carve the scratch in that order so the
      "Scratch out of room" failure modes are deterministic
      and reviewable.

## Administration and logistics

### Success criteria

Phase 6 is complete when:

* `src/crates/commit/` exists and is wired into the
  workspace.
* The public surface from the Mission section is implemented.
* Integration tests from the Mission section pass.
* `make instar`, `make lint`, `make test-rust`,
  `make check-binary-sizes`, `pre-commit run --all-files`
  all pass.
* The execution-table row for phase 6 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hashes.

### Future work created by this phase

Tracked here so the management session can decide later
whether to lift any of these into a follow-up phase plan.

- **Refcount-block extension in the backing allocator**
  (open question 8). v1 returns `RefcountExhausted` when
  the backing's existing refcount blocks are full. The
  resize-phase-7 refcount-block-append logic from
  `src/crates/resize/src/qcow2.rs:plan_l1_and_refcount_grow`
  is the template; folding it into the commit allocator is
  a mechanical lift.
- **Refcount widths other than 16-bit** on both the backing
  allocator and the overlay-clear decrement (open question
  4). 1/2/4/8/32/64-bit follow-up; pair with the rebase-side
  follow-up of the same shape.
- **"Skip when the backing chain already provides the same
  data" mode** (open question 3). Needs the rebase phase 3
  step 3f `read_chain_cluster` helper promoted to a shared
  crate. Saves I/O on chains with shallow divergence; the
  v1 default is the simpler "copy every allocated cluster"
  path.
- **Compressed-cluster handling on the guest side** (open
  question 7). Currently the commit guest binary (phase 7)
  will return `ERROR_UNSUPPORTED_FORMAT` when it
  encounters a compressed L2 entry on the overlay. Adding
  the `decompress` feature to the commit binary's qcow2
  dependency unblocks this; the planner itself is
  unaffected.
- **vmdk GD extension on the backing** (open question
  follow-up). The step 6c allocator only fills GTEs in GTs
  the backing already has; allocating a fresh GT (and
  bumping the backing's GD entry) when the covering GDE is
  zero is a follow-up. Mirrors rebase phase 2 step 2e's
  open follow-up.
- **Subcluster-level commit for extended L2.** v1 treats
  extended L2 on the overlay as "fully allocated", same as
  resize and rebase do; subcluster-granularity commit would
  let the planner skip un-allocated subclusters inside an
  allocated cluster. Out of scope until extended-L2
  workloads ask for it.
- **vmdk twoGbMaxExtent commit.** v1 only supports
  monolithicSparse on both the overlay and the backing;
  twoGbMaxExtent commit is rejected with
  `UnsupportedSubformat`. Track alongside the create-side
  twoGbMaxExtent output gap from PLAN-create's Future
  work.
- **Promote `CommitPatch::Write` to a shared crate.** If
  phase 12 (or any later phase) introduces a third planner
  with the same Write-only patch shape, factor out
  `crates/patch/` so the three crates can share. Today
  this is premature.

### Bugs fixed during this work

To be filled in as work progresses.

### Documentation index maintenance

Not added to `docs/plans/order.yml` — phase plans live
alongside the master plan but only the master plan is
indexed.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
