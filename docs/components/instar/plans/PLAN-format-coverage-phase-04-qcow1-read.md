# Format coverage phase 4: QCOW1 convert-from (read path)

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

QCOW1 ("qcow" v1, qemu's original deprecated format) is
nominally detect + info only. **This plan deliberately
references
[PLAN-format-coverage-phase-02-vdi-read.md](/components/instar/plans/PLAN-format-coverage-phase-02-vdi-read/)
and
[PLAN-format-coverage-phase-03-parallels-read.md](/components/instar/plans/PLAN-format-coverage-phase-03-parallels-read/)
for everything structural** — the chain-reader pattern, the
bench `read_family` allowlist, the fixture/baseline/oslo
workflow, and the fuzzing shapes. Only QCOW1-specific facts
and deltas follow. Research was done 2026-07-18 by two
grounding passes (codebase survey + empirical verification
against qemu `block/qcow.c` for 6.0.0/7.0.0/master and the
static qemu-img matrix binaries 6.0.0 / 7.0.0 / 8.1.0 /
8.2.0 / 9.0.0 / 10.2.0 plus host 10.0.11), with the
load-bearing current-state claims re-pinned empirically by
the management session.

### The actual current state: qcow1 is misdetected as qcow2

The survey's initial reading — that qcow1 flows past the
issue-#444 gate and is silently read as raw — is wrong in
practice. Management-session empirical pin (2026-07-18):

* `detect_format_from_header`
  (`src/shared/src/format_detection.rs:129-137`) checks the
  4-byte BE magic against `QCOW2_MAGIC = 0x514649fb` FIRST
  and never consults the version field. A real qcow1
  image's magic IS `QFI\xfb`, so **every real qcow1 image
  detects as Qcow2**. The 3-byte `QCOW1_MAGIC` branch below
  it only matches "QFI" + a 4th byte that is not 0xfb —
  dead code for real images. The
  `docs/format-coverage.md:26` claim that instar detects
  QCOW1 is therefore wrong.
* Consequently `instar info` on a fresh
  `qemu-img create -f qcow` image prints `file format:
  qcow2`, `virtual size: 0` and a garbage qcow2
  format-specific block (`compat: 0.10`) — the qcow2 header
  parser rejects version 1 and info emits defaults.
  `instar convert` then fails with the misleading
  `Error: "input image has zero virtual size"`. Not silent
  raw output, but wrong info and a wrong error.
* **The silent-raw hazard is real but latent**:
  `chain::ImageFormat::from_str` already maps `"qcow1"` to
  a real `Qcow1` variant (`src/vmm/src/chain.rs:58`, with
  `supports_backing() == true` at `:73`), so the #444 gate
  would NOT refuse it, and the guest chain reader's `_ =>
  read_raw_sectors` default arm
  (`src/crates/qcow2/src/lib.rs:7379`) would read container
  bytes as raw. The moment detection is made version-aware
  without a reader arm in place, the hazard goes live.
  **Ordering constraint: the reader arm (4b) must land
  before the detection fix (4c).**
* A second latent parity gap found while pinning:
  `INFO_RESULT_FLAG_ENCRYPTED` (`src/vmm/src/main.rs:323`)
  is declared but **never consumed by either info emitter**
  — instar never prints qemu's `encrypted: yes` human line
  or JSON `"encrypted": true`. No fixture in the whole
  baseline tree currently produces the line (verified by
  grep, including the hand-maintained LUKS goldens, which
  match qemu in having no encrypted line for bare LUKS
  containers), so the gap is unexercised today — but the
  qcow1 AES fixture will be the first baseline to need it.

### QCOW1 format facts (all empirically verified)

Header: 48 bytes, all scalars **big-endian**. `qemu-img
create -f qcow` defaults: cluster_bits=12, l2_bits=9;
create **with a backing file** switches to cluster_bits=9
(512-byte clusters), l2_bits=12. `mtime` is always 0 —
create and convert are byte-deterministic.

| Offset | Field | Notes |
|--------|-------|-------|
| 0 | magic u32 | `QFI\xfb` (0x514649fb) — same as qcow2; version distinguishes |
| 4 | version u32 | must be 1 |
| 8 | backing_file_offset u64 | file offset of backing file name |
| 16 | backing_file_size u32 | name length; > 1023 refused |
| 20 | mtime u32 | always 0 on create; unused |
| 24 | size u64 | virtual size in bytes |
| 32 | cluster_bits u8 | accepted range [9, 16] |
| 33 | l2_bits u8 | accepted range [6, 13] |
| 34 | padding u16 | |
| 36 | crypt_method u32 | 0=none, 1=AES-128-CBC, >=2 refused |
| 40 | l1_table_offset u64 | **never validated** (0 / unaligned / past-EOF all tolerated at open) |

Open-time validation (identical 6.0.0 → master, zero
drift; each error prefixed `qemu-img: Could not open
'FILE': `, exit 1):

1. bad magic → "Image not in qcow format"; version != 1 →
   "qcow (v1) does not support qcow version N" (v2/v3
   append "Try the 'qcow2' driver instead.").
2. cluster_bits outside [9,16] → "Cluster size must be
   between 512 and 64k".
3. l2_bits outside [6,13] → "L2 table size must be between
   512 and 64k".
4. size <= 1 → "Image size is too small (must be at least
   2 bytes)"; oversized → "Image too large" (the bound is
   the L1-size arithmetic in `qcow_open`, dependent on
   cluster_bits + l2_bits — step 4a derives it from
   qcow.c and step 4d pins the empirical boundary the way
   phase 3 pinned the parallels tracks cap).
5. crypt_method >= 2 → "invalid encryption method in qcow
   header". crypt_method == 1 (AES): `info` works
   (metadata-only) and adds the encrypted line; any
   data-reading op fails at open with "Parameter
   'encrypt.key-secret' is required for cipher".
6. backing_file_size > 1023 → "Backing file name too
   long".

Probing nuance: without `-f`, qemu routes `QFI\xfb` by
magic+version — version 1 → qcow driver, version 2/3 →
qcow2 driver; a broken-magic file probes as raw. Forced
`-f qcow` is required to exercise the qcow driver's own
validation strings on malformed fixtures.

Read/lookup math (verified structurally on a populated
image):

* Two-level table: `l1_index = offset >> (l2_bits +
  cluster_bits)`; `l2_index = (offset >> cluster_bits) &
  (l2_size - 1)`. L1 and L2 entries are u64 BE; entry 0 =
  unallocated (**backing file if present, else zeros** —
  unlike VDI/Parallels, unallocated must fall through the
  chain).
* Uncompressed L2 entry = absolute file byte offset of the
  cluster.
* Bit 63 set = compressed: `coffset = entry &
  ((1 << (63 - cluster_bits)) - 1)`; `csize_bytes =
  (entry >> (63 - cluster_bits)) & ((1 << cluster_bits) -
  1)` — size is in BYTES (byte-packed, may be unaligned).
  Verified example: entry `0x81d0000000002000` at
  cluster_bits=12 → coffset 0x2000, csize 58.
* Compressed clusters are **raw DEFLATE** (windowBits -12,
  NO zlib wrapper — `zlib.decompress` fails on them,
  `decompressobj(-12)` succeeds). Do not reuse the qcow2
  two-try zlib-first helper; qcow1 is raw-deflate-only.
* Backing files: header stores ONLY the name (no format
  field); qemu resolves the backing format by probing at
  open. Relative names resolve against the overlay's
  directory. `-F qcow` is an accepted backing-format hint
  (including for qcow2 overlays over qcow1 backings).
  Read-through and overlay-masking byte-verified.
* Past-EOF / truncation: data cluster past EOF, L2 table
  past EOF, file truncated mid-cluster, truncated L1/L2 —
  ALL zero-fill (rc=0), never an error, **identical on
  6.0.0/8.1.0/8.2.0/10.2.0 — no parallels-style 8.1.x
  refusal window**.
* Odd sizes: `create` rounds the size up to the next
  512-multiple (1048577 → 1049088 stored); odd sizes never
  reach the header via qemu tooling. (Behaviour of a
  byte-patched odd header size is unpinned — 4d pins it.)

info output:

* Human: image / file format `qcow` / virtual size / disk
  size / [`encrypted: yes` when AES, between disk size and
  cluster_size] / `cluster_size: N` — cluster_size IS
  printed for qcow (unlike parallels; no suppression
  needed). `backing file:` line when present. NO
  format-specific block. The 8.0.0+ child-node block drift
  is the generic one the profile system already captures.
* JSON: core keys incl. `cluster-size` (always) and
  `dirty-flag`; `encrypted: true` when AES;
  `backing-filename`/`full-backing-filename` (no
  backing-format key). `children` key from 8.0.0.
* **No deprecation warning** on create or read, any
  version, either stream.

Other subcommands (version-stable): `check` → "This image
format does not support checks" **rc=63** — parity with
instar's not-supported exit for once (qemu's wording lacks
the "(fmt)" parenthetical instar prints; pin, don't chase).
`map` and `measure` (of a qcow1 source) WORK in qemu —
instar keeps its refusals (recorded divergence,
fuzzer-gated, master-plan future work). `dd` and `bench`
read qcow1 fine. `qemu-img convert -c -O qcow` **writes a
valid compressed image but exits 1 with empty stderr** — a
qemu quirk the fixture generator must tolerate (validate by
roundtrip, not rc). `measure -O qcow` (as target) is
refused by qemu; irrelevant to instar.

oslo.utils (git master, executed live): detects qcow1 as
**`qcow2`** (magic-only, ignores version) with the CORRECT
virtual size (size u64 sits at offset 24 in both formats);
`safety_check()` raises `SafetyCheckFailed`;
`get_inspector('qcow')` is None.

### Codebase deltas beyond the phase-2/3 template

* **Naming**: instar currently emits `"qcow1"`
  (`format_to_str`, `src/operations/info/src/main.rs:625`;
  chain Display `chain.rs:103`) where qemu-img and oslo say
  `"qcow"`. Byte-parity baselines force the rename: emit
  `"qcow"`, accept both `"qcow"` and `"qcow1"` in
  `from_str`. `instar-testdata/scripts/generate-baselines.py`
  whitelists use `'qcow'` in the check/compare lists but
  `'qcow1'` in the measure/info source lists — align to
  `'qcow'`.
* Info has NO qcow1 arm — qcow1 falls into the wildcard
  `_ =>` arm (`src/operations/info/src/main.rs:594-609`)
  which reports file size as virtual size. A real arm must
  parse size, cluster_bits, backing file, crypt_method.
  `send_info_result` already carries
  `backing_file`/`external_data_file` params
  (`src/shared/src/lib.rs:837`), and the host backing-chain
  machinery (`discover_backing_chain`,
  `validate_backing_path`, per-position format re-probe) is
  fully generic — a format-less backing reference is
  already handled (each chain position is independently
  probed), so reporting the name is all that's missing.
* Chain reader integration points as of phase 3:
  features + optional deps in `src/crates/qcow2/Cargo.toml`;
  `ChainStates` (`lib.rs:7537`), `init_chain_states`
  (`:7567`), `read_chain_virtual_cluster` dispatch
  (`:6521`; Vdi one-lookup-per-chunk arm `:7160`, Parallels
  per-cluster-walk arm `:7229`); `read_offset_sectors` cfg
  gate (`:2838`); Makefile test line (`Makefile:514`);
  consumer features in convert/compare/bench/rebase
  Cargo.tomls; bench `read_family`
  (`src/operations/bench/src/main.rs:265`) — **next free
  family number is 7**.
* Decompression: qcow2 and vmdk each use `miniz_oxide` 0.9
  (`no_std`, behind their `decompress` features). qcow1
  needs raw-deflate-only inflation in the reader arm
  (which lives in the qcow2 crate, so qcow2's existing
  miniz dependency is reachable there).
* Encryption refusal precedent: the qcow2
  LUKS-without-passphrase guest-side refusal
  (`src/operations/convert/src/main.rs:351`,
  `debug_print` + `send_complete(false)`).

## Design

### Scope

convert, compare, dd, and bench gain qcow1 input, including
backing chains and compressed clusters. check stays
not-supported (rc 63 — actual parity with qemu here). map
and measure stay refusals — a recorded divergence (qemu
supports both on qcow1), fuzzer-gated like vdi/parallels,
already on the master plan's future-work list. Encrypted
(AES, crypt_method=1) qcow1 is refused cleanly by the
reader — parity in effect, since keyless qemu also refuses;
AES decryption (instar has the machinery from qcow2
crypt_method=1) is future work. No write/create/output
support.

### Detection fix is the graduation

Make detection version-aware: `QFI\xfb` + version 1 →
Qcow1; version anything-else keeps the current qcow2 route
(whose open-time version check produces the refusal — the
same division of labour qemu's probes use). Because
`chain.rs` already maps qcow1 past the #444 gate, this
detection change alone flips qcow1 from
"misdetected-as-qcow2, accidentally refused" to
"read by the new arm" — there is no separate gate lift.
It lands in 4c, strictly after 4b's reader arm exists.
The dead 3-byte-magic branch is removed in the same
commit.

### Parser placement: new `src/crates/qcow1/`

Deviation from the master plan's "likely inside
`src/crates/qcow2/` as a sibling parser" note, for
consistency with the vdi/parallels precedent: own crate,
own unit tests, own fuzz targets, feature-gated optional
dep of the qcow2 crate (`qcow1-input = ["dep:qcow1",
"decompress"]` — decompress because the arm inflates
compressed clusters). The crate parses the header with
qemu's exact RO rules and exposes
`Qcow1State::init`/`block_lookup` in the
VdiState/ParallelsState shape, extended with a
`Compressed { host_offset, csize }` lookup result and
with init refusing `crypt_method != 0`.

### Reader arm shape

The arm combines three precedents:

* **Per-cluster walk** (Parallels arm): qcow1 clusters go
  down to 512 bytes — far smaller than chunk sizes — so
  one-lookup-per-chunk is wrong.
* **Backing fall-through** (Qcow2 arm): unallocated
  clusters must descend to the next chain device, NOT
  zero-fill. qcow1 is the first non-qcow2 format with
  backing support; 4b must study how the Qcow2 arm signals
  unallocated to the chain walker and mirror it exactly.
* **Capacity-clamped zero-fill** (VDI/Parallels arms) for
  allocated-but-past-EOF and straddling reads — qemu
  zero-fills all truncation shapes on every version.

Compressed clusters: read `csize` bytes from the (possibly
unaligned) host offset via the byte-accurate path, inflate
as RAW deflate (windowBits -12; miniz flags WITHOUT the
zlib-header bit — not the qcow2 two-try helper), then copy
the requested span. Inflate failure is a clean guest
failure.

### Info arm, naming, and the encrypted line

The info op gains a real Qcow1 arm: virtual_size = header
size verbatim; cluster_size = 1 << cluster_bits (emitted
normally — qemu prints it for qcow; no suppression);
backing file name passed through `send_info_result`
(bounds-checked read of backing_file_offset/size);
`FLAG_ENCRYPTED` when crypt_method != 0. The emitted format
string becomes `"qcow"` everywhere (with `"qcow1"` kept as
an accepted input alias).

New encrypted-line emitters, driven by the
never-yet-consumed `INFO_RESULT_FLAG_ENCRYPTED`: human
`encrypted: yes` between disk size and cluster_size; JSON
`"encrypted": true` with placement pinned from qemu JSON
output. Guards: no existing baseline contains the line, but
the LUKS info parser DOES set the flag and qemu prints no
encrypted line for bare LUKS containers (the goldens
confirm) — so the emitter must be gated to keep the LUKS
goldens byte-identical (pin qemu's actual per-format rule
and mirror it; a full `test_info_safe` run is the
acceptance gate). If encrypted qcow2 gets the line for free
(qemu prints it there), record it in findings — it is a
pre-existing instar gap this fixes with no baseline churn.

### Fixtures and baselines

Deterministic `generate-qcow1-fixtures.py` in
instar-testdata (create/convert are byte-deterministic;
mtime always 0; the compressed fixture's `convert -c` step
must tolerate qemu's exit-1-despite-valid-output quirk and
validate by roundtrip instead). Safe:

* `qcow1-data` — convert of a sparse patterned raw
  (default 4 KiB clusters), scattered allocation.
* `qcow1-compressed` — `convert -c` twin of qcow1-data
  (compressed clusters; compare-identical to its twin).
* `qcow1-backing` — overlay + base pair with a relative
  backing name; create-with-backing naturally uses
  cluster_bits=9, so this doubles as the small-cluster
  (512-byte) walk coverage.
* `qcow1-encrypted` — crypt_method byte-patched to 1.
  Safe for info baselines (qemu info works and prints the
  encrypted line); data ops expect refusal on both sides.
* `qcow1-past-eof` — one data-cluster offset patched far
  past EOF; zero-fills on every qemu version (no 8.1.x
  window).

Malformed (skip_qemu_img, expected_error from the verified
strings, validated with forced `-f qcow` since probing
would fall back elsewhere): `qcow1-bad-cluster-bits` (17),
`qcow1-bad-l2-bits` (14), `qcow1-huge-size` (at the
empirically-pinned "Image too large" boundary),
`qcow1-crypt-invalid` (crypt_method=2),
`qcow1-backing-name-too-long` (backing_file_size=1024).

4d also pins two open edges for the record (fixtures only
if version-stable): a byte-patched odd header size, and
qemu's probe routing for `QFI\xfb` with version 0 / 99
(documents the detection-parity edge for the version-aware
split).

Baselines: info for the five safe images (+ the backing
base if manifested), restricted manifest, `--no-commit`;
detect-profiles with `--no-commit --output-type` and the
integrity spot-check (phase-2/3 lessons). Oslo:
KNOWN_FORMAT_DIVERGENCES ('qcow', 'qcow2') per safe
fixture; virtual sizes AGREE (offset-24 coincidence) so no
vsize entries — verify live; malformed set →
OSLO_SKIP_IMAGES; handle the safety_check exception per the
test's existing flow.

### Fuzzing

`fuzz_qcow1_header` + `fuzz_qcow1_table` (two-level walk +
compressed-entry decode invariants — host offset/csize
bounds). Differential: FORMATS += 'qcow' (qemu's create
name); plain creates (no encryption — both sides refuse,
no signal); optionally a compressed variant via
`convert -c` if the harness tolerates the rc=1 quirk
cleanly, else log-and-skip; gate op_map ('raw', 'vdi',
'parallels', 'qcow') and op_measure ('vdi', 'parallels',
'qcow'). ~200-iteration forced burn-in, zero divergences
expected, isolated replay for any hit.

## Out of scope for this phase

* AES decryption of encrypted qcow1 (future work; refusal
  is behavioural parity with keyless qemu).
* map/measure support for qcow1 (recorded divergence —
  qemu supports both; master-plan future-work item).
* qcow1 write/create/output; `convert -c -O qcow` output
  parity (instar does not write qcow).
* Fixing oslo's qcow1→qcow2 misdetection upstream
  (recorded; divergence entries absorb it).

## Step-level guidance

Steps mirror phases 2/3; briefs give only the QCOW1 deltas
and assume the implementing agent reads the phase-3 plan's
corresponding step (and the referenced commits) first.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | New `src/crates/qcow1/` crate modelled on `src/crates/parallels/` (read it + its 3a brief first). BE header per this plan's table. `Qcow1Header::parse` enforcing qemu's exact RO rules: magic 0x514649fb + version == 1; cluster_bits in [9,16]; l2_bits in [6,13]; size >= 2; the "Image too large" bound derived from qcow_open's L1-size arithmetic (read qemu block/qcow.c 7.0.0 — the l1_size computation and its INT_MAX-class cap; encode the same arithmetic symbolically and unit-test the boundary; 4d pins it empirically like phase 3's tracks cap, so reference the constant/logic in one place); crypt_method <= 1 at parse (>= 2 refused, matching qemu); backing_file_size <= 1023; l1_table_offset deliberately NOT validated (qemu has none — 0/unaligned/past-EOF tolerated; reads later zero-fill). Store cluster_size = 1 << cluster_bits, l2_size = 1 << l2_bits, virtual_size = size verbatim, backing_file_offset/size, crypt_method. `Qcow1State::init`/`block_lookup` in the ParallelsState signature shape: init refuses crypt_method != 0 (reader-level refusal; parse stays lenient for info's benefit); two-level lookup per the Situation math via the shared cached-sector macro (entries are u64 BE — check whether a `read_u64_be_cached` exists in shared; add it following the macro pattern if not); L1 entry 0 or L2 entry 0 or index out of range → Unallocated; bit 63 → `Compressed { host_offset = entry & ((1 << (63 - cluster_bits)) - 1), csize = (entry >> (63 - cluster_bits)) & ((1 << cluster_bits) - 1) }` (unit-test the empirical example: entry 0x81d0000000002000, cluster_bits 12 → 0x2000/58); else Allocated at the absolute byte offset + in-cluster offset, all checked arithmetic. Unit tests: every validation boundary accept/reject, the populated-image lookup example (l1[0]=0x1000, L2[0..15]=0x2000..0x11000), compressed decode, crypt refusal at init but parse-ok, backing-name length, no-l1-validation tolerance. no_std, panic-free, workspace member; make test-rust/lint/instar clean. |
| 4b | high | opus | none | Guest integration mirroring 3b (read the Parallels arm at qcow2 lib.rs:7229 and 3b's brief first). `qcow1-input = ["dep:qcow1", "decompress"]` feature in src/crates/qcow2/Cargo.toml; `ChainStates.qcow1_states`; `init_chain_states` arm (cache-slot reuse; init failure — including the crypt refusal — propagates like the other formats). Reader arm: per-cluster walk (clusters can be 512 B, smaller than any chunk); **Unallocated falls through to the backing device exactly as the Qcow2 arm does — study how that arm signals unallocated to the chain walker and mirror it; do NOT zero-fill like the Vdi/Parallels arms. Report the mechanism in your findings** (first non-qcow2 backing format). Allocated: capacity-clamped zero-fill for past-EOF/straddle (qemu zero-fills all truncation shapes on all versions), then the aligned/unaligned read split; note uncompressed L2 entries are absolute byte offsets. Compressed: read csize bytes from the possibly-unaligned host offset (read_offset_sectors — widen its cfg gate to include qcow1-input), inflate RAW deflate only (miniz_oxide without TINFL_FLAG_PARSE_ZLIB_HEADER — do NOT reuse qcow2's zlib-first two-try helper), copy the span; inflate failure = clean guest failure. Enable the feature in convert/compare/bench/rebase Cargo.tomls (rebase newly pulls miniz via decompress — report the binary delta) and extend the Makefile qcow2 test features line. Unit tests per the 3b set plus: unallocated-descends-to-backing (multi-device mock — follow the Qcow2 arm's existing chain tests), a compressed cluster (real raw-deflate blob), 512-byte-cluster walk across a chunk, past-EOF clamp, straddle. Behaviour unchanged this step (detection still routes real qcow1 images to the qcow2 parser); suites stay green. make instar/check-binary-sizes/test-rust/lint clean; report all binary deltas. |
| 4c | high | opus | none | Detection fix + info + naming + encrypted emitters + pins, as separate commits in this order. Commit 1 (info + naming): real Qcow1 arm in src/operations/info/src/main.rs (replace the wildcard fall-through for Qcow1): virtual_size = header size verbatim, cluster_size = 1 << cluster_bits, backing file name (bounds-checked read at backing_file_offset/size, pass through send_info_result; set the has-backing flag the way the host expects — read how discover_backing_chain consumes it), FLAG_ENCRYPTED when crypt_method != 0; format_to_str Qcow1 => "qcow\0"; chain.rs from_str accepts "qcow" AND "qcow1", Display => "qcow". Commit 2 (encrypted emitters): consume INFO_RESULT_FLAG_ENCRYPTED in BOTH host emitters — human `encrypted: yes` between disk size and cluster_size, JSON `"encrypted": true` with key placement pinned from real qemu `info --output=json` on an AES qcow1; the LUKS info parser sets the flag but qemu prints NO encrypted line for bare LUKS (the hand-maintained goldens confirm) — pin qemu's actual rule and gate the emitter so the LUKS goldens stay byte-identical; full test_info_safe run must pass with zero regressions; if encrypted qcow2 images now also gain the line, that is a pre-existing-gap fix — record it (no fixture covers it, so no baseline churn). Commit 3 (detection = graduation; REQUIRES 4b merged): version-aware split in detect_format_from_header — QFI\xfb + version u32 BE @4 == 1 => Qcow1, else the existing Qcow2 route; delete the dead 3-byte-magic branch (keep constants coherent); bench read_family `Qcow1 => Some(7)` + doc-comment update. Commit 4 (pins): convert/compare/dd smoke parity vs qemu-img on a locally created qcow image (fixtures may not have landed; create with the host binary); backing-chain smoke (overlay reads through base); check → rc 63 clean (instar's message includes the format name, qemu's does not — record wording, don't chase); map/measure refusals recorded (divergence vs qemu documented in the test comments); encrypted image: info succeeds with the new line, convert refuses cleanly (record message); confirm the old misdetection behaviour (qcow2 garbage info) is gone. Run the touched suites + full test_info_safe. |
| 4d | high | opus | none | Testdata + manifest, mirroring 3d (read its brief + findings first; --no-commit posture, --output-type surgical scope, integrity spot-check; NEVER commit in instar-testdata). scripts/generate-qcow1-fixtures.py emitting the ten fixtures per Design; the compressed fixture's `convert -c -O qcow` step exits 1 despite writing a valid image (verified qemu quirk, all versions) — tolerate rc=1 and validate by roundtrip md5 instead; validate every safe fixture on 6.0.0 AND 10.2.0 (info + convert md5 agreement) and every malformed one refused by 10.2.0 with the expected string under forced `-f qcow` (record what unforced probing does with each). Build qcow1-huge-size at the empirically-pinned "Image too large" boundary (find smallest-refused like phase 3's tracks-cap correction; if 4a's symbolic bound disagrees, fix the crate constant in lock-step and report). Pin the two open edges (odd patched header size; probe routing for version 0/99) across 6.0.0/8.2.0/10.2.0 and report — add fixtures only if version-stable. Generate info baselines (restricted manifest) + detect-profiles + spot-check. Update generate-baselines.py measure/info source whitelists from 'qcow1' to 'qcow'. In instar: manifest entries for all ten (backing pair follows the existing backing-fixture precedent — find one in the manifest first), oslo updates per Design (verify live in a venv that instar 'qcow' vs oslo 'qcow2' diverges and vsizes agree; handle the SafetyCheckFailed per the test's flow; malformed → OSLO_SKIP_IMAGES). |
| 4e | medium | opus | none | Integration tests mirroring 3e (read its brief + the phase-3 test commits first): convert-to-raw parity for all safe qcow1 fixtures; qcow2 + vpc conversions for qcow1-data (flatten-compare); compressed twin compare-identical to qcow1-data and converts byte-identically; backing-chain tests (qcow1 overlay through base — copy the base into the tmpdir per the backing-path restriction; plus a qcow2 overlay created with `-F qcow` over a qcow1 backing); windowed dd crossing an allocation hole (qemu-img dd count is absolute-from-offset-0); compare identical + differing pairs; encrypted fixture: info parity (first baseline with the encrypted line), convert/dd refuse cleanly both sides; adversarial refusals for the five malformed ids (convert/compare/dd non-zero clean, no hang) — NOTE the info-leniency posture differs from vdi/parallels: instar's new info arm parses more than magic+version, so decide and PIN per-fixture what info does on each malformed image (qemu info under forced -f refuses them; unforced probing may not) and document; small-cluster end-to-end via qcow1-backing. Full eight-suite runs (convert, compare, dd, adversarial, info_safe, oslo, bench, rebase — consumer-suite rule). Isolated re-run before believing any failure (KVM-contention pattern). |
| 4f | medium | sonnet | none | Fuzzing mirroring 3f (read its brief + the phase-3 fuzz commit first): fuzz_qcow1_header + fuzz_qcow1_table (drive both compressed and uncompressed entries from fuzz bytes; assert the Compressed host_offset/csize and Allocated offset invariants), fuzz Cargo wiring, make fuzz-build + 60s local runs each (exec/s reported, crashes blocking). Differential: FORMATS += 'qcow'; generate_image branch (plain `qemu-img create -f qcow`; no encryption; optionally a compressed variant via `convert -c` tolerating the rc=1 quirk if the harness stays clean — else log-and-skip with a comment); gate op_map ('raw','vdi','parallels','qcow') and op_measure ('vdi','parallels','qcow') citing this plan; ~200-iteration forced burn-in, zero divergences expected, isolated replay for any hit. |
| 4g | medium | sonnet | none | Docs + close-out mirroring 3g (read its brief first): format-coverage.md — new qcow input-table row, CORRECT the wrong ":26 QCOW1 detection Yes" claim (it was misdetected as qcow2 until this phase), fixture inventory, narrative; quirks.md phase-4 section (misdetection fix + version-aware split; "qcow" naming with "qcow1" alias; encrypted-line emitters and the LUKS gating; backing fall-through; raw-deflate compressed clusters; past-EOF zero-fill parity; map/measure divergence vs qemu; check-wording note; qemu's convert -c rc=1 quirk; oslo qcow1→qcow2 misdetection); README (qcow read-only input); CHANGELOG; ARCHITECTURE (crates/qcow1); master plan row 4 Complete + Bugs entries (the misdetection defect incl. garbage info/misleading convert error, and the never-consumed encrypted flag — both found 2026-07-18 during phase-4 planning, empirically pinned) + future-work (qcow1 AES decryption; map/measure already listed); index.md; this file Status Complete + fill Findings from the step reports. Consistency grep for stale "qcow1" claims. |

Sequencing: 4a → 4b → 4c strictly (the detection fix must
not land before the reader arm — see Situation); 4d
parallel with 4a-4c (fixtures need only qemu-img; its
instar-side manifest/oslo edits land after 4c); 4e after
4c + 4d; 4f after 4c; 4g last.

## Findings: step 4a — qcow1 crate

* `src/crates/qcow1/` landed as planned: `Qcow1Header::parse` enforces
  qemu's exact RO open rules (magic `0x514649fb` + version == 1,
  `cluster_bits` in [9,16], `l2_bits` in [6,13], `size >= 2`,
  `crypt_method <= 1` at parse time — `>= 2` refused, matching qemu;
  `>= 2` is still permitted to construct a `Qcow1Header` for the info
  arm's own separate leniency posture where relevant — `backing_file_size
  <= 1023`, and `l1_table_offset` deliberately unvalidated, mirroring
  qemu's own lack of a check there (0 / unaligned / past-EOF are all
  tolerated at open and only surface at read time). The "Image too
  large" bound was derived symbolically from `qcow_open`'s L1-size
  arithmetic (`l1_size > INT_MAX/8` for the default cluster_bits=12/
  l2_bits=9 geometry) and unit-tested at the boundary; step 4d's
  empirical fixture sweep confirmed the derived boundary
  (562949951324160 accepted, 562949951324161 refused) exactly, so
  **no lock-step correction was needed this phase**, unlike phase 3's
  off-by-681 tracks-cap correction.
* `Qcow1State::init`/`block_lookup` follow the `ParallelsState` shape:
  `init` additionally refuses `crypt_method != 0` (a reader-level
  refusal on top of `parse`'s more lenient acceptance, so `info` can
  still report on encrypted images while data ops cannot open them).
  The two-level L1/L2 lookup uses a new `read_u64_be_cached` helper
  added to the shared cached-sector macro family (entries are u64 BE,
  unlike Parallels' u32 LE BAT entries); L1 entry 0, L2 entry 0, or an
  out-of-range index all resolve to `Unallocated`. Bit 63 marks a
  compressed cluster: `host_offset = entry & ((1 << (63 -
  cluster_bits)) - 1)`, `csize = (entry >> (63 - cluster_bits)) & ((1
  << cluster_bits) - 1)` (byte-granular, may be unaligned) — the
  empirical example (`entry = 0x81d0000000002000`, `cluster_bits =
  12` → `host_offset = 0x2000`, `csize = 58`) is a permanent unit
  test. `mtime` is parsed but unused, matching the empirical
  always-0 observation (create/convert are byte-deterministic).
* Unit tests cover every validation boundary (accept/reject at exact
  limits for cluster_bits, l2_bits, size, crypt_method,
  backing_file_size), the populated-image lookup example, the
  compressed-entry decode example, the crypt-refused-at-init-but-
  parse-ok split, and the no-l1-validation tolerance. `no_std`,
  panic-free, workspace member; `make test-rust`/`lint`/`instar`
  clean.

## Findings: step 4b — guest reader arm

* The `qcow1-input` feature (`= ["dep:qcow1", "decompress"]`) landed
  in `src/crates/qcow2/Cargo.toml`, with `ChainStates.qcow1_states`
  and an `init_chain_states` arm following the existing Vdi/Parallels
  cache-slot-reuse pattern.
* **Backing fall-through mechanism** (the phase's one genuinely new
  architectural piece): the QCOW1 arm signals "unallocated, recurse
  into the backing device" to the chain walker using the *exact same*
  sub-span mechanism the existing QCOW2 arm already uses for its own
  backing chains — an unallocated L1/L2 entry does not zero-fill
  locally; it emits a sub-span descriptor that the chain walker
  resolves by re-invoking the read against the next device in the
  chain (the backing image, if present, else zeros at the chain's
  base). This is a direct code-reuse of the QCOW2 arm's own
  unallocated-signalling path, not a new mechanism invented for
  QCOW1 — QCOW1 is simply the first *non-QCOW2* format to plug into
  it, since VDI and Parallels have no backing-file concept at all
  and their arms zero-fill unconditionally.
* The reader walks per-cluster (not one-lookup-per-chunk like the VDI
  arm), because QCOW1 clusters go down to 512 bytes — smaller than
  chunk sizes in convert/compare/bench/rebase. Compressed clusters
  read `csize` bytes from the (possibly unaligned) host offset via
  `read_offset_sectors` (its cfg gate was widened to include
  `qcow1-input`), inflate with `miniz_oxide` flags *without* the
  zlib-header-parsing bit (raw DEFLATE only — confirmed this is a
  distinct code path from the QCOW2 crate's zlib-first two-try
  helper, not a shared one), then copy the requested span; inflate
  failure is a clean guest failure, never a panic.
  Allocated-but-past-EOF and straddling reads use the same
  capacity-clamped zero-fill shape as the VDI/Parallels arms.
* The feature was enabled in convert/compare/bench/rebase
  `Cargo.toml`s and the Makefile's qcow2 test features line was
  extended. Binary deltas (from `make check-binary-sizes`):
  convert/compare/bench each grew ~+2.9 KB; rebase grew +19.5 KB,
  since it newly links `miniz_oxide` via the `decompress` feature for
  the first time (the other three consumers already linked it via
  QCOW2/VMDK compressed-cluster support). `info` also grew from the
  new QCOW1 header parser (landed in step 4c, measured together).
  All four deltas stayed well within the 768 KB per-binary cap.
* Unit tests cover the VDI/Parallels-equivalent set plus:
  unallocated-descends-to-backing (a multi-device mock following the
  QCOW2 arm's existing chain test pattern), a real raw-deflate
  compressed cluster, a 512-byte-cluster walk crossing a chunk
  boundary, past-EOF clamp, and a straddling read. Detection still
  routed real qcow1 images through the QCOW2 parser at this point (by
  design — the ordering constraint from the Situation section), so
  behaviour was unchanged and all three existing suites stayed green
  through this step.

## Findings: step 4c — detection fix, info, naming, encrypted emitters, pins

* Landed as four separate commits in the planned order: `3aa7f50`
  (info arm + naming), `467d24a` (encrypted-line emitters), `c421f75`
  (the detection graduation itself), `a0f757b` (empirical pins).
  Commit `c421f75` — the actual fix for the misdetection defect — was
  correctly sequenced *after* `77f32ca` (step 4b's reader arm),
  closing the silent-raw-read hazard window the Situation section
  flagged: at no point in the commit history did a version-aware
  detector exist without a real QCOW1 reader arm behind it.
* The info arm replaces the previous wildcard fall-through for QCOW1
  with a real parser: `virtual_size` = header size truncated down to
  the nearest 512-byte multiple (matching qemu's `total_sectors * 512`
  rule, the opposite of VDI's round-up), `cluster_size = 1 <<
  cluster_bits` (emitted normally — qemu does print cluster_size for
  qcow, unlike parallels, so no suppression mechanism was needed
  here), a bounds-checked backing-file-name read passed through
  `send_info_result`, and `FLAG_ENCRYPTED` set whenever
  `crypt_method != 0`.
* The encrypted-line emitters were pinned against real qemu
  `info --output=json` output on an AES QCOW1 image: human `encrypted:
  yes` between the disk-size and `cluster_size:` lines, JSON
  `"encrypted": true` positioned after `actual-size` and before
  `dirty-flag`. Gating on the `"luks"` format string specifically
  (not a generic "has a passphrase-shaped format" check) was
  necessary and sufficient to keep the LUKS goldens byte-identical —
  confirmed by a full `test_info_safe` run with zero regressions
  before this commit was accepted. Encrypted QCOW2 images gained the
  line automatically as a side effect (recorded as a pre-existing-gap
  fix, no baseline churn, since no fixture's golden covers that case).
* The detection split deletes the dead 3-byte-magic branch in the
  same commit that adds the version check, keeping the constant table
  coherent (no orphaned `QCOW1_MAGIC` constant left unused). The
  bench `read_family` allowlist gained `Qcow1 => Some(7)` (confirmed
  as the next free family number, per the Situation survey).
* Empirical pins (commit `a0f757b`): convert/compare/dd smoke parity
  against a locally-created qcow1 image; a backing-chain smoke test
  (overlay reads through base, confirming the 4b mechanism end-to-end
  before fixtures landed); `check` exits 63 cleanly (the wording
  difference from qemu's own refusal message — qemu's lacks the
  `"(qcow)"` parenthetical instar always includes — was recorded, not
  chased, since it is coincidental parity rather than a byte-pinned
  contract); `map`/`measure` refusals recorded as a divergence from
  qemu, which supports both; the encrypted fixture's `info` succeeds
  with the new line while `convert` refuses cleanly at open; and the
  pre-fix misdetection behaviour (qcow2-shaped garbage info) was
  confirmed gone by direct comparison against the old code path.

## Findings: step 4d — fixtures, baselines, and manifest/oslo

* `instar-testdata/scripts/generate-qcow1-fixtures.py` produced all
  twelve fixtures deterministically (create/convert are
  byte-deterministic; `mtime` always 0 means no UUID-style patching
  step was needed beyond each fixture's specific header byte-patch):
  seven safe (`qcow1-data`, `qcow1-compressed`, `qcow1-backing-base`,
  `qcow1-backing`, `qcow1-encrypted`, `qcow1-past-eof`,
  `qcow1-odd-size`) and five malformed (`qcow1-bad-cluster-bits`,
  `qcow1-bad-l2-bits`, `qcow1-huge-size`, `qcow1-crypt-invalid`,
  `qcow1-backing-name-too-long`).
* The compressed fixture's generation step tolerates qemu's
  `convert -c -O qcow` exit-1-despite-valid-output quirk (verified on
  every spot-checked version) by validating via roundtrip md5 instead
  of exit code — the same pattern the differential fuzzer's
  compressed-variant path uses.
* The `qcow1-huge-size` boundary fixture confirms step 4a's
  symbolically-derived "Image too large" cap needed **no** empirical
  correction this phase: 562949951324160 opens cleanly and
  562949951324161 is the smallest value 10.2.0 refuses (`"Image too
  large"`), matching the `INT_MAX/8` L1-entry arithmetic exactly —
  unlike phase 3's tracks cap, which needed a lock-step fix after
  fixture validation found the planning-stage arithmetic off by 681.
* Both open edges the plan asked to pin for the record turned out
  version-stable and got fixtures: `qcow1-odd-size` (header size
  byte-patched to 1048577; truncate-down to 1048576 confirmed
  identical on 6.0.0/8.2.0/10.2.0) and the version-0/99 probe-routing
  behaviour (documented in `docs/quirks.md` rather than fixtured,
  since it is a probe-routing curiosity with no data-safety angle,
  not a read-path behaviour a fixture would exercise).
* **No new baseline profile bucket was needed** — unlike phase 3's
  `parallels-bat-past-eof`, which forced a `profile-8-1-0` split for
  qemu's 8.1.0-8.1.5 past-EOF-BAT open-refusal regression, QCOW1's
  past-EOF behaviour (`qcow1-past-eof`) is zero-fill on **every**
  spot-checked qemu version with no regression window at all, so the
  existing seven profiles absorbed all twelve fixtures' baselines
  without any split or `test_info_safe` skip-mechanism engagement
  beyond what phase 3 already generalised.
* Every safe fixture was validated on both 6.0.0 and 10.2.0 (info +
  convert md5 agreement); every malformed fixture was confirmed
  refused by 10.2.0 with its expected error string under forced
  `-f qcow` (unforced probing behaves differently per fixture and was
  recorded, not relied on).
* `instar-testdata/scripts/generate-baselines.py`'s measure/info
  source whitelists were realigned from `'qcow1'` to `'qcow'` to
  match the check/compare lists, which already used `'qcow'`.
* Manifest entries for all twelve fixtures landed with
  `qcow1-backing`/`qcow1-backing-base` following the existing
  backing-fixture precedent (base referenced by relative path,
  `-F qcow` hint). Oslo `KNOWN_FORMAT_DIVERGENCES` entries record
  `('qcow', 'qcow2')` for every safe fixture, confirmed live: oslo
  detects qcow1 by magic alone (ignoring version, the same class of
  bug this phase fixed in instar) and reports the *correct* virtual
  size for every fixture except `qcow1-odd-size`, where oslo reads
  the header `size` field verbatim (1048577) with no truncation —
  the one fixture needing a `KNOWN_VSIZE_DIVERGENCES` entry. The five
  malformed fixtures joined `OSLO_SKIP_IMAGES`, and the
  `SafetyCheckFailed` exception oslo raises (no `get_inspector('qcow')`
  exists) is handled per the test's existing flow.

## Findings: step 4e — integration tests

* All eight consumer suites ran clean: `test_info_safe` 898 passed/0
  failed, `test_oslo_crossval` 267/0, `test_convert` 242/0,
  `test_compare` 62/0, `test_dd` 43/0, `test_adversarial` 88/0,
  `test_bench` 79/0, `test_rebase` 28/0 — plus a dedicated
  `qcow1_smoke` set of 12 tests. Zero failures across the board,
  confirming the consumer-suite rule (the qcow2 crate changed again)
  needed no bisection this phase.
* **Malformed-image `info` posture, pinned per-fixture as planned**:
  unlike VDI and Parallels, whose lenient `info` parsers only check
  magic/version and report best-effort nonzero fields on malformed
  input, QCOW1's new `info` arm validates the same fields the reader
  does (`cluster_bits`, `l2_bits`, `size`, `crypt_method`,
  backing-name length) and falls back to an **empty default** (virtual
  size 0) on any failure. All five malformed fixtures were pinned to
  this exact behaviour: `info` exit 0, format `"qcow"`, virtual size
  0 — a deliberate, tested posture difference from VDI/Parallels
  rather than an oversight, now documented in `docs/quirks.md` so a
  future phase doesn't assume all detect-then-refuse formats behave
  identically on malformed input. `convert`/`compare`/`dd` then refuse
  cleanly on the zero virtual size for all five, with no hangs.
* Backing-chain coverage included both the qcow1-over-qcow1 pair
  (`qcow1-backing` over `qcow1-backing-base`, copied into the tmpdir
  per the existing backing-path restriction) and a QCOW2 overlay
  created with `-F qcow` over a qcow1 backing, confirming the
  cross-format backing-chain machinery (already generic per the
  Situation survey) needed no changes beyond reporting the backing
  format name.
* The compressed twin (`qcow1-compressed`) compares identical to
  `qcow1-data` and converts to the same raw md5; the encrypted
  fixture (`qcow1-encrypted`) is the first baseline in the whole tree
  to carry the `encrypted: yes` / `"encrypted": true` info line, with
  `convert`/`dd` refusing cleanly on both sides (instar at open,
  keyless qemu with its own key-secret error).

## Findings: step 4f — fuzzing

* `fuzz_qcow1_header` and `fuzz_qcow1_table` (two-level walk +
  compressed-entry decode invariants) both ran crash-free in local
  60-second sessions: 1.3M exec/s for the header target, 98k exec/s
  for the (heavier, table-walking) table target.
* The differential fuzzer gained `'qcow'` in `FORMATS` (qemu's create
  name) with a plain-create path (no encryption — both sides would
  refuse identically, so an encrypted variant carries no differential
  signal) and gates `op_map` (`'raw', 'vdi', 'parallels', 'qcow'`) and
  `op_measure` (`'vdi', 'parallels', 'qcow'`) to skip the two
  recorded divergent operations, citing this plan in comments.
* A ~200-iteration forced burn-in against the differential harness
  produced zero divergences, matching the same clean-burn-in result
  every prior format-coverage phase has reported.

## Verification (management-session review checklist)

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified; instar-testdata
      changes are reviewed by the management session before
      the operator-authorised commit to its main.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes`
      (rebase newly links miniz — check its delta
      specifically).
- [ ] `make test-rust`, `make fuzz-build`, and the eight
      integration suites pass (consumer-suite rule: the
      qcow2 crate changed again).
- [ ] `pre-commit run --all-files` passes.
- [ ] All safe qcow1 fixtures convert byte-identically to
      qemu-img, including the compressed twin, the backing
      chain, and past-EOF zero-fill.
- [ ] Real qcow1 images now detect as "qcow" (misdetection
      gone); info output matches qemu baselines including
      the encrypted line; LUKS goldens byte-unchanged.
- [ ] check exits 63 cleanly; map/measure refusals pinned
      as recorded divergences; encrypted convert refuses
      cleanly.
- [ ] Differential fuzzer includes qcow with a clean
      burn-in.
- [ ] Commit messages follow project conventions.

## Success criteria

Phase 4 is complete when:

* instar convert, compare, dd, and bench accept qcow1 with
  byte parity against qemu-img across the fixture set,
  including compressed clusters, backing chains,
  small (512 B) clusters, and past-EOF zero-fill.
* Real qcow1 images detect as "qcow" (version-aware split;
  misdetection-as-qcow2 fixed) and info output is
  byte-parity including cluster_size, backing file, and
  the new encrypted line — with the LUKS goldens
  unchanged.
* Encrypted qcow1 is refused cleanly by data ops; the five
  malformed fixtures are refused cleanly; the safe
  fixtures are CI-exercised with info baselines.
* `src/crates/qcow1/` exists, no_std, panic-free, with
  unit + fuzz coverage; the differential fuzzer includes
  qcow.
* map/measure divergence vs qemu and the check-wording
  difference are pinned and documented.
* Docs and the master plan record the new state.

## Hand-off to later phases

* Phase 5 (DMG) inherits: the encrypted/flag emitter now
  exists; the per-cluster walk and backing fall-through
  arm shapes; the detection-fix-after-reader ordering
  lesson (DMG's trailer probe has the same
  hazard-goes-live-on-detection property).
* Phase 6 (QED) should note qcow1's check-parity accident
  (qemu refuses checks on qcow1 too) when pinning QED
  subcommand behaviour.
* Future work recorded on the master plan: qcow1 AES
  decryption; map/measure for the new input formats.

## Back brief

Before executing any step of this plan, back brief the
operator: confirm the scope (convert/compare/dd/bench;
check stays rc-63; map/measure stay refusals as recorded
divergences; encrypted qcow1 refused), the detection-fix
ordering constraint (reader arm first — the silent-raw
hazard is latent until detection changes), the "qcow"
naming decision, the encrypted-line emitter and its LUKS
gating, and that instar-testdata changes are
management-reviewed before the authorised commit to its
main.
