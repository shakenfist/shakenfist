# PLAN-check-repair phase 02: leak-reclamation planner (safe tier)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly and ground your
answers in what the code actually does today; do not speculate
when you could read. The specific code this phase depends on:

- `src/crates/check/src/qcow2.rs` — the phase-1 scaffold module
  where this phase's planner function lands. Today it is a stub
  with `use` lines and an empty `#[cfg(test)] mod tests {}`.
- `src/crates/check/src/lib.rs` — the phase-1 type surface:
  `RepairTier { Leaks, All }`, `RepairError { RefcountExhausted,
  AmbiguousCorruption, Unsupported, MisalignedAccess, ParseFailed
  }` with `impl From<snapshot::SnapshotError> for RepairError`,
  and `RepairCounters { leaks, refcounts, corruptions, incomplete
  }`.
- `src/crates/snapshot/src/qcow2.rs` — the primitives this phase
  reuses:
  - `read_refcount_in_block(block: &[u8], local_idx: u64,
    refcount_bits: u32) -> Result<u64, SnapshotError>` (line 51).
  - `set_refcount_in_block(block: &mut [u8], local_idx: u64,
    refcount_bits: u32, value: u64) -> Result<(), SnapshotError>`
    (line 152). Sub-byte widths are LSB-first within each byte
    (matched to qemu, fixed in the PLAN-snapshot pre-push audit);
    setting one entry preserves its neighbours.
- `src/operations/check/src/main.rs` — `check_qcow2` (line 2105),
  specifically the leak-detection pass (lines ~2761–2887). This
  is the *reporting* logic phase 4 will later drive to feed this
  planner; read it to understand exactly what a "leak" is here.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/); read its "Design
overview: the repair safety model", especially the tiering table
(safe `leaks` vs lossy `all`). This is phase 2 of ten — the
first phase with real repair logic, but still **pure, no-I/O, and
not yet wired into any binary**.

I prefer one commit per logical change, and at minimum one commit
per phase. The commit must build, pass tests, and have a clear
message.

## Situation

### What a "leak" is in instar today

`check_qcow2`'s leak-detection pass (`check/src/main.rs`
~2761–2887) works in two sweeps over the refcount table:

1. **Reference sweep**: during the L1/L2 walk (and the refcount
   structures' self-references — each refblock marks itself, line
   2787), it builds a **boolean** bitmap `bmp` where `bmp.test(cidx)`
   answers "is host cluster `cidx` referenced by any metadata?"
2. **Leak sweep**: it iterates every refcount-table entry → every
   refblock → every refcount entry. For an entry whose **stored
   refcount `rc > 0`** at cluster index `cidx`, if `!bmp.test(cidx)`
   the cluster is allocated-but-unreferenced → `result.leaks += 1`
   (line 2876–2878).

So a leak is precisely: **a cluster the refcount table marks
allocated that nothing references.** The correct fix is to set
that cluster's stored refcount to **0** (free it). This is the
entire safe (`leaks`) tier.

Crucially, `bmp` is a *boolean*, not a per-cluster reference
*count*. So instar can detect "allocated but referenced by
nothing" but cannot, from this structure alone, detect "refcount
is 3 but only 2 references exist" (an over-count on a still-live
cluster). Correcting that richer case needs a full per-cluster
recount and is the lossy `all` tier — **phase 3**, not this
phase. Phase 2 does exactly one thing: zero the refcount of
unreferenced-but-allocated clusters.

### What this phase builds on

- The phase-1 `check` crate type surface (`RepairError`,
  `RepairCounters`) and its `From<SnapshotError>` bridge, which
  lets the planner `?`-propagate errors from the reused snapshot
  primitives.
- `snapshot::qcow2::{read_refcount_in_block, set_refcount_in_block}`
  — all refcount widths 1/2/4/8/16/32/64, sub-byte LSB-first.
  Setting an entry to 0 is width-safe and preserves neighbouring
  sub-byte entries.

### What this phase produces

A single pure planner function in `src/crates/check/src/qcow2.rs`,
plus its unit tests:

```rust
/// Reclaim leaked clusters within one staged refcount block.
///
/// `refblock` is a contiguous slice whose byte 0 is the first
/// refcount entry of the block (the caller stages a whole
/// refblock and, for sub-sector starts, passes the aligned
/// subslice). For each of the `entries_in_block` entries, reads
/// the stored refcount; for an entry with refcount > 0 that the
/// `is_referenced` predicate reports unreferenced, sets it to 0.
/// Returns the number of entries reclaimed. Entries with
/// refcount 0 are skipped without consulting the predicate; an
/// entry the predicate reports *referenced* is never modified
/// (the safe tier never lowers a live cluster's refcount — that
/// is phase 3's lossy concern).
pub fn reclaim_leaks_in_refblock(
    refblock: &mut [u8],
    entries_in_block: u64,
    refcount_bits: u32,
    is_referenced: impl FnMut(u64) -> bool,
) -> Result<u32, RepairError>
```

