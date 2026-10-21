# PLAN-654 phase 2: the data device is not a chain layer

Master plan: [PLAN-654-subcluster-classification.md](/components/instar/plans/PLAN-654-subcluster-classification/).
Read its Situation, Decisions D3 and D4, and phase 1's plan
([PLAN-654-subcluster-classification-phase-01-classifier.md](/components/instar/plans/PLAN-654-subcluster-classification-phase-01-classifier/))
first, in particular "Step 1a measurements". This file assumes them.

Planning effort: high. The phase changes the chain config ABI shared by
the VMM and every guest operation, and the read path every chained
input goes through.

## Scope

In:

* external data file devices stop being read as backing layers: an
  unallocated cluster in a data-file image reads its backing chain, or
  zeros, never the data file (#792);
* the VMM marks data devices in the chain config, and the guest
  validates the marking before it walks the chain;
* the two other places that treat a data device as a layer: the
  `VmdkDescriptor` extent walk and `check --chain`'s backing validation;
* qemu's two data-file host-offset refusals: a compressed entry in a
  data-file image, and a host offset that differs from the guest
  offset (#803);
* tests built the way phase 1's were, and documentation.

Out:

* moving data devices out of the chain segments (see D1);
* fuzz-target changes: `fuzz_chain_compose` keeps `data_device_idx` at
  0 (the master plan's Future work);
* map and measure composing a qcow2 chain (#641, #642): neither touches
  the device chain today (map refuses `input_device_count != 1`,
  `src/operations/map/src/main.rs:174`);
* #804 (check counts L2 tables) and #805 (subclusters under 512 bytes).

Closes #792 and #803.

## What the survey found

Surveyed on 2026-10-21 against develop `d08841e3` (phase 1 merged).

1. **#792 still reproduces after phase 1.** Six fixtures, under
   `convert` and `compare`, are `EXPECTED_FAILURES` citing #792 in
   `tests/test_qcow2_subclusters.py:51-62`. Phase 1's CI passed with
   them as expected failures, and testtools reports an unexpected
   success as a failure, so they still fail. Phase 1's step 1c table
   lists them.
2. **Every layer advance in the reader steps into the data device.**
   `read_chain_virtual_cluster` (`src/crates/qcow2/src/lib.rs:15162`)
   moves to the next layer in two ways: the device loop's `continue`
   (the qcow2 `Unallocated` arm at `:15225`, and the VMDK, VHD and VHDX
   arms), and a recursive descent sized by `devices_behind`
   (`:14963`), for qcow2 subcluster runs (`:15394`) and the VHD and
   VHDX sector bitmaps. Both land on the next device in the segment,
   and the VMM puts the data device there. The raw arm (`:16881`) then
   reads the data file at the *virtual* offset and returns.
3. **The ABI documents the fall-through as intended.** `ChainSegment`'s
   doc comment (`src/shared/src/lib.rs:4860-4866`) says a data device
   "counts as a device behind the image above it, which is what the
   reader's own arithmetic already assumes". `docs/chain-config.md:63-65`
   says the same thing.
4. **The VMM gives the guest no way to tell a data device from a
   layer.** `write_chain_device_entries` (`src/vmm/src/main.rs:3584-3640`)
   writes a data device as format `Raw`, flags 0 and
   `data_device_idx` 0, immediately after its owner, and sets the
   owner's `data_device_idx` to its own index plus one.
   `open_chain_devices` (`:3648`) and `open_chain_devices_rw` (`:3740`)
   open devices in the same order.
5. **The crate tests never used the VMM's layout.** Phase 1's harness
   puts the data device at index 2 with `chain_len` 1 or 2, outside the
   chain (`lib.rs:6749-6752`, `sc_read` at `:6884-6932`). That is why
   #792 has no crate test: the harness could not reproduce it.
6. **`VmdkDescriptor` walks past its own extents.** Its arm
   (`lib.rs:16839-16878`) treats every device from `data_device_idx`
   onward as an extent until it meets a `virtual_size` of 0 or the end
   of the array. If a layer follows the extents, that layer counts as
   another extent. Step 2a measured that qemu-written images reach
   this: under `compare`, the device after a flat descriptor's extent
   is the other input's top image, so a qcow2 overlay larger than the
   descriptor it sits on compares as different in one argument order
   and identical in the other.
7. **`check --chain` validates a data device as a backing image.**
   `validate_chain` (`src/operations/check/src/main.rs:3398-3400`)
   iterates `1..device_count` and runs format and size cross-checks
   meant for backing images on each device. The VMM sets the flag for
   `instar check --chain` (`src/vmm/src/main.rs:12377`). Step 2a
   measured that a data device reports an error only when its contents
   look like another format, or when it is empty. The chain error that
   every data-file fixture with a raw backing file shows comes from
   the raw backing file. Discovery records a raw file as `Unknown`, so
   every raw backing file gets that error, with or without a data file
   (#808, filed from step 2a).
8. **The chain config version is defined twice.** The guest uses
   `ChainConfig::VERSION` (`src/shared/src/lib.rs:5049`) and the VMM
   has its own `CHAIN_CONFIG_VERSION` (`src/vmm/src/main.rs:336`), both
   3.
9. **Nothing else reads `data_device_idx`.** The reader, `check`'s data
   file size lookup (`check/src/main.rs:294-310`, which reads only
   device 0's data device and is unaffected), and `fuzz_chain_compose`,
   which leaves it 0 on purpose. `compare` packs two chains with each
   data device inside its own segment (`src/vmm/src/main.rs:13071-13256`).
   D1 keeps that layout valid.
10. **#803 is ready to take.** Its fixtures are built by
    `HOST_OFFSET_REFUSAL_BUILDERS` (`tests/helpers/qcow2_subclusters.py:641`)
    and are kept out of the test module (`tests/test_qcow2_subclusters.py:28-32`).
    `docs/quirks.md:4904-4912` records both shapes as open divergences.

The master plan's section was accurate except on one point: its Future
work asked phase 2 to decide whether qemu's data-file offset check
belongs here, and D5 decides it does. The planning commit updates that
entry and the master plan's "Bugs fixed" paragraph to match, so a later
step does not need to.

## Decisions

**D1. Data devices are marked by role, not moved out of the chain.**
The VMM sets a new bit, `ChainDeviceInfo::FLAG_DATA_DEVICE` (bit 31), on
every data device it writes. The info operation never sets it. The
chain config version goes to 4. Moving data devices out of the segments
was considered and rejected: the segments would no longer tile the
device array, so `ChainSegment::covers`, every operation's `chain_len`
(each passes the host's device count today), `compare`'s packing and
the VMM's slot numbering would all change, while the reader already
reaches data devices only through `data_device_idx`. Marking the
devices fixes the defect and leaves the array's shape as it is. This is
the decision a reviewer is most likely to dispute.

**D2. The reader skips a data device at the top of its device loop, and
nowhere else.** Every layer advance passes through that loop: a
`continue` reaches the next iteration, and a descent re-enters the
reader at the next device. One check there covers every format arm.
`devices_behind` still counts data devices. That is harmless, because a
descent into a run of nothing but data devices skips them all and
reaches the zero fill at the bottom of the reader, which is the right
answer at the bottom of a chain. The bit says what a device is, so this
check is about the device's role, not an ad hoc value test of the kind
the master plan criticises.

**D3. `init_chain_states` validates the marking before any read.** A
segment's first device is never a data device. Every non-zero
`data_device_idx` names the marked device in the slot immediately after
its owner, which is where the VMM always puts it. Every marked device has exactly one owner: a qcow2 owns one,
and a `VmdkDescriptor` owns the contiguous run of marked devices that
starts at its `data_device_idx`, which is also how its extent walk now
ends (survey item 6). A config that breaks these rules is refused with
a plain failure, as a bad segmentation is. It means the host and the
guest disagree about the config, not that the image is corrupt.

**D4. `check --chain` skips data devices** in `validate_chain`. A data
file has no header to cross-check, and it is not a backing image. This
removes only the two errors a data device can raise itself (survey item
7). The raw-backing error is #808, so tests of D4 use a qcow2 backing
file.

**D5. #803 joins this phase.** The master plan's Future work left the
decision to phase 2. Both refusals sit at the read site this phase is
already changing (`data_read_device` and the `Normal` reads). The
offset-mismatch shape is a read of the wrong bytes that exits 0, and
its fixtures already exist. The refusal follows the
`InvalidL2EntryRefusal` pattern (`src/shared/src/lib.rs:3190-3226`, VMM
renderer at `src/vmm/src/main.rs:810-860`). Its text uses qemu's own
wording for each shape, taken from the v10.0.0 source rather than from
memory. map, check and measure follow qemu on the two fixtures as the
test module requires. Where one cannot in this phase, it gets an
`EXPECTED_FAILURES` entry that names a filed issue.

**D6. The crate harness uses the VMM's layout.** It places the data
device at the owner's index plus one, inside the segment and marked,
with any backing device after it. The layout with the data device
outside the chain is not one the VMM writes, so tests that use it prove
nothing about real reads. That is how #792 escaped.

**D7. One version constant.** The VMM uses `shared::ChainConfig::VERSION`
instead of its own copy, so a future bump cannot reach only one side.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | **Measure and build the fixtures; no Rust changes.** Build instar from this branch with `make instar` (Docker only; `rm -rf src/target/release` first if `make test-rust` ran in this tree, because the two share that directory and a GLIBC mismatch follows). Use host qemu-img/qemu-io 10.0.13. Extend `tests/helpers/qcow2_subclusters.py` with builders for: (1) #792's two measured cases from the issue body (data file plus raw backing file, cluster size 64K, classic and extended L2; data file with `data_file_raw=off`, no backing, and bytes written straight into the data file at an offset the qcow2 does not map); (2) a three-layer chain whose middle layer is a qcow2 with its own data file, so a data device sits between two layers; (3) one `compare` case whose two inputs are both data-file images, so the device array holds two segments that each contain a data device. Follow the helper's existing conventions: types derived by reading the image back, owners per subcluster, `expected_raw`, and absolute data and backing paths. Then measure and record a table in this file under "Step 2a measurements", for every #792 fixture (the six already in `EXPECTED_FAILURES` and the new ones): exit codes and byte agreement for `convert -O raw` and `compare` on instar against qemu. Also measure, and record: (a) `instar check --chain` against `qemu-img check` on `data_file_partial_backing` and `mixed_classic_backing_datafile`: does instar report a chain error for the data device? (b) whether instar accepts a VMDK monolithicFlat descriptor that has a parent link (`parentFileNameHint`), or a qcow2 overlay whose backing file is a monolithicFlat VMDK, and if so whether the extent walk in `src/crates/qcow2/src/lib.rs:16839-16878` reads the following layer as an extent; (c) what instar's `map`, `check` and `measure` report on the two `HOST_OFFSET_REFUSAL_BUILDERS` fixtures, against qemu's results in phase 1's table. Do not add the new fixtures to `BUILDERS` if that would make the existing test module fail; put them in a separate dict that step 2d merges. Run `pre-commit run --all-files` after `git add` on new files. Leave the work uncommitted and report the table. |
| 2b | xhigh | opus | worktree | **Mark data devices and stop the reader walking into them.** Read this plan's D1-D4, D6 and D7 first. (1) `src/shared/src/lib.rs`: add `pub const FLAG_DATA_DEVICE: u32 = 1 << 31` on `ChainDeviceInfo` with a doc comment saying that the VMM sets it and the info operation never does, add `is_data_device()`, bump `ChainConfig::VERSION` to 4, and rewrite `ChainSegment`'s doc comment (`:4860-4866`): a data device stays inside its owner's segment but is not a layer, and nothing is behind it. Update the shared tests that pin the version (`:7323-7327`). (2) `src/vmm/src/main.rs`: `write_chain_device_entries` (`:3584-3640`) sets the bit on each data device it writes. Delete the VMM's `CHAIN_CONFIG_VERSION` (`:336`) and use `shared::ChainConfig::VERSION`; find every use. (3) `src/crates/qcow2/src/lib.rs`: at the top of `read_chain_virtual_cluster`'s device loop (`:15180`), `continue` past a device whose `is_data_device()` is true, before its capacity or state is read. Make no other change to layer arithmetic: list every `continue`, every `devices_behind` call and every `chain_start + dev_offset + 1` in the reader, plus `parent_in_chain` (`:15105`), and state in the report why each one is right with the skip in place. Bound the `VmdkDescriptor` extent walk (`:16839-16878`) to the run of marked devices starting at its `data_device_idx`. Validate per D3 in `init_chain_states` (`:17131`), beside the existing `segmentation_covers` refusal and in the same style, with a `debug_print` and a plain `false`. (4) `src/operations/check/src/main.rs`: `validate_chain` (`:3398`) skips marked devices. (5) Crate tests: move phase 1's `ScChain` harness (`lib.rs:6749` onward) to the VMM's layout: qcow2 at 0, data device at 1 (marked), backing at 2, `single_chain` covering all three, `data_device_idx` 1. Keep every existing `sc_*` test passing on the new layout. Add tests that fail on develop: an unallocated cluster, classic and extended, in a data-file image with a backing device reads the backing byte; the same without a backing device reads zeros, not data-file bytes; an unallocated subcluster run descends past the data device. Add a crate test for the extent-walk bound in which the device after a flat descriptor's extent belongs to another segment, which is `compare`'s layout (step 2a measured that shape). Add `init_chain_states` tests for each D3 refusal, beside `init_chain_states_refuses_a_config_whose_segments_do_not_cover_the_devices` (`:13991`). (6) Mutation harness `tools/mutate-differencing.sh`: add cases that drop the loop-head skip, drop each D3 check that has a test, and drop the extent-walk bound. Each must be caught by a named test. Update `EXPECTED_CASES` (`:685`) and the case count in `docs/testing.md` ("There are **N cases**", `:2182`), and run `--check-patterns` and the new cases. Run `cargo test -p qcow2` with the guest features and `-p shared` through the Make targets, then `make test-rust`, `make instar`, `make check-binary-sizes` and `make lint`. Keep the crate `no_std`. Leave the work uncommitted; report changes by file, test counts, mutation results and the layer-advance audit, in 600 words or fewer. |
| 2c | high | opus | none | **qemu's data-file host-offset refusals (#803).** Read issue #803 and this plan's D5. Fetch qemu v10.0.0 `block/qcow2-cluster.c` and read `qcow2_get_host_offset` (around `:660-710`) for the two checks and their exact messages. Do not work from memory. In the chain reader, where a data-file `Normal` cluster is read (the `Standard` arm and `Normal` runs in the `StandardSubclusters` arm, both through `data_read_device`), refuse when the host cluster offset differs from the guest cluster offset. Wherever the reader meets a compressed entry and `has_data_file()` is true, refuse instead of inflating from the metadata device. Add `shared::DataFileEntryRefusal` following `InvalidL2EntryRefusal` (`src/shared/src/lib.rs:3190-3226`): a distinct `send_error` marker, the virtual offset, and a reason code for the two shapes. Add its renderer in the VMM next to `invalid_l2_entry_error` (`src/vmm/src/main.rs:844`) in the form `<op>: <qemu's wording> at virtual offset N (0x…)`. Check that the renderer is reached by every operation that renders the invalid-entry refusal today (#665 is the cautionary case: a raiser with no renderer). Apply the same two rules in map, check and measure where step 2a found that they disagree with qemu. Move the two fixtures from `HOST_OFFSET_REFUSAL_BUILDERS` into `BUILDERS` and delete the dict and its mentions in the test module's docstring. Crate tests for both shapes, narrow and wide sector paths, on the step 2b harness. Mutation cases for both checks, with `EXPECTED_CASES` and `docs/testing.md` updated. Gates as in step 2b. Leave the work uncommitted; 400 words. |
| 2d | medium | opus | none | **Integration tests.** In `tests/test_qcow2_subclusters.py`: remove `_ISSUE_792` and its `EXPECTED_FAILURES` loop, merge step 2a's new fixtures into the module (`data_file_mid_chain` gets `convert` and `compare` entries in `EXPECTED_FAILURES` citing #806, because the VMM discovers no data file below the top image). Add `check --chain` tests over a **qcow2** backing file, because a raw one hits #808: a data-file image, one whose data file starts with a qcow2 header in the guest's bytes, and one with a zero-length data file; each must report no chain error, as qemu-img check does. Add a builder and a test for a 1M qcow2 overlay over a 512K monolithicFlat VMDK built with `qemu-img create -o subformat=monolithicFlat`: `compare` against qemu's raw output must say identical in both argument orders. The two #803 fixtures are now in `BUILDERS`. Assert that every refusing operation names the step 2c message and exits non-zero, and that qemu-img also fails. Keep the module's skip behaviour and conventions (`_require_qemu_img`, `tests/base.py:473`). Run `tools/ci/check-test-partition.py`. Run the module plus `test_convert`, `test_compare`, `test_check_chain`, `test_check_formats`, `test_map`, `test_measure` and `test_bench` from `tests/` with `.venv/bin/stestr run <regex>`. All must pass with 0 failures. Read "Ran: N", because a crashed stestr worker exits 0. `test_convert`'s re-encode cases fail under host load with "Content mismatch at offset 0", so replay any failure with `--serial` or `--select` before reporting it. Leave the work uncommitted; 300 words. |
| 2e | low | sonnet | none | **Documentation.** `docs/chain-config.md`: replace the paragraph at `:63-65` with one that says a data device sits in its owner's segment immediately after it, is marked with `FLAG_DATA_DEVICE`, and is never a layer. Add the flag to the "Feature Flags" table as VMM-set. Change the version in the header table to 4. `docs/quirks.md`: rewrite the #803 paragraph (`:4904-4912`) to say that instar now refuses both shapes, with the message class. `CHANGELOG.md`, unreleased section: under **Fixed**, unallocated clusters in data-file images read the data file instead of the backing file or zeros (#792), and data-file entries qemu refuses are refused (#803). Under **Changed**, the chain config is version 4. Search `docs/` for any other claim that a data device is behind its owner and correct it. Do not touch `AGENTS.md`, `ARCHITECTURE.md` or `README.md`: the system's shape does not change. Run `pre-commit run --all-files`. Leave the work uncommitted. |

Steps run in order. 2c and 2d build on 2b's harness and flag, and 2d's
expectations come from 2a's table. Commits: one per step, each passing
`pre-commit run --all-files` (`git add` new files first; pre-commit
cannot see untracked files). Step 2b runs in a worktree because it
changes the ABI on both sides; review its diff against D1-D4 before
merging it back.

## Risks and mitigations

* **A layer advance that does not pass through the loop head.** If a
  path reaches the next device without re-entering the loop, it reads
  the data device. Mitigation: step 2b's report audits every
  advance, and the management session re-checks it with
  `grep -n "devices_behind\|dev_offset + 1\|continue;" src/crates/qcow2/src/lib.rs`
  over the reader before committing.
* **Version skew between the VMM and an operation binary.** Mitigation:
  the bump to 4 makes `is_valid` refuse a mismatched pair, and D7
  removes the second constant. The shared version tests pin it.
* **`check --chain` output changes on existing images.** Skipping data
  devices can only remove reports. Mitigation: step 2d runs
  `test_check_chain` and `test_check_formats`, and any expectation
  that changes is justified against `qemu-img check` in the commit.
* **D3's validation refuses a layout the VMM really writes**, such as a
  VMDK descriptor with several extents, or `compare` with two
  data-file inputs. Mitigation: step 2a's `compare` fixture and the
  existing VMDK extent tests run through the real VMM in step 2d, and
  step 2b has a crate test for each accepted shape as well as each
  refused one.
* **qemu's wording quoted from memory.** Mitigation: step 2c fetches
  the source, and the management session checks the quoted lines
  against it.

## Definition of done

* `grep -n "_ISSUE_792\|HOST_OFFSET_REFUSAL" tests/` returns nothing.
  Every former #792 expected failure passes, and both #803 fixtures are
  in `BUILDERS` and refused by name.
* Step 2a's new fixtures (data file plus backing, `data_file_raw=off`
  stale bytes, two data-file inputs to `compare`) agree with qemu-img
  byte for byte under `convert` and `compare`. The exception is
  `data_file_mid_chain`, which carries an expected failure citing #806:
  the VMM discovers no data file below the top image, so that read
  fails closed.
* `compare` of a qcow2 overlay over a smaller monolithicFlat VMDK
  against qemu's raw output says identical in both argument orders.
* `grep -rn "counts as a device behind" src/shared docs --exclude-dir=plans`
  returns nothing.
* `grep -n "CHAIN_CONFIG_VERSION" src/vmm/src/main.rs` returns nothing.
* Each mutation case added in steps 2b and 2c is caught by a named
  test. `tools/mutate-differencing.sh --check-patterns` passes, and
  `EXPECTED_CASES` equals the count in `docs/testing.md`.
* `make instar`, `make lint`, `make check-binary-sizes` and
  `make test-rust` pass, and the integration modules named in step 2d
  pass with 0 failures and the expected "Ran: N".

## Back brief

Before step 2a, confirm with the operator:

1. D1: mark data devices with a flag rather than moving them out of
   the segments.
2. D5: take #803 into this phase.
3. Whether this session may push and open the pull request once the
   definition of done passes.

## Step 2a measurements

Measured on 2026-10-21 with instar built by `make instar` from this
branch at `97d61334` (no Rust changes), against qemu-img and qemu-io
10.0.13 (Debian `1:10.0.13+ds-0+deb13u1`). Nothing was checked on another
qemu version; read every qemu claim as "qemu 10.0.13 does this".

### The new fixtures

`tests/helpers/qcow2_subclusters.py` gains two dicts, kept out of
`BUILDERS` because every one of their convert and compare cases fails
today. Step 2d merges them.

* `DATA_DEVICE_BUILDERS` (name to `SubclusterFixture`, the existing
  conventions: types read back from the image, owners per subcluster,
  `expected_raw`, absolute paths):
  * `issue_792_backing_classic`, `issue_792_backing_ext`: the issue's
    first case. Data file plus raw backing, 64K clusters, cluster 0
    written whole and 4K written at cluster 2.
  * `issue_792_stale_data_file_classic`, `issue_792_stale_data_file_ext`:
    the issue's second case. `data_file_raw=off`, no backing, 4K written
    at cluster 0, then `0x77` written into the data file at cluster 5
    (and, for extended L2, under cluster 0's unallocated subclusters
    4-31), where the qcow2 maps nothing.
  * `data_file_mid_chain`: a classic qcow2 (no data file) over
    `data_file_mid_chain_middle` (extended L2, its own data file with
    unmapped `0x77` bytes at cluster 3) over a raw file.
* `DATA_DEVICE_COMPARE_PAIRS` (name to `ComparePairFixture`, with `left`,
  `right` and `identical`): `data_file_pair`, an extended-L2 and a
  classic-L2 data-file image over raw backing files that read the same
  disk; the right one's data file holds unmapped bytes at cluster 5.

`build_fixture` gained `backing_fixture` (a built fixture as the
backing file), `data_file_raw` and `data_file_ops` (raw writes into the
data file). `build_all` gained `include_data_device`. Existing fixtures
are built exactly as before. qemu-img's `convert -O raw` equals
`expected_raw` on all seven new images, and `qemu-img compare` of the
pair says identical.

### convert and compare (#792)

Convert: instar exit code, and whether its bytes equal qemu-img's
(qemu exits 0 and equals `expected_raw` on every row). Compare: `instar
compare <fixture> <qemu-img's raw output>`; `qemu-img compare` of the
same pair exits 0 on every row.

| Fixture | instar convert | Bytes differing / first | instar compare |
|---|---|---|---|
| `mixed_classic_backing_datafile` | 0 / differ | 131072 / 196608 (`00`, qemu `82`) | 1, mismatch at 196608 |
| `mixed_ext_backing_datafile` | 0 / differ | 313344 / 4096 | 1, mismatch at 4096 |
| `data_file_partial_backing` | 0 / differ | 520192 / 4096 | 1, mismatch at 4096 |
| `data_file_partial_32k` | 0 / differ | 261120 / 1024 | 1, mismatch at 1024 |
| `data_file_classic_no_copied_backing` | 0 / differ | 462848 / 0 (`ab` from the data file, qemu `01`) | 1, mismatch at 0 |
| `data_file_classic_no_copied` | 0 / differ | 4096 / 0 (`ab`, qemu `00`) | 1, mismatch at 0 |
| `issue_792_backing_classic` | 0 / differ | 393216 / 65536 (`00`, qemu `81`) | 1, mismatch at 65536 |
| `issue_792_backing_ext` | 0 / differ | 454656 / 65536 | 1, mismatch at 65536 |
| `issue_792_stale_data_file_classic` | 0 / differ | 65536 / 327680 (`77`, qemu `00`) | 1, mismatch at 327680 |
| `issue_792_stale_data_file_ext` | 0 / differ | 122880 / 8192 (`77`, qemu `00`) | 1, mismatch at 8192 |
| `data_file_mid_chain` | **1**, `convert operation failed` | no output | 1, `Content mismatch at offset 0!` |
| `data_file_pair` (left vs right) | 0 / differ on both sides | left 520192, right 458752 | 1, mismatch at 4096; qemu-img: identical |

Run through the test module's own checks (a scratch module that swaps
`BUILDERS`), the five new fixtures pass `map`, `check` and `measure` and
fail only `convert` and `compare`.

**`data_file_mid_chain` is not #792.** The VMM discovers a data file for
the top image only (`src/vmm/src/main.rs:3300-3305`, "External data files
on backing images are not currently supported"). The device array is
top, middle, base (three devices, `-v` output), with no data device at
all, so the middle layer's `data_device_idx` is 0, `data_read_device`
returns `None` and the read fails. qemu reads the chain. compare also
renders that read failure as a content mismatch, exit 1, rather than an
error.

### (a) `check --chain`

| Image | instar `check --chain` | instar `check` | qemu-img check |
|---|---|---|---|
| `data_file_partial_backing` | 2, `1 backing chain error(s)`, `chain-errors: 1` | 0 clean | 0 clean |
| `mixed_classic_backing_datafile` | 2, `1 backing chain error(s)`, `chain-errors: 1` | 0 clean | 0 clean |
| `mixed_classic_backing_nodatafile` (no data file) | 2, 1 chain error | — | 0 clean |
| `data_file_partial`, `data_file_classic` (no backing) | 0 clean | — | 0 clean |
| `mixed_ext_backing_nodatafile_over_16k_qcow2` (qcow2 backing) | 0 clean | — | 0 clean |
| data file whose guest content starts with a qcow2 header, raw backing | 2, 2 chain errors | — | 0 clean |
| zero-length data file, nothing allocated, raw backing | 2, 2 chain errors | — | 0 clean |

`-v` shows which device reports. On the two named fixtures the single
error is `check: chain format mismatch` for the **raw backing file**
(device 2), not the data device: the info operation names a raw file
`unknown`, so the chain records `Unknown`, and `validate_chain` detects
`Raw`. Every raw backing file gets it, with or without a data file. The
data device itself passes, because the VMM writes it as `Raw` and its
bytes detect as raw. It reports only when its contents detect as
something else (a guest disk holding a qcow2: a second format mismatch)
or when it is empty (`chain device not available`). D4 removes those
two; it does not make either named fixture clean.

### (b) The `VmdkDescriptor` extent walk

`qemu-img create -o subformat=monolithicFlat -b …` refuses with "Flat
image can't have backing file". A hand-edited flat descriptor with
`parentFileNameHint` naming a monolithicSparse VMDK opens in qemu, which
forces the parent's format to `vmdk` (`qemu-img info` shows "backing
file format: vmdk", and a raw parent fails with "invalid VMDK image
descriptor"); in every row below qemu's output contains none of the
parent's bytes. instar's VMM follows the hint
(`src/vmm/src/main.rs:3245-3248`), so the parent becomes the next device
after the extent.

| Image | qemu-img convert | instar convert | instar compare vs qemu's raw |
|---|---|---|---|
| flat descriptor, no parent | 0 | 0, equal | 0 |
| flat descriptor with a parent hint | 0 | 0, equal (reads never pass the extents) | 0 |
| 1M qcow2 over a 512K flat descriptor | 0, `00` past 512K | 0, equal | **1, mismatch at 524288** |
| 1M qcow2 over a 512K flat descriptor with a 1M sparse parent | 0, `00` past 512K | **0, differ**: `4b` at 524288 (the parent's `KDMV` magic), then the parent file's raw bytes | 1, mismatch at 524288 |
| 1M data-file qcow2 over a 512K flat descriptor | 0, VMDK bytes `33` | 0, differ: `00` from 65536 (#792: the data device is read instead of the descriptor) | 1 |

So the walk does read the following device as an extent, exit 0, once a
read lands past the descriptor's extents, which happens when the
overlay is larger than the descriptor. It needs no hand-editing: under
`compare` the following device is the other input's first device. `instar
compare overlay.qcow2 qemu.raw` reports a mismatch at 524288 although
`instar convert` of the overlay equals qemu's output, and with the
inputs swapped it says identical. qemu-img compare says identical both
ways.

### (c) The two host-offset refusal fixtures

| Fixture | Op | instar | qemu-img (phase 1's table, re-measured) |
|---|---|---|---|
| `data_file_compressed` | map | 0, cluster 0 `data: true, offset: 196608` | 1, EIO: `Compressed cluster entry found in image with external data file` |
| | check | 0 clean | 2, `ERROR compressed cluster 0 with data file`, 1 corruption |
| | measure | 0, required 393216 | 1, EIO |
| | convert | 1, generic `convert operation failed` | 1, EIO |
| `data_file_offset_mismatch` | map | 0, cluster 1 `data: true, offset: 131072` | 1, EIO: `External data file host cluster offset 0x20000 does not match guest cluster offset: 0x10000` |
| | check | 0 clean | 0 clean (agrees) |
| | measure | 0, required 393216 | 1, EIO |
| | convert | 0, all zeros (reads the data file at host 0x20000) | 1, EIO |

Step 2c therefore has map and measure to change for both shapes, check
for the compressed shape only (qemu's check does not apply the offset
rule), and convert for both.

## Execution notes

* 2026-10-21 (AEDT). Back brief answered: D1 (a flag bit) and D5
  (take #803) as written, and this session may push and open the pull
  request once the definition of done passes. The steps run in order
  as planned.
* Step 2a contradicted the plan in three places. The management session
  reproduced each one before correcting the plan where the fact was
  stated:
  * survey item 7: the `check --chain` error on the named fixtures
    comes from the raw backing file, not the data device. Filed as
    #808; D4's tests now use a qcow2 backing file;
  * the mid-chain fixture: the VMM discovers a data file for the top
    image only, so a backing layer with a data file has no data device
    and the read fails closed. Filed as #806, and it becomes an
    expected failure, not a definition-of-done item. The same run
    showed `compare` reporting a read failure as `Content mismatch at
    offset 0!`, filed as #807;
  * survey item 6: the extent walk is reachable through `compare` on
    qemu-written images. D3's bound already fixes it; steps 2b and 2d
    gained tests for it.

  None of the three changes D1-D3 or D5, so the phase continued
  without stopping.
* Step 2b, run in a separate worktree as planned, made D3 stricter than
  written: a `data_device_idx` must name the slot immediately after its
  owner, not just any later slot in the segment. The VMM only ever writes
  it that way, and the stricter rule keeps the VHD, VHDX and qcow1
  bottom-of-chain arithmetic exact. D3 above is corrected to match. The
  rule lives in `ChainConfig::data_devices_are_owned`, beside
  `segmentation_covers`. Two extras were kept: the VMM masks the
  data-device bit out of the flags an info operation reports, so an
  image cannot mark itself, and a VMM unit test with two mutation
  cases checks the host's output against the guest's rule. The
  management session re-ran two of the 12 new mutation cases (both
  caught), and converted all ten #792 fixtures with the step's binary:
  all match qemu, and `data_file_mid_chain` fails closed as #806
  predicts. The definition-of-done grep now excludes `docs/plans`,
  because this plan quotes the phrase it greps for.
* Step 2b's fix turned the twelve #792 entries in
  `EXPECTED_FAILURES` into unexpected successes, which fail the run, so
  the step 2b commit as first made did not pass its own integration
  module. The management session folded the removal of `_ISSUE_792`
  into that commit before anything was pushed. Step 2d's first item is
  therefore already done.
* Step 2c widened the host-offset rule to match qemu. `qcow2_get_host_offset`
  (`block/qcow2-cluster.c:682-705` at v10.0.0) applies it to `ZERO_ALLOC`
  and `UNALLOCATED_ALLOC` entries too, not only `NORMAL`, so instar now
  refuses those as well. The crate harness's data-file host clusters
  had moved off their guest offsets, a shape qemu refuses, so they now
  sit at the guest offsets, and nine expectations moved with them.
  instar's message leaves out the host offset, which qemu prints,
  because the refusal carries only the virtual offset. qemu's separate
  unaligned-offset rule (`:687-695`) applies to every image, not only
  data-file ones. instar has no named refusal for it, but the
  management session measured that it fails closed: a 512-byte
  misaligned entry makes `convert` exit 1 with the generic message, as
  qemu fails with `EIO`. check's compressed-with-data-file rule has no
  mutation case, because it lives in a guest operation; the integration
  module's check test covers it.
* The definition-of-done grep for the stale ABI claim also matched
  "covered by that chain's segment", which stays true under D1, because
  a data device does stay inside its owner's segment. The item now
  greps only for the false half, "counts as a device behind".
* 2026-10-21 (AEDT), definition of done on HEAD `9a9ae6b6`, before the
  push. The three greps are empty, `EXPECTED_CASES` and
  `docs/testing.md` both say 126, and `--check-patterns` reports 126
  land and 0 drifted. `make lint`, `make test-rust` (2680 passed, 0
  failed), `make instar` and `make check-binary-sizes` (convert 366KB
  of 768KB) pass. The subcluster module plus test_convert,
  test_compare, test_check_chain, test_check_formats, test_map,
  test_measure, test_bench and test_dd ran 1805 tests: 1243 passed,
  549 skipped, 6 expected failures, 0 unexpected successes and 7
  failures. All seven were heavy test_convert cases (four VHD, one
  streamOptimized VMDK, two manifest images) with no qcow2 data file
  in them, and all seven passed on a `--serial` replay. The step 2a
  fixtures agree with qemu-img except `data_file_mid_chain` (#806), as
  the definition of done allows.

