# PLAN-check-repair phase 09: coverage-guided fuzzing

## Prompt

Before responding to questions or discussion points in this
document, explore the codebase and ground every claim in what the
code actually does — read the doc comments, do not speculate. The
code this phase touches and fuzzes:

- `src/crates/check/src/qcow2.rs` — the four public repair
  planners this phase exercises: `reclaim_leaks_in_refblock`
  (leaks tier), `account_reference_in_map` (the count-map
  accumulator), `correct_refcounts_in_refblock` (all-tier
  recount correction), and `reconcile_copied_flags_for_l1` (a
  thin wrapper over `snapshot::qcow2::update_copied_flags_for_l1`).
  Read their doc comments and the in-crate unit tests (the four
  `reclaim_*` cases and the rest) — the invariants below are
  derived from those contracts.
- `src/crates/check/src/lib.rs` — `RepairError` and the
  `From<snapshot::SnapshotError>` bridge the planners `?`-propagate
  through; `RefcountFixTally` / `RefcountFix`.
- `src/fuzz/fuzz_targets/fuzz_snapshot_refcount.rs` — the
  **archetype**: a 32-byte structured header, a pool of staged
  slices synthesized from the remaining input, an op selector that
  dispatches one mutator per exec, reusable `thread_local!`
  scratch buffers, and semantic-invariant asserts on the success
  path (panic + ASAN is the base oracle, the asserts are the
  semantic oracle). The check planners reuse the same snapshot
  refcount primitives this target already fuzzes, so it is both
  the structural model and the place the COPIED-walker's deep
  invariants already live (do not re-prove them here).
- `src/fuzz/fuzz_targets/fuzz_resize_planners.rs` /
  `fuzz_commit_planners.rs` — the simpler pure-planner archetype
  for the ops that need only a single staged refblock (0/1/2).
- `src/fuzz/Cargo.toml` — the `[[bin]]` registry; add a stanza,
  and the `check` crate to `[dependencies]`.
- `.github/workflows/coverage-fuzz.yml` — the nightly target list
  (~line 213), the `workflow_dispatch` `N_TARGETS` default (~line
  152), and the nightly-timeout comment (~line 173) that the math
  `1200s × N_targets ≤ 480 min` is checked against.
- `scripts/extract-fuzz-corpus.py` — `create_minimal_seeds` and
  the per-target seed builders (`build_snapshot_refcount_seed` is
  the model); add a `build_check_repair_seed(op)` and emit one
  seed per op.
- `Makefile` — `fuzz-build` / `fuzz-run` (devcontainer-wrapped
  pinned nightly + cargo-fuzz) for local verification.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). This is phase 9 of
eleven — coverage-guided fuzzing of the repair planners. Phase 10
is the **differential** fuzz (instar vs qemu-img on whole images);
this phase is the in-process **coverage** fuzz of the pure planner
surface: corrupt refblock / count-map / L1 / L2 buffers in, assert
no panic, no out-of-bounds write, and that every success path
honours the planners' documented contracts.

I prefer one commit per logical change; the commit must build and
have a clear message. This phase adds a fuzz target plus its
corpus/CI wiring — no instar source change, so `check.bin` is
byte-identical.

## Situation

`check --repair` ships (phases 4–6) and has an end-to-end
integration suite (phase 8). The planners in
`src/crates/check/src/qcow2.rs` are pure functions over
caller-staged byte slices at every qcow2 refcount width (1/2/4/8/
16/32/64-bit, sub-byte entries packed LSB-first within a byte) —
exactly the "no corruption regardless of input" surface the master
plan flags for fuzzing. The unit tests cover the hand-reasoned
cases; coverage fuzzing is what exercises the adversarial widths,
truncated/over-sized slices, and the sub-byte masking edges that a
fixed test set cannot enumerate.

