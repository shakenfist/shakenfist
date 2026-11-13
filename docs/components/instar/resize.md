# `instar resize` — change a disk image's virtual size

`instar resize` changes the virtual size of an existing disk image
in place. It is the safe, sandboxed equivalent of `qemu-img resize`,
with byte-equivalent post-resize `qemu-img info` output for raw and
qcow2 across every qemu-img version 6.0.0 through 10.2.0, plus
instar-only resize support for vmdk, vpc (VHD), and vhdx, which
`qemu-img resize` rejects.

Raw resize is host-only (`open(O_RDWR) + ftruncate` plus optional
preallocation post-pass). Every other format runs the `resize.bin`
guest in the KVM sandbox, which reads the existing header, plans the
metadata mutation, and applies the patches via virtio-block.

## Synopsis

```
instar resize [OPTIONS] FILENAME [+-]SIZE[bkKMGTPE]
```

Common options:

```
  -f, --format <FMT>           raw | qcow2 | vmdk | vpc | vhdx
                               Default: auto-detect from the existing
                               file's magic bytes
      --shrink                 Allow the new size to be smaller than
                               the current size (qcow2 + raw only)
      --preallocation <MODE>   off | metadata | falloc | full
                               Default: off
  -q, --quiet                  Suppress "Image resized." on success
      --output <FORMAT>        human (default) | json
```

The full flag surface is reported by `instar resize --help`.

## End-spec grammar

The SIZE positional accepts three forms, matching `qemu-img resize`:

| Form     | Effect                                                  |
|----------|---------------------------------------------------------|
| `64M`    | Absolute: set virtual size to 64 MiB.                   |
| `+1G`    | Additive: `current_virtual_size + 1 GiB`.               |
| `-512M`  | Subtractive: `current_virtual_size - 512 MiB`.          |

Suffix multipliers: `B`/`b`=1, `k`/`K`=1024, `M`=1024², `G`=1024³,
`T`=1024⁴, `P`=1024⁵, `E`=1024⁶. An absent suffix is bytes, and so is
`b` — qemu-img's `b` is one byte, not qemu-io's 512-byte block, so
`4096b` is 4096 bytes and shrinks a 1 MiB image.

A decimal fraction may precede any suffix that scales: `+1.5M`,
`20.5M` and `.5G` are all accepted, and so is `1.M`. A fraction with
no suffix, or with `B`/`b`, is refused, because a fraction of a byte
is not a size. A fraction that is not a whole number of bytes rounds
to the nearest byte, halves upwards.

The resolved size must be a whole sector for every format but raw --
512 bytes for qcow2, vmdk and vhd, and a VHDX's own logical sector
size, because each records its size in sectors:

```
$ instar resize disk.qcow2 33554433
Error: "resize: the new size must be a multiple of 512"
```

Nothing is written, and `qemu-img resize` refuses the same request on
a qcow2 ("The new size must be a multiple of 512"). A raw image takes
any byte count, as it does under qemu-img: it has no metadata to keep
aligned, so `instar resize -f raw disk.raw 33554433` leaves a file of
exactly 33554433 bytes.

`create` is deliberately more forgiving than `resize` here: `instar
create -f vhdx x 33554433` succeeds, because qemu's `vhdx_co_create_opts`
rounds a requested size up to a sector and instar matches it, while
`instar resize -f vhdx x 33554433` fails. The asymmetry is the safer
pair of the two defaults -- a create has no data to lose, so rounding
is a courtesy, whereas rounding a resize down would silently discard
what lies past the rounded size and rounding it up would hand back a
disk bigger than the one asked for. See the VHDX and VHD create-rounding
entries in [quirks.md](/components/instar/quirks/) for the rounding `create` applies.

A subtractive end-spec implies shrink, but `--shrink` must still be
passed explicitly — it documents the user's intent to discard data
beyond the new EOF.

## Target formats

| Target | Grow | Shrink         | Preallocation modes              | Notes                              |
|--------|------|----------------|----------------------------------|------------------------------------|
| raw    | ✓    | ✓              | off, falloc, full                | host-only (truncate + post-pass)   |
| qcow2  | ✓    | ✓ (`--shrink`) | off, falloc, full (metadata gap) | L1 + refcount-table grow           |
| vmdk   | ✓    | reject         | off, falloc, full                | monolithicSparse only              |
| vpc    | ✓    | reject         | off, falloc, full                | dynamic + fixed grow               |
| vhdx   | ✓    | reject         | off, falloc, full                | dynamic only; two-header dance     |

