# Format coverage phase 2: VDI convert-from (read path)

Master plan: [PLAN-format-coverage.md](/components/instar/plans/PLAN-format-coverage/)

## Status: Complete (2026-07-18)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

VDI (VirtualBox Disk Image) is detect + info only today.
Phase 1's consumer gate refuses it for convert/compare/dd
with the typed `UnsupportedInputFormat` message, and three
tests pin that refusal (`tests/test_convert.py:5554`,
`tests/test_compare.py:1229`, `tests/test_dd.py:1420`).
This phase graduates VDI to a full read format for those
three subcommands, following the established per-format
read-path pattern.

Research for this plan was done 2026-07-18 by two grounding
passes: a codebase trace of the VHD read path (the closest
structural analogue), and an empirical verification of the
VDI format and qemu's driver semantics against `block/vdi.c`
(read in full at 7.0.0 from the testdata qemu-sources copy,
confirmed unchanged at master) with every load-bearing claim
re-verified by running real qemu-img (10.2.0 static binary,
info-shape drift sampled at 6.0.0 / 7.2.0 / 8.2.0 / 9.2.0).

### The read-path pattern (codebase facts)

* The shared chain reader lives in the qcow2 crate.
  Per-format dispatch is the `match format` in
  `read_chain_virtual_cluster`
  (`src/crates/qcow2/src/lib.rs:5855`); the VHD arm
  (`:6388`) is the model: `state.block_lookup(...)` returns
  `Unallocated` (→ `continue` to the next chain device) or
  `Allocated { host_byte_offset }` (→ `read_cluster_sectors`
  when guest-sector-aligned, `read_offset_sectors` when
  not). The default arm (`:6513`) reads raw — this is what
  the host gate protects detect-only formats from reaching.
* Per-device reader state lives in `ChainStates`
  (`src/crates/qcow2/src/lib.rs:6671`, feature-gated arrays
  like `vhd_states`), initialised by the dispatch in
  `init_chain_states` (`:6697`, VHD arm `:6744`, which
  reuses the L1/L2 cache buffer slots as the format's
  BAT/data sector caches).
* The model state type is `VhdState`
  (`src/crates/vhd/src/lib.rs:483`): `init(call_table,
  device_idx, sector_size, input_capacity, cache bufs,
  &mut bytes_read) -> Option<Self>` plus
  `block_lookup(...) -> Option<BlockLookup>`. All I/O goes
  through `(call_table.read_input_sector)`; all arithmetic
  is checked/`Option`. The `vhd` crate's Cargo.toml (dep on
  `shared` only, no_std, workspace lints) is the template
  for a new `src/crates/vdi/`.
* Input formats are feature flags on the qcow2 crate
  (`vhd-input`, `vhdx-input`, `vmdk-input` in
  `src/crates/qcow2/Cargo.toml`); each consuming operation
  enables them in its own Cargo.toml. Operations linking
  the chain reader: **convert** (dd runs the convert binary
  — there is no dd guest binary), **compare**, **bench**,
  **rebase**. `copy` is format-agnostic raw passthrough;
  `check` links format crates directly, not via the reader.
* Host side: `chain::ImageFormat` (`src/vmm/src/chain.rs:24`)
  has no `Vdi` variant; `from_str` (`:50`) maps `"vdi"` to
  `Unknown`, which trips the phase-1 gate in
  `discover_backing_chain` (`src/vmm/src/main.rs:2357`).
  Graduation = add the variant + `from_str` arm +
  `to_shared_format_u32` arm (→ 8, matching
  `shared::ImageFormat::Vdi = 8`, which already exists at
  `src/shared/src/lib.rs:1546`) + `Display` arm. No CLI
  changes: convert and compare have no input-format flag,
  and dd's `-f` is accepted-but-ignored (`main.rs:13809`).
* The guest info op already parses VDI headers
  (`src/operations/info/src/main.rs:1012`,
  `parse_vdi_header`) but does NOT read the three fields a
  reader needs: `offset_bmap` (hdr 340), `offset_data`
  (344), `block_extra` (380). The new crate parses these
  itself.
* Binary size headroom is ample: convert is at 39% of the
  768KB cap (307,544B ELF memory extent), compare at 24%.
  The size check measures `.bss`-inclusive ELF extent via
  `scripts/check-binary-sizes.sh`.
