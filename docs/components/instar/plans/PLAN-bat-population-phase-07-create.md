# Phase 7: `create -f vhdx -o subformat=fixed`, guest streaming and host CLI

Phase 7 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation ("What qemu-img actually emits", "What instar does today" and
"Large outputs and the host"), open questions 4 and 8, and the phase 7
paragraph of "Sequencing rationale" first. Also read the phase 6 plan's
decisions 3 to 5, which define the layout fields, the filler and the
plan this phase consumes. This file assumes them, and corrects the
phase 7 paragraph where the survey found it wrong.

Planning effort: high, as the master plan's guidance sets for phases 6
and 7. The output's only external check is a structural comparison.
The survey also found a reason to move the file extension from the
guest to the host (decision 1), against the master plan's text.

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The ones this
phase carries out:

* **Open question 4:** never materialise a fixed BAT. The guest fills
  one buffer of entries at a time with `vhdx::fill_fixed_bat`. The
  output device's capacity comes from the layout. This phase measures
  the wall-clock cost and records it.
* **Open question 8:** a fixed image's default `block_state_zero` is
  qemu's, `on`, so every payload entry is `PAYLOAD_BLOCK_ZERO`.
* **Open question 7:** dynamic VHDX does not honour `block_state_zero`.
  Its BAT stays `NOT_PRESENT`.

None of these is revisited here.

## Scope

In:

* **The host CLI.** `-o subformat=fixed` and `--subformat fixed` for
  vhdx, and `-o block_state_zero=on|off` parsed rather than ignored.
  Refusals for a backing file and for a missing size (decisions 2
  and 3).
* **The config.** The subformat is carried in `CreateConfig.vhd_subformat`,
  as the master plan says, and the block state in a new `flags` bit.
  The struct layout does not change (decision 4).
* **The guest.** The create op asks `plan_vhdx` for a fixed image and
  streams its BAT through one 64 KiB slot (decision 5).
* **The file length.** The host opens the output at the fixed file
  length that `vhdx::layout` gives, which is also the output device's
  capacity (decision 1).
* **Tests.** Structural tests against every fixed pin case and against
  the host `qemu-img` at sizes the pin does not hold. The refusal pins.
  qemu's unopenable partial-last-block size. The ext4 file-size ceiling
  (decisions 6 and 7).
* **The streaming cost**, measured and recorded (decision 8).
* Mutation cases kept in `tools/mutate-differencing.sh` (decision 9).
* `docs/create.md`, `docs/quirks.md`, the `create --help` text and
  `CHANGELOG.md` for the new subformat (decision 10).

Out:

* **Convert and measure.** `convert -O vhdx --subformat fixed` is
  phase 8. `measure -O vhdx -o subformat=fixed` is phase 9, and keeps
  its "not yet supported" refusal until then.
* **Cross-version baselines.** The fixed cases in `CREATE_CASES`, here
  and in instar-testdata's `generate-baselines.py`, are phase 10. Phase
  2's survey explained why they cannot be added before instar writes
  fixed images, and this phase only makes them possible. The pin
  already holds all 80 builds' structure, and this phase's tests read
  it.
* **Fuzzing.** `fuzz_create_emitters` and the differential fuzzer's
  create picker learn the fixed subformat in phase 11. Today's fuzz
  target exercises only the crate, and this phase does not change the
  crate.
* **`docs/format-coverage.md`.** Its output-side rows and notes belong
  to phase 12. Today it says nothing about vhdx `create` subformats
  (survey).
* **Dynamic `block_state_zero`, and dynamic file length.** Future
  work, as before.
* **Batching BAT writes into larger virtio requests.** That would be a
  core and call-table change. Decision 8 says what happens if the
  measurement makes it necessary.

## What the survey found

All references are to `origin/develop` at `aef757d2`, built with
`make instar` in this worktree (`create.bin` 59024 bytes). qemu claims
are cited to `instar-testdata/qemu-sources/7.0.0/block/vhdx.c` or
measured with the host `qemu-img` 10.0.13, or with the pins in
`tests/golden/qemu-create-pins.json`, which all 80 static builds agree
on.

### The host already sends the subformat, and refuses it twice

`apply_create_overrides` maps `-o subformat=fixed` for any target to
`args.subformat = "fixed"` (`src/vmm/src/main.rs`, the `vhd_subformat`
arm). `run_create_nonraw` then sends `vhd_subformat = 1` for any
target whose subformat is `"fixed"`. So the config already carries
the bit the master plan wants. Two refusals stop it first:

* the `-o` parser's vhdx arm, "create: -O vhdx -o subformat=fixed is
  not yet supported" (`main.rs:15629`);
* `validate_create_args`, which admits `--subformat` only for vmdk and
  vpc (`main.rs:19888`).

`-o block_state_zero` and `-o log_size` are accept-ignored without
parsing the value (`main.rs:15645`), so `block_state_zero=bogus` is
accepted today. qemu refuses it with "Parameter 'block_state_zero'
expects 'on' or 'off'", for a dynamic image as well as a fixed one
(measured, 10.0.13). qemu accepts `on`, `off`, `yes`, `no`, `true` and
`false`. It refuses `OFF`, while instar's `parse_create_o_bool`
lowercases first. That is an existing divergence across every boolean
`-o` key, and is not this phase's to change.

