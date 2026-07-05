# PLAN-bitmap phase 04: the guest operation

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the actual
code and ground every claim in it — in particular the guest-op
orchestration in `src/operations/snapshot/src/main.rs` (the closest
analog: it stages a qcow2 directory + tables + refcount blocks,
mutates them, and writes them back with fsync-ordered groups and a
header commit point), the `check --repair` crash-safe dance in
`src/operations/check/src/main.rs` (`repair_all_qcow2`), the
Phase-3 planner crate `src/crates/bitmap/`, the Phase-2 ABI
(`BitmapConfig`/`BitmapResult`), and the Phase-1 primitives in
`src/crates/qcow2/src/bitmap.rs`. Do not speculate when you could
read. Consult the qcow2 spec / `block/qcow2-bitmap.c` for on-disk
semantics. Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–3 are complete
([parse](/components/instar/plans/PLAN-bitmap-phase-01-parse/),
[ABI](/components/instar/plans/PLAN-bitmap-phase-02-abi/),
[planner](/components/instar/plans/PLAN-bitmap-phase-03-planner/)). This is the fourth of
ten phases. Templates: `src/operations/snapshot/` (orchestration)
and `src/operations/amend/` (op skeleton).

One commit per logical change, minimum one per phase; each commit
must build, pass the gate, and explain what changed and why.

## Situation

This phase builds `src/operations/bitmap/`, the `no_std` / `no_main`
KVM-guest binary `bitmap.bin` that actually mutates the image. It
ties together everything so far: it reads `BitmapConfig`, stages
the qcow2 header + bitmaps directory + refcount blocks into
scratch, drives the ordered action list through the Phase-3 crate,
performs the on-disk work the crate cannot (freeing/allocating
**data** clusters, zeroing table clusters), writes everything back
under a crash-safe **autoclear-bit dance**, and returns a
`BitmapResult`.

**Key reality — no functional test until Phase 5.** There is no
host CLI (`Commands::Bitmap` is Phase 5) to drive this binary, so
phase 4 cannot be exercised end-to-end here. Its gate is: **builds,
fits the 376 KiB op cap, `make lint` clean, and passes management
review for correctness.** Functional validation lands in Phase 5
(host CLI) and Phases 6–7 (rust round-trip + `qemu-img`-oracle
integration tests, including post-op `qemu-img check`). Plan and
review accordingly — this is the amend/snapshot precedent.

Grounding (verified against HEAD `2b5d79e`; see the phase-4
research notes folded into the steps):

- **Op skeleton (`src/operations/amend/`)**: `Cargo.toml` (package
  `amend-op`, `[[bin]] name = "amend"`, deps `shared` + the planner
  crate + `qcow2 { features=["create"] }`, `[profile.release] panic
  = "abort", opt-level = "z", lto = true`); `.cargo/config.toml`
  (target `x86_64-unknown-none`, `-Toperations/<op>/linker.ld`,
  `relocation-model=static`, `build-std=["core"]`); `linker.ld`
  (`OPERATION_BASE = 0x22000`, copied verbatim); `src/main.rs`
  (`#![no_std] #![no_main]`, panic handler, `#[no_mangle] _start()
  -> u64`, `validate_call_table!(ct, "bitmap")`, read
  `&*(OPERATION_CONFIG_ADDR as *const BitmapConfig)`, `is_valid()`,
  … work …, `(ct.send_bitmap_result)(&result)`,
  `(ct.send_complete)(b"bitmap\0", bytes, ok)`; the op returns, core
  halts). **The `#[inline(never)]` on the per-format qcow2 runner
  (amend `:222`) is a load-bearing codegen-miscompile workaround
  (x86_64-unknown-none + opt-level=z + lto); follow it.**