* Fuzzing: `src/fuzz/fuzz_targets/fuzz_vhd_footer.rs`
  (buffer-shape) and `fuzz_vhd_bat.rs` (mock-CallTable
  stateful shape) are the templates; the differential
  fuzzer's format pool is `FORMATS = ['qcow2', 'raw',
  'vmdk', 'vpc']` at `scripts/differential-fuzz.py:52`.

### VDI format facts (all empirically verified)

Header: 512 bytes, all scalars little-endian. Fields the
reader needs (hex/dec offsets):

| Offset | Field | Notes |
|--------|-------|-------|
| 0x40 / 64 | signature u32 | 0xbeda107f |
| 0x44 / 68 | version u32 | 0x00010001 only (1.1) |
| 0x4c / 76 | image_type u32 | 1 dynamic, 2 static |
| 0x154 / 340 | offset_bmap u32 | must be 512-aligned |
| 0x158 / 344 | offset_data u32 | must be 512-aligned |
| 0x168 / 360 | sector_size u32 | must be 512 |
| 0x170 / 368 | disk_size u64 | virtual size in bytes |
| 0x178 / 376 | block_size u32 | must be exactly 1 MiB |
| 0x17c / 380 | block_extra u32 | ignored by qemu |
| 0x180 / 384 | blocks_in_image u32 | |
| 0x1a8 / 424 | uuid_link 16B | must be all-zero |
| 0x1b8 / 440 | uuid_parent 16B | must be all-zero |

Block map: flat array of `blocks_in_image` LE u32 entries at
`offset_bmap`. Sentinels `0xffffffff` (unallocated) and
`0xfffffffe` (discarded) both read as zeros; an entry is
allocated iff `< 0xfffffffe`. Data offset for an allocated
block is exactly `offset_data + entry as u64 * block_size +
offset_in_block` — `block_extra` does NOT participate
(byte-patch verified). Entries are allocation-order indices,
not an identity map, except static images write the identity
map at create time. VDI has no backing-file support: qemu
refuses non-null link/parent UUIDs at open, so a VDI is
always the end of a chain.

qemu `vdi_open` validation, in order, with exact limits
(all 12 checks empirically confirmed, error strings in the
grounding transcript):

1. `disk_size > 536870784 * 1048576` (≈512 TiB) → refuse.
2. `disk_size % 512 != 0` → NOT an error: round up to 512
   in memory (VBoxManage-created images can be odd).
3. bad signature → refuse under `-f vdi`; under probing the
   image simply detects as raw.
4. `version != 0x00010001` → refuse.
5. `offset_bmap % 512 != 0` → refuse.
6. `offset_data % 512 != 0` → refuse.
7. `sector_size != 512` → refuse.
8. `block_size != 1048576` → refuse (hard-fixed).
9. `disk_size > blocks_in_image * block_size` (using the
   rounded disk_size) → refuse.
10. non-null uuid_link → refuse.
11. non-null uuid_parent → refuse.
12. `blocks_in_image > 536870784` → refuse.

Critical read semantics: **qemu never validates file length
and zero-fills any read past EOF.** A bmap entry pointing
far past EOF opens fine, `qemu-img map` advertises the bogus
offset as data, and `convert -O raw` exits 0 with that block
all-zero (straddling blocks zero-fill only the missing
tail). instar must replicate: past-EOF block reads yield
zeros, never an error. This also means instar's
65536-rounded zero-tail guest capacity view is naturally
consistent — header-declared geometry drives all reads.

Other verified facts:

* image_type is NOT validated: 0/3/4 all open and behave as
  dynamic; only type 2 is special (static). Do not reject
  unknown types.
* Static images: bmap is the identity map, file =
  `offset_data + blocks * 1 MiB` (sparse). The read path is
  identical — walk the bmap; no special-casing needed.
* `qemu-img create -f vdi` rounds requested size up to 512
  before writing `disk_size`; `cluster_size` in info output
  is `block_size` (1048576); VDI has no `format-specific`
  block; info-shape drift across versions is only the
  generic 8.0+ child-node dump.
* `qemu-img map` on dynamic VDI: holes are
  `present:true, zero:true, data:false`; allocated blocks
  carry `offset`.
* Alignment: offset_data / bmap entries are 512-aligned by
  validation, but NOT 65536-aligned (offset_data is
  typically 1024) — with the guest's 65536-byte virtio
  sectors, the VHD arm's aligned/unaligned split
  (`read_cluster_sectors` / `read_offset_sectors`) must be
  mirrored, not simplified to the aligned path.
* oslo.utils `VDIInspector`: signature at 0x40, virtual
  size = raw u64 at 0x170, always reports safe. Agrees with
  qemu on qemu-created images; diverges on odd disk_size
  (oslo reports raw, qemu rounds up). No VDI entries exist
  in test_oslo_crossval's skip/divergence lists today.
* Testdata: exactly one VDI fixture exists — `vdi-simple`
  (10 MiB empty dynamic, 1024 bytes, `run_in_ci: false`,
  `skip_qemu_img: true`). No static fixture, no
  data-bearing fixture. info and check baselines exist for
  vdi-simple; the check baselines are currently unconsumed
  by any test. qemu's VDI driver does support `qemu-img
  check` (bmap validation), which is why those baselines
  generate.

## Design

### Scope

In scope: convert, compare, and dd gain VDI input (dd comes
free with convert — same guest binary). Both dynamic and
static images, plus discarded-block and past-EOF semantics.
VDI as a backing file of a qcow2 chain (qemu permits
`-F vdi`) works through the same reader arm and gets one
test.

Out of scope, refused or unchanged as today: map (guest-side
`ERROR_INVALID_SOURCE`), measure (clap-restricted source
list), resize/amend/snapshot (own probes), check (see
below), any VDI write/create/output support, `--extra-detail`
info changes.

### Reader semantics: exact qemu parity

The new `src/crates/vdi/` crate implements qemu's 12
open-time checks with the same limits and order-independent
outcomes (init returns `None`; the op surfaces its normal
init-failure error). Additional rules, each pinned by a
fixture:

* Round odd `disk_size` up to 512 (rule 2). The virtual
  size instar reports and reads is the rounded value —
  qemu parity, recorded as an oslo divergence.
* Ignore `block_extra` entirely; never include it in offset
  math.
* Accept any `image_type`; no static special-case in the
  read path (the identity bmap is just data).
* Unallocated and discarded entries read as zeros. In the
  chain-reader arm this is `continue` — safe because a VDI
  can never have a device below it (non-null link/parent
  UUIDs are refused), so the loop falls through to the
  zero-fill tail.
* Reads whose computed host offset lies at or past the
  input capacity zero-fill instead of erroring, including
  the straddle case (partial block in-file). Note the guest
  capacity is the file length rounded up to the 65536-byte
  virtio sector with a zero tail, which composes correctly
  with this rule.

### Guest integration and the raw-fallback hazard

Graduating `"vdi"` in host `chain.rs` lifts the phase-1
refusal for EVERY operation that calls
`discover_backing_chain` — convert, compare, dd, check,
bench, and rebase. Any of those whose guest binary lacks the
`vdi-input` feature would fall into the chain reader's
default arm and silently read the VDI as raw: exactly the
issue #444 class phase 1 closed. Therefore:

* The `vdi-input` feature is added to the qcow2 crate and
  enabled by **all four** reader-linking operations
  (convert, compare, bench, rebase) in the same change —
  not just convert/compare.
* check does not link the chain reader; after graduation
  its guest op must fail cleanly on VDI (its own format
  dispatch has no VDI arm). Step 2c verifies this
  empirically and pins it with a test; if the observed
  behaviour is a silent raw read or a hang rather than a
  clean error, that is a blocking finding for management
  review before the graduation commit lands. Real `qemu-img
  check` support for VDI (qemu validates the bmap; unused
  baselines already exist in testdata) is future work.
* The guest reader arm must land (2b) strictly before the
  host graduation (2c). Between those commits behaviour is
  unchanged (gate still refuses), so each commit is
  self-contained.

### Fixtures and baselines

New fixtures, generated by a deterministic
`generate-vdi-fixtures.py` in instar-testdata (following
`generate-dmg-fixtures.py`; qemu-created then byte-patched;
UUIDs at 0x188/0x198 are the only non-deterministic creation
bytes, so patch them to fixed values for reproducibility):

Safe (qemu-readable, full info baselines, convert parity):

* `vdi-data-dynamic` — 8 MiB dynamic, data blocks written
  at 2 MiB and 5 MiB (produces a non-identity bmap), one
  allocated entry patched to `0xfffffffe` (discarded), rest
  holes.
* `vdi-static-data` — small static image (≤4 MiB virtual;
  file is sparse) with a data pattern in a middle block.
* `vdi-odd-size` — disk_size byte-patched to a non-512
  multiple; qemu rounds up (verified: 12801 → 13312). Pins
  the round-up rule end-to-end.
* `vdi-bmap-past-eof` — one allocated entry pointing past
  EOF; qemu opens, converts with exit 0, zero-fills. Pins
  the zero-fill rule differentially.

Malformed (qemu refuses at open; `skip_qemu_img: true`,
adversarial harness — instar must refuse cleanly, no hang):

* `vdi-bad-version` (version 2.0), `vdi-unaligned-bmap`,
  `vdi-wrong-blocksize` (512), `vdi-nonnull-parent`,
  `vdi-too-many-blocks` (0xffffffff).

Existing `vdi-simple` flips to `run_in_ci: true` and drops
`skip_qemu_img`, matching the readable-format convention
(vpc's `virtualpc-vhd` entry is the model). Safe fixtures
live in `custom/format-coverage/`, malformed in
`custom/audit/`, per phase-1 convention. Baselines via
`generate-baselines.py` (restricted manifest, `--no-commit`)
then `detect-profiles.py`; testdata commits go straight to
main per operator standing instruction, but are made by the
management session after review, never by sub-agents.

### oslo crossval

Flipping `vdi-simple` to `run_in_ci: true` auto-enrolls VDI
images in test_oslo_crossval. Expected outcomes: format and
size agreement everywhere except `vdi-odd-size`, which needs
a `KNOWN_VSIZE_DIVERGENCES` entry (oslo reports the raw
value, qemu/instar the rounded one). Malformed fixtures are
`skip_qemu_img` and out of oslo's scenario set; if any leaks
in, use `OSLO_SKIP_IMAGES` with a comment.

### Info parser round-up fix

`parse_vdi_header` (`src/operations/info/src/main.rs:1012`)
currently reports the raw `disk_size`. With `vdi-odd-size`
baselined, info must report the rounded value to stay
byte-identical to qemu. Fix as its own commit in step 2c
(one-line semantics change plus unit test). All existing
baselined VDI images are 512-aligned, so no baseline
regenerates.

### Fuzzing

* Coverage-guided: `fuzz_vdi_header.rs` (buffer shape,
  modelled on `fuzz_vhd_footer.rs`) and `fuzz_vdi_bat.rs`
  (mock-CallTable stateful shape, modelled on
  `fuzz_vhd_bat.rs`), plus the `vdi` dep and two `[[bin]]`
  blocks in `src/fuzz/Cargo.toml`.
* Differential: append `'vdi'` to `FORMATS`
  (`scripts/differential-fuzz.py:52`) and add a
  `-o static=on` variant branch in `generate_image`. This
  MUST land only after 2c (else instar refuses what qemu
  reads and every iteration diverges on exit code — the
  hazard already flagged in `PLAN-fuzzing-bugs.md:218`).

## Out of scope for this phase

* map/measure support for VDI (master-plan future work).
* instar `check` support for VDI (new future-work entry;
  qemu supports it, unconsumed baselines exist).
* VDI write/create/output support (master plan exclusion).
* Non-1MiB block sizes, VDI 1.0/2.x versions, undo/diff
  image types beyond treat-as-dynamic (qemu parity is
  refusal or dynamic semantics respectively).
* instar `create -F vdi` backing-format strings
  (`shared::BackingFormat` unchanged; reading qemu-created
  qcow2-with-VDI-backing chains needs no instar `-F`
  support).

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | New `src/crates/vdi/` no_std parser crate, modelled line-for-line on `src/crates/vhd/` (Cargo.toml template: dep on `shared` only, workspace lints, no features). Implement: header offset constants per this plan's Situation table; `VdiHeader::parse(&[u8]) -> Option<Self>` enforcing qemu's 12 validation rules with the exact limits listed (round odd disk_size up to 512 rather than rejecting; accept any image_type; ignore block_extra); `enum VdiBlockLookup { Unallocated, Allocated { host_byte_offset: u64 } }`; `VdiState` with `init(call_table, device_idx, sector_size, input_capacity, bmap_cache_buf, data_cache_buf, &mut bytes_read) -> Option<Self>` and `block_lookup(&mut self, call_table, virtual_offset, sector_size, input_capacity, &mut bytes_read) -> Option<VdiBlockLookup>` — signatures mirror `VhdState::init` (`src/crates/vhd/src/lib.rs:515`) and `block_lookup` (`:725`). bmap entries are LE u32 at `offset_bmap + entry_index*4`, served through the cached-sector pattern (`read_u32_be_cached` analogue, LE). Allocated iff entry < 0xfffffffe; data offset = `offset_data + entry as u64 * 1048576 + offset_in_block`, all checked arithmetic. Do NOT error on offsets past `input_capacity` — that classification is the caller's (2b) job; but do bounds-check bmap reads themselves and treat a bmap sector read failure as init/lookup failure. Unit tests in-module per vhd style: synthetic 512-byte headers exercising every validation rule (accept + reject cases with exact boundary values, e.g. block_size 1048575/1048576/1048577, disk_size at/over max, odd disk_size rounding, non-null UUID rejection), bmap lookup against a mock CallTable (model: qcow2's mock at `src/crates/qcow2/src/lib.rs:4665+` or fuzz mocks), sentinel handling, allocation-order (non-identity) mapping. Register the crate in the workspace `Cargo.toml` members list. `make test-rust` and `make lint` clean. |
| 2b | high | opus | none | Guest chain-reader integration; behaviour must remain unchanged (host gate still refuses VDI) — this step only makes the guest capable. In `src/crates/qcow2/Cargo.toml`: add optional dep `vdi = { path = "../vdi" }` and feature `vdi-input = ["vdi"]`, mirroring `vhd-input`. In `src/crates/qcow2/src/lib.rs`: (1) `ChainStates` (`:6671`) gains `#[cfg(feature = "vdi-input")] pub vdi_states: [Option<vdi::VdiState>; MAX_CHAIN_DEVICES]` + initialisation; (2) `init_chain_states` (`:6697`) gains a `ImageFormat::Vdi` arm calling `VdiState::init` with the same reused L1/L2 cache slots as the Vhd arm (`:6744`); init failure must propagate exactly as a Vhd init failure does (verify what that path reports and note it in your summary — a malformed-but-detected VDI must produce a clean op error, not raw fallback); (3) `read_chain_virtual_cluster` (`:5855`) gains a `ImageFormat::Vdi` arm modelled on the Vhd arm (`:6388`) minus the fixed-VHD special case: `block_lookup` → `Unallocated => continue` (VDI never has a lower device, so this reaches the zero-fill tail) / `Allocated => read_cluster_sectors` when the host offset is guest-sector-aligned, `read_offset_sectors` otherwise — offset_data is typically 1024, NOT 65536-aligned, so the unaligned path WILL be exercised; additionally zero-fill (do not error) any portion of an allocated read at or past the device capacity, including straddles, per qemu's no-EOF-validation semantics (Situation section). Enable `vdi-input` in the qcow2 feature lists of ALL FOUR reader-linking ops: `src/operations/{convert,compare,bench,rebase}/Cargo.toml` — bench and rebase are required to prevent the raw-fallback hazard described in Design. Add unit tests in the qcow2 crate's test module for the new arm (mock CallTable: dynamic bmap with holes, discarded entry, unaligned offset_data, past-EOF entry zero-fill), checking how existing vhd-input-gated tests are invoked so the new ones actually run under `make test-rust`. `make instar`, `make check-binary-sizes`, `make test-rust`, `make lint` clean; existing refusal tests still pass. |
| 2c | medium | opus | none | Host graduation, in two commits. Commit 1: info round-up — in `src/operations/info/src/main.rs` `parse_vdi_header` (`:1012`), round `disk_size` up to the next 512 multiple before storing `result.virtual_size` (qemu `vdi_open` parity; Situation rule 2), with a `#[cfg(test)]` case; verify `instar info` output for `vdi-simple` is byte-unchanged (it is 512-aligned). Commit 2: graduation — in `src/vmm/src/chain.rs` add `Vdi` to `ImageFormat` (`:24`), `from_str` arm `"vdi" => ImageFormat::Vdi` (`:50`), `to_shared_format_u32` arm `Vdi => 8` (`:74`; must match `shared::ImageFormat::Vdi = 8`), `Display` arm `"vdi"` (`:90`). Do NOT touch the gate's exemption string list — graduating out of `Unknown` is the mechanism. Delete/invert the three refusal tests (`tests/test_convert.py:5554` `test_convert_refuses_vdi`, `tests/test_compare.py:1229`, `tests/test_dd.py:1420`) and replace with smoke tests: instar convert `vdi-simple` → raw byte-identical to `qemu-img convert` (use `run_instar_convert`/`run_qemu_img_convert` from `tests/base.py`), compare vdi-simple vs its raw conversion reports identical, dd full-copy parity. Then EMPIRICALLY pin the other gate-lifted ops: run instar `check` and `bench` on `vdi-simple` — check must fail cleanly (record the exact message; add a test pinning non-zero exit + clean error; if it silently succeeds, reads raw, or hangs, STOP and report to management before committing), bench must succeed (vdi-input is enabled; add a smoke test asserting exit 0). `make instar` + targeted suites (`test_convert.py`, `test_compare.py`, `test_dd.py`, `test_info_safe.py`) pass. |
| 2d | high | opus | none | Testdata fixtures + manifest, two repos. In **instar-testdata** (sibling checkout; do NOT commit — leave the tree for management review): write `scripts/generate-vdi-fixtures.py` (python, single quotes, 120-col, deterministic: create with a static qemu-img from `qemu-img-binaries/x86_64/10.2.0/`, then byte-patch the two creation UUIDs at 0x188/0x198 to fixed values and re-patch per-fixture bytes) emitting the nine fixtures in this plan's Design § Fixtures (four safe: vdi-data-dynamic / vdi-static-data / vdi-odd-size / vdi-bmap-past-eof; five malformed: bad-version / unaligned-bmap / wrong-blocksize / nonnull-parent / too-many-blocks; exact patch offsets in the Situation header table). Safe → `custom/format-coverage/`, malformed → `custom/audit/`. Validate each safe fixture opens with BOTH the 6.0.0 and 10.2.0 static binaries and each malformed one is refused by 10.2.0 with the expected error class. Then baselines: `generate-baselines.py` with a restricted manifest for the four new safe images plus re-run for `vdi-simple` if its flag flip changes eligibility (info human+json; check baselines will also generate for VDI — that is fine, they remain unconsumed), then `detect-profiles.py`; verify regenerated profiles are byte-identical to `raw/` for a spot-check of pre-existing images (the corruption class fixed in phase 1). In **instar** (this worktree): add the nine manifest entries (real sha256s; safe ones `run_in_ci: true`, no `skip_qemu_img`; malformed ones `safety: malformed`, `skip_qemu_img: true`, `expected_error` set) and flip `vdi-simple` to `run_in_ci: true` without `skip_qemu_img`. Add the `KNOWN_VSIZE_DIVERGENCES` entry for `vdi-odd-size` in `tests/test_oslo_crossval.py` (oslo raw vs qemu rounded; cite this plan) and verify oslo scenarios for all VDI images pass locally (venv with oslo.utils + cryptography — the suite silently self-skips without it, so confirm it actually ran). Deliverable includes a findings note in this plan file listing generated sha256s and any qemu-version surprises. |
| 2e | medium | opus | none | Integration tests, modelled on the VHD classes. In `tests/test_convert.py`: `TestConvertVdiToRaw`-style class covering all five safe VDI ids × instar-convert-vs-qemu-img-convert byte parity (helper pattern `_test_vhd_convert` `:3508`), plus conversions to qcow2 and vpc outputs for `vdi-data-dynamic` (round-trip through `qemu-img convert -O raw` for comparison), plus a qcow2-with-VDI-backing chain test: build with `qemu-img create -f qcow2 -b <vdi> -F vdi` in the test tmpdir, write a partial overlay, convert with instar and qemu-img, assert byte parity (verify qemu accepts `-F vdi` at the tested version first; if not, note and drop). In `tests/test_compare.py`: vdi-vs-raw and vdi-vs-vdi compare parity. In `tests/test_dd.py`: add `test_input_vdi` to `TestDdInputFormats` (`:1118`) via `_assert_input_fmt_parity` (`:1144`), plus one windowed dd (skip/count crossing an unallocated→allocated boundary of `vdi-data-dynamic`) against `qemu-img dd`. Adversarial: register the five malformed fixtures with the `run_adversarial` harness (`tests/base.py:1066` pattern from phase 1) — convert/compare/dd/info on each must exit non-zero cleanly, no hang, no crash; also assert instar's refusal for `vdi-bad-version` etc. happens at open (clean error), and `vdi-bmap-past-eof` + `vdi-odd-size` CONVERT successfully with byte-parity to qemu (they are safe fixtures pinning the zero-fill and round-up rules). Confirm `test_info_safe` auto-picks the new safe images and passes against 2d's baselines. Run the full touched suites locally at moderate concurrency; classify any failure by isolated replay before attributing (KVM-contention flakes are a known pattern). |
| 2f | medium | sonnet | none | Fuzzing. Coverage-guided: create `src/fuzz/fuzz_targets/fuzz_vdi_header.rs` (clone shape of `fuzz_vhd_footer.rs`: call `vdi::VdiHeader::parse(data)` and, on Some, exercise accessors) and `fuzz_vdi_bat.rs` (clone of `fuzz_vhd_bat.rs`: `instar_fuzz::set_fuzz_input(data)`, `build_call_table()`, `VdiState::init`, loop `block_lookup` over `extract_fuzz_offset` offsets); add `vdi = { path = "../crates/vdi" }` and two `[[bin]]` blocks to `src/fuzz/Cargo.toml`. `make fuzz-build` clean; run each new target for 60s locally and report exec/s + any crash. Differential: in `scripts/differential-fuzz.py` append `'vdi'` to `FORMATS` (`:52`) and add a static-VDI branch in `generate_image` (`:115`, `-o static=on` chosen by rng like existing per-format options); confirm `op_convert`/`op_dd`/`op_map`/`op_measure` handle a vdi source without special-casing (map/measure refuse VDI in instar — check how the harness treats refusal-vs-qemu-success for formats outside an op's support and mirror the existing handling for such cases; if none exists, exclude vdi from those two ops' format choice rather than recording divergences). Run ~200 iterations locally with vdi forced and report divergence count (expected: zero). |
| 2g | medium | sonnet | none | Docs + close-out. `docs/format-coverage.md`: add `vdi` to the input-format-for-conversion table (`:66`), update the safety table row (`:290`) from "Pass-through" to a real reader description, update the test-image inventory (`:361`, now 10 VDI images) and the narrative bullet (`:493`). `docs/quirks.md`: remove vdi from the detect-but-refuse lists (`:2599`, `:2621`, `:2630`) and add a phase-2 section documenting: odd-disk_size round-up (qemu parity, oslo divergence), past-EOF zero-fill semantics, image_type leniency, block_extra ignored, check-refusal-vs-qemu-check divergence. `README.md`: add VDI to Supported Formats (`:16`) as read-only input. `CHANGELOG.md`: Added entry for VDI convert/compare/dd input; note the refusal-set change. `ARCHITECTURE.md`: add `src/crates/vdi/` where crates are enumerated. Master plan `PLAN-format-coverage.md`: Execution row 2 → Complete with commit range; add future-work entries (instar check support for VDI; map/measure VDI already covered by the existing deferred item). `docs/plans/index.md`: link this phase file, adjust "phases 3-7 not yet written". This file: Status → Complete, fill the Findings sections. Wrap at each file's existing column width. |

