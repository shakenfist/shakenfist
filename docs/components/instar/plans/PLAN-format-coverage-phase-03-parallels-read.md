# Format coverage phase 3: Parallels convert-from (read path)

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

Parallels is detect + info only today (phase 1). The phase-1
consumer gate refuses it for convert/compare/dd; only convert
has a refusal test
(`tests/test_convert.py` `test_convert_refuses_parallels`;
compare and dd never got one — a phase-1 gap this phase
closes by replacing refusals with positive coverage). This
phase graduates Parallels to a full read format for
convert/compare/dd (+ bench), mirroring the just-completed
VDI phase.

**This plan deliberately references
[PLAN-format-coverage-phase-02-vdi-read.md](/components/instar/plans/PLAN-format-coverage-phase-02-vdi-read/)
for everything structural** — the chain-reader pattern, the
graduation mechanism, the raw-fallback hazard rationale, the
bench `read_family` allowlist, the fixture/baseline/oslo
workflow, and the fuzzing shapes are identical and are not
restated here. Only Parallels-specific facts and deltas
follow. Research was done 2026-07-18 by two grounding
passes: a codebase survey of the phase-1 Parallels state and
an empirical verification against qemu `block/parallels.{c,h}`
(7.0.0 source read in full, master fetched and diffed) with
every load-bearing claim verified by running the static
qemu-img matrix binaries (6.0.0 / 7.0.0 / 8.2.0 / 9.0.0 /
9.2.4 / 10.2.0 spot set).

### Parallels format facts (all empirically verified)

Header: 64 bytes, all scalars little-endian; the BAT (u32 LE
entries) begins immediately at offset 64.

| Offset | Field | Notes |
|--------|-------|-------|
| 0x00 / 0 | magic[16] | `WithoutFreeSpace` (v1) or `WithouFreSpacExt` (v2/ext), not NUL-terminated |
| 0x10 / 16 | version u32 | must be 2 |
| 0x14 / 20 | heads u32 | geometry, unused by reader |
| 0x18 / 24 | cylinders u32 | geometry, unused by reader |
| 0x1c / 28 | tracks u32 | sectors per cluster; cluster_size = tracks << 9 |
| 0x20 / 32 | bat_entries u32 | BAT entry count |
| 0x24 / 36 | nb_sectors u64 | virtual size in sectors; **v1 masks to low 32 bits**, v2 uses all 64 (byte-patch verified: same field reads 2 MiB under v1 magic, 2 TiB under v2) |
| 0x2c / 44 | inuse u32 | 0x746f6e59 = opened-dirty |
| 0x30 / 48 | data_off u32 | in sectors; **irrelevant to reads** (write-path allocation frontier only; garbage values verified harmless) |
| 0x34 / 52 | flags u32 | unused by reader |
| 0x38 / 56 | ext_off u64 | format-extension offset in sectors; 0 = none |

BAT semantics — the central per-magic gotcha:

* `host_sector = bat[idx] * off_multiplier + (guest_sector %
  tracks)` where **off_multiplier = 1 for v1** (BAT values
  are sector numbers) and **off_multiplier = tracks for v2**
  (BAT values are cluster indices). Verified byte-level on
  both fixtures: v2 BAT `[1,2,3,4]` and v1 BAT
  `[0x80,0x100,0x180,0x200]` decode to the same file
  offsets (data at 0x10000, tracks=128).
* BAT value 0 = unallocated, reads as zeros. Guest offsets
  at or beyond `bat_entries * cluster_size` also read as
  zeros — qemu has **no** BAT-vs-nb_sectors consistency
  check (verified: nb_sectors inflated to 16 MiB over a
  2 MiB BAT converts fine, tail zeros).
* Reads never cross a cluster boundary in one operation
  (qemu clamps per cluster).

qemu open-time validation (RO path — the only one instar
uses), with exact error strings:

1. magic not one of the two, or version != 2 → "Image not
   in Parallels format".
2. `tracks == 0` → "Invalid image: Zero sectors per track".
3. `tracks > INT32_MAX / 513` (4186127; step-3d empirical
   correction — 4185447 opens cleanly, 4186128 is the
   smallest refused value) → "Invalid image:
   Too big cluster".
4. `bat_entries > INT_MAX / 4` (0x3fffffff) → "Catalog too
   large".
