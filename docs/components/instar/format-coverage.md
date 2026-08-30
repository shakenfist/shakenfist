# Format Detection and Safety Check Coverage

This document tracks instar's format coverage along two axes:

1. **oslo.utils `format_inspector` parity** — instar's format detection and
   safety reporting compared against OpenStack's oslo.utils `format_inspector`
   module, ensuring instar detects all the same security-relevant metadata that
   OpenStack uses for image safety validation. This was the document's original
   charter, and instar meets it in full.
2. **qemu-img roster coverage** — which of qemu-img's real on-disk image-format
   drivers instar supports, broken down per subcommand, and exactly where
   instar's behaviour diverges from qemu-img. See
   [qemu-img parity axis](#qemu-img-parity-axis) below for the consolidated
   op × format matrix. This axis was added by
   [PLAN-format-coverage](/components/instar/plans/PLAN-format-coverage/) so future gaps land in
   a table rather than being rediscovered by archaeology.

## Important Distinction: Detection vs Rejection

**oslo.utils format_inspector** performs **safety validation** - it rejects images
that fail safety checks (e.g., QCOW2 with backing files, VMDK with path traversal).

**instar** performs **safety detection** - it reports security-relevant metadata
to the caller but does not reject images. This is because instar's KVM sandbox
architecture makes following these references impossible, so detection and
reporting is sufficient. See [format-detection-safety.md](/components/instar/format-detection-safety/)
for details on why this approach is secure.

---

## Format Detection Comparison

| Format | oslo.utils | instar | Test Images |
|--------|------------|-------|-------------|
| QCOW2 (v2/v3) | Yes | Yes | cirros-qcow2, qcow2-v2, many edge-cases |
| QCOW1 ("qcow") | No | Yes‡ | qcow1-data, qcow1-compressed, qcow1-backing, qcow1-backing-base, and 8 more (see below) |
| VMDK (monolithic sparse) | Yes | Yes | plaso-vmdk, vmdk-multi-partition |
| VMDK (stream optimized) | Yes | Yes | vmdk-streamoptimized |
| VMDK (v3/COWD) | Yes | Yes | vmdk-v3 |
| VHD/VPC | Yes | Yes | hyperv-dynamic-vhd, virtualpc-vhd, vhd-d2v-zerofilled |
| VHDX | Yes | Yes | qemu-vhdx, vhdx-disk2vhd |
| RAW | Yes | Yes | raw-mbr-partitioned, raw-gpt-partitioned, etc. |
| MBR partition table | Yes | Yes | raw-mbr-partitioned |
| GPT partition table | Yes | Yes | raw-gpt-partitioned |
| VDI | Yes | Yes | vdi-simple, vdi-data-dynamic, vdi-static-data, vdi-odd-size, and 6 more (see below) |
| QED | Yes (banned) | Yes | qed-simple |
| ISO | Yes | Yes* | iso-simple |
| LUKS | Yes | Yes | luks-v1, luks-v2, luks-v1-raw-gpt, luks-v1-qcow2, luks-v1-aes-xts |
| Parallels | No | **Yes** | parallels-v1, parallels-v2, parallels-data-v1, parallels-data-v2, and 7 more (see below) |
| Bochs | No | **Yes** | bochs-growing |
| cloop | No | **Yes** | cloop-simple |
| DMG | No | **Yes**† | dmg-simple, dmg-mixed, dmg-multipart, dmg-rsrc-fork, and 12 more (see below) |

*\* ISO detection is controlled by `--unsafe-quirks` flag: by default instar reports "iso", but with `--unsafe-quirks` it reports "raw" to match qemu-img behavior. See [quirks.md](/components/instar/quirks/) for details.*

*† DMG is detected by content — scanning the file's final 1024 bytes for the koly trailer magic — not by the `.dmg` filename extension qemu-img probes for. A misnamed DMG (no `.dmg` suffix) still detects under instar but probes as raw under qemu-img. See [quirks.md](/components/instar/quirks/) for details.*

*‡ QCOW1 detection was actually broken until the PLAN-format-coverage work (2026-07-18), despite this document previously claiming "Yes": `detect_format_from_header` checked the QCOW2 magic first and never consulted the version field, so every real QCOW1 image (whose magic is byte-identical to QCOW2's, `QFI\xfb`) misdetected as QCOW2 — producing garbage `info` output (virtual size 0, a qcow2-shaped `compat: 0.10` block) and a misleading convert error. Detection is now version-aware: `QFI\xfb` + version 1 routes to QCOW1 ("qcow"), any other version keeps the QCOW2 route. See [quirks.md](/components/instar/quirks/) for the full record.*

### Formats Not Yet Detected by Instar

All formats detected by oslo.utils are now also detected by instar.

---

## qemu-img parity axis

This section tracks instar's coverage against qemu-img's **actual** on-disk
image-format roster — the 14 formats instar detects — for every subcommand
surface, and records where instar diverges from qemu-img. It is the
consolidated op × format matrix that the per-op tables further down
(Resize / Rebase / Commit / DD, and the Other Format Safety Checks table)
provide the detail behind.

The underlying evidence for every divergence recorded here lives in the six
`docs/quirks.md` "Format-coverage phase" sections:
[Parallels, Bochs, cloop and DMG detection](/components/instar/quirks/#parallels-bochs-cloop-and-dmg-detection)
(detection, the #444 detect-only refusal, DMG raw pass-through in the in-place
ops),
[VDI](/components/instar/quirks/#vdi-convert-from-read-path) (VDI),
[Parallels](/components/instar/quirks/#parallels-convert-from-read-path)
(Parallels),
[QCOW1](/components/instar/quirks/#qcow1-convert-from-read-path)
(QCOW1),
[DMG](/components/instar/quirks/#dmg-convert-from-read-path) (DMG),
and
[QED](/components/instar/quirks/#qed-read-refusal-as-policy) (QED).
Cells with no recorded source were measured empirically on 2026-07-20 against
the built instar binary and qemu-img 10.0.11, using the existing
instar-testdata fixtures; those measurements are recorded in
[PLAN-format-coverage-phase-07-docs](/components/instar/plans/PLAN-format-coverage-phase-07-docs/).

**Legend**

| Symbol | Meaning |
|--------|---------|
| ✓ | instar supports the op with qemu-img parity |
| ✓‡ | instar supports the op where qemu-img **refuses** it (instar-only capability; recorded divergence) |
| R‡ | instar **refuses** the op where qemu-img supports it (recorded divergence) |
| R= | instar refuses and qemu-img also refuses / cannot perform it (parity refusal — neither tool supports it) |
| ~‡ | instar treats the container as **raw** (pass-through), not recognising the real format — diverges from qemu-img's format-aware handling (recorded divergence) |
| — | not applicable (format is not in the op's supported roster) |

Numbered cells carry a note in **Notes** below the tables.

### Read-side ops

These ops read an image without mutating it.

| Format | info | check | convert | compare | dd | bench | map | measure |
|--------|------|-------|---------|---------|----|-------|-----|---------|
| raw | ✓ | R= | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| qcow2 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| vmdk | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| vhd / vpc | ✓ | ✓‡ 9 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| vhdx | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓‡ 10 | ✓ |
| luks | ✓ | R= 16 | ✓ 16 | ✓ 16 | ✓ 16 | R= 16 | R= 16 | R= 16 |
| vdi | ✓ | R‡ 1 | ✓ | ✓ | ✓ | ✓ | R‡ 2 | R‡ 2 |
| parallels | ✓ | R‡ 3 | ✓ | ✓ | ✓ | ✓ | R‡ 2 | R‡ 2 |
| qcow (v1) | ✓ | R= 4 | ✓ | ✓ | ✓ | ✓ | R‡ 2 | R‡ 2 |
| dmg | ✓ | R= 5 | ✓ | ✓ | ✓ | ✓ | ~‡ 6 | ~‡ 6 |
| qed | ✓ | R‡ 7 | R‡ 7 | R‡ 7 | R‡ 7 | R‡ 7 | R‡ 7 | R‡ 7 |
| bochs | ✓ | R= 12 | R‡ 11 | R‡ 11 | R‡ 11 | R‡ 11 | R= 12 | R‡ 12 |
| cloop | ✓ | R= 12 | R‡ 11 | R‡ 11 | R‡ 11 | R‡ 11 | R= 12 | R‡ 12 |
| iso | ✓ | R= 13 | ✓ 13 | ✓ 13 | ✓ 13 | R‡ 13 | ✓ 13 | ✓ 13 |

### In-place ops

These ops mutate an existing image.

| Format | resize | rebase | commit | amend | snapshot | bitmap |
|--------|--------|--------|--------|-------|----------|--------|
| raw | ✓ | R= | R= | R= | R= | R= |
| qcow2 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| vmdk | ✓‡ 8 | ✓‡ 8 | ✓ 14 | R= | R= | R= |
| vhd / vpc | ✓‡ 8 | R= | R= | R= | R= | R= |
| vhdx | ✓‡ 8 | R= | R= | R= | R= | R= |
| luks | R= 16 | R= | R= | R= | R= | R= |
| vdi | R= | R= | R= | R= | R= | R= |
| parallels | R= | R= | R= | R= | R= | R= |
| qcow (v1) | R= | R= | R= | R= | R= | R= |
| dmg | ~‡ 6 | R= | R= | R= | R= | R= |
| qed | R‡ 7 | R‡ 7 | R‡ 7 | R= 7 | R= 7 | R= 7 |
| bochs | R= | R= | R= | R= | R= | R= |
| cloop | R= | R= | R= | R= | R= | R= |
| iso | ✓ 13 | R= | R= | R= | R= | R= |

### Output side

The convert-output / `create` / `dd`-output roster is unchanged by the
format-coverage programme: instar writes **raw, qcow2, vmdk, vpc (VHD), and
vhdx** only. qemu-img can additionally *create* vdi / parallels / qcow (v1) /
qed images; instar refuses those by scope, not by inability (note 15).

| Format | create / convert-output / dd-output |
|--------|--------------------------------------|
| raw | ✓ |
| qcow2 | ✓ |
| vmdk | ✓ |
| vhd / vpc | ✓ |
| vhdx | ✓ |
| luks | — 15 |
| vdi | R‡ 15 |
| parallels | R‡ 15 |
| qcow (v1) | R‡ 15 |
| qed | R‡ 15 |
| dmg | R= 15 |
| bochs | R= 15 |
| cloop | R= 15 |
| iso | — 15 |

### Notes

1. **VDI `check`** — instar refuses (exit 63, "does not support checks");
 qemu-img validates the VDI block map (rc 0). Recorded in quirks.md
 ("`check` Still Refuses VDI; `qemu-img check` Does Not").
2. **VDI / Parallels / QCOW1 `map` and `measure`** — instar refuses ("source
 format unrecognised" / "source image is unsupported format"); qemu-img
 supports both against these sources (rc 0). Deliberate scope divergence,
 tracked as master-plan future work; QCOW1's is recorded in quirks.md ("`check`, `map`, and `measure`"). Measured 2026-07-20 vs
 qemu-img 10.0.11.
3. **Parallels `check`** — instar refuses (exit 63); qemu-img has a Parallels
 check but **asserts and crashes** (`parallels_check_duplicate`) on
 adversarial BAT input on 10.x, where 6.0.0 reported cleanly — a real qemu
 regression. Recorded in quirks.md.
4. **QCOW1 `check`** — genuine parity: **both** refuse (qemu's qcow driver has
 no check implementation; instar exits 63 with a "(qcow)"-named message,
 qemu with a shorter one). Recorded in quirks.md.
5. **DMG `check`** — both refuse (exit 63). instar's message names the format
 **"raw"**, not "dmg", because `check`'s own dispatch never runs the koly
 trailer probe. Recorded in quirks.md ("`check` Names the Format
 '(raw)', Not '(dmg)'").
6. **DMG `map` / `measure` / `resize`** — instar treats the UDIF container as a
 **raw** disk image (the koly probe is wired only into the `info`/convert
 chain, not these paths), so it returns success against the wrong bytes;
 qemu-img handles the real DMG for `map`/`measure`, and under filename-probe
 auto-detection also reads a `.dmg` as raw for `resize`. Recorded in
 quirks.md ("DMG Pass-Through as Raw in the In-Place Ops") and
. Measured 2026-07-20.
7. **QED (all cells)** — instar refuses every op except `info` **by policy**
 (nil demand + oslo.utils' explicit QED ban), not by inability. qemu-img
 performs convert / compare / dd / bench / check / map / measure / resize /
 rebase / commit on QED (all rc 0), so those are recorded divergences;
 `amend` / `snapshot` / `bitmap` are R= because qemu-img refuses them on QED
 too. Full per-op record in quirks.md ("QED Read-Refusal Is
 Deliberate Policy, Not a Parity Gap").
8. **VMDK / VHD / VHDX `resize` and VMDK `rebase` — instar-only** — qemu-img
 refuses these on every shipped version ("Image format driver does not
 support resize / rebase"); instar performs them (monolithicSparse for
 vmdk). See the Resize and Rebase Format Support tables below. Measured
 2026-07-20.
9. **VHD `check` — instar-only** — instar runs full VHD footer/BAT validation
 (rc 0); qemu-img refuses `check` on vpc ("This image format does not
 support checks", exit 63). Measured 2026-07-20.
10. **VHDX `map` — instar-only** — instar emits the VHDX allocation map (rc 0);
    qemu-img `map` refuses dynamic VHDX ("File contains external, encrypted or
    compressed clusters", rc 1). Measured 2026-07-20.
11. **Bochs / cloop convert / compare / dd / bench** — instar refuses via the
    #444 detect-only gate ("detected but not supported for reading (detection
    and info only)"); qemu-img reads both (rc 0). Bochs and cloop are
    detect + info only in instar (see quirks.md). Measured 2026-07-20.
12. **Bochs / cloop `map` and `measure`** — instar refuses both. qemu-img
    **measures** both (rc 0) — an R‡ divergence — but its `map` also refuses
    both ("File contains external, encrypted or compressed clusters"), so `map`
    is an R= parity refusal. `check` is likewise R= (both exit 63). Measured
    2026-07-20.
13. **ISO** — instar reads ISO as **raw** for convert / compare / dd / map /
    measure / resize, matching qemu-img (which has no ISO driver and also reads
    it as raw) — the deliberate #444 ISO exemption (see quirks.md).
    `check` is R= (both exit 63, instar naming "raw"). The one divergence is
    `bench`, which instar refuses ("bench: unsupported input format") where
    qemu-img benches the raw container (rc 0). Measured 2026-07-20.
14. **VMDK `commit`** — parity via explicit `-b base.vmdk`; the implicit-`-b`
    form is blocked by the info-side `parentFileNameHint` gap. See the Commit
    Format Support table below.
15. **Output side** — instar's write roster (raw / qcow2 / vmdk / vpc / vhdx)
    is deliberately unchanged by this programme (master plan, "Explicitly out
    of scope: write/create/output support for any new format"). qemu-img
    additionally *creates* vdi / parallels / qcow / qed (rc 0), which instar
    refuses by scope (R‡). qemu-img itself refuses `create` for bochs / cloop /
    dmg ("Format driver ... does not support image creation"), so those are
    R= parity refusals. luks and iso are not standalone instar output formats
    (instar's only LUKS-output path is LUKS-encrypted qcow2 via
    `--luks-encrypt-passphrase`; qemu-img has no iso driver). Measured
    2026-07-20.
16. **LUKS non-decrypting ops** — instar's LUKS support is `info` plus
    decrypting convert / compare / dd via `--luks-passphrase` (see the Input
    Format Support table). `check` / `bench` / `map` / `measure` and every
    in-place op refuse; qemu-img cannot open a LUKS container for these without
    key material (`--object secret`) either, so on a bare LUKS fixture both
    tools refuse (R=). Measured 2026-07-20.

### vvfat

vvfat is a directory-backed qemu **pseudo-format**: it synthesises a FAT
filesystem over a host directory on the fly and has **no on-disk single-file
container**. There is therefore nothing for instar to detect or refuse at the
file level — vvfat cannot arrive as an image file, so it appears in no row of
the matrix above.

qemu-img cannot create one either. Re-verified 2026-07-20 against
qemu-img 10.0.11:

```
$ qemu-img create -f vvfat /path/to/dir
qemu-img: /path/to/dir: Format driver 'vvfat' does not support image creation
```

This satisfies the master plan's success-criteria clause — "Bochs, cloop,
vvfat (and QED …) produce clean, tested, documented refusals rather than
misdetection as raw" — for vvfat by documented rationale rather than code:
there is no on-disk artefact to misdetect, so no detection or refusal path is
owed.

---

## Conversion Output Format Support

The `instar convert` operation supports writing output in the following formats:

| Output Format | Status | Key Features |
|---------------|--------|--------------|
| **raw** (default) | Supported | Flat byte-for-byte output, sparse by default (`--no-skip-zeros` for dense) |
| **qcow2** | Supported | QCOW2 v3, 16-bit refcounts, configurable cluster size (512B-64KB), optional zlib compression (`-c`) |
| **vmdk** | Supported | monolithicSparse (default), streamOptimized with `-c`, monolithicFlat with `--subformat monolithicFlat`, configurable grain size (4KB-64KB via `--grain-size`) for sparse/streamOptimized |
| **vpc** (VHD) | Supported | Dynamic VHD, configurable block size (512KB+ via `--block-size`, default 2MB), BAT-based allocation |
| **vhdx** | Supported | Dynamic VHDX, configurable block size (1MB-256MB via `--block-size`, default 32MB), CRC-32C checksums |

### Input Format Support for Conversion

| Input Format | Status | Notes |
|--------------|--------|-------|
| raw | Supported | With MBR/GPT partition validation (unless `--unsafe-quirks`) |
| qcow2 (v2/v3) | Supported | Including compressed clusters (zlib and ZSTD), extended L2 entries, backing chain flattening |
| vmdk (monolithicSparse) | Supported | Grain directory/table two-level lookup, sector-cached reads |
| vmdk (streamOptimized) | Supported | DEFLATE decompression, footer-based GD offset resolution |
| vmdk (monolithicFlat) | Supported | Two-file descriptor + raw flat extent; descriptor is parsed host-side for extent discovery and allowlist validation, flat extent is opened as a second virtio-block device and reads are redirected via `ChainConfig.data_device_idx`. Descriptors with `parentFileNameHint` are followed as a backing chain. |
| vmdk (twoGbMaxExtentFlat) | Supported | Multi-extent flat descriptors with multiple flat extent files; each extent is opened as a separate virtio-block device with reads dispatched by offset. |
| vmdk (twoGbMaxExtentSparse) | Not supported | Multi-extent descriptors whose extents are themselves sparse VMDKs. Host-side descriptor resolution accepts FLAT extents only, so `instar info` fails with "only FLAT extents are supported". Unlike the flat case, each extent carries its own sparse header and grain directory, so extents cannot simply be mapped as raw byte ranges. Real-world fixture `osboxes-vmdk-split-sparse` (26 extents) is registered with baselines captured, and is marked `instar_unsupported` in `tests/manifest.json` so parity tests skip until support lands. |
| vhd (fixed) | Supported | Raw sector reads with footer validation |
| vhd (dynamic) | Supported | BAT-based block lookup, sector-cached reads |
| vhdx (dynamic) | Supported | 64-bit BAT with interleaved SB entries, GUID-based metadata, CRC-32C validation |
| vdi (dynamic and static) | Supported | Header validated against qemu's 12 open-time rules; allocation-order block-map lookup, sector-cached reads; qemu parity for discarded blocks, past-EOF reads (zero-fill), and odd `disk_size` (rounded up to 512) |
| parallels (v1 and v2/ext) | Supported | Open validated against qemu's RO rules (tracks/bat_entries limits, both magics); per-magic BAT decoding (sector-valued under v1, cluster-valued under v2), sector-cached reads; past-EOF and out-of-BAT reads zero-fill, `inuse`-dirty images read normally, `ext_off != 0` refused |
| qcow (v1) | Supported | Header validated against qemu's exact RO rules (cluster_bits/l2_bits ranges, size bounds incl. the "Image too large" boundary, crypt_method, backing-name length); per-cluster walk (clusters down to 512 B), backing-chain fall-through to the next chain device on unallocated clusters (the first non-QCOW2 backing format), raw-DEFLATE compressed clusters (no zlib wrapper, unlike QCOW2's zlib-first two-try); past-EOF/truncated data clusters zero-fill; odd header sizes truncate down (opposite of VDI's round-up); encrypted (AES, crypt_method=1) images: `info` works and reports `encrypted: yes`, data ops refuse cleanly |
| dmg (UDIF) | Supported | koly-trailer detection (content-based, not the `.dmg` extension qemu-img probes for); XML-plist AND old resource-fork chunk-table paths, lenient (glib-parity) base64; zero/raw/ignore/zlib chunk codecs, typed refusals naming the codec for ADC/bzip2/lzfse/zstd/unknown; **reads ERROR on gaps and truncation — never zero-fill**, the inverse of every other format's posture; bounded-memory caps on plist size/chunk count/per-chunk staging (documented divergence from qemu's larger legal range); supported at any backing-chain position; `check`/`map`/`measure`/`resize` unaffected (retained raw pass-through / generic refusal) |
| luks (v1/v2, native) | Supported | Decrypts with `--luks-passphrase`; v1 PBKDF2, v2 Argon2id (`--max-guest-memory`); detects inner format (raw, QCOW2) |
| luks wrapping qcow2 | Supported | Transparent inner QCOW2 detection and decryption via CallTable function pointer wrapping |

### Limitations

- Compressed clusters up to 2MB (MAX_CLUSTER_SIZE) are fully supported. Both
  the decompression staging buffer and compressed input buffer handle up to
  MAX_CLUSTER_SIZE + MAX_SECTOR_SIZE (2MB + 64KB).
- QCOW2 legacy AES-128-CBC encryption (crypt_method=1) is supported via
  `--qcow2-password`. LUKS-in-QCOW2 encryption (crypt_method=2) is supported
  via `--luks-passphrase`. LUKS-encrypted QCOW2 output is supported via
  `--luks-encrypt-passphrase` (AES-256-XTS with PBKDF2-SHA256 key derivation,
  LUKS v1 headers). Encrypted output cannot be combined with
  compression. Native LUKS containers (v1 with PBKDF2, v2 with
  Argon2id) are supported via `--luks-passphrase` (v2 also requires
  `--max-guest-memory`). LUKS containers wrapping QCOW2 images are
  transparently detected and the inner QCOW2 is processed as the
  conversion source.
- QCOW2 snapshots: snapshot table parsing and extraction via
  `convert --snapshot <ID|name>` are supported (up to 16 snapshots).
- `instar compare` supports LUKS-in-QCOW2 decryption
  (crypt_method=2) via `--luks-passphrase`, matching the
  convert operation. This allows comparing encrypted QCOW2
  images directly against their decrypted equivalents.
- Extended L2 images with subclusters are fully supported
  for both input and output. The 16-byte L2 entry bitmap is
  parsed to determine per-subcluster state: Normal subclusters
  read host data, Zero subclusters are zeroed, and Unallocated
  subclusters preserve backing data or read as zeros if no
  backing image is present. QCOW2 output with `--extended-l2`
  writes 16-byte L2 entries with `incompatible_features` bit 4
  set. Written data clusters are marked fully allocated
  (alloc_bits=0xFFFFFFFF). Works with both uncompressed and
  compressed output.

---

## Resize Format Support

The `instar resize` operation mutates an existing image's virtual
size in place. Per-format support:

| Format | Grow | Shrink | Preallocation modes for grow | qemu-img parity |
|--------|------|--------|------------------------------|------------------|
| raw    | Yes  | Yes    | off, falloc, full            | Byte-equivalent across qemu-img 6.0.0–10.2.0 |
| qcow2  | Yes  | Yes (`--shrink`) | off, falloc, full (metadata planner gap) | Byte-equivalent (modulo `KNOWN_RESIZE_DIVERGENCES`) |
| vmdk (monolithicSparse) | Yes | reject | off | **instar-only** — qemu rejects |
| vmdk (other subformats) | reject | reject | n/a | n/a |
| vpc (VHD dynamic) | Yes | reject | off | **instar-only** — qemu rejects |
| vpc (VHD fixed)   | Yes | reject | off | **instar-only** — qemu rejects |
| vhdx (dynamic)    | Yes | reject | off | **instar-only** — qemu rejects |

For the formats marked **instar-only**, `qemu-img resize` rejects
on every shipped version (`6.0.0` through `10.2.0`) with `Image
format driver does not support resize`. Coverage for those formats
is via the internal consistency suite
(`tests/test_resize.py:TestResizeConsistency`) rather than a
cross-tool diff. See [docs/resize.md](/components/instar/resize/) and the
"resize subcommand quirks" section of [docs/quirks.md](/components/instar/quirks/).

For sparse-format grow with `--preallocation=falloc|full`, instar
preallocates only the newly-appended file region, not the entire
data region of the new virtual size. Documented in
[docs/quirks.md](/components/instar/quirks/) as a deliberate divergence from
qemu-img.

---

## Rebase Format Support

The `instar rebase` operation changes the backing-file
pointer recorded in an overlay (and, in safe mode, copies
divergent clusters from the old chain). Per-format support:

| Format | Unsafe (`-u`) | Safe (default) | qemu-img parity |
|--------|---------------|----------------|------------------|
| qcow2 v2 / v3 | Yes | Yes | Byte-equivalent across qemu-img 6.0.0–10.2.0 (modulo `KNOWN_REBASE_DIVERGENCES`) |
| vmdk monolithicSparse | Yes | Reject (planner gap) | **instar-only**: `qemu-img rebase` rejects vmdk on every shipped version |
| Other formats | Reject | Reject | n/a (both refuse; exception: qemu-img rebases QED — instar refuses by policy, see the qemu-img parity axis Note 7) |

For qcow2, the post-rebase `qemu-img info --output=json`
matches `qemu-img rebase` byte-for-byte across every
shipped version. Coverage is via
`tests/test_rebase.py:TestRebaseBaselineMatrix` (cross-
version) and `TestRebaseRoundTrip` (live cross-tool diff).

For vmdk — which `qemu-img rebase` rejects entirely with
`Image format driver does not support rebase` — coverage is
via instar's smoke tests (`TestRebaseSuccessPaths`)
asserting the post-rebase descriptor records the new
`parentFileNameHint`. See [docs/rebase.md](/components/instar/rebase/) and
the "rebase subcommand quirks" section of
[docs/quirks.md](/components/instar/quirks/).

---

## Commit Format Support

The `instar commit` operation merges every allocated cluster
from an overlay into its backing image, then zeroes the
overlay's metadata. Per-format support:

| Format | Implicit `-b` | Explicit `-b` | qemu-img parity |
|--------|---------------|---------------|------------------|
| qcow2 v2 / v3 | Yes | Yes | Byte-equivalent across qemu-img 6.0.0–10.2.0 (modulo `KNOWN_COMMIT_DIVERGENCES`) |
| vmdk monolithicSparse | Reject (info-side gap) | Yes | Cross-version baselines recorded; implicit-`-b` blocked by info-vmdk-backing-file follow-up |
| Other formats | Reject | Reject | n/a (both refuse; exception: qemu-img commits QED — instar refuses by policy, see the qemu-img parity axis Note 7) |

For qcow2, post-commit `qemu-img info --output=json` for
both the overlay and the backing matches `qemu-img commit`
byte-for-byte across every shipped version. Coverage is via
`tests/test_commit.py:TestCommitBaselineMatrix` (cross-
version, both buckets) and `TestCommitRoundTrip` (live
cross-tool diff).

For vmdk monolithicSparse, the implicit-`-b` resolution
path is blocked because the host info operation doesn't
currently expose vmdk monolithicSparse's
`parentFileNameHint` via the `backing_file` field. The matrix and round-trip tests use explicit `-b base.vmdk` to
sidestep this; once the info-side gap lifts (tracked under
PLAN-info's vmdk follow-ups), the implicit form will work
too. See [docs/commit.md](/components/instar/commit/) and the "commit
subcommand quirks" section of
[docs/quirks.md](/components/instar/quirks/).

---

## DD Format Support

The `instar dd` operation performs a windowed block copy, writing
dense output. Per-format output support:

| Output Format | Status | Size rounding | qemu-img parity |
|---------------|--------|---------------|-----------------|
| raw           | Supported | `round_up(window, 512)` | Byte- and size-identical |
| qcow2         | Supported | `round_up(window, 512)` | Byte- and size-identical |
| vmdk          | Supported | `round_up(window, 512)` | Byte- and size-identical |
| vpc (VHD)     | Supported | CHS geometry rounding (may be larger; e.g. 3000-byte window ⇒ 34816) | Byte- and size-identical |
| vhdx          | Supported | `round_up(window, 512)` | Data/virtual-size identical; block-size metadata differs (instar 32 MiB, qemu 8 MiB for small images) |

All input formats supported by `instar convert` are also accepted
as `dd` input. `-O` defaults to **raw** (not the input format).
Window semantics: `bs` (default 512), `count` (clamps down;
`count=0` ⇒ empty), `skip` (subtracts from front; skip-past-EOF
⇒ empty, exit 0). Output is always dense.

Known divergences from `qemu-img dd`: vhdx default block size
(data and virtual size still match); `count=0 -O vmdk` (qemu-img
itself exits 1); `count=0 -O vhdx` (instar's empty vhdx is
rejected by `qemu-img info`). See [docs/dd.md](/components/instar/dd/) for the
full reference.

---

## Safety Check Comparison

### QCOW2 Safety Checks

| Check | Description | oslo.utils | instar | Test Images |
|-------|-------------|------------|-------|-------------|
| backing_file | Detects external backing file reference | Rejects | Reports (FLAG_HAS_BACKING_FILE) | qcow2-overlay-chain, sf-vda, qcow2-backing-* |
| data_file | Detects external data file feature | Rejects | Reports (FLAG_HAS_EXTERNAL_DATA) | qcow2-external-data-file |
| unknown_features | Unknown incompatible feature bits | Rejects | Rejects in check/compare/convert; info reports | qcow2-unknown-features |
| dirty | Image not cleanly closed | N/A | Reports (FLAG_DIRTY) | qcow2-dirty |
| corrupt | Image marked corrupt | N/A | Reports (FLAG_CORRUPT) | qcow2-corrupt |
| encrypted | Encryption enabled | N/A | Reports (FLAG_ENCRYPTED), decrypts with passphrase | qcow2-luks, qcow2-encrypted-aes |

#### QCOW2 Incompatible Feature Bits

| Bit | Name | oslo.utils | instar |
|-----|------|------------|-------|
| 0 | Dirty bit | N/A | QCOW2_INCOMPAT_DIRTY |
| 1 | Corrupt bit | N/A | QCOW2_INCOMPAT_CORRUPT |
| 2 | External data file | Rejects | Supported (data file path in JSON, chain read) |
| 3 | Compression type | N/A | QCOW2_INCOMPAT_COMPRESSION |
| 4 | Extended L2 | N/A | QCOW2_INCOMPAT_EXTENDED_L2 |
| 5+ | Unknown | Rejects | Rejected by check/compare/convert |

### VMDK Safety Checks

| Check | Description | oslo.utils | instar | Test Images |
|-------|-------------|------------|-------|-------------|
| descriptor path traversal | Extent paths with `/` | Rejects | Detects multi-extent (FLAG_NOT_SUPPORTED) | vmdk-path-traversal |
| descriptor missing extents | No extent declarations | Rejects | Validated via GD/GT walk | vmdk-no-extents |
| header/footer consistency | Signature mismatch | Rejects | Footer magic validated (streamOptimized) | vmdk-streamoptimized |
| createType validation | Unsupported types | Partial | Reports createType | vmdk-streamoptimized |
| grain directory bounds | GD offset within file | N/A | Validated | plaso-vmdk, vmdk-multi-partition |
| grain table bounds | GT offsets within file | N/A | Validated per GD entry | plaso-vmdk, vmdk-multi-partition |
| grain data bounds | Grain offsets within file | N/A | Validated per GTE | plaso-vmdk, vmdk-multi-partition |
| grain overlap | Two grains at same offset | N/A | 1-bit-per-grain bitmap | plaso-vmdk, vmdk-multi-partition |
| compressed grain markers | Validate LBA, size, bounds | N/A | Marker structure validated per compressed GTE | vmdk-streamoptimized |
| redundant GD (RGD) | Cross-check against primary GD | N/A | Entry-by-entry comparison when FLAG_USE_RGD set | qemu-img-created VMDKs |
| multi-extent detection | Multiple extents in descriptor | N/A | Supported for twoGbMaxExtentFlat; sparse multi-extent reports FLAG_NOT_SUPPORTED | vmdk-multi-extent, osboxes-vmdk-split-sparse |
| fragmentation | Non-sequential grain layout | N/A | Reports fragmentation count | plaso-vmdk, vmdk-multi-partition |

### RAW/Partition Table Safety Checks

| Check | Description | oslo.utils | instar | Test Images |
|-------|-------------|------------|-------|-------------|
| MBR signature | 0xAA55 at offset 510 | Yes | Yes | raw-mbr-partitioned |
| MBR boot flag validity | Must be 0x00 or 0x80 | Rejects | Yes | raw-mbr-partitioned |
| GPT protective MBR | Partition type 0xEE detection | Yes | Yes | raw-gpt-partitioned |
| Partition table required | Reject files without valid table | N/A | Yes (default) | multiple raw-* images |

### Other Format Safety Checks

| Format | Check | oslo.utils | instar |
|--------|-------|------------|-------|
| QED | Banned entirely | Rejects | Detects format; `info` reads correctly (byte-parity with qemu-img) but every other op is **refused by policy**, not just detected — a deliberate decision, not a parity gap: qemu-img converts/checks/maps/measures/benches/resizes/rebases/commits QED normally (all rc 0) but instar refuses all of those; only amend/snapshot/bitmap match qemu's own refusals. See [quirks.md](/components/instar/quirks/#qed-read-refusal-as-policy) |
| LUKS | Version check (only v1) | Rejects v2+ | Detects format, version, cipher, hash, UUID, payload offset, key slots, inner format (with passphrase); convert decrypts v1/v2 containers |
| VDI | None | Pass-through | Detects format, UUID; convert/compare/dd read via a full reader — header validated against qemu's 12 open-time rules, block-map entries bounds-checked, past-EOF block reads zero-filled (`check` still refuses, exit 63) |
| Parallels | None | Pass-through | Detects format, magic, version; convert/compare/dd/bench read via a full reader — open validated per qemu's RO rules, BAT decoded per-magic (sector-valued v1, cluster-valued v2/ext), past-EOF and out-of-BAT reads zero-filled, `ext_off != 0` refused (`check` still refuses, exit 63) |
| QCOW1 ("qcow") | None | Pass-through | Detects format (version-aware split from QCOW2, since fixed), cluster/L2 bits, backing file, encryption; convert/compare/dd/bench read via a full reader — per-cluster walk, backing-chain fall-through, raw-DEFLATE compressed clusters, past-EOF zero-fill (`check` still refuses, exit 63; `map`/`measure` still refuse, a recorded divergence since qemu supports both) |
| DMG (UDIF) | None | Pass-through | Detects format via koly-trailer content scan; convert/compare/dd/bench read via a full reader — koly + mish/BLKX chunk table (XML-plist and resource-fork paths), zero/raw/ignore/zlib codecs, typed refusals for unsupported codecs and over-cap chunks, gaps and truncation are read ERRORS (never zero-fill) (`check` still refuses, exit 63, naming the format "raw" since check never runs the koly probe; `map`/`measure`/`resize` still pass DMG through as raw, an unchanged divergence since qemu supports all three) |
| ISO | None | Pass-through | Detects format* |
| VHD | None | Pass-through | Detects creator app; full check validation (footer/header checksums, version/feature validation, BAT bounds, overlap detection, fragmentation, fixed VHD size check, footer copy consistency) |
| VHDX | None | Pass-through | Detects block size; full check validation (file identifier, dual header CRC-32C, region table 1+2 cross-check, metadata, BAT bounds/alignment/overlap, fragmentation) |

---

## Test Image Coverage

### Current Test Images by Format

#### QCOW2 Images (25+)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| cirros-qcow2 | CirrOS minimal cloud image | safe | Production-like |
| qcow2-v2 | QCOW2 version 2 (compat=0.10) | safe | Version 2 format |
| qcow2-extended-l2 | Extended L2 entries | safe | Subcluster allocation |
| qcow2-zstd | ZSTD compression | safe | Compression type |
| qcow2-lazy-refcounts | Lazy refcounts enabled | safe | Crash-consistent mode |
| qcow2-min-cluster | 512-byte cluster size | safe | Parser stress test |
| qcow2-max-cluster | 2MB cluster size | safe | Parser stress test |
| qcow2-refcount-bits-64 | 64-bit refcount width | safe | Refcount edge case |
| qcow2-refcount-bits-1 | 1-bit refcount width | safe | Refcount edge case |
| qcow2-overlay-chain | Overlay with backing file | safe | Backing chain |
| qcow2-base-for-chain | Base image (no backing) | safe | Backing chain base |
| sf-vda | Shaken Fist production overlay | safe | Large cluster, 30GB virtual |
| sf-vda-backing | Shaken Fist production base | safe | Large cluster |
| debian-12-sfagent | Debian 12 production image | safe | Cloud image |
| aurel32-* | Historic Debian images (4) | safe | Various architectures |
| chain-top-qcow2 | Three-layer backing chain | safe | Cross-format chain |
| chain-middle-qcow2 | QCOW2 with VMDK backing | safe | Cross-format chain |
| qcow2-dirty | Dirty bit set | safe | Unclean shutdown |
| qcow2-corrupt | Corrupt bit set | safe | Corrupt flag |
| qcow2-backing-textfile | Backing file to text file | malicious | CVE-2015-5163 |
| qcow2-backing-etc-passwd | Backing file to /etc/passwd | malicious | CVE-2015-5163 |
| qcow2-backing-garbage | Backing file to garbage | malicious | CVE-2015-5163 |
| qcow2-external-data-file | External data file feature | malicious | CVE-2024-32498 |
| qcow2-unknown-features | Unknown feature bit set | malicious | Unknown features |

#### VMDK Images (8)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| plaso-vmdk | MonolithicSparse VMDK | safe | Basic VMDK |
| vmdk-multi-partition | Multi-partition VMDK | safe | Multiple partitions |
| vmdk-streamoptimized | streamOptimized VMDK | safe | OVA/OVF format |
| vmdk-v3 | VMDK version 3 | safe | Native version 3 |
| vmdk-multi-extent | Binary VMDK4 with two extent lines | safe | Multi-extent detection |
| chain-base-vmdk | VMDK base for chain test | safe | Cross-format chain |
| vmdk-path-traversal | Path traversal in extent | malicious | /etc/passwd reference |
| vmdk-no-extents | Missing extent declarations | malformed | Invalid descriptor |

#### VHD/VPC Images (6)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| hyperv-dynamic-vhd | Hyper-V 2012 R2 VHD | safe | Dynamic allocation |
| virtualpc-vhd | Virtual PC VHD | safe | Different creator |
| vhd-d2v-zerofilled | Disk2VHD zerofilled VHD | safe | Zerofilled |
| vhd-fixed | Fixed VHD (disk_type=2) | safe | Fixed allocation |
| vhd-differencing | Differencing VHD (disk_type=4) | safe | Differencing type |
| afl-vhd-max-table-entries | AFL-discovered malformed | malformed | Error handling |

#### VHDX Images (2)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| qemu-vhdx | QEMU iotest VHDX | safe | Dynamic disk |
| vhdx-disk2vhd | Disk2VHD created VHDX | safe | Different creator |

#### VDI Images (10)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| vdi-simple | Basic VirtualBox VDI, 10 MiB dynamic, empty | safe | Format detection + convert-from baseline |
| vdi-data-dynamic | 8 MiB dynamic VDI, data at 2 MiB and 5 MiB | safe | Allocation-order block map; one entry patched to discarded (0xfffffffe) |
| vdi-static-data | 3 MiB static (pre-allocated) VDI | safe | Identity block map, data pattern in the middle block |
| vdi-odd-size | 2 MiB dynamic VDI, disk_size patched to 1048577 | safe | Pins the round-up-to-512 rule (qemu reports 1049088); oslo divergence |
| vdi-bmap-past-eof | 8 MiB dynamic VDI, one entry ~256 MiB past EOF | safe | Pins the past-EOF zero-fill rule |
| vdi-bad-version | Version patched to 2.0 | malformed | Refused at open (unsupported version) |
| vdi-unaligned-bmap | offset_bmap patched to 0x201 | malformed | Refused (block-map offset not 512-aligned) |
| vdi-wrong-blocksize | block_size patched to 512 | malformed | Refused (block size must be the hard-fixed 1 MiB) |
| vdi-nonnull-parent | Nonzero byte in uuid_parent | malformed | Refused (VDI has no backing-file support) |
| vdi-too-many-blocks | blocks_in_image patched to 0xffffffff | malformed | Refused (exceeds qemu's max of 536870784) |

#### QED Images (1)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| qed-simple | QED format image | safe | `info` byte-parity with qemu-img; every other op refused by policy — not because qemu deprecates QED (it doesn't) |

#### QCOW1 Images (12)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| qcow1-data | 2 MiB QCOW1, default 4 KiB clusters, data in scattered guest clusters 0/5/17/100/300/511 | safe | Two-level uncompressed L1/L2 lookup baseline |
| qcow1-compressed | `convert -c` twin of qcow1-data, same content via raw-DEFLATE clusters | safe | Compare-identical to qcow1-data; pins the `qemu-img convert -c -O qcow` exit-1-despite-valid-output quirk (validated by roundtrip, not rc) |
| qcow1-backing-base | 1 MiB QCOW1 backing base, no backing file, guest clusters 0..4 filled | safe | Base of the qcow1-backing overlay pair |
| qcow1-backing | Overlay created with `-b qcow1-backing-base.qcow -F qcow` (relative name); create-with-backing uses 512-byte clusters | safe | Backing-chain fall-through and small-cluster (512 B) walk coverage; two overlay clusters mask the base, the rest read through |
| qcow1-encrypted | qcow1-data byte-patched to crypt_method=1 (AES-128-CBC) | safe | First baseline carrying the `encrypted: yes` info line; data ops refuse cleanly |
| qcow1-past-eof | qcow1-data with one data-cluster's L2 entry redirected ~4 GiB past EOF | safe | Pins past-EOF zero-fill (no 8.1.x window, version-stable) |
| qcow1-odd-size | 1 MiB QCOW1, header size u64 byte-patched to the odd value 1048577 | safe | Pins truncate-down to `total_sectors*512` = 1048576 (opposite of VDI's round-up); oslo.utils reads the field verbatim, a real vsize divergence |
| qcow1-bad-cluster-bits | cluster_bits (offset 32) patched to 17 (outside [9,16]) | malformed | Refused at open ("Cluster size must be between 512 and 64k") |
| qcow1-bad-l2-bits | l2_bits (offset 33) patched to 14 (outside [6,13]) | malformed | Refused at open ("L2 table size must be between 512 and 64k") |
| qcow1-huge-size | size (offset 24) patched to 562949951324161, the empirically-pinned smallest refused "Image too large" boundary | malformed | Refused at open ("Image too large") |
| qcow1-crypt-invalid | crypt_method (offset 36) patched to 2 (>= 2 unsupported) | malformed | Refused at open ("invalid encryption method in qcow header") |
| qcow1-backing-name-too-long | backing_file_offset nonzero, backing_file_size (offset 16) patched to 1024 (> 1023) | malformed | Refused at open ("Backing file name too long") |

#### Parallels Images (11)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| parallels-v1 | QEMU iotests image, old "WithoutFreeSpace" magic | safe | nb_sectors masked to low 32 bits |
| parallels-v2 | QEMU iotests image, new "WithouFreSpacExt" magic | safe | Full-width nb_sectors |
| parallels-data-v2 | 2 MiB v2 image, 64 KiB clusters, data in guest clusters 1/3/5/7 | safe | Two BAT entries and their data clusters swapped; pins non-contiguous/non-monotonic BAT decode |
| parallels-data-v1 | parallels-data-v2 rewritten to the v1 magic, every nonzero BAT entry multiplied by tracks | safe | Pins off_multiplier==1 and the v1 32-bit nb_sectors mask; reads identically to the v2 twin |
| parallels-inuse | parallels-data-v2 with `inuse` (offset 44) set to 0x746f6e59 | safe | Opened-dirty header; pins never-refuse-on-dirty (RO opens succeed) |
| parallels-bat-past-eof | 2 MiB v2 image, one guest cluster's BAT entry ~64 GiB past EOF | safe | Pins past-EOF zero-fill; also the fixture behind the qemu 8.1.0-8.1.5 open-refusal window (see quirks.md) |
| parallels-cluster-4k | 256 KiB v2 image, `-o cluster_size=4096` (tracks=8), scattered data clusters | safe | Pins small-cluster chunk-boundary handling |
| parallels-zero-tracks | tracks (offset 28) patched to 0 | malformed | Refused at open ("Zero sectors per track") |
| parallels-huge-tracks | tracks patched to 4186128 (> INT32_MAX/513) | malformed | Refused at open ("Too big cluster") |
| parallels-huge-catalog | bat_entries (offset 32) patched to 0x40000000 (> INT_MAX/4) | malformed | Refused at open ("Catalog too large") |
| parallels-ext-bad-magic | ext_off (offset 56) points at a zeroed in-file sector | malformed | Refused at open (bad format-extension magic) |

#### Bochs Images (1)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| bochs-growing | QEMU iotests `empty.bochs`, growing-mode image | safe | Detect + info test |

#### cloop Images (1)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| cloop-simple | QEMU iotests `simple-pattern.cloop`, V2.0 magic | safe | Detect + info test |

#### DMG Images (16)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| dmg-simple | Minimal valid 4 MiB UDIF, UDZO/zlib chunks + koly trailer | safe | Content-based trailer detection; convert/compare/dd/bench read baseline |
| dmg-mixed | ~2 MiB UDIF, one mish mixing zero + raw + zlib + ignore chunks plus a comment and terminator entry | safe | Full chunk-type mix in one table; byte-parity convert |
| dmg-multipart | 1 MiB UDIF composed of two mish blocks at absolute sectors (`out_offset`) | safe | Convert equals the concatenation of the two zlib parts |
| dmg-rsrc-fork | 512 KiB UDIF using the OLD resource-fork chunk-table path (`RsrcForkLength != 0`, `XMLLength = 0`) | safe | Pins the non-XML chunk-table source; byte-parity convert |
| dmg-gap | UDIF whose koly `SectorCount` (16) exceeds mish coverage (8 sectors) | safe (error-parity fixture) | `info` succeeds both sides (vsize 8192); `convert`/`dd` FAIL both sides (qemu EIO on the uncovered tail, instar a clean gap refusal) — never byte-parity |
| dmg-truncated-koly | koly magic present but trailer cut short | malformed | No valid 512-byte trailer at any candidate offset |
| dmg-sectorcount-negative | Valid koly trailer, SectorCount top bit set | malformed | Collapses koly *detection* to `unknown`; raw pass-through on the small container, exempted from the #444 gate |
| dmg-sectorcount-huge | Valid koly trailer, absurd-but-positive SectorCount | malformed | 128 PiB reported vsize, matches qemu |
| dmg-no-chunk-table | Valid koly trailer, RsrcForkLength and XMLLength both zero | malformed | `skip_qemu_img`: qemu's clean EINVAL (no chunk source at all, never reaches table-build) — distinct from dmg-empty-table's segfault shape |
| dmg-chunk-len-over | One zlib chunk with `comp_len` = 64 MiB + 1 | malformed | qemu refuses at open ("larger than max (67108864)"); instar refuses typed at reader init |
| dmg-sc-over | One raw chunk with `sector_count` = 131073 (raw is not cap-exempt) | malformed | qemu refuses at open ("larger than max (131072)"); instar refuses typed at reader init |
| dmg-codec-bzip2 | One real bzip2 (UDBZ, `0x80000006`) chunk | malformed | `skip_qemu_img`: qemu behaviour is build-dependent (decodes on static 6.0.0 and host 10.0.11; EIO elsewhere) — instar issues a typed UDBZ refusal |
| dmg-codec-lzfse | One lzfse (ULFO, `0x80000007`) chunk | malformed | No qemu build in the matrix ships lzfse (dropped at open, EIO on read); instar issues a typed ULFO refusal |
| dmg-codec-adc | One ADC (UDCO, `0x80000004`) chunk | malformed | qemu enum-names ADC but never implements it (dropped, EIO); instar issues a typed UDCO refusal |
| dmg-overcap-chunk | One qemu-legal zlib chunk, `sector_count` 8192 (4 MiB uncompressed) — under qemu's 131072-sector cap but over instar's 4096-sector (2 MiB) staging cap | malformed (capacity-divergence fixture) | qemu CONVERTS it fine on every version (md5 `dd8d16c0...`); instar refuses typed |
| dmg-empty-table | Valid koly + well-formed XML plist whose single `<data>` block decodes to a mish with a corrupted magic, so qemu parses ZERO chunks | malformed | The true qemu zero-chunk-table NULL-deref: `info` succeeds (rc 0), but convert/read SIGSEGVs (rc 139) on every qemu build tested; instar refuses cleanly at init. Shipped upstream-report reproducer |

#### LUKS Images (9)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| luks-v1 | LUKS v1 header (synthetic) | safe | Header parsing test |
| luks-v2 | LUKS v2 header with JSON metadata (synthetic) | safe | JSON metadata parsing |
| luks-v1-raw-gpt | LUKS v1 wrapping GPT raw image | safe | Inner format detection (raw) |
| luks-v1-qcow2 | LUKS v1 wrapping QCOW2 image | safe | Inner format detection (qcow2) |
| luks-v1-aes-xts | LUKS v1 with known encrypted content | safe | Native LUKS v1 conversion test |
| luks-v2-aes-xts | LUKS v2 with low-memory Argon2id | safe | Native LUKS v2 conversion test |
| luks-v2-raw-gpt | LUKS v2 wrapping GPT raw image | safe | Argon2id decryption test |
| luks-v1-qcow2-inner | LUKS v1 wrapping QCOW2 inner image | safe | LUKS-wrapping-QCOW2 conversion |
| qcow2-luks | QCOW2 v3 with LUKS encryption (crypt_method=2) | safe | LUKS-in-QCOW2 conversion test |

#### ISO Images (1)

| Image ID | Description | Safety | Key Features |
|----------|-------------|--------|--------------|
| iso-simple | Basic ISO 9660 image | safe | Format detection test |

#### RAW Images (12)

| Image ID | Description | Safety | Partition Table |
|----------|-------------|--------|-----------------|
| raw-mbr-partitioned | MBR partition table | safe | MBR |
| raw-gpt-partitioned | GPT partition table | safe | GPT |
| raw-fat-no-partition | FAT16 without partition table | safe | None (requires --unsafe-quirks) |
| raw-sparse-empty | Sparse 100MB file | safe | None (requires --unsafe-quirks) |
| raw-zeros-1mb | 1MB zeros | safe | None (requires --unsafe-quirks) |
| raw-mbr-truncated | Truncated MBR | malformed | Invalid |
| raw-gpt-truncated | Truncated GPT | malformed | Invalid |
| raw-mbr-corrupted | Valid signature, garbage entries | malformed | Invalid |
| raw-random-garbage | Random bytes | malformed | None |
| raw-misleading-header | QCOW2 magic but invalid | malformed | None |
| raw-minimal-1byte | 1-byte file | malformed | None |
| raw-qcow2-magic-wrong-offset | QCOW2 magic at offset 512 | malformed | None |

### Remaining Test Images to Create

#### High Priority - Security Relevant

All high-priority test images have been created (qcow2-encrypted-aes,
qcow2-luks).

---

## Implementation Status

### Completed

1. **MBR/GPT Partition Table Detection** - Implemented as part of `--unsafe-quirks`
   feature. By default, files without recognized format headers must have a valid
   partition table (MBR or GPT) to be accepted as RAW disk images.

   - MBR: Valid 0x55AA signature at offset 510, plus valid boot indicators (0x00/0x80)
   - GPT: Protective MBR with partition type 0xEE

   See [quirks.md](/components/instar/quirks/#raw-as-fallback-format) for details.

2. **QCOW2 Backing File Detection** - Reports backing file path and format
   (from header extension). Tests include security-focused images that attempt
   path traversal attacks (CVE-2015-5163).

3. **QCOW2 Feature Bit Detection** - Reports dirty, corrupt, external data,
   compression type, and extended L2 feature bits.

4. **VMDK CreateType Detection** - Reports createType from descriptor for
   streamOptimized and other VMDK variants.

5. **Cross-Format Backing Chain Detection** - `--chain` flag discovers backing
   chains across format boundaries (e.g., QCOW2 -> VMDK).

6. **QCOW2 Incompatible Feature Bit Validation** - check, compare, and convert
   operations reject images with unsupported incompatible feature bits (per QCOW2
   spec). Supported bits: dirty (0), corrupt (1), external data file (2),
   compression type (3), extended L2 (4). Unsupported: unknown bits (5+).

7. **ZSTD Compressed Cluster Decompression** - QCOW2 v3 images with
   `compression_type=1` (ZSTD) are now supported in compare and convert
   operations using the ruzstd pure-Rust decoder.

8. **Extended L2 Entry Support** - QCOW2 v3 images with 128-bit L2 entries
   (32 subclusters) are now correctly parsed. The 16-byte entry stride is
   used for L2 table iteration and cluster lookup.

9. **Comprehensive Test Image Suite** - Test images now cover:
   - QCOW2 external data file (CVE-2024-32498)
   - QCOW2 unknown features
   - QCOW2 dirty/corrupt bits
   - VMDK path traversal
   - VMDK missing extents
   - QED and ISO format detection

10. **VMDK Input/Output Support** - Convert supports VMDK as both input and
    output format. Input: monolithicSparse (grain directory/table lookup) and
    streamOptimized (DEFLATE decompression). Output: monolithicSparse (default)
    and streamOptimized with `-c` flag (DEFLATE compressed).

11. **VMDK Structural Integrity Check** - Full GD/GT validation in check
    operation: grain directory bounds checking, grain table walk with offset
    validation, grain overlap detection (1-bit-per-grain bitmap in scratch
    memory), streamOptimized footer validation, multi-extent detection via
    descriptor parsing, fragmentation measurement.

12. **VHD Input/Output Support** - Convert supports VHD as both input and
    output format. Input: fixed VHD (raw sector reads) and dynamic VHD
    (BAT-based block lookup). Output: dynamic VHD with 2 MiB blocks,
    sector bitmaps, and skip-zeros support.

13. **VHD Structural Integrity Check** - Full BAT validation in check
    operation: footer cookie and checksum validation, dynamic header
    cookie and checksum validation, BAT offset and entry bounds checking,
    overlap detection (1-bit-per-block bitmap in scratch memory), footer
    copy consistency (start vs end of file).

14. **VHDX Input/Output Support** - Convert supports VHDX as both input
    and output format. Input: dynamic VHDX (64-bit BAT with interleaved
    sector bitmap entries, GUID-based metadata, CRC-32C header/region
    validation). Output: dynamic VHDX with 32 MiB blocks, 1MB-aligned
    structures, and skip-zeros support.

15. **VHDX Structural Integrity Check** - Full validation in check
    operation: dual header CRC-32C validation with active header
    selection by sequence number, dirty log detection, region table
    CRC-32C validation, GUID-based metadata parsing (all required items),
    BAT entry validation (offset bounds, 1MB alignment, overlap
    detection, state validation), differencing disk detection.

16. **LUKS Container Inspection and Conversion** - Full LUKS v1 and v2 header
    parsing with cipher, cipher mode, hash algorithm, UUID, payload offset,
    master key length, and active key slot reporting. LUKS v2 JSON metadata
    parsing extracts cipher/hash from the JSON area. With `--luks-passphrase`,
    LUKS v1 (PBKDF2) and v2 (Argon2id, requires `--max-guest-memory`) containers
    are decrypted using pure-Rust RustCrypto crates in the no_std guest. The
    info operation detects inner format; the convert operation decrypts native
    LUKS containers and LUKS-in-QCOW2 images (crypt_method=2) using AES-XTS-
    plain64. Dynamic guest memory allocation supports Argon2id's 1GB+ working
    memory requirement. Shared LUKS logic is extracted into `src/crates/luks/`
    with `decrypt` and `kdf-argon2` feature flags. Native LUKS containers
    wrapping QCOW2 images are transparently handled via CallTable function
    pointer wrapping (no qcow2 crate changes needed).

17. **QCOW2 External Data File Support** - Full read support for QCOW2 v3
    images with external data files (incompatible feature bit 2). The DATA
    header extension (type 0x44415441) is parsed to extract the data file
    path, which is reported in both human and JSON output. Chain discovery
    validates the data file path against the allowlist (CVE-2024-32498
    prevention) and opens it as a separate virtio-block device. Standard
    cluster reads dispatch to the data device; compressed clusters and
    metadata (L1/L2/refcounts) remain in the metadata device. The check
    operation skips bounds/overlap/refcount validation for data clusters
    when the external data bit is set.

18. **VDI Input Support** - Convert, compare, and dd support VDI
    (VirtualBox Disk Image) as read-only input, both dynamic and
    static images (`src/crates/vdi/`).
    The header is validated against qemu's 12 open-time rules; the
    block map is walked with an allocation-order lookup through the
    standard sector-cached pattern. qemu parity is exact: discarded
    (0xfffffffe) and unallocated (0xffffffff) entries read as zeros,
    `block_extra` never participates in offset math, any `image_type`
    is accepted (only type 2 is special, and needs no special-casing
    since its identity block map is just data), and reads at or past
    the device capacity — including straddling reads — zero-fill
    rather than error, because qemu never validates VDI file length.
    An odd `disk_size` is rounded up to 512 at open, matching qemu
    (`instar info` reports the rounded value). `check` still refuses
    VDI (exit 63); `map`, `measure`, and `resize` are unchanged
    refusals.

19. **Parallels Input Support** - Convert, compare, dd, and bench
    support Parallels as read-only input, both the legacy
    "WithoutFreeSpace" (v1) and "WithouFreSpacExt" (v2/ext) magics
    (`src/crates/parallels/`). The
    header is validated against qemu's RO open-time rules (tracks
    non-zero and under the empirically-corrected cap of 4186127,
    bat_entries under 0x3fffffff, a recognised magic, version 2,
    `ext_off == 0`); the BAT is decoded per-magic — sector-valued
    entries under v1 (`off_multiplier == 1`), cluster-valued entries
    under v2 (`off_multiplier == tracks`) — through the standard
    sector-cached read path. qemu parity is exact: BAT value 0 and
    guest offsets beyond BAT coverage read as zeros, reads at or past
    device capacity (including straddles) zero-fill rather than error,
    and `inuse`-dirty (opened-uncleanly) images are always readable
    since instar only ever opens read-only. `data_off` is parsed but
    never used in read math. `ext_off != 0` is refused at init — a
    deliberate divergence from qemu, which parses the format extension
    for dirty-bitmap metadata; no shipped or creatable fixture needs
    it today (see quirks.md for the rationale). Because qemu prints no
    cluster_size for parallels, `instar info` computes and stores it
    internally (`tracks << 9`) so the chain reader's chunking respects
    real cluster boundaries, but both emitters suppress the field for
    the "parallels" format string so `info` output stays byte-identical
    to qemu. `check` still refuses parallels (exit 63); `map`,
    `measure`, and `resize` are unchanged refusals.

20. **QCOW1 Input Support** - Convert, compare, dd, and bench
    support QCOW1 ("qcow", qemu's original copy-on-write format,
    superseded by qcow2 but not formally deprecated by qemu) as
    read-only input, including backing chains and compressed
    clusters (`src/crates/qcow1/`).
    This phase also **fixed a pre-existing detection defect**: real
    QCOW1 images were misdetected as QCOW2 because
    `detect_format_from_header` checked only the shared `QFI\xfb`
    magic and never consulted the version field, yielding garbage
    `info` output and a misleading convert error (see the footnote
    on the detection table above). Detection is now version-aware —
    `QFI\xfb` + version 1 routes to QCOW1, any other version keeps
    the QCOW2 route (a latent divergence from qemu is recorded:
    version 0 probes as raw under qemu but still routes to the QCOW2
    driver, and therefore refuses, under instar). QCOW1 is also the
    first non-QCOW2 backing format: unallocated clusters fall through
    to the next chain device exactly as the QCOW2 arm does, rather
    than zero-filling like the VDI/Parallels arms. Compressed
    clusters are raw DEFLATE (windowBits -12, no zlib wrapper) —
    NOT the QCOW2 zlib-first two-try helper. The reader walks
    per-cluster (clusters go down to 512 bytes). qemu parity is
    exact for the read path: past-EOF/truncated data clusters
    zero-fill on every qemu version (no 8.1.x-style window), and an
    odd header size truncates DOWN to `total_sectors*512` — the
    opposite of VDI's round-up. `instar info` gained a real QCOW1
    parser (previously qcow1 fell into the generic wildcard arm) and
    now also consumes the previously-dead `INFO_RESULT_FLAG_ENCRYPTED`
    flag in both emitters, printing `encrypted: yes` / `"encrypted":
    true` for AES (crypt_method=1) images — gated off for bare LUKS
    containers (whose goldens qemu prints no such line for) and
    otherwise a pre-existing gap fix with no baseline churn. instar
    emits the format string `"qcow"` (matching qemu-img/oslo), with
    `"qcow1"` still accepted as an input alias. Malformed QCOW1
    fixtures get a lenient-looking but distinct `info` posture from
    VDI/Parallels: the new parser validates cluster_bits/l2_bits/
    size/crypt/backing-name and falls back to an empty (virtual size
    0) default on any failure, rather than reporting best-effort
    nonzero fields. `check` still refuses QCOW1 (exit 63) — this
    happens to be genuine parity, since qemu's own qcow driver also
    refuses checks (instar's message includes "(qcow)", qemu's does
    not). `map` and `measure` stay refusals — a deliberate divergence
    since qemu supports both on qcow1 (master-plan future work).
    Encrypted QCOW1 is refused cleanly by data ops (parity with
    keyless qemu; AES decryption is future work). oslo.utils detects
    QCOW1 as `"qcow2"` (magic-only) with the correct virtual size and
    no dedicated inspector; recorded as a divergence.

21. **DMG Input Support** - Convert, compare, dd, and bench support
    DMG (Apple UDIF) as read-only input, via a new `src/crates/dmg/`
    crate wired into the qcow2 crate's chain reader
    (`src/crates/dmg/`). The reader parses the koly
    trailer (reusing the phase-1 shared trailer helpers), then the
    chunk table from EITHER the XML-plist path (a byte-for-byte port
    of glib's lenient string-scanning base64 decoder — invalid
    characters are skipped, never erroring) OR the old resource-fork
    path, and finally the mish/BLKX chunk entries into a sorted,
    verified lookup table. Codec scope is zero/raw/ignore/zlib
    (zlib-WRAPPED inflate — unlike QCOW1's raw-deflate); ADC, bzip2,
    lzfse, zstd, and any unknown chunk type get a typed refusal
    naming the code, rather than qemu's drop-then-EIO shape. The
    koly `SectorCount` always wins for virtual size, even when it
    exceeds mish coverage.
    **The read-error model inverts every prior phase's posture**: a
    sector covered by no chunk (a gap, a dropped/refused chunk, or
    the koly-vs-mish tail), a truncated raw span, or truncated
    compressed data is a read ERROR, matching qemu exactly — never
    zero-fill. Overlapping chunks are not an error (binary search
    deterministically picks one, matching qemu).
    A universal qemu crash was found and NOT mirrored: any DMG whose
    chunk table parses to zero entries (bad mish magic, broken
    base64, or no `<data>` blocks) SIGSEGVs every qemu-img version
    tested (6.0.0 through host 10.0.11) on read, while `info` is
    unaffected — instar refuses the empty table cleanly at reader
    init instead (shipped reproducer `dmg-empty-table`; a candidate
    upstream report, recorded as master-plan future work).
    instar enforces its own bounded-memory caps — distinct from
    qemu's own, larger, legal range — on the staged plist/rsrc
    region (1 MiB), the chunk table (32768 chunks), and per-chunk
    staging (4096 sectors / 2 MiB): a qemu-legal chunk beyond these
    caps gets a typed refusal, a documented capacity divergence
    pinned by `dmg-overcap-chunk` (qemu converts it; instar refuses).
    Detection remains the phase-1 content-based koly-trailer scan,
    strictly stronger than qemu-img's `.dmg`-extension-only probe —
    an extensionless valid DMG converts as its raw container bytes
    under qemu but as the real decoded disk under instar, a pinned
    divergence. DMG is supported at ANY backing-chain position,
    proven by a `qcow2 -F dmg` overlay-over-DMG chain converging
    byte-for-byte with qemu. `check` still refuses DMG (exit 63) but
    — because check never runs the koly probe — names the format
    "raw", not "dmg", unlike every other refused format in this
    table. `map`, `measure`, and `resize` are unaffected by this
    phase: they still pass DMG through as raw (the phase-1 divergence
    recorded above), since their probe paths never see the trailer;
    the master-plan future-work bullet stays open. The typed guest-
    side refusal strings (e.g. "dmg: unsupported chunk codec …") are
    debug output, not user-facing — the failure a caller actually
    sees is the generic "convert operation failed" wrapper, matching
    the VDI-precedent posture for adversarial pins.

22. **QED Read-Refusal as Policy** - This resolved the master
    plan's Open question 1 (read support vs. refusal for QED) by
    choosing refusal as deliberate policy rather than a sixth read
    path. A per-op audit found zero
    dangerous cases: `info` already reads QED correctly (byte-parity
    with qemu-img), and every other subcommand refuses cleanly with a
    typed message and no file modification. QED-named refusal pins
    now cover every op that previously lacked one — check (exit 63,
    "This image format (qed) does not support checks" — check's own
    probe sees QED's offset-0 magic, so unlike DMG it names the real
    format), map, measure, bench (refused via the issue-#444 chain
    gate, with no `"bench:"` message prefix, a deviation from
    convert/compare/dd), resize, rebase, commit, amend, snapshot, and
    bitmap. Two cosmetic inconsistencies are pinned as-is rather than
    normalised: `resize`/`rebase` render the Debug spelling `"Qed"`
    where other refusals say `"qed"`, and `check` exits 63 while every
    other refusal exits 1. The decision rests on nil real-world demand
    plus oslo.utils' own explicit ban (`SafetyCheckFailed: ... banned`
    from a real `QEDInspector`) — a stronger ecosystem statement than
    the "oslo simply has no inspector" case that justified reading
    VDI/Parallels/QCOW1/DMG. This phase also **corrected a stale
    documentation claim**: QED is NOT formally deprecated by qemu (no
    `deprecated.rst` entry, no runtime warning, `qemu-img create -f
    qed` still works on 10.2.0) — qemu-img reads, writes, checks,
    maps, measures, and benches QED normally on every version tested.
    The refusal is instar's own scope choice, not a response to qemu
    sunsetting the format. The `qed-simple` baselines in
    instar-testdata (predating its `skip_qemu_img: true` manifest
    flag) were reconciled: the check/compare baseline trees
    (permanently unconsumable under this policy) were retired and the
    generator's check/compare/measure/map whitelists lost `qed`, while
    the qemu-img-{human,json} trees were kept — they back `info`'s 14
    active `test_info_safe` scenarios and are the raw source of truth
    profiles regenerate from.
    Revisit criteria are recorded in the phase plan: a real user
    request to read QED, or QED images surfacing in a served workload.
    See [docs/quirks.md](/components/instar/quirks/#format-coverage-phase-6-qed-read-refusal-as-policy)
    for the full per-op divergence table and cosmetic-inconsistency
    record.

### Detections to Add

All oslo.utils formats are now detected. No remaining format detections needed.

### Safety Checks to Add

None currently outstanding. All VMDK safety checks are now implemented.

### Reporting Enhancements

1. **Security warnings** - Flag images with security-relevant features in output
2. **JSON output for chain** - Add `--output json` support for `--chain` flag

---

## oslo.utils Cross-Validation Testing

Automated tests in `tests/test_oslo_crossval.py` run both instar and
oslo.utils `format_inspector` against every test image and compare
results. Three test classes cover format detection, safety checks,
and virtual size.

### Running Locally

```bash
# With oslo.utils installed (included in tests/requirements.txt)
cd tests && ../.venv/bin/stestr run test_oslo_crossval

# Without oslo.utils — all tests skip gracefully
```

### Documented Divergences

The table below records instar-vs-oslo.utils divergences. For instar-vs-qemu-img
divergences per subcommand, see the [qemu-img parity axis](#qemu-img-parity-axis)
above.

| Area | Image(s) | instar | oslo.utils | Reason |
|------|----------|-------|-----------|--------|
| Format | raw-mbr-partitioned, raw-gpt-partitioned | raw | gpt | oslo GPTInspector detects partition tables; instar matches qemu-img |
| Format | vmdk-multi-partition | raw | gpt | File is raw with GPT despite .vmdk extension |
| Format | iso-simple | raw | iso | instar reports ISO as raw with --unsafe-quirks |
| Format | luks-v1, luks-v2 | luks | luks | Match (instar now reports LUKS format with full metadata) |
| Safety | QED images | pass | reject | oslo bans QED; instar uses KVM sandbox |
| Safety | LUKS v2 | pass | reject | oslo rejects LUKS v2+; instar detects both |
| Safety | qcow2-external-data-file | reports data-file | flags data_file | Match: both detect external data file path |
| Vsize | VPC/VHD images | - | - | CHS geometry rounding (up to 8 MB delta allowed) |
| Format | parallels-v1, parallels-v2 | parallels | raw | oslo has no Parallels inspector; falls back to RawFileInspector |
| Format | bochs-growing | bochs | raw | oslo has no Bochs inspector; falls back to RawFileInspector |
| Format | cloop-simple | cloop | raw | oslo has no cloop inspector; falls back to RawFileInspector |
| Format | dmg-simple | dmg | raw | oslo has no DMG inspector; falls back to RawFileInspector |
| Format | qcow1-data and other safe qcow1 fixtures | qcow | qcow2 | oslo has no qcow1 inspector; magic-only detection (`QFI\xfb`) routes qcow1 through the qcow2 inspector — virtual size agrees (the size u64 field sits at the same offset 24 in both formats) |
| Vsize | qcow1-odd-size | 1048576 | 1048577 | oslo reads the header size u64 field verbatim; qemu/instar truncate down to `total_sectors*512` (see the QCOW1 Images fixture table) |
| Format | dmg-mixed, dmg-multipart, dmg-rsrc-fork, dmg-gap | dmg | raw | Same rule as dmg-simple: oslo has no DMG inspector and falls back to RawFileInspector on all four new phase-5 safe fixtures (vsize also diverges the same way as dmg-simple's, per `KNOWN_VSIZE_DIVERGENCES` in `test_oslo_crossval.py` — recorded, not runtime-asserted, since the format divergence skips the vsize test first) |

### CI Integration

The `oslo-crossval-master` job in `.github/workflows/functional-tests.yml`
installs oslo.utils from git master (over the PyPI release) and runs only
the crossval tests. It has `continue-on-error: true` so upstream changes
are surfaced as warnings rather than blocking PRs.

---

## References

- [oslo.utils format_inspector.py](https://github.com/openstack/oslo.utils/blob/master/oslo_utils/imageutils/format_inspector.py)
- [Glance format inspector module](https://docs.openstack.org/glance/latest/_modules/glance/common/format_inspector.html)
- [format-detection-safety.md](/components/instar/format-detection-safety/) - Why instar's detection-only approach is secure
- [security.md](/components/instar/security/) - CVE analysis and threat model
- [testing.md](/components/instar/testing/) - Test framework documentation
- [quirks.md](/components/instar/quirks/) - Safe vs unsafe quirks classification

---

*Document updated: 2026-07-20 (qemu-img parity axis added)*
