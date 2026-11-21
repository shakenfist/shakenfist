# Chain Config Protocol

The chain config is a fixed-layout data structure written by the VMM into
guest memory before starting an operation. It provides metadata about every
device in the backing chain so guest operations can perform format-aware
I/O without re-parsing image headers.

This document covers the binary layout and data flow. For how the VMM
discovers backing chains in the first place, see
[chain-discovery.md](/components/instar/chain-discovery/).

## Data Structures

These structures are all defined in `src/shared/src/lib.rs` and use
`#[repr(C)]` for a stable memory layout.

### ChainConfig (16 bytes header + device array + segment array + extent table)

Written at `CHAIN_CONFIG_ADDR` (`0x000F2000`) in guest physical memory.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `magic` | `0x4348414E` ("CHAN") |
| 4 | 4 | u32 | `device_count` | Number of valid device entries (1 = no backing files) |
| 8 | 4 | u32 | `version` | Structure version (currently 5) |
| 12 | 4 | u32 | `segment_count` | Number of valid segment entries; never 0 |
| 16 | 640 | | `devices` | Array of up to 16 `ChainDeviceInfo` entries |
| 656 | 128 | | `segments` | Array of up to 16 `ChainSegment` entries |
| 784 | 4 | u32 | `extent_count` | Number of valid extent entries; 0 when no device has extents |
| 792 | 2048 | | `extents` | Array of up to 64 `ChainExtent` entries |
| 2840 | 64 | | `_reserved` | Reserved, written as 0 |

Total struct size: 2904 bytes. `extents` is 8-aligned, so four bytes of
padding follow `extent_count`. The memory allocation at
`CHAIN_CONFIG_ADDR` is the whole 4096-byte page up to `VMM_PARAMS_ADDR`
(`CHAIN_CONFIG_MAX_SIZE`), and a compile-time assert in
`src/shared/src/lib.rs` fails the build if the structure outgrows it.

### ChainSegment (8 bytes per chain)

Each segment entry starts at offset `656 + (segment_index * 8)`.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `first` | Index in `devices` of this chain's top image |
| 4 | 4 | u32 | `count` | Number of devices in this chain, including the top |

