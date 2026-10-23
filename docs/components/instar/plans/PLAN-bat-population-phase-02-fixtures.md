# Phase 2: qemu-written fixed VHDX fixtures

Phase 2 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation and the phase 2 paragraph of "Sequencing rationale" first;
this file assumes them, and corrects that paragraph where the survey
found it wrong.

Planning effort: medium. The phase follows an established fixture
pattern, but its survey overturned three of the master plan's
premises (fixture size, generator location, and the create baselines),
so the decisions below are worth reading before the steps.

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21); phase 1's plan
lists them. None is revisited here.

## Scope

In:

* Five small qemu-written VHDX fixtures, committed to instar-testdata
  through git-LFS and registered in `tests/manifest.json`: fixed at a
  block multiple and at a partial final block, each in both
  `block_state_zero` modes (the `off` pair carrying data), and the
  dynamic image at the #626 geometry.
* Four new pin cases, so every committed fixed fixture has a pinned
  qemu structure across all 80 builds.
* The generator, in instar's `scripts/create-vhd-testdata.sh`.
* A test module that proves each fixture is what qemu writes and
  pins what instar's readers do with it today, including the three
  failures #626 and #797 cause, in a form that fails when phase 4
  fixes them. The same module also generates, at test time, the two
  fixed images that are too large or too awkward to commit.
* `docs/testing.md`, for the fixtures and the known-failure table.

Out:

* Any change under `src/`. The read-side defects are phase 4's.
* `generate-baselines.py`'s `CREATE_CASES` and per-image
  cross-version baselines for the new fixtures. Both move to
  phase 10 (decision 5).
* A committed fixed VHDX at the #626 geometry (decision 2).
* Issues #807 (compare reports a read failure as a content mismatch)
  and the unmeasured convert leftover below. They are recorded, not
  fixed.

## What the survey found

Two sub-agents surveyed, one each for instar-testdata's fixture
pattern and for instar's reads of qemu's fixed VHDX. The management
session re-measured every claim below that a decision rests on; the
re-measurements are marked.

### The 128 GiB fixture is not cheap in bytes

The master plan's phase 2 paragraph calls the #626 region-crossing
fixture "sparse and so cheap in bytes". It is not, once it goes
through git-LFS:

* git-LFS does not preserve holes. A 1 GiB sparse fixed VHDX committed
  to a scratch LFS repository was stored fully allocated in
  `.git/lfs/objects` (1,082,134,528 bytes) and checked out fully
  allocated by the smudge filter. Nothing in the tree compresses LFS
  objects.
* CI resparsifies only `downloaded/`, never `custom/`
  (`tools/ci/resparsify-testdata.sh:24-27,112`;
  `docs/testing.md:1156-1162`).
* The manifest's `sha256` check reads the whole file
  (`tests/base.py:355-411`).

So a committed 131041 MiB fixed VHDX is a 128 GiB LFS object, upload
and checkout on every CI leg. The largest fixture today is 1 GiB
(`custom/external-data/qcow2-external-data-raw.raw`); the whole LFS
set is 324 objects, about 10.2 GB.

The fixed file is also unnecessary for #626. The miscount is in the
BAT entry count, which a **dynamic** image at the same geometry
reproduces in 8 MiB. Re-measured with the instar binary built from
`da9f043f`: `qemu-img create -f vhdx -o block_size=1M d.vhdx 131041M`
gives an 8,388,608-byte file on which `instar check` exits 2 and
`instar map` exits 1 ("source format unrecognised"), while the same
command at 131040M checks clean. Phase 1 already pins that dynamic
case (`vhdx-dynamic-131041M-block-1M`).

### The house generator lives in instar, not instar-testdata

