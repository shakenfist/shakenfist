# Phase 1 — semantics pin, oracle selection, and the doc correction

Phase 1 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Establish that this plan can be validated at all, and decide the
things later phases would otherwise each decide differently.

Every write path instar has shipped was cross-validated against
qemu-img. This one cannot be: qemu-img creates neither
differencing VHD nor differencing VHDX, and reads both as though
the parent did not exist. Before an emitter is written, the plan
needs an external implementation that resolves a differencing
chain, or an honest statement that none is available and what we
are doing instead. That is this phase.

No source file under `src/` changes in this phase.

## Planning effort

High. The phase turns on format-spec interpretation and on a
go/no-go judgement that the rest of the plan depends on.

## Review effort

High for step 1a's oracle verdict and step 1b's structure pin --
the management session re-runs a sample of the measurements
rather than accepting the sub-agent's transcript. This repository
has a documented history of agents asserting plausible-but-wrong
qemu and format capabilities, and a wrong offset in a format
nobody else validates is exactly the error this plan is exposed
to.

Medium for the rest.

## Scope

In scope:

* Prove or disprove an external oracle for differencing VHD and
  VHDX chains, with recorded evidence.
* Pin the on-disk structures this plan will emit -- VHD dynamic
  header parent fields and locator table, VHDX parent locator
  metadata item -- against the specs and against real bytes.
* Pin qemu-img's and instar's current behaviour in
  `docs/quirks.md`.
* File the GitHub issue for the silent parent-ignoring read.
* Settle the master plan's remaining open questions.
* The `docs/create.md` correction, which has already landed as
  `a93615d`.

Out of scope:

* Any change under `src/`. The silent-read defect is filed here
  and fixed in phase 4; the temptation to fix it while it is in
  front of you is the thing this line exists to resist.
* Fixtures in `instar-testdata`. Phase 1 builds a throwaway chain
  in the scratchpad to test the oracle with; phase 2 owns the
  maintained generator.
* Documenting the structures in `docs/format-internals.md`. That
  page describes what instar implements, and in this phase instar
  implements none of it. Phase 10 writes it.

## What the survey found

The master plan's Situation section was written on 2026-09-05 and
re-verified line by line while planning this phase. Every claim in
it holds, with the file and line references below confirmed
against the tree at `d59cc40`:

* `src/crates/create/src/lib.rs:767` and `:919` reject a backing
  reference for vpc and vhdx respectively, each with a comment
  deferring the work as "too complex for phase 1" of
  `PLAN-create.md`.
* `src/crates/vhd/src/lib.rs:93` defines
  `DISK_TYPE_DIFFERENCING`; `:578` accepts it into `VhdState`;
  `:664-667` compute the per-block sector bitmap's size and
  `:766` skips past it to the payload. No bit of that bitmap is
  ever read.
* `src/crates/vhdx/src/lib.rs:462` derives `has_parent` from the
  file-parameter flags, `:517-519` discards the parent locator
  offset it just found, `:842` rejects differencing images, and
  `:654` skips the sector-bitmap BAT entries.
* The fixture
  `instar-testdata/custom/format-coverage/vhd-differencing.vhd`
  has `disk_type = 4` at footer offset 60 and zeroes in its
  parent unique id, parent unicode name (offset 576) and all
  eight locator entries (offset 1088). It is a type marker.

The survey did turn up one thing the master plan does not say,
and it is good news for phase 11: **the host already has generic
chain machinery, and it is not qcow2-only.**
`discover_backing_chain` (`src/vmm/src/main.rs:2416`) walks a
chain with circular-reference detection, a depth limit and a path
allowlist, and it already contains a non-qcow2 special case --
the VMDK flat-descriptor short-circuit that resolves
`parentFileNameHint`. The security knobs it uses,
`security.backing_path_allowlist` and `security.max_chain_depth`,
exist in `src/vmm/src/config.rs:65` and `:67`. Phase 11 extends
this function rather than writing one, and open question 5's
resolution rule is a description of what this code already does.

This finding has been added to the master plan's Situation
section as part of the planning commit, so the next reader does
not have to rediscover it.

## Decisions

1. **The oracle is libvhdi, subject to a written go/no-go.**
   Debian 13 packages `libvhdi-utils` (`vhdiinfo`) and
   `python3-libvhdi` from libyal, which implements VHD and VHDX
   parent chains. (Corrected after step 1a: this decision
   originally said `vhdiexport`, which does not exist. Debian's
   `vhdimount` is built without FUSE and refuses to mount, so the
   `pyvhdi` binding driven with an explicit `set_parent()` is the
   only composition path on this host.) It is accepted as this plan's oracle only
   if it passes both halves of step 1a: it resolves a chain
   *instar did not write* and reports the parent, and its
   composed export of a chain matches the content that chain was
   built to represent, byte for byte. Failing that, the fallbacks
   in order are a third-party-produced reference image used as
   ground truth, then structural-only assertions with the plan
   saying plainly that it has no content oracle.
2. **Phase 1's chain generator is disposable.** It lives in the
   scratchpad and its source is pasted into this plan's appendix
   when step 1a reports. Phase 2 lifts it into a maintained
   generator in `instar-testdata`. Blocking the go/no-go on a
   cross-repository, LFS-backed fixture review buys nothing.
3. **A generator we wrote cannot be the only input to the
   oracle test.** If libvhdi accepts our chain and we later emit
   the same misreading from `plan_vhd`, the oracle will have
   validated nothing. Step 1a must obtain at least one
   differencing image produced by something other than us --
   libyal's own test corpus, or a Hyper-V-produced sample -- or
   record that it could not, and downgrade the claim to "the
   oracle validates resolution, not conformance".
4. **The structure pin lives in this plan, not in `docs/`.**
   `docs/format-internals.md` documents what instar implements.
   Only the qemu-vs-instar divergences, which are true today, go
   to `docs/quirks.md` now.
5. **A differencing child must have a parent of its own format**
   (master plan open question 7, resolved yes). Hyper-V requires
   it, and emitting a chain no implementation can resolve is
   worse than a typed refusal. Phase 7 wires the error.
6. **Phase 1 writes no code.** The silent-read defect gets an
   issue number here and a fix in phase 4.
7. **This phase runs on the `vhd-differencing` branch, not a
   fresh phase branch.** The master plan is not yet on `develop`
   and phase 1's first deliverable is already on this branch, so
   a branch off `develop` would not contain the plan it
   implements. Phases 2 onward take their own branches off
   `develop` once this lands. The `Merged` cell for phase 1
   therefore records the merge commit of the pull request that
   carries the master plan and this phase together.

