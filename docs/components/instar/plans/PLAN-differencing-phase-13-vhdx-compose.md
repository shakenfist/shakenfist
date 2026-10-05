# Differencing phase 13: guest VHDX sector-bitmap read path

## Prompt

Plan phase 13 of `PLAN-differencing.md`, the second of the two guest
read-path phases: teach the guest to compose a differencing VHDX child
against its parent at logical-sector granularity, so that a
`PAYLOAD_BLOCK_PARTIALLY_PRESENT` block reads each sector from
whichever file owns it rather than failing the read.

Phase 12 landed the VHD half (`b4ae7fca`, #615). The two formats are
separate phases because they are not the same problem: VHD carries a
512-byte bitmap in front of every block, while VHDX keeps bitmaps in
their own 1 MiB blocks reached through interleaved BAT entries, counts
sectors in logical-sector units that may be 4096 bytes, and orders its
bitmap bits the other way round. Nothing on the host builds a composing
VHDX chain yet -- phase 14 is what changes that -- so this is a
crate-level change verified by crate-level tests. The phase plan is the
deliverable; implementation is a separate ask.

## Planning effort

**High.** Same reasoning as phase 12: a guest read path in `no_std`
code where a wrong answer is silently wrong data rather than a crash.
Two things make it no easier than phase 12 despite the precedent. The
bitmap is reached through a different structure, so the "find the
bitmap" half is new work rather than a port. And the three differences
from VHD -- bit order, sector unit, and a bitmap block that may be
legitimately absent -- are each individually invisible to a test that
happens to use the symmetric case, which is exactly the class of
mistake phase 12 spent two review rounds closing.

Review effort: **high**, for the same reason. The master plan does not
specify one for this phase.

## Scope

**In scope.**

* A sector-bitmap reader in `src/crates/vhdx/src/lib.rs`: resolve a
  payload block's interleaved sector-bitmap BAT entry, read bitmap
  bytes through the state's existing data cache, and coalesce runs of
  same-owner logical sectors.
* The `PAYLOAD_BLOCK_PARTIALLY_PRESENT` case of the VHDX arm of
  `read_chain_virtual_cluster` (`src/crates/qcow2/src/lib.rs:10269`):
  classify a chunk, serve a wholly-owned chunk with one read, and
  compose a mixed one.
* Separating `NotPresent` from `Zero` in that arm. They are currently
  one match arm, which is wrong for a differencing child -- see F3.
* Failing closed, on every path that could otherwise serve a
  parent-owned sector with no parent behind the child, exactly as the
  VHD arm does.
* Adding `vhdx-input` to the lint and unit-test feature matrix
  (issue #616), without which none of the above is compiled by either
  target.
* Crate-level tests, including a VHDX mock-chain harness.

**Out of scope.**

* Lifting `init_chain_states`' refusal of a differencing VHDX
  (`src/crates/qcow2/src/lib.rs:11298`). It stays unconditional, for
  the reason phase 12 established and issue #614 records: `device_count`
  bounds a flat array that may hold more than one chain, so "a device
  follows this one" does not mean "this child has a parent". Phase 14
  owns that, and needs #614 settled first.
* Any operation reaching the new path. No host change at all.
* `map`'s VHDX partial-present walk (`docs/map.md:238`). That is a
  different consumer of the same structure and a separate change; this
  phase must not silently make that limitation note false, and the
  definition of done says so.
* Integration tests and fuzzing (phases 15), documentation (phase 16).
* `PAYLOAD_BLOCK_UNDEFINED` / `UNMAPPED` semantics beyond what the
  current code already does. They are not differencing-specific.
* `vhdx::calculate_bat_layout`'s writer-side BAT entry count
  (`src/crates/vhdx/src/lib.rs:3301`), used by `create`, `convert`,
  `measure` and `resize`. It sizes a differencing image's BAT by the
  same shorter rule 13f's reader-side fix works around rather than
  widens (see F10/F11 below), so every differencing VHDX instar
  writes still has unreachable sector-bitmap entries past the first
  partial chunk group. Fixing it changes the on-disk layout of every
  such image and needs each of its four callers checked on its own.
  Tracked as issue #623, deliberately not fixed here.
* Refusing a block offset that overlaps the BAT or metadata region.
  Review round 3 raised it as a `consider`, and it is the same
  "invent an answer for a malformed image" shape as the offset-zero
  case F15 closed -- but that one is a single comparison against a
  constant, where this needs the declared region bounds retained on
  `VhdxState` and applied at three sites, and raises the question of
  whether `scan_allocation` and `map_extents` should do the same.
  Tracked as issue #625.
* A compose test at 4096-byte logical sectors beyond the first chunk
  group. Also review round 3, also a `consider`. At that geometry
  `chunk_ratio` is 32768 rather than 4096, so the padded bound and
  the group stride are arithmetic the current tests only drive at the
  512 geometry; the fixture BAT region is large enough that the test
  would be cheap. It is coverage of an existing property rather than
  a defect, and phase 15 is the tests-and-fuzz phase, so it belongs
  there -- recorded here so phase 15 does not have to rediscover it.

## What the survey found

Surveyed on 2026-10-04 against `develop` at `758ffba8`, with phase 12
merged. Verified by running commands rather than by reading, where a
command existed.

**F1. The VHDX arm is compiled by neither `make lint` nor
`make test-rust` -- measured, not inferred.** Issue #616 says so;
this is the proof. With a deliberate type error injected into the VHDX
arm, `make lint` exits 0. Adding `vhdx-input` to the two feature lists
(`Makefile:536`, `scripts/check-rust.sh:134,140`) and re-running gives
`error[E0308]` naming the injected line. This is the same gap phase 12
found for VHD (its F12) and deliberately did not close for VHDX,
because this phase was going to rewrite that code.

**F2. Unlike the VHD arm, the VHDX arm already compiles clean.** With
`vhdx-input` added to both lists and no probe, `make lint` exits 0 with
no clippy findings, and `make test-rust` reports 2383 passed / 0 failed
-- identical to the baseline on `develop`. Phase 12's equivalent step
surfaced real clippy findings on first compile; this one will not. The
feature addition also buys **zero** new tests, because there is no
VHDX test in the qcow2 crate at all: `vhdx` appears ten times in that
11,000-line file and every occurrence is production code. So step 13a
is a one-line matrix change that makes later steps' code visible, not a
cleanup job, and it provides no coverage by itself.

**F3. The VHDX arm conflates "not present" with "explicitly zero", and
that is wrong for a differencing child.** At
`src/crates/qcow2/src/lib.rs:10275` the arm reads:

```rust
Some(VhdxBlockLookup::NotPresent) | Some(VhdxBlockLookup::Zero) => {
    continue;
}
```

and `VhdxBlockLookup::NotPresent`'s own doc comment says "(reads as
zero)". For a file with no parent the two are indeed the same answer,
which is why this has never been wrong. For a differencing child they
are opposites: `PAYLOAD_BLOCK_NOT_PRESENT` means the data lives in the
parent, and `PAYLOAD_BLOCK_ZERO` means this block is zeros and the
parent must *not* be consulted. Composing without separating them would
read the parent's data where the child says zero. The master plan does
not mention this; it is this survey's main find, and it is a
correctness item rather than a structural one.

**F4. `PARTIALLY_PRESENT` is already a deliberate, documented refusal,
and it is the phase's subject.** `block_lookup`
(`src/crates/vhdx/src/lib.rs:1984`) returns `None` for state 7, with a
comment saying it is a backstop for a caller that skipped the
`has_parent` check. The arm turns `None` into `return false`. So the
starting position is fail-closed rather than silently-wrong, which is a
better starting point than the VHD arm had.

**F5. `VhdxState` already carries everything the reader needs except
the bitmap itself.** `logical_sector_size` (512 or 4096, validated at
`src/crates/vhdx/src/lib.rs:1818`), `block_size`, `chunk_ratio`
(computed at `:1823` as `(2^23 * logical_sector_size) / block_size`),
`total_bat_entries`, `bat_offset`, and `has_parent` are all fields of
the struct at `:1641`. `block_lookup` already computes the interleave
correction, `sb_entries_before = block_index / chunk_ratio`, at
`:2001`. What is missing is reading the SB entry rather than skipping
past it.

**F6. The vhdx crate has an allocated, never-used data cache -- the
same free resource phase 12 found for VHD.** `data_cached_sector` and
`data_cache_buf` are fields of `VhdxState` (`:1669-1670`), are assigned
at init (`:1864-1865`), and are read nowhere: `grep -c data_cache_buf
src/crates/vhdx/src/lib.rs` returns 4, all declaration or assignment.
The VHD crate's count went from 8 (all unused) to 11 (used) over phase
12. Bitmap reads can use this cache, adding no guest memory, exactly as
phase 12's did.

**F7. The format differences from VHD are already pinned against real
oracles, and so is a fixture generator.** Phase 1 measured them
(`docs/plans/PLAN-differencing-phase-01-pin.md`):

* The VHDX sector bitmap is **least significant bit first**, "the
  opposite of VHD" (`:1463`). VHD is `bit (7 - i % 8)`; VHDX is
  `bit (i % 8)`.
* A bit counts one **logical sector**, which may be 4096 bytes, not a
  fixed 512 as in VHD.
* A sector-bitmap block is 1 MiB, hence `chunk_ratio` payload blocks
  per bitmap block.
* SB BAT entry states are `SB_BLOCK_NOT_PRESENT` (0) and
  `SB_BLOCK_PRESENT` (6) (`:430-431`).
* An SB entry "may only be `SB_BLOCK_NOT_PRESENT` if no associated
  payload block is `PAYLOAD_BLOCK_PARTIALLY_PRESENT`" (`:878-885`), so
  a `PARTIALLY_PRESENT` block whose bitmap block is absent is a
  malformed image.
* `vhdx_sector_bitmap_block()` and `patch_vhdx_child()` at `:1810` and
  `:1819` build such an image in Python; `instar-testdata` carries the
  resulting `vhdx-diff-child.vhdx` / `vhdx-diff-parent.vhdx` pair.

Set bit means the sector lives in this file, same polarity as VHD.

**F8. Neither `SB_BLOCK_NOT_PRESENT` nor `SB_BLOCK_PRESENT` exists as a
constant in the vhdx crate.** `grep -n 'SB_BLOCK' src/crates/vhdx/src/lib.rs`
returns nothing, while the six `PAYLOAD_BLOCK_*` constants are defined
at `:197-207`. The crate's BAT walkers skip SB entries by position
without ever looking at their state.

**F9. Phase 12's final shape differs from its mid-phase shape, and
phase 13 should copy the final one.** Review round 3 (`4a7a143f`)
removed the `read_cluster_sectors` / `read_offset_sectors` branch from
`read_vhd_child_runs`: every child run now goes through
`read_offset_sectors` with the caller's scratch, because the aligned
branch still took the 64 KiB-stack path whenever a run was not a whole
number of device sectors. A VHDX reader written by copying the
mid-phase VHD code would reintroduce that. The same round renamed the
regression guard to `vhd_arm_dynamic_reads_as_a_plain_dynamic_vhd`.

**Corrections made at source.** The master plan's phases 12-and-13
bullet cites pre-phase-12 line numbers throughout
(`read_chain_virtual_cluster` at `:7930`, the format dispatch at
`:8486`, `ChainStates` at `:9385`, the subcluster path at
`:8106-8130`, the refusals at `:9528` and `:9556`). Phase 12 added
about 2,800 lines to that file and every one of them is now wrong. The
planning commit corrects the two a phase 13 reader will follow -- the
VHDX refusal, now `:11298`, and the VHDX arm, now `:10269` -- and
marks the rest as pre-phase-12. No later step needs to redo this.

Nothing else the master plan says about phase 13 was found to be false.

The findings below were made executing the plan above, not planning
it, and are added here rather than left to be inferred from commit
messages.

**F10. The risk list's claim that the `NotPresent`/`Zero` split
"should not" change non-differencing reads (below, "Risks and
mitigations") is wrong, in exactly one case that is unreachable
rather than harmful.** 13c makes `Zero` zero-fill and return
immediately, where before the split it fell through to `continue`
with `NotPresent` and reached the walk's zero-fill tail by the same
route. `cc0989a0`'s regression guard compares five reads of a
parentless image against `758ffba8` and finds four identical, with
the third -- an explicitly zeroed block *with a device behind it* --
changed: it used to descend into that device and now reads as
zeros directly. No chain a host builds can produce this shape, since
a parentless image has no backing file to put behind it, so the
difference cannot reach a real read today. The guard drives the case
anyway, to pin the new answer rather than leave it untested, and that
is what shows the risk list's claim to be false rather than merely
unproven.

**F11. A differencing VHDX's sector-bitmap BAT entries are
unreachable whenever its block count is not a whole number of chunk
groups, and the master plan does not anticipate this at all.**
`VhdxState::init` sized `total_bat_entries` by the rule for an image
with no parent -- one entry per payload block plus one per group --
and `sector_bitmap_lookup` bounded itself by that same count. A
differencing image's BAT is actually padded to whole groups of
`chunk_ratio + 1` entries, because a group's sector-bitmap entry sits
at the end of the group whether or not every payload entry ahead of
it is backed by virtual disk. Any differencing image whose block
count is not a whole number of groups therefore had at least one
unreachable bitmap entry, and one smaller than a single group -- 4
GiB at the common 1 MiB block / 512-byte sector geometry -- had none
reachable at all. The project's own 16 MiB `vhdx-diff-child.vhdx`
fixture is the second case, so this blocked the phase's own
deliverable: the composing reader built in 13c could not have read
instar's real fixture. Found by 13d's implementer noticing that
every fixture it had managed to get working used a whole number of
chunk groups, not by anything in this plan. Fixed in `ec005d57` by
adding a separate bound, `sb_bat_entry_bound`, computed by the padded
rule for a differencing image and capped by what the declared BAT
region holds. `total_bat_entries` itself is deliberately left at the
shorter count, because it also sizes the whole-BAT walks in
`scan_allocation` and `map_extents`; widening it would make those
walk past a BAT region a writer still sizes by the shorter rule --
the writer-side half of this same defect, left in place as issue
#623 (see Scope, above).

**F12. A parentless image could reach the composing arm, and the
answer it got there was invented.** Found by review round 1 on the
phase's pull request, not by this plan or by any step of it.
`block_lookup` returned `PartiallyPresent` for BAT state 7 whatever
`has_parent` said, and the arm composed it without rechecking. Nothing
upstream stops such an image: the differencing refusal in
`init_chain_states` fires on `has_parent`, which is exactly what the
image denies having, and `sb_bat_entry_bound` falls back to
`total_bat_entries` for it, which is wide enough to resolve the first
group's bitmap entry. So a crafted image with `HasParent` clear, a
state-7 payload entry and a present bitmap got a composed read where
before this phase existed it got a failure -- state 7 had no arm at
all and fell through the lookup's catch-all. That is the shape issue
#547 is about: inventing an answer for a malformed image. The doc
comment written in 13c asserted the opposite ("One that has not
cannot resolve the bitmap either, so it still fails rather than
inventing data"), which made it the wrong kind of wrong -- a claim a
reader would rely on. Fixed by refusing state 7 in `block_lookup`
when `has_parent` is clear, which is where the refusal belongs
because the crate is the authority on what each state means for each
kind of image.

**F13. The phase's mutation evidence was not reproducible from the
tree, and neither was phase 12's.** Also from review round 1. Phase
12's and phase 13's commit messages both describe mutations "kept in
a runnable script", and `tools/mutate-differencing.sh` is that script
by name -- but its 26 cases all mutate the *writer*, and the reader
mutations for both phases lived only in a session scratchpad. The
claim was therefore unfalsifiable by anyone reading the repository.
Corrected by committing reader cases to that harness in `13h` and
`13i` below, covering phase 12's VHD set as well as phase 13's VHDX
one: the gap was the same gap, and fixing only this phase's half
would have left the next phase's review to find the other.

**F14. Two properties were pinned by the wrong test, and one guard
was not a guard.** Found by the first full run of the harness once
the reader cases were in it, which is the point of putting them
there. Three cases named an arm test that did not kill their
mutation. Two were a mapping error: a coalescer that never advances
its bitmap byte, and a block-end guard admitting one sector too
many, are both invisible at the arm, because the arm re-enters the
coalescer once per ownership run with a correct starting byte and
refuses an over-long chunk by a second route. Those properties are
the crate's, so the cases now name the `vhd` and `vhdx` tests that do
pin them. The third was not a mapping error: `sector_bitmap_lookup`
checked `state == SB_BLOCK_NOT_PRESENT` and then
`state != SB_BLOCK_PRESENT`, and nothing can reach the second
through the first, so removing the earlier branch changed no
behaviour and no test could kill it. It read as two guards where
there was one. Collapsed into a single comparison whose comment
carries both reasons.

**F15. A present BAT entry naming file offset zero was believed,
three times over.** From review round 2. A BAT entry is a state in
its low bits and a megabyte-granular offset in the rest, so a zeroed
or truncated entry whose state bits happen to read as present names
offset 0 -- which is the file identifier, not a block. Nothing
checked it, for fully present payload blocks, partially present ones
or sector bitmaps alike, so each would have served header bytes as
though they answered the question asked: what is in this block, or
which of its sectors does the child own. The review raised the
sector-bitmap case; the other two are the same invariant and were
found by looking for it. Fixed with one named constant and a
comparison at each of the three sites, which is cheap because the
offset mask already clears the low twenty bits and so zero is the
only value below the floor.

Two smaller things came with it. `sectors_per_chunk_group` returned
`Some(0)` for a chunk ratio of zero, contradicting its own doc
comment, and a zero is a number every bounds check downstream
accepts -- the shape of a "cannot tell" answer dressed as a real
one. It returns `None` now. And the VHDX arm had no three-device
test, so the `vhdx_states[dev_idx]` re-borrow after the parent
recursion could have been pinned to zero undetected; the VHD side
pins the same property with a test and a mutation, and the VHDX side
now does too.

## Decisions

1. **Mirror phase 12's three-part shape rather than inventing one.**
   A lookup that resolves where the bitmap lives, a run coalescer over
   the bitmap, and a classify-then-serve arm. Phase 12 arrived at this
   shape under two review rounds and it is now the house pattern for
   this exact problem; a second, different shape in the same function
   would be worse than a slightly imperfect fit. Concretely:
   `differencing_block_lookup` → `sector_bitmap_lookup`,
   `read_sector_bitmap_run` → `read_vhdx_sector_bitmap_run`,
   `classify_vhd_chunk_ownership` → `classify_vhdx_chunk_ownership`,
   `read_vhd_child_runs` → `read_vhdx_child_runs`.

2. **Do not generalise phase 12's VHD code into shared helpers.** The
   differences are not parameters: a different structure locates the
   bitmap, the bit order is reversed, the sector unit is variable, and
   the bitmap block can be legitimately absent. A shared helper would
   carry four conditionals and would make each format's reader harder
   to check against its own spec. The coalescer is the one piece that
   looks genuinely common -- `coalesce_ownership_run`
   (`src/crates/vhd/src/lib.rs:1401`) already takes a closure
   `FnMut(u32) -> Option<u8>` returning a bitmap byte -- and even there
   the bit order differs, so the VHDX reader passes its own
   bit-extraction. Revisit sharing in phase 15, with both readers
   written and both test suites available to prove the refactor
   invisible, not now.

3. **Separate `NotPresent` from `Zero` in the arm, for every VHDX, not
   only differencing ones (F3).** `Zero` zero-fills the chunk and stops;
   `NotPresent` descends to the next device. For a non-differencing
   VHDX this is behaviour-identical, because a chain never has a device
   behind a parentless image and both answers reach the zero-fill tail.
   The alternative -- gate the split on `has_parent` -- keeps a second
   code path alive to no purpose and leaves the wrong doc comment in
   place. The regression test must prove the identical-behaviour claim
   rather than assert it.

4. **Fail closed at the bottom of a chain on all three parent-owned
   paths**, as phase 12 does: `NotPresent` with no device behind a
   child whose `has_parent` is set, a wholly parent-owned
   `PARTIALLY_PRESENT` chunk, and the parent-owned runs of a mixed one.
   Use `devices_behind()` (`src/crates/qcow2/src/lib.rs:9419`), which
   phase 12 added for exactly this and which already fails closed on an
   impossible offset.

5. **A `PARTIALLY_PRESENT` block whose SB entry is `SB_BLOCK_NOT_PRESENT`
   fails the read.** F7 records that the spec forbids the combination.
   The alternative readings -- treat the absent bitmap as all-child or
   all-parent -- each invent an answer for a malformed image, and
   inventing answers for malformed images is what issue #547 was. The
   same applies to an SB entry in any state other than 0 or 6.

6. **Refuse a chunk that reaches past the end of the block its bitmap
   describes**, as the VHD arm does. This is the asymmetry phase 12's
   F13 recorded: the older non-differencing path does not cap at the
   block boundary and issue #613 tracks it. Phase 13 adds no new
   instance of that defect and does not fix the old one.

7. **Test at crate level with a mock chain; no testdata fixture.** Same
   as phase 12's decision 7 and for the same reason: no host path
   builds a composing VHDX chain until phase 14, so an integration test
   cannot reach this code. Phase 15 owns the fixture-based
   cross-validation. The mock harness should be a VHDX sibling of
   `run_vhd_chain_read`, not a generalisation of it -- see decision 2.

8. **The bitmap fixture builder takes an explicit logical sector
   size and an explicit bit list.** Phase 12's review found that
   single-shape fixtures hide whole classes of mistake: every early
   fixture had a one-byte bitmap, so no arm test crossed a bitmap byte,
   and every fixture had one block, so no test resolved a BAT entry for
   block 1. Build the general fixture first this time.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 13a | low | sonnet | none | Add `vhdx-input` to the two feature lists that gate the qcow2 crate's optional input readers: the `cargo test --release -p qcow2` line in `Makefile`'s `test-rust` target (`:536`), and both `cargo clippy -p qcow2` invocations in `scripts/check-rust.sh` (`:134` and `:140`, the `fix` and check branches). The lists currently end `dmg-input,vhd-input`. Demonstrate the change reaches the code by injecting a deliberate type error into the VHDX arm of `read_chain_virtual_cluster` (`src/crates/qcow2/src/lib.rs:10269`), confirming `make lint` fails with `error[E0308]` naming that line, and removing the probe; state in the commit message that you did this and what it printed. F2 established that no clippy findings and no new tests follow, so do not expect either -- if clippy *does* report something, that is new since 2026-10-04 and worth saying so. Closes issue #616; use the `Fixes #616` keyword. |
| 13b | high | opus | none | Add the sector-bitmap reader to `src/crates/vhdx/src/lib.rs`. Three pieces. (1) `pub const SB_BLOCK_NOT_PRESENT: u64 = 0;` and `pub const SB_BLOCK_PRESENT: u64 = 6;` beside the `PAYLOAD_BLOCK_*` constants at `:197-207` (F8: neither exists today). (2) A `sector_bitmap_lookup` method on `VhdxState` (`:1641`) that, for a virtual offset, returns the host byte offset of the block's sector-bitmap block and the index of the first logical sector of the chunk within it, or `None`. The BAT index of the SB entry for payload block `b` is `(chunk_ratio + 1) * (b / chunk_ratio) + chunk_ratio`; `block_lookup` at `:2001` already computes the payload side of the same interleave and is the model to follow. Validate the SB entry's state: `SB_BLOCK_PRESENT` proceeds, anything else returns `None` (decision 5). Bit `n` of the bitmap block covers logical sector `n` *of the chunk group*, not of the block, so the sector index is relative to the group's first block. (3) A run coalescer. Read bitmap bytes through `data_cached_sector` / `data_cache_buf`, which are allocated and currently unused (F6) -- follow `read_u64_le_cached`'s cached-sector pattern. **The bit order is the opposite of VHD**: sector `i` is bit `i % 8` of byte `i / 8`, least significant first, measured in phase 1 (F7). A set bit means the sector lives in this file. A sector is `logical_sector_size` bytes, which is 512 **or 4096** -- not a constant. `coalesce_ownership_run` in the vhd crate (`src/crates/vhd/src/lib.rs:1401`) is the shape to copy, not to call (decision 2). Unit-test the coalescer as a pure function with both sector sizes, a bitmap spanning several bytes, and runs that start and end mid-byte. |
| 13c | high | opus | none | Teach the VHDX arm of `read_chain_virtual_cluster` (`src/crates/qcow2/src/lib.rs:10269`) to compose. Two separable changes; make them two commits if it reads better. First, split the `NotPresent | Zero` arm (F3, decision 3): `Zero` zero-fills `chunk_size` bytes and returns true, `NotPresent` continues to the next device, and for a child whose `has_parent` is set with no device behind it, `NotPresent` fails closed via `devices_behind()` (`:9419`, decision 4). Fix `VhdxBlockLookup::NotPresent`'s doc comment, which says "(reads as zero)". Second, add the `PARTIALLY_PRESENT` case: `block_lookup` (`src/crates/vhdx/src/lib.rs:1984`) returns `None` for state 7 today, so it needs a new `VhdxBlockLookup` variant carrying the block's file offset; classify the chunk with 13b's reader, serve an all-child chunk with the existing single read, fill an all-parent chunk by recursing into the chain, and for a mixed chunk fill from the parent and then overwrite the child's runs. Refuse a chunk reaching past the block boundary (decision 6). **Read every child run through `read_offset_sectors` with the caller's scratch, never `read_cluster_sectors`** -- phase 12's third review round removed exactly that branch because the aligned path still put a 64 KiB buffer on the guest stack whenever a run was not a whole number of device sectors (F9); `read_vhd_child_runs` on `develop` is the correct model, `ed4f2669`'s version is not. |
| 13d | high | opus | none | Crate-level tests in the qcow2 crate's test module. There is no VHDX harness there at all (F2), so build one: a VHDX sibling of `run_vhd_chain_read` and a fixture builder that takes the logical sector size, the per-block payload states, and an explicit list of child-owned sector indices (decision 8 -- build the general builder first; phase 12 paid two review rounds for not doing so). The mock device must refuse a read at any sector size but its own, as the VHD mock does, or a whole class of sector-size mistake is invisible. Cover, at minimum: an all-child and an all-parent `PARTIALLY_PRESENT` block; a mixed one; the same mixed case at `logical_sector_size` 4096; a run crossing a bitmap byte boundary; a block that is not the first in its chunk group, so the SB interleave arithmetic is exercised; `Zero` versus `NotPresent` giving different answers for a child with a parent behind it; each of the three fail-closed paths at the bottom of a chain; a `PARTIALLY_PRESENT` block with `SB_BLOCK_NOT_PRESENT`; and a chunk crossing a block boundary. Add a regression test proving decision 3's identical-behaviour claim for a non-differencing VHDX, comparing against `develop` at `758ffba8`. Prove each test by mutation rather than by reading it, keep the mutations in a runnable script, and state the count in the commit message -- phase 12 ended at fifteen and two of its survivors were real findings. The bit-order mutation (`i % 8` to `7 - i % 8`) and the sector-unit mutation (`logical_sector_size` to a literal 512) are the two that matter most. |
| 13e | medium | sonnet | none | Bookkeeping. `CHANGELOG.md`: a sibling of the phase 12 entry at `:12` saying the guest chain walker composes a differencing VHDX, and that no operation reaches it yet because `init_chain_states` still refuses every differencing VHDX. Record what the survey found at its source in `docs/plans/PLAN-differencing.md` if 13b-13d falsify anything this plan claims. Confirm `docs/map.md:238`'s VHDX partial-present limitation note is still true -- `map` is a different consumer and this phase does not change it -- and leave it alone if so. Do not touch `docs/` otherwise: phase 16 owns the documentation, and phase 14 owns the user-visible change. |
| 13f | high | opus | none | Unplanned, added during execution after 13d found that a differencing image's sector-bitmap BAT entries were unreachable for any block count that is not an exact multiple of chunk_ratio, including the project's own 16 MiB `vhdx-diff-child.vhdx` fixture (F11, above). `VhdxState::init` sized `total_bat_entries` the way an image with no parent is sized -- one entry per payload block plus one per group -- while a differencing image's BAT is padded to whole groups of `chunk_ratio + 1` entries, so the last group's sector-bitmap entry, and every entry in a disk smaller than one group, sat past the bound `sector_bitmap_lookup` checked itself against. Add a separate bound, `sb_bat_entry_bound`, computed by the padded rule when `has_parent` is set and capped by what the declared BAT region holds; leave `total_bat_entries` at the shorter count, because `scan_allocation` and `map_extents` size their whole-BAT walks by it and widening it would walk those past a region a writer still sizes by the shorter rule. Do not touch the writer side (`calculate_bat_layout`, `src/crates/vhdx/src/lib.rs:3301`) -- that is issue #623, out of scope here (see Scope, above). Add crate-level tests of the entry-count arithmetic and a compose test at the project's own 16 MiB geometry; confirm the compose test fails without the fix and add a mutation reverting the count. Built as `ec005d57`. |
| 13g | high | opus | none | Unplanned, added during execution. The Definition of done requires a test proving the VHDX differencing refusal fires for its own reason rather than for any init failure, and the 13d brief omitted it -- my error in writing the brief, not the implementer's. Add a `send_error` recorder to the VHDX harness and assert the operation and status the refusal raises, with dynamic-image controls and a two-device case so the #614 hazard is pinned. Built as `213725a0`. |
| 13h | high | opus | none | Unplanned, added in response to review round 1 on the phase's pull request. Three things, and the third is the systemic one. Refuse `PAYLOAD_BLOCK_PARTIALLY_PRESENT` in `block_lookup` when `has_parent` is clear, so a crafted parentless image cannot reach the composing arm and get an invented answer (F12, above). Separate the sector-bitmap state check from the absent-offset case with a test that rewrites a *present* entry's state in place, keeping a usable bitmap at the offset the entry carries, so the test cannot pass by rejecting offset zero alone; and cover the two sub-sector shapes the arm is written for but no test drove -- a read beginning part way through a logical sector, and a 512-byte-logical-sector image read through a 4096-byte device sector. Then commit the reader mutations to `tools/mutate-differencing.sh` (F13, above): 31 cases covering phase 12's VHD arm as well as this phase's VHDX one, each naming the one qcow2 test that must kill it and running with the full input-format feature list, plus a `rust_survivor_case` type for the one mutation documented as *not* caught -- it asserts survival and fails if the mutation is ever killed, because that would mean the recorded reason has gone stale. Update `EXPECTED_CASES` and `docs/testing.md` together, which the harness checks for itself. Then run the harness and act on what it says: the first full run returned four cases the named test did not kill, which became F14 above -- three re-pointed at the crate test that does pin the property, and one guard collapsed because it turned out not to be one. Final state 57 cases, 56 killed and one surviving as documented, in 4m43s on a warm tree. Built as `13h`. |
| 13i | high | opus | none | Unplanned, added in response to review round 2. Three of its fix items were stale counts in prose that the round-1 commit had moved -- the harness case total, the reader-case count and the survivor count -- so the fix is not only to correct them but to derive them: `check_case_count` now counts the reader and survivor cases in the script itself and asserts both against `docs/testing.md`, the way it already did for the total, and the hand-maintained numbers are gone from the CI workflow comment entirely. Both new checks were confirmed to fire by making each count wrong in turn. Then the four optional items, all taken: refuse a BAT entry naming file offset zero at all three sites that carry an offset, not only the sector bitmap the review named (F15, above); make `sectors_per_chunk_group` return `None` for a zero-sector group as its doc comment already claimed; add the three-device VHDX chain test and the `dev_idx` mutation the VHD side has had since phase 12; and rewrap a CHANGELOG line. Five mutations accompany the new tests, taking the harness to 62 cases. |

## Risks and mitigations

* **The bit order is written the VHD way.** This is the single most
  likely defect, it is invisible to any symmetric fixture (`0x00`,
  `0xFF`, or a palindromic byte), and it produces plausible data rather
  than an error. *Mitigation:* 13d's fixtures use asymmetric bytes, and
  the mutation set includes the bit-order flip specifically. The
  implementer checks the measured statement at
  `docs/plans/PLAN-differencing-phase-01-pin.md:1463` rather than
  reasoning from the VHD code beside them.
* **`logical_sector_size` 4096 is treated as 512.** Every bitmap
  arithmetic error of this kind still works at 512. *Mitigation:* 13d
  requires the mixed case at 4096, and a mutation replacing
  `logical_sector_size` with a literal 512 must fail a test. Phase 12
  hit precisely this: its first sector-size mutation survived because
  no test used any sector size but 512.
* **The SB interleave is computed for block 0 and never for any
  other.** `(chunk_ratio + 1) * (b / chunk_ratio) + chunk_ratio`
  degenerates to `chunk_ratio` when `b < chunk_ratio`, so a fixture
  with few blocks exercises nothing. *Mitigation:* 13d requires a
  block outside the first chunk group. Phase 12's equivalent gap --
  every fixture a single block -- was found by review, not by the
  phase.
* **The `NotPresent` / `Zero` split changes non-differencing reads.**
  It should not, and decision 3 rests on that. *Mitigation:* 13d's
  regression test compares against `develop` at `758ffba8`; the
  implementer states the two captured outputs in the commit message.
* **The arm is written against a reader that is never compiled.** 13a
  is first for this reason, and F1 proves it is load-bearing rather
  than tidy-mindedness: without it, 13b-13d can be committed broken and
  CI stays green.

## Definition of done

* `make lint` and `make test-rust` compile the VHDX arm. Falsifiable:
  injecting a type error at `src/crates/qcow2/src/lib.rs:10269` makes
  `make lint` fail, where on `758ffba8` it exits 0. Issue #616 is
  closed by the `Fixes` keyword in 13a's pull request body, not only in
  a commit message.
* `grep -c 'data_cache_buf' src/crates/vhdx/src/lib.rs` returns more
  than the 4 it returns today -- that is, F6's allocated-but-unused
  cache is used.
* `grep -c 'SB_BLOCK_PRESENT' src/crates/vhdx/src/lib.rs` is non-zero,
  and no literal `6` stands in for it at a use site.
* A differencing VHDX child over a parent device, with a
  `PARTIALLY_PRESENT` block whose sector bitmap is mixed, reads each
  logical sector from the correct device, at both 512 and 4096. A
  mutation reversing the bit order fails a test, and a mutation
  replacing `logical_sector_size` with 512 fails a different one.
  State in the commit message that both were run and what they printed.
* A `PARTIALLY_PRESENT` block whose SB entry is `SB_BLOCK_NOT_PRESENT`
  fails the read; so does an SB entry in any state but 0 or 6.
* `PAYLOAD_BLOCK_ZERO` and `PAYLOAD_BLOCK_NOT_PRESENT` produce
  different results for a differencing child with a device behind it,
  and identical results for a VHDX with nothing behind it. Both are
  asserted by tests, not argued in a comment.
* A non-differencing VHDX over a backing device produces byte-identical
  output to `develop` at `758ffba8`; the two captured outputs are in
  13d's commit message.
* `init_chain_states` still refuses every differencing VHDX
  unconditionally, and a test asserts the refusal fired for that reason
  -- by the status reaching `send_error`, not by the return value
  alone. Phase 12's `vhd_init_refuses_a_differencing_child_and_admits_a_dynamic_one`
  is the model, including its positive control.
* `make test-rust` passes with zero failures and the count is stated,
  against the 2383 this survey measured on `758ffba8`.
* No test in this phase depends on a testdata fixture.
* No source file or comment added by this phase cites a plan phase,
  step or decision number. Falsifiable:
  `git diff 758ffba8..HEAD -- 'src/*' | grep '^+' | grep -iE 'PLAN-[a-z0-9-]+\.md|decision [0-9]|phase 1[0-9]|13[a-e]'`
  is empty.
* `docs/map.md:238`'s VHDX partial-present limitation is either still
  true or updated; the pull request says which.
* `CHANGELOG.md` says the VHDX composition path exists and that no
  operation reaches it yet.

## Back brief

Before implementing, confirm back to me:

1. **The three VHDX-versus-VHD differences, in your own words**, and
   where in 13b each one is handled: bit order, sector unit, and the
   bitmap block's location and possible absence. Phase 12's review
   rounds were largely about input shapes nobody had thought of; the
   cheapest place to catch the equivalent here is before any code is
   written.
2. **Whether decision 2 still looks right once you have read both
   crates' bitmap code.** If the coalescer really is shareable with one
   closure and no conditionals, say so before writing a second copy --
   that is the one decision here a reviewer is most likely to argue
   with, and it is much cheaper to change now than after 13d's tests
   are written against two readers.
3. **The SB BAT index formula, checked against a worked example** with
   `chunk_ratio` 4096 and a block index above 4096. Get this wrong and
   every test using a small fixture still passes.

Gate: do not start 13d until 13b and 13c are both committed. Phase 12
wrote its harness against mid-phase code and the third review round
changed the production shape underneath it; the tests are cheaper to
write once the shape has settled.
