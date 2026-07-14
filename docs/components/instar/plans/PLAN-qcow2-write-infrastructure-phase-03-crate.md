# qcow2 write infrastructure — phase 03: the qcow2-write crate

Parent:
[PLAN-qcow2-write-infrastructure.md](/components/instar/plans/PLAN-qcow2-write-infrastructure/).
Planned at high effort. Builds the crate the whole master plan
revolves around; phases 4-6 migrate the existing ops onto it and
phase 7 extends it with COW. Every design input below was
settled empirically by phase 1 (see the master plan's
"Findings: phase 1 semantics pin", especially Q4) — sub-agents
implement these decisions, they do not relitigate them.

## Scope

Create **`src/crates/qcow2-write/`**: a pure `no_std` planner
crate providing the windowed step-program API for "write N bytes
at virtual offset X into an existing qcow2". v1 capability
envelope: overwrite-in-place of owned clusters and allocating
writes (fresh data clusters, fresh L2 tables, sub-cluster
RMW/zero-fill), with refcount maintenance inside the staged
refblock set. NOT in v1 (explicitly out of scope here):
copy-on-write (phase 7 — snapshot-bearing images are refused by
the envelope), refcount growth (phase 6 moves bench's growth
planner in), and any guest-op changes (phases 4-6).

No integration tests in this phase — the crate has no consumer
yet. The quality bar is the unit + simulation suite (step 3d),
which must be strong enough that the phase 4-6 migrations are
mechanical.

### Settled design decisions (from phase 1 Q4; binding)

1. **Windowed step-program.** `plan_*` functions emit typed
   steps into a caller-provided buffer; a full-image program is
   never materialised (commit's envelope reaches ~2M clusters).
   The caller loops: plan a window, execute it, resume.
2. **Steps are plain Rust data, not a packed byte encoding.**
   Planner and executor live in the same binary; no
   serialization boundary exists. `Step` is a `#[repr(C)]`
   struct (fixed size, target ≤ 48 bytes — assert with a
   `const`) with a `StepKind` tag. A 64 KiB step buffer (≈ 1300+
   steps ≈ 100+ worst-case clusters per refill at ~11 steps per
   allocating cluster) fits every op's scratch slack (phase 1
   Q4); the buffer length is caller-chosen.
3. **Address-free planner.** Steps reference staged buffers by
   `RegionId` (L1, L2 window slot, refblocks, refcount table,
   bounce, caller data) + offset, and devices by `TargetDevice`
   (`Input0` / `Output` — commit writes the backing via the
   output device and clears the overlay via input slot 0, so
   the device dimension is required from day one). The crate
   never sees a guest address; the executor owns the
   region-id → slice mapping. This keeps the crate pure and
   host-unit-testable.
