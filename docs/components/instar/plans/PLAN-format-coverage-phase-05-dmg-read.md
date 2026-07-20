# Format coverage phase 5: DMG convert-from (read path)

Master plan: [PLAN-format-coverage.md](/components/instar/plans/PLAN-format-coverage/)

## Status: Complete (2026-07-19)

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

DMG (Apple UDIF) is detect + info only, from phase 1. **This
plan references
[PLAN-format-coverage-phase-04-qcow1-read.md](/components/instar/plans/PLAN-format-coverage-phase-04-qcow1-read/)
(and through it phases 2–3) for everything structural** — the
chain-reader pattern, graduation ordering, the bench
`read_family` allowlist, the fixture/baseline/oslo workflow,
and the fuzzing shapes. Only DMG facts and deltas follow.
Research was done 2026-07-19 by two grounding passes: a
codebase survey of the phase-1 DMG state and an empirical
verification against qemu `block/dmg.c` (v7.0.0 + master,
plus `dmg.h`/`dmg-bz2.c`/`dmg-lzfse.c`) with ~22 hand-built
UDIF images run against the static qemu-img matrix (6.0.0 /
6.2.0 / 7.0.0 / 7.2.0 / 8.0.0 / 8.2.0 / 9.0.0 / 10.2.0) and
host 10.0.11.

### Phase-1 state (survey facts)

* Detection is a **guest-info-op koly-trailer probe**, not a
  header signature: shared helpers
  `detect_dmg_koly_offset`/`dmg_sector_count`
  (`src/shared/src/format_detection.rs:317-363`, with the
  qemu candidate window `[len-1023, len-512]`) driven by
  `probe_dmg_trailer`
  (`src/operations/info/src/main.rs:1474-1526`), which reads
  the last 1–2 device sectors and finds the LAST `koly` to
  recover the true file length (virtio capacity is
  sector-rounded-up, so the trailer sits mid-buffer with a
  zero tail — a deliberate deviation from the VHD-footer
  path).
* Info parses ONLY the trailer: virtual_size = SectorCount ×
  512; no plist/mish parsing, no cluster_size, no version.
  Host emitters already handle "dmg" (protocol-length
  512-rounding sets; dirty-flag suppression set).
* `chain::ImageFormat` has **no Dmg variant**; `from_str`
  maps "dmg" → Unknown → the issue-#444 gate refuses
  convert/compare/dd today (pinned:
  `test_convert_refuses_dmg`, compare/dd equivalents).
  **map/measure/resize pass DMG through as raw** (their
  probe paths never see the trailer) — pinned by
  `test_map.py:679`, `test_measure.py:1140`,
  `test_resize.py:834`, recorded as master-plan future work.
* `shared::ImageFormat::Dmg = 16`. bench `read_family`'s
  next free family number is **8**. No `src/crates/dmg/`
  exists. No dmg fuzz target beyond `fuzz_format_detect`'s
  trailer-helper coverage; the differential fuzzer has no
  dmg (qemu-img cannot create DMG).
* Testdata already has
  `scripts/generate-dmg-fixtures.py` (hand-built UDIF:
  koly + XML plist + base64 mish; zlib level 9,
  byte-deterministic), the safe `dmg-simple` fixture (4 MiB,
  two zlib chunks + terminator) with full baselines, and
  four malformed trailer fixtures (`dmg-truncated-koly`,
  `dmg-sectorcount-negative`, `dmg-sectorcount-huge`,
  `dmg-no-chunk-table`) pinned in test_adversarial.
* Ordering hazard (the qcow1 lesson, hand-off note in the
  phase-4 plan): adding the chain `Dmg` variant without a
  reader arm flips DMG from gate-refusal to **silent raw
  reads**. The reader arm (5b) must land before graduation
  (5c).

### DMG format facts (all empirically verified)

**koly trailer** (512 bytes BE, must occupy the file's final
512-aligned region; scan window as in phase 1). Fields qemu
reads: magic @0, DataForkOffset u64 @0x18, RsrcForkOffset
@0x28, RsrcForkLength @0x30, XMLOffset @0xD8, XMLLength
@0xE0, SectorCount s64 @0x1EC (negative refused). Validated:
DataForkOffset ≤ koly offset; rsrc/xml region bounds.
Ignored: version, header size, flags, DataForkLength, all
checksums. Path selection: RsrcForkLength != 0 → the **old
resource-fork path (supported by qemu — verified by
hand-built image)**: u32 rsrc_data_offset, u32 count, then
`[u32 size][mish]` resources; else XMLLength != 0 → plist
path; else EINVAL. XMLLength bounds: 0 < len ≤ 16 MiB.

**plist parsing is string scanning, not XML**: qemu strstr's
every `<data>…</data>`, base64-decodes each block with a
LENIENT decoder (glib skips invalid characters rather than
erroring), and keeps blocks whose decoded bytes carry the
mish magic and length ≥ 244; everything else is silently
ignored. No `<key>blkx</key>` or plist well-formedness
required. A missing `</data>` is "malformed XML" EINVAL.
**Malformed base64 does NOT error** — it just yields a
non-mish buffer, i.e. zero chunks.

