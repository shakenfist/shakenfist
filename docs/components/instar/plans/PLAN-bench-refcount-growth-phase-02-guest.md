# bench refcount growth — phase 02: guest op

Parent: [PLAN-bench-refcount-growth.md](/components/instar/plans/PLAN-bench-refcount-growth/).
Planned at high effort. All changes in
`src/operations/bench/src/main.rs` unless noted.

## Scope

### Staging budget (no region movement)

* `WRITE_MAX_REFBLOCKS: usize = 2048` (fills the existing
  16 KiB `WRITE_RB_OFFSETS` region exactly: 2048 × 8).
* `QcowWriteCtx.dirty` becomes a bitset:
  `dirty: [u64; WRITE_MAX_REFBLOCKS / 64]` with tiny inline
  `dirty_set / dirty_clear / dirty_get` helpers (256 bytes on
  stack, replacing 32 bools). Update `alloc_one_cluster` and
  `flush_dirty_refblocks` accordingly.
* Keep `WRITE_RT_LIMIT` (64 KiB), `WRITE_REFBLOCKS_LIMIT`
  (2 MiB) and every region address unchanged; the
  `const _` asserts must not change values. Update the
  budget doc comments (32-refblock text) to the new envelope:
  refusal now means needed slots > 2048, staged refblock bytes
  > 2 MiB, or extended RT bytes > 64 KiB.

### Growth in `qcow2_write_setup`

After the existing staging (RT prefix read, contiguity gate,
refblock staging — gate now against the new caps):

1. Compute `file_end_clusters` from
   `(call_table.get_input_capacity)(0)`... **no** — capacity is
   the (inflated) device capacity, not the file size. Use the
   host-provided on-disk size: `chain_config.devices[0]`? The
   ChainConfig carries per-device metadata the host filled in;
   check what field holds the on-disk byte length (the read path
   uses virtual_size; discovery info may include file size). If
   no on-disk length is available to the guest, derive
   `file_end_clusters` from the refcount structures instead: the
   highest refcounted cluster index + 1 across the staged
   refblocks (linear scan of staged bytes, cheap at ≤ 2 MiB),
   additionally clamped up by `(refcount_table_offset +
   rt_size)`, `(l1_table_offset + l1_size·8)` and header cluster
   — i.e. every host offset the header names. This is safe:
   placing new structures at the first cluster index that is
   both beyond every refcounted cluster and beyond every
   header-referenced structure guarantees no collision, even if
   the physical file has unrefcounted tail slack (writes there
   land past meaningful bytes; reads zero-fill).
2. Call `bench::worst_case_touched(&params, image_size,
   cluster_size)` and `bench::plan_refcount_growth(...)` (phase
   1). `GrowthOverflow` ⇒ `ERROR_ALLOC_EXHAUSTED` (existing
   message).
3. No-growth plan ⇒ construct `QcowWriteCtx` exactly as today
   (byte-identical setup behavior — this is the v1 fast path and
   most 64 KiB-cluster runs).
4. Growth plan ⇒ still pre-bracket:
   a. Extend the staged RT image in `WRITE_RT_BUF`: new slot
      entries `R..S-1` = big-endian host byte offsets of the new
      refblock clusters (`(refblocks_start + k) · cs`). If
      relocating, the staged RT is the FULL new table
      (`new_rt_clusters · cs` bytes, zero-padded tail).
   b. Fill `WRITE_RB_OFFSETS` for all `S` slots; zero the new
      refblocks' staging bytes.
   c. Set refcount 1 for every new structure cluster (new RT
      clusters if relocating + new refblock clusters) via
      `snapshot::qcow2::set_refcount_in_block` on the staged
      refblock covering each (slot = index / epb — may be an
      existing slot or a new one); mark those slots dirty.
   d. Eagerly write every dirty staged refblock to disk
      (reuse `flush_dirty_refblocks`), leaving dirty flags
      clear.
   e. Write the RT: relocating ⇒ full staged table at
      `rt_start · cs`; in place ⇒ only bytes
      `R·8 .. S·8` of the staged table at
      `refcount_table_offset + R·8` (byte-range RMW helper).
   f. `(call_table.fsync_input)(0)`.
   g. Relocating only: patch header bytes 48..60
      (`refcount_table_offset` u64 BE = `rt_start · cs`,
      `refcount_table_clusters` u32 BE = `new_rt_clusters`) via
      `write_input_byte_range` on sector 0, then
      `(call_table.fsync_input)(0)`. Mirror snapshot create's
      commit-point comment style
      (src/operations/snapshot/src/main.rs:1150-1219).
   h. Relocating only, AFTER the header flip: decrement the old
      RT clusters (offset `old_rt_offset`, `old_rt_clusters`
      clusters) to refcount 0 in the STAGED refblocks and mark
      those slots dirty — do NOT write them now. The run's
      refcounts-last cadence (interval flushes / run-end
      `flush_dirty_refblocks`) persists the free. Crash in the
      window ⇒ old RT leaks (repairable), the documented class.
5. `QcowWriteCtx { refblock_count: S, ... }` otherwise
   unchanged; `host_refblocks_start` stays 0; the untouched
   allocator now sees the grown coverage.

### Constraints and cautions

* The guest binary must stay within budget
  (`scripts/check-binary-sizes.sh`; bench is at ~20% — no
  static arrays, keep new state in existing scratch regions or
  on-stack ≤ a few hundred bytes).
* `#[inline(never)]` on any new sizeable helper called from the
  per-format runner (see the opt-level=z + lto miscompile note
  on existing functions; follow the existing attribute
  placement).
* All new writes go through the existing byte-range helpers
  (`write_input_byte_range`, bounce `WRITE_RMW_BOUNCE`);
  zero-fill semantics past EOF are host-guaranteed.
* Update docs/bench.md "Write tests": replace the "Allocation
  never grows the refcount table" paragraph with the new growth
  design (preemptive, over-provisioned, setup-time; refusal
  envelope; crash-window note for the deferred old-RT free).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | default (Fable) | none | Implement the Scope exactly in src/operations/bench/src/main.rs (constants, dirty bitset, growth in qcow2_write_setup) plus the docs/bench.md update. The parent plan's Situation section lists every relevant file:line (staging at :553-695, contiguity gate :635-659, alloc :474-506, flush :515-542, ctx :309-328, scratch consts :103-148; snapshot's fsync template :1150-1219). Build with `make instar`; run `scripts/check-binary-sizes.sh`; smoke-test by hand: qemu-img create 4M qcow2 cluster_size=512, qemu-io prepopulate 2M, then `instar bench -w -c 21 -s 65536 -o 1269120 --flush-interval 64 -f qcow2` must exit 0 with `qemu-img check` clean and `qemu-img compare` equal against a qemu-img bench run on a twin image (this is issue #397's vector). Also verify the no-growth fast path: a 64 KiB-cluster image run produces a byte-identical image to the pre-change binary for the same schedule. |

## Review checklist deltas

Standard checklist, plus: read the final `qcow2_write_setup`
end-to-end for ordering (refblocks → RT → fsync → header →
fsync → deferred old-RT decrement staged only); confirm the
no-growth path performs zero new I/O; confirm
`check-binary-sizes.sh` output for bench.
