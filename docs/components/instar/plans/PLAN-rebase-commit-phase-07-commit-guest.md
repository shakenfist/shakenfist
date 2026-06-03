# PLAN-rebase-commit phase 07: commit guest binary

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase thoroughly. Read the rebase
guest binary at `src/operations/rebase/src/main.rs` (the
structural twin — its safe-mode runner is the closest
existing analogue to what this phase delivers), the commit
planner crate at `src/crates/commit/` (the API this binary
consumes), the shared types in `src/shared/src/lib.rs`
(`CommitConfig`, `CommitResult`, the `write_input_sector` and
`send_commit_result` call-table entries), the existing
chain-walking helpers in `src/operations/info/src/main.rs`
and `src/operations/convert/src/main.rs`, and the
call-table documentation in `ARCHITECTURE.md` and
`AGENTS.md`. Ground your answers in what the code actually
does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the seventh of twelve.

I prefer one commit per logical step. The step table below
identifies five steps; this phase can land step by step or
as a single consolidated commit.

## Situation

Phase 1 shipped the shared ABI (`CommitConfig`,
`CommitResult`, `send_commit_result`, `write_input_sector` in
`CallTable`). Phase 6 shipped the planner crate
`src/crates/commit/`:

- `plan_commit_qcow2(opts, scratch)` validates the overlay +
  backing headers, stages the backing's refcount blocks, and
  returns a `Qcow2CommitContext` the guest drives.
  `refcount_bits == 16` only in v1; cluster-size mismatches
  between overlay and backing are tolerated (the guest works
  at the overlay's cluster size).
- `plan_commit_vmdk(opts, scratch)` validates both vmdk
  headers, stages the backing's grain directory + grain
  tables, and returns a `VmdkCommitContext`. Compression on
  either side is refused. v1 only allocates into GTs the
  backing already has (GD extension is a follow-up).
- `allocate_backing_cluster_qcow2(ctx, state) -> u64` and
  `allocate_backing_grain_vmdk(ctx, state) -> u64` are the
  pure allocators the guest calls when it decides to copy a
  cluster / grain into the backing.
- `BackingGrainAllocationState::at_eof(backing_file_size,
  grain_size_sectors)` initialises the vmdk grain allocator
  at the next grain-aligned host sector at or after the
  backing's existing EOF.

Phase 7 delivers the guest binary at `src/operations/commit/`
that consumes `CommitConfig` from `OPERATION_CONFIG_ADDR`,
reads the overlay + backing headers, stages the metadata
both planners need, drives the per-cluster (qcow2) /
per-grain (vmdk) commit loop, performs the overlay-clear
pass (the first instar operation to write through
`write_input_sector(0, ...)`), and reports the outcome via
`send_commit_result` + `send_complete`.

Structurally this is the closest twin to the rebase
phase 3 step 3e safe-mode runner. The novel pieces are:

1. **Two metadata sides.** Rebase stages only the overlay's
   metadata. Commit stages the overlay's L1 + allocated L2
   tables (so the guest can walk to find allocated clusters)
   *and* the backing's L1 + allocated L2 tables + refcount
   table + refcount blocks (so the guest can place the data
   and call the allocator). The scratch carve grows
   accordingly.
2. **Two output sinks.** The backing data + metadata writes
   route through `write_output_sector`. The overlay-clear
   pass routes through `write_input_sector(0, ...)`, the
   call-table primitive added in phase 1 specifically for
   commit. Rebase doesn't use `write_input_sector` at all;
   phase 7 is its first real consumer.
3. **No "modes" branch.** Commit is always data-aware (open
   question 1 in the phase 6 plan locked this in); there is
   one runner per format, no unsafe/safe dispatch.

The relevant existing infrastructure this phase builds on:

- **Rebase guest binary**
  (`src/operations/rebase/src/main.rs`, ~1450 lines). The
  closest comparable shipped binary: same scratch-carve
  pattern at `SCRATCH_MEM_BASE`, same `apply_plan` shape,
  same `read_byte_range` / `write_byte_range` sector
  helpers, same per-format dispatch in `_start`. The
  safe-mode runner `run_qcow2_safe` (lines 403–989) is the
  template for the qcow2 commit runner — same staged-L1 +
  staged-L2-on-non-zero-L1 + staged-refcount approach,
  same `find_staged_l2` decoder.
- **Phase 6 planner crate**
  (`src/crates/commit/`). The guest binary consumes
  `plan_commit_qcow2`, `plan_commit_vmdk`,
  `allocate_backing_cluster_qcow2`,
  `allocate_backing_grain_vmdk`,
  `Qcow2CommitContext`, `VmdkCommitContext`,
  `BackingAllocationState`,
  `BackingGrainAllocationState`,
  `overlay_l2_byte_offset_qcow2`,
  `overlay_refcount_byte_offset_qcow2`,
  `overlay_gte_byte_offset_vmdk`.
- **Call-table primitives**
  (`src/shared/src/lib.rs:543–765`). Commit uses
  `read_input_sector` and `write_input_sector` against
  input slot 0 (the overlay), and
  `read_output_sector` / `write_output_sector` against
  the output device (the backing). All four already
  exist; `write_input_sector` was added in phase 1 for
  this binary specifically.
- **Memory layout**
  (`src/shared/src/lib.rs` address constants and
  `ARCHITECTURE.md`).
  `OPERATION_CONFIG_ADDR = 0x00081000`,
  `CHAIN_CONFIG_ADDR = 0x00082000`,
  `SCRATCH_MEM_BASE = 0x00300000`,
  `SCRATCH_MEM_SIZE = 0x00CF0000` (~12.9 MiB),
  `OPERATION_LOAD_ADDR = 0x00020000` (384 KB binary cap).
- **Chain config plumbing**
  (`src/shared/src/lib.rs` `ChainConfig` at
  `CHAIN_CONFIG_ADDR`). For commit the backing's own
  ancestor chain at `[backing_chain_first,
  backing_chain_first + backing_chain_count)` is populated
  by the phase 8 VMM but **unused by the v1 runner** —
  v1's "copy every allocated overlay cluster
  unconditionally" mode doesn't consult the backing chain
  (see phase 6 open question 3 / 6).

## Mission and problem statement

After phase 7 lands:

1. A new operation crate `src/operations/commit/` exists,
   declared in the workspace `src/Cargo.toml`. It produces
   a guest binary `commit.bin` that loads at
   `OPERATION_LOAD_ADDR`, weighs well under 384 KB, and is
   the entry point the VMM launches when the user runs
   `instar commit` (phase 8 wires the host CLI).

2. The binary's `_start`:
   - Validates the call table at `CALL_TABLE_ADDR`.
   - Reads `CommitConfig` from `OPERATION_CONFIG_ADDR` and
     validates its magic.
   - Reads the overlay header (first sector) via
     `read_input_sector(0, ...)`. (Overlay is at input
     slot 0, opened RW.)
   - Reads the backing header (first sector) via
     `read_output_sector(0, ...)`. (Backing is the output
     device, opened RW.)
   - Dispatches on `config.overlay_format`:
     `Qcow2` → `run_qcow2(call_table, config)`;
     `Vmdk4` → `run_vmdk(call_table, config)`;
     anything else → result with
     `ERROR_UNSUPPORTED_FORMAT`.
   - Sends the result via `send_commit_result` and signals
     completion via `send_complete`.

3. The qcow2 commit runner:
   - Parses the overlay header via
     `qcow2::QcowHeader::parse`. Refuses on dirty / corrupt
     / external-data / LUKS / `refcount_bits != 16`.
   - Parses the backing header (same refusals).
   - Refuses if `backing.virtual_size < overlay.virtual_size`
     (`ERROR_OVERLAY_LARGER_THAN_BACKING`).
   - Stages the overlay's L1 table (via
     `read_input_sector(0, ...)`).
   - Stages every overlay L2 table whose covering L1 entry
     is non-zero (same as rebase phase 3 step 3e).
   - Stages the backing's L1 table (via
     `read_output_sector`).
   - Stages every backing L2 table whose covering L1 entry
     is non-zero.
   - Stages the backing's refcount table.
   - Walks the refcount table to collect the
     refblock-host-offsets array.
   - Stages the backing's refcount blocks (concatenated, in
     refcount-table order).
   - Calls `plan_commit_qcow2(&opts, scratch)` to get a
     `Qcow2CommitContext`.
   - Drives the per-cluster commit loop (see section
     "Per-cluster commit loop" below).
   - Performs the **overlay-clear pass**: for every
     overlay cluster the loop committed, write zero u64s
     into the overlay's L2 entry slot via
     `write_input_sector(0, ...)`, then decrement the
     overlay's refcount entry to zero (16-bit width: one
     u16 BE write per cluster).
   - Flushes dirty backing L2 tables via
     `write_output_sector`.
   - Flushes the backing's L1 table if it grew (any new
     L2 table was allocated).
   - Flushes dirty backing refcount blocks via
     `write_output_sector`.
   - Builds a `CommitResult` with `clusters_committed`,
     `bytes_committed`, `overlay_clusters_cleared`, and
     `error = ERROR_OK`.

