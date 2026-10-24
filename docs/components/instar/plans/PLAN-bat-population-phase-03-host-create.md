# Phase 3: host-only create, no-op preallocation and monolithicFlat

Phase 3 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation and the phase 3 paragraph of "Sequencing rationale" first.
This file assumes them, and corrects that paragraph where the survey
found it wrong.

Planning effort: medium, as the master plan sets for this phase. The
work is host-only and follows two existing patterns: raw create's
open-and-truncate, and convert's monolithicFlat descriptor. The
survey's one surprise is a naming divergence in code this phase
shares (decision 3).

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The two this phase
carries out:

* **Open question 1:** preallocation of vmdk, vpc and vhdx is qemu
  parity. `falloc` and `full` produce exactly the image `off` would,
  and `metadata` is refused without promising future support.
* **Open question 2:** `create -f vmdk -o subformat=monolithicFlat`
  is written by the host with no guest. It is a sparse extent plus
  the descriptor convert already writes, and it refuses a backing
  file as qemu does.

Neither is revisited here.

## Scope

In:

* `create` accepts `-o preallocation=falloc|full` and
  `--preallocation falloc|full` for vmdk, vpc and vhdx. Each produces
  the `off` image, and `metadata` is refused in qemu's terms.
* `measure` accepts the same through `-o`, and refuses `metadata`
  through `-o` and `--preallocation` alike. Its answers do not change.
* One host function names a monolithicFlat extent the way qemu does.
  `convert` uses it, which fixes #820, and `create` uses it too.
* `create -f vmdk -o subformat=monolithicFlat` (and
  `--subformat monolithicFlat`) works on the host. It writes a sparse
  extent under every preallocation mode plus the descriptor, and
  refuses a backing file, `zeroed_grain=on` and a grain size.
* Tests that use the phase 1 pin as the oracle, and the per-phase
  documentation: `docs/create.md`, `docs/measure.md`,
  `docs/convert.md`, `docs/format-coverage.md` and `docs/quirks.md`.

Out:

* **VHDX `subformat=fixed`.** It is refused exactly as today, so
  `subformat=fixed,preallocation=falloc` still fails on the fixed
  refusal. Phase 7 lifts it.
* **Cross-version `CREATE_CASES` baselines** for the new cases. They
  go to phase 10, with the fixed VHDX cases phase 2 moved there
  (decision 5).
* **qemu's DDB lines in VMDK descriptors.** Neither instar VMDK writer
  emits them today. They are recorded in Future work (decision 4).
* **Any guest, crate or call-table change.** Nothing under
  `src/operations/` or `src/crates/` changes, and nor does the
  `CreateConfig` or `MeasureConfig` layout.

## What the survey found

Line numbers are against `develop` at `5c5bba75`. The Situation's
line numbers for `src/vmm/src/main.rs` have drifted by about 30 lines
since it was written. Its claims still hold, so it is left as it is,
and the numbers below are the current ones.

### The refusals are where the Situation says

* **create `-o` parser:** `parse_create_o_options` (`main.rs:15276`).
  Its `("vmdk" | "vpc" | "vhdx", "preallocation")` arm
  (`:15487-15505`) accepts `off` and refuses the other three modes
  with "is not yet supported ... (preallocation is implemented for raw
  and qcow2 only)". The vmdk `subformat` arm (`:15418-15430`) already
  accepts `monolithicFlat`. `zeroed_grain`, `adapter_type`,
  `hwversion` and `toolsversion` are accept-ignored and not stored
  (`:15434-15440`).
