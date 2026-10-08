# Chain Config Protocol

The chain config is a fixed-layout data structure written by the VMM into
guest memory before starting an operation. It provides metadata about every
device in the backing chain so guest operations can perform format-aware
I/O without re-parsing image headers.

This document covers the binary layout and data flow. For how the VMM
discovers backing chains in the first place, see
[chain-discovery.md](/components/instar/chain-discovery/).

## Data Structures

Both structures are defined in `src/shared/src/lib.rs` and use `#[repr(C)]`
for a stable memory layout.

### ChainConfig (16 bytes header + device array + segment array)

Written at `CHAIN_CONFIG_ADDR` (`0x000F2000`) in guest physical memory.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `magic` | `0x4348414E` ("CHAN") |
| 4 | 4 | u32 | `device_count` | Number of valid device entries (1 = no backing files) |
| 8 | 4 | u32 | `version` | Structure version (currently 3) |
| 12 | 4 | u32 | `segment_count` | Number of valid segment entries; never 0 |
| 16 | 512 | | `devices` | Array of up to 16 `ChainDeviceInfo` entries |
| 528 | 128 | | `segments` | Array of up to 16 `ChainSegment` entries |
| 656 | 64 | | `_reserved` | Reserved, written as 0 |

Total struct size: 720 bytes. The memory allocation at
`CHAIN_CONFIG_ADDR` is 1024 bytes (`CHAIN_CONFIG_MAX_SIZE`) to allow
for future growth.

### ChainSegment (8 bytes per chain)

Each segment entry starts at offset `528 + (segment_index * 8)`.

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
is covered by that chain's segment.

### ChainDeviceInfo (32 bytes per device)

Each device entry starts at offset `16 + (device_index * 32)` within the
chain config.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `format` | `ImageFormat` enum value (see below) |
| 4 | 4 | u32 | `flags` | Feature flags from the info operation |
| 8 | 8 | u64 | `virtual_size` | Virtual size in bytes |
| 16 | 8 | u64 | `actual_size` | Real file size in bytes (see note below) |
| 24 | 4 | u32 | `cluster_size` | Cluster/grain size in bytes (0 for raw) |
| 28 | 4 | u32 | `data_device_idx` | Device index holding this device's cluster data; 0 = this device itself |

### Device Indexing

- Device 0 is always the **top** (primary) image
- Devices 1 through N-1 are backing files in chain order (higher index =
  closer to the base image)
- The **base** image (no backing file) is at index `device_count - 1`

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
| 12 | `VmdkDescriptor` | VMDK monolithicFlat descriptor file (text; content lives in a separate flat extent file) |
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

1. Iterates over chain images and writes each 32-byte `ChainDeviceInfo`
   at `CHAIN_CONFIG_ADDR + 16 + (i * 32)`, returning the number of
   slots it wrote (an external data file costs an extra slot)
2. Writes the 16-byte header and a single segment covering every slot
   written, via `write_chain_config_header()`
3. All writes use `guest_mem.write_obj()` for type-safe memory access

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

### VHD (future)

VHD stores its footer at the last 512 bytes of the file. Like VMDK,
the chain reader will need `actual_size` to locate the footer correctly.

## Constants Reference

All constants are defined in `src/shared/src/lib.rs`:

```
CHAIN_CONFIG_ADDR      = 0x000F2000
CHAIN_CONFIG_MAX_SIZE  = 1024 bytes
MAX_CHAIN_DEVICES      = 16
MAX_CHAIN_SEGMENTS     = 16
ChainConfig::MAGIC     = 0x4348414E ("CHAN")
ChainConfig::VERSION   = 3
```
