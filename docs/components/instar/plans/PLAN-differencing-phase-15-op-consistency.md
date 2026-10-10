# Differencing phase 15: consistency for the non-composing operations

## Prompt

Plan phase 15 of `PLAN-differencing.md`. Phase 14 made `convert`,
`dd`, `compare`, `bench` and `rebase` compose a differencing VHD or
VHDX against its parent. `map`, `measure` and `check` do not, and
this phase is about making that boundary correct and defensible
rather than about moving it.

Three things changed under these operations when phase 14 landed and
none of them were addressed by it. The composing readers now refuse
a VHDX payload block whose byte range overlaps a region the image
declares (#625); `map` and `measure` do not apply that test, so the
three operations disagree about what a valid block is (#634).
`check` and `bench` attach a backing chain without the
`MAX_CHAIN_DEVICES` guard every other attaching operation has
(#633). And the comments justifying the `check` and `measure`
refusals say instar cannot read a differencing image, which was true
when phase 4 wrote them and is false now.

The phase plan is the deliverable; implementation is a separate ask.

## Planning effort

**High.** Not because the diff is large -- it is the smallest phase
of the composition arc -- but because the scope was wrong in the
master plan and the survey had to settle it, and because the central
decision is a refusal this phase deliberately does *not* lift. A
phase whose main output is "these three stay refused, and here is
why that is still right" has to be argued rather than asserted, or
phase 17's documentation has nothing to stand on.

Review effort: **medium**. The master plan does not specify one.
The code is small and local; the judgement is in this document.

## Scope

**In scope.**

* Issue #634: `VhdxState::map_extents` and `VhdxState::scan_allocation`
  do not apply the block/region overlap test that
  `VhdxState::block_lookup` and `VhdxState::sector_bitmap_lookup`
  apply, so `map` reports as data a block that `convert` refuses to
  read.
* The third answer in the same family: `check` has its own VHDX
  region and BAT validation, and whether it is stricter than, laxer
  than, or merely different from the readers has not been measured.
* Issue #633: `check` and `bench` reach `vmm_config_chain` with an
  uncapped device count, where `convert`, `dd`, `commit`, `rebase`
  and `compare` all refuse an over-deep chain up front.
* The refusal justifications phase 14 falsified, in code comments
  and in `docs/`.
* A test for the `commit` decision phase 14 made and did not test.
* `CHANGELOG.md` and the user documentation for the three
  non-composing operations.

**Out of scope, with the reasons stated.**

* **Chain composition in `map`** -- issue #641. `map` refuses any
  backing pointer, for every format, and has done since `eb6e23f`
  (2026-06-03), which predates this plan. `qemu-img map` composes
  and emits a per-extent `depth`; instar's protobuf reserves the
  field and documents it as always `0`. Closing that is a format
  agnostic parity feature, not differencing work, and it needs
  multi-device plumbing in a guest op that holds no `ChainConfig`.
* **Chain composition in `measure`** -- issue #642. Same shape.
  `measure` already answers a qcow2 chain with the top layer's
  allocations alone, which `docs/measure.md:139` documents as a
  divergence; the plumbing it needs is identical whichever format
  the chain is in.
* Both were deferred by plans that are now `Status: Complete`
  (`PLAN-map.md`, `PLAN-measure.md`), so the follow-up phases they
  point at do not exist. The issues are the record, and whichever
  plan picks them up should take them together.
* **Lifting the `check` or `measure` differencing refusal.**
  Decision 2 below, and issue #643.
* **Fixtures** -- #631 (no CLI differencing-over-differencing
  coverage) and #635 (no parentless differencing VHDX) are phase
  16's, which is the tests and fuzz phase.
* #628 (`create -b` refuses a differencing backing for a reason that
  is no longer right) and #632 (safe-mode rebase onto a raw backing)
  are unrelated to this boundary.

## What the survey found

The master plan's phase 15 section was written before phases 11 to
14 executed. Two of its four claims hold, two do not, and it is
missing the thing that actually makes this phase necessary. All
references below are against `7fef1321`.

**Holds.** "`map` and `measure` have no chain notion at all."
Neither `run_map` (`src/vmm/src/main.rs:15756`) nor `run_measure`
(`:15161`) calls `discover_backing_chain`, and neither guest op
mentions `ChainConfig` -- the five that do are `bench`, `check`,
`compare`, `convert` and `rebase`.

**Holds.** "`check`'s `validate_chain` walks a chain's members to
validate each independently rather than to compose a read."
`src/operations/check/src/main.rs:3281` reads sector 0 of each
member, cross-checks the detected format against the chain metadata
and the member's virtual size against zero, and bounds-checks a
qcow2 header. Nothing composes.

**Wrong, and already answered.** "`commit` is a composing caller on
the host (`src/vmm/src/main.rs:7265`) ... phase 15 should decide
whether that is correct or merely untested." The line reference has
drifted -- `:7265` is now inside `run_rebase` -- and phase 14 decided
it. `run_commit` passes `DifferencingComposition::Unsupported`
(`:7598`) under a comment saying the guest ignores the ancestor
slots the host populates, so resolving a differencing parent could
only turn a working command into a path error. It is correct **and**
untested, which is the half of the master plan's question that
survives.

**Wrong.** "`map`'s own chain composition is additionally deferred to
`PLAN-map.md` ..., so phase 15 must settle which plan owns it before
it starts." Neither candidate owner is open: `PLAN-map.md` and
`PLAN-measure.md` are both `Status: Complete`, each carrying this
work in *Future work* and each pointing at a "follow-up phase" that
was never created. The choice was never between two plans; it was
between this phase and nothing. Settled by decision 1.

**Missing from the master plan entirely, and the reason this phase
exists.** Phase 14 tightened what the composing readers accept and
left the other three behind:

* `block_overlaps_a_declared_region`
  (`src/crates/vhdx/src/lib.rs:2315`) has exactly three callers --
  `block_lookup`'s two payload arms (`:2384`, `:2419`) and
  `sector_bitmap_lookup` (`:2535`). `map_extents` (`:2794`) walks
  the BAT with `classify_vhdx_bat_entry` (`:2881`) and
  `scan_allocation` (`:2635`) counts with
  `count_allocated_in_bat_chunk`. Neither consults it.
* The two gaps are not the same gap. `map` emits a `file_offset`
  for a Data extent, so a block declared inside the metadata region
  is reported to the user as a real location. `scan_allocation`
  never looks at a file offset at all -- it counts BAT entry states
  -- so `measure`'s wrong answer is a size that counts a block the
  reader would refuse, not a wrong location.
* `check` is a third answer again, with its own region table
  cross-validation and overlap detection
  (`src/operations/check/src/main.rs:14-18`) reached only under
  `check`. Whether it catches what the readers refuse is **not
  established** -- 15b measures it rather than assuming.

**Also missing: a measured parity baseline.** The master plan asserts
nothing about what qemu-img does for these three. Measured against
qemu-img 10.0.13 (Debian `1:10.0.13+ds-0+deb13u1`) on a plain qcow2
chain, base holding 1 MiB at offset 0 and child holding 1 MiB at
offset 4 MiB:

| Tool | Behaviour with a backing chain |
|---|---|
| `qemu-img map` | composes; `depth: 1` for base-resolved extents, `depth: 0` for the child's own |
| `qemu-img measure -O qcow2` | composes; child measures 2424832, identical to its own flattening, against 1376256 for its data alone |
| `qemu-img check` | does **not** walk the chain; reports on the top image only |

So `check` needs no composition to reach parity, and `map` and
`measure` need composition that has nothing to do with differencing.
That asymmetry is what split this phase from #641 and #642.

**Corrected at source.** The master plan's phase 15 bullet and the
`index.md` row have been rewritten in this phase's first commit to
say what the survey found, so the next reader does not re-derive it.
Phase 14's Definition of done also carried an unfalsifiable
criterion -- its grep is `grep -rn ... | grep -v '^\s*//'`, and
`grep -rn` prefixes `path:lineno:` so the filter can never match --
corrected in the same commit. The criterion passes once the prefix
is stripped: zero non-comment `PLAN-*.md` references in `src/`.

## Decisions

1. **Chain composition for `map` and `measure` leaves this plan,
   as issues #641 and #642, rather than becoming phase 15.** The
   master plan's success criterion for these operations is that
   "no instar op silently composes a differencing image as though it
   had no parent ... applied uniformly across `info`, `check`,
   `convert`, `compare`, `dd`, `bench`, `map` and `measure`", and it
   explicitly records that `map` already refused before phase 4 and
   is "the precedent phase 4 generalises rather than outstanding
   work". Uniform non-silent handling is met. Composition in these
   two is a qcow2-chain parity feature that two Complete plans
   deferred, and building it for the differencing arms alone would
   construct the plumbing and then decline to use it where qemu-img
   already diverges.

2. **The `check` and `measure` differencing refusals stay.** This is
   the decision most likely to be argued with, because the stated
   reason for one of them is now false. `check`'s VHD arm
   (`src/operations/check/src/main.rs:1996`) justifies itself with
   "walking it and reporting 'no errors' ... tells a user an image
   instar cannot read is fine", and instar can read it now. The
   refusal nevertheless survives its own justification: `check`
   validates one image's structure, and a differencing child's
   structure is almost always intact while the image as a whole is
   unusable without its parent. Reporting "No errors were found" and
   a child-only allocation percentage is precisely the answer phase
   4 existed to prevent -- a complete-looking verdict on a partial
   view. `qemu-img check` gives exactly that answer and is wrong to.
   So the comment gets corrected to the reason that is still true,
   not deleted, and the lift question is filed as #643 with both
   sides written down rather than settled here by a phase whose
   scope is consistency.

3. **`map` and `measure` refuse the malformed image rather than
   skipping the block.** `map_extents` and `scan_allocation` both
   return `Option`, so `None` is the existing failure channel and no
   new control flow is needed. Refusing matches what the readers do
   with the identical predicate, and matches phase 14's rule that
   the VHDX arms fail closed rather than guess. Emitting the block
   as unallocated would be the clamping phase 14 ruled out, and
   would make `map` claim an image is sparse where it is actually
   malformed.

4. **Each of the two gets a new error code rather than reusing an
   existing one.** `MAP_RESULT_ERROR_INVALID_SOURCE` renders as
   "map: source format unrecognised" and
   `MEASURE_RESULT_ERROR_INVALID_SIZE` as "source image is
   unsupported format". Both would be false: the format is
   recognised and the image parsed far enough to walk its BAT. A
   user shown "unrecognised" will go looking for a format problem
   that is not there.

5. **#633 is fixed for `bench` as well as `check`, in one step.**
   `bench` is not this phase's operation, but it is the same missing
   guard in the same shape and the issue covers both. Fixing one and
   leaving the other would close nothing and leave the next reader
   to rediscover the asymmetry. The repository convention is to
   sweep the class.

6. **The half-lifted tree is this plan's end state, stated
   deliberately.** Five operations compose a differencing source and
   three decline it. Phase 14's risk register called that "a new
   user-visible inconsistency", with testing the refusal messages as
   the mitigation. This phase makes it a documented boundary with a
   reason per operation, and phase 17 presents it as a boundary
   rather than a gap. Decisions 1 and 2 are what make it defensible;
   if either is reversed the boundary moves and phase 17's framing
   changes with it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 15a | high | opus | none | Close the `map` and `measure` half of issue #634. `VhdxState::block_lookup` (`src/crates/vhdx/src/lib.rs:2384`, `:2419`) and `VhdxState::sector_bitmap_lookup` (`:2535`) refuse a block whose byte range overlaps a region the image's region table declares, via `block_overlaps_a_declared_region` (`:2315`). `VhdxState::map_extents` (`:2794`) and `VhdxState::scan_allocation` (`:2635`) do not, so `map` reports as data a block `convert` refuses to read. Apply the same predicate in both, refusing rather than skipping (decision 3): `map_extents` classifies each entry with `classify_vhdx_bat_entry` at `:2881` and must test the offset of any entry it is about to emit with a `file_offset`; `scan_allocation` counts through `count_allocated_in_bat_chunk` and never forms a file offset at all, so it needs the offset extracted for each present block before it is counted. Return `None` -- both functions already use it as their failure channel. Add one new error code to each operation rather than reusing an existing one (decision 4): the host's constants are at `src/vmm/src/main.rs:169-172` for `map` and `:148-154` for `measure`, rendered by `map_error_message` (`:16165`) and the match inside `run_measure` (`:15735`). Word both messages for a recognised image with a malformed block table, not an unrecognised format. Crate tests for a payload block overlapping the metadata region, one overlapping the BAT region, and a positive control immediately past the last declared region, mirroring the ones phase 14 added for the reader arms; plus `tools/mutate-differencing.sh` cases reverting each of the two new call sites. Do not touch the zero-offset guard (`MIN_BLOCK_FILE_OFFSET`) -- it is a separate, independently killable check and phase 14 deliberately kept it so. |
| 15b | high | opus | none | Settle the third answer. `check` has its own VHDX region table 1+2 cross-validation, metadata parsing, BAT walk and overlap detection (`src/operations/check/src/main.rs:14-18`), reached only under `check` and using a scratch bitmap (`BitmapContext::init_in_scratch`, see `:749` and the region marking at `:756`) rather than the reader's predicate. Measure, do not assume: build a VHDX whose BAT names a payload block overlapping the metadata region -- the phase 14 crate tests in `src/crates/qcow2/src/lib.rs` show how to patch one in place, and `VhdxState::init` deliberately skips region-table CRC validation so an entry can be edited without a checksum fixup, though `check` validates the CRC so this fixture needs it recomputed -- and record what each of `convert`, `map`, `measure` and `check` says about it, before and after 15a. If `check` already flags it, the deliverable is the measurement plus a test pinning it, and say so plainly. If `check` misses it, make it a counted corruption rather than a refusal: an image whose BAT points into its own metadata is malformed, which is exactly what `check` exists to report, and exit 2 is the right answer where the readers' exit 1 is not. State the four-way table in the commit message -- it is the evidence for phase 17's claim that the operations now agree. |
| 15c | medium | sonnet | none | Fix issue #633. `bench` (`src/vmm/src/main.rs:5696`) and `check` (`:12316`) reach `vmm_config_chain(sector_size, chain.total_devices())` with an uncapped device count; every other attaching operation refuses an over-deep chain first -- `convert`/`dd` at `:13679`, `commit` at `:7613`, `rebase` at `:7255`, `compare` at `:12826`. Note the issue's line numbers for the two call sites have drifted by four and five lines since it was filed; the numbers here are against `7fef1321`. Add the same up-front guard to both (decision 5), wording each message in that operation's own voice as the existing four do, and refusing before KVM is opened rather than after. The downstream disagreement the issue describes is the reason this matters: `write_chain_device_entries` (`:3449`) stops at `MAX_CHAIN_DEVICES` while the count handed to `vmm_config_chain` does not, so the guest is told about more devices than `ChainConfig` describes. Test both operations with a chain of 17 at the CLI level and assert the message, not just a non-zero exit. `src/shared/src/lib.rs:665` asserts `VQ_BASE_START + 16 * 0x10000 <= DMA_POOL_BASE`, so a 17th device's virtqueue lands on `DMA_POOL_BASE` -- say in the commit message whether you reproduced that before the fix, because "the guard was missing" and "the guard was missing and it corrupted memory" are different changelog entries. |
| 15d | medium | sonnet | none | Correct the justifications phase 14 falsified, without changing what any of them do (decision 2). Three comments assert instar cannot read a differencing image: `src/operations/check/src/main.rs:1981-1995` (the VHD arm's "tells a user an image instar cannot read is fine"), the VHDX arm above it at `:1591`, and `src/operations/measure/src/main.rs` around the two `refuse_differencing` call sites at `:422` and `:441`. Five operations read one now. Replace the premise with the one that survives -- `check` and `measure` report on one image, and a differencing child's own structure is intact while the image is unusable without its parent, so a clean verdict or an allocation figure would describe a partial view as though it were whole. Do the same sweep over `docs/`: grep for claims that instar cannot compose a differencing source, flattening whitespace first because these sentences wrap mid-phrase and plain grep misses them -- phase 14 found four files beyond the ones its plan named that way. Do not reword any user-visible refusal message: phase 14 settled that text, `tests/test_differencing.py` pins it, and this step is about reasons rather than behaviour. No production behaviour changes; the proof is the existing differencing suite still passing unmodified. |
| 15e | medium | sonnet | none | Test the `commit` decision phase 14 made and did not test. `run_commit` (`src/vmm/src/main.rs:7598`) passes `DifferencingComposition::Unsupported` to `discover_backing_chain` because the commit guest op reads the overlay and the backing and ignores the ancestor slots the host fills in. `commit` refuses any overlay that is not qcow2 or vmdk (`:7418`, "format '{other}' does not support commit"), so a differencing VHD or VHDX can only ever appear further back in the backing's own ancestor chain. Build that: a qcow2 overlay over a qcow2 backing whose own parent is a real differencing VHD from `instar-testdata`, commit the overlay, and assert both that it succeeds and that the committed contents are right -- an assertion on the exit code alone would pass if the ancestor had been resolved and silently composed. Add a second case with the differencing ancestor's own parent moved aside, which must still succeed: that is the specific thing decision-by-`Unsupported` buys, and a regression to `Supported` would turn it into a path error. Put both in `tests/test_differencing.py` beside `TestDifferencingNonComposingRefusalPolicy`, and add a `tools/mutate-differencing.sh` case flipping `:7598` to `Supported` so the decision is held by something that fails. |
| 15f | low | sonnet | none | Documentation and changelog. `CHANGELOG.md`: the overlap test now reaching `map` and `measure` (a *Fixed* entry -- a malformed image these two used to describe is now refused), the `check`/`bench` depth guard, and whichever of 15b's outcomes happened. `docs/map.md`, `docs/measure.md` and `docs/check.md` each need the new refusal described beside their existing differencing section, and `docs/map.md:210` and `docs/measure.md:139` should be checked to confirm they still read as current limitations rather than as plans now that #641 and #642 carry the follow-up -- link the issues. `docs/quirks.md` carries the per-operation differencing table and must state the boundary as deliberate (decision 6): five compose, three report on one image, with the reason per operation. `docs/chain-config.md`'s Per-Operation Usage table gains nothing unless 15c changes a device count. Do not touch `ARCHITECTURE.md` -- no component or data path changes -- and do not touch `AGENTS.md`: nothing here is a convention an agent could not infer from the code. Check what each page currently claims rather than assuming; phase 14 rewrote several of these and a stale claim here would be two phases old. |

## Risks and mitigations

* **15a tightens a read path for every VHDX image, not only a
  differencing one.** `map_extents` and `scan_allocation` run
  whatever `has_parent` says, so an image that `map` described
  yesterday can be refused today. This is the same surprise phase
  14's review round 1 caught in 14c, where the row claimed "not a
  behaviour change" and was wrong. *Mitigation*: it goes in the
  changelog as a *Fixed* entry that names the behaviour change, a
  crate test pins the refusal on a `has_parent == false` image so
  the dynamic path cannot regress unnoticed, and the management
  session checks that no fixture in `instar-testdata` trips it
  before the step is accepted.

* **15b may find `check` is already right, and the step then looks
  like nothing happened.** *Mitigation*: the measurement is the
  deliverable, stated as a four-way table in the commit message. A
  step that establishes three operations already agree has earned
  its place, and phase 17 cannot claim agreement without it.

* **Decision 2 is the one a reviewer will push on.** Keeping a
  refusal whose written reason is false reads like inertia.
  *Mitigation*: the reason is replaced rather than patched, the
  counter-argument is filed as #643 rather than lost, and the
  success criterion it rests on is quoted in decision 1 rather than
  paraphrased.

* **A phase that mostly says "no" can quietly skip the work it does
  own.** The three code steps are small and the temptation is to
  fold them into the documentation step. *Mitigation*: 15a, 15b and
  15c each close or measure a numbered issue and each carry a test;
  none of them is complete because the docs describe it.

* **The repository's standing warning.** Agents in this project
  assert plausible-but-wrong format and tool capabilities. Every
  claim in 15b about what `check` validates must come from running
  it against a built image, not from reading the module header that
  lists what it is supposed to validate.

## Definition of done

* `instar map` and `instar measure` refuse a VHDX whose BAT names a
  payload block overlapping a declared region, with a message that
  does not say the format is unrecognised. Falsifiable: the same
  image is refused by `convert`, `map` and `measure`, and the three
  messages are in 15a's commit message.
* The positive control passes: a payload block immediately past the
  last declared region is still mapped and still measured. A
  high-water-mark implementation would fail this and an overlap test
  passes it.
* `grep -c 'block_overlaps_a_declared_region(' src/crates/vhdx/src/lib.rs`
  returns 6 -- one definition and five call sites, up from the three
  call sites today.
* A four-way table for one malformed image -- what `convert`, `map`,
  `measure` and `check` each say -- is in 15b's commit message, with
  the `check` row measured against a built binary.
* `instar check` and `instar bench` refuse a 17-image chain before
  opening KVM, each naming its own operation. Falsifiable: a test
  asserts the message text for both, and
  `grep -c 'exceeds the' src/vmm/src/main.rs` has grown by two.
* Nothing in `src/`, `tests/` or the top level of `docs/` claims that
  **instar** cannot read or compose a differencing image. Falsifiable
  by a script rather than a grep:
  `python3 tools/check-no-capability-claims.py`, exit 0.
  Its own docstring records why it is not a grep -- the sentences wrap
  mid-phrase, and a line-granular grep missed the one in
  `src/shared/src/lib.rs` reading "Instar cannot / compose a parent
  yet" across a line break. The script is verified by mutation, not
  only by passing: restoring that wrapped claim makes it exit 1 and
  name the line.

  **Widened during review.** As first written this criterion said "no
  comment in `src/`", and the script read only `//` comments there. The
  first review round asked whether that was enough; widening it to
  paragraphs across `src/`, `tests/` and `docs/` immediately found a
  live claim in a `tests/test_differencing.py` assertion message, which
  the comment-granular form could not see. The criterion is recorded in
  its widened form because the narrow one was satisfiable while the
  claim it exists to prevent was still in the tree.

  The criterion is about the **global** claim, not about any
  per-operation statement. "This operation does not compose a
  differencing chain" is true of `check`, `measure` and `commit` and
  must survive, as must a comment or test that *forbids* the global
  claim. An earlier draft of this criterion was a grep that matched
  both, and the first implementation attempt duly reworded a true,
  clear debug message into worse English to satisfy it. Code is not
  bent to fit a check; a check that flags true statements is the thing
  that is wrong.

  Not wired into `pre-commit` or CI. It is a tool the way
  `tools/mutate-differencing.sh` is, and a future contributor gets it
  only by running it.

* `commit` succeeds over a backing whose ancestor is a differencing
  VHD, and still succeeds when that differencing ancestor's own
  parent is absent. Both asserted in `tests/test_differencing.py`,
  and a mutation flipping `run_commit` to
  `DifferencingComposition::Supported` kills the second.
* `tools/mutate-differencing.sh` case count has grown, the count is
  derived by `check_case_count` rather than written in prose, and
  `docs/testing.md` agrees with it. Every new case is PASS or a
  documented SURVIVOR; no BROKEN.
* `make test-rust` passes with zero failures and the count is
  stated, against the 2450 phase 14 measured.
* `pre-commit run --all-files` is clean.
* Nothing this phase adds cites a plan file, phase, step or decision
  number, and no user-visible string contains `PLAN-`. The check spans
  `tests/` and `tools/` as well as `src/`: an earlier draft of this
  criterion covered only `src/`, and a test docstring added by 15e
  duly cited "phase 14" where the criterion could not see it.

  ```
  git diff 7fef1321..HEAD -- 'src/*' 'tests/*' 'tools/*' \
    | grep '^+' \
    | grep -iE 'PLAN-[a-z0-9-]+\.md|decision [0-9]|phase 1[0-9]|\b15[a-f]\b'
  ```

  The step-letter alternative needs its word boundaries: without
  them it matches `15e9` inside the BAT region GUID a 15b test
  spells out in hex.

  is empty. `docs/plans/` is deliberately out of scope -- plan files
  cite each other by design.

* `tests/test_differencing.py` cites no plan file, phase or decision
  number at all, not merely none added by this phase. Falsifiable:
  `grep -cE 'Decision [0-9]|decision [0-9]|PLAN-[a-z0-9-]+\.md|phase 1[0-9]|phase plan' tests/test_differencing.py`
  returns 0. Four such references predated this phase, one of which --
  "whether instar assembles a chain the way libvhdi does is phase 15's
  question" -- this phase's own rescoping made false, so correcting
  them is this phase's business rather than tidying.
* `docs/quirks.md` states the five-compose / three-report boundary
  with a reason per operation, and #641, #642 and #643 are linked
  from the pages that describe the limitation they would close.

## Back brief

Before implementation starts, confirm:

1. **Decision 2, before any code moves.** Everything else in this
   phase is small and reversible; keeping the `check` and `measure`
   refusals is the call that shapes phase 17 and closes out this
   plan's user-visible story. If it is wrong, it is wrong now, and
   15d and 15f both rest on it.

2. **Decision 1 and the two issues already filed.** #641 and #642
   are written as hand-offs, with the measured qemu-img baseline in
   them, on the assumption that a later plan takes both together.
   If phase 15 is meant to absorb either after all, say so before
   15a: the step plan is five steps smaller than it would otherwise
   be.

3. **Step order.** 15a and 15b are the same family and 15b's
   measurement is more useful after 15a has moved `map` and
   `measure`, so it reads the end state rather than a moving one.
   15c is independent and can run at any point. 15d must follow
   15b, since what `check` does is part of what its comment should
   say.

4. **Model and effort.** 15a and 15b are opus at high effort: one
   changes a predicate's reach across every VHDX read, the other
   has to measure rather than reason. 15c, 15d and 15e are sonnet at
   medium with the briefs above. 15f is low. No step wants `fable`.

5. **The standing warning.** Measure, do not reason, about what a
   VHDX BAT contains at a given geometry and about what `check`
   validates. Phase 13 found its largest defect exactly where the
   arithmetic looked obviously right.
