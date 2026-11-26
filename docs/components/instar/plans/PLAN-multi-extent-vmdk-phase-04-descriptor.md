# Multi-extent VMDK phase 4: the descriptor emitter

## Status: Complete

Master plan: `PLAN-multi-extent-vmdk.md`.

## Planning effort

Medium. Every byte this phase emits was measured by phase 1 and is
committed in `tests/golden/qemu-create-pins.json`, and the crate that
should hold the emitter already holds three like it, so neither the
target nor the home is a judgement call. Two things kept it above
mechanical: the buffer contract the existing emitters carry is unusual
and load-bearing (decision 2), and the oracle this phase tests against
is a hand-copied literal, which makes "the emitter agrees with the
test" worth nothing unless something independent ties the literal to
the pin (decision 8).

The master plan's Planning effort paragraph did not name phase 4 at
all. Corrected at source to medium as part of this phase's planning
commit.

## Decisions already taken by the operator

From the master plan, resolved 2026-11-16: sequential extent rotation
rather than simultaneous output devices (question 1), and the read-side
ceiling inside this plan as phase 3 (question 2).

Neither binds this phase. Rotation is a write-path mechanism and this
phase writes no bytes to any file; phase 3 is landed and its parser is
what reads this phase's output back in a test.

**Open question 3 is not resolved, and this phase turns on it.** It
asks whether multi-extent output emits qemu's `ddb.*` lines; the master
plan recommends yes for the new subformats and no retrofit of the old
ones, and the phase intent says phase 4 "carries the `ddb.*` decision
from open question 3". Decision 4 below adopts that recommendation and
states its cost. It is the first item of the back brief, because
reversing it later is six lines of emitter and one table of literals,
and reversing it after phase 5 ships a user-visible subformat is a
changed output format.

Open questions 4 and 5 -- extent path derivation and whether instar
imposes an extent-count limit -- are reserved to the operator in the
master plan and are **deliberately not needed here**. Decisions 5 and 6
scope the emitter so that it answers neither: it is handed a decomposed
stem and formats names from it, and it imposes no count of its own.
Phase 5 is the phase that cannot start until both are resolved.

## Scope

In:

* `src/crates/vmdk`: a `no_std`, panic-free emitter for the
  `twoGbMaxExtentSparse` and `twoGbMaxExtentFlat` descriptor files,
  including the `ddb.*` block.
* The pure function from a virtual size to a split geometry: extent
  count, each extent's sector count, and the CHS triple the `ddb.*`
  lines carry.
* The `-s%03d` / `-f%03d` extent-name rule, formatted from a
  decomposed output stem.
* Unit tests whose expected bytes come from the pinned qemu
  descriptors, and a check that ties those literals to the pin file.
* Reading the emitter's own output back through phase 3's
  `parse_descriptor_extents`.
* Filing the `ddb.*` retrofit issue the master plan's open question 3
  asks this phase to file, and the issue for the four duplicated
  descriptor builders the survey found.
* `docs/vmdk/vmdk-extents.md`: the naming rule and the geometry
  derivation, as format reference.

Out:

* Any `create` or `convert` wiring, any CLI `subformat=` value, any
  `CreateConfig` change. The emitter ships with no production caller;
  phase 5 is its first.
* Extent paths on a filesystem, and what happens when a target
  already exists (open question 4, phase 5).
* Any instar-side extent-count or size limit (open question 5, phase
  5).
* Sparse extent binary headers, grain directories and grain tables
  (phases 9 and 10). The survey measured that a split-sparse extent's
  reserved descriptor region is written as zeros, so no second
  descriptor is emitted anywhere; see below.
* Retrofitting `ddb.*` onto `monolithicSparse`, `streamOptimized` and
  `monolithicFlat`. An issue, not a change.
* Unifying the four existing descriptor builders. An issue, not a
  change.
* A random `CID`. instar writes the constant `fffffffe` everywhere and
  this phase does the same; the divergence is already documented.
* `twoGbMaxExtent*` as a backing file, excluded by the master plan.

## What the survey found

Checked against the tree at `394302f8`, with phase 3 landed as
`64e6fbc6`.

### The bytes are already pinned, and nothing has to run qemu

Phase 1 added fourteen `vmdk-multi-{sparse,flat}-*` cases to
`tests/golden/qemu-create-pins.json`, over all 80 static builds. Each
case records the descriptor text with the CID and extent names
replaced by placeholders, the extent names, the extent file lengths,
and a parsed structure. The sizes covered are 1 MiB, 2 GiB, 2049 MiB,
4 GiB, 5 GiB and 21 GiB in both subformats -- twelve distinct (size,
subformat) pairs -- plus a pre-existing-extent case in each
subformat at 5 GiB, which brings it to fourteen. The two
existing-extent cases record the same descriptor as the plain 5 GiB
case of their subformat, so what they add is the evidence that an
obstruction where an extent goes does not change the descriptor.

Every case has exactly two version groups, split at 6.2.0, and the
only difference is one line: `ddb.toolsVersion = "2147483647"`.
`tests/test_qemu_create_pins.py` already asserts that this is the one
boundary (`VMDK_MULTI_LAST_OLD`, `VMDK_MULTI_FIRST_NEW`), so a future
qemu change in this area fails a test rather than going unnoticed.

The 6.2.0-and-later descriptor, for 5 GiB sparse:

```
# Disk DescriptorFile
version=1
CID=<hex>
parentCID=ffffffff
createType="twoGbMaxExtentSparse"

# Extent description
RW 4194304 SPARSE "<stem>-s001.vmdk"
RW 4194304 SPARSE "<stem>-s002.vmdk"
RW 2097152 SPARSE "<stem>-s003.vmdk"

# The Disk Data Base
#DDB

ddb.virtualHWVersion = "4"
ddb.geometry.cylinders = "10402"
ddb.geometry.heads = "16"
ddb.geometry.sectors = "63"
ddb.adapterType = "ide"
ddb.toolsVersion = "2147483647"
```

The flat form differs in four places and no others: `createType`, the
`FLAT` keyword, a trailing ` 0` start offset on each extent line, and
`-f%03d` in place of `-s%03d`.

### The geometry rule, including the part the plan did not raise

`heads` is always 16 and `sectors` always 63. `cylinders` is
`total_sectors / (16 * 63)`, truncating. Confirmed against five pinned
sizes: 2048 sectors gives 2, 4194304 gives 4161, 4196352 gives 4163,
8388608 gives 8322, 10485760 gives 10402, 44040192 gives 43690.

The question the master plan did not raise is whether qemu clamps
`cylinders` to the 16-bit 65535 a real CHS geometry allows. It does
not, and no new measurement was needed to find out: phase 2's
`custom/format-coverage/vmdk-split-sparse-33g.vmdk` is qemu-written
and carries `ddb.geometry.cylinders = "68656"`, which is exactly
69206016 / 1008. So the derivation is a plain truncating divide with no
cap, and the 33 GiB fixture is a committed witness for a value past the
16-bit boundary.

### The buffer contract is deliberate, documented and in use