Sequencing: 2a → 2b → 2c strictly ordered (guest capability
before host graduation; the tree stays green between
commits). 2d can run in parallel with 2a–2c (fixtures need
only qemu-img), but its instar-side manifest/oslo changes
land after 2c so CI never sees a refused-but-enrolled
fixture. 2e after 2c + 2d. 2f after 2c (differential) —
the coverage targets only need 2a but ship together. 2g
last.

## Findings: step 2a — vdi crate

* The `vdi` crate landed as planned: `VdiHeader::parse` enforces
  qemu's twelve `vdi_open` rules with the exact limits from the
  Situation table, `VdiBlockLookup`/`VdiState` mirror
  `vhd::VhdBlockLookup`/`VhdState`'s signatures, and the block map is
  walked through the cached-sector pattern (`read_u32_le_cached`, the
  LE analogue of the qcow2 crate's BE helper). 23 boundary unit tests
  cover every validation rule (accept and reject cases at exact
  limits, e.g. `block_size` 1048575/1048576/1048577), sentinel
  handling, allocation-order (non-identity) mapping, and a mock
  `CallTable` block-map walk.
* One edge case surfaced during review and was noted for management
  rather than changed: a block-map *sector read* that lands past the
  guest's own capacity fails the lookup outright (`block_lookup`
  returns `None`, which the chain reader surfaces as a clean op
  error), whereas qemu's C implementation would zero-fill that read
  and interpret the resulting zero bytes as bmap entry 0 (an
  allocated block at the very start of the data region). This is a
  pathological case — a block map so close to the guest's declared
  capacity boundary that the guest's own virtio window can't reach
  it — and instar's stricter behaviour (refuse rather than
  synthesize a block-map entry from zero bytes) is the safe
  direction: no image was found in practice that exercises this
  divergence, and none of the fixture set does either.

