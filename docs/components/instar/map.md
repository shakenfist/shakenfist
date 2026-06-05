# `instar map` — emit the allocation map of a disk image

`instar map` emits the allocation map of a disk image as a stream of
contiguous `(start, length, state)` extents covering
`[0, virtual_size)`, mirroring `qemu-img map`'s output. It is the
safe, sandboxed equivalent of `qemu-img map`, with byte-for-byte
output parity across the cross-version qemu-img baseline matrix
modulo a small number of documented divergences. Single-image v1;
backing-chain `depth` composition is deferred to a follow-up.

The host renderer streams each extent to stdout as the in-guest
parser walks the source's allocation metadata — host memory stays
O(1) regardless of how fragmented the source is.

## Synopsis

```
instar map [OPTIONS] <INPUT>
```

Common options:

```
  -f, --format <FMT>              Source format override (usually auto-detected)
      --output <FORMAT>           human (default) | json
      --start-offset <OFFSET>     Emit extents starting at OFFSET bytes
      --max-length <LEN>          Stop emission at OFFSET + LEN bytes
      --sector-size <N>           Source sector size (default: 65536)
```

`--image-opts` is explicitly rejected; the positional `<INPUT>`
filename is the only supported source specification.

The full flag surface is reported by `instar map --help`.

## Output format

Both renderers consume the same in-guest extent stream and produce
output that matches `qemu-img map` byte-for-byte (modulo the
divergences below).

### Human (default)

```
$ instar map fragmented.qcow2
Offset          Length          Mapped to       File
0               0x10000         0x50000         fragmented.qcow2
0x80000         0x10000         0x60000         fragmented.qcow2
```

Four fixed-width 16-character columns plus a trailing filename
column. Holes (unallocated regions) are elided — only `Data` and
`ZeroAllocated` extents emit a row. Offsets use lowercase `0x` hex,
except a literal `0` for zero values. The trailing newline after the
last row matches qemu-img.

### JSON (`--output=json`)

```
$ instar map --output=json fragmented.qcow2
[{ "start": 0, "length": 65536, "depth": 0, "present": true, "zero": false, "data": true, "compressed": false, "offset": 327680},
{ "start": 65536, "length": 458752, "depth": 0, "present": false, "zero": true, "data": false, "compressed": false},
{ "start": 524288, "length": 65536, "depth": 0, "present": true, "zero": false, "data": true, "compressed": false, "offset": 393216},
{ "start": 589824, "length": 458752, "depth": 0, "present": false, "zero": true, "data": false, "compressed": false}]
```

One JSON object per extent, comma-separated, opened by `[` and
closed by `]\n`. Every extent — including holes — is emitted in
JSON mode. Field semantics:

| Field        | Meaning                                                   |
|--------------|-----------------------------------------------------------|
| `start`      | Virtual offset of the extent's first byte.                |
| `length`     | Extent length in bytes (never zero).                      |
| `depth`      | Backing-chain depth. Always `0` in v1.                    |
| `present`    | The extent is mapped to backing storage.                  |
| `zero`       | The extent reads as zero.                                 |
| `data`       | The extent contains data (not unconditional zero-fill).   |
| `compressed` | The extent is backed by a compressed cluster. Always `false` in v1 — see "Known divergences" below. |
| `offset`     | File offset of the extent's first byte. Omitted (not `null`) when `present == false`. |

The combination `(present, zero, data)` distinguishes the three
underlying states the parser emits:

| State              | `present` | `zero` | `data` |
|--------------------|-----------|--------|--------|
| `Data`             | `true`    | `false`| `true` |
| `ZeroAllocated`    | `true`    | `true` | `false`|
| `Hole`             | `false`   | `true` | `false`|

## Per-format extent classification

Each parser walks the format's on-disk allocation metadata and emits
one extent per coalesced run of same-state clusters. The canonical
implementation lives in `<format>::map_extents` in
`src/crates/<format>/src/lib.rs`.

### qcow2

L1 table → L2 tables walk. Per L2 entry:

- Zero/unallocated entry (`0`): `Hole`.
- `QCOW2_CLUSTER_ZERO_PLAIN` / `QCOW2_CLUSTER_ZERO_ALLOC`
  (extended-L2 subcluster bitmap only): `ZeroAllocated`. On
  *standard* L2 tables instar does not honour the qcow2 v3
  `QCOW_OFLAG_ZERO` bit and reports such clusters as `Data`
  (or `Hole` when `host_offset == 0`). See the "Known
  divergences" section below.
- Allocated cluster: `Data` with the L2 host-cluster offset as the
  `file_offset`.
- Compressed cluster: `Data` with the compressed-payload file
  offset (see compressed-cluster divergence below).

Consecutive same-state clusters merge when their file offsets are
contiguous, matching qemu-img.

### vmdk

Grain directory → grain table walk. Per grain-table entry:

- Allocated grain: `Data` with the grain's file offset.
- Zero-grain sentinel (`0xFFFFFFFE`): `ZeroAllocated`.
- Unallocated entry: `Hole`.

Single-extent monolithic layouts only — descriptor-driven
multi-extent sources are refused host-side (see below).

### vhd

BAT walk. Per BAT entry:

- `0xFFFFFFFF` (unallocated): `Hole`. Note: this is reported as a
  hole faithful to the on-disk BAT marker; qemu-img reports the
  same region as `ZeroAllocated`. See "Known divergences" below.
- Other entries: `Data` with the block's file offset.

Fixed-subformat VHDs emit one extent covering the virtual size.

### vhdx

BAT walk, skipping interleaved sector-bitmap entries by the
`chunk_ratio`. Per payload BAT entry:

- `PAYLOAD_BLOCK_FULLY_PRESENT`: `Data` with the block's file
  offset.
- `PAYLOAD_BLOCK_PARTIALLY_PRESENT`: treated as `Data` (per-sector
  bitmap walk is future work; see below).
- `PAYLOAD_BLOCK_ZERO` / `PAYLOAD_BLOCK_UNMAPPED`: `Hole`.

### raw

Single fully-allocated extent covering the virtual size. The no_std
raw parser cannot call `SEEK_HOLE` from inside the guest, so on-disk
sparseness is not reflected (see below).

## Known divergences from qemu-img

Each entry is documented in [quirks.md](/components/instar/quirks/) with the
rationale and the future-work pointer.