The master plan says the generator goes in instar-testdata "following
the house pattern from `PLAN-differencing` phase 2". That phase's
pattern was the reverse: the generator is instar's
`scripts/create-vhd-testdata.sh` and the binaries are committed to
instar-testdata (`PLAN-differencing-phase-02-fixtures.md:68-84`). The
script already owns every VHDX fixture, including the differencing
ones. Because qemu-written VHDX is not byte-reproducible (a fresh
DataWriteGuid each run), its VHDX outputs are opt-in under
`REGEN_VHDX=1` and skipped when present
(`scripts/create-vhd-testdata.sh:109-127`). Generators inside
instar-testdata exist too (`generate-{vdi,parallels,dmg,qcow1}-fixtures.py`),
but not for VHDX.

### Adding the create baselines now would break develop for no signal

Adding vhdx fixed cases to instar-testdata's
`scripts/generate-baselines.py` `CREATE_CASES` (`:404`; the vhdx
target has five dynamic cases at `:454-461`) has two problems:

* `test_create_cases_match_baselines` (`tests/test_create.py:1793-1826`,
  read by the management session) fails whenever the host's profile
  holds baselines that instar's mirror at `:1618` lacks. Testdata
  lands on `main` before instar's pull request, so develop would be
  red in between. And each mirrored case runs `instar create` and
  asserts exit 0 (`:1868-1872`), which fixed VHDX cannot pass until
  phase 7, so every case would need a `KNOWN_WRITER_DIVERGENCES` entry
  on arrival.
* The baselines are `qemu-img info --output=json`, which for vhdx has
  no format-specific block, and `tests/helpers/info_json.py:23-35`
  strips `actual-size` and the nested `virtual-size`. They compare
  virtual size and cluster size only, and cannot tell fixed from
  dynamic or one `block_state_zero` mode from the other.

The structural comparator and the pin are the oracle for fixed
output; the create-info baselines are a cross-version smoke check. They
belong with the matrix in phase 10, where instar can already create
the images they describe.

### What instar reads today

Measured by sub-agent on qemu-img 10.0.13 fixtures, with the instar
binary built from `da9f043f` (the read paths for vhdx have not changed
since). `qemu-img check` passes every image. The rows marked † were
re-measured by the management session.