4. **Barrier semantics (the fsync asymmetry, decided).**
   `StepKind::Barrier { class }` with `class ∈ {Ordering,
   Durability}`. The contract: no step after a barrier may be
   issued before every step preceding it has been issued
   (Ordering) / made durable (Durability). Executors map
   Durability to `fsync_input(0)` where the target is the RW
   input device (bench, and commit's overlay clear pass) and
   degrade to Ordering where no fsync primitive exists (the
   output device: commit's backing, rebase's overlay — matching
   those ops' current no-fsync reality, so migrations stay
   byte- and behaviour-identical). Adding `fsync_output` to the
   call table is deliberately deferred; recorded in Future work
   as a durability upgrade that phases 4-5 must NOT take
   implicitly.
5. **Staged-L2 model: fixed-slot window with planner-emitted
   load/writeback.** The caller provides a slot count; the
   planner emits `LoadCluster` (disk → L2 slot) steps on demand,
   tracks dirty slots, and emits writeback steps before
   eviction or at flush. Stage-everything (commit today) is the
   degenerate case of a large window; rebase's growth arena and
   bench's zero-staging RMW both map onto it. Eviction is LRU;
   deterministic (no clocks — order by access counter in
   state).
6. **Refblock model: single staged copy, mutated in place**
   (bench's model — phase 1 Q4 found commit/rebase's duplicate
   planner-scratch copies waste 3-4 MiB and are the obstacle to
   a 2 MiB-cluster envelope). Dirty bitset in state;
   `plan_flush` emits explicit per-dirty-refblock write steps
   (the "composite flush" is a planner emission pattern, not a
   magic step — the executor stays dumb).
7. **Crash-ordering contract encoded in emission order.** Within
   any window and across windows: (a) a data-cluster write
   precedes the L2 patch that makes it reachable; (b) a fresh
   L2's zero/init write precedes the L1 patch that references
   it; (c) refcount writebacks are emitted only by `plan_flush`,
   after all pointer patches of the epoch, refcounts-last; (d)
   barriers separate the groups per decision 4. This mirrors
   commit's proven order and is THE property the test suite
   pins (step 3c).
8. **Envelope gates, unified and checked before any write step
   exists**: qcow2 v2/v3 only, `refcount_bits == 16`, no
   extended-L2, no compressed clusters in the write path's L2
   coverage (classification refuses on encounter), no
   encryption, no external data file, dirty/corrupt incompatible
   bits refused, `nb_snapshots == 0` (v1; phase 7 lifts).
   Constructing a `WriteState` runs the gates; a gated image
   yields a typed refusal before any step is emitted ("no
   mutation before envelope checks" — the phase-2 deferral,
   honoured by construction in the new crate).
9. **Capacity refusals are clean but not byte-idempotent in
   v1.** `RefcountExhausted` can surface mid-plan after earlier
   windows executed (same semantics as today's ops: unreferenced
   scaffolding may exist, metadata never flushed, image
   check-clean). Full byte-idempotence needs a worst-case
   pre-pass; phase 6's growth planner provides the bound
   machinery, so it is deferred there. Documented in the crate's
   error-type docs.
10. **Dependencies**: `shared`, `crates/qcow2` (parsing,
    geometry), `crates/snapshot` (allocator + refcount RMW +
    COPIED-flag primitives — `alloc_contiguous_clusters_in_refblocks`,
    `set_refcount_in_block`, `check_refcount_after_addend`; see
    the master plan Situation for line refs). No cycle:
    snapshot depends only on shared + qcow2 (verified). Re-homing
    those primitives INTO qcow2-write stays future work.

### API sketch (step 3a refines names, not shapes)

```rust
pub struct Geometry { /* from QcowHeader: cluster_bits, l1, rt, ... */ }
pub struct StagingConfig { pub l2_slots: usize, pub max_refblocks: usize, ... }
pub struct WriteState { /* geometry, l1 copy meta, l2 window map,
                           refblock offsets + dirty bits, AllocCursor,
                           access counter, resume point */ }
pub enum RegionId { L1, L2Slot(u16), RefcountTable, Refblocks, Bounce, CallerData }
pub enum TargetDevice { Input0, Output }
pub struct Step { pub kind: StepKind, /* device, region, offsets, len */ }
pub enum StepKind { ReadCluster, WriteRange, ZeroRange, PatchEntryU64,
                    LoadCluster, WritebackCluster, Barrier, ... }

pub fn check_envelope(hdr: &QcowHeader) -> Result<(), Gate>;
pub fn new_state(hdr: &QcowHeader, cfg: &StagingConfig, staged: ...)
    -> Result<WriteState, Gate>;
pub fn plan_write(st: &mut WriteState, voff: u64, len: u64,
    data: DataSource, steps: &mut StepBuf) -> Result<Window, WriteError>;
pub fn plan_flush(st: &mut WriteState, steps: &mut StepBuf)
    -> Result<Window, WriteError>;
```

`DataSource` abstracts "caller data region at offset" vs "fill
pattern" (bench) so migrations don't bounce data twice. `Window`
carries `emitted` and a resume marker; `WriteError::BufFull` is
a normal resume signal, not a failure.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | default (Fable) | none | Crate skeleton: `src/crates/qcow2-write/` (Cargo.toml modelled on `src/crates/snapshot/` — no_std, `shared` + `qcow2` + `snapshot` deps, `create` + `qcow2/create` as dev-deps like `crates/commit`); register in `src/Cargo.toml` workspace members. Implement the type vocabulary and envelope from the Settled decisions: `Geometry`, `StagingConfig`, `RegionId`, `TargetDevice`, `Step`/`StepKind` (`#[repr(C)]`, const-assert size ≤ 48 B), `StepBuf`, `Gate`, `WriteError`, `check_envelope`, `new_state` (gates 8 run here; no plan functions yet — stub them `todo!()`-free by returning a NotImplemented error variant so the crate builds standalone). Doc comments cite the master plan findings for each decision. Unit tests for every gate (accept + refuse per gate, incl. nb_snapshots). `make test-rust` green, `make lint` clean. |
| 3b | high | default (Fable) | none | Implement `plan_write`: per-cluster classification (owned overwrite via COPIED+refcount check from staged refblocks; unallocated → allocate data cluster, and fresh L2 when the L1 slot is empty; snapshot-shared/compressed/unknown → typed refusal), sub-cluster zero-fill and RMW inside freshly allocated clusters, L2 window management per decision 5 (LoadCluster on miss, LRU eviction with dirty writeback), allocator + refcount RMW via `crates/snapshot` primitives per decision 6, windowed emission with resume per decision 1 and `BufFull` semantics, emission order per decision 7 (a)-(b). Also `plan_flush` per decisions 6-7 (c)-(d) with barrier classes per decision 4. Exhaustive inline unit tests: classification tables, window resume across BufFull mid-cluster, L1-boundary and straddling writes, LRU eviction correctness, refcount arithmetic vs hand-computed expectations at cluster sizes {512, 4096, 65536, 2 MiB}. |
| 3c | high | default (Fable) | none | The ordering contract as tests: a property-style suite that, for randomized-but-seedless parameter grids (write sets, window sizes down to pathological 4-step buffers, L2 slot counts down to 1), collects the FULL emitted program and asserts invariants 7(a)-(d) mechanically (for every PatchEntryU64 making cluster C reachable, a WriteRange/ZeroRange covering C precedes it; refcount writebacks only after the epoch's pointer patches; Durability barriers exactly at the contract points), plus: no step ever references an out-of-bounds region offset, no write step exists for a gated image, emitted programs are identical regardless of step-buffer size (window-invariance — the key windowing correctness property). Keep the assertion helpers reusable; phase 7 extends them for COW. |
| 3d | high | default (Fable) | none | Simulation harness (dev-dependency test module, host-only): a `SimDisk` (Vec<u8>-backed, per-device) executor that applies emitted programs literally, honouring barriers by epoch-tagging writes. Build minimal valid qcow2 v3 images programmatically (use the `create` dev-dep emitters as `crates/commit`'s tests do). For a grid of {cluster size 512/4096/65536/2M × image size × write patterns (sequential, sparse, straddling, sub-cluster, repeated overwrite)}: run plan→execute to completion + flush, then (1) re-parse the resulting image with `crates/qcow2` and assert the virtual content equals a `BTreeMap` reference model, (2) walk L1/L2/refcounts and assert full consistency (every reachable cluster refcount ≥ 1, COPIED set on owned clusters, no double-allocation, file-length growth sane), (3) crash-consistency spot checks: truncate the executed step stream at every Durability barrier boundary and assert the re-parsed image is either old-content-consistent or new-content-consistent, never referencing-unwritten-data (the ordering contract's payoff, testable because barriers are data). Report any invariant the harness cannot express rather than weakening it. |
| 3e | medium | default (Fable) | none | Close-out: crate-level `lib.rs` docs (design decisions with master-plan citations), ARCHITECTURE.md gains a qcow2-write section (crate role, step-program idiom, barrier semantics), master plan execution table phase 3 → Complete + findings addendum if 3a-3d deviated from the settled decisions, plans index updated, CHANGELOG entry. `pre-commit run --all-files`. |

Steps are sequential (each builds on the previous crate state);
3c and 3d may run concurrently after 3b if desired (disjoint
test modules), at the cost of a merge review. One commit per
step minimum; each builds, lints, and passes `make test-rust`.

## Review checklist deltas

Standard checklist from PLAN-TEMPLATE.md, plus:

* The crate stays `no_std` outside `#[cfg(test)]` and holds NO
  guest addresses, NO call-table types, and NO I/O — pure
  planning over caller-provided state (grep for `0x3` address
  literals and `shared::CALL` uses; there must be none outside
  docs).
* `Step` size const-assert present; step-buffer window-invariance
  test present and passing (3c) — this is the property the whole
  windowed design rests on.
* Emission-order invariants are asserted mechanically over full
  programs, not by reviewing sample outputs by eye.
* The simulation harness's crash-consistency check truncates at
  every barrier, not a sampled subset.
* No existing crate or op is modified in this phase (git diff
  touches only the new crate, `src/Cargo.toml`, and step-3e
  docs).
