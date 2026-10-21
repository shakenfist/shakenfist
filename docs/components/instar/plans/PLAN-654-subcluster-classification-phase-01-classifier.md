# PLAN-654 phase 1: one subcluster classifier

Master plan: [PLAN-654-subcluster-classification.md](/components/instar/plans/PLAN-654-subcluster-classification/).
Read its Situation, Decisions D1-D6 and Open questions first. This
file assumes them.

Planning effort: high. The phase changes the read path that every
qcow2 input goes through, and it reverses a documented policy (D2).

## Scope

In:

* a pure, `no_std` classifier in `src/crates/qcow2` implementing
  qemu's seven subcluster types, including the data-file rule (D1, D3);
* `cluster_lookup` and the chain reader's narrow, wide and `Standard`
  arms rebuilt on it, with `INVALID` refused (D2), and the silent
  bitmap-read fallback removed (open question 3);
* map, measure and check moved onto the same classifier (D5);
* tests by construction (D6), and documentation.

Out: the data device's place in the chain (#792, phase 2), write
paths (unless step 1a finds one that interprets host offset 0 on an
entry it did not validate itself), and fuzz-target changes (see the
master plan's Future work).

Closes #653 and #654. Fixes rows C and D of the master plan's
measurements for read, map, measure and check.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | **Pin qemu's behaviour and build the fixtures; no Rust changes.** (1) Write `tests/helpers/qcow2_subclusters.py`: builders that return paths to small qcow2 images of each shape in the master plan's table and of each of qemu's seven subcluster types, crossed with {no backing, raw backing filled with a known non-zero byte} × {no data file, data file} × {extended L2 off, on} where the combination is meaningful. Build well-formed shapes with `qemu-img create` and `qemu-io -c 'write -P …' / 'write -z …'`, never by hand. Build `INVALID` shapes by patching the L2 entry or bitmap of a qemu-built image with `struct`. Locate the L2 table from the header the way the master plan's measurement did (L1 offset at header byte 40, L2 offset = L1 entry & `0x00fffffffffffe00`, extended entries 16 bytes each). Each builder also returns the expected *ownership*: for each subcluster, which device its bytes should come from (child, data file, backing, zero) or `refuse`. That is what step 1e asserts against. (2) For every fixture, run `qemu-img convert -O raw`, `qemu-img map --output=json`, `qemu-img check` and `qemu-img measure -O qcow2`, and record the results as a table in this file under "Step 1a measurements": exit code, whether the converted bytes match the expected ownership, and qemu's map entries. Settle by measurement, not by reading: does `qemu-img check` report an entry with a non-zero host offset and a zero bitmap (instar's `HostWithoutRef`, `src/crates/qcow2/src/lib.rs:1420-1424`)? Does it report alloc-and-zero overlap, or a compressed entry with a non-zero bitmap? (3) Answer the master plan's open question 2: list every write path (`src/crates/snapshot`, `commit`, `rebase`, `resize`, `qcow2-write`, `src/operations/bench`) that can read an extended-L2 entry, cite where it refuses external data files, and say whether any interprets host offset 0 on an entry it did not validate itself. Record the answer here. Use the host `qemu-img` (10.0.13). If a finding depends on qemu version, say so and do not generalise. |
| 1b | high | opus | none | **The classifier.** In `src/crates/qcow2/src/lib.rs`, beside `ClusterLookup` (`:1379`), add `pub enum SubclusterType { Normal, ZeroAlloc, ZeroPlain, UnallocatedAlloc, UnallocatedPlain, Compressed, Invalid }` and `pub fn subcluster_type(l2_entry: u64, bitmap: u64, sc_index: u32, extended_l2: bool, has_data_file: bool) -> SubclusterType`. It transcribes `qcow2_get_cluster_type` and `qcow2_get_subcluster_type` from qemu v10.0.0 `block/qcow2.h:713-796` (fetch the file; do not work from memory) and cites those lines in its doc comment. Note that qemu's `INVALID` checks the *whole* entry and bitmap, not just the bits for `sc_index`. Add a range form, `subcluster_range(…, sc_from) -> (SubclusterType, u32 run_len)`, in the shape of `qcow2_get_subcluster_range_type` (`block/qcow2-cluster.c:395-429`), for run coalescing. Then change `Qcow2State::cluster_lookup` (`:2207-2322`): derive `has_data_file` from `self.incompatible_features & INCOMPAT_EXTERNAL_DATA`; add `ClusterLookup::Invalid`; return `Standard(h)` only when every subcluster is `Normal`, `Zero` when every one is a zero type, `Unallocated` when every one is unallocated, `Invalid` for an invalid entry, and `StandardSubclusters` only for a genuinely mixed bitmap. `Standard(0)` must now be possible only for a data-file image. Replace the bitmap read's `.unwrap_or(0)` (`:2279`) with `?`, so a failed read is a failed lookup. Unit tests: one per row of qemu's table, citing the qemu line; the #653 entry shape both with and without a data file; the row C entry (`0x8000000000000000`, bitmap `0x3`, data file), which qemu's table makes `Normal` for subclusters 0-1 and `UnallocatedAlloc` for 2-31, and `Invalid` without the data file. Keep it `no_std`; no allocation. Update `validate_subcluster_bitmap`'s doc comment to point at the classifier, but leave its callers to step 1d. Run `cargo test -p qcow2` with every feature the guest ops enable, through the build-and-test skill's Make targets. |
| 1c | xhigh | fable | worktree | **The chain reader.** Rebuild the `StandardSubclusters` arm of `read_chain_virtual_cluster` (`src/crates/qcow2/src/lib.rs:13279-13545`) on `subcluster_range` so the narrow and wide paths share one per-run decision: `Normal` reads from `data_device_idx` if set, else the device itself, at `host + offset` (host 0 is now legitimate, D3); `ZeroAlloc`/`ZeroPlain` zero-fill; `UnallocatedAlloc`/`UnallocatedPlain` descend through `devices_behind`, which already zero-fills at the bottom of the chain. Delete both `is_zero \|\| (!is_alloc && host_offset == 0)` conditions (`:13319`, `:13495`) and both "defensive" zero-fills (`:13394-13402`, `:13530-13540`). Keep the wide path's single chunk read for the `Normal` runs, the AES-CBC and LUKS-XTS decryption with their existing IV rules, and the narrow path's run coalescing. Handle `ClusterLookup::Invalid` by refusing. Resolve the master plan's open question 1 first: read how `send_differencing_refusal` (`:15160`) and `shared::DifferencingRefusal` (`src/shared/src/lib.rs:3115-3135`) reach the VMM and get rendered, and add the equivalent for an invalid qcow2 entry, with its renderer in the VMM and a message that names qemu's wording ("Invalid cluster entry found") and the virtual offset. Keep the reader's `bool` contract for its roughly ten callers. Crate tests: extend the #650 in-memory harness (`:5462` onward, `W650_*`) to a two-device chain whose backing device serves a known non-zero byte, plus a data-device variant. For each subcluster type, for the narrow arm (sector 256 or 512 against a subcluster of at least that size) and the wide arm (sector larger than the subcluster), assert where every byte comes from. Include #654's measured case (bitmap with only subcluster 0's zero bit set: subclusters 1-31 must be the backing byte), #653's case (refused without a data file, read from the data device with one), and row C. Run the full `cargo test -p qcow2`, then `make instar` and `make check-binary-sizes`: the classifier is linked into every guest op, which has a 768KB cap. Then re-run the master plan's measurements A-D on the built binary and paste the results here. |
| 1d | high | opus | none | **map, measure and check onto the classifier.** `classify_qcow2_l2_standard` (`lib.rs:1754`), `classify_qcow2_l2_extended` (`:1826`) and `count_allocated_in_l2_extended` (`:1675`) take `has_data_file` and classify through `subcluster_type`/`subcluster_range`. Follow their callers up to the map and measure ops and thread the flag through from the header. Map must report row C's and row D's cluster 0 as data at offset 0, as `qemu-img map` does. Measure must count it. For check (`src/operations/check/src/main.rs:2860-2935`): report the classifier's `Invalid` as the bitmap error it reports today, stop reporting row C as `AllocWithoutHost`, and make `HostWithoutRef`, alloc-and-zero overlap and compressed-with-bitmap follow step 1a's `qemu-img check` measurements. `validate_subcluster_bitmap` either becomes a thin wrapper over the classifier or is removed: do not leave two definitions of validity. Preserve check's existing error counts and wording for every shape qemu also reports, so the existing `tests/test_check_*.py` expectations keep passing unless step 1a showed they were wrong. If they were, change the test and say why in the commit. |
| 1e | medium | opus | none | **Integration tests.** Add `tests/test_qcow2_subclusters.py`, driven by step 1a's builders. For each fixture, run instar and qemu-img side by side: `convert -O raw` and compare bytes against both qemu's output and the builder's expected ownership; `compare` between the two outputs; `map --output json` against qemu's; `check` exit code and error class against qemu's; `measure` against qemu's. For `INVALID` fixtures, assert that instar refuses with the step 1c message and a non-zero exit, and that qemu-img also fails. Follow `tests/base.py` conventions and skip cleanly, with a stated reason, when `qemu-img` is missing, using `_require_qemu_img` from `tests/base.py:473`. `integration-core` is exclusion-based and will pick up a new module automatically, but run `tools/ci/check-test-partition.py` to confirm both partitions claim it. If the module is slow, add it to `tests/stestr-timing.txt` per that file's convention. Run the new module and the existing `test_convert`, `test_map`, `test_measure`, `test_check_formats`, `test_check_chain` and `test_compare` modules. All must pass at 0 failures; check "Ran: N" against the expected count, because a crashed stestr worker can exit 0. |
| 1f | low | sonnet | none | **Documentation.** Replace the `docs/quirks.md` entry "Malformed qcow2 subcluster: instar zero-fills where qemu-img errors" (`:4848` onward) with one that says instar now refuses what qemu calls an invalid entry, with the same message class, and keep a sentence on why (row C showed the zero-fill premise was wrong). Add `CHANGELOG.md` entries under the unreleased section: **Fixed**: extended-L2 reads that ignored the backing file (#654); a crafted entry that served the image header as data (#653); data-file images whose cluster 0 read as zeros, mapped as a hole, measured short, or failed `check`. **Changed**: invalid extended-L2 entries are now refused rather than zero-filled. Add a short subsection to `docs/qcow2/qcow2-format.md` on subcluster types that names the classifier as the single place they are decided and links to qemu's definition. Do not touch `AGENTS.md`, `ARCHITECTURE.md` or `README.md`: no convention or system shape changes. |

Commits: one per step, each passing `pre-commit run --all-files`
(`git add` new files first: pre-commit cannot see untracked files).
Step 1a's commit carries the fixture helper and the measurement table.
Step 1c's runs in a worktree because it is the riskiest change; review
its diff against the brief before merging it back.

## Step 1a measurements

All against qemu-img and qemu-io 10.0.13 (Debian `1:10.0.13+ds-0+deb13u1`)
on 2026-10-19. qemu source citations are v10.0.0: `block/qcow2.h`,
`block/qcow2-cluster.c`, `block/qcow2.c` and `block/qcow2-refcount.c`.
Nothing here was checked on another qemu version; read every claim as
"qemu 10.0.13 does this", not "qemu does this".

### The fixture helper

`tests/helpers/qcow2_subclusters.py`. `BUILDERS` maps a fixture name to
a function that takes a target directory and returns a
`SubclusterFixture`: the image path, backing and data-file paths, the
L2 slots, and per subcluster of the whole virtual disk (eight clusters)
the qemu type and the owner (`child`, `data-file`, `backing`, `zero`,
`refuse`), plus `expected_raw` (the virtual disk as qemu reads it, or
`None` if anything refuses). `build_all()` builds them all, 32 images
in about two seconds.

The types are derived, not hand-written: the helper reads the L2
entries back out of the built image and classifies them with a Python
transcription of `qcow2_get_cluster_type`/`qcow2_get_subcluster_type`
(`qcow2.h:713-796`). Ownership follows qemu's read path:
zero types, and unallocated types without a backing file, read zeros
(`qcow2.c:2414-2420`); `INVALID` is `EIO` (`qcow2-cluster.c:705-713`).
The child's bytes are taken from the file at the host offset, the data
file's at the host offset, the backing file's at the virtual offset,
and a compressed cluster's from the model of what was written. Each
builder also names the types it exists to exhibit and raises if qemu
does not produce them, so a qemu allocation change cannot silently
turn one fixture into another. The backing file's byte at offset `o`
is `(o // 512) % 255 + 1`: never zero, and different per 512-byte
sector, so a read from the wrong place or of the wrong layer shows.
Data and backing files are named by absolute path in the header,
because qemu resolves a relative `data_file` name against its working
directory, not the image's (measured: `qemu-io` from `/` fails with
"Could not open 'r.data'").

`HOST_OFFSET_REFUSAL_BUILDERS` holds two more shapes that classify as
valid but that qemu's `qcow2_get_host_offset` still refuses: a
compressed entry in a data-file image (`qcow2-cluster.c:664-672`) and a
data-file host offset that differs from the guest offset (`:693-704`,
the master plan's Future work item). They are kept out of `BUILDERS`
because phase 1 does not decide them.

### Results

The mixed fixtures put one qemu-written shape in each cluster (with S
the subcluster size and C the cluster size): 0, the first 2S written;
1, 4S written then its second 2S zeroed; 2, its second subcluster
zeroed only; 3, untouched; 4, written whole then zeroed whole; 5,
written whole; 6, written compressed (not possible with a data file,
so untouched there); 7, zeroed whole. Patched fixtures write cluster 1
first so that the L2 table exists, then patch cluster 0.

The master plan's measured rows are these fixtures: A is `issue_654`, B is
`issue_653`, C is `data_file_partial` and D is `data_file_classic`.

Legend. Types: `N` normal, `Za` zero-alloc, `Zp` zero-plain, `Ua`
unallocated-alloc, `Up` unallocated-plain, `C` compressed, `I`
invalid. Owners after the slash: `c` child, `d` data file, `b`
backing, `0` zero, `R` refuse; `x30` is a run of 30 subclusters.
Convert: exit code / converted bytes equal `expected_raw`. Map: qemu's
entries as `start+length:` then `D` data or `H` hole, `z` zero, `@1`
depth 1 (the backing file), `[offset]`. Check: exit code and the class
of qemu's `ERROR` lines. Measure: `qemu-img measure -O qcow2`
required bytes. `EIO` means the command failed with "Input/output
error".

| Fixture | Types/owners per cluster | Convert | Map | Check | Measure |
|---|---|---|---|---|---|
| `mixed_classic_nobacking_nodatafile` | 0:N/c 1:N/c 2:Zp/0 3:Up/0 4:Za/0 5:N/c 6:C/c 7:Zp/0 | 0 / y | 0k+128k:D[0x50000] 128k+64k:Hz 192k+64k:Hz 256k+64k:Hz[0x70000] 320k+64k:D[0x80000] 384k+64k:D 448k+64k:Hz | 0 clean | 589824 |
| `mixed_classic_nobacking_datafile` | 0:N/d 1:N/d 2:Zp/0 3:Up/0 4:Za/0 5:N/d 6:Up/0 7:Zp/0 | 0 / y | 0k+128k:D[0x0] 128k+64k:Hz 192k+64k:Hz 256k+64k:Hz[0x40000] 320k+64k:D[0x50000] 384k+64k:Hz 448k+64k:Hz | 0 clean | 524288 |
| `mixed_classic_backing_nodatafile` | 0:N/c 1:N/c 2:N/c 3:Up/b 4:Za/0 5:N/c 6:C/c 7:Zp/0 | 0 / y | 0k+192k:D[0x50000] 192k+64k:D@1[0x30000] 256k+64k:Hz[0x80000] 320k+64k:D[0x90000] 384k+64k:D 448k+64k:Hz | 0 clean | 720896 |
| `mixed_classic_backing_datafile` | 0:N/d 1:N/d 2:N/d 3:Up/b 4:Za/0 5:N/d 6:Up/b 7:Zp/0 | 0 / y | 0k+192k:D[0x0] 192k+64k:D@1[0x30000] 256k+64k:Hz[0x40000] 320k+64k:D[0x50000] 384k+64k:D@1[0x60000] 448k+64k:Hz | 0 clean | 720896 |
| `mixed_ext_nobacking_nodatafile` | 0:Nx2 Uax30/cx2 0x30 1:Nx2 Zax2 Uax28/cx2 0x30 2:Up Zp Upx30/0x32 3:Upx32/0x32 4:Zax32/0x32 5:Nx32/cx32 6:Cx32/cx32 7:Zpx32/0x32 | 0 / y | 0k+4k:D[0x50000] 4k+60k:Hz[0x51000] 64k+4k:D[0x60000] 68k+4k:Hz[0x61000] 72k+56k:Hz[0x62000] 128k+2k:Hz 130k+2k:Hz 132k+124k:Hz 256k+64k:Hz[0x70000] 320k+64k:D[0x80000] 384k+64k:D 448k+64k:Hz | 0 clean | 589824 |
| `mixed_ext_nobacking_datafile` | 0:Nx2 Uax30/dx2 0x30 1:Nx2 Zax2 Uax28/dx2 0x30 2:Up Zp Upx30/0x32 3:Upx32/0x32 4:Zax32/0x32 5:Nx32/dx32 6:Upx32/0x32 7:Zpx32/0x32 | 0 / y | 0k+4k:D[0x0] 4k+60k:Hz[0x1000] 64k+4k:D[0x10000] 68k+4k:Hz[0x11000] 72k+56k:Hz[0x12000] 128k+2k:Hz 130k+2k:Hz 132k+124k:Hz 256k+64k:Hz[0x40000] 320k+64k:D[0x50000] 384k+64k:Hz 448k+64k:Hz | 0 clean | 524288 |
| `mixed_ext_backing_nodatafile` | 0:Nx2 Uax30/cx2 bx30 1:Nx2 Zax2 Uax28/cx2 0x2 bx28 2:Up Zp Upx30/b 0 bx30 3:Upx32/bx32 4:Zax32/0x32 5:Nx32/cx32 6:Cx32/cx32 7:Zpx32/0x32 | 0 / y | 0k+4k:D[0x50000] 4k+60k:D@1[0x1000] 64k+4k:D[0x60000] 68k+4k:Hz[0x61000] 72k+58k:D@1[0x12000] 130k+2k:Hz 132k+124k:D@1[0x21000] 256k+64k:Hz[0x70000] 320k+64k:D[0x80000] 384k+64k:D 448k+64k:Hz | 0 clean | 720896 |
| `mixed_ext_backing_datafile` | 0:Nx2 Uax30/dx2 bx30 1:Nx2 Zax2 Uax28/dx2 0x2 bx28 2:Up Zp Upx30/b 0 bx30 3:Upx32/bx32 4:Zax32/0x32 5:Nx32/dx32 6:Upx32/bx32 7:Zpx32/0x32 | 0 / y | 0k+4k:D[0x0] 4k+60k:D@1[0x1000] 64k+4k:D[0x10000] 68k+4k:Hz[0x11000] 72k+58k:D@1[0x12000] 130k+2k:Hz 132k+124k:D@1[0x21000] 256k+64k:Hz[0x40000] 320k+64k:D[0x50000] 384k+64k:D@1[0x60000] 448k+64k:Hz | 0 clean | 720896 |
| `mixed_ext_backing_nodatafile_16k` | 0:Nx2 Uax30/cx2 bx30 1:Nx2 Zax2 Uax28/cx2 0x2 bx28 2:Up Zp Upx30/b 0 bx30 3:Upx32/bx32 4:Zax32/0x32 5:Nx32/cx32 6:Cx32/cx32 7:Zpx32/0x32 | 0 / y | 0k+1k:D[0x14000] 1k+15k:D@1[0x400] 16k+1k:D[0x18000] 17k+1k:Hz[0x18400] 18k+14k:D@1[0x4800] 32k+0k:Hz 33k+31k:D@1[0x8400] 64k+16k:Hz[0x1c000] 80k+16k:D[0x20000] 96k+16k:D 112k+16k:Hz | 0 clean | 458752 |
| `mixed_ext_backing_nodatafile_32k` | 0:Nx2 Uax30/cx2 bx30 1:Nx2 Zax2 Uax28/cx2 0x2 bx28 2:Up Zp Upx30/b 0 bx30 3:Upx32/bx32 4:Zax32/0x32 5:Nx32/cx32 6:Cx32/cx32 7:Zpx32/0x32 | 0 / y | 0k+2k:D[0x28000] 2k+30k:D@1[0x800] 32k+2k:D[0x30000] 34k+2k:Hz[0x30800] 36k+29k:D@1[0x9000] 65k+1k:Hz 66k+62k:D@1[0x10800] 128k+32k:Hz[0x38000] 160k+32k:D[0x40000] 192k+32k:D 224k+32k:Hz | 0 clean | 589824 |
| `mixed_ext_backing_nodatafile_128k` | 0:Nx2 Uax30/cx2 bx30 1:Nx2 Zax2 Uax28/cx2 0x2 bx28 2:Up Zp Upx30/b 0 bx30 3:Upx32/bx32 4:Zax32/0x32 5:Nx32/cx32 6:Cx32/cx32 7:Zpx32/0x32 | 0 / y | 0k+8k:D[0xa0000] 8k+120k:D@1[0x2000] 128k+8k:D[0xc0000] 136k+8k:Hz[0xc2000] 144k+116k:D@1[0x24000] 260k+4k:Hz 264k+248k:D@1[0x42000] 512k+128k:Hz[0xe0000] 640k+128k:D[0x100000] 768k+128k:D 896k+128k:Hz | 0 clean | 1114112 |
| `issue_654` | 0:Zp Upx31/0 bx31 1:Upx32/bx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 0 / y | 0k+2k:Hz 2k+510k:D@1[0x800] | 0 clean | 851968 |
| `issue_653` | 0:Ix32/Rx32 1:Nx32/cx32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `issue_653_backing` | 0:Ix32/Rx32 1:Nx32/cx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `data_file_partial` | 0:Nx2 Uax30/dx2 0x30 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 0 / y | 0k+4k:D[0x0] 4k+60k:Hz[0x1000] 64k+448k:Hz | 0 clean | 393216 |
| `data_file_partial_backing` | 0:Nx2 Uax30/dx2 bx30 1:Upx32/bx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 0 / y | 0k+4k:D[0x0] 4k+508k:D@1[0x1000] | 0 clean | 851968 |
| `data_file_classic` | 0:N/d 1:Up/0 2:Up/0 3:Up/0 4:Up/0 5:Up/0 6:Up/0 7:Up/0 | 0 / y | 0k+64k:D[0x0] 64k+448k:Hz | 0 clean | 393216 |
| `data_file_full_cluster` | 0:Nx32/dx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 0 / y | 0k+64k:D[0x0] 64k+448k:Hz | 0 clean | 393216 |
| `data_file_ext_no_copied` | 0:Ix32/Rx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `data_file_classic_no_copied` | 0:Up/0 1:Up/0 2:Up/0 3:Up/0 4:Up/0 5:Up/0 6:Up/0 7:Up/0 | 0 / y | 0k+512k:Hz | 0 clean | 327680 |
| `data_file_classic_no_copied_backing` | 0:Up/b 1:Up/b 2:Up/b 3:Up/b 4:Up/b 5:Up/b 6:Up/b 7:Up/b | 0 / y | 0k+512k:D@1[0x0] | 0 clean | 851968 |
| `issue_650` | 0:Ix32/Rx32 1:Nx32/cx32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `issue_650_backing` | 0:Ix32/Rx32 1:Nx32/cx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `alloc_zero_overlap` | 0:Ix32/Rx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 alloc-zero-overlap | EIO |
| `alloc_zero_overlap_data_file` | 0:Ix32/Rx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 alloc-zero-overlap | EIO |
| `host_without_ref` | 0:Uax32/0x32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 0 / y | 0k+64k:Hz[0x50000] 64k+448k:Hz | 0 clean | 327680 |
| `host_without_ref_backing` | 0:Uax32/bx32 1:Upx32/bx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 0 / y | 0k+512k:D@1[0x0] | 0 clean | 851968 |
| `compressed_bitmap_all_alloc` | 0:Cx32/cx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 0 / y | 0k+64k:D 64k+448k:Hz | 2 compressed-bitmap+leak | 393216 |
| `compressed_bitmap_arbitrary` | 0:Cx32/cx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 0 / y | 0k+64k:D 64k+448k:Hz | 2 compressed-bitmap+leak | 393216 |
| `issue_654_32k` | 0:Zp Upx31/0 bx31 1:Upx32/bx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 0 / y | 0k+1k:Hz 1k+255k:D@1[0x400] | 0 clean | 589824 |
| `data_file_partial_32k` | 0:N Uax31/d bx31 1:Upx32/bx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 0 / y | 0k+1k:D[0x0] 1k+255k:D@1[0x400] | 0 clean | 589824 |
| `issue_653_32k` | 0:Ix32/Rx32 1:Nx32/cx32 2:Upx32/bx32 3:Upx32/bx32 4:Upx32/bx32 5:Upx32/bx32 6:Upx32/bx32 7:Upx32/bx32 | 1 / n/a | EIO | 2 unalloc-with-alloc-bits | EIO |
| `data_file_compressed` | 0:Cx32/Rx32 1:Upx32/0x32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 2 compressed-with-data-file | EIO |
| `data_file_offset_mismatch` | 0:Upx32/0x32 1:Nx32/Rx32 2:Upx32/0x32 3:Upx32/0x32 4:Upx32/0x32 5:Upx32/0x32 6:Upx32/0x32 7:Upx32/0x32 | 1 / n/a | EIO | 0 clean | EIO |

Every well-formed and every patched-but-valid fixture converts to
exactly `expected_raw`, and every `INVALID` fixture fails. So the
helper's derivation agrees with qemu on all 34 fixtures. qemu's
refusal reads, on stderr, `qcow2: Image is corrupt: Invalid cluster
entry found  (L2 offset: 0x40000, L2 index: 0); further non-fatal
corruption events will be suppressed`, then `qemu-img: error while
reading block status at offset 0: Input/output error`. Opened
read-only, qemu does not set the corrupt bit (the header's incompatible
features were unchanged after every command).

Points the later steps need:

* **Row C's measure.** At this fixture size qemu measures 393216 for
  `data_file_partial`. The master plan's 458752/524288 pair came from a larger
  image; step 1e should compare against qemu, not against those
  numbers.
* **Map of an `UNALLOCATED_ALLOC` run without a backing file** is
  `"data": false, "zero": true` *with* an `offset` (`host_without_ref`:
  `0k+64k:Hz[0x50000]`; the `Ua` runs of the mixed fixtures likewise).
  With a backing file the same run is reported as the backing file's
  data at depth 1.
* **Classic L2 with a backing file and a partial `write -z`** is not a
  zero cluster: qemu writes zeros into a newly allocated cluster
  (`mixed_classic_backing_*` cluster 2 is `N`). The builders take
  types from the image, so this needs no special case.
* **Host 0 without `COPIED` in a data-file image is unallocated.** With
  classic L2 (`data_file_classic_no_copied*`) the cluster reads zeros or
  the backing file, not the data file, which is the case #792's
  fall-through gets wrong. With extended L2 and alloc bits it is
  `INVALID` (`data_file_ext_no_copied`).

### The three `qemu-img check` questions

Read from `check_refcounts_l2` (`qcow2-refcount.c:1662-1849`) and then
measured:

1. **An entry with a non-zero host offset and a zero bitmap
   (instar's `HostWithoutRef`) is not reported.** `host_without_ref`
   and `host_without_ref_backing` check clean, exit 0. qemu has no such
   rule: a `NORMAL` cluster's bitmap is tested only for alloc/zero
   overlap (`:1756-1761`). Per D5, instar's check should stop reporting
   it.
2. **Alloc-and-zero overlap is reported**, exit 2. On an allocated
   cluster (including a data-file `COPIED|0` entry) the message is
   `ERROR offset=<host>: Allocated cluster has corrupted subcluster
   allocation bitmap` (`:1756-1761`). Where the host offset is 0 and
   there is no data file, the cluster is unallocated, and the same
   bitmap is reported instead as `ERROR: Unallocated cluster has
   non-zero subcluster allocation map` (`:1838-1843`), which is also
   the message for #650, #653 (row B) and `data_file_ext_no_copied`.
3. **A compressed entry with a non-zero bitmap is reported**, exit 2:
   `ERROR compressed cluster 0 with non-zero subcluster allocation
   bitmap, entry=0x4000000000050000` (`:1717-1722`). The rule is "any
   non-zero bitmap", so **qemu 10.0.13 also reports
   `0x00000000ffffffff`**, the pattern `validate_subcluster_bitmap`
   (`src/crates/qcow2/src/lib.rs:1452-1461`) exempts as legacy qemu
   output. Because qemu's check `break`s before counting the compressed
   cluster's refcount, it also reports the cluster as leaked (`Leaked
   cluster 5 refcount=1 reference=0`); that leak is an artefact of the
   error path, not a second defect. Reading still works: qemu returns
   `COMPRESSED` without looking at the bitmap, and both fixtures
   convert correctly, as D2 assumes.

### Write paths (master plan open question 2)

| Path | Extended L2 | External data file | Interprets host offset 0 itself |
|---|---|---|---|
| `qcow2-write` (`check_envelope_with`) | refused, `src/crates/qcow2-write/src/lib.rs:674-676` | refused, `:677-679` | Validates it: `classify_l2_entry` (`:1636-1700`) takes entry 0 as unallocated, host 0 with the zero flag as unallocated, and refuses any other host-0 entry as `UnknownL2Entry` (`:1685-1687`). Correct for images without a data file. |
| bench `-w` | refused via `check_envelope_with`, `src/operations/bench/src/main.rs:713` | refused, same call | No decoding of its own; writes go through `qcow2-write`, reads through `read_chain_virtual_cluster` (`:1150`), which phase 1 fixes. |
| rebase | safe mode refused via `check_envelope_with`, `src/operations/rebase/src/main.rs:1070` | refused, `src/crates/rebase/src/qcow2.rs:159-160` (both modes) | No decoding of its own: overlay entries through `qcow2-write`, chain data through `read_chain_virtual_cluster` (`main.rs:1927`), which phase 1 fixes. |
| commit | refused, `src/operations/commit/src/main.rs:925-929` | refused, `main.rs:903-908` and `src/crates/commit/src/qcow2.rs:66-67,81-82` | **Yes, wrongly, on classic L2.** It decodes overlay entries itself and ignores `QCOW_OFLAG_ZERO`: `main.rs:1227-1235` skips any entry with host offset 0 and otherwise copies the host cluster's bytes; the clear pass (`:1384-1390`) and the allocation count (`:1114-1119`) do the same. Measured below. |
| resize | accepted | refused, `src/crates/resize/src/qcow2.rs:1740-1742` | **Yes, wrongly, on shrink.** Any non-zero L2 word above the new size is treated as a host cluster and its refcount set to 0 (`qcow2.rs:1516-1545` for the straddling L2, `:1675-1690` for wholly discarded ones), with no test of the host offset. A word that is non-zero with host offset 0 zeroes the refcount of cluster 0, the header. Measured below. |
| snapshot | accepted | refused by the op, `src/operations/snapshot/src/main.rs:438-441` (the crate itself has no gate) | Yes, and consistently with qemu: the L2 walks treat host 0 as "no host cluster" for refcounting (`src/crates/snapshot/src/qcow2.rs:650-668`, `:1026-1037`) and scrub a stale `COPIED` from host-0 entries (`:1306-1323`). That is what qemu's `qcow2_update_snapshot_refcount` does (`qcow2-refcount.c:1318-1376`, which also ignores the bitmap), and with data files refused, host 0 has no other meaning. Out of scope. |

Measured against the instar binary built from develop `da9f043f`
(`src/target/release/instar` in the primary clone):

* **commit, classic L2.** Base qcow2 filled with `0x42`; overlay with
  `write -z 0 64k` (entry `0x1`, `ZERO_PLAIN`), `write -P 0x55 64k 64k`
  then `write -z 64k 64k` (entry `0x8000000000050001`, `ZERO_ALLOC`),
  and `write -P 0x66 128k 4k`. After `qemu-img commit` the base reads
  `00 00 66` at offsets 0, 64k, 128k; after `instar commit` it reads
  `42 55 66`. Both exit 0; `qemu-img compare` reports "Content mismatch
  at offset 0!". The zero-plain cluster was skipped and the zero-alloc
  cluster's stale bytes were committed.
* **resize `--shrink`, classic L2.** 1M image, cluster 0 written, then
  `write -z 960k 64k` (entry `0x1`), shrunk to 512K. `qemu-img resize`
  leaves a clean image; `instar resize` exits 0 and `qemu-img check`
  then exits 2 with `ERROR cluster 0 refcount=0 reference=1`. The same
  test with a qemu-written extended-L2 zero-plain entry (word 0, so
  skipped) and with compressed entries was clean.

Both shapes are qemu-written, not crafted, and both runs exit 0. Neither
is an extended-L2 entry, so neither is something the classifier
changes, but both are write paths interpreting host offset 0 on entries
they did not validate. The phase's scope admits write paths only in
that case, so it is the management session's call whether they join
phase 1 or are filed as separate issues. No existing issue covers
either (searched 2026-10-19). Both were filed and kept out of this
phase: see Execution notes.

## Step 1c measurements

The master plan's rows A to D, re-run on 2026-10-20 against the binary
built from the step 1c tree, for the read path (`convert -O raw`,
`compare`, `dd` and `bench`). map, measure and check are step 1d's and
are not measured here.

| # | Fixture | Before (develop `da9f043f`) | After step 1c | qemu 10.0.13 |
|---|---|---|---|---|
| A (#654) | `issue_654` | subclusters 1-31 of cluster 0 read zeros | read the backing file; `convert`, `compare`, `dd` and `bench` agree with qemu byte for byte | the same |
| B (#653) | `issue_653` | served file offset 0 (header, L1) as guest data, exit 0 | refused, exit 1: `convert: Invalid cluster entry found at virtual offset 0 (0x0): the qcow2 L2 entry marks a subcluster allocated with no host cluster behind it, or both allocated and zero, so the image is corrupt and the read failed as it does in qemu`; `compare`, `dd` and `bench` refuse with the same sentence under their own name, and `compare` no longer prints `Content mismatch at offset 0!` first | `EIO`, "Invalid cluster entry found" |
| C | `data_file_partial` | `convert` wrote zeros for the 4 KiB of data | data read from the data file, the rest zeros; `compare`, `dd`, `bench` agree with qemu | the same |
| D | `data_file_classic` | right by accident (the data device was next in the chain) | right through `Standard(0)` and `data_device_idx`; the chain's order no longer matters for this cluster | the same |

All 34 of step 1a's fixtures (`build_all(include_host_offset_refusals=True)`)
through `convert -O raw`, compared with `expected_raw`:

| Result | Count | Fixtures |
|---|---|---|
| bytes match `expected_raw` | 18 | every valid fixture without a data file, `mixed_classic_nobacking_datafile`, `mixed_ext_nobacking_datafile`, `data_file_partial`, `data_file_classic`, `data_file_full_cluster` |
| refused by name, exit 1 | 8 | `issue_653`, `issue_653_backing`, `issue_653_32k`, `issue_650`, `issue_650_backing`, `data_file_ext_no_copied`, `alloc_zero_overlap`, `alloc_zero_overlap_data_file` |
| wrong, #792 (unallocated clusters fall into the data device) | 6 | `mixed_classic_backing_datafile`, `mixed_ext_backing_datafile`, `data_file_partial_backing`, `data_file_partial_32k`, `data_file_classic_no_copied_backing`, and `data_file_classic_no_copied`, which has no backing file: its classic host-0 entry without `COPIED` is unallocated, `continue`s into the next device, and that device is the data file |
| not refused: qemu's host-offset rules, not classification | 2 | `data_file_compressed` (exit 1 with the generic text: the compressed cluster is read from the metadata device and does not inflate) and `data_file_offset_mismatch` (exit 0; the master plan's Future work item) |

The `dd` window check (`bs=4096 skip=1 count=3`) is byte-identical to
`qemu-img dd`'s 8192-byte output on `issue_654` and `data_file_partial`.

## Execution notes

* 2026-10-20 (AEDT). Back brief answered: the steps run sequentially as
  planned, with fable for 1c, and this session may push and open the
  pull request once the success criteria pass.
* Step 1a found two write paths that interpret host offset 0 wrongly on
  classic L2, both reproduced by the management session on qemu-written
  images. The operator chose to file them rather than widen the phase:
  #794 (`resize --shrink` sets the header cluster's refcount to 0) and
  #795 (`commit` ignores `QCOW_OFLAG_ZERO`). They are not phase 1 work.
* Step 1a's `qemu-img check` measurements settle D5 for step 1d. `check`
  stops reporting `HostWithoutRef`, which qemu does not report. It
  keeps reporting alloc-and-zero overlap. It reports a compressed entry
  with a non-zero bitmap **including** `alloc_bits == 0xFFFF_FFFF`,
  because qemu 10.0.13 flags that pattern too. The legacy exemption in
  `validate_subcluster_bitmap` goes.
* The fixtures are named for their issue or behaviour rather than for
  the master plan's rows, to keep plan references out of code. The
  mapping is in "Step 1a measurements". The management session
  re-checked all 34 against `qemu-img convert`: 24 valid fixtures match
  `expected_raw` byte for byte, and all 10 refusal fixtures fail.
* Step 1e found that `compare` reads chunks of the larger cluster size
  through a reader that fills one input cluster per call. With clusters
  under 64K it compares stale buffer bytes. That predates this work; the
  management session reproduced it on develop and filed it as #800. The
  affected compare cases are expected failures citing it.
* Step 1d went beyond its row in two places, and both were kept. map
  gained offset-bearing zero and hole states (record codes 3 and 4) so
  that it reports qemu's `"offset"`. measure stopped counting zero-flag
  clusters. On a plain classic image the management session confirmed
  that both changes fix a divergence from qemu-img that develop already
  had.
* 2026-10-20 (AEDT), on HEAD `8eb7a246`, before the push: `make lint`,
  `make test-rust` (2617 passed, 0 failed), `make instar` and
  `make check-binary-sizes` (convert is the largest at 365KB of 768KB)
  all pass. The new module plus test_map, test_measure,
  test_check_formats, test_check_chain and test_check_repair ran 1230
  tests: 0 failed and 19 expected failures. The `host_offset == 0` grep
  finds only the classifier's doc comment and tests.
* 2026-10-20 (AEDT), CI on `b18590b0`: the mutation-pattern guard
  failed, because two cases targeted the #650 zero-fill this phase
  removed. They were retargeted at the refusal and the classifier
  (`1fd5cedd`).
* 2026-10-20 (AEDT), review round 1 on `1fd5cedd` (2 fix, 1 document,
  6 consider). The first fix item was real and older than this phase:
  the chain reader assumed every chunk fits in one cluster of each
  device, so a 64K image over a 16K qcow2 backing file converted wrong
  with exit 0 on develop too. A split at qcow2 cluster and VMDK grain
  boundaries, inside the reader, fixed it and #800 with it. The
  crate harness and the fixture builder gained qcow2 backing files
  whose clusters sit in reverse order on the host; the module now has
  35 fixtures and 175 tests, and the three #800 compare entries left
  `EXPECTED_FAILURES`. The `check` change to count a data-file
  image's cluster 0 exposed #804: L2 tables are counted too. #803
  tracks qemu's data-file host-offset checks. The refusal message was
  shortened in this round to `<op>: Invalid cluster entry found at
  virtual offset N (0x…): corrupt qcow2 L2 entry`; row B of the step
  1c measurements quotes the longer sentence that was measured then.

## Success criteria for the phase

* Measurements A to D from the master plan give qemu's answer on the
  built binary, for read, map, measure and check.
* Every `INVALID` fixture is refused, with the named message, by every
  read operation that reaches it.
* No consumer outside the classifier tests host offset 0 on an L2
  entry: `grep -n "host_offset == 0" src/crates/qcow2/src/lib.rs`
  returns only the classifier and its tests, plus any line that step 1a
  records as a write path that is out of scope.
* `make instar`, `make lint`, `make check-binary-sizes`,
  `make test-rust` and the integration modules named in step 1e pass.
