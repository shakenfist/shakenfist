# Phase 2 — real differencing fixtures

Phase 2 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Give the rest of the plan something real to read. Today instar
has no differencing chain to test against: the one fixture named
for it is a type marker with no parent, and there is no VHDX
differencing fixture at all. Phase 3 cannot write a parser
without input, and phases 8 and 15 cannot assert composition
without chains instar did not write.

This phase produces those chains, and the adversarial
parent-locator images the parse layer will be attacked with.

No source file under `src/` changes in this phase.

## Planning effort

Medium. The structures were pinned in phase 1 and the generator
pattern already exists; what is left is judgement about which
fixtures to make and where they live.

## Review effort

Medium, with one exception: the sector layout of the VHD
happy-path chain is high. libvhdi mis-decodes a VHD sector bitmap
once parent-owned and child-owned sectors share a byte (phase 1
defect A), so a fixture that ignores this will fail
content-exactness in phase 15 for reasons that are not instar's.
The management session checks that layout specifically.

## Scope

In scope:

* A real VHD differencing chain and a real VHDX differencing
  chain, each with content the parent and child both contribute
  to, plus the raw image each chain is intended to compose to.
* The adversarial parent-locator fixtures from `instar-testdata`
  `PLAN-extra-coverage.md` priority 7, per the master plan's
  open question 6.
* Registration in `tests/manifest.json`, with the honest
  `skip_qemu_img` and `run_in_ci` settings for images no test
  reads yet.
* Recording what the existing `vhd-differencing.vhd` fixture
  actually is, since the master plan currently overstates it.

Out of scope:

* Any change under `src/`, and any test that consumes the new
  fixtures. Phase 3 is the first consumer; phase 8 writes the
  integration tests and flips `run_in_ci`.
* Cross-version `expected-outputs` baselines. qemu-img reads a
  differencing VHD wrongly and refuses a differencing VHDX, so a
  baseline would enshrine a wrong answer. The oracle is libvhdi
  and it arrives in phase 15's harness.
* Deleting the orphaned `vhd-diff-base.vhd` — see decision 5.
* Fixing the `generated_by` claim's ambiguity anywhere but the
  manifest entry it belongs to.

## What the survey found

The master plan's phase 2 premises are mostly right and wrong in
one place that matters.

**Wrong: "Nothing in `instar-testdata/scripts/` generates them."**
True as written and misleading. The generator exists — as
`scripts/create-vhd-testdata.sh` **in instar**, 198 lines,
defaulting its output to `../instar-testdata/custom/format-coverage`
and documented at `docs/testing.md:1318`. Its differencing
strategy is explicit at `scripts/create-vhd-testdata.sh:150-152`:
qemu-img cannot create differencing VHDs, so it creates a dynamic
one and patches `disk_type` from 3 to 4 in both footers,
recomputing the checksums. That is precisely why the fixture is a
type marker, and it was deliberate rather than an accident.

This changes the phase's shape. The house pattern is **generator
in instar, binaries in instar-testdata, registration in instar's
`tests/manifest.json`** — so phase 2 spans two repositories and
two pull requests, not one. The master plan's Execution table
says this phase lands in `instar-testdata`; it lands in both, and
its `Merged` cell needs both records. Corrected at source in the
master plan as part of this planning commit.

**Right, and worth more precision:** the existing fixture is a
type marker, but the manifest never claimed otherwise. Its entry
at `tests/manifest.json:197-206` reads "Differencing VHD
(disk_type=4) patched from dynamic for type acceptance testing"
— an accurate description of a deliberately synthetic image. The
master plan says it "does not exercise what its name implies",
which is fair about the name and unfair about the record. Phase 1
already softened this; this phase should not repeat the harsher
framing.

**Two consumers exist**, and they constrain what this phase may
do: `tests/test_check_formats.py:1877` and `:1900` assert that
`check` succeeds with zero corruptions and that `info` reports
`vpc` for `vhd-differencing`. Replacing that fixture with a real
chain would change what those tests exercise.

**An orphan.** `custom/format-coverage/vhd-diff-base.vhd` (2 MiB,
LFS) is referenced by nothing: not `tests/manifest.json`, not
`create-vhd-testdata.sh`, not any test, and nothing in
`instar-testdata` names it either. It appears to be the abandoned
other half of an earlier attempt at a real chain.

