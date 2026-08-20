# bench qcow2 refcount growth

## Status: Complete

Phases 1-4.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead.

## Situation

`instar bench -w` on qcow2 refuses any write schedule whose
allocations outrun the image's already-populated refcount blocks
(`bench: image too large for in-place bench write`,
`BenchResult::ERROR_ALLOC_EXHAUSTED`). The v1 write path
(`src/operations/bench/src/main.rs`) stages the gap-free
contiguous prefix of populated refblocks at setup (at most
`WRITE_MAX_REFBLOCKS = 32`, at most 2 MiB of refblock bytes) and
the allocator (`snapshot::qcow2::alloc_contiguous_clusters_in_refblocks`)
claims free clusters only within that staged coverage. It never
allocates a new refblock and never grows the refcount table.

One 16-bit refblock covers `cluster_size² / 2` bytes of host
file: 2 GiB at 64 KiB clusters (comfortable) but only 128 KiB at
512-byte clusters and 8 MiB at 4 KiB clusters — outrun by almost
any allocating schedule. qemu-img grows refcount structures on
demand and succeeds. This produced differential-fuzz issues
#397–#401; the interim mitigation (landed with those issues)
steers the fuzzer's `-w` qcow2 recipes to ≥ 64 KiB clusters and
registers `qcow2-write-refblock-coverage` in
`KNOWN_BENCH_DIVERGENCES` (tests/test_bench.py) with a live
regression test and a docs/bench.md known-divergence bullet.

Facts established during planning (file:line refer to the tree at
plan-writing time):

* **Oracle allows layout freedom.** The differential fuzzer's
  write oracle for qcow2 (`scripts/differential-fuzz.py`
  `op_bench`) is `qemu-img compare` (virtual content) plus
  `qemu-img check` cleanliness (0 corruptions / 0 leaks /
  0 check-errors) on the instar copy — NOT byte identity and NOT
  file length. instar's grown refcount layout does not need to
  match qemu's bytes, only be valid and complete.
* **Empirical probe** (scratchpad `probe_overgrown.py`, run
  against qemu-img 10.0.8): an image given a relocated,
  over-provisioned refcount table at EOF, pre-allocated all-zero
  refblocks, refcounts for the new structures, freed old RT
  clusters, and a patched header is fully `qemu-img check`-clean,
  readable, and remains writable by qemu-io with clean check
  afterwards. Preemptive over-provisioned growth is acceptable.
* **The host already supports file growth.** On a qcow2 `-w` run
  the VMM exposes input slot 0 read-write with capacity
  `2 × max(on_disk_size, virtual_size)`, floored at 1 GiB
  (src/vmm/src/main.rs:4424-4430); `BackingStore::write_at`
  extends the file sparsely up to that capacity and `read_at`
  zero-fills past EOF (src/vmm/src/backing.rs:104-151), so
  sub-sector RMW at the growth frontier is safe. Worst-case
  growth (≤ virtual size of new data + ~2% metadata + the
  existing file) always fits under the hint; no host change is
  needed.
* **Memory budget is free.** `WRITE_RB_OFFSETS_LIMIT` is 16 KiB
  but only `32 × 8 = 256` bytes are used; raising
  `WRITE_MAX_REFBLOCKS` to 2048 fills it exactly (2048 × 8 =
  16 KiB) with **no scratch-region movement**. 2048 slots × 512 B
  = 1 MiB ≤ `WRITE_REFBLOCKS_LIMIT` (2 MiB). The dirty-flag array
  becomes a 2048-bit bitset (`[u64; 32]`, 256 bytes — stack-safe).
  The bench guest binary sits at ~156 KiB of its 768 KiB budget.
* **Write scheduling is fully guest-side.** The schedule
  (`bench::OffsetSchedule`, wrap rule
  `n % (image_size - bufsize)`) and flush cadence live in the
  dependency-free `crates/bench`; the host only validates CLI
  and displays. `BenchConfig` carries count / depth / bufsize /
  step / offset / flush_interval / flags — everything a
  worst-case analysis needs is available in the guest before the
  timing bracket opens.
* **Header-commit template exists.** snapshot create's commit
  point is a byte-ranged header patch through
  `write_input_byte_range` with `fsync_input(0)` between
  write-back groups (src/operations/snapshot/src/main.rs:1150-1219).
  The refcount-table pointer flip reuses that pattern (12 bytes
  at header offsets 48–59: `refcount_table_offset` u64 +
  `refcount_table_clusters` u32).

