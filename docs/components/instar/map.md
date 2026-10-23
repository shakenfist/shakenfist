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
      --qemu-version <VERSION>    Emulate this qemu-img version's output
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
column. Only extents with `data: true` emit a row; holes and
zero-reading extents are elided, as `qemu-img map` elides them.
Offsets use lowercase `0x` hex, except a literal `0` for zero
values. The trailing newline after the last row matches qemu-img.

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
| `offset`     | File offset of the extent's first byte. Emitted when the state names a host location (`data`, `zero-at` and `hole-at` below), and omitted (not `null`) otherwise, so a `present: false` extent can carry one. |

#### The `compressed` field is version-dependent

qemu-img added `compressed` in **8.2.0**; before that the key is absent
entirely rather than emitted as `false`. instar matches whichever
qemu-img it detects, so on Debian 12 (7.2) or Ubuntu 22.04 (6.2) the
output above has no `compressed` key at all:

```
$ instar map --output=json --qemu-version 8.1 fragmented.qcow2
[{ "start": 0, "length": 65536, "depth": 0, "present": true, "zero": false, "data": true, "offset": 327680},
{ "start": 65536, "length": 458752, "depth": 0, "present": false, "zero": true, "data": false}]
```

Use `--qemu-version` to emulate a specific version explicitly; without
it instar detects the installed qemu-img, exactly as `instar info`
does.

The parser emits one of five states per extent. The combination
`(present, zero, data)` and whether `offset` is emitted distinguish
them:

| State     | Meaning                                                    | `present` | `zero` | `data` | `offset` |
|-----------|------------------------------------------------------------|-----------|--------|--------|----------|
| `data`    | Backed by bytes in the source file                         | `true`    | `false`| `true` | yes      |
| `zero`    | Recorded as zero in the metadata                           | `true`    | `true` | `false`| no       |
| `zero-at` | As `zero`, but the metadata also names a host cluster      | `true`    | `true` | `false`| yes      |
| `hole`    | Unallocated                                                | `false`   | `true` | `false`| no       |
| `hole-at` | As `hole`, but the metadata names a host cluster the extent does not use | `false` | `true` | `false`| yes |

Only qcow2 produces `zero-at` and `hole-at`; they are qemu's
`ZERO_ALLOC` and `UNALLOCATED_ALLOC` subcluster types, for which
`qemu-img map` also prints an `offset`. The parser's state names are
`MapExtentState`'s variants in `src/shared/src/lib.rs` (`Data`,
`ZeroAllocated`, `ZeroAllocatedAt`, `Hole`, `HoleAt`), and the
sections below use them.

## Per-format extent classification

Each parser walks the format's on-disk allocation metadata and emits
one extent per coalesced run of same-state clusters. The canonical
implementation lives in `<format>::map_extents` in
`src/crates/<format>/src/lib.rs`.

### qcow2

L1 table → L2 tables walk. Each L2 entry, or with extended L2 each
run of same-typed subclusters, is classified by `subcluster_type()`,
the same transcription of qemu's `qcow2_get_subcluster_type` that the
read path uses, and maps to the state `qemu-img map` reports for that
type:

- `NORMAL`: `Data` at the host offset. With an external data file the
  offset is into the data file, and an entry of `QCOW_OFLAG_COPIED`
  at host offset 0 is a normal cluster at offset 0.
- `ZERO_ALLOC` (the standard-L2 `QCOW_OFLAG_ZERO` bit, or an
  extended-L2 zero bit, in an entry with a host offset):
  `ZeroAllocatedAt` the host offset.
- `ZERO_PLAIN` (the same without a host offset): `ZeroAllocated`.
- `UNALLOCATED_ALLOC` (extended L2 only: a subcluster with neither
  bitmap bit set, in an entry with a host offset): `HoleAt` the host
  offset.
- `UNALLOCATED_PLAIN` (no host offset): `Hole`.
- `COMPRESSED`: `Data` with the compressed-payload file offset (see
  the compressed-cluster divergence below).
- `INVALID`: refused; see "Refused qcow2 L2 entries" below.

With extended L2 a run's offset is the entry's host offset plus the
run's offset into the cluster. Consecutive same-state clusters merge
when their file offsets are contiguous, matching qemu-img.

### vmdk

Grain directory → grain table walk. Per grain-table entry:

- Allocated grain: `Data` with the grain's file offset.
- Zero-grain sentinel (`0xFFFFFFFE`), or `1` when the header sets
  the zeroed-grain flag: `ZeroAllocated`.
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
  offset, refused instead if the offset is zero (the file identifier
  and headers live there) or the block's byte range overlaps any
  region the image's own region table declares, such as the BAT or
  metadata region. See "Malformed block tables" below.
- `PAYLOAD_BLOCK_PARTIALLY_PRESENT`: treated as `Data` (per-sector
  bitmap walk is future work; see below).
- `PAYLOAD_BLOCK_ZERO`: `ZeroAllocated`.
- `PAYLOAD_BLOCK_NOT_PRESENT` / `PAYLOAD_BLOCK_UNDEFINED` /
  `PAYLOAD_BLOCK_UNMAPPED`, and any reserved state: `Hole`.

### raw

Single fully-allocated extent covering the virtual size. The no_std
raw parser cannot call `SEEK_HOLE` from inside the guest, so on-disk
sparseness is not reflected (see below).

## Malformed block tables

`map`'s VHDX walk refuses a payload block whose BAT entry names a
byte range that is the image's own structure, rather than reporting
it as `Data`: file offset zero, where the file identifier and
headers live, or a range overlapping any region the image's region
table declares (the BAT region, the metadata region, or one this
build does not recognise). This is the same test the guest chain
walker already applies when reading such a block for `convert`,
`dd`, `compare`, `bench` and `rebase`; `map`'s own whole-BAT walk did
not apply it until recently, so it could report a block one of those
operations already refused to read:

```
map: source block table names a block inside the image's own
structure (at file offset zero, or overlapping a region the image
declares); the format is recognised, the image is malformed
```

Exit 1, the same as any other refused source. The format is
recognised and the image parsed far enough to walk its BAT, so the
message deliberately does not say "unrecognised" — that would send a
user hunting a format problem that is not there. A block declared
immediately past the last region in the table is unaffected and
still maps normally. See
[docs/format-internals.md § vhdx](/components/instar/format-internals/#vhdx) for the
predicate both the readers and this walk share.

## Refused qcow2 L2 entries

`map` refuses a qcow2 image whose L2 table holds an entry `qemu-img
map` fails on with `EIO`, rather than mapping it. There are two
kinds, and the message for each keeps qemu's wording:

- An entry `subcluster_type()` classifies as invalid: an extended-L2
  bitmap with an allocation bit set in an entry with no host cluster,
  or a subcluster both allocated and zero.

  ```
  map: Invalid cluster entry found at virtual offset 65536 (0x10000): corrupt qcow2 L2 entry
  ```

- In an image with an external data file, an entry that breaks one of
  qemu's data-file rules: a compressed entry, or one whose host cluster
  offset is not its guest cluster offset (qemu maps a data file one to
  one, and applies this to zero and unallocated entries that name a
  host cluster as well).

  ```
  map: Compressed cluster entry found in image with external data file at virtual offset N (0x...)
  map: External data file host cluster offset does not match guest cluster offset at virtual offset N (0x...)
  ```

The offset is the virtual offset of the cluster the entry describes.
Exit 1; as on any other failure, the extents before the refused entry
stay on stdout. `measure` refuses the same entries, and the reading
operations refuse them when a read reaches one. See
[docs/quirks.md § Invalid qcow2 subcluster entries are refused, as in qemu-img](/components/instar/quirks/#invalid-qcow2-subcluster-entries-are-refused-as-in-qemu-img).

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
- **qcow2 compressed clusters report `compressed: false`** —
  instar's renderer always emits `false`; qemu-img emits `true`
  for compressed-cluster extents. The `file_offset` field for
  compressed clusters also does not have the nb_sectors-1 bits
  stripped. See
  [docs/quirks.md § qcow2 compressed clusters report `compressed: false`](/components/instar/quirks/#qcow2-compressed-clusters-report-compressed-false).
- **Backing-chain `depth` is always 0 in v1** — instar refuses
  sources with a backing pointer; qemu-img walks the chain. Tracked
  as [#641](https://github.com/shakenfist/instar/issues/641). See
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
divergences is `KNOWN_MAP_DIVERGENCES` in `tests/test_map.py`. The differential fuzzer's `MAP_FIELD_SKIPS` in
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