The fuzz crate already has the pattern (`fuzz_snapshot_refcount`),
the build/run plumbing (Makefile `fuzz-build`/`fuzz-run`,
devcontainer + cargo-fuzz), the CI lanes (`coverage-fuzz.yml`:
nightly 1h/target, PR smoke, post-merge 15s smoke), and the corpus
seeder (`extract-fuzz-corpus.py`). This phase adds one target,
`fuzz_check_repair`, and threads it through all four.

The check planners reuse snapshot's `read_refcount_in_block` /
`set_refcount_in_block` / `check_refcount_after_addend` /
`update_copied_flags_for_l1`, all already fuzzed by
`fuzz_snapshot_refcount`. The **new** surface this target owns is
the four check-crate wrappers' own logic: the per-entry
predicate/computed-map iteration, the raised/lowered/freed
tallying, the explicit overflow→`AmbiguousCorruption` translation
in `account_reference_in_map`, and the `From`-bridge error mapping.
The COPIED-walker's deep semantic invariants stay owned by
`fuzz_snapshot_refcount` op 3; this target's op 3 is thin (see
open question 2).

## The target design (`fuzz_check_repair`)

Structured-header archetype, mirroring `fuzz_snapshot_refcount`.
A 32-byte LE header selects the op and sizes the staged state; the
pool (bytes 32..) fills the staged buffers cyclically. Reusable
`thread_local!` scratch sized to the worst case. Errors are
silently ignored (they are the documented refuse path); panic and
ASAN are the base oracle; the asserts below are the semantic
oracle.