For raw and qcow2, the post-resize `qemu-img info --output=json`
matches `qemu-img resize` byte-for-byte across every shipped
qemu-img version (verified by the cross-version baseline matrix in
`instar-testdata/expected-outputs/resize-info-json/` and exercised
by `tests/test_resize.py:TestResizeBaselineMatrix`). For vmdk / vpc
/ vhdx — formats `qemu-img resize` does not support — the
post-resize state is verified by `tests/test_resize.py:TestResize
Consistency` (instar resize → instar info → instar check).

**A qcow2 shrink keeps the size it was given.** The header records
the requested size, not a cluster-aligned version of it, as qemu does:
shrinking a 10 MiB image to 5 MiB + 4096 reports a virtual size of
5246976, which is not a multiple of the 64 KiB cluster. Only whole
clusters are discarded, so the cluster holding the new last byte stays
allocated. A size inside the first cluster is a size like any other --
`--shrink` to 4096 on a 64 KiB-cluster image gives a 4096-byte disk --
and only a size of zero is refused.

The kept cluster's tail is left as it was. Shrinking past the middle of
a cluster and then growing back exposes the bytes that were above the
new size again, rather than zeros, because the shrink discards whole
clusters and writes nothing into the one it keeps. qemu-img does the
same, measured on 10.0.13: a 10 MiB qcow2 holding `0xa5` across 6 MiB,
shrunk to 5246976 and grown back to 10 MiB, reads `0xa5` for all 61440
bytes from 5246976 to the end of that cluster under both tools. A grow
*past* the old EOF still reads as zeros, which is what the plan's
zero-check covers.

**Image-size ceiling.** qcow2 grow is bounded only by what the
filesystem can hold — followup-01's targeted refcount-block
pre-pass keeps the guest's metadata staging budget O(1) blocks
regardless of image size. Tested end-to-end through 1 TiB → 2 TiB
in under 200 ms (`tests/test_resize.py:TestResizeLargeImages`).
qcow2 shrink retains the older "stage every non-zero refcount
block" pre-pass and so retains a per-cluster-size ceiling
(~128 GiB at the default 64 KiB cluster); lifting it is the next
follow-up in the Future-work section below. Raw / vmdk / vpc /
vhdx have no analogous metadata-staging step and are bounded
only by filesystem capacity.

## Output format

Human (default):

```
Image resized.
```

(matches `qemu-img resize` byte-for-byte.) JSON (`--output=json`):

```json
{
  "filename": "/path/to/disk.qcow2",
  "format": "qcow2",
  "action": "grow",
  "old_virtual_size": 1048576,
  "new_virtual_size": 67108864,
  "new_file_size": 262144
}
```

`action` is one of `grow` / `shrink` / `noop`. `-q` (quiet)
suppresses both forms on success and prints only errors.

## `--shrink` semantics

Required whenever the requested final size is smaller than the
current virtual size, for the formats that support shrink (qcow2 +
raw). The flag exists to make the user's intent explicit — a shrink
discards every data byte above the new EOF, and qemu-img has the
same requirement for the same reason.

A subtractive end-spec (`-N`) implies shrink-direction but does not
imply the flag: `instar resize -f raw foo.raw -32M` still requires
`--shrink` to succeed.

For vmdk / vpc / vhdx, `--shrink` is accepted but the planner rejects
the operation unconditionally with `shrink not yet supported for this
format`. Lifting any of these is queued under Future work; qemu-img
has no upstream shrink for vhdx either.

## Preallocation modes

The mode controls how the newly-added file region is treated after a
grow. Shrink and noop ignore the mode; pairing
`--preallocation=falloc|full` with shrink is rejected outright with
`resize: --preallocation=<mode> is meaningless when shrinking`.

| Mode      | raw                                | qcow2                                 | vmdk / vpc / vhdx              |
|-----------|------------------------------------|---------------------------------------|--------------------------------|
| `off`     | sparse (default)                   | sparse (default)                      | sparse (default)               |
| `metadata`| rejected (no metadata to populate) | rejected (planner gap; Future work)   | rejected (planner gap)         |
| `falloc`  | `posix_fallocate` on the added region | `posix_fallocate` on the added region | `posix_fallocate` on the added region |
| `full`    | `fallocate(ZERO_RANGE)` with `pwrite` fallback | `fallocate(ZERO_RANGE)` with `pwrite` fallback | `fallocate(ZERO_RANGE)` with `pwrite` fallback |