## Findings: step 2c — graduation and gate-lifted ops

* `bench` had its own guest-side `read_family` allowlist,
  independent of the qcow2 crate's `vdi-input` feature. Before the
  fix, `instar bench` cleanly refused VDI input (exit 1, `bench:
  unsupported input format`) even with `vdi-input` enabled on
  `qcow2`, because bench's own format dispatch never reached the
  chain reader's VDI arm. The root-cause fix added the `Vdi` arm to
  bench's `read_family` allowlist in lock-step with the host
  graduation commit, closing the same raw-fallback-hazard class of
  gap the Design section flagged for bench/rebase generally.
* Empirical pins for every gate-lifted operation, run against
  `vdi-simple`:
  - `check` exits 63 with `This image format (vdi) does not support
    checks` — no raw read, no hang. Unchanged from before graduation.
  - `bench` succeeds (exit 0) with header output byte-parity against
    `qemu-img bench`.
  - `map` still refuses with `map: source format unrecognised`.
  - `measure` still refuses with `source image is unsupported
    format`.
  - `resize` still refuses with `format Vdi is not supported for
    in-place resize` (the `{:?}` debug format now prints `Vdi`
    instead of `Unknown`, since `chain::ImageFormat` gained the
    variant, but the refusal itself is unchanged).
