# Multi-extent VMDK phase 5: host-only flat create

## Status: Planned

Master plan: `PLAN-multi-extent-vmdk.md`.

## Planning effort

Medium, as the master plan's Planning effort paragraph names it.
Phase 4 pinned and shipped every byte of the descriptor this phase
writes, and the host already has a working single-extent precedent for
the whole shape of the job -- `run_create_vmdk_flat` opens files,
`ftruncate`s an extent, writes a descriptor, `fsync`s both and cleans
up on failure, all without the guest. This phase does that N times.

Two things kept it above mechanical, and both are decisions rather
than code. The operator's answer to open question 4 makes instar's
split create refuse something qemu accepts, which means the phase has
to draw a line between the path the user typed and the paths instar
derived, and defend it in the documentation. And the survey found the
existing cleanup path deletes a file it did not create, which this
phase would otherwise multiply by sixty-four.

## Decisions already taken by the operator

From the master plan, resolved 2026-11-16: sequential extent rotation
rather than simultaneous output devices (question 1), and the read-side
ceiling inside this plan as phase 3 (question 2).

Question 1 does not bind this phase. Rotation is a mechanism for
getting *format emission* out of the guest one extent at a time; a flat
extent has no format to emit, so this phase writes every byte on the
host and never rotates anything. That is the whole reason it can land
before phases 6 to 8.

Question 2 binds it hard, in a way the master plan's phase 5 section
does not anticipate. See decision 2.

Question 3 (`ddb.*` lines) was adopted by phase 4 and is already in the
emitter this phase calls; nothing here revisits it.

**Question 6 is resolved by adoption.** It asked whether flat create is
host-only. The survey confirms the seam exists and is exactly where the
master plan expected: `run_create` returns early into
`run_create_vmdk_flat` at `src/vmm/src/main.rs:21559`, before any VM is
launched, and the VMM already links the `vmdk` crate and calls
`vmdk::build_flat_descriptor` directly at `:17049`. Host-only is not a
choice this phase has to make; it is what the code does.

**Questions 4 and 5 were resolved by the operator on 2026-11-26**, as
this phase was planned, and both answers are recorded in the master
plan's open-questions section as part of the planning commit:

* **Question 4 -- extent paths and what is refused:** derive strictly
  from the output argument, refuse a derived extent that already
  exists, and keep overwriting the descriptor the user named. Decision
  1.
* **Question 5 -- maximum extent count:** refuse above
  `MAX_EXTENTS` (64 extents, 128 GiB) with a message naming the limit
  and the size that reached it, before any file is opened. Decision 2.

## Scope

In:

* `instar create -f vmdk -o subformat=twoGbMaxExtentFlat`: N sparse
  extent files of the lengths `split_layout` gives, plus the descriptor
  `build_split_descriptor` emits. Host-only.
* The extent **path set**: a generalisation of the existing
  `vmdk_flat_extent_path` that yields N paths and the decomposed stem
  the phase 4 emitter takes, carrying forward the name-safety refusal
  that function already enforces.
* Three refusals, all before any file is opened: a size needing more
  than `MAX_EXTENTS` extents, a size of zero, and a derived extent path
  that already exists.
* Failure cleanup that removes only what this run created. This is a
  fix to the existing single-extent path as well; see decision 5.
* `docs/create.md`, `docs/output-formats.md` and
  `docs/vmdk/vmdk-extents.md`.

Out, explicitly:

* **`twoGbMaxExtentSparse` create.** It needs format emission inside
  the guest and therefore phases 6 to 10. `create` must refuse it with
  a message that does not promise a date, and the refusal is a
  definition-of-done item.
* **`convert -O vmdk --subformat twoGbMaxExtent*`.** Phase 11. The
  convert subformat validator at `src/vmm/src/main.rs:16804` is not to
  be widened, and `git diff` proving it unchanged is a
  definition-of-done item.
* **`measure`.** Phase 12.
* **Any ABI change.** `CreateConfig.vmdk_subformat` stays at three
  values (`src/shared/src/lib.rs:2983`). A host-only path never
  populates a `CreateConfig`, so there is nothing to extend. `git
  diff --stat` listing no file under `src/operations/` is a
  definition-of-done item.
* **Raising `MAX_CHAIN_EXTENTS`.** Its own doc says lifting it means
  moving the guest config region. Not this phase, and the operator
  declined it as phase 5's answer to question 5.
* **Retrofitting `ddb.*` onto the existing subformats.** Already filed
  by phase 4 as #964.

## What the survey found

The master plan's phase 5 section is accurate about the shape of the
work and the seam it plugs into. Three of its surrounding claims are
stale, and one of the two questions it defers had already been
answered by code that landed after it was written.

**1. Open question 5's framing is foreclosed by phase 3.** The question
offers a choice between matching qemu's extent ceiling and imposing
one. Phase 1 had already measured that qemu has no ceiling (1000
extents at 2000 GiB, 16384 at 32 TiB, no refusal at 1 PiB), which the
question records. What it does not record, because phase 3 had not
landed, is that `shared::MAX_CHAIN_EXTENTS = 64`
(`src/shared/src/lib.rs:5258`) is a hard read-side cap: the guest's
extent table is a fixed array in a page of its own, and
`layout_descriptor_extents` refuses a descriptor declaring more than 64
extents rather than reading part of the image
(`src/vmm/src/main.rs:4934`). At qemu's 2 GiB extent size that is
128 GiB. Writing past it produces an image instar's own reader refuses,
which would also leave the differential fuzzer blind above the cap --
and reading its own output back was open question 2's stated reason for
doing the read side first.

Phase 4 reached the same conclusion from the other side and wrote it
into the emitter's own documentation at
`src/crates/vmdk/src/lib.rs:484-488`: "a caller that wants its own
output readable must bound `extent_count` at `MAX_EXTENTS`". So the
bound this phase enforces was already specified; it simply had nowhere
to be enforced until there was a caller.

**2. Open question 4's recommendation conflicts with instar's own
documented create contract.** The question recommends refusing if any
target already exists, and reports phase 1's measurement that qemu
instead truncates a pre-existing extent in place on all 80 builds. What
it does not weigh is that `instar create` already promises the same
thing for its own output: `CreateArgs::filename`'s help text at
`src/vmm/src/main.rs:9831` reads "Overwrites if it already exists
(matches qemu-img)", and `write_vmdk_flat` implements it with
`create(true).truncate(true)` at `:22695`. Refusing everything would
make the split subformat the one create path that does not honour that
promise. The operator's resolution splits the difference along the line
of what the user actually named, which decision 1 states.

**3. The extent-name safety predicate already exists.** Phase 4's
review round 2 declined an extent-name predicate and recorded it as a
note for this phase. The survey found the host already has one:
`vmdk_flat_extent_path` (`src/vmm/src/main.rs:17011`) refuses a derived
name that is not UTF-8 or that contains `"`, CR or LF, with a message
naming the operation, because a descriptor cannot quote such a name
unambiguously. Phase 4's emitter documentation points at it as "the
rule to follow" (`src/crates/vmdk/src/lib.rs:470`). This phase
generalises that function rather than writing a second predicate, and
the declined review item is thereby answered by reuse.