The `is_referenced` closure takes the **local** entry index
within this block; the guest (phase 4) maps it to a global
cluster index and tests its `bmp`, keeping the planner ignorant
of global cluster-index math (clean separation, mirroring how the
snapshot crate's visitors take caller closures). The function is
the testable home of the leak *policy* (`rc > 0 && !referenced →
set 0`), so the rule is exercised over synthetic buffers rather
than scattered into the guest.

### What this phase does NOT change

- No guest binary, no host CLI, no ABI. Phase 4 wires the guest;
  phase 5 the host CLI.
- No operation binary depends on the `check` crate yet, so
  **`check.bin` and every operation binary stay byte-identical**
  (same property phase 1 held).
- No over-count correction, no refcount-structure rebuild, no
  COPIED reconciliation, no `corrupt`-bit handling — all phase 3
  / phase 4.
- No patch-list types: like the snapshot mutators, this planner
  mutates the staged slice in place and returns a tally; the
  guest emits the write-back (phase 4).
- User-facing docs (`docs/qcow2/qcow2-refcount.md`) are phase 10;
  this phase documents only the function itself.

## Open questions

### 1. Per-refblock driver with a predicate, or a per-entry primitive?

**Resolved: the per-refblock driver above.** A bare per-entry
`set 0` would just be `set_refcount_in_block(.., 0)` and would
push the leak *policy* (skip zero entries, never touch referenced
ones, tally) into the guest where it cannot be unit-tested over
synthetic buffers. The driver keeps the policy in the pure crate.
It uses `set_refcount_in_block(.., 0)` internally, so the "reuse
`set_refcount_in_block`" requirement from the master plan holds.
(This mirrors snapshot phase 5 open question 12: no standalone
`free_cluster`; the zeroing is folded into the driver.)

### 2. Set to zero, or correct to a computed refcount?

**Resolved: set to zero only.** The detector's `bmp` is boolean,
so the only safe, mechanically-determined fix it supports is
freeing unreferenced clusters. Lowering a still-referenced
cluster's over-counted refcount needs a per-cluster recount —
the lossy `all` tier, phase 3. Documented on the function.

### 3. Does the planner own cluster-index math or the bitmap?

**Resolved: no.** The guest owns `bmp` and the
`(reftable_idx, block, entry) → cluster_index` math (it already
computes `cidx` at `check/src/main.rs:2868–2875`). The planner
takes a `is_referenced(local_idx)` closure and stays global-index
agnostic.

### 4. Staging granularity — whole refblock or per-sector?

**Resolved at the planner boundary: the planner operates on a
contiguous slice aligned to the refblock's first entry and takes
`entries_in_block`.** How phase 4 stages (whole cluster vs
sector-by-sector) is a guest concern. Recommendation for phase 4:
stage the whole refblock (one cluster) into a buffer, run the
planner once, write back only the dirtied sectors — simpler and
one clean write-back per block. The planner does not mandate it;
`read`/`set_refcount_in_block` bounds-check `local_idx` against
the slice and return `MisalignedAccess` (→ `RepairError`) if the
caller under-sizes the slice, which the driver `?`-propagates.

### 5. Refcount-width support?

**Resolved: all widths.** Unlike the snapshot *allocator* (16-bit
only), `read`/`set_refcount_in_block` handle every width, and
setting to 0 is width-safe including sub-byte neighbour
preservation. `check` detects leaks at all widths, so repair must
fix at all widths. A dedicated unit test covers sub-byte
neighbour preservation.

### 6. Does phase 2 emit a `RepairCounters`?

**Resolved: it returns `u32` reclaimed; the guest folds it into
`RepairCounters.leaks` (phase 4).** Returning the bare count keeps
the per-block function composable; the guest sums across blocks
and owns the `incomplete` flag. No `RepairCounters` construction
in this phase.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Implement `reclaim_leaks_in_refblock` in `src/crates/check/src/qcow2.rs` exactly per the signature and semantics in the Situation section. Body: loop `local_idx` in `0..entries_in_block`; `let rc = read_refcount_in_block(refblock, local_idx, refcount_bits)?;` (the `?` converts `SnapshotError` → `RepairError` via the phase-1 `From`); `if rc > 0 && !is_referenced(local_idx) { set_refcount_in_block(refblock, local_idx, refcount_bits, 0)?; reclaimed += 1; }`; return `reclaimed`. Do **not** consult `is_referenced` for `rc == 0` entries (contract + a test asserts it). Remove the now-unneeded `#[allow(unused_imports)]` from the `read_refcount_in_block` / `set_refcount_in_block` imports they are now used; keep the others (`for_each_cluster_in_l1`, `update_copied_flags_for_l1`, the `qcow2::` constants) gated for phase 3, or drop any that stay unused and re-add in phase 3 — pick whichever keeps `cargo build -p check` warning-clean. Add ~14 unit tests in the module's `mod tests` over synthetic refblock buffers: (a) all-zero block, all-referenced predicate → 0 reclaimed, buffer unchanged; (b) block with a mix of rc>0 referenced / rc>0 unreferenced / rc==0 entries → only the unreferenced rc>0 entries become 0, referenced and zero entries untouched, count correct; (c) a referenced entry with rc==3 is left at 3 (safe tier never lowers a live cluster); (d) idempotence — a second call reclaims 0 and leaves the buffer byte-identical; (e) `is_referenced` is never called for rc==0 entries (use a closure that panics / records calls and assert); (f) **sub-byte neighbour preservation** for 1-, 2-, and 4-bit widths — set up a block where entry N is an unreferenced leak and its byte-neighbours are referenced non-zero entries, reclaim, assert only entry N's bits cleared and neighbours bit-for-bit intact; (g) 16-bit standard width happy path; (h) 32- and 64-bit widths; (i) an under-sized `refblock` slice for the given `entries_in_block` surfaces `RepairError::MisalignedAccess` via the `?`-propagated `set`/`read`. Use opus: the sub-byte LSB-first neighbour preservation and the "never lower a referenced cluster" policy are the correctness traps; getting the tests right here is what lets phase 3/4 trust the safe tier. |
| 2b | low | sonnet | worktree | Verify and commit. From the worktree `src/` with the cargo target dir redirected to an owned path (the lint-as-root ownership gotcha — see the rust-devcontainer-permissions note): confirm `cargo test -p check` passes the ~14 new tests, `make test-rust` is green, `make instar` builds and `make check-binary-sizes` shows **`check.bin` and every operation binary byte-identical to its post-phase-1 size** (no operation imports the `check` crate yet), `make lint` and `pre-commit run --all-files` clean. Stage and present ONE commit (steps 2a) with the `~/.claude/CLAUDE.md` convention (≤50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Assisted-By + Co-Authored-By with model/context/effort). The message explains: this lands the safe-tier leak-reclamation planner (`reclaim_leaks_in_refblock`) in the `check` crate, reusing the snapshot refcount primitives; it is pure and not yet wired into any binary, so behaviour is unchanged; the lossy over-count correction and the guest wiring follow in phases 3 and 4. |

## Agent guidance

### Execution model

All implementation is by sub-agents in the `check-repair`
worktree (which is itself the isolation from the main checkout —
do not nest a throwaway worktree). The management session reads
the actual diff, runs the gates, and commits.

### Model and effort notes

- **2a is high-effort opus.** The function body is short, but its
  correctness rests on sub-byte refcount bit handling and the
  safety policy; the value is in the ~14 tests, which need
  careful synthetic-buffer construction and the bit-level
  neighbour-preservation assertions.
- **2b is low-effort sonnet**: a scripted verify-and-commit.

### Management session review checklist

- [ ] `reclaim_leaks_in_refblock` matches the agreed signature
      and zeroes only `rc > 0 && !is_referenced` entries.
- [ ] `is_referenced` is not consulted for `rc == 0` entries.
- [ ] A referenced entry — at any refcount, including > 1 — is
      never modified.
- [ ] Sub-byte (1/2/4-bit) reclamation preserves neighbour
      entries bit-for-bit; a test proves it.
- [ ] Errors from the snapshot primitives propagate as
      `RepairError` via the phase-1 `From` (no `unwrap`).
- [ ] `cargo build -p check` is warning-clean (imports tidied).
- [ ] `make instar` + `make check-binary-sizes`: `check.bin` and
      every operation binary byte-identical to post-phase-1.
- [ ] `make test-rust`, `make lint`, `pre-commit` clean.
- [ ] The `check` crate remains safe Rust (no `unsafe`).

## Administration and logistics

### Success criteria

* `reclaim_leaks_in_refblock` exists in `src/crates/check/src/qcow2.rs`
  with ~14 passing unit tests over synthetic refblocks.
* It reuses `snapshot::qcow2::{read,set}_refcount_in_block` and
  propagates errors via `RepairError`.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass; every
  operation binary is byte-identical to post-phase-1.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- **Over-count correction** (refcount higher than the true
  reference count on a *referenced* cluster) needs a per-cluster
  recount and lands in phase 3's lossy tier.
- **Guest wiring**: phase 4 drives `check_qcow2`'s leak sweep to
  call this planner per refblock and writes the dirtied refblocks
  back, under the crash-safe `corrupt`-bit ordering.

### Bugs fixed during this work

To be filled in if implementation surfaces anything (e.g. a gap
in the existing leak detector that the synthetic-buffer tests
expose).

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. Update the master plan's phase-2 Execution-table row
to "Landed" with a pointer to this file once the commit is in.

### Back brief

Before executing any step, back brief the operator on your
understanding of the phase and how it aligns with this plan and
the master plan's safety model (especially that the safe tier
only zeroes unreferenced clusters and never lowers a live
cluster's refcount).
