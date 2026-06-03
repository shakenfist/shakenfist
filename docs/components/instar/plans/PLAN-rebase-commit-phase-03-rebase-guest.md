# PLAN-rebase-commit phase 03: rebase guest binary

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
resize guest binary at `src/operations/resize/src/main.rs`,
the shared types in `src/shared/src/lib.rs`, the rebase
planner crate in `src/crates/rebase/`, the chain-walking
helpers in `src/operations/info/src/main.rs` and
`src/operations/check/src/main.rs`, and the call-table
documentation in `ARCHITECTURE.md` and `AGENTS.md`. Ground
your answers in what the code actually does today.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). This
phase is the third of twelve.

I prefer one commit per logical step. The step table below
identifies seven steps; this phase can land step by step or
as a single consolidated commit.

## Situation

Phase 1 shipped the shared ABI (`RebaseConfig`, `RebaseResult`,
`send_rebase_result`, `write_input_sector` in `CallTable`).
Phase 2 shipped the planner crate `src/crates/rebase/`:

- `plan_rebase_qcow2(opts, scratch)` covers unsafe and safe
  modes for qcow2, with caveats listed in the phase 2 plan
  (in-place rewrite only, `refcount_bits == 16` only).
- `plan_rebase_vmdk(opts, scratch)` covers unsafe mode only;
  safe mode is deferred (step 2e).
- `allocate_overlay_cluster_qcow2(context, state)` is the
  pure allocator the safe-mode comparison loop calls when
  it needs to copy a cluster from the old chain into the
  overlay.

Phase 3 delivers the guest binary at `src/operations/rebase/`
that consumes `RebaseConfig` from `OPERATION_CONFIG_ADDR`,
reads the overlay header, populates the planner opts, calls
the planner, drives the resulting plan (unsafe) or
comparison loop (safe), and reports the outcome via
`send_rebase_result` and `send_complete`.

The relevant existing infrastructure this phase builds on:

- **Resize guest binary**
  (`src/operations/resize/src/main.rs`). The closest
  comparable shipped binary: same in-place mutation shape
  (output device opened RW; no separate input device for
  the file being mutated). Structural template for the
  rebase `_start`, the scratch-region carve at
  `SCRATCH_MEM_BASE`, the `apply_plan` patch loop, the
  `write_byte_range` sector-aligned write helper, and the
  per-format dispatch in `_start`. Resize ships at ~73 KB,
  well under the 384 KB operation cap.
- **Phase 2 planner crate** (`src/crates/rebase/`). The
  guest binary consumes `plan_rebase_qcow2`,
  `plan_rebase_vmdk`, and (for safe mode)
  `allocate_overlay_cluster_qcow2`, `RebaseQcow2SafeContext`,
  `AllocationState`. Per-format opts shapes are at
  `src/crates/rebase/src/qcow2.rs:42` and
  `src/crates/rebase/src/vmdk.rs:23`.
- **Call-table primitives**
  (`src/shared/src/lib.rs:543–765`). Both modes use
  `read_output_sector`, `write_output_sector`,
  `get_output_capacity`, `get_output_sector_size`. Safe
  mode additionally uses `read_input_sector`,
  `get_input_device_count`, `get_input_capacity`,
  `get_input_sector_size`. Rebase does **not** use
  `write_input_sector` — that primitive landed in phase 1
  for commit's overlay-clear pass and is unused here.
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
  `CHAIN_CONFIG_ADDR`). For safe mode, the VMM populates
  this with per-device metadata (format, virtual size,
  cluster size) for every input device it attached. The
  guest can iterate it to walk the old and new backing
  chains.
- **Chain-walking precedents**
  (`src/operations/info/src/main.rs`,
  `src/operations/check/src/main.rs`). Both read input
  device 0 via `read_input_sector(0, ...)`; check
  additionally uses `get_chain_config` to walk the chain
  metadata. Convert is the most thorough chain-reading
  consumer (used during backing-chain flattening) — see
  `src/operations/convert/src/main.rs`.

## Mission and problem statement

After phase 3 lands:

1. A new operation crate `src/operations/rebase/` exists,
   declared in the workspace `src/Cargo.toml`. It produces a
   guest binary `rebase.bin` that loads at
   `OPERATION_LOAD_ADDR`, weighs well under 384 KB, and is
   the entry point the VMM launches when the user runs
   `instar rebase` (phase 4 wires the host CLI).