For `falloc` and `full`, instar preallocates only the **newly-added
file region** (`[file_size_before, file_size_after)`), not the entire
data region of the new virtual size. qemu-img preallocates the
entire data region. This is a deliberate divergence — see
`docs/quirks.md` and Future work.

## Known divergences from `qemu-img resize`

- **vmdk / vpc / vhdx supported by instar only**. `qemu-img resize`
 rejects every version of these formats with `Image format driver
 does not support resize`. instar resizes all three (vhdx only when
 dynamic: fixed and differencing images are refused); the baseline
 matrix has no qemu side to diff against, so coverage is via the
 internal consistency suite (`TestResizeConsistency`).
- **Sparse-format data-region preallocation**. For grow with
 `falloc`/`full`, instar preallocates only the appended file
 region; qemu preallocates the entire data region. Documented in
 `docs/quirks.md`. Full parity queued under Future work.
- **`--preallocation=falloc|full` + `--shrink`**. qemu silently
 accepts and discards the flag; instar rejects with a clear
 message. Deliberate divergence for user clarity.
- **`--preallocation=metadata` on raw**. qemu accepts and no-ops;
 instar rejects with `--preallocation=metadata is not supported
 for raw`. Same rationale.
- **qcow2 `--preallocation=metadata`**. The qcow2 grow planner
 rejects with `preallocation mode not supported by this format`;
 qemu supports it. Queued under Future work as the
 `Preallocation::Metadata` planner gap.
- **VHD CHS-rounded virtual_size carry-forward**. VHD's legacy
 geometry rounds virtual_size up to the next CHS-aligned multiple;
 qemu rounds during create, instar emits exact bytes (documented
 under `## create subcommand quirks`). The divergence persists
 through resize — the resize planner preserves whatever the create
 writer chose.
- **qcow2 images carrying persistent dirty bitmaps**. instar
 **refuses** to resize them, because resizing rebuilds the header
 cluster and would drop the qcow2 bitmaps extension (and its
 bitmaps). `qemu-img resize` instead resizes the image,
 dropping/invalidating the bitmaps. instar fails with:

  ```
  refusing to resize an image with persistent dirty bitmaps (would
  discard them); remove the bitmaps first with `instar bitmap
  --remove` or qemu-img
  ```

  The refusal prevents silent bitmap-data loss; remove the bitmaps
  first with `instar bitmap --remove` (see
  [docs/bitmap.md](/components/instar/bitmap/)).

The canonical resize-side divergence whitelist is
`KNOWN_RESIZE_DIVERGENCES` at the top of `tests/test_resize.py`.
The create-side carry-forwards are listed in
`tests/test_create.py:KNOWN_WRITER_DIVERGENCES`.

## Future work

- qcow2 `Preallocation::Metadata`: emit the populated
 L1/L2/refcount layout for the new range, mirroring create's
 metadata mode.
- vmdk shrink: currently `UnsupportedShrink`; the
 monolithicSparse format itself permits shrink, the planner just
 doesn't implement the GD walk yet.
- vhd shrink: same — fixed grow + dynamic grow ship,
 shrink is deferred.
- vhdx shrink: qemu has no upstream implementation to
 mirror; we don't either.
- Sparse-format data-region preallocation: close the
 qemu-parity gap on `falloc`/`full` for qcow2 / vhdx / vmdk /
 vhd-dynamic. Each format needs a per-format walk-and-populate
 pass over its data region.
- vmdk multi-extent subformats (`twoGbMaxExtentSparse`,
 `twoGbMaxExtentFlat`, `monolithicFlat`): currently rejected
 with `UnsupportedSubformat`. Resizing one means rewriting the
 descriptor and an extent file together, so the guest would need
 several output devices open at once, which resize does not do
 today.