| Image | info | check | map | compare | convert -O raw |
|---|---|---|---|---|---|
| fixed 64M, `block_state_zero` on and off, empty and with data | ok | ok | ok | ok | identical to qemu's |
| fixed 65M (8 MiB blocks, last block 1 MiB), on | ok | ok | ok | ok | identical |
| fixed 65M, off, empty and with data † | ok | exit 2: `VHDX block offset out of bounds` (#797) | ok | ok | identical |
| fixed 131041M, 1 MiB blocks, on and off † | ok | exit 2: `VHDX BAT region too small` (#626) | exit 1 (#626) | exit 1 (#626, misreported as a content mismatch: #807) | exit 1 (#626) |
| dynamic 64M | ok | ok | ok | ok | identical |

The #626 count is written out three times: `VhdxState::init`
(`src/crates/vhdx/src/lib.rs:2331-2345`), `check_vhdx`
(`src/operations/check/src/main.rs:1677-1688`) and
`calculate_bat_layout` (`src/crates/vhdx/src/lib.rs:3773`). The #797
test is `src/operations/check/src/main.rs:1783-1791`. `info` passes on
the #626 geometry because it does not walk the BAT.

Also measured, and what each means for this phase:

* **qemu does not keep a `block_state_zero=on` fixed image fixed when
  it writes to it.** Writing 64 KiB into a 64M `on` image grew the
  file from 75,497,472 to 83,886,080 bytes (re-measured †): qemu
  appends a new block at end of file instead of using the
  preallocated slot. Writing into an `off` image changes nothing
  structural (re-measured: `structural_diff` is empty). So the
  data-bearing fixtures are `off` images, and the `on` fixtures stay
  empty.
* **qemu's default convert to fixed does the same.** The pinned
  `vhdx-convert-fixed` case is a 100,663,296-byte file against
  75,497,472 for `vhdx-convert-fixed-bsz-off`: the data blocks are
  appended past the preallocated area. That mixed layout is the most
  interesting read case in the phase, and the cheapest to produce at
  test time from `qemu_pins.write_convert_source`, so it is generated
  rather than committed.
* **`instar measure` agrees with qemu-img** on fixed 16M and 17M
  sources for `-O qcow2` and `-O raw` (re-measured †).
* **`instar info` cannot tell fixed from dynamic**, and neither can
  `qemu-img info`: the only differences are file length and the
  child node's sizes.
* `instar create -f vhdx -o subformat=fixed` refuses with
  `create: -O vhdx -o subformat=fixed is not yet supported (only the
  dynamic vhdx subformat is implemented)`. The refusal now sits at
  `src/vmm/src/main.rs:15435-15441`, not the `:15166-15181` the
  master plan cited. Every `src/vmm/src/main.rs` citation in the master
  plan's Situation had drifted by about 270 lines since it was
  written; all seven were re-located by reading the code and corrected
  there (the `-o` parser's `block_state_zero` accept-ignore,
  preallocation refusal and fixed refusal; `validate_create_args`'s
  `--subformat`, monolithicFlat and preallocation checks; and the
  convert host's 32 MiB block assumption, now `:13893`).
* #798 and #799 reproduce as filed. `resize` of a fixed 64M image to
  128M leaves `LeaveBlocksAllocated` set with the eight new BAT
  entries `NOT_PRESENT`, which qemu-img check accepts; `instar create
  -f vhdx` at 65T exits 0 where qemu-img refuses.
* Two smaller defects, recorded where the phases that own them will
  find them:
  * `instar convert` leaves a zero-byte output file when it fails (seen
    on the #626 geometry). Whether qemu-img does the same was not
    measured; it is in the master plan's Future work, which asks for
    that measurement before calling it a defect.
  * `instar check` reports an image end offset of 75,497,472 on the
    65M `off` image, whose file is 76,546,048 bytes; on block-multiple
    images the two agree. It is the partial final block again, so it
    is recorded on #797 (issuecomment-6334705401), which phase 4
    fixes. qemu-img reports no end offset for VHDX at all.

### Manifest and the tests that read it

`tests/manifest.json` entries require `id`, `path`, `format`,
`safety`, `run_in_ci` and `description`
(`tests/helpers/types.py:68-85`), and the VHDX fixtures carry
`sha256`, `generated_by` and `tags`. No manifest image is fixed today:
`leave_blocks_allocated` is false on all six parseable VHDX entries.

Registering a safe vhdx image has these effects, which the steps rely
on:

* `TestVhdxStructureManifest` parses every safe vhdx entry
  (`tests/test_vhdx_structure.py:368-380`), so the new fixtures are
  parsed with no further work.
* `test_info_safe`, `test_map` and `test_measure` build their
  per-image scenarios from cross-version baselines, and skip an image
  that has none (`tests/test_info_safe.py:75-82`; `tests/test_map.py`'s
  "no baseline meta" skip). Until phase 10 generates baselines, those
  modules skip the new fixtures, and the new test module carries their
  coverage.

### Master plan corrections made in this commit

The planning commit corrects, at source: the phase 2 paragraph of
"Sequencing rationale" (fixture size, generator location, and the
create baselines moving to phase 10); the phase 10 paragraph, which
gains the create baselines and the per-image fixture baselines; and
the phase 2 row of the Execution table, which drops "and baselines";
and the seven drifted `src/vmm/src/main.rs` citations in the
Situation. Later steps
do not need to redo them.

## Decisions

1. **The fixtures are small, and match pin cases.** Five fixtures,
   about 106 MiB stored in total:

   | Manifest id | qemu-img command | File | Purpose |
   |---|---|---|---|
   | `vhdx-fixed-16M` | `create -f vhdx -o subformat=fixed X 16M` | 24 MiB | Block multiple (two 8 MiB blocks), ZERO entries |
   | `vhdx-fixed-16M-bsz-off-data` | `create -f vhdx -o subformat=fixed,block_state_zero=off X 16M`, then data at 0 and 12M | 24 MiB | Block multiple, FULLY_PRESENT at sequential offsets, with data to read back |
   | `vhdx-fixed-17M` | `create -f vhdx -o subformat=fixed X 17M` | 25 MiB | Partial final block (three blocks, the last holding 1 MiB), ZERO entries |
   | `vhdx-fixed-17M-bsz-off-data` | `create -f vhdx -o subformat=fixed,block_state_zero=off X 17M`, then data at 0 and in the last 64 KiB | 25 MiB | Partial final block with data in it: the #797 reproducer |
   | `vhdx-dynamic-131041M-block-1M` | `create -f vhdx -o block_size=1M X 131041M` | 8 MiB | The #626 reproducer |

   The data writes are `qemu-io -c 'write -P 0x11 0 64k' -c 'write -P
   0x22 12M 64k'` for the 16M image and `qemu-io -c 'write -P 0x33 0
   64k' -c 'write -P 0x44 17344k 64k'` for the 17M image (17344 KiB is
   17 MiB less 64 KiB). The phase 1 pin already holds 64M and 65M
   cases, but at 72 and 73 MiB each, four of them would triple the
   bytes every CI leg checks out. The four fixed fixtures get pin
   cases of their own instead (`vhdx-fixed-16M`,
   `vhdx-fixed-16M-bsz-off`, `vhdx-fixed-17M`, `vhdx-fixed-17M-bsz-off`),
   and the dynamic one already has `vhdx-dynamic-131041M-block-1M`.
   Each fixture's structural summary must then equal its pin case's,
   which proves the committed file is what every one of the 80 qemu
   builds writes, not only the build that happened to generate it.
   The data writes do not change the summary (re-measured).

2. **The fixed #626 geometry is generated at test time, not committed.**
   Committing it costs 128 GiB per CI checkout (survey), and the
   committed dynamic fixture already reproduces the miscount. The
   test module creates the two fixed 131041M images (`on` and `off`)
   with the host qemu-img in a temporary directory, as
   `tests/test_qemu_create_pins.py` already does for
   `vhdx-fixed-131041M-block-1M-bsz-off`, and runs only the operations
   that do not read the data region: `info`, `check` and `map`. A
   convert or compare of 128 GiB would be the first test in the suite
   to read that much, and the BAT walk that #626 breaks is already
   exercised by `check` and `map`. The same holds for the committed
   dynamic 131041M fixture, which is 128 GiB virtual too, so it also
   gets only `info`, `check` and `map` (corrected during step 2c; see
   the execution notes).

3. **The generator goes in `scripts/create-vhd-testdata.sh`**, as a
   new section behind the existing `REGEN_VHDX=1` switch, writing to
   the script's default output directory
   (`../instar-testdata/custom/format-coverage/`). That is the house
   pattern the master plan meant to follow, and the script already
   owns every VHDX fixture and the non-reproducibility switch. It
   uses the host qemu-img; decision 1's structural check is what makes
   the version that generated it irrelevant.

4. **Today's failures are pinned as known failures that fail once
   fixed.** The new module carries a `KNOWN_READ_FAILURES` table keyed
   by `(image, operation)`, each with the issue number and the guest
   message. A listed pair must still fail with that message; when it
   starts passing, the test fails and says to remove the entry. That
   is the `TestMapDivergenceRegression` pattern in `tests/test_map.py`,
   and it makes phase 4's job visible from the test output rather than
   from a plan: fixing #626 and #797 turns seven known failures into
   test failures that point at the table. The expected entries, from
   the survey, are check on `vhdx-fixed-17M-bsz-off-data` (#797), and
   check and map on `vhdx-dynamic-131041M-block-1M` and on both
   runtime 131041M fixed images (#626). Any other failure the step
   finds is reported to the management session before it is tabled,
   because it may be a defect nobody has filed.

5. **`CREATE_CASES` and per-image baselines move to phase 10.**
   Adding create baselines now breaks `test_create_cases_match_baselines`
   on develop between the testdata push and the instar merge, needs a
   divergence entry per case on arrival, and measures nothing the pin
   does not (survey). Per-image cross-version baselines for the new
   fixtures (`info`, `map`, `measure`, `check`) are the same kind of
   work: regenerating every image for a command across 80 builds, then
   regenerating and validating `profiles/`, which has been silently
   wrong twice before. Phase 10 is the phase the master plan built for
   that procedure. This is the decision a reviewer is most likely to
   question, because it leaves `test_info_safe`, `test_map` and
   `test_measure` skipping the new fixtures until then. The new
   module's live comparison with the host qemu-img is what covers them
   meanwhile, and the skips are by-name (the modules print "no
   baseline"), not silent.

6. **Landing order: instar-testdata first, then instar.** The fixtures
   go to instar-testdata `main` as a direct commit (that repository
   takes no merge requests), and the instar pull request follows.
   Until the instar pull request merges, develop's manifest does not
   name the new files, so nothing on develop can see them. The
   reverse order would make develop's `sha256` checks fail on missing
   files. The `Merged` cell records both: `instar-testdata <sha>
   (direct to main); instar <merge> (#<pr>)`.

7. **The runtime convert-to-fixed images join the read matrix.** The
   test module also builds `vhdx-convert-fixed` and
   `vhdx-convert-fixed-bsz-off` at test time from
   `qemu_pins.write_convert_source` with the host qemu-img, and runs
   the full read matrix on them. qemu's default convert appends data
   blocks past the preallocated area (survey), a layout no committed
   fixture has and the one phase 8's in-place writer will deliberately
   not produce. Reading it correctly is instar's job either way.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | **Pin the fixture sizes.** In `tests/helpers/qemu_pins.py`, add four cases beside the existing fixed 64M and 65M cases, using the same `_fixed` helper: `vhdx-fixed-16M` (`subformat=fixed`, 16M), `vhdx-fixed-16M-bsz-off` (`subformat=fixed,block_state_zero=off`, 16M), `vhdx-fixed-17M` and `vhdx-fixed-17M-bsz-off` (17M). Read decision 1 of `docs/plans/PLAN-bat-population-phase-02-fixtures.md` for why. Re-pin with `python3 tools/sweep-qemu-create.py --write tests/golden/qemu-create-pins.json --jobs 2 --scratch <your scratch dir>/sweep` (about six minutes; testdata at `/home/mikal/src/shakenfist/instar-testdata`). Then confirm, with `git diff tests/golden/qemu-create-pins.json`, that the only change is four new cases, each one group covering all 80 versions. Any change to an existing case means host load made a qemu command fail: replay that case with `--check --cases <name> --versions <v>` and do not commit a changed existing case. `test_vhdx_create_cases_have_one_answer` covers the new cases automatically; run `test_qemu_create_pins`, `test_vhdx_structure` and `test_helpers` with stestr (`cd tests && ../../instar/tests/.venv/bin/stestr run '<regex>'`) and report "Ran: N". If the per-version tests now take over 30 s, update their lines in `tests/stestr-timing.txt` (sorted by id). Commit subject: `Pin the small fixed VHDX fixture sizes.` |
| 2b | medium | sonnet | none | **Generate and register the fixtures.** Add a section to `scripts/create-vhd-testdata.sh` that writes the five fixtures in decision 1's table (exact commands and qemu-io writes there) into the script's output directory, behind the existing `REGEN_VHDX=1` switch and skipped when present, following how the script already handles its qemu-written VHDX outputs (`:109-127`). Run it with `REGEN_VHDX=1` against `/home/mikal/src/shakenfist/instar-testdata/custom/format-coverage/` and confirm the five files appear at the sizes in the table (`ls -l`; the dynamic one is 8,388,608 bytes). Do **not** `git add`, commit or push anything in instar-testdata: the management session does that after the operator approves it. Register the five in `tests/manifest.json`: `id` as in the table, `path` relative to the testdata root, `format` `vhdx`, `safety` `safe`, `run_in_ci` true, a `description` naming the qemu-img command, `sha256`, `generated_by` `scripts/create-vhd-testdata.sh`, and `tags` following the existing vhdx entries plus `fixed` for the four fixed ones. Then run every module that iterates the manifest (`test_info_safe`, `test_map`, `test_measure`, `test_oslo_crossval`, `test_vhdx_structure`, `test_convert`, `test_create`, `test_adversarial`, `test_snapshot`, `test_differencing`, `test_qcow1_smoke`) with `INSTAR_TESTDATA_PATH` pointing at that checkout, and report, for each, "Ran: N", failures, and how it treats the five new ids (parsed, skipped and why, or failed). A failure there is a finding: report it rather than working around it. Commit subject: `Generate fixed VHDX fixtures from qemu-img.` |
| 2c | high | opus | none | **Test what the fixtures are and what instar reads.** Write `tests/test_vhdx_fixed_fixtures.py` (an `InstarTestBase` subclass; single quotes; 120 columns). Read decisions 1, 2, 4 and 7 of `docs/plans/PLAN-bat-population-phase-02-fixtures.md` first. (1) For each of the five fixtures, `summarise(parse_vhdx(path))` equals the `summary` of `qemu_pins.expected(<pin case>)`, from a `FIXTURE_PIN_CASES` dict mapping manifest id to pin case. This needs no qemu. (2) A read matrix: `info --output=json` virtual size equals qemu-img's; `check` exits 0; `map --output=json` exits 0 and its extents cover `[0, virtual size)` without gaps or overlaps (do not compare map JSON with qemu's: instar deliberately reports BAT-level extents, `tests/test_map.py:250-266`); `convert -O raw` is byte-identical to `qemu-img convert -O raw`; and `compare` against qemu's raw conversion exits 0. Run it on the five fixtures, on the runtime `vhdx-convert-fixed` and `vhdx-convert-fixed-bsz-off` images (decision 7: build the source with `qemu_pins.write_convert_source` and convert with the host qemu-img using that case's arguments), and on the two runtime fixed 131041M images, restricted to info, check and map (decision 2). Use a `tempfile.TemporaryDirectory` per test; gate the qemu-dependent parts with `skip_unless_qemu_supports('vhdx')`. (3) `KNOWN_READ_FAILURES` per decision 4: each entry must still fail with its guest message, and a passing entry fails the test with "remove this entry". Expected entries are in decision 4. If any other pair fails, stop and report the image, the operation and the first error line to the management session before tabling it. Run `tools/ci/check-test-partition.sh`, then the module with stestr, confirming "Ran: N" equals the test count with 0 failures. Add `tests/stestr-timing.txt` lines for tests over 30 s, sorted. Mutation check: point one `FIXTURE_PIN_CASES` entry at the wrong pin case, and delete one `KNOWN_READ_FAILURES` entry; confirm each makes a named test fail, from a copy of the module, never by editing it in place. Commit subject: `Test instar's reads of qemu's fixed VHDX.` |
| 2d | low | sonnet | none | **Document the fixtures.** In `docs/testing.md`, where `scripts/create-vhd-testdata.sh`'s fixtures are described (`:2668-2690`), add the five fixed and #626 fixtures: what each covers, that they regenerate only under `REGEN_VHDX=1`, and that each must match its pin case. Then a short paragraph on `tests/test_vhdx_fixed_fixtures.py`: its known-failure table, and that an entry that starts passing fails the test. Name issues #626 and #797, not this plan. Run `git diff origin/develop...HEAD -- tests tools scripts docs/testing.md \| grep -nE 'PLAN-\|[Pp]hase [0-9]\|step 2[a-d]'` and confirm it prints nothing. `git add -A`, then `pre-commit run --all-files`. Commit subject: `Document the fixed VHDX fixtures.` |

The management session runs the steps in order: 2b's fixtures need
2a's pin cases to be checkable, and 2c needs 2b's files and manifest
entries. After 2d, and only with the operator's yes, it commits the
five files in instar-testdata (`git lfs` tracks `*.vhdx` already),
rebases on `origin/main` (the fuzz automation moves it daily) and
pushes, then records the testdata commit in this plan's Execution
notes before the instar push.

## Execution notes

- 2026-10-22 (AEDT). Back brief answers: run 2a→2d sequentially via
  sub-agents; commit the five fixtures directly to instar-testdata
  `main` (rebased on origin/main) once every instar step has passed
  review and before the instar push; after the definition of done
  passes and the testdata push has landed, push the branch and open
  the pull request.
- Step 2b generated the fixtures into a scratch directory with
  `REGEN_VHDX=1` and copied only the five new files into
  instar-testdata, because a `REGEN_VHDX=1` run against the real
  checkout also regenerates the four existing non-reproducible VHDX
  files. Review corrected the pinned range the draft claimed (6.0.0
  to 10.2.0, not 5.2) and the script header's file counts.
- Step 2c found that the step brief put the dynamic 131041M fixture
  through the full read matrix, where convert and compare also fail
  on #626 (compare reporting it as a content mismatch, #807). Rather
  than tabling two more known failures, the fixture now gets only the
  metadata operations, as decision 2 already did for the fixed
  131041M images and for the same reason; decision 2 is corrected to
  say so. The known-failure table holds the seven entries decision 4
  lists, and the module runs 46 tests.
- The five fixtures landed in instar-testdata as `d2f9467719`, directly
  on `main` after rebasing onto origin/main (`278b015a30`), before the
  instar branch was pushed.

## Risks and mitigations

* **The pin and the fixtures disagree because the host qemu-img
  differs from the swept builds.** Decision 1's structural test is
  what detects that; if it fires, the management session compares the
  fixture's summary with the pin's group for 10.0.x and treats any
  difference as a new version boundary, which phase 1 says needs a
  human decision.
* **A new failure in the read matrix is tabled without being
  understood.** Step 2c must stop and report any failure beyond
  decision 4's list. The management session reproduces each with the
  CLI before agreeing to table it, and files an issue for anything
  that is not #626, #797 or #807.
* **The testdata push and the instar merge are separated by a long
  queue.** Harmless in this order (decision 6). If the instar pull
  request is abandoned, the five files sit unreferenced in
  instar-testdata; the management session says so in the plan rather
  than deleting them, since LFS history keeps them anyway.
* **The 131041M runtime images run out of space on a CI filesystem
  that does not support sparse files.** `test_qemu_create_pins`
  already creates the same geometry on every CI leg, so a failure
  would already be visible there. Do not weaken the assertion; report
  it.
* **Host load makes a qemu command fail during the re-pin.** Step 2a
  replays any changed existing case and never commits one.

## Definition of done

Each item is a command, run from the worktree root, with its
expected result.

* `git diff --name-only origin/develop...HEAD -- src/` prints nothing.
* `python3 tools/sweep-qemu-create.py --check tests/golden/qemu-create-pins.json --versions 6.0.0,10.2.0 --cases vhdx-fixed-16M,vhdx-fixed-16M-bsz-off,vhdx-fixed-17M,vhdx-fixed-17M-bsz-off`
  exits 0.
* `python3 -I -c "import json; d = json.load(open('tests/golden/qemu-create-pins.json')); print(len(d['versions']), len(d['cases']))"`
  prints `80 35`.
* For each of the five manifest ids, the file exists in the testdata
  checkout, and its `sha256sum` equals the manifest's.
* `git -C ../instar-testdata log origin/main --oneline -1 -- custom/format-coverage/vhdx-fixed-17M-bsz-off-data.vhdx`
  prints the testdata commit recorded in Execution notes.
* The stestr run of `test_vhdx_fixed_fixtures`, `test_vhdx_structure`,
  `test_qemu_create_pins` and `test_helpers` reports "Ran: N" equal to
  the number of tests in those modules, 0 failures, and no skips
  other than missing-binary and missing-driver skips.
* `test_vhdx_fixed_fixtures` lists exactly the known failures in
  decision 4, each naming #626 or #797.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- tests tools scripts docs/testing.md | grep -nE 'PLAN-|[Pp]hase [0-9]|step 2[a-d]'`
  prints nothing.
* `git add -A && pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with
it. Ask then, not at the end, whether the management session may
commit and push the five fixtures to instar-testdata `main`: that push
is outward-facing and cannot be taken back, since LFS history keeps
the objects.
