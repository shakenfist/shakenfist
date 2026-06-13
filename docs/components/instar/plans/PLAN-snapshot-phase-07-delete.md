# PLAN-snapshot phase 07: delete mode (MODE_DELETE)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the phase 6 create
path end-to-end: `src/operations/snapshot/src/main.rs::run_create`,
the staging layout and RMW helpers there, the
`src/crates/snapshot/` crate including `table.rs`, the host
dispatch `run_snapshot_create` in `src/vmm/src/main.rs`, and the
verification harnesses `tools/snapshot-create-matrix.sh` /
`tools/snapshot-create-refusals.sh`), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on qemu behaviour, the authoritative references are
`block/qcow2-snapshot.c::qcow2_snapshot_delete` /
`qcow2_write_snapshots`, `block/snapshot.c::bdrv_snapshot_find`,
and `qemu-img.c` (`SNAPSHOT_DELETE` case) in qemu 10.0.x — fetch
from `https://gitlab.com/qemu-project/qemu/-/raw/v10.0.0/...` if
needed — plus the locally installed `qemu-img` 10.0.8 for
empirical verification.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 7 of
fourteen.

**Model-tier note:** this is the first phase planned after Fable
became available as a sub-agent tier above opus. Per the updated
master-plan model guidance, the high-risk steps here are assigned
to Fable as an experiment; the phase is dispatched as a single
Fable agent (see Agent guidance).

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