- Differencing VHD / VHDX as a resize target: both are refused
 (`ResizeResult::ERROR_UNSUPPORTED_SUBFORMAT`, "subformat does not
 support resize"), as qemu-img refuses a differencing VHDX. Growing a
 child alone would leave it claiming sectors its parent cannot
 describe, so neither can be supported until the parent-locator update
 path exists.
- A VHDX target size above 64 TiB (70368744177664 bytes), the largest
 VHDX qemu-img creates: refused on the host before the image is opened
 for writing (`resize: virtual size N exceeds the VHDX maximum of 64 TiB
 (70368744177664 bytes)`), and the file is left untouched. A VHDX
 already above the limit is still read by `info` and `check`, as
 qemu-img opens one, but a no-op or `--shrink` of it is refused with the
 same message: resize stages at most a 2 MiB BAT region, and above
 64 TiB even 256 MiB blocks need 3 MiB, so it could not resize one in
 any direction (#850).
- Fixed VHDX as a resize target: refused for every request, grow,
 no-op and `--shrink` alike (`ResizeResult::ERROR_FIXED_VHDX`,
 "refusing to resize a fixed VHDX"), and the file is left untouched.
 A fixed VHDX is one whose File Parameters `LeaveBlocksAllocated`
 flag is set, which is every fixed image qemu-img writes, and
 SPEC(VHDX) 2.6.2.1 says a writer "SHOULD NOT change the state of any
 blocks to a BLOCK_NOT_PRESENT state" on it. The dynamic grow adds
 exactly such blocks, so it would leave an image still flagged fixed
 whose new range is unallocated. To resize one, convert it to a
 dynamic VHDX (`instar convert -O vhdx`) and resize that. A grow that
 keeps the image fixed, by appending allocated blocks, is not
 implemented. qemu-img cannot resize any VHDX.
- `--object OBJDEF` and `--image-opts`: rejected at the host CLI. LUKS-encrypted resize would land alongside the
 matching `--object` plumbing on the convert side.
- Tightening `QCOW2_MAX_RESIZE_SCRATCH` (32 MiB) for non-default
 cluster sizes. The differential fuzzer surfaced this — 2 MiB
 cluster_size with even modest virtual sizes overflows the
 scratch buffer (`image too large for the resize scratch
 buffer`). The picker filters the combination today.
- Targeted shrink-side refcount-block pre-pass. Followup-01
 lifted the per-cluster-size image-size ceiling for qcow2
 *grow* by staging only the refcount blocks the chosen flavour
 will touch. Shrink retains the old "stage every non-zero
 block" pre-pass and so retains the ceiling (~128 GiB at the
 default 64 KiB cluster). Lifting it requires a two-phase
 shrink pre-pass: walk L2 tables first to identify which
 clusters will be discarded, then stage only the refcount
 blocks containing those clusters.
- Planner-side defensive checks for inconsistent host inputs (partially addressed by followup-01d's
 vmdk `checked_mul`). The VHDX planner can return
 `Ok(plan { total_file_size: 0 })` when the host passes
 impossibly small file sizes — not reachable from real callers
 (the host derives `current_file_size` from `stat()`) but worth
 hardening; similar shapes elsewhere need a systematic sweep.
- Re-parse round-trip in `fuzz_resize_planners`: reconstruct a
 faithful starting image from the fuzzer's synthetic
 existing-state bytes and re-parse with the matching format
 crate.
- Curated seed corpus for `fuzz_resize_planners`. `scripts/
 extract-fuzz-corpus.py` doesn't have a resize codepath today.
- Populated-image differential coverage: today the differential
 harness creates empty start images. Once data-region
 preallocation parity lands, the populated-image variant
 becomes meaningful.
- vmdk / vhd / vhdx differential coverage via libyal tools
 (`vmdkinfo`, `vhdiinfo`) if they ever gain resize support.

For the per-format resize planners, see
`src/crates/resize/src/lib.rs` (the `plan_resize_*` functions).
For the divergence whitelist applied during cross-version
baseline comparison, see `tests/helpers/info_json.py` and the
`KNOWN_RESIZE_DIVERGENCES` set at the top of
`tests/test_resize.py`.

## Examples

Grow a qcow2 image by 1 GiB:

```
instar resize -f qcow2 disk.qcow2 +1G
```

Grow a raw image to an absolute size with preallocation:

```
instar resize -f raw --preallocation falloc disk.raw 64M
```

Shrink a qcow2 image, discarding data above the new EOF:

```
instar resize -f qcow2 --shrink disk.qcow2 -512M
```

Grow a VHDX image (qemu-img cannot — `Image format driver does
not support resize`):

```
instar resize -f vhdx disk.vhdx 100G
```

JSON output for scripting:

```
instar resize -f qcow2 --output=json disk.qcow2 16M
```

Auto-detect the format from the file's magic bytes (works for
any of raw / qcow2 / vmdk / vpc / vhdx):

```
instar resize disk.qcow2 +1G
```