**4. A defect in the code this phase extends.** `write_vmdk_flat`
pushes each path onto `opened` immediately after opening it, and
`run_create_vmdk_flat` removes every path in `opened` if the write
fails (`src/vmm/src/main.rs:22663-22673`). The descriptor is opened
first, with `truncate(true)`. So a `create` that fails while opening
the extent -- a permissions error, a full filesystem -- truncates a
pre-existing file the user named *and then deletes it*, where qemu-img
would have left the truncated file in place. It is small, it is
shipping today for `monolithicFlat`, and this phase would otherwise
inherit it with sixty-four times the blast radius. Decision 5 fixes it
in the shared helper rather than filing it, and says why.

**5. Everything else the section claims holds.** `split_layout`,
`SplitLayout::sectors_of_extent`, `build_split_descriptor` and
`parse_descriptor_extents` are all `pub` in `src/crates/vmdk/src/lib.rs`
and the VMM already links that crate. The emitter formats `-s%03d` and
`-f%03d` names itself from a decomposed stem and picks the `FLAT`
keyword and its trailing ` 0` offset from `SplitLayout::kind`
(`:508-536`), so this phase supplies paths and lengths and emits no
descriptor text of its own. `scripts/create-vmdk-flat-testdata.sh`
already builds a `twoGbMaxExtentFlat` image and deletes its extents, as
phase 2 left it, which is the generator the tests extend.

**Corrected at source** in this planning commit, so a later reader does
not have to find these out again:

* Open questions 4 and 5 record the operator's resolutions, and why the
  recommendations as drafted no longer hold.
* Questions 3 and 6 had no resolution recorded although both were
  settled by adoption -- 3 by phase 4's shipped emitter, 6 by this
  phase. Both now say so, and the section's preamble no longer claims
  questions 3 to 6 are open.