**mish/BLKX**: magic 0x6D697368 @0, out_offset u64 @0x08
(added to each chunk's sector number), data_offset u64 @0x18
(added, together with DataForkOffset, to each compressed
offset), 204-byte header, then 40-byte BE chunk entries:
type u32, comment u32, sector u64, sector_count u64,
comp_offset u64, comp_len u64. chunk_count = (decoded_len −
204) / 40. Multiple mish blocks compose one virtual disk;
sector numbers are absolute after +out_offset; convert of a
multipart image equals raw concatenation (md5-verified).
**Virtual size: koly SectorCount always wins** over mish
coverage; uncovered tail sectors then read as errors.

**Chunk types** (dispositions verified per version):

| code | name | qemu behaviour |
|------|------|----------------|
| 0x00000000 | zero | memset zeros |
| 0x00000001 | raw | pread copy |
| 0x00000002 | ignore | zeros (same as zero) |
| 0x80000004 | ADC | enum-named but never implemented → dropped at open → gap |
| 0x80000005 | zlib (UDZO) | inflate — **zlib-WRAPPED** (0x78 header; raw deflate fails) |
| 0x80000006 | bzip2 (UDBZ) | **build-dependent module** |
| 0x80000007 | lzfse (ULFO) | build-dependent module |
| 0x80000008 | (zstd) | unknown → dropped → gap |
| 0x7FFFFFFE | comment | dropped, harmless |
| 0xFFFFFFFF | terminator | dropped, harmless |

**Codec support is compile-flag dependent across the
matrix**: bzip2 decodes only on static 6.0.0 and host
10.0.11; every other static build lacks the module; lzfse is
absent everywhere. Unsupported/unknown chunks are dropped
from the sector table at open (with stderr warnings only
from 7.2.0 on), leaving gaps.

**Error semantics — the big delta from phases 2–4**: DMG
reads ERROR where the other formats zero-fill.
* A sector covered by no chunk (gap, dropped chunk,
  koly-vs-mish mismatch tail) → **EIO at read; never
  zero-fill** (convert exits 1, "error while reading …
  I/O error").
* Compressed offset past EOF / truncated data fork → short
  read → EIO at read (open succeeds).
* Overlapping chunks: no error — binary search silently
  picks one (deterministic; verified).
* **qemu CRASH (universal, 6.0.0 through host 10.0.11): an
  image with a valid koly but ZERO parsed chunks (bad mish
  magic, broken base64, no `<data>` blocks) segfaults
  (SIGSEGV, rc 139) on any read** — a NULL `sectors[]`
  deref. `info` is fine (never touches the table). instar
  must NOT mirror this: refuse an empty chunk table cleanly
  at reader init. Candidate upstream report.

**Limits**: per-chunk comp_len ≤ 64 MiB
(`DMG_LENGTHS_MAX`, "length N for chunk C is larger than
max (67108864)"); per-chunk sector_count ≤ 131072 (64 MiB
uncompressed, "sector count N … larger than max (131072)");
zero/ignore chunks are EXEMPT from the sector cap. Both
stable across versions.

**Reads**: chunk-granular; qemu caches exactly one
uncompressed chunk and binary-searches the table per sector.

**Probe**: qemu's dmg probe is **pure `.dmg` filename
extension**. An extensionless valid DMG probes as raw (and
converts as container bytes); a `.dmg`-named non-DMG selects
the dmg driver and fails hard. instar's trailer-based
detection is strictly stronger — a real divergence for
extensionless files, to document (phase 1 established the
detection; convert inherits it this phase).

**info output**: no cluster-size, no format-specific block,
no dmg-level dirty-flag at any version; JSON `children[]`
from 8.0.0 (existing profile machinery covers it). Phase-1
baselines confirmed consistent.

**Other subcommands** (host + spot versions): check → "This
image format does not support checks" **rc 63** (parity with
instar's not-supported exit, as for qcow1); map works (with
a compressed-clusters stderr warning; JSON field drift:
`present` @7.0.0, `compressed` @8.2.0); measure works; dd
and bench read normally; compare works. Convert-to-raw md5
is version-stable for every openable image.

**oslo.utils** (git master, live): no DMG inspector → raw
fallback, virtual_size = min(file_size, 262144) —
consistent with the phase-1 entries; new fixtures follow the
same rule.

## Design

### Scope

convert, compare, dd, and bench gain DMG input. Chunk codec
scope (master plan Open question 3): **zero, raw, ignore,
zlib (zlib-wrapped inflate), comment/terminator handling** —
plus **typed reader-init refusals naming the codec for
UDBZ/ULFO/ADC/zstd/unknown types**. This diverges from
qemu's drop-at-open-then-EIO-at-read shape, deliberately:
both fail convert with rc 1, instar's failure names the
codec instead of a bare I/O error, and it cannot mis-serve a
partial image. bzip2/lzfse decode support stays future work
(and qemu's own support is build-dependent, so there is no
single parity target anyway). check stays rc-63 (parity).
**map/measure/resize keep their raw-pass-through behaviour**
— their probe paths still never see the trailer; the
master-plan future-work bullet stays open (this phase's
graduation only affects the chain-discovery consumers).
No write/create/output support; DMG stays absent from
convert's output roster.

### Graduation = the chain variant (reader arm first)

Chain discovery probes each position via the guest info op,
which already detects DMG. Adding `chain::ImageFormat::Dmg`
(+ from_str "dmg", to_shared_format_u32 => 16, Display)
lifts the #444 gate for convert/compare/dd — so exactly as
in phase 4, the variant lands strictly AFTER the 5b reader
arm. bench `read_family` gains `Dmg => Some(8)` in
lock-step.

### New `src/crates/dmg/` crate

no_std, panic-free, mirroring the sibling crates. Parses,
from the device via the call table:

1. **koly** (reusing the shared trailer helpers' constants
   and the info op's last-two-sectors true-length recovery
   technique — factor the shared logic rather than
   duplicating): validate exactly qemu's field set, ignore
   exactly qemu's ignored set.
2. **Chunk-table source**: XML-plist path AND the old
   resource-fork path (both qemu-supported). String-scan
   `<data>`/`</data>`; lenient base64 (skip invalid chars,
   matching glib — parity requires opening the same images);
   accept only decoded blocks with mish magic and len ≥ 244.
3. **mish** blocks → one flat chunk table: entries with
   out_offset/data_offset/DataForkOffset applied, qemu's
   per-chunk caps enforced with the exact limits (comp_len
   ≤ 64 MiB, sector_count ≤ 131072 with zero/ignore exempt),
   comment/terminator dropped, unknown/unsupported codecs →
   **typed init refusal naming the code** (see Scope).
4. **instar's own bounded-memory caps** (typed refusals,
   documented divergences — the sandbox has ~12.9 MiB of
   scratch, and qemu-legal chunks can be 64 MiB
   uncompressed + 64 MiB compressed, which cannot be staged):
   * plist/resource-fork region staged for parsing: cap at
     **1 MiB** (real-world plists are KBs; qemu's own cap is
     16 MiB).
   * chunk table: compact entries, capped at **32768 chunks**
     (~32 GiB of default 1 MiB-chunk UDZO coverage; the
     table fits ~1 MiB of scratch).
   * per-chunk staging: uncompressed sector_count ≤ **4096
     sectors (2 MiB, = staging_buf)** and comp_len ≤
     compressed_buf for non-zero/ignore chunks. hdiutil's
     default UDZO chunking is 1 MiB, so real images fit with
     2× headroom; a qemu-legal-but-over-cap image gets a
     typed refusal, pinned by a divergence fixture.