4. The vmdk commit runner:
   - Reads the overlay binary header, parses via
     `vmdk::Vmdk4HeaderFull::parse`. Refuses on
     compression / unsupported subformat.
   - Same checks for the backing.
   - Reads the overlay descriptor + the backing
     descriptor (used only for re-validation; commit does
     not rewrite either descriptor).
   - Stages the overlay's grain directory + every
     allocated overlay GT.
   - Stages the backing's grain directory + every
     allocated backing GT.
   - Calls `plan_commit_vmdk(&opts, scratch)` to get a
     `VmdkCommitContext`.
   - Initialises a `BackingGrainAllocationState` via
     `BackingGrainAllocationState::at_eof(backing_file_size,
     backing_grain_size_sectors)`.
   - Drives the per-grain commit loop.
   - Performs the overlay-clear pass: for every overlay
     grain the loop committed, write a zero u32 into the
     overlay's GTE slot via `write_input_sector(0, ...)`.
     (vmdk has no refcount table; GTE = 0 is the only
     clear operation.)
   - Flushes dirty backing GTs via `write_output_sector`.
   - Builds a `CommitResult` with the commit counters.

5. `CommitError` variants from the planner crate are mapped
   to `CommitResult::ERROR_*` codes via a `map_commit_error`
   helper. Phase 6 surfaced that the wire-level error set
   (8 codes, 0–7) is smaller than the planner's
   `CommitError` variant set (14 variants). Phase 7 step 7a
   adds the missing wire codes to `CommitResult` in
   `src/shared/src/lib.rs` so the host can render
   meaningful messages for each failure mode (mirrors what
   rebase phase 3 step 3a did for `RebaseResult`).