The decision most likely to be argued with is 2. Putting the
generator in the scratchpad means the evidence for the go/no-go
is reproducible only from this plan's appendix, not from a
checked-in script. The alternative -- open the testdata pull
request first -- makes the go/no-go wait on a review in another
repository, and if the answer is no-go, the fixtures were the
wrong thing to have built.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | worktree | Establish the oracle. Install `libvhdi-utils` and record `vhdiinfo -V`. Write a throwaway Python generator in the session scratchpad (NOT in any repository) that produces (i) a dynamic VHD base plus a differencing child referencing it, and (ii) a dynamic VHDX base plus a differencing child, each small (16 MiB) with known content in known sectors, some sectors present in the child and some only in the parent. The VHD child needs `disk_type = 4` at footer offset 60, the parent unique id at absolute offset 552, parent timestamp at 568, parent unicode name (UTF-16BE) at 576, and locator entries from 1088; both footer and dynamic-header checksums are ones-complement sums over the structure with the checksum field zeroed. Then: run `vhdiinfo` and `vhdiexport` on each child and record verbatim output; compare the exported composition against the content you intended, byte for byte, with `cmp`. Separately, obtain at least one differencing image produced by something that is not this script -- try libyal's published test corpus first -- and run the same commands on it; if you cannot obtain one, say so explicitly rather than working around it. Report a go/no-go against decision 1's criterion, the tool version, every command line, and the generator source for pasting into the plan appendix. Do not modify any repository file. |
| 1b | high | opus | none | Pin the structures, against the specs and against real bytes. For VHD: the dynamic header's parent fields and the eight 24-byte parent locator entries (platform code, data space, data length, reserved, data offset), which platform codes Hyper-V writes, how `W2ru` relative paths are encoded, and the checksum algorithm. For VHDX: the parent locator metadata item -- its header, the `parent_linkage` GUID's meaning, and the key/value entry encoding -- plus which file-parameter bits must be set. Verify every offset you state against the images step 1a produced (`xxd` output in the report) and against `src/crates/vhd/src/lib.rs` / `src/crates/vhdx/src/lib.rs` where they already parse the surrounding structure. Cite a spec section or a measurement for every claim; where the spec is ambiguous -- `parent_linkage` is the likely one -- say so and state the interpretation phase 6 should implement. Output is a section appended to this plan, not a docs change. |
| 1c | medium | sonnet | none | Write the `docs/quirks.md` section recording current behaviour, following the shape of the existing "QED read-refusal as policy" section (`:4038`). Content, all of it measured on 2026-09-05 against qemu-img 10.0.11 and instar built from `d59cc40`, and to be re-run and quoted verbatim rather than copied from this plan: `qemu-img create -f vpc -b base.vhd -F vpc child.vhd 16M` fails with "Backing file not supported for file format 'vpc'" and the vhdx equivalent likewise; `qemu-img info` on a differencing child reports a plain image and never mentions a parent; `instar convert -O raw` on the same child exits 0 and writes an image composed without the parent; `instar map` refuses (`src/operations/map/src/main.rs:459-462`) and `instar check` refuses only the VHDX case (`:1555`). State plainly that instar's read is qemu-parity and that both are wrong, and link the issue from step 1d. |
| 1d | low | sonnet | none | File one GitHub issue against shakenfist/instar for the silent parent-ignoring read: title names `convert` reading a differencing VHD as if it had no parent and exiting 0, body carries the reproduction against `instar-testdata/custom/format-coverage/vhd-differencing.vhd` (noting that fixture's own limitation), the qemu-parity observation, and a pointer to phase 4 of this plan as the fix. Label `bug`. Do not fix it. Report the issue number. |
| 1e | medium | opus | none | Settle the master plan's open questions using the evidence from 1a and 1b. Rewrite questions 2 through 7 in `docs/plans/PLAN-differencing.md` as RESOLVED with the answer, the evidence, and the date, in the style question 1 already uses. Question 2 takes step 1a's verdict; question 3 takes whichever locator entries the oracle actually required; question 4 is resolved "no flag", consistent with the unflagged vmdk/vhd/vhdx `resize` divergence; question 5 is resolved as a description of `discover_backing_chain`'s existing rules, citing `src/vmm/src/main.rs:2416` and the two config keys; question 6 is resolved yes; question 7 is resolved yes per decision 5. If any answer contradicts a phase description later in the table, fix that description too and say so in the commit. |
| 1f | low | sonnet | none | Close the phase. Set phase 1's row to Complete in the master plan Execution table and the phase list in `docs/plans/index.md`, add the issue number from 1d to the master plan's *Bugs fixed during this work*, run `pre-commit run --all-files`, and confirm `git diff --name-only develop...HEAD -- src/` is empty. Present the commits. |

## Result — step 1a, the oracle verdict

**GO.** libvhdi is this plan's oracle.

Run on 2026-09-05 by the step 1a sub-agent and sampled again in
the management session, per this phase's review effort. The
management session independently re-ran the third-party
`vhdiinfo`, both compositions with their controls, and the two
qemu-img reads; all agreed.

### Tool version and how it was installed

```
$ vhdiinfo -V
vhdiinfo 20240509
```

`libvhdi-utils`, `libvhdi1` and `python3-libvhdi`, all
`20240509-2+b1` from `deb.debian.org/debian trixie/main amd64`.
This host has no passwordless sudo, so the packages were fetched
with `apt-get download` and extracted with `dpkg-deb -x` into a
scratch prefix rather than installed. Same binaries, no host
mutation. Phase 15 will want them installed properly, or the same
prefix trick in the test harness.

### Two corrections to the phase's assumptions

* **`vhdiexport` does not exist.** libvhdi ships `vhdiinfo` and
  `vhdimount`, and Debian builds `vhdimount` without FUSE
  ("No sub system to mount VHDI format."). Composition therefore
  goes through the `python3-libvhdi` binding with an explicit
  `file.set_parent()`. Decision 1 has been corrected. Phase 15
  plans a Python harness, not a CLI export.
* **libvhdi never parses the VHD parent locator table.**
  `libvhdi_parent_locator*` is reached only from the VHDX
  metadata path; VHD resolution uses the `parent_unicode_name`
  field alone. So the oracle cannot validate instar's VHD locator
  table, and phase 8's assertions on it are structural only. This
  is the single biggest gap in the oracle and it is recorded here
  so phase 5 does not assume otherwise.

### A corpus we did not produce, for both formats

Decision 3's requirement was met. libyal's own test data carries
no differencing images, but `log2timeline/dfvfs`'s `test_data/`
carries Hyper-V produced differencing chains for both formats
(creator application `win `, creator version `0xa0000`, absolute
locators under `C:\Projects\dfvfs\test_data\`).

```
$ vhdiinfo fat-differential.vhdx
	Disk type		: Differential
	Media size		: 4.0 MiB (4194304 bytes)
	Identifier		: e9e37682-8227-44a7-8648-39cb9f52e5e6
	Parent identifier	: f88d4d92-6fcc-408d-9bef-9b7c89f15c89
	Parent filename		: C:\Projects\dfvfs\test_data\fat-parent.vhdx
```

The reported parent identifier equals the parent's own
`Identifier`. Sector-provenance analysis over the composed
4 MiB VHD chain found 8180 sectors identical in both, 2 taken
from the parent, 10 from the child, and **zero** taken from
neither -- so every composed sector is exactly one file's, and
sectors 0 and 65536 came from the parent despite sitting inside a
child-allocated block. libvhdi honours the per-block sector
bitmap.

The sub-agent then wrote its own decoder from the spec and
recomposed the same third-party bytes independently. Three of the
four chains agreed byte for byte with libvhdi; the fourth
disagreement is defect A below, and it reproduces on a Hyper-V
image rather than only on ours.

### Our generated chains

```
$ pyv compose.py vhdx-composed.raw vhdx-child.vhdx vhdx-parent.vhdx
wrote 16777216 bytes to vhdx-composed.raw
$ cmp vhdx-composed.raw vhdx-expected.raw ; echo $?
0
$ cmp vhdx-composed.raw vhdx-parent-only.raw ; echo $?   -> 1 (differ: byte 513)
$ cmp vhdx-composed.raw vhdx-child-only.raw  ; echo $?   -> 1 (differ: byte 1)
```

The VHDX child exercises all three BAT states at once:
`PARTIALLY_PRESENT` with a real 1 MiB sector-bitmap block,
`FULLY_PRESENT` shadowing the parent, and `NOT_PRESENT`. The VHD chain composes byte-exactly too, once its child sectors
are kept out of shared bitmap bytes -- see defect A. Re-run in
the management session on 2026-09-05, closing the gap step 1f
found in this section (the VHD transcript was in
`docs/quirks.md` but not here, where the definition of done
requires it):

```
$ vhdiinfo thirdparty/fat-differential.vhd
vhdiinfo 20240509
Virtual Hard Disk image information:
	Format			: VHD (version 1)
	Format version		: 1.0
	Disk type		: Differential
	Media size		: 4.0 MiB (4194304 bytes)
	Bytes per sector	: 512 bytes
	Identifier		: f84f1636-cd9e-9041-a69e-dcc2380e416a
	Parent identifier	: 5fa21a55-f394-aa4d-9958-1951a67d5540
	Parent filename		: C:\Projects\dfvfs\test_data\fat-parent.vhd

$ pyv compose.py /tmp/vhd-oracle.raw chains-aligned/vhd-child.vhd chains-aligned/vhd-parent.vhd
media_size=16777216 disk_type=4
parent_identifier=11111111-2222-3333-4444-555555555555
parent_filename=.../chains-aligned/vhd-parent.vhd
wrote 16777216 bytes to /tmp/vhd-oracle.raw

$ cmp /tmp/vhd-oracle.raw chains-aligned/vhd-expected.raw ; echo $?
0

$ cmp /tmp/vhd-oracle.raw chains-aligned/vhd-parent-only.raw ; echo $?
/tmp/vhd-oracle.raw chains-aligned/vhd-parent-only.raw differ: byte 4097, line 1
1
$ cmp /tmp/vhd-oracle.raw chains-aligned/vhd-child-only.raw ; echo $?
/tmp/vhd-oracle.raw chains-aligned/vhd-child-only.raw differ: byte 1, line 1
1
```

`vhdiinfo` on the Hyper-V child names a parent it did not write;
the composition of our own byte-aligned chain matches its
intended content exactly and differs from both the parent-only
and child-only controls.

The generated parents were validated against a second
implementation before the oracle was asked anything:
`qemu-img convert -f vpc -O raw` and `-f vhdx -O raw` of each
parent `cmp`-match the intended raw content.

### Two libvhdi defects, found and not fixed

* **A. VHD sector bitmap decoded with an unmasked shift.**
  `libvhdi_block_descriptor_read_sector_bitmap_data` computes
  `byte_value >> (7 - bit_index)` for VHD without masking to one
  bit, then treats a zero result as unallocated. Once any higher
  bit in a bitmap byte is set, every subsequent sector in that
  byte reads as present in the child. Measured on Hyper-V's
  `fat-differential.vhd` (bitmap byte 23 = `0xcb`, sector 186
  wrongly taken from the child) and reproduced deterministically:
  a probe placing one child sector at bit 7 of bitmap byte 1
  predicted exactly seven wrong sectors, and exactly sectors 9
  through 15 came back wrong. The VHDX branch is correct.

  *Impact.* None on `instar create`, whose child has a wholly
  unallocated BAT and no sector bitmaps at all. It matters in
  phase 8 and phase 15: fixtures with partially populated blocks
  must either keep parent-owned and child-owned sectors out of
  the same bitmap byte, or account for the bug in their expected
  output. Discovering this in phase 15 instead would look exactly
  like an instar bug.

* **B. VHDX `relative_path` key never matches.**
  `libvhdi_metadata_values.c` looks the key up with length 12 for
  a 13-character key. Measured by rewriting our child's locator
  with one key at a time: `relative_path` alone yields no
  "Parent filename" line at all, while `absolute_win32_path` and
  `volume_path` both resolve. A differencing VHDX carrying only a
  relative path resolves nothing in libvhdi, though `set_parent`
  works regardless since the harness supplies the parent.

Neither defect has been reported upstream. Whether to do so is
left for the operator; it is not on this plan's critical path.

### Structure findings that change what later phases must do

Every offset this plan already stated was confirmed against
Hyper-V bytes: footer disk type at 60 (`4` for differencing),
dynamic header at 512, parent unique id at 552, parent timestamp
at 568, parent unicode name at 576 in UTF-16 **big** endian, the
eight 24-byte locator entries from 1088, and both checksums as
the ones-complement of the byte sum with the field zeroed. Five
things it did not say, each measured:

1. **Locator platform data is UTF-16 little endian** -- the
   opposite of the parent unicode name field 512 bytes above it.
2. **`platform_data_space` is a byte count, not a sector count**,
   despite the Microsoft spec's wording. Hyper-V writes
   `data_space=4096, data_length=84` for a locator at `0x1000`
   whose neighbour is at `0x3000`; read as sectors that would be
   2 MiB and would overlap the BAT.
3. **VHDX `parent_linkage` is the parent's `DataWriteGuid`**,
   rendered as a braced GUID string in UTF-16LE, compared against
   the parent's *active header*, not its virtual disk id metadata
   item. This answers the master plan's ambiguity by measurement
   rather than by reading, and phase 6 implements it as fact.
4. **The two sector bitmaps have opposite bit order.** VHD is
   MSB-first (sector *i* is bit `7 - i%8` of byte `i/8`); VHDX is
   LSB-first. Phases 12 and 13 must not share a helper here
   without a parameter.
5. **A VHDX child needs a sector-bitmap BAT entry** at index
   `chunk_ratio` per chunk (state 6, `SB_BLOCK_PRESENT`) whenever
   any payload block is `PARTIALLY_PRESENT`.

Also: Hyper-V writes `parent_timestamp = 0` in a real
differencing VHD, and its footer CHS does not multiply out to
`current_size`. Neither field needs to be meaningful. And
libvhdi validates neither VHD checksum -- it reads a dfvfs image
whose footer and dynamic-header checksums are both wrong -- so
checksum correctness needs instar's own assertions.

### The existing fixture, confirmed a type marker

`vhd-differencing.vhd` has `disk_type = 4` at offset 60 and zeros
in the parent unique id, parent unicode name and all eight
locator entries. `vhdiinfo` agrees and prints no "Parent
filename" line at all, reporting
`Parent identifier: 00000000-0000-0000-0000-000000000000`;
reading it fails with "invalid file - missing parent file". Its
checksums are valid and its BAT has one allocated block, and its
companion `vhd-diff-base.vhd` is unreferenced by it.

### Behaviours for step 1c to record

* libvhdi **refuses** a differencing child with no parent
  attached ("invalid file - missing parent file"), where
  qemu-img and instar silently return the child's blocks.
* libvhdi **enforces** the parent GUID ("mismatch in
  identifier") when the wrong parent is attached.
* qemu-img 10.0.11 **refuses** differencing VHDX outright
  ("Operation not supported") while silently mis-reading
  differencing VHD -- the two formats are not symmetric in qemu,
  and instar matches that asymmetry (`VhdxState::init` rejects,
  `VhdState::init` accepts). Re-verified in the management
  session: `file(1)` identifies libvhdi's composition of
  `fat-differential.vhd` as an MBR boot sector and qemu's read of
  the same child as unidentifiable `data`, because sector 0 is
  missing.

### The issue filed (step 1d)

Filed as [issue #547](https://github.com/shakenfist/instar/issues/547),
"VHD differencing (disk type 4): convert -O raw silently composes
wrong data, exits 0", labelled `bug`. The reproduction is against
`instar-testdata/custom/format-coverage/vhd-differencing.vhd`,
noting that fixture's own limitation (it is a type marker with a
zeroed locator table and parent unique id, not a resolvable
chain); the body also records the qemu-parity observation above
and points at phase 4 of this plan as the fix. Not fixed here, per
decision 6. The master plan's *Bugs fixed during this work*
section carries the same number; it is not duplicated here beyond
this pointer.

## Risks and mitigations

* **Correlated error.** Our generator and our future emitter
  could share a misreading that libvhdi tolerates, making the
  oracle look sound while validating nothing. Mitigated by
  decision 3: step 1a must test a differencing image we did not
  produce, or downgrade the claim in writing. The management
  session checks specifically that this was done, because it is
  the step most likely to be quietly skipped.
* **A tolerant oracle.** libvhdi may resolve chains that Hyper-V
  would reject, so passing it is necessary and not sufficient.
  Mitigated by keeping step 1b's structural assertions as a
  second, independent check, and by phase 8 asserting structure
  as well as content.
* **Spec ambiguity on VHDX `parent_linkage`.** Mitigated by
  step 1b naming the interpretation explicitly so phase 6
  implements a decision rather than a guess, and by flagging it
  for revisit if a Hyper-V sample later contradicts it.
* **A buggy oracle in one specific place.** libvhdi's VHD
  sector-bitmap decoder is wrong (defect A above), so a
  content-exact assertion against a VHD chain whose child and
  parent sectors share a bitmap byte will fail for reasons that
  are not instar's. Mitigated by recording it here, and by phases
  8 and 15 choosing fixture sector layouts that avoid shared
  bitmap bytes unless they are deliberately testing this.
* **Scope creep into phase 4.** The defect is in front of the
  sub-agent in steps 1c and 1d and it is a small fix. Mitigated
  by decision 6 and by the `git diff -- src/` check in the
  definition of done.
* **The go/no-go comes back no.** Then phases 8, 9 and 15 change
  shape and the operator should hear it immediately rather than
  after phase 5. Step 1a reports to the management session
  before 1b starts.

## Definition of done

* `vhdiinfo -V` output is recorded in this plan, and a go/no-go
  sentence names the oracle or the fallback taken.
* This plan contains, for both VHD and VHDX, the verbatim command
  lines and output of an oracle run against a differencing chain,
  and a `cmp` result against the intended composed content.
* This plan states whether a differencing image not produced by
  us was tested, and names it or says it could not be obtained.
* Every offset stated in the structure pin is backed by a spec
  citation or an `xxd` of a real image, both present in the plan.
* `docs/quirks.md` has a differencing section whose every factual
  claim quotes a command run during this phase, including tool
  versions.
* A GitHub issue exists for the silent parent-ignoring read, and
  its number appears in the master plan's *Bugs fixed during this
  work*.
* No open question in the master plan is left as a
  recommendation: each of questions 1 through 7 reads RESOLVED
  with an answer and its evidence.
* `git diff --name-only develop...HEAD -- src/` is empty.
* `pre-commit run --all-files` passes.

## The structure pin

This is the field-by-field emit specification for phases 5 and 6.
It is written so that neither emitter has to rediscover anything:
every row gives an absolute byte offset, a size, an endianness and
the value or rule instar writes, and every offset is backed either
by a spec citation or by an `xxd` of a real image, with the
command line.

Three sources are used, and they are named per claim rather than
blended:

* **SPEC(VHD)** — Microsoft, *Virtual Hard Disk Image Format
  Specification*, version 1.0 (October 2006). This document is no
  longer served from microsoft.com. The tables are quoted here via
  libyal's transcription,
  `libyal/libvhdi/documentation/Virtual Hard Disk (VHD) image
  format.asciidoc` (fetched 2026-09-05), in which
  yellow-highlighted text is marked as copied verbatim from the
  Microsoft specification. Where a claim rests on that verbatim
  text it is called out; where it rests on libyal's own
  reverse-engineering it is called out as **LIBYAL** instead, which
  is a weaker source than a spec.
* **SPEC(VHDX)** — `[MS-VHDX]: Virtual Hard Disk v2 (VHDX) File
  Format`, revision 8.0 (2024-04-23), cited by section number and
  read from
  `learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/`
  on 2026-09-05.
* **MEASURED** — an `xxd`, a tool run or a source read performed
  for this step on 2026-09-05. Paths beginning `step1a/` are
  relative to the phase 1 scratchpad directory recorded in the
  step 1a result; `thirdparty/` inside it holds the Hyper-V
  produced `log2timeline/dfvfs` corpus. Paths beginning `src/` are
  in this repository at `d59cc40`.

The Hyper-V images used throughout are `fat-differential.vhd`,
`ntfs-differential.vhd`, `fat-differential.vhdx` and
`ntfs-differential.vhdx`, all with creator application `win ` and
creator version `0xa0000`, and their parents. Every field below
was checked in *both* the fat and the ntfs chain; only the fat
`xxd` is quoted, because the two agree on every structural value
and differ only in paths, sizes and identifiers.

### VHD — the footer of a differencing child

The footer is 512 bytes and is written twice: a head copy at byte
0 and a tail copy at the end of the file. Both copies are
byte-identical in Hyper-V's images, checksum included. Offsets are
relative to the start of the footer copy, so they are absolute for
the head copy.

Layout: SPEC(VHD) "Footer" table. Values: MEASURED unless noted.

| Field | Offset | Size | Endianness | What instar writes |
|-------|--------|------|------------|--------------------|
| Cookie | 0 | 8 | ASCII | `conectix` |
| Features | 8 | 4 | BE u32 | `0x00000002` (reserved bit; SPEC(VHD) "Features": must always be set) |
| File format version | 12 | 4 | BE u32 | `0x00010000` |
| Data offset | 16 | 8 | BE u64 | `512` — the byte offset of the dynamic header |
| Timestamp | 24 | 4 | BE u32 | `0`, as `vhd::build_footer` already does (`src/crates/vhd/src/lib.rs:1065`) |
| Creator application | 28 | 4 | ASCII | `qem2` — **keep it**, see the note below |
| Creator version | 32 | 4 | BE u32 | `0x00010000` |
| Creator host OS | 36 | 4 | ASCII | `Wi2k` |
| Original size | 40 | 8 | BE u64 | the parent's current size |
| Current size | 48 | 8 | BE u64 | the same value |
| Disk geometry | 56 | 4 | BE u16 cylinders, u8 heads, u8 sectors/track | `vhd::footer_geometry(current_size)`, unchanged |
| **Disk type** | **60** | **4** | **BE u32** | **`4` (differencing)** |
| Checksum | 64 | 4 | BE u32 | ones' complement of the sum of all 512 bytes with these four bytes zeroed |
| Unique id | 68 | 16 | opaque 16 bytes | the child's own identifier |
| Saved state | 84 | 1 | — | `0` |
| Reserved | 85 | 427 | — | zero |

```
$ xxd -s 0 -l 96 step1a/thirdparty/fat-differential.vhd
00000000: 636f 6e65 6374 6978 0000 0002 0001 0000  conectix........
00000010: 0000 0000 0000 0200 2719 8e1b 7769 6e20  ........'...win
00000020: 000a 0000 5769 326b 0000 0000 0040 0000  ....Wi2k.....@..
00000030: 0000 0000 0040 0000 0078 0411 0000 0004  .....@...x......
00000040: ffff f02c f84f 1636 cd9e 9041 a69e dcc2  ...,.O.6...A....
00000050: 380e 416a 0000 0000 0000 0000 0000 0000  8.Aj............
```

Reading that back: data offset `0x200` at byte 16, original and
current size `0x400000` at 40 and 48, geometry `0078 04 11` =
(120, 4, 17) at 56, **disk type `0x00000004` at 60**, checksum
`0xfffff02c` at 64, unique id `f84f1636…` at 68. The parent's own
footer carries unique id `5fa21a55-f394aa4d-99581951-a67d5540`,
which is exactly what the child's dynamic header repeats at
offset 552 below.

Three notes on the value column, each MEASURED:

* **Creator application stays `qem2`, not `win `.** Hyper-V writes
  `win `; instar must not copy it. `qem2` is load-bearing: without
  it every qemu before 10.0 derives the disk size from the CHS
  geometry and silently truncates instar's output. The reasoning
  is already written out at `src/crates/vhd/src/lib.rs:1067-1087`
  and nothing about differencing changes it.
* **The geometry does not have to multiply out to the size.**
  Hyper-V's `120 * 4 * 17 = 8160` sectors against a `current_size`
  of 8192 sectors. So instar's existing `footer_geometry` output
  is acceptable for a differencing child, and no new geometry rule
  is needed.
* **Child size equals parent size.** In both Hyper-V VHD chains the
  child's `current_size` equals the parent's (`4194304`). There is
  no SPEC(VHD) sentence requiring it, but the child's BAT is sized
  from its own `max_table_entries` and a differing size gives a
  chain no reader can compose sensibly. Phase 5 should require it;
  see the judgement calls.

### VHD — the dynamic header of a differencing child

The dynamic header is 1024 bytes at the footer's data offset,
which is 512 for every image in the corpus and for everything
instar emits. Both a header-relative and an absolute offset are
given; the absolute column assumes that layout.

Layout: SPEC(VHD) "Dynamic disk header" table. Values: MEASURED
unless noted.

| Field | Abs | Rel | Size | Endianness | What instar writes |
|-------|-----|-----|------|------------|--------------------|
| Cookie | 512 | +0 | 8 | ASCII | `cxsparse` |
| Next offset | 520 | +8 | 8 | BE u64 | `0xFFFFFFFFFFFFFFFF` |
| Table offset | 528 | +16 | 8 | BE u64 | absolute byte offset of the BAT |
| Header version | 536 | +24 | 4 | BE u32 | `0x00010000` |
| Max table entries | 540 | +28 | 4 | BE u32 | `ceil(virtual_size / block_size)` |
| Block size | 544 | +32 | 4 | BE u32 | 2 MiB default, unchanged |
| Checksum | 548 | +36 | 4 | BE u32 | ones' complement of the sum of all 1024 bytes with these four zeroed |
| **Parent unique id** | **552** | **+40** | **16** | opaque 16 bytes | the parent footer's bytes 68..84, copied verbatim |
| **Parent timestamp** | **568** | **+56** | **4** | **BE u32** | `0` |
| Reserved | 572 | +60 | 4 | BE u32 | `0` |
| **Parent unicode name** | **576** | **+64** | **512** | **UTF-16 BIG endian** | the backing path, zero-padded to 512 bytes |
| **Parent locator entries 1..8** | **1088** | **+576** | **8 x 24 = 192** | see below | two populated, six zero |
| Reserved | 1280 | +768 | 256 | — | zero |

```
$ xxd -s 512 -l 96 step1a/thirdparty/fat-differential.vhd
00000200: 6378 7370 6172 7365 ffff ffff ffff ffff  cxsparse........
00000210: 0000 0000 0000 2000 0001 0000 0000 0002  ...... .........
00000220: 0020 0000 ffff d951 5fa2 1a55 f394 aa4d  . .....Q_..U...M
00000230: 9958 1951 a67d 5540 0000 0000 0000 0000  .X.Q.}U@........
00000240: 0043 003a 005c 0050 0072 006f 006a 0065  .C.:.\.P.r.o.j.e
00000250: 0063 0074 0073 005c 0064 0066 0076 0066  .c.t.s.\.d.f.v.f
```

Table offset `0x2000` at 528, max table entries `2` at 540, block
size `0x200000` at 544, checksum `0xffffd951` at 548, parent
unique id `5fa2 1a55 f394 aa4d 9958 1951 a67d 5540` at 552 —
byte-identical to the parent file's own footer unique id — parent
timestamp `0` at 568, and the parent unicode name starting at 576.

The name field's endianness is visible in that last dump and is
worth restating because it is the opposite of the locator data 512
bytes further on:

```
$ xxd -s 576 -l 64 step1a/thirdparty/fat-differential.vhd
00000240: 0043 003a 005c 0050 0072 006f 006a 0065  .C.:.\.P.r.o.j.e
00000250: 0063 0074 0073 005c 0064 0066 0076 0066  .c.t.s.\.d.f.v.f
00000260: 0073 005c 0074 0065 0073 0074 005f 0064  .s.\.t.e.s.t._.d
00000270: 0061 0074 0061 005c 0066 0061 0074 002d  .a.t.a.\.f.a.t.-
```

`00 43 00 3a` is `C:` in UTF-16 **big** endian; SPEC(VHD) says so
too ("Contains an UTF-16 big-endian string"). The string is not
NUL terminated as such — the remainder of the 512-byte field is
simply zero, which amounts to the same thing. instar writes the
backing path here, zero-padding the rest.

**A length rule phase 5 must add.** The field is 512 bytes, so a
path is limited to 256 UTF-16 code units, but the call table
accepts up to `MAX_BACKING_FILE_LEN = 1024` UTF-8 bytes
(`src/crates/create/src/lib.rs:24`). A path whose UTF-16 encoding
exceeds 512 bytes therefore needs a typed refusal rather than a
truncation. That is a new error variant, not an existing one.

**Parent timestamp is `0`.** Hyper-V writes zero here in both
differencing VHDs (byte 568 above), so instar writing zero matches
the only real producer we have. SPEC(VHD) describes it as
"Parent modification time … seconds since January 1, 2000", so a
real value would also be legal; nothing reads it (see *What has no
oracle*).

### VHD — the eight parent locator entries

Each entry is 24 bytes; the eight of them occupy absolute offsets
1088, 1112, 1136, 1160, 1184, 1208, 1232 and 1256.

Layout: SPEC(VHD) "Parent locator entry" table. Values: MEASURED.

| Field | Rel | Size | Endianness | What instar writes |
|-------|-----|------|------------|--------------------|
| Platform code | +0 | 4 | ASCII, not byte-swapped | `W2ku` or `W2ru`; zero for an unused slot |
| Platform data space | +4 | 4 | BE u32 | **byte count**, rounded up to a 512-byte multiple |
| Platform data length | +8 | 4 | BE u32 | length in bytes of the UTF-16LE string, no terminator |
| Reserved | +12 | 4 | BE u32 | `0` |
| Platform data offset | +16 | 8 | BE u64 | absolute byte offset of the string in the file |

```
$ xxd -s 1088 -l 96 step1a/thirdparty/fat-differential.vhd
00000440: 5732 6b75 0000 1000 0000 0054 0000 0000  W2ku.......T....
00000450: 0000 0000 0000 1000 5732 7275 0001 0000  ........W2ru....
00000460: 0000 0020 0000 0000 0000 0000 0000 3000  ... ..........0.
00000470: 0000 0000 0000 0000 0000 0000 0000 0000  ................
00000480: 0000 0000 0000 0000 0000 0000 0000 0000  ................
00000490: 0000 0000 0000 0000 0000 0000 0000 0000  ................
```

Entry 1 (absolute 1088): code `W2ku`, data space `0x00001000` =
4096, data length `0x54` = 84, reserved 0, data offset
`0x0000000000001000`. Entry 2 (absolute 1112): code `W2ru`, data
space `0x00010000` = 65536, data length `0x20` = 32, data offset
`0x3000`. Entries 3 to 8 are zero — the dump above covers entries
1 to 4, and `xxd -s 1184 -l 96` on the same file shows entries 5
to 8 zero as well.

The locator data itself, at the offsets those entries name:

```
$ xxd -s 4096 -l 96 step1a/thirdparty/fat-differential.vhd
00001000: 4300 3a00 5c00 5000 7200 6f00 6a00 6500  C.:.\.P.r.o.j.e.
00001010: 6300 7400 7300 5c00 6400 6600 7600 6600  c.t.s.\.d.f.v.f.
00001020: 7300 5c00 7400 6500 7300 7400 5f00 6400  s.\.t.e.s.t._.d.
00001030: 6100 7400 6100 5c00 6600 6100 7400 2d00  a.t.a.\.f.a.t.-.
00001040: 7000 6100 7200 6500 6e00 7400 2e00 7600  p.a.r.e.n.t...v.
00001050: 6800 6400 0000 0000 0000 0000 0000 0000  h.d.............

$ xxd -s 12288 -l 48 step1a/thirdparty/fat-differential.vhd
00003000: 2e00 5c00 6600 6100 7400 2d00 7000 6100  ..\.f.a.t.-.p.a.
00003010: 7200 6500 6e00 7400 2e00 7600 6800 6400  r.e.n.t...v.h.d.
00003020: 0000 0000 0000 0000 0000 0000 0000 0000  ................
```

`43 00 3a 00` is `C:` in UTF-16 **little** endian, the opposite of
the parent unicode name field. `84` bytes is exactly the 42
characters of `C:\Projects\dfvfs\test_data\fat-parent.vhd` with no
NUL terminator, and `32` bytes is exactly the 16 characters of
`.\fat-parent.vhd`. The bytes past `data_length` up to
`data_space` are zero.

#### Where SPEC(VHD) and Hyper-V disagree: platform data space

SPEC(VHD)'s wording for this field, in the verbatim-from-Microsoft
highlighting of libyal's transcription, is:

> *Platform data space.* This field stores the number of 512-byte
> sectors needed to store the parent hard disk locator.

Hyper-V does not do that. In the dump above the `W2ku` entry has
`data_space = 4096` for a locator at file offset `0x1000`, and the
`W2ru` locator sits at `0x3000`. Read as the spec's sector count,
`4096` sectors is 2 MiB and would run from `0x1000` to `0x201000`,
swallowing the BAT (whose offset the same header gives as
`0x2000`), the second locator's data at `0x3000`, and the first
data block at `0x13e00` — in a file that is only `0x214000` bytes
long. Read as a byte count, `0x1000 + 4096 = 0x2000` lands exactly
on the BAT, which is what a producer laying the file out
sequentially would write. The same reading works for `W2ru`:
`0x3000 + 65536 = 0x13000`, below the first data block at
`0x13e00`. Both Hyper-V VHDs agree, and both values are 512-byte
multiples, so the two readings cannot be told apart by alignment
alone — only by the arithmetic above.

**instar follows Hyper-V: `platform_data_space` is a byte count.**
Hyper-V is the only producer of this structure we can measure, it
is the implementation the format exists to interoperate with, and
the sector reading is not merely unusual there but arithmetically
impossible. instar writes `round_up(data_length, 512)`, which is
`512` for any path short enough to fit the parent unicode name
field anyway.

*What would falsify this:* a Hyper-V or Windows-produced
differencing VHD in which `data_space * 512` is the plausible
extent and `data_space` alone is not — that is, a locator whose
data is longer than `data_space` bytes. No image in the corpus is
like that, and none can be constructed from a 42-character path.

#### The file layout phase 5 should emit

Nothing external constrains this; it is stated so that phases 5, 8
and 10 describe the same file. It extends the dynamic layout
already documented at `src/crates/create/src/lib.rs:740-744`.

```
0                 head footer copy            (512 bytes)
512               dynamic header              (1024 bytes)
1536              W2ru locator data           (one 512-byte sector)
2048              W2ku locator data           (one 512-byte sector)
2560              BAT, every entry 0xFFFFFFFF (sector-padded)
2560+bat_padded   tail footer copy            (512 bytes)
```

The BAT is wholly unallocated at create time, which SPEC(VHD)'s
block allocation table section covers directly: an entry of
`0xffffffff` means "block is sparse or stored in parent". A
freshly created differencing child therefore has no sector bitmaps
at all, which is why libvhdi defect A from the step 1a result
cannot affect `instar create` output.

### VHDX — file parameters

The `HasParent` bit is what makes the file a differencing VHDX.

Layout and semantics: SPEC(VHDX) 2.6.2.1 File Parameters —
"B - HasParent (1 bit): Specifies whether this file has a parent
VHDX file. If set, the file is a differencing file, and one or more
parent locators specify the location and identity of the parent.
LeaveBlockAllocated is ignored when HasParent is set."

| Field | Item offset | Size | Endianness | What instar writes |
|-------|-------------|------|------------|--------------------|
| BlockSize | +0 | 4 | LE u32 | the requested block size |
| LeaveBlockAllocated | +4 bit 0 | — | — | `0` |
| **HasParent** | **+4 bit 1** | — | — | **`1`** |

```
$ xxd -s 0x210000 -l 16 step1a/thirdparty/fat-differential.vhdx
00210000: 0000 2000 0200 0000 0000 4000 0000 0000  .. .......@.....
```

`0x00200000` = 2 MiB block size, flags `0x00000002` = HasParent
set, LeaveBlockAllocated clear. `vhdx::build_metadata` already
takes a `has_parent` argument and writes exactly this
(`src/crates/vhdx/src/lib.rs:1479`), so phase 6 passes `true`
rather than adding a field.

The child's BAT is left as a sparse hole of zeros, i.e. every
payload entry in state `PAYLOAD_BLOCK_NOT_PRESENT` (0) and every
sector-bitmap entry in `SB_BLOCK_NOT_PRESENT` (0). SPEC(VHDX)
2.5.1.1 makes that the right encoding: "For a differencing VHDX
file, this block state specifies that the block contents are not
present in the file and that the parent virtual disk SHOULD be
inspected to determine the associated contents." SPEC(VHDX)
2.5.1.2 makes the sector-bitmap side legal too: a sector bitmap
entry may only be `SB_BLOCK_NOT_PRESENT` if no associated payload
block is `PAYLOAD_BLOCK_PARTIALLY_PRESENT`, and a freshly created
child has none.

### VHDX — the parent locator metadata item

The item is registered by a normal metadata table entry and its
body is the parent locator header plus key/value entries.

| Thing | Value | Source |
|-------|-------|--------|
| Metadata item GUID | `A8D35F2D-B30B-454D-ABF7-D3D84834AB0C` | already in the tree as `PARENT_LOCATOR_GUID`, `src/crates/vhdx/src/lib.rs:151`; MEASURED below |
| Locator type GUID | `B04AEFB7-D19E-4A81-B789-25B8E9445913` | SPEC(VHDX) 2.6.2.6.3: "The only parent-locator type defined by this specification is the VHDX locator type with a GUID value of B04AEFB7-D19E-4A81-B789-25B8E9445913"; MEASURED below |

Both GUIDs are stored in the mixed-endian "bytes_le" form used
everywhere in VHDX: first three groups little-endian, last two
big-endian.

The metadata table entry, SPEC(VHDX) 2.6.1.2:

| Field | Entry offset | Size | Endianness | What instar writes |
|-------|--------------|------|------------|--------------------|
| ItemID | +0 | 16 | GUID bytes_le | the parent locator GUID above |
| Offset | +16 | 4 | LE u32 | `0x10028`, relative to the metadata region start |
| Length | +20 | 4 | LE u32 | the item's exact byte length |
| IsUser / IsVirtualDisk / IsRequired | +24 bits 0/1/2 | — | — | `0x00000004` — IsRequired only |
| Reserved2 | +28 | 4 | LE u32 | `0` |

```
$ xxd -s 0x2000c0 -l 32 step1a/thirdparty/fat-differential.vhdx
002000c0: 2d5f d3a8 0bb3 4d45 abf7 d3d8 4834 ab0c  -_....ME....H4..
002000d0: 2800 0100 a202 0000 0400 0000 0000 0000  (...............
```

That is the sixth table entry (metadata region at `0x200000`,
header 32 bytes, entries 32 bytes each, so entry index 5 is at
`0x200000 + 32 + 5*32 = 0x2000c0`): the parent locator GUID,
offset `0x00010028`, length `0x2a2` = 674, flags `0x00000004`.

The offset `0x10028` is not a coincidence instar has to reproduce
by hand — it is where instar's own `build_metadata` would place a
sixth item today. That function puts item data at `items_base =
0x10000` and consumes 8 + 8 + 4 + 4 + 16 = 40 bytes for the five
existing items (`src/crates/vhdx/src/lib.rs:1420-1504`), so the
next free byte is `0x10028`, exactly where Hyper-V puts its parent
locator. SPEC(VHDX) 2.6.1.2 requires only that the offset be at
least 64 KB and that items not overlap.

#### Parent locator header

SPEC(VHDX) 2.6.2.6.1 Parent Locator Header.

| Field | Item offset | Size | Endianness | What instar writes |
|-------|-------------|------|------------|--------------------|
| LocatorType | +0 | 16 | GUID bytes_le | `B04AEFB7-D19E-4A81-B789-25B8E9445913` |
| Reserved | +16 | 2 | LE u16 | `0` (SPEC: "MUST be set to 0") |
| KeyValueCount | +18 | 2 | LE u16 | the number of entries that follow |

#### Parent locator entry

SPEC(VHDX) 2.6.2.6.2 Parent Locator Entry. Entries are 12 bytes
each and start at item offset +20.

| Field | Entry offset | Size | Endianness | Meaning |
|-------|--------------|------|------------|---------|
| KeyOffset | +0 | 4 | LE u32 | offset **within the metadata item** |
| ValueOffset | +4 | 4 | LE u32 | offset within the metadata item |
| KeyLength | +8 | 2 | LE u16 | key length in bytes |
| ValueLength | +10 | 2 | LE u16 | value length in bytes |

SPEC(VHDX) 2.6.2.6.2 also fixes the string encoding: "The key and
value strings are to be UNICODE strings with UTF-16 little-endian
encoding. There must be no internal NUL characters, and the Length
field must not include a trailing NUL character. The key string is
case sensitive, and lowercase keys are recommended. All keys must
be unique, and there is no ordering to the entries."

```
$ xxd -s 0x210028 -l 80 step1a/thirdparty/fat-differential.vhdx
00210028: b7ef 4ab0 9ed1 814a b789 25b8 e944 5913  ..J....J..%..DY.
00210038: 0000 0500 5000 0000 6c00 0000 1c00 4c00  ....P...l.....L.
00210048: b800 0000 de00 0000 2600 5600 3401 0000  ........&.V.4...
00210058: 4e01 0000 1a00 2200 7001 0000 8601 0000  N.....".p.......
00210068: 1600 b200 3802 0000 5602 0000 1e00 4c00  ....8...V.....L.

$ xxd -s 0x210078 -l 128 step1a/thirdparty/fat-differential.vhdx
00210078: 7000 6100 7200 6500 6e00 7400 5f00 6c00  p.a.r.e.n.t._.l.
00210088: 6900 6e00 6b00 6100 6700 6500 7b00 6600  i.n.k.a.g.e.{.f.
00210098: 3800 3800 6400 3400 6400 3900 3200 2d00  8.8.d.4.d.9.2.-.
002100a8: 3600 6600 6300 6300 2d00 3400 3000 3800  6.f.c.c.-.4.0.8.
002100b8: 6400 2d00 3900 6200 6500 6600 2d00 3900  d.-.9.b.e.f.-.9.
002100c8: 6200 3700 6300 3800 3900 6600 3100 3500  b.7.c.8.9.f.1.5.
002100d8: 6300 3800 3900 7d00 6100 6200 7300 6f00  c.8.9.}.a.b.s.o.
002100e8: 6c00 7500 7400 6500 5f00 7700 6900 6e00  l.u.t.e._.w.i.n.
```

Decoding entry 1 from `0x21003c`: KeyOffset `0x50` = 80,
ValueOffset `0x6c` = 108, KeyLength `0x1c` = 28, ValueLength
`0x4c` = 76. Item start is `0x210028`, so the key is at
`0x210078` — `parent_linkage`, 14 characters, 28 bytes — and the
value at `0x210094`, 38 characters, 76 bytes. That confirms
MEASURED what SPEC(VHDX) 2.6.2.6.2 states: the offsets are
relative to the start of the metadata item (the parent locator
header), not to the metadata region and not to the file. The five
entries decode to key offsets 80, 184, 308, 368, 568 and the item
ends exactly at 568 + 30 + 76 = 674, matching the table entry's
length with no padding.

#### Which keys Hyper-V writes — MEASURED

`fat-differential.vhdx`, in table order:

| Key | Value |
|-----|-------|
| `parent_linkage` | `{f88d4d92-6fcc-408d-9bef-9b7c89f15c89}` |
| `absolute_win32_path` | `C:\Projects\dfvfs\test_data\fat-parent.vhdx` |
| `relative_path` | `.\fat-parent.vhdx` |
| `volume_path` | `\\?\Volume{5e0bd954-71b2-4bff-a928-082af7ab0f8f}\Projects\dfvfs\test_data\fat-parent.vhdx` |
| `parent_linkage2` | `{00000000-0000-0000-0000-000000000000}` |

`ntfs-differential.vhdx` writes the same five keys in the same
order, with its own paths and GUIDs. Both were read with
`python3 step1a/vhdxdump.py step1a/thirdparty/fat-differential.vhdx`
and cross-checked against the raw `xxd` above.

Two divergences from SPEC(VHDX) 2.6.2.6.3 fall out of that, both
MEASURED:

* The spec says "The parent_linkage entry MUST be present, and
  parent_linkage2 can't be present", and then two sentences later
  says an implementation "MUST verify that the DataWriteGuid field
  of the parent's header matches one of these two fields". The
  section contradicts itself. Hyper-V writes `parent_linkage2`
  with an all-zero GUID. instar writes `parent_linkage` only: it
  satisfies the MUST, avoids the prohibition, and matches
  libvhdi, which never looks the second key up
  (`libvhdi_metadata_values.c` reads `parent_linkage` at :213 and
  nothing else GUID-shaped).
* The spec says `absolute_win32_path` "MUST begin with `\\?\`".
  Hyper-V's value is `C:\Projects\dfvfs\test_data\fat-parent.vhdx`,
  with no `\\?\` prefix. So the only real producer we can measure
  violates the MUST, and libvhdi accepts it. instar does not emit
  this key at all (see the recommendation below), so the question
  does not arise for the emitter — but phase 3's parser must not
  reject a value for lacking the prefix.

#### parent_linkage is the parent's DataWriteGuid — settled twice

Step 1a resolved this by measurement. It is now confirmed a second
way, and the two agree, so phase 6 implements it as fact.

SPEC(VHDX) 2.6.2.6.3: "When a differencing VHDX file is created,
the implementation MUST populate the parent's DataWriteGuid field
in this field. When opening the parent VHDX file of a differencing
VHDX, the implementation MUST verify that the DataWriteGuid field
of the parent's header matches one of these two fields." The value
is "encoded as a lowercase string with enclosing braces".

MEASURED, the child's `parent_linkage` against the parent's header:

```
$ xxd -s 0x20000 -l 48 step1a/thirdparty/fat-parent.vhdx
00020000: 6865 6164 2133 c2b1 0700 0000 0000 0000  head!3..........
00020010: 7fab 7d61 34f2 5b45 b63d 4d9d e0a3 06f2  ..}a4.[E.=M.....
00020020: 924d 8df8 cc6f 8d40 9bef 9b7c 89f1 5c89  .M...o.@...|..\.
```

The header signature `head` at `0x20000`, sequence number `7` at
+8, FileWriteGuid at +16, and DataWriteGuid at +32:
`92 4d 8d f8 cc 6f 8d 40 9b ef 9b 7c 89 f1 5c 89`, which in
bytes_le form is `f88d4d92-6fcc-408d-9bef-9b7c89f15c89` — exactly
the child's `parent_linkage` string, lowercase and braced. It is
*not* the parent's virtual disk id, which is
`cc2e9979-9ee7-417c-a2cd-4a3fa18795fb`.

libvhdi implements the same rule: `libvhdi_file_set_parent_file`
(`libvhdi_file.c:2882`) compares the child's
`metadata_values->parent_identifier` against the parent's
identifier, and for VHDX `libvhdi_file_get_identifier` (`:3457`) returns
`libvhdi_image_header_get_data_write_identifier` (`:3508`), not
the virtual disk id. The comparison and its "mismatch in
identifier" error are at `:2992-3005`.

*Which header?* The active one — the header with the higher
sequence number that passes its CRC. In this corpus both headers
of both parents carry the same DataWriteGuid, so the sample does
not discriminate; the rule is taken from SPEC(VHDX)'s "the
parent's header" plus instar's own existing active-header
selection at `src/crates/vhdx/src/lib.rs:741-751`, which already
picks the higher sequence number.

**A trap for phase 6 that this exposes.** instar's
`vhdx::build_header` derives the DataWriteGuid from the sequence
number alone (`src/crates/vhdx/src/lib.rs:1341-1345`), and
`plan_vhdx` always writes sequence numbers 1 and 2
(`src/crates/create/src/lib.rs:964-966`), as does the convert op
(`src/operations/convert/src/main.rs:4469`, `:4492`). Every VHDX
instar has ever written therefore has the same active-header
DataWriteGuid, `00000002-0000-0000-0200-000000000000`. That is
read from the code, not measured on an instar-produced image, and
should be confirmed against a real one in phase 6. If it holds,
the parent-identity check is vacuous for instar-written chains —
any instar VHDX will satisfy any instar child's `parent_linkage` —
so phase 8 must build at least one negative test using a
*third-party* parent, and phase 6 should consider giving created
images a real DataWriteGuid.

#### Copied-from-parent metadata

SPEC(VHDX) 2.6.1.2 on the IsVirtualDisk flag: "When forking, an
implementation MUST copy all metadata items with this field set in
the existing VHDX file to the new file, while leaving items with
this field clear."

Hyper-V does exactly that, MEASURED: the child's `virtual_disk_id`
item is byte-identical to the parent's, and both files' metadata
table entries carry flags `0x06` (IsVirtualDisk | IsRequired) on
`virtual_disk_size`, `logical_sector_size`, `physical_sector_size`
and `virtual_disk_id`, and `0x04` on `file_parameters` and
`parent_locator`.

```
$ xxd -s 0x210018 -l 16 step1a/thirdparty/fat-differential.vhdx
00210018: 7999 2ecc e79e 7c41 a2cd 4a3f a187 95fb  y.....|A..J?....
$ xxd -s 0x210018 -l 16 step1a/thirdparty/fat-parent.vhdx
00210018: 7999 2ecc e79e 7c41 a2cd 4a3f a187 95fb  y.....|A..J?....
$ xxd -s 0x200040 -l 128 step1a/thirdparty/fat-differential.vhdx
00200040: 2442 a52f 1bcd 7648 b211 5dbe d83b f4b8  $B./..vH..]..;..
00200050: 0800 0100 0800 0000 0600 0000 0000 0000  ................
00200060: 1dbf 4181 6fa9 0947 ba47 f233 a8fa ab5f  ..A.o..G.G.3..._
00200070: 1000 0100 0400 0000 0600 0000 0000 0000  ................
00200080: c748 a3cd 5d44 7144 9cc9 e988 5251 c556  .H..]DqD....RQ.V
00200090: 1400 0100 0400 0000 0600 0000 0000 0000  ................
002000a0: ab12 cabe e6b2 2345 93ef c309 e000 c746  ......#E.......F
002000b0: 1800 0100 1000 0000 0600 0000 0000 0000  ................
```

Two consequences for phase 6, both of which change existing code
rather than only adding to it:

* **The child's virtual disk size must equal the parent's.**
  `virtual_disk_size` carries IsVirtualDisk, so forking copies it.
  This is the VHDX half of the "child size equals parent size"
  rule and it is spec-backed, where the VHD half is only measured.
* **instar writes flags `0x04` on all five metadata entries**
  (`src/crates/vhdx/src/lib.rs:1422-1460`) where Hyper-V writes
  `0x06` on four of them, and it synthesises the virtual disk id
  from the size and block size rather than using a GUID
  (`:1491-1504`). For a differencing child the emitter should copy
  the parent's `virtual_disk_id` verbatim, which the guest can
  read from the parent device it already has attached. Whether to
  also correct the IsVirtualDisk flags on the non-differencing
  path is a phase 6 scoping call, not a differencing question.

### Which platform codes to emit — the answer to open question 3

**MEASURED, both Hyper-V VHDs.** Exactly two locator entries are
populated, in this slot order:

| Slot | Absolute offset | Platform code | Contents |
|------|-----------------|---------------|----------|
| 1 | 1088 | `W2ku` | the absolute Windows path, UTF-16LE |
| 2 | 1112 | `W2ru` | the path relative to the child, UTF-16LE |
| 3-8 | 1136-1256 | zero | — |

That is `57 32 6b 75` and `57 32 72 75` on the wire, stored as
plain ASCII in file order and not byte-swapped — read straight off
the `xxd` at 1088 above. SPEC(VHD)'s "Locator platform code" table
lists `W2ku` as "Absolute Unicode (UTF-16) pathname on Windows"
and `W2ru` as "Unicode path (UTF-16) on Windows relative to the
differential disk path", with `Wi2k`/`Wi2r` deprecated and
`MacX`/`Max ` for Mac OS. Hyper-V uses neither deprecated code and
neither Mac code.

**The recommendation: emit `W2ru` only, in slot 1, and leave slots
2 through 8 zero — unless the user's backing path is absolute, in
which case emit `W2ku` in slot 1 instead.** One entry, whose
platform code describes the string instar actually has.

The reason is a constraint on the guest, MEASURED in the host
code: the create op receives the backing path exactly as the user
typed it on the command line and nothing else. `run_create_nonraw`
resolves the typed path against the output's directory *for
opening the file* but sends `typed_backing.as_bytes()` to the
guest unchanged (`src/vmm/src/main.rs:16710-16769`). So the guest
has one string. It cannot construct an absolute path from a
relative one, and it cannot construct a relative one from an
absolute one. Writing both entries would mean fabricating the
second — `.\<basename>` as a stand-in for a relative path, which
is simply wrong whenever the parent is in a different directory
from the child, and a fabricated locator is worse than an absent
one. The alternative, passing a second resolved path from the
host, needs a new call-table field, and the master plan's premise
that this work needs no call-table change (Execution, phase 7
rationale) is worth more than a cosmetic match to Hyper-V.

Emitting one entry rather than two costs nothing that we can
measure: no reader in reach parses the VHD locator table at all
(see *What has no oracle*), and the parent unicode name at offset
576 — which libvhdi *does* use — carries the same string anyway.

Slot order is cosmetic. Nothing in SPEC(VHD) orders the entries,
and instar's own phase 3 parser should select by platform code
rather than by slot, so putting the single entry in slot 1 is a
readability choice.

*What would falsify this:* a Hyper-V or Windows sample that
refuses a differencing child carrying only `W2ru`, or only
`W2ku`. We cannot run that test — there is no Windows host in this
plan's reach — so this recommendation is a reasoned default rather
than a measured one, and it is cheap to revisit: adding a second
entry later is an additive change to the emitter and to nothing
else.

*A related judgement call, stated separately because it is a
security question rather than a format one:* `W2ku` writes an
absolute host path into the image, which is an information
disclosure of the producer's filesystem layout. instar already
stores the user-supplied `backing_file` string verbatim for qcow2
and vmdk, so writing what the user typed — and only what the user
typed — is consistent with existing behaviour and discloses
nothing the user did not choose to disclose. The recommendation
above preserves that property; a scheme that resolved the path to
absolute before writing it would not.

### VHDX — which keys instar should write

**Write `parent_linkage` and exactly one path key**, chosen the
same way as the VHD platform code: `relative_path` when the typed
backing path is relative, `absolute_win32_path` when it is
absolute. Do not write `volume_path` — it requires a Windows
volume GUID that no Linux producer can obtain — and do not write
`parent_linkage2`, per the spec sentence above.

`parent_linkage` is required by SPEC(VHDX) 2.6.2.6.3 and is the
key libvhdi checks, so it is non-negotiable. SPEC(VHDX) 2.6.2.6.3
also requires "At least one entry with key value of
relative_path, volume_path, or absolute_win32_path".

**The libvhdi bug phase 6 must not mistake for its own.** libvhdi
looks the path keys up in the order `absolute_win32_path`,
`volume_path`, `relative_path` (`libvhdi_metadata_values.c:249`,
`:269`, `:290`) — the reverse of the order SPEC(VHDX) 2.6.2.6.3
prescribes — and it looks `relative_path` up with a length of 12
for a 13-character key:

```c
result = libvhdi_parent_locator_get_entry_by_utf8_key(
          parent_locator,
          (uint8_t *) "relative_path",
          12,
```

So a differencing VHDX carrying *only* `relative_path` produces no
"Parent filename" line from `vhdiinfo` at all. That is the oracle
being broken, not instar's output. Step 1a demonstrated it by
rewriting the child's locator with one key at a time;
`absolute_win32_path` and `volume_path` both resolved and
`relative_path` alone did not. Composition through
`pyvhdi.file.set_parent()` is unaffected, because the harness
supplies the parent file object rather than asking libvhdi to find
it — which is how phase 15's harness will work. Phase 6 should
expect `vhdiinfo` to print no parent filename for a
relative-only child and must not "fix" its emitter in response.

### What has no oracle

Step 1a established that libvhdi ignores the VHD locator table
entirely and validates neither VHD checksum. Measuring the
individual fields for this pin extended that list, and corrected
one item on it.

**Correction to the step 1a result.** Step 1a wrote that "libvhdi
validates neither VHD checksum — so checksum correctness needs
instar's own assertions". The first half is right and the
conclusion is too strong: **qemu-img validates the head footer's
checksum and refuses the image on a mismatch.** MEASURED, by
corrupting one field at a time in a copy of `fat-differential.vhd`
and running `qemu-img info` (qemu-img 10.0.11):

| Corrupted | qemu-img 10.0.11 | libvhdi 20240509 |
|-----------|------------------|------------------|
| head footer checksum, offset 64 | `Could not open: Incorrect header checksum` | opens, resolves the parent |
| dynamic header checksum, offset 548 | opens, reports a 4 MiB vpc image | opens, resolves the parent |
| tail footer copy's checksum | opens | opens |

So the footer checksum *does* have an external oracle. The dynamic
header checksum does not.

The list of emitted fields that no external tool checks for us,
and that phase 8's own structural assertions are therefore the
only defence for:

1. **All eight VHD parent locator entries, and the locator data
   they point at.** Every field: platform code, data space, data
   length, reserved, data offset, and the UTF-16LE bytes. Proven
   by overwriting all 192 bytes at offset 1088 with `0xAA` in a
   copy of `fat-differential.vhd`, repairing the dynamic header
   checksum so only the table was under test, and re-running both
   tools: `qemu-img info` reported the same 4 MiB vpc image, and
   `vhdiinfo` still printed the correct parent identifier and
   parent filename. libvhdi then composed the mutilated child
   against the real parent and the result was byte-identical to
   the composition of the pristine child (`cmp` exit 0). In source
   terms, `libvhdi_parent_locator_*` is called only from
   `libvhdi_metadata_values.c`, the VHDX metadata path; the VHD
   dynamic disk header reader touches the 192-byte array once, to
   hex-dump it inside a debug block
   (`libvhdi_dynamic_disk_header.c:433-438`).
2. **The VHD dynamic header checksum**, offset 548. Neither tool
   validates it (table above). Only instar's own `check` does
   (`src/operations/check/src/main.rs:2047-2052`).
3. **The tail footer copy's checksum.** qemu-img reads only the
   head copy for a dynamic or differencing image; instar's `check`
   validates the tail copy at
   `src/operations/check/src/main.rs:1883-1890`.
4. **The VHD parent timestamp**, offset 568. Nothing reads it.
5. **The VHD footer geometry of a differencing child.** Hyper-V's
   own CHS does not multiply out to `current_size`, so no
   consistency assertion is even available.
6. **VHD creator application, creator version, creator host OS,
   features and saved state** in a differencing child.
   `creator_application` has an indirect oracle only through the
   `qem2` size-interpretation behaviour, which is a size check
   rather than a field check.
7. **Every VHDX parent locator key after the first one libvhdi
   resolves.** libvhdi stops at the first of
   `absolute_win32_path`, `volume_path`, `relative_path` that it
   finds, and its `relative_path` lookup never matches at all. If
   instar emits an absolute key, the relative key is unread; if it
   emits only the relative key, nothing reads any path.
8. **VHDX `parent_linkage2`,** which instar does not emit and
   nothing validates either way.
9. **VHDX metadata table entry flag bits.** libvhdi decodes
   IsUser / IsVirtualDisk / IsRequired only for debug output
   (`libvhdi_metadata_table_entry.c:222-285`, one
   `#if defined( HAVE_DEBUG_OUTPUT )` block), and qemu-img refuses
   differencing VHDX outright so it checks nothing at all.
10. **The VHDX child's copied `virtual_disk_id`.** Nothing verifies
    that it matches the parent's, though SPEC(VHDX) 2.6.1.2
    requires the copy and Hyper-V performs it.

What *does* have an oracle, so that phase 8 does not spend
assertions twice: the VHD footer's head-copy checksum, disk type,
current size and data offset (qemu-img opens the file and reports
the size); the dynamic header's table offset, max table entries
and block size (qemu-img walks the BAT); the parent unicode name
and the parent unique id (libvhdi prints the first and enforces
the second in `set_parent`); the VHDX `HasParent` bit, parent
locator item GUID, locator type GUID, header, entry encoding,
`parent_linkage` and whichever path key libvhdi resolves; and the
VHDX BAT states, through composition.

### Judgement calls

Each of these is a decision phase 5 or 6 implements, not a fact.
The call, the reasoning and the falsifier are given so that a
later reader can overturn one without re-deriving the rest.

1. **`platform_data_space` is a byte count.** Reasoning and
   falsifier are in the VHD locator section above. This one is
   nearly forced: the sector reading is arithmetically impossible
   against Hyper-V's own file.
2. **One locator entry, not two.** Detailed above. Falsified by
   any Windows-side rejection of a single-entry child.
3. **Path separators are not translated.** `W2ru`/`relative_path`
   are Windows-shaped fields — SPEC(VHD) calls `W2ru` a path
   "on Windows" and SPEC(VHDX) 2.6.2.6.3 says `relative_path` uses
   "`\` as the path separator". instar writes the user's POSIX
   path verbatim, forward slashes and all, and does not translate
   them. Reasoning: instar's own chain resolution is POSIX, a
   translation is not round-trippable through a filename that
   legitimately contains a backslash, and no reader in reach cares
   — libvhdi never reads the VHD entry and treats the VHDX value
   as an opaque string. Falsified by a Hyper-V sample that refuses
   a forward-slash locator, or by a decision in phase 11 to make
   instar's own resolver Windows-path-aware, in which case the
   translation should live in the resolver and not in the emitter.
4. **The child's virtual size must equal the parent's.** For VHDX
   this is spec-backed (SPEC(VHDX) 2.6.1.2's IsVirtualDisk copy
   rule applied to `virtual_disk_size`). For VHD it is measured in
   both Hyper-V chains and not stated anywhere. The call is to
   require it for both and refuse a mismatch with a typed error,
   and to let `create -b PARENT` with no explicit size default to
   the parent's size. Falsified by a Hyper-V VHD chain whose child
   and parent sizes differ.
5. **The child's parent unicode name carries the typed path, not
   an absolute one.** Hyper-V writes an absolute path there.
   instar writes what the user typed, matching what it already
   does with qcow2 and vmdk backing references and keeping the
   image portable across moves. Falsified if phase 11's resolver
   turns out to need an absolute form to disambiguate, which it
   should not: `discover_backing_chain` resolves relative
   references against the child's directory
   (`src/vmm/src/main.rs:2416`).
6. **`parent_timestamp` stays `0`.** Matches Hyper-V. A real
   modification time would also be legal and would make output
   non-reproducible, which this codebase avoids elsewhere.
   Falsified by an implementation that rejects a zero timestamp;
   none in reach does.
7. **The child's own footer unique id.** `plan_vhd` currently
   writes sixteen zero bytes for every image it creates
   (`UUID_ZERO`, `src/crates/create/src/lib.rs:776`, `:820`). A
   differencing child copies its *parent's* id into the dynamic
   header at offset 552, so if the parent is itself an
   instar-created VHD, that field is sixteen zeros and libvhdi's
   `set_parent` identity check passes against any other
   instar-created VHD. The call for phase 5 is to leave the
   existing behaviour alone — changing the id of every created VHD
   is out of this plan's scope — and to make phase 8 test the
   identity check against a *third-party* parent, where the id is
   real, rather than against an instar-created one. Falsified if
   phase 8 finds it cannot construct a meaningful negative test
   that way, in which case giving created images a real id becomes
   in scope.

`parent_linkage` is deliberately absent from this list: step 1a
resolved it by measurement, this step confirmed it independently
against SPEC(VHDX) 2.6.2.6.3 and against libvhdi's source, and the
three agree. It is the parent's DataWriteGuid, taken from the
parent's active header, rendered as a lowercase braced GUID string
in UTF-16LE. Phase 6 implements it as fact.

## Appendix — the throwaway generator

Per decision 2 this lives here rather than in a repository. Phase
2 lifts it into a maintained generator in `instar-testdata`. It
encodes the structure findings above, including the byte-count
`platform_data_space` and the opposite bitmap bit orders, and its
`--vhd-plan` flag selects between a realistic mixed layout, a
byte-aligned layout that avoids libvhdi defect A, and a probe
that demonstrates the defect.

```python
#!/usr/bin/env python3
"""Throwaway generator for instar PLAN-differencing phase 1 step 1a.

Builds two differencing chains of 16 MiB each, plus the raw image each chain is
intended to compose to:

  vhd-parent.vhd    dynamic VHD  (hand written, disk type 3)
  vhd-child.vhd     differencing VHD (hand written, disk type 4)
  vhd-expected.raw  what vhd-child.vhd + vhd-parent.vhd must read as

  vhdx-parent.vhdx   dynamic VHDX (qemu-img, 1 MiB blocks)
  vhdx-child.vhdx    differencing VHDX (qemu-img dynamic, then patched here)
  vhdx-expected.raw  what vhdx-child.vhdx + vhdx-parent.vhdx must read as

Also written, as controls:

  *-parent-only.raw  the parent's content alone
  *-child-only.raw   what a reader that ignores the parent would produce

Only the Python standard library and qemu-img are used.  Nothing is written
outside the output directory.

Structure facts this encodes, each measured against a Hyper-V produced image
from the log2timeline/dfvfs corpus rather than taken from the spec text:

  * VHD footer disk type is at offset 60, 4 == differencing.
  * VHD dynamic header sits at footer.data_offset (512 here); parent unique id
    is at header+40 (absolute 552), parent timestamp at +56 (568), parent
    unicode name at +64 (576) and the eight 24 byte parent locator entries at
    +576 (1088).
  * The parent unicode name is UTF-16 BIG endian.  The parent locator platform
    data for W2ku/W2ru is UTF-16 LITTLE endian.
  * Parent locator platform_data_space is a BYTE count, not the sector count
    the Microsoft spec's wording implies.
  * Both VHD checksums are the ones' complement of the sum of the structure's
    bytes with the checksum field zeroed.
  * The VHD per block sector bitmap is most significant bit first: virtual
    sector i of the block is bit (7 - i % 8) of byte i // 8.  A set bit means
    the sector lives in this file, a clear bit means read it from the parent.
  * VHDX has_parent is bit 1 (0x2) of the file parameters flags.
  * The VHDX parent locator metadata item is A8D35F2D-B30B-454D-ABF7-D3D84834AB0C
    with locator type B04AEFB7-D19E-4A81-B789-25B8E9445913; keys and values are
    UTF-16 LITTLE endian and are not NUL terminated.
  * The VHDX parent_linkage value is the parent's DataWriteGuid rendered as a
    braced GUID string.
  * The VHDX sector bitmap is least significant bit first, the opposite of VHD.
"""

import argparse
import os
import struct
import subprocess
import sys
import time
import uuid

SECTOR = 512
IMAGE_SIZE = 16 * 1024 * 1024
IMAGE_SECTORS = IMAGE_SIZE // SECTOR          # 32768

VHD_BLOCK_SIZE = 2 * 1024 * 1024
VHD_SECTORS_PER_BLOCK = VHD_BLOCK_SIZE // SECTOR   # 4096

VHDX_BLOCK_SIZE = 1024 * 1024
VHDX_SECTORS_PER_BLOCK = VHDX_BLOCK_SIZE // SECTOR  # 2048

VHD_DISK_TYPE_DYNAMIC = 3
VHD_DISK_TYPE_DIFFERENCING = 4

# --- content plan -----------------------------------------------------------
#
# VHD: 2 MiB blocks, so block b covers sectors [b * 4096, (b + 1) * 4096).
# The child allocates blocks 0 and 2 only, and inside those blocks its sector
# bitmap claims only the sectors it actually wrote.  Everything else, including
# sectors inside blocks 0 and 2 that the child did not claim, must come from
# the parent.
#
# Two sector plans are offered.  "mixed" puts parent-owned and child-owned
# sectors in the same bitmap byte, which is what a real differencing disk looks
# like.  "byte-aligned" keeps every child sector in a bitmap byte of its own,
# with no parent sector in the same byte.  The difference between the two
# matters: see the note on libvhdi's unmasked shift in the report.
VHD_SECTOR_PLANS = {
    # Sector 1 appears in both lists: the child must win.
    'mixed': ([0, 1, 2, 100, 4096, 5000, 28672, 32767], [1, 3, 200, 8192, 9000]),
    'byte-aligned': ([0, 2, 8, 100, 4096, 5000, 28672, 32767], [8, 200, 8192, 9000]),
    # One child sector at bit 7 of bitmap byte 1, with parent data in every
    # other sector that byte covers.  A correct reader returns the parent for
    # sectors 9 to 15.
    'bug-probe': ([9, 10, 11, 12, 13, 14, 15, 100], [8]),
}
VHD_PARENT_SECTORS, VHD_CHILD_SECTORS = VHD_SECTOR_PLANS['mixed']

# VHDX: 1 MiB blocks, so block b covers sectors [b * 2048, (b + 1) * 2048).
#   block 0  PAYLOAD_BLOCK_PARTIALLY_PRESENT + sector bitmap
#   block 3  PAYLOAD_BLOCK_FULLY_PRESENT     (shadows the parent completely)
#   others   PAYLOAD_BLOCK_NOT_PRESENT       (read from the parent)
VHDX_PARENT_SECTORS = [0, 5, 2048, 3000, 7000, 10240, 32767]
VHDX_CHILD_SECTORS = [1, 5, 6144, 6200]
VHDX_CHILD_BLOCKS_PARTIAL = [0]
VHDX_CHILD_BLOCKS_FULL = [3]


def marker(tag, n):
    """A 512 byte sector whose content names its origin and its sector number."""
    stamp = ('%s-sector-%06d.' % (tag, n)).encode('ascii')
    return (stamp * (SECTOR // len(stamp) + 1))[:SECTOR]


def build_raw(sectors, tag, size=IMAGE_SIZE):
    data = bytearray(size)
    for n in sectors:
        data[n * SECTOR:(n + 1) * SECTOR] = marker(tag, n)
    return bytes(data)


# --- VHD --------------------------------------------------------------------

def vhd_checksum(buf, offset):
    tmp = bytearray(buf)
    tmp[offset:offset + 4] = b'\x00\x00\x00\x00'
    return (~sum(tmp)) & 0xFFFFFFFF


def vhd_timestamp(when=None):
    """Seconds since 2000-01-01T00:00:00Z, the VHD epoch."""
    base = 946684800
    return int((when if when is not None else time.time()) - base)


def vhd_geometry(total_sectors):
    """A CHS triple whose product is exactly total_sectors, for 16 MiB."""
    for heads in (16, 8, 4, 2, 1):
        for spt in (63, 32, 17, 16, 8):
            if total_sectors % (heads * spt) == 0:
                cyls = total_sectors // (heads * spt)
                if 0 < cyls <= 0xFFFF:
                    return cyls, heads, spt
    raise ValueError('no exact geometry for %d sectors' % total_sectors)


def vhd_footer(disk_type, size, unique_id, timestamp, data_offset=512):
    cyls, heads, spt = vhd_geometry(size // SECTOR)
    buf = bytearray(512)
    struct.pack_into('>8sIIQI4sI4sQQHBBI', buf, 0,
                     b'conectix',        # cookie
                     0x00000002,         # features: reserved bit
                     0x00010000,         # file format version 1.0
                     data_offset,        # data offset -> dynamic header
                     timestamp,
                     b'qem2',            # creator application
                     0x00010000,         # creator version
                     b'Wi2k',            # creator host OS
                     size,               # original size
                     size,               # current size
                     cyls, heads, spt,   # disk geometry
                     disk_type)
    buf[68:84] = unique_id
    buf[84] = 0                          # saved state
    struct.pack_into('>I', buf, 64, vhd_checksum(buf, 64))
    return bytes(buf)


def vhd_locator_entry(platform_code, data_space, data_length, data_offset):
    return struct.pack('>4sIIIQ', platform_code, data_space, data_length, 0, data_offset)


def vhd_dynamic_header(table_offset, max_entries, block_size,
                       parent_uid=b'\x00' * 16, parent_timestamp=0,
                       parent_name='', locators=()):
    buf = bytearray(1024)
    struct.pack_into('>8sQQIII', buf, 0,
                     b'cxsparse',
                     0xFFFFFFFFFFFFFFFF,   # next offset
                     table_offset,
                     0x00010000,           # header version 1.0
                     max_entries,
                     block_size)
    buf[40:56] = parent_uid
    struct.pack_into('>I', buf, 56, parent_timestamp)
    struct.pack_into('>I', buf, 60, 0)     # reserved
    name = parent_name.encode('utf-16-be')
    if len(name) > 512:
        raise ValueError('parent name too long')
    buf[64:64 + len(name)] = name
    for i, entry in enumerate(locators):
        buf[576 + i * 24:576 + (i + 1) * 24] = entry
    struct.pack_into('>I', buf, 36, vhd_checksum(buf, 36))
    return bytes(buf)


def vhd_sector_bitmap(sector_numbers, block_index):
    """MSB-first per-block sector bitmap, one 512 byte sector for 2 MiB blocks."""
    nbytes = VHD_SECTORS_PER_BLOCK // 8
    nbytes = ((nbytes + SECTOR - 1) // SECTOR) * SECTOR
    bitmap = bytearray(nbytes)
    first = block_index * VHD_SECTORS_PER_BLOCK
    for n in sector_numbers:
        if first <= n < first + VHD_SECTORS_PER_BLOCK:
            i = n - first
            bitmap[i // 8] |= 0x80 >> (i % 8)
    return bytes(bitmap)


def write_dynamic_vhd(path, content, unique_id, timestamp):
    """A plain dynamic VHD holding content; every non-zero block is allocated."""
    nblocks = (len(content) + VHD_BLOCK_SIZE - 1) // VHD_BLOCK_SIZE
    bat_offset = 1536
    bat_bytes = ((nblocks * 4 + SECTOR - 1) // SECTOR) * SECTOR
    data_start = bat_offset + bat_bytes
    bitmap_bytes = len(vhd_sector_bitmap([], 0))

    bat = [0xFFFFFFFF] * nblocks
    blocks = []
    cursor = data_start
    for b in range(nblocks):
        chunk = content[b * VHD_BLOCK_SIZE:(b + 1) * VHD_BLOCK_SIZE]
        if not any(chunk):
            continue
        bat[b] = cursor // SECTOR
        blocks.append((cursor, b'\xff' * bitmap_bytes + chunk))
        cursor += bitmap_bytes + VHD_BLOCK_SIZE

    footer = vhd_footer(VHD_DISK_TYPE_DYNAMIC, len(content), unique_id, timestamp)
    header = vhd_dynamic_header(bat_offset, nblocks, VHD_BLOCK_SIZE)

    with open(path, 'wb') as fh:
        fh.write(footer)
        fh.write(header)
        fh.write(struct.pack('>%dI' % nblocks, *bat))
        fh.write(b'\x00' * (bat_bytes - nblocks * 4))
        for offset, payload in blocks:
            fh.seek(offset)
            fh.write(payload)
        fh.seek(cursor)
        fh.write(footer)
    return unique_id


def write_differencing_vhd(path, child_sectors, size, unique_id, timestamp,
                           parent_uid, parent_path, parent_relative):
    """A differencing VHD whose sector bitmaps claim only child_sectors."""
    nblocks = (size + VHD_BLOCK_SIZE - 1) // VHD_BLOCK_SIZE
    bitmap_bytes = len(vhd_sector_bitmap([], 0))

    # Layout: footer copy | dynamic header | locator data | BAT | blocks | footer
    loc_rel_offset = 1536
    loc_abs_offset = loc_rel_offset + SECTOR
    bat_offset = loc_abs_offset + SECTOR
    bat_bytes = ((nblocks * 4 + SECTOR - 1) // SECTOR) * SECTOR
    data_start = bat_offset + bat_bytes

    rel_blob = parent_relative.encode('utf-16-le')
    abs_blob = parent_path.encode('utf-16-le')
    if len(rel_blob) > SECTOR or len(abs_blob) > SECTOR:
        raise ValueError('locator data does not fit in one sector')
    locators = [
        # data_space is a byte count: that is what Hyper-V writes, despite the
        # Microsoft spec describing it as a sector count.
        vhd_locator_entry(b'W2ru', SECTOR, len(rel_blob), loc_rel_offset),
        vhd_locator_entry(b'W2ku', SECTOR, len(abs_blob), loc_abs_offset),
    ]

    touched = {}
    for n in child_sectors:
        touched.setdefault(n // VHD_SECTORS_PER_BLOCK, []).append(n)

    bat = [0xFFFFFFFF] * nblocks
    blocks = []
    cursor = data_start
    for b in sorted(touched):
        payload = bytearray(VHD_BLOCK_SIZE)
        first = b * VHD_SECTORS_PER_BLOCK
        for n in touched[b]:
            i = n - first
            payload[i * SECTOR:(i + 1) * SECTOR] = marker('CHILD', n)
        bat[b] = cursor // SECTOR
        blocks.append((cursor, vhd_sector_bitmap(touched[b], b) + bytes(payload)))
        cursor += bitmap_bytes + VHD_BLOCK_SIZE

    footer = vhd_footer(VHD_DISK_TYPE_DIFFERENCING, size, unique_id, timestamp)
    header = vhd_dynamic_header(bat_offset, nblocks, VHD_BLOCK_SIZE,
                                parent_uid=parent_uid,
                                parent_timestamp=timestamp,
                                parent_name=parent_path,
                                locators=locators)

    with open(path, 'wb') as fh:
        fh.write(footer)
        fh.write(header)
        fh.seek(loc_rel_offset)
        fh.write(rel_blob)
        fh.seek(loc_abs_offset)
        fh.write(abs_blob)
        fh.seek(bat_offset)
        fh.write(struct.pack('>%dI' % nblocks, *bat))
        fh.write(b'\x00' * (bat_bytes - nblocks * 4))
        for offset, payload in blocks:
            fh.seek(offset)
            fh.write(payload)
        fh.seek(cursor)
        fh.write(footer)


# --- VHDX -------------------------------------------------------------------

REGION_BAT = uuid.UUID('2DC27766-F623-4200-9D64-115E9BFD4A08')
REGION_METADATA = uuid.UUID('8B7CA206-4790-4B9A-B8FE-575F050F886E')
META_FILE_PARAMETERS = uuid.UUID('CAA16737-FA36-4D43-B3B6-33F0AA44E76B')
META_PARENT_LOCATOR = uuid.UUID('A8D35F2D-B30B-454D-ABF7-D3D84834AB0C')
PARENT_LOCATOR_TYPE = uuid.UUID('B04AEFB7-D19E-4A81-B789-25B8E9445913')

VHDX_BAT_NOT_PRESENT = 0
VHDX_BAT_FULLY_PRESENT = 6
VHDX_BAT_PARTIALLY_PRESENT = 7
VHDX_SB_PRESENT = 6

METADATA_FLAG_IS_USER = 0x1
METADATA_FLAG_IS_VIRTUAL_DISK = 0x2
METADATA_FLAG_IS_REQUIRED = 0x4


def qemu_img(*args):
    cmd = ['qemu-img'] + list(args)
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def vhdx_regions(data):
    out = {}
    off = 0x30000
    sig, _csum, count, _res = struct.unpack_from('<4sIII', data, off)
    assert sig == b'regi', sig
    for i in range(count):
        eo = off + 16 + i * 32
        guid = uuid.UUID(bytes_le=bytes(data[eo:eo + 16]))
        fo, ln, _req = struct.unpack_from('<QII', data, eo + 16)
        out[guid] = (fo, ln)
    return out


def vhdx_metadata_items(data, region_offset):
    sig, _res, count, _res2 = struct.unpack_from('<8sHHI', data, region_offset)
    assert sig == b'metadata', sig
    items = []
    for i in range(count):
        eo = region_offset + 32 + i * 32
        guid = uuid.UUID(bytes_le=bytes(data[eo:eo + 16]))
        ioff, ilen, flags, _res3 = struct.unpack_from('<IIII', data, eo + 16)
        items.append((guid, ioff, ilen, flags, eo))
    return count, items


def vhdx_data_write_guid(data):
    """The DataWriteGuid of the header with the higher sequence number."""
    best = None
    for off in (0x10000, 0x20000):
        sig, _csum, seq = struct.unpack_from('<4sIQ', data, off)
        if sig != b'head':
            continue
        if best is None or seq > best[0]:
            best = (seq, uuid.UUID(bytes_le=data[off + 32:off + 48]))
    return best[1]


def vhdx_parent_locator_item(parent_data_write_guid, relative_path, absolute_path):
    """Serialise a VHDX parent locator metadata item.

    Header: 16 byte locator type GUID, 2 reserved, 2 key/value count.
    Then one 12 byte entry per pair: key offset, value offset, key length,
    value length -- all offsets relative to the start of the item.
    Keys and values are UTF-16LE and are not NUL terminated.
    """
    pairs = [
        ('parent_linkage', '{%s}' % parent_data_write_guid),
        ('relative_path', relative_path),
        ('absolute_win32_path', absolute_path),
    ]
    header = struct.pack('<16sHH', PARENT_LOCATOR_TYPE.bytes_le, 0, len(pairs))
    entries = bytearray()
    blob = bytearray()
    base = len(header) + 12 * len(pairs)
    for key, value in pairs:
        kb = key.encode('utf-16-le')
        vb = value.encode('utf-16-le')
        koff = base + len(blob)
        blob += kb
        voff = base + len(blob)
        blob += vb
        entries += struct.pack('<IIHH', koff, voff, len(kb), len(vb))
    return bytes(header) + bytes(entries) + bytes(blob)


def vhdx_sector_bitmap_block(sector_numbers):
    """A 1 MiB VHDX sector bitmap block; least significant bit first."""
    bitmap = bytearray(1024 * 1024)
    for n in sector_numbers:
        bitmap[n // 8] |= 1 << (n % 8)
    return bytes(bitmap)


def patch_vhdx_child(path, parent_path, parent_relative, parent_data_write_guid,
                     partial_blocks, full_blocks, child_sectors):
    data = bytearray(open(path, 'rb').read())
    regions = vhdx_regions(data)
    meta_off, meta_len = regions[REGION_METADATA]
    bat_off, bat_len = regions[REGION_BAT]
    count, items = vhdx_metadata_items(data, meta_off)

    # 1. Set the HasParent bit in the file parameters item.
    fp = [it for it in items if it[0] == META_FILE_PARAMETERS]
    assert len(fp) == 1, 'expected exactly one file parameters item'
    _guid, ioff, ilen, _flags, _eo = fp[0]
    block_size, fp_flags = struct.unpack_from('<II', data, meta_off + ioff)
    struct.pack_into('<II', data, meta_off + ioff, block_size, fp_flags | 0x2)

    # 2. Append a parent locator item after the last item's data.
    end = max(ioff + ilen for _g, ioff, ilen, _f, _e in items)
    item_off = (end + 15) & ~15
    item = vhdx_parent_locator_item(parent_data_write_guid, parent_relative, parent_path)
    assert item_off + len(item) <= meta_len, 'metadata region too small'
    data[meta_off + item_off:meta_off + item_off + len(item)] = item
    entry_off = meta_off + 32 + count * 32
    data[entry_off:entry_off + 16] = META_PARENT_LOCATOR.bytes_le
    struct.pack_into('<IIII', data, entry_off + 16,
                     item_off, len(item), METADATA_FLAG_IS_REQUIRED, 0)
    struct.pack_into('<H', data, meta_off + 10, count + 1)

    # 3. Rewrite the BAT: only the blocks the child owns stay present.
    nblocks = (IMAGE_SIZE + block_size - 1) // block_size
    chunk_ratio = (0x800000 * 512) // block_size
    for b in range(nblocks):
        eo = bat_off + b * 8
        (entry,) = struct.unpack_from('<Q', data, eo)
        file_offset_mb = (entry >> 20) & 0xFFFFFFFFFFF
        if b in partial_blocks:
            state = VHDX_BAT_PARTIALLY_PRESENT
        elif b in full_blocks:
            state = VHDX_BAT_FULLY_PRESENT
        else:
            state = VHDX_BAT_NOT_PRESENT
            file_offset_mb = 0
        struct.pack_into('<Q', data, eo, (file_offset_mb << 20) | state)

    # 4. If any block is partially present, append a sector bitmap block and
    #    point the chunk's sector bitmap BAT entry at it.
    if partial_blocks:
        claimed = []
        for n in child_sectors:
            if n // (block_size // SECTOR) in partial_blocks:
                claimed.append(n)
        sb_offset = (len(data) + 0xFFFFF) & ~0xFFFFF
        data.extend(b'\x00' * (sb_offset - len(data)))
        data.extend(vhdx_sector_bitmap_block(claimed))
        sb_index = chunk_ratio
        struct.pack_into('<Q', data, bat_off + sb_index * 8,
                         ((sb_offset // (1024 * 1024)) << 20) | VHDX_SB_PRESENT)

    open(path, 'wb').write(bytes(data))
    return block_size, chunk_ratio


# --- driver -----------------------------------------------------------------

def main():
    global VHD_PARENT_SECTORS, VHD_CHILD_SECTORS
    parser = argparse.ArgumentParser()
    parser.add_argument('outdir')
    parser.add_argument('--vhd-plan', choices=sorted(VHD_SECTOR_PLANS), default='mixed')
    args = parser.parse_args()
    VHD_PARENT_SECTORS, VHD_CHILD_SECTORS = VHD_SECTOR_PLANS[args.vhd_plan]
    out = os.path.abspath(args.outdir)
    os.makedirs(out, exist_ok=True)

    def p(name):
        return os.path.join(out, name)

    stamp = vhd_timestamp(1757030400)

    # ---------------- VHD ----------------
    vhd_parent_raw = build_raw(VHD_PARENT_SECTORS, 'PARENT')
    vhd_child_raw = build_raw(VHD_CHILD_SECTORS, 'CHILD')
    vhd_expected = bytearray(vhd_parent_raw)
    for n in VHD_CHILD_SECTORS:
        vhd_expected[n * SECTOR:(n + 1) * SECTOR] = marker('CHILD', n)

    open(p('vhd-parent-only.raw'), 'wb').write(vhd_parent_raw)
    open(p('vhd-child-only.raw'), 'wb').write(vhd_child_raw)
    open(p('vhd-expected.raw'), 'wb').write(bytes(vhd_expected))

    parent_uid = uuid.UUID('11111111-2222-3333-4444-555555555555').bytes
    child_uid = uuid.UUID('66666666-7777-8888-9999-aaaaaaaaaaaa').bytes
    write_dynamic_vhd(p('vhd-parent.vhd'), vhd_parent_raw, parent_uid, stamp)
    write_differencing_vhd(p('vhd-child.vhd'), VHD_CHILD_SECTORS, IMAGE_SIZE,
                           child_uid, stamp, parent_uid,
                           parent_path=p('vhd-parent.vhd'),
                           parent_relative='.\\vhd-parent.vhd')

    # ---------------- VHDX ----------------
    vhdx_parent_raw = build_raw(VHDX_PARENT_SECTORS, 'PARENT')
    vhdx_child_raw = build_raw(VHDX_CHILD_SECTORS, 'CHILD')
    open(p('vhdx-parent-only.raw'), 'wb').write(vhdx_parent_raw)
    open(p('vhdx-child-only.raw'), 'wb').write(vhdx_child_raw)
    open(p('vhdx-parent-src.raw'), 'wb').write(vhdx_parent_raw)
    open(p('vhdx-child-src.raw'), 'wb').write(vhdx_child_raw)

    for name in ('vhdx-parent.vhdx', 'vhdx-child.vhdx'):
        if os.path.exists(p(name)):
            os.unlink(p(name))
    qemu_img('convert', '-f', 'raw', '-O', 'vhdx',
             '-o', 'block_size=%d,log_size=1M' % VHDX_BLOCK_SIZE,
             p('vhdx-parent-src.raw'), p('vhdx-parent.vhdx'))
    qemu_img('convert', '-f', 'raw', '-O', 'vhdx',
             '-o', 'block_size=%d,log_size=1M' % VHDX_BLOCK_SIZE,
             p('vhdx-child-src.raw'), p('vhdx-child.vhdx'))

    parent_dwg = vhdx_data_write_guid(open(p('vhdx-parent.vhdx'), 'rb').read())
    block_size, chunk_ratio = patch_vhdx_child(
        p('vhdx-child.vhdx'),
        parent_path=p('vhdx-parent.vhdx'),
        parent_relative='.\\vhdx-parent.vhdx',
        parent_data_write_guid=parent_dwg,
        partial_blocks=set(VHDX_CHILD_BLOCKS_PARTIAL),
        full_blocks=set(VHDX_CHILD_BLOCKS_FULL),
        child_sectors=VHDX_CHILD_SECTORS)

    # The expected composition follows straight from the BAT states above.
    vhdx_expected = bytearray(IMAGE_SIZE)
    spb = block_size // SECTOR
    for n in range(IMAGE_SECTORS):
        b = n // spb
        if b in VHDX_CHILD_BLOCKS_FULL:
            src = 'child-block'
        elif b in VHDX_CHILD_BLOCKS_PARTIAL:
            src = 'child' if n in VHDX_CHILD_SECTORS else 'parent'
        else:
            src = 'parent'
        if src == 'child' or (src == 'child-block' and n in VHDX_CHILD_SECTORS):
            vhdx_expected[n * SECTOR:(n + 1) * SECTOR] = marker('CHILD', n)
        elif src == 'parent' and n in VHDX_PARENT_SECTORS:
            vhdx_expected[n * SECTOR:(n + 1) * SECTOR] = marker('PARENT', n)
    open(p('vhdx-expected.raw'), 'wb').write(bytes(vhdx_expected))

    print('output directory: %s' % out)
    print('vhd  block size %d, %d blocks; child allocates blocks %s'
          % (VHD_BLOCK_SIZE, IMAGE_SIZE // VHD_BLOCK_SIZE,
             sorted({n // VHD_SECTORS_PER_BLOCK for n in VHD_CHILD_SECTORS})))
    print('vhdx block size %d, chunk ratio %d; partial blocks %s, full blocks %s'
          % (block_size, chunk_ratio, VHDX_CHILD_BLOCKS_PARTIAL, VHDX_CHILD_BLOCKS_FULL))
    print('vhd  parent sectors %s' % sorted(set(VHD_PARENT_SECTORS)))
    print('vhd  child  sectors %s' % sorted(set(VHD_CHILD_SECTORS)))
    print('vhdx parent sectors %s' % sorted(set(VHDX_PARENT_SECTORS)))
    print('vhdx child  sectors %s' % sorted(set(VHDX_CHILD_SECTORS)))
    for name in sorted(os.listdir(out)):
        print('  %-24s %d' % (name, os.path.getsize(p(name))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

The composer it is scored with:

```python
"""Compose a libvhdi chain to a raw file.

usage: compose.py OUT CHILD [PARENT [GRANDPARENT ...]]
If only CHILD is given, no parent is attached (child-only read).
"""
import sys
import pyvhdi

out_path = sys.argv[1]
paths = sys.argv[2:]

files = []
for p in paths:
    f = pyvhdi.file()
    f.open(p, 'r')
    files.append(f)

for i in range(len(files) - 1):
    files[i].set_parent(files[i + 1])

top = files[0]
size = top.get_media_size()
sys.stderr.write('media_size=%d disk_type=%d\n' % (size, top.get_disk_type()))
try:
    sys.stderr.write('parent_identifier=%s\n' % top.get_parent_identifier())
    sys.stderr.write('parent_filename=%s\n' % top.get_parent_filename())
except Exception as exc:  # noqa: BLE001
    sys.stderr.write('no parent info: %s\n' % exc)

top.seek(0)
with open(out_path, 'wb') as fh:
    remaining = size
    while remaining > 0:
        chunk = top.read(min(1024 * 1024, remaining))
        if not chunk:
            raise SystemExit('short read with %d bytes remaining' % remaining)
        fh.write(chunk)
        remaining -= len(chunk)
sys.stderr.write('wrote %d bytes to %s\n' % (size, out_path))
```

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work aligns with
it. Step 1a additionally reports its go/no-go to the management
session before step 1b begins: the verdict changes the shape of
three later phases, and it is cheap to hear early and expensive
to discover late.