A segment is exactly the `chain_start` / `chain_len` pair the guest
hands `read_chain_virtual_cluster`. `device_count` bounds a flat
array, so it cannot say how long any one device's chain is: `compare
image1 image2` and `rebase --backing NEW` both attach two unrelated
chains to one device array. The segments say which devices form a
chain, and the device behind a given slot is the next slot *in that
slot's own segment*.

The segments must tile `[0, device_count)` exactly — at least one
segment, none empty, in ascending order, no gaps, no overlaps. The
host checks this before it writes the config and
`qcow2::init_chain_states` checks it again before walking the device
array; a config that fails the check is refused rather than
interpreted. An all-zero segmentation (`segment_count == 0`) is
therefore never read as "one chain spanning everything", which is
what makes an unstated segmentation detectable instead of silently
composing a differencing child against whatever image happened to
follow it.

An external data file device, which the host inserts immediately after
the image that owns it, belongs to the chain it was inserted into and
is covered by that chain's segment. It is marked with
`FLAG_DATA_DEVICE` and is never a layer: it holds its owner's cluster
data, it is not a backing file, and the chain reader steps over it
(#792). A qcow2 owns one such device, its external data file, and a
qcow2 is the only owner there is: a VMDK descriptor's extents share the
descriptor's own device and are named by the extent table, so a
descriptor whose `data_device_idx` is not zero is the host and the guest
disagreeing about how the image was attached. The owner's
`data_device_idx` must name the slot immediately after the owner, and
every marked device must belong to an owner;
`ChainConfig::data_devices_are_owned` refuses any other layout.

### ChainDeviceInfo (40 bytes per device)

Each device entry starts at offset `16 + (device_index * 40)` within the
chain config.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `format` | `ImageFormat` enum value (see below) |
| 4 | 4 | u32 | `flags` | Feature flags from the info operation |
| 8 | 8 | u64 | `virtual_size` | Virtual size in bytes |
| 16 | 8 | u64 | `actual_size` | Real file size in bytes (see note below) |
| 24 | 4 | u32 | `cluster_size` | Cluster/grain size in bytes (0 for raw) |
| 28 | 4 | u32 | `data_device_idx` | Device index holding this device's cluster data; 0 = this device itself |
| 32 | 4 | u32 | `first_extent` | Index in `extents` of this device's first extent |
| 36 | 4 | u32 | `extent_count` | Number of `extents` entries this device owns; 0 = the device is a single file |

### ChainExtent (32 bytes per extent)

Each extent entry starts at offset `792 + (extent_index * 32)`.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 8 | u64 | `device_offset` | Byte offset within the device where this extent's file begins |
| 8 | 8 | u64 | `file_length` | Length in bytes of the extent's own file |
| 16 | 8 | u64 | `virtual_span` | Bytes of the virtual disk this extent covers |
| 24 | 4 | u32 | `kind` | 1 = `KIND_FLAT`, 2 = `KIND_SPARSE`; 0 is refused |
| 28 | 4 | u32 | `_reserved` | Reserved, written as 0 |

The table describes a device whose bytes are several files laid end to
end -- a VMDK descriptor and its extents -- so that the guest can find
the boundaries between them. A read is served by locating the extent
covering the wanted virtual offset and reading at `device_offset +
(offset - that extent's virtual base)`; the entries of a device's run
are in descriptor order, so the first extent's virtual base is zero and
each later one's base is the sum of the spans before it.

A config with no such device leaves `extent_count` zero and the table
untouched: every device is then a single file whose own `extent_count`
is zero as well, and nothing consults the table.

The devices' slices must tile the table from its first entry in device
order -- a device with extents starts where the last one ended, and a
device with none has a `first_extent` of zero. A VMDK descriptor that
is a layer of the chain has at least one entry and no other device has
any, because a descriptor's own bytes are its text and reading them
would serve the descriptor's source as the disk. Within a device the
files are in ascending order and do not overlap, and the spans sum
without overflowing. As with the segmentation, the host checks the rule
before it writes the config and `qcow2::init_chain_states` checks it
again before any read; `ChainConfig::extents_are_sound` is the rule
both halves use.

The host places each extent file at the running total of the lengths of
the files before it, rounded up to a multiple of 512. The length is the
file's own and never the span the descriptor declares for it: a sparse
extent whose grains are all allocated is longer than its span, and one
that is mostly holes is far shorter, so a span would leave the next
extent's base in the wrong place either way. The rounding is what makes
a sparse extent's `device_offset` a multiple of 512, which the guest
requires because sparse VMDK counts 512-byte sectors from the start of
its own file; qemu writes extent files in whole sectors, so for a
qemu-written image the rounding adds nothing.

`virtual_span` is carried rather than derived from `file_length`
because a sparse extent is shorter on disk than the span it covers, and
a descriptor may declare any span it likes. Nor is concatenation alone
enough for a sparse split image: each sparse extent holds its own
header, grain directory and grain tables covering only its own span, so
without the boundaries the guest would read the wrong metadata and do
it silently.

`kind` is carried rather than worked out from the bytes because the
descriptor's extent line is the authority and qemu believes it. A guest
that looked for a sparse header would read a flat extent whose first
sector happened to hold one as sparse, and one that compared the file's
length with its span would read a sparse extent padded out to its span
as flat. Either way one file would mean different bytes to instar than
to qemu. Two sparse extents the guest refuses that qemu would read: a
compressed one, because nothing can hold a compressed grain to its
extent's file, and one whose header capacity is not the span the
descriptor line declares, because then every later extent sits at a
different virtual offset for each reader.

`MAX_CHAIN_EXTENTS` is 64, which is 128 GiB at qemu's 2 GiB default
extent size. qemu has no equivalent limit: the bound exists because the
config region is fixed-size and shared with the guest, so the table
cannot grow with the image. An image with more extents than the table
holds cannot be described to the guest and is refused with a message
naming the bound; raising the bound means moving the config region
rather than changing the constant.

The descriptor parser's own bound, `vmdk::MAX_EXTENTS`, is defined as
this constant rather than chosen separately, so a descriptor the parser
accepts is always one the table can hold. A single over-long descriptor
is refused by the parser, which reads it first; the table's own check
still runs, for a backing chain or a comparison whose images each fit
but whose extents together do not.

### Device Indexing

- Device 0 is always the **top** (primary) image
- The remaining devices are backing files in chain order (higher index =
  closer to the base image), except external data file devices
- An external data file device sits immediately after the image that
  owns it, so the owner's `data_device_idx` is its own index plus one.
  It is not a backing file, and the next backing file follows it
- A VMDK descriptor's extents take no device slot. The descriptor is
  attached as one device whose bytes are its extent files laid end to
  end, so a descriptor with twenty-six extents costs one slot and its
  `data_device_idx` is zero
- The **base** image (no backing file) is the last non-data device

## ImageFormat Values

| Value | Variant | Description |
|-------|---------|-------------|
| 0 | `Unknown` | Format could not be determined |
| 1 | `Raw` | Raw disk image |
| 2 | `Qcow2` | QCOW2 v2/v3 |
| 3 | `Vmdk4` | VMDK version 4 (monolithicSparse, streamOptimized) |
| 4 | `Vmdk3` | VMDK version 3 (COWD) |
| 5 | `Vhd` | VHD/VPC |
| 6 | `Vhdx` | VHDX |
| 7 | `Qcow1` | QCOW version 1 — full read support (convert/compare/dd/bench) |
| 8 | `Vdi` | VDI (VirtualBox) — full read support (convert/compare/dd/bench) |
| 9 | `Qed` | QED (read-refused by policy, not because qemu deprecates it — see `docs/quirks.md` "QED read-refusal as policy") |
| 10 | `Iso` | ISO 9660 |
| 11 | `Luks` | LUKS encrypted container |
| 12 | `VmdkDescriptor` | VMDK descriptor file (text; content lives in one or more separate extent files, named by the extent table) |
| 13 | `Parallels` | Parallels Disk Image, both magics — full read support (convert/compare/dd/bench) |
| 14 | `Bochs` | Bochs growing disk image — detection and info only, no read path |
| 15 | `Cloop` | cloop compressed loopback image — detection and info only, no read path |
| 16 | `Dmg` | Apple UDIF disk image — full read support (convert/compare/dd/bench) |

## Feature Flags

The `flags` field uses the same bit definitions as `InfoResult`:

| Bit | Constant | Meaning |
|-----|----------|---------|
| 0 | `FLAG_HAS_BACKING_FILE` | Image references a backing file |
| 1 | `FLAG_HAS_EXTERNAL_DATA` | Image has an external data file |
| 2 | `FLAG_ENCRYPTED` | Image is encrypted |
| 3 | `FLAG_COMPRESSED` | Image has compressed clusters/grains |
| 4 | `FLAG_HAS_SNAPSHOTS` | Image contains snapshots |
| 5 | `FLAG_DIRTY` | Dirty bit set (unclean shutdown) |
| 6 | `FLAG_CORRUPT` | Corrupt bit set |
| 7 | `FLAG_HAS_MBR` | Raw image has MBR partition table |
| 8 | `FLAG_HAS_GPT` | Raw image has GPT partition table |
| 31 | `FLAG_DATA_DEVICE` | Device is an external data file, not a layer. Set only by the host in the chain config; the host masks it out of the flags an info operation reports, so an image cannot mark itself |

## VMM to Guest Data Flow

### 1. Chain Discovery

The VMM discovers the backing chain by running sandboxed info operations
on each image (see [chain-discovery.md](/components/instar/chain-discovery/)). This
produces a `BackingChain` containing `ChainImage` entries with format,
sizes, flags, and backing file paths. Source:
`discover_backing_chain()` in `src/vmm/src/main.rs`.

### 2. Populating `actual_size`

The guest info operation reports `actual_size = 0` for non-QCOW2 formats
(by design -- the guest has no filesystem access). The VMM fills in the
real file size from `std::fs::metadata()` when the guest reports 0:

```rust
let file_size = std::fs::metadata(&path)
    .map(|m| m.len()).unwrap_or(0);
let actual_size = if info_result.actual_size > 0 {
    info_result.actual_size
} else {
    file_size
};
```

This is critical for formats that locate structures relative to EOF
(e.g. streamOptimized VMDK footer at `file_size - 1024`). Using
`capacity * sector_size` instead would overshoot when the file size
doesn't evenly divide the sector size.

### 3. Writing to Guest Memory

An operation whose devices form a single chain writes the config via
`write_chain_config()` in `src/vmm/src/main.rs`. The function:

1. Iterates over chain images and writes each 40-byte `ChainDeviceInfo`
   at `CHAIN_CONFIG_ADDR + 16 + (i * 40)`, returning the number of
   slots it wrote (an external data file costs an extra slot). The
   stride and the base of the segment array are taken from
   `size_of::<ChainDeviceInfo>()` rather than written as literals, so a
   field added to a device entry cannot leave the host writing at one
   stride while the guest reads at another
2. Writes the 16-byte header and a single segment covering every slot
   written, via `write_chain_config_header()`
3. All writes use `guest_mem.write_obj()` for type-safe memory access

A chain holding more than `MAX_CHAIN_DEVICES` (16) devices is refused
here, naming the depth and the limit, rather than written as its first
16 slots. No command reaches that refusal: `check`, `convert`, `bench`
and `commit` each count the devices of the chain they are about to
write and refuse an over-budget one themselves, with a message naming
the operation. It is a backstop against a caller whose own count
drifts from the chain it passes, which the budget makes easy to get
wrong -- it is in devices and not images, so
`security.max_chain_depth` (16 by default) counts images while each
external data file takes a slot of its own. A guard taking its number
from the depth limit would undershoot, and the chain would be
truncated here with only a debug line to say so.

`compare` and `rebase` attach two unrelated chains, so they call
`write_chain_device_entries()` once per chain and then
`write_chain_config_header()` with one segment per chain. Both take
the segment bounds from the slot counts the entry writer reports,
rather than from the chains themselves, so a segment can never name a
slot the guest was not given.

## Guest-Side Access

Operations read the chain config in one of two ways:

### Direct Memory Cast

Most operations cast `CHAIN_CONFIG_ADDR` directly to a `ChainConfig`
reference:

```rust
let chain_config = &*(CHAIN_CONFIG_ADDR as *const ChainConfig);
if !chain_config.is_valid() {
    // Error: missing or corrupt chain config
}
```

### Call Table Function

The check operation uses the call table's `get_chain_config()` function,
which returns a `ConfigResult` with a pointer and length:

```rust
let chain_result = (call_table.get_chain_config)();
if !chain_result.ptr.is_null() && chain_result.len > 0 {
    let chain_config = &*(chain_result.ptr as *const ChainConfig);
    // ...
}
```

### Validation

`ChainConfig::is_valid()` checks that `magic == 0x4348414E`, that
`version` is the one this build was compiled against, and that
`device_count > 0`. Operations should always validate before accessing
device entries.

The version check is the skew defence. Operation binaries are separate
files the host loads at run time (and `INSTAR_BIN_DIR` can point at
another build entirely), so a guest compiled against one layout can be
handed a config written in another; without the check, a field at an
offset the guest reads as something else is a silent misparse rather
than a refusal. The core binary's own validity check at
`CHAIN_CONFIG_ADDR` stays deliberately magic-only: if it tightened,
the operation could not see the config in order to report why it
refused.

`ChainConfig::segment_of(dev_idx)` returns the segment covering a
device, and `ChainConfig::segmentation_covers(device_count)` is the
tiling check described above. `ChainConfig::single_chain(n)` builds a
config for the common case of one chain over `n` devices.

## Per-Operation Usage

The `Segments` column is how many `ChainSegment` entries the host writes.
One segment means every device is behind the one before it; two means the
operation attached two unrelated chains, and the guest must not treat the
first device of the second as a parent of the last device of the first.

| Operation | Segments | How it uses chain config |
|-----------|----------|------------------------|
| **convert** / **dd** | 1 | Reads each device's format to select the appropriate chain reader (QCOW2 cluster lookup, VMDK grain lookup, VHD or VHDX BAT lookup, or raw sector read). Walks the chain to flatten backing files into a standalone output image. `dd` shares convert's guest binary and so its chain handling. |
| **compare** | 2 | Reads format for both comparison sides, one segment each. Walks each side's own backing chain to resolve unallocated clusters, grains or blocks before comparing virtual content. Supports multi-level chains on both sides. |
| **rebase** | 1 or 2 | A detach (`-b ''`) attaches the old chain's parents only, so one segment. A rebase onto a new backing (`-b NEW`) attaches the old chain and the new one, which are unrelated, so two. The overlay itself is the output device, not an input. |
| **bench** | 1 | Reads the source's chain so a benchmarked read resolves through it rather than against the top image alone. |
| **commit** | 1 | Attaches the backing chain's parents behind the overlay; the backing being committed into is the output device. |
| **check --chain** | 1 | Validates each backing image's format consistency, virtual size, and header integrity (QCOW2 magic, version, table bounds). Reports chain errors separately from primary image errors. |

The chain walkers that `convert`, `dd`, `compare`, `bench` and `rebase`
share also compose a differencing VHD or VHDX member against its parent,
descending for the sectors the member's bitmap leaves to the parent.
`map`, `measure` and `check` read an image on its own and refuse a
differencing source by name instead.

## Format-Specific Notes

### QCOW2

The guest info operation computes `actual_size` from the QCOW2 header
(L1 table extent + refcount table extent), so the VMM receives a
non-zero value directly. This matches qemu-img's "file length"
calculation for minimal QCOW2 files.

### VMDK

The guest info operation reports `actual_size = 0`, so the VMM uses the
filesystem file size. The VMDK chain reader uses `actual_size` in
`VmdkState::init()` to locate the streamOptimized footer at
`actual_size - 1024`. Without the real file size, the footer search
would use `capacity * sector_size`, which overshoots for files not
aligned to the sector size boundary.

### VHD

VHD stores its footer in the last 512 bytes of the file, but the chain
reader does not need `actual_size` to find it. `VhdState::init()` reads
sector 0 first, because a dynamic or differencing VHD carries a footer
*copy* there, and falls back to the last sector -- located as
`input_capacity - 1`, from the virtio capacity -- only for a fixed VHD.
So unlike the VMDK arm above, `init_chain_states()` hands this reader no
`actual_size`; the one the crate computes internally is a local
`input_capacity * sector_size` bound for range checks, not this field.

What a differencing child does need is the per-block sector bitmap. Each
allocated block is preceded by a bitmap whose bits say, per 512-byte
sector, whether that sector's data is the child's or the parent's: set
means the child, most significant bit first. The chain walker coalesces
runs of equally owned sectors and descends for the runs the bitmap
leaves to the parent; an unallocated block belongs to the parent
entirely.

### VHDX

VHDX has no footer at all -- its headers, region tables and BAT sit at
offsets the format fixes -- so `VhdxState::init()` also takes no
`actual_size`, and bounds itself by `input_capacity * sector_size` the
same way.

Its sector bitmaps are arranged differently from VHD's: one 1 MiB bitmap
block serves a whole chunk of payload blocks instead of preceding each
block, so bit numbering restarts at the chunk's start, bits are least
significant first, and they count the image's declared
`logical_sector_size` of 512 or 4096. Only a block the BAT marks
`PAYLOAD_BLOCK_PARTIALLY_PRESENT` sends the reader to the bitmap; the
other states name an owner by themselves.

## Constants Reference

All constants are defined in `src/shared/src/lib.rs`:

```
CHAIN_CONFIG_ADDR      = 0x000F2000
CHAIN_CONFIG_MAX_SIZE  = 4096 bytes (VMM_PARAMS_ADDR - CHAIN_CONFIG_ADDR)
MAX_CHAIN_DEVICES      = 16
MAX_CHAIN_SEGMENTS     = 16
MAX_CHAIN_EXTENTS      = 64
ChainConfig::MAGIC     = 0x4348414E ("CHAN")
ChainConfig::VERSION   = 5
```