* Success criterion 5 ("The size at which instar refuses a multi-extent
  image matches qemu-img's") is rewritten. It rests on the same
  `%03d`-implies-999 premise phase 1 disproved, and the operator has
  now chosen a limit qemu does not have.
* Success criterion 1's byte-identity claim is bounded to the sizes
  instar accepts.

## Decisions

1. **Refuse a derived extent that already exists; keep overwriting the
   named descriptor.** The user typed one path. `out.vmdk` is theirs to
   lose and create has always said so; `out-f001.vmdk` through
   `out-f064.vmdk` are instar's invention, and truncating sixty-four
   files nobody named is not a thing to do silently in a tool whose
   threat model assumes hostile input. The refusal names the first
   offending path and says nothing has been written. This is a
   documented divergence from qemu-img, which truncates all of them.

   *The cost, stated plainly:* a user who re-runs a failed create in
   the same directory now has to remove the extents themselves, where
   qemu-img would have overwritten them. That is the price of the
   guarantee, and `docs/create.md` says so in those terms rather than
   presenting the refusal as unambiguously better.

2. **Bound the extent count at `MAX_EXTENTS` (64), which is 128 GiB.**
   Refuse before any file is opened, with a message naming the limit,
   the extent count the size would need, and the size itself. 128 GiB
   exactly is 64 extents and is accepted; one byte more needs 65 and is
   refused. The bound is instar's, not qemu's, and the message and the
   documentation both say so -- it exists because the guest's extent
   table is a fixed array, and an image instar cannot read back is not
   output worth writing.

3. **Refuse a zero virtual size.** `split_layout(0)` yields zero
   extents, and `build_split_descriptor` then emits a descriptor with no
   extent lines at all, which `parse_descriptor_extents` refuses as
   `NoExtents`. Phase 4's emitter doc makes this the caller's bound.
   Refuse it with its own message rather than letting it reach the
   emitter.

4. **Generalise `vmdk_flat_extent_path`, do not duplicate it.** One
   function yields the decomposed stem the emitter wants and the N
   derived paths, and carries the existing UTF-8-and-no-quote refusal
   unchanged. `monolithicFlat` keeps using it with its `-flat` suffix.
   Writing a second path builder would leave two predicates to keep in
   step, which is how the `ddb.*` divergence in #964 happened.

5. **Fix the cleanup defect in place, in the shared helper.** Track
   whether this run created each file, and remove on failure only the
   ones it did. This changes shipped `monolithicFlat` behaviour: a
   failed create no longer deletes a pre-existing descriptor it
   truncated. That is a bug fix, not a format change, and it is in
   scope because this phase extends the exact function that has the
   defect and would multiply it by sixty-four. It gets its own commit
   and its own test so it is revertable independently of the phase.

   *The argument against, which the operator may prefer:* the skill
   that planned this phase says to record a surveyed bug as an issue
   rather than fix it. The counter is that leaving it would mean
   shipping the same defect twice over in the same release, in code
   written in the same week, and the correct-fixes guidance in this
   repository prefers fixing the cause where it lives. This is the
   first back-brief item.

6. **The extents are written before the descriptor, and every extent
   is `fsync`ed before the descriptor is.** A failure part-way must
   never leave a descriptor pointing at a set that does not match it,
   which is the master plan's success criterion 6. A descriptor that
   does not exist yet cannot lie.

7. **`twoGbMaxExtentSparse` is refused by `create` with a message that
   promises nothing.** It names the subformat as not supported for
   `create` and lists what is. No phase number, no plan file, no "yet"
   that implies a date -- this repository's convention is that landed
   code does not cite plan phases.

## Step plan

Sequential. Every step writes to the same worktree, and steps 5b
onward consume the previous step's interface, so none of these may run
in parallel.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 5a. The path set and the three refusals | high | opus | worktree | Generalise `vmdk_flat_extent_path` (`src/vmm/src/main.rs:17011`) into a function that takes the output path and a `SplitLayout` and returns the decomposed stem (`prefix`, `postfix`) that `vmdk::build_split_descriptor` takes plus the N derived extent paths, keeping its existing UTF-8 / no-`"`-CR-LF refusal verbatim and leaving `monolithicFlat`'s `-flat` call site working. Add the three refusals of decisions 1, 2 and 3, all of them before any file is opened: more than `vmdk::MAX_EXTENTS` extents, a zero size, and a derived extent that already exists. Match the wording shape of the read-side refusal at `:4934`. Read phase 4's emitter doc at `src/crates/vmdk/src/lib.rs:470-495` first -- it specifies these bounds as the caller's. Unit tests for each refusal and for the name derivation at 1, 2, 64 and 65 extents. Commit subject: "Derive a split VMDK's extent paths." |
| 5b. The writer | high | opus | worktree | Add the host-only writer beside `write_vmdk_flat` (`src/vmm/src/main.rs:22683`): for each extent, open and `ftruncate` to `layout.sectors_of_extent(i) * 512`; then write the descriptor from `vmdk::build_split_descriptor` using 5a's stem; `fsync` every extent before the descriptor, then the descriptor (decision 6). Follow the existing function's idioms exactly -- `mode(0o644)`, the measure-then-write two-call pattern at `:17049`, and the `Result` error strings prefixed `create:`. Do **not** change the convert path. Commit subject: "Write a split flat VMDK on the host." |
| 5c. Cleanup that removes only what it created | high | opus | worktree | Decision 5. Change the `opened` list in `run_create_vmdk_flat` / `write_vmdk_flat` (`:22659-22673`) to record whether this run created each file, and remove on failure only those. Both the single-extent and split paths use it. Add a test that a failure after a pre-existing descriptor was truncated leaves that file present. Keep this in its own commit, touching nothing else, so it can be reverted alone. Commit subject: "Delete only the files create itself made." |
| 5d. CLI wiring | medium | sonnet | worktree | Extend `is_vmdk_flat_create` (`src/vmm/src/main.rs`, two lines) and `create`'s subformat validation to accept `twoGbMaxExtentFlat` and route it to 5b. Refuse `twoGbMaxExtentSparse` per decision 7. Find every create-side subformat validation site -- there is more than one, and the owners are not what this row first claimed. `create`'s own `-o subformat=` mapper (the arm whose error reads `create: bad value`) **must** change, or `-o subformat=twoGbMaxExtentFlat` is refused as a bad value; only `measure`'s identical-looking mapper and its string-to-number match are the ones to leave alone, along with `convert`'s `--subformat` value list and its validator. Note the trap in `apply_create_overrides`: a `-o` subformat round-trips through a number and back to a string with `_ => "monolithicSparse"` as its fallback, so a new string added to the forward mapper and not the reverse one silently creates a monolithicSparse image instead of being refused. Commit subject: "Accept twoGbMaxExtentFlat in create." |
| 5e. Tests against the real oracle | high | opus | worktree | Integration tests under `tests/`. The descriptor oracle is already committed: phase 1's pinned cases in `tests/golden/qemu-create-pins.json`, reachable through `tests/helpers/qemu_pins.py`, and the static per-version `qemu-img` builds in `instar-testdata`. There are **seven** pinned flat cases, and their names carry their units: `vmdk-multi-flat-1M` (1 extent), `-2G` (1), `-2049M` (2), `-4G` (2), `-5G` (3), `-21G` (11), and `-5G-existing-extent` (3). **Every one of them is within the 64-extent ceiling, so all are comparable** -- the largest is 11 extents. Compare all six plain cases byte for byte; there is no pinned case past the ceiling, so decision 2's refusal is proven by instar's own arithmetic at unit level and not against a pin. The seventh case is the one to read carefully: `vmdk-multi-flat-5G-existing-extent` pins `rc: 0` and an unchanged descriptor for a create whose extent already existed, which is the measurement that makes decision 1 a divergence rather than a preference. Assert against it that instar refuses where qemu succeeded, and cite the pin. Assert instar's end-to-end `create` descriptor is byte-identical to qemu's for the five comparable sizes with `$CID` substituted, and that every extent file exists with the length the pin implies. Extend `scripts/create-vmdk-flat-testdata.sh`'s split-flat case rather than writing a new generator. Remember that `tests/` is copied to `/tmp` for the packaged run, so no test may resolve a path outside it -- `tools/ci/check-tests-relocatable.sh` enforces this and runs in CI. Classify the suite run with `tools/ci/check-stestr-run.sh`, never stestr's totals block. Commit subject: "Test split flat create against qemu's pins." |
| 5f. Mutation-check the three refusals | medium | sonnet | worktree | The master plan requires it for any test meant to prove a refusal. Break each of 5a's three refusals and decision 5's cleanup fix in turn, on a copy, and confirm a named test fails and names the right thing. Restore from the copy, never with `git checkout <dir>`. Run each mutant under `timeout` and count a timeout as killed. Leave the mutations in a runnable script and record its path and the kill count in the execution notes. Commit subject: "Mutation-check split create's refusals." |
| 5g. Documentation | medium | sonnet | worktree | This row undercounted the work; the full set was mapped before the step ran. `docs/create.md` has **seven** places, not three: the opening paragraph saying the monolithicFlat path does not require KVM (now true of two subformats), the per-format subformat table row, the backing-file refusal sentence, the `-o subformat` key list, the `backing_file` note, the `zeroed_grain` refusal note, and the "Rejected (instar doesn't emit these subformats)" list, which must keep `twoGbMaxExtentSparse` and drop `twoGbMaxExtentFlat`. **Two pages beyond the three named here also carry the roster:** `docs/format-coverage.md`, which says in prose that `create` and convert write monolithicSparse, streamOptimized and monolithicFlat and that "neither writes the split twoGbMaxExtent subformats" -- now false for `create` and still true for convert -- and whose vmdk create row repeats the list; and `ARCHITECTURE.md`'s creatable-format roster, which names monolithicFlat as the host-written one. Add the new subformat, the 128 GiB limit with the reason it is instar's and not qemu's, and the pre-existing-extent refusal stated as the trade-off decision 1 describes, including its cost to the user. `docs/output-formats.md` already names the `twoGbMaxExtent*` subformats on the read side, where it describes the `/extents.N` children; say there that one of them can now be written. `docs/vmdk/vmdk-extents.md` (phase 4's page): the path derivation rule and the refusals, next to the naming rule already there. State the qemu divergences as divergences. Nothing under `docs/` outside `docs/plans/` may carry a `file.rs:123` line reference: the `docs-line-references` pre-commit hook rejects them and excludes only plan files, so name the function rather than the line. Commit subject: "Document split flat create." |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The 128 GiB refusal looks arbitrary to a user who knows qemu writes 32 TiB images | The message names the limit and the mechanism, and `docs/create.md` explains that instar refuses what it could not read back. Checked in 5g by the management session reading the rendered text, not the diff. |
| Decision 5 changes shipped `monolithicFlat` failure behaviour | Its own commit, its own test, revertable alone. First back-brief item, so the operator can decline it before any code moves. |
| Sixty-four `ftruncate`d sparse files look like a 128 GiB write to a watching filesystem | They are holes, as `monolithicFlat` already does for one file. 5e asserts apparent length, not allocated length, and the test must not assert a disk-usage figure -- phase 2 measured that git-LFS stores such holes as bytes, which is why no split-flat fixture is committed. |
| A partial failure leaves extents behind with no descriptor | Decision 6's ordering makes that the only possible half-state, and it is the safe one: files with no descriptor are inert. 5c's cleanup removes them. |
| The refusal for a pre-existing extent fires on instar's own second run and reads as a bug | 5g documents the re-run story explicitly. |
| A sub-agent asserts a qemu-img behaviour nobody measured | The master plan forbids it. Every qemu claim in 5e cites a pinned case or a static build; the management session re-runs a sample. |

## Definition of done

Falsifiable; each is checkable by someone who did not do the work.

1. `instar create -f vmdk -o subformat=twoGbMaxExtentFlat out.vmdk 5G`
   writes `out.vmdk` plus `out-f001.vmdk`, `out-f002.vmdk` and
   `out-f003.vmdk`, and
   `out.vmdk`'s bytes equal phase 1's pinned 5 GiB
   `twoGbMaxExtentFlat` descriptor with `$STEM` and `$CID`
   substituted.