Proposed header layout (final bit assignments are the
implementer's, but cover at least this):

```text
  0:      op selector (% 4)
  1:      flags: bit0 extended_l2 (op 3)
  2:      refcount_bits selector (% 7 -> 1/2/4/8/16/32/64)
  3:      cluster_bits = 9 + (% 6)  (9..=14)            (op 3)
  4:      block byte length seed -> staged refblock/map size
  5:      entries_in_block seed (may exceed capacity, to drive
          the MisalignedAccess bounds path on purpose)
  6:      L1 entry count (% 65)                          (op 3)
  7:      L2 slot length = (% 65) * 16                   (op 3)
  8..16:  reference/presence bitmask (u64): is_referenced
          for op 0, L2-slot presence for op 3
  16..24: computed-map driver (u64): per-entry "computed"
          selector for op 2, target index for op 1
  24..32: misc seeds (op-1 accumulate count, op-2 None-mask, ...)
```

Pool order: refblock/map bytes, then (op 3 only) L1 bytes, the L2
pool, and a staged "refblocks" array for the COPIED walker's
`refcount_for_cluster`. `entries_in_block` is taken from a seed and
**not** clamped to the slice capacity, so the harness deliberately
reaches the `read/set_refcount_in_block` bounds error (which the
planners surface as `RepairError::MisalignedAccess` via `?`) — a
returned `Err` there is correct, not a crash.

### Ops and their invariants

All ops share **invariant 0 (base oracle): no panic, no
out-of-bounds access, for every input** — enforced by libfuzzer +
ASAN with no assert needed. The per-op asserts below fire only on
the success path.

**Op 0 — `reclaim_leaks_in_refblock`** (the leaks tier):
1. *Containment / sub-byte masking.* On `Ok(n)`, recompute the
   expected post-state by replaying the contract entry-by-entry on
   a pre-call copy (for each in-range local index: if stored rc > 0
   and `!is_referenced(idx)`, set 0, else leave) and assert the
   refblock is **byte-identical** to that replay. This is the core
   sub-byte test: `set_refcount_in_block` must touch only the
   target entry's bits, leaving co-resident entries in the same
   byte intact at widths 1/2/4.
2. *Count correctness.* `n` equals the number of entries the
   replay changed.
3. *Predicate discipline.* A referenced entry keeps its exact
   stored value (even rc > 1 — the safe tier never lowers a live
   cluster); an rc == 0 entry is untouched.
4. *Idempotence.* A second call with the same predicate returns
   `Ok(0)` and leaves the buffer byte-identical.
5. *Generalisation cross-check with op 2 (the doc's "this
   generalises reclaim" claim).* Run `correct_refcounts_in_refblock`
   on an independent copy with `computed_for(i) = if
   is_referenced(i) { Some(stored_i) } else { Some(0) }`. The
   resulting buffer must be byte-identical to reclaim's, with
   `tally.freed == n`, `tally.raised == 0`, `tally.lowered == 0`
   (referenced entries map to their own stored value → unchanged;
   unreferenced rc>0 → freed). Asserted only when both calls
   return `Ok`.

**Op 1 — `account_reference_in_map`** (count accumulation):
1. *Single-step containment.* On `Ok(())`, the targeted entry reads
   back `cur + 1` and every other byte is unchanged (sub-byte
   mask). On `Err`, the map is byte-identical (both the
   overflow path and the bounds path return before any `set`).
2. *Overflow classification.* `Err(AmbiguousCorruption)` ⟺ the
   pre-call stored value was the width's maximum (`2^bits − 1`,
   saturated) for an in-range index. A non-saturated in-range
   index never yields `AmbiguousCorruption`.
3. *Bounds classification.* An out-of-range index yields
   `MisalignedAccess` (never `AmbiguousCorruption`), buffer
   byte-identical.
4. *Accumulation round-trip.* Calling it `k` times on the same
   in-range index (with `cur + k ≤ max`) raises the entry by
   exactly `k`.

**Op 2 — `correct_refcounts_in_refblock`** (the all tier):
1. *Containment.* On `Ok(tally)`, replay the contract on a
   pre-call copy (for each in-range local index: `None` → skip,
   `Some(want)` → write `want`) and assert byte-identity — `None`
   entries are never read or written (a partial map must not zero
   uncovered entries), sub-byte co-residents preserved.
2. *Tally correctness.* `raised` = #(want > have), `lowered` =
   #(0 < want < have), `freed` = #(want == 0 ∧ have > 0); entries
   with want == have are not counted.
3. *Post-state.* Every covered (`Some`) entry's stored refcount
   equals its computed value.
4. *Idempotence.* A second call with the same `computed_for`
   returns an all-zero tally and leaves the buffer byte-identical
   (have == want everywhere covered).

**Op 3 — `reconcile_copied_flags_for_l1`** (thin wrapper; light
invariants — see open question 2):
1. *Pass-through fidelity.* On `Ok(rewrites)`, the result and the
   mutated L1/L2 bytes are identical to calling
   `snapshot::qcow2::update_copied_flags_for_l1` directly on an
   independent copy of the same staged state (the wrapper adds only
   the `?`/`From` error mapping).
2. *COPIED consistency + idempotence* (delegated proof): the deep
   "COPIED ⟺ refcount == 1, offset-zero scrubbed, second run = 0
   rewrites, byte-identical" invariants are owned by
   `fuzz_snapshot_refcount` op 3 and cross-referenced, not
   re-derived here. This op asserts only the pass-through and a
   single idempotence check (second wrapper call → `Ok(0)`,
   byte-identical) to lock the check entry point and exercise the
   `From` bridge.

## Open questions

### 1. One combined target, or one per planner?

**Resolved: one combined `fuzz_check_repair`** with an op selector,
mirroring `fuzz_snapshot_refcount` (which fuzzes seven snapshot
primitives in one target). The planners share staged-state shape
(refcount blocks at a common width) and a single target keeps the
CI target list, corpus layout, and Cargo `[[bin]]` registry small.
libfuzzer explores the op byte like any other.

### 2. Does the thin `reconcile_copied_flags_for_l1` wrapper need
its own deep invariants?

**Resolved: no — keep op 3 light.**
`reconcile_copied_flags_for_l1` forwards verbatim to
`snapshot::qcow2::update_copied_flags_for_l1` and only maps the
error. The COPIED-walker's containment / consistency / idempotence
invariants are already proven adversarially by
`fuzz_snapshot_refcount` op 3 against the same primitive. Op 3 here
asserts pass-through fidelity (wrapper output == direct call) plus
one idempotence check, which covers the wrapper's own surface (the
`?`/`From` translation) without duplicating the heavy proof. State
this delegation explicitly in the module docs so a later reader
does not think the wrapper is under-fuzzed.