## Mission and problem statement

Give bench's qcow2 write path the ability to **grow the refcount
structures** so that any schedule qemu-img bench can execute on
the fuzzer's image envelope also succeeds under instar with
identical virtual content and a clean `qemu-img check`:

* Allocate new refblocks beyond the populated prefix.
* Relocate and enlarge the refcount table when it is out of
  slots, freeing the old table.
* Preserve the v1 crash posture (write-through data/L2/L1,
  staged refcounts written back last; a crash leaves at worst
  repairable leaks, never a dangling reference).
* Preserve the v1 fast path exactly: a schedule whose worst case
  fits the existing refblock coverage performs no growth and no
  new writes.
* Retire the fuzzer's `qcow2-write-refblock-coverage`
  steer-around and flip its divergence regression test into a
  parity test.

**Design: preemptive growth at setup.** Because the whole
request schedule is known before the timing bracket opens,
`qcow2_write_setup` computes a worst-case allocation bound and
grows the refcount structures once, pre-bracket. The run-time
allocator is untouched — it allocates from a (now larger) staged
refblock set. This avoids allocation-time reentrancy (growth
that itself allocates during a request) entirely, and keeps the
timed region free of growth work (a benchmark-correctness bonus:
qemu amortises growth into the timed run; instar pays it in
setup).

Growth layout (all placements at the cluster-aligned current
file end `E`):

```
[ existing file ... E ) [ new RT (only if relocating) ) [ new refblocks ... )
```

Setup write ordering (pre-bracket, mirroring snapshot's grouped
fsync discipline):