The guest ignores `vhd_subformat` for vhdx. `vhdx_opts_from` hardcodes
`VhdxSubformat::Dynamic` and `block_state_zero: true`
(`src/operations/create/src/main.rs:548-583`). For vpc,
`vhd_opts_from` maps `1` to fixed and anything else to dynamic
(`:528-531`).

### The guest maps a fixed backing refusal to a generic message

`plan_vhdx` refuses `Fixed` with any backing reference as
`CreateError::BackingFileUnsupported`. The create op maps that to
`ERROR_INVALID_OPTION` (`src/operations/create/src/main.rs:109`), which
the host renders as "invalid option for target format". qemu says
"Backing file not supported for file format 'vhdx'" (pin
`vhdx-fixed-backing`, all 80 builds), because qemu-img creates no
differencing VHDX at all. The host must refuse first, by name, as
`validate_vmdk_flat_create_args` (`main.rs:19771`) does for a backed
monolithicFlat. The guest's refusal stays as the second line.

### The output device and the file

The host opens the create output as a sparse `BackingStore` with a
capacity of `virtual_size + 64 MiB` (`main.rs:18629`). The file grows
only as the guest writes sectors (`src/vmm/src/backing.rs:36-75`), and
writes past the capacity are refused. A fixed image needs
`fixed_file_length = fixed_data_start + virtual_size`. At 64 TiB with
1 MiB blocks the fixed data start is 520 MiB, so the hint is too small
for a fixed image at large sizes, as open question 4 predicted.
`BackingStore::open` with `sparse = false` calls `set_len(capacity)`
before anything is written. That extends the file sparsely, which is
exactly what qemu's `bdrv_truncate` to `data_start + image_size` does
(`vhdx_create_bat`). The host already links the vhdx crate and calls
`vhdx::layout` for convert's capacity (`main.rs:13961`).

### The ext4 ceiling, and what each tool leaves behind

On this host's ext4 (measured with qemu-img 10.0.13):

| Command | Result |
|---|---|
| `qemu-img create -f vhdx -o subformat=fixed x 16T` | "The image size is too large for file format 'vhdx'", exit 1, leaves a 131152-byte file |
| same at `17T` | same |
| `qemu-img create -f raw x 17T` | "The image size is too large for file format 'raw'", exit 1 |
| `instar create -f raw x 17T` (today) | "create: ftruncate failed: File too large (os error 27)", exit 1, leaves nothing |
| `qemu-img create -f vhdx -o subformat=fixed,block_size=1M x 15T` | 0.31 s, 16492808634368 bytes apparent (data start 128 MiB), 121 MiB allocated, `qemu-img check` clean |
| same with `block_state_zero=off` at `2T` | 0.08 s, 2199048421376 bytes, 17 MiB allocated |

16 TiB fails because the fixed data start pushes the file past ext4's
16 TiB - 4 KiB maximum. So 15 TiB is the largest whole-TiB fixed image
these tests can make on ext4. qemu's message names the format but
means the filesystem. qemu leaves a 128 KiB file behind when the
truncate fails. instar removes its output on every create failure,
and `docs/create.md` already records the same divergence for the
64 TiB ceiling ("qemu-img leaves an empty file").

### The guest writes one sector per call

`write_output_sector` writes exactly one output sector
(`src/core/src/virtio.rs:218`), and `create` pins `--sector-size` to
512 (`main.rs:19804-19823`). A fixed BAT is therefore one virtio
request per 64 entries. The cost was measured on a proxy:
`instar create -f qcow2 -o preallocation=metadata x 128G` streams
16 MiB of L2 tables, 32768 sectors, through the same path. It takes
0.90 s against 0.20 s for a 64M dynamic VHDX, so about 21 µs a sector.
That predicts about 0.7 s for a 2 TiB fixed VHDX at 1 MiB blocks
(2097663 entries, 32776 sectors) and about 5 s at 15 TiB (about
245000 sectors). qemu builds the same images in 0.08 s and 0.31 s,
because it `g_malloc`s the BAT and writes it in one call. Decision 8
sets the threshold that would make this a problem.

### The scratch slot

The create op's `HEADER_BUF` is one `MAX_SECTOR_SIZE` (64 KiB) buffer
at `SCRATCH_MEM_BASE`, used only by the backing-header probes
(`src/operations/create/src/main.rs:51`, `:250`, `:465`). A fixed
image has no backing file, so the probes never run, and the slot is
free for the BAT. It holds 8192 entries, the size open question 4
names. `CREATE_SCRATCH` cannot be reused while the `MetadataPlan`
borrows it.

### The tests that see this

* `KNOWN_PREALLOCATION_REFUSALS` in `tests/test_create.py:1497` holds
  `prealloc-vhdx-fixed` with the message this phase removes. Its test
  fails once instar creates the case, and says to remove the entry.
* `qemu_pins.run_case` runs a pin case's `create` command against
  whatever binary it is given, then the same binary's `check`. That is
  how `TestCreateVhdxPinLayout` already compares instar's dynamic
  images. The eleven fixed create cases (`vhdx-fixed-{16,17,64,65}M`,
  `-64M-block-1M`, each with and without `-bsz-off`, and
  `-131041M-block-1M-bsz-off`) can run the same way. The pins record
  `check_rc` 0 for all of them, including the 128 GiB one.