2. Each extent file's apparent length is
   `sectors_of_extent(i) * 512`: 2 GiB for every extent but the last.
   For 5 GiB that is 2 GiB, 2 GiB, 1 GiB.
3. `instar info out.vmdk` reads the image instar just wrote, and
   reports the virtual size it was asked for.
4. A size needing 65 extents (128 GiB + 1 byte) is refused, the
   message names the 64-extent limit and the size, and **no file
   exists afterwards** -- neither descriptor nor extent.
5. 128 GiB exactly is accepted and writes 64 extents.
6. The pinned `vmdk-multi-flat-5G-existing-extent` case -- which
   records `rc: 0` and a complete descriptor for a qemu-img create
   whose extent file already existed -- is refused by instar, and the
   test cites that pin, so decision 1's divergence is recorded against
   a measurement rather than an assertion.
7. A zero size is refused and no file is created. The refusal comes
   from `create`'s own size check, not from the split plan's
   zero-extent guard: `create` rejects a zero SIZE before any
   subformat routing happens, so the guard decision 3 adds is
   defence in depth for a caller inside the VMM rather than a
   message a user can reach. The integration test asserts the
   message a user actually gets; 5a's unit test covers the inner
   guard.
8. A pre-existing `out-f002.vmdk` is refused by name, nothing is
   written, and the pre-existing file is unchanged byte for byte.
9. A pre-existing `out.vmdk` is still overwritten, matching the
   documented contract in `CreateArgs::filename`'s help text.
10. A derived extent name that is not UTF-8, or contains `"`, CR or
   LF, is refused by the same predicate `monolithicFlat` uses, and
   `monolithicFlat`'s own behaviour on such a name is unchanged.
11. A `create` that fails after truncating a pre-existing descriptor
    leaves that file present, for both `monolithicFlat` and the split
    subformat.
12. `create -f vmdk -o subformat=twoGbMaxExtentSparse` is refused with
    a message that lists the supported subformats and cites no plan
    phase, plan file or date.
13. `git diff` touches no line of the convert subformat validator at
    `src/vmm/src/main.rs:16804`, and `git diff --stat` lists no file
    under `src/operations/`.
14. `src/shared/src/lib.rs`'s `CreateConfig.vmdk_subformat` still
    documents three values.
15. Four mutants, one per refusal and one for the cleanup fix, each
    killed by a named test, none counted as killed by a skip or by a
    timeout that was really a hang. The script's path and the count
    are in the execution notes.
16. `docs/create.md` states the 128 GiB limit, that it is instar's and
    not qemu's, and the cost of the pre-existing-extent refusal to a
    user re-running a failed create.
17. `pre-commit run --all-files` passes with new files `git add`ed
    first, `make test-rust` reports 0 failures with the count checked,
    and the integration selection is classified by
    `tools/ci/check-stestr-run.sh` rather than by stestr's totals
    block or an exit code.
18. `scripts/check-binary-sizes.sh` passes. Core's `.bss` is
    untouched by a host-only change, and the before and after numbers
    in the execution notes show it.

## Back brief

Answer these before any step runs.

1. **Decision 5, the cleanup fix.** It is a behaviour change to
   shipping `monolithicFlat` code, made inside a phase about a new
   subformat. In scope as planned, or filed as an issue and left
   alone?
2. **Decision 1's divergence.** Confirm the refusal is worth diverging
   from qemu for, now that the cost to a user re-running a failed
   create is written down.
3. **The 128 GiB ceiling's message.** Should it name
   `MAX_CHAIN_EXTENTS`'s mechanism (the guest's extent table) or just
   the limit? Naming the mechanism explains an otherwise arbitrary
   number; it also tells a reader something about the guest's memory
   map.
4. **Whether this session may push and open the pull request** when
   the phase is done.

## Execution notes

Implementation began 2026-11-27 (AEDT), from commit `8e7ce7df` on
`multi-extent-vmdk-phase-05-flat-create`.

**Back brief, answered 2026-11-27 (AEDT).** All four as the plan
recommended.

1. Decision 5's cleanup fix stays in scope, in its own revertable
   commit with its own test, rather than being filed as an issue.
2. Decision 1's divergence from qemu-img is confirmed, now the cost to
   a user re-running a failed create is written down.
3. The extent-ceiling refusal **names the mechanism** -- that the
   guest's extent table is a fixed array, so instar could not read the
   image back -- and not only the limit. Step 5a's message does.
4. This session may push the branch and open the pull request when the
   phase is done.

Steps run in the order the table gives them, sequentially, because
every one of them writes to the same worktree and 5b onward consume
the previous step's interface.

The step table's `Isolation` column is read as the phase worktree
itself rather than a throwaway copy per sub-agent. A discarded
worktree would break that chain, and one cut from the shared clone
would be on `develop`, carrying neither this branch nor this plan.

**Step 5a, 2026-11-27.** The extent path set and the three refusals,
in `src/vmm/src/main.rs` alone (+442/-30).

The row asked for a function taking the output path and a
`SplitLayout`. That cannot also serve `monolithicFlat`, which has no
layout, so the sub-agent introduced a `VmdkExtentNaming` enum carrying
the layout in its `Split` variant. Kept: it is what decision 4 asks
for -- one derivation and one name-safety predicate -- rather than a
departure from it. `vmdk_flat_extent_path` survives as a two-line
adapter with its old signature, so convert's and create's
single-extent call sites are untouched.

The refusals live in `vmdk_split_create_plan`, applied zero size
first, then the extent count, then existence, so the ceiling refuses
before a size near `u64::MAX` could derive 2^33 names. The existence
test uses `symlink_metadata`, so a dangling symlink counts as the path
being taken: creating through one writes to a file the user never
named. That reading is beyond the row and is kept.

`vmdk_split_create_plan`, and `VmdkExtentPaths`'s two stem fields,
carry `#[allow(dead_code)]` until step 5b's writer calls them. **5b
removes both.**

Verified by the management session rather than from the report:
`build_split_descriptor`'s stem contract was re-read from
`src/crates/vmdk/src/lib.rs` (`prefix` is the base name without its
extension, `postfix` the extension including its dot) and matched
against the derivation; the convert subformat validator and
`CreateArgs::filename`'s help text were confirmed outside every diff
hunk; and `make test-rust` was re-run on the reviewed tree -- 3164
passed, 0 failed, 3 ignored over 83 test binaries -- with the four
refusal tests confirmed present in that run rather than inferred from
the total.

**Correction, 2026-11-27 (AEDT): step 5e's brief and definition-of-done
item 6 were wrong about the pinned cases, and are fixed above.** Found
by the management session checking the brief's premises before sending
it, which is what the risks table's last row asks for.

The brief said there are six pinned flat cases "named in GiB", and that
`vmdk-multi-flat-2049` is 2049 GiB needing 1025 extents, so decision 2
refuses it and it serves as a pinned witness of the ceiling. Read from
`tests/golden/qemu-create-pins.json`, the names carry their units and
there are seven cases: `1M` (1 extent), `2G` (1), `2049M` (2), `4G`
(2), `5G` (3), `21G` (11) and `5G-existing-extent` (3). `2049M` is
2049 **MiB**, two extents, not 2049 GiB. The largest pin is eleven
extents, so **nothing in the pin set is past the 64-extent ceiling**
and there is no pinned witness of it: decision 2's refusal rests on
instar's own arithmetic, unit-tested in step 5a, and the plan no longer
claims otherwise.