1. Stage everything in scratch: extended RT image, rb_offsets
   for all slots, existing refblock bytes plus zeroed new
   refblocks; set refcounts for every new structure cluster in
   the staged refblocks (marking them dirty); if relocating,
   decrement the old RT clusters to 0 in staging only (deferred
   to the run's refcounts-last cadence — a crash in the window
   leaves the old table as a repairable *leak*, the gentler
   artifact and bench's documented crash class).
2. Write all dirty staged refblocks (new ones, plus any existing
   refblock that gained refcounts for new structures) — but NOT
   the deferred old-RT decrement (that refblock's staged copy
   holds the decrement; write it with the decrement included
   only at the normal run-end/interval flush).

   Correction for implementability: stage the old-RT decrement
   AFTER step 2's eager flush, so the eager flush writes
   pre-decrement bytes and the decrement rides the run cadence.
3. Write the refcount table: full extended RT at the new
   location if relocating, or just the new slot entries in place
   if not.
4. `fsync_input(0)`.
5. If relocating: patch header bytes 48–59 (new RT offset +
   cluster count) via the RMW byte-range helper;
   `fsync_input(0)`.

Refusal envelope after this work: `ERROR_ALLOC_EXHAUSTED`
remains for schedules whose worst case exceeds the staging
budget — needed slots > 2048, staged refblock bytes > 2 MiB, or
extended RT bytes > 64 KiB. At 512-byte clusters that is
~256 MiB of host file (vs 4 MiB today); at ≥ 64 KiB clusters it
is ≥ 64 GiB. The fuzzer envelope (≤ 64 MiB images) never
refuses.

Worst-case bound (pure arithmetic in `crates/bench`, from
`BenchParams` + image geometry): touched data clusters ≤
min(count × (bufsize/cs + 2), span clusters when the schedule
does not wrap, total virtual clusters); touched L2 tables ≤
min(count × (bufsize/l2_coverage + 2), span L2 tables, l1_size);
plus the new structures themselves (fixed-point iteration over
needed slots / RT clusters, converges in ≤ 3 rounds).
Over-estimation is harmless: unused pre-grown refblocks are
check-clean (probe-verified) and the oracle ignores layout.

## Open questions

1. **Allocation-time vs preemptive growth?** Resolved:
   preemptive at setup (probe-validated; no reentrancy; timed
   region unchanged). Recorded above.
2. **Must the grown layout match qemu byte-for-byte?** No — the
   bench write oracle is compare + check, not byte identity
   (unlike snapshot's byte-compare oracle). Recorded above.
3. **Does growth change the raw `-w` or read paths?** No. The
   fork at `_start` isolates qcow2 writes.
4. **L1 growth?** Still out of scope: the L1 is always sized for
   the full virtual size on well-formed images and bench refuses
   requests past virtual size before they reach allocation. The
   `l1_idx >= l1_size` guard stays.
5. **Do snapshot / bitmap / commit inherit this?** Not in this
   plan. They share the stage-32-refblocks pattern and the
   "never grow" posture, but their oracles demand byte identity
   with qemu, which preemptive over-provisioned growth cannot
   satisfy. Listed under Future work.
6. **Crash-window class for RT relocation?** The old table is
   freed via a staged decrement that rides the run's
   refcounts-last cadence: a crash after the header flip but
   before the final flush leaves the old RT clusters refcounted
   but unreferenced — a repairable leak, same class as v1's
   mid-run crash artifacts. docs/bench.md documents it.
7. **Wrap-rule schedules?** When the schedule wraps
   (`offset + (count-1)·step + bufsize > image_size`), the bound
   falls back to "entire image allocatable", which is exactly
   the budget the fuzzer envelope needs anyway.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Growth planner (pure, `crates/bench`) | PLAN-bench-refcount-growth-phase-01-planner.md | Complete (commit 7c49a9c) |
| 2. Guest op growth in `qcow2_write_setup` | PLAN-bench-refcount-growth-phase-02-guest.md | Complete (commit 698b65e) |
| 3. Integration tests (matrix + parity flip) | PLAN-bench-refcount-growth-phase-03-integration.md | Complete |
| 4. Fuzzer steer-around retirement + docs | PLAN-bench-refcount-growth-phase-04-fuzzer-docs.md | Complete |

One commit per phase minimum; each commit builds, lints and
tests clean on its own.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. Follow the standard
workflow from PLAN-TEMPLATE.md (plan → spawn sub-agent → review
actual files → fix or retry → commit).

### Planning effort

This master plan was written at high effort with three prior
codebase surveys (bench guest write path; VMM host side +
capacity; refcount helpers + memory budgets + header-commit
precedents). Phase plans 1 and 2 are planned at high effort
(refcount management, crash ordering); phases 3 and 4 at medium
(well-established test/fuzzer patterns).

## Administration and logistics

### Success criteria

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `scripts/check-binary-sizes.sh`.
* All Rust unit tests pass (`make test-rust`), including new
  planner tests in `crates/bench`.
* Bench integration suite passes, including new growth-matrix
  tests; the former `qcow2-write-refblock-coverage` divergence
  regression test now asserts parity (instar rc 0, `qemu-img
  compare` identical, `qemu-img check` clean).
* Replaying differential-fuzz issues #397–#401 (iter_seeds
  978924777, 2808571592, 2843295063, 933136565, 3155371439)
  with the fuzzer steer-around removed reports no divergence.
* A ≥ 250-iteration differential-fuzz soak with the restored
  full cluster-size matrix reports 0 divergences.
* `pre-commit run --all-files` passes.
* docs/bench.md, CHANGELOG.md and the plans index are updated.

### Future work

* Extend refcount growth to `snapshot -c` (and the other staged
  mutators: bitmap, commit) — requires byte-parity with qemu's
  lazy growth, a substantially stricter bar; the fuzzer's
  snapshot avoidance row 7 stays until then.
* A dedicated coverage-guided fuzz target for the growth planner
  (fuzz_bench_schedule covers the schedule iterator only).
* Shrink `ERROR_ALLOC_EXHAUSTED`'s message ("image too large for
  in-place bench write") if the remaining refusal envelope makes
  it misleading; kept for now since the refusal class survives.

### Bugs fixed during this work

* Closes the parity gap behind differential-fuzz issues
  #397–#401 (already closed by the interim steer-around; this
  plan removes the underlying limitation).

### Back brief

Understanding: bench `-w` on qcow2 is the only op whose parity
oracle permits divergent on-disk layout, so it can adopt
preemptive over-provisioned refcount growth without touching the
byte-parity-constrained snapshot/bitmap machinery. The whole
schedule is known pre-bracket, so growth happens once in setup:
compute a worst-case bound, extend the staged refblock set (cap
raised 32 → 2048 slots inside existing scratch regions),
optionally relocate the refcount table to EOF with a
snapshot-style fsync-ordered header flip, and let the untouched
v1 allocator run inside the pre-grown coverage. The fuzzer
steer-around then retires and the full cluster matrix returns to
`-w` coverage.