* **`validate_create_args`** (`:19422`) refuses monolithicFlat
  (`:19498-19503`, "multi-file subformats are not implemented; use
  instar convert -O vmdk for now"). It also refuses non-`off`
  preallocation for the three formats a second time (`:19533-19553`).
  A unit test pins the old message (`:20012`), and so does an
  integration test (`tests/test_create.py:1600-1606`).
* **`run_create`** (`:18223`) applies `-o` overrides, validates, and
  sends raw to `run_create_raw` (`:19125`) and everything else to the
  guest (`run_create_nonraw`, `:18331`).

### The guest already ignores preallocation for these formats

`run_create_nonraw` turns `args.preallocation` into
`CREATE_CONFIG_PREALLOC_*` flag bits for every format
(`:18354-18359`). The create guest reads them only for qcow2
(`src/operations/create/src/main.rs:399-404` and `:1089`), and the
host post-pass is qcow2-only (`main.rs:18566-18567`). Accepting
`falloc` and `full` would therefore produce the `off` image
unchanged. The host still sends `PREALLOC_OFF` for these formats
(decision 1), so that this stays true however the guest changes.

### measure is half there already

`measure --preallocation` is accepted for vpc, vmdk and vhdx, and has
no effect. `--preallocation metadata` is accepted too; measured with
a build of `da9f043f`, `measure -O vpc --size 64M --preallocation
metadata` prints the same answer as `off`. `-o preallocation=...` is
refused as an unrecognised key for all three (`parse_o_options`,
`:15018`; the catch-all is at `:15186`). The two spellings disagree.
qemu-img cannot measure any of the three formats, so instar's own
writer is the only oracle.

### convert names the flat extent differently from qemu (#820)

convert derives the extent as `<file_stem>-flat.vmdk`
(`main.rs:14297-14309`). qemu splits the basename at its last `.` and
writes `<prefix>-flat<postfix>`: see `filename_decompose`, and the
`"%s-flat%s"` format in `vmdk_co_do_create` (`block/vmdk.c:2279-2316`
and `:2599` in qemu 7.0.0). Measured with qemu-img 10.0.13 and the
`da9f043f` build:

| Output | qemu-img extent | instar convert extent |
|---|---|---|
| `out.vmdk` | `out-flat.vmdk` | `out-flat.vmdk` |
| `out.img` | `out-flat.img` | `out-flat.vmdk` |
| `out` | `out-flat` | `out-flat.vmdk` |
| `.hid` | `-flat.hid` | `.hid-flat.vmdk` |

Filed as #820. The image is valid either way, because the descriptor
names the extent that was written. The two differ only when the output
name does not end in `.vmdk`.

qemu also searches for `\` and then `:` when the name has no `/`.
Nothing on a Linux host uses that, and on `a:b.vmdk` it would put the
extent in the working directory while the descriptor resolves it next
to itself, so instar does not copy it (decision 3).

### What qemu does around monolithicFlat create

These were measured with qemu-img 10.0.13, and the pin covers all 80
builds where it says so.

* The extent is sparse, with `ftruncate` to the virtual size under
  `off`, `falloc` and `full`. Its length is the size rounded up to
  512: a `1000` request gives a 1024-byte extent and `RW 2 FLAT`. The
  pin's `prealloc-vmdk-monolithicFlat` and `vmdk-flat-64M` cases cover
  this.
* With `-b`, qemu fails: "Flat image can't have backing file"
  (`block/vmdk.c:2429`; pin `vmdk-flat-backing`, all 80 builds).
* With `zeroed_grain=on`, qemu fails: "Flat image can't enable zeroed
  grain" (`:2434`), and leaves no file.
* With `preallocation=metadata`, qemu fails: "Unsupported
  preallocation mode: metadata", and leaves a zero-byte descriptor.
* The descriptor carries `ddb.virtualHWVersion`, `ddb.geometry.*` and
  `ddb.adapterType`, and from 6.2.0 also `ddb.toolsVersion`. The pin
  has two groups at exactly that boundary. `adapter_type` and
  `hwversion` change those lines. instar's `build_flat_descriptor`
  (`src/crates/vmdk/src/lib.rs:261`) writes none of them and a
  constant `CID=fffffffe`. Its sparse writer omits the DDB lines too,
  so this is not specific to flat output.

### The pin is a ready oracle, but not for absolute lengths

The phase 1 pin (`tests/golden/qemu-create-pins.json`) holds seven
preallocation cases and two monolithicFlat cases, one group each
across all 80 builds except `vmdk-flat-64M`. `qemu_pins.run_case`
runs `<binary> create -f FMT -o ... IMAGE SIZE`, which is also
instar's command line, so the same runner can measure instar. Its
`file_length` fields cannot be compared directly, though. instar's 64M
images are 11264 bytes (vmdk), 4194304 (vhdx; its default block size
is #645) and 67109376 (fixed vpc; qemu rounds to CHS) against qemu's
65536, 8388608 and 67125760. Only dynamic vpc agrees, at 2560. What
transfers is the pin's shape: every mode's `rc`, `same_bytes_as_off`
and `same_allocation_as_off`. Decision 5 builds the tests on that.

### Master plan corrections made in this commit

* The phase 3 paragraph of "Sequencing rationale" said the phase's
  tests were cross-version info baselines. It now says the pin is the
  oracle, that the baselines join phase 10's, and that the phase
  fixes #820.
* The phase 10 paragraph now lists the monolithicFlat create cases
  among the `CREATE_CASES` it adds.
* Future work gains the VMDK descriptor DDB lines.
* The Execution table and the `docs/plans/index.md` row link this
  file.

## Decisions

1. **The host normalises preallocation for vmdk, vpc and vhdx.**
   Once validated, `falloc` and `full` are recorded as they were typed
   in `args.preallocation`. For these three formats,
   `run_create_nonraw` sends `CREATE_CONFIG_PREALLOC_OFF`. The guest
   ignores the bits for these formats today, but the parity claim
   should not rest on that: a future guest reading the bit for vhdx
   would otherwise change every `falloc` image silently. No guest
   change is needed.

2. **One refusal text for `metadata`, shared by create and measure.**
   It reads `<op>: preallocation=metadata is not supported for
   <format>`, built by one helper both validators call. It drops "not
   yet" and "future work", because qemu refuses it at every version
   and the plan does not intend to add it. The create `-o` parser and
   `validate_create_args` both use it, as the old text did. For
   measure, the check sits in `run_measure` after `-o` has been
   applied (`:15805-15860`), so `--preallocation` and `-o` cannot
   disagree again. Making measure's `--preallocation metadata` an
   error for these formats is a deliberate tightening. qemu has no
   measure answer for them, and measure is defined as a prediction of
   instar's own writer, which will refuse the mode.

3. **The extent name follows qemu's rule, in one function, and
   convert changes with it.** Add a host function, for example
   `vmdk_flat_extent_path(output: &Path) -> (PathBuf, String)`,
   returning the extent path and the name the descriptor records. It
   takes the basename after the last `/`, splits it at its last `.`,
   and returns `<prefix>-flat<postfix>` in the output's directory.
   This covers a leading dot (`.hid` gives `-flat.hid`) and no dot at
   all (`out` gives `out-flat`). It does not split on `\` or `:`
   (survey). Both convert and create call it.

   This changes convert's output for any name not ending in `.vmdk`.
   That is the decision a reviewer is most likely to question,
   because it alters an existing command's behaviour inside a phase
   about create. Three things justify it:

   * The alternative is two naming rules, or giving create the wrong
     rule on purpose.
   * The change only moves instar to the name qemu-img writes.
   * The image stays readable, because a descriptor always names its
     own extent.

   The commit and the PR say "Fixes #820", and `docs/convert.md`
   states the rule.

4. **The descriptor stays as convert writes it, without DDB lines and
   with a constant CID.** Matching qemu would mean honouring
   `adapter_type`, `hwversion` and `toolsversion` (all
   accept-ignored), computing qemu's geometry, and picking one side
   of the 6.2.0 `toolsVersion` boundary. The same work would be owed
   to the sparse writer. Neither `qemu-img info` nor any reader
   instar or qemu ships uses those lines, so the work buys only
   byte-level fidelity. It goes to Future work. `docs/quirks.md`
   records the omission, and #566 already covers the constant CID.

   `adapter_type`, `hwversion` and `toolsversion` stay accept-ignored
   for monolithicFlat. Three options are refused rather than ignored:
   * `-o zeroed_grain=on`, as qemu refuses it. The parser stores the
     value instead of dropping it.
   * A grain size, through `--grain-size` or `-o grain_size`. Its
     default is the 0 sentinel; anything else has no meaning for a
     flat extent.
   * `-b`, with instar's wording of qemu's refusal.

5. **The tests take the pin as oracle for shape, and instar's own
   `off` image for lengths.** New tests in `tests/test_create.py` run
   `qemu_pins.run_case(<instar binary>, case, workdir)` on these
   cases:
   * `prealloc-vpc`, `prealloc-vmdk` and `prealloc-vhdx`;
   * `prealloc-vpc-fixed`;
   * `prealloc-vmdk-monolithicFlat`, once step 3c lands;
   * `vmdk-flat-64M` and `vmdk-flat-backing`, after 3c as well.

   For each preallocation case and mode, `rc`, `same_bytes_as_off`
   and `same_allocation_as_off` must equal the pin's
   `qemu_pins.expected(case)`. `file_length` must equal that of
   instar's own `off` image in the same run, and `metadata`'s
   `stderr` must be decision 2's text. For `vmdk-flat-64M`:
   * `extent_length` must equal the pin's;
   * the normalised descriptor must equal the pin's newest group with
     every `ddb.` line removed (decision 4);
   * `CID` is already normalised by `normalise_descriptor`.

   `prealloc-vhdx-fixed` goes in a known-refusal table with the
   `subformat=fixed` message. The table must fail with "remove this
   entry" once phase 7 lets the case pass, the pattern
   `tests/test_vhdx_fixed_fixtures.py` uses. Live comparisons against
   the host qemu-img cover what the pin cannot, and skip when
   qemu-img is absent:
   * the four extent-name shapes, through both create and convert;
   * `qemu-img info --output=json` of an instar flat image against
     qemu's own, ignoring `filename`, `cid`, `actual-size`, and the
     `filename` and `actual-size` inside `children`.

   The cross-version `CREATE_CASES` baselines stay in phase 10. Adding
   a case to the instar mirror without its generated baselines fails
   `test_create_cases_match_baselines`. Generating them is the
   80-version testdata procedure phase 10 owns, and phase 2 already
   moved the fixed VHDX cases there for the same reason.

6. **monolithicFlat create cleans up after itself, and stays sparse.**
   The steps, in order:
   1. Validate everything first: size, backing, preallocation,
      `zeroed_grain` and grain size.
   2. Open the extent with `create(true).truncate(true)` and mode
      0644, as `run_create_raw` does, and `set_len` it to the size
      rounded up to 512. Do not call `create_raw_finalize`: its
      `falloc` and `full` really allocate, and qemu's extent stays
      sparse under every mode.
   3. Write the descriptor with `build_flat_descriptor`, sized from
      the same rounded value, and sync both files.

   On any failure after a file is opened, remove both files. That
   includes a descriptor too long for its buffer (the function
   returns the length it needed, so check it), and it matches what
   every other instar create does on failure. qemu leaves a
   zero-byte descriptor on some failures; instar does not copy that.
   The success line and JSON report the descriptor as `filename` and
   the descriptor's length as both `metadata_bytes_written` and
   `file_size_after`, with `resolved_unit_size` 0.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **No-op preallocation for vmdk, vpc and vhdx.** First read decisions 1, 2 and 5 of `docs/plans/PLAN-bat-population-phase-03-host-create.md`, and its survey. In `src/vmm/src/main.rs`: (1) in `parse_create_o_options` (`:15487-15505`), accept `falloc` and `full` for vmdk, vpc and vhdx; refuse `metadata` with decision 2's text, built by a new helper that measure also uses. (2) Do the same in `validate_create_args` (`:19533-19553`) and fix its comment. (3) In `run_create_nonraw`'s flag mapping (`:18354-18359`), send `CREATE_CONFIG_PREALLOC_OFF` for these three formats. (4) In measure's `parse_o_options` (`:15018`), add a `preallocation` arm for vpc, vmdk and vhdx accepting `off`, `falloc` and `full`; put the `metadata` refusal in `run_measure` after `-o` is applied (`:15805-15860`), so both spellings refuse it. Update the unit test at `:20012`, and add unit tests for each format and mode in both parsers and the validator. In `tests/test_create.py`, replace `:1600-1606` with decision 5's preallocation tests for `prealloc-vpc`, `prealloc-vmdk`, `prealloc-vhdx` and `prealloc-vpc-fixed`. Use `helpers.qemu_pins` (`run_case`, `expected`); do not reimplement the volatile-byte mask. Put `prealloc-vhdx-fixed` and `prealloc-vmdk-monolithicFlat` in a known-refusal table, each with the message it fails with today (step 3c removes the second). Add measure tests: for each of the three formats, `-o preallocation=falloc` and `full` print the same answer as `off`, and `metadata` is refused through `-o` and through `--preallocation`. Documentation: `docs/create.md` (the per-format `-o` lists, the "Preallocation modes" table, the Future work bullet on vmdk/vpc/vhdx preallocation), `docs/measure.md` `:91` and nearby, and `docs/quirks.md`'s "Preallocation accept set" (`:1104-1117`). Each must say the modes are accepted and change nothing, as qemu's are, and that `resize --preallocation` on these formats does allocate (`docs/format-coverage.md`'s resize table). Commit subject: `Accept no-op preallocation for vmdk, vpc and vhdx.` |
| 3b | medium | sonnet | none | **Name flat extents the way qemu does (#820).** First read decision 3 and the #820 survey section of `docs/plans/PLAN-bat-population-phase-03-host-create.md`. In `src/vmm/src/main.rs`, add the naming function decision 3 describes, near the convert host path, with a doc comment citing `block/vmdk.c:2279-2316` and `:2599` (qemu 7.0.0, under `instar-testdata/qemu-sources/7.0.0/`). Replace convert's inline rule (`:14297-14309`) with it. Unit tests: the survey table's four rows, plus a name inside a directory and `a.b.c` (giving `a.b-flat.c`). In `tests/test_convert.py`, near the existing monolithicFlat tests (`:2470-2560`), add a test that converts to `out.img` and `out` and asserts the extent names and the descriptor's `FLAT "..."` line. When the host qemu-img is present, it must also assert the same names as `qemu-img convert -O vmdk -o subformat=monolithicFlat` writes. The existing `.vmdk` tests must pass unchanged. In `docs/convert.md`, near `:83`, state the naming rule. Commit subject: `Name flat VMDK extents the way qemu does.` with `Fixes #820.` in the body. |
| 3c | medium | opus | none | **Create monolithicFlat VMDK on the host.** First read decisions 3 to 6 and the "What qemu does around monolithicFlat create" survey section of `docs/plans/PLAN-bat-population-phase-03-host-create.md`. In `src/vmm/src/main.rs`: remove the refusal in `validate_create_args` (`:19498-19503`). Refuse `-b` ("create: vmdk monolithicFlat cannot have a backing file"), `zeroed_grain=on` (store it in `CreateOptionOverrides`; it is accept-ignored at `:15434-15440` today) and a non-zero grain size for monolithicFlat. Add `run_create_vmdk_flat`, dispatched from `run_create` (`:18223`) beside the raw branch, implementing decision 6 with 3b's naming function and `vmdk::build_flat_descriptor`. Do not touch `create_raw_finalize` or the guest. Unit tests for every new refusal. In `tests/test_create.py`: remove `prealloc-vmdk-monolithicFlat` from 3a's known-refusal table so it runs under decision 5. Add `vmdk-flat-64M` and `vmdk-flat-backing` under decision 5's rules. Add tests that: a 1000-byte request gives a 1024-byte extent; the extent's allocated blocks under `full` equal those under `off` (`os.stat(...).st_blocks`); `instar info` and `instar check` read the image; the live `qemu-img info` comparison passes; create with an output name of `out` puts the extent where qemu does; and a failure after opening (for example a read-only directory or a pre-existing unwritable extent) leaves neither file. Documentation: `docs/create.md` (vmdk `-o` lists, the "Rejected" list at `:131-132`, the Future work bullet at `:392-394`, whose stated reason is false), `docs/format-coverage.md` (`:331` and the create roster near `:155`), and `docs/quirks.md` (a short section on the descriptor omitting qemu's DDB lines and writing a constant CID, citing #566). Commit subject: `Create monolithicFlat VMDK on the host.` |

The management session runs the steps in order. 3b and 3c both edit
the convert and create host paths in `main.rs`, and 3c uses 3b's
function and 3a's known-refusal table. Every brief also carries the
session's standing rules:
* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned.
* Run the touched test modules with stestr from `tests/`, with
  `INSTAR_BINARY_PATH` pointed at the worktree's build.
* Run `pre-commit run --all-files` after `git add -N` on new files,
  and `tools/ci/check-test-partition.sh`.
* Keep plan, phase, step and decision references out of code and
  docs.
* Keep a mutation script under the step's scratch directory, and run
  each mutant under `timeout`.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-10-23 (AEDT).** The operator chose to land the
  convert rename (#820) in this phase, and allowed the management
  session to push the branch and open the pull request once the
  definition of done passes.
* **3a.** Two corrections in review. Decision 1 now covers measure as
  well as create: the host tells the measure guest "off" for vmdk, vpc
  and vhdx too. A stale comment in `validate_create_args` was also
  fixed. The agent added an `fsync` to
  `qemu_pins._prealloc_create`. Without it, `st_blocks` was read
  before delayed allocation settled, and the vhdx case failed about
  one run in three, even comparing `off` with `off`. It was kept,
  because the pin sweep benefits from it too.
* **3b.** The agent found that `convert` rounds a raw source's virtual
  size up to the next 64 KiB for every output format; qemu-img keeps
  it exactly. The management session reproduced it on `da9f043f` and
  on this branch and filed it as #821. It is unrelated to extent
  naming and out of scope here. 3b's tests use a 1 MiB source so that
  they do not depend on it, and 3c's create path does not go through
  convert.
* **3c.** The agent turned up two defects outside this phase's scope.
  The management session reproduced both and filed them:
  * #822: `instar check` exits 63 on any monolithicFlat VMDK,
    including one qemu-img made, although `docs/check.md` says it
    validates them. 3c's test therefore only requires that check
    treats a created image the way it treats a converted one.
  * #823: instar's sparse VMDK descriptors name their extent
    `"output.vmdk"`, whatever the file is called.

  The descriptor comparison with the pin removes the `ddb.` lines and
  collapses trailing newlines to one, because qemu's blank line after
  `#DDB` is left at the end once those lines go.

## Risks and mitigations

* **A no-op preallocation hides a guest that starts honouring the
  bit.** Decision 1 sends `OFF`, and the tests compare every mode's
  bytes and allocation with `off`. The management session confirms
  that a mutant sending the user's mode instead is caught. It is
  caught only if the guest reacts to the bit, so that mutant is
  expected to survive, and its survival is the reason for decision
  1, not a gap.
* **The convert rename breaks a caller expecting `-flat.vmdk`.** Only
  non-`.vmdk` names change. The management session greps `tests/`,
  `scripts/` and `tools/` for `-flat.vmdk` built from a non-`.vmdk`
  output before committing 3b. The phase 3 survey found only `.vmdk`
  outputs (`tests/test_convert.py:2480-2550`).
* **Cleanup removes a file the user cared about.** Both files are
  opened with truncate, as qemu opens them, so a failure after
  opening has already destroyed the old contents. Removing them is
  no worse, and a validation failure removes nothing because
  validation runs first. The management session reads 3c's diff for
  any removal reachable before the first open.
* **The pin comparison passes vacuously.** If `run_case` raised, or
  `expected` returned nothing for a case, a loose test could pass.
  The mutation scripts break the no-op (allocate under `full`), the
  refusal text, the naming rule and the cleanup, and each must be
  killed.

## Definition of done

Each item is a command, run from the worktree root, with its
expected result.

* `git diff --name-only origin/develop...HEAD -- src/` lists only
  `src/vmm/src/main.rs`.
* With the worktree's build in a scratch directory,
  `instar create -f vpc -o preallocation=full a.vhd 64M`,
  `instar create -f vmdk --preallocation falloc a.vmdk 64M` and
  `instar create -f vhdx -o preallocation=full a.vhdx 64M` each exit
  0, and `instar create -f vpc -o preallocation=metadata b.vhd 64M`
  exits 1 printing `preallocation=metadata is not supported for vpc`.
* `instar measure -O vhdx --size 64M -o preallocation=full` prints
  what `instar measure -O vhdx --size 64M` prints, and
  `instar measure -O vpc --size 64M --preallocation metadata` exits 1.
* `instar create -f vmdk -o subformat=monolithicFlat,preallocation=full out.img 1000`
  exits 0. It leaves `out.img` and a 1024-byte `out-flat.img`, and
  `stat -c %b out-flat.img` prints what it prints for the same
  command with `preallocation=off`.
* `instar convert -O vmdk --subformat monolithicFlat src.raw out.img`
  writes `out-flat.img`.
* `instar create -f vmdk -o subformat=monolithicFlat -b base.vmdk -F vmdk c.vmdk 64M`
  exits 1 and leaves no `c.vmdk` or `c-flat.vmdk`.
* `make test-rust` passes.
* The stestr run of `test_create`, `test_convert`, `test_measure` and
  `test_qemu_create_pins` reports "Ran: N" equal to the tests in those
  modules, with 0 failures. The only skips are missing-binary and
  missing-driver ones.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md | grep -nE 'PLAN-|[Pp]hase [0-9]|step 3[a-c]'`
  prints nothing.
* `grep -n 'not yet supported' docs/create.md` finds no line about
  vmdk, vpc or vhdx preallocation or monolithicFlat.
* `git add -A && pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with it.
Ask then whether the convert rename (decision 3) should land in this
phase, or stay as #820 for a pull request of its own. It is the one
change here to an existing command's output.