### 3. Should the harness clamp `entries_in_block` to the staged
slice, or let it overflow?

**Resolved: let it overflow (seed-driven, unclamped).** The
planners' documented error path is precisely "out-of-range
`local_idx` against an under-sized slice → `MisalignedAccess` via
`?`". Clamping would make that path unreachable and leave the
bounds arithmetic unfuzzed. The asserts treat `Err` as a valid
outcome; only the success path is constrained.

### 4. Are new dependencies / features needed in the fuzz crate?

**Resolved: add `check = { path = "../crates/check" }` to
`src/fuzz/Cargo.toml` `[dependencies]`.** The `check` crate is
`#![no_std]` with no features to select. It pulls `snapshot`
(already a fuzz-crate dep) transitively; no version pin or feature
flag is required.

## Execution

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| 9a | high | opus | Write `src/fuzz/fuzz_targets/fuzz_check_repair.rs` (the four-op structured-header target above) and register it: add the `check` path dependency and a `[[bin]]` stanza (`doc = false`, `test = false`) to `src/fuzz/Cargo.toml`. Mirror `fuzz_snapshot_refcount`'s scratch/pool/`fill_from_pool` structure and module-doc style. Encode the per-op invariants exactly as specified; for op 0's cross-check and op 3's pass-through, run the comparison planner on an **independent copy** of the staged state. Errors are silently ignored; the base oracle is panic/ASAN. Document in the module header which invariants are delegated to `fuzz_snapshot_refcount` (op 3's deep COPIED proof) so the wrapper is not mistaken for under-fuzzed. This is the correctness-critical step — a wrong assert burns the nightly lane with phantom crashes (cf. the snapshot phase-12 invariant-2 duplicate-visit subtlety), so each assert's success-path condition must be provably implied by the contract, not merely plausible. |
| 9b | low | sonnet | Add `build_check_repair_seed(op)` to `scripts/extract-fuzz-corpus.py` (model: `build_snapshot_refcount_seed`) producing a small consistent staged fragment that takes each op's success path, and emit one seed per op (`minimal_op0..3`) into the `fuzz_check_repair` corpus dir in `create_minimal_seeds`. The seed must match the target's exact header layout and pool order (keep the two in sync — note that in a comment in both files). |
| 9c | low | sonnet | Wire CI: add `fuzz_check_repair` to the nightly target list in `.github/workflows/coverage-fuzz.yml` (~line 213), bump the `workflow_dispatch` `N_TARGETS` default 21 → 22 (~line 152), and re-check the nightly-timeout comment (~line 173): `1200s × 22 = 26400s = 440 min ≤ 480 min` still fits — update the count in the comment. Leave the PR-smoke single target (`fuzz_format_detect`) unchanged. No Makefile change needed — `fuzz-build`/`fuzz-run` take `FUZZ_TARGET` as a parameter. |
| 9d | medium | sonnet | Verify and commit. Build the target in the devcontainer (`make fuzz-build FUZZ_TARGET=fuzz_check_repair`) and run a bounded smoke (`make fuzz-run FUZZ_TARGET=fuzz_check_repair FUZZ_DURATION=60`) seeded from the new corpus; confirm it builds clean and finds no crash. Confirm `cargo build -p check` / the workspace still build (no source change, so they must). Run `pre-commit run --files` over only the changed files (the rust hook will lint the new target via the workspace; if it tries to run as root and poisons ownership, fall back to the scoped file list excluding the rust hook and note it). Present ONE commit on the `check-repair` branch: the new target + Cargo registration + corpus seeds + CI wiring. The message: coverage-guided fuzz harness for the qcow2 repair planners — reclaim/account/correct/reconcile, asserting sub-byte-masked containment, tally correctness, the overflow→AmbiguousCorruption and bounds→MisalignedAccess classifications, idempotence, and the reclaim⊂correct generalisation; COPIED-walker depth delegated to fuzz_snapshot_refcount; corpus + nightly CI wired; no instar source change (`check.bin` unchanged). |