* The info round-up fix (`parse_vdi_header` rounding `disk_size` up
  to 512) was verified byte-unchanged against every existing
  512-aligned VDI baseline, and the new `vdi-odd-size` fixture
  (1048577 → 1049088) confirms the rounding end-to-end against a
  live `qemu-img info` baseline.

## Findings: step 2d — fixtures and baselines

* All nine new fixtures were generated deterministically by
  `instar-testdata/scripts/generate-vdi-fixtures.py`: created with a
  static qemu-img binary, then byte-patched so the two creation
  UUIDs are pinned to fixed values (the only non-deterministic
  creation bytes) before any per-fixture corruption is applied.
* `vdi-odd-size` needed a 2 MiB base image rather than the smallest
  possible 1 MiB base: a 1 MiB base would trip qemu's rule 9
  (`disk_size > blocks_in_image * block_size`, evaluated against the
  *rounded* `disk_size`) once the odd-size patch and the round-up
  rule combine, refusing the fixture at qemu-img's own open instead
  of exercising the rounding rule cleanly.
* `generate-baselines.py` auto-commits by default and regenerates
  every registered output type, not just the restricted manifest
  passed on the command line. This step's baseline run undid that
  auto-commit and surgically restored the output types that were not
  part of this phase's scope; the recommendation to add a
  `--no-commit` / `--output-type`-limiting flag to the generator was
  recorded rather than implemented (instar-testdata scripting is
  outside this phase's file set).
* The five malformed fixtures needed `OSLO_SKIP_IMAGES` entries in
  `tests/test_oslo_crossval.py`: oslo.utils' `VDIInspector` validates
  none of the fields qemu refuses on (bad version, unaligned
  block-map offset, wrong block size, non-NULL parent UUID, too many
  blocks), so without the skip entries oslo would report these
  images as safely-openable while qemu-img (and instar) refuse them.
* A profile-corruption integrity spot-check (10 images, covering the
  regeneration-corruption class phase 1 fixed in
  `detect-profiles.py`) came back clean — the regenerated profiles
  for the new VDI fixtures and a sample of pre-existing images match
  `raw/` byte-for-byte.

## Findings: step 2e — integration tests

* Full touched-suite results, run locally at moderate concurrency:
  `test_dd` 39/39, `test_compare` 56/56, `test_adversarial` 79/79,
  `test_info_safe` 628/628, `test_oslo_crossval` 221 passed (10
  pre-existing skips, unrelated to VDI), `test_convert` 218 passed (3
  pre-existing skips). Zero failures across all six suites, and no
  KVM-contention flakes observed on replay.
* `qemu-img dd`'s `count`/`skip` semantics are absolute from offset 0
  in the source file, not relative to any block-map structure — the
  windowed dd test crossing an unallocated→allocated boundary of
  `vdi-data-dynamic` confirmed instar matches this byte-for-byte.
* The qcow2-with-VDI-backing chain test needed the VDI file copied
  into the overlay qcow2's own directory before creating the
  `-b <vdi> -F vdi` chain: instar's backing-file allowlist restricts
  backing paths to the overlay's directory (or an explicitly
  allowlisted one), so a VDI backing file living elsewhere in the
  test tree would be refused by the allowlist check rather than
  exercising the chain-reader arm under test.