- **Raw source sparseness is not detected** — instar reports raw
  as one fully-allocated extent; `qemu-img map` walks `SEEK_HOLE`.
  See [docs/quirks.md § Raw source sparseness is not detected](/components/instar/quirks/#raw-source-sparseness-is-not-detected).
- **VHD unallocated blocks are reported as `present: false`** —
  instar reports `present=false, zero=true, data=false` (faithful
  to BAT `0xFFFFFFFF`); qemu-img reports
  `present=true, zero=true, data=false`. Tool authors comparing
  the two binaries should prefer `data` and `zero` over `present`
  for backwards-compatible behaviour. See
  [docs/quirks.md § VHD unallocated blocks are reported as `present: false`](/components/instar/quirks/#vhd-unallocated-blocks-are-reported-as-present-false).
- **VHDX `PARTIALLY_PRESENT` blocks are reported as `data: true`**
  — instar treats them as fully present; qemu-img walks the
  per-sector bitmap and emits per-sector extents. See
  [docs/quirks.md § VHDX `PAYLOAD_BLOCK_PARTIALLY_PRESENT` is reported as `data: true`](/components/instar/quirks/#vhdx-payload_block_partially_present-is-reported-as-data-true).
- **VMDK multi-extent sources are refused** — descriptor-driven
  (monolithicFlat, 2GbMaxExtent…) sources fail host-side via
  `peek_is_vmdk_descriptor`. Use `qemu-img map` as the workaround.
  See [docs/quirks.md § VMDK multi-extent sources are refused](/components/instar/quirks/#vmdk-multi-extent-sources-are-refused).
- **qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO` not honoured** — for
  qcow2 v3 (compat=1.1) images that use standard 8-byte L2
  entries, instar reports `QCOW2_CLUSTER_ZERO_PLAIN` /
  `_ZERO_ALLOC` clusters as `Data` (or `Hole` when
  `host_offset == 0`) rather than `ZeroAllocated`. Extended-L2
  subcluster bitmaps are unaffected. See
  [docs/quirks.md § qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO` not honoured](/components/instar/quirks/#qcow2-v3-standard-l2-qcow_oflag_zero-not-honoured).
- **qcow2 compressed clusters report `compressed: false`** —
  instar's renderer always emits `false`; qemu-img emits `true`
  for compressed-cluster extents. The `file_offset` field for
  compressed clusters also does not have the nb_sectors-1 bits
  stripped. See
  [docs/quirks.md § qcow2 compressed clusters report `compressed: false`](/components/instar/quirks/#qcow2-compressed-clusters-report-compressed-false).
- **Backing-chain `depth` is always 0 in v1** — instar refuses
  sources with a backing pointer; qemu-img walks the chain. See
  [docs/quirks.md § Backing-chain `depth` is always 0 in v1](/components/instar/quirks/#backing-chain-depth-is-always-0-in-v1).
- **`--image-opts` is rejected** — qemu-img's descriptor-based
  source specification (`map --image-opts driver=qcow2,...`) is
  not supported. Use the positional `<INPUT>` argument instead.
  See [docs/quirks.md § `--image-opts` is rejected](/components/instar/quirks/#--image-opts-is-rejected).
- **Window filter is byte-level, not cluster-aligned** —
  `--start-offset=N` in instar clips at byte N; qemu-img clamps to
  the cluster boundary covering N. Functionally equivalent for
  downstream consumers that care about byte ranges. See
  [docs/quirks.md § Window filter is byte-level, not cluster-aligned](/components/instar/quirks/#window-filter-is-byte-level-not-cluster-aligned).

The canonical list of integration-test cases skipped due to these
divergences is `KNOWN_MAP_DIVERGENCES` in `tests/test_map.py`. The
phase 8 differential fuzzer's `MAP_FIELD_SKIPS` in
`scripts/differential-fuzz.py` skips the `present` field on vpc
sources for the same VHD-unallocated reason.

## Future work

- **Backing-chain `depth` composition.** v1 refuses chain sources;
  the follow-up phase feeds multiple parser iterators into a
  shadowing walker that emits the correct `depth` per extent. The
  protobuf already reserves the field.
- **Raw `SEEK_HOLE` / `SEEK_DATA` host-side prepass.** Same shape as
  the analogous `measure` follow-up: the VMM does the `lseek` scan
  before launching the guest and passes a precomputed sparse-extent
  list via `MapConfig`.
- **VHDX per-sector bitmap walk** for `PAYLOAD_BLOCK_PARTIALLY_PRESENT`.
- **VMDK multi-extent descriptor propagation** for descriptor-driven
  layouts.
- **qcow2 compressed-cluster sub-classification** — carry the
  compressed bit through the FFI and renderer; strip the
  `nb_sectors-1` bits from the file offset to match qemu-img's
  high-bit-set marker convention.
- **qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO` honouring** — branch
  on the bit in `classify_qcow2_l2_standard` and emit
  `ZeroAllocated`. A matching `cluster_lookup` change is the
  prerequisite so the parser stays consistent across operations.
- **`-l SNAPSHOT` snapshot-targeted mapping** — reuses convert's
  snapshot machinery.
- **`--image-opts` parsing** — deferred until a real user requests
  it.

## Examples

Default human output:

```
instar map disk.qcow2
```

JSON for scripting:

```
instar map --output=json disk.qcow2
```

Window slice — emit only the extents covering bytes `[1 MiB, 5 MiB)`:

```
instar map --start-offset=1M --max-length=4M disk.qcow2
```

Format hint (skip auto-detection):

```
instar map -f vmdk disk.vmdk
```

Stream into `jq` to extract just the data extents:

```
instar map --output=json disk.qcow2 | jq '.[] | select(.data == true)'
```
