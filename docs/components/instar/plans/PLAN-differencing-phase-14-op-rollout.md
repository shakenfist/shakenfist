# Differencing phase 14: composition rollout across the chain-walker operations

## Prompt

Plan phase 14 of `PLAN-differencing.md`: turn the phase 4 refusal
into composition for the operations that already read through the
guest chain walker, so that `instar convert`, `dd`, `compare`,
`bench` and `rebase` read a differencing VHD or VHDX against its
parent instead of declining the source by name.

Phases 11 to 13 built the machinery and none of it is reachable from
a user command. The host resolves a chain (`c66f8b2`, #603), the
guest composes a differencing VHD block (`b4ae7fc`, #615) and a
differencing VHDX chunk (`d677823c`, #624) -- and every read entry
point still refuses such a source before any of that runs. This is
the phase that changes what a user sees, which is why the master
plan gives it its own review rather than tacking it onto a guest
phase.

The phase plan is the deliverable; implementation is a separate ask.

## Planning effort

**High.** The diff is mostly deletions of `if` statements, which is
exactly what makes it dangerous: the phase's real content is
deciding *when* each deletion is safe, and three of the four
preconditions were discovered rather than planned. Two are filed
issues this phase must settle before it deletes anything (#623,
#614), one is a reachability change this phase creates (#625), and
the fourth is a host-side gate whose 40-line justifying comment
stops being true halfway through the phase.

A wrong answer here is silently wrong user data from a command that
previously failed loudly, which is a strictly worse failure than the
refusal it replaces.

Review effort: **high**. The master plan does not specify one for
this phase.

## Scope

**In scope.**

* Issue #623: `vhdx::calculate_bat_layout` sizes a differencing
  VHDX BAT by the dynamic rule, omitting the chunk-group padding.
  A gate, per the master plan.
* Issue #614: `init_chain_states` cannot tell chain boundaries
  apart from its device count. A gate, per the master plan.
* Issue #625: the VHDX chain walker accepts a block offset that
  overlaps the BAT or metadata region. In scope here because
  lifting the refusal is the change that makes it reachable from a
  user command -- see decision 3.
* The host-side composability gate at `src/vmm/src/main.rs:3047`,
  which today refuses to resolve a differencing parent for *any*
  composing caller.
* Lifting the `init_chain_states` refusals (`src/crates/qcow2/src/lib.rs:13501`
  for VHD, `:13529` for VHDX) for the operations that read through
  the chain walker: `convert`, `compare`, `bench`, `rebase`, and
  `dd` by inheritance.
* Python integration tests proving each of those operations reads a
  real differencing chain correctly, and that the operations this
  phase does not cover still refuse.
* Removing the `PLAN-differencing.md` citation from the
  user-visible refusal message (`src/vmm/src/main.rs:852`).
* `docs/` and `CHANGELOG.md` for the behaviour change.

**Out of scope.**

* `map`, `measure` and `check`. The master plan lists all three as
  phase 14 work; the survey found they do not use the chain walker
  at all and composing in them means building chain support rather
  than lifting a refusal -- see F1 and decision 1. They become
  phase 15, and the current phases 15 to 17 renumber to 16 to 18.
* `commit`. It is a composing caller on the host
  (`src/vmm/src/main.rs:7265`) but its guest op reads through its
  own `backing_chain_first` / `backing_chain_count` slots rather
  than `init_chain_states`, so it is neither refused today nor
  lifted here. Recorded because the survey found it and a reader
  will otherwise wonder.
* Cross-validation against the phase 1 oracle, and coverage fuzzing
  of the compose path. That is phase 16 (the current phase 15),
  including the 4096-byte logical sector gap phase 13 left it.
* Issue #626, raised by 14a and filed rather than acted on: a
  claim that instar's *non-differencing* BAT entry count may
  over-declare by one entry against SPEC(VHDX) and qemu's
  `vhdx_calc_bat_entries` whenever `total_blocks` is an exact
  multiple of `chunk_ratio`. It is out of scope because it was
  not measurable from the evidence to hand -- the megabyte
  rounding of the BAT region makes a writer's intended entry
  count unobservable from the file it produced -- and because
  instar over-declares rather than under-declares, which is the
  safe direction on the writer side. The read side is a
  looseness rather than a defect, since `VhdxState::init`'s
  bound is additionally capped by the declared region. The issue
  records the geometry that would settle it.
* The ~20 other `PLAN-*.md` citations in comments across
  `src/operations/` and `src/shared/`. Pre-existing, not created by
  this phase, and a tree-wide sweep does not belong in a phase that
  changes read behaviour. File an issue.

## What the survey found

Surveyed against `d677823c` on 2026-10-05. The master plan's phase
14 bullet (`docs/plans/PLAN-differencing.md:530`) describes the
phase as "turning the refusals into composition across `convert`,
`compare`, `dd`, `bench`, `map`, `measure` and `check`" and calls it
"mechanical once 11 to 13 land". Six findings, five of which
contradict that sentence. The master plan's bullet and the
`index.md` row are corrected in this phase's planning commit.

**F1. Three of the seven named operations do not use the chain
walker, and one of the walker's consumers is not named.** The
refusal the phase 12 bullet called "a single `if` in
`init_chain_states`" is one of four independent families:

| Operation | Chain walker | Refusal site | What a lift means |
|---|---|---|---|
| `convert` | yes | `init_chain_states` | lift, plus the host gate |
| `dd` | via `convert` | inherits | host only, no guest change |
| `compare` | yes | `init_chain_states` | lift; the op that needs #614 |
| `bench` | yes | `init_chain_states` | lift |
| `rebase` | yes, `src/operations/rebase/src/main.rs:1201` | `init_chain_states` | **absent from the master plan's list** |
| `check` | partial: `validate_chain` walks members | own, `:1593` / `:1996` | validates members independently; composes nothing |
| `measure` | no `ChainConfig` at all | own, `:422` / `:441` | build chain support it lacks |
| `map` | no `ChainConfig` at all | own, `:463` / `:531` | ditto, and `src/shared/src/lib.rs:833` defers it to `PLAN-map.md` |

`rebase` matters most of the four corrections: it reads through
`init_chain_states`, so lifting the refusal there changes `rebase`
whether this phase plans for it or not. Leaving it unnamed would
have shipped an unplanned behaviour change.

`grep -c qcow2::init_chain_states src/operations/*/src/main.rs`
reproduces the first column; `grep -c ChainConfig` the second.

**F2. `dd` is not a separate operation.** `run_dd`
(`src/vmm/src/main.rs:14629`) ends in `execute_convert` (`:14732`),
so there is no `src/operations/dd` and no guest-op work for it. It
does take its own `discover_backing_chain` with `ChainUse::Compose`
at `:14686`, so it needs the host half and nothing else.

**F3. The host gate is per-caller-kind, and the rollout is
per-operation.** `src/vmm/src/main.rs:3047` holds

```rust
(ChainUse::Compose, true) => { debug!(...); break; }
```

which records a differencing parent and never resolves it, for
every composing caller. Its justifying comment (`:2989-3010`)
argues that resolving a parent instar will not read would make the
refusal contingent on the parent's presence -- sound reasoning,
resting on the premise "Nothing in instar can compose a VHD or VHDX
chain yet: every read entry point refuses such a source by name".
This phase makes that premise false for five operations and leaves
it true for three, so the gate cannot stay a property of
`ChainUse`: it has two variants (`:2700`), nine `Compose` call
sites and one `Report` (`run_info`, `:10715`). See decision 5.

**F4. #623's blast radius is five consumers, not the writer.**
`calculate_bat_layout` (`src/crates/vhdx/src/lib.rs:3357`) takes
`(virtual_disk_size, block_size, logical_sector_size)` and no
`has_parent`, so it always sizes by the dynamic rule
`total_blocks + ceil(total_blocks / chunk_ratio)`. The
differencing rule is `ceil(total_blocks / chunk_ratio) * (chunk_ratio + 1)`.
Callers: `src/crates/create/src/lib.rs:1404`,
`src/operations/create/src/main.rs:654`,
`src/crates/measure/src/lib.rs:1025`,
`src/crates/resize/src/vhdx.rs:85`, and
`src/operations/convert/src/main.rs:4394`. That is wider than
"the writer side" as the master plan's gate paragraph (`:537`)
implies, and it is why 14a is its own step with its own risk entry.

**Partly falsified by 14a's execution.** Two predictions in the
paragraph above were wrong, and both are recorded here rather than
silently fixed because a later step would otherwise rely on them.
`measure` cannot predict a differencing VHDX at all -- `VhdxOpts`
carries no parent field and `MeasureConfig` no backing field, so
there is no route to ask it and nothing about its output could
change; its call site passes `false` and says why. And the bytes
`create -f vhdx -b` writes do not change at any geometry the suite
already used: the BAT region is rounded up to a megabyte and the
two rules differ by at most `chunk_ratio - 1` entries, so both
counts land in the same region. No test expectation moved. That
rounding is also why the defect survived -- it is visible only
where the shortfall escapes the megabyte -- so 14a's tests pick
such a geometry deliberately.

**F5. #614 has an in-tree precedent for its fix.** `ChainConfig`
(`src/shared/src/lib.rs:4817`) is a flat `[ChainDeviceInfo; 16]`
with a `device_count` and nothing describing segmentation, which is
the whole of #614. But the information exists twice already, in
per-operation configs: `CompareConfig.image1_device_count` /
`image2_device_count` (`:2376`, `:2381`), and
`CommitConfig.backing_chain_first` / `backing_chain_count`
(`:4563`, `:4566`) -- a literal `(start, len)` segment descriptor
already on the wire. And `read_chain_virtual_cluster` (`:11455`)
already takes `chain_start` and `chain_len`, so the *reader* knows
its own chain's bounds; only `init_chain_states` does not. The fix
is to put the segmentation where the chain is described rather than
deriving it a fourth time. See decision 4.

**Corrected by the 14b design gate**, which read the tree rather
than this finding and found three things wrong with it. Six host
paths write a `ChainConfig`, not four: `run_commit_guest`
(`src/vmm/src/main.rs:10461`) and `run_check` (`:11798`) also do,
and `check` reads one (`src/operations/check/src/main.rs:293`,
`:422`) without ever calling `init_chain_states`, so tightening
`ChainConfig::is_valid` reaches two operations this phase does not
otherwise touch. `init_chain_states` has five call sites, not four
-- `convert` calls it twice, and the second
(`src/operations/convert/src/main.rs:1007`) mutates device 0 in
place for the LUKS inner image with `device_count` 1 and restores
it afterwards, so it must override and restore the segmentation
too. And `is_valid` (`src/shared/src/lib.rs:4853`) already checks
`device_count > 0` as well as magic, which is what makes
`segment_count == 0` an unreachable state rather than merely an
unusual one.

**F6. The user-visible refusal message cites a plan file.**
`src/vmm/src/main.rs:852` renders "composition is deferred (see
PLAN-differencing.md)" into the error a user reads. This phase
rewrites that message anyway, since it stops being the answer for
five operations, so the citation goes with it.

One thing the survey confirmed rather than contradicted: the master
plan is right that both gates must be settled before any refusal is
lifted, and right about why. Lifting before #623 ships a reader that
cannot resolve the sector bitmaps of images instar itself wrote.

## Decisions

1. **Phase 14 covers the chain-walker operations only; `map`,
   `measure` and `check` become phase 15.** Decided with Michael on
   2026-10-05 after the survey. The two groups are different work:
   one deletes guards from a code path that already composes, the
   other builds chain plumbing in operations that have never had
   any, and `map`'s belongs to a different master plan. Mixing them
   would produce a phase whose review could not be scoped. The
   current phases 15, 16 and 17 renumber to 16, 17 and 18.

2. **Both gates are steps of this phase, 14a and 14b, before any
   refusal is lifted.** #614 on its own ships nothing a user can
   observe, so a separate gate phase would have no testable
   deliverable and no way to demonstrate the thing it enables. Put
   adjacent to the lift they gate, each is verifiable by the lift
   failing without it.

3. **Issue #625 is in scope, and it is this phase that makes it
   urgent.** Phase 13 added a `MIN_BLOCK_FILE_OFFSET` floor so a
   zeroed BAT entry cannot name offset 0, but an entry naming 2 MiB
   -- inside the BAT or metadata region of a small image -- is still
   accepted. While every read entry point refuses a differencing
   source, that path is reachable only from crate tests. Lifting the
   refusal makes it reachable from `instar convert` on an untrusted
   image, which turns a latent bound check into a reachable one.
   Fixing it in the phase that creates the reachability is cheaper
   than filing it forward, and a reviewer should not have to
   reconstruct that argument.

4. **#614 is fixed by giving `ChainConfig` the segmentation, at
   version 3 -- not by adding a parameter to `init_chain_states`.**
   The alternative is to pass a segment list from each of the four
   call sites, derived from each operation's own config. That works
   and is a smaller diff, and it is the wrong shape: it asks four
   operations to compute the same fact consistently, when
   `ChainConfig` is the struct whose entire job is describing the
   chain. `ChainConfig` has a `version` (currently 2, for
   `data_device_idx`) and a `_reserved` word, so the extension is
   routine, and `CommitConfig`'s `(first, count)` pair is the
   in-tree precedent for the field shape. Making the answer
   derivable once is also what stops a fifth consumer getting it
   wrong later.

   **This is the decision most likely to be argued with**, because
   it is a guest/host ABI change in a phase whose point is deleting
   `if` statements, and because the per-operation alternative would
   let 14b be a two-line change in `compare` alone. Three things
   decide it. `compare` is not the only op with a multi-chain
   future -- a `convert` with a differencing source and a
   differencing output chain has the same shape. The reader already
   carries `chain_start`/`chain_len`, so the asymmetry being fixed
   is that `init_chain_states` was handed less than its sibling, not
   that nobody knew. And a wrong answer composes a child against an
   unrelated image, which the refusal currently prevents and no test
   that uses a single chain can detect -- exactly the defect class
   phase 12 found by writing the two-device case.

5. **The host gate becomes a per-call capability, not a third
   `ChainUse` variant.** `ChainUse` distinguishes "will attach and
   launch" from "will print"; whether a *particular* operation can
   compose a differencing VHD is orthogonal to that, and encoding it
   as `ChainUse::ComposeDifferencing` would make the enum mean two
   things at once and leave nine call sites to audit for which they
   meant. Add a separate argument carrying the composability, so a
   call site states its capability explicitly and a reader can
   enumerate the five that say yes. The comment at `:2989` is
   rewritten to argue the surviving case rather than deleted: the
   reasoning is still correct for the operations that still refuse.

6. **Rollout order is `convert` (with `dd`), then `compare`, then
   `bench` and `rebase`.**

   **Falsified by 14e's execution, in the part that assumed the lift
   could be staged at all.** `init_chain_states` is one function with
   five callers, so removing its refusal lifts it for `convert`, `dd`,
   `compare`, `bench` and `rebase` in the same instant. Measured: after
   14e, `bench -c 4` on a differencing VHD returns a throughput number
   and `compare` reports a verdict, neither of which 14e set out to
   change. Staging it per operation would have meant a per-operation
   capability on the *guest* ABI, which decisions 4 and 5 deliberately
   keep on the host -- and the host gate from 14d is what actually
   achieves the per-operation rollout, because an operation whose call
   states `Unsupported` never has a parent attached and so can only see
   a single-device chain. The three operations that still refuse do so
   in their own code, never reaching `init_chain_states` at all. The
   consequence is that 14f and 14g carry no code change: they become
   the steps that assert the behaviour 14e already shipped, which is
   what decision 6's own reasoning about test strength was really
   about. `convert` first because it is the
   operation a user reaches for, it has the most read paths
   (`read_chain_virtual_cluster` ten times in
   `src/operations/convert/src/main.rs`), and an integration test
   for it is a whole-file byte comparison -- the cheapest strong
   proof available. `compare` second because it is the op that
   exercises 14b: it is the one that packs two chains into one
   array. `bench` and `rebase` last and together: both are single
   additional call sites once the first two are right.

7. **`calculate_bat_layout` gains a required parameter, not a
   defaulted one or a second function.** Every one of the five
   consumers must state whether it is sizing a differencing image,
   and the compiler should refuse the ones that do not. A
   `calculate_bat_layout_differencing` sibling would let a consumer
   keep calling the old name and be silently wrong, which is the
   failure #623 already is.

8. **A half-lifted tree must say so.** After this phase, `convert`,
   `dd`, `compare`, `bench` and `rebase` compose a differencing
   source and `map`, `measure` and `check` refuse it. The refusal
   message must name the operation and not imply that instar cannot
   compose at all -- a user who has just run a successful `convert`
   and then a refused `map` needs the message to explain the
   difference rather than contradict their last command.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 14a | high | opus | none | Fix issue #623. Give `vhdx::calculate_bat_layout` (`src/crates/vhdx/src/lib.rs:3357`) a required `has_parent: bool` parameter and size the BAT by the differencing rule when it is set: a differencing VHDX BAT is padded to whole chunk groups, `ceil(total_blocks / chunk_ratio) * (chunk_ratio + 1)` entries, where a dynamic one takes `total_blocks + ceil(total_blocks / chunk_ratio)`. Update all five consumers to pass their actual intent: `src/crates/create/src/lib.rs:1404`, `src/operations/create/src/main.rs:654`, `src/crates/measure/src/lib.rs:1025`, `src/crates/resize/src/vhdx.rs:85`, `src/operations/convert/src/main.rs:4394`. Do not add a defaulted parameter or a sibling function (decision 7). Three consequences to handle rather than discover: `measure`'s predicted size for a differencing VHDX changes, so its expectation changes with it; `resize` must be checked for whether it accepts a differencing VHDX at all before its call site is given a `true` branch; and the bytes `create -f vhdx -b` writes change, so any golden fixture or round-trip expectation over that output changes too -- find them with `make test-rust` and the `tests/test_create.py` and `tests/test_differencing.py` suites rather than by reading. The phase 13 reader deliberately caps itself at the declared BAT region (`sb_bat_entry_bound`, added in `ec005d57`); once the writer is right, confirm that cap no longer truncates instar's own output, and say in the commit message which image geometry you checked it at. Add a mutation to `tools/mutate-differencing.sh` reverting the padded rule. Closes #623 -- use the `Fixes` keyword in the pull request body, not only the commit message. Built as `6d8ee05d`. |
| 14b | high | opus | none | Fix issue #614. `init_chain_states` (`src/crates/qcow2/src/lib.rs:13404`) receives only `device_count`, a bound on a flat array that may hold several independent chains -- `compare diff.vhdx base.raw` packs two -- so it cannot tell a differencing child with a real parent behind it from one followed by an unrelated image. Extend `ChainConfig` (`src/shared/src/lib.rs:4817`) to carry the segmentation and bump `ChainConfig::VERSION` to 3, following the `(first, count)` field shape `CommitConfig.backing_chain_first` / `backing_chain_count` (`:4563`) already ships; a `_reserved` word is available. Do not add a parameter to `init_chain_states` instead (decision 4). Populate it on the host for every caller that writes a `ChainConfig` -- and note that the four line numbers this row originally gave for `run_rebase` and `run_compare` were their argument-parsing functions, not their config writers; the writers are `run_rebase_guest` (`src/vmm/src/main.rs:10120`) and `run_compare` (`:12965`, not the `:12674` an earlier draft of this plan gave), and `run_commit_guest` (`:10461`) and `run_check` (`:11798`) write one too -- and assert in the guest that a chain's segment bounds agree with `device_count`. No refusal is lifted in this step: the deliverable is that `init_chain_states` *could* make the per-chain judgement, proved by a test that builds a two-chain array with a differencing child at index 0 and asserts the function sees chain length 1 for it. The existing `vhdx_init_refuses_a_differencing_child_and_admits_a_dynamic_one` (`src/crates/qcow2/src/lib.rs:9795`) documents the hazard in its comment and is the test to extend. Closes #614. Built as `1bd1dac2`. |
| 14c | medium | sonnet | none | Fix issue #625. Phase 13 added `vhdx::MIN_BLOCK_FILE_OFFSET` so a zeroed or truncated BAT entry cannot name file offset 0, but an entry naming any offset inside the BAT or metadata region of a small image is still accepted, and `block_lookup` / `sector_bitmap_lookup` will read a block from there. Bound a payload or sector-bitmap block's file offset below by the end of the last declared region rather than by a 1 MiB constant: `VhdxState` already parses the region table at init, so the bound is derivable from what the image declares about itself. Refuse, do not clamp -- an image whose BAT names a block inside its own metadata is malformed, and the phase 12 and 13 arms both fail closed rather than guess. Add crate tests for an offset inside the BAT region, inside the metadata region, and immediately after the last region as a positive control, plus mutations in `tools/mutate-differencing.sh`. This is a bound check, not a behaviour change: no image instar writes names such an offset, and the test that proves it is the existing compose suite still passing. Closes #625. **Redesigned during execution.** This row originally said to bound the offset below by the end of the last declared region, which was wrong: SPEC(VHDX) does not require a region to precede the payload blocks, so a legally placed trailing region would have made the bound refuse every block in the file. The implemented test is the one the issue names -- the block's byte range overlapping a declared region's -- over every entry the table carries, recognised or not. Offset zero is not subsumed by that predicate, because a block at zero overlaps nothing unless a region starts at zero, so it stays a separate guard and both are independently killable. **The row's "not a behaviour change" was also wrong**, and review round 1 caught it: the overlap test sits in `block_lookup`'s fully-present arm, which runs whatever `has_parent` says, so it tightens every VHDX read rather than only a differencing one. No conforming image is affected, but a malformed one instar used to read now fails the read, and that belongs in the changelog as a *Changed* entry rather than being described as a latent fix. Corrected there, and a crate test now pins the refusal on a `has_parent == false` image so the dynamic path cannot regress unnoticed. Built as `52c3cfbc`. |
| 14d | high | opus | none | The host-side composability gate. `discover_backing_chain` (`src/vmm/src/main.rs`, the match at `:3044`) refuses to resolve a differencing VHD or VHDX parent for every `ChainUse::Compose` caller. Replace the caller-kind test with an explicit per-call capability argument (decision 5) -- not a third `ChainUse` variant -- so each of the ten call sites states whether its operation can compose a differencing chain. Set it true for `run_bench` (`:5045`), `run_rebase` (`:6903`, `:6916`), `run_compare` (`:12407`, `:12417`), `execute_convert` (`:13255`) and `run_dd` (`:14686`); false for `run_commit` (`:7265`) and `run_check` (`:11662`); `run_info` (`:10715`) stays `Report` and is untouched. Rewrite the comment at `:2989-3010` rather than deleting it: its argument -- that a refusal must never become contingent on whether a parent file happens to exist -- is still exactly right for the callers that still refuse, and is the invariant phase 4 established and phase 11 preserved. State in the commit message which call sites you set each way and why, because that list is the phase's actual policy. No guest change in this step; the refusals are still in `init_chain_states`, so every operation still fails, and the proof of this step is that the host now resolves the parent and the guest still declines it. **Corrected by execution.** This row's claim that nothing user-visible changes is false. Resolving a parent means failing when it is missing or outside the allowlist, so `convert`, `dd`, `compare` and `bench` now fail in the host walk for an orphaned parent instead of reaching the guest's typed refusal; the step is invisible only for a well-formed chain whose parent is present. Three integration tests asserted the old caller-kind scoping and were rewritten along the new policy boundary rather than relaxed, and the six hostile-locator fixtures are now declined by path resolution and the allowlist during discovery, with a per-fixture reason table measured against the built binary and an assertion that no output file is produced. One consequence 14e must close: between these two steps a differencing source fails one way with its parent present and another way without, which is the contingent refusal phase 4 ruled out. Built as `185df853`. |
| 14e | high | opus | none | Lift the refusal for `convert`, and `dd` with it. Remove the VHD (`src/crates/qcow2/src/lib.rs:13501`) and VHDX (`:13529`) refusals from `init_chain_states`, replacing them with the per-chain judgement 14b made possible: a differencing child with no device behind it *in its own chain* must still be refused, because composing it would read parent-owned sectors from nothing. That is the condition phase 12 proved unsafe to write against `device_count` and 14b makes safe to write against the segmentation. Keep the typed refusal and its status codes (`shared::DifferencingRefusal`) for that case -- it is now a narrower refusal, not a deleted one. Rewrite the host message at `src/vmm/src/main.rs:841-857` per decision 8: name the operation, say that the source's parent could not be composed and why, and drop the `PLAN-differencing.md` citation (F6). Verify with a Python integration test that converts a real differencing VHD and a real differencing VHDX to raw and compares the whole output byte-for-byte against the same chain flattened by the phase 1 oracle or by `instar` reading the parent directly -- a whole-file comparison, not a spot check. Both formats, and a chain of depth 3 for at least one of them. **Execution notes.** No depth-3 fixture exists and `create -b` refuses to stack a child on a child, so the test builds one with `qemu-img rebase -u`. The expectation came from the recorded `*-composed.raw` fixtures rather than the phase 1 oracle, which is absent on the development host and is in any case the wrong reference for the mixed-bitmap VHD child. Built as `42a23cc1`. |
| 14f | high | opus | none | Lift for `compare`, which is the operation 14b exists for. `compare` packs two independent chains into one device array (`CompareConfig.image1_device_count` / `image2_device_count`, `src/shared/src/lib.rs:2376`) and calls `init_chain_states` once with the total (`src/operations/compare/src/main.rs:215`). The tests that matter are the ones a single-chain test cannot fail: a differencing child as image1 against a raw image2, where the old `dev_idx + 1 >= device_count` form would have composed the child against image2; and both images differencing children of different parents. Assert the comparison verdict, not just that the command exits 0 -- a wrongly composed read can still produce "identical" if both sides are wrong the same way, so one test must compare a differencing chain against its own correctly flattened output and a second must compare it against a deliberately different image and expect a difference at a known offset. **No production change**: 14e's lift already covered `compare`, so this step is the proof rather than the change. Five tests, including the one the brief's point 3 really asked for -- image2 set to qemu's own flattening of the same child, the one file a reader that composed the child against image2 would call identical -- and a four-device, two-chain case built by copying each parent into its own directory and altering one, so the children stay byte-identical and only per-chain descent can move the verdict. Execution found that collapsing the segmentation does not by itself produce silent wrong data, because `read_chain_virtual_cluster`'s own `chain_len` bound refuses the descent, which is why each test pins an exact message or an exact offset rather than a non-zero exit. Built as `969ac5de`. |
| 14g | medium | sonnet | none | Lift for `bench` and `rebase`. Both read through `init_chain_states` and need no new guest logic once 14e is in: `src/operations/bench/src/main.rs:1395` and `src/operations/rebase/src/main.rs:1201`. `rebase` is the one the master plan did not name (F1), so it needs the most care in testing rather than the least. **Re-scoped by 14e's execution**: `rebase` refuses any overlay that is not qcow2 or vmdk (`src/vmm/src/main.rs:7138`, `rebase: format 'Vhd' does not support rebase`), so "rebasing a differencing VHD or VHDX child" is not a thing this tool does and the brief as first written is untestable. What is testable, and what this step must cover, is a differencing VHD or VHDX sitting in the *backing chain* of a qcow2 overlay being rebased -- the chain is read, so the composition matters, and the test must assert the rebased image's contents rather than that the command completed. 14e also found `rebase` has no `differencing_refusal_error` call site, so a guest refusal reaching it renders a generic message; this step decides whether to add one. **Execution found three production defects in `rebase`, so this step did carry code after all.** The operation's `Cargo.toml` never requested `vhd-input`/`vhdx-input` from its `qcow2` dependency, so the host attached a differencing parent to a binary with no VHD parser; `read_chain_cluster`'s own format allowlist, separate from the crate features, still refused VHD and VHDX once that was fixed; and `dummy_buf` pointed at `CHAIN_CACHES`, the first chain device's own L1/BAT cache slot. That last was documented as safe because `compressed_buf` is never touched in this build -- true for qcow2 and raw, false the moment a VHD chain member exists, because a differencing VHD chunk's mixed-ownership arm uses it as a sub-sector bounce buffer, so the read clobbered the cached BAT sector mid-lookup. It was unreachable before this phase and the first two fixes armed it. A carve of their own replaces it (`CHAIN_READ_COMPRESSED`): review round 3 noted the first fix had handed the same pointer to both `compressed_buf` and `staging_buf`, which was safe only by the order in which the `qcow2` crate happens to touch them, so the two now have separate addresses that alias nothing. The refusal call site was added. `bench --output json` has no raw byte-count field -- `count` and `buffer-size` echo the arguments rather than measuring -- so the byte-count assertion the plan asked for -- whose point was that a composed read silently serving zeros would otherwise pass -- does not exist, and position probes at each fixture's validated parent-owned and child-owned sectors stand in for it. Built as `c46eef81`. |
| 14h | medium | sonnet | none | Integration-test the boundary this phase draws. Add tests asserting that `map`, `measure` and `check` *still* refuse a differencing source, with their own refusal messages, and that each message satisfies decision 8 -- names its operation and does not claim instar cannot compose. This is the test that stops phase 15 silently inheriting a half-lifted tree, and the test that would have caught `rebase` being missing from the master plan's list. Put them in `tests/test_differencing.py` beside the composition tests, so the two halves of the policy are read together. Also add a case per operation to `tools/mutate-differencing.sh` that re-asserts the refusal, so a future phase cannot lift one of the three by accident. **Execution note.** The tests assert decision 8's two properties -- the message names its own operation, and never reads as a claim about every operation -- rather than pinning `map`'s sentence verbatim, because 14i rewords it; the verbatim pin stays in the older test. `check` is asserted both with and without `--chain`, since it walks a chain only when asked and a gate that differed under the flag would otherwise be invisible. No production change. Built as `416046bd`. |
| 14i | low | sonnet | none | Documentation and changelog. `CHANGELOG.md`: the five operations that now compose a differencing VHD or VHDX, the three that still refuse, and the three issues closed. `docs/convert.md`, `docs/compare.md`, `docs/bench.md`, `docs/rebase.md` and `docs/dd.md` each gain or lose a differencing limitation -- check what each currently claims rather than assuming, since phase 4 wrote those limitations and some are now false. `docs/map.md:238`'s VHDX partial-present entry and `docs/measure.md` stay as they are and should be checked to confirm they still read as a limitation rather than a plan. `docs/chain-discovery.md` describes the host walk and now needs the per-call capability (decision 5). Do not update `ARCHITECTURE.md` -- no component or data path changes -- and do not update `AGENTS.md` unless 14b's `ChainConfig` version bump creates a convention an agent could not infer, in which case one line about the version is the whole change. **Execution notes.** Nine user-visible plan citations, not the eight the Definition of done counted: the `create --sector-size` message spans a non-quoted continuation line that the criterion's own grep misses. Four files beyond the named list -- `docs/create.md`, `docs/guest-architecture.md`, `docs/format-coverage.md` and `docs/info.md` -- asserted that nothing in instar composes a differencing source, which is false for five operations, and were corrected. `docs/quirks.md`'s claim that `info --chain` reports a one-image chain was already false before this phase began, which is drift from phase 11 or 13 rather than anything 14a to 14h did. `AGENTS.md` was left alone: the `ChainConfig` version bump uses an already-documented versioned-struct pattern and is not a new convention. Built as `b0ba64b8`. |

## Risks and mitigations

* **14a changes bytes instar already writes.** Fixing the BAT
  sizing changes the output of `create -f vhdx -b` and the size
  `measure` predicts for it. A round-trip or golden expectation
  that encodes the old size will fail, and the failure will look
  like a regression. *Mitigation*: 14a's brief requires finding
  them by running the suites, and the management session reviews
  the list of changed expectations as a list -- each one must be
  explained as "the old number was the bug", not adjusted to
  match.

* **14b is a guest/host ABI change.** A version-3 `ChainConfig`
  read by a guest binary built against version 2, or the reverse,
  is a silent misparse rather than an error. *Mitigation*: the
  version field exists and `is_valid` already checks magic;
  14b must make the guest refuse a version it does not know,
  and a test must assert the refusal. The management session
  checks that both halves of the tree are rebuilt before any
  integration test is believed -- a stale `instar-release` image
  has produced a false result in this project before.

* **A half-lifted tree is a new user-visible inconsistency.**
  Five operations compose, three refuse, and a user will hit both
  in one session. *Mitigation*: decision 8 and step 14h, which
  tests the refusal messages rather than only the refusals.

* **The lift's correctness is invisible to a test that uses one
  chain.** The defect #614 describes -- composing a child against
  an unrelated image -- produces plausible output. *Mitigation*:
  14f's two-chain cases are the specific test, and the
  `tools/mutate-differencing.sh` entries from 14b and 14f must
  kill a mutation that reverts the segmentation. The harness
  verdict, not the suite passing, is the evidence.

* **Phase 15's scope is now defined by this phase's omission.**
  If 14h's boundary tests are weak, phase 15 inherits an unclear
  starting point. *Mitigation*: 14h is a step rather than a line
  in 14i's brief, precisely so it is reviewed on its own.

## Definition of done

* `instar convert` writes byte-identical output for a differencing
  VHD chain and for the same data flattened into a single image.
  Both outputs are in 14e's commit message, with the command that
  produced them. Same for VHDX.
* `instar compare` of a differencing child against its own
  correctly flattened output reports identical, and against a
  deliberately different image reports a difference at the
  expected offset. Both assertions are in `tests/test_differencing.py`.
* `instar map`, `instar measure` and `instar check` each still
  refuse a differencing source, and each refusal message names its
  own operation. Falsifiable: a test asserts the message text, not
  just the exit code.
* No user-visible string contains `PLAN-`. Falsifiable:
  `grep -rn 'PLAN-[a-z0-9-]*\.md' --include=*.rs src/ | sed 's/^[^:]*:[0-9]*://' | grep -v '^[[:space:]]*//'`
  returns nothing from `format!`, `println!`, `eprintln!` or a
  `&str` constant. **The command is corrected here.** As first
  written it piped straight into `grep -v '^\s*//'`, and `grep -rn`
  prefixes every line with `path:lineno:`, so the filter could never
  match and the criterion could never pass -- it would have reported
  every comment in the tree as a violation. Phase 15's completion
  check found it; stripping the prefix first, the criterion passes
  with zero hits. **14e found this is eight strings, not the one
  F6 scoped it to**: the differencing read refusal (removed in
  14e), `map`'s refusal citing `PLAN-map.md`
  (`src/vmm/src/main.rs:16138`), `create`'s differencing-backing
  refusal citing `PLAN-differencing.md` (`:18443`), and five
  `PLAN-create.md` / `PLAN-convert-followups.md` citations between
  `:14799` and `:18926`. The criterion stands as written and 14i
  clears all eight -- these are error messages a user reads,
  pointing at a directory of internal planning documents, and
  leaving seven in place while claiming the criterion is met would
  be false. Rewording a refusal's text is not the same as changing
  what it refuses: `create`'s refusal is additionally *wrong* now,
  which is issue #628 and out of scope.
* `vhdx::calculate_bat_layout` cannot be called without stating
  `has_parent`. Falsifiable: it takes four parameters, and
  `grep -c 'calculate_bat_layout(' src/` finds the same five
  consumers it finds today.
* A differencing VHDX written by `instar create -f vhdx -b` and
  read back by `instar convert` resolves every sector bitmap --
  that is, the phase 13 `sb_bat_entry_bound` cap no longer
  truncates instar's own output. Falsifiable: the geometry and the
  before/after entry counts are in 14a's commit message.
* `init_chain_states` refuses a differencing child that has no
  device behind it *in its own chain*, and admits one that does,
  with a two-chain array distinguishing them. Both are asserted by
  tests, and a mutation reverting the segmentation kills one of
  them.
* `tools/mutate-differencing.sh` case count has grown, the new
  count is derived by `check_case_count` rather than written in
  prose, and `docs/testing.md` agrees with it. Every new case is
  PASS or a documented SURVIVOR; no BROKEN.
* `make test-rust` passes with zero failures and the count is
  stated, against the 2427 phase 13 measured.
* `pre-commit run --all-files` is clean.
* No source file or comment added by this phase cites a plan
  phase, step or decision number. Falsifiable:
  `git diff d677823c..HEAD -- 'src/*' | grep '^+' | grep -iE 'PLAN-[a-z0-9-]+\.md|decision [0-9]|phase 1[0-9]|14[a-i]'`
  is empty.
* `CHANGELOG.md` names which operations compose and which refuse.

## Back brief

Before implementation starts, confirm:

1. **The step order and the gate placement.** 14a to 14c settle
   three issues before 14d touches a policy and 14e deletes a
   guard. Nothing user-visible changes until 14e.

2. **A gate on 14b's wire format, before any code is written.**
   The `ChainConfig` version-3 field shape is cheap to propose and
   expensive to redo: it is read by five guest operations and
   written by four host paths, and getting the shape wrong means
   redoing 14b, 14e, 14f and 14g. The implementer of 14b must
   bring the exact struct definition -- field names, types,
   offsets, and what the guest asserts about them -- to the
   management session before editing. Decision 4 settles that the
   segmentation goes in `ChainConfig`; it does not settle whether
   that is a per-device chain id, a per-chain `(first, count)`
   table, or a `chain_start` on each `ChainDeviceInfo`.

3. **That the two gates really are gates.** If 14a or 14b turns
   out to be larger than planned -- 14a's five consumers are the
   likeliest source of that -- the phase stops and reports rather
   than lifting a refusal onto an unsettled gate. The master plan
   is explicit that lifting before #623 ships a reader that cannot
   read instar's own output.

4. **Model and effort.** 14a, 14b, 14d, 14e and 14f are opus at
   high effort: each decides a policy or a wire format rather than
   implementing one. 14c, 14g and 14h are sonnet at medium with
   the briefs above. 14i is low. No step wants `fable`; none of
   them has defeated opus.

5. **The standing warning.** This repository's history is that
   agents assert plausible-but-wrong format and tool
   capabilities. Every claim about what a VHDX BAT contains at a
   given geometry must be measured against a built image, not
   reasoned about -- phase 13 found its largest defect (`ec005d57`)
   exactly where the arithmetic looked obviously right.

## Review round 1

The automated review of #630 raised nine items: two `fix`, one
`document`, five `consider` and one informational. Eight were taken.
Two are worth recording here because they say something the plan got
wrong rather than something the code did:

* **14c's "not a behaviour change" was false.** Corrected in the 14c
  row above and in `CHANGELOG.md`, which now carries a *Changed* entry
  for it. A crate test pins the refusal on a `has_parent == false`
  image so the plain dynamic path cannot lose it unnoticed, and a
  mutation case re-gates the check on `has_parent` to prove that test
  is the one holding it.

* **Nothing exercised `rebase`'s two-segment chain config.** Every
  rebase test in 14g and 14h detaches (`-b ''`), which produces one
  `ChainSegment`; the multi-segment emission that #614's fix exists to
  make safe in `rebase` had no coverage at all. Found while answering
  the review's note that no test puts a differencing image in a *new*
  backing chain, which is the construction that reaches it.
  `test_rebase_onto_a_new_backing_keeps_the_two_chains_apart` now
  does, and a mutation case drops the second chain's segment to prove
  it. The host-side device-count agreement check, which the same round
  turned from a `debug_assert_eq!` into a real error, is what that
  mutation trips.

  Writing that test also turned up issue #632, which has nothing to do
  with differencing: a safe-mode rebase onto a *raw* new backing always
  fails as `the overlay's header could not be parsed`, whatever the old
  chain holds, while the same rebase onto a qcow2 backing succeeds and
  `-u` with the raw backing succeeds. `raw` is an advertised value for
  `-F` that no test exercises. The test uses a qcow2 new backing and
  says why.

Declined, with the reason:

* **Plan citations in `docs/quirks.md`.** The remaining ones quote
  error-message text verbatim inside dated records of what instar did
  at the time. Rewriting them would falsify the record; this phase
  already reframed the surrounding prose into the past tense and added
  the current wording beside it. The phase citations in released
  `CHANGELOG.md` sections are history and out of scope. Separately
  verified: no user-visible message contains `PLAN-` at all, so what
  was stale was documentation quoting a message that no longer exists.

* **A CLI-level differencing-over-differencing chain.** Both formats
  have crate-level coverage of two consecutive composition levels
  (`src/crates/qcow2/src/lib.rs:7996` and `:9853`), but nothing drives
  it through the binary, and nothing can until the fixture exists:
  `qemu-img` cannot author a third differencing level in either
  format. Filed as issue #631 with what the fixture needs.

## Review round 2

Eight items: two `fix`, one `document`, four `consider` and one
informational. Seven taken, one declined on evidence. Both `fix` items
were stale documentation this phase's own changes created -- two test
docstrings describing `map`'s message as still due to be reworded when
this phase reworded it, and a sixth cell on the 14g row of a
five-column table.

Three findings are worth recording:

* **The overlap documentation claimed more than the scan enforces.**
  `VhdxState::init` reads `entry_count.min(8)` region table entries
  while SPEC(VHDX) permits 2047, so "any entry in the region table" was
  true only of the first eight. The docs now say so. The cap is sound
  rather than merely documented: the same scan is what sets `found_bat`
  and `found_metadata`, and `init` returns `None` when either is
  missing, so an image cannot hide the BAT or the metadata region
  behind it -- only an unrecognised region, whose bytes nothing reads
  as structure. `vhdx_overlap_check_covers_the_eighth_region_table_entry`
  pins the eighth entry being checked, with a control proving the
  refusal is that entry's range and not the presence of six more
  entries.

* **The trailing-region test had no mutation case.** It is the one test
  distinguishing an overlap test from a high-water mark -- the
  regression 14c's fix was shaped to avoid -- and nothing proved it
  could fail. `vhdx-read-overlap-replaced-by-a-high-water-mark` now
  does, and it is surgical: because the fixture's payload blocks sit
  immediately above the BAT region's end, the mark only rises past them
  when a region is declared *after* the blocks, so every "inside a
  region" case still passes under it and only the trailing-region test
  fails. Found by the round's own instruction to mutate rather than
  read the guard, not by the review.

* **`bench` and `check` have no chain-depth guard** (issue #633).
  Chased from the review's claim that `compare` lacked a host-side
  agreement check between its `CompareConfig` counts and its written
  device slots. `compare` does not: `:12826` refuses the combined depth
  before KVM is opened, and `total_devices()` counts exactly the slots
  the entry writer emits, so the two cannot disagree. Sweeping the
  class found `bench` and `check` are the only chain-attaching
  operations with no such guard, where an over-long chain is silently
  truncated and a 17th device's virtqueue memory lands on
  `DMA_POOL_BASE`. Pre-existing and unrelated to differencing, so
  filed rather than fixed here.

Declined, with the reason:

* **A host-side count agreement check in `run_compare`.** Declined
  because the premise does not hold, as above. The review's first
  suggestion -- deriving `CompareConfig`'s counts from the written
  slots -- would be a regression rather than a tightening: it would
  replace a refusal naming both chain depths with a silently truncated
  comparison, which is the one answer `compare` must never give. A
  comment at the config write now names the guard that makes the two
  sources agree, since the guard is 90 lines above and a reader at the
  write cannot see it.

## Review round 3

Six items: one `fix`, one `document`, four `consider`. Five taken, one
declined and filed. The round's shape changed: where rounds 1 and 2
found claims the plan or the changelog got wrong, this one found two
genuine defects in code the earlier rounds had added.

* **The overlap check never reached `map` or `measure`** (issue #634).
  `CHANGELOG.md` said it reached "`convert`, `dd`, `compare`, `bench`
  and `map`", which was wrong twice: `map` walks the BAT through
  `classify_vhdx_bat_entry` in `map_extents` and applies no overlap
  test, and `rebase` -- which does reach it -- was missing from the
  list. `measure` is in the same position as `map` via
  `scan_allocation`. There are exactly three callers of
  `block_overlaps_a_declared_region`, all inside `block_lookup` and
  `sector_bitmap_lookup`, so the reach is enumerable rather than
  argued. Both documents now say which operations apply the rule and
  that two do not, and the divergence -- `convert` refusing an image
  `map` still reports as data -- is filed rather than closed here,
  because changing `map`'s output for a malformed image is a
  behaviour change to an operation this phase deliberately leaves
  non-composing. Phase 15 revisits all three readers.

* **`ranges_overlap` refused on an empty region, inconsistently.**
  With `region_len == 0` the predicate reduced to `offset <
  region_offset < end`: a zero-length entry at or below a block's
  start was waved through, one whose offset fell strictly inside the
  block refused it. A region naming no bytes can hide nothing, so the
  refusal bought no safety, and `init` does not reject a zero-length
  entry, so a hostile image can reach it. Empty ranges are now
  answered before either sum is formed. This also falsified a comment
  round 2 added, which claimed a zero-length region "intersects
  nothing" -- true only of the offset-0 entries that comment's own
  fixture carried.

* **The two chain-read bounce buffers shared one address.** Round 1
  moved them off `CHAIN_CACHES`, which was the real defect, but
  handed `read_chain_virtual_cluster` the same pointer for
  `compressed_buf` and `staging_buf`. Those are separate parameters
  with separate lifetimes; sharing was safe only because no arm of
  the chain reader uses both at once, which is a property of call
  ordering inside the `qcow2` crate and invisible from `rebase`. They
  have their own carve now (`CHAIN_READ_COMPRESSED`,
  `CHAIN_READ_STAGING`), above every other carve, aliasing nothing --
  not each other, not a device's cache, not the DMG chunk-table
  scratch. They do not both fit inside `PLANNER_SCRATCH_LIMIT`, which
  is 64 KiB short of `COMPRESSED_BUF_SIZE + MAX_CLUSTER_SIZE`, so
  reusing that carve was not an option. Two static asserts hold the
  new carve below the allocator heap and above the carve it follows.

  No mutation case guards the separation, and the attempt to add one
  is the reason why: a case pointing `staging_buf` back at
  `compressed_buf` was written, run, and came back FAIL -- the
  differencing rebase suite still passed against it. That is the same
  statement the review made, measured rather than argued: no current
  behaviour distinguishes the two layouts, which is exactly why this
  was a latent hazard and not a bug. The case was dropped rather than
  shipped as a permanent FAIL, since the harness has no survivor
  variant for integration cases and building one to hold a single
  comment is not worth the machinery. What holds the separation is
  the two constants and the static asserts, checked at compile time;
  the carve's doc comment records that a mutation survives it, so the
  next reader does not collapse them back on the grounds that nothing
  fails. The one case this round did add -- the empty-region answer --
  takes the harness from 90 to 91.

* **The composing refusal said "source".** For `rebase` the
  differencing image is never the source -- the overlay must be qcow2
  or vmdk -- so the sentence was wrong every time `rebase` rendered
  it, and wrong for a `convert` or `compare` of a qcow2 overlay whose
  backing is a parentless differencing image. It now says "a
  differencing {fmt} image in the chain {op} was given has no parent
  behind it", which holds whether the differencing image is the
  source or a backing member. The non-composing arm keeps "source",
  and that is not an oversight: `validate_chain` never runs the
  VHD/VHDX validators that hold `refuse_differencing`, so `map`,
  `measure` and `check` only ever refuse the image the user named.

Declined, with the reason:

* **An integration fixture for a parentless differencing VHDX**
  (issue #635). The gap is real -- the narrowed refusal is only driven
  end to end for VHD -- but it is a fixture gap, not a test gap.
  `qemu-img` will not create a differencing VHDX, `instar create -b`
  refuses a differencing backing file, and copying `vhdx-diff-child`
  without its parent exercises the unresolvable-parent path instead.
  It needs a hand-built image in `instar-testdata`, the same family as
  #631, and the two are blocked on the same thing.