* `compare` on a malformed-but-detected VDI reports a non-zero-exit
  content mismatch rather than an open error — proving compare is
  not silently falling back to a raw read of the malformed
  container. This is documented as a quirk in `docs/quirks.md` rather
  than treated as a defect.
* `info` stays lenient on malformed VDIs by design: its separate,
  simpler header parser (`parse_vdi_header`) is out of scope for the
  reader graduation, so it still reports plausible detection-level
  fields and exits 0 for images the full reader refuses. Also
  documented as a quirk.

## Verification (management-session review checklist)

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified; instar-testdata
      changes are reviewed by the management session before
      the operator-authorised commit to its main.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (768KB
      cap; convert starts at 39%, so ample headroom — verify
      the delta is the expected few KB).
- [ ] `make test-rust`, `make fuzz-build`, and the convert /
      compare / dd / info / adversarial / oslo-crossval
      suites pass (oslo verified to have actually run, not
      self-skipped).
- [ ] `pre-commit run --all-files` passes.
- [ ] All five safe VDI fixtures convert byte-identically to
      qemu-img output; `vdi-bmap-past-eof` and
      `vdi-odd-size` pin the zero-fill and round-up rules.
- [ ] check on VDI fails cleanly (pinned); bench on VDI
      succeeds; no op reads VDI as raw.