6. The binary builds clean, lints clean, ships under 100 KB,
   and `make check-binary-sizes` is green.

Nothing in phase 7 changes user-visible behaviour because
the host CLI doesn't exist yet — that's phase 8. The phase 7
deliverable is a binary the phase 8 VMM can launch.

### Per-cluster commit loop (qcow2)

For each guest `cluster_idx in 0..context.overlay_cluster_count`:

1. `l1_idx = cluster_idx / entries_per_l2`,
   `l2_inner_idx = cluster_idx % entries_per_l2`.
2. If `l1_idx >= overlay.l1_size`: break (past the L1's
   coverage).
3. Read `overlay_l1_entry = overlay_l1_buf[l1_idx * 8]` (BE
   u64). If `overlay_l1_entry & L1_OFFSET_MASK == 0`: skip
   (overlay does not own this cluster; backing provides it).
4. Find the staged L2 for that L1 entry (via the rebase
   step 3e `find_staged_l2` shape). If not found: defensive
   error (`HeaderMismatch`).
5. Read `overlay_l2_entry = staged_l2[l2_inner_idx * 8]` (BE
   u64). If `0`: skip. If `OFLAG_COMPRESSED` set:
   `ERROR_UNSUPPORTED_FORMAT` (compressed-cluster handling
   deferred per phase 6 open question 7).
6. Extract `overlay_host_offset = overlay_l2_entry &
   L2_OFFSET_MASK`. This is where the cluster's data lives
   on the overlay.
7. Read the cluster's data: `read_input_sector(0, ...)` ×
   `cluster_size / sector_size` calls into a single
   `OVERLAY_DATA_BUF` at the overlay cluster's
   sector range.