5. **Empty-table refusal**: a valid koly with zero parsed
   chunks refuses cleanly at init (where qemu segfaults).
   Step-5d correction (2026-07-19): the existing
   `dmg-no-chunk-table` fixture is qemu's clean-EINVAL path
   (XMLLength = 0 AND RsrcForkLength = 0 — qemu never
   reaches table-build); the segfault requires a valid
   plist whose chunks parse to zero, shipped as the NEW
   `dmg-empty-table` fixture (corrupted mish magic inside a
   well-formed `<data>` block) — qemu info rc 0, convert
   SIGSEGV; instar refuses cleanly at init.
6. **Table ordering**: qemu builds the table in mish order
   and binary-searches, assuming sortedness; real images are
   sorted. instar verifies sorted-by-sector at init and
   refuses unsorted/overlapping tables (typed; qemu's
   behaviour there is silent-arbitrary). No overlap fixture
   ships; recorded as a corner.

Lookup API: `DmgState::init` (stages the chunk table into a
caller-provided scratch region) + `chunk_lookup(sector)` →
Zero / Raw { host_offset } / Zlib { host_offset, comp_len }
per-span results with the containing chunk's bounds, so the
arm can walk span-by-span.

### Reader arm shape

Per-chunk walk (chunk sizes vary per entry; chunks are
routinely ≥ chunk-size > chunk_size of the read loop):
* Zero/ignore spans → write zeros.
* Raw spans → capacity-clamped read? **No — EIO parity**: a
  raw span whose bytes lie past EOF must FAIL the read
  (qemu short-pread → EIO), not zero-fill. `return false`
  when the device cannot supply the bytes. This inverts the
  phase 2–4 zero-fill posture and must be commented
  prominently in the arm.
* Zlib spans → read comp_len bytes (byte-accurate,
  possibly unaligned) into compressed_buf, inflate
  **zlib-wrapped** (TINFL_FLAG_PARSE_ZLIB_HEADER — the
  qcow2 helper's first-try flags; NOT the qcow1 raw-only
  call) into staging_buf, verify exact uncompressed length,
  cache the decompressed chunk via the staging cache
  (invalidate `staging_cluster_offset` correctly — and
  since consecutive reads usually hit the same chunk,
  consider keying the cache so re-inflation is avoided
  within a chunk; qemu caches one chunk the same way).
* Gap (no covering chunk, including the koly-wins
  SectorCount tail) → **return false** (EIO parity).
* No backing files in DMG — no chain descent from this arm.

Feature: `dmg-input = ["dep:dmg", "decompress"]` in the
qcow2 crate; enabled by convert/compare/bench/rebase;
`read_offset_sectors` cfg widened; Makefile feature line
extended.

### Fixtures and baselines

Extend `generate-dmg-fixtures.py` (same deterministic
approach). New safe (full baselines + convert parity):

* `dmg-mixed` — one mish mixing zero + raw + zlib + ignore
  + a comment entry + terminator.
* `dmg-multipart` — two mish blocks; convert equals
  concatenation.
* `dmg-rsrc-fork` — the old resource-fork path (no XML).
* `dmg-gap` — koly SectorCount exceeding mish coverage:
  info baselines fine (qemu info rc 0); convert FAILS on
  both sides (qemu EIO, instar clean gap failure) — pinned
  as an error-parity fixture, not a byte-parity one.

New malformed/refused (skip_qemu_img + expected_error):

* `dmg-chunk-len-over` (comp_len = 64 MiB + 1; qemu
  "larger than max (67108864)") and `dmg-sc-over`
  (sector_count 131073; "larger than max (131072)") — qemu
  refuses at open, instar at init.
* `dmg-codec-bzip2`, `dmg-codec-lzfse`, `dmg-codec-adc` —
  instar's typed codec refusals. skip_qemu_img because
  qemu's behaviour is build-dependent (6.0.0 and host
  decode bzip2; the rest EIO) — note this in the manifest
  descriptions.
* `dmg-overcap-chunk` — qemu-LEGAL (sector_count 8192 =
  4 MiB uncompressed zlib chunk) but over instar's staging
  cap: the documented capacity-divergence fixture (qemu
  converts it; instar refuses typed). skip_qemu_img with an
  explicit divergence note.
* `dmg-empty-table` — valid koly + well-formed plist whose
  single `<data>` block decodes with a corrupted mish
  magic: qemu parses zero chunks, info succeeds, and any
  read SIGSEGVs (the universal NULL-deref; the shipped
  upstream-report reproducer). instar refuses the empty
  table cleanly at init. skip_qemu_img.

Existing malformed trailer fixtures keep their manifest
entries; their convert/compare/dd expectations move from
gate-refusal messages to reader-refusal messages in 5e.
Oslo: new safe fixtures get KNOWN_FORMAT_DIVERGENCES
('dmg', 'raw') + vsize entries per the min(file_size,
262144) rule (verify live); malformed → OSLO_SKIP_IMAGES.

### Fuzzing

