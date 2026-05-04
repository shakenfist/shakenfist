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

### ChainConfig (16 bytes header + device array)

Written at `CHAIN_CONFIG_ADDR` (`0x00082000`) in guest physical memory.

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0 | 4 | u32 | `magic` | `0x4348414E` ("CHAN") |
| 4 | 4 | u32 | `device_count` | Number of valid entries (1 = no backing files) |
| 8 | 4 | u32 | `version` | Structure version (currently 1) |
| 12 | 4 | u32 | `_reserved` | Reserved, written as 0 |
| 16 | 512 | | `devices` | Array of up to 16 `ChainDeviceInfo` entries |

Total struct size: 528 bytes (16 header + 16 x 32 device entries). The
memory allocation at `CHAIN_CONFIG_ADDR` is 1024 bytes
(`CHAIN_CONFIG_MAX_SIZE`) to allow for future growth.

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
| 28 | 4 | u32 | `_reserved` | Reserved, written as 0 |

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
| 7 | `Qcow1` | QCOW version 1 |
| 8 | `Vdi` | VDI (VirtualBox) |
| 9 | `Qed` | QED (deprecated) |
| 10 | `Iso` | ISO 9660 |
| 11 | `Luks` | LUKS encrypted container |

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

The VMM writes the chain config via `write_chain_config()` in
`src/vmm/src/main.rs`. The function:

1. Writes the 16-byte header (magic, device_count, version, reserved)
   at `CHAIN_CONFIG_ADDR`
2. Iterates over chain images and writes each 32-byte `ChainDeviceInfo`
   at `CHAIN_CONFIG_ADDR + 16 + (i * 32)`
3. All writes use `guest_mem.write_obj()` for type-safe memory access

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

`ChainConfig::is_valid()` checks that `magic == 0x4348414E` and
`device_count > 0`. Operations should always validate before accessing
device entries.

## Per-Operation Usage

| Operation | How it uses chain config |
|-----------|------------------------|
| **convert** | Reads each device's format to select the appropriate chain reader (QCOW2 cluster lookup, VMDK grain lookup, or raw sector read). Walks the chain to flatten backing files into a standalone output image. |
| **compare** | Reads format for both comparison sides. For QCOW2/VMDK images, walks the backing chain to resolve unallocated clusters/grains before comparing virtual content. Supports multi-level chains on both sides. |
| **check --chain** | Validates each backing image's format consistency, virtual size, and header integrity (QCOW2 magic, version, table bounds). Reports chain errors separately from primary image errors. |

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
CHAIN_CONFIG_ADDR      = 0x00082000
CHAIN_CONFIG_MAX_SIZE  = 1024 bytes
MAX_CHAIN_DEVICES      = 16
ChainConfig::MAGIC     = 0x4348414E ("CHAN")
ChainConfig::VERSION   = 1
```
