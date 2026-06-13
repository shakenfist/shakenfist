# PLAN-snapshot phase 08: apply mode (MODE_APPLY)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the phase 6/7
mutating paths end-to-end: `run_create` / `run_delete` and the
shared staging helpers in `src/operations/snapshot/src/main.rs`,
the `src/crates/snapshot/` crate including `table.rs` and
`precheck_snapshot_refcount`, the host helper
`run_snapshot_mutating_guest` in `src/vmm/src/main.rs`, and the
four verification harnesses under `tools/`), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead. Where a
question touches on qemu behaviour, the authoritative references
are `block/qcow2-snapshot.c::qcow2_snapshot_goto` /
`find_snapshot_by_id_or_name`,
`block/qcow2-refcount.c::qcow2_update_snapshot_refcount`
(read it in full — its L1-writeback rule and per-entry COPIED
rewrite govern this phase's byte-level requirements), and
`qemu-img.c` (`SNAPSHOT_APPLY` case) in qemu 10.0.x — fetch from
`https://gitlab.com/qemu-project/qemu/-/raw/v10.0.0/...` if
needed — plus the locally installed `qemu-img` 10.0.8 for
empirical verification.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 8 of
fourteen — the mode the master plan rates the most delicate of
the per-mode phases (the active L1 is overwritten in place).

**Model-tier note:** phase 7 ran as a single Fable agent and the
experiment is judged a success (fewer tool calls, two correct
beyond-the-brief judgement calls, zero rework). Phase 8 is
dispatched the same way.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phases 1–7 landed list, create, and delete end-to-end, the
mutator primitives, the table helpers, the read-only precheck,
the shared guest staging helpers, the shared host launch
helper, and four verification harnesses. Phase 8 lands the last
mutating mode: `instar snapshot -a SNAPSHOT` (MODE_APPLY /
"goto"). Apply is structurally different from create and
delete: it rewrites the **active L1 in place**, it never
touches the snapshot table or the header, and it is the one
mode where refcounts move in **both** directions in a single
operation (+1 over the incoming chain, −1 over the outgoing
one).

### Empirically established qemu-img 10.0.8 behaviour

Verified during phase 8 planning against the installed binary
and the v10.0.0 sources. Requirements, not guesses:

1. **Success is silent**, exit 0. Not-found prints
   `qemu-img: Could not apply snapshot 'X': Failed to load
   snapshot: No such file or directory` and exits 1 without
   touching the image.
2. **The argument matches by ID first, then by name** —
   `qcow2_snapshot_goto` resolves via
   `find_snapshot_by_id_or_name`, which runs a **full pass
   over the table comparing IDs**, and only if no ID matches,
   a **second full pass comparing names**. This is the
   opposite asymmetry from delete (phase 7: name only).
   Observed: with `id=1,name="2"` and `id=2,name="x"`,
   `-a 2` applies **ID 2** (the snapshot named "x"), where
   `-d 2` deletes the one *named* "2". Note the two-full-pass
   structure: a later entry matching by ID beats an earlier
   entry matching by name. The phase 2 `qcow2::find_snapshot`
   (per-entry id-or-name) is wrong on both counts and remains
   unused; this phase adds the correct pure helper for both
   modes.
3. **disk_size mismatch → qemu TRUNCATES the image** to the
   snapshot's `disk_size` (`blk_truncate` inside
   `qcow2_snapshot_goto`) rather than refusing. This is
   reachable through normal tooling: modern qemu-img allows
   `resize` on images with internal snapshots (verified).
   instar v1 **refuses** instead (open question 1) — a full
   virtual-size truncate embedded in apply is out of scope.
   (instar's own `resize` currently errors on
   snapshot-bearing images, so the mismatch can only arise
   from qemu-resized images.)
4. **L1 sizing:** qemu grows the active L1 when
   `sn->l1_size > s->l1_size` (`qcow2_grow_l1_table`,
   relocation + header update) and **pads with zeros** when
   the snapshot's L1 is smaller (the copy buffer is
   zero-initialised at the current L1's size). Given fact 3's
   refusal, a larger snapshot L1 is only reachable in
   hand-crafted images — v1 refuses it (defence in depth);
   the pad-smaller case is supported.
