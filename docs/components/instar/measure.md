# `instar measure` — predict file size for a target format

`instar measure` predicts the file size required to convert a source image
(or a hypothetical empty image of a given size) to a target format. It is
the safe, sandboxed equivalent of `qemu-img measure`, with byte-for-byte
output parity for the targets qemu-img supports plus instar-specific support
for vmdk, vpc (VHD), and vhdx targets that qemu-img cannot measure.

## Synopsis

```
instar measure [OPTIONS] [INPUT]
```

Exactly one of `--size SIZE` or a positional `INPUT` file path is required.

Common options:

```
  -O, --target-format <FMT>       raw | qcow2 | vmdk | vpc | vhdx
                                  Default: raw
  -s, --size <SIZE>               Hypothetical image size with K/M/G/T suffix
  -f, --format <FMT>              Source format override (usually auto-detected)
      --output <FORMAT>           human (default) | json
  -o, --options <KEY=VALUE,...>   qemu-img-style options (repeatable)
```

Per-target option flags (alternatives to `-o`):

```
  qcow2: --cluster-size, --refcount-bits, --extended-l2,
         --lazy-refcounts, --compat, --preallocation, --compress
  vmdk:  --subformat, --grain-size
  vpc:   --subformat, --block-size
  vhdx:  --subformat, --block-size
```

The full flag surface is reported by `instar measure --help`.

## Target formats

| Target | Source-image mode | --size mode | qemu-img parity |
|--------|-------------------|-------------|-----------------|
| raw    | Yes | Yes | byte-identical |
| qcow2  | Yes | Yes | byte-identical |
| vmdk   | Yes | Yes | instar-only |
| vpc    | Yes | Yes | instar-only |
| vhdx   | Yes | Yes | instar-only |

For raw and qcow2 targets, instar's `--output=human` and `--output=json`
output matches `qemu-img measure` exactly across every qemu-img version from
6.0.0 through 10.2.0 (verified by the cross-version baseline matrix in
`instar-testdata`).

## Output format

Human (default):

```
required size: 327680
fully allocated size: 1376256
```

JSON (`--output=json`):

```json
{
    "required": 327680,
    "fully-allocated": 1376256
}
```

Note the hyphen in `fully-allocated`. Four-space indent, no trailing comma
after the last field — these match qemu-img exactly.

For qcow2 v3 sources targeting qcow2, instar emits an additional `bitmaps`
field (count of persistent qcow2 bitmaps the conversion would preserve,
currently always 0). qemu-img has the same rule. The gate uses a 4+4 byte
peek of magic and version; see `docs/quirks.md` for details.

## `-o key=value,...` reference

Repeatable; later `-o` invocations override earlier ones. Individual flag
values are overridden by matching `-o` keys when both are specified.

### qcow2

Honoured:
- `cluster_size`, `compat` (0.10|1.1), `refcount_bits`
- `extended_l2` (on|off), `lazy_refcounts` (on|off)
- `compression_type` (zlib|zstd), `preallocation` (off|metadata|falloc|full)

Rejected (future work):
- `backing_file`, `backing_fmt`, `data_file`, `data_file_raw`, `encrypt.*`

### vmdk

Honoured:
- `subformat` (monolithicSparse|streamOptimized|monolithicFlat), `grain_size`

Accepted but no size effect:
- `adapter_type`, `hwversion`, `toolsversion`, `zeroed_grain`

### vpc (VHD)

Honoured:
- `subformat` (dynamic|fixed)

Accepted but no size effect:
- `force_size`, `force_size_calc`

### vhdx

Honoured:
- `subformat` (dynamic), `block_size`

Accepted but no size effect:
- `log_size`, `block_state_zero`

Rejected (future work):
- `subformat=fixed`

### raw

No options accepted. Any `-o` key with `-O raw` is rejected with an error.

## Known divergences from qemu-img

For raw and qcow2 targets, instar measure matches qemu-img exactly on the
cross-version baseline matrix. A handful of source-image cases exhibit small
numeric divergences because instar's parser scanners are simpler than
qemu-img's:

- **Raw sources with on-disk sparse extents**: instar over-reports `required`
  because the raw scanner does not use `SEEK_HOLE`/`SEEK_DATA`. qemu-img does.
- **QCOW2 sources for some real-world images**: instar's scanner counts
  allocated bytes slightly differently. Likely a compressed-cluster or
  extended-L2 subcluster edge case under investigation.
- **QCOW2 sources with backing chains**: instar reports the top layer's
  allocations only. qemu-img consults the chain.
- **VHDX sources**: instar treats every BAT block as fully allocated.
  qemu-img returns the actual block-state distribution.
- **VMDK multi-extent source layouts**: instar's extent map propagation
  differs from qemu-img for some non-trivial layouts.
- **VHD legacy CHS-only sources**: instar's reported virtual_size differs
  by approximately 2 MiB.

The canonical list of test cases skipped due to these divergences is
`KNOWN_SOURCE_SCANNER_DIVERGENCES` in `tests/test_measure.py`. Each entry
names the divergence category.

## Future work

- SEEK_HOLE detection in the raw scanner.
- VHDX scanner partial-block-state walk.
- VMDK multi-extent sparse propagation.
- QCOW2 scanner backing-chain composition.
- QCOW2 compressed-cluster / extended-L2 subcluster overcount investigation.
- `encrypt.format=luks` aware sizing.
- `-l SNAPSHOT` snapshot-targeted measurement.
- `--image-opts` parsing.
- `-o help` listing.
- `subformat=fixed` for vhdx target.

For per-format size formulas implemented today, see
`src/crates/measure/src/lib.rs`.

## Examples

Predict size for a 1 GiB hypothetical qcow2 with the default cluster size:

```
instar measure --size 1G -O qcow2
```

Same but with a 4 KiB cluster size:

```
instar measure --size 1G -O qcow2 -o cluster_size=4k
```

Predict size for converting a qcow2 image to raw:

```
instar measure -O raw mydisk.qcow2
```

JSON output for scripting:

```
instar measure -O qcow2 --output=json mydisk.vmdk
```

Predict an instar-only target (qemu-img cannot do this):

```
instar measure -O vhdx mydisk.qcow2
```