* The structural parser reads a 2 TiB fixed image (qemu, 1 MiB blocks,
  `block_state_zero=off`) in 0.57 s, and `instar check` reads it in
  0.2 s. So the 2 TiB geometry can run in the ordinary suite.
* `tests/test_vhdx_fixed_fixtures.py` already builds the 4608.5M
  image qemu cannot reopen, with the host `qemu-img`, and pins
  `qemu-img check` failing on it.
* No test or fuzzer expects `create -f vhdx -o subformat=fixed` to be
  refused, apart from the `KNOWN_PREALLOCATION_REFUSALS` entry.
  `scripts/differential-fuzz.py` never asks for it.

### Documentation that changes

`docs/create.md` lists vhdx as dynamic-only in its target table
(`:62`), lists only `--block-size` among vhdx's flags (`:44`), puts
`block_state_zero` nowhere and `log_size` under "Accepted but no size
effect", and carries fixed VHDX as a Future work bullet (`:479-484`).
The `--subformat` help text on `CreateArgs` names only vmdk and vhd
(`main.rs:7476-7481`). `docs/quirks.md`'s partial-last-block entry
(`:850-897`) describes qemu's images. After this phase instar writes
the same unopenable-to-qemu image at those sizes, and the entry should
say so.

### Master plan corrections made in this commit

* The phase 7 paragraph of "Sequencing rationale" no longer says the
  guest extends the file "exactly as `emit_qcow2_l2_and_extend`
  does". The host extends it, for the reasons in decision 1. The
  paragraph also records the ext4 finding (16 TiB itself fails) and
  the streaming-cost prediction.
* The Execution table and the `docs/plans/index.md` row link this
  file.

## Decisions