8. Look up the matching backing cluster:
   - `backing_l1_entry =
     backing_l1_buf[l1_idx_in_backing * 8]` (the indexing
     accounts for any cluster-size mismatch — see
     "Cluster-size mismatch handling" below).
   - If `backing_l1_entry & L1_OFFSET_MASK == 0`: the
     backing has no L2 table here. v1 deferral — return
     `ERROR_UNSUPPORTED_FORMAT` because allocating an L2
     table on the backing requires the L1-extension follow-
     up flagged in phase 6. (In practice freshly-created
     qcow2 backings with `Preallocation::Off` hit this
     immediately; test fixtures must use
     `Preallocation::Metadata` on the backing to pre-create
     L2 tables.)
   - Find the staged backing L2. Read
     `backing_l2_entry`.
   - If `backing_l2_entry & L2_OFFSET_MASK != 0`: the
     backing already has an allocated cluster. Write the
     overlay's data to that host offset via
     `write_output_sector`. Done.
   - Else: call `allocate_backing_cluster_qcow2(context,
     state)` → `data_host_offset`. Write the overlay's
     data there. Update
     `backing_l2_entry = data_host_offset | OFLAG_COPIED`
     in the staged L2 buf. Mark that staged L2 dirty.
9. `clusters_committed += 1`,
   `bytes_committed += overlay_cluster_size`.

After the loop, the overlay-clear pass walks the overlay
L1 again and clears every L2 entry whose host offset was
written to the backing.

### Per-grain commit loop (vmdk)

Same shape, with the qcow2 L1/L2 lookups replaced by GD/GT
lookups and the allocator replaced by
`allocate_backing_grain_vmdk`. vmdk-side simplifications:

- No L2-allocation case; vmdk's GT layout is dense — every
  guest offset has a GTE slot whether or not the grain is
  present. If `backing_gte_value == 0` the backing simply
  hasn't allocated this grain yet; we allocate one via the
  EOF cursor.
- No refcount table to decrement on the overlay-clear
  pass. The overlay's GTE = 0 is the sole clear write.

## Open questions

### 1. Per-cluster vs batched overlay-clear pass?

The master plan's "Atomicity and ordering" section calls
for: data → backing refcount → backing L2 → overlay L2 →
overlay refcount. Within that ordering the overlay clears
can be interleaved per-cluster or batched at the end.

Working choice: **batched at the end**. After every overlay
cluster has been written to the backing (and the backing's
metadata has been flushed), do one pass over the committed
clusters and clear them on the overlay. Reasons:

- Matches qemu-img's all-or-nothing observable behaviour:
  while the operation runs, the overlay still presents the
  user's pre-commit state. Only at the final pass does the
  overlay shrink to "empty over the new backing".
- Simpler bookkeeping: we already remember every committed
  overlay cluster (`bytes_committed` and the per-staged-L2
  dirty bitmap track it). A second walk is cheap.
- If the operation is killed mid-way, the partial state is
  "some data is in the backing, the overlay still
  references it, the overlay still has its refcounts" —
  `instar check --chain` reports this cleanly.

Per-cluster clear would interleave the writes; if the user
kills mid-way, the overlay state matches "exactly the
clusters that made it to the backing have been cleared", at
the cost of more inter-device I/O thrashing. Not worth it.

### 2. Cluster-size mismatch between overlay and backing

qcow2 permits the overlay to use a different `cluster_bits`
than its backing. qemu-img commit reads at the overlay's
cluster size and writes into the backing at the overlay's
cluster size, using sub-cluster offsets when the backing's
cluster is larger.

Working choice: **work at the overlay's cluster size; index
the backing's L2 accounting for the cluster ratio**. The
planner already exposes both cluster sizes separately. The
guest's per-cluster loop:

```
overlay_guest_offset = overlay_cluster_idx * overlay_cluster_size
backing_l1_idx = overlay_guest_offset / backing_l2_coverage
backing_l2_idx = (overlay_guest_offset / backing_cluster_size)
                 % backing_entries_per_l2
intra_backing_cluster = overlay_guest_offset % backing_cluster_size
```

`intra_backing_cluster != 0` is the sub-cluster write case:
the backing's host cluster is larger than the overlay's, so
the write lands mid-cluster. The guest just adds that
offset to the backing's host offset before writing.