The seventh case repays the error. `vmdk-multi-flat-5G-existing-extent`
pins `rc: 0` and a complete descriptor for a qemu-img create whose
extent file already existed. That is a measured witness of exactly the
behaviour decision 1 diverges from, which is better evidence than the
claim it replaced, and definition-of-done item 6 is now that
divergence.

**Step 5b, 2026-11-27.** The host-side writer, in `src/vmm/src/main.rs`
alone (+265/-4): `run_create_vmdk_split_flat` as the entry point,
`write_vmdk_split_flat` as the writer, and `vmdk_split_descriptor` as
the measure-then-write pair beside `vmdk_flat_descriptor`.

Each extent is opened, recorded, `set_len` to
`sectors_of_extent(i) * 512` and `fsync`ed, in extent order; only then
is the descriptor text built, the descriptor opened, written and
`fsync`ed. That is the reverse of the single-extent path's order and is
decision 6: a descriptor that does not exist cannot name a set of
extents that is not on disk, so extent files with no descriptor are the
only half-state a failure can leave, and nothing reads those.

The entry point reports `layout.total_sectors * 512` as the virtual
size rather than rounding the request itself. Verified rather than
taken on trust: `split_layout` computes `total_sectors` with
`div_ceil(512)`, so that is the request rounded up to a whole sector,
which is the number the single-extent path reaches through
`checked_next_multiple_of(512)`. It cannot overflow because the plan
has already bounded the extent count.

Two divergences from the single-extent path were kept deliberately.
The new writer's error strings escape their paths with
`terminal_safe_path` where the old one uses `Path::display`: the
derived names are refused for `"`, CR, LF and non-UTF-8 but **not**
for other control characters, so an ESC in a derived name would
otherwise reach the terminal raw. And the `OpenOptions` closure is
duplicated rather than shared, because step 5c reworks the failure
cleanup across both paths and can unify the two call sites once
instead of twice.

Allow bookkeeping came out as planned: step 5a's two
`#[allow(dead_code)]` attributes are gone, and one remains on
`run_create_vmdk_split_flat`, which step 5d's CLI routing removes.
Clippy runs with `-D warnings` here, so nothing else in the step is
unreachable.

Verified by the management session: `make test-rust` re-run on the
reviewed tree, 3168 passed, 0 failed, 3 ignored over 83 binaries --
the 3164 baseline plus this step's four -- with all four named in the
run rather than inferred from the total, and `pre-commit run
--all-files` twelve of twelve.

**Step 5c, 2026-11-27.** The cleanup fix of decision 5, in
`src/vmm/src/main.rs` alone (+267/-62). `VmdkCreatedFiles` records each
opened path together with whether this run brought it into existence,
tested with `symlink_metadata` immediately before the truncating open,
and `remove_created` removes only the files flagged as this run's. Both
flat writers open every file through it, which unified the duplicated
`OpenOptions` closure step 5b left for exactly this purpose.

**The defect was pinned as intended behaviour by its own test.**
`flat_failure_after_opening_removes_the_descriptor` asserted that a
failed create leaves no descriptor -- that is, that it deletes the
pre-existing file it had truncated -- and `git log -S` puts it in
`6c1cd8f4`, the commit that first shipped host-side `monolithicFlat`
create. It passed on every tree since, including this branch's. The
replacement asserts the opposite on the identical scenario, so it
would have failed before this change; that contradiction is the
mutation evidence for this step, and step 5f need not re-derive it.

It also settles decision 5's recorded counter-argument more sharply
than the decision did. Filing the defect as an issue would have left
it behind a green test asserting it was correct, which is why reading
the code was the only way to find it.

Six tests, both halves of the guarantee for both subformats: a
pre-existing descriptor survives a failure, and the files the run did
create are still removed so a failure leaves no litter. For the split
subformat the descriptor's contents survive intact, because that
writer opens it last and a part-way failure never truncates it; a
pre-existing derived extent cannot arise there at all, since step 5a
refuses it before anything is opened, so no test pretends to cover it.

Two limits are stated in the code rather than papered over. A
pre-existing extent for `monolithicFlat`, and the split descriptor
once truncated, are not injectable end to end -- the only portable
failure injection fires at an open, and every later failure needs a
full filesystem or permission games -- so both truncated-then-kept
cases are proven at the helper's own level, where the bookkeeping
lives.

A symlink at the output path now counts as the path being taken, so
the link is no longer unlinked; previously the link was removed and
its target left truncated. Both leave the target truncated, and
keeping the link follows the same rule as the rest of the step.

The helper takes a `render` closure so each writer keeps its existing
path quoting -- `Path::display` for the single-extent path,
`terminal_safe_path` for the split one. Unifying them on the escaping
form would be an improvement, but it changes a shipped error message
in a commit that has to stay revertable alone, so it was left.

Convert shares no part of this cleanup: it shares only
`vmdk_flat_extent_path`, and its own `EmptyOutputCleanup` /
`remove_empty_output` pair already removes a zero-length regular file
only. No convert behaviour changes here, so nothing needed deciding.

**Surfaced, not fixed: `run_create_raw` has the same defect class** --
a failure after `create_raw_finalize` unlinks the output whether or
not this run created it. It is a different code path with its own
callers, this phase has no mandate over `create -f raw`, and widening
the phase is the operator's call. Reported to the operator on
2026-11-27 rather than folded in or filed unilaterally.

Verified by the management session: `make test-rust` re-run on the
reviewed tree, 3173 passed, 0 failed, 3 ignored over 83 binaries --
3168 plus six new, minus the removed defect test -- with all six named
in the run and the removed one confirmed absent from it, and
`pre-commit run --all-files` twelve of twelve.

**Step 5d, 2026-11-27.** The CLI wiring, in `src/vmm/src/main.rs`
(+210/-16): `twoGbMaxExtentFlat` added to `CreateArgs`' `--subformat`
value list, mapped to and from `4` in create's `-o subformat=` parser,
accepted by `validate_create_args`, and routed by a new
`is_vmdk_split_flat_create` beside the `monolithicFlat` route. Step
5b's `#[allow(dead_code)]` is gone, so nothing in the phase is
unreachable any more.

**Step 5d's row in the table above was wrong and is corrected there.**
It said two `-o subformat=` mappers belong to other subcommands and
must not change. Only one does -- `measure`'s, whose error reads
`measure: bad value`. The other is create's own, and leaving it alone
would have refused `-o subformat=twoGbMaxExtentFlat` as a bad value,
which is the exact command definition-of-done item 1 runs. The row now
identifies each site by the subcommand in its error string rather than
by a line number.