1. **The host extends the file, and the device is exactly that long.**
   For a fixed image the host computes
   `vhdx::layout(virtual_size, block_size, false)` and opens the
   output with `BackingStore::open(path, false,
   Some(layout.fixed_file_length), false)`. That truncates and then
   `set_len`s the file to its final length, sparse, before the guest
   starts, and makes it the device's capacity. The guest writes only
   the structure: metadata and BAT. It never writes a tail sector.

   This is the decision a reviewer is most likely to dispute, because
   the master plan says the guest extends the file the way
   `emit_qcow2_l2_and_extend` does. The host is the better place for
   three reasons:

   * **The failure that matters is the filesystem's.** On ext4,
     16 TiB and larger fixed images cannot exist. With host extension,
     `set_len` fails with `EFBIG` before a guest boots, and the host
     can say so in words. With guest extension, the tail write fails
     after the whole BAT has been streamed, and the guest reports
     "write to output device failed". The host cannot tell that from
     a real I/O error.
   * **The guest cannot make the file the wrong length.** The device
     capacity is the layout's file length, so a write past it is
     refused by `BackingStore`. A file shorter than the layout's would
     need the guest to shrink it, which it has no way to do. With
     guest extension, the file length is whatever the guest last
     wrote.
   * **It is what qemu does,** a truncate to `data_start +
     image_size`, and what instar's raw create already does on the
     host (`ftruncate`).

   The qcow2 precedent extends in the guest because its file length
   comes from the guest's planner (`compute_layout` with the
   preallocation mode), and the host only knows it from the result.
   For VHDX the host already calls the same `vhdx::layout` the guest
   plans from (convert's capacity, phase 5), so the two cannot
   disagree.

   An `EFBIG` from that `set_len` is reported as `create: a fixed VHDX
   of <virtual size> bytes needs a <file length>-byte file, which the
   filesystem holding '<filename>' cannot store`, followed by the OS
   error. Nothing is left behind (survey: qemu leaves 128 KiB).
   Any other open error keeps today's "open ... for write failed"
   text. If `vhdx::layout` refuses (an invalid block size, say), the
   host opens the output as it does for a dynamic image, and the guest
   refuses with the message it gives today. The refusal text stays in
   one place.

   As a second line, the guest checks that the device capacity in
   bytes is at least `fixed.file_length` before it writes anything,
   and fails with `ERROR_WRITE_FAILED` if not. It reports
   `fixed.file_length` as `file_size_after`, so the host's `Created:`
   line gives the real file size.

2. **The CLI takes qemu's spelling and instar's.**
   * `-o subformat=fixed` and `--subformat fixed` (and `dynamic`) are
     accepted for vhdx. `validate_create_args`' subformat match gains
     a `"vhdx"` arm admitting `""`, `"dynamic"` and `"fixed"`, with
     the vpc arm's message shape. The `-o` parser's vhdx arm sets
     `out.vhd_subformat` exactly as the vpc arm does.
   * `-o block_state_zero=<bool>` is parsed with `parse_create_o_bool`
     into a new `CreateArgs` field,
     `#[arg(skip)] block_state_zero: Option<bool>`. `None` means
     qemu's default, `on`. It has no `--flag` of its own: qemu-img has
     none, and `zeroed_grain` (`main.rs:7491`) is the precedent. A
     value `parse_create_o_bool` refuses is now refused for both
     subformats, as qemu refuses it. For a dynamic image a valid value
     is still ignored (open question 7). `log_size` stays
     accept-ignored.

3. **A fixed VHDX refuses a backing file on the host, by name.**
   `validate_vhdx_fixed_create_args`, beside
   `validate_vmdk_flat_create_args` and called from
   `validate_create_args` in the same place, refuses `-b` / `-o
   backing_file` with `create: vhdx subformat=fixed cannot have a
   backing file`. It runs before the size check, so `-b` without a
   size gets this message rather than the size one. A fixed image
   always has an explicit size. `preallocation=metadata` is refused by
   the common preallocation check that already exists, which matches
   the `vhdx-fixed-prealloc-metadata` pin. `falloc` and `full` remain
   no-ops (phase 3), and the guest is still sent `off`.

4. **The config carries the subformat in `vhd_subformat` and the state
   in a new flag bit.** `CreateConfig::FLAG_VHDX_FULLY_PRESENT: u32 =
   1 << 6` in `src/shared/src/lib.rs` (bits 0-5 are taken), with its
   host mirror `CREATE_CONFIG_FLAG_VHDX_FULLY_PRESENT` beside the
   others at `main.rs:214-220`. The host sets it only for a fixed
   vhdx with `block_state_zero` off. The bit is named for the state it
   asks for rather than as a negative, so a zeroed `flags` word means
   qemu's default.

   The guest's `vhdx_opts_from` maps `vhd_subformat` `0` to `Dynamic`
   and `1` to `Fixed`, and refuses any other value with
   `ERROR_INVALID_OPTION`. It does not quietly default as
   `vhd_opts_from` does, because a wrong subformat here is a wrong
   file length. `block_state_zero` is `flags & FLAG_VHDX_FULLY_PRESENT
   == 0`. The comment there that says the host refuses fixed is
   replaced. `CreateConfig`'s size and field offsets do not change,
   and the existing layout assertions must still pass unchanged.

5. **The guest streams the BAT before it writes the metadata.** When
   `plan_vhdx` returns `fixed: Some(f)`, the create op:

   1. checks the capacity (decision 1);
   2. fills `HEADER_BUF` (64 KiB, 8192 entries) with
      `vhdx::fill_fixed_bat(&f.layout, f.state, first_entry, buf)`, one
      slot at a time from entry 0, and writes each slot's sectors at
      `f.layout.bat_offset + first_entry * 8` through
      `write_sector_aligned`. The last slot is shortened to
      `round_up(f.bat_bytes, sector_size)` minus what was already
      written. The filler writes the padding entries in that last
      sector as zeros, and the rest of the region stays a hole, as
      qemu's does when read;
   3. runs `write_plan` for the metadata writes, as today.

   The BAT goes first so that a create interrupted part-way never
   leaves a file whose headers and region table describe a BAT that
   is only partly written. The host removes the output on any failure
   it sees, but a killed host sees none. The bytes written count
   includes the BAT bytes, and every offset uses checked arithmetic.
   The streaming lives in one function, `emit_vhdx_fixed_bat`, beside
   `emit_qcow2_l2_and_extend`, and is `#[inline(never)]`. The doc
   comment on `run_amend` in `src/operations/amend/src/main.rs:211`
   explains why: a large function inlined into `_start` for
   `x86_64-unknown-none` has miscompiled before, and the commit and
   bitmap ops follow the same rule.

   Dynamic output is unchanged byte for byte.

6. **The oracle is the pin, compared whole.** A new class,
   `TestCreateVhdxFixedPinLayout` in `tests/test_create.py`, runs every
   fixed create pin case against instar through
   `qemu_pins.run_case`. That is the ten small cases and
   `vhdx-fixed-131041M-block-1M-bsz-off`, which is a 128 GiB sparse
   file. For each case it asserts:

   * `rc` 0, no `parse_error`, and `check_rc` 0 (`instar check`);
   * `structural_diff(summary, group_summary) == []` for **every**
     version group in the pin, with no allowance at all. Unlike the
     dynamic comparison, nothing is masked: file length, every BAT
     run, `LeaveBlocksAllocated` and the region tables must match;
   * for the ten small cases, the host `qemu-img check` exits 0, and
     `qemu-img compare` against an all-zero raw image of the same
     virtual size reports them identical.

   It also checks that:

   * `vhdx-fixed-backing` and `vhdx-fixed-prealloc-metadata` are
     refused, with instar's messages, and leave no file;
   * `--subformat fixed` and `-o subformat=fixed` produce identical
     bytes. instar's VHDX GUIDs are constant (#566), so a byte
     comparison is exact. `-o block_state_zero=on` matches the default,
     and on a dynamic image `-o block_state_zero=off` matches the
     default too (open question 7, pinned so a later change is
     deliberate);
   * `-o block_state_zero=bogus` is refused for both subformats.

   `KNOWN_PREALLOCATION_REFUSALS` loses `prealloc-vhdx-fixed`, so that
   pin runs in `TestCreatePreallocation` like the others. The dict and
   its test helper stay, empty, so the next refusal has somewhere to
   go.

7. **Sizes the pin does not hold are compared with the host
   `qemu-img`.** In a second class, `TestCreateVhdxFixedLarge`:

   * 2 TiB at 1 MiB blocks, in both modes: instar's structure equals
     the host `qemu-img`'s, and `instar check` and `qemu-img check`
     are clean. The pin is 80 builds; the host is one. That is enough
     here, because phase 1 found the structure version-independent
     and phase 10 adds the cross-version cases;
   * `vhdx-fixed-4608.5M-block-1M-bsz-off`, the size qemu creates but
     cannot open (`docs/quirks.md`): instar's structure equals the
     host `qemu-img`'s, and `instar check` is clean. `qemu-img check`
     and `info` are not used, for the reason the master plan's phase 7
     paragraph gives. The test reuses the build arguments
     `tests/test_vhdx_fixed_fixtures.py` keeps for
     `PARTIAL_PAST_FIRST_CHUNK` in `RUNTIME_CREATE_IMAGES`, rather than
     spelling them again;
   * the filesystem ceiling: 16 TiB at the default block size is
     refused with decision 1's message, and no file is left. The test
     first probes whether the directory's filesystem can hold a
     `fixed_file_length`-byte sparse file (truncate and remove). It
     skips with that reason if the probe succeeds, as it would on XFS,
     so the test is meaningful on ext4 and silent elsewhere.

   No test allocates a data region. Every image is sparse.

8. **The streaming cost is measured, with a threshold set in advance.**
   Step 7b times `instar create -f vhdx -o subformat=fixed,block_size=1M`
   at 2 TiB and 15 TiB, in both modes, three runs each, with the host
   `qemu-img` alongside. It records the medians in the execution
   notes, with the binary and commit. The prediction is about 0.7 s
   and 5 s. If 15 TiB takes over 60 s, the step stops and the
   management session brings it to the operator rather than batching.
   Batching would need a multi-sector `write_output_sector`, which is
   a core and call-table change this plan does not carry. If it
   stays under 60 s, the record satisfies the success criterion ("the
   time it takes is recorded in the phase 7 plan"), and nothing is
   batched.

9. **The mutants are kept.** Each goes into
   `tools/mutate-differencing.sh`, added by the step that adds the
   code it breaks:

   * `vhdx_opts_from` ignoring `vhd_subformat` (always `Dynamic`)
     (7a, integration);
   * `block_state_zero` read as always true, ignoring the flag (7a,
     integration);
   * the BAT loop one slot short (7a, integration). The 131041M case
     needs 16 slots, so this is caught; the 64M cases need one;
   * not a case: the BAT streamed after the metadata instead of before
     it. A finished file cannot show its write order, and the script's
     only survivor kind, `rust_survivor_case`, runs a cargo test, which
     cannot reach the create op. So decision 5's order is a reviewed
     property, not a mutated one, and 7a's execution notes say so;
   * the host opening a fixed output with the dynamic capacity hint
     (sparse), which leaves the file short (7a, integration);
   * the host's backing refusal removed, so the guest's generic message
     appears instead (7a). This is a `rust_case` if a host unit test
     covers `validate_create_args`, otherwise an integration case;
   * the `EFBIG` mapping removed (7b, integration, effective only on
     ext4, where the test does not skip).

   Integration cases rebuild instar twice each, so this set is slow.
   That is the price of mutating a guest op that `cargo test` cannot
   reach (the script's own comment). `docs/testing.md`'s case list and
   `EXPECTED_CASES` move with them.

10. **Documentation in the same pull request.** `docs/create.md`:
    * the target table row (`vhdx | dynamic, fixed | Yes (dynamic
      only)`);
    * the per-target flags (`vhdx: --subformat, --block-size`);
    * the vhdx section, which gains `subformat` and `block_state_zero`
      under Honoured. It says what a fixed image is (`LeaveBlocksAllocated`,
      a BAT entry for every payload block, `ZERO` by default or
      `FULLY_PRESENT` at sequential offsets from qemu's data start, a
      sparse file of `data start + size`). It says what the backing,
      `metadata` and filesystem refusals say, that `block_state_zero`
      is parsed but has no effect on a dynamic image, and it links the
      quirks entry for the sizes qemu cannot reopen;
    * the Future work bullet for fixed VHDX goes.

    `docs/quirks.md`'s partial-last-block entry gains a paragraph:
    `instar create` writes qemu's layout at those sizes too, so its
    image is one qemu cannot open either, and `instar check` reads
    it. The `--subformat` help text names vhdx. `CHANGELOG.md` gets an
    Added entry. `docs/format-coverage.md` is phase 12's.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | **Create fixed VHDX from the CLI.** First read decisions 1 to 6 and 9, and the survey sections "The host already sends the subformat", "The guest maps a fixed backing refusal", "The output device and the file", "The guest writes one sector per call" and "The scratch slot", in `docs/plans/PLAN-bat-population-phase-07-create.md`. Also read decisions 3 to 5 of `docs/plans/PLAN-bat-population-phase-06-emitter.md` for `fixed_data_start`, `fixed_file_length`, `fill_fixed_bat` and `VhdxPlan`. **Shared:** add `CreateConfig::FLAG_VHDX_FULLY_PRESENT = 1 << 6` (`src/shared/src/lib.rs:3617-3634`) with a doc comment. **Host** (`src/vmm/src/main.rs`): add `CREATE_CONFIG_FLAG_VHDX_FULLY_PRESENT` at `:214-220`. In the `-o` parser's vhdx arms (`:15626-15647`), set `out.vhd_subformat` as the vpc arm does, and parse `block_state_zero` into a new `CreateOptionOverrides` field applied to a new `#[arg(skip)] block_state_zero: Option<bool>` on `CreateArgs` (beside `zeroed_grain`, `:7491`). `validate_create_args` (`:19794`) gains a vhdx subformat arm, and a call to a new `validate_vhdx_fixed_create_args` placed as `validate_vmdk_flat_create_args` is (decision 3). In `run_create_nonraw` (`:18545`), set the flag (decision 4), and for a fixed vhdx size and open the output from `vhdx::layout` with `sparse = false`, mapping `EFBIG` to decision 1's message (`std::io::Error::raw_os_error() == Some(libc::EFBIG)`). Update the `--subformat` help text (`:7476-7481`). Add host unit tests beside the existing `parse_create_o_options` tests (around `:20246-20290`) for the new parsing and refusals. **Guest** (`src/operations/create/src/main.rs`): `vhdx_opts_from` (`:548`) per decision 4. In the VHDX arm (`:1060-1088`), keep the plan's `fixed`, and add `#[inline(never)] unsafe fn emit_vhdx_fixed_bat` beside `emit_qcow2_l2_and_extend` (`:1154`), streaming through `HEADER_BUF` per decision 5, before `write_plan`, with the capacity check from decision 1. Report `fixed.file_length` as `file_size_after`. **Tests** (`tests/test_create.py`): `TestCreateVhdxFixedPinLayout` exactly as decision 6 says, removing `prealloc-vhdx-fixed` from `KNOWN_PREALLOCATION_REFUSALS` (`:1497`). Compare with the pin's own summaries through `helpers.vhdx_structure.structural_diff`, never through `qemu-img info`. Add 7a's decision 9 mutants and update `EXPECTED_CASES` and `docs/testing.md`'s list. Run `make test-rust`, `make check-binary-sizes` and `make fuzz-build`. Run stestr for `test_create`, `test_vhdx_structure`, `test_vhdx_fixed_fixtures`, `test_check_formats`, `test_info_safe` and `test_compare`, which read what create writes. Also do a byte comparison (`cmp`) of `instar create -f vhdx` at 64M, 1G and 131041M with `block_size=1M` against a build of `aef757d2` made from `git archive`, because `make instar` overwrites `create.bin`. Dynamic output must not change. Commit subject: `Create a fixed VHDX.` |
| 7b | high | opus | none | **Large fixed VHDX: the ceiling, the unopenable size and the cost.** First read decisions 1, 7, 8 and 9 and the survey sections "The ext4 ceiling" and "The guest writes one sector per call" in `docs/plans/PLAN-bat-population-phase-07-create.md`, and `docs/quirks.md`'s VHDX partial-last-block entry. Add `TestCreateVhdxFixedLarge` to `tests/test_create.py` exactly as decision 7 says: 2 TiB at 1 MiB in both modes against the host `qemu-img`; the 4608.5M geometry, taken from `RUNTIME_CREATE_IMAGES[PARTIAL_PAST_FIRST_CHUNK]` in `tests/test_vhdx_fixed_fixtures.py` rather than respelt, with `instar check` only; and the 16 TiB refusal behind a probe-based skip. Every image is created in a `tempfile.TemporaryDirectory` under the suite's usual temp root and is sparse. Check the test's `tools/ci/check-test-partition.sh` placement. Then measure decision 8's timings on a fresh `make instar`, three runs each, and report medians, the binary path and the commit. Stop and report, without changing code, if 15 TiB takes over 60 s. Add 7b's decision 9 mutant. Commit subject: `Test fixed VHDX at large and awkward sizes.` |
| 7c | medium | sonnet | none | **Document fixed VHDX create.** First read decision 10 and the survey section "Documentation that changes" in `docs/plans/PLAN-bat-population-phase-07-create.md`. Make exactly decision 10's edits to `docs/create.md` and `docs/quirks.md`, and add a `CHANGELOG.md` entry under `[Unreleased]` / `### Added`, in the style of the entries already there. Every claim about qemu must come from the plan's survey or pins, which cite their source; add no new qemu claims. State the 16 TiB filesystem refusal with the message 7a implemented, copied from the code, not paraphrased. Do not touch `docs/format-coverage.md`. Run `pre-commit run --all-files`. Commit subject: `Document fixed VHDX create.` |

The management session runs the steps in order. 7b's tests and timings
need 7a's binary, and 7c documents the messages 7a wrote. 7a is one
step rather than a host step and a guest step, because neither half
is testable alone. The guest is unreachable without the host, and the
host's change produces a dynamic image without the guest. It runs on
opus at high effort. The arithmetic it relies on is phase 6's, already
pinned and mutated. What is new is the wiring across the call-table
boundary, which is opus's ground.

Every brief also carries the session's standing rules:

* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned. A
  `create` crate change needs `cargo test -p create` with its feature
  flags as the Makefile runs it.
* Do not run `make test-rust` at the same time as a mutation run.
* Run the touched test modules with stestr from `tests/`, with
  `INSTAR_BINARY_PATH` pointed at the worktree's build, and report
  "Ran: N". Replay any failure alone (`--concurrency 1`) before
  attributing it.
* Run `pre-commit run --all-files` after `git add -N` on new files,
  and `tools/ci/check-test-partition.sh`.
* Keep plan, phase, step and decision references out of code and
  docs.
* Keep a mutation script under the step's scratch directory, and run
  each mutant under `timeout`.
* Every qemu behaviour a comment or doc states is cited to
  `instar-testdata/qemu-sources/7.0.0/` by function name, or was
  measured. Say which.
* Tests stay under ext4's 16 TiB file-size ceiling by staying sparse:
  no test allocates a data region.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-10-30 (AEDT).** The operator kept decision 1 as
  recommended: the host extends the file at open. They kept opus for 7a,
  and allowed the management session to push the branch and open the
  pull request once the definition of done passes. The steps run in
  order.
* **7a.** One departure, kept: `qemu_pins.run_case` checks an image with
  `check -f vhdx`, and `instar check` has no `-f` option and exits 2 on
  one. So decision 6's `check_rc == 0` could never hold for instar.
  `tests/helpers/qemu_pins.py`'s `_check_rc` now leaves `-f` out when
  the binary is named `instar`, which detects the format itself.
  qemu-img is still told the format, so the pin sweep is unchanged. The
  dynamic comparisons never asserted `check_rc`, which is why this had
  not surfaced before.

  The host's backing refusal is a `rust_case`, because a host unit test
  drives `validate_create_args` through the CLI's own resolution. The
  other four mutants are integration cases, all PASS, and the script
  holds 144 cases. Decision 9's write-order item is recorded in the
  script as reviewed, not mutated.

  The management session reproduced the following:
  * the definition of done's pin script, which prints nothing over all
    11 fixed cases in 4.9 s;
  * `instar check -f vhdx` exiting 2;
  * dynamic `create` at 64M (default block size) and at 131041M with
    1 MiB blocks, byte-identical (`cmp`) to the agent's `aef757d2`
    baseline images;
  * the 16 TiB refusal on this host's ext4, "create: a fixed VHDX of
    17592186044416 bytes needs a 17592198627328-byte file, which the
    filesystem holding 'big.vhdx' cannot store: File too large (os
    error 27)", which leaves no file;
  * a 2 TiB fixed create at 1 MiB blocks in 0.74 s.

  From the agent's run:
  * `make test-rust` passed 2790, and `make check-binary-sizes` and
    `make fuzz-build` pass;
  * `create.bin` grew from 59024 to 60224 bytes;
  * the stestr run of the six modules ran 1521: 1496 passed, 25
    skipped and 0 failed. The skips are osboxes multi-extent VMDK (14),
    VHD CHS rounding (5), ZSTD (3, including the accepted-and-ignored
    `compression_type`), raw having no check (2), and the hardcoded
    `compat=1.1` (1).
* **7b.** Decision 7's three tests, plus one more, all kept:
  * `test_file_size_limit_is_refused_like_a_filesystem_ceiling` sets
    `RLIMIT_FSIZE` in the child, as the monolithicFlat test does, so the
    `EFBIG` path is exercised on any filesystem. The 16 TiB test can
    only run where the filesystem refuses the length. The new mutant,
    `create-host-vhdx-fixed-efbig-mapping-removed`, targets this test for
    that reason. It PASSes, and the script holds 145 cases.
  * The plan was wrong on one fact. instar's create refuses a
    fractional size ("invalid memory size: '4608.5M'"), and qemu-img
    accepts one (`do_strtosz`, `util/cutils.c`). So the
    `RUNTIME_CREATE_IMAGES` size is converted to bytes before instar
    sees it. The management session reproduced it with `1.5M`: instar
    exits 1, and qemu-img writes 1572864 bytes. The finding is recorded
    under the master plan's "Bugs fixed during this work" as outside
    this plan. Filing an issue was refused by the session's permission
    check and is left to the operator.

  The stestr run of `test_create` ran 271: 262 passed, 9 skipped, 0
  failed. The 16 TiB test ran on this host's ext4 and passed. Medians of
  three, on `src/target/release/instar` at `72ed4946`, with the host
  `qemu-img` 10.0.13:

  | Size, 1 MiB blocks | `block_state_zero` | instar | qemu-img |
  |---|---|---|---|
  | 2 TiB | on | 0.769 s | 0.042 s |
  | 2 TiB | off | 0.755 s | 0.042 s |
  | 15 TiB | on | 4.454 s | 0.204 s |
  | 15 TiB | off | 4.456 s | 0.192 s |

  That is well under decision 8's 60 s, so nothing is batched. The cost
  is the guest's one-sector-per-request stream, as the survey predicted,
  and it scales with the BAT. The 15 TiB images were sparse (124 MiB
  allocated) and removed after each run.
* **7c.** As decision 10 lists. The management session ran the two
  documented refusal examples (backing file and `metadata`), which print
  exactly what `docs/create.md` shows, and rewrapped one line of the
  quirks paragraph.
* **Definition of done, 2026-10-30 (AEDT).** Checked by the management
  session on a fresh `make instar` at `f082d754`:
  * The pin script prints nothing over all 11 fixed cases.
  * The backed fixed create is refused with "cannot have a backing
    file" and leaves no file.
  * The "not yet supported" grep prints only measure's line, and
    `FLAG_VHDX_FULLY_PRESENT` appears 4 times.
  * `make check-binary-sizes` passes (`create.bin` is 60224 bytes).
    `make test-rust` (2790) and `make fuzz-build` passed in 7a's run.
    No Rust has changed since, so they were not rerun.
  * The stestr run of the six modules at 8 workers ran 1602: 1577
    passed, 25 skipped and 0 failed. The module names are substring
    filters, so the run includes tests elsewhere whose names match. The
    skips are those 7a lists. The 16 TiB test ran on ext4 and passed.
  * The dynamic `cmp` against `aef757d2` and decision 8's timings are
    above.
  * The six new mutation cases PASSed in 7a's and 7b's runs.
    `--check-patterns` reports 145 land.
  * `tools/ci/check-test-partition.sh` exits 0, the plan-reference grep
    prints nothing, and `pre-commit run --all-files` passes.

## Risks and mitigations

* **A fixed image that is structurally right and still wrong.** The
  pin comparison sees every structural field, but not data. The
  `qemu-img compare` against a zero image in decision 6 covers the read
  side for the small cases. `instar check` covers the large ones.
* **Dynamic output changes by accident.** The `cmp` against
  `aef757d2` in 7a's brief. The management session repeats it on one
  size.
* **The host and guest disagree about the layout.** Both call
  `vhdx::layout` with the same size and block size. The guest's
  capacity check catches a host that sized the device too small, and
  the pin comparison catches a file of the wrong length. The
  management session checks that the host passes the same
  `block_size` value the guest receives, and no default of its own.
* **The 16 TiB test skips everywhere CI runs.** That is acceptable,
  since it is a host-filesystem fact, but it must not skip silently.
  The skip reason names the filesystem probe, and the management
  session reports whether it ran on this host (ext4: it should).
* **The 128 GiB pin case is slow, or fills a CI disk.** It is sparse
  (17 MiB allocated for 2 TiB), and `run_case` removes it before
  returning. The management session reports its runtime.
* **`create.bin` grows.** `make check-binary-sizes` after 7a. The filler
  is the only new code linked into the op.
* **Host contention looks like a regression.** Replay any failure
  alone (`--concurrency 1`) before attributing it, as earlier phases
  did.

## Definition of done

Each item is a command, run from the worktree root with the worktree's
build, and its expected result.

* From `tests/`, this prints nothing:

  ```bash
  python3 -I - "$INSTAR_BINARY_PATH" <<'EOF'
  import sys, tempfile
  from pathlib import Path
  sys.path.insert(0, '.')
  from helpers import qemu_pins, vhdx_structure as v
  for case in sorted(qemu_pins.load_pin()['cases']):
      if not case.startswith('vhdx-fixed-') or case in ('vhdx-fixed-backing', 'vhdx-fixed-prealloc-metadata'):
          continue
      with tempfile.TemporaryDirectory() as td:
          got = qemu_pins.run_case(sys.argv[1], case, Path(td))
      if got['rc'] != 0 or got['check_rc'] != 0:
          print(case, 'rc', got['rc'], 'check_rc', got['check_rc'], got['stderr'])
          continue
      for group in qemu_pins.load_pin()['cases'][case]:
          for d in v.structural_diff(got['summary'], group['result']['summary']):
              print(case, group['versions'][0], d)
  EOF
  ```

* `"$INSTAR_BINARY_PATH" create -f vhdx -o subformat=fixed -b x.vhdx -F vhdx y.vhdx 64M`
  exits non-zero, says `cannot have a backing file`, and leaves no
  `y.vhdx`.
* `git grep -n 'subformat=fixed is not yet supported' -- src/vmm/src/main.rs`
  prints exactly one line, the measure refusal, and none in `create`'s
  parser.
* `git grep -n 'FLAG_VHDX_FULLY_PRESENT' -- src` prints at least
  three lines (shared constant, host mirror, guest read).
* `make test-rust`, `make check-binary-sizes` and `make fuzz-build`
  pass.
* The stestr run of `test_create`, `test_vhdx_structure`,
  `test_vhdx_fixed_fixtures`, `test_check_formats`, `test_info_safe`
  and `test_compare` reports "Ran: N" equal to the tests in those
  modules, with 0 failures. The execution notes list every skip by
  reason, including whether the 16 TiB test ran.
* `cmp` of `instar create -f vhdx t.vhdx 64M` from this build and from
  a build of `aef757d2` reports no difference.
* The timings of decision 8 are in the execution notes, with the
  binary and commit they were measured on.
* `bash tools/mutate-differencing.sh` with each new case name reports
  PASS (or the documented survivor outcome), and `--check-patterns`
  passes.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md scripts tools CHANGELOG.md | grep -nE '^\+.*(PLAN-|[Pp]hase [0-9]|step 7[a-c])'`
  prints nothing.
* `git add -A && pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with it.
Put decision 1 to the operator before 7a starts, because it departs
from the master plan. The alternatives are host extension at open (the
plan's recommendation), or guest extension with a tail sector as the
master plan describes, which reports a filesystem ceiling as a write
failure after streaming the BAT. Also confirm opus for 7a, and whether
this session may push and open the pull request once the definition of
done passes.
