# Multi-extent VMDK phase 2: fixtures and baselines

## Status: Not started

Master plan: `PLAN-multi-extent-vmdk.md`.

## Planning effort

Medium. The phase follows a pattern `PLAN-bat-population-phase-02-fixtures.md`
established, and it writes no instar behaviour. What raised it above
mechanical is that the corpus already contains more multi-extent VMDK
than the master plan's intent assumes, and that one of the two obvious
fixture shapes cannot be stored at all -- both settled below by
measurement rather than left to the implementer.

## Decisions already taken by the operator

From the master plan, resolved 2026-11-16:

1. Sequential extent rotation, not simultaneous indexed output devices.
2. The read-side extent ceiling is fixed inside this plan, as phase 3.

Neither constrains this phase directly, but decision 2 is why a fixture
past the 15-extent ceiling is in scope here: phase 3 needs something to
prove itself against.

Open questions 3 to 6 remain open. This phase settles none of them; it
does not touch extent path derivation (question 4) or any instar-side
limit (question 5), because it writes no instar behaviour.

## Scope

In scope:

* Four qemu-written `twoGbMaxExtentSparse` fixtures in
  `instar-testdata/custom/format-coverage/`, with their
  `tests/manifest.json` entries.
* A generator script for them in instar's `scripts/`, pinned to a
  static qemu-img build rather than the host's.
* A test-time generator for `twoGbMaxExtentFlat` split images, which
  are measured below to be unstorable.
* Baselines for the four new fixtures across all 80 static qemu builds,
  for every image-driven command, with the derived profiles validated.
* All three `info` divergences on multi-file VMDK, two found by the
  survey and the third while fixing them: the
  top-level allocation figure and the order of `children[]`, in the
  VMM, with the test helper that masks the first of them, and the
  fixture un-flagged that lets a multi-extent flat image be compared
  at all. An issue records them, and the measurements go into the
  master plan.

Out of scope:

* Reading `twoGbMaxExtent*` images (phase 3) and the structural
  comparison arm of the differential fuzzer (phase 16).
* Baselines for the three VMDK fixtures that stay `skip_qemu_img`.
  `vmdk-twogb-flat` and `vmdk-multi-extent` name extents that
  deliberately do not exist, so the only baseline they could have is
  qemu's error text, and error-text parity is its own subject;
  `vmdk-flat-parent` and `vmdk-flat-with-parent` are a
  `parentFileNameHint` chain, which is neither multi-extent nor this
  plan's. Decision 7 records the reason against each.
* `instar map` on flat sources, refused for every flat source and wider
  than this plan.

## What the survey found

The master plan's phase 2 intent is **stale in three ways**, two of
them load-bearing. All three are corrected at source in the master plan
and `docs/plans/index.md` as part of this phase's planning commit;
a later step should not redo that.

### The past-the-ceiling fixture already exists

The intent says to add "at least one image past the current 15-extent
ceiling so phase 3 has something to prove itself against". The corpus
already has one: `osboxes-vmdk-split-sparse`
(`downloaded/osboxes/android-x86-5.1-rc1-split-sparse/`), a real VMware
Workstation `twoGbMaxExtentSparse` image of 26 extents, 100 GiB virtual
in 1.3 GiB, with full baselines -- 261 files each under
`expected-outputs/qemu-img-human` and `qemu-img-json`, 243 each under
`check-human`, `check-json` and `map-human`, 249 under `map-json`, and
486 each under `measure-human` and `measure-json`.

It is not a parity fixture, though, and the reason is worth recording:
**its extents are 8304640 sectors (3.96 GiB) each**, not 2 GiB, under a
descriptor that says `createType="twoGbMaxExtentSparse"`. The last is
2099200 sectors. So the create type does not imply an extent size, and
a reader that infers 2 GiB from the create type rather than reading each
extent's declared sector count is wrong on the one real-world image in
the corpus. That descriptor also carries `encoding="windows-1252"`,
`isNativeSnapshot="no"`, and extent filenames containing spaces.

It therefore covers the device-slot ceiling and the awkward real-world
spelling, and covers qemu's own geometry not at all.

### No qemu-written split fixture exists, and the flat one cannot

The intent reads as though both subformats can be added the same way.
Measured, they cannot.

A split-sparse extent is a small file: pinned case
`vmdk-multi-sparse-21G` is 11 extents totalling 3,473,408 bytes
(327,680 for each full extent, 196,608 for the 1 GiB tail). A split-flat
extent is a full-length sparse file: `vmdk-multi-flat-21G` totals
22,548,578,304 bytes apparent.

Sparse files do not survive git-LFS. Measured on
`downloaded/qemu-iotests/d2v-zerofilled.vhd`: 264,308,224 bytes
apparent, 512 KiB allocated in the checkout, and its LFS object at
`.git/lfs/objects/af/17/af17...` occupies 264,314,880 bytes. The
repository is already 32 GB with a 15 GB `.git`.

`scripts/create-vmdk-flat-testdata.sh:100-110` already works around
this: it creates a 3 GiB `twoGbMaxExtentFlat` with qemu-img and then
deletes both extents, keeping the descriptor alone
(`vmdk-twogb-flat.vmdk`, and the manifest says so). The small
`vmdk-multi-flat` beside it is hand-built -- three 1024-sector extents
filled 0xAA/0xBB/0xCC -- so neither is qemu-written and neither has a
partial last extent at qemu's geometry.