Coverage-guided: `fuzz_dmg_table` (koly→plist→base64→mish→
table build + lookups; asserts caps, sortedness handling,
and no panics — the plist scanner and lenient base64 are the
juicy attack surface) and `fuzz_dmg_chunk` (arm-level:
lookup + inflate invariants). Differential: qemu-img cannot
create DMG, so `generate_image` gains a dmg branch that
BUILDS images with a ported mini-generator (random chunk
type mixes / multipart / gaps excluded, sizes within caps,
deterministic from the iteration rng) — run convert/dd/
compare differentially; gate op_map and op_measure for
'dmg' (instar's map/measure still treat DMG as raw — the
retained divergence). If the harness makes the custom
builder fragile, 5f may land the fuzz targets and a
REDUCED differential (convert-only) with a comment, but
must state what was dropped. ~200-iteration forced burn-in.

## Out of scope for this phase

* bzip2 (UDBZ) / lzfse (ULFO) / ADC decode support (typed
  refusals instead; master-plan future work — and qemu's
  own support is build-dependent).
* Wiring the koly probe into map/measure/resize and the
  host prefix probes (raw-pass-through stays pinned;
  master-plan future-work bullet remains open).
* Streaming decompression for over-cap chunks (the typed
  refusal + divergence fixture stands in; revisit only if
  real-world images exceed the 2 MiB staging cap).
* DMG write/create/output; encrypted DMGs (a different
  container — FileVault/AES — not part of UDIF chunk
  parsing; detection unaffected).

## Step-level guidance

Steps mirror phase 4; briefs assume the agent reads the
phase-4 plan's corresponding step and the referenced
commits first.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | New `src/crates/dmg/` crate. Read src/crates/qcow1/ + parallels first for the shape, plus the shared DMG trailer helpers (format_detection.rs:317-363) and probe_dmg_trailer (info op :1474) — factor/reuse the koly window+true-length logic per the Design (shared helpers grow if needed; do NOT duplicate the window math). Implement: koly parse with qemu's exact validated/ignored field sets; path selection (rsrc-fork path AND xml path); the `<data>` string scan; lenient base64 (glib semantics: skip invalid chars; no error on garbage); mish parse per the Situation layout with out_offset/data_offset/DataForkOffset application; qemu's per-chunk caps with exact limits and zero/ignore sector-cap exemption; instar's bounded-memory caps (1 MiB plist stage, 32768-chunk table, 4096-sector/compressed_buf per-chunk staging caps) as DISTINCT typed refusals from qemu's caps; unknown/unsupported codec types (ADC 0x80000004, bzip2 0x80000006, lzfse 0x80000007, zstd 0x80000008, anything else) → typed refusal naming the code; comment/terminator dropped; empty-table refusal (the qemu-segfault case — comment it); sorted-by-sector verification with refusal on unsorted/overlap. `DmgState::init(call_table, dev_idx, sector_size, capacity, scratch_region_ptr/len, bytes_read)` staging the compact chunk table into caller scratch; `chunk_lookup(sector)` returning span-typed results (Zero/Raw/Zlib with host offsets, span bounds). All arithmetic checked; no_std; panic-free. Unit tests (~25+): every koly validation accept/reject; both table paths; lenient-base64 cases (invalid chars skipped, truncated `</data>` refused); mish boundary caps at qemu's exact limits both sides; instar-cap refusals; codec refusals per code; empty table; unsorted refusal; multipart absolute sectors; the out_offset/data_offset arithmetic; lookup walks incl. gap spans. Workspace member; make instar/lint/test-rust clean. |
| 5b | high | opus | none | Guest integration: `dmg-input = ["dep:dmg", "decompress"]` feature; `ChainStates.dmg_states`; init arm (decide + document the scratch placement for the chunk table — read the convert memory-layout compile-assert (src/operations/convert/src/main.rs:100-117) and shared memory map (src/shared/src/lib.rs:324-468); the table budget is ~1.25 MiB (32768 × 40 B) and must not collide with the worst-case layout; extend the compile-assert). Reader arm per the Design: per-chunk walk; Zero/ignore → zeros; Raw → read, but **EIO parity: missing bytes (past capacity, short) → return false, NOT zero-fill — comment prominently that this inverts the phase 2-4 posture**; Zlib → byte-accurate comp_len read into compressed_buf, zlib-WRAPPED inflate (TINFL_FLAG_PARSE_ZLIB_HEADER | NON_WRAPPING — not qcow1's raw-only call) into staging_buf with exact-length verification and staging-cache keying so consecutive reads within one chunk avoid re-inflation (study the staging_cluster_offset semantics; report what you did); Gap → return false; no backing descent. Widen read_offset_sectors cfg; enable feature in convert/compare/bench/rebase; Makefile line. Unit tests (feature-gated, mirroring q1_arm_*): mixed-type chunk walk, zlib chunk cached across two reads (assert single inflation via bytes_read accounting), raw-span EIO on truncation, gap EIO, zero spans, multipart-table reads, over-cap init refusal propagates. Behaviour unchanged this step (no chain variant yet — the #444 gate still refuses; three refusal tests still green). make instar/check-binary-sizes/test-rust/lint; report binary deltas. |
| 5c | high | opus | none | Graduation + pins, separate commits. Commit 1: chain.rs `Dmg` variant + from_str "dmg" + to_shared_format_u32 => 16 + Display "dmg"; bench read_family `Dmg => Some(8)` + doc comment. NO info changes (info already emits dmg correctly; run full test_info_safe to prove zero drift). Commit 2 (pins): delete test_convert_refuses_dmg / compare / dd refusal tests, replacing with dmg-simple convert/compare/dd parity smokes vs qemu-img (the fixture has baselines and .dmg extension so qemu probes it); check on dmg → expect exit 63 "This image format (dmg) does not support checks" (record exact); map/measure/resize raw-pass-through pins UNCHANGED (re-run those three tests; they must still pass — graduation only affects chain-discovery consumers; if any flips, STOP for management review); the existing four malformed trailer fixtures' adversarial convert expectations change from the gate message to reader-init failures — update TestAdversarialDmgManifest accordingly (info expectations unchanged; note dmg-no-chunk-table is qemu's clean-EINVAL shape, refused at koly path-selection); the NEW dmg-empty-table fixture (the actual qemu-segfault reproducer — see Design) must fail convert CLEANLY on instar (assert no crash, clean rc, the typed empty-table message). Run the touched suites + full test_info_safe. |
| 5d | high | opus | none | Testdata, mirroring 4d (read its brief/findings; --no-commit posture, --output-type surgical detect-profiles, integrity spot-check; NEVER commit). Extend scripts/generate-dmg-fixtures.py with the eight new fixtures per Design (four safe: mixed, multipart, rsrc-fork, gap; four+ refused: chunk-len-over, sc-over, codec-bzip2/lzfse/adc, overcap-chunk), all byte-deterministic (three-run identical). Validate: safe fixtures info + convert md5 on 6.0.0 AND 10.2.0 (dmg-gap: info rc 0 both, convert rc 1 both — record messages); codec fixtures: record the per-version matrix honestly (bzip2 DECODES on 6.0.0; EIO elsewhere) in the manifest descriptions; overcap-chunk: qemu CONVERTS it fine (it is legal) — record its md5 as the divergence evidence. Baselines for the four safe fixtures (restricted manifest, --no-commit; dmg-gap's baseline meta will record qemu rc 0 for info — fine); detect-profiles + spot-check. Instar-side (only after 5c lands — coordinate with management): manifest entries for the eight (safe run_in_ci; refused skip_qemu_img + expected_error = instar's typed messages, with build-dependence notes for codecs and the capacity-divergence note for overcap); oslo entries: KNOWN_FORMAT_DIVERGENCES ('dmg','raw') + KNOWN_VSIZE_DIVERGENCES per min(file_size, 262144) — verify each pair live in the venv; refused set → OSLO_SKIP_IMAGES. |
| 5e | medium | opus | none | Integration matrix mirroring 4e: convert-to-raw parity for dmg-simple + the three byte-parity safe fixtures (mixed, multipart, rsrc-fork); multipart-equals-concatenation pin; convert dmg-mixed to qcow2 + vpc (flatten-compare); compare dmg-simple vs its raw conversion identical, mixed vs multipart differ; windowed dd crossing a zlib/raw chunk boundary and a zero span (count absolute from 0); dmg-gap: convert AND dd fail non-zero cleanly on both instar and qemu (error-parity, messages differ — comment); adversarial: all refused fixtures (old four + new codec/cap ones) refuse convert/compare/dd non-zero cleanly, no hang, no crash; bench on dmg-simple (header parity); the extensionless-file divergence pin: copy dmg-simple to a non-.dmg name, qemu convert (no -f) treats it as RAW while instar detects dmg — pin both behaviours with a comment citing the plan (qemu probe is extension-only). Full sequential suite matrix (the twelve suites of 4e; build once, then suites one at a time). Isolated re-run before believing any failure. |
| 5f | medium | sonnet | none | Fuzzing mirroring 4f: fuzz_dmg_table + fuzz_dmg_chunk per the Design (the plist scanner + lenient base64 + mish caps are the priority surface; drive both table paths — xml and rsrc-fork — from fuzz bytes); Cargo wiring; make fuzz-build + 60s runs (exec/s, crashes blocking). Differential: generate_image dmg branch using a ported deterministic mini-builder (seeded from the iteration rng: chunk-type mixes zero/raw/zlib, 1-3 mish blocks, chunk sizes within instar caps, NO gaps, valid images only), FORMATS += 'dmg'; gate op_map ('raw','vdi','parallels','qcow','dmg') and op_measure ('vdi','parallels','qcow','dmg') citing this plan (instar map/measure treat dmg as raw — retained divergence); if the builder proves fragile in the harness, land fuzz targets + a convert-only differential and SAY SO. ~200-iteration forced burn-in, zero divergences expected, isolated replay for hits. |
| 5g | medium | sonnet | none | Docs close-out mirroring 4g: format-coverage.md (dmg input row, fixture inventory now 13 dmg images, narrative); quirks.md phase-5 section (EIO-not-zero-fill error semantics; the qemu zero-chunk segfault instar refuses cleanly — upstream-report candidate; codec typed refusals + qemu's build-dependent bzip2; instar's capacity caps + the overcap divergence fixture; extension-only qemu probe vs instar trailer detection incl. the extensionless convert divergence; resource-fork path support; map/measure/resize raw-pass-through retained; lenient base64 parity; koly-wins virtual size); README (dmg read-only input); CHANGELOG; ARCHITECTURE (crates/dmg); master plan row 5 Complete + future-work updates (bzip2/lzfse decode; the qemu segfault upstream report; streaming decompression note) + move the DMG line out of "formats that don't" phrasing where applicable; index.md phases 1-5; this file Status Complete + Findings sections. Consistency grep for stale dmg claims ("detect + info only", the refusal-test names). |