All four existing descriptor builders share one `put` closure that
advances its cursor past the end of the buffer but copies only when the
write fits, so the return value is the length the descriptor *needs*
whatever buffer it was given. That is `snprintf` semantics, and it is
relied on: `vmdk_flat_descriptor` in `src/vmm/src/main.rs:16988` calls
`vmdk::build_flat_descriptor(&mut [], ...)` to measure, allocates, and
calls it again to write, with a comment saying so.

This matters because the one thing a split descriptor makes unbounded
is its length. A caller that writes without measuring gets a silently
truncated descriptor today for a fixed-length descriptor that always
fits; for 64 extents it gets a file that names some of its extents.

### "One emitter used by both" is not what this phase inherits

The master plan's phase intent says "one `no_std`, panic-free emitter
in the crates that writes a multi-extent descriptor from a geometry,
used by both create and convert". The second half describes a
destination, not a starting point. Today there are four builders in two
crates:

| Builder | File | Writes |
|---|---|---|
| `build_descriptor` | `src/crates/vmdk/src/lib.rs:226` | monolithicSparse |
| `build_flat_descriptor` | `src/crates/vmdk/src/lib.rs:266` | monolithicFlat |
| `build_streamoptimized_descriptor` | `src/crates/vmdk/src/lib.rs:186` | streamOptimized |
| `build_vmdk_descriptor_with_backing` | `src/crates/create/src/lib.rs:1024` | either sparse kind, with a parent |