2. The binary's `_start`:
   - Validates the call table at `CALL_TABLE_ADDR`.
   - Reads `RebaseConfig` from `OPERATION_CONFIG_ADDR` and
     validates its magic.
   - Reads the overlay header (first sector) via
     `read_output_sector(0, ...)`.
   - Dispatches on `config.overlay_format`:
     `Qcow2` → `run_qcow2(call_table, config)`;
     `Vmdk4` → `run_vmdk(call_table, config)`;
     anything else → result with
     `ERROR_UNSUPPORTED_FORMAT`.
   - Sends the result via `send_rebase_result` and signals
     completion via `send_complete`.

3. The qcow2 unsafe-mode path:
   - Parses the overlay header via
     `qcow2::QcowHeader::parse`.
   - Populates `Qcow2RebaseOpts` with the parsed header,
     overlay file size (from `get_output_capacity *
     sector_size`), the new backing path slice from
     `config.new_backing_path[..config.new_backing_path_len]`,
     and `mode = RebaseMode::Unsafe`.
   - Calls `plan_rebase_qcow2(&opts, scratch)`. The result
     is `Qcow2RebaseOutput::Unsafe { plan }`.
   - Applies the plan's patches via a `write_byte_range`
     loop modelled on `apply_plan` from resize.
   - Builds a `RebaseResult` with `mode = MODE_UNSAFE`,
     `error = ERROR_OK`, and zero copy counters.

4. The qcow2 safe-mode path:
   - Parses the overlay header and additionally reads:
     - The refcount table (`refcount_table_clusters *
       cluster_size` bytes at
       `refcount_table_offset`).
     - The refcount blocks pointed to by the table
       entries (concatenated into scratch in table
       order).
     - The host file offset of each refcount block (kept
       alongside the staged blocks; used at the end of
       the safe-mode loop to flush dirty blocks back to
       the file).
   - Populates `Qcow2RebaseOpts` with the safe-mode fields
     (`refcount_table`, `refblock_host_offsets`,
     `refcount_blocks`, `refblock_count`) and
     `mode = RebaseMode::Safe`.
   - Calls `plan_rebase_qcow2(&opts, scratch)`. The result
     is `Qcow2RebaseOutput::Safe { context,
     deferred_metadata }`.
   - Drives the safe-mode comparison loop (see
     section "Safe-mode comparison loop" below).
   - Once the loop completes, flushes the dirty refcount
     blocks back to the overlay via `write_byte_range`,
     then applies the `deferred_metadata` patches.
   - Builds a `RebaseResult` with `mode = MODE_SAFE`,
     `clusters_copied = state.allocated`,
     `bytes_copied = state.allocated * cluster_size`,
     `error = ERROR_OK`.

5. The vmdk unsafe-mode path:
   - Reads the overlay header (first sector), parses via
     `vmdk::Vmdk4HeaderFull::parse`.
   - Reads the descriptor region at
     `header.desc_offset_sectors * 512`, length
     `header.desc_size_sectors * 512`.
   - Reads the new backing's descriptor via
     `read_input_sector(config.new_chain_first, ...)` to
     extract the parent CID (the planner's opts require
     `new_parent_cid` from the host side; if the new
     backing is non-vmdk it falls back to the qemu-img
     sentinel 0xffffffff in the rewriter).
   - Populates `VmdkRebaseOpts` with the existing
     descriptor bytes, slot size, slot offset, and new
     parent CID.
   - Calls `plan_rebase_vmdk(&opts, scratch)`. The result
     is `VmdkRebaseOutput::Unsafe { plan }`.
   - Applies the plan's single descriptor-rewrite patch.

6. The vmdk safe-mode path is **out of scope**. The phase 2
   step 2e was deferred; until it lands, vmdk safe-mode
   rebase returns `ERROR_UNSUPPORTED_FORMAT`. Phase 3
   guest binary documents this gap in its error message
   and the master plan's Future Work section already
   tracks it.

7. `RebaseError` variants from the planner crate are mapped
   to `RebaseResult::ERROR_*` codes via a `map_rebase_error`
   helper modelled on resize's `map_error`. Phase 2 surfaced
   that the wire-level error set (7 codes, 0–6) is smaller
   than the planner's `RebaseError` variant set (14
   variants). Phase 3 adds the missing wire codes to
   `RebaseResult` in `src/shared/src/lib.rs` so the host
   can render meaningful messages for each failure mode
   (see open question 4).

8. The binary builds clean, lints clean, ships under 100 KB,
   and `make check-binary-sizes` is green.

Nothing in phase 3 changes user-visible behaviour because the
host CLI doesn't exist yet — that's phase 4. The phase 3
deliverable is a binary the phase 4 VMM can launch.

## Open questions

### 1. Defer safe-mode entirely to a phase 3 follow-up?

Phase 2's safe-mode planner has narrow applicability:

- qcow2 `refcount_bits == 16` only.
- No long-path relocation.
- vmdk safe-mode planner not implemented (step 2e deferred).

This means safe-mode rebase covers only qcow2 v3 images with
default refcount widths and short backing paths. Real-world
images mostly match this; v1 unsafe-mode rebase covers the
same image set with the added "trust me" semantics.

Working choice: **ship both modes in phase 3 for qcow2**;
defer vmdk safe-mode to a phase 3 follow-up that pairs with
phase 2 step 2e. The qcow2 safe-mode path is the load-bearing
correctness work and is worth landing now to validate the
planner contract end-to-end.

Alternative: ship unsafe-mode only in phase 3, defer
safe-mode to a follow-up. Smaller scope, faster to land,
but leaves the planner's safe-mode contract unexercised.

### 2. How should the guest decode the overlay's L2 entries
in safe mode?

For each guest cluster, the comparison loop must check
whether the overlay already owns the cluster (via its L2
entry). The L2 lookup logic — L1 entry to L2 table offset,
L2 entry to cluster offset — already exists in the qcow2
crate via `read_l2_entry` and friends, but those helpers
take a `CallTable` and do their own I/O via
`read_input_sector`.

For rebase the overlay is the *output* device, not an
input. Options:

- **A**: Promote `read_l2_entry_via_io` to take a generic
  read function or generalise the existing helper to read
  from either input or output (small refactor in qcow2).
- **B**: Add an alternative `read_l2_entry_from_output` to
  the qcow2 crate that uses `read_output_sector`.
- **C**: Have the guest stage the L1 table and the entire
  L2 region into scratch up front (same way it stages
  refcount blocks), then decode L2 entries from scratch
  without further I/O.

Working choice: **C**, modelled on the refcount-block
staging. It keeps the planner crate pure and lets the
guest read everything it needs in one pre-loop pass. The
total L2 size for a typical 16 GiB qcow2 with 64 KiB
clusters is 2 MiB, which fits comfortably in scratch
(12.9 MiB total). The phase plan budgets 4 MiB of scratch
for staged metadata, same as resize.

Document a follow-up to switch to option A if the L2
staging proves too memory-heavy for larger images.

### 3. How does the safe-mode loop read the old and new
chains at a given guest offset?

For each guest cluster the loop needs:

- Old chain's data at guest offset `cluster_idx *
  cluster_size`, walked through devices
  `config.old_chain_first .. config.old_chain_first +
  config.old_chain_count`.
- New chain's data at the same offset, walked through
  devices
  `config.new_chain_first .. config.new_chain_first +
  config.new_chain_count`.

The walking logic is "for each device in the chain, decode
the device's format-specific L2 to see if it has the
cluster; if yes return its data; else continue to the next
device".

Existing chain-reading helpers:

- `src/operations/convert/src/main.rs` does the deepest
  chain-reading — chain flattening reads the union of all
  chain devices' allocated data.
- `src/operations/info/src/main.rs` reads device 0 only
  but understands the chain through `get_chain_config`.

Working choice: factor a small `read_chain_cluster`
helper into the rebase operation binary that takes
`(call_table, chain_first_device_idx, chain_count,
chain_config, guest_offset, cluster_size, out_buf)` and
walks the devices until it finds the cluster or returns
zeros. Borrow the structure from convert's chain
flattening but specialise for the rebase use case (it
only needs to read a single cluster at a time, not
flatten a full file). If the resulting helper is generic
enough, file a follow-up to promote it to a shared crate.

### 4. Expand `RebaseResult::ERROR_*` codes to cover all
`RebaseError` variants?

Phase 1 defined 7 wire codes (0 = OK plus 6 error codes).
Phase 2's planner has 14 variants. Phase 3 must map each
variant to a wire code so the host can render meaningful
messages.

Working choice: **add the missing codes** in
`src/shared/src/lib.rs`. Append-only, no breakage:

- `ERROR_OVERLAY_CORRUPT = 7`
- `ERROR_BACKING_PATH_TOO_LONG = 8`
- `ERROR_SCRATCH_TOO_SMALL = 9`
- `ERROR_REFCOUNT_EXHAUSTED = 10`
- `ERROR_DESCRIPTOR_TOO_LARGE = 11`
- `ERROR_PARSE_FAILED = 12`
- `ERROR_INTERNAL_OVERFLOW = 13`

The mapping is then 1:1 with no catch-all losses. Step 3a
adds these to shared and the corresponding unit tests
under the existing `mod tests` block.

### 5. What is the contract on chain-device ordering?

Phase 1's `RebaseConfig` carries `old_chain_first`,
`old_chain_count`, `new_chain_first`, `new_chain_count`,
implying the VMM attaches the old chain at one contiguous
range of input slots and the new chain at another. The
working assumption is:

- Input slot 0 may or may not be used depending on how
  phase 4 lays things out; the guest reads slot indices
  from the config and does not assume slot 0.
- Within each chain, slot N is closer to the top (the
  overlay's immediate parent) and higher slots are
  further away (deeper ancestors).
- The chain config at `CHAIN_CONFIG_ADDR` carries
  per-slot metadata in the same order.

Document this contract explicitly in the phase 4 plan so
the VMM and the guest agree.

### 6. What sector size does the guest assume?

Resize uses `(call_table.get_output_sector_size)()` and
threads that everywhere. Rebase should do the same. v1
assumes the output and all input devices use the same
sector size (which is typically true since the VMM
attaches all of them from the same backing-store layer
with the same default).

If they differ, the planner currently assumes a single
`sector_size` field from `RebaseConfig.sector_size`. The
guest should populate that from the output device's
sector size and trust it. Cross-device sector-size
heterogeneity is a follow-up.

### 7. Failure recovery posture

Resize-style "no rollback, partial failure is detectable
by `instar check`" applies here. If the safe-mode loop
fails after copying some clusters but before flushing
refcount blocks, the overlay is in a transient state:
some clusters are reachable (because refcount blocks
were flushed earlier in the order — see below), some
aren't, and the header still points at the old backing.

Working choice: keep the same ordering as resize and as
the planner expects:

1. Allocate clusters and write their data into the
   overlay (data first).
2. Flush dirty refcount blocks (refcount integrity
   second).
3. Apply the deferred metadata patches (header rewrite
   last).

On mid-step failure: the overlay is consistent under
step 1 (no metadata changed yet, allocated data is just
leaked clusters) or after step 2 (refcounts may show
the new clusters as allocated but the L2 entries from
step 3 didn't go through). `instar check` reports the
inconsistency.

Document this in the user-facing docs (phase 12).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Shipped** as `f96833a`. Extend `RebaseResult` in `src/shared/src/lib.rs`. Append the 7 new error code constants from open question 4 (`ERROR_OVERLAY_CORRUPT = 7` through `ERROR_INTERNAL_OVERFLOW = 13`) inside the existing `impl RebaseResult` block. Update the doc comments on `RebaseError` in `src/crates/rebase/src/lib.rs` to cross-reference the new wire codes. Add a unit test to the existing `mod tests` block in `src/shared/src/lib.rs` asserting the new constants are distinct from the old ones. No behavioural changes. |
| 3b | medium | sonnet | none | **Shipped** as `9dd1fa3`. Scaffold `src/operations/rebase/`: `Cargo.toml` (mirror `src/operations/resize/Cargo.toml`); `.cargo/config.toml` and `linker.ld` matching resize; `src/main.rs` with `_start` + scratch layout + stub runners. Workspace members in `src/Cargo.toml`, `src/build.sh` build steps, `scripts/check-binary-sizes.sh` op list, `scripts/check-rust.sh` clippy exclusion list, `Makefile` CARGO_TOML_FILES + test-rust exclusions. |
| 3c | high | opus | none | **Shipped** as `fd3e338`. Implement `run_qcow2_unsafe` in `src/operations/rebase/src/main.rs`. Reads overlay header, populates `Qcow2RebaseOpts` with `mode=Unsafe`, calls `plan_rebase_qcow2`, dispatches resulting `Unsafe { plan }` through new `apply_rebase_plan` helper. Adds `map_rebase_error` exhaustive mapping. |
| 3d | medium | sonnet | none | **Shipped** as `a47f48d`. Implement `run_vmdk_unsafe`. Parses overlay header, reads descriptor into EXISTING_STATE, probes the new chain's first input device for parent CID via new `extract_parent_cid_from_input` helper (with new `read_input_byte_range` companion to read across sectors against an input device), populates `VmdkRebaseOpts`, calls `plan_rebase_vmdk`, applies the resulting single-patch plan. |
| 3e | high | opus | none | **Shipped** as `90deff9`. Implement `run_qcow2_safe` in `src/operations/rebase/src/main.rs`. Stages overlay L1 + L2 tables + refcount table + refcount-block host offsets + refcount blocks into a new sub-carve of `EXISTING_STATE`, passes them through to `plan_rebase_qcow2(mode = Safe)`, initialises `qcow2::ChainStates` against the old and new chain slots, runs a per-cluster comparison loop that allocates fresh data clusters (and fresh L2 tables when the covering L1 entry is zero) via `allocate_overlay_cluster_qcow2`, writes old-chain content into the allocations, flushes dirty L2 tables → L1 (if grown) → refcount blocks → deferred metadata patches in that order. v1 caps: `refcount_bits == 16`, `cluster_size ≤ COMPARE_BUF_SIZE = 1 MiB`, `staged_l2_count ≤ 256`, `refblock_count ≤ 2048`. |
| 3f | medium | sonnet | none | **Shipped** as `74fac82`. Add `read_chain_cluster` helper in `src/operations/rebase/src/main.rs`. Walks the backing chain at `[chain_first, chain_first + chain_count)` for a single overlay-cluster-sized read at a guest offset, returning the first chain member that owns the cluster or zero-filling when no member does. Pre-flights every chain member's format and refuses anything other than qcow2 / raw (the rebase binary doesn't enable the qcow2 crate's `vmdk-input` / `vhd-input` / `vhdx-input` features so the underlying chain reader would otherwise misread non-qcow2 members as raw). Carves new scratch regions `CHAIN_CACHES` (per-device L1/L2 caches) and `COMPARE_BUFS` (two cluster-sized scratch buffers, reserved for 3e), with a compile-time check that the carve still fits below `ALLOC_HEAP_BASE`. v1 follow-up tracks promotion to a shared crate once commit (phase 7) needs the same primitive. |
| 3g | low | sonnet | none | **Partial.** Run `pre-commit run --all-files`. Verify the new binary builds and is well under 384 KB. Update the execution table row for phase 3 in `docs/plans/PLAN-rebase-commit.md` to mark each shipping commit. Document the 3e + 3f deferrals in the master plan's Future Work section. |

## Agent guidance

### Execution model

Same model as phases 1 and 2: implementation work runs in
the management session unless explicitly delegated. Use
opus for steps 3c, 3e, and 3f because they cross the qcow2
header layout, the planner contract, the chain config, and
the patch ordering simultaneously.

### Planning effort

The master plan flagged this phase as **medium effort**.
Inside the phase the qcow2 safe-mode path (step 3e) is
high-effort; the rest are medium-low.

### Step ordering

Strict dependency: 3a → 3b → (3c | 3d) → 3e → 3f → 3g.
3c and 3d can interleave because they touch different
format paths but share the `apply_plan` helper introduced
in 3c. 3e and 3f are coupled: 3e is the consumer of the
helper from 3f, but writing 3e first as a stub with a
`todo!()` for the chain read lets the structural review
happen before the helper detail lands.

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed.
- [ ] No unrelated files modified.
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make check-binary-sizes` reports the new binary
      under 384 KB.