### The pin's structural oracle is sparse-only, by design

`tests/helpers/qemu_pins.py:711` captures `structure` only when a
descriptor declares a SPARSE extent, with the reason in the comment:
a sparse extent has a header whose fields decide how it is walked and
nothing in the descriptor says what they are. So every
`vmdk-multi-flat-*` pin case records descriptor text, extent names and
extent lengths with `structure: null`. That is not a gap to fill; it is
noted here so a later phase does not read the nulls as a defect.

### Two shipping `info` divergences, invisible today

Found while checking what instar does with the existing fixtures.
Measured with qemu-img 10.2.0 from the static builds and the instar
binary at `src/target/release/instar` **dated 2026-10-19**, which
predates several merges -- step 2b re-measures with a fresh build
before the issue is filed.

On `custom/format-coverage/vmdk-multi-flat.vmdk` (three 512 KiB
fully-written flat extents):

| Field | qemu-img 10.2.0 | instar |
|-------|-----------------|--------|
| top-level `actual-size` | 1576960 | 4096 |
| each child's `actual-size` | 524288, 524288, 524288, 4096 | same |
| `children[]` order | f003, f002, f001, descriptor | f001, f002, f003, descriptor |

qemu reports the sum over the descriptor and every extent; instar
reports the descriptor's allocation alone. The same holds for
monolithicFlat: on `vmdk-flat-1m.vmdk` qemu says `disk size: 1 MiB` and
instar says `disk size: 4 KiB`. In the human format instar additionally
prints the descriptor's 4 KiB for every extent, where qemu prints each
extent's own.

Both are invisible because every multi-file VMDK fixture carries
`skip_qemu_img`, and `generate-baselines.py:1043` never generates for
those -- 31 of the 124 safe images are flagged. And they would stay
invisible after the flag came off, because
`tests/helpers/comparators.py:139-168` substitutes a live
`get_disk_size()` into the baseline per dict that carries a `filename`:
the top-level dict's filename is the descriptor, so qemu's 1576960
would be rewritten to 4096 and instar's wrong answer would match. The
masking is single-file by construction, which is exactly the shape of
the defect it hides.

### What was right

The rest of the intent holds. `instar-testdata` does take direct
commits to `main` and is git-LFS backed. `generate-baselines.py` has
`--image` and `--case` exact-match filters that refuse to match nothing
(added by `PLAN-bat-population-phase-10-matrix.md`), `--no-commit`
exists and the script auto-commits without it, and
`scripts/validate-profiles.py` exists behind `make validate-profiles`.
The derived profile directories have twice been silently wrong while
the raw baselines were fine.

## Measurements this phase starts from

Taken 2026-11-18 against `ca4bfb52` with the static
`qemu-img-binaries/x86_64/10.2.0/qemu-img`, in the session scratchpad.

**Create geometry, 5 GiB sparse.** Three extents of 4194304, 4194304
and 2097152 sectors; files 327,680, 327,680 and 196,608 bytes apparent;
12,288 bytes allocated each on ext4, with a 417-byte descriptor.
`qemu-img info` reports `disk size: 40 KiB`, the sum of the four files'
allocations. This matches pinned case `vmdk-multi-sparse-5G` exactly.

**Data across an extent boundary is reachable with qemu-img alone.**
The static builds ship `qemu-img` and no `qemu-io`, so data cannot be
written into a sparse extent after the fact. Converting from a holey raw
source does it in one step: a 5 GiB raw file with 128 KiB of 0xAB
written at 2 GiB − 64 KiB, converted with
`qemu-img convert -f raw -O vmdk -o subformat=twoGbMaxExtentSparse`,
gives the identical three-extent descriptor and grows both of the first
two extents -- 393,216 bytes apparent and 81,920 / 77,824 allocated --
so the pattern genuinely straddles the boundary. `qemu-img info` reports
`disk size: 172 KiB`. Note that convert writes a 512-byte descriptor
where create writes 417.

**`children[]` appears at 8.0.0 and its order never moves.** Swept over
all 80 static builds with `info --output json` on
`custom/format-coverage/vmdk-multi-flat.vmdk`: 6.0.0 to 7.2.22 (30
builds) emit no `children` key at all, and 8.0.0 to 10.2.0 (50 builds)
emit `extents.2, extents.1, extents.0, file` -- the extents reversed,
the descriptor last. Spot-checked on monolithicFlat `vmdk-flat-1m`,
which behaves the same way: absent at 6.0.0 and 7.2.0, `extents.0,
file` at 8.0.0 and 10.2.0. So the reversal is parity rather than an
accident of one build, and instar's own `v.major >= 8` threshold for
`include_child_node` already matches the boundary exactly.

**Allocation is not reproducible through LFS, and must not be fixed.**
qemu's sparse extents are hole-bearing (327,680 apparent against 12,288
allocated), so a fresh clone reports a different `disk size` than the
machine that generated the fixture. `resparsify_images()`
(`scripts/generate-baselines.py:925`) repairs that, but only under
`downloaded/`. Extending it to `custom/` would be wrong: it runs
`cp --sparse=always`, which punches holes wherever it finds zeroes, so
it would re-sparsify the deliberately dense hand-built fixtures --
`vmdk-flat-1m-flat.vmdk` is a megabyte of zeroes -- and churn their
baselines. Nothing needs to change, because `substitute_actual_size`
recomputes per file at test time. This paragraph exists so that nobody
later reads the mismatch as a bug in `resparsify_images`.