Phases 1–6 landed everything through `instar snapshot -c`:
ABI, streaming parser, list mode (guest + host), the mutator
primitives (with phase 6's L2-table refcount fix), the create
planner helpers (`table.rs`), the create guest path with
fsync-barrier write ordering, the minimal mutating-mode host
dispatch pattern, and two reusable verification harnesses.
Phase 7 adds the second mutating mode: `instar snapshot -d
SNAPSHOT` (MODE_DELETE). The genuinely new work is small —
snapshot-table compaction, the decrement-direction refcount
walk, and the *set*-direction COPIED refresh — because phase 6
built the rest.

### Empirically established qemu-img 10.0.8 behaviour

Verified during phase 7 planning against the installed binary
and confirmed against the v10.0.0 sources. Requirements, not
guesses:

1. **Success is silent**, exit 0. Not-found prints
   `qemu-img: Could not delete snapshot 'X': snapshot not
   found` and exits 1 without touching the image.
2. **The argument matches by NAME only, first occurrence in
   table order.** `qemu-img.c`'s `SNAPSHOT_DELETE` resolves the
   argument via `bdrv_snapshot_find`, which does a plain
   `strcmp(sn->name, name)` scan — **ID matching does not
   exist on this path** in modern qemu. Observed: with
   snapshots `id=1,name="2"` and `id=2,name="x"`, `-d 2`
   deletes the one *named* "2"; on an image whose only
   snapshots are `alpha(id 1)` and `gamma(id 3)`, `-d 3` fails
   with "snapshot not found". With duplicate names, the first
   match in table order is deleted. The master plan's "find
   target by id or name" is **wrong for delete** and is
   corrected by this phase. (Older qemu-img releases did try
   ID first via the since-removed
   `bdrv_snapshot_delete_by_id_or_name` — a cross-version
   wrinkle for phases 10/11/13 to handle; 10.0.8 is the
   primary parity target.)
3. **Deleting the last snapshot writes header
   `nb_snapshots = 0, snapshots_offset = 0`** and allocates no
   new table. The file is not truncated (freed clusters
   remain).
4. **Interior deletes compact the table** preserving order
   (delete `beta` from `alpha,beta,gamma` leaves
   `alpha(1),gamma(3)`; IDs are not renumbered).
5. **Delete writes no timestamps.** Given byte-identical
   inputs, instar's and qemu's post-delete images can be
   compared **byte-for-byte** (modulo instar's documented
   sector-granular file-tail quirk) — a stronger assertion
   than create's modulo-date comparisons. The matrix exploits
   this.
6. **qemu's algorithm and ordering**
   (`qcow2_snapshot_delete`): find → `memmove` the in-memory
   list (compact) + `nb_snapshots--` →
   `qcow2_write_snapshots` (allocate + write the new table,
   flush, 12-byte header write at offset 60, flush, free the
   old table's clusters) →
   `qcow2_update_snapshot_refcount(sn.l1_table_offset,
   sn.l1_size, -1)` (decrement every data cluster *and* L2
   table cluster reachable from the deleted snapshot's L1) →
   `qcow2_free_clusters(sn.l1_table_offset, sn.l1_size * 8)`
   (free the snapshot's L1 clusters) →
   `qcow2_update_snapshot_refcount(s->l1_table_offset,
   s->l1_size, 0)` (addend 0 = pure COPIED-flag refresh on
   the **active** chain). Everything after the header write
   is the "we won't recover but just leak clusters" zone —
   qemu's comment, verbatim.

### The delete algorithm (instar's staged adaptation)

The qemu ordering, adapted to instar's stage-mutate-writeback
model with fsync barriers. In-memory mutation order is chosen
so each disk write group sees exactly the staged state it
needs:

1. Feature gates (identical set to create — see phase 6's
   "Feature gates" section — including `refcount_bits == 16`,
   since the compacted table needs fresh clusters; plus the
   compressed-cluster gate applied to **both** the deleted
   snapshot's L2 chain and the active L2 chain).
2. Stream entries via `for_each_snapshot_entry`: find the
   first entry whose **name** equals the argument (fact 2).
   `ERROR_NOT_FOUND` if none, before any write. Capture the
   match's index, `l1_table_offset`, `l1_size`.
   **Do not use `qcow2::find_snapshot`** — its ID-or-name-
   per-entry semantics are wrong for qemu 10 delete (it
   predates these probes; phase 8 re-evaluates it for apply).
3. Stage: raw old table (verbatim), deleted snapshot's L1 +
   its L2 set, active L1 + its L2 set, refcount table +
   refblocks (same contiguity gate and bounds as create).
   Shared L2 clusters appear in both staged sets; the
   decrement walk only *reads* the snapshot-side copies and
   the flag refresh only *mutates* the active-side copies, so
   the duplication is safe.
4. Build the compacted table:
   `build_snapshot_table_without(old, old_len, remove_idx)`
   — verbatim per-entry copy, 8-aligned entry starts, the
   removed entry skipped. Skipped entirely when the remaining
   count is 0 (fact 3).
5. **Pre-validation walk (read-only, before any disk
   write):** `precheck_snapshot_refcount(DecrementForDelete)`
   over the snapshot's chain, plus refcount ≥ 1 checks on the
   snapshot's L1 clusters and the old table's clusters. A
   corrupt image fails here with the file untouched. (qemu
   has no such check; its equivalent failure happens after
   the commit point. Ours failing earlier is strictly
   better and structurally invisible.)
6. Allocate contiguous clusters for the compacted table in
   the staged refblocks (skip when remaining == 0).
7. **Write group A** (skip when remaining == 0): compacted
   table bytes + all staged refblocks (which at this moment
   carry only the allocation bumps). `fsync_input(0)`.
8. **Write group B (commit point):** 12 bytes at header
   offset 60 — `nb_snapshots - 1` (u32 BE) + the new table
   offset, or `0` when the table is now empty (u64 BE).
   `fsync_input(0)`.
9. In-memory decrements (the post-commit "leak zone", matching
   qemu): `update_snapshot_refcount(DecrementForDelete)` over
   the snapshot's L1 (data clusters + L2 table clusters, per
   the phase 6b fix); decrement the snapshot's L1 clusters to
   free them; decrement the old table's clusters. Then the
   COPIED refresh: `update_copied_flags_for_l1` over the
   **active** L1/L2 with refcounts read from the
   post-decrement staged refblocks — shared data clusters
   that dropped 2→1 get COPIED **set** (the reverse direction
   from create), clusters still shared with other snapshots
   stay cleared.
10. **Write group C:** all staged refblocks (now carrying the
    decrements) + the active L1 + the active L2 set.
    `fsync_input(0)`.

Crash-safety contract (same shape as create's): before group B
the old table is authoritative and the only damage is an
orphaned compacted table (leak); after group B the snapshot is
gone and any crash before group C completes leaves refcounts
too *high* and/or COPIED flags stale — leaks and repairable
flag warnings, never a dangling reference. A dry-run failure
in step 9 (impossible in practice given step 5) leaves a
consistent-but-leaky image and a non-zero exit, the same
failure mode qemu has.

One deliberate divergence from qemu's byte-level behaviour:
qemu's `-1` walk rewrites COPIED flags inside the deleted
snapshot's (about-to-be-freed) L1/L2 clusters as a side
effect; instar never writes to freed clusters. Live metadata
ends up identical; only garbage bytes in *freed* clusters
differ. Noted for phase 13's image-comparison design (its
"stripped-metadata" comparison must not read freed clusters;
`qemu-img check` + structural comparison are unaffected —
and the matrix's byte-identity assertions on freshly-staged
fixtures are unaffected because qemu's flag writes into
shared-and-still-live L2s produce the same bytes our active-
chain refresh produces).

### What phase 7 produces

1. **Snapshot crate additions** (`src/crates/snapshot/`):
   - `table.rs`: `snapshot_table_entry_bounds(table, nb,
     index) -> Result<(usize, usize), SnapshotError>` (start
     offset + unpadded length of one entry, walking raw
     headers) and `build_snapshot_table_without(old_table,
     old_len, nb_snapshots, remove_index, out) ->
     Result<usize, SnapshotError>` (verbatim per-entry copy,
     8-aligned starts, removed entry skipped, unpadded tail).
   - `qcow2.rs`: `precheck_snapshot_refcount(...)` — a public
     read-only wrapper over the existing private dry-run
     pass, so callers can validate a decrement (or increment)
     against staged refblocks **without** the paired apply.
     Same signature shape as `update_snapshot_refcount` minus
     the `&mut`.
   - ~20 new unit tests.
2. **Guest binary**: MODE_DELETE in
   `src/operations/snapshot/src/main.rs` replacing the phase 3
   stub, per the algorithm above. Reuses the create path's
   scratch regions where lifetimes don't overlap and adds the
   second L1/L2 staging set (delete needs both chains; create
   needed one). Budget check: 4 × 64 KiB (old table, active
   L1, snapshot L1, new table) + 2 × 2 MiB (two L2 sets) +
   2 MiB (refblocks) + 64 KiB (RT) + bounce ≈ 6.6 MiB, well
   inside scratch (~12.4 MiB usable below the alloc heap).
3. **Host CLI**: `-d` dispatch. Factor the guest-launch body
   shared by create and delete into a
   `run_snapshot_mutating_guest(...)` helper (mode, arg
   bytes, date fields) so phase 8 doesn't create a third
   copy; `run_snapshot_create` keeps its name-validation
   front half, `run_snapshot_delete` passes the argument
   through **verbatim** (no emptiness check — qemu matches an
   empty name if a qemu-created image has one; instar refuses
   *creating* empty names but must still *delete* them for
   parity). Update `snapshot_error_message`'s
   `ERROR_NOT_FOUND` text: it currently says "matches neither
   a snapshot ID nor a name", which fact 2 makes wrong —
   delete matches names only.
4. **Verification harness**: `tools/snapshot-delete-matrix.sh`
   modelled on the create matrix, plus delete rows in the
   refusals harness (see step 7e for the matrix contents —
   the byte-identity assertion from fact 5 is the
   centrepiece).
5. **Docs**: quirks (name-only matching on modern qemu-img,
   with the older-version ID-matching note; freed-cluster
   byte divergence), `docs/qcow2/qcow2-snapshots.md` delete
   ordering subsection, master-plan corrections (the `-d`
   per-mode plan's "by id or name" claim and its in-place
   compaction step 5, which fact 3/6 supersede — qemu
   rewrites the table, same as create).

### What phase 7 does not change

- The wire ABI: `SnapshotConfig` already carries everything
  delete needs (`mode`, `arg`; the date fields stay zero).
  No shared-crate changes at all this phase.
- Create mode, list mode (guest and host) — byte-identical
  behaviour, re-verified by re-running the phase 6 harnesses.
- MODE_APPLY stub (phase 8).
- The qcow2 crate (`find_snapshot` stays as-is; phase 8
  decides its fate when apply's matching semantics get the
  same empirical treatment).

## Mission and problem statement

After phase 7 lands, on any supported qcow2 image:

```
instar snapshot -d snap1 image.qcow2   # silent, exit 0
qemu-img check image.qcow2             # clean
```

and — because delete writes no timestamps — given two
byte-identical input images, `instar snapshot -d X` and
`qemu-img snapshot -d X` produce **byte-identical** results
(modulo the documented sector-granular file tail), across
first/middle/last/sole-snapshot deletes, duplicate-name
tables, and the name-vs-ID precedence fixtures. Not-found and
feature-gate refusals leave the image bit-for-bit untouched.

## Open questions

### 1. Match semantics: name-only, or instar-extended ID fallback?

Resolved empirically (fact 2): **name only, first match in
table order**, mirroring `bdrv_snapshot_find` in qemu 10. An
ID fallback would be friendlier (and older qemu-img had one)
but would diverge from the primary parity target and poison
the phase 13 differential fuzzer (instar succeeding where
qemu-img errors). Documented in quirks with the
cross-version note. `ERROR_NOT_FOUND`'s host message is
reworded accordingly.

### 2. Where does the dry-run live, given the commit-point ordering?

`update_snapshot_refcount`'s built-in dry-run only helps if
it runs before any disk write, but its apply pass must run
*after* the group A/B writes (the staged refblocks must hold
allocations-only when group A is written). **Working answer:
expose the existing private dry-run as a public read-only
`precheck_snapshot_refcount` and call it in step 5, before
any write.** The later full call's internal dry-run becomes a
redundant-but-free second check. The alternative — snapshotting
the 2 MiB refblock buffer to defer the apply — buys nothing
and costs scratch.

### 3. Free via decrement or set-to-zero?

Phase 6's group D set old-table refcounts to 0. Delete frees
three kinds of clusters (old table, snapshot L1, and
chain-reachable clusters dropping to 0). **Working answer:
decrement everywhere** (`check_refcount_after_addend` with
addend −1), which is qemu's semantics and lets the underflow
check catch double-free bookkeeping bugs; a set-to-0 would
mask them. Step 7c may also align create's group D to
decrement for consistency (one-line change, behaviour
identical on well-formed images) — do it, with a sentence in
the commit message.

### 4. Does delete need the `refcount_bits == 16` gate?

**Working answer: yes, keep the uniform gate.** Deleting one
of several snapshots allocates the compacted table, so the
16-bit-only allocator is on the path. The sole-snapshot
delete technically allocates nothing, but gating uniformly
(all mutating modes, one documented rule) beats a
mode-specific carve-out nobody asked for.

### 5. Compacted-table build: re-serialise or verbatim per-entry copy?

qemu re-serialises every entry from parsed state.
**Working answer: verbatim per-entry copy** (consistent with
phase 6's `build_snapshot_table`): walk raw entry bounds,
copy each surviving entry's bytes unchanged to the next
8-aligned output offset. Preserves unknown extra data
byte-for-byte and is exactly what fact 5's byte-identity
matrix requires for qemu-img-10-written fixtures. The same
phase 6 caveat applies for exotic inputs (a pre-icount
`extra_data_size < 24` entry would be preserved, where qemu
would renormalise it to 24) — no such fixture exists in the
matrix; already documented as a v1 simplification.

### 6. Should the active chain's staging dedupe against the snapshot chain's?

Shared L2 clusters get staged twice (once per chain).
**Working answer: no dedupe.** The budget fits comfortably,
the two walks want different mutability (read-only decrement
vs flag rewrite), and dedupe bookkeeping is exactly the kind
of cleverness that breeds aliasing bugs in a phase about
refcount integrity. Revisit only if a future phase hits the
staging bound.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | fable | worktree | Crate: table helpers. In `src/crates/snapshot/src/table.rs` add (i) `pub fn snapshot_table_entry_bounds(table: &[u8], nb_snapshots: u32, index: u32) -> Result<(usize, usize), SnapshotError>` — walk raw entries exactly like `snapshot_table_byte_len` (8-aligned starts, `40 + extra + id + name` advance) and return the target entry's (start, unpadded length); `ParseFailed` on escape, `InvalidConfig` if `index >= nb_snapshots`. Refactor the shared walk into a private iterator-style helper used by both functions rather than duplicating the loop. (ii) `pub fn build_snapshot_table_without(old_table: &[u8], old_len: usize, nb_snapshots: u32, remove_index: u32, out: &mut [u8]) -> Result<usize, SnapshotError>` — copy every surviving entry verbatim to the next 8-aligned output offset, zeroing alignment gaps, unpadded tail (open question 5); removing the last remaining entry yields length 0. (iii) ~12 unit tests: bounds of first/middle/last entries on a hand-built 3-entry table with mixed id/name lengths; bounds index-out-of-range; build-without first/middle/last preserves survivors byte-identically (including an entry with unknown trailing extra data) and re-aligns correctly when the removed entry's length ≠ a multiple of 8; remove sole entry → 0; malformed table errors. |
| 7b | medium | fable | worktree | Crate: public read-only precheck. In `src/crates/snapshot/src/qcow2.rs` add `pub fn precheck_snapshot_refcount<'l2, L2F, RBF>(op: SnapshotRefcountOp<'_>, refblocks: &[u8], cluster_bits: u32, refcount_bits: u32, extended_l2: bool, l2_for_index: L2F, refblock_byte_offset_for_cluster: RBF) -> Result<(), SnapshotError>` delegating to the existing private `dry_run_refcount_pass` per op variant (the same dispatch `update_snapshot_refcount` does for its pass 1, including L2-table-cluster coverage). Update `update_snapshot_refcount`'s doc comment to mention the standalone precheck. ~8 unit tests: precheck detects data-cluster underflow (refcount 0, addend −1 → `ParseFailed`) and L2-table-cluster underflow without mutating `refblocks` (byte-identity assertion, reusing the existing snapshot-compare pattern); precheck passes on a healthy decrement; precheck of `SwapForApply` checks both sides; `refblocks` is provably untouched on success too. |
| 7c | high | fable | worktree | Guest binary: implement MODE_DELETE in `src/operations/snapshot/src/main.rs`, replacing the stub, per the Situation section's 10-step algorithm. Read `run_create` first and mirror its structure; factor shared staging helpers (header gates, RT/refblock staging + `rb_offsets` + the two closures, L2-set staging with the compressed gate) out of `run_create` into functions both modes call, rather than copy-pasting — the refactor must leave create's behaviour byte-identical (re-run `tools/snapshot-create-matrix.sh` to prove it). Key specifics: find-by-name-only over `for_each_snapshot_entry` (first match; compare `entry.name[..name_len]` against `config.arg[..arg_len]`, empty arg allowed), `ERROR_NOT_FOUND` otherwise; stage BOTH chains (new scratch region for the second L1 + L2 set — extend the layout consts and the compile-time `ALLOC_HEAP_BASE` assert); `snapshot_table_entry_bounds` + `build_snapshot_table_without` for the compacted table; `precheck_snapshot_refcount(DecrementForDelete)` plus ≥1 checks on the snapshot-L1 and old-table clusters BEFORE any write; group A (compacted table + refblocks, skipped when remaining == 0) → fsync → group B (12 bytes at offset 60: `nb_snapshots − 1`, new offset or 0/0 per fact 3) → fsync → in-memory decrements (chain via `update_snapshot_refcount`, then snapshot-L1 clusters, then old-table clusters — decrement, not set-to-0, per open question 3; also switch create's group D to decrement in the same commit) → COPIED refresh on the active chain → group C (refblocks + active L1 + active L2 set) → fsync. Result: `ERROR_OK`, `snapshots_emitted = 0`, empty `assigned_id`. `make instar` + `make check-binary-sizes` (snapshot.bin within 384 KiB). This step is the phase's risk centre: the group A/B/C ordering interacting with the in-memory mutation order, and the both-chains staging, are where corruption bugs would hide. |
| 7d | medium | sonnet | worktree | Host `-d` dispatch in `src/vmm/src/main.rs`. (i) Factor the KVM-launch + message-pump + result-handling body shared by `run_snapshot_create` into `run_snapshot_mutating_guest(filename, mode, arg_bytes, date_sec, date_nsec, sector_size, verbose)`; `run_snapshot_create` keeps its name-validation and date-computation front half and delegates; behaviour byte-identical (re-run the create harnesses). (ii) `run_snapshot_delete(&args, needle, verbose)`: pass the argument through verbatim (no emptiness/length validation — parity per the Situation section; arg over 255 bytes simply won't match anything, like qemu), dates zero, silent success, errors via `snapshot_error_message`. (iii) Route `args.delete` in `run_snapshot`; `-a` keeps its "arrives in phase 8/9" message (update the text). (iv) Reword `snapshot_error_message`'s `ERROR_NOT_FOUND` arm to name-only semantics, e.g. "snapshot: no snapshot with that name (qemu-img 10 matches -d arguments by name only, not ID; see docs/quirks.md)" — and update the message-table unit test. |
| 7e | high | fable | worktree | Verification matrix: `tools/snapshot-delete-matrix.sh` (modelled on the create matrix; shellcheck-clean; reusable). Fixtures: v3 64 KiB with real data, v3 512 B clusters, v2, backing-file, extended-L2, zero-byte disk. Core scheme per fixture — **prepare once with qemu, copy, mutate with each tool, compare bytes** (fact 5): build the image, add snapshots with qemu-img, `cp` to A and B, `instar snapshot -d X` on A vs `qemu-img snapshot -d X` on B, then assert `cmp` equality over the common prefix and that A's tail beyond B's length is all zeroes (the documented sector-tail quirk), plus `qemu-img check` clean on A and `instar snapshot -l` ≡ `qemu-img snapshot -l` on A. Scenarios: delete first / middle / last of three; delete the sole snapshot (assert header bytes 60..72 are exactly nb=0, offset=0 per fact 3); duplicate names (delete removes the FIRST, the id-2 twin survives); the precedence fixture (snapshots named "2" and "x": `-d 2` removes the name-match); `-d` by pure ID fails with exit 1 and a byte-identical image (parity with fact 2); delete-then-create reuses the freed clusters (run the same delete-then-create sequence under both tools and compare structurally — dates differ so byte-identity doesn't hold here, use the create matrix's modulo-date comparisons); post-delete COPIED probe — image with TWO snapshots sharing written data, delete one, assert shared clusters dropped to refcount 1 with COPIED set (decode bytes like the create matrix's probe), then write through the active layer with qemu-io, `qemu-img check` clean, and the SURVIVING snapshot's content still correct (verify via `qemu-img snapshot -a` on a copy + read, i.e. qemu's own apply as the oracle). Refusals (extend `tools/snapshot-create-refusals.sh` or a sibling): not-found by name and by ID (image byte-identical after), delete on a 0-snapshot image, the standard feature gates (zstd, LUKS, external data, dirty), non-qcow2. Also re-run BOTH phase 6 harnesses to prove create/list are unregressed by the 7c/7d refactors. Fix what you find in the same commit; record the full matrix results in the back-brief. |
| 7f | low | sonnet | worktree | Documentation. (i) `docs/quirks.md`: `-d` matches by name only on the modern qemu-img this tracks (older qemu-img tried ID first; instar follows 10.x — cross-version note for the baseline phases); deleting never truncates the file; freed-cluster bytes may differ from qemu's (the flag-rewrite-into-freed-clusters divergence); empty `-d` argument is passed through (can match qemu-created empty-named snapshots). (ii) `docs/qcow2/qcow2-snapshots.md`: "delete write ordering" subsection (groups A/B/C, the commit point, the leak-zone contract) and the new table/precheck helpers in the mutator-surface list. (iii) `docs/plans/PLAN-snapshot.md`: phase 7 row → Landed pointing here; correct the `-d` per-mode plan (name-only matching; full-table rewrite, not in-place compaction — facts 2/3/6); note in the phase 9 row that delete dispatch landed here and the shared `run_snapshot_mutating_guest` helper exists. (iv) `docs/usage.md` `-d` if the snapshot subcommand is documented there. |
| 7g | low | sonnet | worktree | Full verification + commit. `make instar`, `make test-rust` (crate gains ~20 tests; everything else unregressed), `make check-binary-sizes`, `make lint`, `pre-commit run --all-files`, plus a final re-run of all four harnesses (create matrix, delete matrix, refusals, and an `instar snapshot -l` vs `qemu-img snapshot -l` spot check). Single commit for 7a–7f following `~/.claude/CLAUDE.md` conventions (50-char first line ending in `.`, 75-char wrap, Prompt paragraph, Signed-off-by, Co-Authored-By with model + context window + effort). The message should cover: MODE_DELETE end-to-end with the qemu-faithful commit-point ordering, the name-only matching discovery (and the master-plan correction), the byte-identity verification scheme delete makes possible, the read-only precheck addition, the create-path refactors (shared staging helpers, shared host launch helper, group-D decrement alignment) and that they're proven unregressed by harness re-runs. |

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. **This phase is dispatched as a single
Fable agent executing steps 7a–7g** — the first Fable-run
phase, deliberately comparable in shape to the opus-run
phase 6 (crate helpers + guest mode + host dispatch +
verification matrix) so the tiers can be compared on like
work. The per-step model column above records the minimum
tier if a step is ever re-run individually. Worktree
isolation is mandatory (mutating phase).

The management session reviews the result against this plan
and the qemu sources — reading the actual files, re-running
the harnesses independently, and doing at least one
independent byte-level probe (the review checklist below).
If something contradicts this plan in a way that changes the
design, stop and report rather than improvising; mere
line-number or naming drift gets corrected and noted in the
back-brief.

### Management session review checklist

- [ ] Read the changed files.
- [ ] No unrelated files modified.
- [ ] Find semantics are name-only first-match (no ID path,
      no `qcow2::find_snapshot` usage).
- [ ] The write order is A → fsync → B → fsync → C, the
      in-memory decrements happen strictly between B and C,
      and the precheck runs before any write.
- [ ] Group A refblocks carry allocations only; group C
      refblocks carry the decrements (verify by reading the
      code path, not just the harness results).
- [ ] The sole-snapshot delete writes header (0, 0) and skips
      group A.
- [ ] Create's harnesses still pass after the 7c/7d refactors.
- [ ] `make instar`, `make test-rust`,
      `make check-binary-sizes`, `make lint`,
      `pre-commit run --all-files` all clean.
- [ ] Independent probe: stage a 3-snapshot data-bearing
      image, delete the middle one with both tools from
      byte-identical copies, and diff the refcount array,
      header bytes 60..72, table bytes, and active L1/L2
      entries — byte-identical (modulo file tail).

## Administration and logistics

### Success criteria

Phase 7 is complete when:

* All steps land in one commit on the `snapshot` branch.
* The byte-identity contract from the Mission section holds
  across the step 7e matrix.
* The post-delete COPIED probe (shared clusters 2→1 with
  COPIED set; surviving snapshot intact under qemu's own
  apply) passes.
* Not-found and refusal paths leave images bit-identical.
* Create and list modes are proven unregressed.
* All builds, tests, sizes, lint, pre-commit clean;
  `snapshot.bin` within the 384 KiB cap.
* Docs and master-plan corrections from step 7f are in.

### Future work created by this phase

- **Cross-version `-d` matching.** Older qemu-img binaries in
  the test matrix resolve IDs too; phases 10/11/13 must
  either pin delete baselines to name-only versions or
  encode the version split. Flagged in quirks now.
- **`vm_state`-bearing snapshot deletion** is handled
  generically by the chain walk (vm-state clusters are
  ordinary allocated clusters under the snapshot's L1) but
  no fixture exercises it — qemu-img cannot create one;
  a savevm-produced fixture is a phase 10/11 candidate.
- **Freed-cluster byte divergence** noted for phase 13's
  comparison design.

### Bugs fixed during this work

The master plan's `-d` description carried two stale claims
(ID-or-name matching; in-place table compaction) — corrected
by step 7f. Any further gaps surfaced by the byte-identity
matrix are fixed in the same commit and listed here.

**Gap surfaced during execution: qemu discards freed clusters.**
Fact 5's plain-`cmp` byte-identity failed on first contact
because `qemu-img` passes a protocol-level *discard* down to the
file for every cluster a delete frees
(`QCOW2_DISCARD_SNAPSHOT` / `QCOW2_DISCARD_ALWAYS` default on in
`qcow2.c`), punching holes so freed clusters read back as zeros —
a second freed-cluster divergence this plan's "flag-rewrite into
freed clusters" note did not anticipate. instar's design is
unchanged (it never writes to freed clusters, exactly as
specified); the matrix runs the qemu side with `--image-opts
driver=qcow2,file.filename=…,file.discard=ignore`, which disables
only the hole punching, and the byte-identity assertion then
holds bit-for-bit across every fixture. Documented in
docs/quirks.md and flagged for phase 13's comparison design
alongside the existing freed-cluster note.

### Documentation index maintenance

This is a phase plan, not a master plan: **not** added to
`docs/plans/order.yml`. The master plan's Execution table
already links this file; step 7f flips the row to Landed.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan. Include the harness
re-run results proving the create-path refactors are
regression-free.
