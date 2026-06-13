# `instar create` — create a new empty disk image

`instar create` produces a new empty disk image in a target format. It is
the safe, sandboxed equivalent of `qemu-img create`: for every supported
format, `qemu-img info --output=json` on an `instar create`-produced
image matches `qemu-img info` on the corresponding `qemu-img create`
image, modulo a documented divergence whitelist (filename, actual-size,
vmdk per-invocation CIDs, vhdx log-size).

Raw output is host-only — `open(O_CREAT|O_TRUNC) + ftruncate` plus
optional preallocation — so it does not require KVM. Every other target
format runs the `create.bin` guest in the KVM sandbox and writes the
metadata via virtio-block.

## Synopsis

```
instar create [-f FMT] [OPTIONS] [-b BACKING [-F FMT] [-u]] \
              FILENAME [SIZE]
```

Common options:

```
  -f, --format <FMT>            raw | qcow2 | vmdk | vpc | vhdx
                                Default: raw
  -b, --backing <BACKING>       Backing file path (embedded verbatim)
  -F, --backing-format <FMT>    Backing file format hint
  -u, --backing-unsafe          Don't fail if backing isn't accessible
  -q, --quiet                   Suppress "Created: ..." line on success
      --output <FORMAT>         human (default) | json
  -o, --options <KEY=VALUE,...> qemu-img-style options (repeatable)
      --preallocation <MODE>    off | metadata | falloc | full
```

Per-target option flags (alternatives to `-o`):

```
  qcow2: --cluster-size, --refcount-bits, --extended-l2,
         --lazy-refcounts, --compat
  vmdk:  --subformat, --grain-size
  vpc:   --subformat, --block-size
  vhdx:  --block-size
```

`SIZE` accepts a suffix from `{b,K,M,G,T}` (e.g. `1G`, `512M`). It is
required unless `-b BACKING` is given, in which case it defaults to the
backing file's virtual size.

The full flag surface is reported by `instar create --help`.

## Target formats

| Target | Subformats             | Backing? | qemu-img info equivalence |
|--------|------------------------|----------|---------------------------|
| raw    | (host-only)            | No       | byte-equivalent (file size + zero-fill) |
| qcow2  | n/a                    | Yes      | info-equivalent (modulo refcount_bits, compat, zstd) |
| vmdk   | monolithicSparse, streamOptimized | Yes | info-equivalent |
| vpc    | dynamic, fixed         | Yes      | info-equivalent (modulo CHS virtual_size rounding) |
| vhdx   | dynamic                | Yes      | info-equivalent (modulo default block_size when unspecified) |

The "info-equivalence" contract is verified by the cross-version baseline
matrix in `instar-testdata/expected-outputs/create-info-json/` across 80
qemu-img versions (6.0.0 through 10.2.0), exercised by
`tests/test_create.py`'s `TestCreateBaselineMatrix`.

## Output format

Human (default):

```
Created: foo.qcow2 (format=qcow2, virtual_size=1073741824, unit_size=65536)
```

JSON (`--output=json`):

```json
{
    "format": "qcow2",
    "filename": "foo.qcow2",
    "virtual_size": 1073741824,
    "resolved_unit_size": 65536,
    "metadata_bytes_written": 196608,
    "file_size_after": 196608
}
```

`-q` (quiet) suppresses both forms on success and only prints errors.

## `-o key=value,...` reference

Repeatable; later `-o` invocations override earlier ones. Individual
flag values are overridden by matching `-o` keys when both are
specified.

### qcow2

Honoured:
- `size` (alternative to the positional `SIZE`)
- `cluster_size` (512..2 MiB power of two)
- `refcount_bits` — see Known divergences
- `extended_l2` (on|off; requires `cluster_size >= 16k`)
- `lazy_refcounts` (on|off)
- `compat` (0.10|1.1) — see Known divergences
- `compression_type` (zlib|zstd) — see Known divergences
- `preallocation` (off|metadata|falloc|full)
- `backing_file`, `backing_fmt` (alternative to `-b` / `-F`)

Rejected (future work):
- `data_file`, `data_file_raw`, `encrypt.*`

### vmdk

Honoured:
- `size`, `subformat` (monolithicSparse|streamOptimized)
- `backing_file`, `backing_fmt`