The row also now names a trap the plan had missed: a `-o` subformat
round-trips through a number and back to a string in
`apply_create_overrides`, whose fallback arm was
`_ => "monolithicSparse"`. Adding the new subformat to the forward
mapper alone would have silently created a monolithicSparse image
instead of refusing anything -- a wrong answer no existing test could
catch, since every monolithicSparse assertion would still have held.
The fallback is now `unreachable!` naming the number. That is a
deliberate choice: the forward parser is the only writer of the field
and produces only 0, 1, 2 and 4, so the arm is an invariant rather
than a reachable path, and a crash is strictly better than a wrong
image. A returned error would need `apply_create_overrides` to change
signature and every caller to handle it, which is outside this step.
**The structural fix -- one source of truth for the number-to-name
mapping -- is worth a follow-up.**

A correction to the management session's own brief, found by the
sub-agent: the `--subformat` value list documented "when `-O` vmdk"
belongs to `MeasureArgs`, not to convert. `measure` takes `-O` as
well. Convert has no clap value list for subformat at all; its only
gate is the validator this phase must not widen, which is confirmed
untouched -- the diff's hunks run from 9903 straight to 18856, and
that validator sits at 16802.

`twoGbMaxExtentSparse` is deliberately **not** in create's clap value
list, so `--help`'s possible values stay truthful rather than
advertising something always refused. The two routes therefore print
different messages, both correct: `-o subformat=twoGbMaxExtentSparse`
gives decision 7's refusal, naming the subformat and listing the four
that are supported, with no phase number, file name or date; and
`--subformat twoGbMaxExtentSparse` is refused by clap, which names the
valid values.

**One qemu claim in this step is unmeasured, and is deferred to 5e
rather than accepted.** All four of `monolithicFlat`'s option refusals
-- backing file, SIZE, `zeroed_grain` and grain size -- were applied
to the split subformat on the reasoning that qemu treats both as flat.
No pinned case covers grain size with any flat subformat, so that is
an assertion. Instar's behaviour is at least consistent with what it
already does for `monolithicFlat`, so nothing is broken either way,
but whether it diverges from qemu is unknown. Step 5e measures it
against a static build, and 5g documents it if it diverges.

Verified by the management session: `make test-rust` re-run on the
reviewed tree, 3178 passed, 0 failed, 3 ignored over 83 binaries,
3173 plus this step's five, all five named in the run. The re-run was
necessary rather than routine: the sub-agent's own last test run
predated a by-hand reformat it made after a denied `rustfmt` container
call. `pre-commit run --all-files` twelve of twelve. The real binary
is still unexercised by hand; step 5e's suite runs it.

**Step 5c, part two, 2026-11-27.** The defect was pinned **twice**, and
step 5c fixed only one of them. Besides the Rust unit test, the Python
integration suite had
`test_create.TestCreateVmdkFlat.test_failure_opening_the_extent_removes_the_descriptor`,
asserting that a failed `monolithicFlat` create leaves only the
directory behind -- that is, that the pre-existing descriptor is gone.
It was introduced with the subformat and had passed ever since, so from
commit `c3598d58` until this one the branch carried a failing test.