v1 supports `overlay_cluster_size == backing_cluster_size`
only — the matrix tests in phase 9 baseline that. Cross-size
support is a v1.1 follow-up flagged under "Future work".
The guest refuses with `ERROR_UNSUPPORTED_FORMAT` when the
sizes differ.

### 3. Backing without pre-allocated L2 tables

Most freshly-created qcow2 backings have
`Preallocation::Off` and so start with an empty L1 — every
L1 entry is zero. Commit needs the backing's L2 tables to
exist before it can write data through them.

Two real choices:

- **A.** Allocate L2 tables on demand in the per-cluster
  loop. Each allocation is another
  `allocate_backing_cluster_qcow2` call plus an L1 update.
  Mirrors what rebase phase 3 step 3e does for the
  overlay's L2 tables.
- **B.** Defer L2-extension on the backing to v1.1; refuse
  with `ERROR_UNSUPPORTED_FORMAT` when the backing's L1
  doesn't already cover the guest offset.

Working choice: **A**, despite the extra complexity.
Reasons:

- Test fixtures can use `Preallocation::Off` on both sides
  — the common case from `instar create`.
- Phase 9 integration tests can compare against qemu-img
  commit byte-for-byte; qemu-img obviously handles this
  case.
- The "extend L2" path is structurally identical to rebase
  step 3e's path; copying its shape is a contained
  expansion of the runner.

Option B would force every test fixture to use
`Preallocation::Metadata` on the backing, which has its own
ergonomic cost.

If option A turns out to blow the binary-size budget at
implementation time, fall back to B and flag the L2-
extension case as a v1.1 follow-up.

### 4. How does the guest stage the overlay's L1 / L2 (and
the backing's L1 / L2)?

Same approach as rebase phase 3 step 3e: stage L1 entirely,
stage every L2 table whose covering L1 entry is non-zero.
Cache the (l1_idx, l2_staging_idx, host_offset, dirty)
quadruple in a small fixed-size array (`MAX_STAGED_L2 =
256` covers a 128 GiB qcow2 with 64 KiB clusters).

This applies twice — once to the overlay, once to the
backing. The scratch budget needs to fit both staging
regions. See section "Scratch layout" below.

### 5. Compressed clusters in the overlay's L2

Per phase 6 open question 7: refuse with
`ERROR_UNSUPPORTED_FORMAT`. The commit guest binary does
not enable the qcow2 crate's `decompress` feature, matching
the rebase binary, and reports the refusal when it
encounters an `OFLAG_COMPRESSED` L2 entry.

### 6. Should the guest validate the backing header after
the commit data writes?

Defensively yes — read sector 0 of the backing back at the
end, re-parse, and compare key fields (`virtual_size`,
`cluster_size`, `l1_table_offset`) against the values the
planner started with. Any mismatch indicates concurrent
modification or a host-side corruption and reports
`ERROR_HEADER_MISMATCH`.

Mirrors what resize does. Cheap (one sector read).

### 7. Empty overlay (no clusters allocated)

A valid commit input. The guest walks the L1, finds every
entry zero, runs zero allocator calls, the overlay-clear
pass has nothing to do, and the result reports
`clusters_committed == 0`. No special-case branch needed.

### 8. Scratch layout

The commit binary needs to stage substantially more than
rebase did because both metadata sides are now in play.
Worst-case layout for a 16 GiB qcow2 with 64 KiB clusters:

| Region              | Worst-case   | Notes                                            |
|---------------------|--------------|--------------------------------------------------|
| `HEADER_BUF`        | 64 KiB       | Overlay header (also bounce buffer).             |
| `BACKING_HEADER_BUF`| 64 KiB       | Backing header.                                  |
| `OVERLAY_STATE`     | 2.1 MiB      | Overlay L1 (256 B) + up to 32 staged L2 tables.  |
| `BACKING_STATE`     | 2.7 MiB      | Backing L1 (256 B) + up to 32 staged L2 + RT + RB offsets + RB. |
| `PLANNER_SCRATCH`   | 1 MiB        | The planner's own carve (dirty bitmap + a copy of the refblocks). |
| `OVERLAY_DATA_BUF`  | 1 MiB        | One overlay cluster for data ferry.              |
| **Total**           | **~7 MiB**   | Fits in the 12.94 MiB scratch region.            |