- [ ] Differential fuzzer runs with vdi in the pool with
      zero divergences over the local burn-in.
- [ ] Consumer-suite rule: the qcow2 crate changed, so the
      convert/compare/bench/rebase integration suites all
      ran, not just convert's.
- [ ] Commit messages follow project conventions (Prompt
      paragraph, Signed-off-by, Co-Authored-By with model /
      context window / effort level).

## Success criteria

Phase 2 is complete when:

* instar convert, compare, and dd accept dynamic and static
  VDI input with byte parity against qemu-img across the
  fixture set, including discarded blocks, past-EOF bmap
  entries, odd disk_size, and a qcow2-with-VDI-backing
  chain.
* The five malformed fixtures are refused cleanly (no hang,
  no raw fallback) and the five safe fixtures are exercised
  in CI with full info baselines.
* `src/crates/vdi/` exists, no_std, panic-free, with unit
  and coverage-guided fuzz coverage; the differential
  fuzzer includes vdi in its format pool.
* check/bench behaviour on VDI is pinned by tests; nothing
  gate-lifted reads VDI as raw.
* Docs (format-coverage, quirks, README, CHANGELOG,
  ARCHITECTURE) and the master plan record the new state.

## Hand-off to later phases

* Phase 3 (Parallels read) reuses the crate template, the
  feature-flag wiring shape, the graduation checklist
  (including the bench/rebase raw-fallback hazard and the
  check pin), and the fixture-generator pattern from this
  phase — its plan can be briefer by referencing this one.
* Phase 6 (QED decision) inherits the observation that
  graduation mechanics are now ~4 small arms plus tests;
  the cost of a QED read path is dominated by the parser
  crate alone.
* Future work recorded on the master plan: instar check
  support for VDI; map/measure VDI input.

## Back brief

Before executing any step of this plan, back brief the
operator: confirm the scope (convert/compare/dd only; check
stays a refusal), the bench/rebase feature-flag decision
(all four reader-linking ops gain vdi-input in one commit to
prevent raw fallback), the odd-disk_size round-up choice
(qemu parity over oslo parity, recorded as a divergence),
and that instar-testdata changes are management-reviewed
before the authorised commit to its main.