- **Device attach — INPUT-RW, like snapshot** (resolves Phase-3
  OQ7, overriding the Phase-2 note's offhand "output" wording).
  The host (Phase 5) will `BackingStore::open_rw_existing`, build a
  `VirtioBlockDevice::new(.., false /*read-write*/, ..)`, and
  `device_set.add_device(dev, true /*is_input*/)` at slot 0
  (snapshot's `run_snapshot_mutating_guest`,
  `src/vmm/src/main.rs:12241-12265`). The guest uses
  `read_input_sector(0,..)` / `write_input_sector(0,..)` /
  `fsync_input(0)` — all present in `CallTable`
  (`src/shared/src/lib.rs:725/926/975`). This phase's op is written
  against those.
- **I/O + staging helpers to copy from snapshot**
  (`src/operations/snapshot/src/main.rs`): `read_input_byte_range`
  (`:256`, sub-sector RMW read), `write_input_byte_range` (`:293`,
  sub-sector RMW write via a bounce buffer), `stage_refblocks`
  (`:596`, reads the refcount table then the refblocks, enforces
  the **v1 contiguity gate** `:620-647`), and `rb_lookup` /
  `refblock_byte_offset_for_cluster` (`:677`). The scratch model:
  named regions carved forward from `SCRATCH_MEM_BASE` with a
  compile-time assert that the top stays below `ALLOC_HEAP_BASE`
  (`:136-180`); usable budget ≈ 12.4 MiB.
- **Refcount/alloc reuse** — the op calls the same
  `snapshot::qcow2` primitives the Phase-3 crate does (the crate’s
  `action_*` mutate refblocks; the op also directly frees **data**
  clusters via `set_refcount_in_block(.., 0)` and allocates merge
  data clusters via `alloc_*`). Both crate and op share one
  `AllocCursor`.
- **Crash-safe dance model — `check --repair`**
  (`src/operations/check/src/main.rs`, `repair_all_qcow2` `:3737+`):
  pre-flight guards (refuse ⇒ image byte-identical) → set the guard
  header bit + `fsync_input(0)` → mutate structures with fsync
  barriers between passes → clear the guard bit + fsync only on
  success. bitmap inverts the polarity: it **clears** the autoclear
  bitmaps bit (offset 88, `AUTOCLEAR_BITMAPS_BIT = 1<<0`) as the
  guard, rewrites, then **sets** it back on success. A crash mid-
  write leaves the bit clear ⇒ a subsequent open treats the (half-
  written) bitmaps as stale/absent — the semantically correct
  outcome (mirrors qemu's `update_ext_header_and_dir_in_place`).
- **Phase-3 crate API** the op drives: `directory::{find_bitmap,
  build_directory_*}`, `action::{BitmapGeometry, ActionOutcome,
  action_add/remove/clear/enable/disable}` (ActionOutcome carries
  `new_dir_len`, `new_nb_bitmaps`, `extension_now_present`,
  `table_clusters_to_zero[..]`, `freed_table_offset/size`,
  `zero_freed_table`), and `merge::{merge_validate, MergeSpec,
  merge_cluster_action, MergeClusterAction, or_bitmap_data}`. See
  the "Execution notes / 3c–3d design decisions" in the Phase-3
  plan for the crate/guest split contract.
- **Registration checklist** (add a new op): `src/Cargo.toml`
  members += `"operations/bitmap"`; `src/build.sh` build+objcopy
  block + `cp` + inline size guard; `scripts/check-binary-sizes.sh:119`
  op list += `bitmap` (cap = `0x80000 - 0x22000` = **376 KiB**,
  `.bss`-inclusive ELF extent — scratch is external, but op
  `.text`/`.rodata`/`.data`/`.bss` count); `Makefile:508-509`
  `--exclude bitmap-op`.

## Mission and problem statement

Build `src/operations/bitmap/` producing `bitmap.bin`, registered
and size-checked, such that when driven by `BitmapConfig` it:

1. Validates the call table and config; refuses non-qcow2
   (`ERROR_UNSUPPORTED_FORMAT`) — qcow2-only.

2. Reads the full header cluster, parses the header + bitmaps
   extension, and **gates** (refuse before any write, image
   byte-identical): qcow2 v2 ⇒ `ERROR_UNSUPPORTED_VERSION`;
   `INCOMPAT_DIRTY`/`INCOMPAT_CORRUPT` (or the dirty/corrupt bits)
   ⇒ refuse; `refcount_bits != 16` ⇒ `ERROR_UNSUPPORTED_REFCOUNT_
   WIDTH`; a bitmaps extension present but autoclear-inconsistent ⇒
   refuse unless the only actions are `--remove` (qemu allows
   removing inconsistent bitmaps). Cross-check the host-probed
   `BitmapConfig` fields (cluster_size, virtual_size, version,
   autoclear/incompatible features, nb_bitmaps, directory
   offset/size) against the guest's own re-parse ⇒
   `ERROR_HEADER_MISMATCH` on disagreement.

3. Stages the bitmaps **directory** (bounded by a `BITMAP_DIR_LIMIT`
   — reject larger with `ERROR_SCRATCH_TOO_SMALL`), the refcount
   **table**, and the **refcount blocks** (copy snapshot's
   `stage_refblocks` + the v1 contiguity gate).

4. Applies the **ordered action list** (`config.actions[..num_
   actions]`, opcodes `ACTION_ADD..ACTION_MERGE`) in order,
   double-buffering the directory (each action reads the current
   directory buffer, writes the next), threading the refblock
   buffer + a single `AllocCursor`. For each action it drives the
   Phase-3 crate and performs the on-disk work the crate cannot:
   - **add** — zero-fill the newly allocated table clusters
     (`ActionOutcome.table_clusters_to_zero`) on disk.
   - **remove** — walk the removed bitmap's on-disk table
     (`freed_table_offset/size`, staged + decoded via Phase-1
     `decode_bitmap_table_entry`) and free its **data** clusters
     (`set_refcount_in_block(.., 0)` for each `Allocated` entry);
     the crate already freed the table clusters.
   - **clear** — free the data clusters (as remove) and zero the
     table clusters (`zero_freed_table`), leaving the entry.
   - **enable/disable** — nothing on-disk beyond the directory
     rewrite (no alloc/free).
   - **merge** — the full on-disk orchestration (step 4d).

5. Writes everything back under the crash-safe autoclear dance
   (Open question 1), with fsync barriers modeled on `check
   --repair`, then removes the extension + clears autoclear if the
   last bitmap was removed.

6. Returns a `BitmapResult` (`error`, last `action`,
   `actions_applied`, `resulting_nb_bitmaps`) and calls
   `send_complete`.

**Out of scope:** the host CLI / `run_bitmap` / device attach
(Phase 5); rust round-trip + integration tests (Phases 6–7);
cross-file `-b` merge (deferred). Refcount-table growth remains out
of scope everywhere (refuse `RefcountExhausted` ⇒ `ERROR_NO_SPACE`).

## Open questions

### 1. Store-path strategy + the autoclear dance ordering (the load-bearing correctness decision)

qemu uses two store paths: an in-place directory rewrite guarded by
the clear→write→set autoclear dance (pure flag flips), and a
full-rewrite-to-new-clusters for content changes. instar v1 can
simplify by **always guarding the whole write-back with the
autoclear dance** and rewriting the directory **in place when it
still fits its currently-allocated cluster span, relocating to
newly-allocated directory cluster(s) only when it grows past that**:

Proposed ordering (refine against `check --repair`'s exact fsync
barriers in 4c):
1. **Pre-flight**: all gates + the whole action loop computed in
   memory (directory buffers + refblocks fully mutated); nothing
   written. A failure here ⇒ image untouched.
2. **Clear** autoclear bitmaps bit (RMW the 8 bytes @88), `fsync`.
   From here a crash leaves bitmaps ignorable.
3. Write newly-allocated clusters (zeroed table clusters for add;
   OR'd data clusters for merge) and, if the directory relocated,
   the new directory cluster(s); write the mutated **refblocks**;
   `fsync`.
4. Write the (possibly in-place) **directory** bytes and the
   **bitmaps extension** in the header (nb_bitmaps, dir_offset,
   dir_size); `fsync`. Free old clusters (freed data/table clusters
   already at refcount 0 in the written refblocks; old directory
   clusters if relocated).
5. On full success, **set** autoclear back (RMW @88), `fsync` —
   **unless the last bitmap was removed**, in which case leave
   autoclear clear and zero the extension fields (the bitmaps
   extension is gone). Done.

Confirm this ordering is crash-correct (a crash at any point leaves
either the pre-op image, with autoclear set and the old directory
intact, or an image with autoclear clear ⇒ bitmaps ignored). The
one subtlety qemu handles: the extension body and the directory may
span sectors — accept the same torn-window qemu accepts, guarded by
the autoclear bit. This is the highest-risk area; Phase 7's
post-op `qemu-img check` is the ultimate validator.

### 1b. First-add / no-extension case (resolved in 4c)

The first `--add` on an image with **no** bitmaps extension requires
inserting a new `EXT_BITMAPS` header-extension record. **4c
implemented option (a): create the extension in place.** The guest
locates the terminating `EXT_END` record (via
`qcow2::header_extension_area_end`), overwrites it with a new
`[EXT_BITMAPS][len=24][24-byte body]` record, and writes a fresh
`EXT_END` immediately after — all within the staged header cluster in
`HEADER_BUF`, then persists just the affected byte range. It is
guarded by a header-cluster room check (`8 + 24 + 8` bytes from the
old `EXT_END` offset must stay within the cluster); if there is no
room it returns `ERROR_SCRATCH_TOO_SMALL`. In practice a v3 header
(header_length 104/112) has the whole rest of the first cluster free
for extensions, so this always succeeds for realistic images. The
in-place rewrite path (extension already exists) overwrites the
24-byte body directly. `qemu-img bitmap --add` on a fresh image — the
primary use case — is therefore supported.

### 2. Merge in Phase 4, or defer? (master OQ2)

The Phase-3 crate merge logic (validate + `or_bitmap_data` +
`merge_cluster_action`) is done; only the **on-disk orchestration**
remains (step 4d): stage source + dest tables, per index apply
`MergeClusterAction` (read source data cluster, OR into dest,
allocate dest data clusters for `AllocDestFromSource`, set all-ones
table entries for `CopyAllOnes`, free obsolete dest clusters),
update the dest table entries + refcounts. It reads/writes bitmap
**data** (2 more cluster-sized scratch regions). **Recommend
attempting it as step 4d; if it balloons the binary past the
376 KiB cap or the review, defer merge to a follow-up** — the op
returns `ERROR_UNSUPPORTED_ACTION` for `ACTION_MERGE` and the other
five actions ship. Decide at 4d review.

**Resolution (4d — IMPLEMENTED, not deferred).** Merge landed. The
binary grew only marginally (`bitmap.bin` is **36 KiB / 376 KiB, 9 %**
— the crate logic was already linked for the `ERROR_UNSUPPORTED_ACTION`
stub, so the incremental cost was the orchestration alone), and the
flow reviewed cleanly. Design decisions:

- **Dedicated flow, not the 4c write-back.** Merge does **not** touch
  the directory (the dest's directory entry — its table offset/size —
  is unchanged); it mutates only the dest bitmap's **table entries**
  and **data clusters** plus the refcounts of freshly-allocated /
  freed data clusters. So it runs `run_merge` / `write_back_merge` /
  `merge_one_source` rather than 4c's directory-centric `write_back`.
  The autoclear dance is the same shape (clear @88 + fsync → mutate →
  write refblocks + fsync → set @88 + fsync); merge never removes the
  last bitmap, so autoclear is always re-set.
- **Incremental per-table-entry, no whole-table staging.** Source and
  dest tables have equal granularity + equal `table_size`
  (`merge_validate` enforces), and may be multi-cluster, so the flow
  iterates index `i` in `0..table_size`, reads the two 8-byte table
  words directly from disk (into 8-byte stack buffers), applies
  `merge_cluster_action`, and for data-cluster cases reads/OR-s/writes
  through the `DATA_A` (source) / `DATA_B` (dest) cluster scratch. Dest
  table-entry rewrites (`CopyAllOnes` → all-ones sentinel;
  `AllocDestFromSource` → new `Allocated` offset) are 8-byte writes to
  `dest_table_offset + i*8`; `CopyAllOnes` also frees any obsolete dest
  data cluster in the staged refblocks. Refblocks are flushed once,
  after the loop, under the guard (a crash leaves autoclear clear ⇒
  partial merge is safe).
- **Mixing decision (v1 restriction).** An invocation is **either**
  all-metadata-actions (the 4c path) **or** a single `ACTION_MERGE`
  (the 4d path). `run_qcow2` detects any `ACTION_MERGE` in the action
  list; if present it must be the *sole* action (`num_actions == 1` and
  it is a merge), otherwise the op refuses with
  `ERROR_UNSUPPORTED_ACTION`. qemu allows mixing merge with other
  actions in one invocation, but v1 keeps them separate (the Phase-5
  host / Phase-6-7 tests drive them separately). This can be relaxed
  later without an ABI change.
- **Multi-source decision (loop, single-`--merge`-source is the common
  case).** `run_merge` loops over `config.num_merge_sources`, extracting
  each source name from the concatenated `merge_source_pool` via the
  `merge_source_lens[k]` prefix sums; a merge with N sources is applied
  as N sequential single-source merges into the dest (they share one
  `AllocCursor` + refblocks buffer, so allocations don't collide, and
  the refblocks + autoclear are flushed once at the end). All N sources
  are `merge_validate`-d up front (before any write) so a validation
  refusal leaves the image byte-identical even for a multi-source
  request. A self-merge source (`source_name == dest_name`) is skipped
  (OR-ing a bitmap into itself is a no-op); if every source is a
  self-merge the whole invocation is a no-op success.
- **Error mapping.** Allocator `RefcountExhausted` ⇒ `ERROR_NO_SPACE`;
  `Unsupported` (width ≠ 16) ⇒ `ERROR_UNSUPPORTED_REFCOUNT_WIDTH`; a
  malformed source-name length or missing source ⇒
  `ERROR_MERGE_SOURCE_NOT_FOUND`; crate `BitmapError`s map through the
  existing `u32::from` (`IncompatibleMerge` / `BitmapInUse` /
  `BitmapNotFound` / `ParseFailed`).

### 3. Binary size (376 KiB `.bss`-inclusive cap)

The op links `qcow2` (with `create`?), `snapshot`, and `bitmap`,
plus the merge/data-staging code. `amend.bin` is ~79 KiB; bitmap
is bigger but should fit. **4a must confirm the skeleton size and
4c/4d re-check after the logic lands.** If it approaches the cap:
drop the `create` feature if unused, `#[inline(never)]` the large
runners, or (last resort) defer merge. Does the op need
`qcow2 { features=["create"] }` at all, or only the default parse
path + `bitmap`/`snapshot`? Confirm in 4a (prefer the minimal
feature set).

### 4. Scratch layout

Name regions carved from `SCRATCH_MEM_BASE` (mirror snapshot
`:136-180`), with the compile-time `<= ALLOC_HEAP_BASE` assert:
`HEADER_BUF` (1 cluster), two directory buffers (`DIR_A`/`DIR_B`
double-buffer, each `BITMAP_DIR_LIMIT` — suggest 64 KiB like
snapshot's `OLD_TABLE_LIMIT`), `RT_BUF` (refcount table),
`RB_OFFSETS`, `REFBLOCKS_BUF` (2 MiB), `TABLE_BUF` (1 cluster, for
walking a bitmap's on-disk table on remove/clear/merge), `DATA_A`/
`DATA_B` (1 cluster each, merge source+dest data), `ZERO_BUF` (1
cluster of zeros for zero-filling), `RMW_BOUNCE` (1 sector).
Confirm the total fits ≈ 12.4 MiB (it does — the big ones are
REFBLOCKS 2 MiB + a few clusters).

### 5. Idempotent / no-op actions

`enable` on an already-enabled bitmap (or `disable` on disabled) is
a harmless no-op flag write. Apply idempotently (rewrite the same
flag), matching qemu's observable behaviour; the autoclear dance
still runs. `actions_applied` counts actions that ran without
error. Confirm no divergence worth recording.

## Step-level guidance

Plan at **high** effort; this is image-mutation code where errors
corrupt data, and it cannot be functionally tested until Phase 5,
so review rigor substitutes. Skew to opus. Steps are sequenced so
each **builds and is size-checked**, even if intermediate steps
send a placeholder result.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Create `src/operations/bitmap/` (Cargo.toml `bitmap-op` / `[[bin]] name="bitmap"` / deps `shared` + `bitmap` + `qcow2` [minimal features — confirm whether `create` is needed] + `snapshot`; verbatim `[profile.release]`; `.cargo/config.toml` with `-Toperations/bitmap/linker.ld`; `linker.ld` copied verbatim). `src/main.rs`: `#![no_std] #![no_main]`, panic handler, `_start`, `validate_call_table!(ct,"bitmap")`, read `BitmapConfig`, `is_valid()` else error; dispatch on `config.target_format` (Qcow2 ⇒ a `#[inline(never)]` `run_qcow2` stub that for now returns `ERROR_UNSUPPORTED_ACTION`; else `ERROR_UNSUPPORTED_FORMAT`); send `BitmapResult` + `send_complete`. Register: `src/Cargo.toml` members, `src/build.sh` (build+objcopy+cp+inline size guard, model amend `:269-285`/`:309`/`:362`), `scripts/check-binary-sizes.sh:119` op list, `Makefile:508-509` `--exclude bitmap-op`. GATE: `make instar` builds `bitmap.bin`; `make check-binary-sizes` passes and REPORTS bitmap.bin's extent/headroom; `make lint` clean. |
| 4b | high | opus | none | Header read + gates + staging. In `run_qcow2`: copy snapshot's `read_input_byte_range`/`write_input_byte_range`/`stage_refblocks`/`rb_lookup` (dev 0). Read the whole header cluster into `HEADER_BUF`; `QcowHeader::parse`; `qcow2::bitmap::parse_bitmaps_extension` (from the header) + read autoclear @88. Implement the gates (Mission §2): v2/dirty/corrupt/refcount-width/autoclear-inconsistent/format, and the host cross-check vs `BitmapConfig` ⇒ `ERROR_HEADER_MISMATCH`. Stage the directory into `DIR_A` (bounded by `BITMAP_DIR_LIMIT`, else `ERROR_SCRATCH_TOO_SMALL`) using `bitmap_directory_offset`/`size`; stage the refcount table + refblocks with the v1 contiguity gate. Still send a placeholder result (no actions applied) — this step wires state, not mutation. Define the full scratch layout (Open question 4) with the compile-time assert. GATE: builds, size-checked, lint clean; review the gates + cross-check for correctness. |
| 4c | high | opus | none | The metadata-action loop + write-back + autoclear dance for add/remove/clear/enable/disable (NOT merge). Loop `config.actions[..num_actions]`: dispatch each opcode to the Phase-3 `action_*`, double-buffering `DIR_A`↔`DIR_B`, threading `refblocks` + one `AllocCursor`; handle the crate/guest split — for add, remember `table_clusters_to_zero`; for remove/clear, stage the freed bitmap's on-disk table (`freed_table_offset/size`) into `TABLE_BUF`, decode entries (`decode_bitmap_table_entry`), and free each `Allocated` data cluster via `set_refcount_in_block(refblocks,..,0)` (using `rb_lookup`); for clear, also zero the table clusters. Accumulate `resulting_nb_bitmaps`, `extension_now_present`. Then the write-back + autoclear dance (Open question 1) with `fsync_input(0)` barriers modeled on `check --repair` `repair_all_qcow2`: clear autoclear → write new/zeroed clusters + refblocks → write directory (in place if it fits its cluster span, else relocate to a newly-allocated dir cluster and update the extension `dir_offset`) + bitmaps extension → set autoclear (or drop it if last removed). Send `BitmapResult`. GATE: builds, size-checked (re-report headroom), lint; RIGOROUS review of the write ordering + the data-cluster free walk. |
| 4d | high | opus | none | Merge on-disk orchestration (Open question 2). For `ACTION_MERGE`: `merge_validate` → if self-merge, no-op; else stage the source + dest on-disk tables (`TABLE_BUF` — may need two, or reuse), and for each of the `table_size` indices apply `merge_cluster_action(source_entry, dest_entry)`: `Skip`; `CopyAllOnes` (set the dest table entry's all-ones flag, free any obsolete dest data cluster); `OrIntoExisting` (read source+dest data clusters into `DATA_A`/`DATA_B`, `or_bitmap_data`, write dest back); `AllocDestFromSource` (alloc a dest data cluster via `alloc_*`, copy source bits in, set the dest table entry). Update the dest bitmap's table (write it back) + refcounts, then flow into the same write-back + autoclear dance as 4c. Reuse 4c's helpers. If this balloons past the 376 KiB cap or clean review, STOP and recommend deferring merge (return `ERROR_UNSUPPORTED_ACTION`), reserving the follow-up. Report the size + the decision. GATE: builds, size-checked, lint; review the OR/alloc/free correctness. |
| 4e | medium | sonnet | none | Full gate + docs touch: `make instar`, `make lint`, `make test-rust` (no new op-level rust tests — the crate is already tested; confirm nothing regressed), `make check-binary-sizes` (final bitmap.bin headroom recorded), `pre-commit run --all-files`. Confirm `bitmap.bin` is produced and registered everywhere. Note in the phase plan that functional validation is deferred to Phase 5/6/7. |

## Management session review checklist

- [ ] Intended files created/changed, no others.
- [ ] `bitmap.bin` builds, is registered in build.sh + check-binary-
      sizes.sh + Cargo.toml + Makefile, and fits the 376 KiB
      `.bss`-inclusive cap (headroom recorded).
- [ ] Input-RW device idiom (`read/write_input_sector`,
      `fsync_input(0)`); the `#[inline(never)]` runner workaround
      applied.
- [ ] All gates refuse **before** any write (image byte-identical
      on refusal); the host cross-check ⇒ `ERROR_HEADER_MISMATCH`.
- [ ] The action loop threads the double-buffered directory +
      refblocks + one AllocCursor correctly; the crate/guest
      data-cluster split (free data clusters on remove/clear via the
      on-disk table walk; zero new table clusters on add) is
      correct.
- [ ] The autoclear dance ordering is crash-correct (clear → write →
      set; leave clear if last removed); fsync barriers present.
- [ ] Merge (if landed) drives the truth table correctly; (if
      deferred) `ERROR_UNSUPPORTED_ACTION` + recorded decision.
- [ ] `make instar`, `make lint`, `make test-rust`,
      `make check-binary-sizes`, `pre-commit run --all-files` green.
- [ ] Commit messages follow conventions (Co-Authored-By with
      model/context/effort/settings; Signed-off-by; Prompt:).

## Success criteria

- `src/operations/bitmap/` builds `bitmap.bin`, registered and
  within the 376 KiB cap.
- The op validates + gates + stages + applies the ordered action
  list (add/remove/clear/enable/disable, and merge or a documented
  deferral) via the Phase-3 crate, doing the on-disk data-cluster
  work, and writes back under the crash-safe autoclear dance.
- Returns a correct `BitmapResult`; refusals leave the image
  byte-identical.
- `make instar`, `make lint`, `make test-rust`,
  `make check-binary-sizes`, `pre-commit run --all-files` are green.
- Functional correctness is validated in Phase 5 (host CLI) and
  Phases 6–7 (rust round-trip + `qemu-img`-oracle tests incl.
  post-op `qemu-img check`) — this phase's bar is build + size +
  review.

## Back brief

Before executing any step, the executing agent should back brief
the operator — in particular that this op **cannot be functionally
tested until Phase 5** (so review rigor is the bar), that it is an
**input-RW** op (`read/write_input_sector` + `fsync_input(0)`,
overriding the Phase-2 "output" note), that the whole write-back is
guarded by the **clear→write→set autoclear dance** (modeled on
`check --repair`), that it performs the **on-disk data-cluster
work** the Phase-3 crate deliberately left to the guest, and that
**merge may be deferred** if it threatens the 376 KiB cap.