The fourth re-implements the first; its own comment says why ("the
stock builder hardcodes parentCID=FFFFFFFF and has no hook for the
parent filename"), and it carries a second copy of `format_u64` named
`format_u64_decimal`. Unifying them would touch every VMDK instar
writes and is not this phase's business. Step 4e files it.

### qemu writes the same `ddb.*` block for monolithicFlat

Which means instar's monolithicFlat output diverges from qemu's today,
not merely inconsistently with its own future output. `vmdk-flat-64M`
pins the full `ddb.*` block with `cylinders = "130"`, by the same rule.
The divergence is documented, in `docs/quirks.md` ("VMDK descriptors
omit qemu's DDB lines") and in `docs/create.md`'s Future work, and
`PLAN-bat-population.md` carries it as future work too.

What does not exist is a GitHub issue: `gh issue list --search ddb
--state all` returns nothing. So the issue open question 3 asks this
phase to file is genuinely still to file, and the retrofit that
eventually closes it has to rewrite that `docs/quirks.md` section as
well as the descriptors.

### The naming rule already has an implementation to factor

`vmdk_flat_extent_path` in `src/vmm/src/main.rs:16934` implements
qemu's `filename_decompose` (cited to `block/vmdk.c:2279-2316`): take
the basename after the last `/`, split it at its last `.`, and join
`<prefix>` + suffix + `<postfix>`. It also refuses a name that is not
UTF-8 or that holds `"`, CR or LF, which qemu writes verbatim --
instar's addition, because the descriptor records the name as a quoted
string on a line of text.

The split rule is that same decompose with `-s%03d` or `-f%03d` in
place of `-flat`. So this phase factors a rule rather than inventing
one, and the refusal above is a property phase 5 inherits, not
something to re-decide.

### `%03d` is a floor, not a cap

Phase 1's probe for open question 5 measured four builds writing 1000
extents at 2000 GiB with the thousandth line verbatim `RW 4194304
SPARSE "image-s1000.vmdk"`, and 16384 extents at 32 TiB. So the width
is a minimum and the index runs past three digits unpadded.

### A split-sparse extent's reserved descriptor region is zeros

Measured for this plan, on `vmdk-split-sparse-33g-s001.vmdk`: the
VMDK4 header gives `descriptor_offset_sectors = 1` and
`descriptor_size_sectors = 20`, and all 10240 bytes of that region are
zero. qemu reserves the space in every split-sparse extent and writes
nothing into it. So the only descriptor text a split image contains is
the standalone descriptor file, and this phase's emitter is never
asked for a second, embedded copy. Phase 10 needs the same fact when
it writes those headers.

### Rust-side golden literals are the house pattern, and unguarded

`src/crates/create/tests/round_trip.rs:2190` compares instar's fixed
VHDX against values "copied from `tests/golden/qemu-create-pins.json`,
whose every pinned qemu build agrees on them", with each table row
naming its pin case, and states the reason the file is not read
directly: "the workspace has no JSON parser, so a change to the pins
has to be copied here by hand". Nothing checks that copy. No Python
test in `tests/` reads a `.rs` file, so there is no precedent for the
guard decision 8 adds, and no precedent against it.

### Everything else the phase intent claims is true

`src/crates/vmdk` is `#![no_std]` with no `alloc`. `vmdk_subformat` in
`shared::CreateConfig` is a `u8` carrying 0, 1 and 2 today, so adding
values is a later phase's change and not one this phase needs.
`parse_descriptor_extents` and `MAX_EXTENTS` exist from phase 3 and can
read this phase's output back.

## Decisions

**1. The emitter lives in `src/crates/vmdk`, as one function over a
kind parameter.** `build_split_descriptor(buf, offset, &SplitLayout)
-> usize`, with the subformat a field of the layout. The two
subformats differ in four places and nowhere else, so two functions
would be two copies of the `ddb.*` block and the extent loop. The
crate is already the home of the other three emitters, it is `no_std`
with no `alloc`, and both the host (phase 5) and the guest (phases 10
and 11) can link it.

**2. Keep the measure-then-write buffer contract; do not return a
`Result`.** The function returns the length the descriptor needs, and
copies only the prefix that fits. Three reasons: it is the contract of
all four existing builders, so a fifth with different semantics would
leave two contracts in one crate; it is what the one live host caller
is written against, with a comment saying so; and `snprintf` semantics
are a correct API rather than a workaround -- the hazard is a caller
that ignores the return value, not the contract.

**Amended at the back brief, 2026-11-24:** keep `snprintf` semantics
for now, and file an issue to revisit whether the interface should be
done the idiomatic Rust way -- a `Result`, or a writer the caller
feeds -- across all five builders rather than only the new one. Step
4e files it, and this plan does not pre-empt its answer.

The cost is real and worth stating. Today no caller can get it wrong,
because every descriptor is a fixed length that fits any buffer anyone
would pass. A split descriptor is as long as its extent count makes
it, so from this phase on a caller that does not measure writes a file
that names some of its extents and no error is raised. The mitigations
are in the definition of done: the written length must equal the
measured length at 1, 2, 17 and 1000 extents, a test must assert that a
one-byte-short buffer produces the needed length rather than a panic or
an overrun, and phase 5's plan must state that its caller measures
first.

Note what "truncated" means here, because the first draft of this plan
got it wrong and step 4b's tests corrected it. A `put` copies all of
its bytes or none of them, so a short buffer does not hold the first
`len - 1` bytes of the descriptor: it holds every line that fitted
whole, and nothing of the line that did not. That is the behaviour of
all four existing builders too. It makes a silently truncated
descriptor *more* likely to parse as valid, not less, since it ends at
a line boundary -- which is a reason the caller must measure, not a
reason to prefer byte-level truncation.

**3. Geometry is computed from the size, not passed in.**
`split_layout(virtual_size_bytes, kind) -> SplitLayout` yields the
total sector count, the extent count, each extent's sector count, and
the CHS triple. All of it is a pure
function of the size, every pinned case agrees, and letting a caller
supply `cylinders` invites two callers deriving it differently -- which
is how instar came to have four descriptor builders. `heads` and
`sectors` are the constants 16 and 63; `cylinders` truncates and is
not clamped, which the 33 GiB fixture witnesses at 68656.

**4. Emit the 6.2.0-and-later `ddb.*` set, including
`ddb.toolsVersion = "2147483647"`, unconditionally.** This adopts the
master plan's recommendation on open question 3.

Reasoning: 76 of the 80 pinned builds write that set, the pinned 10.2.0
build is the oracle this plan targets throughout, and the descriptor is
compared byte-for-byte, so omitting six lines means either a weaker
oracle or a documented divergence on output that does not exist yet.

Not making it conditional on a version, although instar has a
version-profile mechanism: `resolve_output_profile` in
`src/vmm/src/main.rs:14063` says it is "shared by every subcommand
whose output is version-dependent -- `info`, `map` and `snapshot`".
That mechanism shapes *reported* output, not written image bytes, and
`create` does not take `--qemu-version` at all. Inventing a first
image-bytes version knob for six lines would be a larger change than
this phase, and a worse default than matching current qemu.

The cost: instar's VMDK descriptors become inconsistent with each
other -- the new subformats carry `ddb.*`, the three old ones do not --
until the retrofit lands. The master plan already accepts this and
assigns the documentation to phase 17. A second, smaller cost: output
for a 6.0.x or 6.1.x target carries one line that version never wrote.
Nothing in instar targets a qemu version for written bytes today, so
there is nothing for that to break, but it is a divergence and phase 17
should list it rather than only the `ddb.*` inconsistency.

**5. The emitter derives extent names from a decomposed stem.** It
takes `prefix` and `postfix` byte slices -- the output basename split
at its last dot, exactly as `vmdk_flat_extent_path` splits it -- and
formats `<prefix>-s001<postfix>` itself. It is handed neither a path
nor a list of names.

Reasoning: the name is a qemu-parity fact that appears in the bytes
this function emits, so it belongs next to them and should be testable
without a filesystem. The *path* the caller opens, and what the caller
does about a name that is not UTF-8 or that holds a quote, stay with
the caller -- which is open question 4, and phase 5's.

**6. The emitter imposes no extent-count limit.** The index formats at
minimum width three, zero-padded, and runs past three digits unpadded,
matching the measured `image-s1000.vmdk`. Any instar limit is open
question 5 and belongs where a refusal can be raised before a file is
opened, which is phase 5.

There is a second consequence, found by step 4b: the emitter loops once
per extent, so a `u64::MAX` virtual size means 2^33 iterations. It
neither panics nor overflows, but it would run for hours. Phase 5's
caller therefore has to bound the size *before* it measures, not only
before it writes -- the measuring pass walks every extent too. That is
the same refusal open question 5 asks phase 5 to decide, so it costs
phase 5 nothing extra, but it is a correctness requirement on the
caller rather than a nicety.

Note the asymmetry this leaves, deliberately: instar will be able to
*write* a descriptor with more extents than it can *read*, because the
read side is capped at `MAX_CHAIN_EXTENTS` (64) by the size of the
guest config page. Phase 5 is where that becomes a user-visible
decision and it is listed in the master plan's Future work. It is
recorded here so phase 5's planner does not meet it as a surprise.

**7. `CID=fffffffe`, as every other instar VMDK.** qemu writes a
random CID, which cannot be matched; the pin replaces it with a
placeholder for exactly that reason. Parity with instar's own output is
worth more than a pretence of parity with qemu's randomness, and the
divergence is already documented in `docs/quirks.md` against #566.

**8. The oracle is the pin file, copied into Rust literals, with a new
Python guard tying the two together.** The literals follow the house
pattern and its stated reason. The guard is a Python test that, for
every `vmdk-multi-*` pin case, re-derives the descriptor text the
6.2.0-and-later group records and asserts it appears verbatim in the
Rust test source.

Reasoning: without it, the phase's whole oracle is a copy a sub-agent
made, and the most likely way for this phase to be wrong is for the
same agent to derive both the emitter and its expectations from the
same misreading. The guard is the only part of the verification that
an emitter bug cannot also satisfy. Scoped to the split literals;
extending it to the VHDX literals it would also fit goes to Future
work rather than growing this phase.

**9. No user-visible change, so no user documentation beyond format
reference.** `docs/vmdk/vmdk-extents.md` gains the split naming rule
and the geometry derivation, because they are facts about the format
that a human reading that page wants. `docs/quirks.md`,
`docs/create.md` and `docs/format-coverage.md` change in phase 5, when
a split subformat first becomes reachable from the command line.
There is no changelog to add an entry to: the repository keeps none,
and a `no-changelog` pre-commit hook over `git ls-files` stops one
coming back, because older plans in `docs/plans/` still say to write
one. Release notes go on the GitHub release; see "Releases" in
`docs/development.md`.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | **The split layout.** In `src/crates/vmdk/src/lib.rs`, add `pub enum SplitKind { Sparse, Flat }`, `pub struct SplitLayout` and `pub fn split_layout(virtual_size_bytes: u64, kind: SplitKind) -> SplitLayout`. The layout carries `total_sectors` (`virtual_size_bytes` rounded up to 512), `extent_count`, `last_extent_sectors`, `cylinders`, `heads`, `sectors` and `kind`. Extents are 4194304 sectors (2 GiB) each except the last, which is the remainder; a size that is an exact multiple of 2 GiB gives a whole number of full extents and no short one (`vmdk-multi-sparse-2G` pins one extent of 4194304, not two). `heads = 16`, `sectors = 63`, `cylinders = total_sectors / (16 * 63)` truncating, **not clamped to 65535**. Add a `sectors_of_extent(index) -> u64` accessor so callers never re-derive the short last extent. The crate is `#![no_std]` with no `alloc` and no panics: no indexing that can panic, no `unwrap`, and the division is by a non-zero constant. Unit tests must cover, with the pin case named in a comment on each: 1 MiB (1 extent, 2048 sectors, cylinders 2), 2 GiB (1, 4194304, 4161), 2049 MiB (2, last 2048, 4163), 4 GiB (2, 4194304, 8322), 5 GiB (3, last 2097152, 10402), 21 GiB (11, last 2097152, 43690), and 33 GiB (17, last 2097152, **cylinders 68656**, from `custom/format-coverage/vmdk-split-sparse-33g.vmdk`, which is the witness that cylinders is not clamped). Also test a size of 0 and a size that is not a multiple of 512, and say in a comment what the chosen answer is and that no pin covers it. Commit subject: `Derive a split VMDK's extent and CHS geometry.` |
| 4b | medium | sonnet | none | **The emitter.** In the same file, add `pub fn build_split_descriptor(buf: &mut [u8], offset: usize, layout: &SplitLayout, prefix: &[u8], postfix: &[u8]) -> usize`. Follow `build_flat_descriptor` at `src/crates/vmdk/src/lib.rs:266` exactly for structure and for the `put` closure: advance the cursor past the end of the buffer, copy only when the write fits, return the needed length. **Do not return a `Result` and do not change the existing builders** -- see decision 2; the host caller at `src/vmm/src/main.rs:16988` measures with an empty slice and depends on this. Emit, in order: the five fixed header lines with `CID=fffffffe` and `parentCID=ffffffff`; `createType="twoGbMaxExtentSparse"` or `..Flat`; a blank line; `# Extent description`; one line per extent, `RW <sectors> SPARSE "<name>"` or `RW <sectors> FLAT "<name>" 0`; a blank line; `# The Disk Data Base`; `#DDB`; a blank line; then `ddb.virtualHWVersion = "4"`, `ddb.geometry.cylinders = "<n>"`, `ddb.geometry.heads = "16"`, `ddb.geometry.sectors = "63"`, `ddb.adapterType = "ide"`, `ddb.toolsVersion = "2147483647"`. Note the blank line after `#DDB`, which the existing single-extent emitters do not have because they stop there. Extent names are `<prefix>-s<NNN><postfix>` for sparse and `-f<NNN>` for flat, where `<NNN>` is the 1-based index formatted at **minimum** width 3, zero-padded, running to four or more digits past 999 (qemu writes `image-s1000.vmdk` verbatim; measured in phase 1 on four builds). Reuse `format_u64` for the sector counts and cylinders; add a small zero-padded formatter for the index rather than widening `format_u64`. Unit tests here cover only mechanics, not parity, which is step 4c's job: that the measured length equals the written length for 1, 2, 17 and 1000 extents; that a buffer one byte short returns the same needed length and leaves the first `len - 1` bytes equal to the full output's prefix, with no panic; and that a zero-length buffer writes nothing. Commit subject: `Emit twoGbMaxExtent VMDK descriptors.` |
| 4c | medium | sonnet | none | **Parity against the pin.** Add unit tests that compare `build_split_descriptor`'s output byte-for-byte with what qemu writes. **Derive every expected byte from `tests/golden/qemu-create-pins.json`, and do not read the emitter's output to decide what to expect** -- an expectation copied from the thing under test proves nothing. For each of the fourteen `vmdk-multi-*` cases (twelve distinct size-and-subformat pairs, plus the two 5 GiB existing-extent cases, whose descriptors must come out identical to the plain 5 GiB ones -- assert that rather than skipping them), take the last version group (6.2.0 to 10.2.0), substitute `image` for `$STEM`, `fffffffe` for `$CID` and the case's `extent_names` for `$EXTENT<n>`, and assert the emitter produces exactly that text for the case's size with `prefix = b"image"` and `postfix = b".vmdk"`. Follow the pattern and the comment style of `src/crates/create/tests/round_trip.rs:2180-2250`: a table of cases, each row naming the pin case it came from, and a comment stating that the crate tests cannot read the pin file because the workspace has no JSON parser, so the literals are copied by hand and `tests/test_vmdk_split_descriptor_pins.py` guards the copy. Add one more test that is not from the pin: emit the 17-extent 33 GiB sparse descriptor with `prefix = b"vmdk-split-sparse-33g"` and compare it with the committed descriptor of `custom/format-coverage/vmdk-split-sparse-33g.vmdk`, with its CID line replaced -- that fixture is qemu-written and is the only committed witness past 16 extents and past 65535 cylinders. Finally, round-trip: feed the emitter's output to phase 3's `parse_descriptor_extents` and assert the extent count, each kind, each sector count and each filename come back. Commit subject: `Pin the split descriptors against qemu's.` |
| 4d | medium | sonnet | none | **Guard the copy.** Add `tests/test_vmdk_split_descriptor_pins.py`: for every pin case whose name starts `vmdk-multi-`, read the last version group's `descriptor` from `tests/golden/qemu-create-pins.json` via `helpers.qemu_pins`, build the text with the same substitutions step 4c used, and assert that text appears verbatim in the Rust test source that holds the literals. Read the `.rs` file as text; no Rust is run. The failure message must say which case drifted, print a unified diff, and name both remedies: re-copy the literal, or re-run `tools/sweep-qemu-create.py` if qemu is what changed. Follow `tests/test_qemu_create_pins.py` for `REPIN_HINT` and for the wording of a remedy, but take the class shape from `TestQemuPinCases` in `tests/test_helpers.py`: a plain `unittest.TestCase` that imports `helpers.qemu_pins` and reads the pin file, with no `InstarTestBase` and so no testdata dependency, which a source-only check does not need. **No registration is needed and `tests/manifest.json` is not the place to look** -- it is the test *image* manifest and names no test module. Discovery is stestr's, and the `integration-core` CI job is exclude-based so it absorbs a new module automatically. What turns a gap into a failure is `tools/ci/check-test-partition.py`; run it and report that the new module is claimed. Also assert, in the same file, that the pin's own `vmdk-multi-sparse-5G-existing-extent` and `vmdk-multi-flat-5G-existing-extent` descriptors are byte-identical to the plain 5 GiB case of the same subformat. Step 4c shares one constant between those rows, so only a test that reads the pin can check the sharing is still true; asserting it in Rust compares a constant with itself. This test is the only part of the phase's verification that a wrong emitter cannot also satisfy, so it must fail if a literal is edited -- prove that by editing one digit, observing the failure, and restoring from a copy rather than with `git checkout`. Commit subject: `Guard the pin literals against drift.` |
| 4e | low | haiku | none | **File the deferred work and document the rules.** Two GitHub issues. First, the `ddb.*` retrofit for `monolithicSparse`, `streamOptimized` and `monolithicFlat`, which open question 3 asks this phase to file: say that qemu writes the block for every subformat including `monolithicFlat` (pin case `vmdk-flat-64M`, `cylinders = "130"`), that instar omits it everywhere, that the retrofit changes the bytes of every VMDK instar has ever written and churns every VMDK baseline, that `-o adapter_type`, `hwversion` and `toolsversion` are accepted and ignored today, and that `docs/quirks.md`'s "VMDK descriptors omit qemu's DDB lines" section and `docs/create.md`'s Future work entry both have to be rewritten when it lands. Second, the four duplicated descriptor builders listed in this plan's survey table, with `format_u64` / `format_u64_decimal` as the concrete duplication and the create crate's own comment as the reason the fourth exists. Third, the buffer contract: all five builders return the length the descriptor needs and copy only what fits, which is `snprintf` semantics rather than anything idiomatic in Rust, and a caller that does not measure first silently writes a truncated descriptor -- now reachable, because a split descriptor's length is its extent count. Say what the alternatives are (a `Result`, or a writer the caller feeds), that the existing host helper's only check is a `debug_assert_eq!` that release builds drop, that `src/vmm/src/main.rs`'s measure-then-write caller is the one live consumer, and that changing it touches every VMDK instar writes. Do not propose a fix in the issue; it is a decision to revisit, raised at this phase's back brief. Then add all three issue numbers to the master plan's Future work, add a Future work entry for extending step 4d's guard to the VHDX literals in `src/crates/create/tests/round_trip.rs`, and add the split naming rule and the geometry derivation to `docs/vmdk/vmdk-extents.md` (naming: qemu's `filename_decompose` plus `-s%03d` / `-f%03d`, minimum width three; geometry: 2 GiB extents with a short last one, heads 16, sectors 63, cylinders a truncating divide by 1008 with no 65535 clamp, witnessed at 68656). Do not create or edit a changelog file: this repository keeps none and the `no-changelog` hook refuses one wherever it is put. Commit subject: `File the DDB retrofit and document the rules.` |
| 4f | medium | sonnet | none | **Mutation-check the guards.** Extend `tools/mutate-differencing.sh` with one case per property this phase claims a test enforces, following the existing cases' shape (`rust_case`, a `--check-patterns` entry, and `EXPECTED_CASES` bumped to match): drop the `ddb.toolsVersion` line; emit `-f%03d` for a sparse layout; format the index at width 2; stop clamping the last extent to the remainder (make every extent 4194304 sectors); and remove the blank line after `#DDB`. Each must be killed by a named test, and the report must say which test caught each and that no mutant was counted as killed by a skip. Run every mutant under `timeout`. Restore from a copy, never with `git checkout` on a directory. Then re-run the suites the master plan's "Verification that is not optional" requires: `make test-rust`, the integration selection that covers the new Python test, and `scripts/check-binary-sizes.sh` -- the last because this phase adds code to a crate linked into guest operations, and the before and after numbers go in the execution notes. Classify the integration run with `tools/ci/check-stestr-run.sh`, not by reading stestr's totals block. Commit subject: `Mutation-check the descriptor emitter.` |

## Risks and mitigations

**The emitter ships with no caller, so a mistake waits for phase 5.**
Mitigated by making the parity test the point of the phase rather than
an afterthought: step 4c compares against fourteen pinned cases plus a
qemu-written 17-extent fixture, so a wrong byte fails in this phase's
own `make test-rust`, not in phase 5's integration run. The management
session re-runs one case by hand against the pin file before accepting
4c.

**The oracle is a hand copy, and the same sub-agent could get the
emitter and its expectations wrong the same way.** Mitigated by step
4d, which derives the expectation from the pin file independently of
any Rust, and by running 4c and 4d as separate sub-agents with 4c's
brief forbidding it to derive expectations from the emitter's output.
Checked by the management session: edit one digit of a literal and
confirm 4d fails naming that case.

**The buffer contract is a loaded gun once descriptors are
unbounded.** Mitigated by decision 2's three tests, and by a note this
plan leaves for phase 5: its caller measures first, and the management
session checks that before accepting phase 5's step that opens the
descriptor file. Not fully mitigated, and honestly so -- the existing
host helper's check is a `debug_assert_eq!`, which release builds drop.
Changing that is the duplicated-builders issue's business, not this
phase's.

**Scope pull toward unifying the four builders.** Mitigated by making
it an issue in step 4e and by naming it in Scope's Out list, so a
reviewer who raises it has an answer in the plan rather than a round of
review.

**The 6.0.x/6.1.x divergence decision 4 accepts could be read as an
oversight.** Mitigated by stating it in the plan, in the commit
message for 4b, and in the Future work entry phase 17 will document
from.

## Definition of done

Falsifiable; each is checkable by someone who did not do the work.

1. `split_layout` returns the pinned extent count, last-extent sector
   count and cylinder value for all seven sizes listed in step 4a,
   including `cylinders = 68656` for 33 GiB, and each test names the
   pin case or fixture it came from.
2. `build_split_descriptor`'s output is byte-identical to the
   6.2.0-and-later pinned descriptor for all fourteen `vmdk-multi-*`
   cases, with `$STEM`, `$CID` and `$EXTENT<n>` substituted.
3. Emitting the 33 GiB sparse descriptor with that fixture's stem
   reproduces `custom/format-coverage/vmdk-split-sparse-33g.vmdk`'s
   committed text, CID line excepted.
4. `parse_descriptor_extents` reads the emitter's output back and
   agrees on the extent count, every kind, every sector count and
   every filename.
5. The measured length equals the written length for 1, 2, 17 and 1000
   extents. A buffer one byte short returns the same needed length and
   does not panic, and the bytes it holds are a prefix of the full
   output ending at the last write that fitted **whole** -- a `put` is
   all-or-nothing, so the final line is skipped entirely rather than
   truncated, and the byte past what was copied is left as the caller
   had it. A zero-length buffer, and an offset past the end of the
   buffer, write nothing and still measure.
6. The 1000-extent descriptor's thousandth extent line reads `RW
   4194304 SPARSE "image-s1000.vmdk"`, matching phase 1's measurement
   verbatim.
7. The pin-against-source check passes, asserts the two existing-extent
   cases' pinned descriptors equal the plain 5 GiB ones, and fails when
   one digit of one Rust literal is edited, naming that case. The edit
   was made, the failure observed, and the file restored from a copy.
   This started as `tests/test_vmdk_split_descriptor_pins.py` and is now
   `tools/ci/check-vmdk-split-pins.py`; the merge queue round below says
   why it had to move, and the move is what the item means from here on.
8. The existing emitters are unchanged: `git diff` touches no line of
   `build_descriptor`, `build_flat_descriptor`,
   `build_streamoptimized_descriptor` or
   `build_vmdk_descriptor_with_backing`.
9. No `create`, `convert`, CLI or `CreateConfig` change: `git diff
   --stat` lists no file under `src/operations/`, and `shared`'s
   `vmdk_subformat` still carries three values.
10. Three issues exist, with the content step 4e specifies, and all
    three numbers appear in the master plan's Future work.
11. `docs/vmdk/vmdk-extents.md` states the naming rule and the
    geometry derivation, including that cylinders is not clamped.
12. Five mutants, one per property in step 4f, each killed by a named
    test, none counted as killed by a skip or a timeout that was
    really a hang.
13. `scripts/check-binary-sizes.sh` passes, with the before and after
    numbers in the execution notes.
14. `pre-commit run --all-files` passes with the new files `git add`ed
    first, and `make test-rust` reports 0 failures with the count
    checked. The integration selection covering the new Python test is
    classified by `tools/ci/check-stestr-run.sh`, not by reading
    stestr's totals block or trusting an exit code.

## Back brief

Answered 2026-11-24 (AEDT); see *Execution notes* for the answers.
Questions as put:

1. **Open question 3, which this phase turns on.** Decision 4 adopts
   the master plan's recommendation: emit the 6.2.0-and-later `ddb.*`
   set unconditionally on the new subformats, and file the retrofit of
   the old three as an issue rather than doing it. Confirm, or say
   which part to change. The two live alternatives are to emit no
   `ddb.*` at all, which keeps instar's four descriptor forms
   consistent with each other and takes a documented divergence from
   qemu on brand-new output, and to retrofit all four subformats at
   once, which churns every VMDK baseline and wants its own
   differential-fuzz exposure.
2. **Decision 2's buffer contract.** Keeping `snprintf` semantics
   rather than returning a `Result` is the choice a reviewer is most
   likely to argue with, because it keeps a silent-truncation path
   alive at exactly the moment descriptors stop being a fixed length.
   Confirm, or say that the new emitter should return a `Result` and
   accept two contracts in one crate until the duplicated-builders
   issue unifies them.
3. **The guard in step 4d.** A Python test that reads a Rust source
   file is new in this repository. Confirm that is acceptable, or say
   to drop it and accept the hand copy as the house pattern does
   today.
4. **Decision 6's asymmetry.** instar will be able to write more
   extents than it can read, with the write side uncapped here and the
   read side capped at 64 by the guest config page. Confirm that
   deciding instar's write-side limit is phase 5's, under open
   question 5, rather than something this phase should settle.

## Execution notes

Implementation began 2026-11-24 (AEDT), from commit `8adaf05a` on
`multi-extent-vmdk-phase-04-descriptor`.

**Back brief, answered 2026-11-24.** Three of the four questions were
answered as the plan recommended: the new subformats emit the
6.2.0-and-later `ddb.*` set and the retrofit of the existing three is
filed rather than done (open question 3, now resolved); step 4d's
Python guard over the Rust literals goes in; and instar's write-side
extent limit stays phase 5's decision under open question 5.

The fourth answer changed the plan. The `snprintf` buffer contract
stays for now, as decision 2 proposed, **and** step 4e files a third
issue to revisit whether the interface should be done the idiomatic
Rust way across all five builders. Decision 2 and the definition of
done are amended above. The phase does not pre-empt that issue's
answer: this emitter ships with the contract the other four have.

Steps run in the order the table gives them, sequentially, because
every one of 4a to 4d writes to the same worktree and 4f mutates it.

**Step 4a, 2026-11-24.** `split_layout`, `SplitLayout`,
`sectors_of_extent` and the two subformat kinds, with eight tests.
Verified by the management session rather than from the report: every
geometry literal was re-derived from the pin file and from the 33 GiB
fixture independently of the code, and `make test-rust` was re-run on
the committed tree because the sub-agent had run it before its own
rustfmt fix. 3104 passed, 0 failed, 3 ignored over 83 test binaries;
`pre-commit run --all-files` 9 of 9. Three additions beyond the row
were kept: public extent-size, heads and sectors constants that the
emitter needs; `sectors_of_extent` as a method; and tests for the
extent sectors summing to the total, the subformat passing through,
and a `u64::MAX` size not overflowing.

**Step 4b, 2026-11-24.** `build_split_descriptor` and a private
zero-padding formatter, with five mechanics tests. 3109 passed, 0
failed, 3 ignored over 83 test binaries on the committed tree, re-run
by the management session rather than taken from the report;
`pre-commit run --all-files` 9 of 9. Verified independently of the
report: the emitted 5 GiB sparse descriptor is byte-identical to the
426 bytes `vmdk-multi-sparse-5G` records, compared by extracting the
Rust literal from the test and the expectation from the pin file, and
both diff hunks are pure insertions so the three existing builders
are provably untouched.

**Two more facts this plan had wrong, corrected above at source.**
Definition of done item 5 and decision 2's mitigation both described a
short buffer as holding the first `len - 1` bytes of the descriptor.
It does not: a `put` is all-or-nothing, so the last line is skipped
whole. Step 4b's test asserts the real behaviour and says so in a
comment rather than bending to the plan's wording, which is what
surfaced it. Had the plan not been corrected, item 5 would have been
ticked at the end of the phase on a false reading of a passing test.

Decision 6 also now records the cost of leaving the emitter
unbounded, which step 4b's author raised: the emit loop runs once per
extent, so phase 5 must bound the virtual size before the *measuring*
pass, not just before the write.

**Step 4c, 2026-11-24.** Twelve descriptor constants, a fourteen-row
parity table, the 33 GiB fixture comparison and a round-trip through
`parse_descriptor_extents`. The emitter agreed with every pinned case
and with the fixture first time; nothing was adjusted to make a test
pass.

Verified independently of the report: all fourteen pinned descriptors,
rebuilt from the pin file, and the 33 GiB fixture's own 1202 bytes
appear verbatim in `src/crates/vmdk/src/lib.rs` once `\"` is
unescaped, which is the single substitution step 4d's guard performs.
3113 passed, 0 failed, 3 ignored.

**Review removed one test and corrected a fifth stale case count.**
`split_descriptor_existing_extent_cases_match_the_plain_case` compared
`PIN_SPARSE_5G` with itself, because the table deliberately shares one
constant between the plain and existing-extent rows, so it could not
fail. It is deleted; the table's doc comment now says where the real
check lives, and step 4d above gained it, since only a test that reads
the pin can make it. This plan asked for that assertion without
noticing the crate cannot read the pin -- the same oversight as the
`ten` case count, which step 4c found still standing in the risks
section after the earlier sweep fixed the other four.

**Four facts this plan had wrong, corrected above at source.** Step
4a found the first: decision 3 wrote `split_layout` as taking
`prefix` and `postfix`, which contradicts the 4a row and would put
extent naming in two places. The row was right and the sub-agent
followed it. The other three were found by checking the later rows'
premises before dispatching them. There are **fourteen**
`vmdk-multi-*` pin cases, not the ten this plan claimed in three
places. `tests/manifest.json` is the test image manifest and declares
no test modules, so step 4d's instruction to register there was
wrong; stestr discovers the module, the exclude-based
`integration-core` job absorbs it, and `tools/ci/check-test-partition.py`
is what makes a gap fail. And step 4d now names
`TestQemuPinCases` in `tests/test_helpers.py` as its pattern: a plain
`unittest.TestCase` reading the pin with no testdata dependency,
where the brief would otherwise have led to `InstarTestBase`.

**Step 4d, 2026-11-24.** `tests/test_vmdk_split_descriptor_pins.py`,
four tests, run directly rather than through Docker because the module
imports only the standard library and `helpers.qemu_pins`. All
fourteen literals matched first time, and the fixture check did not
skip on this host.

The guard was proved to fail twice, on different constants: by its
author on the 21 GiB cylinder count, and in review on the 2049 MiB
case's short last extent, each naming the right case with a readable
diff. Both restorations were `cp` from a copy, verified with `cmp`,
never `git checkout` -- there was uncommitted work in the tree
throughout.

Scope grew by one check beyond the row, deliberately.
`QEMU_SPLIT_SPARSE_33G` is hand-copied from the testdata fixture and
nothing guarded *that* copy either, which is the same gap decision 8
exists to close and which the row had simply missed. It skips when the
testdata clone is absent, which is legitimate because testdata is a
separate git-LFS repository; a skip would not be acceptable for the
pin checks, since the pin file is committed here.

What the guard still does not check is which literal each table row
uses, so a row pointing at the wrong constant would pass it. The
crate's parity test covers exactly that, because the emitter's output
for one case's size cannot match another case's text. The two together
close it and neither does alone.

**Step 4e, 2026-11-25 (AEDT).** Run by the management session rather
than a sub-agent, which is a departure from the step table. The step
is three GitHub issues whose text is public the moment it exists, two
paragraphs of format reference, and three Future work entries that
have to cite issue numbers which do not exist until the issues are
filed. Briefing that was more work than doing it, and the issue
wording went to the operator for review first; he approved all three
as drafted.

Filed: [#964](https://github.com/shakenfist/instar/issues/964), the
`ddb.*` retrofit open question 3 asks for;
[#965](https://github.com/shakenfist/instar/issues/965), the five
descriptor builders; and
[#966](https://github.com/shakenfist/instar/issues/966), the
`snprintf` interface the operator asked to have revisited. All three
are in the master plan's Future work, together with a fourth entry for
extending step 4d's guard to the VHDX literals, which are copied from
the same pin file with nothing checking them.

`docs/vmdk/vmdk-extents.md`'s *Split Image Layout* section gained the
extent naming rule, the zero-padding floor, the geometry derivation
with its missing 65535 clamp, and the measurement that a split-sparse
extent's own 20 reserved descriptor sectors are left zero. The
*instar support* section was deliberately left alone: it describes
input variants, and this phase changed nothing a user can reach. No
changelog entry, because the repository keeps none and a hook refuses
one.

**Step 4f, 2026-11-25 (AEDT).** Five mutation cases against the `vmdk`
package, taking `tools/mutate-differencing.sh` from 172 to 177, and
`docs/testing.md` a seventeenth group describing them. All five killed:
`--list` shows the names, `--check-patterns` reports 177 of 177
landing with none drifted, and running the five by name exits 0 with
5 PASS, 0 FAIL, 0 BROKEN. Re-run by the management session, not taken
from the report.

The useful result is that one case did not kill its test when written.
`split_descriptor_index_padding_is_a_floor` asserted only that
`image-s999.vmdk` and `image-s1000.vmdk` appear, which both do at any
padding width, so the width-two mutant survived it and only the broad
golden comparison failed. The test now also asserts `image-s001.vmdk`
and `image-s099.vmdk` and kills it. Four of the five cases target a
narrow test rather than the fourteen-case parity table, so no mutant
rests on one broad comparison alone.

`scripts/check-binary-sizes.sh` passes with the figures unmoved:
core 70 KB (`.bin` 70672 bytes), convert 372 KB (381328 bytes), info
143 KB, compare 247 KB, bench 224 KB, every other operation under 125
KB, all against the 768 KB cap. They did not move because no operation
calls the new emitter, so the linker drops it. That is the honest
answer rather than evidence the emitter is cheap; the real cost lands
in phase 5, when a caller appears, and that is where this check earns
its place.

Two corrections to the sub-agent's account. It bumped
`docs/testing.md`'s headline count to 177 and left the sixteen group
counts beneath it summing to 172, which is the same off-by-one this
file carried when the descriptor-attachment group landed; the five
cases now have their own group with a paragraph saying what each
proves. And it read the harness's exit 2 on its own run as the
case-count check failing on a partial selection, which cannot be right:
`check_case_count` returns early whenever a selection is given. Exit 2
is `check_selection_matched`, so a name it passed matched no case --
deliberately a failure rather than an empty pass. With exactly the five
names the run exits 0.

## Verification

Run on 2026-11-25 (AEDT) against the branch tip, by the management
session.

| Check | Result |
|-------|--------|
| `pre-commit run --all-files` | 9 of 9 Passed |
| `make test-rust` | 3112 passed, 0 failed, 3 ignored, over 83 test binaries |
| `make test-container-core` | `MAKE_EXIT=0`, Ran 4231 in 546 s, 0 failures |
| `tools/ci/check-stestr-run.sh <log> in-output` | exit 0: 3284 of 4231 executed, 947 skipped, against 2767 discovered; no worker lost |
| `tools/mutate-differencing.sh --check-patterns` | 177 patterns, 177 land, 0 drifted |
| The five new mutation cases, by name | exit 0, 5 PASS, 0 FAIL, 0 BROKEN |
| `scripts/check-binary-sizes.sh` | pass, figures unmoved |
| `tests/test_vmdk_split_descriptor_pins.py` | 4 tests, all ran in the container, none skipped |

The integration run was classified by the repository's own guard rather
than by reading stestr's totals block, which prints `Failed: 0` for a
run that lost a worker. It was run with `INSTAR_REQUIRE_LIBVHDI=1`, so
a missing libvhdi oracle would have been red rather than a skip reading
as a pass; the devcontainer provides the oracle and both
`TestDifferencingLibvhdiOracle` tests ran green, which is why the
host's missing `vhdiinfo` does not matter here.

The phase's own diff is ten files, scoped against the merge base rather
than `develop`, which has moved five commits since the branch point: a
two-dot diff showed this phase reverting other people's merged work and
would have made the scope items unanswerable.

## Review round 1 (2026-11-25, AEDT)

The automated reviewer raised 2 fix, 3 document, 7 consider and 1
informational item on the pushed branch. Both fix items were real and
both were defects in what this phase added.

The one worth recording is the short-buffer test. Its final assertion
read a byte *outside* the slice the emitter was handed, so it held
whatever the emitter did, and its prefix comparison stopped one byte
short of the boundary. Definition of done item 5 -- already corrected
once during the phase, because a `put` is all-or-nothing rather than
byte-wise -- therefore still had nothing testing it. The assertion now
sits inside the slice, and a new mutation case makes a `put` that does
not fit copy the bytes that do: it kills the strengthened test and was
*survived* by the old one, which is the proof the old form could not
fail. That is three tests in this phase found claiming more than they
checked, all three in code written to demonstrate a contract rather
than to implement it, and in every case the fault was an assertion
placed where the behaviour it describes cannot reach it.

The other fix item was a documentation example that contradicted its own
rule: splitting `.hid` at its last dot leaves an empty prefix, so qemu
writes `-s001.hid` and not `.hid-s001`. The host's own
`vmdk_flat_extent_path` rustdoc had it right, which is where the rule
was taken from in the first place. A test for an empty prefix was added
with the correction.

Two rustdoc gaps were taken because phase 5's author will read the
signature and not this plan: that `prefix` and `postfix` are copied
verbatim into a quoted field so the caller must refuse a name holding a
`"`, CR or LF, and that both the measuring and the writing pass cost one
iteration per extent so the virtual size must be bounded before the
measure, not only before the write. Neither changes behaviour; both say
out loud what decisions 6 and 8 left to the caller.

Of the seven optional items, five were taken: the 33 GiB fixture guard
now fails rather than skipping when testdata is absent, matching
`InstarTestBase`, since it is the only check on that literal and a skip
reads as a pass; the flat subformat gained a narrow text test and a
mutation case for its trailing ` 0`, which was the last of the four
sparse/flat differences covered only by the broad parity table; the
private index formatter gained a direct test, and both of its defensive
branches were confirmed killable by hand-run mutants that were not kept,
because the branches are unreachable from the only caller and a
permanent case would pin code that cannot affect behaviour; and the
subformat tuple's fourth field was renamed from `start`, which read as a
start offset when it holds the end of the line.

The claim that `docs/testing.md`'s group counts do not sum was checked
and is wrong: the seventeen groups sum to exactly the headline. The
reviewer missed three groups (`compare` with two chains, `commit`, and
`bench` and `rebase`), which is 12 cases and accounts for its figures
precisely. The nested sub-totals are what make that easy, so the section
now carries a flat table of group against count; it is the suggestion
without the premise. The remaining two items were declined: the
testdata-root resolution now has five copies in `tests/`, of which this
phase wrote one, filed as #973; and making `SplitLayout`'s CHS fields
`u64` is cosmetic, since every cast is an infallible widening.

What was re-run for the round, on the round's own tree:

| Check | Result |
|-------|--------|
| `pre-commit run --all-files` | all hooks pass (rustfmt wanted two assertions split; applied) |
| `make test-rust` | 3115 passed, 0 failed |
| `tools/mutate-differencing.sh --check-patterns` | 179 patterns, 179 land, 0 drifted |
| The seven split-descriptor cases, by name | 7 PASS, 0 FAIL, 0 BROKEN; sources restored and `cmp`-checked |
| The same put-truncation case against the old assertion | FAIL, "the test still passed against mutated code" |
| `tests/test_vmdk_split_descriptor_pins.py` | 4 tests, none skipped |
| The same module with `INSTAR_TESTDATA_PATH` at an empty directory | 1 failure naming the missing fixture, no skip |
| `tools/ci/check-test-partition.sh` | PASS, halves cover the baseline exactly |

## Review round 2 (2026-11-25, AEDT)

No `fix` items: 2 document, 5 consider, 3 informational. Both document
items were taken, and three of the five optional ones.

The useful finding was that a zero-extent layout emits a descriptor the
crate's own `parse_descriptor_extents` refuses as `NoExtents`, so the
emitter can write something instar cannot read. Nothing was wrong --
`split_layout(0, ..)` has always been documented as this function's own
choice of the least surprising answer, no pin covers it, and there is no
caller -- but no test emitted a descriptor at that size, so the fact was
incidental rather than pinned. It is now a test, and the two bounds a
caller must apply beyond the cost bound (refuse a zero size; cap
`extent_count` at `MAX_EXTENTS` if the output is to be readable) are
stated in the rustdoc where phase 5's author will read them rather than
only in decision 6 here.

Two test gaps were taken on the same reasoning as round 1's. The
`offset` parameter had no test distinguishing it from a hardcoded zero:
every test passed either 0 or a value past the end of the buffer, so the
one piece of arithmetic the parameter exists for was unexercised. And
`split_descriptor_measured_length_equals_written_length` asserted
`buf[written] == 0`, which only caught an overrun because the buffer was
zero-filled and every emitted byte happens to be non-zero ASCII; it now
fills with 0xAA and checks the whole tail, so its teeth come from the
sentinel rather than from the initialiser. Both are the same class as
the three this phase has already found: an assertion that holds for a
reason other than the property it names.

Two items were declined. The reviewer proposed exporting an extent-name
safety predicate from the crate so phase 5 and the guest writers share
one rule instead of re-deriving the host's. The argument is good -- five
descriptor builders exist in this tree precisely because rules got
re-derived -- but where the refusal lives is open question 4, which is
the operator's to resolve and is explicitly phase 5's, so adding the
predicate here would pre-empt a decision this phase was told not to
take. **Note for phase 5:** when open question 4 is settled, the
predicate belongs beside the emitter in the vmdk crate rather than only
in `vmdk_flat_extent_path`, with a unit test per rejected byte, so the
guest-side writers in phases 10 and 11 call it rather than copy it. The
other declined item was loading the pin file once per module instead of
once per case, which the reviewer itself scoped as tidiness worth
milliseconds in a suite measured in minutes.

Round 2's prose finding was fair and is fixed: the group paragraph in
`docs/testing.md` called two different cases "the last" and listed the
seven out of script order. The seven sentences now run in the order
`tools/mutate-differencing.sh` invokes them, and only the last one says
so.

With no `fix` item live the loop exits here rather than going a third
round. Round 2 raised nothing about code round 1 added, which is the
trajectory that matters: the reviewer is a generator with no notion of
good enough, and the stopping rule is an empty `fix` column.

What was re-run for round 2:

| Check | Result |
|-------|--------|
| `pre-commit run --all-files` | all hooks pass |
| `make test-rust` | 3117 passed, 0 failed |
| `tools/mutate-differencing.sh --check-patterns` | 179 patterns, 179 land, 0 drifted |
| A hand-run mutant ignoring `offset` | killed `split_descriptor_writes_at_an_offset_within_the_buffer`; not kept, and the harness is back to 179 cases |

The offset mutant needed `let mut pos = 0usize` rather than `0`: the
untyped literal makes `saturating_add` ambiguous, and the harness scores
a mutant that will not compile as BROKEN rather than killed, which is
the right answer and not a pass. The strengthened overrun assertion was
not mutated, because a sentinel fill checked across the whole tail is
strictly stronger than a zero fill checked at one index -- there is no
behaviour the old form caught that the new one does not.

## Merge queue round (2026-11-26, AEDT)

Mikal enqueued the pull request on 2026-11-25. It was ejected the same
day: all seven `package-matrix` legs failed identically, two tests out
of 4496, both

```
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/src/crates/vmdk/src/lib.rs'
```

`tests/test_vmdk_split_descriptor_pins.py` read the Rust source through
`Path(__file__).resolve().parent.parent / 'src' / ...`.
`tools/test-package-functional.sh` copies `tests/` alone to
`/tmp/instar-tests` and runs it there against an installed package, so
that expression resolved to `/tmp/src/...`. It was the only test in the
suite that reached outside `tests/` without an environment guard;
`tests/base.py`'s build-tree binary fallback is the only other escape,
and the harness sets `INSTAR_BINARY_PATH=/usr/bin/instar` so it is never
reached there.

Nothing in review could have caught it, and neither could the pull
request. `package-matrix` is gated `github.event_name == 'merge_group'`,
so on a pull request the suite runs from the checkout where `src/`
exists. The first execution of the test in a relocated tree was the
queue. The workflow's own header documents this split, and #959 is the
same shape for a different cause: a convention the PR gate cannot see,
found by ejecting queued pull requests.

The fix moves the whole module to `tools/ci/check-vmdk-split-pins.py`,
beside the other source-level guards, and deletes it from `tests/`.
These were never runtime tests -- all four compared committed text with
committed text, and none ran instar. The guard takes `--source` and
`--testdata` so its own test can point it at mutated copies, and
`--no-fixture` for a job with no testdata clone. An absent clone is a
failure rather than a quiet pass, which is round 1's item 5 carried over
intact: the 33 GiB fixture is the only committed witness past sixteen
extents and past 65535 cylinders.

`tools/ci/check-tests-relocatable.sh` is what stops it recurring. It
matches the pathlib join form against the repository's top-level
directories, read from `git ls-tree` rather than written down, and run
on every pull request and as a pre-commit hook. Checked against the
tree as it stood before the fix, it reported exactly the one offending
line and nothing else.

| Check | Result |
|-------|--------|
| `tools/ci/check-tests-relocatable.sh` on the pre-fix tree | 1 problem: the `lib.rs` join in the pins test |
| `tools/ci/check-tests-relocatable.sh` after the move | clean |
| `tools/ci/test-check-tests-relocatable.sh` | 9 cases, all pass |
| `tools/ci/test-check-vmdk-split-pins.sh` | 7 cases, all pass |
| `tools/ci/check-vmdk-split-pins.py --no-fixture` | 14 descriptors match |
| `tools/ci/check-vmdk-split-pins.py --testdata ...` | 14 descriptors match, fixture literal matches |

Both guards were run against deliberately broken input rather than read:
a changed `createType` in a copy of `lib.rs`, a renamed fixture
constant, an empty directory for the clone, an absent source file, and
for the relocatable guard the #968 line itself, a sibling
`instar-testdata` path that must pass, a `tests/golden` path that must
pass, and a bitmap named `'src'` that must not match.

The rebase onto `c43b08f1` conflicted twice in `docs/plans/index.md`,
both times mechanically: this branch owns the multi-extent row and
develop owns the http-input row, whose phase 3 landed as #974 while this
pull request sat in the queue.