5. **qemu's ordering** (`qcow2_snapshot_goto`): find →
   validate snapshot L1 bounds → [truncate, refused by v1] →
   [grow L1, refused by v1] → read the snapshot's L1 → **+1
   walk over the snapshot's chain** → `bdrv_pwrite_sync` of
   the snapshot's L1 content (zero-padded to the current L1
   byte size) **over the active L1 offset** → **−1 walk over
   the old active chain** (using the cached in-memory old L1)
   → in-memory L1 swap → **addend-0 walk** over the new
   active chain (pure COPIED refresh).
6. **The walk's byte-level side effects**
   (`qcow2_update_snapshot_refcount`, read in full):
   - Every walk — any addend, including 0 — recomputes each
     visited L2 entry's COPIED bit from the **post-addend**
     refcount and rewrites the entry if it changed. Entries
     classified UNALLOCATED or ZERO_PLAIN get `refcount = 0`
     → COPIED **cleared** (a stale flag on an unallocated /
     zero-plain entry is actively scrubbed).
   - The walked **L1 is written back** (refreshed flags) only
     when `addend >= 0` ("Update L1 only if it isn't deleted
     anyway"). So the **+1 walk writes the snapshot's stored
     L1** with refreshed flags — empirically confirmed:
     apply-after-create changes exactly one byte, the stale
     COPIED bit in the snapshot's stored L1 (offset
     `0x60000`, `0x80 → 0x00`). The −1 walk never writes the
     walked (old active) L1 back — which is also why it
     cannot clobber the just-overwritten active L1.
   - **Freed clusters are never written**: the walk runs with
     `cache_discards = true`, so dirty cache entries for
     clusters whose refcount reaches 0 are *discarded*, not
     flushed. Empirically: a diverged apply (snapshot, then
     writes, then apply) modifies only {refblock, active L1,
     snapshot's stored L1} — the freed old-active L2 and data
     clusters are bit-identical to their pre-apply content
     (with `file.discard=ignore` suppressing the
     protocol-level hole punch, as in phase 7).
7. **Apply writes no timestamps, no snapshot-table bytes, no
   header bytes** (in the v1-supported path). Combined with
   fact 6, **full byte-identity with qemu is achievable for
   every matrix scenario, including diverged applies** —
   stronger than phase 7, which only needed identity on
   freshly-staged fixtures.

### The key flag invariant (why one flag pass suffices)

After an apply, **every cluster reachable from the new active
chain has refcount ≥ 2** — the active L1 is a copy of the
snapshot's L1, so everything active references is also
referenced by the still-present snapshot. Proof sketch: a
new-chain cluster's final refcount = pre + 1 (the +1 walk) −
(1 if it was also in the old active chain). Pre ≥ 2 for
shared-with-old clusters, pre ≥ 1 for snapshot-only clusters;
both cases end ≥ 2. Consequently every COPIED flag on the new
chain ends **clear**, and the flags qemu computes mid-state
(during the +1 walk, for the snapshot's stored L1) equal the
flags at final state. instar therefore computes flags **once,
at final state**, and writes the same bytes qemu produces with
its two-stage rewrite. The matrix's byte-identity assertions
are the empirical check on this argument.

### The apply algorithm (instar's staged adaptation)

1. Feature gates (same set as create/delete, uniform
   `refcount_bits == 16` per open question 2; compressed gate
   on **both** chains).
2. Stage the raw snapshot table (find only — never rewritten).
   Find the target by **ID-then-name, two full passes** over
   the raw table (full-length strings, not the bounded
   parser's 63-byte truncation), via the new pure helper.
   `ERROR_NOT_FOUND` before any write.
3. Geometry checks: `sn.disk_size != virtual_size` →
   `ERROR_L1_SIZE_MISMATCH` (fact 3; a 0 `disk_size` sentinel
   from the streaming parser means the extra-data field was
   absent — qemu's reader defaults it such that the check
   passes; verify `qcow2_read_snapshots`' default and mirror
   it). `sn.l1_size > hdr.l1_size` → same error (fact 4).
   `sn.l1_size < hdr.l1_size` → pad path.
4. Stage both chains (snapshot's L1 + L2 set; active L1 + L2
   set) and the refcount table + refblocks, exactly like
   delete.
5. **Precheck (read-only, before any write):**
   `precheck_snapshot_refcount(SwapForApply { from: active
   L1, to: snapshot L1 })` — validates the increment side for
   overflow and the decrement side for underflow against the
   staged refblocks.
6. In-memory +1 over the snapshot's chain
   (`update_snapshot_refcount(IncrementForCreate)` — the inc
   walk; the create-flavoured name is cosmetic).
7. **Write group A:** all staged refblocks (carrying the
   increments only). `fsync_input(0)`.
8. **Write group B (commit point):** the snapshot's raw L1
   content, zero-padded to `hdr.l1_size * 8` bytes, written
   at `hdr.l1_table_offset` (mirrors qemu's `pwrite_sync`
   with the *unrefreshed* stale flags — the refreshed bytes
   land in group C, exactly as qemu's addend-0 walk
   overwrites its own raw copy later). `fsync_input(0)`.
9. In-memory −1 over the **old** active chain
   (`DecrementForDelete` walk over the staged old active L1 +
   its L2 set).
10. In-memory flag refresh at **final-state** refcounts over
    the **new** chain: a staged padded copy of the snapshot's
    L1 + the snapshot's L2 set, via
    `update_copied_flags_for_l1` — extended by this phase to
    scrub stale COPIED bits on UNALLOCATED / ZERO_PLAIN
    entries (fact 6, first bullet; the current walker skips
    them, which would diverge from qemu on
    contrived-but-valid images).
11. **Write group C:** all staged refblocks (now carrying the
    decrements) + the refreshed L1 written to **both**
    locations — `hdr.l1_table_offset` (padded length) and
    `sn.l1_table_offset` (the snapshot's own `l1_size * 8`
    length; this is the write that replicates fact 6's
    snapshot-stored-L1 flag scrub) — + the dirty
    snapshot-set L2s. Freed old-active-only L2s are **not**
    written (fact 6, third bullet). `fsync_input(0)`.

**Gap surfaced during execution: surviving old-active L2s are
written by qemu.** The algorithm above enumerated group C as
refblocks + both L1 writes + dirty snapshot-set L2s, with freed
old-active L2s never written (fact 6, third bullet). That
enumeration missed a case: when the old active chain shares an
L2 table with a *different* snapshot than the one being applied
(e.g. `s1`, write, `s2`, apply `s1` — the old active L2 is
shared with `s2`), that L2 survives the apply at refcount ≥ 1,
and qemu's −1 walk recomputes its entries' COPIED flags at
post-decrement (= final) refcounts, marks it dirty, and flushes
it (`cache_discards` only drops entries for *freed* clusters;
the trailing `bdrv_flush` writes the rest). Verified
empirically: the s2-shared L2's data entry gains COPIED under
qemu (refcount 2 → 1). This scenario is reachable by the
matrix's own cross-mode row, so byte-identity required closing
it: step 10 gains a second final-state flag refresh over the
staged old active chain, and group C additionally writes back
the surviving (final refcount > 0) active-set L2s — freed ones
are still never written. A physical L2 staged in both sets is
written twice with identical bytes (both refreshes are the same
pure function of entry content and final refcounts). Facts 6
and 7 are unchanged; this is a completion of the group C
enumeration, not a design change.

Crash-safety contract: before group B the image is unchanged
except over-referenced refcounts (leaks). After B the active
view is the snapshot; until C completes, refcounts are
over-referenced and COPIED flags stale — `qemu-img check`
reports repairable leaks / flag warnings, never a dangling
reference. qemu's goto has the same best-effort character
(the master plan's per-mode notes call for documenting this in
quirks; step 8f does). One window differs cosmetically from
qemu: qemu scrubs the snapshot's stored L1 *before* its active
overwrite, instar after — both orders leave only repairable
states, and final bytes are identical.

### What phase 8 produces

1. **Snapshot crate additions:**
   - `table.rs`: `MatchMode { IdThenName, NameOnly }` and
     `find_snapshot_in_table(table, len, nb_snapshots,
     needle, mode) -> Result<Option<FoundSnapshot>,
     SnapshotError>` where `FoundSnapshot` carries `index`,
     `l1_table_offset`, `l1_size`, `disk_size_or_zero`
     (extra-data offset 8 when present, 0 otherwise) — the
     raw-table two-full-pass finder. The delete guest's
     inline find is refactored onto `NameOnly` (phase 7
     harnesses re-run as the regression gate).
   - `qcow2.rs`: `update_copied_flags_for_l1` extended to
     clear stale COPIED on UNALLOCATED / ZERO_PLAIN entries
     (returns them in the rewrite count), with unit tests
     covering standard and extended L2.
   - ~18 new unit tests.
2. **Guest binary:** MODE_APPLY in
   `src/operations/snapshot/src/main.rs` replacing the last
   stub, per the algorithm above. Scratch reuse: the
   delete-mode regions cover both chains; the padded new-L1
   working copy takes the (unused-in-apply) `NEW_TABLE_BUF`
   region or a new region if cleaner — agent's choice within
   the existing `ALLOC_HEAP_BASE` assert discipline.
3. **Host CLI:** `-a` dispatch through
   `run_snapshot_mutating_guest` (argument verbatim, dates
   zero, silent success); the "arrives in phase 8" message
   retired; `snapshot_error_message` made **mode-aware** for
   `ERROR_NOT_FOUND` (delete: name-only; apply: ID then
   name) and `ERROR_L1_SIZE_MISMATCH` (apply: "the
   snapshot's disk size or L1 geometry differs from the
   image's current state — the image was resized after the
   snapshot was taken; qemu-img truncates the image on
   apply, instar refuses; resize the image back to the
   snapshot's size first. See docs/quirks.md").
4. **Verification harness:** `tools/snapshot-apply-matrix.sh`
   + apply rows in a refusals harness, centred on **full
   byte-identity** (fact 7), plus the master plan's
   apply-specific test rows (content restoration via
   `qemu-img compare`).
5. **Docs:** quirks (the `-d` name-only vs `-a` ID-then-name
   asymmetry, presented together; the disk-size-mismatch
   refusal with the qemu-truncates note and the resize-back
   workaround; apply's best-effort crash consistency),
   `docs/qcow2/qcow2-snapshots.md` apply-ordering subsection,
   master-plan corrections (the `-a` per-mode plan: no L1
   grow in v1, the pad path, the disk-size refusal, the
   snapshot-stored-L1 flag write; open question 9 marked
   resolved by the round-trip matrix scenarios; phase 8 row
   → Landed; phase 9 row updated — all three modes now have
   dispatch, phase 9 is consolidation only).

### What phase 8 does not change

- The wire ABI (apply needs only `mode` + `arg`; dates stay
  zero).
- The snapshot table and image header are never written by
  apply (v1 path) — no table helpers needed beyond the
  finder.
- Create, delete, list — byte-identical behaviour,
  re-verified by harness re-runs (the find refactor is the
  one shared-code touch).
- The qcow2 crate. (`qcow2::find_snapshot` is now confirmed
  wrong for *both* mutating-mode semantics; it stays unused —
  phase 14 decides whether to remove or re-document it.)

## Mission and problem statement

After phase 8 lands, on any supported qcow2 image:

```
instar snapshot -a snap1 image.qcow2   # silent, exit 0
qemu-img check image.qcow2             # clean
qemu-img compare image.qcow2 ref.qcow2 # "Images are identical"
```

where `ref.qcow2` is a copy of the image taken when `snap1`
was created — and, from byte-identical inputs, `instar
snapshot -a X` and `qemu-img snapshot -a X` (with
`file.discard=ignore`) produce **byte-identical** images
across every matrix scenario: fresh applies, diverged applies,
ID-vs-name precedence, duplicate names, round-trips, and
multi-snapshot chains. Refusals and not-found leave the image
bit-for-bit untouched.

## Open questions

### 1. disk_size mismatch: truncate like qemu, or refuse?

qemu embeds a full image truncate inside apply (fact 3).
**Working answer: refuse with `ERROR_L1_SIZE_MISMATCH` and a
workaround message.** A virtual-size change cascades into L1
geometry, header, and (for shrink) cluster discard — a
re-implementation of resize inside apply for a case that only
arises when a qemu-resized image meets an old snapshot.
The refusal is honest, documented, and reversible by the user
(`qemu-img resize` back to the snapshot's size, then apply).
Tracked as future work. The matrix pins the refusal (image
untouched) on a qemu-resized fixture.

### 2. Does apply need the `refcount_bits == 16` gate?

Apply allocates nothing (no new table, L1 overwritten in
place), and the scalar refcount accessors support all widths —
technically apply could lift the gate. **Working answer: keep
the uniform gate.** One documented rule for all three mutating
modes beats a per-mode matrix of support; revisit across all
modes at once if a non-16-bit user ever materialises.

### 3. SwapForApply's combined apply pass goes unused — problem?

The disk-barrier ordering forces the increment to be applied
in memory before group A and the decrement after group B, so
the guest calls the inc walk and dec walk separately;
`SwapForApply` is used only by the precheck (both-direction
validation in one call). **Working answer: fine as is.** The
enum variant earns its keep in the precheck; the combined
apply path stays covered by phase 5/6 unit tests. Document the
asymmetry in the function's doc comment rather than churning
the phase 5 API.

### 4. Where do the refreshed-L1 bytes get written, and when?

qemu writes the snapshot's stored L1 (refreshed, mid-state)
during the +1 walk, the raw padded copy over the active L1,
then the active L1 again (refreshed, final-state) in the
addend-0 walk. instar compresses this to: raw padded copy in
group B, one final-state refresh, written to both locations in
group C. **Working answer: provably the same final bytes**
(the flag invariant section); the differing intermediate
window is documented in the quirks crash-consistency note. The
alternative — replicating qemu's mid-state write order
exactly — would need a second flag pass for zero benefit.

### 5. How is the find's disk_size obtained for the geometry check?

The raw-table finder reads extra-data offset 8 directly when
`extra_data_size >= 16`, else reports 0. qemu's reader
(`qcow2_read_snapshots`) defaults an absent `disk_size` so the
goto check passes — the implementing agent verifies the exact
default in the source and mirrors the observable behaviour
(treat absent as matching). A genuinely-zero `disk_size` on a
0-byte image also matches (virtual_size 0). Pinned by a unit
test on the finder and a zero-byte-disk matrix fixture.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | fable | worktree | Crate: the raw-table finder. In `src/crates/snapshot/src/table.rs` add `pub enum MatchMode { IdThenName, NameOnly }`, `pub struct FoundSnapshot { pub index: u32, pub l1_table_offset: u64, pub l1_size: u32, pub disk_size_or_zero: u64 }`, and `pub fn find_snapshot_in_table(table: &[u8], table_len: usize, nb_snapshots: u32, needle: &[u8], mode: MatchMode) -> Result<Option<FoundSnapshot>, SnapshotError>` built on the existing private entry-bounds walk. `IdThenName` = one full pass comparing the id bytes, then (only if no id matched) a second full pass comparing the name bytes — qemu's `find_snapshot_by_id_or_name` shape, where a later id match beats an earlier name match (fact 2). `NameOnly` = single name pass, first match. Full on-disk strings, exact byte compare, empty needle allowed. `disk_size_or_zero` from extra-data offset 8 when `extra_data_size >= 16`, else 0. (ii) Refactor `run_delete`'s inline find in `src/operations/snapshot/src/main.rs` onto `find_snapshot_in_table(..., NameOnly)` — behaviour must be byte-identical (phase 7 harnesses are the gate). (iii) ~10 unit tests: id-pass priority over an earlier name match; name fallback when no id matches; NameOnly ignores ids entirely; first-match within each pass for duplicates; empty needle; >63-byte names match (beyond the bounded parser's truncation); absent extra data → disk_size 0; malformed table errors. |
| 8b | medium | fable | worktree | Crate: stale-flag scrub in the flags walker. Extend `update_copied_flags_for_l1` in `src/crates/snapshot/src/qcow2.rs`: entries currently skipped as unallocated must first be checked for a stale COPIED bit — an entry that is UNALLOCATED (only OFLAG_COPIED set, offset 0) or ZERO_PLAIN (bit 0 set, offset 0) with COPIED set gets the bit cleared and counts as a rewrite, mirroring qemu's `refcount = 0` rule in `qcow2_update_snapshot_refcount` (fact 6). Allocated-entry behaviour unchanged. Read qemu's switch on `qcow2_get_cluster_type` first and mirror the classification exactly. ~8 unit tests: stale COPIED on a zero-plain entry cleared (standard and extended L2, and the extended-L2 subcluster bitmap untouched); stale COPIED on an all-zero-but-COPIED entry cleared; clean unallocated entries untouched (no rewrite counted); existing allocated-entry tests unregressed. Re-run the phase 6/7 harnesses after this change — it alters shared mutating-mode code (the create/delete flag refreshes now also scrub; argue in the back-brief why their byte-identity matrices still pass, or report if they don't: qemu scrubs in every walk, so matching it everywhere should *improve* fidelity). |
| 8c | high | fable | worktree | Guest binary: implement MODE_APPLY in `src/operations/snapshot/src/main.rs` per the Situation section's 11-step algorithm, replacing the last stub. Specifics: gates via the shared `mutating_feature_gates`; find via `find_snapshot_in_table(..., IdThenName)` on the staged raw table; geometry checks per algorithm step 3 (disk_size sentinel handling per open question 5 — verify `qcow2_read_snapshots`' default in the qemu source first); stage both chains with the existing shared helpers (snapshot chain in the SNAP_* regions, active chain in the create-mode regions); build the padded new-L1 working copy (snapshot L1 bytes + zero pad to `hdr.l1_size * 8`) in a scratch region per the production-notes (NEW_TABLE_BUF reuse or a new region — keep the `ALLOC_HEAP_BASE` assert honest); precheck `SwapForApply` before any write; in-mem inc walk → group A (refblocks) → fsync → group B (padded raw snapshot-L1 content at `hdr.l1_table_offset` — stale flags intact) → fsync → in-mem dec walk over the staged old active chain → final-state flag refresh over the padded copy + snapshot L2 set → group C (refblocks, refreshed L1 at `hdr.l1_table_offset` [padded length] AND at `sn.l1_table_offset` [`sn.l1_size * 8` length], dirty snapshot-set L2s; never the freed old-active L2s) → fsync. Result `ERROR_OK`, `snapshots_emitted = 0`, empty `assigned_id`. `make instar` + `make check-binary-sizes`. The risk centre: the two flag-bearing L1 writes in group C (two different lengths at two offsets from one refreshed buffer) and the inc-before-B / dec-after-B in-memory discipline. |
| 8d | medium | sonnet | worktree | Host `-a` dispatch in `src/vmm/src/main.rs`. (i) `run_snapshot_apply(&args, needle, verbose)` delegating to `run_snapshot_mutating_guest` (argument verbatim, dates zero, silent success); route `args.apply` in `run_snapshot`; retire the "arrives in phase 8" message. (ii) Make the not-found and geometry messages mode-aware: thread the mode into `snapshot_error_message` (or split per-mode wrappers at the call sites — pick whichever reads cleaner against the existing structure): delete's `ERROR_NOT_FOUND` keeps the name-only text, apply's says ID-then-name; `ERROR_L1_SIZE_MISMATCH` gets the apply text from the Situation section (qemu truncates, instar refuses, resize-back workaround, quirks pointer). Update the message-table unit tests. |
| 8e | high | fable | worktree | Verification harness: `tools/snapshot-apply-matrix.sh` modelled on the delete matrix (prepare with qemu, `cp`, mutate with each tool, `cmp` + zero-tail check, `file.discard=ignore` on the qemu side), plus apply rows in the refusals harness. Fixtures: the standard six (v3 64 KiB data-bearing, v3 512 B clusters, v2, backing-file, extended-L2, zero-byte disk). Scenarios: **fresh apply** (apply immediately after create — byte-identical; per fact 6 the only expected delta vs the pre-apply image is the snapshot-L1 flag scrub, and instar must match qemu exactly); **diverged apply** (snapshot → divergent writes incl. one beyond the original data range → apply): byte-identical vs qemu AND content-restored — `qemu-img compare` against a reference copy taken at snapshot time returns "Images are identical" (the master plan's test-matrix row); `qemu-img check` clean throughout; **precedence fixture** (`id=1,name="2"` / `id=2,name="x"`): `-a 2` applies ID 2 under both tools — assert by content, and contrast in the same fixture that `-d 2` (phase 7 behaviour) targets the name; **apply by pure ID** (works — unlike delete); **duplicate names** (first name-match when no id matches); **round-trip** (snap → write → apply → write → apply again; check clean and content correct each leg); **apply middle of three snapshots** then delete one and apply again (cross-mode interaction; master plan open question 9's verify-by-test); **post-apply write probe** (after apply every active cluster is shared per the flag invariant — a qemu-io write must COW: snapshot still intact via qemu's own `-a` on a copy, check clean). Refusals: not-found by neither-id-nor-name (image bit-identical), 0-snapshot image, disk-size-mismatch fixture (qemu-img resize the snapshot-bearing image larger, then instar `-a` refuses with the image untouched — and document in the harness comments that qemu would truncate), the standard feature gates, non-qcow2. Re-run ALL prior harnesses (create matrix + refusals, delete matrix + refusals) — the 8a find refactor and 8b walker change touch shared code. Fix what you find in the same commit; record everything in the back-brief. |
| 8f | low | sonnet | worktree | Documentation. (i) `docs/quirks.md`: a single "snapshot argument matching" note presenting the asymmetry — `-d` name-only, `-a` ID-then-name (with the precedence example from fact 2 and the cross-version caveat from phase 7); the disk-size-mismatch refusal (qemu truncates since it allows resize-with-snapshots; instar refuses; resize-back workaround); apply's best-effort crash consistency (write groups, what a crash between B and C leaves, qemu equivalence — per the master plan's apply notes). (ii) `docs/qcow2/qcow2-snapshots.md`: "apply write ordering" subsection (groups A/B/C, the commit point, the both-locations L1 write, the flag invariant) added to the mutator-surface docs; note the walker's stale-flag scrub. (iii) `docs/plans/PLAN-snapshot.md`: phase 8 row → Landed pointing here; correct the `-a` per-mode plan (ID-then-name matching, no L1 grow in v1, pad-smaller path, disk-size refusal vs qemu's truncate, the snapshot-stored-L1 flag write qemu does in its +1 walk); mark open question 9 resolved (round-trip + cross-mode matrix scenarios); update the phase 9 row (all three modes have dispatch; phase 9 = consolidation: shared open path, `-q`/`-U` semantics review, message polish); add the disk-size truncate support and the `qcow2::find_snapshot` disposition to Future work. |
| 8g | low | sonnet | worktree | Full verification + commit. `make instar`, `make test-rust` (crate gains ~18 tests; everything else unregressed), `make check-binary-sizes`, `make lint`, `pre-commit run --all-files`, and the final re-run of all six harnesses (create/delete/apply × matrix/refusals). Single commit for 8a–8f following `~/.claude/CLAUDE.md` conventions (50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Co-Authored-By with model + context window + effort). The message should cover: MODE_APPLY end-to-end with the inc-before/dec-after barrier discipline and the both-locations refreshed-L1 write; the ID-then-name matching discovery and its asymmetry with delete; the disk-size-mismatch refusal where qemu truncates; the stale-flag scrub extension to the shared walker; the find-helper refactor unifying delete; and the full-byte-identity verification including diverged applies (with the cache-discard finding that makes it possible). |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **This phase is dispatched as a single
Fable agent executing steps 8a–8g**, continuing the phase 7
arrangement. The per-step model column records the minimum
tier if a step is ever re-run individually. Worktree isolation
is mandatory (mutating phase). Verify the worktree is based on
the `snapshot` branch head before starting (phases 6 and 7
both began on the wrong base; `git reset --hard snapshot` if
so).

The management session reviews the result against this plan
and the qemu sources — reading the actual files, re-running
the harnesses independently, and performing an independent
byte-level probe. If something contradicts this plan in a way
that changes the design, stop and report rather than
improvising; line-number or naming drift gets corrected and
noted in the back-brief.

### Management session review checklist

- [ ] Read the changed files.
- [ ] No unrelated files modified.
- [ ] The finder is two-full-pass ID-then-name (not per-entry
      or-matching), and delete now uses the same helper in
      NameOnly mode.
- [ ] The in-memory discipline: inc applied before group A,
      dec strictly after group B; the precheck covers both
      directions before any write.
- [ ] Group B writes the *unrefreshed* padded snapshot L1;
      group C writes the refreshed buffer to both offsets
      with the two different lengths.
- [ ] Freed old-active L2s are never written.
- [ ] The walker's stale-flag scrub matches qemu's
      cluster-type classification (read the qemu switch, not
      just the tests).
- [ ] All six harnesses pass; prior-phase harnesses
      unregressed.
- [ ] `make instar`, `make test-rust`,
      `make check-binary-sizes`, `make lint`,
      `pre-commit run --all-files` all clean.
- [ ] Independent probe: diverged apply from byte-identical
      copies under both tools; assert the full-file diff is
      empty (modulo sector tail) and that the only clusters
      differing from the *pre-apply* image are {refblocks,
      active L1, snapshot's stored L1} — the fact 6
      signature.

## Administration and logistics

### Success criteria

Phase 8 is complete when:

* All steps land in one commit on the `snapshot` branch.
* The Mission section's byte-identity and content-restoration
  contracts hold across the step 8e matrix.
* The precedence fixture demonstrates the documented `-a` /
  `-d` asymmetry under both tools.
* Refusals and not-found leave images bit-identical; the
  disk-size-mismatch fixture refuses cleanly.
* Create, delete, and list are proven unregressed (all prior
  harnesses re-run green after the shared-code refactors).
* All builds, tests, sizes, lint, pre-commit clean;
  `snapshot.bin` within the 384 KiB cap.
* Docs and master-plan corrections from step 8f are in.

### Future work created by this phase

- **disk_size-mismatch apply** (qemu truncates; instar v1
  refuses). A follow-up could compose the resize planner with
  apply; only worth it if a user hits the refusal.
- **`qcow2::find_snapshot` disposition** — confirmed wrong
  for both mutating modes' semantics and unused; phase 14
  removes or re-documents it.
- **Cross-version `-a` matching** — older qemu-img versions
  may resolve differently (the delete path changed over
  time); phases 10/11/13 handle the version split alongside
  the delete note from phase 7.

### Bugs fixed during this work

The master plan's `-a` description assumed L1 growth support
and did not know about qemu's disk-size truncate or the
snapshot-stored-L1 flag write — corrected by step 8f. The
flags walker's unallocated/zero-plain skip (phases 5–7) is a
fidelity gap against qemu's scrub rule — fixed by step 8b.
Observed in passing, *not* phase 8 scope: `instar resize` on a
snapshot-bearing qcow2 fails with a confusing
internal-inconsistency error (error 13) where qemu-img
resizes successfully — worth a master-plan future-work note
(step 8f may add it) and an eventual fix in the resize plan
family.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 8f flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan. Include the
`qcow2_read_snapshots` disk-size-default verification (open
question 5) and the rationale for why the 8b scrub change
leaves the phase 6/7 byte-identity matrices passing.