The carve is deliberate: each region has a fixed start, a
fixed cap, and a compile-time assert that the sum stays
below `ALLOC_HEAP_BASE`. Same shape as the rebase binary's
scratch layout.

For images larger than the worst-case-fits-in-4MiB caps,
the runner returns `ERROR_SCRATCH_TOO_SMALL` and the user
falls back to `qemu-img commit`. Document the bound.

### 9. Allocator failure mode

Same as rebase phase 3: `ERROR_REFCOUNT_EXHAUSTED` when the
backing's existing refcount blocks fill up. The refcount-
block-extension follow-up is shared with rebase.

### 10. Should the overlay-clear pass also detach the
backing reference?

`qemu-img commit` without `-d` clears the overlay's
allocation but **leaves the backing reference intact** —
the overlay still points at the backing afterwards, it's
just sparse. With `-d`, qemu-img unlinks the overlay
after commit; the rebase / detach equivalent never
involves the commit binary.

Working choice: **leave the backing reference intact**
(matches qemu-img's default). The `-d` flag is deferred to
the phase 8 host CLI as a simple post-success unlink; the
guest doesn't need to do anything.

## Execution

The phase plan recommends five steps. Each step is small
enough to review independently; consolidating into one or
two commits at the end is also fine. The step table below
is for sub-agent assignment.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | **Shipped** as `0de7e2c`. Appended six new wire codes to `CommitResult` in `src/shared/src/lib.rs` (`ERROR_OVERLAY_CORRUPT = 8` through `ERROR_INTERNAL_OVERFLOW = 13`), updated `CommitError` doc comments in `src/crates/commit/src/lib.rs` to name the destination wire code per variant, and added a `commit_result_error_codes_distinct` unit test asserting the full 0..=13 set is contiguous and each code is distinct. |
| 7b | medium | sonnet | none | **Shipped** as `b9821bb`. Scaffolded `src/operations/commit/`: Cargo.toml (`commit-op`), linker.ld + `.cargo/config.toml` copied from rebase, main.rs with `_start` + stub dispatch returning `ERROR_UNSUPPORTED_FORMAT`. Wired into workspace members, `src/build.sh`, `scripts/check-binary-sizes.sh`, `scripts/check-rust.sh`, and the Makefile's `CARGO_TOML_FILES` + `test-rust` exclusions. `commit.bin` at the stub stage: 5 KB / 384 KB. |
| 7c | high | opus | none | **Shipped** as `b02d143`. Implemented `run_qcow2`: stages overlay L1 + L2 + refcount table + backing L1 + L2 + refcount table + refblock host offsets + refcount blocks, calls `plan_commit_qcow2`, drives the per-cluster commit loop with L2-extension on the backing (open question 3 option A — allocate via the same allocator when the backing's L1 entry is zero), refuses compressed L2 entries and cluster-size mismatches, performs the batched overlay-clear pass via `write_input_sector(0, ...)`, flushes dirty backing L2 / L1 / refcount blocks, and re-reads the backing header for the `HeaderMismatch` check. Adds `map_commit_error` exhaustive mapping. Binary size at this step: `commit.bin` = 17 KB / 384 KB. |
| 7d | medium | sonnet | none | **Shipped** as `db3ed0d`. Implemented `run_vmdk`: stages overlay GD + allocated GTs + backing GD + allocated GTs, calls `plan_commit_vmdk`, initialises the EOF allocator via `BackingGrainAllocationState::at_eof`, drives the per-grain commit loop, skips grains whose covering backing GDE is zero (GD-extension follow-up), performs the overlay-clear pass via `write_input_sector(0, ...)` zeroing each GTE (no refcount entries on vmdk), flushes dirty backing GTs, and re-reads the backing header. Reuses the qcow2 runner's scratch carve and the `StagedL2` shape (aliased as `StagedGt`). Binary size after both runners: `commit.bin` = 26 KB / 384 KB. |
| 7e | low | sonnet | none | **Partial.** Pre-commit clean. Master plan + phase plan updated to reflect all five steps shipped. |

## Agent guidance

### Execution model

Same model as phases 1–6: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should also use opus when working on step 7c because the
two-sided metadata staging + the per-cluster loop +
the L2-extension case + the overlay-clear pass all carry
real cognitive load.

### Planning effort

The master plan flagged this phase as **medium effort**.
Within the phase, 7c (qcow2 commit runner) is high effort;
7d (vmdk) is medium; the rest are low.

### Step ordering

Strict dependency: 7a → 7b → 7c → 7d → 7e. 7c and 7d are
sequential because they share the `_start` dispatch
plumbing and the `map_commit_error` helper; landing 7c
first lets 7d be a close copy with the qcow2-specific
parts swapped for vmdk. The alternative — landing 7d first
with a shorter implementation — produces an interim commit
that doesn't build (no `run_qcow2`).

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make check-binary-sizes` reports the new binary
      under 384 KB (target: under 100 KB).
- [ ] `make test-rust` passes (and the new tests are
      actually exercised, not silently skipped).
- [ ] `pre-commit run --all-files` clean.
- [ ] Patch ordering matches the master plan's section
      "Atomicity and ordering" (data into backing first,
      backing metadata next, overlay metadata last).
- [ ] Error codes from the planner crate are exhaustively
      mapped — no `_ => ERROR_INTERNAL_OVERFLOW` catch-all.
- [ ] No new `unsafe` outside the scratch-region setup
      and the call-table dispatch. The same lifetime /
      pointer hygiene rebase uses applies.
- [ ] For the `HeaderMismatch` defensive read-back: the
      re-parsed backing header's `virtual_size`,
      `cluster_size`, and `l1_table_offset` match the
      planner's values.

## Administration and logistics

### Success criteria

Phase 7 is complete when:

* `src/operations/commit/` exists, builds, and produces a
  `commit.bin` that fits the 384 KB cap.
* qcow2 commit works end-to-end on a hand-crafted test
  case (verified manually via the phase 8 VMM wiring;
  end-to-end tests sit in phase 9).
* vmdk commit works on a monolithicSparse overlay + backing
  pair.
* `CommitError` → `CommitResult::ERROR_*` mapping is
  exhaustive.
* The overlay-clear pass uses `write_input_sector(0, ...)`
  for every cleared L2 entry / refcount entry / GTE — the
  call-table primitive added in phase 1 specifically for
  commit has its first real consumer here.
* `pre-commit run --all-files`, `make instar`,
  `make check-binary-sizes`, `make test-rust` all pass.
* The execution-table row for phase 7 in
  `PLAN-rebase-commit.md` is marked complete with the
  shipping commit hashes.

### Future work created by this phase

Anticipated; the implementation may surface more.

- **Cluster-size mismatch between overlay and backing**
  (open question 2). v1 refuses; v1.1 needs to handle
  sub-cluster offsets at the backing's L2 lookup time.
- **L2-extension on the backing** (open question 3). If 7c
  takes option B (defer L2-extension) due to binary-size
  pressure, file a follow-up to add it. Likely shares the
  refcount-extension follow-up timing.
- **Compressed-cluster handling on the overlay** (phase 6
  open question 7). Currently the commit guest binary
  returns `ERROR_UNSUPPORTED_FORMAT` when it encounters a
  compressed L2 entry. Adding the `decompress` feature to
  the commit binary's qcow2 dependency unblocks this; the
  planner itself is unaffected.
- **vmdk GD extension on the backing** (phase 6 open
  question follow-up). v1 only writes grains into GTs the
  backing already has; allocating a fresh GT (and bumping
  the backing's GD entry) when the covering GDE is zero is
  a follow-up. Mirrors rebase phase 2 step 2e's open
  follow-up.
- **`-d` (drop the overlay after commit)**. Handled at the
  phase 8 host CLI as a post-success unlink; the guest
  doesn't change. Track the host-side implementation under
  phase 8.
- **"Skip when the backing chain already provides the same
  data" mode**. Master plan's "skip clusters whose overlay
  value equals what the backing chain already returns"
  variant; needs the rebase phase 3 step 3f
  `read_chain_cluster` helper promoted to a shared crate.
  Out of scope for v1.

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