5. `ext_off != 0` → qemu parses the format extension and
   refuses on bad magic ("Wrong parallels Format Extension
   magic: ... expected: 0xab234cef23dcea87"). A huge
   ext_off (≥ INT64_MAX>>9) is refused only by newer qemu
   ("Invalid image: Too big offset") — the one genuine
   open-refusal drift across the matrix; avoid fixtures
   that depend on it.
6. `inuse == 0x746f6e59` → **read-only open succeeds** and
   converts correctly; only read/write opens are refused.
   instar always reads RO, so dirty images must be
   readable.

No data_off validation and no file-length validation affect
reads. **Past-EOF/truncation semantics (verified identical
on 6.0.0/7.0.0/10.2.0): out-of-image BAT entries, straddling
truncation, truncated BAT, even a 30-byte file — all read as
zeros wherever bytes are missing, never an error.** Same
parity rule as VDI. Step-3d addendum: qemu 8.1.0–8.1.5
refuse a past-EOF BAT entry at OPEN ("Offset ... in BAT[n]
entry is larger than file size") — a regression window fixed
in 8.2.0. instar zero-fills uniformly, diverging from 8.1.x
only; the parallels-bat-past-eof baselines record the 8.1.x
refusals faithfully (they split a profile-8-1-0 bucket).

Other verified facts:

* `qemu-img create -f parallels` writes the v2/ext magic,
  default cluster 1 MiB (tracks=2048), honours
  `-o cluster_size` (rounded to 512), and is
  **byte-deterministic** (repeat creates and repeat
  `convert -O parallels` are cmp-identical). No qemu-io in
  testdata; data-bearing fixtures come from
  `convert -f raw -O parallels` of sparse raws.
* `qemu-img info` prints **no cluster_size and no
  format-specific block** for parallels; only the generic
  8.0 child-node drift. The read phase must not change info
  output (see Design).
* `qemu-img check` supports parallels but **crashes on
  10.2.0** (assertion in parallels_check_duplicate) for
  out-of-image BAT entries where 6.0.0 reports cleanly — a
  real qemu regression. instar's refuse-to-check stance is
  correct and stays.
* oslo.utils has no parallels inspector: every parallels
  image detects as raw with
  `virtual_size = min(file_size, 262144)` (verified rule
  across five file sizes). Divergence entries for new
  fixtures follow that formula.
* Both existing fixtures (`parallels-v1`, `parallels-v2`,
  327680 bytes, data-bearing: 4 allocated 64 KiB clusters
  filled 0x11/0x22/0x33/0x44 then a hole to 2 MiB) convert
  to the same raw md5 on every spot-checked version; the
  phase-1 6.x driver rebuild is confirmed effective.
* Host `qemu-img` (10.0.11) creates and reads parallels —
  differential-fuzz suitable.

### Codebase state left by phase 1 (survey facts)

* Detection: both magics + version==2 in
  `src/shared/src/format_detection.rs:192-206`;
  `shared::ImageFormat::Parallels = 13`.
* Info: `parse_parallels_header`
  (`src/operations/info/src/main.rs:1153`) reads ONLY
  nb_sectors (with the v1 mask) and sets virtual_size; no
  ParallelsInfo struct, generic `send_info_result`,
  `result.cluster_size` stays 0. A reader additionally
  needs tracks, bat_entries, and the BAT — the new crate
  parses the header itself.
* Host emitters: "parallels" is in the info child
  512-rounding set (`src/vmm/src/main.rs:1481/:1542`) and
  the JSON dirty-flag suppression set (`:1668`); both are
  info-only and unaffected by the read path, EXCEPT the
  cluster-size question below.
* `chain::ImageFormat::from_str` has no "parallels" arm →
  Unknown → gate refusal. Graduation mirrors VDI:
  variant + from_str + `to_shared_format_u32 => 13` +
  Display "parallels".
* bench `read_family`
  (`src/operations/bench/src/main.rs:264`) is still the
  only guest-side allowlist (re-verified by grep) and needs
  a Parallels arm in lock-step.
* check: after graduation the host gate lifts and the guest
  check op's default arm reports not-supported (the VDI
  precedent: clean exit 63) — pin empirically.
* Manifest: parallels-v1/v2 are safe, run_in_ci, no
  skip_qemu_img; info baselines exist (raw + profiles);
  check baseline trees do NOT exist yet for parallels.
* Oslo: KNOWN_FORMAT_DIVERGENCES parallels→raw and
  KNOWN_VSIZE_DIVERGENCES (262144 vs 2 MiB) already
  recorded for both fixtures.
* Differential fuzz: FORMATS has vdi; op_map gates
  `('raw', 'vdi')` and op_measure gates 'vdi' — parallels
  needs identical gating.

## Design

### Scope

Identical to phase 2 with s/VDI/Parallels/: convert, compare,
dd (and bench) gain Parallels input, both magics. map,
measure, resize, check stay refusals; check's post-graduation
behaviour is pinned (expected: same clean not-supported exit
as VDI). No write/create/output support.

### Reader semantics: exact qemu parity

The new `src/crates/parallels/` crate implements the RO
open checks (rules 1–4 above) with qemu's exact limits.
Additional rules, each pinned by a fixture:

* **off_multiplier by magic**: 1 for `WithoutFreeSpace`,
  tracks for `WithouFreSpacExt`. Per-magic unit tests are
  mandatory — this is the failure mode the phase's whole
  fixture design guards.
* **nb_sectors masking**: low 32 bits under the v1 magic,
  full u64 under v2; virtual size = sectors × 512.
* `inuse`-dirty images are readable (instar is always RO);
  never refuse on inuse.
* `data_off` is parsed but never used in read math.
* **ext_off != 0 → refuse at init.** qemu would parse the
  extension (dirty bitmaps) read-only; instar refuses until
  a real need appears. This is a deliberate, documented
  divergence (a valid-extension image qemu reads, instar
  refuses cleanly) recorded in quirks and future work.
  Rationale: no shipped or creatable fixture has ext_off
  set, the extension adds no read-path data, and silently
  ignoring it risks misreading images whose extensions
  matter.
* BAT value 0, guest offsets beyond BAT coverage, and any
  read touching bytes at or past the device capacity —
  including straddles — read as zeros, never error
  (identical arm-level clamping to the VDI arm).
* No BAT-vs-nb_sectors consistency check; no file-length
  checks at init.

### The cluster-size plumbing question (this phase's one new
architectural item)

