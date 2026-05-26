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

Suffix multipliers: `b`=512, `k`/`K`=1024, `M`=1024², `G`=1024³,
`T`=1024⁴, `P`=1024⁵, `E`=1024⁶. An absent suffix is bytes.

A subtractive end-spec implies shrink, but `--shrink` must still be
passed explicitly — it documents the user's intent to discard data
beyond the new EOF.

## Target formats

| Target | Grow | Shrink         | Preallocation modes              | Notes                              |
|--------|------|----------------|----------------------------------|------------------------------------|
| raw    | ✓    | ✓              | off, falloc, full                | host-only (truncate + post-pass)   |
| qcow2  | ✓    | ✓ (`--shrink`) | off, falloc, full (metadata gap) | L1 + refcount-table grow           |
| vmdk   | ✓    | reject         | off                              | monolithicSparse only              |
| vpc    | ✓    | reject         | off                              | dynamic + fixed grow               |
| vhdx   | ✓    | reject         | off                              | dynamic; two-header dance          |

For raw and qcow2, the post-resize `qemu-img info --output=json`
matches `qemu-img resize` byte-for-byte across every shipped
qemu-img version (verified by the cross-version baseline matrix in
`instar-testdata/expected-outputs/resize-info-json/` and exercised
by `tests/test_resize.py:TestResizeBaselineMatrix`). For vmdk / vpc
/ vhdx — formats `qemu-img resize` does not support — the
post-resize state is verified by `tests/test_resize.py:TestResize
Consistency` (instar resize → instar info → instar check).

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
| `off`     | sparse (default)                   | sparse (default)                      | sparse (only mode supported)   |
| `metadata`| rejected (no metadata to populate) | rejected (planner gap; Future work)   | rejected (planner gap)         |
| `falloc`  | `posix_fallocate` on the added region | `posix_fallocate` on the added region | rejected (planner gap)      |
| `full`    | `fallocate(ZERO_RANGE)` with `pwrite` fallback | `fallocate(ZERO_RANGE)` with `pwrite` fallback | rejected (planner gap) |

For `falloc` and `full`, instar preallocates only the **newly-added
file region** (`[file_size_before, file_size_after)`), not the entire
data region of the new virtual size. qemu-img preallocates the
entire data region. This is a deliberate divergence — see
`docs/quirks.md` and Future work.

## Known divergences from `qemu-img resize`

- **vmdk / vpc / vhdx supported by instar only**. `qemu-img resize`
  rejects every version of these formats with `Image format driver
  does not support resize`. instar resizes all three; the baseline
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
  `Preallocation::Metadata` planner gap from phase 2c.
- **VHD CHS-rounded virtual_size carry-forward**. VHD's legacy
  geometry rounds virtual_size up to the next CHS-aligned multiple;
  qemu rounds during create, instar emits exact bytes (documented
  under `## create subcommand quirks`). The divergence persists
  through resize — the resize planner preserves whatever the create
  writer chose.

The canonical resize-side divergence whitelist is
`KNOWN_RESIZE_DIVERGENCES` at the top of `tests/test_resize.py`.
The create-side carry-forwards are listed in
`tests/test_create.py:KNOWN_WRITER_DIVERGENCES`.

## Future work

- qcow2 `Preallocation::Metadata` (phase 2c): emit the populated
  L1/L2/refcount layout for the new range, mirroring create's
  metadata mode.
- vmdk shrink (phase 6): currently `UnsupportedShrink`; the
  monolithicSparse format itself permits shrink, the planner just
  doesn't implement the GD walk yet.
- vhd shrink (phase 4): same — fixed grow + dynamic grow ship,
  shrink is deferred.
- vhdx shrink (phase 5): qemu has no upstream implementation to
  mirror; we don't either.
- Sparse-format data-region preallocation (phase 9): close the
  qemu-parity gap on `falloc`/`full` for qcow2 / vhdx / vmdk /
  vhd-dynamic. Each format needs a per-format walk-and-populate
  pass over its data region.
- vmdk multi-extent subformats (`twoGbMaxExtentSparse`,
  `twoGbMaxExtentFlat`, `monolithicFlat`): currently rejected
  with `UnsupportedSubformat`. Multi-file resize needs the same
  multi-output-device call-table extension that create's roadmap
  already calls out.
- Differencing VHD / VHDX as a resize target: rejected today;
  needs the parent-locator update path.
- `--object OBJDEF` and `--image-opts`: rejected at the host CLI
  (phase 8). LUKS-encrypted resize would land alongside the
  matching `--object` plumbing on the convert side.
- Tightening `QCOW2_MAX_RESIZE_SCRATCH` (32 MiB) for non-default
  cluster sizes. The differential fuzzer surfaced this — 2 MiB
  cluster_size with even modest virtual sizes overflows the
  scratch buffer (`image too large for the resize scratch
  buffer`). The picker filters the combination today.
- Targeted shrink-side refcount-block pre-pass. Followup-01
  lifted the per-cluster-size image-size ceiling for qcow2
  *grow* by staging only the refcount blocks the chosen flavour
  will touch.  Shrink retains the old "stage every non-zero
  block" pre-pass and so retains the ceiling (~128 GiB at the
  default 64 KiB cluster).  Lifting it requires a two-phase
  shrink pre-pass: walk L2 tables first to identify which
  clusters will be discarded, then stage only the refcount
  blocks containing those clusters.
- Planner-side defensive checks for inconsistent host inputs
  (phase 12 finding; partially addressed by followup-01d's
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