## Decisions

1. **Split-sparse fixtures are stored; split-flat fixtures are never
   stored.** The cost asymmetry above is three orders of magnitude, and
   git-LFS objects on `main` are effectively permanent. The existing
   generator's delete-the-extents trick is the precedent, and this
   phase makes the rule explicit rather than leaving the next person to
   rediscover it.

2. **Four fixtures**, all `twoGbMaxExtentSparse`, in
   `custom/format-coverage/`:

   | id | Size | Extents | Tail | Apparent cost | Why |
   |----|------|---------|------|---------------|-----|
   | `vmdk-split-sparse-2049m` | 2049M | 2 | 2048 sectors (1 MiB) | 393,216 B | The smallest non-multiple: a tail extent of one grain-and-a-bit |
   | `vmdk-split-sparse-5g` | 5G | 3 | 2097152 sectors (1 GiB) | 851,968 B | The reference geometry, identical to pinned `vmdk-multi-sparse-5G` |
   | `vmdk-split-sparse-5g-boundary` | 5G | 3 | 2097152 sectors | 983,040 B | 128 KiB of 0xAB straddling the 2 GiB boundary, so a rotation bug shows as wrong bytes rather than a wrong size |
   | `vmdk-split-sparse-33g` | 33G | 17 | 2097152 sectors (1 GiB) | 5,439,488 B | Past the 15-extent ceiling at qemu's own 2 GiB geometry, with a partial tail |

   About 7.5 MB in all, which LFS stores in full. The 33 GiB one is the
   phase 3 fixture; `osboxes-vmdk-split-sparse` is kept as the
   real-world foil, not replaced.

3. **The generator is pinned to the static 10.2.0 build**, overridable
   with `QEMU_IMG`, and fails loudly if the binary is absent -- unlike
   the existing VMDK generators, which call whatever `qemu-img` is on
   the host. The honest limit of that pin: the descriptor's `CID` is
   random, so a regeneration is never byte-identical to what is
   committed whatever build runs it. Pinning removes the host from the
   inputs for everything else (`ddb.toolsVersion` appears from 6.2.0,
   per the phase 1 pin), and the fixtures are generated once and
   committed, not regenerated casually.

4. **The new entries do not set `skip_qemu_img`**, so baselines
   generate, and each carries `instar_unsupported` with the refusal
   measured in step 2b. `test_info_safe.py:124` turns that key into an
   assertion that instar *fails*, so the suite stays green today and
   phase 3 removes the key as part of lifting the ceiling. That is the
   handoff: four fixtures whose manifest entries phase 3 must change to
   claim success.

5. **Split-flat coverage is a test-time generator, not a fixture.** A
   helper in `tests/helpers/` builds a split-flat image into a caller's
   temporary directory with the pinned qemu-img, for phases 3, 11 and 16
   to use. A 33 GiB split-flat image costs 17 files of 2 GiB apparent
   and about 4 KiB each allocated, which is free on a sparse filesystem
   and unstorable in LFS.