**LFS applies.** `.gitattributes` in `instar-testdata` tracks
`*.vhd`, `*.vhdx`, `*.raw` and `*.img` through git-lfs, so every
fixture this phase adds is an LFS object and the push needs the
Maintainer-scoped token that `main` requires.

## Decisions

1. **Extend `scripts/create-vhd-testdata.sh` rather than write a
   new generator**, and lift phase 1's throwaway generator into
   it. The script already owns VHD fixture generation, already
   knows the footer and checksum layout, and is already
   documented in `docs/testing.md`. A second script generating
   overlapping structures is how the two drift. Phase 1's
   appendix supplies the parent-locator and VHDX halves it lacks.
2. **Supplement, never replace.** `vhd-differencing.vhd` keeps
   its bytes, its id and its description; two tests depend on it
   and its stated purpose is type acceptance, which it serves.
   New fixtures take new ids.
3. **Two happy-path VHD chains, not one.** One byte-aligned,
   where no bitmap byte mixes parent-owned and child-owned
   sectors, for content-exact assertions; one realistically
   mixed, which is what Hyper-V produces and what the parser must
   survive. The mixed chain is registered with its libvhdi
   caveat in the description, so phase 15 does not read a known
   oracle defect as an instar regression.
4. **Fixtures are registered dark.** Every new entry gets
   `run_in_ci: false`. Nothing consumes them until phase 3, so
   phase 8 flips `run_in_ci` for the happy-path pair when there
   are tests to run.

   `skip_qemu_img: true` goes only where qemu-img's reading is
   wrong by construction, which is the nine differencing
   children -- the two VHD children, the VHDX child and the six
   adversarial locator fixtures -- and nothing else. It does
   **not** go on the two parents or the three composed `.raw`
   files. Those five are plain images, and qemu-img reads them
   correctly: measured, `qemu-img convert` recovers exactly
   sectors 0, 1, 2, 8, 100, 4096, 5000, 28672 and 32767 from
   `vhd-diff-parent.vhd` and exactly sectors 0, 5, 2048, 3000,
   7000, 10240 and 32767 from `vhdx-diff-parent.vhdx`, which are
   precisely the sector sets the generator writes. Suppressing
   parity on them would throw away free cross-checking of the
   plain dynamic VHD and VHDX writers the whole chain rests on.
5. **Leave the orphan alone, and ask.** `vhd-diff-base.vhd` looks
   deletable and this phase does not delete it: `instar-testdata`
   is shared with imago, and a fixture unreferenced from instar
   is not thereby unreferenced. The phase records it and puts the
   question to the operator; removing an LFS object is cheap to
   do later and awkward to undo.
6. **The adversarial set comes in the same generator run.** Per
   master plan open question 6, priority 7's five images
   (absolute `/etc/passwd`, relative `../../../etc/passwd`, UNC
   `\\attacker\share\probe`, the URL
   `http://attacker.example/probe`, and eight mutually
   disagreeing locators) are generated alongside the happy-path
   chains. They are cheap once the locator writer exists, and
   phase 3 needs them the moment it parses a locator table.

   A **sixth** image is added beyond priority 7: an over-long
   parent name filling the whole 512-byte field with no NUL
   terminator. Priority 7 does not name it. It is here because
   phase 5 has to pick a length bound and the boundary is
   exactly where a reader is most likely to be wrong -- which it
   proved to be, turning up libvhdi defect C and moving phase
   5's rule from "refuse above 512 bytes" to "refuse at 512
   bytes" (255 code units, not 256). Six fixtures ship, not
   five.
7. **The expected composition ships with the chain.** Each chain
   gets its intended composed image as a `.raw` sibling, produced
   by the generator, so phase 15 compares against a recorded
   intent rather than against whatever the oracle happens to say
   that day.

8. **The reproducibility claim needs no qemu-version caveat,
   because it is not qemu-version dependent.** The second review
   round asked for one, reasoning that a VHD footer records a
   creator application and creator version and that
   `vhd-fixed.vhd` and `vhd-differencing.vhd` come from
   `qemu-img`. Measured, neither premise holds. `vhd-fixed.vhd`
   is not a `qemu-img` product at all: it is struct-packed in
   this script with `creator_app` `imgo`. `vhd-differencing.vhd`
   is, but qemu's creator fields are constants of its `vpc`
   driver rather than its own version -- `qemu` and
   `0x00050003` -- so the patch step's pinned timestamp and
   unique id were the only two fields that ever needed pinning.
   Regenerating on qemu 10.0.11 reproduces both files byte for
   byte against a checkout the review reports was generated
   under 7.2.22. The header comment now records that as a
   measured fact instead, which is more useful than either
   silence or a caveat that would send a future reader looking
   for a qemu difference that is not there.