Sequencing: 5a → 5b → 5c strictly (reader before variant);
5d's testdata portion parallel with 5a-5c, its instar-side
edits after 5c; 5e after 5c + 5d; 5f after 5c; 5g last.

## Findings: step 5a — dmg crate

* `src/crates/dmg/` landed as planned: koly-trailer parsing reuses
  the phase-1 shared helpers
  (`shared::format_detection::{detect_dmg_koly_offset,
  find_last_dmg_koly, parse_dmg_koly}`) rather than duplicating the
  window-scan/true-length-recovery logic, so the crate and the info
  op's trailer probe share one source of truth for trailer geometry.
* Both chunk-table sources landed: the XML-plist `<data>…</data>`
  string scan with a byte-for-byte port of glib's lenient base64
  (invalid characters skipped, never erroring; a missing `</data>` is
  the one case treated as malformed XML and refused), and the old
  resource-fork walk (`u32 rsrc_data_offset`, `u32 count`, then
  `[u32 size][mish]` resources) — path selection is
  `RsrcForkLength != 0` first, else `XMLLength != 0`, else refused,
  matching qemu's `dmg_open` exactly.
* mish/BLKX parsing applies `out_offset`/`data_offset`/
  `DataForkOffset` exactly as qemu does, enforces qemu's own
  per-chunk caps (`comp_len <= 64 MiB`, `sector_count <= 131072` with
  zero/ignore chunks exempt) as one refusal class, and instar's own
  smaller bounded-memory caps (`DMG_REGION_STAGE_CAP` = 1 MiB,
  `DMG_MAX_CHUNKS` = 32768, `DMG_MAX_STAGED_SECTOR_COUNT` = 4096
  sectors) as a **distinct** typed refusal class — the two never
  share a message, so a fixture's refusal reason unambiguously
  records which cap it hit.