6. **Both `info` divergences are fixed here.** The plan as first
   written filed them and left them to phase 3, on the grounds that the
   fix changes `info` output for every monolithicFlat image instar has
   ever read and cannot be proved without also changing the comparator.
   The operator overrode that in the back brief on 2026-11-18: fix them
   in this phase. So the phase gains three changes it did not have.

   The VMM reports the top-level allocation as the sum over the
   descriptor and every resolved extent, and the human path stats each
   extent instead of reusing the input file's block count. Both halves
   were re-measured on a binary built 2026-11-18 and both still
   reproduce: on `vmdk-multi-flat` qemu reports 1576960 against
   instar's 4096, and on monolithicFlat `vmdk-flat-1m` 1052672 against
   4096. The JSON
   children path already stats each extent correctly
   (`src/vmm/src/main.rs:2768`), so the defect is the top-level figure
   and the human rendering, not the per-child JSON.

   `children[]` is emitted in qemu's order, and only where qemu emits
   the array at all. This was measured across all 80 builds before
   anything copied it, because a reversal that moved between versions
   would be a divergence to document rather than match. It does not
   move: no build from 6.0.0 to 7.2.22 emits `children` for a
   multi-file VMDK, every build from 8.0.0 to 10.2.0 emits it with the
   extents reversed and the descriptor last, and that holds for
   monolithicFlat as well as the split subformat. instar's
   `include_child_node` is already `v.major >= 8`
   (`src/vmm/src/version.rs:123`), so it has the boundary right and
   only the order wrong.

   What it does not have right is the override beside it:
   `if profile.include_child_node || vmdk_flat.is_some()`
   (`src/vmm/src/main.rs:2763`, added 2026-05-15 in `c284872c` to emit
   per-extent children for monolithicFlat) bypasses the version gate,
   so `instar info --qemu-version 7.2` emits a `children` array where
   every qemu build before 8.0.0 emits none. That is the third
   divergence, found while checking the second, and it is fixed in the
   same step.

   `tests/helpers/comparators.py` sums the siblings for the top-level
   figure of a multi-file image, so the oracle stops substituting the
   descriptor's own allocation over qemu's sum. This is what makes the
   first fix provable, and it is why the fix and the helper change land
   together: either alone turns a green suite red.

   All three are filed as
   [#922](https://github.com/shakenfist/instar/issues/922), which the
   management session wrote from step 2b's measurements and the 80-build
   sweep, so the record of the defect exists independently of the commit
   that fixes it.

   What makes this affordable is something the survey found late.
   `vmdk-flat-1m` and `vmdk-flat-10m` do **not** carry
   `skip_qemu_img` -- they run in CI and already have `info` baselines
   -- so the monolithicFlat half of the fix is provable against
   baselines that already exist, with no generation step in between.
   That is also the answer to how a shipping divergence on a CI fixture
   went unnoticed: the masking, not a missing fixture.

7. **`vmdk-multi-flat` loses `skip_qemu_img`; three other VMDK
   fixtures keep it.** It is the one complete multi-extent flat image
   in the corpus -- three 512 KiB extents, all present -- so it is the
   only fixture that can compare a multi-extent flat `info` against
   qemu's, which is half of what decision 6 fixes. The three that keep
   the flag keep it for stated reasons: `vmdk-twogb-flat` and
   `vmdk-multi-extent` name extents that deliberately do not exist, and
   `vmdk-flat-parent` with `vmdk-flat-with-parent` form a
   `parentFileNameHint` chain that is not multi-extent. Each reason is
   recorded in the manifest entry's description rather than left to be
   rediscovered.

8. **Baselines for all four image-driven commands across all 80
   builds** (`info`, `check`, `measure`, `map`), generated with
   `--no-commit` and `--image`, with `make validate-profiles` run
   afterwards and a profile spot-checked by hand. The testdata half
   lands as a direct commit to `main`, which the back brief gates.

## Step plan

Steps 2a and 2b are independent and may run in parallel: 2a writes only
into the testdata checkout, 2b only builds in the instar worktree and
reads. 2c consumes 2b's measured refusal text and 2a's fixtures, so it
follows both. 2d and 2e may then run together, because 2d changes only
the instar worktree and 2e only the testdata clone; 2d must not be
reviewed as passing before 2e has produced `vmdk-multi-flat`'s
baselines, since that is what makes its multi-extent half provable.
2f and 2g follow 2d, because both run `pre-commit` in the same
worktree.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Write `scripts/create-vmdk-split-testdata.sh` in the instar worktree and generate the four fixtures of decision 2 into `../instar-testdata/custom/format-coverage/`. Read `scripts/create-vmdk-flat-testdata.sh` first and follow its shape: `set -euo pipefail`, an output directory as `$1` defaulting to `../instar-testdata/custom/format-coverage`, `rm -f` of every file it is about to write (including the extents, whose names it must derive, not glob), and an `ls -la` of what it made at the end. Use `QEMU_IMG` from the environment, defaulting to `$INSTAR_TESTDATA_PATH/qemu-img-binaries/x86_64/10.2.0/qemu-img`, and exit non-zero with a message naming the path if it is not executable -- do not fall back to the host's `qemu-img`. Three fixtures are `qemu-img create -f vmdk -o subformat=twoGbMaxExtentSparse <out> <size>` at 2049M, 5G and 33G. The fourth, `vmdk-split-sparse-5g-boundary`, is made by writing a 5 GiB raw file with 128 KiB of 0xAB at offset 2147418112 (2 GiB − 64 KiB) via a short `python3 -c` heredoc exactly as the existing script seeds its flat extent, then `qemu-img convert -f raw -O vmdk -o subformat=twoGbMaxExtentSparse`, then deleting the raw source. Verify before finishing, and report the numbers: each descriptor's extent count and the declared sector count of its last extent; that every named extent file exists; and for the boundary fixture that `qemu-img map --output=json` reports a data range covering 2147418112 through 2147549184. Do NOT commit anything in either repository, and do not run `git add` in the testdata clone. Python inside the script follows this repo's conventions: single quotes, 120 columns. |
| 2b | medium | sonnet | none | Re-measure four things with a freshly built binary and report them verbatim, changing no files. Build with `make instar` in the instar worktree (it builds in Docker; see the `build-and-test` skill) and say what the binary's mtime and `git describe`/HEAD are, because the measurements in the phase plan were taken with a binary dated 2026-10-19 that predates several merges. (1) The exact refusal `instar info` gives for a multi-extent SPARSE descriptor, run against `downloaded/osboxes/android-x86-5.1-rc1-split-sparse/Android-x86 5.1 RC1.vmdk` in the testdata checkout -- quote the whole line. (2) `instar info` and `instar info --output json` against `custom/format-coverage/vmdk-multi-flat.vmdk` and `custom/format-coverage/vmdk-flat-1m.vmdk`, compared field by field with `qemu-img-binaries/x86_64/10.2.0/qemu-img` on the same files: report the top-level `actual-size`/`disk size`, every child's, and the order of `children[]`. Say for each whether the divergence in the phase plan's table still reproduces. (3) Whether `instar info` resolves `custom/format-coverage/vmdk-twogb-flat.vmdk`, whose extents do not exist, and what it says. (4) Whether the `children[]` order is stable across builds: run `qemu-img info --output json` on `vmdk-multi-flat.vmdk` with the 6.0.0, 7.2.0, 8.2.0 and 10.2.0 static binaries and report the order each one gives. Step 2d copies that order, so a build where it differs is a divergence to document rather than match, and this is the measurement that decides which. Report only; file no issue and fix nothing. |
| 2c | low | sonnet | none | Add four image entries to `tests/manifest.json` in the instar worktree, for the fixtures step 2a generated. Copy the field set of the existing `vmdk-multi-flat` entry (around line 483) -- `id`, `path`, `format`, `safety: safe`, `run_in_ci: true`, `description`, `tags`, `generated_by` -- with two differences. Do **not** set `skip_qemu_img`: `generate-baselines.py:1043` never generates baselines for an image that has it, which is why the existing multi-extent entries have none. And set `instar_unsupported` to the refusal step 2b measured, worded like the `osboxes-vmdk-split-sparse` entry's (around line 2380), because `tests/test_info_safe.py:124` reads that key as an assertion that instar fails. Tags: `vmdk`, `twoGbMaxExtentSparse`, `multi-extent`, `format-coverage`, plus `extent-boundary` on the boundary fixture and `past-device-ceiling` on the 33G one. `generated_by` is `scripts/create-vmdk-split-testdata.sh`. Each description states the virtual size, the extent count, the tail extent's sector count and, for the boundary fixture, where the pattern sits. Then remove `skip_qemu_img` from the `vmdk-multi-flat` entry, because it is the one complete multi-extent flat image in the corpus and step 2e needs it to have baselines; add to its description, in a sentence, that all three extents are present. Leave the flag on `vmdk-twogb-flat`, `vmdk-multi-extent`, `vmdk-flat-parent` and `vmdk-flat-with-parent`, and add to each of those four descriptions the reason it cannot have a qemu baseline -- the first two name extents that deliberately do not exist, the last two are a `parentFileNameHint` chain -- so the next reader does not have to rediscover it. Then confirm `python3 -c "import json; json.load(open('tests/manifest.json'))"` succeeds and report the manifest's new image count and which entries you changed. |
| 2d | high | opus | none | Fix both `info` divergences of decision 6 in the instar worktree, and the test helper that hides one of them. Three changes, and they land together because either alone turns a green suite red. (1) In `src/vmm/src/main.rs`, the top-level allocation figure for a VMDK whose descriptor resolves to extents must be the sum over the descriptor and every extent, as qemu-img reports it; today it is the input file's own `st_blocks` alone (`input_disk_blocks` around line 12287). (2) In the same file's human-format `info` rendering, each extent's line must carry that extent's own allocation, not the input file's; the JSON children path around line 2768 already stats each extent and is the pattern to follow rather than a second implementation. (3) Emit `children[]` with the extents reversed and the descriptor last, which is the order every qemu build from 8.0.0 to 10.2.0 uses; this was swept over all 80 builds and does not move, so it is parity rather than a guess, and `format-specific.data.extents[*]` stays in descriptor order because qemu keeps it that way. (4) Gate the array on the version profile. `if profile.include_child_node || vmdk_flat.is_some()` (around line 2763) bypasses the gate, so `instar info --qemu-version 7.2` emits a `children` array where no qemu build before 8.0.0 emits one. `include_child_node` is already `v.major >= 8` in `src/vmm/src/version.rs:123` and that boundary is correct; the override is the bug. It was added in `c284872c` to emit per-extent children for monolithicFlat, so check with `git show c284872c` whether anything depended on it being unconditional before you remove it, and say what you found. (5) In `tests/helpers/comparators.py`, `_substitute_json_actual_size` resolves a dict's own `filename` to that file's allocation (lines 139-168), which for the top-level dict is the descriptor, so qemu's sum is substituted away and instar's wrong answer matches. Make the top-level figure of a multi-file image the sum of the files the output names, and do the same for the human-format path. Verify against baselines that already exist: `vmdk-flat-1m` and `vmdk-flat-10m` are not `skip_qemu_img` and have `info` baselines under `expected-outputs/qemu-img-json/raw/10.2.0/`, so the monolithicFlat half needs no generation step -- run those tests and report the counts. Then mutation-check all five changes: break each one in turn in a COPY of the tree, confirm a test fails and names the right thing, run each mutant under `timeout` and count a timeout as killed, restore from a copy and never with `git checkout <dir>`, clear `__pycache__` after restoring, and keep the mutations in a runnable script whose path you report. Read a skip as a failure, not a kill. Also run `make test` -- this changes VMM output and there are unit tests in that file -- and `pre-commit run --all-files`. Report which files changed, the test counts, the mutation results, and anything you did not do. |
| 2e | high | opus | none | Generate and validate baselines for the four new fixtures, and for `vmdk-multi-flat`, in the testdata clone at `../instar-testdata` (branch `main`; do not create a worktree there). `vmdk-multi-flat` is in the list because step 2c removed its `skip_qemu_img`: it is the one complete multi-extent flat image in the corpus and it has no baselines at all today, which is why a shipping `info` divergence on it went unnoticed. This step is the one with a history of silent wrongness, so it reports numbers, not assurances. For each of the five image ids and each of `info`, `check`, `measure`, `map`, run `scripts/generate-baselines.py --command <cmd> --image <id> ... --no-commit`. **`--no-commit` is not optional**: without it the script commits by itself (see its `main()` around line 4104). Both `--image` and `--case` refuse to match nothing, so a typo fails loudly rather than generating zero files. Expect every one of the 80 version directories under `qemu-img-binaries/x86_64/` to be covered; say so with a count per command, and name any version that produced nothing. Existing coverage is uneven -- `vmdk-flat-1m` has `info` baselines for 10.2.0 only, and one raw version directory holds 78 ids against 124 safe images -- so do not take a partially populated tree as a sign the run failed. Note that `info` baselines land under the legacy `expected-outputs/qemu-img-human` and `qemu-img-json` directories, not `info-*`. Then run `make validate-profiles` (it runs `scripts/validate-profiles.py`) and report its exit status and output in full, and open one derived profile for one new fixture by hand and confirm it matches the raw baseline it was derived from -- the derived directories have twice been silently wrong while the raw ones were fine. Report the wall time, the total file count added, and `git status --short | wc -l` in the testdata clone. Do NOT commit and do NOT push. |
| 2f | medium | sonnet | none | Add a test-time generator for split-flat images to the instar worktree, with unit tests. It belongs beside the existing helpers in `tests/helpers/`; read `tests/helpers/qemu_pins.py` for how that package already locates a static qemu-img build and runs it, and reuse rather than duplicate. The function takes a directory and a size, builds `qemu-img create -f vmdk -o subformat=twoGbMaxExtentFlat` there, and returns the descriptor path and the extent paths in descriptor order. It exists because a split-flat image cannot be stored: its extents are full-length sparse files -- 2 GiB each -- and git-LFS stores holes as bytes, so a 33 GiB fixture would be a 33 GiB LFS object. Say that in the module or function docstring, with the numbers. Add unit tests in `tests/test_helpers.py` (or a new file if that one is near 1500 lines -- check, `PUSH-AUDIT.md`'s source-size rule bites at 800 and 1500) asserting a 5 GiB image gives three extents whose declared sector counts are 4194304, 4194304 and 2097152 and whose file lengths are 2147483648, 2147483648 and 1073741824, that the directory is cleaned up, and that a missing qemu-img raises rather than silently skipping. Run the new tests with the venv at `tests/.venv` and `INSTAR_TESTDATA_PATH` set, under stestr, and report the "Ran: N" line. Python conventions: single quotes except docstrings, 120 columns, no trailing whitespace. |
| 2g | medium | sonnet | none | Record what this phase found and fixed, in the instar worktree. The management session has already filed the issue for the two `info` divergences and will give you its number; you do not file it. (1) Add that issue to the master plan's *Bugs fixed during this work*, with one sentence on each divergence, and a *Future work* entry only for anything the phase found and did not fix. (2) Update `docs/testing.md` where it describes recording split-subformat behaviour (phase 1 added that paragraph): the LFS cost rule of decision 1 with the measured numbers, the `skip_qemu_img`-blocks-baselines mechanism and which VMDK fixtures still carry it and why, and that the top-level allocation figure of a multi-file image is a sum, so a test helper comparing it must sum the siblings too. (3) Add a `CHANGELOG.md` entry for the `info` fix if that file takes one for a user-visible output change -- read how the last few entries are worded before deciding, and say in your report which way you went and why. Cite no plan, phase or step number anywhere outside `docs/plans/`. Report the sections you touched. |

## Risks and mitigations

* **An LFS object pushed to `main` is permanent.** 7.5 MB is small, but
  a wrong fixture cannot be cheaply unpublished. *Mitigation:* the back
  brief gates the fixture set and the push separately, and the
  management session checks each file's apparent size against the table
  in decision 2 before anything is added.
* **The baseline generator auto-commits.** Without `--no-commit` it
  commits to `main` by itself. *Mitigation:* stated in step 2e's brief
  as not optional; the management session checks `git log` in the
  testdata clone before reviewing the diff.
* **The derived profiles have twice been silently wrong while the raw
  baselines were fine.** *Mitigation:* step 2e runs
  `make validate-profiles` and additionally opens one profile by hand;
  the management session re-runs the validator itself.
* **A fixture that quietly joins the #845 cohort.** 31 of 124 safe
  images carry `skip_qemu_img` and generate nothing.
  *Mitigation:* decision 4 forbids the flag, and the definition of done
  counts the files that appear.
* **The 2 GiB-extent assumption.** The one real-world split-sparse
  image in the corpus has 3.96 GiB extents under a
  `twoGbMaxExtentSparse` create type. *Mitigation:* recorded in the
  survey above and carried into the master plan, so phase 3 reads
  declared sector counts rather than inferring from the create type.
* **An `info` output change has a wide blast radius.** Decision 6's fix
  changes the allocation figure instar prints for every multi-file VMDK
  it has ever read, which is monolithicFlat as well as the split
  subformats. *Mitigation:* step 2d runs `make test` as well as the
  python suite, because the VMM's own unit tests live in the file it
  edits; it mutation-checks each of the four changes rather than
  reading the guards; and the management session re-runs the
  monolithicFlat comparison against the baselines that already exist,
  which is the one case provable without any generation step.
* **Copying a divergence that is not stable.** Matching qemu's
  `children[]` order is only parity if qemu's order is the same across
  builds. *Answered before step 2d was briefed:* swept over all 80
  builds, the array is absent below 8.0.0 and identically reversed on
  all 50 builds from 8.0.0 up. The risk was real and is now closed, so
  step 2d matches the order rather than documenting a divergence.
* **The comparator change and the VMM change must land together.**
  Either alone turns a green suite red: the helper would reject
  instar's current output, and the VMM fix would be substituted away.
  *Mitigation:* both are one step and one commit, and the step's brief
  says so.
* **Measuring instar with a stale binary.** The divergence table was
  taken with a binary dated 2026-10-19. *Mitigation:* step 2b rebuilds
  and re-measures before anything is filed, and steps 2d and 2g use 2b's
  numbers rather than the plan's.

## Definition of done

Falsifiable; each is checkable by someone who did not do the work.

1. `custom/format-coverage/vmdk-split-sparse-33g.vmdk` has 17
   `RW ... SPARSE` extent lines, the seventeenth declaring 2097152
   sectors, and every file it names exists.
2. `vmdk-split-sparse-2049m.vmdk` has exactly two extent lines, the
   second declaring 2048 sectors.
3. `qemu-img map --output=json` on `vmdk-split-sparse-5g-boundary.vmdk`
   reports a data range covering offsets 2147418112 to 2147549184, so
   the pattern straddles the first extent boundary.
4. None of the four new manifest entries sets `skip_qemu_img`, and all
   four set `instar_unsupported`.
5. `find expected-outputs -name 'vmdk-split-sparse-*' | wc -l` in the
   testdata clone equals the count step 2e reports, and every one of
   the 80 version directories appears for `info`, `check`, `measure`
   and `map`.
6. `measure-json` contains at least one file for each of the four ids,
   so none of them joins the 81 fixtures of #845.
7. `make validate-profiles` exits 0.
8. The instar python suite runs with "Ran: N" matching the selection
   and 0 failures, with `test_info_safe` asserting the refusal for all
   four new fixtures. The count is checked, not the exit code.
9. The split-flat test-time generator builds a 5 GiB image whose three
   extents have file lengths 2147483648, 2147483648 and 1073741824, and
   a unit test asserts it.
10. `pre-commit run --all-files` passes in the instar worktree, and
    `make test` passes, because this phase now changes the VMM.
11. `instar info` and `qemu-img info` agree on the top-level
    allocation figure for `custom/format-coverage/vmdk-flat-1m.vmdk`
    and `custom/format-coverage/vmdk-multi-flat.vmdk`, in both the
    human and the JSON output, where before the fix instar reported
    the descriptor's 4 KiB against qemu's sum.
12. `instar info --output json` lists `children[]` for
    `vmdk-multi-flat.vmdk` in the order step 2b measured qemu using,
    or the report says which builds disagreed and instar's order was
    left alone.
13. `instar info --qemu-version 7.2 --output json` on
    `vmdk-multi-flat.vmdk` emits no `children` key, matching every
    qemu build below 8.0.0, where before the fix it emitted one.
14. Breaking each of the five changes of step 2d in turn makes a test
    fail, and the mutation script that does it is reported with a
    killed count and no skips read as kills.
15. The issue for the three `info` divergences exists, cites step 2b's
    numbers, and is linked from the master plan.
16. No plan, phase or step reference appears in anything landed outside
    `docs/plans/` -- including the issue body.
17. The master plan's phase 2 intent and the `docs/plans/index.md` row
    say what the survey found, not what the intent said before it.

## Back brief

Before implementation begins, the implementing session restates this
phase's scope and the four gates below, and the operator confirms.

1. **The fixture set.** Four split-sparse fixtures at 2049M, 5G, 5G
   with boundary data and 33G, about 7.5 MB in LFS, named and sized as
   decision 2 lists them. Cheap to change now, permanent once pushed.
2. **No stored split-flat fixture**, decision 1, with split-flat
   coverage provided by a test-time generator instead.
3. **The direct push to `instar-testdata` `main`.** That repository
   takes direct commits and no merge requests, so the fixtures and
   baselines reach `main` without review. The operator confirms before
   the push, and the phase's `Merged` record must carry both
   repositories -- the instar merge commit and the testdata
   `first..last` range -- which
   `PLAN-bat-population-phase-13-push-audit.md` found missing once.
4. **Decision 6**, which asked whether to leave both `info`
   divergences to phase 3 or fix them here.

## Execution notes

**Back brief, 2026-11-18 (AEDT).** All four gates were put to the
operator in one question. Three were confirmed as planned: the fixture
set of decision 2, no stored split-flat fixture, and a direct push to
`instar-testdata` `main` after the management session has reviewed the
fixtures and baselines. The fourth was **overridden**: decision 6 had
filed the two `info` divergences and left them to phase 3, and the
answer was to fix them in this phase instead.

The plan was amended at source before any step ran, rather than carried
as a departure note. What changed: `src/` is no longer out of scope, and
the definition of done no longer requires an empty `src/` diff;
decision 6 is rewritten to describe the fix; decision 7 is new and
records which VMDK fixtures lose `skip_qemu_img` and which keep it, with
a reason each; a new step 2d makes the three code changes and
mutation-checks them, which renumbered the old 2d, 2e and 2f to 2e, 2f
and 2g; step 2b gained a fourth measurement, the `children[]` order
across four builds, because matching an order that moves between builds
would not be parity; step 2c un-flags `vmdk-multi-flat`; step 2e covers
it; step 2g no longer files the issue, which the management session
files from 2b's measurements, and gained a CHANGELOG question.

The survey finding that makes the override cheap was already in the
plan but not weighed there: `vmdk-flat-1m` and `vmdk-flat-10m` are not
`skip_qemu_img` and already have `info` baselines, so the monolithicFlat
half of the fix is provable with no generation step between the change
and the evidence.

The pull request may be opened by this session.

**Steps 2a and 2b, 2026-11-18 (AEDT).** Both ran in parallel as the
plan allows and both reported without needing a second pass. Step 2a's
numbers all reproduced against the tree, and the management session
went further than the row asked by converting the boundary fixture back
to raw: 131072 bytes of 0xAB from 2 GiB minus 64 KiB with zeroes either
side, in two 64 KiB map ranges that land in different extents. Step 2a
also measured something the plan did not anticipate -- `qemu-img
convert` pads its descriptor to a 512-byte sector with NULs where
`create` writes exactly the bytes it needs -- which phase 11 will want,
since convert is its target.

**Step 2b's ordering conclusion was overturned in review.** 2b measured
`children[]` on 6.0.0, 7.2.0, 8.2.0 and 10.2.0, found the array absent
on the first two, and concluded by this plan's own rule that the order
was "not stable across the four builds" and should therefore be
documented rather than matched. That reads the rule more literally than
it was meant: an array that is absent below a version and identical
above it has not moved, and instar already models exactly that split
with its `v.major >= 8` profile flag. The management session swept all
80 builds rather than four, found the boundary at 8.0.0 and the
reversal identical on all 50 builds above it, and briefed step 2d to
match the order. The sweep also turned up the third divergence, the
`vmdk_flat.is_some()` override that emits children under a pre-8
profile, which 2b's four-build sample could not have shown.

**Steps 2c to 2e, 2026-11-18 (AEDT).** Step 2c invented three of its
four reasons. The row asked it to add, to each fixture that keeps
`skip_qemu_img`, the reason it cannot have a qemu baseline -- and the
row asserted what those reasons were. Measured with the pinned 10.2.0
build, qemu-img opens `vmdk-multi-extent` (reporting 2 GiB without
opening its absent extents), `vmdk-flat-parent` and
`vmdk-flat-with-parent`; only `vmdk-twogb-flat` is refused. `git log
-S` puts each flag in the commit that created its fixture with no
explanation, so the real reason is unrecoverable and the three invented
sentences were removed rather than reworded. Decision 7 was written on
the same wrong premise as the row. This is recorded as future work.

Step 2e needed a step the brief omitted: `make profiles` after
generation, without which `make validate-profiles` exits 2 with a
`no-profile` problem for every new file. It also found that
`generate-baselines.py`'s `find_manifest()` auto-detection resolves to
the primary clone rather than the worktree whose manifest has the new
images, which would have generated nothing and exited 0; it passed
`--manifest` explicitly instead. Both are now in `docs/testing.md`.

The testdata push happened after step 2d rather than at the end of the
phase. Pushing baselines that then need regenerating is the worse
order, so the push waited until the instar side was proved to agree
with them.

**Step 2d ran out of weekly quota and the management session finished
it, 2026-11-18 (AEDT).** The sub-agent completed all five changes and
its mutation pass -- eight mutants, all killed by a named test -- and
then terminated on a rate limit before running the suite or the gate.
Its work was left uncommitted in the worktree and reviewed as written;
nothing was redone. Review reproduced every load-bearing claim rather
than reading the report: human and JSON `info` are byte-identical to
the pinned 6.0.0, 7.2.0, 8.0.0 and 10.2.0 builds on `vmdk-multi-flat`,
and the allocation sum was confirmed by stat (three extents of 524288
plus a 4096 descriptor against a reported 1576960).

Removing the `vmdk_flat.is_some()` override was the change most worth
checking, since it narrows output. `git show c284872c` states the
premise it was added on -- that qemu-img emitted children for
monolithicFlat "back to 6.0" -- which is exactly the claim the 80-build
sweep disproved, and no baseline below 8.0.0 carries a `children` array
at any profile. The override was the bug and `version.rs` was right.

`make test` reported five failures, all of them the known contention
cluster on this host: the four `test_convert` VHD cases exit 4, which
is `COMPARE_EXIT_READ_ERROR` rather than the exit code for a content
difference, and the `test_dd` case reports a guest info operation
timing out. The host was at a load average near 20 on 16 cores. All
five pass on replay at concurrency 2. That the convert helper reports a
read error as a content difference, and discards the stderr that would
say so, is recorded as future work.

**Steps 2f and 2g, 2026-11-18 (AEDT).** Both ran as planned and both
needed review corrections, none of them large enough to send back.

In 2f the generator looked its build up with `shutil.which`, which
searches PATH for a bare name and would quietly resolve to the host's
qemu-img -- the fallback every other part of this work refuses. It now
takes a path and checks it, with a test that a bare name raises. Its
tests resolved the testdata checkout from the environment alone and
failed outright without it, but `make test` sets no such variable and
every other test in the suite reaches its data through `base.py`'s
sibling-directory fallback, so the suite would have gone red for
anyone whose shell did not export it; they now resolve it the same way.
They also moved out of `tests/test_helpers.py`, whose tests touch
neither the binary nor testdata. The runner shared with the pin harness
was made public with its call sites renamed, rather than left behind an
alias.

In 2g the record said four VMDK fixtures carry `skip_qemu_img` where
fifteen do -- four of the *safe* ones, which was the claim meant. The
CHANGELOG entry attributed the `--qemu-version 7.2` array to the
descriptor ordering, where the two were separate faults. The read-side
ceiling appeared under both future work and bugs fixed. And the brief
had missed `docs/output-formats.md` entirely, whose child-node section
described a single `/file` child and so was incomplete for every
multi-file image; the management session wrote that part.

The master plan and the index row said "two" `info` divergences
throughout, which was true when the survey wrote it and stopped being
true when the sweep found the third. Both are corrected at source.