The decision most likely to be argued with is 3. Two VHD chains
where one would do is more fixtures to maintain, and the
byte-aligned one is artificial — no real tool produces images
that avoid sharing bitmap bytes. The alternative is a single
realistic chain whose expected output encodes a third-party
tool's bug, which is worse: the expected file would have to
change when libvhdi is fixed, and nothing in it would say why.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Extend `scripts/create-vhd-testdata.sh` (instar) with differencing-chain generation, lifting the generator from the appendix of `docs/plans/PLAN-differencing-phase-01-pin.md` — it is known-good and its structure facts are measured, so port it rather than rewriting. Keep the script's existing two fixtures byte-identical: it must remain idempotent for `vhd-fixed.vhd` and `vhd-differencing.vhd`, verified by regenerating into a temp dir and `cmp`-ing against the current files in `../instar-testdata/custom/format-coverage/`. Add: a VHD parent + byte-aligned child, a VHD parent + mixed child, a VHDX parent + child exercising PARTIALLY_PRESENT / FULLY_PRESENT / NOT_PRESENT, and the intended composed `.raw` for each chain. Structure facts are in the phase 1 plan's "The structure pin" section — parent unique id at absolute 552, parent unicode name at 576 in UTF-16BE, locator entries from 1088 with UTF-16LE platform data and a byte-count `platform_data_space`, VHDX `parent_linkage` = the parent's DataWriteGuid as a braced string. Do not invent offsets; that section is the authority. |
| 2b | medium | opus | worktree | Add the five adversarial parent-locator fixtures from `instar-testdata/docs/plans/PLAN-extra-coverage.md` priority 7 to the same script -- absolute `/etc/passwd`, relative `../../../etc/passwd`, UNC `\\\\attacker\\share\\probe`, the URL `http://attacker.example/probe`, and one with eight mutually disagreeing locator entries -- plus a sixth beyond priority 7: an over-long path filling the 512-byte parent-name field with no NUL terminator, which is where phase 5's length bound has to be decided. These are hostile *paths*, not malformed structures: each image must otherwise be a well-formed differencing VHD, so that a parser reaching the path has already passed everything else. Nothing in the generator or the fixtures may reference a real file outside the output directory. |
| 2c | medium | sonnet | none | Register the new fixtures in `tests/manifest.json` (instar), following the shape of the existing `vhd-differencing` entry at `:197-206`. Every entry: `run_in_ci: false`, `skip_qemu_img: true`, `generated_by: scripts/create-vhd-testdata.sh`, a description saying what the image is *for*, and for the mixed VHD chain a description that names the libvhdi sector-bitmap caveat. Do not touch the existing `vhd-differencing` entry. Add `sha256` where the neighbouring entries carry one. Update `docs/testing.md:1318`'s description of the script to cover what it now generates. |
| 2d | medium | sonnet | none | (As executed, this opened a merge request, which was wrong for this repository -- see decision 5; the commits landed directly on `main`.) Land the fixtures in `instar-testdata`: the generated binaries into `custom/format-coverage/`, LFS-tracked (`.gitattributes` already covers `.vhd`, `.vhdx`, `.raw`). Confirm with `git lfs ls-files` that every new binary is an LFS object and not a bare blob, and that no file was committed as a 131-byte pointer by mistake. The push needs the Maintainer-scoped token, since `main` is protected. Report the branch, the PR number, and the merge commit once it lands, because the master plan's `Merged` cell for this phase records both repositories. |
| 2e | low | sonnet | none | Close the phase: verify the definition of done item by item, set phase 2 to Complete in `docs/plans/PLAN-differencing.md` and `docs/plans/index.md`, fill the `Merged` cell with both records, and present the commits. Do not commit. |

## Risks and mitigations

* **The generator stops being idempotent** for the two fixtures
  that already exist, silently changing images two tests depend
  on. Mitigated by step 2a's explicit `cmp` against the current
  files; the management session re-runs that check rather than
  trusting the report.
* **A fixture encodes libvhdi's bug as expected output.**
  Mitigated by decision 3 and by the mixed chain's description
  naming the caveat at the point of registration.