The management session's review of 5c did not catch it: `make
test-rust` and `pre-commit run --all-files` both pass on that tree, and
neither runs the Python suite. Step 5e caught it because it was the
first step to run the real binary. That is the review gap worth
remembering -- **a change to host behaviour needs the integration
suite run, not only the Rust tests and the gate.**

The class was swept rather than the instance fixed: that file has three
failure-cleanup tests, and only this one plants a file before running
create, so only this one encoded the defect. The other two create
everything they assert about, so their expectations still hold
unchanged. The test is renamed for what it now proves, asserts the
descriptor is present **and** zero length, and says in its docstring
that the guarantee is "not deleted" rather than "contents preserved".

Reverting decision 5 now means reverting this commit with `c3598d58`;
the pair is the revertable unit, not the single commit the plan asked
for.

**Step 5e, 2026-11-27.** Integration tests against the pinned oracle:
`tests/test_create_vmdk_split_flat.py`, thirteen tests, plus comments
on `scripts/create-vmdk-flat-testdata.sh`'s split-flat case recording
why its extents are deleted and why a real split-flat image is made at
test time rather than committed. No new fixture was needed, so
`instar-testdata` is untouched.

The approach is better than the row asked for. `qemu_pins.run_case`
takes any binary and reduces a create to rc, stderr, the descriptor
with its CID and extent names masked, the extent names, and each
extent's apparent length -- so running it with the instar binary
produces the same shape as the pinned qemu result, and the comparison
is structural rather than a second implementation of it. Six plain
pins are asserted identical that way.

Two facts the sub-agent established that the plan did not have. Each
pinned case has **two** groups, 6.0.0-6.1.1 and 6.2.0-10.2.0,
differing only in `ddb.toolsVersion`, so a comparison must name a
version; instar writes the modern form and the tests compare against
the newest pinned build, checked to discriminate by diffing against
the older group and against a different size. And **qemu-img has no
grain-size option for vmdk at all** -- the management session
re-measured this on the 10.2.0 static build: `-o help` lists nine
options with no `grain_size` among them, and
`-o subformat=twoGbMaxExtentFlat,grain_size=65536` gives `Invalid
parameter 'grain_size'`, rc 1. So step 5d's refusal matches qemu's
outcome at every subformat, and 5d's stated reason for it -- that
qemu treats both subformats as flat -- was wrong. Documentation must
say qemu takes no grain size for vmdk, not that instar diverges.
Accepting `grain_size` for the sparse subformats is instar's own
extension.

The existing-extent divergence is asserted against the measurement
rather than described: one test reads `rc: 0`, extent lengths
`[2Gi, 2Gi, 1Gi]` and `existing_kept_lengths [2147483648]` out of the
pin for all 80 builds, with the docstring explaining that a kept link
holding an extent's length means qemu truncated in place where 1 MiB
would have meant unlink-and-recreate; a second test runs instar on the
same case and asserts it refuses, names `image-f002.vmdk`, writes
nothing, and leaves the kept link at 1 MiB.

Verified by the management session, and the verification mattered
twice. The first run died in **discovery** -- `testscenarios` is
missing from the shared tools venv -- so nothing executed, with
`STESTR_EXIT=100` while the task notification reported exit code 0,
which is the trailing command's code. Re-run through the project's own
`make test-venv`, then classified with
`tools/ci/check-stestr-run.sh` against a listed selection rather than
from stestr's totals: **292 of 304 executed, 12 skipped, against 304
discovered; no worker lost**, 0 failed. All thirteen of this step's
tests are recorded `success` with **none skipped**, checked per test
rather than inferred -- a pin-gated test that skipped would have made
the run prove nothing. The twelve skips are pre-existing
baseline-matrix and round-trip cases for qcow2, vhd and raw.
`pre-commit run --all-files` twelve of twelve with the new file
`git add -N`'d, including the relocatable-tests check.

`make instar` needed `make clean-instar` first, failing with
`GLIBC_2.32/2.33/2.34 not found` because the dev image's artefacts
were in the shared target directory from the Rust test runs.

**Step 5f, 2026-11-27.** Mutation check. The row said to leave the
mutations in a runnable script; one already exists and is mature, so
four cases were added to **`tools/mutate-differencing.sh`** rather than
a second harness being written. That script has a PASS / FAIL / BROKEN
vocabulary in which BROKEN means the case proved nothing and is never
scored as a pass, restores from `.mutation-backups/` rather than with
`git checkout`, refuses to start on a dirty `src/`, and applies every
edit through `tools/replace-once.py`, which fails unless the search
string occurs exactly once. Its own header records that an earlier
hand-rolled harness reported success for code it never changed. A new
script would have re-learned that badly.

| Case | Mutation | Killed by |
|---|---|---|
| `vmdk-split-create-extent-ceiling-off-by-one` | bound at `MAX_EXTENTS + 1` | `one_byte_past_the_table_is_refused` |
| `vmdk-split-create-zero-size-guard-removed` | `if false && extent_count == 0` | `a_zero_size_is_refused_with_its_own_message` |
| `vmdk-split-create-existing-extent-not-refused` | `if false && symlink_metadata(..).is_ok()` | `a_derived_extent_that_exists_is_refused_by_name` |
| `vmdk-create-cleanup-removes-every-opened-file` | record every opened file as created | `a_file_that_was_already_there_is_kept` |

**4 PASS, 0 FAIL, 0 BROKEN**, harness exit 0, re-run by the management
session rather than taken from the report, with `src/` confirmed
restored afterwards -- a mutation left in the tree compiles and passes
the gate, so nothing downstream would catch it. The ceiling case is
bounded at one past the limit rather than deleted so that sixty-four
extents stay accepted: the mutant is confined to the boundary its test
is named for and cannot kill a different test instead. The first three
fail as `unwrap_err` on an `Ok`, which is the refusal vanishing rather
than a build error or an unrelated assertion firing first.

`EXPECTED_CASES` is 183, was 179. `--list` reports 183 cases and
`--check-patterns` reports 183 of 183 landing with none drifted, so
every search string still matches exactly once.

**The sub-agent left a documentation inconsistency, fixed by the
management session.** `EXPECTED_CASES` is cross-checked against a
headline count in `docs/testing.md`, which was bumped to 183 -- but
that page also carries a per-group table it describes as the flat
version so the sum can be checked in one pass, and the table still
totalled 179 with no row for the new cases. The page says the group
counts are the thing to re-derive when the two disagree, and nothing
checks them mechanically. Added: a `split flat create` row of 4, the
corrected total, "eighteen groups" for "seventeen", and the group's
prose paragraph in the page's established style. The table now has
eighteen rows summing to 183, matching the total row and the headline.

That also explains an exit 2 the sub-agent reported and did not trace:
the harness cross-checks the headline, `EXPECTED_CASES` and its own
count, and on that invocation the doc still said 179. It was the guard
working on a half-finished edit, not a mutation failure.

Decision 5's cleanup fix now has mutation evidence from two
independent directions: this mutant, and the contradicting test pair
recorded under step 5c part two. The second is the stronger of the
two, because it shows the defect was pinned as intended behaviour
rather than merely left untested.

**Step 5g, 2026-11-27.** Documentation. `docs/create.md` gains a
`twoGbMaxExtentFlat` section -- the naming rule, the holes, the
sync-before-descriptor order, a refusal table, the divergence with its
cost, and the unsupported sparse subformat -- plus the seven existing
places the row's correction above enumerates. `docs/format-coverage.md`,
`ARCHITECTURE.md`'s creatable roster, `docs/output-formats.md` and
`docs/vmdk/vmdk-extents.md` carry the rest.

**The row as planned named three pages and three places; the real set
was five pages and nine places, and the row is corrected above.** Two
of the pages the survey missed were the ones most likely to mislead:
`docs/format-coverage.md` stated in prose that neither `create` nor
convert writes the split subformats, which this phase makes false for
`create` while leaving it true for convert, and `ARCHITECTURE.md`'s
roster named `monolithicFlat` as the only host-written one. This is the
third row whose factual claims needed correcting during execution,
after 5d's site map and 5e's pin facts, and all three failed the same
way: **the survey recorded what it expected to find rather than
enumerating it.** Worth doing differently in the next phase's survey.

**One error found by reading the rendered text rather than the diff,
which is what this phase's risk table asks of the management session.**
The new section claimed the descriptor is what qemu-img writes "without
its DDB lines and with a constant CID". That is backwards: phase 4
adopted the `ddb.*` block, the emitter writes all six lines, and the
pinned descriptors contain them -- which is what step 5e asserts byte
identity against. The one sentence summarising this phase's headline
property contradicted it. It now says the descriptor is byte for byte
qemu-img's, disk database lines included, apart from the `CID`, where
qemu-img writes a random value and instar a constant one.

Two smaller corrections by the management session: `ARCHITECTURE.md`
wraps at about 71 columns and the new text ran to 94, so the sentence
was rewrapped (the first attempt only moved the overflow to the next
line); and `docs/format-coverage.md`'s new prose stated the convert
case twice, so it was tightened to one sentence.

The grain-size fact is documented as measured: qemu-img has no
grain-size option for vmdk at any subformat, so `--grain-size` is
instar's own extension, accepted for the sparse subformats and refused
for both flat ones, and the refusal is **not** described as a
divergence because it matches qemu-img's outcome.

`pre-commit run --all-files` twelve of twelve, including the hook that
bans source line references under `docs/`.

## Definition of done: results

Run on the committed tree at `f899f08d`, 2026-11-27 (AEDT). All
eighteen pass. Two items were reworded during execution, both recorded
above with the measurement that forced it.

| # | Result | How it was checked |
|---|---|---|
| 1 | Pass | `test_descriptor_extent_names_and_lengths_match_the_pin` over the six plain pins, and `test_five_gib_writes_three_extents_of_the_pinned_lengths` end to end |
| 2 | Pass | Same tests; 5 GiB gives 2 GiB, 2 GiB, 1 GiB, and every length asserted is an apparent length |
| 3 | Pass | `test_instar_info_reads_back_what_create_wrote` at 1 MiB, 5 GiB and 21 GiB |
| 4 | Pass | `test_one_byte_past_the_ceiling_is_refused_and_writes_nothing` |
| 5 | Pass | `test_the_largest_accepted_size_writes_sixty_four_extents` |
| 6 | Pass, **reworded** | The item named a 1025-extent pin that does not exist; it is now the `5G-existing-extent` pin, asserted by two tests -- one reading the measurement out of the pin, one running instar against the same case |
| 7 | Pass, **reworded** | The user-visible refusal is `create`'s own size check, not the split plan's zero-extent guard, which is unreachable from the CLI; the integration test asserts the real message and 5a's unit test covers the inner guard |
| 8 | Pass | `test_a_pre_existing_derived_extent_is_refused_byte_for_byte`, by sha256 over each of `f001` to `f003` |
| 9 | Pass | `test_a_pre_existing_descriptor_is_still_overwritten` |
| 10 | Pass | 5a's unit tests on the split path, and `vmdk_flat_extent_path_tests` unchanged and passing: `monolithicFlat` reaches the same predicate through a two-line adapter, so its messages are identical by construction |
| 11 | Pass | 5c's six tests for both subformats, plus the inverted integration test from 5c part two |
| 12 | Pass | `test_the_sparse_split_subformat_is_refused_with_what_is_supported`, and no `phase`, `PLAN-`, `.md`, "yet" or year anywhere near the refusal text |
| 13 | Pass | Over the whole phase the `main.rs` diff's hunks run 9903 then 16994 onward; the convert validator is at 16802, so no hunk touches it, and the diff against `src/operations/` is empty |
| 14 | Pass | The phase diff against `src/shared/` is empty, so `CreateConfig.vmdk_subformat` still documents three values. The host-side number 4 lives only in the VMM's own option struct |
| 15 | Pass | `tools/mutate-differencing.sh`, four new cases, 4 PASS / 0 FAIL / 0 BROKEN, harness exit 0, re-run by the management session with `src/` confirmed restored. `EXPECTED_CASES` 183 |
| 16 | Pass | `docs/create.md` carries all three statements: the 128 GiB limit, that it is instar's and qemu-img has none, and the cost of re-running a failed create |
| 17 | Pass | `pre-commit run --all-files` twelve of twelve with new files `git add -N`'d; `make test-rust` 3178 passed, 0 failed, 3 ignored, summed from 83 `test result:` lines rather than read from an exit code; the integration selection classified by `tools/ci/check-stestr-run.sh` as 292 of 304 executed, 12 skipped, against 304 discovered, no worker lost |
| 18 | Pass | `scripts/check-binary-sizes.sh` exit 0, every binary inside its region, core at **70672 bytes, 55% of its 128 KiB region**. The before equals the after: the phase diff against `src/core/`, `src/operations/`, `src/shared/` and `src/crates/` is empty, so nothing the guest's memory map depends on moved |

The twelve skips in the integration selection are pre-existing
baseline-matrix and round-trip cases for qcow2, vhd and raw. All
thirteen of this phase's integration tests are recorded `success`,
checked per test rather than inferred from the total, because a
pin-gated test that skipped would have made the run prove nothing.

## Review gap worth carrying forward

`make test-rust` and `pre-commit run --all-files` both pass on a tree
whose Python integration suite is failing, which is how step 5c's
missed pin survived review. **A change to host-visible behaviour needs
the integration suite run, not only the Rust tests and the gate.**

Three of the seven step rows had factual claims that needed correcting
during execution -- 5d's site map, 5e's pinned cases, 5g's page set --
and all three failed the same way: the survey recorded what it expected
to find rather than enumerating it. A survey claim of the form "there
are N of these" should be produced by a command whose output is kept,
not by reading until the expected number appears.

## Review round 1 (#984)

2026-11-27, +11:00. The automated reviewer raised 2 `fix`, 3
`document`, 4 `consider` and 2 informational items on head `12b5197c`.

Both `fix` items were real, and the first is the one worth carrying
forward. The two split-create cleanup tests in `create_option_tests`
planted a directory at the *second extent's* path, which the plan's
existence check refuses before any file is opened -- so both tests ran
through the refusal path and asserted nothing at all about cleanup.
Proved by mutation rather than by reading: with `remove_created`'s body
disabled, `split_failure_keeps_the_descriptor_it_did_not_create` and
`split_failure_removes_the_extents_it_created` both still passed, while
`flat_failure_removes_the_descriptor_it_created` and
`a_file_this_run_created_is_removed` failed. **A test whose failure
injection is refused earlier than it intends passes for the wrong
reason, and reads as coverage.** The replacement plants the directory
at the descriptor's own path -- the only failure a split create can be
made to take, because the plan refuses every other file it could find
in place -- and asserts the error starts at `create: open `, so a
refusal moved earlier would fail the test rather than satisfy it.

The other half of that guarantee, a file the run did not create being
kept once truncated, is unreachable from the split entry point at all:
once the first file is opened, nothing this writer opens can be a file
the user already had. It is tested in `vmdk_created_files_tests`, where
the bookkeeping is, and the test that used to claim it is now the
refusal test it had always been.

Three claims this phase wrote were stronger than the code, and are
corrected at their source. `docs/create.md` said a refused
pre-existing extent still overwrote the descriptor, in the one case
where nothing is opened at all. The "byte for byte the one qemu-img
writes" claim holds for 6.2.0 and later only: the four 6.0.0-6.1.1
builds in the pin write no `ddb.toolsVersion` and instar does. And the
extents-before-descriptor ordering protects a create that fails, not a
host crash -- the containing directory is never `fsync`ed, so the
descriptor's directory entry and an extent's can reach stable storage
in either order.

`docs/quirks.md` was the page the phase missed, and it is the one
`ARCHITECTURE.md` sends a reader to for writer divergences. Its DDB
entry became false as a general statement the moment this phase shipped
a subformat whose descriptor carries the whole `ddb.*` block, and both
new divergences -- the pre-existing-extent refusal and the 64-extent
ceiling -- belonged there and were absent. All three are fixed.

Declined, with the reasoning posted on the pull request: the
`unreachable!` arm stays, because a crash beats a silently wrong image
and the structural fix is a typed subformat rather than a `u8` round
trip, which is the follow-up the notes above already name. The `O_EXCL`
and path-quoting follow-ups are
[#986](https://github.com/shakenfist/instar/issues/986), and
`run_create_raw`'s cleanup defect -- surfaced in the notes above, and
reproducible in one command -- is
[#985](https://github.com/shakenfist/instar/issues/985). Both are named
in the master plan's Future work.

## Merge queue: the source size ratchet (#984)

2026-11-28. The first merge-queue run failed `Check the source size
ratchet` (run
[45940303198](https://github.com/shakenfist/instar/actions/runs/45940303198)).
The ratchet landed in #967 after this branch was cut, so the pull
request's own CI never ran it; the merge group, built on the new
`develop`, did. Three files had grown past their entries:
`src/vmm/src/main.rs` by 1,082 lines, `tests/test_create.py` by 6 and
`tools/mutate-differencing.sh` by 37.

The branch was rebased onto `develop` and the flat VMDK create path
moved out of `main.rs` into `src/vmm/src/vmdk_create.rs`: the extent
naming and path set, both descriptor builders, `VmdkCreatedFiles`, both
writers and their `run_create_*` entry points, the two subformat
predicates, the flat argument validator, and the four unit test modules
that cover them. It is a move with no change of behaviour; seven
functions became `pub(super)` because `main.rs` still calls them from
convert, `run_create` and `validate_create_args`. `main.rs` is now
26,795 lines, below the 26,811 it was baselined at, and its entry was
lowered with `--update`.

The five mutation cases this phase added named `main.rs` as their
target and now name `vmdk_create.rs`; `--check-patterns` lands all
184, and the five re-run 5 PASS, 0 FAIL, 0 BROKEN.

The other two entries were raised by hand. `test_create.py`'s growth is
one existing test renamed and re-asserted for the cleanup fix, with a
docstring saying why the truncated file survives; the test belongs
beside the rest of `TestCreateVmdkFlat`, and the phase's new tests
already went to a file of their own. `mutate-differencing.sh` is one
case list by design -- `EXPECTED_CASES` asserts its total -- and
splitting it would split that count.