- [ ] `pre-commit run --all-files` clean.
- [ ] Patch ordering matches resize's pattern (data first,
      metadata last) in the comparison loop.
- [ ] Error codes from the planner crate are exhaustively
      mapped — no `_ => ERROR_INTERNAL_OVERFLOW` catch-all.
- [ ] No new `unsafe` outside the scratch-region setup
      and the call-table dispatch. The same lifetime /
      pointer hygiene resize uses applies.

## Administration and logistics

### Success criteria

Phase 3 is complete when:

* `src/operations/rebase/` exists, builds, and produces a
  `rebase.bin` that fits the 384 KB cap.
* qcow2 unsafe-mode rebase works end-to-end on a
  hand-crafted test case (verified via the test setup
  from phase 5; not part of this phase but should be
  testable manually with the VMM host wiring from phase
  4).
* qcow2 safe-mode rebase works against an overlay with
  the v1 constraints (`refcount_bits == 16`, no LUKS,
  short backing path, in-place rewrite).
* vmdk unsafe-mode rebase works on a monolithicSparse
  overlay.
* `RebaseError` → `RebaseResult::ERROR_*` mapping is
  exhaustive.
* `pre-commit run --all-files`, `make instar`,
  `make check-binary-sizes` all pass.
* The execution-table row for phase 3 in
  `PLAN-rebase-commit.md` is marked complete with the
  shipping commit hashes.

### Future work created by this phase

- vmdk safe-mode rebase guest path. Blocked on phase 2
  step 2e (vmdk safe-mode planner + grain allocator).
- Promotion of `read_chain_cluster` to a shared crate if
  commit (phase 7) or any later operation needs the same
  primitive. Track as a refactor once the second consumer
  arrives.
- Cross-device sector-size heterogeneity (open question
  6). Out of scope until the test infrastructure can
  generate images with mismatched sector sizes.
- Option A in open question 2 — switching from staged-L2
  to read-on-demand L2 if the staging memory footprint
  becomes a constraint. Not needed for v1 but worth
  flagging.

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
