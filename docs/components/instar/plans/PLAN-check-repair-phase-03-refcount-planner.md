# PLAN-check-repair phase 03: refcount-rebuild + COPIED reconciliation (lossy tier)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly and ground your
answers in what the code actually does today; do not speculate
when you could read. Research qemu's repair semantics where
needed — `block/qcow2-refcount.c` (`qcow2_check_refcounts`,
`check_refcounts_l1`, `check_refcounts_l2`,
`rebuild_refcount_structure`) and `docs/qcow2/qcow2-refcount.md`
(the "Relationship to COPIED Flag" and consistency-checking
sections) are authoritative. The code this phase depends on:

- `src/crates/check/src/{lib.rs,qcow2.rs}` — the phase-1 type
  surface (`RepairError`, `RepairCounters`, `From<SnapshotError>`)
  and the phase-2 planner (`reclaim_leaks_in_refblock`), which
  this phase generalises.
- `src/crates/snapshot/src/qcow2.rs` — the primitives this phase
  composes:
  - `read_refcount_in_block` / `set_refcount_in_block` (all
    widths; sub-byte LSB-first).
  - `check_refcount_after_addend(current, addend, refcount_bits)
    -> Result<u64, SnapshotError>` — overflow/underflow-checked
    arithmetic; positive overflow returns
    `RefcountOverflow { at_host_offset }`.
  - `update_copied_flags_for_l1(l1_bytes: &mut [u8], cluster_bits,
    l2_for_index: FnMut(u32) -> Option<&mut [u8]>,
    refcount_for_cluster: FnMut(u64) -> Option<u64>, extended_l2)
    -> Result<u32, SnapshotError>` — the COPIED reconciler. Read
    its body: the rule it enforces is **`COPIED` set iff the
    referenced cluster's refcount == 1**, for both the L1 entry
    (keyed on the L2-table cluster's own refcount) and each L2
    entry (keyed on the data cluster's refcount).
- `src/operations/check/src/main.rs` — `check_qcow2`; the
  refcount-error detection at lines 2424 and 2655 (a *referenced*
  cluster whose stored refcount is **0** → `refcount_errors`),
  and the boolean reference bitmap `bmp`.

The parent master plan is
[PLAN-check-repair.md](/components/instar/plans/PLAN-check-repair/). Read its "Design
overview: the repair safety model" and open questions 4
(in-place correction vs full rebuild), 5 (success = post-repair
`qemu-img check` cleanliness), and 7 (no refcount-table growth).
This is phase 3 of ten — **the highest-correctness-stakes phase**:
a wrong refcount repair silently corrupts an image the user asked
us to fix. Still pure, no I/O, not wired into any binary (phase 4
does that).

I prefer one commit per logical change, and at minimum one commit
per phase. The commit must build, pass tests, and have a clear
message.

## Situation

### Why the lossy tier needs reference *counting*, not a patch

Phase 2's safe tier frees clusters that nothing references
(stored refcount > 0, boolean `bmp` says unreferenced → set 0).
It cannot touch two error classes the lossy `all` tier owns:

1. **Refcount too low** — a *referenced* cluster whose stored
   refcount is below its true reference count (the detector flags
   the stored-0 sub-case as `refcount_errors`, lines 2424/2655).
   This is the **dangerous** direction: an under-counted cluster
   can be reallocated and overwritten while still in use → data
   loss. It must be raised to the *correct* value.
2. **Refcount too high on a live cluster** — stored refcount
   above the true count (but the cluster *is* referenced, so it
   is not a phase-2 leak). Lower it to the correct value.

The correct value in both cases is **the actual number of
references**, which can be > 1 (clusters shared between the
active image and snapshots). instar's current detector uses a
**boolean** bitmap, so it knows "referenced or not" but not "how
many times". There is no safe shortcut: naively setting a
stored-0 cluster to 1 is wrong (and unsafe) whenever the cluster
is referenced more than once. **The lossy tier therefore must
recount references per cluster** — exactly qemu's
`qcow2_check_refcounts`: build a fresh refcount map by walking
all metadata, then fix every discrepancy, then reconcile COPIED.

### Division of labour: pure planner (phase 3) vs guest (phase 4)

The recount needs a per-cluster *computed-refcount map* and a
walk over every metadata structure (active L1 → L2s, each
snapshot L1 → L2s, the refcount table + blocks, the snapshot
table, the header/L1/refcount clusters themselves). The **walk
is guest I/O orchestration → phase 4**. Phase 3 provides the
**pure, testable primitives** the walk and the fix-up use:

1. Accumulate a reference into a staged computed-refcount map
   (overflow-checked).
2. Correct an on-disk refcount block against the computed map.
3. Reconcile COPIED flags on an L1 from the corrected refcounts.

The computed-refcount map is a caller-staged `&mut [u8]`
interpreted as refcount entries at the image's width — a second
refcount structure held in guest memory, mirroring qemu's
in-memory `refcount_table`. Its size bounds the repairable image
(see open question 5); phase 4 owns the capacity decision, phase
3 bounds-checks defensively.

### What this phase produces

In `src/crates/check/src/qcow2.rs`:

1. `account_reference_in_map(map: &mut [u8], cluster_index: u64,
   refcount_bits: u32) -> Result<(), RepairError>` — increments
   the computed count of `cluster_index` by 1 via
   `read_refcount_in_block` + `check_refcount_after_addend(+1)` +
   `set_refcount_in_block`. A positive overflow (a cluster
   referenced more times than the width can store — genuine
   unrepairable corruption) is translated **explicitly** to
   `RepairError::AmbiguousCorruption` (not the generic
   `From<SnapshotError>` mapping); an out-of-range
   `cluster_index` against the map slice surfaces
   `MisalignedAccess` via `?`.

2. `RefcountFix` enum `{ Unchanged, Raised { from: u64, to: u64 },
   Lowered { from: u64, to: u64 } }` and
   `RefcountFixTally { raised: u32, lowered: u32, freed: u32 }`
   (`freed` = lowered-to-zero, i.e. a leak discovered by the
   count rather than the boolean predicate).

3. `correct_refcounts_in_refblock(refblock: &mut [u8],
   entries_in_block: u64, refcount_bits: u32, computed_for:
   impl FnMut(u64) -> Option<u64>) -> Result<RefcountFixTally,
   RepairError>` — for each entry, compare the stored refcount to
   `computed_for(local_idx)`; where they differ, set stored =
   computed and tally the direction (`computed_for` returns
   `None` for entries outside the walk's covered range → skip,
   leave untouched). Reuses `set_refcount_in_block`. This
   **generalises** phase 2: a leak is just `computed == 0`. (Phase
   2 stays — the safe tier uses the cheap boolean predicate and
   no count-map memory; the lossy tier uses this count-driven
   correction. They are the two cost/capability tiers, mirroring
   `-r leaks` vs `-r all`.)

4. `reconcile_copied_flags_for_l1(l1_bytes: &mut [u8],
   cluster_bits: u32, l2_for_index, refcount_for_cluster,
   extended_l2) -> Result<u32, RepairError>` — a thin wrapper over
   `snapshot::update_copied_flags_for_l1` that maps its
   `SnapshotError` to `RepairError`, keeping the `check` crate the
   single home for repair entry points. The guest supplies
   `refcount_for_cluster` backed by the **corrected** on-disk
   refcounts (reconciliation runs after correction).

Plus unit tests over synthetic buffers.

### What this phase does NOT do (deferred)

- **The guest counting-walk** over all metadata structures, the
  computed-map staging, the `corrupt`-bit set/clear ordering +
  `fsync_input`, and the write-backs — all **phase 4**.
- **Refcount-structure growth/relocation** — if the corrected
  refcounts need more refblocks than the existing refcount table
  addresses (the refcount structure itself was undersized), phase
  3 does not grow it; the guest reports `FLAG_REPAIR_INCOMPLETE`.
  Inherited from master-plan open question 7 / the snapshot
  allocator's `RefcountExhausted` boundary.
- **Over-capacity images** — if the image's cluster count exceeds
  the staged computed-map, the guest refuses with
  `FLAG_REPAIR_INCOMPLETE` rather than partially recounting.
- No operation binary imports the `check` crate yet, so
  **`check.bin` and every operation binary stay byte-identical**
  (the property phases 1–2 held).

## Open questions

### 1. Count into a separate map, or into the on-disk refblocks?

**Resolved: a separate staged computed-refcount map.** You cannot
overwrite the on-disk refcounts while still counting — the
original is needed for the discrepancy comparison, and no entry
can be finalised until the whole-image walk completes (references
arrive from anywhere). qemu uses a separate in-memory array; so
do we.

### 2. Does phase 3 replace phase 2's `reclaim_leaks_in_refblock`?

**Resolved: no, both coexist.** `correct_refcounts_in_refblock`
subsumes leak-freeing only when a full count map exists. The safe
(`leaks`) tier deliberately avoids the count-map memory cost and
uses phase 2's boolean predicate. The lossy (`all`) tier uses the
count-driven correction. Keeping both matches `qemu-img -r
leaks` (cheap) vs `-r all` (full recount).

### 3. What value does a refcount-too-low cluster get raised to?

**Resolved: its computed reference count, never a naive 1.** This
is the entire reason the lossy tier recounts. Raising to 1 when a
cluster is shared (refcount should be 2+) would re-introduce the
dangerous under-count. The computed map carries the true count.

### 4. How is a counting overflow handled?

**Resolved: explicit `RepairError::AmbiguousCorruption`.** A
cluster referenced more times than the refcount width can store
is unrepairable without widening the refcount order (out of
scope) — refuse rather than guess. `account_reference_in_map`
matches the `RefcountOverflow` result specifically instead of
relying on the generic `From<SnapshotError>` (which maps overflow
to `MisalignedAccess` — wrong semantics here).

### 5. Bounded-map capacity — refuse or partial?

**Resolved: refuse the whole lossy repair (`FLAG_REPAIR_INCOMPLETE`),
never partial.** A partially-recounted image yields wrong
"computed" values for uncounted clusters and would corrupt on
correction. The capacity check is the guest's (phase 4); phase
3's primitives bounds-check the slice and return `MisalignedAccess`
if the guest mis-sizes. Documented as a real v1 limit; large
images fall back to `qemu-img check -r` until refcount-structure
growth lands (future work).

### 6. COPIED reconciliation: wrapper in `check`, or call snapshot directly?

**Resolved: a thin `reconcile_copied_flags_for_l1` wrapper in the
`check` crate.** It only maps `SnapshotError → RepairError` and
returns the rewrite count. Keeping the entry point in `check`
makes the crate the single home for repair logic and gives phase
4 one consistent error type. The COPIED rule itself
(`refcount == 1`) lives in the already-tested snapshot reconciler
— do not reimplement it.

### 7. Does phase 3 emit `RepairCounters`?

**Resolved: no — it returns per-block `RefcountFixTally` and per-L1
rewrite counts; the guest (phase 4) folds them into
`RepairCounters` and owns `incomplete`.** Consistent with phase 2
returning a bare count. `freed` maps to `RepairCounters.leaks`,
`raised + lowered` to `RepairCounters.refcounts`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Implement the counting + correction primitives in `src/crates/check/src/qcow2.rs`. (i) `account_reference_in_map(map: &mut [u8], cluster_index: u64, refcount_bits: u32) -> Result<(), RepairError>`: `let cur = read_refcount_in_block(map, cluster_index, refcount_bits)?;` then `match check_refcount_after_addend(cur, 1, refcount_bits) { Ok(n) => set_refcount_in_block(map, cluster_index, refcount_bits, n)?, Err(SnapshotError::RefcountOverflow { .. }) => return Err(RepairError::AmbiguousCorruption), Err(e) => return Err(e.into()) }`. (ii) `RefcountFix { Unchanged, Raised { from: u64, to: u64 }, Lowered { from: u64, to: u64 } }` and `RefcountFixTally { raised: u32, lowered: u32, freed: u32 }` (derive Debug/Clone/Copy/PartialEq/Eq/Default for the tally). (iii) `correct_refcounts_in_refblock(refblock, entries_in_block, refcount_bits, mut computed_for: impl FnMut(u64) -> Option<u64>) -> Result<RefcountFixTally, RepairError>`: for `local_idx` in `0..entries_in_block`, `let Some(want) = computed_for(local_idx) else { continue };` `let have = read_refcount_in_block(refblock, local_idx, refcount_bits)?;` if `want != have` then `set_refcount_in_block(refblock, local_idx, refcount_bits, want)?` and tally — `want > have` → raised; `want < have && want > 0` → lowered; `want == 0` → freed. Add ~18 unit tests over synthetic buffers: counting accumulates correctly across repeated `account_reference_in_map` calls; counting overflow at each width returns `AmbiguousCorruption` (e.g. 16-bit: account 65536 times — or seed the map near max and account once more); out-of-range `cluster_index`/`local_idx` → `MisalignedAccess`; correction raises 0→2, lowers 5→2, frees 3→0, leaves equal entries Unchanged with the tally exact; `computed_for` returning None skips an entry (left byte-identical); all widths 1/2/4/8/16/32/64; sub-byte neighbour preservation when correcting one entry among packed neighbours; a referenced-but-stored-0 cluster (the dangerous case) is raised to its multi-reference computed value (e.g. 0→3). Use opus: this is the load-bearing correctness step; the both-directions tally, the overflow-to-AmbiguousCorruption translation, and sub-byte correctness are the traps. |
| 3b | high | opus | worktree | Implement `reconcile_copied_flags_for_l1` in `src/crates/check/src/qcow2.rs` as a thin wrapper over `snapshot::qcow2::update_copied_flags_for_l1`, with the same generic parameters/closures, mapping the returned `SnapshotError` to `RepairError` (via `?`/`From`) and returning the `u32` rewrite count as `Result<u32, RepairError>`. Remove the now-used `update_copied_flags_for_l1` (and `for_each_cluster_in_l1` if used) from the phase-3 `#[allow(unused_imports)]` gate; keep the gate only for symbols still unused (drop any that remain unused to stay warning-clean). Add ~8 unit tests building synthetic L1 + L2 byte tables and a `refcount_for_cluster` closure: COPIED is SET on an L1/L2 entry whose cluster has computed refcount == 1; CLEARED when refcount > 1 (shared); standard L2 and extended-L2 (16-byte entries, subcluster bitmap preserved) both handled; idempotence (running twice is a no-op the second time, rewrite count 0); a `refcount_for_cluster` returning None for a needed cluster surfaces the snapshot crate's error mapped to `RepairError`. Cross-reference the existing `update_copied_flags_for_l1` tests in `src/crates/snapshot/src/qcow2.rs` for fixture-construction patterns; do not reimplement the COPIED rule. Use opus: the extended-L2 stride and the refcount==1 boundary in both directions are easy to get subtly wrong, and this is the invariant that, if mis-set, makes qemu refuse the image. |
| 3c | low | sonnet | worktree | Verify and commit. From the worktree `src/` with the cargo target dir redirected to an owned path (lint-as-root ownership gotcha): confirm `cargo test -p check` passes the ~26 new tests, `make test-rust` green, `make instar` + `make check-binary-sizes` show **`check.bin` and every operation binary byte-identical to post-phase-2** (no operation imports the crate yet), `make lint` and `pre-commit run --all-files` clean. Watch for the rustfmt comment-alignment trap seen in phases 1–2 (don't put explanatory comment blocks after an inline `// ...` on a `let`). Stage and present ONE commit (steps 3a+3b) with the `~/.claude/CLAUDE.md` convention (≤50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Assisted-By + Co-Authored-By with model/context/effort). The message explains: this lands the lossy-tier refcount-rebuild primitives (counting into a computed-refcount map, both-directions correction against it) and the COPIED reconciliation wrapper, reusing the snapshot refcount/COPIED primitives; it recounts because the boolean detector cannot, refuses on counting overflow and over-capacity; it is pure and unwired so behaviour is unchanged; the counting-walk orchestration and crash-safe write ordering are phase 4. |

## Agent guidance

### Execution model

All implementation is by sub-agents in the `check-repair`
worktree (itself the isolation from the main checkout — do not
nest a throwaway worktree). The management session reads the
actual diff, runs the gates, and commits.

### Model and effort notes

- **3a and 3b are both high-effort opus.** Phase 3 is the
  riskiest phase in the family; the correctness lives in the
  ~26 unit tests, which need careful synthetic buffers (both-
  directions correction, overflow refusal, sub-byte preservation,
  extended-L2 COPIED). A wrong refcount or COPIED flag silently
  corrupts an image qemu will then refuse.
- **3c is low-effort sonnet**: scripted verify-and-commit.

### Management session review checklist

- [ ] `account_reference_in_map` translates `RefcountOverflow` to
      `AmbiguousCorruption` explicitly; out-of-range →
      `MisalignedAccess`.
- [ ] `correct_refcounts_in_refblock` raises to the **computed**
      value (never naive 1), tallies raised/lowered/freed exactly,
      and skips `None` entries byte-for-byte.
- [ ] Sub-byte correction preserves neighbour entries (a test
      proves it at 1/2/4-bit).
- [ ] `reconcile_copied_flags_for_l1` sets COPIED iff refcount==1,
      handles extended-L2 (subcluster bitmap untouched), and is
      idempotent.
- [ ] No reimplementation of the COPIED rule or refcount-width
      arithmetic — the snapshot primitives are reused.
- [ ] Errors propagate as `RepairError`; no `unwrap` in the
      planners.
- [ ] `cargo build -p check` warning-clean (imports tidied).
- [ ] `make instar` + `check-binary-sizes`: `check.bin` and every
      operation binary byte-identical to post-phase-2.
- [ ] `make test-rust`, `make lint`, `pre-commit` clean; crate
      stays safe Rust (no `unsafe`).

## Administration and logistics

### Success criteria

* `account_reference_in_map`, `correct_refcounts_in_refblock`
  (+ `RefcountFix`/`RefcountFixTally`), and
  `reconcile_copied_flags_for_l1` exist in
  `src/crates/check/src/qcow2.rs` with ~26 passing unit tests.
* They reuse the snapshot refcount + COPIED primitives and
  propagate errors via `RepairError`.
* `make instar`, `make test-rust`, `make check-binary-sizes`,
  `make lint`, `pre-commit run --all-files` all pass; every
  operation binary byte-identical to post-phase-2.
* Lands in one commit on the `check-repair` branch.

### Future work created by this phase

- **Refcount-structure growth/relocation** when the corrected
  refcounts need more refblocks than exist (OQ7); until then the
  guest reports `FLAG_REPAIR_INCOMPLETE`.
- **Unbounded recount** for images whose cluster count exceeds
  the staged computed-map; until then over-capacity images are
  `FLAG_REPAIR_INCOMPLETE`.
- **Guest counting-walk + crash-safe write ordering** — phase 4,
  which composes these primitives over a real image.

### Bugs fixed during this work

To be filled in if implementation surfaces anything (e.g. a gap
in the snapshot reconciler exposed by the extended-L2 tests, or a
detector blind spot — the over-count-on-a-live-cluster case is
invisible to today's boolean `bmp`; if phase 4 needs it surfaced,
note it here for a detector follow-up).

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`order.yml`. Update the master plan's phase-3 Execution-table row
to "Landed" with a pointer to this file once the commit is in.

### Back brief

Before executing any step, back brief the operator on your
understanding of the phase and how it aligns with the master
plan's safety model — especially that the lossy tier recounts
references (never guesses a refcount), refuses on overflow and
over-capacity, and reconciles COPIED to `refcount == 1` only
after corrections are applied.