Rejected (instar doesn't emit these subformats):
- `subformat=monolithicFlat`, `subformat=twoGbMaxExtent*`

Note: qemu-img doesn't accept an `-o grain_size=` key — grain is set by
the subformat. instar exposes `--grain-size` as an independent flag.

### vpc (VHD)

Honoured:
- `size`, `subformat` (dynamic|fixed)
- `backing_file`, `backing_fmt`

Accepted but no size effect:
- `force_size`

### vhdx

Honoured:
- `size`, `block_size` (1 MiB..256 MiB power of two)
- `backing_file`, `backing_fmt`

Accepted but no size effect:
- `log_size`

### raw

Honoured:
- `size`, `preallocation` (off|falloc|full)

Rejected:
- `preallocation=metadata` (raw has no metadata to preallocate)
- Any backing-file key (raw doesn't support backing)

## Backing-file semantics

The user-typed path is embedded verbatim into the new image's metadata,
matching qemu-img: a relative path stays relative; an absolute path
stays absolute. The host resolves the path **relative to the new
image's directory** when opening the backing file, so the resulting
reference is portable across moves of the parent.

A backing file requires either `-F FMT` (explicit format hint) or `-u`
(unsafe; assume raw). The hint is used as the initial format guess; if
the backing file's first sector contradicts the hint via its magic
bytes, auto-detection wins and the metadata records the detected
format. Three-level chains record only the immediate parent — instar
does not recurse to grandparents (matches qemu-img). When the backing
size would exceed the target format's addressable range,
`ERROR_BACKING_SIZE_TOO_LARGE` fires with an actionable
"try a larger cluster size" hint.

## Preallocation modes

| Mode      | raw                   | qcow2                                                | vmdk / vpc / vhdx |
|-----------|-----------------------|------------------------------------------------------|-------------------|
| `off`     | `ftruncate` only      | header + L1 + refcount only                          | header + BAT only |
| `metadata`| rejected              | L1 + L2 + refcount populated; data region as a hole  | rejected (future work) |
| `falloc`  | `posix_fallocate`     | metadata mode + `posix_fallocate` on data region     | rejected (future work) |
| `full`    | `fallocate(ZERO_RANGE)` with `pwrite` fallback | metadata mode + zero-fill data region   | rejected (future work) |

For qcow2 metadata mode the L2 tables can total well over the guest's
8 MiB scratch limit at large virtual sizes, so the guest streams them
through a reusable single-cluster slot rather than packaging them into
the `MetadataPlan`. The host's `apply_preallocation` helper handles
`falloc` and `full` post-emission.

## Known divergences from `qemu-img create`

The cross-version baseline matrix passes for the bulk of supported
options, but four categories of writer divergence are documented and
tracked as future work. The canonical list (with per-case rationale)
is `KNOWN_WRITER_DIVERGENCES` in `tests/test_create.py`.

- **VHD `virtual_size`**: qemu-img rounds up to the next CHS-aligned
  multiple (legacy VHD geometry); instar emits exact bytes. The
  divergence is typically < 256 KiB. Both files are valid VHDs; the
  difference surfaces only in `qemu-img info`'s `virtual-size` field.
- **qcow2 `compat=0.10`**: the writer always emits `compat=1.1`.
  qemu-img honours both.
- **qcow2 `compression_type=zstd`**: the writer records `zlib` in the
  header regardless. qemu-img switches over to the zstd cluster
  encoder when zstd is requested. The cluster data itself is empty in
  a fresh image so the field-only divergence has no functional
  impact, but `qemu-img info` reports the discrepancy.
- **vhdx default `block_size`**: at virtual sizes ≤ 1 GiB instar
  defaults to 8 MiB; qemu-img always defaults to 32 MiB. Pass
  `-o block_size=...` (or `--block-size`) explicitly to match.
  Explicit block sizes round-trip cleanly.

All `-o refcount_bits=` widths (1/2/4/8/16/32/64) are now written
correctly: `build_header` derives `refcount_order` from `refcount_bits`
and sub-byte widths pack LSB-first, so the produced files round-trip
through `instar check` and match `qemu-img` (instar #365).

## Future work

- VHD CHS-geometry round-trip matching (close the `virtual_size`
  divergence).
- qcow2 `compat=0.10` honouring.
- zstd-aware qcow2 create (drop the accept-ignore; emit the zstd
  header bit).
- vhdx default `block_size` matching qemu's 32 MiB at all virtual
  sizes.
- Preallocation for vmdk / vpc / vhdx (each format needs its own
  BAT-population pattern plus the same host `apply_preallocation`
  post-pass that qcow2 already uses).
- Multi-file VMDK subformats (`monolithicFlat`, `twoGbMaxExtentSparse`,
  `twoGbMaxExtentFlat`) — needs multi-output-device support in the
  call table.
- VHDX `subformat=fixed` (not emitted by qemu-img either).
- `--object OBJDEF` and `encrypt.*` for LUKS-encrypted create —
  pair with the existing convert-side passphrase plumbing.
- Atomic-rename safety: write to `FILENAME.tmp` and `rename()` on
  guest success, so a guest crash mid-emission doesn't leave a
  partial file.
- Refactor convert to call into `crates/create/` for the metadata
  emission step (master-plan future-work item).

For the per-format metadata layouts implemented today, see
`src/crates/create/src/lib.rs` (the `plan_qcow2` / `plan_vmdk` /
`plan_vhd` / `plan_vhdx` functions). For the divergence-whitelist
applied during JSON comparison, see `tests/helpers/info_json.py`.

## Examples

Create a 1 GiB qcow2 with default options:

```
instar create -f qcow2 disk.qcow2 1G
```

Same but with a 4 KiB cluster size and extended L2:

```
instar create -f qcow2 -o cluster_size=4k,extended_l2=on disk.qcow2 1G
```

Create a vmdk with the stream-optimised subformat:

```
instar create -f vmdk -o subformat=streamOptimized disk.vmdk 1G
```

Create a 16 MiB fixed VHD (footer-only metadata at end of file):

```
instar create -f vpc -o subformat=fixed disk.vhd 16M
```

Create a vhdx with an explicit 32 MiB block size (matches qemu-img's
default and avoids the `vhdx` default-block-size divergence):

```
instar create -f vhdx -o block_size=32M disk.vhdx 1G
```

Create a qcow2 child image with a backing reference (virtual_size
defaults from the parent):

```
instar create -f qcow2 -b parent.qcow2 -F qcow2 child.qcow2
```

Create a raw image with `falloc` preallocation (reserves blocks
without writing them):

```
instar create -f raw --preallocation falloc disk.raw 1G
```

JSON output for scripting:

```
instar create -f qcow2 --output json disk.qcow2 1G
```