* **An LFS pointer lands instead of a binary**, which has bitten
  this testdata repository before and presents as mass "file
  format: unknown" failures far from the cause. Mitigated by step
  2d's `git lfs ls-files` check.
* **The adversarial fixtures point at something real.** A
  generator that writes `/etc/passwd` into a locator is fine; a
  generator that *reads* it is not. Mitigated by 2b's constraint
  and by the management session reading the generated paths
  before the testdata PR opens.
* **Two repositories, one phase.** The instar half is unusable
  until the testdata half merges, and the testdata half is
  unreferenced until the instar half merges. Mitigated by
  ordering: testdata first (binaries are inert), then instar's
  manifest entries pointing at them.

## Definition of done

* `scripts/create-vhd-testdata.sh` regenerates `vhd-fixed.vhd`
  and `vhd-differencing.vhd` byte-identically to the files
  currently in `instar-testdata`, proven by `cmp`.
* Three chains exist as fixtures — VHD byte-aligned, VHD mixed,
  VHDX — each with its parent, its child and its intended
  composed `.raw`.
* libvhdi resolves each new chain and its composition matches the
  shipped `.raw`, except for the mixed VHD chain, whose deviation
  is exactly the sectors phase 1 defect A predicts and is
  recorded as such.
* The five priority 7 adversarial fixtures exist -- absolute,
  relative, UNC, URL and conflicting -- plus the over-long
  fixture added beyond priority 7, six in total, and each is a
  structurally valid differencing VHD that differs from the
  happy-path child only in its locator paths, its parent unicode
  name, its own child unique id and the two checksums those
  force.
* Those six are written to `custom/audit/`, which is where
  `tests/manifest.json` and `docs/testing.md` say they live, and
  the generator puts them there rather than in the
  format-coverage output directory.
* Regeneration drift is caught by two standing mechanisms rather
  than by a one-off `cmp`: every fixture carries a `sha256` in
  the manifest, so `tests/base.py`'s `skip_if_hash_mismatch`
  fires the moment a consumer appears; and a default generator
  run is now a byte-exact no-op, because the only
  non-reproducible outputs -- the VHDX pair -- are skipped
  unless `REGEN_VHDX=1` is passed. Re-running the script against
  the testdata checkout and finding `git status` clean is
  therefore the check, and it needs no new flag. A one-argument
  run stays entirely inside the directory it is given -- the
  audit directory defaults to `<output-dir>/audit` rather than
  independently of the output directory -- so a scratch dry-run
  cannot write the adversarial six into the real checkout.

  On the skip path the composed `.raw` is still written, but
  from the shipped bytes: the block size, payload block states,
  sector bitmap and written sectors are read back out of the
  committed VHDX pair and asserted against the constants before
  the composition is built. Without that, a skipped run derived
  the composition entirely from constants nothing had checked,
  and `vhdx_composition`'s block-size assert compared
  `VHDX_BLOCK_SIZE` with itself.
* `git lfs ls-files` in `instar-testdata` lists every new binary;
  no new file is a bare blob or a 131-byte pointer.
* Every new fixture is in `tests/manifest.json` with
  `run_in_ci: false`, with `skip_qemu_img: true` on the nine
  differencing children only, and with a `sha256` so
  `tests/base.py`'s drift check is live for all fourteen. The two
  pre-existing fixtures the footer pinning was done for,
  `vhd-fixed` and `vhd-differencing`, now carry a `sha256` too:
  they are the only two with live CI consumers, so they are the
  two where the drift check can fire today. Adding a hash leaves
  those entries otherwise untouched, which decision 2 requires.

* The three composed `.raw` images carry
  `unsafe_quirks_required: true`. They have no partition table --
  sector 0 is a live `PARENT` marker in all three chains, so
  stamping an MBR signature there would falsify the composition
  -- and without the flag instar's secure default rejects them as
  unknown format, which would have surfaced as a phase 8 failure
  at the point `run_in_ci` is flipped.
* `tests/test_check_formats.py` still passes unchanged, proving
  the existing fixture was not disturbed.
* `git diff --name-only develop...HEAD -- src/` is empty.
* `pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this phase and how the intended work aligns with
it. Step 2a additionally shows the management session its
proposed sector layout for the two VHD chains before generating
them: the layout is the one thing here that is cheap to agree
now and expensive to discover wrong in phase 15.