The chain reader's chunking relies on the device's
`ChainDeviceInfo.cluster_size`, which the host copies from
the guest info result. VDI worked for free because qemu (and
therefore instar's info) reports cluster_size for VDI.
**qemu prints no cluster_size for parallels**, and instar's
parser leaves it 0 — but the reader needs real cluster
boundaries (cluster_size is user-settable via
`-o cluster_size`, so chunks could otherwise straddle
non-contiguous clusters).

Resolution: the guest info op sets
`result.cluster_size = tracks << 9` internally, and the host
emitters suppress cluster_size for the "parallels" format
string in both human and JSON output — the exact mechanism
phase 1 used for the dirty-flag suppression set, keeping
info output byte-identical to qemu. Step 3c must verify with
a full `test_info_safe` run (zero regressions) plus a manual
check that the suppression really is format-gated, not
value-gated. Step 3b must additionally confirm (by reading
the chunking code, not assuming) that chunk boundaries never
exceed the device cluster size once it is populated, and
must add a small-cluster fixture test (4 KiB clusters,
tracks=8) to pin boundary behaviour end-to-end.

### Fixtures and baselines

New fixtures via a deterministic
`generate-parallels-fixtures.py` in instar-testdata
(creation is byte-deterministic; no UUID patching needed).
Safe (qemu-readable, full baselines, convert parity):

* `parallels-data-v2` — convert of a sparse patterned raw
  (≥ 262144 bytes file size so the oslo vsize divergence is
  the constant 262144), then two BAT entries swapped (with
  their data clusters) to pin non-contiguous decoding.
* `parallels-data-v1` — the same image byte-patched to the
  v1 magic with the BAT rewritten to sector values
  (× tracks), pinning off_multiplier=1 and the 32-bit mask.
* `parallels-inuse` — inuse set to 0x746f6e59; reads
  identically (RO), pins never-refuse-dirty.
* `parallels-bat-past-eof` — one BAT entry pointing far
  past EOF; qemu zero-fills that cluster, exit 0.
* `parallels-cluster-4k` — created with
  `-o cluster_size=4096` (tracks=8), data in scattered
  clusters; pins small-cluster chunk-boundary handling.

Malformed (qemu refuses at open; skip_qemu_img,
expected_error from the verified strings): `parallels-zero-
tracks`, `parallels-huge-tracks`, `parallels-huge-catalog`,
`parallels-ext-bad-magic`. Do NOT build the huge-ext_off
fixture (version-drifted refusal). The existing
parallels-v1/v2 fixtures stay as-is (already safe +
run_in_ci) and join the convert-parity matrix.

Baselines: info for the five new safe images (the check
COMMANDS tree will also generate; harmless/unconsumed — note
qemu check is crash-prone on adversarial parallels, so if
the generator's check pass hits the 10.2.0 assertion on
parallels-bat-past-eof, record the non-zero/crash meta
faithfully and flag it in the findings). detect-profiles
with the phase-2 lessons applied (--no-commit posture,
integrity spot-check).

Oslo: KNOWN_FORMAT_DIVERGENCES entries ('parallels', 'raw')
for each new safe fixture and KNOWN_VSIZE_DIVERGENCES
entries computed as `min(file_size, 262144)` vs the real
virtual size; malformed fixtures join OSLO_SKIP_IMAGES.

### Fuzzing

`fuzz_parallels_header` + `fuzz_parallels_bat` mirroring the
vdi pair (the bat target must exercise BOTH magics so
off_multiplier is fuzzed). Differential: FORMATS +=
'parallels' with an rng-chosen cluster_size option
(including small values) some of the time; gate op_map and
op_measure for 'parallels' exactly as for 'vdi'; op_convert
and op_dd flow through. Land only after 3c. Burn-in ~200
forced iterations, zero divergences expected.

## Out of scope for this phase

* Parallels format extensions / dirty bitmaps (ext_off != 0
  refused; future work).
* instar check support for parallels (qemu's own check
  asserts on newer versions for out-of-image BAT entries —
  recorded; refusal stays).
* map/measure support (master-plan deferred item).
* Parallels write/create/output; parallels-with-backing
  read-through (qemu code path exists but is unreachable
  via qemu-img; refused implicitly since instar builds
  1-device chains for parallels).
* v1 images with data_off == 0 beyond what the parser
  ignores anyway (data_off is unused by reads).

## Step-level guidance

Steps mirror phase 2 exactly; briefs below give only the
Parallels deltas and assume the implementing agent reads the
phase-2 plan's corresponding step first.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | New `src/crates/parallels/` crate modelled on `src/crates/vdi/` (read it and its phase-2 brief first). Header constants per this plan's table; `ParallelsHeader::parse(&[u8]) -> Option<Self>` enforcing rules 1-4 with qemu's exact limits (tracks != 0, tracks <= 4186127, bat_entries <= 0x3fffffff, both magics + version 2) plus the ext_off != 0 refusal (Design); store `off_multiplier` (1 vs tracks by magic), `cluster_size = tracks << 9` (checked shl), and `virtual_size = (nb_sectors masked per magic) * 512` (checked). `ParallelsState::init`/`block_lookup` with the VdiState signatures: BAT entries are LE u32 at 64 + 4*idx via the cached-sector macro; entry 0 or idx >= bat_entries => Unallocated; else host_byte_offset = (entry as u64 * off_multiplier as u64) * 512 + offset_in_cluster, all checked. No file-length or data_off checks. Unit tests: every validation boundary; **per-magic BAT decoding equivalence** (v1 [0x80,0x100..] and v2 [1,2..] with tracks=128 must produce identical offsets — the empirical fixture values); nb_sectors 32-bit mask under v1 vs full width under v2 (0x100001000 -> 2 MiB vs 2 TiB); inuse set still parses; data_off garbage ignored; ext_off nonzero refused. Workspace member registration; make test-rust/lint/instar clean. |
| 3b | high | opus | none | Guest integration mirroring phase-2 step 2b (read that brief + commit 80b30f4's diff first): `parallels-input = ["parallels"]` feature + optional dep in src/crates/qcow2/Cargo.toml; `ChainStates.parallels_states`; `init_chain_states` arm (same cache-slot reuse; init failure propagates like Vdi/Vhd); `read_chain_virtual_cluster` arm with identical capacity-clamped zero-fill (past-EOF and straddle) and the aligned/unaligned read split. **Before writing the arm, read the chunking code and state in your report how chunk sizes are bounded relative to ChainDeviceInfo.cluster_size — the arm may assume a chunk never crosses a parallels cluster boundary ONLY if the code guarantees it once cluster_size is populated (step 3c does the populating; coordinate: your unit tests must set cluster_size in the mock ChainDeviceInfo).** If the guarantee doesn't hold, clamp in the arm and report. Enable the feature in convert/compare/bench/rebase Cargo.tomls; extend the Makefile qcow2 test line to `"create,vdi-input,parallels-input"`. Unit tests per the 2b set plus: both magics through the arm, a small-cluster (tracks=8) image with scattered allocation, chunk at a cluster boundary. Behaviour unchanged this step (gate still refuses); the three-suite refusal state must still hold. make instar/check-binary-sizes/test-rust/lint clean; report binary deltas. |
| 3c | high | opus | none | Graduation + info cluster-size plumbing, three commits. Commit 1 (info): `parse_parallels_header` additionally reads tracks (offset 28) and sets `result.cluster_size = tracks << 9`; host emitters `print_info_result`/`print_info_result_json` (src/vmm/src/main.rs ~:1135/:1446) suppress cluster_size for format "parallels" in BOTH human and json paths, mirroring the dirty-flag suppression mechanism (~:1668) — verify the current cluster_size rendering is value-gated (0 = hidden) and make the parallels suppression format-gated so a real tracks value stays hidden; full test_info_safe must pass byte-identical (zero regressions). Commit 2 (graduation): chain.rs Parallels variant + from_str "parallels" + to_shared_format_u32 => 13 (verify against shared) + Display; bench read_family `Parallels => Some(6)` with doc-comment update. Commit 3 (tests): delete test_convert_refuses_parallels; add convert/compare/dd parity smoke tests on parallels-v1 AND parallels-v2 (both magics; model on the VDI smokes from commit 3cdf925); pin check on parallels (expect the VDI-style clean not-supported exit — record exact code/message; if it silently succeeds or hangs, STOP for management review) and bench success; verify map/measure/resize refusals unchanged (record messages). Run the touched suites + test_info_safe. |
| 3d | high | opus | none | Testdata + manifest, mirroring phase-2 step 2d (read its brief and findings first — especially the detect-profiles auto-commit hazard: use --no-commit posture and surgically limit output types; integrity spot-check pre-existing images). In instar-testdata (do NOT commit): `scripts/generate-parallels-fixtures.py` emitting the nine fixtures per this plan's Design (five safe incl. the BAT-swap non-contiguity patch and the v1 magic+BAT rewrite; four malformed with the verified error strings; creation is byte-deterministic so no UUID patching); validate every safe fixture on 6.0.0 AND 10.2.0 (info + convert md5 agreement) and every malformed one refused by 10.2.0 with the expected error. Generate info baselines for the five new images (restricted manifest); if the check-tree generation crashes qemu on parallels-bat-past-eof (known 10.2.0 assertion), record faithfully and flag. detect-profiles + integrity spot-check. In instar: nine manifest entries + oslo updates per Design (divergence values computed by the min(file_size, 262144) rule — state each pair in your report); verify oslo actually ran (venv with cryptography). |
| 3e | medium | opus | none | Integration tests mirroring phase-2 step 2e (read its brief + commit cf213ed first): convert-to-raw parity for all seven safe parallels ids (five new + v1/v2), convert to qcow2 and vpc for parallels-data-v2 (flatten-compare), windowed dd crossing a hole boundary, compare identical + differing pairs, adversarial refusals for the four malformed ids (convert/compare/dd non-zero clean, no hang; info stays lenient per the established posture — verify and document), a small-cluster end-to-end convert (parallels-cluster-4k), and the inuse fixture converting identically to its clean twin. Full suite runs: test_convert, test_compare, test_dd, test_adversarial, test_info_safe, test_oslo_crossval, plus test_bench and test_rebase (consumer-suite rule). Isolated re-run before believing any failure (KVM-contention pattern). |
| 3f | medium | sonnet | none | Fuzzing mirroring phase-2 step 2f (read its brief + commit 61ec049 first): fuzz_parallels_header + fuzz_parallels_bat (bat target must derive the magic choice from fuzz bytes so both off_multiplier paths are exercised); fuzz Cargo wiring; make fuzz-build + 60s local runs each (report exec/s, crashes blocking). Differential: FORMATS += 'parallels'; generate_image branch choosing `-o cluster_size` from [4096, 65536, 1048576] some of the time; gate op_map ('raw','vdi','parallels') and op_measure ('vdi','parallels') with comments citing this plan; ~200-iteration forced burn-in, zero divergences expected, isolated replay for any hit. |
| 3g | medium | sonnet | none | Docs + close-out mirroring phase-2 step 2g (read its brief + commit 0b2e405 first): format-coverage.md input table + safety row + fixture inventory (11 parallels images) + narrative; quirks.md — remove parallels from the refused/collapse lists (:2599/:2635 area, following the vdi edit pattern), fix the ":2671 parallels refuses correctly in every op" claim, add a phase-3 section (off_multiplier per magic; v1 32-bit mask; inuse readable RO; data_off ignored; past-EOF zero-fill; ext_off refusal divergence vs qemu; cluster-size internal-report-but-suppressed mechanism; qemu check assertion regression note); README (parallels read-only input); CHANGELOG; ARCHITECTURE (crates/parallels/); master plan row 3 Complete + future-work bullets (parallels format extensions/dirty bitmaps; the qemu check crash upstream note); index.md phases 1-3; this file Status Complete + fill Findings sections from the step reports. Consistency grep for stale parallels claims. |

Sequencing: 3a → 3b → 3c strictly; 3d parallel with 3a-3c
(fixtures need only qemu-img; its instar-side manifest/oslo
edits land after 3c); 3e after 3c + 3d; 3f after 3c; 3g
last.

## Findings: step 3a — parallels crate

* The `parallels` crate landed as planned: `ParallelsHeader::parse`
  enforces qemu's RO open rules with the exact limits from the
  Situation table (tracks non-zero and under the cap, bat_entries
  under the catalog limit, a recognised magic, version 2, `ext_off ==
  0`), and stores `off_multiplier` (1 for the v1 `WithoutFreeSpace`
  magic, `tracks` for the `WithouFreSpacExt` ext magic),
  `cluster_size = tracks << 9`, and the per-magic-masked
  `virtual_size`. `ParallelsState::init`/`block_lookup` mirror the
  `VdiState` signatures, walking the BAT through the same
  cached-sector pattern the vdi and vhd crates use. 20 boundary unit
  tests cover every validation rule (accept/reject at exact limits),
  the per-magic BAT decoding equivalence (v1 `[0x80,0x100,...]` vs v2
  `[1,2,...]` at `tracks=128` producing identical offsets), the
  `nb_sectors` 32-bit mask under v1 vs full-width under v2, `inuse`
  parsing without refusal, `data_off` garbage being ignored, and
  `ext_off` nonzero being refused.
* The magic constants (`WithoutFreeSpace` / `WithouFreSpacExt`) are
  not redefined in the new crate — it imports
  `shared::format_detection::PARALLELS_MAGIC_V1` and
  `PARALLELS_MAGIC_V2` (aliased locally to `PARALLELS_MAGIC_EXT` for
  clarity) so the detector and the reader can never drift apart on
  what a valid magic is.
* The crate's initial `PARALLELS_TRACKS_MAX` came from the plan's
  planning-stage arithmetic (`INT32_MAX / 513`, computed as
  4185446) and was later corrected to the empirically-verified
  4186127 by step 3d's fixture validation (commit `8dbf89f`) — the
  boundary unit tests reference the constant symbolically, so no test
  rewrite was needed for the correction, only the constant itself and
  the plan's own Situation table.

## Findings: step 3c — graduation, info plumbing, gate-lifted ops

* The cluster-size plumbing landed exactly as designed: the guest
  `info` op's `parse_parallels_header` now additionally reads `tracks`
  and sets `result.cluster_size = tracks << 9` internally, while both
  host emitters (`print_info_result` and `print_info_result_json`)
  suppress `cluster_size` for the `"parallels"` format string — a
  format-gated suppression (not value-gated), mirroring the existing
  dirty-flag JSON suppression mechanism. This was verified
  byte-identical against the 10.2.0 info baselines and a full
  `test_info_safe` run with zero regressions before graduation
  landed, confirming the suppression hides even a real nonzero
  `tracks` value rather than relying on it happening to be zero.
* Step 3b's chunking analysis (read ahead of writing the chain-reader
  arm, per the plan's coordination note) found that compare/bench/
  rebase chunk sizes can span multiple small Parallels clusters —
  unlike VDI, where the chain reader's 64 KiB-capped aligned chunks
  always divide VDI's fixed 1 MiB blocks and so never cross a block
  boundary. Because Parallels' cluster size is user-settable
  (`-o cluster_size`) and can be smaller than a chunk, the Parallels
  reader arm cannot inherit the VDI arm's "one lookup per chunk"
  shape; it instead walks per-cluster internally using the
  header-derived `cluster_size`, a documented deviation from the VDI
  arm's structure. `parallels-cluster-4k` (tracks=8, 4 KiB clusters)
  pins this end-to-end.
* Graduation (chain.rs `Parallels` variant, `from_str`,
  `to_shared_format_u32 => 13`, `Display`) and the bench
  `read_family` allowlist arm (`Parallels => Some(6)`) landed
  together, closing the same raw-fallback-hazard class of gap the
  VDI phase found for bench's independent format dispatch.
* Empirical pins for every gate-lifted or gate-unchanged operation,
  run against the parallels fixtures:
  - `check` exits 63 cleanly with `This image format (parallels) does
    not support checks` — no raw read, no hang, unchanged from
    pre-graduation.
  - `bench` succeeds (exit 0) with header output byte-parity against
    `qemu-img bench`.
  - `map` still refuses with `source format unrecognised`.
  - `measure` still refuses with `source image is unsupported
    format`.
  - `resize` still refuses with `format Parallels is not supported
    for in-place resize` — `chain::ImageFormat`'s own `{:?}` debug
    string now prints `Parallels` instead of `Unknown` since the
    variant exists, but the refusal test itself is unchanged.

## Findings: step 3d — fixtures and baselines

* All nine new fixtures were generated deterministically by
  `instar-testdata/scripts/generate-parallels-fixtures.py`. Unlike
  VDI, Parallels image creation needed no UUID-patching step to reach
  byte-determinism — `qemu-img create -f parallels` and
  `convert -O parallels` are already cmp-identical across repeat
  runs, so the generator only applies each fixture's specific
  byte-patch (magic swap, BAT rewrite, header field corruption) on
  top of a plain create/convert.
* Step 3a's planning-stage tracks-cap arithmetic (4185446) was found
  too low by one boundary step during fixture validation: qemu opens
  `tracks=4185447` cleanly and refuses `tracks=4186128` as the
  smallest rejected value, giving a true cap of 4186127. The
  `parallels-huge-tracks` fixture was built at the corrected boundary
  and the crate constant was fixed in lock-step (commit `8dbf89f`,
  folded back into step 3a's crate and the plan's Situation table).
* Fixture validation also surfaced a version-drift the planning spot
  set had missed: qemu 8.1.0 through 8.1.5 refuse a past-EOF BAT
  entry **at open** (a regression window closed again in 8.2.0),
  while every other spot-checked version zero-fills it like instar
  does uniformly. This forced a new `profile-8-1-0` baseline bucket,
  split out from its neighbouring profile so the two refusing
  versions don't corrupt a shared baseline; the new bucket received
  copies of `profile-8-0-0`'s hand-maintained LUKS goldens so that
  pre-split LUKS coverage wasn't silently dropped by the split. This
  also motivated `tests/test_info_safe.py`'s new general mechanism
  (commit `30ecf77`) to skip scenario generation for any profile
  whose baseline meta records a non-zero qemu-img return code —
  `parallels-bat-past-eof` is the first safe fixture to be refused by
  qemu on only a subset of profiles, so the mechanism needed to be
  general rather than a one-off special case.
* oslo divergence pairs were computed by the `min(file_size, 262144)`
  rule and confirmed live against the real oslo.utils
  `RawFileInspector` fallback: `(262144, 2097152)` for
  `parallels-data-v2`, `parallels-data-v1`, `parallels-inuse`, and
  `parallels-bat-past-eof` (all four share the same 2 MiB virtual
  size and an on-disk size at or above the 262144 cap), and
  `(20480, 262144)` for `parallels-cluster-4k` (a 20480-byte on-disk
  file below the cap, so oslo reports the raw file size, against a
  256 KiB virtual size).
* No qemu check crash was hit during this step's own baseline
  generation run; the known 10.2.0 `parallels_check_duplicate`
  assertion is a property of adversarial out-of-image BAT entries
  under `qemu-img check`, not of `generate-baselines.py`'s info-only
  baseline pass, so it did not need to be worked around here (it is
  the reason `instar check` continues to refuse Parallels — see
  quirks.md and the master plan's future-work list).

## Findings: step 3e — integration tests

* Full touched-suite results, run locally: `test_convert` 227
  passed, `test_compare` 59 passed, `test_dd` 41 passed,
  `test_adversarial` 83 passed, `test_info_safe` 800 passed,
  `test_oslo_crossval` 231 passed, `test_bench` 78 passed,
  `test_rebase` 27 passed. All skips present were pre-existing and
  unrelated to Parallels; zero failures across all eight suites.
* qemu accepts `-F parallels` for a backing-file format hint, so the
  qcow2-with-Parallels-backing chain test is live (not skipped),
  unlike formats qemu-img has no `-F` support for.
* `info` stays lenient on all four malformed fixtures by design: its
  detection-level parser (`parse_parallels_header`) only checks the
  magic and version, both of which are intact in every malformed
  fixture (they mutate `tracks`/`bat_entries`/`ext_off`, not the
  magic), so all four still detect as `parallels` and `info` exits 0
  with best-effort fields — the read-path validation that refuses
  them lives in the reader, not the detector. This mirrors the same
  info-stays-lenient stance the VDI and DMG phases established, and
  is documented as a quirk rather than treated as a defect.
* Cross-magic equivalence is pinned directly by `compare`:
  `parallels-data-v1` (sector-valued v1 BAT) and `parallels-data-v2`
  (cluster-valued ext BAT) carry identical guest content under
  different on-disk encodings, and `instar compare` between them
  reports identical, proving the per-magic `off_multiplier` decoding
  is correct end-to-end and not just at the unit-test level.

## Verification (management-session review checklist)

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified; instar-testdata
      changes are reviewed by the management session before
      the operator-authorised commit to its main.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes` (expect
      ~2 KB deltas like phase 2).
- [ ] `make test-rust`, `make fuzz-build`, and the convert /
      compare / dd / adversarial / info / oslo / bench /
      rebase suites pass (consumer-suite rule: the qcow2
      crate changed again).
- [ ] `pre-commit run --all-files` passes.
- [ ] All seven safe parallels fixtures convert
      byte-identically to qemu-img; both magics, the
      BAT-swap, small-cluster, inuse, and past-EOF fixtures
      pin their rules.
- [ ] Info output for parallels is byte-unchanged (cluster
      size reported internally, suppressed in both emitters;
      full test_info_safe green).
- [ ] check on parallels fails cleanly (pinned); bench
      succeeds; map/measure/resize refusals unchanged.
- [ ] Differential fuzzer includes parallels with a clean
      burn-in.
- [ ] Commit messages follow project conventions.

## Success criteria

Phase 3 is complete when:

* instar convert, compare, and dd accept both Parallels
  magics with byte parity against qemu-img across the
  fixture set, including non-contiguous BATs, small
  clusters, dirty (inuse) images, past-EOF BAT entries, and
  the v1 32-bit nb_sectors mask.
* ext_off != 0 images are refused cleanly and the
  divergence from qemu (which parses extensions) is
  documented.
* The four malformed fixtures are refused cleanly; the five
  new safe fixtures are CI-exercised with info baselines.
* `src/crates/parallels/` exists, no_std, panic-free, with
  unit + fuzz coverage; the differential fuzzer includes
  parallels.
* Info output is byte-unchanged; check/bench behaviour is
  pinned.
* Docs and the master plan record the new state.

## Hand-off to later phases

* Phase 4 (QCOW1) repeats this template again; its plan can
  be even briefer. The cluster-size plumbing mechanism
  built here (internal report + emitter suppression) is
  reusable for any format qemu prints no cluster_size for.
* Phase 5 (DMG) inherits the fixture-generator and
  graduation checklists but adds decompression; it should
  read this plan's ext_off refusal rationale when deciding
  its unsupported-codec refusals.
* Future work recorded on the master plan: parallels format
  extensions (dirty bitmaps); the upstream qemu check
  assertion regression (worth reporting to qemu).

## Back brief

Before executing any step of this plan, back brief the
operator: confirm the scope (convert/compare/dd/bench;
check/map/measure/resize stay refusals), the ext_off
refusal decision (deliberate divergence from qemu's RO
extension parsing, documented), the cluster-size plumbing
mechanism (internal report + format-gated emitter
suppression, gated on byte-identical info output), and that
instar-testdata changes are management-reviewed before the
authorised commit to its main.