## Agent guidance

### Execution model

Sub-agents implement 9a–9c; the management session reviews the
target (especially that each success-path assert is provably
implied by the contract, not just plausible), builds and
smoke-runs it in the devcontainer, and commits. The hazard is a
**false assertion** — an invariant that holds for the hand-reasoned
cases but not for some adversarial width/slice — which would turn
the nightly lane red on a non-bug. When in doubt, weaken the assert
to a guarded conditional (as `fuzz_snapshot_refcount` does for its
duplicate-visit precondition) rather than assert unconditionally.

### Model and effort notes

- **9a is high opus**: the asserts are the deliverable; their
  soundness across all widths and slice sizes is the whole point,
  and a wrong one is worse than no assert.
- **9b / 9c are low sonnet**: a seed builder mirroring an existing
  one, and a three-line CI list/count/comment edit.
- **9d is medium sonnet**: devcontainer build + bounded smoke run +
  one commit.

### Management session review checklist

- [ ] Every success-path assert is implied by the planner's doc
      contract; adversarial-width counterexamples considered.
- [ ] Sub-byte widths (1/2/4-bit) exercised, and containment
      asserts compare the **whole** buffer (co-resident bits) not
      just the target entry.
- [ ] `entries_in_block` is unclamped; the `MisalignedAccess`
      bounds path is reachable and treated as a valid `Err`.
- [ ] Op 0's reclaim⊂correct cross-check and op 3's pass-through
      run on **independent copies** of the staged state.
- [ ] The module docs name the invariants delegated to
      `fuzz_snapshot_refcount`.
- [ ] Corpus seeds match the target's header/pool layout (kept in
      sync, noted in both files); one seed per op.
- [ ] CI: target in the nightly list, `N_TARGETS` 22, timeout
      comment re-checked.
- [ ] Builds + smoke-runs clean in the devcontainer; no crash;
      `check.bin` byte-identical (no source change).

## Administration and logistics

### Success criteria

* `fuzz_check_repair` exists, registered in `src/fuzz/Cargo.toml`,
  builds via `make fuzz-build`, and runs a bounded smoke without a
  crash.
* It exercises all four planners across the 1/2/4/8/16/32/64-bit
  widths with the documented invariants asserted on the success
  path and the refuse/error paths reachable.
* Corpus seeds (one per op) land under `fuzz_check_repair`.
* CI nightly runs it; `N_TARGETS` and the timeout comment updated.
* One commit on the `check-repair` branch; no instar source
  change, `check.bin` byte-identical.

### Future work created by this phase

- Phase 10 (differential fuzz): random corruptions into a valid
  image, repaired by both instar and qemu-img, compared for
  `qemu-img check` cleanliness and guest-data equivalence — the
  whole-image counterpart to this in-process planner fuzz.
- If the guest later grows the snapshot/compression-aware recount
  (currently those images are refused), extend op 2's computed-map
  driver to cover multi-reference counts from a snapshot walk.

### Bugs fixed during this work

To be filled in if fuzzing surfaces a real planner defect (a
genuine panic / OOB / contract violation, distinct from a false
assertion in the harness — the latter is a harness fix).

### Documentation index maintenance

A phase plan, not a master plan: **not** added to `order.yml`. The
master plan's phase-9 row is updated to "Landed" once the commit is
in.

### Back brief

Before executing any step, back brief the operator on your
understanding — especially that the deep COPIED-walker invariants
are delegated to `fuzz_snapshot_refcount` (op 3 here is the thin
wrapper's pass-through only), that `entries_in_block` is
deliberately unclamped so the bounds-error path is fuzzed, and that
a false success-path assert is the primary hazard.