* Unsupported/unknown codec types (ADC `0x80000004`, bzip2
  `0x80000006`, lzfse `0x80000007`, zstd `0x80000008`, and any other
  unrecognised code) get a typed `DmgRefusal::UnsupportedCodec`
  refusal naming the exact u32; comment (`0x7ffffffe`) and terminator
  (`0xffffffff`) entries are dropped silently, matching qemu.
* The empty-table refusal (`DmgRefusal::EmptyChunkTable`) — the
  reader's answer to qemu's universal zero-chunk NULL-deref crash —
  fires whenever the assembled table has zero entries, regardless of
  which chunk-table source produced it (an empty plist, an
  all-garbage-base64 plist, or an empty resource fork all collapse to
  the same refusal).
* Sortedness verification landed as specified: the table is checked
  sorted-by-sector at init and an unsorted/overlapping table is
  refused rather than silently binary-searched against (qemu's own
  behaviour there is silent-arbitrary); no shipped fixture exercises
  an unsorted table, recorded as an accepted un-fixtured corner
  matching the established policy elsewhere in this document.
* `DmgState::init`/`chunk_lookup` follow the sibling crates' stateful
  shape (`init` stages the table into caller scratch, `chunk_lookup`
  returns span-typed `Zero`/`Raw{host_offset}`/`Zlib{host_offset,
  comp_len}` results with the containing chunk's bounds). `no_std`,
  panic-free, workspace member; unit tests cover every koly
  validation accept/reject, both table paths, the lenient-base64
  edge cases, both cap classes at their exact boundaries, every codec
  refusal, the empty-table refusal, the unsorted-table refusal, the
  multipart absolute-sector arithmetic, and lookup walks including
  gap spans — `make instar`/`lint`/`test-rust` clean.

## Findings: step 5b — guest reader arm

* The `dmg-input = ["dep:dmg", "decompress"]` feature landed in the
  qcow2 crate, with `ChainStates.dmg_states` and a per-device scratch
  slot carved from a caller-reserved region sized
  `DMG_REQUIRED_SCRATCH` (~3.25 MiB: a 1.25 MiB persistent chunk
  table plus a 2 MiB transient plist/decode region). `convert`'s
  memory-layout compile-time assertion
  (`src/operations/convert/src/main.rs`) was extended to account for
  the new region; the transient init suffix reuses convert's existing
  staging buffer, so the layout's **net** addition is only the
  persistent 1.25 MiB table, not the full 3.25 MiB slot size.
  `compare` reserves two slots (either side of the comparison may be
  a DMG); `bench`/`rebase` use the write-only overlay-scratch shape.
* **EIO parity is the one genuinely new architectural piece this
  phase** (inverting the phase 2-4 zero-fill posture, commented
  prominently in the arm per the plan's explicit instruction): a raw
  span whose bytes are unavailable (past capacity or short), a gap
  (no covering chunk, including the koly-`SectorCount`-vs-mish-
  coverage tail), and truncated/short compressed data all make the
  reader return `false` rather than zero-filling. Zero/ignore spans
  still write zeros (that part of the posture is unchanged from every
  other format).
* Zlib spans read `comp_len` bytes (byte-accurate, possibly
  unaligned) into the compressed buffer and inflate
  **zlib-wrapped** (`TINFL_FLAG_PARSE_ZLIB_HEADER`, matching the
  qcow2 crate's own zlib-first helper flags — explicitly NOT QCOW1's
  raw-deflate-only call) with exact-uncompressed-length verification.
  The decompressed-chunk staging cache is keyed by the chunk's host
  file offset with bit 63 OR'd in
  (`cache_key = host_offset | (1u64 << 63)`), which can never collide
  with the qcow2/vmdk staging cache's own offset-keyed entries (real
  file offsets are always well under 2^63) — so consecutive reads
  within one chunk avoid re-inflation without any risk of aliasing
  another format's cached cluster.
* No backing-file descent from this arm — DMG carries no backing
  reference of its own, so the arm never recurses into a "next
  device," unlike QCOW1's arm.
* `read_offset_sectors`'s cfg gate was widened to include
  `dmg-input`; the feature was enabled in convert/compare/bench/
  rebase `Cargo.toml`s and the Makefile's qcow2 test-features line
  was extended. Behaviour was unchanged this step (no chain variant
  yet — the #444 gate still refused DMG at this point in the commit
  sequence); the three pre-existing DMG refusal tests stayed green
  through this step, confirming the reader-before-variant ordering
  held.
* Unit tests mirror the VDI/Parallels/QCOW1 pattern plus DMG-specific
  cases: a mixed-type chunk walk, a zlib chunk cached across two
  reads (asserting a single inflation via `bytes_read` accounting), a
  raw-span EIO on truncation, a gap EIO, zero spans, multipart-table
  reads, and an over-cap init refusal propagating cleanly up through
  the arm.

## Findings: step 5c — graduation and pins

* Landed as two separate commits per the plan: `ede8fd4` (the
  `chain::ImageFormat::Dmg` variant — `from_str "dmg"`,
  `to_shared_format_u32 => 16`, `Display "dmg"`, and bench
  `read_family`'s `Dmg => Some(8)`) and `9033505` (the pins). No info
  changes were needed in this step — info already emitted `dmg`
  correctly from phase 1 — confirmed by a full `test_info_safe` run
  with zero drift.
* Three empirically-grounded pin deviations from the plan's initial
  expectations, all confirmed correct during this step rather than
  assumed:
  1. **`check` names the format "raw", not "dmg"** — because check's
     own format dispatch never runs the koly-trailer probe (that
     probe lives only in the `info` op's guest chain), check sees a
     DMG image as `Raw` and refuses with `This image format (raw)
     does not support checks`, exit 63. This is still genuine rc
     parity with qemu-img's own dmg-check refusal (also exit 63,
     "does not support checks") — only the named format differs.
  2. **`dmg-sectorcount-negative` is a pre-existing unknown-format
     pass-through, not a reader-refusal fixture** — the negative
     `SectorCount` collapses the shared trailer helper's detection to
     `unknown` (not `dmg`) before the image ever reaches the #444
     gate or the DMG reader, so it reads as ordinary raw pass-through
     on the small container. This behaviour predates phase 5 (phase-1
     detection logic); this step added the first test pinning it.
  3. **`dmg-no-chunk-table` keeps its clean-EINVAL shape and is
     distinct from the new `dmg-empty-table` segfault reproducer** —
     the four pre-existing malformed trailer fixtures' adversarial
     convert expectations moved from the gate-refusal message to
     reader-init failure messages, but `dmg-no-chunk-table`
     specifically (both `RsrcForkLength` and `XMLLength` zero) never
     reaches table-build at all, so it refuses at koly path-selection
     with a different reason than the empty-table case.
* The new `dmg-empty-table` fixture (the actual qemu-segfault
  reproducer) was confirmed to fail convert cleanly on instar with no
  crash and the typed empty-table message, as required.
* map/measure/resize raw-pass-through pins were re-run and confirmed
  **unchanged** — graduation only affects chain-discovery consumers
  (convert/compare/dd/bench), and none of the three raw-pass-through
  pins flipped, so no management-review stop was triggered.

## Findings: step 5d — fixtures, baselines, and manifest/oslo

* `instar-testdata/scripts/generate-dmg-fixtures.py` was extended
  with the twelve new fixtures deterministically (three-run
  byte-identical, per the established DMG generator posture): four
  safe (`dmg-mixed`, `dmg-multipart`, `dmg-rsrc-fork`, `dmg-gap`) and
  eight malformed/refused (`dmg-chunk-len-over`, `dmg-sc-over`,
  `dmg-codec-bzip2`, `dmg-codec-lzfse`, `dmg-codec-adc`,
  `dmg-overcap-chunk`, `dmg-empty-table`, plus the pre-existing four
  trailer fixtures whose manifest entries carried forward unchanged
  in id/description but changed expectation for convert). Combined
  with the phase-1 `dmg-simple` and four trailer fixtures, the DMG
  fixture inventory now totals **16** images (5 safe, 11 malformed/
  refused) — more than the plan's rough step-5g estimate of 13,
  because the Design section's malformed set (7 new fixtures) plus
  the 4 pre-existing trailer fixtures came to 11, not 8.
* Safe fixtures were validated on 6.0.0 and 10.2.0: `info` + convert
  md5 agreement for `dmg-mixed`/`dmg-multipart`/`dmg-rsrc-fork`;
  `dmg-gap` recorded as an error-parity fixture instead (`info` rc 0
  both sides at virtual size 8192; convert rc 1 both sides, messages
  differ and are not compared).
* Codec fixtures record the per-version matrix honestly rather than
  asserting one qemu build as the oracle: `dmg-codec-bzip2` decodes
  on static 6.0.0 and host 10.0.11 (md5 `3b4c90d4a1a30682b7ec51174fb-
  6928d`) but EIOs elsewhere; `dmg-codec-lzfse` and `dmg-codec-adc`
  EIO on every tested version. `dmg-overcap-chunk` (the capacity-
  divergence fixture) converts fine on every qemu version (md5
  `dd8d16c0893059dd98d1a3bf1f8675bd`) despite being over instar's
  staging cap. All codec/capacity fixtures carry `skip_qemu_img` in
  the manifest with the build-dependence or divergence noted in the
  description text.
* `dmg-empty-table` was constructed as a well-formed XML plist whose
  single `<data>` block decodes to a mish with a corrupted magic
  (`0xDEADBEEF`) — confirmed to reproduce the universal SIGSEGV (rc
  139) on static 6.0.0, static 10.2.0, and host 10.0.11 alike, with
  `info` unaffected (rc 0) since it never touches the chunk table.
  `skip_qemu_img` since no convert baseline can exist against a
  crashing tool.
* Instar-side manifest entries (safe fixtures `run_in_ci`; refused
  fixtures `skip_qemu_img` + `expected_error` recording instar's
  typed guest-side message verbatim) landed after step 5c, as
  required. Oslo entries: `KNOWN_FORMAT_DIVERGENCES` = `('dmg',
  'raw')` for all four new safe fixtures (oslo has no DMG inspector,
  falls back to `RawFileInspector`), plus `KNOWN_VSIZE_DIVERGENCES`
  recording each fixture's file size (all four fit under oslo's
  262144-byte raw-fallback read-chunk cap, so oslo reports the whole
  file length while instar reports the koly-derived virtual size) —
  verified live against oslo.utils 10.1.2.dev8 (git master). The
  eight refused fixtures joined `OSLO_SKIP_IMAGES`.

## Findings: step 5e — integration tests

* The full sequential suite matrix ran clean: `test_info_safe`
  954/0, `test_convert` 251/0, `test_compare` 65/0, `test_dd` 45/0,
  `test_adversarial` 96/0, `test_oslo_crossval` 279/0, `test_bench`
  80/0, `test_rebase` 28/0, `qcow1_smoke` 12/0, `test_check_formats`
  77/0, `test_map` 268/0, `test_measure` 570/0, `test_resize` 125/0 —
  zero failures across every consumer suite, confirming the
  crate-change consumer-suite rule needed no bisection this phase.
* Byte parity confirmed for `dmg-simple`/`dmg-mixed`/`dmg-multipart`/
  `dmg-rsrc-fork` convert-to-raw against `qemu-img convert`;
  multipart-equals-concatenation pinned directly (converting
  `dmg-multipart` byte-matches the concatenation of its two mish
  parts); `dmg-mixed` converted to qcow2 and vpc and flatten-compared
  clean; compare pinned `dmg-simple` identical to its raw conversion
  and `dmg-mixed` different from `dmg-multipart`.
* Windowed `dd` was pinned crossing both a zlib/raw chunk boundary
  and a zero span, with absolute-from-0 counts, confirming the
  per-chunk arm walk handles sub-chunk windowing correctly.
* `dmg-gap` was confirmed to fail convert AND dd non-zero cleanly on
  both instar and qemu (error parity, messages differ, never
  byte-parity, as designed).
* All refused fixtures — the four pre-existing trailer fixtures plus
  the eight new codec/capacity/empty-table fixtures — were confirmed
  to refuse convert/compare/dd non-zero cleanly with no hang and no
  crash, including a dedicated no-crash assertion for
  `dmg-empty-table` (the qemu-segfault reproducer).
* The extensionless-file divergence was pinned exactly as specified:
  a copy of `dmg-simple` without its `.dmg` suffix converts as raw
  container bytes under `qemu-img convert` (no `-f` given, extension
  probe falls through to raw) but as the real decoded 4 MiB disk
  under instar (content-based trailer detection) — both behaviours
  asserted, with a comment citing this plan.
* **The `-F dmg` backing-chain convergence** (the DMG-at-any-chain-
  position proof): a qcow2 overlay created with `qemu-img create -f
  qcow2 -b <dmg> -F dmg` reads through instar's chain walker and
  converges byte-for-byte with the same chain read through
  `qemu-img convert` — confirming the DMG reader arm composes
  correctly with the existing chain-descent machinery even though DMG
  itself has no backing-file concept, because the *overlay*'s backing
  reference (not DMG's own) is what places the DMG reader mid-chain.
* `bench` on `dmg-simple` confirmed header parity with `qemu-img
  bench`.

## Findings: step 5f — fuzzing

* `fuzz_dmg_table` (koly → plist/rsrc-fork → base64 → mish → table
  build + lookups) and `fuzz_dmg_chunk` (arm-level lookup + inflate
  invariants) both ran crash-free in local 60-second sessions:
  approximately 67k exec/s for the table target (the plist scanner
  and lenient base64 are the heaviest part of that target) and
  approximately 28k exec/s for the chunk target. Both drive the XML
  and resource-fork table paths from fuzz bytes, and both assert
  qemu's exact caps, sortedness handling, and no panics.
* The differential fuzzer's `generate_image` gained a `dmg` branch
  using a ported deterministic mini-builder (qemu-img cannot create
  DMG images itself), seeded from the iteration rng: chunk-type mixes
  of zero/raw/zlib, 1-3 mish blocks, chunk sizes kept within instar's
  caps, and no gaps — every generated image is a valid, fully-openable
  DMG so the differential comparison stays meaningful. `FORMATS`
  gained `'dmg'`; `op_map` was gated to `('raw', 'vdi', 'parallels',
  'qcow', 'dmg')` and `op_measure` to `('vdi', 'parallels', 'qcow',
  'dmg')`, citing this plan, since instar's map/measure still treat
  DMG as raw (the retained phase-1 divergence — see step 5c).
* A ~200-iteration forced burn-in (seed 52001) against the
  differential harness produced **zero divergences**, matching every
  prior format-coverage phase's clean burn-in result. The custom
  builder proved robust enough that the full differential scope
  (convert/dd/compare, not a reduced convert-only fallback) landed as
  originally planned.

## Verification (management-session review checklist)

- [ ] Files that were supposed to change changed (read them);
      no unrelated modifications; testdata reviewed before
      the operator-authorised commit to its main.
- [ ] make instar / lint / test-rust / check-binary-sizes /
      fuzz-build clean; pre-commit clean per commit.
- [ ] Byte parity for dmg-simple/mixed/multipart/rsrc-fork
      convert; multipart == concatenation; error parity for
      dmg-gap (both sides fail, no instar crash on the
      qemu-segfault fixtures).
- [ ] Codec and capacity refusals typed and pinned;
      overcap divergence fixture documented.
- [ ] Info output byte-unchanged (full test_info_safe);
      check rc 63; map/measure/resize raw-pass-through pins
      unchanged.
- [ ] Differential dmg burn-in clean (or the reduced-scope
      fallback explicitly recorded).
- [ ] Commit messages follow conventions.

## Success criteria

Phase 5 is complete when:

* instar convert, compare, dd, and bench read DMG with byte
  parity against qemu-img for zero/raw/ignore/zlib chunk
  images including multipart and the resource-fork path.
* Gap/truncation reads fail cleanly on both sides (EIO
  parity, no zero-fill); the qemu zero-chunk segfault class
  is a clean instar refusal.
* Unsupported codecs and over-cap chunks get typed,
  documented, fixture-pinned refusals.
* Detection/info behaviour is byte-unchanged; check is
  rc 63; map/measure/resize pins unchanged.
* `src/crates/dmg/` exists (no_std, panic-free) with unit +
  fuzz coverage; the differential fuzzer exercises dmg via
  the custom builder (or the recorded reduced scope).
* Docs and the master plan record the new state.

## Hand-off to later phases

* Phase 6 (QED decision) inherits a complete
  convert/compare/dd/bench graduation checklist and the
  reader-before-variant ordering rule; QED's check/map/
  measure behaviour pins should reference qcow1's and dmg's
  rc-63 parity notes.
* Phase 7 (docs) collects the cross-phase divergence tables
  (map/measure scope refusals; dmg capacity caps; codec
  refusals) into the qemu-img-parity axis document.
* Master-plan future work after this phase: bzip2/lzfse
  decode; the qemu zero-chunk segfault upstream report; the
  in-place-op trailer probing bullet remains open.

## Back brief

Before executing any step of this plan, back brief the
operator: confirm the codec scope (zlib/zero/raw/ignore
with typed refusals for the rest), the EIO-parity error
semantics (no zero-fill for gaps/truncation — inverted from
phases 2-4), the bounded-memory caps and their divergence
fixture, the reader-before-variant ordering, that
map/measure/resize keep their raw-pass-through pins, and
that instar-testdata changes are management-reviewed before
the authorised commit to its main.
